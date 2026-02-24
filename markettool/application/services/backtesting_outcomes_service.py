"""
Backtesting Outcomes Service

Calculates TP/SL outcomes for entries by iterating through historical candles.
Similar to frontend's safeComputeOutcome() but for backend batch processing.

Configurable via BACKTEST_OUTCOMES_ENABLED env var to control performance impact.
"""

import os
import logging
from typing import Dict, List, Any, Optional
pass
import pandas as pd
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# ========================================
# Configuration
# ========================================
BACKTEST_OUTCOMES_ENABLED = os.environ.get('BACKTEST_OUTCOMES_ENABLED', 'false').lower() == 'true'
BACKTEST_MAX_LOOKBACK_CANDLES = int(os.environ.get('BACKTEST_MAX_LOOKBACK_CANDLES', '200'))


@dataclass
class OutcomeResult:
    """Result of outcome calculation."""
    outcome: str  # 'tp', 'sl', 'pending'
    outcome_at: Optional[int] = None  # Timestamp when TP/SL was hit
    activated_at: Optional[int] = None  # Timestamp when entry was activated
    bars_to_outcome: Optional[int] = None  # Number of bars until TP/SL
    max_profit_pct: Optional[float] = None  # Maximum profit reached before TP/SL
    max_loss_pct: Optional[float] = None  # Maximum loss reached before TP/SL


