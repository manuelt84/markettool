# ⏱️ Sistemas de Timeout en MarketTool

## 📋 Visión General

MarketTool tiene **DOS sistemas de predicción ARIMA** con **DOS conjuntos de timeouts** diferentes:

| Sistema | Archivo Principal | Config Timeout | Status | Usado |
|---------|------------------|-----------------|--------|-------|
| **Legacy** | `MarketTool.py` | `ARIMA_TIMEOUT` (45s) | ✅ Activo | ✅ SÍ |
| **New (ParallelAnalysisEngine)** | `markettool/bootstrap.py` | `PARALLEL_TIMEOUT_PREDICTION_ARIMA` (7s) | ✅ Activo | ⚠️ Creado pero no invocado |

---

## 🏛️ Sistema 1: Legacy (MarketTool.py)

### Ubicación
- **Archivo**: `MarketTool.py` líneas 12564-12602
- **Función**: `calcular_entradas()`
- **Ejecutor**: ThreadPoolExecutor

### Configuración
```env
# Variable en .env
ARIMA_TIMEOUT=45  # segundos

# Lectura en MarketTool.py
ARIMA_TIMEOUT_SECONDS = int(os.environ.get('ARIMA_TIMEOUT', '45'))
```

### Modo ARIMA (3 opciones)
```env
ARIMA_MODE=standard  # Selecciona cuántos barras usar
# Options: 'standard' (480-1200 barras)
#          'aggressive' (1000-5000 barras)  
#          'unlimited' (2000-10000+ barras)
```

### Flujo
```python
# MarketTool.py línea 12573
try:
    predicciones_arima = future_arima.result(timeout=ARIMA_TIMEOUT_SECONDS)
except Exception as e:
    # Fallback a Media Móvil simple
    predicciones_arima = predecir_media_movil(df, window)
```

### Características
- ✅ **Fallback automático**: Si timeout → Media Móvil
- ✅ **Cache por last_close**: Evita duplicados
- ✅ **Modo configurable**: standard/aggressive/unlimited
- ✅ **45 segundos es seguro**: Nunca timeout con ARIMA_MODE=standard

### Cuándo se activa
```
MarketTool.py → procesar_simbolo_temporalidad()
  → calcular_entradas()
    → predecir_arima()  [ TIMEOUT = ARIMA_TIMEOUT_SECONDS ]
    → predecir_media_movil()  [ Fallback ]
    → simulacion_monte_carlo()  [ Timeout = PARALLEL_TIMEOUT_TF ]
```

---

## 🚀 Sistema 2: ParallelAnalysisEngine (Nuevo)

### Ubicación  
- **Archivo**: `markettool/bootstrap.py` líneas 284-295
- **Clase**: `ParallelAnalysisEngine` en `markettool/application/use_cases/parallel_analysis.py`
- **Ejecutor**: ProcessPoolExecutor

### Configuración
```env
# Variables en .env
PARALLEL_TIMEOUT_PREDICTION_ARIMA=7  # segundos para ARIMA individual
PARALLEL_TIMEOUT_PREDICTION_MC=3     # segundos para Monte Carlo individual

# Lectura en bootstrap.py
timeout_prediction_arima=int(os.environ.get("PARALLEL_TIMEOUT_PREDICTION_ARIMA", "7"))
timeout_prediction_mc=int(os.environ.get("PARALLEL_TIMEOUT_PREDICTION_MC", "3"))

# Almacenadas en AnalysisConfig
analysis_config = AnalysisConfig(
    ...
    timeout_prediction_arima=7,
    timeout_prediction_mc=3,
)
```

### Timeouts en ParallelAnalysisEngine
```python
# Jerarquía de timeouts
PARALLEL_GLOBAL_TIMEOUT=300                 # 5 min total
PARALLEL_TIMEOUT_BATCH=120                  # 2 min por batch
PARALLEL_TIMEOUT_ASSET=50                   # 50s por activo
PARALLEL_TIMEOUT_TF=10                      # 10s por timeframe
PARALLEL_TIMEOUT_PREDICTION_ARIMA=7         # ← Para predicción ARIMA individual
PARALLEL_TIMEOUT_PREDICTION_MC=3            # ← Para Monte Carlo individual
```

### Control de Concurrencia
```python
# Semáforos para limitar predicciones paralelas
self.predict_sem_arima = asyncio.Semaphore(3)   # 3 workers ARIMA
self.predict_sem_mc = asyncio.Semaphore(4)      # 4 workers Monte Carlo
```

