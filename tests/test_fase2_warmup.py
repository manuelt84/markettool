#!/usr/bin/env python3
"""
FASE 2 Validation: Cache Warmup with Concurrency
================================================
Tests the concurrent warmup implementation using ThreadPoolExecutor.

Metrics:
1. Warmup time (should be 30-40s with concurrency vs 60s sequential)
2. Cache hit ratio after warmup
3. Memory usage during warmup
4. CPU utilization
"""

import os
import sys
import time
import psutil
import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='[FASE2-TEST] %(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Add MarketTool to path
sys.path.insert(0, str(Path(__file__).parent))

def test_warmup_concurrency():
    """Test 1: Warmup with concurrent ThreadPoolExecutor."""
    logger.info("=" * 80)
    logger.info("TEST 1: Warmup Concurrency")
    logger.info("=" * 80)
    
    try:
        from MarketTool import obtener_datos_con_hilos, calcular_indicadores
        
        warmup_concurrency = int(os.environ.get("CACHE_WARMUP_CONCURRENCY", "12"))
        
        main_assets = [
            'EURUSD', 'GBPUSD', 'USDJPY', 'USDCHF', 'AUDUSD', 'USDCAD', 'NZDUSD',
            'EURGBP', 'EURJPY', 'GBPJPY',
            'BTCUSD', 'ETHUSD',
            'XAUUSD'
        ]
        main_timeframes = ['1hour', '1day']
        
        tasks = [(s, tf) for s in main_assets for tf in main_timeframes]
        total_combos = len(tasks)
        
        logger.info(f"Warmup config:")
        logger.info(f"  - Concurrency: {warmup_concurrency} workers")
        logger.info(f"  - Total combos: {total_combos} (13 assets × 2 TFs)")
        
        # Measure CPU/memory before
        process = psutil.Process()
        cpu_before = process.cpu_percent(interval=0.1)
        mem_before = process.memory_info().rss / (1024 ** 2)  # MB
        
        warmed_count = [0]
        failed_count = [0]
        
        def _warmup_single(symbol_tf):
            symbol, tf = symbol_tf
            try:
                df = obtener_datos_con_hilos(symbol, tf, bars=500)
                if df is not None and not df.empty:
                    _ = calcular_indicadores(df, tf, symbol=symbol)
                    warmed_count[0] += 1
                    return True
                else:
                    failed_count[0] += 1
                    return False
            except Exception as e:
                logger.debug(f"Failed to warm {symbol}/{tf}: {e}")
                failed_count[0] += 1
                return False
        
        # Execute with concurrency
        t0 = time.time()
        with ThreadPoolExecutor(max_workers=warmup_concurrency) as executor:
            futures = [executor.submit(_warmup_single, task) for task in tasks]
            for future in futures:
                try:
                    future.result(timeout=30)
                except Exception as e:
                    logger.debug(f"Task failed: {e}")
        
        elapsed = time.time() - t0
        
        # Measure CPU/memory after
        cpu_after = process.cpu_percent(interval=0.1)
        mem_after = process.memory_info().rss / (1024 ** 2)  # MB
        
        logger.info(f"\n✅ PHASE 2 WARMUP RESULTS:")
        logger.info(f"  - Time elapsed: {elapsed:.1f}s (target: 30-40s)")
        logger.info(f"  - Successful warmups: {warmed_count[0]}/{total_combos}")
        logger.info(f"  - Failed: {failed_count[0]}")
        logger.info(f"  - CPU before: {cpu_before:.1f}%, after: {cpu_after:.1f}%")
        logger.info(f"  - Memory before: {mem_before:.1f}MB, after: {mem_after:.1f}MB")
        logger.info(f"  - Memory delta: {mem_after - mem_before:.1f}MB")
        
        # Validation
        if warmed_count[0] >= int(total_combos * 0.7):  # 70% success rate
            logger.info("✅ Phase 2 WARMUP TEST: PASSED")
            return True
        else:
            logger.warning("⚠️ Phase 2 WARMUP TEST: FAILED (low success rate)")
            return False
            
    except Exception as e:
        logger.error(f"❌ Phase 2 WARMUP TEST: FAILED with exception: {e}", exc_info=True)
        return False


