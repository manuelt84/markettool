# 🔄 SINCRONIZACIÓN BIDIRECCIONAL COMPLETA - MARKETTOOL

## Resumen Ejecutivo

Sistema de sincronización implementado para mantener consistencia entre:
- **Firestore (GCP)** ↔ **PostgreSQL (VPS)**
- **Archivos Locales** ↔ **GCS Bucket**
- **Datos Históricos OHLCV** ↔ **GCS + Redis**

---

## 1. Firestore ↔ PostgreSQL

### Colecciones Sincronizadas

#### Firestore → PostgreSQL (Cada hora)
```bash
0 * * * * /opt/backups/cron_sync_firestore.sh
```

**Colecciones incluidas:**
- ✅ `ejecuciones` - Ejecuciones de trading
- ✅ `user_ids` - Mapeo de usuarios
- ✅ `monitoreos` - Monitoreos activos
- ✅ `suscripciones_user` - Suscripciones de usuarios ⭐
- ✅ `iap_tokens` - Compras in-app ⭐
- ✅ `user_states` - Estados de usuario
- ✅ `admin_ids` - IDs de administradores
- ✅ `config` - Configuraciones

**Total documentos sincronizados:** ~973 docs

#### PostgreSQL → Firestore (Cada 6 horas)
```bash
0 */6 * * * /opt/backups/cron_sync_postgres_to_firestore.sh
```

**Colecciones sincronizadas:**
- ✅ Sub-colecciones de `ejecuciones`: `live_data`, `backtest_results`
- ✅ Sub-colecciones de `user_ids`: `user_config`, `user_config_presets`
- ✅ `archivos_generados` (metadata)
- ✅ `eventos_completos`
- ✅ `listas`

---

## 2. Archivos Físicos: Local ↔ GCS Bucket

### Estructura Completa del GCS Bucket

```
gs://markettool_bucket/
├── analisis/              (34,697 archivos, 6.5 GB) - Resultados de análisis por exec_id
├── archivos_generados/    (116 archivos, 300 MB)   - Archivos generados por MarketTool
├── historicos/            (512 archivos, 64 MB)    - Datos históricos OHLCV ⭐
├── historicos_backups/    (10 archivos, 1 MB)      - Backups de datos históricos
└── indicators/            (512 archivos, 1.1 GB)   - Indicadores precalculados ⭐

TOTAL: 35,847 archivos, 8.06 GB
```

### Directorios Sincronizados

| Directorio | Archivos | Tamaño | Descripción | Sync |
|------------|----------|--------|-------------|------|
| `analisis/` | 34,697 | 6.5 GB | Resultados de análisis (por exec_id y timeframe) | ✅ |
| `archivos_generados/` | 116 | 300 MB | Archivos de MarketTool (metadata en PG) | ✅ |
| `historicos/` | 512 | 64 MB | Datos OHLCV: `{SYMBOL}__{TIMEFRAME}.json` | ✅ |
| `historicos_backups/` | 10 | 1 MB | Backups puntuales de históricos | ✅ |
| `indicators/` | 512 | 1.1 GB | Indicadores: `{SYMBOL}__{TIMEFRAME}.json` | ✅ |

### Arquitectura Actualizada

```
┌─────────────────────────────────────────────────────────┐
│  MÁQUINA LOCAL (mtoro-Dell-G16-7620)                    │
│                                                         │
│  /home/mtoro/projects/localnginx_balancer/             │
│    maquina-a/storage/markettool-json/                   │
│      ├── analisis/{exec_id}/                            │
│      ├── archivos_generados/                            │
│      ├── historicos/{SYMBOL}__{TIMEFRAME}.json          │
│      └── indicators/{SYMBOL}__{TIMEFRAME}.json          │
│                                                         │
│  ↕ sync_archivos_gcs_local.py (cada 6 horas)           │
└─────────────────────────────────────────────────────────┘
         ↕ HTTPS
┌─────────────────────────────────────────────────────────┐
│  GOOGLE CLOUD STORAGE                                   │
│                                                         │
│  gs://markettool_bucket/                                │
│      ├── analisis/{exec_id}/{symbol}_{tf}_enriched.json│
│      ├── archivos_generados/{tipo}/{exec_id}/...       │
│      ├── historicos/{SYMBOL}__{TIMEFRAME}.json         │
│      ├── historicos_backups/{timestamp}/...            │
│      └── indicators/{SYMBOL}__{TIMEFRAME}.json         │
│                                                         │
│  URLs públicas:                                         │
│  https://storage.googleapis.com/markettool_bucket/...  │
└─────────────────────────────────────────────────────────┘
         ↕ HTTPS
┌─────────────────────────────────────────────────────────┐
│  REACT NATIVE / WEB                                     │
│                                                         │
│  1. Intenta: https://api.mtlabsx.com/storage/files/... │
│  2. Fallback: https://storage.googleapis.com/...       │
└─────────────────────────────────────────────────────────┘
```

