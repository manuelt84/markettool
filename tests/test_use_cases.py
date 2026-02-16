"""Unit tests for use cases with mocked ports."""

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime
import pandas as pd
import pytz

from markettool.application.use_cases import (
    GetHistoricosUseCase,
    GetQuoteUseCase,
    RunAnalysisUseCase,
    WarmCacheUseCase,
)
from markettool.core.models.historico import Historico
from markettool.core.models.quote import Quote
from markettool.core.models.signal import Signal, SignalType
from markettool.core.errors import (
    DataNotFoundError,
    CacheError,
    AnalysisError,
)


class TestGetHistoricosUseCase(unittest.TestCase):
    """Tests for GetHistoricosUseCase."""
    
    def setUp(self):
        """Setup mocks."""
        self.mock_repo = AsyncMock()
        self.mock_cache = AsyncMock()
        self.use_case = GetHistoricosUseCase(
            historicos_repo=self.mock_repo,
            cache_provider=self.mock_cache,
        )
        
        # Mock data
        self.test_data = pd.DataFrame({
            'open': [100, 101],
            'high': [101, 102],
            'low': [99, 100],
            'close': [100.5, 101.5],
            'volume': [1000, 1100],
        }, index=pd.date_range('2024-01-01', periods=2, freq='D'))
        
        self.mock_historico = Historico(
            symbol='AAPL',
            timeframe='1d',
            data=self.test_data,
        )
    
    def test_get_historicos_from_cache(self):
        """Test getting historicos from cache."""
        # Setup cache hit
        self.mock_cache.get.return_value = self.mock_historico
        
        # Execute
        result = asyncio.run(
            self.use_case.execute(symbol='AAPL', timeframe='1d')
        )
        
        # Assert
        self.assertEqual(result.symbol, 'AAPL')
        self.mock_cache.get.assert_called_once()
        self.mock_repo.get_historicos.assert_not_called()
    
    def test_get_historicos_from_repo_on_cache_miss(self):
        """Test fetching from repo when cache misses."""
        # Setup cache miss, repo hit
        self.mock_cache.get.return_value = None
        self.mock_repo.get_historicos.return_value = self.mock_historico
        
        # Execute
        result = asyncio.run(
            self.use_case.execute(symbol='AAPL', timeframe='1d')
        )
        
        # Assert
        self.assertEqual(result.symbol, 'AAPL')
        self.mock_repo.get_historicos.assert_called_once()
        self.mock_cache.set.assert_called_once()
    
    def test_get_historicos_not_found(self):
        """Test error when historicos not found."""
        self.mock_cache.get.return_value = None
        self.mock_repo.get_historicos.side_effect = DataNotFoundError("Not found")
        
        with self.assertRaises(DataNotFoundError):
            asyncio.run(self.use_case.execute(symbol='UNKNOWN'))


class TestGetQuoteUseCase(unittest.TestCase):
    """Tests for GetQuoteUseCase."""
    
    def setUp(self):
        """Setup mocks."""
        self.mock_provider = AsyncMock()
        self.use_case = GetQuoteUseCase(primary_provider=self.mock_provider)
        
        self.mock_quote = Quote(
            symbol='AAPL',
            price=150.25,
            bid=150.23,
            ask=150.27,
        )
    
    def test_get_quote_success(self):
        """Test getting quote successfully."""
        self.mock_provider.get_quote.return_value = self.mock_quote
        
        result = asyncio.run(self.use_case.execute(symbol='AAPL'))
        
        self.assertEqual(result.symbol, 'AAPL')
        self.assertEqual(result.price, 150.25)
    
    def test_get_quote_not_found(self):
        """Test handling missing quote."""
        self.mock_provider.get_quote.side_effect = DataNotFoundError("Not found")
        
        with self.assertRaises(DataNotFoundError):
            asyncio.run(self.use_case.execute(symbol='UNKNOWN'))
    
    def test_get_multiple_quotes(self):
        """Test getting multiple quotes at once."""
        quotes = {
            'AAPL': self.mock_quote,
            'GOOGL': Quote(symbol='GOOGL', price=140.50),
        }
        self.mock_provider.get_quotes.return_value = quotes
        
        result = asyncio.run(
            self.use_case.execute_batch(symbols=['AAPL', 'GOOGL'])
        )
        
        self.assertEqual(len(result), 2)
        self.assertIn('AAPL', result)
        self.assertIn('GOOGL', result)


class TestRunAnalysisUseCase(unittest.TestCase):
    """Tests for RunAnalysisUseCase."""
    
    def setUp(self):
        """Setup mocks."""
        self.mock_cache = AsyncMock()
        self.use_case = RunAnalysisUseCase(cache_provider=self.mock_cache)
    
    def test_run_analysis_generates_signals(self):
        """Test that analysis generates signals."""
        result = asyncio.run(
            self.use_case.execute(symbol='AAPL', timeframe='1d')
        )
        
        self.assertIsNotNone(result)
        self.assertIn('signals', result)
        self.assertIn('timestamp', result)
    
    def test_run_analysis_caches_result(self):
        """Test that analysis result is cached."""
        asyncio.run(self.use_case.execute(symbol='AAPL'))
        
        self.mock_cache.set.assert_called()
    
    def test_run_analysis_error_handling(self):
        """Test error handling in analysis."""
        self.mock_cache.get.side_effect = CacheError("Cache error")
        
        # Should not raise, but log error
        result = asyncio.run(
            self.use_case.execute(symbol='AAPL')
        )
        self.assertIsNotNone(result)


class TestWarmCacheUseCase(unittest.TestCase):
    """Tests for WarmCacheUseCase."""
    
    def setUp(self):
        """Setup mocks."""
        self.mock_cache = AsyncMock()
        self.mock_repo = AsyncMock()
        self.use_case = WarmCacheUseCase(
            cache_provider=self.mock_cache,
            historicos_repo=self.mock_repo,
        )
    
    def test_warm_cache_batch(self):
        """Test warming cache with symbols."""
        symbols = ['AAPL', 'GOOGL', 'MSFT']
        
        result = asyncio.run(
            self.use_case.execute(symbols=symbols)
        )
        
        self.assertIsNotNone(result)
        self.assertIn('symbols_requested', result)
    
    def test_warm_cache_calls_warmup(self):
        """Test that warming calls cache.warmup."""
        asyncio.run(self.use_case.execute(symbols=['AAPL']))
        
        self.mock_cache.warm_cache.assert_called()
    
    def test_warm_cache_empty_symbols(self):
        """Test warming cache with no symbols."""
        result = asyncio.run(self.use_case.execute(symbols=[]))
        
        self.assertIsNotNone(result)
        self.assertEqual(result['symbols_requested'], 0)


if __name__ == '__main__':
    unittest.main()
