# 🔄 CÓMO MARKETTOOL AGREGA NUEVA DATA A INDICADORES

## Resumen Ejecutivo

MarketTool usa un sistema de **cache inteligente con actualizaciones incrementales** para mantener los indicadores precalculados siempre actualizados sin recalcular todo desde cero.

---

## 1. Flujo General de Actualización

### Código Base: `markettool/infra/cache/indicators_cache.py`

```python
class IndicatorsCache:
    """
    Sistema de cache multi-nivel para indicadores técnicos:
    
    Niveles:
    1. Memory Cache (LRU, < 5 min) → Más rápido
    2. Local JSON (< 24h) → Persiste entre requests
    3. GCS Bucket (< 24h) → Compartido entre pods
    4. Cálculo en vivo → Solo si no existe cache
    """
```

---

## 2. Escenarios de Actualización

### 🟢 Escenario A: Cache Válido (Perfect Hit)

**Condición:** Los datos históricos no cambiaron y el cache está fresco (< 24h)

```python
if cached_hash == current_hash and cached_rows == current_rows:
    # ✅ Usar cache directamente
    return df_result, {
        "cache_hit": True,
        "incremental": False,
        "calc_time_ms": 0,
        "source": "cache_perfect_match"
    }
```

**Flujo:**
```
1. Calcular hash de historicos actuales
2. Comparar con hash guardado en cache
3. Si son iguales → RETORNAR CACHE (0ms de cálculo)
```

**Tiempo:** < 10ms ⚡

---

### 🟡 Escenario B: Actualización Incremental (Tail Refresh)

**Condición:** Llegaron nuevas velas (ej: cerró una vela de 1hour) pero la data histórica anterior no cambió

```python
if current_rows > cached_rows:
    # Hay nuevas velas → Actualización incremental
    
    # 1. Calcular ventana de contexto necesaria
    window = self._window_func(tf)  # ej: 50 para SMA50
    context_start = max(0, current_rows - window)
    
    # 2. Extraer solo las velas nuevas + contexto
    df_to_calc = df_historicos.iloc[context_start:].copy()
    
    # 3. Calcular indicadores SOLO en esa porción
    df_partial = calc_func(df_to_calc, tf)
    indicators_partial = self._extract_indicators_from_df(df_partial)
    
    # 4. Merge con cache existente
    indicators_merged = merge_indicators_incremental(
        cached["indicators"],      # Datos antiguos (0 a context_start)
        indicators_partial,        # Datos nuevos (context_start a end)
        context_start,
        0
    )
    
    # 5. Guardar actualizado en GCS
    self.save(symbol, tf, indicators_merged, df_historicos, ...)
```

**Ejemplo concreto:**

```
Estado inicial:
- BTCUSD 1hour tiene 1000 velas en cache
- Indicadores calculados: RSI[0..999], MACD[0..999], etc.

Llega nueva vela:
- BTCUSD 1hour ahora tiene 1001 velas
- Ventana necesaria: 50 (para SMA50, EMA50, etc.)
- Context start: 1001 - 50 = 951

Proceso:
1. Extraer velas 951..1001 (50 velas)
2. Calcular indicadores SOLO en esas 50 velas
3. Merge:
   - RSI[0..950] ← del cache antiguo
   - RSI[951..1001] ← del cálculo nuevo
4. Guardar RSI[0..1001] completo en GCS

Resultado:
- Recalculaste 50 velas en vez de 1001
- Ahorro: 95% de CPU ⚡
```

**Código de merge:**

```python
def merge_indicators_incremental(
    cached_indicators: dict,
    new_indicators: dict,
    split_index: int,
    overlap: int = 0
) -> dict:
    """
    Combina indicadores cacheados con nuevos cálculos.
    
    Args:
        cached_indicators: {"rsi": [val0, val1, ...], "macd": [...]}
        new_indicators: {"rsi": [val_split, val_split+1, ...], ...}
        split_index: Índice donde empieza lo nuevo
        overlap: Cantidad de velas solapadas para continuidad
    
    Returns:
        Indicadores merged completos
    """
    merged = {}
    
    for key in cached_indicators.keys():
        cached_vals = cached_indicators[key]
        new_vals = new_indicators.get(key, [])
        
        if new_vals:
            # Mantener antiguos hasta split_index
            merged[key] = cached_vals[:split_index] + new_vals[overlap:]
        else:
            merged[key] = cached_vals
    
    return merged
```

**Tiempo:** ~50-100ms (dependiendo de la ventana) ⚡

---

### 🔴 Escenario C: Recálculo Completo (Cold Start)

