"""Legacy monitoreo use case."""

from __future__ import annotations

import time
from typing import Tuple

import pandas as pd


class LegacyMonitoreoUseCase:
    def __init__(self, services):
        self._services = services

    async def eventos(self, body: dict | None) -> Tuple[dict, int]:
        try:
            body = body or {}
            user_id = str(body.get("user_id") or body.get("usuario_id") or "").strip()
            exec_id = str(body.get("exec_id") or body.get("id_ejecucion") or "").strip()
            symbol = str(body.get("symbol") or body.get("simbolo") or "").strip().upper()
            hours_back = int(body.get("hours_back", 6))
            minutes_fwd = int(body.get("minutes_fwd", 5))
            cursor_hash = str(body.get("cursor_hash") or "").strip()

            if not user_id or not exec_id or not symbol:
                return {
                    "status": "error",
                    "message": "user_id, exec_id y symbol son obligatorios",
                }, 400

            ok, msg = await self._services.charge_monitoreo_per_call(user_id, origen="app")
            if not ok:
                return {
                    "status": "error",
                    "code": "INSUFFICIENT_TRANSACTIONS",
                    "error_code": "transacciones_insuficientes",
                    "message": msg,
                }, 402

            key = (exec_id, symbol)
            cached_hash = self._services.last_hash_ref.get(key)

            if cursor_hash and cached_hash and cursor_hash == cached_hash:
                self._services.logger.info(
                    "[monitoreo/eventos] cursor_hash match %s - checking for new_results",
                    symbol,
                )
                df_check = self._services.fetch_events_for(
                    symbol, hours_back=hours_back, minutes_fwd=minutes_fwd
                )
                new_results_check = (
                    self._services.detect_new_results(symbol, df_check)
                    if not df_check.empty
                    else []
                )

                if not new_results_check:
                    self._services.logger.info(
                        "[monitoreo/eventos] No new_results - returning empty response"
                    )
                    return {
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
                    }, 200

                self._services.logger.info(
                    "[monitoreo/eventos] Hash match but new_results found - processing"
                )

            self._services.logger.info(
                "Llamando fetch_events_for(%s, hb=%s, mf=%s)",
                symbol,
                hours_back,
                minutes_fwd,
            )
            df = self._services.fetch_events_for(
                symbol, hours_back=hours_back, minutes_fwd=minutes_fwd
            )
            self._services.logger.info("fetch_events_for termino")

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
                return out, 200

            impact_norm = (
                df["impact"]
                .astype(str)
                .str.strip()
                .str.lower()
            )
            df = df[
                impact_norm.eq("high")
                | impact_norm.eq("medium")
                | impact_norm.eq("alta")
                | impact_norm.eq("media")
                | impact_norm.eq("3")
                | impact_norm.eq("2")
                | impact_norm.str.contains("high", na=False)
                | impact_norm.str.contains("medium", na=False)
                | impact_norm.str.contains("alta", na=False)
                | impact_norm.str.contains("media", na=False)
            ].copy()
            df["impact"] = impact_norm.loc[df.index].map(
                lambda v: "High"
                if v in {"high", "alta", "3"} or "high" in v or "alta" in v
                else "Medium"
            )
            df = self._services.filter_by_symbol_currencies(df, symbol)

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

            payload_hash = self._services.hash_payload(events)
            self._services.last_hash_ref[key] = payload_hash

            new_results = self._services.detect_new_results(symbol, df)

            signals = []
            agg = 0.0
            for row in df.itertuples(index=False):
                actual = getattr(row, "actual", None)
                if pd.notna(actual):
                    sig = self._services.evaluar_evento_para_symbol(
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
                        "date": (row.date.isoformat() if pd.notna(row.date) else None),
                        "currency": getattr(row, "currency", None),
                        "event": getattr(row, "event", None),
                        "impact": getattr(row, "impact", None),
                        "score": sig["score"],
                        "direction": sig["direction"],
                        "reason": sig["reason"],
                    }
                    signals.append(sig_out)
                    agg += sig["score"]

            agg_score = agg / max(1, len(signals)) if signals else 0.0
            agg_direction = (
                "bullish" if agg_score > 0 else "bearish" if agg_score < 0 else "neutral"
            )

            out = {
                "status": "ok",
                "exec_id": exec_id,
                "symbol": symbol,
                "server_time": int(time.time() * 1000),
                "hash": payload_hash,
                "count": len(events),
                "new_results": new_results,
                "events": events,
                "signals": signals,
                "agg_score": agg_score,
                "agg_direction": agg_direction,
            }
            return out, 200
        except Exception as exc:
            self._services.logger.exception("Error en /monitoreo/eventos")
            return {"status": "error", "message": str(exc)}, 500
