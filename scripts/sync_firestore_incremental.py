#!/usr/bin/env python3
"""Sincronización incremental desde Firestore a PostgreSQL.

Este script sincroniza solo los documentos nuevos o modificados desde las últimas N horas.
Ideal para ejecutar periódicamente vía cron.
"""

from __future__ import annotations

import argparse
import base64
import json
import math
import os
import time as time_module
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import psycopg
from google.api_core import exceptions as google_exceptions
from google.cloud import firestore
from google.oauth2 import service_account


# Colecciones críticas a sincronizar por defecto
DEFAULT_COLLECTIONS = [
    "ejecuciones",
    "user_ids",
    "monitoreos",
    "suscripciones_user",
    "iap_tokens",
    "user_states",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--credentials",
        default=os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "trading-firestore.json"),
        help="Path to GCP service account JSON"
    )
    parser.add_argument("--dsn", default=os.getenv("MARKETTOOL_POSTGRES_DSN"))
    parser.add_argument("--dsn-file", default=os.getenv("MARKETTOOL_POSTGRES_DSN_FILE"))
    parser.add_argument("--schema", default=os.getenv("MARKETTOOL_POSTGRES_SCHEMA", "markettool"))
    parser.add_argument("--project", default=os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("GCP_PROJECT"))
    parser.add_argument(
        "--collections",
        action="append",
        default=[],
        help="Colecciones a sincronizar. Por defecto: " + ", ".join(DEFAULT_COLLECTIONS)
    )
    parser.add_argument(
        "--hours",
        type=int,
        default=24,
        help="Sincronizar documentos modificados en las últimas N horas (default: 24)"
    )
    parser.add_argument("--dry-run", action="store_true", help="Solo mostrar, no escribir")
    parser.add_argument("--batch-size", type=int, default=100, help="Documentos por batch")
    parser.add_argument("--page-size", type=int, default=500, help="Documentos por página Firestore")
    parser.add_argument("--retries", type=int, default=5, help="Reintentos por página")
    parser.add_argument("--verbose", "-v", action="store_true", help="Output detallado")
    return parser.parse_args()


def read_dsn(args: argparse.Namespace) -> str:
    if args.dsn:
        return args.dsn.strip()
    if args.dsn_file:
        return Path(args.dsn_file).read_text(encoding="utf-8").strip()
    raise SystemExit("MARKETTOOL_POSTGRES_DSN or MARKETTOOL_POSTGRES_DSN_FILE is required")


def firestore_client(args: argparse.Namespace) -> firestore.Client:
    credentials_path = Path(args.credentials)
    if not credentials_path.exists():
        raise SystemExit(f"Firestore credentials not found: {credentials_path}")
    credentials = service_account.Credentials.from_service_account_file(str(credentials_path))
    project = args.project or credentials.project_id
    return firestore.Client(project=project, credentials=credentials)


def json_safe(value: Any) -> Any:
    """Convertir valores Firestore a formato JSON-safe."""
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_safe(item) for item in value]
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, (date, time)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, bytes):
        return {"__bytes_b64__": base64.b64encode(value).decode("ascii")}
    if value.__class__.__name__ == "DocumentReference":
        return getattr(value, "path", str(value))
    if value.__class__.__name__ == "GeoPoint":
        return {"latitude": value.latitude, "longitude": value.longitude}
    try:
        json.dumps(value)
        return value
    except TypeError:
        return str(value)


def should_retry_firestore_error(exc: BaseException) -> bool:
    retryable = (
        google_exceptions.DeadlineExceeded,
        google_exceptions.ResourceExhausted,
        google_exceptions.ServiceUnavailable,
        google_exceptions.TooManyRequests,
    )
    if isinstance(exc, retryable):
        return True
    message = str(exc).lower()
    return "query timed out" in message or "quota exceeded" in message or "temporarily unavailable" in message


