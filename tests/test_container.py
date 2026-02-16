"""Unit tests for dependency container and routes."""

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock

from markettool.interfaces.containers import DIContainer
from markettool.infra.repositories import (
    FirestoreHistoricosRepository,
    FMPQuoteProvider,
    MultiLayerCacheProvider,
    TelegramNotifier,
)


class TestDIContainer(unittest.TestCase):
    """Tests for dependency injection container."""
    
    def setUp(self):
        """Setup mock adapters."""
        self.firestore_mock = MagicMock()
        self.fmp_mock = MagicMock()
        self.memory_cache_mock = AsyncMock()
        self.telegram_mock = MagicMock()
        
        # Create adapters
        self.historicos_repo = FirestoreHistoricosRepository(
            firestore_client=self.firestore_mock,
            fmp_client=self.fmp_mock,
        )
        
        self.quote_provider = FMPQuoteProvider(
            fmp_client=self.fmp_mock,
        )
        
        self.cache_provider = MultiLayerCacheProvider(
            memory_cache=self.memory_cache_mock,
        )
        
        self.notifier = TelegramNotifier(
            telegram_app=self.telegram_mock,
            chat_id='12345',
        )
        
        # Create container
        self.container = DIContainer(
            historicos_repo=self.historicos_repo,
            quote_provider=self.quote_provider,
            cache_provider=self.cache_provider,
            notifier=self.notifier,
        )
    
    def test_container_provides_get_historicos(self):
        """Test container provides GetHistoricosUseCase."""
        uc = self.container.get_historicos
        self.assertIsNotNone(uc)
        self.assertEqual(uc.__class__.__name__, 'GetHistoricosUseCase')
    
    def test_container_provides_get_quote(self):
        """Test container provides GetQuoteUseCase."""
        uc = self.container.get_quote
        self.assertIsNotNone(uc)
        self.assertEqual(uc.__class__.__name__, 'GetQuoteUseCase')
    
    def test_container_provides_run_analysis(self):
        """Test container provides RunAnalysisUseCase."""
        uc = self.container.run_analysis
        self.assertIsNotNone(uc)
        self.assertEqual(uc.__class__.__name__, 'RunAnalysisUseCase')
    
    def test_container_provides_warm_cache(self):
        """Test container provides WarmCacheUseCase."""
        uc = self.container.warm_cache
        self.assertIsNotNone(uc)
        self.assertEqual(uc.__class__.__name__, 'WarmCacheUseCase')
    
    def test_use_cases_are_cached(self):
        """Test that use case instances are cached."""
        uc1 = self.container.get_historicos
        uc2 = self.container.get_historicos
        
        self.assertIs(uc1, uc2)
    
    def test_get_all_returns_dict(self):
        """Test getting all use cases as dict."""
        all_uc = self.container.get_all()
        
        self.assertIsInstance(all_uc, dict)
        self.assertEqual(len(all_uc), 4)
        self.assertIn('get_historicos', all_uc)
        self.assertIn('get_quote', all_uc)
        self.assertIn('run_analysis', all_uc)
        self.assertIn('warm_cache', all_uc)
    
    def test_container_with_different_dependencies(self):
        """Test creating container with different dependencies."""
        # Create new adapters
        different_quote_provider = FMPQuoteProvider(fmp_client=MagicMock())
        
        # Create container with different dependency
        container2 = DIContainer(
            historicos_repo=self.historicos_repo,
            quote_provider=different_quote_provider,
            cache_provider=self.cache_provider,
            notifier=self.notifier,
        )
        
        # Verify it uses the different provider
        self.assertIs(container2.quote_provider, different_quote_provider)


class TestContainerWiring(unittest.TestCase):
    """Test that container wires dependencies correctly."""
    
    def setUp(self):
        """Setup container with mocks."""
        self.firestore_mock = MagicMock()
        self.fmp_mock = MagicMock()
        self.memory_cache_mock = AsyncMock()
        self.telegram_mock = MagicMock()
        
        self.historicos_repo = FirestoreHistoricosRepository(
            firestore_client=self.firestore_mock,
            fmp_client=self.fmp_mock,
        )
        
        self.quote_provider = FMPQuoteProvider(fmp_client=self.fmp_mock)
        self.cache_provider = MultiLayerCacheProvider(
            memory_cache=self.memory_cache_mock
        )
        self.notifier = TelegramNotifier(telegram_app=self.telegram_mock)
        
        self.container = DIContainer(
            historicos_repo=self.historicos_repo,
            quote_provider=self.quote_provider,
            cache_provider=self.cache_provider,
            notifier=self.notifier,
        )
    
    def test_use_case_has_repo(self):
        """Test that GetHistoricosUseCase has repository."""
        uc = self.container.get_historicos
        self.assertIsNotNone(uc.historicos_repo)
    
    def test_use_case_has_cache(self):
        """Test that GetHistoricosUseCase has cache."""
        uc = self.container.get_historicos
        self.assertIsNotNone(uc.cache_provider)
    
    def test_quote_use_case_has_provider(self):
        """Test that GetQuoteUseCase has quote provider."""
        uc = self.container.get_quote
        self.assertIsNotNone(uc.primary_provider)
    
    def test_analysis_use_case_has_cache(self):
        """Test that RunAnalysisUseCase has cache."""
        uc = self.container.run_analysis
        self.assertIsNotNone(uc.cache_provider)


if __name__ == '__main__':
    unittest.main()
