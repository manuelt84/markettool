╔════════════════════════════════════════════════════════════════════════╗
║                     📋 PRÓXIMOS PASOS - DEPLOYMENT                     ║
║                                                                        ║
║  Instrucciones paso a paso para desplegar y verificar las mejoras     ║
╚════════════════════════════════════════════════════════════════════════╝

═══════════════════════════════════════════════════════════════════════════
PASO 1: VERIFICAR FILES ACTUALIZADOS
═══════════════════════════════════════════════════════════════════════════

Antes de hacer restart, verificar que todos los cambios estén en place:

✅ Verificar parallel_analysis.py tiene valores nuevos:
   Abrir: markettool/application/use_cases/parallel_analysis.py
   Buscar línea ~35: max_concurrent_assets: int = 16
   Verificar: timeout_per_tf: int = 10
   Verificar: ADDED timeout_prediction_arima: int = 5
   Verificar: ADDED timeout_prediction_mc: int = 3

✅ Verificar bootstrap.py tiene los nuevos defaults:
   Abrir: markettool/bootstrap.py
   Buscar línea ~120: "indicators=128, pred=8, analysis=32"
   Verificar: max_concurrent_assets=16, timeframe_fan_out=6

✅ Verificar .env tiene 13 parallelism variables:
   Abrir: .env (scroll al final)
   Buscar: ANALYSIS_MAX_WORKERS=128
   Buscar: PARALLEL_TIMEOUT_TF=10
   Buscar: PARALLEL_TIMEOUT_PREDICTION_ARIMA=5 (NUEVO)
   Buscar: PARALLEL_TIMEOUT_PREDICTION_MC=3 (NUEVO)

✅ Verificar MarketTool.py tiene fixes de zone functions:
   Abrir: MarketTool.py
   Buscar línea 9384: def verificar_zona_no_trading
   Verificar: Contiene _coerce_float() y guard clause
   Buscar línea 9435: def verificar_zona_sobreventa
   Verificar: Contiene _coerce_float() y guard clause

═══════════════════════════════════════════════════════════════════════════
PASO 2: BACKUP ACTUAL
═══════════════════════════════════════════════════════════════════════════

Crear backup por si necesita rollback:

$ mkdir -p backup/{$DATE}
$ cp .env backup/{$DATE}/.env_backup
$ cp markettool/bootstrap.py backup/{$DATE}/bootstrap_backup.py
$ cp markettool/application/use_cases/parallel_analysis.py backup/{$DATE}/parallel_analysis_backup.py

Guardar en lugar seguro, especialmente .env

═══════════════════════════════════════════════════════════════════════════
PASO 3: RESTART BOT
═══════════════════════════════════════════════════════════════════════════

Opción A - Sistema local:
  $ python markettool/bootstrap.py

Opción B - Docker container:
  $ docker-compose down
  $ docker-compose up -d marketTool
  $ docker-compose logs -f marketTool

Esperado en logs (primeros 30 segundos):
  ✅ "Successfully initialized database"
  ✅ "Scheduler job registered: _parallel_analysis_job"
  ✅ "indicators=128, pred=8, analysis=32" (NEW parallelism)
  ✅ "max_concurrent=16, timeframe_fanout=6" (NEW config)
  ✅ "Engine timeout_per_tf=10s" (NEW timeout)

═══════════════════════════════════════════════════════════════════════════
PASO 4: ESPERAR PRIMERA EJECUCIÓN
═══════════════════════════════════════════════════════════════════════════

El scheduler ejecuta cada 10 minutos (configurable):

1. Abrir otra terminal:
   $ tail -f logs/app.log | grep "gather\|paralelismo\|Lento"

2. Esperar ~10-15 minutos para que se ejecute primer batch

3. Debería ver logs similares a:
   ✅ "[Paralelismo] Iniciando gather de 256 análisis..."
   ✅ "[Paralelismo] gather() completado en 85.3s" (ANTES: 163s)
   ✅ "[Paralelismo] promedio per task: 331ms" (ANTES: 637ms)
   ✅ "[Paralelismo] paralelismo efectivo: 4.7x" (ANTES: 1.6x)
   ✅ "[Paralelismo] Análisis lento (>10s): 2" (ANTES: 9)

═══════════════════════════════════════════════════════════════════════════
PASO 5: VERIFICACIÓN DETALLADA
═══════════════════════════════════════════════════════════════════════════

