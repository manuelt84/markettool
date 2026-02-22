"""Alert system for ponderacion threshold crossings and momentum events."""

from __future__ import annotations

import json as _json
from datetime import datetime, timedelta
from enum import Enum


class AlertType(str, Enum):
    """Alert notification types."""
    RANK_IMPROVED = "rank_improved"  # Symbol rank improved (moved up in list)
    RANK_WORSENED = "rank_worsened"  # Symbol rank worsened (moved down)
    MOMENTUM_BULLISH = "momentum_bullish"  # Bullish momentum detected
    MOMENTUM_BEARISH = "momentum_bearish"  # Bearish momentum detected
    SCORE_THRESHOLD = "score_threshold"  # Ponderacion crossed threshold
    BREAKOUT_DETECTED = "breakout_detected"  # Strong directional move
    CONFLUENCE_PEAK = "confluence_peak"  # High confluence reading
    ENTRY_READY = "entry_ready"  # Algorithm suggests entry signal


class AlertSeverity(str, Enum):
    """Alert importance levels."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class PonderacionAlert:
    """
    Alert system for trading events based on ponderacion analysis.
    Stores alerts in Redis, delivers via WebSocket.
    """
    
    def __init__(self, redis_client=None):
        self.redis_client = redis_client
        self.local_alerts = []  # Fallback if Redis unavailable
        self.alert_history_limit = 1000  # Keep last 1000 alerts
        self.thresholds = {
            "momentum_bullish": 1.5,  # Momentum > 1.5 triggers alert
            "momentum_bearish": -1.5,  # Momentum < -1.5 triggers alert
            "rank_improvement": 5,  # Rank improved by 5+ positions
            "confluence_high": 75.0,  # Confluence % > 75
            "min_interval_seconds": 30,  # Min seconds between same symbol alerts
        }
    
    def _make_key(self, prefix: str) -> str:
        """Generate Redis key."""
        return f"alert:{prefix}"
    
    def create_alert(
        self,
        symbol: str,
        timeframe: str,
        alert_type: AlertType,
        severity: AlertSeverity,
        title: str,
        message: str,
        metadata: dict | None = None,
    ) -> dict:
        """Create and store a new alert.
        
        Args:
            symbol: Trading pair
            timeframe: Timeframe
            alert_type: Type of alert
            severity: Severity level
            title: Short title
            message: Detailed message
            metadata: Additional context (scores, ranks, etc.)
        
        Returns:
            Alert dict with id, timestamp, etc.
        """
        alert = {
            "id": f"{symbol}_{timeframe}_{datetime.utcnow().timestamp()}",
            "symbol": symbol,
            "timeframe": timeframe,
            "type": alert_type.value,
            "severity": severity.value,
            "title": title,
            "message": message,
            "metadata": metadata or {},
            "timestamp": datetime.utcnow().isoformat(),
            "read": False,
        }
        
        # Store in Redis
        if self.redis_client:
            try:
                alert_json = _json.dumps(alert)
                # Add to active alerts set
                self.redis_client.lpush(self._make_key("active"), alert_json)
                # Trim to limit
                self.redis_client.ltrim(self._make_key("active"), 0, self.alert_history_limit - 1)
                # Set expiration (7 days)
                self.redis_client.expire(self._make_key("active"), 7 * 24 * 3600)
                
                # Also add to symbol-specific queue
                self.redis_client.rpush(
                    self._make_key(f"symbol:{symbol}"),
                    alert_json
                )
            except Exception as e:
                print(f"[PonderacionAlert] Redis store failed: {e}")
        
        # Fallback: in-memory
        self.local_alerts.append(alert)
        if len(self.local_alerts) > self.alert_history_limit:
            self.local_alerts = self.local_alerts[-self.alert_history_limit:]
        
        return alert
    
    def check_momentum_alert(
        self,
        symbol: str,
        timeframe: str,
        momentum_analysis: dict,
    ) -> dict | None:
        """Check if momentum meets alert threshold.
        
        Args:
            symbol: Trading pair
            timeframe: Timeframe
            momentum_analysis: Result from PonderacionHistory.calculate_momentum()
        
        Returns:
            Alert dict if threshold crossed, None otherwise
        """
        score = momentum_analysis.get("momentum_score", 0.0)
        direction = momentum_analysis.get("direction", "neutral")
        
        if direction == "bullish" and score > self.thresholds["momentum_bullish"]:
            return self.create_alert(
                symbol=symbol,
                timeframe=timeframe,
                alert_type=AlertType.MOMENTUM_BULLISH,
                severity=AlertSeverity.MEDIUM if score < 2.0 else AlertSeverity.HIGH,
                title=f"🟢 {symbol} Bullish Signals ({timeframe})",
                message=f"Strong bullish momentum detected (score: {score:.2f}). Recent trend: {momentum_analysis.get('recent_trend', 0):.1f}%",
                metadata={
                    "momentum_score": momentum_analysis.get("momentum_score"),
                    "strength": momentum_analysis.get("strength"),
                    "recent_trend": momentum_analysis.get("recent_trend"),
                },
            )
        
        elif direction == "bearish" and score < self.thresholds["momentum_bearish"]:
            return self.create_alert(
                symbol=symbol,
                timeframe=timeframe,
                alert_type=AlertType.MOMENTUM_BEARISH,
                severity=AlertSeverity.MEDIUM if score > -2.0 else AlertSeverity.HIGH,
                title=f"🔴 {symbol} Bearish Signals ({timeframe})",
                message=f"Strong bearish momentum detected (score: {score:.2f}). Recent trend: {momentum_analysis.get('recent_trend', 0):.1f}%",
                metadata={
                    "momentum_score": momentum_analysis.get("momentum_score"),
                    "strength": momentum_analysis.get("strength"),
                    "recent_trend": momentum_analysis.get("recent_trend"),
                },
            )
        
        return None
    
    def check_rank_alert(
        self,
        symbol: str,
        timeframe: str,
        rank_change: dict,
    ) -> dict | None:
        """Check if rank improved/worsened significantly.
        
        Args:
            symbol: Trading pair
            timeframe: Timeframe
            rank_change: Result from PonderacionHistory.get_rank_change()
        
        Returns:
            Alert dict if rank change significant, None otherwise
        """
        change = rank_change.get("change", 0)
        current_rank = rank_change.get("current_rank")
        
        if change == 0 or current_rank is None:
            return None
        
        if change < -self.thresholds["rank_improvement"]:  # Improved (negative change = better)
            return self.create_alert(
                symbol=symbol,
                timeframe=timeframe,
                alert_type=AlertType.RANK_IMPROVED,
                severity=AlertSeverity.MEDIUM,
                title=f"📈 {symbol} Rank Improved ({timeframe})",
                message=f"Rank improved by {abs(change)} positions (now #{current_rank}). Strong momentum building.",
                metadata={
                    "previous_rank": rank_change.get("previous_rank"),
                    "current_rank": current_rank,
                    "change": change,
                },
            )
        
        elif change > self.thresholds["rank_improvement"]:  # Worsened
            return self.create_alert(
                symbol=symbol,
                timeframe=timeframe,
                alert_type=AlertType.RANK_WORSENED,
                severity=AlertSeverity.LOW,
                title=f"📉 {symbol} Rank Worsened ({timeframe})",
                message=f"Rank worsened by {change} positions (now #{current_rank}). Momentum fading.",
                metadata={
                    "previous_rank": rank_change.get("previous_rank"),
                    "current_rank": current_rank,
                    "change": change,
                },
            )
        
        return None
    
    def get_active_alerts(self, symbol: str | None = None) -> list[dict]:
        """Get active alerts.
        
        Args:
            symbol: If provided, get alerts for specific symbol only
        
        Returns:
            List of active alerts
        """
        if self.redis_client:
            try:
                key = self._make_key(f"symbol:{symbol}") if symbol else self._make_key("active")
                alerts_json = self.redis_client.lrange(key, 0, -1)
                return [_json.loads(a) for a in alerts_json]
            except Exception as e:
                print(f"[PonderacionAlert] Redis get failed: {e}")
        
        # Fallback: in-memory
        if symbol:
            return [a for a in self.local_alerts if a.get("symbol") == symbol]
        return self.local_alerts
    
    def mark_alert_read(self, alert_id: str) -> bool:
        """Mark alert as read."""
        if self.redis_client:
            try:
                # This would require more complex Redis operations with hashes
                # For now, we track read status in client-side
                pass
            except Exception:
                pass
        return True
    
    def clear_old_alerts(self, hours: int = 24) -> int:
        """Remove alerts older than specified hours.
        
        Returns:
            Number of alerts removed
        """
        if not self.redis_client:
            # Fallback: in-memory cleanup
            cutoff = datetime.utcnow() - timedelta(hours=hours)
            initial_len = len(self.local_alerts)
            self.local_alerts = [
                a for a in self.local_alerts
                if datetime.fromisoformat(a["timestamp"]) > cutoff
            ]
            return initial_len - len(self.local_alerts)
        
        # Redis cleanup would be done via expire() during creation
        return 0


__all__ = ["PonderacionAlert", "AlertType", "AlertSeverity"]
