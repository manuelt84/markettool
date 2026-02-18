# 🚀 NEXT STEPS - GUÍA DE ACCIÓN INMEDIATA

## ✅ Cambios Completados

- ✅ Dockerfile actualizado con COPY models, env vars, validación
- ✅ `.ultralytics.yaml` creado para bloquear downloads
- ✅ `MarketTool.py` refactorizado para lazy loading YOLO
- ✅ Cache warmup no-bloqueante activado
- ✅ Logging deduplicado
- ✅ Deprecation warnings eliminados (~20 datetime.utcnow() → datetime.now(UTC))

---

## 📋 QUÉ HACER AHORA (Orden de Pasos)

### PASO 1: Verificar cambios en código (5 minutos)

```bash
cd /path/to/MarketTool

# Ejecutar verificación
bash verify_changes.sh
```

**DEBE mostrar** todo ✅ green. Si hay ❌ o ⚠️, pausar y leer [CAMBIOS_REALIZADOS.md](./CAMBIOS_REALIZADOS.md).

---

### PASO 2: Testing Local (10 minutos)

**Opción A: Rápido (sin cargar modelos)**
```bash
# Verifica importación rápida
python -c "import time; s=time.time(); from MarketTool import app; print(f'Import: {time.time()-s:.2f}s')"
# Esperado: <2 segundos
```

**Opción B: Completo (con modelos)**
Sigue pasos del [LOCAL_TESTING_GUIDE.md](./LOCAL_TESTING_GUIDE.md)

---

### PASO 3: Build Docker (10-15 minutos)

```bash
cd /path/to/MarketTool

# Iniciar build
docker build -t markettool:latest . --no-cache

# Esperar a que complete...
```

**Monitorear build output:**
- ❌ **NO DEBE ver**: `Downloading yolov8n`, `Downloading models`, etc.
- ✅ **DEBE ver**: 
  ```
  COPY patrones.pt ruido.pt ./
  RUN ls -lh /app/*.pt
  [output should show both files]
  ```

---

### PASO 4: Validar Docker (5 minutos)

```bash
# Script automatizado de validación
bash validate_docker.sh
```

O manual:
```bash
# Verificar modelos en contenedor
docker run --rm markettool:latest ls -lh /app/*.pt

# Verificar config YOLO
docker run --rm markettool:latest cat /app/.ultralytics.yaml

# Verificar startup rápido
time docker run --rm -p 5000:5000 markettool:latest &
sleep 20
curl http://localhost:5000/cache-status
```

**Esperado**:
- ✅ Ambos .pt files muestran >100 MB
- ✅ `/cache-status` retorna JSON con warmup info
- ✅ Startup time: Dell <20s, ASUS <35s

---

### PASO 5: Exportar Imagen (5 minutos)

Si necesitas subir a registro remoto:

```bash
# Copia imagen con tag de registry
docker tag markettool:latest docker.io/yourregistry/markettool:latest
docker push docker.io/yourregistry/markettool:latest

# O si es privado:
docker login
docker push docker.io/yourregistry/markettool:latest
```

---

### PASO 6: Deploy en Kubernetes (10-20 minutos)

Actualiza `markettool-deployment.yaml`:
```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: markettool
spec:
  replicas: 2  # Dell + ASUS o ambos en mismo cluster
  selector:
    matchLabels:
      app: markettool
  template:
    metadata:
      labels:
        app: markettool
    spec:
      containers:
      - name: markettool
        image: markettool:latest  # O docker.io/yourregistry/markettool:latest
        imagePullPolicy: Always
        ports:
        - containerPort: 5000
        env:
        - name: CACHE_WARMUP_ENABLED
          value: "true"
        - name: CACHE_WARMUP_BLOCKING_STARTUP
          value: "false"
        - name: CACHE_WARMUP_LEADER_ONLY
          value: "false"
        healthCheck:
          httpGet:
            path: /cache-status
            port: 5000
          initialDelaySeconds: 30
          periodSeconds: 10
```

Deploy:
```bash
kubectl apply -f markettool-deployment.yaml
kubectl get pods  # Esperar STATUS "Running"
```

---

### PASO 7: Monitorear Startup (5-10 minutos)

```bash
# Ver logs de startup
kubectl logs -f deployment/markettool --all-containers

# BUSCAR ESTAS LÍNEAS:
# [OK] App starting...
# [YOLO] Models loaded via lazy loading
# [Warmup] Starting cache warmup...
# [Warmup] ===== COMPLETADO =====  <-- ESTO SIGNIFICA WARMUP TERMINÓ

# Si ves "Downloading yolov8n" = PROBLEMA, parar aquí y debuggear
```

---

### PASO 8: Verificar Performance en ambas máquinas

**En Dell:**
```bash
curl http://dell-pod-ip:5000/cache-status | jq
# Esperado:
# - warmup.elapsed_seconds: 15-25
# - cache_stats: >85% hit rate
```

