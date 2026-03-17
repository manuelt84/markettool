"""API routes for analysis and trading signals."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

from markettool.core.errors import AnalysisError, InsufficientDataError

if TYPE_CHECKING:
    from markettool.interfaces.containers import DIContainer
    from flask import Flask


def _run_async_for_request(coro):
    """Run async coroutine in request handler with proper event loop management.
    
    Creates a dedicated event loop for each request that doesn't prematurely
    close while pending tasks are still running.
    """
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
            # Expected if tasks are still pending
            pass


def register_analysis_routes(app: Flask, container: DIContainer, logger: logging.Logger) -> None:
    """
    Register analysis and signal routes.
    
    Args:
        app: Flask application
        container: Dependency container with use cases
        logger: Logger instance
    """
    
    @app.route("/api/v1/analysis/<symbol>/<timeframe>", methods=["GET"])
    def analyze_symbol(symbol: str, timeframe: str):
        """
        Run technical analysis on symbol and generate trading signals.
        
        Query params:
            - analysis_type: Type of analysis (default: technical)
        """
        try:
            from flask import request
            
            # Normalize inputs
            symbol = symbol.strip().upper()
            timeframe = timeframe.strip().lower()
            analysis_type = request.args.get("analysis_type", "technical")
            
            # First get historical data
            historico = _run_async_for_request(container.get_historicos.execute(
                symbol=symbol,
                timeframe=timeframe,
                use_cache=True,
            ))
            
            # Run analysis
            signals = _run_async_for_request(container.run_analysis.execute(
                historico=historico,
                analysis_type=analysis_type,
            ))
            
            return {
                "status": "ok",
                "symbol": symbol,
                "timeframe": timeframe,
                "analysis_type": analysis_type,
                "signals": [s.to_dict() for s in signals.signals],
                "signal_count": len(signals),
            }, 200
        
        except InsufficientDataError as e:
            logger.warning(f"Insufficient data: {e}")
            return {"status": "error", "message": str(e)}, 422
        
        except AnalysisError as e:
            logger.warning(f"Analysis failed: {e}")
            return {"status": "error", "message": str(e)}, 422
        
        except Exception as e:
            logger.exception(f"Analysis failed: {e}")
            return {"status": "error", "message": "Internal server error"}, 500
    
    @app.route("/api/v1/analysis/batch", methods=["POST"])
    def analyze_batch():
        """
        Run analysis on multiple symbols.
        
        Request body:
            {
                "symbols": ["AAPL", "GOOGL"],
                "timeframe": "1day",
                "analysis_type": "technical"
            }
        """
        try:
            from flask import request
            
            data = request.get_json()
            symbols = data.get("symbols", [])
            timeframe = data.get("timeframe", "1day")
            analysis_type = data.get("analysis_type", "technical")
            
            if not symbols:
                return {"status": "error", "message": "Missing symbols"}, 400
            
            results = {}
            for symbol in symbols:
                try:
                    # Normalize symbol
                    sym_normalized = symbol.strip().upper()
                    tf_normalized = timeframe.strip().lower()
                    historico = _run_async_for_request(container.get_historicos.execute(
                        symbol=sym_normalized,
                        timeframe=tf_normalized,
                        use_cache=True,
                    ))
                    signals = _run_async_for_request(container.run_analysis.execute(
                        historico=historico,
                        analysis_type=analysis_type,
                    ))
                    results[symbol] = [s.to_dict() for s in signals.signals]
                except Exception as e:
                    logger.error(f"Failed to analyze {symbol}: {e}")
                    results[symbol] = {"error": str(e)}
            
            return {
                "status": "ok",
                "results": results,
                "count": len([r for r in results.values() if not isinstance(r, dict) or "error" not in r]),
            }, 200
        
        except Exception as e:
            logger.exception(f"Batch analysis failed: {e}")
            return {"status": "error", "message": str(e)}, 500
    
    logger.info("Analysis routes registered")
