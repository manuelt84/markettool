"""
Centralized cache configuration and validation.

This module defines consistent TTL policies, age thresholds, and validation
logic for all cache layers (memory, local, GCS, etc.) to prevent stale data
from being served.
"""

import logging
from typing import Optional, Tuple
pass

logger = logging.getLogger("MarketTool")


# ============================================================================
# PROPOSAL 1: UNIFIED TTL AND AGE WINDOWS
# ============================================================================
# All cache layers and validation use these values for consistency

CACHE_CONFIG = {
    # Memory cache (hot/fast layer)
    'memory_ttl_seconds': 600,           # 10 minutes - hot data
    'memory_max_size': 10,               # Max entries in memory
    
    # Local disk cache (fallback)
    'local_ttl_seconds': 3600,           # 1 hour
    
    # GCS backup cache (archive)
    'gcs_ttl_seconds': 86400,            # 24 hours
    
    # ★ CRITICAL: Maximum age for data to be considered "fresh"
    # This prevents serving stale data even if cache hasn't expired
    'max_data_age_seconds': 120,         # 2 minutes for data to be "fresh"
    
    # ★ Threshold for considering data "stale" and triggering refresh
    'stale_threshold_seconds': 1800,     # 30 minutes = data older than this needs refresh
    
    # Per-timeframe freshness requirements (in seconds)
    'freshness_by_timeframe': {
        '1min':     60,       # 1m bars must be < 1 min old
        '5min':     300,      # 5m bars must be < 5 min old
        '15min':    900,      # 15m bars must be < 15 min old
        '30min':    1800,     # 30m bars must be < 30 min old
        '1hour':    3600,     # 1h bars must be < 1 hour old
        '4hour':    7200,     # 4h bars must be < 2 hours old
        '1day':     86400,    # 1d bars must be < 1 day old
        '1week':    604800,   # 1w bars must be < 1 week old
        '_default': 600,      # 10 minutes for unknown timeframes
    }
}


def get_freshness_requirement_for_timeframe(timeframe: str) -> int:
    """
    Get maximum age in seconds for data to be considered "fresh" for a timeframe.
    
    Args:
        timeframe: Timeframe string (e.g., '1min', '5min', '1hour', '1day')
        
    Returns:
        Maximum age in seconds
    """
    tf_norm = str(timeframe).lower().strip()
    
    # Exact match
    if tf_norm in CACHE_CONFIG['freshness_by_timeframe']:
        return CACHE_CONFIG['freshness_by_timeframe'][tf_norm]
    
    # Partial matches for common variants
    if '1m' in tf_norm or tf_norm == '1':
        return CACHE_CONFIG['freshness_by_timeframe']['1min']
    if '5m' in tf_norm or tf_norm == '5':
        return CACHE_CONFIG['freshness_by_timeframe']['5min']
    if '15m' in tf_norm or tf_norm == '15':
        return CACHE_CONFIG['freshness_by_timeframe']['15min']
    if '30m' in tf_norm or tf_norm == '30':
        return CACHE_CONFIG['freshness_by_timeframe']['30min']
    if ('1h' in tf_norm or '60' in tf_norm) and '4h' not in tf_norm:
        return CACHE_CONFIG['freshness_by_timeframe']['1hour']
    if '4h' in tf_norm or tf_norm == '4':
        return CACHE_CONFIG['freshness_by_timeframe']['4hour']
    if '1d' in tf_norm or 'daily' in tf_norm:
        return CACHE_CONFIG['freshness_by_timeframe']['1day']
    if '1w' in tf_norm or 'weekly' in tf_norm:
        return CACHE_CONFIG['freshness_by_timeframe']['1week']
    
    # Default for unknown
    logger.debug("[CacheConfig] Unknown timeframe '%s', using default freshness=%ds", 
                 timeframe, CACHE_CONFIG['freshness_by_timeframe']['_default'])
    return CACHE_CONFIG['freshness_by_timeframe']['_default']


# ============================================================================
# PROPOSAL 2: VALIDATE DATA RECENCY
# ============================================================================

