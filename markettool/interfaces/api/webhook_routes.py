"""Webhook and error routes."""

from __future__ import annotations

from flask import jsonify, request


def register_webhook_routes(app, *, application, update_cls, logger) -> None:
    @app.errorhandler(404)
    def _not_found(_exc):
        return jsonify({"status": "error", "message": "not found"}), 404

    @app.errorhandler(500)
    def _server_err(_exc):
        return jsonify({"status": "error", "message": "internal error"}), 500

    @app.route("/webhook", methods=["POST"])
    async def webhook():
        try:
            payload = request.get_json()
            logger.info("Payload recibido: %s", payload)
            update = update_cls.de_json(payload, application.bot)
            await application.process_update(update)
            return jsonify({"status": "ok"})
        except Exception as exc:
            logger.info("Error procesando webhook: %s", exc)
            return jsonify({"status": "error", "message": str(exc)}), 500
