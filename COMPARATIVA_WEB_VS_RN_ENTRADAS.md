# Comparativa Exhaustiva: Web vs RN en Entradas en Vivo

**Fecha:** 2026-07-27 10:45 GMT-4  
**Análisis:** Comparación directa de implementación Web vs React Native  
**Estado:** Documentado

---

## ✅ Áreas IDÉNTICAS (Sincronizadas)

### 1. Intervalos de Polling Backend

**Web (`MonitoreoPage.tsx` línea 3935-3945):**
```typescript
const TF_BACKEND_FALLBACK_MS: Record<string, number> = {
  '1m': 30_000,    // 30s
  '5m': 60_000,    // 1min
  '15m': 120_000,  // 2min
  '30m': 180_000,  // 3min
  '1h': 300_000,   // 5min
  '4h': 600_000,   // 10min
  '1d': 1_800_000, // 30min
  '1w': 3_600_000, // 60min
};
```

**RN (`MonitoreoScreen.tsx` línea 17032-17042):**
```typescript
const TF_BACKEND_FALLBACK_MS: Record<string, number> = {
  '1m': 30_000,
  '5m': 60_000,
  '15m': 120_000,
  '30m': 180_000,
  '1h': 300_000,
  '4h': 600_000,
  '1d': 1_800_000,
  '1w': 3_600_000,
};
```

**✅ ESTADO:** Idéntico. Ambas plataformas pollenan con la misma frecuencia.

---

### 2. Funciones de Fingerprint (`liveDedup.ts`)

**Web:** `/home/mtoro/projects/markettool-web/src/utils/live/liveDedup.ts`  
**RN:** `/home/mtoro/projects/markettoolapp/src/utils/live/liveDedup.ts`

**✅ ESTADO:** Archivos **byte-idénticos**. Mismas funciones:
- `normTf()` - Normalización de TF
- `priceTicks()` - Redondeo a 5 decimales
- `timeBucket()` - Timestamp exacto (sin bucketing)
- `liveFingerprint()` - Fingerprint completo (incluye TP/SL/timestamp)
- `liveHistoryFingerprint()` - Fingerprint grosero (NO incluye TP/SL/timestamp)
- `LiveFingerprintSet` - Clase para gestión de fingerprints

**Impacto:** Cualquier problema de fingerprint afecta **ambas plataformas por igual**.

---

### 3. Lógica de Deduplicación en Merge

**Web (`MonitoreoPage.tsx` línea 2139-2148):**
```typescript
const fps = new Set(existing_.map((e) => liveHistoryFingerprint(e)));
liveEntriesFPRef.current[tfKey] = fps;
const newOnes = liveEntries.filter((e) => {
  const fp = liveHistoryFingerprint(e);
  if (fps.has(fp)) return false;
  fps.add(fp);
  return true;
});
```

**RN (`MonitoreoScreen.tsx` línea 8143-8158):**
```typescript
const existingFPs = new Set(existing.map(fpOfEntry));
const newOnes = liveEntries.filter(
  e => !existingFPs.has(fpOfEntry(e)),
);
// Donde fpOfEntry usa liveHistoryFingerprint
```

**✅ ESTADO:** Lógica idéntica. Ambas usan `liveHistoryFingerprint` para deduplicación en merge.

---

### 4. Estructura de Datos de Entradas

Ambas plataformas usan la misma estructura:
```typescript
{
  symbol: string,
  timeframe: string,
  source: string,
  side: 'long' | 'short',
  entry_price: number,
  take_profit?: number,
  stop_loss?: number,
  outcome?: 'pending' | 'tp' | 'sl' | 'expired',
  timestamp?: number,
  created_at?: number,
  _origin?: 'live' | 'analysis',
  _backend_live?: boolean,
}
```

**✅ ESTADO:** Compatible.

---

## 🔴 DIFERENCIAS CRÍTICAS ENCONTRADAS

### DIFERENCIA #1: Caché de Entradas por Hash de Inputs

