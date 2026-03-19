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
pass
import logging
pass


logger = logging.getLogger("MarketTool")


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
    # Leverage recommendations (calculated)
    leverage_recommendations: Optional[Dict[str, float]] = None
    # Entry status and confirmation
    status: str = "pending"  # pending, triggered, filled, expired, cancelled
    confirmation_count: int = 0  # number of confluent signals
    confirmation_pct: float = 0.0  # % of signals aligned
    # 🆕 Execution tracking
    execution_id: Optional[str] = None  # Links entry to specific execution/analysis session


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

    def get_all_entries(
        self,
        limit: int = 100,
        sort_by: str = "score",
        skip_expired: bool = True,
        execution_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Get all entries, ranked and filtered.
        
        Args:
            limit: Max entries to return
            sort_by: Sort key ('score', 'rrr', 'timestamp')
            skip_expired: Exclude expired entries
            execution_id: Filter by execution ID (optional)
            
        Returns:
            List of entry dicts, ranked by sort_by
        """
        entries = list(self._entries_cache.values())
        
        # Filter by execution_id if provided
        if execution_id:
            entries = [e for e in entries if e.execution_id == execution_id]
        
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

    def filter_entries(
        self,
        symbol: Optional[str] = None,
        timeframe: Optional[str] = None,
        side: Optional[str] = None,
        execution_id: Optional[str] = None,
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
            execution_id: Filter by execution ID
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

        if execution_id:
            entries = [e for e in entries if e.execution_id == execution_id]
        
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

    def get_statistics(self) -> Dict[str, Any]:
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

    def _calculate_leverage_recommendations(
        self,
        entry_price: float,
        take_profit: float,
        stop_loss: float,
        rrr: float,
        confluence_score: float,
        risk_percentage: Optional[float] = None
    ) -> Dict[str, float]:
        """
        Calculate leverage recommendations based on RRR, confluence, and risk.
        Always returns a valid dict, never empty.
        """
        try:
            # Default fallback based purely on RRR if prices are invalid
            if not (entry_price > 0 and take_profit > 0 and stop_loss > 0):
                # Fallback: use RRR and confluence only
                base_leverage = max(1, min(25, rrr))
                confidence_multiplier = 0.5 + (confluence_score / 100.0) * 0.5
                leverage_base = base_leverage * confidence_multiplier
            else:
                # Calculate distance from entry to stop loss
                distance = abs(entry_price - stop_loss) / entry_price
                if distance <= 0:
                    # Fallback
                    base_leverage = max(1, min(25, rrr))
                    confidence_multiplier = 0.5 + (confluence_score / 100.0) * 0.5
                    leverage_base = base_leverage * confidence_multiplier
                else:
                    # Normal calculation
                    base_leverage = min(25, max(1, rrr / distance)) if distance > 0 else 1
                    confidence_multiplier = 0.5 + (confluence_score / 100.0) * 0.5
                    risk_multiplier = 1.0
                    if risk_percentage:
                        risk_multiplier = 1.0 + (risk_percentage / 5.0) * 0.5
                    leverage_base = base_leverage * confidence_multiplier * risk_multiplier
            
            # Calculate different levels - always return valid numbers
            level_1_conservative = max(1, min(20, int(leverage_base * 0.8)))
            level_1_theoretical = max(1, min(100, int(leverage_base * 1.2)))
            level_2_moderate = max(1, min(50, int(leverage_base * 1.5)))
            level_2_theoretical = max(1, min(200, int(leverage_base * 2.0)))
            recommended = max(1, min(25, int(leverage_base)))
            
            return {
                "level_1_conservative": float(level_1_conservative),
                "level_1_theoretical": float(level_1_theoretical),
                "level_2_moderate": float(level_2_moderate),
                "level_2_theoretical": float(level_2_theoretical),
                "recommended": float(recommended),
            }
        except Exception:
            # Last resort: hardcoded minimums
            return {
                "level_1_conservative": 1.0,
                "level_1_theoretical": 2.0,
                "level_2_moderate": 3.0,
                "level_2_theoretical": 5.0,
                "recommended": 2.0,
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
        ttl_minutes: int = 60,
        execution_id: Optional[str] = None
    ) -> int:
        """
        Update cache with newly calculated entries for a symbol/TF.
        Removes old entries for this symbol/TF, adds new ones.
        
        Args:
            symbol: Symbol (e.g., 'EURUSD')
            timeframe: Timeframe (e.g., '1H')
            calculated_entries: New entries from calculation
            ttl_minutes: Time-to-live for entries
            execution_id: Optional execution ID to link entries to analysis session
            
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
            entry_price = float(calc_entry.get('entry', 0))
            take_profit = float(calc_entry.get('tp', 0))
            stop_loss = float(calc_entry.get('sl', 0))
            rrr = float(calc_entry.get('rrr', 0))
            confluence_score = float(calc_entry.get('score', 0))
            strategies = calc_entry.get('strategies', [])
            
            # 🔧 FIX: Determine source with refined confluence detection
            # Priority: 
            # 1. Explicit source from calc_entry (if provided)
            # 2. Single strategy → use that strategy
            # 3. Multiple strategies with high score (≥85) AND ≥3 strategies → confluence
            # 4. Multiple strategies → use highest priority strategy
            # 5. No strategies → unknown
            
            if 'source' in calc_entry and calc_entry['source']:
                # Explicit source provided by strategy
                source = calc_entry['source']
            elif not strategies or len(strategies) == 0:
                # No strategies detected
                source = "unknown"
            elif len(strategies) == 1:
                # Single strategy - use it directly
                source = strategies[0].lower()
            elif len(strategies) >= 3 and confluence_score >= 85:
                # TRUE confluence: 3+ strategies with very high score
                source = "confluence"
            else:
                # Multiple strategies but not strong enough for confluence
                # Use highest priority strategy
                strategy_priority = {"fvg": 4, "smc": 3, "sr": 2, "tech": 1}
                sorted_strategies = sorted(
                    strategies,
                    key=lambda s: strategy_priority.get(s.lower(), 0),
                    reverse=True
                )
                source = sorted_strategies[0].lower()
            
            # Debug logging for source assignment
            if len(strategies) > 1:
                logger.debug(
                    "[EntriesAgg] %s/%s: strategies=%s score=%.1f → source=%s",
                    symbol, timeframe, strategies, confluence_score, source
                )
            
            # Extract execution_id from metadata or use parameter
            entry_exec_id = execution_id
            if not entry_exec_id and calc_entry.get('metadata'):
                entry_exec_id = calc_entry['metadata'].get('exec_id')
            
            entry = EntryCandidateData(
                id=str(uuid.uuid4()),
                symbol=symbol,
                timeframe=timeframe,
                side=calc_entry.get('side', 'long').lower(),
                entry_price=entry_price,
                take_profit=take_profit,
                stop_loss=stop_loss,
                rrr=rrr,
                confluence_score=confluence_score,
                strategies=strategies,
                source=source,
                created_at=now.isoformat(),
                expires_at=expires_at,
                atr_multiplier=calc_entry.get('atr_mult'),
                risk_percentage=calc_entry.get('risk_pct'),
                metadata=calc_entry.get('metadata'),
                # Calculate leverage recommendations
                leverage_recommendations=self._calculate_leverage_recommendations(
                    entry_price=entry_price,
                    take_profit=take_profit,
                    stop_loss=stop_loss,
                    rrr=rrr,
                    confluence_score=confluence_score,
                    risk_percentage=calc_entry.get('risk_pct')
                ),
                # Set entry status
                status="pending",
                confirmation_count=len(strategies),
                confirmation_pct=min(100.0, confluence_score),
                # 🆕 Link to execution session
                execution_id=entry_exec_id
            )
            await self.add_entry(entry)
            new_count += 1
        
        return new_count


# Global instance
entries_agg = EntriesAggregationService()
