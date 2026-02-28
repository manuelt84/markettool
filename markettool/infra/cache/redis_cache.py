"""
Redis-backed distributed caches for high-performance MarketTool processes.
Reduces API calls, CPU usage, and I/O by caching expensive calculations.

Caches (with intelligent TTL per timeframe):
1. Indicators (RSI, MACD, BBands, etc.) - CPU-intensive
2. OHLCV Historical Data - I/O-intensive (GCS downloads)
3. Calculated Entries - Deterministic results from analysis

All caches include Pub/Sub for cross-pod invalidation.
"""

import json
import logging
import hashlib
import time
import os
from typing import Optional, Dict, Any, Callable, Tuple, TYPE_CHECKING
from dataclasses import dataclass
from datetime import datetime, timedelta

# Type checking imports (for IDE/mypy)
if TYPE_CHECKING:
    from redis import Redis  # type: ignore
    import pandas
    DataFrame = pandas.DataFrame
else:
    DataFrame = Any  # Fallback type

# Runtime imports with fallback
try:
    import redis  # type: ignore
    import pandas as pd
    _HAS_REDIS = True
except Exception:
    redis = None  # type: ignore
    pd = None  # type: ignore
    _HAS_REDIS = False


logger = logging.getLogger(__name__)


@dataclass
class CacheStats:
    """Statistics for cache performance."""
    hits: int = 0
    misses: int = 0
    errors: int = 0
    
    @property
    def hit_rate(self) -> float:
        total = self.hits + self.misses
        return (self.hits / total * 100) if total > 0 else 0
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "hits": self.hits,
            "misses": self.misses,
            "errors": self.errors,
            "total": self.hits + self.misses,
            "hit_rate_pct": round(self.hit_rate, 2),
        }


class RedisDistributedCache:
    """Base class for Redis-backed distributed caches with TTL and Pub/Sub."""
    
    def __init__(self, redis_url: Optional[str] = None, prefix: str = "cache"):
        """
        Args:
            redis_url: Redis connection URL (e.g., "redis://localhost:6379")
            prefix: Key prefix for this cache (e.g., "indicators", "ohlcv")
        """
        self.prefix = prefix
        self.redis_url = redis_url or os.getenv("REDIS_URL")
        self.redis_client = None
        self.is_available = False
        self.stats = CacheStats()
        self.pubsub_channel = f"{prefix}:changes"
        
        if self.redis_url and _HAS_REDIS:
            try:
                self.redis_client = redis.Redis.from_url(
                    self.redis_url,
                    decode_responses=True,
                    socket_connect_timeout=2,
                    socket_keepalive=True,
                )
                # Test connection
                self.redis_client.ping()
                self.is_available = True
                logger.info(f"[RedisCache:{prefix}] ✅ Connected to Redis ({self.redis_url})")
            except Exception as e:
                logger.warning(
                    f"[RedisCache:{prefix}] ⚠️ Redis connection failed ({e}). "
                    f"Will use in-memory fallback."
                )
                self.redis_client = None
                self.is_available = False
    
    def _make_key(self, *parts: str) -> str:
        """Generate cache key from parts."""
        key_parts = [self.prefix] + list(parts)
        return ":".join(str(p) for p in key_parts)
    
    def _get_ttl_seconds(self, timeframe: str) -> int:
        """
        Calculate TTL in seconds based on timeframe.
        Longer timeframes = longer cache TTL (less volatile).
        """
        ttl_map = {
            "1min": 300,      # 5 minutes
            "5min": 600,      # 10 minutes
            "15min": 900,     # 15 minutes
            "30min": 1800,    # 30 minutes
            "1hour": 3600,    # 1 hour
            "4hour": 7200,    # 2 hours
            "1day": 14400,    # 4 hours (daily data changes once a day)
            "1week": 86400,   # 1 day
            "1month": 259200, # 3 days
        }
        return ttl_map.get(timeframe, 3600)  # Default 1h
    
    def get(self, key: str) -> Optional[str]:
        """Get value from cache. Returns None if not found."""
        if not self.is_available:
            return None
        
        try:
            value = self.redis_client.get(key)
            if value:
                self.stats.hits += 1
                return value
        except Exception as e:
            logger.warning(f"[RedisCache:{self.prefix}] Get error: {e}")
            self.stats.errors += 1
        
        self.stats.misses += 1
        return None
    
    def set(self, key: str, value: str, ttl_seconds: int = 3600) -> bool:
        """Set value in cache with TTL."""
        if not self.is_available:
            return False
        
        try:
            self.redis_client.setex(key, ttl_seconds, value)
            # Publish cache update event
            self._publish_change(key)
            return True
        except Exception as e:
            logger.warning(f"[RedisCache:{self.prefix}] Set error: {e}")
            self.stats.errors += 1
            return False
    
    def delete(self, key: str) -> bool:
        """Delete key from cache."""
        if not self.is_available:
            return False
        
        try:
            self.redis_client.delete(key)
            self._publish_change(key)
            return True
        except Exception as e:
            logger.warning(f"[RedisCache:{self.prefix}] Delete error: {e}")
            return False
    
    def _publish_change(self, key: str) -> None:
        """Publish cache change event for other pods."""
        if not self.is_available:
            return
        
        try:
            message = json.dumps({
                "key": key,
                "timestamp": datetime.utcnow().isoformat(),
            })
            self.redis_client.publish(self.pubsub_channel, message)
        except Exception as e:
            logger.debug(f"[RedisCache:{self.prefix}] Pub/Sub error: {e}")
    
    def stats_dict(self) -> Dict[str, Any]:
        """Get cache statistics."""
        return {
            "available": self.is_available,
            "prefix": self.prefix,
            **self.stats.to_dict(),
        }


