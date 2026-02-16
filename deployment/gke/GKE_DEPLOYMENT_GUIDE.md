📋 GKE Deployment Guide - MarketTool
====================================

## ¿Qué hay aquí?

Esta carpeta contiene toda la configuración necesaria para desplegar MarketTool en Google Kubernetes Engine (GKE).

```
deployment/gke/
├── manifests/              # Archivos YAML de Kubernetes
│   ├── 00-namespace.yaml  # Namespace "trading"
│   ├── 01-configmap.yaml  # Configuración de la aplicación
│   ├── 02-deployment.yaml # Deployment con 3 replicas
│   ├── 03-service.yaml    # LoadBalancer service
│   ├── 04-hpa.yaml        # Horizontal Pod Autoscaler
│   ├── 05-ingress.yaml    # Ingress + SSL/TLS
│   ├── 06-rbac.yaml       # Service Account y permisos
│   └── 07-secrets.yaml    # Secretos (credenciales)
├── deploy-gke.ps1         # Script de deploy (PowerShell - Windows)
└── deploy-gke.sh          # Script de deploy (Bash - Linux/Mac)
```

## OPCIÓN 1: Desplegar Automáticamente (RECOMENDADO)

### Requisitos:
- `gcloud` CLI instalado: https://cloud.google.com/sdk/docs/install
- `kubectl` instalado: https://kubernetes.io/docs/tasks/tools/
- Cluster GKE ya creado en tu proyecto
- Acceso a GCP con permisos de admin

### Paso 1: Preparar credenciales

Asegurate de tener el archivo `trading-firestore.json` en la raíz del proyecto:
```bash
# Si no lo tienes, descárgalo desde GCP
gcloud iam service-accounts keys create trading-firestore.json \
  --iam-account=trading@trading-449607.iam.gserviceaccount.com
```

### Paso 2: Actualizar .env con credenciales

Asegurate que `.env` tenga:
```
TELEGRAM_BOT_TOKEN=7820894723:AAFRp-hzu3aLoZ45...
FMP_API_KEY=NRnN8Z4yUQOuqvESjIL8Vny97uLk4G9n
WEBHOOK_URL=tu-dominio-o-ip.com
```

### Paso 3: Ejecutar el script de deploy

**En Windows (PowerShell):**
```powershell
cd deployment/gke
.\deploy-gke.ps1
```

**Parámetros opcionales:**
```powershell
.\deploy-gke.ps1 -ClusterName "my-cluster" -Region "us-central1" -ProjectId "my-project"
.\deploy-gke.ps1 -ValidateOnly  # Solo valida sin desplegar
```

**En Linux/Mac:**
```bash
cd deployment/gke
chmod +x deploy-gke.sh
./deploy-gke.sh
# O con parámetros:
./deploy-gke.sh my-cluster us-central1 my-project
```

### Paso 4: Monitorear el deployment

```bash
# Ver estado de los pods
kubectl get pods -n trading

# Ver logs en tiempo real
kubectl logs -f deployment/markettool -n trading

# Esperar a que esté listo
kubectl rollout status deployment/markettool -n trading

# Obtener IP externa del LoadBalancer
kubectl get svc markettool -n trading
```

## OPCIÓN 2: Desplegar Manualmente

Si prefieres hacer el deploy paso a paso:

### Paso 1: Conectar a cluster
```bash
gcloud container clusters get-credentials markettool-cluster \
  --region=southamerica-west1 \
  --project=trading-449607
```

### Paso 2: Crear namespace y secretos
```bash
kubectl create namespace trading

# Secretos de aplicación
kubectl create secret generic markettool-secrets \
  --from-literal=telegram-bot-token="YOUR_TOKEN" \
  --from-literal=fmp-api-key="YOUR_API_KEY" \
  -n trading

# Credenciales GCP
kubectl create secret generic gcp-service-account-key \
  --from-file=trading-firestore.json=trading-firestore.json \
  -n trading
```

### Paso 3: Aplicar manifests en orden
```bash
cd deployment/gke/manifests
kubectl apply -f 00-namespace.yaml
kubectl apply -f 01-configmap.yaml
kubectl apply -f 02-deployment.yaml
kubectl apply -f 03-service.yaml
kubectl apply -f 04-hpa.yaml
kubectl apply -f 05-ingress.yaml
kubectl apply -f 06-rbac.yaml
kubectl apply -f 07-secrets.yaml
```

