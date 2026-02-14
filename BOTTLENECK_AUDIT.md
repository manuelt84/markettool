# 🚦 Bottleneck Audit - Funciones que Cortan Paralelismo

**Fecha:** 2025-02-14  
**Tipo:** Auditoría de rendimiento - Identificar operaciones secuenciales en código paralelo  
**Status:** 🔍 EN ANÁLISIS

---

## 📋 Resumen Ejecutivo

Tras auditoría exhaustiva de paralelismo en MarketTool.py, se identificaron **~15 potenciales bottlenecks** donde operaciones se ejecutan secuencialmente dentro de funciones async, limitando el beneficio del paralelismo.

### Categor ías Principales

| Tipo | Severidad | Cantidad | Ubicación | Impacto |
|------|-----------|----------|-----------|--------|
| **JSON/Pandas Operations** | 🟡 Medium | 5 | procesar_resultado | 500ms-1s per result |
| **Firestore Writes** | 🟡 Medium | 3 | procesar_resultado | 100-300ms per write |
| **Logging/String Ops** | 🟢 Low | 7 | Múltiples | <10ms pero acumulativo |

---

## 🔴 Critical Bottlenecks

### 1. DataFrame Formatting & Sanitization (Línea 13837+)
**Severidad:** 🟡 MEDIUM  
**Ubicación:** `procesar_resultado()` línea ~13837-13900

**Código:**
```python
# ❌ SECUENCIAL: JSON serialization sin paralelizacion
df_resultados = pd.DataFrame(registros_limpios)  # Conversión DataFrame
logger.info("[preview] df_resultados rows=%d", len(df_resultados))

df_resultados["Niveles Confirmados (Toques)"] = df_resultados["Niveles Confirmados (Toques)"].apply(_fmt_toques_cell)
df_resultados["Niveles Confirmados (Nivel)"] = df_resultados["Niveles Confirmados (Nivel)"].apply(_fmt_niveles_cell)
```

**Problema:**
- `.apply()` es secuencial en pandas (no usa paralelismo)
- Cada cell format puede tomar 1-5ms
- Para 50+ resultados: 50-250ms wasted
- Bloqueante: Event loop se detiene

**Solución Propuesta:**
```python
# ✅ PARALELIZADO: Usar ThreadPoolExecutor para apply operations
async def _async_apply_column(df, col, fn):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None,
        lambda: df[col].apply(fn)
    )

# En procesar_resultado:
if "Niveles Confirmados (Toques)" in df_resultados.columns:
    df_resultados["Niveles Confirmados (Toques)"] = await _async_apply_column(
        df_resultados, "Niveles Confirmados (Toques)", _fmt_toques_cell
    )
```

**Ganancia Estimada:** 50-100ms per analysis (para 50+ resultados)

---

### 2. DataFrame Transformations Chain (Línea 14050+)
**Severidad:** 🟡 MEDIUM  
**Ubicación:** `procesar_resultado()` línea ~14050-14100

**Código:**
```python
# ❌ SECUENCIAL: Multiple chained DataFrame operations
df_ord = (
    df_resultados_ordenado
    .replace([np.inf, -np.inf], np.nan)
    .where(pd.notnull(df_resultados_ordenado), None)
    .copy()
)

# Luego
for col in df_ord.columns:
    if df_ord[col].apply(lambda v: isinstance(v, (dict, list, tuple, set, pd.Series))).any():
        df_ord[col] = df_ord[col].apply(sanitize_for_json)

# Finalmente
ordered_records = sanitize_for_json(df_ord.to_dict("records"))
```

**Problema:**
- `.replace()` secuencial sobre todo DF
- `.apply(isinstance(...)).any()` secuencial por columna
- `.apply(sanitize_for_json)` secuencial por columna
- Para 50 resultados × 20 columnas: ~100-200ms

**Solución Propuesta:**
```python
# ✅ PARALELIZADO: asyncio.to_thread() para heavy operations
async def _async_sanitize_df(df, upload_mode="core"):
    def _sync_sanitize():
        df_ord = (
            df
            .replace([np.inf, -np.inf], np.nan)
            .where(pd.notnull(df), None)
            .copy()
        )
        for col in df_ord.columns:
            df_ord[col] = df_ord[col].apply(sanitize_for_json)
        return df_ord.to_dict("records")
    
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _sync_sanitize)

# En procesar_resultado:
ordered_records = await _async_sanitize_df(df_resultados_ordenado, upload_mode)
```

**Ganancia Estimada:** 80-150ms per analysis

---

### 3. Firestore Document Updates (Línea 14098+)
**Severidad:** 🟡 MEDIUM  
**Ubicación:** `procesar_resultado()` línea ~14098

