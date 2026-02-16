"""Historical OHLCV data model."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

import pandas as pd


@dataclass
class OHLCV:
    """Single OHLCV candle."""
    
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
        }


class Historico:
    """
    Represents historical OHLCV data for a symbol and timeframe.
    Wraps pandas DataFrame for convenience while providing domain semantics.
    """
    
    def __init__(
        self,
        symbol: str,
        timeframe: str,
        df: pd.DataFrame,
        source: str = "unknown",
    ):
        """
        Initialize Historico from DataFrame.
        
        Args:
            symbol: Trading symbol (e.g., "AAPL", "EURUSD")
            timeframe: Timeframe code (e.g., "1min", "1hour", "1day")
            df: pandas DataFrame with required columns: open, high, low, close, volume
            source: Origin of data (e.g., "fmp", "local", "cache")
        """
        self.symbol = symbol
        self.timeframe = timeframe
        self.source = source
        
        # Validate DataFrame
        self._df = self._validate_df(df)
    
    def _validate_df(self, df: pd.DataFrame) -> pd.DataFrame:
        """Validate and normalize DataFrame structure."""
        required_cols = {"open", "high", "low", "close", "volume"}
        missing = required_cols - set(df.columns)
        if missing:
            raise ValueError(f"Missing required columns: {missing}")
        
        # Ensure index is DatetimeIndex
        if not isinstance(df.index, pd.DatetimeIndex):
            if "date" in df.columns:
                df = df.set_index("date")
            elif "timestamp" in df.columns:
                df = df.set_index("timestamp")
            else:
                # Try to convert first column to datetime
                df.index = pd.to_datetime(df.index)
        
        # Ensure UTC timezone
        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC")
        else:
            df.index = df.index.tz_convert("UTC")
        
        # Sort by timestamp ascending
        df = df.sort_index()
        
        return df
    
    @property
    def df(self) -> pd.DataFrame:
        """Get underlying pandas DataFrame."""
        return self._df
    
    @property
    def length(self) -> int:
        """Number of candles."""
        return len(self._df)
    
    @property
    def is_empty(self) -> bool:
        """Check if data is empty."""
        return self.length == 0
    
    @property
    def first_timestamp(self) -> Optional[datetime]:
        """Get first candle timestamp."""
        return self._df.index[0] if not self.is_empty else None
    
    @property
    def last_timestamp(self) -> Optional[datetime]:
        """Get last candle timestamp."""
        return self._df.index[-1] if not self.is_empty else None
    
    @property
    def last_close(self) -> Optional[float]:
        """Get most recent close price."""
        if self.is_empty:
            return None
        return self._df.iloc[-1]["close"]
    
    @property
    def last_ohlcv(self) -> Optional[OHLCV]:
        """Get most recent OHLCV candle."""
        if self.is_empty:
            return None
        row = self._df.iloc[-1]
        return OHLCV(
            timestamp=self._df.index[-1],
            open=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
            volume=float(row["volume"]),
        )
    
    def get_range(
        self,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
    ) -> Historico:
        """
        Get subset of data within timestamp range.
        
        Args:
            start: Start timestamp (inclusive)
            end: End timestamp (inclusive)
            
        Returns:
            New Historico with filtered data
        """
        df = self._df
        if start:
            df = df[df.index >= start]
        if end:
            df = df[df.index <= end]
        
        return Historico(
            symbol=self.symbol,
            timeframe=self.timeframe,
            df=df,
            source=self.source,
        )
    
    def resample(self, new_timeframe: str) -> Historico:
        """
        Resample to different timeframe.
        
        Args:
            new_timeframe: Target timeframe code (e.g., "5min", "1hour")
            
        Returns:
            New Historico with resampled data
        """
        # Map timeframe to pandas rule
        tf_map = {
            "1min": "1T",
            "5min": "5T",
            "15min": "15T",
            "30min": "30T",
            "1hour": "1H",
            "4hour": "4H",
            "1day": "1D",
            "1week": "1W",
            "1month": "1MS",
        }
        rule = tf_map.get(new_timeframe, new_timeframe)
        
        resampled = self._df.resample(rule).agg({
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "volume": "sum",
        }).dropna()
        
        return Historico(
            symbol=self.symbol,
            timeframe=new_timeframe,
            df=resampled,
            source=f"{self.source}->resample",
        )
    
    def merge(self, other: Historico) -> Historico:
        """
        Merge with another Historico.
        Later timestamps from other take precedence in overlaps.
        
        Args:
            other: Another Historico to merge with
            
        Returns:
            New merged Historico
        """
        if self.symbol != other.symbol or self.timeframe != other.timeframe:
            raise ValueError(
                f"Cannot merge: mismatched symbol/timeframe "
                f"({self.symbol}/{self.timeframe} vs {other.symbol}/{other.timeframe})"
            )
        
        merged = pd.concat([self._df, other._df])
        merged = merged[~merged.index.duplicated(keep="last")]
        merged = merged.sort_index()
        
        return Historico(
            symbol=self.symbol,
            timeframe=self.timeframe,
            df=merged,
            source=f"{self.source}+{other.source}",
        )
    
    def to_dict(self) -> Dict[str, Any]:
        """Export data as dictionary."""
        data = self._df.reset_index().to_dict(orient="records")
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "source": self.source,
            "length": self.length,
            "first_timestamp": self.first_timestamp.isoformat() if self.first_timestamp else None,
            "last_timestamp": self.last_timestamp.isoformat() if self.last_timestamp else None,
            "data": data,
        }
    
    def __repr__(self) -> str:
        return (
            f"Historico({self.symbol}/{self.timeframe}, "
            f"{self.length} candles, "
            f"{self.first_timestamp} -> {self.last_timestamp}, "
            f"src={self.source})"
        )
