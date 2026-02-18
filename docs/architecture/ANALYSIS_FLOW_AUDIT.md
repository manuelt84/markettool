# 📊 Auditoría: Flujo de Análisis de Activos y Cálculo de Entradas

**Fecha:** 2025-02-14  
**Función Principal:** `procesar_simbolo_temporalidad()`  
**Ubicación:** MarketTool.py líneas ~11700-13100

---

## 🎯 Diagrama del Flujo

```
procesar_simbolo_temporalidad(symbol, temporalidad, df_eventos, user_chat_id)
│
├─ ✅ FASE 1: PARALELIZACIÓN (línea ~11715)
│  │
│  ├─ future_patrones = ThreadPoolExecutor.submit(detectar_patrones_confirmados_velas)
│  ├─ future_rango = ThreadPoolExecutor.submit(detectar_rango_zigzag)
│  ├─ future_tecnica = ThreadPoolExecutor.submit(analisis_tecnico_detallado)
│  └─ future_fundamental = ThreadPoolExecutor.submit(ajustar_probabilidad_fundamental)
│     └─ [4 tasks corren en paralelo] ✅ WELL PARALLELIZED
│
├─ ✅ FASE 2: RECOLECCIÓN DE RESULTADOS (línea ~11755)
│  │
│  ├─ future_patrones.result()        → patrones_detectados
│  ├─ future_rango.result()           → en_rango (rango_dinamico, estructura_tendencia, etc)
│  ├─ future_tecnica.result()         → tecnica_meta
│  └─ future_fundamental.result()     → prob_funda, fundamental_meta
│     └─ [Secuencial, espera a todos] ⚠️ BLOCKING si alguno es lento
│
├─ ✅ FASE 3: PREDICCIONES (línea ~11802)
│  │
│  ├─ future_arima = ThreadPoolExecutor.submit(predecir_arima)
│  ├─ future_mm = ThreadPoolExecutor.submit(predecir_media_movil)
│  └─ future_mc = ThreadPoolExecutor.submit(_wrapper_simulacion_monte_carlo)
│     ├─ [3 tasks corren en paralelo] ✅ WELL PARALLELIZED
│     └─ future_*.result(timeout=30) [Secuencial recolección]
│
├─ ✅ FASE 4: CACHE & NIVELES (línea ~11831)
│  │
│  ├─ _get_cached_niveles(cache_key)
│  └─ if not cached:
│     └─ ajustar_window_dinamico_optimizado()  ⚠️ COSTOSO si no hit en cache
│        └─ _cache_niveles() [almacenar en cache]
│
├─ 🔴 FASE 5: CÁLCULOS SECUENCIALES (línea ~11870+)
│  │
│  ├─ probabilidad_tecnica = ajustar_probabilidad_tecnica()  ⚠️ NO paralelizado
│  ├─ probabilidad_general = calcular_probabilidad_general()
│  ├─ zona_no_trading = verificar_zona_no_trading()
│  ├─ zona_sobreventa = verificar_zona_sobreventa()
│  ├─ zona_sobrecompra = verificar_zona_sobrecompra()
│  └─ tipo_operacion = determinar_tipo_operacion()
│     └─ [Todas SECUENCIALES] ⚠️ BOTTLENECK #1
│
├─ ✅ FASE 6: CÁLCULO DE ENTRADAS (línea ~11945)
│  │
│  └─ entradas_mult = generar_entradas_multiples(
│        precio_actual, ATR, niveles, tipo_operacion, ...
│     )
│     └─ [Calcula 4-8 entradas secuencialmente] ⚠️ BOTTLENECK #2
│
├─ ✅ FASE 7: WHITELISTING (línea ~12986)
│  │
│  └─ whitelist_result = evaluar_si_autorizado_operar()
│     └─ [Evalúa config de whitelist] ✅ Rápido (<50ms típicamente)
│
└─ ✅ FASE 8: RETORNO (línea ~13000+)
   │
   └─ return {
        "Activo": symbol,
        "Temporalidad": tf,
        "Oportunidad": flag_oportunidad,
        "Patrones Detectados": patrones_detectados,
        ... (50+ campos)
     }
```

---

## 🔴 BOTTLENECK #1: Cálculos Secuenciales de Probabilidades (Línea ~11870)

