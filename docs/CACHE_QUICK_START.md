# Guía Rápida: Configuración de Caché en MarketTool

## Resumen de Cambios

Se ha implementado un **sistema configurable de caché de 5 capas** que permite elegir entre:

- **Redis + GCS** (Producción - Recomendado)
- **Redis solamente** (Desarrollo)
- **GCS solamente** (Sin Redis)
- **Memory solamente** (Testing)
- **Sin caché** (Debugging)

## Aclaración Importante

❗ **Redis NO reemplaza GCS - Son COMPLEMENTARIOS** ❗

### Arquitectura Real (5 Capas)

```
┌──────────────────────────────────────────────────┐
│ Tier-0: Redis (NEW)                              │
│         Configurable via CACHE_STRATEGY          │
│         Ultra-rápido (<50ms), volátil            │
└──────────────────────────────────────────────────┘
          ↓ (si cache miss)
┌──────────────────────────────────────────────────┐
│ Tier-1: Memory (LazyHistoricosLoader)            │
│         SIEMPRE ACTIVA (no configurable)         │
│         LRU cache en proceso (<5ms)              │
└──────────────────────────────────────────────────┘
          ↓ (si cache miss)
┌──────────────────────────────────────────────────┐
│ Tier-2: Local Files (historicos/*.json)          │
│         SIEMPRE ACTIVA (no configurable)         │
│         Con freshness check por timeframe (<50ms)│
└──────────────────────────────────────────────────┘
          ↓ (si stale o miss)
┌──────────────────────────────────────────────────┐
│ Tier-3: GCS (Google Cloud Storage)               │
│         Configurable via GCS_BUCKET              │
│         Persistencia en la nube (<500ms)         │
└──────────────────────────────────────────────────┘
          ↓ (si cache miss)
┌──────────────────────────────────────────────────┐
│ Tier-4: FMP API (Financial Modeling Prep)        │
│         SIEMPRE ACTIVA (fallback final)          │
│         Fuente de datos principal (1-5s)         │
└──────────────────────────────────────────────────┘
```

**IMPORTANTE**: `CACHE_STRATEGY` solo controla **Tier-0** (Redis). Las capas Memory + Local Files son **optimizaciones internas** que siempre están activas.

## Opción 1: PRODUCCIÓN (Recomendado)

```bash
# En tu .env o variables de entorno:
CACHE_ENABLED=true
CACHE_STRATEGY=redis_gcs
REDIS_URL=redis://localhost:6379
GCS_BUCKET=market-tool-historical-data
```

**Ventajas**:
- ✅ Máxima velocidad: Redis (Tier-0) cachea hits en <50ms
- ✅ Máxima confiabilidad: GCS (Tier-3) respaldo persistente
- ✅ Fallback automático a Memory (Tier-1) + Local Files (Tier-2)
- ✅ Hit rate: 80-95% después de 1 hora (combinando todas las capas)

**Rendimiento esperado** (con todas las capas activas):
- Redis hit (50%): <50ms
- Memory hit (15%): <5ms
- Local file hit (20%): <50ms
- GCS hit (5%): <500ms
- FMP fetch (10%): 1-5s
- **Promedio ponderado**: ~100ms

## Opción 2: DESARROLLO (Sin GCS)

```bash
CACHE_ENABLED=true
CACHE_STRATEGY=redis_only
REDIS_URL=redis://localhost:6379
```

**Cuándo usar**:
- Desarrollo local
- Testing del caché Redis
- No tienes acceso a GCS setup

**Ventajas**:
- ✅ Rápido
- ✅ Simple (un solo servicio: Redis)
- ❌ Sin persistencia (datos se pierden al reiniciar Redis)

## Opción 3: Sin Redis

```bash
CACHE_ENABLED=true
CACHE_STRATEGY=gcs_only
GCS_BUCKET=market-tool-historical-data
```

**Cuándo usar**:
- Redis no disponible
- Ya tienes GCS configurado
- Necesitas persistencia pero no performance ultra-rápida

## Opción 4: Testing (Sin Persistencia)

```bash
CACHE_ENABLED=true
CACHE_STRATEGY=memory_only
```

**Cuándo usar**:
- Tests unitarios
- Desarrollo rápido sin infraestructura

## Cambiar Estrategia

### Opción A: Editar `.env` manualmente (Recomendado)

```bash
nano .env
# Buscar la sección "ADVANCED CACHE CONFIGURATION"
# Cambiar CACHE_STRATEGY según necesites:
#   - redis_gcs   (producción)
#   - redis_only  (desarrollo)
#   - gcs_only    (sin Redis)
#   - memory_only (testing)
```

### Opción B: Ver estrategia actual con script helper

