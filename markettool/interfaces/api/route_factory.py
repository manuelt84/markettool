"""Route factory for registering all API routes."""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

from flask import abort, send_file

from markettool.interfaces.api.historicos_routes import register_historicos_routes
from markettool.interfaces.api.quotes_routes import register_quotes_routes
from markettool.interfaces.api.analysis_routes import register_analysis_routes
from markettool.interfaces.api.cache_management_routes import register_cache_routes
from markettool.interfaces.api.analisis_routes import register_analisis_routes
from markettool.interfaces.api.webhook_routes import register_webhook_routes
from markettool.interfaces.api.monitoreo_routes import register_monitoreo_routes
from markettool.interfaces.api.cache_routes import register_cache_routes as register_cache_routes_legacy
from markettool.interfaces.api.hexagonal_analysis_routes import register_hexagonal_analysis_routes
from markettool.interfaces.api.risk_management_routes import register_risk_management_routes
from markettool.interfaces.api.signal_validation_routes import register_signal_validation_routes
from markettool.interfaces.api.mt5_routes import register_mt5_routes
from markettool.interfaces.api.bot_inject_routes import register_bot_inject_routes
from markettool.interfaces.api.ponderacion_routes import register_ponderacion_routes
from markettool.interfaces.api.ponderacion_history import PonderacionHistory
from markettool.interfaces.api.ponderacion_alerts import PonderacionAlert
from markettool.interfaces.api.payment_routes import register_payment_routes
from markettool.interfaces.api.whatsapp_routes import register_whatsapp_routes
from markettool.interfaces.api.live_entries_routes import register_live_entries_routes
from markettool.interfaces.api.fmp_ledger_routes import register_fmp_ledger_routes
from markettool.infra.storage.vps_json_store import VpsJsonStore, vps_mode_enabled
# backtest_routes removed — backtest is now 100% client-side

if TYPE_CHECKING:
    from flask import Flask
    from markettool.interfaces.containers import DIContainer


class _PonderacionCacheAdapter:
    """Minimal ponderacion cache adapter for API routes in hexagonal runtime."""

    def __init__(self, logger: logging.Logger):
        self.logger = logger
        self.redis_client = None
        self._hits = 0
        self._misses = 0

        redis_url = os.getenv("REDIS_URL")
        if not redis_url:
            return

        try:
            import redis as redis_lib

            client = redis_lib.Redis.from_url(redis_url, decode_responses=True)
            client.ping()
            self.redis_client = client
        except Exception as exc:
            self.logger.warning("Ponderacion cache adapter Redis unavailable: %s", exc)

    def _make_key(self, symbol: str, timeframe: str, version: str = "v1") -> str:
        return f"ponderacion:{symbol}:{timeframe}:{version}"

    def invalidate(self, symbol: str, timeframe: str, version: str = "v1") -> bool:
        if self.redis_client is None:
            return True
        try:
            self.redis_client.delete(self._make_key(symbol, timeframe, version))
            return True
        except Exception as exc:
            self.logger.warning("Ponderacion cache invalidate failed: %s", exc)
            return False

    def stats(self) -> dict:
        total = self._hits + self._misses
        hit_rate = (self._hits / total * 100) if total > 0 else 0.0
        return {
            "hits": self._hits,
            "misses": self._misses,
            "total": total,
            "hit_rate_pct": round(hit_rate, 2),
            "redis_connected": self.redis_client is not None,
            "local_cache_size": 0,
        }


def register_all_routes(
    app: Flask,
    container: DIContainer,
    logger: logging.Logger,
) -> None:
    """
    Register all API routes.
    
    Args:
        app: Flask application
        container: Dependency container
        logger: Logger instance
    """
    logger.info("Registering API routes...")
    
    # Domain-based routes
    register_historicos_routes(app, container, logger)
    register_quotes_routes(app, container, logger)
    register_analysis_routes(app, container, logger)
    register_cache_routes(app, container, logger)
    
    # ✅ PHASE 8: Hexagonal architecture API routes
    register_hexagonal_analysis_routes(app)
    register_risk_management_routes(app)
    register_signal_validation_routes(app)
    register_mt5_routes(app)
    register_bot_inject_routes(app)
    register_fmp_ledger_routes(app)

    # Ponderacion API routes
    ponderacion_cache = _PonderacionCacheAdapter(logger=logger)
    ponderacion_history = PonderacionHistory(redis_client=ponderacion_cache.redis_client)
    ponderacion_alert = PonderacionAlert(redis_client=ponderacion_cache.redis_client)
    register_ponderacion_routes(
        app,
        ponderacion_cache=ponderacion_cache,
        ponderacion_history=ponderacion_history,
        ponderacion_alert=ponderacion_alert,
    )
    logger.info("✅ Hexagonal API routes registered")

    # Payment routes (PayPal)
    register_payment_routes(app)
    logger.info("✅ Payment routes registered")

    # WhatsApp support routes (UltraMsg external service)
    register_whatsapp_routes(app)
    logger.info("✅ WhatsApp routes registered")

    # Legacy routes migrated into hexagonal registration
    legacy_services = getattr(container, "legacy_services", None)
    if legacy_services is not None:
        register_analisis_routes(app, services=legacy_services)
        register_webhook_routes(app, services=legacy_services)
        register_monitoreo_routes(app, services=legacy_services)
        register_cache_routes_legacy(app, services=legacy_services)
        register_live_entries_routes(app, services=legacy_services)
        # backtest routes removed — backtest is now 100% client-side
        logger.info("✅ Legacy routes registered via container")
    
    # Health check endpoint
    @app.route("/api/v1/health", methods=["GET"])
    def health_check():
        return {
            "status": "ok",
            "service": "MarketTool API",
            "version": "2.0.0",
        }, 200

    @app.route("/storage/files/<path:rel_path>", methods=["GET"])
    def storage_file(rel_path: str):
        if not vps_mode_enabled():
            abort(404)
        store = VpsJsonStore.from_env()
        full_path = (store.root / rel_path).resolve()
        root = store.root.resolve()
        if root not in full_path.parents and full_path != root:
            abort(400)
        if not full_path.exists() or not full_path.is_file():
            abort(404)
        return send_file(full_path)
    
    logger.info("✅ All API routes registered")