class BacktestingOutcomesService:
    """
    Calculates TP/SL outcomes for past entries using historical candle data.
    
    This service enables detailed backtesting analysis by:
    1. Taking an entry specification (price, TP, SL, side)
    2. Iterating through historical candles from entry time onwards
    3. Detecting when TP or SL was first hit
    4. Tracking intermediate profit/loss metrics
    
    Can be disabled via BACKTEST_OUTCOMES_ENABLED env var for better performance.
    """
    
    def __init__(self, logger: Optional[logging.Logger] = None):
        self.logger = logger or logging.getLogger(__name__)
        self.enabled = BACKTEST_OUTCOMES_ENABLED
        
        if self.enabled:
            self.logger.info(
                "[BacktestingOutcomesService] ENABLED - Backtesting outcomes will be calculated"
            )
        else:
            self.logger.info(
                "[BacktestingOutcomesService] DISABLED - Set BACKTEST_OUTCOMES_ENABLED=true to enable"
            )
    
    def compute_outcome(
        self,
        entry_price: float,
        take_profit: float,
        stop_loss: float,
        side: str,  # 'LONG' or 'SHORT'
        candles: List[Dict[str, float]],
        entry_timestamp: Optional[int] = None,
    ) -> OutcomeResult:
        """
        Compute TP/SL outcome from historical candles.
        
        Args:
            entry_price: Entry price for the trade
            take_profit: Take profit level
            stop_loss: Stop loss level
            side: 'LONG' or 'SHORT'
            candles: List of candle dicts with keys: 
                    ['timestamp'|'time', 'open', 'high', 'low', 'close']
            entry_timestamp: Timestamp when entry was created (for filtering candles)
        
        Returns:
            OutcomeResult with outcome ('tp', 'sl', 'pending') and metrics
        """
        if not self.enabled:
            # Return pending when disabled
            return OutcomeResult(outcome='pending')
        
        if not candles:
            return OutcomeResult(outcome='pending')
        
        # Validate inputs
        if not all(isinstance(x, (int, float)) for x in [entry_price, take_profit, stop_loss]):
            self.logger.warning(f"Invalid prices: entry={entry_price}, tp={take_profit}, sl={stop_loss}")
            return OutcomeResult(outcome='pending')
        
        # Ensure side is uppercase
        side = (side or '').upper()
        if side not in ['LONG', 'SHORT']:
            self.logger.warning(f"Invalid side: {side}")
            return OutcomeResult(outcome='pending')
        
        activated_at = None
        outcome = 'pending'
        outcome_at = None
        bars_to_outcome = 0
        max_profit_pct = 0.0
        max_loss_pct = 0.0
        
        # Iterate through candles
        for bar_idx, candle in enumerate(candles):
            if bar_idx > BACKTEST_MAX_LOOKBACK_CANDLES:
                break  # Limit lookback for performance
            
            # Extract OHLC from candle (handle different key names)
            high = candle.get('high') or candle.get('h')
            low = candle.get('low') or candle.get('l')
            close = candle.get('close') or candle.get('c')
            candle_time = candle.get('timestamp') or candle.get('time') or candle.get('t')
            
            if not all([high, low, close]):
                continue
            
            # Filter by entry timestamp if provided
            if entry_timestamp and candle_time and candle_time < entry_timestamp:
                continue
            
            # === Activation Phase ===
            if activated_at is None:
                if side == 'LONG':
                    # Entry activated if price touches or goes below entry
                    if low <= entry_price:
                        activated_at = candle_time
                    else:
                        continue  # Not yet activated
                else:  # SHORT
                    # Entry activated if price touches or goes above entry
                    if high >= entry_price:
                        activated_at = candle_time
                    else:
                        continue  # Not yet activated
            
            # === Outcome Detection Phase ===
            # Only search for outcome after activation
            hit_tp = False
            hit_sl = False
            
            if side == 'LONG':
                # Check if TP was hit (high >= take_profit)
                if high >= take_profit:
                    hit_tp = True
                # Check if SL was hit (low <= stop_loss)
                if low <= stop_loss:
                    hit_sl = True
                
                # Track max profit/loss
                profit_pct = ((close - entry_price) / entry_price) * 100
                max_profit_pct = max(max_profit_pct, profit_pct)
                max_loss_pct = min(max_loss_pct, profit_pct)
            
            else:  # SHORT
                # Check if TP was hit (low <= take_profit)
                if low <= take_profit:
                    hit_tp = True
                # Check if SL was hit (high >= stop_loss)
                if high >= stop_loss:
                    hit_sl = True
                
                # Track max profit/loss
                profit_pct = ((entry_price - close) / entry_price) * 100
                max_profit_pct = max(max_profit_pct, profit_pct)
                max_loss_pct = min(max_loss_pct, profit_pct)
            
            # TP takes priority in the same bar (but SL might hit first chronologically)
            # We check both and return the first one hit
            if hit_tp:
                outcome = 'tp'
                outcome_at = candle_time
                bars_to_outcome = bar_idx
                break
            elif hit_sl:
                outcome = 'sl'
                outcome_at = candle_time
                bars_to_outcome = bar_idx
                break
        
        return OutcomeResult(
            outcome=outcome,
            outcome_at=outcome_at,
            activated_at=activated_at,
            bars_to_outcome=bars_to_outcome if bars_to_outcome > 0 else None,
            max_profit_pct=round(max_profit_pct, 4) if max_profit_pct != 0 else None,
            max_loss_pct=round(max_loss_pct, 4) if max_loss_pct < 0 else None,
        )
    
    def compute_outcomes_for_entries(
        self,
        entries: List[Dict[str, Any]],
        df: pd.DataFrame,
        symbol: str,
        timeframe: str,
    ) -> List[Dict[str, Any]]:
        """
        Batch compute outcomes for multiple entries.
        
        Args:
            entries: List of entry dicts with keys:
                    ['entry', 'tp', 'sl', 'side', 'createdAt']
            df: DataFrame with OHLCV data
            symbol: Trading symbol
            timeframe: Timeframe
        
        Returns:
            List of entries with outcome data added
        """
        if not self.enabled:
            self.logger.debug(f"[{symbol}/{timeframe}] Outcomes disabled - skipping")
            return entries
        
        if df.empty:
            self.logger.warning(f"Empty DataFrame for {symbol}/{timeframe}")
            return entries
        
        entries_out = []
        processed_count = 0
        
        # Convert DataFrame to list of candle dicts for efficiency
        candles = []
        for _, row in df.iterrows():
            candle = {
                'timestamp': int(row.name.timestamp() * 1000) if hasattr(row.name, 'timestamp') else None,
                'time': int(row.name.timestamp() * 1000) if hasattr(row.name, 'timestamp') else None,
                'open': float(row['open']),
                'high': float(row['high']),
                'low': float(row['low']),
                'close': float(row['close']),
            }
            candles.append(candle)
        
        for entry in entries:
            try:
                entry_price = float(entry.get('entry', 0))
                tp = float(entry.get('tp', 0))
                sl = float(entry.get('sl', 0))
                side = str(entry.get('side', 'LONG')).upper()
                created_at = entry.get('createdAt') or entry.get('created_at')
                
                # Skip if no valid prices
                if not all([entry_price, tp, sl]):
                    entries_out.append(entry)
                    continue
                
                # Compute outcome
                outcome_result = self.compute_outcome(
                    entry_price=entry_price,
                    take_profit=tp,
                    stop_loss=sl,
                    side=side,
                    candles=candles,
                    entry_timestamp=created_at,
                )
                
                # Add outcome data to entry
                entry_with_outcome = {
                    **entry,
                    'outcome': outcome_result.outcome,
                    'outcomeAt': outcome_result.outcome_at,
                    'activatedAt': outcome_result.activated_at,
                    'barsToOutcome': outcome_result.bars_to_outcome,
                    'maxProfitPct': outcome_result.max_profit_pct,
                    'maxLossPct': outcome_result.max_loss_pct,
                }
                
                entries_out.append(entry_with_outcome)
                processed_count += 1
            
            except Exception as e:
                self.logger.warning(f"Error computing outcome for entry: {e}")
                entries_out.append(entry)  # Return original entry on error
        
        self.logger.debug(
            f"[{symbol}/{timeframe}] Processed {processed_count}/{len(entries)} entries"
        )
        return entries_out


# ==================== FACTORY ====================

def get_backtesting_outcomes_service(
    logger: Optional[logging.Logger] = None
) -> BacktestingOutcomesService:
    """Get BacktestingOutcomesService instance."""
    return BacktestingOutcomesService(logger=logger)
