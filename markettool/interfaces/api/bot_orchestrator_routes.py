"""Backend-owned bot orchestration routes."""

from __future__ import annotations

import logging
from typing import Any

import requests
from flask import jsonify, request

from markettool.application.services.bot_orchestrator_service import (
    BotOrder,
    get_bot_orchestrator_service,
)
from markettool.application.services.broker_session_store import get_broker_session_store

logger = logging.getLogger(__name__)


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


def _optional_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except Exception:
        return None
