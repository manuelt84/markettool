"""Local filesystem cache implementation."""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Optional, Set

from markettool.core.models.historico import Historico


class LocalCache:
    """File-based cache using local filesystem."""
    
    def __init__(self, cache_dir: str = "./cache", logger: Optional[logging.Logger] = None):
        self.cache_dir = cache_dir
        self.logger = logger or logging.getLogger(__name__)
        os.makedirs(cache_dir, exist_ok=True)
    
    async def get(self, key: str) -> Optional[Any]:
        """Get from local cache."""
        path = os.path.join(self.cache_dir, key + ".json")
        try:
            if os.path.exists(path):
                with open(path, "r") as f:
                    return json.load(f)
        except Exception as e:
            self.logger.error(f"Failed to read cache: {e}")
        return None
    
    async def set(self, key: str, value: Any, ttl_seconds: Optional[int] = None) -> None:
        """Set in local cache."""
        path = os.path.join(self.cache_dir, key + ".json")
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as f:
                json.dump(value if not isinstance(value, Historico) else value.to_dict(), f)
        except Exception as e:
            self.logger.error(f"Failed to write cache: {e}")
    
    async def delete(self, key: str) -> None:
        """Delete from local cache."""
        path = os.path.join(self.cache_dir, key + ".json")
        try:
            if os.path.exists(path):
                os.remove(path)
        except Exception as e:
            self.logger.error(f"Failed to delete cache: {e}")
    
    async def exists(self, key: str) -> bool:
        """Check if key exists."""
        path = os.path.join(self.cache_dir, key + ".json")
        return os.path.exists(path)
    
    async def clear(self) -> None:
        """Clear local cache."""
        try:
            import shutil
            if os.path.exists(self.cache_dir):
                shutil.rmtree(self.cache_dir)
            os.makedirs(self.cache_dir, exist_ok=True)
        except Exception as e:
            self.logger.error(f"Failed to clear cache: {e}")
    
    async def get_historico(self, symbol: str, timeframe: str) -> Optional[Historico]:
        """Get cached historico."""
        return None
    
    async def set_historico(self, historico: Historico, ttl_seconds: Optional[int] = None) -> None:
        """Cache historico."""
        await self.set(f"historicos_{historico.symbol}_{historico.timeframe}", historico.to_dict())
    
    async def invalidate_historico(self, symbol: str, timeframe: str) -> None:
        """Invalidate cached historico."""
        await self.delete(f"historicos_{symbol}_{timeframe}")
    
    async def get_cache_stats(self) -> dict:
        """Get cache statistics."""
        return {"type": "local", "dir": self.cache_dir}
    
    async def warmup(self, symbols: Set[str]) -> None:
        """Warmup cache (placeholder)."""
        pass
