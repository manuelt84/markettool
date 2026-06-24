"""Backend-owned bot orchestration routes."""

from __future__ import annotations

import logging
import json
import os
import threading
import time
from datetime import datetime, timezone
from typing import Any

import requests
from flask import jsonify, request

from markettool.application.services.bot_orchestrator_service import (
    BotOrder,
    get_bot_orchestrator_service,
)
from markettool.application.services.broker_session_store import get_broker_session_store

logger = logging.getLogger(__name__)
_DAEMONS: dict[str, dict[str, Any]] = {}
_DAEMONS_LOCK = threading.RLock()


def _daemon_id(user_id: str, bot_type: str, exec_id: str, symbol: str) -> str:
    return f"{user_id}:{bot_type}:{exec_id}:{symbol.upper()}"


def _redis_client_for_daemon():
    try:
        import redis as _redis

        redis_url = (
            os.getenv("BOT_DAEMON_REDIS_URL")
            or os.getenv("LIVE_ENTRIES_REDIS_URL")
            or os.getenv("REDIS_URL", "redis://redis:6379/0")
        )
        client = _redis.Redis.from_url(redis_url, decode_responses=True, socket_connect_timeout=3)
        client.ping()
        return client
    except Exception:
        return None


def _redis_daemon_key(daemon_id: str) -> str:
    return f"bot_daemon:{daemon_id}"


def _redis_daemon_stop_key(daemon_id: str) -> str:
    return f"bot_daemon_stop:{daemon_id}"


def _write_daemon_status(redis_client, state: dict[str, Any]) -> None:
    if redis_client is None:
        return
    try:
        redis_client.setex(_redis_daemon_key(str(state.get("id"))), 180, json.dumps(_public_daemon(state)))
    except Exception as exc:
        logger.debug("Bot daemon redis status write failed: %s", exc)


def _read_daemon_status(redis_client, daemon_id: str) -> dict[str, Any] | None:
    if redis_client is None:
        return None
    try:
        raw = redis_client.get(_redis_daemon_key(daemon_id))
        return json.loads(raw) if raw else None
    except Exception:
        return None


def _list_redis_daemon_status(redis_client) -> list[dict[str, Any]]:
    if redis_client is None:
        return []
    try:
        result: list[dict[str, Any]] = []
        for key in redis_client.scan_iter("bot_daemon:*"):
            raw = redis_client.get(key)
            if raw:
                result.append(json.loads(raw))
        return result
    except Exception as exc:
        logger.debug("Bot daemon redis status list failed: %s", exc)
        return []


