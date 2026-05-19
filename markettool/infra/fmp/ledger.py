"""Lightweight FMP usage ledger.

The ledger is intentionally best-effort: FMP calls must never fail because the
usage accounting backend is unavailable. Redis stores aggregate counters and a
short recent-call list; an in-memory fallback keeps diagnostics useful in local
or test runs without Redis.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from zoneinfo import ZoneInfo
import json
import logging
import os
import threading
import time

try:
    import redis
except Exception:  # pragma: no cover - optional dependency
    redis = None

logger = logging.getLogger("MarketTool.FMP.Ledger")

_LOCAL_LOCK = threading.Lock()
_LOCAL_SUMMARY: dict[str, int] = {"calls": 0, "errors": 0, "bytes": 0}
_LOCAL_RECENT: list[dict[str, Any]] = []
_TLS = threading.local()
_REDIS_CLIENT = None
_REDIS_READY = False
_REDIS_LOCK = threading.Lock()


def _enabled() -> bool:
    return str(os.getenv("FMP_LEDGER_ENABLED", "true")).strip().lower() in {"1", "true", "yes", "on"}


def _namespace() -> str:
    return (os.getenv("FMP_LEDGER_NAMESPACE") or "fmp_ledger").strip() or "fmp_ledger"


def _redis_url() -> str:
    return (
        os.getenv("FMP_LEDGER_REDIS_URL")
        or os.getenv("MARKET_DATA_REDIS_URL")
        or os.getenv("LIVE_ENTRIES_REDIS_URL")
        or os.getenv("REDIS_URL")
        or ""
    ).strip()


def _redis():
    global _REDIS_CLIENT, _REDIS_READY
    if _REDIS_READY:
        return _REDIS_CLIENT
    with _REDIS_LOCK:
        if _REDIS_READY:
            return _REDIS_CLIENT
        url = _redis_url()
        if not url or redis is None:
            _REDIS_READY = True
            _REDIS_CLIENT = None
            return None
        try:
            client = redis.Redis.from_url(url, decode_responses=True, socket_connect_timeout=1, socket_timeout=2)
            client.ping()
            _REDIS_CLIENT = client
        except Exception as exc:
            logger.debug("FMP ledger Redis unavailable: %s", exc)
            _REDIS_CLIENT = None
        _REDIS_READY = True
        return _REDIS_CLIENT


def _sanitize_url(url: str) -> str:
    try:
        parts = urlsplit(url)
        query = [
            (k, v)
            for k, v in parse_qsl(parts.query, keep_blank_values=True)
            if k.lower() not in {"apikey", "api_key", "token", "key"}
        ]
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))
    except Exception:
        return str(url).split("?apikey=", 1)[0]


def _endpoint_from_url(url: str) -> str:
    path = urlsplit(url).path if "://" in str(url) else str(url)
    parts = [p for p in path.split("/") if p and p not in {"api", "v3", "v4", "stable"}]
    if not parts:
        return "unknown"
    if parts[0] == "historical-chart" and len(parts) > 1:
        return f"historical-chart/{parts[1]}"
    return parts[0]


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _ledger_now() -> datetime:
    tz_name = (
        os.getenv("FMP_LEDGER_DAY_TZ")
        or os.getenv("MARKET_TIMEZONE")
        or os.getenv("FMP_DAILY_SOURCE_TZ")
        or "America/New_York"
    )
    try:
        return datetime.now(ZoneInfo(tz_name))
    except Exception:
        return datetime.now(ZoneInfo("America/New_York"))


def current_context() -> dict[str, Any]:
    ctx = getattr(_TLS, "ctx", None)
    return dict(ctx or {})


@contextmanager
def fmp_context(**fields: Any):
    prev = current_context()
    merged = {**prev, **{k: v for k, v in fields.items() if v is not None and v != ""}}
    _TLS.ctx = merged
    try:
        yield
    finally:
        _TLS.ctx = prev


def record_fmp_call(
    *,
    url: str,
    status_code: int | None,
    elapsed_ms: int,
    response_bytes: int = 0,
    symbol: str | None = None,
    timeframe: str | None = None,
    source: str | None = None,
    rows: int | None = None,
    error: str | None = None,
    method: str = "GET",
) -> None:
    if not _enabled():
        return
    ctx = current_context()
    endpoint = _endpoint_from_url(url)
    status = _safe_int(status_code, 0)
    byte_count = max(0, _safe_int(response_bytes, 0))
    now_local = _ledger_now()
    now_utc = datetime.now(timezone.utc)
    rec = {
        "ts": now_utc.isoformat().replace("+00:00", "Z"),
        "ledger_day": now_local.strftime("%Y-%m-%d"),
        "ledger_tz": getattr(now_local.tzinfo, "key", str(now_local.tzinfo)),
        "environment": os.getenv("MARKETTOOL_ENV") or os.getenv("APP_ENV") or os.getenv("ENV") or "unknown",
        "node_id": os.getenv("MARKET_DATA_NODE_ID") or os.getenv("WORKER_ID") or "",
        "method": method,
        "endpoint": endpoint,
        "url": _sanitize_url(url),
        "status": status,
        "ok": 200 <= status < 400,
        "elapsed_ms": max(0, _safe_int(elapsed_ms, 0)),
        "bytes": byte_count,
        "symbol": (symbol or ctx.get("symbol") or "").upper(),
        "timeframe": timeframe or ctx.get("timeframe") or ctx.get("tf") or "",
        "source": source or ctx.get("source") or "",
        "user_id": ctx.get("user_id") or "",
        "exec_id": ctx.get("exec_id") or "",
        "rows": rows if rows is not None else "",
        "error": str(error or "")[:180],
    }

    day = str(rec["ledger_day"]).replace("-", "")
    ns = _namespace()
    summary_key = f"{ns}:daily:{day}:summary"
    recent_key = f"{ns}:recent"
    ttl = max(3600, int(os.getenv("FMP_LEDGER_RETENTION_SECONDS", str(7 * 86400))))
    recent_limit = max(20, int(os.getenv("FMP_LEDGER_RECENT_LIMIT", "500")))

    client = _redis()
    if client is not None:
        try:
            pipe = client.pipeline(transaction=False)
            increments: dict[str, int] = {
                "calls": 1,
                "bytes": byte_count,
                f"status:{status}": 1,
                f"endpoint:{endpoint}:calls": 1,
                f"endpoint:{endpoint}:bytes": byte_count,
            }
            if not rec["ok"] or rec["error"]:
                increments["errors"] = 1
            if rec["source"]:
                increments[f"source:{rec['source']}:calls"] = 1
                increments[f"source:{rec['source']}:bytes"] = byte_count
            if rec["symbol"]:
                increments[f"symbol:{rec['symbol']}:calls"] = 1
            if rec["user_id"]:
                increments[f"user:{rec['user_id']}:calls"] = 1
            for field, value in increments.items():
                pipe.hincrby(summary_key, field, value)
            pipe.expire(summary_key, ttl)
            pipe.lpush(recent_key, json.dumps(rec, separators=(",", ":"), ensure_ascii=False))
            pipe.ltrim(recent_key, 0, recent_limit - 1)
            pipe.expire(recent_key, ttl)
            pipe.execute()
            return
        except Exception as exc:
            logger.debug("FMP ledger write failed: %s", exc)

    with _LOCAL_LOCK:
        _LOCAL_SUMMARY["calls"] = _LOCAL_SUMMARY.get("calls", 0) + 1
        _LOCAL_SUMMARY["bytes"] = _LOCAL_SUMMARY.get("bytes", 0) + byte_count
        if not rec["ok"] or rec["error"]:
            _LOCAL_SUMMARY["errors"] = _LOCAL_SUMMARY.get("errors", 0) + 1
        _LOCAL_RECENT.insert(0, rec)
        del _LOCAL_RECENT[recent_limit:]


def get_fmp_ledger_summary(limit_recent: int = 20) -> dict[str, Any]:
    ns = _namespace()
    now_local = _ledger_now()
    day = now_local.strftime("%Y%m%d")
    client = _redis()
    if client is not None:
        try:
            summary = client.hgetall(f"{ns}:daily:{day}:summary")
            recent_raw = client.lrange(f"{ns}:recent", 0, max(0, limit_recent - 1))
            recent = []
            for item in recent_raw:
                try:
                    recent.append(json.loads(item))
                except Exception:
                    continue
            return {
                "enabled": _enabled(),
                "backend": "redis",
                "day": day,
                "summary": {k: _safe_int(v, v) for k, v in summary.items()},
                "recent": recent,
            }
        except Exception as exc:
            logger.debug("FMP ledger read failed: %s", exc)
    with _LOCAL_LOCK:
        return {
            "enabled": _enabled(),
            "backend": "memory",
            "day": day,
            "summary": dict(_LOCAL_SUMMARY),
            "recent": list(_LOCAL_RECENT[:limit_recent]),
        }
