"""FMP adapter for HistoricalDataProvider port."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

import pandas as pd

from markettool.core.ports.historical_data_provider import HistoricalDataProvider
from markettool.core.errors import PlanNotAllowed
from markettool.infra.fmp.client import FMPClient, FMPPlanNotAllowed


class FMPHistoricalDataAdapter(HistoricalDataProvider):
    """
    Adapter that wraps FMPClient to implement HistoricalDataProvider port.
    
    This allows the Application layer (HistoryManager) to depend only on the port,
    not on the concrete FMP implementation.
    """
    
    def __init__(self, fmp_client: FMPClient):
        """
        Initialize adapter with FMP client.
        
        Args:
            fmp_client: Configured FMPClient instance
        """
        self._client = fmp_client
    
    def historical_intraday(
        self,
        symbol: str,
        timeframe: str,
        from_dt: datetime,
        to_dt: datetime,
    ) -> pd.DataFrame:
        """
        Fetch intraday data via FMP API.
        
        Raises:
            PlanNotAllowed: If plan doesn't support intraday
        """
        try:
            return self._client.historical_intraday(symbol, timeframe, from_dt, to_dt)
        except FMPPlanNotAllowed as e:
            raise PlanNotAllowed(str(e)) from e
    
    def historical_eod(
        self,
        symbol: str,
        from_dt: datetime,
        to_dt: datetime,
    ) -> pd.DataFrame:
        """Fetch EOD data via FMP API."""
        return self._client.historical_eod(symbol, from_dt, to_dt)
    
    def quote_last(self, symbol: str) -> Optional[float]:
        """Get last quote via FMP API."""
        return self._client.quote_last(symbol)
