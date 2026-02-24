"""Google Cloud Storage client abstraction."""

from __future__ import annotations

import asyncio
pass
import logging
from typing import Optional

from markettool.core.errors import StorageError


class GCSClient:
    """
    Google Cloud Storage client for file persistence.
    Handles uploading/downloading files and caches.
    """
    
    def __init__(
        self,
        bucket_name: str,
        project_id: str,
        credentials_path: Optional[str] = None,
        logger: Optional[logging.Logger] = None,
    ):
        """
        Initialize GCS client.
        
        Args:
            bucket_name: GCS bucket name
            project_id: GCP project ID
            credentials_path: Path to service account JSON
            logger: Optional logger instance
        """
        self.bucket_name = bucket_name
        self.project_id = project_id
        self.credentials_path = credentials_path
        self.logger = logger or logging.getLogger(__name__)
        self._client = None
    
    @property
    def client(self):
        """Lazy initialization of GCS client."""
        if self._client is None:
            self._init_client()
        return self._client
    
    def _init_client(self):
        """Initialize GCS client."""
        try:
            from google.cloud import storage
            
            if self.credentials_path:
                self._client = storage.Client(
                    project=self.project_id,
                    credentials=self._load_credentials(self.credentials_path),
                )
            else:
                self._client = storage.Client(project=self.project_id)
            
            self.logger.info(f"GCS client initialized for bucket {self.bucket_name}")
        except Exception as e:
            raise StorageError(f"Failed to initialize GCS: {e}")
    
    @staticmethod
    def _load_credentials(path: str):
        """Load GCP service account credentials."""
        from google.oauth2 import service_account
        return service_account.Credentials.from_service_account_file(path)
    
    async def upload_file(
        self,
        local_path: str,
        remote_path: str,
        public: bool = False,
    ) -> str:
        """
        Upload a file to GCS.
        
        Args:
            local_path: Local file path
            remote_path: Remote path in GCS
            public: Make file publicly readable
            
        Returns:
            Public URL if public=True, else bucket path
        """
        try:
            bucket = self.client.bucket(self.bucket_name)
            blob = bucket.blob(remote_path)
            blob.upload_from_filename(local_path)
            
            if public:
                blob.make_public()
                return blob.public_url
            
            return f"gs://{self.bucket_name}/{remote_path}"
        except Exception as e:
            raise StorageError(f"Failed to upload {local_path}: {e}")
    
    async def download_file(
        self,
        remote_path: str,
        local_path: str,
    ) -> None:
        """
        Download a file from GCS.
        
        Args:
            remote_path: Remote path in GCS
            local_path: Local destination path
        """
        try:
            bucket = self.client.bucket(self.bucket_name)
            blob = bucket.blob(remote_path)
            blob.download_to_filename(local_path)
        except Exception as e:
            raise StorageError(f"Failed to download {remote_path}: {e}")
    
    async def download_to_bytes(self, remote_path: str) -> bytes:
        """
        Download file contents as bytes.
        
        Args:
            remote_path: Remote path in GCS
            
        Returns:
            File contents as bytes
        """
        try:
            bucket = self.client.bucket(self.bucket_name)
            blob = bucket.blob(remote_path)
            return blob.download_as_bytes()
        except Exception as e:
            raise StorageError(f"Failed to download {remote_path}: {e}")
    
    async def upload_bytes(
        self,
        remote_path: str,
        data: bytes,
        content_type: str = "application/octet-stream",
    ) -> str:
        """
        Upload bytes to GCS.
        
        Args:
            remote_path: Remote path in GCS
            data: Data to upload
            content_type: MIME type
            
        Returns:
            GCS path
        """
        try:
            bucket = self.client.bucket(self.bucket_name)
            blob = bucket.blob(remote_path)
            blob.upload_from_string(data, content_type=content_type)
            return f"gs://{self.bucket_name}/{remote_path}"
        except Exception as e:
            raise StorageError(f"Failed to upload to {remote_path}: {e}")
    
    async def delete_file(self, remote_path: str) -> None:
        """Delete a file from GCS."""
        try:
            bucket = self.client.bucket(self.bucket_name)
            blob = bucket.blob(remote_path)
            blob.delete()
        except Exception as e:
            raise StorageError(f"Failed to delete {remote_path}: {e}")
    
    async def list_files(self, prefix: str = "") -> list:
        """List files in bucket with optional prefix."""
        try:
            bucket = self.client.bucket(self.bucket_name)
            blobs = bucket.list_blobs(prefix=prefix)
            return [blob.name for blob in blobs]
        except Exception as e:
            raise StorageError(f"Failed to list files: {e}")
    
    async def file_exists(self, remote_path: str) -> bool:
        """Check if file exists in GCS."""
        try:
            bucket = self.client.bucket(self.bucket_name)
            blob = bucket.blob(remote_path)
            return blob.exists()
        except Exception as e:
            self.logger.error(f"Failed to check file existence: {e}")
            return False
    
    async def get_file_size(self, remote_path: str) -> Optional[int]:
        """Get size of a file in GCS (bytes)."""
        try:
            bucket = self.client.bucket(self.bucket_name)
            blob = bucket.blob(remote_path)
            blob.reload()
            return blob.size
        except Exception as e:
            self.logger.error(f"Failed to get file size: {e}")
            return None
    
    async def health_check(self) -> bool:
        """Check if GCS is accessible."""
        try:
            bucket = self.client.bucket(self.bucket_name)
            return bucket.exists()
        except Exception as e:
            self.logger.error(f"GCS health check failed: {e}")
            return False

    async def batch_upload_bytes(
        self,
        uploads: list[tuple[str, bytes, str]],
        max_concurrent: int = 10,
    ) -> list[tuple[str, Optional[str]]]:
        """
        Upload multiple byte payloads in parallel with concurrency control.
        
        Args:
            uploads: List of (remote_path, data, content_type) tuples
            max_concurrent: Max concurrent uploads (default 10)
            
        Returns:
            List of (remote_path, result_or_error) tuples in same order
        """
        semaphore = asyncio.Semaphore(max_concurrent)
        
        async def _upload_single(remote_path: str, data: bytes, content_type: str):
            async with semaphore:
                try:
                    bucket = self.client.bucket(self.bucket_name)
                    blob = bucket.blob(remote_path)
                    blob.upload_from_string(data, content_type=content_type)
                    return (remote_path, f"gs://{self.bucket_name}/{remote_path}")
                except Exception as e:
                    self.logger.error(f"Batch upload failed for {remote_path}: {e}")
                    return (remote_path, None)
        
        # Create all tasks
        tasks = [
            _upload_single(remote_path, data, content_type)
            for remote_path, data, content_type in uploads
        ]
        
        # Execute all in parallel with concurrency control
        results = await asyncio.gather(*tasks, return_exceptions=False)
        return results

    async def batch_upload_files(
        self,
        uploads: list[tuple[str, str]],
        max_concurrent: int = 5,
    ) -> list[tuple[str, Optional[str]]]:
        """
        Upload multiple files in parallel from local filesystem.
        
        Args:
            uploads: List of (local_path, remote_path) tuples
            max_concurrent: Max concurrent uploads (default 5)
            
        Returns:
            List of (remote_path, result_or_error) tuples
        """
        semaphore = asyncio.Semaphore(max_concurrent)
        
        async def _upload_single(local_path: str, remote_path: str):
            async with semaphore:
                try:
                    bucket = self.client.bucket(self.bucket_name)
                    blob = bucket.blob(remote_path)
                    blob.upload_from_filename(local_path)
                    return (remote_path, f"gs://{self.bucket_name}/{remote_path}")
                except Exception as e:
                    self.logger.error(f"Batch file upload failed for {remote_path}: {e}")
                    return (remote_path, None)
        
        # Create all tasks
        tasks = [
            _upload_single(local_path, remote_path)
            for local_path, remote_path in uploads
        ]
        
        # Execute all in parallel with concurrency control
        results = await asyncio.gather(*tasks, return_exceptions=False)
        return results
