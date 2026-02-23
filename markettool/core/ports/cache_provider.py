"""Port for cache operations."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Optional, Set, Tuple

from ..models.historico import Historico


class CacheProvider(ABC):
    """
    Port for cache operations.
    Implementations can use memory, Redis, local files, GCS, etc.
    
    🆕 Interface now includes freshness validation contract.
    """
    
    @abstractmethod
    async def get(self, key: str) -> Optional[Any]:
        """
        Get value from cache.
        
        Args:
            key: Cache key
            
        Returns:
            Cached value or None if not found/expired
            
        🆕 Implementations SHOULD validate data freshness before returning.
        """
        pass
    
    @abstractmethod
    async def set(
        self,
        key: str,
        value: Any,
        ttl_seconds: Optional[int] = None,
    ) -> None:
        """
        Set value in cache.
        
        Args:
            key: Cache key
            value: Value to cache
            ttl_seconds: Time-to-live in seconds (None = use default from CACHE_CONFIG)
            
        🆕 Implementations SHOULD use CACHE_CONFIG for default TTLs.
        """
        pass
    
    @abstractmethod
    async def delete(self, key: str) -> None:
        """Delete value from cache."""
        pass
    
    @abstractmethod
    async def exists(self, key: str) -> bool:
        """
        Check if key exists in cache.
        
        🆕 Should check TTL expiration, not just key presence.
        """
        pass
    
    @abstractmethod
    async def clear(self) -> None:
        """Clear entire cache."""
        pass
    
    @abstractmethod
    async def get_historico(
        self,
        symbol: str,
        timeframe: str,
    ) -> Optional[Historico]:
        """
        Get cached historical data.
        
        🆕 Implementations SHOULD validate data freshness per timeframe.
        """
        pass
    
    @abstractmethod
    async def set_historico(
        self,
        historico: Historico,
        ttl_seconds: Optional[int] = None,
    ) -> None:
        """
        Cache historical data.
        
        Args:
            historico: Historical data to cache
            ttl_seconds: Time-to-live (None = use timeframe-specific default)
        """
        pass
    
    @abstractmethod
    async def invalidate_historico(self, symbol: str, timeframe: str) -> None:
        """Invalidate cached historical data."""
        pass
    
    @abstractmethod
    async def get_cache_stats(self) -> dict:
        """
        Get cache statistics (hits, misses, size, etc.).
        
        🆕 Should include freshness stats if available:
        - fresh_count: Number of fresh entries
        - stale_count: Number of stale entries
        - ttl_config: Current TTL configuration
        """
        pass
    
    @abstractmethod
    async def warmup(self, symbols: Set[str]) -> None:
        """
        Pre-load cache with common symbols.
        
        🆕 Implementations SHOULD validate data freshness before caching.
        """
        pass
    
    def validate_freshness(
        self,
        data: Any,
        symbol: str,
        timeframe: str,
    ) -> Tuple[bool, int, str]:
        """
        🆕 OPTIONAL: Validate data freshness.
        
        Implementations can override this to provide custom freshness validation.
        Default delegates to cache_config.validate_data_freshness().
        
        Args:
            data: Data to validate (DataFrame, Historico, etc.)
            symbol: Symbol name
            timeframe: Timeframe
            
        Returns:
            (is_fresh, age_seconds, reason) tuple
        """
        from markettool.core.cache_config import validate_data_freshness
        import pandas as pd
        
        # Extract DataFrame if needed
        df = None
        if isinstance(data, pd.DataFrame):
            df = data
        elif hasattr(data, 'df'):
            df = data.df
        
        if df is None or df.empty:
            return (False, 0, "No data to validate")
        
        return validate_data_freshness(df, symbol, timeframe)
