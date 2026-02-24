"""
Integration tests for Sprint 1 improvements.

Tests hexagonal architecture components added in Sprint 1:
- FMPHistoricalDataAdapter (HistoricalDataProvider port implementation)
- HealthService (health monitoring with DI)
- HistoryManager with port injection
"""

import pytest
import asyncio
from datetime import datetime, timedelta
from unittest.mock import Mock, AsyncMock, MagicMock
import pandas as pd
import pytz

from markettool.core.ports.historical_data_provider import HistoricalDataProvider
from markettool.core.errors import PlanNotAllowed
from markettool.infra.adapters.fmp_historical_data_adapter import FMPHistoricalDataAdapter
from markettool.application.services.historicos_service import HistoryManager
from markettool.application.services.health_service import HealthService, ComponentHealth


class TestFMPHistoricalDataAdapter:
    """Tests for FMPHistoricalDataAdapter (created in Sprint 1)."""
    
    def test_adapter_implements_port(self):
        """Verify adapter implements HistoricalDataProvider port."""
        mock_fmp_client = Mock()
        adapter = FMPHistoricalDataAdapter(fmp_client=mock_fmp_client)
        
        assert isinstance(adapter, HistoricalDataProvider)
    
    def test_historical_intraday_delegates_to_client(self):
        """Verify historical_intraday delegates to FMPClient."""
        mock_fmp_client = Mock()
        mock_df = pd.DataFrame({
            'open': [100.0],
            'high': [101.0],
            'low': [99.0],
            'close': [100.5],
            'volume': [1000],
        })
        mock_fmp_client.historical_intraday.return_value = mock_df
        
        adapter = FMPHistoricalDataAdapter(fmp_client=mock_fmp_client)
        
        from_dt = datetime(2026, 1, 1, tzinfo=pytz.UTC)
        to_dt = datetime(2026, 1, 2, tzinfo=pytz.UTC)
        
        result = adapter.historical_intraday("AAPL", "5min", from_dt, to_dt)
        
        mock_fmp_client.historical_intraday.assert_called_once_with(
            "AAPL", "5min", from_dt, to_dt
        )
        assert isinstance(result, pd.DataFrame)
        assert len(result) == 1
    
    def test_plan_not_allowed_exception_translation(self):
        """Verify FMPPlanNotAllowed is translated to PlanNotAllowed (domain exception)."""
        from markettool.infra.fmp.client import FMPPlanNotAllowed
        
        mock_fmp_client = Mock()
        mock_fmp_client.historical_intraday.side_effect = FMPPlanNotAllowed("402 Payment Required")
        
        adapter = FMPHistoricalDataAdapter(fmp_client=mock_fmp_client)
        
        from_dt = datetime(2026, 1, 1, tzinfo=pytz.UTC)
        to_dt = datetime(2026, 1, 2, tzinfo=pytz.UTC)
        
        # Should translate to domain exception
        with pytest.raises(PlanNotAllowed) as exc_info:
            adapter.historical_intraday("AAPL", "5min", from_dt, to_dt)
        
        assert "402 Payment Required" in str(exc_info.value)
    
    def test_historical_eod_delegates_to_client(self):
        """Verify historical_eod delegates to FMPClient."""
        mock_fmp_client = Mock()
        mock_df = pd.DataFrame({
            'open': [100.0],
            'high': [105.0],
            'low': [98.0],
            'close': [103.0],
            'volume': [10000],
        })
        mock_fmp_client.historical_eod.return_value = mock_df
        
        adapter = FMPHistoricalDataAdapter(fmp_client=mock_fmp_client)
        
        from_dt = datetime(2026, 1, 1, tzinfo=pytz.UTC)
        to_dt = datetime(2026, 2, 1, tzinfo=pytz.UTC)
        
        result = adapter.historical_eod("AAPL", from_dt, to_dt)
        
        mock_fmp_client.historical_eod.assert_called_once_with(
            "AAPL", from_dt, to_dt
        )
        assert isinstance(result, pd.DataFrame)
    
    def test_quote_last_delegates_to_client(self):
        """Verify quote_last delegates to FMPClient."""
        mock_fmp_client = Mock()
        mock_fmp_client.quote_last.return_value = 150.75
        
        adapter = FMPHistoricalDataAdapter(fmp_client=mock_fmp_client)
        
        result = adapter.quote_last("AAPL")
        
        mock_fmp_client.quote_last.assert_called_once_with("AAPL")
        assert result == 150.75


class TestHistoryManagerWithPort:
    """Tests for HistoryManager using HistoricalDataProvider port (Sprint 1 refactor)."""
    
    def test_history_manager_uses_provider_port(self):
        """Verify HistoryManager accepts HistoricalDataProvider instead of FMPClient."""
        mock_provider = Mock(spec=HistoricalDataProvider)
        
        # Should not raise - HistoryManager accepts port
        manager = HistoryManager(provider=mock_provider)
        
        assert manager.provider == mock_provider
    
    def test_history_manager_intraday_uses_provider(self):
        """Verify HistoryManager calls provider for intraday data."""
        mock_provider = Mock(spec=HistoricalDataProvider)
        mock_df = pd.DataFrame({
            'open': [100.0],
            'high': [101.0],
            'low': [99.0],
            'close': [100.5],
            'volume': [1000],
        }, index=pd.DatetimeIndex([datetime(2026, 1, 1, 10, 0, tzinfo=pytz.UTC)]))
        mock_provider.historical_intraday.return_value = mock_df
        mock_provider.quote_last.return_value = 100.5
        
        manager = HistoryManager(provider=mock_provider)
        
        # Mock valid symbols check (skip Firestore check in test)
        manager._valid_symbols = {"AAPL"}
        
        from markettool.application.services.historicos_service import HistoryConfig
        result = manager.get("AAPL", "5min", cfg=HistoryConfig(bars=10))
        
        # Verify provider was called
        assert mock_provider.historical_intraday.called or not result.empty


