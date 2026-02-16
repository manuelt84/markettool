# MarketTool GKE Deployment

Estructura completa para desplegar MarketTool en Google Kubernetes Engine (GKE) con configuración optimizada.

## 📁 Estructura

```
deployment/gke/
├── manifests/                    # Archivos YAML de Kubernetes (en orden)
│   ├── 00-namespace.yaml        # Namespace "trading"
│   ├── 01-configmap.yaml        # ConfigMap con configuración de paralelismo
│   ├── 02-deployment.yaml       # Deployment (3 replicas + pod disruption budgets)
│   ├── 03-service.yaml          # LoadBalancer Service
│   ├── 04-hpa.yaml              # Horizontal Pod Autoscaler (3-6 replicas)
│   ├── 05-ingress.yaml          # Ingress con SSL/TLS + ManagedCertificate
│   ├── 06-rbac.yaml             # ServiceAccount + ClusterRole
│   └── 07-secrets.yaml          # Secretos (credenciales)
│
├── deploy-gke.ps1               # Script de deploy (Windows/PowerShell)
├── deploy-gke.sh                # Script de deploy (Linux/Mac/Bash)
├── GKE_DEPLOYMENT_GUIDE.md      # Guía completa de deployment
└── README.md                     # Este archivo
```

## 🚀 Inicio Rápido

### Opción 1: Deploy Automático (5 minutos)

**Windows (PowerShell):**
```powershell
.\deploy-gke.ps1
```

**Linux/Mac (Bash):**
```bash
chmod +x deploy-gke.sh
./deploy-gke.sh
```

### Opción 2: Validar sin desplegar

```powershell
# Windows
.\deploy-gke.ps1 -ValidateOnly

# Linux
./deploy-gke.sh --validate
```

## ✨ Características

- ✅ **Configuración Optimizada**: Todos los valores de paralelismo actualizados
- ✅ **Alta Disponibilidad**: 3 replicas mínimo, escala hasta 6 con HPA
- ✅ **Seguridad**: RBAC, Secrets, SecurityContext
- ✅ **Health Checks**: Liveness y Readiness probes configurados
- ✅ **Auto-scaling**: HPA basado en CPU y memoria
- ✅ **SSL/TLS**: ManagedCertificate integrado
- ✅ **Aislamiento**: Namespace dedicado "trading"
- ✅ **Rolling Updates**: Zero-downtime deployments

## 📊 Configuración Actualizada

| Variable | Valor | Nota |
|----------|-------|------|
| ANALYSIS_MAX_WORKERS | 160 | +150% desde baseline |
| PARALLEL_MAX_CONCURRENT_ASSETS | 18 | Multi-asset orchestration |
| PARALLEL_TIMEFRAME_FANOUT | 7 | 7 TF por activo |
| PARALLEL_TIMEOUT_PREDICTION_ARIMA | 7s | Optimizado |
| PARALLEL_TIMEOUT_PREDICTION_MC | 3s | Optimizado |

Ver `manifests/01-configmap.yaml` para configuración completa.

## 📋 Requisitos

### Instalados:
```bash
gcloud --version
kubectl version --client
```

### GCP:
- Proyecto creado: `trading-449607`
- Cluster GKE: `markettool-cluster` en `southamerica-west1`
- Artifact Registry: `southamerica-west1-docker.pkg.dev/trading-449607/trading-repo/markettool`

### Credenciales:
- `.env` con TELEGRAM_BOT_TOKEN y FMP_API_KEY
- `trading-firestore.json` (GCP service account key)

## 🔧 Scripts de Deploy

### deploy-gke.ps1 (PowerShell - Windows)

```powershell
# Deploy estándar
.\deploy-gke.ps1

# Deploy en cluster diferente
.\deploy-gke.ps1 -ClusterName "prod-cluster" -Region "us-central1"

# Solo validar
.\deploy-gke.ps1 -ValidateOnly
```

