"""PayPal payment routes for MarketTool transaction packs."""

from __future__ import annotations

import base64
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any

import requests
from flask import Blueprint, jsonify, request

from markettool.infra.storage.vps_json_store import PostgresDocumentStore, vps_mode_enabled

logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
PAYPAL_CLIENT_ID = os.getenv(
    "PAYPAL_CLIENT_ID",
    "AcjCqompffbJCKuvd84PpDz0R8IHwJ_fQpC30kt3eEJI6RA0p97lRoUXzrrNhOGz7V6RDsvyit7bib5m",
)
PAYPAL_SECRET = os.getenv(
    "PAYPAL_SECRET",
    "EF0d9leMO_SubTg8UkqEx-GOD5oVmS0F2haAsnJ16SkveOUEuN_cIdPPz7helCaseARdt-NdAx9FxbLv",
)
PAYPAL_MODE = os.getenv("PAYPAL_MODE", "sandbox")
PAYPAL_BASE = (
    "https://api-m.sandbox.paypal.com"
    if PAYPAL_MODE == "sandbox"
    else "https://api-m.paypal.com"
)

MP_ACCESS_TOKEN = os.getenv(
    "MP_ACCESS_TOKEN",
    "APP_USR-879512907011100-040411-9c2ee0b1eea3719c52d0c5f4bcf57c66-3313855008",
)
MP_PUBLIC_KEY = os.getenv(
    "MP_PUBLIC_KEY",
    "APP_USR-42ac0944-8a88-4823-aa7b-18f471592003",
)
MP_API_BASE = "https://api.mercadopago.com"
ADMIN_BILLING_MODE = os.getenv("ADMIN_BILLING_MODE", "no_charge").strip().lower()
PAYMENT_WEB_BASE_URL = os.getenv("PAYMENT_WEB_BASE_URL", "https://markettool.mtlabsx.com").rstrip("/")

PACKS: dict[str, dict] = {
    "pack_basic_200":    {"ops": 200,  "price": 6.47,   "plan": "premium-mensual"},
    "pack_basic_400":    {"ops": 400,  "price": 11.85,  "plan": "premium-mensual"},
    "pack_basic_800":    {"ops": 800,  "price": 21.55,  "plan": "premium-mensual"},
    "pack_premium_600":  {"ops": 600,  "price": 15.62,  "plan": "premium-semestral"},
    "pack_premium_1200": {"ops": 1200, "price": 30.71,  "plan": "premium-semestral"},
    "pack_premium_2600": {"ops": 2600, "price": 77.00,  "plan": "premium-semestral"},
    "pack_advanced_2000": {"ops": 2000, "price": 53.88, "plan": "premium-anual"},
    "pack_advanced_4000": {"ops": 4000, "price": 102.37,"plan": "premium-anual"},
    "pack_advanced_8000": {"ops": 8000, "price": 193.96,"plan": "premium-anual"},
}

PLANS: dict[str, dict[str, Any]] = {
    "premium-mensual": {
        "price": 19.99,
        "transactions": 600,
        "duration_days": 30,
        "productId": "suscripcion_markettool_2",
        "title": "Premium Mensual",
    },
    "premium-semestral": {
        "price": 99.99,
        "transactions": 1200,
        "duration_days": 180,
        "productId": "suscripcion_markettool_2",
        "title": "Premium Semestral",
    },
    "premium-anual": {
        "price": 179.99,
        "transactions": 2600,
        "duration_days": 365,
        "productId": "suscripcion_markettool_2",
        "title": "Premium Anual",
    },
}

# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_access_token() -> str:
    auth = base64.b64encode(f"{PAYPAL_CLIENT_ID}:{PAYPAL_SECRET}".encode()).decode()
    res = requests.post(
        f"{PAYPAL_BASE}/v1/oauth2/token",
        headers={"Authorization": f"Basic {auth}"},
        data={"grant_type": "client_credentials"},
        timeout=15,
    )
    res.raise_for_status()
    return res.json()["access_token"]


