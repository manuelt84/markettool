#!/bin/bash
# Cron script para sincronización de archivos VPS ↔ GCS
# Ejecutar cada 6 horas: 0 */6 * * * /opt/backups/cron_sync_archivos_gcs.sh

set -euo pipefail

SCRIPT_DIR="/opt/backups"
LOCAL_DIR="/opt/markettool/data/archivos"
LOG_DIR="/var/log/markettool"
LOG_FILE="$LOG_DIR/gcs_files_sync.log"
VENV_DIR="/opt/backups/firestore-sync-venv"

# Crear directorios si no existen
mkdir -p "$LOG_DIR" "$LOCAL_DIR"
chmod 755 "$LOG_DIR"

# Variables de entorno
export GOOGLE_APPLICATION_CREDENTIALS="/root/markettool/trading-firestore.json"
export GCS_BUCKET_NAME="${GCS_BUCKET_NAME:-markettool_bucket}"

# Activar entorno virtual (tiene google-cloud-storage instalado)
if [ ! -d "$VENV_DIR" ]; then
    echo "ERROR: Entorno virtual no existe en $VENV_DIR" >> "$LOG_FILE"
    exit 1
fi
source "$VENV_DIR/bin/activate"

# Función de logging
log() {
    echo "[$(date -Iseconds)] $*" | tee -a "$LOG_FILE"
}

# Iniciar sync
log "=== Starting GCS files sync ==="

# Sincronizar bidireccionalmente (últimas 6 horas)
python3 "$SCRIPT_DIR/sync_archivos_gcs.py" \
    --local-dir "$LOCAL_DIR" \
    --bucket "$GCS_BUCKET_NAME" \
    --direction both \
    --hours 6 \
    --verbose \
    >> "$LOG_FILE" 2>&1

EXIT_CODE=$?

if [ $EXIT_CODE -eq 0 ]; then
    log "✅ GCS files sync completed successfully"
else
    log "❌ GCS files sync failed with exit code $EXIT_CODE"
fi

log "=== Finished GCS files sync ==="
echo "" >> "$LOG_FILE"

exit $EXIT_CODE
