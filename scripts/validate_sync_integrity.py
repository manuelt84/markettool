#!/usr/bin/env python3
"""Validación de integridad de sincronización Firestore ↔ PostgreSQL.

Compara conteos y checksums entre Firestore y PostgreSQL para detectar inconsistencias.
Ideal para ejecutar después de cada sync vía cron.

Retorna:
- Exit code 0: Todo OK
- Exit code 1: Diferencias encontradas (detalles en stdout)
"""

import argparse
import hashlib
import json
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import psycopg2
from google.cloud import firestore
from google.oauth2 import service_account

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--credentials", default=os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "trading-firestore.json"))
    parser.add_argument("--dsn", default=os.getenv("MARKETTOOL_POSTGRES_DSN"))
    parser.add_argument("--dsn-file", default=os.getenv("MARKETTOOL_POSTGRES_DSN_FILE"))
    parser.add_argument("--schema", default=os.getenv("MARKETTOOL_POSTGRES_SCHEMA", "markettool"))
    parser.add_argument("--project", default=os.getenv("GOOGLE_CLOUD_PROJECT"))
    parser.add_argument("--collections", action="append", default=[], help="Colecciones a validar")
    parser.add_argument("--hours", type=int, default=24, help="Validar docs de últimas N horas")
    parser.add_argument("--tolerance", type=int, default=0, help="Diferencias permitidas antes de fallar")
    parser.add_argument("--verbose", "-v", action="store_true")
    return parser.parse_args()


def read_dsn(args) -> str:
    if args.dsn:
        return args.dsn.strip()
    if args.dsn_file:
        return Path(args.dsn_file).read_text(encoding="utf-8").strip()
    raise SystemExit("MARKETTOOL_POSTGRES_DSN or MARKETTOOL_POSTGRES_DSN_FILE is required")


def get_firestore_client(args) -> firestore.Client:
    credentials_path = Path(args.credentials)
    if not credentials_path.exists():
        raise SystemExit(f"Firestore credentials not found: {credentials_path}")
    credentials = service_account.Credentials.from_service_account_file(str(credentials_path))
    project = args.project or credentials.project_id
    return firestore.Client(project=project, credentials=credentials)


def count_firestore_docs(db: firestore.Client, collection: str) -> int:
    """Contar documentos en una colección de Firestore."""
    try:
        col_ref = db.collection(collection)
        # Usar aggregation query si está disponible, sino contar manualmente
        docs = list(col_ref.select([]).stream())
        return len(docs)
    except Exception as e:
        logger.warning(f"Error counting Firestore docs for {collection}: {e}")
        return -1


