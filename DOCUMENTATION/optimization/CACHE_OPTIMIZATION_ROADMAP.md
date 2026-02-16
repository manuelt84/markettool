# 🚀 Roadmap de Optimización - Sistema de Caché

## 📊 Estado Actual vs Futuro

### FASE 1: ✅ COMPLETADA (Implementación actual)
```
GCS + Firestore + LRU(5)
Performance: 30 min → 2-3 min (90% mejora)
Costo: $0 adicional
```

### FASE 2: 🔄 OPCIONAL (Mejora incremental si necesitas más velocidad)
```
Redis + GCS + Firestore + LRU(5)
Performance: 2-3 min → 10-30 seg (95% mejora)
Costo: +$30-50/mes
```

---

## 🎯 ¿Cuándo implementar Fase 2?

### Indicadores de que necesitas Redis:

1. **Usuarios se quejan de latencia**
   - Actual: 2-3 min incremental
   - Con Redis: 10-30 seg

2. **Alta concurrencia** (>20 usuarios simultáneos)
   - Redis distribuido maneja mejor la carga

3. **Análisis frecuentes del mismo activo**
   - Redis TTL de 5 min para hits ultra-rápidos

4. **Presupuesto no es problema**
   - $30-50/mes es aceptable

### Indicadores de que NO necesitas Redis (aún):

1. **Usuarios están contentos** con 2-3 min
2. **<10 usuarios simultáneos** típicamente
3. **Presupuesto ajustado**
4. **Primera vez usando el bot** (cold start es aceptable)

---

## 🔧 Implementación Fase 2 (cuando sea necesario)

### Paso 1: Setup Redis en GCP

```bash
# Cloud Memorystore (Redis managed)
gcloud redis instances create markettool-cache \
  --size=1 \
  --region=us-central1 \
  --tier=basic \
  --redis-version=redis_6_x

# Costo: ~$35/mes (1GB basic tier)
```

### Paso 2: Agregar variables de entorno

```bash
REDIS_ENABLED=true
REDIS_HOST=10.x.x.x  # IP privada de Redis
REDIS_PORT=6379
REDIS_TTL_SECONDS=300  # 5 min en Redis
```

### Paso 3: Código adicional (solo ~100 líneas)

```python
# Agregar a MarketTool.py después de imports
import redis

_REDIS_ENABLED = os.environ.get("REDIS_ENABLED", "false").lower() == "true"
_REDIS_CLIENT = None

def _get_redis_client():
    global _REDIS_CLIENT
    if _REDIS_CLIENT is None and _REDIS_ENABLED:
        _REDIS_CLIENT = redis.Redis(
            host=os.environ.get("REDIS_HOST", "localhost"),
            port=int(os.environ.get("REDIS_PORT", "6379")),
            decode_responses=True
        )
    return _REDIS_CLIENT

# Modificar IndicatorsCache.load() para añadir Redis L1
def load(self, symbol: str, tf: str) -> Optional[dict]:
    # 1. Check Redis (nuevo!)
    if _REDIS_ENABLED:
        redis_client = _get_redis_client()
        if redis_client:
            try:
                redis_key = f"indicators:{symbol}:{tf}"
                cached_json = redis_client.get(redis_key)
                if cached_json:
                    logger.debug(f"[Redis] Hit: {symbol}/{tf}")
                    return json.loads(cached_json)
            except Exception as e:
                logger.warning(f"[Redis] Error: {e}")
    
    # 2. Check memory LRU (actual)
    mem_data = self._memory_get(symbol, tf)
    if mem_data is not None:
        return mem_data
    
    # 3. Check GCS (actual)
    # ... resto del código igual
```

### Paso 4: Deploy incremental

```bash
# Test en 1 pod primero
kubectl scale deployment/markettool --replicas=1
kubectl set env deployment/markettool REDIS_ENABLED=true

# Monitorear
kubectl logs -f deployment/markettool | grep Redis

# Si funciona bien, escalar a todos los pods
kubectl scale deployment/markettool --replicas=3
```

---

## 📊 Comparativa de Performance

### Escenario: 50 activos, análisis completo

| Fase | Latencia | Costo/mes | Complejidad |
|------|----------|-----------|-------------|
| **Sin caché** | 30 min | $0 | Baja |
| **Fase 1 (actual)** | 2-3 min | $0 | Media |
| **Fase 2 (con Redis)** | 10-30 seg | $35 | Media-Alta |
| **Pre-cálculo** | <5 seg | $50+ | Alta |

### Escenario: 1 activo, consulta individual

| Fase | Primera vez | Subsecuente |
|------|-------------|-------------|
| Sin caché | 30 seg | 30 seg |
| Fase 1 | 30 seg | 200ms (GCS) |
| Fase 2 | 30 seg | **20ms** (Redis) |

---

## 💡 Recomendación

### Para AHORA (empezar con Fase 1):

✅ **Usar la implementación actual** porque:
- Zero costo adicional
- 90% de mejora es suficiente para empezar
- Simple de mantener
- Validar que los usuarios usan el bot antes de invertir más

### Para FUTURO (evaluar Fase 2 en 1-2 meses):

🔄 **Considerar Redis si:**
- Usuarios activos >20 simultáneos
- Se quejan de latencia de 2-3 min
- Presupuesto permite $30-50/mes adicionales
- Quieres ser competitivo con bots pagos

### Para NUNCA:

❌ **NO implementar si:**
- <10 usuarios típicamente
- Usuarios están contentos con 2-3 min
- Presupuesto muy ajustado
- Bot es uso personal/pequeño equipo

---

## 🎯 Métricas para decidir

Monitorear en las próximas 2-4 semanas:

```bash
# 1. Usuarios simultáneos pico
kubectl top pods | grep markettool
# Si >20 usuarios → considerar Redis

# 2. Quejas de latencia
# Revisar feedback de usuarios
# Si >30% se quejan → considerar Redis

# 3. Hit rate del caché
curl http://pod:8080/api/cache/stats
# Si hit rate >80% → Redis no necesario (GCS es suficiente)
# Si hit rate <50% → evaluar pre-cálculo en vez de Redis

# 4. Costos FMP
# Si ahorras >$100/mes con caché actual → reinvertir $35 en Redis
```

---

## 📚 Recursos

### Si decides implementar Fase 2:
- [Cloud Memorystore Redis](https://cloud.google.com/memorystore/docs/redis)
- [Redis Python Client](https://redis-py.readthedocs.io/)
- [Pricing Calculator](https://cloud.google.com/products/calculator)

### Alternativas a Redis (más baratas):
- [Upstash Redis](https://upstash.com/) - Serverless, pay-per-use
- [Railway Redis](https://railway.app/) - $5-10/mes
- Redis local (solo para dev/testing)

---

**Conclusión:** Fase 1 (actual) es la **solución más idónea para empezar**. Fase 2 es una mejora incremental que puedes evaluar después según métricas reales de uso.

---

**Status:** 📋 Roadmap definido  
**Recomendación:** Empezar con Fase 1, evaluar Fase 2 en 1-2 meses  
**Última actualización:** 11 de Febrero, 2026
