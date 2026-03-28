"""ASGI and Flask app factory for HTTP API."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from flask import Flask, request, make_response
from asgiref.wsgi import WsgiToAsgi

if TYPE_CHECKING:
    from markettool.interfaces.containers import DIContainer

logger = logging.getLogger(__name__)

# Lazy-loaded app instances
_webhook_app: Flask | None = None
_asgi_app: WsgiToAsgi | None = None
_routes_registered: bool = False


def get_webhook_app(
    container: DIContainer | None = None,
) -> Flask:
    """
    Lazy-load Flask webhook app and optionally register all routes.
    
    Args:
        container: Optional DI container for route registration.
                 If provided, will register hexagonal architecture routes.
    
    Returns:
        Flask application instance
    """
    global _webhook_app, _routes_registered
    
    # Create Flask app if not already created
    if _webhook_app is None:
        _webhook_app = Flask(__name__)
        logger.info("✅ Flask webhook app created")
        
        # Register CORS middleware (manual implementation without flask_cors)
        @_webhook_app.before_request
        def handle_cors_preflight():
            """Handle CORS preflight requests and add CORS headers to all responses."""
            if request.method == "OPTIONS":
                response = make_response()
                response.headers["Access-Control-Allow-Origin"] = "*"
                response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS, HEAD"
                response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization, X-Requested-With"
                response.headers["Access-Control-Max-Age"] = "3600"
                return response
        
        @_webhook_app.after_request
        def add_cors_headers(response):
            """Add CORS headers to all responses."""
            response.headers["Access-Control-Allow-Origin"] = "*"
            response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS, HEAD"
            response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization, X-Requested-With"
            response.headers["Access-Control-Expose-Headers"] = "Content-Type, X-Total-Count"
            return response
        
        logger.info("✅ CORS middleware enabled for all /api/* routes")
    
    # Register routes once after app creation (if container provided)
    if container is not None and not _routes_registered:
        try:
            from markettool.interfaces.api.route_factory import register_all_routes
            
            register_all_routes(
                app=_webhook_app,
                container=container,
                logger=logger,
            )
            _routes_registered = True
            logger.info("✅ All API routes registered via DI container")
        except Exception as e:
            logger.error(f"Error registering API routes: {e}", exc_info=True)
            raise
    
    return _webhook_app


def get_asgi_app(
    container: DIContainer | None = None,
) -> WsgiToAsgi:
    """
    Lazy-load ASGI app wrapping Flask.
    
    Args:
        container: Optional DI container for Flask app initialization.
    
    Returns:
        ASGI application instance (WsgiToAsgi wrapper)
    """
    global _asgi_app
    
    if _asgi_app is None:
        _asgi_app = WsgiToAsgi(
            get_webhook_app(container=container)
        )
        logger.info("✅ ASGI app (WsgiToAsgi wrapper) created")
    
    return _asgi_app


def reset_app_instances() -> None:
    """Reset lazy-loaded app instances (useful for testing)."""
    global _webhook_app, _asgi_app, _routes_registered
    _webhook_app = None
    _asgi_app = None
    _routes_registered = False
    logger.debug("App instances reset")


# Module-level exports for backward compatibility
webhook_app = None
asgi_app = None
