#!/bin/bash
# ====================================
# Quick Deployment Script
# Phase 8: Simplified deployment workflow
# ====================================

set -e

echo "🚀 MarketTool Deployment Script"
echo "================================"
echo ""

# Check if .env file exists
if [ ! -f .env ]; then
    echo "❌ Error: .env file not found"
    echo "Please create a .env file with required environment variables"
    exit 1
fi

# Source environment variables
source .env

# Validate required variables
REQUIRED_VARS=(
    "TELEGRAM_BOT_TOKEN"
    "FMP_API_KEY"
    "GOOGLE_APPLICATION_CREDENTIALS"
)

echo "Validating environment variables..."
for var in "${REQUIRED_VARS[@]}"; do
    if [ -z "${!var}" ]; then
        echo "❌ Error: $var is not set"
        exit 1
    fi
    echo "✅ $var is set"
done
echo ""

# Build Docker image
echo "Building Docker image..."
docker build -t markettool:latest -f Dockerfile.optimized .
echo "✅ Docker image built successfully"
echo ""

# Run validation
if [ -f validate_deployment.sh ]; then
    echo "Running deployment validation..."
    bash validate_deployment.sh
    echo ""
fi

# Start container
echo "Starting MarketTool container..."
docker run -d \
    --name markettool \
    --restart unless-stopped \
    -p 8080:8080 \
    --env-file .env \
    -v "$(pwd)/trading-firestore.json:/app/trading-firestore.json:ro" \
    markettool:latest

echo "✅ Container started: markettool"
echo ""

# Wait for startup
echo "Waiting for application startup (10s)..."
sleep 10

# Test health endpoints
echo "Testing health endpoints..."
if curl -f http://localhost:8080/healthz > /dev/null 2>&1; then
    echo "✅ /healthz - OK"
else
    echo "❌ /healthz - FAILED"
fi

if curl -f http://localhost:8080/ready > /dev/null 2>&1; then
    echo "✅ /ready - OK"
else
    echo "⚠️  /ready - Not ready yet (may need more time)"
fi
echo ""

echo "================================"
echo "🎉 Deployment Complete!"
echo "================================"
echo ""
echo "Container: markettool"
echo "Port: 8080"
echo ""
echo "Useful commands:"
echo "  View logs:    docker logs -f markettool"
echo "  Stop:         docker stop markettool"
echo "  Restart:      docker restart markettool"
echo "  Remove:       docker rm -f markettool"
echo "  Health check: curl http://localhost:8080/health | jq"
echo ""