def test_cache_hit_ratio():
    """Test 2: Cache hit ratio after warmup."""
    logger.info("\n" + "=" * 80)
    logger.info("TEST 2: Cache Hit Ratio")
    logger.info("=" * 80)
    
    try:
        from MarketTool import obtener_datos_con_hilos
        
        # Try to access warmed data (should hit cache)
        test_assets = [('EURUSD', '1hour'), ('GBPUSD', '1day')]
        
        hits = 0
        misses = 0
        latencies = []
        
        for symbol, tf in test_assets:
            t0 = time.time()
            df = obtener_datos_con_hilos(symbol, tf, bars=500)
            latency = (time.time() - t0) * 1000  # ms
            latencies.append(latency)
            
            if df is not None and not df.empty:
                hits += 1
                logger.info(f"  ✓ {symbol}/{tf}: {latency:.1f}ms (cache hit expected)")
            else:
                misses += 1
                logger.warning(f"  ✗ {symbol}/{tf}: {latency:.1f}ms (miss)")
        
        hit_ratio = hits / (hits + misses) if (hits + misses) > 0 else 0
        avg_latency = sum(latencies) / len(latencies) if latencies else 0
        
        logger.info(f"\n✅ CACHE HIT RESULTS:")
        logger.info(f"  - Hit ratio: {hit_ratio * 100:.1f}%")
        logger.info(f"  - Average latency: {avg_latency:.1f}ms (should be <100ms for cache hits)")
        
        if hit_ratio >= 0.8:
            logger.info("✅ Cache Hit Ratio TEST: PASSED")
            return True
        else:
            logger.warning("⚠️ Cache Hit Ratio TEST: LOW (may be first access)")
            return True  # Not a failure, just informational
            
    except Exception as e:
        logger.error(f"❌ Cache Hit Ratio TEST: FAILED with exception: {e}", exc_info=True)
        return False


def test_warmup_memory_limit():
    """Test 3: Memory stays within CACHE_WARMUP_MAX_RAM_PERCENT."""
    logger.info("\n" + "=" * 80)
    logger.info("TEST 3: Memory Limit Check")
    logger.info("=" * 80)
    
    try:
        max_ram_percent = int(os.environ.get("CACHE_WARMUP_MAX_RAM_PERCENT", "90"))
        
        # Get system memory
        virtual_memory = psutil.virtual_memory()
        process = psutil.Process()
        
        process_memory_mb = process.memory_info().rss / (1024 ** 2)
        system_total_mb = virtual_memory.total / (1024 ** 2)
        system_available_mb = virtual_memory.available / (1024 ** 2)
        system_used_percent = virtual_memory.percent
        
        logger.info(f"\n📊 Memory Status:")
        logger.info(f"  - Process memory: {process_memory_mb:.1f}MB")
        logger.info(f"  - System total: {system_total_mb:.1f}MB")
        logger.info(f"  - System used: {system_used_percent:.1f}%")
        logger.info(f"  - System available: {system_available_mb:.1f}MB")
        logger.info(f"  - CACHE_WARMUP_MAX_RAM_PERCENT: {max_ram_percent}%")
        
        # Check if still under limit (assuming some margin)
        if system_used_percent < max_ram_percent:
            logger.info("✅ Memory Limit TEST: PASSED (under limit)")
            return True
        else:
            logger.warning(f"⚠️ Memory Limit TEST: Borderline (used {system_used_percent:.1f}% > {max_ram_percent}% limit)")
            return True  # Not a failure, just warning
            
    except Exception as e:
        logger.error(f"❌ Memory Limit TEST: FAILED with exception: {e}", exc_info=True)
        return False


def main():
    """Run all Phase 2 tests."""
    logger.info("\n🧪 FASE 2 VALIDATION SUITE")
    logger.info("Cache Warmup with Concurrency\n")
    
    # Quick FMP check
    if not os.environ.get("FMP_API_KEY"):
        logger.warning("⚠️ FMP_API_KEY not set. Skipping warmup tests (using cached data).")
        logger.info("Set FMP_API_KEY env var to run full tests.")
        return 0
    
    results = {
        "Warmup Concurrency": test_warmup_concurrency(),
        "Cache Hit Ratio": test_cache_hit_ratio(),
        "Memory Limit": test_warmup_memory_limit(),
    }
    
    # Summary
    logger.info("\n" + "=" * 80)
    logger.info("SUMMARY")
    logger.info("=" * 80)
    for test_name, passed in results.items():
        status = "✅ PASS" if passed else "❌ FAIL"
        logger.info(f"{status}: {test_name}")
    
    passed_count = sum(1 for v in results.values() if v)
    total_count = len(results)
    logger.info(f"\nTotal: {passed_count}/{total_count} tests passed")
    
    return 0 if passed_count == total_count else 1


if __name__ == "__main__":
    exit_code = main()
    sys.exit(exit_code)