def register_bot_orchestrator_routes(app) -> None:
    service = get_bot_orchestrator_service()

    @app.route("/api/v1/bot-orchestrator/orders", methods=["POST"])
    def create_bot_order():
        payload = request.get_json(silent=True) or {}
        try:
            order, created = service.create_order(payload)
        except ValueError as exc:
            return jsonify({"status": "failed", "error": str(exc)}), 400

        if not created:
            return jsonify({"status": "duplicate", "order": order.__dict__}), 200

        execute_now = bool(payload.get("execute_now", True))
        if not execute_now:
            return jsonify({"status": "queued", "order": order.__dict__}), 202

        executed = _execute_order(order, payload)
        return jsonify({"status": executed.status, "order": executed.__dict__}), _status_code(executed.status)

    @app.route("/api/v1/bot-orchestrator/orders", methods=["GET"])
    def list_bot_orders():
        return jsonify({
            "orders": service.list_orders(
                user_id=request.args.get("user_id"),
                status=request.args.get("status"),
            )
        }), 200

    @app.route("/api/v1/bot-orchestrator/positions", methods=["GET"])
    def list_bot_positions():
        return jsonify({
            "positions": service.list_positions(
                user_id=request.args.get("user_id"),
                status=request.args.get("status"),
            )
        }), 200

    @app.route("/api/v1/bot-orchestrator/sessions/libertex", methods=["POST"])
    def save_libertex_session():
        payload = request.get_json(silent=True) or {}
        user_id = str(payload.get("user_id") or "anonymous")
        session_payload = payload.get("libertex_session") or payload.get("session") or {}
        if not isinstance(session_payload, dict) or not session_payload:
            return jsonify({"status": "failed", "error": "libertex_session is required"}), 400
        saved = get_broker_session_store().save_session(
            user_id=user_id,
            broker="libertex",
            session_payload=session_payload,
            account_hint=str(payload.get("account_hint") or "default"),
            expires_at=payload.get("expires_at"),
        )
        return jsonify({"status": "ok", "session": saved}), 200

    @app.route("/api/v1/bot-orchestrator/sessions", methods=["GET"])
    def list_broker_sessions():
        return jsonify({
            "sessions": get_broker_session_store().list_sessions(user_id=request.args.get("user_id"))
        }), 200

    @app.route("/api/v1/bot-orchestrator/daemon/start", methods=["POST"])
    def start_bot_daemon():
        payload = request.get_json(silent=True) or {}
        enabled = bool(payload.get("enabled", False))
        if not enabled:
            return jsonify({"status": "disabled", "message": "Daemon requires enabled=true"}), 200

        user_id = str(payload.get("user_id") or "anonymous")
        bot_type = str(payload.get("bot_type") or "trading").lower()
        exec_id = str(payload.get("exec_id") or "").strip()
        symbol = str(payload.get("symbol") or "").replace("/", "").upper().strip()
        tfs = [str(tf).strip().lower() for tf in (payload.get("tfs") or []) if str(tf).strip()]
        broker = str(payload.get("broker") or "mt5").lower()
        if not exec_id or not symbol or not tfs:
            return jsonify({"status": "failed", "error": "exec_id, symbol and tfs are required"}), 400

        daemon_id = _daemon_id(user_id, bot_type, exec_id, symbol)
        redis_client = _redis_client_for_daemon()
        with _DAEMONS_LOCK:
            current = _DAEMONS.get(daemon_id)
            if current and current.get("running"):
                execution_config = _normalize_execution_config(payload, tfs)
                current["last_start_payload"] = _public_daemon_payload({**payload, "execution_config": execution_config})
                current["payload"] = {**payload, "user_id": user_id, "bot_type": bot_type, "exec_id": exec_id, "symbol": symbol, "tfs": tfs, "broker": broker, "execution_config": execution_config}
                _write_daemon_status(redis_client, current)
                return jsonify({"status": "already_running", "daemon_id": daemon_id, "daemon": _public_daemon(current)}), 200
            remote = _read_daemon_status(redis_client, daemon_id)
            if remote and remote.get("running") and int(time.time() * 1000) - int(remote.get("last_tick_at") or remote.get("started_at") or 0) < 180_000:
                return jsonify({"status": "already_running", "daemon_id": daemon_id, "daemon": remote}), 200

            stop_event = threading.Event()
            execution_config = _normalize_execution_config(payload, tfs)
            state: dict[str, Any] = {
                "id": daemon_id,
                "running": True,
                "stop_event": stop_event,
                "started_at": int(time.time() * 1000),
                "last_tick_at": None,
                "last_error": None,
                "orders_submitted": 0,
                "entries_seen": 0,
                "entries_rejected": 0,
                "last_decision": None,
                "paused": bool(execution_config.get("paused")),
                "pause_reason": execution_config.get("pause_reason"),
                "last_start_payload": _public_daemon_payload({**payload, "execution_config": execution_config}),
            }
            state["payload"] = {**payload, "user_id": user_id, "bot_type": bot_type, "exec_id": exec_id, "symbol": symbol, "tfs": tfs, "broker": broker, "execution_config": execution_config}
            thread = threading.Thread(
                target=_run_bot_daemon,
                args=(state, state["payload"]),
                daemon=True,
                name=f"bot-daemon-{bot_type}-{symbol}",
            )
            state["thread"] = thread
            _DAEMONS[daemon_id] = state
            _write_daemon_status(redis_client, state)
            thread.start()

        return jsonify({"status": "started", "daemon_id": daemon_id, "daemon": _public_daemon(state)}), 200

    @app.route("/api/v1/bot-orchestrator/daemon/stop", methods=["POST"])
    def stop_bot_daemon():
        payload = request.get_json(silent=True) or {}
        daemon_id = str(payload.get("daemon_id") or "")
        if not daemon_id:
            daemon_id = _daemon_id(
                str(payload.get("user_id") or "anonymous"),
                str(payload.get("bot_type") or "trading").lower(),
                str(payload.get("exec_id") or "").strip(),
                str(payload.get("symbol") or "").replace("/", "").upper().strip(),
            )
        with _DAEMONS_LOCK:
            state = _DAEMONS.get(daemon_id)
            redis_client = _redis_client_for_daemon()
            if redis_client is not None:
                try:
                    redis_client.setex(_redis_daemon_stop_key(daemon_id), 180, "1")
                except Exception:
                    pass
            if not state:
                return jsonify({"status": "stopping", "daemon_id": daemon_id, "local": False}), 200
            state["running"] = False
            stop_event = state.get("stop_event")
            if stop_event:
                stop_event.set()
            _write_daemon_status(redis_client, state)
        return jsonify({"status": "stopping", "daemon_id": daemon_id}), 200

    @app.route("/api/v1/bot-orchestrator/daemon/status", methods=["GET"])
    def bot_daemon_status():
        user_id = request.args.get("user_id")
        exec_id = request.args.get("exec_id")
        symbol = request.args.get("symbol")
        bot_type = request.args.get("bot_type")
        with _DAEMONS_LOCK:
            raw_daemons = list(_DAEMONS.values())
        redis_daemons = _list_redis_daemon_status(_redis_client_for_daemon())
        by_id = {item.get("id"): item for item in redis_daemons if item.get("id")}
        by_id.update({item.get("id"): item for item in [_public_daemon(item) for item in raw_daemons] if item.get("id")})
        daemons = list(by_id.values())
        if user_id:
            daemons = [item for item in daemons if item.get("user_id") == user_id]
        if exec_id:
            daemons = [item for item in daemons if item.get("exec_id") == exec_id]
        if symbol:
            norm_symbol = str(symbol).replace("/", "").upper()
            daemons = [item for item in daemons if str(item.get("symbol") or "").replace("/", "").upper() == norm_symbol]
        if bot_type:
            daemons = [item for item in daemons if item.get("bot_type") == bot_type]
        orders = _filter_status_items(service.list_orders(user_id=user_id) if user_id else [], exec_id=exec_id, symbol=symbol, bot_type=bot_type)[:100]
        positions = _filter_status_items(service.list_positions(user_id=user_id) if user_id else [], exec_id=exec_id, symbol=symbol, bot_type=bot_type)[:100]
        return jsonify({"daemons": daemons, "orders": orders, "positions": positions}), 200

    @app.route("/api/v1/bot-orchestrator/reconcile", methods=["POST"])
    def reconcile_bot_positions():
        payload = request.get_json(silent=True) or {}
        broker = str(payload.get("broker") or "mt5").lower()
        if broker == "mt5":
            try:
                from markettool.interfaces.api.mt5_routes import get_mt5_service

                mt5 = get_mt5_service()
                positions = mt5.get_open_positions()
                return jsonify({"status": "ok", "broker": "mt5", "positions": positions}), 200
            except Exception as exc:
                logger.warning("MT5 reconcile failed: %s", exc, exc_info=True)
                return jsonify({"status": "failed", "broker": "mt5", "error": str(exc)}), 500

        if broker == "libertex":
            session_data = _resolve_libertex_session(payload, str(payload.get("user_id") or "anonymous"))
            if not session_data:
                return jsonify({
                    "status": "session_required",
                    "broker": "libertex",
                    "message": "Libertex reconciliation requires an encrypted/passed session bundle.",
                }), 428
            result, code = _libertex_get_open_positions(session_data)
            return jsonify(result), code

        return jsonify({"status": "failed", "error": f"Unsupported broker: {broker}"}), 400


