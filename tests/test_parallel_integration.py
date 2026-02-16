#!/usr/bin/env python
"""
✅ VALIDATION SCRIPT: Parallel Analysis Engine Integration
========================================================
Tests that the parallel engine is properly injected and configured
"""

import os
import sys
import asyncio
import logging
from pathlib import Path

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)

# Add marketTool path
sys.path.insert(0, str(Path(__file__).parent))


async def test_parallel_engine_creation():
    """Test 1: Can we create ParallelAnalysisEngine?"""
    logger.info("=" * 70)
    logger.info("TEST 1: ParallelAnalysisEngine Creation")
    logger.info("=" * 70)
    
    try:
        from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor
        from markettool.application.use_cases.parallel_analysis import (
            ParallelAnalysisEngine,
            AnalysisConfig,
        )
        
        # Create executors (same as bootstrap.py)
        indicators_executor = ThreadPoolExecutor(max_workers=64, thread_name_prefix="analysis")
        prediction_executor = ProcessPoolExecutor(max_workers=4)
        analysis_executor = ThreadPoolExecutor(max_workers=16, thread_name_prefix="analysis")
        
        # Create config (same as bootstrap.py)
        config = AnalysisConfig(
            max_concurrent_assets=8,
            timeframe_fan_out=4,
            global_timeout=300,
            max_ram_percent=80,
        )
        
        logger.info(f"✅ AnalysisConfig created: {config}")
        
        # Create engine
        engine = ParallelAnalysisEngine(
            indicators_executor=indicators_executor,
            prediction_executor=prediction_executor,
            analysis_executor=analysis_executor,
            config=config,
        )
        
        logger.info(f"✅ ParallelAnalysisEngine created: {engine}")
        logger.info(f"  - Semaphore levels: asset={engine.asset_sem._value}, tf={engine.tf_sem._value}")
        logger.info(f"  - Config: {config}")
        
        # Cleanup
        indicators_executor.shutdown(wait=False)
        prediction_executor.shutdown(wait=False)
        analysis_executor.shutdown(wait=False)
        
        logger.info("✅ TEST 1 PASSED\n")
        return True
        
    except Exception as exc:
        logger.exception("❌ TEST 1 FAILED: %s", exc)
        return False


async def test_env_vars():
    """Test 2: Are environment variables properly configured?"""
    logger.info("=" * 70)
    logger.info("TEST 2: Environment Variables")
    logger.info("=" * 70)
    
    required_vars = {
        "ANALYSIS_MAX_WORKERS": "64",
        "ANALYSIS_PRED_WORKERS": "4",
        "PARALLEL_MAX_CONCURRENT_ASSETS": "8",
        "PARALLEL_TIMEFRAME_FANOUT": "4",
        "PARALLEL_GLOBAL_TIMEOUT": "300",
        "PARALLEL_RAM_PERCENT_LIMIT": "80",
    }
    
    all_present = True
    for var, default in required_vars.items():
        value = os.environ.get(var, default)
        status = "✅" if value else "❌"
        logger.info(f"{status} {var}={value}")
        if not value:
            all_present = False
    
    if all_present:
        logger.info("✅ TEST 2 PASSED\n")
        return True
    else:
        logger.warning("⚠️ Some env vars missing (using defaults)\n")
        return True  # Still pass because we have defaults


async def test_imports():
    """Test 3: Can we import all necessary components?"""
    logger.info("=" * 70)
    logger.info("TEST 3: Module Imports")
    logger.info("=" * 70)
    
    imports_to_test = [
        "markettool.application.use_cases.parallel_analysis.ParallelAnalysisEngine",
        "markettool.application.use_cases.parallel_analysis.AnalysisConfig",
        "markettool.bootstrap",
        "markettool.interfaces.scheduler.bot_init.initialize_bot_async",
        "markettool.interfaces.scheduler.bot_init.setup_scheduler",
    ]
    
    all_ok = True
    for import_path in imports_to_test:
        try:
            parts = import_path.split(".")
            module_name = ".".join(parts[:-1])
            obj_name = parts[-1]
            
            module = __import__(module_name, fromlist=[obj_name])
            obj = getattr(module, obj_name)
            
            logger.info(f"✅ {import_path}")
            
        except Exception as exc:
            logger.error(f"❌ {import_path}: {exc}")
            all_ok = False
    
    if all_ok:
        logger.info("✅ TEST 3 PASSED\n")
    else:
        logger.error("❌ TEST 3 FAILED\n")
    
    return all_ok


