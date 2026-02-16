"""Execution tracking API routes."""

from __future__ import annotations

import asyncio
import socket
from flask import jsonify


def register_execution_routes(app, execution_tracker, running, logger) -> None:
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
        if execution_tracker.firestore_enabled and execution_tracker.db:
            try:
                doc = execution_tracker.db.collection("ejecuciones").document(exec_id).get()
                if doc.exists:
                    return jsonify(doc.to_dict())
            except Exception as exc:
                return jsonify({"error": str(exc)}), 500

        if exec_id in running:
            return jsonify({
                "exec_id": exec_id,
                "estado": "running" if not running[exec_id].done() else "completed",
                "pod_id": socket.gethostname(),
            })

        return jsonify({
            "error": "Not found",
            "message": f"Execution {exec_id} not found",
        }), 404