def _status_code(status: str) -> int:
    if status in {"ack", "open", "closed"}:
        return 200
    if status in {"queued", "sent"}:
        return 202
    if status == "session_required":
        return 428
    if status == "failed":
        return 502
    return 200


def _execute_order(order: BotOrder, payload: dict[str, Any]) -> BotOrder:
    service = get_bot_orchestrator_service()
    try:
        service.update_order(order.id, status="sent", message="Order sent to broker adapter")
        if order.action == "close":
            return _execute_close(order, payload)
        if order.action == "reconcile":
            return service.update_order(order.id, status="reconcile_needed", message="Reconcile orders are handled by /reconcile")
        return _execute_open(order, payload)
    except Exception as exc:
        logger.warning("Bot orchestrator execution failed: %s", exc, exc_info=True)
        return service.update_order(order.id, status="failed", message=str(exc), error=str(exc))


def _execute_open(order: BotOrder, payload: dict[str, Any]) -> BotOrder:
    broker = order.broker.lower()
    if broker == "mt5":
        return _execute_mt5_open(order, payload)
    if broker == "libertex":
        return _execute_libertex_open(order, payload)
    return get_bot_orchestrator_service().update_order(
        order.id,
        status="failed",
        message=f"Unsupported broker for backend execution: {broker}",
    )


def _execute_close(order: BotOrder, payload: dict[str, Any]) -> BotOrder:
    broker = order.broker.lower()
    if broker == "mt5":
        return _execute_mt5_close(order, payload)
    if broker == "libertex":
        return _execute_libertex_close(order, payload)
    return get_bot_orchestrator_service().update_order(
        order.id,
        status="failed",
        message=f"Unsupported broker for backend close: {broker}",
    )


def _execute_mt5_open(order: BotOrder, payload: dict[str, Any]) -> BotOrder:
    from markettool.application.services.broker_mt5_service import MT5OrderRequest
    from markettool.interfaces.api.mt5_routes import get_mt5_service

    entry = payload.get("entry") if isinstance(payload.get("entry"), dict) else payload
    mt5 = get_mt5_service()
    req = MT5OrderRequest(
        symbol=str(payload.get("broker_symbol") or entry.get("brokerSymbol") or order.symbol),
        volume=float(payload.get("volume") or payload.get("size") or entry.get("volume") or entry.get("size") or 0.01),
        side=("BUY" if order.side == "buy" else "SELL"),
        order_type="MARKET",
        entry_price=float(entry.get("entry") or entry.get("entry_price") or 0),
        stop_loss=_optional_float(entry.get("sl") or entry.get("stop_loss")),
        take_profit=_optional_float(entry.get("tp") or entry.get("take_profit")),
        deviation=float(payload.get("mt5_deviation") or 20),
        magic=int(payload.get("mt5_magic") or 0),
        comment=str(payload.get("comment") or f"MarketTool backend {order.bot_type}"),
    )
    response = mt5.place_order(req)
    raw = response.__dict__
    if not response.success:
        return get_bot_orchestrator_service().update_order(
            order.id,
            status="failed",
            message=response.message or "MT5 rejected order queue",
            broker_response=raw,
            error=response.message,
        )
    return get_bot_orchestrator_service().update_order(
        order.id,
        status="ack",
        message=response.message or "MT5 order queued",
        broker_response=raw,
        broker_order_id=str(response.order_id) if response.order_id else None,
    )


