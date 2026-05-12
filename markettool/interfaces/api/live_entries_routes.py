"""
Live Entries Worker — Fase 1
============================
Genera entradas en vivo en el backend por cada (exec_id, symbol, tf).
Usa los mismos candles/niveles que ya tiene el backend para backtest.

Contrato API:
  POST /monitoreo/live-entries/start
  GET  /monitoreo/live-entries
  POST /monitoreo/live-entries/stop
  POST /monitoreo/live-entries/outcome
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from typing import Any

import pandas as pd

logger = logging.getLogger("MarketTool")

# ─────────────────────────────────────────────
# TTL y constantes
# ─────────────────────────────────────────────
ENTRY_TTL_S = 300          # 5 min en Redis
WORKER_IDLE_TIMEOUT_S = 300  # mata el worker si nadie hace GET en 5 min
LIVE_WINDOW = 3              # candles finales a evaluar (homologa generateLiveEntries)
MIN_CANDLES = 30

# Intervalo de beat por TF — homologa TF_POLL_MS del cliente (Web/RN)
TF_BEAT_S: dict[str, float] = {
    "1m":   5.0,
    "1min": 5.0,
    "5m":   20.0,
    "5min": 20.0,
    "15m":  45.0,
    "15min": 45.0,
    "30m":  90.0,
    "30min": 90.0,
    "1h":   180.0,
    "1hour": 180.0,
    "4h":   600.0,
    "4hour": 600.0,
    "1d":   6_000.0,
    "1day": 6_000.0,
    "1w":   18_000.0,
    "1week": 18_000.0,
}
_DEFAULT_BEAT_S = 60.0

# ─────────────────────────────────────────────
# Estado global de workers
# ─────────────────────────────────────────────
_WORKERS: dict[str, asyncio.Task] = {}          # worker_id → Task
_WORKER_LAST_POLL: dict[str, float] = {}        # worker_id → ts último GET
_WORKER_LOCK = asyncio.Lock()


def _worker_id(exec_id: str, symbol: str) -> str:
    return f"{exec_id}__{symbol.upper()}"


# ─────────────────────────────────────────────
# Helpers Redis
# ─────────────────────────────────────────────

def _redis_entries_key(exec_id: str, symbol: str, tf: str) -> str:
    return f"live_entries:{exec_id}:{symbol.upper()}:{tf}"


def _redis_beat_key(exec_id: str, symbol: str) -> str:
    return f"live_beat:{exec_id}:{symbol.upper()}"


def _push_entries_to_redis(redis_client, exec_id: str, symbol: str, tf: str, entries: list[dict]):
    """Persiste entradas nuevas en Redis con TTL. Dedup por id."""
    key = _redis_entries_key(exec_id, symbol, tf)
    existing_raw = redis_client.get(key)
    existing: list[dict] = json.loads(existing_raw) if existing_raw else []

    existing_ids = {e["id"] for e in existing}
    new_entries = [e for e in entries if e["id"] not in existing_ids]
    if not new_entries:
        return 0

    merged = existing + new_entries
    # mantener solo las últimas 100 entradas por TF
    if len(merged) > 100:
        merged = merged[-100:]

    redis_client.setex(key, ENTRY_TTL_S, json.dumps(merged))
    return len(new_entries)


def _get_entries_from_redis(redis_client, exec_id: str, symbol: str, tfs: list[str], since_ts: int | None) -> list[dict]:
    result = []
    for tf in tfs:
        key = _redis_entries_key(exec_id, symbol, tf)
        raw = redis_client.get(key)
        if not raw:
            continue
        entries: list[dict] = json.loads(raw)
        if since_ts:
            entries = [e for e in entries if e.get("timestamp", 0) > since_ts]
        result.extend(entries)
    return result


def _set_outcome_in_redis(redis_client, exec_id: str, symbol: str, tfs: list[str], entry_id: str, outcome: str, close_price: float, closed_at: str):
    for tf in tfs:
        key = _redis_entries_key(exec_id, symbol, tf)
        raw = redis_client.get(key)
        if not raw:
            continue
        entries: list[dict] = json.loads(raw)
        updated = False
        for e in entries:
            if e["id"] == entry_id:
                e["outcome"] = outcome
                e["close_price"] = close_price
                e["closed_at"] = closed_at
                updated = True
        if updated:
            redis_client.setex(key, ENTRY_TTL_S, json.dumps(entries))


# ─────────────────────────────────────────────
# Motor de entradas live — corazón del worker
# ─────────────────────────────────────────────

def _series_to_df(series_ms: list[dict]) -> pd.DataFrame:
    """Convierte la serie OHLCV del mon_cache a DataFrame."""
    if not series_ms:
        return pd.DataFrame()
    df = pd.DataFrame(series_ms)
    # normalizar columnas
    rename = {"t": "timestamp", "o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"}
    df = df.rename(columns={k: v for k, v in rename.items() if k in df.columns})
    for col in ["open", "high", "low", "close", "volume"]:
        if col not in df.columns:
            df[col] = 0.0
    if "timestamp" in df.columns:
        df["timestamp"] = df["timestamp"].apply(lambda x: x if x > 1e12 else x * 1000)
        df = df.sort_values("timestamp").reset_index(drop=True)
        df.index = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    return df


def _are_candles_fresh(series_ms: list[dict], tf: str, tf_ms_fn) -> bool:
    """Homologa areCandlesFresh del cliente."""
    if not series_ms:
        return False
    last = series_ms[-1]
    raw_ts = last.get("t") or last.get("timestamp") or 0
    last_ts = raw_ts if raw_ts > 1e12 else raw_ts * 1000
    tf_ms = tf_ms_fn(tf) or 60_000
    max_age = tf_ms * 5
    age = (time.time() * 1000) - last_ts
    return age < max_age


def _extract_sr_seed(niveles: dict | None) -> dict | None:
    """Extrae el seed GCS S/R del dict de niveles del backend."""
    if not niveles:
        return None
    s1 = niveles.get("soporte_nivel_1")
    s2 = niveles.get("soporte_nivel_2")
    r1 = niveles.get("resistencia_nivel_1")
    r2 = niveles.get("resistencia_nivel_2")
    if s1 and r1:
        return {"s1": s1, "s2": s2, "r1": r1, "r2": r2}
    return None


def _build_entry_id(symbol: str, tf: str, side: str, ts: int) -> str:
    return f"live|{symbol}|{tf}|{side}|{ts}"


def _generate_live_entries_sync(
    symbol: str,
    tf: str,
    series_ms: list[dict],
    niveles: dict | None,
    df_eventos: pd.DataFrame,
    calcular_entradas_sync_wrapper,
    norm_tf_fn,
) -> tuple[list[dict], dict]:
    """
    Genera entradas live para un symbol+tf usando el motor Python existente.
    Evalúa solo los últimos LIVE_WINDOW candles (homologa liveWindow=3 del cliente).
    Retorna (entradas, sr_levels).
    """
    if len(series_ms) < MIN_CANDLES:
        return [], {}

    if not _are_candles_fresh(series_ms, tf, lambda t: {
        "1m": 60_000, "1min": 60_000,
        "5m": 300_000, "5min": 300_000,
        "15m": 900_000, "15min": 900_000,
        "30m": 1_800_000, "30min": 1_800_000,
        "1h": 3_600_000, "1hour": 3_600_000,
        "4h": 14_400_000, "4hour": 14_400_000,
        "1d": 86_400_000, "1day": 86_400_000,
    }.get(norm_tf_fn(t), 60_000)):
        logger.info("[LiveWorker] %s/%s candles stale — skip", symbol, tf)
        return [], {}

    # Usar solo los últimos LIVE_WINDOW candles como "ventana viva"
    # pero pasar todo el histórico para cálculo de S/R
    df = _series_to_df(series_ms)
    if df.empty or len(df) < MIN_CANDLES:
        return [], {}

    sr_levels = _extract_sr_seed(niveles) or {}

    try:
        result = calcular_entradas_sync_wrapper(
            df,
            df_eventos if df_eventos is not None else pd.DataFrame(),
            symbol,
            tf,
            cfg={"niveles": niveles or {}, "live_window": LIVE_WINDOW},
        )
    except Exception as exc:
        logger.warning("[LiveWorker] calcular_entradas_sync_wrapper error %s/%s: %s", symbol, tf, exc)
        return [], sr_levels

    # Extraer niveles del resultado si están disponibles
    if isinstance(result, dict):
        lvl = result.get("niveles") or result.get("levels") or {}
        if lvl:
            sr_levels = {
                "s1": lvl.get("soporte_nivel_1") or sr_levels.get("s1"),
                "s2": lvl.get("soporte_nivel_2") or sr_levels.get("s2"),
                "r1": lvl.get("resistencia_nivel_1") or sr_levels.get("r1"),
                "r2": lvl.get("resistencia_nivel_2") or sr_levels.get("r2"),
            }

    # Convertir señales a LiveEntry format
    entradas_raw: list[dict] = []
    if isinstance(result, dict):
        entradas_raw = result.get("entradas") or result.get("resumen_senal") or result.get("resumen") or []
    elif isinstance(result, list):
        entradas_raw = result

    now_ts = int(time.time() * 1000)
    entries: list[dict] = []
    for e in entradas_raw:
        side = e.get("tipo_operacion") or e.get("side") or e.get("direction", "")
        if isinstance(side, str):
            side = "long" if "compra" in side.lower() or "long" in side.lower() or "buy" in side.lower() else "short"
        entry_price = e.get("precio_entrada") or e.get("entry_price") or e.get("precio") or 0.0
        tp = e.get("take_profit") or e.get("tp") or 0.0
        sl = e.get("stop_loss") or e.get("sl") or 0.0
        rrr = 0.0
        if entry_price and tp and sl and abs(entry_price - sl) > 0:
            rrr = round(abs(tp - entry_price) / abs(entry_price - sl), 2)

        entry_ts = e.get("timestamp") or now_ts
        entry_id = _build_entry_id(symbol, tf, side, entry_ts)

        entries.append({
            "id": entry_id,
            "symbol": symbol,
            "timeframe": tf,
            "side": side,
            "entry_price": float(entry_price),
            "take_profit": float(tp),
            "stop_loss": float(sl),
            "rrr": rrr,
            "confluence_score": int(e.get("confluencia") or e.get("confluence_score") or e.get("score") or 0),
            "source": e.get("fuente") or e.get("source") or "BACKEND",
            "outcome": "pending",
            "_origin": "live",
            "created_at": pd.Timestamp.utcnow().isoformat() + "Z",
            "timestamp": entry_ts,
            "nivel_confirmado": bool(e.get("nivel_confirmado") or e.get("confirmado")),
            "dentro_rango": bool(e.get("dentro_rango") or e.get("en_rango")),
            "early_detection": False,
            "sr_levels": sr_levels,
        })

    logger.info("[LiveWorker] %s/%s → %d entradas generadas", symbol, tf, len(entries))
    return entries, sr_levels


# ─────────────────────────────────────────────
# Worker asyncio por exec_id+symbol
# ─────────────────────────────────────────────

async def _live_worker(
    worker_key: str,
    exec_id: str,
    symbol: str,
    tfs: list[str],
    redis_client,
    load_cache,
    maybe_refresh_from_gcs,
    fetch_historical_range,
    fetch_events_for,
    norm_tf_fn,
    tf_ms_fn,
    current_closed_bucket_start,
    calcular_entradas_sync_wrapper,
):
    """
    Worker asyncio. Lanza un sub-task por TF, cada uno con su propio intervalo.
    Homologa TF_POLL_MS del cliente (1m=5s, 5m=20s, 15m=45s, ...).
    Se cancela entero cuando se llama /stop o se alcanza idle timeout.
    """
    logger.info("[LiveWorker] START %s tfs=%s", worker_key, tfs)

    async def _run_beat(tf: str):
        try:
            norm = norm_tf_fn(tf)

            # 1. Candles del mon_cache
            st: dict = await asyncio.to_thread(load_cache, exec_id, symbol, norm)
            series_ms: list[dict] = st.get("series") or []

            # 2. Refrescar GCS seed
            await asyncio.to_thread(maybe_refresh_from_gcs, exec_id, symbol, norm, st)
            series_ms = st.get("series") or series_ms

            # 3. Fallback FMP si faltan candles
            if len(series_ms) < MIN_CANDLES:
                tf_ms_val = tf_ms_fn(norm) or 60_000
                closed_end = current_closed_bucket_start(norm) - tf_ms_val
                from_ms = closed_end - 300 * tf_ms_val
                hist = await asyncio.to_thread(fetch_historical_range, symbol, norm, from_ms, closed_end)
                if hist:
                    series_ms = hist

            if len(series_ms) < MIN_CANDLES:
                logger.info("[LiveWorker] %s/%s sin candles suficientes (%d)", symbol, norm, len(series_ms))
                return

            # 4. Niveles del indicators cache
            niveles: dict | None = None
            try:
                from markettool.infra.cache.indicators_cache import _INDICATORS_CACHE
                cached = _INDICATORS_CACHE.get(symbol, norm)
                if cached and isinstance(cached, dict):
                    niveles = cached.get("niveles") or cached.get("levels")
            except Exception:
                pass

            # 5. Eventos económicos
            df_eventos = pd.DataFrame()
            try:
                events_raw = await asyncio.to_thread(fetch_events_for, symbol, hours_back=6)
                if events_raw:
                    df_eventos = pd.DataFrame(events_raw) if isinstance(events_raw, list) else events_raw
            except Exception:
                pass

            # 6. Generar entradas
            entries, sr_levels = await asyncio.to_thread(
                _generate_live_entries_sync,
                symbol, norm, series_ms, niveles, df_eventos,
                calcular_entradas_sync_wrapper, norm_tf_fn,
            )

            # 7. Persistir en Redis
            if entries:
                added = _push_entries_to_redis(redis_client, exec_id, symbol, norm, entries)
                if added:
                    logger.info("[LiveWorker] %s/%s +%d nuevas entradas", symbol, norm, added)

            # 8. Actualizar beat timestamp + sr_levels en Redis
            try:
                beat_key = _redis_beat_key(exec_id, symbol)
                beat_raw = redis_client.get(beat_key)
                beat_data: dict = json.loads(beat_raw) if beat_raw else {"tfs": tfs, "symbol": symbol, "sr_levels": {}}
                beat_data["ts"] = int(time.time() * 1000)
                beat_data["sr_levels"][norm] = sr_levels
                redis_client.setex(beat_key, WORKER_IDLE_TIMEOUT_S, json.dumps(beat_data))
            except Exception:
                pass

        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("[LiveWorker] Error beat %s/%s: %s", symbol, tf, exc, exc_info=True)

    async def _tf_beat_loop(tf: str):
        """Loop independiente por TF con su propio intervalo."""
        interval_s = TF_BEAT_S.get(norm_tf_fn(tf), _DEFAULT_BEAT_S)
        # Primer beat inmediato (seed sin stagger, igual que el cliente)
        await _run_beat(tf)
        while True:
            await asyncio.sleep(interval_s)
            await _run_beat(tf)

    # Lanzar un task por TF con stagger de 1s entre ellos
    tf_tasks: list[asyncio.Task] = []
    for i, tf in enumerate(tfs):
        if i > 0:
            await asyncio.sleep(1.0)
        task = asyncio.get_event_loop().create_task(_tf_beat_loop(tf))
        tf_tasks.append(task)

    # Idle monitor — mata todo si nadie hace GET en WORKER_IDLE_TIMEOUT_S
    try:
        while True:
            await asyncio.sleep(30)
            last_poll = _WORKER_LAST_POLL.get(worker_key, time.time())
            if time.time() - last_poll > WORKER_IDLE_TIMEOUT_S:
                logger.info("[LiveWorker] IDLE TIMEOUT %s — stopping", worker_key)
                break
    except asyncio.CancelledError:
        pass
    finally:
        for t in tf_tasks:
            t.cancel()
        logger.info("[LiveWorker] STOP %s", worker_key)
        async with _WORKER_LOCK:
            _WORKERS.pop(worker_key, None)
            _WORKER_LAST_POLL.pop(worker_key, None)


# ─────────────────────────────────────────────
# Registro de rutas Flask
# ─────────────────────────────────────────────
# ─────────────────────────────────────────────
# Registro de rutas Flask
# ─────────────────────────────────────────────

def register_live_entries_routes(app, *, services) -> None:
    from flask import jsonify, request

    load_cache = services.load_cache
    maybe_refresh_from_gcs = services.maybe_refresh_from_gcs
    fetch_historical_range = services.fetch_historical_range
    fetch_events_for = services.fetch_events_for
    norm_tf = services.norm_tf
    tf_ms = services.tf_ms
    current_closed_bucket_start = services.current_closed_bucket_start
    mon_cache_lock = services.mon_cache_lock

    # Redis client
    try:
        from markettool.infra.cache.redis_cache import get_redis_client
        redis_client = get_redis_client()
    except Exception:
        redis_client = None
        logger.warning("[LiveEntries] Redis no disponible — entradas no se persistirán")

    # calcular_entradas_sync_wrapper
    try:
        from MarketTool import calcular_entradas_sync_wrapper
    except ImportError:
        calcular_entradas_sync_wrapper = None
        logger.warning("[LiveEntries] calcular_entradas_sync_wrapper no disponible")

    # ── POST /monitoreo/live-entries/start ──────────────────────────────────
    @app.route("/monitoreo/live-entries/start", methods=["POST"])
    async def live_entries_start():
        body = request.get_json(force=True) or {}
        exec_id = body.get("exec_id", "").strip()
        symbol = (body.get("symbol") or "").upper().strip()
        tfs_raw: list[str] = body.get("tfs") or []
        platform = body.get("platform", "UNKNOWN")

        if not exec_id or not symbol or not tfs_raw:
            return jsonify({"error": "exec_id, symbol y tfs son requeridos"}), 400

        tfs = [norm_tf(t) for t in tfs_raw]
        wid = _worker_id(exec_id, symbol)

        async with _WORKER_LOCK:
            if wid in _WORKERS and not _WORKERS[wid].done():
                _WORKER_LAST_POLL[wid] = time.time()
                return jsonify({"ok": True, "worker_id": wid, "status": "already_running"}), 200

            loop = asyncio.get_event_loop()
            task = loop.create_task(
                _live_worker(
                    worker_key=wid,
                    exec_id=exec_id,
                    symbol=symbol,
                    tfs=tfs,
                    redis_client=redis_client,
                    load_cache=load_cache,
                    maybe_refresh_from_gcs=maybe_refresh_from_gcs,
                    fetch_historical_range=fetch_historical_range,
                    fetch_events_for=fetch_events_for,
                    norm_tf_fn=norm_tf,
                    tf_ms_fn=tf_ms,
                    current_closed_bucket_start=current_closed_bucket_start,
                    calcular_entradas_sync_wrapper=calcular_entradas_sync_wrapper,
                )
            )
            _WORKERS[wid] = task
            _WORKER_LAST_POLL[wid] = time.time()

        logger.info("[LiveEntries] Worker iniciado: %s platform=%s tfs=%s", wid, platform, tfs)
        return jsonify({"ok": True, "worker_id": wid, "status": "started"}), 200

    # ── GET /monitoreo/live-entries ─────────────────────────────────────────
    @app.route("/monitoreo/live-entries", methods=["GET"])
    async def live_entries_get():
        exec_id = request.args.get("exec_id", "").strip()
        symbol = (request.args.get("symbol") or "").upper().strip()
        tfs_raw = request.args.get("tfs", "")
        since_ts_raw = request.args.get("since_ts")

        if not exec_id or not symbol:
            return jsonify({"error": "exec_id y symbol son requeridos"}), 400

        tfs = [norm_tf(t) for t in tfs_raw.split(",") if t.strip()] if tfs_raw else []
        since_ts = int(since_ts_raw) if since_ts_raw else None
        wid = _worker_id(exec_id, symbol)

        # Actualizar last poll para evitar idle timeout
        _WORKER_LAST_POLL[wid] = time.time()

        if not redis_client:
            return jsonify({"error": "Redis no disponible"}), 503

        # Entradas del Redis
        entries = _get_entries_from_redis(redis_client, exec_id, symbol, tfs, since_ts)

        # Beat info + sr_levels
        beat_raw = redis_client.get(_redis_beat_key(exec_id, symbol))
        beat_data: dict = json.loads(beat_raw) if beat_raw else {}
        last_beat_ts = beat_data.get("ts", 0)
        sr_levels = beat_data.get("sr_levels", {})

        # candles_count por TF
        candles_count: dict[str, int] = {}
        for tf in tfs:
            try:
                st = await asyncio.to_thread(load_cache, exec_id, symbol, tf)
                candles_count[tf] = len(st.get("series") or [])
            except Exception:
                candles_count[tf] = 0

        # worker status
        worker_status = "stopped"
        if wid in _WORKERS:
            worker_status = "running" if not _WORKERS[wid].done() else "stopped"

        return jsonify({
            "entries": entries,
            "last_beat_ts": last_beat_ts,
            "worker_status": worker_status,
            "candles_count": candles_count,
            "sr_levels": sr_levels,
        }), 200

    # ── POST /monitoreo/live-entries/stop ──────────────────────────────────
    @app.route("/monitoreo/live-entries/stop", methods=["POST"])
    async def live_entries_stop():
        body = request.get_json(force=True) or {}
        exec_id = body.get("exec_id", "").strip()
        symbol = (body.get("symbol") or "").upper().strip()

        if not exec_id or not symbol:
            return jsonify({"error": "exec_id y symbol son requeridos"}), 400

        wid = _worker_id(exec_id, symbol)
        async with _WORKER_LOCK:
            task = _WORKERS.pop(wid, None)
            _WORKER_LAST_POLL.pop(wid, None)
            if task and not task.done():
                task.cancel()
                logger.info("[LiveEntries] Worker detenido: %s", wid)
                return jsonify({"ok": True, "stopped": wid}), 200

        return jsonify({"ok": True, "stopped": wid, "was_running": False}), 200

    # ── POST /monitoreo/live-entries/outcome ───────────────────────────────
    @app.route("/monitoreo/live-entries/outcome", methods=["POST"])
    async def live_entries_outcome():
        body = request.get_json(force=True) or {}
        exec_id = body.get("exec_id", "").strip()
        symbol = (body.get("symbol") or "").upper().strip()
        entry_id = body.get("entry_id", "").strip()
        outcome = body.get("outcome", "").strip()
        close_price = float(body.get("close_price") or 0)
        closed_at = body.get("closed_at") or pd.Timestamp.utcnow().isoformat() + "Z"

        if not entry_id or outcome not in ("tp", "sl", "expired"):
            return jsonify({"error": "entry_id y outcome (tp|sl|expired) son requeridos"}), 400

        if not redis_client:
            return jsonify({"error": "Redis no disponible"}), 503

        # Buscar en todos los TFs conocidos para este exec+symbol
        beat_raw = redis_client.get(_redis_beat_key(exec_id, symbol))
        beat_data = json.loads(beat_raw) if beat_raw else {}
        tfs = beat_data.get("tfs") or []

        _set_outcome_in_redis(redis_client, exec_id, symbol, tfs, entry_id, outcome, close_price, closed_at)
        logger.info("[LiveEntries] Outcome %s → %s para %s", entry_id, outcome, symbol)
        return jsonify({"ok": True}), 200