def _firestore_client():
    if vps_mode_enabled():
        store = PostgresDocumentStore.from_env()
        if store is None:
            raise RuntimeError("MARKETTOOL_POSTGRES_DSN or MARKETTOOL_POSTGRES_DSN_FILE is required in VPS mode")
        return store
    from google.cloud import firestore  # lazy import
    return firestore.Client(project="trading-449607")


class Increment:
    def __init__(self, value: int):
        self._value = value


def _increment(value: int) -> Any:
    if vps_mode_enabled():
        return Increment(value)
    from google.cloud import firestore
    return firestore.Increment(value)


def _server_timestamp() -> Any:
    if vps_mode_enabled():
        return datetime.now(timezone.utc).isoformat()
    from google.cloud import firestore
    return firestore.SERVER_TIMESTAMP


def _parse_item(data: dict[str, Any]) -> tuple[str, str]:
    item_type = str(data.get("item_type") or "").strip().lower()
    item_id = str(data.get("item_id") or "").strip()
    pack_id = str(data.get("pack_id") or "").strip()
    plan_id = str(data.get("plan_id") or "").strip()

    if not item_type:
        item_type = "plan" if plan_id else "pack"
    if not item_id:
        item_id = plan_id if item_type == "plan" else pack_id
    return item_type, item_id


def _external_reference(user_id: str, item_type: str, item_id: str) -> str:
    if item_type == "pack":
        return f"{user_id}|{item_id}"
    return f"{user_id}|{item_type}|{item_id}"


def _parse_external_reference(external_ref: str) -> tuple[str, str, str] | None:
    parts = [p for p in str(external_ref or "").split("|") if p]
    if len(parts) == 2:
        user_id, pack_id = parts
        return user_id, "pack", pack_id
    if len(parts) == 3:
        user_id, item_type, item_id = parts
        return user_id, item_type, item_id
    return None


def _item_definition(item_type: str, item_id: str) -> dict[str, Any] | None:
    if item_type == "pack":
        return PACKS.get(item_id)
    if item_type == "plan":
        return PLANS.get(item_id)
    return None


def _is_admin_user(user_id: str) -> bool:
    if not user_id:
        return False
    try:
        db = _firestore_client()
        candidate_ids = {str(user_id)}
        for collection_name in ("suscripciones_user", "user_ids", "chat_ids"):
            try:
                snap = db.collection(collection_name).document(str(user_id)).get()
                if snap.exists:
                    data = snap.to_dict() or {}
                    for linked in (data.get("user_id"), data.get("telegram_id"), data.get("doc_alias_of"), data.get("chat_id")):
                        if linked:
                            candidate_ids.add(str(linked))
            except Exception:
                pass

        for linked_key in list(candidate_ids):
            for collection_name in ("suscripciones_user", "user_ids", "chat_ids"):
                try:
                    snap = db.collection(collection_name).document(str(linked_key)).get()
                    if snap.exists:
                        data = snap.to_dict() or {}
                        for linked in (data.get("user_id"), data.get("telegram_id"), data.get("doc_alias_of"), data.get("chat_id")):
                            if linked:
                                candidate_ids.add(str(linked))
                except Exception:
                    pass

        for snap in db.collection("admin_ids").stream():
            data = snap.to_dict() or {}
            admin_id = data.get("chat_id") or snap.id
            if str(admin_id) in candidate_ids:
                return True
        return False
    except Exception as exc:
        logger.warning("Admin ids lookup failed for %s: %s", user_id, exc)
        return False


