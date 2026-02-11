# 🔍 Análisis Multi-Pod/Multi-Usuario - Problemas y Soluciones

## 📊 Resumen Ejecutivo

**Fecha:** 11 de Febrero, 2026  
**Alcance:** Revisión completa del código para compatibility multi-pod  
**Problemas Encontrados:** 15 issues críticos y de alto impacto  
**Estado Actual:** ⚠️ Código tiene múltiples dependencias de estado local

---

## 🚨 Problemas Críticos (P0 - Alta Urgencia)

### 1. **Estado de Usuario en Memoria Local** ⚠️⚠️⚠️

**Ubicación:** `MarketTool.py:1006`

```python
# ❌ PROBLEMÁTICO: Estado local no compartido entre pods
user_states = {}  # Diccionario en memoria RAM local del pod
```

**Problema:**
- Cada pod tiene su propia copia de `user_states`
- Si usuario conecta a Pod A, luego a Pod B → pierde su estado
- `soportes_resistencias_cache` del usuario se pierde entre pods
- `par_seleccionado`, `cache_realtime`, `exec_id` locales

**Impacto:**
- ❌ Usuario experimenta pérdida de contexto al cambiar de pod
- ❌ Análisis repetidos innecesarios (pierde caché calculado)
- ❌ UX inconsistente (menús muestran estados incorrectos)

**Solución:**

```python
# ✅ OPCIÓN 1: Firestore como source of truth (ya implementado parcialmente)
# mark_user_state() ya escribe a Firestore, pero lectura aún usa user_states local

def get_user_state(uuid: str) -> dict:
    """Lee estado desde Firestore con caché local de 10 segundos."""
    # Check cache local (TTL: 10s)
    cache_key = f"user_state:{uuid}"
    if cache_key in _state_cache:
        cached_at, data = _state_cache[cache_key]
        if time.time() - cached_at < 10:
            return data
    
    # Fetch from Firestore
    doc = _user_state_doc_by_uuid(uuid).get()
    if doc.exists:
        data = doc.to_dict()
        _state_cache[cache_key] = (time.time(), data)
        return data
    
    return {"estado": "disponible"}

# ✅ OPCIÓN 2: Redis (mejor performance, requiere infraestructura)
# Usar Redis con TTL automático
# redis_client.setex(f"user_state:{uuid}", 300, json.dumps(state))
```

**Prioridad:** 🔴 **P0 - Implementar en próxima iteración**

---

### 2. **Locks Locales No Funcionan Entre Pods** ⚠️⚠️

**Ubicación:** Múltiples lugares

```python
# ❌ PROBLEMÁTICO: Locks locales no coordinan entre pods
matplotlib_lock = threading.Lock()      # MarketTool.py:1014
guardar_lock = asyncio.Lock()           # MarketTool.py:1043
RUNNING_LOCK = asyncio.Lock()           # MarketTool.py:1143
ocupado_lock = threading.Lock()         # MarketTool.py:1188
_reader_lock = threading.Lock()         # MarketTool.py:1191
STOP_EVENTS_LOCK = threading.Lock()     # MarketTool.py:1146
file_locks = {}                         # MarketTool.py:1042 (dict de locks locales)
```

**Problema:**
- `matplotlib_lock`: Múltiples pods pueden generar gráficos simultáneamente (OK, pero desperdicio)
- `RUNNING_LOCK`: Protege dict local `RUNNING`, pero no coordina entre pods
- `file_locks`: Locks para archivos locales, no funcionan con GCS

**Impacto:**
- ⚠️ **Race conditions potenciales** en operaciones compartidas
- ⚠️ Posible **corrupción de datos** si múltiples pods escriben al mismo archivo GCS
- ⚠️ Desperdicio de recursos (múltiples pods procesando lo mismo)

**Solución:**

