"""
Backtesting Service

Provides backtesting framework to validate trading signals and optimize parameters.
Supports:
- Historical signal generation
- Performance metrics calculation (Sharpe, Profit Factor, Win Rate, Max Drawdown)
- Parameter optimization
- Multi-timeframe analysis
"""

import logging
import pandas as pd
import numpy as np
from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


@dataclass
class BacktestTrade:
    """Represents a single trade in backtest"""
    symbol: str
    entry_price: float
    entry_time: datetime
    exit_price: float
    exit_time: datetime
    direction: str  # 'Compra' or 'Venta'
    profit_loss: float
    return_pct: float
    holding_bars: int
    

@dataclass
class BacktestMetrics:
    """Backtest performance metrics"""
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    total_profit: float
    total_loss: float
    gross_profit: float
    gross_loss: float
    profit_factor: float  # Gross profit / Gross loss
    average_win: float
    average_loss: float
    expectancy: float
    largest_win: float
    largest_loss: float
    consecutive_wins: int
    consecutive_losses: int
    sharpe_ratio: float
    max_drawdown: float
    max_drawdown_pct: float
    recovery_factor: float  # Total Profit / Max Drawdown
    

class BacktestingService:
    """
    Backtesting service for validating trading signals.
    """
    
    def __init__(self, logger: Optional[logging.Logger] = None):
        self.logger = logger or logging.getLogger(__name__)
    
    def simulate_trades(
        self,
        df: pd.DataFrame,
        signals: List[Dict[str, Any]],
        threshold_buy: float = 65,
        threshold_sell: float = 35,
        slippage: float = 0.0005,  # 0.05% slippage
    ) -> List[BacktestTrade]:
        """
        Simulate trades based on generated signals.
        
        Args:
            df: OHLCV DataFrame with index as datetime
            signals: List of signal dictionaries with keys: 'timestamp', 'direction', 'probability'
            threshold_buy: Probability threshold for buy signals
            threshold_sell: Probability threshold for sell signals
            slippage: Slippage as decimal (0-1)
        
        Returns:
            List of BacktestTrade objects
        """
        trades = []
        pending_entry = None
        
        for signal in signals:
            timestamp = signal.get('timestamp')
            direction = signal.get('direction')
            probability = signal.get('probability', 50)
            
            if pending_entry is None:
                # Look for entry signal
                if (direction == 'Compra' and probability > threshold_buy) or \
                   (direction == 'Venta' and probability > threshold_sell):
                    # Get entry price from candle
                    entry_candle = self._get_candle_at_time(df, timestamp)
                    if entry_candle is not None:
                        entry_price = entry_candle['close']
                        entry_price_with_slippage = entry_price * (1 + slippage if direction == 'Compra' else 1 - slippage)
                        
                        pending_entry = {
                            'symbol': signal.get('symbol', 'UNKNOWN'),
                            'entry_price': entry_price_with_slippage,
                            'entry_time': timestamp,
                            'entry_bar': len(trades),
                            'direction': direction,
                        }
            else:
                # Look for exit signal (opposite direction or time-based exit)
                if (pending_entry['direction'] == 'Compra' and direction == 'Venta') or \
                   (pending_entry['direction'] == 'Venta' and direction == 'Compra'):
                    
                    exit_candle = self._get_candle_at_time(df, timestamp)
                    if exit_candle is not None:
                        exit_price = exit_candle['close']
                        exit_price_with_slippage = exit_price * (1 + slippage if pending_entry['direction'] == 'Venta' else 1 - slippage)
                        
                        # Calculate P&L
                        if pending_entry['direction'] == 'Compra':
                            profit_loss = exit_price_with_slippage - pending_entry['entry_price']
                            return_pct = (exit_price_with_slippage / pending_entry['entry_price'] - 1) * 100
                        else:
                            profit_loss = pending_entry['entry_price'] - exit_price_with_slippage
                            return_pct = (pending_entry['entry_price'] / exit_price_with_slippage - 1) * 100
                        
                        trade = BacktestTrade(
                            symbol=pending_entry['symbol'],
                            entry_price=pending_entry['entry_price'],
                            entry_time=pending_entry['entry_time'],
                            exit_price=exit_price_with_slippage,
                            exit_time=timestamp,
                            direction=pending_entry['direction'],
                            profit_loss=profit_loss,
                            return_pct=return_pct,
                            holding_bars=len(trades) - pending_entry['entry_bar']
                        )
                        
                        trades.append(trade)
                        pending_entry = None
        
        return trades
    
    def calculate_metrics(
        self,
        trades: List[BacktestTrade],
        risk_free_rate: float = 0.02,
    ) -> BacktestMetrics:
        """
        Calculate comprehensive backtest metrics.
        
        Args:
            trades: List of BacktestTrade objects
            risk_free_rate: Annual risk-free rate for Sharpe calculation
        
        Returns:
            BacktestMetrics object
        """
        if not trades:
            return BacktestMetrics(
                total_trades=0,
                winning_trades=0,
                losing_trades=0,
                win_rate=0,
                total_profit=0,
                total_loss=0,
                gross_profit=0,
                gross_loss=0,
                profit_factor=0,
                average_win=0,
                average_loss=0,
                expectancy=0,
                largest_win=0,
                largest_loss=0,
                consecutive_wins=0,
                consecutive_losses=0,
                sharpe_ratio=0,
                max_drawdown=0,
                max_drawdown_pct=0,
                recovery_factor=0,
            )
        
        # Basic stats
        winning_trades = [t for t in trades if t.profit_loss > 0]
        losing_trades = [t for t in trades if t.profit_loss < 0]
        
        total_trades = len(trades)
        num_winning = len(winning_trades)
        num_losing = len(losing_trades)
        win_rate = num_winning / total_trades if total_trades > 0 else 0
        
        # P&L stats
        gross_profit = sum(t.profit_loss for t in winning_trades)
        gross_loss = abs(sum(t.profit_loss for t in losing_trades))
        total_profit = gross_profit - gross_loss
        
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else (gross_profit if gross_profit > 0 else 0)
        
        average_win = gross_profit / num_winning if num_winning > 0 else 0
        average_loss = gross_loss / num_losing if num_losing > 0 else 0
        
        expectancy = (win_rate * average_win) - ((1 - win_rate) * average_loss)
        
        largest_win = max([t.profit_loss for t in winning_trades]) if winning_trades else 0
        largest_loss = abs(min([t.profit_loss for t in losing_trades])) if losing_trades else 0
        
        # Streak stats
        consecutive_wins = self._calculate_consecutive_wins(trades)
        consecutive_losses = self._calculate_consecutive_losses(trades)
        
        # Drawdown stats
        returns = np.array([t.return_pct / 100 for t in trades])
        cumulative_returns = np.cumprod(1 + returns) - 1
        
        running_max = np.maximum.accumulate(cumulative_returns)
        drawdowns = (cumulative_returns - running_max) / (1 + running_max)
        
        max_drawdown = np.min(drawdowns) if len(drawdowns) > 0 else 0
        max_drawdown_pct = max_drawdown * 100
        
        # Sharpe ratio
        if len(returns) > 1:
            daily_returns = returns
            excess_returns = daily_returns - (risk_free_rate / 252)
            sharpe_ratio = np.mean(excess_returns) / np.std(excess_returns) * np.sqrt(252) if np.std(excess_returns) > 0 else 0
        else:
            sharpe_ratio = 0
        
        # Recovery factor
        recovery_factor = total_profit / abs(max_drawdown) if max_drawdown != 0 else 0
        
        return BacktestMetrics(
            total_trades=total_trades,
            winning_trades=num_winning,
            losing_trades=num_losing,
            win_rate=win_rate,
            total_profit=total_profit,
            total_loss=gross_loss,
            gross_profit=gross_profit,
            gross_loss=gross_loss,
            profit_factor=profit_factor,
            average_win=average_win,
            average_loss=average_loss,
            expectancy=expectancy,
            largest_win=largest_win,
            largest_loss=largest_loss,
            consecutive_wins=consecutive_wins,
            consecutive_losses=consecutive_losses,
            sharpe_ratio=sharpe_ratio,
            max_drawdown=max_drawdown,
            max_drawdown_pct=max_drawdown_pct,
            recovery_factor=recovery_factor,
        )
    
    def optimize_thresholds(
        self,
        df: pd.DataFrame,
        signals: List[Dict[str, Any]],
        threshold_buy_range: Tuple[float, float] = (55, 75),
        threshold_sell_range: Tuple[float, float] = (20, 40),
        step: float = 5,
    ) -> Dict[str, Any]:
        """
        Optimize buy/sell thresholds using backtest metrics.
        
        Args:
            df: OHLCV DataFrame
            signals: List of signal dictionaries
            threshold_buy_range: (min, max) for buy threshold
            threshold_sell_range: (min, max) for sell threshold
            step: Step size for parameter sweep
        
        Returns:
            Dict with best parameters and metrics
        """
        best_params = None
        best_profit_factor = 0
        results = []
        
        buy_thresholds = np.arange(threshold_buy_range[0], threshold_buy_range[1] + step, step)
        sell_thresholds = np.arange(threshold_sell_range[0], threshold_sell_range[1] + step, step)
        
        for buy_thresh in buy_thresholds:
            for sell_thresh in sell_thresholds:
                trades = self.simulate_trades(
                    df,
                    signals,
                    threshold_buy=buy_thresh,
                    threshold_sell=sell_thresh
                )
                
                metrics = self.calculate_metrics(trades)
                
                result = {
                    'threshold_buy': buy_thresh,
                    'threshold_sell': sell_thresh,
                    'trades': len(trades),
                    'win_rate': metrics.win_rate,
                    'profit_factor': metrics.profit_factor,
                    'sharpe_ratio': metrics.sharpe_ratio,
                    'max_drawdown_pct': metrics.max_drawdown_pct,
                    'total_profit': metrics.total_profit,
                }
                
                results.append(result)
                
                # Track best by profit factor
                if metrics.profit_factor > best_profit_factor:
                    best_profit_factor = metrics.profit_factor
                    best_params = {
                        'threshold_buy': buy_thresh,
                        'threshold_sell': sell_thresh,
                        'metrics': metrics,
                    }
        
        return {
            'best_params': best_params,
            'all_results': results,
            'total_combinations_tested': len(results),
        }
    
    # ==================== PRIVATE METHODS ====================
    
    @staticmethod
    def _get_candle_at_time(df: pd.DataFrame, timestamp: datetime) -> Optional[Dict]:
        """Get candle (row) at specific timestamp."""
        try:
            if isinstance(df.index, pd.DatetimeIndex):
                candle = df.loc[timestamp]
            else:
                # Fallback: search by column
                matching = df[df.index == timestamp]
                if matching.empty:
                    return None
                candle = matching.iloc[0]
            
            return {
                'open': candle['open'],
                'high': candle['high'],
                'low': candle['low'],
                'close': candle['close'],
            }
        except Exception:
            return None
    
    @staticmethod
    def _calculate_consecutive_wins(trades: List[BacktestTrade]) -> int:
        """Calculate maximum consecutive winning trades."""
        max_streak = 0
        current_streak = 0
        
        for trade in trades:
            if trade.profit_loss > 0:
                current_streak += 1
                max_streak = max(max_streak, current_streak)
            else:
                current_streak = 0
        
        return max_streak
    
    @staticmethod
    def _calculate_consecutive_losses(trades: List[BacktestTrade]) -> int:
        """Calculate maximum consecutive losing trades."""
        max_streak = 0
        current_streak = 0
        
        for trade in trades:
            if trade.profit_loss < 0:
                current_streak += 1
                max_streak = max(max_streak, current_streak)
            else:
                current_streak = 0
        
        return max_streak


# ==================== FACTORY ====================

def get_backtesting_service(
    logger: Optional[logging.Logger] = None
) -> BacktestingService:
    """Get BacktestingService instance"""
    return BacktestingService(logger=logger)