## Configuración Actualizada

Todos los manifests incluyen la configuración optimizada de paralelismo:

- **ANALYSIS_MAX_WORKERS**: 160 (fue 64 antes de optimización)
- **PARALLEL_MAX_CONCURRENT_ASSETS**: 18 (fue 8)
- **PARALLEL_TIMEFRAME_FANOUT**: 7 (fue 4)
- **PARALLEL_TIMEOUT_PREDICTION_ARIMA**: 7s
- **PARALLEL_TIMEOUT_PREDICTION_MC**: 3s

Ver `01-configmap.yaml` para todos los parámetros.

## Cambios desde la versión anterior

**Antes (backup):**
- 4 replicas
- 900m CPU limite, 3000Mi memory
- Namespace: default

**Ahora:**
- 3 replicas + HPA (escala automáticamente 3-6)
- 1200m CPU limite, 3500Mi memory
- Namespace: trading (aislado)
- Health checks mejorados
- Security context
- Pod anti-affinity para alta disponibilidad
- ConfigMap centralizado para configuración

## Verificar Deployment

### Ver estado general
```bash
kubectl get all -n trading
```

### Ver eventos recientes
```bash
kubectl get events -n trading --sort-by='.lastTimestamp'
```

### Inspect pod específico
```bash
kubectl describe pod <pod-name> -n trading
```

### Ejecutar comando en pod
```bash
kubectl exec -it <pod-name> -n trading -- bash
```

### Ver recursos utilizados
```bash
kubectl top pods -n trading
kubectl top nodes
```

## Escalar Replicas

### Escalar manualmente
```bash
kubectl scale deployment markettool --replicas=5 -n trading
```

### Ver HPA status
```bash
kubectl get hpa -n trading
kubectl describe hpa markettool-hpa -n trading
```

## Troubleshooting

### Pod no inicia
```bash
kubectl logs <pod-name> -n trading --previous
kubectl describe pod <pod-name> -n trading
```

### Imagen no encontrada
```bash
kubectl get pods -n trading -o jsonpath='{.items[0].status}'
# Verificar que la imagen existe en Artifact Registry
gcloud container images list --repository=southamerica-west1-docker.pkg.dev/trading-449607/trading-repo
```

### Memória/CPU insuficiente
```bash
# Ver nodos disponibles
kubectl get nodes
kubectl top nodes

# O escalar cluster
gcloud container clusters resize markettool-cluster --num-nodes 5 --region southamerica-west1
```

### Eliminar deployment
```bash
kubectl delete namespace trading
```

## Rolling Update

Para actualizar la imagen sin downtime:

```bash
# Actualizar imagen
kubectl set image deployment/markettool \
  markettool=southamerica-west1-docker.pkg.dev/trading-449607/trading-repo/markettool:v2 \
  -n trading

# Ver progreso
kubectl rollout status deployment/markettool -n trading

# Reverts si hay problemas
kubectl rollout undo deployment/markettool -n trading
```

## Configuración de DNS

Después de que el LoadBalancer tenga IP externa:

```bash
kubectl get svc markettool -n trading -w
# Copiar la IP EXTERNAL-IP y configurar en tu DNS:
# trading.libertex.dev  A  <EXTERNAL-IP>
# api.trading.libertex.dev  A  <EXTERNAL-IP>
```

## Monitoreo Continuo

### Crear ServiceMonitor para Prometheus (opcional)
```yaml
apiVersion: monitoring.coreos.com/v1
kind: ServiceMonitor
metadata:
  name: markettool-monitor
  namespace: trading
spec:
  selector:
    matchLabels:
      app: markettool
  endpoints:
  - port: http
    interval: 30s
```

## Backup

Exportar configuración actual:
```bash
kubectl get all -n trading -o yaml > backup-trading-$(date +%Y%m%d).yaml
```

## Soporte

- Kubernetes Dashboard: `gcloud container clusters describe markettool-cluster --region=southamerica-west1 | grep dasboardUri`
- Logs: `kubectl logs -f deployment/markettool -n trading`
- Eventos: `kubectl get events -n trading -w`
