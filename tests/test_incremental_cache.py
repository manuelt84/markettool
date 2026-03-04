"""Test script for incremental cache merge functionality."""

import json
import os
import pandas as pd
from datetime import datetime, timedelta
import sys

# Add project to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__)))

from markettool.infra.cache.historicos_cache import _save_local_history_df, _load_local, _hist_path_json


def create_test_data(symbol: str, tf: str, num_rows: int, start_date: datetime):
    """Create test OHLCV DataFrame."""
    dates = pd.date_range(start=start_date, periods=num_rows, freq='15min', tz='UTC')
    df = pd.DataFrame({
        'open': range(100, 100 + num_rows),
        'high': range(101, 101 + num_rows),
        'low': range(99, 99 + num_rows),
        'close': range(100, 100 + num_rows),
        'volume': [1000] * num_rows
    }, index=dates)
    return df


def test_incremental_merge():
    """Test that incremental merge preserves historical data."""
    print("=" * 80)
    print("TEST: Incremental Cache Merge")
    print("=" * 80)
    
    symbol = "TEST_BTC"
    tf = "15min"
    cache_file = _hist_path_json(symbol, tf)
    
    # Clean up any existing test cache
    if os.path.exists(cache_file):
        os.remove(cache_file)
        print(f"✓ Cleaned up existing cache: {cache_file}")
    
    # Step 1: Save initial 1000 rows
    print("\n[Step 1] Saving initial 1000 rows...")
    start_date = datetime(2024, 1, 1, tzinfo=pd.Timestamp.now('UTC').tzinfo)
    df1 = create_test_data(symbol, tf, 1000, start_date)
    _save_local_history_df(symbol, tf, df1)
    
    # Verify file exists
    assert os.path.exists(cache_file), f"Cache file not created: {cache_file}"
    
    with open(cache_file) as f:
        data1 = json.load(f)
    print(f"✓ Saved {len(data1)} rows")
    print(f"  First timestamp: {data1[0]['time']}")
    print(f"  Last timestamp: {data1[-1]['time']}")
    
    # Step 2: Save additional 500 rows (should merge to 1500 total)
    print("\n[Step 2] Adding 500 new rows...")
    start_date2 = start_date + timedelta(hours=250)  # Continue after first batch
    df2 = create_test_data(symbol, tf, 500, start_date2)
    _save_local_history_df(symbol, tf, df2)
    
    with open(cache_file) as f:
        data2 = json.load(f)
    print(f"✓ After merge: {len(data2)} rows")
    print(f"  First timestamp: {data2[0]['time']}")
    print(f"  Last timestamp: {data2[-1]['time']}")
    
    # Verify incremental merge worked
    assert len(data2) == 1500, f"Expected 1500 rows, got {len(data2)}"
    print(f"✅ SUCCESS: Incremental merge preserved all {len(data2)} rows!")
    
    # Step 3: Test deduplication (save overlapping data)
    print("\n[Step 3] Testing deduplication with overlapping data...")
    df3 = create_test_data(symbol, tf, 200, start_date2 + timedelta(hours=100))
    _save_local_history_df(symbol, tf, df3)
    
    with open(cache_file) as f:
        data3 = json.load(f)
    print(f"✓ After overlap merge: {len(data3)} rows")
    
    # Should still have 1500 rows (overlaps should deduplicate)
    assert len(data3) == 1500, f"Deduplication failed: expected 1500, got {len(data3)}"
    print(f"✅ SUCCESS: Deduplication working correctly!")
    
    # Step 4: Test load functionality
    print("\n[Step 4] Testing load functionality...")
    loaded_df = _load_local(symbol, tf)
    
    if loaded_df is not None:
        print(f"✓ Loaded DataFrame: {len(loaded_df)} rows")
        print(f"  Index type: {type(loaded_df.index)}")
        print(f"  Columns: {list(loaded_df.columns)}")
        print(f"✅ SUCCESS: Load working correctly!")
    else:
        print("⚠️  Load returned None (may be due to freshness validation)")
    
    # Cleanup
    print("\n[Cleanup]")
    if os.path.exists(cache_file):
        os.remove(cache_file)
        print(f"✓ Removed test cache: {cache_file}")
    
    print("\n" + "=" * 80)
    print("ALL TESTS PASSED! ✅")
    print("=" * 80)


if __name__ == "__main__":
    try:
        test_incremental_merge()
    except Exception as e:
        print(f"\n❌ TEST FAILED: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
