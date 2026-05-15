# MarketTool — Arquitectura Compartida
> Live Engine · Backtest · Dedup · TTL · Bugs históricos

---

## 1. Resumen Ejecutivo

MarketTool está formado por dos frontends (Web + React Native) que comparten **27 archivos byte-identical** para toda la lógica crítica: generación de entradas live, backtest, dedup, TTL y tipos. Un script `sync-shared.sh` mantiene la paridad entre repos. Cualquier cambio en un archivo compartido **debe aplicarse en ambos repos simultáneamente**.

---

## 2. Archivos Compartidos (27)

Ubicación en ambos repos: `src/` (relativo)

| Archivo RN/Web | Descripción |
|---|---|
| `utils/live/liveDedup.ts` | Fingerprint y set de dedup para entradas live |
| `utils/live/liveTTL.ts` | TTL por TF y utilidades de frescura de velas |
| `utils/live/types.ts` | Interface `LiveEntry` + `GenerateLiveEntriesOptions` |
| `utils/live/generateLiveEntries.ts` | Wrapper de `buildBacktestEntries` con liveWindow=3 |
| `utils/live/index.ts` | Re-exports del módulo live |
| `utils/liveSignalEngine.ts` | Motor de señales de proximidad (early detection) |
| `utils/backtest/buildBacktestEntries.ts` | Motor principal de backtest |
| `utils/backtest/breakerBlocks.ts` | Detección de Breaker Blocks |
| `utils/backtest/confluence.ts` | Score de confluencia |
| `utils/backtest/constants.ts` | Constantes del backtest |
| `utils/backtest/detectSignals.ts` | Detección técnica (RSI/MACD/BB/EMA/Stoch) |
| `utils/backtest/divergences.ts` | Divergencias RSI/MACD |
| `utils/backtest/economicEvents.ts` | Entradas por eventos económicos |
| `utils/backtest/fibonacci.ts` | Zonas Fibonacci |
| `utils/backtest/fvg.ts` | Fair Value Gaps / Imbalances |
| `utils/backtest/index.ts` | Re-exports del módulo backtest |
| `utils/backtest/indicators.ts` | ATR, S/R, RSI, MACD arrays |
| `utils/backtest/inducement.ts` | Inducement / Liquidity Pools |
| `utils/backtest/mtTraining.ts` | Entradas de entrenamiento MT |
| `utils/backtest/orderBlocks.ts` | Order Blocks + Wyckoff |
| `utils/backtest/outcome.ts` | Cálculo de outcome (TP/SL hit) |
| `utils/backtest/smc.ts` | SMC (BOS, CHoCH, Liquidity Sweep) |
| `utils/backtest/types.ts` | Types `BacktestEntry`, `Candle`, `BacktestOutcome` |
| `utils/botStats/sourceConstants.ts` | Constantes de fuentes/estrategias |
| `utils/botStats/computeLivePreviewStats.ts` | Stats en vivo para preview |
| `utils/botStats/computeBotCardStats.ts` | Stats para tarjetas de bot |
| `utils/botStats/index.ts` | Re-exports botStats |

### Script de sincronización

```bash
# Ubicación: markettool-web/scripts/sync-shared.sh
# (También existe en markettoolapp/scripts/sync-shared.sh)

./scripts/sync-shared.sh           # diff + sync RN → Web (default)
./scripts/sync-shared.sh --check   # solo diff, sin copiar
./scripts/sync-shared.sh --to-rn   # sync Web → RN
```

**Regla de oro:** Si editas cualquier archivo de la lista anterior, ejecuta `sync-shared.sh` antes de commitear.

---

## 3. Sistema Live — Tipos Clave

```typescript
// utils/live/types.ts
interface LiveEntry {
  id: string;
  symbol: string;
  timeframe: string;
  side: 'long' | 'short';
  entry_price: number;
  take_profit: number;
  stop_loss: number;
  rrr: number;
  confluence_score: number;
  source?: string;
  status?: string;
  outcome?: string;
  created_at?: string;   // ISO string — base del TTL
  timestamp?: number;    // epoch ms
  _origin?: 'live';      // 'live' | 'gcs' | undefined
  early_detection?: boolean; // señal de proximidad, opt-in
}
```

---

## 4. TTL por Timeframe (`liveTTL.ts`)

