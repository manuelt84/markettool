"""Historicos cache layer (local, GCS, Firestore metadata, lazy loader)."""

from __future__ import annotations

import json
import logging
import os
import time
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import numpy as np
import pandas as pd
import pytz
from google.cloud import firestore
from google.cloud import storage

from markettool.core.config import load_config
from markettool.infra.fmp import normalize_tf
from markettool.core.cache_config import validate_data_freshness, get_freshness_requirement_for_timeframe, CACHE_CONFIG


logger = logging.getLogger("MarketTool")
APP_CONFIG = load_config()

# ============================================================================
# Thread-safe infrastructure for incremental cache merging
# ============================================================================

# Per-file locks dictionary to allow concurrent updates to different symbols
_FILE_LOCKS_DICT: Dict[str, threading.RLock] = {}
_LOCKS_MUTEX = threading.RLock()  # Protects the locks dictionary itself

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


def _hist_path(symbol: str, tf: str) -> str:
    if hasattr(APP_CONFIG, "storage_format") and APP_CONFIG.storage_format == "json":
        return _hist_path_json(symbol, tf)
    return _hist_path_csv(symbol, tf)


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
            
            logger.info("[HistCache] Saved %s/%s: %d rows (new=%d, total=%d)",
                       symbol, tf, total_rows, new_rows, total_rows)
            
        except Exception as exc:
            logger.warning("[HistCache] Incremental save failed %s/%s: %s", symbol, tf, exc)
            
            # Fallback: try simple save without merge
            try:
                simple_payload = out[["time", "open", "high", "low", "close", "volume"]].tail(1000).to_dict(orient="records")
                os.makedirs(APP_CONFIG.hist_dir, exist_ok=True)
                with open(local_hist, "w", encoding="utf-8") as f:
                    json.dump(simple_payload, f, ensure_ascii=False)
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
        
        # 🆕 Validate data freshness (max age per timeframe)
        is_fresh, age_seconds, reason = validate_data_freshness(df, symbol, tf)
        if not is_fresh:
            logger.debug("[hist_local] Data too stale for %s/%s: %s (age=%ds)", 
                        symbol, tf, reason, age_seconds)
            return None
        
        logger.debug("[hist_local] Loaded fresh %s/%s: %d rows, age=%ds, size=%.2fMB", 
                    symbol, tf, len(df), age_seconds, file_size_mb)
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


@safe_op(default=pd.DataFrame(columns=["open", "high", "low", "close", "volume"]))
def load_cached_history(symbol: str, tf: str) -> pd.DataFrame:
    """
    Carga historicos del cache.
    Orden:
      1) LazyHistoricosLoader
      2) Local JSON cache
      3) Firestore metadata (TTL compartido)
      4) GCS
      5) Legacy local files
    """
    # Opcion 1: Lazy loader
    try:
        df = _LAZY_HIST_LOADER.get(symbol, tf)
        if not df.empty:
            logger.debug("[load_cached] Hit LazyLoader: %s/%s", symbol, tf)
            return df
    except Exception as exc:
        logger.debug("[LazyLoader] Failed to load %s/%s: %s", symbol, tf, exc)

    # Opcion 2: Local files
    try:
        df = _load_local(symbol, tf)
        if df is not None and not df.empty:
            logger.debug("[load_cached] Hit Local: %s/%s", symbol, tf)
            try:
                _LAZY_HIST_LOADER.put(symbol, tf, df)
            except Exception:
                pass
            return df
    except Exception as exc:
        logger.debug("[load_cached] Local check failed: %s", exc)

    # Opcion 3: Firestore metadata
    try:
        metadata = get_historicos_metadata(symbol, tf)
        if metadata is not None and not is_metadata_stale(metadata, tf):
            logger.debug("[load_cached] Firestore metadata valid: %s/%s", symbol, tf)
            try:
                df = load_from_gcs(symbol, tf)
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
        if "time" not in df.columns and "date" not in df.columns:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

        time_col = "time" if "time" in df.columns else "date"
        df[time_col] = pd.to_datetime(df[time_col], errors="coerce", utc=True)
        df = df.dropna(subset=[time_col]).set_index(time_col).sort_index()
        df = _ensure_cols(df)
        if df.index.tz is None:
            df.index = df.index.tz_localize(pytz.UTC)
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
            _save_local_history_df(symbol, tf, out)
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
                return pd.DataFrame(data)
            except json.JSONDecodeError:
                try:
                    lines = content.split("\n")
                    data = [json.loads(line) for line in lines if line.strip()]
                    return pd.DataFrame(data)
                except json.JSONDecodeError:
                    logger.error("[LazyLoader] Invalid JSON in %s", filepath)
                    return pd.DataFrame()

        except Exception as exc:
            logger.error("[LazyLoader] Error loading %s: %s", symbol, exc)
            return pd.DataFrame()

    def put(self, symbol: str, temporalidad: str, df: pd.DataFrame) -> None:
        cache_key = f"{symbol.upper()}"
        with self._lock:
            if len(self._cache) >= self.maxsize:
                oldest_key = min(self._cache_times, key=self._cache_times.get)
                del self._cache[oldest_key]
                del self._cache_times[oldest_key]
                logger.debug("[LazyLoader] Evicted %s from cache", oldest_key)

            self._cache[cache_key] = df.copy()
            self._cache_times[cache_key] = time.time()
            logger.debug("[LazyLoader] Cached %s (%d rows)", symbol, len(df))

    def clear_cache(self) -> None:
        with self._lock:
            self._cache.clear()
            self._cache_times.clear()
            logger.info("[LazyLoader] Cache cleared")


_LAZY_HIST_LOADER = LazyHistoricosLoader(
    hist_dir=os.environ.get("HIST_DIR", "historicos"),
    maxsize=APP_CONFIG.cache_max_size_historicos,
    ttl_seconds=APP_CONFIG.cache_ttl_historicos,
)


# ======================================================================
# GCS storage layer
# ======================================================================

_GCS_CLIENT = None
_GCS_BUCKET_NAME = os.environ.get("GCS_BUCKET_NAME", "markettool_bucket")
_GCS_ENABLED = os.environ.get("GCS_ENABLED", "true").lower() == "true"


def _get_gcs_bucket():
    global _GCS_CLIENT
    if not _GCS_ENABLED:
        return None
    try:
        if _GCS_CLIENT is None:
            _GCS_CLIENT = storage.Client()
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

        payload = out[["time", "open", "high", "low", "close", "volume"]].tail(1000).to_dict(orient="records")

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