```python
# ✅ Para operaciones críticas: Usar distributed locks de Firestore
# (ya implementado en IndicatorsCache y PodLeaderCoordinator)

class DistributedLock:
    """Lock distribuido usando Firestore para coordinación multi-pod."""
    
    def __init__(self, lock_name: str, ttl_seconds: int = 60):
        self.lock_name = lock_name
        self.ttl_seconds = ttl_seconds
        self.pod_id = socket.gethostname()
        self.db = firestore.Client() if FIRESTORE_ENABLED else None
    
    async def acquire(self, timeout: int = 30) -> bool:
        """Intenta adquirir el lock."""
        if not self.db:
            return True  # Sin Firestore, permitir (fallback)
        
        doc_ref = self.db.document(f"locks/{self.lock_name}")
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            doc = doc_ref.get()
            now_utc = datetime.now(timezone.utc)
            
            if not doc.exists:
                # Intentar tomar el lock
                doc_ref.set({
                    "pod_id": self.pod_id,
                    "acquired_at": now_utc.isoformat(),
                    "ttl_seconds": self.ttl_seconds
                })
                return True
            
            # Verificar si lock expiró
            data = doc.to_dict()
            acquired_at = datetime.fromisoformat(data["acquired_at"].replace('Z', '+00:00'))
            elapsed = (now_utc - acquired_at).total_seconds()
            
            if elapsed > data.get("ttl_seconds", self.ttl_seconds):
                # Lock expirado, tomarlo
                doc_ref.set({
                    "pod_id": self.pod_id,
                    "acquired_at": now_utc.isoformat(),
                    "ttl_seconds": self.ttl_seconds,
                    "previous_owner": data.get("pod_id")
                })
                return True
            
            # Esperar y reintentar
            await asyncio.sleep(1)
        
        return False
    
    async def release(self):
        """Libera el lock."""
        if not self.db:
            return
        
        doc_ref = self.db.document(f"locks/{self.lock_name}")
        doc = doc_ref.get()
        
        if doc.exists and doc.to_dict().get("pod_id") == self.pod_id:
            doc_ref.delete()

# Uso:
async with DistributedLock("matplotlib_render"):
    # Solo 1 pod ejecuta esto a la vez
    generar_grafico()
```

**Prioridad:** 🟡 **P1 - Evaluar caso por caso**
- `matplotlib_lock`: P2 (bajo impacto, solo performance)
- `RUNNING_LOCK`: P1 (protege tracking de ejecuciones)
- `file_locks`: P0 si se usa con GCS, P2 si solo local

---

### 3. **Caché de Noticias en Memoria Local** ⚠️

**Ubicación:** `MarketTool.py:1009`

```python
# ❌ PROBLEMÁTICO: Caché no compartido entre pods
cache_noticias = {}  # defaultdict(pd.DataFrame)
```

**Problema:**
- Cada pod re-descarga las mismas noticias de FMP
- Desperdicio de cuota API de FMP
- Latencia aumentada para usuarios (cada pod cold start)

**Impacto:**
- 💰 **Costo aumentado** de API FMP (3 pods = 3x solicitudes)
- ⏱️ **Latencia aumentada** para primer usuario de cada pod
- 📊 **Inconsistencia**: Pods pueden tener versiones diferentes de noticias

**Solución:**

```python
# ✅ OPCIÓN 1: GCS como caché compartido (similar al sistema de históricos)
# forex_news/EURUSD_noticias.json ya existe, usarlo como source of truth

cache_noticias_ttl = {}  # {symbol: timestamp}
CACHE_NOTICIAS_TTL_SECONDS = 300  # 5 minutos

async def obtener_noticias_raw(symbol: str, forzar: bool = False) -> pd.DataFrame:
    """Obtiene noticias con caché GCS compartido entre pods."""
    
    # 1. Check cache local (TTL: 5 min)
    if not forzar and symbol in cache_noticias:
        cached_at = cache_noticias_ttl.get(symbol, 0)
        if time.time() - cached_at < CACHE_NOTICIAS_TTL_SECONDS:
            return cache_noticias[symbol]
    
    # 2. Check GCS (ya existe en forex_news/{symbol}_noticias.json)
    gcs_path = f"forex_news/{symbol}_noticias.json"
    if GCS_ENABLED:
        try:
            bucket = storage.Client().bucket(GCS_BUCKET_NAME)
            blob = bucket.blob(gcs_path)
            
            # Verificar metadata de última actualización
            blob.reload()
            updated_at = blob.updated
            age_seconds = (datetime.now(timezone.utc) - updated_at).total_seconds()
            
            if age_seconds < 86400:  # 24 horas
                content = blob.download_as_text()
                df = pd.read_json(StringIO(content))
                
                # Cachear localmente
                cache_noticias[symbol] = df
                cache_noticias_ttl[symbol] = time.time()
                
                logger.info(f"[Noticias] Cache hit GCS: {symbol} (age: {age_seconds:.0f}s)")
                return df
        except Exception as e:
            logger.warning(f"[Noticias] Error leyendo GCS: {e}")
    
    # 3. Fetch desde FMP (solo si GCS vacío o muy antiguo)
    df = await _fetch_noticias_desde_fmp(symbol)
    
    # 4. Guardar en GCS para otros pods
    if GCS_ENABLED and not df.empty:
        try:
            bucket = storage.Client().bucket(GCS_BUCKET_NAME)
            blob = bucket.blob(gcs_path)
            blob.upload_from_string(
                df.to_json(orient='records', date_format='iso'),
                content_type='application/json'
            )
            logger.info(f"[Noticias] Guardado en GCS: {symbol}")
        except Exception as e:
            logger.error(f"[Noticias] Error guardando GCS: {e}")
    
    # 5. Cachear localmente
    cache_noticias[symbol] = df
    cache_noticias_ttl[symbol] = time.time()
    
    return df
```

