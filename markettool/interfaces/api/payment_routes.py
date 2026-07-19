"""PayPal payment routes for MarketTool transaction packs."""

from __future__ import annotations

import base64
import logging
import os
from datetime import datetime, timezone

import requests
from flask import Blueprint, jsonify, request

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
    from google.cloud import firestore  # lazy import
    return firestore.Client(project="trading-449607")


def _resolve_subscription_refs(db, user_key: str):
    """Resolve canonical subscription doc and alias docs from user_id/uuid/telegram key."""
    key = str(user_key or "").strip()
    col = db.collection("suscripciones_user")

    if not key:
        return col.document(""), []

    # 1) Direct lookup by document id.
    direct_ref = col.document(key)
    direct_snap = direct_ref.get()
    if direct_snap.exists:
        data = direct_snap.to_dict() or {}
        is_numeric_id = key.isdigit()
        candidate_uid = str(data.get("doc_alias_of") or data.get("user_id") or key)
        looks_alias = bool(data.get("doc_alias_of")) or (
            is_numeric_id and candidate_uid and candidate_uid != key
        )

        if looks_alias and candidate_uid:
            canonical_ref = col.document(candidate_uid)
            aliases = [direct_ref]
            tg = data.get("telegram_id")
            if tg is not None:
                aliases.append(col.document(str(tg)))
            return canonical_ref, aliases

        canonical_id = str(data.get("user_id") or key)
        canonical_ref = col.document(canonical_id)
        aliases = []
        tg = data.get("telegram_id")
        if tg is not None:
            aliases.append(col.document(str(tg)))
        return canonical_ref, aliases

    # 2) Lookup by user_id field.
    qs_user = list(col.where("user_id", "==", key).limit(1).stream())
    if qs_user:
        snap = qs_user[0]
        data = snap.to_dict() or {}
        canonical_id = str(data.get("user_id") or snap.id)
        canonical_ref = col.document(canonical_id)
        aliases = []
        tg = data.get("telegram_id")
        if tg is not None:
            aliases.append(col.document(str(tg)))
        return canonical_ref, aliases

    # 3) Lookup by telegram_id field.
    qs_tg = list(col.where("telegram_id", "==", key).limit(1).stream())
    if qs_tg:
        snap = qs_tg[0]
        data = snap.to_dict() or {}
        canonical_id = str(data.get("user_id") or snap.id)
        canonical_ref = col.document(canonical_id)
        return canonical_ref, [col.document(key)]

    # 4) Fallback: treat key as canonical id.
    return col.document(key), []


