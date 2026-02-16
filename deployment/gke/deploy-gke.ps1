#Requires -Version 7.0
<#
.SYNOPSIS
    Deploy MarketTool to Google Kubernetes Engine (GKE)

.DESCRIPTION
    Automaticamente despliega MarketTool en GKE con toda la configuracion necesaria

.PARAMETER ClusterName
    Nombre del cluster GKE (default: markettool-cluster)

.PARAMETER Region
    Region de GCP (default: southamerica-west1)

.PARAMETER ProjectId
    Project ID de GCP (default: trading-449607)

.PARAMETER ValidateOnly
    Solo valida los manifests sin hacer el deploy

.EXAMPLE
    .\deploy-gke.ps1
    .\deploy-gke.ps1 -ClusterName "prod-cluster" -Region "us-central1"
    .\deploy-gke.ps1 -ValidateOnly

#>

[CmdletBinding()]
param(
    [string]$ClusterName = "markettool-cluster",
    [string]$Region = "southamerica-west1",
    [string]$ProjectId = "trading-449607",
    [switch]$ValidateOnly,
    [switch]$Force
)

$ErrorActionPreference = "Stop"

# Configuration
$NAMESPACE = "trading"
$MANIFESTS_DIR = "deployment/gke/manifests"
$ENV_FILE = ".env"

function Write-Info {
    param([string]$Message)
    Write-Host "[INFO] $Message" -ForegroundColor Blue
}

function Write-Success {
    param([string]$Message)
    Write-Host "[✓] $Message" -ForegroundColor Green
}

function Write-Error-Custom {
    param([string]$Message)
    Write-Host "[ERROR] $Message" -ForegroundColor Red
    exit 1
}

function Write-Warning-Custom {
    param([string]$Message)
    Write-Host "[⚠] $Message" -ForegroundColor Yellow
}

# Validate prerequisites
Write-Info "Verificando prerequisitos..."

$prerequisites = @("gcloud", "kubectl")
foreach ($cmd in $prerequisites) {
    if (-not (Get-Command $cmd -ErrorAction SilentlyContinue)) {
        Write-Error-Custom "$cmd no está instalado o no están en PATH"
    }
}

Write-Success "Prerequisitos validados"

# Check if manifest directory exists
if (-not (Test-Path $MANIFESTS_DIR)) {
    Write-Error-Custom "Directorio de manifests no encontrado: $MANIFESTS_DIR"
}

# Load environment variables
Write-Info "Cargando variables de configuración..."
if (-not (Test-Path $ENV_FILE)) {
    Write-Error-Custom "Archivo .env no encontrado"
}

$envVars = @{}
Get-Content $ENV_FILE | Where-Object { $_ -match "^[A-Z_]+=.+" } | ForEach-Object {
    $name, $value = $_ -split "=", 2
    $envVars[$name] = $value.Trim()
}

$TELEGRAM_BOT_TOKEN = $envVars["TELEGRAM_BOT_TOKEN"]
$FMP_API_KEY = $envVars["FMP_API_KEY"]
$WEBHOOK_URL = $envVars["WEBHOOK_URL"]

if (-not $TELEGRAM_BOT_TOKEN -or -not $FMP_API_KEY) {
    Write-Error-Custom "Credenciales incompletas en .env"
}

Write-Success "Variables de configuración cargadas"

# Only validate if -ValidateOnly switch
if ($ValidateOnly) {
    Write-Info "Validando manifests..."
    Get-ChildItem $MANIFESTS_DIR -Filter "*.yaml" | ForEach-Object {
        Write-Info "Validando: $($_.Name)"
        kubectl apply -f $_.FullName --dry-run=client -o yaml | Out-Null
        if ($LASTEXITCODE -ne 0) {
            Write-Error-Custom "Validación fallida para: $($_.Name)"
        }
    }
    Write-Success "Todos los manifests son válidos"
    exit 0
}

# GCP Authentication
Write-Info "Autenticando con Google Cloud..."
& gcloud auth application-default login | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Error-Custom "No se pudo autenticar con GCP"
}
& gcloud config set project $ProjectId
Write-Success "Autenticado con proyecto: $ProjectId"

# Get cluster credentials
Write-Info "Conectando a cluster GKE: $ClusterName ($Region)"
& gcloud container clusters get-credentials $ClusterName `
    --region=$Region `
    --project=$ProjectId | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Error-Custom "No se pudo conectar al cluster $ClusterName"
}
Write-Success "Conectado a cluster: $ClusterName"

# Verify kubectl
Write-Info "Verificando conexión a Kubernetes..."
$clusterInfo = & kubectl cluster-info 2>&1 | Select-Object -First 1
if ($clusterInfo -like "*error*" -or $clusterInfo -like "*Error*") {
    Write-Error-Custom "No se puede alcanzar el cluster"
}
Write-Success "Conexión a Kubernetes verificada"

