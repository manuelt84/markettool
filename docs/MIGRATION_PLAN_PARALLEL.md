# 🚀 Plan de Migración: ParallelAnalysisEngine (Opción B - Reescritura Completa)

## 📋 Inventario de Funciones Legacy a Reutilizar

### Funciones Core que VIVEN en MarketTool.py
```
✅ calcular_entradas()              # 2000+ líneas, punto de entrada
✅ predecir_arima()                 # ARIMA con timeout + cache
✅ calcular_rsi()                   # Indicador
✅ calcular_macd()                  # Indicador
✅ calcular_bollinger()             # Indicador
✅ detectar_patrones_confirmados_velas()  # YOLO
✅ analisis_tecnico_detallado()     # Síntesis técnica
✅ simulacion_monte_carlo()         # MC predictions
✅ ajustar_probabilidad_fundamental() # Fundamental
```

### Problema Actual
- Todo paralelizado **dentro** de `calcular_entradas()` con ThreadPoolExecutor
- NO hay paralelismo **entre activos** (secuencial: 50 activos = 4+ horas)
- ParallelAnalysisEngine solo tiene stubs, no implementado

---

## 🎯 Arquitectura Nueva (ParallelAnalysisEngine)

### Nivel 0: Entry Point (nuevo)
```python
async def analyze_all_assets_v2(
    symbols: List[str],              # 50+ activos
    tfs: List[str],                  # [1day, 4hour, 1hour, ...]
    hist_data: Dict,                 # precargado
    events_df: pd.DataFrame,
    cfg: dict
) -> Dict[str, Dict[str, Dict]]:  # symbol -> tf -> señal
    """Reemplaza el loop secuencial actual"""
    return await engine.analyze_assets_parallel(...)
```

### Nivel 1: Multi-Asset (NUEVO PARALELISMO)
```
18 activos simultáneos
├─ Asset 1: 7 TF en paralelo
│  ├─ TF 1: calcular_entradas()
│  ├─ TF 2: calcular_entradas()
│  └─ ...
├─ Asset 2: 7 TF en paralelo
└─ ...
```

### Nivel 2: Multi-Timeframe (OPTIMIZADO)
```
7 TF paralelos por activo
├─ 1day: [patrones] + [técnica] + [fundamental] + [ARIMA] + [MC]
├─ 4hour: [patrones] + [técnica] + [fundamental] + [ARIMA] + [MC]
└─ ...
```

### Nivel 3: Entry Calculation (SIN CAMBIOS)
```
Dentro de cada TF:
- Patrones (YOLO) en thread
- Técnica en thread
- ARIMA en thread (timeout 15s)
- MC en process
- Síntesis
```

---

## 📌 Cambios Mínimos Necesarios

### 1. ParallelAnalysisEngine
**Completar stubs:**
- `_compute_indicators_fast()` → Remitir a indicadores legacy
- `_predict_arima()` → Wrapper de `predecir_arima()` actual
- `_synthesize_signal()` → Wrapper de síntesis en `calcular_entradas()`
- `_detect_candle_patterns()` → Wrapper de YOLO actual

### 2. Adapter Layer (NUEVO)
```python
# markettool/application/adapters/legacy_adapter.py
class LegacyMarketToolAdapter:
    @staticmethod
    def calculate_entries(df, df_eventos, symbol, tf, cfg):
        """Wrapper sobre calcular_entradas() con error handling"""
        # Remitir a MarketTool.calcular_entradas()
        
    @staticmethod
    def predict_arima_with_timeout(df, tf, symbol, timeout=15):
        """ARIMA con garantía de timeout"""
        # Con asyncio.wait_for() para enforcement
```

### 3. Bootstrap.py
**Cambiar entrada:**
```python
# Antes:
async def analyze_symbols_sequential(symbols, tfs, ...):
    for symbol in symbols:
        for tf in tfs:
            result = calcular_entradas(...)  # 4+ HORAS

# Después:
async def analyze_symbols_parallel(symbols, tfs, ...):
    return await engine.analyze_assets_parallel(...)  # 2-3 MINUTOS
```

### 4. MarketTool.py
**NO ELIMINAR**, mantener como biblioteca:
- Mantener todas las funciones intactas
- Solo "marcar como legacy" en docstrings
- ParallelAnalysisEngine las reutiliza vía adapter

---

## ⏱️ Timeline Estimado

### Fase 1: Fundación (1-2h)
- [ ] Crear `legacy_adapter.py`
- [ ] Entender flujo de `calcular_entradas()` completo
- [ ] Crear tests dummy

### Fase 2: Adapter Implementation (2-3h)
- [ ] Implementar `calculate_entries()` wrapper
- [ ] Implementar `predict_arima_with_timeout()`
- [ ] Implementar `synthesize_signal_wrapper()`
- [ ] Tests unitarios

### Fase 3: ParallelAnalysisEngine Core (3-4h)
- [ ] Completar `_compute_indicators_fast()`
- [ ] Completar `_predict_arima()`
- [ ] Completar `_detect_candle_patterns()`
- [ ] Completar `_synthesize_signal()`
- [ ] Integración multi-nivel

### Fase 4: Testing & Integration (1-2h)
- [ ] Tests de paralelismo
- [ ] Tests de timeout
- [ ] Validación vs. legacy
- [ ] Benchmarks de velocidad

### Fase 5: Cleanup (30min)
- [ ] Deprecate funciones si aplica
- [ ] Actualizar documentación
- [ ] Git cleanup

---

## 🔧 Decisiones Técnicas

### Timeouts en Parallelization Context
| Level | Timeout | Aplicado por | Fallback |
|-------|---------|--------------|----------|
| **Global** | 300s | asyncio root | Batch incompleto |
| **Asset** | 50s | asyncio.wait_for | Siguiente asset |
| **TF** | 10s | asyncio.wait_for | None (skip TF) |
| **ARIMA** | 15s | executor timeout | Media Móvil |

✅ **NO CONFLICTO**: Todos dentro de 10s por TF

### Memory Management
```python
if memory_usage > 80%:
    pause_new_assets()  # No lanzar nuevos
    wait_for_completes()
    resume()
```

---

## 📂 Archivos a Crear/Modificar

### CREATE
```
markettool/application/adapters/
├── __init__.py
├── legacy_adapter.py          # ← NUEVO (wrapper sobre legacy)
└── parallel_engine_v2.py      # ← Completar (reemplaza parallel_analysis.py)
```

### MODIFY
```
markettool/bootstrap.py         # Entrada: analyze_assets_parallel
markettool/application/use_cases/parallel_analysis.py  # Completar stubs
.env                            # Confirmar timeouts
```

### KEEP (no eliminar)
```
marketTool/MarketTool.py        # Funciones reutilizadas
```

---

## ✅ Definition of Done

- [ ] 50 assets procesados en < 3 minutos
- [ ] 0 timeout conflicts
- [ ] Output igual al legacy (Fase 1)
- [ ] 100% test coverage en adapter
- [ ] Documentación actualizada
- [ ] Sin código duplicado

---

**START: Fase 1 = Ahora**
