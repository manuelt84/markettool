# FLUJO COMPLETO DE CACHÉ - MarketTool
## Todas las Capas Identificadas

## 🎯 RESUMEN EJECUTIVO

**TOTAL DE CAPAS DE CACHÉ: 5 (¡no 3!)**

```
┌─────────────────────────────────────────────────────────────┐
│ Tier-0: Redis (NEW)                                         │
│         Ultra-rápido (<50ms), volátil, distribuido          │
│         Configurable via CACHE_STRATEGY                     │
└─────────────────────────────────────────────────────────────┘
                         ↓ (si cache miss)
┌─────────────────────────────────────────────────────────────┐
│ Tier-1: LazyHistoricosLoader (Memory LRU Cache)             │
│         En proceso Python (LRU cache), muy rápido (<5ms)    │
│         Se pierde al reiniciar el proceso                   │
└─────────────────────────────────────────────────────────────┘
                         ↓ (si cache miss)
┌─────────────────────────────────────────────────────────────┐
│ Tier-2: Local Files (historicos/*.json)                     │
│         Disco local, rápido (<50ms read)                    │
│         Con FRESHNESS CHECK por timeframe (time-aware)      │
└─────────────────────────────────────────────────────────────┘
                         ↓ (si stale o missing)
┌─────────────────────────────────────────────────────────────┐
│ Tier-3: GCS (Google Cloud Storage)                          │
│         Persistencia en la nube (<500ms)                    │
│         Compartido entre todos los pods                     │
└─────────────────────────────────────────────────────────────┘
                         ↓ (si cache miss)
┌─────────────────────────────────────────────────────────────┐
│ Tier-4: FMP API (Financial Modeling Prep)                   │
│         Fuente de datos principal (1-5s)                    │
│         Rate limited, lento, costoso                        │
└─────────────────────────────────────────────────────────────┘
```

---

## 📊 FLUJO DETALLADO POR TIPO DE DATOS

### 1️⃣ DATOS OHLCV (Históricos de Precios)

#### Función: `obtener_datos_con_hilos(symbol, tf)`

```python
┌──────────────────────────────────────────────────────────────┐
│ REQUEST: EURUSD, 1hour                                       │
└──────────────────────────────────────────────────────────────┘
                         ↓
┌──────────────────────────────────────────────────────────────┐
│ TIER-0: Redis (OHLCVRedisCache)                              │
│ Key: "ohlcv:EURUSD:1hour:2026-02-27"                        │
│ TTL: 1 hora (según timeframe)                               │
│                                                              │
│ IF HIT:   Return data (<50ms) ✅                            │
│ IF MISS:  Continue to Tier-1 ↓                             │
└──────────────────────────────────────────────────────────────┘
                         ↓
┌──────────────────────────────────────────────────────────────┐
│ TIER-1: LazyHistoricosLoader (Memory LRU)                   │
│ Location: _LAZY_HIST_LOADER.get(symbol, tf)                 │
│ Storage: Dict in Python process memory                      │
│ Speed: <5ms                                                  │
│                                                              │
│ IF HIT:   Return data ✅                                    │
│ IF MISS:  Continue to Tier-2 ↓                             │
└──────────────────────────────────────────────────────────────┘
                         ↓
┌──────────────────────────────────────────────────────────────┐
│ TIER-2: Local Files (with FRESHNESS CHECK)                  │
│ File: historicos/EURUSD__1hour.json                         │
│ Check: _is_cache_fresh(file, "1hour")                       │
│                                                              │
│ Freshness Thresholds (by timeframe):                        │
│   1min:  5 min    (scalping needs fresh data)               │
│   5min:  10 min                                             │
│   15min: 30 min                                             │
│   30min: 1 hour                                             │
│   1hour: 2 hours  ← Este caso                              │
│   4hour: 4 hours                                            │
│   1day:  12 hours (very tolerant)                           │
│   1week: 24 hours (very tolerant)                           │
│                                                              │
│ IF FRESH: Return data + Update LazyLoader ✅               │
│ IF STALE: Log "Local stale, checking GCS" ↓                │
│ IF MISS:  Continue to Tier-3 ↓                             │
└──────────────────────────────────────────────────────────────┘
                         ↓
┌──────────────────────────────────────────────────────────────┐
│ TIER-3: GCS (Google Cloud Storage)                          │
│ Bucket: market-tool-historical-data                         │
│ Path: historicos/EURUSD_1hour.parquet                       │
│ Speed: 300-500ms                                             │
│                                                              │
│ IF HIT:                                                      │
│   1. Return data                                            │
│   2. Update Local File (_save_local_history_df)            │
│   3. Update LazyLoader                                      │
│   4. Return ✅                                              │
│ IF MISS:  Continue to Tier-4 ↓                             │
└──────────────────────────────────────────────────────────────┘
                         ↓
┌──────────────────────────────────────────────────────────────┐
│ TIER-4: FMP API (Financial Modeling Prep)                   │
│ Endpoint: /api/v3/historical-chart/1hour/EURUSD             │
│ Speed: 1-5 seconds                                           │
│ Rate Limits: 250 req/day (free), 300 req/min (premium)     │
│                                                              │
│ Strategy:                                                    │
│   A. Cold Start (no cache): Full fetch ⬇                   │
│   B. Incremental: Peek GCS for last_ts → Fetch only delta  │
│                                                              │
│ AFTER SUCCESSFUL FETCH:                                      │
│   1. Return data                                            │
│   2. Save to GCS (with throttling: 5min cooldown)          │
│   3. Save to Local File                                     │
│   4. Update LazyLoader                                      │
│   5. Update Redis (OHLCVRedisCache.set_dataframe)          │
│   6. Return ✅                                              │
└──────────────────────────────────────────────────────────────┘
```