| TF | TTL |
|---|---|
| 1m | 30 minutos |
| 5m | 2 horas |
| 15m | 6 horas |
| 30m | 12 horas |
| 1h | 24 horas |
| 4h | 3 días |
| 1d | 7 días |
| 1w | 30 días |

```typescript
export function isLiveEntryExpired(createdAt, timeframe): boolean
export function areCandlesFresh(lastCandleTs, tf): boolean
// "fresco" = lastCandle dentro de 2× TF duration
```

---

## 5. Dedup (`liveDedup.ts`)

```typescript
// Fingerprint: symbol|timeframe|source|side|roundedPrice(1e5)
export function liveFingerprint(e): string

// Set de fingerprints activos; pruneStale() limpia los expirados
export class LiveFingerprintSet { has/add/pruneStale/clear }
```

---

## 6. `generateLiveEntries` — Flujo Principal

```
generateLiveEntries(symbol, tf, candles, trainingData, events)
  │
  ├─ areCandlesFresh(lastCandle.t, tf)?  NO → return []
  │
  └─ buildBacktestEntries(symbol, tf, candles, trainingData, events,
                          skipOutcome=true, liveWindow=3)
       │
       └─ map cada BacktestEntry → toEntradaViva()
            { outcome='pending', _origin='live', created_at=now }
```

`liveWindow=3` significa que el engine solo genera entradas sobre las **últimas 3 velas**, usando todas las velas anteriores para calcular indicadores.

---

## 7. `buildBacktestEntries` — Fuentes de Señal

| # | Fuente | Módulo |
|---|---|---|
| 1 | Técnico (RSI/MACD/BB/EMA/Stoch) | `detectSignals.ts` |
| 1.5 | Candle Strategies (EMA 3/9, triada) | `candleStrategyEngine.ts` |
| 2 | S/R bounce | `indicators.ts` (`calcSRLevels`) |
| 2.5 | Order Blocks | `orderBlocks.ts` |
| 2.6 | FVG/Imbalance | `fvg.ts` |
| 2.7 | SMC (BOS/CHoCH/Sweep) | `smc.ts` |
| 2.8 | Breaker Blocks | `breakerBlocks.ts` |
| 2.9 | Inducement/Liquidity | `inducement.ts` |
| 2.10 | Divergencias RSI/MACD | `divergences.ts` |
| 2.11 | Confluence Mega Setups | `confluence.ts` |
| 2.12 | Fibonacci Zones | `fibonacci.ts` |
| 3 | MT Training | `mtTraining.ts` (requiere Firestore) |
| 4 | Economic Events | `economicEvents.ts` (requiere data externa) |

Parámetros clave:
- `liveWindow`: si se pasa, solo itera sobre las últimas N velas para generación (usa toda la historia para indicadores)
- `skipOutcome=true`: en modo live no se computa TP/SL hit (candles futuras no existen)
- `unlimitedCandles=true`: salta límites por TF (usado en backtest histórico completo)

---

## 8. `liveSignalEngine.ts` — Diferencia con Backtest

`liveSignalEngine.ts` es un motor **paralelo e independiente** al backtest. Opera sobre `calcSRLevels` y genera señales de proximidad ("precio cerca de un nivel"). Sus salidas se marcan con `early_detection: true`.

| Aspecto | `buildBacktestEntries` | `liveSignalEngine.ts` |
|---|---|---|
| Uso principal | Backtest + live window | Early Detection (proximidad) |
| Validación histórica | Sí (touchCount, retoque) | No |
| Presente en RN | Sí | Sí (ported) |
| Marcado | `_origin:'live'` | `early_detection:true` |
| Incluido en globals | Sí (vía pollIncremental) | Opt-in checkbox |

---

## 9. Bugs Históricos y sus Fixes

