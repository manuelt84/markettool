"""Durable backend-owned bot order orchestration.

This module is intentionally broker-agnostic at the ledger/state level. Broker
adapters can fail or be unavailable; the durable order record still gives the
frontend and recovery jobs a single source of truth.
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal


OrderAction = Literal["open", "close", "reconcile"]
OrderStatus = Literal[
    "planned",
    "queued",
    "sent",
    "ack",
    "open",
    "close_requested",
    "closed",
    "failed",
    "session_required",
    "reconcile_needed",
]


def _now_ms() -> int:
    return int(time.time() * 1000)


def _default_store_path() -> Path:
    raw = os.getenv("MARKETTOOL_BOT_ORCHESTRATOR_PATH", "").strip()
    if raw:
        return Path(raw)
    return Path("data") / "bot_orchestrator_state.json"


def _normalize_symbol(value: Any) -> str:
    return str(value or "").replace("/", "").strip().upper()


def _normalize_side(value: Any) -> str:
    raw = str(value or "").strip().lower()
    if raw in {"long", "buy", "compra"}:
        return "buy"
    if raw in {"short", "sell", "venta"}:
        return "sell"
    return raw


def _safe_public_payload(value: Any) -> Any:
    """Drop obvious sensitive session material before persisting audit records."""
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            lowered = str(key).lower()
            if any(token in lowered for token in ("password", "cookie", "csrf", "token", "secret", "session")):
                result[key] = "[redacted]"
            else:
                result[key] = _safe_public_payload(item)
        return result
    if isinstance(value, list):
        return [_safe_public_payload(item) for item in value]
    return value


@dataclass
class BotOrder:
    id: str
    idempotency_key: str
    user_id: str
    bot_type: str
    action: OrderAction
    broker: str
    symbol: str
    side: str = ""
    timeframe: str = ""
    entry_id: str = ""
    status: OrderStatus = "planned"
    broker_order_id: str | None = None
    broker_position_id: str | None = None
    message: str = ""
    request: dict[str, Any] = field(default_factory=dict)
    broker_response: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    created_at: int = field(default_factory=_now_ms)
    updated_at: int = field(default_factory=_now_ms)
    retry_count: int = 0


@dataclass
class BotPosition:
    id: str
    user_id: str
    bot_type: str
    broker: str
    symbol: str
    side: str
    timeframe: str = ""
    entry_id: str = ""
    order_id: str = ""
    broker_position_id: str | None = None
    broker_order_id: str | None = None
    status: Literal["open", "closing", "closed", "orphan", "unknown"] = "open"
    entry_price: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    amount: float | None = None
    leverage: float | None = None
    opened_at: int = field(default_factory=_now_ms)
    closed_at: int | None = None
    updated_at: int = field(default_factory=_now_ms)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class BotEvent:
    id: str
    order_id: str
    event_type: str
    payload: dict[str, Any]
    created_at: int = field(default_factory=_now_ms)


class BotOrchestratorService:
    """Small durable JSON ledger for backend-managed bot operations.

    Persists to local JSON file AND Redis (if available) for multi-pod durability.
    """

    _REDIS_LEDGER_KEY = "bot_orchestrator:ledger"
    _REDIS_LEDGER_TTL_S = 86400 * 7  # 7 days

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or _default_store_path()
        self._lock = threading.RLock()
        self._redis = self._init_redis()

    def _init_redis(self) -> Any:
        try:
            import os, redis as _redis_mod
            url = os.getenv("LIVE_ENTRIES_REDIS_URL") or os.getenv("REDIS_URL", "redis://redis:6379/0")
            client = _redis_mod.Redis.from_url(url, decode_responses=True, socket_connect_timeout=3)
            client.ping()
            return client
        except Exception:
            return None

    def _empty_state(self) -> dict[str, Any]:
        return {"orders": {}, "positions": {}, "events": []}

    def _load(self) -> dict[str, Any]:
        # Try Redis first (multi-pod source of truth)
        if self._redis is not None:
            try:
                raw = self._redis.get(self._REDIS_LEDGER_KEY)
                if raw:
                    data = json.loads(raw)
                    if isinstance(data, dict):
                        data.setdefault("orders", {})
                        data.setdefault("positions", {})
                        data.setdefault("events", [])
                        return data
            except Exception:
                pass
        # Fallback to local file
        if not self.path.exists():
            return self._empty_state()
        try:
            with self.path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
            if not isinstance(data, dict):
                return self._empty_state()
            data.setdefault("orders", {})
            data.setdefault("positions", {})
            data.setdefault("events", [])
            return data
        except Exception:
            return self._empty_state()

    def _save(self, state: dict[str, Any]) -> None:
        # Save to local file
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as handle:
            json.dump(state, handle, ensure_ascii=False, sort_keys=True)
        tmp.replace(self.path)
        # Also persist to Redis for multi-pod
        if self._redis is not None:
            try:
                self._redis.setex(self._REDIS_LEDGER_KEY, self._REDIS_LEDGER_TTL_S, json.dumps(state, ensure_ascii=False))
            except Exception:
                pass

    def _append_event(self, state: dict[str, Any], order_id: str, event_type: str, payload: dict[str, Any]) -> None:
        event = BotEvent(
            id=str(uuid.uuid4()),
            order_id=order_id,
            event_type=event_type,
            payload=_safe_public_payload(payload),
        )
        events = state.setdefault("events", [])
        events.append(asdict(event))
        state["events"] = events[-1000:]

    def create_order(self, payload: dict[str, Any]) -> tuple[BotOrder, bool]:
        """Create or return an existing order by idempotency key.

        Returns (order, created).
        """
        with self._lock:
            state = self._load()
            request_payload = dict(payload or {})
            user_id = str(request_payload.get("user_id") or "anonymous")
            bot_type = str(request_payload.get("bot_type") or "trading").lower()
            action = str(request_payload.get("action") or "open").lower()
            if action not in {"open", "close", "reconcile"}:
                raise ValueError(f"Invalid bot action: {action}")
            broker = str(request_payload.get("broker") or request_payload.get("brokerName") or "mt5").lower()
            entry = request_payload.get("entry") if isinstance(request_payload.get("entry"), dict) else request_payload
            symbol = _normalize_symbol(entry.get("symbol") or request_payload.get("symbol"))
            side = _normalize_side(entry.get("side") or request_payload.get("side"))
            timeframe = str(entry.get("timeframe") or entry.get("tf") or request_payload.get("timeframe") or "").strip()
            entry_id = str(entry.get("id") or request_payload.get("entry_id") or request_payload.get("entryId") or "")

            idem = str(
                request_payload.get("idempotency_key")
                or request_payload.get("correlation_id")
                or f"{user_id}:{bot_type}:{action}:{broker}:{symbol}:{timeframe}:{side}:{entry_id}"
            )
            for raw in state["orders"].values():
                if raw.get("idempotency_key") == idem:
                    return BotOrder(**raw), False

            order = BotOrder(
                id=str(uuid.uuid4()),
                idempotency_key=idem,
                user_id=user_id,
                bot_type=bot_type,
                action=action,  # type: ignore[arg-type]
                broker=broker,
                symbol=symbol,
                side=side,
                timeframe=timeframe,
                entry_id=entry_id,
                status="queued",
                request=_safe_public_payload(request_payload),
                message="Order queued by backend orchestrator",
            )
            state["orders"][order.id] = asdict(order)
            self._append_event(state, order.id, "order_queued", asdict(order))
            self._save(state)
            return order, True

    def update_order(
        self,
        order_id: str,
        *,
        status: OrderStatus | None = None,
        message: str | None = None,
        broker_response: dict[str, Any] | None = None,
        broker_order_id: str | None = None,
        broker_position_id: str | None = None,
        error: str | None = None,
    ) -> BotOrder:
        with self._lock:
            state = self._load()
            raw = state["orders"].get(order_id)
            if not raw:
                raise KeyError(order_id)
            if status:
                raw["status"] = status
            if message is not None:
                raw["message"] = message
            if broker_response is not None:
                raw["broker_response"] = _safe_public_payload(broker_response)
            if broker_order_id is not None:
                raw["broker_order_id"] = str(broker_order_id)
            if broker_position_id is not None:
                raw["broker_position_id"] = str(broker_position_id)
            if error is not None:
                raw["error"] = str(error)
            raw["updated_at"] = _now_ms()
            state["orders"][order_id] = raw
            self._append_event(state, order_id, f"order_{raw['status']}", raw)
            self._save(state)
            return BotOrder(**raw)

    def upsert_position_from_order(self, order: BotOrder, payload: dict[str, Any]) -> BotPosition:
        with self._lock:
            state = self._load()
            broker_position_id = (
                payload.get("investId")
                or payload.get("invest_id")
                or payload.get("mt5_order_id")
                or payload.get("orderId")
                or order.broker_position_id
                or order.broker_order_id
            )
            position_id = f"{order.broker}:{broker_position_id}" if broker_position_id else f"order:{order.id}"
            entry = order.request.get("entry") if isinstance(order.request.get("entry"), dict) else order.request
            position = BotPosition(
                id=position_id,
                user_id=order.user_id,
                bot_type=order.bot_type,
                broker=order.broker,
                symbol=order.symbol,
                side=order.side,
                timeframe=order.timeframe,
                entry_id=order.entry_id,
                order_id=order.id,
                broker_position_id=str(broker_position_id) if broker_position_id else None,
                broker_order_id=order.broker_order_id,
                status="open",
                entry_price=_float_or_none(entry.get("entry") or entry.get("entry_price") or payload.get("openPrice") or payload.get("open_price")),
                stop_loss=_float_or_none(entry.get("sl") or entry.get("stop_loss")),
                take_profit=_float_or_none(entry.get("tp") or entry.get("take_profit")),
                amount=_float_or_none(order.request.get("amount") or order.request.get("volume") or entry.get("amount")),
                leverage=_float_or_none(order.request.get("leverage") or entry.get("leverage")),
                raw=_safe_public_payload(payload),
            )
            state["positions"][position.id] = asdict(position)
            self._append_event(state, order.id, "position_open", asdict(position))
            self._save(state)
            return position

    def list_orders(self, user_id: str | None = None, status: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            orders = list(self._load()["orders"].values())
        if user_id:
            orders = [order for order in orders if order.get("user_id") == user_id]
        if status:
            orders = [order for order in orders if order.get("status") == status]
        return sorted(orders, key=lambda item: int(item.get("created_at") or 0), reverse=True)

    def list_positions(self, user_id: str | None = None, status: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            positions = list(self._load()["positions"].values())
        if user_id:
            positions = [position for position in positions if position.get("user_id") == user_id]
        if status:
            positions = [position for position in positions if position.get("status") == status]
        return sorted(positions, key=lambda item: int(item.get("updated_at") or 0), reverse=True)


def _float_or_none(value: Any) -> float | None:
    try:
        parsed = float(value)
        return parsed if parsed == parsed else None
    except Exception:
        return None


_SERVICE: BotOrchestratorService | None = None


def get_bot_orchestrator_service() -> BotOrchestratorService:
    global _SERVICE
    if _SERVICE is None:
        _SERVICE = BotOrchestratorService()
    return _SERVICE
