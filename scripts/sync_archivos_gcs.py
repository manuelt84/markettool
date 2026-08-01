#!/usr/bin/env python3
"""Sincronización de archivos entre VPS y Google Cloud Storage.

Este script sincroniza archivos generados por MarketTool entre:
- Directorio local en VPS: /opt/markettool/data/archivos/
- Google Cloud Storage: gs://{BUCKET}/archivos_generados/

Usa checksums para sincronización incremental eficiente.
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


logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)


# Configuración
DEFAULT_LOCAL_DIR = "/opt/markettool/data/archivos"
DEFAULT_BUCKET = os.getenv("GCS_BUCKET_NAME", "markettool_bucket")
DEFAULT_CREDENTIALS = "/root/markettool/trading-firestore.json"
STATE_FILE = "/opt/backups/gcs_sync_state.json"


def get_gcs_client(credentials_path: str) -> storage.Client:
    """Inicializar cliente de GCS."""
    if not Path(credentials_path).exists():
        raise FileNotFoundError(f"Credenciales no encontradas: {credentials_path}")
    
    credentials = service_account.Credentials.from_service_account_file(credentials_path)
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
    """Obtener lista de archivos locales con metadata.
    
    Args:
        local_dir: Directorio base
        max_age_hours: Solo archivos modificados en las últimas N horas (default: 168 = 7 días)
    
    Returns:
        Dict: {relative_path: {size, md5, mtime}}
    """
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
                    continue  # Saltar archivos antiguos
                
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
    """Obtener lista de archivos en GCS con metadata.
    
    Returns:
        Dict: {relative_path: {size, md5, updated}}
    """
    files = {}
    
    try:
        blobs = bucket.list_blobs(prefix=prefix)
        for blob in blobs:
            if blob.name.endswith('/'):
                continue  # Saltar "directorios"
            
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


def upload_to_gcs(bucket: storage.Bucket, local_path: str, gcs_path: str, dry_run: bool = False) -> bool:
    """Subir archivo a GCS."""
    if dry_run:
        logger.info(f"[DRY-RUN] UPLOAD: {local_path} → gs://{bucket.name}/{gcs_path}")
        return True
    
    try:
        blob = bucket.blob(gcs_path)
        blob.upload_from_filename(local_path)
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
        # Crear directorios padres si es necesario
        local_path.parent.mkdir(parents=True, exist_ok=True)
        
        blob = bucket.blob(gcs_path)
        blob.download_to_filename(str(local_path))
        logger.info(f"DOWNLOADED: gs://{bucket.name}/{gcs_path} → {local_path}")
        return True
    except Exception as e:
        logger.error(f"ERROR downloading {gcs_path}: {e}")
        return False


def load_state() -> Dict[str, Any]:
    """Cargar estado previo de sincronización."""
    state_file = Path(STATE_FILE)
    if state_file.exists():
        try:
            return json.loads(state_file.read_text())
        except Exception as e:
            logger.warning(f"Error leyendo estado: {e}")
    return {"last_sync": None, "files": {}}


def save_state(state: Dict[str, Any]) -> None:
    """Guardar estado de sincronización."""
    state["last_sync"] = datetime.now(timezone.utc).isoformat()
    state_file = Path(STATE_FILE)
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps(state, indent=2))


def sync(
    local_dir: Path,
    bucket: storage.Bucket,
    direction: str = "both",
    max_age_hours: int = 168,
    dry_run: bool = False,
) -> Tuple[int, int, int]:
    """Sincronizar archivos entre local y GCS.
    
    Args:
        local_dir: Directorio local base
        bucket: Bucket de GCS
        direction: "upload", "download", o "both"
        max_age_hours: Ventana de tiempo para sincronizar
        dry_run: Solo mostrar, no ejecutar
    
    Returns:
        (uploaded_count, downloaded_count, skipped_count)
    """
    prefix = "archivos_generados/"
    uploaded = 0
    downloaded = 0
    skipped = 0
    
    logger.info(f"=== Sincronización {'DRY-RUN' if dry_run else ''} ===")
    logger.info(f"Dirección: {direction}")
    logger.info(f"Local: {local_dir}")
    logger.info(f"GCS: gs://{bucket.name}/{prefix}")
    logger.info(f"Max age: {max_age_hours} horas")
    logger.info()
    
    # Obtener listas de archivos
    local_files = get_local_files(local_dir, max_age_hours)
    gcs_files = get_gcs_files(bucket, prefix)
    
    logger.info(f"Archivos locales (últimas {max_age_hours}h): {len(local_files)}")
    logger.info(f"Archivos en GCS: {len(gcs_files)}")
    logger.info()
    
    # Subir archivos nuevos/modificados desde local a GCS
    if direction in ("upload", "both"):
        logger.info("--- Subiendo archivos a GCS ---")
        for rel_path, info in local_files.items():
            gcs_path = f"{prefix}{rel_path}"
            
            if rel_path in gcs_files:
                gcs_info = gcs_files[rel_path]
                if info["md5"] == gcs_info.get("md5"):
                    skipped += 1
                    continue  # Ya está sincronizado
            
            if upload_to_gcs(bucket, info["path"], gcs_path, dry_run):
                uploaded += 1
        
        logger.info(f"Subidos: {uploaded}, Saltados: {skipped}")
        logger.info()
    
    # Descargar archivos nuevos desde GCS a local
    if direction in ("download", "both"):
        downloaded = 0
        logger.info("--- Descargando archivos desde GCS ---")
        for rel_path, info in gcs_files.items():
            local_path = local_dir / rel_path
            
            if rel_path in local_files:
                local_info = local_files[rel_path]
                if info.get("md5") == local_info.get("md5"):
                    continue  # Ya está sincronizado
            
            if download_from_gcs(bucket, info["name"], local_path, dry_run):
                downloaded += 1
        
        logger.info(f"Descargados: {downloaded}")
        logger.info()
    
    return uploaded, downloaded, skipped


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--local-dir", default=DEFAULT_LOCAL_DIR, help="Directorio local")
    parser.add_argument("--bucket", default=DEFAULT_BUCKET, help="Nombre del bucket GCS")
    parser.add_argument("--credentials", default=DEFAULT_CREDENTIALS, help="Credenciales GCS JSON")
    parser.add_argument("--direction", choices=["upload", "download", "both"], default="both",
                       help="Dirección de sincronización")
    parser.add_argument("--hours", type=int, default=168, help="Sincronizar archivos de las últimas N horas")
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
        client = get_gcs_client(args.credentials)
        bucket = get_bucket(client, args.bucket)
        
        # Ejecutar sincronización
        uploaded, downloaded, skipped = sync(
            local_dir=local_dir,
            bucket=bucket,
            direction=args.direction,
            max_age_hours=args.hours,
            dry_run=args.dry_run,
        )
        
        logger.info()
        logger.info("=== Resumen ===")
        logger.info(f"Subidos: {uploaded}")
        logger.info(f"Descargados: {downloaded}")
        logger.info(f"Saltados (ya sincronizados): {skipped}")
        
        if not args.dry_run:
            # Guardar estado
            state = load_state()
            state["last_upload_count"] = uploaded
            state["last_download_count"] = downloaded
            save_state(state)
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
