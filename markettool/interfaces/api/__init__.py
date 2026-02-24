"""HTTP API interface."""

from .app import get_asgi_app, get_webhook_app, asgi_app, webhook_app
from .route_factory import register_all_routes
from .historicos_routes import register_historicos_routes
from .quotes_routes import register_quotes_routes
from .analysis_routes import register_analysis_routes
from .cache_management_routes import register_cache_routes
from .analisis_routes import register_analisis_routes as register_analisis_routes_legacy
from .execution_routes import register_execution_routes
from .health_routes import register_health_routes
from .monitoreo_routes import register_monitoreo_routes
from .pod_routes import register_pod_routes
from .webhook_routes import register_webhook_routes

__all__ = [
	"get_asgi_app",
	"get_webhook_app",
	"asgi_app",
	"webhook_app",
	"register_all_routes",  # New factory
	"register_historicos_routes",  # New domain routes
	"register_quotes_routes",
	"register_analysis_routes",
	"register_cache_routes",
	# Legacy routes (deprecated)
	"register_analisis_routes_legacy",
	"register_execution_routes",
	"register_health_routes",
	"register_monitoreo_routes",
	"register_pod_routes",
	"register_webhook_routes",
]

