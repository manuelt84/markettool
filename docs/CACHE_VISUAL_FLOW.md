# FLUJO DE CACHÉ VISUAL - MarketTool

## TODAS LAS CAPAS (5 CAPAS TOTALES)

```
REQUEST: Obtener datos EURUSD 1hour
│
├─→ [1] REDIS (Tier-0) ────────────────────── <50ms (nuevo, configurable)
│   │   Key: "ohlcv:EURUSD:1hour:2026-02-27"
│   │   TTL: 1 hora
│   │   Compartido entre pods
│   │
│   ├─ HIT? ✅ RETORNAR (40-60% de requests)
│   └─ MISS? ↓ Continuar a Tier-1
│
├─→ [2] MEMORY (Tier-1) ───────────────────── <5ms (siempre activo)
│   │   _LAZY_HIST_LOADER.get(symbol, tf)
│   │   LRU cache en memoria del proceso
│   │   Se pierde al reiniciar
│   │
│   ├─ HIT? ✅ RETORNAR (10-20% de requests)
│   └─ MISS? ↓ Continuar a Tier-2
│
├─→ [3] LOCAL FILE (Tier-2) ──────────────── <50ms (siempre activo)
│   │   historicos/EURUSD__1hour.json
│   │   Con FRESHNESS CHECK por timeframe
│   │
│   │   Thresholds:
│   │   - 1min:  5 min
│   │   - 1hour: 2 horas ← Este caso
│   │   - 1day:  12 horas
│   │
│   ├─ FRESH? ✅ RETORNAR + Update Memory (15-25% de requests)
│   ├─ STALE? ↓ Continuar a Tier-3 (archivo viejo)
│   └─ MISS?  ↓ Continuar a Tier-3 (archivo no existe)
│
├─→ [4] GCS (Tier-3) ──────────────────────── <500ms (siempre activo)
│   │   gs://market-tool-data/historicos/EURUSD_1hour.parquet
│   │   Persistencia en la nube
│   │   Compartido entre todos los servidores
│   │
│   ├─ HIT? ✅ RETORNAR + Update Local + Update Memory (1-5% de requests)
│   └─ MISS? ↓ Continuar a Tier-4
│
└─→ [5] FMP API (Tier-4) ──────────────────── 1-5s (fallback final)
    │   https://fmp.com/api/v3/historical-chart/1hour/EURUSD
    │   Fuente de verdad
    │   Rate limited, lento
    │
    └─ FETCH ✅ RETORNAR + Guardar en TODOS los niveles (<1% de requests)
                          └─→ Redis (Tier-0)
                          └─→ Memory (Tier-1)
                          └─→ Local File (Tier-2)
                          └─→ GCS (Tier-3)
```

---

## CONFIGURABILIDAD

### ¿Qué controla CACHE_STRATEGY?

```
CACHE_STRATEGY     | Tier-0      | Tier-1  | Tier-2     | Tier-3 | Tier-4
-------------------|-------------|---------|------------|--------|--------
redis_gcs (default)| Redis ✅    | Memory ✅| Local File ✅| GCS ✅ | FMP ✅
redis_only         | Redis ✅    | Memory ✅| Local File ✅| GCS ❌ | FMP ✅
gcs_only           | GCS (↑T0) ✅| Memory ✅| Local File ✅| -      | FMP ✅
memory_only        | Memory (↑T0)✅| -       | Local File ✅| GCS ❌ | FMP ✅
disabled           | -           | -       | -          | -      | FMP ✅
```

**Clave**: 
- `CACHE_STRATEGY` **solo controla Tier-0** (la capa más rápida)
- **Tiers 1-2 (Memory + Local) SIEMPRE están activas** (optimización interna)
- **Tier-3 (GCS)** depende de si está en la estrategia
- **Tier-4 (FMP)** es el fallback final, siempre disponible

---

## EJEMPLO REAL DE UN REQUEST

### Escenario: Análisis diario de EURUSD/1hour

