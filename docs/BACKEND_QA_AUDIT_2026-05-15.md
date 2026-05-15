# Backend QA Audit - 2026-05-15

Alcance: auditoria documental del backend, sin cambios de codigo, commit ni deploy. Fuentes revisadas: MarketTool.py, markettool/interfaces/api/monitoreo_routes.py, markettool/interfaces/api/mt5_routes.py, markettool/application/services/broker_mt5_service.py, mt5_bridge/MT5HttpBridge.mq5, Dockerfile, build-image.sh, scripts de deploy, compose local externo localnginx_balancer/maquina-a_test, y fix 6859f5b.

## 1. Contratos monitoreados

### POST /monitoreo/history

Entrada requerida:
- user_id: string no vacio. Si falta responde 400.
- exec_id: string no vacio. Si falta responde 400.
- symbol: string, se normaliza a uppercase. Si falta responde 400.
- timeframe: se normaliza con norm_tf, por ejemplo 1m -> 1min y 1h -> 1hour. Si falta responde 400.

Entrada opcional:
- limit: default 600, clamp 1..5000.
- from_ts, to_ts: epoch ms. Filtran la serie devuelta. Si ambos existen, el rango maximo es 365 dias.
- persist: bool. Solo marca st["dirty"]; el flujo de persistencia GCS de stream fue removido.
- force_api: bool. Fuerza fetch FMP por rango antes de responder.
- fill_gaps: bool. Ejecuta backfill interno de huecos, con max_minutes_per_call.
- max_minutes_per_call: default 10000, clamp 1..100000.

Comportamiento observado:
- Verifica si el TF esta habilitado por Firestore. Si esta deshabilitado, intenta renovar heartbeat y revalidar, salvo cuando Firestore refleja un stop explicito (`stopped/detenido/inactivo/access_denied/denied` o `enabled=false`). Si sigue deshabilitado, responde 200 con candles: [].
- Cobra cuota con charge_monitoreo_per_call(user_id, origen="app"). Sin saldo responde 402 con code: "INSUFFICIENT_TRANSACTIONS" y marca el TF como access_denied.
- Carga cache local/GCS por load_cache(exec_id, symbol, timeframe).
- Para 1min hace refresh directo desde FMP antes de leer la serie.
- Para TFs no 1min, usa maybe_refresh_from_gcs.
- Normaliza candles a OHLCV ms con series_to_ms; las respuestas deben tener t, o, h, l, c, y opcionalmente v.
- Si force_api o fill_gaps agregan velas, mergea por bucket con merge_bars_series y deduplica con snap_and_dedupe_to_minutes.

Respuesta 200 esperada:

    {
      "status": "ok",
      "symbol": "EURUSD",
      "timeframe": "1min",
      "exec_id": "exec123",
      "from_ts": 1778880000000,
      "to_ts": 1778883600000,
      "count": 600,
      "candles": [{"t": 1778880000000, "o": 1.1, "h": 1.2, "l": 1.0, "c": 1.15, "v": 1000}],
      "gapfill": {"requested": false, "force_api": false, "fill_gaps": false, "fetched": 0, "added": 0},
      "data_quality": {"timeframe": "1min", "candles": 600, "coverage_pct": 100.0}
    }

QA contract notes:
- candles is always a list, including empty responses.
- from_ts and to_ts echo the filtered output bounds, or the requested bound when no candle matches.
- data_quality is calculated over the returned filtered list, not necessarily the full cached series.
- No persisted_path should be expected by clients.

### POST /monitoreo/incremental

Entrada requerida:
- user_id, exec_id, symbol, timeframe with the same validation rules as history.

Entrada opcional:
- last_ts: epoch ms of the last candle known by the client. If missing or null, the endpoint returns the current base series.
- persist: bool. It can mark state dirty, but stream persistence to GCS has been removed.

