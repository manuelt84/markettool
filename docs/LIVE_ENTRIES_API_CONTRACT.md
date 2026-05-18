# LIVE ENTRIES API CONTRACT
**Versión:** 1.1
**Fecha:** 2026-05-16
**Propósito:** Mover la generación de entradas en vivo del cliente (RN/Web) al backend Python, habilitando iOS y eliminando dependencia de procesamiento local de candles.

---

## Estado Actual 2026-05-16

Este endpoint ya existe en backend y quedó en desarrollo **sin migrar RN/Web todavía**. La pantalla actual de Monitoreo en Web y RN debe seguir funcionando como está hasta que se active explícitamente un modo nuevo en el frontend.

### Implementado

- Archivo backend: `markettool/interfaces/api/live_entries_routes.py`.
- Registro de rutas: `markettool/interfaces/api/route_factory.py` llama `register_live_entries_routes(app, services=legacy_services)`.
- Endpoints actuales: `POST /start`, `GET /live-entries`, `GET /stream`, `POST /stop`, `POST /outcome` bajo `/monitoreo/live-entries`.
- Worker asyncio por `exec_id + symbol`.
- Un subtask por TF, con cadencia homologada al monitoreo actual: `1m=5s`, `5m=20s`, `15m=45s`, `30m=90s`, `1h=180s`, `4h=600s`, `1d=6000s`, `1w=18000s`.
- Auto-descubrimiento de TFs activos desde Firestore `monitoreos/{exec_id}__{SYMBOL}` usando `running`, `allowed_timeframes` o `tf_states`.
- Reconciliación dinámica de TFs cada 30s: si Firestore agrega/quita TFs, el worker arranca/cancela subtasks.
- Generación de entradas en backend usando `calcular_entradas_sync_wrapper` y `calcular_indicadores`.
- Deduplicación backend por `id` estable con fingerprint `symbol/tf/side/timestamp/prices/source`.
- Persistencia de entradas con TTL 5 min en Redis y fallback en memoria si Redis no está disponible.
- Publicación de nuevas entradas por Redis Pub/Sub si hay Redis, o bus in-process si no hay Redis.
- Canal push SSE `GET /monitoreo/live-entries/stream` para consumo futuro de RN/Web.
- El worker no depende de que iOS mantenga un loop largo de frontend: si Firestore sigue indicando TFs activos, el backend continúa aunque el cliente deje de hacer polling.

### Decisión de alcance

- **No enlazar todavía Web/RN al endpoint.**
- Web/RN no deben llamar `/monitoreo/live-entries` ni `/monitoreo/live-entries/stream` hasta que se implemente un modo seleccionable.
- La futura migración debe ser opt-in, idealmente con chip/radio button: `Generar en pantalla` vs `Entradas desde backend`.
- El backend solo debe monitorear el activo que el usuario está viendo. El cliente futuro debe iniciar/suscribirse al `symbol` visible y detener/cambiar al cambiar de activo.

### Validación ejecutada

- `python3 -m py_compile markettool/interfaces/api/live_entries_routes.py` OK.
- `git diff --check -- markettool/interfaces/api/live_entries_routes.py` OK.
- Scan en Web/RN sin llamadas a `/monitoreo/live-entries`.
- Web y RN se mantienen sin integración al nuevo endpoint.

### Pendiente para retomar

- Probar end-to-end `start` + `stream` con un cliente SSE real.
- Validar Cloud Run/nginx para SSE sin buffering ni cortes.
- Definir si producción multi-instancia exige Redis obligatorio para Pub/Sub.
- Diseñar el modo frontend opt-in sin alterar el comportamiento actual.
- Crear hook futuro RN/Web para `Entradas desde backend`: abrir stream solo para el activo visible, mezclar entradas con el dedupe actual y cerrar/cambiar al cambiar de activo.
- Decidir si outcome TP/SL queda en cliente vía `POST /outcome` o migra también al backend.
- Definir persistencia durable si se requiere historial más allá del TTL de 5 min.

---

## Contexto

### Hoy (cliente genera las entradas)
```
RN/Web
  → POST /monitoreo/incremental  (candles)
  → corre generateLiveEntries()  (TypeScript local)
  → liveEntriesByTf              (estado local)
```

### Objetivo (backend genera las entradas)
```
Backend Python
  → descarga candles FMP/GCS     (ya lo hace)
  → corre motor Python existente (indicadores + calcular_entradas_sync_wrapper)
  → persiste en Redis            (TTL 5 min)
  → publica nuevas entradas      (Redis Pub/Sub / SSE)

RN / Web / iOS
  → futuro modo opt-in: SSE o GET liviano
  → liveEntriesByTf              (misma interfaz UI, sin cambiar pantallas actuales hasta migración)
```

