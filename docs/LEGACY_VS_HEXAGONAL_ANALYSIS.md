# 🔍 Análisis: Legacy vs Hexagonal Architecture

## 📊 Resumen Ejecutivo

**Estado Actual**: Bootstrap.py está importando **16 componentes legacy** desde MarketTool.py cuando **ya existen alternativas hexagonales** para al menos **4 de ellos**.

---

## 🚨 Componentes NO Hexagonales en Bootstrap.py

### ✅ YA MIGRADOS (Recientemente)
1. ✅ **`fmp`** → Migrado a `FMPClient` (hexagonal)
2. ✅ **`programar_actualizacion_menus`** → Migrado a `setup_scheduler` (hexagonal)

### ⚠️ TIENEN ALTERNATIVA HEXAGONAL (Deberían migrarse)

#### 1. **APP_CONFIG** (Config)
- **Actual**: `from MarketTool import APP_CONFIG`
- **Hexagonal**: `from markettool.core.config import load_config`
- **Acción**: Crear directamente en bootstrap.py
```python
# En vez de importar desde MarketTool
from markettool.core.config import load_config
APP_CONFIG = load_config()
```

#### 2. **HTTP_SESSION** (HTTP Client)
- **Actual**: `from MarketTool import HTTP_SESSION`
- **Hexagonal**: `from markettool.infra.http.session import build_session`
- **Acción**: Crear directamente en bootstrap.py
```python
from markettool.infra.http.session import build_session
HTTP_SESSION = build_session(
    retries=APP_CONFIG.http_retries,
    backoff=APP_CONFIG.http_backoff
)
```

#### 3. **warmup_cache_all_assets** (Cache Warmup)
- **Actual**: `from MarketTool import warmup_cache_all_assets` (función legacy)
- **Hexagonal**: `WarmCacheUseCase` (markettool/application/use_cases/warm_cache.py)
- **Acción**: Usar `container.warm_cache.execute_full_warmup()`
```python
# En bot_init.py, en vez de:
await warmup_cache_all_assets()

# Usar hexagonal:
result = await container.warm_cache.execute_full_warmup()
logger.info(f"Cache warmup: {result['success_count']}/{result['total_count']}")
```

#### 4. **db (firestore_db)** y **storage** (GCS Client)
- **Actual**: `from MarketTool import db, storage` → luego pasan a DIContainer
- **Problema**: Bootstrap.py importa clientes legacy solo para pasarlos al container
- **Acción**: Crear clientes en bootstrap.py o usar lazy initialization en container
```python
# Opción 1: Crear en bootstrap (mejor control)
from google.cloud import firestore, storage as gcs_storage
firestore_db = firestore.Client()
gcs_client = gcs_storage.Client()

# Opción 2: Lazy init en DIContainer (más hexagonal)
# El container crea los clientes internamente cuando se necesitan
```

---

### 🔴 NO TIENEN ALTERNATIVA HEXAGONAL (Legacy puro)

Estas funciones son **específicas del bot de Telegram** y cargan datos de Firestore para el sistema legacy. **No hay equivalente hexagonal porque son parte del dominio legacy**.

#### Funciones de Carga de Datos Telegram:
1. **`cargar_datos_subscription_user`** - Carga usuarios suscritos desde Firestore
2. **`cargar_datos_subscription_type`** - Carga tipos de suscripción
3. **`cargar_chat_ids`** - Carga IDs de chats desde Firestore
4. **`cargar_admin_ids`** - Carga IDs de administradores

#### Funciones de Carga/Guardado de Datos de Mercado:
5. **`cargar_noticias_en_memoria`** - Carga noticias de Forex en memoria
6. **`cargar_datos_historicos_inicial`** - Carga históricos iniciales (warmup legacy)
7. **`guardar_noticias_forex_diarias`** - Job: Guarda noticias diarias
8. **`guardar_datos_historicos_diarios`** - Job: Guarda históricos diarios

#### Funciones del Scheduler:
9. **`actualizar_menus`** - Job: Actualiza menús de Telegram cada 10 minutos

#### Aplicaciones:
10. **`asgi_app`** - Flask app (wrapper ASGI)
11. **`application`** - Telegram bot application