Opción A - Script automático (RECOMENDADO):
  $ python monitor_performance.py
  
  Muestra:
  ├─ Gather time trend (última 5 ejecuciones)
  ├─ Avg task time histórico
  ├─ Paralelismo efectivo trend
  ├─ Slow analyses count
  └─ Cache hit rate

Opción B - Manual logs:
  // Ver gather time última ejecución
  $ grep "gather() completado" logs/app.log | tail -1
  
  // Ver paralelismo efectivo
  $ grep "paralelismo efectivo" logs/app.log | tail -1
  
  // Ver análisis lento count
  $ grep "\[Paralelismo\] Análisis lento" logs/app.log | tail -5
  
  // Ver si hay timeout errors
  $ grep "timeout\|TimeoutError" logs/app.log | tail -10

Opción C - Buscar error específicos:
  // Verificar que NO hay TypeError de zone functions
  $ grep "verificar_zona_.*TypeError" logs/app.log
  → Debería estar VACÍO ✅
  
  // Verificar que NO hay "None" en comparaciones
  $ grep "not supported between instances" logs/app.log
  → Debería estar VACÍO ✅

═══════════════════════════════════════════════════════════════════════════
PASO 6: INTERPRETAR RESULTADOS
═══════════════════════════════════════════════════════════════════════════

MÉTRICA | ANTES | TARGET | SIGNIFICADO
─────────────────────────────────────────────────────────────────────────
Gather | 163s  | 80-100s | Tiempo para procesar 256 análisis
Promedio | 637ms | 300-400ms | Latencia per-task
Paralelo | 1.6x | 4-5x | Efectividad de concurrencia
Lentos | 9 | <5 | Análisis que exceden 10s
Timeouts | 0 | 0-1 | Errores por timeout (OK si <1%)

✅ ÉXITO (Metrices cumplen targets dentro de margen 10%):
   → Keep current settings
   → Monitor próximas 10 ejecuciones para trend
   → Documentar en logs como referencia

⚠️ PARCIAL (Mejora pero no alcanzó target):
   → Aumentar PARALLEL_TIMEOUT_TF=12 (de 10) si hay timeouts
   → O aumentar PARALLEL_MAX_CONCURRENT_ASSETS=12 (de 16) si hay errors
   → Esperar 5 ejecuciones más

❌ FALLIDO (Sin mejora o peor):
   → Revertir a backup: cp backup/{$DATE}/.env .env
   → Buscar en logs si hay errors específicos
   → Contactar support si hay errores raros

═══════════════════════════════════════════════════════════════════════════
PASO 7: FINE-TUNING (SI ES NECESARIO)
═══════════════════════════════════════════════════════════════════════════

ESCENARIO A: Timeouts frecuentes (>1% de tasks)
  Solución 1 (menos agresivo):
    PARALLEL_TIMEOUT_TF=12          (era 10)
    PARALLEL_TIMEOUT_PREDICTION_ARIMA=7     (era 5)
    PARALLEL_TIMEOUT_PREDICTION_MC=4        (era 3)
  
  Luego: $ python markettool/bootstrap.py
  Esperar 3 ejecuciones

ESCENARIO B: Memory warnings (RAM > 80%)
  Solución 1 (menos concurrencia):
    PARALLEL_MAX_CONCURRENT_ASSETS=12       (era 16)
    PARALLEL_TIMEFRAME_FANOUT=5             (era 6)
  
  Solución 2 (menos workers):
    ANALYSIS_MAX_WORKERS=96                 (era 128)
  
  Luego: $ python markettool/bootstrap.py
  Esperar 1 ejecución

ESCENARIO C: Análisis particular muy lento (ej. EURCAD/1h)
  Root cause: Indica/Predicción particular problemática
  Solución: Aumentar timeout solo para ese par
  
  (En bootstrap.py, línea 130-140 aprox):
    Override: timeout_analysis_config['EURCAD']['1h'] = 15
  
  O en .env:
    SLOW_PAIR_TIMEOUT_EURCAD_1H=15

═══════════════════════════════════════════════════════════════════════════
PASO 8: VALIDACIÓN FINAL
═══════════════════════════════════════════════════════════════════════════

Una vez verificado que funciona, ejecutar:

1. Tests de robustez (opcional pero recomendado):
   $ python test_zone_functions_unit.py
   → Debería ser 4/4 PASS ✅

2. Validar caché está activo (buscar en logs):
   $ grep "cache.*hit\|cache.*miss" logs/app.log | head -10
   → Debería ver cache usage (señal de optimization)

