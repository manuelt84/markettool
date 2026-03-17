#!/usr/bin/env python3
"""
Parameter Optimization Script

Runs backtests with different trading signal thresholds to find optimal configuration.

Usage:
    python scripts/optimize_thresholds.py --symbols EURUSD,GBPUSD --timeframes 1hour,4hour --days 90
"""

import argparse
import logging
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from pathlib import Path
import json
import csv
from typing import Dict, List, Any

from markettool.application.services.backtesting_service import get_backtesting_service

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class ParameterOptimizer:
    """Runs parameter optimization using backtesting"""
    
    def __init__(self):
        self.backtest_service = get_backtesting_service(logger=logger)
    
    def load_historical_data(
        self,
        symbol: str,
        timeframe: str,
        days: int
    ) -> pd.DataFrame:
        """
        Load historical OHLCV data.
        
        This is a placeholder - in production, load from your data source.
        """
        logger.info(f"Loading {symbol} on {timeframe} for last {days} days")
        
        # Placeholder: Generate synthetic data for demonstration
        # In production, load from API or database
        dates = pd.date_range(
            datetime.now() - timedelta(days=days),
            datetime.now(),
            freq=self._get_freq(timeframe)
        )
        
        np.random.seed(42)
        closes = 1.0 + np.cumsum(np.random.randn(len(dates)) * 0.001)
        opens = closes + np.random.randn(len(dates)) * 0.0002
        highs = np.maximum(closes, opens) + np.abs(np.random.randn(len(dates)) * 0.0003)
        lows = np.minimum(closes, opens) - np.abs(np.random.randn(len(dates)) * 0.0003)
        volumes = np.random.randint(1000, 10000, len(dates))
        
        df = pd.DataFrame({
            'open': opens,
            'high': highs,
            'low': lows,
            'close': closes,
            'volume': volumes,
            'atr': np.mean(highs - lows),
        }, index=dates)
        
        logger.info(f"Loaded {len(df)} candles")
        return df
    
    def generate_signals(
        self,
        df: pd.DataFrame,
        symbol: str,
    ) -> List[Dict[str, Any]]:
        """
        Generate trading signals from dataframe.
        
        This is a placeholder - generate real signals from your analysis.
        """
        signals = []
        
        for i, (idx, row) in enumerate(df.iterrows()):
            # Placeholder: Random signals for demonstration
            if i % 5 == 0:
                probability = np.random.uniform(55, 85)
                direction = 'Compra' if probability > 70 else 'Venta'
                
                signals.append({
                    'timestamp': idx,
                    'symbol': symbol,
                    'direction': direction,
                    'probability': probability,
                })
        
        logger.info(f"Generated {len(signals)} signals for {symbol}")
        return signals
    
    def optimize_symbol_timeframe(
        self,
        symbol: str,
        timeframe: str,
        days: int,
    ) -> Dict[str, Any]:
        """Optimize thresholds for a specific symbol/timeframe combination"""
        logger.info(f"Optimizing {symbol}/{timeframe}")
        
        # Load data
        df = self.load_historical_data(symbol, timeframe, days)
        
        # Generate signals
        signals = self.generate_signals(df, symbol)
        
        if not signals:
            logger.warning(f"No signals generated for {symbol}/{timeframe}")
            return {
                'symbol': symbol,
                'timeframe': timeframe,
                'error': 'No signals generated',
            }
        
        # Optimize
        result = self.backtest_service.optimize_thresholds(
            df,
            signals,
            threshold_buy_range=(55, 80),
            threshold_sell_range=(20, 45),
            step=5,
        )
        
        if result['best_params'] is None:
            logger.warning(f"No valid parameters found for {symbol}/{timeframe}")
            return {
                'symbol': symbol,
                'timeframe': timeframe,
                'error': 'No valid parameters',
            }
        
        best = result['best_params']
        metrics = best['metrics']
        
        logger.info(
            f"Best config for {symbol}/{timeframe}: "
            f"Buy={best['threshold_buy']}, Sell={best['threshold_sell']}, "
            f"WR={metrics.win_rate:.1%}, PF={metrics.profit_factor:.2f}"
        )
        
        return {
            'symbol': symbol,
            'timeframe': timeframe,
            'threshold_buy': best['threshold_buy'],
            'threshold_sell': best['threshold_sell'],
            'win_rate': metrics.win_rate,
            'profit_factor': metrics.profit_factor,
            'sharpe_ratio': metrics.sharpe_ratio,
            'max_drawdown_pct': metrics.max_drawdown_pct,
            'total_trades': metrics.total_trades,
            'avg_win': metrics.average_win,
            'avg_loss': metrics.average_loss,
            'expectancy': metrics.expectancy,
            'total_combinations_tested': result['total_combinations_tested'],
        }
    
    def run_optimization(
        self,
        symbols: List[str],
        timeframes: List[str],
        days: int,
    ) -> List[Dict[str, Any]]:
        """Run optimization for multiple symbol/timeframe combinations"""
        results = []
        
        total_combinations = len(symbols) * len(timeframes)
        current = 0
        
        for symbol in symbols:
            for timeframe in timeframes:
                current += 1
                logger.info(f"[{current}/{total_combinations}] Optimizing {symbol}/{timeframe}")
                
                try:
                    result = self.optimize_symbol_timeframe(symbol, timeframe, days)
                    results.append(result)
                except Exception as e:
                    logger.error(f"Error optimizing {symbol}/{timeframe}: {e}", exc_info=True)
                    results.append({
                        'symbol': symbol,
                        'timeframe': timeframe,
                        'error': str(e),
                    })
        
        return results
    
    def save_results(
        self,
        results: List[Dict[str, Any]],
        output_path: str,
    ) -> None:
        """Save optimization results to CSV"""
        output_file = Path(output_path)
        output_file.parent.mkdir(parents=True, exist_ok=True)
        
        # Filter successful results
        successful = [r for r in results if 'error' not in r]
        
        if not successful:
            logger.warning("No successful optimizations to save")
            return
        
        # Get all keys
        fieldnames = list(successful[0].keys())
        
        # Write CSV
        with open(output_file, 'w', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(successful)
        
        logger.info(f"Results saved to {output_file}")
    
    @staticmethod
    def _get_freq(timeframe: str) -> str:
        """Convert timeframe string to pandas frequency"""
        mapping = {
            '1min': '1T',
            '5min': '5T',
            '15min': '15T',
            '1hour': '1H',
            '4hour': '4H',
            '1day': '1D',
            '1week': '1W',
        }
        return mapping.get(timeframe, '1H')


def main():
    parser = argparse.ArgumentParser(
        description='Optimize trading signal thresholds using backtesting'
    )
    
    parser.add_argument(
        '--symbols',
        required=True,
        help='Comma-separated list of symbols (e.g., EURUSD,GBPUSD,USDJPY)'
    )
    
    parser.add_argument(
        '--timeframes',
        required=True,
        help='Comma-separated list of timeframes (e.g., 1hour,4hour,1day)'
    )
    
    parser.add_argument(
        '--days',
        type=int,
        default=90,
        help='Number of historical days to use (default: 90)'
    )
    
    parser.add_argument(
        '--output',
        default='optimization_results.csv',
        help='Output CSV file for results (default: optimization_results.csv)'
    )
    
    args = parser.parse_args()
    
    # Parse symbols and timeframes
    symbols = [s.strip() for s in args.symbols.split(',')]
    timeframes = [t.strip() for t in args.timeframes.split(',')]
    
    logger.info(f"Starting optimization")
    logger.info(f"Symbols: {symbols}")
    logger.info(f"Timeframes: {timeframes}")
    logger.info(f"Historical days: {args.days}")
    
    # Run optimization
    optimizer = ParameterOptimizer()
    results = optimizer.run_optimization(symbols, timeframes, args.days)
    
    # Save results
    optimizer.save_results(results, args.output)
    
    # Print summary
    successful = [r for r in results if 'error' not in r]
    logger.info(f"\nOptimization complete!")
    logger.info(f"Successful: {len(successful)}/{len(results)}")
    
    if successful:
        logger.info("\nTop 5 configurations by Win Rate:")
        sorted_by_wr = sorted(successful, key=lambda x: x.get('win_rate', 0), reverse=True)
        for i, config in enumerate(sorted_by_wr[:5], 1):
            logger.info(
                f"{i}. {config['symbol']}/{config['timeframe']}: "
                f"Buy={config['threshold_buy']}, Sell={config['threshold_sell']}, "
                f"WR={config['win_rate']:.1%}, PF={config['profit_factor']:.2f}"
            )


if __name__ == '__main__':
    main()
