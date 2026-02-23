"""Monitoreo API routes."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta
from typing import Any, Callable, Mapping

import pandas as pd
from flask import jsonify, request

from markettool.application.use_cases.legacy import LegacyMonitoreoUseCase
from markettool.core.cache_config import validate_data_freshness, get_freshness_requirement_for_timeframe


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
                return jsonify({"status": "error", "message": msg}), 402

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
                "HIST TFCHK sym=%s tf=%s enabled=%s user_id=%s exec_id=%s",
                symbol,
                timeframe,
                enabled,
                user_id,
                exec_id,
            )

            if not enabled:
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

            # NOTE: ensure_stream_initialized removed - no longer maintains GCS stream
            # All real-time data flows through Firestore

            # ============ 1MIN HOTFIX: Force Firestore refresh for 1m to avoid cache staleness ============
            if timeframe == "1min":
                try:
                    fs_doc_ref = db.collection("executions").document(exec_id)
                    fs_live_ref = fs_doc_ref.collection("live_data").document(f"{symbol}_1min")
                    fs_snap = await asyncio.to_thread(fs_live_ref.get)
                    
                    if fs_snap.exists():
                        fs_data = fs_snap.to_dict() or {}
                        fs_candles = fs_data.get("candles", []) or []
                        
                        if fs_candles and len(fs_candles) > 0:
                            # Convert Firestore candles to millisecond format
                            # series_to_ms already available from outer scope (line 32)
                            fs_series = series_to_ms(fs_candles)
                            cache_series_old = st.get("series", []) or []
                            
                            if len(fs_series) > len(cache_series_old):
                                st["series"] = fs_series
                                logging.info(
                                    "INC 1min HOTFIX %s: Refreshed from Firestore: %d candles (was %d in cache)",
                                    symbol,
                                    len(fs_series),
                                    len(cache_series_old)
                                )
                except Exception as e:
                    logging.warning("INC 1min HOTFIX %s: Firestore refresh failed: %s", symbol, e)
                    # Continue with cache as-is

            # ============ COLD-START FIX: If cache is empty, fetch historical data ============
            cache_series = st.get("series", []) or []
            if not cache_series or len(cache_series) == 0:
                logging.info(
                    "INC %s %s: COLD-START detected (empty cache), fetching historical...",
                    symbol,
                    timeframe,
                )
                try:
                    # Fetch last ~300 candles (2-3 days for 1m, weeks for 4h)
                    # This gives the frontend enough context for technical analysis
                    hist_from = datetime.utcnow() - timedelta(days=14)  # 14 days back
                    hist_to = datetime.utcnow()
                    
                    hist_df = await asyncio.to_thread(
                        fetch_historical_range,
                        symbol,
                        timeframe,
                        hist_from,
                        hist_to,
                    )
                    
                    if hist_df is not None and not hist_df.empty:
                        # Convert DataFrame to dict format that cache expects
                        hist_series = []
                        for idx, row in hist_df.iterrows():
                            ts_ms = int(idx.timestamp() * 1000) if hasattr(idx, 'timestamp') else int(idx)
                            hist_series.append({
                                "t": ts_ms,
                                "o": float(row.get("open", 0)),
                                "h": float(row.get("high", 0)),
                                "l": float(row.get("low", 0)),
                                "c": float(row.get("close", 0)),
                                "v": float(row.get("volume", 0)) if "volume" in row else None,
                            })
                        
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
                            "INC %s %s: Cold-start fetch returned empty DataFrame",
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
                if (
                    timeframe == "1min"
                    and not inc
                    and last_ts
                    and last_server_t
                    and (last_server_t - last_ts) >= 55000  # Within 5 seconds of expected 60s boundary
                ):
                    inc = [last_server]  # Return the last_server even if "changed" is False
                    logging.info(
                        "INC 1min HOTFIX %s: Returning last_server (time gap %.0fs suggests new candle)",
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
            try:
                max_minutes_per_call = int(body.get("max_minutes_per_call") or 10_000)
            except Exception:
                max_minutes_per_call = 10_000

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

            if not enabled:
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
                return jsonify({"status": "error", "message": msg}), 402

            try:
                limit = int(limit)
            except Exception:
                limit = 600
            limit = max(1, min(limit, 5000))

            st = await asyncio.to_thread(load_cache, exec_id, symbol, timeframe)

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

            if from_ts is not None:
                try:
                    from_ts = int(from_ts)
                except Exception:
                    from_ts = None
            if to_ts is not None:
                try:
                    to_ts = int(to_ts)
                except Exception:
                    to_ts = None

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
            }
            # Removed: persisted_path response (no longer persisting to GCS stream)
            return jsonify(resp), 200

        except Exception as exc:
            logging.exception("Error en /monitoreo/history")
            return jsonify({"status": "error", "message": str(exc)}), 500
    # ==================== NEW ENDPOINTS FOR ENTRIES ====================
    
    # 🚀 Cache for freshness requirements (avoid repeated function calls)
    _freshness_cache = {}
    
    def _validate_entry_freshness(entry: dict, freshness_cache: dict) -> dict:
        """
        🆕 PROPOSAL2: Validate data freshness for each entry (OPTIMIZED).
        
        Adds freshness metadata to entry:
        - freshness_status: "fresh" | "stale" | "unknown"
        - data_age_seconds: estimated age of underlying data
        - freshness_requirement_seconds: max age allowed for this timeframe
        
        Args:
            entry: Entry dict
            freshness_cache: Dict to cache timeframe requirements (avoid repeated calls)
        
        Returns: entry dict with added metadata
        """
        try:
            symbol = entry.get("symbol", "unknown")
            timeframe = entry.get("timeframe", "unknown")
            created_at_str = entry.get("created_at")
            
            if not created_at_str:
                entry["freshness_status"] = "unknown"
                return entry
            
            # 🚀 OPTIMIZED: Faster datetime parsing
            try:
                # Fast path: assume ISO format with Z
                if 'Z' in created_at_str:
                    created_at_str_clean = created_at_str.replace('Z', '')
                    created_at = datetime.fromisoformat(created_at_str_clean)
                else:
                    created_at = datetime.fromisoformat(created_at_str)
                
                now = datetime.utcnow()
                data_age_seconds = int((now - created_at).total_seconds())
            except:
                data_age_seconds = 0
            
            # 🚀 OPTIMIZED: Cache freshness requirements (avoid 500 function calls)
            if timeframe not in freshness_cache:
                try:
                    freshness_cache[timeframe] = get_freshness_requirement_for_timeframe(timeframe)
                except:
                    freshness_cache[timeframe] = 3600  # Default 1 hour
            
            freshness_req_seconds = freshness_cache[timeframe]
            
            # Validate freshness
            is_fresh = data_age_seconds <= freshness_req_seconds
            
            entry["freshness_status"] = "fresh" if is_fresh else "stale"
            entry["data_age_seconds"] = data_age_seconds
            
            # 🚀 REMOVED: Verbose debug logging (was causing slowdown with 500 entries)
            
            return entry
        except Exception as e:
            # Silent fail - don't log debug per entry (too verbose)
            entry["freshness_status"] = "unknown"
            return entry
    
    @app.route("/api/entries/all", methods=["POST"])
    def get_all_entries():
        """
        POST /api/entries/all
        Get all calculated entries across all symbols, ranked by confluence score.
        
        Body:
        {
            "limit": 100,           # optional, default 100
            "sort_by": "score",     # optional: "score", "rrr", "timestamp", "symbol"
            "skip_expired": true,   # optional, default true
            "validate_freshness": false  # 🆕 optional, default false (enable for freshness validation)
        }
        
        Returns:
        {
            "entries": [
                {
                    "id": "uuid",
                    "symbol": "EURUSD",
                    "timeframe": "1H",
                    "side": "long",
                    "entry_price": 1.0850,
                    "take_profit": 1.0890,
                    "stop_loss": 1.0810,
                    "rrr": 2.0,
                    "confluence_score": 85,
                    "strategies": ["tech", "sr", "smc", "fvg"],
                    "source": "confluence",
                    "rank": 1,
                    "created_at": "2026-02-22T15:30:00Z",
                    "expires_at": "2026-02-22T16:30:00Z",
                    "atr_multiplier": 1.5,
                    "risk_percentage": 2.0,
                    "status": "pending",
                    "confirmation_count": 4,
                    "confirmation_pct": 85.0,
                    "leverage_recommendations": {
                        "level_1_conservative": 5.0,
                        "level_1_theoretical": 8.0,
                        "level_2_moderate": 10.0,
                        "level_2_theoretical": 15.0,
                        "recommended": 8.0
                    },
                    "metadata": {}
                }
            ],
            "total": 245,
            "timestamp": "2026-02-22T15:35:00Z"
        }
        """
        try:
            from markettool.application.services.entries_aggregation_service import entries_agg
            
            body = request.get_json(force=True) or {}
            limit = body.get("limit", 100)
            sort_by = body.get("sort_by", "score")
            skip_expired = body.get("skip_expired", True)
            validate_freshness = body.get("validate_freshness", False)  # 🚀 OFF by default for performance
            
            # Ensure limits
            limit = min(int(limit), 500)  # Max 500 entries
            
            entries = entries_agg.get_all_entries(
                limit=limit,
                sort_by=sort_by,
                skip_expired=skip_expired
            )
            
            # 🚀 OPTIMIZED: Freshness validation now OPTIONAL (default OFF to avoid 504 timeout)
            response_data = {
                "status": "ok",
                "entries": entries,
                "total": len(entries),
                "timestamp": datetime.utcnow().isoformat()
            }
            
            # Only validate if explicitly requested
            if validate_freshness:
                validated_entries = []
                fresh_count = 0
                stale_count = 0
                
                for entry in entries:
                    validated = _validate_entry_freshness(entry, _freshness_cache)
                    validated_entries.append(validated)
                    if validated.get("freshness_status") == "fresh":
                        fresh_count += 1
                    elif validated.get("freshness_status") == "stale":
                        stale_count += 1
                
                logger.info(
                    "[FRESHNESS] Total: %d, Fresh: %d, Stale: %d",
                    len(validated_entries), fresh_count, stale_count
                )
                
                response_data["entries"] = validated_entries
                response_data["freshness_summary"] = {
                    "fresh_count": fresh_count,
                    "stale_count": stale_count,
                    "total": len(validated_entries),
                }
            
            return jsonify(response_data), 200
        
        except Exception as exc:
            logger.exception("Error en /api/entries/all")
            return jsonify({"status": "error", "message": str(exc)}), 500

    @app.route("/api/entries/search", methods=["POST"])
    def search_entries():
        """
        POST /api/entries/search
        Search entries with filters.
        
        Body:
        {
            "symbol": "EURUSD",     # optional
            "timeframe": "1H",      # optional
            "side": "long",         # optional: "long", "short"
            "min_score": 50,        # optional, default 0
            "max_score": 100,       # optional, default 100
            "limit": 50,            # optional, default 100
            "sort_by": "score"      # optional
        }
        
        Returns:
        Same as /api/entries/all with leverage_recommendations, status, confirmation_count, confirmation_pct
        """
        try:
            from markettool.application.services.entries_aggregation_service import entries_agg
            
            body = request.get_json(force=True) or {}
            
            symbol = body.get("symbol")
            timeframe = body.get("timeframe")
            side = body.get("side")
            min_score = int(body.get("min_score", 0))
            max_score = int(body.get("max_score", 100))
            limit = min(int(body.get("limit", 100)), 500)
            sort_by = body.get("sort_by", "score")
            
            entries = entries_agg.filter_entries(
                symbol=symbol,
                timeframe=timeframe,
                side=side,
                min_score=min_score,
                max_score=max_score,
                limit=limit,
                sort_by=sort_by
            )
            
            return jsonify({
                "status": "ok",
                "entries": entries,
                "total": len(entries),
                "filters": {
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "side": side,
                    "min_score": min_score,
                    "max_score": max_score
                },
                "timestamp": datetime.utcnow().isoformat()
            }), 200
        
        except Exception as exc:
            logger.exception("Error en /api/entries/search")
            return jsonify({"status": "error", "message": str(exc)}), 500

    @app.route("/api/entries/stats", methods=["GET"])
    def get_entries_stats():
        """
        GET /api/entries/stats
        Get statistics about cached entries.
        
        Returns:
        {
            "total_entries": 245,
            "avg_score": 67.5,
            "max_score": 95,
            "min_score": 35,
            "avg_rrr": 1.8,
            "symbols_count": 12,
            "symbols": ["EURUSD", "GBPUSD", ...],
            "longs_count": 125,
            "shorts_count": 120,
            "last_updated": "2026-02-22T15:35:00Z"
        }
        """
        try:
            from markettool.application.services.entries_aggregation_service import entries_agg
            
            stats = entries_agg.get_statistics()
            
            return jsonify({
                "status": "ok",
                "data": stats
            }), 200
        
        except Exception as exc:
            logger.exception("Error en /api/entries/stats")
            return jsonify({"status": "error", "message": str(exc)}), 500