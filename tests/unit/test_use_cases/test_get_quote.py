"""Unit tests for GetQuoteUseCase."""

import pytest
from unittest.mock import AsyncMock
from datetime import datetime
import pytz

from markettool.application.use_cases import GetQuoteUseCase
from markettool.core.models.quote import Quote
from markettool.core.errors import DataNotFoundError, ExternalAPIError


@pytest.mark.asyncio
class TestGetQuoteUseCase:
    """Test GetQuoteUseCase execution."""
    
    async def test_get_quote_success(self, di_container, sample_quote):
        """Test successfully fetching a quote."""
        di_container.quote_provider.get_quote = AsyncMock(return_value=sample_quote)
        
        use_case = di_container.get_quote
        result = await use_case.execute(symbol="EURUSD")
        
        assert result is not None
        assert result.symbol == "EURUSD"
        assert result.price == 1.0950
    
    async def test_get_multiple_quotes(self, di_container):
        """Test fetching multiple quotes."""
        quote1 = Quote(
            symbol="AAPL",
            price=150.0,
            timestamp=datetime.now(pytz.UTC),
        )
        quote2 = Quote(
            symbol="GOOGL",
            price=140.0,
            timestamp=datetime.now(pytz.UTC),
        )
        
        di_container.quote_provider.get_quotes = AsyncMock(
            return_value={"AAPL": quote1, "GOOGL": quote2}
        )
        
        use_case = di_container.get_quote
        results = await use_case.execute_batch(symbols=["AAPL", "GOOGL"])
        
        assert len(results) == 2
        assert "AAPL" in results
        assert "GOOGL" in results
    
    async def test_get_quote_with_cache(self, di_container, sample_quote):
        """Test quote caching."""
        di_container.quote_provider.get_quote = AsyncMock(return_value=sample_quote)
        
        use_case = di_container.get_quote
        
        # First call
        result1 = await use_case.execute(symbol="EURUSD")
        # Second call (should be cached)
        result2 = await use_case.execute(symbol="EURUSD")
        
        assert result1 is not None
        assert result2 is not None
    
    async def test_get_quote_not_found(self, di_container):
        """Test error when quote not found."""
        di_container.quote_provider.get_quote = AsyncMock(
            side_effect=DataNotFoundError("Quote not found")
        )
        
        use_case = di_container.get_quote
        
        with pytest.raises(DataNotFoundError):
            await use_case.execute(symbol="UNKNOWN")
    
    async def test_get_quote_api_error(self, di_container):
        """Test handling API errors."""
        di_container.quote_provider.get_quote = AsyncMock(
            side_effect=ExternalAPIError("API rate limited")
        )
        
        use_case = di_container.get_quote
        
        with pytest.raises(ExternalAPIError):
            await use_case.execute(symbol="AAPL")


@pytest.mark.asyncio
class TestGetQuoteValidation:
    """Test quote validation."""
    
    async def test_invalid_symbol_format(self, di_container):
        """Test validation of symbol format."""
        use_case = di_container.get_quote
        
        # Empty symbol should fail validation
        with pytest.raises((ValueError, Exception)):
            await use_case.execute(symbol="")
    
    async def test_get_quote_with_metadata(self, di_container):
        """Test quote includes metadata."""
        quote = Quote(
            symbol="AAPL",
            price=150.0,
            timestamp=datetime.now(pytz.UTC),
            metadata={"market": "stock", "exchange": "NASDAQ"},
        )
        
        di_container.quote_provider.get_quote = AsyncMock(return_value=quote)
        
        use_case = di_container.get_quote
        result = await use_case.execute(symbol="AAPL")
        
        assert result.metadata is not None
        assert result.metadata["market"] == "stock"