**Condición:** No existe cache o los datos históricos cambiaron (hash diferente)

```python
if not cached or cached_hash != current_hash:
    # ❌ Cache inválido → Recalcular TODO
    
    lock_acquired = self._acquire_lock(symbol, tf)
    
    if lock_acquired:
        try:
            # Calcular indicadores en TODOS los datos
            df_result = calc_func(df_historicos.copy(), tf)
            
            # Extraer todos los indicadores
            indicators = self._extract_indicators_from_df(df_result)
            
            # Guardar en GCS + Firestore metadata
            self.save(
                symbol, tf,
                indicators,
                df_historicos,
                calc_duration_ms,
                analysis_audit={
                    "last_mode": "bootstrap",
                    "last_bootstrap_at": datetime.now().isoformat()
                }
            )
        finally:
            self._release_lock(symbol, tf)
```

**Cuándo ocurre:**
- Primera vez que se pide ese símbolo/timeframe
- Los datos históricos se corrigieron (ej: FMP ajustó precios)
- El cache expiró (> 24h) y se borró

**Tiempo:** ~2-5 segundos (dependiendo de cantidad de velas) 🐌

---

## 3. Sistema de Locks Distribuidos

### Problema: Múltiples Pods Calculando lo Mismo

Si 10 usuarios piden BTCUSD 1hour al mismo tiempo y no hay cache:
- ❌ Sin lock: Los 10 pods calculan lo mismo (desperdicio)
- ✅ Con lock: Solo 1 pod calcula, los otros esperan

### Implementación:

```python
def _acquire_lock(self, symbol: str, tf: str, timeout_sec: int = 180) -> bool:
    """
    Usa Firestore como lock distribuido.
    
    Documento: indicators_metadata/{SYMBOL}__{TF}
    Campos:
      - calculating_by_pod: "pod-abc123"
      - calculating_since: 2026-08-01T06:00:00Z
    """
    
    doc_ref = self.db.collection("indicators_metadata").document(doc_id)
    
    # Verificar si otro pod tiene el lock
    doc = doc_ref.get()
    if doc.exists:
        lock_pod = doc.get("calculating_by_pod")
        lock_time = doc.get("calculating_since")
        
        age_sec = (now - lock_time).total_seconds()
        
        if age_sec < timeout_sec and lock_pod != self._pod_id:
            # Otro pod está calculando → ESPERAR
            return False
    
    # Adquirir lock
    doc_ref.set({
        "calculating_by_pod": self._pod_id,
        "calculating_since": now,
    }, merge=True)
    
    return True


def _wait_for_lock_release(self, symbol: str, tf: str, max_wait_sec: int = 120) -> bool:
    """
    Esperar a que otro pod termine de calcular.
    
    Polling cada 2 segundos hasta:
    - Lock se libera → TRUE
    - Timeout → FALSE
    """
    
    start = time.time()
    
    while time.time() - start < max_wait_sec:
        doc = doc_ref.get()
        
        if not doc.exists or doc.get("calculating_by_pod") is None:
            return True  # Lock liberado
        
        time.sleep(2)
    
    return False  # Timeout
```

**Flujo con locks:**

```
Pod A: Adquiere lock → Calcula (5s) → Libera lock
Pod B: Detecta lock → Espera (5s) → Usa resultado de Pod A
Pod C: Detecta lock → Espera (5s) → Usa resultado de Pod A
Pod D: Detecta lock → Espera (5s) → Usa resultado de Pod A

Total: 5s (en vez de 20s si todos calcularan)
```

---

## 4. Estructura de Datos en GCS

### Archivo: `gs://markettool_bucket/indicators/BTCUSD__1hour.json`

```json
{
  "metadata": {
    "symbol": "BTCUSD",
    "timeframe": "1hour",
    "last_update_utc": "2026-08-01T06:00:00Z",
    "data_hash": "abc123def456...",
    "rows_count": 1001,
    "last_calc_index": 1000,
    "calc_duration_ms": 2345,
    "indicators_list": ["rsi", "macd", "bb_upper", "bb_lower", "ema_20", "sma_50"],
    "analysis_audit": {
      "last_mode": "incremental",
      "last_bootstrap_at": "2026-07-30T10:00:00Z",
      "last_incremental_at": "2026-08-01T06:00:00Z",
      "last_incremental_bars": 50,
      "last_data_mismatch_at": null
    }
  },
  "indicators": {
    "rsi": [65.2, 63.8, 67.1, ..., 72.5],
    "macd": [0.12, 0.08, 0.15, ..., 0.23],
    "macd_signal": [0.10, 0.09, 0.11, ..., 0.19],
    "macd_hist": [0.02, -0.01, 0.04, ..., 0.04],
    "bb_upper": [42500, 42480, 42520, ..., 43100],
    "bb_middle": [42150, 42130, 42170, ..., 42800],
    "bb_lower": [41800, 41780, 41820, ..., 42500],
    "ema_20": [42100, 42080, 42120, ..., 42750],
    "sma_50": [41900, 41880, 41920, ..., 42600]
  }
}
```

