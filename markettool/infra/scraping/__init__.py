"""Web scraping layer."""

from .investing_adapter import InvestingAdapter
from .playwright_adapter import PlaywrightAdapter

__all__ = ["InvestingAdapter", "PlaywrightAdapter"]
