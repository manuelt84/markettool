"""
Risk Management Service

Calculates position sizing, risk metrics, and portfolio exposure.
Implements Kelly Criterion and Risk-Reward Ratio analysis.
"""

import logging
from dataclasses import dataclass
from typing import Optional, Dict, Any
import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class RiskMetrics:
    """Risk analysis result"""
    position_size: float  # Units to trade
    risk_amount: float  # Dollar amount at risk
    reward_amount: float  # Potential reward in dollars
    risk_reward_ratio: float  # RRR (reward/risk)
    kelly_fraction: float  # Kelly percentage (0.0-1.0)
    position_size_kelly: float  # Position size using Kelly
    max_loss_pct: float  # Max loss as % of account
    expectancy: float  # Expected value per trade
    warning: Optional[str] = None


class RiskManagementService:
    """
    Manages position sizing and risk metrics.
    
    Implements:
    - Fixed fractional position sizing
    - Kelly Criterion calculation
    - Risk-Reward Ratio analysis
    - Exposure calculation
    """
    
    def __init__(self, logger: Optional[logging.Logger] = None):
        self.logger = logger or logging.getLogger(__name__)
    
    def calculate_position_size(
        self,
        account_balance: float,
        entry_price: float,
        stop_loss: float,
        take_profit: float,
        risk_pct: float = 0.02,
        use_kelly: bool = False,
        win_rate: float = 0.55,
        avg_win: float = 1.0,
        avg_loss: float = 0.9,
    ) -> RiskMetrics:
        """
        Calculate optimal position size based on risk parameters.
        
        Args:
            account_balance: Current account balance
            entry_price: Entry point price
            stop_loss: Stop loss price
            take_profit: Take profit price
            risk_pct: Risk per trade as % of account (default 2%)
            use_kelly: Use Kelly Criterion if True
            win_rate: Historical win rate (0-1)
            avg_win: Average winning trade payout ratio
            avg_loss: Average losing trade loss ratio
        
        Returns:
            RiskMetrics with position sizing recommendation
        """
        if entry_price <= 0 or stop_loss <= 0 or take_profit <= 0:
            return RiskMetrics(
                position_size=0,
                risk_amount=0,
                reward_amount=0,
                risk_reward_ratio=0,
                kelly_fraction=0,
                position_size_kelly=0,
                max_loss_pct=0,
                expectancy=0,
                warning="Invalid prices provided"
            )
        
        # Determine if BUY or SELL
        if entry_price < stop_loss:
            # BUY trade
            risk_per_unit = entry_price - stop_loss
            reward_per_unit = take_profit - entry_price
        else:
            # SELL trade
            risk_per_unit = stop_loss - entry_price
            reward_per_unit = entry_price - take_profit
        
        if risk_per_unit <= 0 or reward_per_unit <= 0:
            return RiskMetrics(
                position_size=0,
                risk_amount=0,
                reward_amount=0,
                risk_reward_ratio=0,
                kelly_fraction=0,
                position_size_kelly=0,
                max_loss_pct=0,
                expectancy=0,
                warning="Invalid SL/TP configuration"
            )
        
        # === FIXED FRACTIONAL SIZING ===
        risk_amount = account_balance * risk_pct
        position_size = risk_amount / risk_per_unit
        reward_amount = position_size * reward_per_unit
        rrr = reward_per_unit / risk_per_unit
        
        # === KELLY CRITERION ===
        kelly_frac = self._calculate_kelly(
            win_rate=win_rate,
            avg_win=avg_win,
            avg_loss=avg_loss,
            rrr=rrr
        )
        position_size_kelly = (account_balance * kelly_frac) / risk_per_unit
        
        # === EXPECTANCY ===
        expectancy = (win_rate * reward_amount) - ((1 - win_rate) * risk_amount)
        
        return RiskMetrics(
            position_size=position_size,
            risk_amount=risk_amount,
            reward_amount=reward_amount,
            risk_reward_ratio=rrr,
            kelly_fraction=kelly_frac,
            position_size_kelly=position_size_kelly,
            max_loss_pct=risk_pct,
            expectancy=expectancy,
            warning=self._validate_metrics(rrr, kelly_frac, risk_pct)
        )
    
    def calculate_drawdown_risk(
        self,
        account_balance: float,
        consecutive_losses: int = 5,
        loss_per_trade: float = 0.02
    ) -> Dict[str, float]:
        """
        Calculate maximum drawdown risk from consecutive losses.
        
        Args:
            account_balance: Current account balance
            consecutive_losses: Number of consecutive losing trades
            loss_per_trade: Risk per trade as decimal
        
        Returns:
            Dict with drawdown metrics
        """
        remaining_balance = account_balance
        for _ in range(consecutive_losses):
            remaining_balance *= (1 - loss_per_trade)
        
        max_drawdown_pct = (account_balance - remaining_balance) / account_balance
        max_drawdown_usd = account_balance - remaining_balance
        
        return {
            'max_drawdown_pct': max_drawdown_pct,
            'max_drawdown_usd': max_drawdown_usd,
            'remaining_balance': remaining_balance,
            'recovery_trades_needed': self._calculate_recovery_trades(
                max_drawdown_pct
            ),
        }
    
    def calculate_portfolio_exposure(
        self,
        positions: list[Dict[str, float]],
        account_balance: float,
    ) -> Dict[str, Any]:
        """
        Calculate total portfolio exposure and heat.
        
        Args:
            positions: List of {'symbol': str, 'size': float, 'entry': float}
            account_balance: Total account balance
        
        Returns:
            Exposure metrics
        """
        total_exposure = sum(
            abs(pos.get('size', 0) * pos.get('entry', 0))
            for pos in positions
        )
        exposure_pct = total_exposure / account_balance if account_balance > 0 else 0
        
        return {
            'total_exposure_usd': total_exposure,
            'exposure_pct': exposure_pct,
            'exposure_warning': 'HIGH' if exposure_pct > 0.30 else 'NORMAL',
            'max_allowed_pct': 0.30,
        }
    
    # ===================== PRIVATE METHODS =====================
    
    @staticmethod
    def _calculate_kelly(
        win_rate: float,
        avg_win: float,
        avg_loss: float,
        rrr: float,
    ) -> float:
        """
        Kelly Criterion: f* = (bp - q) / b
        where b = odds (RRR), p = win rate, q = loss rate
        """
        if win_rate <= 0 or win_rate >= 1:
            return 0.0
        
        p = win_rate
        q = 1 - win_rate
        
        # Kelly formula: f = (p * avg_win - q * avg_loss) / avg_win
        numerator = (p * avg_win) - (q * avg_loss)
        denominator = avg_win
        
        kelly = numerator / denominator if denominator > 0 else 0
        
        # Clamp to valid range [0, 1]
        return max(0.0, min(kelly, 1.0))
    
    @staticmethod
    def _calculate_recovery_trades(drawdown_pct: float) -> int:
        """
        Calculate how many winning trades needed to recover from drawdown.
        """
        if drawdown_pct <= 0:
            return 0
        
        # Reverse: if we lost X%, we need (X% / (1 - X%)) to recover
        recovery_needed = drawdown_pct / (1 - drawdown_pct)
        
        # Assume 50% win rate -> need 2x recovery trades
        return int(np.ceil(recovery_needed * 2))
    
    @staticmethod
    def _validate_metrics(rrr: float, kelly: float, risk_pct: float) -> Optional[str]:
        """Validate risk metrics and return warnings"""
        warnings = []
        
        if rrr < 1.0:
            warnings.append("RRR < 1.0 (unfavorable risk/reward)")
        if kelly > 0.25:
            warnings.append("Kelly > 25% (aggressive sizing)")
        if risk_pct > 0.05:
            warnings.append("Risk > 5% per trade (high risk)")
        
        return " | ".join(warnings) if warnings else None


# ==================== FACTORY ====================

def get_risk_service(
    logger: Optional[logging.Logger] = None
) -> RiskManagementService:
    """Get RiskManagementService instance"""
    return RiskManagementService(logger=logger)
