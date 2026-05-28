"""Firestore-compatible document routes backed by PostgreSQL.

These endpoints are intentionally generic because MarketTool web/mobile still
use Firestore-shaped collections in many screens.  When Google billing is off,
the clients can switch to this REST layer without changing their document
model.
"""

from __future__ import annotations

import os
from typing import Any

from flask import abort, jsonify, request

from markettool.infra.storage.vps_json_store import PostgresDocumentStore, vps_fallback_enabled


def _store() -> PostgresDocumentStore:
    store = PostgresDocumentStore.from_env()
    if store is None:
        abort(503, description="MARKETTOOL_POSTGRES_DSN or MARKETTOOL_POSTGRES_DSN_FILE is required")
    return store


def _enabled() -> bool:
    return vps_fallback_enabled() or os.getenv("MARKETTOOL_FIRESTORE_REST_ENABLED", "").lower() in {"1", "true", "yes", "on"}


def _clean_path(path: str) -> str:
    clean = str(path or "").strip().strip("/")
    parts = [part for part in clean.split("/") if part and part not in {".", ".."}]
    if not parts:
        abort(400, description="empty document path")
    return "/".join(parts)


def _doc_parts(path: str) -> tuple[str, str]:
    clean = _clean_path(path)
    parts = clean.split("/")
    if len(parts) < 2:
        abort(400, description="document path must include collection and document id")
    return "/".join(parts[:-1]), parts[-1]


def _normalize_filter(raw: Any) -> dict[str, Any] | None:
    if isinstance(raw, dict):
        field = raw.get("field")
        if not field:
            return None
        return {"field": str(field), "op": str(raw.get("op") or "=="), "value": raw.get("value")}
    if isinstance(raw, (list, tuple)) and len(raw) >= 3:
        return {"field": str(raw[0]), "op": str(raw[1] or "=="), "value": raw[2]}
    return None


def _filter_rows(rows: list[tuple[str, dict[str, Any]]], filters: list[dict[str, Any]]) -> list[tuple[str, dict[str, Any]]]:
    def field_value(data: dict[str, Any], field: str) -> Any:
        target: Any = data
        for part in field.split("."):
            if not isinstance(target, dict) or part not in target:
                return None
            target = target[part]
        return target

    def matches(doc_id: str, data: dict[str, Any]) -> bool:
        for flt in filters:
            field = str(flt.get("field") or "")
            op = str(flt.get("op") or "==")
            expected = flt.get("value")
            actual = doc_id if field == "__name__" else field_value(data, field)
            if op == "==" and str(actual) != str(expected):
                return False
            if op == "!=" and str(actual) == str(expected):
                return False
            if op == "in":
                if not isinstance(expected, list) or str(actual) not in {str(item) for item in expected}:
                    return False
            if op in {">", ">=", "<", "<="}:
                try:
                    a = float(actual)
                    b = float(expected)
                except (TypeError, ValueError):
                    a = str(actual or "")
                    b = str(expected or "")
                if op == ">" and not (a > b):
                    return False
                if op == ">=" and not (a >= b):
                    return False
                if op == "<" and not (a < b):
                    return False
                if op == "<=" and not (a <= b):
                    return False
        return True

    return [(doc_id, data) for doc_id, data in rows if matches(doc_id, data)]


def _field_value(data: dict[str, Any], field: str) -> Any:
    target: Any = data
    for part in field.split("."):
        if not isinstance(target, dict) or part not in target:
            return None
        target = target[part]
    return target


def _apply_cursors(
    rows: list[tuple[str, dict[str, Any]]],
    order_by: str | None,
    start_at: Any,
    end_at: Any,
) -> list[tuple[str, dict[str, Any]]]:
    if not order_by or (start_at is None and end_at is None):
        return rows

    def comparable(value: Any) -> str:
        return "" if value is None else str(value)

    result = rows
    if start_at is not None:
        start = comparable(start_at)
        result = [
            (doc_id, data)
            for doc_id, data in result
            if comparable(_field_value(data, order_by)) >= start
        ]
    if end_at is not None:
        end = comparable(end_at)
        result = [
            (doc_id, data)
            for doc_id, data in result
            if comparable(_field_value(data, order_by)) <= end
        ]
    return result


def _has_field_ops(value: Any) -> bool:
    if isinstance(value, dict):
        if "__op" in value:
            return True
        return any(_has_field_ops(v) for v in value.values())
    if isinstance(value, list):
        return any(_has_field_ops(v) for v in value)
    return False


def register_firestore_vps_routes(app) -> None:
    @app.route("/api/v1/firestore/doc/<path:doc_path>", methods=["GET"])
    def firestore_get_doc(doc_path: str):
        if not _enabled():
            abort(404)
        collection, doc_id = _doc_parts(doc_path)
        data = _store().get_document(collection, doc_id)
        return jsonify({"id": doc_id, "exists": data is not None, "data": data})

    @app.route("/api/v1/firestore/doc/<path:doc_path>", methods=["PUT", "PATCH"])
    def firestore_set_doc(doc_path: str):
        if not _enabled():
            abort(404)
        collection, doc_id = _doc_parts(doc_path)
        payload = request.get_json(silent=True) or {}
        data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
        merge = bool(payload.get("merge")) or request.method == "PATCH"
        store = _store()
        if request.method == "PATCH" or (merge and _has_field_ops(data)):
            store.update_document(collection, doc_id, data)
        else:
            store.set_document(collection, doc_id, data, merge=merge)
        return jsonify({"status": "ok", "id": doc_id})

    @app.route("/api/v1/firestore/doc/<path:doc_path>", methods=["DELETE"])
    def firestore_delete_doc(doc_path: str):
        if not _enabled():
            abort(404)
        collection, doc_id = _doc_parts(doc_path)
        _store().delete_document(collection, doc_id)
        return jsonify({"status": "ok", "id": doc_id})

    @app.route("/api/v1/firestore/query/<path:collection_path>", methods=["POST"])
    def firestore_query(collection_path: str):
        if not _enabled():
            abort(404)
        payload = request.get_json(silent=True) or {}
        filters = [
            flt for flt in (_normalize_filter(item) for item in (payload.get("filters") or []))
            if flt is not None
        ]
        order_by = payload.get("orderBy")
        order_direction = str(payload.get("orderDirection") or "asc").lower()
        limit = payload.get("limit")
        start_at = payload.get("startAt")
        end_at = payload.get("endAt")
        sql_filters = [f for f in filters if f.get("op", "==") == "=="]
        remaining_filters = [f for f in filters if f.get("op", "==") != "=="]
        sql_limit = None if remaining_filters or start_at is not None or end_at is not None else limit
        rows = _store().query_documents(
            _clean_path(collection_path),
            [(str(f.get("field")), str(f.get("op") or "=="), f.get("value")) for f in sql_filters],
            str(order_by) if order_by else None,
            int(sql_limit) if sql_limit else None,
            "asc" if order_direction == "asc" else "desc",
        )
        if remaining_filters:
            rows = _filter_rows(rows, remaining_filters)
            if any(f.get("field") == "__name__" for f in remaining_filters) and not order_by:
                rows = sorted(rows, key=lambda row: row[0])
        rows = _apply_cursors(rows, str(order_by) if order_by else None, start_at, end_at)
        if limit and (remaining_filters or start_at is not None or end_at is not None):
            rows = rows[:int(limit)]
        return jsonify({
            "documents": [
                {"id": doc_id, "exists": True, "path": f"{_clean_path(collection_path)}/{doc_id}", "data": data}
                for doc_id, data in rows
            ]
        })