**En ASUS:**
```bash
curl http://asus-pod-ip:5000/cache-status | jq
# Esperado:
# - warmup.elapsed_seconds: 25-35
# - cache_stats: >85% hit rate
```

**Diferencia aceptable**: ASUS puede ser 10-15s más lento, pero ambos deben:
- ✅ Startup < 40 segundos
- ✅ Cache hit rate > 80%
- ✅ Segunda ejecución muy rápida (<2s)

---

## 🆘 TROUBLESHOOTING

### Problema: Build aún toma mucho tiempo

**Síntoma**: `docker build` tarda >30 minutos

**Causa probable**: Descarga de modelos o dependencias

**Solución**:
```bash
# Limpiar capas viejas
docker system prune -a

# Rebuild sin cache
docker build -t markettool:latest . --no-cache
```

---

### Problema: Container arranca pero "Progress: |--| 1.5%"

**Síntoma**: Startup OK pero logs muestran barra de progreso estancada

**Causa probable**: YOLO intentando descargar, o modelo no carga en lazy loading

**Solución**:
1. Verificar `/app/patrones.pt` y `/app/ruido.pt` existen en container:
   ```bash
   docker run --rm markettool:latest ls -lh /app/*.pt
   ```

2. Verificar con network disabled:
   ```bash
   docker run --rm --network none markettool:latest python check_models.py
   ```

3. Si falla "Connection refused": YOLO intenta descargar. Revisar:
   - ¿`.ultralytics.yaml` existe?
   - ¿`YOLO_CACHE` env var en Dockerfile?
   - ¿Archivo models copied correctamente?

---

### Problema: Cache hit rate <60%

**Síntoma**: `/cache-status` muestra "45% hit rate" después de warmup

**Causa probable**: Cache key inestable (puede haber precios en la key)

**Solución**:
```bash
# Verificar cache key en código
grep -n "cache_key = " MarketTool.py

# Debe ser: symbol|tf|df_len
# NO debe incluir: price, open, close, hash(price), etc.
```

---

### Problema: ASUS sigue lento (>60s startup)

**Síntoma**: Dell: 18s, ASUS: 120s

**Causa probable**: Problema de red o hardware específico ASUS

**Solución**:
1. Verificar `/cache-status` en ASUS:
   ```bash
   curl http://asus-pod-ip:5000/cache-status | jq .warmup.status
   # Debería ser: "completed"
   ```

2. Si "in_progress" después de 60s: warmup lento
   ```bash
   # Revisar CACHE_WARMUP_CONCURRENCY en .env
   # ASUS podría necesitar: CACHE_WARMUP_CONCURRENCY=4 (vs 8)
   ```

3. Si "not started": warmup no inició
   ```bash
   # Revisar CACHE_WARMUP_ENABLED=true en Dockerfile
   ```

---

## ✨ SEÑALES DE ÉXITO

Después de todo, deberías ver:

✅ **Startup remoto**:
```
[INFO] MarketTool starting on port 5000...
[YOLO] Models loaded via lazy loading
[Warmup] Starting cache warmup with 8 concurrent tasks...
[Warmup] ===== COMPLETADO ===== (took 23.5s)
✅ Ready to serve requests
```

✅ **Cache Status endpoint**:
```json
{
  "warmup": {
    "status": "completed",
    "elapsed_seconds": 23.5
  },
  "cache_stats": {
    "niveles_cache": "1847/2314 (79.8% hit rate)",
    "atr_cache": "1847/2314 (79.8% hit rate)"
  }
}
```

✅ **No duplicate logs**: Same request no longer appears twice

✅ **Fast second run**: Same asset analysis in <1s (vs 5-10s first time)

---

## 📞 SUPPORT

Documentación referencia:
- [CAMBIOS_REALIZADOS.md](./CAMBIOS_REALIZADOS.md) - Detalle técnico
- [PRE_BUILD_CHECKLIST.md](./PRE_BUILD_CHECKLIST.md) - Verificar antes de build
- [LOCAL_TESTING_GUIDE.md](./LOCAL_TESTING_GUIDE.md) - Tests sin Docker
- [POST_DOCKER_VALIDATION.md](./POST_DOCKER_VALIDATION.md) - Tests post-Docker

Scripts:
- `verify_changes.sh` - Verificar que cambios están en código
- `validate_docker.sh` - Validar Docker image después de build
- `build_and_deploy.sh` - Build + push + deploy (todo en uno)
- `check_models.py` - Diagnóstico de modelos YOLO

---

## 🎯 TIMELINE ESPERADO

- **Paso 1 (Verificación)**: 5 min
- **Paso 2 (Testing local)**: 10 min
- **Paso 3 (Build Docker)**: 10-15 min
- **Paso 4 (Validar Docker)**: 5 min
- **Paso 5 (Exportar)**: 5 min (skip si no necesitas remoto)
- **Paso 6 (Deploy K8s)**: 10-20 min
- **Paso 7 (Monitorear)**: 5 min
- **Paso 8 (Performance)**: 5 min

**TOTAL**: ~60 minutos desde aquí hasta verificación final