---

### 2️⃣ INDICADORES TÉCNICOS (RSI, MACD, BBands, etc.)

#### Función: `calcular_indicadores(df, symbol, tf)`

```python
┌──────────────────────────────────────────────────────────────┐
│ REQUEST: Calculate RSI, MACD for EURUSD/1hour               │
└──────────────────────────────────────────────────────────────┘
                         ↓
┌──────────────────────────────────────────────────────────────┐
│ TIER-0: Redis (IndicatorsRedisCache)                        │
│ Key: "indicators:EURUSD:1hour:<hash of input data>"         │
│ Hash: MD5(df timestamps + config) para detectar cambios     │
│ TTL: 1 hora (según timeframe)                               │
│                                                              │
│ IF HIT:   Return cached indicators (<50ms) ✅              │
│ IF MISS:  Calculate + Cache + Return ↓                     │
└──────────────────────────────────────────────────────────────┘
                         ↓
┌──────────────────────────────────────────────────────────────┐
│ CALCULATION: TA-Lib + NumPy (CPU-intensive)                 │
│ Time: 500ms - 2s (depends on data size)                     │
│                                                              │
│ Operations:                                                  │
│   - RSI (14 periods)                                        │
│   - MACD (12, 26, 9)                                        │
│   - Bollinger Bands (20, 2)                                 │
│   - Stochastic                                              │
│   - ATR                                                     │
│   - Moving Averages (SMA, EMA)                              │
│                                                              │
│ AFTER CALCULATION:                                           │
│   1. Serialize to JSON                                      │
│   2. Store in Redis (with TTL)                              │
│   3. Publish to Pub/Sub (for cross-pod invalidation)       │
│   4. Return ✅                                              │
└──────────────────────────────────────────────────────────────┘
```

---

### 3️⃣ ENTRADAS CALCULADAS (Señales de Trading)

#### Función: `calcular_entradas_async(symbol, tf, config)`

```python
┌──────────────────────────────────────────────────────────────┐
│ REQUEST: Calculate entry signals for EURUSD/1hour           │
└──────────────────────────────────────────────────────────────┘
                         ↓
┌──────────────────────────────────────────────────────────────┐
│ TIER-0: Redis (EntradasRedisCache)                          │
│ Key: "entradas:EURUSD:1hour:<hash of config+indicators>"    │
│ TTL: Calculated per timeframe                               │
│                                                              │
│ Pre-Cache Strategy:                                          │
│   1. Generate cache key at START of function                │
│   2. Try to get from Redis                                  │
│   3. If HIT: Return immediately ✅                          │
│   4. If MISS: Calculate (deterministic) ↓                   │
│                                                              │
│ Post-Store Strategy (after calculation):                    │
│   1. Non-blocking store to Redis                            │
│   2. Publish to Pub/Sub                                     │
│   3. Continue without waiting                               │
└──────────────────────────────────────────────────────────────┘
                         ↓
┌──────────────────────────────────────────────────────────────┐
│ CALCULATION: Deterministic Entry Logic                      │
│ Time: 500ms - 1s                                             │
│                                                              │
│ Operations:                                                  │
│   - Apply entry rules (RSI, MACD, BBands crossovers)        │
│   - Filter by volatility conditions                         │
│   - Calculate entry/exit points                             │
│   - Assign confidence scores                                │
│                                                              │
│ AFTER CALCULATION:                                           │
│   1. Serialize results                                      │
│   2. Store in Redis (non-blocking)                          │
│   3. Return ✅                                              │
└──────────────────────────────────────────────────────────────┘
```

