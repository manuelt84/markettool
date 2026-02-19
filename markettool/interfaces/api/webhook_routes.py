"""Webhook and error routes."""

from __future__ import annotations

from flask import jsonify, request

from markettool.application.use_cases.legacy import LegacyWebhookUseCase


def register_webhook_routes(app, *, services) -> None:
    use_case = LegacyWebhookUseCase(services)
    logger = services.logger
    @app.errorhandler(404)
    def _not_found(_exc):
        return jsonify({"status": "error", "message": "not found"}), 404

    @app.errorhandler(500)
    def _server_err(_exc):
        return jsonify({"status": "error", "message": "internal error"}), 500

    @app.route("/webhook", methods=["POST"])
    async def webhook():
        payload, status = await use_case.handle(request.get_json())
        return jsonify(payload), status
