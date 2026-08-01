# 📁 ARQUITECTURA DE ALMACENAMIENTO DE ARCHIVOS - MARKETTOOL VPS

## ¿Dónde guarda MarketTool los archivos generados en modo VPS?

### Configuración Actual

Cuando MarketTool corre en **modo VPS** (`CLOUD_BACKEND=vps` o `MARKETTOOL_VPS_STORAGE_ROUTES_ENABLED=true`):

```bash
# Variables de entorno configuradas en .env:
MARKETTOOL_VPS_STORAGE_ROOT=/app/storage/markettool-json
MARKETTOOL_VPS_STORAGE_PUBLIC_BASE_URL=https://api.mtlabsx.com/storage/files
MARKETTOOL_VPS_STORAGE_RSYNC_TARGET=root@170.239.86.106:/var/www/markettool-data
MARKETTOOL_VPS_STORAGE_RSYNC_PORT=22222
```

### Flujo de Almacenamiento

```
┌──────────────────────────────────────────────────────────────┐
│  MarketTool (Docker en tu máquina local)                     │
│                                                              │
│  Genera archivo → /app/storage/markettool-json/{path}       │
│       ↓                                                      │
│  Guarda metadata en PostgreSQL tabla firestore_docs          │
│  collection="archivos_generados"                             │
│       ↓                                                      │
│  rsync automático → VPS (mtlabsx.com)                        │
│  /var/www/markettool-data/{path}                             │
│       ↓                                                      │
│  Accesible vía: https://api.mtlabsx.com/storage/files/{path}│
└──────────────────────────────────────────────────────────────┘
```

### Colección `archivos_generados` en PostgreSQL

La tabla `firestore_docs` en PostgreSQL contiene:

```sql
SELECT collection_name, COUNT(*) 
FROM markettool.firestore_docs 
WHERE collection_name LIKE '%archivos%' 
GROUP BY collection_name;

-- Resultado actual: 23,793 documentos
```

Cada documento tiene:
- `doc_id`: Identificador único
- `data`: JSON con metadata del archivo
  - `exec_id`: ID de la ejecución que generó el archivo
  - `symbol`: Símbolo analizado
  - `timeframe`: Temporalidad
  - `file_type`: Tipo de archivo (json, pdf, csv, etc.)
  - `storage_path`: Ruta relativa del archivo
  - `created_at`: Fecha de creación
  - `size_bytes`: Tamaño del archivo

### Tipos de Archivos Generados

1. **Resultados de Análisis Técnico**
   - `/analisis/{exec_id}/resultado.json`
   - `/analisis/{exec_id}/niveles.json`
   - `/analisis/{exec_id}/grafico.png`

2. **Backtests**
   - `/backtests/{exec_id}/reporte.json`
   - `/backtests/{exec_id}/operaciones.csv`

3. **Monitoreos en Vivo**
   - `/monitoreos/{exec_id}/live_data.json`
   - `/monitoreos/{exec_id}/alertas.log`

4. **Exportaciones**
   - `/exports/{user_id}/{timestamp}.pdf`
   - `/exports/{user_id}/{timestamp}.xlsx`

### Sincronización con GCS Bucket

**PROBLEMA IDENTIFICADO:**

Actualmente los archivos se guardan en:
- ✅ PostgreSQL (metadata en `firestore_docs`)
- ✅ VPS filesystem (`/app/storage/markettool-json/`)
- ❌ **NO se sincronizan automáticamente con GCS Bucket**

**SOLUCIÓN IMPLEMENTADA:**

Script `sync_archivos_gcs.py` + cron job cada 6 horas:

```bash
# En VPS (mtlabsx.com)
0 */6 * * * /opt/backups/cron_sync_archivos_gcs.sh
```

Este script:
1. Lee archivos locales desde `/opt/markettool/data/archivos/` ⚠️
2. Compara checksums MD5 con GCS
3. Sube archivos nuevos/modificados a `gs://markettool_bucket/archivos_generados/`
4. Descarga archivos faltantes desde GCS (bidireccional)

**⚠️ NOTA IMPORTANTE:**

El script apunta a `/opt/markettool/data/archivos/` pero la configuración actual de MarketTool usa `/app/storage/markettool-json/`. 

**Hay DOS opciones:**

#### Opción A: Cambiar el script para que use la ruta correcta
```bash
# Editar /opt/backups/cron_sync_archivos_gcs.sh
LOCAL_DIR="/app/storage/markettool-json"
```

#### Opción B: Crear un symlink
```bash
# En el VPS
ln -s /app/storage/markettool-json /opt/markettool/data/archivos
```

**RECOMENDACIÓN:** Usar la **Opción A** y actualizar el script para que apunte a `/app/storage/markettool-json`.

### Datos Históricos OHLCV

Los datos históricos de símbolos/temporalidades siguen una ruta diferente:

```
┌─────────────────────────────────────────────────────────────┐
│  Historicos Cache (markettool/infra/cache/historicos_cache)│
│                                                             │
│  1. Redis (cache rápido, TTL por timeframe)                │
│     Key: hist:{SYMBOL}:{TF}                                │
│     Value: Gzip-compressed JSON                            │
│                                                             │
│  2. GCS Bucket (persistencia)                              │
│     gs://markettool_bucket/historicos/{SYMBOL}__{TF}.json  │
│                                                             │
│  3. Local (temp durante ejecución)                         │
│     /tmp/historicos_cache/                                 │
└─────────────────────────────────────────────────────────────┘
```

**Funciones clave:**
- `load_from_gcs(symbol, tf)` - Descarga desde GCS
- `save_to_gcs(symbol, tf, df)` - Sube a GCS
- `RedisHistoricosCache.get/set()` - Cache intermedio

### Resumen de Rutas

| Tipo de Dato | Ruta Local (VPS) | Ruta GCS | Metadata |
|--------------|------------------|----------|----------|
| Archivos generados | `/app/storage/markettool-json/` | `gs://markettool_bucket/archivos_generados/` | PostgreSQL `firestore_docs` |
| Históricos OHLCV | `/tmp/` (temp) | `gs://markettool_bucket/historicos/` | Redis cache |
| Backups DB | `/opt/backups/db-encrypted/` | N/A (se envían a VPS2) | N/A |

### Próximos Pasos Recomendados

1. **Actualizar `cron_sync_archivos_gcs.sh`** para usar `/app/storage/markettool-json`
2. **Verificar permisos** de lectura/escritura en esa ruta
3. **Probar sincronización** manual primero (dry-run)
4. **Monitorear logs** en `/var/log/markettool/gcs_files_sync.log`