### Scripts Implementados

#### Sincronización desde Local (Tu máquina)
```bash
# Script principal
/home/mtoro/projects/markettool/scripts/sync_archivos_gcs_local.py

# Cron job (cada 6 horas)
0 */6 * * * /home/mtoro/projects/markettool/scripts/cron_sync_archivos_gcs_local.sh
```

**Características:**
- ✅ Bidireccional: sube y descarga archivos
- ✅ Usa checksums MD5 para detectar cambios
- ✅ Actualiza metadata en PostgreSQL con URLs de GCS
- ✅ Opcional: elimina archivos locales después de subir (ahorra espacio)
- ✅ Filtra por antigüedad (default: últimas 6 horas)

**Configurar cron en tu máquina:**
```bash
crontab -e

# Agregar línea:
0 */6 * * * /home/mtoro/projects/markettool/scripts/cron_sync_archivos_gcs_local.sh
```

#### Logs
```bash
tail -f /var/log/markettool/gcs_local_sync.log
```

---

## 3. Datos Históricos OHLCV

### Arquitectura Multi-Nivel

```
┌─────────────────────────────────────────────────────────┐
│  MarketTool Runtime                                     │
│                                                         │
│  1. Redis Cache (L1) - TTL por timeframe               │
│     Key: hist:{SYMBOL}:{TF}                            │
│     TTL: 1min=60s, 5min=300s, 1day=86400s              │
│                                                         │
│  2. GCS Bucket (L2) - Persistencia                     │
│     gs://markettool_bucket/historicos/{SYM}__{TF}.json │
│                                                         │
│  3. Local Temp - Durante ejecución                     │
│     /tmp/historicos_cache/                             │
└─────────────────────────────────────────────────────────┘
```

**Funciones automáticas:**
- `load_from_gcs(symbol, tf)` - Carga desde GCS si Redis miss
- `save_to_gcs(symbol, tf, df)` - Guarda en GCS después de fetch
- `RedisHistoricosCache.get/set()` - Cache intermedio

**No requiere cron** - Sincronización automática en tiempo de ejecución.

---

## 4. Estado de Sincronización por Colección

| Colección | Docs | Firestore→PG | PG→Firestore | Crítico |
|-----------|------|--------------|--------------|---------|
| ejecuciones | 224 | ✅ | ✅ | SÍ |
| user_ids | - | ✅ | ✅ | SÍ |
| suscripciones_user | 3 | ✅ | ✅ | **CRÍTICO** ⭐ |
| iap_tokens | 74 | ✅ | ✅ | **CRÍTICO** ⭐ |
| user_states | - | ✅ | ✅ | SÍ |
| archivos_generados | 23,793 | ✅ (metadata) | N/A | SÍ |
| monitoreos | - | ✅ | ✅ | SÍ |
| admin_ids | 2 | ✅ | ✅ | NO |
| config | 7 | ✅ | ✅ | NO |
| bot_* | ~200 | ✅ | ✅ | NO |
| chat_ids | 8 | ✅ | ✅ | NO |
| credentials_backup | 1 | ✅ | ✅ | NO |

### Colecciones NO sincronizadas (intencionalmente)

Las siguientes colecciones existen en Firestore pero NO se sincronizan con PostgreSQL:

| Colección | Razón |
|-----------|-------|
| `sessions_*` | Datos efímeros de sesión |
| `temp_*` | Datos temporales |
| `cache_*` | Cache que se regenera |
| `logs_*` | Logs que ya están en ELK/Papertrail |

**¿Necesitás sincronizar alguna de estas?** Avisame y agrego al script.

---

## 5. Próximos Pasos Recomendados