def _credit_transactions(user_id: str, pack_id: str, order_id: str, *, provider: str = "paypal") -> int:
    """Increment transacciones_restantes and log payment_history. Returns new balance."""
    pack = PACKS[pack_id]
    ops = pack["ops"]
    db = _firestore_client()
    doc_ref = db.collection("suscripciones_user").document(user_id)

    doc_ref.update(
        {
            "transacciones_restantes": _increment(ops),
            "updated_at": _server_timestamp(),
        }
    )

    # Log in payment_history subcollection
    db.collection("suscripciones_user").document(user_id).collection(
        "payment_history"
    ).document(order_id).set(
        {
            "order_id": order_id,
            "pack_id": pack_id,
            "ops": ops,
            "price": pack["price"],
            "plan": pack["plan"],
            "status": "COMPLETED",
            "provider": provider,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
    )

    snap = doc_ref.get()
    return int(snap.to_dict().get("transacciones_restantes", ops))


def _activate_plan(user_id: str, plan_id: str, order_id: str, *, provider: str = "paypal") -> dict[str, Any]:
    plan = PLANS[plan_id]
    now = datetime.now(timezone.utc)
    end = now + timedelta(days=int(plan["duration_days"]))
    db = _firestore_client()
    doc_ref = db.collection("suscripciones_user").document(user_id)

    payload = {
        "user_id": user_id,
        "estado": "activa",
        "tipo": plan_id,
        "basePlanId": plan_id,
        "productId": plan["productId"],
        "inicio": now.isoformat(),
        "fin": end.isoformat(),
        "limite_transacciones": int(plan["transactions"]),
        "transacciones_restantes": int(plan["transactions"]),
        "updated_at": _server_timestamp(),
    }
    doc_ref.set(payload, merge=True)
    doc_ref.collection("payment_history").document(order_id).set(
        {
            "order_id": order_id,
            "item_type": "plan",
            "plan_id": plan_id,
            "ops": int(plan["transactions"]),
            "price": float(plan["price"]),
            "status": "COMPLETED",
            "provider": provider,
            "created_at": now.isoformat(),
        },
        merge=True,
    )
    return {"plan_id": plan_id, "ops": int(plan["transactions"]), "fin": end.isoformat()}


def _grant_item(user_id: str, item_type: str, item_id: str, order_id: str, *, provider: str) -> dict[str, Any]:
    if item_type == "pack":
        new_balance = _credit_transactions(user_id, item_id, order_id, provider=provider)
        return {"success": True, "item_type": "pack", "item_id": item_id, "new_balance": new_balance, "ops": PACKS[item_id]["ops"]}
    if item_type == "plan":
        result = _activate_plan(user_id, item_id, order_id, provider=provider)
        return {"success": True, "item_type": "plan", "item_id": item_id, **result}
    raise ValueError(f"Unknown item_type: {item_type}")


# ── Blueprint ─────────────────────────────────────────────────────────────────
payment_bp = Blueprint("payments", __name__, url_prefix="/payments")


@payment_bp.route("/config", methods=["GET"])
def payment_config():
    """Return public payment config. Never include server secrets."""
    return jsonify(
        {
            "paypal_client_id": PAYPAL_CLIENT_ID,
            "paypal_mode": PAYPAL_MODE,
            "mercadopago_public_key": MP_PUBLIC_KEY,
            "mercadopago_mode": "test"
            if ("test" in MP_ACCESS_TOKEN.lower() or "sandbox" in MP_ACCESS_TOKEN.lower())
            else "live",
            "admin_billing_mode": ADMIN_BILLING_MODE,
            "plans": {k: {kk: vv for kk, vv in v.items() if kk != "productId"} for k, v in PLANS.items()},
            "packs": PACKS,
        }
    )


@payment_bp.route("/paypal/create-order", methods=["POST"])
def create_order():
    """Create a PayPal order for a transaction pack."""
    data = request.get_json(silent=True) or {}
    user_id = data.get("user_id")
    item_type, item_id = _parse_item(data)
    currency = data.get("currency", "USD")

    if not user_id or not item_id:
        return jsonify({"error": "user_id and item_id/pack_id/plan_id are required"}), 400
    item = _item_definition(item_type, item_id)
    if not item:
        return jsonify({"error": f"Unknown payment item: {item_type}/{item_id}"}), 400

    amount = str(round(float(item["price"]), 2))

    try:
        token = _get_access_token()
        res = requests.post(
            f"{PAYPAL_BASE}/v2/checkout/orders",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json={
                "intent": "CAPTURE",
                "purchase_units": [
                    {
                        "amount": {"currency_code": currency, "value": amount},
                        "description": f"MarketTool {item_id}",
                        "custom_id": _external_reference(user_id, item_type, item_id),
                    }
                ],
            },
            timeout=20,
        )
        res.raise_for_status()
        order = res.json()
        logger.info("PayPal order created: %s for user %s item %s/%s", order["id"], user_id, item_type, item_id)
        return jsonify({"order_id": order["id"]}), 200
    except requests.HTTPError as exc:
        logger.error("PayPal create-order HTTP error: %s — %s", exc, exc.response.text if exc.response else "")
        return jsonify({"error": "PayPal error", "detail": str(exc)}), 502
    except Exception as exc:
        logger.exception("PayPal create-order unexpected error: %s", exc)
        return jsonify({"error": str(exc)}), 500


@payment_bp.route("/paypal/capture-order", methods=["POST"])
def capture_order():
    """Capture a PayPal order and credit transactions to Firestore."""
    data = request.get_json(silent=True) or {}
    order_id = data.get("order_id")
    user_id = data.get("user_id")
    item_type, item_id = _parse_item(data)

    if not order_id or not user_id or not item_id:
        return jsonify({"error": "order_id, user_id, and item_id/pack_id/plan_id are required"}), 400
    if not _item_definition(item_type, item_id):
        return jsonify({"error": f"Unknown payment item: {item_type}/{item_id}"}), 400

    try:
        token = _get_access_token()
        res = requests.post(
            f"{PAYPAL_BASE}/v2/checkout/orders/{order_id}/capture",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            timeout=20,
        )
        res.raise_for_status()
        capture = res.json()
        status = capture.get("status")

        if status != "COMPLETED":
            return jsonify({"error": f"Unexpected order status: {status}"}), 400

        result = _grant_item(user_id, item_type, item_id, order_id, provider="paypal")
        logger.info(
            "PayPal capture COMPLETED: order=%s user=%s item=%s/%s",
            order_id, user_id, item_type, item_id,
        )
        return jsonify(result), 200

    except requests.HTTPError as exc:
        logger.error("PayPal capture HTTP error: %s — %s", exc, exc.response.text if exc.response else "")
        return jsonify({"error": "PayPal capture error", "detail": str(exc)}), 502
    except Exception as exc:
        logger.exception("PayPal capture unexpected error: %s", exc)
        return jsonify({"error": str(exc)}), 500


@payment_bp.route("/mercadopago/create-preference", methods=["POST"])
def mp_create_preference():
    """Create a MercadoPago Checkout Pro preference."""
    data = request.get_json(silent=True) or {}
    user_id = data.get("user_id")
    item_type, item_id = _parse_item(data)

    if not user_id or not item_id:
        return jsonify({"error": "user_id and item_id/pack_id/plan_id are required"}), 400
    item = _item_definition(item_type, item_id)
    if not item:
        return jsonify({"error": f"Unknown payment item: {item_type}/{item_id}"}), 400

    item_title = item.get("title") or item_id
    ops = int(item.get("ops") or item.get("transactions") or 0)

    # MercadoPago Chile only accepts CLP — convert USD price to CLP (approx rate)
    usd_to_clp = 950  # approximate, update periodically
    price_clp = round(float(item["price"]) * usd_to_clp)

    try:
        res = requests.post(
            f"{MP_API_BASE}/checkout/preferences",
            headers={
                "Authorization": f"Bearer {MP_ACCESS_TOKEN}",
                "Content-Type": "application/json",
            },
            json={
                "items": [
                    {
                        "title": f"{item_title} - {ops} transacciones",
                        "quantity": 1,
                        "unit_price": price_clp,
                        "currency_id": "CLP",
                    }
                ],
                "back_urls": {
                    "success": f"{PAYMENT_WEB_BASE_URL}/planes?mp_status=approved&item_type={item_type}&item_id={item_id}",
                    "failure": f"{PAYMENT_WEB_BASE_URL}/planes?mp_status=failure",
                    "pending": f"{PAYMENT_WEB_BASE_URL}/planes?mp_status=pending",
                },
                "auto_return": "approved",
                "external_reference": _external_reference(user_id, item_type, item_id),
                "notification_url": "https://api.mtlabsx.com/payments/webhook/mercadopago",
            },
            timeout=20,
        )
        res.raise_for_status()
        pref = res.json()
        logger.info("MP preference created: %s for user %s item %s/%s", pref.get("id"), user_id, item_type, item_id)
        # Use sandbox_init_point for test credentials, init_point for production
        is_test = "test" in MP_ACCESS_TOKEN.lower() or "sandbox" in MP_ACCESS_TOKEN.lower()
        checkout_url = pref.get("sandbox_init_point", pref["init_point"]) if is_test else pref["init_point"]
        return jsonify({"init_point": checkout_url}), 200
    except requests.HTTPError as exc:
        logger.error("MP create-preference HTTP error: %s — %s", exc, exc.response.text if exc.response else "")
        return jsonify({"error": "MercadoPago error", "detail": str(exc)}), 502
    except Exception as exc:
        logger.exception("MP create-preference unexpected error: %s", exc)
        return jsonify({"error": str(exc)}), 500


@payment_bp.route("/webhook/mercadopago", methods=["POST"])
def mp_webhook():
    """Receive IPN notifications from MercadoPago."""
    payload = request.get_json(silent=True) or {}
    event_type = payload.get("type", "")
    logger.info("MercadoPago webhook received: type=%s", event_type)

    if event_type != "payment":
        return jsonify({"status": "ignored"}), 200

    payment_id = str(payload.get("data", {}).get("id", ""))
    if not payment_id:
        return jsonify({"status": "no payment id"}), 200

    try:
        res = requests.get(
            f"{MP_API_BASE}/v1/payments/{payment_id}",
            headers={"Authorization": f"Bearer {MP_ACCESS_TOKEN}"},
            timeout=15,
        )
        res.raise_for_status()
        payment = res.json()
    except Exception as exc:
        logger.exception("MP webhook: error fetching payment %s: %s", payment_id, exc)
        return jsonify({"status": "error", "detail": str(exc)}), 500

    if payment.get("status") != "approved":
        logger.info("MP webhook: payment %s not approved (status=%s)", payment_id, payment.get("status"))
        return jsonify({"status": "not approved"}), 200

    external_ref = payment.get("external_reference", "")
    parsed = _parse_external_reference(external_ref)
    if not parsed:
        logger.error("MP webhook: invalid external_reference: %s", external_ref)
        return jsonify({"status": "error", "detail": "invalid external_reference"}), 400

    user_id, item_type, item_id = parsed
    if not _item_definition(item_type, item_id):
        logger.error("MP webhook: unknown item: %s/%s", item_type, item_id)
        return jsonify({"status": "error", "detail": f"unknown item: {item_type}/{item_id}"}), 400

    try:
        db = _firestore_client()
        hist_ref = (
            db.collection("suscripciones_user")
            .document(user_id)
            .collection("payment_history")
            .document(payment_id)
        )
        if hist_ref.get().exists:
            logger.info("MP webhook: payment %s already credited, skipping", payment_id)
            return jsonify({"status": "already_credited"}), 200

        _grant_item(user_id, item_type, item_id, payment_id, provider="mercadopago")
        logger.info("MP webhook credited item %s/%s to user %s (payment %s)", item_type, item_id, user_id, payment_id)
        return jsonify({"status": "ok"}), 200
    except Exception as exc:
        logger.exception("MP webhook credit error: %s", exc)
        return jsonify({"status": "error", "detail": str(exc)}), 500


@payment_bp.route("/webhook/paypal", methods=["POST"])
def paypal_webhook():
    """Receive PayPal webhook events (fallback credits)."""
    payload = request.get_json(silent=True) or {}
    event_type = payload.get("event_type", "")

    logger.info("PayPal webhook received: %s", event_type)

    if event_type == "CHECKOUT.ORDER.APPROVED":
        resource = payload.get("resource", {})
        order_id = resource.get("id")
        purchase_units = resource.get("purchase_units", [])
        if purchase_units:
            custom_id = purchase_units[0].get("custom_id", "")
            parts = custom_id.split("|")
            parsed = _parse_external_reference(custom_id)
            if parsed:
                user_id, item_type, item_id = parsed
                if _item_definition(item_type, item_id):
                    try:
                        # Only credit if not already done (idempotency check)
                        db = _firestore_client()
                        hist_ref = (
                            db.collection("suscripciones_user")
                            .document(user_id)
                            .collection("payment_history")
                            .document(order_id)
                        )
                        if not hist_ref.get().exists:
                            _grant_item(user_id, item_type, item_id, order_id, provider="paypal_webhook")
                            logger.info(
                                "Webhook credited %s/%s to user %s (order %s)",
                                item_type, item_id, user_id, order_id,
                            )
                        else:
                            logger.info("Webhook: order %s already credited, skipping", order_id)
                    except Exception as exc:
                        logger.exception("Webhook credit error: %s", exc)
                        return jsonify({"status": "error", "detail": str(exc)}), 500

    return jsonify({"status": "ok"}), 200


@payment_bp.route("/admin/grant", methods=["POST"])
def admin_grant():
    """Grant a pack or plan without external payment for Firestore admin users."""
    data = request.get_json(silent=True) or {}
    user_id = str(data.get("user_id") or "").strip()
    item_type, item_id = _parse_item(data)

    if not user_id or not item_id:
        return jsonify({"error": "user_id and item_id/pack_id/plan_id are required"}), 400
    if ADMIN_BILLING_MODE != "no_charge":
        return jsonify({"error": "admin no-charge billing is disabled"}), 403
    if not _is_admin_user(user_id):
        return jsonify({"error": "admin role required"}), 403
    if not _item_definition(item_type, item_id):
        return jsonify({"error": f"Unknown payment item: {item_type}/{item_id}"}), 400

    order_id = f"admin_bypass_{item_type}_{item_id}_{int(datetime.now(timezone.utc).timestamp())}"
    try:
        result = _grant_item(user_id, item_type, item_id, order_id, provider="admin_bypass")
        return jsonify({**result, "admin_bypass": True, "order_id": order_id}), 200
    except Exception as exc:
        logger.exception("Admin grant failed: %s", exc)
        return jsonify({"error": str(exc)}), 500


def register_payment_routes(app) -> None:
    """Register payment blueprint with CORS headers."""
    from flask import after_this_request

    ALLOWED_ORIGINS = {
        "https://markettool.mtlabsx.com",
        "http://localhost:5173",
        "http://localhost:3000",
    }

    @payment_bp.after_request
    def add_cors(response):
        origin = request.headers.get("Origin", "")
        if origin in ALLOWED_ORIGINS:
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
            response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
            response.headers["Access-Control-Allow-Credentials"] = "true"
        return response

    @payment_bp.route("/paypal/create-order", methods=["OPTIONS"])
    @payment_bp.route("/paypal/capture-order", methods=["OPTIONS"])
    @payment_bp.route("/webhook/paypal", methods=["OPTIONS"])
    @payment_bp.route("/mercadopago/create-preference", methods=["OPTIONS"])
    @payment_bp.route("/webhook/mercadopago", methods=["OPTIONS"])
    @payment_bp.route("/admin/grant", methods=["OPTIONS"])
    @payment_bp.route("/config", methods=["OPTIONS"])
    def options_handler():
        from flask import Response
        resp = Response()
        origin = request.headers.get("Origin", "")
        if origin in ALLOWED_ORIGINS:
            resp.headers["Access-Control-Allow-Origin"] = origin
            resp.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
            resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
        return resp, 204

    app.register_blueprint(payment_bp)
    logger.info("✅ Payment routes registered at /payments/*")
