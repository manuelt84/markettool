"""
Application-level adapters for bridging different components.

This package contains adapters that facilitate integration between:
- ParallelAnalysisEngine (new architecture)
- MarketTool.py (legacy functions)
- Bootstrap/configuration components

Exports:
    get_adapter: Get singleton instance of LegacyMarketToolAdapter
    LegacyMarketToolAdapter: Main adapter class
"""

from .legacy_adapter import LegacyMarketToolAdapter, get_adapter

__all__ = [
    'LegacyMarketToolAdapter',
    'get_adapter',
]
