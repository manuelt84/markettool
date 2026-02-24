# Backtesting Outcomes Configuration Guide

## Overview

The backtesting outcomes feature enables detailed TP/SL hit analysis for past entries by iterating through historical candles. This provides rich metrics for strategy evaluation and optimization.

**Status**: Optional (Disabled by default for performance)

---

## Environment Variables

### `BACKTEST_OUTCOMES_ENABLED`

Controls whether backtesting outcome calculations are performed.

| Variable | Value | Behavior |
|----------|-------|----------|
| `BACKTEST_OUTCOMES_ENABLED` | `true` | ✅ Calculate outcomes (computationally expensive) |
| `BACKTEST_OUTCOMES_ENABLED` | `false` (default) | ⏭️ Skip outcomes (fast, minimal processing) |

**Example:**
```bash
# Enable backtesting outcomes
export BACKTEST_OUTCOMES_ENABLED=true

# Disable (default)
export BACKTEST_OUTCOMES_ENABLED=false
```

### `BACKTEST_MAX_LOOKBACK_CANDLES`

Limits historical candle iterations for performance.

| Variable | Default | Range | Behavior |
|----------|---------|-------|----------|
| `BACKTEST_MAX_LOOKBACK_CANDLES` | `200` | 50-1000 | Max candles analyzed per entry |

**Example:**
```bash
# Limit to 500 candles per entry (slower but more thorough)
export BACKTEST_MAX_LOOKBACK_CANDLES=500

# Limit to 100 candles per entry (faster)
export BACKTEST_MAX_LOOKBACK_CANDLES=100
```

---

## Performance Impact

### Scenario 1: Disabled (Default)
```
Time to process 100 entries: ~50ms
Memory overhead: Minimal
Features: Basic entry calculation only
```

### Scenario 2: Enabled - Standard Settings
```
Time to process 100 entries: ~500-800ms (10x slower)
Memory overhead: ~5-10MB per symbol
Features: Complete outcome analysis with TP/SL metrics
```

### Scenario 3: Enabled - Aggressive Lookback
```
Time to process 100 entries: ~1-2s (20x slower)
Memory overhead: ~20-30MB per symbol
Features: Extended outcome analysis, deeper history
```

---

## Usage in Code

### Basic Usage - Without Outcomes

```python
from markettool.application.use_cases import get_calculate_entries_use_case

use_case = get_calculate_entries_use_case()

result = await use_case.execute(
    df=df_ohlcv,
    df_eventos=df_events,
    symbol='EURUSD',
    timeframe='1h',
    config={'risk_per_trade': 0.02}
)

# result contains entry signals WITHOUT outcome data
print(result['tipo_operacion'])  # 'Compra', 'Venta', or 'Neutral'
```

### Advanced Usage - With Outcomes

```python
from markettool.application.use_cases import get_calculate_entries_use_case

# Only works if BACKTEST_OUTCOMES_ENABLED=true
use_case = get_calculate_entries_use_case()

# Step 1: Get entry signals
result = await use_case.execute(
    df=df_ohlcv,
    df_eventos=df_events,
    symbol='EURUSD',
    timeframe='1h',
)

# Step 2: Optionally enrich with outcome data
entries_list = [{
    'entry': 1.2000,
    'tp': 1.2500,
    'sl': 1.1900,
    'side': 'LONG',
    'createdAt': 1708640000000
}]

enriched_entries = use_case.enrich_entries_with_outcomes(
    entries=entries_list,
    df=df_ohlcv,
    symbol='EURUSD',
    timeframe='1h'
)

# enriched_entries now contains:
# {
#     'entry': 1.2000,
#     'tp': 1.2500,
#     'sl': 1.1900,
#     'side': 'LONG',
#     'outcome': 'tp',              # ← New
#     'outcomeAt': 1708641000000,   # ← New
#     'activatedAt': 1708640100000, # ← New
#     'barsToOutcome': 12,          # ← New
#     'maxProfitPct': 2.15,         # ← New
#     'maxLossPct': -0.45,          # ← New
# }
```

---

## Output Metrics

When enabled, each entry receives these additional fields:

| Field | Type | Description |
|-------|------|-------------|
| `outcome` | `str` | `'tp'` (take profit hit), `'sl'` (stop loss hit), or `'pending'` (neither hit) |
| `outcomeAt` | `int` | Unix timestamp (ms) when outcome occurred |
| `activatedAt` | `int` | Unix timestamp (ms) when entry was activated/triggered |
| `barsToOutcome` | `int` | Number of candles until TP/SL was hit |
| `maxProfitPct` | `float` | Maximum profit percentage reached before outcome |
| `maxLossPct` | `float` | Maximum loss percentage reached before outcome |

### Example Output
```json
{
  "entry": 1.2000,
  "tp": 1.2500,
  "sl": 1.1900,
  "side": "LONG",
  "source": "technical",
  "outcome": "tp",
  "outcomeAt": 1708641000000,
  "activatedAt": 1708640100000,
  "barsToOutcome": 12,
  "maxProfitPct": 2.15,
  "maxLossPct": -0.45
}
```

---

## Docker Configuration

### Enable in Docker

**docker-compose.yml:**
```yaml
services:
  market-tool:
    environment:
      - BACKTEST_OUTCOMES_ENABLED=true
      - BACKTEST_MAX_LOOKBACK_CANDLES=200
```