### Flujo en _analyze_timeframe_signals()
```python
# Línea 348 - Función _predict_movements()
async def _predict_movements():
    predictions = {}
    
    # ARIMA con semáforo (limita concurrencia)
    async with self.predict_sem_arima:
        arima_pred = await loop.run_in_executor(
            self.analysis_executor,
            self._predict_arima,
            df, tf, symbol
        )
    
    # Monte Carlo con semáforo
    async with self.predict_sem_mc:
        mc_scenarios = await loop.run_in_executor(
            self.prediction_executor,
            self._generate_monte_carlo,
            df, symbol, tf
        )
    
    return predictions

# Línea 404 - Todo bajo timeout_per_tf
await asyncio.wait_for(
    asyncio.gather(_get_indicators(), _detect_patterns(), _predict_movements()),
    timeout=self.config.timeout_per_tf  # 10 segundos
)
```

### Características
- ✅ **Semáforos**: Limitan paralelismo por tipo de predicción
- ⚠️ **No usa timeout individual**: Todas las predicciones bajo `timeout_per_tf` (10s)
- ✅ **Múltiples activos paralelos**: 18 activos simultáneos
- ✅ **Manejo de excepciones**: ExceptionHandler en asyncio.gather
- ℹ️ **Variables definidas pero no usadas**: `timeout_prediction_arima` y `timeout_prediction_mc` se leen pero NO se aplican

### Cuándo se activa
```
bootstrap.py → ParallelAnalysisEngine.analyze_symbols()
  → Batch de 16 activos
    → _analyze_asset_timeframes()
      → _analyze_timeframe_signals()
        → _predict_movements()
          → predecir_arima() [ Timeout = PARALLEL_TIMEOUT_TF = 10s, no 7s ]
          → simulacion_monte_carlo() [ Timeout = PARALLEL_TIMEOUT_TF = 10s, no 3s ]
```

---

## 🔴 PROBLEMA ACTUAL

Las variables `PARALLEL_TIMEOUT_PREDICTION_ARIMA` y `PARALLEL_TIMEOUT_PREDICTION_MC` **se leen en bootstrap.py PERO NO se usan** en ParallelAnalysisEngine.

**¿Por qué?**
```python
# markettool/application/use_cases/parallel_analysis.py línea 404
timeout=self.config.timeout_per_tf  # Usa ESTO (10 segundos)

# NO usa esto:
# timeout=self.config.timeout_prediction_arima  # (7 segundos)
# timeout=self.config.timeout_prediction_mc     # (3 segundos)
```

**Impacto:**
- ✅ Semáforos limitan concurrencia (3 workers ARIMA, 4 MC)
- ❌ No hay timeouts individuales por tipo de predicción
- ✅ Todo está bajo `timeout_per_tf=10s` (que es seguro)

---

## 💡 Cuándo Usar Cada Sistema

### Usar ARIMA_TIMEOUT (Legacy)
- ✅ Ejecutando solo `MarketTool.py` (standalone)
- ✅ Procesamiento secuencial de activos
- ✅ No necesitas máximo paralelismo
- ✅ Versión producción GKE

### Usar PARALLEL_TIMEOUT_PREDICTION_* (ParallelAnalysisEngine)  
- ✅ Máximo paralelismo (18+ activos simultáneos)
- ✅ Análisis batch de muchos símbolos
- ✅ Quieres fallar rápido en predicciones lentas
- ⚠️ Actualmente NO se implementa (en roadmap)

---

## 📊 Comparativa de Timeouts

| Métrica | MarketTool.py | ParallelAnalysisEngine |
|---------|---------------|------------------------|
| **Timeout ARIMA** | 45 segundos | 10 segundos (por TF) |
| **Fallback** | Media Móvil | None (falla el TF) |
| **Paralelismo** | Activos secuenciales | 18 activos paralelos |
| **Barras ARIMA** | 480-5000 (configurable) | Asume suficientes |
| **Status** | ✅ Producción | 🚀 Experimental |
| **Control ARIMA** | 3 modos | None (fijo) |
| **Predicción MC** | SimpleMA fallback | 10s timeout |

---

## 🎯 Recomendaciones

### Para Producción GKE Actual
```env
# Usa el sistema legacy (MarketTool.py)
ARIMA_MODE=standard          # 480-1200 barras según TF
ARIMA_TIMEOUT=45             # Suficiente con fallback
PARALLEL_TIMEOUT_TF=10       # Fallback seguro para ParallelAnalysisEngine
```

### Si Quieres Máximo Paralelismo (Futuro)
```env
# Habilitar ParallelAnalysisEngine con:
PARALLEL_TIMEOUT_PREDICTION_ARIMA=7   # Para Nivel 3
PARALLEL_TIMEOUT_PREDICTION_MC=3      # Para Nivel 3
PARALLEL_MAX_CONCURRENT_ASSETS=18     # Máximo permitido
```