### Metadata en Firestore: `indicators_metadata/BTCUSD__1hour`

```json
{
  "symbol": "BTCUSD",
  "timeframe": "1hour",
  "gcs_path": "gs://markettool_bucket/indicators/BTCUSD__1hour.json",
  "last_update_utc": "2026-08-01T06:00:00Z",
  "data_hash": "abc123def456...",
  "rows_count": 1001,
  "last_calc_index": 1000,
  "indicators_list": ["rsi", "macd", "bb_upper", ...],
  "ttl_hours": 24,
  "is_valid": true,
  "calculating_by_pod": null,
  "calculating_since": null,
  "analysis_audit": {
    "last_mode": "incremental",
    "last_bootstrap_at": "2026-07-30T10:00:00Z",
    "last_incremental_at": "2026-08-01T06:00:00Z",
    "last_incremental_bars": 50
  }
}
```

---

## 5. Ejemplo Paso a Paso: Nueva Vela de BTCUSD

### Contexto:
- Símbolo: BTCUSD
- Timeframe: 1hour
- Velas en cache: 1000 (hasta las 05:00 UTC)
- Hora actual: 06:00 UTC (cierra nueva vela)

### Paso 1: Usuario pide análisis

```
RN/WEB → GET /api/analysis?symbol=BTCUSD&tf=1hour
```

### Paso 2: MarketTool verifica cache

```python
cached = indicators_cache.load("BTCUSD", "1hour")
# ✅ Existe en GCS, age = 1.0 horas (< 24h) → CACHE HIT
```

### Paso 3: Verificar si hay nuevas velas

```python
df_historicos = get_historicos("BTCUSD", "1hour")
# df_historicos tiene 1001 velas (llegó la de 06:00)

current_hash = hash_dataframe(df_historicos)
cached_hash = cached["metadata"]["data_hash"]

if current_hash != cached_hash:
    # ❌ Hash diferente → Hay data nueva
    # Pero rows solo aumentó en 1 → Actualización incremental
```

### Paso 4: Adquirir lock

```python
lock_acquired = indicators_cache._acquire_lock("BTCUSD", "1hour")
# ✅ Lock adquirido (nadie más está calculando)
```

### Paso 5: Calcular ventana de contexto

```python
window = 50  # Para SMA50, EMA50, etc.
context_start = 1001 - 50 = 951

df_to_calc = df_historicos.iloc[951:1001].copy()
# Solo 50 velas en vez de 1001
```

### Paso 6: Calcular indicadores parciales

```python
start_time = time.time()
df_partial = calc_func(df_to_calc, "1hour")
# Calcula RSI, MACD, BB, etc. para las velas 951..1001
calc_time_ms = (time.time() - start_time) * 1000
# ~50ms en vez de ~2000ms (40x más rápido)
```

### Paso 7: Merge con cache existente

```python
indicators_merged = merge_indicators_incremental(
    cached["indicators"],      # RSI[0..999], MACD[0..999], ...
    indicators_partial,        # RSI[950..1000], MACD[950..1000], ...
    split_index=951,
    overlap=0
)

# Resultado:
# RSI[0..950] ← del cache antiguo
# RSI[951..1000] ← del cálculo nuevo
# = RSI[0..1000] completo
```

### Paso 8: Guardar en GCS + Firestore

```python
indicators_cache.save(
    symbol="BTCUSD",
    tf="1hour",
    indicators=indicators_merged,
    df_historicos=df_historicos,
    calc_duration_ms=calc_time_ms,
    analysis_audit={
        "last_mode": "incremental",
        "last_incremental_at": "2026-08-01T06:00:00Z",
        "last_incremental_bars": 50
    }
)
```

**Archivo GCS actualizado:**
- Path: `gs://markettool_bucket/indicators/BTCUSD__1hour.json`
- Rows: 1001 (antes 1000)
- Last update: 2026-08-01T06:00:00Z
- Mode: incremental

### Paso 9: Liberar lock

```python
indicators_cache._release_lock("BTCUSD", "1hour")
# Otros pods ahora pueden usar el cache actualizado
```

