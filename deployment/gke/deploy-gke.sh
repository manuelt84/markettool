#!/bin/bash
#
# MarketTool GKE Deployment Script
# Automaticamente despliega MarketTool en Google Kubernetes Engine
# Uso: ./deploy-gke.sh [cluster-name] [region] [project-id]
#

set -e

# Configuration
CLUSTER_NAME="${1:-markettool-cluster}"
REGION="${2:-southamerica-west1}"
PROJECT_ID="${3:-trading-449607}"
NAMESPACE="trading"
IMAGE_REPO="southamerica-west1-docker.pkg.dev/trading-449607/trading-repo/markettool:latest"
MANIFESTS_DIR="deployment/gke/manifests"

# Colors
GREEN='\033[0;32m'
BLUE='\033[0;34m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

# Functions
info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

success() {
    echo -e "${GREEN}[✓]${NC} $1"
}

error() {
    echo -e "${RED}[ERROR]${NC} $1"
    exit 1
}

warning() {
    echo -e "${YELLOW}[⚠]${NC} $1"
}

# Check prerequisites
info "Verificando prerequisitos..."
command -v gcloud >/dev/null 2>&1 || error "gcloud CLI no está instalado"
command -v kubectl >/dev/null 2>&1 || error "kubectl no está instalado"
command -v base64 >/dev/null 2>&1 || error "base64 no está disponible"

success "Prerequisitos validados"

# Authenticate with GCP
info "Autenticando con Google Cloud..."
gcloud auth application-default login || error "No se pudo autenticar con GCP"
gcloud config set project $PROJECT_ID
success "Autenticado con proyecto: $PROJECT_ID"

# Get cluster credentials
info "Conectando a cluster GKE: $CLUSTER_NAME ($REGION)..."
gcloud container clusters get-credentials $CLUSTER_NAME \
    --region=$REGION \
    --project=$PROJECT_ID || error "No se pudo conectar al cluster"
success "Conectado a cluster: $CLUSTER_NAME"

# Verify kubectl connection
info "Verificando conexión a Kubernetes..."
kubectl cluster-info | head -1 || error "No se puede alcanzar el cluster"
success "Conexión a Kubernetes verificada"

# Create namespace if not exists
info "Asegurando namespace '$NAMESPACE'..."
kubectl create namespace $NAMESPACE --dry-run=client -o yaml | kubectl apply -f - || true
success "Namespace '$NAMESPACE' disponible"

# Create GCP service account secret if it exists locally
if [ -f "trading-firestore.json" ]; then
    info "Codificando credenciales GCP..."
    GCP_KEY_BASE64=$(base64 -w0 < trading-firestore.json)
    
    # Create temporary secrets file with encoded key
    kubectl create secret generic gcp-service-account-key \
        --from-file=trading-firestore.json=trading-firestore.json \
        -n $NAMESPACE \
        --dry-run=client -o yaml | kubectl apply -f - || true
    success "Secreto GCP configurado"
else
    warning "trading-firestore.json no encontrado. Asegurate de crear el secret manualmente:"
    echo "  kubectl create secret generic gcp-service-account-key \\"
    echo "    --from-file=trading-firestore.json=trading-firestore.json \\"
    echo "    -n $NAMESPACE"
fi

# Create secrets for API keys
info "Configurando secretos de aplicación..."
TELEGRAM_BOT_TOKEN=$(grep "TELEGRAM_BOT_TOKEN" .env | cut -d'=' -f2 | tr -d '\r')
FMP_API_KEY=$(grep "FMP_API_KEY" .env | cut -d'=' -f2 | tr -d '\r')
WEBHOOK_URL=$(grep "WEBHOOK_URL" .env | cut -d'=' -f2 | tr -d '\r')

if [ -z "$TELEGRAM_BOT_TOKEN" ] || [ -z "$FMP_API_KEY" ]; then
    error "No se encontraron credenciales en .env"
fi

kubectl create secret generic markettool-secrets \
    --from-literal=telegram-bot-token="$TELEGRAM_BOT_TOKEN" \
    --from-literal=fmp-api-key="$FMP_API_KEY" \
    -n $NAMESPACE \
    --dry-run=client -o yaml | kubectl apply -f -
success "Secretos de aplicación configurados"

# Apply manifests in order
info "Desplegando manifests de Kubernetes..."
for manifest in $MANIFESTS_DIR/*.yaml; do
    if [ -f "$manifest" ]; then
        filename=$(basename "$manifest")
        info "Aplicando: $filename..."
        kubectl apply -f "$manifest" -n $NAMESPACE || error "No se pudo aplicar: $filename"
    fi
done

success "Todos los manifests aplicados"

# Wait for deployment to be ready
info "Esperando que el deployment esté listo..."
kubectl rollout status deployment/markettool -n $NAMESPACE --timeout=5m || error "Deployment no se completó en tiempo"
success "Deployment completado"

# Get service info
info "Información del servicio:"
echo ""
kubectl get svc markettool -n $NAMESPACE
echo ""

# Get pod status
info "Estado de los pods:"
echo ""
kubectl get pods -n $NAMESPACE -l app=markettool
echo ""

# Get deployment info
info "Información del deployment:"
echo ""
kubectl describe deployment markettool -n $NAMESPACE | head -30
echo ""

# Show next steps
echo -e "${GREEN}════════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}✓ DESPLIEGUE COMPLETADO${NC}"
echo -e "${GREEN}════════════════════════════════════════════════════════${NC}"
echo ""
echo "Próximos pasos:"
echo "1. Verifica los logs: kubectl logs -f deployment/markettool -n $NAMESPACE"
echo "2. Accede el dashboard: gcloud container clusters describe $CLUSTER_NAME --region=$REGION"
echo "3. Configura DNS: Apunta tu dominio a la IP del LoadBalancer"
echo ""
echo "Comandos útiles:"
echo "  # Ver logs en tiempo real"
echo "  kubectl logs -f deployment/markettool -n $NAMESPACE"
echo ""
echo "  # Ejecutar comando en pod"
echo "  kubectl exec -it <pod-name> -n $NAMESPACE -- bash"
echo ""
echo "  # Escalar replicas"
echo "  kubectl scale deployment markettool --replicas=5 -n $NAMESPACE"
echo ""
echo "  # Eliminar deployment"
echo "  kubectl delete deployment markettool -n $NAMESPACE"
echo ""
