Backtesting & Optimization Scripts
===================================

This directory contains utility scripts for validating and optimizing trading strategies.

## Quick Start

### 1. Run Backtesting Tests
```bash
cd c:\projects\marketTool
pytest tests/test_backtest.py -v
```

Expected output:
```
tests/test_backtest.py::test_backtest_service_creation PASSED
tests/test_backtest.py::test_simulate_basic_trades PASSED
tests/test_backtest.py::test_simulate_empty_trades PASSED
...
13 passed in 0.45s
```

### 2. Optimize Trading Parameters
```bash
python scripts/optimize_thresholds.py \
  --symbols EURUSD,GBPUSD \
  --timeframes 1hour,4hour,1day \
  --days 90 \
  --output optimization_results.csv
```

This will:
- Fetch historical data for each symbol/timeframe combination
- Run backtest with threshold combinations (55-75 buy, 20-40 sell, step=5)
- Save best parameters to CSV

Example output (optimization_results.csv):
```
symbol,timeframe,threshold_buy,threshold_sell,win_rate,profit_factor,sharpe_ratio,max_drawdown,total_trades
EURUSD,1hour,67,33,0.58,1.85,0.92,-12.5,245
EURUSD,4hour,68,32,0.61,2.1,1.05,-10.2,67
EURUSD,1day,70,30,0.63,2.3,1.2,-8.5,18
GBPUSD,1hour,66,34,0.56,1.72,0.81,-14.8,218
...
```

### 3. Analyze Results
```bash
# View optimization results
cat optimization_results.csv

# Sort by Profit Factor (best strategy)
sort -t',' -k6 -rn optimization_results.csv | head -10

# Filter for Sharpe Ratio > 1.0
grep -E ',[0-9]\.[0-9]{2},' optimization_results.csv
```

## Script Documentation

### optimize_thresholds.py

**Purpose**: Find optimal buy/sell thresholds for each symbol/timeframe combination

**Required Arguments**:
- `--symbols`: Comma-separated trading pairs (e.g., `EURUSD,GBPUSD,USDJPY`)
- `--timeframes`: Comma-separated timeframes (e.g., `1hour,4hour,1day`)

**Optional Arguments**:
- `--days`: Days of historical data (default: 30)
- `--output`: Output CSV path (default: `optimization_results.csv`)

**Example Workflows**:

```bash
# Quick test - 1 symbol, 1 timeframe, 7 days
python scripts/optimize_thresholds.py \
  --symbols EURUSD \
  --timeframes 1hour \
  --days 7 \
  --output quick_test.csv

# Comprehensive - All major pairs, all TFs, 90 days
python scripts/optimize_thresholds.py \
  --symbols EURUSD,GBPUSD,USDJPY,AUDUSD,NZDUSD \
  --timeframes 1hour,4hour,1day,1week \
  --days 90 \
  --output comprehensive_90day.csv

# Production - Get optimal params for deployment
python scripts/optimize_thresholds.py \
  --symbols EURUSD,GBPUSD \
  --timeframes 1hour,4hour \
  --days 60 \
  --output production_params.csv
```

**Output Metrics Explained**:

| Metric | Meaning | Good Value |
|--------|---------|------------|
| `win_rate` | % of winning trades | > 55% |
| `profit_factor` | Gross profit / Gross loss | > 1.5 |
| `sharpe_ratio` | Risk-adjusted returns | > 1.0 |
| `max_drawdown` | Largest peak-to-trough decline | > -20% bad |
| `total_trades` | Number of trades in period | > 30 meaningful |

**Interpreting Results**:

1. **High Profit Factor (>2.0)**: Strategy is very profitable
2. **Low Max Drawdown (<-10%)**: Good risk management
3. **Sharpe > 1.0**: Risk-adjusted returns are good
4. **Win Rate 55-65%**: Target zone (too high may indicate overfitting)
5. **Many trades (>100)**: Robust statistical significance

**Common Issues**:

1. **Script takes too long**
   - Reduce `--days` (default 30 is good for testing)
   - Use fewer symbols in first run
   - Check your internet (data fetching may be slow)

2. **Data is incomplete**
   - Ensure you have 1+ months of historical available
   - Check symbol spelling (EURUSD, not EUR/USD)
   - Verify timeframe format (1hour, 4hour, 1day, 1week)

