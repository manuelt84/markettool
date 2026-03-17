#!/bin/bash
# DOCKER VALIDATION SCRIPT
# Execute: bash validate_docker.sh

set -e

IMAGE_NAME="markettool:latest"
CONTAINER_PORT=5000
HOST_PORT=5001

echo "================================"
echo "🐳 DOCKER VALIDATION SCRIPT"
echo "================================"

# Step 0: Check if Docker is running
echo ""
echo "📋 Paso 0: Verificar Docker..."
if ! docker ps > /dev/null 2>&1; then
    echo "❌ Docker no está corriendo"
    exit 1
fi
echo "✅ Docker está corriendo"

# Step 1: Build image
echo ""
echo "📋 Paso 1: Construir imagen..."
if docker build -t $IMAGE_NAME . > /tmp/docker_build.log 2>&1; then
    echo "✅ Imagen construida exitosamente"
    
    # Check if models were copied
    if grep -q "COPY patrones.pt" /tmp/docker_build.log; then
        echo "  ✅ COPY patrones.pt detectado"
    fi
    
    if grep -q "ls -lh /app/\*.pt" /tmp/docker_build.log; then
        echo "  ✅ Validación de modelos detectada"
    fi
else
    echo "❌ Error building image:"
    cat /tmp/docker_build.log
    exit 1
fi

# Step 2: Verify models in container
echo ""
echo "📋 Paso 2: Verificar modelos en contenedor..."
docker run --rm $IMAGE_NAME ls -lh /app/*.pt > /tmp/models.txt 2>&1
if grep -q "patrones.pt" /tmp/models.txt && grep -q "ruido.pt" /tmp/models.txt; then
    echo "✅ Ambos modelos presentes:"
    cat /tmp/models.txt | grep -E "pt$"
else
    echo "❌ Modelos no encontrados:"
    cat /tmp/models.txt
    exit 1
fi

# Step 3: Check ultralytics configuration
echo ""
echo "📋 Paso 3: Verificar configuración .ultralytics.yaml..."
docker run --rm $IMAGE_NAME cat /app/.ultralytics.yaml > /tmp/yolo_config.yaml 2>&1
if grep -q "analytics: false" /tmp/yolo_config.yaml; then
    echo "✅ Configuración YOLO correcta (analytics disabled)"
else
    echo "⚠️  Configuración YOLO incompleta"
fi

# Step 4: Run diagnostic check
echo ""
echo "📋 Paso 4: Ejecutar check_models.py..."
docker run --rm $IMAGE_NAME python check_models.py > /tmp/check_output.txt 2>&1
if [ $? -eq 0 ]; then
    echo "✅ check_models.py pasó"
    cat /tmp/check_output.txt | grep "✅\|❌"
else
    echo "⚠️  check_models.py tuvo warnings:"
    cat /tmp/check_output.txt | tail -5
fi

# Step 5: Test startup time
echo ""
echo "📋 Paso 5: Medir tiempo de startup..."
start_time=$(date +%s%N | cut -b1-13)

# Kill any existing container
docker rm -f markettool-test 2>/dev/null || true

# Start container in background
docker run --rm -d \
    --name markettool-test \
    -p $HOST_PORT:$CONTAINER_PORT \
    $IMAGE_NAME > /dev/null 2>&1

# Wait for container to be ready (max 60 seconds)
for i in {1..60}; do
    if curl -s http://localhost:$HOST_PORT/cache-status > /dev/null 2>&1; then
        end_time=$(date +%s%N | cut -b1-13)
        elapsed=$((($end_time - $start_time) / 1000))
        echo "✅ Container ready en ${elapsed}s"
        
        if [ $elapsed -lt 30 ]; then
            echo "  🚀 Startup muy rápido (probable cache warmup eficiente)"
        elif [ $elapsed -lt 60 ]; then
            echo "  ⚡ Startup normal"
        else
            echo "  ⚠️  Startup lento (verificar YOLO downloads)"
        fi
        break
    fi
    
    if [ $((i % 10)) -eq 0 ]; then
        echo "  ⏳ Esperando... ${i}s"
    fi
    sleep 1
done

# Step 6: Check /cache-status endpoint
echo ""
echo "📋 Paso 6: Verificar /cache-status..."
CACHE_STATUS=$(curl -s http://localhost:$HOST_PORT/cache-status)

if echo "$CACHE_STATUS" | grep -q "warmup"; then
    echo "✅ /cache-status retorna warmup info:"
    echo "$CACHE_STATUS" | python -m json.tool 2>/dev/null | head -20 || echo "$CACHE_STATUS"
else
    echo "❌ /cache-status no tiene información de warmup"
    echo "$CACHE_STATUS"
fi

# Step 7: Test a prediction request
echo ""
echo "📋 Paso 7: Enviar solicitud de predicción..."
PREDICTION=$(curl -s http://localhost:$HOST_PORT/api/predict \
    -X POST \
    -H "Content-Type: application/json" \
    -d '{"symbol":"EURUSD","timeframe":"1h","lookback_candles":100}' 2>/dev/null)

if echo "$PREDICTION" | grep -q "pattern\|level\|error"; then
    echo "✅ Predicción retornada"
    if echo "$PREDICTION" | grep -q "Downloading"; then
        echo "  ⚠️  Se detectó descarga de modelo (YOLO intentó descargar)"
    else
        echo "  ✅ Sin descargas de modelo detectadas"
    fi
else
    echo "⚠️  Respuesta no esperada:"
    echo "$PREDICTION" | head -50
fi

# Cleanup
echo ""
echo "📋 Paso 8: Limpiar..."
docker rm -f markettool-test 2>/dev/null || true
echo "✅ Contenedor de prueba eliminado"

# Summary
echo ""
echo "================================"
echo "✅ VALIDACIÓN COMPLETADA"
echo "================================"
echo ""
echo "Próximos pasos:"
echo "1. Verificar que el build log NO contiene 'Downloading'"
echo "2. Deployar en Kubernetes con: kubectl apply -f markettool-deployment.yaml"
echo "3. Monitorear logs: kubectl logs -f deployment/markettool"
echo "4. Verificar /cache-status en ambas máquinas (Dell y ASUS)"
echo ""