def _execute_mt5_close(order: BotOrder, payload: dict[str, Any]) -> BotOrder:
    from markettool.application.services.broker_mt5_service import MT5CloseOrderRequest
    from markettool.interfaces.api.mt5_routes import get_mt5_service

    ticket = payload.get("position_ticket") or payload.get("ticket") or payload.get("broker_position_id")
    if not ticket:
        return get_bot_orchestrator_service().update_order(
            order.id,
            status="failed",
            message="MT5 close requires position_ticket/broker_position_id",
        )
    response = get_mt5_service().close_order(
        MT5CloseOrderRequest(
            symbol=str(payload.get("broker_symbol") or order.symbol),
            position_ticket=int(ticket),
            volume=_optional_float(payload.get("volume")),
            comment=str(payload.get("comment") or f"MarketTool backend close {order.bot_type}"),
        )
    )
    raw = response.__dict__
    if not response.success:
        return get_bot_orchestrator_service().update_order(
            order.id,
            status="failed",
            message=response.message or "MT5 rejected close queue",
            broker_response=raw,
            error=response.message,
        )
    return get_bot_orchestrator_service().update_order(
        order.id,
        status="close_requested",
        message=response.message or "MT5 close queued",
        broker_response=raw,
        broker_order_id=str(response.order_id) if response.order_id else None,
    )


def _execute_libertex_open(order: BotOrder, payload: dict[str, Any]) -> BotOrder:
    session_data = _resolve_libertex_session(payload, order.user_id)
    if not session_data:
        return get_bot_orchestrator_service().update_order(
            order.id,
            status="session_required",
            message="Libertex backend execution requires a session bundle from the logged-in WebView.",
        )

    entry = payload.get("entry") if isinstance(payload.get("entry"), dict) else payload
    base_url = str(session_data.get("base_url") or "https://app.libertex.org").rstrip("/")
    csrf_token = str(session_data.get("csrf_token") or "")
    cookies = session_data.get("session_cookies") or {}
    if not csrf_token:
        return get_bot_orchestrator_service().update_order(
            order.id,
            status="session_required",
            message="Libertex session is missing csrf_token",
        )

    body = {
        "instrumentId": payload.get("instrument_id") or entry.get("instrumentId") or order.symbol,
        "direction": "buy" if order.side == "buy" else "sell",
        "amount": float(payload.get("amount") or payload.get("volume") or entry.get("amount") or 20),
        "leverage": int(payload.get("leverage") or entry.get("leverage") or 1),
    }
    if entry.get("sl") or entry.get("stop_loss"):
        body["stopLoss"] = float(entry.get("sl") or entry.get("stop_loss"))
    if entry.get("tp") or entry.get("take_profit"):
        body["takeProfit"] = float(entry.get("tp") or entry.get("take_profit"))

    resp = requests.post(
        f"{base_url}/spa/investing/open-position",
        json=body,
        headers={"X-Token": csrf_token, "Content-Type": "application/json", "Accept": "application/json"},
        cookies=cookies,
        timeout=15,
    )
    try:
        result = resp.json()
    except Exception:
        result = {"text": resp.text[:1000], "http_status": resp.status_code}

    if resp.ok and result.get("status") == "ok":
        raw_result = result.get("result") or {}
        updated = get_bot_orchestrator_service().update_order(
            order.id,
            status="open",
            message="Libertex position opened by backend",
            broker_response=result,
            broker_position_id=str(raw_result.get("investId")) if raw_result.get("investId") else None,
        )
        get_bot_orchestrator_service().upsert_position_from_order(updated, raw_result)
        return updated
    return get_bot_orchestrator_service().update_order(
        order.id,
        status="failed",
        message=str(result.get("messages") or result.get("error") or f"Libertex HTTP {resp.status_code}"),
        broker_response=result,
        error=str(result),
    )


def _execute_libertex_close(order: BotOrder, payload: dict[str, Any]) -> BotOrder:
    session_data = _resolve_libertex_session(payload, order.user_id)
    invest_id = payload.get("invest_id") or payload.get("investId") or payload.get("broker_position_id")
    if not session_data or not invest_id:
        return get_bot_orchestrator_service().update_order(
            order.id,
            status="session_required",
            message="Libertex close requires session bundle and invest_id.",
        )
    base_url = str(session_data.get("base_url") or "https://app.libertex.org").rstrip("/")
    csrf_token = str(session_data.get("csrf_token") or "")
    cookies = session_data.get("session_cookies") or {}
    resp = requests.post(
        f"{base_url}/spa/investing/close-position",
        json={"investId": invest_id},
        headers={"X-Token": csrf_token, "Content-Type": "application/json", "Accept": "application/json"},
        cookies=cookies,
        timeout=15,
    )
    try:
        result = resp.json()
    except Exception:
        result = {"text": resp.text[:1000], "http_status": resp.status_code}
    if resp.ok and result.get("status") == "ok":
        return get_bot_orchestrator_service().update_order(
            order.id,
            status="closed",
            message="Libertex position closed by backend",
            broker_response=result,
            broker_position_id=str(invest_id),
        )
    return get_bot_orchestrator_service().update_order(
        order.id,
        status="failed",
        message=str(result.get("messages") or result.get("error") or f"Libertex HTTP {resp.status_code}"),
        broker_response=result,
        error=str(result),
    )


