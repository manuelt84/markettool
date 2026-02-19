"""Cache API routes."""

from __future__ import annotations

from flask import jsonify, request

from markettool.application.use_cases.legacy import LegacyCacheUseCase


def register_cache_routes(app, *, services) -> None:
    use_case = LegacyCacheUseCase(services)
    @app.route("/api/cache/invalidate", methods=["POST"])
    def api_cache_invalidate():
        data = request.get_json() or {}
        payload, status = use_case.invalidate(data.get("symbol"), data.get("timeframe"))
        return jsonify(payload), status

    @app.route("/api/cache/stats", methods=["GET"])
    def api_cache_stats():
        payload, status = use_case.stats()
        return jsonify(payload), status

    @app.route("/api/cache/clear", methods=["POST"])
    def api_cache_clear():
        payload, status = use_case.clear()
        return jsonify(payload), status

    @app.route("/api/cache/metadata", methods=["GET"])
    def api_cache_metadata():
        payload, status = use_case.metadata(
            request.args.get("symbol"),
            request.args.get("timeframe"),
        )
        return jsonify(payload), status
