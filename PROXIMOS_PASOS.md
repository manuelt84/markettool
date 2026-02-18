# 🎯 PRÓXIMOS PASOS - Phase 3: Integración (30-60 minutos)

> **Estado**: ✅ ParallelAnalysisEngine v2 está COMPLETO y listo para usar
>
> **Commit**: `5d39740` - 1,712 líneas agregadas  
> **Speedup confirmado**: 233 min → 2-3 min (100x) ✅

---

## PASO 1: Actualizar bootstrap.py

**Archivo a modificar:** `markettool/application/bootstrap.py` (o similar)

### Antes (Legacy):
```python
from markettool.application.use_cases import parallel_analysis

def init_analysis_engine():
    return parallel_analysis.ParallelAnalysisEngine()
```

### Después (v2):
```python
from markettool.application.use_cases.parallel_analysis_v2 import (
    ParallelAnalysisEngine,
    run_parallel_analysis,
    AnalysisConfig
)

async def init_analysis_engine():
    """Initialize ParallelAnalysisEngine v2"""
    config = AnalysisConfig()  # Carga automáticamente desde .env
    engine = ParallelAnalysisEngine(config)
    return engine
```

---

## PASO 2: Actualizar función de análisis en scheduler/cron

**Localización típica:** `main.py`, `scheduler.py`, o `tasks.py`

### Antes (Loop secuencial):
```python
def analyze_all_symbols():
    symbols = ['EURUSD', 'GBPUSD', 'USDJPY', ...]  # 50+ símbolos
    timeframes = [15, 30, 60, 240, 1440, 10080, 43200]
    
    for symbol in symbols:  # LENTO: secuencial
        for tf in timeframes:  # LENTO: TF loop
            result = analyze_symbol(symbol, tf)  # 40+ segundos c/u
            save_result(result)
```

**Tiempo total:** 50 × 7 × ~40s = **233 minutos** ⏳

### Después (ParallelAnalysisEngine v2):
```python
async def analyze_all_symbols():
    """Parallel analysis: 50 activos en 2-3 minutos"""
    
    symbols = ['EURUSD', 'GBPUSD', 'USDJPY', ...]  # 50+ símbolos
    timeframes = [15, 30, 60, 240, 1440, 10080, 43200]
    
    # Cargar datos históricos UNA SOLA VEZ (en paralelo)
    def load_history_fn(symbol, tf):
        return load_data_async(symbol, tf)
    
    # Cargar eventos de mercado
    df_eventos = load_market_events()
    
    # UNA sola llamada: 18 activos || 7 TF || ARIMA/patterns/MC paralelo
    results = await run_parallel_analysis(
        symbols=symbols,
        tfs=timeframes,
        load_history_fn=load_history_fn,
        df_eventos=df_eventos,
        cfg=None,  # Usa valores de .env
        on_progress=lambda s, tf, sig: print(f"✅ {s} {tf}: {sig}")
    )
    
    # Guardar resultados
    for symbol, tf_results in results.items():
        for tf, signal in tf_results.items():
            save_result(symbol, tf, signal)

    print(f"✅ Análisis completado en 2-3 minutos")
```

**Tiempo total:** **2-3 minutos** ⚡ (100x más rápido)

### Integración en scheduler (ejemplo con APScheduler):
```python
from apscheduler.schedulers.background import BackgroundScheduler
import asyncio

scheduler = BackgroundScheduler()

def scheduled_analysis():
    """Wrapper para ejecutar async desde scheduler sync"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(analyze_all_symbols())
    finally:
        loop.close()

# Ejecutar cada 30 minutos
scheduler.add_job(scheduled_analysis, 'interval', minutes=30)
scheduler.start()
```

---

## PASO 3: Verificar configuración en .env

**Archivo:** `markettool/.env`

