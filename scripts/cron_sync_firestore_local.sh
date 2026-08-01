#!/bin/bash
# Cron script para sincronización incremental Firestore → PostgreSQL (ejecución local)
# Ejecutar cada hora: 0 * * * * /home/mtoro/projects/markettool/scripts/cron_sync_firestore_local.sh

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
export MARKETTOOL_POSTGRES_DSN_FILE="$PROJECT_DIR/config/.env.maquina-b.dsn"
export MARKETTOOL_POSTGRES_SCHEMA="markettool"

# Función de logging
log() {
    echo "[$(date -Iseconds)] $*" | sudo tee -a "$LOG_FILE"
}

# Iniciar sync
log "=== Starting Firestore incremental sync (local) ==="

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
    | sudo tee -a "$LOG_FILE"

EXIT_CODE=${PIPESTATUS[0]}

if [ $EXIT_CODE -eq 0 ]; then
    log "✅ Sync completed successfully"
else
    log "❌ Sync failed with exit code $EXIT_CODE"
fi

log "=== Finished Firestore sync ==="
echo "" | sudo tee -a "$LOG_FILE"

exit $EXIT_CODE