**Código:**
```python
# ❌ SECUENCIAL: Se ejecutan una tras otra
probabilidad_tecnica = round(ajustar_probabilidad_tecnica(
    df, tf, window, cfg, niveles=niveles_clave, symbol=symbol
), 2)

probabilidad_general = calcular_probabilidad_general(
    probabilidad_tecnica, probabilidad_fundamental, cfg
)

zona_no_trading = verificar_zona_no_trading(df, window) if verificar_znt else False
zona_no_trading_evento = bool(isinstance(fundamental_meta, dict) and fundamental_meta.get("blackout") is True)

zona_sobreventa = verificar_zona_sobreventa(df, window)
zona_sobrecompra = verificar_zona_sobrecompra(df, window)

tipo_operacion = determinar_tipo_operacion(...)
confluencia = evaluar_confluencia_trade(...)
```

**Problema:**
- 6-7 funciones ejecutadas **secuencialmente**
- Dependencias entre algunas (ej: `tipo_operacion` usa `probabilidad_tecnica`)
- Pero `zona_no_trading`, `zona_sobreventa`, `zona_sobrecompra` **podrían paralelizarse**
- Estimado: 50-100ms bloqueante por analysis

**Dependencias:**
```
analisis_tecnico (paralelo) → probabilidad_tecnica ─┐
                                                     ├─ tipo_operacion
ajustar_probabilidad_fundamental (paralelo) ────────┤
                                                     ├─ confluencia
patrones_detectados (paralelo) ──────────────────────┘

zona_sobreventa (df, window)  ─┐
zona_sobrecompra (df, window) ─┼─ [SIN DEPENDENCIA ENTRE ELLAS]
                               └─ Podrían paralelizarse
```

**Solución Propuesta:**
```python
# ✅ PARALELIZADO: Usar ThreadPoolExecutor para independents
_verify_exec = ThreadPoolExecutor(max_workers=4)

future_zona_sb = _verify_exec.submit(verificar_zona_sobreventa, df, window)
future_zona_sc = _verify_exec.submit(verificar_zona_sobrecompra, df, window)
future_zona_nt = _verify_exec.submit(verificar_zona_no_trading, df, window) if verificar_znt else None

# Estas se pueden calcular ahora
probabilidad_tecnica = round(ajustar_probabilidad_tecnica(...), 2)
probabilidad_general = calcular_probabilidad_general(probabilidad_tecnica, probabilidad_fundamental, cfg)

# Esperar resultados
zona_sobreventa = future_zona_sb.result(timeout=10)
zona_sobrecompra = future_zona_sc.result(timeout=10)
zona_no_trading = future_zona_nt.result(timeout=10) if future_zona_nt else False

# Puis tipo_operacion (depende de probabilidades)
tipo_operacion = determinar_tipo_operacion(...)
```

**Ganancia Estimada:** 30-60ms de paralelization (si `verificar_zona_* ` toman ~20-30ms cada una)

---

## 🔴 BOTTLENECK #2: generar_entradas_multiples() - Cálculos Secuenciales (Línea ~11945)

**Código (Line ~11945 en procesar_simbolo_temporalidad):**
```python
entradas_mult = generar_entradas_multiples(
    precio_actual=precio_actual,
    ATR=ATR,
    niveles=niveles_clave,
    tipo_operacion=tipo_operacion,
    en_rango=en_rango,
    prob_general=probabilidad_general,
    bollinger_upper=bollinger_upper,
    bollinger_lower=bollinger_lower,
    señales_compra=señales_compra,
    señales_venta=señales_venta,
)
```

**Dentro de `generar_entradas_multiples()` (línea ~11400+):**
```python
# ❌ SECUENCIAL: Genera entradas una por una
entries = []

# Si tipo_operacion es COMPRA:
_add_entry(entries, side="long", entry=precio_mid, ...)
_add_entry(entries, side="long", entry=precio_pb_s1, ...)
_add_entry(entries, side="long", entry=precio_pb_s2, ...)
_add_entry(entries, side="long", entry=precio_breakout, ...)

# O si VENTA:
_add_entry(entries, side="short", entry=..., ...)
# etc...

# Luego
return sorted(entries, key=lambda e: e.get('score'))  # O-rdering secuencial
```

**Problema:**
- Cada entrada se calcula **secuencialmente**
- Validaciones: check TP/SL (calcular RRR), deduping, scoring
- Para 4-8 entradas: ~50-100ms típico
- `sorted()` al final es O(n log n) pero n<8, así que ~<1ms

**Dependencias:**
```
Nivel de entrada (precio_mid, precio_pb_s1, etc) ─┐
ATR, multiplicadores ────────────────────────────┼─ _add_entry() [INDEPENDIENTES]
niveles_clave ──────────────────────────────────┘

├─ Calcular TP/SL
├─ Validar RRR 
└─ Compute score
```

