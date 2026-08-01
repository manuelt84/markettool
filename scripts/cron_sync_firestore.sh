#!/bin/bash
# Cron script para sincronización incremental de Firestore a PostgreSQL
# Ejecutar cada hora: 0 * * * * /opt/backups/cron_sync_firestore.sh

set -euo pipefail

SCRIPT_DIR="/opt/backups"
PROJECT_DIR="/root/markettool"
LOG_DIR="/var/log/markettool"
LOG_FILE="$LOG_DIR/firestore_sync.log"
VENV_DIR="/opt/backups/firestore-sync-venv"

# Crear directorio de logs si no existe
mkdir -p "$LOG_DIR"
chmod 755 "$LOG_DIR"

# Variables de entorno
export GOOGLE_APPLICATION_CREDENTIALS="/root/markettool/trading-firestore.json"
export MARKETTOOL_POSTGRES_DSN_FILE="/run/secrets/markettool_postgres_dsn"
export MARKETTOOL_POSTGRES_SCHEMA="markettool"
export PYTHONPATH="${PYTHONPATH:-}:$PROJECT_DIR"

# Función de logging
log() {
    echo "[$(date -Iseconds)] $*" >> "$LOG_FILE"
}

# Crear/activar entorno virtual con Python compatible
if [ ! -d "$VENV_DIR" ]; then
    log "Creando entorno virtual..."
    python3 -m venv "$VENV_DIR" || {
        log "ERROR: No se pudo crear entorno virtual con python3 -m venv"
        exit 1
    }
    
    # Activar y actualizar pip
    source "$VENV_DIR/bin/activate"
    log "Actualizando pip..."
    pip install --upgrade pip setuptools wheel >> "$LOG_FILE" 2>&1 || true
    
    # Instalar dependencias compatibles con Python 3.6
    log "Instalando dependencias (versiones compatibles con Python 3.6)..."
    pip install 'psycopg2-binary==2.9.9' 'google-cloud-firestore==2.7.2' >> "$LOG_FILE" 2>&1 || {
        log "ERROR: Falló instalación de dependencias"
        exit 1
    }
else
    source "$VENV_DIR/bin/activate"
fi

# Cambiar al directorio del proyecto
cd "$PROJECT_DIR" || { log "ERROR: Cannot cd to $PROJECT_DIR"; exit 1; }

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