def count_postgres_docs(conn, schema: str, collection: str) -> int:
    """Contar documentos en una colección en PostgreSQL."""
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""SELECT COUNT(*) FROM "{schema}".firestore_docs 
                    WHERE collection_name = %s""",
                (collection,)
            )
            result = cur.fetchone()
            return result[0] if result else 0
    except Exception as e:
        logger.warning(f"Error counting PostgreSQL docs for {collection}: {e}")
        return -1


def get_recent_firestore_docs(db: firestore.Client, collection: str, hours: int) -> Dict[str, str]:
    """Obtener hashes de documentos modificados recientemente en Firestore."""
    doc_hashes = {}
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    
    try:
        col_ref = db.collection(collection)
        docs = col_ref.stream()
        
        for doc in docs:
            data = doc.to_dict()
            updated_at = data.get('updated_at')
            
            # Filtrar por fecha de actualización
            if updated_at:
                if isinstance(updated_at, datetime):
                    doc_time = updated_at
                elif isinstance(updated_at, str):
                    try:
                        doc_time = datetime.fromisoformat(updated_at.replace('Z', '+00:00'))
                    except:
                        continue
                else:
                    continue
                
                if doc_time < cutoff:
                    continue
            
            # Calcular hash del contenido
            doc_hash = hashlib.md5(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()
            doc_hashes[doc.id] = doc_hash
            
    except Exception as e:
        logger.warning(f"Error reading Firestore docs for {collection}: {e}")
    
    return doc_hashes


def get_recent_postgres_docs(conn, schema: str, collection: str, hours: int) -> Dict[str, str]:
    """Obtener hashes de documentos modificados recientemente en PostgreSQL."""
    doc_hashes = {}
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""SELECT doc_id, data, updated_at 
                    FROM "{schema}".firestore_docs 
                    WHERE collection_name = %s AND updated_at >= %s""",
                (collection, cutoff.isoformat())
            )
            
            rows = cur.fetchall()
            for doc_id, data, updated_at in rows:
                # Calcular hash del contenido
                doc_hash = hashlib.md5(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()
                doc_hashes[doc_id] = doc_hash
                
    except Exception as e:
        logger.warning(f"Error reading PostgreSQL docs for {collection}: {e}")
    
    return doc_hashes


def validate_collection(
    db: firestore.Client,
    conn,
    schema: str,
    collection: str,
    hours: int = 24,
    verbose: bool = False
) -> Tuple[int, int, List[str]]:
    """
    Validar integridad de una colección.
    
    Retorna: (count_diff, hash_mismatches, errors)
    """
    errors = []
    
    # 1. Comparar conteos totales
    fs_count = count_firestore_docs(db, collection)
    pg_count = count_postgres_docs(conn, schema, collection)
    
    count_diff = abs(fs_count - pg_count) if fs_count >= 0 and pg_count >= 0 else -1
    
    if verbose:
        logger.info(f"  {collection}: FS={fs_count}, PG={pg_count}, diff={count_diff}")
    
    if count_diff > 0:
        errors.append(f"COUNT MISMATCH {collection}: Firestore={fs_count} vs PostgreSQL={pg_count}")
    
    # 2. Comparar hashes de documentos recientes
    fs_docs = get_recent_firestore_docs(db, collection, hours)
    pg_docs = get_recent_postgres_docs(conn, schema, collection, hours)
    
    hash_mismatches = 0
    
    # Verificar docs que están en Firestore pero no en PostgreSQL
    for doc_id, fs_hash in fs_docs.items():
        if doc_id not in pg_docs:
            hash_mismatches += 1
            if verbose:
                errors.append(f"MISSING IN PG: {collection}/{doc_id}")
        elif fs_hash != pg_docs[doc_id]:
            hash_mismatches += 1
            if verbose:
                errors.append(f"HASH MISMATCH: {collection}/{doc_id}")
    
    # Verificar docs que están en PostgreSQL pero no en Firestore
    for doc_id in pg_docs.keys():
        if doc_id not in fs_docs:
            hash_mismatches += 1
            if verbose:
                errors.append(f"MISSING IN FS: {collection}/{doc_id}")
    
    if verbose and hash_mismatches > 0:
        logger.info(f"  {collection}: {hash_mismatches} hash mismatches")
    
    return count_diff, hash_mismatches, errors


def main() -> int:
    args = parse_args()
    
    if not args.collections:
        # Usar colecciones por defecto (las mismas que sync_firestore_incremental.py)
        args.collections = [
            "ejecuciones", "user_ids", "monitoreos", 
            "suscripciones_user", "iap_tokens", "user_states",
            "indicators_metadata", "historicos_metadata"
        ]
    
    logger.info(f"=== Validación de Integridad ===")
    logger.info(f"Colecciones a validar: {len(args.collections)}")
    logger.info(f"Ventana de tiempo: últimas {args.hours} horas")
    logger.info(f"Tolerancia: {args.tolerance} diferencias")
    logger.info()
    
    # Conectar a servicios
    try:
        db = get_firestore_client(args)
        dsn = read_dsn(args)
        conn = psycopg2.connect(dsn)
        logger.info("Conexiones establecidas: Firestore + PostgreSQL")
    except Exception as e:
        logger.error(f"Error conectando: {e}")
        return 1
    
    # Validar cada colección
    total_count_diff = 0
    total_hash_mismatches = 0
    all_errors = []
    
    for collection in args.collections:
        logger.info(f"Validando {collection}...")
        
        try:
            count_diff, hash_mismatches, errors = validate_collection(
                db, conn, args.schema, collection, args.hours, args.verbose
            )
            
            total_count_diff += count_diff if count_diff > 0 else 0
            total_hash_mismatches += hash_mismatches
            all_errors.extend(errors)
            
        except Exception as e:
            logger.error(f"Error validando {collection}: {e}")
            all_errors.append(f"ERROR VALIDANDO {collection}: {e}")
    
    # Reportar resultados
    logger.info()
    logger.info("=== Resultados ===")
    logger.info(f"Diferencias de conteo: {total_count_diff}")
    logger.info(f"Mismatches de hash: {total_hash_mismatches}")
    logger.info(f"Total errores: {len(all_errors)}")
    
    if all_errors and args.verbose:
        logger.info()
        logger.info("=== Errores Detallados ===")
        for error in all_errors[:20]:  # Mostrar máximo 20 errores
            logger.warning(f"  - {error}")
        if len(all_errors) > 20:
            logger.warning(f"  ... y {len(all_errors) - 20} más")
    
    # Determinar si pasó o falló
    total_issues = total_count_diff + total_hash_mismatches
    
    if total_issues <= args.tolerance:
        logger.info()
        logger.info("✅ VALIDACIÓN EXITOSA")
        return 0
    else:
        logger.info()
        logger.error(f"❌ VALIDACIÓN FALLÓ ({total_issues} issues > {args.tolerance} tolerance)")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