class IndicatorsRedisCache(RedisDistributedCache):
    """
    Redis cache for technical indicators.
    
    Benefits:
    - Avoids repeated calculation of expensive indicators (RSI, MACD, etc.)
    - Shares cache across multiple pods
    - TTL per timeframe (shorter for volatile 1m, longer for 1d)
    
    Example:
        cache = IndicatorsRedisCache()
        key = cache.make_key(symbol="EURUSD", tf="1hour", hash_val="abc123")
        cached_indicators = cache.get(key)
        if not cached_indicators:
            # Calculate and cache
            indicators_json = calculate_and_serialize()
            cache.set(key, indicators_json, ttl_seconds=cache._get_ttl_seconds("1hour"))
    """
    
    def __init__(self, redis_url: Optional[str] = None):
        super().__init__(redis_url, prefix="indicators")
    
    def make_key(self, symbol: str, tf: str, hash_val: str) -> str:
        """Generate unique cache key for indicator set."""
        return self._make_key(symbol, tf, hash_val)
    
    def get_or_calculate(
        self,
        symbol: str,
        tf: str,
        hash_val: str,
        calc_func: Callable,
        serialize_func: Callable = json.dumps,
    ) -> Tuple[Any, Dict[str, Any]]:
        """
        Get from cache or calculate and cache result.
        
        Args:
            symbol: Trading symbol (e.g., "EURUSD")
            tf: Timeframe (e.g., "1hour")
            hash_val: Hash of input data (to detect changes)
            calc_func: Function to call if cache miss
            serialize_func: Function to serialize result to JSON
        
        Returns:
            (result, stats) where stats contains hit/miss info
        """
        key = self.make_key(symbol, tf, hash_val)
        start = time.time()
        
        # Try Redis
        cached_data = self.get(key)
        if cached_data:
            try:
                result = json.loads(cached_data)
                elapsed_ms = (time.time() - start) * 1000
                logger.debug(f"[Indicators Redis CACHE HIT] {symbol}/{tf} in {elapsed_ms:.1f}ms")
                return result, {"source": "redis", "elapsed_ms": elapsed_ms, "hit": True}
            except Exception as e:
                logger.warning(f"[Indicators] Deserialize error: {e}")
        
        # Cache miss - calculate
        try:
            logger.debug(f"[Indicators Redis CACHE MISS] {symbol}/{tf} - calculating...")
            result = calc_func()
            elapsed_ms = (time.time() - start) * 1000
            
            # Serialize and cache (async-safe)
            try:
                serialized = serialize_func(result)
                ttl = self._get_ttl_seconds(tf)
                self.set(key, serialized, ttl_seconds=ttl)
                logger.debug(f"[Indicators] Cached {symbol}/{tf} with {ttl}s TTL")
            except Exception as e:
                logger.debug(f"[Indicators] Cache store error (non-blocking): {e}")
            
            return result, {"source": "calculation", "elapsed_ms": elapsed_ms, "hit": False}
        except Exception as e:
            logger.error(f"[Indicators] Calculation error: {e}")
            raise


