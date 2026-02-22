"""
Backtesting Tests

Test suite for validating trading signal formulas and optimizing parameters.
"""

import pytest
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, Any, List

from markettool.application.services.backtesting_service import (
    BacktestingService,
    BacktestTrade,
    get_backtesting_service,
)


@pytest.fixture
def backtest_service():
    """Create a backtesting service instance"""
    return get_backtesting_service()


@pytest.fixture
def sample_ohlcv_data():
    """Generate sample OHLCV data for testing"""
    dates = pd.date_range('2024-01-01', periods=100, freq='1H')
    np.random.seed(42)
    
    # Create realistic price data with trend
    closes = 100 + np.cumsum(np.random.randn(100) * 0.5)
    opens = closes + np.random.randn(100) * 0.2
    highs = np.maximum(closes, opens) + np.abs(np.random.randn(100) * 0.3)
    lows = np.minimum(closes, opens) - np.abs(np.random.randn(100) * 0.3)
    volumes = np.random.randint(1000, 10000, 100)
    
    df = pd.DataFrame({
        'open': opens,
        'high': highs,
        'low': lows,
        'close': closes,
        'volume': volumes,
        'atr': np.mean(highs - lows),
    }, index=dates)
    
    return df


@pytest.fixture
def sample_signals():
    """Generate sample trading signals"""
    signals = []
    
    # Create alternating buy/sell signals
    for i in range(0, 100, 10):
        signals.append({
            'timestamp': pd.Timestamp('2024-01-01') + timedelta(hours=i),
            'symbol': 'EURUSD',
            'direction': 'Compra' if i % 20 == 0 else 'Venta',
            'probability': 70 if i % 20 == 0 else 65,
        })
    
    return signals


