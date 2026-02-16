# Performance Validation Guide

## How to Verify Optimizations in Production

### 1. Metrics to Monitor

#### Primary Metrics (per analysis)
```
analysis_latency_ms             # Total time for procesar_simbolo_temporalidad()
fmp_calls_count                 # Number of FMP API calls per analysis
firestore_queries_count         # Number of Firestore get() calls
quote_cache_hit_rate            # % of quote lookups served from cache
history_cache_hit_rate          # % of history loads from cache (not FMP)
indicators_cache_hit_rate       # % of indicators from cache (not recalculated)
```

#### Secondary Metrics
```
cold_start_latency_ms           # First analysis (always slow)
warm_start_latency_ms           # Subsequent analyses (should be 8-12s)
cache_memory_usage_mb           # In-memory cache size
fmp_api_latency_p95_ms          # 95th percentile FMP response time
```

### 2. Log Indicators (Search for these in logs)

#### Success Indicators
```bash
# Look for these log prefixes showing cache hits:
"[CACHE-FIRST]"         # Cache fresh, skipped FMP  ✅
"[FMP-DEDUP]"           # Duplicate fetch prevented ✅
"[Indicators].*hit"     # Indicator cache hit      ✅
"[HIST][RETURN]"        # History retrieved        ✅
```

#### Warning Indicators (Investigate if frequent)
```bash
"[INTEGRIDAD]"          # Cache corruption detected ❌
"[VALIDACIÓN].*no válidos" # Data quality issues   ❌
"Error validando OHLCV"  # Data quality checks fail ❌
```

### 3. Baseline Measurements

#### Before Optimizations (Reference)
```
Cold start:     ~35-45 seconds
Warm start:     ~30-40 seconds
FMP calls:      3-5 per analysis
Firestore:      16 queries per analysis
Quote calls:    6-8 per analysis
```

#### After Optimizations (Expected)
```
Cold start:     ~12-18 seconds
Warm start:     ~8-12 seconds
FMP calls:      1-2 per analysis (cache-first skips)
Firestore:      0-2 queries per analysis (only on first load)
Quote calls:    1-2 per analysis (10s cache)
```

### 4. Testing Procedure

#### Step 1: Clear Cache (Cold Start Test)
```bash
# Remove local history cache
rm -rf historicos/*.json

# Clear GCS cache (if accessible)
gsutil rm gs://your-bucket/cache/*

# Restart service/pod
```

Then analyze a symbol. Expected: 12-18 seconds.

#### Step 2: Warm Start Test
```bash
# Analyze same symbol again immediately
# Expected: 8-12 seconds

# Analyze a different symbol
# Expected: 8-12 seconds (uses fresh FMP data but shares indicators)

# Analyze again within TTL
# Expected: 6-10 seconds (everything cached)
```

#### Step 3: Cache-First Validation
```bash
# For 1hour+ TFs, wait less than TTL and reanalyze
# Example: Analyze EURUSD/1hour at 12:00
#         Reanalyze same at 12:45 (within 60m TTL)

# Expected: Should see "[CACHE-FIRST] ... SKIPPING FMP"
```

#### Step 4: Deduplication Test
```bash
# Analyze 10 symbols in parallel
# Expected: One FMP call per symbol-TF combo
#          [FMP-DEDUP] messages for concurrent workers
```

### 5. Automated Health Check Script

Create a monitoring script:

```python
#!/usr/bin/env python3
import logging
import time
from datetime import datetime
from marketTool import procesar_simbolo_temporalidad

symbols_tfs = [
    ("EURUSD", "1hour"),
    ("GBPUSD", "4hour"),
    ("GOLD", "1day"),
]

metrics = {}

for symbol, tf in symbols_tfs:
    start = time.time()
    
    try:
        result = procesar_simbolo_temporalidad(
            symbol, tf, 
            df_eventos=None, 
            user_chat_id="health_check"
        )
        elapsed_ms = (time.time() - start) * 1000
        
        metrics[f"{symbol}/{tf}"] = {
            "status": "OK",
            "latency_ms": elapsed_ms,
            "has_result": result is not None,
            "timestamp": datetime.now().isoformat()
        }
        
        # Success criteria
        if elapsed_ms > 15000:
            print(f"⚠️  SLOW: {symbol}/{tf} took {elapsed_ms:.0f}ms")
        else:
            print(f"✅ OK: {symbol}/{tf} in {elapsed_ms:.0f}ms")
            
    except Exception as e:
        metrics[f"{symbol}/{tf}"] = {
            "status": "FAIL",
            "error": str(e),
            "timestamp": datetime.now().isoformat()
        }
        print(f"❌ FAIL: {symbol}/{tf} - {e}")

# Output metrics for monitoring system
import json
print("\n" + json.dumps(metrics, indent=2))
```

