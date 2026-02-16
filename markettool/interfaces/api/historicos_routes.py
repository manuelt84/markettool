"""API routes for historical data management."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from markettool.core.errors import DataNotFoundError, InsufficientDataError

if TYPE_CHECKING:
    from markettool.interfaces.containers import DIContainer
    from flask import Flask


def register_historicos_routes(app: Flask, container: DIContainer, logger: logging.Logger) -> None:
    """
    Register historical data routes.
    
    Args:
        app: Flask application
        container: Dependency container with use cases
        logger: Logger instance
    """
    
    @app.route("/api/v1/historicos/<symbol>/<timeframe>", methods=["GET"])
    async def get_historico(symbol: str, timeframe: str):
        """
        Get historical OHLCV data for symbol and timeframe.
        
        Query params:
            - start_date: Optional start date (ISO8601)
            - end_date: Optional end date (ISO8601)
            - use_cache: Whether to use cache (default: true)
        """
        try:
            from flask import request
            
            start_date = request.args.get("start_date")
            end_date = request.args.get("end_date")
            use_cache = request.args.get("use_cache", "true").lower() == "true"
            
            historico = await container.get_historicos.execute(
                symbol=symbol,
                timeframe=timeframe,
                start_date=start_date,
                end_date=end_date,
                use_cache=use_cache,
            )
            
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
    async def resample_historico(symbol: str, source_tf: str, target_tf: str):
        """
        Get historical data resampled to different timeframe.
        
        Query params:
            - days_back: Number of days of history (default: 30)
        """
        try:
            from flask import request
            
            days_back = int(request.args.get("days_back", 30))
            
            historico = await container.get_historicos.execute_with_resample(
                symbol=symbol,
                source_timeframe=source_tf,
                target_timeframe=target_tf,
                days_back=days_back,
            )
            
            return {
                "status": "ok",
                "data": historico.to_dict(),
            }, 200
        
        except Exception as e:
            logger.exception(f"Resample failed: {e}")
            return {"status": "error", "message": str(e)}, 500
    
    logger.info("Historicos routes registered")
