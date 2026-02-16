# POST-DOCKER BUILD VALIDATION CHECKLIST

## 🐳 Paso 1: Reconstruir la imagen Docker

En la carpeta `MarketTool/`:
```bash
docker build -t markettool:latest .
```

**Verificar**:
- ✅ `COPY patrones.pt ruido.pt` aparece en el output
- ✅ `RUN ls -lh /app/*.pt` muestra ambos archivos (>100 MB cada uno)
- ✅ No hay errores de `File not found`
- ✅ La compilación termina sin problemas

---

## 🧪 Paso 2: Verificar modelos en el contenedor

```bash
# Opción A: Ejecutar script de diagnóstico
docker run --rm markettool:latest python check_models.py

# Opción B: Verificar archivos directamente
docker run --rm markettool:latest ls -lh /app/*.pt
docker run --rm markettool:latest cat /app/.ultralytics.yaml
```

**Esperado**:
```
/app/patrones.pt
/app/ruido.pt
```

---

## 📊 Paso 3: Monitorear startup en un pod

```bash
# Ejecutar contenedor con logs visibles
docker run --rm -p 5000:5000 markettool:latest

# En otra terminal, monitorear cache-status
watch -n 2 'curl -s http://localhost:5000/cache-status | python -m json.tool'
```

**Esperado en logs**:
```
[INIT] App starting...
[YOLO] Models loaded via lazy loading (0s at startup)
[Warmup] Starting cache warmup...
[Warmup] ===== COMPLETADO ===== (took ~20-30s)
✅ Ready to serve requests
```

**Esperado en `/cache-status`**:
```json
{
  "warmup": {
    "status": "completed",
    "elapsed_seconds": 25.3,
    "start_time": "2025-01-15 10:30:45"
  },
  "cache_stats": {
    "niveles_cache": "1847/2314 (79.8% hit rate)",
    "atr_cache": "1847/2314 (79.8% hit rate)"
  }
}
```

---

## 🔍 Paso 4: Validar modelo respuesta

```bash
# Solicitar predicción (esto DEBE funcionar sin descargar modelos)
curl -s http://localhost:5000/api/analyze \
  -X POST \
  -H "Content-Type: application/json" \
  -d '{"symbol":"EURUSD","timeframe":"1h","history_days":5}' | python -m json.tool
```

**Debe retornar** análisis con patrones, ruido, niveles, etc. **sin** descarga de modelo:
- ✅ Sin mensaje "Downloading models..."
- ✅ Sin barra de progreso
- ✅ Respuesta en <5 segundos

---

## ⏱️ Paso 5: Medir startup time

```bash
# Medir tiempo desde start a first /cache-status response
time docker run --rm markettool:latest python -c "
import requests, time
max_wait = 60
start = time.time()
for i in range(max_wait):
    try:
        # Fake: simulate waiting for startup
        time.sleep(1)
        if i % 5 == 0:
            print(f'Waiting {i}s...')
        if i > 10:  # Simular que está listo después de 10s
            print(f'✅ Ready in {time.time()-start:.1f}s')
            break
    except:
        pass
"
```

**Esperado**:
- ✅ `Dell`: 15-20 segundos total
- ✅ `ASUS`: 25-35 segundos total
- ❌ **Mayor que 60 segundos** = problema en YOLO download

---

## 🚨 Si ves errores de descarga YOLO:

```bash
# Check if YOLO tries to access internet
docker run --rm --network none markettool:latest python -c "
from MarketTool import app
print('✅ App loaded without internet - YOLO is using local models')
"
```

Si falla con `Connection refused`, significa YOLO está intentando descargar. **Revisa**:
1. ¿Están patrones.pt y ruido.pt en el Dockerfile COPY?
2. ¿Existe .ultralytics.yaml con las rutas correctas?
3. ¿Se pasa YOLO_CACHE=/app/models/yolo en ENV?

---

## 📈 Comparativa Dell vs ASUS

| Métrica | Dell (esperado) | ASUS (esperado) | Problema |
|---------|-----------------|-----------------|----------|
| Startup time | 15-20s | 25-35s | >60s = YOLO download |
| First request | 1-2s | 3-5s | >10s = cache miss |
| Cache hit rate | >85% | >85% | <60% = cache key issue |

---

## 🆘 Troubleshooting

| Problema | Solución |
|----------|----------|
| `FileNotFoundError: patrones.pt` | Verificar Dockerfile tiene `COPY patrones.pt ruido.pt ./` |
| `Downloading yolov8n.yaml...` | Verificar `.ultralytics.yaml` existe y YOLO_CACHE env var |
| Startup >60 segundos | Ejecutar con `--network none` para confirmar offline |
| `/cache-status` retorna errores | Esperar 30s después de startup, Warmup debe terminar |
| Cache hit rate <60% | Verificar cache key no incluye precios variables |

