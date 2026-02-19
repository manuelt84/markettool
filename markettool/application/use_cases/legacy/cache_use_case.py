"""Legacy cache use case."""

from __future__ import annotations

from typing import Tuple


class LegacyCacheUseCase:
    def __init__(self, services):
        self._services = services

    def invalidate(self, symbol: str | None, timeframe: str | None) -> Tuple[dict, int]:
        try:
            if not symbol or not timeframe:
                return {"error": "Missing symbol or timeframe"}, 400

            self._services.indicators_cache.invalidate(symbol, timeframe)
            return {
                "status": "ok",
                "message": f"Cache invalidated for {symbol}/{timeframe}",
            }, 200
        except Exception as exc:
            return {"error": str(exc)}, 500

    def stats(self) -> Tuple[dict, int]:
        try:
            cached_keys = list(self._services.indicators_cache._memory_cache.keys())
            return {
                "enabled": self._services.cache_enabled,
                "memory_cache_size": len(cached_keys),
                "ttl_hours": self._services.ttl_hours,
                "force_recalc": self._services.force_recalc,
                "cached_symbols": cached_keys,
            }, 200
        except Exception as exc:
            return {"error": str(exc)}, 500

    def clear(self) -> Tuple[dict, int]:
        try:
            count = len(self._services.indicators_cache._memory_cache)
            self._services.indicators_cache._memory_cache.clear()
            self._services.indicators_cache._memory_cache_ttl.clear()
            return {
                "status": "ok",
                "cleared_items": count,
                "message": "Memory cache cleared (GCS data preserved)",
            }, 200
        except Exception as exc:
            return {"error": str(exc)}, 500

    def metadata(self, symbol: str | None, timeframe: str | None) -> Tuple[dict, int]:
        try:
            if not symbol or not timeframe:
                return {"error": "Missing symbol or timeframe parameters"}, 400

            if self._services.indicators_cache.db:
                doc_id = self._services.indicators_cache._metadata_doc_id(symbol, timeframe)
                doc = self._services.indicators_cache.db.collection("indicators_metadata").document(doc_id).get()

                if doc.exists:
                    metadata = doc.to_dict()
                    if "last_update_utc" in metadata:
                        metadata["last_update_utc"] = metadata["last_update_utc"].isoformat()

                    return {
                        "exists": True,
                        "metadata": metadata,
                    }, 200

                return {
                    "exists": False,
                    "message": f"No metadata found for {symbol}/{timeframe}",
                }, 404

            return {"error": "Firestore not available"}, 503
        except Exception as exc:
            return {"error": str(exc)}, 500
