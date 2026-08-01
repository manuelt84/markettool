# 🔍 ANÁLISIS DE MEJORAS Y PENDIENTES DE SINCRONIZACIÓN

## Resumen Ejecutivo

Análisis completo de:
1. ✅ Qué está bien sincronizado hoy
2. ⚠️ Qué se puede mejorar en la sincronización actual
3. ❌ Qué falta sincronizar (data importante)
4. 🎯 Mejoras de arquitectura recomendadas

---

## 1. ESTADO ACTUAL DE SINCRONIZACIÓN

### ✅ **Bien Sincronizado (Bidireccional)**

| Colección | Docs | F→PG | PG→F | Crítica | Estado |
|-----------|------|------|------|---------|--------|
| `suscripciones_user` | 3 | ✅ | ✅ | **CRÍTICA** | Perfecto ⭐ |
| `iap_tokens` | 74 | ✅ | ✅ | **CRÍTICA** | Perfecto ⭐ |
| `ejecuciones` | 224 | ✅ | ✅ | Alta | Perfecto |
| `user_ids` + subcolecciones | - | ✅ | ✅ | Alta | Perfecto |
| `user_states` | - | ✅ | ✅ | Media | Perfecto |
| `monitoreos` | - | ✅ | ✅ | Media | Perfecto |
| `archivos_generados` (metadata) | 23,793 | ✅ | N/A | Alta | Unidireccional OK |

### ✅ **Archivos Físicos (Local ↔ GCS)**

| Directorio | Archivos | Tamaño | Sync | Estado |
|------------|----------|--------|------|--------|
| `analisis/` | 34,697 | 6.5 GB | ✅ Bidireccional | En progreso |
| `archivos_generados/` | 116 | 300 MB | ✅ Bidireccional | En progreso |
| `historicos/` | 512 | 64 MB | ✅ Bidireccional | En progreso |
| `historicos_backups/` | 10 | 1 MB | ✅ Bidireccional | En progreso |
| `indicators/` | 512 | 1.1 GB | ✅ Bidireccional | En progreso |

### ⚠️ **Metadata NO Sincronizada (Gap Importante)**

| Metadata | Ubicación Actual | Debería Estar | Gap |
|----------|------------------|---------------|-----|
| `indicators_metadata` | Firestore | PostgreSQL | ❌ FALTA ⚠️ |
| `historicos_metadata` | Firestore | PostgreSQL | ❌ FALTA ⚠️ |
| `user_config` (sub-colección) | PostgreSQL | Firestore | ✅ Ya sync |
| `user_config_presets` | PostgreSQL | Firestore | ✅ Ya sync |

---

## 2. MEJORAS RECOMENDADAS (Prioridad Alta)

### 🎯 **Mejora #1: Sincronizar Metadata de Indicadores e Históricos**

**Problema:**
- Los indicadores y históricos tienen metadata en Firestore (`indicators_metadata`, `historicos_metadata`)
- Esta metadata NO se sincroniza con PostgreSQL
- Si el VPS necesita saber cuándo se actualizaron los indicadores, tiene que consultar Firestore directamente

**Solución:**

Agregar estas colecciones al script `sync_firestore_to_postgres.py`:

```python
# markettool/scripts/sync_firestore_to_postgres.py

COLLECTIONS_TO_SYNC = [
    # ... existentes ...
    
    # NUEVAS (agregar):
    "indicators_metadata",      # ← AGREGAR
    "historicos_metadata",      # ← AGREGAR
]
```

**Beneficios:**
- ✅ PostgreSQL tendría toda la metadata centralizada
- ✅ Podés hacer queries SQL sobre cuándo se actualizaron los indicadores
- ✅ Mejor auditoría y trazabilidad
- ✅ Consistencia con el resto del sistema

**Ejemplo de Query Posible (después de sync):**

```sql
-- Ver últimos updates de indicadores por símbolo
SELECT 
    data->>'symbol' as symbol,
    data->>'timeframe' as timeframe,
    (data->>'analysis_audit'->>'last_incremental_at')::timestamp as last_update,
    data->>'rows_count' as rows
FROM markettool.firestore_docs
WHERE collection_name = 'indicators_metadata'
ORDER BY (data->>'analysis_audit'->>'last_incremental_at') DESC NULLS LAST
LIMIT 20;
```

**Prioridad:** 🔴 **ALTA** - Es metadata crítica para operar