---

### 4️⃣ PONDERACIONES (Solo si existe)

#### Clase: `PonderacionCache`

```python
┌──────────────────────────────────────────────────────────────┐
│ TIER-0: Redis (PonderacionCache)                            │
│ Key: "ponderacion:<symbol>:<tf>"                            │
│                                                              │
│ IF Redis Available:                                          │
│   - Get from Redis                                          │
│   - If HIT: Return ✅                                       │
│   - If MISS: Calculate + Store                              │
│                                                              │
│ FALLBACK: In-Memory Dict (self.local_cache)                 │
│   - If Redis fails, use local dict                          │
│   - Not shared between pods                                 │
└──────────────────────────────────────────────────────────────┘
```

---

## 📈 FRESHNESS THRESHOLDS (Time-Aware Caching)

### Para OHLCV (Datos de Precios)

```python
CACHE_FRESHNESS_THRESHOLDS = {
    "1min":   5 * 60,      # 5 min  (scalping needs VERY fresh data)
    "5min":   10 * 60,     # 10 min
    "15min":  30 * 60,     # 30 min
    "30min":  60 * 60,     # 1 hour
    "1hour":  2 * 3600,    # 2 hours
    "4hour":  4 * 3600,    # 4 hours
    "1day":   12 * 3600,   # 12 hours (very tolerant)
    "1week":  24 * 3600,   # 24 hours
}
```

**Ejemplo**:
- Archivo `EURUSD__1hour.json` creado hace **1 hora** → ✅ FRESH (threshold 2h)
- Archivo `EURUSD__1min.json` creado hace **10 min** → ❌ STALE (threshold 5min)

### Para Indicadores

```python
INDICATORS_FRESHNESS_THRESHOLDS = {
    "1min":   5 * 60,      # 5 min
    "5min":   10 * 60,     # 10 min
    "15min":  30 * 60,     # 30 min
    "30min":  60 * 60,     # 1 hour
    "1hour":  2 * 3600,    # 2 hours
    "4hour":  4 * 3600,    # 4 hours
    "1day":   12 * 3600,   # 12 hours
    "1week":  24 * 3600,   # 24 hours
}
```

---

## 🚀 ESTRATEGIAS AVANZADAS

### Incremental Fetch (OHLCV)

```python
# Si cache local está vacío pero GCS tiene datos:
1. Peek GCS para obtener last_timestamp (sin cargar DF completo)
2. Fetch FMP solo desde last_timestamp hasta ahora
3. Append nuevos datos a GCS (incremental)
4. Evita re-fetch de datos históricos completos

┌──────────────────────────────────────────────────────────┐
│ EJEMPLO:                                                 │
│                                                          │
│ GCS tiene datos hasta: 2026-02-27 00:00                 │
│ Ahora es:              2026-02-27 10:00                 │
│                                                          │
│ ❌ OLD: Fetch todo desde 2025-01-01 (3000 velas)        │
│ ✅ NEW: Fetch solo desde 2026-02-27 00:00 (10 velas)   │
│                                                          │
│ Speedup: 300x menos datos transmitidos                  │
└──────────────────────────────────────────────────────────┘
```

### GCS Backup Throttling

```python
# Evita sobrescribir GCS en cada request
_GCS_BACKUP_THROTTLE_SECONDS = 5 * 60  # 5 min cooldown

┌──────────────────────────────────────────────────────────┐
│ STRATEGY:                                                │
│ 1. SIEMPRE backup en cold-start (first fetch)           │
│ 2. En subsequent fetches: solo backup si >5min desde    │
│    último backup                                         │
│                                                          │
│ BENEFIT: Reduce GCS write costs + API rate limits       │
└──────────────────────────────────────────────────────────┘
```

---

## ⚡ RENDIMIENTO ESPERADO (según Hit Rate)

### Con CACHE_STRATEGY=redis_gcs (todas las capas activas)

| Capa | Latencia | Hit Rate (1h uso) | Contribución |
|------|----------|-------------------|--------------|
| Redis (Tier-0) | <50ms | 40-60% | 🚀 Ultra-fast |
| LazyLoader (Tier-1) | <5ms | 10-20% | ⚡ In-process |
| Local Files (Tier-2) | <50ms | 15-25% | ✅ Fast disk |
| GCS (Tier-3) | <500ms | 1-5% | 📦 Cloud backup |
| FMP (Tier-4) | 1-5s | <1% | 🐌 Slow API |

