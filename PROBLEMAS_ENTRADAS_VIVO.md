# Problemas Críticos Identificados en Entradas en Vivo

**Fecha:** 2026-07-27 10:30 GMT-4  
**Análisis:** Exhaustivo de flujos sospechosos  
**Estado:** Documentado para corrección

---

## 🚨 PROBLEMA #1: Fingerprint Mismatch Backend vs Frontend

### Descripción
El backend y frontend usan **lógica de fingerprint diferente** para deduplicación, causando inconsistencias.

### Backend (`live_entries_routes.py` línea 215-225)
```python
def _time_bucket(value: Any) -> str:
    # Bucket de 5 MINUTOS
    return str(ms // 300_000)  # 300,000 ms = 5 min
```

**Fingerprint incluye:**
- symbol, tf, source, side
- entry_price, take_profit, stop_loss (redondeados a 5 decimales)
- **timestamp con bucketing de 5 minutos**

### Frontend (`liveDedup.ts` línea 40-48)
```typescript
const timeBucket = (value: unknown): string => {
  // SIN bucketing - timestamp EXACTO
  return String(Math.trunc(value > 1e12 ? value : value * 1000));
};
```

**Fingerprint (`liveFingerprint`) incluye:**
- symbol, tf, source, side
- entry_price, take_profit, stop_loss (redondeados a 5 decimales)
- **timestamp exacto (sin bucketing)**

**Fingerprint (`liveHistoryFingerprint`) incluye:**
- symbol, tf, source, side
- entry_price
- **❌ NO incluye TP, SL, ni timestamp**

### Impacto
1. **Backend agrupa en ventanas de 5 min**: Dos entradas idénticas generadas dentro de 5 min se consideran duplicadas
2. **Frontend usa timestamp exacto**: Podría recibir entradas que el backend filtró como duplicadas
3. **`liveHistoryFingerprint` es demasiado grosero**: Filtra entradas distintas con mismo symbol/tf/source/side/price pero diferente TP/SL

### Ejemplo de Colisión Incorrecta

Entrada A:
- Symbol: BTCUSD, TF: 1m, Source: mt, Side: long
- Entry: 95000.00, TP: 96000.00, SL: 94500.00
- Timestamp: 10:00:00

Entrada B (generada 3 min después con nuevos datos):
- Symbol: BTCUSD, TF: 1m, Source: mt, Side: long
- Entry: 95000.00, TP: 96100.00, SL: 94400.00  ← ¡TP/SL diferentes!
- Timestamp: 10:03:00

**Backend:** Ambas tienen mismo `_time_bucket()` (10:00-10:05) → **B se filtra como duplicada** ❌

**Frontend `liveHistoryFingerprint`:** Mismo fingerprint (no incluye TP/SL/timestamp) → **B se filtra como duplicada** ❌

**Frontend `liveFingerprint`:** Fingerprints diferentes (incluye TP/SL/timestamp exacto) → **Ambas se muestran** ✅

### Solución Recomendada

**Opción A (Recomendada):** Eliminar bucketing de tiempo en backend
```python
# Backend: Usar timestamp exacto como frontend
def _time_bucket(value: Any) -> str:
    if isinstance(value, (int, float)) and value > 0:
        ms = int(value if value > 1e12 else value * 1000)
        return str(ms)  # Sin división, timestamp exacto
    # ... resto igual
```

**Opción B:** Hacer consistente el bucketing en ambos lados
```typescript
// Frontend: Agregar bucketing de 5 min
const timeBucket = (value: unknown): string => {
  if (typeof value === 'number' && Number.isFinite(value) && value > 0) {
    const ms = Math.trunc(value > 1e12 ? value : value * 1000);
    return String(Math.trunc(ms / 300_000));  // Bucket de 5 min
  }
  // ... resto igual
};
```

**Adicional:** Unificar `liveFingerprint` y `liveHistoryFingerprint` o documentar cuándo usar cada uno.

---

## 🚨 PROBLEMA #2: Expiración Demasiado Rápida

### TTL Actual (`live_entries_routes.py` línea 247-259)
```python
_ENTRY_TTL_BY_TF_S = {
    "1m": 30 * 60,       # 30 minutos
    "5m": 2 * 3600,      # 2 horas
    "15m": 4 * 3600,     # 4 horas
    "30m": 6 * 3600,     # 6 horas
    "1h": 24 * 3600,     # 24 horas
    "4h": 48 * 3600,     # 48 horas
    "1d": 7 * 86400,     # 7 días
}
```

### Impacto
- En TF 1m: Una entrada válida expira en **30 minutos**
- Si el usuario no ve la entrada en ese窗口, desaparece
- Para scalping, 30 minutos puede ser suficiente, pero...
- **El polling del frontend puede no ser lo suficientemente frecuente** para capturar todas las entradas antes de que expiren

### Solución Recomendada

**Opción A:** Extender TTL para TFs cortos
```python
_ENTRY_TTL_BY_TF_S = {
    "1m": 60 * 60,       # 1 hora (doble)
    "5m": 4 * 3600,      # 4 horas (doble)
    # ... resto igual
}
```