### 6. Performance Degradation Alerts

Set up alerts for:

```
if analysis_latency_ms > 20000 {
    alert("Performance degradation: Analysis slow")
}

if fmp_calls_count > 5 {
    alert("FMP calls too high: Cache may be corrupted or skipped")
}

if firestore_queries_count > 5 {
    alert("Firestore queries too high: History validation broken")
}

if quote_cache_hit_rate < 30% {
    alert("Quote cache low hit rate: TTL too short or cache cleared")
}
```

### 7. Regression Testing Checklist

Before deploying, verify:

- [ ] Cold start: 12-18s
- [ ] Warm start: 8-12s  
- [ ] FMP calls: 1-2 per analysis
- [ ] Firestore queries: 0-2 per analysis
- [ ] Quote cache hits: >50%
- [ ] No "[INTEGRIDAD]" errors
- [ ] Concurrent analysis: locks work (dedup prevent concurrent calls)
- [ ] Different TFs: respect TTL boundaries
- [ ] Long-running analysis: no memory leaks
- [ ] Pod restart: cache rebuilds properly

### 8. Log Examples (Expected Output)

#### Good (Optimized)
```
[2024-01-15 10:30:45] [CACHE-FIRST] EURUSD/1hour: cache age=45min < ttl=60min - SKIPPING FMP
[2024-01-15 10:30:46] [FMP-DEDUP] GBPUSD/4hour: Previous worker ahead fetched (lock), skipping FMP
[2024-01-15 10:30:47] [Indicators] EURUSD/1hour: Cache hit (age=0.5h, 0ms, source=memory)
[2024-01-15 10:30:48] ✅ Analysis EURUSD/1hour completed in 8.2s
```

#### Bad (Requires Investigation)
```
[2024-01-15 10:30:45] [INTEGRIDAD] calcular_indicadores failed: faltan columnas
[2024-01-15 10:30:46] [VALIDACIÓN] EURUSD/1hour: Datos OHLCV no válidos
[2024-01-15 10:30:47] obtener_datos_con_hilos falló: Datos históricos no disponibles
[2024-01-15 10:30:50] ❌ Analysis EURUSD/1hour FAILED in 23.4s
```

### 9. Production Rollout Plan

1. **Monitor Phase (24 hours)**
   - Deploy to staging
   - Run health checks every 5 minutes
   - Compare latencies vs baseline
   
2. **Canary Phase (24-48 hours)**
   - Deploy to 10% of production pods
   - Monitor error rates, latencies
   - Compare against 90% old version
   
3. **Full Rollout (if metrics good)**
   - Deploy to 100% of pods
   - Continue monitoring for 1 week
   - Adjust TTL/concurrency based on real patterns

### 10. Troubleshooting Guide

#### Problem: Latency still 30s+
**Check:**
- [ ] `HISTORY_REFRESH_TTL_MINUTES` correct? Compare against actual age
- [ ] Firestore still slow? Check if TTL is 0 (fetching every time)
- [ ] FMP API down? Check FMP response times in logs
- [ ] Cache files corrupted? Delete and restart

#### Problem: Indicators missing
**Check:**
- [ ] `[INTEGRIDAD]` errors in logs?
- [ ] Delete local cache: `rm -rf historicos/`
- [ ] Verify GCS access: `gsutil ls gs://bucket/cache/`
- [ ] Check `OHLCV_VALIDATE_SAMPLE_N` not set too high

#### Problem: Quote cache not working
**Check:**
- [ ] `HISTORY_QUOTE_CACHE_SECONDS` > 0?
- [ ] Multiple servers? Cache is per-pod (not shared)
- [ ] High quote call frequency normal (6 symbols × 1s apart)

---

**For Support:** Check logs with `grep -E "\[CACHE|FMP-DEDUP|INTEGRIDAD|VALIDACIÓN\]"`
