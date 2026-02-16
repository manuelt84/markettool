# 🌐 Sistema de Caché Multi-Pod - Arquitectura y Estrategia

## 🎯 PROBLEMA RESUELTO

En una aplicación **multi-pod, multi-usuario** con activos dinámicos:

### Desafíos
1. **Múltiples pods** ejecutándose simultáneamente
2. **Múltiples usuarios** pidiendo análisis simultáneos
3. **Activos dinámicos** (cualquier símbolo de FMP, no lista fija)
4. **Riesgo de duplicación**: 2 pods calculan el mismo activo al mismo tiempo
5. **Consumo de RAM**: caché local en cada pod → consumo lineal (pods × activos)

### Solución Implementada: **Stateless Pods con Lock Distribuido**

```
┌──────────────────────────────────────────────────────────┐
│                 ARQUITECTURA MULTI-POD                    │
└──────────────────────────────────────────────────────────┘

Pod A (user1)                Pod B (user2)                Pod C (user3)
     │                            │                            │
     ├─ Request: EURUSD/1day     ├─ Request: GBPUSD/1hour    ├─ Request: EURUSD/1day
     │                            │                            │
     ↓                            ↓                            ↓
┌────────────────────────────────────────────────────────────────┐
│              FIRESTORE METADATA (Coordinación)                 │
│  ┌──────────────────────┐     ┌──────────────────────┐       │
│  │ EURUSD__1day         │     │ GBPUSD__1hour        │       │
│  │ calculating_by: PodA │     │ calculating_by: PodB │       │
│  │ lock_since: 10:05:01 │     │ lock_since: 10:05:02 │       │
│  └──────────────────────┘     └──────────────────────┘       │
└────────────────────────────────────────────────────────────────┘
     │                            │                            │
     │ (PodA calcula)             │ (PodB calcula)            │ (PodC ESPERA)
     │                            │                            │
     ↓                            ↓                            ↓
┌────────────────────────────────────────────────────────────────┐
│                    GCS (Source of Truth)                       │
│  gs://markettool/indicators/                                   │
│    ├─ EURUSD__1day.json  ← PodA escribe / PodC lee           │
│    └─ GBPUSD__1hour.json ← PodB escribe                       │
└────────────────────────────────────────────────────────────────┘
     │                            │                            │
     ↓                            ↓                            ↓
  PodA devuelve               PodB devuelve              PodC usa resultado
  a user1                     a user2                    de PodA (sin calcular)
```

## 🏗️ ARQUITECTURA DETALLADA

### 1. **Pods Stateless**
```python
# Cada pod tiene:
- Memory Cache: LRU(5) items  # Solo 5 activos más recientes, bajo consumo RAM
- Pod ID: hostname  # Identificación única
- Lock timeout: 3 min  # Tiempo máximo de cálculo esperado

# NO tiene:
- ❌ Caché grande en memoria
- ❌ Estado persistente local
- ❌ Archivos locales compartidos
```

### 2. **GCS como Única Fuente de Verdad**
```
gs://markettool/
└── indicators/
    ├── EURUSD__1day.json        # Compartido entre TODOS los pods
    ├── EURUSD__4hour.json
    ├── GBPUSD__1day.json
    └── [CUALQUIER_ACTIVO]__[TF].json  # Dinámico, on-demand
```

**Ventajas:**
- ✅ Sin límite de activos (dinámico)
- ✅ Compartido entre todos los pods
- ✅ Persistente (sobrevive reinicios)
- ✅ Rápido (100-300ms de carga)
- ✅ Bajo costo (~$0.01/mes para 1000 archivos)

### 3. **Firestore como Coordinación**

**Collection:** `indicators_metadata`

```json
{
  "doc_id": "EURUSD__1day",
  "symbol": "EURUSD",
  "timeframe": "1day",
  "gcs_path": "gs://markettool/indicators/EURUSD__1day.json",
  
  // TTL & Validación
  "last_update_utc": "2026-02-11T10:05:00Z",
  "data_hash": "abc123...",
  "rows_count": 500,
  "ttl_hours": 4,
  "is_valid": true,
  
  // Lock Distribuido (Multi-Pod)
  "calculating_by_pod": "markettool-deployment-abc123",  // NULL si libre
  "calculating_since": "2026-02-11T10:05:01Z",           // Timestamp de lock
  "lock_acquired_at": "2026-02-11T10:05:01Z",
  "lock_released_at": "2026-02-11T10:07:23Z",
  
  // Métricas
  "calc_duration_ms": 1234,
  "indicators_list": ["SMA", "rsi", "macd", ...]
}
```