---

## Endpoints

---

### 1. `POST /monitoreo/live-entries/start`
Registra interés del cliente en recibir entradas en vivo.  
El backend arranca (o reutiliza) un worker asyncio por `exec_id+symbol`.

**Request:**
```json
{
  "exec_id": "abc123",
  "user_id": "uid_xxx",
  "symbol": "BTCUSD",
  "tfs": ["1m", "5m", "15m"],
  "platform": "RN_ANDROID | RN_IOS | WEB"
}
```

**Response:**
```json
{
  "ok": true,
  "worker_id": "abc123__BTCUSD",
  "status": "started | already_running"
}
```

**Comportamiento del worker:**
- Ciclo por TF según temporalidad (`TF_BEAT_S`).
- Por cada TF: refresca candles FMP/GCS → calcula indicadores → corre motor de entradas → detecta entradas nuevas.
- Persiste entradas nuevas en Redis con TTL 5 min o fallback en memoria.
- Publica entradas nuevas por Redis Pub/Sub o bus in-process.
- Se mantiene activo si Firestore mantiene TFs activos; no depende de polling continuo del cliente.

---

### 2. `GET /monitoreo/live-entries`
Polling liviano del cliente. Devuelve entradas generadas por el worker.

**Query params:**
```
exec_id=abc123
symbol=BTCUSD
tfs=1m,5m,15m
since_ts=1715000000000   (opcional — solo entradas más nuevas que este timestamp)
```

**Response:**
```json
{
  "entries": [
    {
      "id": "live|BTCUSD|1m|long|1715001234567",
      "symbol": "BTCUSD",
      "timeframe": "1m",
      "side": "long",
      "entry_price": 62500.0,
      "take_profit": 63000.0,
      "stop_loss": 62200.0,
      "rrr": 1.67,
      "confluence_score": 72,
      "source": "SR_BOUNCE",
      "outcome": "pending",
      "_origin": "live",
      "created_at": "2026-05-12T12:00:00Z",
      "timestamp": 1715001234567,
      "nivel_confirmado": true,
      "dentro_rango": true,
      "early_detection": false,
      "sr_levels": {
        "s1": 62100.0,
        "s2": 61500.0,
        "r1": 63200.0,
        "r2": 64000.0
      }
    }
  ],
  "last_beat_ts": 1715001260000,
  "worker_status": "running | stopped | error",
  "candles_count": {
    "1m": 240,
    "5m": 200,
    "15m": 150
  },
  "sr_levels": {
    "1m": { "s1": 62100.0, "s2": 61500.0, "r1": 63200.0, "r2": 64000.0 },
    "5m": { "s1": 61800.0, "s2": 61000.0, "r1": 63500.0, "r2": 64500.0 },
    "15m": { "s1": 61000.0, "s2": 60000.0, "r1": 64000.0, "r2": 65000.0 }
  }
}
```

**Notas:**
- Si `since_ts` está presente, solo devuelve entradas con `timestamp > since_ts`
- `sr_levels` en la respuesta raíz son los niveles actuales (para que el cliente no tenga que calcularlos)
- El cliente usa estos `sr_levels` para enriquecer la UI (igual que hoy con GCS seed)
- Este endpoint queda como fallback/polling liviano. La ruta preferida para el modo futuro puede ser SSE.

---

### 3. `GET /monitoreo/live-entries/stream`
Canal push SSE para futuras integraciones RN/Web. No está enlazado al frontend todavía.

**Query params:**
```text
exec_id=abc123
symbol=BTCUSD
tfs=1m,5m,15m   (opcional)
```

**Eventos SSE:**

- `ready`: confirma conexión y estado del worker.
- `entries`: entrega solo entradas nuevas publicadas por el worker.
- `heartbeat`: mantiene viva la conexión y facilita detectar cortes.

**Ejemplo `entries`:**
```json
{
  "type": "entries",
  "exec_id": "abc123",
  "symbol": "BTCUSD",
  "timeframe": "1m",
  "entries": [
    {
      "id": "live|d4e5f6...",
      "symbol": "BTCUSD",
      "timeframe": "1m",
      "side": "long",
      "entry_price": 62500.0,
      "take_profit": 63000.0,
      "stop_loss": 62200.0,
      "outcome": "pending",
      "_origin": "live",
      "_backend_live": true
    }
  ],
  "ts": 1715001260000
}
```

**Transporte interno:**

- Redis Pub/Sub: `live_entries_events:{exec_id}:{SYMBOL}`.
- Fallback local: cola in-process para desarrollo/instancia única.
- Headers SSE: `Content-Type: text/event-stream`, `Cache-Control: no-cache`, `X-Accel-Buffering: no`.



---