3. **Results are inconsistent across runs**
   - This is normal - market data changes daily
   - Rerun after 1-2 weeks to validate trends
   - Use longer periods (90 days) for stability

## Using Results

### Deploy Optimized Parameters

Once you have optimization results, update `calculate_entries.py`:

**Option 1: Use Dynamic Thresholds (Current - Recommended)**
```python
# Keep the dynamic threshold logic - it adapts to volatility
# No code changes needed, it already uses ATR to adjust thresholds
```

**Option 2: Per-Timeframe Fixed Parameters**
```python
# If you want to hardcode optimized values:
OPTIMIZED_THRESHOLDS = {
    ('EURUSD', '1hour'): {'buy': 67, 'sell': 33},
    ('EURUSD', '4hour'): {'buy': 68, 'sell': 32},
    ('EURUSD', '1day'): {'buy': 70, 'sell': 30},
    ('GBPUSD', '1hour'): {'buy': 66, 'sell': 34},
}

# In _determine_signal():
key = (symbol, timeframe)
if key in OPTIMIZED_THRESHOLDS:
    threshold_buy = OPTIMIZED_THRESHOLDS[key]['buy']
    threshold_sell = OPTIMIZED_THRESHOLDS[key]['sell']
else:
    # Fallback to dynamic thresholds
    threshold_buy = 65 + (volatility_ratio - 1.0) * 5
```

### Monitor Performance

```bash
# Run optimization weekly
python scripts/optimize_thresholds.py \
  --symbols EURUSD,GBPUSD \
  --timeframes 1hour,4hour \
  --days 90 \
  --output weekly_optimization_$(date +%Y%m%d).csv

# Compare results over time
diff optimization_2024-02-01.csv optimization_2024-02-08.csv
```

## Testing

### Run Unit Tests
```bash
pytest tests/test_backtest.py -v
pytest tests/test_backtest.py::test_calculate_metrics -v  # Single test
pytest tests/test_backtest.py -k "metric" -v              # Tests matching keyword
```

### Run with Coverage
```bash
pytest tests/test_backtest.py --cov=markettool.application.services.backtesting_service
```

## Troubleshooting

### ModuleNotFoundError
```
ModuleNotFoundError: No module named 'markettool'
```
**Solution**: Run from project root:
```bash
cd c:\projects\marketTool
python scripts/optimize_thresholds.py ...
```

### Data Loading Errors
```
Error fetching data for EURUSD/1hour
```
**Solution**: Check internet connection, verify symbol format, reduce date range

### Timeout Errors
```
Timeout waiting for data...
```
**Solution**: Reduce `--days` or `--symbols` count, increase timeout:
```bash
# Modify scripts/optimize_thresholds.py
# Change: self.timeout = 30  →  self.timeout = 60
```

## Performance Benchmarks

**Expected Runtimes** (on modern CPU, good internet):

```
1 symbol, 1 TF, 7 days:   ~15 seconds
1 symbol, 4 TF, 30 days:  ~90 seconds
5 symbols, 4 TF, 30 days: ~5-7 minutes
5 symbols, 4 TF, 90 days: ~15-20 minutes
```

To speed up:
1. Parallel optimization (run multiple symbols in separate terminals)
2. Reduce days (7 days vs 90 days = 90% faster)
3. Use fewer symbols in first pass

## Integration

### In Your Trading Bot
```python
# After optimization, load best parameters
import pandas as pd

results = pd.read_csv('optimization_results.csv')

# Get best strategy overall (by Sharpe Ratio)
best = results.nlargest(1, 'sharpe_ratio').iloc[0]
print(f"Best strategy: {best['symbol']}/{best['timeframe']}")
print(f"  Buy threshold: {best['threshold_buy']}")
print(f"  Sell threshold: {best['threshold_sell']}")
print(f"  Expected Sharpe: {best['sharpe_ratio']:.2f}")
```

## Further Development

### Planned Enhancements
- [ ] Monte Carlo optimization for robustness
- [ ] Walk-forward analysis for out-of-sample validation
- [ ] Parameter stability analysis (sensitivity)
- [ ] Parallel optimization (run multiple symbols simultaneously)
- [ ] Real-time performance tracking
- [ ] Autonomous reoptimization (weekly scheduled)

## Support

For issues or questions:
1. Check this file's Troubleshooting section
2. Review the BACKEND_OPTIMIZATION_REPORT.md
3. Check test output: `pytest tests/test_backtest.py -v`
