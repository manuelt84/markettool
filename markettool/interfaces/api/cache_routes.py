"""Cache API routes."""

from __future__ import annotations

from flask import jsonify, request


def register_cache_routes(app, indicators_cache, cache_enabled: bool, ttl_hours: int, force_recalc: bool) -> None:
    @app.route("/api/cache/invalidate", methods=["POST"])
    def api_cache_invalidate():
        try:
            data = request.get_json()
            symbol = data.get("symbol")
            timeframe = data.get("timeframe")

            if not symbol or not timeframe:
                return jsonify({"error": "Missing symbol or timeframe"}), 400

            indicators_cache.invalidate(symbol, timeframe)

            return jsonify({
                "status": "ok",
                "message": f"Cache invalidated for {symbol}/{timeframe}",
            }), 200

        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @app.route("/api/cache/stats", methods=["GET"])
    def api_cache_stats():
        try:
            cached_keys = list(indicators_cache._memory_cache.keys())

            return jsonify({
                "enabled": cache_enabled,
                "memory_cache_size": len(cached_keys),
                "ttl_hours": ttl_hours,
                "force_recalc": force_recalc,
                "cached_symbols": cached_keys,
            }), 200

        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @app.route("/api/cache/clear", methods=["POST"])
    def api_cache_clear():
        try:
            count = len(indicators_cache._memory_cache)
            indicators_cache._memory_cache.clear()
            indicators_cache._memory_cache_ttl.clear()

            return jsonify({
                "status": "ok",
                "cleared_items": count,
                "message": "Memory cache cleared (GCS data preserved)",
            }), 200

        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @app.route("/api/cache/metadata", methods=["GET"])
    def api_cache_metadata():
        try:
            symbol = request.args.get("symbol")
            timeframe = request.args.get("timeframe")

            if not symbol or not timeframe:
                return jsonify({"error": "Missing symbol or timeframe parameters"}), 400

            if indicators_cache.db:
                doc_id = indicators_cache._metadata_doc_id(symbol, timeframe)
                doc = indicators_cache.db.collection("indicators_metadata").document(doc_id).get()

                if doc.exists:
                    metadata = doc.to_dict()
                    if "last_update_utc" in metadata:
                        metadata["last_update_utc"] = metadata["last_update_utc"].isoformat()

                    return jsonify({
                        "exists": True,
                        "metadata": metadata,
                    }), 200

                return jsonify({
                    "exists": False,
                    "message": f"No metadata found for {symbol}/{timeframe}",
                }), 404

            return jsonify({"error": "Firestore not available"}), 503

        except Exception as exc:
            return jsonify({"error": str(exc)}), 500
