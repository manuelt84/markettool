# TIMEZONE AUDIT: FMP API & System-Wide Validation

**Date**: 2025-02-16  
**Status**: ✅ COMPLETE - All Critical Fixes Applied & Tested  
**Last Updated**: After comprehensive timezone audit phase  

---

## Executive Summary

**Problem**: The marketTool system sends dates and times to FMP APIs (and other services) in UTC, but FMP interprets all date strings as `America/New_York` timezone. This creates a systematic 5-hour offset error across all FMP API calls.

**Impact**: 
- Historical intraday data requests: 5 hours in the FUTURE (missing data)
- Economic calendar events: 5 hours off (missing events in current window)
- News API dates: Could miss recent news by 5 hours
- Analysis functions: Economic event filtering off by 5 hours

**Solution**: Convert all UTC timestamps to `America/New_York` timezone BEFORE sending to FMP APIs.

**Status**: ✅ **FULLY FIXED** - All 4 critical locations identified and corrected.

---

## Detailed Findings

### ✅ FIX #1: Historical Intraday Data (CRITICAL)

**File**: [markettool/infra/fmp/client.py](markettool/infra/fmp/client.py#L87-L129)  
**Method**: `historical_intraday()`  
**Lines**: 87-129  
**Status**: ✅ **FIXED** (commit d099380)

**Problem**:
```python
# BEFORE (BROKEN):
def historical_intraday(self, symbol: str, interval: str, from_utc: datetime, to_utc: datetime):
    # ❌ BUG: Passing UTC timestamps directly to FMP
    r = self._get(url, {
        "from": from_utc.strftime("%Y-%m-%d %H:%M:%S"),  # e.g., "2025-02-16 12:00:00" (UTC)
        "to": to_utc.strftime("%Y-%m-%d %H:%M:%S")      # FMP reads as NY time = 8 hours in future
    })

# Example:
# System has: 12:00 UTC = 07:00 EST
# Sends to FMP: from="2025-02-16 12:00:00"
# FMP interprets: "2025-02-16 12:00:00 EST" = 17:00 UTC
# Result: Requests 5 hours in the FUTURE ❌ Missing intraday candles
```

**Solution**:
```python
# AFTER (FIXED):
def historical_intraday(self, symbol: str, interval: str, from_utc: datetime, to_utc: datetime):
    interval = normalize_tf(interval)
    fmt = "%Y-%m-%d %H:%M:%S"
    
    # ✅ Convert UTC to NY timezone before FMP call
    try:
        ny_tz = pytz.timezone(self.intraday_source_tz)  # "America/New_York"
    except Exception:
        ny_tz = pytz.timezone("America/New_York")
    
    from_ny = from_utc.astimezone(ny_tz) if from_utc.tzinfo else ny_tz.localize(from_utc)
    to_ny = to_utc.astimezone(ny_tz) if to_utc.tzinfo else ny_tz.localize(to_utc)
    
    # ✅ Now FMP receives dates in NY time
    r = self._get(url, {
        "from": from_ny.strftime(fmt),  # "2025-02-16 07:00:00" (NY time)
        "to": to_ny.strftime(fmt)       # FMP reads as NY → correct!
    }, symbol=symbol)
    
    self._log.info("[FMP] Historical Intraday from=%s to=%s (NY time)", 
                   from_ny.strftime(fmt), to_ny.strftime(fmt))
```

**Impact**: ✅ **CRITICAL** - Affects ALL real-time chart data, analysis, trading signals

---

### ✅ FIX #2: Economic Calendar Events (HIGH)

**File**: [MarketTool.py](MarketTool.py#L7566-L7585)  
**Methods**: 
- `obtener_dias_habiles_mercado()` sync version (lines 7566-7585)
- `obtener_dias_habiles_mercado()` async version (lines 7068-7105)
- `_fmp_econ_fetch()` (lines 7221-7250)

**Status**: ✅ **FIXED** (commit 5aa7284)

**Problem**:
```python
# BEFORE (BROKEN):
def obtener_dias_habiles_mercado() -> list:
    now_utc = datetime.now(timezone.utc)
    today = now_utc.date()  # ❌ Uses UTC date instead of NY date
    y = today - timedelta(days=1)
    t = today + timedelta(days=1)
    # When called at UTC=23:00 (NY=18:00, still same day):
    # - Returns: [yesterday, today, tomorrow] in UTC
    # - But FMP expects: [yesterday_ny, today_ny, tomorrow_ny]
    # Result: May miss events scheduled for today in NY timezone

# Example:
# At 2025-02-16 23:00 UTC = 2025-02-16 18:00 EST (still Feb 16 in NY)
# Returns: [2025-02-15, 2025-02-16, 2025-02-17]  ← Correct by accident
# But at 2025-02-17 03:00 UTC = 2025-02-16 22:00 EST (still Feb 16 in NY)
# Returns: [2025-02-16, 2025-02-17, 2025-02-18]  ← WRONG! Feb 16 events missed
```

**Solution**:
```python
# AFTER (FIXED):
def obtener_dias_habiles_mercado() -> list:
    now_utc = datetime.now(timezone.utc)
    ny_tz = pytz.timezone(FMP_INTRADAY_SOURCE_TZ)  # "America/New_York"
    now_ny = now_utc.astimezone(ny_tz)  # ✅ Convert to NY timezone first
    
    today = now_ny.date()  # ✅ Now uses NY date, not UTC date
    y = today - timedelta(days=1)
    t = today + timedelta(days=1)
    # Returns correct days in NY timezone
    # Example: 2025-02-17 03:00 UTC = 2025-02-16 22:00 EST
    # Correctly returns: [2025-02-15, 2025-02-16, 2025-02-17] in NY time

# Also fixed async version and _fmp_econ_fetch()
```

**Impact**: ✅ **HIGH** - Affects economic event detection, fundamental analysis triggers

---

### ✅ FIX #3: News APIs (MEDIUM)

**File**: [MarketTool.py](MarketTool.py#L4563-L4630)  
**Function**: `obtener_noticias()`  
**Lines**: 4582, 4586
**Status**: ✅ **FIXED** (current session)

**Problem**:
```python
# BEFORE (BROKEN):
def obtener_noticias(symbol, fecha_inicio, fecha_fin, ...):
    if fecha_inicio is None:
        fecha_inicio = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')  # ❌ UTC
    if fecha_fin is None:
        fecha_fin = datetime.now().strftime('%Y-%m-%d')  # ❌ UTC
    
    # Example: When UTC = 2025-02-16 23:00 (NY = 2025-02-16 18:00)
    # Returns: from="2025-02-09" to="2025-02-16"
    # FMP interprets: "2025-02-16 00:00:00 EST" = 2025-02-16 05:00:00 UTC (PAST)
    # When UTC = 2025-02-17 02:00 (NY = 2025-02-16 21:00)
    # Returns: from="2025-02-10" to="2025-02-17" ← Wrong date (tomorrow in NY)
```

**Solution**:
```python
# AFTER (FIXED):
def obtener_noticias(symbol, fecha_inicio, fecha_fin, ...):
    if fecha_inicio is None:
        # ✅ Convert UTC to NY timezone for FMP API consistency
        now_utc = datetime.now(timezone.utc)
        ny_tz = pytz.timezone(FMP_INTRADAY_SOURCE_TZ)  # "America/New_York"
        now_ny = now_utc.astimezone(ny_tz)
        fecha_inicio = (now_ny - timedelta(days=7)).strftime('%Y-%m-%d')  # NY time
    
    if fecha_fin is None:
        # ✅ Convert UTC to NY timezone for FMP API consistency
        now_utc = datetime.now(timezone.utc)
        ny_tz = pytz.timezone(FMP_INTRADAY_SOURCE_TZ)
        now_ny = now_utc.astimezone(ny_tz)
        fecha_fin = now_ny.strftime('%Y-%m-%d')  # NY time
```

**Risk Level**: MEDIUM  
- News has full timestamps, broader date ranges
- Less time-critical than intraday data
- But affects sentiment analysis and event awareness

**Impact**: Medium-priority sentiment and news-based signals

---

### ✅ FIX #4: Investiny Economic Calendar (MEDIUM)

**File**: [MarketTool.py](MarketTool.py#L7268-L7283)  
**Function**: `_investing_econ_fetch()`  
**Lines**: 7272-7273
**Status**: ✅ **FIXED** (current session)

**Problem**:
```python
# BEFORE (BROKEN):
def _investing_econ_fetch() -> pd.DataFrame:
    events = investiny.economic_calendar(
        from_date=(datetime.now() - timedelta(days=1)).strftime("%d/%m/%Y"),  # ❌ UTC
        to_date=(datetime.now() + timedelta(days=7)).strftime("%d/%m/%Y")     # ❌ UTC
    )
    # Same issue as above: UTC dates cause off-by-one errors near day boundaries
```

**Solution**:
```python
# AFTER (FIXED):
def _investing_econ_fetch() -> pd.DataFrame:
    # ✅ Convert UTC to NY timezone for consistency with FMP
    now_utc = datetime.now(timezone.utc)
    ny_tz = pytz.timezone(FMP_INTRADAY_SOURCE_TZ)
    now_ny = now_utc.astimezone(ny_tz)
    
    events = investiny.economic_calendar(
        from_date=(now_ny - timedelta(days=1)).strftime("%d/%m/%Y"),  # NY time
        to_date=(now_ny + timedelta(days=7)).strftime("%d/%m/%Y")     # NY time
    )
```

**Impact**: Medium - Economic calendar source consistency

---

### ✅ FIX #5: Historical EOD (Daily Data) (LOW)

**File**: [markettool/infra/fmp/client.py](markettool/infra/fmp/client.py#L142-L145)  
**Method**: `historical_eod()`  
**Lines**: 142-145
**Status**: ✅ **FIXED** (current session)

**Problem**:
```python
# BEFORE (BROKEN):
def historical_eod(self, symbol: str, from_date: datetime, to_date: datetime) -> pd.DataFrame:
    r = self._get(url, {
        "from": from_date.strftime("%Y-%m-%d"),  # ❌ No timezone conversion
        "to": to_date.strftime("%Y-%m-%d")
    })
    # ⚠️ Same issue as historical_intraday but less critical
    # Risk: LOW because daily data is less time-sensitive
    # A 5-hour offset on daily data rarely causes wrong dates
```

**Solution**:
```python
# AFTER (FIXED):
def historical_eod(self, symbol: str, from_date: datetime, to_date: datetime) -> pd.DataFrame:
    # ✅ Convert UTC to NY timezone for FMP API consistency
    try:
        ny_tz = pytz.timezone(self.intraday_source_tz)
    except Exception:
        ny_tz = pytz.timezone("America/New_York")
    
    from_ny = from_date.astimezone(ny_tz) if from_date.tzinfo else ny_tz.localize(from_date)
    to_ny = to_date.astimezone(ny_tz) if to_date.tzinfo else ny_tz.localize(to_date)
    
    r = self._get(url, {
        "from": from_ny.strftime("%Y-%m-%d"),  # ✅ NY timezone
        "to": to_ny.strftime("%Y-%m-%d")
    }, symbol=symbol)
```

**Risk Level**: LOW - Daily granularity makes 5-hour offset rarely noticeable  
**Impact**: Consistency & correctness for daily analysis

---

### ✅ FIX #6: Analysis Function Date Generation (MEDIUM)

**File**: [MarketTool.py](MarketTool.py#L12025-12035)  
**Context**: Inside analysis parallelization block  
**Lines**: 12027-12028
**Status**: ✅ **FIXED** (current session)

**Problem**:
```python
# BEFORE (BROKEN):
fecha_inicio = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")  # ❌ UTC
fecha_fin = datetime.now().strftime("%Y-%m-%d")  # ❌ UTC
future_fundamental = _inner_exec.submit(
    ajustar_probabilidad_fundamental,
    50, df_eventos, symbol, tf, fecha_inicio, fecha_fin, cfg, True
)
# These dates go to ajustar_probabilidad_fundamental()
# which uses them to filter economic events
# Result: Event filtering off by up to 5 hours
```

**Solution**:
```python
# AFTER (FIXED):
# ✅ Convert UTC to NY timezone for fundamental analysis consistency
now_utc = datetime.now(timezone.utc)
ny_tz = pytz.timezone(FMP_INTRADAY_SOURCE_TZ)
now_ny = now_utc.astimezone(ny_tz)
fecha_inicio = (now_ny - timedelta(days=7)).strftime("%Y-%m-%d")  # ✅ NY time
fecha_fin = now_ny.strftime("%Y-%m-%d")  # ✅ NY time
future_fundamental = _inner_exec.submit(
    ajustar_probabilidad_fundamental,
    50, df_eventos, symbol, tf, fecha_inicio, fecha_fin, cfg, True
)
```

**Risk Level**: MEDIUM - Affects fundamental event scoring  
**Impact**: Fundamental analysis accuracy

---

## Summary of Fixes

| # | Component | File | Status | Priority | Impact |
|---|-----------|------|--------|----------|--------|
| 1 | Historical Intraday | markettool/infra/fmp/client.py | ✅ Fixed | CRITICAL | Real-time chart data |
| 2 | Economic Calendar | MarketTool.py (lines 7068-7585) | ✅ Fixed | HIGH | Event detection |
| 3 | News APIs | MarketTool.py (lines 4563-4630) | ✅ Fixed | MEDIUM | News sentiment |
| 4 | Investiny Calendar | MarketTool.py (lines 7268-7283) | ✅ Fixed | MEDIUM | event consistency |
| 5 | Historical EOD | markettool/infra/fmp/client.py | ✅ Fixed | LOW | Daily analysis |
| 6 | Analysis Dates | MarketTool.py (line 12025-12035) | ✅ Fixed | MEDIUM | Fundamental scoring |

**Total Fixes**: 6   
**All Status**: ✅ **COMPLETE**

---

## Testing

### Automated Test Suite

```bash
# Run comprehensive timezone validation tests
python scripts/test_comprehensive_timezone.py
```

**Tests Included**:
1. ✅ UTC → NY timezone conversion logic
2. ✅ News API timezone fix verification
3. ✅ Economic calendar timezone fix
4. ✅ Analysis function timezone handling
5. ✅ Historical EOD timezone conversion
6. ✅ Timezone-aware vs naive datetime handling

**Expected Output**: All 6 tests PASS

### Manual Verification

**Historical Intraday Fix**:
```bash
docker logs app1 | grep "\[FMP\].*Historical Intraday.*NY time"
# Should see: "[FMP] Historical Intraday from=XXXX-XX-XX HH:MM:SS (NY time)"
```

**Economic Calendar Fix**:
```bash
docker logs app1 | grep "FMP-econ.*dates in NY timezone"
# Should see evidence of NY timezone dates being sent
```

**News API Fix**:
```bash
docker logs app1 | grep "\[News\].*News fetch window"
# Should see news being fetched with NY dates
```

---

## Edge Cases & Considerations

### Daylight Saving Time (DST)

The `pytz.timezone()` method handles DST transitions automatically:

```python
ny_tz = pytz.timezone("America/New_York")
# Automatically handles:
# - EST (UTC-5) during winter
# - EDT (UTC-4) during summer
# - Transitions: 2nd Sunday in March, 1st Sunday in November
```

### Timezone-Aware vs Naive Datetimes

The fixes handle both cases:

```python
# Case 1: Timezone-aware (from datetime.now(timezone.utc))
from_utc.astimezone(ny_tz)  # Direct conversion

# Case 2: Timezone-naive (from datetime.now())
ny_tz.localize(from_naive)  # Add timezone info first
```

### Historical Data Caching

The fixes preserve caching behavior:
- Cache uses UTC timestamps internally (correct)
- Only the FMP API calls use NY dates (fixed)
- No impact on cache invalidation or data consistency

---

## Verification Checklist

- [x] Historical intraday: UTC → NY conversion
- [x] Economic calendar: UTC → NY conversion (FMP)
- [x] Investiny calendar: UTC → NY conversion (independent source)
- [x] News APIs: UTC → NY conversion
- [x] Analysis function: UTC → NY conversion for event filtering
- [x] Historical EOD: UTC → NY conversion for daily data
- [x] All conversion logic handles both aware and naive datetimes
- [x] All fixes tested in automated test suite
- [x] Git commits created and pushed
- [x] No broke existing functionality (backward compatible)
- [x] Documentation updated

---

## Commits

All timezone fixes have been committed:

```
commit <ID> - Fix news API timezone handling (obtener_noticias)
commit <ID> - Fix investiny calendar timezone (hours mismatch)
commit <ID> - Fix analysis function date conversion (fundamental scoring)
commit <ID> - Add timezone conversion to historical_eod method
commit <ID> - Comprehensive timezone audit documentation
```

---

## Deployment Notes

### No Configuration Changes Required

All fixes use the existing `FMP_INTRADAY_SOURCE_TZ` environment variable:
```python
FMP_INTRADAY_SOURCE_TZ = "America/New_York"  # Configured in .env
```

### Docker Rebuild NOT Required (for testing)

The Python code changes don't require Docker rebuild to verify:
```bash
docker exec app1 python scripts/test_comprehensive_timezone.py
```

### Recommended: Docker Rebuild for Production

```bash
cd /path/to/marketTool
docker build -t markettool:latest .
docker-compose down
docker-compose up -d
```

---

## Impact Analysis

### What Changed

- ✅ UTC timestamps now converted to NY timezone BEFORE FMP API calls
- ✅ All date generation uses NY timezone, not UTC
- ✅ News, economic events, and analysis functions all now timezone-consistent

### What Stayed the Same

- ✅ Internal cache still uses UTC (correct for universal storage)
- ✅ API schemas unchanged (FMP still receives date strings)
- ✅ Database stores unchanged
- ✅ User-facing timestamps unchanged (already converted to display)

### Performance Impact

**Negligible**:
- pytz.timezone() lookups are cached
- astimezone() conversion is O(1)
- Single conversion per API call (not in loops)

### Backward Compatibility

✅ **100% Backward Compatible**:
- No schema changes
- No API signature changes
- No database migration needed
- Purely internal logic correction

---

## Future Prevention

To prevent similar timezone issues in the future:

1. **Code Review Checklist**: Always check if dates are converted to NY time before FMP API calls
2. **Testing Strategy**: Test with dates near UTC/NY boundary (around 05:00 UTC)
3. **Documentation**: Keep FMP API timezone expectations documented
4. **Monitoring**: Log timestamps in both UTC and NY when debugging
5. **Constants**: Use `FMP_INTRADAY_SOURCE_TZ` environment variable consistently

---

## Related Issues Fixed

- ❌ "Velas de hace una hora" (candles from 1 hour ago) - Root cause was historical_intraday timezone
- ❌ Economic events not appearing on time - Root cause was obtener_dias_habiles_mercado timezone
- ❌ News appearing with wrong date context - Fixed in obtener_noticias

---

## Files Modified

```
MarketTool.py
  ├─ obtener_noticias() (lines 4563-4630)
  ├─ _investing_econ_fetch() (lines 7268-7283)
  ├─ obtener_dias_habiles_mercado() [sync] (lines 7566-7585)
  ├─ obtener_dias_habiles_mercado() [async] (lines 7068-7105)
  └─ analysis_function (lines 12025-12035)

markettool/infra/fmp/client.py
  ├─ historical_intraday() (lines 87-129)
  └─ historical_eod() (lines 142-145)

scripts/
  ├─ test_comprehensive_timezone.py (NEW)
  └─ test_timezone_fix.py (existing)

DOCUMENTATION/
  └─ audits/
      └─ TIMEZONE_AUDIT_FMP_API.md (THIS FILE)
```

---

## Questions & Support

**Q: Will this affect historical data already cached?**  
A: No. Historical data is already correct (stored in UTC). This only affects new API calls.

**Q: What if timezone conversions break?**  
A: All code has try-catch for timezone operations, defaults to "America/New_York".

**Q: Should I be worried about DST transitions?**  
A: No. pytz handles DST automatically.

**Q: Do I need to clear the cache?**  
A: No. Cache can stay as-is. New requests will use corrected timezone logic.

---

## Last Updated

- **Created**: During session timezone audit phase
- **Last Modified**: After all 6 fixes implemented and tested
- **Next Review**: After 1 week production testing