#### Otros:
12. **`scheduler`** - BackgroundScheduler (APScheduler) - **YA SE USA EN HEXAGONAL**
13. **`_POD_COORDINATOR`** - Coordinador de pods (leader election)

#### Cache Metrics (para health endpoints):
14. **`_warmup_start_time`** - Timestamp inicio warmup
15. **`_warmup_end_time`** - Timestamp fin warmup
16. **`_niveles_cache_hits/misses`** - Métricas de cache niveles S/R
17. **`_atr_cache_hits/misses`** - Métricas de cache ATR

---

## 🎯 Plan de Migración Recomendado

### FASE 1: Migraciones Rápidas (1-2 horas)
**Prioridad: ALTA** - Son simples y eliminan dependencias innecesarias

#### 1.1. Migrar APP_CONFIG
```python
# bootstrap.py
from markettool.core.config import load_config

# En vez de:
# from MarketTool import APP_CONFIG

# Hacer:
APP_CONFIG = load_config()
```

#### 1.2. Migrar HTTP_SESSION
```python
# bootstrap.py
from markettool.infra.http.session import build_session

# En vez de:
# from MarketTool import HTTP_SESSION

# Hacer:
HTTP_SESSION = build_session(
    retries=APP_CONFIG.http_retries,
    backoff=APP_CONFIG.http_backoff
)
```

#### 1.3. Migrar warmup_cache_all_assets
```python
# markettool/interfaces/scheduler/bot_init.py
async def setup_scheduler(...):
    # En vez de:
    # await warmup_cache_all_assets()
    
    # Hacer:
    if app_config.cache_warmup_enabled:
        result = await container.warm_cache.execute_full_warmup()
        logger.info(f"✅ Cache warmup: {result['success_count']}/{result['total_count']}")
```

---

### FASE 2: Crear Factories para Clientes (2-3 horas)
**Prioridad: MEDIA** - Desacopla clientes de MarketTool.py

#### 2.1. Crear markettool/infra/firestore/client.py
```python
"""Firestore client factory."""
from google.cloud import firestore
import os

def create_firestore_client() -> firestore.Client:
    """Create Firestore client with credentials from env."""
    credentials_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if credentials_path:
        return firestore.Client.from_service_account_json(credentials_path)
    return firestore.Client()
```

#### 2.2. Crear markettool/infra/storage/gcs_client.py
```python
"""GCS client factory."""
from google.cloud import storage
import os

def create_gcs_client() -> storage.Client:
    """Create GCS client with credentials from env."""
    credentials_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if credentials_path:
        return storage.Client.from_service_account_json(credentials_path)
    return storage.Client()
```

#### 2.3. Actualizar bootstrap.py
```python
from markettool.infra.firestore.client import create_firestore_client
from markettool.infra.storage.gcs_client import create_gcs_client

# En vez de:
# from MarketTool import db, storage

# Hacer:
firestore_db = create_firestore_client()
gcs_client = create_gcs_client()
```

---

### FASE 3: Crear Use Cases para Datos Telegram (5-8 horas)
**Prioridad: BAJA** - Son funciones legacy del bot, no afectan la arquitectura hexagonal

Estas funciones están fuertemente acopladas al sistema legacy de Telegram. Migrarlas requeriría:

1. Crear repositorios hexagonales para cada entidad:
   - `SubscriptionUserRepository`
   - `SubscriptionTypeRepository`
   - `ChatIDRepository`
   - `AdminRepository`

2. Crear use cases de carga:
   - `LoadSubscriptionDataUseCase`
   - `LoadChatIDsUseCase`
   - `LoadNewsUseCase`

**RECOMENDACIÓN**: Postergar estas migraciones hasta que el bot de Telegram se refactorice completamente.

---

### FASE 4: Migrar Scheduler Jobs (3-4 horas)
**Prioridad: MEDIA** - Algunos jobs ya tienen equivalentes hexagonales

#### 4.1. actualizar_menus
- **Estado**: Es legacy puro, actualiza menús del bot Telegram
- **Acción**: Dejar en legacy hasta refactor completo del bot

