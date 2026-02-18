# ⚠️  UNIFICACIÓN DE TIMEOUT ARIMA

## Problema Encontrado

El código tiene **2 sistemas de timeout ARIMA en conflicto:**

### Sistema 1: ParallelAnalysisEngine (NO SE USA)
```
PARALLEL_TIMEOUT_PREDICTION_ARIMA=7        # En .env
├─ Ubicado en: markettool/application/use_cases/parallel_analysis.py (línea 43)
├─ Usado por: ParallelAnalysisEngine._analyze_tf_entry_signals()
└─ Estado: ❌ **NO CONECTADO EN PRODUCCIÓN**
```

### Sistema 2: procesar_simbolo_temporalidad (SÍ SE USA) ✅
```
ARIMA_TIMEOUT=45                           # En .env (línea 42)
├─ Ubicado en: MarketTool.py (línea 315)
├─ Variable: ARIMA_TIMEOUT_SECONDS
├─ Usado por: calcular_entradas() → future_arima.result(timeout=ARIMA_TIMEOUT_SECONDS)
└─ Estado: ✅ **EN PRODUCCIÓN REAL**
```

---

## Flujo Real en Producción

```
main()
  ↓ (carga .env)
  ↓
initialize_bot_async()
  ↓
procesar_simbolo_temporalidad() [MarketTool.py línea 13727]
  ├─ Carga ARIMA_TIMEOUT_SECONDS desde .env [MarketTool.py línea 315]
  ├─ Obtiene datos
  ├─ Calcula indicadores
  │
  └─ calcular_entradas() [MarketTool.py línea 12464]
      ├─ Paraleliza: ARIMA con timeout=45s ✅
      ├─ Paraleliza: Media Móvil 
      ├─ Paraleliza: Monte Carlo
      └─ Fallback: Si falla ARIMA → usa Media Móvil simple
```

---

## Solución Implementada

### 1. ✅ Mantenemos Sistema Real (correcto)
```env
# .env
ARIMA_MODE=standard    # 3 modos: standard, aggressive, unlimited
ARIMA_TIMEOUT=45       # ← ESTO ES LO QUE FUNCIONA
```

### 2. ⚠️  Documentado Sistema Viejo (no usado)
```env
# DEPRECATED: ParallelAnalysisEngine (no conectado)
# PARALLEL_TIMEOUT_PREDICTION_ARIMA=7        # ← NO USA ESTO
# PARALLEL_TIMEOUT_PREDICTION_MC=3           # ← NO USA ESTO
```

---

## Por Qué No Hay Conflicto Ahora

| Variable | Quién la Lee | Se Usa | Timeout Real |
|----------|-------------|--------|--------------|
| `PARALLEL_TIMEOUT_PREDICTION_ARIMA` | ParallelAnalysisEngine | ❌ NO | N/A |
| `ARIMA_TIMEOUT` | MarketTool.py/calcular_entradas | ✅ SÍ | **45s** |

**Resultado:** ARIMA nunca tira timeout con 45s, siempre hay fallback a Media Móvil.

---

## 3 Modos Configurables (SOLO ARIMA_MODE)

### Cambiar mediante .env
```bash
# 1. Standard (DEFAULT) - Recomendado producción
ARIMA_MODE=standard
ARIMA_TIMEOUT=45

# 2. Aggressive - Más historia, más lento
ARIMA_MODE=aggressive  
ARIMA_TIMEOUT=45

# 3. Unlimited - Máxima historia
ARIMA_MODE=unlimited
ARIMA_TIMEOUT=45
```

### O mediante Docker
```yaml
environment:
  ARIMA_MODE: standard   # standard, aggressive, unlimited
  ARIMA_TIMEOUT: 45      # segundos
```

---

## Resumen Final

✅ **1 Solo Timeout Real:** `ARIMA_TIMEOUT=45s`  
✅ **3 Modos de Configuración:** standard, aggressive, unlimited  
✅ **1 Fallback Automático:** Si falla ARIMA → Media Móvil simple  
✅ **Documentado:** El sistema viejo está comentado  
✅ **Sin Conflictos:** System Uno (ParallelAnalysisEngine) no se ejecuta
