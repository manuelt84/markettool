"""Multi-layer cache provider with fallback chain."""

from __future__ import annotations

import logging
import threading
from typing import Any, List, Optional
pass

from markettool.core.ports.cache_provider import CacheProvider
from markettool.core.errors import CacheError
from markettool.core.cache_config import CACHE_CONFIG


class MultiLayerCacheProvider(CacheProvider):
    """
    Cache provider with fallback chain: Memory → Local → GCS.
    Each layer is optional; at least one must be configured.
    """
    
    def __init__(
        self,
        memory_cache: Optional[Any] = None,
        local_cache: Optional[Any] = None,
        gcs_cache: Optional[Any] = None,
        logger: Optional[logging.Logger] = None,
    ):
        """
        Initialize multi-layer cache.
        
        Args:
            memory_cache: Memory cache implementation
            local_cache: Local file cache implementation
            gcs_cache: Google Cloud Storage cache implementation
            logger: Optional logger
        """
        self.memory = memory_cache
        self.local = local_cache
        self.gcs = gcs_cache
        self.logger = logger or logging.getLogger(__name__)
        self._lock = threading.RLock()  # 🆕 Thread-safe fallback operations
        
        # Verify at least one cache is configured
        if not any([memory_cache, local_cache, gcs_cache]):
            raise ValueError("At least one cache layer must be configured")
    
    async def get(self, key: str) -> Optional[Any]:
        """
        Get value with fallback chain: Memory → Local → GCS.
        """
        try:
            # Try memory cache first (fastest)
            if self.memory:
                try:
                    value = await self.memory.get(key)
                    if value is not None:
                        self.logger.debug(f"Cache HIT (memory) for {key}")
                        return value
                except Exception as e:
                    self.logger.warning(f"Memory cache error for {key}: {e}")
            
            # Try local cache (medium speed)
            if self.local:
                try:
                    value = await self.local.get(key)
                    if value is not None:
                        self.logger.debug(f"Cache HIT (local) for {key}")
                        # 🆕 Write back to memory with memory-specific TTL (no stale propagation)
                        if self.memory:
                            try:
                                # Use memory TTL, not local TTL (prevents 1h data getting 10min expiry)
                                await self.memory.set(key, value, ttl_seconds=CACHE_CONFIG['memory_ttl_seconds'])
                            except Exception:
                                pass
                        return value
                except Exception as e:
                    self.logger.warning(f"Local cache error for {key}: {e}")
            
            # Try GCS cache (slowest)
            if self.gcs:
                try:
                    value = await self.gcs.get(key)
                    if value is not None:
                        self.logger.debug(f"Cache HIT (gcs) for {key}")
                        # 🆕 Write back to faster layers with layer-specific TTLs (prevents 24h data getting 10min TTL)
                        if self.memory:
                            try:
                                await self.memory.set(key, value, ttl_seconds=CACHE_CONFIG['memory_ttl_seconds'])
                            except Exception:
                                pass
                        if self.local:
                            try:
                                await self.local.set(key, value, ttl_seconds=CACHE_CONFIG['local_ttl_seconds'])
                            except Exception:
                                pass
                        return value
                except Exception as e:
                    self.logger.warning(f"GCS cache error for {key}: {e}")
            
            self.logger.debug(f"Cache MISS for {key}")
            return None
        
        except Exception as e:
            self.logger.error(f"Multi-layer cache GET error for {key}: {e}")
            raise CacheError(f"Failed to get {key}: {e}")
    
    async def set(
        self,
        key: str,
        value: Any,
        ttl_seconds: Optional[int] = None,
    ) -> None:
        """
        Set value across all configured cache layers with per-layer TTLs.
        
        🆕 Each layer gets its appropriate TTL from CACHE_CONFIG if not specified.
        This prevents TTL corruption (e.g., 24h GCS data getting 10min memory TTL).
        """
        with self._lock:  # 🆕 Thread-safe multi-layer write
            errors = []
            
            # Write to memory with memory-specific TTL
            if self.memory:
                try:
                    memory_ttl = ttl_seconds if ttl_seconds is not None else CACHE_CONFIG['memory_ttl_seconds']
                    await self.memory.set(key, value, memory_ttl)
                except Exception as e:
                    errors.append(f"Memory: {e}")
                    self.logger.warning(f"Failed to set in memory cache: {e}")
            
            # Write to local with local-specific TTL
            if self.local:
                try:
                    local_ttl = ttl_seconds if ttl_seconds is not None else CACHE_CONFIG['local_ttl_seconds']
                    await self.local.set(key, value, local_ttl)
                except Exception as e:
                    errors.append(f"Local: {e}")
                    self.logger.warning(f"Failed to set in local cache: {e}")
            
            # Write to GCS with GCS-specific TTL
            if self.gcs:
                try:
                    gcs_ttl = ttl_seconds if ttl_seconds is not None else CACHE_CONFIG['gcs_ttl_seconds']
                    await self.gcs.set(key, value, gcs_ttl)
                except Exception as e:
                    errors.append(f"GCS: {e}")
                    self.logger.warning(f"Failed to set in GCS cache: {e}")
            
            # If all layers failed, raise error
            if errors and not any([
                hasattr(self, f) and getattr(self, f)
                for f in ["memory", "local", "gcs"]
            ]):
                raise CacheError(f"Failed to set {key} in any cache layer: {errors}")
    
    async def invalidate(self, key: str) -> None:
        """
        Invalidate key across all cache layers.
        """
        errors = []
        
        if self.memory:
            try:
                await self.memory.invalidate(key)
            except Exception as e:
                errors.append(f"Memory: {e}")
        
        if self.local:
            try:
                await self.local.invalidate(key)
            except Exception as e:
                errors.append(f"Local: {e}")
        
        if self.gcs:
            try:
                await self.gcs.invalidate(key)
            except Exception as e:
                errors.append(f"GCS: {e}")
        
        if errors:
            self.logger.warning(f"Errors invalidating {key}: {errors}")
    
    async def invalidate_pattern(self, pattern: str) -> None:
        """
        Invalidate keys matching pattern.
        """
        if self.memory and hasattr(self.memory, "invalidate_pattern"):
            try:
                await self.memory.invalidate_pattern(pattern)
            except Exception as e:
                self.logger.warning(f"Memory pattern invalidate error: {e}")
        
        if self.local and hasattr(self.local, "invalidate_pattern"):
            try:
                await self.local.invalidate_pattern(pattern)
            except Exception as e:
                self.logger.warning(f"Local pattern invalidate error: {e}")
        
        if self.gcs and hasattr(self.gcs, "invalidate_pattern"):
            try:
                await self.gcs.invalidate_pattern(pattern)
            except Exception as e:
                self.logger.warning(f"GCS pattern invalidate error: {e}")
    
    async def warm_cache(self, keys: List[str]) -> None:
        """
        Warm all cache layers with given keys.
        (Placeholder - actual warmup would load data from source)
        """
        self.logger.info(f"Warming cache with {len(keys)} keys")
        # Implementation would fetch data for keys and populate caches
        pass
    
    async def get_stats(self) -> dict:
        """Get cache statistics across all layers."""
        stats = {"layers": {}}
        
        if self.memory and hasattr(self.memory, "get_stats"):
            try:
                stats["layers"]["memory"] = await self.memory.get_stats()
            except Exception:
                stats["layers"]["memory"] = {"status": "unavailable"}
        
        if self.local and hasattr(self.local, "get_stats"):
            try:
                stats["layers"]["local"] = await self.local.get_stats()
            except Exception:
                stats["layers"]["local"] = {"status": "unavailable"}
        
        if self.gcs and hasattr(self.gcs, "get_stats"):
            try:
                stats["layers"]["gcs"] = await self.gcs.get_stats()
            except Exception:
                stats["layers"]["gcs"] = {"status": "unavailable"}
        
        return stats
    
    async def delete(self, key: str) -> None:
        """Delete key from all cache layers."""
        # Alias for invalidate
        await self.invalidate(key)
    
    async def exists(self, key: str) -> bool:
        """Check if key exists in any cache layer."""
        value = await self.get(key)
        return value is not None
    
    async def clear(self) -> None:
        """Clear all cache layers."""
        if self.memory:
            try:
                await self.memory.clear()
            except Exception as e:
                self.logger.warning(f"Failed to clear memory cache: {e}")
        
        if self.local:
            try:
                await self.local.clear()
            except Exception as e:
                self.logger.warning(f"Failed to clear local cache: {e}")
        
        if self.gcs:
            try:
                await self.gcs.clear()
            except Exception as e:
                self.logger.warning(f"Failed to clear GCS cache: {e}")
    
    async def get_historico(
        self,
        symbol: str,
        timeframe: str,
    ):
        """Get cached historical data."""
        pass
        
        key = f"historico:{symbol}:{timeframe}"
        return await self.get(key)
    
    async def set_historico(
        self,
        historico,
        ttl_seconds: Optional[int] = None,
    ) -> None:
        """Cache historical data."""
        key = f"historico:{historico.symbol}:{historico.timeframe}"
        await self.set(key, historico, ttl_seconds)
    
    async def invalidate_historico(self, symbol: str, timeframe: str) -> None:
        """Invalidate cached historical data."""
        key = f"historico:{symbol}:{timeframe}"
        await self.invalidate(key)
    
    async def get_cache_stats(self) -> dict:
        """Get cache statistics across all layers."""
        return await self.get_stats()
    
    async def warmup(self, symbols: set) -> None:
        """Pre-load cache with common symbols."""
        await self.warm_cache(list(symbols))