**Prioridad:** 🟡 **P1 - Implementar si costos FMP son altos**

---

### 4. **Tracking de Ejecuciones en Memoria Local** ⚠️⚠️

**Ubicación:** `MarketTool.py:1142-1146`

```python
# ❌ PROBLEMÁTICO: Solo rastrea ejecuciones del pod actual
RUNNING: Dict[str, asyncio.Task] = {}
RUNNING_LOCK = asyncio.Lock()
STOP_EVENTS: dict[str, threading.Event] = {}
STOP_EVENTS_LOCK = threading.Lock()
```

**Problema:**
- Usuario inicia análisis en Pod A (exec_id registrado en `RUNNING`)
- Usuario se reconecta a Pod B, intenta cancelar → Pod B no tiene el exec_id
- No puede cancelar ejecución (no existe en su dict local)

**Impacto:**
- ❌ **Cancelación de tareas no funciona** entre pods
- ⚠️ **Tracking inconsistente** de ejecuciones activas
- 😡 **UX frustrante**: "No se encontró ejecución con ese ID"

**Solución:**

```python
# ✅ Usar Firestore para tracking global de ejecuciones
# (Ya existe parcialmente en Collection: ejecuciones)

async def registrar_ejecucion_global(exec_id: str, user_id: str, pod_id: str, tipo: str):
    """Registra ejecución en Firestore para tracking multi-pod."""
    db.collection("ejecuciones").document(exec_id).set({
        "exec_id": exec_id,
        "user_id": user_id,
        "pod_id": pod_id,  # ← Pod que ejecuta
        "tipo": tipo,
        "estado": "running",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat()
    })
    
    # Mantener RUNNING local para taskObject
    RUNNING[exec_id] = asyncio.current_task()

async def cancelar_ejecucion_multipod(exec_id: str) -> bool:
    """Cancela ejecución, incluso si está en otro pod."""
    
    # 1. Check local RUNNING
    if exec_id in RUNNING:
        RUNNING[exec_id].cancel()
        del RUNNING[exec_id]
        logger.info(f"[Cancel] Ejecución {exec_id} cancelada localmente")
        return True
    
    # 2. Check Firestore para ver si está en otro pod
    doc = db.collection("ejecuciones").document(exec_id).get()
    if not doc.exists:
        return False
    
    data = doc.to_dict()
    pod_ejecutor = data.get("pod_id")
    
    if pod_ejecutor == socket.gethostname():
        # Está en este pod pero no en RUNNING (ya terminó)
        return False
    
    # 3. Marcar como "cancelled" en Firestore
    # El pod ejecutor debe revisar periódicamente y cancelar
    db.collection("ejecuciones").document(exec_id).update({
        "estado": "cancelled_requested",
        "cancelled_at": datetime.now(timezone.utc).isoformat(),
        "cancelled_by_pod": socket.gethostname()
    })
    
    logger.info(f"[Cancel] Solicitud de cancelación enviada a pod {pod_ejecutor}")
    return True

# En el worker que ejecuta la tarea, revisar periódicamente:
async def worker_con_cancelacion(exec_id: str):
    while processing:
        # Check si otro pod solicitó cancelación
        doc = db.collection("ejecuciones").document(exec_id).get()
        if doc.exists and doc.to_dict().get("estado") == "cancelled_requested":
            logger.info(f"[Worker] Cancelación solicitada desde otro pod")
            raise asyncio.CancelledError()
        
        # Procesar...
        await asyncio.sleep(1)
```

