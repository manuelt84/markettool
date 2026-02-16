"""Phase 5 Integration Test - Verify all components work together"""

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock, patch

# Configure logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)


async def test_di_container_creation():
    """Test DIContainer can be created and provides use cases."""
    from markettool.interfaces.containers import DIContainer
    from markettool.infra.repositories import (
        FirestoreHistoricosRepository,
        FMPQuoteProvider,
        MultiLayerCacheProvider,
        TelegramNotifier,
    )
    
    logger.info("✓ Testing DIContainer creation...")
    
    # Create mocks
    firestore_mock = MagicMock()
    gcs_mock = MagicMock()
    fmp_mock = MagicMock()
    telegram_mock = MagicMock()
    memory_cache_mock = AsyncMock()
    
    # Create adapters manually since create_default requires at least one cache layer
    historicos_repo = FirestoreHistoricosRepository(
        firestore_client=firestore_mock,
        fmp_client=fmp_mock,
    )
    
    quote_provider = FMPQuoteProvider(
        fmp_client=fmp_mock,
    )
    
    cache_provider = MultiLayerCacheProvider(
        memory_cache=memory_cache_mock,
    )
    
    notifier = TelegramNotifier(
        telegram_app=telegram_mock,
    )
    
    # Create container
    container = DIContainer(
        historicos_repo=historicos_repo,
        quote_provider=quote_provider,
        cache_provider=cache_provider,
        notifier=notifier,
    )
    
    # Verify use cases are provided
    assert hasattr(container, 'get_historicos'), "Missing get_historicos"
    assert hasattr(container, 'get_quote'), "Missing get_quote"
    assert hasattr(container, 'run_analysis'), "Missing run_analysis"
    assert hasattr(container, 'warm_cache'), "Missing warm_cache"
    
    logger.info("✅ DIContainer creation test PASSED")
    return True


async def test_fmp_quote_provider():
    """Test FMPQuoteProvider adapter."""
    from markettool.infra.repositories import FMPQuoteProvider
    from markettool.core.errors import ExternalAPIError
    
    logger.info("✓ Testing FMPQuoteProvider...")
    
    # Create mock FMP client
    fmp_mock = MagicMock()
    
    # Create provider
    provider = FMPQuoteProvider(fmp_client=fmp_mock)
    
    # Verify provider has required methods
    assert hasattr(provider, 'get_quote'), "Missing get_quote"
    assert hasattr(provider, 'get_quotes'), "Missing get_quotes"
    assert hasattr(provider, 'supported_symbols'), "Missing supported_symbols"
    
    # Test supported symbols
    symbols = provider.supported_symbols()
    assert len(symbols) > 0, "No supported symbols"
    assert "AAPL" in symbols, "Missing AAPL"
    
    logger.info("✅ FMPQuoteProvider test PASSED")
    return True


async def test_multi_layer_cache_provider():
    """Test MultiLayerCacheProvider with fallback."""
    from markettool.infra.repositories import MultiLayerCacheProvider
    from markettool.core.errors import CacheError
    
    logger.info("✓ Testing MultiLayerCacheProvider...")
    
    try:
        # Create mock cache layers
        memory_mock = AsyncMock()
        local_mock = AsyncMock()
        gcs_mock = AsyncMock()
        
        # Create provider (requires at least one cache layer)
        provider = MultiLayerCacheProvider(
            memory_cache=memory_mock,
            local_cache=local_mock,
            gcs_cache=gcs_mock,
        )
        
        # Verify provider has required methods
        methods = ['get', 'set', 'delete', 'exists', 'invalidate', 'clear',
                   'get_historico', 'set_historico', 'invalidate_historico',
                   'get_cache_stats', 'warmup']
        
        for method in methods:
            assert hasattr(provider, method), f"Missing {method}"
        
        # Test error when no cache layers configured
        try:
            bad_provider = MultiLayerCacheProvider()
            assert False, "Should have raised ValueError"
        except ValueError as e:
            error_msg = str(e).lower()
            assert "at least one cache layer" in error_msg, f"Wrong error: {e}"
        
        logger.info("✅ MultiLayerCacheProvider test PASSED")
        return True
    
    except Exception as e:
        logger.error(f"MultiLayerCacheProvider test error: {e}", exc_info=True)
        raise


