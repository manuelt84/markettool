#!/bin/bash
# QUICK START - BUILD & DEPLOY SCRIPT

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}================================${NC}"
echo -e "${GREEN}🚀 MARKETTOOL BUILD & DEPLOY${NC}"
echo -e "${GREEN}================================${NC}"

# Configuration
IMAGE_NAME="${1:-markettool:latest}"
DOCKERFILE_PATH="${2:-.}"
REGISTRY="${3:-}"  # Optional: docker.io/myrepo, ghcr.io/myuser, etc.

echo ""
echo -e "${YELLOW}Configuración:${NC}"
echo "  Image name: $IMAGE_NAME"
echo "  Dockerfile path: $DOCKERFILE_PATH"
echo "  Registry: ${REGISTRY:-(local only)}"
echo ""

# Step 1: Verify files exist
echo -e "${YELLOW}1. Verificar archivos requeridos...${NC}"

REQUIRED_FILES=("patrones.pt" "ruido.pt" ".ultralytics.yaml" "Dockerfile" "MarketTool.py")
MISSING=0

for file in "${REQUIRED_FILES[@]}"; do
    if [ -f "$DOCKERFILE_PATH/$file" ]; then
        echo -e "  ${GREEN}✅${NC} $file"
    else
        echo -e "  ${RED}❌${NC} $file: NO ENCONTRADO"
        MISSING=$((MISSING + 1))
    fi
done

if [ $MISSING -gt 0 ]; then
    echo -e "${RED}Error: $MISSING archivos faltantes.${NC}"
    exit 1
fi

# Step 2: Check Docker
echo ""
echo -e "${YELLOW}2. Verificar Docker...${NC}"

if ! command -v docker &> /dev/null; then
    echo -e "${RED}❌ Docker no está instalado${NC}"
    exit 1
fi

if ! docker ps > /dev/null 2>&1; then
    echo -e "${RED}❌ Docker daemon no está corriendo${NC}"
    exit 1
fi

echo -e "  ${GREEN}✅${NC} Docker está corriendo"

# Step 3: Build image
echo ""
echo -e "${YELLOW}3. Compilar imagen (esto puede tomar 5-10 minutos)...${NC}"

if docker build -t "$IMAGE_NAME" . > /tmp/build.log 2>&1; then
    echo -e "  ${GREEN}✅${NC} Imagen compilada exitosamente"
    
    # Show build summary
    echo ""
    echo -e "${YELLOW}   Build summary:${NC}"
    [ -f /tmp/build.log ] && grep -E "Step|COPY|RUN|===>" /tmp/build.log | tail -10 || true
else
    echo -e "  ${RED}❌${NC} Error compiling image:"
    cat /tmp/build.log
    exit 1
fi

# Step 4: Verify build
echo ""
echo -e "${YELLOW}4. Validar compilación...${NC}"

docker run --rm "$IMAGE_NAME" ls -lh /app/*.pt 2>/dev/null | grep -E "patrones|ruido" | while read line; do
    echo -e "  ${GREEN}✅${NC} $line"
done

# Step 5: Test startup
echo ""
echo -e "${YELLOW}5. Test de startup (timeout 60s)...${NC}"

docker rm -f markettool-test 2>/dev/null || true

START_TIME=$(date +%s)
docker run --rm -d \
    --name markettool-test \
    -p 5001:5000 \
    "$IMAGE_NAME" &

# Wait for app to be ready
for i in {1..60}; do
    if docker exec markettool-test curl -s http://localhost:5000/health > /dev/null 2>&1; then
        END_TIME=$(date +%s)
        ELAPSED=$((END_TIME - START_TIME))
        echo -e "  ${GREEN}✅${NC} App ready en ${ELAPSED}s"
        
        # Test cache-status
        if docker exec markettool-test curl -s http://localhost:5000/cache-status > /tmp/cache_status.json 2>&1; then
            echo -e "  ${GREEN}✅${NC} Cache status endpoint respondiendo"
            grep -q "warmup" /tmp/cache_status.json && echo -e "  ${GREEN}✅${NC} Warmup info disponible" || true
        fi
        break
    fi
    
    if [ $((i % 10)) -eq 0 ]; then
        echo -e "  ⏳ Esperando... ${i}s"
    fi
    
    sleep 1
done

docker rm -f markettool-test 2>/dev/null || true

# Step 6: Push to registry (if provided)
if [ -n "$REGISTRY" ]; then
    echo ""
    echo -e "${YELLOW}6. Push a registry...${NC}"
    
    FULL_IMAGE="$REGISTRY/$IMAGE_NAME"
    echo -e "  Tagging: $IMAGE_NAME -> $FULL_IMAGE"
    docker tag "$IMAGE_NAME" "$FULL_IMAGE"
    
    echo -e "  ${YELLOW}Pushing...${NC} (this may take a while)"
    if docker push "$FULL_IMAGE" > /tmp/push.log 2>&1; then
        echo -e "  ${GREEN}✅${NC} Pushed successfully"
    else
        echo -e "  ${RED}⚠️${NC} Push failed:"
        tail -20 /tmp/push.log
    fi
fi

# Step 7: Show next steps
echo ""
echo -e "${GREEN}================================${NC}"
echo -e "${GREEN}✅ BUILD COMPLETADO EXITOSAMENTE${NC}"
echo -e "${GREEN}================================${NC}"
echo ""

echo -e "${YELLOW}Próximos pasos:${NC}"
echo ""
echo "📋 LOCAL TESTING:"
echo "  docker run --rm -p 5000:5000 $IMAGE_NAME"
echo ""
echo "📋 KUBERNETES DEPLOYMENT:"
echo "  kubectl apply -f markettool-deployment.yaml"
echo "  kubectl logs -f deployment/markettool"
echo ""
echo "📋 VERIFY RUNNING:"
echo "  curl http://localhost:5000/cache-status | jq"
echo ""
echo "📋 PERFORMANCE CHECK:"
echo "  # Dell: should be 15-20s startup"
echo "  # ASUS: should be 25-35s startup"
echo "  # If >60s: check /cache-status for warmup completion"
echo ""

echo -e "${YELLOW}Debugging tips:${NC}"
echo "  • Log access: kubectl logs deployment/markettool --all-containers --timestamps"
echo "  • Check cache: curl http://localhost:5000/cache-status"
echo "  • Monitor warmup: tail -f logs.txt | grep COMPLETADO"
echo "  • Model verification: docker run --rm IMAGE_NAME python check_models.py"
echo ""