def _libertex_get_open_positions(session_data: dict[str, Any]) -> tuple[dict[str, Any], int]:
    base_url = str(session_data.get("base_url") or "https://app.libertex.org").rstrip("/")
    csrf_token = str(session_data.get("csrf_token") or "")
    cookies = session_data.get("session_cookies") or {}
    if not csrf_token:
        return {"status": "session_required", "message": "Missing csrf_token"}, 428
    resp = requests.get(
        f"{base_url}/spa/user-investments",
        headers={"X-Token": csrf_token, "Accept": "application/json"},
        cookies=cookies,
        timeout=15,
    )
    try:
        result = resp.json()
    except Exception:
        result = {"text": resp.text[:1000], "http_status": resp.status_code}
    if resp.ok:
        return {"status": "ok", "broker": "libertex", "positions": result}, 200
    return {"status": "failed", "broker": "libertex", "raw": result}, 502


def _resolve_libertex_session(payload: dict[str, Any], user_id: str) -> dict[str, Any]:
    inline = payload.get("libertex_session") or {}
    if isinstance(inline, dict) and inline:
        return inline
    session_id = str(payload.get("broker_session_id") or payload.get("libertex_session_id") or "")
    store = get_broker_session_store()
    if session_id:
        return store.get_session(session_id) or {}
    latest = store.get_latest_session(user_id, "libertex")
    if latest:
        return latest[1]
    return {}


def _public_daemon_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "user_id": payload.get("user_id"),
        "bot_type": payload.get("bot_type"),
        "exec_id": payload.get("exec_id"),
        "symbol": payload.get("symbol"),
        "tfs": payload.get("tfs"),
        "broker": payload.get("broker"),
        "interval_ms": payload.get("interval_ms"),
        "enabled": bool(payload.get("enabled")),
        "execution_config": payload.get("execution_config"),
    }


def _normalize_execution_config(payload: dict[str, Any], tfs: list[str]) -> dict[str, Any]:
    raw = (
        payload.get("execution_config")
        or payload.get("executionConfig")
        or payload.get("filters")
        or payload.get("criteria")
        or {}
    )
    if not isinstance(raw, dict):
        raw = {}

    def _string_list(*keys: str, uppercase: bool = False) -> list[str]:
        for key in keys:
            value = raw.get(key)
            if isinstance(value, list):
                items = [str(item).strip() for item in value if str(item).strip()]
                return [item.replace("/", "").upper() if uppercase else item.lower() for item in items]
        return []

    return {
        "min_confidence": _optional_float(raw.get("min_confidence") or raw.get("minConfidence")),
        "min_rr": _optional_float(raw.get("min_rr") or raw.get("minRR")),
        "source_filters": _string_list("source_filters", "sourceFilters", "allowed_sources", "allowedSources"),
        "timeframe_filters": _string_list("timeframe_filters", "timeframeFilters", "allowed_timeframes", "allowedTimeframes"),
        "monitored_timeframes": _string_list("monitored_timeframes", "monitoredTimeframes") or [str(tf).lower() for tf in tfs],
        "allowed_symbols": _string_list("allowed_symbols", "allowedSymbols", uppercase=True),
        "max_daily_trades": _optional_float(raw.get("max_daily_trades") or raw.get("maxDailyTrades")),
        "max_open_positions": _optional_float(raw.get("max_open_positions") or raw.get("maxOpenPositions")),
        "require_lower_tf_confirmation": bool(raw.get("require_lower_tf_confirmation") or raw.get("requireLowerTfConfirmation")),
        "require_higher_tf_bias": bool(raw.get("require_higher_tf_bias") or raw.get("requireHigherTfBias")),
        "require_bollinger_direction": bool(raw.get("require_bollinger_direction") or raw.get("requireBollingerDirection")),
        "require_rsi_stoch_direction": bool(raw.get("require_rsi_stoch_direction") or raw.get("requireRsiStochDirection")),
        "paused": bool(raw.get("paused")),
        "pause_reason": raw.get("pause_reason") or raw.get("pauseReason"),
    }