**Solución Propuesta:**
```python
# ✅ PARALELIZADO: generar candidatos de entrada en paralelo
_entry_exec = ThreadPoolExecutor(max_workers=8)

futures = []
for entry_config in [
    {"entry": precio_mid, "name": "mid", ...},
    {"entry": precio_pb_s1, "name": "pullback_s1", ...},
    # ... más configuraciones
]:
    future = _entry_exec.submit(_create_entry_candidate, entry_config)
    futures.append(future)

entries = []
for future in futures:
    candidate = future.result(timeout=5)
    if candidate:
        entries.append(candidate)

return sorted(entries, key=lambda e: e.get('score'))
```

**Ganancia Estimada:** 30-60ms de paralelization (si cada entrada toma ~20ms)

---

## 🟡 BOTTLENECK #3: Cache Miss en Niveles Dinámicos (Línea ~11831)

**Código:**
```python
cache_key = _get_niveles_cache_key(symbol, tf, len(df), precio_actual)
soportes_cached, resistencias_cached = _get_cached_niveles(cache_key)

if soportes_cached is not None and resistencias_cached is not None:
    # Cache HIT - rápido
    soportes_dinamicos = soportes_cached
    resistencias_dinamicas = resistencias_cached
else:
    # Cache MISS - costoso
    df, soportes_dinamicos, resistencias_dinamicas = ajustar_window_dinamico_optimizado(
        df, symbol, tf, precio_actual, calc_windows, max_incremento=5, ...
    )
```

**Problema:**
- `ajustar_window_dinamico_optimizado()` es **muy costoso** (~100-200ms)
- Ejecutado **dentro del flujo principal** cuando hay cache miss
- Bloquea todo lo demás

**Estadísticas Esperadas:**
- Cache hit rate: ~70-80% (durante análisis de mismo symbol/tf)
- Cache miss: ~20-30% 
- Impacto: Algunos análisis toman <1s, otros 1.5-2s (dependiendo de cache)

**Solución Propuesta:**
```python
# ✅ PARALELIZADO: Calcular niveles en background si miss
cache_key = _get_niveles_cache_key(symbol, tf, len(df), precio_actual)
soportes_cached, resistencias_cached = _get_cached_niveles(cache_key)

future_niveles = None
if soportes_cached is not None and resistencias_cached is not None:
    soportes_dinamicos = soportes_cached
    resistencias_dinamicas = resistencias_cached
else:
    # IMPORTANT: Submit calculo para background ejecución MIENTRAS continuamos
    # pero usamos valores fallback por ahora
    future_niveles = _ANALYSIS_INNER_EXECUTOR.submit(
        ajustar_window_dinamico_optimizado,
        df, symbol, tf, precio_actual, calc_windows, max_incremento=5, ...
    )
    # Fallback: usar niveles simples
    soportes_dinamicos = [precio_actual * 0.95, precio_actual * 0.90]
    resistencias_dinamicas = [precio_actual * 1.05, precio_actual * 1.10]

# ... continuar cálculos (confluencia, entradas, etc)

# Al final, si hay future, reemplazar con valores calculados
if future_niveles:
    try:
        _, sup, res = future_niveles.result(timeout=5)
        soportes_dinamicos = sup
        resistencias_dinamicas = res
        _cache_niveles(cache_key, sup, res)
    except Exception:
        pass  # usar fallback
```

**Ganancia Estimada:** 100-200ms (cuando cache miss - paralelizar con resto del flujo)

---

## 🔍 Análisis de Tiempos por Fase

