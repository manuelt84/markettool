"""
Firestore-based signal repository adapter.

Implements SignalRepository port using Google Cloud Firestore as the persistence layer.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from markettool.core.models.signal import Signal, SignalType
from markettool.core.ports.signal_repository import SignalRepository
from markettool.core.errors import DataNotFoundError, StorageError


class FirestoreSignalRepository(SignalRepository):
    """
    Signal repository using Firestore as backend.
    
    Collection structure:
        signals/ (collection)
            {signal_id}/ (document)
                - symbol: str
                - signal_type: str
                - timestamp: datetime
                - confidence: float
                - entry_price: float | null
                - target_price: float | null
                - stop_loss: float | null
                - reason: str | null
                - indicators: dict
                - risk_points: float | null
                - reward_points: float | null
                - source: str
                - analysis_type: str
    """
    
    def __init__(
        self,
        firestore_client,
        collection_name: str = "signals",
        logger: Optional[logging.Logger] = None,
    ):
        """
        Initialize Firestore signal repository.
        
        Args:
            firestore_client: Firestore database client
            collection_name: Name of Firestore collection
            logger: Optional logger
        """
        self.firestore = firestore_client
        self.collection_name = collection_name
        self.logger = logger or logging.getLogger(__name__)
    
    async def save(self, signal: Signal) -> None:
        """Save a single signal to Firestore."""
        try:
            doc_id = f"{signal.symbol}_{signal.timestamp.isoformat()}"
            data = signal.to_dict()
            
            # Convert datetime to ISO string for Firestore
            if isinstance(data.get("timestamp"), datetime):
                data["timestamp"] = data["timestamp"].isoformat()
            
            self.firestore.collection(self.collection_name).document(doc_id).set(data)
            
            self.logger.debug(f"Saved signal for {signal.symbol}: {signal.signal_type}")
        
        except Exception as exc:
            self.logger.error(f"Failed to save signal {signal.symbol}: {exc}")
            raise StorageError(f"Could not save signal: {exc}")
    
    async def save_batch(self, signals: List[Signal]) -> int:
        """Save multiple signals in batch operation."""
        if not signals:
            return 0
        
        try:
            batch = self.firestore.batch()
            collection_ref = self.firestore.collection(self.collection_name)
            
            saved_count = 0
            for signal in signals:
                doc_id = f"{signal.symbol}_{signal.timestamp.isoformat()}"
                doc_ref = collection_ref.document(doc_id)
                
                data = signal.to_dict()
                
                # Convert datetime to ISO string
                if isinstance(data.get("timestamp"), datetime):
                    data["timestamp"] = data["timestamp"].isoformat()
                
                batch.set(doc_ref, data)
                saved_count += 1
                
                # Firestore has a limit of 500 operations per batch
                if saved_count % 500 == 0:
                    batch.commit()
                    batch = self.firestore.batch()
            
            # Commit remaining
            if saved_count % 500 != 0:
                batch.commit()
            
            self.logger.info(f"Saved {saved_count} signals to Firestore")
            return saved_count
        
        except Exception as exc:
            self.logger.error(f"Failed to save batch of signals: {exc}")
            raise StorageError(f"Batch save failed: {exc}")
    
    async def get_by_symbol(
        self,
        symbol: str,
        from_date: Optional[datetime] = None,
        to_date: Optional[datetime] = None,
    ) -> List[Signal]:
        """Get signals for a specific symbol with optional date range."""
        try:
            query = self.firestore.collection(self.collection_name).where("symbol", "==", symbol)
            
            # Apply date filters if provided
            if from_date:
                query = query.where("timestamp", ">=", from_date.isoformat())
            if to_date:
                query = query.where("timestamp", "<=", to_date.isoformat())
            
            # Order by timestamp descending
            query = query.order_by("timestamp", direction="DESCENDING")
            
            docs = query.stream()
            
            signals = []
            for doc in docs:
                data = doc.to_dict()
                signal = self._dict_to_signal(data)
                signals.append(signal)
            
            if not signals:
                self.logger.debug(f"No signals found for {symbol}")
                return []
            
            self.logger.debug(f"Found {len(signals)} signals for {symbol}")
            return signals
        
        except Exception as exc:
            self.logger.error(f"Failed to query signals for {symbol}: {exc}")
            raise StorageError(f"Query failed: {exc}")
    
    async def get_latest(self, symbol: str, limit: int = 10) -> List[Signal]:
        """Get most recent signals for a symbol."""
        try:
            query = (
                self.firestore.collection(self.collection_name)
                .where("symbol", "==", symbol)
                .order_by("timestamp", direction="DESCENDING")
                .limit(limit)
            )
            
            docs = query.stream()
            
            signals = []
            for doc in docs:
                data = doc.to_dict()
                signal = self._dict_to_signal(data)
                signals.append(signal)
            
            self.logger.debug(f"Found {len(signals)} latest signals for {symbol}")
            return signals
        
        except Exception as exc:
            self.logger.error(f"Failed to get latest signals for {symbol}: {exc}")
            return []
    
    async def delete_old(self, days: int) -> int:
        """Delete signals older than specified days."""
        try:
            cutoff_date = datetime.now(timezone.utc) - timedelta(days=days)
            
            query = (
                self.firestore.collection(self.collection_name)
                .where("timestamp", "<", cutoff_date.isoformat())
            )
            
            docs = query.stream()
            
            deleted_count = 0
            batch = self.firestore.batch()
            
            for doc in docs:
                batch.delete(doc.reference)
                deleted_count += 1
                
                # Commit in batches of 500
                if deleted_count % 500 == 0:
                    batch.commit()
                    batch = self.firestore.batch()
            
            # Commit remaining
            if deleted_count % 500 != 0:
                batch.commit()
            
            self.logger.info(f"Deleted {deleted_count} old signals (older than {days} days)")
            return deleted_count
        
        except Exception as exc:
            self.logger.error(f"Failed to delete old signals: {exc}")
            raise StorageError(f"Deletion failed: {exc}")
    
    def _dict_to_signal(self, data: dict) -> Signal:
        """Convert Firestore document dict to Signal model."""
        # Parse timestamp
        timestamp_str = data.get("timestamp")
        if isinstance(timestamp_str, str):
            timestamp = datetime.fromisoformat(timestamp_str)
        else:
            timestamp = datetime.now(timezone.utc)
        
        # Parse signal type
        signal_type_str = data.get("signal_type", "NEUTRAL")
        try:
            signal_type = SignalType(signal_type_str)
        except ValueError:
            signal_type = SignalType.NEUTRAL
        
        return Signal(
            symbol=data.get("symbol", ""),
            signal_type=signal_type,
            timestamp=timestamp,
            confidence=data.get("confidence", 0.5),
            entry_price=data.get("entry_price"),
            target_price=data.get("target_price"),
            stop_loss=data.get("stop_loss"),
            reason=data.get("reason"),
            indicators=data.get("indicators", {}),
            risk_points=data.get("risk_points"),
            reward_points=data.get("reward_points"),
            source=data.get("source", "unknown"),
            analysis_type=data.get("analysis_type", "technical"),
        )