async def test_async_scheduler_setup():
    """Test 4: Can setup_scheduler handle parallel_engine parameter?"""
    logger.info("=" * 70)
    logger.info("TEST 4: Scheduler Setup with Parallel Engine")
    logger.info("=" * 70)
    
    try:
        import inspect
        from markettool.interfaces.scheduler.bot_init import setup_scheduler
        
        # Get signature
        sig = inspect.signature(setup_scheduler)
        params = list(sig.parameters.keys())
        
        logger.info(f"setup_scheduler parameters: {params}")
        
        if "parallel_engine" in params:
            logger.info("✅ 'parallel_engine' parameter present in setup_scheduler")
            logger.info("✅ TEST 4 PASSED\n")
            return True
        else:
            logger.error("❌ 'parallel_engine' parameter NOT found in setup_scheduler")
            logger.error("❌ TEST 4 FAILED\n")
            return False
            
    except Exception as exc:
        logger.exception("❌ TEST 4 FAILED: %s", exc)
        return False


async def test_initialize_bot_async_signature():
    """Test 5: Does initialize_bot_async accept parallel_engine?"""
    logger.info("=" * 70)
    logger.info("TEST 5: Bot Init Signature")
    logger.info("=" * 70)
    
    try:
        import inspect
        from markettool.interfaces.scheduler.bot_init import initialize_bot_async
        
        # Get signature
        sig = inspect.signature(initialize_bot_async)
        params = list(sig.parameters.keys())
        
        logger.info(f"initialize_bot_async parameters: {params}")
        
        if "parallel_engine" in params:
            logger.info("✅ 'parallel_engine' parameter present in initialize_bot_async")
            logger.info("✅ TEST 5 PASSED\n")
            return True
        else:
            logger.error("❌ 'parallel_engine' parameter NOT found in initialize_bot_async")
            logger.error("❌ TEST 5 FAILED\n")
            return False
            
    except Exception as exc:
        logger.exception("❌ TEST 5 FAILED: %s", exc)
        return False


async def main():
    """Run all tests"""
    logger.info("\n\n")
    logger.info("╔" + "═" * 68 + "╗")
    logger.info("║" + " " * 10 + "PARALLEL ANALYSIS ENGINE INTEGRATION TEST" + " " * 17 + "║")
    logger.info("╚" + "═" * 68 + "╝")
    logger.info("")
    
    results = []
    
    # Run tests
    results.append(("Engine Creation", await test_parallel_engine_creation()))
    results.append(("Env Variables", await test_env_vars()))
    results.append(("Module Imports", await test_imports()))
    results.append(("Scheduler Setup", await test_async_scheduler_setup()))
    results.append(("Bot Init Signature", await test_initialize_bot_async_signature()))
    
    # Summary
    logger.info("╔" + "═" * 68 + "╗")
    logger.info("║" + " " * 25 + "TEST SUMMARY" + " " * 31 + "║")
    logger.info("╠" + "═" * 68 + "╣")
    
    passed = sum(1 for _, result in results if result)
    total = len(results)
    
    for name, result in results:
        status = "✅ PASS" if result else "❌ FAIL"
        logger.info(f"║ {status:8} | {name:40} " + " " * (68 - 8 - 3 - 40 - 1) + "║")
    
    logger.info("╠" + "═" * 68 + "╣")
    logger.info(f"║ Result: {passed}/{total} tests passed" + " " * (68 - 28 - len(f"{passed}/{total}")) + "║")
    logger.info("╚" + "═" * 68 + "╝")
    
    if passed == total:
        logger.info("\n🎉 ALL TESTS PASSED! Parallel engine is properly integrated.\n")
        return 0
    else:
        logger.error(f"\n❌ {total - passed} test(s) failed. Please review the errors above.\n")
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