**Dockerfile:**
```dockerfile
ENV BACKTEST_OUTCOMES_ENABLED=true
ENV BACKTEST_MAX_LOOKBACK_CANDLES=200
```

### Kubernetes

**deployment.yaml:**
```yaml
spec:
  template:
    spec:
      containers:
      - name: market-tool
        env:
        - name: BACKTEST_OUTCOMES_ENABLED
          value: "true"
        - name: BACKTEST_MAX_LOOKBACK_CANDLES
          value: "200"
```

---

## Recommendations

### For Development / Analysis
```bash
# Enable full analysis with standard lookback
export BACKTEST_OUTCOMES_ENABLED=true
export BACKTEST_MAX_LOOKBACK_CANDLES=200
```
→ Good balance of detail and speed

### For Production (Real-Time Trading)
```bash
# Disable for speed
export BACKTEST_OUTCOMES_ENABLED=false
```
→ Minimal overhead, fast execution

### For Deep Historical Analysis
```bash
# Enable with extended lookback
export BACKTEST_OUTCOMES_ENABLED=true
export BACKTEST_MAX_LOOKBACK_CANDLES=500
```
→ Most thorough, slowest

### For Testing New Strategies
```bash
# Enable with moderate lookback
export BACKTEST_OUTCOMES_ENABLED=true
export BACKTEST_MAX_LOOKBACK_CANDLES=300
```
→ Detailed metrics, reasonable performance

---

## Implementation Details

### Architecture

The backtesting outcomes feature is implemented as a separate service:

```
MarketTool.py
  ↓
CalculateEntriesUseCase (calculate_entries.py)
  ├─ execute() → returns entry signals
  ├─ enrich_entries_with_outcomes() → adds outcome metrics
  ↓
BacktestingOutcomesService (backtesting_outcomes_service.py)
  ├─ compute_outcome() → calculates single entry outcome
  ├─ compute_outcomes_for_entries() → batch processing
  ↓
OutcomeResult (dataclass)
  └─ outcome, outcomeAt, activatedAt, barsToOutcome, maxProfitPct, maxLossPct
```

### Processing Flow

1. **Initialization**: Service checks `BACKTEST_OUTCOMES_ENABLED` env var
2. **Disabled**: Returns `OutcomeResult(outcome='pending')` immediately
3. **Enabled**:
   - Converts entries to dict format
   - Iterates through historical candles
   - Detects activation (price touches entry)
   - Searches for TP/SL hit
   - Tracks max profit/loss
   - Returns complete `OutcomeResult`

### Performance Optimization

- **Lazy Evaluation**: Only computes outcomes when explicitly called
- **Batch Processing**: Converts entire DataFrame once, reuses for all entries
- **Early Exit**: Stops iterating when TP/SL is detected
- **Lookback Limit**: Prevents excessive computation with `BACKTEST_MAX_LOOKBACK_CANDLES`
- **Candle Caching**: Converts DataFrame to list once for efficiency

---

## Troubleshooting

### Outcomes showing as 'pending'

**Possible causes:**
1. `BACKTEST_OUTCOMES_ENABLED` is `false` (check env vars)
2. Entry prices are invalid (NaN, Inf, None)
3. Insufficient historical data in DataFrame
4. Entry timestamp doesn't match any candles

**Solution:**
```bash
# Enable outcomes
export BACKTEST_OUTCOMES_ENABLED=true

# Verify setting
# In Python:
import os
print(os.environ.get('BACKTEST_OUTCOMES_ENABLED'))  # Should print: true
```

### Performance is slow

**Possible causes:**
1. `BACKTEST_MAX_LOOKBACK_CANDLES` is too high
2. Large number of entries being processed
3. Very long historical DataFrames

**Solution:**
```bash
# Reduce lookahead window
export BACKTEST_MAX_LOOKBACK_CANDLES=100

# Or disable entirely for production
export BACKTEST_OUTCOMES_ENABLED=false
```

### Memory usage is high

**Possible causes:**
1. Processing many entries with long history
2. `BACKTEST_MAX_LOOKBACK_CANDLES` is set very high

**Solution:**
```bash
# Reduce lookback and disable outcomes
export BACKTEST_OUTCOMES_ENABLED=false

# If needed, reduce max lookback
export BACKTEST_MAX_LOOKBACK_CANDLES=100
```

---

## Related Files

| File | Purpose |
|------|---------|
| `backtesting_outcomes_service.py` | Core service implementation |
| `calculate_entries.py` | Integration point (enrich_entries_with_outcomes method) |
| `__init__.py` | Service exports |
| This file | Configuration guide |

---

## Frontend Equivalence

This backend implementation mirrors the frontend's `buildBacktestEntries()` function:

| Feature | Frontend | Backend |
|---------|----------|---------|
| **Outcome Calculation** | `safeComputeOutcome(entry, series.slice(i))` | `compute_outcome(entry, candles)` |
| **TP/SL Detection** | Iterates candle by candle | Same approach |
| **Metrics** | outcome, outcomeAt, barsToOutcome | ✓ All included |
| **Lookback Limit** | N/A | `BACKTEST_MAX_LOOKBACK_CANDLES` |
| **Enable/Disable** | Always on | `BACKTEST_OUTCOMES_ENABLED` |

Both implementations ensure outcomes are only calculated from entry activation onwards, avoiding false positives from pre-activation candles.
