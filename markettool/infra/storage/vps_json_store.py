"""Configurable VPS/local storage and PostgreSQL document metadata.

This module is intentionally small and dependency-light.  It gives the legacy
MarketTool runtime a Firestore/GCS-shaped fallback without making Google Cloud
mandatory when billing is disabled.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

logger = logging.getLogger(__name__)


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def cloud_backend() -> str:
    return (os.getenv("MARKETTOOL_CLOUD_BACKEND") or os.getenv("CLOUD_BACKEND") or "gcp").strip().lower()


def vps_mode_enabled() -> bool:
    return cloud_backend() in {"vps", "postgres", "local", "filesystem", "fs"}


def _clean_rel_path(value: str) -> str:
    clean = str(value or "").strip().replace("\\", "/")
    clean = clean.split("?", 1)[0]
    if clean.startswith("gs://"):
        clean = clean.replace("gs://", "", 1).split("/", 1)[-1]
    clean = clean.lstrip("/")
    parts = [p for p in clean.split("/") if p and p not in {".", ".."}]
    return "/".join(parts)


@dataclass
class VpsJsonStore:
    root: Path
    public_base_url: str = ""
    rsync_target: str = ""
    rsync_port: str = ""

    @classmethod
    def from_env(cls) -> "VpsJsonStore":
        return cls(
            root=Path(os.getenv("MARKETTOOL_VPS_STORAGE_ROOT", "/app/storage/markettool-json")),
            public_base_url=(os.getenv("MARKETTOOL_VPS_STORAGE_PUBLIC_BASE_URL") or "").rstrip("/"),
            rsync_target=os.getenv("MARKETTOOL_VPS_STORAGE_RSYNC_TARGET", "").strip(),
            rsync_port=os.getenv("MARKETTOOL_VPS_STORAGE_RSYNC_PORT", "").strip(),
        )

    def _path(self, rel_path: str) -> Path:
        safe = _clean_rel_path(rel_path)
        if not safe:
            raise ValueError("empty storage path")
        full = (self.root / safe).resolve()
        root = self.root.resolve()
        if root not in full.parents and full != root:
            raise ValueError(f"unsafe storage path: {rel_path}")
        return full

    def bucket(self, *_: Any, **__: Any) -> "VpsJsonStore":
        return self

    def blob(self, rel_path: str) -> "_VpsBlob":
        return _VpsBlob(self, rel_path)

    def public_url(self, rel_path: str) -> str:
        safe = _clean_rel_path(rel_path)
        api_base = (os.getenv("MARKETTOOL_API_PUBLIC_BASE_URL") or "https://api.mtlabsx.com").rstrip("/")
        if self.public_base_url:
            return f"{self.public_base_url}/{safe}"
        return f"{api_base}/storage/files/{safe}"

    def write_bytes(self, rel_path: str, data: bytes) -> str:
        dest = self._path(rel_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(dest)
        self._mirror(dest)
        return self.public_url(rel_path)

    def upload_file(self, source: str | os.PathLike[str], rel_path: str) -> str:
        dest = self._path(rel_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".tmp")
        shutil.copyfile(source, tmp)
        tmp.replace(dest)
        self._mirror(dest)
        return self.public_url(rel_path)

    def read_bytes(self, rel_path: str) -> bytes:
        return self._path(rel_path).read_bytes()

    def exists(self, rel_path: str) -> bool:
        return self._path(rel_path).exists()

    def _mirror(self, local_path: Path) -> None:
        if not self.rsync_target:
            return
        rel = local_path.resolve().relative_to(self.root.resolve())
        target = self.rsync_target.rstrip("/") + "/" + str(rel.parent).replace("\\", "/") + "/"
        cmd = ["rsync", "-a", str(local_path), target]
        if self.rsync_port:
            cmd[2:2] = ["-e", f"ssh -p {self.rsync_port} -o StrictHostKeyChecking=no"]
        try:
            subprocess.run(cmd, check=True, timeout=int(os.getenv("MARKETTOOL_VPS_STORAGE_RSYNC_TIMEOUT", "30")))
        except Exception as exc:
            logger.warning("[VPSStorage] rsync mirror failed for %s: %s", rel, exc)


class _VpsBlob:
    def __init__(self, store: VpsJsonStore, rel_path: str):
        self._store = store
        self.name = _clean_rel_path(rel_path)
        self.public_url = store.public_url(self.name)
        self.size: Optional[int] = None

    def upload_from_string(self, data: bytes | str, content_type: str = "application/octet-stream", **_: Any) -> None:
        payload = data.encode("utf-8") if isinstance(data, str) else data
        self._store.write_bytes(self.name, payload)
        self.size = len(payload)

    def upload_from_filename(self, filename: str, **_: Any) -> None:
        self._store.upload_file(filename, self.name)
        self.reload()

    def download_as_text(self, encoding: str = "utf-8", **_: Any) -> str:
        return self._store.read_bytes(self.name).decode(encoding)

    def download_as_bytes(self, **_: Any) -> bytes:
        return self._store.read_bytes(self.name)

    def download_to_filename(self, filename: str, **_: Any) -> None:
        Path(filename).parent.mkdir(parents=True, exist_ok=True)
        Path(filename).write_bytes(self._store.read_bytes(self.name))

    def exists(self, **_: Any) -> bool:
        return self._store.exists(self.name)

    def make_public(self) -> None:
        self.public_url = self._store.public_url(self.name)

    def delete(self) -> None:
        self._store._path(self.name).unlink(missing_ok=True)

    def reload(self) -> None:
        path = self._store._path(self.name)
        self.size = path.stat().st_size if path.exists() else None


class _PgSnapshot:
    def __init__(self, doc_id: str, data: Optional[dict[str, Any]]):
        self.id = doc_id
        self._data = data
        self.exists = data is not None

    def to_dict(self) -> Optional[dict[str, Any]]:
        if self._data is None:
            return None
        return dict(self._data)


class _PgDocumentRef:
    def __init__(self, store: "PostgresDocumentStore", collection: str, doc_id: str):
        self._store = store
        self._collection = collection
        self.id = doc_id

    def get(self) -> _PgSnapshot:
        return _PgSnapshot(self.id, self._store.get_document(self._collection, self.id))

    def set(self, data: dict[str, Any], merge: bool = False) -> None:
        self._store.set_document(self._collection, self.id, data, merge=merge)

    def update(self, data: dict[str, Any]) -> None:
        self._store.update_document(self._collection, self.id, data)

    def delete(self) -> None:
        self._store.delete_document(self._collection, self.id)


class _PgQuery:
    def __init__(self, store: "PostgresDocumentStore", collection: str):
        self._store = store
        self._collection = collection
        self._filters: list[tuple[str, str, Any]] = []
        self._order_by: Optional[str] = None
        self._limit: Optional[int] = None

    def where(self, field: str, op: str, value: Any) -> "_PgQuery":
        clone = self._clone()
        clone._filters.append((field, op, value))
        return clone

    def order_by(self, field: str, **_: Any) -> "_PgQuery":
        clone = self._clone()
        clone._order_by = field
        return clone

    def limit(self, count: int) -> "_PgQuery":
        clone = self._clone()
        clone._limit = int(count)
        return clone

    def stream(self) -> Iterable[_PgSnapshot]:
        rows = self._store.query_documents(self._collection, self._filters, self._order_by, self._limit)
        return [_PgSnapshot(doc_id, data) for doc_id, data in rows]

    def _clone(self) -> "_PgQuery":
        clone = _PgQuery(self._store, self._collection)
        clone._filters = list(self._filters)
        clone._order_by = self._order_by
        clone._limit = self._limit
        return clone


class _PgCollectionRef(_PgQuery):
    def document(self, doc_id: Optional[str] = None) -> _PgDocumentRef:
        return _PgDocumentRef(self._store, self._collection, str(doc_id or uuid.uuid4().hex))

    def add(self, data: dict[str, Any]):
        doc_id = uuid.uuid4().hex
        self._store.set_document(self._collection, doc_id, data, merge=False)
        return self.document(doc_id)


class PostgresDocumentStore:
    def __init__(self, dsn: str, schema: str = "markettool", auto_init: bool = True):
        self.dsn = dsn
        self.schema = schema
        self._ready = False
        if auto_init:
            self.ensure_schema()

    @classmethod
    def from_env(cls) -> Optional["PostgresDocumentStore"]:
        dsn = os.getenv("MARKETTOOL_POSTGRES_DSN") or os.getenv("DATABASE_URL")
        if not dsn:
            return None
        return cls(
            dsn=dsn,
            schema=os.getenv("MARKETTOOL_POSTGRES_SCHEMA", "markettool"),
            auto_init=_env_bool("MARKETTOOL_POSTGRES_AUTO_INIT", True),
        )

    def _connect(self):
        import psycopg

        return psycopg.connect(self.dsn)

    def ensure_schema(self) -> None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(f'CREATE SCHEMA IF NOT EXISTS "{self.schema}"')
            cur.execute(
                f'''
                CREATE TABLE IF NOT EXISTS "{self.schema}".firestore_docs (
                  collection_name text NOT NULL,
                  doc_id text NOT NULL,
                  data jsonb NOT NULL DEFAULT '{{}}'::jsonb,
                  created_at timestamptz NOT NULL DEFAULT now(),
                  updated_at timestamptz NOT NULL DEFAULT now(),
                  PRIMARY KEY (collection_name, doc_id)
                )
                '''
            )
            cur.execute(
                f'CREATE INDEX IF NOT EXISTS firestore_docs_collection_updated_idx '
                f'ON "{self.schema}".firestore_docs (collection_name, updated_at DESC)'
            )
            conn.commit()
        self._ready = True

    def collection(self, name: str) -> _PgCollectionRef:
        return _PgCollectionRef(self, name)

    def batch(self):
        return _PgBatch(self)

    def get_document(self, collection: str, doc_id: str) -> Optional[dict[str, Any]]:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                f'SELECT data FROM "{self.schema}".firestore_docs WHERE collection_name=%s AND doc_id=%s',
                (collection, doc_id),
            )
            row = cur.fetchone()
            return row[0] if row else None

    def set_document(self, collection: str, doc_id: str, data: dict[str, Any], merge: bool = False) -> None:
        clean = self._normalize(data)
        with self._connect() as conn, conn.cursor() as cur:
            if merge:
                cur.execute(
                    f'''
                    INSERT INTO "{self.schema}".firestore_docs(collection_name, doc_id, data)
                    VALUES (%s, %s, %s::jsonb)
                    ON CONFLICT (collection_name, doc_id)
                    DO UPDATE SET data = "{self.schema}".firestore_docs.data || EXCLUDED.data, updated_at = now()
                    ''',
                    (collection, doc_id, json.dumps(clean, ensure_ascii=False)),
                )
            else:
                cur.execute(
                    f'''
                    INSERT INTO "{self.schema}".firestore_docs(collection_name, doc_id, data)
                    VALUES (%s, %s, %s::jsonb)
                    ON CONFLICT (collection_name, doc_id)
                    DO UPDATE SET data = EXCLUDED.data, updated_at = now()
                    ''',
                    (collection, doc_id, json.dumps(clean, ensure_ascii=False)),
                )
            conn.commit()

    def update_document(self, collection: str, doc_id: str, data: dict[str, Any]) -> None:
        current = self.get_document(collection, doc_id) or {}
        for key, value in data.items():
            if value.__class__.__name__ == "Increment":
                delta = int(getattr(value, "_value", 1) or 1)
                current[str(key)] = int(current.get(str(key), 0) or 0) + delta
            else:
                current[str(key)] = self._normalize(value)
        self.set_document(collection, doc_id, current, merge=False)

    def delete_document(self, collection: str, doc_id: str) -> None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                f'DELETE FROM "{self.schema}".firestore_docs WHERE collection_name=%s AND doc_id=%s',
                (collection, doc_id),
            )
            conn.commit()

    def query_documents(
        self,
        collection: str,
        filters: list[tuple[str, str, Any]],
        order_by: Optional[str],
        limit: Optional[int],
    ) -> list[tuple[str, dict[str, Any]]]:
        sql = [f'SELECT doc_id, data FROM "{self.schema}".firestore_docs WHERE collection_name=%s']
        params: list[Any] = [collection]
        for field, op, value in filters:
            if op != "==":
                continue
            sql.append("AND data ->> %s = %s")
            params.extend([field, str(value)])
        if order_by:
            sql.append("ORDER BY data ->> %s DESC NULLS LAST, updated_at DESC")
            params.append(order_by)
        else:
            sql.append("ORDER BY updated_at DESC")
        if limit:
            sql.append("LIMIT %s")
            params.append(limit)
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(" ".join(sql), params)
            return [(row[0], row[1]) for row in cur.fetchall()]

    def collections(self, max_results: int = 1):
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                f'SELECT DISTINCT collection_name FROM "{self.schema}".firestore_docs ORDER BY 1 LIMIT %s',
                (max_results,),
            )
            return [row[0] for row in cur.fetchall()]

    def _normalize(self, value: Any) -> Any:
        if isinstance(value, dict):
            return {str(k): self._normalize(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [self._normalize(v) for v in value]
        if isinstance(value, datetime):
            return value.astimezone(timezone.utc).isoformat()
        if value.__class__.__name__ in {"Sentinel", "_MethodDefault"} or str(value).endswith("SERVER_TIMESTAMP"):
            return datetime.now(timezone.utc).isoformat()
        if value.__class__.__name__ == "Increment":
            return int(getattr(value, "_value", 1) or 1)
        try:
            json.dumps(value)
            return value
        except TypeError:
            return str(value)


class _PgBatch:
    def __init__(self, store: PostgresDocumentStore):
        self._store = store
        self._ops: list[tuple[str, _PgDocumentRef, Optional[dict[str, Any]]]] = []

    def set(self, ref: _PgDocumentRef, data: dict[str, Any], merge: bool = True):
        self._ops.append(("set_merge" if merge else "set", ref, data))

    def delete(self, ref: _PgDocumentRef):
        self._ops.append(("delete", ref, None))

    def commit(self):
        for op, ref, data in self._ops:
            if op == "delete":
                ref.delete()
            else:
                ref.set(data or {}, merge=(op == "set_merge"))