**Prioridad:** 🟠 **P0 - Crítico para UX**

---

### 5. **Caché de Configuración con Function Attribute** ⚠️

**Ubicación:** `MarketTool.py:3479-3513`

```python
# ⚠️ PROBLEMÁTICO: Caché usando atributos de función (poco convencional)
_config_cache = getattr(obtener_datos_firestore, '_cache', {})
_cache_time = getattr(obtener_datos_firestore, '_cache_time', {})
# ...
obtener_datos_firestore._cache = {'base_data': result}
obtener_datos_firestore._cache_time = {'base_data': now}
```

**Problema:**
- Funciona, pero es poco idiomático y difícil de mantener
- No es thread-safe (aunque en asyncio puede ser OK)
- Caché local por pod (no compartido)

**Impacto:**
- 🟡 **Bajo impacto funcional** (TTL de 300s es razonable)
- 🟡 **Mantenibilidad reducida** (código confuso)
- 🟡 Cada pod hace su propia query a Firestore

**Solución:**

```python
# ✅ Usar clase de caché dedicada (más limpio)
class FirestoreConfigCache:
    def __init__(self, ttl_seconds: int = 300):
        self.ttl_seconds = ttl_seconds
        self._cache: Dict[str, Any] = {}
        self._cache_time: Dict[str, float] = {}
        self._lock = asyncio.Lock()
    
    async def get_or_fetch(self, key: str, fetch_fn: Callable) -> Any:
        async with self._lock:
            now = time.time()
            
            if key in self._cache:
                if (now - self._cache_time[key]) < self.ttl_seconds:
                    return self._cache[key]
            
            # Fetch
            data = await fetch_fn()
            self._cache[key] = data
            self._cache_time[key] = now
            
            return data

_config_cache = FirestoreConfigCache()

async def obtener_datos_firestore():
    return await _config_cache.get_or_fetch('base_data', _fetch_base_data)
```

**Prioridad:** 🟢 **P2 - Refactor en próxima limpieza de código**

---

## 🟡 Problemas de Impacto Medio (P1)

### 6. **Cache de Eventos Económicos en Memoria Local**

**Ubicación:** `MarketTool.py:5707`

```python
_cache_eventos_economicos = {}
```

**Solución:** Similar a cache_noticias, usar GCS o Redis

---

### 7. **Diccionarios de Backfill Tracking en Memoria**

**Ubicación:** `MarketTool.py:15213-15236`

```python
_LAST_INTERNAL_GAP_ATTEMPT = {}
_LAST_BACKFILL_EMPTY = {}
_LAST_RANGE_BACKFILL_ATTEMPT = {}
_LAST_BACKFILL_ATTEMPT = {}
_LAST_BACKFILL_RANGE = {}
```

**Problema:**
- Cooldowns de backfill son por pod
- Si Pod A intenta backfill y falla, Pod B lo reintenta inmediatamente

**Solución:**
```python
# ✅ Guardar en Firestore con TTL
def set_backfill_cooldown(symbol: str, tf: str, cooldown_s: int):
    db.collection("backfill_cooldowns").document(f"{symbol}_{tf}").set({
        "symbol": symbol,
        "timeframe": tf,
        "attempted_at": datetime.now(timezone.utc).isoformat(),
        "cooldown_until": (datetime.now(timezone.utc) + timedelta(seconds=cooldown_s)).isoformat(),
        "pod_id": socket.gethostname()
    })

def is_backfill_in_cooldown(symbol: str, tf: str) -> bool:
    doc = db.collection("backfill_cooldowns").document(f"{symbol}_{tf}").get()
    if not doc.exists:
        return False
    
    data = doc.to_dict()
    cooldown_until = datetime.fromisoformat(data["cooldown_until"].replace('Z', '+00:00'))
    return datetime.now(timezone.utc) < cooldown_until
```

