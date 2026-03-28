"""Health check endpoints for production monitoring."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from flask import Flask, jsonify, Response

logger = logging.getLogger(__name__)


@dataclass
class HealthStatus:
    """Health check status information."""
    status: str  # "healthy", "degraded", "unhealthy"
    timestamp: str
    uptime_seconds: float
    version: str
    
    # Component health
    telegram_bot: bool
    firestore: bool
    cache: bool
    
    # Additional info
    environment: str
    worker_id: str
    
    # MarketTool specific metrics (optional)
    warmup_status: str = "unknown"
    cache_stats: Dict[str, Any] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON response."""
        result = {
            "status": self.status,
            "timestamp": self.timestamp,
            "uptime_seconds": self.uptime_seconds,
            "version": self.version,
            "components": {
                "telegram_bot": "healthy" if self.telegram_bot else "unhealthy",
                "firestore": "healthy" if self.firestore else "unhealthy",
                "cache": "healthy" if self.cache else "unhealthy",
            },
            "environment": self.environment,
            "worker_id": self.worker_id,
        }
        
        # Add MarketTool specific metrics if available
        if self.warmup_status != "unknown":
            result["warmup_status"] = self.warmup_status
        
        if self.cache_stats:
            result["cache_stats"] = self.cache_stats
        
        return result


class HealthChecker:
    """
    Service health checker.
    
    ✅ Hexagonal architecture compliant: Uses HealthService from DI Container
    instead of importing from MarketTool.py legacy.
    """
    
    def __init__(self, health_service: Optional[Any] = None) -> None:
        """
        Initialize health checker.
        
        Args:
            health_service: HealthService instance from DI Container (optional for backward compatibility)
        """
        self._health_service = health_service
    
    @property
    def is_ready(self) -> bool:
        """Check if service is ready to accept requests."""
        if self._health_service:
            return self._health_service.is_ready
        # Fallback for legacy code paths
        return getattr(self, "_ready", False)
    
    def mark_ready(self) -> None:
        """Mark service as ready."""
        if self._health_service:
            self._health_service.mark_ready()
        else:
            self._ready = True
            logger.info("✅ Service marked as READY")
    
    def mark_not_ready(self) -> None:
        """Mark service as not ready (e.g., during shutdown)."""
        if self._health_service:
            self._health_service.mark_not_ready()
        else:
            self._ready = False
            logger.info("⏸️  Service marked as NOT READY")
    
    async def get_health_status(self) -> HealthStatus:
        """
        Get comprehensive health status.
        
        ✅ Uses HealthService if available (hexagonal), falls back to legacy if not.
        """
        if self._health_service:
            # Use hexagonal HealthService
            system_health = await self._health_service.get_system_health()
            
            # Convert to legacy HealthStatus format
            return HealthStatus(
                status=system_health.status,
                timestamp=system_health.timestamp,
                uptime_seconds=system_health.uptime_seconds,
                version=system_health.version,
                telegram_bot=system_health.components.get("telegram_bot", type("obj", (), {"healthy": False})()).healthy,
                firestore=system_health.components.get("firestore", type("obj", (), {"healthy": False})()).healthy,
                cache=system_health.components.get("cache", type("obj", (), {"healthy": False})()).healthy,
                environment=system_health.environment,
                worker_id=system_health.worker_id,
            )
        else:
            # ⚠️ DEPRECATED: Legacy fallback (imports from MarketTool.py)
            logger.warning("HealthChecker using DEPRECATED legacy fallback - please inject HealthService")
            return await self._get_health_status_legacy()
    
    async def _get_health_status_legacy(self) -> HealthStatus:
        """
        ⚠️ DEPRECATED: Legacy health check with MarketTool.py imports.
        This method will be removed once all deployments use DI Container.
        """
        # Check all components in parallel
        telegram_ok, firestore_ok, cache_ok = await asyncio.gather(
            self._check_telegram_bot_legacy(),
            self._check_firestore_legacy(),
            self._check_cache_legacy(),
            return_exceptions=True
        )
        
        # Handle exceptions from gather
        telegram_ok = telegram_ok if isinstance(telegram_ok, bool) else False
        firestore_ok = firestore_ok if isinstance(firestore_ok, bool) else False
        cache_ok = cache_ok if isinstance(cache_ok, bool) else False
        
        # Determine overall status
        all_ok = telegram_ok and firestore_ok and cache_ok
        status = "healthy" if all_ok else "degraded"
        
        start_time = getattr(self, "_start_time", time.time())
        uptime = time.time() - start_time
        
        return HealthStatus(
            status=status,
            timestamp=datetime.now(timezone.utc).isoformat(),
            uptime_seconds=round(uptime, 2),
            version=os.environ.get("APP_VERSION", "unknown"),
            telegram_bot=telegram_ok,
            firestore=firestore_ok,
            cache=cache_ok,
            environment=os.environ.get("ENVIRONMENT", "production"),
            worker_id=os.environ.get("WORKER_ID", "unknown"),
        )
    
    async def _check_telegram_bot_legacy(self) -> bool:
        """⚠️ DEPRECATED: Legacy check that imports from MarketTool.py"""
        try:
            from MarketTool import application
            return application is not None and application.bot is not None
        except Exception as exc:
            logger.warning("Telegram bot health check failed: %s", exc)
            return False
    
    async def _check_firestore_legacy(self) -> bool:
        """⚠️ DEPRECATED: Legacy check that imports from MarketTool.py"""
        try:
            from MarketTool import db
            if db is None:
                return False
            # Quick ping - check if we can list collections
            # max_results not supported in all versions; use limit via itertools
            import itertools
            list(itertools.islice(db.collections(), 1))
            return True
        except Exception as exc:
            logger.warning("Firestore health check failed: %s", exc)
            return False
    
    async def _check_cache_legacy(self) -> bool:
        """⚠️ DEPRECATED: Legacy check that imports from MarketTool.py"""
        try:
            from MarketTool import historicos_cache
            return historicos_cache is not None
        except Exception as exc:
            logger.warning("Cache health check failed: %s", exc)
            return False


