# 📁 Estructura del Proyecto MarketTool

## 🎯 Resumen Ejecutivo

**MarketTool** está organizado en **5 capas principales**:

```
marketTool/
├── 📚 docs/              ← Documentación temática
├── 🔧 config/            ← Configuración
├── 🤖 models/            ← Modelos ML
├── 📊 data/              ← Datos y logs
├── 📦 setup-files/       ← Setup
├── 💻 markettool/        ← Código fuente (Core)
├── MarketTool.py         ← Entry point legacy
└── 📝 Archivos esenciales (Dockerfile, requirements, .env, etc)
```

---

## 📁 Estructura Detallada

### 1️⃣ **RAÍZ (Esenciales para Docker Deployment)**

```
marketTool/
├── MarketTool.py ✅             # Entry point (legacy entry)
├── Dockerfile ✅                # Build image (Docker)
├── docker-compose.yml ✅        # Compose config
├── requirements.txt ✅          # Python dependencies
├── .env ✅                      # Ambiente actual (⚠️ NO COMMITAR)
├── .env.example ❌              # Ahora en config/
├── .ultralytics.yaml ⚠️         # Config YOLO (necesario para Docker)
├── patrones.pt ⚠️              # Modelo PG (necesario para Docker)
├── ruido.pt ⚠️                 # Modelo Ruido (necesario para Docker)
├── README.md ✅                # Documentación principal
├── .git ⚠️                     # Control de versiones
├── .gitignore ✅               # Ignora .env, *.pt, etc
└── .dockerignore ✅            # Ignora archivos en Docker build
```

**⚠️ Nota sobre archivos críticos para Docker:**
- `patrones.pt` y `ruido.pt` **DEBEN estar en raíz** (Dockerfile línea 8)
- `.env` se copia automáticamente en `COPY . .` (línea 31)
- `.ultralytics.yaml` necesario para modelos YOLO

---

### 2️⃣ **docs/ (Documentación)**

```
docs/
├── README.md                    # Índice maestro
├── ESTRUCTURA_PROYECTO.md       # Este archivo
├── ARIMA/
│   ├── ARIMA_CONFIG.md
│   ├── ARIMA_IMPLEMENTATION.md
│   └── ARIMA_TIMEOUT_UNIFIED.md
│
├── architecture/                # Diseño del sistema
│   ├── ARQUITECTURA_HEXAGONAL.md
│   ├── ANALYSIS_FLOW_AUDIT.md
│   ├── PARALELISMO_AUDIT_COMPLETO.md
│   └── ... (15+ archivos)
│
├── guides/                      # Guías prácticas
│   ├── DEPLOYMENT_STEPS.md
│   ├── QUICK_START_PERFORMANCE.md
│   └── ... (10+ archivos)
│
├── optimization/                # Performance
│   ├── OPTIMIZATION_PERFORMANCE.md
│   └── ... (15+ archivos)
│
├── legacy/                      # Archivos históricos
└── phases/                      # Fases de desarrollo
```

---

### 3️⃣ **config/ (Configuración)**

```
config/
├── .env.example                 # Template de .env (✅ COMMITAR)
├── pytest.ini                   # Configuración pytest
└── san.cnf                      # Certificados SSL
```

**Cómo usar:**
```bash
# Primero: Copiar template
cp config/.env.example .env

# Luego: Editar .env con tus valores
# ⚠️ NUNCA commitar .env a git
```

---

### 4️⃣ **models/ (Machine Learning)**

```
models/
├── patrones.pt                  # Modelo Pattern Recognition
├── patrones_con_confirmacion.pt # Versión con confirmación
├── patrones_sin_confirmacion.pt # Versión sin confirmación
└── ruido.pt                     # Modelo Ruido/Volatilidad
```

**Nota:** Los archivos `patrones.pt` y `ruido.pt` se **copian a raíz** para Docker.

---

### 5️⃣ **data/ (Datos y Logs)**

```
data/
├── test_results.txt
├── build.log
├── app_logs.txt
├── palabras_clave_categoria.json
└── ... (archivos de datos auxiliares)
```

---

### 6️⃣ **setup-files/ (Setup & Documentación de Instalación)**

```
setup-files/
├── ManualMT.pdf                 # Manual del usuario
├── utc_only.patch               # Patches a aplicar
└── comandos.txt                 # Comandos útiles
```

---

### 7️⃣ **markettool/ (Código Fuente - Arquitectura Hexagonal)**

```
markettool/
├── bootstrap.py                 # Punto de entrada principal
├── core/
│   ├── config.py               # Carga de env vars
│   ├── env_validation.py       # Validación de entorno
│   └── shutdown.py             # Graceful shutdown
│
├── domain/                      # Lógica de negocio
│   ├── analysis/
│   │   ├── parallel_engine.py
│   │   └── signal_processor.py
│   └── models/
│       └── trading_signals.py
│
├── application/                 # Casos de uso
│   ├── use_cases/
│   │   ├── parallel_analysis.py
│   │   └── symbol_analysis.py
│   └── services/
│       └── cache_service.py
│
├── infra/                       # Infraestructura
│   ├── http/
│   │   └── session.py
│   ├── fmp.py                  # Cliente FMP
│   └── gcs.py                  # Google Cloud Storage
│
└── interfaces/                  # API & Schedulers
    ├── api/
    │   ├── route_factory.py
    │   └── health.py
    ├── scheduler/
    │   └── bot_init.py
    └── containers.py            # Dependency Injection
```

---

### 8️⃣ **Carpetas Legacy (No tocar)**

