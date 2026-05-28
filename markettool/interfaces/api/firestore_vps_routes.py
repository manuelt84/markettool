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

from markettool.infra.storage.vps_json_store import PostgresDocumentStore, vps_mode_enabled


def _store() -> PostgresDocumentStore:
    store = PostgresDocumentStore.from_env()
    if store is None:
        abort(503, description="MARKETTOOL_POSTGRES_DSN or MARKETTOOL_POSTGRES_DSN_FILE is required")
    return store


def _enabled() -> bool:
    return vps_mode_enabled() or os.getenv("MARKETTOOL_FIRESTORE_REST_ENABLED", "").lower() in {"1", "true", "yes", "on"}


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


def _filter_rows(rows: list[tuple[str, dict[str, Any]]], filters: list[dict[str, Any]]) -> list[tuple[str, dict[str, Any]]]:
    def matches(doc_id: str, data: dict[str, Any]) -> bool:
        for flt in filters:
            field = str(flt.get("field") or "")
            op = str(flt.get("op") or "==")
            expected = flt.get("value")
            actual = doc_id if field == "__name__" else data.get(field)
            if op == "==" and str(actual) != str(expected):
                return False
            if op == "!=" and str(actual) == str(expected):
                return False
            if op == "in":
                if not isinstance(expected, list) or actual not in expected:
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
        filters = payload.get("filters") or []
        order_by = payload.get("orderBy")
        limit = payload.get("limit")
        rows = _store().query_documents(
            _clean_path(collection_path),
            [(str(f.get("field")), str(f.get("op") or "=="), f.get("value")) for f in filters if isinstance(f, dict) and f.get("op", "==") == "=="],
            str(order_by) if order_by else None,
            int(limit) if limit else None,
        )
        remaining_filters = [f for f in filters if isinstance(f, dict) and f.get("op", "==") != "=="]
        if remaining_filters:
            rows = _filter_rows(rows, remaining_filters)
        return jsonify({
            "documents": [
                {"id": doc_id, "exists": True, "data": data}
                for doc_id, data in rows
            ]
        })