def validate_data_freshness(
    df,  # pd.DataFrame or None
    symbol: str,
    timeframe: str,
    data_timestamp: Optional[float] = None
) -> Tuple[bool, int, str]:
    """
    Validate if data is "fresh" enough for the given timeframe.
    
    This prevents serving data that technically passed the cache TTL but is
    older than the timeframe frequency should allow.
    
    Args:
        df: DataFrame with data (should have datetime index in UTC)
        symbol: Symbol being validated
        timeframe: Timeframe ('1min', '5min', etc.)
        data_timestamp: Unix timestamp when data was fetched (if None, uses latest candle time)
        
    Returns:
        Tuple of (is_fresh: bool, age_seconds: int, reason: str)
        
    Example:
        is_fresh, age, reason = validate_data_freshness(df, 'EURUSD', '1hour')
        if not is_fresh:
            logger.warning(f"Data too old: {reason}")
            # Force refresh from external source
    """
    import pandas as pd
    
    # No data = not fresh
    if df is None or (isinstance(df, pd.DataFrame) and df.empty):
        return False, 0, "No data"
    
    try:
        # Get last candle time
        if isinstance(df, pd.DataFrame):
            if len(df) == 0:
                return False, 0, "Empty DataFrame"
            
            last_time = df.index[-1]
            if isinstance(last_time, str):
                last_time = pd.to_datetime(last_time, utc=True)
            else:
                # Handle both tz-aware and tz-naive timestamps
                if hasattr(last_time, 'tz') and last_time.tz is not None:
                    # Already tz-aware, just convert to UTC if needed
                    last_time = pd.Timestamp(last_time).tz_convert('UTC')
                else:
                    # tz-naive, localize to UTC
                    last_time = pd.Timestamp(last_time).tz_localize('UTC', ambiguous='raise', nonexistent='raise')
        else:
            # Assume it's a timestamp
            last_time = pd.Timestamp(data_timestamp, unit='s', tz='UTC') if data_timestamp else pd.Timestamp.now(tz='UTC')
        
        # Current time
        now = pd.Timestamp.now(tz='UTC')
        
        # Calculate age
        age = (now - last_time).total_seconds()
        
        # Get freshness requirement for this timeframe
        max_age = get_freshness_requirement_for_timeframe(timeframe)
        
        # Check if fresh
        is_fresh = age <= max_age
        
        reason = f"age={int(age)}s, max={int(max_age)}s for {timeframe}" if is_fresh else \
                 f"STALE: age={int(age)}s exceeds max={int(max_age)}s for {timeframe}"
        
        logger.info(f"[Data Freshness] {symbol}/{timeframe}: {reason}, fresh={is_fresh}")
        
        return is_fresh, int(age), reason
        
    except Exception as exc:
        logger.warning(f"[Data Freshness] Error validating {symbol}/{timeframe}: {exc}")
        return False, 0, f"Validation error: {exc}"


def check_cache_expiration(
    cached_timestamp: float,
    cache_ttl_seconds: int,
    max_data_age: Optional[int] = None
) -> Tuple[bool, int, str]:
    """
    Check if cached entry is expired based on TTL AND max data age.
    
    Args:
        cached_timestamp: Unix timestamp when data was cached
        cache_ttl_seconds: Normal TTL in seconds (e.g., 600 for 10 min)
        max_data_age: Max age data is allowed (overrides TTL if set)
        
    Returns:
        Tuple of (is_valid: bool, age_seconds: int, reason: str)
    """
    from time import time as time_now
    
    now = time_now()
    age = now - cached_timestamp
    
    # Check TTL first
    if age > cache_ttl_seconds:
        return False, int(age), f"TTL expired: {int(age)}s > {cache_ttl_seconds}s"
    
    # Check max data age if specified
    if max_data_age and age > max_data_age:
        return False, int(age), f"Data too old: {int(age)}s > {max_data_age}s"
    
    return True, int(age), f"Valid: age={int(age)}s"
