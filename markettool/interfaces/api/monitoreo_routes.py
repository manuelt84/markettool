"""Monitoreo API routes."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Callable, Mapping

import pandas as pd
from flask import jsonify, request


def register_monitoreo_routes(
    app,
    *,
    logger,
    db,
    charge_monitoreo_per_call,
    fetch_events_for,
    filter_by_symbol_currencies,
    hash_payload,
    last_hash_ref: dict,
    detect_new_results,
    evaluar_evento_para_symbol,
    norm_tf,
    tf_is_enabled,
    load_cache,
    series_to_ms,
    snap_and_dedupe_to_minutes,
    densify_minutes,
    maybe_tick_quote,
    mon_cache_lock,
    maybe_refresh_from_gcs,
    fs_touch_monitoreo,
    tf_ms,
    current_closed_bucket_start,
    fetch_historical_range,
    merge_bars_series,
    backfill_internal_gaps,
    bucket_name: str,
) -> None:
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
        try:
            body = request.get_json(force=True) or {}
            user_id = str(body.get("user_id") or "").strip()
            exec_id = str(body.get("exec_id") or "").strip()
            symbol = str(body.get("symbol") or "").strip().upper()
            hours_back = int(body.get("hours_back", 6))
            minutes_fwd = int(body.get("minutes_fwd", 5))
            cursor_hash = str(body.get("cursor_hash") or "").strip()

            if not user_id or not exec_id or not symbol:
                return (
                    jsonify(
                        {
                            "status": "error",
                            "message": "user_id, exec_id y symbol son obligatorios",
                        }
                    ),
                    400,
                )

            ok, msg = await charge_monitoreo_per_call(user_id, origen="app")
            if not ok:
                return jsonify({"status": "error", "message": msg}), 402

            # ✅ OPTIMIZATION: Check last_hash_ref before expensive fetch+processing
            # If cursor_hash matches and we have cached hash, return early with empty events
            key = (exec_id, symbol)
            cached_hash = last_hash_ref.get(key)
            
            if cursor_hash and cached_hash and cursor_hash == cached_hash:
                logger.info("[monitoreo/eventos] cursor_hash match %s - checking for new_results", symbol)
                # Still need to check for new_results (requires lightweight fetch with adaptive cache)
                df_check = fetch_events_for(symbol, hours_back=hours_back, minutes_fwd=minutes_fwd)
                new_results_check = detect_new_results(symbol, df_check) if not df_check.empty else []
                
                if not new_results_check:
                    # No changes - return early without processing
                    logger.info("[monitoreo/eventos] No new_results - returning empty response")
                    return (
                        jsonify(
                            {
                                "status": "ok",
                                "exec_id": exec_id,
                                "symbol": symbol,
                                "server_time": int(time.time() * 1000),
                                "hash": cached_hash,
                                "count": 0,
                                "new_results": [],
                                "events": [],
                                "signals": [],
                                "agg_score": 0.0,
                                "agg_direction": "neutral",
                            }
                        ),
                        200,
                    )
                # If there ARE new_results, continue with full processing below
                logger.info("[monitoreo/eventos] Hash match but new_results found - processing")

            logger.info(
                "Llamando fetch_events_for(%s, hb=%s, mf=%s)",
                symbol,
                hours_back,
                minutes_fwd,
            )
            df = fetch_events_for(symbol, hours_back=hours_back, minutes_fwd=minutes_fwd)
            logger.info("fetch_events_for termino")

            if df.empty:
                out = {
                    "status": "ok",
                    "exec_id": exec_id,
                    "symbol": symbol,
                    "server_time": int(time.time() * 1000),
                    "hash": "0" * 8,
                    "count": 0,
                    "new_results": [],
                    "events": [],
                }
                return jsonify(out), 200

            df = df[df["impact"].isin(["High", "Medium"])].copy()
            df = filter_by_symbol_currencies(df, symbol)

            events = [
                {
                    "date": (row.date.isoformat() if pd.notna(row.date) else None),
                    "currency": getattr(row, "currency", None),
                    "event": getattr(row, "event", None),
                    "impact": getattr(row, "impact", None),
                    "actual": (
                        float(row.actual)
                        if pd.notna(getattr(row, "actual", None))
                        else None
                    ),
                    "estimate": (
                        float(row.estimate)
                        if pd.notna(getattr(row, "estimate", None))
                        else None
                    ),
                    "previous": (
                        float(row.previous)
                        if pd.notna(getattr(row, "previous", None))
                        else None
                    ),
                }
                for row in df.itertuples(index=False)
            ]

            payload_hash = hash_payload(events)
            key = (exec_id, symbol)
            last_hash_ref[key] = payload_hash

            new_results = detect_new_results(symbol, df)

            signals = []
            agg = 0.0
            for row in df.itertuples(index=False):
                actual = getattr(row, "actual", None)
                if pd.notna(actual):
                    sig = evaluar_evento_para_symbol(
                        symbol,
                        {
                            "date": row.date,
                            "currency": getattr(row, "currency", None),
                            "event": getattr(row, "event", None),
                            "impact": getattr(row, "impact", None),
                            "actual": actual,
                            "estimate": getattr(row, "estimate", None),
                            "previous": getattr(row, "previous", None),
                        },
                    )
                    sig_out = {
                        "date": (
                            row.date.isoformat() if pd.notna(row.date) else None
                        ),
                        "currency": getattr(row, "currency", None),
                        "event": getattr(row, "event", None),
                        "impact": getattr(row, "impact", None),
                        "score": sig["score"],
                        "direction": sig["direction"],
                        "reason": sig["reason"],
                    }
                    signals.append(sig_out)
                    agg += float(sig["score"])

            agg_direction = (
                "bullish" if agg > 0.02 else ("bearish" if agg < -0.02 else "neutral")
            )

            try:
                if db is not None:
                    doc_id = f"{exec_id}__{symbol}"
                    db.collection("monitoreos").document(doc_id).set(
                        {
                            "eventos_hash": payload_hash,
                            "eventos_count": len(events),
                            "eventos_updated_at": int(time.time() * 1000),
                            "eventos_agg_score": float(agg),
                            "eventos_agg_direction": agg_direction,
                        },
                        merge=True,
                    )
            except Exception:
                pass

            if cursor_hash and cursor_hash == payload_hash and not new_results:
                return (
                    jsonify(
                        {
                            "status": "ok",
                            "exec_id": exec_id,
                            "symbol": symbol,
                            "server_time": int(time.time() * 1000),
                            "hash": payload_hash,
                            "count": len(events),
                            "new_results": [],
                            "events": [],
                            "signals": [],
                            "agg_score": float(agg),
                            "agg_direction": agg_direction,
                        }
                    ),
                    200,
                )

            return (
                jsonify(
                    {
                        "status": "ok",
                        "exec_id": exec_id,
                        "symbol": symbol,
                        "server_time": int(time.time() * 1000),
                        "hash": payload_hash,
                        "count": len(events),
                        "new_results": new_results,
                        "events": events,
                        "signals": signals,
                        "agg_score": float(agg),
                        "agg_direction": agg_direction,
                    }
                ),
                200,
            )

        except Exception as exc:
            logger.exception("Error en /monitoreo/eventos")
            return jsonify({"status": "error", "message": str(exc)}), 500

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

            eps = 1
            if last_ts is None:
                inc = base_ms
            elif last_server_t is not None and last_server_t > last_ts + eps:
                inc = [c for c in base_ms if int(c.get("t", 0)) > last_ts]
            else:
                inc = (
                    [last_server]
                    if (
                        changed
                        and last_server_t
                        and last_server_t >= (last_ts or 0) - eps
                    )
                    else []
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

            return (
                jsonify(
                    {
                        "status": "ok",
                        "symbol": symbol,
                        "timeframe": timeframe,
                        "exec_id": exec_id,
                        "from_ts": inc[0]["t"] if inc else last_ts,
                        "to_ts": inc[-1]["t"] if inc else last_ts,
                        "candles": inc,
                    }
                ),
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
