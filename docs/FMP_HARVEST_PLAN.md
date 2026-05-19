# FMP historical and dashboard harvest plan

## What FMP Docs confirm

- Intraday OHLCV uses `https://financialmodelingprep.com/stable/historical-chart/{interval}?symbol=...`.
- Supported intraday intervals documented for the app scope: `1min`, `5min`, `15min`, `30min`, `1hour`, `4hour`.
- FMP recommends iterating by day-sized windows for intraday ranges longer than one month instead of requesting huge ranges.
- Endpoint timestamps follow the instrument exchange/region timezone. Docs explicitly mention forex uses EST; the backend should continue converting UTC requests to `America/New_York` for forex-style symbols.
- Real-time/batch quote endpoints exist for stocks, forex, crypto, commodities and indexes.
- Company dashboard data can be built from profile, quote, market cap, key metrics, ratios, financial scores, enterprise values and financial statements.

## Endpoint groups to persist

### Catalog

- `stock-list`
- `actively-trading-list`
- `available-exchanges`
- `forex-list`
- `cryptocurrency-list`
- `commodities-list`

Use this as the canonical symbol universe. Store raw catalog snapshots and derive curated symbol sets from them.

### Historical prices

- Stock EOD: `historical-price-eod/full`
- Intraday: `historical-chart/{interval}`
- Forex intraday: `historical-chart/{interval}` with forex symbols such as `EURUSD`
- Crypto intraday: `historical-chart/{interval}` with crypto symbols such as `BTCUSD`
- Commodities/index intraday: same interval endpoint, symbol-driven.

Persist by asset class, symbol, timeframe and window:

```text
fmp/history/{asset_class}/{symbol}/{timeframe}/{from}_{to}.json
```

### Company dashboard

- `profile`
- `quote`
- `market-cap`
- `historical-market-cap`
- `key-metrics-ttm`
- `ratios-ttm`
- `financial-scores`
- `enterprise-values`
- `income-statement`
- `balance-sheet-statement`
- `cash-flow-statement`

Persist:

```text
fmp/dashboard/company/{symbol}/{dataset}.json
```

### Bulk datasets

- `key-metrics-ttm-bulk`
- `income-statement-bulk`
- `balance-sheet-statement-bulk`
- `cash-flow-statement-bulk`

Use bulk endpoints for nightly company dashboards when possible; use per-symbol endpoints for targeted refresh or repair.

## Validation rules before promoting data

- Normalize timestamps to UTC internally, preserving source timezone metadata.
- Reject rows with missing/non-finite OHLC.
- Reject OHLC rows where `high < max(open, close)` or `low > min(open, close)`.
- Dedupe by timestamp keeping the newest response.
- Sort ascending by timestamp.
- For intraday, reject/cut rows outside the requested window with one bucket of tolerance.
- Track coverage ratio, gap count and largest gap in bars.
- For market calendars, do not mark weekends/closed sessions as gaps for stocks; forex/crypto need separate calendars.
- If existing GCS data has higher coverage and newer last timestamp, keep it. If the new data is more complete, atomically replace or write a new version then promote.

## Recommended rollout

1. Run `scripts/fmp_harvest.py catalog --no-dry-run` once to snapshot symbol universes.
2. Use curated symbol sets for known assets; avoid downloading every possible ticker at once.
3. For active app symbols, run `prices` in small windows and store local + GCS.
4. For dashboards, prefer bulk endpoints nightly, then targeted per-symbol repair.
5. Add a small admin dashboard over GCS manifests: rows, coverage, gaps, last update, source endpoint, and replacement decisions.

## Current tool

The first CLI lives at:

```text
scripts/fmp_harvest.py
```

Dry-run example:

```bash
python3 scripts/fmp_harvest.py --api-key "$FMP_API_KEY" prices \
  --symbols AAPL,MSFT \
  --asset-class stock \
  --interval 1min \
  --from-date 2026-05-19T14:00:00+00:00 \
  --to-date 2026-05-19T15:00:00+00:00 \
  --max-bars 30
```

Actual GCS-backed run:

```bash
python3 scripts/fmp_harvest.py --no-dry-run --bucket "$FMP_HARVEST_GCS_BUCKET" prices \
  --symbols AAPL,MSFT \
  --asset-class stock \
  --interval 1min \
  --from-date 2026-05-19T14:00:00+00:00 \
  --to-date 2026-05-19T15:00:00+00:00 \
  --max-bars 240
```