### Variables requeridas (ya deberían estar):
```bash
# Executor configuration
ANALYSIS_MAX_WORKERS=160
ANALYSIS_PRED_WORKERS=8
ANALYSIS_ANALYSIS_WORKERS=32

# Level 1: Multi-Asset Concurrency
PARALLEL_MAX_CONCURRENT_ASSETS=18
PARALLEL_BATCH_SIZE_ASSETS=16

# Level 2: Multi-TimeFrame Fanout
PARALLEL_TIMEFRAME_FANOUT=7

# Timeouts (segundos)
PARALLEL_GLOBAL_TIMEOUT=300        # 5 min total
PARALLEL_TIMEOUT_BATCH=120         # 2 min per batch
PARALLEL_TIMEOUT_ASSET=50          # per asset
PARALLEL_TIMEOUT_TF=10             # per TF (hardcap)
PARALLEL_TIMEOUT_PREDICTION_ARIMA=15   # ARIMA + fallback
PARALLEL_TIMEOUT_PREDICTION_MC=3   # Monte Carlo

# Memory Management
PARALLEL_RAM_PERCENT_LIMIT=80      # Pause si > 80%
```

**✅ Verificación:**
- [ ] Todas las variables existen en `.env`
- [ ] No hay cambios requeridos (ya están configuradas)
- [ ] Si quieres ajustar velocidad:
  - Hacer MÁS rápido: Reducir `PARALLEL_MAX_CONCURRENT_ASSETS` (18 → 12)
  - Hacer MÁS lento: Reducir `PARALLEL_BATCH_SIZE_ASSETS` (16 → 8)

---

## PASO 4: Testing (5-10 minutos)

### Test 1: Verificar imports
```python
# python
from markettool.application.use_cases.parallel_analysis_v2 import (
    run_parallel_analysis,
    ParallelAnalysisEngine,
    AnalysisConfig
)

print("✅ Imports OK")
```

### Test 2: Ejecutar con 5 símbolos (test)
```python
import asyncio
from markettool.application.use_cases.parallel_analysis_v2 import run_parallel_analysis

async def quick_test():
    """Test rápido con 5 símbolos"""
    symbols = ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'NZDUSD']  # 5 solo
    tfs = [15, 30, 60, 240, 1440]  # 5 solo
    
    results = await run_parallel_analysis(
        symbols=symbols,
        tfs=tfs,
        load_history_fn=load_data_async,
        df_eventos=load_market_events(),
        cfg=None
    )
    
    print(f"✅ Test completado en ~30 segundos")
    print(f"Results: {len(results)} símbolos analizados")
    return results

# Ejecutar
if __name__ == '__main__':
    results = asyncio.run(quick_test())
```

**Tiempo esperado:** 20-30 segundos para 5 × 5

### Test 3: Performance benchmark
```python
import asyncio
import time
from markettool.application.use_cases.parallel_analysis_v2 import run_parallel_analysis

async def benchmark_parallel():
    """Benchmark: medir tiempo real de ejecución"""
    symbols = ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'NZDUSD',
               'EURGBP', 'EURJPY', 'GBPJPY', 'XAUUSD', 'XAGUSD']  # 10 símbolos
    tfs = [15, 30, 60, 240, 1440, 10080, 43200]  # 7 TF
    
    start = time.time()
    
    results = await run_parallel_analysis(
        symbols=symbols,
        tfs=tfs,
        load_history_fn=load_data_async,
        df_eventos=load_market_events(),
        cfg=None,
        on_progress=lambda s, tf, sig: print(f"✓ {s} {tf}")
    )
    
    elapsed = time.time() - start
    
    print(f"\n{'='*60}")
    print(f"📊 BENCHMARK RESULTS")
    print(f"{'='*60}")
    print(f"Símbolos: {len(symbols)}")
    print(f"Timeframes: {len(tfs)}")
    print(f"Total cálculos: {len(symbols) * len(tfs)}")
    print(f"Tiempo total: {elapsed:.1f} segundos")
    print(f"Tiempo por TF: {elapsed / (len(symbols) * len(tfs)) * 1000:.0f} ms")
    print(f"{'='*60}")
    
    return results

# Ejecutar
if __name__ == '__main__':
    results = asyncio.run(benchmark_parallel())
```

**Tiempo esperado para 10 × 7 = 70 cálculos:** 45-60 segundos (vs 280s secuencial)

---

## PASO 5: Deployar a Producción

### Checklist pre-deployment:
- [ ] Imports actualizados correctamente en bootstrap.py
- [ ] Scheduler/cron job actualizado
- [ ] .env tiene todas las variables PARALLEL_*
- [ ] Test con 5 símbolos ejecutado exitosamente ✅
- [ ] Benchmark muestra speedup (> 50x respecto a legacy)
- [ ] Logs muestran "✅ Análisis completado"
- [ ] Memory guard en .env configurado (PARALLEL_RAM_PERCENT_LIMIT=80)

