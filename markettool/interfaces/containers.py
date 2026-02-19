"""Dependency container for use cases and services."""

from __future__ import annotations

import logging
import os
from typing import Optional, Any

from markettool.interfaces.legacy_services import LegacyServices

from markettool.application.use_cases import (
    GetHistoricosUseCase,
    GetQuoteUseCase,
    RunAnalysisUseCase,
    WarmCacheUseCase,
)
from markettool.core.ports import (
    HistoricosRepository,
    QuoteProvider,
    CacheProvider,
    Notifier,
)
from markettool.infra.repositories import (
    FirestoreHistoricosRepository,
    FMPQuoteProvider,
    MultiLayerCacheProvider,
    TelegramNotifier,
)
from markettool.infra.cache.memory_cache import MemoryCache
from markettool.infra.cache.local_cache import LocalCache
from markettool.infra.cache.gcs_cache import GCSCache


class DIContainer:
    """
    Dependency Injection Container.
    Manages creation and lifecycle of use cases and port adapters.
    """
    
    def __init__(
        self,
        historicos_repo: HistoricosRepository,
        quote_provider: QuoteProvider,
        cache_provider: CacheProvider,
        notifier: Notifier,
        legacy_services: Optional[LegacyServices] = None,
        logger: Optional[logging.Logger] = None,
    ):
        """
        Initialize container with port implementations.
        
        Args:
            historicos_repo: Historical data repository
            quote_provider: Quote provider
            cache_provider: Cache implementation
            notifier: Notification service
            logger: Optional logger
        """
        self.historicos_repo = historicos_repo
        self.quote_provider = quote_provider
        self.cache_provider = cache_provider
        self.notifier = notifier
        self.legacy_services = legacy_services
        self.logger = logger or logging.getLogger(__name__)
        
        # Cache use case instances
        self._get_historicos_uc: Optional[GetHistoricosUseCase] = None
        self._get_quote_uc: Optional[GetQuoteUseCase] = None
        self._run_analysis_uc: Optional[RunAnalysisUseCase] = None
        self._warm_cache_uc: Optional[WarmCacheUseCase] = None
    
    @property
    def get_historicos(self) -> GetHistoricosUseCase:
        """Get GetHistoricosUseCase instance."""
        if self._get_historicos_uc is None:
            self._get_historicos_uc = GetHistoricosUseCase(
                historicos_repo=self.historicos_repo,
                cache_provider=self.cache_provider,
                logger=self.logger,
            )
        return self._get_historicos_uc
    
    @property
    def get_quote(self) -> GetQuoteUseCase:
        """Get GetQuoteUseCase instance."""
        if self._get_quote_uc is None:
            self._get_quote_uc = GetQuoteUseCase(
                primary_provider=self.quote_provider,
                logger=self.logger,
            )
        return self._get_quote_uc
    
    @property
    def run_analysis(self) -> RunAnalysisUseCase:
        """Get RunAnalysisUseCase instance."""
        if self._run_analysis_uc is None:
            self._run_analysis_uc = RunAnalysisUseCase(
                cache_provider=self.cache_provider,
                logger=self.logger,
            )
        return self._run_analysis_uc
    
    @property
    def warm_cache(self) -> WarmCacheUseCase:
        """Get WarmCacheUseCase instance."""
        if self._warm_cache_uc is None:
            self._warm_cache_uc = WarmCacheUseCase(
                cache_provider=self.cache_provider,
                historicos_repo=self.historicos_repo,
                logger=self.logger,
            )
        return self._warm_cache_uc
    
    def get_all(self) -> dict:
        """
        Get dictionary of all use cases.
        Useful for dependency injection into routes.
        """
        return {
            "get_historicos": self.get_historicos,
            "get_quote": self.get_quote,
            "run_analysis": self.run_analysis,
            "warm_cache": self.warm_cache,
            "legacy_services": self.legacy_services,
        }
    
    @classmethod
    def create_default(
        cls,
        firestore_db: Optional[Any] = None,
        gcs_client: Optional[Any] = None,
        fmp_client: Optional[Any] = None,
        telegram_app: Optional[Any] = None,
        default_chat_id: Optional[str] = None,
        legacy_services: Optional[LegacyServices] = None,
        logger: Optional[logging.Logger] = None,
    ) -> DIContainer:
        """
        Create a container with all default implementations.
        
        Args:
            firestore_db: Firestore database client
            gcs_client: Google Cloud Storage client
            fmp_client: FMP API client
            telegram_app: Telegram application
            default_chat_id: Default Telegram chat ID
            logger: Optional logger
        
        Returns:
            Fully configured DIContainer
        """
        _logger = logger or logging.getLogger(__name__)
        
        # Create port adapters
        historicos_repo = FirestoreHistoricosRepository(
            firestore_client=firestore_db,
            fmp_client=fmp_client,
            logger=_logger,
        )
        
        quote_provider = FMPQuoteProvider(
            fmp_client=fmp_client,
            logger=_logger,
        )
        
        # Create cache layers (hexagonal architecture)
        _logger.info("[Hexagonal] Creating cache layers...")
        
        # Always create memory cache (fastest, no dependencies)
        memory_cache = MemoryCache(logger=_logger)
        _logger.info("✅ MemoryCache created")
        
        # Create local cache with configurable directory
        cache_dir = os.environ.get("CACHE_DIR", "./cache")
        local_cache = LocalCache(cache_dir=cache_dir, logger=_logger)
        _logger.info(f"✅ LocalCache created (dir: {cache_dir})")
        
        # Optionally create GCS cache if client available
        gcs_cache = None
        if gcs_client:
            bucket_name = os.environ.get("GCS_BUCKET_NAME", "markettool_bucket")
            gcs_cache = GCSCache(bucket_name=bucket_name, logger=_logger)
            _logger.info(f"✅ GCSCache created (bucket: {bucket_name})")
        
        # Create multi-layer cache provider with fallback chain
        cache_provider = MultiLayerCacheProvider(
            memory_cache=memory_cache,
            local_cache=local_cache,
            gcs_cache=gcs_cache,
            logger=_logger,
        )
        _logger.info("✅ MultiLayerCacheProvider created (Memory → Local → GCS)")
        
        notifier = TelegramNotifier(
            telegram_app=telegram_app,
            chat_id=default_chat_id,
            logger=_logger,
        )
        
        # Create and return container
        return cls(
            historicos_repo=historicos_repo,
            quote_provider=quote_provider,
            cache_provider=cache_provider,
            notifier=notifier,
            legacy_services=legacy_services,
            logger=_logger,
        )
