"""Trading signal model."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional


class SignalType(str, Enum):
    """Types of trading signals."""
    
    BUY = "BUY"
    SELL = "SELL"
    STRONG_BUY = "STRONG_BUY"
    STRONG_SELL = "STRONG_SELL"
    NEUTRAL = "NEUTRAL"
    HOLD = "HOLD"


@dataclass
class Signal:
    """Trading signal for a symbol."""
    
    symbol: str
    signal_type: SignalType
    timestamp: datetime
    
    # Confidence and price targets
    confidence: float = 0.5  # 0-1
    entry_price: Optional[float] = None
    target_price: Optional[float] = None
    stop_loss: Optional[float] = None
    
    # Analysis details
    reason: Optional[str] = None
    indicators: Dict[str, Any] = field(default_factory=dict)  # e.g. {"RSI": 65, "MACD": "bullish"}
    
    # Risk/reward
    risk_points: Optional[float] = None
    reward_points: Optional[float] = None
    
    # Source
    source: str = "unknown"
    analysis_type: str = "technical"  # "technical", "fundamental", "news", "pattern"
    
    def to_dict(self) -> Dict[str, Any]:
        """Export as dictionary."""
        return {
            "symbol": self.symbol,
            "signal_type": self.signal_type.value,
            "timestamp": self.timestamp.isoformat(),
            "confidence": self.confidence,
            "entry_price": self.entry_price,
            "target_price": self.target_price,
            "stop_loss": self.stop_loss,
            "reason": self.reason,
            "indicators": self.indicators,
            "risk_points": self.risk_points,
            "reward_points": self.reward_points,
            "source": self.source,
            "analysis_type": self.analysis_type,
        }
    
    @property
    def risk_reward_ratio(self) -> Optional[float]:
        """Calculate risk-reward ratio."""
        if self.risk_points and self.reward_points and self.risk_points > 0:
            return self.reward_points / self.risk_points
        return None
    
    @property
    def is_bullish(self) -> bool:
        """Check if signal is bullish."""
        return self.signal_type in (SignalType.BUY, SignalType.STRONG_BUY)
    
    @property
    def is_bearish(self) -> bool:
        """Check if signal is bearish."""
        return self.signal_type in (SignalType.SELL, SignalType.STRONG_SELL)
    
    @property
    def is_strong(self) -> bool:
        """Check if signal is strong (strong buy/sell)."""
        return self.signal_type in (SignalType.STRONG_BUY, SignalType.STRONG_SELL)
    
    def __repr__(self) -> str:
        return (
            f"Signal({self.symbol} {self.signal_type.value} "
            f"@ {self.timestamp} "
            f"conf={self.confidence:.1%})"
        )


@dataclass
class SignalSet:
    """Collection of signals for multiple symbols."""
    
    signals: List[Signal] = field(default_factory=list)
    timestamp: datetime = field(default_factory=datetime.utcnow)
    
    def add(self, signal: Signal) -> None:
        """Add signal to set."""
        self.signals.append(signal)
    
    def get_by_symbol(self, symbol: str) -> List[Signal]:
        """Get signals for specific symbol."""
        return [s for s in self.signals if s.symbol == symbol]
    
    def get_bullish(self) -> List[Signal]:
        """Get all bullish signals."""
        return [s for s in self.signals if s.is_bullish]
    
    def get_bearish(self) -> List[Signal]:
        """Get all bearish signals."""
        return [s for s in self.signals if s.is_bearish]
    
    def get_strong(self) -> List[Signal]:
        """Get all strong signals."""
        return [s for s in self.signals if s.is_strong]
    
    def filter_by_confidence(self, min_confidence: float) -> List[Signal]:
        """Get signals with minimum confidence."""
        return [s for s in self.signals if s.confidence >= min_confidence]
    
    def __len__(self) -> int:
        return len(self.signals)
    
    def __repr__(self) -> str:
        return f"SignalSet({len(self.signals)} signals)"