3. Comparar con baseline anterior (data 3-5 días atrás):
   $ python monitor_performance.py --compare-with=5days
   → Debería mostrar 40-50% improvement en gather time
   → Debería mostrar 2.5-3x improvement en parallelismo

═══════════════════════════════════════════════════════════════════════════
PASO 9: SI ALGO SALE MAL
═══════════════════════════════════════════════════════════════════════════

ERROR: TypeError en verificar_zona_*
  Causa: Zone function fix no aplicada
  Solución: 
    1. Verificar MarketTool.py línea 9384
    2. Confirma que tiene _coerce_float()
    3. Si no: aplicar de nuevo el fix
    4. Restart bot

ERROR: TimeoutError en gather()
  Causa: timeout_per_tf muy agresivo (10s muy bajo)
  Solución:
    1. Editar .env: PARALLEL_TIMEOUT_TF=12
    2. Restart bot
    3. Monitor próximas 3 ejecuciones
    4. Si sigue: aumentar a 15

ERROR: OutOfMemory o RAM 100%
  Causa: Demasiada concurrencia
  Solución:
    1. Editar .env: PARALLEL_MAX_CONCURRENT_ASSETS=8
    2. Editar .env: ANALYSIS_MAX_WORKERS=64
    3. Restart bot
    4. Verificar logs por RAM usage

ERROR: Logs vacíos o no hay paralelismo logs
  Causa: Bootstrap.py no se ejecutó con nuevos valores
  Solución:
    1. Kill el bot actual
    2. Verificar .env tiene PARALLEL_TIMEOUT_TF=10
    3. Verificar bootstrap.py línea 120 tiene "128, 8, 32"
    4. $ python markettool/bootstrap.py
    5. Esperar 10min para ejecución

ERROR: Algunos análisis sin completar
  Causa: Timeout muy bajo para pair específico
  Solución:
    1. Identificar pair lento en logs: grep "Lento:"
    2. Aumentar timeout per-asset en bootstrap.py
    3. O aumentar PARALLEL_TIMEOUT_TF a 12-13

═══════════════════════════════════════════════════════════════════════════
QUICK CHECKLIST - DEPLOYMENT
═══════════════════════════════════════════════════════════════════════════

Pre-deployment:
  ☐ Verificar parallel_analysis.py: max=16, timeout_tf=10
  ☐ Verificar bootstrap.py: "128, 8, 32" loggers
  ☐ Verificar .env: PARALLEL_TIMEOUT_TF=10 y otros 12 vars
  ☐ Verificar MarketTool.py: 3 zone functions tienen _coerce_float()
  ☐ Crear backup de .env

Deployment:
  ☐ $ python markettool/bootstrap.py (o docker-compose restart)
  ☐ Esperar logs iniciales: "DB initialized", "Scheduler registered"
  ☐ Verificar: "indicators=128, pred=8, analysis=32"

Post-deployment:
  ☐ Esperar primer batch (~10-15 min)
  ☐ $ python monitor_performance.py (o manual tail -f)
  ☐ Verificar gather: ~85s (target) vs 163s (antes)
  ☐ Verificar parallelismo: ~4.5x (target) vs 1.6x (antes)
  ☐ Verificar lentos: <5 (target) vs 9 (antes)
  ☐ Verificar NO hay TypeError en logs

Validation:
  ☐ Ver trend de 3-5 ejecuciones
  ☐ Si todo cumple: ✅ ÉXITO
  ☐ Si hay issues: Aplicar fine-tuning o revertir

═══════════════════════════════════════════════════════════════════════════

📞 SUPPORT
═══════════════════════════════════════════════════════════════════════════

Si necesita revertir COMPLETAMENTE:
  $ cp backup/{$DATE}/.env .env
  $ cp backup/{$DATE}/bootstrap_backup.py markettool/bootstrap.py
  $ cp backup/{$DATE}/parallel_analysis_backup.py \
    markettool/application/use_cases/parallel_analysis.py
  $ python markettool/bootstrap.py

Documentación completa en:
  - SESSION_FINAL_COMPLETE.md (este documento)
  - OPTIMIZATION_PERFORMANCE.md
  - PERFORMANCE_OPTIMIZATION_FINAL.md
  - QUICK_START_PERFORMANCE.md

═══════════════════════════════════════════════════════════════════════════

✅ LISTO PARA DESPLEGAR
═══════════════════════════════════════════════════════════════════════════
