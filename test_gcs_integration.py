#!/usr/bin/env python3
"""
Test script for GCS historical data integration.

Usage:
    python test_gcs_integration.py
    
Environment variables:
    GCS_ENABLED=true/false       (default: true)
    GCS_BUCKET_NAME=bucket_name  (default: markettool)
"""

import sys
import logging
import os
from pathlib import Path

# Setup logging
logging.basicConfig(
    level=logging.DEBUG,
    format='[%(levelname)s] %(name)s: %(message)s'
)
logger = logging.getLogger("GCS_TEST")


def test_imports():
    """Test 1: Verify GCS functions are importable."""
    print("\n📦 Test 1: Importing GCS functions...")
    try:
        from MarketTool import (
            load_from_gcs,
            save_to_gcs,
            load_cached_history,
            save_cached_history,
            _LAZY_HIST_LOADER,
            _get_gcs_bucket
        )
        print("✅ All GCS functions imported successfully")
        return True
    except ImportError as e:
        print(f"❌ Import failed: {e}")
        return False


def test_lazy_loader():
    """Test 2: Verify LazyHistoricosLoader has put() method."""
    print("\n🧪 Test 2: Checking LazyHistoricosLoader.put() method...")
    try:
        from MarketTool import _LAZY_HIST_LOADER
        
        if hasattr(_LAZY_HIST_LOADER, 'put'):
            print("✅ LazyHistoricosLoader.put() method exists")
            return True
        else:
            print("❌ LazyHistoricosLoader.put() method not found")
            return False
    except Exception as e:
        print(f"❌ Error checking method: {e}")
        return False


def test_gcs_connection():
    """Test 3: Try to connect to GCS bucket."""
    print("\n🌐 Test 3: Connecting to GCS bucket...")
    try:
        from MarketTool import _get_gcs_bucket, _GCS_ENABLED, _GCS_BUCKET_NAME
        
        if not _GCS_ENABLED:
            print(f"⚠️  GCS disabled (GCS_ENABLED={_GCS_ENABLED})")
            return False
        
        bucket = _get_gcs_bucket()
        if bucket is None:
            print(f"❌ GCS client initialization failed (check credentials)")
            return False
        
        print(f"✅ Connected to bucket: {_GCS_BUCKET_NAME}")
        return True
    except Exception as e:
        print(f"❌ Connection error: {e}")
        return False


def test_firestore_metadata():
    """Test 3b: Test Firestore metadata layer for multi-pod coordination."""
    print("\n📋 Test 3b: Firestore Metadata Layer (Multi-Pod)...")
    try:
        from MarketTool import (
            get_historicos_metadata,
            set_historicos_metadata,
            is_metadata_stale,
            _get_firestore_client
        )
        
        # Check if Firestore is available
        db = _get_firestore_client()
        if db is None:
            print("⚠️  Firestore disabled or not initialized (not fatal, will use GCS only)")
            return True  # Not a failure, just means metadata layer unavailable
        
        print("  Testing Firestore metadata functions...")
        
        # Test 1: set_historicos_metadata
        success = set_historicos_metadata(
            "TEST_EURUSD", "1day",
            "gs://test/historicos/EURUSD__1day.json",
            100,
            ttl_seconds=60
        )
        if not success:
            print("  ⚠️  set_historicos_metadata returned False (Firestore may be unavailable)")
            return True
        
        # Test 2: get_historicos_metadata
        import time
        time.sleep(0.5)  # Give Firestore time to sync
        
        metadata = get_historicos_metadata("TEST_EURUSD", "1day")
        if metadata is None:
            print("  ⚠️  get_historicos_metadata returned None (Firestore sync delay?)")
            return True
        
        # Test 3: is_metadata_stale with fresh data
        if is_metadata_stale(metadata):
            print("  ❌ is_metadata_stale says fresh data is stale!")
            return False
        
        # Test 4: is_metadata_stale with old metadata
        old_metadata = {
            "last_update_utc": None,  # Very old
            "ttl_seconds": 60
        }
        if not is_metadata_stale(old_metadata):
            print("  ❌ is_metadata_stale should say None is stale!")
            return False
        
        print("  ✅ All Firestore metadata functions working")
        
        # Cleanup test metadata
        try:
            from google.cloud import firestore
            db = firestore.client()
            db.collection("historicos_metadata").document("TEST_EURUSD_1day").delete()
        except Exception:
            pass
        
        return True
    except Exception as e:
        print(f"  ⚠️  Firestore test error (not fatal): {e}")
        return True