def get_last_sync_times(conn: psycopg.Connection, schema: str, collections: List[str]) -> Dict[str, datetime]:
    """Obtener la última fecha de sync para cada colección."""
    result = {}
    with conn.cursor() as cur:
        for collection in collections:
            cur.execute(
                f'''SELECT MAX(updated_at) FROM "{schema}".firestore_docs 
                    WHERE collection_name = %s''',
                (collection,)
            )
            row = cur.fetchone()
            last_sync = row[0] if row and row[0] else None
            # Si no hay sync previo, usar fecha muy antigua
            result[collection] = last_sync or datetime(2000, 1, 1, tzinfo=timezone.utc)
    return result


def stream_page(query: Any, retries: int) -> List[Any]:
    delay_seconds = 1.0
    for attempt in range(1, retries + 1):
        try:
            return list(query.stream())
        except Exception as exc:
            if attempt >= retries or not should_retry_firestore_error(exc):
                raise
            print(f"WARN retrying Firestore page after {exc.__class__.__name__}: {exc}", flush=True)
            time_module.sleep(delay_seconds)
            delay_seconds = min(delay_seconds * 2, 30.0)
    return []


def fetch_incremental_docs(
    db: firestore.Client,
    collection_name: str,
    since: datetime,
    page_size: int,
    retries: int,
    verbose: bool = False,
) -> List[Tuple[str, str, Dict[str, Any]]]:
    """Obtener documentos modificados desde 'since'."""
    docs = []
    collection_ref = db.collection(collection_name)
    
    # Query ordenado por __name__ para paginación estable
    query = collection_ref.order_by("__name__").limit(page_size)
    
    last_snapshot = None
    total_fetched = 0
    
    while True:
        if last_snapshot is not None:
            query = query.start_after(last_snapshot)
        
        snapshots = stream_page(query, retries)
        if not snapshots:
            break
        
        for snapshot in snapshots:
            last_snapshot = snapshot
            
            # Verificar si el documento fue modificado después del último sync
            data = snapshot.to_dict() or {}
            updated_at = None
            
            # Buscar campo de timestamp en el documento
            for field in ["updated_at", "created_at", "_timestamp"]:
                if field in data:
                    ts_value = data[field]
                    if hasattr(ts_value, "toDate"):
                        # Firestore Timestamp
                        updated_at = ts_value.toDate().replace(tzinfo=timezone.utc)
                    elif hasattr(ts_value, "seconds"):
                        # Unix timestamp
                        updated_at = datetime.fromtimestamp(ts_value.seconds, tz=timezone.utc)
                    elif isinstance(ts_value, str):
                        try:
                            updated_at = datetime.fromisoformat(ts_value.replace("Z", "+00:00"))
                        except:
                            pass
                    break
            
            # Si no hay timestamp, incluir siempre (caso conservador)
            if updated_at is None or updated_at >= since:
                doc_path = f"{collection_name}/{snapshot.id}"
                if verbose:
                    print(f"READ\t{doc_path}\t{'NEW' if updated_at is None else f'UPDATED:{updated_at}'}", flush=True)
                docs.append((collection_name, snapshot.id, json_safe(data)))
                total_fetched += 1
                
                # Recursivamente buscar sub-colecciones
                for child_collection in snapshot.reference.collections():
                    child_docs = fetch_incremental_docs_from_ref(
                        child_collection,
                        since,
                        page_size,
                        retries,
                        verbose
                    )
                    docs.extend(child_docs)
        
        if len(snapshots) < page_size:
            break
    
    if verbose:
        print(f"Collection '{collection_name}': fetched {total_fetched} docs since {since}", flush=True)
    
    return docs


def fetch_incremental_docs_from_ref(
    collection_ref: Any,
    since: datetime,
    page_size: int,
    retries: int,
    verbose: bool = False,
) -> List[Tuple[str, str, Dict[str, Any]]]:
    """Fetch docs from a collection reference (for nested collections)."""
    docs = []
    # Obtener el path de la colección correctamente
    collection_name = str(collection_ref._path)
    
    query = collection_ref.order_by("__name__").limit(page_size)
    last_snapshot = None
    
    while True:
        if last_snapshot is not None:
            query = query.start_after(last_snapshot)
        
        snapshots = stream_page(query, retries)
        if not snapshots:
            break
        
        for snapshot in snapshots:
            last_snapshot = snapshot
            data = snapshot.to_dict() or {}
            doc_path = f"{collection_name}/{snapshot.id}"
            
            if verbose:
                print(f"READ\t{doc_path}", flush=True)
            
            docs.append((collection_name, snapshot.id, json_safe(data)))
            
            # Sub-colecciones
            for child in snapshot.reference.collections():
                child_docs = fetch_incremental_docs_from_ref(child, since, page_size, retries, verbose)
                docs.extend(child_docs)
        
        if len(snapshots) < page_size:
            break
    
    return docs


