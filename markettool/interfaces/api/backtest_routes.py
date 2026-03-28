"""Backtest API routes.

Provides endpoints to run backtests and retrieve cached results.
Results are persisted in Firestore collection ``backtest_results``
with a ``status`` field: "running" | "completed" | "failed".
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Dict, Optional

from flask import Flask, jsonify, request

from markettool.application.services.backtesting_service import get_backtesting_service

logger = logging.getLogger(__name__)

COLLECTION = "backtest_results"


def _get_firestore_db(services: Any):
    """Get Firestore db from services or fall back to firebase_admin."""
    if hasattr(services, "db") and services.db is not None:
        return services.db
    try:
        import firebase_admin
        from firebase_admin import firestore as fs_module

        if not firebase_admin._apps:
            firebase_admin.initialize_app()
        return fs_module.client()
    except Exception as exc:
        logger.error("Could not get Firestore client: %s", exc)
        return None


def _get_gcs_bucket(services: Any):
    """Get GCS bucket for reading enriched JSONs."""
    try:
        from google.cloud import storage as gcs_storage

        bucket_name = getattr(services, "gcs_bucket_name", None) or "markettool_bucket"
        client = gcs_storage.Client()
        return client.bucket(bucket_name)
    except Exception as exc:
        logger.warning("Could not get GCS bucket: %s", exc)
        return None


def _load_enriched_json(bucket, gcs_path: str) -> Optional[Dict]:
    """Download and parse a JSON blob from GCS."""
    try:
        blob = bucket.blob(gcs_path)
        if not blob.exists():
            return None
        raw = blob.download_as_text()
        return json.loads(raw)
    except Exception as exc:
        logger.warning("Failed to load enriched JSON %s: %s", gcs_path, exc)
        return None


def _find_enriched_for_symbol_tf(
    db, bucket, exec_id: str, symbol: str, timeframe: str
) -> Optional[Dict]:
    """Find the enriched/oportunidades JSON for a symbol+tf inside an exec."""
    try:
        docs = list(
            db.collection("archivos_generados")
            .where("exec_id", "==", exec_id)
            .stream()
        )
        sym_upper = symbol.upper()
        tf_lower = timeframe.lower()

        for doc in docs:
            data = doc.to_dict() or {}
            nombre = (
                data.get("metadata", {}).get("nombre")
                or data.get("gcs_path")
                or ""
            )
            nombre_upper = nombre.upper()
            if sym_upper in nombre_upper and tf_lower.upper() in nombre_upper:
                gcs_path = data.get("gcs_path") or data.get("metadata", {}).get("gcs_path")
                if gcs_path and bucket:
                    payload = _load_enriched_json(bucket, gcs_path)
                    if payload is not None:
                        return payload
                signed_url = data.get("metadata", {}).get("signed_url")
                if signed_url:
                    import requests as req_lib
                    resp = req_lib.get(signed_url, timeout=15)
                    if resp.status_code == 200:
                        return resp.json()
    except Exception as exc:
        logger.warning("archivos_generados query failed: %s", exc)

    if bucket:
        safe_sym = symbol.upper().replace("/", "_")
        safe_tf = timeframe.lower()
        candidates = [
            f"analisis/exec/{exec_id}/{safe_sym}_{safe_tf}_ordenados.json",
            f"analisis/exec/{exec_id}/{safe_sym}_{safe_tf}_oportunidades.json",
            f"analisis/exec/{exec_id}/{safe_sym}__{safe_tf}_ordenados.json",
        ]
        for path in candidates:
            payload = _load_enriched_json(bucket, path)
            if payload is not None:
                return payload

    return None


def _make_key(exec_id: str, symbol: str, timeframe: str) -> str:
    return f"{exec_id}_{symbol.upper()}_{timeframe.lower()}"


def register_backtest_routes(app: Flask, *, services) -> None:
    """Register backtest API endpoints on Flask app."""

    db = _get_firestore_db(services)
    bucket = _get_gcs_bucket(services)
    bt_service = get_backtesting_service(logger=logger)

    # ─── GET /api/backtest/<exec_id> ── batch: all backtests for an exec ──
    @app.route("/api/backtest/<exec_id>", methods=["GET"])
    def backtest_batch_get(exec_id: str):
        try:
            if not db:
                return jsonify({"status": "error", "message": "Firestore not available"}), 503

            docs = list(
                db.collection(COLLECTION)
                .where("exec_id", "==", exec_id)
                .stream()
            )

            results: Dict[str, Any] = {}
            for doc in docs:
                data = doc.to_dict() or {}
                sym = data.get("symbol", "")
                tf = data.get("timeframe", "")
                bt_key = f"{sym}_{tf}"
                bt_status = data.get("status", "not_found")
                entry: Dict[str, Any] = {"backtest_status": bt_status}
                if bt_status == "completed" and "stats" in data:
                    entry["stats"] = data["stats"]
                results[bt_key] = entry

            return jsonify({"status": "ok", "results": results})
        except Exception as exc:
            logger.exception("Error in GET /api/backtest/<exec_id>")
            return jsonify({"status": "error", "message": str(exc)}), 500

    # ─── GET /api/backtest/<exec_id>/<symbol>/<timeframe> ─────────────
    @app.route("/api/backtest/<exec_id>/<symbol>/<timeframe>", methods=["GET"])
    def backtest_get(exec_id: str, symbol: str, timeframe: str):
        try:
            doc_key = _make_key(exec_id, symbol, timeframe)

            if not db:
                return jsonify({"status": "error", "message": "Firestore not available"}), 503

            doc = db.collection(COLLECTION).document(doc_key).get()
            if not doc.exists:
                return jsonify({
                    "status": "ok",
                    "backtest_status": "not_found",
                    "key": doc_key,
                })

            data = doc.to_dict() or {}
            bt_status = data.get("status", "not_found")
            resp: Dict[str, Any] = {
                "status": "ok",
                "backtest_status": bt_status,
                "key": doc_key,
            }
            if bt_status == "completed" and "stats" in data:
                resp["stats"] = data["stats"]

            return jsonify(resp)
        except Exception as exc:
            logger.exception("Error in GET /api/backtest/<exec_id>/<sym>/<tf>")
            return jsonify({"status": "error", "message": str(exc)}), 500

    # ─── POST /api/backtest/run ───────────────────────────────────────
    @app.route("/api/backtest/run", methods=["POST"])
    def backtest_run():
        try:
            body = request.get_json(force=True, silent=True) or {}
            exec_id = str(body.get("exec_id") or "").strip()
            symbol = str(body.get("symbol") or "").strip().upper()
            timeframe = str(body.get("timeframe") or "").strip().lower()
            user_id = str(body.get("user_id") or "").strip()

            if not exec_id or not symbol or not timeframe:
                return jsonify({
                    "status": "error",
                    "message": "exec_id, symbol, and timeframe are required",
                }), 400

            doc_key = _make_key(exec_id, symbol, timeframe)

            # Check existing doc
            if db:
                try:
                    existing = db.collection(COLLECTION).document(doc_key).get()
                    if existing.exists:
                        existing_data = existing.to_dict() or {}
                        ex_status = existing_data.get("status", "")

                        if ex_status == "completed":
                            return jsonify({
                                "status": "ok",
                                "backtest_status": "completed",
                                "key": doc_key,
                                "stats": existing_data.get("stats", {}),
                                "cached": True,
                            })

                        if ex_status == "running":
                            return jsonify({
                                "status": "ok",
                                "backtest_status": "running",
                                "key": doc_key,
                            })
                except Exception as exc:
                    logger.warning("Cache read failed: %s", exc)

            # Mark as running
            if db:
                try:
                    db.collection(COLLECTION).document(doc_key).set({
                        "status": "running",
                        "exec_id": exec_id,
                        "symbol": symbol,
                        "timeframe": timeframe,
                        "user_id": user_id,
                        "created_at": int(time.time() * 1000),
                    })
                except Exception as exc:
                    logger.warning("Firestore running write failed: %s", exc)

            # Load enriched JSON
            enriched = _find_enriched_for_symbol_tf(db, bucket, exec_id, symbol, timeframe)
            if enriched is None:
                # Mark as failed
                if db:
                    try:
                        db.collection(COLLECTION).document(doc_key).update({
                            "status": "failed",
                            "error": f"No enriched data found for {exec_id}/{symbol}/{timeframe}",
                            "completed_at": int(time.time() * 1000),
                        })
                    except Exception:
                        pass
                return jsonify({
                    "status": "error",
                    "backtest_status": "failed",
                    "message": f"No enriched data found for {exec_id}/{symbol}/{timeframe}",
                }), 404

            # Extract candles and entries from enriched payload
            candles: list = []
            entries: list = []

            if isinstance(enriched, list):
                entries = enriched
            elif isinstance(enriched, dict):
                candles = enriched.get("candles") or enriched.get("series") or []
                entries = (
                    enriched.get("entries")
                    or enriched.get("entradas")
                    or enriched.get("oportunidades")
                    or enriched.get("records")
                    or []
                )
                if not entries and isinstance(enriched.get("data"), list):
                    entries = enriched["data"]

            # Run backtest
            try:
                stats = bt_service.run_from_enriched(
                    candles=candles,
                    entries=entries,
                    symbol=symbol,
                    timeframe=timeframe,
                )
            except Exception as exc:
                if db:
                    try:
                        db.collection(COLLECTION).document(doc_key).update({
                            "status": "failed",
                            "error": str(exc),
                            "completed_at": int(time.time() * 1000),
                        })
                    except Exception:
                        pass
                return jsonify({
                    "status": "error",
                    "backtest_status": "failed",
                    "message": str(exc),
                }), 500

            # Persist completed to Firestore
            if db:
                try:
                    db.collection(COLLECTION).document(doc_key).update({
                        "status": "completed",
                        "stats": stats,
                        "completed_at": int(time.time() * 1000),
                    })
                except Exception as exc:
                    logger.warning("Firestore completed write failed: %s", exc)

            return jsonify({
                "status": "ok",
                "backtest_status": "completed",
                "key": doc_key,
                "stats": stats,
                "cached": False,
            })

        except Exception as exc:
            logger.exception("Error in POST /api/backtest/run")
            return jsonify({"status": "error", "message": str(exc)}), 500

    logger.info(
        "✅ Backtest routes registered (/api/backtest/run, /api/backtest/<exec_id>, /api/backtest/<exec_id>/<sym>/<tf>)"
    )