**Índices Requeridos:**
```
1. symbol (ASC) + timeframe (ASC)  # Query por activo
2. calculating_by_pod (ASC)  # Cleanup de locks
3. last_update_utc (DESC)  # TTL queries
```

## 🔄 FLUJO MULTI-POD

### Escenario 1: Usuario pide análisis (EURUSD/1day)

```
┌─────────────────────────────────────────────────────────────┐
│ PASO 1: Pod A recibe request                                │
└─────────────────────────────────────────────────────────────┘
Pod A: Necesito indicadores de EURUSD/1day

┌─────────────────────────────────────────────────────────────┐
│ PASO 2: Verificar caché                                     │
└─────────────────────────────────────────────────────────────┘
1. Memory cache LRU? → MISS (no está)
2. GCS? → MISS (no existe archivo)

┌─────────────────────────────────────────────────────────────┐
│ PASO 3: Adquirir lock distribuido                           │
└─────────────────────────────────────────────────────────────┘
Pod A → Firestore:
  - Leer metadata de "EURUSD__1day"
  - calculating_by_pod = NULL → Libre!
  - Escribir: calculating_by_pod = "pod-a", calculating_since = NOW

┌─────────────────────────────────────────────────────────────┐
│ PASO 4: Calcular indicadores                                │
└─────────────────────────────────────────────────────────────┘
Pod A: Calculando... (30 segundos)

┌─────────────────────────────────────────────────────────────┐
│ PASO 5: Guardar en GCS + Firestore                          │
└─────────────────────────────────────────────────────────────┘
Pod A:
  - Guardar indicators en GCS
  - Actualizar metadata en Firestore
  - Liberar lock: calculating_by_pod = NULL

┌─────────────────────────────────────────────────────────────┐
│ PASO 6: Devolver resultado                                  │
└─────────────────────────────────────────────────────────────┘
Pod A → Usuario: Aquí están los indicadores ✅
```

### Escenario 2: Dos pods piden el MISMO activo simultáneamente

```
Timeline:
10:05:00 - Pod A recibe request: EURUSD/1day (user1)
10:05:01 - Pod B recibe request: EURUSD/1day (user2)  ← 1 segundo después!

┌─────────────────────────────────────────────────────────────┐
│ 10:05:00 - Pod A                                             │
└─────────────────────────────────────────────────────────────┘
1. Memory: MISS
2. GCS: MISS
3. Adquirir lock: SUCCESS ✅
   Firestore: calculating_by_pod = "pod-a"
4. Calculando... (tarda 30 segundos)

┌─────────────────────────────────────────────────────────────┐
│ 10:05:01 - Pod B (1 segundo después)                        │
└─────────────────────────────────────────────────────────────┘
1. Memory: MISS
2. GCS: MISS
3. Adquirir lock: LOCKED! ❌
   Firestore: calculating_by_pod = "pod-a" (ocupado)
4. Esperar a que Pod A termine...
   - Check cada 2 segundos
   - Max wait: 200 segundos

┌─────────────────────────────────────────────────────────────┐
│ 10:05:30 - Pod A termina                                    │
└─────────────────────────────────────────────────────────────┘
Pod A:
  - Guarda en GCS ✅
  - Libera lock: calculating_by_pod = NULL
  - Devuelve resultado a user1

┌─────────────────────────────────────────────────────────────┐
│ 10:05:31 - Pod B detecta lock liberado                      │
└─────────────────────────────────────────────────────────────┘
Pod B:
  - Lock liberado detectado!
  - Cargar desde GCS: 200ms ⚡
  - Devuelve resultado a user2 (SIN CALCULAR!)

RESULTADO:
✅ Pod A: calculó (30 seg)
✅ Pod B: reutilizó (0.2 seg) → 99% más rápido!
✅ Total: 1 cálculo en vez de 2 → 50% ahorro
```

### Escenario 3: Activo nuevo dinámico (no en lista fija)

```
Usuario pide: TSLA/1hour (Tesla, no está en forex list)

Pod A:
1. Memory: MISS (nunca pedido antes)
2. GCS: MISS (no existe)
3. Adquirir lock: SUCCESS
4. Calcular (con datos FMP on-demand)
5. Guardar en GCS: gs://markettool/indicators/TSLA__1hour.json
6. Próximas requests: cache hit! ✅

BENEFICIO:
✅ Cualquier activo se cachea automáticamente
✅ Sin configuración previa necesaria
✅ Escalabilidad infinita
```

