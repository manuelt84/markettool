"""
Entries Aggregation Service
============================
Consolidates, ranks, and filters calculated entries from all strategies.
Provides unified interface for entry retrieval and filtering.
"""

from typing import Dict, List, Optional, Any
from datetime import datetime, timedelta
from dataclasses import dataclass, asdict
from enum import Enum
import asyncio


class SortBy(Enum):
    """Sorting methods for entries"""
    SCORE = "score"
    RRR = "rrr"
    TIMESTAMP = "timestamp"
    SYMBOL = "symbol"


@dataclass
class EntryCandidateData:
    """
    Entry candidate data structure.
    Represents a single calculated entry with all metadata.
    """
    id: str
    symbol: str
    timeframe: str
    side: str  # 'long' or 'short'
    entry_price: float
    take_profit: float
    stop_loss: float
    rrr: float  # Risk/Reward Ratio
    confluence_score: float  # 0-100
    strategies: List[str]  # Which strategies triggered
    source: str  # Primary strategy that triggered
    created_at: str  # ISO datetime
    expires_at: Optional[str]  # When entry becomes invalid
    atr_multiplier: Optional[float] = None
    risk_percentage: Optional[float] = None
    metadata: Optional[Dict[str, Any]] = None


class EntriesAggregationService:
    """
    Service to manage and aggregate entry candidates.
    Handles:
    - Consolidation from multiple sources
    - Ranking and sorting
    - Filtering by criteria
    - Expiration management
    """

    def __init__(self, cache_ttl_seconds: int = 300):
        """
        Initialize aggregation service.
        
        Args:
            cache_ttl_seconds: Cache time-to-live in seconds (default 5min)
        """
        self.cache_ttl = timedelta(seconds=cache_ttl_seconds)
        self._entries_cache: Dict[str, EntryCandidateData] = {}
        self._last_cache_update: Optional[datetime] = None

    async def get_all_entries(
        self,
        limit: int = 100,
        sort_by: str = "score",
        skip_expired: bool = True
    ) -> List[Dict[str, Any]]:
        """
        Get all entries, ranked and filtered.
        
        Args:
            limit: Max entries to return
            sort_by: Sort key ('score', 'rrr', 'timestamp')
            skip_expired: Exclude expired entries
            
        Returns:
            List of entry dicts, ranked by sort_by
        """
        entries = list(self._entries_cache.values())
        
        # Filter expired
        if skip_expired:
            now = datetime.utcnow()
            entries = [
                e for e in entries
                if not e.expires_at or datetime.fromisoformat(e.expires_at) > now
            ]
        
        # Sort
        entries = self._sort_entries(entries, sort_by)
        
        # Limit
        entries = entries[:limit]
        
        # Convert to dicts with rank
        result = []
        for idx, entry in enumerate(entries):
            entry_dict = asdict(entry)
            entry_dict['rank'] = idx + 1
            result.append(entry_dict)
        
        return result

    async def filter_entries(
        self,
        symbol: Optional[str] = None,
        timeframe: Optional[str] = None,
        side: Optional[str] = None,
        min_score: int = 0,
        max_score: int = 100,
        limit: int = 100,
        sort_by: str = "score"
    ) -> List[Dict[str, Any]]:
        """
        Search entries with filters.
        
        Args:
            symbol: Filter by symbol (e.g., 'EURUSD')
            timeframe: Filter by timeframe (e.g., '1H')
            side: Filter by side ('long', 'short', or None for all)
            min_score: Minimum confluence score
            max_score: Maximum confluence score
            limit: Max results
            sort_by: Sort key
            
        Returns:
            Filtered entries
        """
        entries = list(self._entries_cache.values())
        
        # Apply filters
        if symbol:
            entries = [e for e in entries if e.symbol == symbol.upper()]
        
        if timeframe:
            entries = [e for e in entries if e.timeframe == timeframe]
        
        if side:
            entries = [e for e in entries if e.side == side.lower()]
        
        entries = [
            e for e in entries
            if min_score <= e.confluence_score <= max_score
        ]
        
        # Filter expired
        now = datetime.utcnow()
        entries = [
            e for e in entries
            if not e.expires_at or datetime.fromisoformat(e.expires_at) > now
        ]
        
        # Sort and limit
        entries = self._sort_entries(entries, sort_by)
        entries = entries[:limit]
        
        # Add ranks
        result = []
        for idx, entry in enumerate(entries):
            entry_dict = asdict(entry)
            entry_dict['rank'] = idx + 1
            result.append(entry_dict)
        
        return result

    async def add_entry(self, entry: EntryCandidateData) -> None:
        """
        Add or update an entry in the cache.
        
        Args:
            entry: Entry to add
        """
        self._entries_cache[entry.id] = entry
        self._last_cache_update = datetime.utcnow()

    async def add_entries_batch(self, entries: List[EntryCandidateData]) -> None:
        """
        Add multiple entries at once.
        
        Args:
            entries: List of entries to add
        """
        for entry in entries:
            await self.add_entry(entry)

    async def remove_entry(self, entry_id: str) -> bool:
        """
        Remove an entry (mark as used/closed).
        
        Args:
            entry_id: ID of entry to remove
            
        Returns:
            True if removed, False if not found
        """
        if entry_id in self._entries_cache:
            del self._entries_cache[entry_id]
            return True
        return False

    async def get_entry_by_id(self, entry_id: str) -> Optional[Dict[str, Any]]:
        """
        Get specific entry by ID.
        
        Args:
            entry_id: Entry ID
            
        Returns:
            Entry dict or None if not found
        """
        if entry_id in self._entries_cache:
            entry = self._entries_cache[entry_id]
            # Check if expired
            if entry.expires_at:
                now = datetime.utcnow()
                if datetime.fromisoformat(entry.expires_at) <= now:
                    await self.remove_entry(entry_id)
                    return None
            return asdict(entry)
        return None

    async def clear_expired(self) -> int:
        """
        Remove all expired entries.
        
        Returns:
            Number of entries removed
        """
        now = datetime.utcnow()
        expired_ids = [
            entry_id for entry_id, entry in self._entries_cache.items()
            if entry.expires_at and datetime.fromisoformat(entry.expires_at) <= now
        ]
        
        for entry_id in expired_ids:
            await self.remove_entry(entry_id)
        
        return len(expired_ids)

    async def get_statistics(self) -> Dict[str, Any]:
        """
        Get statistics about cached entries.
        
        Returns:
            Stats dict with counts, averages, etc.
        """
        entries = list(self._entries_cache.values())
        
        if not entries:
            return {
                'total_entries': 0,
                'avg_score': 0,
                'avg_rrr': 0,
                'symbols_count': 0,
                'longs_count': 0,
                'shorts_count': 0
            }
        
        # Filter non-expired
        now = datetime.utcnow()
        valid_entries = [
            e for e in entries
            if not e.expires_at or datetime.fromisoformat(e.expires_at) > now
        ]
        
        symbols = set(e.symbol for e in valid_entries)
        longs = [e for e in valid_entries if e.side == 'long']
        shorts = [e for e in valid_entries if e.side == 'short']
        
        scores = [e.confluence_score for e in valid_entries]
        rrrs = [e.rrr for e in valid_entries if e.rrr > 0]
        
        return {
            'total_entries': len(valid_entries),
            'avg_score': sum(scores) / len(scores) if scores else 0,
            'max_score': max(scores) if scores else 0,
            'min_score': min(scores) if scores else 0,
            'avg_rrr': sum(rrrs) / len(rrrs) if rrrs else 0,
            'symbols_count': len(symbols),
            'symbols': sorted(list(symbols)),
            'longs_count': len(longs),
            'shorts_count': len(shorts),
            'last_updated': self._last_cache_update.isoformat() if self._last_cache_update else None
        }

    def _sort_entries(
        self,
        entries: List[EntryCandidateData],
        sort_by: str
    ) -> List[EntryCandidateData]:
        """
        Sort entries by specified key.
        
        Args:
            entries: List to sort
            sort_by: Sort key
            
        Returns:
            Sorted list (descending)
        """
        if sort_by == "score":
            return sorted(entries, key=lambda e: e.confluence_score, reverse=True)
        elif sort_by == "rrr":
            return sorted(entries, key=lambda e: e.rrr, reverse=True)
        elif sort_by == "timestamp":
            return sorted(entries, key=lambda e: e.created_at, reverse=True)
        elif sort_by == "symbol":
            return sorted(entries, key=lambda e: (e.symbol, -e.confluence_score))
        else:
            # Default to score
            return sorted(entries, key=lambda e: e.confluence_score, reverse=True)

    async def bulk_update_from_calculations(
        self,
        symbol: str,
        timeframe: str,
        calculated_entries: List[Dict[str, Any]],
        ttl_minutes: int = 60
    ) -> int:
        """
        Update cache with newly calculated entries for a symbol/TF.
        Removes old entries for this symbol/TF, adds new ones.
        
        Args:
            symbol: Symbol (e.g., 'EURUSD')
            timeframe: Timeframe (e.g., '1H')
            calculated_entries: New entries from calculation
            ttl_minutes: Time-to-live for entries
            
        Returns:
            Number of entries added
        """
        import uuid
        from datetime import datetime, timedelta
        
        # Remove old entries for this symbol/TF
        old_ids = [
            entry_id for entry_id, entry in self._entries_cache.items()
            if entry.symbol == symbol and entry.timeframe == timeframe
        ]
        
        for entry_id in old_ids:
            await self.remove_entry(entry_id)
        
        # Add new entries
        now = datetime.utcnow()
        expires_at = (now + timedelta(minutes=ttl_minutes)).isoformat()
        
        new_count = 0
        for calc_entry in calculated_entries:
            entry = EntryCandidateData(
                id=str(uuid.uuid4()),
                symbol=symbol,
                timeframe=timeframe,
                side=calc_entry.get('side', 'long').lower(),
                entry_price=float(calc_entry.get('entry', 0)),
                take_profit=float(calc_entry.get('tp', 0)),
                stop_loss=float(calc_entry.get('sl', 0)),
                rrr=float(calc_entry.get('rrr', 0)),
                confluence_score=float(calc_entry.get('score', 0)),
                strategies=calc_entry.get('strategies', []),
                source=calc_entry.get('source', 'unknown'),
                created_at=now.isoformat(),
                expires_at=expires_at,
                atr_multiplier=calc_entry.get('atr_mult'),
                risk_percentage=calc_entry.get('risk_pct'),
                metadata=calc_entry.get('metadata')
            )
            await self.add_entry(entry)
            new_count += 1
        
        return new_count


# Global instance
entries_agg = EntriesAggregationService()