def _passes_backend_execution_config(
    entry: dict[str, Any],
    batch_entries: list[dict[str, Any]],
    config: dict[str, Any],
    service,
    *,
    user_id: str,
    bot_type: str,
    broker: str,
    exec_id: str,
    symbol: str,
) -> tuple[bool, str]:
    entry_symbol = _entry_symbol(entry)
    entry_tf = _entry_tf(entry)
    entry_side = _entry_side(entry)
    if symbol and entry_symbol != symbol:
        return False, f"symbol {entry_symbol} != daemon {symbol}"
    if config.get("allowed_symbols") and entry_symbol not in set(config.get("allowed_symbols") or []):
        return False, "symbol not enabled in backend criteria"

    effective_tfs = set(config.get("timeframe_filters") or []) or set(config.get("monitored_timeframes") or [])
    if effective_tfs and entry_tf not in effective_tfs:
        return False, "timeframe not enabled in backend criteria"
    if config.get("source_filters") and _entry_source(entry) not in set(config.get("source_filters") or []):
        return False, "source not enabled in backend criteria"

    max_daily = config.get("max_daily_trades")
    if max_daily is not None and _daily_open_count(service, user_id, bot_type, broker, exec_id, entry_symbol) >= int(max_daily):
        return False, "daily trade limit reached"
    max_open = config.get("max_open_positions")
    if max_open is not None and _open_position_count(service, user_id, bot_type, broker, entry_symbol) >= int(max_open):
        return False, "open position limit reached"

    monitored = set(config.get("monitored_timeframes") or [])
    if config.get("require_lower_tf_confirmation") and not _has_tf_confirmation(entry, batch_entries, monitored, lower=True):
        return False, "missing lower timeframe confirmation"
    if config.get("require_higher_tf_bias") and not _has_tf_confirmation(entry, batch_entries, monitored, lower=False):
        return False, "missing higher timeframe bias"
    if (config.get("require_bollinger_direction") or config.get("require_rsi_stoch_direction")) and not _passes_indicator_direction(entry, config):
        return False, "indicator direction rejected"

    min_conf = config.get("min_confidence")
    if min_conf is not None and _entry_adjusted_confidence(entry) < float(min_conf):
        return False, "confidence below backend minimum"
    min_rr = config.get("min_rr")
    if min_rr is not None:
        rr = _entry_rr(entry)
        if rr is None or rr < float(min_rr):
            return False, "risk/reward below backend minimum"
    if not entry_side:
        return False, "entry side is missing"
    return True, "accepted"


def _daily_open_count(service, user_id: str, bot_type: str, broker: str, exec_id: str, symbol: str) -> int:
    today = datetime.now(timezone.utc).date()
    total = 0
    for order in service.list_orders(user_id=user_id):
        request_payload = order.get("request") if isinstance(order.get("request"), dict) else {}
        if order.get("action") != "open" or order.get("bot_type") != bot_type or order.get("broker") != broker:
            continue
        if request_payload.get("exec_id") and request_payload.get("exec_id") != exec_id:
            continue
        if str(order.get("symbol") or "").replace("/", "").upper() != symbol:
            continue
        if order.get("status") in {"failed", "session_required"}:
            continue
        created_at = int(order.get("created_at") or 0)
        if created_at and datetime.fromtimestamp(created_at / 1000, timezone.utc).date() == today:
            total += 1
    return total


def _open_position_count(service, user_id: str, bot_type: str, broker: str, symbol: str) -> int:
    total = 0
    for position in service.list_positions(user_id=user_id, status="open"):
        if position.get("bot_type") != bot_type or position.get("broker") != broker:
            continue
        if str(position.get("symbol") or "").replace("/", "").upper() == symbol:
            total += 1
    return total


def _entry_symbol(entry: dict[str, Any]) -> str:
    return str(entry.get("symbol") or "").replace("/", "").upper()


def _entry_tf(entry: dict[str, Any]) -> str:
    return str(entry.get("timeframe") or entry.get("tf") or "").strip().lower()


def _entry_side(entry: dict[str, Any]) -> str:
    raw = str(entry.get("side") or entry.get("direction") or "").strip().lower()
    if raw in {"buy", "long", "compra"}:
        return "long"
    if raw in {"sell", "short", "venta"}:
        return "short"
    return raw


def _entry_source(entry: dict[str, Any]) -> str:
    return str(entry.get("executionSourceKey") or entry.get("sourceKey") or entry.get("source") or entry.get("sourceTag") or "").strip().lower()


def _entry_adjusted_confidence(entry: dict[str, Any]) -> float:
    confidence = _optional_float(entry.get("confidence") or entry.get("confianza")) or 0.0
    wr = next(
        (
            value
            for value in (
                _optional_float(entry.get("individualWR")),
                _optional_float(entry.get("familyWR")),
                _optional_float(entry.get("indicatorWinRate")),
            )
            if value is not None
        ),
        None,
    )
    if wr is not None:
        if wr >= 65:
            confidence *= 1.15
        elif wr < 45:
            confidence *= 0.85
    return confidence


def _entry_rr(entry: dict[str, Any]) -> float | None:
    for key in ("rrNet", "rr", "riskReward", "risk_reward"):
        value = _optional_float(entry.get(key))
        if value is not None:
            return value
    entry_price = _optional_float(entry.get("entry") or entry.get("entry_price"))
    tp = _optional_float(entry.get("tp") or entry.get("take_profit"))
    sl = _optional_float(entry.get("sl") or entry.get("stop_loss"))
    if entry_price is None or tp is None or sl is None:
        return None
    risk = abs(entry_price - sl)
    return None if risk <= 0 else abs(tp - entry_price) / risk


