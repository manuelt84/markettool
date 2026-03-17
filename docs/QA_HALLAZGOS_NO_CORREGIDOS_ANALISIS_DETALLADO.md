# 📋 QA: Hallazgos No Corregidos - Análisis Detallado

## 🔴 HALLAZGO CRÍTICO #1: WebSocket sin sincronización multi-pod

### **Ubicación**
- **Archivo:** [markettool/interfaces/api/ponderacion_routes.py](markettool/interfaces/api/ponderacion_routes.py)
- **Línea aproximada:** 89-110
- **Código problemático:**
```python
_connected_clients = set()  # Global en cada pod

async def broadcast_ponderacion_update(...):
    for ws in _connected_clients:  # ❌ Solo pods locales
        ws.send(message)
```

### **Problema**
En un ambiente Kubernetes con N pods:
- Pod 1 tiene 5 clientes WebSocket conectados
- Pod 2 tiene 3 clientes WebSocket conectados
- Pod 3 tiene 2 clientes WebSocket conectados

Cuando ocurre un evento en Pod 1:
- Se ejecuta `broadcast_ponderacion_update()` solo en Pod 1
- Los 5 clientes locales reciben el update ✓
- Los 3 clientes en Pod 2 no reciben NADA ❌
- Los 2 clientes en Pod 3 no reciben NADA ❌

**Impacto:** Clientes desincronizados, datos obsoletos, decisiones de trading incorrectas

### **Solución Recomendada**
**Redis Pub/Sub Pattern:**

```python
import redis

# Inicializar una sola vez
redis_conn = redis.Redis(host='redis-service', port=6379, decode_responses=True)

# En lugar de broadcast_ponderacion_update():
async def broadcast_ponderacion_update(symbol, timeframe, ponderacion_data):
    # Publicar a Redis para todos los pods
    await redis_conn.publish(
        f"ponderacion:{symbol}:{timeframe}",
        json.dumps(ponderacion_data)
    )
    
    # Enviar a clientes locales
    for ws in _connected_clients:
        ws.send(json.dumps(ponderacion_data))

# En conexión WebSocket (en cada pod):
async def handle_websocket(ws):
    # Suscribirse a updates globales
    pubsub = redis_conn.pubsub()
    pubsub.subscribe(f"ponderacion:{symbol}:{timeframe}")
    
    # Escuchar tanto updates locales como de Redis
    while True:
        msg = await pubsub.get_message()
        if msg:
            ws.send(msg['data'])
```

**Ventajas:**
- ✅ Sincronización global entre pods
- ✅ No duplica mensajes (Redis se encarga)
- ✅ Escalable a N pods
- ✅ Manejo de pod failures

### **Esfuerzo Estimado**
- Implementación: 4-6 horas
- Testing: 2-3 horas
- Total: 1-1.5 días

**Prioridad:** 🟠 MAYOR (afecta integridad de datos en producción)

---

## 🔴 HALLAZGO CRÍTICO #2: Ponderación - Bonificaciones Inconsistentes

### **Ubicación**
- **Archivo 1:** [MarketTool.py](MarketTool.py) (Legacy)
  - `calcular_ponderacion_incremental_mejorada()` línea aprox. 26000-26100
  - `calcular_ponderacion_direccional()` línea aprox. 26200-26300

- **Archivo 2:** [markettool/application/services/ponderacion_service.py](markettool/application/services/ponderacion_service.py) (Hexagonal)
  - Búsqueda pendiente (no implementado en versión nueva?)

### **Problema**

Existen **3 versiones diferentes** del cálculo de ponderación:

#### **Versión 1: PI_Long/PI_Short (Mejorada)**
```python
def calcular_ponderacion_incremental_mejorada(...):
    # Aplica bonificación por confluencia
    pi_long *= 1.5  if confluencia >= 1.0 else pi_long *= 1.25 if confluencia >= 0.75
    # Resultado: exponencial por timeframe + bonificación confluencia
```

#### **Versión 2: Ponderacion_Long/Ponderacion_Short (Direccional)**
```python
def calcular_ponderacion_direccional(...):
    # NO aplica bonificación por confluencia
    # Solo suma deltas (impulsos técnicos)
    ponderacion = delta_1h + delta_4h + delta_1d + ...
    # Resultado: suma aritmética, sin confluencia
```

