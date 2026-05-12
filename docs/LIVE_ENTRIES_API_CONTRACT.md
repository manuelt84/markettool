# LIVE ENTRIES API CONTRACT
**Versión:** 1.0  
**Fecha:** 2026-05-12  
**Propósito:** Mover la generación de entradas en vivo del cliente (RN/Web) al backend Python, habilitando iOS y eliminando dependencia de procesamiento local de candles.

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
  → corre live_engine.py         (nuevo — port de generateLiveEntries)
  → persiste en Redis            (TTL 5 min)
  → push FCM/APNs                (opcional)

RN / Web / iOS
  → GET /monitoreo/live-entries  (polling liviano ~30s)
  → liveEntriesByTf              (misma interfaz UI)
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
  "push_token": "fcm_token_opcional",
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
- Ciclo cada ~30s por TF
- Por cada TF: refresca candles FMP → corre `live_engine.py` → detecta entradas nuevas
- Persiste entradas nuevas en Redis con TTL 5 min
- Si `push_token` presente → envía push FCM (Android) o APNs (iOS)
- El worker se destruye automáticamente si no hay polling por >5 min (cliente desconectado)

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

---

### 3. `POST /monitoreo/live-entries/stop`
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

### 4. `POST /monitoreo/live-entries/outcome`
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

## Módulo Python nuevo: `live_engine.py`

Port de los módulos TypeScript shared:

| TypeScript (shared) | Python (nuevo) |
|---|---|
| `buildBacktestEntries.ts` | `live_engine.py::build_live_entries()` |
| `calcSRLevelsLive()` | `live_engine.py::calc_sr_levels_live()` — usa `_INDICATORS_CACHE` existente |
| `areCandlesFresh()` | `live_engine.py::are_candles_fresh()` — comparar timestamp |
| `generateLiveEntries()` | `live_engine.py::generate_live_entries()` — wrapper |
| Training data / MTResumen | Ya en `_INDICATORS_CACHE` del backend |

**Firma de `generate_live_entries`:**
```python
def generate_live_entries(
    symbol: str,
    tf: str,
    candles: list[dict],           # OHLCV ms
    training_data: dict | None,    # MTResumen equivalente
    events: list[dict] | None,     # Eventos económicos
    live_window: int = 3,
    platform: str = "BACKEND",
) -> list[dict]:                   # Lista de LiveEntry
```

---

## Lo que NO cambia en RN/Web

- Toda la UI: filtros, chips, DetalleEjecucion, GlobalEntriesModal — igual
- Los hooks de monitoreo solo cambian la **fuente** de datos
- `liveEntriesByTf` sigue siendo el estado central — ahora se llena desde el endpoint en vez de local
- El outcome checker sigue en el cliente (o puede migrar al backend en fase 2)

```typescript
// Hoy
generateLiveEntries(candles) → setLiveEntriesByTf(...)

// Nuevo
GET /monitoreo/live-entries → setLiveEntriesByTf(...)
```

---

## Fases de implementación

### Fase 1 — Backend worker + endpoint (sin tocar RN/Web)
- [ ] `live_engine.py` — port de buildBacktestEntries + calcSRLevelsLive
- [ ] Worker asyncio por exec_id+symbol
- [ ] Redis TTL para entradas
- [ ] Endpoints: start / GET / stop / outcome
- [ ] Tests con BTCUSD en local

### Fase 2 — Migrar RN/Web al nuevo endpoint
- [ ] Hook `useLiveEntriesBackend` que consume GET polling
- [ ] Reemplazar `generateLiveEntries` local por el hook
- [ ] Eliminar descarga local de candles para generación de entradas
- [ ] Mantener descarga local solo para la UI (charts, etc.)

### Fase 3 — Push notifications
- [ ] FCM para Android
- [ ] APNs para iOS
- [ ] Configuración en backend

---

## Dependencias del backend

| Dependencia | Estado |
|---|---|
| FMP API para candles | ✅ Ya implementado (`_fetch_historical_range`) |
| GCS para seed S/R | ✅ Ya implementado (`_maybe_refresh_from_gcs`) |
| Training data / niveles | ✅ Ya en `_INDICATORS_CACHE` |
| Eventos económicos | ✅ Ya implementado (`_fetch_events_for`) |
| Redis para TTL entradas | ✅ Ya corriendo |
| `live_engine.py` | ❌ Pendiente |
| Worker asyncio | ❌ Pendiente |
| FCM/APNs push | ❌ Pendiente (Fase 3) |