def _credit_transactions(user_key: str, pack_id: str, order_id: str) -> int:
    """Increment transactions on canonical doc and mirror to aliases. Returns new balance."""
    from google.cloud import firestore

    pack = PACKS[pack_id]
    ops = pack["ops"]
    db = _firestore_client()
    canonical_ref, alias_refs = _resolve_subscription_refs(db, user_key)

    @firestore.transactional
    def _tx(transaction: firestore.Transaction) -> int:
        canonical_snap = canonical_ref.get(transaction=transaction)
        canonical_data = canonical_snap.to_dict() or {}
        current = int(canonical_data.get("transacciones_restantes") or 0)
        new_balance = current + int(ops)
        canonical_user_id = str(canonical_data.get("user_id") or canonical_ref.id)
        canonical_telegram_id = canonical_data.get("telegram_id")

        transaction.set(
            canonical_ref,
            {
                "user_id": canonical_user_id,
                "transacciones_restantes": new_balance,
                "estado": "activa" if new_balance > 0 else "inactiva",
                "updated_at": firestore.SERVER_TIMESTAMP,
            },
            merge=True,
        )

        for alias_ref in alias_refs:
            if alias_ref.id == canonical_ref.id:
                continue
            alias_payload = {
                "user_id": canonical_user_id,
                "doc_alias_of": canonical_user_id,
                "transacciones_restantes": new_balance,
                "estado": "activa" if new_balance > 0 else "inactiva",
                "updated_at": firestore.SERVER_TIMESTAMP,
            }
            if canonical_telegram_id is not None:
                alias_payload["telegram_id"] = canonical_telegram_id
            transaction.set(alias_ref, alias_payload, merge=True)

        transaction.set(
            canonical_ref.collection("payment_history").document(order_id),
            {
                "order_id": order_id,
                "pack_id": pack_id,
                "ops": ops,
                "price": pack["price"],
                "plan": pack["plan"],
                "status": "COMPLETED",
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
            merge=True,
        )
        return new_balance

    return _tx(db.transaction())


# ── Blueprint ─────────────────────────────────────────────────────────────────
payment_bp = Blueprint("payments", __name__, url_prefix="/payments")


@payment_bp.route("/paypal/create-order", methods=["POST"])
def create_order():
    """Create a PayPal order for a transaction pack."""
    data = request.get_json(silent=True) or {}
    user_id = data.get("user_id")
    pack_id = data.get("pack_id")
    currency = data.get("currency", "USD")

    if not user_id or not pack_id:
        return jsonify({"error": "user_id and pack_id are required"}), 400
    if pack_id not in PACKS:
        return jsonify({"error": f"Unknown pack_id: {pack_id}"}), 400

    pack = PACKS[pack_id]
    amount = str(round(pack["price"], 2))

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
                        "description": f"MarketTool {pack_id} — {pack['ops']} transacciones",
                        "custom_id": f"{user_id}|{pack_id}",
                    }
                ],
            },
            timeout=20,
        )
        res.raise_for_status()
        order = res.json()
        logger.info("PayPal order created: %s for user %s pack %s", order["id"], user_id, pack_id)
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
    pack_id = data.get("pack_id")

    if not order_id or not user_id or not pack_id:
        return jsonify({"error": "order_id, user_id, and pack_id are required"}), 400
    if pack_id not in PACKS:
        return jsonify({"error": f"Unknown pack_id: {pack_id}"}), 400

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

        new_balance = _credit_transactions(user_id, pack_id, order_id)
        logger.info(
            "PayPal capture COMPLETED: order=%s user=%s pack=%s new_balance=%d",
            order_id, user_id, pack_id, new_balance,
        )
        return jsonify({"success": True, "new_balance": new_balance, "ops": PACKS[pack_id]["ops"]}), 200

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
    pack_id = data.get("pack_id")

    if not user_id or not pack_id:
        return jsonify({"error": "user_id and pack_id are required"}), 400
    if pack_id not in PACKS:
        return jsonify({"error": f"Unknown pack_id: {pack_id}"}), 400

    pack = PACKS[pack_id]

    # MercadoPago Chile only accepts CLP — convert USD price to CLP (approx rate)
    usd_to_clp = 950  # approximate, update periodically
    price_clp = round(pack["price"] * usd_to_clp)

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
                        "title": f"{pack_id} - {pack['ops']} transacciones",
                        "quantity": 1,
                        "unit_price": price_clp,
                        "currency_id": "CLP",
                    }
                ],
                "back_urls": {
                    "success": f"https://markettool.mtlabsx.com/planes?mp_status=approved&pack_id={pack_id}",
                    "failure": "https://markettool.mtlabsx.com/planes?mp_status=failure",
                    "pending": "https://markettool.mtlabsx.com/planes?mp_status=pending",
                },
                "auto_return": "approved",
                "external_reference": f"{user_id}|{pack_id}",
                "notification_url": "https://api.mtlabsx.com/payments/webhook/mercadopago",
            },
            timeout=20,
        )
        res.raise_for_status()
        pref = res.json()
        logger.info("MP preference created: %s for user %s pack %s", pref.get("id"), user_id, pack_id)
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
    parts = external_ref.split("|")
    if len(parts) != 2:
        logger.error("MP webhook: invalid external_reference: %s", external_ref)
        return jsonify({"status": "error", "detail": "invalid external_reference"}), 400

    user_id, pack_id = parts
    if pack_id not in PACKS:
        logger.error("MP webhook: unknown pack_id: %s", pack_id)
        return jsonify({"status": "error", "detail": f"unknown pack_id: {pack_id}"}), 400

    try:
        db = _firestore_client()
        canonical_ref, _ = _resolve_subscription_refs(db, user_id)
        hist_ref = (
            db.collection("suscripciones_user")
            .document(canonical_ref.id)
            .collection("payment_history")
            .document(payment_id)
        )
        if hist_ref.get().exists:
            logger.info("MP webhook: payment %s already credited, skipping", payment_id)
            return jsonify({"status": "already_credited"}), 200

        new_balance = _credit_transactions(user_id, pack_id, payment_id)
        hist_ref.set(
            {
                "status": "approved",
                "provider": "mercadopago",
            },
            merge=True,
        )
        logger.info(
            "MP webhook credited %d ops to user %s (payment %s). new_balance=%d canonical=%s",
            PACKS[pack_id]["ops"],
            user_id,
            payment_id,
            new_balance,
            canonical_ref.id,
        )
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
            if len(parts) == 2:
                user_id, pack_id = parts
                if pack_id in PACKS:
                    try:
                        # Only credit if not already done (idempotency check)
                        db = _firestore_client()
                        canonical_ref, _ = _resolve_subscription_refs(db, user_id)
                        hist_ref = (
                            db.collection("suscripciones_user")
                            .document(canonical_ref.id)
                            .collection("payment_history")
                            .document(order_id)
                        )
                        if not hist_ref.get().exists:
                            _credit_transactions(user_id, pack_id, order_id)
                            logger.info(
                                "Webhook credited %d ops to user %s (order %s)",
                                PACKS[pack_id]["ops"], user_id, order_id,
                            )
                        else:
                            logger.info("Webhook: order %s already credited, skipping", order_id)
                    except Exception as exc:
                        logger.exception("Webhook credit error: %s", exc)
                        return jsonify({"status": "error", "detail": str(exc)}), 500

    return jsonify({"status": "ok"}), 200


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