**Prioridad:** 🟡 **P1 - Evita desperdicio de cuota FMP**

---

### 8. **Subscriptions y Admin IDs en Memoria**

**Ubicación:** `MarketTool.py:1010-1012`

```python
subscriptions = {}
subscriptions_type = {}
admin_ids = {}
```

**Nota:** Ya se cargan desde Firestore con `cargar_datos_subscription_user()`, pero se cachean localmente.

**Solución:**
- ✅ **ACTUAL:** Ya está mayormente OK (Firestore es source of truth)
- 🟡 **MEJORA:** Agregar TTL para refresh automático cada 5 min

```python
_subscriptions_cache_time = 0
SUBSCRIPTIONS_TTL = 300  # 5 min

async def get_subscriptions():
    global subscriptions, _subscriptions_cache_time
    
    now = time.time()
    if now - _subscriptions_cache_time > SUBSCRIPTIONS_TTL:
        subscriptions = await cargar_datos_subscription_user()
        _subscriptions_cache_time = now
    
    return subscriptions
```

**Prioridad:** 🟢 **P2 - Low risk, ya funciona**

---

## 🟢 Problemas Menores (P2)

### 9. **matplotlib_lock - Lock Local para Renderizado**

**Impacto:** Bajo. Cada pod puede renderizar gráficos independientemente.

**Solución:** No necesaria. El lock actual evita race conditions en matplotlib interno.

---

### 10. **file_locks - Locks para Archivos Locales**

**Ubicación:** `MarketTool.py:1042`

```python
file_locks = {}
```

**Problema:** Si se usa con GCS, no protege entre pods.

**Solución:**
- ✅ Si solo se usa para archivos temporales locales → OK
- ⚠️ Si se usa para GCS → reemplazar con DistributedLock

**Acción:** Auditar uso de `file_locks` para confirmar alcance.

---

## 📋 Plan de Implementación Priorizado

### Sprint 1: Críticos (P0) - 2-3 días

1. ✅ **Leader Election** (ya implementado)
2. 🔴 **Estado de Usuario distribuido**
   - Modificar `get_user_state()` para leer de Firestore
   - Mantener caché local con TTL de 10s
3. 🔴 **Tracking de ejecuciones multi-pod**
   - Cancelación cross-pod con Firestore
   - Worker que revisa `cancelled_requested`

### Sprint 2: Alto Impacto (P1) - 3-5 días

4. 🟡 **Caché de noticias compartido** (si costos FMP son problema)
5. 🟡 **Backfill cooldowns distribuidos**
6. 🟡 **Distributed locks para operaciones críticas**

### Sprint 3: Refactoring (P2) - Continuous

7. 🟢 **Limpiar caché con function attributes** (cosmético)
8. 🟢 **TTL automático para subscriptions**
9. 🟢 **Auditoría de file_locks**

---

## 🧪 Testing Multi-Pod

### Setup de Testing

```bash
# 1. Levantar 3 réplicas localmente
docker-compose up --scale app2=3

# 2. Configurar load balancer round-robin
# (Ingress de GKE ya hace esto)

# 3. Test de switching entre pods
curl http://pod-1:8080/api/pod/status  # Pod A
curl http://pod-2:8080/api/pod/status  # Pod B
curl http://pod-3:8080/api/pod/status  # Pod C
```

### Test Cases

#### Test 1: Estado de Usuario Persistente

```python
# Usuario en Pod A
POST /api/procesar_simbolo user_id=123 → exec_id=abc

# Usuario cambia a Pod B (round-robin)
GET /api/user/state user_id=123
# Esperado: estado="en_ejecucion", exec_id="abc"

# ✅ PASS si estado se mantiene
# ❌ FAIL si estado="disponible" (perdió contexto)
```

#### Test 2: Cancelación Cross-Pod

```python
# Usuario en Pod A inicia análisis
POST /api/procesar_simbolo user_id=123 → exec_id=abc (runs in Pod A)

# Usuario cambia a Pod B
POST /api/cancelar_ejecucion exec_id=abc (sent to Pod B)

# Verificar en Pod A
GET /api/ejecucion/abc/estado
# Esperado: estado="cancelled"

# ✅ PASS si cancelación funciona cross-pod
# ❌ FAIL si "Ejecución no encontrada"
```

