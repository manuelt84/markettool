"""Historical ponderacion tracking and momentum scoring."""

from __future__ import annotations

import json as _json
import logging
from datetime import datetime
from collections import deque
pass

logger = logging.getLogger(__name__)


class PonderacionHistory:
    """
    Track historical ponderaciones for momentum and trend analysis.
    Stores last 100 calculations per symbol/timeframe in Redis.
    """
    
    def __init__(self, redis_client=None):
        self.redis_client = redis_client
        self.local_history = {}  # Fallback if Redis unavailable
        self.history_size = 100  # Keep last 100 calculations
    
    def _make_history_key(self, symbol: str, timeframe: str) -> str:
        """Generate Redis key for ponderacion history."""
        return f"ponderacion:history:{symbol}:{timeframe}"

    @staticmethod
    def _to_float(value) -> float | None:
        try:
            if value is None:
                return None
            return float(value)
        except (TypeError, ValueError):
            return None

    def _normalize_record(self, raw_record: dict) -> dict:
        """Normalize multiple ponderacion formats into a canonical schema."""
        record = dict(raw_record or {})

        pi_long = self._to_float(record.get("pi_long", record.get("PI_Long")))
        pi_short = self._to_float(record.get("pi_short", record.get("PI_Short")))
        dir_long = self._to_float(record.get("ponderacion_long", record.get("Ponderacion_Long")))
        dir_short = self._to_float(record.get("ponderacion_short", record.get("Ponderacion_Short")))
        legacy_score = self._to_float(
            record.get("ponderacion", record.get("Ponderacion", record.get("ponderacion_value")))
        )

        score = legacy_score
        method = "legacy_single"

        if score is None and pi_long is not None and pi_short is not None:
            score = pi_long - pi_short
            method = "pi_delta"

        if score is None and dir_long is not None and dir_short is not None:
            score = dir_long - dir_short
            method = "directional_delta"

        if score is None:
            score = 0.0
            method = "fallback_zero"

        record["canonical_score"] = float(score)
        record["calculation_method"] = str(record.get("calculation_method") or method)
        if pi_long is not None:
            record["pi_long"] = pi_long
        if pi_short is not None:
            record["pi_short"] = pi_short
        if dir_long is not None:
            record["ponderacion_long"] = dir_long
        if dir_short is not None:
            record["ponderacion_short"] = dir_short
        return record
    
    def add_record(self, symbol: str, timeframe: str, ponderacion_data: dict) -> bool:
        """Add ponderacion record to history with timestamp.
        
        Args:
            symbol: Trading pair (e.g., 'BTCUSD')
            timeframe: Timeframe (e.g., '1m', '5m', '1h')
            ponderacion_data: Dict with 'pi_long', 'pi_short', 'ponderacion', etc.
        
        Returns:
            True if successful
        """
        key = self._make_history_key(symbol, timeframe)
        record = self._normalize_record({
            "timestamp": datetime.utcnow().isoformat(),
            **ponderacion_data,
        })
        
        if self.redis_client:
            try:
                # Use Redis list (RPUSH) for ordered history
                record_json = _json.dumps(record)
                self.redis_client.rpush(key, record_json)
                
                # Keep only last 100 records (trim)
                self.redis_client.ltrim(key, -self.history_size, -1)
                
                # Set expiration to 7 days
                self.redis_client.expire(key, 7 * 24 * 3600)
                return True
            except Exception as e:
                logger.warning("[PonderacionHistory] Redis add_record failed: %s", e)
        
        # Fallback: in-memory deque
        if key not in self.local_history:
            self.local_history[key] = deque(maxlen=self.history_size)
        self.local_history[key].append(record)
        return True
    
    def get_history(self, symbol: str, timeframe: str, limit: int = 100) -> list[dict]:
        """Get last N ponderacion records for analysis.
        
        Args:
            symbol: Trading pair
            timeframe: Timeframe
            limit: Max records to return (default 100)
        
        Returns:
            List of ponderacion records with timestamps
        """
        key = self._make_history_key(symbol, timeframe)
        
        if self.redis_client:
            try:
                # Redis LRANGE gets records (newest last, so reverse offset)
                records_json = self.redis_client.lrange(key, -limit, -1)
                return [self._normalize_record(_json.loads(r)) for r in records_json]
            except Exception as e:
                logger.warning("[PonderacionHistory] Redis get_history failed: %s", e)
        
        # Fallback: in-memory
        if key in self.local_history:
            return [self._normalize_record(r) for r in list(self.local_history[key])[-limit:]]
        
        return []
    
    def calculate_momentum(self, symbol: str, timeframe: str, lookback: int = 10) -> dict:
        """Calculate momentum score based on recent ponderacion changes.
        
        Momentum = (Latest - Average(Lookback)) / StdDev
        Positive = bullish trend, Negative = bearish trend
        
        Args:
            symbol: Trading pair
            timeframe: Timeframe
            lookback: How many candles back to analyze
        
        Returns:
            {
                'momentum_score': float,
                'direction': 'bullish'|'bearish'|'neutral',
                'strength': 'strong'|'moderate'|'weak',
                'recent_trend': float (pct change),
                'records_analyzed': int,
            }
        """
        history = self.get_history(symbol, timeframe, limit=lookback + 1)
        
        if len(history) < 2:
            return {
                "momentum_score": 0.0,
                "direction": "neutral",
                "strength": "weak",
                "recent_trend": 0.0,
                "records_analyzed": len(history),
            }
        
        # Extract ponderacion values (try multiple field names)
        scores = []
        for record in history:
            # Try multiple field names for compatibility
            score = record.get("canonical_score")
            if score is not None:
                scores.append(float(score))
        
        if len(scores) < 2:
            return {
                "momentum_score": 0.0,
                "direction": "neutral",
                "strength": "weak",
                "recent_trend": 0.0,
                "records_analyzed": len(scores),
            }
        
        # Calculate statistics
        latest = scores[-1]
        previous = scores[-2] if len(scores) >= 2 else scores[0]
        lookback_avg = sum(scores[:-1]) / len(scores[:-1]) if len(scores) > 1 else latest
        
        # Simple momentum: directional change
        recent_trend = ((latest - previous) / previous * 100) if previous != 0 else 0.0
        momentum_score = latest - lookback_avg  # Difference from average
        
        # Determine direction and strength
        if momentum_score > 0.5:
            direction = "bullish"
            strength = "strong" if momentum_score > 2.0 else "moderate"
        elif momentum_score < -0.5:
            direction = "bearish"
            strength = "strong" if momentum_score < -2.0 else "moderate"
        else:
            direction = "neutral"
            strength = "weak"
        
        return {
            "momentum_score": round(momentum_score, 4),
            "direction": direction,
            "strength": strength,
            "recent_trend": round(recent_trend, 2),
            "records_analyzed": len(scores),
            "latest_score": round(latest, 4),
            "lookback_avg": round(lookback_avg, 4),
        }
    
    def get_rank_change(self, symbol: str, timeframe: str) -> dict:
        """Calculate if symbol rank improved/worsened since last calculation.
        
        Used for determining if an alert should be triggered.
        
        Returns:
            {
                'rank_changed': bool,
                'previous_rank': int,
                'current_rank': int,
                'change': int (positive = improved, negative = worsened),
                'timestamp': ISO string,
            }
        """
        history = self.get_history(symbol, timeframe, limit=2)
        
        if len(history) < 2:
            return {
                "rank_changed": False,
                "previous_rank": None,
                "current_rank": None,
                "change": 0,
                "timestamp": datetime.utcnow().isoformat(),
            }
        
        # Extract rank (try multiple field names)
        current = history[-1]
        previous = history[-2]
        
        current_rank = current.get("rank") or current.get("Rank")
        previous_rank = previous.get("rank") or previous.get("Rank")
        
        if current_rank is None or previous_rank is None:
            return {
                "rank_changed": False,
                "previous_rank": previous_rank,
                "current_rank": current_rank,
                "change": 0,
                "timestamp": current.get("timestamp", datetime.utcnow().isoformat()),
            }
        
        change = int(previous_rank) - int(current_rank)  # Negative = better rank
        
        return {
            "rank_changed": change != 0,
            "previous_rank": int(previous_rank),
            "current_rank": int(current_rank),
            "change": change,
            "timestamp": current.get("timestamp", datetime.utcnow().isoformat()),
        }


__all__ = ["PonderacionHistory"]
