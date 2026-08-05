"""Telegram login/link routes for linking Telegram accounts with Google IDs."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from flask import Blueprint, jsonify, request

if TYPE_CHECKING:
    from google.cloud import firestore

telegram_bp = Blueprint("telegram", __name__, prefix="/api/telegram")
logger = logging.getLogger(__name__)


def _get_firestore_client():
    """Get Firestore client instance."""
    from google.cloud import firestore
    return firestore.Client()


@telegram_bp.route("/link", methods=["POST"])
def link_telegram():
    """
    Link a Telegram account with a Google ID (Firebase UID).
    
    Expected payload:
    {
        "google_id": "Q6ffvylrxQN6ONPm8pyhzuW8YYk2",  # Firebase UID / google_id
        "telegram_id": "1407024046",                  # Telegram user ID
        "telegram_data": {                            # Optional: full Telegram auth data
            "id": "1407024046",
            "first_name": "Manuel",
            "last_name": "Toro",
            "username": "ManuToroM",
            "photo_url": "...",
            "auth_date": "1234567890",
            "hash": "..."
        }
    }
    
    Stores the association in Firestore:
    - user_ids/{google_id}: adds telegram_id field
    - suscripciones_user/{telegram_id}: ensures document exists
    """
    try:
        data = request.get_json(force=True)
        
        google_id = data.get("google_id")
        telegram_id = data.get("telegram_id")
        telegram_data = data.get("telegram_data", {})
        
        if not google_id or not telegram_id:
            return jsonify({
                "status": "error",
                "message": "google_id and telegram_id are required"
            }), 400
        
        db = _get_firestore_client()
        
        # Update user_ids/{google_id} with telegram_id
        user_ref = db.collection("user_ids").document(google_id)
        user_doc = user_ref.get()
        
        if user_doc.exists:
            # Update existing document
            user_ref.update({
                "telegram_id": str(telegram_id),
                "telegram_username": telegram_data.get("username"),
                "telegram_first_name": telegram_data.get("first_name"),
                "telegram_last_name": telegram_data.get("last_name"),
                "telegram_linked_at": datetime.now(timezone.utc).isoformat()
            })
            logger.info(f"✅ Updated user_ids/{google_id} with telegram_id={telegram_id}")
        else:
            # Create new document
            user_ref.set({
                "google_id": google_id,
                "telegram_id": str(telegram_id),
                "telegram_username": telegram_data.get("username"),
                "telegram_first_name": telegram_data.get("first_name"),
                "telegram_last_name": telegram_data.get("last_name"),
                "created_at": datetime.now(timezone.utc).isoformat(),
                "telegram_linked_at": datetime.now(timezone.utc).isoformat()
            })
            logger.info(f"✅ Created user_ids/{google_id} with telegram_id={telegram_id}")
        
        # Ensure suscripciones_user/{telegram_id} exists
        sub_ref = db.collection("suscripciones_user").document(str(telegram_id))
        sub_doc = sub_ref.get()
        
        if not sub_doc.exists:
            sub_ref.set({
                "telegram_id": str(telegram_id),
                "google_id": google_id,
                "telegram_username": telegram_data.get("username"),
                "created_at": datetime.now(timezone.utc).isoformat()
            })
            logger.info(f"✅ Created suscripciones_user/{telegram_id}")
        elif not sub_doc.to_dict().get("google_id"):
            # Update existing subscription with google_id
            sub_ref.update({
                "google_id": google_id
            })
            logger.info(f"✅ Updated suscripciones_user/{telegram_id} with google_id={google_id}")
        
        return jsonify({
            "status": "success",
            "message": "Telegram account linked successfully",
            "google_id": google_id,
            "telegram_id": str(telegram_id)
        }), 200
        
    except Exception as e:
        logger.error(f"Error linking Telegram: {e}", exc_info=True)
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500


def register_telegram_routes(app):
    """Register Telegram routes."""
    app.register_blueprint(telegram_bp)
    logger.info("✅ Telegram routes registered")
