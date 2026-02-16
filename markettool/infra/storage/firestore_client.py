"""Firestore client abstraction."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from markettool.core.errors import StorageError


class FirestoreClient:
    """
    Firestore persistence layer.
    Handles document CRUD operations for MarketTool entities.
    """
    
    def __init__(
        self,
        project_id: str,
        credentials_path: Optional[str] = None,
        logger: Optional[logging.Logger] = None,
    ):
        """
        Initialize Firestore client.
        
        Args:
            project_id: GCP project ID
            credentials_path: Path to service account JSON (if not using default)
            logger: Optional logger instance
        """
        self.project_id = project_id
        self.credentials_path = credentials_path
        self.logger = logger or logging.getLogger(__name__)
        self._client = None
    
    @property
    def client(self):
        """Lazy initialization of Firebase client."""
        if self._client is None:
            self._init_client()
        return self._client
    
    def _init_client(self):
        """Initialize Firebase/Firestore client."""
        try:
            import firebase_admin
            from firebase_admin import credentials, firestore
            
            if self.credentials_path:
                cred = credentials.Certificate(self.credentials_path)
            else:
                cred = credentials.ApplicationDefault()
            
            if not firebase_admin._apps:
                firebase_admin.initialize_app(cred)
            
            self._client = firestore.client()
            self.logger.info(f"Firestore client initialized for project {self.project_id}")
        except Exception as e:
            raise StorageError(f"Failed to initialize Firestore: {e}")
    
    async def get_document(self, collection: str, doc_id: str) -> Optional[Dict[str, Any]]:
        """
        Fetch a document from Firestore.
        
        Args:
            collection: Collection name
            doc_id: Document ID
            
        Returns:
            Document data or None if not found
        """
        try:
            doc = self.client.collection(collection).document(doc_id).get()
            return doc.to_dict() if doc.exists else None
        except Exception as e:
            raise StorageError(f"Failed to get {collection}/{doc_id}: {e}")
    
    async def set_document(
        self,
        collection: str,
        doc_id: str,
        data: Dict[str, Any],
        merge: bool = False,
    ) -> None:
        """
        Write/update a document in Firestore.
        
        Args:
            collection: Collection name
            doc_id: Document ID
            data: Document data
            merge: Merge with existing or overwrite
        """
        try:
            self.client.collection(collection).document(doc_id).set(
                data,
                merge=merge,
            )
        except Exception as e:
            raise StorageError(f"Failed to set {collection}/{doc_id}: {e}")
    
    async def delete_document(self, collection: str, doc_id: str) -> None:
        """Delete a document from Firestore."""
        try:
            self.client.collection(collection).document(doc_id).delete()
        except Exception as e:
            raise StorageError(f"Failed to delete {collection}/{doc_id}: {e}")
    
    async def list_documents(self, collection: str) -> List[Dict[str, Any]]:
        """List all documents in a collection."""
        try:
            docs = self.client.collection(collection).stream()
            return [doc.to_dict() for doc in docs]
        except Exception as e:
            raise StorageError(f"Failed to list {collection}: {e}")
    
    async def query(
        self,
        collection: str,
        filters: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        """
        Query documents with filters.
        
        Args:
            collection: Collection name
            filters: Dict of field -> value filters
            
        Returns:
            List of matching documents
        """
        try:
            query = self.client.collection(collection)
            for field, value in filters.items():
                query = query.where(field, "==", value)
            return [doc.to_dict() for doc in query.stream()]
        except Exception as e:
            raise StorageError(f"Failed to query {collection}: {e}")
    
    async def batch_write(
        self,
        operations: List[Dict[str, Any]],
    ) -> None:
        """
        Batch write multiple operations.
        
        Args:
            operations: List of {"action": "set"|"delete", "collection": str, "doc_id": str, "data": dict}
        """
        try:
            batch = self.client.batch()
            for op in operations:
                action = op["action"]
                collection = op["collection"]
                doc_id = op["doc_id"]
                ref = self.client.collection(collection).document(doc_id)
                
                if action == "set":
                    batch.set(ref, op["data"], merge=True)
                elif action == "delete":
                    batch.delete(ref)
            
            batch.commit()
        except Exception as e:
            raise StorageError(f"Batch write failed: {e}")
    
    async def health_check(self) -> bool:
        """Check if Firestore is accessible."""
        try:
            self.client.collection("_health_check").document("_test").update({"_ts": 1})
            return True
        except Exception as e:
            self.logger.error(f"Firestore health check failed: {e}")
            return False
