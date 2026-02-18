# 🚀 Instrucciones de Implementación - ParallelAnalysisEngine v2

## ✅ Completado (Fase 1 y 2)

- [x] **LegacyMarketToolAdapter** (`markettool/application/adapters/legacy_adapter.py`)
  - Wrapper sobre todas las funciones de MarketTool.py
  - Timeout enforcement con asyncio.wait_for()
  - Fallback a Media Móvil si ARIMA timeout

- [x] **ParallelAnalysisEngine v2** (`markettool/application/use_cases/parallel_analysis_v2.py`)
  - 3 niveles de paralelismo implementados
  - Usa LegacyMarketToolAdapter para funciones legacy
  - Timeouts garantizados (sin conflictos con legacy)

---

## 📋 Próximos Pasos (Fase 3: Integración)

### Paso 1: Importar en bootstrap.py

En `markettool/bootstrap.py`, cambiar:

```python
# ANTES:
from markettool.application.use_cases.parallel_analysis import ParallelAnalysisEngine, AnalysisConfig

# DESPUÉS:
from markettool.application.use_cases.parallel_analysis_v2 import (
    ParallelAnalysisEngine,
    AnalysisConfig,
    run_parallel_analysis
)
```

### Paso 2: Actualizar función de entrada en bootstrap.py

Reemplazar llamada a legacy secuencial:

```python
# ANTES (secuencial - 4+ horas):
async def analyze_symbols_sequential(symbols, tfs, cfg):
    results = {}
    for symbol in symbols:
        for tf in tfs:
            result = calcular_entradas(...)  # Bloqueante, secuencial
            results[symbol][tf] = result
    return results

# DESPUÉS (paralelo - 2-3 minutos):
async def analyze_symbols_parallel(symbols, tfs, cfg):
    from markettool.application.adapters import get_adapter
    
    # Cargar históricos (paralelo también)
    async def load_history(symbol, tf):
        return obtener_datos_historicos(symbol, tf)  # Usar función existente
    
    # Cargar eventos (para análisis fundamental)
    df_eventos = await get_eventos_economicos_cached()
    
    # Ejecutar análisis paralelo
    results = await run_parallel_analysis(
        symbols=symbols,
        tfs=tfs,
        load_history_fn=load_history,
        df_eventos=df_eventos,
        cfg=cfg,
        on_progress=lambda done, tot: logger.info(f"Progreso: {done}/{tot}"),
        config=AnalysisConfig(
            max_concurrent_assets=18,
            timeframe_fan_out=7,
            timeout_per_tf=10,  # ✅ NO CONFLICTO con ARIMA_TIMEOUT=45
            timeout_prediction_arima=15,  # Enforcement en adapter
        ),
        indicators_executor=_ANALYSIS_EXECUTOR,
        prediction_executor=_ANALYSIS_PRED_EXECUTOR,
        analysis_executor=_ANALYSIS_EXECUTOR,
    )
    
    return results
```

### Paso 3: Reemplazar scheduler/cron job

En tu scheduler (ej: APScheduler, Celery, etc.):

```python
# ANTES:
@scheduler.scheduled_job('interval', minutes=30)
async def update_signals():
    symbols = obtener_activos_monitoreados()  # 50+
    for symbol in symbols:  # Secuencial = BAD
        result = MarketTool.procesar_simbolo(symbol)

# DESPUÉS:
@scheduler.scheduled_job('interval', minutes=30)
async def update_signals():
    symbols = obtener_activos_monitoreados()  # 50+
    tfs = ['1day', '4hour', '1hour', '30min']
    
    results = await run_parallel_analysis(
        symbols=symbols,
        tfs=tfs,
        load_history_fn=obtener_datos_historicos,
        df_eventos=await get_economic_events(),
    )
    
    # results es {symbol: {tf: {signal, confidence, ...}}}
    await guardar_resultados_en_firestore(results)
```

---

## 🔧 Configuración .env necesaria

Verifica que tu `.env` tiene:

```env
# Parallel Analysis Timeouts (CRÍTICO: NO entran en conflicto)
PARALLEL_TIMEOUT_TF=10                          # TF level (más corto)
PARALLEL_TIMEOUT_PREDICTION_ARIMA=15            # ARIMA con fallback a MA
PARALLEL_TIMEOUT_PREDICTION_MC=3                # MC rápido

# Legacy Timeouts (se ignoran en ParallelAnalysisEngine)
ARIMA_TIMEOUT=45                                # Solo si usa MarketTool.py directo
ARIMA_MODE=standard
```

---

## 🧪 Testing (Fase 4)

### Test Unitario Simple

