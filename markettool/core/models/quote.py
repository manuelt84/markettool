"""Current market quote model."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Optional


@dataclass
class Quote:
    """Current market quote for a symbol."""
    
    symbol: str
    price: float
    bid: Optional[float] = None
    ask: Optional[float] = None
    timestamp: Optional[datetime] = None
    change: Optional[float] = None
    change_pct: Optional[float] = None
    volume: Optional[float] = None
    high_52w: Optional[float] = None
    low_52w: Optional[float] = None
    market_cap: Optional[float] = None
    pe_ratio: Optional[float] = None
    source: str = "unknown"
    
    def to_dict(self) -> Dict[str, Any]:
        """Export as dictionary."""
        return {
            "symbol": self.symbol,
            "price": self.price,
            "bid": self.bid,
            "ask": self.ask,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "change": self.change,
            "change_pct": self.change_pct,
            "volume": self.volume,
            "high_52w": self.high_52w,
            "low_52w": self.low_52w,
            "market_cap": self.market_cap,
            "pe_ratio": self.pe_ratio,
            "source": self.source,
        }
    
    @property
    def mid_price(self) -> float:
        """Mid price between bid/ask (or price if bid/ask unavailable)."""
        if self.bid and self.ask:
            return (self.bid + self.ask) / 2
        return self.price
    
    @property
    def spread(self) -> Optional[float]:
        """Bid-ask spread (absolute)."""
        if self.bid and self.ask:
            return self.ask - self.bid
        return None
    
    @property
    def spread_pct(self) -> Optional[float]:
        """Bid-ask spread (percentage)."""
        if self.bid and self.ask and self.mid_price > 0:
            return (self.spread / self.mid_price) * 100
        return None
    
    def __repr__(self) -> str:
        return (
            f"Quote({self.symbol} @ {self.price} "
            f"[{self.timestamp}] "
            f"chg={self.change_pct}%)"
        )
