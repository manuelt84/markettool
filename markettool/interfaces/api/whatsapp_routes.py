"""WhatsApp support routes backed by the external UltraMsg service.

This mirrors the non-Facebook path used by estetica-api: a linked WhatsApp
device sends messages through UltraMsg when credentials are configured.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any

import requests
from flask import Blueprint, Response, jsonify, request

logger = logging.getLogger(__name__)

ULTRAMSG_BASE = os.getenv("ULTRAMSG_BASE_URL", "https://api.ultramsg.com").rstrip("/")
ULTRAMSG_INSTANCE = os.getenv("ULTRAMSG_INSTANCE", "").strip()
ULTRAMSG_TOKEN = os.getenv("ULTRAMSG_TOKEN", "").strip()
MARKETTOOL_WHATSAPP_SUPPORT_TO = os.getenv("MARKETTOOL_WHATSAPP_SUPPORT_TO", "").strip()
MARKETTOOL_WHATSAPP_PUBLIC_NUMBER = os.getenv("MARKETTOOL_WHATSAPP_PUBLIC_NUMBER", "56959036525").strip()

whatsapp_bp = Blueprint("whatsapp", __name__, url_prefix="/whatsapp")


def _normalize_phone(value: str | None) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def _is_configured() -> bool:
    return bool(ULTRAMSG_INSTANCE and ULTRAMSG_TOKEN)


def _send_ultramsg(to: str, body: str) -> dict[str, Any]:
    if not _is_configured():
        raise RuntimeError("UltraMsg is not configured")

    phone = _normalize_phone(to)
    if not phone:
        raise ValueError("Missing destination phone")

    res = requests.post(
        f"{ULTRAMSG_BASE}/{ULTRAMSG_INSTANCE}/messages/chat",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={"token": ULTRAMSG_TOKEN, "to": phone, "body": body},
        timeout=15,
    )
    res.raise_for_status()
    data = res.json()
    sent = data.get("sent") is True or data.get("id") or data.get("message") == "ok"
    if not sent:
        raise RuntimeError(f"UltraMsg did not confirm delivery: {data}")
    return data


def _format_support_message(payload: dict[str, Any]) -> str:
    message = str(payload.get("message") or "").strip()
    user_id = str(payload.get("user_id") or payload.get("userId") or "").strip()
    source = str(payload.get("source") or "markettool-web").strip()
    page = str(payload.get("page") or "").strip()
    item_type = str(payload.get("item_type") or payload.get("itemType") or "").strip()
    item_id = str(payload.get("item_id") or payload.get("itemId") or "").strip()

    lines = [
        "Nueva solicitud desde MarketTool Web",
        f"Fecha: {datetime.now(timezone.utc).isoformat()}",
        f"Origen: {source}",
    ]
    if user_id:
        lines.append(f"Usuario: {user_id}")
    if page:
        lines.append(f"Pagina: {page}")
    if item_type or item_id:
        lines.append(f"Item: {item_type or '-'} / {item_id or '-'}")
    if message:
        lines.extend(["", message])
    return "\n".join(lines)


@whatsapp_bp.route("/config", methods=["GET"])
def whatsapp_config():
    return jsonify(
        {
            "enabled": _is_configured(),
            "provider": "ultramsg",
            "public_number": _normalize_phone(MARKETTOOL_WHATSAPP_PUBLIC_NUMBER),
            "support_target_configured": bool(_normalize_phone(MARKETTOOL_WHATSAPP_SUPPORT_TO)),
        }
    )


@whatsapp_bp.route("/support", methods=["POST"])
def whatsapp_support():
    payload = request.get_json(silent=True) or {}
    destination = _normalize_phone(payload.get("to") or MARKETTOOL_WHATSAPP_SUPPORT_TO)
    if not destination:
        return jsonify({"success": False, "error": "support destination is not configured"}), 400

    try:
        message = _format_support_message(payload)
        result = _send_ultramsg(destination, message)
        logger.info("UltraMsg support notification sent to %s", destination)
        return jsonify({"success": True, "provider": "ultramsg", "result": result}), 200
    except requests.HTTPError as exc:
        detail = exc.response.text if exc.response is not None else str(exc)
        logger.error("UltraMsg HTTP error: %s", detail)
        return jsonify({"success": False, "error": "ultramsg_http_error", "detail": detail}), 502
    except Exception as exc:
        logger.warning("UltraMsg support notification failed: %s", exc)
        return jsonify({"success": False, "error": str(exc)}), 500


def register_whatsapp_routes(app) -> None:
    allowed_origins = {
        "https://markettool.mtlabsx.com",
        "http://localhost:5173",
        "http://localhost:3000",
    }

    @whatsapp_bp.after_request
    def add_cors(response):
        origin = request.headers.get("Origin", "")
        if origin in allowed_origins:
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
            response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
            response.headers["Access-Control-Allow-Credentials"] = "true"
        return response

    @whatsapp_bp.route("/config", methods=["OPTIONS"])
    @whatsapp_bp.route("/support", methods=["OPTIONS"])
    def options_handler():
        resp = Response()
        origin = request.headers.get("Origin", "")
        if origin in allowed_origins:
            resp.headers["Access-Control-Allow-Origin"] = origin
            resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
            resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
        return resp, 204

    app.register_blueprint(whatsapp_bp)
    logger.info("✅ WhatsApp routes registered at /whatsapp/*")
