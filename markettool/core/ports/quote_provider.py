"""Port for getting current market quotes."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Dict, List, Optional

from ..models.quote import Quote


class QuoteProvider(ABC):
    """
    Port for fetching current market quotes.
    Implementations can use APIs, websocket streams, etc.
    """
    
    @abstractmethod
    async def get_quote(self, symbol: str) -> Quote:
        """
        Get current quote for a symbol.
        
        Args:
            symbol: Trading symbol
            
        Returns:
            Current Quote
            
        Raises:
            DataNotFoundError: If quote not available
            ExternalAPIError: If API fails
        """
        pass
    
    @abstractmethod
    async def get_quotes(self, symbols: List[str]) -> Dict[str, Quote]:
        """
        Get quotes for multiple symbols.
        
        Args:
            symbols: List of trading symbols
            
        Returns:
            Dict mapping symbol to Quote
        """
        pass
    
    @abstractmethod
    async def get_quote_with_cache(
        self,
        symbol: str,
        cache_ttl_seconds: int = 60,
    ) -> Quote:
        """
        Get quote with optional caching.
        
        Args:
            symbol: Trading symbol
            cache_ttl_seconds: Cache time-to-live in seconds
            
        Returns:
            Current Quote (from cache or fresh)
        """
        pass
    
    @abstractmethod
    def supported_symbols(self) -> List[str]:
        """Get list of symbols supported by provider."""
        pass
    
    @abstractmethod
    async def is_available(self) -> bool:
        """Check if service is available."""
        pass
