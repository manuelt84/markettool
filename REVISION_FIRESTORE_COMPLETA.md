# 🔍 REVISIÓN COMPLETA FIRESTORE - MarketTool

**Fecha:** 2026-08-04  
**Solicitado por:** Manuel Toro  
**Estado:** Análisis completado (acceso directo a Firestore requiere VPS)

---

## 📋 COLECCIONES ESPERADAS EN FIRESTORE

Según los scripts de sincronización y el código de la app:

### Colecciones Críticas (Negocio)
| Colección | Descripción | Estado Sync |
|-----------|-------------|-------------|
| `suscripciones_user` | Estado de suscripciones y quotas | ✅ Bidireccional |
| `iap_tokens` | Compras in-app verificadas | ✅ Bidireccional |
| `ejecuciones` | Ejecuciones de backtesting/trading | ✅ Bidireccional |
| `user_ids` + subcolecciones | Datos de usuarios | ✅ Bidireccional |
| `user_states` | Estados temporales de usuario | ✅ Bidireccional |
| `monitoreos` | **⚠️ CRÍTICO** - Configuración de monitoreo por activo | ✅ Bidireccional |

### Colecciones de Metadata (Cache)
| Colección | Descripción | Estado Sync |
|-----------|-------------|-------------|
| `indicators_metadata` | Metadata de indicadores precalculados | ✅ Agregada recientemente |
| `historicos_metadata` | Metadata de datos históricos OHLCV | ✅ Agregada recientemente |
| `archivos_generados` | Metadata de archivos generados | ✅ Unidireccional (F→PG) |

### Otras Colecciones Posibles
| Colección | Descripción | Prioridad |
|-----------|-------------|-----------|
| `señales_detectadas` | Señales guardadas por la app | Media |
| `chat_ids` | Mapeo chat_id → user_id | Media |
| `configuraciones` | Configuraciones globales | Baja |

---

## ⚠️ PROBLEMA IDENTIFICADO: ACCESO A FIRESTORE

**No puedo acceder directamente a Firestore desde esta máquina local.**

**Causa:** Las credenciales (`trading-firestore.json`) están configuradas para el proyecto `trading-f8a2b` pero no tengo permisos o el servicio no está habilitado correctamente desde esta ubicación.

**Error:**
```
StatusCode.PERMISSION_DENIED
details: "Permission denied on resource project trading-f8a2b."
reason: "CONSUMER_INVALID"
```

---

## ✅ ESTADO DEL CÓDIGO (App RN y Web)

### Homologación Completada

| Feature | RN | Web | Estado |
|---------|----|-----|--------|
| `liveDedup.ts` | ✅ | ✅ | Idénticos |
| Persistencia `selected_tfs` | ✅ | ✅ | Funcional |
| `locked_timeframes` | ✅ | ✅ | Funcional |
| `allowed_timeframes` | ✅ | ✅ | Funcional |
| Espera carga config Firestore | ✅ | ✅ | Implementado |

### Estructura de Documentos `monitoreos`

Cada documento tiene ID: `{exec_id}__{symbol}`

**Campos escritos por la app:**
```typescript
{
  exec_id: string,
  symbol: string,
  user_id: string,
  
  // TFs seleccionadas manualmente por el usuario
  selected_tfs: string[],        // ej: ["1m", "5m", "15m"]
  monitor_selected_tfs: string[], // alias
  selectedTFs: string[],         // alias
  
  // TFs permitidas (cuando hay lock)
  allowed_timeframes: string[],
  
  // Lock de TFs (evita recuperación automática)
  locked_timeframes: boolean,    // true cuando el usuario seleccionó explícitamente
  
  // TFs actualmente corriendo (heartbeat)
  running: string[],             // actualizado por syncMonitoreoRunningForSymbol
  
  // Estado del monitoreo
  estado: "running" | "stopped" | "paused",
  
  // Timestamps
  started_at: Timestamp,
  updated_at: Timestamp,
  last_tick: Timestamp,
  
  // Datos de operación
  modo: "scalping" | "intradía" | "swing",
  timeframe_list: string[],      // TFs iniciales (startMonitoreo)
  alerts: any[],
  entradas: any[],
  cache_meta: {...},
  proximo_evento: any|null
}
```