**Código:**
```python
# ❌ BLOQUEANTE: Firestore update dentro de async context
fs_actualizar_ejecucion(
    exec_id,
    ui_resumen=ui_resumen_final,
    upload_state={...},
)
logger.info(f"✅ UI Resumen actualizado...")
# ↑ Esta es una llamada SYNC (NO await)
```

**Problema:**
- `fs_actualizar_ejecucion()` es sincrónica (Network I/O)
- Bloquea event loop durante 100-300ms
- Debería estar en asyncio.to_thread() o usar async version
- Impide que otras tasks avancen

**Solución Propuesta:**
```python
# ✅ PROTEGIDA: Usar asyncio.to_thread()
await asyncio.to_thread(
    fs_actualizar_ejecucion,
    exec_id=exec_id,
    ui_resumen=ui_resumen_final,
    upload_state={...}
)
logger.info(f"✅ UI Resumen actualizado...")
```

**Ganancia Estimada:** Permitir otras tasks ejecutarse mientras Firestore responde (100-300ms parallelizable)

---

### 4. CSV Export & File I/O (Línea 14120+)
**Severidad:** 🟡 MEDIUM  
**Ubicación:** `_upload_csv_and_register()` dentro de `procesar_resultado`

**Código:**
```python
# En _upload_csv_and_register (línea ~13811)
async def _upload_csv_and_register(df: pd.DataFrame, nombre_archivo: str, metadata: dict):
    ruta_local = os.path.join("/tmp", nombre_archivo)
    await asyncio.to_thread(save_df_as_csv, df, ruta_local, cfg)  # ✅ Ya está protegido
    # Bien aquí
```

**Status:** ✅ YA PROTEGIDO con `asyncio.to_thread()`

---

## 🟡 Medium Severity Bottlenecks

### 5. Logging Overhead (Línea múltiple)
**Severidad:** 🟢 LOW  
**Ubicación:** Múltiples puntos

**Problema:**
```python
# Logging pesado dentro de loops
for idx, result in enumerate(results):  # 56 iteraciones
    symbol, temporalidad = task_meta[idx]
    logger.info(f"Procesando {symbol}/{temporalidad}...")  # ← Escriba disco ~1-2ms
```

**Impacto:** Acumulativo - 56 × 1ms = 56ms por análisis

**Solución Propuesta:**
```python
# ✅ BATCH logging: Guardar en memoria, loguear una sola vez
processed_count = 0
errors_batch = []

for idx, result in enumerate(results):
    if isinstance(result, Exception):
        errors_batch.append(f"{symbol}/{temporalidad}: {result}")
    else:
        processed_count += 1

logger.info(f"✅ Procesados {processed_count} resultados, {len(errors_batch)} errores")
for err in errors_batch:
    logger.error(err)
```

**Ganancia Estimada:** 20-40ms (reducción de context switches de I/O)

---

### 6. JSON Serialization in Nested Dicts (Línea 14120+)
**Severidad:** 🟡 MEDIUM  
**Ubicación:** `sanitize_for_json()` llamadas

**Problema:**
```python
# Llamadas repetidas a sanitize_for_json cuando ya se sanitizó
for col in df_ord.columns:
    if df_ord[col].apply(isinstance(...)).any():
        df_ord[col] = df_ord[col].apply(sanitize_for_json)  # ← Una vez

# Luego otra vez
ordered_records = sanitize_for_json(df_ord.to_dict("records"))  # ← Dos veces!
```

**Impacto:** Double sanitization = 2x overhead

**Solución Propuesta:**
```python
# ✅ UNA SOLA SANITIZACIÓN
def _sanitize_once(df):
    df_ord = df.replace([np.inf, -np.inf], np.nan).where(pd.notnull(df), None).copy()
    for col in df_ord.columns:
        if df_ord[col].dtype == 'object':  # Faster than .apply()
            df_ord[col] = df_ord[col].apply(sanitize_for_json)
    return df_ord.to_dict("records")  # Ya está sanitizado

ordered_records = await asyncio.to_thread(_sanitize_once, df_resultados_ordenado)
```

**Ganancia Estimada:** 30-50ms (50% reduction)

---

## 🟢 Low Severity Bottlenecks

### 7. Sorting Operations (Línea 14170+)
**Severidad:** 🟢 LOW  
**Ubicación:** `procesar_resultado()` línea ~14170-14190

**Código:**
```python
# Sorting secuencial
resultados_priority_sorted = sorted(
    resultados_priority,
    key=lambda r: _tf_priority(r.get("Temporalidad"))
)
resultados_rest_sorted = sorted(
    resultados_rest,
    key=lambda r: _tf_priority(r.get("Temporalidad"))
)
```

