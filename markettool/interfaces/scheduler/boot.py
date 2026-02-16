"""Scheduler entrypoints (compat wrapper).

DEPRECATED: This is a compatibility wrapper. Use bot_init.py directly.
"""

from .bot_init import setup_scheduler

# Re-export for backward compatibility
__all__ = ["setup_scheduler"]
