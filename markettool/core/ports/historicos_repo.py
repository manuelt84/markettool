"""Repository port for historical data."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Optional

from ..models.historico import Historico


class HistoricosRepository(ABC):
    """
    Port for accessing historical OHLCV data.
    Implementations can use local files, databases, APIs, etc.
    """
    
    @abstractmethod
    async def get_historico(
        self,
        symbol: str,
        timeframe: str,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
    ) -> Historico:
        """
        Fetch historical data for symbol and timeframe.
        
        Args:
            symbol: Trading symbol
            timeframe: Timeframe code
            start_date: Optional start date
            end_date: Optional end date
            
        Returns:
            Historico with loaded data
            
        Raises:
            DataNotFoundError: If data not found
            DataValidationError: If data is invalid
        """
        pass
    
    @abstractmethod
    async def save_historico(self, historico: Historico) -> None:
        """
        Save historical data.
        
        Args:
            historico: Historico to save
            
        Raises:
            StorageError: If save fails
        """
        pass
    
    @abstractmethod
    async def delete_historico(self, symbol: str, timeframe: str) -> None:
        """
        Delete historical data.
        
        Args:
            symbol: Trading symbol
            timeframe: Timeframe code
        """
        pass
    
    @abstractmethod
    async def exists(self, symbol: str, timeframe: str) -> bool:
        """Check if historical data exists."""
        pass
    
    @abstractmethod
    async def get_last_update(self, symbol: str, timeframe: str) -> Optional[datetime]:
        """Get timestamp of last update."""
        pass