```
DÍA 1 (Cold Start)
─────────────────
Request #1 (10:00 AM):
│
├─ Redis:      MISS (vacío)
├─ Memory:     MISS (vacío)
├─ Local File: MISS (no existe)
├─ GCS:        MISS (no existe)
└─ FMP:        FETCH ✅ (5 segundos)
               └─→ Guardar en Redis, Memory, Local, GCS
               
⏱️ Tiempo total: 5 segundos


Request #2 (10:05 AM) - Mismo símbolo:
│
├─ Redis:      HIT ✅ (50ms)
└─ RETORNAR

⏱️ Tiempo total: 50ms (100x más rápido)


Request #3 (12:00 PM) - Mismo símbolo (2 horas después):
│
├─ Redis:      HIT ✅ (50ms)  ← Redis TTL=1h, pero aún puede estar
└─ RETORNAR                      si no se reinició

⏱️ Tiempo total: 50ms


───────────────────────────────────────────────────────
DÍA 2 (Redis reiniciado, pero Local/GCS tienen datos)
───────────────────────────────────────────────────────

Request #1 (10:00 AM):
│
├─ Redis:      MISS (reiniciado)
├─ Memory:     MISS (proceso reiniciado)
├─ Local File: HIT ✅ (50ms) ← Archivo tiene 24h
│              └─ Freshness check: 24h < 2h? ❌ STALE
│
├─ GCS:        HIT ✅ (500ms) ← Backup en la nube
└─ RETORNAR
   └─→ Actualizar Redis, Memory, Local

⏱️ Tiempo total: 500ms (10x más rápido que FMP)


Request #2 (10:05 AM):
│
├─ Redis:      HIT ✅ (50ms) ← Actualizado por request anterior
└─ RETORNAR

⏱️ Tiempo total: 50ms
```

---

## FRESHNESS CHECK (Time-Aware)

### Por qué es importante

```
Archivo: EURUSD__1min.json
Creado: 10:00 AM
Ahora:  10:06 AM
Edad:   6 minutos

Threshold para 1min: 5 minutos

├─ FRESHNESS CHECK:
│  6 min > 5 min? SÍ
│
└─ RESULTADO: ❌ STALE
   └─→ Saltar a GCS para obtener datos frescos
```

VS

```
Archivo: EURUSD__1day.json
Creado: Ayer 10:00 AM
Ahora:  Hoy  10:00 AM
Edad:   24 horas

Threshold para 1day: 12 horas

├─ FRESHNESS CHECK:
│  24h > 12h? SÍ
│
└─ RESULTADO: ❌ STALE
   └─→ Saltar a GCS para obtener datos frescos
```

**Sin freshness check**: Datos de 1min de hace 1 día serían usados (incorrecto)
**Con freshness check**: Cada timeframe tiene su propio threshold

---

## HIT RATE ESPERADO (después de 1 hora de uso)

### Con CACHE_STRATEGY=redis_gcs

```
┌────────────────────────────────────────────────────┐
│ DISTRIBUCIÓN DE HITS (1000 requests)               │
├────────────────────────────────────────────────────┤
│ Redis (T0):       500 hits (50%) ████████████████  │
│ Memory (T1):      150 hits (15%) ████              │
│ Local File (T2):  200 hits (20%) █████             │
│ GCS (T3):          50 hits (5%)  █                 │
│ FMP (T4):         100 hits (10%) ██                │
├────────────────────────────────────────────────────┤
│ Promedio ponderado:                                │
│ (500×50ms + 150×5ms + 200×50ms + 50×500ms +        │
│  100×5000ms) / 1000 = ~100ms                       │
└────────────────────────────────────────────────────┘

💡 Speedup: 50x más rápido que sin caché (5s → 100ms)
```

### Sin Redis (CACHE_STRATEGY=gcs_only)

```
┌────────────────────────────────────────────────────┐
│ DISTRIBUCIÓN DE HITS (1000 requests)               │
├────────────────────────────────────────────────────┤
│ Memory (T0):      300 hits (30%) ████████          │
│ Local File (T1):  400 hits (40%) ████████████      │
│ GCS (T2):         200 hits (20%) █████             │
│ FMP (T3):         100 hits (10%) ██                │
├────────────────────────────────────────────────────┤
│ Promedio ponderado:                                │
│ (300×5ms + 400×50ms + 200×500ms + 100×5000ms) /    │
│  1000 = ~150ms                                     │
└────────────────────────────────────────────────────┘

💡 Speedup: 33x más rápido que sin caché (5s → 150ms)
```

---

## RESUMEN EJECUTIVO

### Lo que CACHE_STRATEGY controla

```
redis_gcs   → Tier-0 = Redis     (compartido, rápido, volátil)
redis_only  → Tier-0 = Redis     (sin GCS backup)
gcs_only    → Tier-0 = GCS       (Redis no disponible)
memory_only → Tier-0 = Memory    (testing, sin persistencia)
disabled    → Sin Tier-0         (solo FMP API)
```

### Lo que SIEMPRE está activo (no configurable)

```
✅ Memory (LazyHistoricosLoader) - LRU cache en proceso
✅ Local Files (historicos/*.json) - Con freshness check
✅ FMP API - Fallback final
```

### Lo configurable via GCS_BUCKET

```
✅ GCS (Google Cloud Storage) - Si GCS_BUCKET está definido
```

---

**Última actualización**: 2026-02-27
