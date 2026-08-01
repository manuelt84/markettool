#!/bin/bash
# Cron script para sincronización de archivos Local ↔ GCS
# Ejecutar cada 6 horas: 0 */6 * * * /home/mtoro/projects/markettool/scripts/cron_sync_archivos_gcs_local.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOCAL_DIR="/home/mtoro/projects/localnginx_balancer/maquina-a/storage/markettool-json"
LOG_DIR="/var/log/markettool"
LOG_FILE="$LOG_DIR/gcs_local_sync.log"
VENV_DIR="/home/mtoro/projects/markettool/.venv"

# Crear directorios si no existen
mkdir -p "$LOG_DIR"
chmod 755 "$LOG_DIR" 2>/dev/null || true

# Variables de entorno
export GOOGLE_APPLICATION_CREDENTIALS="/home/mtoro/.openclaw/workspace/trading-firestore.json"
export GCS_BUCKET_NAME="${GCS_BUCKET_NAME:-markettool_bucket}"
export MARKETTOOL_POSTGRES_DSN="postgresql://markettool:mt_r75iut75ddrq0vykbah3pb@10.8.0.1:5432/markettool"

# Activar entorno virtual
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
log "=== Starting GCS local files sync ==="

# Sincronizar bidireccionalmente (últimas 6 horas)
python3 "$SCRIPT_DIR/sync_archivos_gcs_local.py" \
    --local-dir "$LOCAL_DIR" \
    --bucket "$GCS_BUCKET_NAME" \
    --direction both \
    --hours 6 \
    --verbose \
    >> "$LOG_FILE" 2>&1

EXIT_CODE=$?

if [ $EXIT_CODE -eq 0 ]; then
    log "✅ GCS local files sync completed successfully"
else
    log "❌ GCS local files sync failed with exit code $EXIT_CODE"
fi

log "=== Finished GCS local files sync ==="
echo "" >> "$LOG_FILE"

exit $EXIT_CODE