#### 4.2. guardar_noticias_forex_diarias
- **Posible Hexagonal**: Podría ser un `SaveNewsUseCase`
- **Beneficio**: Reutilizable fuera del contexto del bot

#### 4.3. guardar_datos_historicos_diarios
- **Posible Hexagonal**: Podría ser un `SaveHistoricalDataUseCase`
- **Beneficio**: Ya existe `HistoricosRepository.save_historico()` en hexagonal

---

## 📊 Métricas de Integración

### Imports desde MarketTool.py en Bootstrap.py:

| Componente | Tipo | Estado Hexagonal | Prioridad Migración |
|-----------|------|------------------|---------------------|
| `APP_CONFIG` | Config | ✅ Existe (`load_config`) | 🔴 ALTA |
| `HTTP_SESSION` | HTTP Client | ✅ Existe (`build_session`) | 🔴 ALTA |
| `fmp` | API Client | ✅ Migrado | ✅ COMPLETO |
| `warmup_cache_all_assets` | Cache | ✅ Existe (`WarmCacheUseCase`) | 🔴 ALTA |
| `db` (firestore) | Database | ⚠️ Debe crearse factory | 🟡 MEDIA |
| `storage` (GCS) | Storage | ⚠️ Debe crearse factory | 🟡 MEDIA |
| `scheduler` | Scheduler | ✅ Ya es hexagonal | ✅ OK |
| `cargar_datos_*` (4 funcs) | Telegram Data | ❌ Legacy puro | 🟢 BAJA |
| `cargar_noticias_*` | Market Data | ❌ Legacy puro | 🟢 BAJA |
| `guardar_*` (2 funcs) | Persistence | ⚠️ Podría ser hexagonal | 🟡 MEDIA |
| `actualizar_menus` | Telegram | ❌ Legacy puro | 🟢 BAJA |
| `asgi_app` | Flask App | ❌ Legacy necesario | ✅ OK |
| `application` | Telegram Bot | ❌ Legacy necesario | ✅ OK |
| `_POD_COORDINATOR` | Infrastructure | ❌ Legacy necesario | ✅ OK |
| Cache metrics (6 vars) | Monitoring | ❌ Legacy necesario | ✅ OK |

**Total**: 21 imports  
**Migrados**: 2 (10%)  
**Pueden migrarse ahora**: 4 (19%)  
**Requieren factories**: 2 (10%)  
**Legacy aceptable**: 13 (61%)

---

## ✅ Checklist de Acciones Inmediatas

### Quick Wins (Hacer YA):
- [ ] Migrar `APP_CONFIG` a `load_config()` en bootstrap.py
- [ ] Migrar `HTTP_SESSION` a `build_session()` en bootstrap.py
- [ ] Reemplazar `warmup_cache_all_assets()` por `container.warm_cache.execute_full_warmup()`

### Mediano Plazo (Esta semana):
- [ ] Crear `markettool/infra/firestore/client.py` con factory
- [ ] Crear `markettool/infra/storage/gcs_client.py` con factory
- [ ] Actualizar bootstrap.py para usar los factories

### Largo Plazo (Cuando se refactorice bot):
- [ ] Crear repositorios hexagonales para datos de Telegram
- [ ] Crear use cases para carga/guardado de datos
- [ ] Migrar scheduler jobs a hexagonal

---

## 🎯 Conclusión

**De 21 imports desde MarketTool.py:**
- ✅ **2 ya migrados** a hexagonal (fmp, programar_actualizacion_menus)
- 🔴 **4 deberían migrarse YA** (APP_CONFIG, HTTP_SESSION, warmup_cache_all_assets + clientes)
- 🟡 **2 necesitan factories** (db, storage)
- ✅ **13 son legacy aceptable** (funciones de bot, apps, métricas)

**Impacto de migrar los 4 prioritarios:**
- ✅ Elimina dependencias circulares CONFIG/HTTP
- ✅ Usa la versión hexagonal del cache warmup
- ✅ Bootstrap.py se vuelve más independiente
- ✅ Mejora testabilidad (factories mockeables)

**Próximo Paso Sugerido**: Implementar FASE 1 (migraciones rápidas) - toma 1-2 horas y elimina 4 dependencias legacy.