---

### 🎯 **Mejora #2: Agregar Validación de Integridad Post-Sync**

**Problema:**
- Los scripts de sync asumen que todo salió bien
- No hay verificación de que los datos sean consistentes después de sync
- Podría haber corrupción silenciosa

**Solución:**

Agregar validaciones al final de cada sync:

```bash
# scripts/cron_sync_firestore.sh (agregar al final)

# Validar integridad después de sync
python3 /opt/backups/validate_sync_integrity.py \
    --source firestore \
    --target postgres \
    --collections suscripciones_user,iap_tokens,ejecuciones \
    --tolerance 0  # 0 diferencias permitidas
```

```python
# scripts/validate_sync_integrity.py

def validate_sync(source: str, target: str, collections: List[str]) -> bool:
    """
    Compara conteos y checksums entre source y target.
    
    Retorna True si todo coincide, False si hay diferencias.
    """
    all_ok = True
    
    for collection in collections:
        # Contar documentos en Firestore
        fs_count = count_firestore_docs(collection)
        
        # Contar documentos en PostgreSQL
        pg_count = count_postgres_docs(collection)
        
        if fs_count != pg_count:
            logger.error(f"COUNT MISMATCH {collection}: FS={fs_count} vs PG={pg_count}")
            all_ok = False
            continue
        
        # Comparar checksums de datos recientes (últimas 24h)
        recent_docs = get_recent_docs(collection, hours=24)
        for doc_id in recent_docs:
            fs_hash = get_firestore_doc_hash(collection, doc_id)
            pg_hash = get_postgres_doc_hash(collection, doc_id)
            
            if fs_hash != pg_hash:
                logger.error(f"HASH MISMATCH {collection}/{doc_id}")
                all_ok = False
    
    return all_ok
```

**Beneficios:**
- ✅ Detecta corrupción temprano
- ✅ Alertas automáticas si algo falla
- ✅ Confianza en la sincronización

**Prioridad:** 🟡 **MEDIA-ALTA** - Importante para producción

---

### 🎯 **Mejora #3: Sincronización Delta en Vez de Completa**

**Problema:**
- El script `sync_postgres_to_firestore.py` actual consulta TODOS los docs modificados desde el último sync
- Pero el script `sync_firestore_to_postgres.py` podría estar haciendo full scan

**Verificación Necesaria:**

Revisar si `sync_firestore_to_postgres.py` usa watermarking (último timestamp procesado):

```python
# ¿Está implementado así? (BIEN)
last_sync_time = read_watermark()
docs = query_firestore(f"updated_at >= {last_sync_time}")

# ¿O está implementado así? (MAL - ineficiente)
all_docs = query_firestore("ALL")  # ← Escanear todo cada vez
```

**Si no usa watermarking, agregar:**

```python
# scripts/sync_firestore_to_postgres.py

WATERMARK_FILE = "/var/lib/markettool/firestore_sync_watermark.json"

def read_watermark() -> datetime:
    """Leer último timestamp procesado."""
    if os.path.exists(WATERMARK_FILE):
        with open(WATERMARK_FILE, "r") as f:
            data = json.load(f)
            return datetime.fromisoformat(data["last_updated_at"])
    return datetime.min

def write_watermark(timestamp: datetime):
    """Guardar timestamp procesado."""
    with open(WATERMARK_FILE, "w") as f:
        json.dump({"last_updated_at": timestamp.isoformat()}, f)

# En el main loop:
last_sync = read_watermark()
docs = query_firestore(f"updated_at >= {last_sync}")
process(docs)
write_watermark(datetime.now(UTC))
```

**Beneficios:**
- ✅ 10-100x más rápido (solo procesa cambios)
- ✅ Menos carga en Firestore
- ✅ Menos transferencia de datos

**Prioridad:** 🟡 **MEDIA** - Depende de cómo esté implementado actualmente

---

### 🎯 **Mejora #4: Agregar Retry Exponencial con Backoff**

**Problema:**
- Si Firestore o PostgreSQL están temporalmente indisponibles, el script falla
- Reintentos inmediatos pueden empeorar la situación

**Solución:**

Implementar retry exponencial:

```python
from tenacity import retry, stop_after_attempt, wait_exponential

@retry(
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=1, min=2, max=60),
    reraise=True
)
def save_to_postgres(doc: dict):
    """Guardar documento en PostgreSQL con retry exponencial."""
    # Implementación...
    pass
```

