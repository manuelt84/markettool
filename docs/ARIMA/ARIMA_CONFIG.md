# 📊 ARIMA Configuration Guide

## Descripción General

El sistema ARIMA ahora tiene **3 modos configurables** con **fallback automático** a predicción simple si ocurre timeout.

**Cambios implementados:**
- ✅ Timeout aumentado a **45 segundos** (antes 30s)
- ✅ Fallback a predicción simple (media móvil) si ARIMA falla
- ✅ 3 modos de configuración por temporalidad

---

## 🎯 Los 3 Modos

### **1. STANDARD (DEFAULT) ⭐**
```
ARIMA_MODE = 'standard'
ARIMA_TIMEOUT = 45s
```

**Características:**
- Balance óptimo entre historia y velocidad
- NGUSD-30min: 1200 barras = 3.6 semanas = ~5-10s de ARIMA
- **Nunca llega al timeout** (margen de ~35s)
- Activos bien ponderados
- **Recomendado para producción**

**Valores por TF:**
```
1min:    480 barras (~8 horas)
5min:  1000 barras (~5 días)
15min: 1200 barras (~15 días)
30min: 1200 barras (~3.6 semanas) ⭐ NGUSD aquí
1hour: 1000 barras (~6 semanas)
4hour:  800 barras (~3.2 meses)
1day:   500 barras (~2 años)
1week:  300 barras (~6 años)
```

---

### **2. AGGRESSIVE**
```
ARIMA_MODE = 'aggressive'
ARIMA_TIMEOUT = 45s
```

**Características:**
- Más historia = detecta tendencias largas
- NGUSD-30min: 5000 barras = 15 semanas (~3.5 meses)
- Tarda ~20-30s en ARIMA (seguro dentro del timeout)
- Saca **activos distintos** en ranking → más ponderados los de tendencia larga
- Puede perder precisión de corto plazo

**Cuándo usar:**
- Comparación con modo standard
- Análisis de tendencias largas
- Cuando quieres ver otros activos en ranking

---

### **3. UNLIMITED**
```
ARIMA_MODE = 'unlimited'
ARIMA_TIMEOUT = 45s
```

**Características:**
- SIN LÍMITE en barras históricas
- NGUSD-30min: 10000+ barras = 30+ semanas
- ⚠️ **MUY LENTO** → Puede alcanzar timeout
- Si falla ARIMA → Fallback automático a media móvil
- **Backtesting/análisis offline**

---

## 🔧 Cómo Cambiar la Configuración

### **Opción 1: Variable de Entorno (RECOMENDADO)**

```bash
# En docker-compose o deploy
export ARIMA_MODE='standard'      # o 'aggressive' o 'unlimited'
export ARIMA_TIMEOUT='45'         # segundos
```

**Dockerfile:**
```dockerfile
ENV ARIMA_MODE=standard
ENV ARIMA_TIMEOUT=45
```

**Docker Compose:**
```yaml
environment:
  - ARIMA_MODE=standard
  - ARIMA_TIMEOUT=45
```

### **Opción 2: Código (Editar directamente)**

En `MarketTool.py` línea ~307:
```python
ARIMA_ACTIVE_MODE = 'standard'  # Cambiar aquí
ARIMA_TIMEOUT_SECONDS = 45      # Cambiar aquí
```

---

## 📊 Comportamiento del Sistema

### **Flujo Normal (ARIMA exitosa)**
```
1. Recolectar datos (max_barras según modo)
2. Ejecutar ARIMA en paralelo (timeout=45s)
3. Retornar predicción ARIMA
```

### **Fallback (ARIMA falló o timeout)**
```
1. ARIMA timeout después de 45s OR error
2. Ejecutar predicción simple (media móvil)
3. Log: "Error en predicciones paralelas... Fallback a predicción simple"
4. Retornar predicción simple
5. Sistema continúa sin fallar
```

### **Timeout Details**
- ARIMA: 45s
- Media Móvil: 30s
- Monte Carlo: 30s
- Si uno falla → su fallback entra automáticamente

---

## 📈 Comparativa de Modos

| Aspecto | Standard | Aggressive | Unlimited |
|---------|----------|-----------|-----------|
| **NGUSD-30min barras** | 1200 | 5000 | 10000+ |
| **Período (30min)** | 3.6 sem | 15 sem | 30+ sem |
| **Tiempo ARIMA** | 5-10s | 20-30s | 30-45s+ |
| **Timeout llega?** | Nunca | Raramente | A veces |
| **Fallback usado?** | Casi nunca | Raro | Ocasional |
| **Activos distintos?** | No | Sí (tendencias) | Muy sí |
| **Precisión corto** | Excelente | Buena | Regular |
| **Precisión largo** | Buena | Excelente | Máxima |
| **Producción?** | ✅ Sí | ⚠️ Test | ❌ No |

---

## 🧪 Testing

### **Cambiar a Aggressive (comparación)**
```bash
export ARIMA_MODE=aggressive
docker-compose up -d
# Ver logs: Activos diferentes aparecen en ranking
# Validar: ¿Mejor o peor que standard?
```

### **Monitorear Fallbacks**
```bash
docker logs -f app1 | grep "Fallback a predicción simple"
# Si ves muchos → aumenta ARIMA_TIMEOUT a 60
```

### **Ver Configuración Activa al Startup**
```bash
docker logs app1 | grep "ARIMA configurado"
# Output: ✅ ARIMA configurado: modo=standard, timeout=45s, fallback=SimpleMA
```

---

## ⚠️ Troubleshooting

### Problema: "Aún hay timeouts con modo standard"
**Solución:**
```bash
# Aumentar timeout (últimos 5s de margen)
export ARIMA_TIMEOUT=50
```

### Problema: "Quiero activos con tendencias largas (aggressive)"
**Solución:**
```bash
# Cambiar a aggressive Y monitorear logs
export ARIMA_MODE=aggressive
# Ver si hay más fallbacks → tune TIMEOUT
```

### Problema: "Fallback tira predicción muy simple"
**Comportamiento esperado:**
- Fallback = Media móvil simple (no ARIMA)
- Es rápido, seguro, no falla
- Mejor que nada si ARIMA explota

---

## 📌 Recomendación Final

**Para producción: mantener `standard` con `timeout=45`**

- Rápido (<10s)
- Confiable (sin fallbacks)
- Activos bien ponderados
- Sin overhead

**Cambiar a `aggressive` solo si:**
- Quieres comparar activos de tendencias largas
- Tienes 45+ segundos disponibles por ciclo
- Monitorizas logs de fallbacks