#### **Versión 3: Ponderacion (General)**
```python
def calcular_ponderacion(...):
    # Desconocida - requiere investigación
```

### **Inconsistencias Detectadas**

| Aspecto | PI_Long | Ponderacion_Long | Diferencia |
|---------|---------|------------------|-----------|
| **Base de cálculo** | Exponencial | Suma aritmética | ❌ |
| **Bonificación confluencia** | ✅ Sí | ❌ No | ❌ |
| **Sensibilidad cambios** | Alta | Baja | ❌ |
| **Rango típico** | -50 a 150 | -30 a 30 | ❌ |
| **API expuesta para...?** | Ranking global | Ranking directional | ❌ |

### **Impacto en Endpoints**

1. **GET `/api/ponderacion/stats`**
   - ¿Retorna PI_Long, Ponderacion_Long o ambas?
   - ¿El cliente sabe cuál usar para qué?

2. **GET `/api/ponderacion/rank-change`**
   - ¿Ranking basado en qué versión?
   - ¿Puede ser inconsistente entre llamadas?

3. **WebSocket `/api/ponderacion/stream`**
   - ¿Qué versión se envía?
   - ¿Cambia según configuración de usuario?

### **Problema Real**
Un trader configura su sistema usando `PI_Long` (exponencial + confluencia).
Cambia de proveedor de datos o reinicia el pod.
Se carga `Ponderacion_Long` por defecto (suma + sin confluencia).
Sus rankings cambian dramáticamente sin explicación.
**Pérdida de confianza y capital.**

### **Solución Recomendada**

1. **Auditoría** (2 horas)
   - Mapear EXACTAMENTE cuáles versiones se usan en producción
   - Verificar código legacy vs hexagonal

2. **Decisión de diseño** (1 hora con producto)
   - ¿Deprecated una versión?
   - ¿Usar ambas pero documentar claramente?
   - ¿Parámetro en configuración de usuario?

3. **Implementación** (4-6 horas)
   - Unificar lógica o documentar divergencias
   - Agregar test de regresión

4. **API Clarity** (2 horas)
   ```json
   // Respuesta mejorada:
   {
     "symbol": "EURUSD",
     "timeframe": "1h",
     "ponderacion": {
       "pi_long": 85.3,  // exponencial + confluencia
       "pi_short": -42.1,
       "ponderacion_long": 12.5,  // suma simple
       "ponderacion_short": -8.2,
       "calculation_method": "hybrid",  // documentar
       "confidence": 0.87,
       "timestamp": "2026-03-17T10:30:00Z"
     }
   }
   ```

**Prioridad:** 🔴 CRÍTICA (causa inconsistencias de trading)

---

## 🟠 HALLAZGO MAYOR #3: Persistencia de Ejecuciones - Fallback Insuficiente

### **Ubicación**
- **Archivo:** [markettool/interfaces/api/execution_routes.py](markettool/interfaces/api/execution_routes.py)
- **Línea:** aprox. 45-85

### **Código Problemático**
```python
@app.route("/api/execution/<exec_id>/status", methods=["GET"])
def get_execution_status(exec_id: str):
    # Paso 1: Intenta Firestore si está habilitado
    if execution_tracker.firestore_enabled and execution_tracker.db:
        try:
            doc = execution_tracker.db.collection("ejecuciones").document(exec_id).get()
            if doc.exists:
                return jsonify(doc.to_dict()), 200
        except:
            pass  # ← Silencio el error, continúa
    
    # Paso 2: Fallback a memoria local (PROBLEMA)
    if exec_id in running:
        return jsonify({...}), 200
    
    # Paso 3: No encontrado
    return jsonify({"error": "Not found"}), 404
```

### **Problemas**

#### **A. Pod Reinicia = Pérdida Total de Data**
```
Hora 10:00 → Pod 1 inicia ejecución exec_id=xyz
Hora 10:05 → Pod 1 falla, reinicia
Hora 10:06 → Cliente query /api/execution/xyz/status
            → En-memoria está vacío, no está en Firestore aún
            → Retorna 404 (INCORRECTO, debería ser "pendiente")
```