| # | Bug | Causa raíz | Fix | Commit |
|---|---|---|---|---|
| 1 | **CRYPTO_ALWAYS_ON_RE regex** | `\b` (word boundary) falla con `BTCUSD` — BTC seguido de USD sin separador. Todos los crypto eran tratados como non-24/7, generando falsos "mercado inactivo" | Cambiar regex a `^(BTC|ETH|...)([^A-Za-z]\|[A-Za-z]{2,}\|$)/i` para match de prefijo | Web: `d72f777`, RN: `028e945` |
| 2 | **Double-merge GCS** | GCS entries se mergeaban directo a `liveEntriesByTf` Y también pasaban por `useLiveSignalEngine` → `liveEngineEntries`. Al mergear ambas en globals, había duplicados | GCS entries → solo a `setEntries()` (alimentan el engine). NO mergear GCS directo a `liveEntriesByTf` | Web: `6346e44`, `a62c842` |
| 3 | **trainingData global vs per-TF** | `useLiveSignalEngine` usaba un único `trainingData` para todos los TF. RN usa `mtEntriesBySymbolTfRef[sym][tf]` (per-TF). Discrepancia en calidad de señales | Agregar `trainingDataByTf: Record<string, MTResumen>` al hook; usar `trainingDataByTfRef.current?.[tf] ?? trainingDataRef.current` | Web: `c7bc87e` |
| 4 | **Acumulación excesiva de entradas** | `liveEngineEntries` (useLiveSignalEngine) producía ~28 entradas S/R vs ~3 en RN porque mergeaba un motor extra. Inflaba todos los contadores | Remover `liveEngineEntries` del merge a `_globalEntries`. Solo usar para bot-hydration y win-rate stats | Web: `4240ea3` |
| 5 | **TTL en display time** | Web no aplicaba filtro TTL al construir `_globalEntries`. RN sí lo aplica en `createFilteredEntries`. Entradas caducadas aparecían en el panel | Aplicar filtro TTL usando `LIVE_TTL_BY_TF` al construir `_globalEntries` en render | Web: `152481f` |
| 6 | **Filtro por TFs monitoreados** | Web mostraba entradas de todos los TFs en `_globalEntries`. RN solo muestra las de `monitorSel` (TFs activos). Discrepancia en conteo de entradas globales | Filtrar `_globalEntries` por `engineTFs` (TFs monitoreados actualmente) | Web: `c674bc7` |
| 7 | **Infinite render loop** | `useEarlyDetectionEngine` recibía `monitoredTFs` y `tfCandlesMap` como deps del `useCallback` principal. Estos arrays/objetos se recrean en cada render → loop infinito | Usar refs (`monitoredTFsRef`, `tfCandlesMapRef`, `trainingDataRef`) para las deps volátiles; `useCallback` solo depende de `[enabled, symbol]` (estables) | Web: `17f467a`, RN: `4cd41cc` |
| 8 | **`created_at` faltante en earlyDetection** | `useEarlyDetectionEngine` no asignaba `created_at` a las entries generadas. TTL no podía calcularse → entries nunca expiraban | Asignar `created_at: new Date(sig.timestamp > 0 ? sig.timestamp : Date.now()).toISOString()` | Web: `92ae2ec` |
| 9 | **GCS entries sin `outcome:'pending'`** | GCS entries guardaban el `outcome` del backend (puede ser 'win'/'loss' de análisis previos). En contexto live, el outcome debe ser siempre 'pending' | Forzar `outcome: 'pending'` al cargar GCS entries. También forzar `_origin: 'gcs'` y `created_at: Date.now()` para que el TTL no expire inmediatamente | Web: `a62c842`, RN: `830e671` |
| 10 | **Live window entries sin `outcome:'pending'`** | `toEntradaViva()` no forzaba `outcome:'pending'` en entries generadas por `generateLiveEntries`. En RN se homologó explícitamente | Forzar `outcome: 'pending'` + `_origin: 'live'` en `toEntradaViva()` | RN: `dd4c3ed` |

---

## 10. Patrones de Código Importantes

### Refs anti-loop infinito
```typescript
// Problema: arrays/objetos como deps → nuevo ref en cada render → loop
const monitoredTFsRef = useRef(monitoredTFs);
monitoredTFsRef.current = monitoredTFs; // siempre actualizado
const tfCandlesMapRef = useRef(tfCandlesMap);
tfCandlesMapRef.current = tfCandlesMap;

// useCallback solo depende de valores estables
const runEngine = useCallback(() => {
  const _tfs = monitoredTFsRef.current; // leer desde ref
  // ...
}, [enabled, symbol]); // NO arrays/objetos
```

