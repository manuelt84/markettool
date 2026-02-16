"""Port for cache operations."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Optional, Set

from ..models.historico import Historico


class CacheProvider(ABC):
    """
    Port for cache operations.
    Implementations can use memory, Redis, local files, GCS, etc.
    """
    
    @abstractmethod
    async def get(self, key: str) -> Optional[Any]:
        """
        Get value from cache.
        
        Args:
            key: Cache key
            
        Returns:
            Cached value or None if not found/expired
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
            ttl_seconds: Time-to-live in seconds (None = no expiry)
        """
        pass
    
    @abstractmethod
    async def delete(self, key: str) -> None:
        """Delete value from cache."""
        pass
    
    @abstractmethod
    async def exists(self, key: str) -> bool:
        """Check if key exists in cache."""
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
        """Get cached historical data."""
        pass
    
    @abstractmethod
    async def set_historico(
        self,
        historico: Historico,
        ttl_seconds: Optional[int] = None,
    ) -> None:
        """Cache historical data."""
        pass
    
    @abstractmethod
    async def invalidate_historico(self, symbol: str, timeframe: str) -> None:
        """Invalidate cached historical data."""
        pass
    
    @abstractmethod
    async def get_cache_stats(self) -> dict:
        """Get cache statistics (hits, misses, size, etc.)."""
        pass
    
    @abstractmethod
    async def warmup(self, symbols: Set[str]) -> None:
        """Pre-load cache with common symbols."""
        pass