# Global health checker instance
_health_checker: HealthChecker | None = None


def get_health_checker(health_service: Optional[Any] = None) -> HealthChecker:
    """
    Get or create global health checker instance.
    
    Args:
        health_service: Optional HealthService from DI Container (recommended)
        
    Returns:
        HealthChecker instance
        
    Note:
        If health_service is provided (hexagonal), it will be used for health checks.
        Otherwise falls back to legacy imports from MarketTool.py (deprecated).
    """
    global _health_checker
    if _health_checker is None:
        _health_checker = HealthChecker(health_service=health_service)
    elif health_service is not None and _health_checker._health_service is None:
        # Update existing instance with health service if it didn't have one
        _health_checker._health_service = health_service
    return _health_checker


def register_health_routes(
    app: Flask,
    health_service: Optional[Any] = None,
    warmup_start_ref=None,
    warmup_end_ref=None,
    levels_hits_ref=None,
    levels_misses_ref=None,
    atr_hits_ref=None,
    atr_misses_ref=None,
    app_config=None,
) -> None:
    """
    Register health check routes with Flask app.
    
    Args:
        app: Flask application
        health_service: HealthService from DI Container (recommended for hexagonal architecture)
        warmup_start_ref: Callable that returns warmup start time (optional)
        warmup_end_ref: Callable that returns warmup end time (optional)
        levels_hits_ref: Callable that returns niveles cache hits (optional)
        levels_misses_ref: Callable that returns niveles cache misses (optional)
        atr_hits_ref: Callable that returns ATR cache hits (optional)
        atr_misses_ref: Callable that returns ATR cache misses (optional)
        app_config: App configuration object (optional)
    """
    
    health_checker = get_health_checker(health_service=health_service)
    
    def _get_cache_stats() -> Dict[str, Any] | None:
        """Get MarketTool cache statistics if available."""
        if not all([levels_hits_ref, levels_misses_ref, atr_hits_ref, atr_misses_ref]):
            return None
        
        try:
            niveles_hits = levels_hits_ref()
            niveles_misses = levels_misses_ref()
            atr_hits = atr_hits_ref()
            atr_misses = atr_misses_ref()
            
            return {
                "niveles_hits": niveles_hits,
                "niveles_misses": niveles_misses,
                "niveles_hit_rate": round(100 * niveles_hits / max(1, niveles_hits + niveles_misses), 1),
                "atr_hits": atr_hits,
                "atr_misses": atr_misses,
                "atr_hit_rate": round(100 * atr_hits / max(1, atr_hits + atr_misses), 1),
            }
        except Exception:
            return None
    
    def _get_warmup_status() -> Dict[str, Any] | None:
        """Get warmup status if available."""
        if not warmup_start_ref or not warmup_end_ref:
            return None
        
        try:
            warmup_start_time = warmup_start_ref()
            warmup_end_time = warmup_end_ref()
            
            if warmup_start_time is None:
                return {"status": "not started"}
            
            if warmup_end_time is not None:
                return {
                    "status": "completed",
                    "start_time": warmup_start_time,
                    "end_time": warmup_end_time,
                    "elapsed_seconds": warmup_end_time - warmup_start_time,
                }
            else:
                return {
                    "status": "in progress",
                    "start_time": warmup_start_time,
                    "elapsed_seconds": time.time() - warmup_start_time,
                }
        except Exception:
            return None
    
    @app.route("/health", methods=["GET"])
    def health() -> tuple[Response, int]:
        """
        Comprehensive health check endpoint.
        Returns 200 if service is healthy, 503 if degraded.
        Includes MarketTool cache stats if configured.
        """
        try:
            # Run async health check
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                status = loop.run_until_complete(health_checker.get_health_status())
            finally:
                loop.close()
            
            response_data = status.to_dict()
            
            # Add MarketTool specific metrics if available
            cache_stats = _get_cache_stats()
            if cache_stats:
                response_data["cache_stats"] = cache_stats
            
            warmup_info = _get_warmup_status()
            if warmup_info:
                response_data["warmup"] = warmup_info
            
            if app_config:
                response_data["warmup_config"] = {
                    "enabled": getattr(app_config, "cache_warmup_enabled", None),
                    "blocking_startup": getattr(app_config, "cache_warmup_blocking_startup", None),
                    "leader_only": getattr(app_config, "cache_warmup_leader_only", None),
                    "concurrency": getattr(app_config, "cache_warmup_concurrency", None),
                    "max_ram_percent": getattr(app_config, "cache_warmup_max_ram_percent", None),
                }
            
            status_code = 200 if status.status == "healthy" else 503
            return jsonify(response_data), status_code
        except Exception as exc:
            logger.exception("Health check failed: %s", exc)
            return jsonify({
                "status": "unhealthy",
                "error": str(exc),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }), 503
    
    @app.route("/ready", methods=["GET"])
    def ready() -> tuple[Response, int]:
        """
        Readiness probe endpoint.
        Returns 200 if service is ready to accept requests, 503 otherwise.
        Used by Kubernetes for traffic routing.
        """
        if health_checker.is_ready:
            return jsonify({
                "status": "ready",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }), 200
        else:
            return jsonify({
                "status": "not_ready",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }), 503
    
    @app.route("/healthz", methods=["GET"])
    def healthz() -> tuple[Response, int]:
        """
        Simple liveness probe endpoint.
        Returns 200 if process is alive.
        Used by Docker HEALTHCHECK and Kubernetes liveness probes.
        """
        return jsonify({
            "status": "alive",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }), 200
    
    @app.route("/startup", methods=["GET"])
    def startup() -> tuple[Response, int]:
        """
        Startup probe endpoint.
        Returns 200 once application has completed initialization.
        Used by Kubernetes startup probes.
        """
        if health_checker.is_ready:
            return jsonify({
                "status": "started",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }), 200
        else:
            return jsonify({
                "status": "starting",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }), 503
    
    @app.route("/cache-status", methods=["GET"])
    def cache_status() -> tuple[Response, int]:
        """
        Legacy cache status endpoint for MarketTool metrics.
        Returns detailed cache hit rates and warmup information.
        Maintained for backward compatibility with monitoring systems.
        """
        try:
            response = {}
            
            # Add cache statistics
            cache_stats = _get_cache_stats()
            if cache_stats:
                response["cache"] = cache_stats
            
            # Add warmup information
            warmup_info = _get_warmup_status()
            if warmup_info:
                response["warmup"] = warmup_info
            
            # Add configuration if available
            if app_config:
                response["config"] = {
                    "warmup_enabled": getattr(app_config, "cache_warmup_enabled", None),
                    "blocking_startup": getattr(app_config, "cache_warmup_blocking_startup", None),
                    "leader_only": getattr(app_config, "cache_warmup_leader_only", None),
                    "concurrency": getattr(app_config, "cache_warmup_concurrency", None),
                    "max_ram_percent": getattr(app_config, "cache_warmup_max_ram_percent", None),
                }
            
            response["timestamp"] = datetime.now(timezone.utc).isoformat()
            
            return jsonify(response), 200
        except Exception as exc:
            logger.exception("Cache status check failed: %s", exc)
            return jsonify({
                "error": str(exc),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }), 500
    
    @app.route("/", methods=["GET"])
    def index() -> tuple[str, int]:
        """
        Root endpoint.
        Returns simple status message.
        """
        return "El bot esta funcionando", 200
    
    logger.info("✅ Health check routes registered: /health, /ready, /healthz, /startup, /cache-status, /")