**Promedio ponderado**: ~100ms (85% hit rate en capas rápidas)

### Sin Redis (CACHE_STRATEGY=gcs_only)

| Capa | Latencia | Hit Rate (1h uso) | Contribución |
|------|----------|-------------------|--------------|
| LazyLoader (Tier-0) | <5ms | 20-30% | ⚡ In-process |
| Local Files (Tier-1) | <50ms | 30-50% | ✅ Fast disk |
| GCS (Tier-2) | <500ms | 10-20% | 📦 Cloud backup |
| FMP (Tier-3) | 1-5s | <5% | 🐌 Slow API |

**Promedio ponderado**: ~300ms (sin Redis, aún rápido gracias a LazyLoader)

---

## 🛠️ VARIABLES DE ENTORNO RELACIONADAS

```bash
# Cache Strategy (controla Tier-0: Redis vs GCS vs Memory)
CACHE_STRATEGY=redis_gcs   # (redis_gcs | redis_only | gcs_only | memory_only)
REDIS_URL=redis://localhost:6379
GCS_BUCKET=market-tool-historical-data

# Full History Mode (mantener serie completa en análisis)
ANALYSIS_PERSIST_FULL_SERIES=true   # default: true
ANALYSIS_USE_FULL_HISTORY=false     # override: false (not recommended)

# GCS Backup Throttling
_GCS_BACKUP_THROTTLE_SECONDS=300    # 5 min cooldown (hardcoded)
```

---

## 🔍 DEBUGGING: Cómo Saber Qué Capa Se Usó

### Logs de Debug

```python
# Tier-0 (Redis)
[OHLCV Redis HIT] EURUSD/1hour - <50ms (2153 rows)
[OHLCV Redis MISS] EURUSD/1hour - calculating...
[OHLCV Redis STORE] EURUSD/1hour cached (2153 rows)

# Tier-1 (LazyLoader)
[load_cached] Hit LazyLoader (memory): EURUSD/1hour

# Tier-2 (Local Files)
[load_cached] Hit Local (FRESH): EURUSD/1hour age=1200s < 7200s threshold
[load_cached] Local stale for EURUSD/1hour: age=8000s > 7200s threshold, checking GCS...

# Tier-3 (GCS)
[load_cached] Hit GCS (check after local stale): EURUSD/1hour, threshold=7200s

# Tier-4 (FMP)
[HIST][COLD_START] EURUSD/1hour: no cache and no GCS data, full fetch
[HIST][INCREMENTAL] EURUSD/1hour: cache empty but GCS has data. Fetching from 2026-02-27
```

### Endpoint de Monitoreo

```bash
curl http://localhost:5000/api/cache/stats

# Respuesta incluye stats de Redis:
{
  "redis": {
    "indicators": {"hits": 850, "misses": 120, "hit_rate": "87.6%"},
    "ohlcv": {"hits": 620, "misses": 95, "hit_rate": "86.7%"},
    "entradas": {"hits": 380, "misses": 50, "hit_rate": "88.4%"}
  }
}
```

---

## ✅ CONCLUSIONES

### Capas Reales

```
5 CAPAS TOTALES (no 3):

✅ Tier-0: Redis (NEW, configurable)
✅ Tier-1: LazyHistoricosLoader (LRU memory, SIEMPRE activo)
✅ Tier-2: Local Files (historicos/*.json, con freshness check)
✅ Tier-3: GCS (persistencia en la nube)
✅ Tier-4: FMP API (fallback final)
```

### Por Qué Tantas Capas

1. **Redis (Tier-0)**: Compartir cache entre pods, ultra-rápido
2. **LazyLoader (Tier-1)**: Evitar I/O dentro del mismo proceso
3. **Local Files (Tier-2)**: Persistencia local (sobrevive reinicio del proceso)
4. **GCS (Tier-3)**: Persistencia cloud (compartida entre servidores)
5. **FMP (Tier-4)**: Fuente de verdad (cuando nada más funciona)

### Configurabilidad

- **CACHE_STRATEGY** controla solo **Tier-0** (Redis vs GCS vs Memory)
- **Tiers 1-4** son SIEMPRE activos (no configurables, optimización interna)
- Puedes deshabilitar Redis, pero LazyLoader + Local Files + GCS siguen funcionando

---

**Documentado**: 2026-02-27
