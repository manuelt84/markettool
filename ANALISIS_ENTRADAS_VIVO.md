# Análisis Exhaustivo: Cálculo de Entradas en Vivo vs Backtesting

**Fecha:** 2026-07-27  
**Autor:** Luna (AI Assistant)

## Resumen Ejecutivo

Se realizó un análisis exhaustivo del cálculo de entradas en vivo en MarketTool-web y MarketTool-app, comparándolo con el cálculo de backtesting (que funciona correctamente).

## Hallazgos Principales

### 1. Flujo de Cálculo de Entradas

#### Backend - Entradas en Vivo

**Archivo:** `MarketTool.py`

```
calcular_entradas_async()
  └─> generar_entradas_multiples()
       └─> _create_entry_candidate()
            └─> Retorna: {precio_entrada, take_profit, stop_loss, side, rrr, score}
```

**Precios de entrada calculados como:**
- **Pullback a S/R:** `s1`, `s2`, `r1`, `r2` (niveles puros)
- **Breakout con offset:** `r1 + 0.2*ATR` o `s1 - 0.2*ATR`
- **Midpoint:** `(r1 + s1) / 2`
- **Bollinger reversion:** `bollinger_lower + 0.1*ATR`
- **Range reversion:** `rango_low + 0.15*ATR`
- **Ladder steps:** `level ± step * 0.25*ATR`

**Parámetros clave en `generar_entradas_multiples()`:**
```python
mult_pullback_s1=(2.0, 1.2)    # (tp_mult, sl_mult)
mult_pullback_s2=(2.2, 1.2)
mult_breakout=(1.6, 1.1)
breakout_offset_atr=0.2
ladder_step_atr=0.25
min_rrr=1.5  # Mínimo RRR para aceptar entrada
```

#### Backend - Backtesting

**Archivo:** `markettool/application/services/backtesting_service.py`

```
run_from_enriched(candles, entries, ...)
  └─> Recibe entradas YA CALCULADAS con precio_entrada, tp, sl
       └─> Simula activación y outcome (tp/sl/pending)
```

**✅ CONFIRMADO:** El backtesting usa las MISMAS entradas generadas por `generar_entradas_multiples()`. Los datos vienen del archivo enriched JSON.

### 2. Flujo Frontend Web

**Archivo:** `markettool-web/src/pages/MonitoreoPage.tsx`

```
pollBackend()  // cada ~30s-5min según TF
  └─> GET /monitoreo/live-entries
       └─> mergeBackendEntries(entries)
            └─> setLiveEntriesByTf()
                 └─> EntradaItem renderiza entry_price
```

**Archivo:** `markettool-web/src/components/EntradaItem.tsx`

```typescript
const entryPrice = entrada.entry ?? entrada.entry_price ?? 0;
// Renderiza: Number(entryPrice).toFixed(5)
```

**✅ CONFIRMADO:** No hay transformación de precios en el frontend. Se muestran tal cual vienen del backend.

### 3. Posibles Fuentes de Discrepancia

#### A. Deduplicación por Fingerprint

**Backend (`live_entries_routes.py` línea 227-236):**
```python
def _entry_fingerprint(entry: dict) -> str:
    entry_price = entry.get("entry_price", entry.get("entry", entry.get("precio")))
    # ... incluye symbol, tf, side, price, tp, sl, timestamp bucket
    return "|".join([...])
```

**Frontend (`MonitoreoPage.tsx` línea 3963):**
```typescript
const fp = liveHistoryFingerprint(entry);
if (!existing.some((e) => liveHistoryFingerprint(e) === fp)) {
  next[key] = [...existing, entry];  // Solo agrega si NO existe
}
```

**⚠️ RIESGO:** Si el fingerprint es demasiado sensible, entradas válidas podrían filtrarse.

#### B. Expiración de Entradas

**Backend (`live_entries_routes.py`):**
```python
ENTRY_TTL_BY_TF_S = {
    "1m": 30 * 60,      # 30 minutos
    "5m": 2 * 3600,     # 2 horas
    "1h": 24 * 3600,    # 24 horas
    "1d": 7 * 86400,    # 7 días
}

def _is_entry_expired(entry: dict, tf: str, now_ms: int) -> bool:
    return now_ms - _entry_created_ms(entry) > _entry_ttl_s(tf) * 1000
```

**⚠️ RIESGO:** Entradas válidas podrían expirar antes de ser vistas por el usuario.

#### C. Filtros de Visualización en UI

**Frontend (`MonitoreoPage.tsx`):**
- Filtrado por símbolo seleccionado
- Filtrado por TF seleccionado
- Filtrado por estado (open/closed/expired)
- Límite de entradas mostradas

**⚠️ RIESGO:** Entradas podrían generarse pero no mostrarse por filtros de UI.