def _has_tf_confirmation(entry: dict[str, Any], entries: list[dict[str, Any]], monitored: set[str], *, lower: bool) -> bool:
    current_rank = _tf_rank(_entry_tf(entry))
    if current_rank is None:
        return False
    for other in entries:
        other_tf = _entry_tf(other)
        if monitored and other_tf not in monitored:
            continue
        other_rank = _tf_rank(other_tf)
        if other_rank is None:
            continue
        if lower and other_rank >= current_rank:
            continue
        if not lower and other_rank <= current_rank:
            continue
        if _entry_symbol(other) == _entry_symbol(entry) and _entry_side(other) == _entry_side(entry):
            return True
    return False


def _tf_rank(tf: str) -> int | None:
    return {"1m": 1, "3m": 2, "5m": 3, "15m": 4, "30m": 5, "1h": 6, "2h": 7, "4h": 8, "1d": 9, "1w": 10}.get(str(tf).lower())


def _passes_indicator_direction(entry: dict[str, Any], config: dict[str, Any]) -> bool:
    side = _entry_side(entry)
    if not side:
        return False
    if config.get("require_bollinger_direction"):
        value = str(entry.get("bollingerSignal") or entry.get("bollinger_direction") or entry.get("bollinger") or "").lower()
        if value and not _signal_matches_side(value, side):
            return False
    if config.get("require_rsi_stoch_direction"):
        signals = [
            str(entry.get("rsiSignal") or entry.get("rsi_signal") or entry.get("rsi") or "").lower(),
            str(entry.get("stochasticSignal") or entry.get("stoch_signal") or entry.get("stochastic") or "").lower(),
        ]
        known = [value for value in signals if value]
        if known and not any(_signal_matches_side(value, side) for value in known):
            return False
    return True


def _signal_matches_side(signal: str, side: str) -> bool:
    if side == "long":
        return any(token in signal for token in ("long", "buy", "bull", "alcista", "compra"))
    if side == "short":
        return any(token in signal for token in ("short", "sell", "bear", "bajista", "venta"))
    return False


def _is_pause_worthy_broker_error(message: str) -> bool:
    lowered = str(message or "").lower()
    return any(
        token in lowered
        for token in (
            "insufficient",
            "saldo",
            "balance",
            "fondos",
            "money",
            "exposure",
            "exposición",
            "margin",
            "margen",
            "max amount",
            "maximum amount",
            "not enough",
        )
    )


def _public_daemon(state: dict[str, Any]) -> dict[str, Any]:
    payload = state.get("payload") if isinstance(state.get("payload"), dict) else state.get("last_start_payload", {})
    return {
        "id": state.get("id"),
        "running": bool(state.get("running")),
        "started_at": state.get("started_at"),
        "last_tick_at": state.get("last_tick_at"),
        "last_error": state.get("last_error"),
        "orders_submitted": state.get("orders_submitted", 0),
        "entries_seen": state.get("entries_seen", 0),
        "entries_rejected": state.get("entries_rejected", 0),
        "last_decision": state.get("last_decision"),
        "paused": bool(state.get("paused")),
        "pause_reason": state.get("pause_reason"),
        "last_start_payload": state.get("last_start_payload"),
        "execution_config": payload.get("execution_config"),
        "user_id": payload.get("user_id"),
        "bot_type": payload.get("bot_type"),
        "exec_id": payload.get("exec_id"),
        "symbol": payload.get("symbol"),
        "tfs": payload.get("tfs"),
        "broker": payload.get("broker"),
    }


