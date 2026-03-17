"""Execution tracking API routes."""

from __future__ import annotations

import asyncio
import socket
import time
from flask import jsonify


def register_execution_routes(app, execution_tracker, running, logger) -> None:
    # Local status cache prevents false 404s during transient persistence outages.
    status_cache: dict[str, dict] = {}
    status_cache_ttl_seconds = 300

    def _cache_status(exec_id: str, payload: dict) -> None:
        status_cache[exec_id] = {
            "payload": payload,
            "cached_at": time.time(),
        }

    def _get_cached_status(exec_id: str) -> dict | None:
        cached = status_cache.get(exec_id)
        if not cached:
            return None

        age_seconds = time.time() - float(cached.get("cached_at", 0))
        if age_seconds > status_cache_ttl_seconds:
            status_cache.pop(exec_id, None)
            return None

        payload = dict(cached.get("payload", {}))
        payload["source"] = "local_status_cache"
        payload["cached_age_seconds"] = int(age_seconds)
        return payload

    @app.route("/api/execution/<exec_id>/cancel", methods=["POST"])
    def cancel_execution(exec_id: str):
        loop = asyncio.new_event_loop()
        try:
            success = loop.run_until_complete(execution_tracker.request_cancel(exec_id))
            loop.close()

            if not success:
                return jsonify({
                    "error": "Not found",
                    "message": f"Execution {exec_id} not found",
                }), 404

            if exec_id in running:
                logger.info("[API] Cancelling local execution: %s", exec_id)
                running[exec_id].cancel()

            return jsonify({
                "success": True,
                "message": f"Cancellation requested for {exec_id}",
            })

        except Exception as exc:
            loop.close()
            return jsonify({"error": str(exc)}), 500

    @app.route("/api/execution/<exec_id>/status", methods=["GET"])
    def get_execution_status(exec_id: str):
        # 1) Persistent source of truth (Firestore)
        if execution_tracker.firestore_enabled and execution_tracker.db:
            try:
                doc = execution_tracker.db.collection("ejecuciones").document(exec_id).get()
                if doc.exists:
                    payload = doc.to_dict()
                    payload["source"] = "firestore"
                    _cache_status(exec_id, payload)
                    return jsonify(payload)
            except Exception as exc:
                logger.warning("[API] Firestore status read failed for %s: %s", exec_id, exc)

        # 2) Local in-process execution map
        if exec_id in running:
            payload = {
                "exec_id": exec_id,
                "estado": "running" if not running[exec_id].done() else "completed",
                "pod_id": socket.gethostname(),
                "source": "local_running_map",
            }
            _cache_status(exec_id, payload)
            return jsonify(payload)

        # 3) Last known status cache (short TTL)
        cached = _get_cached_status(exec_id)
        if cached:
            return jsonify(cached)

        if not execution_tracker.firestore_enabled or not execution_tracker.db:
            return jsonify({
                "error": "Execution status unavailable",
                "message": "Persistent tracking is disabled (FIRESTORE_ENABLED=false)",
                "exec_id": exec_id,
            }), 503

        return jsonify({
            "error": "Not found",
            "message": f"Execution {exec_id} not found",
        }), 404
