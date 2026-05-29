"""Historicos cache layer (local, GCS, Firestore metadata, lazy loader)."""

from __future__ import annotations

import json
import logging
import os
import time
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd
import pytz
from google.cloud import firestore
from google.cloud import storage
from requests.adapters import HTTPAdapter

from markettool.core.config import load_config
from markettool.infra.fmp import normalize_tf
from markettool.core.cache_config import validate_data_freshness, get_freshness_requirement_for_timeframe, CACHE_CONFIG
from markettool.infra.storage.vps_json_store import (
    PostgresDocumentStore,
    VpsJsonStore,
    vps_mode_enabled,
)

# Optional Redis support
try:
    import redis
except ImportError:
    redis = None

logger = logging.getLogger("MarketTool")
APP_CONFIG = load_config()

# ============================================================================
# Redis-based distributed cache layer (L2)
# ============================================================================

class RedisHistoricosCache:
    """
    Redis-backed distributed cache for OHLCV historical data.
    
    Benefits:
    - Shared across multiple pods (avoids per-pod cache warmup)
    - Persists across container restarts (avoids 120s penalty)
    - Automatic TTL expiration per timeframe
    - Fallback to disabled state if Redis unavailable
    
    Storage format:
    - Key: "hist:{symbol}:{tf}"
    - Value: Gzip-compressed JSON array of OHLCV records
    - TTL: Timeframe-based (1min=60s, 5min=300s, 1day=86400s)
    """
    
    def __init__(self):
        self.redis_url = os.getenv("REDIS_URL", None)
        self.redis_client = None
        self.enabled = False
        self.hits = 0
        self.misses = 0
        
        if self.redis_url and redis:
            try:
                connect_timeout = float(os.getenv("REDIS_HIST_CONNECT_TIMEOUT", "3"))
                socket_timeout = float(os.getenv("REDIS_HIST_SOCKET_TIMEOUT", "10"))
                self.redis_client = redis.Redis.from_url(
                    self.redis_url, 
                    decode_responses=False,  # We'll handle binary (gzip) data
                    socket_connect_timeout=connect_timeout,
                    socket_timeout=socket_timeout,
                    health_check_interval=30,
                )
                self.redis_client.ping()
                self.enabled = True
                logger.info("[RedisHistCache] Connected to Redis: %s", self.redis_url)
            except Exception as e:
                logger.warning("[RedisHistCache] Redis connection failed (%s). Cache disabled.", e)
                self.redis_client = None
                self.enabled = False
        else:
            reason = "no REDIS_URL" if not self.redis_url else "redis library not installed"
            logger.info("[RedisHistCache] Redis cache disabled (%s)", reason)
    
    def _make_key(self, symbol: str, tf: str) -> str:
        """Generate Redis key for historicos."""
        return f"hist:{symbol.upper()}:{normalize_tf(tf)}"
    
    def _get_ttl_seconds(self, tf: str) -> int:
        """Get TTL in seconds based on timeframe."""
        ttl_map = {
            "1min": 60,
            "5min": 300,
            "15min": 900,
            "30min": 1800,
            "1hour": 3600,
            "4hour": 14400,
            "1day": 86400,
            "1week": 604800,
            "1month": 2592000,
        }
        return ttl_map.get(normalize_tf(tf), 3600)  # Default 1h
    
    def get(self, symbol: str, tf: str) -> Optional[pd.DataFrame]:
        """Load OHLCV DataFrame from Redis if available."""
        if not self.enabled:
            return None
        
        key = self._make_key(symbol, tf)
        
        try:
            import gzip
            
            # Get compressed data from Redis
            compressed = self.redis_client.get(key)
            if not compressed:
                self.misses += 1
                return None
            
            # Decompress and parse JSON
            raw_json = gzip.decompress(compressed).decode('utf-8')
            data = json.loads(raw_json)
            
            if not data:
                self.misses += 1
                return None
            
            # Convert to DataFrame
            df = pd.DataFrame(data)
            if "time" not in df.columns:
                self.misses += 1
                return None
            
            df["time"] = pd.to_datetime(df["time"], errors="coerce", utc=True)
            df = df.dropna(subset=["time"]).set_index("time").sort_index()
            df = df[["open", "high", "low", "close", "volume"]]
            
            if df.index.tz is None:
                df.index = df.index.tz_localize(pytz.UTC)
            
            self.hits += 1
            logger.debug("[RedisHistCache] HIT: %s/%s (%d rows)", symbol, tf, len(df))
            return df
            
        except Exception as e:
            logger.debug("[RedisHistCache] GET failed for %s/%s: %s", symbol, tf, e)
            self.misses += 1
            return None
    
    def set(self, symbol: str, tf: str, df: pd.DataFrame) -> bool:
        """Save OHLCV DataFrame to Redis with TTL."""
        if not self.enabled or df is None or df.empty:
            return False
        
        key = self._make_key(symbol, tf)
        ttl = self._get_ttl_seconds(tf)
        
        try:
            import gzip
            
            # Prepare data as JSON records
            df_copy = df.copy()
            df_copy["time"] = df_copy.index.strftime("%Y-%m-%dT%H:%M:%SZ")
            data = df_copy[["time", "open", "high", "low", "close", "volume"]].to_dict(orient="records")
            
            # Compress JSON
            json_bytes = json.dumps(data).encode('utf-8')
            compressed = gzip.compress(json_bytes, compresslevel=6)
            
            # Save to Redis with TTL. Under full-universe runs Redis can briefly
            # stall on large compressed payloads, so retry once before falling
            # back to the local/GCS cache path.
            attempts = int(os.getenv("REDIS_HIST_SET_ATTEMPTS", "2"))
            for attempt in range(max(1, attempts)):
                try:
                    self.redis_client.setex(key, ttl, compressed)
                    break
                except Exception:
                    if attempt >= max(1, attempts) - 1:
                        raise
                    time.sleep(0.15 * (attempt + 1))
            
            compression_ratio = len(json_bytes) / len(compressed)
            logger.debug("[RedisHistCache] SET: %s/%s (%d rows, %.1fKB → %.1fKB, ratio=%.1fx, TTL=%ds)",
                        symbol, tf, len(df), 
                        len(json_bytes)/1024, len(compressed)/1024,
                        compression_ratio, ttl)
            return True
            
        except Exception as e:
            logger.warning("[RedisHistCache] SET failed for %s/%s: %s", symbol, tf, e)
            return False
    
    def invalidate(self, symbol: str, tf: str) -> bool:
        """Remove entry from Redis cache."""
        if not self.enabled:
            return False
        
        key = self._make_key(symbol, tf)
        
        try:
            self.redis_client.delete(key)
            logger.debug("[RedisHistCache] INVALIDATE: %s/%s", symbol, tf)
            return True
        except Exception as e:
            logger.debug("[RedisHistCache] INVALIDATE failed for %s/%s: %s", symbol, tf, e)
            return False
    
    def get_stats(self) -> dict:
        """Return cache statistics."""
        total = self.hits + self.misses
        hit_rate = (self.hits / total * 100) if total > 0 else 0.0
        return {
            "enabled": self.enabled,
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate_pct": hit_rate,
            "redis_url": self.redis_url if self.redis_url else "not configured",
        }

