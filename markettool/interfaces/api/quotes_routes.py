"""API routes for market quotes."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from markettool.core.errors import DataNotFoundError

if TYPE_CHECKING:
    from markettool.interfaces.containers import DIContainer
    from flask import Flask


def register_quotes_routes(app: Flask, container: DIContainer, logger: logging.Logger) -> None:
    """
    Register market quote routes.
    
    Args:
        app: Flask application
        container: Dependency container with use cases
        logger: Logger instance
    """
    
    @app.route("/api/v1/quotes/<symbol>", methods=["GET"])
    def get_quote(symbol: str):
        """
        Get current market quote for symbol.
        
        Query params:
            - cache_ttl: Cache TTL in seconds (default: 60)
        """
        try:
            from flask import request
            
            # Normalize symbol
            symbol = symbol.strip().upper()
            cache_ttl = int(request.args.get("cache_ttl", 60))
            
            quote = asyncio.run(container.get_quote.execute_with_cache(
                symbol=symbol,
                cache_ttl_seconds=cache_ttl,
            ))
            
            return {
                "status": "ok",
                "data": quote.to_dict(),
            }, 200
        
        except DataNotFoundError as e:
            logger.warning(f"Quote not found: {e}")
            return {"status": "error", "message": str(e)}, 404
        
        except Exception as e:
            logger.exception(f"Failed to get quote: {e}")
            return {"status": "error", "message": "Internal server error"}, 500
    
    @app.route("/api/v1/quotes", methods=["POST"])
    def get_quotes_batch():
        """
        Get quotes for multiple symbols.
        
        Request body:
            {
                "symbols": ["AAPL", "GOOGL", "MSFT"]
            }
        """
        try:
            from flask import request
            
            data = request.get_json()
            symbols = [s.strip().upper() for s in data.get("symbols", [])]
            
            if not symbols:
                return {"status": "error", "message": "Missing symbols"}, 400
            
            quotes = asyncio.run(container.get_quote.execute_batch(symbols))
            
            return {
                "status": "ok",
                "data": {s: q.to_dict() for s, q in quotes.items()},
                "count": len(quotes),
            }, 200
        
        except Exception as e:
            logger.exception(f"Batch quote fetch failed: {e}")
            return {"status": "error", "message": str(e)}, 500
    
    @app.route("/api/v1/quotes/supported", methods=["GET"])
    def get_supported_symbols():
        """Get list of supported symbols."""
        try:
            symbols = asyncio.run(container.get_quote.get_supported_symbols())
            return {
                "status": "ok",
                "symbols": symbols,
                "count": len(symbols),
            }, 200
        
        except Exception as e:
            logger.exception(f"Failed to get supported symbols: {e}")
            return {"status": "error", "message": str(e)}, 500
    
    logger.info("Quotes routes registered")