**Lo que hace:**
1. Valida que gcloud y kubectl estén instalados
2. Se autentica con GCP
3. Conecta al cluster GKE
4. Crea namespace y secretos
5. Aplica todos los manifests en orden
6. Espera a que el deployment esté listo (5 min timeout)
7. Muestra status e información de conexión

### deploy-gke.sh (Bash - Linux/Mac)

```bash
# Deploy estándar
./deploy-gke.sh

# Deploy con parámetros personalizados
./deploy-gke.sh "prod-cluster" "us-central1" "my-project"
```

**Lo que hace:** Mismo que PowerShell, pero compatible con Linux/Mac.

## 📈 Monitoreo

### Ver estado
```bash
kubectl get all -n trading
kubectl describe deployment markettool -n trading
```

### Logs en tiempo real
```bash
kubectl logs -f deployment/markettool -n trading
```

### Recursos utilizados
```bash
kubectl top pods -n trading
kubectl top nodes
```

### HPA/Auto-scaling
```bash
kubectl get hpa -n trading
kubectl describe hpa markettool-hpa -n trading
```

## 🔄 Actualizaciones

### Rolling Update de imagen
```bash
kubectl set image deployment/markettool \
  markettool=southamerica-west1-docker.pkg.dev/trading-449607/trading-repo/markettool:v2 \
  -n trading

# Monitorear
kubectl rollout status deployment/markettool -n trading
```

### Rollback
```bash
kubectl rollout undo deployment/markettool -n trading
```

## 🔐 Secretos y Configuración

### Actualizar credenciales
```bash
# Telegram token
kubectl create secret generic markettool-secrets \
  --from-literal=telegram-bot-token="NEW_TOKEN" \
  -n trading --dry-run=client -o yaml | kubectl apply -f -

# FMP API key
kubectl patch secret markettool-secrets \
  -n trading \
  -p '{"data":{"fmp-api-key":"'$(echo -n "NEW_KEY" | base64)'"}}'
```

### Actualizar ConfigMap
```bash
# Cambiar timeout de ARIMA
kubectl patch configmap markettool-config \
  -n trading \
  --type merge \
  -p '{"data":{"PARALLEL_TIMEOUT_PREDICTION_ARIMA":"8"}}'
```

## 🗑️ Limpiar

### Eliminar deployment
```bash
kubectl delete namespace trading
```

### Eliminar solo la aplicación (mantener namespace)
```bash
kubectl delete deployment markettool -n trading
```

## 📚 Documentación

- [GKE_DEPLOYMENT_GUIDE.md](GKE_DEPLOYMENT_GUIDE.md) - Guía completa con troubleshooting
- `manifests/*.yaml` - Archivos YAML comentados
- `../README.md` - Documentación general del proyecto

## 🆘 Troubleshooting

### Pod no inicia
```bash
kubectl logs <pod-name> -n trading --previous
kubectl describe pod <pod-name> -n trading
```

### Error de imagen
```bash
# Verificar que la imagen existe
gcloud container images list --repository=southamerica-west1-docker.pkg.dev/trading-449607/trading-repo
```

### Memoria/CPU insuficiente
```bash
# Escalar cluster
gcloud container clusters resize markettool-cluster --num-nodes 5
```

### Ver eventos del cluster
```bash
kubectl get events -n trading --sort-by='.lastTimestamp'
```

## 📞 Soporte

Para más información ver `GKE_DEPLOYMENT_GUIDE.md` o ejecutar:

```bash
# Dashboard GKE
gcloud container clusters describe markettool-cluster --region=southamerica-west1

# Logs y eventos
kubectl logs -f deployment/markettool -n trading
kubectl get events -n trading -w
```

---

**Version**: 2.0  
**Updated**: 2026-02-16  
**Optimizations**: Paralelismo Nivel 3 (160 workers, 18 assets, 7 TFs)
