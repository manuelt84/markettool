"""Legacy route use cases."""

from .analisis_use_case import LegacyAnalisisUseCase
from .monitoreo_use_case import LegacyMonitoreoUseCase
from .webhook_use_case import LegacyWebhookUseCase
from .cache_use_case import LegacyCacheUseCase

__all__ = [
    "LegacyAnalisisUseCase",
    "LegacyMonitoreoUseCase",
    "LegacyWebhookUseCase",
    "LegacyCacheUseCase",
]
