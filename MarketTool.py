
import os
import sys
import math
import time
import json
import logging
import signal
import functools
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, Tuple, List, Callable, Iterable
from datetime import datetime, timedelta, timezone
import pytz
import requests
import pandas as pd
import numpy as np
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from asgiref.wsgi import WsgiToAsgi
from asyncio import Lock, Semaphore
from collections import Counter
from collections import defaultdict
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta, date, datetime, timezone, UTC, timezone as dt_timezone
from flask import Flask, request, jsonify
from functools import partial
from google.cloud import firestore
from google.cloud.firestore_v1 import FieldFilter
from google.cloud import firestore as gcf
from google.cloud import storage
from icalendar import Calendar, Event
from io import StringIO, BytesIO
from joblib import Parallel, delayed, parallel_backend
from numba import njit
from pandas.tseries.offsets import CustomBusinessDay
from scipy.signal import argrelextrema
from statsmodels.tsa.arima.model import ARIMA
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, BotCommand, BotCommandScopeChat
from telegram import InputFile
from telegram.error import TimedOut
from telegram.ext import ApplicationBuilder, Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters, CallbackContext
from telegram.helpers import escape_markdown
from textblob import TextBlob
from textwrap import wrap
from threading import Lock
from typing import Any, Iterable, Mapping, Optional, Callable, Dict, Tuple, List
from ultralytics import YOLO
from urllib.parse import urlencode
from uvicorn.config import LOGGING_CONFIG
from pydantic import BaseModel
from zoneinfo import ZoneInfo
import aiofiles
import asyncio
import base64
import concurrent.futures
import csv as _csv
import cv2
import datetime as _dt
import easyocr
import hashlib
import investpy
import matplotlib
import matplotlib as mpl
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import pytz # Para manejar las zonas horarias
import re
import socket
import statistics
import telegram
import tempfile
import torch
import uuid
import uvicorn
import warnings
import threading


# ======================================================================
# Config & Infra (Production-grade)
# ======================================================================

@dataclass
class AppConfig:
    storage_format: str = field(default_factory=lambda: os.environ.get("STORAGE_FORMAT", "json").strip().lower())
    fmp_plan: str = field(default_factory=lambda: (os.environ.get("FMP_PLAN") or "premium").strip().lower())
    fmp_api_key: str = field(default_factory=lambda: os.environ.get("API_FMP", ""))
    http_timeout: int = field(default_factory=lambda: int(os.environ.get("HTTP_TIMEOUT", "10")))
    http_retries: int = field(default_factory=lambda: int(os.environ.get("HTTP_RETRIES", "3")))
    http_backoff: float = field(default_factory=lambda: float(os.environ.get("HTTP_BACKOFF", "1.8")))
    hist_dir: str = field(default_factory=lambda: os.environ.get("HIST_DIR", "historicos"))
    log_level: str = field(default_factory=lambda: os.environ.get("LOG_LEVEL", "INFO"))
    econ_chunk_days: int= field(default_factory=lambda: int(os.environ.get("ECON_CHUNK_DAYS","31")))



# --------- .env loader (supports --env / -env) ---------
import sys, argparse
try:
    from dotenv import load_dotenv
except Exception:
    load_dotenv = None

def _early_load_env():
    # Allow: python script.py --env .env   or   -env .env
    env_path = None
    if ('--env' in sys.argv) or ('-env' in sys.argv):
        p = argparse.ArgumentParser(add_help=False)
        p.add_argument('--env','-env', dest='env_path', default='.env')
        args, _ = p.parse_known_args()
        env_path = args.env_path
    # If dotenv is present, load either explicit or default .env
    if load_dotenv:
        if env_path:
            load_dotenv(env_path)
        else:
            # Load default .env if exists
            import os
            if os.path.exists('.env'):
                load_dotenv('.env')

_early_load_env()
# -------------------------------------------------------
APP_CONFIG = AppConfig()
FMP_INTRADAY_SOURCE_TZ = os.getenv("FMP_INTRADAY_SOURCE_TZ", "America/New_York")


# Structured logging
ECON_CHUNK_DAYS = int(os.environ.get("ECON_CHUNK_DAYS","31"))
_LOGGER_FORMAT = "%(levelname)s:%(asctime)s:%(name)s:%(message)s"
logging.basicConfig(level=getattr(logging, APP_CONFIG.log_level.upper(), logging.INFO),
                    format=_LOGGER_FORMAT)
logger = logging.getLogger("MarketTool")

# HTTP Session with retries
def _build_session(retries: int, backoff: float) -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=retries,
        read=retries,
        connect=retries,
        status=retries,
        backoff_factor=backoff,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET","POST","PUT","DELETE","HEAD","OPTIONS"]),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_maxsize=20)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session

HTTP_SESSION = _build_session(APP_CONFIG.http_retries, APP_CONFIG.http_backoff)

# Graceful shutdown hooks (optional for long-running services)
_SHOULD_STOP = False
def _signal_handler(signum, frame):
    global _SHOULD_STOP
    logger.warning("Received signal %s — initiating graceful shutdown...", signum)
    _SHOULD_STOP = True

try:
    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)
except Exception:
    pass  # Not all environments allow signal handling (e.g., Windows/threads)

# ======================================================================
# Timezone/Datetime utilities — UTC-first
# ======================================================================

def utc_now() -> datetime:
    return datetime.now(timezone.utc)

def ensure_utc_index(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty: return df
    out = df.copy()
    idx = pd.to_datetime(out.index, utc=True, errors="coerce")
    out.index = idx
    if out.index.tz is None:
        out.index = out.index.tz_localize(pytz.UTC)
    return out

def get_local_tz() -> pytz.BaseTzInfo:
    return pytz.UTC


# ======================================================================
# Error-handling decorator
# ======================================================================
def safe_op(default=None, log: logging.Logger | None = None):
    log = log or logger
    def _decorator(fn: Callable):
        @functools.wraps(fn)
        def _wrapped(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except Exception as e:
                log.warning("%s failed: %s", fn.__name__, e, exc_info=False)
                return default
        return _wrapped
    return _decorator



# ======================================================================
# FMP historicals — UTC-first with cache & resampling
# ======================================================================

class FMPError(Exception): ...
class FMPPlanNotAllowed(FMPError): ...

def normalize_tf(tf: str) -> str:
    m = (tf or "").strip().lower()
    tf_map = {
        "1m":"1min","1min":"1min",
        "5m":"5min","5min":"5min",
        "15m":"15min","15min":"15min",
        "30m":"30min","30min":"30min",
        "1h":"1hour","h1":"1hour","1hour":"1hour",
        "4h":"4hour","h4":"4hour","4hour":"4hour",
        "1d":"1day","d1":"1day","1day":"1day",
        "1w":"1week","w1":"1week","1week":"1week",
        "1mo":"1month","1month":"1month",
    }
    return tf_map.get(m, m)

def _is_intraday(tf: str) -> bool:
    return normalize_tf(tf) in {"1min","5min","15min","30min","1hour","4hour"}

DEFAULT_FMP_WINDOWS: Dict[str, int] = {
    "1min":  2400, "5min": 2000, "15min": 1600, "30min": 1600,
    "1hour": 1600, "4hour": 2200, "1day": 2000, "1week": 520, "1month": 240
}
RESAMPLE_PLAN: Dict[str, Tuple[str, str]] = {
    "15min": ("5min",  "15min"),
    "30min": ("5min",  "30min"),
    "4hour": ("1hour", "4h"),
}
EOD_RESAMPLE_RULE: Dict[str, str] = {"1week": "W", "1month": "M"}

HIST_GRACE_S = {"1min": 5, "5min": 5, "15min": 8, "30min": 10, "1hour": 15, "4hour": 20}
QUOTE_TTL    = {
    "1min": 0.15,   # antes 0.3 → hasta ~10 ticks/s
    "5min": 1,     # antes 2
    "15min": 5,    # antes 10
    "30min": 10,   # antes 15
    "1hour": 15,   # antes 20
    "4hour": 20,   # antes 30
}
SYNC_TTL     = {
    "1min": 30,    # antes 60
    "5min": 90,    # antes 120
    "15min": 180,  # antes 240
    "30min": 240,  # antes 300
    "1hour": 480,  # antes 600
    "4hour": 900,  # antes 1200
}

_LAST_QUOTE_TICK: Dict[tuple, float] = {}  # (exec_id, symbol, tf) -> epoch_s
_LAST_SYNC: Dict[tuple, float] = {}

_STOP_WORDS = {"stopped", "paused", "off", "detenido", "parado"}

def _norm_tf_allowed(tf: str) -> str:
    """
    Forma CANÓNICA para allowed_timeframes:
    1min/1m/1hour/1h/1day/1d/1week/1w -> 1m,1h,1d,1w, etc.
    (lo que estás guardando en allowed_timeframes)
    """
    s = (tf or "").lower()
    if s in ("1m", "1min"):      return "1m"
    if s in ("5m", "5min"):      return "5m"
    if s in ("15m", "15min"):    return "15m"
    if s in ("30m", "30min"):    return "30m"
    if s in ("1h", "1hour", "h1"):   return "1h"
    if s in ("4h", "4hour", "h4"):   return "4h"
    if s in ("1d", "1day", "d1"):    return "1d"
    if s in ("1w", "1week", "w1"):   return "1w"
    return s


def _norm_tf_backend(tf: str) -> str:
    """
    Normaliza TF hacia lo que escribe el front en tf_states (1min, 1day, 1week, etc.)
    """
    s = (tf or "").lower()
    if s in ("1m", "1min", "1"):    return "1min"
    if s in ("5m", "5min"):         return "5min"
    if s in ("15m", "15min"):       return "15min"
    if s in ("30m", "30min"):       return "30min"
    if s in ("1h", "1hour"):        return "1hour"
    if s in ("4h", "4hour"):        return "4hour"
    if s in ("1d", "1day", "d1"):   return "1day"
    if s in ("1w", "1week", "w1"):  return "1week"
    return s


TF_TTL_MINUTES = {
    "1m":  5,
    "5m":  10,
    "15m": 30,
    "30m": 60,
    "1h":  180,
    "4h":  360,
    "1d":  1440,
    "1w":  10080,
}


def _tf_is_enabled(exec_id: str, symbol: str, tf: str) -> bool:
    """
    Devuelve True si ese TF se considera activo según monitoreos/{exec_id}__{symbol}.

    Regla:
      1) Si estado global está 'stopped'/etc → False
      2) Si tf_states[tf] dice 'stopped' o enabled=False → False
      3) Si tf_states[tf].enabled=True → True
      4) Si no hay info específica → miramos allowed_timeframes
      5) Opcional: TTL por last_ts/updated_at (en ms)
    """
    # Normalizaciones básicas
    symbol = (symbol or "").upper()
    tf_backend = _norm_tf_backend(tf)   # p.ej "1" / "1m" -> "1min"
    tf_allowed = _norm_tf_allowed(tf)   # p.ej "1" / "1min" -> "1m"

    # Cargamos el documento de monitoreo
    doc_id = f"{exec_id}__{symbol}"
    snap = db.collection("monitoreos").document(doc_id).get()
    if not snap.exists:
        return False

    doc = snap.to_dict() or {}

    # 1) estado global (apagado → todo False)
    estado_global = str(doc.get("estado") or doc.get("status") or "").lower()
    if any(w in estado_global for w in _STOP_WORDS):
        return False

    # 2) Obtenemos el estado particular del TF
    tf_states = doc.get("tf_states") or {}

    # Prioridad:
    #   a) tf_states["1min"]
    #   b) tf_states["1m"]
    #   c) doc["1min"]  (campo raíz)
    #   d) doc["1m"]    (campo raíz)
    st = (
        tf_states.get(tf_backend)
        or tf_states.get(tf)
        or doc.get(tf_backend)
        or doc.get(tf)
        or {}
    )

    # 3) estado particular del TF
    estado_tf = str(st.get("estado") or "").lower()
    if any(w in estado_tf for w in _STOP_WORDS):
        return False

    enabled = st.get("enabled")
    if enabled is False:
        return False
    if enabled is True:
        # Si explícitamente está en True, ya consideramos habilitado
        # (sin mirar allowed_timeframes ni TTL, como en tu lógica original)
        return True
    # 4) Fallback: allowlist de timeframes.
    # Si no definiste allowlist, NO bloqueamos por defecto (evita que el backend
    # pise "running" → "stopped" solo por no tener allowed_timeframes configurado).
    allowed_list = (
        doc.get("allowed_timeframes")
        or doc.get("timeframes")
        or doc.get("tf_list")
        or doc.get("tfs")
        or []
    )
    if allowed_list:
        allowed_norm = {_norm_tf_allowed(x) for x in allowed_list}
        if tf_allowed not in allowed_norm:
            return False
    # 5) Opcional: TTL (en ms)
    last = (
        st.get("last_heartbeat_ms")
        or st.get("last_heartbeat")
        or st.get("last_ts")
        or st.get("updated_at_ms")
        or st.get("updated_at")
    )
    last_ms = _to_ms(last)
    if last_ms is not None:
        now_ms = int(time.time() * 1000)
        tf_key = tf_backend  # "1min", "5min", etc.
        ttl_minutes = TF_TTL_MINUTES.get(tf_key, 60)
        if now_ms - last_ms > ttl_minutes * 60_000:
            return False

    return True


@dataclass
class FMPClient:
    api_key: str
    plan: str = "premium"
    timeout: int = field(default_factory=lambda: APP_CONFIG.http_timeout)

    def _get(self, url: str, params: Dict[str, Any] | None = None) -> requests.Response:
        params = dict(params or {})
        params.setdefault("apikey", self.api_key)
        r = HTTP_SESSION.get(url, params=params, timeout=self.timeout)
        if r.status_code == 402:
            raise FMPPlanNotAllowed(f"402 Payment Required: {url}")
        return r

    @safe_op(default=pd.DataFrame(), log=logging.getLogger("MarketTool.FMP"))
    def historical_intraday(self, symbol: str, interval: str,
                            from_utc: datetime, to_utc: datetime) -> pd.DataFrame:
        interval = normalize_tf(interval)
        assert interval in {"1min","5min","15min","30min","1hour","4hour"}
        fmt = "%Y-%m-%d %H:%M:%S"
        url = f"https://financialmodelingprep.com/api/v3/historical-chart/{interval}/{symbol}"
        logging.info(f"MTORO5 {url}")
        r = self._get(url, {"from": from_utc.strftime(fmt), "to": to_utc.strftime(fmt)})
        if r.status_code != 200: return pd.DataFrame()
        data = r.json() or []
        if not isinstance(data, list) or not data: return pd.DataFrame()
        df = pd.DataFrame(data)
        if "date" not in df.columns: return pd.DataFrame()
        cols = [c for c in ["date","open","high","low","close","volume"] if c in df.columns]
        df = df[cols].copy()
        # Parse naive timestamp from FMP intraday, localize to source tz, then convert to UTC
        s = pd.to_datetime(df["date"], errors="coerce")
        try:
            tz_src = pytz.timezone(FMP_INTRADAY_SOURCE_TZ)
        except Exception:
            tz_src = pytz.UTC
        try:
            # If series is tz-naive, localize; if tz-aware, convert
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
        for c in ["open","high","low","close","volume"]:
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors="coerce")
                return df

    @safe_op(default=pd.DataFrame(), log=logging.getLogger("MarketTool.FMP"))
    def historical_eod(self, symbol: str, from_date: datetime, to_date: datetime) -> pd.DataFrame:
        url = f"https://financialmodelingprep.com/api/v3/historical-price-full/{symbol}"
        logging.info(f"MTORO6 {url}")
        r = self._get(url, {"from": from_date.strftime("%Y-%m-%d"), "to": to_date.strftime("%Y-%m-%d")})
        if r.status_code != 200: return pd.DataFrame()
        payload = r.json() or {}; hist = payload.get("historical") or []
        if not hist: return pd.DataFrame()

        df = pd.DataFrame(hist)
        if "date" not in df.columns: return pd.DataFrame()

        cols = [c for c in ["date","open","high","low","close","volume"] if c in df.columns]
        df = df[cols].copy()

        ny = ZoneInfo("America/New_York")
        dt_day =  pd.to_datetime(df["date"], errors="coerce")

        df["date"] = (
        dt_day.dt.tz_localize(ny)
              .dt.tz_convert("UTC")
              .dt.normalize() + pd.Timedelta(hours=20)
              )
              
        df = df.dropna(subset=["date"]).set_index("date").sort_index()
        for c in ["open","high","low","close","volume"]:
            if c in df.columns: df[c] = pd.to_numeric(df[c], errors="coerce")
        return df

    @safe_op(default=None, log=logging.getLogger("MarketTool.FMP"))
    def quote_last(self, symbol: str) -> Optional[float]:
        url = f"https://financialmodelingprep.com/api/v3/quote/{symbol}"
        logging.info(f"MTORO7 {url}")
        r = self._get(url, {})
        if r.status_code != 200: return None
        arr = r.json() or []
        if not arr or not isinstance(arr, list): return None
        q = arr[0]
        for k in ("price","c","close","previousClose"):
            if k in q and q[k] is not None:
                try: return float(q[k])
                except Exception: continue
        return None

# Cache CSV
def _safe_symbol_for_filename(symbol: str) -> str:
    import string
    allowed = set(string.ascii_letters + string.digits + "._-")
    return "".join(ch if ch in allowed else "_" for ch in symbol)


def _hist_base(symbol: str, tf: str) -> str:
    os.makedirs(APP_CONFIG.hist_dir, exist_ok=True)
    return os.path.join(APP_CONFIG.hist_dir, f"{_safe_symbol_for_filename(symbol)}__{normalize_tf(tf)}")

def _hist_path_csv(symbol: str, tf: str) -> str:
    return _hist_base(symbol, tf) + ".csv"

def _hist_path_json(symbol: str, tf: str) -> str:
    return _hist_base(symbol, tf) + ".json"

def _hist_path(symbol: str, tf: str) -> str:
    if hasattr(APP_CONFIG, "storage_format") and APP_CONFIG.storage_format == "json":
        return _hist_path_json(symbol, tf)
    return _hist_path_csv(symbol, tf)

def _ensure_cols(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for c in ["open","high","low","close","volume"]:
        if c in out.columns: out[c] = pd.to_numeric(out[c], errors="coerce")
        else: out[c] = np.nan
    return out[["open","high","low","close","volume"]]

@safe_op(default=pd.DataFrame(columns=["open","high","low","close","volume"]))
def load_cached_history(symbol: str, tf: str) -> pd.DataFrame:
    import json
    primary = _hist_path(symbol, tf)
    alt = _hist_path_json(symbol, tf) if primary.endswith(".csv") else _hist_path_csv(symbol, tf)

    def _from_df(df):
        df["time"] = pd.to_datetime(df["time"], errors="coerce", utc=True)
        df = df.dropna(subset=["time"]).set_index("time").sort_index()
        df = _ensure_cols(df)
        if df.index.tz is None: df.index = df.index.tz_localize(pytz.UTC)
        return df

    # Try primary
    if os.path.exists(primary):
        if primary.endswith(".csv"):
            df = pd.read_csv(primary)
            if "time" not in df.columns: return pd.DataFrame(columns=["open","high","low","close","volume"])
            return _from_df(df)
        else:
            raw = Path(primary).read_text(encoding="utf-8")
            data = json.loads(raw) if raw.strip() else []
            if isinstance(data, dict): data = data.get("data", [])
            df = pd.DataFrame(data)
            if "time" not in df.columns: return pd.DataFrame(columns=["open","high","low","close","volume"])
            return _from_df(df)

    # Fallback to alternative format
    if os.path.exists(alt):
        if alt.endswith(".csv"):
            df = pd.read_csv(alt)
            if "time" not in df.columns: return pd.DataFrame(columns=["open","high","low","close","volume"])
            return _from_df(df)
        else:
            raw = Path(alt).read_text(encoding="utf-8")
            data = json.loads(raw) if raw.strip() else []
            if isinstance(data, dict): data = data.get("data", [])
            df = pd.DataFrame(data)
            if "time" not in df.columns: return pd.DataFrame(columns=["open","high","low","close","volume"])
            return _from_df(df)

    return pd.DataFrame(columns=["open","high","low","close","volume"])
    df = pd.read_csv(p)
    if "time" not in df.columns:
        return pd.DataFrame(columns=["open","high","low","close","volume"])
    df["time"] = pd.to_datetime(df["time"], errors="coerce", utc=True)
    df = df.dropna(subset=["time"]).set_index("time").sort_index()
    df = _ensure_cols(df)
    if df.index.tz is None: df.index = df.index.tz_localize(pytz.UTC)
    return df

@safe_op(default=None)
def save_cached_history(symbol: str, tf: str, out: pd.DataFrame, *, storage_dir: str | None = None) -> None:
    """
    Persiste un recorte de historia ya combinada.
    - Asegura índice tz-aware UTC
    - Crea columna 'time' como ISO8601 sin usar .dt sobre DatetimeIndex
    - Guarda JSON enriquecido en carpeta temporal del SO (no asume /tmp)
    """
    try:
        if out is None or out.empty:
            return

        # Normaliza índice -> DatetimeIndex UTC
        idx_utc = pd.DatetimeIndex(pd.to_datetime(out.index, utc=True, errors="coerce"))
        # Filtra NaT por si acaso
        mask = ~idx_utc.isna()
        if not mask.all():
            out = out.loc[mask].copy()
            idx_utc = idx_utc[mask]

        # 'time' como string ISO (sin .dt)
        out = out.copy()
        out["time"] = idx_utc.strftime("%Y-%m-%dT%H:%M:%SZ")

        # Columnas estándar
        for c in ("open", "high", "low", "close", "volume"):
            if c not in out.columns:
                out[c] = pd.NA

        out = out[["time", "open", "high", "low", "close", "volume"]]

        # Nombre de archivo “enriched” y carpeta temporal cross-platform
        import re, os, json, tempfile
        safe_sym = re.sub(r"[^A-Za-z0-9_]+", "_", str(symbol))
        safe_tf  = re.sub(r"[^A-Za-z0-9_]+", "_", str(tf))
        nombre   = f"{safe_sym}_{safe_tf}_enriched.json"
        local_json = os.path.join(tempfile.gettempdir(), nombre)

        payload = out.tail(1000).to_dict(orient="records")
        with open(local_json, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)

        logger.debug("[save_cached_history] guardado %s filas=%d", local_json, len(payload))

    except Exception as e:
        # MUY importante mantener este mensaje, porque tus logs lo buscan por texto
        logger.warning("save_cached_history failed: %s", e)
        return


@dataclass
class HistoryConfig:
    bars: Optional[int] = None
    append_realtime: bool = True
    allow_refresh: bool = True


# --------------------------- Historical merge helpers ---------------------------
def merge_histories(*parts):
    """
    Acepta múltiples DataFrames OHLCV (o una lista como primer argumento) y devuelve
    un único DataFrame con índice UTC, ordenado y sin duplicados.
    """
    import pandas as pd, numpy as np
    # Soporta llamada merge_histories([df1, df2]) o merge_histories(df1, df2)
    if len(parts) == 1 and isinstance(parts[0], (list, tuple)):
        parts = tuple(parts[0])

    valid = []
    for df in parts:
        if df is None:
            continue
        if getattr(df, "empty", True):
            continue
        d = df.copy()
        # normaliza índice/columna time
        if not isinstance(d.index, pd.DatetimeIndex):
            if "time" in d.columns:
                d["time"] = pd.to_datetime(d["time"], errors="coerce", utc=True)
                d = d.dropna(subset=["time"]).set_index("time")
            else:
                d.index = pd.to_datetime(d.index, errors="coerce", utc=True)
        if d.index.tz is None:
            d.index = d.index.tz_localize(pytz.UTC)

        # columnas estándar
        for c in ["open","high","low","close","volume"]:
            if c not in d.columns:
                d[c] = np.nan
            else:
                d[c] = pd.to_numeric(d[c], errors="coerce")
        d = d[["open","high","low","close","volume"]]
        valid.append(d)

    if not valid:
        import pandas as pd
        return pd.DataFrame(columns=["open","high","low","close","volume"])

    out = valid[0]
    if len(valid) > 1:
        out = pd.concat(valid, axis=0, ignore_index=False)
    out = out[~out.index.isna()].sort_index()
    out = out[~out.index.duplicated(keep="last")]
    return out
# --------------------------------------------------------------------------------


def normalize_resample_rule(rule: str) -> str:
    if not rule:
        return rule
    return rule.replace("H","h")

class HistoryManager:
    def __init__(self, client: FMPClient):
        self.client = client

    def _base_interval_for(self, tf: str) -> str:
        tf = normalize_tf(tf)
        return RESAMPLE_PLAN.get(tf, (tf, ""))[0]

    def _timedelta_for(self, tf: str, units: int) -> timedelta:
        tf = normalize_tf(tf)
        return {
            "1min": timedelta(minutes=units),
            "5min": timedelta(minutes=5*units),
            "15min": timedelta(minutes=15*units),
            "30min": timedelta(minutes=30*units),
            "1hour": timedelta(hours=units),
            "4hour": timedelta(hours=4*units),
            "1day": timedelta(days=units),
            "1week": timedelta(weeks=units),
            "1month": timedelta(days=30*units),
        }.get(tf, timedelta(days=units))

    def _maybe_resample(self, df: pd.DataFrame, tf: str) -> pd.DataFrame:
        tf = normalize_tf(tf)
        if df is None or df.empty: return df
        if tf not in RESAMPLE_PLAN: return df
        _, rule = RESAMPLE_PLAN[tf]
        g = df.resample(normalize_resample_rule(rule), label="right", closed="right").agg({
            "open":"first","high":"max","low":"min","close":"last","volume":"sum"
        })
        return g.dropna(subset=["open","high","low","close"])

    def _maybe_resample_eod(self, df: pd.DataFrame, tf: str) -> pd.DataFrame:
        rule = EOD_RESAMPLE_RULE.get(normalize_tf(tf))
        if not rule or df is None or df.empty: return df
        g = df.resample(normalize_resample_rule(rule), label="right", closed="right").agg({
            "open":"first","high":"max","low":"min","close":"last","volume":"sum"
        })
        return g.dropna(subset=["open","high","low","close"])

    def _append_realtime_last_bar(self, symbol: str, tf: str, df: pd.DataFrame) -> pd.DataFrame:
        try:
            last_ts = df.index[-1]; now = utc_now()
            lag_min = (now - last_ts).total_seconds() / 60.0
            tol = {"1min":3,"5min":7,"15min":18,"30min":35,"1hour":70,"4hour":260}.get(normalize_tf(tf), 180)
            if lag_min > tol: return df
            px = self.client.quote_last(symbol)
            if px is None or math.isnan(px) or px <= 0: return df
            out = df.copy()
            h = float(out.iloc[-1]["high"]); l = float(out.iloc[-1]["low"])
            out.iloc[-1, out.columns.get_loc("high")]  = max(h, px)
            out.iloc[-1, out.columns.get_loc("low")]   = min(l, px)
            out.iloc[-1, out.columns.get_loc("close")] = px
            return out
        except Exception:
            return df

    def get(self, symbol: str, tf: str, cfg: HistoryConfig | None = None) -> pd.DataFrame:
        cfg = cfg or HistoryConfig()
        tf = normalize_tf(tf)
        cache_df = load_cached_history(symbol, tf)

        now = utc_now()
        if cache_df.empty:
            win = DEFAULT_FMP_WINDOWS.get(tf, 1000)
            from_dt = now - self._timedelta_for(tf, win)
        else:
            last = cache_df.index[-1]
            base_tf = self._base_interval_for(tf)
            from_dt = last + self._timedelta_for(base_tf, 1)

        to_dt = now
        new_df = pd.DataFrame()
        if cfg.allow_refresh and from_dt < to_dt:
            try:
                if _is_intraday(tf):
                    base_tf = self._base_interval_for(tf)
                    raw = self.client.historical_intraday(symbol, base_tf, from_dt, to_dt)
                    raw = ensure_utc_index(raw)
                    new_df = self._maybe_resample(raw, tf)
                else:
                    raw = self.client.historical_eod(symbol, from_dt, to_dt)
                    raw = ensure_utc_index(raw)
                    new_df = self._maybe_resample_eod(raw, tf) if tf in EOD_RESAMPLE_RULE else raw
            except FMPPlanNotAllowed:
                logger.info("Plan no permite intradía para %s (%s).", symbol, tf)
                new_df = pd.DataFrame()
            except Exception as e:
                logger.warning("Descarga fallida %s %s: %s", symbol, tf, e)
                new_df = pd.DataFrame()

        out = merge_histories(cache_df, new_df)
        if cfg.bars and isinstance(cfg.bars, int) and cfg.bars > 0 and len(out) > cfg.bars:
            out = out.tail(cfg.bars)
        if cfg.append_realtime and not out.empty:
            out = self._append_realtime_last_bar(symbol, tf, out)
        if not out.empty:
            save_cached_history(symbol, tf, out)
        return out

# Public API (overrides legacy)
_FMP = FMPClient(api_key=APP_CONFIG.fmp_api_key, plan=APP_CONFIG.fmp_plan)
_HIST = HistoryManager(client=_FMP)

def obtener_datos_historicos(symbol: str, temporalidad: str,
                             bars: Optional[int] = None,
                             append_realtime: bool = True,
                             allow_refresh: bool = True) -> pd.DataFrame:
    cfg = HistoryConfig(bars=bars, append_realtime=append_realtime, allow_refresh=allow_refresh)
    return _HIST.get(symbol, temporalidad, cfg=cfg)

def obtener_datos_historicos_fmp(symbol: str, temporalidad: str, *,
                                 bars: int | None = None, **kwargs):
    return obtener_datos_historicos(symbol, temporalidad, bars=bars, append_realtime=True, allow_refresh=True)


#mpl.rcParams['figure.max_open_warning'] = 200

matplotlib.use('Agg')

pd.set_option('future.no_silent_downcasting', True)

warnings.filterwarnings("ignore", message="Maximum Likelihood optimization failed to converge")

#from pathlib import Path
#from dotenv import load_dotenv
#load_dotenv(dotenv_path=Path(__file__).with_name(".env"))

try:
    # Si usas firebase_admin con google-cloud-firestore detrás:

    SERVER_TS = gcf.SERVER_TIMESTAMP
except Exception:
    SERVER_TS = None  # fallback si no está disponible

timezone_country = pytz.UTC
user_states = {}
timeout_request_global = 2 # Tiempo máximo de espera en segundos
max_workers_global = min(32, (os.cpu_count() or 1) * 2) #puede tener 64
cache_noticias = {}
subscriptions = {}
subscriptions_type = {}
admin_ids = {}

matplotlib_lock = threading.Lock()

CARPETA_HISTORICOS = "historicos"
CARPETA_FOREX_NEWS = "forex_news"

# Archivo JSON para almacenar las suscripciones
TIME_BETWEEN_MESSAGES = 1  # En segundos

#DIRECCION_USDT_TRC20 = 'TJ5HvX7EfNCrNFXHGCdGYQ59n5H6pcjm6b' #BINANCE
DIRECCION_USDT_TRC20 = 'TNYdZMs5eGYcwdY8vEAe59utu2RYhdyquh' #UNSTOPPABLE

# Memoria temporal para las noticias
cache_noticias = defaultdict(pd.DataFrame)  # Diccionario donde la clave es el símbolo
cache_historicos = {}
ultima_actualizacion_historicos = {}

señales_compra = ['Compra', 'Compra Fuerte', 'Compra Predicha', 'Compra Predicha con ARIMA', 'Compra Predicha con Media Movil', 'Compra Predicha con ARIMA y Media Movil']
señales_venta = ['Venta', 'Venta Fuerte', 'Venta Predicha', 'Venta Predicha con ARIMA', 'Venta Predicha con Media Movil', 'Venta Predicha con ARIMA y Media Movil']

file_locks = {}
guardar_lock = asyncio.Lock()

logging.basicConfig(level=logging.INFO, stream=sys.stdout, format='%(levelname)s:%(message)s')
logger = logging.getLogger(__name__)

logging.getLogger("httpx").setLevel(logging.WARNING)  # Para httpx
logging.getLogger("urllib3").setLevel(logging.WARNING)  # Para requests

# API Key de FMP (Premium)
API_KEY = (os.environ.get("API_FMP") or os.environ.get("API_FMP") or "").strip()
if not API_KEY:
    raise RuntimeError("Falta API_FMP (o FMP_API_KEY) en el entorno/.env. Usa --env .env o define la variable antes de ejecutar.")
db = firestore.Client()

# Personalizar los logs
LOGGING_CONFIG["handlers"]["default"] = {
    "level": "INFO",
    "class": "logging.StreamHandler",
    "stream": "ext://sys.stdout",  # Enviar a stdout
}

#log_file = open('output.log', 'w')
#sys.stdout = log_file
#sys.stderr = log_file

# Cargar el diccionario desde el archivo JSON
with open('palabras_clave_categoria.json', 'r', encoding='utf-8') as file:
    palabras_clave_categoria = json.load(file)

patrones_alcistas = [
    'Martillo', 'Martillo Invertido', 'Envolvente Alcista',
    'Bandera Alcista', 'Estrella del Amanecer',
    'Tres Soldados Blancos', 'Pinzas de Suelo',
    'Hombro Cabeza Hombro Invertido'
]

patrones_bajistas = [
    'Hombre Colgado', 'Envolvente Bajista', 'Bandera Bajista',
    'Estrella de la Noche', 'Tres Cuervos Negros',
    'Estrella Fugaz', 'Pinzas de Techo',
    'Harami Bajista', 'Hombro Cabeza Hombro'
]

# --- Ventanas por defecto para FMP (cantidad de velas a descargar) ---
DEFAULT_FMP_WINDOWS: dict[str, int] = {
    '1min':   2400,
    '5min':   2000,
    '15min':  1600,
    '30min':  1600,
    '1hour':  1600,
    '4hour':  2200,
    '1day':   2000,
    '1week':  520,
}

# --- Ventanas por defecto para los cálculos del back ---
DEFAULT_CALC_WINDOWS: dict[str, int] = {
    '1min':   14,
    '5min':   20,
    '15min':  20,
    '30min':  25,
    '1hour':  50,
    '4hour':  100,
    '1day':   200,
    '1week':  52,
}

_TF_ALIAS = {
    '1m':'1min',
    '5m':'5min',
    '15m':'15min',
    '30m':'30min',
    '1h':'1hour',
    '4h':'4hour',
    '1d':'1day',
    '1w':'1week'
}
_TF_MINUTES = {
    '1min': 1,
    '5min': 5,
    '15min': 15,
    '30min': 30,
    '1hour': 60,
    '4hour': 240,
    '1day': 1440,
    '1week': 10080,
}

TF_MAP = {
    '1m':'1min','5m':'5min','15m':'15min','30m':'30min',
    '1h':'1hour','4h':'4hour','1d':'1day','1w':'1week',
}

MY_ID   = os.getenv("WORKER_ID") or socket.gethostname()
MY_ADDR = (os.getenv("WORKER_ADDR") or "").strip()  # p. ej. "10.8.0.2:8103"
# (Opcional) valida formato/IP:puerto que expones
_ALLOW_TARGET = re.compile(r"^10\.8\.0\.(\d{1,3}):8(1|2|3)\d{2}$")  # rangos 81xx/82xx/83xx

# Registro de ejecuciones en curso: exec_id -> asyncio.Task
RUNNING: Dict[str, asyncio.Task] = {}

STOP_EVENTS: dict[str, threading.Event] = {}
STOP_EVENTS_LOCK = threading.Lock()

USER_STATE_STALE_SECONDS = int(os.getenv("USER_STATE_STALE_SECONDS", "180"))   # 3 min
USER_STATE_SWEEP_EVERY   = int(os.getenv("USER_STATE_SWEEP_EVERY", "60"))       # cada 60s
USER_STATE_BUSY_VALUES   = {"ocupado", "en_ejecucion", "esperando_grafico_ia", "running"}

modelo_patrones = YOLO("patrones.pt")
modelo_ruido = YOLO("ruido.pt")

# Lock global para simular carga
ocupado_lock = threading.Lock()

_reader = None
_reader_lock = threading.Lock()

# --- Compatibilidad legacy: evita NameError si algún módulo viejo lo menciona ---
try:
    fmp_map  # type: ignore[name-defined]
except NameError:
    fmp_map = None  # NO es default; solo evita NameError

try:
    calc_map  # type: ignore[name-defined]
except NameError:
    calc_map = None  # idem



def _as_unix(d):
    """
    Convierte distintos formatos posibles de updated_at a unix.
    Acepta: int/float, datetime, string ISO. Devuelve None si no se puede.
    """
    if d is None:
        return None
    if isinstance(d, (int, float)):
        return int(d)
    if hasattr(d, "timestamp"):  # Firestore Timestamp o datetime
        try:
            return int(d.timestamp())
        except Exception:
            pass
    if isinstance(d, str):
        try:
            # admite "2025-09-22T05:29:51.313045+00:00" o con 'Z'
            s = d.replace("Z", "+00:00")
            return int(datetime.fromisoformat(s).timestamp())
        except Exception:
            return None
    return None

def _sweep_stuck_user_states_once():
    now = int(time.time())
    cutoff = now - USER_STATE_STALE_SECONDS

    try:
        # No filtramos por campo de tiempo porque puede variar el tipo; filtramos en cliente.
        docs = db.collection("user_states").stream()
        batch = db.batch()
        pending = 0

        for doc in docs:
            data = doc.to_dict() or {}

            estado = str(data.get("estado") or "").lower()
            if estado not in USER_STATE_BUSY_VALUES:
                continue

            # preferimos updated_at_unix, si no, intentamos con otros
            ts = (
                _as_unix(data.get("updated_at_unix"))
                or _as_unix(data.get("updated_at"))
                or _as_unix(data.get("fecha_inicio"))
            )

            # si nunca tuvo timestamp o está vencido, liberamos
            if (ts is None) or (ts < cutoff):
                batch.set(
                    doc.reference,
                    {
                        "estado": "disponible",
                        "updated_at_unix": now,
                        "fecha_fin": datetime.now(timezone.utc).isoformat(),
                    },
                    merge=True,
                )
                pending += 1
                # evita batches gigantes
                if pending % 400 == 0:
                    batch.commit()
                    batch = db.batch()

        if pending:
            batch.commit()

    except Exception as e:
        logger.warning(f"[watchdog] error barriendo user_states: {e}")

def _user_states_watchdog_loop():
    # pequeño delay para no pelearse con el arranque
    time.sleep(5)
    while True:
        _sweep_stuck_user_states_once()
        time.sleep(USER_STATE_SWEEP_EVERY)

if os.getenv("ENABLE_USER_STATE_WATCHDOG", "1") == "1":
    threading.Thread(target=_user_states_watchdog_loop, daemon=True).start()
# ====== /Watchdog user_states ======

def _now_unix() -> int:
    return int(time.time())

def fs_marcar_worker(
    exec_id: str,
    *,
    estado: str = "running",
    worker_addr: str | None = None,
    detalles_worker: dict | None = None,
):
    """
    Actualiza ejecuciones/{exec_id} con la metadata del worker para que el front pueda
    rutar /analisis/stop directo al contenedor correcto.

    - estado: "running" | "stop_requested" | "completed" | "stopped" | "fallido"
    - worker_addr: "IP:PUERTO" del contenedor (si no se pasa, usa WORKER_ADDR)
    - detalles_worker: (opcional) info adicional (pid, versión, etc.)
    """
    addr = (worker_addr or MY_ADDR or "").strip()

    # Validación suave del target (no rompe si falla, solo evita guardar basura).
    if addr and not _ALLOW_TARGET.match(addr):
        # Si no pasa el patrón, lo descartamos para no quebrar el stop.
        addr = ""

    ts_unix = _now_unix()

    payload = {
        "worker_id": MY_ID,
        "task_key": exec_id,
        "estado": estado,
        "updated_at": ts_unix,
        "last_heartbeat": ts_unix,
    }

    if addr:
        payload["worker_addr"] = addr
        # Útil para debug/observabilidad desde la app o consola
        payload["stop_url"] = f"http://{addr}/analisis/stop"

    if detalles_worker and isinstance(detalles_worker, dict):
        payload["detalles_worker"] = detalles_worker

    # Si dispones de SERVER_TIMESTAMP, puedes añadirlo sin romper el resto
    if SERVER_TS is not None:
        payload["updated_at_server"] = SERVER_TS

    # merge=True para no pisar otros campos (resumen, urls, etc.)
    db.collection("ejecuciones").document(exec_id).set(payload, merge=True)


def fs_heartbeat(exec_id: str):
    ts = int(time.time())
    db.collection("ejecuciones").document(exec_id).set(
        {"last_heartbeat": ts, "updated_at": ts}, merge=True
    )
    # ⬇️ Mantén vivo el user_state (sirve para que el watchdog no lo resetee por error)
    try:
        snap = db.collection("ejecuciones").document(exec_id).get()
        d = snap.to_dict() or {}
        # la ejecución guarda ambos; usa el que haya
        key = d.get("user_id") or (f"tg_{d.get('chat_id')}" if d.get("chat_id") else None)
        if key:
            db.collection("user_states").document(key).set(
                {"updated_at_unix": ts}, merge=True
            )
    except Exception:
        pass

#@profile
def _sanitize_bars(bars):
    """Devuelve None o un entero > 0."""
    return bars if isinstance(bars, int) and bars > 0 else None

#@profile
def get_bars_for_tf(cfg: dict | None, tf: str) -> int | None:
    """
    Si cfg trae fmpWindows/fmp_windows y existe un número para 'tf', úsalo.
    Si no, devuelve None (o sea, SIN límite).
    """
    if not isinstance(cfg, dict) or not cfg:
        return None
    fmp_windows = cfg.get('fmpWindows') or cfg.get('fmp_windows')
    if isinstance(fmp_windows, dict):
        v = fmp_windows.get(tf)
        return _sanitize_bars(v)
    return None

print("🔍 GPU habilitada:", torch.cuda.is_available())

#@profile
def get_easyocr_reader(prefer_gpu: bool = True):
    """
    Inicializa EasyOCR una sola vez con cache de modelos, usa GPU si está disponible,
    y hace fallback a CPU si falla.
    """
    global _reader
    if _reader is not None:
        return _reader

    with _reader_lock:
        if _reader is not None:
            return _reader

        model_dir = os.environ.get('EASY_OCR_MODEL_DIR', '/app/models/easyocr')
        os.makedirs(model_dir, exist_ok=True)

        #@profile
        def _try_init(gpu_flag: bool):
            return easyocr.Reader(
                ['en'],
                gpu=gpu_flag,
                model_storage_directory=model_dir
            )

        # 1) Intentar con GPU si se prefiere y está disponible
        use_gpu = (prefer_gpu and torch.cuda.is_available())
        try:
            _reader = _try_init(use_gpu)
            return _reader
        except Exception as e:
            logger.error(f"EasyOCR con gpu={use_gpu} falló: {e}")
            # limpiar zips corruptos antes del fallback
            try:
                for f in os.listdir(model_dir):
                    if f.endswith('.zip'):
                        os.remove(os.path.join(model_dir, f))
            except Exception:
                pass

        # 2) Fallback a CPU
        _reader = _try_init(False)
        return _reader

# Inicializar EasyOCR (solo una vez, fuera de la función)
reader = get_easyocr_reader(prefer_gpu=True) 


#@profile
def _user_state_doc(user_id: str | None, chat_id: str | None):
    """
    Devuelve la referencia al doc en user_states.
    Prefiere user_id; si no hay, usa chat_id. Si no hay ninguno, None.
    """
    doc_id = (user_id or chat_id or "").strip()
    if not doc_id:
        return None
    return db.collection("user_states").document(doc_id)


#@profile
def _drop_nones(obj):
    if isinstance(obj, dict):
        return {k: _drop_nones(v) for k, v in obj.items() if v is not None}
    if isinstance(obj, (list, tuple)):
        return [_drop_nones(v) for v in obj if v is not None]
    return obj

#@profile
def _user_id_from_chat(chat_id: str | None) -> str | None:
    """Devuelve user_id desde chat_ids. Si chat_id está vacío, devuelve None y no toca Firestore."""
    if not chat_id:
        return None
    try:
        doc = db.collection("chat_ids").document(str(chat_id)).get()
        return (doc.to_dict() or {}).get("user_id") if doc.exists else None
    except Exception as e:
        logger.warning(f"_user_id_from_chat falló: {e}")
        return None


#@profile
def _subscription_doc(user_id: str):
    return db.collection("suscripciones_user").document(str(user_id))

# Mapeo de TF frontend -> backend
#@profile
def _tf_backend(tf: str) -> str:
    m = str(tf).strip().lower()
    mapping = {
        '1m':'1min', '1min':'1min',
        '5m':'5min', '5min':'5min',
        '15m':'15min', '15min':'15min',
        '30m':'30min', '30min':'30min',
        '1h':'1hour', 'h1':'1hour', '1hour':'1hour',
        '4h':'4hour', 'h4':'4hour', '4hour':'4hour',
        '1d':'1day', 'd1':'1day', '1day':'1day',
        '1w':'1week', 'w1':'1week', '1week':'1week',
    }
    return mapping.get(m, m)

# Si no las tienes, define estas sugerencias de combinaciones
if 'RECOMMENDED_TF_OPTIONS' not in globals():
    RECOMMENDED_TF_OPTIONS = {
        'swing': [
            ['1week','1day','4hour'],  # W1 → D1 → 4H
            ['1day','4hour','1hour'],  # D1 → 4H → 1H
            ['1week','1day','1hour'],  # Conservadora
        ],
        'intra': [
            ['4hour','15min','5min'],
            ['1hour','15min','5min'],
            ['4hour','15min','1min'],
            ['1hour','15min','1min'],
            ['4hour','30min','5min'],
            ['1hour','30min','5min'],
        ],
        'scalp': [
            ['15min','5min','1min'],
            ['30min','5min','1min'],
        ],
    }
REQUEST_OPERATORIA: dict[str, dict] = {}

#@profile
def clear_current_request_cfg(user_chat_id: str) -> None:
    REQUEST_OPERATORIA.pop(user_chat_id, None)

#@profile
def normalize_operatoria_payload(cfg: dict | None) -> dict:
    cfg = cfg or {}
    mode = (cfg.get('mode') or 'swing').strip().lower()
    tfs = cfg.get('tfs') or []
    tfs = [ _tf_backend(x) for x in tfs if x ]

    fmpWindows  = _norm_windows(cfg.get('fmpWindows'), DEFAULT_FMP_WINDOWS)
    calcWindows = _norm_windows(cfg.get('calcWindows'), DEFAULT_CALC_WINDOWS)

    return {
        'mode': mode,
        'tfs': tfs,                    # ya normalizados al backend
        'fmpWindows': fmpWindows,
        'calcWindows': calcWindows,
    }



#@profile
def _norm_tf(tf: str) -> str:
    tf = str(tf).lower().strip()
    return _TF_ALIAS.get(tf, tf)

#@profile
def _fmt_for_tf(tf: str) -> str:
    # FMP acepta fecha-hora para intradía; para 1d/1w basta YYYY-MM-DD
    return '%Y-%m-%d' if tf in ('1day', '1week') else '%Y-%m-%d %H:%M:%S'

#@profile
def _tf_to_backend(tf: str) -> str | None:
    return TF_MAP.get(str(tf).lower().strip())

#@profile
def _norm_windows(d: dict | None, defaults: dict[str,int]) -> dict[str,int]:
    out = dict(defaults)
    if isinstance(d, dict):
        for k, v in d.items():
            k2 = _tf_to_backend(k) or k  # admite ya-normalizadas
            try:
                vv = int(v)
                if vv > 0:
                    out[k2] = vv
            except Exception:
                pass
    return out

# Constantes de layout (ajústalas si quieres)
MAX_CELL_CHARS = 60
WRAP_WIDTH = 24
DPI = 150

COL_WIDTH_IN = 1.6

WRAP_WIDTH_HEADER = 18      # para cabeceras multilínea
MAX_FIG_W_IN    = 20.0    # clamp de ancho figura
MAX_FIG_H_IN    = 15.0    # clamp de alto figura
MIN_FIG_W_IN    = 6.0
MIN_FIG_H_IN    = 3.0
ROW_HEIGHT_IN   = 0.35    # alto por fila (aprox. pulgadas)
CHAR2IN         = 0.10    # “ancho” en pulgadas por carácter (heurística)

WRAP_WIDTH_HDR  = 18   # cabeceras largas
WRAP_WIDTH_CELL = 22   # contenido (evento, tipo operación)
MAX_W_INCH      = 18   # ancho máximo de la figura
MAX_H_INCH      = 24   # alto máximo de la figura
DPI_IMG         = 150
FILAS_POR_IMG_OPPS   = 40
FILAS_POR_IMG_EVENTO = 40
WRAP_HDR = 16
WRAP_CELL = 22

#@profile
def _fmt_pct(x):
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return ""
    try:
        return _fmt_num(float(x), nd=1)
    except Exception:
        return str(x)

#@profile
def _fmt_apal(x):
    """Acepta valores 'x2243.0', 2243, '2243', etc y devuelve '2243'."""
    if x is None or (isinstance(x, float) and np.isnan(x)): 
        return ""
    s = str(x).strip()
    if s.startswith("x"): 
        s = s[1:]
    # deja entero si aplica
    try:
        f = float(s)
        return _fmt_num(f, nd=0)
    except Exception:
        return s

# --- helpers de texto/medidas ---
#@profile
def _wrap_text(s, width: int) -> str:
    s = "" if s is None else str(s)
    if not s:
        return ""
    return "\n".join(wrap(s, width=width))

#@profile
def _fmt_num(x, nd=4):
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return ""
    try:
        return f"{float(x):.{nd}f}"
    except Exception:
        return str(x)

#@profile
def _wrap_text_multiline(s: str, width: int) -> str:
    """Envuelve texto a 'width' caracteres (sin cortar palabras), respetando saltos existentes."""
    if s is None:
        return ""
    s = str(s)
    lines = s.splitlines() or [s]
    wrapped_lines: list[str] = []
    for ln in lines:
        if not ln:
            wrapped_lines.append("")
            continue
        wrapped_lines.extend(
            wrap(
                ln,
                width=width,
                break_long_words=False,
                break_on_hyphens=False,
            )
        )
    return "\n".join(wrapped_lines)


#@profile
def _max_line_len(s: str) -> int:
    return max((len(x) for x in str(s).split("\n")), default=0)

#@profile
def _to_list_str_df(df: pd.DataFrame, wrap_cell=WRAP_WIDTH_CELL, wrap_head=WRAP_WIDTH_HEADER):
    headers = [_wrap_text(c, wrap_head) for c in df.columns]
    cells   = df.astype(str).applymap(lambda x: _wrap_text(x, wrap_cell))
    return headers, cells

#@profile
def _col_char_widths(headers: list[str], cells: pd.DataFrame,
                     min_chars=6, max_chars=40) -> list[int]:
    w = []
    for j, h in enumerate(headers):
        max_cell = max((_max_line_len(v) for v in cells.iloc[:, j].tolist()), default=0)
        width_j  = max(_max_line_len(h), max_cell) + 2
        w.append(min(max(width_j, min_chars), max_chars))
    return w

#@profile
def _render_table(headers, cells, col_char_w,
                  font=12, dpi=180, max_w_in=18, max_h_in=14):
    # Tamaños basados en caracteres para que respete los wraps
    CHAR_PX   = 7                 # ~px por carácter a font 12
    total_ch  = sum(col_char_w)
    width_in  = min((total_ch * CHAR_PX) / dpi + 1.2, max_w_in)

    rows      = len(cells) + 1
    row_px    = font * 2.2        # alto de fila generoso para multilínea
    height_in = min((rows * row_px) / dpi + 0.6, max_h_in)

    fig, ax = plt.subplots(figsize=(width_in, height_in), dpi=dpi)
    ax.axis('off')

    colWidths = [w / total_ch for w in col_char_w]
    tbl = ax.table(
        cellText=cells.values,
        colLabels=headers,
        colWidths=colWidths,
        cellLoc='center',
        loc='upper left',
        bbox=[0, 0, 1, 1]
    )
    tbl.auto_set_font_size(False)
    tbl.set_fontsize(font)

    # Aumentar altura de fila global para multilínea
    base = font * 1.2
    scale_y = max(1.0, row_px / base)
    tbl.scale(1.0, scale_y)

    buf = BytesIO()
    plt.savefig(buf, format='png', dpi=dpi, bbox_inches='tight')
    buf.seek(0)
    plt.close(fig)
    return buf

#@profile
def _wrap_header(h: str, width: int) -> str:
    return _wrap_text(h, width)


#@profile
def _es_serializable_basico(v: Any) -> bool:
    if v is None: return True
    if isinstance(v, (str, int, float, bool)): return True
    if isinstance(v, (list, tuple)):  # listas anidadas OK si sus elementos lo son
        return all(_es_serializable_basico(x) for x in v)
    if isinstance(v, dict):            # dicts anidados OK si claves son str y valores serializables
        return all(isinstance(k, str) and _es_serializable_basico(val) for k, val in v.items())
    if isinstance(v, (np.integer,)): return True
    if isinstance(v, (np.floating,)): return True
    if isinstance(v, (np.bool_,)):    return True
    if isinstance(v, (datetime, date, pd.Timestamp)): return True
    return False

#@profile
def _sanitize_for_firestore(obj: Any) -> Any:
    """Deja solo tipos permitidos (str, int, float, bool, None, list/dict de los mismos, datetime)."""
    if obj is None or isinstance(obj, (str, int, float, bool, datetime)):
        return obj
    if isinstance(obj, (np.integer,)):  return int(obj)
    if isinstance(obj, (np.floating,)): return float(obj)
    if isinstance(obj, (np.bool_,)):    return bool(obj)
    if isinstance(obj, (pd.Timestamp, date)): return datetime.fromisoformat(obj.isoformat())
    if isinstance(obj, list):  return [_sanitize_for_firestore(x) for x in obj]
    if isinstance(obj, tuple): return [_sanitize_for_firestore(x) for x in obj]
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if isinstance(k, str):
                out[k] = _sanitize_for_firestore(v)
        return out
    return None


#@profile
def _sanitize_records_for_json(records: list[dict]) -> list[dict]:
    """
    Elimina claves internas (_...) y valores no serializables (DataFrame, ndarray, etc).
    """
    safe: list[dict] = []
    for r in records or []:
        if not isinstance(r, dict):
            continue
        clean = {}
        for k, v in r.items():
            # descartar adjuntos internos y cualquier clave privada
            if str(k).startswith('_'):
                continue
            if _es_serializable_basico(v):
                clean[k] = v
        safe.append(clean)
    return safe


#@profile
def _to_epoch_ms(s: pd.Series) -> pd.Series:
    # A datetime (tz-aware en UTC)
    s = pd.to_datetime(s, errors='coerce', utc=True)

    # ns desde epoch sin usar .view()
    ns = s.astype('int64', copy=False)          # iNaT para NaT

    # a ms
    ms = ns // 10**6
    ms = pd.Series(ms, index=s.index)

    # Preservar NaT como NA (no como iNaT numérico)
    ms[s.isna()] = pd.NA

    # nullable Int64 (para JSON limpio)
    return ms.astype('Int64')


#@profile
def _json_sanitize_df(d: pd.DataFrame) -> pd.DataFrame:
    d = d.where(pd.notnull(d), None)
    for c in d.columns:
        if pd.api.types.is_bool_dtype(d[c]):   d[c] = d[c].astype(object)
        elif pd.api.types.is_integer_dtype(d[c]): d[c] = d[c].astype(object)
        elif pd.api.types.is_float_dtype(d[c]):   d[c] = d[c].astype(float)
    return d

#@profile
def sanitize_for_json(x):
    """
    Convierte NaN/±Inf a None, castea tipos numpy/pandas a tipos Python
    y sanea recursivamente colecciones. Evita usar pd.isna sobre arrays.
    """

    # --- Colecciones primero (para no pasar arrays a pd.isna) ---
    # ndarrays (incluye 0-D)
    if isinstance(x, np.ndarray):
        # 0-D array -> escalar Python
        if x.ndim == 0:
            return sanitize_for_json(x.item())
        # arrays 1-D+ -> lista
        return [sanitize_for_json(v) for v in x.tolist()]

    # listas/tuplas/conjuntos
    if isinstance(x, (list, tuple, set)):
        return [sanitize_for_json(v) for v in x]

    # diccionarios
    if isinstance(x, dict):
        return {str(k): sanitize_for_json(v) for k, v in x.items()}

    # DataFrame / Series
    if isinstance(x, pd.DataFrame):
        df = x.replace([np.inf, -np.inf], np.nan)
        df = df.where(pd.notnull(df), None)
        return [sanitize_for_json(rec) for rec in df.to_dict("records")]

    if isinstance(x, pd.Series):
        s = x.replace([np.inf, -np.inf], np.nan)
        s = s.where(pd.notnull(s), None)
        return sanitize_for_json(s.to_dict())

    # --- Escalares y tipos básicos ---
    # None
    if x is None:
        return None

    # booleanos
    if isinstance(x, (bool, np.bool_)):
        return bool(x)

    # enteros
    if isinstance(x, (int, np.integer)):
        return int(x)

    # floats
    if isinstance(x, (float, np.floating)):
        fx = float(x)
        if math.isnan(fx) or math.isinf(fx):
            return None
        return fx

    # fechas/horas
    if isinstance(x, (pd.Timestamp, np.datetime64, _dt.datetime, _dt.date)):
        try:
            return str(pd.Timestamp(x).isoformat())
        except Exception:
            return str(x)

    # --- Solo ahora: chequeo NA para escalares verdaderos ---
    try:
        # En este punto no llegan arrays/listas/dicts/Series/DF
        if pd.isna(x):          # NaN, NaT, pd.NA
            return None
    except Exception:
        pass

    # strings u otros tipos serializables
    return x
    
#@profile
def df_to_ohlcv_records_ext(
    df: pd.DataFrame,
    *,
    include: Iterable[str] | None = None,              # columnas extra a incluir
    rename_extras: Mapping[str, str] | None = None,    # renombres opcionales
    put_flags_inside: bool = True,                      # divergencias dentro de "flags"
) -> list[dict]:
    """
    Devuelve lista de velas con: t, date, o,h,l,c,v y, opcionalmente, indicadores/flags por vela.
    Acepta 'time' o 'date' o índice datetime.
    """
    if df is None or df.empty:
        return []

    d = df.copy()

    # ---- Normalizar tiempo
    if 'time' not in d.columns:
        if 'date' in d.columns:
            d = d.rename(columns={'date': 'time'})
        elif isinstance(d.index, pd.DatetimeIndex):
            d = d.reset_index().rename(columns={d.index.name or 'index': 'time'})
    d['time'] = pd.to_datetime(d['time'], errors='coerce', utc=True)
    d = d.dropna(subset=['time'])

    # ---- Campos core
    core_map = {'open':'o', 'high':'h', 'low':'l', 'close':'c', 'volume':'v'}
    for col in core_map:
        if col in d.columns:
            d[col] = pd.to_numeric(d[col], errors='coerce')

    d['t'] = _to_epoch_ms(d['time']).astype('Int64')
    d['date'] = d['time'].dt.strftime('%Y-%m-%dT%H:%M:%S.%fZ')

    # salida base
    out = pd.DataFrame({
        't': d['t'].astype(object),
        'date': d['date'],
        'o': d.get('open'),
        'h': d.get('high'),
        'l': d.get('low'),
        'c': d.get('close'),
        'v': d.get('volume')
    })

    # ---- Extras
    default_extras = [
        'rsi','SMA','ema_12','ema_26',
        'bollinger_upper','bollinger_lower',
        'macd','signal','%K','%D','ATR'
    ]
    divergence_cols = [
        'divergencia_macd','divergencia_rsi',
        'divergencia_macd_bull','divergencia_macd_bear',
        'divergencia_rsi_bull','divergencia_rsi_bear',
    ]

    extras = list(include) if include is not None else default_extras + divergence_cols
    extras = [c for c in extras if c in d.columns]

    # histogram si hay macd/signal
    if 'macd' in d.columns and 'signal' in d.columns and ('hist' in (include or []) or include is None):
        d['hist'] = (pd.to_numeric(d['macd'], errors='coerce') -
                     pd.to_numeric(d['signal'], errors='coerce'))
        extras.append('hist')

    # dividir extras en numéricos y flags
    rename_extras = dict(rename_extras or {})
    num_extras = [c for c in extras if c not in divergence_cols]
    flag_extras = [c for c in extras if c in divergence_cols]

    # numéricos boolean/numeric → JSON
    for c in num_extras:
        out[ rename_extras.get(c, c) ] = pd.to_numeric(d[c], errors='coerce')

    # flags (optimizado: sin iterrows)
    if flag_extras:
        if put_flags_inside:
            flags = d[flag_extras].copy()
            for c in flags.columns:
                flags[c] = flags[c].astype('boolean')

            flags = flags.astype(object).where(~flags.isna(), None)
            flags = flags.rename(columns={k: rename_extras.get(k, k) for k in flags.columns})
            out['flags'] = flags.to_dict('records')
        else:
            for c in flag_extras:
                out[rename_extras.get(c, c)] = d[c].astype('boolean').astype(object)

    out = _json_sanitize_df(out)
    return out.to_dict('records')

def construir_payload_enriquecido(
    symbol: str,
    tf: str,
    d: pd.DataFrame,
    *,
    extra_meta: dict | None = None,
    niveles: dict | None = None,
    entradas: dict | None = None,
    # controla qué extras metes dentro de cada vela; None = todos los que existan
    include_extras_en_candles: Iterable[str] | None = None,
    # si quieres un JSON mínimo (solo OHLCV), ponlo en False
    candles_with_extras: bool = True,
) -> dict:
    """
    JSON apto para el front con 'series' **solo** -> {'candles': [...]}
    - Las demás series (sma/rsi/macd/...) NO se generan.
    - Si candles_with_extras=True, los indicadores/flags van embebidos por vela.
    """
    if d is None or d.empty:
        return {
            "symbol": str(symbol).upper(),
            "timeframe": str(tf),
            "count": 0,
            "series": {"candles": []},
            "events": [],
            "last": {},
            "meta": {"computed_at": pd.Timestamp.now('UTC').isoformat(), **(extra_meta or {})},
            "levels": niveles or {},
            "entradas": entradas or {},
        }

    # --- Candles ---
    if candles_with_extras:
        candles = df_to_ohlcv_records_ext(
            d,
            include=include_extras_en_candles,  # None = extras por defecto + flags
            rename_extras={
                'SMA':'sma','ema_12':'ema12','ema_26':'ema26',
                'bollinger_upper':'bb_upper','bollinger_lower':'bb_lower',
                '%K':'stoch_k','%D':'stoch_d'
            },
            put_flags_inside=True
        )
    else:
        # solo OHLCV + t/date
        candles = df_to_ohlcv_records_ext(
            d,
            include=[],                 # <- sin extras
            rename_extras={}, 
            put_flags_inside=False
        )

    series = {"candles": candles}      # <-- AQUÍ queda solo candles

    # --- Eventos (igual que antes) ---
    events = []
    if 'macd' in d.columns and 'signal' in d.columns:
        t_ms = _to_epoch_ms(d['time']).astype('Int64')
        diff = (pd.to_numeric(d['macd'], errors='coerce') -
                pd.to_numeric(d['signal'], errors='coerce'))
        macd_crosses = []
        for i in range(1, len(d)):
            prev, curr = diff.iat[i-1], diff.iat[i]
            if pd.isna(prev) or pd.isna(curr): 
                continue
            if prev < 0 <= curr:
                macd_crosses.append({"t": int(t_ms.iat[i]), "type": "macd_bullish_cross"})
            elif prev > 0 >= curr:
                macd_crosses.append({"t": int(t_ms.iat[i]), "type": "macd_bearish_cross"})
        if macd_crosses:
            events.append({"kind": "macd_cross", "points": macd_crosses})

    if {'bollinger_upper','bollinger_lower','close'}.issubset(d.columns):
        t_ms = _to_epoch_ms(d['time']).astype('Int64')
        bb_points = []
        for i in range(len(d)):
            cu, cl, cc = d['bollinger_upper'].iat[i], d['bollinger_lower'].iat[i], d['close'].iat[i]
            if pd.isna(cu) or pd.isna(cl) or pd.isna(cc): 
                continue
            if cc > cu: bb_points.append({"t": int(t_ms.iat[i]), "type": "bb_break_up"})
            elif cc < cl: bb_points.append({"t": int(t_ms.iat[i]), "type": "bb_break_down"})
        if bb_points:
            events.append({"kind": "bollinger_touch", "points": bb_points})

    for col in ['divergencia_macd','divergencia_rsi',
                'divergencia_macd_bull','divergencia_macd_bear',
                'divergencia_rsi_bull','divergencia_rsi_bear']:
        if col in d.columns:
            t_ms = _to_epoch_ms(d['time']).astype('Int64')
            pts = [{"t": int(t_ms.iat[i])} for i, v in enumerate(d[col]) if pd.notna(v) and bool(v)]
            if pts:
                events.append({"kind": col, "points": pts})

    # --- Snapshot (igual que antes) ---
    last = {}
    if not d.empty:
        i = len(d) - 1
        last = {
            "t": int(_to_epoch_ms(pd.Series([d['time'].iat[i]])).iat[0]),
            "o": float(d['open'].iat[i]), "h": float(d['high'].iat[i]),
            "l": float(d['low'].iat[i]),  "c": float(d['close'].iat[i]),
            "v": (None if 'volume' not in d.columns or pd.isna(d['volume'].iat[i]) 
                  else float(d['volume'].iat[i])),
        }
        # últimos indicadores si existen (opcionales)
        for col, key in [('rsi','rsi'),('SMA','sma'),('ema_12','ema12'),('ema_26','ema26'),
                         ('bollinger_upper','bb_upper'),('bollinger_lower','bb_lower'),
                         ('ATR','atr'),('%K','stochK'),('%D','stochD'),
                         ('macd','macd'),('signal','signal')]:
            if col in d.columns and pd.notna(d[col].iat[i]):
                last[key] = float(d[col].iat[i])
        if 'macd' in d.columns and 'signal' in d.columns and \
           pd.notna(d['macd'].iat[i]) and pd.notna(d['signal'].iat[i]):
            last['hist'] = float(d['macd'].iat[i] - d['signal'].iat[i])
        for col in ['divergencia_macd','divergencia_rsi',
                    'divergencia_macd_bull','divergencia_macd_bear',
                    'divergencia_rsi_bull','divergencia_rsi_bear']:
            if col in d.columns and pd.notna(d[col].iat[i]):
                last[col] = bool(d[col].iat[i])

    return {
        "symbol": str(symbol).upper(),
        "timeframe": str(tf),
        "count": int(len(d)),
        "series": series,        # <-- solo candles
        "events": events,
        "last": last,
        "meta": {"computed_at": pd.Timestamp.now('UTC').isoformat(), **(extra_meta or {})},
        "levels": niveles or {},
        "entradas": entradas or {}
    }

#@profile
def _num_or_none(x):
    """Convierte números a tipos nativos y reemplaza NaN/Inf por None."""
    if x is None:
        return None
    # bool primero (np.bool_ es subtipo de np.integer)
    if isinstance(x, (bool, np.bool_)):
        return bool(x)
    # enteros
    if isinstance(x, (int, np.integer)):
        return int(x)
    # flotantes
    if isinstance(x, (float, np.floating)):
        xf = float(x)
        return xf if (xf == xf and np.isfinite(xf)) else None  # (xf==xf) filtra NaN
    return x

#@profile
def json_safe(obj):
    """
    Limpia recursivamente:
      - np.nan/np.inf → None
      - numpy.* → tipos Python nativos
      - pd.Timestamp → ISO 8601
      - np.ndarray → list
    Devuelve algo 100% serializable a JSON estándar.
    """
    if obj is None:
        return None
    if isinstance(obj, (pd.Timestamp, )):
        return obj.isoformat()
    if isinstance(obj, (np.ndarray, )):
        return json_safe(obj.tolist())
    if isinstance(obj, (list, tuple, set)):
        return [json_safe(v) for v in obj]
    if isinstance(obj, dict):
        return {str(k): json_safe(v) for k, v in obj.items()}
    if isinstance(obj, pd.Series):
        return json_safe(obj.to_dict())
    # números / booleanos
    if isinstance(obj, (bool, np.bool_, int, np.integer, float, np.floating)):
        return _num_or_none(obj)
    # Como última opción, intenta ver si ya es serializable
    try:
        json.dumps(obj, allow_nan=False)
        return obj
    except Exception:
        return str(obj)

#@profile
def _json_safe_numeric(s: pd.Series) -> pd.Series:
    """Convierte a numérico, limpia ±inf/NaN y devuelve None (tipo object) donde falte valor."""
    s = pd.to_numeric(s, errors='coerce')
    s = s.replace([np.inf, -np.inf], np.nan)
    return s.where(s.notna(), None).astype(object)

#@profile
def normalizar_df_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()

    d = df.copy()

    # Si viene por índice -> columna time
    if isinstance(d.index, pd.DatetimeIndex):
        d = d.reset_index().rename(columns={d.index.name or 'index': 'time'})

    # Mapeo de nombres
    colmap_posibles = {
        'time':   ['time', 'timestamp', 'date', 'datetime'],
        'open':   ['open', 'o', 'apertura'],
        'high':   ['high', 'h', 'max', 'maximo'],
        'low':    ['low', 'l', 'min', 'minimo'],
        'close':  ['close', 'c', 'cierre', 'close_price'],
        'volume': ['volume', 'v', 'vol', 'volumen'],
        'SMA': ['SMA', 'sma'],
        'bollinger_upper': ['bollinger_upper', 'bb_upper', 'upper'],
        'bollinger_lower': ['bollinger_lower', 'bb_lower', 'lower'],
        'bollinger_signal': ['bollinger_signal'],
        'ema_12': ['ema_12', 'ema12'],
        'ema_26': ['ema_26', 'ema26'],
        'macd': ['macd'],
        'signal': ['signal', 'macd_signal'],
        '%K': ['%K', 'stoch_k', 'k'],
        '%D': ['%D', 'stoch_d', 'd'],
        'ATR': ['ATR', 'atr'],
        'rsi': ['rsi', 'RSI'],
        'divergencia_macd': ['divergencia_macd'],
        'divergencia_rsi':  ['divergencia_rsi'],
        'divergencia_macd_bull': ['divergencia_macd_bull'],
        'divergencia_macd_bear': ['divergencia_macd_bear'],
        'divergencia_rsi_bull':  ['divergencia_rsi_bull'],
        'divergencia_rsi_bear':  ['divergencia_rsi_bear'],
    }

    m = {}
    for tgt, cands in colmap_posibles.items():
        for c in cands:
            if c in d.columns:
                m[c] = tgt
                break
    d = d.rename(columns=m)

    # Requisitos OHLC
    for c in ['time', 'open', 'high', 'low', 'close']:
        if c not in d.columns:
            return pd.DataFrame()

    # Tiempos a UTC ISO
    d['time'] = pd.to_datetime(d['time'], errors='coerce', utc=True)
    d = d.dropna(subset=['time', 'open', 'high', 'low', 'close'])

    # Columnas numéricas que deben ser JSON-safe (null en lugar de NaN)
    numeric_cols = [
        'open','high','low','close','volume','SMA','ema_12','ema_26',
        'macd','signal','%K','%D','ATR','rsi','bollinger_upper','bollinger_lower'
    ]
    for c in numeric_cols:
        if c in d.columns:
            d[c] = _json_safe_numeric(d[c])

    # Booleans como bool/None (no <NA>)
    for c in [
        'divergencia_macd','divergencia_rsi',
        'divergencia_macd_bull','divergencia_macd_bear',
        'divergencia_rsi_bull','divergencia_rsi_bear'
    ]:
        if c in d.columns:
            s = d[c]
            # si ya es boolean nullable, conviértelo a bool/None
            if str(s.dtype) == 'boolean':
                d[c] = s.astype(object).where(s.notna(), None)
            else:
                d[c] = s.astype(object)

    d = d.sort_values('time').reset_index(drop=True)

    MAX_VELAS = 1500
    if len(d) > MAX_VELAS:
        d = d.tail(MAX_VELAS).reset_index(drop=True)

    return d

#@profile
async def subir_ohlcv_enriquecido_y_registrar(
    *, exec_id: str, chat_id: str, user_id: str, symbol: str, temporalidad: str,
    df_velas: pd.DataFrame, df_indicadores: pd.DataFrame | None,
    subir_a_bucket_y_obtener_url,
    niveles: dict | None = None, entradas: dict | None = None,
    extra_metadata: dict | None = None,
) -> str | None:

    d_norm = normalizar_df_ohlcv(df_velas)
    payload = construir_payload_enriquecido(
        symbol, temporalidad, d_norm,
        extra_meta=extra_metadata or {},
        niveles=niveles, entradas=entradas,
        include_extras_en_candles=None,   # None = incluye extras si existen
        candles_with_extras=True          # o False si quieres OHLCV puro
    )
    return await guardar_json_en_storage_y_registrar(
        exec_id=exec_id, chat_id=chat_id, user_id=user_id,
        nombre_base=f"{symbol}_{temporalidad}_enriched",
        data=payload,
        subir_a_bucket_y_obtener_url=subir_a_bucket_y_obtener_url,
        metadata=extra_metadata or {},
    )


#@profile
def build_object_path(exec_id: str, nombre: str) -> str:
    # Estructura uniforme en el bucket por ejecución
    return f"exec/{exec_id}/{nombre}"


#@profile
def fs_crear_ejecucion(
    *, user_id: str | None, chat_id: str | None,
    activos_solicitados: list[str], origen: str, opciones_usuario: list[str]
) -> str:
    exec_id = uuid.uuid4().hex
    payload = _drop_nones({
        "exec_id": exec_id,
        "user_id": user_id,          # <— NUEVO (principal)
        "chat_id": chat_id,          # <— opcional (solo si viene)
        "activos_solicitados": list(map(str, activos_solicitados or [])),
        "origen": str(origen),
        "opciones_usuario": list(map(str, opciones_usuario or [])),
        "estado": "en_proceso",
        "archivos": 0,
        "created_at": firestore.SERVER_TIMESTAMP,
        "updated_at": firestore.SERVER_TIMESTAMP,
    })
    db.collection("ejecuciones").document(exec_id).set(payload)
    return exec_id

#@profile
def fs_actualizar_ejecucion(exec_id: str, **campos):
    campos = _sanitize_for_firestore({k: v for k, v in campos.items() if v is not None})
    if not isinstance(campos, dict):
        campos = {}
    campos["updated_at"] = firestore.SERVER_TIMESTAMP
    db.collection("ejecuciones").document(exec_id).update(campos)

#@profile
def fs_finalizar_ejecucion(exec_id: str, estado: str = "completado", resumen: dict | None = None):
    db.collection("ejecuciones").document(exec_id).update({
        "estado": str(estado),
        "resumen": _sanitize_for_firestore(resumen or {}),
        "updated_at": firestore.SERVER_TIMESTAMP
    })

#@profile
def fs_registrar_archivo_generado(
    exec_id: str,
    *, user_id: str | None, chat_id: str | None,
    tipo: str, nombre: str, gcs_path: str,
    signed_url: str | None = None, content_type: str | None = None,
    metadata: dict | None = None,
):
    payload = _drop_nones({
        "exec_id": str(exec_id),
        "user_id": user_id,          
        "chat_id": chat_id,          
        "tipo": str(tipo),
        "nombre": str(nombre),
        "gcs_path": str(gcs_path),
        "signed_url": signed_url,
        "content_type": content_type,
        "metadata": metadata or {},
        "created_at": firestore.SERVER_TIMESTAMP,
    })
    db.collection("archivos_generados").add(payload)
    db.collection("ejecuciones").document(exec_id).update({
        "archivos": firestore.Increment(1),
        "updated_at": firestore.SERVER_TIMESTAMP
    })

#@profile
async def guardar_json_en_storage_y_registrar(
    *, exec_id: str, chat_id: str, user_id:str, nombre_base: str,
    data, subir_a_bucket_y_obtener_url, metadata: dict | None = None,
) -> str | None:
    nombre = f"{nombre_base}.json"
    object_path = build_object_path(exec_id, nombre)
    local_json = f"/tmp/{nombre}"

    # Normaliza si llega DataFrame
    if isinstance(data, pd.DataFrame):
        data = data.replace([np.inf, -np.inf], np.nan).where(pd.notnull(data), None).to_dict("records")

    # 🔒 Sanitiza SIEMPRE (recursivo)
    data = sanitize_for_json(data)

    # Si quedara algo, esto lanzará ValueError -> lo verás en logs
    with open(local_json, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, allow_nan=False)

    url_publica = await subir_a_bucket_y_obtener_url(local_json, object_path)
    if not isinstance(url_publica, str):
        raise RuntimeError(f"subir_a_bucket_y_obtener_url devolvió {type(url_publica)}")

    await asyncio.to_thread(
        fs_registrar_archivo_generado,
        exec_id=exec_id, 
        user_id=user_id, 
        chat_id=chat_id, 
        tipo="json", 
        nombre=nombre, 
        gcs_path=object_path,
        signed_url=url_publica, 
        content_type="application/json",
        metadata=metadata or {},
    )
    return url_publica


#@profile
def es_grafico_de_velas(ruta_imagen: str) -> bool:
    """
    Intenta detectar si una imagen tiene características de un gráfico de velas:
    - muchas líneas verticales y horizontales (bordes)
    - alto contraste en regiones pequeñas (cuerpos de velas)
    """

    imagen = cv2.imread(ruta_imagen, cv2.IMREAD_GRAYSCALE)
    if imagen is None:
        return False

    # Redimensionar proporcionalmente si es pequeña
    altura, ancho = imagen.shape[:2]
    if altura < 300 or ancho < 300:
        escala = max(300 / ancho, 300 / altura)
        nueva_dim = (int(ancho * escala), int(altura * escala))
        imagen = cv2.resize(imagen, nueva_dim)


    # Detectar bordes
    bordes = cv2.Canny(imagen, 50, 150)

    # Detectar líneas (Hough transform)
    lineas = cv2.HoughLinesP(bordes, 1, np.pi / 180, threshold=80, minLineLength=30, maxLineGap=5)

    if lineas is None:
        return False

    # Contar líneas verticales/horizontales
    verticales = 0
    horizontales = 0
    for x1, y1, x2, y2 in lineas[:, 0]:
        angulo = np.arctan2(y2 - y1, x2 - x1) * 180 / np.pi
        if abs(angulo) < 10:
            horizontales += 1
        elif abs(angulo - 90) < 10 or abs(angulo + 90) < 10:
            verticales += 1

    total = len(lineas)
    if verticales + horizontales > total * 0.4:
        return True

    return False

#@profile
def analizar_con_yolo(ruta_imagen: str) -> tuple[str, str]:
    nombre_archivo = os.path.basename(ruta_imagen)
    imagen_limpia_path = f"procesadas/limpia_{nombre_archivo}"
    imagen_final_path = f"procesadas/patrones_{nombre_archivo}"

    # Leer imagen original
    imagen = cv2.imread(ruta_imagen)

    # --- PASO 1: Detección de ruido visual ---
    resultados_ruido = modelo_ruido.predict(ruta_imagen, save=False, conf=0.4)

    for box in resultados_ruido[0].boxes:
        x1, y1, x2, y2 = map(int, box.xyxy[0])
        cv2.rectangle(imagen, (x1, y1), (x2, y2), (255, 255, 255), thickness=-1)

    # --- PASO 2: OCR (detección de texto adicional) ---
    resultados_ocr = reader.readtext(ruta_imagen)
    for (bbox, texto, conf) in resultados_ocr:
        if conf < 0.4:
            continue
        pts = np.array(bbox).astype(np.int32)
        cv2.fillPoly(imagen, [pts], (255, 255, 255))  # Borrar con blanco

    # Guardar imagen limpia
    cv2.imwrite(imagen_limpia_path, imagen)

    # --- PASO 3: Detección de patrones sobre imagen limpia ---
    resultados_patrones = modelo_patrones.predict(imagen_limpia_path, save=False, conf=0.4)

    # Guardar imagen con patrones detectados
    cv2.imwrite(imagen_final_path, resultados_patrones[0].plot())

    # --- PASO 4: Formatear texto descriptivo ---
    detalles = []
    for box in resultados_patrones[0].boxes:
        clase_id = int(box.cls[0])
        conf = float(box.conf[0])
        nombre = modelo_patrones.names[clase_id]
        detalles.append(f"{nombre} ({conf:.2f})")

    texto_resultado = "🔎 Patrones detectados:\n" + "\n".join(detalles) if detalles else "❌ No se detectaron patrones."

    return imagen_final_path, texto_resultado


#@profile
async def subir_a_bucket_y_obtener_url(nombre_local, nombre_remoto=None, carpeta='analisis'):
    nombre_remoto = nombre_remoto or os.path.basename(nombre_local)
    bucket_name = "markettool_bucket"  # 🔁 Reemplazar con el nombre real de tu bucket

    client = storage.Client()
    bucket = client.bucket(BUCKET_NAME)
    blob = bucket.blob(f"{carpeta}/{nombre_remoto}")
    blob.upload_from_filename(nombre_local)
    blob.make_public()  # O usar signed_url si prefieres enlaces temporales

    return blob.public_url

#@profile
def obtener_datos_firestore():
    """
    Obtiene los datos de Firestore y los devuelve como listas de Python.
    """
    print("Obteniendo datos de Firestore...")

    try:
        # Leer datos desde Firestore
        activos_ref = db.collection("config").document("activos").get()
        forex_ref = db.collection("config").document("forex").get()
        relacionados_usd_ref = db.collection("config").document("relacionados_usd").get()

        if activos_ref.exists and forex_ref.exists and relacionados_usd_ref.exists:
            activos = activos_ref.to_dict().get("data", [])
            forex = forex_ref.to_dict().get("data", [])
            relacionados_usd = relacionados_usd_ref.to_dict().get("data", [])

            print("Datos obtenidos exitosamente.")
            return activos, forex, relacionados_usd
        else:
            print("No se encontraron datos en Firestore.")
            return [], [], []

    except Exception as e:
        print(f"Error obteniendo datos de Firestore: {e}")
        return [], [], []

# Llamar a la función al inicio de la aplicación
activos, forex, relacionados_usd = obtener_datos_firestore()


#@profile
def obtener_configuracion():
    """
    Obtiene los datos de Firestore para las categorías, temporalidades y zonas horarias.
    """
    print("Obteniendo configuración desde Firestore...")

    try:
        categorias_ref = db.collection("config").document("categorias").get()
        temporalidades_ref = db.collection("config").document("temporalidades").get()
        zonas_horarias_ref = db.collection("config").document("zonas_horarias").get()

        if categorias_ref.exists and temporalidades_ref.exists and zonas_horarias_ref.exists:
            categorias = categorias_ref.to_dict().get("data", {})
            temporalidades = temporalidades_ref.to_dict().get("data", [])
            zonas_horarias = zonas_horarias_ref.to_dict().get("data", [])

            print("Configuración obtenida exitosamente.")
            return categorias, temporalidades, zonas_horarias
        else:
            print("No se encontraron datos en Firestore.")
            return {}, [], []

    except Exception as e:
        print(f"Error obteniendo configuración desde Firestore: {e}")
        return {}, [], []
    
# Llamar a la función al inicio de la aplicación
categorias, temporalidades, zonas_horarias = obtener_configuracion()

#@profile
def definir_window(temporalidad: str, overrides: dict[str,int] | None = None) -> int:

    if overrides and temporalidad in overrides:
        try:
            v = int(overrides[temporalidad])
            if v > 0:
                return v
        except Exception:
            pass

    # Definir el window según la temporalidad
    if temporalidad == '1min':
        window = 14   # Antes 50
    elif temporalidad == '5min':
        window = 20   # Antes 50
    elif temporalidad == '15min':
        window = 20  # Antes 50
    elif temporalidad == '30min':
        window = 25  # Antes 50
    elif temporalidad == '1hour':
        window = 50  # Antes 100
    elif temporalidad == '4hour':
        window = 100  # Antes 150
    elif temporalidad == '1day':
        window = 200  # Antes 100
    elif temporalidad == '1week':
        window = 52   # Antes 52
    else:
        window = 30  # Antes 30 Valor por defecto para otras temporalidades
    return window


#@profile
def _user_state_doc_by_uuid(uuid: str):
    return db.collection("user_states").document(uuid)


# ------------------------------------------------------------------------------------
# UUID RESOLVER  (APP: user_id → uuid;  TELEGRAM: chat_id → tg_<chat_id> si no hay user_id)
# ------------------------------------------------------------------------------------
#@profile
def resolve_user_uuid(*, user_id: Optional[str] = None, chat_id: Optional[str] = None) -> Optional[str]:
    """
    - Si hay user_id (APP), lo usamos como UUID.
    - Si no hay user_id pero sí chat_id (Telegram), intentamos mapearlo a user_id.
      Si ese mapeo falla/no existe, usamos 'tg_<chat_id>' como UUID estable.
    """
    if user_id:
        return str(user_id)

    if chat_id:
        # Intentar mapear chat_id → user_id si tu proyecto lo soporta
        try:
            uid = _user_id_from_chat(str(chat_id))
            if uid:
                return str(uid)
        except Exception:
            pass
        # Fallback: doc separado por Telegram
        return f"tg_{chat_id}"

    return None


# ------------------------------------------------------------------------------------
# MARK USER STATE  (ESCRIBE SIEMPRE; sincroniza memoria y Firestore)
# ------------------------------------------------------------------------------------
#@profile
def mark_user_state(
    *, user_id: Optional[str] = None, chat_id: Optional[str] = None,
    estado: str = "disponible", extra: Optional[Dict[str, Any]] = None
) -> None:
    """
    Actualiza user_states/{UUID}. Soporta ambas entradas por compatibilidad.
    - APP:  mark_user_state(user_id=..., estado="...")
    - TG:   mark_user_state(chat_id=..., estado="...")
    """
    uuid = resolve_user_uuid(user_id=user_id, chat_id=chat_id)
    if not uuid:
        print(f"[mark_user_state] No se pudo resolver UUID (user_id={user_id}, chat_id={chat_id})")
        return

    payload: Dict[str, Any] = {
        "estado": estado,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    if user_id is not None:
        payload["user_id"] = str(user_id)
    if chat_id is not None:
        payload["chat_id"] = str(chat_id)
    if extra:
        payload.update(extra)

    # Firestore
    try:
        _user_state_doc_by_uuid(uuid).set(payload, merge=True)
    except Exception as e:
        logging.warning(f"[mark_user_state] Firestore fallo (uuid={uuid}): {e}")

    # Memoria (clave principal = uuid)
    st = user_states.setdefault(uuid, {})
    st["estado"] = estado
    # copia campos útiles si vinieron en extra
    for k in ("par_seleccionado", "soportes_resistencias_cache", "cache_realtime", "moneda_filtro", "exec_id"):
        if k in (extra or {}):
            st[k] = (extra or {})[k]
    user_states[uuid] = st

    # Espejos en memoria para compatibilidad con código existente que indexa por chat_id o user_id
    if chat_id is not None:
        st2 = user_states.setdefault(str(chat_id), {})
        st2["estado"] = estado
        user_states[str(chat_id)] = st2
    if user_id is not None:
        st3 = user_states.setdefault(str(user_id), {})
        st3["estado"] = estado
        user_states[str(user_id)] = st3


# ------------------------------------------------------------------------------------
# RETURN STATE  (LEE Firestore; fallback a memoria; compatible con docs antiguos)
# ------------------------------------------------------------------------------------
#@profile
def return_state(
    *, user_id: Optional[str] = None, chat_id: Optional[str] = None, default: str = "disponible"
) -> str:
    """
    Lee el estado desde Firestore (user_states/{uuid}). Si falla, cae a memoria.
    - Si vienes de Telegram y antes guardabas el doc solo con chat_id "crudo",
      se intenta un segundo doc-id de compatibilidad (sin el prefijo tg_).
    """
    uuid = resolve_user_uuid(user_id=user_id, chat_id=chat_id)
    if not uuid:
        return default

    # 1) Firestore por UUID (nuevo esquema)
    try:
        snap = _user_state_doc_by_uuid(uuid).get()
        if getattr(snap, "exists", False):
            data = snap.to_dict() or {}
            return str(data.get("estado") or default)
    except Exception as e:
        logging.warning(f"[return_state] Firestore fallo (uuid={uuid}): {e}")

    # 1.b) Compatibilidad: si uuid es tg_<chat_id>, intenta doc con chat_id "crudo"
    try:
        if (chat_id is not None) and str(uuid).startswith("tg_"):
            snap2 = _user_state_doc_by_uuid(str(chat_id)).get()
            if getattr(snap2, "exists", False):
                data2 = snap2.to_dict() or {}
                return str(data2.get("estado") or default)
    except Exception:
        pass

    # 2) Memoria por UUID
    if uuid in user_states and "estado" in user_states[uuid]:
        return str(user_states[uuid]["estado"])

    # 3) Memoria por claves “crudas” (compat)
    if chat_id is not None and str(chat_id) in user_states and "estado" in user_states[str(chat_id)]:
        return str(user_states[str(chat_id)]["estado"])
    if user_id is not None and str(user_id) in user_states and "estado" in user_states[str(user_id)]:
        return str(user_states[str(user_id)]["estado"])

    return default



#@profile
async def actualizar_menus(application:Application):
    # Obtén la lista de usuarios (aquí debes implementar tu lógica para obtener los usuarios registrados)
    start_time = time.time()
    usuarios= await cargar_chat_ids()
    logger.info(f"✅ Usuarios cargados en {time.time() - start_time:.2f} segundos.")
    tareas = []

    for user_chat_id in usuarios:
        start_user = time.time()
        if not es_administrador(user_chat_id):
            logger.info("Es usuario registrado se procede a actualizar el menú")
            tareas.append(menu_usuario_registrado(application.bot, user_chat_id))
        elif es_administrador(user_chat_id):
            logger.info("Es administrador se procede a actualizar el menú")
            tareas.append(menu_usuario_administrador(application, user_chat_id))
    # Ejecutar todas las tareas en paralelo
    await asyncio.gather(*tareas)
    logger.info(f"✅ Actualización de menús finalizada en {time.time() - start_time:.2f} segundos.")


# Función para asignar la categoría en base al campo 'event'
#@profile
async def seleccionar_zona_horaria(update, context):
    query = update.callback_query
    zona_seleccionada = query.data.split("_", 2)[2]

    user_chat_id = str(query.message.chat_id)
    await actualizar_timezone(user_chat_id, zona_seleccionada)

     # Actualizar la variable global para el usuario actual
    global timezone_country
    timezone_country = pytz.timezone(zona_seleccionada)

    now = datetime.now(pytz.timezone(zona_seleccionada)).strftime('%Y-%m-%d %H:%M:%S')
    await query.answer()
    await query.edit_message_text(
        f"Zona horaria actualizada a {zona_seleccionada}. La hora local es {now}."
    )

#@profile
async def cargar_timezone_por_defecto(chat_id):
    """Carga la zona horaria predeterminada para un chat_id."""
    chat_ids = await cargar_chat_ids()
    return chat_ids.get(chat_id, {}).get("timezone", "UTC")

#@profile
def detectar_categoria(event):
    for palabra_clave, categoria in palabras_clave_categoria.items():
        if palabra_clave.lower() in event.lower():
            return categoria
        return None  # Si no se encuentra una categoría, devuelve None

# Cargar la lista de chat_ids desde el archivo
#@profile
async def cargar_admin_ids():
    """Carga los chat_ids desde Firestore o devuelve una lista vacía si no hay datos."""
    try:
        # Obtén la referencia a la colección "admin_ids"
        collection_ref = db.collection("admin_ids")
        
        # Consulta todos los documentos de la colección
        docs = collection_ref.stream()

        # Extraer los chat_ids desde los documentos
        admin_ids = [doc.to_dict().get("chat_id") for doc in docs if doc.exists]
        
        # Devuelve la lista de chat_ids
        return admin_ids
    except Exception as e:
        print(f"Error al cargar admin_ids desde Firestore: {e}")
        return [] 

# Cargar la lista de chat_ids desde el archivo
#@profile
async def cargar_chat_ids():
    """Carga los chat_ids desde Firestore o devuelve un diccionario vacío si no hay datos."""
    try:
        # Obtén la referencia a la colección "chat_ids"
        collection_ref = db.collection("chat_ids")
        
        # Consulta todos los documentos de la colección
        docs = collection_ref.stream()

        # Cargar los datos en un diccionario
        chat_ids = {
            doc.id: doc.to_dict()
            for doc in docs if doc.exists
        }
        
        return chat_ids
    except Exception as e:
        print(f"Error al cargar chat_ids desde Firestore: {e}")
        return {}  # Devuelve un diccionario vacío en caso de error

# Guardar la lista de chat_ids en el archivo
#@profile
async def guardar_chat_id(user_chat_id, username=None, timezone="America/Santiago"):
    """Guarda o actualiza un chat_id con información adicional en Firestore."""
    try:
        # Referencia al documento del usuario
        doc_ref = db.collection("chat_ids").document(str(user_chat_id))

        # Actualiza solo los campos necesarios
        update_data = {}
        if username:
            update_data["username"] = username
        if timezone:
            update_data["timezone"] = timezone
        
        # Escribe los datos en Firestore
        doc_ref.set(update_data, merge=True)  # merge=True actualiza los campos sin sobrescribir el documento
        print(f"Chat ID {user_chat_id} guardado/actualizado con éxito.")
    except Exception as e:
        print(f"Error al guardar/actualizar chat_id {user_chat_id}: {e}")


#@profile
async def eliminar_chat_id(chat_id):
    """Elimina un chat_id de Firestore si está registrado."""
    try:
        # Referencia al documento del usuario
        doc_ref = db.collection("chat_ids").document(str(chat_id))
        
        # Eliminar el documento
        doc_ref.delete()
        print(f"Chat ID {chat_id} eliminado correctamente.")
    except Exception as e:
        print(f"Error al eliminar chat_id {chat_id}: {e}")


#@profile
async def actualizar_timezone(user_chat_id, nueva_timezone):
    """Actualiza la zona horaria de un chat_id."""
    await guardar_chat_id(user_chat_id, timezone=nueva_timezone)


#@profile
def last_of(df, col, default=None):
    """Devuelve el último valor de df[col] de forma robusta (sin warnings),
    o default si no existe o está vacío."""
    try:
        s = df[col]
    except Exception:
        return default

    # Serie/ndarray/list vacío
    try:
        if getattr(s, "empty", False) or len(s) == 0:
            return default
    except Exception:
        return default

    # Preferir posición por .iloc para evitar FutureWarning
    try:
        return s.iloc[-1] if hasattr(s, "iloc") else s[-1]
    except Exception:
        try:
            vals = getattr(s, "values", None)
            return vals[-1] if vals is not None and len(vals) else default
        except Exception:
            return default

#@profile
def _coerce_float_safe(v):
    try:
        return float(v)
    except Exception:
        return None

#@profile
def obtener_monedas(symbol):
    """
    Identifica si un símbolo es un par de divisas o un símbolo que no tiene divisa secundaria.
    """
    # Verificar si el símbolo parece un par Forex
    if len(symbol) > 3 and symbol[-3:].isalpha() and symbol[:-3].isalpha():
        return symbol[:-3], symbol[-3:]  # Divisa base y divisa secundaria

    # Si no es un par Forex, asumir que no tiene divisa secundaria
    return symbol, None

#@profile
def obtener_noticias(symbol, fecha_inicio, fecha_fin, limite=50, max_reintentos=3, tiempo_espera_inicial=5):
    """
    Obtiene noticias del mercado Forex para un símbolo dado.
    Utiliza caché en memoria y actualiza con los datos más recientes de la API.
    """
    global cache_noticias
    # Verificar si el símbolo ya está en el caché
    if symbol not in cache_noticias:
        cache_noticias[symbol] = pd.DataFrame()

    # Obtener el caché actual
    df_cache = cache_noticias[symbol]

    # Determinar la última fecha registrada en el caché
    if not df_cache.empty:
        ultima_fecha = df_cache['publishedDate'].max()
        fecha_inicio = ultima_fecha + timedelta(seconds=1)  # Buscar desde la última fecha más 1 segundo
        logger.info(f"Última fecha en caché para {symbol}: {ultima_fecha}")
    elif fecha_inicio is None:
        fecha_inicio = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')  # Predeterminado: última semana

    # Asegúrate de que fecha_fin esté establecida
    if fecha_fin is None:
        fecha_fin = datetime.now().strftime('%Y-%m-%d')  # Hasta la fecha actual

    # Convertir fechas de inicio y fin a UTC
    fecha_inicio = pd.to_datetime(fecha_inicio).tz_localize(pytz.UTC) if pd.to_datetime(fecha_inicio).tzinfo is None else pd.to_datetime(fecha_inicio)

    # Ajustar fecha_fin para incluir todo el día 23
    fecha_fin = pd.to_datetime(fecha_fin).tz_localize(pytz.UTC) if pd.to_datetime(fecha_fin).tzinfo is None else pd.to_datetime(fecha_fin)
    fecha_fin = fecha_fin + timedelta(days=1)  # Aumentar un día
    fecha_fin = fecha_fin.replace(hour=0, minute=0, second=0)  # Establecer a las 00:00:00 del siguiente día


    # Determinar el endpoint adecuado según la categoría del símbolo
    if symbol in categorias["Cripto"]:
        endpoint = "https://financialmodelingprep.com/api/v4/crypto_news"
        logging.info(f"MTORO9 {endpoint}")
    elif any(symbol in categorias[categoria] for categoria in ["Principales", "Cruces", "Exóticos", "OilAndGas", "Agricultura", "Indices"]):
        endpoint = "https://financialmodelingprep.com/api/v4/forex_news"
        logging.info(f"MTORO10 {endpoint}")
    else:
        endpoint = "https://financialmodelingprep.com/api/v3/stock_news"
        logging.info(f"MTORO11 {endpoint}")


    # Llamar a la API para obtener nuevas noticias
    # Construir la URL
    if "stock_news" in endpoint:
        url = f"{endpoint}?tickers={symbol}&from={fecha_inicio.strftime('%Y-%m-%d')}&to={fecha_fin.strftime('%Y-%m-%d')}&limit={limite}&apikey={API_KEY}"
    else:
        url = f"{endpoint}?symbol={symbol}&from={fecha_inicio.strftime('%Y-%m-%d')}&to={fecha_fin.strftime('%Y-%m-%d')}&limit={limite}&apikey={API_KEY}"
    reintento = 0
    tiempo_espera = tiempo_espera_inicial

    while reintento < max_reintentos:
        try:
            response = requests.get(url, timeout_request_global)
            if response.status_code == 200:
                nuevas_noticias = response.json()
                if isinstance(nuevas_noticias, list) and len(nuevas_noticias) > 0:
                    df_nuevas = pd.DataFrame(nuevas_noticias)

                    # Procesar fechas en 'publishedDate'
                    if 'publishedDate' in df_nuevas.columns:
                        df_nuevas['publishedDate'] = pd.to_datetime(df_nuevas['publishedDate'], errors='coerce')

                        # Validar si las fechas son tz-naive o ya tienen información de zona horaria
                        if df_nuevas['publishedDate'].dt.tz is None:
                            # Si es tz-naive, localiza primero en UTC
                            df_nuevas['publishedDate'] = df_nuevas['publishedDate'].dt.tz_localize(pytz.UTC)

                        # Convertir las fechas a la zona horaria configurada
                        df_nuevas['publishedDate'] = df_nuevas['publishedDate'].dt.tz_convert(pytz.UTC)

                    # Eliminar filas con fechas inválidas
                    df_nuevas = df_nuevas.dropna(subset=['publishedDate'])

                    # Actualizar el caché combinando con los datos nuevos
                    df_cache = pd.concat([df_cache, df_nuevas]).drop_duplicates(subset='title').sort_values('publishedDate')

                    # Actualizar el caché global
                    cache_noticias[symbol] = df_cache
                else:
                    logger.info(f"No se encontraron noticias nuevas para {symbol}.")
            else:
                logger.info(f"Error al consultar la API de noticias para {symbol}. Código de respuesta: {response.status_code}")

            # Retornar el caché actualizado
            return cache_noticias[symbol]
        except requests.exceptions.RequestException as e:
            logger.info(f"Error de conexión: {e}")
            reintento += 1
            if reintento < max_reintentos:
                logger.info(f"Reintentando en {tiempo_espera} segundos...")
                time.sleep(tiempo_espera)
                tiempo_espera *= 2

#@profile
def obtener_noticias_simbolo(symbol, fecha_inicio, fecha_fin, limite=50, max_reintentos=3, tiempo_espera_inicial=5):
    """
    Obtiene noticias del mercado Forex para un símbolo dado.
    Utiliza caché en memoria y actualiza con los datos más recientes de la API.
    """

    # Convertir fechas de inicio y fin a UTC
    fecha_inicio = pd.to_datetime(fecha_inicio).tz_localize(pytz.UTC) if pd.to_datetime(fecha_inicio).tzinfo is None else pd.to_datetime(fecha_inicio)
    fecha_fin = fecha_inicio

    # Determinar el endpoint adecuado según la categoría del símbolo
    if symbol in categorias["Cripto"]:
        endpoint = "https://financialmodelingprep.com/api/v4/crypto_news"
    elif any(symbol in categorias[categoria] for categoria in ["Principales", "Cruces", "Exóticos", "OilAndGas", "Agricultura", "Indices"]):
        endpoint = "https://financialmodelingprep.com/api/v4/forex_news"
    else:
        endpoint = "https://financialmodelingprep.com/api/v3/stock_news"


    # Llamar a la API para obtener nuevas noticias
    # Construir la URL
    if "stock_news" in endpoint:
        url = f"{endpoint}?tickers={symbol}&from={fecha_inicio.strftime('%Y-%m-%d')}&to={fecha_fin.strftime('%Y-%m-%d')}&limit={limite}&apikey={API_KEY}"
    else:
        url = f"{endpoint}?symbol={symbol}&from={fecha_inicio.strftime('%Y-%m-%d')}&to={fecha_fin.strftime('%Y-%m-%d')}&limit={limite}&apikey={API_KEY}"

    logging.info(f"MTORO stock URL: {url}")

    reintento = 0
    tiempo_espera = tiempo_espera_inicial

    while reintento < max_reintentos:
        try:
            response = requests.get(url, timeout_request_global)
            if response.status_code == 200:
                nuevas_noticias = response.json()
                if isinstance(nuevas_noticias, list) and len(nuevas_noticias) > 0:
                    df_nuevas = pd.DataFrame(nuevas_noticias)

                    # Procesar fechas en 'publishedDate'
                    if 'publishedDate' in df_nuevas.columns:
                        df_nuevas['publishedDate'] = pd.to_datetime(df_nuevas['publishedDate'], errors='coerce')

                        # Validar si las fechas son tz-naive o ya tienen información de zona horaria
                        if df_nuevas['publishedDate'].dt.tz is None:
                            # Si es tz-naive, localiza primero en UTC
                            df_nuevas['publishedDate'] = df_nuevas['publishedDate'].dt.tz_localize(pytz.UTC)

                        # Convertir las fechas a la zona horaria configurada
                        df_nuevas['publishedDate'] = df_nuevas['publishedDate'].dt.tz_convert(pytz.UTC)

                    # Eliminar filas con fechas inválidas
                    df_nuevas = df_nuevas.dropna(subset=['publishedDate'])
                    return df_nuevas
                else:
                    logger.info(f"No se encontraron noticias nuevas para {symbol}.")
                    return pd.DataFrame()
            else:
                logger.info(f"Error al consultar la API de noticias para {symbol}. Código de respuesta: {response.status_code}")
                return pd.DataFrame()

            # Retornar el caché actualizado    
        except requests.exceptions.RequestException as e:
            logger.info(f"Error de conexión: {e}")
            reintento += 1
            if reintento < max_reintentos:
                logger.info(f"Reintentando en {tiempo_espera} segundos...")
                time.sleep(tiempo_espera)
                tiempo_espera *= 2


#@profile
def calcular_impacto_noticias(df_noticias):
    if df_noticias.empty:
        return 0

    impacto_total = 0
    for _, noticia in df_noticias.iterrows():
        texto = noticia.get('title', '') + ' ' + noticia.get('summary', '')
        sentimiento = analizar_sentimiento(texto)  # Función que analiza sentimiento del texto
        impacto_total += sentimiento  # Acumular sentimiento como impacto

    # Normalizar impacto
    impacto_normalizado = impacto_total / len(df_noticias) if len(df_noticias) > 0 else 0
    return impacto_normalizado


# CARPETA_HISTORICOS debe estar definido en tu módulo
# cache_historicos es global

#@profile
async def cargar_datos_historicos_inicial():
    """
    Carga inicial de los datos históricos en un diccionario global desde los archivos locales.
    Permite JSON estándar (lista), NDJSON (una fila JSON por línea) y envoltura {"data": [...]}.
    """
    global cache_historicos
    nuevo_cache = {}

    for archivo in os.listdir(CARPETA_HISTORICOS):
        # Soporta .json y opcionalmente .jsonl (NDJSON)
        if not (archivo.endswith(".json") or archivo.endswith(".jsonl")):
            continue

        ruta = os.path.join(CARPETA_HISTORICOS, archivo)

        try:
            # 1) Leer archivo (maneja BOM con utf-8-sig)
            async with aiofiles.open(ruta, mode="r", encoding="utf-8-sig", errors="ignore") as f:
                contenido = await f.read()

            if not contenido or not contenido.strip():
                logging.info("Archivo vacío: %s. Saltando.", ruta)
                continue

            # 2) Parseo JSON robusto
            data_local = None
            try:
                data_local = json.loads(contenido)
            except json.JSONDecodeError as je:
                # Intentar NDJSON (una fila por línea)
                lineas = [ln for ln in contenido.splitlines() if ln.strip()]
                try:
                    data_local = [json.loads(ln) for ln in lineas]
                    logging.info("Parseado como NDJSON: %s (%d filas)", archivo, len(data_local))
                except Exception:
                    logging.info("JSON inválido en %s: %s; inicio=%r", archivo, je, contenido[:120])
                    continue

            # 3) Normalizar a lista de registros
            if isinstance(data_local, dict) and "data" in data_local and isinstance(data_local["data"], list):
                data_local = data_local["data"]

            if not isinstance(data_local, list) or len(data_local) == 0:
                logging.info("%s no contiene lista de registros válida. Saltando.", archivo)
                continue

            df_local = pd.DataFrame(data_local)

            # 4) Validar/normalizar index datetime
            if "date" in df_local.columns:
                # Convierte con tolerancia y fuerza a tz-aware si quieres
                df_local["date"] = pd.to_datetime(df_local["date"], errors="coerce", utc=True)
                df_local = df_local.dropna(subset=["date"]).set_index("date").sort_index()
            else:
                logging.info("Archivo %s no tiene columna 'date'. Saltando.", archivo)
                continue

            if df_local.empty or not isinstance(df_local.index, pd.DatetimeIndex):
                logging.info("Advertencia: DataFrame inválido o vacío en %s.", archivo)
                continue

            # 5) Extraer symbol/temporalidad desde nombre archivo
            base = archivo
            if base.endswith(".jsonl"):
                base = base[:-6]
            elif base.endswith(".json"):
                base = base[:-5]
            partes = base.split("_")
            if len(partes) != 2:
                logging.info("Formato de nombre inesperado: %s. Esperaba 'SYMBOL_TF.json'.", archivo)
                continue

            symbol, temporalidad = partes[0], partes[1]

            # 6) Guardar en cache local
            nuevo_cache.setdefault(symbol, {})[temporalidad] = df_local
            logging.info("Cargados datos históricos para %s en %s (%d filas).", symbol, temporalidad, len(df_local))

        except Exception as e:
            logging.info("Error al cargar datos de %s: %s", archivo, e)

    # 7) Swap atómico del cache
    cache_historicos = nuevo_cache
    logging.info("Datos históricos cargados en memoria: %d símbolos.", len(cache_historicos))


#@profile
async def obtener_dias_habiles_mercado():
    """
    Obtiene los días hábiles del mercado Forex para determinar el día anterior y el día siguiente.
    """
    # Obtener la fecha y hora actual en UTC (sin información de zona horaria para las fechas)
    ahora = datetime.now(pytz.UTC)
    fecha_actual = ahora.date()

    # Horas de apertura y cierre del mercado en UTC
    apertura_domingo_utc = 17  # 5 PM UTC
    cierre_viernes_utc = 17    # 5 PM UTC

    # Lista para almacenar los días hábiles del mercado
    dias_habiles = []

    # Retroceder para encontrar el día hábil anterior
    fecha_ayer = fecha_actual - timedelta(days=1)
    while True:
        dia_semana = fecha_ayer.weekday()
        if (dia_semana == 6 and ahora.hour >= apertura_domingo_utc) or (0 <= dia_semana <= 4) or (dia_semana == 5 and ahora.hour < cierre_viernes_utc):
            dias_habiles.append(fecha_ayer)
            break
        fecha_ayer -= timedelta(days=1)

    # Avanzar para encontrar el próximo día hábil
    fecha_manana = fecha_actual + timedelta(days=1)
    while True:
        dia_semana = fecha_manana.weekday()
        if (dia_semana == 6 and ahora.hour >= apertura_domingo_utc) or (0 <= dia_semana <= 4) or (dia_semana == 5 and ahora.hour < cierre_viernes_utc):
            dias_habiles.append(fecha_manana)
            break
        fecha_manana += timedelta(days=1)

    # Retornar fechas en formato naive (sin zona horaria)
    return dias_habiles



# ======================================================================
# Economic Events — FMP + (optional) investpy, UTC-first + local presentation
# ======================================================================

# Invariants:
# - Use UTC for network/API timestamps.
# - Convert to local timezone for presentation/DF columns exposed to caller.
# - Cache by local-day key, with de-duplication by logical keys.
# - Persist/Load fallback in JSON file (APP_CONFIG.events_file).
# Logical keys to deduplicate events
_EVENT_DEDUP_KEYS = ["currency", "event", "date_country"]

# In-memory cache (by local day key "YYYY-MM-DD")
_cache_eventos_economicos = {}
_cache_eventos_lock = threading.Lock()


def _local_tz():
    # Reuse application's configured local tz
    return get_local_tz()


def _local_day_key(dt_like) -> str:
    ts = pd.to_datetime(dt_like, errors="coerce")
    if ts.tzinfo is None:
        ts = ts.tz_localize(pytz.UTC)
    else:
        ts = ts.tz_convert(pytz.UTC)
    return ts.strftime("%Y-%m-%d")


def _dedupe_events(df: pd.DataFrame, keys=None) -> pd.DataFrame:
    if df is None or df.empty:
        return df
    keys = keys or _EVENT_DEDUP_KEYS
    df = df.copy()
    for k in keys:
        if k not in df.columns:
            df[k] = pd.NA
    # stable sort then drop dups
    sort_cols = [c for c in ["date_country", "date"] if c in df.columns]
    if sort_cols:
        df = df.sort_values(sort_cols, ascending=True, kind="mergesort")
    return df.drop_duplicates(subset=keys, keep="last").reset_index(drop=True)


def _split_by_local_day(df: pd.DataFrame) -> dict:
    if df is None or df.empty:
        return {}
    if "date_country" not in df.columns:
        if "date" in df.columns:
            df = df.copy()
            df["date_country"] = df["date"]
        else:
            return {}
    buckets = {}
    for _, row in df.iterrows():
        try:
            key = _local_day_key(row["date_country"])
        except Exception:
            continue
        buckets.setdefault(key, []).append(row)
    for k in list(buckets.keys()):
        buckets[k] = pd.DataFrame(buckets[k])
    return buckets


def cargar_eventos_completos() -> list[dict]:
    """
    Retorna eventos de ~últimos 365 días desde Firestore (colección 'eventos_completos').
    Backend en UTC (sin conversiones locales).
    """
    try:
        col = db.collection("eventos_completos")

        now_utc = pd.Timestamp.utcnow().tz_localize("UTC")
        fi_utc = (now_utc - pd.Timedelta(days=365)).to_pydatetime()
        ff_utc = now_utc.to_pydatetime()

        q = col.where("date_utc", ">=", fi_utc).where("date_utc", "<=", ff_utc)
        docs = q.stream()
        return [doc.to_dict() for doc in docs if getattr(doc, "exists", True)]
    except Exception as e:
        logger.info("[Firestore] cargar_eventos_completos error: %s", e)
        return []


def cache_eventos_merge(df: pd.DataFrame) -> None:
    """Thread-safe merge into cache by local day key."""
    if df is None or df.empty:
        return
    groups = _split_by_local_day(df)
    if not groups:
        return
    with _cache_eventos_lock:
        for day_key, part in groups.items():
            existing = _cache_eventos_economicos.get(day_key)
            if existing is not None and not existing.empty:
                merged = pd.concat([existing, part], ignore_index=True)
            else:
                merged = part
            _cache_eventos_economicos[day_key] = _dedupe_events(merged)
        logger.info("[Eventos] Cached days: %d", len(_cache_eventos_economicos))


def _fmp_econ_fetch(from_date: str, to_date: str, *, timeout: int) -> pd.DataFrame:
    """Fetch one calendar window from FMP with HTTP_SESSION (retry-enabled)."""
    url = "https://financialmodelingprep.com/api/v3/economic_calendar"
    params = {"from": from_date, "to": to_date, "apikey": APP_CONFIG.fmp_api_key}
    try:
        t0 = time.time()
        logger.info("[FMP-econ] GET %s params=%s timeout=%s", url, params, timeout)
        r = HTTP_SESSION.get(url, params=params, timeout=timeout)
        logger.info("[FMP-econ] respuesta status=%s en %.3fs", r.status_code, time.time()-t0)

        if r.status_code != 200:
            logger.info("[FMP-econ] HTTP %s params=%s", r.status_code, params)
            return pd.DataFrame()
        data = r.json()
        if not isinstance(data, list) or not data:
            return pd.DataFrame()
        df = pd.DataFrame(data)
        for c in ["date","currency","event","impact","actual","estimate","previous"]:
            if c not in df.columns:
                df[c] = pd.NA
        # API is UTC timestamps
        df["date"] = pd.to_datetime(df["date"], errors="coerce", utc=True)
        df = df.dropna(subset=["date"])
        df["impact"] = df["impact"].astype(str).str.capitalize()
        df = df[df["impact"].isin(["High","Medium","Low"])].copy()
        for c in ["actual","estimate","previous"]:
            df[c] = pd.to_numeric(df[c], errors="coerce")
        return df.sort_values("date", ascending=True).reset_index(drop=True)
    except Exception as e:
        logger.warning("[FMP-econ] Error tras %.3fs: %s", time.time()-t0, e)
        logger.warning("[FMP-econ] Error: %s", e)
        return pd.DataFrame()


def _investing_econ_fetch() -> pd.DataFrame:
    """Optional investpy calendar (GMT base)."""
    if not globals().get("_HAS_INVESTPY", False):
        return pd.DataFrame()
    try:
        cal = investpy.economic_calendar(time_zone="GMT")
        cal = cal[cal["importance"].isin(["high","medium","low"])].copy()
        cal["date"] = pd.to_datetime(cal["date"], format="%d/%m/%Y", errors="coerce")
        time_str = cal["time"].where(cal["time"].str.match(r"^\d{2}:\d{2}$", na=False), "00:00")
        cal["date"] = pd.to_datetime(cal["date"].dt.strftime("%Y-%m-%d") + " " + time_str, utc=True)
        cal = cal.rename(columns={"importance":"impact", "forecast":"estimate"})
        keep = ["date","currency","event","actual","estimate","previous","impact"]
        for c in keep:
            if c not in cal.columns:
                cal[c] = pd.NA
        for c in ["actual","estimate","previous"]:
            cal[c] = pd.to_numeric(cal[c], errors="coerce")
        cal["impact"] = cal["impact"].astype(str).str.capitalize()
        return cal[keep].sort_values("date", ascending=True).reset_index(drop=True)
    except Exception as e:
        logger.info("[Investing] Error economic calendar: %s", e)
        return pd.DataFrame()


def _to_local_df(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return df
    out = df.copy()
    out["date"] = pd.to_datetime(out["date"], utc=True, errors="coerce").dt.tz_convert(pytz.UTC)
    out["date_country"] = out["date"]
    return out


def obtener_dias_habiles_mercado() -> list:
    """Return [yesterday, tomorrow] dates considering FX 17:00 UTC window loosely."""
    now_utc = datetime.now(timezone.utc)
    today = now_utc.date()
    y = today - timedelta(days=1)
    t = today + timedelta(days=1)
    # Loosely assume weekdays, but include late Sunday/early Friday window
    days = []
    # yesterday-ish
    while True:
        d = y.weekday()  # 0 Mon ... 6 Sun
        if (d == 6 and now_utc.hour >= 17) or (0 <= d <= 4) or (d == 5 and now_utc.hour < 17):
            days.append(y); break
        y -= timedelta(days=1)
    # tomorrow-ish
    while True:
        d = t.weekday()
        if (d == 6 and now_utc.hour >= 17) or (0 <= d <= 4) or (d == 5 and now_utc.hour < 17):
            days.append(t); break
        t += timedelta(days=1)
    return days


def obtener_eventos_economicos(*, plan: str | None = None, desde_inicio: bool = False) -> pd.DataFrame:
    """
    Pulls economic events around the FX window:
      - starter: only [yesterday, tomorrow]
      - premium + desde_inicio: paginate from 1900-01-01 to tomorrow in APP_CONFIG.econ_chunk_days
    Returns local-tz DataFrame with columns:
      ['date','currency','event','actual','estimate','previous','impact','date_country']
    """
    plan = (plan or APP_CONFIG.fmp_plan).lower()

    dias = obtener_dias_habiles_mercado()
    y = dias[0].strftime("%Y-%m-%d")
    tm = dias[1].strftime("%Y-%m-%d")
    if plan == "premium" and desde_inicio:
        start, end = "1900-01-01", tm
    else:
        start, end = y, tm

    # paginate if needed
    parts = []
    if plan == "premium" and desde_inicio:
        fi = pd.to_datetime(start)
        ff = pd.to_datetime(end)
        cur = fi
        step = APP_CONFIG.econ_chunk_days
        while cur <= ff:
            a = cur.strftime("%Y-%m-%d")
            b = min(cur + timedelta(days=step-1), ff).strftime("%Y-%m-%d")
            d = _fmp_econ_fetch(a, b, timeout=APP_CONFIG.http_timeout)
            if not d.empty: parts.append(d)
            cur = pd.to_datetime(b) + timedelta(days=1)
    else:
        d = _fmp_econ_fetch(start, end, timeout=APP_CONFIG.http_timeout)
        if not d.empty: parts.append(d)

    # optional investing layer
    inv = _investing_econ_fetch()
    if not inv.empty:
        parts.append(inv)

    if not parts:
        return pd.DataFrame(columns=["date","currency","event","actual","estimate","previous","impact","date_country"])

    df = pd.concat(parts, ignore_index=True)
    df = _to_local_df(df)
    df = _dedupe_events(df)
    cache_eventos_merge(df)
    return df.reset_index(drop=True)


def obtener_eventos_economicos_futuros(fecha_inicio, fecha_fin) -> pd.DataFrame:
    """
    Future window [fecha_inicio, fecha_fin] ingresada en timezone_country (usuario).
    La ventana de consulta a FMP se calcula en America/New_York (FMP_TZ).
    Se consulta en chunks y se filtra a impactos High/Medium.
    """
    FMP_TZ = pytz.timezone('America/New_York')

    # --- Parseo y TZ del usuario ---
    fi = pd.to_datetime(fecha_inicio, errors="coerce")
    ff = pd.to_datetime(fecha_fin, errors="coerce")
    if fi is pd.NaT or ff is pd.NaT:
        return pd.DataFrame(columns=[
            "date","currency","event","actual","estimate","previous",
            "impact","ponderacion","date_country"
        ])

    # Si vienen naive, asume timezone_country; si no, convierte a esa tz
    if fi.tzinfo is None: fi = timezone_country.localize(fi)
    else:                 fi = fi.tz_convert(timezone_country)
    if ff.tzinfo is None: ff = timezone_country.localize(ff)
    else:                 ff = ff.tz_convert(timezone_country)

    # --- Proyectar el rango a la TZ de FMP para construir fechas de consulta ---
    fi_fmp = fi.astimezone(FMP_TZ)
    ff_fmp = ff.astimezone(FMP_TZ)

    # Cerrar al fin de día **en la TZ de FMP**
    ff_fmp = ff_fmp.normalize() + pd.Timedelta(days=1) - pd.Timedelta(seconds=1)

    # Asegurar orden
    if fi_fmp > ff_fmp:
        fi_fmp, ff_fmp = ff_fmp, fi_fmp

    # Fechas (solo día) para las consultas chunked (en FMP_TZ)
    fi_day = fi_fmp.strftime("%Y-%m-%d")
    ff_day = ff_fmp.strftime("%Y-%m-%d")

    # --- Loop chunked ---
    parts = []
    cur = pd.to_datetime(fi_day)  # naive date; solo usamos la parte de fecha
    end = pd.to_datetime(ff_day)
    step = APP_CONFIG.econ_chunk_days

    while cur <= end:
        a = cur.strftime("%Y-%m-%d")
        b = min(cur + timedelta(days=step - 1), end).strftime("%Y-%m-%d")

        d = _fmp_econ_fetch(a, b, timeout=APP_CONFIG.http_timeout)
        if not d.empty:
            # Impacto robusto (case-insensitive) + ponderación
            impact_norm = d["impact"].astype(str).str.strip().str.lower()
            mask = impact_norm.isin({"high", "medium"})
            d = d.loc[mask].copy()
            if not d.empty:
                # solo aplica el map sobre las filas filtradas, usando el mismo índice
                d["ponderacion"] = impact_norm.loc[mask].map({"high": 1.0, "medium": 0.5}).fillna(0.25)
                parts.append(d)

        cur = pd.to_datetime(b) + timedelta(days=1)

    if not parts:
        return pd.DataFrame(columns=[
            "date","currency","event","actual","estimate","previous",
            "impact","ponderacion","date_country"
        ])

    df = pd.concat(parts, ignore_index=True)

    # Tu pipeline: aquí conviertes fechas del evento a timezone_country y deduplicas
    df = _to_local_df(df)      # asegúrate que genere 'date_country' en timezone_country
    df = _dedupe_events(df)
    cache_eventos_merge(df)

    # Columnas garantizadas
    for c in ["ponderacion", "date_country"]:
        if c not in df.columns:
            df[c] = pd.NA

    return df.reset_index(drop=True)



def _slugify_event(s: str) -> str:
    import re
    s = (s or "").lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s or "event"

def _event_doc_id(row: dict) -> str:
    # Deterministic doc id to avoid duplicates on re-saves
    # Use epoch seconds of date_utc + currency + slugified event
    try:
        dt = row.get("date_utc") or row.get("date") or row.get("date_country")
        if dt is None:
            return _slugify_event(row.get("event","event"))
        ts = pd.Timestamp(dt)
        if ts.tzinfo is None:
            # assume UTC
            ts = ts.tz_localize(pytz.UTC)
        epoch = int(ts.timestamp())
    except Exception:
        epoch = 0
    cur = str(row.get("currency") or "XX")
    ev = _slugify_event(str(row.get("event") or "event"))
    return f"{epoch}_{cur}_{ev}"

def _to_utc_datetime(dt_like) -> datetime | None:
    """Convierte cualquier valor de fecha a datetime timezone-aware en UTC (o None si no se puede)."""
    try:
        ts = pd.to_datetime(dt_like, errors="coerce", utc=True)
        if pd.isna(ts):
            return None
        return ts.to_pydatetime()
    except Exception:
        return None

def _firestore_save_events(events: list[dict]) -> None:
    """
    Persiste eventos en 'eventos_completos' usando batch en trozos de 400.
    Asegura date_utc en UTC, normaliza numéricos y capitaliza 'impact'.
    """
    try:
        col = db.collection("eventos_completos")
    except Exception:
        col = None

    if not col or not events:
        return

    chunk = 400  # por debajo del límite 500
    for i in range(0, len(events), chunk):
        batch = db.batch()
        for row in events[i:i + chunk]:
            data = dict(row)  # copia defensiva

            # 2.1) Asegurar date_utc (preferida para queries)
            date_val = data.get("date_utc") or data.get("date")
            utc_dt = _to_utc_datetime(date_val)
            if utc_dt is not None:
                data["date_utc"] = utc_dt
            else:
                # si no hay fecha válida, evita grabar basura
                continue

            # 2.2) Normalizar numéricos
            for key in ("actual", "estimate", "previous"):
                if key in data:
                    try:
                        data[key] = float(data[key]) if data[key] is not None else None
                    except Exception:
                        data[key] = None

            # 2.3) Impact capitalizado
            if isinstance(data.get("impact"), str):
                data["impact"] = data["impact"].capitalize()

            # 2.4) Doc ID estable
            doc_id = _event_doc_id(data)  # se asume que ya existe en tu código
            ref = col.document(doc_id)
            batch.set(ref, data, merge=True)

        batch.commit()

def _firestore_load_events_range(fecha_inicio, fecha_fin) -> list[dict]:
    """
    Carga eventos por rango [fecha_inicio, fecha_fin] usando 'date_utc' (UTC).
    Incluye fin de día completo.
    """
    try:
        col = db.collection("eventos_completos")
    except Exception:
        return []

    # Rango → UTC aware
    fi = pd.to_datetime(fecha_inicio, errors="coerce", utc=True)
    ff = pd.to_datetime(fecha_fin, errors="coerce", utc=True)
    if pd.isna(fi) or pd.isna(ff):
        return []

    # Incluir día final completo
    ff = ff + pd.Timedelta(days=1) - pd.Timedelta(seconds=1)

    fi_utc = fi.to_pydatetime()
    ff_utc = ff.to_pydatetime()

    try:
        q = col.where("date_utc", ">=", fi_utc).where("date_utc", "<=", ff_utc)
        docs = q.stream()
        return [doc.to_dict() for doc in docs if getattr(doc, "exists", True)]
    except Exception as e:
        logger.info("[Firestore] load range error: %s", e)
        return []


def guardar_eventos_completos(eventos: list[dict]) -> None:
    """Persist events to Firestore (batch writes)."""
    try:
        _firestore_save_events(eventos)
    except Exception as e:
        logger.info("[Firestore] save error: %s", e)
    


def obtener_eventos_guardados_o_futuros(fecha_inicio, fecha_fin) -> pd.DataFrame:
    """
    Try API future fetch first; if empty/error, fall back to Firestore for the range.
    """
    # 1) Try pulling from API
    try:
        df = obtener_eventos_economicos_futuros(fecha_inicio, fecha_fin)
        if not df.empty:
            # Save to Firestore
            try:
                guardar_eventos_completos(df.to_dict(orient="records"))
            except Exception as e:
                logger.info("[Eventos] Could not persist to Firestore: %s", e)
            return df
    except Exception as e:
        logger.info("[Eventos] API error future fetch: %s", e)

    # 2) Fallback: load from Firestore
    try:
        saved = _firestore_load_events_range(fecha_inicio, fecha_fin)
        if saved:
            s = pd.DataFrame(saved)
            # Rebuild DataFrame schema and local tz
            if "date_utc" in s.columns:
                s["date"] = pd.to_datetime(s["date_utc"], errors="coerce", utc=True).dt.tz_convert(pytz.UTC)
            elif "date" in s.columns:
                s["date"] = pd.to_datetime(s["date"], errors="coerce", utc=True).dt.tz_convert(pytz.UTC)
            else:
                s["date"] = pd.NaT
            s["date_country"] = s["date"]
            keep = ["date","currency","event","actual","estimate","previous","impact","date_country"]
            for c in keep:
                if c not in s.columns:
                    s[c] = pd.NA
            s = s[keep]
            s = s.sort_values("date", ascending=True).reset_index(drop=True)
            return s
    except Exception as e:
        logger.info("[Firestore] fallback error: %s", e)

    return pd.DataFrame()



#@profile

def generar_imagen_por_currency(df, currency, max_filas=50):
    """
    Genera imágenes para eventos económicos filtrados por moneda con ajustes dinámicos de ancho de columna y contenido.
    """
    df_currency = df[df['currency'] == currency]

    if df_currency.empty:
        logger.info(f"No hay eventos para la divisa {currency}.")
        return None

    # Dividir el DataFrame en partes si es necesario
    num_filas = len(df_currency)
    buffers = []

    for inicio in range(0, num_filas, max_filas):
        df_parte = df_currency.iloc[inicio:inicio + max_filas]

        # Calcular ancho de cada columna basado en el contenido y las cabeceras
        max_col_widths = [
            max(len(str(item)) for item in df_parte[col].tolist() + [col]) + 2
            for col in df_parte.columns
        ]

        # Configurar tamaño de la figura dinámicamente
        fig_width = sum(max_col_widths) * 0.1  # Ajustar el multiplicador según sea necesario
        fig, ax = plt.subplots(figsize=(min(fig_width, 20), min(len(df_parte) * 0.5, 12)))

        ax.axis('tight')
        ax.axis('off')

        # Crear la tabla
        col_labels = [_wrap_header(c, WRAP_WIDTH_HEADER) for c in df_parte.columns]
        table = ax.table(
            cellText=df_parte.astype(str).values,
            colLabels=col_labels,
            cellLoc='center',
            loc='center'
        )

        # Ajustar escalas y fuente
        table.auto_set_font_size(False)
        table.set_fontsize(8)  # Reducir tamaño de fuente
        table.scale(1.0, 0.7)  # Escala más compacta

        # Ajustar ancho de las columnas
        for i, col_width in enumerate(max_col_widths):
            for (row, col), cell in table.get_celld().items():
                if col == i:  # Ajusta solo las celdas de la columna actual
                    cell.set_width(col_width * 0.01)

        # Ajustar márgenes
        fig.subplots_adjust(left=0.01, right=0.99, top=0.99, bottom=0.01)

        buf = BytesIO()
        try:
            plt.savefig(buf, format='png', bbox_inches='tight', dpi=150)  # DPI ajustado para claridad
            buf.seek(0)
            plt.close(fig)
            buffers.append(buf)
        except Exception as e:
            logger.info(f"Error al generar la imagen para {currency}: {e}")
            plt.close(fig)
            return None

    # Retorna una lista de buffers o un único buffer
    return buffers if len(buffers) > 1 else buffers[0]

# Función para enviar imágenes por currency a Telegram
#@profile
async def enviar_imagenes_por_currency_a_usuario(df, context, user_chat_id=None, intentos=3):

    currencies = df['currency'].unique()

    # Determinar la lista de chat_ids a los que enviar
    chat_ids = [user_chat_id] if user_chat_id else clientes_chat_ids

    for currency in currencies:
        imagen = generar_imagen_por_currency(df, currency)

        if isinstance(imagen, list):  # Si es una lista de imágenes
            total_partes = len(imagen)
            for indice, img in enumerate(imagen, start=1):
                if img.getbuffer().nbytes > 0:
                    await context.bot.send_photo(chat_id=user_chat_id, photo=img,caption=f"Eventos para la divisa {currency}. Parte {indice} de {total_partes}")
        elif imagen is None or imagen.getbuffer().nbytes == 0:
            for chat_id in chat_ids:
                for intento in range(intentos):
                    try:
                        await context.bot.send_message(chat_id=chat_id, text=f"No se pudo generar la imagen de eventos para la divisa {currency}.")
                        break  # Sal del bucle si tiene éxito
                    except TimedOut:
                        logger.info(f"Intento {intento + 1} fallido. Reintentando...")
                        await asyncio.sleep(2)  # Espera antes de reintentar
        else:
            for chat_id in chat_ids:
                for intento in range(intentos):
                    try:
                        await context.bot.send_photo(chat_id=chat_id, photo=imagen, caption=f"Eventos para la divisa {currency}")
                        imagen.seek(0)
                        break
                    except TimedOut:
                        logger.info(f"Intento {intento + 1} fallido. Reintentando...")
                        await asyncio.sleep(2)  # Espera antes de reintentar
                    except telegram.error.BadRequest as e:
                        logger.info(f"Error al enviar la imagen para {currency} a {chat_id}: {e}")


# Función para generar el link de Google Calendar, ahora con currency
#@profile
def generar_link_google_calendar(event, date, currency, ponderacion):
    base_url = "https://www.google.com/calendar/render"

    # Fuerza UTC para Calendar
    dt_utc = pd.to_datetime(date, errors='coerce', utc=True)

    start_date_str = dt_utc.strftime('%Y%m%dT%H%M%SZ')
    end_date_str   = start_date_str

    query = {
        "action": "TEMPLATE",
        "text": f"{event} {currency} Peso: {ponderacion}",
        "dates": f"{start_date_str}/{end_date_str}",
        "details": f"Recordatorio para el evento: {event} {currency}. Peso: {ponderacion}",
        "sf": "true",
        "output": "xml"
    }
    return f"{base_url}?{urlencode(query)}"

    
#@profile
async def enviar_eventos_y_archivo_calendar(df, context, user_chat_id):
    # --- Normalización defensiva del DF ---
    if df is None or getattr(df, "empty", True):
        return

    df = df.copy()  # evitar chained assignments

    # 1) Fecha: prioriza UTC para Calendar
    if "date" not in df.columns or not pd.api.types.is_datetime64_any_dtype(df["date"]):
        if "date_utc" in df.columns and pd.api.types.is_datetime64_any_dtype(df["date_utc"]):
            df["date"] = df["date_utc"]
        elif "date_country" in df.columns and pd.api.types.is_datetime64_any_dtype(df["date_country"]):
            # si solo tienes la local, conviértela a UTC para Calendar
            df["date"] = df["date_country"].dt.tz_convert(pytz.UTC)
        else:
            # intenta parsear y forzar UTC
            df["date"] = pd.to_datetime(df.get("date"), errors="coerce", utc=True)

    # Asegura que 'date' sea tz-aware UTC
    if not hasattr(df["date"].dtype, "tz") or df["date"].dt.tz is None:
        df["date"] = pd.to_datetime(df["date"], errors="coerce", utc=True)
    else:
        df["date"] = df["date"].dt.tz_convert(pytz.UTC)

    # 2) Currency y Event como columnas existentes
    if "currency" not in df.columns:
        df["currency"] = ""
    if "event" not in df.columns:
        df["event"] = ""

    # 3) Ponderación: si falta, deriva de 'impact'
    if "ponderacion" not in df.columns:
        if "impact" in df.columns:
            impact_norm = df["impact"].astype(str).str.strip().str.lower()
            df["ponderacion"] = impact_norm.map({"high": 1.0, "medium": 0.5}).fillna(0.25)
        else:
            df["ponderacion"] = 0.5  # valor seguro

    # 4) Filtra filas válidas
    df = df[df["date"].notna()]
    if df.empty:
        return

    # --- Resto de tu función, con pequeñas defensas al acceder a la fila ---
    cal = Calendar()
    cal.add('prodid', '-//Mi Sistema de Trading//ES')
    cal.add('version', '2.0')

    for _, row in df.iterrows():
        # lee con .get() y defaults seguros
        ev   = row.get('event', '')
        cur  = row.get('currency', '')
        peso = row.get('ponderacion', 0.5)
        dt_utc = row.get('date')

        # Generar link usando SIEMPRE UTC
        link = generar_link_google_calendar(ev, dt_utc, cur, peso)

        if link:
            # Mostrar la fecha al usuario en su zona local, pero marcando zona
            fecha_local_str = (
                pd.to_datetime(dt_utc)
                  .tz_convert(timezone_country)
                  .strftime('%Y-%m-%d %H:%M:%S %Z')
            )
            ponderacion_str = "Alta" if peso == 1.0 else ("Media" if peso == 0.5 else str(peso))

            evento_msg = (
                f"Evento: {escape_markdown(ev, version=2)}\n"
                f"Divisa: {escape_markdown(cur, version=2)}\n"
                f"Fecha: {escape_markdown(fecha_local_str, version=2)}\n"
                f"Ponderación: {escape_markdown(ponderacion_str, version=2)}\n"
                f"[Agregar a Google Calendar]({escape_markdown(link, version=2)})\n"
            )

            try:
                await context.bot.send_message(chat_id=user_chat_id, text=evento_msg, parse_mode='MarkdownV2')
            except Exception as e:
                logger.info(f"Error al enviar texto de eventos a {user_chat_id}: {e}")

        # Agregar evento al .ics (en UTC como pediste)
        event = Event()
        event.add('summary', ev)
        event.add('dtstart', pd.to_datetime(dt_utc).to_pydatetime())  # aware UTC
        event.add('dtend',   pd.to_datetime(dt_utc).to_pydatetime())
        event.add('description', f"Recordatorio para el evento: {ev} ({cur}). Peso: {peso}")
        event.add('location', "Google Calendar")

        cal.add_component(event)

    # Guardado y envío del .ics (igual que tu código)
    with tempfile.NamedTemporaryFile(delete=False, suffix=".ics") as f:
        f.write(cal.to_ical())
        file_path = f.name

    try:
        await context.bot.send_document(chat_id=user_chat_id, document=open(file_path, 'rb'), filename="eventos_calendar.ics")
    except Exception as e:
        logger.info(f"Error al enviar el archivo de calendario a {user_chat_id}: {e}")
    finally:
        os.remove(file_path)


#@profile
def _is_finite_number(x) -> bool:
    try:
        return isinstance(x, (int, float, np.floating)) and math.isfinite(float(x))
    except Exception:
        return False


#@profile
def _coerce_float(x):
    """Devuelve float(x) si es finito; si no, None."""
    try:
        if x is None:
            return None
        if isinstance(x, str):
            s = x.strip()
            if s == "" or s.lower() in {"nan", "none", "null"}:
                return None
            x = s
        v = float(x)
        return v if math.isfinite(v) else None
    except Exception:
        return None

#@profile
def _lookup_rt_tick(cache_rt: dict, symbol: str):
    """Busca el último close en varias variantes de clave."""
    if symbol in cache_rt:
        return cache_rt[symbol]
    up = symbol.upper()
    lo = symbol.lower()
    if up in cache_rt:
        return cache_rt[up]
    if lo in cache_rt:
        return cache_rt[lo]
    # soportar prefijos tipo "OANDA:EURUSD"
    if ":" in symbol:
        tail = symbol.split(":", 1)[1]
        if tail in cache_rt:
            return cache_rt[tail]
        if tail.upper() in cache_rt:
            return cache_rt[tail.upper()]
        if tail.lower() in cache_rt:
            return cache_rt[tail.lower()]
    return None

# Implementación de hilos para optimizar las solicitudes de datos
#@profile
def obtener_datos_con_hilos(
    symbol: str,
    temporalidad: str,
    user_chat_id: str | None = None,  # ya no se usa (conservado por compatibilidad)
    cfg: dict | None = None
):
    """
    Obtiene únicamente histórico desde FMP y aplica recorte final por `bars` si corresponde.
    Se eliminó todo el manejo de cache/tick realtime y la mezcla de última vela.
    """
    try:
        # 1) normalizar TF
        tf = _norm_tf(temporalidad)

        # 2) resolver bars desde cfg; si no hay → None
        bars = get_bars_for_tf(cfg, tf)

        # 3) histórico (acepta bars=None; si tu fetch ya respeta bars, igual hacemos tail defensivo)
        df_historico = obtener_datos_historicos_fmp(symbol, tf, bars=bars)
        if df_historico is None or df_historico.empty:
            logger.info("Datos históricos no disponibles para %s en %s", symbol, tf)
            return pd.DataFrame()

        df_out = df_historico.sort_index()

        # 4) recorte final si bars es numérico
        if isinstance(bars, int) and bars > 0 and len(df_out) > bars:
            before = len(df_out)
            df_out = df_out.tail(bars)
            logging.info("[HIST][TAIL] recortado de %d a %d por bars=%d", before, len(df_out), bars)

        logging.info(
            "[HIST][RETURN] %s-%s len=%d last_ts=%s last_close=%s",
            symbol, tf, len(df_out),
            (df_out.index[-1] if not df_out.empty else None),
            (df_out['close'].iloc[-1] if not df_out.empty else None),
        )
        return df_out

    except Exception as e:
        logger.info("Se cayó en obtener_datos_con_hilos %s-%s – error: %s", symbol, temporalidad, e)
        return pd.DataFrame()

# Función para calcular indicadores
#@profile
def calcular_indicadores(df, temporalidad):
    window = min(definir_window(temporalidad), len(df))

    # Media Móvil Simple (SMA)
    df['SMA'] = df['close'].rolling(window=window).mean()

    # Desviación Estándar para las Bandas de Bollinger
    df['bollinger_std'] = df['close'].rolling(window=window).std()

    # Cálculo de Bandas de Bollinger
    df['bollinger_upper'] = df['SMA'] + (2 * df['bollinger_std'])
    df['bollinger_lower'] = df['SMA'] - (2 * df['bollinger_std'])

    # Eliminamos la columna temporal de desviación estándar
    df.drop(columns=['bollinger_std'], inplace=True)

    # Señal de compra/venta basada en Bandas de Bollinger
    df['bollinger_signal'] = 'Neutral'
    df.loc[df['close'] > df['bollinger_upper'], 'bollinger_signal'] = 'Venta'
    df.loc[df['close'] < df['bollinger_lower'], 'bollinger_signal'] = 'Compra'

    
    # Cálculo de EMAs y MACD
    df['ema_12'] = df['close'].ewm(span=12, adjust=False).mean()
    df['ema_26'] = df['close'].ewm(span=26, adjust=False).mean()
    df['macd'] = df['ema_12'] - df['ema_26']
    df['signal'] = df['macd'].ewm(span=9, adjust=False).mean()

    # Detectar cruce del MACD (evita recalcular shift varias veces)
    macd_prev = df['macd'].shift(1)
    sig_prev  = df['signal'].shift(1)
    macd_cur  = df['macd']
    sig_cur   = df['signal']
    df['macd_cruce'] = np.where(
        (macd_prev < sig_prev) & (macd_cur > sig_cur), 'Cruce Alcista',
        np.where((macd_prev > sig_prev) & (macd_cur < sig_cur), 'Cruce Bajista', 'No cruce')
    )

    # Detectar si el MACD está cerca de cruzar la señal
    df['macd_cerca_de_cruzar'] = np.where(
        (macd_cur - sig_cur).abs() < (df['macd'].std() * 0.05), 'Cerca del cruce', 'No cerca'
    )

    # Cálculo de RSI
    delta = df['close'].diff(1)
    gain = delta.where(delta > 0, 0).rolling(window=window).mean()
    loss = -delta.where(delta < 0, 0).rolling(window=window).mean()
    rs = gain / loss
    df['rsi'] = 100 - (100 / (1 + rs))

    # Cálculo del Estocástico
    low_min = df['low'].rolling(window=window).min()
    high_max = df['high'].rolling(window=window).max()
    df['%K'] = 100 * (df['close'] - low_min) / (high_max - low_min)
    df['%D'] = df['%K'].rolling(window=window).mean()

    # Cálculo del ATR
    df['true_range'] = np.maximum(
        df['high'] - df['low'],
        np.maximum((df['high'] - df['close'].shift(1)).abs(),
                   (df['low'] - df['close'].shift(1)).abs())
    )
    df['ATR'] = df['true_range'].rolling(window=window).mean()

    # Señales de divergencia
    df['divergencia_macd'] = (df['macd'] > df['macd'].shift(1)) & (df['close'] < df['close'].shift(1))
    df['divergencia_rsi'] = (df['rsi'] > df['rsi'].shift(1)) & (df['close'] < df['close'].shift(1))

    df['divergencia_macd_bull'] = (df['macd'] > df['macd'].shift(1)) & (df['close'] < df['close'].shift(1))
    df['divergencia_macd_bear'] = (df['macd'] < df['macd'].shift(1)) & (df['close'] > df['close'].shift(1))
    df['divergencia_rsi_bull']  = (df['rsi']  > df['rsi'].shift(1))  & (df['close'] < df['close'].shift(1))
    df['divergencia_rsi_bear']  = (df['rsi']  < df['rsi'].shift(1))  & (df['close'] > df['close'].shift(1))

    # Convertir columnas clave a tipo numérico
    for col in ['rsi', '%K', '%D', 'ATR', 'macd', 'signal', 'ema_12', 'ema_26']:
        df[col] = pd.to_numeric(df[col], errors='coerce')

    # Interpolación optimizada: evita df.drop + df.update (costoso en DF grandes)
    cols_excluir = ['macd_cruce', 'macd_cerca_de_cruzar', 'bollinger_signal', 'bollinger_upper', 'bollinger_lower']
    cols_interp = [
        c for c in df.columns
        if c not in cols_excluir and pd.api.types.is_numeric_dtype(df[c])
    ]
    if cols_interp:
        df.loc[:, cols_interp] = df.loc[:, cols_interp].interpolate(method='linear')

    # Rellenar valores restantes con forward fill y backward fill (mantiene comportamiento original)
    df.ffill(inplace=True)
    df.bfill(inplace=True)

    return df

def limitar_probabilidad(probabilidad_exito):
    return max(1, min(probabilidad_exito, 100))

# Función para ajustar la probabilidad técnica con incrementos controlados
#@profile
def ajustar_probabilidad_tecnica(df, temporalidad, window, cfg: Optional[dict] = None):
    """
    Calcula la probabilidad técnica usando:
      - flags de activación y magnitudes desde cfg.tecnica si existen
      - en caso contrario usa los valores “en duro” (defaults)
    """
    # ---- defaults (mismos que vives en la UI) ----
    FLAGS_DEF = dict(
        ponderacion_macd=True,
        cruce_reciente_macd=True,
        ponderacion_rsi=True,
        ponderacion_estocastico=True,
        divergencias=True,
        atr_baja_vol=True,
        senal_alcista_soporte=True,
        senal_bajista_resistencia=True,
        bonus_triple_signal=True,
    )

    MAG_DEF = dict(
        macd_base=10,                # ±10
        macd_cruce_reciente=7,       # ±7
        rsi_base=3,                  # ±3
        estoc_base=3,                # ±3
        divergencias_bonus=10,       # +10
        atr_penalizacion=-5,         # -5
        near_support_bonus=3,        # +3
        near_resistance_penalty=-3,  # -3
        triple_signal_bonus=10,      # +10
    )

    # Umbrales “de libro” iguales a tu lógica original
    RSI_LOW, RSI_HIGH = 25, 75
    STOCH_LOW, STOCH_HIGH = 25, 75
    ATR_LOW_FACTOR = 0.8
    NEAR_LEVEL_ATR_MULT = 0.5

    # ---- extrae cfg segura ----
    tcfg = (cfg or {}).get("tecnica", {})
    flags = {k: bool(tcfg.get(k, v)) for k, v in FLAGS_DEF.items()}
    mag   = {k: float(tcfg.get(k, v)) for k, v in MAG_DEF.items()}

    # ---- datos mínimos ----
    if len(df) < 2:
        logger.info("No hay suficientes datos para calcular la probabilidad técnica.")
        return 50.0

    ultima_fila = df.iloc[-1]
    penultima_fila = df.iloc[-2]
    probabilidad_tecnica = 50.0

    # Soportes / resistencias sobre ventana
    soporte_nivel_1 = df["low"].rolling(window=window).min().iloc[-1]
    resistencia_nivel_1 = df["high"].rolling(window=window).max().iloc[-1]

    # Señales detectadas para el bono triple
    senal_macd = False
    senal_rsi = False
    senal_estocastico = False

    # ---------- MACD base ----------
    if flags["ponderacion_macd"]:
        if ultima_fila["macd"] > ultima_fila["signal"]:
            # MACD > señal → sesgo alcista
            if ultima_fila["macd"] < penultima_fila["macd"]:
                probabilidad_tecnica += mag["macd_base"] * 0.5   # tu “+5” cuando base=10
            else:
                probabilidad_tecnica += mag["macd_base"]         # tu “+10”
            senal_macd = True
        else:
            # MACD < señal → sesgo bajista
            if ultima_fila["macd"] > penultima_fila["macd"]:
                probabilidad_tecnica -= abs(mag["macd_base"]) * 0.5  # tu “-5”
            else:
                probabilidad_tecnica -= abs(mag["macd_base"])        # tu “-10”

    # ---------- Cruce reciente MACD ----------
    if flags["cruce_reciente_macd"]:
        if penultima_fila["macd"] < penultima_fila["signal"] and ultima_fila["macd"] > ultima_fila["signal"]:
            probabilidad_tecnica += mag["macd_cruce_reciente"]   # tu “+7”
        elif penultima_fila["macd"] > penultima_fila["signal"] and ultima_fila["macd"] < ultima_fila["signal"]:
            probabilidad_tecnica -= abs(mag["macd_cruce_reciente"])  # tu “-7”

    # ---------- RSI ----------
    if flags["ponderacion_rsi"]:
        rsi = float(ultima_fila["rsi"])
        if rsi < RSI_LOW:
            probabilidad_tecnica += mag["rsi_base"]   # “+3”
            senal_rsi = True
        elif rsi > RSI_HIGH:
            probabilidad_tecnica -= abs(mag["rsi_base"])  # “-3”

    # ---------- Estocástico (%K/%D) ----------
    if flags["ponderacion_estocastico"]:
        k, d = float(ultima_fila["%K"]), float(ultima_fila["%D"])
        if k > d and k < STOCH_LOW:
            probabilidad_tecnica += mag["estoc_base"]   # “+3”
            senal_estocastico = True
        elif k < d and k > STOCH_HIGH:
            probabilidad_tecnica -= abs(mag["estoc_base"])  # “-3”

    # ---------- Divergencias (MACD/RSI) ----------
    if flags["divergencias"]:
        try:
            if df["divergencia_macd"].tail(3).sum() > 1 or df["divergencia_rsi"].tail(3).sum() > 1:
                probabilidad_tecnica += mag["divergencias_bonus"]  # “+10”
        except Exception:
            pass  # columnas ausentes → no afecta

    # ---------- ATR bajo (filtra señales) ----------
    if flags["atr_baja_vol"]:
        atr = ultima_fila.get("ATR")
        if pd.notna(atr):
            atr_media = df["ATR"].rolling(window).mean().iloc[-1]
            if pd.notna(atr_media) and atr < atr_media * ATR_LOW_FACTOR:
                probabilidad_tecnica += mag["atr_penalizacion"]  # “-5”

    # ---------- Cercanía a niveles ----------
    if pd.notna(ultima_fila.get("ATR")):
        near_thresh = ultima_fila["ATR"] * NEAR_LEVEL_ATR_MULT
    else:
        near_thresh = (resistencia_nivel_1 - soporte_nivel_1) * 0.02  # fallback

    if flags["senal_alcista_soporte"]:
        if abs(ultima_fila["close"] - soporte_nivel_1) < near_thresh:
            probabilidad_tecnica += mag["near_support_bonus"]  # “+3”

    if flags["senal_bajista_resistencia"]:
        if abs(ultima_fila["close"] - resistencia_nivel_1) < near_thresh:
            probabilidad_tecnica += mag["near_resistance_penalty"]  # “-3”

    # ---------- Bono triple señal ----------
    if flags["bonus_triple_signal"]:
        if senal_macd and senal_rsi and senal_estocastico:
            probabilidad_tecnica += mag["triple_signal_bonus"]  # “+10”

    # Limitar al rango [0,100]
    return limitar_probabilidad(probabilidad_tecnica)


#@profile
def limpiar_valores(val):
    """Limpia y convierte los valores que contienen 'K' y '%'."""
    if isinstance(val, str):
        val = val.replace('%', '')  # Eliminar el símbolo de porcentaje
        if 'K' in val:
            val = val.replace('K', '')  # Eliminar la 'K'
            try:
                val = float(val) * 1000  # Multiplicar por 1000 para convertir a miles
            except ValueError:
                return None
        try:
            return float(val)  # Convertir el valor a float
        except ValueError:
            return None
    return val

#@profile
def analizar_sentimiento(texto):

    if not texto or pd.isna(texto):
        return 0  # Sin ajuste si no hay texto

    # Analizar el sentimiento usando TextBlob
    sentimiento = TextBlob(texto).sentiment.polarity

    # Ajuste basado en el sentimiento
    if sentimiento > 0.2:
        return 2.5  # Sentimiento positivo
    elif sentimiento < -0.2:
        return -2.5  # Sentimiento negativo
    else:
        return 0  # Sentimiento neutro


#@profile
def ajustar_probabilidad_fundamental(probabilidad_exito, df_eventos, symbol, temporalidad,
                                     fecha_inicio=None, fecha_fin=None, cfg: Optional[dict]=None):

    FUND_DEFAULTS = {
        "obtener_noticias": True,
        "calcular_impacto_noticias": True,
        "impacto_noticias_factor": 0.10,
        "consider_events_hours": 72,
        "recency_recent_minutes": 15,
        "recency_recent_boost": 1.5,
        "recency_decay_floor": 0.30,
        "impact_weights": {"high": 2.0, "medium": 1.5, "low": 1.0},
        "flip_secondary_currency": True,
        "cat_weights": {
            "unemployment": {"good": 0.3, "bad": -0.2},
            "employment":   {"good": 0.3, "bad": -0.2},
            "inflation":    {"good": 0.3, "bad": -0.3},
            "gdp":          {"good": 0.3, "bad": -0.3},
            "retail":       {"good": 0.3, "bad": -0.2},
            "rates":        {"good": 0.3, "bad": -0.3},
            "generic": {"better_both": 0.25, "better_estimate": 0.15, "better_prev": 0.10, "worse": -0.25},
        },
        "per_event_cap": 0.35,
        "return_on_no_events": 50.0,
    }

    # Mezclar defaults con cfg
    fund = {**FUND_DEFAULTS, **(cfg or {}).get("fundamental", {})}
    impact_weights = {**FUND_DEFAULTS["impact_weights"], **fund.get("impact_weights", {})}
    catw = FUND_DEFAULTS["cat_weights"].copy()
    for k, v in fund.get("cat_weights", {}).items():
        catw[k] = {**catw.get(k, {}), **v}

    # --- Noticias (igual que antes pero con factor configurable) ---
    try:
        if fund["obtener_noticias"]:
            global cache_noticias
            if "cache_noticias" not in globals() or cache_noticias is None:
                cache_noticias = {}
            df_noticias = cache_noticias.get(symbol) or obtener_noticias(symbol, fecha_inicio, fecha_fin)
            cache_noticias[symbol] = df_noticias
        else:
            df_noticias = None

        if fund["calcular_impacto_noticias"] and df_noticias is not None:
            imp = calcular_impacto_noticias(df_noticias)
            if imp is not None:
                probabilidad_exito += imp * float(fund["impacto_noticias_factor"])
    except Exception as e:
        logging.info(f"Noticias/impacto omitidos: {e}")

    # --- Validación de eventos ---
    cols = {'date','actual','estimate','previous','currency','event','impact'}
    if df_eventos is None or df_eventos.empty or not cols.issubset(df_eventos.columns):
        return limitar_probabilidad(float(fund["return_on_no_events"]))

    df = df_eventos.copy()
    df['date'] = pd.to_datetime(df['date'], errors='coerce')
    df['actual']   = pd.to_numeric(df['actual'].apply(limpiar_valores),   errors='coerce')
    df['estimate'] = pd.to_numeric(df['estimate'].apply(limpiar_valores), errors='coerce')
    df['previous'] = pd.to_numeric(df['previous'].apply(limpiar_valores), errors='coerce')

    # Filtrar por ventana temporal
    now = datetime.now(pytz.UTC)
    if fund["consider_events_hours"] is not None:
        cutoff = now - timedelta(hours=int(fund["consider_events_hours"]))
        df = df[df['date'] >= cutoff]

    base_empty_return = float(fund["return_on_no_events"])
    if df.empty:
        return limitar_probabilidad(base_empty_return)

    # Orden + recencia
    df = df.sort_values('date')
    reciente = df.iloc[-1]
    recent_minutes = (now - reciente['date']).total_seconds()/60.0
    recent_boost = float(fund["recency_recent_boost"]) if recent_minutes < int(fund["recency_recent_minutes"]) else 1.0
    tmax = max((now - df['date'].min()).total_seconds(), 1.0)

    div_p, div_s = obtener_monedas(symbol)

    for _, ev in df.iterrows():
        if any(pd.isna(ev[k]) for k in ['actual','estimate','previous']):
            continue

        # impacto base
        iw = impact_weights.get(str(ev.get('impact','')).lower(), 1.0)
        mult = iw

        if ev['date'] == reciente['date']:
            mult *= recent_boost

        # decaimiento por antigüedad
        age = max((now - ev['date']).total_seconds(), 0.0)
        decay = max(1.0 - (age / tmax), float(fund["recency_decay_floor"]))
        mult *= decay

        # signo por moneda secundaria
        if fund["flip_secondary_currency"] and ev['currency'] == div_s:
            mult *= -1.0

        cat = detectar_categoria(ev['event'])
        adj = 0.0

        #@profile
        def _cap(x):  # limitar por evento
            cap = float(fund["per_event_cap"])
            return max(min(x, cap), -cap)

        # helpers cat weights
        cw = catw
        if symbol in ['WTI','BRENT'] and cat == 'Crude Oil Inventories':
            # misma lógica; usa 0.3/0.2 pero capea
            if pd.isna(ev['estimate']):
                adj = (0.3 if ev['actual'] < ev['previous'] else -0.3) * mult
            else:
                if ev['actual'] < ev['estimate'] and ev['actual'] < ev['previous']:
                    adj = 0.3 * mult
                elif ev['actual'] < ev['estimate']:
                    adj = 0.2 * mult
                else:
                    adj = -0.3 * mult

        elif symbol == 'SOYB' and cat in ['Crop Production Report','Weather Report']:
            if cat == 'Crop Production Report':
                if pd.isna(ev['estimate']):
                    adj = (0.3 if ev['actual'] < ev['previous'] else -0.3) * mult
                else:
                    if ev['actual'] < ev['estimate'] and ev['actual'] < ev['previous']:
                        adj = 0.3 * mult
                    elif ev['actual'] < ev['estimate']:
                        adj = 0.2 * mult
                    else:
                        adj = -0.3 * mult
            else:
                txt = str(ev['event']).lower()
                adj = (0.3 if ('drought' in txt or 'storm' in txt) else -0.2) * mult

        else:
            # categorías macro generales
            if cat == 'Unemployment Rate':
                g,b = cw['unemployment']['good'], cw['unemployment']['bad']
                if pd.isna(ev['estimate']):
                    adj = (g if ev['actual'] < ev['previous'] else b) * mult
                else:
                    if ev['actual'] < ev['estimate'] and ev['actual'] < ev['previous']:
                        adj = g * mult
                    elif ev['actual'] < ev['estimate']:
                        adj = 0.2 * mult
                    else:
                        adj = b * mult

            elif cat == 'Employment Report':
                g,b = cw['employment']['good'], cw['employment']['bad']
                if pd.isna(ev['estimate']):
                    adj = (g if ev['actual'] > ev['previous'] else b) * mult
                else:
                    if ev['actual'] > ev['estimate'] and ev['actual'] > ev['previous']:
                        adj = g * mult
                    elif ev['actual'] > ev['estimate']:
                        adj = 0.2 * mult
                    else:
                        adj = b * mult

            elif cat == 'Inflation Rate':
                g,b = cw['inflation']['good'], cw['inflation']['bad']
                if pd.isna(ev['estimate']):
                    adj = (g if ev['actual'] < ev['previous'] else b) * mult
                else:
                    if ev['actual'] < ev['estimate'] and ev['actual'] < ev['previous']:
                        adj = g * mult
                    elif ev['actual'] < ev['estimate']:
                        adj = 0.2 * mult
                    else:
                        adj = b * mult

            elif cat == 'GDP':
                g,b = cw['gdp']['good'], cw['gdp']['bad']
                if pd.isna(ev['estimate']):
                    adj = (g if ev['actual'] > ev['previous'] else b) * mult
                else:
                    if ev['actual'] > ev['estimate'] and ev['actual'] > ev['previous']:
                        adj = g * mult
                    elif ev['actual'] > ev['estimate']:
                        adj = 0.2 * mult
                    else:
                        adj = -0.2 * mult

            elif cat == 'Retail Sales':
                g,b = cw['retail']['good'], cw['retail']['bad']
                if pd.isna(ev['estimate']):
                    adj = (g if ev['actual'] > ev['previous'] else b) * mult
                else:
                    if ev['actual'] > ev['estimate'] and ev['actual'] > ev['previous']:
                        adj = g * mult
                    elif ev['actual'] > ev['estimate']:
                        adj = 0.2 * mult
                    else:
                        adj = b * mult

            elif cat == 'Interest Rate':
                g,b = cw['rates']['good'], cw['rates']['bad']
                if pd.isna(ev['estimate']):
                    adj = (g if ev['actual'] < ev['previous'] else b) * mult
                else:
                    if ev['actual'] < ev['estimate'] and ev['actual'] < ev['previous']:
                        adj = g * mult
                    elif ev['actual'] < ev['estimate']:
                        adj = 0.2 * mult
                    else:
                        adj = b * mult

            else:
                # genérico
                gg = cw['generic']
                if pd.isna(ev['estimate']):
                    adj = (gg['better_prev'] if ev['actual'] > ev['previous'] else gg['worse']) * mult
                else:
                    if ev['actual'] > ev['estimate'] and ev['actual'] > ev['previous']:
                        adj = gg['better_both'] * mult
                    elif ev['actual'] > ev['estimate']:
                        adj = gg['better_estimate'] * mult
                    elif ev['actual'] > ev['previous']:
                        adj = gg['better_prev'] * mult
                    else:
                        adj = gg['worse'] * mult

        probabilidad_exito += _cap(adj)

    return limitar_probabilidad(probabilidad_exito)

# Función para calcular la probabilidad general ponderando más la probabilidad fundamental
#@profile
def calcular_probabilidad_general(probabilidad_tecnica: float,
                                  probabilidad_fundamental: float,
                                  cfg: dict | None = None) -> float:
    """
    Combina prob. técnica y fundamental usando pesos de cfg.general
    (prob_tecnica_pct y prob_fundamental_pct). Si no hay cfg, usa 50/50.
    Devuelve [0..100].
    """
    # fallbacks si no viene cfg o vienen valores raros
    try:
      g = (cfg or {}).get("general", {})
      w_t = float(g.get("prob_tecnica_pct", 50.0))
      w_f = float(g.get("prob_fundamental_pct", 50.0))
    except Exception:
      w_t, w_f = 50.0, 50.0

    # normaliza (evita división por cero y pesos negativos)
    w_t = max(0.0, w_t)
    w_f = max(0.0, w_f)
    s = (w_t + w_f) or 1.0
    w_t /= s
    w_f /= s

    out = probabilidad_tecnica * w_t + probabilidad_fundamental * w_f
    # clamp
    if out < 0: out = 0
    if out > 100: out = 100
    return float(out)

# Implementación de la zona de no trading
#@profile
def verificar_zona_no_trading(df, window):
    # Condiciones para identificar una zona de no trading
    if df['ATR'].iloc[-1] < df['ATR'].rolling(window=window).mean().iloc[-1] * 0.8:
        return True  # Baja volatilidad, podría ser una zona de no trading
    return False

# Calcular RSI
#@profile
def calcular_rsi(df, window):
    delta = df['close'].diff(1)
    ganancia = (delta.where(delta > 0, 0)).rolling(window=window).mean()
    perdida = (-delta.where(delta < 0, 0)).rolling(window=window).mean()
    rs = ganancia / perdida
    rsi = 100 - (100 / (1 + rs))
    df['RSI'] = rsi  # Agregar RSI al DataFrame
    return df

# Calcular Estocástico %K y %D
#@profile
def calcular_estocastico(df, window):
    low_min = df['low'].rolling(window=window).min()
    high_max = df['high'].rolling(window=window).max()
    df['%K'] = 100 * (df['close'] - low_min) / (high_max - low_min)
    df['%D'] = df['%K'].rolling(window).mean()  # %D es la media de %K
    return df

# Verificar si el RSI y %K indican sobreventa
#@profile
def verificar_zona_sobreventa(df, window, rsi_threshold=30, k_threshold=20):
    if 'RSI' not in df.columns:
        df = calcular_rsi(df, window)  # Calcular RSI si no existe
    if '%K' not in df.columns:
        df = calcular_estocastico(df, window)  # Calcular %K si no existe
    return df['RSI'].iloc[-1] < rsi_threshold and df['%K'].iloc[-1] < k_threshold

# Verificar si el RSI y %K indican sobrecompra
#@profile
def verificar_zona_sobrecompra(df, window, rsi_threshold=70, k_threshold=80):
    if 'RSI' not in df.columns:
        df = calcular_rsi(df, window)  # Calcular RSI si no existe
    if '%K' not in df.columns:
        df = calcular_estocastico(df, window)  # Calcular %K si no existe
    return df['RSI'].iloc[-1] > rsi_threshold and df['%K'].iloc[-1] > k_threshold

# Función para predecir la tendencia basándose en datos en tiempo real
#@profile
def predecir_tendencia_en_tiempo_real(df, temporalidad):

    if df.empty:
        logger.info(f"No hay suficientes datos para predecir la tendencia en {temporalidad}.")
        return "Neutral"  # Si no hay datos, se retorna una tendencia neutral

    # Usar los datos en tiempo real para proyectar el próximo cruce del MACD
    macd_actual = df['macd'].iloc[-1]
    signal_actual = df['signal'].iloc[-1]
    
    # Definir la tendencia con base en el MACD y su señal
    if macd_actual > signal_actual:
        if macd_actual > df['macd'].iloc[-2]:  # Si el MACD sigue subiendo
            return "Alcista"
        else:
            return "Alcista debil"
    elif macd_actual < signal_actual:
        if macd_actual < df['macd'].iloc[-2]:  # Si el MACD sigue bajando
            return "Bajista"
        else:
            return "Bajista debil"
    else:
        return "Neutral"

PRIORIDAD_PATRONES = {
    "Hombro Cabeza Hombro": 5,
    "Hombro Cabeza Hombro Invertido": 5,
    "Estrella del Amanecer": 4,
    "Estrella de la Noche": 4,
    "Tres Cuervos Negros": 3,
    "Tres Soldados Blancos": 3,
    "Bandera Alcista": 3,
    "Bandera Bajista": 3,
    "Harami Bajista": 2,
    "Envolvente Alcista": 2,
    "Envolvente Bajista": 2,
    "Martillo": 1,
    "Martillo Invertido": 1,
    "Hombre Colgado": 1,
    "Estrella Fugaz": 1,
    "Pinzas de Techo": 1,
    "Pinzas de Suelo": 1,
}

#MTORO Antiguo (detecta mas patrones porque no tiene confirmación)
#@profile
def detectar_patrones_velas(df, window):
    patrones_detectados = {}

    # -------------------- Hombro Cabeza Hombro --------------------
    if len(df) >= 7:
        highs = df['high'].iloc[-7:].values
        o = df['open'].iloc[-7:].values
        c = df['close'].iloc[-7:].values
        l = df['low'].iloc[-7:].values

        h1, h2, h3, cabeza, h4, h5, h6 = highs

        i_cabeza = np.argmax(highs)
        hombros_simetricos = abs(h1 - h6) / max(h1, h6, 1e-6) < 0.15 and abs(h2 - h5) / max(h2, h5, 1e-6) < 0.15
        cuerpo = abs(c[3] - o[3])
        mecha_sup = highs[3] - max(c[3], o[3])
        mecha_inf = min(c[3], o[3]) - l[3]
        cabeza_definida = cuerpo > 0 and mecha_sup < cuerpo * 1.5 and mecha_inf < cuerpo * 1.5

        if i_cabeza == 3 and hombros_simetricos and cabeza > h2 and h4 < cabeza and cabeza_definida:
            patrones_detectados['Hombro Cabeza Hombro'] = True

        lows = df['low'].iloc[-7:].values
        l1, l2, l3, cabeza_inv, l4, l5, l6 = lows
        i_cabeza_inv = np.argmin(lows)
        hombros_simetricos_inv = abs(l1 - l6) / max(l1, l6, 1e-6) < 0.15 and abs(l2 - l5) / max(l2, l5, 1e-6) < 0.15
        if i_cabeza_inv == 3 and hombros_simetricos_inv and cabeza_inv < l2 and l4 > cabeza_inv:
            patrones_detectados['Hombro Cabeza Hombro Invertido'] = True

    
    # -------------------- Conflicto con patrones mayores --------------------
    conflicto_mayor = any(p in patrones_detectados for p in ["Hombro Cabeza Hombro", "Hombro Cabeza Hombro Invertido"])

    # -------------------- Martillo --------------------
    if not conflicto_mayor and len(df) >= 5:
        # Patrón de Martillo (alcista en tendencia bajista)
        martillo = (df['close'].iloc[-2] > df['open'].iloc[-2]) & \
                ((df['low'].iloc[-2] < df['open'].iloc[-2]) & (df['low'].iloc[-2] < df['close'].iloc[-2])) & \
                ((df['close'].iloc[-2] - df['low'].iloc[-2]) >= (df['high'].iloc[-2] - df['close'].iloc[-2]) * 2) & \
                ((df['high'].iloc[-2] - df['close'].iloc[-2]) <= (df['close'].iloc[-2] - df['low'].iloc[-2]) * 0.3)

        confirmacion_martillo = df['close'].iloc[-1] > df['open'].iloc[-1]

        if martillo and confirmacion_martillo:
            patrones_detectados['Martillo'] = True

        # Patrón de Martillo Invertido (alcista en tendencia bajista)
        martillo_invertido = (df['close'].iloc[-2] > df['open'].iloc[-2]) & \
                            ((df['high'].iloc[-2] > df['open'].iloc[-2]) & (df['high'].iloc[-2] > df['close'].iloc[-2])) & \
                            ((df['high'].iloc[-2] - df['close'].iloc[-2]) >= (df['close'].iloc[-2] - df['low'].iloc[-2]) * 2) & \
                            ((df['close'].iloc[-2] - df['low'].iloc[-2]) <= (df['high'].iloc[-2] - df['close'].iloc[-2]) * 0.3)

        confirmacion_martillo_inv = df['close'].iloc[-1] > df['open'].iloc[-1]

        if martillo_invertido and confirmacion_martillo_inv:
            patrones_detectados['Martillo Invertido'] = True

        # Patrón de Hombre Colgado (bajista en tendencia alcista)
        hombre_colgado = (df['close'].iloc[-2] < df['open'].iloc[-2]) & \
                        ((df['low'].iloc[-2] < df['open'].iloc[-2]) & (df['low'].iloc[-2] < df['close'].iloc[-2])) & \
                        ((df['close'].iloc[-2] - df['low'].iloc[-2]) >= (df['high'].iloc[-2] - df['close'].iloc[-2]) * 2) & \
                        ((df['high'].iloc[-2] - df['close'].iloc[-2]) <= (df['close'].iloc[-2] - df['low'].iloc[-2]) * 0.3)

        confirmacion_colgado = df['close'].iloc[-1] < df['open'].iloc[-1]

        if hombre_colgado and confirmacion_colgado:
            patrones_detectados['Hombre Colgado'] = True

        # Patrón de Estrella Fugaz (bajista en tendencia alcista)
        estrella_fugaz = (df['close'].iloc[-2] < df['open'].iloc[-2]) & \
                        ((df['high'].iloc[-2] > df['open'].iloc[-2]) & (df['high'].iloc[-2] > df['close'].iloc[-2])) & \
                        ((df['high'].iloc[-2] - df['close'].iloc[-2]) >= (df['close'].iloc[-2] - df['low'].iloc[-2]) * 2) & \
                        ((df['close'].iloc[-2] - df['low'].iloc[-2]) <= (df['high'].iloc[-2] - df['close'].iloc[-2]) * 0.3)

        confirmacion_estrella = df['close'].iloc[-1] < df['open'].iloc[-1]

        if estrella_fugaz and confirmacion_estrella:
            patrones_detectados['Estrella Fugaz'] = True


    # -------------------- Patrón de 3 velas --------------------
    if not conflicto_mayor and len(df) >= 3:
        # Patrón de Tres Soldados Blancos (alcista)
        tres_soldados_blancos = (
            (df['close'].iloc[-3] > df['open'].iloc[-3]) and
            (df['close'].iloc[-2] > df['open'].iloc[-2]) and
            (df['close'].iloc[-1] > df['open'].iloc[-1]) and
            (df['open'].iloc[-2] >= df['open'].iloc[-3]) and (df['open'].iloc[-2] <= df['close'].iloc[-3]) and
            (df['open'].iloc[-1] >= df['open'].iloc[-2]) and (df['open'].iloc[-1] <= df['close'].iloc[-2]) and
            (df['close'].iloc[-2] > df['close'].iloc[-3]) and
            (df['close'].iloc[-1] > df['close'].iloc[-2]) and
            (df['close'].iloc[-3] - df['low'].iloc[-3]) < (df['close'].iloc[-3] - df['open'].iloc[-3]) * 0.5 and
            (df['close'].iloc[-2] - df['low'].iloc[-2]) < (df['close'].iloc[-2] - df['open'].iloc[-2]) * 0.5 and
            (df['close'].iloc[-1] - df['low'].iloc[-1]) < (df['close'].iloc[-1] - df['open'].iloc[-1]) * 0.5
        )
        if tres_soldados_blancos:
            patrones_detectados['Tres Soldados Blancos'] = True

        # Patrón de Tres Cuervos Negros (bajista)
        tres_cuervos_negros = (
            (df['close'].iloc[-3] < df['open'].iloc[-3]) and
            (df['close'].iloc[-2] < df['open'].iloc[-2]) and
            (df['close'].iloc[-1] < df['open'].iloc[-1]) and
            (df['open'].iloc[-2] <= df['open'].iloc[-3]) and (df['open'].iloc[-2] >= df['close'].iloc[-3]) and
            (df['open'].iloc[-1] <= df['open'].iloc[-2]) and (df['open'].iloc[-1] >= df['close'].iloc[-2]) and
            (df['close'].iloc[-2] < df['close'].iloc[-3]) and
            (df['close'].iloc[-1] < df['close'].iloc[-2]) and
            (df['high'].iloc[-3] - df['close'].iloc[-3]) < (df['open'].iloc[-3] - df['close'].iloc[-3]) * 0.5 and
            (df['high'].iloc[-2] - df['close'].iloc[-2]) < (df['open'].iloc[-2] - df['close'].iloc[-2]) * 0.5 and
            (df['high'].iloc[-1] - df['close'].iloc[-1]) < (df['open'].iloc[-1] - df['close'].iloc[-1]) * 0.5
        )
        if tres_cuervos_negros:
            patrones_detectados['Tres Cuervos Negros'] = True

        # Estrella del Amanecer
        if (df['close'].shift(2).iloc[-1] < df['open'].shift(2).iloc[-1]) and \
           (abs(df['close'].shift(1).iloc[-1] - df['open'].shift(1).iloc[-1]) <= (df['high'].shift(1).iloc[-1] - df['low'].shift(1).iloc[-1]) * 0.3) and \
           (df['close'].iloc[-1] > df['open'].iloc[-1]) and \
           (df['close'].iloc[-1] > (df['open'].shift(2).iloc[-1] + df['close'].shift(2).iloc[-1]) / 2):
            patrones_detectados['Estrella del Amanecer'] = True

        # Estrella de la Noche
        if (df['close'].shift(2).iloc[-1] > df['open'].shift(2).iloc[-1]) and \
           (abs(df['close'].shift(1).iloc[-1] - df['open'].shift(1).iloc[-1]) <= (df['high'].shift(1).iloc[-1] - df['low'].shift(1).iloc[-1]) * 0.3) and \
           (df['close'].iloc[-1] < df['open'].iloc[-1]) and \
           (df['close'].iloc[-1] < (df['open'].shift(2).iloc[-1] + df['close'].shift(2).iloc[-1]) / 2):
            patrones_detectados['Estrella de la Noche'] = True

    # -------------------- Pinzas --------------------
    if not conflicto_mayor and len(df) >= 2:

        # Patrón de Pinzas de Techo (bajista en tendencia alcista)
        pinzas_techo = (abs(df['high'].iloc[-1] - df['high'].iloc[-2]) <= (df['high'].iloc[-2] - df['low'].iloc[-2]) * 0.05) & \
                    (df['close'].iloc[-2] > df['open'].iloc[-2]) & \
                    (df['close'].iloc[-1] < df['open'].iloc[-1])
        if pinzas_techo:
            patrones_detectados['Pinzas de Techo'] = True

        # Patrón de Pinzas de Suelo (alcista en tendencia bajista)
        pinzas_suelo = (abs(df['low'].iloc[-1] - df['low'].iloc[-2]) <= (df['high'].iloc[-2] - df['low'].iloc[-2]) * 0.05) & \
                    (df['close'].iloc[-2] < df['open'].iloc[-2]) & \
                    (df['close'].iloc[-1] > df['open'].iloc[-1])
        if pinzas_suelo:
            patrones_detectados['Pinzas de Suelo'] = True

        # Patrón de Envolvente Alcista (tendencia bajista)
        envolvente_alcista = (df['close'].shift(1) < df['open'].shift(1)) & \
                            (df['open'] < df['close'].shift(1)) & \
                            (df['close'] > df['open'].shift(1)) & \
                            (abs(df['close'] - df['open']) > abs(df['close'].shift(1) - df['open'].shift(1)))
        if envolvente_alcista.iloc[-1]:
            patrones_detectados['Envolvente Alcista'] = True

        # Patrón de Envolvente Bajista (tendencia alcista)
        envolvente_bajista = (df['close'].shift(1) > df['open'].shift(1)) & \
                            (df['open'] > df['close'].shift(1)) & \
                            (df['close'] < df['open'].shift(1)) & \
                            (abs(df['close'] - df['open']) > abs(df['close'].shift(1) - df['open'].shift(1)))
        if envolvente_bajista.iloc[-1]:
            patrones_detectados['Envolvente Bajista'] = True

        # Patrón de Harami Bajista (tendencia alcista)
        harami_bajista = (df['close'].shift(1) > df['open'].shift(1)) & \
                        (df['open'] > df['open'].shift(1)) & \
                        (df['close'] < df['close'].shift(1)) & \
                        (abs(df['close'] - df['open']) < abs(df['close'].shift(1) - df['open'].shift(1)))
        if harami_bajista.iloc[-1]:
            patrones_detectados['Harami Bajista'] = True

    # -------------------- Banderas --------------------
    if len(df) >= window: 

        # Patrón de Bandera Alcista
        bandera_alcista = (df['high'] > df['high'].rolling(window).max().shift(1)) & \
                        (df['low'] > df['low'].rolling(window).min().shift(1)) & \
                        (df['close'] > df['open'])
        if bandera_alcista.iloc[-1]:
            patrones_detectados['Bandera Alcista'] = True

        # Patrón de Bandera Bajista
        bandera_bajista = (df['low'] < df['low'].rolling(window).min().shift(1)) & \
                        (df['high'] < df['high'].rolling(window).max().shift(1)) & \
                        (df['close'] < df['open'])
        if bandera_bajista.iloc[-1]:
            patrones_detectados['Bandera Bajista'] = True

    return patrones_detectados

# Función para detectar patrones de velas japonesas mejorada con Estrella del Amanecer, Estrella de la Noche y Martillo Invertido
#@profile
def detectar_patrones_confirmados_velas(df: pd.DataFrame, window: int = 10):
    """
    Core mejorado: detecta patrones con confirmación y devuelve
    [(start_idx, end_idx, nombre_patron), ...] ya filtrados por prioridad.
    """
    # Validaciones rápidas
    cols = {"open", "high", "low", "close"}
    if not cols.issubset(df.columns):
        return []
    if len(df) < 2:
        return []

    patrones_detectados_dict = {}

    # -------------------- Pinzas / Envolventes / Harami (2 velas) --------------------
    if len(df) >= 2:
        c0, o0, h0, l0 = df['close'].iloc[-1], df['open'].iloc[-1], df['high'].iloc[-1], df['low'].iloc[-1]
        c1, o1, h1, l1 = df['close'].iloc[-2], df['open'].iloc[-2], df['high'].iloc[-2], df['low'].iloc[-2]

        # Pinzas de Techo
        if (abs(h0 - h1) <= (h1 - l1) * 0.05) and (c1 > o1) and (c0 < o0):
            patrones_detectados_dict['Pinzas de Techo'] = True

        # Pinzas de Suelo
        if (abs(l0 - l1) <= (h1 - l1) * 0.05) and (c1 < o1) and (c0 > o0):
            patrones_detectados_dict['Pinzas de Suelo'] = True

        # Envolvente Alcista
        envolvente_alcista = (c1 < o1) and (o0 < c1) and (c0 > o1) and (abs(c0 - o0) > abs(c1 - o1))
        if envolvente_alcista:
            patrones_detectados_dict['Envolvente Alcista'] = True

        # Envolvente Bajista
        envolvente_bajista = (c1 > o1) and (o0 > c1) and (c0 < o1) and (abs(c0 - o0) > abs(c1 - o1))
        if envolvente_bajista:
            patrones_detectados_dict['Envolvente Bajista'] = True

        # Harami Bajista
        harami_bajista = (c1 > o1) and (o0 > o1) and (c0 < c1) and (abs(c0 - o0) < abs(c1 - o1))
        if harami_bajista:
            patrones_detectados_dict['Harami Bajista'] = True

    # -------------------- Patrones de 3 velas --------------------
    if len(df) >= 3:
        c2, o2, h2, l2 = df['close'].iloc[-3], df['open'].iloc[-3], df['high'].iloc[-3], df['low'].iloc[-3]
        c1, o1, h1, l1 = df['close'].iloc[-2], df['open'].iloc[-2], df['high'].iloc[-2], df['low'].iloc[-2]
        c0, o0, h0, l0 = df['close'].iloc[-1], df['open'].iloc[-1], df['high'].iloc[-1], df['low'].iloc[-1]

        # Tres Soldados Blancos
        tsb = (
            (c2 > o2) and (c1 > o1) and (c0 > o0) and
            (o1 >= o2) and (o1 <= c2) and
            (o0 >= o1) and (o0 <= c1) and
            (c1 > c2) and (c0 > c1) and
            (c2 - l2) < (c2 - o2) * 0.5 and
            (c1 - l1) < (c1 - o1) * 0.5 and
            (c0 - l0) < (c0 - o0) * 0.5
        )
        if tsb:
            patrones_detectados_dict['Tres Soldados Blancos'] = True

        # Tres Cuervos Negros
        tcn = (
            (c2 < o2) and (c1 < o1) and (c0 < o0) and
            (o1 <= o2) and (o1 >= c2) and
            (o0 <= o1) and (o0 >= c1) and
            (c1 < c2) and (c0 < c1) and
            (h2 - c2) < (o2 - c2) * 0.5 and
            (h1 - c1) < (o1 - c1) * 0.5 and
            (h0 - c0) < (o0 - c0) * 0.5
        )
        if tcn:
            patrones_detectados_dict['Tres Cuervos Negros'] = True

        # Estrella del Amanecer
        ea = (
            (c2 < o2) and
            (abs(c1 - o1) <= (h1 - l1) * 0.3) and
            (c0 > o0) and
            (c0 > (o2 + c2) / 2.0)
        )
        if ea:
            patrones_detectados_dict['Estrella del Amanecer'] = True

        # Estrella de la Noche
        enoche = (
            (c2 > o2) and
            (abs(c1 - o1) <= (h1 - l1) * 0.3) and
            (c0 < o0) and
            (c0 < (o2 + c2) / 2.0)
        )
        if enoche:
            patrones_detectados_dict['Estrella de la Noche'] = True

    # -------------------- Martillo / Colgado / Invertidos (2+1 confirmación) --------------------
    if len(df) >= 5:
        c1, o1, h1, l1 = df['close'].iloc[-2], df['open'].iloc[-2], df['high'].iloc[-2], df['low'].iloc[-2]
        c0, o0 = df['close'].iloc[-1], df['open'].iloc[-1]

        cuerpo = abs(c1 - o1)
        if cuerpo > 0:
            mecha_sup = h1 - max(c1, o1)
            mecha_inf = min(c1, o1) - l1

            # Martillo (confirmación alcista)
            if (mecha_inf >= cuerpo * 2) and (mecha_sup <= cuerpo * 0.3) and (c0 > o0):
                patrones_detectados_dict['Martillo'] = True

            # Martillo Invertido (confirmación alcista)
            if (mecha_sup >= cuerpo * 2) and (mecha_inf <= cuerpo * 0.3) and (c0 > o0):
                patrones_detectados_dict['Martillo Invertido'] = True

            # Hombre Colgado (confirmación bajista)
            if (mecha_inf >= cuerpo * 2) and (mecha_sup <= cuerpo * 0.3) and (c1 < o1) and (c0 < o0):
                patrones_detectados_dict['Hombre Colgado'] = True

            # Estrella Fugaz (confirmación bajista)
            if (mecha_sup >= cuerpo * 2) and (mecha_inf <= cuerpo * 0.3) and (c1 < o1) and (c0 < o0):
                patrones_detectados_dict['Estrella Fugaz'] = True

    # -------------------- Hombro-Cabeza-Hombro (7 velas) --------------------
    if len(df) >= 7:
        highs = df['high'].iloc[-7:].values
        lows  = df['low'].iloc[-7:].values
        o     = df['open'].iloc[-7:].values
        c     = df['close'].iloc[-7:].values

        # HCH
        i_cabeza = np.argmax(highs)
        h1, h2, h3, cabeza, h4, h5, h6 = highs
        hombros_simetricos = (abs(h1 - h6) / max(h1, h6, 1e-6) < 0.15) and (abs(h2 - h5) / max(h2, h5, 1e-6) < 0.15)
        cuerpo_cabeza = abs(c[3] - o[3])
        mecha_sup_cabeza = highs[3] - max(c[3], o[3])
        mecha_inf_cabeza = min(c[3], o[3]) - lows[3]
        cabeza_definida = (cuerpo_cabeza > 0) and (mecha_sup_cabeza < cuerpo_cabeza * 1.5) and (mecha_inf_cabeza < cuerpo_cabeza * 1.5)
        if (i_cabeza == 3) and hombros_simetricos and (cabeza > h2) and (h4 < cabeza) and cabeza_definida:
            patrones_detectados_dict['Hombro Cabeza Hombro'] = True

        # HCH Invertido
        i_cabeza_inv = np.argmin(lows)
        l1, l2, l3, cabeza_inv, l4, l5, l6 = lows
        hombros_simetricos_inv = (abs(l1 - l6) / max(l1, l6, 1e-6) < 0.15) and (abs(l2 - l5) / max(l2, l5, 1e-6) < 0.15)
        if (i_cabeza_inv == 3) and hombros_simetricos_inv and (cabeza_inv < l2) and (l4 > cabeza_inv):
            patrones_detectados_dict['Hombro Cabeza Hombro Invertido'] = True

    # -------------------- Banderas (rolling window) --------------------
    if len(df) >= window:
        bandera_alcista = (df['high'] > df['high'].rolling(window).max().shift(1)) & \
                          (df['low']  > df['low'].rolling(window).min().shift(1))  & \
                          (df['close'] > df['open'])
        if bool(bandera_alcista.iloc[-1]):
            patrones_detectados_dict['Bandera Alcista'] = True

        bandera_bajista = (df['low']  < df['low'].rolling(window).min().shift(1)) & \
                          (df['high'] < df['high'].rolling(window).max().shift(1)) & \
                          (df['close'] < df['open'])
        if bool(bandera_bajista.iloc[-1]):
            patrones_detectados_dict['Bandera Bajista'] = True

    # --- Normaliza a lista de (start, end, nombre) y resuelve conflictos por prioridad ---
    patrones_encontrados = [(len(df) - window, len(df), nombre) for nombre in patrones_detectados_dict.keys()]

    patrones_filtrados = []
    for start1, end1, patron1 in patrones_encontrados:
        solapa = False
        for j, (start2, end2, patron2) in enumerate(patrones_filtrados):
            if max(start1, start2) <= min(end1, end2):  # hay solape
                if PRIORIDAD_PATRONES.get(patron1, 0) > PRIORIDAD_PATRONES.get(patron2, 0):
                    patrones_filtrados[j] = (start1, end1, patron1)
                solapa = True
                break
        if not solapa:
            patrones_filtrados.append((start1, end1, patron1))

    return patrones_filtrados

# Función para predicción con ARIMA
#@profile
def predecir_arima(df, temporalidad, symbol, steps=5):

    # Mapeo actualizado de temporalidades
    mapeo_temporalidades = {
        '1min': 'min',
        '5min': '5min',
        '15min': '15min',
        '30min': '30min',
        '1hour': 'h',
        '4hour': '4h',
        '1day': 'B', #B para dias laborales a excepcion de fines de semana, incluye festivos
        '1week': 'W-MON'
    }

    dias_festivos = [
        '2024-01-01',  # Año Nuevo
        '2024-12-25',  # Navidad
        '2024-07-04',  # Día de la Independencia de EE.UU.
        '2024-11-28',  # Día de Acción de Gracias
    ]

    dias_festivos = pd.to_datetime(dias_festivos)

    # Crear un CustomBusinessDay con días festivos
    custom_bday = CustomBusinessDay(holidays=dias_festivos)

    # Verificar que haya suficientes datos
    if len(df) < 30:
        logger.info(f"Datos insuficientes para ARIMA. activo: {df['Activo']}, temporalidad {temporalidad}")
        return None

    # Eliminar valores NaN
    series = df['close'].dropna()

    # Convertir columnas de tipo object a tipos numéricos
    df = df.infer_objects(copy=False)

    # Convertir el índice a datetime y eliminar duplicados
    if not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(df.index, errors='coerce')

    df = df[df.index.notna()]
    df = df.sort_index()
    df = df[~df.index.duplicated(keep='first')]
    df.sort_index(inplace=True)

    # Asignar frecuencia al índice
    freq = mapeo_temporalidades.get(temporalidad, 'min')
    try:
        if freq == "B":
            df = df.asfreq(custom_bday)  # Establecer frecuencia diaria
        else:
            #df.index = df.index.round('s')
            df = df.asfreq(freq)

    except Exception as e:
        logger.info(f"Error al establecer la frecuencia '{freq}': {e}")
        return None

    # Convertir a tipos numéricos e interpolar
    df = df.infer_objects(copy=False)
    try:
        # Convertir columnas específicas a tipo numérico
        for col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')

        # Interpolar después de la conversión
        df.interpolate(method='linear', inplace=True)
    except Exception as e:
        logger.info(f"Error al interpolar datos: {e}")
        return None

    # Verificar datos después de interpolar
    series = df['close'].dropna()
    if len(series) < 30:
        logger.info(f"Datos insuficientes después de limpiar la serie. Activo:{symbol}, Temporalidad:{temporalidad}")
        return None
    
    if series.isnull().any():
        logger.info(f"Valores NaN detectados en la serie después de la limpieza. Activo: {symbol}, Temporalidad: {temporalidad}")
        return None

    if np.std(series) == 0 or series.isna().any():
        logger.info("Datos sin variabilidad suficiente o con valores NaN.")
        return None

    try:
        # Ajustar el modelo ARIMA
        model = ARIMA(series, order=(1, 1, 0), trend='n')
        model_fit = model.fit()

        # Predecir los próximos 'steps' valores
        forecast = model_fit.forecast(steps=steps)

        return forecast.tolist()
    except Exception as e:
        logger.info(f"Error al ajustar ARIMA: {e}")
        return None

# Función para simulación de Monte Carlo
#@profile
def simulacion_monte_carlo(df, temporalidad, num_simulaciones=100, num_dias=5, seed=None):
    # Validar temporalidad
    temporalidades_no_validas = ['1min', '5min', '15min', '30min', '1hour', '4hour']
    if temporalidad in temporalidades_no_validas:
        return 50, 50  # Valores neutros para evitar problemas

    # Validar el DataFrame
    if df.empty or 'close' not in df.columns or len(df) < 2:
        logger.info("Datos insuficientes para la simulación de Monte Carlo.")
        return 50, 50

    # Filtrar valores no válidos
    df = df.dropna(subset=['close'])
    if (df['close'] <= 0).any():
        logger.info("Datos de cierre contienen valores no válidos (cero o negativos).")
        return 50, 50

    # Configurar el seed para reproducibilidad
    if seed is not None:
        np.random.seed(seed)

    # Calcular log returns
    log_returns = np.log(1 + df['close'].pct_change())
    log_returns = log_returns.dropna()  # Eliminar valores NaN resultantes de pct_change

    # Manejar valores extremos en log_returns
    log_returns = log_returns.clip(lower=-1, upper=1)

    media = log_returns.mean()
    desviacion = log_returns.std()

    # Limitar la desviación estándar para evitar valores extremos
    desviacion = min(desviacion, 1)

    # Configurar simulaciones
    simulaciones = np.zeros((num_dias, num_simulaciones))
    simulaciones[0] = df['close'].iloc[-1]

    # Ejecutar simulaciones
    for t in range(1, num_dias):
        random_shocks = np.random.standard_normal(num_simulaciones)
        random_shocks = np.clip(random_shocks, -10, 10)  # Limitar valores extremos
        simulaciones[t] = simulaciones[t-1] * np.exp(
            np.clip((media - 0.5 * desviacion**2) + desviacion * random_shocks, -10, 10)
        )

    # Calcular probabilidades
    probabilidad_alza = np.mean(simulaciones[-1] > simulaciones[0])
    probabilidad_baja = 1 - probabilidad_alza

    return probabilidad_alza * 100, probabilidad_baja * 100

# Predicción de precios futuros con media móvil
#@profile
def predecir_media_movil(df, window, steps=5):
    if len(df) < window:
        logger.info("No hay suficientes datos para realizar predicción con media móvil.")
        return None
    prediccion = df['close'].rolling(window=window).mean().iloc[-1]
    return [prediccion] * steps  # Repetir el valor predicho para el horizonte de `steps` días

# Funciones para determinar señales de compra
#@profile
def es_compra_arima(precio_actual, arima_prediccion, probabilidad_general, zona_no_trading):
    return precio_actual < arima_prediccion and probabilidad_general > 53 and not zona_no_trading

#@profile
def es_compra_media_movil(precio_actual, media_movil_prediccion, probabilidad_general, zona_no_trading):
    return precio_actual < media_movil_prediccion and probabilidad_general > 53 and not zona_no_trading

#@profile
def es_compra_arima_media_movil(precio_actual, arima_prediccion, media_movil_prediccion, probabilidad_general, zona_no_trading):
    return precio_actual < arima_prediccion and precio_actual < media_movil_prediccion and probabilidad_general > 53 and not zona_no_trading

#@profile
def es_compra_fuerte(probabilidad_alza, patrones_detectados, zona_sobreventa, probabilidad_general, zona_no_trading):
     return (
        (probabilidad_alza > 60 or zona_sobreventa or probabilidad_general > 53) and
        not zona_no_trading and (
            'Martillo' in patrones_detectados or
            'Martillo Invertido' in patrones_detectados or
            'Envolvente Alcista' in patrones_detectados or
            'Bandera Alcista' in patrones_detectados or
            'Estrella del Amanecer' in patrones_detectados or
            'Tres Soldados Blancos' in patrones_detectados or
            'Pinzas de Suelo' in patrones_detectados or
            'Hombro Cabeza Hombro Invertido' in patrones_detectados
        )
    )

# Funciones para determinar señales de venta
#@profile
def es_venta_arima(precio_actual, arima_prediccion, probabilidad_general, zona_no_trading):
    return precio_actual > arima_prediccion and probabilidad_general < 47 and not zona_no_trading

#@profile
def es_venta_media_movil(precio_actual, media_movil_prediccion, probabilidad_general, zona_no_trading):
    return precio_actual > media_movil_prediccion and probabilidad_general < 47 and not zona_no_trading

#@profile
def es_venta_arima_media_movil(precio_actual, arima_prediccion, media_movil_prediccion, probabilidad_general, zona_no_trading):
    return precio_actual > arima_prediccion and precio_actual > media_movil_prediccion and probabilidad_general < 47 and not zona_no_trading

#@profile
def es_venta_fuerte(probabilidad_baja, patrones_detectados, zona_sobrecompra, probabilidad_general, zona_no_trading):
    return (
        (probabilidad_baja > 60 or zona_sobrecompra or probabilidad_general < 47) and
        not zona_no_trading and (
            'Hombre Colgado' in patrones_detectados or
            'Envolvente Bajista' in patrones_detectados or
            'Bandera Bajista' in patrones_detectados or
            'Harami Bajista' in patrones_detectados or
            'Estrella de la Noche' in patrones_detectados or
            'Tres Cuervos Negros' in patrones_detectados or
            'Estrella Fugaz' in patrones_detectados or
            'Pinzas de Techo' in patrones_detectados or
            'Hombro Cabeza Hombro' in patrones_detectados
        )
    )

# Función principal para determinar el tipo de operación basado en las señales
#@profile
def determinar_tipo_operacion(precio_actual, arima_prediccion, media_movil_prediccion, probabilidad_alza, probabilidad_baja,
                              patrones_detectados, zona_sobreventa, zona_sobrecompra, probabilidad_general, zona_no_trading):
    
    if arima_prediccion and media_movil_prediccion:
        # Combinaciones de compra
        if es_compra_arima_media_movil(precio_actual, arima_prediccion, media_movil_prediccion, probabilidad_general, zona_no_trading):
            return "Compra Predicha con ARIMA y Media Movil"
        elif es_compra_arima(precio_actual, arima_prediccion, probabilidad_general, zona_no_trading):
            return "Compra Predicha con ARIMA"
        elif es_compra_media_movil(precio_actual, media_movil_prediccion, probabilidad_general, zona_no_trading):
            return "Compra Predicha con Media Movil"
        elif es_compra_fuerte(probabilidad_alza, patrones_detectados, zona_sobreventa, probabilidad_general, zona_no_trading):
            return "Compra Fuerte"
        elif probabilidad_general > 53 and not zona_no_trading:
            return "Compra"
        
        # Combinaciones de venta
        if es_venta_arima_media_movil(precio_actual, arima_prediccion, media_movil_prediccion, probabilidad_general, zona_no_trading):
            return "Venta Predicha con ARIMA y Media Movil"
        elif es_venta_arima(precio_actual, arima_prediccion, probabilidad_general, zona_no_trading):
            return "Venta Predicha con ARIMA"
        elif es_venta_media_movil(precio_actual, media_movil_prediccion, probabilidad_general, zona_no_trading):
            return "Venta Predicha con Media Movil"
        elif es_venta_fuerte(probabilidad_baja, patrones_detectados, zona_sobrecompra, probabilidad_general, zona_no_trading):
            return "Venta Fuerte"
        elif probabilidad_general < 47 and not zona_no_trading:
            return "Venta"

    return "Neutral"  # Si no se cumplen otras condiciones, se retorna 'Neutral'

@njit
#@profile
def calcular_tr(high, low, close):
    """
    Calcula el True Range (TR) para un conjunto de precios de manera eficiente con Numba.
    """
    n = len(high)
    tr = np.empty(n, dtype=np.float64)
    for i in range(n):
        if i == 0:
            tr[i] = high[i] - low[i]
        else:
            tr[i] = max(
                high[i] - low[i],
                np.abs(high[i] - close[i - 1]),
                np.abs(low[i] - close[i - 1])
            )
    return tr

@njit
#@profile
def filtrar_niveles_numba(niveles, tolerancia):
    """
    Filtra niveles eliminando duplicados dentro de la tolerancia usando Numba.
    """
    niveles_filtrados = [niveles[0]]
    for nivel in niveles[1:]:
        es_valido = True
        for nf in niveles_filtrados:
            if abs(nivel - nf) <= tolerancia:
                es_valido = False
                break
        if es_valido:
            niveles_filtrados.append(nivel)
    return niveles_filtrados

#@profile
def calcular_soportes_resistencias(df, window, atr_multiplier, precio_actual, min_levels=5, symbol='', temporalidad=''):

    if len(df) < window:
        raise ValueError(f"El DataFrame tiene menos filas ({len(df)}) que el tamaño de ventana ({window}).")
    
    order = max(1, window // 2)

    # Detectar máximos y mínimos locales
    min_indices = argrelextrema(df['low'].values, np.less, order=order)[0]
    max_indices = argrelextrema(df['high'].values, np.greater, order=order)[0]

    soportes = df['low'].iloc[min_indices].tolist()
    resistencias = df['high'].iloc[max_indices].tolist()

    atr_mean = df['atr'].mean() if 'atr' in df.columns else 0
    tolerancia = atr_multiplier * atr_mean
    
    # Filtrar niveles cercanos
    if soportes:
        soportes_filtrados = filtrar_niveles_numba(soportes, tolerancia)
    else: 
        soportes_filtrados = []
    
    if resistencias:
        resistencias_filtradas = filtrar_niveles_numba(resistencias, tolerancia)
    else:
        resistencias_filtradas = []

    # Validación para que los soportes sean menores que el precio actual y las resistencias mayores
    soportes_filtrados = [s for s in soportes_filtrados if s < precio_actual]
    resistencias_filtradas = [r for r in resistencias_filtradas if r > precio_actual]
    
    # Ajustar el `atr_multiplier` si no se cumplen los niveles mínimos
    while (len(soportes_filtrados) < min_levels or len(resistencias_filtradas) < min_levels) and atr_multiplier > 0.1:
        atr_multiplier -= 0.1
        tolerancia = atr_multiplier * atr_mean
        # Filtrar nuevamente con el nuevo valor de tolerancia
        if soportes:
            soportes_filtrados = set(filtrar_niveles_numba(soportes, tolerancia))
        else:
            soportes_filtrados = set()
        
        if resistencias:
            resistencias_filtradas = set(filtrar_niveles_numba(resistencias, tolerancia))
        else:
            resistencias_filtradas = set()

        # Reaplicar la validación respecto al precio actual
        soportes_filtrados = [s for s in soportes_filtrados if s < precio_actual]
        resistencias_filtradas = [r for r in resistencias_filtradas if r > precio_actual]

    return list(soportes_filtrados), list(resistencias_filtradas)
        
#@profile
def calcular_soportes_resistencias_para_window(window, df, precio_actual, min_levels, symbol, temporalidad):
    """
    Calcula soportes y resistencias para un valor de ventana específico.
    """
    soportes, resistencias = calcular_soportes_resistencias(
        df, window, atr_multiplier=1.5, precio_actual=precio_actual, min_levels=min_levels, symbol=symbol, temporalidad=temporalidad
    )
    return soportes, resistencias

#@profile
def _clean_levels(L):
    if not L: return []
    out=[]
    for v in L:
        vv = _tofloat(v)
        if _finite(vv):
            out.append(vv)
    return out

#@profile
def ajustar_window_dinamico_optimizado(
    df: pd.DataFrame,
    symbol: str,
    temporalidad: str,
    precio_actual: float,
    *,
    calc_windows: dict[str, int] | None = None,   # 👈 nuevo
    max_incremento: int = 5,
    min_factor: int = 2,
    max_factor: int = 5,
    min_levels: int = 2,
    n_jobs: int = -1,
):
    # Obtener la ventana inicial
    if calc_windows is not None:
        calc_map = _norm_windows(calc_windows, DEFAULT_CALC_WINDOWS)
        window = min(definir_window(temporalidad, overrides=calc_map), len(df))
    else:
        window = min(definir_window(temporalidad, None), len(df))
    
    max_window = min(window * min_factor, len(df))
    increment = max_incremento

    if len(df) < window:
        raise ValueError(
            f"El DataFrame {symbol} en {temporalidad} tiene menos filas ({len(df)}) que el tamaño mínimo de ventana ({window})."
        )

    # Calcular TR y ATR (hacerlo una vez para evitar cálculos repetidos)
    df['tr'] = calcular_tr(df['high'].values, df['low'].values, df['close'].values)
    df['atr'] = df['tr'].rolling(window).mean()

    # Inicializar variables
    soportes_dinamicos, resistencias_dinamicas = set(), set()
    niveles_suficientes = False

    window_ajustado = window
    min_factor_temporal = 1

    # Determinar si usar paralelización según el tamaño de los datos
    use_parallel = len(df) > 500 and n_jobs != 1
    backend_options = {'n_jobs': n_jobs}

    while not niveles_suficientes:
        if min_factor_temporal > max_factor:
            logger.info(
                f"min_factor alcanzó el límite max_factor ({max_factor}) para {symbol} en {temporalidad}."
            )
            break

        # Verificar si la ventana ajustada ya alcanzó su límite
        if window_ajustado > max_window:
            logger.info(f"Ventana ajustada alcanzó el límite máximo permitido ({max_window}) para {symbol} en {temporalidad}.")
            break

        # Función para calcular soportes y resistencias
        #@profile
        def calcular_soportes_resistencias():
            return calcular_soportes_resistencias_para_window(
                window_ajustado, df, precio_actual, min_levels, symbol, temporalidad
            )

        # Usar paralelización o ejecución directa
        if use_parallel:
            with parallel_backend('loky', **backend_options):
                resultados = Parallel()(
                    delayed(calcular_soportes_resistencias)()
                    for _ in range(1)
                )
        else:
            resultados = [calcular_soportes_resistencias()]

        # Procesar resultados
        for soportes, resistencias in resultados:
            soportes_dinamicos.update(soportes)
            resistencias_dinamicas.update(resistencias)

        # Verificar si se alcanzaron niveles suficientes
        if len(soportes_dinamicos) >= min_levels and len(resistencias_dinamicas) >= min_levels:
            niveles_suficientes = True
            #logger.info(f"{symbol}-{temporalidad}: Niveles suficientes alcanzados en ventana {window_ajustado}.")
            break
        else:
            # Incrementar dinámicamente el min_factor si no se alcanzaron niveles suficientes
            min_factor += 1
            min_factor_temporal += 1
            max_window = min(len(df), window * min_factor_temporal)
            window_ajustado = min(window + increment * min_factor, max_window)

    # Ordenar y eliminar duplicados finales
    soportes_dinamicos = sorted(soportes_dinamicos)
    resistencias_dinamicas = sorted(resistencias_dinamicas, reverse=True)

    return df, soportes_dinamicos, resistencias_dinamicas

#@profile
def filtrar_por_distancia(niveles, atr, precio_actual, max_distancia=1.5):
    if not niveles:
        return []

    # Ordenar niveles por cercanía al precio actual
    niveles = sorted(niveles, key=lambda nivel: abs(precio_actual - nivel))

    # Filtrar niveles estrictamente dentro de la distancia máxima
    niveles_filtrados = [nivel for nivel in niveles if abs(precio_actual - nivel) <= atr * max_distancia]

    return niveles_filtrados

#@profile
def contar_toques(nivel, precios, umbral=0.01):
    return sum(abs(precios - nivel) / nivel <= umbral)

#@profile
def unificar_niveles(cache, symbol):
    # Verificar si el símbolo existe en el caché
    if symbol not in cache:
        raise KeyError(f"El símbolo {symbol} no existe en el caché.")
    
    # Inicializar conjuntos para evitar duplicados
    soportes_unificados = set()
    resistencias_unificadas = set()
    
    # Recorrer cada temporalidad del símbolo
    for temporalidad, datos in cache[symbol].items():
        if isinstance(datos, dict):  # Asegurarse de que contiene soportes/resistencias
            soportes = datos.get('soportes', [])
            resistencias = datos.get('resistencias', [])
            
            # Agregar soportes y resistencias al conjunto
            soportes_unificados.update(soportes)
            resistencias_unificadas.update(resistencias)
    
    # Asignar los niveles unificados al símbolo en el caché
    cache[symbol]['soportes'] = sorted(soportes_unificados)
    cache[symbol]['resistencias'] = sorted(resistencias_unificadas)
    
    return cache

#@profile
def eliminar_niveles_redundantes(niveles, tolerancia):

    niveles_filtrados = []
    for nivel in niveles:
        if not niveles_filtrados or abs(nivel - niveles_filtrados[-1]) > tolerancia:
            niveles_filtrados.append(nivel)
    return niveles_filtrados

#@profile
def seleccionar_valor_cercano(niveles, precio_actual, atr=None, tolerancia_factor=0.1):
    
    if not niveles:
        return []

    # Calcular ATR adaptado a la temporalidad
    #tolerancia = max(atr * 0.5, precio_actual * 0.0001)  # Tolerancia dinámica ajustada
    tolerancia = atr if atr is not None else precio_actual * tolerancia_factor

    # Ordenar los niveles por cercanía al precio actual
    niveles_ordenados = sorted(niveles, key=lambda x: abs(x[0] - precio_actual))

    # Filtrar niveles dentro de la tolerancia
    niveles_cercanos = [nivel[0] for nivel in niveles_ordenados if abs(nivel[0] - precio_actual) <= tolerancia]

    # Eliminar redundancias entre niveles
    niveles_cercanos_filtrados = eliminar_niveles_redundantes(niveles_cercanos, tolerancia)

    return niveles_cercanos_filtrados


#@profile
def detectar_rango_zigzag(
    df,
    ventana_rebotes=140,    # Número de velas recientes a analizar
    tolerancia_pct=0.002,  # Tolerancia ajustada
    min_rebotes=3          # Rebotes mínimos para confirmar rango
):
    
    # Validar columnas y datos
    if 'close' not in df.columns or len(df) < ventana_rebotes:
        return {"es_rango_repetitivo": False, "estructura_tendencia": "indefinida", "rango_dinamico": None, "rebotes": 0}

    # Seleccionar las últimas 'ventana_rebotes' velas
    df = df.iloc[-ventana_rebotes:]
    precios = df['close'].values

    # Detectar extremos locales
    order = max(1, ventana_rebotes // 10)  # Ajustar dinamicamente el orden
    min_indices = argrelextrema(precios, np.less, order=order)[0]
    max_indices = argrelextrema(precios, np.greater, order=order)[0]

    # Unir y ordenar extremos
    zigzag_indices = np.sort(np.concatenate((min_indices, max_indices)))
    zigzag_precios = precios[zigzag_indices]

    # Validar rebotes
    if len(zigzag_precios) < 2 * min_rebotes:
        return {"es_rango_repetitivo": False, "estructura_tendencia": "indefinida", "rango_dinamico": None, "rebotes": 0}

    # Calcular el rango dinámico ajustado
    min_rango = zigzag_precios.min()
    max_rango = zigzag_precios.max()
    tolerancia = (max_rango - min_rango) * tolerancia_pct
    rango_min = round(min_rango - tolerancia, 5)
    rango_max = round(max_rango + tolerancia, 5)

    # Contar rebotes válidos dentro del rango ajustado
    rebotes_validos = sum(rango_min <= precio <= rango_max for precio in zigzag_precios)

    # Determinar si el patrón es repetitivo
    es_rango_repetitivo = rebotes_validos >= min_rebotes

    # Calcular la tendencia
    estructura_tendencia = "lateral"
    if es_rango_repetitivo:
        # Comparar extremos iniciales y finales
        dif_inicio_fin = zigzag_precios[-1] - zigzag_precios[0]
        if abs(dif_inicio_fin) < tolerancia:  # Sin cambio significativo
            estructura_tendencia = "lateral"
        elif dif_inicio_fin > 0:  # Patrón alcista
            estructura_tendencia = "alcista"
        elif dif_inicio_fin < 0:  # Patrón bajista
            estructura_tendencia = "bajista"

    return {
        "es_rango_repetitivo": es_rango_repetitivo,
        "estructura_tendencia": estructura_tendencia,
        "rango_dinamico": (float(round(rango_min, 5)), float(round(rango_max, 5))),
        "rebotes": rebotes_validos
    }

#@profile
def obtener_niveles_clave(df, soportes_dinamicos, resistencias_dinamicas, soportes_resistencias_cache, symbol, temporalidad_actual, umbral_atr=2.0, max_niveles=5):

    if symbol not in soportes_resistencias_cache or temporalidad_actual not in soportes_resistencias_cache[symbol]:
        raise KeyError(f"El símbolo {symbol} o la temporalidad {temporalidad_actual} no se encuentran en el caché.")

    #@profile
    def procesar_niveles_importantes(niveles):
        # Validar si es una tupla con un único elemento que contiene una lista
        if isinstance(niveles, tuple) and len(niveles) == 1 and isinstance(niveles[0], list):
            return [float(n) for n in niveles[0]]  # Convertir cada elemento a float

        # Validar si es simplemente una lista
        if isinstance(niveles, list):
            return [float(n) for n in niveles]  # Convertir cada elemento a float

        # Si no cumple con ninguno de los formatos, lanzar un error
        raise ValueError(f"El formato de los niveles no es el esperado. Tipo recibido: {type(niveles)}, contenido: {niveles}")

    #@profile
    def aplanar_niveles(niveles):
        if isinstance(niveles, (list, tuple, np.ndarray)):  # Incluye numpy.ndarray
            return [nivel for sublist in niveles for nivel in (sublist if isinstance(sublist, (list, tuple, np.ndarray)) else [sublist])]
        elif isinstance(niveles, (float, np.float64)):  # Es un único valor
            return [niveles]
        else:
            raise TypeError(f"Formato inesperado de niveles: {type(niveles)}")
        
    soportes = sorted(set(soportes_dinamicos), reverse=True)
    resistencias = sorted(set(resistencias_dinamicas))

    cache_actualizado = unificar_niveles(soportes_resistencias_cache, symbol)
    soportes_cache = sorted(cache_actualizado[symbol]['soportes'], reverse=True)
    resistencias_cache = sorted(cache_actualizado[symbol]['resistencias'])

    if not soportes:
        logger.info(f"Advertencia: No se encontraron soportes para {symbol} en {temporalidad_actual}.")
        soportes = []
    if not resistencias:
        logger.info(f"Advertencia: No se encontraron resistencias para {symbol} en {temporalidad_actual}.")
        resistencias = []

    # Obtener el ATR y el precio actual
    atr = df['atr'].iloc[-1]
    precio_actual = df['close'].iloc[-1]
    umbral = atr * umbral_atr

    # Filtrar soportes y resistencias cercanos al precio actual
    soportes_cercanos = [s for s in soportes if abs(precio_actual - s) <= umbral and s < precio_actual]
    resistencias_cercanas = [r for r in resistencias if abs(precio_actual - r) <= umbral and r > precio_actual]

    # Si no hay suficientes datos cercanos, expandir búsqueda
    if len(soportes_cercanos) < max_niveles:
        soportes_cercanos.extend([s for s in soportes if s not in soportes_cercanos])

    if len(resistencias_cercanas) < max_niveles:
        resistencias_cercanas.extend([r for r in resistencias if r not in resistencias_cercanas])

    soportes_cercanos = sorted(list(aplanar_niveles(soportes_cercanos)), reverse=True)  # Ordenar de mayor a menor
    resistencias_cercanas = sorted(list(aplanar_niveles(resistencias_cercanas)))       # Ordenar de menor a mayor

    # Confirmar soportes y resistencias con más toques
    soportes_confirmados = [(s, contar_toques(s, df['low'])) for s in soportes_cache]
    resistencias_confirmadas = [(r, contar_toques(r, df['high'])) for r in resistencias_cache]

    # Mezclar soportes y resistencias en una sola lista
    niveles_combinados = soportes_confirmados + resistencias_confirmadas

    # Ordenar en orden descendente por el número de toques
    niveles_ordenados_toques = sorted(niveles_combinados, key=lambda x: x[1], reverse=True)

    niveles_confirmados_orden_toques_all = [
    f'{{{nivel}, {toques}}}' for nivel, toques in niveles_ordenados_toques
    ]

    niveles_ordenados_nivel = sorted(niveles_combinados, key=lambda x: x[0], reverse=True)

    niveles_confirmados_orden_nivel_all = [
    f'{{{nivel}, {toques}}}' for nivel, toques in niveles_ordenados_nivel
    ]

    # Filtrar niveles_confirmados_orden_nivel_all para incluir solo 3 valores por encima y 3 por debajo del precio actual
    niveles_arriba = [nivel for nivel, _ in niveles_ordenados_nivel if nivel >= precio_actual][:3]
    niveles_abajo = [nivel for nivel, _ in niveles_ordenados_nivel if nivel <= precio_actual][-3:]

    # Combinar los niveles seleccionados y mantener el formato requerido
    niveles_reducidos = list(dict.fromkeys(niveles_abajo + niveles_arriba))
    niveles_confirmados_orden_nivel_reduced = [
        f'{{{nivel}, {toques}}}' for nivel, toques in niveles_ordenados_nivel if nivel in niveles_reducidos
    ]


    soportes_rebote = seleccionar_valor_cercano(soportes_confirmados, precio_actual, atr=atr)
    resistencias_rebote = seleccionar_valor_cercano(resistencias_confirmadas, precio_actual ,atr=atr)

    soportes_confirmados_orden = sorted(list(soportes_rebote), reverse=True)
    resistencias_confirmadas_orden = sorted(list(resistencias_rebote))

    # Inicializar valores predeterminados como NaN
    soporte_nivel_2, soporte_nivel_1 = np.nan, np.nan
    resistencia_nivel_1, resistencia_nivel_2 = np.nan, np.nan

    # Manejar caso único en soportes
    if len(soportes_cercanos) == 1:
        soporte_nivel_1 = soportes_cercanos[0]  # Único valor como soporte nivel 1
        soporte_nivel_2 = None        # No hay valor anterior
    elif len(soportes_cercanos) > 1:
       if soportes_cercanos[1] < soportes_cercanos[0]:  # Soporte Nivel 2 < Soporte Nivel 1
           soporte_nivel_2 = soportes_cercanos[1]
           soporte_nivel_1 = soportes_cercanos[0]

    # Manejar caso único en resistencias
    if len(resistencias_cercanas) == 1:
        resistencia_nivel_1 = resistencias_cercanas[0]  # Único valor como resistencia nivel 1
        resistencia_nivel_2 = None           # No hay valor siguiente
    elif len(resistencias_cercanas) > 1:
        if resistencias_cercanas[0] < resistencias_cercanas[1]:  # Resistencia Nivel 1 < Resistencia Nivel 2
          resistencia_nivel_1 = resistencias_cercanas[0]
          resistencia_nivel_2 = resistencias_cercanas[1]

    # Validar Soporte Nivel 1 < Precio Actual < Resistencia Nivel 1
    if soporte_nivel_1 >= precio_actual or precio_actual >= resistencia_nivel_1:
       soporte_nivel_2, soporte_nivel_1 = np.nan, np.nan
       resistencia_nivel_1, resistencia_nivel_2 = np.nan, np.nan

    # Definir el porcentaje residual (ejemplo: conservar 5% del precio actual)
    porcentaje_residual = 0.10  # 10.5%

    # Apalancamiento para compra
    if soporte_nivel_1 and precio_actual > soporte_nivel_1:
        perdida_relativa_nivel_1 = (precio_actual - soporte_nivel_1) / precio_actual
        apalancamiento_compra_nivel_1 = int((1 - porcentaje_residual) / perdida_relativa_nivel_1) if perdida_relativa_nivel_1 > 0 else 0
    else:
        apalancamiento_compra_nivel_1 = 0
    
    if soporte_nivel_2 and precio_actual > soporte_nivel_2:
        perdida_relativa_nivel_2 = (precio_actual - soporte_nivel_2) / precio_actual
        apalancamiento_compra_nivel_2 = int((1 - porcentaje_residual) / perdida_relativa_nivel_2) if perdida_relativa_nivel_2 > 0 else 0
    else:
        apalancamiento_compra_nivel_2 = 0
    
    # Apalancamiento para venta (proceso inverso)
    if resistencia_nivel_1 and precio_actual < resistencia_nivel_1:
        perdida_relativa_venta_nivel_1 = (resistencia_nivel_1 - precio_actual) / precio_actual
        apalancamiento_venta_nivel_1 = int((1 - porcentaje_residual) / perdida_relativa_venta_nivel_1) if perdida_relativa_venta_nivel_1 > 0 else 0
    else:
        apalancamiento_venta_nivel_1 = 0
    
    if resistencia_nivel_2 and precio_actual < resistencia_nivel_2:
        perdida_relativa_venta_nivel_2 = (resistencia_nivel_2 - precio_actual) / precio_actual
        apalancamiento_venta_nivel_2 = int((1 - porcentaje_residual) / perdida_relativa_venta_nivel_2) if perdida_relativa_venta_nivel_2 > 0 else 0
    else:
        apalancamiento_venta_nivel_2 = 0
    
    multiplicador = {
        "apalancamiento_compra_nivel_1": apalancamiento_compra_nivel_1,
        "apalancamiento_compra_nivel_2": apalancamiento_compra_nivel_2,
        "apalancamiento_venta_nivel_1": apalancamiento_venta_nivel_1,
        "apalancamiento_venta_nivel_2": apalancamiento_venta_nivel_2
    }

    # Usar filtrar_por_distancia para identificar niveles importantes
    niveles_importantes_soportes = filtrar_por_distancia(soportes, atr, precio_actual)
    niveles_importantes_resistencias = filtrar_por_distancia(resistencias, atr, precio_actual)

    return {
        "soporte_nivel_2": soporte_nivel_2,
        "soporte_nivel_1": soporte_nivel_1,
        "resistencia_nivel_1": resistencia_nivel_1,
        "resistencia_nivel_2": resistencia_nivel_2,
        "niveles_importantes_soportes": procesar_niveles_importantes(niveles_importantes_soportes),
        "niveles_importantes_resistencias": procesar_niveles_importantes(niveles_importantes_resistencias),
        "soportes_confirmados_orden": soportes_confirmados_orden,
        "resistencias_confirmadas_orden": resistencias_confirmadas_orden,
        "niveles_confirmados_orden_toques_all": niveles_confirmados_orden_toques_all,
        "niveles_confirmados_orden_nivel_all": niveles_confirmados_orden_nivel_all,
        "niveles_confirmados_orden_nivel_reduced": niveles_confirmados_orden_nivel_reduced,
        "multiplicador": multiplicador,
        "DataFrame Actualizado": df
    }

#@profile
def _finite(x) -> bool:
    try:
        return np.isfinite(float(x))
    except Exception:
        return False

#@profile
def _tofloat(x):
    try:
        v = float(x)
        return v if np.isfinite(v) else None
    except Exception:
        return None
    
#@profile
def calc_tp_sl_compra(entry, atr, mult=1.5):
    if not (_finite(entry) and _finite(atr)):
        return None, None
    tp = entry + atr * mult
    sl = entry - atr * mult
    if not (sl < entry < tp):
        return None, None
    return tp, sl

#@profile
def calc_tp_sl_venta(entry, atr, mult=1.5):
    if not (_finite(entry) and _finite(atr)):
        return None, None
    tp = entry - atr * mult
    sl = entry + atr * mult
    if not (tp < entry < sl):
        return None, None
    return tp, sl

#@profile
def calc_tp_sl_compra_asym(entry: float, atr: float, tp_mult: float, sl_mult: float):
    if not (_finite(entry) and _finite(atr)):
        return None, None
    tp = entry + atr * tp_mult
    sl = entry - atr * sl_mult
    if not (sl < entry < tp):
        return None, None
    return tp, sl

#@profile
def calc_tp_sl_venta_asym(entry: float, atr: float, tp_mult: float, sl_mult: float):
    if not (_finite(entry) and _finite(atr)):
        return None, None
    tp = entry - atr * tp_mult
    sl = entry + atr * sl_mult
    if not (tp < entry < sl):
        return None, None
    return tp, sl

# ─────────────────────────────────────────────────────────────────────────────
# Helpers para múltiples entradas con rango dinámico, niveles confirmados y RRR
# ─────────────────────────────────────────────────────────────────────────────

#@profile
def _finite(x) -> bool:
    try:
        return x is not None and math.isfinite(float(x))
    except Exception:
        return False

#@profile
def _rrr(entry: float, tp: float, sl: float, side: str) -> Optional[float]:
    """Risk-Reward Ratio."""
    if not (_finite(entry) and _finite(tp) and _finite(sl)):
        return None
    if side == "long":
        risk = entry - sl
        reward = tp - entry
    else:
        risk = sl - entry
        reward = entry - tp
    if risk <= 0 or reward <= 0:
        return None
    return reward / risk

#@profile
def _add_entry(
    entries: list[dict],
    *,
    side: str,                       # "long" | "short"
    entry: float,
    atr: float,
    mult_tp_sl,                      # float  ó  (tp_mult, sl_mult)
    make_tp_sl: Callable[..., tuple[Optional[float], Optional[float]]],
    basado_en: str,
    precio_actual: float,
    niveles: dict,
    rango_dinamico: Iterable[Optional[float]] = (None, None),
    min_rrr: float = 1.2
):
    """Calcula TP/SL, RRR y agrega la entrada si pasa validaciones."""
    if not (_finite(entry) and _finite(atr) and atr > 0):
        logging.info(" - DESCARTADA: entry/ATR no finitos")
        return

    # Soportar multiplicadores asimétricos (tp_mult, sl_mult)
    if isinstance(mult_tp_sl, (tuple, list)) and len(mult_tp_sl) == 2:
        tp_mult, sl_mult = float(mult_tp_sl[0]), float(mult_tp_sl[1])
        if side == "long":
            tp, sl = calc_tp_sl_compra_asym(entry, atr, tp_mult, sl_mult)
        else:
            tp, sl = calc_tp_sl_venta_asym(entry, atr, tp_mult, sl_mult)
    else:
        # Compat con tus calc_tp_sl_* (simétricas)
        tp, sl = make_tp_sl(entry, atr, mult_tp_sl)

    if not (_finite(tp) and _finite(sl)):
        logging.info(f" - DESCARTADA: tp/sl inválidos (entry={entry:.6f}, atr={atr:.6f})")
        return

    if side == "long" and not (sl < entry < tp):
        logging.info(" - DESCARTADA: no cumple sl<entry<tp (long)")
        return
    if side == "short" and not (tp < entry < sl):
        logging.info(" - DESCARTADA: no cumple tp<entry<sl (short)")
        return

    rrr = _rrr(entry, tp, sl, side)
    if rrr is None or rrr < min_rrr:
        logging.info(f" - DESCARTADA: RRR={rrr if rrr is not None else 'None'} < min_rrr={min_rrr}")
        return

    # Score: distancia al precio actual en unidades de ATR (menor = mejor)
    try:
        lo, hi = rango_dinamico
    except Exception:
        lo, hi = None, None
    score = abs(entry - (precio_actual or entry)) / (atr or 1e-9)
    if _finite(lo) and _finite(hi):
        # penaliza si “se sale” del rango en estrategias de rango
        if side == "long" and entry > hi: score += 1.0
        if side == "short" and entry < lo: score += 1.0

    entries.append({
        "side": side,
        "basado_en": basado_en,
        "precio_entrada": float(entry),
        "take_profit": float(tp),
        "stop_loss": float(sl),
        "rrr": float(rrr),
        "score": float(score),
        "meta": {
            "atr": float(atr),
            "precio_actual": float(precio_actual),
            "rango_dinamico": [lo, hi] if (_finite(lo) and _finite(hi)) else None,
            "niveles": {
                "s1": niveles.get("soporte_nivel_1"),
                "s2": niveles.get("soporte_nivel_2"),
                "r1": niveles.get("resistencia_nivel_1"),
                "r2": niveles.get("resistencia_nivel_2"),
            }
        }
    })
    logging.info(f" + AGREGADA {side.upper()} [{basado_en}] entry={entry:.6f} tp={tp:.6f} sl={sl:.6f} RRR={rrr:.3f} score={score:.3f}")


#@profile
def generar_entradas_multiples(
    *,
    precio_actual: float,
    ATR: float | None,
    niveles: dict,
    tipo_operacion: str,
    en_rango: dict,
    prob_general: float | None,
    bollinger_upper: float | None = None,
    bollinger_lower: float | None = None,
    señales_compra: set[str],
    señales_venta: set[str],
    # --- parámetros tunables (asimétricos por defecto) ---
    mult_mid=(1.8, 1.2),
    mult_pullback_s1=(2.0, 1.2),
    mult_pullback_s2=(2.2, 1.2),
    mult_pullback_r1=(2.0, 1.2),
    mult_pullback_r2=(2.2, 1.2),
    mult_breakout=(1.6, 1.1),
    mult_scale_hi=(1.6, 1.1),
    mult_scale_lo=(2.0, 1.3),
    breakout_offset_atr=0.2,
    scale_offset_atr=0.5,
    boll_offset_atr=0.1,
    min_rrr=1.2,
    # --- nuevos parámetros opcionales ---
    enable_breakout_retest=True,
    retest_offset_atr=0.2,          # distancia típica del pullback tras la ruptura
    enable_ladder=True,
    ladder_steps=2,                 # cuántas escalas a cada lado del nivel
    ladder_step_atr=0.25,           # separación entre escalas
    enable_range_mean_revert=True,  # mean-reversion adicional usando rango_dinamico
    range_pad_atr=0.15,             # al acercarse al borde del rango
    max_candidates=40,              # límite de propuestas (antes de ordenar)
    dedupe_tol_atr=0.05,            # entradas a <0.05*ATR se consideran duplicadas
):
    """
    Crea múltiples candidatos (long/short) usando niveles, rango, Bollinger y ATR,
    con ajustes adaptativos según prob_general, tendencia y ancho de Bollinger.
    Filtra por coherencia y RRR >= min_rrr. Ordena por score (menor=mejor).
    """
    entries: list[dict] = []

    logging.info("===== INPUT =====")
    logging.info(f"precio_actual={precio_actual:.6f}, ATR={ATR if ATR is not None else None}")
    logging.info(f"niveles: S1={niveles.get('soporte_nivel_1')}, S2={niveles.get('soporte_nivel_2')}, "
                 f"R1={niveles.get('resistencia_nivel_1')}, R2={niveles.get('resistencia_nivel_2')}")
    logging.info(f"tipo_operacion={tipo_operacion}, estructura={(en_rango or {}).get('estructura_tendencia')}, "
                 f"es_rango={bool((en_rango or {}).get('es_rango_repetitivo'))}")
    logging.info(f"rango_dinamico={(en_rango or {}).get('rango_dinamico')} prob_general={prob_general}")
    logging.info("=================")

    # Validaciones básicas
    if not (_finite(precio_actual) and ATR is not None and _finite(ATR) and ATR > 0):
        logging.info("Input inválido: sin precio o ATR.")
        return entries

    # --- desestructurar entradas base ---
    s1 = niveles.get("soporte_nivel_1")
    s2 = niveles.get("soporte_nivel_2")
    r1 = niveles.get("resistencia_nivel_1")
    r2 = niveles.get("resistencia_nivel_2")

    estructura = (en_rango or {}).get("estructura_tendencia", "indefinida")
    es_rango = bool((en_rango or {}).get("es_rango_repetitivo"))
    rango_dinamico = (en_rango or {}).get("rango_dinamico") or (None, None)
    rango_low, rango_high = rango_dinamico

    sesgo_long = (tipo_operacion in señales_compra) or (tipo_operacion == "Neutral" and estructura in ("alcista", "indefinida"))
    sesgo_short = (tipo_operacion in señales_venta)  or (tipo_operacion == "Neutral" and estructura == "bajista")

    logging.info(f"sesgo_long={sesgo_long}, sesgo_short={sesgo_short}, min_rrr={min_rrr}")

    midpoint = ((r1 + s1) / 2.0) if _finite(r1) and _finite(s1) else precio_actual

    # ====== ADAPTADORES DE CONTEXTO ======
    # 1) Factor por prob_general (0..100). 50 = neutro.
    def _prob_factor(p: float | None) -> float:
        if p is None or not _finite(p): return 1.0
        # mapear [20..80] -> [0.9..1.1], saturando fuera
        p = max(0.0, min(100.0, p))
        if p < 50:
            return max(0.9, 0.9 + (p - 50) * 0.004)  # 50->0.9, 0->≈0.7 (cap 0.9)
        else:
            return min(1.1, 0.9 + (p - 50) * 0.004)  # 50->0.9+0 =0.9? ajustemos:
    # corrección: neutro=1.0
    def _prob_factor(p: float | None) -> float:
        if p is None or not _finite(p): return 1.0
        p = max(0.0, min(100.0, p))
        # 50->1.0; 80->1.1; 20->0.9 (lineal)
        return 0.9 + (p - 20.0) * (0.2 / 60.0)

    # 2) Factor por estructura
    def _trend_factor(estr: str, side: str) -> float:
        estr = (estr or "indefinida").lower()
        if estr == "alcista":
            return 1.08 if side == "long" else 0.96
        if estr == "bajista":
            return 1.08 if side == "short" else 0.96
        return 1.0  # indefinida

    # 3) Factor por régimen de volatilidad usando ancho de Bollinger
    def _vol_factor(bu: float | None, bl: float | None, atr: float) -> float:
        if _finite(bu) and _finite(bl) and atr > 0:
            width_atr = max(0.1, (bu - bl) / atr)
            # ancho ~1.0-2.5 ATR: 1.0->0.95 (aprieta), 2.5->1.10 (afloja)
            width_atr = max(0.5, min(3.0, width_atr))
            return 0.85 + (width_atr - 0.5) * (0.35 / 2.5)  # ~0.85..1.20
        return 1.0

    def _adapt_mult(base: tuple[float, float], side: str) -> tuple[float, float]:
        """Ajusta (tp_mult, sl_mult) con contexto."""
        tp, sl = base
        f_prob = _prob_factor(prob_general)
        f_trend = _trend_factor(estructura, side)
        f_vol = _vol_factor(bollinger_upper, bollinger_lower, ATR)
        # Heurística: TP escala con prob y tendencia, SL inverso (protege en baja convicción).
        tp_adj = tp * f_prob * f_trend * f_vol
        sl_adj = sl * (2.0 - f_prob) * (2.0 - min(1.15, f_trend))  # cap para no disparar SL
        # Limites razonables
        tp_adj = max(0.8, min(3.5, tp_adj))
        sl_adj = max(0.8, min(2.0, sl_adj))
        return (tp_adj, sl_adj)

    # ====== ÚTILES ======
    def _near(a: float, b: float, tol: float) -> bool:
        return abs(a - b) <= tol

    def _try_add(side: str, entry: float, mult_base: tuple[float, float], basado_en: str):
        """Aplica adaptadores, crea y filtra por RRR, dedup y límites."""
        if not _finite(entry): 
            return
        # dedupe
        for e in entries:
            if e.get("side") == side and _near(e.get("precio_entrada", 0.0), entry, dedupe_tol_atr * ATR):
                return  # muy cercano a otro ya agregado

        mult_adj = _adapt_mult(mult_base, side)
        make = calc_tp_sl_compra if side == "long" else calc_tp_sl_venta
        _add_entry(entries, side=side, entry=entry, atr=ATR, mult_tp_sl=mult_adj,
                   make_tp_sl=make, basado_en=basado_en,
                   precio_actual=precio_actual, niveles=niveles, rango_dinamico=rango_dinamico,
                   min_rrr=min_rrr)

    # ====== ESTRATEGIAS BASE (tus originales) ======
    if sesgo_long:
        if _finite(s1):
            _try_add("long", s1, mult_pullback_s1, "pullback_S1")
        if _finite(s2):
            _try_add("long", s2, mult_pullback_s2, "pullback_S2")
        if _finite(r1):
            _try_add("long", r1 + breakout_offset_atr * ATR, mult_breakout, "breakout_R1")
        if _finite(midpoint):
            _try_add("long", midpoint, mult_mid, "midpoint")
            _try_add("long", midpoint - scale_offset_atr * ATR, mult_scale_lo, "scale_in_midpoint_-0.5ATR")
            _try_add("long", midpoint + scale_offset_atr * ATR, mult_scale_hi, "scale_in_midpoint_+0.5ATR")
        if es_rango and _finite(bollinger_lower):
            _try_add("long", bollinger_lower + boll_offset_atr * ATR, mult_mid, "bollinger_lower_reversion")

    if sesgo_short:
        if _finite(r1):
            _try_add("short", r1, mult_pullback_r1, "pullback_R1")
        if _finite(r2):
            _try_add("short", r2, mult_pullback_r2, "pullback_R2")
        if _finite(s1):
            _try_add("short", s1 - breakout_offset_atr * ATR, mult_breakout, "breakdown_S1")
        if _finite(midpoint):
            _try_add("short", midpoint, mult_mid, "midpoint")
            _try_add("short", midpoint + scale_offset_atr * ATR, mult_scale_lo, "scale_in_midpoint_+0.5ATR")
            _try_add("short", midpoint - scale_offset_atr * ATR, mult_scale_hi, "scale_in_midpoint_-0.5ATR")
        if es_rango and _finite(bollinger_upper):
            _try_add("short", bollinger_upper - boll_offset_atr * ATR, mult_mid, "bollinger_upper_reversion")

    # ====== NUEVA ESTRATEGIA 1: Breakout-Retest ======
    # Long: rompe R1, esperar pullback a (~R1 + retest_offset) para entrar mejor.
    # Short: rompe S1, esperar pullback a (~S1 - retest_offset).
    if enable_breakout_retest:
        if sesgo_long and _finite(r1):
            retest_long = r1 + retest_offset_atr * ATR
            _try_add("long", retest_long, mult_breakout, "breakout_R1_retest")
        if sesgo_short and _finite(s1):
            retest_short = s1 - retest_offset_atr * ATR
            _try_add("short", retest_short, mult_breakout, "breakdown_S1_retest")

    # ====== NUEVA ESTRATEGIA 2: Laddered Pullback alrededor de niveles ======
    # Genera pequeñas escalas ±k*ATR del nivel base para mejorar el precio promedio.
    if enable_ladder:
        def _ladder(side: str, level: float, base_mult: tuple[float, float], tag: str, dir_sign: int):
            # dir_sign: +1 para long (comprar más abajo), -1 para short (vender más arriba)
            if not _finite(level): 
                return
            for step in range(1, ladder_steps + 1):
                off = dir_sign * step * ladder_step_atr * ATR
                _try_add(side, level - off, base_mult, f"{tag}_ladder_{step}")

        if sesgo_long:
            if _finite(s1): _ladder("long", s1, mult_pullback_s1, "pullback_S1", +1)
            if _finite(s2): _ladder("long", s2, mult_pullback_s2, "pullback_S2", +1)
        if sesgo_short:
            if _finite(r1): _ladder("short", r1, mult_pullback_r1, "pullback_R1", -1)
            if _finite(r2): _ladder("short", r2, mult_pullback_r2, "pullback_R2", -1)

    # ====== NUEVA ESTRATEGIA 3: Mean-Reversion con rango_dinamico ======
    # Si hay rango, buscar reversión cerca de sus bordes (además de Bollinger).
    if enable_range_mean_revert and es_rango and _finite(rango_low) and _finite(rango_high):
        # Long cerca del borde inferior del rango
        if sesgo_long:
            e_low = rango_low + range_pad_atr * ATR
            _try_add("long", e_low, mult_mid, "range_lower_reversion")
        # Short cerca del borde superior del rango
        if sesgo_short:
            e_high = rango_high - range_pad_atr * ATR
            _try_add("short", e_high, mult_mid, "range_upper_reversion")

    # ====== LIMITE DE CANDIDATOS (por performance/ruido) ======
    if len(entries) > max_candidates:
        entries = entries[:max_candidates]

    # ====== ORDENACIÓN Y LOG ======
    logging.info("===== RESUMEN =====")
    logging.info(f"Intentos totales: {len(entries)} (antes de ordenar)")

    # Mejora de score: pondera RRR alto, cercanía a precio, y confluencia (señales/estructura)
    def _confluence_boost(e: dict) -> float:
        base = 0.0
        # bonus si basado en pullback a nivel fuerte
        if "pullback" in e.get("basado_en", ""): base -= 0.05
        if "retest"  in e.get("basado_en", ""): base -= 0.04
        if "range_"  in e.get("basado_en", ""): base -= 0.03
        if "bollinger" in e.get("basado_en", ""): base -= 0.02
        # cercanía al precio (prefiere no demasiado lejos)
        dist = abs(e.get("precio_entrada", 0.0) - precio_actual) / max(1e-9, ATR)
        base += min(0.3, 0.03 * dist)  # penaliza muy lejos
        # RRR alto mejor
        rrr = e.get("rrr", 1.0)
        base -= min(0.4, 0.06 * (rrr - min_rrr))  # recompensa RRR por encima del mínimo
        # ligero sesgo con estructura
        if estructura == "alcista" and e.get("side") == "long": base -= 0.02
        if estructura == "bajista" and e.get("side") == "short": base -= 0.02
        return base

    # Si ya tenías un "score" propio dentro de _add_entry, esto lo re-combina sin romper.
    for e in entries:
        e["score"] = float(e.get("score", 0.0)) + _confluence_boost(e)

    entries.sort(key=lambda e: e.get("score", 1e9))

    for i, e in enumerate(entries[:10], 1):
        logging.info(f"{i:02d}) {e['side'].upper()} {e['basado_en']} "
                     f"entry={e['precio_entrada']:.6f} tp={e['take_profit']:.6f} "
                     f"sl={e['stop_loss']:.6f} RRR={e['rrr']:.3f} score={e['score']:.3f}")

    return entries


# Función para calcular puntos de entrada ajustando las probabilidades
#@profile
def calcular_entradas(
    df,
    df_eventos,
    symbol: str,
    temporalidad: str,
    user_chat_id: str,
    *,
    calc_windows: dict[str, int] | None = None,
    cfg: dict | None = None,
):
    salida = {}
    try:
        estado_usuario = obtener_estado_usuario(user_chat_id)
        soportes_resistencias_cache = estado_usuario["soportes_resistencias_cache"]

        tf = _tf_backend(temporalidad)
        window = min(definir_window(tf, overrides=calc_windows), len(df))

        # --- Patrones ---
        patrones_detectados = {}
        resultados = detectar_patrones_confirmados_velas(df, window)
        for _, _, nombre in resultados:
            patrones_detectados[nombre] = True
        # print(f"Paso exitosamente la detección de patrones: {patrones_detectados}")

        # --- Predicciones/MC ---
        predicciones_arima = predecir_arima(df, tf, symbol)
        predicciones_media_movil = predecir_media_movil(df, window)

        probabilidad_alza, probabilidad_baja = simulacion_monte_carlo(
            df, tf, num_simulaciones=100, num_dias=5, seed=42
        )
        probabilidad_alza = probabilidad_alza if probabilidad_alza is not None else 50
        probabilidad_baja = probabilidad_baja if probabilidad_baja is not None else 50

        precio_actual = df["close"].iloc[-1]

        # --- Soportes/Resistencias dinámicos ---
        df, soportes_dinamicos, resistencias_dinamicas = ajustar_window_dinamico_optimizado(
            df,
            symbol,
            tf,
            precio_actual,
            calc_windows=calc_windows,
            max_incremento=5,
            min_factor=2,
            max_factor=8,
            min_levels=2,
            n_jobs=-1,
        )

        soportes_dinamicos = _clean_levels(soportes_dinamicos)
        resistencias_dinamicas = _clean_levels(resistencias_dinamicas)

        if symbol not in soportes_resistencias_cache:
            soportes_resistencias_cache[symbol] = {}

        if tf not in soportes_resistencias_cache[symbol]:
            soportes_resistencias_cache[symbol][tf] = {
                "soportes": soportes_dinamicos,
                "resistencias": resistencias_dinamicas,
            }
        else:
            s = soportes_resistencias_cache[symbol][tf]
            s["soportes"] = list(set(s["soportes"] + soportes_dinamicos))
            s["resistencias"] = list(set(s["resistencias"] + resistencias_dinamicas))

        niveles_clave = obtener_niveles_clave(
            df,
            soportes_dinamicos,
            resistencias_dinamicas,
            soportes_resistencias_cache,
            symbol,
            tf,
            umbral_atr=2.0,
            max_niveles=2,
        )

        try:
            en_rango = detectar_rango_zigzag(
                df, ventana_rebotes=140, tolerancia_pct=0.002, min_rebotes=3
            )
        except Exception:
            en_rango = {
                "es_rango_repetitivo": False,
                "estructura_tendencia": "indefinida",
                "rebotes": [],
                "rango_dinamico": [None, None],
            }

        ATR = _tofloat(df["ATR"].iloc[-1]) if "ATR" in df.columns else None

        # --- Prob. técnica y fundamental (usan cfg) ---
        probabilidad_tecnica = round(ajustar_probabilidad_tecnica(df, tf, window, cfg), 2)

        fecha_inicio = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
        fecha_fin = datetime.now().strftime("%Y-%m-%d")
        prob_funda = ajustar_probabilidad_fundamental(
            50, df_eventos, symbol, tf, fecha_inicio=fecha_inicio, fecha_fin=fecha_fin, cfg=cfg
        )
        probabilidad_fundamental = round(prob_funda if prob_funda is not None else 50, 2)

        # --- Prob. general (con pesos desde cfg.general) ---
        probabilidad_general = calcular_probabilidad_general(
            probabilidad_tecnica, probabilidad_fundamental, cfg
        )
        probabilidad_general = round(probabilidad_general if probabilidad_general is not None else 50, 2)

        # --- Zona no trading (condicionada por cfg.entrada.verificar_zona_no_trading) ---
        verificar_znt = True
        try:
            verificar_znt = bool((cfg or {}).get("entrada", {}).get("verificar_zona_no_trading", True))
        except Exception:
            verificar_znt = True

        zona_no_trading = verificar_zona_no_trading(df, window) if verificar_znt else False
        zona_sobreventa = verificar_zona_sobreventa(df, window)
        zona_sobrecompra = verificar_zona_sobrecompra(df, window)

        # --- Tipo de operación ---
        tipo_operacion = determinar_tipo_operacion(
            precio_actual,
            predicciones_arima[0] if predicciones_arima else None,
            predicciones_media_movil[0] if predicciones_media_movil else None,
            probabilidad_alza,
            probabilidad_baja,
            patrones_detectados,
            zona_sobreventa,
            zona_sobrecompra,
            probabilidad_general,
            zona_no_trading,
        )

        # Bollinger (último)
        bollinger_upper = _coerce_float_safe(salida.get("bollinger_upper")) or _coerce_float_safe(
            last_of(df, "bollinger_upper", default=None)
        )
        bollinger_lower = _coerce_float_safe(salida.get("bollinger_lower")) or _coerce_float_safe(
            last_of(df, "bollinger_lower", default=None)
        )

        # --- Entradas múltiples ---
        entradas_mult = generar_entradas_multiples(
            precio_actual=precio_actual,
            ATR=ATR,
            niveles=niveles_clave,
            tipo_operacion=tipo_operacion,
            en_rango=en_rango,
            prob_general=probabilidad_general,
            bollinger_upper=bollinger_upper,
            bollinger_lower=bollinger_lower,
            señales_compra=señales_compra,
            señales_venta=señales_venta,
        )

        # “legacy” (mejor entrada)
        best = entradas_mult[0] if entradas_mult else None
        if best:
            precio_entrada = best.get("precio_entrada")
            take_profit = best.get("take_profit")
            stop_loss = best.get("stop_loss")
        else:
            if tipo_operacion in señales_compra or (
                tipo_operacion == "Neutral" and en_rango["estructura_tendencia"] in ("alcista", "indefinida")
            ):
                precio_entrada = (
                    (niveles_clave["resistencia_nivel_1"] + niveles_clave["soporte_nivel_1"]) / 2
                    if niveles_clave["resistencia_nivel_1"] and niveles_clave["soporte_nivel_1"]
                    else precio_actual
                )
                take_profit, stop_loss = calc_tp_sl_compra(precio_entrada, ATR)
                if not (stop_loss and take_profit and (stop_loss < precio_entrada < take_profit)):
                    logger.warning(
                        f"Valores incorrectos en {symbol} temporalidad:{tf} (compra): SL={stop_loss}, Entrada={precio_entrada}, TP={take_profit}"
                    )
                    stop_loss, take_profit = np.nan, np.nan

            elif tipo_operacion in señales_venta or (
                tipo_operacion == "Neutral" and en_rango["estructura_tendencia"] == "bajista"
            ):
                precio_entrada = (
                    (niveles_clave["resistencia_nivel_1"] + niveles_clave["soporte_nivel_1"]) / 2
                    if niveles_clave["resistencia_nivel_1"] and niveles_clave["soporte_nivel_1"]
                    else precio_actual
                )
                take_profit, stop_loss = calc_tp_sl_venta(precio_entrada, ATR)
                if not (take_profit and stop_loss and (take_profit < precio_entrada < stop_loss)):
                    logger.warning(
                        f"Valores incorrectos en {symbol} temporalidad:{tf} (venta): TP={take_profit}, Entrada={precio_entrada}, SL={stop_loss}"
                    )
                    stop_loss, take_profit = np.nan, np.nan
            else:
                take_profit = None
                stop_loss = None

        # Cercanía a niveles
        #@profile
        def esta_cerca(precio, nivel, umbral_cercania=0.01):
            return False if nivel is None else abs(precio - nivel) / precio <= umbral_cercania

        cerca_de_soporte_resistencia = (
            "Cerca de Soporte Nivel 2"
            if esta_cerca(precio_actual, niveles_clave.get("soporte_nivel_2"))
            else "Cerca de Soporte Nivel 1"
            if esta_cerca(precio_actual, niveles_clave.get("soporte_nivel_1"))
            else "Cerca de Resistencia Nivel 1"
            if esta_cerca(precio_actual, niveles_clave.get("resistencia_nivel_1"))
            else "Cerca de Resistencia Nivel 2"
            if esta_cerca(precio_actual, niveles_clave.get("resistencia_nivel_2"))
            else "No Cerca"
        )

        # Flag oportunidad (respetando zonas)
        flag_oportunidad = False
        if not zona_no_trading:
            if probabilidad_general > 53 and not zona_sobrecompra:
                flag_oportunidad = True
            elif probabilidad_general < 47 and not zona_sobreventa:
                flag_oportunidad = True

        # Tendencia en tiempo real
        tendencia_predicha = predecir_tendencia_en_tiempo_real(df, temporalidad)

        salida = {
            "patrones_detectados": patrones_detectados,
            "predicciones_arima": predicciones_arima,
            "predicciones_media_movil": predicciones_media_movil,
            "probabilidad_alza": probabilidad_alza,
            "probabilidad_baja": probabilidad_baja,
            "macd_cruce": df["macd_cruce"].iloc[-1] if "macd_cruce" in df.columns else None,
            "macd_cerca_de_cruzar": df["macd_cerca_de_cruzar"].iloc[-1] if "macd_cerca_de_cruzar" in df.columns else None,
            "bollinger_signal": df["bollinger_signal"].iloc[-1] if "bollinger_signal" in df.columns else None,
            "bollinger_upper": last_of(df, "bollinger_upper", default=None) if "bollinger_upper" in df.columns else None,
            "bollinger_lower": last_of(df, "bollinger_lower", default=None) if "bollinger_lower" in df.columns else None,
            "tendencia_predicha": tendencia_predicha,
            "ultimo_valor": precio_actual,
            "soporte_nivel_2": niveles_clave.get("soporte_nivel_2"),
            "soporte_nivel_1": niveles_clave.get("soporte_nivel_1"),
            "resistencia_nivel_1": niveles_clave.get("resistencia_nivel_1"),
            "resistencia_nivel_2": niveles_clave.get("resistencia_nivel_2"),
            "apalancamiento_compra_nivel_1": niveles_clave.get("multiplicador", {}).get("apalancamiento_compra_nivel_1"),
            "apalancamiento_compra_nivel_2": niveles_clave.get("multiplicador", {}).get("apalancamiento_compra_nivel_2"),
            "apalancamiento_venta_nivel_1": niveles_clave.get("multiplicador", {}).get("apalancamiento_venta_nivel_1"),
            "apalancamiento_venta_nivel_2": niveles_clave.get("multiplicador", {}).get("apalancamiento_venta_nivel_2"),
            "precio_entrada": precio_entrada,
            "take_profit": take_profit,
            "stop_loss": stop_loss,
            "es_rango_repetitivo": en_rango.get("es_rango_repetitivo"),
            "estructura_tendencia": en_rango.get("estructura_tendencia"),
            "rebotes": en_rango.get("rebotes"),
            "rango_dinamico": en_rango.get("rango_dinamico"),
            "soportes_alcanzados": niveles_clave.get("niveles_importantes_soportes"),
            "resistencias_alcanzadas": niveles_clave.get("niveles_importantes_resistencias"),
            "cerca_de_soporte_resistencia": cerca_de_soporte_resistencia,
            "soportes_importantes_alcanzados": niveles_clave.get("soportes_confirmados_orden"),
            "resistencias_importantes_alcanzadas": niveles_clave.get("resistencias_confirmadas_orden"),
            "niveles_confirmados_orden_toques_all": niveles_clave.get("niveles_confirmados_orden_toques_all"),
            "niveles_confirmados_orden_nivel_all": niveles_clave.get("niveles_confirmados_orden_nivel_all"),
            "niveles_confirmados_orden_nivel_reduced": niveles_clave.get("niveles_confirmados_orden_nivel_reduced"),
            "probabilidad_tecnica": probabilidad_tecnica,
            "probabilidad_fundamental": probabilidad_fundamental,
            "probabilidad_general": probabilidad_general,
            "tipo_operacion": tipo_operacion,
            "flag_oportunidad": flag_oportunidad,
            "zona_no_trading": zona_no_trading,
            "zona_sobreventa": zona_sobreventa,
            "zona_sobrecompra": zona_sobrecompra,
            "entradas": entradas_mult,
        }
        return json_safe(salida)

    except Exception as e:
        logger.exception("calcular_entradas falló: %s", e)
        if not salida:
            salida = {}
        salida.setdefault("entradas_multiples", [])
        salida.setdefault("entradas", {"lista": []})
        return json_safe(salida)


# Función para generar un archivo con la fecha y hora en el nombre
#@profile
def generar_nombre_archivo(moneda_filtro, filtro=False, tipo=None):
    fecha_hora = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    # Determinar el tipo de archivo (principal/secundaria) primero
    if tipo == "principal":
        tipo_parte = "principal"
    elif tipo == "secundaria":
        tipo_parte = "secundaria"
    else:
        tipo_parte = "general"  # Por defecto, indicar que es un archivo general
    
    # Determinar la base (resultados u oportunidades)
    if filtro:
        base = "oportunidades"
    else:
        base = "resultados"
    
    # Construir el nombre en el orden deseado
    return f"{moneda_filtro}_{tipo_parte}_{base}_{fecha_hora}.csv"


# Función para enviar el archivo CSV a todos los clientes
#@profile
async def enviar_csv_telegram(
    df,
    context,
    filename: str = "resultados.csv",
    user_chat_id=None,
    intentos: int = 3,
    cfg: dict | None = None,   # <— NUEVO (opcional)
):
    """Envía CSV formateado según cfg a 1+ chats."""
    chat_ids = [user_chat_id] if user_chat_id else clientes_chat_ids

    if df.empty:
        for chat_id in chat_ids:
            for intento in range(intentos):
                try:
                    await context.bot.send_message(
                        chat_id=chat_id,
                        text="No se pudo generar el CSV. El DataFrame está vacío."
                    )
                    break
                except TimedOut:
                    await asyncio.sleep(2)
        return

    # Parametrización regional
    sep, quotechar, header, encoding, lineterminator = _csv_params_from_cfg(cfg)

    # Usa el mismo preformateo que el archivo en disco
    df_out = _prepare_df_for_csv(df, cfg)

    # Crear buffer binario y escribir CSV respetando encoding / saltos de línea
    buffer = BytesIO()
    df_out.to_csv(
        buffer,
        sep=sep,
        index=False,
        header=header,
        encoding=encoding,
        lineterminator=lineterminator,
        quoting=_csv.QUOTE_MINIMAL,
        quotechar=quotechar,
    )
    buffer.seek(0)

    for chat_id in chat_ids:
        try:
            await context.bot.send_document(
                chat_id=chat_id, document=buffer, filename=f"{filename}"
            )
            buffer.seek(0)
        except Exception as e:
            logger.info(f"Error al enviar CSV a {chat_id}: {e}")


#@profile
def generar_imagen_eventos_oportunidades(
    df_eventos: pd.DataFrame,
    divisas_oportunidades: Sequence[str] | None,
    *,
    tz_name: str = "America/Santiago",
    max_filas_por_imagen: int = 22,
    dpi: int = 170,
    font_size: int = 9
):


    # 1) Validaciones y filtro por divisas
    try:
        divisas = (
            pd.Series(list(divisas_oportunidades) if divisas_oportunidades is not None else [])
            .dropna().astype(str).unique().tolist()
        )
    except Exception:
        divisas = []

    if df_eventos is None or getattr(df_eventos, "empty", True) or len(divisas) == 0:
        return None

    df = df_eventos.copy()

    if divisas_oportunidades:
        divs = set(str(x).upper() for x in divisas_oportunidades if x)
        if "currency" in df.columns:
            df = df[df["currency"].astype(str).str.upper().isin(divs)]
    if df.empty:
        return None

    # 2) Orden por tiempo si existe y columna "Fecha/Hora"
    for cand in ["t", "time", "timestamp", "fecha", "date", "datetime"]:
        if cand in df.columns:
            try:
                df[cand] = pd.to_datetime(df[cand], errors="coerce", utc=True)
                try:

                    tz = pytz.timezone(tz_name)
                    df[cand] = df[cand].dt.tz_convert(pytz.UTC)
                except Exception:
                    df[cand] = df[cand].dt.tz_localize(pytz.UTC)
                df = df.sort_values(cand)
                df["Fecha/Hora"] = df[cand].dt.strftime("%Y-%m-%d %H:%M")
            except Exception:
                pass
            break

    # 3) Búsqueda robusta de columnas (case-insensitive con coalesce por fila)
    cols_lower = [str(c).strip().lower() for c in df.columns]

    def _find_cols(candidates: list[str], *, forbid_substrings: list[str] | None = None) -> list[str]:
        """Devuelve columnas reales que matchean aliases (exacto primero, luego por token/substring seguro).
           forbid_substrings: evita columnas que contengan estas cadenas (p.ej. para no confundir 'value' prev/forecast)."""
        found = []
        seen = set()
        forb = [s.lower() for s in (forbid_substrings or [])]

        def _allowed(name_lower: str) -> bool:
            return not any(f in name_lower for f in forb)

        # exact matches
        for a in candidates:
            for i, lc in enumerate(cols_lower):
                if lc == a and _allowed(lc) and df.columns[i] not in seen:
                    found.append(df.columns[i]); seen.add(df.columns[i])
        # token-aware / substring segura: límite por separadores comunes
        for a in candidates:
            pat = re.compile(rf'(^|[ _\-\.:/]){re.escape(a)}($|[ _\-\.:/])')
            for i, lc in enumerate(cols_lower):
                if df.columns[i] in seen:
                    continue
                if _allowed(lc) and (pat.search(lc) or a in lc):
                    found.append(df.columns[i]); seen.add(df.columns[i])
        return found

    def _coalesce_into(dst: str, aliases: list[str], *, forbid_substrings: list[str] | None = None):
        """Crea df[dst] como el primer no-nulo por fila entre las columnas candidatas."""
        if dst in df.columns:
            return
        srcs = _find_cols([a.lower() for a in aliases], forbid_substrings=forbid_substrings)
        if srcs:
            tmp = df[srcs].copy()
            for c in tmp.columns:
                tmp[c] = tmp[c].replace("", np.nan)
            df[dst] = tmp.bfill(axis=1).iloc[:, 0]
        else:
            df[dst] = np.nan

    # Aliases: quitamos 'act' y 'value' para evitar confundir con 'impact' y valores genéricos
    _coalesce_into("Evento",   ["title", "event", "name", "evento"])
    _coalesce_into("Moneda",   ["currency", "cur", "moneda", "fx"])
    _coalesce_into("Impacto",  ["impact", "importance", "volatility", "impacto"])
    _coalesce_into("Actual",   ["actual", "actual_value", "last", "real", "resultado"], forbid_substrings=["impact", "forecast", "previous", "prior"])
    _coalesce_into("Estimado", ["forecast", "estimate", "consensus", "expected", "predicted", "projection"])
    _coalesce_into("Anterior", ["previous", "prior", "prev", "previous_value", "anterior", "revised_previous", "revised_prior"])

    # 4) Orden de columnas solicitado
    desired_order = ["Fecha/Hora", "Moneda", "Evento", "Impacto", "Anterior", "Estimado", "Actual"]
    cols = [c for c in desired_order if c in df.columns]

    # Asegurar existencia (se rellenan luego)
    for name in desired_order:
        if name not in df.columns:
            df[name] = np.nan
        if name not in cols:
            cols.append(name)

    # Evitar duplicados manteniendo orden
    seen = set(); cols = [c for c in cols if not (c in seen or seen.add(c))]
    df = df[cols]

    # 5) Normalización/numérico y relleno
    df = df.replace([np.inf, -np.inf], np.nan)

    # Intentar convertir a numérico Actual/Estimado/Anterior; si falla, quedará NaN -> "—"
    for c in ("Actual", "Estimado", "Anterior"):
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")

    df = df.fillna("—")

    for c in ("Actual", "Estimado", "Anterior"):
        if c in df.columns:
            df[c] = df[c].apply(_fmt_num)

    # 6) Ajustes de legibilidad/ancho
    fs = min(font_size, 8)    # un punto menos para ganar ancho
    wrap = {"Evento": 18}     # más wrap en Evento (columna larga)

    imgs = tabla_a_imagenes(
        df,
        max_filas_por_imagen=max_filas_por_imagen,
        dpi=dpi,
        font_size=fs,
        wrap_map=wrap
    )
    return imgs if imgs else None

#@profile
def preparar_df_oportunidades_para_tabla(df_in: pd.DataFrame) -> pd.DataFrame:
    """Devuelve un DF listo para pintar: columnas seleccionadas + formato + headers multilínea."""
    if df_in is None or df_in.empty:
        return pd.DataFrame()

    df = df_in.copy()

    # columnas que queremos mostrar (si existen en df)
    preferidas = [
        "Activo",
        "Temporalidad",
        "Tipo de Operacion",
        "Ultimo Valor",
        "Probabilidad General (%)",
        "Apalancamiento Compra Nivel 1",
        "Apalancamiento Compra Nivel 2",
        "Apalancamiento Venta Nivel 1",
        "Apalancamiento Venta Nivel 2",
    ]
    cols = [c for c in preferidas if c in df.columns]
    if not cols:
        # fallback razonable
        cols = [c for c in ["Activo","Temporalidad","Ultimo Valor"] if c in df.columns]

    df = df[cols].copy()

    # Formato por columna
    if "Ultimo Valor" in df:
        df["Ultimo Valor"] = df["Ultimo Valor"].apply(_fmt_num)

    if "Probabilidad General (%)" in df:
        df["Probabilidad General (%)"] = df["Probabilidad General (%)"].apply(_fmt_pct)

    for c in [
        "Apalancamiento Compra Nivel 1",
        "Apalancamiento Compra Nivel 2",
        "Apalancamiento Venta Nivel 1",
        "Apalancamiento Venta Nivel 2",
    ]:
        if c in df:
            df[c] = df[c].apply(_fmt_apal)

    # Wrap de contenido para evitar desbordes
    for c in df.columns:
        df[c] = df[c].apply(lambda v: _wrap_text(v, WRAP_CELL))

    # Encabezados multilínea (más cortos)
    ren = {
        "Tipo de Operacion":          "Tipo de\nOperación",
        "Probabilidad General (%)":   "Prob.\nGeneral (%)",
        "Apalancamiento Compra Nivel 1": "Apalancamiento\nCompra N1",
        "Apalancamiento Compra Nivel 2": "Apalancamiento\nCompra N2",
        "Apalancamiento Venta Nivel 1":  "Apalancamiento\nVenta N1",
        "Apalancamiento Venta Nivel 2":  "Apalancamiento\nVenta N2",
    }
    df.columns = [ _wrap_text(ren.get(c, c), WRAP_HDR) for c in df.columns ]

    return df


#@profile
def tabla_a_imagenes(
    df: pd.DataFrame,
    max_filas_por_imagen: int = 18,
    dpi: int = 170,
    font_size: int = 10,
    *,
    wrap_map: Optional[dict[str, int]] = None,   # 👈 NUEVO: {columna: ancho_en_chars}
) -> list[BytesIO]:
    if df is None or df.empty:
        return []

    buffers: list[BytesIO] = []

    # Particionar el DF en páginas
    for start in range(0, len(df), max_filas_por_imagen):
        parte = df.iloc[start:start + max_filas_por_imagen].copy()

        # ---- 1) Aplicar multilínea por columnas según wrap_map ----
        if wrap_map:
            for col, width in wrap_map.items():
                if col in parte.columns and isinstance(width, int) and width > 0:
                    parte[col] = parte[col].astype(str).map(lambda x: _wrap_text_multiline(x, width))

        # ---- 2) Calcular anchos de columna tras el wrap ----
        # Longitud efectiva: máximo de la línea más larga por celda/encabezado (cap a 40)
        char_w = []
        for i, c in enumerate(parte.columns):
            header_len = len(str(c))
            col_vals = parte.iloc[:, i].astype(str).tolist()
            max_len_cell = 0
            for txt in col_vals:
                # línea más larga en el texto multilínea
                max_len_cell = max(max_len_cell, max((len(ln) for ln in (txt.split("\n") or [""])), default=0))
            max_len = max(header_len, max_len_cell)
            max_len = max(8, min(40, max_len))      # entre 8 y 40 chars
            char_w.append(max_len)

        # convertir chars -> pulgadas (aprox 0.12 in/char) y normalizar para colWidths
        col_in = [w * 0.12 for w in char_w]
        total_w = sum(col_in) or 1.0
        col_widths_norm = [w / total_w for w in col_in]

        # ---- 3) Calcular alto de figura según líneas por fila (tras el wrap) ----
        #@profile
        def _line_count_cell(txt: str) -> int:
            return max(1, len((str(txt) or "").split("\n")))
        # Para cada fila, toma el máximo de líneas entre sus celdas
        lines_per_row = []
        for r in range(len(parte)):
            max_lines = 1
            for c in parte.columns:
                max_lines = max(max_lines, _line_count_cell(parte.iloc[r][c]))
            lines_per_row.append(max_lines)

        # Heurística de alto: cada línea ~0.38 in + header ~0.55 in
        base_line_h = 0.38 * (font_size / 10.0)     # escala suave con font_size
        header_h = 0.55
        filas_h = sum(base_line_h * lc for lc in lines_per_row)
        fig_h = header_h + filas_h + 0.5            # padding inferior
        fig_w = total_w + 0.8                       # margen lateral

        # ---- 4) Render de la tabla ----
        fig, ax = plt.subplots(figsize=(fig_w, fig_h), dpi=dpi)
        ax.axis("off")

        tbl = ax.table(
            cellText=parte.values,
            colLabels=list(parte.columns),
            cellLoc="left",               # por defecto izquierda (mejor para texto multilínea)
            loc="upper left",
            bbox=[0, 0, 1, 1],
            colWidths=col_widths_norm,    # 👈 respetar anchos relativos
        )
        tbl.auto_set_font_size(False)
        tbl.set_fontsize(font_size)

        # Header en negrita
        for (r, c), cell in tbl.get_celld().items():
            if r == 0:
                cell.set_text_props(weight="bold")

        # Alineación derecha para celdas que "parezcan" numéricas o porcentajes
        n_rows, n_cols = parte.shape
        for r in range(1, n_rows + 1):
            for c in range(n_cols):
                txt = str(parte.iat[r - 1, c])
                if txt.endswith("%") or _parece_numero(txt):
                    tbl[r, c]._loc = "right"
                else:
                    tbl[r, c]._loc = "left"

        buf = BytesIO()
        plt.savefig(buf, format="png", dpi=dpi, bbox_inches="tight", pad_inches=0.05)
        buf.seek(0)
        plt.close(fig)
        buffers.append(buf)

    return buffers

#@profile
def _parece_numero(s: str) -> bool:
    s = s.strip().replace(",", "")
    # permite "123", "123.45", "-0.5", "1 234.56"
    try:
        float(s)
        return True
    except Exception:
        return False

        
# Función para enviar la imagen de los eventos relacionados a las oportunidades
#@profile
async def enviar_imagen_eventos_oportunidades(
    df_eventos,
    divisas_oportunidades,
    context,
    user_chat_id=None,
    intentos=3,
    *,
    moneda_filtro: str | None = None
) -> list[str]:
    """
    Genera 1..N imágenes de eventos (estilo tabla), las envía a Telegram,
    y si corresponde, las sube al bucket registrándolas. Devuelve URLs subidas.
    """
    # 1) Generar (puede devolver list[BytesIO] o None)
    imagen_eventos = generar_imagen_eventos_oportunidades(df_eventos, divisas_oportunidades)

    chat_ids = [user_chat_id] if user_chat_id else clientes_chat_ids
    urls_subidas: list[str] = []

    # Normalizar a lista
    if imagen_eventos is None:
        imgs: list[BytesIO] = []
    elif isinstance(imagen_eventos, list):
        imgs = imagen_eventos
    else:
        imgs = [imagen_eventos]

    # 2) Enviar a Telegram
    if not imgs:
        logger.info("No se generó imagen de eventos (lista vacía).")
        return urls_subidas

    total = len(imgs)
    for idx, img in enumerate(imgs, start=1):
        if img.getbuffer().nbytes <= 0:
            continue
        caption_base = "Eventos relacionados a las oportunidades."
        caption = f"{caption_base} Parte {idx} de {total}" if total > 1 else caption_base

        for chat_id in chat_ids:
            # Reintentos
            for intento in range(intentos):
                try:
                    img.seek(0)
                    await context.bot.send_photo(chat_id=chat_id, photo=img, caption=caption)
                    break
                except TimedOut:
                    logger.info(f"Intento {intento + 1} fallido enviando eventos a {chat_id}. Reintentando…")
                    await asyncio.sleep(2)
                except telegram.error.BadRequest as e:
                    logger.info(f"Error al enviar imagen de eventos a {chat_id}: {e}")
                    break
                except Exception as e:
                    logger.info(f"Error inesperado enviando imagen de eventos a {chat_id}: {e}")
                    await asyncio.sleep(2)


#@profile
def calcular_ponderacion_incremental_por_divisa(df: pd.DataFrame, cfg: dict | None = None) -> pd.DataFrame:
    """
    Suma/Restar PI por 'Activo' según señales y orden de temporalidades.
    Usa cfg.ponderacion_inc: enable, peso_base, temporalidades (CSV).
    Si está desactivado o faltan columnas, devuelve PI=0.
    """
    inc = _norm_ponder_inc_cfg(cfg)

    # crea columna si no existe
    if "Ponderacion Incremental" not in df.columns:
        df["Ponderacion Incremental"] = 0

    # validaciones mínimas
    need_cols = {"Activo", "Tipo de Operacion", "Temporalidad"}
    if not inc.get("enable", True) or not need_cols.issubset(set(df.columns)):
        df["Ponderacion Incremental"] = 0
        return df

    # señales_* pueden no estar en scope; respeta comportamiento original
    try:
        _ = señales_compra
        _ = señales_venta
    except (NameError, TypeError):
        df["Ponderacion Incremental"] = 0
        return df

    peso_base = max(1, int(inc.get("peso_base", 1)))
    tfs = inc.get("_tfs_list", [])
    if not tfs:
        tfs = [t.strip() for t in DEFAULT_PONDER_INC_CFG["temporalidades"].split(",")]

    idx = {tf.lower(): i for i, tf in enumerate(tfs)}
    aliases = {"1min":"1m","5min":"5m","15min":"15m","30min":"30m","1hour":"1h","4hour":"4h","1day":"1d","1week":"1w"}

    req = {"Temporalidad", "Tipo de Operacion", "Activo"}
    if not req.issubset(df.columns):
        df["Ponderacion Incremental"] = 0
        return df

    tf_ser = df["Temporalidad"].astype(str).str.strip().str.lower().replace(aliases)
    tf_i = tf_ser.map(idx)  # float/NaN
    valid_tf = tf_i.notna()

    W = np.zeros(len(df), dtype=np.int64)
    if valid_tf.any():
        W[valid_tf.to_numpy()] = (
            peso_base * (2 ** tf_i[valid_tf].astype(int))
        ).to_numpy(dtype=np.int64)

    try:
        buy_set = set(señales_compra)
        sell_set = set(señales_venta)
    except NameError:
        buy_set, sell_set = set(), set()

    tipo_ser = df["Tipo de Operacion"]
    buy_mask = tipo_ser.isin(buy_set).to_numpy()
    sell_mask = tipo_ser.isin(sell_set).to_numpy()
    sell_mask = sell_mask & (~buy_mask)

    signed = np.zeros(len(df), dtype=np.int64)
    signed[buy_mask] = W[buy_mask]
    signed[sell_mask] = -W[sell_mask]

    pi = (
        pd.Series(signed, index=df.index)
        .groupby(df["Activo"])
        .transform("sum")
        .fillna(0)
        .astype(np.int64)
    )
    df["Ponderacion Incremental"] = pi
    return df


# ---- DEFAULTS para cfg.ponderacion y cfg.ponderacion_inc ----
DEFAULT_PONDER_CFG = {
    "enable": True,
    "prob_high": 60, "prob_low": 40, "neutral_min": 47, "neutral_max": 53,
    "prob_high_delta": 2, "prob_low_delta": -2, "neutral_delta": -1,
    "patrones_alcistas_delta": 3, "patrones_bajistas_delta": -3,
    "concordancia_bull_delta": 2, "concordancia_bear_delta": -2,
    "near_s1_delta": 2, "near_r1_delta": -2, "near_s2_delta": 1, "near_r2_delta": -1,
    "macd_cruce_alcista_delta": 1, "macd_cruce_bajista_delta": -1,
    "bollinger_bajo_delta": 2, "bollinger_alto_delta": -2,
    "tendencia_alcista_delta": 2, "tendencia_bajista_delta": -2,
    "senal_compra_delta": 3, "senal_venta_delta": -3,
    "ponder_inc_threshold": 10, "ponder_inc_pos_delta": 3, "ponder_inc_neg_delta": -3,
    "mult_1m_5m": 1.10, "mult_15m_30m": 1.05, "mult_1h_4h": 1.00, "mult_1d_1w": 0.90,
    "clamp_min": -20, "clamp_max": 20,
}

DEFAULT_PONDER_INC_CFG = {
    "enable": True,
    "peso_base": 1,
    # orden de mayor a menor peso (i=0,1,2...) -> 2**i
    "temporalidades": "1w,1d,4h,1h,30m,15m,5m,1m",
}

#@profile
def _norm_ponder_cfg(cfg: dict | None) -> dict:
    try:
        user = ((cfg or {}).get("ponderacion") or {})
        return {**DEFAULT_PONDER_CFG, **user}
    except Exception:
        return DEFAULT_PONDER_CFG.copy()

#@profile
def _norm_ponder_inc_cfg(cfg: dict | None) -> dict:
    try:
        user = ((cfg or {}).get("ponderacion_inc") or {})
        base = {**DEFAULT_PONDER_INC_CFG, **user}
        # normaliza CSV -> lista ordenada
        tfs = [t.strip() for t in str(base.get("temporalidades", "")).split(",") if t.strip()]
        base["_tfs_list"] = tfs if tfs else [t.strip() for t in DEFAULT_PONDER_INC_CFG["temporalidades"].split(",")]
        return base
    except Exception:
        out = DEFAULT_PONDER_INC_CFG.copy()
        out["_tfs_list"] = [t.strip() for t in out["temporalidades"].split(",")]
        return out

#@profile
def calcular_ponderacion(row: dict, cfg: dict | None = None) -> float:
    p = _norm_ponder_cfg(cfg)
    if not p.get("enable", True):
        return 0.0

    ponderacion = 0.0

    # --- Probabilidad general ---
    pg = row.get('Probabilidad General (%)')
    if pg is not None:
        if pg > p["prob_high"]:
            ponderacion += p["prob_high_delta"]
        elif pg < p["prob_low"]:
            ponderacion += p["prob_low_delta"]
        elif p["neutral_min"] <= pg <= p["neutral_max"]:
            ponderacion += p["neutral_delta"]

    # --- Patrones ---
    patrones = row.get('Patrones Detectados') or []
    try:
        if any(pat in patrones for pat in patrones_alcistas):
            ponderacion += p["patrones_alcistas_delta"]
        elif any(pat in patrones for pat in patrones_bajistas):
            ponderacion += p["patrones_bajistas_delta"]
    except NameError:
        # si no existen las listas en este scope, no penaliza/bonifica
        pass

    # --- Concordancia Tec + Fund ---
    pt = row.get('Probabilidad Tecnica (%)', 50)
    pf = row.get('Probabilidad Fundamental (%)', 50)
    if pt > 60 and pf > 60:
        ponderacion += p["concordancia_bull_delta"]
    elif pt < 40 and pf < 40:
        ponderacion += p["concordancia_bear_delta"]

    # --- Niveles / cercanías ---
    precio = row.get('Ultimo Valor')
    s1 = row.get('Soporte Nivel 1')
    r1 = row.get('Resistencia Nivel 1')
    s2 = row.get('Soporte Nivel 2')
    r2 = row.get('Resistencia Nivel 2')

    # Distancias relativas (si hay datos)
    if precio is None or s1 is None or r1 is None:
        logger.info(f"Valores no válidos para niveles: precio={precio}, s1={s1}, r1={r1}")
    else:
        try:
            if abs(precio - s1) / s1 < 0.01:
                ponderacion += p["near_s1_delta"]
        except ZeroDivisionError:
            pass
        try:
            if abs(r1 - precio) / r1 < 0.01:
                ponderacion += p["near_r1_delta"]
        except ZeroDivisionError:
            pass

        # Ventanas 1% sobre/under
        if precio is not None and s1 is not None and precio <= s1 * 1.01:
            ponderacion += max(0, p["near_s1_delta"])  # refuerzo
        if precio is not None and r1 is not None and precio >= r1 * 0.99:
            ponderacion += min(0, p["near_r1_delta"])  # refuerzo hacia negativo

    if s2 is not None and precio is not None and precio <= s2 * 1.01:
        ponderacion += p["near_s2_delta"]
    if r2 is not None and precio is not None and precio >= r2 * 0.99:
        ponderacion += p["near_r2_delta"]

    # --- MACD cruce ---
    macd_cruce = row.get('Cruce MACD')
    if macd_cruce == 'Cruce Alcista':
        ponderacion += p["macd_cruce_alcista_delta"]
    elif macd_cruce == 'Cruce Bajista':
        ponderacion += p["macd_cruce_bajista_delta"]

    # --- Bollinger ---
    bup = row.get('bollinger_upper')
    blo = row.get('bollinger_lower')
    if pd.notna(bup) and pd.notna(blo) and precio is not None:
        if precio < blo:
            ponderacion += p["bollinger_bajo_delta"]
        elif precio > bup:
            ponderacion += p["bollinger_alto_delta"]

    # --- Tendencia predicha ---
    tend = row.get('Tendencia Predicha', 'Neutral')
    if tend == 'Alcista':
        ponderacion += p["tendencia_alcista_delta"]
    elif tend == 'Bajista':
        ponderacion += p["tendencia_bajista_delta"]

    # --- Señal detectada (compra/venta) ---
    signal = row.get('Tipo de Operacion', 'Neutral')
    try:
        if signal in señales_compra:
            ponderacion += p["senal_compra_delta"]
        elif signal in señales_venta:
            ponderacion += p["senal_venta_delta"]
    except NameError:
        pass

    # --- Ponderación incremental (columna previa del DF) ---
    pi = row.get('Ponderacion Incremental', 0)
    th = p["ponder_inc_threshold"]
    if isinstance(pi, (int, float)) and th is not None:
        if pi >= th:
            ponderacion += p["ponder_inc_pos_delta"]
        elif pi <= -th:
            ponderacion += p["ponder_inc_neg_delta"]

    # --- Multiplicador por temporalidad ---
    tf = str(row.get('Temporalidad', '')).lower()
    if any(k in tf for k in ('1min', '5min', '1m', '5m')):
        ponderacion *= p["mult_1m_5m"]
    elif any(k in tf for k in ('15min', '30min', '15m', '30m')):
        ponderacion *= p["mult_15m_30m"]
    elif any(k in tf for k in ('1hour', '4hour', '1h', '4h')):
        ponderacion *= p["mult_1h_4h"]
    elif any(k in tf for k in ('1day', '1week', '1d', '1w')):
        ponderacion *= p["mult_1d_1w"]

    # --- Clamp final ---
    lo, hi = p["clamp_min"], p["clamp_max"]
    ponderacion = max(min(ponderacion, hi), lo)

    return float(ponderacion)

#@profile
def procesar_simbolo_temporalidad(
    symbol: str,
    temporalidad: str,
    df_eventos,
    user_chat_id: str,
    context,
    *,
    fmp_windows: dict[str,int] | None = None,
    calc_windows: dict[str,int] | None = None,
    cfg: dict | None = None
):
    # Asegurar notación backend de TF (1min, 4hour, 1day, 1week…)
    tf = _tf_backend(temporalidad)  # <- este es el valor correcto a usar en todo el flujo

    # ------------------- OHLCV (hist + realtime) -------------------
    try:
        # Solo pasa fmpWindows si viene; si no, deja cfg vacío => bars=None
        cfg_local = {"fmpWindows": fmp_windows} if isinstance(fmp_windows, dict) and fmp_windows else {}

        df_combinado = obtener_datos_con_hilos(
            symbol, tf, user_chat_id=user_chat_id, cfg=cfg_local
        )
        if df_combinado is None or df_combinado.empty:
            logger.info(f"No hay datos combinados para {symbol} en {tf}.")
            return None
    except Exception as e:
        logger.info(f"obtener_datos_con_hilos falló para {symbol}-{tf}: {e}")
        return None

    # ------------------- Indicadores -------------------
    try:
        df_indicadores = calcular_indicadores(df_combinado, tf)
        if df_indicadores is None or df_indicadores.empty:
            logger.info(f"No hay indicadores para {symbol} en {tf}.")
            return None
    except Exception as e:
        logger.info(f"calcular_indicadores falló para {symbol}-{tf}: {e}")
        return None

    # ------------------- Entradas / señales -------------------
    try:
        entradas = calcular_entradas(
            df_indicadores, df_eventos, symbol, tf, user_chat_id,
            calc_windows=calc_windows
        )
        if not entradas:
            logger.info(f"No se pudieron calcular entradas para {symbol} en {tf}.")
            return None
    except Exception as e:
        logger.info(f"calcular_entradas falló para {symbol}-{tf}: {e}")
        return None

    # Devolver resultados
    resultado = {
        "Activo": symbol,
        "Temporalidad": temporalidad,
        "Oportunidad": entradas.get('flag_oportunidad'),
        "Patrones Detectados": entradas.get('patrones_detectados'),
        "Tipo de Operacion": entradas.get('tipo_operacion'),
        "Ultimo Valor": entradas.get('ultimo_valor'),
        "Soporte Nivel 2": entradas.get('soporte_nivel_2'),
        "Soporte Nivel 1": entradas.get('soporte_nivel_1'),
        "Resistencia Nivel 1": entradas.get('resistencia_nivel_1'),
        "Resistencia Nivel 2": entradas.get('resistencia_nivel_2'),
        "Apalancamiento Compra Nivel 2": entradas.get('apalancamiento_compra_nivel_2'),
        "Apalancamiento Compra Nivel 1": entradas.get('apalancamiento_compra_nivel_1'),
        "Apalancamiento Venta Nivel 2": entradas.get('apalancamiento_venta_nivel_2'),
        "Apalancamiento Venta Nivel 1": entradas.get('apalancamiento_venta_nivel_1'),
        "Precio de Entrada": entradas.get('precio_entrada'),
        "Take Profit": entradas.get('take_profit'),
        "Stop Loss": entradas.get('stop_loss'),
        "Soportes Alcanzados": entradas.get("soportes_alcanzados"),
        "Resistencias Alcanzadas": entradas.get("resistencias_alcanzadas"),
        "Cerca de Soporte Resistencia": entradas.get('cerca_de_soporte_resistencia'),
        "Es Rango Repetitivo": entradas.get("es_rango_repetitivo"),
        "Estructura Tendencia": entradas.get('estructura_tendencia'),
        "Rebotes": entradas.get("rebotes"),
        "Rango Dinamico": entradas.get("rango_dinamico"),
        "Soportes Importantes Alcanzados": entradas.get("soportes_importantes_alcanzados"),
        "Resistencias Importantes Alcanzadas": entradas.get("resistencias_importantes_alcanzadas"),
        **({"Niveles Confirmados (Toques)": entradas.get('niveles_confirmados_orden_toques_all')} if es_administrador(user_chat_id) else {}),
        "Niveles Confirmados (Nivel)":  entradas.get("niveles_confirmados_orden_nivel_all") if es_administrador(user_chat_id) else entradas.get("niveles_confirmados_orden_nivel_reduced"),
        "Bollinger Signal": entradas.get('bollinger_signal'),
        "bollinger_upper": entradas.get('bollinger_upper'),
        "bollinger_lower": entradas.get('bollinger_lower'),
        "MACD Tendencia Predicha": entradas.get('tendencia_predicha'),
        "Cruce MACD": entradas.get('macd_cruce'),
        "MACD Cerca": entradas.get('macd_cerca_de_cruzar'),
        "Zona Sobreventa RSI-Stochastic": entradas.get('zona_sobreventa'),
        "Zona Sobrecompra RSI-Stochastic": entradas.get('zona_sobrecompra'),
        "Zona No Trading": entradas.get('zona_no_trading'),
        "Probabilidad Alza (Montecarlo)": entradas.get('probabilidad_alza'),
        "Probabilidad Baja (Montecarlo)": entradas.get('probabilidad_baja'),
        "Probabilidad Tecnica (%)": entradas.get('probabilidad_tecnica'),
        "Probabilidad Fundamental (%)": entradas.get('probabilidad_fundamental'),
        "Probabilidad General (%)": entradas.get('probabilidad_general')
    }

    # --- Adjuntos internos para subir JSON enriquecido/ohlcv (no se guardan en Firestore) ---
    resultado["_ohlcv_df"] = df_combinado          # velas combinadas (hist + realtime)
    resultado["_indicadores_df"] = df_indicadores  # trae SMA, BB, MACD, RSI, %K/%D, ATR, etc.
    resultado["_entradas"] = entradas              # señales/valores calculados
    resultado["_niveles"]  = {                     # niveles clave para dibujar en el gráfico
        "soporte_nivel_1": entradas.get("soporte_nivel_1"),
        "soporte_nivel_2": entradas.get("soporte_nivel_2"),
        "resistencia_nivel_1": entradas.get("resistencia_nivel_1"),
        "resistencia_nivel_2": entradas.get("resistencia_nivel_2"),
    }


    return resultado



#@profile
def filtrar_activos_por_moneda(lista_activos, moneda_filtro):
    """
    Filtra activos según la moneda o categoría especificada.
    Si el símbolo ingresado no está en ninguna lista, se devuelve tal cual.
    """
    logger.info(f"Filtrando activos por: {moneda_filtro}")

    moneda_filtro = moneda_filtro.strip().lower()  # Normalizar el filtro

    # Filtro especial para todas las monedas
    if moneda_filtro in {"all", "todos"}:
        return lista_activos

    # Filtros por categorías específicas
    categorias_especiales = {
        "cruces": categorias.get("Cruces", []),
        "exóticos": categorias.get("Exóticos", []),
        "oilandgas": categorias.get("OilAndGas", []),
        "agricultura": categorias.get("Agricultura", []),
        "cripto": categorias.get("Cripto", []),
        "indices": categorias.get("Indices", [])
    }
    if moneda_filtro in categorias_especiales:
        return categorias_especiales[moneda_filtro]

    # Filtros específicos por divisas principales
    if moneda_filtro.upper() == "USD":
        return relacionados_usd
    if moneda_filtro.upper() in categorias.get("Principales", []):
        return [activo for activo in forex if moneda_filtro.upper() in activo]

    # Verificar si es un par específico o un símbolo no conocido
    if moneda_filtro.upper() not in [activo.upper() for activo in lista_activos]:
        logger.info(f"Símbolo no reconocido: {moneda_filtro.upper()}. Devuelto como único resultado.")
        return [moneda_filtro.upper()]

    # Filtrar activos que contengan la moneda o parte del nombre
    return [activo for activo in lista_activos if moneda_filtro.upper() in activo]

# Función principal para ejecutar el análisis usando hilos
#@profile
async def ejecutar_analisis_con_hilos(
    df_eventos,
    activos_filtrados,
    user_chat_id,
    context,
    overrides: dict | None = None,           # operatoria normalizada
    cfg: dict | None = None
):
    resultados = []
    errores = []

    cfg_overrides       = overrides or {}
    fmp_map   = cfg_overrides.get('fmpWindows')
    calc_map  = cfg_overrides.get('calcWindows')
    temps     = cfg_overrides.get('tfs') or temporalidades

    valid = {'1min','5min','15min','30min','1hour','4hour','1day','1week'}
    temps = [t for t in temps if t in valid]

    loop = asyncio.get_running_loop()

    # --- Análisis principal ---
    analisis_tasks = []
    meta = []  # (symbol, temporalidad) alineado con analisis_tasks

    for symbol in activos_filtrados:
        for temporalidad in temps:
            fn = partial(
                procesar_simbolo_temporalidad,
                symbol, temporalidad, df_eventos, user_chat_id, context,
                fmp_windows=fmp_map,
                calc_windows=calc_map,
                cfg=cfg
            )
            fut = loop.run_in_executor(None, fn)
            analisis_tasks.append(fut)
            meta.append((symbol, temporalidad))

    analisis_results = await asyncio.gather(*analisis_tasks, return_exceptions=True)

    for idx, result in enumerate(analisis_results):
        symbol, temporalidad = meta[idx]
        if isinstance(result, Exception):
            logger.info(f"Error en análisis para símbolo {symbol} y temporalidad {temporalidad}: {result}")
            errores.append(str(result))
        elif result is not None:
            resultados.append(result)
        else:
            logger.info(f"Resultado vacío para símbolo {symbol} y temporalidad {temporalidad}.")

    if not resultados and errores:
        logger.info("No se pudieron obtener resultados debido a errores.")
        for error in errores:
            logger.info(f" - {error}")

    return resultados



def _datetime_strf_pattern(cfg: dict) -> str:
    """Construye el patrón strftime según la config regional."""
    date_fmt = (cfg.get("locale") or {}).get("date_format", "YYYY-MM-DD")
    # mapear tokens comunes a strftime
    date_map = {
        "DD/MM/YYYY": "%d/%m/%Y",
        "MM/DD/YYYY": "%m/%d/%Y",
        "YYYY-MM-DD": "%Y-%m-%d",
    }
    date_strf = date_map.get(date_fmt, "%Y-%m-%d")

    time_fmt = (cfg.get("locale") or {}).get("time_format", "24h")
    time_strf = "%H:%M" if time_fmt == "24h" else "%I:%M %p"

    # Combinar fecha + hora. Si una col es solo fecha o solo hora no pasa nada,
    # strftime aplica lo que corresponda.
    return f"{date_strf} {time_strf}"

def _format_numeric(x, dec_sep: str, thou_sep: str, decimals: int = 5):
    if x is None or (isinstance(x, float) and (np.isnan(x) or np.isinf(x))):
        return ""
    if isinstance(x, (int, np.integer)):
        # miles en enteros
        s = f"{x:,}"
        # estandariza a , / . y luego reemplaza al separador pedido
        s = s.replace(",", "_COMA_").replace(".", "_PUNTO_")
        if thou_sep == ",":
            s = s.replace("_COMA_", ",").replace("_PUNTO_", "")
        elif thou_sep == ".":
            s = s.replace("_COMA_", "").replace("_PUNTO_", ".")
        elif thou_sep == " ":
            s = s.replace("_COMA_", " ").replace("_PUNTO_", "")
        else:  # ''
            s = s.replace("_COMA_", "").replace("_PUNTO_", "")
        return s
    try:
        # flotantes con N decimales
        s = f"{float(x):,.{decimals}f}"  # usa coma como miles y punto decimal (estándar en US)
    except Exception:
        return str(x)

    # normaliza a marcadores, luego aplica los separadores elegidos
    s = s.replace(",", "_COMA_").replace(".", "_PUNTO_")
    # primero miles:
    if thou_sep == ",":
        s = s.replace("_COMA_", ",")
    elif thou_sep == ".":
        s = s.replace("_COMA_", ".")
    elif thou_sep == " ":
        s = s.replace("_COMA_", " ")
    else:  # ''
        s = s.replace("_COMA_", "")
    # luego decimal:
    s = s.replace("_PUNTO_", dec_sep)
    return s

def _csv_params_from_cfg(cfg: dict):
    csv_cfg = (cfg or {}).get("csv") or {}
    sep = csv_cfg.get("delimiter", ";")
    quotechar = csv_cfg.get("quote", '"')
    header = bool(csv_cfg.get("header", True))
    enc = (csv_cfg.get("encoding") or "utf-8").lower()
    newline = (csv_cfg.get("newline") or "LF").upper()
    lineterminator = "\r\n" if newline == "CRLF" else "\n"
    # normaliza encoding
    encoding = "ISO-8859-1" if enc in ("iso-8859-1", "latin-1") else "utf-8"
    return sep, quotechar, header, encoding, lineterminator


def _use_dayfirst(cfg: dict | None) -> bool:
    fmt = ((cfg or {}).get("locale") or {}).get("date_format")
    return fmt == "DD/MM/YYYY"

# si quieres formatear el texto de salida también:
def _strftime_from_cfg(cfg: dict | None) -> tuple[str, str, str]:
    loc = (cfg or {}).get("locale") or {}
    date_map = {
        "DD/MM/YYYY": "%d/%m/%Y",
        "MM/DD/YYYY": "%m/%d/%Y",
        "YYYY-MM-DD": "%Y-%m-%d",
    }
    time_map = {"24h": "%H:%M", "12h": "%I:%M %p"}
    date_fmt = date_map.get(loc.get("date_format"), "%Y-%m-%d")
    time_fmt = time_map.get(loc.get("time_format"), "%H:%M")
    return date_fmt, time_fmt, f"{date_fmt} {time_fmt}"

def _prepare_df_for_csv(df_in: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Copia el DF y aplica formatos de fecha/hora y numéricos según cfg, sin destruir columnas no-fecha."""
    if df_in is None or df_in.empty:
        return df_in

    df = df_in.copy()

    # --- Config ---
    dt_fmt    = _datetime_strf_pattern(cfg)
    dayfirst  = _use_dayfirst(cfg)
    loc       = (cfg.get("locale") or {})
    dec_sep   = loc.get("decimal_sep", ".")
    thou_sep  = loc.get("thousands_sep", "")

    # --- Heurística: solo considerar como fechas las columnas con nombre relacionado a fecha/hora ---
    def _looks_datetime_col(name: str) -> bool:
        n = str(name).strip().lower()
        # añade o quita claves si te conviene
        keys = ("fecha", "hora", "date", "time", "timestamp", "datetime", "created_at", "updated_at")
        return any(k in n for k in keys)

    # 1) Formatear columnas que YA son datetime
    for col in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df[col]):
            df[col] = df[col].dt.strftime(dt_fmt).fillna("")

    # 2) Intentar parsear SOLO columnas string que "parecen" de fecha/hora y con tasa de éxito suficiente
    for col in df.columns:
        ser = df[col]
        if pd.api.types.is_datetime64_any_dtype(ser):
            continue
        if not pd.api.types.is_string_dtype(ser):
            continue
        if not _looks_datetime_col(col):
            continue

        try:
            parsed = pd.to_datetime(
                ser, errors="coerce", utc=False, dayfirst=dayfirst, format="mixed"
            )
            success_ratio = float(parsed.notna().mean()) if len(parsed) else 0.0
            # Solo aceptar si parseó una proporción razonable de filas
            if success_ratio >= 0.6:
                df[col] = parsed.dt.strftime(dt_fmt).fillna("")
            # si no, dejamos la columna tal cual para NO romper texto numérico o categórico
        except Exception:
            # ante cualquier falla, no tocar la columna
            pass

    # 3) Formato numérico (solo columnas numéricas reales)
    num_cols = df.select_dtypes(include=["number"]).columns.tolist()
    for c in num_cols:
        df[c] = df[c].apply(lambda v: _format_numeric(v, dec_sep, thou_sep, decimals=5))

    return df


def save_df_as_csv(df: pd.DataFrame, path: str, cfg: dict):
    """Exporta df a CSV aplicando config regional/csv, evitando romper columnas no-fecha."""
    if df is None:
        return

    sep, quotechar, header, encoding, lineterminator = _csv_params_from_cfg(cfg)

    # Aviso si decimal == delimitador (solo log)
    try:
        dec_sep = (cfg.get("locale") or {}).get("decimal_sep", ".")
        if dec_sep == sep:
            logging.info(
                f"[CSV] El separador decimal '{dec_sep}' coincide con el delimitador '{sep}'. "
                "Considera usar ';' como delimitador para evitar ambigüedad."
            )
    except Exception:
        pass

    df_out = _prepare_df_for_csv(df, cfg)

    df_out.to_csv(
        path,
        sep=sep,
        index=False,
        header=header,
        encoding="ISO-8859-1" if (encoding or "").lower() in ("iso-8859-1", "latin-1") else "utf-8",
        lineterminator=lineterminator,
        quoting=_csv.QUOTE_MINIMAL,
        quotechar=quotechar,
    )


def _read_telegram_id_prefer_subscription(user_id: str) -> Optional[str]:
    """Lee telegram_id priorizando suscripciones_user, luego user_ids."""
    try:
        sub_doc = db.collection('suscripciones_user').document(user_id).get()
        if sub_doc.exists:
            tg = (sub_doc.to_dict() or {}).get('telegram_id')
            if tg:
                return str(tg)
    except Exception:
        pass

    try:
        uid_doc = db.collection('user_ids').document(user_id).get()
        if uid_doc.exists:
            tg = (uid_doc.to_dict() or {}).get('telegram_id')
            if tg:
                return str(tg)
    except Exception:
        pass

    return None


def _resolve_chat_id(user_id: Optional[str], user_chat_id: Optional[str]) -> Optional[str]:
    """
    Resuelve el chat_id (telegram_id) con prioridad:
      1) user_chat_id explícito (cuando viene del bot TG).
      2) suscripciones_user/{userId}.telegram_id
      3) user_ids/{userId}.telegram_id
    """
    # 1) parámetro explícito
    if user_chat_id and str(user_chat_id).strip():
        return str(user_chat_id).strip()

    # 2) buscar por user_id en Firestore
    if user_id and str(user_id).strip():
        return _read_telegram_id_prefer_subscription(str(user_id).strip())

    return None

def _is_uploads_enabled(cfg: Optional[dict]) -> bool:
    cfg = cfg or {}
    # acepta dos formas: plana o anidada en "features"
    if "enable_file_uploads" in cfg:
        return bool(cfg.get("enable_file_uploads"))
    return bool((cfg.get("features") or {}).get("enable_file_uploads"))


#@profile
# ==============================
# procesar_resultado (optimizado)
# ==============================
async def procesar_resultado(
    resultados,
    df_eventos,
    context,
    update,
    moneda_filtro,
    user_id,
    user_chat_id=None,
    opciones_usuario=[],
    origen="telegram",
    exec_id: str | None = None,
    cfg: dict | None = None
):
    # --- CARGA CFG
    if cfg is None:
        cfg, _ = await asyncio.to_thread(
            _load_cfg_and_tz_sync, db, user_id=user_id, chat_id=user_chat_id
        )

    # --- normalizaciones básicas
    origen_norm   = (origen or "app").lower()
    cfg           = cfg or {}
    notifications = cfg.get("notifications") or {}
    send_results  = bool(notifications.get("send_results_telegram"))

    # --- resolver chat_id (telegram_id) usando la prioridad definida
    chat_id  = _resolve_chat_id(user_id, user_chat_id)
    has_chat = bool(chat_id)

    # --- política de envío:
    send_to_tg = has_chat and (
        (origen_norm == "telegram") or
        (origen_norm == "app" and send_results)
    )

    # --- ¿podemos archivar?
    can_archive = bool(exec_id)
    urls_generadas = []

    # >>> registros sin DataFrames ni claves privadas
    registros_limpios = _sanitize_records_for_json(
        [r for r in resultados if isinstance(r, dict)]
    )

    # --- JSON completo (antes de filtrar) ---
    df_resultados = pd.DataFrame(registros_limpios)

        # Serializadores locales (no crean funciones globales)
    def _fmt_toques_cell(v):
        try:

            if isinstance(v, (list, tuple, set, pd.Series)):
                parts = []
                for item in v:
                    if isinstance(item, (list, tuple)) and len(item) >= 2:
                        nivel, cnt = item[0], item[1]
                        try: cnt = int(cnt)
                        except: pass
                        parts.append(f"{_fmt_num(nivel)}×{cnt}")
                    else:
                        parts.append(str(item))
                return " | ".join(parts) if parts else "—"
            if isinstance(v, str):
                return v if v.strip() else "—"
            return "—" if v is None else str(v)
        except Exception:
            return "—" if v is None else str(v)

    def _fmt_niveles_cell(v):
        # admite lista de niveles, lista de tuplas, dicts, etc.
        try:

            if isinstance(v, (list, tuple, set, pd.Series)):
                parts = []
                for item in v:
                    if isinstance(item, (list, tuple)) and item:
                        parts.append(_fmt_num(item[0]))       # solo el nivel
                    elif isinstance(item, dict) and "nivel" in item:
                        parts.append(_fmt_num(item.get("nivel")))
                    else:
                        parts.append(str(item))
                return " | ".join(parts) if parts else "—"
            if isinstance(v, str):
                return v if v.strip() else "—"
            return "—" if v is None else str(v)
        except Exception:
            return "—" if v is None else str(v)

    if "Niveles Confirmados (Toques)" in df_resultados.columns:
        df_resultados["Niveles Confirmados (Toques)"] = df_resultados["Niveles Confirmados (Toques)"].apply(_fmt_toques_cell)

    if "Niveles Confirmados (Nivel)" in df_resultados.columns:
        df_resultados["Niveles Confirmados (Nivel)"] = df_resultados["Niveles Confirmados (Nivel)"].apply(_fmt_niveles_cell)

    if "Niveles Confirmados Reduced (Nivel)" in df_resultados.columns:
        df_resultados["Niveles Confirmados Reduced (Nivel)"] = df_resultados["Niveles Confirmados Reduced (Nivel)"].apply(_fmt_niveles_cell)


    # Ponderaciones
    df_resultados = df_resultados.copy()
    df_resultados = calcular_ponderacion_incremental_por_divisa(df_resultados, cfg)

    df_resultados = df_resultados.copy()
    df_resultados["Ponderacion"] = (
        df_resultados.apply(lambda row: calcular_ponderacion(row, cfg), axis=1)
        .astype(float)
    )

    # Limpia columnas internas si existen
    if not df_resultados.empty:
        df_resultados = df_resultados.drop(
            columns=["bollinger_lower", "bollinger_upper"],
            errors="ignore"
        )

    
    # Ordenado por ponderación
    df_resultados_ordenado = df_resultados.sort_values(
        by="Ponderacion", ascending=False
    )

    # 7) Subir enriquecidos por símbolo/TF
    if can_archive:
        urls_enriched = []
        for res in resultados:
            if not isinstance(res, dict):
                continue

            sym       = res.get("Activo")
            tf        = res.get("Temporalidad")
            df_velas  = res.get("_ohlcv_df")
            df_inds   = res.get("_indicadores_df")
            niveles   = res.get("_niveles") or {}
            entradas  = res.get("_entradas") or {}

            tiene_datos = (
                isinstance(df_velas, pd.DataFrame) and not df_velas.empty
            ) or (isinstance(df_inds, pd.DataFrame) and not df_inds.empty)

            if sym and tf and tiene_datos:
                try:
                    url = await subir_ohlcv_enriquecido_y_registrar(
                        exec_id=exec_id,
                        chat_id=user_chat_id,
                        symbol=sym,
                        temporalidad=tf,
                        df_velas=df_velas if isinstance(df_velas, pd.DataFrame) else pd.DataFrame(),
                        df_indicadores=df_inds if isinstance(df_inds, pd.DataFrame) else None,
                        subir_a_bucket_y_obtener_url=subir_a_bucket_y_obtener_url,
                        niveles=niveles,
                        entradas=entradas,
                        extra_metadata={"moneda_filtro": moneda_filtro},
                        user_id=user_id
                    )
                    if url:
                        urls_enriched.append(url)
                except Exception as e:
                    logger.info(f"No se pudo subir JSON enriquecido de {sym}-{tf}: {e}")
        if urls_enriched:
            urls_generadas.extend(urls_enriched)

    # 8) (Solo app) Subir **ordenado** saneado
    if can_archive:
        df_ord = (
            df_resultados_ordenado
            .replace([np.inf, -np.inf], np.nan)
            .where(pd.notnull(df_resultados_ordenado), None)
            .copy()
        )
        # sanea celdas anidadas si las hubiera
        for col in df_ord.columns:
            if df_ord[col].apply(lambda v: isinstance(v, (dict, list, tuple, set, pd.Series))).any():
                df_ord[col] = df_ord[col].apply(sanitize_for_json)

        ordered_records = sanitize_for_json(df_ord.to_dict("records"))

        url_ordenado = await guardar_json_en_storage_y_registrar(
            exec_id=exec_id,
            chat_id=user_chat_id,
            user_id=user_id,
            nombre_base=f"{moneda_filtro.upper()}_resultados_ordenados",
            data=ordered_records,
            subir_a_bucket_y_obtener_url=subir_a_bucket_y_obtener_url,
            metadata={"moneda_filtro": moneda_filtro, "scope": "ordenado"},
        )
        if url_ordenado:
            urls_generadas.append(url_ordenado)

    # Oportunidades base (solo zona válida)
    df_filtrado = df_resultados_ordenado[
        (df_resultados_ordenado.get('Oportunidad') == True) &
        (df_resultados_ordenado.get('Zona No Trading') == False)
    ].copy()

    # --- JSON oportunidades ---
    if can_archive:
        opp_records = df_filtrado.where(pd.notnull(df_filtrado), None).to_dict("records")
        url_opp = await guardar_json_en_storage_y_registrar(
            exec_id=exec_id,
            chat_id=user_chat_id,
            user_id=user_id,
            nombre_base=f"{moneda_filtro.upper()}_oportunidades",
            data=opp_records,
            subir_a_bucket_y_obtener_url=subir_a_bucket_y_obtener_url,
            metadata={"moneda_filtro": moneda_filtro, "scope": "oportunidades"},
        )
        if url_opp:
            urls_generadas.append(url_opp)

    df_resultadosToImage = pd.DataFrame(df_filtrado)

    # Extraer divisas de los símbolos de las oportunidades
    if 'Activo' in df_filtrado.columns and not df_filtrado.empty:
        divisas_oportunidades = (
            df_filtrado['Activo']
            .astype(str)
            .str.slice(0, 3)
            .dropna()
            .unique()
            .tolist()
        )
    else:
        divisas_oportunidades = []

    # Filtro para imágenes (compras)
    df_filtradoToImage = df_resultadosToImage[
        (df_resultadosToImage.get('Oportunidad') == True) &
        (df_resultadosToImage.get('Zona No Trading') == False) &
        (df_resultadosToImage.get('Tipo de Operacion').isin([
            "Compra", "Compra Fuerte",
            "Compra Predicha con ARIMA y Media Movil",
            "Compra Predicha con Media Movil",
            "Compra Predicha con ARIMA"
        ]))
    ].copy()

    # Quitar columnas según plan, sin romper si faltan
    def _drop_cols(df, cols):
        return df.drop(columns=cols, errors="ignore")

    df_filtradoToImage = _drop_cols(df_filtradoToImage, [
        "Niveles Confirmados (Toques)","Niveles Confirmados (Nivel)",
        "Soportes Importantes Alcanzados","Resistencias Importantes Alcanzadas",
        "Patrones Detectados","Soportes Alcanzados","Resistencias Alcanzadas",
        "Cerca de Soporte Resistencia","Es Rango Repetitivo","Estructura Tendencia",
        "Rebotes","Rango Dinamico","Probabilidad Alza (Montecarlo)","Probabilidad Baja (Montecarlo)",
        "Oportunidad","Zona No Trading","Cruce MACD","MACD Cerca","Bollinger Signal",
        "MACD Tendencia Predicha","Probabilidad Tecnica (%)","Probabilidad Fundamental (%)",
        "Zona Sobreventa RSI-Stochastic","Zona Sobrecompra RSI-Stochastic",
        "Ponderacion","Ponderacion Incremental","Soporte Nivel 2","Soporte Nivel 1",
        "Resistencia Nivel 1","Resistencia Nivel 2","Precio de Entrada","Take Profit","Stop Loss"
    ])
    

    # ---------- ⬇️ LÓGICA “DIVISA PRINCIPAL” RESTAURADA ⬇️ ----------
    # Origen: solo generar artefactos extra (principal/secundaria) si la divisa consultada es "principal"
    # Fuente de categorías: usar 'categorias["Principales"]' si existe; si no, fallback estándar FX
    _cat = (globals().get("categorias") or {})
    _principales = set(map(str.upper, _cat.get("Principales") or
                           ["USD","EUR","JPY","GBP","AUD","CAD","CHF","NZD"]))
    is_principal_moneda = str(moneda_filtro or "").upper() in _principales

    if is_principal_moneda:
        # Dividir por posición de la divisa (prefijo/sufijo)
        df_principal  = df_resultados_ordenado[df_resultados_ordenado["Activo"].astype(str).str.startswith(moneda_filtro.upper())].copy()
        df_secundaria = df_resultados_ordenado[df_resultados_ordenado["Activo"].astype(str).str.endswith(moneda_filtro.upper())].copy()

        nombre_archivo_principal  = generar_nombre_archivo(moneda_filtro, tipo="principal")
        nombre_archivo_secundaria = generar_nombre_archivo(moneda_filtro, tipo="secundaria")

        if not df_principal.empty:
            if origen == "app":
                ruta_local = os.path.join("/tmp", nombre_archivo_principal)
                save_df_as_csv(df_principal, ruta_local, cfg)
                object_path = build_object_path(exec_id, nombre_archivo_principal) if can_archive else nombre_archivo_principal
                url_publica = await subir_a_bucket_y_obtener_url(ruta_local, object_path)
                urls_generadas.append(url_publica)
                if can_archive:
                    await asyncio.to_thread(
                        fs_registrar_archivo_generado,
                        exec_id=exec_id,
                        user_id=user_id,
                        chat_id=user_chat_id,
                        tipo="csv",
                        nombre=nombre_archivo_principal,
                        gcs_path=object_path,
                        signed_url=url_publica,
                        content_type="text/csv",
                        metadata={"moneda_filtro": moneda_filtro, "particion": "principal", "filtrado": False},
                    )
            if send_to_tg:
                if origen == "telegram":
                    asyncio.create_task(enviar_csv_telegram(df_principal, context, nombre_archivo_principal, user_chat_id, cfg=cfg))
                else:
                    await enviar_csv_telegram(df_principal, context, nombre_archivo_principal, user_chat_id, cfg=cfg)
        else:
            logger.info(f"DF principal vacío; no se envía {nombre_archivo_principal}")

        if not df_secundaria.empty:
            if origen == "app":
                ruta_local = os.path.join("/tmp", nombre_archivo_secundaria)
                save_df_as_csv(df_secundaria, ruta_local, cfg)
                object_path = build_object_path(exec_id, nombre_archivo_secundaria) if can_archive else nombre_archivo_secundaria
                url_publica = await subir_a_bucket_y_obtener_url(ruta_local, object_path)
                urls_generadas.append(url_publica)
                if can_archive:
                    await asyncio.to_thread(
                        fs_registrar_archivo_generado,
                        exec_id=exec_id,
                        user_id=user_id,
                        chat_id=user_chat_id,
                        tipo="csv",
                        nombre=nombre_archivo_secundaria,
                        gcs_path=object_path,
                        signed_url=url_publica,
                        content_type="text/csv",
                        metadata={"moneda_filtro": moneda_filtro, "particion": "principal", "filtrado": False},
                    )
            if send_to_tg:
                if origen == "telegram":
                    asyncio.create_task(enviar_csv_telegram(df_secundaria, context, nombre_archivo_secundaria, user_chat_id, cfg=cfg))
                else:
                    await enviar_csv_telegram(df_secundaria, context, nombre_archivo_secundaria, user_chat_id, cfg=cfg)
        else:
            logger.info(f"DF secundaria vacío; no se envía {nombre_archivo_secundaria}")

        # Filtrados por oportunidades (principal/secundaria)
        df_filtrado_principal  = df_filtrado[df_filtrado["Activo"].astype(str).str.startswith(moneda_filtro.upper())].copy()
        df_filtrado_secundaria = df_filtrado[df_filtrado["Activo"].astype(str).str.endswith(moneda_filtro.upper())].copy()

        nombre_archivo_filtrado_principal  = generar_nombre_archivo(moneda_filtro, filtro=True, tipo="principal")
        nombre_archivo_filtrado_secundaria = generar_nombre_archivo(moneda_filtro, filtro=True, tipo="secundaria")

        if not df_filtrado_principal.empty:
            if origen == "app":
                ruta_local = os.path.join("/tmp", nombre_archivo_filtrado_principal)
                save_df_as_csv(df_filtrado_principal, ruta_local, cfg)
                object_path = build_object_path(exec_id, nombre_archivo_filtrado_principal) if can_archive else nombre_archivo_filtrado_principal
                url_publica = await subir_a_bucket_y_obtener_url(ruta_local, object_path)
                urls_generadas.append(url_publica)
                if can_archive:
                    await asyncio.to_thread(
                        fs_registrar_archivo_generado,
                        exec_id=exec_id,
                        user_id=user_id,
                        chat_id=user_chat_id,
                        tipo="csv",
                        nombre=nombre_archivo_filtrado_principal,
                        gcs_path=object_path,
                        signed_url=url_publica,
                        content_type="text/csv",
                        metadata={"moneda_filtro": moneda_filtro, "particion": "principal", "filtrado": True},
                    )
            if send_to_tg:
                if origen == "telegram":
                    asyncio.create_task(enviar_csv_telegram(df_filtrado_principal, context, nombre_archivo_filtrado_principal, user_chat_id, cfg=cfg))
                else:
                    await enviar_csv_telegram(df_filtrado_principal, context, nombre_archivo_filtrado_principal, user_chat_id, cfg=cfg)
        else:
            logger.info(f"DF filtrado principal vacío; no se envía {nombre_archivo_filtrado_principal}")

        if not df_filtrado_secundaria.empty:
            if origen == "app":
                ruta_local = os.path.join("/tmp", nombre_archivo_filtrado_secundaria)
                save_df_as_csv(df_filtrado_secundaria, ruta_local, cfg)
                object_path = build_object_path(exec_id, nombre_archivo_filtrado_secundaria) if can_archive else nombre_archivo_filtrado_secundaria
                url_publica = await subir_a_bucket_y_obtener_url(ruta_local, object_path)
                urls_generadas.append(url_publica)
                if can_archive:
                    await asyncio.to_thread(
                        fs_registrar_archivo_generado,
                        exec_id=exec_id,
                        user_id=user_id,
                        chat_id=user_chat_id,
                        tipo="csv",
                        nombre=nombre_archivo_filtrado_secundaria,
                        gcs_path=object_path,
                        signed_url=url_publica,
                        content_type="text/csv",
                        metadata={"moneda_filtro": moneda_filtro, "particion": "secundaria", "filtrado": True},
                    )
            if send_to_tg:
                if origen == "telegram":
                    asyncio.create_task(enviar_csv_telegram(df_filtrado_secundaria, context, nombre_archivo_filtrado_secundaria, user_chat_id, cfg=cfg))
                else:
                    await enviar_csv_telegram(df_filtrado_secundaria, context, nombre_archivo_filtrado_secundaria, user_chat_id, cfg=cfg)
        else:
            logger.info(f"DF filtrado secundaria vacío; no se envía {nombre_archivo_filtrado_secundaria}")
    else:
        logger.info(f"La divisa '{moneda_filtro}' NO es principal: se omiten artefactos principal/secundaria.")
    # ---------- ⬆️ FIN LÓGICA PRINCIPAL / SECUNDARIA ⬆️ ----------

    # Guardar CSVs “globales”
    nombre_archivo          = generar_nombre_archivo(moneda_filtro)
    nombre_archivo_filtrado = generar_nombre_archivo(moneda_filtro, filtro=True)

    # Asegurar llaves en user_states
    user_states.setdefault(user_chat_id, {})
    if "lock" not in user_states[user_chat_id]:
        user_states[user_chat_id]["lock"] = asyncio.Lock()
        user_states[user_chat_id]["lock_holder"] = None
    for k in ("archivos_enviados","imagenes_oportunidades_enviadas","imagenes_eventos_enviadas"):
        user_states[user_chat_id].setdefault(k, False)

    async with user_states[user_chat_id]["lock"]:
        user_states[user_chat_id]["lock_holder"] = asyncio.current_task()

        # CSV “completo”
        if not df_resultados.empty:
            if origen == "app":
                ruta_local = os.path.join("/tmp", nombre_archivo)
                save_df_as_csv(df_resultados, ruta_local, cfg)
                object_path = build_object_path(exec_id, nombre_archivo) if can_archive else nombre_archivo
                url_publica = await subir_a_bucket_y_obtener_url(ruta_local, object_path)
                urls_generadas.append(url_publica)
                if can_archive:
                    await asyncio.to_thread(
                        fs_registrar_archivo_generado,
                        exec_id=exec_id, user_id=user_id, chat_id=user_chat_id,
                        tipo="csv", nombre=nombre_archivo, gcs_path=object_path,
                        signed_url=url_publica, content_type="text/csv",
                        metadata={"moneda_filtro": moneda_filtro, "particion": "principal", "filtrado": False},
                    )
            if send_to_tg:
                if origen == "telegram":
                    asyncio.create_task(enviar_csv_telegram(df_resultados, context, nombre_archivo, user_chat_id, cfg=cfg))
                else:
                    await enviar_csv_telegram(df_resultados, context, nombre_archivo, user_chat_id, cfg=cfg)
        else:
            logger.info(f"DF df_resultados vacío; no se envía {nombre_archivo}")

        # CSV “filtrado”
        if not df_filtrado.empty:
            if origen == "app":
                ruta_local = os.path.join("/tmp", nombre_archivo_filtrado)
                save_df_as_csv(df_filtrado, ruta_local, cfg)
                object_path = build_object_path(exec_id, nombre_archivo_filtrado) if can_archive else nombre_archivo_filtrado
                url_publica = await subir_a_bucket_y_obtener_url(ruta_local, object_path)
                urls_generadas.append(url_publica)
                if can_archive:
                    await asyncio.to_thread(
                        fs_registrar_archivo_generado,
                        exec_id=exec_id, user_id=user_id, chat_id=(user_chat_id or None),
                        tipo="csv", nombre=nombre_archivo_filtrado, gcs_path=object_path,
                        signed_url=url_publica, content_type="text/csv",
                        metadata={"moneda_filtro": moneda_filtro, "particion": "principal", "filtrado": True},
                    )
            if send_to_tg:
                if origen == "telegram":
                    asyncio.create_task(enviar_csv_telegram(df_filtrado, context, nombre_archivo_filtrado, user_chat_id, cfg=cfg))
                else:
                    await enviar_csv_telegram(df_filtrado, context, nombre_archivo_filtrado, user_chat_id, cfg=cfg)
        else:
            logger.info(f"DF df_filtrado vacío; no se envía {nombre_archivo_filtrado}")

        user_states[user_chat_id]["archivos_enviados"] = True

        # Imágenes de oportunidades
        if user_states[user_chat_id]["archivos_enviados"]:
            if not df_filtradoToImage.empty:
                df_para_imagen = preparar_df_oportunidades_para_tabla(df_filtradoToImage)
                imagenes = tabla_a_imagenes(
                    df_para_imagen,
                    max_filas_por_imagen=18,
                    dpi=170,
                    font_size=9,
                    wrap_map={"Tipo de Operación": 22}
                )
                if imagenes and send_to_tg:
                    for i, img in enumerate(imagenes, 1):
                        try:
                            caption = "Oportunidades relacionadas a los activos seleccionados."
                            if len(imagenes) > 1:
                                caption += f" Parte {i} de {len(imagenes)}"
                            await context.bot.send_photo(chat_id=user_chat_id, photo=img, caption=caption)
                        except Exception as e:
                            logger.info(f"Error enviando imagen de oportunidades: {e}")
            else:
                logger.info("DF df_filtradoToImage vacío; no se envían imágenes.")
            user_states[user_chat_id]["imagenes_oportunidades_enviadas"] = True

            # Imágenes de eventos si aplica
            if user_states[user_chat_id]["imagenes_oportunidades_enviadas"]:
                if not df_eventos.empty and divisas_oportunidades:
                    if send_to_tg:
                        await enviar_imagen_eventos_oportunidades(
                            df_eventos, divisas_oportunidades, context, user_chat_id,
                            moneda_filtro=moneda_filtro
                        )
                else:
                    logger.info("df_eventos vacío o sin divisas_oportunidades válidas.")
                user_states[user_chat_id]["imagenes_eventos_enviadas"] = True

            # ⚠️ Nota: el descuento de transacciones lo moviste a *antes* de ejecutar_analisis_con_hilos.
            # Aquí NO descontamos nada para evitar doble cargo.

    urls_generadas = _solo_strings_urls(urls_generadas)
    logger.info(f"Devolviendo URLs al frontend: {urls_generadas}")
    return urls_generadas


#@profile
def _solo_strings_urls(items: list[Any]) -> list[str]:
    out: list[str] = []
    for it in items:
        if isinstance(it, str):
            out.append(it)
        elif isinstance(it, dict):
            # por si algún helper devuelve {"url": "..."} o {"signed_url": "..."}
            url = it.get("url") or it.get("signed_url")
            if isinstance(url, str):
                out.append(url)
        else:
            logger.warning(f"Descartando elemento no serializable en urls: {type(it)}")
    # deduplicar preservando orden
    seen, uniq = set(), []
    for u in out:
        if u and u not in seen:
            seen.add(u); uniq.append(u)
    return uniq

# Función para obtener el estado de un usuario
#@profile
def obtener_estado_usuario(user_chat_id):
    if user_chat_id not in user_states:
        user_states[user_chat_id] = {"estado": "disponible", "par_seleccionado": None, "cache_realtime": {}, "soportes_resistencias_cache": {}}
    return user_states[user_chat_id]

# Función para actualizar el estado de un usuario
# ----------------- Estado en memoria -----------------
#@profile
def actualizar_estado_usuario(user_chat_id, estado, par_seleccionado=None):
    estado_usuario = obtener_estado_usuario(user_chat_id)
    estado_usuario["estado"] = estado
    estado_usuario["par_seleccionado"] = par_seleccionado
    estado_usuario["soportes_resistencias_cache"] = {}
    user_states[user_chat_id] = estado_usuario

#@profile
def limpiar_estado_usuario(user_chat_id):
    if user_chat_id in user_states:
        user_states[user_chat_id]["estado"] = "disponible"
        user_states[user_chat_id]["par_seleccionado"] = None
        user_states[user_chat_id]["cache_realtime"] = {}

#@profile
def limpiar_soportes_resistencias_cache(user_chat_id):
    if user_chat_id in user_states:
        user_states[user_chat_id]["soportes_resistencias_cache"] = {}
        logger.info(f"Cache de soportes y resistencias reseteado para usuario {user_chat_id}.")
    else:
        # Si no hay estado, inicialízalo como disponible
        user_states[user_chat_id] = {
            "estado": "disponible",
            "soportes_resistencias_cache": {}
        }
        # ¡Ojo! Este es un chat_id, por eso usamos chat_id=... (no user_id)
        mark_user_state(chat_id=user_chat_id, estado="disponible")
        logger.info(f"Estado inicializado para usuario {user_chat_id}.")

# ----------------- Comandos / Flujos Telegram -----------------
#@profile
async def manejar_fecha_eventos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_chat_id = str(update.effective_chat.id)

    chat_ids = await cargar_chat_ids()
    if user_chat_id not in chat_ids:
        await update.message.reply_text("No estás registrado. Por favor, usa /start para registrarte.")
        return
    
    # Suscripción (rama Telegram)
    if await estado_suscripcion(chat_id=user_chat_id) != 'activa' and not es_administrador(user_chat_id):
        await update.message.reply_text(
            "No tiene una suscripción activa o no cuenta con la cuota de transacciones requerida.\n"
            "Por favor, contacta con un administrador."
        )
        return

    # Permisos específicos
    opciones_usuario = await obtener_opciones_usuario(user_chat_id, origen="telegram")
    if not es_administrador(user_chat_id) and (not opciones_usuario or "eventos" not in opciones_usuario):
        await context.bot.send_message(
            chat_id=user_chat_id,
            text="No tienes opciones habilitadas para esta operación. Por favor, adquiere una suscripción."
        )
        return

    # Evitar ejecución concurrente
    estado_actual = return_state(chat_id=user_chat_id)
    if estado_actual == "en ejecución":
        await context.bot.send_message(
            chat_id=user_chat_id,
            text="Ya tienes un análisis en ejecución. Por favor, espera a que termine."
        )
        return

    try:
        # Inicializa el estado
        st = user_states.setdefault(user_chat_id, {})
        st["estado"] = "esperando_fechas"
        st["fecha_inicio"] = None
        st["fecha_fin"] = None
        mark_user_state(chat_id=user_chat_id, estado="esperando_fechas")

        await context.bot.send_message(
            chat_id=user_chat_id,
            text="Por favor, envíame las fechas de inicio y fin en formato YYYY-MM-DD separadas por un espacio."
        )
    except Exception as e:
        logger.info(f"Error al manejar el comando 'eventos_futuros': {e}")

#@profile
async def manejar_fecha_noticias_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_chat_id = str(update.effective_chat.id)

    chat_ids = await cargar_chat_ids()
    if user_chat_id not in chat_ids:
        await update.message.reply_text("No estás registrado. Por favor, usa /start para registrarte.")
        return

    if await estado_suscripcion(chat_id=user_chat_id) != 'activa' and not es_administrador(user_chat_id):
        await update.message.reply_text(
            "No tiene una suscripción activa o no cuenta con la cuota de transacciones requerida.\n"
            "Por favor, contacta con un administrador."
        )
        return
    
    opciones_usuario = await obtener_opciones_usuario(user_chat_id, origen="telegram")
    if not es_administrador(user_chat_id) and (not opciones_usuario or "noticias" not in opciones_usuario):
        await context.bot.send_message(
            chat_id=user_chat_id,
            text="No tienes opciones habilitadas para esta operación. Por favor, adquiere una suscripción."
        )
        return
    
    if return_state(chat_id=user_chat_id) == "en ejecución":
        await context.bot.send_message(
            chat_id=user_chat_id,
            text="Ya tienes un análisis en ejecución. Por favor, espera a que termine."
        )
        return
  
    # Cambiar el estado para capturar fecha+símbolo en el siguiente mensaje
    st = user_states.setdefault(user_chat_id, {})
    st["estado"] = "esperando_fechas_noticias_user"
    st["fecha_inicio"] = None
    st["fecha_fin"] = None
    mark_user_state(chat_id=user_chat_id, estado="esperando_fechas_noticias_user")

    await context.bot.send_message(
        chat_id=user_chat_id,
        text="Envíame una fecha y un símbolo (ej: 2025-09-20 AAPL)."
    )

#@profile
async def manejar_fecha_noticias_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_chat_id = str(update.effective_chat.id)

    # 1) Verificar registro
    chat_ids = await cargar_chat_ids()
    if user_chat_id not in chat_ids:
        await update.message.reply_text("No estás registrado. Por favor, usa /start para registrarte.")
        return

    # 2) Suscripción (rama Telegram) o admin
    try:
        estado_sub = await estado_suscripcion(chat_id=user_chat_id)
    except TypeError:
        # Si tu estado_suscripcion usa firma nueva, descomenta la línea de abajo y comenta la anterior.
        # estado_sub = await estado_suscripcion(user_id=None, chat_id=user_chat_id)
        estado_sub = "activa"  # fallback blando para no romper flujo

    if estado_sub != 'activa' and not es_administrador(user_chat_id):
        await update.message.reply_text(
            "No tiene una suscripción activa o no cuenta con la cuota de transacciones requerida.\n"
            "Por favor, contacta con un administrador."
        )
        return

    # 3) Permisos por opciones
    opciones_usuario = await obtener_opciones_usuario(user_chat_id, origen="telegram")
    if not es_administrador(user_chat_id) and (not opciones_usuario or "noticias" not in opciones_usuario):
        await context.bot.send_message(
            chat_id=user_chat_id,
            text="No tienes opciones habilitadas para esta operación. Por favor, adquiere una suscripción."
        )
        return

    # 4) Evitar ejecuciones simultáneas
    if return_state(chat_id=user_chat_id) == "en ejecución":
        await context.bot.send_message(
            chat_id=user_chat_id,
            text="Ya tienes un análisis en ejecución. Por favor, espera a que termine."
        )
        return

    # 5) Dejar el estado listo para que el próximo mensaje sea la fecha
    st = user_states.setdefault(user_chat_id, {})
    st["estado"] = "esperando_fechas_noticias_admin"
    st["fecha_inicio"] = None
    st["fecha_fin"] = None

    # Si tu mark_user_state acepta 'extra', lo usamos para persistir y sincronizar memoria
    try:
        mark_user_state(
            chat_id=user_chat_id,
            estado="esperando_fechas_noticias_admin",
            extra={"fecha_inicio": None, "fecha_fin": None}
        )
    except TypeError:
        # Compatibilidad si tu versión de mark_user_state no tiene 'extra'
        mark_user_state(chat_id=user_chat_id, estado="esperando_fechas_noticias_admin")

    # 6) Pedir la fecha
    await context.bot.send_message(
        chat_id=user_chat_id,
        text="Envíame una fecha en formato YYYY-MM-DD."
    )


#@profile
async def analizar_simbolo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Inicia el flujo de análisis por símbolo: deja el estado en 'esperando_simbolo'."""
    user_chat_id = str(update.effective_chat.id)

    # Verificar registro
    chat_ids = await cargar_chat_ids()
    if user_chat_id not in chat_ids:
        await update.message.reply_text("No estás registrado. Por favor, usa /start para registrarte.")
        return

    # Suscripción vigente o admin
    if await estado_suscripcion(chat_id=user_chat_id) != 'activa' and not es_administrador(user_chat_id):
        await update.message.reply_text(
            "No tiene una suscripción activa o no cuenta con la cuota de transacciones requerida.\n"
            "Por favor, contacta con un administrador."
        )
        return
    
    # Evitar ejecuciones simultáneas
    if return_state(chat_id=user_chat_id) == "en ejecución":
        await context.bot.send_message(
            chat_id=user_chat_id,
            text="Ya tienes un análisis en ejecución. Por favor, espera a que termine."
        )
        return

    # Dejar listo para que el próximo mensaje sea el símbolo
    st = user_states.setdefault(user_chat_id, {})
    st["estado"] = "esperando_simbolo"
    st["fecha_inicio"] = None
    st["fecha_fin"] = None
    mark_user_state(chat_id=user_chat_id, estado="esperando_simbolo")

    await update.message.reply_text(
        "Por favor, ingresa el símbolo que deseas analizar (ej: AAPL, BTCUSD, EURUSD…)\n"
        "Si tienes dudas, escribe a soporte: manuelt84@gmail.com"
    )
    # (context.user_data puede usarse, pero tu flujo principal mira user_states/Firestore)

# ----------------- Utilidad existente -----------------
#@profile
def analizar_importancia(texto):
    if not texto or pd.isna(texto):
        return "Sin clasificación"
    sentimiento = TextBlob(texto).sentiment.polarity
    if sentimiento > 0.2:
        return "Alta"
    elif sentimiento < -0.2:
        return "Baja"
    return "Media"
    

#@profile
async def manejar_respuesta_fechas(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Maneja las respuestas del usuario según el estado guardado en Firestore/user_states.
    - Usa SIEMPRE chat_id para la rama Telegram (clave en user_states).
    - Llama a obtener_opciones_usuario(..., origen="telegram") para permisos.
    - mark_user_state SIEMPRE con kwargs: chat_id=..., estado="..."
    """
    user_chat_id = str(update.effective_chat.id)

    # Zona horaria por defecto del usuario (telegram)
    global timezone_country
    try:
        tz_name = await cargar_timezone_por_defecto(user_chat_id)
        timezone_country = pytz.timezone(tz_name)
    except Exception:
        timezone_country = pytz.utc


    # Si no hay un estado esperando, cortamos.
    current_state = return_state(chat_id=user_chat_id)
    if current_state == "disponible":
        await update.message.reply_text("Por favor, usa el comando adecuado primero.")
        return

    estado_firestore = current_state

    # ───────────────────────────────
    # 1) Espera de símbolo para análisis puntual
    # ───────────────────────────────
    if estado_firestore == "esperando_simbolo":
        try:
            await update.message.reply_text("Empezamos a obtener la información, espera un momento por favor.")

            simbolo = (update.message.text or "").strip()
            if not simbolo:
                raise ValueError("El símbolo no puede estar vacío.")

            # Permisos del usuario (rama Telegram)
            opciones_usuario = await obtener_opciones_usuario(user_chat_id, origen="telegram")

            # Lanza la ejecución en background
            asyncio.create_task(
                ejecutar_recurrente(
                    context, update, simbolo.upper(),
                    user_chat_id=user_chat_id,
                    opciones_usuario=opciones_usuario,
                    origen="telegram"
                )
            )
        except Exception as e:
            await update.message.reply_text(f"Hubo un error procesando el símbolo: {e}")
        finally:
            # Limpieza de estado local (el runner actualizará a 'en ejecución' cuando corresponda)
            if user_chat_id in user_states:
                user_states[user_chat_id]["estado"] = "disponible"
            mark_user_state(chat_id=user_chat_id, estado="disponible")
        return

    # ───────────────────────────────
    # 2) Rango de fechas (eventos económicos por rango)
    # ───────────────────────────────
    if estado_firestore == "esperando_fechas":
        uid_chat = user_chat_id  # clave consistente para Telegram
        try:
            await update.message.reply_text("Empezamos a obtener la información, espera un momento por favor.")

            # Estructura de estado segura
            state = user_states.setdefault(uid_chat, {})
            state.setdefault("estado", "disponible")
            state.setdefault("links_enviados", False)
            state.setdefault("imagenes_enviadas", False)
            state.setdefault("lock", asyncio.Lock())
            state.setdefault("lock_holder", None)
            state["fecha_inicio"] = None
            state["fecha_fin"] = None

            partes = (update.message.text or "").split()
            if len(partes) != 2:
                raise ValueError("Debes ingresar dos fechas en formato YYYY-MM-DD YYYY-MM-DD.")

            fecha_inicio = pd.to_datetime(partes[0], format="%Y-%m-%d", errors='coerce')
            fecha_fin    = pd.to_datetime(partes[1], format="%Y-%m-%d", errors='coerce')
            if pd.isnull(fecha_inicio) or pd.isnull(fecha_fin):
                raise ValueError("Formato de fecha inválido.")
            if fecha_inicio > fecha_fin:
                raise ValueError("La fecha de inicio debe ser menor o igual a la fecha de fin.")

            hoy_local = datetime.now(timezone_country).date()
            if (fecha_fin.date() - fecha_inicio.date()).days > 7:
                raise ValueError("El rango de fechas no puede exceder 7 días para eventos.")
            if fecha_inicio.date() < (hoy_local - timedelta(days=14)):
                raise ValueError("El rango de fechas no puede superar 14 días en el pasado para eventos.")
            if fecha_fin.date() > (hoy_local + timedelta(days=14)):
                raise ValueError("El rango de fechas no puede superar 14 días en el futuro para eventos.")

            state["fecha_inicio"] = fecha_inicio
            state["fecha_fin"] = fecha_fin
            actualizar_estado_usuario(uid_chat, "en ejecución")
            mark_user_state(chat_id=uid_chat, estado="en ejecución")

            # Traer/filtrar eventos
            df_eventos = obtener_eventos_guardados_o_futuros(fecha_inicio, fecha_fin)
            if df_eventos is None or getattr(df_eventos, "empty", True):
                await update.message.reply_text(
                    f"No se encontraron eventos económicos entre {fecha_inicio.strftime('%Y-%m-%d')} y {fecha_fin.strftime('%Y-%m-%d')}."
                )
            else:
                async with state["lock"]:
                    state["lock_holder"] = asyncio.current_task()
                    await enviar_imagenes_por_currency_a_usuario(df_eventos, context, uid_chat)
                    state["imagenes_enviadas"] = True

                    if state["imagenes_enviadas"]:
                        asyncio.create_task(enviar_eventos_y_archivo_calendar(df_eventos, context, uid_chat))
                        state["links_enviados"] = True

                    if not es_administrador(uid_chat):
                        success, mensaje = await descontar_transaccion(uid_chat, 1, origen="telegram")
                        if not success:
                            await update.message.reply_text(mensaje)
        except Exception as e:
            await update.message.reply_text(f"Hubo un error procesando las fechas: {e}")
        finally:
            if uid_chat in user_states:
                user_states[uid_chat]["fecha_inicio"] = None
                user_states[uid_chat]["fecha_fin"] = None
                user_states[uid_chat]["estado"] = "disponible"
            mark_user_state(chat_id=uid_chat, estado="disponible")
        return

    # ───────────────────────────────
    # 3) Noticias por fecha + símbolo (usuario)
    # ───────────────────────────────
    if estado_firestore == "esperando_fechas_noticias_user":
        try:
            await update.message.reply_text("Empezamos a obtener la información, espera un momento por favor.")

            partes = (update.message.text or "").split()
            if len(partes) != 2:
                raise ValueError("Debes ingresar una fecha y un símbolo en formato: YYYY-MM-DD SIMBOLO")

            # Fecha con tz local (aware)
            fecha_inicio = pd.to_datetime(partes[0], format="%Y-%m-%d", errors='coerce')
            if pd.isnull(fecha_inicio):
                raise ValueError("Formato de fecha inválido.")
            # Aware en tz del usuario
            fecha_inicio = fecha_inicio.tz_localize(pytz.UTC)
            fecha_fin = fecha_inicio

            hoy_local = datetime.now(timezone_country).date()
            if fecha_fin.date() > hoy_local:
                raise ValueError("La fecha no puede ser mayor que hoy para noticias.")

            # Inicializa en estado local
            st = user_states.setdefault(user_chat_id, {})
            st["fecha_inicio"] = fecha_inicio
            st["fecha_fin"] = fecha_fin
            actualizar_estado_usuario(user_chat_id, "en ejecución")
            mark_user_state(chat_id=user_chat_id, estado="en ejecución")

            symbol = partes[1].upper()
            noticias = obtener_noticias_simbolo(symbol, fecha_inicio, fecha_fin, limite=15)

            if noticias is None or getattr(noticias, "empty", True):
                await update.message.reply_text(
                    "No se encontraron noticias en la fecha indicada para el símbolo ingresado."
                )
                return

            if not all(col in noticias.columns for col in ['symbol', 'publishedDate', 'url', 'title']):
                logger.info(f"Columnas inesperadas para {symbol}: {list(noticias.columns)}")
                return

            # Filtrar por el día exacto (en tz local)
            noticias_del_dia = noticias[noticias['publishedDate'].dt.date == fecha_inicio.date()]
            if noticias_del_dia.empty:
                await update.message.reply_text("No se encontraron noticias publicadas en la fecha ingresada.")
                return

            for _, noticia in noticias_del_dia.iterrows():
                title = noticia.get('title', '')
                sitio = noticia.get('site', 'No especificado')
                text = noticia.get('text', 'Sin Descripción') or 'Sin Descripción'
                symbol = noticia.get('symbol', symbol)
                fecha = noticia['publishedDate']
                try:
                    fecha_str = fecha.strftime('%Y-%m-%d %H:%M:%S')
                except Exception:
                    fecha_str = str(fecha)
                importancia = analizar_importancia(f"{title} {text}")
                url = noticia.get('url', '')
                link_traductor = f"https://translate.google.com/translate?sl=auto&tl=es&u={url}"

                mensaje = (
                    f"Titulo: {title}\n"
                    f"Descripción: {text}\n"
                    f"Activo: {symbol}\n"
                    f"Fecha: {fecha_str}\n"
                    f"Sitio: {sitio}\n"
                    f"Importancia: {importancia}\n"
                    f"Link: {url}\n"
                    f"Link Traducido: {link_traductor}\n"
                )
                await enviar_mensaje_noticias(context, user_chat_id, mensaje)

            if not es_administrador(user_chat_id):
                success, mensaje = await descontar_transaccion(user_chat_id, 1, origen="telegram")
                if not success:
                    await update.message.reply_text(mensaje)
        except ValueError as e:
            await update.message.reply_text(f"Error: {e}")
        except Exception as e:
            await update.message.reply_text(f"Hubo un error procesando las fechas para noticias: {e}")
        finally:
            if user_chat_id in user_states:
                user_states[user_chat_id]["fecha_inicio"] = None
                user_states[user_chat_id]["fecha_fin"] = None
                user_states[user_chat_id]["estado"] = "disponible"
            mark_user_state(chat_id=user_chat_id, estado="disponible")
        return

    # ───────────────────────────────
    # 4) Noticias por fecha (admin, recorre varios símbolos)
    # ───────────────────────────────
    if estado_firestore == "esperando_fechas_noticias_admin":
        try:
            await update.message.reply_text("Empezamos a obtener la información, espera un momento por favor.")

            partes = (update.message.text or "").split()
            if len(partes) != 1:
                raise ValueError("Debes ingresar una fecha en formato YYYY-MM-DD.")

            fecha_inicio = pd.to_datetime(partes[0], format="%Y-%m-%d", errors='coerce')
            if pd.isnull(fecha_inicio):
                raise ValueError("Formato de fecha inválido.")
            fecha_inicio = fecha_inicio.tz_localize(pytz.UTC)
            fecha_fin = fecha_inicio

            hoy_local = datetime.now(timezone_country).date()
            if fecha_fin.date() > hoy_local:
                raise ValueError("La fecha no puede ser mayor que hoy para noticias.")

            st = user_states.setdefault(user_chat_id, {})
            st["fecha_inicio"] = fecha_inicio
            st["fecha_fin"] = fecha_fin
            actualizar_estado_usuario(user_chat_id, "en ejecución")
            mark_user_state(chat_id=user_chat_id, estado="en ejecución")

            activos_filtrados = filtrar_activos_por_moneda(activos, 'todos')
            hubo_algo = False

            for symbol in activos_filtrados:
                noticias = obtener_noticias_simbolo(symbol, fecha_inicio, fecha_fin, limite=2)
                if noticias is None or getattr(noticias, "empty", True):
                    continue
                if not all(col in noticias.columns for col in ['symbol', 'publishedDate', 'url', 'title']):
                    logger.info(f"Columnas inesperadas para {symbol}: {list(noticias.columns)}")
                    continue

                noticias_del_dia = noticias[noticias['publishedDate'].dt.date == fecha_inicio.date()]
                if noticias_del_dia.empty:
                    continue

                hubo_algo = True
                for _, noticia in noticias_del_dia.iterrows():
                    title = noticia.get('title', '')
                    sitio = noticia.get('site', 'No especificado')
                    text = noticia.get('text', 'Sin Descripción') or 'Sin Descripción'
                    sym  = noticia.get('symbol', symbol)
                    fecha = noticia['publishedDate']
                    try:
                        fecha_str = fecha.strftime('%Y-%m-%d %H:%M:%S')
                    except Exception:
                        fecha_str = str(fecha)
                    importancia = analizar_importancia(f"{title} {text}")
                    url = noticia.get('url', '')
                    link_traductor = f"https://translate.google.com/translate?sl=auto&tl=es&u={url}"

                    mensaje = (
                        f"Titulo: {title}\n"
                        f"Descripción: {text}\n"
                        f"Activo: {sym}\n"
                        f"Fecha: {fecha_str}\n"
                        f"Sitio: {sitio}\n"
                        f"Importancia: {importancia}\n"
                        f"Link: {url}\n"
                        f"Link Traducido: {link_traductor}\n"
                    )
                    await enviar_mensaje_noticias(context, user_chat_id, mensaje)

            if not hubo_algo:
                await update.message.reply_text("No se encontraron noticias en la fecha indicada.")
                return

            if not es_administrador(user_chat_id):
                success, mensaje = await descontar_transaccion(user_chat_id, 1, origen="telegram")
                if not success:
                    await update.message.reply_text(mensaje)
        except Exception as e:
            await update.message.reply_text(f"Hubo un error procesando las fechas para noticias: {e}")
        finally:
            if user_chat_id in user_states:
                user_states[user_chat_id]["fecha_inicio"] = None
                user_states[user_chat_id]["fecha_fin"] = None
                user_states[user_chat_id]["estado"] = "disponible"
            mark_user_state(chat_id=user_chat_id, estado="disponible")
        return

    # ───────────────────────────────
    # 5) Modo envío de mensaje (admin)
    # ───────────────────────────────
    if estado_firestore == "modo_envio_mensaje":
        try:
            mensaje_usuario = update.message.text if update.message.text else update.message.caption
            archivos_guardados = []

            if update.message.photo:
                imagen = update.message.photo[-1]
                archivos_guardados.append({"tipo": "imagen", "file_id": imagen.file_id})

            if update.message.video:
                video = update.message.video
                archivos_guardados.append({"tipo": "video", "file_id": video.file_id})

            if update.message.document:
                doc = update.message.document
                archivos_guardados.append({"tipo": "documento", "file_id": doc.file_id})

            # Resolver user_id (si tienes mapeo chat->user)
            user_id_val = None
            try:
                user_id_val = str(_user_id_from_chat(user_chat_id))
            except Exception:
                user_id_val = None

            # Guardar mensaje y adjuntos en Firestore (estado del admin)
            ref = _user_state_doc(user_id=user_id_val, chat_id=user_chat_id)
            snap = ref.get()
            user_data = snap.to_dict() if getattr(snap, "exists", False) else {}
            destinatario_manual = user_data.get("destinatario_manual")

            ref.set({
                "mensaje_admin": mensaje_usuario,
                "archivos_guardados": archivos_guardados,
                "destinatario_manual": destinatario_manual
            }, merge=True)

            keyboard = [
                [InlineKeyboardButton("✅ Confirmar Envío", callback_data="confirmar_envio")],
                [InlineKeyboardButton("❌ Cancelar", callback_data="cancelar_envio_mensaje")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await update.message.reply_text("📩 ¿Confirmas el envío del mensaje?", reply_markup=reply_markup)
        except Exception as e:
            await update.message.reply_text(f"Hubo un error procesando el envío del mensaje: {e}")
        finally:
            mark_user_state(chat_id=user_chat_id, estado="disponible")
        return

    # ───────────────────────────────
    # 6) Esperando id de usuario para envío
    # ───────────────────────────────
    if estado_firestore == "esperando_id_usuario":
        try:
            await recibir_usuario_especifico(update, context)
        except Exception as e:
            await update.message.reply_text(f"Hubo un error procesando el envío del mensaje a un usuario específico: {e}")
        finally:
            mark_user_state(chat_id=user_chat_id, estado="disponible")
        return

    # ───────────────────────────────
    # 7) Análisis de gráfico con IA a partir de imagen
    # ───────────────────────────────
    if estado_firestore == "esperando_grafico_ia":
        ruta_local = None
        ruta_salida = None
        try:
            if not update.message.photo:
                await update.message.reply_text("⚠️ Por favor, sube una imagen válida.")
                return

            archivo = await update.message.photo[-1].get_file()
            os.makedirs("imagenes", exist_ok=True)
            os.makedirs("procesadas", exist_ok=True)

            ruta_local = f"imagenes/{update.effective_user.id}.jpg"
            await archivo.download_to_drive(ruta_local)

            if not es_grafico_de_velas(ruta_local):
                await update.message.reply_text("❌ No parece ser un gráfico de velas. Intenta con otra imagen.")
                return

            await update.message.reply_text("Empezó el análisis...")

            ruta_salida, texto_resultado = analizar_con_yolo(ruta_local)

            with open(ruta_salida, 'rb') as foto:
                await update.message.reply_photo(photo=foto)
                await update.message.reply_text(texto_resultado)

            if not es_administrador(user_chat_id):
                success, mensaje = await descontar_transaccion(user_chat_id, 1, origen="telegram")
                if not success:
                    await update.message.reply_text(mensaje)
        except Exception as e:
            await update.message.reply_text(f"Hubo un error analizando la imagen: {e}")
        finally:
            mark_user_state(chat_id=user_chat_id, estado="disponible")
            try:
                if ruta_local and os.path.exists(ruta_local):
                    os.remove(ruta_local)
                if ruta_salida and os.path.exists(ruta_salida):
                    os.remove(ruta_salida)
            except Exception as cleanup_error:
                print(f"⚠️ Error al eliminar archivos temporales: {cleanup_error}")
        return



#@profile
async def recibir_usuario_especifico(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Guarda el ID del usuario específico y activa el modo de envío de mensaje."""
    user_chat_id = str(update.effective_chat.id)
    message_text = update.message.text.strip()

    if not message_text.isdigit():
        await update.message.reply_text("⚠️ Ingresa un ID de usuario válido (solo números).")
        return
    
    # Guardar ID del usuario en Firestore directamente
    user_ref = _user_state_doc(user_id=user_id, chat_id=user_chat_id)
    user_ref.set({"destinatario_manual": message_text}, merge=True)  # 👈 Se guarda aquí manualmente

    # Guardar ID en Firestore y activar el modo de envío de mensaje
    mark_user_state(chat_id=user_chat_id, estado="modo_envio_mensaje")
    await update.message.reply_text(f"✅ Usuario {message_text} seleccionado. Ahora envía el mensaje o archivo.")


#@profile
async def enviar_mensaje_noticias(context, user_chat_id, mensaje):
    try:
        await context.bot.send_message(chat_id=user_chat_id, text=mensaje)
        await asyncio.sleep(TIME_BETWEEN_MESSAGES)  # Esperar antes de enviar el siguiente mensaje
    except Exception as e:
        logger.info(f"Error al enviar mensaje: {e}")
        if "Flood control exceeded" in str(e):
            # Implementar un tiempo de espera más largo y reintentar
            await asyncio.sleep(10)  # Esperar 10 segundos antes de volver a intentar
            await enviar_mensaje_noticias(context, user_chat_id, mensaje)


def _find_user_id_for_chat_sync(db, chat_id: str) -> str | None:
    chat_id = (chat_id or "").strip()
    if not chat_id:
        return None

    # A0) alias directo: suscripciones_user/{chat_id}
    try:
        alias_snap = db.collection("suscripciones_user").document(chat_id).get()
        if alias_snap.exists:
            d = alias_snap.to_dict() or {}
            uid = d.get("doc_alias_of") or d.get("user_id")
            if uid:
                return str(uid)
    except Exception:
        pass

    # A) mapping directo: chat_ids/{chat_id} => { user_id }
    try:
        doc = db.collection("chat_ids").document(chat_id).get()
        if doc.exists:
            uid = (doc.to_dict() or {}).get("user_id")
            if uid:
                return str(uid)
    except Exception:
        pass

    # B) buscar canónico por telegram_id == chat_id
    try:
        q = db.collection("suscripciones_user").where("telegram_id", "==", chat_id).limit(1).get()
        if q:
            return str(q[0].id)   # id del canónico = user_id
    except Exception:
        pass

    # C) fallback: user_ids por telegram_id
    try:
        q = db.collection("user_ids").where("telegram_id", "==", chat_id).limit(1).get()
        if q:
            return str(q[0].id)
    except Exception:
        pass

    return None

def _load_cfg_and_tz_sync(db, *, user_id: str | None, chat_id: str | None):
    """
    Lee cfg + timezone desde Firestore.
    Retorna: (cfg_dict, tz_name_str)  -- cfg puede ser {}
    """
    uid = (user_id or "").strip()
    if not uid and chat_id:
        uid = _find_user_id_for_chat_sync(db, chat_id)

    if not uid:
        return {}, "UTC"

    user_ref = db.collection("user_ids").document(uid)
    cfg_ref  = user_ref.collection("user_config").document("current")

    doc_user, doc_cfg = list(db.get_all([user_ref, cfg_ref]))

    cfg = (doc_cfg.to_dict() or {})  # puede incluir notifications, features.enable_file_uploads, etc.
    tz_name = (doc_user.to_dict() or {}).get("timezone") or "UTC"

    return cfg, tz_name


def _find_chat_id_for_user_sync(db, user_id: str | None) -> str | None:
    """Resuelve chat_id (telegram_id) a partir de un user_id (UUID de app)."""
    uid = (user_id or "").strip()
    if not uid:
        return None

    # A) mapping directo: chat_ids/* con campo user_id == uid
    try:
        q = db.collection("chat_ids").where("user_id", "==", uid).limit(1).get()
        if q:
            # id del doc es el chat_id
            return str(q[0].id)
    except Exception:
        pass

    # B) user_ids/{uid}.telegram_id
    try:
        d = db.collection("user_ids").document(uid).get()
        if d.exists:
            tg = (d.to_dict() or {}).get("telegram_id")
            if tg:
                return str(tg)
    except Exception:
        pass

    # C) suscripciones_user/{uid}.telegram_id
    try:
        d = db.collection("suscripciones_user").document(uid).get()
        if d.exists:
            tg = (d.to_dict() or {}).get("telegram_id")
            if tg:
                return str(tg)
    except Exception:
        pass

    return None

#@profile
# ==============================
# ejecutar_recurrente (optimizado)
# ==============================
async def ejecutar_recurrente( 
    context,
    update,
    moneda_filtro,
    user_chat_id: str | None = None,
    opciones_usuario: list = [],
    user_id: str | None = None,             # UUID de la app (preferido)
    origen: str = "telegram",
    exec_id: str | None = None,
    operatoria_cfg: dict | None = None,
    cfg: dict | None = None,
):
    """
    Ejecuta análisis para un conjunto de activos filtrados por moneda.
    - Prioriza user_id (UUID). Si no hay, opera con user_chat_id (telegram).
    - Marca estado 'en ejecución' y libera al final.
    - AHORA: descuenta transacciones justo antes de ejecutar_analisis_con_hilos.
    """

    logger = logging.getLogger(__name__)
    error_occurred = False
    url_generadas = None

    # --- CFG base (carga perezosa)
    if cfg is None:
        cfg, _ = await asyncio.to_thread(
            _load_cfg_and_tz_sync, db, user_id=user_id, chat_id=user_chat_id
        )

    # --- Resolver chat_id si falta y hay user_id
    if not user_chat_id and user_id:
        user_chat_id = await asyncio.to_thread(_find_chat_id_for_user_sync, db, user_id)

    # --- Filtrado de activos por moneda
    global activos
    try:
        activos_filtrados = filtrar_activos_por_moneda(activos, moneda_filtro)
    except Exception as e:
        logger.error(f"Error filtrando activos por moneda '{moneda_filtro}': {e}", exc_info=True)
        activos_filtrados = []

    # --- Normalizaciones
    origen_norm   = (origen or "app").lower()
    cfg           = cfg or {}
    opciones_usuario = list(opciones_usuario or [])
    notifications = cfg.get("notifications") or {}
    send_results  = bool(notifications.get("send_results_telegram"))
    has_chat      = bool(user_chat_id)

    # Política de envío a Telegram
    send_to_tg = has_chat and (
        origen_norm == "telegram" or (origen_norm == "app" and send_results)
    )

    # Estado local por chat
    if user_chat_id and user_chat_id not in user_states:
        user_states[user_chat_id] = {}

    cfg_overrides = operatoria_cfg or {}
    temps = cfg_overrides.get("tfs") or temporalidades

    # Contabilizar transacciones (activo x tf)
    n = len(activos_filtrados) * len(temps)
    if user_id:
        user_states.setdefault(str(user_id), {})["numero_transacciones"] = n
    if user_chat_id:
        user_states.setdefault(str(user_chat_id), {})["numero_transacciones"] = n

    # Sin activos
    if not activos_filtrados:
        if send_to_tg:
            try:
                await context.bot.send_message(
                    chat_id=user_chat_id,
                    text="No se encontraron activos para analizar con el filtro especificado."
                )
            except Exception as e:
                logger.warning(f"No se pudo enviar mensaje Telegram (sin activos): {e}")
        return

    # --- Validación de suscripción previa (no descuenta aún)
    estado = await estado_suscripcion(
        user_id=user_id,
        chat_id=user_chat_id or None,
        numero_transacciones=n if n > 0 else 1,
    )
    estado_code = None
    if isinstance(estado, str):
        estado_code = estado
    elif isinstance(estado, dict):
        if "code" in estado:
            estado_code = estado["code"]
        elif estado.get("active") is False and estado.get("reason"):
            estado_code = estado["reason"]

    if estado_code == "transacciones_insuficientes" and not es_administrador(user_id or user_chat_id):
        if send_to_tg:
            try:
                await context.bot.send_message(
                    chat_id=user_chat_id,
                    text="No cuenta con la cuota de transacciones requerida. Por favor, contacta con un administrador."
                )
            except Exception as e:
                logger.warning(f"No se pudo enviar mensaje Telegram (cuota): {e}")
        return

    # --- Evitar ejecuciones duplicadas por chat
    if user_chat_id and return_state(chat_id=user_chat_id) == "en ejecución":
        if send_to_tg:
            try:
                await context.bot.send_message(
                    chat_id=user_chat_id,
                    text="Ya tienes un análisis en ejecución. Por favor, espera a que termine."
                )
            except Exception as e:
                logger.warning(f"No se pudo enviar mensaje Telegram (duplicado): {e}")
        return

    # --- Guardar operatoria_cfg en memoria (si hay chat)
    if user_chat_id:
        user_states[user_chat_id]["operatoria_cfg"] = dict(operatoria_cfg or {})

    # --- Saludo
    if send_to_tg:
        try:
            user = getattr(update, "effective_user", None)
            first_name = getattr(user, "first_name", "") if user else ""
            await context.bot.send_message(
                chat_id=user_chat_id,
                text=f"Hola {first_name}, comenzó el análisis. Por favor, espera un momento..."
            )
        except Exception as e:
            logger.warning(f"No se pudo enviar mensaje de saludo: {e}")

    # --- Crear exec en FS si corresponde (solo auto en origen telegram y con uploads habilitados)
    can_archive = False
    if origen_norm == "telegram" and exec_id is None and _is_uploads_enabled(cfg):
        try:
            activos_solicitados = [moneda_filtro] if moneda_filtro else []
            exec_id = await asyncio.to_thread(
                fs_crear_ejecucion,
                user_id=user_id,
                chat_id=user_chat_id or None,
                activos_solicitados=activos_solicitados,
                origen="telegram",
                opciones_usuario=opciones_usuario,
            )
        except Exception as e:
            logger.warning(f"No se pudo crear exec_id auto (telegram): {e}", exc_info=True)
            exec_id = None

    can_archive = bool(exec_id)

    # =======================
    # ARCHIVADO inicial (si hay exec)
    # =======================
    if can_archive:
        try:
            db.collection("ejecuciones").document(exec_id).set({
                "exec_id": exec_id,
                "user_id": user_id,
                "chat_id": user_chat_id or None,
                "origen": origen_norm,
                "moneda_filtro": moneda_filtro,
                "cfg_snapshot": cfg,
                "estado": "running",
                "created_at": _now_utc().isoformat().replace("+00:00", "Z"),
                "updated_at": _now_utc().isoformat().replace("+00:00", "Z")
            }, merge=True)
        except Exception as e:
            logger.warning(f"No se pudo setear documento base de ejecución {exec_id}: {e}")

    # --- Marcar estados en memoria y remoto
    actualizar_estado_usuario(user_chat_id, "en ejecución", moneda_filtro)
    try:
        if user_id:
            mark_user_state(user_id=user_id, estado="en ejecución")
        elif user_chat_id:
            mark_user_state(chat_id=user_chat_id, estado="en ejecución")
    except Exception as e:
        logger.warning(f"No se pudo marcar estado remoto 'en ejecución': {e}")

    if user_chat_id:
        try:
            limpiar_soportes_resistencias_cache(user_chat_id)
            estado_usuario = obtener_estado_usuario(user_chat_id)
            estado_usuario["cache_realtime"] = {}
        except Exception as e:
            logger.warning(f"No se pudo limpiar/sincronizar cache_realtime: {e}")

    logger.info(
        f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Ejecutando análisis "
        f"chat={user_chat_id} uuid={user_id} activos={len(activos_filtrados)} temps={len(temps)}"
    )

    # --- Actualizar metadata si vino exec_id externo
    if exec_id:
        try:
            await asyncio.to_thread(
                fs_actualizar_ejecucion,
                exec_id,
                activos_resueltos=activos_filtrados,
                numero_transacciones=n,
            )
        except Exception as e:
            logger.warning(f"No se pudo actualizar ejecución {exec_id}: {e}")

    # === Descontar transacciones JUSTO ANTES del análisis ===
    try:
        # ✅ Preferimos cobrar por user_id (doc id en suscripciones_user). Si no hay, usamos chat_id.
        identificador_cobro = (str(user_id) if user_id else None) or (str(user_chat_id) if user_chat_id else None)

        if not es_administrador(user_id or user_chat_id) and identificador_cobro:
            success, mensaje = await descontar_transaccion(
                identificador_cobro,
                numero_transacciones_in=n,
                origen=origen_norm,  # para logs/telemetría
            )
            if not success:
                error_occurred = True
                if send_to_tg and context:
                    try:
                        await context.bot.send_message(chat_id=user_chat_id, text=mensaje or "No fue posible descontar transacciones.")
                    except Exception:
                        pass
                if can_archive:
                    try:
                        await asyncio.to_thread(
                            fs_finalizar_ejecucion,
                            exec_id,
                            "fallido",
                            {"error": "saldo_insuficiente"}
                        )
                    except Exception:
                        pass
                return
    except Exception as e:
        error_occurred = True
        logger.error(f"Error al descontar transacciones: {e}", exc_info=True)
        if can_archive:
            try:
                await asyncio.to_thread(
                    fs_finalizar_ejecucion,
                    exec_id,
                    "fallido",
                    {"error": f"descuento_fallido: {e}"}
                )
            except Exception:
                pass
        # liberar estados al final del finally

    # --- Ejecución principal ---
    try:
        if error_occurred:
            return  # ya se manejó y se liberará estado en finally

        start_time = datetime.now()

        # Eventos económicos (tolerante a error)
        try:
            df_eventos = obtener_eventos_economicos()
        except Exception as e:
            logger.warning(f"Error al obtener eventos económicos: {e}")
            df_eventos = None

        resultados = await ejecutar_analisis_con_hilos(
            df_eventos,
            activos_filtrados,
            user_chat_id,
            context,
            overrides={
                "tfs":         (operatoria_cfg or {}).get("tfs"),
                "fmpWindows":  (operatoria_cfg or {}).get("fmpWindows"),
                "calcWindows": (operatoria_cfg or {}).get("calcWindows"),
            },
            cfg=cfg,
        )

        if not resultados:
            if send_to_tg:
                try:
                    await context.bot.send_message(
                        chat_id=user_chat_id,
                        text="El análisis no produjo resultados. Verifique los datos y vuelva a intentarlo."
                    )
                except Exception as e:
                    logger.warning(f"No se pudo enviar mensaje Telegram (sin resultados): {e}")
            return

        url_generadas = await procesar_resultado(
            resultados, df_eventos, context, update,
            moneda_filtro, user_id, user_chat_id, opciones_usuario, origen,
            exec_id=exec_id, cfg=cfg
        )

        elapsed_time = (datetime.now() - start_time).total_seconds()
        logger.info(
            f"[{datetime.now()}] Análisis finalizado (chat={user_chat_id}, uuid={user_id}). "
            f"Tiempo: {elapsed_time:.2f}s."
        )
        return url_generadas

    except Exception as e:
        error_occurred = True
        logger.error(f"Error durante la ejecución principal: {e}", exc_info=True)
        if can_archive:
            try:
                await asyncio.to_thread(
                    fs_finalizar_ejecucion,
                    exec_id,
                    "fallido",
                    {"error": str(e)}
                )
            except Exception as e2:
                logger.warning(f"No se pudo marcar fallido exec_id={exec_id}: {e2}")

    finally:
        # Cierre de ejecución (solo si no se marcó como fallido/abortado arriba)
        if can_archive and not error_occurred:
            try:
                await asyncio.to_thread(
                    fs_finalizar_ejecucion,
                    exec_id,
                    "completado",
                    {"origen": origen_norm, "urls": url_generadas or []}
                )
            except Exception as e:
                logger.warning(f"No se pudo marcar completado exec_id={exec_id}: {e}")

        # Limpieza estado local por chat
        if user_chat_id:
            try:
                limpiar_estado_usuario(user_chat_id)
            except Exception as e:
                logger.warning(f"Error limpiando estado local del usuario {user_chat_id}: {e}")

        # Liberar estado remoto
        try:
            if user_id:
                mark_user_state(user_id=user_id, estado="disponible")
            elif user_chat_id:
                mark_user_state(chat_id=user_chat_id, estado="disponible")
        except Exception as e:
            logger.warning(f"No se pudo marcar usuario disponible: {e}")


#@profile
async def stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Maneja el comando /stop para eliminar el chat_id del registro."""
    user_chat_id = str(update.effective_chat.id)
    
    await resetear_menu_usuario(context, user_chat_id)

    await eliminar_chat_id(user_chat_id)

    await update.message.reply_text("Has sido eliminado del registro. Si deseas volver a usar el bot, usa el comando /start.")

#@profile
async def start(update: Update, context):
    """Función que maneja el comando /start y guarda el chat_id del usuario."""
    user_chat_id = str(update.effective_chat.id)
    
    user = update.effective_user.first_name

    # Verificar si el usuario ya está registrado
    chat_ids = await cargar_chat_ids()
    if user_chat_id not in chat_ids:
        # Registrar nuevo usuario
        await guardar_chat_id(user_chat_id, user)

        if not es_administrador(user_chat_id):
            await menu_usuario_registrado(context.bot, user_chat_id)
        elif es_administrador(user_chat_id):
            await menu_usuario_administrador(context, user_chat_id)

        await update.message.reply_text(f"Hola {user}, bienvenido al bot. Has sido registrado.")
    else:
        await update.message.reply_text(f"Hola {user}, bienvenido de nuevo.")


# Función para mostrar el menú principal
#@profile
async def trader_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user_chat_id = str(update.effective_chat.id)
    chat_ids = await cargar_chat_ids()

    if user_chat_id not in chat_ids:
        await update.message.reply_text("No estás registrado. Por favor, usa /start para registrarte.")
        return
    
    if await estado_suscripcion(chat_id=user_chat_id) != 'activa' and not es_administrador(user_chat_id):
        await update.message.reply_text("No tiene una suscripción activa o no cuenta con la cuota de transacciones requerida.\n" \
                                        "Por favor,  contacta con un administrador.")
        return
        
    await menu(update, context)


#@profile
async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE):

    global categorias

    user_id = update.effective_chat.id
    query = update.callback_query

    # Obtener los datos de activos con descripción desde Firestore
    doc_ref_activos = db.collection("config").document("activos_con_descripcion")
    doc_activos = doc_ref_activos.get()

    if doc_activos.exists:
        activos_con_descripcion = doc_activos.to_dict().get("data", {})
    else:
        activos_con_descripcion = {}

    # Obtener los datos de categorías desde Firestore
    doc_ref_categorias = db.collection("config").document("categorias")
    doc_categorias = doc_ref_categorias.get()

    if doc_categorias.exists:
        categorias = doc_categorias.to_dict().get("data", {})
    else:
        categorias = {}

    # Definir el teclado principal dinámicamente con las categorías obtenidas
    botones = [
        [InlineKeyboardButton(cat, callback_data=f"{user_id}_menu_{cat}")] for cat in categorias.keys()
    ]

    botones.append([InlineKeyboardButton("Cancelar", callback_data=f"{user_id}_menu_Cancelar")])
    teclado_principal = InlineKeyboardMarkup(botones)

    if not query:  # Si no hay un query (llamada desde /start u otra fuente)
        await update.message.reply_text("Menú principal - Selecciona una opción:", reply_markup=teclado_principal)
        return

    # Responde para evitar mensajes de error en Telegram
    await query.answer()

    # Procesar el menú según el callback_data
    categoria = query.data.split("_")[2]  # Extraer la categoría del callback_data

    if categoria == "volver":
        await query.edit_message_text(text="Selecciona una opción:", reply_markup=teclado_principal)
        return
    
    if categoria == "Cancelar":
        await query.edit_message_text(text="Operación cancelada. ¡Vuelve pronto!")
        return

    # Construir los botones para la categoría seleccionada
    if categoria in categorias:
        if categoria == "Todos":
            botones = [
                [InlineKeyboardButton(f"{categoria} - {activos_con_descripcion.get(par, {}).get('descripcion', par)}", 
                                    callback_data=f"{user_id}_par_{par}")] 
                                    for par in categorias[categoria]
            ]
        else:
            botones = [
                [InlineKeyboardButton(f"{par} - {activos_con_descripcion.get(par, {}).get('descripcion', par)}",
                                    callback_data=f"{user_id}_par_{par}")]
                                    for par in categorias[categoria]
            ]

        botones.append([InlineKeyboardButton("Volver", callback_data=f"{user_id}_menu_volver")])  # Botón para volver al menú principal

        reply_markup = InlineKeyboardMarkup(botones)
        await query.edit_message_text(text=f"Selecciona activo(s) de la categoría {categoria}:", reply_markup=reply_markup)
    else:
        await query.edit_message_text(text="Opción no válida. Por favor, selecciona nuevamente.")


#@profile
async def seleccionar_par(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_chat_id = str(update.callback_query.message.chat_id)

    global timezone_country
    timezone_country = pytz.timezone(await cargar_timezone_por_defecto(user_chat_id))

    chat_ids = await cargar_chat_ids()
    if user_chat_id not in chat_ids:
        await update.message.reply_text("No estás registrado. Por favor, usa /start para registrarte.")
        return

    if await estado_suscripcion(chat_id=user_chat_id) != 'activa' and not es_administrador(user_chat_id):
        await update.message.reply_text("No tiene una suscripción activa o no cuenta con la cuota de transacciones requerida.\n" \
                                        "Por favor,  contacta con un administrador.")
        return
    
    # Obtener las opciones del usuario
    opciones_usuario = await obtener_opciones_usuario(user_chat_id)
    logger.info(f"Estas son las opciones: {opciones_usuario}")
    if not es_administrador(user_chat_id) and (not opciones_usuario or not any(opcion in opciones_usuario for opcion in ["analisis basico", "analisis premium", "analisis avanzado"])):
        await context.bot.send_message(chat_id=user_chat_id, text="No tienes opciones habilitadas para esta operación. Por favor, adquiere una suscripción.")
        return

    # Obtener o inicializar el estado del usuario
    estado_usuario = obtener_estado_usuario(user_chat_id)

    if  return_state(chat_id=user_chat_id) == "en ejecución":
        await context.bot.send_message(
            chat_id=user_chat_id, 
            text="Ya tienes un análisis en ejecución. Por favor, espera a que termine."
        )
        return
    
    query = update.callback_query
    await query.answer()

    par = query.data.split("_")[2]  # Extraer el par del callback_data

    await query.edit_message_text(f"Has seleccionado el activo: {par}")

    # Ejecutar el análisis en una tarea asíncrona
    asyncio.create_task(ejecutar_recurrente(context, update, par, user_chat_id, opciones_usuario))


# Menú principal
#@profile
async def menu_usuario_administrador(context: ContextTypes.DEFAULT_TYPE, user_chat_id):
    """El menú del usuario registrado."""
    try:
        # Menú principal por defecto
        comandos_principales = [
            BotCommand("trader_menu", "Menú Operador"),
            BotCommand("ia_grafico", "Analisis IA por imagen"),
            BotCommand("analizar_simbolo", "Analizar Simbolo"),
            BotCommand("eventos_futuros", "Eventos Economicos"),
            BotCommand("noticias_user", "Noticias de un Simbolo"),
            BotCommand("noticias_admin", "Noticias de todos los Simbolos"),
            BotCommand("noticias_general", "Noticias Generales"),
            BotCommand("verificar_suscripcion", "Verificar Suscripción"),
            BotCommand("agregar_suscripcion", "Agregar Suscripción"),
            BotCommand("eliminar_suscripcion", "Eliminar Suscripción"),
            BotCommand("listar_suscripciones", "Listar Suscripciones"),
            BotCommand("set_timezone", "Menu Zonas Horarias"),
            BotCommand("enviar_mensaje", "Enviar mensaje masivo"),
            BotCommand("descargar_manual", "Descargar Manual"),
            BotCommand("stop", "Detener el bot"),
        ]

        # Configurar comandos para el usuario
        await context.bot.set_my_commands(comandos_principales, scope=BotCommandScopeChat(user_chat_id))
    except Exception as e:
        logger.info(f"Error al setear el menú para el usuario adminsitrador {user_chat_id}: {e}")



#@profile
async def menu_usuario_registrado(bot, user_chat_id: str):
    """El menú del usuario registrado según su estado de suscripción."""
    try:
        # ⬇️ usa keyword para que respete la firma de estado_suscripcion
        if await estado_suscripcion(chat_id=user_chat_id) == "activa":
            comandos_principales = [
                BotCommand("trader_menu", "Menú Operador"),
                BotCommand("ia_grafico", "Analisis IA por imagen"),
                BotCommand("analizar_simbolo", "Analizar Simbolo"),
                BotCommand("eventos_futuros", "Obtiene Eventos Economicos"),
                BotCommand("noticias_user", "Noticias de un Simbolo"),
                BotCommand("verificar_suscripcion", "Verificar Suscripción"),
                BotCommand("set_timezone", "Menu Zonas Horarias"),
                BotCommand("descargar_manual", "Descargar Manual"),
                BotCommand("stop", "Detener el bot"),
            ]
        else:
            comandos_principales = [
                BotCommand("menu_suscripciones", "Menu suscripción"),
                BotCommand("verificar_pago", "Verificar pago"),
                BotCommand("listar_pagos", "Listar pagos"),
                BotCommand("verificar_suscripcion", "Verificar suscripción"),
                BotCommand("stop", "Detener el bot"),
            ]

        await bot.set_my_commands(comandos_principales, scope=BotCommandScopeChat(user_chat_id))
    except Exception as e:
        logger.info(f"Error al resetear el menú para el usuario {user_chat_id}: {e}")



#@profile
async def resetear_menu_usuario(context: ContextTypes.DEFAULT_TYPE, user_chat_id: int):
    """Resetea el menú del usuario a la configuración principal."""
    global timezone_country
    try:
        # Menú principal por defecto
        comandos_principales = [
            BotCommand("start", "Inicia el bot")
        ]

        # Configurar comandos para el usuario
        await context.bot.set_my_commands(comandos_principales, scope=BotCommandScopeChat(user_chat_id))
    except Exception as e:
        logger.info(f"Error al resetear el menú para el usuario {user_chat_id}: {e}")

#@profile
async def comando_reset_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando para que un usuario pueda resetear su menú."""
    global timezone_country
    user_chat_id = update.effective_chat.id
    await resetear_menu_usuario(context, user_chat_id)

#@profile
async def cargar_datos_subscription_user():
    try:
        docs = db.collection("suscripciones_user").stream()
        out = {}
        for doc in docs:
            if not doc.exists:
                continue
            d = doc.to_dict() or {}
            # Alias = tiene doc_alias_of, o el id es numérico y no coincide con user_id
            is_alias = bool(d.get("doc_alias_of")) or (str(doc.id).isdigit() and str(d.get("user_id") or "") != str(doc.id))
            if is_alias:
                continue
            out[doc.id] = d
        return out
    except Exception as e:
        print("Error al cargar suscripciones:", e)
        return {}


#@profile
async def cargar_datos_subscription_type():
    """Carga los datos de tipos de suscripción desde Firestore y los ordena según el tipo (solo para bot o ambos)."""
    try:
        # Referencia a la colección "suscripciones_tipo"
        collection_ref = db.collection("suscripciones_tipo")
        
        # Consulta todos los documentos
        docs = collection_ref.stream()

        # Construir un diccionario solo con los que tienen show = 'a' o 'b'
        datos_tipos_suscripciones = {
            doc.id: doc.to_dict()
            for doc in docs
            if doc.exists and doc.to_dict().get("show") in ["a", "b"]
        }

        # Ordenar por prioridad: básicas, premium, avanzadas
        orden_prioridad = ["basica", "premium", "avanzada"]
        datos_ordenados = {
            key: datos_tipos_suscripciones[key]
            for key in sorted(
                datos_tipos_suscripciones.keys(),
                key=lambda x: orden_prioridad.index(x.split("-")[0]) if x.split("-")[0] in orden_prioridad else len(orden_prioridad)
            )
        }

        return datos_ordenados
    except Exception as e:
        print(f"Error al cargar tipos de suscripción desde Firestore: {e}")
        return {}


#@profile
async def guardar_datos(data: dict):
    """data = {user_id: {...campos...}}"""
    try:
        batch = db.batch()
        for user_id, detalles in data.items():
            # 2.1 upsert suscripción
            doc_ref = _subscription_doc(user_id)
            batch.set(doc_ref, detalles, merge=True)

            # 2.2 mantener índice chat_ids si viene telegram_id
            tg = (detalles or {}).get("telegram_id")
            if tg:
                chat_ref = db.collection("chat_ids").document(str(tg))
                batch.set(chat_ref, {"user_id": str(user_id)}, merge=True)

        batch.commit()

        # refrescar cache en memoria
        global subscriptions
        subscriptions = await cargar_datos_subscription_user()
        print("Suscripciones actualizadas.")
    except Exception as e:
        print("Error al guardar suscripciones:", e)


# Función para verificar si un usuario es administrador
#@profile
def es_administrador(user_id):
    return user_id in admin_ids


def parse_iso_aware(s: str):
    # Acepta ISO con o sin tz. Devuelve AWARE (UTC) o None.
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            return dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)
    except Exception:
        return None

# Devuelve: 'activa' | 'expirada' | 'inactiva' | 'transacciones_insuficientes' | 'sin suscripción'
# user_key puede ser user_id (app) o chat_id (bot).
#@profile
async def estado_suscripcion(
    *, 
    user_id: str | None = None, 
    chat_id: str | None = None,
    numero_transacciones: int | None = None,
    **_kwargs
) -> str:
    try:
        key = (user_id or chat_id or "").strip()
        if not key:
            return "inactiva"

        canon_ref, alias_ref, data_canon = _resolve_refs_from_key(key)
        if not canon_ref:
            return "inactiva"

        # lee canónico
        doc = data_canon
        if not doc:
            snap = canon_ref.get()
            if not snap.exists:
                return "inactiva"
            doc = snap.to_dict() or {}

        fin  = parse_iso_aware(doc.get("fin") or "")
        rest = int(doc.get("transacciones_restantes", 0))
        ahora = _now_utc()

        base_estado = "activa" if (fin and fin >= ahora and rest > 0) else "inactiva"

        # Si cambió, normaliza en canónico (+ alias si existe)
        try:
            batch = db.batch()
            batch.set(canon_ref, {"estado": base_estado, "updated_at": firestore.SERVER_TIMESTAMP}, merge=True)
            if alias_ref is not None:
                batch.set(alias_ref, {"estado": base_estado, "updated_at": firestore.SERVER_TIMESTAMP}, merge=True)
            batch.commit()
        except Exception:
            pass

        if base_estado != "activa":
            return "inactiva"

        if isinstance(numero_transacciones, int) and numero_transacciones > 0 and rest < numero_transacciones:
            return "transacciones_insuficientes"

        return "activa"

    except Exception as e:
        logger.info(f"estado_suscripcion error: {e}")
        return "inactiva"


# Función para cargar pagos pendientes
#@profile
async def cargar_pagos_pendientes():
    """Carga los pagos pendientes desde Firestore o devuelve una estructura vacía si no hay datos."""
    try:
        # Referencia a la colección "pagos_pendientes"
        collection_ref = db.collection("pagos_pendientes")

        # Consulta todos los documentos de la colección
        docs = collection_ref.stream()

        # Construir la estructura esperada
        pendientes = [
            doc.to_dict()
            for doc in docs if doc.exists
        ]

        return {"pendientes": pendientes}
    except Exception as e:
        print(f"Error al cargar pagos pendientes desde Firestore: {e}")
        return {"pendientes": []}  # Devuelve la estructura vacía en caso de error


# Función para guardar pagos pendies
#@profile
def guardar_pagos_pendientes(data):
    """Guarda o actualiza un pago pendiente en Firestore."""
    try:
        for pago in data["pendientes"]:
            # Usa el campo `id_pago` como ID del documento en Firestore
            doc_ref = db.collection("pagos_pendientes").document(pago["id_pago"])

            # Guardar o actualizar los datos del pago pendiente
            doc_ref.set(pago, merge=True)

        print("Pagos pendientes guardados/actualizados con éxito.")
    except Exception as e:
        print(f"Error al guardar pagos pendientes en Firestore: {e}")


# Nota: asumo que ya existen:
#   - subscriptions: dict en memoria
#   - guardar_datos(subscriptions): persiste el dict
#   - _user_id_from_chat(chat_id): devuelve el user_id (si usas Telegram)
# Si no usas Telegram en ese entorno, simplemente no pases origen="telegram".

#@profile
async def obtener_opciones_usuario(user_or_chat_id: str, *, origen: str = "telegram") -> list[str]:
    try:
        key = (user_or_chat_id or "").strip()
        if not key:
            return []

        canon_ref, alias_ref, data_canon = _resolve_refs_from_key(key)
        if not canon_ref:
            return []

        doc = data_canon
        if not doc:
            snap = canon_ref.get()
            if not snap.exists:
                return []
            doc = snap.to_dict() or {}

        fin  = parse_iso_aware(doc.get("fin") or "")
        rest = int(doc.get("transacciones_restantes", 0))
        ahora = _now_utc()
        estado_nuevo = "activa" if (fin and fin >= ahora and rest > 0) else "inactiva"

        # Normalizar estado en ambos
        try:
            batch = db.batch()
            batch.set(canon_ref, {"estado": estado_nuevo, "updated_at": firestore.SERVER_TIMESTAMP}, merge=True)
            if alias_ref is not None:
                batch.set(alias_ref, {"estado": estado_nuevo, "updated_at": firestore.SERVER_TIMESTAMP}, merge=True)
            batch.commit()
        except Exception:
            pass

        return doc.get("opciones", []) if estado_nuevo == "activa" else []

    except Exception as e:
        logger.info(f"obtener_opciones_usuario error: {e}")
        return []


#@profile
async def mostrar_menu_suscripciones(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Despliega el menú de suscripciones para que el usuario seleccione."""
    user_id = update.effective_chat.id
    opciones = []

    try:
        for clave, detalles in subscriptions_type.items():
            texto_opcion = f"{clave.replace('-', ' ').title()} - ${detalles['precio']} ({detalles['duracion']})"
            opciones.append([InlineKeyboardButton(texto_opcion, callback_data=f"{user_id}_suscripcion_{clave}")])

        opciones.append([InlineKeyboardButton("Cancelar", callback_data=f"{user_id}_suscripciones_cancelar")])
        menu = InlineKeyboardMarkup(opciones)
        await update.message.reply_text("Selecciona una suscripción:", reply_markup=menu)

    except Exception as e:
        await update.message.reply_text(f"Ocurrió un error al mostrar el menú de suscripciones: {str(e)}")



#@profile
async def cancelar_suscripcion(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Maneja la cancelación de la selección de suscripción."""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("Has cancelado la selección de la suscripción... ")

#@profile
async def cancelar_zonas_horarias(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Maneja la cancelación de la selección del timezone."""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("Has cancelado la selección del timezone... ")


#@profile
async def volver_al_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Volver al menú principal"""
    await mostrar_menu_suscripciones(update, context)

#@profile
def _suscripciones_col():
    return db.collection("suscripciones_user")

#@profile
def _user_ids_col():
    return db.collection("user_ids")  # mapea user_id <-> telegram_id

#@profile
def _resolve_user_uuid(user_id: Optional[str], chat_id: Optional[str]) -> Optional[str]:
    """
    - Si viene user_id (APP), úsalo.
    - Si no hay user_id pero hay chat_id, intenta mapear; si no, usa chat_id tal cual como uid.
    """
    if user_id:
        return str(user_id)
    if chat_id:
        try:
            mapped = _user_id_from_chat(str(chat_id))
            if mapped:
                return str(mapped)
        except Exception:
            pass
        return str(chat_id)
    return None

#@profile
def _now_utc():
    return datetime.now(dt_timezone.utc)

#@profile
def _parse_dt_mixed(v) -> Optional[datetime]:
    """
    Acepta Timestamp de Firestore, ISO string o None.
    Devuelve datetime UTC aware.
    """
    if v is None:
        return None
    try:
        # Firestore Timestamp
        if hasattr(v, "timestamp"):
            return datetime.fromtimestamp(v.timestamp(), tz=dt_timezone.utc)
        # pandas Timestamp
        if hasattr(v, "to_pydatetime"):
            d = v.to_pydatetime()
            return d if d.tzinfo else d.replace(tzinfo=dt_timezone.utc)
        # ISO string
        d = pd.to_datetime(v, utc=True, errors="coerce")
        if pd.isna(d):
            return None
        return d.to_pydatetime()
    except Exception:
        return None

#@profile
def _build_sub_doc_id(user_uuid: str, origen: str) -> str:
    """
    Creamos un doc-id estable por origen, pero SIEMPRE consultamos por campo user_id en queries.
    """
    origen_norm = (origen or "telegram").lower()
    return f"{user_uuid}__{origen_norm}"

#@profile
def _options_from_catalog(tipo_suscripcion: str) -> Tuple[List[str], int, str]:
    """
    Lee desde tu diccionario subscriptions_type.
    Retorna (opciones, transacciones_maximas, duracion_texto)
    """
    st = subscriptions_type.get(tipo_suscripcion, {})
    opts = list(st.get("opciones", []))
    txs = int(st.get("transacciones_maximas", 0))
    dur = str(st.get("duracion", "1 mes"))
    return (opts, txs, dur)

#@profile
def _calc_fin_from_text(duracion_texto: str, inicio: datetime) -> datetime:
    dur = (duracion_texto or "").lower()
    if "año" in dur or "ano" in dur:
        n = int(pd.to_numeric(dur.split()[0], errors="coerce")) if dur.split() else 1
        return inicio + timedelta(days=365*n)
    if "mes" in dur:
        n = int(pd.to_numeric(dur.split()[0], errors="coerce")) if dur.split() else 1
        return inicio + timedelta(days=30*n)
    if "semana" in dur:
        n = int(pd.to_numeric(dur.split()[0], errors="coerce")) if dur.split() else 1
        return inicio + timedelta(days=7*n)
    return inicio + timedelta(days=30)

#@profile
def _get_all_subs_for_user(user_uuid: str) -> List[Any]:
    try:
        qs = _suscripciones_col().where("user_id", "==", user_uuid).stream()
        return list(qs)
    except Exception as e:
        logging.warning(f"[subs] No se pudieron obtener suscripciones para {user_uuid}: {e}")
        return []

#@profile
def _pick_active_or_latest(sub_snaps: List[Any]) -> Tuple[Optional[Any], Optional[Dict[str, Any]]]:
    """
    De una lista de docs de suscripciones, elige la activa (fin>=now y estado!='inactiva' y tx>0).
    Si no hay activa, elige la más reciente por 'fin'.
    """
    now = _now_utc()
    best = None
    best_data = None
    latest = None
    latest_data = None

    for s in sub_snaps:
        data = s.to_dict() or {}
        fin = _parse_dt_mixed(data.get("fin"))
        tx = int(data.get("transacciones_restantes", 0))
        estado = str(data.get("estado") or "").lower()
        if fin and fin >= now and estado != "inactiva" and tx > 0:
            # candidata activa
            if (best is None) or (_parse_dt_mixed(best_data.get("fin")) or now) < fin:
                best = s
                best_data = data
        # track más reciente
        if fin and ((latest is None) or (_parse_dt_mixed(latest_data.get("fin")) or now) < fin):
            latest = s
            latest_data = data

    return (best or latest, best_data or latest_data)

#@profile
def get_active_subscription(*, user_id: Optional[str] = None, chat_id: Optional[str] = None) -> Tuple[Optional[Any], Optional[Dict[str, Any]]]:
    """
    Devuelve (doc_ref | None, data | None) de la suscripción activa (o la más reciente si no hay activa).
    Busca por user_id (resuelto desde chat_id si hace falta).
    """
    user_uuid = _resolve_user_uuid(user_id, chat_id)
    if not user_uuid:
        return (None, None)
    snaps = _get_all_subs_for_user(user_uuid)
    return _pick_active_or_latest(snaps)

#@profile
def upsert_subscription_firestore(
    *,
    user_id: str,
    nombre_usuario: str,
    tipo_suscripcion: str,
    origen: str,
    id_pago: Optional[str] = None,
    hash_transaccion: Optional[str] = None,
    telegram_id: Optional[str] = None,
    inicio: Optional[datetime] = None,
    fin: Optional[datetime] = None
) -> Tuple[Any, Dict[str, Any]]:
    """
    Crea/actualiza la suscripción del usuario en el doc canónico:
      /suscripciones_user/{user_id}
    y refleja (espejo mínimo) en el alias:
      /suscripciones_user/{telegram_id}  (si existe)
    
    Reglas:
    - SSOT = canónico por user_id.
    - Alias es solo espejo (campos mínimos que el bot necesita).
    - Si no existía saldo previo, arranca en tx_max.
    - Si cambió el plan (tipo/limite), se capa el saldo al nuevo límite.
    - Timestamps con SERVER_TIMESTAMP.
    """

    user_uuid = str(user_id).strip()
    if not user_uuid:
        raise ValueError("user_id vacío")

    origen_norm = (origen or "telegram").lower()

    # 1) Obtener parámetros desde catálogo
    # Debe devolver: opciones (lista), limite transacciones (int), duración en texto (p.ej. "1 mes")
    opts, tx_max, dur = _options_from_catalog(tipo_suscripcion)

    # 2) Calcular inicio/fin
    start = inicio or _now_utc()
    end = fin or _calc_fin_from_text(dur, start)

    # 3) Referencias canónico + alias
    col = db.collection("suscripciones_user")
    canon_ref = col.document(user_uuid)
    alias_ref = col.document(str(telegram_id)) if telegram_id else None

    # 4) Leer estado previo del canónico (si existe)
    prev = canon_ref.get()
    prev_data = prev.to_dict() if prev.exists else {}
    saldo_actual = int(prev_data.get("transacciones_restantes") or 0)

    # 5) Determinar saldo
    #    - Si no existía, arranca en tx_max
    #    - Si existía y cambió límite o tipo, cap al nuevo tx_max (no sube automáticamente)
    prev_rest = prev.get("transacciones_restantes")
    if prev_rest is None:
        tx_rest = int(tx_max)
    else:
        try:
            tx_rest = int(prev_rest)
        except Exception:
            tx_rest = int(tx_max)
        # Cap si bajó el tope o cambiaste de plan
        try:
            prev_lim = int(prev.get("limite_transacciones", tx_max))
        except Exception:
            prev_lim = int(tx_max)
        prev_tipo = str(prev.get("tipo") or "")
        if prev_tipo != tipo_suscripcion or prev_lim != tx_max:
            tx_rest = min(tx_rest, int(tx_max))

    # 6) Payload canónico (SSOT)
    payload = {
        "user_id": user_uuid,
        "telegram_id": str(telegram_id) if telegram_id else (prev.get("telegram_id") or None),
        "nombre_usuario": nombre_usuario,
        "tipo": tipo_suscripcion,
        "origen": origen_norm,  # info, no forma parte del doc id
        "id_pago": id_pago or prev.get("id_pago"),
        "hash_transaccion": hash_transaccion or prev.get("hash_transaccion"),
        "inicio": start.isoformat(),
        "fin": end.isoformat(),
        "estado": "activa",
        "opciones": opts,
        "limite_transacciones": int(tx_max),   # solo visual
        "transacciones_restantes": saldo_actual,  # ❗️preserva
        "updated_at": firestore.SERVER_TIMESTAMP,
    }

    # 7) Alias mínimo para el bot (solo si hay telegram_id)
    alias_payload = None
    if alias_ref is not None:
        alias_payload = {
            "user_id": user_uuid,
            "doc_alias_of": user_uuid,
            "telegram_id": str(telegram_id),
            "estado": payload["estado"],
            "fin": payload["fin"],
            "transacciones_restantes": payload["transacciones_restantes"],
            "updated_at": firestore.SERVER_TIMESTAMP,
            # Puedes añadir más campos si tu bot los usa,
            # pero evita arrays como "tokens" aquí para no duplicar.
        }

    # 8) Commit atómico en ambos (batch)
    batch = db.batch()
    batch.set(canon_ref, payload, merge=True)
    if alias_payload is not None:
        batch.set(alias_ref, alias_payload, merge=True)
    batch.commit()

    return (canon_ref, payload)


#@profile
def _resolve_refs_from_key(user_key: str):
    """
    Resuelve referencias para el modelo:
      - Canónico:  /suscripciones_user/{user_id}
      - Alias TG:  /suscripciones_user/{telegram_id}  (espejo mínimo)

    Devuelve: (canon_ref, alias_ref, data_canon)
      - canon_ref: DocumentReference del doc canónico (SSOT)
      - alias_ref: DocumentReference del alias (o None si no aplica)
      - data_canon: dict del canónico si ya lo leímos en este flujo, si no -> None

    Estrategia de resolución (en orden):
      1) Si existe /suscripciones_user/{user_key}:
         - Si parece alias (tiene doc_alias_of o es id numérico y user_id != id) => usa ese como alias y resuelve canónico por doc_alias_of/user_id.
         - Si no, trátalo como canónico; alias_ref a partir de su telegram_id (si existe).
      2) Buscar canónico por where("telegram_id" == user_key) => alias_ref es el doc {user_key}.
      3) Buscar canónico por where("user_id" == user_key).
      4) Mapping auxiliar chat_ids/{chat_id} => { user_id }, y de ahí el canónico.

    Notas:
      - No escribe nada; solo resuelve referencias.
      - Considera id numérico como indicio fuerte de alias (chat_id), pero confirma con campos.
    """
    key = (user_key or "").strip()
    if not key:
        return None, None, None

    col = db.collection("suscripciones_user")

    # ---------- 1) Intento directo: existe un doc con id == key ----------
    try:
        doc_ref = col.document(key)
        snap = doc_ref.get()
    except Exception:
        snap = None

    if snap and snap.exists:
        d = snap.to_dict() or {}
        is_numeric_id = key.isdigit()

        # Candidatos provistos por el documento leído
        cand_user_id = d.get("doc_alias_of") or d.get("user_id")
        cand_telegram_id = d.get("telegram_id")

        # Heurística de "parece alias":
        #   - Si trae doc_alias_of => alias.
        #   - O si el id es numérico y trae un user_id distinto a ese id => alias.
        looks_alias = bool(d.get("doc_alias_of")) or (is_numeric_id and cand_user_id and str(cand_user_id) != key)

        if looks_alias:
            # Este doc es el alias; resolver canónico por doc_alias_of/user_id
            user_id = str(cand_user_id) if cand_user_id else None
            if user_id:
                canon_ref = col.document(user_id)
                alias_ref = col.document(key)  # el propio doc leído
                # No tenemos garantía de haber leído el canónico aún → data_canon = None
                return canon_ref, alias_ref, None
            # Si no pudimos sacar user_id, seguimos a los demás pasos
        else:
            # Tratar el doc directo como canónico
            user_id = str(d.get("user_id") or key)
            canon_ref = col.document(user_id)
            alias_ref = col.document(str(cand_telegram_id)) if cand_telegram_id else None
            # Si el id del doc coincide con el canónico, devolvemos data_canon para evitar otra lectura
            data_canon = d if snap.id == user_id else None
            return canon_ref, alias_ref, data_canon

    # ---------- 2) Buscar canónico por telegram_id == key ----------
    try:
        qs = col.where("telegram_id", "==", key).limit(1).get()
        if qs:
            canon_snap = qs[0]
            d = canon_snap.to_dict() or {}
            user_id = str(canon_snap.id)  # en tu diseño, el id del canónico es el user_id
            canon_ref = col.document(user_id)
            alias_ref = col.document(key)  # el alias debería ser {telegram_id}
            return canon_ref, alias_ref, d
    except Exception:
        pass

    # ---------- 3) Buscar canónico por user_id == key ----------
    try:
        qs = col.where("user_id", "==", key).limit(1).get()
        if qs:
            canon_snap = qs[0]
            d = canon_snap.to_dict() or {}
            user_id = str(d.get("user_id") or canon_snap.id)
            canon_ref = col.document(user_id)
            tg = d.get("telegram_id")
            alias_ref = col.document(str(tg)) if tg else None
            return canon_ref, alias_ref, d
    except Exception:
        pass

    # ---------- 4) Mapping auxiliar: chat_ids/{chat_id} => { user_id } ----------
    # Útil cuando existe un mapping separado y no hay telegram_id en canónico.
    try:
        map_snap = db.collection("chat_ids").document(key).get()
        if map_snap.exists:
            uid = (map_snap.to_dict() or {}).get("user_id")
            if uid:
                canon_ref = col.document(str(uid))
                # Intentar armar alias_ref con el propio chat_id (key) o con el telegram_id del canónico si existe
                try:
                    canon_doc = canon_ref.get()
                    if canon_doc.exists:
                        d = canon_doc.to_dict() or {}
                        tg = d.get("telegram_id") or key
                        alias_ref = col.document(str(tg)) if tg else None
                        return canon_ref, alias_ref, d
                    else:
                        return canon_ref, None, None
                except Exception:
                    return canon_ref, None, None
    except Exception:
        pass

    # ---------- No se pudo resolver ----------
    return None, None, None


async def descontar_transaccion(user_key: str, numero_transacciones_in=1, origen="telegram") -> Tuple[bool, str]:
    """
    Descuenta 'numero_transacciones_in' de transacciones_restantes:
      - Lee SIEMPRE del doc canónico (/suscripciones_user/{user_id})
      - Escribe en canónico y refleja en alias (si existe) ATÓMICAMENTE (transacción)
      - Actualiza 'estado' => 'inactiva' si llega a 0
    """
    try:
        dec = int(numero_transacciones_in) if int(numero_transacciones_in) > 0 else 1
    except Exception:
        dec = 1

    canon_ref, alias_ref, _ = _resolve_refs_from_key(user_key)
    if not canon_ref:
        return False, f"❌ No se encontró suscripción para {user_key}."

    @firestore.transactional
    def _tx(transaction: firestore.Transaction):
        canon_snap = canon_ref.get(transaction=transaction)
        if not canon_snap.exists:
            raise ValueError("No existe el documento canónico.")

        sus = canon_snap.to_dict() or {}
        curr = int(sus.get("transacciones_restantes") or 0)
        nuevo = max(curr - dec, 0)
        estado = "activa" if nuevo > 0 else "inactiva"

        # Canónico (SSOT)
        transaction.set(
            canon_ref,
            {
                "transacciones_restantes": nuevo,
                "estado": estado,
                "updated_at": firestore.SERVER_TIMESTAMP,
            },
            merge=True,
        )

        # Alias (si existe): espejo mínimo
        if alias_ref is not None:
            transaction.set(
                alias_ref,
                {
                    "user_id": sus.get("user_id") or canon_ref.id,
                    "doc_alias_of": sus.get("user_id") or canon_ref.id,
                    "transacciones_restantes": nuevo,
                    "estado": estado,
                    "updated_at": firestore.SERVER_TIMESTAMP,
                },
                merge=True,
            )

        return nuevo

    try:
        tx = db.transaction()
        nuevo = _tx(tx)
        if nuevo <= 0:
            return True, "✅ Transacción aplicada. Te quedan 0; tu suscripción quedó inactiva."
        return True, f"✅ Transacción aplicada. Te quedan {nuevo}."
    except Exception as e:
        return False, f"❌ Error inesperado al descontar transacción: {e}"


# =========================
# ✉️ Noticias generales (Firestore + descuento)
# =========================
#@profile
async def obtener_noticias_generales(update, context):
    limite = 20
    max_reintentos = 3
    tiempo_espera_inicial = 5
    user_chat_id = str(update.effective_chat.id)

    # Estado UI
    try:
        mark_user_state(chat_id=user_chat_id, estado="en ejecución")
    except Exception:
        pass

    try:
        # Validar suscripción activa en Firestore (o admin)
        ref, sub = get_active_subscription(chat_id=user_chat_id)
        if not sub and not es_administrador(user_chat_id):
            await update.message.reply_text(
                "No tienes una suscripción activa o sin transacciones. Contacta con un administrador."
            )
            return

        endpoint = "https://financialmodelingprep.com/api/v4/general_news"
        url = f"{endpoint}?limit={limite}&apikey={API_KEY}"

        reintento = 0
        tiempo_espera = tiempo_espera_inicial
        noticias = []

        while reintento < max_reintentos:
            try:
                response = requests.get(url, timeout=10)
                if response.status_code == 200:
                    if not response.text.strip():
                        logging.info("La respuesta de la API está vacía.")
                        break
                    noticias = response.json()
                    break
                else:
                    logging.info(f"Error en la API: {response.status_code}")
            except requests.exceptions.RequestException as e:
                logging.info(f"Error al obtener noticias generales: {e}")
                reintento += 1
                if reintento < max_reintentos:
                    logging.info(f"Reintentando en {tiempo_espera} segundos...")
                    time.sleep(tiempo_espera)
                    tiempo_espera *= 2

        # Procesar
        if isinstance(noticias, list) and len(noticias) > 0:
            df = pd.DataFrame(noticias)

            if "publishedDate" in df.columns:
                # Parse seguro → UTC aware
                df["publishedDate"] = pd.to_datetime(df["publishedDate"], utc=True, errors="coerce")
                df = df.dropna(subset=["publishedDate"])

                for _, n in df.iterrows():
                    title = n.get("title", "")
                    sitio = n.get("site", "Desconocido")
                    text = n.get("text", "Sin Descripción")
                    symbol = n.get("symbol", "No Aplica")
                    fecha = n["publishedDate"].strftime("%Y-%m-%d %H:%M:%S")
                    importancia = analizar_importancia(f"{title} {text}")
                    url = n.get("url", "")
                    link_traductor = f"https://translate.google.com/translate?sl=auto&tl=es&u={url}"

                    mensaje = (
                        f"Titulo: {title}\n"
                        f"Descripción: {text}\n"
                        f"Activo: {symbol}\n"
                        f"Fecha: {fecha}\n"
                        f"Sitio: {sitio}\n"
                        f"Importancia: {importancia}\n"
                        f"Link: {url}\n"
                        f"Link Traducido: {link_traductor}\n"
                    )
                    await enviar_mensaje_noticias(context, user_chat_id, mensaje)

                # Descontar 1 transacción si no es admin
                if not es_administrador(user_chat_id):
                    success, mensaje_tx = await descontar_transaccion(user_chat_id, 1, origen="telegram")
                    if not success:
                        await update.message.reply_text(mensaje_tx)
        else:
            logging.info("No se encontraron noticias válidas en la respuesta.")

    except Exception as e:
        logging.info(f"Error en obtener_noticias_generales: {e}")
    finally:
        # ⚠️ corregido: era user_id=user_chat_id
        try:
            mark_user_state(chat_id=user_chat_id, estado="disponible")
        except Exception:
            pass


# =========================
# 🖼️ IA gráfico (sin cambios de subs)
# =========================
#@profile
async def manejar_ia_grafico(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_chat_id = str(update.effective_chat.id)
    try:
        mark_user_state(chat_id=user_chat_id, estado="esperando_grafico_ia")
        await update.message.reply_text("📸 Sube una imagen de un gráfico de velas para analizarla con IA.")
    except Exception as e:
        await update.message.reply_text(f"Ocurrió un error al preparar el análisis: {e}")


# =========================
# 🛠️ Admin: agregar / eliminar / listar / verificar
# =========================
#@profile
async def agregar_suscripcion(update: Update, context: ContextTypes.DEFAULT_TYPE):
    admin_chat = str(update.effective_user.id)
    if not es_administrador(admin_chat):
        await update.message.reply_text("No tienes permisos para usar este comando.")
        return

    args = context.args
    if len(args) < 3 or len(args) > 4:
        await update.message.reply_text("Uso: /agregar_suscripcion <user_id|telegram_id> <tipo_suscripcion> <nombre_usuario> [hash_transaccion]")
        return

    raw_user_id, tipo_suscripcion, nombre_usuario = args[:3]
    hash_transaccion = args[3] if len(args) == 4 else None

    # Resolver user_id real (acepta chat_id)
    user_uuid = _resolve_user_uuid(user_id=raw_user_id if raw_user_id.isdigit() else raw_user_id, chat_id=raw_user_id)
    if not user_uuid:
        await update.message.reply_text("No se pudo resolver el usuario.")
        return

    if tipo_suscripcion not in subscriptions_type:
        await update.message.reply_text("Tipo de suscripción no válido.")
        return

    # Intenta leer telegram_id mapeado (si existe)
    telegram_id = None
    try:
        # Si raw es chat_id y existe mapping inverso, úsalo
        if raw_user_id and raw_user_id.isdigit():
            telegram_id = raw_user_id
        else:
            # Busca en user_ids si tiene telegram_id
            q = _user_ids_col().document(user_uuid).get()
            if q.exists:
                telegram_id = str((q.to_dict() or {}).get("telegram_id") or "") or None
    except Exception:
        pass

    try:
        ref, payload = upsert_subscription_firestore(
            user_id=user_uuid,
            nombre_usuario=nombre_usuario,
            tipo_suscripcion=tipo_suscripcion,
            origen="telegram",
            id_pago=f"admin_{_now_utc().timestamp():.0f}",
            hash_transaccion=hash_transaccion,
            telegram_id=telegram_id,
        )
        await update.message.reply_text(
            f"✅ Suscripción {tipo_suscripcion} activada para {nombre_usuario} (uid: {user_uuid})."
        )
    except Exception as e:
        await update.message.reply_text(f"⚠️ Error al registrar suscripción: {e}")


#@profile
async def eliminar_suscripcion(update: Update, context: ContextTypes.DEFAULT_TYPE):
    admin_chat = str(update.effective_user.id)
    if not es_administrador(admin_chat):
        await update.message.reply_text("No tienes permisos para usar este comando.")
        return

    if len(context.args) != 1:
        await update.message.reply_text("Uso: /eliminar_suscripcion <user_id|telegram_id>")
        return

    arg = context.args[0]
    user_uuid = _resolve_user_uuid(user_id=arg if arg.isdigit() else arg, chat_id=arg)
    if not user_uuid:
        await update.message.reply_text("No se pudo resolver el usuario.")
        return

    snaps = _get_all_subs_for_user(user_uuid)
    if not snaps:
        await update.message.reply_text(f"No se encontraron suscripciones para {arg}.")
        return

    try:
        for s in snaps:
            s.reference.delete()
        await update.message.reply_text(f"✅ Eliminadas {len(snaps)} suscripciones para {user_uuid}.")
    except Exception as e:
        await update.message.reply_text(f"⚠️ Error al eliminar: {e}")


#Filtros opcionales:
#/listar_suscripciones → todas
#/listar_suscripciones activas|inactivas|expiradas → filtra por estado calculado
#/listar_suscripciones detalle o /listar_suscripciones activas detalle → salida expandida
#@profile
async def listar_suscripciones(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Lista suscripciones de la colección 'suscripciones_user'.
    Uso:
      /listar_suscripciones
      /listar_suscripciones activas|inactivas|expiradas
      /listar_suscripciones detalle
      /listar_suscripciones activas detalle
    """

    # ---- helpers locales para evitar conflictos de nombres ----
    #@profile
    def _parse_iso_z_local(s: str | None):
        if not s:
            return None
        try:
            ss = str(s)
            if ss.endswith("Z"):
                ss = ss.replace("Z", "+00:00")
            dt = datetime.fromisoformat(ss)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            return None

    #@profile
    def _fmt_dt_local_local(dt: datetime, tz_str: str | None) -> str:
        try:
            tz = pytz.timezone(tz_str) if tz_str else pytz.utc
        except Exception:
            tz = pytz.utc
        return dt.astimezone(tz).strftime("%Y-%m-%d %H:%M:%S %Z")

    #@profile
    def _estado_calc_local(data: dict, now_utc: datetime):
        fin = _parse_iso_z_local(data.get("fin"))
        rest = int((data.get("transacciones_restantes") or 0) or 0)
        if fin and fin < now_utc:
            return "expirada", rest, fin
        if rest <= 0:
            return "inactiva", rest, fin
        return "activa", rest, fin

    admin_id = str(update.effective_user.id)
    if not es_administrador(admin_id):
        await update.message.reply_text("🚫 No tienes permisos para usar este comando.")
        return

    # filtros
    args = [a.lower() for a in (context.args or [])]
    valid_filters = {"activas", "inactivas", "expiradas", "todas"}
    filtro = next((a for a in args if a in valid_filters), "todas")
    modo_detalle = ("detalle" in args) or ("det" in args)

    try:
        tz_admin = await cargar_timezone_por_defecto(admin_id)
    except Exception:
        tz_admin = "UTC"

    # ---- LECTURA DE FIRESTORE ----
    try:
        # 'db' debe ser tu cliente Firestore ya inicializado
        snaps = list(db.collection("suscripciones_user").stream())
    except Exception as e:
        await update.message.reply_text(f"⚠️ Error leyendo suscripciones: {e}")
        return

    if not snaps:
        await update.message.reply_text("No hay suscripciones registradas.")
        return

    now = datetime.now(timezone.utc)

    counters = {"activa": 0, "inactiva": 0, "expirada": 0}
    filas = []  # [(estado, fin_dt, linea_formateada)]

    for s in snaps:
        d = s.to_dict() or {}
        uid = d.get("user_id") or s.id
        tg  = d.get("telegram_id")
        base_plan = d.get("basePlanId") or d.get("base_plan_id") or "-"
        product   = d.get("productId") or "-"
        inicio_dt = _parse_iso_z_local(d.get("inicio"))
        estado, rest, fin_dt = _estado_calc_local(d, now)
        limit = int((d.get("limite_transacciones") or 0) or 0)
        opciones = d.get("opciones") or []
        trans_id = d.get("transactionId") or d.get("transaction_id")
        tokens   = d.get("tokens") or []

        updated = d.get("updated_at")
        if hasattr(updated, "to_datetime"):
            updated = updated.to_datetime().astimezone(timezone.utc)
        elif isinstance(updated, str):
            updated = _parse_iso_z_local(updated)
        else:
            updated = None

        # filtro por estado
        if filtro != "todas" and filtro.rstrip("s") != estado:
            # permite "activas" o "activa", etc.
            continue

        dias = (fin_dt - now).days if fin_dt else None
        badge = "✅" if estado == "activa" else ("⚠️" if estado == "inactiva" else "⛔")

        if modo_detalle:
            lineas = [
                f"{badge} *{estado.capitalize()}*",
                f"• Usuario: `{uid}`" + (f"  · TG: `{tg}`" if tg else ""),
                f"• Plan/basePlanId: `{base_plan}`  · Producto: `{product}`",
                f"• Transacciones: {rest}" + (f" / {limit}" if limit else ""),
            ]
            if inicio_dt:
                lineas.append(f"• Inicio: {_fmt_dt_local_local(inicio_dt, tz_admin)}")
            if fin_dt:
                extra = f"  · Quedan {dias} días" if dias is not None and dias >= 0 else ""
                lineas.append(f"• Fin: {_fmt_dt_local_local(fin_dt, tz_admin)}{extra}")
            if opciones:
                lineas.append("• Opciones: " + ", ".join(opciones))
            if trans_id:
                lineas.append(f"• Última transacción: `{trans_id}`")
            if tokens:
                lineas.append(f"• Tokens Play guardados: {len(tokens)}")
            if updated:
                lineas.append(f"• Actualizado: {_fmt_dt_local_local(updated, tz_admin)}")

            texto = "\n".join(lineas)
        else:
            vence_txt = _fmt_dt_local_local(fin_dt, tz_admin) if fin_dt else "—"
            extra = f" · queda {dias}d" if fin_dt and dias is not None and dias >= 0 else ""
            texto = (
                f"{badge} `{uid}` · {estado} · {rest}"
                + (f"/{limit}" if limit else "")
                + f" · vence: {vence_txt}{extra} · {base_plan}"
                + (f" · tg:{tg}" if tg else "")
            )

        filas.append((estado, fin_dt or now, texto))

    if not filas:
        await update.message.reply_text("No hay suscripciones que coincidan con el filtro.")
        return

    # ordenar: activa → inactiva → expirada, y por fecha de fin
    orden_estado = {"activa": 0, "inactiva": 1, "expirada": 2}
    filas.sort(key=lambda r: (orden_estado.get(r[0], 9), r[1] or now))

    # contadores
    for est, _, _ in filas:
        counters[est] += 1

    total = len(snaps)
    header = (
        f"*Suscripciones* (total docs: {total})\n"
        f"• Activas: {counters['activa']}  ·  Inactivas: {counters['inactiva']}  ·  Expiradas: {counters['expirada']}\n"
    )
    if filtro != "todas":
        header += f"_Filtro aplicado_: *{filtro}*\n"
    header += "_Vista_: " + ("detalle\n" if modo_detalle else "compacta  (usa `detalle` para ampliar)\n")

    # Telegram 4096 chars → partimos en trozos
    CHUNK = 3500
    buffer = header + "\n"
    for _, __, linea in filas:
        if len(buffer) + len(linea) + 1 > CHUNK:
            await update.message.reply_text(buffer, parse_mode="Markdown", disable_web_page_preview=True)
            buffer = ""
        buffer += linea + "\n"

    if buffer.strip():
        await update.message.reply_text(buffer, parse_mode="Markdown", disable_web_page_preview=True)

#@profile
def _parse_iso_z(s: str | None) -> datetime | None:
    """Acepta '2025-09-20T18:35:47.867Z' o ISO sin tz; siempre devuelve UTC aware."""
    if not s:
        return None
    try:
        s = str(s)
        # Firestore puede guardar con "Z"
        if s.endswith("Z"):
            s = s.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None

#@profile
def _fmt_dt_local(dt: datetime, tz_str: str | None) -> str:
    """Formatea un datetime UTC a la zona del usuario."""
    try:
        tz = pytz.timezone(tz_str) if tz_str else pytz.utc
    except Exception:
        tz = pytz.utc
    return dt.astimezone(tz).strftime("%Y-%m-%d %H:%M:%S %Z")


#@profile
async def verificar_suscripcion(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Muestra en Telegram el detalle enriquecido de la(s) suscripción(es) del usuario."""
    chat_id = str(update.effective_user.id)

    # Intenta resolver el UUID canónico si tienes mapping; si no, usa el chat_id
    try:
        user_id = _user_id_from_chat(chat_id) or chat_id
    except Exception:
        user_id = chat_id

    # Zona horaria preferida del usuario (si tienes esa función)
    try:
        tz_str = await cargar_timezone_por_defecto(user_id)  # p.ej. "America/Santiago"
    except Exception:
        tz_str = "UTC"

    # --- Recolecta documentos candidatos (puede haber varios por "origen") ---
    docs: list[tuple[str, dict]] = []

    # 1) doc con id = user_id (alias canónico)
    try:
        snap = _subscription_doc(user_id).get()  # -> db.collection("suscripciones_user").document(user_id)
        if snap.exists:
            docs.append((snap.id, snap.to_dict() or {}))
    except Exception:
        pass

    # 2) docs con campo user_id == user_id
    try:
        for s in db.collection("suscripciones_user").where("user_id", "==", user_id).stream():
            docs.append((s.id, s.to_dict() or {}))
    except Exception:
        pass

    # 3) docs con campo telegram_id == chat_id
    try:
        for s in db.collection("suscripciones_user").where("telegram_id", "==", chat_id).stream():
            docs.append((s.id, s.to_dict() or {}))
    except Exception:
        pass

    # Dedup por id de documento
    uniq = {}
    for doc_id, data in docs:
        if doc_id not in uniq and isinstance(data, dict):
            uniq[doc_id] = data
    docs = list(uniq.items())

    if not docs:
        # Sin suscripción registrada
        if es_administrador(chat_id):
            await update.message.reply_text(
                "No se encontraron suscripciones asociadas a tu cuenta.\n"
                "Puedes activarla desde el panel de administración o con /agregar_suscripcion."
            )
            await menu_usuario_administrador(context, chat_id)
        else:
            await update.message.reply_text(
                "No tienes una suscripción activa. Ve a *Suscripción y Paquetes* en la app para contratar o renueva con /suscripcion.",
                parse_mode="Markdown"
            )
        return

    # --- Arma los mensajes enriquecidos ---
    now = datetime.now(timezone.utc)
    tarjetas: list[str] = []
    hay_activa = False

    for doc_id, data in docs:
        inicio = _parse_iso_z(data.get("inicio"))
        fin    = _parse_iso_z(data.get("fin"))
        trans_rest = int((data.get("transacciones_restantes") or 0) or 0)
        limite     = int((data.get("limite_transacciones") or 0) or 0)
        estado_raw = str(data.get("estado") or "").lower()

        # Estado calculado (toma prioridad sobre el guardado)
        if fin and fin < now:
            estado = "expirada"
        elif trans_rest <= 0:
            estado = "inactiva"
        else:
            estado = "activa"

        hay_activa = hay_activa or (estado == "activa")

        base_plan   = data.get("basePlanId") or data.get("base_plan_id") or "-"
        product_id  = data.get("productId") or "-"
        opciones    = data.get("opciones") or []
        transaction = data.get("transactionId") or data.get("transaction_id") or "-"
        tokens      = data.get("tokens") or []
        updated_at  = data.get("updated_at")

        # Firestore timestamp → datetime
        if hasattr(updated_at, "to_datetime"):
            updated_at = updated_at.to_datetime().astimezone(timezone.utc)
        elif isinstance(updated_at, str):
            updated_at = _parse_iso_z(updated_at)
        else:
            updated_at = None

        # Días restantes si corresponde
        dias_rest = (fin - now).days if fin else None

        badge = "✅" if estado == "activa" else ("⚠️" if estado == "inactiva" else "⛔")
        lineas = [
            f"{badge} *Estado*: {estado}  _(registrado: {estado_raw or '—'})_",
        ]
        if inicio:
            lineas.append(f"• Inicio: {_fmt_dt_local(inicio, tz_str)}")
        if fin:
            lineas.append(f"• Fin: {_fmt_dt_local(fin, tz_str)}")
        if dias_rest is not None and dias_rest >= 0:
            lineas.append(f"• Quedan: *{dias_rest}* días")
        if limite:
            lineas.append(f"• Transacciones: {trans_rest} / {limite}")
        else:
            lineas.append(f"• Transacciones restantes: {trans_rest}")

        lineas += [
            f"• Plan/basePlanId: `{base_plan}`",
            f"• Producto: `{product_id}`",
        ]

        if opciones:
            lineas.append("• Opciones: " + ", ".join(opciones))
        if transaction and transaction != "-":
            lineas.append(f"• Última transacción: `{transaction}`")
        if isinstance(tokens, list) and tokens:
            lineas.append(f"• Tokens Play guardados: {len(tokens)}")

        if updated_at:
            lineas.append(f"• Actualizado: {_fmt_dt_local(updated_at, tz_str)}")

        # Identificador por si hay varias
        lineas.append(f"• DocID: `{doc_id}`")

        tarjetas.append("\n".join(lineas))

    # Mensaje final
    if len(tarjetas) == 1:
        texto = "*Tu suscripción:*\n\n" + tarjetas[0]
    else:
        texto = "*Suscripciones encontradas:*\n\n" + "\n\n— — — — —\n\n".join(tarjetas)

    await update.message.reply_text(texto, parse_mode="Markdown")

    # Navegación contextual
    if hay_activa:
        if es_administrador(chat_id):
            await menu_usuario_administrador(context, chat_id)
        else:
            # tu firma de menú de usuario parece ser (bot, chat_id)
            await menu_usuario_registrado(context.bot, chat_id)

# ==============
# 💳 Flujos TRC20 (se mantienen, pero al verificar escribimos Firestore)
# ==============
#@profile
async def verificar_pago(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_chat_id = str(update.effective_user.id)
    args = context.args

    if len(args) != 2:
        await update.message.reply_text("Uso: /verificar_pago <id_pago> <hash_transaccion>")
        return

    id_pago_proporcionado, hash_proporcionado = args
    pagos_pendientes = await cargar_pagos_pendientes()

    # Evitar duplicados
    for pago in pagos_pendientes["pendientes"] + pagos_pendientes.get("verificados", []):
        if pago.get("hash_transaccion") == hash_proporcionado:
            await update.message.reply_text("El hash de transacción ya está asociado con otro pago.")
            return

    for pago in pagos_pendientes["pendientes"]:
        if pago["user_id"] == user_chat_id and pago["id_pago"] == id_pago_proporcionado:
            if pago["estado"] == "verificado":
                await update.message.reply_text("Este pago ya ha sido verificado.")
                return

            monto_esperado = pago["monto"]
            pago_verificado = await validar_pago_blockchain(hash_proporcionado, monto_esperado)
            if not pago_verificado:
                await update.message.reply_text("La transacción no fue verificada en la blockchain o el monto no coincide.")
                return

            # Activar suscripción en Firestore
            pago["estado"] = "verificado"
            pago["hash_transaccion"] = hash_proporcionado
            guardar_pagos_pendientes(pagos_pendientes)

            seleccion = pago["suscripcion"]
            inicio = _now_utc()
            dur = subscriptions_type[seleccion]["duracion"]
            fin   = _calc_fin_from_text(dur, inicio)

            user_uuid = _resolve_user_uuid(user_id=None, chat_id=user_chat_id)
            nombre = update.effective_user.full_name

            try:
                upsert_subscription_firestore(
                    user_id=user_uuid,
                    nombre_usuario=nombre,
                    tipo_suscripcion=seleccion,
                    origen="telegram",
                    id_pago=pago["id_pago"],
                    hash_transaccion=hash_proporcionado,
                    telegram_id=user_chat_id,
                    inicio=inicio,
                    fin=fin,
                )
            except Exception as e:
                await update.message.reply_text(f"⚠️ Pago verificado pero fallo guardando suscripción: {e}")
                return

            await update.message.reply_text(
                f"Pago verificado. Tu suscripción {seleccion} está activa hasta {fin}."
            )

            if not es_administrador(user_chat_id):
                await menu_usuario_registrado(context.bot, user_chat_id)
            else:
                await menu_usuario_administrador(context, user_chat_id)
            return

    await update.message.reply_text("No se encontró un pago pendiente con ese hash.")


#@profile
async def seleccionar_suscripcion(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Procesa la selección de suscripción y muestra detalles."""
    user_id = str(update.callback_query.message.chat_id)
    query = update.callback_query
    await query.answer()

    seleccion = query.data.replace(f"{user_id}_suscripcion_", "")
    suscripcion = subscriptions_type.get(seleccion)

    if suscripcion:
        detalles = (
            f"Suscripción: {seleccion.replace('-', ' ').title()}\n"
            f"Duración: {suscripcion.get('duracion', 'No especificada')}\n"
            f"Transacciones máximas: {suscripcion.get('transacciones_maximas', 'No especificado')}\n"
            f"Opciones disponibles:\n- " + "\n- ".join(suscripcion.get("opciones", []))
        )
        opciones = [
            [InlineKeyboardButton("Pagar ahora", callback_data=f"{user_id}_pagar_{seleccion}")],
            [InlineKeyboardButton("Cancelar", callback_data=f"{user_id}_suscripciones_cancelar")]  
        ]
        await query.edit_message_text(detalles, reply_markup=InlineKeyboardMarkup(opciones))
    else:
        await query.edit_message_text("Suscripción no encontrada.")


#@profile
def generar_hash(user_id, suscripcion):
    datos = f"{user_id}-{suscripcion}-{time.time()}"
    hash_valor = hashlib.sha256(datos.encode()).hexdigest()
    id_pago = str(int(hash_valor, 16))[:7]
    return id_pago

# Procesar selección de suscripción
#@profile
async def procesar_seleccion_suscripcion(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    seleccion = query.data.split("_")[2]
    user_chat_id = str(update.effective_user.id)

    # Agregar pago pendiente
    pagos_pendientes = await cargar_pagos_pendientes()
    id_pago = generar_hash(user_chat_id, seleccion)

    fecha_actual = datetime.now().isoformat()

    pago_info = {
        "user_id": user_chat_id,
        "monto": subscriptions_type[seleccion]["precio"],
        "id_pago": id_pago,
        "estado": "pendiente",
        "suscripcion": seleccion,
        "fecha": fecha_actual 
    }

    index_existente = next(
        (index for index, pago in enumerate(pagos_pendientes["pendientes"]) if pago["user_id"] == user_chat_id and pago["estado"] == "pendiente"), 
        None
    )

    if index_existente is not None:
        # Reemplazar el pago existente con el nuevo
        pagos_pendientes["pendientes"][index_existente] = pago_info
        logger.info(f"Pago pendiente actualizado para el usuario {user_chat_id}.")
    else:
        # Agregar un nuevo pago pendiente
        pagos_pendientes["pendientes"].append(pago_info)
        logger.info(f"Nuevo pago pendiente agregado para el usuario {user_chat_id}.")

    guardar_pagos_pendientes(pagos_pendientes)

    await query.message.reply_text(
        f"Has seleccionado la suscripción {seleccion}. Debes realizar un pago de {subscriptions_type[seleccion]['precio']} USDT a la billetera por la red TRC20:\n\n"
        f"{DIRECCION_USDT_TRC20}\n\n"
        "Incluye el siguiente id de pago para validar la transaccion:\n"
        f"{id_pago}\n\n"
        "Tras realizar el pago, utiliza la opción de menú 'Verificar suscripcion' para confirmar y activar el plan."
    )


#@profile
async def validar_pago_blockchain(hash_transaccion, monto_esperado, max_reintentos=3, tiempo_espera_inicial=5):
    # URL de la API de Tronscan para verificar transacciones
    url = f"https://apilist.tronscanapi.com/api/transaction-info?hash={hash_transaccion}"

    reintento = 0
    tiempo_espera = tiempo_espera_inicial
    
    while reintento < max_reintentos:
        try:
            # Realizar la solicitud GET a la API
            response = requests.get(url, timeout_request_global)

            # Verificar si la respuesta es exitosa
            if response.status_code == 200:
                data = response.json()

                # Comprobar el estado de la transacción
                if data['contractRet'] == 'SUCCESS':
                    # Obtener la información de la transferencia
                    transfers = data.get('trc20TransferInfo', [])
                    
                    # Si hay transferencias, validar la primera
                    if transfers:
                        transfer = transfers[0]
                        amount_sent = int(transfer['amount_str'])  # Monto enviado
                        decimals = int(transfer.get('decimals', 6))  # Decimales del token (por defecto 6)
                        amount_real = amount_sent / (10 ** decimals)  # Convertir a la cantidad real

                        to_address = transfer['to_address']  # Dirección del destinatario

                        # Validar la dirección de la billetera y el monto
                        if to_address.lower() == DIRECCION_USDT_TRC20.lower() and amount_real >= monto_esperado:
                            logger.info(f"Transacción válida. Dirección: {to_address}, Monto enviado: {amount_real}, Monto esperado: {monto_esperado}")
                            return True  # Transacción válida
                        else:
                            logger.info(f"Transacción no válida. Dirección: {to_address}, Monto enviado: {amount_real}, Monto esperado: {monto_esperado}")
                            return False  # Monto o dirección no coinciden
                    else:
                        logger.info("No se encontraron transferencias.")
                        return False  # No se encontraron transferencias
                else:
                    logger.info("Transacción no exitosa.")
                    return False  # Transacción no exitosa
            else:
                logger.info(f"Error al consultar la API: {response.status_code}")
                return False  # Error en la consultae}")
                return False  # Error en la consulta
        except requests.exceptions.RequestException as e:
                logger.info(f"Error de conexión: {e}")
                reintento += 1
                if reintento < max_reintentos:
                    logger.info(f"Reintentando url:{url} en {tiempo_espera} segundos...")
                    time.sleep(tiempo_espera)
                    tiempo_espera *= 2    

#@profile
async def listar_pagos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_chat_id = str(update.effective_user.id)
    pagos_pendientes = await cargar_pagos_pendientes()  # Esto debería devolver el JSON de los pagos

    # Filtrar los pagos del usuario actual
    pagos_usuario = [
        pago for pago in pagos_pendientes["pendientes"] if pago["user_id"] == user_chat_id
    ]

    # Ordenar los pagos por fecha en orden descendente (suponiendo que el campo 'fecha' exista y sea una cadena ISO 8601)
    pagos_usuario.sort(key=lambda x: x.get("fecha", ""), reverse=True)

    if not pagos_usuario:
        await update.message.reply_text("No tienes pagos pendientes.")
        return

    # Crear el mensaje con los pagos ordenados
    mensaje = "Tus pagos pendientes:\n\n"
    for pago in pagos_usuario:
        id_pago = pago["id_pago"]
        mensaje += (
            f"Id Pago: {id_pago}\n"
            f"Monto: {pago['monto']} USDT\n"
            f"Dirección TRC20: {DIRECCION_USDT_TRC20}\n"
            f"Estado: {pago['estado']}\n"
            f"Hash: {pago.get('hash_transaccion', 'No enviado')}\n"
            f"Suscripción: {pago['suscripcion']}\n"
            f"Fecha: {pago.get('fecha', 'Fecha no disponible')}\n\n"
        )

    await update.message.reply_text(mensaje)



# Programa la tarea para que se ejecute todos los días a las 00:00
scheduler = BackgroundScheduler()
#@profile
def programar_actualizacion_menus(application: Application):
    loop = asyncio.get_running_loop()

    #@profile
    def actualizar():
        asyncio.run_coroutine_threadsafe(actualizar_menus(application), loop)

    scheduler.add_job(
        actualizar,
        IntervalTrigger(minutes=10),  # Se ejecutará cada 10 minutos
    )

    scheduler.start()

#@profile
async def menu_zonas_horarias(update, context):
    # Crear los botones para cada zona horaria
    user_id = update.effective_chat.id
    botones = [
        [InlineKeyboardButton(zona, callback_data=f"{user_id}_timezone_{zona}")]
        for zona in zonas_horarias
    ]
    botones.append([InlineKeyboardButton("Cancelar", callback_data=f"{user_id}_zonas_horarias_cancelar")])
    teclado = InlineKeyboardMarkup(botones)

    # Enviar el mensaje con los botones
    await update.message.reply_text(
        "Selecciona tu zona horaria:",
        reply_markup=teclado
    )

#@profile
async def cargar_noticias_en_memoria():
    global cache_noticias
    for archivo in os.listdir(CARPETA_FOREX_NEWS):
        if archivo.endswith("_noticias.json"):
            partes_archivo = archivo.split("_")
            if len(partes_archivo) < 3:
                continue  # Saltar archivos con un nombre inesperado

            symbol = partes_archivo[0]
            temporalidad = partes_archivo[1]

            if temporalidad not in temporalidades:
                continue

            archivo_cache = os.path.join(CARPETA_FOREX_NEWS, archivo)
            if os.path.exists(archivo_cache):
                with open(archivo_cache, "r", encoding="utf-8") as file:
                    try:
                        data_local = json.load(file)
                        if isinstance(data_local, list) and len(data_local) > 0:
                            df_local = pd.DataFrame(data_local)
                            if 'publishedDate' in df_local.columns:
                                df_local["publishedDate"] = pd.to_datetime(
                                    df_local["publishedDate"], errors="coerce"
                                )
                                df_local = df_local.dropna(subset=["publishedDate"])
                                df_local["publishedDate"] = (
                                    df_local["publishedDate"]
                                    .dt.tz_localize(pytz.UTC)  # Asignar timezone UTC
                                    .dt.tz_convert(pytz.UTC)  # Convertir al timezone del usuario
                                )
                            if symbol not in cache_noticias:
                                cache_noticias[symbol] = {}
                            cache_noticias[symbol][temporalidad] = df_local.sort_values("publishedDate")
                            logger.info(f"Noticias cargadas en memoria para {symbol} ({temporalidad}).")
                    except Exception as e:
                        logger.info(f"Error al cargar noticias de {symbol} ({temporalidad}): {e}")
    logger.info("Noticias cargadas en memoria.")


#@profile
async def guardar_noticias_forex():
    """
    Guarda las noticias Forex almacenadas en `cache_noticias` en archivos locales.
    """
    try:
        for symbol, df_local in cache_noticias.items():
            archivo_cache = os.path.join(CARPETA_FOREX_NEWS, f"{symbol}_noticias.json")
            if 'publishedDate' in df_local.columns:
                df_local["publishedDate"] = df_local["publishedDate"].dt.tz_localize(pytz.UTC)  # Eliminar timezone
            async with aiofiles.open(archivo_cache, "w", encoding="utf-8") as file:
                await file.write(df_local.to_json(orient="records", date_format="iso"))
            logger.info(f"Noticias guardadas para {symbol}.")
    except Exception as e:
        logger.info(f"Error al guardar noticias Forex: {e}")

#@profile
async def guardar_datos_historicos(): 
    """
    Guarda los datos del diccionario global `cache_historicos` en archivos locales.
    """
    async with guardar_lock:
        try:
            for symbol, temporalidades in cache_historicos.items():
                if not temporalidades:
                    logger.info(f"Advertencia: No se encontraron datos para el símbolo {symbol}.")
                    continue

                for temporalidad, df in temporalidades.items():
                    if df.empty:
                        logger.info(f"Advertencia: El DataFrame para {symbol} en {temporalidad} está vacío.")
                        continue

                    if not isinstance(df.index, pd.DatetimeIndex):
                        logger.info(f"Advertencia: El índice del DataFrame para {symbol} en {temporalidad} no es un DatetimeIndex.")
                        continue
                    
                    # Asegurar que 'date' esté como columna
                    df = df.reset_index()
                    archivo_cache = os.path.join(CARPETA_HISTORICOS, f"{symbol}_{temporalidad}.json")
                    async with aiofiles.open(archivo_cache, mode="w", encoding="utf-8") as file:
                        await file.write(df.to_json(orient="records", date_format="iso"))
                    logger.info(f"Guardados datos históricos para {symbol} en {temporalidad}.")
        except Exception as e:
            logger.info(f"Error al guardar datos históricos: {e}")

#@profile
async def descargar_manual(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Envía el manual PDF al usuario."""
    user_chat_id = update.effective_chat.id
    ruta_manual = "ManualMT.pdf"  # Ajusta el nombre del archivo si es diferente

    try:
        # Verificar si el archivo existe antes de enviarlo
        if os.path.exists(ruta_manual):
            await context.bot.send_document(
                chat_id=user_chat_id,
                document=open(ruta_manual, "rb"),
                caption="Aquí tienes el manual de usuario en formato PDF."
            )
        else:
            await update.message.reply_text("El manual no se encuentra disponible en este momento.")
    except Exception as e:
        logger.info(f"Error al enviar el manual al usuario {user_chat_id}: {e}")
        await update.message.reply_text("Ocurrió un error al intentar enviar el manual.")



#@profile
async def cancelar_envio_mensaje(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancela el envío de mensaje y resetea el estado en Firestore."""
    query = update.callback_query
    await query.answer()

    user_id = str(update.effective_user.id)


    # Obtener archivos guardados en Firestore
    user_ref = db.collection("user_states").document(user_id)

    # Limpiar datos en Firestore
    user_ref.set({
        "mensaje_admin": None,
        "archivos_guardados": [],
        "destinatario_manual": None,
        "destinatarios": None,
        "estado": "disponible"  # 👈 ¡Estado cambiado a "disponible" aquí!
    }, merge=True)


    await query.edit_message_text("🚫 Envío de mensaje cancelado.")


#@profile
async def confirmar_envio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Envía el mensaje a los destinatarios seleccionados (por user_id) y limpia el estado."""
    query = update.callback_query
    await query.answer()

    admin_chat_id = str(update.effective_user.id)

    # Datos guardados en Firestore por tu flujo de admin
    user_ref = db.collection("user_states").document(admin_chat_id)
    user_data = user_ref.get()
    datos_usuario = user_data.to_dict() if user_data.exists else {}

    mensaje = datos_usuario.get("mensaje_admin")
    archivos_guardados = datos_usuario.get("archivos_guardados", [])
    destinatarios_tipo = datos_usuario.get("destinatarios", "todos")
    destinatario_manual = datos_usuario.get("destinatario_manual")

    # --- 1) Construir destinatarios como **user_id** ---
    suscripciones = await cargar_datos_subscription_user()  # {user_id: {...}}
    if destinatario_manual:
        # Si en tu UI el manual es user_id, lo dejas tal cual; si a veces es chat_id, mapea a user_id aquí.
        destinatarios = [str(destinatario_manual)]
    elif destinatarios_tipo == "todos":
        destinatarios = list(suscripciones.keys())
    elif destinatarios_tipo == "suscriptores_activos":
        destinatarios = [
            uid for uid in suscripciones.keys()
            if await estado_suscripcion(user_id=uid, origen="app") == "activa"
        ]
    elif destinatarios_tipo == "suscriptores_inactivos":
        destinatarios = [
            uid for uid in suscripciones.keys()
            if await estado_suscripcion(user_id=uid, origen="app") in ("inactiva", "expirada", "transacciones_insuficientes")
        ]
    else:
        destinatarios = []

    # --- 2) Enviar por Telegram SOLO si el doc tiene telegram_id ---
    for user_id in destinatarios:
        try:
            s = suscripciones.get(user_id) or {}
            chat_id = s.get("telegram_id")  # opcional: si no está, ese usuario no usa TG
            if not chat_id:
                continue  # no hay donde enviar por TG; lo saltamos

            texto_enviado = False

            for archivo in archivos_guardados:
                tipo = archivo.get("tipo")
                file_id = archivo.get("file_id")
                if not file_id:
                    continue

                if tipo == "imagen":
                    await context.bot.send_photo(chat_id=chat_id, photo=file_id, caption=mensaje if not texto_enviado else None)
                    texto_enviado = True
                elif tipo == "video":
                    await context.bot.send_video(chat_id=chat_id, video=file_id, caption=mensaje if not texto_enviado else None)
                    texto_enviado = True
                elif tipo == "documento":
                    await context.bot.send_document(chat_id=chat_id, document=file_id)

            if mensaje and not texto_enviado:
                await context.bot.send_message(chat_id=chat_id, text=mensaje)

        except Exception as e:
            print(f"⚠️ Error al enviar a user_id {user_id}: {e}")

    # --- 3) Limpiar estado ---
    user_ref.set({
        "mensaje_admin": None,
        "archivos_guardados": [],
        "destinatario_manual": None,
        "destinatarios": None,
        "estado": "disponible"
    }, merge=True)

    await query.edit_message_text("✅ Mensaje enviado a los destinatarios seleccionados.")    

#@profile
async def procesar_envio_mensaje(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Guarda la opción elegida y activa el modo de envío de mensaje."""
    user_chat_id = str(update.effective_chat.id)
    query = update.callback_query
    await query.answer()

    if return_state(chat_id=user_chat_id) == "en ejecución":
        await context.bot.send_message(
            chat_id=user_chat_id,
            text="Ya tienes un análisis en ejecución. Por favor, espera a que termine."
        )
        return

    destinatarios = query.data.replace("mensaje_", "")

    # Si elige "usuario_especifico", pedimos que ingrese el ID manualmente
    if destinatarios == "usuario_especifico":
        # Guardar estado temporal en Firestore para esperar el ID manualmente
        mark_user_state(user_id=user_chat_id, estado="esperando_id_usuario", extra={"destinatarios": destinatarios})
        await query.edit_message_text("🔢 Por favor, ingresa el ID del usuario al que deseas enviar el mensaje:")
        return  # No continuamos hasta recibir el ID

    # Guardar estado y destinatarios en Firestore para envío masivo
    mark_user_state(user_id=user_chat_id, estado="modo_envio_mensaje", extra={"destinatarios": destinatarios})
    await query.edit_message_text("✍️ Envía el mensaje o archivo que deseas compartir.")


#@profile
async def enviar_mensaje(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Muestra opciones para seleccionar destinatarios del mensaje."""
    user_id = str(update.effective_user.id)

    if not es_administrador(user_id):
        await update.message.reply_text("🚫 No tienes permisos para usar este comando.")
        return

    keyboard = [
        [InlineKeyboardButton("🟢 Todos los usuarios", callback_data="mensaje_todos")],
        [InlineKeyboardButton("🔵 Solo suscriptores activos", callback_data="mensaje_suscriptores_activos")],
        [InlineKeyboardButton("🟠 Solo suscriptores inactivos", callback_data="mensaje_suscriptores_inactivos")],
        [InlineKeyboardButton("🟣 Usuario específico", callback_data="mensaje_usuario_especifico")], 
        [InlineKeyboardButton("❌ Cancelar", callback_data="cancelar_envio_mensaje")]
    ]

    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("📢 ¿A quién deseas enviar el mensaje?", reply_markup=reply_markup)


# Crear la aplicación del bot de Telegram
application = Application.builder().token(os.environ["BOT_TOKEN"]).build()

# Configurar manejadores
application.add_handler(CommandHandler('start', start))
application.add_handler(CommandHandler("trader_menu", trader_menu))
application.add_handler(CommandHandler("analizar_simbolo", analizar_simbolo))
application.add_handler(CommandHandler("stop", stop))
application.add_handler(CommandHandler("eventos_futuros", manejar_fecha_eventos))
application.add_handler(CommandHandler("noticias_user", manejar_fecha_noticias_user))
application.add_handler(CommandHandler("noticias_admin", manejar_fecha_noticias_admin))
application.add_handler(CommandHandler("noticias_general", obtener_noticias_generales))
application.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, manejar_respuesta_fechas))
application.add_handler(CommandHandler("reset_menu", comando_reset_menu))
application.add_handler(CallbackQueryHandler(menu, pattern=r"^\d+_menu"))
application.add_handler(CallbackQueryHandler(seleccionar_par, pattern=r"^\d+_par_"))
application.add_handler(CommandHandler("set_timezone", menu_zonas_horarias))
application.add_handler(CallbackQueryHandler(seleccionar_zona_horaria, pattern=r"^\d+_timezone_"))
application.add_handler(CommandHandler("ia_grafico", manejar_ia_grafico))

# Comandos de administrador
application.add_handler(CommandHandler("agregar_suscripcion", agregar_suscripcion))
application.add_handler(CommandHandler("eliminar_suscripcion", eliminar_suscripcion))
application.add_handler(CommandHandler("listar_suscripciones", listar_suscripciones))

# Comandos generales
application.add_handler(CommandHandler("verificar_suscripcion", verificar_suscripcion))
application.add_handler(CommandHandler("menu_suscripciones", mostrar_menu_suscripciones))
application.add_handler(CallbackQueryHandler(procesar_seleccion_suscripcion, pattern=r"^\d+_pagar_"))
application.add_handler(CallbackQueryHandler(seleccionar_suscripcion, pattern=r"^\d+_suscripcion_"))
application.add_handler(CallbackQueryHandler(cancelar_suscripcion, pattern=r"^\d+_suscripciones_cancelar$"))
application.add_handler(CallbackQueryHandler(cancelar_zonas_horarias, pattern=r"^\d+_zonas_horarias_cancelar$"))
application.add_handler(CommandHandler("verificar_pago", verificar_pago))
application.add_handler(CommandHandler("listar_pagos", listar_pagos))
application.add_handler(CommandHandler("enviar_mensaje", enviar_mensaje))
application.add_handler(CallbackQueryHandler(procesar_envio_mensaje, pattern="^mensaje_"))
application.add_handler(CallbackQueryHandler(confirmar_envio, pattern="^confirmar_envio$"))
application.add_handler(CallbackQueryHandler(cancelar_envio_mensaje, pattern="^cancelar_envio_mensaje$"))
application.add_handler(CommandHandler("descargar_manual", descargar_manual))

webhook_app = Flask(__name__)
# Convierte la aplicación Flask a ASGI
asgi_app = WsgiToAsgi(webhook_app)

#@profile
async def guardar_noticias_forex_diarias():
    """
    Ejecuta `guardar_noticias_forex` una vez al día, a medianoche.
    """
    while True:
        ahora = datetime.now()
        siguiente_medianoche = datetime.combine(ahora.date() + timedelta(days=1), datetime.min.time())
        tiempo_para_guardar = (siguiente_medianoche - ahora).total_seconds()

        logger.info(f"Esperando {tiempo_para_guardar} segundos para guardar noticias Forex...")

        await asyncio.sleep(tiempo_para_guardar)
        await guardar_noticias_forex()


#@profile
async def guardar_datos_historicos_diarios():
    """
    Ejecuta `guardar_datos_historicos` una vez al día, a medianoche.
    """
    while True:
        ahora = datetime.now()
        siguiente_medianoche = datetime.combine(ahora.date() + timedelta(days=1), datetime.min.time())
        tiempo_para_guardar = (siguiente_medianoche - ahora).total_seconds()

        logger.info(f"Esperando {tiempo_para_guardar} segundos para guardar datos históricos...")

        await asyncio.sleep(tiempo_para_guardar)
        await guardar_datos_historicos()


# Cargar datos iniciales
#@profile
async def initialize_bot():
    try:
        global subscriptions, subscriptions_type, clientes_chat_ids, admin_ids

        # Inicializar la aplicación
        logger.info("Inicializando la aplicación de Telegram...")
        start_time = time.time()
        await application.initialize()
        logger.info(f"Tiempo en inicializar aplicación: {time.time() - start_time:.2f} segundos")

        # Cargar datos iniciales
        logger.info("Cargando datos iniciales...")
        (
            subscriptions,
            subscriptions_type,
            clientes_chat_ids,
            admin_ids,
        ) = await asyncio.gather(
            cargar_datos_subscription_user(),
            cargar_datos_subscription_type(),
            cargar_chat_ids(),
            cargar_admin_ids(),
        )

        logger.info("Cargando noticias y datos históricos...")
        await asyncio.gather(
            cargar_noticias_en_memoria(),
            cargar_datos_historicos_inicial(),
        )

        # Programar el guardado diario
        asyncio.create_task(guardar_noticias_forex_diarias())
        asyncio.create_task(guardar_datos_historicos_diarios())

        # Ejecutar la primera actualización de menús en segundo plano
        asyncio.create_task(actualizar_menus(application))

        logger.info("Actualizar menus de usuarios telegram...")
        programar_actualizacion_menus(application)

        # Configurar webhook
        webhook_url = os.environ.get("WEBHOOK_URL")
        logger.info(f"WEBHOOK_URL = {webhook_url}")
        if webhook_url:
            logger.info("Configurando webhook...")
            full_webhook_url = f"https://{webhook_url}/webhook"
            current_webhook = await application.bot.get_webhook_info()
            logger.info(f"EL Webhook configurado en telegram es: {current_webhook}")
            if current_webhook.url != full_webhook_url:
                logger.info(f"Entro a actualizar el Webhook {full_webhook_url}")
                await application.bot.delete_webhook(drop_pending_updates=True)
                cert_path = os.getenv("WEBHOOK_CERT_PATH", "cert.crt")
                with open(cert_path, "rb") as cert:
                    await application.bot.set_webhook(full_webhook_url, certificate=cert)
                logger.info(await application.bot.get_webhook_info())
                logger.info(f"Webhook configurado exitosamente en {full_webhook_url}")
            else:
                logger.info(f"Webhook ya se encontraba configurado en {full_webhook_url}")
            
        else:
            logger.info("No se encontró WEBHOOK_URL. Se ejecutará localmente en modo local con polling...")
            asyncio.create_task(application.start())
            asyncio.create_task(application.updater.start_polling())
            try:
                await asyncio.Event().wait()
            except KeyboardInterrupt:
                logger.info("Deteniendo el bot...")
            finally:
                await guardar_datos_historicos()
                await guardar_noticias_forex()
                await application.updater.stop()
                await application.stop()
                await application.shutdown()

        logger.info("Bot inicializado correctamente.")
    except Exception as e:
        logger.info(f"Error durante la inicialización del bot: {e}")



# Cache de DF por símbolo (evita martillar FMP si poleás cada 1s)
_EVENTS_MEMO: Dict[str, Dict[str, Any]] = {}   # symbol -> {"df": DataFrame, "ts": epoch_s}
# Últimos valores 'actual' vistos por (symbol, event_key) para detectar cambios
# event_key = (currency, event, date_ms UTC)
_LAST_ACTUAL: Dict[Tuple[str, Tuple[str,str,int]], float] = {}
# Último hash enviado al cliente por (exec_id, symbol)
_LAST_HASH: Dict[Tuple[str,str], str] = {}


# Intervalo mínimo entre llamados “caros” a FMP por símbolo
MIN_FETCH_INTERVAL_S = 5


BUCKET_NAME = "markettool_bucket"

# Mapea los nombres de timeframe que te gustan en el app
TIMEFRAME_MAP = {
    "1min": "1min",
    "5min": "5min",
    "15min": "15min",
    "30min": "30min",
    "1hour": "1hour",
    "1day": "1day",
    "1week": "1week",
}

_LAST_INTERNAL_GAP_ATTEMPT = {}  # dict[(symbol, tf, from_ms, to_ms)] = epoch_s
_INTERNAL_GAP_COOLDOWN_S = {
    "1min": 90, "5min": 180, "15min": 240, "30min": 300, "1hour": 600, "4hour": 900,
}

_BACKFILL_IN_FLIGHT = set()  # set([(symbol, tf)])

_LAST_BACKFILL_EMPTY = {}  # dict[(symbol, tf)] = (from_ms, to_ms, epoch_s)
_EMPTY_SUPPRESS_S = 60


_LAST_RANGE_BACKFILL_ATTEMPT = {}  # {(symbol, tf): epoch_s}
_LAST_BACKFILL_ATTEMPT = {}   # dict[ (symbol, tf) ] -> epoch_seconds
_LAST_BACKFILL_RANGE   = {}   # dict[ (symbol, tf) ] -> (from_ms, to_ms)

_BACKFILL_COOLDOWN_S = {
    "1min": 180,  # 3 min
    "5min": 300,
    "15min": 600,
    "30min": 900,
    "1hour": 1200,
    "4hour": 1800,
}
_MAX_BACKFILL_BARS = {
    "1min": 1500,  # limita tamaño de cada rango para evitar timeouts
    "5min": 1500,
    "15min": 1500,
    "30min": 1500,
    "1hour": 1500,
    "4hour": 1500,
}

_FLUSHER_STARTED = False

from contextlib import contextmanager
@contextmanager
def _backfill_guard(symbol: str, tf: str):
    key = (symbol, tf)
    if key in _BACKFILL_IN_FLIGHT:
        yield False
        return
    _BACKFILL_IN_FLIGHT.add(key)
    try:
        yield True
    finally:
        _BACKFILL_IN_FLIGHT.discard(key)

# -------------------------
# Utilidades GCS
# -------------------------

# --- cache en memoria (compartido por proceso) ---
_MON_CACHE_LOCK = threading.RLock()
# estructura: { (exec_id, SYMBOL, timeframe): {"series": [candles], "dirty": False, "source": "gcs|memory", "ts_loaded": int} }
_MON_CACHE: Dict[tuple, Dict[str, Any]] = {}

# ---------- Helpers NO destructivos (solo definen si no existen) ----------

def _gcs_exec_base(exec_id: str) -> str:
    # Path base donde guardas los archivos del análisis (según tus capturas):
    # gs://markettool_bucket/analisis/exec/<exec_id>/
    return f"analisis/exec/{exec_id}/"

def _gcs_file_name_for(symbol: str, timeframe: str) -> str:
    # Usa los nombres que vi en tu bucket: BTCUSD_1min_enriched.json, etc.
    tf_map = {
        "1min": "1min",
        "5min": "5min",
        "15min": "15min",
        "30min": "30min",
        "1hour": "1hour",
        "1day": "1day",
        "4hour": "4hour",
        "1week": "1week",
    }
    norm = tf_map.get(timeframe, timeframe)
    return f"{symbol.upper()}_{norm}_enriched.json"


def _download_json_from_gcs(path: str) -> Any:
    client = storage.Client()
    """
    Lee un JSON de GCS (usando storage_client global si ya existe).
    Retorna dict/list. Lanza excepción si falla.
    """
    # Requiere google-cloud-storage y que tengas storage_client global (ya suele existir en tu app).
    bucket = client.bucket(BUCKET_NAME)
    blob = bucket.blob(path)
    data = blob.download_as_bytes()
    return json.loads(data.decode("utf-8"))



def _persist_if_needed(exec_id: str, symbol: str, timeframe: str, force: bool = False) -> Optional[str]:
    client = storage.Client()
    key = (exec_id, symbol.upper(), timeframe)
    with _MON_CACHE_LOCK:
        state = _MON_CACHE.get(key)
        if not state:
            return None
        if not state.get("dirty") and not force:
            return None
        path = _gcs_stream_path(exec_id, symbol, timeframe)
        payload = json.dumps(state["series"], ensure_ascii=False).encode("utf-8")
        bucket = client.bucket(BUCKET_NAME)
        blob = bucket.blob(path)
        blob.upload_from_string(payload, content_type="application/json")
        blob.reload()
        state["dirty"] = False
        # >>> reflejar metadata para que _maybe_refresh_from_gcs no re-baje de inmediato
        state["gcs_path"] = path
        try:
            state["gcs_generation"] = blob.generation
            state["gcs_updated"] = blob.updated.timestamp() if blob.updated else time.time()
        except Exception:
            state["gcs_updated"] = time.time()
        state["ts_loaded"] = time.time()
        return path



def _load_cache(exec_id: str, symbol: str, tf: str) -> dict:
    key = (exec_id, symbol.upper(), tf)
    with _MON_CACHE_LOCK:
        if key in _MON_CACHE:
            return _MON_CACHE[key]

    # 1) intenta stream primero
    stream_path = _gcs_stream_path(exec_id, symbol, tf)
    if _gcs_exists(stream_path):
        raw = _gcs_read_json(stream_path) or {"series": []}
        data = _coerce_series_container(raw)
        data["source"] = "stream"
    else:
        # 2) si no hay stream, cae a enriched
        enriched_path = _gcs_enriched_path(exec_id, symbol, tf)
        if _gcs_exists(enriched_path):
            raw = _gcs_read_json(enriched_path) or {"series": []}
            data = _coerce_series_container(raw)
            data["source"] = "enriched"
            # Promueve enriched->stream una única vez (para que el resto ya use stream)
            try:
                _ensure_stream_initialized(exec_id, symbol, tf, data)
            except Exception:
                pass
        else:
            data = {"series": [], "source": "empty"}

    with _MON_CACHE_LOCK:
        _MON_CACHE[key] = data
    return data


def fs_touch_monitoreo(exec_id: str, symbol: str, data: Dict[str, Any]) -> None:
    try:
        doc_id = f"{exec_id}__{(symbol or '').upper()}"
        ref = db.collection("monitoreos").document(doc_id)
        snap = ref.get()
        cur = (snap.to_dict() if snap.exists else {}) or {}

        incoming_tf = data.get("tf_states")
        if isinstance(incoming_tf, dict):
            cur_tf = dict(cur.get("tf_states") or {})
            for tf_k, tf_v in incoming_tf.items():
                if isinstance(tf_v, dict) and isinstance(cur_tf.get(tf_k), dict):
                    merged = dict(cur_tf.get(tf_k) or {})
                    merged.update(tf_v)
                    cur_tf[tf_k] = merged
                else:
                    cur_tf[tf_k] = tf_v
            cur["tf_states"] = cur_tf
            data = dict(data)
            data.pop("tf_states", None)

        cur.update(data)
        cur["updated_at"] = int(time.time() * 1000)
        ref.set(cur, merge=True)
    except Exception:
        pass


def _maybe_refresh_from_gcs(exec_id: str, symbol: str, timeframe: str, st: dict, max_age_s: int = 30):
    try:
        client = storage.Client()
        for path in (
            _gcs_stream_path(exec_id, symbol, timeframe),
            f"{_gcs_exec_base(exec_id)}{_gcs_file_name_for(symbol, timeframe)}",
        ):
            bucket = client.bucket(BUCKET_NAME)
            blob = bucket.blob(path)
            if not blob.exists():
                continue
            blob.reload()
            changed = (st.get("gcs_generation") != blob.generation) or \
                      (st.get("gcs_updated", 0) < (blob.updated.timestamp() if blob.updated else 0))
            too_old = (time.time() - st.get("ts_loaded", 0)) > max_age_s
            if not (changed or too_old):
                return
            js = _download_json_from_gcs(path)
            if isinstance(js, dict) and "series" in js and "candles" in js["series"]:
                arr = js["series"]["candles"]
            elif isinstance(js, dict) and "candles" in js:
                arr = js["candles"]
            elif isinstance(js, list):
                arr = js
            else:
                arr = []
            norm = _series_to_ms(arr)

            prev = st.get("series") or []
            if prev:
                merged = _snap_and_dedupe_to_minutes(prev + norm, timeframe)  # usa tu tf actual si lo tienes en alcance
                st_series = merged
            else:
                st_series = norm

            src = "stream" if path.endswith(f"{symbol}_{timeframe}.json") else "enriched"
            st.update({
                "series": st_series,
                "gcs_path": path,
                "gcs_generation": blob.generation,
                "gcs_updated": blob.updated.timestamp() if blob.updated else time.time(),
                "ts_loaded": time.time(),
                "source": src
            })
            return
    except Exception:
        pass




def _to_ms(t):
    """
    Convierte múltiples formatos de tiempo a epoch ms.
    Soporta:
      - int/float en segundos o milisegundos
      - str numérico o ISO ('YYYY-MM-DD HH:MM:SS' / 'YYYY-MM-DDTHH:MM:SSZ')
      - Firestore Timestamp (objeto con .seconds/.nanoseconds o dict {'seconds','nanoseconds'})
      - datetime (naive -> UTC, aware -> su tz)
    Devuelve int ms o None si no puede convertir.
    """
    if t is None:
        return None

    # Firestore Timestamp objeto
    try:
        # google.cloud.firestore_v1._helpers.Timestamp o similares
        if hasattr(t, "seconds") and hasattr(t, "nanoseconds"):
            return int(t.seconds * 1000 + t.nanoseconds / 1_000_000)
    except Exception:
        pass

    # Firestore Timestamp dict
    if isinstance(t, dict) and ("seconds" in t or "nanoseconds" in t):
        try:
            seconds = int(t.get("seconds", 0))
            nanos   = int(t.get("nanoseconds", 0))
            return int(seconds * 1000 + nanos / 1_000_000)
        except Exception:
            return None

    # datetime
    if isinstance(t, datetime):
        try:
            dt = t if t.tzinfo else t.replace(tzinfo=timezone.utc)
            return int(dt.timestamp() * 1000)
        except Exception:
            return None

    # numérico (int/float)
    if isinstance(t, (int, float)):
        # Heurística: si es < 1e12 asumimos segundos, si no, milisegundos
        ts = float(t)
        return int(ts * 1000) if ts < 1_000_000_000_000 else int(ts)

    # string
    if isinstance(t, str):
        s = t.strip()
        # 1) numérico en string
        try:
            # permite floats en string
            num = float(s)
            return int(num * 1000) if num < 1_000_000_000_000 else int(num)
        except Exception:
            pass
        # 2) ISO/fechas comunes
        try:
            # normaliza 'T' y 'Z'
            s2 = s.replace("T", " ").replace("Z", "")
            # formatos más comunes de FMP y otros
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
                try:
                    dt = datetime.strptime(s2, fmt).replace(tzinfo=timezone.utc)
                    return int(dt.timestamp() * 1000)
                except Exception:
                    continue
            # última chance: fromisoformat (soporta fracciones de segundo)
            try:
                dt = datetime.fromisoformat(s2).replace(tzinfo=timezone.utc)
                return int(dt.timestamp() * 1000)
            except Exception:
                pass
        except Exception:
            return None

    # tipo no soportado
    return None


def _as_list(obj):
    # Convierte cualquier iteración rara en lista simple
    if obj is None:
        return []
    if isinstance(obj, list):
        return obj
    if isinstance(obj, tuple):
        return list(obj)
    return [obj]

def _coerce_series_container(js: Any) -> dict:
    """
    Normaliza lo que venga de GCS a un contenedor dict {'series': list}.
    Acepta:
      - list                      -> {'series': list}
      - {'series': list}          -> idem
      - {'candles': list}         -> {'series': list}
      - {'series': {'candles': list}} -> {'series': list}
      - cualquier otro            -> {'series': []}
    """
    try:
        if isinstance(js, list):
            return {"series": js}
        if isinstance(js, dict):
            if isinstance(js.get("series"), list):
                return {"series": js["series"]}
            if isinstance(js.get("candles"), list):
                return {"series": js["candles"]}
            if isinstance(js.get("series"), dict) and isinstance(js["series"].get("candles"), list):
                return {"series": js["series"]["candles"]}
    except Exception:
        pass
    return {"series": []}


def _coerce_series(series_any) -> list:
    """
    Acepta múltiples formatos y devuelve SIEMPRE list[dict] con llaves {t,o,h,l,c,v?}.
    Soporta:
      - str con JSON
      - dict con 'series' o 'candles'
      - list de dicts ya normalizados
      - list de dicts "simples" que solo tienen 't' y 'c'
      - list de timestamps/strings (se convierte a [{'t': ts}])
    """
    try:
        s = series_any
        # 1) Si es string, intenta parsear JSON
        if isinstance(s, str):
            try:
                s = json.loads(s)
            except Exception:
                # no es JSON válido → devolver lista vacía para no romper
                return []

        # 2) Si es dict que contiene 'series' o 'candles'
        if isinstance(s, dict):
            if "series" in s and isinstance(s["series"], dict) and "candles" in s["series"]:
                s = s["series"]["candles"]
            elif "series" in s and isinstance(s["series"], list):
                s = s["series"]
            elif "candles" in s and isinstance(s["candles"], list):
                s = s["candles"]
            else:
                # dict sin estructura conocida → lista vacía
                return []

        # 3) Si es lista, normaliza cada item
        s = _as_list(s)
        out = []
        for item in s:
            if isinstance(item, dict):
                # Clonar y dejar pasar las llaves reconocidas
                t = item.get("t") if "t" in item else item.get("time") or item.get("timestamp")
                if t is None:
                    # a veces FMP trae 'date' ISO, conviértelo
                    if "date" in item:
                        try:
                            t = _parse_fmp_datetime_to_ms(str(item["date"]))
                        except Exception:
                            continue
                d = {
                    "t": t,
                    "o": item.get("o", item.get("open")),
                    "h": item.get("h", item.get("high")),
                    "l": item.get("l", item.get("low")),
                    "c": item.get("c", item.get("close")),
                    "v": item.get("v", item.get("volume", 0)),
                }
                out.append(d)
            else:
                # si es un escalar (timestamp numérico o str), úsalo como 't'
                try:
                    ts = _to_ms(item)
                    if ts is not None:
                        out.append({"t": ts})
                except Exception:
                    continue
        return out
    except Exception:
        return []


def _series_to_ms(series_any):
    """
    Garantiza list[dict] y convierte 't' a ms, orden ascendente.
    Ignora elementos sin 't' válido.
    """
    series = _coerce_series(series_any)
    out = []
    for c in (series or []):
        t = _to_ms(c.get("t"))
        if t is None:
            continue
        # clona y fija 't' en ms
        cc = dict(c)
        cc["t"] = t
        out.append(cc)
    out.sort(key=lambda x: x["t"])
    return out


_TF_MAP = {
    "1m":"1min", "5m":"5min", "15m":"15min", "30m":"30min",
    "1h":"1hour", "4h":"4hour", "1d":"1day", "1w":"1week",
    "1min":"1min", "5min":"5min", "15min":"15min", "30min":"30min",
    "1hour":"1hour", "4hour":"4hour", "1day":"1day", "1week":"1week",
}
def _norm_tf(tf):
    tf = (tf or "").lower()
    return _TF_MAP.get(tf, tf)


def _normalize_fmp_bars(raw: list) -> list:
    """Normaliza barras FMP -> [{'t','o','h','l','c','v'}] en orden ascendente."""
    out = []
    for r in (raw or []):
        try:
            t = _parse_fmp_datetime_to_ms(str(r.get("date")))
            o = float(r.get("open", 0))
            h = float(r.get("high", 0))
            l = float(r.get("low",  0))
            c = float(r.get("close",0))
            v = float(r.get("volume",0))
            # Ajuste de envolvente por robustez
            h = max(h, o, c); l = min(l, o, c)
            out.append({"t": t, "o": o, "h": h, "l": l, "c": c, "v": v})
        except Exception:
            continue
    # FMP suele venir en orden descendente; lo dejamos ascendente
    out.sort(key=lambda x: x["t"])
    return out

# ---------- paths ----------
def _gcs_enriched_path(exec_id: str, symbol: str, tf: str) -> str:
    # gs://markettool_bucket/analisis/exec/{exec_id}/{SYMBOL}_{TF}_enriched.json
    return f"analisis/exec/{exec_id}/{symbol}_{tf}_enriched.json"

def _gcs_stream_path(exec_id: str, symbol: str, tf: str) -> str:
    return f"analisis/stream/{exec_id}/{symbol}_{tf}.json"

# ---------- IO ----------
def _gcs_exists(path: str) -> bool:
    return gcs_blob_exists(BUCKET_NAME, path)  # usa tu helper real

def _gcs_read_json(path: str) -> dict:
    return read_json_from_gcs(BUCKET_NAME, path)  # usa tu helper real

def _gcs_write_json(path: str, obj: dict):
    write_json_to_gcs(BUCKET_NAME, path, obj)  # usa tu helper real


def _ensure_stream_initialized(exec_id: str, symbol: str, tf: str, st: dict):
    """
    Si NO existe stream, intenta copiar enriched -> stream UNA sola vez.
    'st' es el state in-memory (series ya cargada); si viene vacío, lee enriched y lo copia.
    """
    stream_path = _gcs_stream_path(exec_id, symbol, tf)
    if _gcs_exists(stream_path):
        return  # ya existe

    enriched_path = _gcs_enriched_path(exec_id, symbol, tf)
    if not _gcs_exists(enriched_path):
        # no hay enriched, no podemos inicializar (lo dejará vacío)
        return

    data = st if st and isinstance(st, dict) else {"series": []}
    series = data.get("series") if isinstance(data.get("series"), list) else []
    _gcs_write_json(stream_path, series)  # << stream guarda SOLO la lista


def _tf_ms(tf: str) -> int:
    return {
        "1min": 60_000, "5min": 5*60_000, "15min": 15*60_000,
        "30min": 30*60_000, "1hour": 60*60_000, "4hour": 4*60*60_000
    }.get(tf, 60_000)

def _bucket_ts(ts_ms: int, tf_ms: int) -> int:
    return (ts_ms // tf_ms) * tf_ms

def merge_bars_series(series: list, incoming: list, tf: str) -> int:
    """Mergea por bucket en toda la serie y retorna cuántos BUCKETS NUEVOS se agregaron."""
    if not incoming:
        return 0

    tfms = _tf_ms(tf)

    def _norm(c):
        b = _bucket_ts(int(c["t"]), tfms)
        o = float(c["o"]); h = float(c["h"]); l = float(c["l"]); c_ = float(c["c"]); v = float(c.get("v", 0))
        # Envolvente básica por si llega OHLC “degenerado”
        h = max(h, o, c_)
        l = min(l, o, c_)
        return {"t": b, "o": o, "h": h, "l": l, "c": c_, "v": v}

    # --- claves (buckets) que ya existen ANTES del merge
    old_keys = set()
    by_bucket = {}

    for c in series or []:
        nc = _norm(c)
        t  = nc["t"]
        old_keys.add(t)
        by_bucket[t] = _prefer(by_bucket[t], nc) if t in by_bucket else nc

    # merge del incoming
    for b in incoming:
        try:
            nb = _norm(b)
        except Exception:
            continue
        t = nb["t"]
        by_bucket[t] = _prefer(by_bucket[t], nb) if t in by_bucket else nb

    # reconstruye lista ordenada
    new_series = list(by_bucket.values())
    new_series.sort(key=lambda x: x["t"])

    # --- cuántos buckets NUEVOS aparecen tras el merge
    new_keys = set(x["t"] for x in new_series)
    added_count = len(new_keys - old_keys)

    # escribe in-place y regresa conteo real
    series[:] = new_series
    return added_count


def _fmp_interval(tf: str) -> str:
    return {"1min":"1min","5min":"5min","15min":"15min","30min":"30min","1hour":"1hour","4hour":"4hour"}.get(tf,"1min")

def _fetch_quote(symbol: str) -> Optional[float]:
    """Intenta stable/quote (forex), fallback a v3/quote. Devuelve last price."""
    if not API_KEY:
        return None
    try:
        # 1) estable/realtime (forex)
        url1 = f"https://financialmodelingprep.com/stable/quote?symbol={symbol}&apikey={API_KEY}"
        logging.info(f"MTORO1 URL: {url1}")
        r = requests.get(url1, timeout=8)
        if r.ok:
            arr = r.json() or []
            if isinstance(arr, list) and arr:
                p = arr[0].get("price") or arr[0].get("last") or arr[0].get("bid") or arr[0].get("ask")
                if p: return float(p)
        # 2) fallback v3
        url2 = f"https://financialmodelingprep.com/api/v3/quote/{symbol}?apikey={API_KEY}"
        logging.info(f"MTORO2 URL: {url2}")
        r = requests.get(url2, timeout=8)
        if r.ok:
            arr = r.json() or []
            if isinstance(arr, list) and arr:
                p = arr[0].get("price") or arr[0].get("previousClose") or arr[0].get("dayHigh")
                if p: return float(p)
    except Exception:
        pass
    return None

def _fetch_historical(symbol: str, tf: str) -> list[dict]:
    """Barras recientes para sellar/ajustar (toma las MÁS NUEVAS correctamente)."""
    if not API_KEY:
        return []
    try:
        iv = _fmp_interval(tf)
        url = f"https://financialmodelingprep.com/api/v3/historical-chart/{iv}/{symbol}?apikey={API_KEY}"
        logging.info(f"MTORO3 URL: {url}")
        r = requests.get(url, timeout=5)
        if not r.ok:
            return []
        arr = r.json() or []
        # FMP devuelve MÁS RECIENTE primero → tomar las N MÁS NUEVAS con [:N]
        arr = arr[:120]  # <-- no usar [-120:]
        out = []
        for x in reversed(arr):  # y ahora sí a ascendente (viejo → nuevo)
            try:
                dt = x.get("date") or x.get("timestamp")
                if isinstance(dt, str):
                    # dt viene como 'YYYY-MM-DD HH:MM:SS' (UTC) → hazlo UTC explícito
                    ts = _parse_fmp_datetime_to_ms(dt) 
                else:
                    ts = int(float(dt) * 1000)
                out.append({
                    "t": ts,
                    "o": float(x["open"]),
                    "h": float(x["high"]),
                    "l": float(x["low"]),
                    "c": float(x["close"]),
                    "v": float(x.get("volume", 0))
                })
            except Exception:
                continue
        return out
    except Exception:
        return []


# Cache muy simple para histórico FMP por (symbol, timeframe) para no pegarle en cada incremental
_HIST_CACHE: Dict[Tuple[str, str], dict] = {}
_HIST_CACHE_TTL: Dict[str, int] = {
    "1min":  20,   # 1m: como mucho 1 llamada nueva cada ~20s
    "5min":  60,
    "15min": 120,
    "30min": 180,
    "1hour": 300,
    "4hour": 600,
}

def _fetch_historical_cached(symbol: str, tf: str) -> list[dict]:
    """Envuelve _fetch_historical con un TTL simple por símbolo/TF."""
    tf_norm = _norm_tf(tf)
    key = (symbol.upper(), tf_norm)
    now = time.time()
    ttl = _HIST_CACHE_TTL.get(tf_norm, 120)

    cached = _HIST_CACHE.get(key)
    if cached and (now - cached.get("ts", 0)) < ttl:
        return cached.get("data", [])

    data = _fetch_historical(symbol, tf_norm)
    _HIST_CACHE[key] = {"ts": now, "data": data}
    return data


def _maybe_tick_quote(exec_id: str, symbol: str, tf: str, st: dict) -> bool:
    key = (exec_id, symbol, tf)
    now = time.time()
    ttl = QUOTE_TTL.get(tf, 3)
    last = _LAST_QUOTE_TICK.get(key, 0)
    if now - last < ttl:
        return False

    price = _fetch_quote(symbol)
    _LAST_QUOTE_TICK[key] = now
    if price is None:
        return False

    series = st.get("series") or []
    t_ms = int(now * 1000)
    tfms = _tf_ms(tf)
    new_bucket = _bucket_ts(t_ms, tfms)
    last_bucket = _bucket_ts(series[-1]["t"], tfms) if series else None

    # Si abre bucket nuevo → o = price (primer precio del bucket).
    # Si es mismo bucket → el open no importa (no se usa en el merge), igual lo pasamos como el open vigente.
    if series and new_bucket == last_bucket:
        o_open = float(series[-1].get("o", price))
    else:
        o_open = float(price)

    incoming = [{
        "t": t_ms,
        "o": o_open,
        "h": float(price),
        "l": float(price),
        "c": float(price),
        "v": 0.0,
    }]

    changed = merge_bars_series(series, incoming, tf)
    st["series"] = series
    return changed > 0


# ---- Exists ----
def gcs_blob_exists(bucket_name: str, path: str) -> bool:
    """
    Verifica si existe un blob en GCS.
    """
    try:
        client = storage.Client()
        bucket = client.bucket(bucket_name)
        blob = bucket.blob(path)
        return blob.exists()
    except Exception:
        return False


# ---- Read JSON ----
def read_json_from_gcs(bucket_name: str, path: str) -> Any:
    """
    Lee un JSON (UTF-8) desde GCS y lo parsea.
    Retorna list/dict o {} si falla.
    """
    try:
        client = storage.Client()
        bucket = client.bucket(bucket_name)
        blob = bucket.blob(path)
        if not blob.exists():
            return {}
        data = blob.download_as_bytes()
        # Si algún día subes gz, aquí podrías detectar y descomprimir
        return json.loads(data.decode("utf-8"))
    except Exception:
        return {}


# ---- Write JSON ----
def write_json_to_gcs(bucket_name: str, path: str, obj: Any, content_type: str = "application/json") -> Optional[str]:
    """
    Escribe un objeto como JSON (UTF-8) en GCS.
    Devuelve la generación (o None) si algo falla.
    """
    try:
        payload = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        client =  storage.Client()
        bucket = client.bucket(bucket_name)
        blob = bucket.blob(path)
        blob.upload_from_string(payload, content_type=content_type)
        # Devuelve la generación para control de cambios
        blob.reload()  # actualiza metadata local
        return str(blob.generation) if getattr(blob, "generation", None) else None
    except Exception:
        return None

def _now_ms() -> int:
    return int(time.time() * 1000)

def _tfms(tf: str) -> int:
    return _tf_ms(tf)

def _bucket_start(ts_ms: int, tfms: int) -> int:
    return (ts_ms // tfms) * tfms

def _current_bucket_start(tfms: int) -> int:
    return _bucket_start(_now_ms(), tfms)

def _prefer(a: dict, b: dict) -> dict:
    # Caso 1: histórico vs tick
    if (a.get("v", 0) > 0) and (b.get("v", 0) == 0):
        return a
    if (b.get("v", 0) > 0) and (a.get("v", 0) == 0):
        return b

    # Caso 2: ambos del mismo “tipo” (ambos v=0 o ambos v>0)
    o = a.get("o", b.get("o"))
    h = max(float(a.get("h", o)), float(b.get("h", o)), float(o))
    l = min(float(a.get("l", o)), float(b.get("l", o)), float(o))
    c = float(b.get("c", a.get("c", o)))  # último close prevalece

    # ⚠️ clave: NO sumar si ambos tienen v>0 → evita inflar volumen
    va = float(a.get("v", 0) or 0)
    vb = float(b.get("v", 0) or 0)
    if va > 0 and vb > 0:
        v = max(va, vb)   # o v = vb (si quieres “el más reciente”)
    else:
        v = va + vb       # tick + histórico o tick + tick

    return {"t": a["t"], "o": float(o), "h": h, "l": l, "c": c, "v": v}


def _snap_and_dedupe_to_minutes(series: list[dict], tf: str) -> list[dict]:
    """
    - 'Snapea' todo t al inicio de bucket.
    - Dedup por bucket conservando la mejor vela (prefer histórico con v>0).
    - Ordena ascendente.
    """
    if not series: return []
    tfms = _tfms(tf)
    by_bucket = {}
    for c in series:
        t = _bucket_start(int(c["t"]), tfms)
        norm = {"t": t, "o": float(c["o"]), "h": float(c["h"]), "l": float(c["l"]), "c": float(c["c"]), "v": float(c.get("v",0))}
        if t in by_bucket:
            by_bucket[t] = _prefer(by_bucket[t], norm)
        else:
            by_bucket[t] = norm
    out = list(by_bucket.values())
    out.sort(key=lambda x: x["t"])
    return out

def _densify_minutes(series: list[dict], tf: str, max_fill:int = 10, max_gap_minutes:int = 10) -> list[dict]:
    """
    Rellena SOLO huecos pequeños (<= max_gap_minutes) y como mucho max_fill velas sintéticas.
    No toca el bucket en curso. Asume series ordenada y bucketizada.
    """
    if not series:
        return series

    tfms = _tf_ms(tf)
    cur_bucket = _current_bucket_start(tfms)
    out = [series[0]]
    filled = 0

    for i in range(1, len(series)):
        prev = out[-1]
        cur  = series[i]
        expected = prev["t"] + tfms

        # si el hueco es grande, lo dejamos para historical
        gap_minutes = (cur["t"] - prev["t"]) // tfms
        
        if gap_minutes > max_gap_minutes:
            out.append(cur)
            continue

        while expected < cur["t"] and expected < cur_bucket and filled < max_fill:
            cprev = float(prev["c"])
            out.append({"t": expected, "o": cprev, "h": cprev, "l": cprev, "c": cprev, "v": 0.0})
            expected += tfms
            prev = out[-1]
            filled += 1

        out.append(cur)

    return out

def _overlay_historical_for_closed(series: list[dict], hist: list[dict], tf: str) -> list[dict]:
    """
    Reemplaza (o crea) los buckets ya CERRADOS usando barras de historical (que suelen traer v>0).
    Mantiene la vela del bucket en curso tal como estaba (tick).
    """
    if not series: return series
    tfms = _tfms(tf)
    cur_bucket = _current_bucket_start(tfms)
    # index por bucket
    s_map = {c["t"]: dict(c) for c in series}
    for b in (hist or []):
        t = _bucket_start(int(b["t"]), tfms)
        if t >= cur_bucket:
            continue  # no sobreescribas el bucket vigente
        cand = {"t": t, "o": float(b["o"]), "h": float(b["h"]), "l": float(b["l"]), "c": float(b["c"]), "v": float(b.get("v",0))}
        if t in s_map:
            s_map[t] = _prefer(s_map[t], cand)
        else:
            s_map[t] = cand
    out = list(s_map.values())
    out.sort(key=lambda x: x["t"])
    return out

def _closed_signature(series: list[dict], tf: str) -> tuple:
    """
    Firma barata de 'buckets cerrados' para detectar cambios reales tras overlay/backfill.
    Devuelve (count_closed, last_closed_ts, checksum_int)
    """
    if not series:
        return (0, None, 0)
    tfms = _tf_ms(tf)
    cur_bucket = _current_closed_bucket_start(tf)
    count = 0
    last_t = None
    checksum = 0
    for c in series:
        t = int(c["t"])
        if t >= cur_bucket:
            break
        count += 1
        last_t = t
        # checksum liviano y determinista
        # redondeo para evitar ruido por floats
        o = int(round(float(c.get("o", 0))*1e5))
        h = int(round(float(c.get("h", 0))*1e5))
        l = int(round(float(c.get("l", 0))*1e5))
        v = int(round(float(c.get("v", 0))*1e2))
        checksum = (checksum * 1315423911 + t + o + h + l + v) & 0xFFFFFFFF
    return (count, last_t, checksum)


def _ms_to_iso(ms: int) -> str:
    return datetime.utcfromtimestamp(ms/1000.0).strftime("%Y-%m-%d %H:%M:%S")


def _current_closed_bucket_start(tf: str) -> int:
    tfms = _tf_ms(tf)
    now_bucket = (_now_ms() // tfms) * tfms
    return now_bucket  # este es el inicio del bucket en curso; 'cerrados' son < now_bucket


def _backfill_range_once(series: list[dict], symbol: str, tf: str, from_ms: int, to_ms: int) -> int:
    """
    Backfill de una sola llamada con rango exacto [from_ms, to_ms].
    - No depende de que 'series' ya tenga datos (puede sembrar).
    - No toca el bucket en curso (filtra por seguridad).
    """
    if to_ms <= from_ms:
        return 0

    rng = _fetch_historical_range(symbol, tf, from_ms, to_ms)
    if not rng:
        return 0

    # Blindaje opcional: evita afectar el bucket vigente
    cur_open = _current_closed_bucket_start(tf)  # inicio del bucket en curso
    rng = [b for b in rng if int(b["t"]) < cur_open]
    if not rng:
        return 0

    added = merge_bars_series(series, rng, tf)
    if added:
        series[:] = _snap_and_dedupe_to_minutes(series, tf)
    return added

def _ms_to_iso_utc(ts_ms: int) -> str:
    # "YYYY-MM-DD HH:MM:SS" en UTC (formato que acepta FMP en historical-chart)
    return datetime.utcfromtimestamp(ts_ms/1000).strftime("%Y-%m-%d %H:%M:%S")
def _fetch_historical_range(symbol: str, tf: str, from_ms: int, to_ms: int) -> list[dict]:
    if not API_KEY: return []
    try:
        iv = _fmp_interval(tf)
        if to_ms <= from_ms:
            return []
        url = f"https://financialmodelingprep.com/api/v3/historical-chart/{iv}/{symbol}"
        params = {
            "apikey": API_KEY,
            "from": _ms_to_fmp_local(from_ms),
            "to":   _ms_to_fmp_local(to_ms)
        }
        logging.info(f"MTORO10 url: {url} params: from={params['from']} to={params['to']}")
        r = requests.get(url, params=params, timeout=5)
        if not r.ok:
            logging.info(f"MTORO10 HTTP {r.status_code}: {r.text[:200]}")
            return []
        return _normalize_fmp_bars(r.json())
    except Exception as e:
        logging.warning(f"MTORO10 error: {e}")
        return []


def _fmp_hist_with_range(symbol: str, tf: str, from_ms: int, to_ms: int) -> list[dict]:
    if not API_KEY: 
        return []
    iv = _fmp_interval(tf)
    url = f"https://financialmodelingprep.com/api/v3/historical-chart/{iv}/{symbol}"
    logging.info(f"MTORO _fmp_hist_with_range URL: {url}")
    params = {"apikey": API_KEY, "from": _ms_to_fmp_local(from_ms), "to": _ms_to_fmp_local(to_ms)}
    try:
        r = requests.get(url, params=params, timeout=20)
        if not r.ok:
            return []
        return _normalize_fmp_bars(r.json())  # ya devuelve ascendente
    except Exception:
        return []


def _detect_closed_gaps(series: list[dict], tf: str) -> list[tuple[int, int]]:
    """
    Lista los huecos entre buckets YA CERRADOS: [(from_ms, to_ms)].
    La serie debe venir snap/dedup y ordenada.
    """
    if not series:
        return []
    tfms = _tf_ms(tf)
    cur_bucket = _current_bucket_start(tfms)
    gaps = []
    for i in range(1, len(series)):
        a = series[i-1]["t"]
        b = series[i]["t"]
        if b >= cur_bucket:
            break  # no miramos gaps que toquen el bucket en curso
        delta = (b - a) // tfms
        if delta > 1:
            from_ms = a + tfms
            to_ms   = b
            gaps.append((from_ms, to_ms))
    return gaps


def _backfill_internal_gaps(
    base_ms: list[dict],
    symbol: str,
    tf: str,
    exec_id: str | None = None,
    max_minutes_per_call: int = 10_000,
) -> int:
    if tf in ("1min", "1m", "5min", "5m"):
        return 0
    if not base_ms:
        return 0
    tfms = _tf_ms(tf)
    closed_end = _current_closed_bucket_start(tf) - tfms
    total_added = 0

    cooldown = _INTERNAL_GAP_COOLDOWN_S.get(tf, 300)
    now_s = time.time()

    start = now_s

    i = 0
    while i < len(base_ms) - 1:

        if time.time() - start > 5:  # o 8, como prefieras
            logging.info(
                "BACKFILL_INTERNAL_GAPS time budget exceeded (%.3fs), "
                "symbol=%s tf=%s total_added=%d; saliendo",
                time.time() - start, symbol, tf, total_added
            )
            break

        # antes de seguir escaneando gaps, revisamos Firestore
        if exec_id and not _tf_is_enabled(exec_id, symbol, tf):
            logging.info(
                "BACKFILL_INTERNAL_GAPS abortado: TF deshabilitada en Firestore "
                "exec=%s symbol=%s tf=%s",
                exec_id, symbol, tf,
            )
            break

        a = base_ms[i]
        b = base_ms[i + 1]
        gap_buckets = (b["t"] - a["t"]) // tfms - 1
        if gap_buckets > 0:
            from_ms = a["t"] + tfms
            to_ms   = min(b["t"] - tfms, closed_end)
            if to_ms >= from_ms:
                cap_to_ms = min(from_ms + max_minutes_per_call * tfms, to_ms)

                key = (symbol, tf, from_ms, cap_to_ms)
                last_try = _LAST_INTERNAL_GAP_ATTEMPT.get(key, 0)
                if (now_s - last_try) < cooldown:
                    # evita reintentar inmediatamente el mismo hueco
                    i += 1
                    continue

                _LAST_INTERNAL_GAP_ATTEMPT[key] = now_s
                logging.info(f"GAPFILL {symbol} {tf} from={_ms_to_iso_utc(from_ms)} to={_ms_to_iso_utc(cap_to_ms)} gap_bars={gap_buckets} (INT)")
                rng = _fetch_historical_range(symbol, tf, from_ms, cap_to_ms)

                # después (muestra UTC y lo que se envía a FMP en ET)
                et_from = _ms_to_fmp_local(from_ms)     # UTC -> America/New_York "YYYY-MM-DD HH:MM:SS"
                et_to   = _ms_to_fmp_local(cap_to_ms)

                logging.info(
                    "GAPFILL %s %s UTC:[%s → %s] ET:[%s → %s] gap_bars=%d (INT)",
                    symbol, tf, _ms_to_iso_utc(from_ms), _ms_to_iso_utc(cap_to_ms),
                    et_from, et_to, gap_buckets
                )

                added = 0
                if rng:
                    added = merge_bars_series(base_ms, rng, tf)
                    if added:
                        base_ms[:] = _snap_and_dedupe_to_minutes(base_ms, tf)
                        total_added += added
                        # retrocede para revalidar alrededor del hueco rellenado
                        i = max(i - 1, 0)
                        continue

                # Si no agregó nada, no sigas martillando: el cooldown ya quedó marcado
        i += 1

    return total_added


def _parse_fmp_datetime_to_ms(s: str, src_tz: str = FMP_INTRADAY_SOURCE_TZ) -> int:
    """
    Convierte 'YYYY-MM-DD HH:MM:SS' de FMP (ET) a epoch ms UTC.
    """
    s2 = s.replace("T", " ").replace("Z", "")  # FMP no trae Z, pero por si acaso
    dt_local = datetime.strptime(s2, "%Y-%m-%d %H:%M:%S").replace(tzinfo=ZoneInfo(src_tz))
    return int(dt_local.astimezone(timezone.utc).timestamp() * 1000)

def _ms_to_fmp_local(ms: int, dst_tz: str = FMP_INTRADAY_SOURCE_TZ) -> str:
    """
    Convierte epoch ms UTC a string 'YYYY-MM-DD HH:MM:SS' en ET para pasar a FMP.
    """
    dt_utc = datetime.fromtimestamp(ms/1000, tz=timezone.utc)
    dt_loc = dt_utc.astimezone(ZoneInfo(dst_tz))
    return dt_loc.strftime("%Y-%m-%d %H:%M:%S")


# ==========================
# Helpers de tiempo y hash
# ==========================

def _now_utc() -> datetime:
    return datetime.now(timezone.utc)

def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

def _hash_payload(rows: list) -> str:
    """ Hash FNV-1a simple para snapshots; evita renders innecesarios """
    acc = 2166136261
    for r in rows:
        s = json.dumps(r, sort_keys=True, ensure_ascii=False, default=str)
        for ch in s:
            acc ^= ord(ch)
            acc = (acc * 16777619) & 0xFFFFFFFF
    return f"{acc:08x}"

def _numeric_or_nan(x):
    try:
        return float(x)
    except Exception:
        return math.nan

# ==========================
# Forex helper
# ==========================

def obtener_monedas(symbol: str) -> Tuple[str,str]:
    # Forex: EURUSD -> (EUR, USD). Metales/commodities: XAUUSD → (XAU, USD). Intenta fallbacks
    s = (symbol or "").upper()
    if len(s) >= 6:
        return s[:3], s[3:6]
    # fallback genérico
    return s, "USD"

# ==========================
# Memo y dedupe
# ==========================

MIN_FETCH_INTERVAL_S = 5  # memo 5 segundos por símbolo
_EVENTS_MEMO: Dict[str, Dict[str, Any]] = {}  # {symbol: {"df":DataFrame,"ts":epoch}}
_LAST_ACTUAL: Dict[Tuple[str, Tuple[str,str,int]], float] = {}  # (symbol,(cur,event,ms)) -> actual
_LAST_HASH: Dict[Tuple[str,str], str] = {}  # (exec_id,symbol) -> hash

def _event_row_key(row: pd.Series) -> Tuple[str,str,int]:
    """ Llave estable por fila de evento (currency, event, date_ms UTC). """
    currency = str(row.get("currency") or "")
    event    = str(row.get("event") or "")
    date     = pd.to_datetime(row.get("date"), errors="coerce", utc=True)
    ms = int(date.value//1_000_000) if not pd.isna(date) else 0
    return (currency, event, ms)

TZ_NY = ZoneInfo("America/New_York")
def _trading_now_utc() -> datetime:
    """
    'Ahora' pensado en términos de día bursátil NY.
    Si en NY son las 17:00 o más, considera el día de trading como mañana.
    Devuelve un datetime en UTC.
    """
    raw_now_utc = _now_utc()
    if raw_now_utc.tzinfo is None:
        raw_now_utc = raw_now_utc.replace(tzinfo=timezone.utc)

    now_ny = raw_now_utc.astimezone(TZ_NY)

    # Opción B: después de las 17:00 NY, usamos "mañana"
    if now_ny.hour >= 17:
        trading_ny = now_ny + timedelta(days=1)
    else:
        trading_ny = now_ny

    trading_utc = trading_ny.astimezone(timezone.utc)

    logger.info(
        "[eventos] _trading_now_utc raw_now_utc=%s now_ny=%s trading_ny=%s trading_utc=%s",
        raw_now_utc.isoformat(),
        now_ny.isoformat(),
        trading_ny.isoformat(),
        trading_utc.isoformat(),
    )

    return trading_utc

# ==========================
# Fetch de eventos (API o Firestore)
# ==========================

def _fetch_events_for(symbol: str, hours_back: int = 6, minutes_fwd: int = 5) -> pd.DataFrame:
    """
    Obtiene eventos en ventana [now - hours_back, now + minutes_fwd] normalizados.
    Aplica memoización de 5s por símbolo para soportar polling de 1s.
    """

    t0 = time.time()

    # 🔹 aquí usamos el "now bursátil" (Opción B)
    now = _trading_now_utc()
    a = now - timedelta(hours=int(hours_back))
    b = now + timedelta(minutes=int(minutes_fwd))

    memo = _EVENTS_MEMO.get(symbol)
    if memo and (time.time() - memo.get("ts", 0) < MIN_FETCH_INTERVAL_S):
        df = memo["df"].copy()
    else:
        df = obtener_eventos_guardados_o_futuros(_iso(a), _iso(b))
        if df is None or df.empty:
            df = pd.DataFrame(columns=["date","currency","event","actual","estimate","previous","impact","date_country"])
        # normaliza tipos
        df["date"] = pd.to_datetime(df["date"], errors="coerce", utc=True)
        for c in ["actual","estimate","previous"]:
            df[c] = pd.to_numeric(df[c], errors="coerce")
        df["impact"] = df["impact"].astype(str).str.capitalize()
        df = df.sort_values("date", ascending=True).reset_index(drop=True)
        _EVENTS_MEMO[symbol] = {"df": df.copy(), "ts": time.time()}

        logger.info("[eventos] _fetch_events_for %s tardó %.3fs", symbol, time.time() - t0)

    return df

def _detect_new_results(symbol: str, df: pd.DataFrame) -> List[dict]:
    """
    Devuelve filas donde 'actual' apareció/cambió respecto de lo último visto.
    """
    new_rows = []
    for _, row in df.iterrows():
        k = (symbol, _event_row_key(row))
        actual = _numeric_or_nan(row.get("actual"))
        if math.isnan(actual):
            continue
        prev = _LAST_ACTUAL.get(k, math.nan)
        if math.isnan(prev) or not math.isclose(prev, actual, rel_tol=0, abs_tol=1e-12):
            _LAST_ACTUAL[k] = actual
            new_rows.append({
                "date": row["date"].isoformat(),
                "currency": row.get("currency"),
                "event": row.get("event"),
                "impact": row.get("impact"),
                "actual": actual,
                "estimate": _numeric_or_nan(row.get("estimate")),
                "previous": _numeric_or_nan(row.get("previous")),
            })
    return new_rows

# ==========================
# Scoring y dirección por evento
# ==========================

# pesos por impacto (puedes ajustar por config)
IMPACT_WEIGHTS = {
    "high": 1.0,
    "medium": 0.6,
    "low": 0.3,
}

# pesos por categoría (good/bad) base
CATEGORY_WEIGHTS = {
    "unemployment": {"good": +0.35, "bad": -0.35},
    "employment":   {"good": +0.35, "bad": -0.35},
    "inflation":    {"good": +0.30, "bad": -0.30},
    "gdp":          {"good": +0.25, "bad": -0.25},
    "retail":       {"good": +0.20, "bad": -0.20},
    "rates":        {"good": +0.25, "bad": -0.25},
    "generic": {
        "better_both": +0.25,
        "better_estimate": +0.18,
        "better_prev": +0.12,
        "worse": -0.18,
    },
    # commodities especiales:
    "crude_oil_inventories": {"draw_good": +0.30, "build_bad": -0.30},
}

def detectar_categoria(event_name: Any) -> str:
    if not event_name:
        return "Generic"
    s = str(event_name).lower()
    if "unemployment" in s or "jobless" in s:
        return "Unemployment Rate"
    if "nonfarm" in s or "employment" in s or "payrolls" in s or "jobs" in s:
        return "Employment Report"
    if "inflation" in s or "cpi" in s or "consumer price" in s or "ppi" in s:
        return "Inflation Rate"
    if "gdp" in s or "gross domestic" in s:
        return "GDP"
    if "retail" in s or "sales" in s:
        return "Retail Sales"
    if "rate decision" in s or "interest rate" in s or "policy rate" in s or "refi rate" in s or "overnight rate" in s:
        return "Interest Rate"
    if "crude" in s and "inventory" in s:  # EIA
        return "Crude Oil Inventories"
    return "Generic"

def _cap(x: float, limit: float) -> float:
    return max(min(x, limit), -limit)

def evaluar_evento_para_symbol(
    symbol: str,
    ev: Dict[str, Any],
    *,
    consider_hours: int = 6,
    recent_minutes_boost: int = 30,
    recent_boost: float = 1.15,
    decay_floor: float = 0.6,
    per_event_cap: float = 0.35,
    flip_secondary_currency: bool = True
) -> Dict[str, Any]:
    """
    Devuelve dict con:
     - score: float ([-cap, +cap])
     - direction: 'bullish'|'bearish'|'neutral'
     - reason: texto breve
    """
    now = _now_utc()
    base, quote = obtener_monedas(symbol)

    try:
        dt = pd.to_datetime(ev.get("date"), errors="coerce", utc=True)
    except Exception:
        dt = pd.NaT

    actual   = _numeric_or_nan(ev.get("actual"))
    estimate = _numeric_or_nan(ev.get("estimate"))
    previous = _numeric_or_nan(ev.get("previous"))
    impact   = str(ev.get("impact") or "").strip().lower()
    iw       = IMPACT_WEIGHTS.get(impact, 0.4)

    # ventana temporal
    if not pd.isna(dt):
        if consider_hours is not None:
            cutoff = now - timedelta(hours=int(consider_hours))
            if dt < cutoff:
                return {"score": 0.0, "direction": "neutral", "reason": "out_of_window"}
        # recency
        recent_mult = 1.0
        if (now - dt).total_seconds() / 60.0 < float(recent_minutes_boost):
            recent_mult *= float(recent_boost)
    else:
        recent_mult = 1.0

    # decaimiento relativo a la “edad” vs la primera fecha de df (aquí aproximamos 6h)
    tmax = float(max(1.0, consider_hours * 3600))
    age = float((now - dt).total_seconds()) if not pd.isna(dt) else tmax
    decay = max(1.0 - (age / tmax), float(decay_floor))

    # signo por moneda (si el dato es de la cotizada, invierte)
    mult_sign = -1.0 if (flip_secondary_currency and str(ev.get("currency","")).upper() == str(quote).upper()) else 1.0

    cat = detectar_categoria(ev.get("event"))
    cw  = CATEGORY_WEIGHTS

    # regla base: si faltan números, no puntúa
    if math.isnan(actual) or math.isnan(previous):
        return {"score": 0.0, "direction": "neutral", "reason": "missing_values"}

    # lógica por categoría
    adj = 0.0
    if cat == "Unemployment Rate":
        good, bad = cw["unemployment"]["good"], cw["unemployment"]["bad"]
        if math.isnan(estimate):
            adj = good if actual < previous else bad
        else:
            if actual < estimate and actual < previous:
                adj = good
            elif actual < estimate:
                adj = +0.20
            else:
                adj = bad

    elif cat == "Employment Report":
        good, bad = cw["employment"]["good"], cw["employment"]["bad"]
        if math.isnan(estimate):
            adj = good if actual > previous else bad
        else:
            if actual > estimate and actual > previous:
                adj = good
            elif actual > estimate:
                adj = +0.20
            else:
                adj = bad

    elif cat == "Inflation Rate":
        good, bad = cw["inflation"]["good"], cw["inflation"]["bad"]
        if math.isnan(estimate):
            adj = good if actual < previous else bad
        else:
            if actual < estimate and actual < previous:
                adj = good
            elif actual < estimate:
                adj = +0.20
            else:
                adj = bad

    elif cat == "GDP":
        good, bad = cw["gdp"]["good"], cw["gdp"]["bad"]
        if math.isnan(estimate):
            adj = good if actual > previous else bad
        else:
            if actual > estimate and actual > previous:
                adj = good
            elif actual > estimate:
                adj = +0.20
            else:
                adj = -0.20

    elif cat == "Retail Sales":
        good, bad = cw["retail"]["good"], cw["retail"]["bad"]
        if math.isnan(estimate):
            adj = good if actual > previous else bad
        else:
            if actual > estimate and actual > previous:
                adj = good
            elif actual > estimate:
                adj = +0.20
            else:
                adj = bad

    elif cat == "Interest Rate":
        # Para tasas: menor que est/prv = bueno (apoya crecimiento/bolsa),
        # mayor = malo (aprecia divisa pero enfría activos de riesgo).
        good, bad = cw["rates"]["good"], cw["rates"]["bad"]
        if math.isnan(estimate):
            adj = good if actual < previous else bad
        else:
            if actual < estimate and actual < previous:
                adj = good
            elif actual < estimate:
                adj = +0.20
            else:
                adj = bad

    elif cat == "Crude Oil Inventories":
        # draw (actual < 0 o menor a est/prv) = bullish WTI/Brent
        inv = cw["crude_oil_inventories"]
        if math.isnan(estimate):
            adj = inv["draw_good"] if actual < previous else inv["build_bad"]
        else:
            if actual < estimate and actual < previous:
                adj = inv["draw_good"]
            elif actual < estimate:
                adj = +0.20
            else:
                adj = inv["build_bad"]

    else:
        gg = cw["generic"]
        if math.isnan(estimate):
            adj = gg["better_prev"] if actual > previous else gg["worse"]
        else:
            if actual > estimate and actual > previous:
                adj = gg["better_both"]
            elif actual > estimate:
                adj = gg["better_estimate"]
            elif actual > previous:
                adj = gg["better_prev"]
            else:
                adj = gg["worse"]

    # componer y capear
    score = adj * iw * recent_mult * decay * mult_sign
    score = _cap(score, per_event_cap)

    direction = "bullish" if score > 0.01 else ("bearish" if score < -0.01 else "neutral")
    reason = f"{cat} | impact={impact} | adj={adj:.2f} iw={iw:.2f} rec={recent_mult:.2f} dec={decay:.2f} sign={mult_sign:+.0f}"
    return {"score": float(score), "direction": direction, "reason": reason}


_SYMBOL_SPECIAL_CURRENCIES = {
    "DX-Y.NYB": {"USD"},  # US Dollar Index (FMP)
    "USDX": {"USD"},
    "DXY": {"USD"},
    # agrega tus índices/commodities aquí si quieres forzar moneda
}

def _split_base_quote(symbol: str) -> tuple[str | None, str | None]:
    """
    FOREX clásico: 6 letras A-Z, p.ej. EURUSD, GBPJPY.
    Devuelve (base, quote) o (None, None) si no es FOREX 3+3.
    """
    sym = (symbol or "").upper()
    m = re.fullmatch(r"([A-Z]{3})([A-Z]{3})", sym)
    if m:
        return m.group(1), m.group(2)
    return None, None

def _filter_by_symbol_currencies(df: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """
    Mantiene eventos cuya 'currency' coincide con la base o la secundaria del símbolo (FOREX).
    Para símbolos no-FOREX, usa mapping especial si existe; si no, NO filtra (deja High/Medium de la ventana).
    """
    if df is None or df.empty:
        return df

    symU = str(symbol or "").upper()

    # Casos especiales (índices, etc.)
    if symU in _SYMBOL_SPECIAL_CURRENCIES:
        wanted = {c.upper() for c in _SYMBOL_SPECIAL_CURRENCIES[symU]}
        return df[df["currency"].astype(str).str.upper().isin(wanted)].copy()

    # FOREX puro 3+3
    base, quote = _split_base_quote(symU)
    if base and quote:
        wanted = {base, quote}
        return df[df["currency"].astype(str).str.upper().isin(wanted)].copy()

    # No-FOREX y sin mapping → no filtres
    return df.copy()


# ==========================
# ENDPOINT
# ==========================

@webhook_app.errorhandler(404)
def _not_found(e):
    return jsonify({"status":"error","message":"not found"}), 404

@webhook_app.errorhandler(500)
def _server_err(e):
    return jsonify({"status":"error","message":"internal error"}), 500

@webhook_app.route("/monitoreo/eventos", methods=["POST"])
def monitoreo_eventos():
    """
    POST /monitoreo/eventos
    Body:
      {
        "user_id": "...",
        "exec_id": "...",
        "symbol": "EURUSD",
        "hours_back": 6,        # opcional (default 6)
        "minutes_fwd": 5,       # opcional (default 5)
        "cursor_hash": "..."    # opcional: hash del último snapshot recibido por el front
      }

    Respuesta:
      {
        "status": "ok",
        "exec_id": "...",
        "symbol": "EURUSD",
        "server_time": ms_utc,
        "hash": "abcd1234",
        "count": N,
        "new_results": [...],   # filas nuevas con 'actual' presente/cambiado
        "events": [...],        # snapshot High/Medium para base/quote del símbolo
        "signals": [...],       # señales con score/direction por cada fila con actual
        "agg_score": float,     # agregado reciente (suma cap)
        "agg_direction": "bullish"|"bearish"|"neutral"
      }
    """
    try:
        body = request.get_json(force=True) or {}
        user_id  = str(body.get("user_id") or "").strip()
        exec_id  = str(body.get("exec_id") or "").strip()
        symbol   = str(body.get("symbol") or "").strip().upper()
        hours_back = int(body.get("hours_back", 6))
        minutes_fwd = int(body.get("minutes_fwd", 5))
        cursor_hash = str(body.get("cursor_hash") or "").strip()

        if not user_id or not exec_id or not symbol:
            return jsonify({"status":"error","message":"user_id, exec_id y symbol son obligatorios"}), 400

        logger.info(f"Llamando _fetch_events_for({symbol}, hb={hours_back}, mf={minutes_fwd})")
        df = _fetch_events_for(symbol, hours_back=hours_back, minutes_fwd=minutes_fwd)
        logger.info("_fetch_events_for terminó")
        
        if df.empty:
            out = {"status":"ok","exec_id":exec_id,"symbol":symbol,"server_time":int(time.time()*1000),"hash":"0"*8,"count":0,"new_results":[],"events":[]}
            return jsonify(out), 200

        # impacto alto/medio y filtro por monedas del símbolo
        df = df[df["impact"].isin(["High","Medium"])].copy()
        df = _filter_by_symbol_currencies(df, symbol)

        # snapshot compacto
        events = []
        for _, row in df.iterrows():
            events.append({
                "date": (row["date"].isoformat() if pd.notna(row["date"]) else None),
                "currency": row.get("currency"),
                "event": row.get("event"),
                "impact": row.get("impact"),
                "actual": (float(row.get("actual")) if pd.notna(row.get("actual")) else None),
                "estimate": (float(row.get("estimate")) if pd.notna(row.get("estimate")) else None),
                "previous": (float(row.get("previous")) if pd.notna(row.get("previous")) else None),
            })

        h = _hash_payload(events)
        key = (exec_id, symbol)
        _LAST_HASH[key] = h

        # cambios "actual" desde la última vez
        new_results = _detect_new_results(symbol, df)

        # señales por fila (cuando actual existe)
        signals = []
        agg = 0.0
        for _, row in df.iterrows():
            if pd.notna(row.get("actual")):
                sig = evaluar_evento_para_symbol(symbol, {
                    "date": row["date"],
                    "currency": row.get("currency"),
                    "event": row.get("event"),
                    "impact": row.get("impact"),
                    "actual": row.get("actual"),
                    "estimate": row.get("estimate"),
                    "previous": row.get("previous"),
                })
                sig_out = {
                    "date": (row["date"].isoformat() if pd.notna(row["date"]) else None),
                    "currency": row.get("currency"),
                    "event": row.get("event"),
                    "impact": row.get("impact"),
                    "score": sig["score"],
                    "direction": sig["direction"],
                    "reason": sig["reason"],
                }
                signals.append(sig_out)
                agg += float(sig["score"])

        agg_direction = "bullish" if agg > 0.02 else ("bearish" if agg < -0.02 else "neutral")

        # heartbeat opcional
        try:
            if db is not None:
                doc_id = f"{exec_id}__{symbol}"
                db.collection("monitoreos").document(doc_id).set({
                    "eventos_hash": h,
                    "eventos_count": len(events),
                    "eventos_updated_at": int(time.time()*1000),
                    "eventos_agg_score": float(agg),
                    "eventos_agg_direction": agg_direction,
                }, merge=True)
        except Exception:
            pass

        # Si no hay cambios y el front ya tiene el hash → responde vacío
        if cursor_hash and cursor_hash == h and not new_results:
            return jsonify({
                "status":"ok",
                "exec_id": exec_id,
                "symbol": symbol,
                "server_time": int(time.time()*1000),
                "hash": h,
                "count": len(events),
                "new_results": [],
                "events": [],
                "signals": [],
                "agg_score": float(agg),
                "agg_direction": agg_direction,
            }), 200

        return jsonify({
            "status":"ok",
            "exec_id": exec_id,
            "symbol": symbol,
            "server_time": int(time.time()*1000),
            "hash": h,
            "count": len(events),
            "new_results": new_results,
            "events": events,
            "signals": signals,
            "agg_score": float(agg),
            "agg_direction": agg_direction,
        }), 200

    except Exception as e:
        logger.exception("Error en /monitoreo/eventos")
        return jsonify({"status":"error","message":str(e)}), 500



@webhook_app.route("/monitoreo/incremental", methods=["POST"])
async def monitoreo_incremental():
    start = time.time()

    try:
        body = request.get_json(force=True) or {}
        user_id  = str(body.get("user_id") or "").strip()
        exec_id  = str(body.get("exec_id") or "").strip()
        symbol   = str(body.get("symbol") or "").strip().upper()
        timeframe = _norm_tf(body.get("timeframe"))
        last_ts  = body.get("last_ts")
        persist  = bool(body.get("persist", False))

        if not user_id:
            return jsonify({"status": "error", "message": "user_id es obligatorio"}), 400
        if not exec_id:
            return jsonify({"status": "error", "message": "exec_id es obligatorio"}), 400
        if not symbol or not timeframe:
            return jsonify({"status": "error", "message": "symbol y timeframe son obligatorios"}), 400

        logging.info(
            "INC START user=%s exec=%s body=%s",
            body.get("user_id"), body.get("exec_id"), body,
        )

        # normalizamos TF para usar en feature-flag
        tf_api = timeframe

        enabled = True
        if exec_id:
            enabled = _tf_is_enabled(exec_id, symbol, tf_api)

        logging.info(
            "HIST TFCHK sym=%s tf=%s enabled=%s user_id=%s exec_id=%s",
            symbol, timeframe, enabled, user_id, exec_id,
        )

        if not enabled:
            # Endpoint de lectura: no pisamos Firestore (evita que "running" vuelva a "stopped").
            # Para habilitar una TF, usá tf_states.<tf>.enabled=True o agregala a allowed_timeframes.
            return jsonify(
                {
                    "status": "ok",
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "exec_id": exec_id,
                    "from_ts": None,
                    "to_ts": None,
                    "candles": [],
                }
            ), 200

        # ------------------------------------------------------------
        # 0) Cargar estado base (serie) y refrescar desde GCS si hace falta
        # ------------------------------------------------------------
        st: dict = await asyncio.to_thread(_load_cache, exec_id, symbol, timeframe)
        prev_series_ms = _series_to_ms(st.get("series", []))
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
                _maybe_refresh_from_gcs, exec_id, symbol, timeframe, st, age
            )
        except Exception:
            logging.exception("INC %s %s: _maybe_refresh_from_gcs falló", symbol, timeframe)

        try:
            await asyncio.to_thread(
                _ensure_stream_initialized, exec_id, symbol, timeframe, st
            )
        except Exception:
            logging.exception("INC %s %s: _ensure_stream_initialized falló", symbol, timeframe)

        # ------------------------------------------------------------
        # 1) Normalizar last_ts
        # ------------------------------------------------------------
        try:
            last_ts = int(last_ts) if last_ts is not None else None
        except Exception:
            last_ts = None

        # ------------------------------------------------------------
        # 2) Serie base en ms + bucketizada
        # ------------------------------------------------------------
        base_ms = _series_to_ms(st.get("series", []) or [])
        base_ms = _snap_and_dedupe_to_minutes(base_ms, timeframe)

        # 3) Densificar + último tick de quote (SIN backfills ni FMP pesado)
        try:
            base_ms = _densify_minutes(base_ms, timeframe)
        except Exception:
            logging.exception("INC %s %s: _densify_minutes falló", symbol, timeframe)

        # Aseguramos que st["series"] tenga la serie base antes del tick
        st["series"] = base_ms

        try:
            # Firma: _maybe_tick_quote(exec_id, symbol, tf, st)
            changed_tick = await asyncio.to_thread(
                _maybe_tick_quote, exec_id, symbol, timeframe, st
            )
            if changed_tick:
                # _maybe_tick_quote modifica st["series"], la volvemos a tomar
                base_ms = _snap_and_dedupe_to_minutes(
                    _series_to_ms(st.get("series", [])), timeframe
                )
        except Exception:
            logging.exception("INC %s %s: _maybe_tick_quote falló", symbol, timeframe)

        last_server = base_ms[-1] if base_ms else None
        last_server_t = int(last_server.get("t")) if last_server and "t" in last_server else None

        # ------------------------------------------------------------
        # 4) Detectar cambios (nueva vela cerrada o actualización de la última)
        # ------------------------------------------------------------
        changed_by_reload = False
        if (
            prev_last
            and last_server
            and last_server_t
            and last_server_t > int(prev_last.get("t", 0))
        ):
            changed_by_reload = True
        elif prev_last and last_server:
            for k in ("o", "h", "l", "c", "v"):
                try:
                    if float(last_server.get(k, 0)) != float(prev_last.get(k, 0)):
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

        # ------------------------------------------------------------
        # 5) Calcular incremental a devolver
        #    - Si last_ts es None → devolvemos TODA la serie (seed)
        #    - Si hay velas nuevas (t > last_ts) → devolvemos sólo esas
        #    - Si no hay velas nuevas pero cambió la última → devolvemos sólo la última
        # ------------------------------------------------------------
        EPS = 1
        if last_ts is None:
            inc = base_ms
        elif last_server_t is not None and last_server_t > last_ts + EPS:
            inc = [c for c in base_ms if int(c.get("t", 0)) > last_ts]
        else:
            inc = (
                [last_server]
                if (changed and last_server_t and last_server_t >= (last_ts or 0) - EPS)
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

        # ------------------------------------------------------------
        # 6) Persistencia ligera en GCS
        #    Persistimos sólo cuando hay nueva vela cerrada o el cliente lo pide.
        # ------------------------------------------------------------
        new_bucket_started = bool(
            prev_last
            and last_server
            and last_server_t
            and last_server_t > int(prev_last.get("t", 0))
        )

        if inc:
            try:
                with _MON_CACHE_LOCK:
                    st["dirty"] = True
            except Exception:
                # por si _MON_CACHE_LOCK es un dummy en algún entorno
                st["dirty"] = True

        if new_bucket_started or persist:
            try:
                await asyncio.to_thread(
                    _persist_if_needed, exec_id, symbol, timeframe, True
                )
            except Exception:
                logging.exception(
                    "INC %s %s: _persist_if_needed falló", symbol, timeframe
                )

        # ------------------------------------------------------------
        # 7) Heartbeat / monitoreo en Firestore
        # ------------------------------------------------------------
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
            logging.exception("INC %s %s: fs_touch_monitoreo falló", symbol, timeframe)

        # ------------------------------------------------------------
        # 8) Log final + respuesta
        # ------------------------------------------------------------
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

        return jsonify(
            {
                "status": "ok",
                "symbol": symbol,
                "timeframe": timeframe,
                "exec_id": exec_id,
                "from_ts": inc[0]["t"] if inc else last_ts,
                "to_ts": inc[-1]["t"] if inc else last_ts,
                "candles": inc,
            }
        ), 200

    except Exception as e:
        logging.exception(
            "Error en /monitoreo/incremental (dur=%.3fs)",
            time.time() - start,
        )
        return jsonify({"status": "error", "message": str(e)}), 500



@webhook_app.route("/monitoreo/describe", methods=["GET"])
def monitoreo_describe():
    exec_id = request.args.get("exec_id","").strip()
    symbol  = request.args.get("symbol","").strip().upper()
    if not exec_id or not symbol:
        return jsonify({"status":"error","message":"exec_id y symbol son obligatorios"}), 400
    doc = db.collection("monitoreos").document(f"{exec_id}__{symbol}").get()
    data = doc.to_dict() or {}
    return jsonify({"status":"ok","doc": data}), 200



@webhook_app.route("/monitoreo/resume", methods=["GET"])
async def monitoreo_resume():
    """
    GET /monitoreo/resume?symbol=BTCUSD&timeframe=1min&exec_id=xxxx
    Respuesta: { status, symbol, timeframe, exec_id, last_ts (ms), count, source }
    """
    try:
        symbol = str((request.args.get("symbol") or "")).strip().upper()
        timeframe = _norm_tf(request.args.get("timeframe"))
        exec_id = str((request.args.get("exec_id") or "")).strip()
        if not symbol or not timeframe or not exec_id:
            return jsonify({"status":"error","message":"symbol, timeframe y exec_id son obligatorios"}), 400

        st = await asyncio.to_thread(_load_cache, exec_id, symbol, timeframe)
        series_ms = _series_to_ms(st.get("series", []))
        last_ts = series_ms[-1]["t"] if series_ms else None
        # Nota: /resume es de lectura; no cambiamos 'estado' para no pisar running/stopped.
        await asyncio.to_thread(fs_touch_monitoreo, exec_id, symbol, {
            "symbol": symbol,
            "timeframe": timeframe,
            "last_resume_at_ms": int(time.time() * 1000),
        })

        return jsonify({
            "status":"ok",
            "symbol": symbol,
            "timeframe": timeframe,
            "exec_id": exec_id,
            "last_ts": last_ts,
            "count": len(series_ms),
            "source": st.get("source","unknown"),
        }), 200
    except Exception as e:
        logging.exception("Error en /monitoreo/resume")
        return jsonify({"status": "error", "message": str(e)}), 500



@webhook_app.route("/monitoreo/history", methods=["POST"])
async def monitoreo_history():
    """
    POST /monitoreo/history
    Body {
      user_id: str,
      exec_id: str,
      symbol: str,
      timeframe: "1min"|"5min"|...|"1hour",
      limit?: int = 600,
      from_ts?: int (ms),
      to_ts?: int (ms),
      persist?: bool
    }
    Respuesta: {
      status, symbol, timeframe, exec_id,
      from_ts, to_ts, count, candles, persisted_path?
    }
    """
    try:
        body = request.get_json(force=True) or {}

        user_id   = str(body.get("user_id") or "").strip()
        exec_id   = str(body.get("exec_id") or "").strip()
        symbol    = str(body.get("symbol")  or "").strip().upper()
        timeframe = _norm_tf(body.get("timeframe"))

        limit     = body.get("limit", 600)
        from_ts   = body.get("from_ts", None)   # epoch ms
        to_ts     = body.get("to_ts", None)     # epoch ms
        persist   = bool(body.get("persist", False))

        if not user_id:
            return jsonify({"status": "error", "message": "user_id es obligatorio"}), 400
        if not exec_id:
            return jsonify({"status": "error", "message": "exec_id es obligatorio"}), 400
        if not symbol or not timeframe:
            return jsonify({"status": "error", "message": "symbol y timeframe son obligatorios"}), 400
        # timeframe ya está normalizado por _norm_tf(...)
        tf_api = timeframe

        enabled = True
        if exec_id:
            enabled = _tf_is_enabled(exec_id, symbol, tf_api)
        
        logging.info(
            "HIST TFCHK sym=%s tf=%s enabled=%s user_id=%s exec_id=%s",
            symbol, tf_api, enabled, user_id, exec_id,
        )

        if not enabled:
            # Endpoint de lectura: no pisamos Firestore acá.
            return jsonify(
                {
                    "status": "ok",
                    "symbol": symbol,
                    "timeframe": tf_api,
                    "exec_id": exec_id,
                    "from_ts": None,
                    "to_ts": None,
                    "candles": [],
                }
            ), 200

        try:
            limit = int(limit)
        except Exception:
            limit = 600
        limit = max(1, min(limit, 5000))

        # Carga cache y normaliza a ms
        st = await asyncio.to_thread(_load_cache, exec_id, symbol, timeframe)

        # TTL de refresco desde GCS por TF
        age_map = {
            "1min": 300,    # ya lo tratamos especial
            "5min": 120,    # antes 60
            "15min": 300,   # antes 120
            "30min": 600,   # antes 240
            "1hour": 1200,  # antes 600
            "4hour": 2400,  # antes 1200
        }
        age = age_map.get(timeframe, 60)

        # Para 1min: NO refrescamos desde GCS en cada history si ya hay serie en memoria.
        # Para el resto de TF, o si aún no hay datos, sí refrescamos normalmente.
        if timeframe not in ("1min", "1m") or not st.get("series"):
            await asyncio.to_thread(
                _maybe_refresh_from_gcs,
                exec_id,
                symbol,
                timeframe,
                st,
                age,
            )

        # Normaliza a ms DESPUÉS del posible refresh
        series_ms = _series_to_ms(st.get("series", []))



        # Filtrado por ventana (en ms si viene)
        if from_ts is not None:
            try: from_ts = int(from_ts)
            except Exception: from_ts = None
        if to_ts is not None:
            try: to_ts = int(to_ts)
            except Exception: to_ts = None

        if from_ts is not None or to_ts is not None:
            lo = float("-inf") if from_ts is None else from_ts
            hi = float("inf")  if to_ts   is None else to_ts
            filt = [c for c in series_ms if lo <= c["t"] <= hi]
        else:
            filt = series_ms

        # Aplica límite (últimas N) preservando orden ascendente
        if limit and len(filt) > limit:
            filt = filt[-limit:]

        from_out = filt[0]["t"] if filt else from_ts
        to_out   = filt[-1]["t"] if filt else to_ts

        # Persistencia opcional
        persisted = None
        if persist:
            with _MON_CACHE_LOCK:
                st["dirty"] = True
            persisted = await asyncio.to_thread(_persist_if_needed, exec_id, symbol, timeframe, True)

        # Heartbeat
        await asyncio.to_thread(fs_touch_monitoreo, exec_id, symbol, {
            "estado": "running",
            "symbol": symbol,
            "timeframe": timeframe,
            "user_id": user_id,
            "count_served": len(filt),
            "last_ts_served": (filt[-1]["t"] if filt else None),
        })

        resp = {
            "status": "ok",
            "symbol": symbol,
            "timeframe": timeframe,
            "exec_id": exec_id,
            "from_ts": from_out,
            "to_ts": to_out,
            "count": len(filt),
            "candles": filt,
        }
        if persisted:
            resp["persisted_path"] = f"gs://{BUCKET_NAME}/{persisted}"
        return jsonify(resp), 200

    except Exception as e:
        logging.exception("Error en /monitoreo/history")
        return jsonify({"status": "error", "message": str(e)}), 500


@webhook_app.route('/analisis/ejecutar', methods=['POST'])
#@profile
async def ejecutar_analisis_desde_app():
    chat_id_local = None
    acquired_lock = False
    try:
        # --- lee el payload primero ---
        data = request.json or {}
        user_id = str(data.get("user_id") or "").strip()
        chat_id = str(data.get("chat_id") or "").strip()
        chat_id_local = chat_id

        if not user_id:
            return jsonify({"status": "error", "message": "user_id es obligatorio"}), 400

        activo = data.get("activo")
        if activo is None:
            return jsonify({"status": "error", "message": "Falta 'activo'"}), 400

        origen = (data.get("origen") or "app").lower()

        # --- lock (después de validar mínimos) ---
        if ocupado_lock.locked():
            return "Estoy ocupado", 503
            
        await asyncio.to_thread(ocupado_lock.acquire)
        acquired_lock = True

        # --- validar suscripción ---
        kwargs = {"user_id": user_id} if user_id else {"chat_id": chat_id}
        estado_sub = await estado_suscripcion(
            **kwargs,
            numero_transacciones=1,
            origen=origen,
        )
        if estado_sub != "activa" and not es_administrador(user_id or chat_id):
            return jsonify({"status": "error", "message": "Suscripción inactiva o insuficiente"}), 403

        await asyncio.to_thread(mark_user_state, user_id=user_id or chat_id, estado="ocupado")

        # 1) Primero intenta con la clave de APP (user_id)
        opciones_usuario = await obtener_opciones_usuario(user_id, origen="app")

        # 2) Si no hay nada y existe un chat_id, intenta espejo con semántica de bot
        if (not opciones_usuario) and chat_id:
            try:
                opciones_usuario = await obtener_opciones_usuario(chat_id, origen="telegram")
            except Exception:
                opciones_usuario = opciones_usuario or []

        # 3) Admin por cualquiera de las dos claves
        is_admin = es_administrador(user_id) or (chat_id and es_administrador(chat_id))

        if (not is_admin) and not any(
            o in (opciones_usuario or []) for o in ("analisis basico", "analisis premium", "analisis avanzado")
        ):
            return jsonify({"status": "error", "message": "No tienes permisos para esta operación"}), 403

        # 4) config enviada por la app (setup/operatoria)
        raw_cfg = None
        if isinstance(data.get("setup"), dict):
            raw_cfg = data["setup"]
        elif isinstance(data.get("operatoria"), dict):
            op = data["operatoria"]
            raw_cfg = op.get("config", op)
        op_cfg = normalize_operatoria_payload(raw_cfg) if raw_cfg else None

        # 5) crear ejecución (guarda ambos IDs; chat_id puede venir vacío)
        exec_id = await asyncio.to_thread(
            fs_crear_ejecucion,
            user_id=user_id,
            chat_id=chat_id or None,
            activos_solicitados=[activo],
            origen="app",
            opciones_usuario=opciones_usuario,
        )

        my_worker_addr = os.getenv("WORKER_ADDR") 
        await asyncio.to_thread(
            fs_marcar_worker,
            exec_id,
            estado="running",
            worker_addr=os.getenv("WORKER_ADDR"),  # ej. "10.8.0.2:8103"
            detalles_worker={
                "pid": os.getpid(),
                "host": socket.gethostname(),
                "image": os.getenv("DOCKER_IMAGE", "markettool:latest"),
            },
        )

        # 6) dummy context/update para reusar el pipeline
        dummy_update = type("DummyUpdate", (), {
            "effective_chat": type("DummyChat", (), {"id": chat_id})(),
            "callback_query": None,
            "effective_user": type("DummyUser", (), {"first_name": "AppUser", "id": chat_id})()
        })()
        dummy_context = type("DummyContext", (), {"bot": application.bot})()

        # 7) cargar CFG + TZ desde user_ids/user_config.current
        user_ref = db.collection('user_ids').document(user_id)
        cfg_ref  = user_ref.collection('user_config').document('current')
        doc_user, doc_cfg = list(db.get_all([user_ref, cfg_ref]))

        cfg = (doc_cfg.to_dict() or {})
        tz_name = (doc_user.to_dict() or {}).get('timezone') or 'UTC'

        global timezone_country, timezone_name
        timezone_name = tz_name
        try:
            timezone_country = pytz.timezone(tz_name)
        except pytz.UnknownTimeZoneError:
            timezone_country = pytz.UTC
            timezone_name = 'UTC'

        # 8) ejecutar

        async def _runner():
            try:
                urls_local = await ejecutar_recurrente(
                    dummy_context, dummy_update,
                    activo, chat_id, opciones_usuario,
                    user_id=user_id, origen="app",
                    exec_id=exec_id, operatoria_cfg=op_cfg, cfg=cfg
                )
                # fin normal
                await asyncio.to_thread(fs_finalizar_ejecucion, exec_id, "completado", {"urls": urls_local})
                return urls_local
            except asyncio.CancelledError:
                # cancelación solicitada
                await asyncio.to_thread(fs_finalizar_ejecucion, exec_id, "stopped", {"detalle": "detenido_por_usuario"})
                raise
            except Exception as e:
                # error real
                await asyncio.to_thread(fs_finalizar_ejecucion, exec_id, "fallido", {"error": str(e)})
                raise
            finally:
                RUNNING.pop(exec_id, None)

        task = asyncio.create_task(_runner())
        RUNNING[exec_id] = task

        # Heartbeat en background (opcional, útil para detectar zombies)
        async def _hb():
            try:
                while not task.done():
                    await asyncio.sleep(8)
                    await asyncio.to_thread(fs_heartbeat, exec_id)
            except Exception:
                pass
        asyncio.create_task(_hb())

        resp = jsonify({"status": "accepted", "exec_id": exec_id})
        status_code = 202
        return resp, status_code
        
        #urls = await task
        #return jsonify({
        #    "status": "ok",
        #    "exec_id": exec_id,
        #    "message": f"Análisis ejecutado para {activo}",
        #    "download_urls": _solo_strings_urls(urls),
        #}), 200

    except Exception as e:
        logger.error(f"Error en /analisis/ejecutar: {e}")
        logging.exception("Error en /analisis/ejecutar")
        try:
            if 'exec_id' in locals():
                await asyncio.to_thread(fs_finalizar_ejecucion, exec_id, "fallido", {"error": str(e)})
        except Exception:
            pass
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        try:
            await asyncio.to_thread(mark_user_state, user_id=user_id or chat_id_local, estado="disponible")
            if chat_id_local:
                clear_current_request_cfg(chat_id_local)
        except Exception:
            pass
        if acquired_lock and ocupado_lock.locked():
            try:
                ocupado_lock.release()
            except Exception:
                pass    

@webhook_app.route('/analisis/stop', methods=['POST'])
async def detener_analisis_desde_app():
    try:
        body = request.get_json(force=True) or {}
        exec_id = str(body.get("exec_id") or "").strip()
        if not exec_id:
            return jsonify({"status":"error","message":"exec_id es obligatorio"}), 400

        # Debe llegar enroutado al nodo correcto por NGINX (?target=IP:PUERTO)
        task = RUNNING.get(exec_id)
        if not task:
            # idempotencia: si ya no existe, mira en DB el estado
            doc = db.collection("ejecuciones").document(exec_id).get()
            estado = (doc.to_dict() or {}).get("estado")
            if estado in {"stopped","completed","fallido"}:
                return jsonify({"status":"ok","exec_id":exec_id,"already":estado}), 200
            return jsonify({"status":"error","message":"exec_id no encontrado en este worker"}), 404

        # 1) marca intención de stop (NO toques worker_addr aquí)
        await asyncio.to_thread(
            fs_marcar_worker,
            exec_id,
            estado="stop_requested",
            detalles_worker={
                "stop_requested_at": int(time.time()),
                "stop_origin": "user/app",  # o "system/timeout", etc.
            },
        )

        # cancelar
        task.cancel()
        try:
            await task     # esperar cleanup del runner
        except asyncio.CancelledError:
            pass

        # 3) marca estado final
        await asyncio.to_thread(
            fs_marcar_worker,
            exec_id,
            estado="stopped",
            detalles_worker={
                "stopped_at": int(time.time()),
                "stopped_by": "user/app",
            },
        )

        return jsonify({"status":"ok","exec_id":exec_id,"stopped":True}), 200

    except Exception as e:
        logging.exception("Error en /analisis/stop")
        return jsonify({"status":"error","message":str(e)}), 500


def _get_stop_evt(exec_id: str) -> threading.Event:
    with STOP_EVENTS_LOCK:
        return STOP_EVENTS.setdefault(exec_id, threading.Event())

def _release_stop_evt(exec_id: str):
    with STOP_EVENTS_LOCK:
        STOP_EVENTS.pop(exec_id, None)

class StopRequested(Exception):
    pass

@webhook_app.route('/analisis/imagen', methods=['POST'])
async def subir_imagen_y_analizar():
    """
    Bloqueante (devuelve 200 con imagen_base64) y cancelable con /analisis/stop.
    Acepta exec_id opcional en el form/json para poder cancelarlo desde la app.
    """
    ruta_local = None
    ruta_salida = None
    acquired_lock = False
    exec_id = None
    user_id = None

    try:
        # ---- payload ----
        form = request.form or {}
        j = request.get_json(silent=True) or {}
        user_id = str(form.get("user_id") or j.get("user_id") or "").strip()
        chat_id = str(form.get("chat_id") or j.get("chat_id") or "").strip()  # opcional/legacy
        if not user_id:
            return jsonify({"status": "error", "message": "user_id es obligatorio"}), 400
        if "imagen" not in request.files:
            return jsonify({"status": "error", "message": "Falta archivo 'imagen'"}), 400

        # ---- lock global (si aplica) ----
        if ocupado_lock.locked():
            return "Estoy ocupado", 503
        await asyncio.to_thread(ocupado_lock.acquire)
        acquired_lock = True

        # ---- suscripción/permisos ----
        estado_sub = await estado_suscripcion(user_id=user_id, numero_transacciones=1, origen="app")
        if estado_sub != "activa" and not es_administrador(user_id or chat_id):
            return jsonify({"status": "error", "message": "Suscripción inactiva o insuficiente"}), 403

        await asyncio.to_thread(mark_user_state, user_id=user_id or chat_id, estado="ocupado")

        # ---- exec_id y guardado del archivo ----
        exec_id = (form.get("exec_id") or j.get("exec_id") or uuid.uuid4().hex)
        os.makedirs("imagenes", exist_ok=True)
        os.makedirs("procesadas", exist_ok=True)

        imagen = request.files["imagen"]
        ruta_local = os.path.join("imagenes", f"{exec_id}.jpg")
        await asyncio.to_thread(imagen.save, ruta_local)

        ts = int(time.time())
        db.collection("ejecuciones").document(exec_id).set({
            "estado": "running",
            "tipo": "analisis_imagen",
            "user_id": user_id,
            "created_at": ts,
            "updated_at": ts
        }, merge=True)

        # Publica dónde corre para que /analisis/stop pueda enrutar
        await asyncio.to_thread(
            fs_marcar_worker,
            exec_id,
            estado="running",
            worker_addr=os.getenv("WORKER_ADDR"),
            detalles_worker={"pid": os.getpid(), "tipo": "imagen", "origen": "app"},
        )

        stop_evt = _get_stop_evt(exec_id)
        RUNNING[exec_id] = asyncio.current_task()  # <- permite cancelación desde /analisis/stop

        try:
            mark_user_state(user_id=user_id, estado="esperando_grafico_ia")
        except Exception:
            pass

        # ---- validación rápida ----
        es_chart = await asyncio.to_thread(es_grafico_de_velas, ruta_local)
        if not es_chart:
            await asyncio.to_thread(fs_marcar_worker, exec_id, estado="fallido")
            db.collection("ejecuciones").document(exec_id).set({
                "estado": "fallido",
                "resumen": {"message": "❌ No parece ser un gráfico de velas"},
                "updated_at": int(time.time())
            }, merge=True)
            return jsonify({"status": "error", "message": "❌ No parece ser un gráfico de velas"}), 400

        # ---- análisis (en hilo) con soporte de stop_cb si existe) ----
        try:
            ruta_salida, texto_resultado = await asyncio.to_thread(
                analizar_con_yolo, ruta_local, stop_cb=stop_evt.is_set
            )
        except TypeError:
            ruta_salida, texto_resultado = await asyncio.to_thread(analizar_con_yolo, ruta_local)

        if stop_evt.is_set():
            raise asyncio.CancelledError()

        if not ruta_salida or not os.path.exists(ruta_salida):
            await asyncio.to_thread(fs_marcar_worker, exec_id, estado="fallido")
            db.collection("ejecuciones").document(exec_id).set({
                "estado": "fallido",
                "resumen": {"message": "No se generó imagen procesada"},
                "updated_at": int(time.time())
            }, merge=True)
            return jsonify({"status": "error", "message": "No se generó imagen procesada"}), 500

        # ---- base64 para la app ----
        with open(ruta_salida, "rb") as f:
            img_base64 = base64.b64encode(f.read()).decode("utf-8")

        # ---- cobro (si corresponde) ----
        try:
            if not es_administrador(user_id or chat_id):
                success, mensaje = await descontar_transaccion(user_id, 1)
                if not success:
                    db.collection("ejecuciones").document(exec_id).set({"billing_warn": mensaje}, merge=True)
        except Exception as cobro_e:
            logger.warning(f"[IA] Error en cobro: {cobro_e}")

        # ---- final OK ----
        await asyncio.to_thread(fs_marcar_worker, exec_id, estado="completed")
        db.collection("ejecuciones").document(exec_id).set({
            "estado": "completed",
            "resumen": {"message": texto_resultado, "imagen_base64": img_base64},
            "updated_at": int(time.time())
        }, merge=True)

        return jsonify({
            "status": "ok",
            "exec_id": exec_id,
            "message": texto_resultado,
            "imagen_base64": img_base64
        }), 200

    except asyncio.CancelledError:
        # Cancelado mediante /analisis/stop
        await asyncio.to_thread(fs_marcar_worker, exec_id, estado="stopped")
        db.collection("ejecuciones").document(exec_id).set({
            "estado": "stopped", "updated_at": int(time.time())
        }, merge=True)
        return jsonify({"status": "stopped", "exec_id": exec_id}), 200

    except Exception as e:
        logger.exception("❌ Error en /analisis/imagen")
        if exec_id:
            try:
                await asyncio.to_thread(fs_marcar_worker, exec_id, estado="fallido", detalles_worker={"error": str(e)})
                db.collection("ejecuciones").document(exec_id).set({
                    "estado": "fallido", "error": str(e), "updated_at": int(time.time())
                }, merge=True)
            except Exception:
                pass
        return jsonify({"status": "error", "message": str(e)}), 500

    finally:
        RUNNING.pop(exec_id, None)
        _release_stop_evt(exec_id)
        try:
            await asyncio.to_thread(mark_user_state, user_id=user_id or chat_id_local, estado="disponible")
        except Exception:
            pass
        try:
            if ruta_local and os.path.exists(ruta_local): os.remove(ruta_local)
            if ruta_salida and os.path.exists(ruta_salida): os.remove(ruta_salida)
        except Exception:
            pass
        if acquired_lock and ocupado_lock.locked():
            try: ocupado_lock.release()
            except Exception:
                pass


# Ruta para el webhook
@webhook_app.route('/webhook', methods=['POST'])
#@profile
async def webhook():
    try:
        payload = request.get_json()
        logger.info(f"Payload recibido: {payload}")
        update = Update.de_json(payload, application.bot)
        await application.process_update(update)
        return jsonify({"status": "ok"})
    except Exception as e:
        logger.info(f"Error procesando webhook: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500
    
@webhook_app.route('/health', methods=['GET'])
#@profile
def health():
    return {"status": "ok", "instance": socket.gethostname()}
    
@webhook_app.route('/healthz', methods=['GET'])
#@profile
def health_check():
    return jsonify({"status": "ok"}), 200

@webhook_app.route('/', methods=['GET'])
#@profile
def index():
    return "El bot está funcionando", 200


# Ruta de prueba
if __name__ == "__main__":
    logger.info("Inicializando el bot...")
    
    try:
        # Crear un bucle de eventos para manejar la inicialización
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(initialize_bot())
        
        logger.info("Inicialización completada. Ejecutando el servidor...")
        # Ejecutar el servidor
        webhook_url = os.environ.get("WEBHOOK_URL")
        port = int(os.environ.get("PUERTO", 8080))
        logger.info(f"WEBHOOK_URL = {webhook_url}, PUERTO={port}")
        if webhook_url:
            uvicorn.run(
                asgi_app, 
                host="0.0.0.0", 
                port=port, 
                log_level="info", 
                lifespan="off",
                log_config=LOGGING_CONFIG, 
                timeout_keep_alive=900,  # Espera hasta 5 minutos en keep-alive
                timeout_graceful_shutdown=900
                )
            logger.info("Webhook con Server Web configurado...")
        
    except Exception as e:
       logger.info(f"Error en la aplicación principal: {e}")
    except KeyboardInterrupt:
       logger.info("Programa detenido manualmente.")
    finally:
        # Cancelar todas las tareas pendientes
        pending = asyncio.all_tasks(loop)
        for task in pending:
            task.cancel()
        loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        loop.run_until_complete(loop.shutdown_asyncgens())  # Cerrar generadores asíncronos
        loop.close()  # Cerrar el bucle de eventos
        logger.info("Bucle de eventos cerrado correctamente.")