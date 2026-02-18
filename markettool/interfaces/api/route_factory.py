"""Route factory for registering all API routes."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from markettool.interfaces.api.historicos_routes import register_historicos_routes
from markettool.interfaces.api.quotes_routes import register_quotes_routes
from markettool.interfaces.api.analysis_routes import register_analysis_routes
from markettool.interfaces.api.cache_management_routes import register_cache_routes
from markettool.interfaces.api.hexagonal_analysis_routes import register_hexagonal_analysis_routes
from markettool.interfaces.api.risk_management_routes import register_risk_management_routes
from markettool.interfaces.api.signal_validation_routes import register_signal_validation_routes

if TYPE_CHECKING:
    from flask import Flask
    from markettool.interfaces.containers import DIContainer


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
    logger.info("✅ Hexagonal API routes registered")
    
    # Health check endpoint
    @app.route("/api/v1/health", methods=["GET"])
    def health_check():
        return {
            "status": "ok",
            "service": "MarketTool API",
            "version": "2.0.0",
        }, 200
    
    logger.info("✅ All API routes registered")
