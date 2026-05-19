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


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except Exception:
        return default


def _default_usage_kind(endpoint: str) -> str:
    if endpoint.startswith("historical-chart"):
        return "historical_backfill"
    if endpoint in {"historical-price-full"}:
        return "historical_backfill"
    if endpoint in {"quote"}:
        return "market_quote"
    return "unknown"


def _billable_units_for_usage_kind(usage_kind: str, *, ok: bool, rows: int | None) -> tuple[int, str]:
    if not ok:
        return 0, "provider_error"
    if rows is not None and rows <= 0:
        return 0, "empty_response"
    mapping = {
        "monitoring_live_refresh": ("FMP_USAGE_UNITS_MONITORING_LIVE_REFRESH", 0),
        "monitoring_history_refresh": ("FMP_USAGE_UNITS_MONITORING_HISTORY_REFRESH", 0),
        "market_pool_refresh": ("FMP_USAGE_UNITS_MARKET_POOL_REFRESH", 0),
        "historical_backfill": ("FMP_USAGE_UNITS_HISTORICAL_BACKFILL", 0),
        "asset_analysis_basic": ("FMP_USAGE_UNITS_ASSET_ANALYSIS_BASIC", 1),
        "asset_analysis_full": ("FMP_USAGE_UNITS_ASSET_ANALYSIS_FULL", 5),
        "bot_context_analysis": ("FMP_USAGE_UNITS_BOT_CONTEXT_ANALYSIS", 2),
        "market_quote": ("FMP_USAGE_UNITS_MARKET_QUOTE", 0),
        "unknown": ("FMP_USAGE_UNITS_UNKNOWN", 1),
    }
    env_name, default = mapping.get(usage_kind, mapping["unknown"])
    return max(0, _env_int(env_name, default)), ""


def get_fmp_usage_policy() -> dict[str, Any]:
    """Return the active FMP commercial usage policy.

    This is operational metadata for dashboards/admins. It intentionally does
    not include secrets and mirrors the same env/default mapping used when
    recording calls.
    """
    mapping = {
        "monitoring_live_refresh": ("FMP_USAGE_UNITS_MONITORING_LIVE_REFRESH", 0, "Live monitoring refresh"),
        "monitoring_history_refresh": ("FMP_USAGE_UNITS_MONITORING_HISTORY_REFRESH", 0, "Monitoring history/gap refresh"),
        "market_pool_refresh": ("FMP_USAGE_UNITS_MARKET_POOL_REFRESH", 0, "Central MarketPool refresh"),
        "historical_backfill": ("FMP_USAGE_UNITS_HISTORICAL_BACKFILL", 0, "Historical cache/backfill"),
        "asset_analysis_basic": ("FMP_USAGE_UNITS_ASSET_ANALYSIS_BASIC", 1, "Basic asset analysis"),
        "asset_analysis_full": ("FMP_USAGE_UNITS_ASSET_ANALYSIS_FULL", 5, "Complete/advanced asset analysis"),
        "bot_context_analysis": ("FMP_USAGE_UNITS_BOT_CONTEXT_ANALYSIS", 2, "Bot contextual analysis"),
        "market_quote": ("FMP_USAGE_UNITS_MARKET_QUOTE", 0, "Single quote lookup"),
        "unknown": ("FMP_USAGE_UNITS_UNKNOWN", 1, "Unclassified FMP call"),
    }
    usage_kinds: dict[str, dict[str, Any]] = {}
    for usage_kind, (env_name, default, label) in mapping.items():
        usage_kinds[usage_kind] = {
            "label": label,
            "env": env_name,
            "default_units": default,
            "active_units": max(0, _env_int(env_name, default)),
        }
    return {
        "enabled": _enabled(),
        "namespace": _namespace(),
        "redis_url_configured": bool(_redis_url()),
        "ledger_day_tz": (
            os.getenv("FMP_LEDGER_DAY_TZ")
            or os.getenv("MARKET_TIMEZONE")
            or os.getenv("FMP_DAILY_SOURCE_TZ")
            or "America/New_York"
        ),
        "refunds": {
            "provider_error": "0 units when FMP/network returns an error",
            "empty_response": "0 units when FMP returns a successful empty payload",
        },
        "usage_kinds": usage_kinds,
    }


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
    ok = 200 <= status < 400
    usage_kind = str(ctx.get("usage_kind") or _default_usage_kind(endpoint))
    billable_units, refund_reason = _billable_units_for_usage_kind(usage_kind, ok=ok, rows=rows)
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
        "ok": ok,
        "usage_kind": usage_kind,
        "billable_units": billable_units,
        "refund_reason": refund_reason,
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
                "billable_units": billable_units,
                f"status:{status}": 1,
                f"endpoint:{endpoint}:calls": 1,
                f"endpoint:{endpoint}:bytes": byte_count,
                f"usage_kind:{usage_kind}:calls": 1,
                f"usage_kind:{usage_kind}:billable_units": billable_units,
            }
            if not rec["ok"] or rec["error"]:
                increments["errors"] = 1
            if refund_reason:
                increments[f"refund:{refund_reason}:calls"] = 1
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
        _LOCAL_SUMMARY["billable_units"] = _LOCAL_SUMMARY.get("billable_units", 0) + billable_units
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
