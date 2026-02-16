"""Unit tests for GetHistoricosUseCase."""

import pytest
from unittest.mock import AsyncMock
from datetime import datetime
import pytz

from markettool.application.use_cases import GetHistoricosUseCase
from markettool.core.models.historico import Historico
from markettool.core.errors import DataNotFoundError, CacheError


@pytest.mark.asyncio
class TestGetHistoricosUseCase:
    """Test GetHistoricosUseCase execution."""
    
    async def test_get_historicos_success(self, di_container, sample_historico):
        """Test successfully fetching historicos."""
        # Mock the repository
        di_container.historicos_repo.get_historico = AsyncMock(
            return_value=sample_historico
        )
        
        # Get use case from container
        use_case = di_container.get_historicos
        
        # Execute
        result = await use_case.execute(symbol="AAPL", timeframe="1h")
        
        # Verify
        assert result is not None
        assert result.symbol == "AAPL"
        di_container.historicos_repo.get_historico.assert_called_once()
    
    async def test_get_historicos_from_cache(self, di_container, sample_historico):
        """Test fetching historicos from cache."""
        # Setup mock cache to return data
        di_container.cache_provider.get_historico = AsyncMock(
            return_value=sample_historico
        )
        di_container.historicos_repo.get_historico = AsyncMock()
        
        use_case = di_container.get_historicos
        result = await use_case.execute(symbol="AAPL", timeframe="1h")
        
        # Should get from cache, not call repository
        assert result is not None
        di_container.cache_provider.get_historico.assert_called_once()
    
    async def test_get_historicos_cache_miss_fetches_from_repo(
        self,
        di_container,
        sample_historico,
    ):
        """Test cache miss falls back to repository."""
        # Setup: cache returns None, repo returns data
        di_container.cache_provider.get_historico = AsyncMock(return_value=None)
        di_container.historicos_repo.get_historico = AsyncMock(
            return_value=sample_historico
        )
        di_container.cache_provider.set_historico = AsyncMock()
        
        use_case = di_container.get_historicos
        result = await use_case.execute(symbol="AAPL", timeframe="1h")
        
        # Should fetch from repo and cache it
        assert result is not None
        di_container.historicos_repo.get_historico.assert_called_once()
        di_container.cache_provider.set_historico.assert_called_once()
    
    async def test_get_historicos_not_found_error(self, di_container):
        """Test error when historicos not found."""
        # Setup: both cache and repo return None
        di_container.cache_provider.get_historico = AsyncMock(return_value=None)
        di_container.historicos_repo.get_historico = AsyncMock(return_value=None)
        
        use_case = di_container.get_historicos
        
        # Should raise DataNotFoundError
        with pytest.raises(DataNotFoundError):
            await use_case.execute(symbol="UNKNOWN", timeframe="1h")
    
    async def test_get_historicos_with_date_range(
        self,
        di_container,
        sample_historico,
    ):
        """Test fetching historicos with date range."""
        di_container.historicos_repo.get_historico = AsyncMock(
            return_value=sample_historico
        )
        di_container.cache_provider.get_historico = AsyncMock(return_value=None)
        
        use_case = di_container.get_historicos
        
        start_date = datetime.now(pytz.UTC)
        end_date = datetime.now(pytz.UTC)
        
        result = await use_case.execute(
            symbol="AAPL",
            timeframe="1h",
            start_date=start_date,
            end_date=end_date,
        )
        
        assert result is not None
        di_container.historicos_repo.get_historico.assert_called_once()


@pytest.mark.asyncio
class TestGetHistoricosErrorHandling:
    """Test error handling in GetHistoricosUseCase."""
    
    async def test_cache_error_continues(self, di_container, sample_historico):
        """Test that cache errors don't break the flow."""
        # Setup: cache raises error, repo succeeds
        di_container.cache_provider.get_historico = AsyncMock(
            side_effect=CacheError("Cache failed")
        )
        di_container.historicos_repo.get_historico = AsyncMock(
            return_value=sample_historico
        )
        
        use_case = di_container.get_historicos
        
        # Should still return data from repo despite cache error
        result = await use_case.execute(symbol="AAPL", timeframe="1h")
        assert result is not None
    
    async def test_repo_error_propagates(self, di_container):
        """Test that repository errors propagate."""
        di_container.cache_provider.get_historico = AsyncMock(return_value=None)
        di_container.historicos_repo.get_historico = AsyncMock(
            side_effect=Exception("Connection failed")
        )
        
        use_case = di_container.get_historicos
        
        # Should raise the error
        with pytest.raises(Exception):
            await use_case.execute(symbol="AAPL", timeframe="1h")