**Backoff exponencial:**
- Intento 1: Falla → espera 2s
- Intento 2: Falla → espera 4s
- Intento 3: Falla → espera 8s
- Intento 4: Falla → espera 16s
- Intento 5: Falla → espera 32s → ERROR FINAL

**Beneficios:**
- ✅ Resiliencia a fallos transitorios
- ✅ No sobrecarga el servicio durante outages
- ✅ Mayor tasa de éxito

**Prioridad:** 🟢 **MEDIA** - Nice to have para producción

---

### 🎯 **Mejora #5: Dashboard de Monitoreo de Sincronización**

**Problema:**
- No hay visibilidad en tiempo real del estado de sync
- Tenés que revisar logs manualmente

**Solución:**

Crear endpoint o script de monitoreo:

```python
# scripts/sync_status_dashboard.py

def generate_sync_status_report() -> dict:
    """Generar reporte de estado de sincronización."""
    
    return {
        "firestore_to_postgres": {
            "last_run": get_last_run_time("cron_sync_firestore.sh"),
            "status": get_last_exit_code("cron_sync_firestore.sh"),
            "docs_synced": count_docs_synced_last_run(),
            "errors": get_errors_from_log("firestore_sync.log", hours=24),
        },
        "postgres_to_firestore": {
            "last_run": get_last_run_time("cron_sync_postgres_to_firestore.sh"),
            "status": get_last_exit_code("cron_sync_postgres_to_firestore.sh"),
            "docs_synced": count_docs_synced_last_run(),
            "errors": get_errors_from_log("postgres_reverse_sync.log", hours=24),
        },
        "local_to_gcs": {
            "last_run": get_last_run_time("cron_sync_archivos_gcs_local.sh"),
            "status": get_last_exit_code("cron_sync_archivos_gcs_local.sh"),
            "files_uploaded": count_files_uploaded_last_run(),
            "bytes_transferred": get_bytes_transferred(),
            "errors": get_errors_from_log("gcs_local_sync.log", hours=24),
        },
        "integrity_checks": {
            "count_mismatches": query_postgres("""
                SELECT COUNT(*) FROM (
                    SELECT collection_name, COUNT(*) as cnt
                    FROM firestore_docs
                    GROUP BY collection_name
                    HAVING COUNT(*) != (SELECT firestore_count FROM sync_metadata WHERE collection = collection_name)
                ) mismatches
            """),
            "hash_mismatches": get_recent_hash_mismatches(hours=24),
        }
    }

# Uso:
# python3 scripts/sync_status_dashboard.py --output json > /var/www/status/sync_status.json
# O enviar a Telegram/Discord webhook
```

**Dashboard Visual (HTML simple):**

```html
<!DOCTYPE html>
<html>
<head><title>MarketTool Sync Status</title></head>
<body>
  <h1>Estado de Sincronización</h1>
  
  <div class="card success">
    <h2>Firestore → PostgreSQL</h2>
    <p>Última ejecución: hace 15 min</p>
    <p>Docs sincronizados: 973</p>
    <p>Errores (24h): 0</p>
  </div>
  
  <div class="card success">
    <h2>PostgreSQL → Firestore</h2>
    <p>Última ejecución: hace 2 horas</p>
    <p>Docs sincronizados: 973</p>
    <p>Errores (24h): 0</p>
  </div>
  
  <div class="card warning">
    <h2>Local → GCS</h2>
    <p>Última ejecución: EN PROGRESO</p>
    <p>Archivos subidos: 498 / ~1400</p>
    <p>Errores (24h): 0</p>
  </div>
  
  <div class="card success">
    <h2>Integridad</h2>
    <p>Count mismatches: 0</p>
    <p>Hash mismatches: 0</p>
  </div>
</body>
</html>
```

**Beneficios:**
- ✅ Visibilidad inmediata del estado
- ✅ Alertas tempranas de problemas
- ✅ Métricas históricas para troubleshooting

**Prioridad:** 🟢 **MEDIA** - Muy útil para operaciones diarias

---

## 3. DATA IMPORTANTE QUE PODRÍA FALTAR

### ❌ **Posibles Gaps de Sincronización:**

#### A. **Configuraciones de Usuarios**

**Preguntar:**
- ¿Existe una colección `user_settings` o `user_preferences` en Firestore?
- ¿Dónde se guardan las configuraciones personalizadas de cada usuario (temas, notificaciones, etc.)?

