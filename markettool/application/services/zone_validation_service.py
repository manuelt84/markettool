"""
Zone Validation Service

Validates if current price/conditions are suitable for trading
based on various zone constraints (no-trade zones, overbought/oversold, etc).
"""

import logging
from dataclasses import dataclass
from typing import Optional, Dict, Any, List
from enum import Enum
from datetime import datetime

logger = logging.getLogger(__name__)


class ZoneType(Enum):
    """Types of trading zones"""
    NO_TRADE_ZONE = "no_trade_zone"  # Economic blackout
    OVERBOUGHT = "overbought"  # Price too high (RSI > 70)
    OVERSOLD = "oversold"  # Price too low (RSI < 30)
    RESISTANCE_PROXIMITY = "resistance_proximity"  # Too close to resistance
    SUPPORT_PROXIMITY = "support_proximity"  # Too close to support
    HIGH_VOLATILITY = "high_volatility"  # ATR too high
    BREAKOUT_ZONE = "breakout_zone"  # Recent breakout


@dataclass
class ZoneValidation:
    """Zone validation result"""
    is_valid: bool  # True if safe to trade
    zone_violations: List[str]  # Names of violated zones
    zone_type: Optional[str]  # Type of violation (if any)
    reason: str  # Human-readable explanation
    confidence: float  # 0.0-1.0, confidence in validation
    metadata: Dict[str, Any] = None


