"""In-memory cache implementation."""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional, Set

from markettool.core.models.historico import Historico


class MemoryCache:
    """In-memory cache with TTL support."""
    
    def __init__(self, logger: Optional[logging.Logger] = None):
        self.logger = logger or logging.getLogger(__name__)
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._stats = {"hits": 0, "misses": 0}
    
    async def get(self, key: str) -> Optional[Any]:
        """Get from cache."""
        if key in self._cache:
            entry = self._cache[key]
            if entry["ttl"] is None or time.time() < entry["ttl"]:
                self._stats["hits"] += 1
                return entry["value"]
            else:
                del self._cache[key]
        
        self._stats["misses"] += 1
        return None
    
    async def set(
        self,
        key: str,
        value: Any,
        ttl_seconds: Optional[int] = None,
    ) -> None:
        """Set in cache."""
        ttl = None if ttl_seconds is None else time.time() + ttl_seconds
        self._cache[key] = {"value": value, "ttl": ttl}
    
    async def delete(self, key: str) -> None:
        """Delete from cache."""
        self._cache.pop(key, None)
    
    async def exists(self, key: str) -> bool:
        """Check if key exists."""
        return await self.get(key) is not None
    
    async def clear(self) -> None:
        """Clear cache."""
        self._cache.clear()
    
    async def get_historico(self, symbol: str, timeframe: str) -> Optional[Historico]:
        """Get cached historico."""
        return await self.get(f"historicos:{symbol}:{timeframe}")
    
    async def set_historico(self, historico: Historico, ttl_seconds: Optional[int] = None) -> None:
        """Cache historico."""
        await self.set(f"historicos:{historico.symbol}:{historico.timeframe}", historico, ttl_seconds)
    
    async def invalidate_historico(self, symbol: str, timeframe: str) -> None:
        """Invalidate cached historico."""
        await self.delete(f"historicos:{symbol}:{timeframe}")
    
    async def get_cache_stats(self) -> dict:
        """Get cache statistics."""
        total = self._stats["hits"] + self._stats["misses"]
        hit_rate = (self._stats["hits"] / total * 100) if total > 0 else 0
        return {
            "hits": self._stats["hits"],
            "misses": self._stats["misses"],
            "hit_rate": hit_rate,
            "size": len(self._cache),
        }
    
    async def warmup(self, symbols: Set[str]) -> None:
        """Warmup cache (placeholder)."""
        pass