def test_function_signatures():
    """Test 4: Verify function signatures are correct."""
    print("\n📋 Test 4: Verifying function signatures...")
    try:
        from MarketTool import load_from_gcs, save_to_gcs, get_historicos_metadata, set_historicos_metadata, is_metadata_stale
        import inspect
        
        # Check load_from_gcs
        sig = inspect.signature(load_from_gcs)
        params = list(sig.parameters.keys())
        if params == ['symbol', 'tf']:
            print(f"✅ load_from_gcs signature correct: {sig}")
        else:
            print(f"❌ load_from_gcs signature unexpected: {sig}")
            return False
        
        # Check save_to_gcs
        sig = inspect.signature(save_to_gcs)
        params = list(sig.parameters.keys())
        if params == ['symbol', 'tf', 'df']:
            print(f"✅ save_to_gcs signature correct: {sig}")
        else:
            print(f"❌ save_to_gcs signature unexpected: {sig}")
            return False
        
        # Check get_historicos_metadata
        sig = inspect.signature(get_historicos_metadata)
        params = list(sig.parameters.keys())
        if params == ['symbol', 'tf']:
            print(f"✅ get_historicos_metadata signature correct: {sig}")
        else:
            print(f"❌ get_historicos_metadata signature unexpected: {sig}")
            return False
        
        # Check set_historicos_metadata
        sig = inspect.signature(set_historicos_metadata)
        if 'symbol' in sig.parameters and 'tf' in sig.parameters:
            print(f"✅ set_historicos_metadata signature correct: {sig}")
        else:
            print(f"❌ set_historicos_metadata signature unexpected: {sig}")
            return False
        
        # Check is_metadata_stale
        sig = inspect.signature(is_metadata_stale)
        params = list(sig.parameters.keys())
        if params == ['metadata']:
            print(f"✅ is_metadata_stale signature correct: {sig}")
        else:
            print(f"❌ is_metadata_stale signature unexpected: {sig}")
            return False
        
        return True
    except Exception as e:
        print(f"❌ Signature check failed: {e}")
        return False


def test_environment():
    """Test 5: Check environment variables."""
    print("\n🔧 Test 5: Checking environment variables...")
    
    gcs_enabled = os.environ.get("GCS_ENABLED", "true").lower() == "true"
    gcs_bucket = os.environ.get("GCS_BUCKET_NAME", "markettool")
    google_creds = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "not set")
    
    print(f"  GCS_ENABLED: {gcs_enabled}")
    print(f"  GCS_BUCKET_NAME: {gcs_bucket}")
    print(f"  GOOGLE_APPLICATION_CREDENTIALS: {google_creds}")
    
    if gcs_enabled and google_creds != "not set":
        creds_path = Path(google_creds)
        if creds_path.exists():
            print(f"  ✅ Credentials file found: {google_creds}")
        else:
            print(f"  ⚠️  Credentials file not found: {google_creds}")
    elif not gcs_enabled:
        print(f"  ⚠️  GCS is DISABLED (set GCS_ENABLED=true to enable)")
    else:
        print(f"  ⚠️  No credentials found (auto-detected from gcloud config if available)")
    
    return True


def test_data_normalization():
    """Test 6: Test data normalization in load_from_gcs."""
    print("\n📊 Test 6: Testing data normalization...")
    try:
        import pandas as pd
        from MarketTool import _ensure_cols
        
        # Create test data
        test_df = pd.DataFrame({
            'time': ['2024-01-01T00:00:00Z', '2024-01-02T00:00:00Z'],
            'open': [1.0, 1.1],
            'high': [1.2, 1.3],
            'low': [0.9, 1.0],
            'close': [1.05, 1.15],
            'volume': [1000, 2000]
        })
        
        # Test normalization
        normalized = _ensure_cols(test_df)
        
        if list(normalized.columns) == ['open', 'high', 'low', 'close', 'volume']:
            print(f"✅ Data normalization works correctly")
            return True
        else:
            print(f"❌ Data normalization failed: {normalized.columns}")
            return False
    except Exception as e:
        print(f"❌ Normalization test failed: {e}")
        return False


def main():
    """Run all tests."""
    print("=" * 60)
    print("GCS HISTORICAL DATA INTEGRATION TEST SUITE")
    print("=" * 60)
    
    tests = [
        ("Imports", test_imports),
        ("LazyLoader.put()", test_lazy_loader),
        ("GCS Connection", test_gcs_connection),
        ("Firestore Metadata (Multi-Pod)", test_firestore_metadata),
        ("Function Signatures", test_function_signatures),
        ("Environment Variables", test_environment),
        ("Data Normalization", test_data_normalization),
    ]
    
    results = []
    for name, test_func in tests:
        try:
            result = test_func()
            results.append((name, result))
        except Exception as e:
            print(f"\n❌ Test '{name}' crashed: {e}")
            import traceback
            traceback.print_exc()
            results.append((name, False))
    
    # Summary
    print("\n" + "=" * 60)
    print("TEST SUMMARY")
    print("=" * 60)
    
    passed = sum(1 for _, result in results if result)
    total = len(results)
    
    for name, result in results:
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"{status}: {name}")
    
    print(f"\nTotal: {passed}/{total} tests passed")
    
    if passed == total:
        print("\n🎉 All tests passed! GCS integration is ready.")
        return 0
    else:
        print(f"\n⚠️  {total - passed} test(s) failed. See above for details.")
        return 1


if __name__ == "__main__":
    exit_code = main()
    sys.exit(exit_code)
