"""Integration tests for DI Container and routes."""

import pytest
from unittest.mock import AsyncMock
from datetime import datetime
import pytz

from markettool.interfaces.containers import DIContainer
from markettool.infra.repositories import (
    FirestoreHistoricosRepository,
    FMPQuoteProvider,
    MultiLayerCacheProvider,
    TelegramNotifier,
)
from markettool.core.models.historico import Historico
from markettool.core.models.quote import Quote


@pytest.mark.asyncio
class TestDIContainerIntegration:
    """Test full DI container integration."""
    
    def test_container_creates_all_use_cases(self, di_container):
        """Test that container provides all use cases."""
        # Access all use cases
        uc_historicos = di_container.get_historicos
        uc_quote = di_container.get_quote
        uc_analysis = di_container.run_analysis
        uc_cache = di_container.warm_cache
        
        # All should be non-None
        assert uc_historicos is not None
        assert uc_quote is not None
        assert uc_analysis is not None
        assert uc_cache is not None
    
    def test_use_case_caching(self, di_container):
        """Test that use cases are cached by container."""
        uc1 = di_container.get_historicos
        uc2 = di_container.get_historicos
        
        # Should be same instance
        assert uc1 is uc2
    
    def test_container_get_all(self, di_container):
        """Test getting all use cases at once."""
        all_uc = di_container.get_all()
        
        assert "get_historicos" in all_uc
        assert "get_quote" in all_uc
        assert "run_analysis" in all_uc
        assert "warm_cache" in all_uc
    
    async def test_use_case_receives_dependencies(self, di_container, sample_historico):
        """Test that use cases receive their dependencies."""
        di_container.historicos_repo.get_historico = AsyncMock(
            return_value=sample_historico
        )
        di_container.cache_provider.get_historico = AsyncMock(return_value=None)
        
        use_case = di_container.get_historicos
        
        # Use case should be able to call methods
        result = await use_case.execute(symbol="AAPL", timeframe="1h")
        
        assert result is not None
        assert result.symbol == "AAPL"


@pytest.mark.asyncio
class TestContainerWithRealAdapters:
    """Test container with real (but mocked) adapters."""
    
    def test_create_container_with_defaults(
        self,
        mock_firestore,
        mock_gcs,
        mock_fmp,
        mock_telegram,
        mock_memory_cache,
    ):
        """Test creating container with real adapter classes."""
        # Create adapters
        historicos_repo = FirestoreHistoricosRepository(
            firestore_client=mock_firestore,
            fmp_client=mock_fmp,
        )
        
        quote_provider = FMPQuoteProvider(
            fmp_client=mock_fmp,
        )
        
        cache_provider = MultiLayerCacheProvider(
            memory_cache=mock_memory_cache,
        )
        
        notifier = TelegramNotifier(
            telegram_app=mock_telegram,
            chat_id="123",
        )
        
        # Create container
        container = DIContainer(
            historicos_repo=historicos_repo,
            quote_provider=quote_provider,
            cache_provider=cache_provider,
            notifier=notifier,
        )
        
        # Verify container works
        assert container.get_historicos is not None
        assert container.get_quote is not None


@pytest.mark.asyncio
class TestEndToEndFlow:
    """Test complete end-to-end workflow."""
    
    async def test_fetch_quote_flow(self, di_container, sample_quote):
        """Test complete quote fetch flow."""
        # Setup mocks
        di_container.quote_provider.get_quote = AsyncMock(return_value=sample_quote)
        
        # Get use case
        use_case = di_container.get_quote
        
        # Execute
        result = await use_case.execute(symbol="EURUSD")
        
        # Verify
        assert result is not None
        assert result.symbol == "EURUSD"
        assert result.price == 1.0950
    
    async def test_fetch_and_cache_historicos(
        self,
        di_container,
        sample_historico,
    ):
        """Test fetching historicos and caching them."""
        # Setup
        di_container.cache_provider.get_historico = AsyncMock(return_value=None)
        di_container.cache_provider.set_historico = AsyncMock()
        di_container.historicos_repo.get_historico = AsyncMock(
            return_value=sample_historico
        )
        
        # Execute
        use_case = di_container.get_historicos
        result = await use_case.execute(symbol="AAPL", timeframe="1h")
        
        # Verify fetched from repo
        assert result is not None
        assert result.symbol == "AAPL"
        
        # Verify cached
        di_container.cache_provider.set_historico.assert_called_once()
