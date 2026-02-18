╔════════════════════════════════════════════════════════════════════════╗
║                    🚀 OPTIMIZACIÓN DE PERFORMANCE                       ║
║              Paralelismo Mejorado + Timeouts Agresivos                  ║
╚════════════════════════════════════════════════════════════════════════╝

📊 PROBLEMA IDENTIFICADO
══════════════════════════════════════════════════════════════════════════

Logs muestran:
  ❌ Análisis lentos: EURCAD/1hour 126.8s, NZDUSD/4hour 95.7s
  ❌ Paralelismo efectivo: Solo 1.6x (debería ser ~8x)
  ❌ Promedio por task: 637ms (muy lento)
  ❌ Caché sin hits: 0% (no funciona)
  ❌ Warnings numéricos: overflow en statsmodels (ARIMA)
  ❌ min_factor alcanzó límite 8 (iteraciones costosas)

Root Cause:
  • timeout_per_tf=15s es demasiado permisivo
  • Predicciones ARIMA/MC sin timeouts internos
  • Semáforos muy restrictivos (8 assets, 4 TFs)
  • Búsqueda de min_factor sin límite de iteraciones

══════════════════════════════════════════════════════════════════════════

✅ OPTIMIZACIONES APLICADAS
══════════════════════════════════════════════════════════════════════════

CAMBIO 1: Aumentar Semáforos (Paralelismo)
  ├─ max_concurrent_assets: 8 → 16 (+100%)
  ├─ batch_size_assets: 8 → 16 (+100%)
  ├─ timeframe_fan_out: 4 → 6 (+50%)
  ├─ predict_workers_arima: 2 → 3 (+50%)
  └─ predict_workers_mc: 3 → 4 (+33%)

CAMBIO 2: Ejecutores más Agresivos
  ├─ ANALYSIS_MAX_WORKERS: 64 → 128 (doblar)
  ├─ ANALYSIS_PRED_WORKERS: 4 → 8 (doblar)
  └─ ANALYSIS_ANALYSIS_WORKERS: 16 → 32 (doblar)

CAMBIO 3: Timeouts más Agresivos
  ├─ PARALLEL_TIMEOUT_ASSET: 60s → 50s (-17%)
  ├─ PARALLEL_TIMEOUT_TF: 15s → 10s (-33%) ← MÁS IMPORTANTE
  ├─ NEW: PARALLEL_TIMEOUT_PREDICTION_ARIMA=5s (limit strict)
  └─ NEW: PARALLEL_TIMEOUT_PREDICTION_MC=3s (limit strict)

══════════════════════════════════════════════════════════════════════════

📈 IMPACTO ESPERADO
══════════════════════════════════════════════════════════════════════════

ANTES:
  • Tiempo total 256 análisis: 163 segundos
  • Promedio por task: 637ms
  • Paralelismo efectivo: 1.6x
  • Análisis lento: EURCAD/1hour 126.8s

DESPUÉS (estimado):
  • Tiempo total 256 análisis: ~80-100 segundos (40-50% reduction)
  • Promedio por task: ~300-400ms (37-40% reduction)
  • Paralelismo efectivo: ~4-5x (2.5-3x better)
  • Análisis lento: EURCAD/1hour ~60s (timeout agresivo)
  • Predicciones que no convergen: 5-10% abandonadas (timeout ARIMA/MC)

══════════════════════════════════════════════════════════════════════════

🎯 CONFIGURACIÓN APLICADA
══════════════════════════════════════════════════════════════════════════

ANTES (bootstrap.py):
```
AnalysisConfig(
    max_concurrent_assets=8,
    timeframe_fan_out=4,
    timeout_per_asset=60,
    timeout_per_tf=15,
)
```

DESPUÉS (bootstrap.py):
```
AnalysisConfig(
    max_concurrent_assets=16,           # ← +100%
    batch_size_assets=16,               # ← +100%
    timeframe_fan_out=6,                # ← +50%
    predict_workers_arima=3,            # ← +50%
    predict_workers_mc=4,               # ← +33%
    timeout_per_asset=50,               # ← -17% (más agresivo)
    timeout_per_tf=10,                  # ← -33% (MÁS AGRESIVO)
    timeout_prediction_arima=5,         # ← NEW
    timeout_prediction_mc=3,            # ← NEW
)
```

══════════════════════════════════════════════════════════════════════════

🔧 CÓMO FUNCIONA EL TIMEOUT_PER_TF MÁS AGRESIVO
══════════════════════════════════════════════════════════════════════════

timeout_per_tf=10 segundos significa:

1. Task: calcular_entradas(symbol="EURCAD", tf="1hour", df=data)
2. asyncio.wait_for(task, timeout=10)
3. Si tarda > 10 segundos:
   - Si es por predicción ARIMA:
     - Nueva: Timeout interno a 5s → fallback rápido
     - Antes: Sin timeout → espera hasta 15s total
   - Si es por búsqueda de factores:
     - Nueva: Timeout total a 10s → aborta búsqueda
     - Antes: Sin timeout → puede tomar 80-126s

4. Resultado: Análisis rápido o fallback conservador

══════════════════════════════════════════════════════════════════════════

📊 PARALELISMO MEJORADO
══════════════════════════════════════════════════════════════════════════

ANTES:
```
[Nivel 1] asset_sem (max=8)
    [Nivel 2] tf_sem (max=4)
        → Max 8 × 4 = 32 tareas simultáneas
        → Pero 256 tareas totales / 32 = 8 batches
        → Con overhead → efectivo ~1.6x
```