# Global Redis cache instance
_REDIS_HIST_CACHE = RedisHistoricosCache()

# ============================================================================
# Thread-safe infrastructure for incremental cache merging
# ============================================================================

# Per-file locks dictionary to allow concurrent updates to different symbols
# ⚠️ LIMIT TO 4096 locks max to prevent unbounded growth memory leak
_FILE_LOCKS_DICT: Dict[str, threading.RLock] = {}
_LOCKS_MUTEX = threading.RLock()  # Protects the locks dictionary itself
_MAX_LOCKS = 4096  # Max locks to prevent memory leak (32 symbols × 128 TFs or similar)
_HISTORY_MANIFEST_SCHEMA_VERSION = 1

# Maximum rows to keep in local cache (0 = unlimited)
_MAX_HISTORICO_CACHE_ROWS = int(os.environ.get("MAX_HISTORICO_CACHE_ROWS", "0"))

# Validate and cap max rows for safety
if _MAX_HISTORICO_CACHE_ROWS > 100000:
    logger.warning("[HistCache] MAX_HISTORICO_CACHE_ROWS=%d very high, capping at 100000", 
                   _MAX_HISTORICO_CACHE_ROWS)
    _MAX_HISTORICO_CACHE_ROWS = 100000

if _MAX_HISTORICO_CACHE_ROWS == 0:
    logger.info("[HistCache] Incremental cache mode: UNLIMITED rows (preserve all history)")
else:
    logger.info("[HistCache] Incremental cache mode: MAX %d rows per symbol/timeframe", 
                _MAX_HISTORICO_CACHE_ROWS)


