#!/usr/bin/env python3
"""Copy Firestore documents into the PostgreSQL Firestore-compatible store."""

from __future__ import annotations

import argparse
import base64
import json
import math
import os
import time as time_module
from datetime import date, datetime, time, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable

import psycopg
from google.api_core import exceptions as google_exceptions
from google.cloud import firestore
from google.oauth2 import service_account


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--credentials", default=os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "trading-firestore.json"))
    parser.add_argument("--dsn", default=os.getenv("MARKETTOOL_POSTGRES_DSN"))
    parser.add_argument("--dsn-file", default=os.getenv("MARKETTOOL_POSTGRES_DSN_FILE"))
    parser.add_argument("--schema", default=os.getenv("MARKETTOOL_POSTGRES_SCHEMA", "markettool"))
    parser.add_argument("--project", default=os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("GCP_PROJECT"))
    parser.add_argument("--include", action="append", default=[], help="Root collection to include. Repeatable.")
    parser.add_argument("--exclude", action="append", default=[], help="Root collection to exclude. Repeatable.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--batch-size", type=int, default=200)
    parser.add_argument("--page-size", type=int, default=200, help="Firestore documents to read per paged query.")
    parser.add_argument("--retries", type=int, default=5, help="Retries per Firestore page before failing.")
    parser.add_argument("--limit", type=int, default=0, help="Stop after N documents, for smoke tests.")
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


def stream_page(query: Any, retries: int) -> list[Any]:
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


def walk_collection(
    collection_ref: Any,
    root: str,
    *,
    page_size: int,
    retries: int,
) -> Iterable[tuple[str, str, dict[str, Any]]]:
    last_snapshot = None
    while True:
        query = collection_ref.order_by("__name__").limit(page_size)
        if last_snapshot is not None:
            query = query.start_after(last_snapshot)
        snapshots = stream_page(query, retries)
        if not snapshots:
            break
        for snapshot in snapshots:
            last_snapshot = snapshot
            print(f"READ\t{root}\t{snapshot.id}", flush=True)
            if snapshot.exists:
                yield root, snapshot.id, json_safe(snapshot.to_dict() or {})
            for child in snapshot.reference.collections():
                yield from walk_collection(
                    child,
                    f"{snapshot.reference.path}/{child.id}",
                    page_size=page_size,
                    retries=retries,
                )
        if len(snapshots) < page_size:
            break


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


def flush(conn: psycopg.Connection, schema: str, rows: list[tuple[str, str, dict[str, Any]]]) -> None:
    if not rows:
        return
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
    conn.commit()


def main() -> int:
    args = parse_args()
    include = set(args.include)
    exclude = set(args.exclude)
    db = firestore_client(args)
    if include:
        root_collections = [db.collection(name) for name in sorted(include) if name not in exclude]
    else:
        root_collections = [collection for collection in db.collections() if collection.id not in exclude]

    counts: dict[str, int] = {}
    total = 0
    batch: list[tuple[str, str, dict[str, Any]]] = []

    conn = None if args.dry_run else psycopg.connect(read_dsn(args))
    if conn is not None:
        ensure_schema(conn, args.schema)

    try:
        for collection in root_collections:
            for collection_path, doc_id, data in walk_collection(
                collection,
                collection.id,
                page_size=args.page_size,
                retries=args.retries,
            ):
                counts[collection_path] = counts.get(collection_path, 0) + 1
                total += 1
                if conn is not None:
                    batch.append((collection_path, doc_id, data))
                    if len(batch) >= args.batch_size:
                        flush(conn, args.schema, batch)
                        batch.clear()
                if args.limit and total >= args.limit:
                    break
            if args.limit and total >= args.limit:
                break
        if conn is not None:
            flush(conn, args.schema, batch)
    finally:
        if conn is not None:
            conn.close()

    for collection_path, count in sorted(counts.items()):
        print(f"{collection_path}\t{count}")
    print(f"TOTAL\t{total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