**Si existe y no está sincronizada:**
- Agregar a `sync_firestore_to_postgres.py`

---

#### B. **Historial de Operaciones/Trades**

**Preguntar:**
- ¿Hay una colección `trades`, `orders`, o `operations`?
- ¿Esta data es crítica para el negocio?

**Si existe:**
- Debería estar en la lista de colecciones críticas (como `suscripciones_user`)
- Verificar que tenga sync bidireccional

---

#### C. **Logs de Auditoría**

**Preguntar:**
- ¿Hay logs de acciones de usuarios (logins, cambios de configuración, etc.)?
- ¿Se guardan en Firestore o en otro lado?

**Recomendación:**
- Si están en Firestore y son importantes → sincronizar
- Si son solo para debugging → no hace falta sincronizar

---

#### D. **Cache de Datos Externos**

**Preguntar:**
- ¿Hay cache de datos de FMP API, noticias, u otras fuentes externas?
- ¿Dónde se guarda este cache?

**Recomendación:**
- Probablemente NO hace falta sincronizar (es cache regenerable)
- Pero verificar que no haya data importante mezclada

---

#### E. **Sessions Activas**

**Preguntar:**
- ¿Hay una colección `sessions` o `active_sessions`?
- ¿Importa si se pierde esta data?

**Recomendación:**
- Sessions son efímeras → probablemente NO sincronizar
- Pero si hay data de sesión crítica (ej: carritos de compra, estados temporales importantes) → considerar

---

#### F. **Notificaciones Push**

**Preguntar:**
- ¿Hay una colección `notifications` o `push_tokens`?
- ¿Se envían notificaciones push a los usuarios?

**Si existe:**
- `push_tokens` podría ser importante sincronizar (para que el VPS pueda enviar notificaciones)
- `notifications` history → depende si es crítico

---

#### G. **Métricas y Analytics**

**Preguntar:**
- ¿Se guardan métricas de uso (cuántas veces se usó cada feature, tiempos de respuesta, etc.)?
- ¿Dónde se guardan?

**Recomendación:**
- Si es para analytics → probablemente en BigQuery o similar, no hace falta sync
- Si es para billing o límites de uso → debería sincronizarse

---

## 4. CHECKLIST DE VERIFICACIÓN COMPLETA

### 🔍 **Para Identificar Gaps:**

```bash
# 1. Listar TODAS las colecciones en Firestore
gcloud firestore databases list --project trading-f8a2b

# O con Python:
python3 -c "
from google.cloud import firestore
db = firestore.Client(project='trading-f8a2b')
collections = db.collections()
for col in collections:
    print(f'{col.id}: {col.document_count()} docs')
"

# 2. Listar TODAS las colecciones sincronizadas en PostgreSQL
psql -h 10.8.0.1 -U markettool -d markettool -c "
SELECT DISTINCT collection_name, COUNT(*) as doc_count
FROM markettool.firestore_docs
GROUP BY collection_name
ORDER BY collection_name;
"

# 3. Comparar listas para encontrar gaps
# Colecciones en Firestore pero NO en PostgreSQL → FALTA SYNCRONIZAR
# Colecciones en PostgreSQL pero NO en Firestore → OK (pueden ser solo lectura)
```

---

### 📋 **Checklist de Preguntas Clave:**

- [ ] ¿Todas las colecciones críticas están en `sync_firestore_to_postgres.py`?
- [ ] ¿Las sub-colecciones importantes están incluidas (ej: `user_ids/user_config`)?
- [ ] ¿La metadata de indicadores/históricos está sincronizada?
- [ ] ¿Hay datos de usuarios que deberían persistir pero no se sync?
- [ ] ¿Los tokens de notificación push están sincronizados?
- [ ] ¿Hay configuraciones globales que deberían estar en ambos lados?
- [ ] ¿Los logs de auditoría críticos están respaldados?
- [ ] ¿Hay data de billing/pagos que necesita consistencia absoluta?

---

## 5. RECOMENDACIONES DE ARQUITECTURA

### 🏗️ **Mejora de Arquitectura #1: Usar Eventos en Vez de Polling**

**Estado Actual:**
- Cron jobs ejecutan cada X tiempo (polling)
- Puede haber delay entre cambio y sincronización
- Ineficiente si hay pocos cambios

