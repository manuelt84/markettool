# 🚀 QUICK REFERENCE - COMANDOS ESENCIALES

## 📍 Localización
Todos los comandos asumen que estás en `/path/to/MarketTool/`

---

## ✅ PRE-BUILD CHECKS

```bash
# 1. Verificar archivos presentes
ls -lh patrones.pt ruido.pt .ultralytics.yaml

# 2. Verificar cambios en código
bash verify_changes.sh

# 3. Test local rápido
python -c "import time; s=time.time(); from MarketTool import app; print(f'Startup: {time.time()-s:.2f}s')"
```

---

## 🐳 DOCKER BUILD

```bash
# Build simple
docker build -t markettool:latest .

# Build sin cache (limpia capas viejas)
docker build -t markettool:latest . --no-cache

# Build con output completo
docker build -t markettool:latest . -v 2>&1 | tee build.log

# Verificar qué se copió
docker run --rm markettool:latest ls -lh /app/*.pt
```

---

## 🧪 DOCKER TEST

```bash
# Test startup time
time docker run --rm -p 5000:5000 markettool:latest &
sleep 20
curl http://localhost:5000/cache-status | jq

# Test sin internet (verificar modelos locales)
docker run --rm --network none markettool:latest python check_models.py

# Ver logs completos de startup
docker run --rm markettool:latest 2>&1 | head -50

# Enter container para debugging
docker run --rm -it markettool:latest bash
```

---

## 📊 MONITOREAR CACHE

```bash
# En contenedor en ejecución:
curl http://localhost:5000/cache-status | jq

# Ver solo warmup status
curl -s http://localhost:5000/cache-status | jq .warmup.status

# Ver solo cache hit rates
curl -s http://localhost:5000/cache-status | jq .cache_stats
```

---

## ☸️ KUBERNETES

```bash
# Deploy
kubectl apply -f markettool-deployment.yaml

# Ver pods
kubectl get pods -l app=markettool

# Ver logs
kubectl logs deployment/markettool --all-containers

# Ver logs en tiempo real
kubectl logs -f deployment/markettool --all-containers

# Reiniciar pods
kubectl rollout restart deployment/markettool

# Ver estado detallado
kubectl describe deployment markettool

# Acceder a /cache-status
kubectl port-forward deployment/markettool 5000:5000
curl http://localhost:5000/cache-status | jq
```

---

## 🔍 DEBUGGING

### Problema: Modelos no se cargan
```bash
# Entrar al container
docker run --rm -it markettool:latest bash

# Dentro del container:
ls -lh /app/*.pt          # Deben existir
python check_models.py     # Debe pasar
cat /app/.ultralytics.yaml # Verificar config
```

### Problema: Startup lento
```bash
# Ver si YOLO intenta descargar
docker run --rm markettool:latest 2>&1 | grep -i download

# NetworkDisabled test
docker run --rm --network none markettool:latest python -c "from MarketTool import app"
# Si falla = YOLO intentando internet

# Ver logs con timestamps
docker run --rm markettool:latest 2>&1 | grep -E "INIT|YOLO|Warmup|COMPLETADO"
```

### Problema: Cache hit rate bajo
```bash
# Ver cache stats
curl -s http://localhost:5000/cache-status | jq .cache_stats

# Verificar cache key en código
grep -n "cache_key = " MarketTool.py | head -3
# Debería ser: symbol|tf|df_len (SIN price)
```

---

## 📈 PERFORMANCE BASELINE

```bash
# Medir primera ejecución
time curl http://localhost:5000/api/analyze \
  -X POST \
  -H "Content-Type: application/json" \
  -d '{"symbol":"EURUSD","timeframe":"1h","days":5}'

# Segunda ejecución (debe ser más rápida)
time curl http://localhost:5000/api/analyze \
  -X POST \
  -H "Content-Type: application/json" \
  -d '{"symbol":"EURUSD","timeframe":"1h","days":5}'

# Comparar: segunda debería ser 5-10x más rápida
```

---

## 🚀 DEPLOYMENT RÁPIDO (All-in-one)

