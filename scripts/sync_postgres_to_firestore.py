#!/usr/bin/env python3
"""Sincronización inversa: PostgreSQL → Firestore.

Este script lee documentos desde PostgreSQL y los inserta/actualiza en Firestore.
Útil para datos generados en el backend que deben estar disponibles en Firestore.
"""

import argparse
import base64
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import psycopg2
from google.api_core import exceptions as google_exceptions
from google.cloud import firestore
from google.oauth2 import service_account


# Tablas/c colecciones a sincronizar por defecto
DEFAULT_COLLECTIONS = [
    "backtest_results",
    "configuraciones",
    "logs_sistema",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--credentials",
        default=os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "/root/markettool/trading-firestore.json"),
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


def should_retry_firestore_error(exc: BaseException) -> bool:
    from google.api_core import exceptions as google_exceptions
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


def fetch_from_postgres(
    conn: psycopg2.extensions.connection,
    schema: str,
    collection: str,
    since: datetime,
    verbose: bool = False,
) -> List[Dict[str, Any]]:
    """Obtener documentos desde PostgreSQL para una colección dada."""
    docs = []
    
    with conn.cursor() as cur:
        # Verificar si la tabla existe
        cur.execute(
            f"""SELECT EXISTS (
                SELECT FROM information_schema.tables 
                WHERE table_schema = %s AND table_name = %s
            )""",
            (schema, collection)
        )
        exists = cur.fetchone()[0]
        
        if not exists:
            if verbose:
                print(f"SKIP\t{collection}: tabla no existe en PostgreSQL", flush=True)
            return []
        
        # Obtener documentos modificados desde 'since'
        cur.execute(
            f"""SELECT doc_id, data, created_at, updated_at 
                FROM "{schema}".firestore_docs 
                WHERE collection_name = %s AND updated_at >= %s
                ORDER BY updated_at""",
            (collection, since)
        )
        
        rows = cur.fetchall()
        for row in rows:
            doc_id, data, created_at, updated_at = row
            if verbose:
                print(f"READ\t{collection}/{doc_id}\tupdated:{updated_at}", flush=True)
            docs.append({
                "id": doc_id,
                "data": data,
                "created_at": created_at,
                "updated_at": updated_at,
            })
    
    return docs


def write_to_firestore(
    db: firestore.Client,
    collection: str,
    docs: List[Dict[str, Any]],
    dry_run: bool = False,
    verbose: bool = False,
) -> int:
    """Escribir documentos en Firestore con retries."""
    if not docs:
        return 0
    
    written = 0
    collection_ref = db.collection(collection)
    
    for doc_info in docs:
        doc_ref = collection_ref.document(doc_info["id"])
        
        if dry_run:
            if verbose:
                print(f"DRY-RUN\t{collection}/{doc_info['id']}", flush=True)
            written += 1
            continue
        
        # Intentar escribir con retry
        delay_seconds = 1.0
        for attempt in range(1, 6):
            try:
                doc_ref.set(doc_info["data"], merge=True)
                if verbose:
                    print(f"WRITE\t{collection}/{doc_info['id']}", flush=True)
                written += 1
                break
            except Exception as exc:
                if attempt >= 5 or not should_retry_firestore_error(exc):
                    print(f"ERROR\t{collection}/{doc_info['id']}: {exc}", flush=True)
                    break
                print(f"WARN\tretrying {collection}/{doc_info['id']} after {exc.__class__.__name__}", flush=True)
                import time
                time.sleep(delay_seconds)
                delay_seconds = min(delay_seconds * 2, 30.0)
    
    return written


def main() -> int:
    args = parse_args()
    
    collections = args.collections if args.collections else DEFAULT_COLLECTIONS
    since = datetime.now(timezone.utc).replace(microsecond=0)
    
    print(f"=== PostgreSQL → Firestore Sync ===", flush=True)
    print(f"Collections: {', '.join(collections)}", flush=True)
    print(f"Since: {since.isoformat()} (últimas {args.hours} horas)", flush=True)
    print(f"Dry run: {args.dry_run}", flush=True)
    print()
    
    # Conectar a PostgreSQL
    conn = psycopg2.connect(read_dsn(args))
    
    # Conectar a Firestore
    db = firestore_client(args)
    
    total_docs = 0
    total_written = 0
    
    try:
        for collection in collections:
            # Leer desde PostgreSQL
            docs = fetch_from_postgres(
                conn,
                args.schema,
                collection,
                since,
                verbose=args.verbose,
            )
            
            if not docs:
                continue
            
            total_docs += len(docs)
            
            # Escribir en Firestore
            written = write_to_firestore(
                db,
                collection,
                docs,
                dry_run=args.dry_run,
                verbose=args.verbose,
            )
            
            total_written += written
    
    finally:
        conn.close()
    
    print()
    print(f"=== Summary ===", flush=True)
    print(f"Total documents read from PostgreSQL: {total_docs}", flush=True)
    print(f"Total documents written to Firestore: {total_written}", flush=True)
    if not args.dry_run:
        print(f"Status: ✅ Sync completed", flush=True)
    else:
        print(f"Status: ⚠️  Dry run (no writes)", flush=True)
    
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
