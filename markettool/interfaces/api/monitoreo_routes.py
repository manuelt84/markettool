"""Monitoreo API routes."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime, timedelta
pass

pass
from flask import jsonify, request

from markettool.application.use_cases.legacy import LegacyMonitoreoUseCase
from markettool.core.cache_config import get_freshness_requirement_for_timeframe
from markettool.infra.fmp.ledger import fmp_context


def register_monitoreo_routes(app, *, services) -> None:
    use_case = LegacyMonitoreoUseCase(services)
    logger = services.logger
    db = services.db
    charge_monitoreo_per_call = services.charge_monitoreo_per_call
    fetch_events_for = services.fetch_events_for
    filter_by_symbol_currencies = services.filter_by_symbol_currencies
    hash_payload = services.hash_payload
    last_hash_ref = services.last_hash_ref
    detect_new_results = services.detect_new_results
    evaluar_evento_para_symbol = services.evaluar_evento_para_symbol
    norm_tf = services.norm_tf
    tf_is_enabled = services.tf_is_enabled
    load_cache = services.load_cache
    series_to_ms = services.series_to_ms
    snap_and_dedupe_to_minutes = services.snap_and_dedupe_to_minutes
    densify_minutes = services.densify_minutes
    maybe_tick_quote = services.maybe_tick_quote
    mon_cache_lock = services.mon_cache_lock
    maybe_refresh_from_gcs = services.maybe_refresh_from_gcs
    fs_touch_monitoreo = services.fs_touch_monitoreo
    tf_ms = services.tf_ms
    current_closed_bucket_start = services.current_closed_bucket_start
    fetch_historical_range = services.fetch_historical_range
    merge_bars_series = services.merge_bars_series
    backfill_internal_gaps = services.backfill_internal_gaps
    bucket_name = services.bucket_name
    max_history_window_ms = int(timedelta(days=365).total_seconds() * 1000)

    def _parse_int_field(value, default: int, *, min_value: int | None = None, max_value: int | None = None):
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return default

        if min_value is not None:
            parsed = max(min_value, parsed)
        if max_value is not None:
            parsed = min(max_value, parsed)
        return parsed

    def _quota_error_response(message: str):
        return (
            jsonify(
                {
                    "status": "error",
                    "code": "INSUFFICIENT_TRANSACTIONS",
                    "error_code": "transacciones_insuficientes",
                    "message": message,
                }
            ),
            402,
        )

    def _tf_has_explicit_stop(exec_id: str, symbol: str, timeframe: str) -> bool:
        try:
            doc_id = f"{exec_id}__{(symbol or '').upper()}"
            snap = db.collection("monitoreos").document(doc_id).get()
            if not snap.exists:
                return False
            data = snap.to_dict() or {}
            tf_states = data.get("tf_states") or {}
            tf_canonical = norm_tf(timeframe)
            state = (
                tf_states.get(tf_canonical)
                or tf_states.get(timeframe)
                or data.get(tf_canonical)
                or data.get(timeframe)
                or {}
            )
            if not isinstance(state, dict):
                return False
            estado = str(state.get("estado") or state.get("status") or "").strip().lower()
            reason = str(state.get("reason") or data.get("last_reason") or "").strip().lower()
            stop_reason = str(state.get("stop_reason") or "").strip().lower()
            if (
                estado in {"access_denied", "denied"}
                or "cuota" in reason
                or "transacciones" in reason
                or "quota" in reason
                or stop_reason == "subscription_inactive_or_expired"
            ):
                return False
            if estado in {"stopped", "detenido", "inactivo", "access_denied", "denied"}:
                return True
            return state.get("enabled") is False
        except Exception as exc:
            logger.debug(
                "TF explicit stop lookup failed exec=%s symbol=%s tf=%s: %s",
                exec_id,
                symbol,
                timeframe,
                exc,
            )
            return False

    def _tf_access_denied_reason(exec_id: str, symbol: str, timeframe: str) -> str | None:
        try:
            doc_id = f"{exec_id}__{(symbol or '').upper()}"
            snap = db.collection("monitoreos").document(doc_id).get()
            if not snap.exists:
                return None
            data = snap.to_dict() or {}
            tf_states = data.get("tf_states") or {}
            tf_canonical = norm_tf(timeframe)
            state = (
                tf_states.get(tf_canonical)
                or tf_states.get(timeframe)
                or data.get(tf_canonical)
                or data.get(timeframe)
                or {}
            )
            if not isinstance(state, dict):
                return None

            estado = str(state.get("estado") or state.get("status") or "").strip().lower()
            stop_reason = str(state.get("stop_reason") or "").strip().lower()
            reason = str(state.get("reason") or data.get("last_reason") or "").strip()
            reason_l = reason.lower()

            is_access_denied = estado in {"access_denied", "denied"}
            is_quota_reason = (
                "cuota" in reason_l
                or "transacciones" in reason_l
                or "quota" in reason_l
                or stop_reason == "subscription_inactive_or_expired"
            )
            if is_access_denied or is_quota_reason:
                return reason or "No cuenta con la cuota de transacciones requerida. Por favor, adquiere un paquete."
            return None
        except Exception as exc:
            logger.debug(
                "TF access_denied lookup failed exec=%s symbol=%s tf=%s: %s",
                exec_id,
                symbol,
                timeframe,
                exc,
            )
            return None

    async def _mark_tf_stopped_access_denied(
        exec_id: str,
        symbol: str,
        timeframe: str,
        user_id: str,
        reason: str,
    ) -> None:
        try:
            tf_value = norm_tf(timeframe)
            now_ms = int(time.time() * 1000)
            await asyncio.to_thread(
                fs_touch_monitoreo,
                exec_id,
                symbol,
                {
                    "user_id": user_id,
                    "last_reason": reason,
                    "tf_states": {
                        tf_value: {
                            "enabled": False,
                            "estado": "access_denied",
                            "reason": reason,
                            "last_heartbeat_ms": now_ms,
                            "last_ts": now_ms,
                        }
                    },
                },
            )
        except Exception:
            logger.exception(
                "No se pudo marcar monitoreo como access_denied para %s/%s/%s",
                exec_id,
                symbol,
                timeframe,
            )

    def _hist_df_to_series_ms(hist_df) -> list[dict]:
        if hist_df is None or getattr(hist_df, "empty", False):
            return []

        hist_df_copy = hist_df.copy()
        try:
            timestamps_ms = (hist_df_copy.index.astype("int64") // 1_000_000).astype(int)
        except Exception:
            timestamps_ms = [int(getattr(idx, "timestamp")() * 1000) for idx in hist_df_copy.index]

        return [
            {
                "t": int(t_ms),
                "o": float(hist_df_copy.iloc[i].get("open", 0) or 0),
                "h": float(hist_df_copy.iloc[i].get("high", 0) or 0),
                "l": float(hist_df_copy.iloc[i].get("low", 0) or 0),
                "c": float(hist_df_copy.iloc[i].get("close", 0) or 0),
                "v": float(hist_df_copy.iloc[i].get("volume", 0) or 0)
                if "volume" in getattr(hist_df_copy, "columns", [])
                else None,
            }
            for i, t_ms in enumerate(timestamps_ms)
        ]

    def _history_payload_to_series_ms(payload, timeframe: str) -> list[dict]:
        """Normalize historical payload into ms OHLCV series.

        Supports both payload styles currently used by legacy services:
        - pandas.DataFrame (index datetime)
        - list[dict] with candles
        """
        tf_value = norm_tf(timeframe)
        if payload is None:
            return []

        try:
            if isinstance(payload, (list, tuple)):
                return snap_and_dedupe_to_minutes(series_to_ms(list(payload)), tf_value)

            if hasattr(payload, "empty") and hasattr(payload, "copy"):
                return snap_and_dedupe_to_minutes(_hist_df_to_series_ms(payload), tf_value)

            return snap_and_dedupe_to_minutes(series_to_ms(payload), tf_value)
        except Exception:
            logging.exception("Error normalizando payload historico (%s)", tf_value)
            return []

    def _build_timeframe_data_quality(series_ms, timeframe: str) -> dict:
        """Build a lightweight data-quality summary for a timeframe.

        This helper is intentionally defensive because input can be partial or malformed
        while backfill/incremental updates are in progress.
        """
        tf_value = norm_tf(timeframe)
        tf_value_ms = tf_ms(tf_value)
        rows = list(series_ms or [])

        if not rows:
            return {
                "timeframe": tf_value,
                "candles": 0,
                "is_empty": True,
                "first_ts": None,
                "last_ts": None,
                "expected_candles": 0,
                "coverage_ratio": 0.0,
                "coverage_pct": 0.0,
                "complete_ohlc_count": 0,
                "ohlc_completeness_ratio": 0.0,
                "ohlc_completeness_pct": 0.0,
                "staleness_ms": None,
                "staleness_bars": None,
            }

        ts_values = []
        complete_ohlc_count = 0

        for row in rows:
            if not isinstance(row, dict):
                continue

            try:
                t_raw = row.get("t")
                t_val = int(t_raw) if t_raw is not None else None
            except Exception:
                t_val = None

            if t_val is not None:
                ts_values.append(t_val)

            has_all_ohlc = all(row.get(k) is not None for k in ("o", "h", "l", "c"))
            if has_all_ohlc:
                complete_ohlc_count += 1

        if not ts_values:
            return {
                "timeframe": tf_value,
                "candles": len(rows),
                "is_empty": len(rows) == 0,
                "first_ts": None,
                "last_ts": None,
                "expected_candles": 0,
                "coverage_ratio": 0.0,
                "coverage_pct": 0.0,
                "complete_ohlc_count": complete_ohlc_count,
                "ohlc_completeness_ratio": round(complete_ohlc_count / max(1, len(rows)), 4),
                "ohlc_completeness_pct": round((complete_ohlc_count / max(1, len(rows))) * 100.0, 2),
                "staleness_ms": None,
                "staleness_bars": None,
            }

        first_ts = min(ts_values)
        last_ts = max(ts_values)
        span_ms = max(0, last_ts - first_ts)

        expected_candles = len(ts_values)
        coverage_ratio = 1.0
        if tf_value_ms and tf_value_ms > 0:
            expected_from_span = int(span_ms // tf_value_ms) + 1
            expected_candles = max(1, expected_from_span)
            coverage_ratio = min(1.0, len(ts_values) / max(1, expected_candles))

            try:
                closed_bucket = current_closed_bucket_start(tf_value)
                staleness_ms = max(0, int(closed_bucket) - int(last_ts))
                staleness_bars = int(staleness_ms // tf_value_ms)
            except Exception:
                staleness_ms = None
                staleness_bars = None
        else:
            staleness_ms = None
            staleness_bars = None

        ohlc_ratio = complete_ohlc_count / max(1, len(rows))

        return {
            "timeframe": tf_value,
            "candles": len(rows),
            "is_empty": False,
            "first_ts": first_ts,
            "last_ts": last_ts,
            "expected_candles": expected_candles,
            "coverage_ratio": round(coverage_ratio, 4),
            "coverage_pct": round(coverage_ratio * 100.0, 2),
            "complete_ohlc_count": complete_ohlc_count,
            "ohlc_completeness_ratio": round(ohlc_ratio, 4),
            "ohlc_completeness_pct": round(ohlc_ratio * 100.0, 2),
            "staleness_ms": staleness_ms,
            "staleness_bars": staleness_bars,
        }

    async def _refresh_1min_series_from_fmp(
        symbol: str,
        timeframe: str,
        st: dict,
        *,
        limit: int = 240,
        from_ts: int | None = None,
        to_ts: int | None = None,
    ) -> bool:
        tf_value = norm_tf(timeframe)
        if tf_value != "1min" or not symbol:
            return False

        try:
            tf_value_ms = tf_ms(tf_value)
            if not tf_value_ms:
                return False

            limit_i = _parse_int_field(limit, 240, min_value=1, max_value=5000)
            closed_end = current_closed_bucket_start(tf_value) - tf_value_ms

            from_eff = from_ts if from_ts is not None else (closed_end - (limit_i - 1) * tf_value_ms)
            to_eff = to_ts if to_ts is not None else closed_end

            to_eff = min(to_eff, closed_end)
            if from_eff > to_eff:
                from_eff, to_eff = to_eff, from_eff

            fetch_to = min(to_eff + tf_value_ms, closed_end + tf_value_ms)
            with fmp_context(usage_kind="monitoring_history_refresh", source="monitoreo_1min_refresh", symbol=symbol, timeframe=tf_value):
                hist_payload = await asyncio.to_thread(
                    fetch_historical_range,
                    symbol,
                    tf_value,
                    from_eff,
                    fetch_to,
                )
            hist_series = _history_payload_to_series_ms(hist_payload, tf_value)
            if not hist_series:
                return False

            with mon_cache_lock:
                st["series"] = hist_series
                st["source"] = "fmp-1min"

            logging.info(
                "1MIN FMP REFRESH %s: loaded %d candles directly from FMP",
                symbol,
                len(hist_series),
            )
            return True
        except Exception as exc:
            logging.warning("1MIN FMP REFRESH %s failed: %s", symbol, exc)
            return False

    @app.route("/monitoreo/eventos", methods=["POST"])
    async def monitoreo_eventos():
        """
        POST /monitoreo/eventos
        Body:
          {
            "user_id": "...",
            "exec_id": "...",
            "symbol": "EURUSD",
            "hours_back": 6,        # opcional (default 6)
            "minutes_fwd": 5,       # opcional (default 5)
            "cursor_hash": "..."    # opcional: hash del ultimo snapshot recibido por el front
          }
        """
        payload, status = await use_case.eventos(request.get_json(force=True) or {})
        return jsonify(payload), status

    @app.route("/monitoreo/incremental", methods=["POST"])
    async def monitoreo_incremental():
        start = time.time()
        try:
            body = request.get_json(force=True) or {}
            user_id = str(body.get("user_id") or "").strip()
            exec_id = str(body.get("exec_id") or "").strip()
            symbol = str(body.get("symbol") or "").strip().upper()
            timeframe = norm_tf(body.get("timeframe"))
            last_ts = body.get("last_ts")
            persist = bool(body.get("persist", False))

            if not user_id:
                return jsonify({"status": "error", "message": "user_id es obligatorio"}), 400
            if not exec_id:
                return jsonify({"status": "error", "message": "exec_id es obligatorio"}), 400
            if not symbol or not timeframe:
                return (
                    jsonify(
                        {
                            "status": "error",
                            "message": "symbol y timeframe son obligatorios",
                        }
                    ),
                    400,
                )

            ok, msg = await charge_monitoreo_per_call(user_id, origen="app")
            if not ok:
                await _mark_tf_stopped_access_denied(
                    exec_id=exec_id,
                    symbol=symbol,
                    timeframe=timeframe,
                    user_id=user_id,
                    reason=msg,
                )
                return _quota_error_response(msg)

            logging.info(
                "INC START user=%s exec=%s body=%s",
                body.get("user_id"),
                body.get("exec_id"),
                body,
            )

            tf_api = timeframe
            enabled = True
            if exec_id:
                enabled = tf_is_enabled(exec_id, symbol, tf_api)

            logging.info(
                "INC TFCHK sym=%s tf=%s enabled=%s user_id=%s exec_id=%s",
                symbol,
                timeframe,
                enabled,
                user_id,
                exec_id,
            )

            # Auto-renovar heartbeat solo si está cerca de expirar (optimización de Firestore)
            # Simplificado: siempre renovar si enabled es False, para recuperación rápida
            if exec_id and symbol and not enabled and not _tf_has_explicit_stop(exec_id, symbol, tf_api):
                try:
                    now_ms = int(time.time() * 1000)
                    
                    heartbeat_data = {
                        "tf_states": {
                            tf_api: {
                                "enabled": True,
                                "estado": "running",
                                "reason": None,
                                "stop_reason": None,
                                "last_heartbeat_ms": now_ms,
                                "last_ts": now_ms,
                            }
                        }
                    }
                    await asyncio.to_thread(fs_touch_monitoreo, exec_id, symbol, heartbeat_data)
                    enabled = tf_is_enabled(exec_id, symbol, tf_api)
                except Exception as e:
                    logging.warning("INC AUTO-RENEW fallido para %s %s: %s", symbol, tf_api, e)

            if not enabled:
                denied_reason = _tf_access_denied_reason(exec_id, symbol, tf_api)
                if denied_reason:
                    return _quota_error_response(denied_reason)
                return (
                    jsonify(
                        {
                            "status": "ok",
                            "symbol": symbol,
                            "timeframe": timeframe,
                            "exec_id": exec_id,
                            "from_ts": None,
                            "to_ts": None,
                            "candles": [],
                        }
                    ),
                    200,
                )

            st: dict = await asyncio.to_thread(load_cache, exec_id, symbol, timeframe)
            prev_series_ms = series_to_ms(st.get("series", []))
            prev_last = prev_series_ms[-1] if prev_series_ms else None

            age = {
                "1min": 20,
                "5min": 60,
                "15min": 180,
                "30min": 300,
                "1hour": 600,
                "4hour": 900,
            }.get(timeframe, 300)

            try:
                await asyncio.to_thread(
                    maybe_refresh_from_gcs, exec_id, symbol, timeframe, st, age
                )
            except Exception:
                logging.exception(
                    "INC %s %s: maybe_refresh_from_gcs fallo", symbol, timeframe
                )

            if norm_tf(timeframe) == "1min":
                warm_limit = 600 if last_ts is None else 240
                await _refresh_1min_series_from_fmp(symbol, timeframe, st, limit=warm_limit)

            # NOTE: ensure_stream_initialized removed - no longer maintains GCS stream
            # All real-time data flows through Firestore

            # ============ COLD-START FIX: If cache is empty, fetch historical data ============
            cache_series = st.get("series", []) or []
            if not cache_series or len(cache_series) == 0:
                logging.info(
                    "INC %s %s: COLD-START detected (empty cache), fetching historical...",
                    symbol,
                    timeframe,
                )
                try:
                    # Cold-start must use the same ms-range contract as incremental/history routes.
                    tf_value = norm_tf(timeframe)
                    tf_value_ms = tf_ms(tf_value)
                    if not tf_value_ms:
                        raise ValueError(f"tf_ms invalido para {timeframe}")

                    bars_target = {
                        "1min": 1500,
                        "5min": 900,
                        "15min": 750,
                        "30min": 600,
                        "1hour": 600,
                        "4hour": 480,
                    }.get(tf_value, 600)

                    closed_end = current_closed_bucket_start(tf_value) - tf_value_ms
                    hist_from_ms = closed_end - (bars_target - 1) * tf_value_ms
                    hist_to_ms = min(closed_end + tf_value_ms, int(time.time() * 1000))

                    with fmp_context(usage_kind="monitoring_history_refresh", source="monitoreo_cold_start", symbol=symbol, timeframe=tf_value):
                        hist_payload = await asyncio.to_thread(
                            fetch_historical_range,
                            symbol,
                            tf_value,
                            hist_from_ms,
                            hist_to_ms,
                        )

                    hist_series = _history_payload_to_series_ms(hist_payload, tf_value)
                    if hist_series:
                        st["series"] = hist_series
                        cache_series = hist_series
                        logging.info(
                            "INC %s %s: Cold-start fetched %d historical candles",
                            symbol,
                            timeframe,
                            len(hist_series),
                        )
                    else:
                        logging.warning(
                            "INC %s %s: Cold-start fetch returned empty payload",
                            symbol,
                            timeframe,
                        )
                except Exception as e:
                    logging.exception(
                        "INC %s %s: Cold-start fetch failed: %s",
                        symbol,
                        timeframe,
                        str(e),
                    )
                    # Continue with empty series - frontend will retry

            try:
                last_ts = int(last_ts) if last_ts is not None else None
            except Exception:
                last_ts = None

            base_ms = series_to_ms(st.get("series", []) or [])
            base_ms = snap_and_dedupe_to_minutes(base_ms, timeframe)

            try:
                base_ms = densify_minutes(base_ms, timeframe)
            except Exception:
                logging.exception("INC %s %s: densify_minutes fallo", symbol, timeframe)

            st["series"] = base_ms

            try:
                changed_tick = await asyncio.to_thread(
                    maybe_tick_quote, exec_id, symbol, timeframe, st
                )
                if changed_tick:
                    base_ms = snap_and_dedupe_to_minutes(
                        series_to_ms(st.get("series", [])), timeframe
                    )
            except Exception:
                logging.exception("INC %s %s: maybe_tick_quote fallo", symbol, timeframe)

            last_server = base_ms[-1] if base_ms else None
            last_server_t = (
                int(last_server.get("t")) if last_server and "t" in last_server else None
            )

            changed_by_reload = False
            if (
                prev_last
                and last_server
                and last_server_t
                and last_server_t > int(prev_last.get("t", 0))
            ):
                changed_by_reload = True
            elif prev_last and last_server:
                for key in ("o", "h", "l", "c", "v"):
                    try:
                        if float(last_server.get(key, 0)) != float(
                            prev_last.get(key, 0)
                        ):
                            changed_by_reload = True
                            break
                    except Exception:
                        continue

            changed = (
                changed_by_reload
                or (not prev_last and bool(last_server))
                or (
                    prev_last
                    and last_server
                    and last_server_t
                    and last_server_t > int(prev_last.get("t", 0))
                )
            )

            # 1MIN HOTFIX: Increase tolerance for 1m to handle Firestore write delays (50-500ms)
            eps = 5000 if timeframe == "1min" else 1
            
            if last_ts is None:
                inc = base_ms
            elif last_server_t is not None and last_server_t > last_ts + eps:
                # New data is available beyond the tolerance window
                inc = [c for c in base_ms if int(c.get("t", 0)) > last_ts]
            else:
                # Within tolerance: include last_server if it's changed and within eps window
                inc = (
                    [last_server]
                    if (
                        changed
                        and last_server_t
                        and last_server_t >= (last_ts or 0) - eps
                    )
                    else []
                )
                # 1MIN HOTFIX: If we're waiting for a new 1m candle and cache shows no change,
                # double-check by looking at time gap - if >= 60s, last_server might be valid
                # ✅ FIX: Only return last_server if price ACTUALLY CHANGED (not just timestamp)
                if (
                    timeframe == "1min"
                    and not inc
                    and last_ts
                    and last_server_t
                    and (last_server_t - last_ts) >= 55000  # Within 5 seconds of expected 60s boundary
                    and changed  # ← Added: Validate actual price change, not just time gap
                ):
                    inc = [last_server]  # Return the last_server only if "changed" is True
                    logging.info(
                        "INC 1min HOTFIX %s: Returning last_server (time gap %.0fs + price change)",
                        symbol,
                        (last_server_t - last_ts) / 1000.0
                    )

            logging.info(
                "INC %s %s last_ts=%s last_server_t=%s changed=%s -> inc_len=%d",
                symbol,
                timeframe,
                last_ts,
                last_server_t,
                changed,
                len(inc),
            )

            new_bucket_started = bool(
                prev_last
                and last_server
                and last_server_t
                and last_server_t > int(prev_last.get("t", 0))
            )

            if inc:
                try:
                    with mon_cache_lock:
                        st["dirty"] = True
                except Exception:
                    st["dirty"] = True

            # NOTE: persist_if_needed removed - no longer writes to GCS stream
            # All real-time data flows through Firestore (written by runTicker on frontend)


            now_ms = int(time.time() * 1000)
            last_served_ts = inc[-1]["t"] if inc else last_ts

            try:
                await asyncio.to_thread(
                    fs_touch_monitoreo,
                    exec_id,
                    symbol,
                    {
                        "estado": "running",
                        "symbol": symbol,
                        "timeframe": timeframe,
                        "user_id": user_id,
                        "tf_states": {
                            timeframe: {
                                "estado": "running",
                                "last_ts": last_served_ts,
                                "count_served": len(inc),
                                "updated_at": now_ms,
                            }
                        },
                    },
                )
            except Exception:
                logging.exception("INC %s %s: fs_touch_monitoreo fallo", symbol, timeframe)

            if last_ts is None:
                inc_ms = base_ms
            else:
                inc_ms = [b for b in base_ms if int(b.get("t", 0)) > last_ts]

            logging.info(
                "INC RESP sym=%s tf=%s candles=%d from_ts=%s to_ts=%s",
                symbol,
                timeframe,
                len(inc_ms),
                inc_ms[0]["t"] if inc_ms else None,
                inc_ms[-1]["t"] if inc_ms else None,
            )
            logging.info(
                "INC DONE sym=%s tf=%s candles=%d dur=%.3fs",
                symbol,
                timeframe,
                len(inc),
                time.time() - start,
            )

            # ============ COLD-START DETECTION: Add flag when returning initial data ============
            is_cold_start = last_ts is None and len(inc) > 0
            is_empty_response = len(inc) == 0
            
            if is_empty_response:
                logging.warning(
                    "INC EMPTY sym=%s tf=%s (may be first request with no cached/historical data)",
                    symbol,
                    timeframe,
                )
            
            resp_payload = {
                "status": "ok",
                "symbol": symbol,
                "timeframe": timeframe,
                "exec_id": exec_id,
                "from_ts": inc[0]["t"] if inc else last_ts,
                "to_ts": inc[-1]["t"] if inc else last_ts,
                "candles": inc,
                "data_quality": _build_timeframe_data_quality(base_ms, timeframe),
            }
            
            # Add cold_start flag for frontend to detect initial data load
            if is_cold_start:
                resp_payload["cold_start"] = True
            
            # Add empty_response flag if truly no data available
            if is_empty_response:
                resp_payload["empty_response"] = True

            return (
                jsonify(resp_payload),
                200,
            )

        except Exception as exc:
            logging.exception(
                "Error en /monitoreo/incremental (dur=%.3fs)", time.time() - start
            )
            return jsonify({"status": "error", "message": str(exc)}), 500

    @app.route("/monitoreo/describe", methods=["GET"])
    def monitoreo_describe():
        exec_id = request.args.get("exec_id", "").strip()
        symbol = request.args.get("symbol", "").strip().upper()
        if not exec_id or not symbol:
            return (
                jsonify(
                    {"status": "error", "message": "exec_id y symbol son obligatorios"}
                ),
                400,
            )
        doc = db.collection("monitoreos").document(f"{exec_id}__{symbol}").get()
        data = doc.to_dict() or {}
        return jsonify({"status": "ok", "doc": data}), 200

    @app.route("/monitoreo/resume", methods=["GET"])
    async def monitoreo_resume():
        limit = 600
        from_ts = None
        to_ts = None
        persist = False
        fill_gaps = False
        force_api = False
        max_minutes_per_call = 10_000
        try:
            symbol = str((request.args.get("symbol") or "")).strip().upper()
            timeframe = norm_tf(request.args.get("timeframe"))
            exec_id = str((request.args.get("exec_id") or "")).strip()
            if not symbol or not timeframe or not exec_id:
                return (
                    jsonify(
                        {
                            "status": "error",
                            "message": "symbol, timeframe y exec_id son obligatorios",
                        }
                    ),
                    400,
                )

            def _arg_bool(value) -> bool:
                return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}

            limit = request.args.get("limit", 600)
            from_ts = request.args.get("from_ts", None)
            to_ts = request.args.get("to_ts", None)
            persist = _arg_bool(request.args.get("persist", False))

            fill_gaps = _arg_bool(request.args.get("fill_gaps", False))
            force_api = _arg_bool(request.args.get("force_api", False))
            try:
                max_minutes_per_call = int(
                    request.args.get("max_minutes_per_call") or 10_000
                )
            except Exception:
                max_minutes_per_call = 10_000

            st = await asyncio.to_thread(load_cache, exec_id, symbol, timeframe)
            if norm_tf(timeframe) == "1min":
                resume_limit = _parse_int_field(limit, 600, min_value=1, max_value=5000)
                await _refresh_1min_series_from_fmp(symbol, timeframe, st, limit=resume_limit)
            series_ms = series_to_ms(st.get("series", []))

            gapfill_meta = {
                "requested": bool(force_api or fill_gaps),
                "force_api": bool(force_api),
                "fill_gaps": bool(fill_gaps),
                "fetched": 0,
                "added": 0,
            }
            if force_api or fill_gaps:
                try:
                    tf_ms_value = tf_ms(timeframe)
                    if tf_ms_value:
                        try:
                            limit_i = int(limit) if limit is not None else 600
                        except Exception:
                            limit_i = 600
                        if limit_i <= 0:
                            limit_i = 600

                        closed_end = current_closed_bucket_start(timeframe) - tf_ms_value

                        from_in = from_ts
                        to_in = to_ts
                        try:
                            from_in = int(from_in) if from_in is not None else None
                        except Exception:
                            from_in = None
                        try:
                            to_in = int(to_in) if to_in is not None else None
                        except Exception:
                            to_in = None

                        from_eff = (
                            from_in
                            if from_in is not None
                            else (closed_end - (limit_i - 1) * tf_ms_value)
                        )
                        to_eff = to_in if to_in is not None else closed_end

                        to_eff = min(to_eff, closed_end)
                        if from_eff > to_eff:
                            from_eff, to_eff = to_eff, from_eff

                        fetch_to = min(to_eff + tf_ms_value, closed_end + tf_ms_value)

                        with fmp_context(usage_kind="monitoring_history_refresh", source="monitoreo_gapfill", symbol=symbol, timeframe=timeframe):
                            rng = await asyncio.to_thread(
                                fetch_historical_range, symbol, timeframe, from_eff, fetch_to
                            )
                        gapfill_meta["fetched"] = len(rng or [])

                        if rng:
                            added = merge_bars_series(series_ms, rng, timeframe)
                            if added:
                                gapfill_meta["added"] += int(added)
                                series_ms[:] = snap_and_dedupe_to_minutes(
                                    series_ms, timeframe
                                )
                                with mon_cache_lock:
                                    st["series"] = series_ms
                                    if persist:
                                        st["dirty"] = True

                        if fill_gaps:
                            added2 = await asyncio.to_thread(
                                backfill_internal_gaps,
                                series_ms,
                                symbol,
                                timeframe,
                                exec_id,
                                max_minutes_per_call,
                                True,
                            )
                            if added2:
                                gapfill_meta["added"] += int(added2)
                                series_ms[:] = snap_and_dedupe_to_minutes(
                                    series_ms, timeframe
                                )
                                with mon_cache_lock:
                                    st["series"] = series_ms
                                    if persist:
                                        st["dirty"] = True

                except Exception:
                    logging.exception("Gapfill/force_api fallo en /monitoreo/history")

            last_ts = series_ms[-1]["t"] if series_ms else None
            await asyncio.to_thread(
                fs_touch_monitoreo,
                exec_id,
                symbol,
                {
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "last_resume_at_ms": int(time.time() * 1000),
                },
            )

            return (
                jsonify(
                    {
                        "status": "ok",
                        "symbol": symbol,
                        "timeframe": timeframe,
                        "exec_id": exec_id,
                        "last_ts": last_ts,
                        "count": len(series_ms),
                        "source": st.get("source", "unknown"),
                    }
                ),
                200,
            )
        except Exception as exc:
            logging.exception("Error en /monitoreo/resume")
            return jsonify({"status": "error", "message": str(exc)}), 500

    @app.route("/monitoreo/history", methods=["POST"])
    async def monitoreo_history():
        try:
            body = request.get_json(force=True) or {}

            user_id = str(body.get("user_id") or "").strip()
            exec_id = str(body.get("exec_id") or "").strip()
            symbol = str(body.get("symbol") or "").strip().upper()
            timeframe = norm_tf(body.get("timeframe"))

            limit = body.get("limit", 600)
            from_ts = body.get("from_ts", None)
            to_ts = body.get("to_ts", None)
            persist = bool(body.get("persist", False))

            fill_gaps = bool(body.get("fill_gaps", False))
            force_api = bool(body.get("force_api", False))
            max_minutes_per_call = _parse_int_field(
                body.get("max_minutes_per_call"),
                10_000,
                min_value=1,
                max_value=100_000,
            )

            if not user_id:
                return jsonify({"status": "error", "message": "user_id es obligatorio"}), 400
            if not exec_id:
                return jsonify({"status": "error", "message": "exec_id es obligatorio"}), 400
            if not symbol or not timeframe:
                return (
                    jsonify(
                        {
                            "status": "error",
                            "message": "symbol y timeframe son obligatorios",
                        }
                    ),
                    400,
                )

            tf_api = timeframe
            enabled = True
            if exec_id:
                enabled = tf_is_enabled(exec_id, symbol, tf_api)

            logging.info(
                "HIST TFCHK sym=%s tf=%s enabled=%s user_id=%s exec_id=%s",
                symbol,
                tf_api,
                enabled,
                user_id,
                exec_id,
            )

            # Auto-renovar heartbeat solo si está cerca de expirar (optimización de Firestore)
            # Simplificado: siempre renovar si enabled es False, para recuperación rápida
            if exec_id and symbol and not enabled and not _tf_has_explicit_stop(exec_id, symbol, tf_api):
                try:
                    now_ms = int(time.time() * 1000)
                    
                    heartbeat_data = {
                        "tf_states": {
                            tf_api: {
                                "enabled": True,
                                "estado": "running",
                                "reason": None,
                                "stop_reason": None,
                                "last_heartbeat_ms": now_ms,
                                "last_ts": now_ms,
                            }
                        }
                    }
                    await asyncio.to_thread(fs_touch_monitoreo, exec_id, symbol, heartbeat_data)
                    logging.info(
                        "HIST AUTO-RENEW sym=%s tf=%s - TTL renovado por request del frontend",
                        symbol,
                        tf_api,
                    )
                    # Re-validar enabled después de renovar
                    enabled = tf_is_enabled(exec_id, symbol, tf_api)
                except Exception as e:
                    logging.warning("HIST AUTO-RENEW fallido para %s %s: %s", symbol, tf_api, e)

            if not enabled:
                denied_reason = _tf_access_denied_reason(exec_id, symbol, tf_api)
                if denied_reason:
                    return _quota_error_response(denied_reason)
                return (
                    jsonify(
                        {
                            "status": "ok",
                            "symbol": symbol,
                            "timeframe": tf_api,
                            "exec_id": exec_id,
                            "from_ts": None,
                            "to_ts": None,
                            "candles": [],
                        }
                    ),
                    200,
                )

            ok, msg = await charge_monitoreo_per_call(user_id, origen="app")
            if not ok:
                await _mark_tf_stopped_access_denied(
                    exec_id=exec_id,
                    symbol=symbol,
                    timeframe=timeframe,
                    user_id=user_id,
                    reason=msg,
                )
                return _quota_error_response(msg)

            limit = _parse_int_field(limit, 600, min_value=1, max_value=5000)

            st = await asyncio.to_thread(load_cache, exec_id, symbol, timeframe)
            refreshed_1min_direct = False
            if norm_tf(timeframe) == "1min":
                refreshed_1min_direct = await _refresh_1min_series_from_fmp(
                    symbol,
                    timeframe,
                    st,
                    limit=limit,
                    from_ts=_parse_int_field(from_ts, None) if from_ts is not None else None,
                    to_ts=_parse_int_field(to_ts, None) if to_ts is not None else None,
                )

            age_map = {
                "1min": 300,
                "5min": 120,
                "15min": 300,
                "30min": 600,
                "1hour": 1200,
                "4hour": 2400,
            }
            age = age_map.get(timeframe, 60)

            if timeframe not in ("1min", "1m") or not st.get("series"):
                await asyncio.to_thread(
                    maybe_refresh_from_gcs, exec_id, symbol, timeframe, st, age
                )

            series_ms = series_to_ms(st.get("series", []))

            gapfill_meta = {
                "requested": bool(force_api or fill_gaps),
                "force_api": bool(force_api),
                "fill_gaps": bool(fill_gaps),
                "fetched": 0,
                "added": 0,
            }
            if force_api or fill_gaps:
                try:
                    # 1min path already did a direct FMP refresh above.
                    # If fill_gaps is not requested, avoid a second identical fetch in the same request.
                    skip_redundant_1min_fetch = (
                        norm_tf(timeframe) == "1min"
                        and refreshed_1min_direct
                        and not fill_gaps
                    )

                    if skip_redundant_1min_fetch:
                        gapfill_meta["skipped_redundant_fetch"] = True
                        logging.info(
                            "HIST 1MIN SKIP REDUNDANT FETCH sym=%s tf=%s force_api=%s fill_gaps=%s",
                            symbol,
                            timeframe,
                            force_api,
                            fill_gaps,
                        )
                    else:
                        tf_ms_value = tf_ms(timeframe)
                        if tf_ms_value:
                            try:
                                limit_i = int(limit) if limit is not None else 600
                            except Exception:
                                limit_i = 600
                            if limit_i <= 0:
                                limit_i = 600

                            closed_end = current_closed_bucket_start(timeframe) - tf_ms_value

                            from_in = _parse_int_field(from_ts, None) if from_ts is not None else None
                            to_in = _parse_int_field(to_ts, None) if to_ts is not None else None

                            if from_in is not None and to_in is not None:
                                lo = min(from_in, to_in)
                                hi = max(from_in, to_in)
                                if (hi - lo) > max_history_window_ms:
                                    return (
                                        jsonify(
                                            {
                                                "status": "error",
                                                "message": "Rango temporal excede el maximo de 365 dias",
                                                "max_window_ms": max_history_window_ms,
                                            }
                                        ),
                                        400,
                                    )

                            from_eff = (
                                from_in
                                if from_in is not None
                                else (closed_end - (limit_i - 1) * tf_ms_value)
                            )
                            to_eff = to_in if to_in is not None else closed_end

                            to_eff = min(to_eff, closed_end)
                            if from_eff > to_eff:
                                from_eff, to_eff = to_eff, from_eff

                            fetch_to = min(to_eff + tf_ms_value, closed_end + tf_ms_value)

                            with fmp_context(usage_kind="monitoring_history_refresh", source="monitoreo_gapfill", symbol=symbol, timeframe=timeframe):
                                rng = await asyncio.to_thread(
                                    fetch_historical_range, symbol, timeframe, from_eff, fetch_to
                                )
                            gapfill_meta["fetched"] = len(rng or [])

                            if rng:
                                added = merge_bars_series(series_ms, rng, timeframe)
                                if added:
                                    gapfill_meta["added"] += int(added)
                                    series_ms[:] = snap_and_dedupe_to_minutes(
                                        series_ms, timeframe
                                    )
                                    with mon_cache_lock:
                                        st["series"] = series_ms
                                        if persist:
                                            st["dirty"] = True

                            if fill_gaps:
                                added2 = await asyncio.to_thread(
                                    backfill_internal_gaps,
                                    series_ms,
                                    symbol,
                                    timeframe,
                                    exec_id,
                                    max_minutes_per_call,
                                    True,
                                )
                                if added2:
                                    gapfill_meta["added"] += int(added2)
                                    series_ms[:] = snap_and_dedupe_to_minutes(
                                        series_ms, timeframe
                                    )
                                    with mon_cache_lock:
                                        st["series"] = series_ms
                                        if persist:
                                            st["dirty"] = True
                except Exception:
                    logging.exception("Gapfill/force_api fallo en /monitoreo/history")

            # 🔧 IMPORTANT: Sync series_ms back to st before persisting
            # (especially when force_api/fill_gaps are false, which is the case for seed loading on higher TFs)
            with mon_cache_lock:
                st["series"] = series_ms
                if persist:
                    st["dirty"] = True
                    logging.info("HIST PERSIST FLAGGED sym=%s tf=%s", symbol, timeframe)

            from_ts = _parse_int_field(from_ts, None) if from_ts is not None else None
            to_ts = _parse_int_field(to_ts, None) if to_ts is not None else None

            if from_ts is not None and to_ts is not None:
                lo = min(from_ts, to_ts)
                hi = max(from_ts, to_ts)
                if (hi - lo) > max_history_window_ms:
                    return (
                        jsonify(
                            {
                                "status": "error",
                                "message": "Rango temporal excede el maximo de 365 dias",
                                "max_window_ms": max_history_window_ms,
                            }
                        ),
                        400,
                    )

            if from_ts is not None or to_ts is not None:
                lo = float("-inf") if from_ts is None else from_ts
                hi = float("inf") if to_ts is None else to_ts
                filt = [c for c in series_ms if lo <= c["t"] <= hi]
            else:
                filt = series_ms

            if limit and len(filt) > limit:
                filt = filt[-limit:]

            from_out = filt[0]["t"] if filt else from_ts
            to_out = filt[-1]["t"] if filt else to_ts

            # NOTE: persist_if_needed removed - all real-time data flows through Firestore


            await asyncio.to_thread(
                fs_touch_monitoreo,
                exec_id,
                symbol,
                {
                    "estado": "running",
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "user_id": user_id,
                    "count_served": len(filt),
                    "last_ts_served": (filt[-1]["t"] if filt else None),
                },
            )

            resp = {
                "status": "ok",
                "symbol": symbol,
                "timeframe": timeframe,
                "exec_id": exec_id,
                "from_ts": from_out,
                "to_ts": to_out,
                "count": len(filt),
                "candles": filt,
                "gapfill": (gapfill_meta if "gapfill_meta" in locals() else None),
                "data_quality": _build_timeframe_data_quality(filt, timeframe),
            }
            # Removed: persisted_path response (no longer persisting to GCS stream)
            return jsonify(resp), 200

        except Exception as exc:
            logging.exception("Error en /monitoreo/history")
            return jsonify({"status": "error", "message": str(exc)}), 500
    @app.route("/api/monitoreos/list", methods=["POST"])
    def list_monitoreos():
        """
        ✅ FASE OPTIMIZATION: List user's monitoreos with 24h optimization.
        
        POST /api/monitoreos/list
        Body:
        {
          "user_id": "uid123",
          "hours_back": 24,      # optional (default 24)
          "limit": 50            # optional (default 50)
        }
        
        Returns:
        {
          "status": "ok",
          "strategy": "recent" | "fallback",
          "count": 12,
          "monitoreos": [
            {
              "id": "exec123__EURUSD",
              "symbol": "EURUSD",
              "exec_id": "exec123",
              "estado": "running",
              "updated_at": 1740000000000,
              "allowed_timeframes": ["1m", "5m"]
            }
          ]
        }
        """
        try:
            body = request.get_json(force=True) or {}
            user_id = str(body.get("user_id") or "").strip()
            hours_back = int(body.get("hours_back", 24))
            limit_count = int(body.get("limit", 50))
            
            if not user_id:
                return jsonify({"status": "error", "message": "user_id required"}), 400
            
            # ✅ Calculate 24h cutoff timestamp
            cutoff_ms = int((time.time() - hours_back * 3600) * 1000)
            
            # ✅ STRATEGY 1: Query with 24h filter (requires index)
            try:
                docs = db.collection("monitoreos").where(
                    "user_id", "==", user_id
                ).where(
                    "updated_at", ">", cutoff_ms
                ).order_by(
                    "updated_at", direction="DESCENDING"
                ).limit(limit_count).stream()
                
                monitoreos = []
                for doc in docs:
                    data = doc.to_dict() or {}
                    monitoreos.append({
                        "id": doc.id,
                        "symbol": data.get("symbol"),
                        "exec_id": data.get("exec_id"),
                        "estado": data.get("estado"),
                        "updated_at": data.get("updated_at"),
                        "allowed_timeframes": data.get("allowed_timeframes", []),
                        "timeframes_permitidas": data.get("timeframes_permitidas", []),
                        "selected_tfs": data.get("selected_tfs", []),
                        "monitor_selected_tfs": data.get("monitor_selected_tfs", []),
                        "selectedTFs": data.get("selectedTFs", []),
                        "tfs": data.get("tfs", []),
                        "running": data.get("running", []),
                    })
                
                logger.info(f"[MonitoreosAPI] Strategy=RECENT user={user_id} count={len(monitoreos)}")
                
                return jsonify({
                    "status": "ok",
                    "strategy": "recent",
                    "count": len(monitoreos),
                    "monitoreos": monitoreos
                }), 200
            
            except Exception as e:
                error_msg = str(e).lower()
                if "index" in error_msg or "precondition" in error_msg:
                    # ✅ STRATEGY 2: Fallback - no 24h filter, just limit
                    logger.warn(f"[MonitoreosAPI] Index missing, using fallback: {e}")
                    
                    docs = db.collection("monitoreos").where(
                        "user_id", "==", user_id
                    ).order_by(
                        "updated_at", direction="DESCENDING"
                    ).limit(limit_count).stream()
                    
                    monitoreos = []
                    for doc in docs:
                        data = doc.to_dict() or {}
                        # Client-side filtering for 24h
                        if data.get("updated_at", 0) > cutoff_ms:
                            monitoreos.append({
                                "id": doc.id,
                                "symbol": data.get("symbol"),
                                "exec_id": data.get("exec_id"),
                                "estado": data.get("estado"),
                                "updated_at": data.get("updated_at"),
                                "allowed_timeframes": data.get("allowed_timeframes", []),
                                "timeframes_permitidas": data.get("timeframes_permitidas", []),
                                "selected_tfs": data.get("selected_tfs", []),
                                "monitor_selected_tfs": data.get("monitor_selected_tfs", []),
                                "selectedTFs": data.get("selectedTFs", []),
                                "tfs": data.get("tfs", []),
                                "running": data.get("running", []),
                            })
                    
                    logger.info(f"[MonitoreosAPI] Strategy=FALLBACK user={user_id} count={len(monitoreos)}")
                    
                    return jsonify({
                        "status": "ok",
                        "strategy": "fallback",
                        "count": len(monitoreos),
                        "monitoreos": monitoreos
                    }), 200
                else:
                    raise
        
        except Exception as exc:
            logger.exception("Error en /api/monitoreos/list")
            return jsonify({"status": "error", "message": str(exc)}), 500

    # ── Live-candle cache ──────────────────────────────────────────────────────
    # Short-lived per (symbol, tf) cache so consecutive polls within the same
    # second reuse the last FMP response instead of hitting the API again.
    _lc_cache: dict = {}
    _lc_cache_ttl = {
        "1min":  5,
        "5min":  10,
        "15min": 20,
        "30min": 30,
        "1hour": 60,
        "4hour": 120,
    }

    @app.route("/monitoreo/live-candle", methods=["GET"])
    async def monitoreo_live_candle():
        """
        GET /monitoreo/live-candle?symbol=BTCUSD&timeframe=1min

        Returns the current *building* candle (open period) for the requested
        symbol / timeframe by fetching the most recent bar from FMP's intraday
        historical endpoint.  The frontend overlays this on top of the closed
        historical series so the live candle always shows a proper body + wicks
        instead of a flat dash.

        Response:
          {
            "status": "ok",
            "symbol": "BTCUSD",
            "timeframe": "1min",
            "candle": { "t": <ms>, "o": ..., "h": ..., "l": ..., "c": ..., "v": ... }
                        | null
          }
        """
        try:
            live_enabled = str(os.getenv("ENABLE_WORKING_LIVE", "false")).strip().lower() in {"1", "true", "yes", "y", "on"}
            if not live_enabled:
                return jsonify({"status": "disabled", "message": "working live disabled"}), 404

            symbol    = request.args.get("symbol",    "").strip().upper()
            timeframe = norm_tf(request.args.get("timeframe", "1min"))

            if not symbol or not timeframe:
                return jsonify({"status": "error",
                                "message": "symbol y timeframe son obligatorios"}), 400

            cache_key = (symbol, timeframe)
            now       = time.time()
            ttl       = _lc_cache_ttl.get(timeframe, 10)
            cached    = _lc_cache.get(cache_key)

            if cached and (now - cached["ts"]) < ttl:
                return jsonify({
                    "status":    "ok",
                    "symbol":    symbol,
                    "timeframe": timeframe,
                    "candle":    cached["data"],
                    "cached":    True,
                }), 200

            tf_ms_value = tf_ms(timeframe)
            if not tf_ms_value:
                return jsonify({"status": "ok", "symbol": symbol,
                                "timeframe": timeframe, "candle": None}), 200

            now_ms      = int(now * 1000)
            # Fetch from the start of the current open bucket plus a small look-
            # back (one extra bar) so we definitely capture the building candle.
            bucket_start = current_closed_bucket_start(timeframe)  # last CLOSED
            from_ms      = bucket_start - tf_ms_value               # one bar back
            to_ms        = now_ms + tf_ms_value                     # a bit ahead

            with fmp_context(usage_kind="monitoring_live_refresh", source="live_candle", symbol=symbol, timeframe=timeframe):
                candles = await asyncio.to_thread(
                    fetch_historical_range, symbol, timeframe, from_ms, to_ms
                )

            if not candles:
                _lc_cache[cache_key] = {"data": None, "ts": now}
                return jsonify({"status": "ok", "symbol": symbol,
                                "timeframe": timeframe, "candle": None}), 200

            # fetch_historical_range returns bars in ascending order (oldest→newest).
            # The last bar is either the last closed bar or the currently building one.
            live = candles[-1]

            _lc_cache[cache_key] = {"data": live, "ts": now}
            logging.info(
                "LIVE-CANDLE %s %s t=%s o=%.5f h=%.5f l=%.5f c=%.5f",
                symbol, timeframe,
                live.get("t"), live.get("o", 0), live.get("h", 0),
                live.get("l", 0), live.get("c", 0),
            )
            return jsonify({
                "status":    "ok",
                "symbol":    symbol,
                "timeframe": timeframe,
                "candle":    live,
            }), 200

        except Exception as exc:
            logging.exception("Error en /monitoreo/live-candle")
            return jsonify({"status": "error", "message": str(exc)}), 500