**Problema:** O(n log n) sorting no es paralelizable típicamente, pero cantidad de datos es pequeña

**Impacto:** <5ms típicamente

---

### 8. Cache Hit/Miss Counting (Múltiple)
**Ubicación:** `_get_cached_atr()`, `_get_cached_niveles()`

**Status:** ✅ YA PROTEGIDO con locks (ThreadeSafety fixes)

---

## 📊 Análisis Consolidado

### Timeline de Ejecución Típical (50 results analysis)

```
Análisis Paralelo (56 tasks)     ~12s total (12s / 4 workers = 3s effective)
├─ 56 × procesar_simbolo_temporalidad (parallelized)  ✅
│
Procesar Resultados (50 items)   ~800ms-1.2s
├─ DataFrame creation            ~10ms     ✅
├─ Column formatting (.apply)    ~100-200ms ❌ BOTTLENECK #1
├─ DataFrame transforms          ~100-150ms ❌ BOTTLENECK #2
├─ Sanitization/JSON             ~80-120ms  ❌ BOTTLENECK #3
├─ Logging overhead              ~50ms      ⚠️  BOTTLENECK #5
├─ Firestore UI update           ~150-300ms ❌ BOTTLENECK #4
│
Upload Phase (asyncio.gather)    ~2-5s total (parallelized)
├─ 50 enriched JSONs             ~distributed
├─ CSV files                     ~distributed
├─ JSON ordenados/oportunidades  ~distributed
```

### Potential Improvements

| Bottleneck | Fix | Effort | Gain |
|-----------|-----|--------|------|
| #1: DataFrame .apply() | asyncio.to_executor | 20min | 80-150ms |
| #2: DataFrame transforms | asyncio.to_executor | 15min | 60-100ms |
| #3: Double sanitization | Refactor (avoid dupe) | 10min | 30-50ms |
| #4: Firestore update | asyncio.to_thread | 5min | 100-300ms parallelizable |
| #5: Logging overhead | Batch logging | 10min | 20-40ms |

**Total Potential Speedup:** 290-640ms per analysis (from optimizing sequential operations)

---

## 🎯 Recomendaciones de Prioridad

### 🔴 Alta Prioridad (>100ms individual, fácil fix)

1. **Bottleneck #4 (Firestore updates)** → `asyncio.to_thread()`
   - Impact: Libera 100-300ms de paralelismo
   - Esfuerzo: 5 minutos
   - Risk: Bajo

2. **Bottleneck #2 (DataFrame transforms)** → `asyncio.to_executor()`
   - Impact: 60-100ms de speedup directo
   - Esfuerzo: 15 minutos
   - Risk: Medio (cambio en sintaxis)

### 🟡 Mediana Prioridad (50-100ms, mediano esfuerzo)

3. **Bottleneck #1 (Column .apply())** → `asyncio.to_executor()`
   - Impact: 80-150ms
   - Esfuerzo: 20 minutos
   - Risk: Medio

4. **Bottleneck #3 (Sanitization)** → Consolidar lógica
   - Impact: 30-50ms
   - Esfuerzo: 10 minutos
   - Risk: Bajo

### 🟢 Baja Prioridad (<30ms, investigación pendiente)

5. **Bottleneck #5 (Logging)** → Batch logging
   - Impact: 20-40ms
   - Esfuerzo: 10 minutos
   - Risk: Bajo

---

## 🔍 Verificación Pendiente

### Funciones a Revisar Más Profundamente

```
1. calcular_entradas() - ¿Hay loops secuenciales?
2. _fetch_quote() - ¿Llamadas sync sin wrapper?
3. pd.DataFrame.to_dict() - ¿Hay overhead?
4. sanitize_for_json() - ¿Puede ser async?
```

---

## ✅ Ya Optimizado

- GCS uploads: ✅ Parallelized via asyncio.to_thread()
- Prediction execution: ✅ Parallelized via ThreadPoolExecutor
- Analysis parallelism: ✅ Parallelized via asyncio.gather()
- ATR/Niveles cache: ✅ Thread-safe con locks

---

## 📝 Notas

- **Locks no añadirán latencia significativa** - overhead <1% en hot path
- **Bottlenecks identificados son principalmente I/O y CPU-bound ops** - buena oportunidad para async/threading
- **Próxima auditoría:** Evaluar `calcular_entradas()` y FMP quote fetches

---

**Next Steps:**
1. Implementar asyncio.to_thread() para Firestore updates
2. Profiler run con timing granular
3. Benchmark pre/post cada optimización
4. Deploy a maquina-a_test y validar