```bash
# Ver estrategia actual
python3 set_cache_strategy.py --show

# Listar opciones disponibles
python3 set_cache_strategy.py --list
```

**Nota**: El script `set_cache_strategy.py` solo MUESTRA información. Para cambiar la estrategia, edita `.env` manualmente.

## Monitoreo

Ver estadísticas en tiempo real:

```bash
curl http://localhost:5000/api/cache/stats

# Respuesta:
{
  "enabled": true,
  "strategy": "redis_gcs",
  "redis": {
    "available": true,
    "indicators": {"hits": 850, "misses": 120, "hit_rate": "87.6%"}
  },
  "cache_layers": ["redis", "gcs", "memory"]
}
```

## Troubleshooting Rápido

| Problema | Causa | Solución |
|----------|-------|----------|
| "Redis connection failed" | Redis no corre | `redis-cli ping` o cambiar a `gcs_only` |
| Hit rate <30% | Caché muy nuevo | Esperar 1 hora para calentamiento |
| Datos desactualizados | TTL muy largo | Implementar invalidación manual o Pub/Sub |
| Desarrollo lento | GCS setup complejo | Cambiar a `redis_only` o `memory_only` |

## Estructura de Archivos Nuevos

```
markettool/
├── config/
│   └── cache_config.py           ← NUEVA: Configuración centralizada
├── infra/cache/
│   └── redis_cache.py            ← ACTUALIZADO: Respeta CacheConfig

.env.local                         ← NUEVO: Variables de entorno (local)
.env.example                       ← NUEVO: Ejemplo de todas las opciones
set_cache_strategy.py             ← NUEVO: Helper para cambiar estrategias
CACHE_CONFIGURATION.md            ← NUEVO: Documentación completa
```

## Próximos Pasos

1. **Ahora**: Elige tu estrategia y actualiza `.env.local`
   ```bash
   python3 set_cache_strategy.py redis_gcs
   ```

2. **Inicia los servicios**:
   ```bash
   # Redis
   redis-server
   
   # MarketTool
   python3 -m markettool.main
   ```

3. **Monitorea el caché**:
   ```bash
   # En otra terminal
   watch 'curl -s http://localhost:5000/api/cache/stats | jq'
   ```

4. **Verifica hit rate** crece en tiempo real (debería llegar a 80-95%)

## Preguntas Frecuentes

**P: ¿Cuál estrategia debo usar?**
- Producción: `redis_gcs` ← Mejor rendimiento
- Desarrollo: `redis_only` ← Más simple
- Testing: `memory_only` ← Más rápido

**P: ¿Cómo paso de desarrollo a producción?**
```bash
# Cambio estrategia
python3 set_cache_strategy.py redis_gcs

# Actualizo URL de Redis (si es diferente)
sed -i 's/localhost:6379/redis-prod:6379/' .env
```

**P: ¿Puedo desactivar caché temporalmente?**
```bash
python3 set_cache_strategy.py disabled
# O
export CACHE_ENABLED=false
```

**P: ¿Mi caché persiste?**
- `redis_gcs`: Sí (GCS + Local Files)
- `redis_only`: Solo Local Files (Redis se borra)
- `gcs_only`: Sí (GCS + Local Files)
- `memory_only`: Solo Local Files

**P: ¿Cuántas capas hay REALMENTE?**
- **TOTAL: 5 capas** (Redis → Memory → Local → GCS → FMP)
- **Configurables**: Redis (Tier-0), GCS (Tier-3)
- **Siempre activas**: Memory (Tier-1), Local Files (Tier-2), FMP (Tier-4)
- Ver [CACHE_FULL_ARCHITECTURE.md](CACHE_FULL_ARCHITECTURE.md) para detalles completos

**P: ¿Por qué tantas capas?**
- **Redis**: Compartir cache entre pods (distribuido)
- **Memory**: Evitar I/O dentro del mismo proceso (ultra-rápido)
- **Local Files**: Sobrevivir a reinicio del proceso (persistencia local)
- **GCS**: Compartir entre servidores diferentes (persistencia cloud)
- **FMP**: Fuente de verdad cuando todo falla (fallback)

---

## Documentación Completa

Para detalles técnicos y casos avanzados:
- **[CACHE_VISUAL_FLOW.md](CACHE_VISUAL_FLOW.md)** ← Flujo visual con ejemplos
- **[CACHE_FULL_ARCHITECTURE.md](CACHE_FULL_ARCHITECTURE.md)** ← Arquitectura completa
- **[CACHE_CONFIGURATION.md](CACHE_CONFIGURATION.md)** ← Referencia técnica

---

**Última actualización**: 2026-02-27
