"""Backend-owned bot orchestration routes."""

from __future__ import annotations

import logging
import json
import os
import threading
import time
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

        redis_url = os.getenv("LIVE_ENTRIES_REDIS_URL") or os.getenv("REDIS_URL", "redis://redis:6379/0")
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
                current["last_start_payload"] = _public_daemon_payload(payload)
                current["payload"] = {**payload, "user_id": user_id, "bot_type": bot_type, "exec_id": exec_id, "symbol": symbol, "tfs": tfs, "broker": broker}
                _write_daemon_status(redis_client, current)
                return jsonify({"status": "already_running", "daemon_id": daemon_id, "daemon": _public_daemon(current)}), 200
            remote = _read_daemon_status(redis_client, daemon_id)
            if remote and remote.get("running") and int(time.time() * 1000) - int(remote.get("last_tick_at") or remote.get("started_at") or 0) < 180_000:
                return jsonify({"status": "already_running", "daemon_id": daemon_id, "daemon": remote}), 200

            stop_event = threading.Event()
            state: dict[str, Any] = {
                "id": daemon_id,
                "running": True,
                "stop_event": stop_event,
                "started_at": int(time.time() * 1000),
                "last_tick_at": None,
                "last_error": None,
                "orders_submitted": 0,
                "last_start_payload": _public_daemon_payload(payload),
            }
            state["payload"] = {**payload, "user_id": user_id, "bot_type": bot_type, "exec_id": exec_id, "symbol": symbol, "tfs": tfs, "broker": broker}
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
    }


def _public_daemon(state: dict[str, Any]) -> dict[str, Any]:
    payload = state.get("payload") if isinstance(state.get("payload"), dict) else state.get("last_start_payload", {})
    return {
        "id": state.get("id"),
        "running": bool(state.get("running")),
        "started_at": state.get("started_at"),
        "last_tick_at": state.get("last_tick_at"),
        "last_error": state.get("last_error"),
        "orders_submitted": state.get("orders_submitted", 0),
        "last_start_payload": state.get("last_start_payload"),
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
            state["last_tick_at"] = int(time.time() * 1000)
            _touch_worker(redis_client, _worker_id(exec_id, symbol))
            _write_daemon_status(redis_client, state)
            entries = _get_entries_from_redis(redis_client, exec_id, symbol, tfs, since_ts)
            for entry in entries:
                entry_created = _entry_created_ms_safe(entry)
                since_ts = max(since_ts, entry_created)
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
    return f"daemon:{user_id}:{bot_type}:{broker}:{symbol}:{tf}:{side}:{entry_id}"


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