**RN (`MonitoreoScreen.tsx` línea 11566-11590):**
```typescript
// Cache liviano por TF: si inputs clave no cambiaron, reutiliza entradas
const cacheKey = `${symbol}__${tf}`;
const sigLen = (signalsByTf[tf] ?? []).length;
const sig5Len = (signalsByTf['5m'] ?? []).length;

// Include price and MT state in cache hash
const cacheHash = [
  lastCandle.t,
  lastCandle.c,
  s1Use, s2Use, r1Use, r2Use,
  atrUse,
  spreadPipsEffective,
  commissionPctEffective,
  sigLen, sig5Len,
  allowLong1m, allowShort1m,
  mtEntriesBySymbolTf[symbol]?.[tf]?.entradas ? 'mt' : 'nomt',
].map(x => String(x ?? '')).join('|');

const cached = entriesByTfCacheRef.current[cacheKey];
if (cached && cached.hash === cacheHash) {
  out[tf] = cached.entries;  // ✅ Reutiliza caché
  continue;
}
```

**Web:** ❌ **NO TIENE** este caché.

**Impacto:**
- **RN:** Reduce "saltos" en UI al reutilizar entradas cuando los inputs no cambian
- **Web:** Regenera/reprocesa entradas en cada render, potencialmente causando más fluctuación

**Severidad:** 🟡 Media - Mejora UX pero no afecta funcionalidad core

---

### DIFERENCIA #2: Persistencia de Estado

**Web:**
```typescript
// localStorage para estado persistente
localStorage.setItem('mt_last_exec_id', rawExecId);
localStorage.setItem(`mt.monitorSel.${execId}`, data);
localStorage.setItem(SS_KEY, JSON.stringify({...liveEntriesByTf}));
```

**RN:**
```typescript
// AsyncStorage para estado persistente
AsyncStorage.setItem('user_id', userId);
AsyncStorage.setItem('telegram_id', telegramId);
AsyncStorage.setItem('chat_id', chatId);
AsyncStorage.setItem(indexKey, JSON.stringify(next));
```

**Impacto:**
- **Web:** `localStorage` se pierde en ciertos escenarios (modo incógnito, clear cache)
- **RN:** `AsyncStorage` es más persistente pero más lento

**Severidad:** 🟢 Baja - Implementación diferente, mismo resultado

---

### DIFERENCIA #3: Filtros de Visualización

**RN tiene función dedicada `filterLiveEntriesForDisplay`:**
```typescript
export function filterLiveEntriesForDisplay<T extends LiveEntry>(
  entries: readonly T[],
  options: LiveEntryScopeOptions = {},
): T[] {
  const {
    symbol,
    runningKeys,
    allowedSources,
    sourceFilterMode = 'event-additive',
    originFilter = 'all',
    excludeExpired = true,  // ✅ Filtra expired por defecto
    dedupe = true,
  } = options;
  
  for (const entry of entries) {
    if (excludeExpired && entry.outcome === 'expired') continue;  // ✅ Filtro explícito
    // ... más filtros
  }
}
```

**Web:** No tiene función equivalente centralizada. Los filtros están dispersos en el código.

**Impacto:**
- **RN:** Filtros consistentes y centralizados
- **Web:** Riesgo de inconsistencia si hay múltiples puntos de filtrado

**Severidad:** 🟡 Media - Podría causar diferencias en qué entradas se muestran

---

### DIFERENCIA #4: Manejo de Errores de Red

**RN (`MonitoreoScreen.tsx` línea ~8195):**
```typescript
catch (e) {
  console.warn(`[onBeat] error ${sym} ${tf}:`, e);
  const errMsg = String((e as any)?.message ?? '');
  if (
    errMsg.includes('Network request failed') ||
    errMsg.includes('timeout') ||
    errMsg.includes('ECONNREFUSED')
  ) {
    // ⚡ PERF: Track network failures globally to prevent hammering when offline
    setGlobalNetworkFailure(true);
  }
  // Backoff handling...
}
```

