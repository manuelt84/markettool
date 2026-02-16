"""Unit tests for MultiLayerCacheProvider adapter."""

import pytest
from unittest.mock import AsyncMock
from datetime import datetime, timedelta
import pytz

from markettool.infra.repositories import MultiLayerCacheProvider
from markettool.core.models.historico import Historico
from markettool.core.errors import CacheError


@pytest.mark.asyncio
class TestMultiLayerCacheFallback:
    """Test multi-layer cache fallback chain."""
    
    async def test_get_from_memory_cache(self, multi_layer_cache):
        """Test getting value from memory cache."""
        # Setup: memory cache has value
        test_value = "cached_data"
        multi_layer_cache.memory.get = AsyncMock(return_value=test_value)
        
        result = await multi_layer_cache.get("test_key")
        
        assert result == test_value
        multi_layer_cache.memory.get.assert_called_once()
    
    async def test_fallback_to_local_cache(self, multi_layer_cache):
        """Test fallback from memory to local cache."""
        test_value = "local_data"
        
        # Setup: memory miss, local hit
        multi_layer_cache.memory.get = AsyncMock(return_value=None)
        multi_layer_cache.local.get = AsyncMock(return_value=test_value)
        multi_layer_cache.memory.set = AsyncMock()
        
        result = await multi_layer_cache.get("test_key")
        
        assert result == test_value
        # Should write back to memory
        multi_layer_cache.memory.set.assert_called_once()
    
    async def test_fallback_to_gcs_cache(self, multi_layer_cache):
        """Test fallback from memory/local to GCS."""
        test_value = "gcs_data"
        
        # Setup: memory and local miss, GCS hit
        multi_layer_cache.memory.get = AsyncMock(return_value=None)
        multi_layer_cache.local.get = AsyncMock(return_value=None)
        multi_layer_cache.gcs.get = AsyncMock(return_value=test_value)
        multi_layer_cache.memory.set = AsyncMock()
        multi_layer_cache.local.set = AsyncMock()
        
        result = await multi_layer_cache.get("test_key")
        
        assert result == test_value
        # Should propagate back to both memory and local
        multi_layer_cache.memory.set.assert_called_once()
        multi_layer_cache.local.set.assert_called_once()
    
    async def test_complete_miss(self, multi_layer_cache):
        """Test when value not in any cache layer."""
        # Setup: all caches return None
        multi_layer_cache.memory.get = AsyncMock(return_value=None)
        multi_layer_cache.local.get = AsyncMock(return_value=None)
        multi_layer_cache.gcs.get = AsyncMock(return_value=None)
        
        result = await multi_layer_cache.get("missing_key")
        
        assert result is None


@pytest.mark.asyncio
class TestMultiLayerCacheWrite:
    """Test multi-layer cache write operations."""
    
    async def test_set_writes_to_all_layers(self, multi_layer_cache):
        """Test that set writes to all configured layers."""
        test_value = "new_data"
        
        await multi_layer_cache.set("key", test_value, ttl_seconds=3600)
        
        # All layers should be written
        multi_layer_cache.memory.set.assert_called_once()
        multi_layer_cache.local.set.assert_called_once()
        multi_layer_cache.gcs.set.assert_called_once()
    
    async def test_set_continues_on_layer_error(self, multi_layer_cache):
        """Test that write to one layer error doesn't stop others."""
        # Setup: memory fails, others succeed
        multi_layer_cache.memory.set = AsyncMock(
            side_effect=Exception("Memory write failed")
        )
        multi_layer_cache.local.set = AsyncMock()
        multi_layer_cache.gcs.set = AsyncMock()
        
        # Should complete despite memory error
        await multi_layer_cache.set("key", "data")
        
        multi_layer_cache.local.set.assert_called_once()
        multi_layer_cache.gcs.set.assert_called_once()
    
    async def test_set_with_ttl(self, multi_layer_cache):
        """Test setting value with TTL."""
        await multi_layer_cache.set("key", "data", ttl_seconds=300)
        
        # Verify all layers called with TTL
        assert multi_layer_cache.memory.set.called
        assert multi_layer_cache.local.set.called
        assert multi_layer_cache.gcs.set.called


@pytest.mark.asyncio
class TestMultiLayerCacheInvalidation:
    """Test cache invalidation."""
    
    async def test_invalidate_all_layers(self, multi_layer_cache):
        """Test invalidating key in all layers."""
        await multi_layer_cache.invalidate("key")
        
        multi_layer_cache.memory.invalidate.assert_called_once_with("key")
        multi_layer_cache.local.invalidate.assert_called_once_with("key")
        multi_layer_cache.gcs.invalidate.assert_called_once_with("key")
    
    async def test_delete_alias(self, multi_layer_cache):
        """Test that delete works as alias for invalidate."""
        await multi_layer_cache.delete("key")
        
        # Should call invalidate
        assert multi_layer_cache.memory.invalidate.called
    
    async def test_clear_all_caches(self, multi_layer_cache):
        """Test clearing all cache layers."""
        await multi_layer_cache.clear()
        
        multi_layer_cache.memory.clear.assert_called_once()
        multi_layer_cache.local.clear.assert_called_once()
        multi_layer_cache.gcs.clear.assert_called_once()


@pytest.mark.asyncio
class TestMultiLayerCacheHistorico:
    """Test Historico-specific cache operations."""
    
    async def test_get_historico(self, multi_layer_cache, sample_historico):
        """Test getting cached Historico."""
        multi_layer_cache.memory.get = AsyncMock(return_value=sample_historico)
        
        result = await multi_layer_cache.get_historico("AAPL", "1h")
        
        assert result is not None
        assert result.symbol == "AAPL"
    
    async def test_set_historico(self, multi_layer_cache, sample_historico):
        """Test caching Historico."""
        await multi_layer_cache.set_historico(sample_historico)
        
        # Should call set on all layers
        assert multi_layer_cache.memory.set.called
        assert multi_layer_cache.local.set.called
        assert multi_layer_cache.gcs.set.called
    
    async def test_invalidate_historico(self, multi_layer_cache):
        """Test invalidating Historico cache."""
        await multi_layer_cache.invalidate_historico("AAPL", "1h")
        
        # Should invalidate key pattern
        assert multi_layer_cache.memory.invalidate.called


@pytest.mark.asyncio
class TestMultiLayerCacheStats:
    """Test cache statistics."""
    
    async def test_get_cache_stats(self, multi_layer_cache):
        """Test retrieving cache statistics."""
        multi_layer_cache.memory.get_stats = AsyncMock(
            return_value={"type": "memory", "size": 100}
        )
        multi_layer_cache.local.get_stats = AsyncMock(
            return_value={"type": "local", "size": 500}
        )
        multi_layer_cache.gcs.get_stats = AsyncMock(
            return_value={"type": "gcs", "size": 5000}
        )
        
        stats = await multi_layer_cache.get_cache_stats()
        
        assert "layers" in stats
        assert "memory" in stats["layers"]
        assert "local" in stats["layers"]
        assert "gcs" in stats["layers"]