class OHLCVRedisCache(RedisDistributedCache):
    """
    Redis cache for OHLCV historical data.
    
    Benefits:
    - Avoids repeated GCS downloads
    - Faster lookups across pods
    - TTL per timeframe
    
    Use case:
    1. Check Redis for recent bars
    2. If not found, load from GCS
    3. Cache new/updated bars in Redis
    """
    
    def __init__(self, redis_url: Optional[str] = None):
        super().__init__(redis_url, prefix="ohlcv")
    
    def make_key(self, symbol: str, tf: str, date_key: str) -> str:
        """Generate cache key for OHLCV data.
        
        Args:
            symbol: Trading symbol
            tf: Timeframe
            date_key: Date key or range identifier (e.g., "2026-02-27" or "2026-02-27_week")
        """
        return self._make_key(symbol, tf, date_key)
    
    def get_dataframe(self, key: str) -> Optional[DataFrame]:
        """Get cached DataFrame."""
        if not pd:
            return None
        
        cached_json = self.get(key)
        if not cached_json:
            return None
        
        try:
            data_dict = json.loads(cached_json)
            df = pd.DataFrame(data_dict)
            # Reconstruct index
            if "index" in data_dict:
                df.index = pd.to_datetime(data_dict["index"])
            return df
        except Exception as e:
            logger.warning(f"[OHLCV] Deserialize error: {e}")
            return None
    
    def set_dataframe(
        self,
        symbol: str,
        tf: str,
        date_key: str,
        df: DataFrame,
    ) -> bool:
        """Cache DataFrame."""
        if not pd:
            return False
        
        key = self.make_key(symbol, tf, date_key)
        
        try:
            # Serialize with index
            data_dict = df.to_dict(orient="list")
            data_dict["index"] = df.index.strftime("%Y-%m-%d %H:%M:%S").tolist()
            serialized = json.dumps(data_dict)
            
            ttl = self._get_ttl_seconds(tf)
            return self.set(key, serialized, ttl_seconds=ttl)
        except Exception as e:
            logger.warning(f"[OHLCV] Cache store error: {e}")
            return False


class EntradasRedisCache(RedisDistributedCache):
    """
    Redis cache for calculated entries/signals.
    
    Benefits:
    - Avoids recalculation of expensive analysis
    - Deterministic results can be safely cached
    - Cross-pod sharing of analysis results
    """
    
    def __init__(self, redis_url: Optional[str] = None):
        super().__init__(redis_url, prefix="entradas")
    
    def make_key(self, symbol: str, tf: str, entry_id: str) -> str:
        """Generate cache key for entry."""
        return self._make_key(symbol, tf, entry_id)
    
    def get_entradas(self, key: str) -> Optional[Dict[str, Any]]:
        """Get cached entries dict."""
        cached_json = self.get(key)
        if not cached_json:
            return None
        
        try:
            return json.loads(cached_json)
        except Exception as e:
            logger.warning(f"[Entradas] Deserialize error: {e}")
            return None
    
    def set_entradas(
        self,
        symbol: str,
        tf: str,
        entry_id: str,
        entradas: Dict[str, Any],
        ttl_override: Optional[int] = None,
    ) -> bool:
        """Cache entries dict."""
        key = self.make_key(symbol, tf, entry_id)
        
        try:
            serialized = json.dumps(entradas)
            ttl = ttl_override or self._get_ttl_seconds(tf)
            return self.set(key, serialized, ttl_seconds=ttl)
        except Exception as e:
            logger.warning(f"[Entradas] Cache store error: {e}")
            return False


# Global singleton instances
_indicators_cache = None
_ohlcv_cache = None
_entradas_cache = None


def get_indicators_cache() -> IndicatorsRedisCache:
    """Get or create indicators cache instance."""
    global _indicators_cache
    if _indicators_cache is None:
        _indicators_cache = IndicatorsRedisCache()
    return _indicators_cache


def get_ohlcv_cache() -> OHLCVRedisCache:
    """Get or create OHLCV cache instance."""
    global _ohlcv_cache
    if _ohlcv_cache is None:
        _ohlcv_cache = OHLCVRedisCache()
    return _ohlcv_cache


def get_entradas_cache() -> EntradasRedisCache:
    """Get or create entradas cache instance."""
    global _entradas_cache
    if _entradas_cache is None:
        _entradas_cache = EntradasRedisCache()
    return _entradas_cache