**Web:** Manejo de errores similar pero sin tracking global de fallos de red.

**Impacto:**
- **RN:** Mejor manejo de desconexiones, reduce polling cuando hay problemas de red
- **Web:** Puede seguir pollenando incluso cuando hay problemas de red

**Severidad:** 🟢 Baja - Mejora de UX, no afecta cálculo de entradas

---

### DIFERENCIA #5: Watchdog de Freshness

**RN (`MonitoreoScreen.tsx` línea 5350-5380):**
```typescript
const watchdog = setInterval(() => {
  // Verifica que las entradas/candles se estén actualizando
  // Si no hay updates recientes, marca como stale
}, 15_000);
```

**Web:** Tiene verificación similar (`checkStaleAndPurge`) pero implementada diferentemente.

**Impacto:** Potencial diferencia en cuándo se marcan datos como "stale".

**Severidad:** 🟢 Baja - Ambos tienen mecanismos similares

---

## 📊 Resumen de Comparativa

| Área | Web | RN | ¿Diferencia? | Impacto |
|------|-----|----|--------------|---------|
| **Polling intervals** | ✅ Idéntico | ✅ Idéntico | ❌ No | Ninguno |
| **Fingerprint functions** | ✅ Mismo código | ✅ Mismo código | ❌ No | Ninguno |
| **Dedup logic** | ✅ Idéntica | ✅ Idéntica | ❌ No | Ninguno |
| **Entradas cache por hash** | ❌ No tiene | ✅ Sí tiene | 🔴 Sí | UX (saltos en UI) |
| **Persistencia** | localStorage | AsyncStorage | 🟡 Implementación | Baja |
| **Filtros centralizados** | ❌ Dispersos | ✅ `filterLiveEntriesForDisplay` | 🔴 Sí | Potencial inconsistencia |
| **Network failure tracking** | ❌ Básico | ✅ Global + backoff | 🟡 Sí | UX en mala red |
| **Watchdog freshness** | ✅ Sí | ✅ Sí | 🟡 Implementación | Baja |

---

## 🔍 Conclusión Principal

**Las diferencias Web vs RN NO explican la discrepancia entre backtesting y vivo.**

Ambas plataformas:
1. ✅ Usan los mismos intervalos de polling
2. ✅ Usan las mismas funciones de fingerprint
3. ✅ Usan la misma lógica de deduplicación
4. ✅ Reciben los mismos datos del backend

**La causa raíz está en el BACKEND o en la deduplicación compartida:**
- 🔴 Fingerprint mismatch backend vs frontend (bucketing 5min vs timestamp exacto)
- 🔴 `liveHistoryFingerprint` demasiado grosero (filtra entradas válidas)
- 🔴 TTL de expiración muy corto

**Diferencias Web vs RN son principalmente de UX:**
- 🟡 RN tiene caché de entradas que reduce "saltos" en UI
- 🟡 RN tiene mejores filtros centralizados
- 🟡 RN tiene mejor manejo de fallos de red

---

## 🛠️ Recomendaciones Específicas

### Para Web:
1. **Agregar caché de entradas por hash** (como RN) para reducir saltos en UI
2. **Centralizar filtros** en una función como `filterLiveEntriesForDisplay`
3. **Agregar tracking global de fallos de red** para mejor UX

### Para RN:
1. ✅ Ya tiene las mejoras anteriores
2. **Verificar que `filterLiveEntriesForDisplay` se use consistentemente** en todos los puntos

### Para Ambas:
1. 🔴 **Fix urgente:** Unificar fingerprint backend/frontend
2. 🔴 **Fix urgente:** Mejorar `liveHistoryFingerprint` para incluir TP/SL
3. 🟡 **Mejora:** Extender TTL para TFs cortos

---

**Próximos pasos:** Confirmar con el usuario si quiere proceder con las correcciones críticas primero, luego las mejoras de UX.
