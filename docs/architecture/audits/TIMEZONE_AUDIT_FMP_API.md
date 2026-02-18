# Timezone Audit Report - FMP API Calls

## ✅ Fixed Issues

### 1. **Historical Intraday Data** (Commit: d099380)
- **File**: `markettool/infra/fmp/client.py`
- **Method**: `historical_intraday()`
- **Fix**: Convert UTC timestamps to America/New_York before API call
- **Impact**: Critical - Affects all chart data and analysis

### 2. **Economic Calendar Events** (Commit: 5aa7284)
- **File**: `MarketTool.py`
- **Functions**: `obtener_dias_habiles_mercado()`, `_fmp_econ_fetch()`
- **Fix**: Convert UTC to America/New_York for date range calculations
- **Impact**: High - Affects event detection and fundamental analysis

## ⚠️ Potential Issues (Lower Priority)

### 3. **News API Endpoints**
- **File**: `MarketTool.py`
- **Functions**: 
  - `obtener_noticias()` (line 4563)
  - `obtener_noticias_simbolo()` (line 4664)
- **Endpoints**:
  - `/api/v4/forex_news`
  - `/api/v4/crypto_news`
  - `/api/v3/stock_news`
- **Current Behavior**: Passes dates in UTC format `%Y-%m-%d`
- **Risk**: Low - News has full timestamps and broader date ranges
- **Recommendation**: Monitor for missing news articles

### 4. **Historical EOD (End of Day)**
- **File**: `markettool/infra/fmp/client.py`
- **Method**: `historical_eod()`
- **Current Behavior**: Uses `strftime("%Y-%m-%d")` without timezone
- **Risk**: Low - EOD data is less time-sensitive (daily granularity)
- **Impact**: Minimal - Unlikely to cause issues except at market boundaries

## 🔍 Review Needed

### APIs That May Expect NY Timezone:
1. ✅ `/api/v3/historical-chart/{interval}/{symbol}` - **FIXED**
2. ✅ `/api/v3/economic_calendar` - **FIXED**
3. ⚠️ `/api/v4/forex_news` - **TO REVIEW**
4. ⚠️ `/api/v4/crypto_news` - **TO REVIEW**
5. ⚠️ `/api/v3/stock_news` - **TO REVIEW**
6. ⚠️ `/api/v3/historical-price-full/{symbol}` (EOD) - **TO REVIEW**

## 💡 Testing Recommendations

### Test Scenario 1: Events at Market Boundaries
```python
# Test at 05:00 UTC (00:00 ET)
# Should include events from "today" in ET, not UTC
```

### Test Scenario 2: Historical Data Near Market Open
```python
# Test requesting data from "last hour" during market open hours
# Verify candles start from correct time
```

### Test Scenario 3: Cross-Day Boundary
```python
# Test at 04:59 UTC (23:59 ET previous day)
# vs 05:01 UTC (00:01 ET new day)
# Verify event filtering matches ET day boundaries
```

## 📊 Verification Steps

1. **Deploy Updated Code**
   ```bash
   docker build -t markettool:latest .
   docker-compose restart
   ```

2. **Test Economic Events**
   ```bash
   # From container
   docker exec app1 python scripts/test_timezone_fix.py
   
   # Check logs for "[FMP-econ]" messages showing "(dates in NY timezone)"
   docker logs app1 | grep "FMP-econ"
   ```

3. **Monitor User Reports**
   - Events appearing on time
   - No missing economic data
   - Charts showing recent candles correctly

## 🎯 Root Cause Summary

**FMP API Timezone Behavior:**
- FMP APIs interpret date/datetime strings without explicit timezone as **America/New_York** time
- System was generating dates from **UTC** and converting to string without timezone info
- Result: 5-hour offset during EST (4-hour during EDT)

**Example of Bug:**
```
UTC Time: 2026-02-16 12:00:00 UTC
NY Time:  2026-02-16 07:00:00 ET

Request: "from=2026-02-16" (generated from UTC)
FMP interprets: "2026-02-16 00:00:00 ET" = "2026-02-16 05:00:00 UTC"
```

**Result:** Missing 5 hours of data (00:00-05:00 UTC)

## ✅ Solution Applied

Convert all datetime calculations to **America/New_York timezone BEFORE** formatting to string for FMP API:

```python
# BEFORE (BROKEN):
now_utc = datetime.now(timezone.utc)
date_str = now_utc.date().strftime("%Y-%m-%d")  # FMP misinterprets

# AFTER (FIXED):
now_utc = datetime.now(timezone.utc)
ny_tz = pytz.timezone("America/New_York")
now_ny = now_utc.astimezone(ny_tz)
date_str = now_ny.date().strftime("%Y-%m-%d")  # FMP interprets correctly
```

---

**Last Updated**: 2026-02-16
**Status**: 2/6 APIs fixed, 4 remaining under review
