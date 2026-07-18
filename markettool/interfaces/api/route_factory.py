"""Route factory for registering all API routes."""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

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
# backtest_routes removed — backtest is now 100% client-side

if TYPE_CHECKING:
    from flask import Flask
    from markettool.interfaces.containers import DIContainer


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "y", "on"}


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

    # By default keep broker execution APIs disabled in production hardening mode.
    if _env_flag("ENABLE_BROKER_EXECUTION", default=False):
        register_mt5_routes(app)
        register_bot_inject_routes(app)
        logger.info("Broker execution routes enabled (ENABLE_BROKER_EXECUTION=true)")
    else:
        logger.info("Broker execution routes disabled (set ENABLE_BROKER_EXECUTION=true to enable)")

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

    # Legacy routes migrated into hexagonal registration
    legacy_services = getattr(container, "legacy_services", None)
    if legacy_services is not None:
        register_analisis_routes(app, services=legacy_services)
        register_webhook_routes(app, services=legacy_services)
        register_monitoreo_routes(app, services=legacy_services)
        register_cache_routes_legacy(app, services=legacy_services)
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
    
    logger.info("✅ All API routes registered")
