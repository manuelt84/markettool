"""Historicos service and history manager."""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional, Dict, Tuple

import pandas as pd
import numpy as np
import pytz
pass
from google.cloud import firestore

from markettool.core.time import ensure_utc_index, utc_now
from markettool.core.ports.historical_data_provider import HistoricalDataProvider
from markettool.core.errors import PlanNotAllowed
from markettool.infra.fmp import normalize_tf
from markettool.infra.cache.historicos_cache import load_cached_history, save_cached_history
from markettool.infra.storage.vps_json_store import PostgresDocumentStore, vps_mode_enabled


logger = logging.getLogger("MarketTool")


RESAMPLE_PLAN: Dict[str, Tuple[str, str]] = {
    "15min": ("5min", "15min"),
    "30min": ("5min", "30min"),
    "4hour": ("1hour", "4h"),
}

EOD_RESAMPLE_RULE: Dict[str, str] = {"1week": "W", "1month": "M"}


@dataclass
class HistoryConfig:
    bars: Optional[int] = None
    append_realtime: bool = True
    allow_refresh: bool = True
    fmp_window: Optional[int] = None
    from_timestamp: Optional[datetime] = None  # ✅ NEW: Override default from_dt for incremental fetch


# --------------------------- Historical merge helpers ---------------------------

def merge_histories(*parts):
    """
    Acepta multiples DataFrames OHLCV (o una lista como primer argumento) y devuelve
    un unico DataFrame con indice UTC, ordenado y sin duplicados.
    """
    if len(parts) == 1 and isinstance(parts[0], (list, tuple)):
        parts = tuple(parts[0])

    valid = []
    for df in parts:
        if df is None or getattr(df, "empty", True):
            continue
        d = df.copy()
        if not isinstance(d.index, pd.DatetimeIndex):
            if "time" in d.columns:
                d["time"] = pd.to_datetime(d["time"], errors="coerce", utc=True)
                d = d.dropna(subset=["time"]).set_index("time")
            else:
                d.index = pd.to_datetime(d.index, errors="coerce", utc=True)
        if d.index.tz is None:
            d.index = d.index.tz_localize(pytz.UTC)

        for c in ["open", "high", "low", "close", "volume"]:
            if c not in d.columns:
                d[c] = np.nan
            else:
                d[c] = pd.to_numeric(d[c], errors="coerce")
        d = d[["open", "high", "low", "close", "volume"]]
        valid.append(d)

    if not valid:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

    out = valid[0]
    if len(valid) > 1:
        out = pd.concat(valid, axis=0, ignore_index=False)
    out = out[~out.index.isna()].sort_index()
    out = out[~out.index.duplicated(keep="last")]
    return out


def normalize_resample_rule(rule: str) -> str:
    if not rule:
        return rule
    return rule.replace("H", "h")