class ZoneValidationService:
    """
    Validates trading zones and conditions for entry.
    
    Checks for:
    - Economic calendar blackouts / no-trade times
    - Overbought/oversold conditions (RSI)
    - Proximity to support/resistance
    - High volatility zones
    - Recent breakout zones
    """
    
    # Zone thresholds
    RSI_OVERBOUGHT = 70
    RSI_OVERSOLD = 30
    PROXIMITY_PCT = 0.015  # 1.5% from S/R
    VOLATILITY_MULTIPLIER = 1.5  # ATR threshold
    BREAKOUT_LOOKBACK = 20  # Bars for breakout detection
    
    def __init__(self, logger: Optional[logging.Logger] = None):
        self.logger = logger or logging.getLogger(__name__)
    
    def validate_trading_zone(
        self,
        current_price: float,
        rsi: Optional[float] = None,
        atr: Optional[float] = None,
        support_levels: Optional[List[float]] = None,
        resistance_levels: Optional[List[float]] = None,
        recent_high: Optional[float] = None,
        recent_low: Optional[float] = None,
        recent_atr_avg: Optional[float] = None,
    ) -> ZoneValidation:
        """
        Validate if current conditions are suitable for trading.
        
        Args:
            current_price: Current price level
            rsi: RSI value (0-100)
            atr: Current ATR value
            support_levels: List of support price levels
            resistance_levels: List of resistance price levels
            recent_high: Recent high price (for breakout detection)
            recent_low: Recent low price (for breakout detection)
            recent_atr_avg: Recent average ATR (for volatility)
        
        Returns:
            ZoneValidation with safety assessment
        """
        violations = []
        metadata = {}
        
        # Check RSI overbought/oversold
        if rsi is not None:
            if rsi > self.RSI_OVERBOUGHT:
                violations.append(f"RSI overbought: {rsi:.1f}")
                metadata['rsi_status'] = 'overbought'
            elif rsi < self.RSI_OVERSOLD:
                violations.append(f"RSI oversold: {rsi:.1f}")
                metadata['rsi_status'] = 'oversold'
            else:
                metadata['rsi_status'] = 'normal'
        
        # Check proximity to support/resistance
        if support_levels:
            proximity_violation = self._check_sr_proximity(
                current_price, support_levels, 'support'
            )
            if proximity_violation:
                violations.append(proximity_violation)
        
        if resistance_levels:
            proximity_violation = self._check_sr_proximity(
                current_price, resistance_levels, 'resistance'
            )
            if proximity_violation:
                violations.append(proximity_violation)
        
        # Check high volatility
        if atr is not None and recent_atr_avg is not None:
            if atr > recent_atr_avg * self.VOLATILITY_MULTIPLIER:
                violations.append(f"High volatility: ATR {atr:.4f} vs avg {recent_atr_avg:.4f}")
                metadata['volatility_status'] = 'high'
            else:
                metadata['volatility_status'] = 'normal'
        
        # Check breakout zone
        if recent_high is not None and recent_low is not None:
            breakout_violation = self._check_breakout_zone(
                current_price, recent_high, recent_low
            )
            if breakout_violation:
                violations.append(breakout_violation)
        
        # Determine validity
        is_valid = len(violations) == 0
        confidence = 1.0 - (len(violations) * 0.15)  # 15% confidence loss per violation
        confidence = max(0.0, min(1.0, confidence))
        
        # Generate reason
        if is_valid:
            reason = "Price zone is valid for trading - all conditions passed"
        else:
            reason = f"Price zone has {len(violations)} violation(s): {', '.join(violations[:2])}"
        
        return ZoneValidation(
            is_valid=is_valid,
            zone_violations=violations,
            zone_type=self._get_primary_zone_type(violations),
            reason=reason,
            confidence=confidence,
            metadata=metadata or {}
        )
    
    def check_no_trading_hours(
        self,
        current_time: datetime,
        no_trade_windows: Optional[List[tuple]] = None,  # [(start_hour, end_hour), ...]
    ) -> ZoneValidation:
        """
        Check if current time is in a no-trading window.
        
        Default no-trade windows:
        - 20:00-22:00 (major news releases expected)
        - 23:00-08:00 (low volume, Asian session)
        
        Args:
            current_time: Current datetime
            no_trade_windows: List of (start_hour, end_hour) tuples
        
        Returns:
            ZoneValidation indicating if trading is allowed
        """
        if no_trade_windows is None:
            # Default economic blackout windows (UTC)
            no_trade_windows = [
                (20, 22),  # Major releases (typically NY news)
                (23, 8),   # Low volume, Asian session
            ]
        
        current_hour = current_time.hour
        in_blackout = False
        blackout_reason = ""
        
        for start, end in no_trade_windows:
            if start < end:
                # Normal range (e.g., 20-22)
                if start <= current_hour < end:
                    in_blackout = True
                    blackout_reason = f"Blackout window {start:02d}:00-{end:02d}:00"
                    break
            else:
                # Range wraps midnight (e.g., 23-8)
                if current_hour >= start or current_hour < end:
                    in_blackout = True
                    blackout_reason = f"Blackout window {start:02d}:00-{end:02d}:00 (wraps midnight)"
                    break
        
        return ZoneValidation(
            is_valid=not in_blackout,
            zone_violations=[blackout_reason] if in_blackout else [],
            zone_type='no_trade_zone' if in_blackout else None,
            reason=blackout_reason or "Trading window is active",
            confidence=1.0,
            metadata={
                'current_hour': current_hour,
                'in_blackout': in_blackout,
            }
        )
    
    def validate_multi_condition(
        self,
        validations: Dict[str, bool],  # {condition_name: is_valid}
    ) -> ZoneValidation:
        """
        Aggregate multiple zone validation conditions.
        
        Args:
            validations: Dict of condition_name -> is_valid
        
        Returns:
            Aggregated validation result
        """
        all_valid = all(validations.values())
        violations = [name for name, valid in validations.items() if not valid]
        
        confidence = (sum(validations.values()) / len(validations)) if validations else 0.0
        
        return ZoneValidation(
            is_valid=all_valid,
            zone_violations=violations,
            zone_type='multi_condition',
            reason=f"{sum(validations.values())}/{len(validations)} conditions passed",
            confidence=confidence,
            metadata={'conditions': validations}
        )
    
    # ===================== PRIVATE METHODS =====================
    
    @staticmethod
    def _check_sr_proximity(
        current_price: float,
        levels: List[float],
        level_type: str  # 'support' or 'resistance'
    ) -> Optional[str]:
        """Check if price is too close to support/resistance"""
        if not levels:
            return None
        
        for level in levels:
            distance_pct = abs(current_price - level) / level
            
            if distance_pct < ZoneValidationService.PROXIMITY_PCT:
                return f"Too close to {level_type}: {level:.4f} ({distance_pct*100:.2f}% away)"
        
        return None
    
    @staticmethod
    def _check_breakout_zone(
        current_price: float,
        recent_high: float,
        recent_low: float,
    ) -> Optional[str]:
        """Check if price recently broke out (risky zone)"""
        price_range = recent_high - recent_low
        
        if price_range == 0:
            return None
        
        # Price near recent high → breakout up
        if current_price >= recent_high - (price_range * 0.05):
            return f"Near recent high {recent_high:.4f} - breakout zone"
        
        # Price near recent low → breakout down
        if current_price <= recent_low + (price_range * 0.05):
            return f"Near recent low {recent_low:.4f} - breakout zone"
        
        return None
    
    @staticmethod
    def _get_primary_zone_type(violations: List[str]) -> Optional[str]:
        """Determine primary zone type from violations"""
        if not violations:
            return None
        
        violation_key = violations[0].lower()
        
        if 'rsi' in violation_key:
            return 'overbought' if 'overbought' in violation_key else 'oversold'
        elif 'support' in violation_key or 'resistance' in violation_key:
            return 'sr_proximity'
        elif 'volatility' in violation_key:
            return 'high_volatility'
        elif 'breakout' in violation_key:
            return 'breakout_zone'
        elif 'blackout' in violation_key:
            return 'no_trade_zone'
        
        return 'unknown'


# ==================== FACTORY ====================

def get_zone_validator(
    logger: Optional[logging.Logger] = None
) -> ZoneValidationService:
    """Get ZoneValidationService instance"""
    return ZoneValidationService(logger=logger)