### 4. `POST /monitoreo/live-entries/stop`
Detiene el worker del backend para ese `exec_id+symbol`.

**Request:**
```json
{
  "exec_id": "abc123",
  "symbol": "BTCUSD"
}
```

**Response:**
```json
{
  "ok": true,
  "stopped": "abc123__BTCUSD"
}
```

---

### 5. `POST /monitoreo/live-entries/outcome`
El cliente notifica al backend cuando una entrada resuelve TP/SL.  
Permite que el backend actualice el estado y no re-emita la entrada.

**Request:**
```json
{
  "entry_id": "live|BTCUSD|1m|long|1715001234567",
  "outcome": "tp | sl | expired",
  "close_price": 63000.0,
  "closed_at": "2026-05-12T12:30:00Z"
}
```

**Response:**
```json
{ "ok": true }
```

---

## Motor Python actual

No se creó todavía un `live_engine.py` separado. El endpoint reutiliza el motor Python existente para reducir riesgo:

| Responsabilidad | Implementación actual |
|---|---|
| Candles | `load_cache`, `maybe_refresh_from_gcs`, `fetch_historical_range` |
| Indicadores | `MarketTool.calcular_indicadores()` |
| Entradas | `MarketTool.calcular_entradas_sync_wrapper()` |
| S/R seed | niveles desde cache/resultado si están disponibles |
| Freshness | `_are_candles_fresh()` local |
| Formato LiveEntry | `_generate_live_entries_sync()` |

Pendiente: decidir si conviene extraer un `live_engine.py` propio cuando el contrato esté estable y probado end-to-end.

---

## Lo que NO cambia en RN/Web

- Por ahora no cambia nada en RN/Web: no están enlazados al nuevo endpoint.
- Cuando se implemente, toda la UI debe seguir igual: filtros, chips, DetalleEjecucion, GlobalEntriesModal.
- La migración debe ser por modo opt-in, no reemplazo directo.
- `liveEntriesByTf` debe seguir siendo el estado central para no rehacer la UI.
- El outcome checker sigue en el cliente hasta decidir Fase 3.

```typescript
// Hoy
generateLiveEntries(candles) → setLiveEntriesByTf(...)

// Futuro modo opt-in
SSE /monitoreo/live-entries/stream → setLiveEntriesByTf(...)
```

---

## Fases de implementación

### Fase 1 — Backend worker + endpoint (sin tocar RN/Web)
- [x] Worker asyncio por exec_id+symbol
- [x] Subtask por TF con cadencia por temporalidad
- [x] Redis TTL para entradas
- [x] Fallback en memoria si Redis no está disponible
- [x] Endpoints: start / GET / stream / stop / outcome
- [x] Publicación de nuevas entradas por Redis Pub/Sub / SSE
- [x] Auto-descubrimiento y reconciliación de TFs desde Firestore
- [ ] Prueba end-to-end con BTCUSD en local usando cliente SSE real
- [ ] Validar Cloud Run/nginx para SSE en producción

### Fase 2 — Modo opt-in RN/Web, no migración directa
- [ ] Agregar chip/radio button: `Generar en pantalla` vs `Entradas desde backend`.
- [ ] Crear hook `useLiveEntriesBackend` que consuma SSE y tenga fallback GET.
- [ ] Suscribirse solo al activo visible por el usuario.
- [ ] Al cambiar de activo, cerrar stream anterior y abrir el nuevo.
- [ ] Mezclar entradas backend con el dedupe actual sin romper `liveEntriesByTf`.
- [ ] Mantener modo actual como default hasta QA y aprobación explícita.

### Fase 3 — Outcomes y persistencia
- [ ] Decidir si TP/SL se sigue resolviendo en cliente o migra al backend.
- [ ] Persistencia durable si se necesita historial más allá del TTL 5 min.
- [ ] Métricas de worker: entradas generadas, eventos publicados, subscribers activos, errores por TF.

---

## Dependencias del backend

| Dependencia | Estado |
|---|---|
| FMP API para candles | ✅ Ya implementado (`_fetch_historical_range`) |
| GCS para seed S/R | ✅ Ya implementado (`_maybe_refresh_from_gcs`) |
| Training data / niveles | ✅ Ya en `_INDICATORS_CACHE` |
| Eventos económicos | ✅ Ya implementado (`_fetch_events_for`) |
| Redis para TTL entradas | ✅ Implementado con fallback memoria |
| Worker asyncio | ✅ Implementado |
| SSE stream | ✅ Implementado backend-only |
| Redis Pub/Sub | ✅ Implementado para nuevas entradas |
| `live_engine.py` separado | ⏳ Opcional/pendiente |
| Frontend opt-in | ❌ Pendiente |
| QA end-to-end SSE | ❌ Pendiente |
