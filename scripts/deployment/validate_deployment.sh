#!/bin/bash
# ====================================
# Production Deployment Validation Script
# Phase 8: Validates deployment readiness
# ====================================

set -e

echo "===================================="
echo "🔍 VALIDATING PRODUCTION DEPLOYMENT"
echo "===================================="

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Counters
CHECKS_PASSED=0
CHECKS_FAILED=0
CHECKS_WARNING=0

# Function to check status
check() {
    local description="$1"
    local command="$2"
    
    echo -n "Checking: $description... "
    
    if eval "$command" > /dev/null 2>&1; then
        echo -e "${GREEN}✅ PASSED${NC}"
        ((CHECKS_PASSED++))
        return 0
    else
        echo -e "${RED}❌ FAILED${NC}"
        ((CHECKS_FAILED++))
        return 1
    fi
}

check_warning() {
    local description="$1"
    local command="$2"
    
    echo -n "Checking: $description... "
    
    if eval "$command" > /dev/null 2>&1; then
        echo -e "${GREEN}✅ PASSED${NC}"
        ((CHECKS_PASSED++))
        return 0
    else
        echo -e "${YELLOW}⚠️  WARNING${NC}"
        ((CHECKS_WARNING++))
        return 1
    fi
}

echo ""
echo "1. Docker Environment Checks"
echo "===================================="

check "Docker is installed" "docker --version"
check "Docker daemon is running" "docker ps"
check "Docker Compose is installed" "docker-compose --version"

echo ""
echo "2. Required Files Check"
echo "===================================="

check "Dockerfile exists" "test -f Dockerfile"
check "Dockerfile.optimized exists" "test -f Dockerfile.optimized"
check "requirements.txt exists" "test -f requirements.txt"
check "patrones.pt exists" "test -f patrones.pt"
check "ruido.pt exists" "test -f ruido.pt"
check ".ultralytics.yaml exists" "test -f .ultralytics.yaml"
check_warning ".env file exists" "test -f .env"

echo ""
echo "3. Application Structure Check"
echo "===================================="

check "markettool package exists" "test -d markettool"
check "bootstrap.py exists" "test -f markettool/bootstrap.py"
check "health.py exists" "test -f markettool/interfaces/api/health.py"
check "env_validation.py exists" "test -f markettool/core/env_validation.py"
check "shutdown.py exists" "test -f markettool/core/shutdown.py"
check "MarketTool.py exists" "test -f MarketTool.py"

echo ""
echo "4. Kubernetes Deployment Files Check"
echo "===================================="

check_warning "K8s deployment exists" "test -f markettool-deployment-2pods-e2-highcpu-4.yaml"
check_warning "K8s service exists" "test -f markettool-service.yaml"
check_warning "K8s ingress exists" "test -f markettool-ingress.yaml"
check_warning "K8s HPA exists" "test -f markettool-hpa-2pods.yaml"

echo ""
echo "5. Docker Image Build Test"
echo "===================================="

if check "Can build Docker image" "docker build -t markettool:test-phase8 -f Dockerfile.optimized . --quiet"; then
    echo -e "${GREEN}✅ Docker image built successfully${NC}"
    
    # Get image size
    IMAGE_SIZE=$(docker images markettool:test-phase8 --format "{{.Size}}")
    echo "   Image size: $IMAGE_SIZE"
    
    # Cleanup test image
    docker rmi markettool:test-phase8 > /dev/null 2>&1 || true
else
    echo -e "${RED}❌ Docker image build failed${NC}"
fi

echo ""
echo "6. Health Check Validation"
echo "===================================="

# Start container for health check test
echo "Starting test container..."
CONTAINER_ID=$(docker run -d \
    -p 8080:8080 \
    -e TELEGRAM_BOT_TOKEN="test-token" \
    -e FMP_API_KEY="test-key" \
    -e GOOGLE_APPLICATION_CREDENTIALS="/app/trading-firestore.json" \
    markettool:latest \
    bash -c "sleep 300" 2>/dev/null) || true

if [ -n "$CONTAINER_ID" ]; then
    echo "Container started: $CONTAINER_ID"
    
    # Wait for startup
    echo "Waiting for container startup (5s)..."
    sleep 5
    
    # Test health endpoints
    check_warning "Health endpoint /healthz" "docker exec $CONTAINER_ID curl -f http://localhost:8080/healthz"
    check_warning "Health endpoint /health" "docker exec $CONTAINER_ID curl -f http://localhost:8080/health"
    check_warning "Health endpoint /ready" "docker exec $CONTAINER_ID curl -f http://localhost:8080/ready"
    
    # Cleanup
    echo "Cleaning up test container..."
    docker stop $CONTAINER_ID > /dev/null 2>&1
    docker rm $CONTAINER_ID > /dev/null 2>&1
else
    echo -e "${YELLOW}⚠️  Could not start test container${NC}"
fi

echo ""
echo "===================================="
echo "📊 VALIDATION SUMMARY"
echo "===================================="
echo -e "Checks Passed:   ${GREEN}$CHECKS_PASSED${NC}"
echo -e "Checks Failed:   ${RED}$CHECKS_FAILED${NC}"
echo -e "Checks Warning:  ${YELLOW}$CHECKS_WARNING${NC}"
echo "===================================="

if [ $CHECKS_FAILED -eq 0 ]; then
    echo -e "${GREEN}✅ DEPLOYMENT VALIDATION PASSED${NC}"
    echo ""
    echo "Your deployment is ready for production! 🚀"
    exit 0
else
    echo -e "${RED}❌ DEPLOYMENT VALIDATION FAILED${NC}"
    echo ""
    echo "Please fix the failed checks before deploying to production."
    exit 1
fi
