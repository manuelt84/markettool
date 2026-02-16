"""Use case: Get historical data."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from markettool.core.models.historico import Historico
from markettool.core.ports.cache_provider import CacheProvider
from markettool.core.ports.historicos_repo import HistoricosRepository
from markettool.core.errors import InsufficientDataError, DataNotFoundError


class GetHistoricosUseCase:
    """
    Orchestrates fetching historical data with caching and fallbacks.
    """
    
    def __init__(
        self,
        historicos_repo: HistoricosRepository,
        cache_provider: CacheProvider,
        logger: Optional[logging.Logger] = None,
    ):
        self.repo = historicos_repo
        self.cache = cache_provider
        self.logger = logger or logging.getLogger(__name__)
    
    async def execute(
        self,
        symbol: str,
        timeframe: str,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        use_cache: bool = True,
        cache_ttl_seconds: int = 3600,
    ) -> Historico:
        """
        Get historical data with caching.
        
        Args:
            symbol: Trading symbol
            timeframe: Timeframe code
            start_date: Optional start date
            end_date: Optional end date
            use_cache: Whether to use cache
            cache_ttl_seconds: Cache TTL in seconds
            
        Returns:
            Historico with loaded data
            
        Raises:
            DataNotFoundError: If data not found
            InsufficientDataError: If data is insufficient
        """
        cache_key = f"historicos:{symbol}:{timeframe}"
        
        # Try cache first
        if use_cache:
            cached = await self.cache.get_historico(symbol, timeframe)
            if cached:
                self.logger.debug(f"Cache hit for {symbol}/{timeframe}")
                return cached
        
        # Fetch from repository
        self.logger.info(f"Fetching {symbol}/{timeframe} from repository")
        historico = await self.repo.get_historico(
            symbol=symbol,
            timeframe=timeframe,
            start_date=start_date,
            end_date=end_date,
        )
        
        if historico.is_empty:
            raise InsufficientDataError(f"No data found for {symbol}/{timeframe}")
        
        # Validate minimum data points
        min_candles = self._get_min_candles_for_timeframe(timeframe)
        if historico.length < min_candles:
            self.logger.warning(
                f"Insufficient data for {symbol}/{timeframe}: "
                f"{historico.length} candles (minimum {min_candles})"
            )
            raise InsufficientDataError(
                f"Only {historico.length} candles available, need at least {min_candles}"
            )
        
        # Cache the result
        if use_cache:
            await self.cache.set_historico(historico, ttl_seconds=cache_ttl_seconds)
            self.logger.debug(f"Cached {symbol}/{timeframe} for {cache_ttl_seconds}s")
        
        return historico
    
    async def execute_with_resample(
        self,
        symbol: str,
        source_timeframe: str,
        target_timeframe: str,
        days_back: int = 30,
    ) -> Historico:
        """
        Get historicals and resample to target timeframe.
        
        Args:
            symbol: Trading symbol
            source_timeframe: Source timeframe code
            target_timeframe: Target timeframe for resampling
            days_back: Days of history to fetch
            
        Returns:
            Resampled Historico
        """
        start_date = datetime.now(timezone.utc) - timedelta(days=days_back)
        
        historico = await self.execute(
            symbol=symbol,
            timeframe=source_timeframe,
            start_date=start_date,
        )
        
        if source_timeframe != target_timeframe:
            self.logger.info(f"Resampling {symbol} from {source_timeframe} to {target_timeframe}")
            historico = historico.resample(target_timeframe)
        
        return historico
    
    @staticmethod
    def _get_min_candles_for_timeframe(timeframe: str) -> int:
        """Minimum candles required for analysis."""
        # Shorter timeframes need more candles
        minimums = {
            "1min": 100,
            "5min": 50,
            "15min": 30,
            "30min": 20,
            "1hour": 20,
            "4hour": 10,
            "1day": 5,
            "1week": 5,
        }
        return minimums.get(timeframe, 20)
