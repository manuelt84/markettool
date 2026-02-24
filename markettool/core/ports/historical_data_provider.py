"""Port for fetching historical OHLCV data (sync interface)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Optional

import pandas as pd


class HistoricalDataProvider(ABC):
    """
    Port for fetching historical market data.
    
    This is a sync interface that abstracts external data sources (FMP, Yahoo, etc.)
    for use in the HistoryManager service.
    
    Unlike HistoricosRepository (which is async and domain-focused),
    this port provides low-level access to raw historical data.
    """
    
    @abstractmethod
    def historical_intraday(
        self,
        symbol: str,
        timeframe: str,
        from_dt: datetime,
        to_dt: datetime,
    ) -> pd.DataFrame:
        """
        Fetch intraday historical data (1min, 5min, 15min, 30min, 1hour, 4hour).
        
        Args:
            symbol: Trading symbol
            timeframe: Intraday timeframe (1min, 5min, 15min, 30min, 1hour, 4hour)
            from_dt: Start datetime (UTC)
            to_dt: End datetime (UTC)
            
        Returns:
            DataFrame with columns [open, high, low, close, volume] and DatetimeIndex
            
        Raises:
            PlanNotAllowed: If plan doesn't support intraday data
            ExternalAPIError: If API call fails
        """
        pass
    
    @abstractmethod
    def historical_eod(
        self,
        symbol: str,
        from_dt: datetime,
        to_dt: datetime,
    ) -> pd.DataFrame:
        """
        Fetch end-of-day historical data (daily, weekly, monthly).
        
        Args:
            symbol: Trading symbol
            from_dt: Start datetime (UTC)
            to_dt: End datetime (UTC)
            
        Returns:
            DataFrame with columns [open, high, low, close, volume] and DatetimeIndex
            
        Raises:
            ExternalAPIError: If API call fails
        """
        pass
    
    @abstractmethod
    def quote_last(self, symbol: str) -> Optional[float]:
        """
        Get last traded price for symbol (for realtime bar updates).
        
        Args:
            symbol: Trading symbol
            
        Returns:
            Last price or None if unavailable
        """
        pass
