#!/bin/bash
# Cron script para sincronización inversa PostgreSQL → Firestore
# Ejecutar cada 6 horas: 0 */6 * * * /opt/backups/cron_sync_postgres_to_firestore.sh

set -euo pipefail

SCRIPT_DIR="/opt/backups"
PROJECT_DIR="/root/markettool"
LOG_DIR="/var/log/markettool"
LOG_FILE="$LOG_DIR/firestore_reverse_sync.log"
VENV_DIR="/opt/backups/firestore-sync-venv"

# Crear directorio de logs si no existe
mkdir -p "$LOG_DIR"
chmod 755 "$LOG_DIR"

# Variables de entorno
export GOOGLE_APPLICATION_CREDENTIALS="/root/markettool/trading-firestore.json"
export MARKETTOOL_POSTGRES_DSN_FILE="/run/secrets/markettool_postgres_dsn"
export MARKETTOOL_POSTGRES_SCHEMA="markettool"

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
log "=== Starting PostgreSQL → Firestore sync ==="

# Sincronizar todas las colecciones desde PostgreSQL (últimas 6 horas)
# El script detecta automáticamente qué colecciones tienen datos nuevos
python3 "$SCRIPT_DIR/sync_postgres_to_firestore.py" \
    --hours 6 \
    --batch-size 100 \
    --verbose \
    >> "$LOG_FILE" 2>&1

EXIT_CODE=$?

if [ $EXIT_CODE -eq 0 ]; then
    log "✅ Reverse sync completed successfully"
else
    log "❌ Reverse sync failed with exit code $EXIT_CODE"
fi

log "=== Finished PostgreSQL → Firestore sync ==="
echo "" >> "$LOG_FILE"

exit $EXIT_CODE
