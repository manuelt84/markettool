"""FMP-based quote provider adapter."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import pytz

from markettool.core.models.quote import Quote
from markettool.core.ports.quote_provider import QuoteProvider
from markettool.core.errors import ExternalAPIError, DataNotFoundError


class FMPQuoteProvider(QuoteProvider):
    """
    Quote provider that fetches current market data from FMP API.
    Acts as adapter between domain and FMP infrastructure.
    """
    
    def __init__(
        self,
        fmp_client,
        cache: Optional[dict] = None,
        cache_ttl_seconds: int = 60,
        logger: Optional[logging.Logger] = None,
    ):
        """
        Initialize FMP quote provider.
        
        Args:
            fmp_client: FMP API client
            cache: Optional simple cache dict
            cache_ttl_seconds: Cache TTL in seconds
            logger: Optional logger
        """
        self.fmp = fmp_client
        self.cache = cache or {}
        self.cache_ttl = cache_ttl_seconds
        self.logger = logger or logging.getLogger(__name__)
    
    async def get_quote(self, symbol: str) -> Quote:
        """Get current quote from FMP API."""
        try:
            # Check cache first
            cached = self._get_cached(symbol)
            if cached:
                return cached
            
            # Fetch from FMP
            self.logger.debug(f"Fetching quote for {symbol} from FMP")
            
            # Call FMP client (would be implemented in MarketTool context)
            quote_data = await self._fetch_from_fmp(symbol)
            
            if not quote_data:
                raise DataNotFoundError(f"No quote data for {symbol}")
            
            quote = self._map_to_quote(symbol, quote_data)
            
            # Cache result
            self._cache_quote(symbol, quote)
            
            return quote
        
        except Exception as e:
            self.logger.error(f"Failed to get quote for {symbol}: {e}")
            raise ExternalAPIError(f"FMP API error: {e}")
    
    async def get_quotes(self, symbols: List[str]) -> Dict[str, Quote]:
        """Get quotes for multiple symbols."""
        results = {}
        for symbol in symbols:
            try:
                quote = await self.get_quote(symbol)
                results[symbol] = quote
            except Exception as e:
                self.logger.warning(f"Failed to get quote for {symbol}: {e}")
        
        return results
    
    async def get_quote_with_cache(
        self,
        symbol: str,
        cache_ttl_seconds: int = 60,
    ) -> Quote:
        """Get quote with cache support."""
        # Check cache first
        cached = self._get_cached(symbol)
        if cached:
            return cached
        
        # Fetch and cache
        quote = await self.get_quote(symbol)
        self._cache_quote(symbol, quote, ttl=cache_ttl_seconds)
        return quote
    
    def supported_symbols(self) -> List[str]:
        """Get supported symbols."""
        # Could be loaded from config
        return [
            "AAPL", "GOOGL", "MSFT", "TSLA", "AMZN",
            "EURUSD", "GBPUSD", "JPYUSD",
            "GOLD", "CRUDE",
        ]
    
    async def is_available(self) -> bool:
        """Check if FMP is available."""
        try:
            # Try fetching a known symbol
            await self.get_quote("AAPL")
            return True
        except Exception:
            return False
    
    async def _fetch_from_fmp(self, symbol: str) -> Optional[dict]:
        """
        Fetch quote data from FMP.
        Placeholder - implement using FMP client in MarketTool context.
        """
        # This would call FMP client
        # return await self.fmp.get_quote(symbol)
        raise NotImplementedError("Implement FMP fetch in MarketTool.py context")
    
    def _map_to_quote(self, symbol: str, fmp_data: dict) -> Quote:
        """Map FMP response to Quote model."""
        return Quote(
            symbol=symbol,
            price=float(fmp_data.get("price", 0)),
            bid=float(fmp_data.get("bid")) if "bid" in fmp_data else None,
            ask=float(fmp_data.get("ask")) if "ask" in fmp_data else None,
            timestamp=datetime.now(pytz.UTC),
            change=float(fmp_data.get("change")) if "change" in fmp_data else None,
            change_pct=float(fmp_data.get("changePercent")) if "changePercent" in fmp_data else None,
            volume=float(fmp_data.get("volume")) if "volume" in fmp_data else None,
            source="fmp",
        )
    
    def _get_cached(self, symbol: str) -> Optional[Quote]:
        """Get from simple cache."""
        if symbol in self.cache:
            entry = self.cache[symbol]
            if datetime.now() < entry.get("expires", datetime.min):
                return entry["quote"]
        return None
    
    def _cache_quote(self, symbol: str, quote: Quote, ttl: Optional[int] = None) -> None:
        """Store in simple cache."""
        self.cache[symbol] = {
            "quote": quote,
            "expires": datetime.now() + timedelta(seconds=ttl or self.cache_ttl),
        }