# Create namespace
Write-Info "Asegurando namespace '$NAMESPACE'..."
& kubectl create namespace $NAMESPACE --dry-run=client -o yaml | & kubectl apply -f - | Out-Null
Write-Success "Namespace '$NAMESPACE' disponible"

# Create GCP service account secret
if (Test-Path "trading-firestore.json") {
    Write-Info "Configurando secreto de credenciales GCP..."
    & kubectl create secret generic gcp-service-account-key `
        --from-file=trading-firestore.json=trading-firestore.json `
        -n $NAMESPACE `
        --dry-run=client -o yaml | & kubectl apply -f - | Out-Null
    Write-Success "Secreto GCP configurado"
} else {
    Write-Warning-Custom "trading-firestore.json no encontrado"
    Write-Host "Crear manualmente:" -ForegroundColor Yellow
    Write-Host "  kubectl create secret generic gcp-service-account-key ``"
    Write-Host "    --from-file=trading-firestore.json=trading-firestore.json ``"
    Write-Host "    -n $NAMESPACE"
}

# Create application secrets
Write-Info "Configurando secretos de aplicación..."
& kubectl create secret generic markettool-secrets `
    --from-literal=telegram-bot-token="$TELEGRAM_BOT_TOKEN" `
    --from-literal=fmp-api-key="$FMP_API_KEY" `
    -n $NAMESPACE `
    --dry-run=client -o yaml | & kubectl apply -f - | Out-Null
Write-Success "Secretos de aplicación configurados"

# Apply manifests
Write-Info "Desplegando manifests de Kubernetes..."
$manifests = Get-ChildItem $MANIFESTS_DIR -Filter "*.yaml" | Sort-Object Name
foreach ($manifest in $manifests) {
    Write-Info "Aplicando: $($manifest.Name)"
    & kubectl apply -f $manifest.FullName -n $NAMESPACE | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Error-Custom "Error aplicando: $($manifest.Name)"
    }
}
Write-Success "Todos los manifests aplicados"

# Wait for deployment
Write-Info "Esperando que el deployment esté listo (timeout: 5 minutos)..."
$timeout = 0
$maxTimeout = 300
$ready = $false

while ($timeout -lt $maxTimeout) {
    $status = & kubectl get deployment markettool -n $NAMESPACE -o jsonpath='{.status.readyReplicas}/{.status.replicas}' 2>$null
    if ($status -match "(\d+)/(\d+)" -and $matches[1] -eq $matches[2]) {
        $ready = $true
        break
    }
    Start-Sleep -Seconds 5
    $timeout += 5
    Write-Host -NoNewline "."
}

Write-Host ""

if ($ready) {
    Write-Success "Deployment completado y listo"
} else {
    Write-Warning-Custom "Deployment no estuvo completamente listo después de 5 minutos"
    Write-Warning-Custom "Verifica con: kubectl rollout status deployment/markettool -n $NAMESPACE"
}

# Show deployment info
Write-Info "Información del servicio:"
Write-Host ""
& kubectl get svc markettool -n $NAMESPACE
Write-Host ""

Write-Info "Estado de los pods:"
Write-Host ""
& kubectl get pods -n $NAMESPACE -l app=markettool
Write-Host ""

Write-Info "Información del deployment:"
Write-Host ""
& kubectl describe deployment markettool -n $NAMESPACE | Select-Object -First 30
Write-Host ""

# Summary
Write-Host "════════════════════════════════════════════════════════" -ForegroundColor Green
Write-Host "✓ DESPLIEGUE COMPLETADO" -ForegroundColor Green
Write-Host "════════════════════════════════════════════════════════" -ForegroundColor Green
Write-Host ""
Write-Host "Próximos pasos:" -ForegroundColor Yellow
Write-Host "1. Verifica los logs:"
Write-Host "   kubectl logs -f deployment/markettool -n $NAMESPACE"
Write-Host ""
Write-Host "2. Obtén la IP externa (puede tardar unos minutos):"
Write-Host "   kubectl get svc markettool -n $NAMESPACE -w"
Write-Host ""
Write-Host "3. Configura DNS apuntando al LoadBalancer"
Write-Host ""
Write-Host "Comandos útiles:" -ForegroundColor Yellow
Write-Host "  # Ver logs en tiempo real"
Write-Host "  kubectl logs -f deployment/markettool -n $NAMESPACE"
Write-Host ""
Write-Host "  # Escalar replicas"
Write-Host "  kubectl scale deployment markettool --replicas=5 -n $NAMESPACE"
Write-Host ""
Write-Host "  # Eliminar deployment"
Write-Host "  kubectl delete namespace $NAMESPACE"
Write-Host ""

Write-Success "Script completado exitosamente"