### trainingDataByTf pattern
```typescript
// RN: mtEntriesBySymbolTfRef[sym][tf] — training data específico por TF
// Web (c7bc87e): trainingDataByTfRef.current?.[tf] ?? trainingDataRef.current
const tfTrainingData = trainingDataByTfRef.current?.[tf] ?? trainingDataRef.current ?? null;
```

### liveFingerprint dedup
```typescript
// Format: "EURUSD|15m|sr|long|123456789"
const fp = liveFingerprint({ symbol, timeframe, source, side, entry_price });
if (seenFPs.has(fp)) continue; // dedup exacto
seenFPs.add(fp);
```

---

## 11. GCS Bucket

- **Bucket:** `markettool_bucket`
- **Path patrón:** `analisis/exec/{execId}/{symbol}_{tfNorm}_enriched.json`
- **TF normalization:**
  - `1m` → `1min`
  - `5m` → `5min`
  - `15m` → `15min`
  - `30m` → `30min`
  - `1h` → `1hour`
  - `4h` → `4hour`
  - `1d` → `1day`
  - `1w` → `1week`
- **URL base:** `https://storage.googleapis.com/markettool_bucket/`
- **ExecId activo:** `c4498ac8a36f422a94811af063b1cb1d`

---

## 12. Backend API

### `GET /monitoreo/live-candle`
- **Params:** `symbol`, `timeframe` (formato `1min`, `5min`, etc.)
- **Retorna:** `{ candle: { t, o, h, l, c, v } }` — la última vela cerrada
- **Uso:** `pollIncremental` para actualización incremental de candles
- **Timeout:** 8 segundos
- **Base URL:** `https://api.mtlabsx.com`

---

## 13. Backend QA Audit — 2026-05-15

Detalle completo: [BACKEND_QA_AUDIT_2026-05-15.md](./BACKEND_QA_AUDIT_2026-05-15.md)

### Contratos monitoreados

- `POST /monitoreo/incremental`: requiere `user_id`, `exec_id`, `symbol`, `timeframe`; opcionales `last_ts` y `persist`. Respuesta nominal: `status`, `symbol`, `timeframe`, `exec_id`, `from_ts`, `to_ts`, `candles`, `data_quality`; puede agregar `cold_start` o `empty_response`.
- `POST /monitoreo/history`: requiere los mismos campos; opcionales `limit`, `from_ts`, `to_ts`, `persist`, `fill_gaps`, `force_api`, `max_minutes_per_call`. Respuesta nominal: `status`, `symbol`, `timeframe`, `exec_id`, `from_ts`, `to_ts`, `count`, `candles`, `gapfill`, `data_quality`.
- Ambas rutas devuelven `402 INSUFFICIENT_TRANSACTIONS` cuando falla cuota y pueden devolver `status=ok` con `candles=[]` si el TF queda deshabilitado o detenido explicitamente.

### `calcular_entradas_async`

- Entrada: `df`, `df_eventos`, `symbol`, `temporalidad`, `user_chat_id?`, `calc_windows?`, `cfg?`.
- Salida nominal: formato legacy + `entradas` como lista de candidatos. Si no hay `entradas_mult` pero existe `precio_entrada` finito, empaqueta fallback `legacy_fallback`.
- Fix `6859f5b`: renombra el helper local `_finite` a `_finite_local` para que el calculo ATR use el helper global y no dispare `UnboundLocalError`.

### Campos de broker esperados

RN/Web esperan que la apertura o el status final preserven:

| Campo canonico | Aliases tolerados | Motivo |
|---|---|---|
| `openPrice` | `open_price`, `price` | Fill real para PnL/risk guard. |
| `executedVolume` | `executed_volume`, `volume` | Volumen real tras ajuste del broker. |
| `openCommission` | `open_commission`, `commission` | Comision de apertura para `commPct` y PnL neto. |

### Riesgos QA pendientes

- Faltan tests de contrato para `history/incremental` con cuota, TF deshabilitado, cold-start, rangos y `data_quality`.
- Faltan tests de regresion para `calcular_entradas_async` con ATR faltante/no finito y shape `entradas=[]` en fallback/exception.
- Faltan logs reales que prueben propagacion broker -> backend -> RN/Web de `openPrice`, `openCommission` y `executedVolume`.
- El compose vivo esta fuera del repo (`localnginx_balancer/maquina-a_test`); los scripts historicos del repo no representan por completo el deploy actual.
