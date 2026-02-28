# Configuración del Sistema de Caché - MarketTool

## Visión General

MarketTool ahora soporta **3 capas de caché configurable**:

```
┌─────────────────────────────────────────────────────────┐
│ Tier-0: Redis (Ultra-rápido <50ms, volátil)            │
│         OR GCS (Persistencia, <500ms)                   │
│         OR Memory (Rápido, no persistente)              │
├─────────────────────────────────────────────────────────┤
│ Tier-1: GCS (Si Tier-0 es Redis)                        │
│         O Calculation (Si Tier-0 es Memory/None)        │
├─────────────────────────────────────────────────────────┤
│ Tier-2: Calculation (Fallback final, 1-5s)              │
└─────────────────────────────────────────────────────────┘
```

**Lo clave**: Redis NO reemplaza GCS. Es complementario:
- **Redis** = Caché volátil ultra-rápida (resultados recientes)
- **GCS** = Persistencia a largo plazo (histórico completo)

## Parámetros de Configuración

### `CACHE_ENABLED` (default: `true`)

Habilita/deshabilita TODO el sistema de caché.

```bash
# Usar caché (default)
export CACHE_ENABLED=true

# Deshabilitar caché completamente (debugging)
export CACHE_ENABLED=false
```

### `CACHE_STRATEGY` (default: `redis_gcs`)

Define **QUÉ capas** usar. Valores válidos:

#### 1. `redis_gcs` (RECOMENDADO PRODUCCIÓN)

```
Tier-0 (Redis)  ---> Tier-1 (GCS) ---> Tier-2 (Cálculo)
```

Máxima eficiencia:
- Cachea resultados recientes en Redis
- Persiste datos a largo plazo en GCS
- Fallback automático si una capa falla

```bash
export CACHE_STRATEGY=redis_gcs
export REDIS_URL=redis://localhost:6379
export GCS_BUCKET=market-tool-historical-data
```

#### 2. `redis_only` (DESARROLLO)

```
Tier-0 (Redis) ---> Tier-1 (Cálculo)
```

Para testing/desarrollo sin GCS:

```bash
export CACHE_STRATEGY=redis_only
export REDIS_URL=redis://localhost:6379
```

#### 3. `gcs_only` (SIN REDIS)

```
Tier-0 (GCS) ---> Tier-1 (Cálculo)
```

Si Redis no está disponible pero tienes GCS:

```bash
export CACHE_STRATEGY=gcs_only
export GCS_BUCKET=market-tool-historical-data
```

#### 4. `memory_only` (TESTING)

```
Tier-0 (Memory) ---> Tier-1 (Cálculo)
```

Para tests rápidos sin infraestructura:

```bash
export CACHE_STRATEGY=memory_only
```

## Configuración por Componente

### Redis

Solo se requiere si `CACHE_STRATEGY` incluye `redis`:

```bash
# Conexión local (desarrollo)
export REDIS_URL=redis://localhost:6379

# Con autenticación
export REDIS_URL=redis://:password@localhost:6379

# Redis Cluster
export REDIS_URL=redis://host1:port1,host2:port2

# Todas las opciones: redis://[[username][:password]@][host][:port][/db]
```

### GCS (Google Cloud Storage)

Solo se requiere si `CACHE_STRATEGY` incluye `gcs`:

```bash
# Nombra tu bucket
export GCS_BUCKET=market-tool-historical-data

# Si está en un proyecto GCP específico
export GCP_PROJECT_ID=my-project-id
```

## Ejemplos de Configuración

### Ejemplo 1: Producción (Recomendado)

```bash
#!/bin/bash
export CACHE_ENABLED=true
export CACHE_STRATEGY=redis_gcs
export REDIS_URL=redis://redis-master:6379
export GCS_BUCKET=market-tool-prod-data
```

**Comportamiento**:
- Intenta cargar desde Redis (50-100x más rápido si hit)
- Si falla, carga desde GCS (30-60x más rápido)
- Si falla, calcula (1-5s)
- Automatiza fallback sin intervención manual

**Rendimiento esperado**:
- Hit rate: 80-95% después de 1 hora
- Latencia promedio: <100ms (vs 2-5s sin caché)

### Ejemplo 2: Desarrollo Local

```bash
#!/bin/bash
export CACHE_ENABLED=true
export CACHE_STRATEGY=redis_only
export REDIS_URL=redis://localhost:6379
```

**Ventajas**:
- No depende de GCS setup
- Muy rápido para testing
- Ideal para desarrollo

### Ejemplo 3: Sin Redis (Solo GCS)

```bash
#!/bin/bash
export CACHE_ENABLED=true
export CACHE_STRATEGY=gcs_only
export GCS_BUCKET=market-tool-historical-data
```

**Cuándo usar**:
- Redis no disponible en tu infraestructura
- GCS ya está configurado
- Necesitas persistencia pero no performance ultra-rápida

### Ejemplo 4: Debugging (Sin Caché)

```bash
#!/bin/bash
export CACHE_ENABLED=false
```

**Cuándo usar**:
- Debugging de lógica de cálculo
- Comprobación de que datos son correctos
- Performance testing del backend (sin caché)

## Cómo Funciona el Sistema

### Flujo de Lectura (Get)

Con `CACHE_STRATEGY=redis_gcs`:

```python
# 1. Intenta Redis
if Redis.get(key):
    return cached_value  # ✅ Hit (~50ms)

# 2. Si no, intenta GCS
if GCS.get(key):
    redis.set(key, value)  # Warm up Redis para próxima vez
    return value  # (300-500ms)

# 3. Si no, calcula
result = calculate()
redistribute.set(key, result)  # Almacena en ambas capas
return result  # (1-5s)
```

### Flujo de Escritura (Set)

Cuando se calcula un resultado nuevo:

```python
result = calculate()

# Con "redis_gcs"
redis.set(key, result, ttl=1hour)  # Tier-0 (instant)
gcs.set(key, result)               # Tier-1 (async)

# Con "redis_only"
redis.set(key, result, ttl=1hour)  # Tier-0 (instant)

# Con "gcs_only"
gcs.set(key, result)               # Tier-0 (instant)
```

## TTL (Time-To-Live)

Redis automáticamente expira datos según el timeframe:

```python
"1min":   5 min   (60s datos = cache 5min)
"5min":   10 min  (300s datos = cache 10min)
"15min":  15 min
"30min":  30 min
"1hour":  1 hour
"4hour":  2 hours (4h datos = cache 2h para evitar stale)
"1day":   4 hours (mercado cerrado de noche)
"1week":  1 day   (cambios infrequentes)
```

**Racionalidad**: Timeframes volatiles = TTL corto. Timeframes estables = TTL largo.

## Monitoreo

Ver estadísticas de caché en tiempo real:

```bash
# Llamar endpoint de monitoreo
curl http://localhost:5000/api/cache/stats
```

Respuesta:

```json
{
  "enabled": true,
  "strategy": "redis_gcs",
  "redis": {
    "available": true,
    "indicators": {"hits": 850, "misses": 120, "hit_rate": "87.6%"},
    "ohlcv": {"hits": 620, "misses": 95, "hit_rate": "86.7%"},
    "entradas": {"hits": 380, "misses": 50, "hit_rate": "88.4%"}
  },
  "cache_layers": ["redis", "gcs", "memory"]
}
```

## Migración Entre Estrategias

### De `redis_gcs` a `redis_only`

```bash
# Antes
export CACHE_STRATEGY=redis_gcs
export REDIS_URL=redis://...
export GCS_BUCKET=...

# Después
export CACHE_STRATEGY=redis_only
export REDIS_URL=redis://...
# GCS_BUCKET no se usa más
```

- ✅ Redis cache existente continúa funcionando
- ⚠️ Nuevos cálculos NO se guardan en GCS
- ⚠️ Si Redis reinicia, datos se pierden

### De `redis_only` a `redis_gcs`

```bash
# Antes
export CACHE_STRATEGY=redis_only
export REDIS_URL=redis://...

# Después
export CACHE_STRATEGY=redis_gcs
export REDIS_URL=redis://...
export GCS_BUCKET=...
```

- ✅ Nuevos cálculos ahora se guardan en GCS
- ✅ Persistencia a largo plazo activada
- ℹ️ Redis cache anterior sigue disponible

### De `redis_gcs` a `gcs_only`

```bash
# Antes
export CACHE_STRATEGY=redis_gcs

# Después
export CACHE_STRATEGY=gcs_only
export GCS_BUCKET=...
# Puedes dejar de tener Redis corriendo
```

- ✅ GCS datos disponibles para fallback
- ❌ Sin Redis, latencia aumenta (300-500ms vs <50ms)
- ℹ️ Considera mantener Redis por performance

## Troubleshooting

### Problema: "Redis connection failed"

```
[RedisCache:indicators] ⚠️ Redis connection failed (Connection refused)
```

**Solución**:
1. Verificar Redis corriendo: `redis-cli ping`
2. Verificar URL: `echo $REDIS_URL`
3. Alternativa: cambiar a `CACHE_STRATEGY=gcs_only`

### Problema: Hit rate bajo (<30%)

```
Hit rate: 12.5% - esperado >80%
```

**Causas comunes**:
1. Caché muy nuevo (calentamiento toma 30min-1h)
2. Datos cambian frecuentemente (nuevos symbolos cada análisis)
3. TTL muy corta para tu patrón de uso

**Solución**:
1. Esperar más tiempo para calentar (hit rate sube con uso)
2. Revisar que key generation sea determinística
3. Ajustar TTL en `_get_ttl_seconds()` si necesario

### Problema: Datos stale (desactualizados)

```
Resultado de caché anticuado vs cálculo fresco
```

**Causas**:
1. TTL muy largo para cambios frecuentes
2. Manual no invalidó caché después de actualización

**Solución**:
1. Reducir TTL para esa timeframe
2. Usar Redis Pub/Sub para invalidación (implementado en `redis_cache.py`)
3. Llamar `cache.delete(key)` manualmente después de actualizar datos

## Referencia Rápida

| Estrategia | Producción | Desarrollo | Latencia | Persistencia |
|-----------|-----------|-----------|----------|-------------|
| redis_gcs | ✅ | ❌ | <100ms promedio | ✅ GCS |
| redis_only | ⚠️ (testing) | ✅ | <50ms (hits) | ❌ |
| gcs_only | ⚠️ | ❌ | <500ms promedio | ✅ GCS |
| memory_only | ❌ | ✅ | <50ms | ❌ |

## Preguntas Frecuentes

**P: ¿Redis reemplaza GCS?**
R: No. Redis es ultra-rápido pero volátil. GCS es persistente pero más lento. Juntos = óptimo.

**P: ¿Puedo cambiar CACHE_STRATEGY sin reiniciar?**
R: Se recompila la configuración al reimportar el módulo. Mejor reiniciar para garantizar cambios.

**P: ¿Qué pasa si Redis está caído con CACHE_STRATEGY=redis_only?**
R: Fallback a cálculo directo. Será lento pero funcionará.

**P: ¿Puedo usar Redis con GCS, pero no en el mismo orden?**
R: No. El orden es fijo: Redis (si activo) → GCS (si activo) → Cálculo.

**P: ¿Cómo invalido el caché manualmente?**
R: Endpoint `/api/cache/invalidate/{symbol}/{timeframe}` (a implementar si necesario).

---

**Última actualización**: 2026-02-27
