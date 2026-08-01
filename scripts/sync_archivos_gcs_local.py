#!/usr/bin/env python3
"""Sincronización bidireccional de archivos: Local ↔ GCS Bucket.

Este script sincroniza archivos generados por MarketTool entre:
- Directorio local: /home/mtoro/projects/localnginx_balancer/maquina-a/storage/markettool-json/
- Google Cloud Storage: gs://markettool_bucket/archivos_generados/

Características:
- Bidireccional: sube archivos nuevos y descarga faltantes
- Usa checksums MD5 para detectar cambios
- Actualiza metadata en PostgreSQL con URLs de GCS
- Opcional: elimina archivos locales antiguos para ahorrar espacio
"""

import argparse
import hashlib
import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    from google.cloud import storage
    from google.oauth2 import service_account
except ImportError:
    print("ERROR: google-cloud-storage not installed. Run: pip install google-cloud-storage")
    sys.exit(1)

try:
    import psycopg2
except ImportError:
    print("ERROR: psycopg2 not installed. Run: pip install psycopg2-binary")
    sys.exit(1)


logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)


# Configuración
DEFAULT_LOCAL_DIR = Path.home() / "projects/localnginx_balancer/maquina-a/storage/markettool-json"
DEFAULT_BUCKET = os.getenv("GCS_BUCKET_NAME", "markettool_bucket")
DEFAULT_CREDENTIALS = Path.home() / ".openclaw/workspace/trading-firestore.json"
POSTGRES_DSN_FILE = "/run/secrets/markettool_postgres_dsn"  # Para VPS
STATE_FILE = Path.home() / ".openclaw/workspace/gcs_local_sync_state.json"


def get_gcs_client(credentials_path: Path) -> storage.Client:
    """Inicializar cliente de GCS."""
    if not credentials_path.exists():
        raise FileNotFoundError(f"Credenciales no encontradas: {credentials_path}")
    
    credentials = service_account.Credentials.from_service_account_file(str(credentials_path))
    return storage.Client(credentials=credentials)


def get_bucket(client: storage.Client, bucket_name: str) -> storage.Bucket:
    """Obtener referencia al bucket."""
    return client.bucket(bucket_name)


def calculate_md5(filepath: Path) -> str:
    """Calcular MD5 de un archivo local."""
    hash_md5 = hashlib.md5()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            hash_md5.update(chunk)
    return hash_md5.hexdigest()


def get_local_files(local_dir: Path, max_age_hours: int = 168) -> Dict[str, Dict[str, Any]]:
    """Obtener lista de archivos locales con metadata."""
    files = {}
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
    
    if not local_dir.exists():
        logger.warning(f"Directorio local no existe: {local_dir}")
        return files
    
    for filepath in local_dir.rglob("*"):
        if filepath.is_file():
            try:
                stat = filepath.stat()
                mtime = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
                
                if mtime < cutoff:
                    continue
                
                rel_path = str(filepath.relative_to(local_dir))
                files[rel_path] = {
                    "path": str(filepath),
                    "size": stat.st_size,
                    "md5": calculate_md5(filepath),
                    "mtime": mtime.isoformat(),
                }
            except Exception as e:
                logger.warning(f"Error leyendo {filepath}: {e}")
    
    return files


def get_gcs_files(bucket: storage.Bucket, prefix: str = "archivos_generados/") -> Dict[str, Dict[str, Any]]:
    """Obtener lista de archivos en GCS con metadata."""
    files = {}
    
    try:
        blobs = bucket.list_blobs(prefix=prefix)
        for blob in blobs:
            if blob.name.endswith('/'):
                continue
            
            rel_path = blob.name[len(prefix):] if blob.name.startswith(prefix) else blob.name
            files[rel_path] = {
                "name": blob.name,
                "size": blob.size,
                "md5": blob.md5_hash,
                "updated": blob.updated.isoformat() if blob.updated else None,
            }
    except Exception as e:
        logger.warning(f"Error listando blobs en GCS: {e}")
    
    return files