```bash
# Si tienes build_and_deploy.sh:
bash build_and_deploy.sh markettool:latest

# O manual:
docker build -t markettool:latest . && \
docker run --rm -d -p 5000:5000 markettool:latest && \
echo "✅ Container running at http://localhost:5000"
```

---

## 📤 EXPORTAR A REGISTRY

```bash
# Taggear
docker tag markettool:latest docker.io/myusername/markettool:latest

# Login (solo primera vez)
docker login

# Push
docker push docker.io/myusername/markettool:latest

# Verificar en remoto
docker pull docker.io/myusername/markettool:latest
```

---

## 🧹 CLEANUP

```bash
# Remover container de prueba
docker rm -f markettool-test

# Remover imagen vieja
docker rmi markettool:old

# Limpiar todo (⚠️ CUIDADO)
docker system prune -a

# Ver espacio en disco
docker system df
```

---

## 📋 CHECKLIST POR ETAPA

### Pre-Build (5 min)
```bash
[ ] bash verify_changes.sh  # Todo debe ser ✅
```

### Post-Build (15 min)
```bash
[ ] docker run --rm markettool:latest ls -lh /app/*.pt  # Ver modelos
[ ] docker run --rm markettool:latest python check_models.py  # Check pass
[ ] time docker run --rm -p 5000:5000 markettool:latest  # Startup time
[ ] curl http://localhost:5000/cache-status | jq  # Cache info
```

### Post-Deploy (10 min)
```bash
[ ] kubectl get pods | grep markettool  # Status: Running
[ ] kubectl logs deployment/markettool | grep COMPLETADO  # Warmup done
[ ] curl http://pod-ip:5000/cache-status | jq  # Cache info
```

---

## ⚡ SHORTCUT ALIASES

Agregar a `.bashrc` o `.zshrc`:

```bash
alias mt='cd /path/to/MarketTool'
alias mtbuild='docker build -t markettool:latest .'
alias mttest='docker run --rm -p 5000:5000 markettool:latest'
alias mtlog='docker logs -f $(docker ps | grep markettool | awk "{print $1}")'
alias mtls='curl -s http://localhost:5000/cache-status | jq'
alias mkdeploy='kubectl apply -f markettool-deployment.yaml'
alias mklogs='kubectl logs -f deployment/markettool --all-containers'
```

Entonces puedes:
```bash
mt         # Go to directory
mtbuild    # Build image
mttest     # Run container
mtls       # Check cache status
mkdeploy   # Deploy on K8s
mklogs     # See logs
```

---

## 📊 EXPECTED OUTPUT

### Startup exitoso:
```
[INIT] App starting...
[YOLO] Models loaded via lazy loading
[Warmup] Starting cache warmup...
  [+] EURUSD/1h
  [+] GBPUSD/1h
  ... (más assets)
[Warmup] ===== COMPLETADO ===== (took 23.5s)
✅ Ready to serve requests
```

### Cache status:
```json
{
  "warmup": {
    "status": "completed",
    "elapsed_seconds": 23.5,
    "start_time": "2025-01-15T10:30:45"
  },
  "cache_stats": {
    "niveles_cache": "1847/2314 (79.8% hit rate)",
    "atr_cache": "1847/2314 (79.8% hit rate)"
  },
  "config": {
    "CACHE_WARMUP_ENABLED": true,
    "CACHE_WARMUP_BLOCKING_STARTUP": false
  }
}
```

---

## 🆘 ONE-LINER TROUBLESHOOTING

```bash
# Modelos no se cargan
docker run --rm markettool:latest ls -lh /app/*.pt 2>&1 | grep -q ".pt" && echo "✅ Models OK" || echo "❌ Models missing"

# YOLO intenta descargar
docker run --rm --network none markettool:latest 2>&1 | grep -i download && echo "❌ YOLO downloading" || echo "✅ Offline mode OK"

# Startup time
docker run --rm -p 5000:5000 markettool:latest &
sleep 25 && curl -s http://localhost:5000/cache-status | jq .warmup.elapsed_seconds

# Cache hit rate verificación
curl -s http://localhost:5000/cache-status | jq '.cache_stats | .[] | split("(")[1] | split("%")[0]'
```