### Paso 10: Retornar resultado al usuario

```python
return df_result, {
    "cache_hit": True,
    "incremental": True,
    "calc_time_ms": 50,
    "source": "tail_refresh",
    "saved_to_gcs": True
}
```

**Tiempo total:** ~100ms (vs ~2-3s sin cache) ⚡

---

## 6. Ventajas del Sistema

| Ventaja | Impacto | Ejemplo |
|---------|---------|---------|
| **Velocidad** | ⚡ 40x más rápido en incremental | 50ms vs 2000ms |
| **CPU** | 🧠 95% menos uso de CPU | Solo calcula 50 velas de 1000 |
| **Escalabilidad** | 📈 Soporta 100+ usuarios | Locks evitan cálculos duplicados |
| **Persistencia** | 💾 Sobrevive a reinicios | GCS no se pierde |
| **Consistencia** | 🎯 Todos ven lo mismo | Firestore coordina locks |
| **Audit** | 📝 Trazabilidad completa | Sabés cuándo y cómo se calculó |

---

## 7. Monitoreo y Debugging

### Logs Típicos

```
# Cold start (primera vez)
[IndicatorsCache] Cold start: BTCUSD/1hour (pod=pod-abc123)
[IndicatorsCache] Full calculation: 2345ms

# Tail refresh (nueva vela)
[IndicatorsCache] Tail refresh: BTCUSD/1hour (rows=1001, context=50, pod=pod-abc123)
[IndicatorsCache] Incremental calculation: 52ms

# Perfect hit (sin cambios)
[IndicatorsCache] Perfect hit: BTCUSD/1hour (1000 rows, pod=pod-abc123)
[IndicatorsCache] Cache age: 0.5 hours

# Lock wait (otro pod calculando)
[IndicatorsCache] Lock held by pod-xyz789: BTCUSD/1hour (age=3s)
[IndicatorsCache] Waiting for lock release...
[IndicatorsCache] Lock released, using cached result
```

### Query para Ver Estado en Firestore

```sql
-- En PostgreSQL (metadata sincronizada desde Firestore)
SELECT 
    doc_id,
    data->>'symbol' as symbol,
    data->>'timeframe' as timeframe,
    data->>'last_update_utc' as last_update,
    data->>'rows_count' as rows,
    data->>'analysis_audit'->>'last_mode' as last_mode,
    data->>'analysis_audit'->>'last_incremental_at' as last_incremental
FROM markettool.firestore_docs
WHERE collection_name = 'indicators_metadata'
ORDER BY (data->>'analysis_audit'->>'last_incremental_at') DESC NULLS LAST
LIMIT 10;
```

---

## 8. Configuración y Ajustes

### Variables de Entorno

```bash
# TTL del cache (horas)
INDICATORS_CACHE_TTL_HOURS=24

# Timeout de locks (segundos)
INDICATORS_LOCK_TIMEOUT_SEC=180

# Máximo de rows a guardar en GCS
GCS_INDICATORS_MAX_ROWS=5000

# Habilitar/deshabilitar cache
INDICATORS_CACHE_ENABLED=true
```

### Ajustar Ventana de Contexto

En el código:

```python
def _default_window_func(tf: str) -> int:
    """Ventana necesaria según timeframe para indicadores."""
    
    if tf in ["1min", "5min"]:
        return 200  # Más historia para timeframes cortos
    elif tf in ["15min", "30min", "1hour"]:
        return 100  # Estándar
    elif tf in ["4hour", "1day", "1week"]:
        return 50   # Menos necesario en timeframes largos
    else:
        return 100  # Default
```

---

## 9. Conclusión

**Respuesta a tu pregunta:**

> ¿Cómo hace MarketTool para agregar nueva data a indicadores?

**Respuesta corta:** Usa **actualización incremental** con locks distribuidos.

**Respuesta detallada:**

1. **Detecta nuevas velas** comparando hashes de historicos
2. **Calcula solo la ventana necesaria** (ej: últimas 50 velas para SMA50)
3. **Mergea con cache existente** (antiguo + nuevo = completo)
4. **Guarda en GCS + Firestore** con metadata de auditoría
5. **Usa locks** para evitar cálculos duplicados entre pods

**Resultado:** 40x más rápido que recalcular todo, 95% menos CPU, escalable a 100+ usuarios.

---

**Documentación creada:** Agosto 2026  
**Archivos referenciados:**
- `markettool/infra/cache/indicators_cache.py` (líneas 280-900)
- Funciones clave: `save()`, `_load_from_gcs()`, `_acquire_lock()`, `merge_indicators_incremental()`