**Mejora Propuesta:**
- Usar Cloud Functions con Firestore triggers
- Cada vez que un doc cambia → dispara sync inmediato

```python
# functions/sync_on_change/index.py

import functions_framework
from google.cloud import firestore
import psycopg2

@functions_framework.http
def sync_on_firestore_change(request):
    """Triggered by Firestore document change."""
    
    # Parsear el cambio desde el request
    data = request.get_json()
    collection = data['collection']
    doc_id = data['document']
    operation = data['operation']  # create, update, delete
    
    if operation in ['create', 'update']:
        # Obtener doc actualizado
        db = firestore.Client()
        doc = db.collection(collection).document(doc_id).get()
        
        # Sync a PostgreSQL
        sync_to_postgres(collection, doc_id, doc.to_dict())
    
    elif operation == 'delete':
        # Eliminar en PostgreSQL
        delete_from_postgres(collection, doc_id)
    
    return {'status': 'ok'}, 200
```

**Setup en Firebase:**
```bash
firebase deploy --only functions:sync_on_firestore_change

# Configurar trigger:
gcloud functions deploy sync_on_firestore_change \
  --trigger-event providers/cloud.firestore/eventTypes/document.write \
  --trigger-resource "projects/trading-f8a2b/databases/(default)/documents/{collection}/{document}"
```

**Beneficios:**
- ✅ Sync casi instantáneo (< 1s)
- ✅ Sin polling innecesario
- ✅ Más eficiente en recursos

**Contras:**
- ⚠️ Más complejo de configurar
- ⚠️ Dependencia de Cloud Functions
- ⚠️ Costos adicionales (aunque mínimos)

**Prioridad:** 🟢 **BAJA-MEDIA** - Solo si necesitás sync en tiempo real

---

### 🏗️ **Mejora de Arquitectura #2: Cola de Sincronización**

**Problema:**
- Si hay muchos cambios simultáneos, el sync puede saturarse
- No hay prioridad (un cambio crítico compite con uno trivial)

**Solución:**
- Usar Cloud Tasks o Redis Stream como cola
- Priorizar cambios críticos (suscripciones, iap_tokens)
- Procesar en background de forma ordenada

```python
# Producer (cuando hay cambio):
from google.cloud import tasks_v2

def enqueue_sync(collection: str, doc_id: str, priority: str = "normal"):
    """Encolar solicitud de sync."""
    
    client = tasks_v2.CloudTasksClient()
    queue_path = client.queue_path(PROJECT, LOCATION, "firestore-sync-queue")
    
    task = {
        "http_request": {
            "http_method": tasks_v2.HttpMethod.POST,
            "url": SYNC_HANDLER_URL,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({
                "collection": collection,
                "doc_id": doc_id,
                "priority": priority,
            }).encode(),
        }
    }
    
    # Prioridad afecta el delay
    if priority == "critical":
        task["schedule_time"] = None  # Ejecutar inmediatamente
    elif priority == "high":
        task["schedule_time"] = Timestamp(seconds=int(time.time()))  # ASAP
    else:
        task["schedule_time"] = Timestamp(seconds=int(time.time() + 60))  # 1min delay
    
    client.create_task(parent=queue_path, task=task)

# Uso:
enqueue_sync("suscripciones_user", "user123", priority="critical")
enqueue_sync("logs", "log456", priority="low")
```

**Beneficios:**
- ✅ Priorización de cambios críticos
- ✅ Throttling automático
- ✅ Retry automático con backoff
- ✅ Visibilidad de cola pendiente

**Prioridad:** 🟢 **BAJA** - Solo si tenés problemas de escala

---

### 🏗️ **Mejora de Arquitectura #3: CDC (Change Data Capture) Nativo**

**Herramientas Profesionales:**
- **Debezium**: CDC open-source para PostgreSQL
- **Google Dataflow**: CDC nativo de GCP
- **Fivetran**: Servicio managed de CDC

**Cómo Funciona:**
- Leen el WAL (Write-Ahead Log) de PostgreSQL
- Detectan cada INSERT/UPDATE/DELETE en tiempo real
- Replican a Firestore automáticamente

**Ventajas:**
- ✅ Zero-latency (sync en milisegundos)
- ✅ Zero-código (configuras y funciona)
- ✅ Confiabilidad enterprise

**Desventajas:**
- 💰 Costo ($$$ para servicios managed)
- ⚙️ Complejidad de setup inicial
- 🔧 Menos control sobre la lógica de sync

