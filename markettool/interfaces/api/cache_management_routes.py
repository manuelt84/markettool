"""API routes for cache operations."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from markettool.interfaces.containers import DIContainer
    from flask import Flask


def register_cache_routes(app: Flask, container: DIContainer, logger: logging.Logger) -> None:
    """
    Register cache management routes.
    
    Args:
        app: Flask application
        container: Dependency container with use cases
        logger: Logger instance
    """
    
    @app.route("/api/v1/cache/stats", methods=["GET"])
    def v1_get_cache_stats():
        """Get cache statistics (hits, misses, size)."""
        try:
            stats = asyncio.run(container.warm_cache.get_cache_stats())
            return {
                "status": "ok",
                "stats": stats,
            }, 200
        
        except Exception as e:
            logger.exception(f"Failed to get cache stats: {e}")
            return {"status": "error", "message": str(e)}, 500
    
    @app.route("/api/v1/cache/warmup", methods=["POST"])
    def v1_warmup_cache():
        """
        Warm cache with frequently used symbols.
        
        Request body (optional):
            {
                "symbols": ["AAPL", "GOOGL"],
                "timeframes": ["1hour", "1day"],
                "force": false
            }
        """
        try:
            from flask import request
            
            data = request.get_json() or {}
            symbols = set(data.get("symbols", ["AAPL", "GOOGL", "MSFT"]))
            timeframes = data.get("timeframes", ["1hour", "1day"])
            force = data.get("force", False)
            
            result = asyncio.run(container.warm_cache.execute(
                symbols=symbols,
                timeframes=timeframes,
                force=force,
            ))
            
            return {
                "status": "ok",
                "result": result,
            }, 200
        
        except Exception as e:
            logger.exception(f"Cache warmup failed: {e}")
            return {"status": "error", "message": str(e)}, 500
    
    @app.route("/api/v1/cache/clear", methods=["POST"])
    def v1_clear_cache():
        """Clear entire cache."""
        try:
            asyncio.run(container.warm_cache.cache.clear())
            return {
                "status": "ok",
                "message": "Cache cleared",
            }, 200
        
        except Exception as e:
            logger.exception(f"Failed to clear cache: {e}")
            return {"status": "error", "message": str(e)}, 500
    
    @app.route("/api/v1/cache/invalidate/<symbol>/<timeframe>", methods=["DELETE"])
    def v1_invalidate_cache(symbol: str, timeframe: str):
        """Invalidate cache for specific symbol/timeframe."""
        try:
            asyncio.run(container.warm_cache.cache.invalidate_historico(symbol, timeframe))
            return {
                "status": "ok",
                "message": f"Cache invalidated for {symbol}/{timeframe}",
            }, 200
        
        except Exception as e:
            logger.exception(f"Failed to invalidate cache: {e}")
            return {"status": "error", "message": str(e)}, 500
    
    logger.info("Cache routes registered")