#### D. Timing de Generación

**En vivo:**
- Entradas se regeneran cada beat (cada 5s-6000s según TF)
- Niveles S/R se recargan cada 10min de GCS (`_ENRICHED_NIVELES_TTL_S = 600`)
- Datos pueden cambiar entre refreshes

**En backtesting:**
- Snapshot fijo en el tiempo
- Mismos niveles S/R para todo el backtest
- Sin cambios dinámicos

**⚠️ RIESGO:** Inconsistencia entre niveles usados en vivo vs backtest.

### 4. Puntos de Verificación Recomendados

#### Logs a Agregar en Backend

**En `generar_entradas_multiples()` (MarketTool.py ~14,500):**
```python
logger.info(f"[ENTRADAS-VIVO] {symbol}/{tf}: Generadas {len(entries)} entradas")
for e in entries[:5]:  # Log primeras 5
    logger.info(f"  - {e['side']} @{e['precio_entrada']:.5f} TP={e['take_profit']:.5f} SL={e['stop_loss']:.5f} RRR={e['rrr']:.2f}")
```

**En `live_entries_routes.py` (endpoint GET /live-entries):**
```python
logger.info(f"[LIVE-ENTRIES-API] {exec_id}/{symbol}: Retornando {len(entries)} entradas")
```

#### Logs a Agregar en Frontend

**En `MonitoreoPage.tsx` (mergeBackendEntries):**
```typescript
console.log(`[ENTRADAS-VIVO] Merge: ${incoming.length} entradas recibidas`);
incoming.slice(0, 5).forEach(e => {
  console.log(`  - ${e.side} @${e.entry_price} TP=${e.take_profit} SL=${e.stop_loss}`);
});
```

### 5. Pruebas de Diagnóstico Sugeridas

#### Test 1: Comparar Entradas Generadas vs Mostradas

1. Activar logs en backend en `generar_entradas_multiples()`
2. Activar logs en frontend en `mergeBackendEntries()`
3. Comparar cantidades: ¿Se generan X pero se reciben Y < X?

#### Test 2: Verificar Fingerprint Collision

1. Para un symbol/TF dado, loggear el fingerprint de cada entrada generada
2. Verificar si fingerprints únicos están siendo filtrados incorrectamente

#### Test 3: Comparar Niveles S/R

1. Loggear niveles S/R usados en vivo: `s1, s2, r1, r2`
2. Loggear niveles S/R del enriched JSON usado en backtesting
3. Comparar: ¿Son los mismos niveles?

#### Test 4: Verificar Expiración Prematura

1. Marcar timestamp de creación de cada entrada
2. Monitorear cuándo desaparecen de la UI
3. Verificar si es por expiración o por otro filtro

### 6. Archivos Clave Revisados

| Archivo | Función | Líneas Clave |
|---------|---------|--------------|
| `MarketTool.py` | `generar_entradas_multiples()` | 14,388 - 14,900 |
| `MarketTool.py` | `_create_entry_candidate()` | 14,225 - 14,300 |
| `MarketTool.py` | `calcular_entradas_async()` | 22,103 - 22,800 |
| `live_entries_routes.py` | `_generate_live_entries()` | 1,640 - 1,750 |
| `live_entries_routes.py` | `_entry_fingerprint()` | 227 - 240 |
| `backtesting_service.py` | `run_from_enriched()` | 480 - 650 |
| `MonitoreoPage.tsx` | `mergeBackendEntries()` | 3,949 - 3,975 |
| `MonitoreoPage.tsx` | `pollBackend()` | 4,030 - 4,060 |
| `EntradaItem.tsx` | Renderizado de precio | 90 - 120 |

### 7. Conclusión Preliminar

**El cálculo de precios de entrada es CONSISTENTE entre vivo y backtesting.** Ambos usan `generar_entradas_multiples()` que calcula precios basados en niveles S/R, ATR, y otros factores técnicos.

**La discrepancia observada probablemente se debe a:**
1. **Filtros de deduplicación** muy agresivos (fingerprint collision)
2. **Expiración prematura** de entradas
3. **Filtros de UI** que ocultan entradas válidas
4. **Timing mismatch** entre generación y visualización

**NO es un problema de cálculo de precios**, sino de **flujo de datos y filtrado**.

### 8. Próximos Pasos Recomendados

1. **Agregar logging diagnóstico** en los puntos identificados
2. **Capturar traces completos** de una entrada desde generación hasta UI
3. **Comparar side-by-side** entradas generadas vs entradas mostradas
4. **Revisar thresholds** de fingerprint y expiración
5. **Verificar configuración** de filtros en UI

---

**Nota:** Este análisis se basó en revisión estática de código. Para diagnóstico preciso, se requiere instrumentación con logs y tracing en tiempo de ejecución.
