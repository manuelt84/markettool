"""In-memory cache implementation."""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, Optional, Set

from markettool.core.models.historico import Historico
from markettool.core.cache_config import CACHE_CONFIG


class MemoryCache:
    """In-memory cache with TTL support and thread-safe operations."""
    
    def __init__(self, logger: Optional[logging.Logger] = None):
        self.logger = logger or logging.getLogger(__name__)
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._stats = {"hits": 0, "misses": 0}
        self._lock = threading.RLock()  # 🆕 Thread-safe access to cache operations
        self._default_ttl = CACHE_CONFIG['memory_ttl_seconds']  # 🆕 Use unified TTL config
    
    async def get(self, key: str) -> Optional[Any]:
        """Get from cache (thread-safe with RLock)."""
        with self._lock:
            if key in self._cache:
                entry = self._cache[key]
                if entry["ttl"] is None or time.time() < entry["ttl"]:
                    self._stats["hits"] += 1
                    self.logger.debug("[MemoryCache] Hit: %s", key)
                    return entry["value"]
                else:
                    # Remove expired entry
                    del self._cache[key]
                    self.logger.debug("[MemoryCache] Expired: %s", key)
            
            self._stats["misses"] += 1
            return None
    
    async def set(
        self,
        key: str,
        value: Any,
        ttl_seconds: Optional[int] = None,
    ) -> None:
        """Set in cache (thread-safe with RLock)."""
        with self._lock:
            ttl = None if ttl_seconds is None else time.time() + ttl_seconds
            self._cache[key] = {"value": value, "ttl": ttl}
            self.logger.debug("[MemoryCache] Set: %s (ttl=%s)", key, ttl_seconds or "infinite")
    
    async def delete(self, key: str) -> None:
        """Delete from cache (thread-safe with RLock)."""
        with self._lock:
            self._cache.pop(key, None)
            self.logger.debug("[MemoryCache] Deleted: %s", key)
    
    async def exists(self, key: str) -> bool:
        """Check if key exists."""
        return await self.get(key) is not None
    
    async def clear(self) -> None:
        """Clear cache (thread-safe with RLock)."""
        with self._lock:
            self._cache.clear()
            self.logger.debug("[MemoryCache] Cleared all entries")
    
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
        """Get cache statistics (thread-safe with RLock)."""
        with self._lock:
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
