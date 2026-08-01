#!/bin/bash
# Cron script para sincronización incremental de Firestore a PostgreSQL
# Ejecutar cada hora: 0 * * * * /opt/backups/cron_sync_firestore.sh

set -euo pipefail

SCRIPT_DIR="/opt/backups"
PROJECT_DIR="/root/markettool"  # Asumiendo que markettool está en /root
LOG_DIR="/var/log/markettool"
LOG_FILE="$LOG_DIR/firestore_sync.log"

# Crear directorio de logs si no existe
mkdir -p "$LOG_DIR"
chmod 755 "$LOG_DIR"

# Variables de entorno
export GOOGLE_APPLICATION_CREDENTIALS="$PROJECT_DIR/trading-firestore.json"
export MARKETTOOL_POSTGRES_DSN_FILE="/run/secrets/markettool_postgres_dsn"
export MARKETTOOL_POSTGRES_SCHEMA="markettool"
export PYTHONPATH="$PROJECT_DIR:$PYTHONPATH"

# Cambiar al directorio del proyecto
cd "$PROJECT_DIR" || { log "ERROR: Cannot cd to $PROJECT_DIR"; exit 1; }

# Función de logging
log() {
    echo "[$(date -Iseconds)] $*" >> "$LOG_FILE"
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
