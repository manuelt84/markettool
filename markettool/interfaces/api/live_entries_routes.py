"""
Live Entries Worker — Fase 1
============================
Genera entradas en vivo en el backend por cada (exec_id, symbol, tf).
Usa los mismos candles/niveles que ya tiene el backend para backtest.

Contrato API:
  POST /monitoreo/live-entries/start
  GET  /monitoreo/live-entries
  GET  /monitoreo/live-entries/stream
  POST /monitoreo/live-entries/stop
  POST /monitoreo/live-entries/outcome
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import queue
import threading
import time
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from markettool.infra.fmp.ledger import fmp_context

logger = logging.getLogger("MarketTool")

# ─────────────────────────────────────────────
# TTL y constantes
# ─────────────────────────────────────────────
EVENT_REPLAY_TTL_S = 300     # 5 min para replay SSE corto
WORKER_IDLE_TIMEOUT_S = 300  # mata el worker si nadie hace GET en 5 min
WORKER_ZOMBIE_TIMEOUT_S = 900  # mata el worker si hay TFs activos pero nadie polling en 15 min
_POD_ID = os.environ.get("WORKER_ID", os.environ.get("HOSTNAME", "pod-unknown"))
LIVE_WINDOW = 3              # candles finales a evaluar (homologa generateLiveEntries)
MIN_CANDLES = 30

# Homologa LIVE_TTL_BY_TF de RN/Web para que el backend no expire señales antes
# que el cliente cuando se activa el push/poll backend.
ENTRY_TTL_BY_TF_S: dict[str, int] = {
    "1m": 30 * 60,
    "1min": 30 * 60,
    "5m": 2 * 3600,
    "5min": 2 * 3600,
    "15m": 6 * 3600,
    "15min": 6 * 3600,
    "30m": 12 * 3600,
    "30min": 12 * 3600,
    "1h": 24 * 3600,
    "1hour": 24 * 3600,
    "4h": 3 * 86400,
    "4hour": 3 * 86400,
    "1d": 7 * 86400,
    "1day": 7 * 86400,
    "1w": 30 * 86400,
    "1week": 30 * 86400,
}
_DEFAULT_ENTRY_TTL_S = 24 * 3600

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
_MEM_ENTRIES: dict[str, list[dict]] = {}        # fallback when Redis is unavailable
_MEM_EXPIRY: dict[str, float] = {}
_MEM_BEATS: dict[str, dict] = {}
_MEM_EVENTS: dict[str, list[dict]] = {}         # short replay buffer for SSE when Redis is unavailable
_MEM_BILLING_KEYS: dict[str, float] = {}
_MEM_MARKET_POOL: dict[str, dict] = {}
_MEM_MARKET_POOL_RATE: dict[str, int] = {}
_MEM_MARKET_POOL_COOLDOWN: dict[str, float] = {}
_MARKET_POOL_TASK: asyncio.Task | None = None
_SUBSCRIBERS: dict[str, list[queue.Queue]] = {}
_SUBSCRIBERS_LOCK = threading.Lock()


def _worker_id(exec_id: str, symbol: str) -> str:
    return f"{exec_id}__{symbol.upper()}"


# ─────────────────────────────────────────────
# Helpers Redis
# ─────────────────────────────────────────────

def _redis_entries_key(exec_id: str, symbol: str, tf: str) -> str:
    return f"live_entries:{exec_id}:{symbol.upper()}:{tf}"


def _redis_beat_key(exec_id: str, symbol: str) -> str:
    return f"live_beat:{exec_id}:{symbol.upper()}"


def _redis_events_channel(exec_id: str, symbol: str) -> str:
    return f"live_entries_events:{exec_id}:{symbol.upper()}"


def _redis_event_log_key(exec_id: str, symbol: str) -> str:
    return f"live_entries_event_log:{exec_id}:{symbol.upper()}"


def _redis_worker_touch_key(worker_key: str) -> str:
    return f"live_worker_touch:{worker_key}"


def _redis_worker_owner_key(worker_key: str) -> str:
    return f"live_worker_owner:{worker_key}"


def _redis_worker_stop_key(worker_key: str) -> str:
    return f"live_worker_stop:{worker_key}"


def _redis_billing_key(user_id: str, exec_id: str, symbol: str, tf: str, bucket_ms: int, source: str) -> str:
    safe_source = str(source or "cache").replace(":", "_")
    return f"live_data_billed:{user_id}:{exec_id}:{symbol.upper()}:{tf}:{bucket_ms}:{safe_source}"


def _redis_market_pool_key(symbol: str, tf: str) -> str:
    return f"market_pool:candles:{symbol.upper()}:{tf}"


def _redis_market_pool_lock_key(symbol: str, tf: str) -> str:
    return f"market_pool:lock:{symbol.upper()}:{tf}"


def _redis_market_pool_cooldown_key(symbol: str, tf: str) -> str:
    return f"market_pool:cooldown:{symbol.upper()}:{tf}"


def _redis_market_pool_rate_key() -> str:
    return f"market_pool:fmp_calls:{int(time.time() // 60)}"


def _event_id(exec_id: str, symbol: str, tf: str, ts_ms: int) -> str:
    base = f"{exec_id}|{symbol.upper()}|{tf}|{ts_ms}"
    return hashlib.sha1(base.encode("utf-8")).hexdigest()[:16]


def _entry_ttl_s(tf: str) -> int:
    return ENTRY_TTL_BY_TF_S.get(str(tf or "").lower(), _DEFAULT_ENTRY_TTL_S)


def _norm_tf_for_fp(tf: Any) -> str:
    value = str(tf or "").strip().lower()
    value = value.replace("minute", "min").replace("minutes", "min")
    value = value.replace("hour", "h").replace("hours", "h")
    value = value.replace("day", "d").replace("days", "d")
    value = value.replace("week", "w").replace("weeks", "w")
    if value.endswith("min"):
        value = value[:-3] + "m"
    return value


def _norm_symbol_for_fp(symbol: Any) -> str:
    return str(symbol or "").replace("/", "").strip().upper()


def _norm_side_for_fp(side: Any) -> str:
    value = str(side or "").strip().lower()
    if value in {"long", "buy", "compra"}:
        return "long"
    if value in {"short", "sell", "venta"}:
        return "short"
    return value


def _price_ticks(value: Any) -> str:
    try:
        return str(round(float(value) * 1e5))
    except Exception:
        return "na"


def _time_bucket(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (int, float)):
        if value <= 0:
            return ""
        ms = int(value if value > 1e12 else value * 1000)
        # Bucket de 5 minutos para deduplicar entradas idénticas regeneradas
        return str(ms // 300_000)
    raw = str(value).strip()
    if not raw:
        return ""
    try:
        parsed = pd.Timestamp(raw)
        if not pd.isna(parsed):
            ms = int(parsed.timestamp() * 1000)
            return str(ms // 300_000)
    except Exception:
        pass
    return raw


def _entry_fingerprint(entry: dict) -> str:
    entry_price = entry.get("entry_price", entry.get("entry", entry.get("precio")))
    take_profit = entry.get("take_profit", entry.get("tp"))
    stop_loss = entry.get("stop_loss", entry.get("sl"))
    ts = entry.get("timestamp", entry.get("created_at", entry.get("createdAt")))
    return "|".join([
        _norm_symbol_for_fp(entry.get("symbol")),
        _norm_tf_for_fp(entry.get("timeframe", entry.get("tf"))),
        str(entry.get("source") or "").strip().lower(),
        _norm_side_for_fp(entry.get("side")),
        _price_ticks(entry_price),
        _price_ticks(take_profit),
        _price_ticks(stop_loss),
        _time_bucket(ts),
    ])


def _entry_created_ms(entry: dict) -> int:
    for value in (entry.get("created_at"), entry.get("createdAt"), entry.get("timestamp")):
        if value is None:
            continue
        if isinstance(value, (int, float)):
            return int(value if value > 1e12 else value * 1000)
        try:
            parsed = pd.Timestamp(str(value))
            if not pd.isna(parsed):
                return int(parsed.timestamp() * 1000)
        except Exception:
            continue
    return int(time.time() * 1000)


def _is_entry_expired(entry: dict, tf: str, now_ms: int | None = None) -> bool:
    now_ms = now_ms or int(time.time() * 1000)
    return now_ms - _entry_created_ms(entry) > _entry_ttl_s(tf) * 1000


def _dedupe_entries(entries: list[dict]) -> list[dict]:
    seen: set[str] = set()
    result: list[dict] = []
    for entry in entries:
        fp = _entry_fingerprint(entry)
        if fp in seen:
            continue
        seen.add(fp)
        result.append(entry)
    return result


def _persist_entries(redis_client, key: str, entries: list[dict], ttl_s: int) -> None:
    if redis_client is None:
        _MEM_ENTRIES[key] = entries
        _MEM_EXPIRY[key] = time.time() + ttl_s
        return
    if entries:
        redis_client.setex(key, ttl_s, json.dumps(entries))
    else:
        redis_client.delete(key)


def _normalize_source(raw: Any) -> str:
    """Mapea fuentes legacy del motor Python a los SourceKey compartidos RN/Web."""
    value = str(raw or "").strip().lower()
    if not value:
        return "mt"

    direct = {
        "soporte_resistencia": "sr",
        "sr": "sr",
        "tecnico": "tech",
        "technical": "tech",
        "tech": "tech",
        "market_tool": "mt",
        "markettool": "mt",
        "mt": "mt",
        "evento": "event",
        "eventos": "event",
        "event": "event",
        "fibonacci": "fibonacci",
        "fib": "fibonacci",
        "breaker": "breaker",
        "inducement": "inducement",
        "divergence": "divergence",
        "divergencia": "divergence",
        "confluence": "confluence",
        "confluencia": "confluence",
        "ob": "ob",
        "order_block": "ob",
        "order_blocks": "ob",
        "fvg": "fvg",
        "smc": "smc",
        "ema_cross_3_9": "ema_cross_3_9",
        "triada": "triada",
        "engulfing_reclaim": "engulfing_reclaim",
        "inside_bar_breakout": "inside_bar_breakout",
        "opening_reclaim": "opening_reclaim",
        "pinbar_reversal": "pinbar_reversal",
        "three_bar_reversal": "three_bar_reversal",
    }
    if value in direct:
        return direct[value]

    # Orden intencional: solo pullback_S1/S2 y breakout_R1/R2 requieren SR confirmado.
    # midpoint, scale_in, range_, reversion, bollinger son patrones tecnicos que no requieren SR.
    patterns: tuple[tuple[tuple[str, ...], str], ...] = (
        (("pullback_s1", "pullback_s2", "breakout_r1", "breakout_r2"), "sr"),
        (("pullback", "midpoint", "scale_in", "range_", "reversion", "bollinger"), "tech"),
        (("r1", "r2", "s1", "s2"), "sr"),
        (("fibonacci", "fib_"), "fibonacci"),
        (("breaker",), "breaker"),
        (("inducement",), "inducement"),
        (("diverg",), "divergence"),
        (("conflu",), "confluence"),
        (("order_block", "orderblock"), "ob"),
        (("fvg", "imbalance"), "fvg"),
        (("smc", "smart_money"), "smc"),
        (("ema_cross_3_9", "ema_3_9"), "ema_cross_3_9"),
        (("triada",), "triada"),
        (("engulfing_reclaim",), "engulfing_reclaim"),
        (("inside_bar_breakout",), "inside_bar_breakout"),
        (("opening_reclaim",), "opening_reclaim"),
        (("pinbar",), "pinbar_reversal"),
        (("three_bar",), "three_bar_reversal"),
        (("event", "news", "economic", "calendar"), "event"),
        (("ema", "macd", "rsi", "bollinger", "stoch", "momentum", "breakout", "breakdown"), "tech"),
    )
    for needles, source in patterns:
        if any(needle in value for needle in needles):
            return source
    return "mt"


def _store_event_for_replay(redis_client, exec_id: str, symbol: str, event: dict) -> None:
    key = _redis_event_log_key(exec_id, symbol)
    if redis_client is None:
        events = (_MEM_EVENTS.get(key) or [])[-49:] + [event]
        _MEM_EVENTS[key] = events
        return
    try:
        redis_client.rpush(key, json.dumps(event, separators=(",", ":")))
        redis_client.ltrim(key, -50, -1)
        redis_client.expire(key, EVENT_REPLAY_TTL_S)
    except Exception as exc:
        logger.warning("[LiveEntries] Redis replay log failed %s: %s", key, exc)


def _get_events_for_replay(redis_client, exec_id: str, symbol: str, last_event_id: str | None, tfs_filter: set[str], norm_tf_fn) -> list[dict]:
    if not last_event_id:
        return []
    key = _redis_event_log_key(exec_id, symbol)
    if redis_client is None:
        events = list(_MEM_EVENTS.get(key) or [])
    else:
        try:
            events = [json.loads(raw) for raw in (redis_client.lrange(key, 0, -1) or [])]
        except Exception:
            events = []
    found = False
    replay: list[dict] = []
    for event in events:
        if event.get("id") == last_event_id:
            found = True
            continue
        if not found:
            continue
        tf = norm_tf_fn(event.get("timeframe") or event.get("tf") or "")
        if not tfs_filter or tf in tfs_filter:
            replay.append(event)
    return replay


def _push_entries_to_redis(redis_client, exec_id: str, symbol: str, tf: str, entries: list[dict]) -> list[dict]:
    """Persiste entradas nuevas con TTL por entrada. Dedup igual que RN/Web."""
    ttl_s = _entry_ttl_s(tf)
    key = _redis_entries_key(exec_id, symbol, tf)
    now_ms = int(time.time() * 1000)
    if redis_client is None:
        existing = _MEM_ENTRIES.get(key, [])
        if time.time() >= _MEM_EXPIRY.get(key, 0):
            existing = []
        existing = _dedupe_entries([e for e in existing if not _is_entry_expired(e, tf, now_ms)])
        existing_fps = {_entry_fingerprint(e) for e in existing}
        new_entries = [e for e in _dedupe_entries(entries) if _entry_fingerprint(e) not in existing_fps]
        if not new_entries:
            _persist_entries(redis_client, key, existing, ttl_s)
            return []
        merged = _dedupe_entries(existing + new_entries)
        _persist_entries(redis_client, key, merged, ttl_s)
        return new_entries

    existing_raw = redis_client.get(key)
    existing: list[dict] = json.loads(existing_raw) if existing_raw else []
    existing = _dedupe_entries([e for e in existing if not _is_entry_expired(e, tf, now_ms)])

    existing_fps = {_entry_fingerprint(e) for e in existing}
    new_entries = [e for e in _dedupe_entries(entries) if _entry_fingerprint(e) not in existing_fps]
    if not new_entries:
        _persist_entries(redis_client, key, existing, ttl_s)
        return []

    merged = _dedupe_entries(existing + new_entries)
    _persist_entries(redis_client, key, merged, ttl_s)
    return new_entries


def _get_entries_from_redis(redis_client, exec_id: str, symbol: str, tfs: list[str], since_ts: int | None) -> list[dict]:
    result = []
    now_ms = int(time.time() * 1000)
    for tf in tfs:
        ttl_s = _entry_ttl_s(tf)
        key = _redis_entries_key(exec_id, symbol, tf)
        if redis_client is None:
            if time.time() >= _MEM_EXPIRY.get(key, 0):
                _MEM_ENTRIES.pop(key, None)
                _MEM_EXPIRY.pop(key, None)
                continue
            entries = list(_MEM_ENTRIES.get(key, []))
        else:
            raw = redis_client.get(key)
            if not raw:
                continue
            entries = json.loads(raw)
        entries = _dedupe_entries([e for e in entries if not _is_entry_expired(e, tf, now_ms)])
        _persist_entries(redis_client, key, entries, ttl_s)
        if since_ts:
            entries = [e for e in entries if _entry_created_ms(e) > since_ts]
        result.extend(entries)
    return _dedupe_entries(result)


def _set_outcome_in_redis(redis_client, exec_id: str, symbol: str, tfs: list[str], entry_id: str, outcome: str, close_price: float, closed_at: str):
    for tf in tfs:
        ttl_s = _entry_ttl_s(tf)
        key = _redis_entries_key(exec_id, symbol, tf)
        if redis_client is None:
            entries = list(_MEM_ENTRIES.get(key, []))
            if not entries:
                continue
        else:
            raw = redis_client.get(key)
            if not raw:
                continue
            entries = json.loads(raw)
        updated = False
        for e in entries:
            if e["id"] == entry_id:
                e["outcome"] = outcome
                e["close_price"] = close_price
                e["closed_at"] = closed_at
                updated = True
        if updated:
            if redis_client is None:
                _MEM_ENTRIES[key] = entries
                _MEM_EXPIRY[key] = time.time() + ttl_s
            else:
                redis_client.setex(key, ttl_s, json.dumps(entries))


def _set_beat(redis_client, exec_id: str, symbol: str, beat_data: dict) -> None:
    beat_data = dict(beat_data or {})
    beat_data["ts"] = int(time.time() * 1000)
    if redis_client is None:
        _MEM_BEATS[_redis_beat_key(exec_id, symbol)] = beat_data
        return
    redis_client.setex(_redis_beat_key(exec_id, symbol), WORKER_IDLE_TIMEOUT_S, json.dumps(beat_data))


def _get_beat(redis_client, exec_id: str, symbol: str) -> dict:
    key = _redis_beat_key(exec_id, symbol)
    if redis_client is None:
        return dict(_MEM_BEATS.get(key) or {})
    raw = redis_client.get(key)
    return json.loads(raw) if raw else {}


def _touch_worker(redis_client, worker_key: str) -> None:
    now = time.time()
    _WORKER_LAST_POLL[worker_key] = now
    if redis_client is not None:
        try:
            redis_client.setex(_redis_worker_touch_key(worker_key), WORKER_IDLE_TIMEOUT_S, str(now))
        except Exception as exc:
            logger.debug("[LiveEntries] touch worker redis failed %s: %s", worker_key, exc)


def _get_worker_last_touch(redis_client, worker_key: str) -> float:
    if redis_client is not None:
        try:
            raw = redis_client.get(_redis_worker_touch_key(worker_key))
            if raw:
                return float(raw)
        except Exception as exc:
            logger.debug("[LiveEntries] read worker touch redis failed %s: %s", worker_key, exc)
    return _WORKER_LAST_POLL.get(worker_key, time.time())


def _claim_worker_owner(redis_client, worker_key: str) -> bool:
    if redis_client is None:
        return True
    owner = _POD_ID
    try:
        redis_client.delete(_redis_worker_stop_key(worker_key))
        return bool(redis_client.set(_redis_worker_owner_key(worker_key), owner, nx=True, ex=WORKER_IDLE_TIMEOUT_S))
    except Exception as exc:
        logger.warning("[LiveEntries] Redis owner claim failed %s: %s", worker_key, exc)
        return True


def _refresh_worker_owner(redis_client, worker_key: str) -> None:
    if redis_client is None:
        return
    try:
        redis_client.expire(_redis_worker_owner_key(worker_key), WORKER_IDLE_TIMEOUT_S)
    except Exception as exc:
        logger.debug("[LiveEntries] Redis owner refresh failed %s: %s", worker_key, exc)


def _clear_worker_owner(redis_client, worker_key: str) -> None:
    if redis_client is None:
        return
    try:
        redis_client.delete(
            _redis_worker_owner_key(worker_key),
            _redis_worker_touch_key(worker_key),
            _redis_worker_stop_key(worker_key),
        )
    except Exception as exc:
        logger.debug("[LiveEntries] Redis owner clear failed %s: %s", worker_key, exc)


def _has_worker_owner(redis_client, worker_key: str) -> bool:
    if redis_client is None:
        return worker_key in _WORKERS and not _WORKERS[worker_key].done()
    try:
        return bool(redis_client.exists(_redis_worker_owner_key(worker_key)))
    except Exception as exc:
        logger.debug("[LiveEntries] Redis owner exists failed %s: %s", worker_key, exc)
        return worker_key in _WORKERS and not _WORKERS[worker_key].done()


def _worker_status(redis_client, worker_key: str, beat_data: dict | None = None) -> str:
    if worker_key in _WORKERS and not _WORKERS[worker_key].done():
        return "running"
    if _has_worker_owner(redis_client, worker_key):
        return "running"
    beat_ts = int((beat_data or {}).get("ts") or 0)
    if beat_ts and int(time.time() * 1000) - beat_ts <= WORKER_IDLE_TIMEOUT_S * 1000:
        return "running"
    return "stopped"


def _request_worker_stop(redis_client, worker_key: str) -> None:
    if redis_client is not None:
        try:
            redis_client.setex(_redis_worker_stop_key(worker_key), WORKER_IDLE_TIMEOUT_S, "1")
        except Exception as exc:
            logger.debug("[LiveEntries] Redis stop request failed %s: %s", worker_key, exc)


def _is_worker_stop_requested(redis_client, worker_key: str) -> bool:
    if redis_client is None:
        return False
    try:
        return bool(redis_client.exists(_redis_worker_stop_key(worker_key)))
    except Exception as exc:
        logger.debug("[LiveEntries] Redis stop read failed %s: %s", worker_key, exc)
        return False


def _subscribe_local(exec_id: str, symbol: str) -> tuple[str, queue.Queue]:
    """Suscripción in-process para entornos sin Redis o instancia única."""
    worker_key = _worker_id(exec_id, symbol)
    q: queue.Queue = queue.Queue(maxsize=100)
    with _SUBSCRIBERS_LOCK:
        _SUBSCRIBERS.setdefault(worker_key, []).append(q)
    return worker_key, q


def _unsubscribe_local(worker_key: str, q: queue.Queue) -> None:
    with _SUBSCRIBERS_LOCK:
        subscribers = _SUBSCRIBERS.get(worker_key) or []
        if q in subscribers:
            subscribers.remove(q)
        if subscribers:
            _SUBSCRIBERS[worker_key] = subscribers
        else:
            _SUBSCRIBERS.pop(worker_key, None)


def _publish_live_entries(redis_client, exec_id: str, symbol: str, tf: str, entries: list[dict]) -> None:
    """Publica nuevas entradas para clientes push sin acoplar RN/Web todavía."""
    if not entries:
        return

    ts_ms = int(time.time() * 1000)
    event = {
        "id": _event_id(exec_id, symbol, tf, ts_ms),
        "type": "entries",
        "exec_id": exec_id,
        "symbol": symbol.upper(),
        "timeframe": tf,
        "entries": entries,
        "ts": ts_ms,
    }
    _store_event_for_replay(redis_client, exec_id, symbol, event)

    if redis_client is not None:
        try:
            redis_client.publish(_redis_events_channel(exec_id, symbol), json.dumps(event))
            return
        except Exception as exc:
            logger.warning("[LiveEntries] Redis publish failed %s/%s: %s", symbol, tf, exc)

    worker_key = _worker_id(exec_id, symbol)
    with _SUBSCRIBERS_LOCK:
        subscribers = list(_SUBSCRIBERS.get(worker_key) or [])
    for q in subscribers:
        try:
            q.put_nowait(event)
        except queue.Full:
            logger.warning("[LiveEntries] subscriber queue full %s/%s — event skipped", symbol, tf)


def _normalize_candle_for_payload(candle: dict) -> dict | None:
    try:
        raw_t = candle.get("t", candle.get("timestamp", candle.get("time")))
        t_ms = int(raw_t if float(raw_t) > 1e12 else float(raw_t) * 1000)
        return {
            "t": t_ms,
            "o": float(candle.get("o", candle.get("open", 0)) or 0),
            "h": float(candle.get("h", candle.get("high", 0)) or 0),
            "l": float(candle.get("l", candle.get("low", 0)) or 0),
            "c": float(candle.get("c", candle.get("close", 0)) or 0),
            "v": float(candle.get("v", candle.get("volume", 0)) or 0),
        }
    except Exception:
        return None


def _build_candles_payload(series_ms: list[dict], tail: int = 240) -> dict:
    candles = [
        c for c in (_normalize_candle_for_payload(raw) for raw in (series_ms or [])[-tail:])
        if c is not None
    ]
    live_candle = candles[-1] if candles else None
    return {
        "candles": candles,
        "live_candle": live_candle,
        "last_ts": live_candle.get("t") if live_candle else 0,
        "count": len(series_ms or []),
    }


def _market_pool_ttl_s(tf: str) -> int:
    tf_norm = str(tf or "").lower()
    return max(int(os.getenv("MARKET_POOL_MIN_TTL_SECONDS", "60")), int(_DEFAULT_BEAT_S + ENTRY_TTL_BY_TF_S.get(tf_norm, 300) / 24))


def _market_pool_fresh(series_ms: list[dict], tf: str, tf_ms_fn) -> bool:
    if len(series_ms or []) < MIN_CANDLES:
        return False
    last = (series_ms or [])[-1]
    raw_ts = last.get("t") or last.get("timestamp") or 0
    try:
        last_ms = int(raw_ts if float(raw_ts) > 1e12 else float(raw_ts) * 1000)
    except Exception:
        return False
    tf_ms = tf_ms_fn(tf) or 60_000
    max_age = max(tf_ms * int(os.getenv("MARKET_POOL_MAX_AGE_BUCKETS", "4")), 90_000)
    return int(time.time() * 1000) - last_ms <= max_age


def _load_market_pool_series(redis_client, symbol: str, tf: str, tf_ms_fn) -> list[dict]:
    key = _redis_market_pool_key(symbol, tf)
    try:
        if redis_client is not None:
            raw = redis_client.get(key)
            if not raw:
                return []
            payload = json.loads(raw)
        else:
            payload = _MEM_MARKET_POOL.get(key) or {}
        series = payload.get("series") if isinstance(payload, dict) else payload
        series = [c for c in (_normalize_candle_for_payload(row) for row in (series or [])) if c is not None]
        if not _market_pool_fresh(series, tf, tf_ms_fn):
            return []
        return series
    except Exception as exc:
        logger.debug("[MarketPool] load failed %s/%s: %s", symbol, tf, exc)
        return []


def _store_market_pool_series(redis_client, symbol: str, tf: str, series_ms: list[dict]) -> None:
    series = [c for c in (_normalize_candle_for_payload(row) for row in (series_ms or [])) if c is not None]
    if not series:
        return
    tail = int(os.getenv("MARKET_POOL_TAIL", "360"))
    payload = {
        "symbol": symbol.upper(),
        "tf": tf,
        "series": series[-tail:],
        "updated_at": int(time.time() * 1000),
    }
    key = _redis_market_pool_key(symbol, tf)
    ttl_s = max(_market_pool_ttl_s(tf), 300)
    if redis_client is not None:
        redis_client.setex(key, ttl_s, json.dumps(payload, separators=(",", ":")))
    else:
        _MEM_MARKET_POOL[key] = payload


def _market_pool_acquire_fmp_slot(redis_client) -> bool:
    """Global FMP guard shared by all pool producers.

    FMP plan limit is currently 750 calls/min. The default leaves large headroom
    for user-triggered exclusive calls, analysis, events and retries.
    """
    limit = int(os.getenv("MARKET_POOL_GLOBAL_MAX_FMP_CALLS_PER_MIN", "300"))
    if limit <= 0:
        return False
    key = _redis_market_pool_rate_key()
    if redis_client is not None:
        try:
            count = int(redis_client.incr(key))
            if count == 1:
                redis_client.expire(key, 75)
            if count > limit:
                logger.debug("[MarketPool] global FMP/min limit reached count=%d limit=%d", count, limit)
                return False
            return True
        except Exception as exc:
            logger.debug("[MarketPool] redis rate limiter failed: %s", exc)

    now_min = str(int(time.time() // 60))
    count = int(_MEM_MARKET_POOL_RATE.get(now_min, 0)) + 1
    _MEM_MARKET_POOL_RATE.clear()
    _MEM_MARKET_POOL_RATE[now_min] = count
    return count <= limit


def _market_pool_cooldown_seconds(tf: str, tf_ms_fn=None) -> int:
    base = int(os.getenv("MARKET_POOL_REFRESH_COOLDOWN_SECONDS", "900"))
    try:
        tf_ms_val = int(tf_ms_fn(tf)) if callable(tf_ms_fn) else 0
    except Exception:
        tf_ms_val = 0
    if not tf_ms_val:
        tf_ms_val = 60_000
    return max(base, int((tf_ms_val / 1000) * 4))


def _market_pool_in_cooldown(redis_client, symbol: str, tf: str) -> bool:
    key = _redis_market_pool_cooldown_key(symbol, tf)
    if redis_client is not None:
        try:
            return bool(redis_client.exists(key))
        except Exception as exc:
            logger.debug("[MarketPool] cooldown check failed %s/%s: %s", symbol, tf, exc)
    expires_at = float(_MEM_MARKET_POOL_COOLDOWN.get(key, 0) or 0)
    if expires_at <= time.time():
        _MEM_MARKET_POOL_COOLDOWN.pop(key, None)
        return False
    return True


def _market_pool_set_cooldown(redis_client, symbol: str, tf: str, seconds: int | None = None, tf_ms_fn=None) -> None:
    ttl = max(1, int(seconds or _market_pool_cooldown_seconds(tf, tf_ms_fn)))
    key = _redis_market_pool_cooldown_key(symbol, tf)
    if redis_client is not None:
        try:
            redis_client.setex(key, ttl, "1")
            return
        except Exception as exc:
            logger.debug("[MarketPool] cooldown set failed %s/%s: %s", symbol, tf, exc)
    _MEM_MARKET_POOL_COOLDOWN[key] = time.time() + ttl


_CRYPTO_BASES = {
    "ADA", "BCH", "BNB", "BTC", "DOGE", "DOT", "ETH", "LINK", "LTC",
    "MATIC", "SOL", "TRX", "XLM", "XRP",
}
_FX_CODES = {
    "AUD", "CAD", "CHF", "EUR", "GBP", "JPY", "MXN", "NOK", "NZD",
    "SEK", "USD", "ZAR",
}


def _market_pool_symbol_market_open(symbol: str) -> bool:
    if str(os.getenv("MARKET_POOL_RESPECT_MARKET_HOURS", "true")).strip().lower() not in {"1", "true", "yes", "on"}:
        return True
    sym = str(symbol or "").upper().replace("/", "").replace("-", "")
    if len(sym) >= 6 and sym[-3:] == "USD" and sym[:-3] in _CRYPTO_BASES:
        return True
    now_utc = datetime.now(timezone.utc)
    try:
        market_tz = (
            os.getenv("MARKET_TIMEZONE")
            or os.getenv("FMP_INTRADAY_SOURCE_TZ")
            or "America/New_York"
        )
        now_ny = now_utc.astimezone(ZoneInfo(market_tz))
    except Exception:
        now_ny = now_utc
    weekday = now_ny.weekday()  # Monday=0, Sunday=6
    hour = now_ny.hour + now_ny.minute / 60
    if weekday == 5:
        return False
    if weekday == 6:
        return hour >= 17
    if weekday == 4:
        return hour < 17
    if len(sym) == 6 and sym[:3] in _FX_CODES and sym[3:] in _FX_CODES:
        return True
    return weekday < 5


async def _load_from_market_pool(
    *,
    redis_client,
    symbol: str,
    target_tf: str,
    active_tfs: list[str],
    norm_tf_fn,
    tf_ms_fn,
) -> tuple[list[dict], str | None]:
    target = norm_tf_fn(target_tf)
    direct = await asyncio.to_thread(_load_market_pool_series, redis_client, symbol, target, tf_ms_fn)
    if direct:
        return direct, "market_pool"

    configured_base = [
        norm_tf_fn(tf)
        for tf in os.getenv("MARKET_POOL_BASE_TFS", "1min,5min").split(",")
        if tf.strip()
    ]
    candidates = sorted(
        {
            norm_tf_fn(tf)
            for tf in [*configured_base, *(active_tfs or [])]
            if _tf_rank(norm_tf_fn(tf)) and _tf_rank(norm_tf_fn(tf)) < _tf_rank(target)
        },
        key=_tf_rank,
        reverse=True,
    )
    for lower_tf in candidates:
        lower = await asyncio.to_thread(_load_market_pool_series, redis_client, symbol, lower_tf, tf_ms_fn)
        if not lower:
            continue
        derived = _resample_series_ms(lower, target)
        if len(derived) >= MIN_CANDLES and _market_pool_fresh(derived, target, tf_ms_fn):
            return derived, f"market_pool_derived_from_{lower_tf}"
    return [], None


def _market_pool_symbols(norm_tf_fn) -> list[str]:
    try:
        from MarketTool import _ensure_globals_loaded, _universe_symbols
        _ensure_globals_loaded()
        symbols = sorted(str(s).upper() for s in (_universe_symbols() or []) if str(s).strip())
    except Exception as exc:
        logger.warning("[MarketPool] could not load common symbols: %s", exc)
        symbols = []
    max_symbols = int(os.getenv("MARKET_POOL_MAX_SYMBOLS", os.getenv("CACHE_WARMUP_MAX_SYMBOLS", "80")))
    env_symbols = [
        str(s).strip().upper()
        for s in os.getenv("MARKET_POOL_SYMBOLS", "").split(",")
        if str(s).strip()
    ]
    if env_symbols:
        symbols = env_symbols
    return symbols[:max(1, max_symbols)]


def _market_pool_status(redis_client, norm_tf_fn) -> dict[str, Any]:
    base_tfs = [
        norm_tf_fn(tf)
        for tf in os.getenv("MARKET_POOL_BASE_TFS", "1min").split(",")
        if tf.strip()
    ]
    symbols = _market_pool_symbols(norm_tf_fn)
    status: dict[str, Any] = {
        "enabled": str(os.getenv("MARKET_POOL_ENABLED", "true")).strip().lower() in {"1", "true", "yes", "on"},
        "producer_enabled": str(os.getenv("MARKET_POOL_PRODUCER_ENABLED", "true")).strip().lower() in {"1", "true", "yes", "on"},
        "redis": redis_client is not None,
        "symbols": len(symbols),
        "base_tfs": base_tfs,
        "expected_pairs": len(symbols) * len(base_tfs),
        "memory_candles": len(_MEM_MARKET_POOL),
        "memory_cooldowns": len(_MEM_MARKET_POOL_COOLDOWN),
        "current_minute_fmp_calls": 0,
        "redis_candles": 0,
        "redis_cooldowns": 0,
    }
    if redis_client is None:
        now_min = int(time.time() // 60)
        status["current_minute_fmp_calls"] = int(_MEM_MARKET_POOL_RATE.get(now_min, 0) or 0)
        return status
    try:
        status["current_minute_fmp_calls"] = int(redis_client.get(_redis_market_pool_rate_key()) or 0)
    except Exception:
        pass
    try:
        status["redis_candles"] = sum(1 for _ in redis_client.scan_iter("market_pool:candles:*", count=500))
    except Exception as exc:
        status["redis_candles_error"] = str(exc)[:160]
    try:
        status["redis_cooldowns"] = sum(1 for _ in redis_client.scan_iter("market_pool:cooldown:*", count=500))
    except Exception as exc:
        status["redis_cooldowns_error"] = str(exc)[:160]
    return status


async def _refresh_market_pool_symbol_tf(
    *,
    redis_client,
    symbol: str,
    tf: str,
    fetch_historical_range,
    tf_ms_fn,
    current_closed_bucket_start,
) -> bool:
    existing = await asyncio.to_thread(_load_market_pool_series, redis_client, symbol, tf, tf_ms_fn)
    if existing:
        return False
    if not _market_pool_symbol_market_open(symbol):
        await asyncio.to_thread(_market_pool_set_cooldown, redis_client, symbol, tf, 3600, tf_ms_fn)
        return False
    if await asyncio.to_thread(_market_pool_in_cooldown, redis_client, symbol, tf):
        return False

    lock_key = _redis_market_pool_lock_key(symbol, tf)
    lock_ttl = int(os.getenv("MARKET_POOL_LOCK_TTL_SECONDS", "45"))
    lock_acquired = False
    if redis_client is not None:
        try:
            lock_acquired = bool(redis_client.set(lock_key, str(os.getpid()), nx=True, ex=lock_ttl))
            if not lock_acquired:
                return False
        except Exception:
            lock_acquired = True
    else:
        lock_acquired = True

    try:
        tf_ms_val = tf_ms_fn(tf) or 60_000
        closed_end = current_closed_bucket_start(tf) - tf_ms_val
        if closed_end <= 0:
            closed_end = int(time.time() * 1000)
        if not _market_pool_acquire_fmp_slot(redis_client):
            return False
        bars = int(os.getenv("MARKET_POOL_FETCH_BARS", "180"))
        from_ms = closed_end - max(MIN_CANDLES + 10, bars) * tf_ms_val
        await asyncio.to_thread(_market_pool_set_cooldown, redis_client, symbol, tf, None, tf_ms_fn)
        with fmp_context(usage_kind="market_pool_refresh", source="market_pool", symbol=symbol, timeframe=tf):
            hist = await asyncio.to_thread(fetch_historical_range, symbol, tf, from_ms, closed_end)
        if not hist:
            return False
        await asyncio.to_thread(_store_market_pool_series, redis_client, symbol, tf, hist)
        logger.info("[MarketPool] refreshed %s/%s rows=%d", symbol, tf, len(hist))
        return True
    except Exception as exc:
        logger.debug("[MarketPool] refresh failed %s/%s: %s", symbol, tf, exc)
        return False
    finally:
        if redis_client is not None and lock_acquired:
            try:
                redis_client.delete(lock_key)
            except Exception:
                pass


async def _market_pool_loop(
    *,
    redis_client,
    fetch_historical_range,
    norm_tf_fn,
    tf_ms_fn,
    current_closed_bucket_start,
):
    base_tfs = [
        norm_tf_fn(tf)
        for tf in os.getenv("MARKET_POOL_BASE_TFS", "1min").split(",")
        if tf.strip()
    ]
    interval_s = max(5.0, float(os.getenv("MARKET_POOL_INTERVAL_SECONDS", "10")))
    max_per_tick = max(1, int(os.getenv("MARKET_POOL_MAX_REFRESH_PER_TICK", "4")))
    cursor = 0
    logger.info("[MarketPool] started base_tfs=%s interval=%.1fs max_per_tick=%d", base_tfs, interval_s, max_per_tick)
    while True:
        try:
            symbols = _market_pool_symbols(norm_tf_fn)
            pairs = [(s, tf) for s in symbols for tf in base_tfs]
            if pairs:
                refreshed = 0
                checked = 0
                while checked < len(pairs) and refreshed < max_per_tick:
                    symbol, tf = pairs[cursor % len(pairs)]
                    cursor += 1
                    checked += 1
                    did = await _refresh_market_pool_symbol_tf(
                        redis_client=redis_client,
                        symbol=symbol,
                        tf=tf,
                        fetch_historical_range=fetch_historical_range,
                        tf_ms_fn=tf_ms_fn,
                        current_closed_bucket_start=current_closed_bucket_start,
                    )
                    if did:
                        refreshed += 1
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("[MarketPool] loop error: %s", exc)
        await asyncio.sleep(interval_s)


def _ensure_market_pool_started(
    *,
    redis_client,
    fetch_historical_range,
    norm_tf_fn,
    tf_ms_fn,
    current_closed_bucket_start,
) -> None:
    global _MARKET_POOL_TASK
    enabled = str(os.getenv("MARKET_POOL_ENABLED", "true")).strip().lower() in {"1", "true", "yes", "on"}
    producer_enabled = str(os.getenv("MARKET_POOL_PRODUCER_ENABLED", "true")).strip().lower() in {"1", "true", "yes", "on"}
    if not enabled:
        logger.info("[MarketPool] disabled by MARKET_POOL_ENABLED")
        return
    if not producer_enabled:
        logger.info("[MarketPool] producer disabled by MARKET_POOL_PRODUCER_ENABLED")
        return
    loop_kwargs = dict(
        redis_client=redis_client,
        fetch_historical_range=fetch_historical_range,
        norm_tf_fn=norm_tf_fn,
        tf_ms_fn=tf_ms_fn,
        current_closed_bucket_start=current_closed_bucket_start,
    )
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        def _run_pool_thread():
            asyncio.run(_market_pool_loop(**loop_kwargs))

        thread = threading.Thread(target=_run_pool_thread, daemon=True, name="market-pool")
        thread.start()
        logger.info("[MarketPool] started in background thread")
        return
    if _MARKET_POOL_TASK is not None and not _MARKET_POOL_TASK.done():
        return
    _MARKET_POOL_TASK = loop.create_task(_market_pool_loop(**loop_kwargs))
    logger.info("[MarketPool] started in asyncio loop")


async def _charge_live_data_once(
    *,
    redis_client,
    user_id: str | None,
    exec_id: str,
    symbol: str,
    tf: str,
    candles_payload: dict,
    source: str,
    charge_monitoreo_per_call,
) -> tuple[bool, str]:
    """
    Cobra una vez por bucket de vela viva entregada/construida.

    Esto separa cuota comercial de costo FMP: aunque el dato venga de cache o sea
    resampleado desde 1min, sigue siendo data servida por la plataforma.
    """
    if not user_id or charge_monitoreo_per_call is None:
        return True, "no_user"
    live_candle = candles_payload.get("live_candle") or {}
    bucket_ms = int(live_candle.get("t") or candles_payload.get("last_ts") or 0)
    if bucket_ms <= 0:
        return True, "no_bucket"
    key = _redis_billing_key(user_id, exec_id, symbol, tf, bucket_ms, source)
    ttl_s = max(_entry_ttl_s(tf), 3600)

    if redis_client is not None:
        try:
            if not redis_client.set(key, "pending", nx=True, ex=ttl_s):
                return True, "already_billed"
        except Exception as exc:
            logger.debug("[LiveBilling] redis billing key failed %s: %s", key, exc)
    else:
        now = time.time()
        expired = [k for k, exp in _MEM_BILLING_KEYS.items() if exp <= now]
        for k in expired[:1000]:
            _MEM_BILLING_KEYS.pop(k, None)
        if key in _MEM_BILLING_KEYS:
            return True, "already_billed"
        _MEM_BILLING_KEYS[key] = now + ttl_s

    units = _live_data_units_for_source(source)
    ok, msg = await charge_monitoreo_per_call(user_id, origen="app", units=units)
    if ok:
        if redis_client is not None:
            try:
                redis_client.setex(key, ttl_s, "charged")
            except Exception:
                pass
        logger.info("[LiveBilling] charged user=%s %s/%s source=%s units=%d bucket=%s", user_id, symbol, tf, source, units, bucket_ms)
        return True, msg

    if redis_client is not None:
        try:
            redis_client.delete(key)
        except Exception:
            pass
    else:
        _MEM_BILLING_KEYS.pop(key, None)
    logger.warning("[LiveBilling] blocked user=%s %s/%s source=%s: %s", user_id, symbol, tf, source, msg)
    return False, msg


def _live_data_units_for_source(source: str) -> int:
    source_norm = str(source or "").lower()
    if source_norm == "fmp":
        env_key = "LIVE_DATA_UNITS_FMP"
        default = "3"
    elif "derived" in source_norm:
        env_key = "LIVE_DATA_UNITS_DERIVED"
        default = "1"
    elif "market_pool" in source_norm:
        env_key = "LIVE_DATA_UNITS_POOL"
        default = "1"
    else:
        env_key = "LIVE_DATA_UNITS_CACHE"
        default = "1"
    try:
        return max(1, int(os.getenv(env_key, default)))
    except Exception:
        return max(1, int(default))


def _tf_to_resample_rule(tf: str) -> str | None:
    mapping = {
        "5m": "5min",
        "5min": "5min",
        "15m": "15min",
        "15min": "15min",
        "30m": "30min",
        "30min": "30min",
        "1h": "1h",
        "1hour": "1h",
        "4h": "4h",
        "4hour": "4h",
        "1d": "1d",
        "1day": "1d",
        "1w": "1W",
        "1week": "1W",
    }
    return mapping.get(str(tf or "").lower())


def _tf_rank(tf: str) -> int:
    order = {
        "1m": 1,
        "1min": 1,
        "5m": 5,
        "5min": 5,
        "15m": 15,
        "15min": 15,
        "30m": 30,
        "30min": 30,
        "1h": 60,
        "1hour": 60,
        "4h": 240,
        "4hour": 240,
        "1d": 1440,
        "1day": 1440,
        "1w": 10080,
        "1week": 10080,
    }
    return order.get(str(tf or "").lower(), 0)


def _resample_series_ms(series_ms: list[dict], target_tf: str) -> list[dict]:
    """Forma velas de una TF mayor desde una serie menor ya disponible."""
    rule = _tf_to_resample_rule(target_tf)
    if not rule or not series_ms:
        return []
    df = _series_to_df(series_ms)
    if df.empty:
        return []
    try:
        agg = df.resample(rule, label="left", closed="left").agg(
            {
                "open": "first",
                "high": "max",
                "low": "min",
                "close": "last",
                "volume": "sum",
            }
        )
        agg = agg.dropna(subset=["open", "high", "low", "close"])
        result: list[dict] = []
        for ts, row in agg.iterrows():
            result.append(
                {
                    "t": int(ts.timestamp() * 1000),
                    "o": float(row["open"]),
                    "h": float(row["high"]),
                    "l": float(row["low"]),
                    "c": float(row["close"]),
                    "v": float(row.get("volume", 0) or 0),
                }
            )
        return result
    except Exception as exc:
        logger.debug("[LiveWorker] resample failed target=%s: %s", target_tf, exc)
        return []


async def _load_resampled_from_lower_tfs(
    *,
    exec_id: str,
    symbol: str,
    target_tf: str,
    active_tfs: list[str],
    load_cache,
    maybe_refresh_from_gcs,
    norm_tf_fn,
) -> tuple[list[dict], str | None]:
    target_rank = _tf_rank(norm_tf_fn(target_tf))
    candidates = sorted(
        {
            norm_tf_fn(tf)
            for tf in active_tfs
            if _tf_rank(norm_tf_fn(tf)) and _tf_rank(norm_tf_fn(tf)) < target_rank
        },
        key=_tf_rank,
        reverse=True,
    )
    for lower_tf in candidates:
        try:
            lower_st: dict = await asyncio.to_thread(load_cache, exec_id, symbol, lower_tf)
            await asyncio.to_thread(maybe_refresh_from_gcs, exec_id, symbol, lower_tf, lower_st)
            lower_series = lower_st.get("series") or []
            derived = _resample_series_ms(lower_series, target_tf)
            if len(derived) >= MIN_CANDLES:
                return derived, lower_tf
        except Exception as exc:
            logger.debug(
                "[LiveWorker] lower-TF derive failed %s/%s from %s: %s",
                symbol,
                target_tf,
                lower_tf,
                exc,
            )
    return [], None


def _publish_live_candles(redis_client, exec_id: str, symbol: str, tf: str, candles_payload: dict) -> None:
    if not candles_payload.get("live_candle") and not candles_payload.get("candles"):
        return

    ts_ms = int(time.time() * 1000)
    event = {
        "id": _event_id(exec_id, symbol, tf, ts_ms),
        "type": "candles",
        "exec_id": exec_id,
        "symbol": symbol.upper(),
        "timeframe": tf,
        **candles_payload,
        "ts": ts_ms,
    }
    _store_event_for_replay(redis_client, exec_id, symbol, event)

    if redis_client is not None:
        try:
            redis_client.publish(_redis_events_channel(exec_id, symbol), json.dumps(event))
            return
        except Exception as exc:
            logger.warning("[LiveEntries] Redis candle publish failed %s/%s: %s", symbol, tf, exc)

    worker_key = _worker_id(exec_id, symbol)
    with _SUBSCRIBERS_LOCK:
        subscribers = list(_SUBSCRIBERS.get(worker_key) or [])
    for q in subscribers:
        try:
            q.put_nowait(event)
        except queue.Full:
            logger.warning("[LiveEntries] subscriber queue full %s/%s — candle skipped", symbol, tf)


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


# ─────────────────────────────────────────────
# Carga de niveles S/R desde GCS enriched (igual que RN/Web)
# Compatible con modos: gcp, vps, vps_gcp, gcp_vps
# ─────────────────────────────────────────────

_enriched_niveles_cache: dict[str, dict] = {}  # (symbol, tf, exec_id) → niveles dict
_enriched_niveles_ts: dict[str, float] = {}   # (symbol, tf, exec_id) → last load time
_ENRICHED_NIVELES_TTL_S = 600  # 10 min cache en memoria para no ir a GCS cada beat


def _get_gcs_bucket_for_enriched(services: Any = None):
    """Obtiene bucket GCS o VpsJsonStore según el cloud backend mode."""
    try:
        from markettool.infra.storage.vps_json_store import vps_mode_enabled, VpsJsonStore
        if vps_mode_enabled():
            return VpsJsonStore.from_env(), "vps"
        # GCS mode (gcp, vps_gcp, gcp_vps usan GCS para enriched)
        bucket_name = getattr(services, "gcs_bucket_name", None) or os.environ.get("GCS_BUCKET_NAME", "markettool_bucket")
        gcs_client = getattr(services, "gcs_client", None)
        if gcs_client is None:
            from google.cloud import storage as gcs_storage
            gcs_client = gcs_storage.Client()
        return gcs_client.bucket(bucket_name), "gcs"
    except Exception as exc:
        logger.warning("[LiveWorker] No se pudo obtener storage bucket: %s", exc)
        return None, None


def _get_firestore_client_for_enriched(services: Any = None):
    """Obtiene cliente Firestore (GCP mode) o None (VPS-only mode).
    Usa google.cloud.firestore directamente (igual que indicators_cache).
    """
    try:
        from markettool.infra.storage.vps_json_store import vps_mode_enabled
        if vps_mode_enabled():
            # En VPS mode, los metadatos están en Postgres, no en Firestore
            return None
        # GCP / hybrid mode: usar Firestore via services o google.cloud
        if services and getattr(services, "db", None):
            return services.db
        from google.cloud import firestore as fs_module
        return fs_module.Client()
    except Exception as exc:
        logger.warning("[LiveWorker] No se pudo obtener Firestore client: %s", exc)
        return None


def _load_enriched_niveles_from_gcs(
    symbol: str,
    tf: str,
    exec_id: str,
    services: Any = None,
) -> dict | None:
    """
    Carga niveles S/R del análisis enriched desde GCS/VPS storage.
    Replica el flujo de RN: Firestore archivos_generados → signed URL → enriched JSON → niveles.
    Compatible con gcp, vps, vps_gcp, gcp_vps.
    Retorna dict con soporte_nivel_1/2, resistencia_nivel_1/2, niveles_confirmados_orden_toques_all.
    """
    cache_key = f"{symbol.upper()}_{tf}_{exec_id}"
    now = time.time()

    # Cache en memoria para no ir a GCS cada beat (TTL 10 min)
    if cache_key in _enriched_niveles_cache and (now - _enriched_niveles_ts.get(cache_key, 0)) < _ENRICHED_NIVELES_TTL_S:
        return _enriched_niveles_cache[cache_key]

    bucket, storage_mode = _get_gcs_bucket_for_enriched(services)
    if bucket is None:
        logger.debug("[LiveWorker] Sin storage bucket para enriched %s/%s", symbol, tf)
        return None

    sym_upper = symbol.upper()
    tf_norm = tf.lower()
    safe_sym = sym_upper.replace("/", "_")

    # Normalizar TF para nombre de archivo (igual que backtest_routes_factory)
    from markettool.infra.fmp import normalize_tf
    gcs_tf = normalize_tf(tf_norm)

    enriched_payload: dict | None = None

    # 1. Intentar vía Firestore archivos_generados (igual que RN)
    db = _get_firestore_client_for_enriched(services)
    if db is not None:
        try:
            docs = list(db.collection("archivos_generados").where("exec_id", "==", exec_id).stream())
            # Patrón exacto: {SYMBOL}_{tf}_enriched.json (evita que 5min haga match con 15min)
            target_pattern = f"{sym_upper}_{gcs_tf.upper()}_ENRICHED"
            for doc in docs:
                data = doc.to_dict() or {}
                gcs_path_raw = (data.get("gcs_path") or data.get("metadata", {}).get("gcs_path") or "").upper()
                nombre = (data.get("nombre") or data.get("metadata", {}).get("nombre") or "").upper()
                if target_pattern in gcs_path_raw or (sym_upper in nombre and gcs_tf.upper() in nombre and "ENRICHED" in nombre):
                    gcs_path = data.get("gcs_path") or data.get("metadata", {}).get("gcs_path")
                    if gcs_path and hasattr(bucket, "blob"):
                        blob = bucket.blob(gcs_path)
                        if hasattr(blob, "exists") and blob.exists():
                            enriched_payload = json.loads(blob.download_as_text())
                            break
        except Exception as exc:
            logger.debug("[LiveWorker] archivos_generados query failed: %s", exc)

    # 2. Fallback: rutas canónicas en GCS/VPS (igual que backtest_routes)
    if enriched_payload is None and hasattr(bucket, "blob"):
        for path in [
            f"analisis/exec/{exec_id}/{safe_sym}_{gcs_tf}_enriched.json",
            f"exec/{exec_id}/{safe_sym}_{gcs_tf}_enriched.json",
        ]:
            try:
                blob = bucket.blob(path)
                if hasattr(blob, "exists") and blob.exists():
                    enriched_payload = json.loads(blob.download_as_text())
                    break
            except Exception:
                continue

    # 3. VPS mode: buscar en filesystem directamente
    if enriched_payload is None and storage_mode == "vps":
        try:
            from markettool.infra.storage.vps_json_store import VpsJsonStore
            store = VpsJsonStore.from_env()
            for path in [
                f"analisis/exec/{exec_id}/{safe_sym}_{gcs_tf}_enriched.json",
                f"exec/{exec_id}/{safe_sym}_{gcs_tf}_enriched.json",
            ]:
                p = store._path(path)
                if p.exists():
                    with open(p, "r", encoding="utf-8") as f:
                        enriched_payload = json.load(f)
                    break
        except Exception as exc:
            logger.debug("[LiveWorker] VPS filesystem enriched lookup failed: %s", exc)

    if enriched_payload is None:
        logger.info("[LiveWorker] No enriched JSON para %s/%s exec=%s", symbol, tf, exec_id)
        _enriched_niveles_cache[cache_key] = {}  # cache negativo por 10 min
        _enriched_niveles_ts[cache_key] = now
        return None

    # Extraer niveles del enriched (igual que parseEnriched de RN)
    levels = enriched_payload.get("levels") or {}
    entradas = enriched_payload.get("entradas") or {}
    # entradas puede ser una lista en algunos formatos — convertir a dict vacío
    if not isinstance(entradas, dict):
        entradas = {}
    if not isinstance(levels, dict):
        levels = {}

    to_float = lambda v: float(v) if v is not None and str(v).strip() != "" and _is_numeric(v) else None

    s1 = to_float(levels.get("soporte_nivel_1")) or to_float(entradas.get("soporte_nivel_1"))
    s2 = to_float(levels.get("soporte_nivel_2")) or to_float(entradas.get("soporte_nivel_2"))
    r1 = to_float(levels.get("resistencia_nivel_1")) or to_float(entradas.get("resistencia_nivel_1"))
    r2 = to_float(levels.get("resistencia_nivel_2")) or to_float(entradas.get("resistencia_nivel_2"))

    # niveles_confirmados_orden_toques_all (array de [level, touches] o strings)
    niveles_toques_all = entradas.get("niveles_confirmados_orden_toques_all") or []
    niveles_parsed: list[dict] = []
    for item in niveles_toques_all:
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            lvl = to_float(item[0])
            tou = int(item[1]) if _is_numeric(item[1]) else 0
            if lvl is not None:
                niveles_parsed.append({"level": lvl, "touches": tou})
        elif isinstance(item, str):
            import re
            m = re.match(r"(-?\d+(?:\.\d+)?)[^0-9\-+]*(-?\d+(?:\.\d+)?)", item)
            if m:
                lvl = to_float(m.group(1))
                tou = int(float(m.group(2))) if _is_numeric(m.group(2)) else 0
                if lvl is not None:
                    niveles_parsed.append({"level": lvl, "touches": tou})

    niveles_result = {
        "soporte_nivel_1": s1,
        "soporte_nivel_2": s2,
        "resistencia_nivel_1": r1,
        "resistencia_nivel_2": r2,
        "niveles_confirmados_orden_toques_all": niveles_parsed,
        "_source": "enriched_gcs",
    }

    # Cachear resultado
    _enriched_niveles_cache[cache_key] = niveles_result
    _enriched_niveles_ts[cache_key] = now

    logger.info(
        "[LiveWorker] Niveles enriched cargados %s/%s: S1=%s R1=%s (toques=%d, mode=%s)",
        symbol, tf, s1, r1, len(niveles_parsed), storage_mode,
    )
    return niveles_result


def _is_numeric(v) -> bool:
    try:
        float(v)
        return True
    except (TypeError, ValueError):
        return False
    return None


def _is_sr_entry(source: str, raw_entry: dict) -> bool:
    source_key = str(source or raw_entry.get("source") or raw_entry.get("fuente") or "").lower()
    based_on = str(raw_entry.get("basado_en") or raw_entry.get("reason") or raw_entry.get("rawText") or raw_entry.get("text") or "").lower()
    return source_key == "sr" or "s/r" in based_on or "support" in based_on or "resistance" in based_on or "soporte" in based_on or "resistencia" in based_on


def _build_entry_id(symbol: str, tf: str, side: str, ts: int, entry: float, sl: float, tp: float, source: str) -> str:
    payload = "|".join(
        [
            symbol.upper(),
            tf,
            side,
            str(int(ts or 0)),
            str(round(float(entry or 0) * 1e5)),
            str(round(float(sl or 0) * 1e5)),
            str(round(float(tp or 0) * 1e5)),
            str(source or "backend").lower(),
        ]
    )
    return "live|" + hashlib.sha1(payload.encode("utf-8")).hexdigest()[:24]


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

    # Si no hay niveles del análisis enriched, NO improvisar con min/max del DataFrame.
    # Los niveles S/R son producto del análisis completo (toques confirmados, estructura).
    # Sin ellos, las entradas que dependen de S/R confirmado no se generan.
    if not sr_levels:
        logger.info(
            "[LiveWorker] %s/%s sin niveles enriched — S/R entries serán omitidas",
            symbol, tf,
        )
        # El wrapper aún puede generar entradas técnicas (RSI, MACD, etc.)
        # sin necesidad de niveles S/R

    try:
        try:
            from MarketTool import calcular_indicadores
            df_ind = calcular_indicadores(df, norm_tf_fn(tf), symbol=symbol)
            if df_ind is not None and not getattr(df_ind, "empty", False):
                df = df_ind
        except Exception as ind_exc:
            logger.warning("[LiveWorker] indicadores no disponibles %s/%s: %s", symbol, tf, ind_exc)

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
    if isinstance(entradas_raw, dict):
        entradas_raw = entradas_raw.get("lista") or []

    now_ts = int(time.time() * 1000)
    now_iso = pd.Timestamp.now(tz="UTC").isoformat().replace("+00:00", "Z")
    entries: list[dict] = []
    for e in entradas_raw:
        if not isinstance(e, dict):
            continue
        side = e.get("tipo_operacion") or e.get("side") or e.get("direction", "")
        if isinstance(side, str):
            side_raw = side.lower()
            if "compra" in side_raw or "long" in side_raw or "buy" in side_raw:
                side = "long"
            elif "venta" in side_raw or "short" in side_raw or "sell" in side_raw:
                side = "short"
            else:
                continue
        elif side not in ("long", "short"):
            continue
        entry_price = e.get("precio_entrada") or e.get("entry_price") or e.get("precio") or 0.0
        tp = e.get("take_profit") or e.get("tp") or 0.0
        sl = e.get("stop_loss") or e.get("sl") or 0.0
        rrr = 0.0
        if entry_price and tp and sl and abs(entry_price - sl) > 0:
            rrr = round(abs(tp - entry_price) / abs(entry_price - sl), 2)

        source = _normalize_source(e.get("source") or e.get("fuente") or e.get("basado_en"))
        confirmed_level = bool(e.get("nivel_confirmado") or e.get("confirmado"))
        if _is_sr_entry(source, e):
            confirmed_level = confirmed_level or bool(sr_levels)
            if not confirmed_level:
                continue
        entry_ts = int(e.get("timestamp") or (series_ms[-1].get("t") if series_ms else now_ts) or now_ts)
        entry_id = _build_entry_id(symbol, tf, side, entry_ts, float(entry_price or 0), float(sl or 0), float(tp or 0), str(source))

        entries.append({
            "id": entry_id,
            "symbol": symbol,
            "timeframe": tf,
            "tf": tf,
            "side": side,
            "entry_price": float(entry_price),
            "entry": float(entry_price),
            "precio": float(entry_price),
            "take_profit": float(tp),
            "tp": float(tp),
            "stop_loss": float(sl),
            "sl": float(sl),
            "rrr": rrr,
            "rr": rrr,
            "confluence_score": int(e.get("confluencia") or e.get("confluence_score") or e.get("score") or 0),
            "source": source,
            "status": "open",
            "outcome": "pending",
            "_origin": "live",
            "_backend_live": True,
            "created_at": now_iso,
            "createdAt": now_ts,
            "timestamp": entry_ts,
            "rawText": e.get("rawText") or e.get("text") or "Backend live",
            "text": e.get("text") or e.get("rawText") or "Backend live",
            "nivel_confirmado": confirmed_level,
            "dentro_rango": bool(e.get("dentro_rango") or e.get("en_rango")),
            "early_detection": False,
            "sr_levels": sr_levels,
        })

    logger.info("[LiveWorker] %s/%s → %d entradas generadas", symbol, tf, len(entries))
    return entries, sr_levels


# ─────────────────────────────────────────────
# Descubrimiento de TFs activos desde Firestore
# ─────────────────────────────────────────────

def _get_active_tfs(exec_id: str, symbol: str, norm_tf_fn) -> list[str]:
    """
    Lee monitoreos/{exec_id}__{SYMBOL} en Firestore y devuelve los TFs activos.
    Usa allowed_timeframes + tf_states para filtrar TFs detenidos.
    Fallback: si no hay doc en Firestore, devuelve lista vacía.
    """
    try:
        from MarketTool import db, _tf_is_enabled
        doc_id = f"{exec_id}__{symbol.upper()}"
        snap = db.collection("monitoreos").document(doc_id).get()
        if not snap.exists:
            logger.warning("[LiveWorker] No hay doc monitoreos/%s en Firestore", doc_id)
            return []

        doc = snap.to_dict() or {}
        estado_global = str(doc.get("estado") or "").lower()
        stop_words = {"stopped", "detenido", "inactivo", "cancelado", "finalizado"}
        if any(w in estado_global for w in stop_words):
            logger.info("[LiveWorker] %s estado global=%s — sin TFs", doc_id, estado_global)
            return []

        allowed: list[str] = doc.get("running") or doc.get("allowed_timeframes") or []
        # Si no hay allowed_timeframes, intentar desde tf_states
        if not allowed:
            tf_states = doc.get("tf_states") or {}
            allowed = list(tf_states.keys())

        active_tfs = []
        for tf_raw in allowed:
            tf = norm_tf_fn(tf_raw)
            try:
                if _tf_is_enabled(exec_id, symbol, tf):
                    active_tfs.append(tf)
            except Exception as exc:
                logger.debug("[LiveWorker] tf_is_enabled error %s/%s: %s", symbol, tf, exc)
                active_tfs.append(tf)  # incluir por defecto si no se puede verificar

        logger.info("[LiveWorker] %s TFs activos desde Firestore: %s", doc_id, active_tfs)
        return active_tfs

    except Exception as exc:
        logger.warning("[LiveWorker] Error obteniendo TFs activos %s/%s: %s", exec_id, symbol, exc)
        return []


# ─────────────────────────────────────────────
# Worker asyncio por exec_id+symbol
# ─────────────────────────────────────────────

async def _live_worker(
    worker_key: str,
    exec_id: str,
    symbol: str,
    tfs: list[str],
    user_id: str | None,
    redis_client,
    load_cache,
    maybe_refresh_from_gcs,
    fetch_historical_range,
    fetch_events_for,
    norm_tf_fn,
    tf_ms_fn,
    current_closed_bucket_start,
    calcular_entradas_sync_wrapper,
    charge_monitoreo_per_call,
    reponer_transaccion,
    services: Any = None,
):
    """
    Worker asyncio. Lanza un sub-task por TF, cada uno con su propio intervalo.
    Homologa TF_POLL_MS del cliente (1m=5s, 5m=20s, 15m=45s, ...).
    Se cancela entero cuando se llama /stop o se alcanza idle timeout.
    """
    _refresh_worker_owner(redis_client, worker_key)
    # Auto-descubrir TFs desde Firestore si no se pasaron explícitamente
    if not tfs:
        tfs = _get_active_tfs(exec_id, symbol, norm_tf_fn)
        if not tfs:
            logger.warning("[LiveWorker] %s sin TFs activos — worker no arranca", worker_key)
            async with _WORKER_LOCK:
                _WORKERS.pop(worker_key, None)
                _clear_worker_owner(redis_client, worker_key)
            return

    logger.info("[LiveWorker] START %s tfs=%s", worker_key, tfs)

    async def _run_beat(tf: str):
        try:
            norm = norm_tf_fn(tf)

            # 0. Mercado cerrado — no generar entradas (respeta crypto 24/7)
            if not _market_pool_symbol_market_open(symbol):
                logger.info("[LiveWorker] %s/%s mercado cerrado — skip beat", symbol, norm)
                return

            # 1. Pool central Redis para activos comunes. Esto evita que cada
            # ejecución privada vuelva a consultar FMP si ya existe data viva.
            series_ms, pool_source = await _load_from_market_pool(
                redis_client=redis_client,
                symbol=symbol,
                target_tf=norm,
                active_tfs=list(tf_tasks_map.keys()),
                norm_tf_fn=norm_tf_fn,
                tf_ms_fn=tf_ms_fn,
            )
            data_source = pool_source or "cache"

            # 2. Candles del mon_cache específico de la ejecución si el pool
            # no tiene suficiente cobertura fresca.
            st: dict = {}
            if len(series_ms) < MIN_CANDLES:
                st = await asyncio.to_thread(load_cache, exec_id, symbol, norm)
                series_ms = st.get("series") or []

                # 3. Refrescar GCS seed
                await asyncio.to_thread(maybe_refresh_from_gcs, exec_id, symbol, norm, st)
                series_ms = st.get("series") or series_ms

            # 4. Antes de ir a FMP, inferir TF mayor desde una TF menor ya cacheada.
            derived_from_tf = None
            if len(series_ms) < MIN_CANDLES:
                derived, derived_from_tf = await _load_resampled_from_lower_tfs(
                    exec_id=exec_id,
                    symbol=symbol,
                    target_tf=norm,
                    active_tfs=list(tf_tasks_map.keys()),
                    load_cache=load_cache,
                    maybe_refresh_from_gcs=maybe_refresh_from_gcs,
                    norm_tf_fn=norm_tf_fn,
                )
                if derived:
                    series_ms = derived
                    data_source = f"derived_from_{derived_from_tf}"
                    logger.info(
                        "[LiveWorker] %s/%s derived %d candles from %s cache",
                        symbol,
                        norm,
                        len(series_ms),
                        derived_from_tf,
                    )

            # 5. Fallback FMP si faltan candles. La cuota se cobra solo cuando
            # existe data lista para entregar, así no hay que reponer fallos.
            if len(series_ms) < MIN_CANDLES:
                tf_ms_val = tf_ms_fn(norm) or 60_000
                closed_end = current_closed_bucket_start(norm) - tf_ms_val
                from_ms = closed_end - 300 * tf_ms_val
                try:
                    with fmp_context(
                        usage_kind="monitoring_live_refresh",
                        source="live_entries_fallback",
                        user_id=user_id,
                        exec_id=exec_id,
                        symbol=symbol,
                        timeframe=norm,
                    ):
                        hist = await asyncio.to_thread(fetch_historical_range, symbol, norm, from_ms, closed_end)
                    if hist:
                        series_ms = hist
                        data_source = "fmp"
                        try:
                            await asyncio.to_thread(_store_market_pool_series, redis_client, symbol, norm, hist)
                        except Exception:
                            pass
                except Exception:
                    raise

            if len(series_ms) < MIN_CANDLES:
                logger.info("[LiveWorker] %s/%s sin candles suficientes (%d)", symbol, norm, len(series_ms))
                return

            candles_payload = _build_candles_payload(series_ms)
            charged_ok, charged_msg = await _charge_live_data_once(
                redis_client=redis_client,
                user_id=user_id,
                exec_id=exec_id,
                symbol=symbol,
                tf=norm,
                candles_payload=candles_payload,
                source=data_source,
                charge_monitoreo_per_call=charge_monitoreo_per_call,
            )
            if not charged_ok:
                logger.warning(
                    "[LiveWorker] %s/%s data blocked by quota user=%s source=%s: %s",
                    symbol,
                    norm,
                    user_id,
                    data_source,
                    charged_msg,
                )
                return

            # 5. Niveles desde GCS enriched (igual que RN: archivos_generados → enriched JSON)
            #    El indicators_cache solo tiene indicadores técnicos, NO niveles S/R confirmados.
            #    Los niveles del análisis se cargan desde GCS/VPS enriched JSON.
            niveles: dict | None = None
            try:
                niveles = await asyncio.to_thread(
                    _load_enriched_niveles_from_gcs,
                    symbol, norm, exec_id, services,
                )
            except Exception as exc:
                logger.warning("[LiveWorker] Error cargando niveles enriched %s/%s: %s", symbol, norm, exc)

            # Fallback: indicators_cache (puede tener niveles si el cálculo de indicadores los incluyó)
            if not niveles:
                try:
                    from markettool.infra.cache.indicators_cache import _INDICATORS_CACHE
                    cached = _INDICATORS_CACHE.get(symbol, norm)
                    if cached and isinstance(cached, dict):
                        niveles = cached.get("niveles") or cached.get("levels")
                except Exception:
                    pass

            # 6. Eventos económicos
            df_eventos = pd.DataFrame()
            try:
                events_raw = await asyncio.to_thread(fetch_events_for, symbol, hours_back=6)
                if events_raw:
                    df_eventos = pd.DataFrame(events_raw) if isinstance(events_raw, list) else events_raw
            except Exception:
                pass

            # 7. Generar entradas
            entries, sr_levels = await asyncio.to_thread(
                _generate_live_entries_sync,
                symbol, norm, series_ms, niveles, df_eventos,
                calcular_entradas_sync_wrapper, norm_tf_fn,
            )

            # 8. Persistir en Redis
            if entries:
                new_entries = _push_entries_to_redis(redis_client, exec_id, symbol, norm, entries)
                if new_entries:
                    logger.info("[LiveWorker] %s/%s +%d nuevas entradas", symbol, norm, len(new_entries))
                    _publish_live_entries(redis_client, exec_id, symbol, norm, new_entries)

            _publish_live_candles(redis_client, exec_id, symbol, norm, candles_payload)

            # 9. Actualizar beat timestamp + sr_levels + candles en Redis
            try:
                beat_data: dict = _get_beat(redis_client, exec_id, symbol) or {
                    "tfs": [],
                    "symbol": symbol,
                    "sr_levels": {},
                    "candles": {},
                }
                beat_data["tfs"] = list(sorted(tf_tasks_map.keys()))
                beat_data["symbol"] = symbol
                beat_data.setdefault("sr_levels", {})[norm] = sr_levels
                beat_data.setdefault("candles", {})[norm] = candles_payload
                _set_beat(redis_client, exec_id, symbol, beat_data)
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

    # tf_tasks_map: tf → Task (para poder agregar/quitar dinámicamente)
    tf_tasks_map: dict[str, asyncio.Task] = {}

    async def _start_tf_task(tf: str):
        if tf not in tf_tasks_map or tf_tasks_map[tf].done():
            task = asyncio.get_event_loop().create_task(_tf_beat_loop(tf))
            tf_tasks_map[tf] = task
            logger.info("[LiveWorker] TF task iniciado: %s/%s", symbol, tf)

    # Lanzar tasks iniciales con stagger de 1s
    for i, tf in enumerate(tfs):
        if i > 0:
            await asyncio.sleep(1.0)
        await _start_tf_task(tf)

    # Monitor loop — cada 30s:
    #   1. TF reconciliation — agrega/quita tasks según Firestore
    #   2. Idle check — solo mata el worker si ya no hay TFs activos ni frontend consultando
    try:
        while True:
            await asyncio.sleep(30)
            _refresh_worker_owner(redis_client, worker_key)
            if _is_worker_stop_requested(redis_client, worker_key):
                logger.info("[LiveWorker] STOP solicitado por Redis %s — stopping", worker_key)
                break

            current_tfs: set[str] = set()

            # 1. Re-leer TFs activos desde Firestore
            try:
                current_tfs = set(await asyncio.to_thread(
                    _get_active_tfs, exec_id, symbol, norm_tf_fn
                ))
                running_tfs = set(tf_tasks_map.keys())

                # TFs nuevos → arrancar
                for tf in current_tfs - running_tfs:
                    logger.info("[LiveWorker] %s nuevo TF detectado: %s — arrancando task", symbol, tf)
                    await _start_tf_task(tf)

                # TFs removidos → cancelar
                for tf in running_tfs - current_tfs:
                    task = tf_tasks_map.pop(tf, None)
                    if task and not task.done():
                        task.cancel()
                        logger.info("[LiveWorker] %s TF removido: %s — task cancelado", symbol, tf)

            except Exception as exc:
                logger.warning("[LiveWorker] Error en TF reconciliation %s: %s", worker_key, exc)

            # 2. Idle check — proteccion contra zombies:
            #    - Sin TFs activos: mata tras 5 min sin polling
            #    - Con TFs activos pero sin polling (app cerrada/crash): mata tras 15 min
            #    - Esto evita workers zombies que generan entradas que nadie consume
            last_poll = _get_worker_last_touch(redis_client, worker_key)
            idle_s = time.time() - last_poll
            if not current_tfs and idle_s > WORKER_IDLE_TIMEOUT_S:
                logger.info("[LiveWorker] IDLE TIMEOUT sin TFs activos %s — stopping", worker_key)
                break
            if current_tfs and idle_s > WORKER_ZOMBIE_TIMEOUT_S:
                logger.info("[LiveWorker] ZOMBIE TIMEOUT con TFs activos pero sin polling %.0fs %s — stopping", idle_s, worker_key)
                break

    except asyncio.CancelledError:
        pass
    finally:
        for t in tf_tasks_map.values():
            t.cancel()
        logger.info("[LiveWorker] STOP %s", worker_key)
        async with _WORKER_LOCK:
            _WORKERS.pop(worker_key, None)
            _WORKER_LAST_POLL.pop(worker_key, None)
            _clear_worker_owner(redis_client, worker_key)


# ─────────────────────────────────────────────
# Registro de rutas Flask
# ─────────────────────────────────────────────
# ─────────────────────────────────────────────
# Registro de rutas Flask
# ─────────────────────────────────────────────

def register_live_entries_routes(app, *, services) -> None:
    from flask import Response, jsonify, request, stream_with_context

    load_cache = services.load_cache
    maybe_refresh_from_gcs = services.maybe_refresh_from_gcs
    fetch_historical_range = services.fetch_historical_range
    charge_monitoreo_per_call = getattr(services, "charge_monitoreo_per_call", None)
    reponer_transaccion = getattr(services, "reponer_transaccion", None)
    fetch_events_for = services.fetch_events_for
    norm_tf = services.norm_tf
    tf_ms = services.tf_ms
    current_closed_bucket_start = services.current_closed_bucket_start
    mon_cache_lock = services.mon_cache_lock

    # Redis client for live entries state/coordination.
    # REDIS_URL remains available for hot local caches; LIVE_ENTRIES_REDIS_URL can point
    # to a shared Redis when multiple machines sit behind the global load balancer.
    try:
        import os, redis as _redis
        _redis_url = os.getenv("LIVE_ENTRIES_REDIS_URL") or os.getenv("REDIS_URL", "redis://redis:6379/0")
        redis_client = _redis.Redis.from_url(_redis_url, decode_responses=True, socket_connect_timeout=3)
        redis_client.ping()  # verificar conexión
    except Exception:
        redis_client = None
        logger.warning("[LiveEntries] Redis no disponible — usando fallback en memoria del proceso")

    # calcular_entradas_sync_wrapper
    try:
        from MarketTool import calcular_entradas_sync_wrapper
    except ImportError:
        calcular_entradas_sync_wrapper = None
        logger.warning("[LiveEntries] calcular_entradas_sync_wrapper no disponible")

    _ensure_market_pool_started(
        redis_client=redis_client,
        fetch_historical_range=fetch_historical_range,
        norm_tf_fn=norm_tf,
        tf_ms_fn=tf_ms,
        current_closed_bucket_start=current_closed_bucket_start,
    )

    @app.route("/monitoreo/market-pool/status", methods=["GET"])
    @app.route("/api/market-pool/status", methods=["GET"])
    def market_pool_status():
        return jsonify(_market_pool_status(redis_client, norm_tf)), 200

    # ── POST /monitoreo/live-entries/start ──────────────────────────────────
    @app.route("/monitoreo/live-entries/start", methods=["POST"])
    async def live_entries_start():
        body = request.get_json(force=True) or {}
        exec_id = body.get("exec_id", "").strip()
        user_id = str(body.get("user_id") or "").strip()
        symbol = (body.get("symbol") or "").upper().strip()
        tfs_raw: list[str] = body.get("tfs") or []   # opcional — si vacío, auto-descubre desde Firestore
        platform = body.get("platform", "UNKNOWN")

        if not exec_id or not symbol:
            return jsonify({"error": "exec_id y symbol son requeridos"}), 400
        if calcular_entradas_sync_wrapper is None:
            return jsonify({"error": "Motor de entradas no disponible"}), 503

        # Si el cliente pasa TFs explícitamente, los normaliza; si no, el worker los descubre
        tfs = [norm_tf(t) for t in tfs_raw] if tfs_raw else []
        wid = _worker_id(exec_id, symbol)

        async with _WORKER_LOCK:
            if wid in _WORKERS and not _WORKERS[wid].done():
                _touch_worker(redis_client, wid)
                return jsonify({"ok": True, "worker_id": wid, "status": "already_running"}), 200
            if _has_worker_owner(redis_client, wid):
                # Worker existe en Redis (posiblemente en otro pod).
                # Verificar si está vivo: si el touch key expiró, el worker es zombie.
                last_touch = _get_worker_last_touch(redis_client, wid)
                touch_age = time.time() - last_touch
                if touch_age > WORKER_IDLE_TIMEOUT_S:
                    # Zombie detectado: el owner key existe pero nadie hace polling.
                    # Forzar takeover: eliminar owner viejo y reclamar.
                    logger.info("[LiveEntries] Zombie worker detectado %s (sin touch %.0fs) — takeover", wid, touch_age)
                    _clear_worker_owner(redis_client, wid)
                    _request_worker_stop(redis_client, wid)
                else:
                    _touch_worker(redis_client, wid)
                    return jsonify({"ok": True, "worker_id": wid, "status": "already_running"}), 200
            if not _claim_worker_owner(redis_client, wid):
                _touch_worker(redis_client, wid)
                return jsonify({"ok": True, "worker_id": wid, "status": "already_running"}), 200

            loop = asyncio.get_event_loop()
            task = loop.create_task(
                _live_worker(
                    worker_key=wid,
                    exec_id=exec_id,
                    symbol=symbol,
                    tfs=tfs,
                    user_id=user_id,
                    redis_client=redis_client,
                    load_cache=load_cache,
                    maybe_refresh_from_gcs=maybe_refresh_from_gcs,
                    fetch_historical_range=fetch_historical_range,
                    fetch_events_for=fetch_events_for,
                    norm_tf_fn=norm_tf,
                    tf_ms_fn=tf_ms,
                    current_closed_bucket_start=current_closed_bucket_start,
                    calcular_entradas_sync_wrapper=calcular_entradas_sync_wrapper,
                    charge_monitoreo_per_call=charge_monitoreo_per_call,
                    reponer_transaccion=reponer_transaccion,
                    services=services,
                )
            )
            _WORKERS[wid] = task
            _touch_worker(redis_client, wid)

        logger.info("[LiveEntries] Worker iniciado: %s platform=%s tfs=%s", wid, platform, tfs)
        return jsonify({"ok": True, "worker_id": wid, "status": "started"}), 200

    # ── GET /monitoreo/live-entries ─────────────────────────────────────────
    @app.route("/monitoreo/live-entries", methods=["GET"])
    async def live_entries_get():
        exec_id = request.args.get("exec_id", "").strip()
        symbol = (request.args.get("symbol") or "").upper().strip()
        tfs_raw = request.args.get("tfs", "")
        last_event_id = request.headers.get("Last-Event-ID") or request.args.get("last_event_id")
        since_ts_raw = request.args.get("since_ts")

        if not exec_id or not symbol:
            return jsonify({"error": "exec_id y symbol son requeridos"}), 400

        tfs = [norm_tf(t) for t in tfs_raw.split(",") if t.strip()] if tfs_raw else []
        since_ts = int(since_ts_raw) if since_ts_raw else None
        wid = _worker_id(exec_id, symbol)

        # Actualizar last poll para evitar idle timeout incluso si GET cae en otro pod
        _touch_worker(redis_client, wid)

        # Entradas del Redis
        entries = _get_entries_from_redis(redis_client, exec_id, symbol, tfs, since_ts)

        # Beat info + sr_levels
        beat_data: dict = _get_beat(redis_client, exec_id, symbol)
        last_beat_ts = beat_data.get("ts", 0)
        sr_levels = beat_data.get("sr_levels", {})
        candles_payload = beat_data.get("candles", {})

        # candles_count por TF
        candles_count: dict[str, int] = {}
        for tf in tfs:
            try:
                st = await asyncio.to_thread(load_cache, exec_id, symbol, tf)
                candles_count[tf] = len(st.get("series") or [])
            except Exception:
                candles_count[tf] = 0

        worker_status = _worker_status(redis_client, wid, beat_data)

        return jsonify({
            "entries": entries,
            "last_beat_ts": last_beat_ts,
            "worker_status": worker_status,
            "candles_count": candles_count,
            "sr_levels": sr_levels,
            "candles": candles_payload,
        }), 200

    # ── GET /monitoreo/live-entries/stream ───────────────────────────────────
    @app.route("/monitoreo/live-entries/stream", methods=["GET"])
    def live_entries_stream():
        """
        Canal push SSE para RN/Web futuro.

        No migra el frontend actual: solo expone eventos cuando el worker backend
        ya está generando entradas para exec_id+symbol. Redis Pub/Sub se usa si
        está disponible; si no, queda un bus in-process para desarrollo/instancia única.
        """
        exec_id = request.args.get("exec_id", "").strip()
        symbol = (request.args.get("symbol") or "").upper().strip()
        tfs_raw = request.args.get("tfs", "")
        last_event_id = request.headers.get("Last-Event-ID") or request.args.get("last_event_id")

        if not exec_id or not symbol:
            return jsonify({"error": "exec_id y symbol son requeridos"}), 400

        wid = _worker_id(exec_id, symbol)
        tfs_filter = {norm_tf(t) for t in tfs_raw.split(",") if t.strip()} if tfs_raw else set()
        _touch_worker(redis_client, wid)
        ready_beat_data = _get_beat(redis_client, exec_id, symbol)

        def _sse(event_name: str, payload: dict) -> str:
            event_id = payload.get("id")
            prefix = f"id: {event_id}\n" if event_id else ""
            return f"{prefix}event: {event_name}\ndata: {json.dumps(payload, separators=(',', ':'))}\n\n"

        def _event_stream():
            pubsub = None
            local_key = None
            local_q = None
            last_heartbeat = 0.0
            try:
                yield "retry: 5000\n\n"
                yield _sse("ready", {
                    "type": "ready",
                    "exec_id": exec_id,
                    "symbol": symbol,
                    "worker_status": _worker_status(redis_client, wid, ready_beat_data),
                    "candles": ready_beat_data.get("candles", {}),
                    "ts": int(time.time() * 1000),
                })
                for replay_event in _get_events_for_replay(redis_client, exec_id, symbol, last_event_id, tfs_filter, norm_tf):
                    yield _sse(replay_event.get("type") or "entries", replay_event)

                if redis_client is not None:
                    pubsub = redis_client.pubsub(ignore_subscribe_messages=True)
                    pubsub.subscribe(_redis_events_channel(exec_id, symbol))
                else:
                    local_key, local_q = _subscribe_local(exec_id, symbol)

                while True:
                    _touch_worker(redis_client, wid)
                    payload = None
                    if pubsub is not None:
                        msg = pubsub.get_message(timeout=1.0)
                        if msg and msg.get("type") == "message":
                            try:
                                payload = json.loads(msg.get("data") or "{}")
                            except Exception:
                                payload = None
                    elif local_q is not None:
                        try:
                            payload = local_q.get(timeout=1.0)
                        except queue.Empty:
                            payload = None

                    if payload:
                        tf = norm_tf(payload.get("timeframe") or payload.get("tf") or "")
                        if not tfs_filter or tf in tfs_filter:
                            yield _sse(payload.get("type") or "entries", payload)

                    now = time.time()
                    if now - last_heartbeat >= 25:
                        last_heartbeat = now
                        heartbeat_beat_data = _get_beat(redis_client, exec_id, symbol)
                        yield _sse("heartbeat", {
                            "type": "heartbeat",
                            "exec_id": exec_id,
                            "symbol": symbol,
                            "candles": heartbeat_beat_data.get("candles", {}),
                            "ts": int(now * 1000),
                        })
            finally:
                if pubsub is not None:
                    try:
                        pubsub.close()
                    except Exception:
                        pass
                if local_key and local_q is not None:
                    _unsubscribe_local(local_key, local_q)

        return Response(
            stream_with_context(_event_stream()),
            mimetype="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    # ── POST /monitoreo/live-entries/stop ──────────────────────────────────
    @app.route("/monitoreo/live-entries/stop", methods=["POST"])
    async def live_entries_stop():
        body = request.get_json(force=True) or {}
        exec_id = body.get("exec_id", "").strip()
        symbol = (body.get("symbol") or "").upper().strip()

        if not exec_id or not symbol:
            return jsonify({"error": "exec_id y symbol son requeridos"}), 400

        wid = _worker_id(exec_id, symbol)
        _request_worker_stop(redis_client, wid)
        async with _WORKER_LOCK:
            task = _WORKERS.pop(wid, None)
            _WORKER_LAST_POLL.pop(wid, None)
            _clear_worker_owner(redis_client, wid)
            if task and not task.done():
                task.cancel()
                logger.info("[LiveEntries] Worker detenido: %s", wid)
                return jsonify({"ok": True, "stopped": wid}), 200

        return jsonify({"ok": True, "stopped": wid, "was_running": False}), 200

    # ── GET /monitoreo/live-entries/workers ───────────────────────────────
    @app.route("/monitoreo/live-entries/workers", methods=["GET"])
    async def live_entries_workers():
        """Lista workers activos en este pod + en Redis (multi-pod).
        Permite detectar zombies: workers con owner en Redis pero sin touch reciente."""
        local_workers = []
        async with _WORKER_LOCK:
            for wid, task in _WORKERS.items():
                last_poll = _WORKER_LAST_POLL.get(wid, 0)
                local_workers.append({
                    "worker_id": wid,
                    "running": not task.done(),
                    "last_poll_ago_s": round(time.time() - last_poll, 1) if last_poll else None,
                    "pod": _POD_ID,
                })
        redis_workers = []
        if redis_client is not None:
            try:
                for key in redis_client.scan_iter("live_worker_owner:*"):
                    wid = key.split(":", 1)[1] if isinstance(key, str) else key.decode().split(":", 1)[1]
                    last_touch = _get_worker_last_touch(redis_client, wid)
                    touch_age = time.time() - last_touch if last_touch else None
                    redis_workers.append({
                        "worker_id": wid,
                        "owner_pod": redis_client.get(key) or "",
                        "last_touch_ago_s": round(touch_age, 1) if touch_age else None,
                        "is_zombie": touch_age is not None and touch_age > WORKER_ZOMBIE_TIMEOUT_S if touch_age else False,
                    })
            except Exception as exc:
                logger.warning("[LiveEntries] workers list failed: %s", exc)
        return jsonify({
            "pod_id": _POD_ID,
            "local_workers": local_workers,
            "redis_workers": redis_workers,
            "idle_timeout_s": WORKER_IDLE_TIMEOUT_S,
            "zombie_timeout_s": WORKER_ZOMBIE_TIMEOUT_S,
        }), 200

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

        # Buscar en todos los TFs conocidos para este exec+symbol
        beat_data = _get_beat(redis_client, exec_id, symbol)
        tfs = beat_data.get("tfs") or []

        _set_outcome_in_redis(redis_client, exec_id, symbol, tfs, entry_id, outcome, close_price, closed_at)
        logger.info("[LiveEntries] Outcome %s → %s para %s", entry_id, outcome, symbol)
        return jsonify({"ok": True}), 200