#### Test 3: Caché Compartido (Noticias)

```python
# Pod A fetch noticias de EURUSD (cold start)
GET /api/noticias?symbol=EURUSD (Pod A)
# Tiempo: ~2000ms (fetch desde FMP)

# Pod B fetch noticias de EURUSD (debe usar GCS)
GET /api/noticias?symbol=EURUSD (Pod B)
# Tiempo: ~200ms (cache hit GCS)

# ✅ PASS si Pod B < 500ms
# ❌ FAIL si Pod B > 1500ms (re-fetch desde FMP)
```

---

## 📊 Métricas de Éxito

| Métrica | Antes | Meta Después |
|---------|-------|--------------|
| **Estado perdido al cambiar pod** | 100% | 0% |
| **Cancelaciones fallidas cross-pod** | 100% | 0% |
| **Solicitudes duplicadas FMP** | 3x (por pods) | 1x |
| **Latencia promedio (cache hit)** | 2000ms | 200ms |
| **Inconsistencias de sincronización** | Frecuentes | Ninguna |
| **Desperdicio de cuota API** | Alto | Mínimo |

---

## 🛠️ Variables de Entorno Adicionales

```bash
# Estado de usuario
USER_STATE_CACHE_TTL_SECONDS=10   # TTL de caché local de user_states
USER_STATE_STORAGE=firestore      # firestore | redis | memory (fallback)

# Caché de noticias
NEWS_CACHE_STORAGE=gcs            # gcs | redis | memory
NEWS_CACHE_TTL_SECONDS=300        # 5 min

# Distributed locks
DISTRIBUTED_LOCKS_ENABLED=true
DISTRIBUTED_LOCK_TTL_SECONDS=60   # Default TTL para locks

# Backfill cooldowns
BACKFILL_COOLDOWN_STORAGE=firestore  # firestore | memory
```

---

## 🔐 Permisos Firestore Adicionales

### Colecciones Nuevas Requeridas

```
locks/               # Distributed locks
  {lock_name}
    - pod_id
    - acquired_at
    - ttl_seconds

backfill_cooldowns/  # Cooldowns de backfill
  {symbol}_{tf}
    - symbol
    - timeframe
    - attempted_at
    - cooldown_until
    - pod_id

ejecuciones/         # Ya existe, mejorar schema
  {exec_id}
    - exec_id
    - user_id
    - pod_id          ← AGREGAR
    - tipo
    - estado          # running | completed | failed | cancelled_requested | cancelled
    - started_at
    - updated_at
    - cancelled_at    ← AGREGAR
    - cancelled_by_pod ← AGREGAR
```

### Índices Firestore

```bash
# Collection: ejecuciones
# Index: estado (ASC) + pod_id (ASC)
gcloud firestore indexes composite create \
  --collection-group=ejecuciones \
  --field-config=field-path=estado,order=ASCENDING \
  --field-config=field-path=pod_id,order=ASCENDING

# Collection: backfill_cooldowns
# Index: cooldown_until (ASC)
gcloud firestore indexes composite create \
  --collection-group=backfill_cooldowns \
  --field-config=field-path=cooldown_until,order=ASCENDING
```

---

## 🎯 Conclusión

**Estado Actual:**
- ⚠️ Código funcionaen single-pod o con load balancer sin sticky sessions se pierde contexto

**Estado Objetivo:**
- ✅ Completamente stateless (pods intercambiables)
- ✅ Estado compartido vía Firestore/Redis
- ✅ Coordinación distribuida (locks, leader election)
- ✅ Caché compartido (GCS para datos grandes, Redis para hot data)

**Prioridades:**
1. 🔴 P0: Estado de usuario + tracking de ejecuciones (3 días)
2. 🟡 P1: Cachés compartidos + backfill cooldowns (5 días)
3. 🟢 P2: Refactoring continuo

**ROI Esperado:**
- 🎯 UX consistente independiente del pod
- 💰 Reducción de costos API (3x → 1x)
- ⚡ Mejor performance (cachés compartidos)
- 🔒 Más robusto (sin race conditions)

---

**Última actualización:** 11 de Febrero, 2026  
**Status:** 📋 Análisis completo  
**Siguiente paso:** Priorizar P0 items para Sprint 1