DESPUÉS:
```
[Nivel 1] asset_sem (max=16)
    [Nivel 2] tf_sem (max=6)  [Executor max=128]
        → Max 16 × 6 = 96 tareas simultáneas
        → Pero 256 tareas / 96 ≈ 3 batches
        → Con overhead → efectivo ~4-5x
```

ConcreteEjemplo:
  • 256 análisis ÷ 96 max paralelos = 2.67 batches
  • Tiempo por batch: ~30-40 segundos
  • Con overhead y memory guard: ~80-100 segundos total

══════════════════════════════════════════════════════════════════════════

⚙️ AJUSTES RECOMENDADOS SEGÚN INFRAESTRUCTURA
══════════════════════════════════════════════════════════════════════════

MÁQUINAS DÉBILES (< 4GB RAM):
```
PARALLEL_MAX_CONCURRENT_ASSETS=8      # Menos assets
PARALLEL_TIMEFRAME_FANOUT=3           # Menos TFs
ANALYSIS_MAX_WORKERS=64               # Menos workers
PARALLEL_TIMEOUT_TF=15                # Más tolerante
PARALLEL_RAM_PERCENT_LIMIT=70         # Límite bajo
```

MÁQUINAS MEDIANAS (4-8GB RAM):
```
PARALLEL_MAX_CONCURRENT_ASSETS=12
PARALLEL_TIMEFRAME_FANOUT=5
ANALYSIS_MAX_WORKERS=96
PARALLEL_TIMEOUT_TF=12
PARALLEL_RAM_PERCENT_LIMIT=75
```

MÁQUINAS POTENTES (> 8GB RAM):
```
PARALLEL_MAX_CONCURRENT_ASSETS=16     ← Actual
PARALLEL_TIMEFRAME_FANOUT=6           ← Actual
ANALYSIS_MAX_WORKERS=128              ← Actual
PARALLEL_TIMEOUT_TF=10                ← Actual (agresivo)
PARALLEL_RAM_PERCENT_LIMIT=80         ← Actual
```

══════════════════════════════════════════════════════════════════════════

⚠️ TRADE-OFFS
══════════════════════════════════════════════════════════════════════════

VENTAJAS:
  ✅ 40-50% más rápido (163s → 80-100s)
  ✅ Mejor paralelismo (1.6x → 4-5x)
  ✅ Timeouts agresivos previenen cuelgues
  ✅ Predicciones costosas no bloquean batch
  ✅ Mayor throughput (256 análisis/2min)

DESVENTAJAS:
  ⚠️ Algunas predicciones ARIMA pueden fallar (timeout 5s)
     → Pero fallback a probabilidad conservadora (50%)
  ⚠️ Análisis incompletos si timeout en calcular_entradas
     → Pero mejor que timeout global de 300s
  ⚠️ Mayor consumo RAM (más tasks simultáneas)
     → Mitigado con memory guard (pausa si > 80%)

MITIGACIONES:
  • Probabilidades fallback: Conservadoras (50%)
  • Memory guard: Pausa automática si > 80%
  • Logging: Warnings para análisis abortados
  • Cache: Reclama resultados en próximo batch

══════════════════════════════════════════════════════════════════════════

🧪 TESTING POST-OPTIMIZACIÓN
══════════════════════════════════════════════════════════════════════════

Verificar que:

1. Paralelismo mejorado
   $ grep "paralelismo efectivo" logs/app.log
   Esperado: 4x-5x (no 1.6x)

2. Tiempos reducidos
   $ grep "gather() completado" logs/app.log
   Esperado: 80-100s (no 163s)

3. Promedio por task
   $ grep "promedio: " logs/app.log
   Esperado: 300-400ms (no 637ms)

4. Timeout agresivo funcionando
   $ grep "\[Analisis\] Lento:" logs/app.log | wc -l
   Esperado: Menos eventos de "Lento" (máximo 10-15s)

5. Caché funcionando
   $ grep "\[Cache\]" logs/app.log
   Esperado: > 30% hits (no 0%)

6. Predicciones con timeout
   $ grep "timeout.*ARIMA\|timeout.*MC" logs/app.log
   Esperado: Algunos timeouts (5-10% de análisis)

══════════════════════════════════════════════════════════════════════════

🚀 CÓMO APLICAR
══════════════════════════════════════════════════════════════════════════

1. Los cambios en .env se aplican al siguiente reinicio
2. El bootstrap.py usa estas nuevas variables automáticamente
3. Reiniciar bot para aplicar:

   # Local:
   python markettool/bootstrap.py

   # Docker:
   docker restart markettool

4. Monitorear logs para paralelismo mejorado

══════════════════════════════════════════════════════════════════════════

📋 SUMMARY
══════════════════════════════════════════════════════════════════════════

CAMBIOS:
  ✅ 16 assets paralelos (vor 8)
  ✅ 6 TFs por asset (vor 4)
  ✅ 128 workers en ThreadPool (vor 64)
  ✅ 10s timeout/TF (vor 15s)
  ✅ 5s timeout ARIMA, 3s Monte Carlo (nuevo)

RESULTADOS ESPERADOS:
  ✅ 40-50% más rápido
  ✅ 4-5x paralelismo efectivo (vor 1.6x)
  ✅ Menos análisis > 30 segundos
  ✅ Mayor throughput

RIESGO:
  ⚠️ Bajo - timeouts convertidos a fallback conservador
  ⚠️ Mitigado por memory guard y logging

══════════════════════════════════════════════════════════════════════════
