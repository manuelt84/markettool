"""API routes for historical data management."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

from markettool.core.errors import DataNotFoundError, InsufficientDataError

if TYPE_CHECKING:
    from markettool.interfaces.containers import DIContainer
    from flask import Flask


def _run_async_for_request(coro):
    """Run async coroutine in request handler with proper event loop management."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    try:
        result = loop.run_until_complete(coro)
        
        # Allow pending tasks brief time to initialize
        pending = asyncio.all_tasks(loop)
        if pending:
            try:
                loop.run_until_complete(asyncio.wait(pending, timeout=0.5))
            except asyncio.TimeoutError:
                pass
            except Exception:
                pass
        
        return result
    finally:
        try:
            loop.close()
        except RuntimeError:
            pass


def register_historicos_routes(app: Flask, container: DIContainer, logger: logging.Logger) -> None:
    """
    Register historical data routes.
    
    Args:
        app: Flask application
        container: Dependency container with use cases
        logger: Logger instance
    """
    
    @app.route("/api/v1/historicos/<symbol>/<timeframe>", methods=["GET"])
    def get_historico(symbol: str, timeframe: str):
        """
        Get historical OHLCV data for symbol and timeframe.
        
        Query params:
            - start_date: Optional start date (ISO8601)
            - end_date: Optional end date (ISO8601)
            - use_cache: Whether to use cache (default: true)
        """
        try:
            from flask import request
            
            # Normalize inputs
            symbol = symbol.strip().upper()
            timeframe = timeframe.strip().lower()
            start_date = request.args.get("start_date")
            end_date = request.args.get("end_date")
            use_cache = request.args.get("use_cache", "true").lower() == "true"
            
            historico = _run_async_for_request(container.get_historicos.execute(
                symbol=symbol,
                timeframe=timeframe,
                start_date=start_date,
                end_date=end_date,
                use_cache=use_cache,
            ))
            
            return {
                "status": "ok",
                "data": historico.to_dict(),
            }, 200
        
        except DataNotFoundError as e:
            logger.warning(f"Historico not found: {e}")
            return {"status": "error", "message": str(e)}, 404
        
        except InsufficientDataError as e:
            logger.warning(f"Insufficient data: {e}")
            return {"status": "error", "message": str(e)}, 422
        
        except Exception as e:
            logger.exception(f"Failed to get historico: {e}")
            return {"status": "error", "message": "Internal server error"}, 500
    
    @app.route("/api/v1/historicos/<symbol>/<source_tf>/resample/<target_tf>", methods=["GET"])
    def resample_historico(symbol: str, source_tf: str, target_tf: str):
        """
        Get historical data resampled to different timeframe.
        
        Query params:
            - days_back: Number of days of history (default: 30)
        """
        try:
            from flask import request
            
            # Normalize inputs
            symbol = symbol.strip().upper()
            source_tf = source_tf.strip().lower()
            target_tf = target_tf.strip().lower()
            days_back = int(request.args.get("days_back", 30))
            
            historico = _run_async_for_request(container.get_historicos.execute_with_resample(
                symbol=symbol,
                source_timeframe=source_tf,
                target_timeframe=target_tf,
                days_back=days_back,
            ))
            
            return {
                "status": "ok",
                "data": historico.to_dict(),
            }, 200
        
        except Exception as e:
            logger.exception(f"Resample failed: {e}")
            return {"status": "error", "message": str(e)}, 500
    
    logger.info("Historicos routes registered")
