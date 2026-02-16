"""Firestore-backed metadata cache."""

from __future__ import annotations

import logging
from typing import Optional


class FirestoreMetadata:
    """Store cache metadata in Firestore."""
    
    def __init__(self, firestore_client, logger: Optional[logging.Logger] = None):
        self.firestore = firestore_client
        self.logger = logger or logging.getLogger(__name__)
    
    async def get_last_update(self, key: str) -> Optional[int]:
        """Get last update timestamp."""
        doc = await self.firestore.get_document("cache_metadata", key)
        return doc.get("timestamp") if doc else None
    
    async def set_last_update(self, key: str, timestamp: int) -> None:
        """Set last update timestamp."""
        await self.firestore.set_document("cache_metadata", key, {"timestamp": timestamp})
    
    async def get_cache_stats(self) -> dict:
        """Get stats from Firestore."""
        return {"type": "firestore_metadata"}
