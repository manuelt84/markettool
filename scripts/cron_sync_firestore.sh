#!/bin/bash
# Cron script para sincronización incremental de Firestore a PostgreSQL
# Ejecutar cada hora: 0 * * * * /path/to/cron_sync_firestore.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
LOG_DIR="/var/log/markettool"
LOG_FILE="$LOG_DIR/firestore_sync.log"

# Crear directorio de logs si no existe
sudo mkdir -p "$LOG_DIR"
sudo chmod 755 "$LOG_DIR"

# Variables de entorno
export GOOGLE_APPLICATION_CREDENTIALS="$PROJECT_DIR/trading-firestore.json"
export MARKETTOOL_POSTGRES_DSN_FILE="/run/secrets/markettool_postgres_dsn"
export MARKETTOOL_POSTGRES_SCHEMA="markettool"
export PYTHONPATH="$PROJECT_DIR:$PYTHONPATH"

# Cambiar al directorio del proyecto
cd "$PROJECT_DIR"

# Función de logging
log() {
    echo "[$(date -Iseconds)] $*" | sudo tee -a "$LOG_FILE"
}

# Iniciar sync
log "=== Starting Firestore incremental sync ==="

# Sincronizar colecciones críticas (últimas 2 horas por defecto)
python3 "$SCRIPT_DIR/sync_firestore_incremental.py" \
    --collections ejecuciones \
    --collections user_ids \
    --collections monitoreos \
    --collections suscripciones_user \
    --collections iap_tokens \
    --collections user_states \
    --hours 2 \
    --batch-size 100 \
    --page-size 500 \
    --retries 5 \
    >> "$LOG_FILE" 2>&1

EXIT_CODE=$?

if [ $EXIT_CODE -eq 0 ]; then
    log "✅ Sync completed successfully"
else
    log "❌ Sync failed with exit code $EXIT_CODE"
fi

log "=== Finished Firestore sync ==="
echo "" >> "$LOG_FILE"

exit $EXIT_CODE
