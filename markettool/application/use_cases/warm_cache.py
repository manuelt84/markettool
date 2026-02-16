"""Use case: Warm cache with data."""

from __future__ import annotations

import asyncio
import logging
from typing import List, Optional, Set

from markettool.core.ports.cache_provider import CacheProvider
from markettool.core.ports.historicos_repo import HistoricosRepository
from markettool.core.errors import DataNotFoundError


class WarmCacheUseCase:
    """
    Orchestrates pre-loading cache with frequently used data.
    """
    
    def __init__(
        self,
        cache_provider: CacheProvider,
        historicos_repo: HistoricosRepository,
        logger: Optional[logging.Logger] = None,
    ):
        self.cache = cache_provider
        self.repo = historicos_repo
        self.logger = logger or logging.getLogger(__name__)
    
    async def execute(
        self,
        symbols: Set[str],
        timeframes: List[str] = None,
        force: bool = False,
    ) -> dict:
        """
        Warm cache for given symbols and timeframes.
        
        Args:
            symbols: Set of trading symbols
            timeframes: List of timeframes (default common ones)
            force: Force reload even if cached
            
        Returns:
            Statistics dict with success/failure counts
        """
        timeframes = timeframes or ["1hour", "1day"]
        
        self.logger.info(
            f"Starting cache warmup: {len(symbols)} symbols × {len(timeframes)} timeframes"
        )
        
        stats = {
            "total_tasks": len(symbols) * len(timeframes),
            "succeeded": 0,
            "failed": 0,
            "skipped": 0,
            "errors": [],
        }
        
        tasks = []
        for symbol in symbols:
            for timeframe in timeframes:
                task = self._warm_one(
                    symbol=symbol,
                    timeframe=timeframe,
                    force=force,
                    stats=stats,
                )
                tasks.append(task)
        
        # Run with concurrency limit
        await asyncio.gather(*tasks, return_exceptions=True)
        
        self.logger.info(
            f"Cache warmup complete: {stats['succeeded']} ok, "
            f"{stats['failed']} failed, {stats['skipped']} skipped"
        )
        
        return stats
    
    async def _warm_one(
        self,
        symbol: str,
        timeframe: str,
        force: bool,
        stats: dict,
    ) -> None:
        """Warm cache for one symbol/timeframe."""
        try:
            cache_key = f"{symbol}:{timeframe}"
            
            # Check if already cached
            if not force:
                exists = await self.cache.exists(f"historicos:{cache_key}")
                if exists:
                    stats["skipped"] += 1
                    return
            
            # Fetch and cache
            historico = await self.repo.get_historico(
                symbol=symbol,
                timeframe=timeframe,
            )
            
            if historico.is_empty:
                self.logger.warning(f"No data for {cache_key}")
                stats["failed"] += 1
                return
            
            await self.cache.set_historico(historico)
            stats["succeeded"] += 1
            self.logger.debug(f"Cached {cache_key}")
        
        except Exception as e:
            stats["failed"] += 1
            error_msg = f"Failed to warm {symbol}/{timeframe}: {e}"
            stats["errors"].append(error_msg)
            self.logger.error(error_msg)
    
    async def execute_full_warmup(self) -> dict:
        """
        Do a complete warmup with default symbols and timeframes.
        Override this with your specific list of important symbols.
        """
        # Default important symbols - should be configured
        symbols = {
            "AAPL", "GOOGL", "MSFT", "TSLA",  # Tech
            "EURUSD", "GBPUSD", "JPYUSD",      # Forex
            "GOLD", "CRUDE",                    # Commodities
        }
        
        timeframes = ["1hour", "4hour", "1day"]
        
        return await self.execute(
            symbols=symbols,
            timeframes=timeframes,
            force=False,
        )
    
    async def get_cache_stats(self) -> dict:
        """Get current cache statistics."""
        return await self.cache.get_cache_stats()
