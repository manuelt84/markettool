"""Pod coordination API routes."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from flask import jsonify


def register_pod_routes(app, pod_coordinator) -> None:
    @app.route("/api/pod/status", methods=["GET"])
    def get_pod_status():
        return jsonify({
            "pod_id": pod_coordinator.pod_id,
            "is_leader": pod_coordinator.is_leader,
            "firestore_enabled": pod_coordinator.firestore_enabled,
            "ttl_seconds": pod_coordinator.ttl_seconds,
            "heartbeat_interval": pod_coordinator.heartbeat_interval,
        })

    @app.route("/api/pod/leader", methods=["GET"])
    def get_cluster_leader():
        if not pod_coordinator._is_firestore_available():
            return jsonify({
                "error": "Firestore not enabled",
                "message": "Multi-pod coordination requires FIRESTORE_ENABLED=true",
            }), 503

        try:
            doc_ref = pod_coordinator.db.document(pod_coordinator.leader_doc_path)
            doc = doc_ref.get()

            if not doc.exists:
                return jsonify({
                    "current_leader": None,
                    "message": "No leader elected yet",
                }), 404

            data = doc.to_dict()
            last_heartbeat_str = data.get("heartbeat_utc")
            last_heartbeat = datetime.fromisoformat(last_heartbeat_str.replace("Z", "+00:00"))
            now_utc = datetime.now(timezone.utc)
            elapsed = (now_utc - last_heartbeat).total_seconds()

            return jsonify({
                "current_leader": data.get("pod_id"),
                "heartbeat_utc": last_heartbeat_str,
                "elected_at_utc": data.get("elected_at_utc"),
                "seconds_since_heartbeat": round(elapsed, 1),
                "is_alive": elapsed < pod_coordinator.ttl_seconds,
                "ttl_seconds": data.get("ttl_seconds", pod_coordinator.ttl_seconds),
            })

        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @app.route("/api/pod/release-leadership", methods=["POST"])
    def release_leadership():
        if not pod_coordinator.is_leader:
            return jsonify({
                "error": "Not leader",
                "message": f"This pod ({pod_coordinator.pod_id}) is not the current leader",
            }), 403

        try:
            loop = asyncio.new_event_loop()
            loop.run_until_complete(pod_coordinator.release_leadership())
            loop.close()

            return jsonify({
                "success": True,
                "message": f"Leadership released by pod {pod_coordinator.pod_id}",
                "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            })

        except Exception as exc:
            return jsonify({"error": str(exc)}), 500