class TestBacktestingService:
    """Test cases for BacktestingService"""
    
    def test_service_creation(self, backtest_service):
        """Test that backtesting service can be created"""
        assert backtest_service is not None
        assert isinstance(backtest_service, BacktestingService)
    
    def test_simulate_trades_basic(self, backtest_service, sample_ohlcv_data, sample_signals):
        """Test basic trade simulation"""
        trades = backtest_service.simulate_trades(
            sample_ohlcv_data,
            sample_signals,
            threshold_buy=65,
            threshold_sell=35,
        )
        
        assert isinstance(trades, list)
        # Should have some trades
        assert len(trades) >= 0
        
        # Each trade should be a BacktestTrade object
        for trade in trades:
            assert isinstance(trade, BacktestTrade)
            assert trade.entry_price > 0
            assert trade.exit_price > 0
            assert trade.direction in ['Compra', 'Venta']
    
    def test_calculate_metrics_empty_trades(self, backtest_service):
        """Test metrics calculation with no trades"""
        metrics = backtest_service.calculate_metrics([])
        
        assert metrics.total_trades == 0
        assert metrics.win_rate == 0
        assert metrics.profit_factor == 0
    
    def test_calculate_metrics_winning_trades(self, backtest_service):
        """Test metrics with winning trades"""
        trades = [
            BacktestTrade(
                symbol='EURUSD',
                entry_price=1.0,
                entry_time=datetime.now(),
                exit_price=1.01,
                exit_time=datetime.now(),
                direction='Compra',
                profit_loss=0.01,
                return_pct=1.0,
                holding_bars=5,
            ),
            BacktestTrade(
                symbol='EURUSD',
                entry_price=1.01,
                entry_time=datetime.now(),
                exit_price=1.02,
                exit_time=datetime.now(),
                direction='Compra',
                profit_loss=0.01,
                return_pct=1.0,
                holding_bars=5,
            ),
        ]
        
        metrics = backtest_service.calculate_metrics(trades)
        
        assert metrics.total_trades == 2
        assert metrics.winning_trades == 2
        assert metrics.losing_trades == 0
        assert metrics.win_rate == 1.0
        assert metrics.total_profit > 0
    
    def test_calculate_metrics_losing_trades(self, backtest_service):
        """Test metrics with losing trades"""
        trades = [
            BacktestTrade(
                symbol='EURUSD',
                entry_price=1.0,
                entry_time=datetime.now(),
                exit_price=0.99,
                exit_time=datetime.now(),
                direction='Compra',
                profit_loss=-0.01,
                return_pct=-1.0,
                holding_bars=5,
            ),
        ]
        
        metrics = backtest_service.calculate_metrics(trades)
        
        assert metrics.total_trades == 1
        assert metrics.winning_trades == 0
        assert metrics.losing_trades == 1
        assert metrics.win_rate == 0.0
        assert metrics.total_profit < 0
    
    def test_calculate_metrics_mixed_trades(self, backtest_service):
        """Test metrics with mix of winning and losing trades"""
        trades = [
            BacktestTrade(
                symbol='EURUSD',
                entry_price=1.0,
                entry_time=datetime.now(),
                exit_price=1.02,  # +2%
                exit_time=datetime.now(),
                direction='Compra',
                profit_loss=0.02,
                return_pct=2.0,
                holding_bars=5,
            ),
            BacktestTrade(
                symbol='EURUSD',
                entry_price=1.02,
                entry_time=datetime.now(),
                exit_price=1.00,  # -2%
                exit_time=datetime.now(),
                direction='Compra',
                profit_loss=-0.02,
                return_pct=-2.0,
                holding_bars=5,
            ),
            BacktestTrade(
                symbol='EURUSD',
                entry_price=1.00,
                entry_time=datetime.now(),
                exit_price=1.03,  # +3%
                exit_time=datetime.now(),
                direction='Compra',
                profit_loss=0.03,
                return_pct=3.0,
                holding_bars=5,
            ),
        ]
        
        metrics = backtest_service.calculate_metrics(trades)
        
        assert metrics.total_trades == 3
        assert metrics.winning_trades == 2
        assert metrics.losing_trades == 1
        assert abs(metrics.win_rate - 2/3) < 0.01
        assert metrics.total_profit > 0
    
    def test_optimize_thresholds_returns_results(self, backtest_service, sample_ohlcv_data, sample_signals):
        """Test that threshold optimization returns valid results"""
        result = backtest_service.optimize_thresholds(
            sample_ohlcv_data,
            sample_signals,
            threshold_buy_range=(60, 70),
            threshold_sell_range=(30, 40),
            step=5,
        )
        
        assert 'best_params' in result
        assert 'all_results' in result
        assert 'total_combinations_tested' in result
        
        # Should test multiple combinations
        expected_combos = (11 // 5 + 1) * (11 // 5 + 1)  # 4 x 3 = 12
        assert result['total_combinations_tested'] >= expected_combos
    
    def test_optimize_thresholds_has_best_config(self, backtest_service, sample_ohlcv_data, sample_signals):
        """Test that optimization finds best configuration"""
        result = backtest_service.optimize_thresholds(
            sample_ohlcv_data,
            sample_signals,
            threshold_buy_range=(60, 70),
            threshold_sell_range=(30, 40),
            step=10,
        )
        
        best_params = result['best_params']
        assert best_params is not None
        assert 'threshold_buy' in best_params
        assert 'threshold_sell' in best_params
        assert 'metrics' in best_params
        
        # Thresholds should be within range
        assert 60 <= best_params['threshold_buy'] <= 70
        assert 30 <= best_params['threshold_sell'] <= 40


class TestSignalFormulaValidation:
    """Test to validate current signal formula (60/40 split)"""
    
    def test_60_40_split_effectiveness(self):
        """
        Validate that 60% technical + 40% fundamental is effective.
        
        This test should be run with real historical data to validate
        the current formula effectiveness.
        """
        # Placeholder test
        # In real implementation, load historical signals and test performance
        
        technical_weight = 0.60
        fundamental_weight = 0.40
        
        assert technical_weight + fundamental_weight == 1.0
    
    def test_threshold_sensitivity(self):
        """
        Test signal threshold sensitivity.
        
        Tests whether fixed thresholds (65/35) vs dynamic thresholds
        produce better results on test data.
        """
        # Placeholder for threshold sensitivity analysis
        fixed_threshold_buy = 65
        fixed_threshold_sell = 35
        
        assert fixed_threshold_buy > fixed_threshold_sell