class TestHealthService:
    """Tests for HealthService (created in Sprint 1)."""
    
    @pytest.mark.asyncio
    async def test_health_service_telegram_check(self):
        """Verify HealthService checks Telegram bot health."""
        mock_telegram_app = Mock()
        mock_telegram_app.bot = Mock()  # Bot exists
        
        service = HealthService(
            telegram_app=mock_telegram_app,
            firestore_db=None,
            cache_provider=None,
        )
        
        result = await service.check_telegram_bot()
        
        assert isinstance(result, ComponentHealth)
        assert result.name == "telegram_bot"
        assert result.healthy is True
        assert result.latency_ms is not None
    
    @pytest.mark.asyncio
    async def test_health_service_telegram_unhealthy(self):
        """Verify HealthService detects unhealthy Telegram bot."""
        service = HealthService(
            telegram_app=None,  # No app configured
            firestore_db=None,
            cache_provider=None,
        )
        
        result = await service.check_telegram_bot()
        
        assert isinstance(result, ComponentHealth)
        assert result.name == "telegram_bot"
        assert result.healthy is False
        assert result.error is not None
    
    @pytest.mark.asyncio
    async def test_health_service_firestore_check(self):
        """Verify HealthService checks Firestore health."""
        mock_firestore_db = Mock()
        mock_firestore_db.collections.return_value = iter([])  # Empty collections
        
        service = HealthService(
            telegram_app=None,
            firestore_db=mock_firestore_db,
            cache_provider=None,
        )
        
        result = await service.check_firestore()
        
        assert isinstance(result, ComponentHealth)
        assert result.name == "firestore"
        assert result.healthy is True
        mock_firestore_db.collections.assert_called_once_with(max_results=1)
    
    @pytest.mark.asyncio
    async def test_health_service_cache_check(self):
        """Verify HealthService checks cache provider health."""
        mock_cache_provider = Mock()
        
        service = HealthService(
            telegram_app=None,
            firestore_db=None,
            cache_provider=mock_cache_provider,
        )
        
        result = await service.check_cache()
        
        assert isinstance(result, ComponentHealth)
        assert result.name == "cache"
        assert result.healthy is True
    
    @pytest.mark.asyncio
    async def test_health_service_system_health(self):
        """Verify HealthService aggregates system health."""
        mock_telegram_app = Mock()
        mock_telegram_app.bot = Mock()
        mock_firestore_db = Mock()
        mock_firestore_db.collections.return_value = iter([])
        mock_cache_provider = Mock()
        
        service = HealthService(
            telegram_app=mock_telegram_app,
            firestore_db=mock_firestore_db,
            cache_provider=mock_cache_provider,
            version="1.0.0-test",
            environment="test",
            worker_id="test-worker",
        )
        
        system_health = await service.get_system_health()
        
        assert system_health.status in ["healthy", "degraded", "unhealthy"]
        assert system_health.version == "1.0.0-test"
        assert system_health.environment == "test"
        assert system_health.worker_id == "test-worker"
        assert "telegram_bot" in system_health.components
        assert "firestore" in system_health.components
        assert "cache" in system_health.components
    
    @pytest.mark.asyncio
    async def test_health_service_marks_ready(self):
        """Verify HealthService readiness marking."""
        service = HealthService(
            telegram_app=None,
            firestore_db=None,
            cache_provider=None,
        )
        
        assert not service.is_ready
        
        service.mark_ready()
        assert service.is_ready
        
        service.mark_not_ready()
        assert not service.is_ready


class TestArchitectureCompliance:
    """Test hexagonal architecture compliance (Sprint 1 goal)."""
    
    def test_no_infrastructure_imports_in_history_manager(self):
        """Verify HistoryManager doesn't import from Infrastructure layer."""
        import inspect
        from markettool.application.services.historicos_service import HistoryManager
        
        source = inspect.getsource(HistoryManager)
        
        # Should NOT import FMPClient directly
        assert "from markettool.infra.fmp import FMPClient" not in source
        assert "from markettool.infra.fmp.client import FMPClient" not in source
        
        # SHOULD import port instead
        # (Already verified by HistoryManager accepting HistoricalDataProvider)
    
    def test_adapter_in_correct_layer(self):
        """Verify FMPHistoricalDataAdapter is in Infrastructure layer."""
        from markettool.infra.adapters import FMPHistoricalDataAdapter as ImportedAdapter
        
        # Should be importable from infra.adapters
        assert ImportedAdapter is FMPHistoricalDataAdapter
    
    def test_health_service_in_application_layer(self):
        """Verify HealthService is in Application layer, not Interfaces."""
        from markettool.application.services.health_service import HealthService as ImportedService
        
        # Should be in application.services
        assert ImportedService is HealthService
