"""
Service for Support/Resistance level detection and range analysis.

This service provides hexagonal architecture implementation for:
- Support and resistance levels calculation
- Range/zigzag detection
- Dynamic window adjustment
- Key level filtering
"""

import logging
import numpy as np
import pandas as pd
from typing import Dict, List, Tuple, Optional, Any
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class SupportResistanceLevels:
    """Support and resistance levels."""
    supports: List[float]
    resistances: List[float]
    atr: float
    window_used: int


@dataclass
class RangeDetectionResult:
    """Result of range/zigzag detection."""
    is_range: bool
    structure: str  # 'range', 'uptrend', 'downtrend', 'undefined'
    rebounds: List[Dict[str, Any]]
    dynamic_range: Tuple[Optional[float], Optional[float]]


class SupportResistanceService:
    """
    Servicio para detectar soportes, resistencias y rangos de mercado.
    
    Hexagonal architecture implementation - no dependencies on MarketTool.py
    """
    
    def __init__(self, logger: Optional[logging.Logger] = None):
        self.logger = logger or logging.getLogger(__name__)
    
    def calculate_atr(
        self,
        df: pd.DataFrame,
        period: int = 14
    ) -> float:
        """Calculate Average True Range."""
        if len(df) < period:
            return float(df['high'].mean() - df['low'].mean())
        
        high = df['high'].values
        low = df['low'].values
        close = df['close'].values
        
        tr1 = high - low
        tr2 = np.abs(high[1:] - close[:-1])
        tr3 = np.abs(low[1:] - close[:-1])
        
        tr = np.maximum(np.maximum(tr1[1:], tr2), tr3)
        atr = np.mean(tr[-period:]) if len(tr) >= period else np.mean(tr)
        
        return float(atr)
    
    def calculate_support_resistance(
        self,
        df: pd.DataFrame,
        window: int,
        atr_multiplier: float = 2.0,
        current_price: Optional[float] = None,
        min_levels: int = 5
    ) -> SupportResistanceLevels:
        """
        Calculate support and resistance levels using local minima/maxima.
        
        Args:
            df: OHLCV DataFrame
            window: Window size for level detection
            atr_multiplier: ATR multiplier for filtering levels
            current_price: Current price (default: last close)
            min_levels: Minimum number of levels to return
        
        Returns:
            SupportResistanceLevels with supports, resistances, and ATR
        """
        if len(df) < window:
            self.logger.warning(f"Insufficient data for S/R calc: {len(df)} < {window}")
            last_close = float(df['close'].iloc[-1])
            return SupportResistanceLevels(
                supports=[last_close * 0.99],
                resistances=[last_close * 1.01],
                atr=last_close * 0.01,
                window_used=len(df)
            )
        
        current_price = current_price or float(df['close'].iloc[-1])
        atr = self.calculate_atr(df)
        tolerance = atr * atr_multiplier
        
        # Find local minima (supports) and maxima (resistances)
        supports = []
        resistances = []
        
        rolling_low = df['low'].rolling(window=window, center=True).min()
        rolling_high = df['high'].rolling(window=window, center=True).max()
        
        for i in range(window, len(df) - window):
            # Support: local minimum
            if df['low'].iloc[i] == rolling_low.iloc[i]:
                supports.append(float(df['low'].iloc[i]))
            
            # Resistance: local maximum
            if df['high'].iloc[i] == rolling_high.iloc[i]:
                resistances.append(float(df['high'].iloc[i]))
        
        # Filter duplicates and nearby levels
        supports = self._filter_nearby_levels(supports, tolerance)
        resistances = self._filter_nearby_levels(resistances, tolerance)
        
        # Filter by distance from current price
        supports = self._filter_by_distance(supports, current_price, atr, max_distance=3.0)
        resistances = self._filter_by_distance(resistances, current_price, atr, max_distance=3.0)
        
        # Ensure minimum number of levels
        if len(supports) < min_levels:
            # Add simple support levels
            for i in range(1, min_levels - len(supports) + 1):
                supports.append(current_price - (i * atr))
        
        if len(resistances) < min_levels:
            # Add simple resistance levels
            for i in range(1, min_levels - len(resistances) + 1):
                resistances.append(current_price + (i * atr))
        
        # Sort
        supports = sorted(supports, reverse=True)[:min_levels]
        resistances = sorted(resistances)[:min_levels]
        
        return SupportResistanceLevels(
            supports=supports,
            resistances=resistances,
            atr=atr,
            window_used=window
        )
    
    def detect_zigzag_range(
        self,
        df: pd.DataFrame,
        rebound_window: int = 140,
        tolerance_pct: float = 0.002,
        min_rebounds: int = 3
    ) -> RangeDetectionResult:
        """
        Detect if market is range-bound using zigzag analysis.
        
        Args:
            df: OHLCV DataFrame
            rebound_window: Window for detecting rebounds
            tolerance_pct: Tolerance % for level matching
            min_rebounds: Minimum rebounds to consider a range
        
        Returns:
            RangeDetectionResult with range status and structure
        """
        if len(df) < rebound_window:
            return RangeDetectionResult(
                is_range=False,
                structure='undefined',
                rebounds=[],
                dynamic_range=(None, None)
            )
        
        # Simplified zigzag: find major swing highs and lows
        highs = df['high'].values
        lows = df['low'].values
        
        # Find swings
        swing_highs = []
        swing_lows = []
        
        for i in range(rebound_window, len(df) - rebound_window):
            # Swing high: local maximum
            if highs[i] == np.max(highs[i-rebound_window:i+rebound_window]):
                swing_highs.append((i, highs[i]))
            
            # Swing low: local minimum
            if lows[i] == np.min(lows[i-rebound_window:i+rebound_window]):
                swing_lows.append((i, lows[i]))
        
        # Detect range: consistent swing highs and lows
        is_range = False
        if len(swing_highs) >= min_rebounds and len(swing_lows) >= min_rebounds:
            high_values = [h[1] for h in swing_highs]
            low_values = [l[1] for l in swing_lows]
            
            high_avg = np.mean(high_values)
            low_avg = np.mean(low_values)
            
            high_std = np.std(high_values) / high_avg
            low_std = np.std(low_values) / low_avg
            
            # Range if standard deviations are small (tight clustering)
            if high_std < tolerance_pct * 2 and low_std < tolerance_pct * 2:
                is_range = True
        
        # Determine structure
        if is_range:
            structure = 'range'
        elif len(swing_highs) > 0 and len(swing_lows) > 0:
            recent_highs = [h[1] for h in swing_highs[-3:]]
            recent_lows = [l[1] for l in swing_lows[-3:]]
            
            if len(recent_highs) >= 2 and recent_highs[-1] > recent_highs[0]:
                structure = 'uptrend'
            elif len(recent_lows) >= 2 and recent_lows[-1] < recent_lows[0]:
                structure = 'downtrend'
            else:
                structure = 'undefined'
        else:
            structure = 'undefined'
        
        # Dynamic range
        if swing_highs and swing_lows:
            dynamic_upper = np.mean([h[1] for h in swing_highs[-3:]])
            dynamic_lower = np.mean([l[1] for l in swing_lows[-3:]])
        else:
            dynamic_upper = None
            dynamic_lower = None
        
        rebounds = [
            {'type': 'high', 'idx': idx, 'value': val}
            for idx, val in swing_highs
        ] + [
            {'type': 'low', 'idx': idx, 'value': val}
            for idx, val in swing_lows
        ]
        
        return RangeDetectionResult(
            is_range=is_range,
            structure=structure,
            rebounds=rebounds,
            dynamic_range=(dynamic_lower, dynamic_upper)
        )
    
    def get_key_levels(
        self,
        df: pd.DataFrame,
        supports: List[float],
        resistances: List[float],
        atr_threshold: float = 2.0,
        max_levels: int = 2
    ) -> Dict[str, Optional[float]]:
        """
        Filter and return the most important support/resistance levels.
        
        Args:
            df: OHLCV DataFrame
            supports: List of support levels
            resistances: List of resistance levels
            atr_threshold: ATR threshold for proximity to current price
            max_levels: Maximum levels to return per side
        
        Returns:
            Dict with s1, s2, r1, r2 (closest important levels)
        """
        current_price = float(df['close'].iloc[-1])
        atr = self.calculate_atr(df)
        max_distance = atr * atr_threshold
        
        # Filter supports below current price
        supports_below = [s for s in supports if s < current_price and abs(current_price - s) <= max_distance]
        supports_below = sorted(supports_below, reverse=True)[:max_levels]
        
        # Filter resistances above current price
        resistances_above = [r for r in resistances if r > current_price and abs(r - current_price) <= max_distance]
        resistances_above = sorted(resistances_above)[:max_levels]
        
        result = {
            's1': supports_below[0] if len(supports_below) > 0 else None,
            's2': supports_below[1] if len(supports_below) > 1 else None,
            'r1': resistances_above[0] if len(resistances_above) > 0 else None,
            'r2': resistances_above[1] if len(resistances_above) > 1 else None,
        }
        
        return result
    
    # ==================== PRIVATE HELPERS ====================
    
    def _filter_nearby_levels(
        self,
        levels: List[float],
        tolerance: float
    ) -> List[float]:
        """Remove duplicate/nearby levels within tolerance."""
        if not levels:
            return []
        
        levels = sorted(set(levels))
        filtered = [levels[0]]
        
        for level in levels[1:]:
            if abs(level - filtered[-1]) > tolerance:
                filtered.append(level)
        
        return filtered
    
    def _filter_by_distance(
        self,
        levels: List[float],
        current_price: float,
        atr: float,
        max_distance: float = 3.0
    ) -> List[float]:
        """Filter levels by distance from current price."""
        max_dist = atr * max_distance
        return [
            level for level in levels
            if abs(level - current_price) <= max_dist
        ]


# ==================== FACTORY FUNCTION ====================

def get_sr_service(logger: Optional[logging.Logger] = None) -> SupportResistanceService:
    """Get SupportResistanceService instance."""
    return SupportResistanceService(logger=logger)
