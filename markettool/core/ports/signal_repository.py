"""
Signal repository port.

Defines the interface for storing and retrieving trading signals.
This is a domain port that will be implemented by infrastructure adapters
(e.g., Firestore, PostgreSQL, etc.)
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import List, Optional

from ..models.signal import Signal


class SignalRepository(ABC):
    """
    Port for signal persistence.
    
    Implementation examples:
    - FirestoreSignalRepository: Store in Firestore
    - PostgresSignalRepository: Store in PostgreSQL
    - InMemorySignalRepository: Store in memory (testing)
    """
    
    @abstractmethod
    async def save(self, signal: Signal) -> None:
        """
        Save a single signal.
        
        Args:
            signal: Trading signal to persist
            
        Raises:
            StorageError: If save operation fails
        """
    
    @abstractmethod
    async def save_batch(self, signals: List[Signal]) -> int:
        """
        Save multiple signals in batch.
        
        Args:
            signals: List of trading signals to persist
            
        Returns:
            Number of signals successfully saved
            
        Raises:
            StorageError: If batch operation fails
        """
    
    @abstractmethod
    async def get_by_symbol(
        self,
        symbol: str,
        from_date: Optional[datetime] = None,
        to_date: Optional[datetime] = None,
    ) -> List[Signal]:
        """
        Get signals for a specific symbol.
        
        Args:
            symbol: Trading symbol (e.g., "AAPL")
            from_date: Start date filter (inclusive)
            to_date: End date filter (inclusive)
            
        Returns:
            List of signals matching criteria
            
        Raises:
            DataNotFoundError: If no signals found
            StorageError: If query fails
        """
    
    @abstractmethod
    async def get_latest(self, symbol: str, limit: int = 10) -> List[Signal]:
        """
        Get most recent signals for a symbol.
        
        Args:
            symbol: Trading symbol
            limit: Maximum number of signals to return
            
        Returns:
            List of most recent signals (newest first)
        """
    
    @abstractmethod
    async def delete_old(self, days: int) -> int:
        """
        Delete signals older than specified days.
        
        Args:
            days: Delete signals older than this many days
            
        Returns:
            Number of signals deleted
            
        Raises:
            StorageError: If deletion fails
        """