```
backup/        # Backups antiguos
historicos/    # Datos históricos
indicators/    # Indicadores calculados cache
forex_news/    # Noticias forex cache
app/           # Legacy app data
```

---

## 🔄 Flujo de Startup

```
1. Docker COPY . .
   └─> MarketTool.py + markettool/ + .env + patrones.pt + ruido.pt

2. bootstrap.py inicia
   ├─> early_load_env()
   │   └─> load_dotenv(".env") ← Lee variables de ambiente
   ├─> load_config()
   │   └─> Instancia AppConfig con os.environ.get()
   ├─> Di Container + Dependency Injection
   ├─> ParallelAnalysisEngine
   └─> API Routes

3. MarketTool.py legacy
   └─> Puede ejecutarse como:
       - python MarketTool.py (standalone)
       - importado como módulo
```

---

## 📋 Checklist para Setup

### 1. Clonar repo
```bash
git clone <repo>
cd marketTool
```

### 2. Setup .env
```bash
# Copiar template
cp config/.env.example .env

# Editar con tus credenciales
nano .env  # o VS Code
```

**Variables críticas en .env:**
```env
# API Keys
FMP_API_KEY=sk_...
FMP_PLAN=premium

# ARIMA Configuration
ARIMA_MODE=standard          # standard|aggressive|unlimited
ARIMA_TIMEOUT=45            # segundos

# Timeouts de paralelismo
PARALLEL_TIMEOUT_TF=10       # por timeframe
PARALLEL_GLOBAL_TIMEOUT=300  # total

# Cache
INDICATORS_CACHE_ENABLED=true
INDICATORS_CACHE_TTL_HOURS=4

# Concurrencia
ANALYSIS_PER_SYMBOL_CONCURRENCY=8
```

### 3. Build Docker (local)
```bash
docker build -t markettool:latest .
```

### 4. Uso en localNginx_Balancer
El `docker-compose.yaml` en maquina-a/ carga:
```yaml
env_file:
  - .env  # Lee del .env en maquina-a/
```

---

## ⚠️ Errores Comunes y Soluciones

### ❌ Error: "patrones.pt not found"
**Causa:** Docker espera `patrones.pt` en raíz, pero está en `models/`
```bash
# Solución:
cp models/patrones.pt .
cp models/ruido.pt .
```

### ❌ Error: "ARIMA timeout"
**Causa:** `.env` no cargado, usando defaults
```bash
# Verifica que .env existe en raíz:
ls -la .env
# Verifica variables:
grep ARIMA .env
```

### ❌ Error: "FMP_API_KEY not set"
**Causa:** `.env` no contiene la variable
```bash
echo "FMP_API_KEY=sk_xxx" >> .env
```

---

## 🎯 Variables de Entorno Principales

| Variable | Default | Valor en Producción | Descripción |
|----------|---------|------------------|-------------|
| `FMP_API_KEY` | "" | sk_xxx... | API key de FMP |
| `ARIMA_MODE` | "standard" | standard | Modo ARIMA (barras) |
| `ARIMA_TIMEOUT` | 45 | 45 | Timeout ARIMA (segundos) |
| `PARALLEL_TIMEOUT_TF` | 10 | 10 | Timeout por timeframe |
| `PARALLEL_GLOBAL_TIMEOUT` | 300 | 300 | Timeout global (segundos) |
| `INDICATORS_CACHE_ENABLED` | true | true | Cachear indicadores |
| `ANALYSIS_PER_SYMBOL_CONCURRENCY` | 8 | 8 | Workers por símbolo |
| `LOG_LEVEL` | INFO | INFO | Nivel de logging |

---

## 🚀 Deployment

### Para desarrollo local:
```bash
# Terminal 1: Run MarketTool.py
python MarketTool.py

# Terminal 2: Monitoring
tail -f logs/markettool.log
```

### Para Docker:
```bash
# Build
docker build -t markettool:latest .

# Run
docker run -p 8080:8080 --env-file .env markettool:latest
```

### Para Kubernetes (GKE):
```bash
# Deploy con localNginx_Balancer/maquina-a
cd ../localNginx_Balancer/maquina-a
docker-compose up -d
```

---

## 📊 Resumen de Cambios Recientes

| Cambio | Antes | Después | Impacto |
|--------|-------|---------|---------|
| Ubicación de .env | Raíz | Raíz (template en config/) | ✅ Docker compatible |
| Ubicación de .pt | Raíz | Raíz (copia desde models/) | ✅ Docker compatible |
| Docs en raíz | 80+ .md | todos en docs/ | ✅ Limpio |
| .gitignore | No incluye .env | ✅ Incluye .env | ✅ Seguro |

---

## 🔗 Referencias Rápidas

| Quiero... | Ir a... |
|-----------|---------|
| Aprender ARIMA | [docs/ARIMA/README.md](ARIMA/README.md) |
| Entender arquitectura | [docs/architecture/ARQUITECTURA_HEXAGONAL.md](architecture/ARQUITECTURA_HEXAGONAL.md) |
| Setup producción | [docs/guides/DEPLOYMENT_STEPS.md](guides/DEPLOYMENT_STEPS.md) |
| Optimizar performance | [docs/optimization/OPTIMIZATION_PERFORMANCE.md](optimization/OPTIMIZATION_PERFORMANCE.md) |
| Ver paralelismo | [docs/architecture/PARALELISMO_AUDIT_COMPLETO.md](architecture/PARALELISMO_AUDIT_COMPLETO.md) |

---

**Status:** ✅ ESTRUCTURADO  
**Última actualización:** 2026-02-18  
**Responsable:** DevOps Team  
