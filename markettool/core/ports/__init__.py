"""Port definitions (domain interfaces)."""

from .historicos_repo import HistoricosRepository
from .quote_provider import QuoteProvider
from .cache_provider import CacheProvider
from .notifier import Notifier

__all__ = [
    "HistoricosRepository",
    "QuoteProvider",
    "CacheProvider",
    "Notifier",
]
