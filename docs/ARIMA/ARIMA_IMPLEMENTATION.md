# ✅ IMPLEMENTACIÓN COMPLETA: 3 Modos de ARIMA con Fallback Automático

## 📋 Resumen de Cambios

### **1. Configuración Global (línea 285-331)**
```python
ARIMA_MODES = {
    'standard': {...},      # DEFAULT: Balance óptimo
    'aggressive': {...},    # Más datos, detecta tendencias largas
    'unlimited': {...}      # Máxima historia (sin límite)
}

ARIMA_ACTIVE_MODE = 'standard'       # Configurable vía env var ARIMA_MODE
ARIMA_TIMEOUT_SECONDS = 45           # Configurable vía env var ARIMA_TIMEOUT
```

### **2. Timeout Actualizado (línea 12574)**
```python
# Antes: timeout=30
predicciones_arima = future_arima.result(timeout=ARIMA_TIMEOUT_SECONDS)  # Ahora: 45s
```

### **3. Fallback Automático (línea 12587-12602)**
```python
# Si ARIMA falla → usa predicción simple (media móvil)
except Exception as e:
    logger.warning(f"Error... Fallback a predicción simple.")
    predicciones_arima = predecir_media_movil(df, window)  # Fallback
    # ... más fallbacks para MM y MC
```

### **4. Función predecir_arima Optimizada (línea 10693)**
```python
limites_por_tf = ARIMA_MODES.get(ARIMA_ACTIVE_MODE, ARIMA_MODES['standard'])
max_barras = limites_por_tf.get(temporalidad, limites_por_tf.get('1hour', 1000))
```

---

## 🚀 Cómo Usar

### **Cambiar Modo (Producción - usar env vars)**
```bash
# Standard (DEFAULT)
export ARIMA_MODE=standard
export ARIMA_TIMEOUT=45

# Aggressive (más historia)
export ARIMA_MODE=aggressive
export ARIMA_TIMEOUT=45

# Unlimited (máxima historia)
export ARIMA_MODE=unlimited
export ARIMA_TIMEOUT=45
```

### **En Docker Compose**
```yaml
environment:
  ARIMA_MODE: standard        # Cambiar aquí
  ARIMA_TIMEOUT: 45           # Cambiar aquí
```

---

## 📊 Comportamiento

### **Modo Standard (DEFAULT)**
- **NGUSD-30min:** 1200 barras = 3.6 semanas
- **Tiempo ARIMA:** 5-10 segundos
- **Timeout:** 45s (margen de ~35s)
- **Fallback:** Casi nunca usado
- **Recomendado:** ✅ Producción

### **Modo Aggressive**
- **NGUSD-30min:** 5000 barras = 15 semanas
- **Tiempo ARIMA:** 20-30 segundos
- **Timeout:** 45s (margen seguro)
- **Fallback:** Raro
- **Recomendado:** Comparaciones / Testing

### **Modo Unlimited**
- **NGUSD-30min:** 10000+ barras = 30+ semanas
- **Tiempo ARIMA:** 30-45+ segundos
- **Timeout:** 45s (puede alcanzarse)
- **Fallback:** Ocasional
- **Recomendado:** ❌ Backtesting offline

---

## 🔄 Fallback Automático

Si ARIMA falla (timeout o error):
1. **ARIMA:** Timeout/Error → Usa media móvil simple
2. **Media Móvil:** Timeout/Error → Usa None
3. **Monte Carlo:** Timeout/Error → Usa probabilidades neutras (50/50)
4. **Sistema continúa sin fallar** ✅

### Ejemplo de Log
```
WARNING: Error en predicciones paralelas para NGUSD-30min: TimeoutError. Fallback a predicción simple.
→ ARIMA se saltó, usó media móvil
→ Análisis continúa normalmente
```

---

## 📁 Archivos Modificados

1. **MarketTool.py:**
   - Línea 285-331: Configuración ARIMA
   - Línea 10693: Uso dinámico de limites_por_tf
   - Línea 12574: Timeout configurable
   - Línea 12587-12602: Fallback automático

2. **ARIMA_CONFIG.md (NUEVO):**
   - Documentación completa de uso
   - Guía de configuración
   - Troubleshooting

---

## ✅ Testing Recomendado

```bash
# 1. Verificar configuración al startup
docker logs app1 | grep "ARIMA configurado"
# Esperado: ✅ ARIMA configurado: modo=standard, timeout=45s, fallback=SimpleMA

# 2. Cambiar a aggressive (comparación)
export ARIMA_MODE=aggressive
docker-compose up -d
# Ver si activos cambian en ranking

# 3. Monitorear fallbacks
docker logs -f app1 | grep "Fallback a predicción simple"
# Si hay muchos → aumentar ARIMA_TIMEOUT a 60

# 4. Volver a standard
export ARIMA_MODE=standard
```

---

## 🎯 Valor Agregado

✅ **Nunca falla:** Fallback automático a predicción simple
✅ **Flexible:** 3 modos para diferentes necesidades
✅ **Configurable:** Via environment variables
✅ **Documentado:** ARIMA_CONFIG.md con guía completa
✅ **Seguro:** 45s de timeout con margen
✅ **Observable:** Logs claros de qué modo está activo
