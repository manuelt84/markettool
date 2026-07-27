# Cambios Aplicados - Correcciones Críticas y Mejoras UX

**Fecha:** 2026-07-27  
**Hora:** 10:36 - 11:00 GMT-4  
**Estado:** ✅ Completado

---

## 🔴 CORRECCIONES CRÍTICAS (Backend)

### Corrección #1: Eliminar Bucketing de 5 Minutos en Fingerprint

**Problema:**
- Backend usaba `ms // 300_000` (bucketing de 5 minutos)
- Frontend (Web/RN) usa timestamp exacto
- Causaba colisión de fingerprints para entradas válidas generadas dentro de la misma ventana de 5 min

**Solución:**
```python
# Antes:
return str(ms // 300_000)  # Bucket de 5 min

# Ahora:
return str(ms)  # Timestamp exacto
```

**Archivo:** `markettool/interfaces/api/live_entries_routes.py`  
**Función:** `_time_bucket()`  
**Impacto:** ✅ Entradas válidas ya no se filtran incorrectamente

---

### Corrección #2: Agregar Logging Diagnóstico de Deduplicación

**Problema:**
- No había visibilidad de cuántas entradas se filtraban por deduplicación
- Imposible debuggear pérdida de entradas

**Solución:**
```python
# Log de expiración
logger.debug("[DEDUPE] %s/%s: %d entradas expiradas removidas", symbol, tf, expired_count)

# Log de filtrado por fingerprint
logger.info(
    "[DEDUPE] %s/%s: %d generadas → %d nuevas (%d filtradas por fingerprint)",
    symbol, tf, original_count, len(new_entries), filtered_count
)
```

**Archivo:** `markettool/interfaces/api/live_entries_routes.py`  
**Función:** `_push_entries_to_redis()`  
**Impacto:** ✅ Monitoreo en tiempo real de deduplicación y expiración

---

### Corrección #3: Extender TTL para TFs Cortos

**Problema:**
- TTL muy corto causaba expiración prematura de entradas
- 1m: 30 min, 5m: 2h (insuficiente para visualización)

**Solución:**
```python
ENTRY_TTL_BY_TF_S = {
    "1m": 60 * 60,       # 1 hora (antes 30 min) → 2x
    "5m": 4 * 3600,      # 4 horas (antes 2h) → 2x
    "15m": 6 * 3600,     # 6 horas (igual)
    "30m": 12 * 3600,    # 12 horas (igual)
    "1h": 24 * 3600,     # 24 horas (igual)
    # ... resto igual
}
```

**Archivo:** `markettool/interfaces/api/live_entries_routes.py`  
**Variable:** `ENTRY_TTL_BY_TF_S`  
**Impacto:** ✅ Entradas duran más tiempo antes de expirar

---

## 🟡 MEJORAS DE UX (Frontend Web)

### Mejora #1: Caché de Entradas por Hash

**Problema:**
- Web regeneraba entradas en cada render incluso si los inputs no cambiaban
- Causaba "saltos" en UI (entradas aparecían/desaparecían)
- RN ya tenía esta optimización, Web no

**Solución:**
```typescript
// Cache key: symbol__tf
const cacheHash = [
  lastCandle.t,
  lastCandle.c,
  soporte_nivel_1, soporte_nivel_2,
  resistencia_nivel_1, resistencia_nivel_2,
  indicators?.atr?.[tf],
  eventosHookRef.current?.length,
].map(x => String(x)).join('|');

const cached = entriesByTfCacheRef.current[tfKey];
if (cached && cached.hash === cacheHash && !isFirstSeed) {
  // Reutilizar entradas del caché
  console.log(`[CACHE][WEB] ${sym}/${tf}: Reutilizando ${cached.entries.length} entradas`);
  // ... merge con existing
} else {
  // Generar nuevas entradas y actualizar caché
  const liveEntries = await generateLiveEntriesCore({...});
  entriesByTfCacheRef.current[tfKey] = { hash: cacheHash, entries: liveEntries };
}
```