class HistoryManager:
    """Manages historical data fetching, caching, and resampling."""
    
    def __init__(self, provider: HistoricalDataProvider):
        """
        Initialize HistoryManager with a HistoricalDataProvider.
        
        Args:
            provider: Implementation of HistoricalDataProvider port (e.g., FMPHistoricalDataAdapter)
        """
        self.provider = provider

    def _base_interval_for(self, tf: str) -> str:
        tf = normalize_tf(tf)
        return RESAMPLE_PLAN.get(tf, (tf, ""))[0]

    def _timedelta_for(self, tf: str, units: int) -> timedelta:
        tf = normalize_tf(tf)
        return {
            "1min": timedelta(minutes=units),
            "5min": timedelta(minutes=5 * units),
            "15min": timedelta(minutes=15 * units),
            "30min": timedelta(minutes=30 * units),
            "1hour": timedelta(hours=units),
            "4hour": timedelta(hours=4 * units),
            "1day": timedelta(days=units),
            "1week": timedelta(weeks=units),
            "1month": timedelta(days=30 * units),
        }.get(tf, timedelta(days=units))

    def _maybe_resample(self, df: pd.DataFrame, tf: str) -> pd.DataFrame:
        tf = normalize_tf(tf)
        if df is None or df.empty:
            return df
        if tf not in RESAMPLE_PLAN:
            return df
        _, rule = RESAMPLE_PLAN[tf]
        g = df.resample(normalize_resample_rule(rule), label="right", closed="right").agg({
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "volume": "sum",
        })
        return g.dropna(subset=["open", "high", "low", "close"])

    def _maybe_resample_eod(self, df: pd.DataFrame, tf: str) -> pd.DataFrame:
        rule = EOD_RESAMPLE_RULE.get(normalize_tf(tf))
        if not rule or df is None or df.empty:
            return df
        g = df.resample(normalize_resample_rule(rule), label="right", closed="right").agg({
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "volume": "sum",
        })
        return g.dropna(subset=["open", "high", "low", "close"])

    def _append_realtime_last_bar(self, symbol: str, tf: str, df: pd.DataFrame) -> pd.DataFrame:
        try:
            last_ts = df.index[-1]
            now = utc_now()
            lag_min = (now - last_ts).total_seconds() / 60.0
            tol = {"1min": 3, "5min": 7, "15min": 18, "30min": 35, "1hour": 70, "4hour": 260}.get(
                normalize_tf(tf),
                180,
            )
            if lag_min > tol:
                return df
            px = self.provider.quote_last(symbol)
            if px is None or math.isnan(px) or px <= 0:
                return df
            out = df.copy()
            h = float(out.iloc[-1]["high"])
            l = float(out.iloc[-1]["low"])
            out.iloc[-1, out.columns.get_loc("high")] = max(h, px)
            out.iloc[-1, out.columns.get_loc("low")] = min(l, px)
            out.iloc[-1, out.columns.get_loc("close")] = px
            return out
        except Exception:
            return df

    def _is_intraday(self, tf: str) -> bool:
        return normalize_tf(tf) in {"1min", "5min", "15min", "30min", "1hour", "4hour"}

    def get(self, symbol: str, tf: str, cfg: HistoryConfig | None = None) -> pd.DataFrame:
        cfg = cfg or HistoryConfig()
        tf = normalize_tf(tf)
        cache_df = load_cached_history(symbol, tf)

        if not hasattr(self, "_valid_symbols"):
            try:
                db = PostgresDocumentStore.from_env() if vps_mode_enabled() else firestore.Client()
                if db is None:
                    raise RuntimeError("Postgres document store is not configured")
                activos = set()
                activos_docs = db.collection("config").document("activos").get()
                if activos_docs.exists:
                    activos_data = activos_docs.to_dict()
                    activos.update(activos_data.get("symbols", []))
                categorias_docs = db.collection("config").document("categorias").get()
                if categorias_docs.exists:
                    categorias_data = categorias_docs.to_dict().get("data", {})
                    for arr in categorias_data.values():
                        if isinstance(arr, list):
                            activos.update(arr)
                cat_words = {k.strip().upper() for k in categorias_data.keys() if isinstance(k, str) and k.strip()}
                cat_words.update({"TODOS", "ALL"})
                self._valid_symbols = set(
                    str(s).strip().upper()
                    for s in activos
                    if isinstance(s, str) and s.strip() and str(s).strip().upper() not in cat_words
                )
            except Exception as exc:
                logger.warning("[FIRESTORE] Error al recuperar activos validos: %s", exc)
                self._valid_symbols = set()
        if symbol not in self._valid_symbols:
            logger.info("[FILTRO] Ignorado simbolo no valido: %s", symbol)
            return pd.DataFrame()

        now = utc_now()
        
        # ✅ NEW: If from_timestamp is explicitly provided, use it (incremental fetch override)
        if cfg.from_timestamp is not None:
            from_dt = cfg.from_timestamp
            if from_dt.tzinfo is None or from_dt.tzinfo == pytz.UTC:
                from_dt = from_dt if from_dt.tzinfo else pytz.UTC.localize(from_dt)
            else:
                from_dt = from_dt.astimezone(pytz.UTC)
            logger.info(f"[HIST] Incremental fetch override: from_timestamp={from_dt} for {symbol}/{tf}")
        elif cache_df.empty:
            from_dt = datetime(1900, 1, 1, tzinfo=pytz.UTC)
        else:
            try:
                last = cache_df.index[-1]
                if not isinstance(last, pd.Timestamp):
                    last = pd.to_datetime(last, utc=True)
                else:
                    last = last.to_pydatetime()

                if getattr(last, "tzinfo", None) is None or last.tzinfo is None:
                    last = pytz.UTC.localize(last)
                elif last.tzinfo != pytz.UTC:
                    last = last.astimezone(pytz.UTC)

                base_tf = self._base_interval_for(tf)
                from_dt = last + self._timedelta_for(base_tf, 1)
            except Exception as idx_err:
                logger.warning("[HIST][ERROR] Index parsing failed for %s/%s: %s. Using fallback (1900).", symbol, tf, idx_err)
                from_dt = datetime(1900, 1, 1, tzinfo=pytz.UTC)

        to_dt = now
        new_df = pd.DataFrame()
        if cfg.allow_refresh and from_dt < to_dt:
            try:
                if self._is_intraday(tf):
                    base_tf = self._base_interval_for(tf)
                    raw = self.provider.historical_intraday(symbol, base_tf, from_dt, to_dt)
                    raw = ensure_utc_index(raw)
                    new_df = self._maybe_resample(raw, tf)
                else:
                    raw = self.provider.historical_eod(symbol, from_dt, to_dt)
                    raw = ensure_utc_index(raw)
                    new_df = self._maybe_resample_eod(raw, tf) if tf in EOD_RESAMPLE_RULE else raw
            except PlanNotAllowed:
                logger.info("Plan no permite intradia para %s (%s).", symbol, tf)
                new_df = pd.DataFrame()
            except Exception as exc:
                logger.warning("Descarga fallida %s %s: %s", symbol, tf, exc)
                new_df = pd.DataFrame()

        out_full = merge_histories(cache_df, new_df)
        out = out_full
        if cfg.bars and isinstance(cfg.bars, int) and cfg.bars > 0 and len(out) > cfg.bars:
            out = out.tail(cfg.bars)
        if cfg.append_realtime and not out.empty:
            out = self._append_realtime_last_bar(symbol, tf, out)
        if not out_full.empty:
            save_cached_history(symbol, tf, out_full)
        return out