def upload_to_gcs(bucket: storage.Bucket, local_path: Path, gcs_path: str, dry_run: bool = False) -> bool:
    """Subir archivo a GCS."""
    if dry_run:
        logger.info(f"[DRY-RUN] UPLOAD: {local_path} → gs://{bucket.name}/{gcs_path}")
        return True
    
    try:
        blob = bucket.blob(gcs_path)
        blob.upload_from_filename(str(local_path))
        
        # Hacer el blob público (opcional, configurable)
        if os.getenv("GCS_MAKE_PUBLIC", "true").lower() == "true":
            blob.make_public()
        
        logger.info(f"UPLOADED: {local_path} → gs://{bucket.name}/{gcs_path}")
        return True
    except Exception as e:
        logger.error(f"ERROR uploading {local_path}: {e}")
        return False


def download_from_gcs(bucket: storage.Bucket, gcs_path: str, local_path: Path, dry_run: bool = False) -> bool:
    """Descargar archivo desde GCS."""
    if dry_run:
        logger.info(f"[DRY-RUN] DOWNLOAD: gs://{bucket.name}/{gcs_path} → {local_path}")
        return True
    
    try:
        local_path.parent.mkdir(parents=True, exist_ok=True)
        
        blob = bucket.blob(gcs_path)
        blob.download_to_filename(str(local_path))
        logger.info(f"DOWNLOADED: gs://{bucket.name}/{gcs_path} → {local_path}")
        return True
    except Exception as e:
        logger.error(f"ERROR downloading {gcs_path}: {e}")
        return False


def update_postgres_metadata(rel_path: str, gcs_url: str, dsn: str) -> bool:
    """Actualizar metadata en PostgreSQL con URL de GCS."""
    try:
        conn = psycopg2.connect(dsn)
        with conn.cursor() as cur:
            # Buscar documento en firestore_docs
            cur.execute(
                """SELECT doc_id, data FROM markettool.firestore_docs 
                   WHERE collection_name = 'archivos_generados' AND data->>'storage_path' = %s""",
                (rel_path,)
            )
            row = cur.fetchone()
            
            if row:
                doc_id, data = row
                # Agregar URL de GCS al JSON
                data['gcs_url'] = gcs_url
                data['synced_to_gcs'] = datetime.now(timezone.utc).isoformat()
                
                cur.execute(
                    """UPDATE markettool.firestore_docs 
                       SET data = %s, updated_at = NOW()
                       WHERE doc_id = %s""",
                    (json.dumps(data), doc_id)
                )
                conn.commit()
                logger.debug(f"Updated PG metadata for {rel_path}")
                return True
            else:
                logger.debug(f"No PG metadata found for {rel_path}")
                return False
                
    except Exception as e:
        logger.warning(f"Error updating PG metadata: {e}")
        return False
    finally:
        if 'conn' in locals():
            conn.close()


def read_postgres_dsn() -> str:
    """Leer DSN de PostgreSQL desde archivo o variable de entorno."""
    dsn = os.getenv("MARKETTOOL_POSTGRES_DSN")
    if dsn:
        return dsn.strip()
    
    dsn_file = os.getenv("MARKETTOOL_POSTGRES_DSN_FILE", POSTGRES_DSN_FILE)
    if dsn_file and Path(dsn_file).exists():
        return Path(dsn_file).read_text().strip()
    
    # Fallback: construir DSN para conexión VPN local
    return "postgresql://markettool:mt_r75iut75ddrq0vykbah3pb@10.8.0.1:5432/markettool"