**Prioridad:** 🔴 **NO RECOMENDADO** para tu caso
- Overkill para 973 documentos
- Tu solución actual con cron es suficiente
- Solo considerar si escalás a millones de docs

---

## 6. PLAN DE ACCIÓN RECOMENDADO

### 📅 **Semana 1: Fixes Críticos**

1. **Agregar metadata de indicadores/históricos al sync** 🔴
   - Editar `sync_firestore_to_postgres.py`
   - Agregar `indicators_metadata` y `historicos_metadata`
   - Probar y desplegar

2. **Configurar cron de archivos local** 🟡
   ```bash
   crontab -e
   0 */6 * * * /home/mtoro/projects/markettool/scripts/cron_sync_archivos_gcs_local.sh
   ```

3. **Esperar finalización de primera sync** ⏳
   - Monitorear logs
   - Verificar que todos los archivos se subieron

---

### 📅 **Semana 2: Validación y Monitoreo**

4. **Implementar validación de integridad** 🟡
   - Crear `validate_sync_integrity.py`
   - Agregar al final de cada cron job
   - Configurar alertas si hay mismatch

5. **Crear dashboard de estado** 🟢
   - Script `sync_status_dashboard.py`
   - HTML simple o enviar a Telegram
   - Métricas clave: última ejecución, errores, docs sync

---

### 📅 **Semana 3: Optimizaciones**

6. **Verificar/implementar watermarking** 🟢
   - Revisar si `sync_firestore_to_postgres.py` ya lo usa
   - Si no, implementar
   - Medir mejora de performance

7. **Agregar retry exponencial** 🟢
   - Instalar `tenacity`: `pip install tenacity`
   - Decorar funciones de DB con `@retry`
   - Testear con fallos simulados

---

### 📅 **Semana 4: Documentación y Cleanup**

8. **Documentar todas las colecciones** 🟢
   - Crear spreadsheet con:
     - Nombre colección
     - Descripción
     - ¿Crítica? (Y/N)
     - ¿Sync F→PG? (Y/N)
     - ¿Sync PG→F? (Y/N)
     - Owner/responsable
   
9. **Identificar colecciones huérfanas** 🟢
   - Colecciones en Firestore sin sync → ¿deberían syncronizarse?
   - Colecciones obsoletas → ¿se pueden eliminar?

10. **Cleanup de código** 🟢
    - Eliminar scripts viejos no usados
    - Consolidar configs en un solo lugar
    - Agregar tests básicos

---

## 7. RESUMEN EJECUTIVO

### ✅ **Lo Que Está Bien:**
- Sincronización bidireccional de colecciones críticas funcionando
- Archivos físicos sincronizándose correctamente
- Metadata esencial en su mayoría cubierta

### ⚠️ **Lo Que Falta (Prioridad Alta):**
1. **Metadata de indicadores/históricos** → Agregar al sync F→PG
2. **Validación de integridad** → Implementar post-sync checks
3. **Monitoreo/dashboard** → Visibilidad del estado

### 🟢 **Mejoras Opcionales (Prioridad Media-Baja):**
- Watermarking (si no existe)
- Retry exponencial
- Event-driven sync (Cloud Functions)
- Colas de prioridad

### ❌ **No Hacer (Overkill):**
- CDC enterprise (Debezium, Fivetran)
- Soluciones complejas antes de validar necesidades reales

---

## 8. PRÓXIMOS PASOS INMEDIATOS

### Hoy/Esta Semana:

1. **Esperar que termine la sync de archivos** (en progreso: 498/~1400)
2. **Configurar cron job local**:
   ```bash
   crontab -e
   0 */6 * * * /home/mtoro/projects/markettool/scripts/cron_sync_archivos_gcs_local.sh
   ```
3. **Agregar metadata al sync** (15 min de trabajo):
   ```python
   # scripts/sync_firestore_to_postgres.py
   COLLECTIONS_TO_SYNC.extend(["indicators_metadata", "historicos_metadata"])
   ```

### Esta Quincena:

4. **Implementar validación de integridad** (2-3 horas)
5. **Crear dashboard básico** (1-2 horas)
6. **Documentar colecciones faltantes** (1 hora de investigación)

---

**¿Querés que implemente alguna de estas mejoras ahora o preferís esperar a que termine la sincronización actual primero?**
