"""Pytest fixtures and configuration for all tests."""

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock
from datetime import datetime, timedelta
import pytz

from markettool.core.models.historico import Historico
from markettool.core.models.quote import Quote
from markettool.core.models.signal import Signal, SignalType
from markettool.infra.repositories import (
    FMPQuoteProvider,
    MultiLayerCacheProvider,
    TelegramNotifier,
    FirestoreHistoricosRepository,
)
from markettool.interfaces.containers import DIContainer


# ============================================================================
# Event Loop Configuration
# ============================================================================

@pytest.fixture(scope="session")
def event_loop():
    """Create event loop for async tests."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


# ============================================================================
# Mock Clients
# ============================================================================

@pytest.fixture
def mock_firestore():
    """Mock Firestore client."""
    return MagicMock()


@pytest.fixture
def mock_gcs():
    """Mock GCS client."""
    return MagicMock()


@pytest.fixture
def mock_fmp():
    """Mock FMP client."""
    return MagicMock()


@pytest.fixture
def mock_telegram():
    """Mock Telegram application."""
    mock = MagicMock()
    mock.bot = AsyncMock()
    return mock


@pytest.fixture
def mock_memory_cache():
    """Mock memory cache."""
    return AsyncMock()


@pytest.fixture
def mock_local_cache():
    """Mock local cache."""
    return AsyncMock()


@pytest.fixture
def mock_gcs_cache():
    """Mock GCS cache."""
    return AsyncMock()


# ============================================================================
# Domain Models
# ============================================================================

@pytest.fixture
def sample_historico():
    """Create sample Historico for testing."""
    return Historico(
        symbol="AAPL",
        timeframe="1h",
        open=[100.0, 101.0, 102.0],
        high=[101.0, 102.0, 103.0],
        low=[99.0, 100.0, 101.0],
        close=[100.5, 101.5, 102.5],
        volume=[1000000, 1100000, 1200000],
        timestamps=[
            datetime.now(pytz.UTC) - timedelta(hours=2),
            datetime.now(pytz.UTC) - timedelta(hours=1),
            datetime.now(pytz.UTC),
        ],
    )


@pytest.fixture
def sample_quote():
    """Create sample Quote for testing."""
    return Quote(
        symbol="EURUSD",
        price=1.0950,
        bid=1.0948,
        ask=1.0952,
        timestamp=datetime.now(pytz.UTC),
        source="fmp",
    )


@pytest.fixture
def sample_signal():
    """Create sample Signal for testing."""
    return Signal(
        symbol="AAPL",
        signal_type=SignalType.BUY,
        confidence=0.85,
        price=105.0,
        timestamp=datetime.now(pytz.UTC),
    )


# ============================================================================
# Adapters
# ============================================================================

@pytest.fixture
def fmp_quote_provider(mock_fmp):
    """Create FMPQuoteProvider for testing."""
    return FMPQuoteProvider(fmp_client=mock_fmp)


@pytest.fixture
def multi_layer_cache(mock_memory_cache, mock_local_cache, mock_gcs_cache):
    """Create MultiLayerCacheProvider for testing."""
    return MultiLayerCacheProvider(
        memory_cache=mock_memory_cache,
        local_cache=mock_local_cache,
        gcs_cache=mock_gcs_cache,
    )


@pytest.fixture
def telegram_notifier(mock_telegram):
    """Create TelegramNotifier for testing."""
    return TelegramNotifier(
        telegram_app=mock_telegram,
        chat_id="123456789",
    )


@pytest.fixture
def firestore_repository(mock_firestore, mock_fmp):
    """Create FirestoreHistoricosRepository for testing."""
    return FirestoreHistoricosRepository(
        firestore_client=mock_firestore,
        fmp_client=mock_fmp,
    )


# ============================================================================
# Dependency Injection Container
# ============================================================================

@pytest.fixture
def di_container(
    firestore_repository,
    fmp_quote_provider,
    multi_layer_cache,
    telegram_notifier,
):
    """Create DIContainer with all mocked dependencies."""
    return DIContainer(
        historicos_repo=firestore_repository,
        quote_provider=fmp_quote_provider,
        cache_provider=multi_layer_cache,
        notifier=telegram_notifier,
    )