Comportamiento observado:
- Cobra cuota before cache work. Same 402 insufficient-transactions response shape as history.
- If the TF is disabled, it tries heartbeat auto-renew unless Firestore has an explicit stop/access_denied/disabled state. If still disabled, returns 200 with empty candles.
- Loads cached series, optionally refreshes from GCS, and for 1min refreshes from FMP: 600 bars on cold request, 240 bars when last_ts exists.
- Cold start with an empty cache fetches historical data by ms range. Target bars: 1min=1500, 5min=900, 15min=750, 30min=600, 1hour=600, 4hour=480.
- Normalizes and dedupes candles into bucketed ms OHLCV and may densify small gaps.
- maybe_tick_quote can update the latest candle with a live quote.
- For 1min, comparison tolerance is 5000ms; other TFs use 1ms.

Respuesta 200 esperada:

    {
      "status": "ok",
      "symbol": "EURUSD",
      "timeframe": "1min",
      "exec_id": "exec123",
      "from_ts": 1778883600000,
      "to_ts": 1778883660000,
      "candles": [{"t": 1778883660000, "o": 1.1, "h": 1.2, "l": 1.0, "c": 1.15, "v": 0}],
      "data_quality": {"timeframe": "1min", "candles": 1500, "coverage_pct": 100.0},
      "cold_start": true,
      "empty_response": false
    }

QA contract notes:
- cold_start appears only when last_ts is missing and candles were returned.
- empty_response appears only when no incremental candle is returned.
- data_quality is calculated over the backend base series, not only over candles.
- The endpoint currently computes an inc_ms list for logging, but returns candles: inc.

## 2. calcular_entradas_async entry contract and fix 6859f5b

Signature:

    async def calcular_entradas_async(
        df: pd.DataFrame,
        df_eventos: pd.DataFrame,
        symbol: str,
        temporalidad: str,
        user_chat_id: str = None,
        *,
        calc_windows: dict[str, int] | None = None,
        cfg: dict | None = None,
    ) -> dict[str, Any]:

Input expectations:
- df must include a usable close series and an index that can participate in cache-key generation. ATR is optional.
- df_eventos is accepted as the event context for fundamental weighting.
- symbol and normalized temporalidad feed cache keys, S/R calculation, entries, leverage, and metadata.
- cfg and calc_windows are optional but affect deterministic cache keys and strategy thresholds.

Output expectations:
- Returns a JSON-safe dict with legacy fields such as tipo_operacion, probabilidad_*, precio_entrada, take_profit, stop_loss, S/R levels, leverage fields, confluencia, tecnica_meta, fundamental_meta, and entradas.
- entradas should be a list of entry dicts. If generar_entradas_multiples produces no entry but a finite legacy precio_entrada exists, the fallback wraps one entry:

    {
      "precio_entrada": 1.1,
      "take_profit": 1.2,
      "stop_loss": 1.0,
      "side": "long",
      "rrr": 2.0,
      "score": null,
      "basado_en": "legacy_fallback",
      "meta": {}
    }

Recent fix 6859f5b:
- Root cause: a nested helper named _finite inside calcular_entradas_async shadowed the global _finite. Python treated _finite as a local name throughout the function, so earlier ATR fallback code could crash with UnboundLocalError: cannot access local variable '_finite'.
- Fix: the nested fallback helper was renamed to _finite_local and all fallback calls were updated.
- QA implication: this prevents the shadowing crash, but it does not add a regression test. The risk remains test coverage, not the immediate code path.

## 3. Broker MT5 response fields expected by RN/Web

Backend MT5 flow:
- POST /api/v1/broker/mt5/place-order queues an order and currently returns status, orderId, and message.
- The EA polls POST /api/v1/broker/mt5/poll, executes the order, then posts result to POST /api/v1/broker/mt5/result.
- GET /api/v1/broker/mt5/order-status/<order_id> returns the stored EA result after completion.

Observed EA success result today:

    {
      "success": true,
      "order_id": "uuid",
      "lease_id": "uuid",
      "mt5_order_id": 123456789,
      "price": 1.09503,
      "volume": 0.10,
      "original_volume": 0.10,
      "sl_adjusted": false
    }

Client contract expected by RN/Web:
- openPrice: real broker fill price. Accepted aliases in clients include openPrice, open_price, and price.
- executedVolume: actual executed volume after broker normalization/reduction. Accepted aliases include executedVolume, executed_volume, and volume.
- openCommission: opening commission as a signed number, usually negative for cost. Accepted aliases include openCommission, open_commission, and commission.