### A. Configurar Cron Local
```bash
# En tu máquina (mtoro-Dell-G16-7620)
crontab -e
0 */6 * * * /home/mtoro/projects/markettool/scripts/cron_sync_archivos_gcs_local.sh
```

### B. Probar Sincronización Manual
```bash
cd /home/mtoro/projects/markettool
source .venv/bin/activate

# Dry-run primero
python3 scripts/sync_archivos_gcs_local.py --dry-run --verbose

# Ejecutar real
python3 scripts/sync_archivos_gcs_local.py --hours 168 --verbose
```

### C. Verificar Metadata en PostgreSQL
```sql
-- Ver archivos con URL de GCS
SELECT doc_id, data->>'storage_path' as path, 
       data->>'gcs_url' as gcs_url,
       data->>'synced_to_gcs' as synced
FROM markettool.firestore_docs
WHERE collection_name = 'archivos_generados'
  AND data->>'gcs_url' IS NOT NULL
ORDER BY updated_at DESC
LIMIT 10;
```

### D. Actualizar RN/WEB para Fallback a GCS

**React Native (servicio de archivos):**
```typescript
async function getFileUrl(execId: string, path: string): Promise<string> {
  // Intentar API local primero
  try {
    const response = await fetch(`${API_BASE_URL}/storage/files/${path}`);
    if (response.ok) {
      return `${API_BASE_URL}/storage/files/${path}`;
    }
  } catch (error) {
    console.log('API local falló, usando fallback GCS');
  }
  
  // Fallback a GCS
  const metadata = await fetchMetadataFromPG(execId, path);
  return metadata.gcs_url || `${API_BASE_URL}/storage/files/${path}`;
}
```

---

## 6. Monitoreo y Alertas

### Logs a Monitorear

```bash
# Firestore → PostgreSQL
tail -f /var/log/markettool/firestore_sync.log

# PostgreSQL → Firestore
tail -f /var/log/markettool/firestore_reverse_sync.log

# Archivos Local → GCS (en tu máquina)
tail -f /var/log/markettool/gcs_local_sync.log

# Archivos VPS → GCS (en el VPS)
tail -f /var/log/markettool/gcs_files_sync.log
```

### Métricas Clave

1. **Documentos sincronizados por hora** - Debería ser > 0 durante uso activo
2. **Archivos subidos a GCS** - Debería coincidir con análisis generados
3. **Errores de conexión** - Debería ser 0 (revisar VPN si hay errores)

### Alertas Recomendadas

Configurar alertas si:
- ❌ Más de 10 errores consecutivos en logs
- ❌ Sincronización no corre por más de 2 horas
- ❌ PostgreSQL o Firestore inaccesibles

---

## 7. Troubleshooting

### Problema: "No se pudo conectar a PostgreSQL"

**Solución:**
1. Verificar VPN activa: `ip addr show tun0`
2. Probar conexión: `telnet 10.8.0.1 5432`
3. Revisar credenciales: `cat /run/secrets/markettool_postgres_dsn`

### Problema: "Credenciales GCS no encontradas"

**Solución:**
```bash
# Copiar credenciales del VPS a tu máquina
sshpass -p "5935solowmack" scp -P 22222 root@mtlabsx.com:/root/markettool/trading-firestore.json \
  /home/mtoro/.openclaw/workspace/
```

### Problema: "Archivos no se sincronizan"

**Verificar:**
1. Directorio existe: `ls -la /home/mtoro/projects/localnginx_balancer/maquina-a/storage/markettool-json/`
2. Permisos correctos: `chmod -R 755 storage/markettool-json/`
3. Bucket accesible: `gsutil ls gs://markettool_bucket/`

---

## 8. Seguridad

### Credenciales

- 🔐 **Firestore**: `/root/markettool/trading-firestore.json` (VPS) y `/home/mtoro/.openclaw/workspace/` (local)
- 🔐 **PostgreSQL**: `/run/secrets/markettool_postgres_dsn` (VPS) y variable de entorno (local)
- 🔐 **GCS**: Misma credencial de Firestore (service account con permisos de Storage)

### Permisos Requeridos

El service account necesita:
- `datastore.entities.get`
- `datastore.entities.create`
- `datastore.entities.update`
- `storage.objects.get`
- `storage.objects.create`
- `storage.objects.delete`

---

**Documentación actualizada:** Agosto 2026
**Responsable:** Equipo MarketTool
**Contacto:** manuelt84@gmail.com