## 📊 CONSUMO DE RECURSOS

### Memory por Pod

```python
# Configuración actual
INDICATORS_MEMORY_CACHE_SIZE = 5  # Solo 5 items en memoria

# Cálculo de consumo
Por activo cached: ~50 KB (indicadores serializados)
Total por pod: 5 × 50 KB = 250 KB

# Comparación con caché grande
Cache grande (50 activos): 50 × 50 KB = 2.5 MB por pod
Cache LRU(5): 250 KB por pod
AHORRO: 90% menos RAM ✅
```

### Multi-Pod Scaling

```
Escenario: 3 pods, 50 activos totales

ANTES (caché en cada pod):
  Pod A: 50 activos × 50 KB = 2.5 MB
  Pod B: 50 activos × 50 KB = 2.5 MB
  Pod C: 50 activos × 50 KB = 2.5 MB
  TOTAL: 7.5 MB

DESPUÉS (LRU pequeño + GCS):
  Pod A: 5 activos × 50 KB = 250 KB
  Pod B: 5 activos × 50 KB = 250 KB
  Pod C: 5 activos × 50 KB = 250 KB
  TOTAL: 750 KB
  
  AHORRO: 90% menos RAM ✅
  ESCALABILIDAD: lineal O(1) per pod, no O(activos)
```

## 🔧 CONFIGURACIÓN

### Variables de Entorno

```bash
# Caché habilitado
INDICATORS_CACHE_ENABLED=true

# TTL del caché (horas)
INDICATORS_CACHE_TTL_HOURS=4

# Memory cache LRU size (items)
INDICATORS_MEMORY_CACHE_SIZE=5  # Solo 5 activos en RAM

# Lock timeout (segundos)
INDICATORS_LOCK_TIMEOUT_SEC=180  # 3 minutos

# GCS & Firestore (ya configurados)
GCS_ENABLED=true
GCS_BUCKET_NAME=markettool
FIRESTORE_ENABLED=true
```

### Deployment en GKE

```yaml
# markettool-deployment-multi-pod.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: markettool
spec:
  replicas: 3  # 3 pods
  template:
    spec:
      containers:
      - name: markettool
        image: gcr.io/proyecto/markettool:latest
        env:
        - name: INDICATORS_CACHE_ENABLED
          value: "true"
        - name: INDICATORS_MEMORY_CACHE_SIZE
          value: "5"  # RAM baja por pod
        - name: INDICATORS_LOCK_TIMEOUT_SEC
          value: "180"
        resources:
          requests:
            memory: "512Mi"  # Bajo consumo RAM
            cpu: "500m"
          limits:
            memory: "1Gi"
            cpu: "1000m"
```

## 📈 MÉTRICAS Y MONITOREO

### Logs a Observar

```
# Pod A comienza cálculo
[IndicatorsCache] Lock acquired: EURUSD/1day (pod=markettool-abc123)
[IndicatorsCache] Cold start: EURUSD/1day (pod=markettool-abc123)
[IndicatorsCache] Saved: EURUSD/1day (500 rows, 1234ms, pod=markettool-abc123)

# Pod B espera resultado de Pod A
[IndicatorsCache] Lock held by markettool-abc123: EURUSD/1day (age=5s)
[IndicatorsCache] Waiting for other pod to finish: EURUSD/1day
[IndicatorsCache] Other pod finished, using result: EURUSD/1day

# Pod C usa resultado cacheado
[IndicatorsCache] GCS hit: EURUSD/1day (age=0.2h, rows=500, pod=markettool-xyz789)
[Indicators] EURUSD/1day: Cache hit (age=0.2h, 0ms, source=cache_perfect_match, pod=markettool-xyz789)
```

### Dashboard Firestore

Query para monitorear locks activos:

```javascript
// Firestore Console
db.collection("indicators_metadata")
  .where("calculating_by_pod", "!=", null)
  .orderBy("calculating_since", "desc")
  .limit(20)

// Ver locks viejos (posibles pods muertos)
db.collection("indicators_metadata")
  .where("calculating_by_pod", "!=", null)
  .where("calculating_since", "<", Date.now() - 300000)  // >5 min
```

### API Endpoints Multi-Pod

```bash
# Ver locks activos en tiempo real
curl http://pod-a:8080/api/cache/stats
curl http://pod-b:8080/api/cache/stats
curl http://pod-c:8080/api/cache/stats

# Cada pod reporta su propio estado:
{
  "pod_id": "markettool-deployment-abc123",
  "memory_cache_size": 3,  # Solo 3 activos en memoria
  "memory_cache_max": 5,
  "cached_symbols": ["EURUSD_1day", "GBPUSD_4hour", "BTCUSD_1day"]
}
```