def ensure_schema(conn: psycopg.Connection, schema: str) -> None:
    with conn.cursor() as cur:
        cur.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
        cur.execute(
            f'''
            CREATE TABLE IF NOT EXISTS "{schema}".firestore_docs (
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
            f'ON "{schema}".firestore_docs (collection_name, updated_at DESC)'
        )
        cur.execute(
            f'CREATE INDEX IF NOT EXISTS firestore_docs_data_gin_idx '
            f'ON "{schema}".firestore_docs USING gin (data)'
        )
    conn.commit()


def flush(conn: psycopg.Connection, schema: str, rows: List[Tuple[str, str, Dict[str, Any]]]) -> int:
    if not rows:
        return 0
    
    inserted = 0
    with conn.cursor() as cur:
        cur.executemany(
            f'''
            INSERT INTO "{schema}".firestore_docs(collection_name, doc_id, data)
            VALUES (%s, %s, %s::jsonb)
            ON CONFLICT (collection_name, doc_id)
            DO UPDATE SET data = EXCLUDED.data, updated_at = now()
            ''',
            [(collection, doc_id, json.dumps(data, ensure_ascii=False)) for collection, doc_id, data in rows],
        )
        inserted = cur.rowcount if cur.rowcount else len(rows)
    conn.commit()
    return inserted


def main() -> int:
    args = parse_args()
    
    collections = args.collections if args.collections else DEFAULT_COLLECTIONS
    since = datetime.now(timezone.utc) - timedelta(hours=args.hours)
    
    print(f"=== Firestore Incremental Sync ===", flush=True)
    print(f"Collections: {', '.join(collections)}", flush=True)
    print(f"Since: {since.isoformat()} ({args.hours} hours ago)", flush=True)
    print(f"Dry run: {args.dry_run}", flush=True)
    print()
    
    db = firestore_client(args)
    conn = None if args.dry_run else psycopg.connect(read_dsn(args))
    
    if conn is not None:
        ensure_schema(conn, args.schema)
        last_syncs = get_last_sync_times(conn, args.schema, collections)
        
        # Usar el máximo entre --hours y el último sync real
        effective_since = {}
        for coll in collections:
            last_sync = last_syncs.get(coll, since)
            effective_since[coll] = max(last_sync, since)
            print(f"Collection '{coll}': last sync={last_sync}, syncing since={effective_since[coll]}", flush=True)
    else:
        effective_since = {coll: since for coll in collections}
    
    total_docs = 0
    batch: List[Tuple[str, str, Dict[str, Any]]] = []
    
    try:
        for collection in collections:
            since_time = effective_since[collection]
            docs = fetch_incremental_docs(
                db,
                collection,
                since_time,
                page_size=args.page_size,
                retries=args.retries,
                verbose=args.verbose,
            )
            
            for doc_tuple in docs:
                batch.append(doc_tuple)
                total_docs += 1
                
                if len(batch) >= args.batch_size and conn is not None:
                    inserted = flush(conn, args.schema, batch)
                    print(f"Flushed {inserted} docs to DB", flush=True)
                    batch.clear()
    
    finally:
        if conn is not None and batch:
            inserted = flush(conn, args.schema, batch)
            print(f"Final flush: {inserted} docs", flush=True)
            conn.close()
    
    print()
    print(f"=== Summary ===", flush=True)
    print(f"Total documents fetched: {total_docs}", flush=True)
    if not args.dry_run:
        print(f"Status: ✅ Sync completed", flush=True)
    else:
        print(f"Status: ⚠️  Dry run (no writes)", flush=True)
    
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
