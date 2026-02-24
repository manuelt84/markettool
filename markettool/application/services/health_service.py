"""Health check service for monitoring system components."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


@dataclass
class ComponentHealth:
    """Health status of a single component."""
    name: str
    healthy: bool
    latency_ms: Optional[float] = None
    error: Optional[str] = None


@dataclass
class SystemHealth:
    """Overall system health status."""
    status: str  # "healthy", "degraded", "unhealthy"
    timestamp: str
    uptime_seconds: float
    components: Dict[str, ComponentHealth]
    version: str
    environment: str
    worker_id: str
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON response."""
        return {
            "status": self.status,
            "timestamp": self.timestamp,
            "uptime_seconds": self.uptime_seconds,
            "version": self.version,
            "environment": self.environment,
            "worker_id": self.worker_id,
            "components": {
                name: {
                    "healthy": comp.healthy,
                    "latency_ms": comp.latency_ms,
                    "error": comp.error,
                }
                for name, comp in self.components.items()
            },
        }


class HealthService:
    """
    Application service for health monitoring.
    
    Uses dependency injection instead of importing from MarketTool.py legacy.
    """
    
    def __init__(
        self,
        telegram_app: Optional[Any] = None,
        firestore_db: Optional[Any] = None,
        cache_provider: Optional[Any] = None,
        version: str = "unknown",
        environment: str = "production",
        worker_id: str = "unknown",
    ):
        """
        Initialize health service with dependencies.
        
        Args:
            telegram_app: Telegram application instance
            firestore_db: Firestore database client
            cache_provider: Cache provider instance
            version: Application version
            environment: Deployment environment
            worker_id: Worker/pod identifier
        """
        self._telegram_app = telegram_app
        self._firestore_db = firestore_db
        self._cache_provider = cache_provider
        self._version = version
        self._environment = environment
        self._worker_id = worker_id
        self._start_time = time.time()
        self._ready = False
    
    @property
    def is_ready(self) -> bool:
        """Check if service is ready."""
        return self._ready
    
    def mark_ready(self) -> None:
        """Mark service as ready."""
        self._ready = True
        logger.info("✅ Service marked as READY")
    
    def mark_not_ready(self) -> None:
        """Mark service as not ready."""
        self._ready = False
        logger.info("⏸️  Service marked as NOT READY")
    
    async def check_telegram_bot(self) -> ComponentHealth:
        """Check Telegram bot health."""
        start = time.time()
        try:
            if self._telegram_app is None:
                return ComponentHealth(
                    name="telegram_bot",
                    healthy=False,
                    error="Telegram app not configured",
                )
            
            # Check if bot is accessible
            is_healthy = (
                self._telegram_app is not None
                and hasattr(self._telegram_app, "bot")
                and self._telegram_app.bot is not None
            )
            
            latency = (time.time() - start) * 1000
            return ComponentHealth(
                name="telegram_bot",
                healthy=is_healthy,
                latency_ms=round(latency, 2),
            )
        except Exception as exc:
            latency = (time.time() - start) * 1000
            logger.warning("Telegram bot health check failed: %s", exc)
            return ComponentHealth(
                name="telegram_bot",
                healthy=False,
                latency_ms=round(latency, 2),
                error=str(exc),
            )
    
    async def check_firestore(self) -> ComponentHealth:
        """Check Firestore health."""
        start = time.time()
        try:
            if self._firestore_db is None:
                return ComponentHealth(
                    name="firestore",
                    healthy=False,
                    error="Firestore not configured",
                )
            
            # Quick ping - list one collection
            list(self._firestore_db.collections(max_results=1))
            
            latency = (time.time() - start) * 1000
            return ComponentHealth(
                name="firestore",
                healthy=True,
                latency_ms=round(latency, 2),
            )
        except Exception as exc:
            latency = (time.time() - start) * 1000
            logger.warning("Firestore health check failed: %s", exc)
            return ComponentHealth(
                name="firestore",
                healthy=False,
                latency_ms=round(latency, 2),
                error=str(exc),
            )
    
    async def check_cache(self) -> ComponentHealth:
        """Check cache provider health."""
        start = time.time()
        try:
            if self._cache_provider is None:
                return ComponentHealth(
                    name="cache",
                    healthy=False,
                    error="Cache provider not configured",
                )
            
            # Cache provider is available (passive check)
            latency = (time.time() - start) * 1000
            return ComponentHealth(
                name="cache",
                healthy=True,
                latency_ms=round(latency, 2),
            )
        except Exception as exc:
            latency = (time.time() - start) * 1000
            logger.warning("Cache health check failed: %s", exc)
            return ComponentHealth(
                name="cache",
                healthy=False,
                latency_ms=round(latency, 2),
                error=str(exc),
            )
    
    async def get_system_health(self) -> SystemHealth:
        """Get comprehensive system health status."""
        # Check all components in parallel
        results = await asyncio.gather(
            self.check_telegram_bot(),
            self.check_firestore(),
            self.check_cache(),
            return_exceptions=True,
        )
        
        # Process results
        components = {}
        for result in results:
            if isinstance(result, ComponentHealth):
                components[result.name] = result
            else:
                # Exception occurred
                logger.error("Health check raised exception: %s", result)
        
        # Determine overall status
        all_healthy = all(comp.healthy for comp in components.values())
        any_healthy = any(comp.healthy for comp in components.values())
        
        if all_healthy:
            status = "healthy"
        elif any_healthy:
            status = "degraded"
        else:
            status = "unhealthy"
        
        uptime = time.time() - self._start_time
        
        return SystemHealth(
            status=status,
            timestamp=datetime.now(timezone.utc).isoformat(),
            uptime_seconds=round(uptime, 2),
            components=components,
            version=self._version,
            environment=self._environment,
            worker_id=self._worker_id,
        )