#### **B. Múltiples Pods = Inconsistencia**
```
Hora 10:00 → Pod 1 inicia ejecución exec_id=abc
            → Crea doc en Firestore: estado="pendiente"
Hora 10:01 → Pod 2 recibe query /api/execution/abc/status
            → Pod 2 no tiene en-memoria (exec_id=abc no está en running)
            → Firestore read falla por timeout
            → Retorna 404 (INCORRECTO)
```

#### **C. Kubernetes Lifecycle Events**
```
Scenario: Pod en readiness probe durante 30 seg
- Firestore puede estar "down" desde perspectiva del pod
- En-memoria vacío
- Cliente recibe 404 falso
```

### **Impacto**
- 🔴 Clientes no pueden tracking operaciones
- 🔴 Automatic traders abortan por "pérdida de contexto"
- 🔴 Reporte de balance incorrectos
- 💰 **Pérdida potencial de múltiples operaciones en vivo**

### **Solución Recomendada - Tres Niveles**

```python
# Nivel 1: En-memoria (rápido, pod-local)
_execution_cache = TTLCache(maxsize=1000, ttl=300)  # 5 minutos

# Nivel 2: Redis (compartido entre pods)
redis_cache = redis.Redis(...)

# Nivel 3: Firestore (persistencia permanente)
firestore_db = ...

async def get_execution_status(exec_id: str):
    # 1️⃣ Nivel 1: Caché en-memoria (rápido)
    if exec_id in _execution_cache:
        return _execution_cache[exec_id]
    
    # 2️⃣ Nivel 2: Redis (compartido entre pods)
    try:
        status = await redis_cache.get(f"exec:{exec_id}")
        if status:
            _execution_cache[exec_id] = status
            return status
    except RedisError:
        logger.warning(f"Redis unavailable, skipping L2 cache")
    
    # 3️⃣ Nivel 3: Firestore (persistencia)
    try:
        doc = firestore_db.collection("ejecuciones").document(exec_id).get()
        if doc.exists:
            status = doc.to_dict()
            # Repopulate L1 y L2
            _execution_cache[exec_id] = status
            await redis_cache.setex(f"exec:{exec_id}", 300, json.dumps(status))
            return status
    except FirestoreError as e:
        logger.error(f"Firestore error: {e}")
        # Retornar último estado conocido si está disponible
        return {"status": "unknown", "warning": "persistence layer unavailable"}
    
    # No encontrado en ningún nivel
    return {"error": "execution not found", "exec_id": exec_id}, 404
```

**Ventajas:**
- L1: Rapidez (99% de accesos aquí)
- L2: Sincronización entre pods
- L3: Persistencia permanente

**Esfuerzo:** 8-10 horas (incluye testing con failures inyectados)

**Prioridad:** 🔴 CRÍTICA (integridad de operaciones)

---

## 🟠 HALLAZGO MAYOR #4: Validación Temporal sin Límites

### **Ubicación**
- **Archivo:** [markettool/interfaces/api/monitoreo_routes.py](markettool/interfaces/api/monitoreo_routes.py)
- **Línea:** varios endpoints de historial

### **Problema**

```python
@app.route("/monitoreo/history", methods=["POST"])
def get_history():
    data = request.get_json()
    start_date = data.get("start_date")  # ¿2026-01-01? ¿2020-01-01? ¿1970-01-01?
    end_date = data.get("end_date")      # ¿2026-12-31?
    
    # ❌ Sin validación:
    # - ¿Rango es realista? (¿6 años de historia?)
    # - ¿Puede causar timeout de BD?
    # - ¿Puede causar OOM (out of memory) en agregación?
```

### **Casos de Ataque**

```javascript
// Cliente malévolo:
POST /api/monitoreo/history
{
  "start_date": "1970-01-01",  // ← 56 años de historial
  "end_date": "2026-12-31"
}
// Resultado: Query masiva, posible crash

// O intencionalmente ambiguo:
{
  "start_date": "2020-01-01",
  "end_date": "2020-01-01",
  "limit": 999999  // Pedir 1M records de un solo día
}
```