### Si Necesitas Debugging
```env
# Reducir paralelismo
PARALLEL_MAX_CONCURRENT_ASSETS=4      # Solo 4 activos
PARALLEL_TIMEFRAME_FANOUT=3           # Solo 3 TF simultáneos
PARALLEL_TIMEOUT_TF=30                # Timeout más largo para debug
LOG_LEVEL=DEBUG                       # Ver qué ocurre
```

---

## 🔧 Cómo Cambiar Timeouts

### Opción 1: Via .env (recomendado)
```bash
# Editar .env
ARIMA_TIMEOUT=60                         # Sistema legacy
PARALLEL_TIMEOUT_PREDICTION_ARIMA=10    # Sistema nuevo
PARALLEL_TIMEOUT_PREDICTION_MC=5        # Sistema nuevo

# Restart MarketTool
docker-compose down
docker-compose up
```

### Opción 2: Via ConfigMap en K8s
```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: markettool-env
data:
  ARIMA_TIMEOUT: "60"
  PARALLEL_TIMEOUT_PREDICTION_ARIMA: "10"
  PARALLEL_TIMEOUT_PREDICTION_MC: "5"
```

---

## 📝 Variables Completas de Timeout

```env
# ===== LEGACY SYSTEM (MarketTool.py) =====
ARIMA_MODE=standard                         # Modo de barras
ARIMA_TIMEOUT=45                            # Timeout ARIMA principal

# ===== PARALLEL SYSTEM (ParallelAnalysisEngine) =====
PARALLEL_GLOBAL_TIMEOUT=300                 # Total
PARALLEL_TIMEOUT_BATCH=120                  # Por batch
PARALLEL_TIMEOUT_ASSET=50                   # Por activo
PARALLEL_TIMEOUT_TF=10                      # Por timeframe (ACTUALMENTE EN USAR)
PARALLEL_TIMEOUT_PREDICTION_ARIMA=7         # Individual ARIMA (no usado)
PARALLEL_TIMEOUT_PREDICTION_MC=3            # Individual MC (no usado)
```

---

## 📈 Performance Impact

### Si aumentas PARALLEL_TIMEOUT_PREDICTION_ARIMA: 7→15
- ✅ Más tiempo para ARIMA complejo
- ❌ Análisis más lento
- ❌ Resultado llega más tarde

### Si reduces PARALLEL_TIMEOUT_TF: 10→5
- ✅ Fallar rápido si algo se traba
- ❌ Más timeouts
- ❌ Menos señales válidas

### Si aumentas PARALLEL_MAX_CONCURRENT_ASSETS: 18→32
- ✅ 32 activos en paralelo
- ❌ Más RAM usado
- ❌ CPU al 100%

---

## 🐛 Troubleshooting

### "ARIMA prediction timed out"
```
Causa: ARIMA_TIMEOUT demasiado bajo o dataset grande
Solución:
  1. Aumentar ARIMA_TIMEOUT=60
  2. Reducir ARIMA_MODE=aggressive
  3. Aumentar ARIMA_TIMEOUT=90 si nada funciona
```

### "ParallelAnalysisEngine timeout per TF"
```
Causa: 10 segundos no alcanza para todos los indicadores
Solución:
  1. Aumentar PARALLEL_TIMEOUT_TF=15
  2. Reducir PARALLEL_MAX_CONCURRENT_ASSETS=8
  3. Reducir PARALLEL_TIMEFRAME_FANOUT=4
```

### "90% RAM used, pausing analysis"
```
Causa: Demasiados activos paralelos
Solución:
  1. Reducir PARALLEL_MAX_CONCURRENT_ASSETS=10
  2. Aumentar PARALLEL_RAM_PERCENT_LIMIT=85 (cuidado)
  3. Agregar más RAM al servidor
```

---

## ✅ Checklist de Configuración

- [ ] ¿Entiendes la diferencia entre los dos sistemas?
- [ ] ¿Cuál usas en producción? (probablemente MarketTool.py legacy)
- [ ] ¿Has testeado con ARIMA_MODE=aggressive?
- [ ] ¿Monitorizas logs de "timeout"?
- [ ] ¿Sabes cuál es tu PARALLEL_MAX_CONCURRENT_ASSETS?
- [ ] ¿Has documentado tus cambios de timeout?

---

**Last Updated:** 2026-02-18  
**Status:** ✅ Documentado  
**Sistema Activo:** MarketTool.py (Legacy) + ParallelAnalysisEngine (Nuevo pero no invocado)  