**Archivo:** `markettool-web/src/pages/MonitoreoPage.tsx`  
**Refs agregadas:** `entriesByTfCacheRef`  
**Impacto:** ✅ Menos fluctuación en UI, mejor performance

---

## 📊 Resumen de Cambios

| Repositorio | Commits | Archivos | Líneas (+/-) |
|-------------|---------|----------|--------------|
| **markettool** (backend) | 1 | 1 | +70 / -15 |
| **markettool-web** | 1 | 1 | +77 / -23 |
| **markettool-app** | 0 | 0 | 0 (ya tenía las mejoras) |

---

## 🎯 Impacto Esperado

### Cuantitativo:
- **Más entradas visibles:** Reducción de filtrado incorrecto por fingerprint collision
- **Menos expiración:** TTL 2x más largo para TFs cortos
- **Mejor UX:** Cache hit rate estimado ~60-80% en condiciones normales

### Cualitativo:
- **Consistencia:** Backend y frontend ahora usan mismo fingerprint (timestamp exacto)
- **Diagnóstico:** Logs permiten monitorear pérdida de entradas en tiempo real
- **Paridad Web/RN:** Web ahora tiene caché de entradas como RN

---

## 📝 Documentación Generada

| Archivo | Contenido |
|---------|-----------|
| `ANALISIS_ENTRADAS_VIVO.md` | Análisis completo del flujo de entradas |
| `PROBLEMAS_ENTRADAS_VIVO.md` | 4 problemas críticos identificados + soluciones |
| `COMPARATIVA_WEB_VS_RN_ENTRADAS.md` | Comparativa exhaustiva Web vs RN |
| `CAMBIOS_APLICADOS_2026-07-27.md` | Este documento - resumen de cambios aplicados |

---

## 🔄 Próximos Pasos Sugeridos

### Inmediato (Post-Deploy):
1. **Monitorear logs** de `[DEDUPE]` para verificar reducción en filtrado
2. **Verificar cache hits** en console log de Web (`[CACHE][WEB]`)
3. **Comparar métricas** de entradas mostradas antes/después

### Corto Plazo:
1. **Ajustar TTL** si es necesario (basado en métricas reales)
2. **Optimizar hash** si hay muchos cache misses injustificados
3. **Agregar métricas** de cache hit rate a dashboard

### Largo Plazo:
1. **Unificar completamente** lógica de deduplicación backend/frontend
2. **Agregar tests** para fingerprints y TTL
3. **Documentar** comportamiento esperado por TF

---

## ⚠️ Consideraciones

### Backward Compatibility:
- ✅ Cambios son retrocompatibles
- ✅ Redis keys existentes se actualizan automáticamente
- ✅ No requiere migración de datos

### Riesgos:
- 🟡 TTL más largo → más memoria en Redis (estimado +50-100% para TFs cortos)
- 🟡 Cache puede mantener entradas "viejas" si hash no cambia (mitigado por guards existentes)

### Rollback:
- Simple: Revertir commits `f84c256` (backend) y `3191f5d` (web)
- Sin downtime: Cambios son hot-reloadables

---

## 📌 Commits

### Backend (markettool)
```
f84c256 🔴 CORRECCIONES CRÍTICAS aplicadas en entradas en vivo
a600448 📊 COMPARATIVA EXHAUSTIVA Web vs RN en entradas en vivo
166e6ac 🔴 PROBLEMAS CRÍTICOS IDENTIFICADOS en entradas en vivo
56a4cce 📝 Análisis exhaustivo de cálculo de entradas en vivo
```

### Frontend Web (markettool-web)
```
3191f5d 🟡 MEJORA UX: Agregar caché de entradas por hash (igual que RN)
```

### Frontend RN (markettool-app)
```
Sin cambios necesarios - ya tenía las mejoras implementadas
```

---

**Estado Final:** ✅ Todas las correcciones críticas y mejoras de UX aplicadas  
**Prueba Recomendada:** Deploy a staging y monitoreo de logs por 1-2 horas  
**Rollout:** Listo para producción cuando se valide en staging