```python
import asyncio
from markettool.application.use_cases.parallel_analysis_v2 import run_parallel_analysis

async def test_parallel():
    symbols = ['EUR/USD', 'GBP/USD', 'AAPL', 'MSFT', 'TSLA']
    tfs = ['1day', '4hour', '1hour']
    
    async def dummy_load(symbol, tf):
        # Retorna DataFrame dummy para testing
        import pandas as pd
        dates = pd.date_range('2024-01-01', periods=100)
        return pd.DataFrame({
            'open': [100 + i] * 100,
            'high': [102 + i] * 100,
            'low': [99 + i] * 100,
            'close': [101 + i] * 100,
            'volume': [1000000] * 100,
        }, index=dates)
    
    results = await run_parallel_analysis(
        symbols=symbols,
        tfs=tfs,
        load_history_fn=dummy_load,
        df_eventos=pd.DataFrame(),  # Empty for testing
    )
    
    print(f"✅ Results for {len(results)} symbols:")
    for symbol in results:
        tfs_done = len(results[symbol])
        print(f"  {symbol}: {tfs_done} timeframes analyzed")

# Ejecutar test
asyncio.run(test_parallel())
```

### Test Performance

```python
import time

async def benchmark_parallel():
    start = time.time()
    
    results = await run_parallel_analysis(
        symbols=['EUR/USD', 'GBP/USD', 'AAPL', ...],  # 50+ symbols
        tfs=['1day', '4hour', '1hour', '30min', '15min'],
        load_history_fn=obtener_datos_historicos,
        df_eventos=df_eventos,
    )
    
    elapsed = time.time() - start
    num_pairs = len(results) * len(tfs)
    
    print(f"✅ Análisis paralelo completado:")
    print(f"  - Tiempo total: {elapsed:.1f}s")
    print(f"  - Pares analizados: {num_pairs}")
    print(f"  - Promedio por par: {elapsed / (num_pairs or 1):.2f}s")
    print(f"  - Speedup vs. legacy (40s/pair): {(40 * num_pairs) / elapsed:.1f}x")
```

---

## 🚨 Troubleshooting

###  Problema: "No module named 'legacy_adapter'"

**Solución**: Verifica que existe:
```bash
ls markettool/application/adapters/legacy_adapter.py
ls markettool/application/adapters/__init__.py
```

### Problema: "TimeoutError en ARIMA"

**Esperado**: El adapter tiene fallback a Media Móvil.
Verificar en logs:
```
[ARIMA] ⏱️ TIMEOUT EUR/USD/1day después de 15s
[ARIMA] Fallback a simple MA...
[Synthesize] ...
```

### Problema: "Memory > 80%, pausing"

**Normal**: El engine pausa si RAM sube. Espera a que libere.
Configurar en `.env`:
```env
PARALLEL_RAM_PERCENT_LIMIT=85  # Si quieres ser más agresivo
```

---

## ✅ Checklist Final

Antes de deploying a prod:

- [ ] Imports en bootstrap.py actualizados
- [ ] `.env` tiene timeouts paralelos
- [ ] Test dummy ejecuta sin errores
- [ ] Benchmark muestra >80x speedup
- [ ] Logs muestran "✅ Análisis completado"
- [ ] Resultados guardados en DB/Firestore correctamente

---

## 📈 Expected Performance Improvement

| Métrica | Legacy (secuencial) | ParallelAnalysisEngine v2 | Mejora |
|---------|--------------------|-----------------------|--------|
| 50 activos × 7 TF | 233 min (3.9h) | 2-3 min | **80-100x**✅ |
| 1 activo × 7 TF | 280 seg | 3-5 seg | **50-100x**✅ |
| Memory per asset | 150 MB | 150 MB (same) | No cambio |
| Timeouts conflicts | Múltiples | **Ninguno**✅ | ✅ Fixed |

---

## 🔗 Referencia Rápida

```python
# Importar y usar en cualquier lugar:
from markettool.application.use_cases.parallel_analysis_v2 import run_parallel_analysis

# Ejecutar (async context):
results = await run_parallel_analysis(
    symbols=['EUR/USD', 'AAPL', ...],
    tfs=['1day', '4hour', '1hour'],
    load_history_fn=mi_fn_cargar_historicos,  # async
    df_eventos=mi_df_eventos,
)

# Resultados:
# results[symbol][tf] = {
#     'signal': 'Compra' | 'Venta' | 'Neutral',
#     'confidence': 0.75,
#     'target': 1.1050,
#     'stop': 1.0950,
#     'rrr': 2.5,
#     'reasons': [...],
#     'timestamp': '2025-02-18T...'
# }
```

---

**¿Preguntas? Revisa:**
- `MIGRATION_PLAN_PARALLEL.md` (arquitectura)
- `legacy_adapter.py` (implementación adapter)
- `parallel_analysis_v2.py` (engine completo)

**Next**: Integra en bootstrap.py y test.