**Opción B:** Agregar logging de expiración para diagnóstico
```python
def _is_entry_expired(entry: dict, tf: str, now_ms: int | None = None) -> bool:
    now_ms = now_ms or int(time.time() * 1000)
    created = _entry_created_ms(entry)
    age_s = (now_ms - created) / 1000
    ttl_s = _entry_ttl_s(tf)
    
    if age_s > ttl_s:
        logger.debug(
            "[EXPIRED] %s/%s entry_id=%s age=%.1fs ttl=%ds",
            entry.get('symbol'), tf, entry.get('id'), age_s, ttl_s
        )
    
    return age_s > ttl_s
```

---

## 🚨 PROBLEMA #3: Doble Deduplicación (Backend + Frontend)

### Flujo Actual
1. **Backend genera N entradas**
2. **Backend deduplica** con `_dedupe_entries()` → quedan M entradas (M ≤ N)
3. **Backend guarda en Redis** solo entradas únicas
4. **Frontend hace polling** → recibe M entradas
5. **Frontend deduplica nuevamente** con `liveHistoryFingerprint()` → quedan K entradas (K ≤ M)

### Problema
Si una entrada tiene:
- Mismo symbol, tf, source, side, entry_price
- Pero diferente TP, SL, o timestamp

**Backend:** La pasa (fingerprint incluye TP/SL/timestamp bucket)
**Frontend `liveHistoryFingerprint`:** La filtra (NO incluye TP/SL/timestamp)

Resultado: **Entradas válidas se pierden en el frontend**

### Solución Recomendada

**Opción A (Recomendada):** Usar `liveFingerprint` en lugar de `liveHistoryFingerprint` en el frontend para deduplicación primaria

**Opción B:** Eliminar deduplicación en frontend y confiar solo en backend

**Opción C:** Hacer que `liveHistoryFingerprint` incluya al menos TP y SL:
```typescript
export function liveHistoryFingerprint(e: ...) {
  const entry = e.entry_price ?? e.entry ?? e.precio;
  const tp = e.take_profit ?? e.tp;
  const sl = e.stop_loss ?? e.sl;
  return [
    normSymbol(e.symbol),
    normTf(e.timeframe ?? e.tf),
    String(e.source ?? '').trim().toLowerCase(),
    normSide(e.side),
    priceTicks(entry),
    priceTicks(tp),  // ✅ Agregar
    priceTicks(sl),  // ✅ Agregar
  ].join('|');
}
```

---

## 🚨 PROBLEMA #4: Logging Insuficiente

### Estado Actual
Solo hay 2 logs relevantes:
```python
logger.info("[LiveWorker] %s/%s → %d entradas generadas", symbol, tf, len(entries))
logger.info("[LiveWorker] %s/%s +%d nuevas entradas", symbol, norm, len(new_entries))
```

### Lo que falta
- ❌ No hay log de cuántas entradas se filtran por deduplicación
- ❌ No hay log de cuántas expiran
- ❌ No hay log de fingerprints colisionando
- ❌ No hay log de diferencias entre generado vs persistido

### Solución Recomendada

Agregar logging diagnóstico:
```python
# En _push_entries_to_redis()
original_count = len(entries)
new_entries = [e for e in _dedupe_entries(entries) if _entry_fingerprint(e) not in existing_fps]
filtered_count = original_count - len(new_entries)

if filtered_count > 0:
    logger.info(
        "[DEDUPE] %s/%s: %d generadas → %d nuevas (%d filtradas por fingerprint)",
        symbol, tf, original_count, len(new_entries), filtered_count
    )
    # Log primeros fingerprints filtrados para diagnóstico
    for e in entries[:5]:
        if _entry_fingerprint(e) in existing_fps:
            logger.debug("  [FILTERED] fp=%s", _entry_fingerprint(e))
```

---

## 📊 Resumen de Impacto

| Problema | Severidad | Frecuencia Estimada | Síntoma |
|----------|-----------|---------------------|---------|
| #1 Fingerprint mismatch | 🔴 Alta | Cada 5 min | Entradas válidas filtradas |
| #2 TTL muy corto | 🟠 Media | Depende del TF | Entradas expiran antes de verse |
| #3 Doble deduplicación | 🔴 Alta | Constante | Pérdida silenciosa de entradas |
| #4 Logging insuficiente | 🟡 Baja | N/A | Difícil debuggear |

---

## 🛠️ Plan de Corrección Sugerido

### Fase 1: Diagnóstico (Inmediato)
1. Agregar logging detallado en backend (_push_entries_to_redis)
2. Agregar logging en frontend (mergeBackendEntries)
3. Capturar traces de 1 hora de operación normal

### Fase 2: Correcciones Críticas
1. **Unificar fingerprint backend/frontend** (eliminar bucketing o hacerlo consistente)
2. **Fix `liveHistoryFingerprint`** para incluir TP y SL
3. **Extender TTL** para TFs cortos (1m: 30min → 1h)

### Fase 3: Validación
1. Comparar cantidad de entradas generadas vs mostradas antes/después
2. Verificar que no haya pérdida de entradas válidas
3. Confirmar que backtesting sigue funcionando (no tocar ese código)

---

## ⚠️ Advertencias

1. **NO tocar código de backtesting** - Funciona perfecto según el usuario
2. **Sincronizar cambios en Web y RN** - Comparten `liveDedup.ts`
3. **Testear exhaustivamente** antes de deploy - Cambios afectan deduplicación en tiempo real
4. **Considerar migración** - Entradas existentes en Redis pueden tener fingerprints antiguos

---

**Próximos pasos:** Esperar confirmación del usuario para proceder con las correcciones.