### **Solución Rápida (1 hora)**

```python
def get_history():
    data = request.get_json()
    
    # Parsear con validación
    try:
        start_date = datetime.fromisoformat(data.get("start_date", ""))
        end_date = datetime.fromisoformat(data.get("end_date", ""))
    except ValueError:
        return jsonify({"error": "Invalid date format"}), 400
    
    # Limitar rango máximo
    max_range = timedelta(days=365)  # Max 1 año de historial
    if end_date - start_date > max_range:
        return jsonify({
            "error": f"Date range exceeds {max_range.days} days"
        }), 400
    
    # Limitar límite de registros
    limit = min(int(data.get("limit", 100)), 5000)  # Max 5000 records
    
    # Ahora sí, query segura
    ...
```

**Prioridad:** 🟠 MAYOR (previene DOS)

---

## 🟡 HALLAZGO MENOR #5: Error Handling Incompleto

### **Ubicación - Multiple locations**

#### **A. Falta try/except para conversiones de tipo**
```python
# monitoreo_routes.py, historicos_routes.py, etc.
offset = int(request.args.get("offset", 0))  # ❌ Si no es número, crash
# Fix:
try:
    offset = int(request.args.get("offset", 0))
except (ValueError, TypeError):
    offset = 0
```

#### **B. Ausencia de logging distribuido (tracing)**
```python
# Ningún endpoint loguea:
# - Request ID (para correlacionar requets)
# - Timestamp preciso
# - Parámetros (para debugging)
# - Tiempo de ejecución (performance monitoring)
```

**Solución (Middleware global):**
```python
from uuid import uuid4

@app.before_request
def add_request_context():
    request.request_id = str(uuid4())
    request.start_time = time.time()
    logger.info(f"[{request.request_id}] {request.method} {request.path}", extra={
        "params": dict(request.args),
        "ip": request.remote_addr
    })

@app.after_request
def log_response(response):
    duration = time.time() - request.start_time
    logger.info(f"[{request.request_id}] Completed in {duration:.2f}s", extra={
        "status_code": response.status_code,
        "size_bytes": len(response.get_data())
    })
    return response
```

**Prioridad:** 🟡 MENOR (pero importante para debugging en producción)

---

## 📊 Tabla Resumen - Hallazgos No Corregidos

| # | Hallazgo | Severidad | Ubicación | Esfuerzo | Usuario Impactado |
|----|----------|-----------|-----------|----------|------------------|
| 1 | WebSocket multi-pod | 🔴 CRÍTICA | ponderacion_routes.py | 8h | TODOS (datos desincronizados) |
| 2 | Ponderación inconsistente | 🔴 CRÍTICA | MarketTool.py + services | 6h | Traders (rankings impredecibles) |
| 3 | Persistencia ejecuciones | 🔴 CRÍTICA | execution_routes.py | 10h | Bots automáticos |
| 4 | Validación temporal | 🟠 MAYOR | monitoreo_routes.py | 2h | Seguridad (DOS) |
| 5 | Error handling | 🟡 MENOR | All APIs | 4h | Debugging |

---

## 🚀 Recomendación de Próximos Pasos

### **Semana 1: Críticas**
1. ✅ WebSocket multi-pod (Redis Pub/Sub)
2. ✅ Auditoría de Ponderación
3. ✅ Persistencia multi-nivel

### **Semana 2: Mayores**
4. ✅ Validación temporal
5. ✅ Error handling global

### **Observación Importante**
Todos estos hallazgos están relacionados con **escalabilidad en Kubernetes**. La arquitectura fue diseñada para un solo pod, pero no para múltiples instancias sincronizadas.

---

## 🔗 Relacionado: BUY/SELL Logic - Por Aclarar

Pendiente verificación de:
- ¿Cómo parametriza el front la dirección de trade?
- ¿Usa `trade_type` explícito o se infiere del orden de precios?
- ¿Legacy (MarketTool.py) y Hexagonal tienen comportamientos diferentes?

**Recomendación:** Añadir parámetro `trade_type` optativo ("BUY"/"SELL") a `calculate_position_size()` para mayor claridad y robustez.