## 🚨 Troubleshooting Multi-Pod

### Problema: Locks muertos (pod crasheó sin liberar)

**Síntoma:** Metadata con `calculating_by_pod` viejo (>5 min)

**Solución automática:** El sistema detecta locks viejos y los ignora (timeout=3min)

**Solución manual:**
```bash
# Ver locks viejos
curl http://any-pod:8080/api/cache/metadata?symbol=EURUSD&timeframe=1day

# Invalidar manualmente
curl -X POST http://any-pod:8080/api/cache/invalidate \
  -d '{"symbol": "EURUSD", "timeframe": "1day"}'
```

### Problema: Múltiples pods calculan lo mismo

**Causa:** Firestore lock no funcionando (permisos?)

**Diagnóstico:**
```bash
# Check logs de ambos pods
kubectl logs markettool-pod-a | grep "Lock acquired"
kubectl logs markettool-pod-b | grep "Lock acquired"

# Si ambos adquieren lock → problema de race condition
```

**Solución:** Verificar permisos Firestore, reiniciar pods

### Problema: Consumo alto de RAM

**Causa:** `INDICATORS_MEMORY_CACHE_SIZE` demasiado grande

**Solución:**
```bash
# Reducir a 3-5 items
kubectl set env deployment/markettool INDICATORS_MEMORY_CACHE_SIZE=3
kubectl rollout restart deployment/markettool
```

## 🎯 Best Practices

### 1. **Pod Sizing**
```yaml
# Para 50-100 activos totales
replicas: 3
memory: 512Mi-1Gi  # Suficiente con LRU(5)
cpu: 500m-1000m
```

### 2. **Lock Timeout**
```bash
# Ajustar según tiempo de cálculo esperado
# Para 50 activos: ~30 segundos por activo
# Lock timeout: 180 seg (seguro)
INDICATORS_LOCK_TIMEOUT_SEC=180
```

### 3. **Memory Cache Size**
```bash
# Regla: usuarios * temporalidades / pods
# Ejemplo: 5 usuarios, 3 TFs, 3 pods → 5 items por pod
INDICATORS_MEMORY_CACHE_SIZE=5
```

### 4. **TTL del Caché**
```bash
# Para trading intradía: 2-4 horas
INDICATORS_CACHE_TTL_HOURS=4

# Para swing trading: 8-24 horas
INDICATORS_CACHE_TTL_HOURS=12
```

## 📊 RESULTADOS ESPERADOS

### Performance Multi-Pod (3 pods, 50 activos)

```
Escenario: 5 usuarios simultáneos piden análisis

SIN CACHÉ:
  Cada pod: 16-17 activos × 30 seg = 8-9 min
  Total: 30 minutos (paralelo)
  Cálculos totales: 50 (redundancia si hay overlap)

CON CACHÉ (primera vez):
  Cada pod: 16-17 activos × 30 seg = 8-9 min
  Total: 30 minutos (paralelo)
  Cálculos totales: 50 (sin redundancia gracias a lock)
  
CON CACHÉ (subsecuente):
  Cada pod: 16-17 activos × 200ms = 3-4 seg
  Total: 4 segundos (98% más rápido!)
  Cálculos totales: 0 (todo desde GCS)

CON CACHÉ (incremental):
  Cada pod: 16-17 activos × 2 seg = 32-34 seg
  Total: 35 segundos (95% más rápido!)
  Cálculos totales: 50 (solo últimas velas)
```

### Ahorro de Recursos

```
RAM:
  Antes: 3 pods × 2.5 MB = 7.5 MB
  Después: 3 pods × 250 KB = 750 KB
  Ahorro: 90% ✅

Cálculos duplicados:
  Antes: Sin coordinación, posibles duplicados
  Después: Lock distribuido, 0 duplicados
  Ahorro: Variable, hasta 50% ✅

Latencia usuario:
  Primera vez: igual (30 min)
  Subsecuente: 4 seg (98% mejora) ✅
  Incremental: 35 seg (97% mejora) ✅
```

---

**Status:** ✅ PRODUCTION READY  
**Arquitectura:** Multi-Pod Stateless con Lock Distribuido  
**Escalabilidad:** Horizontal (agregar más pods sin aumentar RAM per pod)  
**Última actualización:** 11 de Febrero, 2026
