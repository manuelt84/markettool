"""Backtest API routes.

Results are persisted as subcollection ``ejecuciones/{exec_id}/backtest_results/{SYMBOL}_{tf}``
with a ``status`` field: "running" | "completed" | "failed".
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, Optional

from flask import Flask, jsonify, request

from markettool.infra.fmp.client import normalize_tf
from markettool.infra.storage.vps_json_store import PostgresDocumentStore, VpsJsonStore, vps_mode_enabled

from markettool.application.services.backtesting_service import get_backtesting_service

logger = logging.getLogger(__name__)


def _get_firestore_db(services: Any):
    if vps_mode_enabled():
        return PostgresDocumentStore.from_env()
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
    try:
        if vps_mode_enabled():
            return VpsJsonStore.from_env()
        bucket_name = getattr(services, "gcs_bucket_name", None) or "markettool_bucket"
        gcs_client = getattr(services, "gcs_client", None)
        if gcs_client is None:
            from google.cloud import storage as gcs_storage
            gcs_client = gcs_storage.Client()
        return gcs_client.bucket(bucket_name)
    except Exception as exc:
        logger.warning("Could not get JSON storage bucket: %s", exc)
        return None


def _load_enriched_json(bucket, gcs_path: str) -> Optional[Dict]:
    try:
        blob = bucket.blob(gcs_path)
        if not blob.exists():
            return None
        return json.loads(blob.download_as_text())
    except Exception as exc:
        logger.warning("Failed to load enriched JSON %s: %s", gcs_path, exc)
        return None


def _find_enriched_for_symbol_tf(db, bucket, exec_id: str, symbol: str, timeframe: str) -> Optional[Dict]:
    try:
        docs = list(db.collection("archivos_generados").where("exec_id", "==", exec_id).stream())
        sym_upper = symbol.upper()
        tf_lower = timeframe.lower()
        for doc in docs:
            data = doc.to_dict() or {}
            nombre = (data.get("metadata", {}).get("nombre") or data.get("gcs_path") or "").upper()
            if sym_upper in nombre and tf_lower.upper() in nombre and "ENRICHED" in nombre:
                gcs_path = data.get("gcs_path") or data.get("metadata", {}).get("gcs_path")
                if gcs_path and bucket:
                    payload = _load_enriched_json(bucket, gcs_path)
                    if payload is not None:
                        return payload
    except Exception as exc:
        logger.warning("archivos_generados query failed: %s", exc)

    if bucket:
        safe_sym = symbol.upper().replace("/", "_")
        gcs_tf = normalize_tf(tf_lower)
        for path in [
            f"analisis/exec/{exec_id}/{safe_sym}_{gcs_tf}_enriched.json",
            f"exec/{exec_id}/{safe_sym}_{gcs_tf}_enriched.json",
        ]:
            payload = _load_enriched_json(bucket, path)
            if payload is not None:
                return payload
    return None


def _bt_ref(db, exec_id: str, symbol: str, timeframe: str):
    """Get Firestore doc ref: ejecuciones/{exec_id}/backtest_results/{SYMBOL}_{tf}"""
    doc_id = f"{symbol.upper()}_{timeframe.lower()}"
    return db.collection("ejecuciones").document(exec_id).collection("backtest_results").document(doc_id)


def register_backtest_routes(app: Flask, *, services) -> None:
    db = _get_firestore_db(services)
    bucket = _get_gcs_bucket(services)
    bt_service = get_backtesting_service(logger=logger)

    # ─── GET /api/backtest/<exec_id> ── batch ──
    @app.route("/api/backtest/<exec_id>", methods=["GET"])
    def backtest_batch_get(exec_id: str):
        try:
            if not db:
                return jsonify({"status": "error", "message": "Firestore not available"}), 503
            docs = list(
                db.collection("ejecuciones").document(exec_id)
                .collection("backtest_results").stream()
            )
            results: Dict[str, Any] = {}
            for doc in docs:
                data = doc.to_dict() or {}
                bt_status = data.get("status", "not_found")
                entry: Dict[str, Any] = {"backtest_status": bt_status}
                if bt_status == "completed" and "stats" in data:
                    entry["stats"] = data["stats"]
                results[doc.id] = entry  # doc.id = SYMBOL_tf
            return jsonify({"status": "ok", "results": results})
        except Exception as exc:
            logger.exception("Error in GET /api/backtest/<exec_id>")
            return jsonify({"status": "error", "message": str(exc)}), 500

    # ─── GET /api/backtest/<exec_id>/<symbol>/<timeframe> ──
    @app.route("/api/backtest/<exec_id>/<symbol>/<timeframe>", methods=["GET"])
    def backtest_get(exec_id: str, symbol: str, timeframe: str):
        try:
            if not db:
                return jsonify({"status": "error", "message": "Firestore not available"}), 503
            ref = _bt_ref(db, exec_id, symbol, timeframe)
            doc = ref.get()
            if not doc.exists:
                return jsonify({"status": "ok", "backtest_status": "not_found"})
            data = doc.to_dict() or {}
            bt_status = data.get("status", "not_found")
            resp: Dict[str, Any] = {"status": "ok", "backtest_status": bt_status}
            if bt_status == "completed" and "stats" in data:
                resp["stats"] = data["stats"]
            return jsonify(resp)
        except Exception as exc:
            logger.exception("Error in GET /api/backtest single")
            return jsonify({"status": "error", "message": str(exc)}), 500

    # ─── POST /api/backtest/run ──
    @app.route("/api/backtest/run", methods=["POST"])
    def backtest_run():
        try:
            body = request.get_json(force=True, silent=True) or {}
            exec_id = str(body.get("exec_id") or "").strip()
            symbol = str(body.get("symbol") or "").strip().upper()
            timeframe = str(body.get("timeframe") or "").strip().lower()
            user_id = str(body.get("user_id") or "").strip()

            if not exec_id or not symbol or not timeframe:
                return jsonify({"status": "error", "message": "exec_id, symbol, and timeframe are required"}), 400

            force = bool(body.get("force", False))
            ref = _bt_ref(db, exec_id, symbol, timeframe) if db else None

            # Force: delete existing cached result first
            if ref and force:
                try:
                    ref.delete()
                except Exception:
                    pass

            # Check existing (skip if force=true)
            if ref and not force:
                try:
                    existing = ref.get()
                    if existing.exists:
                        data = existing.to_dict() or {}
                        ex_status = data.get("status", "")
                        if ex_status == "completed":
                            return jsonify({"status": "ok", "backtest_status": "completed", "stats": data.get("stats", {}), "cached": True})
                        if ex_status == "running":
                            return jsonify({"status": "ok", "backtest_status": "running"})
                except Exception as exc:
                    logger.warning("Cache read failed: %s", exc)

            # Mark running
            if ref:
                try:
                    ref.set({"status": "running", "exec_id": exec_id, "symbol": symbol, "timeframe": timeframe, "user_id": user_id, "created_at": int(time.time() * 1000)})
                except Exception:
                    pass

            # Load enriched
            enriched = _find_enriched_for_symbol_tf(db, bucket, exec_id, symbol, timeframe)
            if enriched is None:
                if ref:
                    try:
                        ref.update({"status": "failed", "error": "No enriched data found", "completed_at": int(time.time() * 1000)})
                    except Exception:
                        pass
                return jsonify({"status": "error", "backtest_status": "failed", "message": f"No enriched data for {symbol}/{timeframe}"}), 404

            candles, entries = [], []
            if isinstance(enriched, list):
                entries = enriched
            elif isinstance(enriched, dict):
                raw_series = enriched.get("candles") or enriched.get("series") or []
                # series can be a dict like { candles: [...] }
                candles = raw_series.get("candles") if isinstance(raw_series, dict) else raw_series
                entries = enriched.get("entries") or enriched.get("entradas") or enriched.get("oportunidades") or enriched.get("records") or []
                if not entries and isinstance(enriched.get("data"), list):
                    entries = enriched["data"]

            try:
                stats = bt_service.run_from_enriched(candles=candles, entries=entries, symbol=symbol, timeframe=timeframe)
            except Exception as exc:
                if ref:
                    try:
                        ref.update({"status": "failed", "error": str(exc), "completed_at": int(time.time() * 1000)})
                    except Exception:
                        pass
                return jsonify({"status": "error", "backtest_status": "failed", "message": str(exc)}), 500

            if ref:
                try:
                    ref.update({"status": "completed", "stats": stats, "completed_at": int(time.time() * 1000)})
                except Exception as exc:
                    logger.warning("Firestore write failed: %s", exc)

            return jsonify({"status": "ok", "backtest_status": "completed", "stats": stats, "cached": False})

        except Exception as exc:
            logger.exception("Error in POST /api/backtest/run")
            return jsonify({"status": "error", "message": str(exc)}), 500

    logger.info("✅ Backtest routes registered (subcollection ejecuciones/{exec_id}/backtest_results/)")