def _get_file_lock(symbol: str, tf: str) -> threading.RLock:
    """Get or create a reentrant lock for a specific symbol/timeframe.
    
    This allows multiple threads to update different symbols concurrently,
    while preventing race conditions on the same symbol/timeframe.
    
    ✅ Memory leak protection: Limits dict to MAX_LOCKS entries
    When exceeded, removes 25% of oldest unused locks.
    
    Args:
        symbol: Trading symbol
        tf: Timeframe string
        
    Returns:
        RLock for this specific symbol/timeframe combination
    """
    key = f"{symbol}_{normalize_tf(tf)}"
    
    # Fast path: lock already exists
    if key in _FILE_LOCKS_DICT:
        return _FILE_LOCKS_DICT[key]
    
    # Slow path: create new lock (thread-safe)
    with _LOCKS_MUTEX:
        if key not in _FILE_LOCKS_DICT:
            # Check if we need to cleanup to prevent unbounded growth
            if len(_FILE_LOCKS_DICT) >= _MAX_LOCKS:
                logger.warning("[HistCache] Lock dict at capacity (%d), cleaning old entries", _MAX_LOCKS)
                # Remove 25% oldest entries (rough cleanup - not perfect LRU)
                num_to_remove = max(1, len(_FILE_LOCKS_DICT) // 4)
                keys_to_remove = list(_FILE_LOCKS_DICT.keys())[:num_to_remove]
                for k in keys_to_remove:
                    del _FILE_LOCKS_DICT[k]
                logger.debug("[HistCache] Cleaned %d locks, remaining: %d", num_to_remove, len(_FILE_LOCKS_DICT))
            
            _FILE_LOCKS_DICT[key] = threading.RLock()
        return _FILE_LOCKS_DICT[key]


def _get_ttl_for_timeframe(tf: str) -> int:
    """Get the maximum cache TTL in seconds for a given timeframe using unified CACHE_CONFIG.
    
    Delegates to cache_config.get_freshness_requirement_for_timeframe() for timeframe-based expiration.
    Falls back to CACHE_CONFIG['local_ttl_seconds'] for unknown timeframes.
    
    Args:
        tf: Timeframe string (e.g., "1min", "5min", "1hour", "1day")
        
    Returns:
        TTL in seconds
    """
    try:
        # Use unified cache_config for timeframe requirements
        ttl = get_freshness_requirement_for_timeframe(tf)
        logger.debug("[Cache] Timeframe %s mapped to freshness requirement TTL=%ds", tf, ttl)
        return ttl
    except (ValueError, KeyError) as e:
        # Unknown timeframe - use local cache default
        default_ttl = CACHE_CONFIG['local_ttl_seconds']
        logger.debug("[Cache] Unknown timeframe %s, using default local_ttl=%ds (reason: %s)", 
                     tf, default_ttl, str(e))
        return default_ttl


def safe_op(default=None, log: logging.Logger | None = None):
    log = log or logger
    def _decorator(fn):
        def _wrapped(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except Exception as exc:
                log.warning("%s failed: %s", fn.__name__, exc, exc_info=False)
                return default
        return _wrapped
    return _decorator


# Cache CSV

def _safe_symbol_for_filename(symbol: str) -> str:
    import string
    allowed = set(string.ascii_letters + string.digits + "._-")
    return "".join(ch if ch in allowed else "_" for ch in symbol)


def _hist_base(symbol: str, tf: str) -> str:
    os.makedirs(APP_CONFIG.hist_dir, exist_ok=True)
    return os.path.join(APP_CONFIG.hist_dir, f"{_safe_symbol_for_filename(symbol)}__{normalize_tf(tf)}")


def _hist_path_csv(symbol: str, tf: str) -> str:
    return _hist_base(symbol, tf) + ".csv"


def _hist_path_json(symbol: str, tf: str) -> str:
    return _hist_base(symbol, tf) + ".json"


def _hist_path_manifest(symbol: str, tf: str) -> str:
    return _hist_base(symbol, tf) + ".manifest.json"


def _hist_path(symbol: str, tf: str) -> str:
    if hasattr(APP_CONFIG, "storage_format") and APP_CONFIG.storage_format == "json":
        return _hist_path_json(symbol, tf)
    return _hist_path_csv(symbol, tf)


def _timeframe_step_ms(tf: str) -> int | None:
    """Expected candle spacing for quality manifests."""
    tf_norm = normalize_tf(tf)
    step_seconds = {
        "1min": 60,
        "5min": 300,
        "15min": 900,
        "30min": 1800,
        "1hour": 3600,
        "4hour": 14400,
        "1day": 86400,
        "1week": 604800,
        "1month": 2592000,
    }.get(tf_norm)
    return step_seconds * 1000 if step_seconds else None


def _history_manifest_from_df(symbol: str, tf: str, df: pd.DataFrame, *, source: str = "local_incremental") -> dict[str, Any]:
    """Build a compact quality manifest for persisted historical candles."""
    tf_norm = normalize_tf(tf)
    manifest: dict[str, Any] = {
        "schema_version": _HISTORY_MANIFEST_SCHEMA_VERSION,
        "symbol": str(symbol).upper(),
        "tf": tf_norm,
        "rows": 0,
        "first_ts": None,
        "last_ts": None,
        "expected_step_ms": _timeframe_step_ms(tf_norm),
        "gap_count": 0,
        "max_gap_ms": 0,
        "coverage_ratio": 0.0,
        "source": source,
        "node_id": os.getenv("MARKET_DATA_NODE_ID") or os.getenv("WORKER_ID") or os.getenv("HOSTNAME"),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    if df is None or getattr(df, "empty", True):
        return manifest

    try:
        idx = pd.DatetimeIndex(pd.to_datetime(df.index, errors="coerce", utc=True)).dropna().sort_values()
        if len(idx) == 0:
            return manifest
        idx = idx.drop_duplicates()
        rows = len(idx)
        first = idx[0]
        last = idx[-1]
        step_ms = manifest["expected_step_ms"]
        gap_count = 0
        max_gap_ms = 0
        coverage_ratio = 1.0
        if rows > 1 and step_ms:
            diffs_ms = pd.Series(idx).diff().dropna().dt.total_seconds().mul(1000)
            gap_threshold = step_ms * 1.5
            gaps = diffs_ms[diffs_ms > gap_threshold]
            gap_count = int(len(gaps))
            max_gap_ms = int(diffs_ms.max()) if not diffs_ms.empty else 0
            expected_rows = int(((last - first).total_seconds() * 1000) // step_ms) + 1
            if expected_rows > 0:
                coverage_ratio = max(0.0, min(1.0, rows / expected_rows))
        manifest.update({
            "rows": int(rows),
            "first_ts": first.isoformat(),
            "last_ts": last.isoformat(),
            "gap_count": gap_count,
            "max_gap_ms": max_gap_ms,
            "coverage_ratio": round(float(coverage_ratio), 6),
        })
    except Exception as exc:
        logger.debug("[HistCache] Manifest calculation failed for %s/%s: %s", symbol, tf, exc)
    return manifest


def _publish_history_manifest(symbol: str, tf: str, manifest: dict[str, Any]) -> None:
    """Publish only metadata to shared Redis so other machines know cache quality.

    The historical payload can be large, so cross-machine sync uses files/GCS and
    Redis carries a lightweight index instead of duplicating heavy candles there.
    """
    if not redis:
        return
    redis_url = os.getenv("MARKET_DATA_REDIS_URL") or os.getenv("LIVE_ENTRIES_REDIS_URL")
    if not redis_url:
        return
    try:
        client = redis.Redis.from_url(
            redis_url,
            decode_responses=True,
            socket_connect_timeout=float(os.getenv("MARKET_DATA_REDIS_CONNECT_TIMEOUT", "1.5")),
            socket_timeout=float(os.getenv("MARKET_DATA_REDIS_SOCKET_TIMEOUT", "2.0")),
        )
        symbol_key = str(symbol).upper()
        tf_norm = normalize_tf(tf)
        node_id = manifest.get("node_id") or "unknown"
        payload = json.dumps(manifest, ensure_ascii=False, default=str)
        ttl_seconds = int(os.getenv("MARKET_DATA_MANIFEST_TTL_SECONDS", "2592000"))
        client.setex(f"hist_manifest:{symbol_key}:{tf_norm}:{node_id}", ttl_seconds, payload)
        latest_key = f"hist_manifest_latest:{symbol_key}:{tf_norm}"
        previous_raw = client.get(latest_key)
        previous = json.loads(previous_raw) if previous_raw else None
        def _score(item: dict[str, Any] | None) -> tuple:
            if not item:
                return (0, 0.0, 0, 0, "")
            return (
                int(item.get("rows") or 0),
                float(item.get("coverage_ratio") or 0.0),
                -int(item.get("gap_count") or 0),
                int(pd.Timestamp(item.get("last_ts")).timestamp()) if item.get("last_ts") else 0,
                str(item.get("updated_at") or ""),
            )
        if _score(manifest) >= _score(previous):
            client.setex(latest_key, ttl_seconds, payload)
    except Exception as exc:
        logger.debug("[HistCache] Shared manifest publish failed for %s/%s: %s", symbol, tf, exc)


def _write_history_manifest(symbol: str, tf: str, df: pd.DataFrame, *, source: str = "local_incremental") -> None:
    manifest = _history_manifest_from_df(symbol, tf, df, source=source)
    manifest_path = _hist_path_manifest(symbol, tf)
    tmp_path = manifest_path + ".tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2, sort_keys=True)
        os.replace(tmp_path, manifest_path)
    finally:
        try:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
        except Exception:
            pass
    _publish_history_manifest(symbol, tf, manifest)


def _merge_local_data(existing_df: Optional[pd.DataFrame], new_df: pd.DataFrame) -> pd.DataFrame:
    """Merge existing cached data with new data using incremental strategy.
    
    Similar to merge_histories() in historicos_service.py:
    - Concatenates DataFrames
    - Deduplicates with keep='last' (newer data wins)
    - Sorts by index
    
    Args:
        existing_df: Previously cached DataFrame (or None)
        new_df: New data to merge
        
    Returns:
        Merged DataFrame with all historical data
    """
    try:
        # No existing data - just use new
        if existing_df is None or existing_df.empty:
            return new_df.copy()
        
        # No new data - return existing
        if new_df.empty:
            return existing_df.copy()
        
        # Both exist - merge incrementally
        merged = pd.concat([existing_df, new_df], axis=0, ignore_index=False)
        merged = merged[~merged.index.isna()].sort_index()
        merged = merged[~merged.index.duplicated(keep="last")]  # Newer data wins
        
        rows_added = len(merged) - len(existing_df)
        logger.debug("[HistCache] Merged: %d existing + %d new = %d total (net +%d rows)",
                    len(existing_df), len(new_df), len(merged), rows_added)
        
        return merged
        
    except Exception as exc:
        logger.warning("[HistCache] Merge failed (%s), using new data only: %s", 
                      type(exc).__name__, exc)
        return new_df.copy()


def _save_local_history_df(symbol: str, tf: str, df: pd.DataFrame) -> None:
    """Incremental local save for historicos with thread-safe merge.
    
    NEW BEHAVIOR:
    - Loads existing cached data (if any)
    - Merges with new data using pd.concat + deduplication
    - Preserves all historical data (unless MAX_HISTORICO_CACHE_ROWS limit set)
    - Thread-safe via per-file locks
    - Atomic write using temp file + os.replace
    
    Args:
        symbol: Trading symbol
        tf: Timeframe string
        df: New DataFrame to save/merge
    """
    if df is None or getattr(df, "empty", True):
        return
    
    # Get per-file lock for thread safety
    file_lock = _get_file_lock(symbol, tf)
    
    with file_lock:
        try:
            # 1. Prepare new data
            out = df.copy()
            idx_utc = pd.DatetimeIndex(pd.to_datetime(out.index, utc=True, errors="coerce"))
            mask = ~idx_utc.isna()
            if not mask.all():
                out = out.loc[mask].copy()
                idx_utc = idx_utc[mask]

            out["time"] = idx_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
            for c in ("open", "high", "low", "close", "volume"):
                if c not in out.columns:
                    out[c] = np.nan
            
            new_data_df = out[["time", "open", "high", "low", "close", "volume"]].copy()
            new_rows = len(new_data_df)
            
            # 2. Load existing cache (if any)
            existing_df = None
            local_hist = _hist_path_json(symbol, tf)
            
            if os.path.exists(local_hist):
                try:
                    raw = Path(local_hist).read_text(encoding="utf-8")
                    existing_data = json.loads(raw) if raw.strip() else []
                    if existing_data:
                        existing_df = pd.DataFrame(existing_data)
                        logger.debug("[HistCache] Loaded existing cache: %d rows for %s/%s", 
                                   len(existing_df), symbol, tf)
                except (json.JSONDecodeError, Exception) as load_exc:
                    logger.warning("[HistCache] Corrupt cache file %s, will recreate: %s", 
                                 local_hist, load_exc)
                    existing_df = None
            
            # 3. Merge existing + new data
            if existing_df is not None and not existing_df.empty:
                # Convert existing to same format for merge
                if "time" in existing_df.columns:
                    existing_df["time"] = pd.to_datetime(existing_df["time"], errors="coerce", utc=True)
                    existing_df = existing_df.dropna(subset=["time"]).set_index("time").sort_index()
                    existing_df = existing_df[["open", "high", "low", "close", "volume"]]
                    
                    # Now merge
                    new_data_df_indexed = new_data_df.copy()
                    new_data_df_indexed["time"] = pd.to_datetime(new_data_df_indexed["time"], utc=True)
                    new_data_df_indexed = new_data_df_indexed.set_index("time").sort_index()
                    new_data_df_indexed = new_data_df_indexed[["open", "high", "low", "close", "volume"]]
                    
                    merged = _merge_local_data(existing_df, new_data_df_indexed)
                    
                    # Convert back to records format
                    merged_with_time = merged.copy()
                    merged_with_time["time"] = merged.index.strftime("%Y-%m-%dT%H:%M:%SZ")
                    new_data_df = merged_with_time[["time", "open", "high", "low", "close", "volume"]]
            
            # 4. Apply row limit (if configured)
            total_rows = len(new_data_df)
            if _MAX_HISTORICO_CACHE_ROWS > 0 and total_rows > _MAX_HISTORICO_CACHE_ROWS:
                new_data_df = new_data_df.tail(_MAX_HISTORICO_CACHE_ROWS)
                logger.info("[HistCache] Truncated %s/%s from %d to %d rows (limit=%d)",
                           symbol, tf, total_rows, len(new_data_df), _MAX_HISTORICO_CACHE_ROWS)
                total_rows = len(new_data_df)
            
            # 5. Atomic write (temp file + rename)
            payload = new_data_df.to_dict(orient="records")
            os.makedirs(APP_CONFIG.hist_dir, exist_ok=True)
            
            temp_file = local_hist + ".tmp"
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False)
            
            # Atomic replacement
            os.replace(temp_file, local_hist)
            try:
                manifest_df = new_data_df.copy()
                manifest_df["time"] = pd.to_datetime(manifest_df["time"], errors="coerce", utc=True)
                manifest_df = manifest_df.dropna(subset=["time"]).set_index("time").sort_index()
                _write_history_manifest(symbol, tf, manifest_df, source="local_incremental")
            except Exception as manifest_exc:
                logger.debug("[HistCache] Manifest write skipped for %s/%s: %s", symbol, tf, manifest_exc)
            
            # Calculate net new rows from merge
            net_new_rows = total_rows - (len(existing_df) if existing_df is not None else 0)
            logger.info("[HistCache] Saved %s/%s: %d rows (new=%d, merged_delta=+%d, total=%d)",
                       symbol, tf, total_rows, new_rows, max(0, net_new_rows), total_rows)
            
        except Exception as exc:
            logger.warning("[HistCache] Incremental save failed %s/%s: %s", symbol, tf, exc)
            
            # Fallback: try simple save without merge
            try:
                simple_payload = out[["time", "open", "high", "low", "close", "volume"]].tail(1000).to_dict(orient="records")
                os.makedirs(APP_CONFIG.hist_dir, exist_ok=True)
                with open(local_hist, "w", encoding="utf-8") as f:
                    json.dump(simple_payload, f, ensure_ascii=False)
                try:
                    _write_history_manifest(symbol, tf, out.set_index(pd.DatetimeIndex(pd.to_datetime(out["time"], utc=True))), source="fallback_simple")
                except Exception:
                    pass
                logger.debug("[HistCache] Fallback save succeeded: %d rows", len(simple_payload))
            except Exception as fallback_exc:
                logger.debug("[HistCache] Fallback save also failed: %s", fallback_exc)


def _load_local(symbol: str, tf: str) -> Optional[pd.DataFrame]:
    """Best-effort local load for historicos using the JSON cache.
    
    Validates:
    1. File TTL expiration (via os.path.getmtime)
    2. Data freshness requirements (via validate_data_freshness)
    
    Enhanced for incremental cache:
    - Handles larger files (10K-100K+ rows)
    - Better error handling for corrupt JSON
    - Logs file size for performance monitoring
    """
    try:
        local_hist = _hist_path_json(symbol, tf)
        if not os.path.exists(local_hist):
            return None

        # Check file TTL expiration first
        ttl_seconds = int(os.environ.get("HIST_LOCAL_TTL_SECONDS", APP_CONFIG.cache_ttl_historicos))
        if ttl_seconds > 0:
            age_seconds = time.time() - os.path.getmtime(local_hist)
            if age_seconds > ttl_seconds:
                logger.debug("[hist_local] Cache expired: %s/%s (age=%ds > ttl=%ds)", 
                             symbol, tf, int(age_seconds), ttl_seconds)
                return None

        # Load DataFrame from JSON with size monitoring
        file_size_mb = os.path.getsize(local_hist) / (1024 * 1024)
        if file_size_mb > 5:
            logger.debug("[HistCache] Loading large cache file: %.2f MB for %s/%s",
                        file_size_mb, symbol, tf)
        
        raw = Path(local_hist).read_text(encoding="utf-8")
        
        try:
            data = json.loads(raw) if raw.strip() else []
        except json.JSONDecodeError as json_err:
            logger.warning("[HistCache] Corrupt JSON in %s: %s (will trigger refresh)",
                          local_hist, json_err)
            return None
        
        if isinstance(data, dict):
            data = data.get("data", data.get("payload", []))

        df = pd.DataFrame(data)
        if df.empty:
            return df

        if "time" not in df.columns and "date" not in df.columns:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

        time_col = "time" if "time" in df.columns else "date"
        df[time_col] = pd.to_datetime(df[time_col], errors="coerce", utc=True)
        df = df.dropna(subset=[time_col]).set_index(time_col).sort_index()
        df = _ensure_cols(df)
        if df.index.tz is None:
            df.index = df.index.tz_localize(pytz.UTC)
        
        # 🆕 Validate data freshness only for daily+ (weekly/monthly)
        # For intraday (1min-4hour): Skip validate_data_freshness since it checks candle age (not file age)
        # and causes false rejection when last candle = 1h old but file = 5min old
        intraday_tfs = {"1min", "5min", "15min", "30min", "1hour", "4hour"}
        if tf not in intraday_tfs:
            is_fresh, age_seconds, reason = validate_data_freshness(df, symbol, tf)
            if not is_fresh:
                logger.debug("[hist_local] Data too stale for %s/%s: %s (age=%ds)", 
                            symbol, tf, reason, age_seconds)
                return None
            logger.debug("[hist_local] Loaded fresh %s/%s: %d rows, age=%ds, size=%.2fMB", 
                        symbol, tf, len(df), age_seconds, file_size_mb)
        else:
            logger.debug("[hist_local] Loaded cached %s/%s: %d rows, size=%.2fMB (intraday - TTL check sufficient)", 
                        symbol, tf, len(df), file_size_mb)
        
        return df
    except Exception as exc:
        logger.debug("[hist_local] Load failed %s/%s: %s", symbol, tf, exc)
        return None


def _ensure_cols(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for c in ["open", "high", "low", "close", "volume"]:
        if c in out.columns:
            out[c] = pd.to_numeric(out[c], errors="coerce")
        else:
            out[c] = np.nan
    return out[["open", "high", "low", "close", "volume"]]


def _normalize_history_df(df: pd.DataFrame) -> pd.DataFrame:
    """Return OHLCV with a UTC DatetimeIndex, or an empty frame if unusable."""
    if df is None or df.empty:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

    out = df.copy()
    if "time" in out.columns or "date" in out.columns:
        time_col = "time" if "time" in out.columns else "date"
        out[time_col] = pd.to_datetime(out[time_col], errors="coerce", utc=True)
        out = out.dropna(subset=[time_col]).set_index(time_col)
    else:
        idx = pd.to_datetime(out.index, errors="coerce", utc=True)
        if idx.isna().all():
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        out = out.loc[~idx.isna()].copy()
        out.index = idx[~idx.isna()]

    out = _ensure_cols(out).sort_index()
    out = out[~out.index.duplicated(keep="last")]
    if out.index.tz is None:
        out.index = out.index.tz_localize(pytz.UTC)
    return out


@safe_op(default=pd.DataFrame(columns=["open", "high", "low", "close", "volume"]))
def load_cached_history(symbol: str, tf: str) -> pd.DataFrame:
    """
    Carga historicos del cache.
    Orden:
      1) LazyHistoricosLoader (L1 - in-memory, fastest)
      2) Redis (L2 - shared across pods, persists across restarts)
      3) Local JSON cache (L2 fallback - pod-specific)
      4) Firestore metadata (TTL compartido)
      5) GCS
      6) Legacy local files
    """
    # Opcion 1: Lazy loader (L1 - in-memory)
    try:
        df = _LAZY_HIST_LOADER.get(symbol, tf)
        if not df.empty:
            df = _normalize_history_df(df)
        if not df.empty:
            logger.debug("[load_cached] Hit LazyLoader: %s/%s", symbol, tf)
            return df
    except Exception as exc:
        logger.debug("[LazyLoader] Failed to load %s/%s: %s", symbol, tf, exc)

    # ✅ NEW Opcion 2: Redis (L2 - shared distributed cache)
    try:
        df = _REDIS_HIST_CACHE.get(symbol, tf)
        if df is not None and not df.empty:
            df = _normalize_history_df(df)
        if df is not None and not df.empty:
            logger.debug("[load_cached] Hit Redis: %s/%s (%d rows)", symbol, tf, len(df))
            # Populate LazyLoader for future in-memory hits
            try:
                _LAZY_HIST_LOADER.put(symbol, tf, df)
            except Exception:
                pass
            # Also save to local cache as backup
            try:
                _save_local_history_df(symbol, tf, df)
            except Exception:
                pass
            return df
    except Exception as exc:
        logger.debug("[load_cached] Redis check failed: %s", exc)

    # Opcion 3: Local files (L2 fallback)
    try:
        df = _load_local(symbol, tf)
        if df is not None and not df.empty:
            df = _normalize_history_df(df)
        if df is not None and not df.empty:
            logger.debug("[load_cached] Hit Local: %s/%s", symbol, tf)
            try:
                _LAZY_HIST_LOADER.put(symbol, tf, df)
            except Exception:
                pass
            # ✅ Backfill Redis if it was a miss
            try:
                _REDIS_HIST_CACHE.set(symbol, tf, df)
            except Exception:
                pass
            return df
    except Exception as exc:
        logger.debug("[load_cached] Local check failed: %s", exc)

    # Opcion 4: Firestore metadata
    try:
        metadata = get_historicos_metadata(symbol, tf)
        if metadata is not None and not is_metadata_stale(metadata, tf):
            logger.debug("[load_cached] Firestore metadata valid: %s/%s", symbol, tf)
            try:
                df = load_from_gcs(symbol, tf)
                if df is not None and not df.empty:
                    df = _normalize_history_df(df)
                if df is not None and not df.empty:
                    logger.debug("[load_cached] Hit GCS (via Firestore TTL): %s/%s", symbol, tf)
                    _save_local_history_df(symbol, tf, df)
                    try:
                        _LAZY_HIST_LOADER.put(symbol, tf, df)
                    except Exception:
                        pass
                    return df
            except Exception as gcs_err:
                logger.debug("[GCS] Load failed even though Firestore valid: %s", gcs_err)
    except Exception as exc:
        logger.debug("[Firestore] Metadata check failed (not fatal): %s", exc)

    # Opcion 4: GCS fallback
    try:
        df = load_from_gcs(symbol, tf)
        if df is not None and not df.empty:
            df = _normalize_history_df(df)
        if df is not None and not df.empty:
            logger.debug("[load_cached] Hit GCS: %s/%s", symbol, tf)
            _save_local_history_df(symbol, tf, df)
            try:
                _LAZY_HIST_LOADER.put(symbol, tf, df)
            except Exception:
                pass
            return df
    except Exception as exc:
        logger.debug("[GCS] Load failed for %s/%s, trying local: %s", symbol, tf, exc)

    # Opcion 5: legacy local CSV/JSON
    primary = _hist_path(symbol, tf)
    alt = _hist_path_json(symbol, tf) if primary.endswith(".csv") else _hist_path_csv(symbol, tf)

    def _from_df(df: pd.DataFrame) -> pd.DataFrame:
        df = _normalize_history_df(df)
        if df.empty:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        return df

    if os.path.exists(primary):
        try:
            if primary.endswith(".csv"):
                df = pd.read_csv(primary, low_memory=False)
            else:
                raw = Path(primary).read_text(encoding="utf-8")
                data = json.loads(raw) if raw.strip() else []
                if isinstance(data, dict):
                    data = data.get("data", [])
                df = pd.DataFrame(data)
            return _from_df(df)
        except Exception as exc:
            logger.debug("[load_cached] Error loading primary %s/%s: %s", symbol, tf, exc)

    if os.path.exists(alt):
        try:
            if alt.endswith(".csv"):
                df = pd.read_csv(alt, low_memory=False)
            else:
                raw = Path(alt).read_text(encoding="utf-8")
                data = json.loads(raw) if raw.strip() else []
                if isinstance(data, dict):
                    data = data.get("data", [])
                df = pd.DataFrame(data)
            return _from_df(df)
        except Exception as exc:
            logger.debug("[load_cached] Error loading alt %s/%s: %s", symbol, tf, exc)

    return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])


@safe_op(default=None)
def save_cached_history(symbol: str, tf: str, out: pd.DataFrame, *, storage_dir: str | None = None) -> None:
    """
    Persiste un recorte de historia ya combinada en local (GCS diferido).
    """
    try:
        if out is None or out.empty:
            return

        idx_utc = pd.DatetimeIndex(pd.to_datetime(out.index, utc=True, errors="coerce"))
        mask = ~idx_utc.isna()
        if not mask.all():
            out = out.loc[mask].copy()
            idx_utc = idx_utc[mask]

        if normalize_tf(tf) == "1day" and len(out) > 5:
            diffs = pd.Series(idx_utc[-20:]).diff().dropna()
            if not diffs.empty:
                med = diffs.median()
                if med < pd.Timedelta(hours=4):
                    logger.warning(
                        "[save_cached_history] CORRUPT DATA DETECTED: %s/1day contains intraday data (median diff %s). Resampling to 1D...",
                        symbol,
                        med,
                    )
                    for c in ("open", "high", "low", "close", "volume"):
                        if c in out.columns:
                            out[c] = pd.to_numeric(out[c], errors="coerce")
                    out = out.resample("1D").agg({
                        "open": "first",
                        "high": "max",
                        "low": "min",
                        "close": "last",
                        "volume": "sum",
                    }).dropna(subset=["close"])
                    idx_utc = pd.DatetimeIndex(pd.to_datetime(out.index, utc=True))

        out = out.copy()
        out["time"] = idx_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
        for c in ("open", "high", "low", "close", "volume"):
            if c not in out.columns:
                out[c] = pd.NA

        out = out[["time", "open", "high", "low", "close", "volume"]]

        import re, tempfile
        safe_sym = re.sub(r"[^A-Za-z0-9_]+", "_", str(symbol))
        safe_tf = re.sub(r"[^A-Za-z0-9_]+", "_", str(tf))
        nombre = f"{safe_sym}_{safe_tf}_enriched.json"
        local_json = os.path.join(tempfile.gettempdir(), nombre)

        payload = out.tail(1000).to_dict(orient="records")

        try:
            # ✅ Save to local JSON cache (incremental merge)
            _save_local_history_df(symbol, tf, out)
            
            # ✅ Save to Redis distributed cache (shared across pods)
            try:
                _REDIS_HIST_CACHE.set(symbol, tf, out)
            except Exception as redis_err:
                logger.debug("[save_cached_history] Redis save failed for %s/%s: %s", 
                            symbol, tf, redis_err)
            
            # Legacy temp file save
            with open(local_json, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, default=str)
            logger.debug("[save_cached_history] Local saved: %s rows=%d (GCS deferred to warmup)", symbol, len(out))
        except Exception as local_err:
            logger.warning("[save_cached_history] Even local save failed for %s/%s: %s", symbol, tf, local_err)

    except Exception as exc:
        logger.warning("save_cached_history failed: %s", exc)
        return


# ======================================================================
# Lazy loader for historicos
# ======================================================================

class LazyHistoricosLoader:
    """
    Carga historicos bajo demanda con cache LRU y TTL.
    """

    def __init__(self, hist_dir: str, maxsize: int = 100, ttl_seconds: int = 1800):
        self.hist_dir = hist_dir
        self.maxsize = maxsize
        self.ttl_seconds = ttl_seconds
        self._cache: dict[str, pd.DataFrame] = {}
        self._cache_times: dict[str, float] = {}
        self._lock = threading.Lock()

    def get(self, symbol: str, temporalidad: str = "1day", cfg: dict | None = None) -> pd.DataFrame:
        cache_key = f"{symbol.upper()}__{normalize_tf(temporalidad)}"
        with self._lock:
            now = time.time()
            if cache_key in self._cache:
                cached_time = self._cache_times.get(cache_key, 0)
                if (now - cached_time) < self.ttl_seconds:
                    return self._cache[cache_key].copy()
                del self._cache[cache_key]
                del self._cache_times[cache_key]

            df = self._load_from_disk(symbol, temporalidad)
            if len(self._cache) >= self.maxsize:
                oldest_key = min(self._cache_times, key=self._cache_times.get)
                del self._cache[oldest_key]
                del self._cache_times[oldest_key]
                logger.debug("[LazyLoader] Evicted %s from cache", oldest_key)

            self._cache[cache_key] = df.copy()
            self._cache_times[cache_key] = now
            return df

    def _load_from_disk(self, symbol: str, temporalidad: str) -> pd.DataFrame:
        try:
            safe_sym = _safe_symbol_for_filename(symbol).upper()
            safe_tf = normalize_tf(temporalidad)
            candidates = [
                os.path.join(self.hist_dir, f"{safe_sym}__{safe_tf}.json"),
                os.path.join(self.hist_dir, f"{safe_sym}_{safe_tf}.json"),
                os.path.join(self.hist_dir, f"{symbol.upper()}.json"),
            ]

            filepath = None
            for cand in candidates:
                if os.path.exists(cand):
                    filepath = cand
                    break

            if not filepath:
                logger.warning("[LazyLoader] File not found: %s (%s/%s)", self.hist_dir, symbol, safe_tf)
                return pd.DataFrame()

            with open(filepath, "r", encoding="utf-8") as f:
                content = f.read().strip()

            if not content:
                return pd.DataFrame()

            try:
                data = json.loads(content)
                if isinstance(data, dict) and "data" in data:
                    data = data["data"]
                elif isinstance(data, dict):
                    data = [data]
                if not isinstance(data, list):
                    data = [data]
                return _normalize_history_df(pd.DataFrame(data))
            except json.JSONDecodeError:
                try:
                    lines = content.split("\n")
                    data = [json.loads(line) for line in lines if line.strip()]
                    return _normalize_history_df(pd.DataFrame(data))
                except json.JSONDecodeError:
                    logger.error("[LazyLoader] Invalid JSON in %s", filepath)
                    return pd.DataFrame()

        except Exception as exc:
            logger.error("[LazyLoader] Error loading %s: %s", symbol, exc)
            return pd.DataFrame()

    def put(self, symbol: str, temporalidad: str, df: pd.DataFrame) -> None:
        safe_tf = normalize_tf(temporalidad)
        cache_key = f"{symbol.upper()}__{safe_tf}"
        with self._lock:
            if len(self._cache) >= self.maxsize:
                oldest_key = min(self._cache_times, key=self._cache_times.get)
                del self._cache[oldest_key]
                del self._cache_times[oldest_key]
                logger.debug("[LazyLoader] Evicted %s from cache", oldest_key)

            self._cache[cache_key] = df.copy()
            self._cache_times[cache_key] = time.time()
            logger.debug("[LazyLoader] Cached %s/%s (%d rows)", symbol, safe_tf, len(df))

    def clear_cache(self) -> None:
        with self._lock:
            self._cache.clear()
            self._cache_times.clear()
            logger.info("[LazyLoader] Cache cleared")


_LAZY_HIST_LOADER = LazyHistoricosLoader(
    hist_dir=APP_CONFIG.hist_dir,
    maxsize=APP_CONFIG.cache_max_size_historicos,
    ttl_seconds=APP_CONFIG.cache_ttl_historicos,
)


# ======================================================================
# GCS storage layer
# ======================================================================

_GCS_CLIENT = None
_GCS_BUCKET_NAME = os.environ.get("GCS_BUCKET_NAME", "markettool_bucket")
_GCS_ENABLED = os.environ.get("GCS_ENABLED", "true").lower() == "true"
_GCS_POOL_CONNECTIONS = int(os.environ.get("GCS_POOL_CONNECTIONS", "64"))
_GCS_POOL_MAXSIZE = int(os.environ.get("GCS_POOL_MAXSIZE", "64"))


def _tune_gcs_client(client: storage.Client) -> storage.Client:
    try:
        adapter = HTTPAdapter(
            pool_connections=_GCS_POOL_CONNECTIONS,
            pool_maxsize=_GCS_POOL_MAXSIZE,
            pool_block=False,
        )
        http = getattr(client, "_http", None)
        if http is not None and hasattr(http, "mount"):
            http.mount("https://", adapter)
            http.mount("http://", adapter)
    except Exception as exc:
        logger.debug("[GCS] Could not tune HTTP pool: %s", exc)
    return client


def _get_gcs_bucket():
    global _GCS_CLIENT
    if vps_mode_enabled():
        return VpsJsonStore.from_env()
    if not _GCS_ENABLED:
        return None
    try:
        if _GCS_CLIENT is None:
            _GCS_CLIENT = _tune_gcs_client(storage.Client())
        return _GCS_CLIENT.bucket(_GCS_BUCKET_NAME)
    except Exception as exc:
        logger.warning("[GCS] Client initialization failed: %s. GCS disabled.", exc)
        return None


def load_from_gcs(symbol: str, tf: str) -> Optional[pd.DataFrame]:
    try:
        bucket = _get_gcs_bucket()
        if bucket is None:
            return None

        safe_sym = _safe_symbol_for_filename(symbol)
        safe_tf = normalize_tf(tf)
        gcs_path = f"historicos/{safe_sym}__{safe_tf}.json"

        blob = bucket.blob(gcs_path)
        if not blob.exists():
            return None

        json_data = blob.download_as_text(encoding="utf-8")
        data = json.loads(json_data)

        if isinstance(data, dict) and "data" in data:
            data = data["data"]

        df = pd.DataFrame(data)

        if "time" in df.columns:
            df["time"] = pd.to_datetime(df["time"], utc=True, errors="coerce")
            df = df.set_index("time").sort_index()
        elif "date" in df.columns:
            df["date"] = pd.to_datetime(df["date"], utc=True, errors="coerce")
            df = df.set_index("date").sort_index()

        if df.index.tz is None and hasattr(df.index, "tz_localize"):
            df.index = df.index.tz_localize(pytz.UTC)

        df = _ensure_cols(df)

        logger.debug("[GCS] Loaded %s/%s from gs://%s/%s (%d rows)", symbol, tf, _GCS_BUCKET_NAME, gcs_path, len(df))
        return df

    except Exception as exc:
        logger.debug("[GCS] Failed to load %s/%s: %s", symbol, tf, exc)
        return None


def save_to_gcs(symbol: str, tf: str, df: pd.DataFrame) -> bool:
    try:
        if df is None or df.empty:
            return False

        bucket = _get_gcs_bucket()
        if bucket is None:
            return False

        safe_sym = _safe_symbol_for_filename(symbol)
        safe_tf = normalize_tf(tf)
        gcs_path = f"historicos/{safe_sym}__{safe_tf}.json"

        out = df.copy()
        if hasattr(out.index, "tz_localize") and out.index.tz is None:
            out.index = out.index.tz_localize(pytz.UTC)
        elif hasattr(out.index, "tz_convert"):
            out.index = out.index.tz_convert(pytz.UTC)

        idx_utc = pd.DatetimeIndex(pd.to_datetime(out.index, utc=True, errors="coerce"))
        out["time"] = idx_utc.strftime("%Y-%m-%dT%H:%M:%SZ")

        for c in ("open", "high", "low", "close", "volume"):
            if c not in out.columns:
                out[c] = np.nan

        payload_df = out[["time", "open", "high", "low", "close", "volume"]]
        max_rows = int(os.getenv("GCS_HISTORY_MAX_ROWS", "1000"))
        if max_rows > 0 and len(payload_df) > max_rows:
            payload_df = payload_df.tail(max_rows)
        payload = payload_df.to_dict(orient="records")

        blob = bucket.blob(gcs_path)
        blob.upload_from_string(
            json.dumps(payload, ensure_ascii=False),
            content_type="application/json",
        )

        logger.debug("[GCS] Saved %s/%s to gs://%s/%s (%d rows)", symbol, tf, _GCS_BUCKET_NAME, gcs_path, len(payload))
        return True

    except Exception as exc:
        logger.warning("[GCS] Failed to save %s/%s: %s", symbol, tf, exc)
        return False


# ======================================================================
# Firestore metadata layer
# ======================================================================

_FIRESTORE_CLIENT = None
_FIRESTORE_ENABLED = os.environ.get("FIRESTORE_ENABLED", "true").lower() == "true"


def _get_firestore_client() -> Optional[firestore.Client]:
    global _FIRESTORE_CLIENT
    if vps_mode_enabled():
        return PostgresDocumentStore.from_env()
    if not _FIRESTORE_ENABLED:
        return None

    try:
        if _FIRESTORE_CLIENT is None:
            if hasattr(firestore, "Client"):
                _FIRESTORE_CLIENT = firestore.Client()
            elif hasattr(firestore, "client"):
                _FIRESTORE_CLIENT = firestore.client()
            else:
                raise AttributeError("No Firestore client constructor found")
        return _FIRESTORE_CLIENT
    except Exception as exc:
        logger.warning("[Firestore] Metadata client initialization failed: %s. Operating without shared TTL.", exc)
        return None


def get_historicos_metadata(symbol: str, tf: str) -> Optional[Dict[str, Any]]:
    try:
        db = _get_firestore_client()
        if db is None:
            return None

        doc_id = f"{symbol.upper()}_{normalize_tf(tf)}"
        doc = db.collection("historicos_metadata").document(doc_id).get()
        if doc.exists:
            return doc.to_dict()
        return None
    except Exception as exc:
        logger.debug("[Firestore] Failed to get metadata for %s/%s: %s", symbol, tf, exc)
        return None


def set_historicos_metadata(symbol: str, tf: str, gcs_path: str, rows_count: int, ttl_seconds: int = 1800) -> bool:
    try:
        db = _get_firestore_client()
        if db is None:
            return False

        doc_id = f"{symbol.upper()}_{normalize_tf(tf)}"
        now_utc = datetime.now(timezone.utc).replace(tzinfo=timezone.utc)

        metadata = {
            "symbol": symbol.upper(),
            "timeframe": normalize_tf(tf),
            "gcs_path": gcs_path,
            "last_update_utc": now_utc,
            "rows_available": rows_count,
            "ttl_seconds": ttl_seconds,
            "is_stale": False,
            "updated_by_pod": os.environ.get("POD_NAME", "unknown"),
        }

        db.collection("historicos_metadata").document(doc_id).set(metadata, merge=True)
        logger.debug("[Firestore] Set metadata for %s/%s: ttl=%ss", symbol, tf, ttl_seconds)
        return True
    except Exception as exc:
        logger.debug("[Firestore] Failed to set metadata for %s/%s: %s", symbol, tf, exc)
        return False


def is_metadata_stale(metadata: Dict[str, Any], tf: str = "1day") -> bool:
    """
    Check if cached metadata is stale based on timeframe-specific TTL.
    
    Different timeframes have different update frequencies:
    - 1m: must be <5 min old (updates every minute)
    - 1h: can be <1 hour old (updates every hour)
    - 1d: can be <24 hours old (updates daily)
    
    Args:
        metadata: Firestore metadata dict with 'last_update_utc'
        tf: Timeframe for TTL lookup (e.g., "1min", "1hour", "1day")
        
    Returns:
        True if metadata is stale (expired), False if fresh
    """
    if not metadata:
        return True

    try:
        last_update = metadata.get("last_update_utc")
        
        # ✅ NEW: Use timeframe-specific TTL instead of fixed 1800s
        ttl_seconds = _get_ttl_for_timeframe(tf)
        
        # Fallback to metadata's own TTL if specified (for backwards compatibility)
        ttl_seconds = metadata.get("ttl_seconds", ttl_seconds)

        if last_update is None:
            return True

        if hasattr(last_update, "timestamp"):
            last_update = datetime.fromtimestamp(last_update.timestamp(), tz=timezone.utc)
        elif isinstance(last_update, datetime):
            if last_update.tzinfo is None:
                last_update = last_update.replace(tzinfo=timezone.utc)
        else:
            return True

        age_seconds = (datetime.now(timezone.utc).replace(tzinfo=timezone.utc) - last_update).total_seconds()
        is_stale = age_seconds > ttl_seconds
        
        if is_stale:
            logger.debug("[Cache] Metadata stale: %s age=%.0fs > ttl=%ds", tf, age_seconds, ttl_seconds)
        
        return is_stale

    except Exception as exc:
        logger.debug("[Firestore] Error checking staleness: %s", exc)
        return True
