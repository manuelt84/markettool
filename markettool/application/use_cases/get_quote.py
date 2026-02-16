"""Use case: Get current market quotes."""

from __future__ import annotations

import logging
from typing import Dict, List, Optional

from markettool.core.models.quote import Quote
from markettool.core.ports.quote_provider import QuoteProvider
from markettool.core.errors import DataNotFoundError


class GetQuoteUseCase:
    """
    Orchestrates fetching current market quotes with fallback providers.
    """
    
    def __init__(
        self,
        primary_provider: QuoteProvider,
        fallback_providers: Optional[List[QuoteProvider]] = None,
        logger: Optional[logging.Logger] = None,
    ):
        self.primary_provider = primary_provider
        self.fallback_providers = fallback_providers or []
        self.logger = logger or logging.getLogger(__name__)
    
    async def execute(self, symbol: str) -> Quote:
        """
        Get quote for a symbol with fallback logic.
        
        Args:
            symbol: Trading symbol
            
        Returns:
            Current Quote
            
        Raises:
            DataNotFoundError: If no provider has available data
        """
        # Try primary provider
        try:
            quote = await self.primary_provider.get_quote(symbol)
            self.logger.debug(f"Got quote for {symbol} from primary provider")
            return quote
        except Exception as e:
            self.logger.warning(
                f"Primary provider failed for {symbol}: {e}"
            )
        
        # Try fallback providers
        for i, provider in enumerate(self.fallback_providers, 1):
            try:
                self.logger.info(f"Trying fallback provider {i} for {symbol}")
                quote = await provider.get_quote(symbol)
                self.logger.debug(f"Got quote for {symbol} from fallback provider {i}")
                return quote
            except Exception as e:
                self.logger.warning(
                    f"Fallback provider {i} failed for {symbol}: {e}"
                )
        
        # All providers failed
        raise DataNotFoundError(
            f"Could not fetch quote for {symbol} from any provider"
        )
    
    async def execute_batch(self, symbols: List[str]) -> Dict[str, Quote]:
        """
        Get quotes for multiple symbols.
        
        Args:
            symbols: List of trading symbols
            
        Returns:
            Dict mapping symbol to Quote
        """
        results = {}
        
        # First try bulk fetch from primary provider
        try:
            quotes = await self.primary_provider.get_quotes(symbols)
            self.logger.debug(f"Got {len(quotes)} quotes from primary provider")
            results.update(quotes)
        except Exception as e:
            self.logger.warning(f"Primary provider batch failed: {e}")
        
        # Fetch missing symbols individually
        missing = set(symbols) - set(results.keys())
        for symbol in missing:
            try:
                quote = await self.execute(symbol)
                results[symbol] = quote
            except DataNotFoundError:
                self.logger.warning(f"Could not fetch quote for {symbol}")
        
        return results
    
    async def execute_with_cache(
        self,
        symbol: str,
        cache_ttl_seconds: int = 60,
    ) -> Quote:
        """
        Get quote with caching if provider supports it.
        
        Args:
            symbol: Trading symbol
            cache_ttl_seconds: Cache TTL in seconds
            
        Returns:
            Current Quote
        """
        try:
            quote = await self.primary_provider.get_quote_with_cache(
                symbol,
                cache_ttl_seconds=cache_ttl_seconds,
            )
            return quote
        except Exception:
            # Fallback to non-cached version
            return await self.execute(symbol)
    
    async def get_supported_symbols(self) -> List[str]:
        """Get symbols supported by primary provider."""
        return self.primary_provider.supported_symbols()