def _run_bot_daemon(state: dict[str, Any], payload: dict[str, Any]) -> None:
    from markettool.interfaces.api.live_entries_routes import _get_entries_from_redis, _touch_worker, _worker_id

    redis_client = _redis_client_for_daemon()
    service = get_bot_orchestrator_service()
    since_ts = 0

    while not state.get("stop_event").is_set():
        try:
            if redis_client is not None:
                try:
                    if redis_client.get(_redis_daemon_stop_key(str(state.get("id")))):
                        break
                except Exception:
                    pass
            runtime_payload = state.get("payload") if isinstance(state.get("payload"), dict) else payload
            interval_s = max(2.0, min(float(runtime_payload.get("interval_ms") or 5000) / 1000.0, 60.0))
            user_id = str(runtime_payload.get("user_id") or "anonymous")
            bot_type = str(runtime_payload.get("bot_type") or "trading").lower()
            exec_id = str(runtime_payload.get("exec_id") or "")
            symbol = str(runtime_payload.get("symbol") or "").replace("/", "").upper()
            tfs = [str(tf).lower() for tf in (runtime_payload.get("tfs") or [])]
            broker = str(runtime_payload.get("broker") or "mt5").lower()
            execution_config = _normalize_execution_config(runtime_payload, tfs)
            state["paused"] = bool(execution_config.get("paused") or state.get("paused"))
            if state.get("paused"):
                state["last_decision"] = {
                    "status": "paused",
                    "reason": state.get("pause_reason") or execution_config.get("pause_reason") or "Backend daemon paused",
                    "at": int(time.time() * 1000),
                }
                _write_daemon_status(redis_client, state)
                state.get("stop_event").wait(interval_s)
                continue
            state["last_tick_at"] = int(time.time() * 1000)
            _touch_worker(redis_client, _worker_id(exec_id, symbol))
            _write_daemon_status(redis_client, state)
            entries = _get_entries_from_redis(redis_client, exec_id, symbol, tfs, since_ts)
            for entry in entries:
                entry_created = _entry_created_ms_safe(entry)
                since_ts = max(since_ts, entry_created)
                state["entries_seen"] = int(state.get("entries_seen") or 0) + 1
                passes, reason = _passes_backend_execution_config(
                    entry,
                    entries,
                    execution_config,
                    service,
                    user_id=user_id,
                    bot_type=bot_type,
                    broker=broker,
                    exec_id=exec_id,
                    symbol=symbol,
                )
                if not passes:
                    state["entries_rejected"] = int(state.get("entries_rejected") or 0) + 1
                    state["last_decision"] = {
                        "status": "rejected",
                        "reason": reason,
                        "entry_id": entry.get("id") or entry.get("signalKey"),
                        "symbol": _entry_symbol(entry),
                        "timeframe": _entry_tf(entry),
                        "side": _entry_side(entry),
                        "at": int(time.time() * 1000),
                    }
                    continue
                order_payload = {
                    "user_id": user_id,
                    "bot_type": bot_type,
                    "exec_id": exec_id,
                    "monitored_tfs": tfs,
                    "action": "open",
                    "broker": broker,
                    "entry": entry,
                    "execute_now": True,
                    "platform": "backend-daemon",
                    "idempotency_key": _daemon_order_key(user_id, bot_type, broker, entry),
                }
                order, created = service.create_order(order_payload)
                if not created:
                    continue
                executed = _execute_order(order, order_payload)
                if executed.status not in {"failed", "session_required"}:
                    state["orders_submitted"] = int(state.get("orders_submitted") or 0) + 1
                    state["last_decision"] = {
                        "status": "submitted",
                        "order_id": executed.id,
                        "entry_id": entry.get("id") or entry.get("signalKey"),
                        "symbol": executed.symbol,
                        "timeframe": executed.timeframe,
                        "side": executed.side,
                        "at": int(time.time() * 1000),
                    }
                elif _is_pause_worthy_broker_error(executed.message or executed.error or ""):
                    state["paused"] = True
                    state["pause_reason"] = executed.message or executed.error or "Broker rejected because balance or exposure limit was reached"
                    state["last_decision"] = {
                        "status": "paused",
                        "reason": state["pause_reason"],
                        "entry_id": entry.get("id") or entry.get("signalKey"),
                        "at": int(time.time() * 1000),
                    }
                    break
            state["last_error"] = None
        except Exception as exc:
            logger.warning("Bot daemon tick failed: %s", exc, exc_info=True)
            state["last_error"] = str(exc)
            _write_daemon_status(redis_client, state)
        runtime_payload = state.get("payload") if isinstance(state.get("payload"), dict) else payload
        interval_s = max(2.0, min(float(runtime_payload.get("interval_ms") or 5000) / 1000.0, 60.0))
        state.get("stop_event").wait(interval_s)
    state["running"] = False
    _write_daemon_status(redis_client, state)


def _daemon_order_key(user_id: str, bot_type: str, broker: str, entry: dict[str, Any]) -> str:
    symbol = str(entry.get("symbol") or "").replace("/", "").upper()
    tf = str(entry.get("timeframe") or entry.get("tf") or "").lower()
    side = str(entry.get("side") or "").lower()
    entry_id = str(entry.get("id") or entry.get("signalKey") or "")
    return f"{user_id}:{bot_type}:open:{broker}:{symbol}:{tf}:{side}:{entry_id}"


def _entry_created_ms_safe(entry: dict[str, Any]) -> int:
    for key in ("created_at", "createdAt", "timestamp"):
        value = entry.get(key)
        if isinstance(value, (int, float)):
            return int(value if value > 1e12 else value * 1000)
        if value:
            try:
                from pandas import Timestamp

                parsed = Timestamp(str(value))
                if not parsed is None:
                    return int(parsed.timestamp() * 1000)
            except Exception:
                pass
    return int(time.time() * 1000)


def _filter_status_items(
    items: list[dict[str, Any]],
    *,
    exec_id: str | None,
    symbol: str | None,
    bot_type: str | None,
) -> list[dict[str, Any]]:
    result = list(items)
    if exec_id:
        result = [item for item in result if not isinstance(item.get("request"), dict) or item.get("request", {}).get("exec_id") == exec_id]
    if symbol:
        norm_symbol = str(symbol).replace("/", "").upper()
        result = [item for item in result if str(item.get("symbol") or item.get("request", {}).get("symbol") or "").replace("/", "").upper() == norm_symbol]
    if bot_type:
        result = [item for item in result if item.get("bot_type") == bot_type or item.get("request", {}).get("bot_type") == bot_type]
    return result


def _optional_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except Exception:
        return None
