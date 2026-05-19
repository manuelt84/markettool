"""FMP client with per-symbol concurrency guards."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Dict, Optional
import threading
import logging
import time
import requests
import pandas as pd
import pytz
from zoneinfo import ZoneInfo
from datetime import datetime

from markettool.infra.fmp.ledger import record_fmp_call


class FMPError(Exception):
    pass


class FMPPlanNotAllowed(FMPError):
    pass


@dataclass
class FMPClient:
    api_key: str
    plan: str = "premium"
    timeout: int = 10
    http_session: requests.Session | None = None
    intraday_source_tz: str = "America/New_York"
    max_concurrency: int = 6
    per_symbol_concurrency: int = 1

    def __post_init__(self) -> None:
        self._global_sem = (
            threading.BoundedSemaphore(self.max_concurrency)
            if self.max_concurrency > 0
            else None
        )
        self._symbol_sems: dict[str, threading.BoundedSemaphore] = {}
        self._symbol_sems_lock = threading.Lock()
        self._log = logging.getLogger("MarketTool.FMP")

    def _get_symbol_sem(self, symbol: str) -> threading.BoundedSemaphore | None:
        if self.per_symbol_concurrency <= 0:
            return None
        key = (symbol or "").strip().upper()
        if not key:
            return None
        with self._symbol_sems_lock:
            sem = self._symbol_sems.get(key)
            if sem is None:
                sem = threading.BoundedSemaphore(self.per_symbol_concurrency)
                self._symbol_sems[key] = sem
        return sem

    @contextmanager
    def _http_guard(self, symbol: str | None = None):
        sems: list[threading.BoundedSemaphore] = []
        if self._global_sem is not None:
            sems.append(self._global_sem)
        if symbol:
            sym_sem = self._get_symbol_sem(symbol)
            if sym_sem is not None:
                sems.append(sym_sem)
        for sem in sems:
            sem.acquire()
        try:
            yield
        finally:
            for sem in reversed(sems):
                sem.release()

    def _get(self, url: str, params: Dict[str, Any] | None = None, symbol: str | None = None) -> requests.Response:
        params = dict(params or {})
        params.setdefault("apikey", self.api_key)
        if self.http_session is None:
            self.http_session = requests.Session()
        start = time.perf_counter()
        status_code = None
        response_bytes = 0
        error = None
        try:
            with self._http_guard(symbol):
                r = self.http_session.get(url, params=params, timeout=self.timeout)
            status_code = r.status_code
            try:
                response_bytes = len(r.content or b"")
            except Exception:
                response_bytes = 0
        except Exception as exc:
            error = exc
            raise
        finally:
            record_fmp_call(
                url=url,
                status_code=status_code,
                elapsed_ms=int((time.perf_counter() - start) * 1000),
                response_bytes=response_bytes,
                symbol=symbol,
                error=str(error) if error else None,
            )
        if r.status_code == 402:
            raise FMPPlanNotAllowed(f"402 Payment Required: {url}")
        return r

    def historical_intraday(self, symbol: str, interval: str, from_utc: datetime, to_utc: datetime) -> pd.DataFrame:
        interval = normalize_tf(interval)
        assert interval in {"1min", "5min", "15min", "30min", "1hour", "4hour"}
        fmt = "%Y-%m-%d %H:%M:%S"
        
        # ✅ FIX: Convert UTC timestamps to FMP's expected timezone (America/New_York)
        # FMP API expects timestamps in ET/NY time, not UTC
        try:
            ny_tz = pytz.timezone(self.intraday_source_tz)
        except Exception:
            ny_tz = pytz.timezone("America/New_York")
        
        # Convert UTC to NY timezone
        from_ny = from_utc.astimezone(ny_tz) if from_utc.tzinfo else ny_tz.localize(from_utc)
        to_ny = to_utc.astimezone(ny_tz) if to_utc.tzinfo else ny_tz.localize(to_utc)
        
        url = f"https://financialmodelingprep.com/api/v3/historical-chart/{interval}/{symbol}"
        self._log.info("[FMP] Historical Intraday %s from=%s to=%s (NY time)", url, from_ny.strftime(fmt), to_ny.strftime(fmt))
        r = self._get(url, {"from": from_ny.strftime(fmt), "to": to_ny.strftime(fmt)}, symbol=symbol)
        if r.status_code != 200:
            return pd.DataFrame()
        data = r.json() or []
        if not isinstance(data, list) or not data:
            return pd.DataFrame()
        df = pd.DataFrame(data)
        if "date" not in df.columns:
            return pd.DataFrame()
        cols = [c for c in ["date", "open", "high", "low", "close", "volume"] if c in df.columns]
        df = df[cols].copy()

        s = pd.to_datetime(df["date"], errors="coerce")
        try:
            tz_src = pytz.timezone(self.intraday_source_tz)
        except Exception:
            tz_src = pytz.UTC
        try:
            if getattr(s.dt, "tz", None) is None:
                try:
                    s = s.dt.tz_localize(tz_src, ambiguous="infer", nonexistent="shift_forward")
                except Exception:
                    s = s.dt.tz_localize(tz_src, ambiguous="NaT", nonexistent="shift_forward")
            else:
                s = s.dt.tz_convert(tz_src)
        except Exception:
            try:
                s = s.dt.tz_localize(tz_src)
            except Exception:
                pass
        s = s.dt.tz_convert(pytz.UTC)
        df["date"] = s
        df = df.dropna(subset=["date"]).set_index("date").sort_index()
        for c in ["open", "high", "low", "close", "volume"]:
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors="coerce")
        return df

    def historical_eod(self, symbol: str, from_date: datetime, to_date: datetime) -> pd.DataFrame:
        url = f"https://financialmodelingprep.com/api/v3/historical-price-full/{symbol}"
        self._log.info("[FMP] Historical Daily %s", url)
        
        # ✅ FIX: Convert UTC to NY timezone for FMP API consistency
        try:
            ny_tz = pytz.timezone(self.intraday_source_tz)  # "America/New_York"
        except Exception:
            ny_tz = pytz.timezone("America/New_York")
        
        from_ny = from_date.astimezone(ny_tz) if from_date.tzinfo else ny_tz.localize(from_date)
        to_ny = to_date.astimezone(ny_tz) if to_date.tzinfo else ny_tz.localize(to_date)
        
        r = self._get(url, {"from": from_ny.strftime("%Y-%m-%d"), "to": to_ny.strftime("%Y-%m-%d")}, symbol=symbol)
        if r.status_code != 200:
            return pd.DataFrame()
        payload = r.json() or {}
        hist = payload.get("historical") or []
        if not hist:
            return pd.DataFrame()

        df = pd.DataFrame(hist)
        if "date" not in df.columns:
            return pd.DataFrame()

        cols = [c for c in ["date", "open", "high", "low", "close", "volume"] if c in df.columns]
        df = df[cols].copy()

        ny = ZoneInfo("America/New_York")
        dt_day = pd.to_datetime(df["date"], errors="coerce")
        df["date"] = (
            dt_day.dt.tz_localize(ny)
            .dt.tz_convert("UTC")
            .dt.normalize() + pd.Timedelta(hours=20)
        )
        df = df.dropna(subset=["date"]).set_index("date").sort_index()
        for c in ["open", "high", "low", "close", "volume"]:
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors="coerce")
        return df

    def quote_last(self, symbol: str) -> Optional[float]:
        url = f"https://financialmodelingprep.com/api/v3/quote/{symbol}"
        self._log.info("[FMP] Quote %s", url)
        r = self._get(url, {}, symbol=symbol)
        if r.status_code != 200:
            return None
        arr = r.json() or []
        if not arr or not isinstance(arr, list):
            return None
        q = arr[0]
        for k in ("price", "c", "close", "previousClose"):
            if k in q and q[k] is not None:
                try:
                    return float(q[k])
                except Exception:
                    continue
        return None


def normalize_tf(tf: str) -> str:
    m = (tf or "").strip().lower()
    tf_map = {
        "1m": "1min", "1min": "1min",
        "5m": "5min", "5min": "5min",
        "15m": "15min", "15min": "15min",
        "30m": "30min", "30min": "30min",
        "1h": "1hour", "h1": "1hour", "1hour": "1hour",
        "4h": "4hour", "h4": "4hour", "4hour": "4hour",
        "1d": "1day", "d1": "1day", "1day": "1day",
        "1w": "1week", "w1": "1week", "1week": "1week",
        "1mo": "1month", "1month": "1month",
    }
    return tf_map.get(m, m)