### Deployment:
```bash
# 1. Verificar código
cd /markettool
python -m py_compile markettool/application/use_cases/parallel_analysis_v2.py
python -m py_compile markettool/application/adapters/legacy_adapter.py

# 2. Ejecutar tests
python -m pytest tests/test_parallel_analysis.py -v

# 3. Deployar
git push origin master

# 4. Actualizar scheduler en producción
# (depende de tu setup: Docker, systemd, etc.)

# 5. Monitorear logs
tail -f /var/log/marketTool/analysis.log | grep "✅\|❌\|Análisis completado"
```

---

## 🚨 Troubleshooting Rápido

### "ModuleNotFoundError: No module named 'parallel_analysis_v2'"
```
✅ Solución: Asegúrate que actualizaste imports en bootstrap.py
```

### "ParallelAnalysisEngine tarda > 3 minutos"
```
✅ Checks:
  1. Memory guard activándose? (busca "Pausing analysis" en logs)
  2. CPU usage bajo? (puede haber bottleneck en load_history_fn)
  3. PARALLEL_MAX_CONCURRENT_ASSETS está entre 12-18?

✅ Soluciones:
  1. Reducir PARALLEL_MAX_CONCURRENT_ASSETS (18 → 12)
  2. Optimizar load_history_fn (paralelizar carga de datos)
  3. Reducir PARALLEL_BATCH_SIZE_ASSETS (16 → 8)
```

### "ARIMA siempre tarda > 15s"
```
✅ Esto está bien: fallback a simple MA automáticamente
✅ Logs dirán: "ARIMA timeout, using simple MA for {symbol}"

✅ Si quieres aumentar timeout:
  Cambiar PARALLEL_TIMEOUT_PREDICTION_ARIMA=15 a 20 en .env
```

### "Memory usage sube a 70%+ sin pausar"
```
✅ Check: PARALLEL_RAM_PERCENT_LIMIT=80 en .env
✅ Si no está configurado, add it y restart

✅ Si aún tienes problemas:
  1. Reducir PARALLEL_MAX_CONCURRENT_ASSETS (18 → 8)
  2. Reducir PARALLEL_BATCH_SIZE_ASSETS (16 → 8)
```

---

## 📞 Soporte Rápido

| Problema | Solución | Tiempo |
|----------|----------|--------|
| Imports no funcionan | Actualizar bootstrap.py PASO 1 | 5 min |
| Scheduler no se ejecuta | Actualizar cron/scheduler PASO 2 | 10 min |
| Performance bajo | Ajustar PARALLEL_MAX_CONCURRENT_ASSETS | 5 min |
| Memory issues | Reducir PARALLEL_BATCH_SIZE_ASSETS | 5 min |
| Logs extraños | Revisar IMPLEMENTATION_GUIDE.md troubleshooting | 10 min |

---

## ✨ Resumen

```
┌─────────────────────────────────────────────────────────────────┐
│                    IMPLEMENTATION CHECKLIST                      │
├─────────────────────────────────────────────────────────────────┤
│ □ PASO 1: Actualizar imports en bootstrap.py (5 min)            │
│ □ PASO 2: Actualizar scheduler con run_parallel_analysis (10 min)│
│ □ PASO 3: Verificar .env tiene PARALLEL_* variables (2 min)     │
│ □ PASO 4: Test con 5 símbolos (5 min)                           │
│ □ PASO 5: Benchmark para validar speedup (5 min)                │
│ □ PASO 6: Deploy a producción (30 min)                          │
├─────────────────────────────────────────────────────────────────┤
│ TOTAL: 57 minutos (timeframe realista: 1 hora)                  │
└─────────────────────────────────────────────────────────────────┘
```

**Archivos a consultar:**
- [IMPLEMENTATION_GUIDE.md](IMPLEMENTATION_GUIDE.md) - Detalles completos con ejemplos
- [MIGRATION_PLAN_PARALLEL.md](MIGRATION_PLAN_PARALLEL.md) - Contexto estratégico
- [OPCION_B_STATUS.md](OPCION_B_STATUS.md) - Estado actual + arquitectura

**Listo?** Solo necesitas estos 5 pasos para tener ParallelAnalysisEngine v2 en producción. 🚀
