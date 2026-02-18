"""
Application-level adapters for standalone analysis (100% no legacy).

This package contains adapters for:
- Technical analysis (ARIMA, indicators, patterns, Monte Carlo)
- Risk management (ATR, support/resistance)
- Signal synthesis

Exports:
    get_analyzer: Get singleton instance of StandaloneAnalyzer
    StandaloneAnalyzer: Main analyzer class
    Signal: Signal dataclass
"""

from .standalone_analyzer import StandaloneAnalyzer, get_analyzer, Signal

__all__ = [
    'StandaloneAnalyzer',
    'get_analyzer',
    'Signal',
]