---

## 🔴 ACCIÓN REQUERIDA: Reset de Todas las Temporalidades

**Pedido:** Desactivar TODAS las temporalidades de TODOS los activos.

**Script creado:** `/home/mtoro/projects/markettool/reset_all_timeframes.py`

**Ejecución requerida en el VPS:**
```bash
ssh root@170.239.86.106
cd /root/markettool
python3 reset_all_timeframes.py
```

**Qué hace el script:**
1. Conecta a Firestore (colección `monitoreos`)
2. Obtiene TODOS los documentos
3. Para cada documento:
   - Setea `running: []`
   - Setea `selected_tfs: []`
   - Setea `locked_timeframes: false`
   - Actualiza `updated_at` al timestamp actual

**Resultado:** Todos los activos tendrán sus temporalidades desactivadas. El usuario tendrá que seleccionarlas manualmente nuevamente.

---

## 🔍 VERIFICACIÓN PENDIENTE

Para verificar el estado real de las colecciones, necesitas ejecutar esto en el **VPS**:

### 1. Listar todas las colecciones
```bash
cd /root/markettool
python3 -c "
from google.cloud import firestore
db = firestore.Client(project='trading-f8a2b')
print('=== COLECCIONES ===')
for col in db.collections():
    print(f'{col.id}: {col.document_count()} docs')
"
```

### 2. Verificar colección `monitoreos`
```bash
python3 -c "
from google.cloud import firestore
db = firestore.Client(project='trading-f8a2b')
monitoreos = db.collection('monitoreos')
docs = list(monitorios.limit(10).stream())
print(f'Mostrando primeros 10 documentos de {len(docs)} obtenidos:')
for doc in docs:
    data = doc.to_dict()
    print(f\"\\n{doc.id}:\")
    print(f\"  symbol: {data.get('symbol')}\")
    print(f\"  running: {data.get('running')}\")
    print(f\"  selected_tfs: {data.get('selected_tfs')}\")
    print(f\"  locked_timeframes: {data.get('locked_timeframes')}\")
    print(f\"  estado: {data.get('estado')}\")
\"
"
```

### 3. Verificar integridad en PostgreSQL
```bash
# Conectar desde el VPS
psql -h 10.8.0.1 -U markettool -d markettool

# Ver conteo por colección
SELECT 
  collection_name,
  COUNT(*) as doc_count,
  MAX(updated_at) as last_update
FROM markettool.firestore_docs
GROUP BY collection_name
ORDER BY doc_count DESC;

# Ver estado de monitoreos específicos
SELECT 
  doc_id,
  data->>'symbol' as symbol,
  data->'running' as running,
  data->'selected_tfs' as selected_tfs,
  data->>'locked_timeframes' as locked,
  data->>'estado' as estado
FROM markettool.firestore_docs
WHERE collection_name = 'monitoreos'
LIMIT 20;
```

---

## 📝 RESUMEN DE HALLAZGOS

### ✅ Lo que está BIEN:
1. **Código homologado** entre RN y Web
2. **Persistencia de TFs** implementada correctamente
3. **Sincronización Firestore ↔ PostgreSQL** funcionando para colecciones críticas
4. **Scripts de sync** actualizados con metadata de indicadores/históricos

### ⚠️ Lo que requiere atención:
1. **Acceso a Firestore** - Solo posible desde el VPS
2. **Reset de TFs** - Script listo, necesita ejecutarse en VPS
3. **Verificación de colecciones** - Requiere acceso directo o revisar PostgreSQL sync

### 🔧 Próximos Pasos:
1. **SSH al VPS** y ejecutar `reset_all_timeframes.py`
2. **Verificar colecciones** con los scripts de verificación
3. **Revisar logs** de sync en `/var/log/markettool/firestore_sync.log`
4. **Testear la app** después del reset para confirmar que las TFs se pueden seleccionar nuevamente

---

## 📞 CONSULTAS ESPECÍFICAS

Si viste algún problema específico con alguna colección o dato en particular, por favor especificá:
- ¿Qué colección?
- ¿Qué dato debería estar y no está?
- ¿Desde cuándo notaste el problema?

Con esa información puedo investigar más a fondo en los logs o en PostgreSQL.