Backend QA finding:
- price and volume are present in the EA result and can satisfy client aliases when clients poll order-status.
- openPrice and executedVolume camelCase aliases are not emitted by the backend today.
- openCommission/commission is not emitted by the MT5 EA/backend result today. RN/Web have fallbacks, but the backend contract should be explicit because bot risk guard, commission logs, and PnL reconciliation depend on this field.
- RN placeMT5Order currently returns only queued orderId from place-order; Web maps broker detail fields if the backend returns them. For MT5, final fill details are only available after polling order-status unless the backend changes to wait for completion.

Contract recommendation for docs/tests:

    {
      "status": "completed",
      "success": true,
      "mt5_order_id": 123456789,
      "price": 1.09503,
      "openPrice": 1.09503,
      "volume": 0.10,
      "executedVolume": 0.10,
      "original_volume": 0.10,
      "originalVolumeRequested": 0.10,
      "commission": -0.50,
      "openCommission": -0.50
    }

## 4. Docker/deploy flow observed

Repo-local build/runtime:
- Production Dockerfile builds markettool:latest, exposes 8080, sets healthcheck GET /healthz, and starts python -m markettool.bootstrap.
- build-image.sh builds markettool:latest and supports fast cached rebuilds by default.
- scripts/deployment/deploy.sh uses Dockerfile.optimized, runs a single container named markettool on 8080:8080, and verifies /healthz plus /ready.
- GKE manifests use image southamerica-west1-docker.pkg.dev/trading-449607/trading-repo/markettool:latest, port 8080, /healthz liveness, and /ready readiness.

Current local compose observed read-only:
- External compose path: /home/mtoro/projects/localnginx_balancer/maquina-a_test/docker-compose.yaml.
- Service app1 runs image markettool:latest, maps 8101:8080, depends on Redis, and healthchecks http://localhost:8080/healthz.
- nginx_local proxies to app1:8080 on port 8001.
- nginx_global proxies public traffic through the local upstream.
- /monitoreo/ routes have longer proxy timeouts: 180s global, 240s local internal, and disabled buffering.
- /api/v1/broker/mt5/ has short timeouts and no POST retry fanout, proxy_next_upstream_tries 1, to reduce duplicate order risk.

Deploy drift to track:
- Some older validation scripts still assume container port 5000, while the current Dockerfile and compose use 8080.
- The active compose is outside this repo, so repo-only docs/scripts can drift from actual production/local behavior.

## 5. Remaining backend QA risks requiring tests or live logs

1. Monitoring endpoint contract tests:
   - Add focused tests for required-field 400s, insufficient quota 402, disabled TF empty 200, last_ts delta behavior, cold_start, empty_response, force_api, fill_gaps, and 365-day range guard.
   - Validate data_quality semantics separately for history filtered output vs incremental base series.

2. calcular_entradas_async regression tests:
   - Add a test where ATR is missing/non-finite and the function reaches ATR fallback plus legacy fallback entry wrapping.
   - Assert no UnboundLocalError and assert entradas is always a list, including finite legacy fallback entries and exception fallbacks.

3. MT5 broker response contract:
   - Capture live /api/v1/broker/mt5/result and /order-status logs for a real/demo order.
   - Confirm fill price, executed volume, volume adjustment metadata, and commission are present with aliases expected by RN/Web.
   - If commission is unavailable from MT5, explicitly document/store backend fallback behavior so openCommission=0 is distinguishable from "not provided".

4. Deploy/runtime verification:
   - Keep /healthz and recent container logs in the QA checklist after rebuilds.
   - Reconcile old 5000-port validation scripts with the active 8080 runtime before relying on them as gates.
   - Because active compose lives outside this repo, production changes need a live compose/log check, not only repo inspection.

5. Live duplicate/staleness diagnostics:
   - For /monitoreo/history and /monitoreo/incremental, capture request IDs or correlation logs around exec_id/symbol/timeframe/last_ts during live RN/Web sessions.
   - Verify Nginx retries and frontend polling do not duplicate charges or re-emit stale candles under timeout/retry conditions.