def sync(
    local_dir: Path,
    bucket: storage.Bucket,
    direction: str = "both",
    max_age_hours: int = 168,
    delete_local_after_upload: bool = False,
    dry_run: bool = False,
) -> Tuple[int, int, int]:
    """Sincronizar archivos entre local y GCS."""
    prefix = "archivos_generados/"
    uploaded = 0
    downloaded = 0
    skipped = 0
    
    logger.info(f"=== Sincronización {'DRY-RUN' if dry_run else ''} ===")
    logger.info(f"Dirección: {direction}")
    logger.info(f"Local: {local_dir}")
    logger.info(f"GCS: gs://{bucket.name}/{prefix}")
    logger.info(f"Max age: {max_age_hours} horas")
    logger.info(f"Delete after upload: {delete_local_after_upload}")
    logger.info("")
    
    # Obtener listas de archivos
    local_files = get_local_files(local_dir, max_age_hours)
    gcs_files = get_gcs_files(bucket, prefix)
    
    logger.info(f"Archivos locales (últimas {max_age_hours}h): {len(local_files)}")
    logger.info(f"Archivos en GCS: {len(gcs_files)}")
    logger.info("")
    
    # Leer DSN de PostgreSQL
    pg_dsn = None
    try:
        pg_dsn = read_postgres_dsn()
        logger.info(f"PostgreSQL: conectado (DSN configurado)")
    except Exception as e:
        logger.warning(f"No se pudo conectar a PostgreSQL: {e}")
        logger.info("Continuando sin actualizar metadata en PG")
    
    # Subir archivos nuevos/modificados desde local a GCS
    if direction in ("upload", "both"):
        logger.info("--- Subiendo archivos a GCS ---")
        for rel_path, info in local_files.items():
            gcs_path = f"{prefix}{rel_path}"
            
            if rel_path in gcs_files:
                gcs_info = gcs_files[rel_path]
                if info["md5"] == gcs_info.get("md5"):
                    skipped += 1
                    continue
            
            if upload_to_gcs(bucket, info["path"], gcs_path, dry_run):
                uploaded += 1
                
                # Actualizar metadata en PostgreSQL
                if not dry_run and pg_dsn:
                    gcs_url = f"https://storage.googleapis.com/{bucket.name}/{gcs_path}"
                    update_postgres_metadata(rel_path, gcs_url, pg_dsn)
                
                # Eliminar archivo local si se configura
                if delete_local_after_upload and not dry_run:
                    try:
                        info["path"] = Path(info["path"])
                        info["path"].unlink()
                        logger.debug(f"Deleted local file: {rel_path}")
                    except Exception as e:
                        logger.warning(f"Failed to delete {rel_path}: {e}")
        
        logger.info(f"Subidos: {uploaded}, Saltados: {skipped}")
        logger.info("")
    
    # Descargar archivos faltantes desde GCS a local
    if direction in ("download", "both"):
        downloaded = 0
        logger.info("--- Descargando archivos desde GCS ---")
        for rel_path, info in gcs_files.items():
            local_path = local_dir / rel_path
            
            if rel_path in local_files:
                local_info = local_files[rel_path]
                if info.get("md5") == local_info.get("md5"):
                    continue
            
            if download_from_gcs(bucket, info["name"], local_path, dry_run):
                downloaded += 1
        
        logger.info(f"Descargados: {downloaded}")
        logger.info("")
    
    return uploaded, downloaded, skipped


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--local-dir", default=str(DEFAULT_LOCAL_DIR), help="Directorio local")
    parser.add_argument("--bucket", default=DEFAULT_BUCKET, help="Nombre del bucket GCS")
    parser.add_argument("--credentials", default=str(DEFAULT_CREDENTIALS), help="Credenciales GCS JSON")
    parser.add_argument("--direction", choices=["upload", "download", "both"], default="both",
                       help="Dirección de sincronización")
    parser.add_argument("--hours", type=int, default=168, help="Sincronizar archivos de las últimas N horas")
    parser.add_argument("--delete-after-upload", action="store_true", 
                       help="Eliminar archivos locales después de subir (ahorra espacio)")
    parser.add_argument("--dry-run", action="store_true", help="Solo mostrar, no ejecutar")
    parser.add_argument("--verbose", "-v", action="store_true", help="Output detallado")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    
    local_dir = Path(args.local_dir)
    
    try:
        # Inicializar cliente GCS
        client = get_gcs_client(Path(args.credentials))
        bucket = get_bucket(client, args.bucket)
        
        # Ejecutar sincronización
        uploaded, downloaded, skipped = sync(
            local_dir=local_dir,
            bucket=bucket,
            direction=args.direction,
            max_age_hours=args.hours,
            delete_local_after_upload=args.delete_after_upload,
            dry_run=args.dry_run,
        )
        
        logger.info("")
        logger.info("=== Resumen ===")
        logger.info(f"Subidos: {uploaded}")
        logger.info(f"Descargados: {downloaded}")
        logger.info(f"Saltados (ya sincronizados): {skipped}")
        
        if not args.dry_run:
            # Guardar estado
            state = {
                "last_sync": datetime.now(timezone.utc).isoformat(),
                "last_upload_count": uploaded,
                "last_download_count": downloaded,
            }
            STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            STATE_FILE.write_text(json.dumps(state, indent=2))
            logger.info(f"Estado guardado en {STATE_FILE}")
        
        return 0
        
    except FileNotFoundError as e:
        logger.error(str(e))
        return 1
    except Exception as e:
        logger.exception(f"Error inesperado: {e}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
