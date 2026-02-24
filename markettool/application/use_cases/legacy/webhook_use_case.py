"""Legacy webhook use case."""

from __future__ import annotations

from typing import Tuple


class LegacyWebhookUseCase:
    def __init__(self, services):
        self._services = services

    async def handle(self, payload: dict | None) -> Tuple[dict, int]:
        try:
            application = self._services.application
            update_cls = self._services.update_cls
            logger = self._services.logger

            logger.info("Payload recibido: %s", payload)
            update = update_cls.de_json(payload, application.bot)
            await application.process_update(update)
            return {"status": "ok"}, 200
        except Exception as exc:
            self._services.logger.info("Error procesando webhook: %s", exc)
            return {"status": "error", "message": str(exc)}, 500