| Fase | Función | Duración Est. | Parallelizable | Status |
|------|---------|--------------|----------------|--------|
| 1 | detectar_patrones_confirmados_velas | 30-50ms | ✅ Sí (ya paralelo) | ✅ OK |
| 2 | detectar_rango_zigzag | 40-60ms | ✅ Sí (ya paralelo) | ✅ OK |
| 3 | analisis_tecnico_detallado | 50-100ms | ✅ Sí (ya paralelo) | ✅ OK |
| 4 | ajustar_probabilidad_fundamental | 50-150ms | ✅ Sí (ya paralelo) | ✅ OK |
| 5 | predecir_arima | 50-80ms | ✅ Sí (ya paralelo) | ✅ OK |
| 6 | predecir_media_movil | 10-20ms | ✅ Sí (ya paralelo) | ✅ OK |
| 7 | _wrapper_simulacion_monte_carlo | 40-80ms | ✅ Sí (ya paralelo) | ✅ OK |
| 8 | ajustar_window_dinamico (cache miss) | 100-200ms | ⚠️ Parcial | 🔴 SLOW |
| 9 | ajustar_probabilidad_tecnica | 20-40ms | ✅ Sí (ahora es serial) | 🟡 LINEAR |
| 10 | verificar_zona_sobreventa | 10-20ms | ✅ Sí | 🔴 SERIAL |
| 11 | verificar_zona_sobrecompra | 10-20ms | ✅ Sí | 🔴 SERIAL |
| 12 | verificar_zona_no_trading | 10-20ms | ✅ Sí | 🔴 SERIAL |
| 13 | generar_entradas_multiples | 50-100ms | ✅ Sí (candidatos) | 🔴 SERIAL |
| 14 | evaluar_confluencia_trade | 20-40ms | ✅ Sí | 🔴 SERIAL |

**Timeline Actual (Critical Path):**
```
Sequential (9-14): 130-240ms
Parallel (1-7): ~100-150ms (overlapped, so adds ~0ms)
Cache Miss (8): +100-200ms (sometimes)

Total: 230-390ms per analysis
```

**Timeline Optimized (Post-fixes):**
```
Parallel Phase (1-7): 150ms [baseline]
Parallel Zones (10-12): 0ms [concurrent with 1-7]
Entradas (13): 0-30ms [concurrent with others]
Confluencia (14): 0ms [concurrent]
Cache Miss (8): 0ms [background, concurrent]

Total Potential: 150-180ms (vs 230-390ms now)
= 25-50% SPEEDUP per analysis
```

---

## 📋 Recomendaciones de Optimización

### ✅ Already Well Parallelized
1. **detectar_patrones_confirmados_velas** - ThreadPoolExecutor
2. **detectar_rango_zigzag** - ThreadPoolExecutor
3. **analisis_tecnico_detallado** - ThreadPoolExecutor
4. **ajustar_probabilidad_fundamental** - ThreadPoolExecutor
5. **Predicciones (ARIMA, Media Móvil, Monte Carlo)** - ThreadPoolExecutor

### 🔴 Needs Parallelization

| Priority | Bottleneck | Fix | Effort | Gain |
|----------|-----------|-----|--------|------|
| **HIGH** | BOTTLENECK #1: Zona verification (sobreventa/sobrecompra/no_trading) | Submit all 3 to executor | 20min | 30-60ms |
| **HIGH** | BOTTLENECK #2: generar_entradas_multiples (candidates) | Parallelize entry candidate creation | 30min | 30-60ms |
| **MED** | BOTTLENECK #3: ajustar_window_dinamico cache miss | Background execution | 25min | 100-200ms (sometimes) |
| **LOW** | ajustar_probabilidad_tecnica | Could be parallelized but depends on df indicators | 15min | <10ms |

---

## 🎯 Action Plan

### Next Steps (in order):

1. **BOTTLENECK #1 (Zona Verifications)** - 30-60ms gain
   - Wrap `verificar_zona_sobreventa`, `verificar_zona_sobrecompra`, `verificar_zona_no_trading` in ThreadPoolExecutor
   - Submit all 3 concurrently
   - Collect results before `tipo_operacion` calculation
   - Effort: 20 min
   - Risk: Low (independent functions)

2. **BOTTLENECK #2 (Entry Generation)** - 30-60ms gain
   - Refactor `generar_entradas_multiples()` to submit candidates in parallel
   - Collect and sort results
   - Effort: 30 min
   - Risk: Medium (validation logic changes)

3. **BOTTLENECK #3 (Cache Miss Background)** - 100-200ms conditional gain
   - Submit `ajustar_window_dinamico_optimizado()` to executor if cache miss
   - Use fallback values meanwhile
   - Replace with real values when available
   - Effort: 25 min
   - Risk: Medium-High (complex state management)

---

## 📚 Related Documentation

- [BOTTLENECK_AUDIT.md](./BOTTLENECK_AUDIT.md) - General bottleneck audit
- [THREAD_SAFETY_FIXES.md](./THREAD_SAFETY_FIXES.md) - Lock implementations
- [PARALLEL_GCP_UPLOADS_IMPLEMENTATION.md](./PARALLEL_GCP_UPLOADS_IMPLEMENTATION.md) - Upload parallelization

---

**Status:** 🔍 IDENTIFIED - Ready for optimization

