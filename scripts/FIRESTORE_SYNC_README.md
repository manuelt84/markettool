# Sincronización Incremental Firestore → PostgreSQL

## 📋 Descripción

Sistema de sincronización automática entre Firestore (GCP) y PostgreSQL (VPS) mediante cron jobs.

## 🔧 Scripts

### 1. `sync_firestore_incremental.py`
Script principal que realiza la sincronización incremental:
- Lee solo documentos nuevos o modificados desde las últimas N horas
- Soporta múltiples colecciones simultáneamente
- Maneja sub-colecciones recursivamente
- Reintentos automáticos ante errores de Firestore
- Opción de dry-run para testing

**Uso básico:**
```bash
python3 scripts/sync_firestore_incremental.py \
    --collections ejecuciones \
    --collections user_ids \
    --hours 24 \
    --verbose
```

**Parámetros principales:**
- `--collections`: Colecciones a sincronizar (repeatable)
- `--hours`: Sincronizar últimas N horas (default: 24)
- `--dry-run`: Solo mostrar, no escribir
- `--batch-size`: Documentos por batch (default: 100)
- `--page-size`: Documentos por página Firestore (default: 500)
- `--retries`: Reintentos por página (default: 5)
- `--verbose`: Output detallado

### 2. `cron_sync_firestore.sh`
Wrapper para ejecución vía cron:
- Ejecuta cada hora (`0 * * * *`)
- Sincroniza últimas 2 horas de datos
- Logging en `/var/log/markettool/firestore_sync.log`

## 📦 Instalación en VPS

### 1. Copiar scripts al VPS
```bash
scp -P 22222 scripts/sync_firestore_incremental.py root@mtlabsx.com:/opt/backups/
scp -P 22222 scripts/cron_sync_firestore.sh root@mtlabsx.com:/opt/backups/
ssh -p 22222 root@mtlabsx.com "chmod +x /opt/backups/*.sh /opt/backups/*.py"
```

### 2. Copiar credenciales de Firestore (CRÍTICO)
```bash
# Desde tu máquina local, copia las credenciales de GCP
scp -P 22222 trading-firestore.json root@mtlabsx.com:/root/markettool/
```

⚠️ **Importante:** Las credenciales deben estar en `/root/markettool/trading-firestore.json`

### 3. Verificar DSN de PostgreSQL
El script usa `MARKETTOOL_POSTGRES_DSN_FILE=/run/secrets/markettool_postgres_dsn`

Verificar que el archivo existe:
```bash
ssh -p 22222 root@mtlabsx.com "cat /run/secrets/markettool_postgres_dsn"
```

### 4. Instalar dependencias Python en VPS
```bash
ssh -p 22222 root@mtlabsx.com "pip3 install --break-system-packages psycopg[binary] google-cloud-firestore"
```

### 5. Configurar cron job
```bash
ssh -p 22222 root@mtlabsx.com "(crontab -l; echo '0 * * * * /opt/backups/cron_sync_firestore.sh') | crontab -"
```

## 🔍 Verificación

### Ejecutar sync manual (dry-run)
```bash
ssh -p 22222 root@mtlabsx.com "cd /root/markettool && python3 /opt/backups/sync_firestore_incremental.py --collections ejecuciones --hours 24 --dry-run --verbose"
```

### Ver logs
```bash
ssh -p 22222 root@mtlabsx.com "tail -f /var/log/markettool/firestore_sync.log"
```

### Ver estado de sincronización en DB
```bash
ssh -p 22222 root@mtlabsx.com "sudo -u postgres psql -d markettool -c \"SELECT collection_name, COUNT(*) as docs, MAX(updated_at) as last_sync FROM markettool.firestore_docs GROUP BY collection_name ORDER BY collection_name;\""
```

## 📊 Colecciones Sincronizadas

Por defecto, el cron sincroniza:
- `ejecuciones` - Ejecuciones de trading
- `user_ids` - Mapeo de usuarios
- `monitoreos` - Monitoreos activos
- `suscripciones_user` - Suscripciones de usuarios
- `iap_tokens` - Tokens de compras in-app
- `user_states` - Estados de usuario

## ⚙️ Personalización

### Cambiar frecuencia del cron
Editar crontab:
```bash
ssh -p 22222 root@mtlabsx.com "crontab -e"
```

Ejemplos:
- Cada hora: `0 * * * *`
- Cada 6 horas: `0 */6 * * *`
- Cada día a las 3 AM: `0 3 * * *`

### Cambiar ventana de tiempo
Editar `/opt/backups/cron_sync_firestore.sh` y modificar `--hours 2` a otro valor.

### Agregar más colecciones
Editar el script cron y agregar más líneas `--collections NOMBRE`.

## 🐛 Troubleshooting

### Error: "Firestore credentials not found"
Las credenciales no están en la ruta esperada. Verificar:
```bash
ls -la /root/markettool/trading-firestore.json
```

### Error: "ModuleNotFoundError: No module named 'psycopg'"
Instalar dependencias:
```bash
pip3 install --break-system-packages psycopg[binary] google-cloud-firestore
```

### Error: "MARKETTOOL_POSTGRES_DSN_FILE not found"
Verificar que el archivo de secretos existe:
```bash
ls -la /run/secrets/markettool_postgres_dsn
```

### Sync muy lento
Reducir `--page-size` o `--batch-size` en el script cron.

### Ver solo errores recientes
```bash
grep "ERROR\|WARN" /var/log/markettool/firestore_sync.log | tail -20
```

## 📈 Métricas

Para ver cuántos documentos se sincronizaron:
```bash
grep "Total documents fetched" /var/log/markettool/firestore_sync.log | tail -10
```

## 🔒 Seguridad

- Las credenciales de GCP deben tener permisos mínimos necesarios (solo lectura a Firestore)
- El archivo de credenciales debe tener permisos `600`
- Los logs no deben contener información sensible

---

**Última actualización:** 2026-08-01  
**Versión:** 1.0