async def test_telegram_notifier():
    """Test TelegramNotifier adapter."""
    from markettool.infra.repositories import TelegramNotifier
    from markettool.core.models.signal import Signal, SignalType
    
    logger.info("✓ Testing TelegramNotifier...")
    
    # Create mock Telegram app
    telegram_mock = MagicMock()
    telegram_mock.bot = AsyncMock()
    
    # Create notifier
    notifier = TelegramNotifier(
        telegram_app=telegram_mock,
        chat_id="12345",
    )
    
    # Verify notifier has required methods
    assert hasattr(notifier, 'notify_signal'), "Missing notify_signal"
    assert hasattr(notifier, 'notify_analysis_complete'), "Missing notify_analysis_complete"
    assert hasattr(notifier, 'notify_cache_warmed'), "Missing notify_cache_warmed"
    assert hasattr(notifier, 'notify_error'), "Missing notify_error"
    
    # Test chat ID management
    notifier.add_chat_id("67890")
    assert "67890" in notifier.chat_ids, "Chat ID not added"
    
    notifier.remove_chat_id("12345")
    assert "12345" not in notifier.chat_ids, "Chat ID not removed"
    
    logger.info("✅ TelegramNotifier test PASSED")
    return True


async def test_adapter_imports():
    """Test all adapters can be imported."""
    logger.info("✓ Testing adapter imports...")
    
    from markettool.infra.repositories import (
        FirestoreHistoricosRepository,
        FMPQuoteProvider,
        MultiLayerCacheProvider,
        TelegramNotifier,
    )
    
    assert FirestoreHistoricosRepository is not None
    assert FMPQuoteProvider is not None
    assert MultiLayerCacheProvider is not None
    assert TelegramNotifier is not None
    
    logger.info("✅ Adapter imports test PASSED")
    return True


async def test_container_use_case_injection():
    """Test container ctor with manually injected dependencies."""
    from markettool.interfaces.containers import DIContainer
    from markettool.infra.repositories import (
        FirestoreHistoricosRepository,
        FMPQuoteProvider,
        MultiLayerCacheProvider,
        TelegramNotifier,
    )
    
    logger.info("✓ Testing use case injection...")
    
    # Create mocks for adapters
    firestore_mock = MagicMock()
    fmp_mock = MagicMock()
    memory_cache_mock = AsyncMock()
    telegram_mock = MagicMock()
    
    # Create adapters manually
    historicos_repo = FirestoreHistoricosRepository(
        firestore_client=firestore_mock,
        fmp_client=fmp_mock,
    )
    
    quote_provider = FMPQuoteProvider(
        fmp_client=fmp_mock,
    )
    
    cache_provider = MultiLayerCacheProvider(
        memory_cache=memory_cache_mock,
    )
    
    notifier = TelegramNotifier(
        telegram_app=telegram_mock,
        chat_id="12345",
    )
    
    # Create container with manual injection
    container = DIContainer(
        historicos_repo=historicos_repo,
        quote_provider=quote_provider,
        cache_provider=cache_provider,
        notifier=notifier,
    )
    
    # Get use cases (should not raise)
    uc_historicos = container.get_historicos
    uc_quote = container.get_quote
    uc_analysis = container.run_analysis
    uc_cache = container.warm_cache
    
    # Verify use cases are instances (not None)
    assert uc_historicos is not None, "GetHistoricosUseCase is None"
    assert uc_quote is not None, "GetQuoteUseCase is None"
    assert uc_analysis is not None, "RunAnalysisUseCase is None"
    assert uc_cache is not None, "WarmCacheUseCase is None"
    
    # Verify caching (same instance on second access)
    uc_historicos_2 = container.get_historicos
    assert uc_historicos is uc_historicos_2, "Use cases not cached"
    
    logger.info("✅ Use case injection test PASSED")
    return True


async def main():
    """Run all Phase 5 integration tests."""
    logger.info("=" * 70)
    logger.info("PHASE 5 INTEGRATION TEST SUITE")
    logger.info("=" * 70)
    
    tests = [
        test_adapter_imports,
        test_fmp_quote_provider,
        test_multi_layer_cache_provider,
        test_telegram_notifier,
        test_di_container_creation,
        test_container_use_case_injection,
    ]
    
    results = []
    for test in tests:
        try:
            logger.info("")
            result = await test()
            results.append((test.__name__, True, None))
        except Exception as e:
            logger.error(f"❌ {test.__name__} FAILED: {e}")
            results.append((test.__name__, False, str(e)))
    
    logger.info("")
    logger.info("=" * 70)
    logger.info("TEST RESULTS")
    logger.info("=" * 70)
    
    passed = sum(1 for _, success, _ in results if success)
    total = len(results)
    
    for name, success, error in results:
        status = "✅ PASS" if success else "❌ FAIL"
        logger.info(f"{status}: {name}")
        if error:
            logger.info(f"       Error: {error}")
    
    logger.info("")
    logger.info(f"TOTAL: {passed}/{total} tests passed")
    logger.info("=" * 70)
    
    return passed == total


if __name__ == "__main__":
    success = asyncio.run(main())
    exit(0 if success else 1)
