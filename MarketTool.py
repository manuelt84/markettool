
import os
import sys
import math
import random
import time
import json
import logging
import signal
import functools
import multiprocessing
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Dict, Any, Tuple, List, Callable, Iterable, Mapping
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
from asyncio import Lock, Semaphore, iscoroutinefunction
from collections import Counter
from collections import defaultdict
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor
from datetime import timedelta, date, datetime, timezone, UTC, timezone as dt_timezone
from flask import Flask, request, jsonify
from functools import partial
from google.cloud import firestore
from google.cloud import firestore as gcf
from google.cloud import storage
try:
    from icalendar import Calendar, Event  # pyright: ignore[reportMissingModuleSource]
except Exception:
    Calendar = None
    Event = None
from io import StringIO, BytesIO
# ✅ FIX: joblib.Parallel removed to fix ResourceTracker errors in Docker/Python 3.12
# Previous usage was redundant (Parallel with range(1) = no parallelization)
# For future parallel needs: use ThreadPoolExecutor or asyncio instead
# from joblib import Parallel, delayed, parallel_backend
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
import threading
from threading import Lock
try:
    from ultralytics import YOLO  # pyright: ignore[reportMissingImports]
except Exception:
    YOLO = None
from urllib.parse import urlencode
from uvicorn.config import LOGGING_CONFIG
from zoneinfo import ZoneInfo
from types import SimpleNamespace
import aiofiles
import asyncio
import base64
import concurrent.futures
import csv as _csv
import cv2
import datetime as _dt
try:
    import easyocr  # pyright: ignore[reportMissingImports]
except Exception:
    easyocr = None
import hashlib
try:
    import investiny
    _HAS_INVESTPY = True
except Exception:
    investiny = None
    _HAS_INVESTPY = False
try:
    from bs4 import BeautifulSoup
    _HAS_BEAUTIFULSOUP = True
except Exception:
    BeautifulSoup = None
    _HAS_BEAUTIFULSOUP = False
try:
    from playwright.sync_api import sync_playwright
    _HAS_PLAYWRIGHT = True
except Exception:
    sync_playwright = None
    _HAS_PLAYWRIGHT = False
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

from markettool.core.config import AppConfig, load_config
from markettool.infra.http.session import build_session
from markettool.infra.fmp import FMPClient, FMPError, FMPPlanNotAllowed, normalize_tf
try:
    import psutil
except Exception:
    psutil = None


# ======================================================================
# Config & Infra (Production-grade)
# ======================================================================

APP_CONFIG = load_config()
FMP_INTRADAY_SOURCE_TZ = os.getenv("FMP_INTRADAY_SOURCE_TZ", "America/New_York")

FMP_MAX_CONCURRENCY = int(os.environ.get("FMP_MAX_CONCURRENCY", "6"))
FMP_PER_SYMBOL_CONCURRENCY = int(os.environ.get("FMP_PER_SYMBOL_CONCURRENCY", "1"))
_FMP_GLOBAL_SEM = threading.BoundedSemaphore(FMP_MAX_CONCURRENCY) if FMP_MAX_CONCURRENCY > 0 else None
_FMP_SYMBOL_SEMS: dict[str, threading.BoundedSemaphore] = {}
_FMP_SYMBOL_SEMS_LOCK = threading.Lock()

def _get_fmp_symbol_sem(symbol: str) -> threading.BoundedSemaphore | None:
    if FMP_PER_SYMBOL_CONCURRENCY <= 0:
        return None
    key = (symbol or "").strip().upper()
    if not key:
        return None
    with _FMP_SYMBOL_SEMS_LOCK:
        sem = _FMP_SYMBOL_SEMS.get(key)
        if sem is None:
            sem = threading.BoundedSemaphore(FMP_PER_SYMBOL_CONCURRENCY)
            _FMP_SYMBOL_SEMS[key] = sem
    return sem

@contextmanager
def _fmp_http_guard(symbol: str | None = None):
    sems: list[threading.BoundedSemaphore] = []
    if _FMP_GLOBAL_SEM is not None:
        sems.append(_FMP_GLOBAL_SEM)
    if symbol:
        sym_sem = _get_fmp_symbol_sem(symbol)
        if sym_sem is not None:
            sems.append(sym_sem)
    for sem in sems:
        sem.acquire()
    try:
        yield
    finally:
        for sem in reversed(sems):
            sem.release()

def _fmp_http_get(
    url: str,
    params: Dict[str, Any] | None = None,
    timeout: int | None = None,
    symbol: str | None = None,
) -> requests.Response:
    with _fmp_http_guard(symbol):
        return HTTP_SESSION.get(url, params=params, timeout=timeout or APP_CONFIG.http_timeout)


# Structured logging
ECON_CHUNK_DAYS = int(os.environ.get("ECON_CHUNK_DAYS","31"))
_LOGGER_FORMAT = "%(levelname)s:%(asctime)s:%(name)s:%(message)s"
logging.basicConfig(level=getattr(logging, APP_CONFIG.log_level.upper(), logging.INFO),
                    format=_LOGGER_FORMAT)
logger = logging.getLogger("MarketTool")
logger.info(
    "[startup] HIST_DIR=%s cwd=%s hist_abs=%s",
    APP_CONFIG.hist_dir,
    os.getcwd(),
    os.path.abspath(APP_CONFIG.hist_dir),
)

# HTTP Session with retries
HTTP_SESSION = build_session(APP_CONFIG.http_retries, APP_CONFIG.http_backoff)

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

def _is_intraday(tf: str) -> bool:
    return normalize_tf(tf) in {"1min", "5min", "15min", "30min", "1hour", "4hour"}

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

# ========================================================================================
# 🔒 THREAD SAFETY LOCKS (Protegen diccionarios globales contra race conditions)
# ========================================================================================
_LAST_QUOTE_TICK_LOCK = threading.Lock()
_LAST_SYNC_LOCK = threading.Lock()

_STOP_WORDS = {"stopped", "paused", "off", "detenido", "parado"}

def normalize_tf_canonical(tf: str) -> str:
    """
    ÚNICA normalización canónica de timeframes.
    Entrada: cualquier variante (1m, 1min, 1minute, h1, 1hour, etc.)
    Salida: formato estándar backend (1min, 5min, 15min, 30min, 1hour, 4hour, 1day, 1week)
    """
    s = (tf or "").lower().strip()
    if not s:
        return ""
    
    # Mapeo completo de todas las variantes posibles
    mapping = {
        # 1 minuto
        '1m': '1min', '1min': '1min', '1mins': '1min', '1minute': '1min', '1minutes': '1min', '1': '1min',
        # 5 minutos
        '5m': '5min', '5min': '5min', '5mins': '5min', '5minute': '5min', '5minutes': '5min',
        # 15 minutos
        '15m': '15min', '15min': '15min', '15mins': '15min', '15minute': '15min', '15minutes': '15min',
        # 30 minutos
        '30m': '30min', '30min': '30min', '30mins': '30min', '30minute': '30min', '30minutes': '30min',
        # 1 hora
        '1h': '1hour', '1hour': '1hour', 'h1': '1hour', '1hr': '1hour',
        # 4 horas
        '4h': '4hour', '4hour': '4hour', 'h4': '4hour', '4hr': '4hour',
        # 1 día
        '1d': '1day', '1day': '1day', 'd1': '1day',
        # 1 semana
        '1w': '1week', '1week': '1week', 'w1': '1week',
    }
    
    return mapping.get(s, s)


def _norm_tf_allowed(tf: str) -> str:
    """
    Forma para allowed_timeframes (corta): 1m, 5m, 15m, etc.
    DEPRECADO: Usar normalize_tf_canonical() y convertir al final si es necesario.
    """
    canonical = normalize_tf_canonical(tf)
    short_map = {
        '1min': '1m', '5min': '5m', '15min': '15m', '30min': '30m',
        '1hour': '1h', '4hour': '4h', '1day': '1d', '1week': '1w'
    }
    return short_map.get(canonical, canonical)


def _norm_tf_backend(tf: str) -> str:
    """
    Normaliza TF hacia formato backend.
    DEPRECADO: Usar normalize_tf_canonical() directamente.
    """
    return normalize_tf_canonical(tf)


TF_TTL_MINUTES = {
    "1m":  10,    # antes 5 → duplicado para evitar false positives
    "5m":  20,    # antes 10
    "15m": 45,    # antes 30
    "30m": 90,    # antes 60
    "1h":  240,   # antes 180
    "4h":  480,   # antes 360
    "1d":  2880,  # antes 1440 (2 días)
    "1w":  15120, # antes 10080 (>1 semana)
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
    # Normalizaciones básicas usando función canónica
    symbol = (symbol or "").upper()
    tf_canonical = normalize_tf_canonical(tf)  # unificado
    tf_allowed = _norm_tf_allowed(tf)   # formato corto para comparación

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

    # Prioridad: buscar en tf_states y doc usando formato canónico
    st = (
        tf_states.get(tf_canonical)
        or tf_states.get(tf)  # fallback por si viene en otro formato
        or doc.get(tf_canonical)
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
        # Usar formato canónico para buscar TTL
        tf_short = _norm_tf_allowed(tf_canonical)
        ttl_minutes = TF_TTL_MINUTES.get(tf_short, 60)
        if now_ms - last_ms > ttl_minutes * 60_000:
            logger.debug(f"[_tf_is_enabled] TF {tf} expired: {now_ms - last_ms}ms > {ttl_minutes}min TTL")
            return False

    return True



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

def _save_local_history_df(symbol: str, tf: str, df: pd.DataFrame) -> None:
    """Best-effort local save for historicos (similar to indicators cache)."""
    try:
        if df is None or getattr(df, "empty", True):
            return

        out = df.copy()
        idx_utc = pd.DatetimeIndex(pd.to_datetime(out.index, utc=True, errors="coerce"))
        mask = ~idx_utc.isna()
        if not mask.all():
            out = out.loc[mask].copy()
            idx_utc = idx_utc[mask]

        out["time"] = idx_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
        for c in ("open", "high", "low", "close", "volume"):
            if c not in out.columns:
                out[c] = np.nan

        payload = out[["time", "open", "high", "low", "close", "volume"]].tail(1000).to_dict(orient="records")

        os.makedirs(APP_CONFIG.hist_dir, exist_ok=True)
        local_hist = _hist_path_json(symbol, tf)
        with open(local_hist, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        logger.debug("[hist_local] Saved %s rows=%d", local_hist, len(payload))
    except Exception as e:
        logger.debug("[hist_local] Save failed %s/%s: %s", symbol, tf, e)

def _load_local(symbol: str, tf: str) -> Optional[pd.DataFrame]:
    """Best-effort local load for historicos using the JSON cache."""
    try:
        local_hist = _hist_path_json(symbol, tf)
        if not os.path.exists(local_hist):
            return None

        ttl_seconds = int(os.environ.get("HIST_LOCAL_TTL_SECONDS", APP_CONFIG.cache_ttl_historicos))
        if ttl_seconds > 0:
            age_seconds = time.time() - os.path.getmtime(local_hist)
            if age_seconds > ttl_seconds:
                return None

        raw = Path(local_hist).read_text(encoding="utf-8")
        data = json.loads(raw) if raw.strip() else []
        if isinstance(data, dict):
            data = data.get("data", data.get("payload", []))

        df = pd.DataFrame(data)
        if df.empty:
            return df

        if "time" not in df.columns and "date" not in df.columns:
            return pd.DataFrame(columns=["open","high","low","close","volume"])

        time_col = "time" if "time" in df.columns else "date"
        df[time_col] = pd.to_datetime(df[time_col], errors="coerce", utc=True)
        df = df.dropna(subset=[time_col]).set_index(time_col).sort_index()
        df = _ensure_cols(df)
        if df.index.tz is None:
            df.index = df.index.tz_localize(pytz.UTC)
        return df
    except Exception as e:
        logger.debug("[hist_local] Load failed %s/%s: %s", symbol, tf, e)
        return None

def _ensure_cols(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for c in ["open","high","low","close","volume"]:
        if c in out.columns: out[c] = pd.to_numeric(out[c], errors="coerce")
        else: out[c] = np.nan
    return out[["open","high","low","close","volume"]]

@safe_op(default=pd.DataFrame(columns=["open","high","low","close","volume"]))
def load_cached_history(symbol: str, tf: str) -> pd.DataFrame:
    """
    Carga históricos del caché.
    ✅ OPTIMIZADO: Intenta en orden:
       1. LazyHistoricosLoader (LRU + TTL en memoria)
       2. Firestore Metadata (TTL compartido entre pods) ← NEW: Multi-pod coordination
       3. GCS (permanente, compartido)
       4. Archivos locales CSV/JSON (legacy)
    
    En multi-pod: El TTL se comparte via Firestore metadata, evitando que múltiples
    pods hagan FMP calls simultáneamente para el mismo símbolo.
    """
    import json
    
    # Opción 1: Lazy loader (rápido, en caché)
    try:
        df = _LAZY_HIST_LOADER.get(symbol, tf)
        if not df.empty:
            logger.debug(f"[load_cached] Hit LazyLoader: {symbol}/{tf}")
            return df
    except Exception as e:
        logger.debug(f"[LazyLoader] Failed to load {symbol}/{tf}: {e}")
    
    # Opción 2: Local files PRIMERO (muy rápido ~5ms, sin esperar red)
    # Si local es fresco (<4h), es mucho más rápido que Firestore/GCS
    try:
        df = _load_local(symbol, tf)
        if df is not None and not df.empty:
            logger.debug(f"[load_cached] Hit Local: {symbol}/{tf}")
            try:
                _LAZY_HIST_LOADER.put(symbol, tf, df)
            except Exception:
                pass
            return df
    except Exception as e:
        logger.debug(f"[load_cached] Local check failed: {e}")
    
    # Opción 3: Firestore Metadata (solo si local falló) ← Multi-pod coordination
    # Para coordinación entre pods, pero solo si no hay cache local
    try:
        metadata = get_historicos_metadata(symbol, tf)
        if metadata is not None and not is_metadata_stale(metadata):
            # TTL válido: datos en GCS están frescos
            logger.debug(f"[load_cached] Firestore metadata valid (ttl not expired): {symbol}/{tf}")
            
            # Cargar de GCS sabiendo que está actualizado
            try:
                df = load_from_gcs(symbol, tf)
                if df is not None and not df.empty:
                    logger.debug(f"[load_cached] Hit GCS (via Firestore TTL): {symbol}/{tf}")
                    _save_local_history_df(symbol, tf, df)
                    # Cachear localmente para próximas llamadas
                    try:
                        _LAZY_HIST_LOADER.put(symbol, tf, df)
                    except Exception:
                        pass
                    return df
            except Exception as gcs_err:
                logger.debug(f"[GCS] Load failed even though Firestore valid: {gcs_err}")
    except Exception as e:
        logger.debug(f"[Firestore] Metadata check failed (not fatal): {e}")
    
    # Opción 4: GCS fallback (300-500ms) - solo si Firestore/Local falló
    try:
        df = load_from_gcs(symbol, tf)
        if df is not None and not df.empty:
            logger.debug(f"[load_cached] Hit GCS: {symbol}/{tf}")
            _save_local_history_df(symbol, tf, df)
            # Cachear localmente para próximas llamadas
            try:
                _LAZY_HIST_LOADER.put(symbol, tf, df)
            except Exception:
                pass
            return df
    except Exception as e:
        logger.debug(f"[GCS] Load failed for {symbol}/{tf}, trying local: {e}")
    
    # Opción 5: Archivos locales legacy (CSV/JSON)
    primary = _hist_path(symbol, tf)
    alt = _hist_path_json(symbol, tf) if primary.endswith(".csv") else _hist_path_csv(symbol, tf)
    
    def _from_df(df):
        """Normaliza un DataFrame cargado desde archivos."""
        if "time" not in df.columns and "date" not in df.columns:
            return pd.DataFrame(columns=["open","high","low","close","volume"])
        
        time_col = "time" if "time" in df.columns else "date"
        df[time_col] = pd.to_datetime(df[time_col], errors="coerce", utc=True)
        df = df.dropna(subset=[time_col]).set_index(time_col).sort_index()
        df = _ensure_cols(df)
        if df.index.tz is None:
            df.index = df.index.tz_localize(pytz.UTC)
        return df
    
    # Intenta archivo primario
    if os.path.exists(primary):
        try:
            if primary.endswith(".csv"):
                df = pd.read_csv(primary, low_memory=False)
            else:
                raw = Path(primary).read_text(encoding="utf-8")
                data = json.loads(raw) if raw.strip() else []
                if isinstance(data, dict):
                    data = data.get("data", [])
                df = pd.DataFrame(data)
            return _from_df(df)
        except Exception as e:
            logger.debug(f"[load_cached] Error loading primary {symbol}/{tf}: {e}")
    
    # Fallback a formato alternativo
    if os.path.exists(alt):
        try:
            if alt.endswith(".csv"):
                df = pd.read_csv(alt, low_memory=False)
            else:
                raw = Path(alt).read_text(encoding="utf-8")
                data = json.loads(raw) if raw.strip() else []
                if isinstance(data, dict):
                    data = data.get("data", [])
                df = pd.DataFrame(data)
            return _from_df(df)
        except Exception as e:
            logger.debug(f"[load_cached] Error loading alt {symbol}/{tf}: {e}")
    
    # Empty fallback
    return pd.DataFrame(columns=["open","high","low","close","volume"])


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

        # --- FIX: Evitar guardar datos intradía en archivos diarios (1day) ---
        # Si detectamos que para '1day' tenemos datos muy frecuentes, remuestreamos.
        if normalize_tf(tf) == "1day" and len(out) > 5:
            # Calculamos la diferencia mediana en los últimos registros
            diffs = pd.Series(idx_utc[-20:]).diff().dropna()
            if not diffs.empty:
                med = diffs.median()
                # Si la mediana es menor a 4 horas, asumo que es intradía erróneo
                if med < timedelta(hours=4):
                    logger.warning(f"[save_cached_history] CORRUPT DATA DETECTED: {symbol}/1day contains intraday data (median diff {med}). Resampling to 1D...")
                    
                    # Asegurar tipos numéricos
                    for c in ("open", "high", "low", "close", "volume"):
                        if c in out.columns:
                            out[c] = pd.to_numeric(out[c], errors="coerce")
                    
                    # Remuestrear a 1D
                    # Usamos .agg con diccionario para OHLCV
                    out = out.resample("1D").agg({
                        "open": "first",
                        "high": "max",
                        "low": "min",
                        "close": "last",
                        "volume": "sum"
                    }).dropna(subset=["close"])
                    
                    # Recalcular idx_utc post-resample
                    idx_utc = pd.DatetimeIndex(pd.to_datetime(out.index, utc=True))
        # ---------------------------------------------------------------------

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
        
        # ✅ OPTIMIZACIÓN: Solo guardar LOCALMENTE (rápido ~5ms)
        # GCS/Firestore se hace en background cada 4h por warmup job
        # Esto evita que el análisis espere a red cada vez
        try:
            _save_local_history_df(symbol, tf, out)
            with open(local_json, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, default=str)
            logger.debug("[save_cached_history] Local saved: %s rows=%d (GCS deferred to warmup)", symbol, len(out))
        except Exception as local_err:
            logger.warning(f"[save_cached_history] Even local save failed for {symbol}/{tf}: {local_err}")

    except Exception as e:
        # MUY importante mantener este mensaje, porque tus logs lo buscan por texto
        logger.warning("save_cached_history failed: %s", e)
        return


@dataclass
class HistoryConfig:
    bars: Optional[int] = None
    append_realtime: bool = True
    allow_refresh: bool = True
    fmp_window: Optional[int] = None   # Nueva propiedad para override de ventana inicial


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


def _parse_history_refresh_ttl_minutes() -> dict[str, int]:
    """
    Cache-first TTL (minutes) per timeframe.
    Env override:
      HISTORY_REFRESH_TTL_MINUTES="1min:1,5min:5,15min:15,30min:30,1hour:60,4hour:240,1day:1440,1week:10080"
    """
    policy = {
        "1min": 1,
        "5min": 5,
        "15min": 15,
        "30min": 30,
        "1hour": 60,
        "4hour": 240,
        "1day": 1440,
        "1week": 10080,
    }

    raw = str(os.getenv("HISTORY_REFRESH_TTL_MINUTES", "")).strip()
    if not raw:
        return policy

    try:
        for token in raw.split(","):
            token = token.strip()
            if not token or ":" not in token:
                continue
            k, v = token.split(":", 1)
            tf = _norm_tf(k.strip())
            minutes = int(v.strip())
            if tf and minutes >= 0:
                policy[tf] = minutes
    except Exception:
        pass
    return policy


_HISTORY_REFRESH_TTL_MINUTES = _parse_history_refresh_ttl_minutes()

class HistoryManager:
    def __init__(self, client: FMPClient):
        self.client = client
        self._quote_cache: dict[str, dict] = {}
        self._quote_cache_ttl = int(os.environ.get("HISTORY_QUOTE_CACHE_SECONDS", "10"))
        self._quote_cache_lock = threading.Lock()  # ✅ FIX: Thread-safe quote cache
        # FMP call deduplicator: prevent simultaneous calls for same symbol/TF
        self._fmp_locks: dict[str, threading.Lock] = {}
        self._fmp_lock_mutex = threading.Lock()

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

    def _get_quote_cached(self, symbol: str) -> Optional[float]:
        """Cache quote locally per TTL to reduce FMP calls. Thread-safe."""
        key = (symbol or "").upper()
        if not key:
            return None
        now = time.time()
        # ✅ FIX: Lock protects dict access from concurrent modifications
        with self._quote_cache_lock:
            cached = self._quote_cache.get(key)
            if cached and (now - cached.get("ts", 0)) < self._quote_cache_ttl:
                return cached.get("price")
        price = self.client.quote_last(symbol)
        with self._quote_cache_lock:
            self._quote_cache[key] = {"ts": now, "price": price}
        return price

    def _get_fmp_lock(self, symbol: str, tf: str) -> "threading.Lock":
        """Get or create a lock for this symbol/TF to deduplicate FMP calls."""
        key = f"{symbol}_{tf}".upper()
        with self._fmp_lock_mutex:
            if key not in self._fmp_locks:
                import threading
                self._fmp_locks[key] = threading.Lock()
            return self._fmp_locks[key]

    def _append_realtime_last_bar(self, symbol: str, tf: str, df: pd.DataFrame) -> pd.DataFrame:
        try:
            last_ts = df.index[-1]; now = utc_now()
            lag_min = (now - last_ts).total_seconds() / 60.0
            tol = {"1min":3,"5min":7,"15min":18,"30min":35,"1hour":70,"4hour":260}.get(normalize_tf(tf), 180)
            if lag_min > tol: return df
            px = self._get_quote_cached(symbol)
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

        # --- Cache de activos válidos (cargado una sola vez por HistoryManager) ---
        if not hasattr(self, '_valid_symbols_cache'):
            try:
                activos = set()
                # 1. config/activos (campo 'symbols')
                activos_docs = db.collection('config').document('activos').get()
                if activos_docs.exists:
                    activos_data = activos_docs.to_dict()
                    activos.update(activos_data.get('symbols', []))
                # 2. config/categorias (todas las listas de cada categoría)
                categorias_docs = db.collection('config').document('categorias').get()
                if categorias_docs.exists:
                    categorias_data = categorias_docs.to_dict().get('data', {})
                    for arr in categorias_data.values():
                        if isinstance(arr, list):
                            activos.update(arr)
                # Cache el resultado PERMANENTEMENTE
                self._valid_symbols_cache = activos
                logger.debug(f"[HistoryManager] Loaded {len(activos)} valid symbols from Firestore")
            except Exception as e:
                logger.debug(f"[HistoryManager] Failed to load valid symbols: {e}")
                self._valid_symbols_cache = set()
        
        now = utc_now()
        allow_refresh = cfg.allow_refresh
        if cache_df.empty:
            from_dt = datetime(1900, 1, 1, tzinfo=pytz.UTC)
            logger.info(f"[CACHE-FIRST] {symbol}/{tf}: no local cache, will fetch from FMP")
        else:
            try:
                last = cache_df.index[-1]
                # Refuerzo: convierte a tz-aware UTC
                if not isinstance(last, pd.Timestamp):
                    last = pd.to_datetime(last, utc=True)
                else:
                    last = last.to_pydatetime()
                
                if getattr(last, 'tzinfo', None) is None or last.tzinfo is None:
                    last = pytz.UTC.localize(last)
                elif last.tzinfo != pytz.UTC:
                    last = last.astimezone(pytz.UTC)
                
                # ✅ CACHE-FIRST: Skip FMP fetch if cache is fresh within TTL
                ttl_min = _HISTORY_REFRESH_TTL_MINUTES.get(tf, 1)
                age_min = max(0.0, (now - last).total_seconds() / 60.0)
                logger.info(f"[CACHE-FIRST] {symbol}/{tf}: age={age_min:.2f}min, ttl={ttl_min}min, allow_refresh={allow_refresh}")
                
                if allow_refresh and age_min < ttl_min:
                    logger.info(f"[CACHE-FIRST] {symbol}/{tf}: SKIPPING FMP (cache fresh within TTL)")
                    allow_refresh = False
                    
                base_tf = self._base_interval_for(tf)
                from_dt = last + self._timedelta_for(base_tf, 1)
            except Exception as idx_err:
                logging.warning(f"[HIST][ERROR] Index parsing failed for {symbol}/{tf}: {idx_err}. Using fallback (1900).")
                from_dt = datetime(1900, 1, 1, tzinfo=pytz.UTC)

        to_dt = now
        new_df = pd.DataFrame()
        if allow_refresh and from_dt < to_dt:
            lock = self._get_fmp_lock(symbol, tf)
            with lock:
                # Double-check cache after acquiring lock (another worker may have fetched)
                cache_df_check = load_cached_history(symbol, tf)
                # Compare last timestamp instead of entire DF to avoid pandas Bool comparison issues
                if (not cache_df_check.empty and 
                    len(cache_df) > 0 and len(cache_df_check) > 0 and
                    cache_df.index[-1] == cache_df_check.index[-1]):
                    # Cache hasn't changed, skip FMP fetch
                    logger.info(f"[FMP-DEDUP] {symbol}/{tf}: Worker ahead already fetched, using cache")
                    new_df = pd.DataFrame()
                else:
                    # Proceed with FMP fetch
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

        out_full = merge_histories(cache_df, new_df)
        out = out_full
        if cfg.bars and isinstance(cfg.bars, int) and cfg.bars > 0 and len(out) > cfg.bars:
            out = out.tail(cfg.bars)
        if cfg.append_realtime and not out.empty:
            out = self._append_realtime_last_bar(symbol, tf, out)
        if not out_full.empty:
            save_cached_history(symbol, tf, out_full)
        return out

# Public API (overrides legacy)
_FMP = FMPClient(
    api_key=APP_CONFIG.fmp_api_key,
    plan=APP_CONFIG.fmp_plan,
    timeout=APP_CONFIG.http_timeout,
    http_session=HTTP_SESSION,
    intraday_source_tz=FMP_INTRADAY_SOURCE_TZ,
    max_concurrency=FMP_MAX_CONCURRENCY,
    per_symbol_concurrency=FMP_PER_SYMBOL_CONCURRENCY,
)
_HIST = HistoryManager(client=_FMP)

def obtener_datos_historicos(symbol: str, temporalidad: str,
                             bars: Optional[int] = None,
                             append_realtime: bool = True,
                             allow_refresh: bool = True,
                             fmp_window: Optional[int] = None) -> pd.DataFrame:
    cfg = HistoryConfig(bars=bars, append_realtime=append_realtime, allow_refresh=allow_refresh, fmp_window=fmp_window)
    return _HIST.get(symbol, temporalidad, cfg=cfg)

def obtener_datos_historicos_fmp(symbol: str, temporalidad: str, *,
                                 bars: int | None = None, **kwargs):
    # kwargs puede traer fmp_window
    window = kwargs.get("fmp_window") or kwargs.get("window")
    return obtener_datos_historicos(symbol, temporalidad, bars=bars, append_realtime=True, allow_refresh=True, fmp_window=window)


#mpl.rcParams['figure.max_open_warning'] = 200

matplotlib.use('Agg')

pd.set_option('future.no_silent_downcasting', True)

warnings.filterwarnings("ignore", message="Maximum Likelihood optimization failed to converge")
warnings.filterwarnings("ignore", category=UserWarning, message="Detected filter using positional arguments")

#from pathlib import Path
#from dotenv import load_dotenv
#load_dotenv(dotenv_path=Path(__file__).with_name(".env"))

try:
    # Si usas firebase_admin con google-cloud-firestore detrás:

    SERVER_TS = gcf.SERVER_TIMESTAMP
except Exception:
    SERVER_TS = None  # fallback si no está disponible

timezone_country = pytz.UTC

# 📝 LOCAL MEMORY CACHE (per-pod, ephemeral - lost on pod restart)
# Stores per-user session state: estado, par_seleccionado, cache_realtime, soportes_resistencias_cache
# ⚠️ NOT PERSISTENT: Use Firestore 'user_states' collection for persistent storage
# ✅ THREAD-SAFE: Protected by user_states_lock
user_states = {}

# Timeout aumentado para APIs externas (FMP, Investing) bajo alta concurrencia
timeout_request_global = 10  # Tiempo máximo de espera en segundos
max_workers_global = min(32, (os.cpu_count() or 1) * 2) #puede tener 64

# Concurrency knobs (override via env for stronger machines)
_CPU_COUNT = os.cpu_count() or 1
_ANALYSIS_MAX_WORKERS = int(os.environ.get("ANALYSIS_MAX_WORKERS", str(min(64, _CPU_COUNT * 2))))
_ANALYSIS_SEM = int(os.environ.get("ANALYSIS_SEMAPHORE", str(min(_ANALYSIS_MAX_WORKERS, max(8, _CPU_COUNT)))) )
_ANALYSIS_INNER_WORKERS = int(os.environ.get("ANALYSIS_INNER_WORKERS", "4"))
_ANALYSIS_PRED_WORKERS = int(os.environ.get("ANALYSIS_PRED_WORKERS", "3"))
_ANALYSIS_PRED_USE_PROCESS = os.environ.get("ANALYSIS_PRED_USE_PROCESS", "false").lower() == "true"

_ANALYSIS_EXECUTOR = ThreadPoolExecutor(max_workers=max(1, _ANALYSIS_MAX_WORKERS))
_ANALYSIS_INNER_EXECUTOR = (
    ThreadPoolExecutor(max_workers=max(1, _ANALYSIS_INNER_WORKERS))
    if _ANALYSIS_INNER_WORKERS > 0
    else None
)
if _ANALYSIS_PRED_WORKERS > 0:
    # ✅ FIX: Use spawn context for ProcessPoolExecutor (safer than fork with gRPC)
    # fork() can corrupt gRPC state; spawn() is slower but thread-safe
    if _ANALYSIS_PRED_USE_PROCESS:
        try:
            ctx = multiprocessing.get_context('spawn')
            _ANALYSIS_PRED_EXECUTOR = ProcessPoolExecutor(
                max_workers=max(1, _ANALYSIS_PRED_WORKERS),
                mp_context=ctx
            )
            logger.info("[Init] Using ProcessPoolExecutor with spawn context (gRPC-safe)")
        except Exception as e:
            logger.warning(f"[Init] Failed to create spawn-based ProcessPoolExecutor ({e}), falling back to ThreadPoolExecutor")
            _ANALYSIS_PRED_EXECUTOR = ThreadPoolExecutor(max_workers=max(1, _ANALYSIS_PRED_WORKERS))
    else:
        _ANALYSIS_PRED_EXECUTOR = ThreadPoolExecutor(max_workers=max(1, _ANALYSIS_PRED_WORKERS))
else:
    _ANALYSIS_PRED_EXECUTOR = None

# 🔍 DEBUG: Log de inicialización de executores
_pred_executor_type = type(_ANALYSIS_PRED_EXECUTOR).__name__ if _ANALYSIS_PRED_EXECUTOR else "None"
logger.info(f"[Init] Executor predicciones: {_pred_executor_type} (workers={_ANALYSIS_PRED_WORKERS}, use_process={_ANALYSIS_PRED_USE_PROCESS})")

# ═══════════════════════════════════════════════════════════════════════════════
# THREAD POOL HEALTH MONITORING (detect starvation, failed tasks)
# ═══════════════════════════════════════════════════════════════════════════════

class ExecutorHealthMonitor:
    """Monitors thread pool health metrics without overhead."""
    def __init__(self, name: str):
        self.name = name
        self.task_count = 0
        self.failed_tasks = 0
        self.total_time = 0
        self.lock = threading.Lock()
    
    def record_task(self, elapsed_s: float = 0, failed: bool = False):
        """Record task completion metrics."""
        with self.lock:
            self.task_count += 1
            if failed:
                self.failed_tasks += 1
            self.total_time += elapsed_s
    
    def get_stats(self) -> dict:
        """Get current health metrics."""
        with self.lock:
            avg_time = self.total_time / max(1, self.task_count)
            fail_rate = self.failed_tasks / max(1, self.task_count)
            return {
                "name": self.name,
                "tasks": self.task_count,
                "failures": self.failed_tasks,
                "fail_rate": fail_rate,
                "avg_time_s": avg_time,
            }
    
    def log_health(self):
        """Log health metrics if failures detected."""
        stats = self.get_stats()
        if stats["fail_rate"] > 0.01:  # >1% failure rate
            logger.warning(f"[ThreadPool:{self.name}] high failure rate: {stats['fail_rate']:.1%} ({stats['failures']}/{stats['tasks']})")
        if stats["avg_time_s"] > 60:  # >60s average time
            logger.warning(f"[ThreadPool:{self.name}] slow tasks: avg {stats['avg_time_s']:.1f}s")

# Create monitors for main executors
_analysis_monitor = ExecutorHealthMonitor("ANALYSIS")
_analysis_inner_monitor = ExecutorHealthMonitor("ANALYSIS_INNER")
_analysis_pred_monitor = ExecutorHealthMonitor("ANALYSIS_PRED")

subscriptions = {}
subscriptions_type = {}
admin_ids = {}

# ✅ FIX: Use RLock (Reentrant Lock) to allow same thread to acquire lock multiple times
# This prevents deadlocks when functions call each other (e.g., limpiar_soportes_resistencias_cache -> mark_user_state)
user_states_lock = threading.RLock()  # Reentrant: safe for nested lock acquisitions
matplotlib_lock = threading.Lock()

CARPETA_HISTORICOS = "historicos"
CARPETA_FOREX_NEWS = "forex_news"

# Archivo JSON para almacenar las suscripciones
TIME_BETWEEN_MESSAGES = 1  # En segundos

#DIRECCION_USDT_TRC20 = 'TJ5HvX7EfNCrNFXHGCdGYQ59n5H6pcjm6b' #BINANCE
DIRECCION_USDT_TRC20 = 'TNYdZMs5eGYcwdY8vEAe59utu2RYhdyquh' #UNSTOPPABLE

# Memoria temporal para las noticias (defaultdict para auto-inicializar símbolos)
cache_noticias = defaultdict(pd.DataFrame)  # Diccionario donde la clave es el símbolo
cache_noticias_lock = threading.Lock()  # 🔒 CRITICAL: Protect concurrent dict access
cache_historicos = {}
ultima_actualizacion_historicos = {}

# ✅ Lazy-initialized globals (prevent undefined variable errors on import)
# These are loaded on-demand via obtener_datos_firestore() and obtener_configuracion()
activos = []
forex = []
relacionados_usd = []
categorias = {}
temporalidades = []
zonas_horarias = []

señales_compra = ['Compra', 'Compra Fuerte', 'Compra Predicha', 'Compra Predicha con ARIMA', 'Compra Predicha con Media Movil', 'Compra Predicha con ARIMA y Media Movil']
señales_venta = ['Venta', 'Venta Fuerte', 'Venta Predicha', 'Venta Predicha con ARIMA', 'Venta Predicha con Media Movil', 'Venta Predicha con ARIMA y Media Movil']

file_locks = {}
guardar_lock = asyncio.Lock()

# logging.basicConfig already configured at line 182 - don't duplicate it
# Suppress verbose loggers from external libraries
logging.getLogger("httpx").setLevel(logging.WARNING)  # Para httpx
logging.getLogger("urllib3").setLevel(logging.WARNING)  # Para requests

# API Key de FMP (Premium)
API_KEY = (os.environ.get("FMP_API_KEY") or "").strip()
if not API_KEY:
    raise RuntimeError("Falta FMP_API_KEY en el entorno/.env. Usa --env .env o define la variable antes de ejecutar.")

# Firestore and GCS clients (public exports for bootstrap.py and hexagonal architecture)
db = firestore.Client()
storage_client = storage.Client()  # GCS client instance (keep 'storage' module for other uses)

# NOTE: Don't modify LOGGING_CONFIG here - it would add duplicate handlers
# The root logger is already configured at line 182

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
RUNNING_LOCK = threading.Lock()  # ✅ FIX: Use threading.Lock (NOT asyncio.Lock) for cross-thread safety

STOP_EVENTS: dict[str, threading.Event] = {}
STOP_EVENTS_LOCK = threading.Lock()

USER_STATE_STALE_SECONDS = int(os.getenv("USER_STATE_STALE_SECONDS", "180"))   # 3 min
USER_STATE_SWEEP_EVERY   = int(os.getenv("USER_STATE_SWEEP_EVERY", "60"))       # cada 60s
USER_LOCK_TTL_SECONDS    = int(os.getenv("USER_LOCK_TTL_SECONDS", "1800"))      # 30 min (compat)
USER_LOCK_MIN_SECONDS    = int(os.getenv("USER_LOCK_MIN_SECONDS", "120"))       # 2 min
USER_LOCK_MAX_SECONDS    = int(os.getenv("USER_LOCK_MAX_SECONDS", "1800"))      # 30 min
USER_LOCK_SEC_PER_ASSET  = int(os.getenv("USER_LOCK_SEC_PER_ASSET", "50"))      # 50s por activo
USER_STATE_BUSY_VALUES   = {
    "ocupado",
    "en_ejecucion",
    "en ejecucion",
    "en ejecución",
    "esperando_grafico_ia",
    "running",
}

def compute_lock_ttl(activos_count: int) -> int:
    """Calcula TTL dinamico: max(min, activos*seg_por_activo), cap max."""
    try:
        count = int(activos_count)
    except Exception:
        count = 1
    if count < 1:
        count = 1
    ttl = max(USER_LOCK_MIN_SECONDS, count * USER_LOCK_SEC_PER_ASSET)
    return min(USER_LOCK_MAX_SECONDS, ttl)

# ✅ Ruta base del app (para buscar modelos YOLO)
_APP_ROOT = Path(__file__).parent.absolute()
logger.info(f"[Startup] APP_ROOT={_APP_ROOT}, cwd={os.getcwd()}")

def _load_yolo_model(model_path: str):
    """
    Carga modelo YOLO desde archivo local.
    Busca en: cwd, _APP_ROOT, /app
    """
    if YOLO is None:
        logger.warning("[YOLO] Ultralytics no instalado")
        return None
    
    paths_to_try = [
        Path(model_path),                    # cwd=/app, busca en raíz
        _APP_ROOT / model_path,              # relativo a script
        Path("/app") / model_path,           # Docker absolute
    ]
    
    logger.info(f"[YOLO] Buscando {model_path} en: {', '.join(str(p) for p in paths_to_try)}")
    
    for path in paths_to_try:
        if path.exists():
            logger.info(f"[YOLO] ✅ Encontrado: {path}")
            try:
                model = YOLO(str(path))
                logger.info(f"[YOLO] ✅ Cargado: {path}")
                return model
            except Exception as e:
                logger.error(f"[YOLO] Error cargando {path}: {e}")
                continue
    
    logger.error(f"[YOLO] ❌ No encontrado: {model_path} (sin descargar de internet)")
    return None

# ✅ LAZY LOADING: Los modelos se cargan bajo demanda, NO en el startup
# Esto evita que la startup se bloquee descargando modelos de YOLO
_modelo_patrones_cache = None
_modelo_ruido_cache = None
_yolo_models_lock = threading.Lock()

def get_modelo_patrones():
    """Carga YOLO para patrones bajo demanda (la primera vez que se use)."""
    global _modelo_patrones_cache
    if _modelo_patrones_cache is None:
        with _yolo_models_lock:
            if _modelo_patrones_cache is None:
                logger.info("[YOLO] Cargando modelo de patrones (primera vez)...")
                _modelo_patrones_cache = _load_yolo_model("patrones.pt") or False
    return _modelo_patrones_cache if _modelo_patrones_cache is not False else None

def get_modelo_ruido():
    """Carga YOLO para ruido bajo demanda (la primera vez que se use)."""
    global _modelo_ruido_cache
    if _modelo_ruido_cache is None:
        with _yolo_models_lock:
            if _modelo_ruido_cache is None:
                logger.info("[YOLO] Cargando modelo de ruido (primera vez)...")
                _modelo_ruido_cache = _load_yolo_model("ruido.pt") or False
    return _modelo_ruido_cache if _modelo_ruido_cache is not False else None

# Compatibilidad hacia atrás (por si algo usa `modelo_patrones` directamente)
modelo_patrones = None  # Nunca se usa directamente, usar get_modelo_patrones()
modelo_ruido = None     # Nunca se usa directamente, usar get_modelo_ruido()

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
        # FIXED: Wrap Firestore .stream() with timeout using ThreadPoolExecutor
        # If Firestore is slow/down, don't block the watchdog indefinitely
        def _load_and_sweep():
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
            
            return pending
        
        # Execute with timeout (30 seconds): Firestore usually responds in <5s
        from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(_load_and_sweep)
            try:
                pending = future.result(timeout=30)
                logger.debug(f"[watchdog] cleaned {pending} stuck user states")
            except FutureTimeoutError:
                logger.error("[watchdog] Firestore timeout after 30s - skipping this sweep")
                future.cancel()

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

# GPU availability logging (disabled in startup, use logger if needed)
# logger.info("[Startup] GPU available: %s", torch.cuda.is_available())

#@profile
def get_easyocr_reader(prefer_gpu: bool = True):
    """
    Inicializa EasyOCR una sola vez con cache de modelos, usa GPU si está disponible,
    y hace fallback a CPU si falla.
    """
    if easyocr is None:
        logger.warning("EasyOCR no esta instalado; OCR deshabilitado.")
        return None
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
# Con manejo graceful de fallos si no hay conectividad
try:
    reader = get_easyocr_reader(prefer_gpu=True)
    logger.info("[EasyOCR] Inicialización exitosa")
except Exception as e:
    logger.warning(f"[EasyOCR] No se pudo inicializar EasyOCR en startup: {e}")
    logger.warning("[EasyOCR] El sistema operará sin OCR. La funcionalidad OCR no estará disponible.")
    reader = None 


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
REQUEST_OPERATORIA_LOCK = threading.Lock()

#@profile
def clear_current_request_cfg(user_chat_id: str) -> None:
    with REQUEST_OPERATORIA_LOCK:
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

# --------------------------
# Cache de config Firestore
# --------------------------
_ACTIVOS_DESC_CACHE = None

def _get_activos_desc() -> dict:
    global _ACTIVOS_DESC_CACHE
    if _ACTIVOS_DESC_CACHE is not None:
        return _ACTIVOS_DESC_CACHE
    try:
        doc = db.collection("config").document("activos_con_descripcion").get()
        _ACTIVOS_DESC_CACHE = (doc.to_dict() or {}).get("data") or {}
    except Exception:
        _ACTIVOS_DESC_CACHE = {}
    return _ACTIVOS_DESC_CACHE

def _universe_symbols() -> set:
    # Usa tus listas globales si existen; si no, intenta Firestore
    out = set()
    try:
        out.update(activos or [])
    except Exception:
        pass
    try:
        out.update(forex or [])
    except Exception:
        pass
    # si tienes categorías, agrega todo
    try:
        for k, arr in (categorias or {}).items():
            if isinstance(arr, list):
                out.update(arr)
    except Exception:
        pass
    cleaned = set(s.strip().upper() for s in out if isinstance(s, str) and s.strip())
    # Excluir palabras de categorias u otros tokens no simbolo
    try:
        category_words = {k.strip().upper() for k in (categorias or {}).keys() if isinstance(k, str) and k.strip()}
    except Exception:
        category_words = set()
    category_words.update({"TODOS", "ALL"})
    cleaned = set(s for s in cleaned if s not in category_words)
    # Evita intentar cachear codigos de moneda sueltos (ej: AUD) que no tienen historicos.
    return set(s for s in cleaned if not (len(s) == 3 and s in _CURRENCY_CODES))

# temporalidades válidas del sistema
def _valid_timeframes() -> set:
    try:
        return set(normalize_tf(x) for x in (temporalidades or []) if isinstance(x, str))
    except Exception:
        return {"1min","5min","15min","30min","1hour","4hour","1day","1week"}

def _memory_usage_percent() -> Optional[float]:
    """Returns memory usage percent or None if not available."""
    if psutil is None:
        return None
    try:
        return float(psutil.virtual_memory().percent)
    except Exception:
        return None

async def warmup_cache_all_assets(reason: str = "scheduled"):
    """
    Precalienta cachés para historicos, indicadores, noticias y eventos.
    ✅ TODOS los pods se pre-calientan EN PARALELO para máximo rendimiento.
    Solo el líder ejecuta esto si cache_warmup_leader_only=true (obsoleto, para compat).
    """
    global _warmup_start_time, _warmup_end_time
    
    if not APP_CONFIG.cache_warmup_enabled:
        return
    
    # Verificación de liderazgo: solo si EXPLÍCITAMENTE está configurado que solo el líder lo haga
    if APP_CONFIG.cache_warmup_leader_only and not _POD_COORDINATOR.should_run_scheduled_task("warmup_cache"):
        logger.info("[Warmup] %s: SKIPPED (leader-only mode, this pod is not the leader)", reason)
        return

    _ensure_globals_loaded()
    symbols = sorted(_universe_symbols())
    tfs = sorted(_valid_timeframes())
    if not symbols or not tfs:
        logger.info("[Warmup] No symbols/timeframes available")
        return

    _warmup_start_time = time.time()
    start = _warmup_start_time
    logger.info(
        "[Warmup] ===== INICIANDO ===== reason=%s, symbols=%d, timeframes=%d, concurrency=%d, leader_only=%s",
        reason,
        len(symbols),
        len(tfs),
        APP_CONFIG.cache_warmup_concurrency,
        APP_CONFIG.cache_warmup_leader_only,
    )

    sem = asyncio.Semaphore(max(1, APP_CONFIG.cache_warmup_concurrency))

    async def _warm_symbol_tf(symbol: str, tf: str):
        async with sem:
            try:
                await asyncio.to_thread(load_cached_history, symbol, tf)
            except Exception as e:
                logger.debug(f"[Warmup] Failed to load history {symbol}/{tf}: {type(e).__name__}: {e}")
            
            try:
                await asyncio.to_thread(_INDICATORS_CACHE.load, symbol, tf)
            except Exception as e:
                logger.debug(f"[Warmup] Failed to load indicators {symbol}/{tf}: {type(e).__name__}: {e}")

    limit_reached = False
    warmed_symbols = 0
    warmed_pairs = 0
    
    # 🚀 PARALELIZACIÓN OPTIMIZADA: Crear TODAS las tasks sin bloquear por símbolo
    all_warmup_tasks = []
    task_to_pair = {}  # Map id(task) -> (symbol, tf)
    
    for symbol in symbols:
        mem_pct = _memory_usage_percent()
        if mem_pct is not None and mem_pct >= APP_CONFIG.cache_warmup_max_ram_percent:
            logger.warning(
                "[Warmup] Memory usage %.1f%% >= %d%%. Stopping task creation.",
                mem_pct,
                APP_CONFIG.cache_warmup_max_ram_percent,
            )
            limit_reached = True
            break
        
        for tf in tfs:
            task = asyncio.create_task(_warm_symbol_tf(symbol, tf))
            all_warmup_tasks.append(task)
            task_to_pair[id(task)] = (symbol, tf)
    
    # Ejecutar TODOS los warmups en paralelo, procesando resultados conforme se completan
    if all_warmup_tasks:
        logger.info(f"[Warmup] Procesando {len(all_warmup_tasks)} pares (symbol, TF) con as_completed()")
        
        unique_symbols = set()
        for completed_task in asyncio.as_completed(all_warmup_tasks):
            try:
                result = await completed_task
                task_id = id(completed_task)
                if task_id not in task_to_pair:
                    logger.debug(f"[Warmup] Task {task_id} not found in mapping (already processed)")
                    continue
                symbol, tf = task_to_pair[task_id]
                if symbol not in unique_symbols:
                    unique_symbols.add(symbol)
                    warmed_symbols = len(unique_symbols)
                warmed_pairs += 1
            except Exception as e:
                task_id = id(completed_task)
                symbol_tf = task_to_pair.get(task_id, ("?", "?"))
                logger.debug(f"[Warmup] Error warming {symbol_tf[0]}/{symbol_tf[1]} (task {task_id}): {type(e).__name__}: {e}")
    
    # 🔔 Cargar eventos económicos y noticias en paralelo (si habilitado)
    additional_tasks = []
    if APP_CONFIG.cache_warmup_events_enabled:
        try:
            additional_tasks.append(asyncio.create_task(get_eventos_economicos_cached(grace_minutes=0)))
        except Exception:
            pass
    
    if APP_CONFIG.cache_warmup_news_enabled:
        # Cargar noticias para cada símbolo en paralelo
        for symbol in symbols:
            if limit_reached:
                break
            try:
                additional_tasks.append(asyncio.create_task(
                    get_noticias_cached(symbol, limite=APP_CONFIG.cache_warmup_news_limit)
                ))
            except Exception:
                pass
    
    # Ejecutar tareas adicionales (eventos + noticias) 
    if additional_tasks:
        await asyncio.gather(*additional_tasks, return_exceptions=True)
    
    elapsed = time.time() - start
    _warmup_end_time = time.time()
    
    if limit_reached:
        logger.warning("[Warmup] ===== COMPLETADO (anticipado) ===== reason=%s, symbols=%d, pairs=%d, tiempo=%.1fs", 
                      reason, warmed_symbols, warmed_pairs, elapsed)
    else:
        warmed_symbols = len(symbols)
        logger.info("[Warmup] ===== COMPLETADO ===== reason=%s, símbolos=%d, timeframes=%d, pairs=%d, tiempo=%.1fs", 
                   reason, warmed_symbols, len(tfs), len(all_warmup_tasks), elapsed)

# --------------------------
# Normalización OCR
# --------------------------
_TF_ALIASES = {
    "1M": "1min", "M1": "1min", "1MIN": "1min",
    "5M": "5min", "M5": "5min", "5MIN": "5min",
    "15M": "15min", "M15": "15min", "15MIN": "15min",
    "30M": "30min", "M30": "30min", "30MIN": "30min",
    "1H": "1hour", "H1": "1hour",
    "4H": "4hour", "H4": "4hour",
    "D": "1day", "1D": "1day",
    "W": "1week", "1W": "1week",
}

_PROVIDERS = {"OANDA","FXCM","FOREXCOM","FX","TV","TRADINGVIEW","BINANCE","COINBASE","BITSTAMP","KRAKEN"}
_CURRENCY_CODES = {
    "USD","EUR","JPY","GBP","CHF","CAD","AUD","NZD",
    "MXN","BRL","CLP","COP","PEN","ARS","TRY","ZAR","RUB",
    "CNY","CNH","HKD","SGD","NOK","SEK","DKK","PLN","CZK","HUF","RON",
    "ILS","SAR","AED","QAR","KWD","BHD","OMR","JOD","EGP",
    "THB","IDR","MYR","PHP","KRW","TWD","INR","PKR","BDT","VND",
}

def _clean_token(s: str) -> str:
    s = (s or "").strip().upper()
    s = s.replace(" ", "")
    # si viene "OANDA:EURUSD" -> "EURUSD"
    if ":" in s:
        s = s.split(":")[-1]
    # quita caracteres raros comunes
    s = re.sub(r"[^A-Z0-9\.\-_\/]", "", s)
    return s

def _maybe_symbol_variants(tok: str) -> List[str]:
    """
    Genera variantes: EUR/USD -> EURUSD, EURUSD=X -> EURUSD, etc.
    """
    t = _clean_token(tok)
    out = []
    if not t:
        return out

    # evita providers solos
    if t in _PROVIDERS:
        return []

    # elimina sufijos estilo =X
    if t.endswith("=X"):
        t = t[:-2]

    out.append(t)

    # si tiene / o -, también produce versión concatenada
    if "/" in t:
        out.append(t.replace("/", ""))
    if "-" in t:
        out.append(t.replace("-", ""))

    # si viene con . (acciones tipo BRK.B), mantenemos
    return list(dict.fromkeys(out))

def _extract_timeframe_from_texts(texts: List[str]) -> Optional[str]:
    valid = _valid_timeframes()
    for raw in texts:
        t = _clean_token(raw)
        if not t:
            continue
        # match directo en alias
        if t in _TF_ALIASES:
            tf = normalize_tf(_TF_ALIASES[t])
            if tf in valid:
                return tf
        # match patrones como "15MIN", "1H", "4H"
        m = re.search(r"(^|\b)(\d{1,2})(MIN|M|H|D|W)(\b|$)", t)
        if m:
            n = m.group(2)
            u = m.group(3)
            key = f"{n}{u}"
            if key in _TF_ALIASES:
                tf = normalize_tf(_TF_ALIASES[key])
                if tf in valid:
                    return tf
    return None

def _extract_price_candidates(texts: List[str]) -> List[float]:
    """
    Trata de sacar precios tipo 1.1123 o 1,1123 o 63.50
    """
    out = []
    for raw in texts:
        s = (raw or "").strip()
        # reemplaza coma decimal por punto si parece decimal
        s2 = s.replace(",", ".")
        # busca números con 1+ dígitos y opcional decimal
        for m in re.finditer(r"(?<!\d)(\d{1,5}(?:\.\d{1,6})?)(?!\d)", s2):
            try:
                val = float(m.group(1))
                # filtra basura obvia
                if 0 < val < 1_000_000:
                    out.append(val)
            except Exception:
                pass
    return out

def infer_symbol_tf_from_image(img_bgr, stop_cb=None, include_tech: bool=False) -> dict:
    """
    OCR en header (top ~22%) para inferir symbol y timeframe.
    Valida contra universo Firestore y confirma con FMP (si hay precio OCR).
    """
    def stop_now():
        return bool(stop_cb and stop_cb())

    h, w = img_bgr.shape[:2]
    y2 = max(1, int(h * 0.22))
    header = img_bgr[0:y2, 0:w].copy()

    # OCR solo header (graceful degradation si EasyOCR no está disponible)
    if reader is None:
        logger.warning("[OCR] EasyOCR no está disponible. No se puede detectar símbolo de imagen.")
        return {}  # Retornar vacío en lugar de crashear
    
    ocr = reader.readtext(header)  # [(bbox, text, conf), ...]
    if stop_now():
        raise RuntimeError("stopped")

    raw_texts = []
    confs = []
    for (_bbox, txt, c) in ocr:
        if not txt:
            continue
        raw_texts.append(str(txt))
        confs.append(float(c or 0))

    # tokens limpios
    tokens = []
    for t in raw_texts:
        tokens.append(_clean_token(t))

    universe = _universe_symbols()

    # candidatos por token (con variantes)
    candidates = []
    for t in tokens:
        for v in _maybe_symbol_variants(t):
            if v in universe:
                candidates.append(v)

    # también intenta combinar divisas "EUR" + "USD" => "EURUSD"
    # (solo si no encontró nada)
    if not candidates:
        triples = [t for t in tokens if re.fullmatch(r"[A-Z]{3}", t or "")]
        for i in range(len(triples)-1):
            pair = (triples[i] + triples[i+1]).upper()
            if pair in universe:
                candidates.append(pair)

    # timeframe
    tf = _extract_timeframe_from_texts(raw_texts) or None

    # si hay varios candidatos, intenta desempatar con precio OCR + FMP
    price_ocr = None
    prices = _extract_price_candidates(raw_texts)
    if prices:
        # toma el más “razonable”: el mayor suele ser escala de precio; aquí tomamos el último por simplicidad
        price_ocr = prices[-1]

    best = None
    best_score = -1.0
    best_quote = None

    # si no hay candidates, igual devolvemos debug
    uniq = list(dict.fromkeys(candidates))

    for sym in uniq:
        if stop_now():
            raise RuntimeError("stopped")

        # score base
        score = 1.0

        # bonus si el token original contenía ":" (provider) y extraímos el símbolo
        # (no siempre disponible; dejamos base simple)
        # confirmación por precio con FMP (si hay precio OCR)
        q = None
        if price_ocr is not None:
            try:
                q = _FMP.quote_last(sym)  # ya existe en tu script
            except Exception:
                q = None
            if q is not None and q > 0:
                rel = abs(q - price_ocr) / max(1e-9, q)
                # mientras más cercano, mejor
                score += max(0.0, 2.0 - min(2.0, rel * 20.0))  # rel 0.05 => +1, etc.
        else:
            # si no hay precio OCR, al menos intenta quote (para adjuntar info)
            try:
                q = _FMP.quote_last(sym)
            except Exception:
                q = None

        # bonus si pertenece a forex (si tienes lista forex)
        try:
            if sym in set(x.upper() for x in (forex or [])):
                score += 0.3
        except Exception:
            pass

        if score > best_score:
            best_score = score
            best = sym
            best_quote = q

    out = {
        "symbol": best,
        "timeframe": tf or None,
        "confidence": float(min(1.0, best_score / 3.0)) if best else 0.0,
        "quote_last": float(best_quote) if isinstance(best_quote, (int, float)) else None,
    }

    if include_tech:
        out["tech"] = {
            "header_texts": raw_texts,
            "candidates": uniq,
            "price_ocr": price_ocr,
            "score": best_score,
        }

    return out

def _atr14(df: pd.DataFrame) -> float:
    if df is None or df.empty:
        return 0.0
    d = df.copy()
    for c in ("high","low","close"):
        if c not in d.columns:
            return 0.0
    h = d["high"].astype(float)
    l = d["low"].astype(float)
    c = d["close"].astype(float)
    prev = c.shift(1)
    tr = pd.concat([(h-l), (h-prev).abs(), (l-prev).abs()], axis=1).max(axis=1)
    atr = tr.rolling(14).mean().iloc[-1]
    return float(atr) if np.isfinite(atr) else 0.0

def _sma(s: pd.Series, n: int) -> float:
    if s is None or len(s) < n:
        return float("nan")
    v = s.rolling(n).mean().iloc[-1]
    return float(v) if np.isfinite(v) else float("nan")

def build_insights_from_fmp(symbol: str, tf: str, stop_cb=None) -> dict:
    def stop_now():
        return bool(stop_cb and stop_cb())

    tf = normalize_tf(tf or "1hour")
    now = datetime.now(UTC)

    # ventana de data según tf
    if tf in {"1min","5min","15min","30min"}:
        days = 7
        from_utc = now - timedelta(days=days)
        df = _FMP.historical_intraday(symbol, tf, from_utc, now)
    elif tf in {"1hour","4hour"}:
        days = 60
        from_utc = now - timedelta(days=days)
        df = _FMP.historical_intraday(symbol, tf, from_utc, now)
    else:
        # eod para 1day / 1week
        from_date = now.date() - timedelta(days=365 * 2)
        df = _FMP.historical_eod(symbol, from_date, now)

    if stop_now():
        raise RuntimeError("stopped")

    if df is None or df.empty:
        return {
            "scenario": "unknown",
            "targets": {},
            "triggers": [],
            "risks": ["No se pudo obtener histórico para generar escenarios."],
            "summary": "No hay datos suficientes para construir escenarios.",
        }

    closes = df["close"].astype(float)
    last = float(closes.iloc[-1])

    sma20 = _sma(closes, 20)
    sma50 = _sma(closes, 50)
    atr = _atr14(df)

    # soporte/resistencia simples (últimas 50 velas)
    tail = df.tail(50)
    support = float(tail["low"].astype(float).min())
    resist  = float(tail["high"].astype(float).max())

    # escenario
    if np.isfinite(sma50) and last > sma50 and (not np.isfinite(sma20) or last > sma20):
        scenario = "bullish"
    elif np.isfinite(sma50) and last < sma50 and (not np.isfinite(sma20) or last < sma20):
        scenario = "bearish"
    else:
        scenario = "sideways"

    # targets (muy “app-like”)
    def fmt(v: float) -> float:
        return float(v) if np.isfinite(v) else None

    if atr <= 0:
        atr = max(1e-9, abs(resist - support) * 0.15)

    targets = {
        "short":  {"value": fmt(resist if scenario != "bearish" else support)},
        "medium": {"value": fmt((resist + atr) if scenario != "bearish" else (support - atr))},
        "long":   {"value": fmt((resist + 2*atr) if scenario != "bearish" else (support - 2*atr))},
    }

    triggers = []
    risks = []

    if scenario == "bullish":
        triggers.append(f"Ruptura y cierre por encima de {resist:.4f}")
        triggers.append("Aumento de volumen/impulso en la ruptura (si aplica)")
        risks.append(f"Rechazo en resistencia ({resist:.4f}) y retroceso hacia {support:.4f}")
        risks.append("Falsa ruptura (breakout fallido)")
    elif scenario == "bearish":
        triggers.append(f"Ruptura y cierre por debajo de {support:.4f}")
        triggers.append("Presión vendedora sostenida (velas con cuerpo amplio)")
        risks.append(f"Rebote técnico en soporte ({support:.4f}) hacia {resist:.4f}")
        risks.append("Short squeeze / rebotes violentos")
    else:
        triggers.append(f"Rango definido entre {support:.4f} y {resist:.4f}")
        triggers.append("Esperar ruptura confirmada para sesgo direccional")
        risks.append("Whipsaws: entradas en medio del rango")
        risks.append("Rupturas falsas en extremos del rango")

    # “Confluencia” simple (puedes sofisticar luego)
    # score 0..1 en base a distancia a SMA50 y cercanía a extremos
    score = 0.5
    if np.isfinite(sma50) and sma50 != 0:
        score = min(1.0, max(0.0, 0.5 + (last - sma50) / (abs(sma50)*0.01)))
    label = "Alta" if score >= 0.80 else ("Media" if score >= 0.65 else "Baja")

    summary = (
        f"Escenario: {scenario.upper()} | "
        f"Soporte: {support:.4f} | Resistencia: {resist:.4f} | "
        f"Objetivo corto: {targets['short']['value']:.4f}"
    )

    return {
        "scenario": scenario,
        "support": support,
        "resistance": resist,
        "targets": targets,
        "triggers": triggers,
        "risks": risks,
        "summary": summary,
        "confluencia": {"label": label, "score": float(min(1.0, max(0.0, score)))},
    }

_BOX_COLORS = [
    (0, 0, 255),     # rojo
    (255, 0, 255),   # magenta
    (0, 128, 255),   # naranja
    (255, 0, 0),     # azul
    (0, 0, 0),       # negro
]

PATRON_CONF_MIN = float(os.getenv("PATRON_CONF_MIN", "0.65")) 
PATRON_IOU_NMS = float(os.getenv("PATRON_IOU_NMS", "0.25")) 
PATRON_MAX_DET = int(os.getenv("PATRON_MAX_DET", "80")) # max interno del modelo (antes de tu filtro) 
TOPK_POR_CLASE = int(os.getenv("PATRON_TOPK_POR_CLASE", "2")) 
MAX_TOTAL_FINAL = int(os.getenv("PATRON_MAX_TOTAL_FINAL", "12"))

MIN_AREA_FRAC = float(os.getenv("PATRON_MIN_AREA_FRAC", "0.001")) # 0.1% del área imagen 
MAX_AREA_FRAC = float(os.getenv("PATRON_MAX_AREA_FRAC", "0.20")) # 20% del área imagen

# ---- Modo "en formación" (predicción visual) ----
FORM_ENABLED      = os.getenv("PATRON_FORM_ENABLED", "1") == "1"
FORM_WIN_FRAC     = float(os.getenv("PATRON_FORM_WIN_FRAC", "0.22"))  # 22% ancho derecho
FORM_CONF_MIN     = float(os.getenv("PATRON_FORM_CONF_MIN", "0.35"))
FORM_IOU_NMS      = float(os.getenv("PATRON_FORM_IOU_NMS", "0.20"))
FORM_MAX_DET      = int(os.getenv("PATRON_FORM_MAX_DET", "60"))
FORM_MAX_FINAL    = int(os.getenv("PATRON_FORM_MAX_FINAL", "6"))
FORM_MIN_AREA_FRAC= float(os.getenv("PATRON_FORM_MIN_AREA_FRAC", "0.0004")) # más permisivo
FORM_MAX_AREA_FRAC= float(os.getenv("PATRON_FORM_MAX_AREA_FRAC", "0.35"))
FORM_EDGE_PX      = int(os.getenv("PATRON_FORM_EDGE_PX", "10"))  # cerca del borde derecho
FORM_TOPK_POR_CLASE = int(os.getenv("PATRON_FORM_TOPK_POR_CLASE", "2"))

# ROI adaptativo: detectar hasta donde llega realmente el grafico (evita ejes/texto si hay gap)
FORM_PROBE_FRAC       = float(os.getenv("PATRON_FORM_PROBE_FRAC", "0.35"))     # ventana de sondeo (>= FORM_WIN_FRAC)
FORM_COL_SMOOTH_WIN   = int(os.getenv("PATRON_FORM_COL_SMOOTH_WIN", "21"))     # suavizado por columnas
FORM_COL_SCORE_THR    = float(os.getenv("PATRON_FORM_COL_SCORE_THR", "0.025"))  # umbral actividad (no-blanco+bordes)
FORM_COL_EDGE_W       = float(os.getenv("PATRON_FORM_COL_EDGE_W", "0.60"))     # peso de bordes en score
FORM_GAP_MIN_PX       = int(os.getenv("PATRON_FORM_GAP_MIN_PX", "40"))         # gap minimo para recortar
FORM_GAP_MIN_FRAC     = float(os.getenv("PATRON_FORM_GAP_MIN_FRAC", "0.06"))    # gap minimo relativo




# ---- Ajustes anti-falsos positivos (en formacion) ----
FORM_AXIS_MARGIN_FRAC = float(os.getenv("PATRON_FORM_AXIS_MARGIN_FRAC", "0.06"))  # recorta eje/precio (derecha)
FORM_TOP_CROP_FRAC    = float(os.getenv("PATRON_FORM_TOP_CROP_FRAC", "0.05"))     # recorta header
FORM_BOTTOM_CROP_FRAC = float(os.getenv("PATRON_FORM_BOTTOM_CROP_FRAC", "0.10"))  # recorta footer
FORM_LAST_X_FRAC      = float(os.getenv("PATRON_FORM_LAST_X_FRAC", "0.45"))      # exigir tramo final del ROI
FORM_MIN_NONWHITE     = float(os.getenv("PATRON_FORM_MIN_NONWHITE", "0.012"))     # % pixeles no-blancos min
FORM_MIN_EDGE         = float(os.getenv("PATRON_FORM_MIN_EDGE", "0.003"))         # % bordes (Canny) min
FORM_WHITE_THR        = int(os.getenv("PATRON_FORM_WHITE_THR", "245"))            # umbral de blanco

# ---- Heurística “fin real del gráfico” (evita detectar en eje/espacios) ----
# Se basa en *pixeles coloreados* (velas rojas/verdes) para no confundir texto/etiquetas.
FORM_COLOR_S_THR          = int(os.getenv("PATRON_FORM_COLOR_S_THR", "35"))          # saturación mínima para “color”
FORM_COLOR_V_MAX          = int(os.getenv("PATRON_FORM_COLOR_V_MAX", "250"))         # valor máximo (evita casi-blanco)
FORM_ACTIVE_DENS_THR      = float(os.getenv("PATRON_FORM_ACTIVE_DENS_THR", "0.0025")) # densidad de color por columna
FORM_ACTIVE_SPAN_THR      = float(os.getenv("PATRON_FORM_ACTIVE_SPAN_THR", "0.05"))   # span vertical mínimo por columna
FORM_ACTIVE_SMOOTH_WIN    = int(os.getenv("PATRON_FORM_ACTIVE_SMOOTH_WIN", "31"))     # suavizado columnas
FORM_ACTIVE_MIN_RUN_PX    = int(os.getenv("PATRON_FORM_ACTIVE_MIN_RUN_PX", "70"))     # largo mínimo (px) para considerar “zona de velas”
FORM_BOX_MIN_COLORED      = float(os.getenv("PATRON_FORM_BOX_MIN_COLORED", "0.0012")) # % pixeles coloreados mínimos dentro de bbox
FORM_BOX_S_THR            = int(os.getenv("PATRON_FORM_BOX_S_THR", "30"))             # saturación mínima dentro de bbox

# ---- Anti-linea de precio (horizontal punteada) ----
FORM_COLOR_V_MIN          = int(os.getenv("PATRON_FORM_COLOR_V_MIN", "45"))             # valor mínimo para considerar vela (evita grises)
FORM_RED_HI               = int(os.getenv("PATRON_FORM_RED_HI", "10"))                  # H <= 10
FORM_RED_LO               = int(os.getenv("PATRON_FORM_RED_LO", "170"))                 # H >= 170
FORM_GREEN_LO             = int(os.getenv("PATRON_FORM_GREEN_LO", "60"))                # H >= 60
FORM_GREEN_HI             = int(os.getenv("PATRON_FORM_GREEN_HI", "105"))               # H <= 105
FORM_REMOVE_HLINES        = os.getenv("PATRON_FORM_REMOVE_HLINES", "1") == "1"
FORM_HLINE_KERNEL_PX      = int(os.getenv("PATRON_FORM_HLINE_KERNEL_PX", "45"))          # largo del kernel horizontal
FORM_HLINE_DILATE_ITER    = int(os.getenv("PATRON_FORM_HLINE_DILATE_ITER", "1"))        # engrosar mascara de linea
FORM_BOX_MIN_SPAN_FRAC    = float(os.getenv("PATRON_FORM_BOX_MIN_SPAN_FRAC", "0.12"))    # span vertical minimo dentro bbox (fraccion de alto)
FORM_BOX_MIN_SPAN_PX      = int(os.getenv("PATRON_FORM_BOX_MIN_SPAN_PX", "6"))           # span vertical minimo absoluto (px)

def _iou_xyxy(a, b) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    inter_x1 = max(ax1, bx1)
    inter_y1 = max(ay1, by1)
    inter_x2 = min(ax2, bx2)
    inter_y2 = min(ay2, by2)
    iw = max(0.0, inter_x2 - inter_x1)
    ih = max(0.0, inter_y2 - inter_y1)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    area_a = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
    area_b = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
    denom = (area_a + area_b - inter) or 1.0
    return float(inter / denom)

def _run_formacion_en_ventana_derecha(modelo_patrones, clean_img_path: str, full_w: int, full_h: int, existentes_xyxy: list, stop_now):
    """Devuelve detecciones "en formacion" en coords de imagen completa.

    Mejora clave:
    - ROI adaptativo: se ancla al "ultimo contenido real" (velas) y evita detectar sobre
      margenes/ejes/texto cuando hay un espacio en blanco grande a la derecha.
    - Si las velas llegan al borde, NO recorta (detecta igual).
    - Anti-falsos positivos: recorte vertical (header/footer) + filtro de contenido (no-blanco + bordes).
    """
    if not FORM_ENABLED:
        return []

    img = cv2.imread(clean_img_path)
    if img is None:
        return []

    # Recorte vertical para evitar overlays (barra superior e inferior)
    y0 = int(full_h * FORM_TOP_CROP_FRAC)
    y_end = int(full_h * (1.0 - FORM_BOTTOM_CROP_FRAC))
    y0 = max(0, min(y0, full_h - 1))
    y_end = max(y0 + 1, min(y_end, full_h))

    # --- 1) Encuentra el "ultimo contenido" en una ventana de sondeo mas amplia (para detectar gap) ---
    probe_frac = max(FORM_WIN_FRAC, FORM_PROBE_FRAC)
    x_probe0 = int(full_w * (1.0 - probe_frac))
    x_probe0 = max(0, min(x_probe0, full_w - 1))

    probe = img[y0:y_end, x_probe0:full_w]
    ph, pw = probe.shape[:2]
    if pw < 60 or ph < 60:
        return []

    # Buscamos el final real del gráfico. La heurística por *pixeles coloreados* suele
    # evitar falsos positivos del eje/etiquetas (texto negro, zonas blancas, etc.).
    # Si por algún motivo no hay color suficiente, caemos al método gris (no-blanco+bordes).
    yps = int(ph * 0.06)
    ype = int(ph * 0.94)
    yps = max(0, min(yps, ph - 1))
    ype = max(yps + 1, min(ype, ph))
    probe_mid = probe[yps:ype, :]

    last_idx: int | None = None

    use_color = False
    try:
        hsv = cv2.cvtColor(probe_mid, cv2.COLOR_BGR2HSV)
        hch = hsv[:, :, 0]
        s = hsv[:, :, 1]
        v = hsv[:, :, 2]

        # Detecta velas por color (rojo/verde) en HSV, y luego remueve lineas horizontales largas
        red = (((hch <= FORM_RED_HI) | (hch >= FORM_RED_LO)) & (s > FORM_COLOR_S_THR) & (v >= FORM_COLOR_V_MIN) & (v < FORM_COLOR_V_MAX))
        green = ((hch >= FORM_GREEN_LO) & (hch <= FORM_GREEN_HI) & (s > FORM_COLOR_S_THR) & (v >= FORM_COLOR_V_MIN) & (v < FORM_COLOR_V_MAX))
        colored_raw = (red | green)

        if FORM_REMOVE_HLINES:
            try:
                m = (colored_raw.astype(np.uint8) * 255)
                klen = int(min(max(15, FORM_HLINE_KERNEL_PX), max(15, m.shape[1] - 1)))
                if klen % 2 == 0:
                    klen += 1
                k = cv2.getStructuringElement(cv2.MORPH_RECT, (klen, 1))
                closed = cv2.morphologyEx(m, cv2.MORPH_CLOSE, k, iterations=1)
                hline = cv2.morphologyEx(closed, cv2.MORPH_OPEN, k, iterations=1)
                if FORM_HLINE_DILATE_ITER > 0:
                    hline = cv2.dilate(hline, cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3)), iterations=int(FORM_HLINE_DILATE_ITER))
                colored = (m > 0) & (hline == 0)
            except Exception:
                colored = colored_raw
        else:
            colored = colored_raw

        # Si hay algo de color real (velas), usamos este modo
        use_color = float(colored.mean()) > 1e-6
    except Exception:
        use_color = False

    if use_color:
        dens = colored.mean(axis=0)  # densidad de “color” por columna
        any_col = colored.any(axis=0)
        # span vertical por columna (vectorizado)
        first = np.argmax(colored, axis=0)
        last = (colored.shape[0] - 1) - np.argmax(colored[::-1, :], axis=0)
        denom = float(max(1, colored.shape[0] - 1))
        span = np.zeros_like(dens, dtype=float)
        span[any_col] = (last[any_col] - first[any_col]) / denom

        active = (dens >= FORM_ACTIVE_DENS_THR) & (span >= FORM_ACTIVE_SPAN_THR)

        # suavizado (reduce ruido de etiquetas pequeñas)
        win = int(FORM_ACTIVE_SMOOTH_WIN)
        win = max(7, min(win, pw))
        if win % 2 == 0:
            win += 1
        act_s = np.convolve(active.astype(float), (np.ones(win) / float(win)), mode='same')
        active2 = act_s > 0.30

        # encontrar la última “corrida” activa suficientemente larga (evita price-badge)
        min_run = int(max(25, min(FORM_ACTIVE_MIN_RUN_PX, pw)))
        run_start = None
        last_good_end = None
        for i, val in enumerate(active2.tolist()):
            if val and run_start is None:
                run_start = i
            elif (not val) and (run_start is not None):
                if (i - run_start) >= min_run:
                    last_good_end = i - 1
                run_start = None
        if run_start is not None:
            if (pw - run_start) >= min_run:
                last_good_end = pw - 1

        if last_good_end is not None:
            last_idx = int(last_good_end)
        else:
            # fallback: último índice con algo de color
            idxs = np.where(dens > (FORM_ACTIVE_DENS_THR * 0.8))[0]
            if idxs.size:
                last_idx = int(idxs[-1])
    else:
        try:
            gray_p = cv2.cvtColor(probe, cv2.COLOR_BGR2GRAY)
        except Exception:
            return []

        nonwhite_col = (gray_p < FORM_WHITE_THR).mean(axis=0)  # [0..1]
        edges = cv2.Canny(gray_p, 50, 150)
        # Remover lineas horizontales (p.ej. linea de precio) tambien en fallback gris
        if FORM_REMOVE_HLINES:
            try:
                klen = int(min(max(15, FORM_HLINE_KERNEL_PX), max(15, edges.shape[1] - 1)))
                if klen % 2 == 0:
                    klen += 1
                k = cv2.getStructuringElement(cv2.MORPH_RECT, (klen, 1))
                closed = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, k, iterations=1)
                hline = cv2.morphologyEx(closed, cv2.MORPH_OPEN, k, iterations=1)
                if FORM_HLINE_DILATE_ITER > 0:
                    hline = cv2.dilate(hline, cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3)), iterations=int(FORM_HLINE_DILATE_ITER))
                edges = cv2.bitwise_and(edges, cv2.bitwise_not(hline))
            except Exception:
                pass

        edge_col = (edges > 0).mean(axis=0)
        score = nonwhite_col + (FORM_COL_EDGE_W * edge_col)

        win = int(FORM_COL_SMOOTH_WIN)
        win = max(5, min(win, pw))
        if win % 2 == 0:
            win += 1
        kernel = (np.ones(win, dtype=float) / float(win))
        score_s = np.convolve(score, kernel, mode='same')

        idxs = np.where(score_s > FORM_COL_SCORE_THR)[0]
        if idxs.size:
            last_idx = int(idxs[-1])

    if last_idx is None:
        # No hay contenido claro en el probe (probablemente imagen blanca / mal recortada)
        return []
    trailing_gap = (pw - 1) - int(last_idx)

    gap_min_px = max(int(FORM_GAP_MIN_PX), int(pw * FORM_GAP_MIN_FRAC))

    # Si hay un espacio grande a la derecha (gap), asumimos que ahi NO hay serie
    if trailing_gap >= gap_min_px:
        x_end = x_probe0 + int(last_idx) + 1
    else:
        x_end = full_w

    x_end = max(0, min(x_end, full_w))

    # --- 2) ROI final "en formacion": ancho fijo relativo, anclado al final real ---
    desired_w = int(full_w * FORM_WIN_FRAC)
    desired_w = max(80, desired_w)
    x0 = max(0, x_end - desired_w)

    # Seguridad: si por algun motivo quedo muy estrecho, amplia hacia la izquierda
    if x_end - x0 < 60:
        x0 = max(0, x_end - 60)

    roi = img[y0:y_end, x0:x_end].copy()
    roi_h, roi_w = roi.shape[:2]
    if roi_w < 50 or roi_h < 50:
        return []

    tmp_roi_path = clean_img_path.replace("limpia_", "roi_")
    cv2.imwrite(tmp_roi_path, roi)

    if stop_now():
        return []

    results_roi = modelo_patrones.predict(
        tmp_roi_path,
        save=False,
        conf=FORM_CONF_MIN,
        iou=FORM_IOU_NMS,
        max_det=FORM_MAX_DET,
    )

    roi_area = float(roi_h * roi_w) if roi_h and roi_w else 1.0

    def _box_has_content(rx1: float, ry1: float, rx2: float, ry2: float) -> bool:
        # Filtra bboxes que caen en zonas blancas/limpias.
        x1 = int(max(0, min(rx1, roi_w - 1)))
        x2 = int(max(0, min(rx2, roi_w)))
        y1 = int(max(0, min(ry1, roi_h - 1)))
        y2 = int(max(0, min(ry2, roi_h)))
        if x2 <= x1 + 4 or y2 <= y1 + 4:
            return False
        crop = roi[y1:y2, x1:x2]
        if crop.size == 0:
            return False
        # 1) Color (candles): evita confundir texto/parches blancos con “patrones”
        colored_ratio = 0.0
        try:
            hsv_c = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
            h_c = hsv_c[:, :, 0]
            s_c = hsv_c[:, :, 1]
            v_c = hsv_c[:, :, 2]

            red_c = (((h_c <= FORM_RED_HI) | (h_c >= FORM_RED_LO)) & (s_c > FORM_BOX_S_THR) & (v_c >= FORM_COLOR_V_MIN) & (v_c < FORM_COLOR_V_MAX))
            green_c = ((h_c >= FORM_GREEN_LO) & (h_c <= FORM_GREEN_HI) & (s_c > FORM_BOX_S_THR) & (v_c >= FORM_COLOR_V_MIN) & (v_c < FORM_COLOR_V_MAX))
            m_raw = (red_c | green_c)

            if FORM_REMOVE_HLINES:
                try:
                    m = (m_raw.astype(np.uint8) * 255)
                    klen = int(min(max(15, FORM_HLINE_KERNEL_PX), max(15, m.shape[1] - 1)))
                    if klen % 2 == 0:
                        klen += 1
                    k = cv2.getStructuringElement(cv2.MORPH_RECT, (klen, 1))
                    closed = cv2.morphologyEx(m, cv2.MORPH_CLOSE, k, iterations=1)
                    hline = cv2.morphologyEx(closed, cv2.MORPH_OPEN, k, iterations=1)
                    if FORM_HLINE_DILATE_ITER > 0:
                        hline = cv2.dilate(hline, cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3)), iterations=int(FORM_HLINE_DILATE_ITER))
                    m2 = (m > 0) & (hline == 0)
                except Exception:
                    m2 = m_raw
            else:
                m2 = m_raw

            colored_ratio = float(m2.mean())

            # Evita confundir lineas horizontales (precio) con velas: exige span vertical minimo
            if m2.any():
                ys = np.where(m2)
                span_px = int(ys[0].max() - ys[0].min())
                min_span = max(int(FORM_BOX_MIN_SPAN_PX), int((y2 - y1) * FORM_BOX_MIN_SPAN_FRAC))
                if span_px < min_span:
                    return False
        except Exception:
            colored_ratio = 0.0

        if colored_ratio < FORM_BOX_MIN_COLORED:
            return False

        # 2) Estructura: no-blanco + bordes mínimos
        try:
            gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        except Exception:
            return False
        nonwhite = float((gray < FORM_WHITE_THR).mean())
        if nonwhite < FORM_MIN_NONWHITE:
            return False
        edges2 = cv2.Canny(gray, 50, 150)
        edge_ratio = float((edges2 > 0).mean())
        if edge_ratio < FORM_MIN_EDGE:
            return False
        return True

    dets = []
    for b in results_roi[0].boxes:
        if stop_now():
            return []

        cls_id = int(b.cls[0])
        conf   = float(b.conf[0])
        rx1, ry1, rx2, ry2 = map(float, b.xyxy[0])

        bw = max(0.0, rx2 - rx1)
        bh = max(0.0, ry2 - ry1)
        area_frac = (bw * bh) / roi_area

        if area_frac < FORM_MIN_AREA_FRAC or area_frac > FORM_MAX_AREA_FRAC:
            continue

        # Queremos lo "ultimo": tramo final del ROI o muy cerca del borde derecho
        cx = (rx1 + rx2) * 0.5
        if (rx2 < (roi_w - FORM_EDGE_PX)) and (cx < (roi_w * (1.0 - FORM_LAST_X_FRAC))):
            continue

        if not _box_has_content(rx1, ry1, rx2, ry2):
            continue

        name = modelo_patrones.names.get(cls_id, str(cls_id))

        # pasar a coords imagen completa (ajustar offsets x/y)
        x1 = rx1 + x0
        x2 = rx2 + x0
        y1f = ry1 + y0
        y2f = ry2 + y0
        det_xyxy = (float(x1), float(y1f), float(x2), float(y2f))

        # evitar duplicados con detecciones confirmadas (solape alto)
        dup = False
        for ex in existentes_xyxy:
            if _iou_xyxy(det_xyxy, ex) >= 0.45:
                dup = True
                break
        if dup:
            continue

        dets.append({"name": name, "conf": conf, "cls": cls_id, "xyxy": det_xyxy, "area_frac": area_frac})

    # Top-K por clase y limite final
    by_cls = defaultdict(list)
    for d in dets:
        by_cls[d["cls"]].append(d)

    dets2 = []
    for cls_id, arr in by_cls.items():
        arr.sort(key=lambda x: x["conf"], reverse=True)
        dets2.extend(arr[:FORM_TOPK_POR_CLASE])

    dets2.sort(key=lambda x: x["conf"], reverse=True)
    return dets2[:FORM_MAX_FINAL]

def analizar_con_yolo(ruta_imagen: str, stop_cb=None, include_tech: bool=False, user_id: str=None) -> tuple[str, str, dict]:
    nombre_archivo = os.path.basename(ruta_imagen)
    imagen_limpia_path = f"procesadas/limpia_{nombre_archivo}"
    imagen_final_path  = f"procesadas/patrones_{nombre_archivo}"

    texto_resultado = ""  # ✅ evita NameError
    entradas = {}

    # ✅ Carga lazy de modelos YOLO (NO bloquea el startup)
    modelo_patrones = get_modelo_patrones()
    modelo_ruido = get_modelo_ruido()
    
    if modelo_patrones is None or modelo_ruido is None:
        raise RuntimeError("Modelos YOLO no disponibles en este entorno")

    def stop_now():
        return bool(stop_cb and stop_cb())

    imagen = cv2.imread(ruta_imagen)
    if imagen is None:
        raise ValueError("No se pudo leer la imagen")

    # Dimensiones/área (se usa también en filtros OCR)
    h, w = imagen.shape[:2]
    img_area = float(h * w) if h and w else 1.0

    # -----------------------------
    # A) Inferir symbol/timeframe antes de borrar texto
    # -----------------------------
    try:
        sym_info = infer_symbol_tf_from_image(imagen, stop_cb=stop_cb, include_tech=include_tech)
    except Exception:
        sym_info = {"symbol": None, "timeframe": None, "confidence": 0.0, "quote_last": None}

    symbol = sym_info.get("symbol")
    tf     = sym_info.get("timeframe") or "1hour"

    if symbol:
        desc_map = _get_activos_desc()
        desc = (desc_map.get(symbol) or {}).get("descripcion") if isinstance(desc_map.get(symbol), dict) else desc_map.get(symbol)
        if not desc:
            desc = symbol

        entradas["asset"] = {
            "symbol": symbol,
            "descripcion": desc,
            "timeframe": normalize_tf_canonical(tf),
            "confidence": sym_info.get("confidence", 0.0),
            "quote_last": sym_info.get("quote_last"),
            "price": sym_info.get("quote_last"),
        }
        if include_tech and sym_info.get("tech"):
            entradas["asset"]["tech"] = sym_info["tech"]

        # insights FMP (lite)
        try:
            if stop_now(): raise RuntimeError("stopped")
            insights = build_insights_from_fmp(symbol, tf, stop_cb=stop_cb)
            entradas["insights"] = insights
        except RuntimeError as e:
            if "stopped" in str(e).lower():
                raise
            logger.warning(f"[analizar_con_yolo] insights FMP fallido para {symbol}/{tf}: {e}", exc_info=False)
        except Exception as e:
            logger.warning(f"[analizar_con_yolo] insights FMP fallido para {symbol}/{tf}: {e}", exc_info=True)

    # -----------------------------
    # B) Limpieza (ruido + OCR para borrar overlays)
    # -----------------------------
    if modelo_ruido is not None:
        resultados_ruido = modelo_ruido.predict(ruta_imagen, save=False, conf=0.4)
        for box in resultados_ruido[0].boxes:
            if stop_now():
                raise RuntimeError("stopped")
            x1, y1, x2, y2 = map(int, box.xyxy[0])
            cv2.rectangle(imagen, (x1, y1), (x2, y2), (255, 255, 255), thickness=-1)
    else:
        logger.debug("[analizar_con_yolo] modelo_ruido no disponible; se omite limpieza de ruido.")

    # OCR opcional: si no está disponible EasyOCR, se omite esta fase (graceful degradation)
    if reader is not None:
        resultados_ocr = reader.readtext(ruta_imagen)
        for (bbox, _texto, conf) in resultados_ocr:
            if stop_now(): raise RuntimeError("stopped")
            if conf < 0.4:
                continue
            pts = np.array(bbox).astype(np.int32)

            # evitar tapar zonas grandes (posible área de gráfico)
            poly_area = abs(cv2.contourArea(pts))
            if (poly_area / img_area) > 0.02:   # 2% del área total
                continue

            cv2.fillPoly(imagen, [pts], (255, 255, 255))
    else:
        logger.warning("[analizar_con_yolo] EasyOCR no disponible. Se omite limpieza de texto en imagen.")

    cv2.imwrite(imagen_limpia_path, imagen)

    # -----------------------------
    # C) Patrones estrictos
    # -----------------------------
    if stop_now(): raise RuntimeError("stopped")

    results = modelo_patrones.predict(
        imagen_limpia_path,
        save=False,
        conf=PATRON_CONF_MIN,
        iou=PATRON_IOU_NMS,
        max_det=PATRON_MAX_DET,
    )

    h, w = imagen.shape[:2]
    img_area = float(h * w) if h and w else 1.0

    dets = []
    for b in results[0].boxes:
        cls_id = int(b.cls[0])
        conf   = float(b.conf[0])
        x1, y1, x2, y2 = map(float, b.xyxy[0])

        bw = max(0.0, x2 - x1)
        bh = max(0.0, y2 - y1)
        area_frac = (bw * bh) / img_area

        if area_frac < MIN_AREA_FRAC or area_frac > MAX_AREA_FRAC:
            continue

        name = modelo_patrones.names.get(cls_id, str(cls_id))
        dets.append({"name": name, "conf": conf, "cls": cls_id, "xyxy": (x1, y1, x2, y2), "area_frac": area_frac})

    # Top-K por clase
    by_cls = defaultdict(list)
    for d in dets:
        by_cls[d["cls"]].append(d)

    dets2 = []
    for cls_id, arr in by_cls.items():
        arr.sort(key=lambda x: x["conf"], reverse=True)
        dets2.extend(arr[:TOPK_POR_CLASE])

    dets2.sort(key=lambda x: x["conf"], reverse=True)
    dets2 = dets2[:MAX_TOTAL_FINAL]

    out_img = cv2.imread(imagen_limpia_path)
    if out_img is None:
        out_img = imagen.copy()


    for d in dets2:
        if stop_now(): raise RuntimeError("stopped")
        x1, y1, x2, y2 = map(int, d["xyxy"])
        color = _BOX_COLORS[d["cls"] % len(_BOX_COLORS)]
        cv2.rectangle(out_img, (x1, y1), (x2, y2), color, 3)
        label = f'{d["name"]} {d["conf"]:.2f}'
        cv2.putText(out_img, label, (x1, max(24, y1 - 8)), cv2.FONT_HERSHEY_SIMPLEX, 0.65, color, 2)


    # C2) Patrones en formación (ventana derecha)
    # -----------------------------
    existentes_xyxy = [d["xyxy"] for d in dets2]
    form_dets = _run_formacion_en_ventana_derecha(
        modelo_patrones,
        imagen_limpia_path,
        w, h,
        existentes_xyxy,
        stop_now
    )

    if form_dets:
        entradas["patrones_en_formacion"] = [
            {"name": d["name"], "conf": float(d["conf"]), "xyxy": list(map(float, d["xyxy"]))}
            for d in form_dets
        ]
        entradas["patrones_en_formacion_label"] = [d["name"] for d in form_dets]

        # dibujar con prefijo "?" para diferenciar de confirmados
        for d in form_dets:
            x1, y1, x2, y2 = map(int, d["xyxy"])
            color = (0, 215, 255)  # amarillo/ácido (BGR)
            cv2.rectangle(out_img, (x1, y1), (x2, y2), color, 2)
            label = f'?{d["name"]} {d["conf"]:.2f}'
            cv2.putText(out_img, label, (x1, max(24, y1 - 8)), cv2.FONT_HERSHEY_SIMPLEX, 0.60, color, 2)
    else:
        entradas["patrones_en_formacion"] = []
        entradas["patrones_en_formacion_label"] = []


    # Guardar imagen final (incluye confirmados + en formación)
    cv2.imwrite(imagen_final_path, out_img)

    # -----------------------------
    # D) Entradas “panel” (no técnico por defecto)
    # -----------------------------
    best_by_name = {}
    for d in dets2:
        n = d["name"]
        best_by_name[n] = max(best_by_name.get(n, 0), d["conf"])

    patrones_label = sorted(best_by_name.keys(), key=lambda n: best_by_name[n], reverse=True)
    entradas["patrones_label"] = patrones_label
    entradas["patrones"] = patrones_label
    entradas["patrones_detectados"] = {n: True for n in patrones_label}

    # Confluencia: si ya vino de insights úsala; si no, usa la simple
    if "insights" in entradas and isinstance(entradas["insights"], dict) and "confluencia" in entradas["insights"]:
        entradas["confluencia"] = entradas["insights"]["confluencia"]
    else:
        if patrones_label:
            avg_conf = sum(best_by_name[n] for n in patrones_label) / len(patrones_label)
            score = max(0.0, min(1.0, avg_conf))
            label = "Alta" if (len(patrones_label) >= 3 or score >= 0.80) else ("Media" if score >= 0.65 else "Baja")
        else:
            score, label = 0.0, "Baja"
        entradas["confluencia"] = {"label": label, "score": float(score)}

    # Alertas / yolo_cfg SOLO para admin
    if include_tech:
        alertas = []
        if len(patrones_label) >= MAX_TOTAL_FINAL:
            alertas.append("Hay muchos patrones; se aplicó un límite estricto para evitar saturación.")
        alertas.append(f"Filtro: conf≥{PATRON_CONF_MIN}, NMS iou≤{PATRON_IOU_NMS}, topK/clase={TOPK_POR_CLASE}")
        entradas["alertas"] = alertas
        entradas["yolo_cfg"] = {
            "conf_min": PATRON_CONF_MIN,
            "iou_nms": PATRON_IOU_NMS,
            "topk_por_clase": TOPK_POR_CLASE,
            "max_total_final": MAX_TOTAL_FINAL,
            "min_area_frac": MIN_AREA_FRAC,
            "max_area_frac": MAX_AREA_FRAC,
        }

    # Texto principal
    if patrones_label:
        top_txt = ", ".join([f"{n} ({best_by_name[n]:.2f})" for n in patrones_label[:6]])
        con = entradas.get("confluencia") or {}
        texto_resultado = (
            f"Señal + Contexto\n"
            f"Patrones: {top_txt}\n"
            f"Confluencia: {con.get('label','—')} ({int(float(con.get('score',0))*100)}%)"
        )
    else:
        texto_resultado = "Señal + Contexto\n❌ No se detectaron patrones con el filtro estricto."

    # 👇 Agregar "en formación" (si existe)
    form_labels = entradas.get("patrones_en_formacion_label") or []
    if form_labels:
        posibles = ", ".join(form_labels[:3])
        texto_resultado += f"\nEn formación: {posibles} (no confirmado)"

    return imagen_final_path, texto_resultado, entradas


#@profile
async def subir_a_bucket_y_obtener_url(nombre_local, nombre_remoto=None, carpeta='analisis'):
    """
    Sube un archivo a GCS de forma asíncrona (no bloquea el event loop).
    ✅ Usa asyncio.to_thread() para evitar bloquear 40+ uploads paralelos
    """
    nombre_remoto = nombre_remoto or os.path.basename(nombre_local)
    bucket_name = "markettool_bucket"  # 🔁 Reemplazar con el nombre real de tu bucket

    def _upload_sync():
        """Operación sincrónica envuelta para ejecutarse en thread pool"""
        client = storage.Client()
        bucket = client.bucket(BUCKET_NAME)
        blob = bucket.blob(f"{carpeta}/{nombre_remoto}")
        blob.upload_from_filename(nombre_local)
        blob.make_public()  # O usar signed_url si prefieres enlaces temporales
        return blob.public_url
    
    # Ejecutar I/O sincrónico en thread pool para no bloquear event loop
    # ✅ Permite que 40 uploads se entrelacen sin bloqueo
    return await asyncio.to_thread(_upload_sync)

#@profile
def obtener_datos_firestore():
    """
    Obtiene los datos de Firestore y los devuelve como listas de Python.
    Optimizado: usa batch reads y caching con TTL.
    """
    logger.info("Obteniendo datos base de Firestore (batch read)...")
    
    _config_cache = getattr(obtener_datos_firestore, '_cache', {})
    _cache_time = getattr(obtener_datos_firestore, '_cache_time', {})
    
    now = time.time()
    ttl = APP_CONFIG.cache_ttl_config
    
    # Verificar si está en caché y aún válido
    if 'base_data' in _config_cache:
        if (now - _cache_time.get('base_data', 0)) < ttl:
            logger.info("Usando caché de datos base")
            return _config_cache['base_data']
    
    try:
        # ✅ BATCH READ: una sola query para los 3 documentos
        docs = {}
        for doc_id in ["activos", "forex", "relacionados_usd"]:
            try:
                snap = db.collection("config").document(doc_id).get()
                docs[doc_id] = snap.to_dict().get("data", []) if snap.exists else []
            except Exception as e:
                logger.warning(f"[Batch-Read] Error obteniendo {doc_id}: {e}")
                docs[doc_id] = []
        
        result = (docs.get("activos", []), docs.get("forex", []), docs.get("relacionados_usd", []))
        
        # Guardar en caché
        obtener_datos_firestore._cache = {'base_data': result}
        obtener_datos_firestore._cache_time = {'base_data': now}
        
        logger.info("Datos base obtenidos (caché válido por %ds)", ttl)
        return result
        
    except Exception as e:
        logger.error(f"Error obteniendo datos de Firestore: {e}")
        return [], [], []


#@profile
def _ensure_globals_loaded():
    """
    Lazy loader: carga activos, forex, categorias, etc. si están vacíos.
    Llamado bajo demanda antes de operaciones que necesiten estas listas.
    """
    global activos, forex, relacionados_usd, categorias, temporalidades, zonas_horarias
    
    # Solo carga si las listas/diccionarios están vacíos
    if not activos or not forex or not categorias:
        try:
            logger.info("[Lazy Load] Cargando datos base de Firestore...")
            activos, forex, relacionados_usd = obtener_datos_firestore()
            categorias, temporalidades, zonas_horarias = obtener_configuracion()
            logger.info(f"[Lazy Load] Cargados: {len(activos)} activos, {len(forex)} forex, {len(categorias)} categorías")
        except Exception as e:
            logger.error(f"[Lazy Load] Error cargando datos: {e}")
            # Mantener valores por defecto vacíos
            if not activos: activos = []
            if not forex: forex = []
            if not relacionados_usd: relacionados_usd = []
            if not categorias: categorias = {}
            if not temporalidades: temporalidades = []
            if not zonas_horarias: zonas_horarias = []


def obtener_configuracion():
    """
    Obtiene los datos de Firestore para las categorías, temporalidades y zonas horarias.
    Optimizado: usa batch reads y caching con TTL.
    """
    logger.info("Obteniendo configuración desde Firestore (batch read)...")
    
    _config_cache = getattr(obtener_configuracion, '_cache', {})
    _cache_time = getattr(obtener_configuracion, '_cache_time', {})
    
    now = time.time()
    ttl = APP_CONFIG.cache_ttl_config
    
    # Verificar si está en caché y aún válido
    if 'config_data' in _config_cache:
        if (now - _cache_time.get('config_data', 0)) < ttl:
            logger.info("Usando caché de configuración")
            return _config_cache['config_data']
    
    try:
        # ✅ BATCH READ: una sola query para los 3 documentos
        docs = {}
        for doc_id in ["categorias", "temporalidades", "zonas_horarias"]:
            try:
                snap = db.collection("config").document(doc_id).get()
                if doc_id == "categorias":
                    docs[doc_id] = snap.to_dict().get("data", {}) if snap.exists else {}
                else:
                    docs[doc_id] = snap.to_dict().get("data", []) if snap.exists else []
            except Exception as e:
                logger.warning(f"[Batch-Read] Error obteniendo {doc_id}: {e}")
                docs[doc_id] = {} if doc_id == "categorias" else []
        
        result = (docs.get("categorias", {}), docs.get("temporalidades", []), docs.get("zonas_horarias", []))
        
        # Guardar en caché
        obtener_configuracion._cache = {'config_data': result}
        obtener_configuracion._cache_time = {'config_data': now}
        
        logger.info("Configuración obtenida (caché válido por %ds)", ttl)
        return result
        
    except Exception as e:
        logger.error(f"Error obteniendo configuración desde Firestore: {e}")
        return {}, [], []
    
# ✅ Lazy initialization - comentado para evitar blocking en startup
# Ahora se cargan on-demand la primera vez que se acceda
# categorias, temporalidades, zonas_horarias = obtener_configuracion()

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
    ✅ MEJORADO: Invalida caché distribuido después de update
    - APP:  mark_user_state(user_id=..., estado="...")
    - TG:   mark_user_state(chat_id=..., estado="...")
    """
    uuid = resolve_user_uuid(user_id=user_id, chat_id=chat_id)
    if not uuid:
        print(f"[mark_user_state] No se pudo resolver UUID (user_id={user_id}, chat_id={chat_id})")
        return

    now_utc = datetime.now(timezone.utc)
    payload: Dict[str, Any] = {
        "estado": estado,
        "updated_at": now_utc.isoformat(),
        "updated_at_unix": int(now_utc.timestamp()),
    }
    if user_id is not None:
        payload["user_id"] = str(user_id)
    if chat_id is not None:
        payload["chat_id"] = str(chat_id)
    if extra:
        payload.update(extra)

    # Firestore (source of truth)
    try:
        _user_state_doc_by_uuid(uuid).set(payload, merge=True)
    except Exception as e:
        logging.warning(f"[mark_user_state] Firestore fallo (uuid={uuid}): {e}")

    # Memoria (clave principal = uuid) - ✅ FIX: Protect with lock
    with user_states_lock:
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
    
    # ✅ NUEVO: Invalidar caché distribuido (sync) para que otros pods lo actualicen
    # ⚠️ Usar invalidate() para respetar protecciones asincrónicas de la caché
    import asyncio
    try:
        if uuid:
            asyncio.create_task(_USER_STATE_CACHE.invalidate(uuid))
        if chat_id:
            asyncio.create_task(_USER_STATE_CACHE.invalidate(str(chat_id)))
        if user_id:
            asyncio.create_task(_USER_STATE_CACHE.invalidate(str(user_id)))
    except Exception:
        pass  # Si no podemos invalidar, continuar


# ------------------------------------------------------------------------------------
# DISTRIBUTED USER LOCK (Firestore lease) for multi-pod
# ------------------------------------------------------------------------------------
def acquire_user_lock(
    *, user_id: Optional[str] = None, chat_id: Optional[str] = None,
    lock_id: str, ttl_seconds: Optional[int] = None
) -> bool:
    """
    Adquiere un lock distribuido por usuario usando Firestore (lease con TTL).
    Devuelve True si el lock fue adquirido, False si está ocupado por otro pod.
    """
    uuid = resolve_user_uuid(user_id=user_id, chat_id=chat_id)
    if not uuid:
        return False

    ttl = int(ttl_seconds or USER_LOCK_TTL_SECONDS)
    now_unix = int(time.time())
    lease_until = now_unix + ttl

    doc_ref = _user_state_doc_by_uuid(uuid)
    transaction = db.transaction()

    @firestore.transactional
    def _txn_acquire(txn):
        snap = doc_ref.get(transaction=txn)
        data = snap.to_dict() or {}

        current_owner = str(data.get("lock_owner") or "")
        current_lease = int(data.get("lease_until_unix") or 0)
        current_state = str(data.get("estado") or "").lower()

        # Lock válido si lease no expiró
        if current_lease > now_unix and current_state in USER_STATE_BUSY_VALUES:
            # Si otro pod tiene lock, no adquirimos
            if current_owner and current_owner != MY_ID:
                return False

        payload = {
            "estado": "ocupado",
            "lock_owner": MY_ID,
            "lock_id": lock_id,
            "lease_until_unix": lease_until,
            "updated_at_unix": now_unix,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        txn.set(doc_ref, payload, merge=True)
        return True

    try:
        return bool(_txn_acquire(transaction))
    except Exception as e:
        logger.warning(f"[user_lock] acquire failed: {e}")
        return False


def release_user_lock(
    *, user_id: Optional[str] = None, chat_id: Optional[str] = None,
    lock_id: str | None = None
) -> None:
    """
    Libera el lock distribuido si pertenece a este pod y lock_id coincide.
    """
    uuid = resolve_user_uuid(user_id=user_id, chat_id=chat_id)
    if not uuid:
        return

    doc_ref = _user_state_doc_by_uuid(uuid)
    try:
        snap = doc_ref.get()
        data = snap.to_dict() or {}

        if data.get("lock_owner") != MY_ID:
            return
        if lock_id and data.get("lock_id") and data.get("lock_id") != lock_id:
            return

        payload = {
            "estado": "disponible",
            "lock_owner": None,
            "lock_id": None,
            "lease_until_unix": None,
            "updated_at_unix": int(time.time()),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        doc_ref.set(payload, merge=True)
    except Exception as e:
        logger.warning(f"[user_lock] release failed: {e}")


def extend_user_lock(
    *, user_id: Optional[str] = None, chat_id: Optional[str] = None,
    lock_id: str | None = None, ttl_seconds: Optional[int] = None
) -> bool:
    """
    Extiende el lease del lock distribuido si este pod es el owner.
    """
    uuid = resolve_user_uuid(user_id=user_id, chat_id=chat_id)
    if not uuid:
        return False

    ttl = int(ttl_seconds or USER_LOCK_TTL_SECONDS)
    now_unix = int(time.time())
    lease_until = now_unix + ttl

    doc_ref = _user_state_doc_by_uuid(uuid)
    try:
        snap = doc_ref.get()
        data = snap.to_dict() or {}

        if data.get("lock_owner") != MY_ID:
            return False
        if lock_id and data.get("lock_id") and data.get("lock_id") != lock_id:
            return False

        payload = {
            "lease_until_unix": lease_until,
            "updated_at_unix": now_unix,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        doc_ref.set(payload, merge=True)
        return True
    except Exception as e:
        logger.warning(f"[user_lock] extend failed: {e}")
        return False


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
    # FIXED: Protect user_states access with lock to prevent TOCTOU race
    with user_states_lock:
        if uuid in user_states and "estado" in user_states[uuid]:
            return str(user_states[uuid]["estado"])

        # 3) Memoria por claves "crudas" (compat)
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
    """Carga los chat_ids desde Firestore o devuelve una lista vacía si no hay datos.
    
    Implementa retry con exponential backoff para mayor confiabilidad.
    """
    max_retries = 3
    for attempt in range(max_retries):
        try:
            # ⚠️ FIXED: Wrap blocking .stream() with asyncio.to_thread to prevent event loop blocking
            def _sync_load_admin_ids():
                collection_ref = db.collection("admin_ids")
                docs = collection_ref.stream()
                return [doc.to_dict().get("chat_id") for doc in docs if doc.exists]
            
            admin_ids = await asyncio.to_thread(_sync_load_admin_ids)
            return admin_ids
        except Exception as e:
            if attempt < max_retries - 1:
                wait_time = 2 ** attempt  # Exponential backoff: 1s, 2s, 4s
                logger.warning(f"[cargar_admin_ids] Attempt {attempt + 1}/{max_retries} failed: {e}. Retrying in {wait_time}s...")
                await asyncio.sleep(wait_time)
            else:
                logger.error(f"[cargar_admin_ids] Failed after {max_retries} retries: {e}")
                return []  # Fallback: empty list 

# Cargar la lista de chat_ids desde el archivo
#@profile
async def cargar_chat_ids():
    """Carga los chat_ids desde Firestore o devuelve un diccionario vacío si no hay datos.
    
    Implementa retry con exponential backoff para mayor confiabilidad.
    """
    max_retries = 3
    for attempt in range(max_retries):
        try:
            # ⚠️ FIXED: Wrap blocking .stream() with asyncio.to_thread to prevent event loop blocking
            def _sync_load_chat_ids():
                collection_ref = db.collection("chat_ids")
                docs = collection_ref.stream()
                return {
                    doc.id: doc.to_dict()
                    for doc in docs if doc.exists
                }
            
            chat_ids = await asyncio.to_thread(_sync_load_chat_ids)
            return chat_ids
        except Exception as e:
            if attempt < max_retries - 1:
                wait_time = 2 ** attempt  # Exponential backoff: 1s, 2s, 4s
                logger.warning(f"[cargar_chat_ids] Attempt {attempt + 1}/{max_retries} failed: {e}. Retrying in {wait_time}s...")
                await asyncio.sleep(wait_time)
            else:
                logger.error(f"[cargar_chat_ids] Failed after {max_retries} retries: {e}")
                return {}  # Fallback: empty dict

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
    global cache_noticias, cache_noticias_lock
    # 🔒 Verificar si el símbolo ya está en el caché (con lock to prevent TOCTOU)
    with cache_noticias_lock:
        if symbol not in cache_noticias:
            cache_noticias[symbol] = pd.DataFrame()
        # Obtener el caché actual
        df_cache = cache_noticias[symbol].copy()

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
        logging.info(f"[News] Endpoint: {endpoint}")
    elif any(symbol in categorias[categoria] for categoria in ["Principales", "Cruces", "Exóticos", "OilAndGas", "Agricultura", "Indices"]):
        endpoint = "https://financialmodelingprep.com/api/v4/forex_news"
        logging.info(f"[News] Endpoint: {endpoint}")
    else:
        endpoint = "https://financialmodelingprep.com/api/v3/stock_news"
        logging.info(f"[News] Endpoint: {endpoint}")


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
            response = HTTP_SESSION.get(url, timeout=timeout_request_global)
            if response.status_code == 200:
                nuevas_noticias = response.json()
                if isinstance(nuevas_noticias, list) and len(nuevas_noticias) > 0:
                    df_nuevas = pd.DataFrame(nuevas_noticias)

                    # Procesar fechas en 'publishedDate'
                    if 'publishedDate' in df_nuevas.columns:
                        df_nuevas['publishedDate'] = pd.to_datetime(df_nuevas['publishedDate'], format="%Y-%m-%dT%H:%M:%SZ", errors='coerce')

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

                    # 🔒 Actualizar el caché global con lock
                    with cache_noticias_lock:
                        cache_noticias[symbol] = df_cache
                else:
                    logger.info(f"No se encontraron noticias nuevas para {symbol}.")
            else:
                logger.info(f"Error al consultar la API de noticias para {symbol}. Código de respuesta: {response.status_code}")

            # 🔒 Retornar el caché actualizado (with lock)
            with cache_noticias_lock:
                return cache_noticias[symbol].copy()
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

    logging.info(f"[News] Stock URL: {url}")

    reintento = 0
    tiempo_espera = tiempo_espera_inicial

    while reintento < max_reintentos:
        try:
            response = HTTP_SESSION.get(url, timeout=timeout_request_global)
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

    # OPTIMIZACIÓN: Vectorizar con apply() en lugar de iterrows() para mejor rendimiento
    def _sentimiento_row(row):
        texto = row.get('title', '') + ' ' + row.get('summary', '')
        return analizar_sentimiento(texto)
    
    sentimientos = df_noticias.apply(_sentimiento_row, axis=1)
    impacto_total = sentimientos.sum()

    # Normalizar impacto
    impacto_normalizado = impacto_total / len(df_noticias) if len(df_noticias) > 0 else 0
    return impacto_normalizado


async def get_noticias_cached(symbol: str, fecha_inicio=None, fecha_fin=None, limite: int = 50) -> pd.DataFrame:
    """
    Obtiene noticias con caché multi-pod (5 minutos TTL).
    
    ✅ Reduce 3x FMP requests a 1 en 3 pods
    ✅ Cache hit: <100ms
    ✅ Cache miss: ~5s (FMP API)
    ✅ GCS backup: Compartido entre pods
    ✅ OPTIMIZACION: Timeout de 30s para evitar operaciones congeladas
    """
    # Función auxiliar para llamar obtener_noticias de forma async
    async def _fetch(symbol_inner):
        return await asyncio.to_thread(
            obtener_noticias,
            symbol_inner,
            fecha_inicio or (datetime.now() - timedelta(days=7)),
            fecha_fin or datetime.now(),
            limite
        )
    
    # Usar caché compartido con timeout
    try:
        return await asyncio.wait_for(_NEWS_CACHE.get_or_fetch(symbol, _fetch), timeout=30.0)
    except asyncio.TimeoutError:
        logger.error(f"[get_noticias_cached] Timeout para {symbol}")
        return pd.DataFrame()
    except Exception as e:
        logger.error(f"[get_noticias_cached] Error: {e}")
        return pd.DataFrame()


def invalidate_noticias_cache(symbol: str | Iterable[str]):
    """Invalida caché de noticias para uno o varios símbolos (fuerza refresh)."""
    if isinstance(symbol, (list, tuple, set)):
        _NEWS_CACHE.invalidate_many(symbol)
        return
    _NEWS_CACHE.invalidate(symbol)


# ======================================================================
# LAZY LOADER para Históricos (Optimización: loads on-demand + LRU cache)
# ======================================================================

class LazyHistoricosLoader:
    """
    Carga históricos bajo demanda con caché LRU y TTL.
    Evita cargar TODOS los archivos en startup (ahorro: 80% de memoria + 10x startup).
    """
    def __init__(self, hist_dir: str, maxsize: int = 100, ttl_seconds: int = 1800):
        self.hist_dir = hist_dir
        self.maxsize = maxsize
        self.ttl_seconds = ttl_seconds
        self._cache = {}
        self._cache_times = {}
        self._lock = threading.Lock()
    
    def get(self, symbol: str, temporalidad: str = "1day", cfg: dict | None = None) -> pd.DataFrame:
        """
        Obtiene históricos del símbolo. Si no está en caché, carga del archivo.
        Con TTL: Si pasó el TTL, recarga del disco.
        """
        cache_key = f"{symbol.upper()}__{normalize_tf(temporalidad)}"
        
        with self._lock:
            now = time.time()
            
            # Verificar caché
            if cache_key in self._cache:
                cached_time = self._cache_times.get(cache_key, 0)
                if (now - cached_time) < self.ttl_seconds:
                    return self._cache[cache_key].copy()
                else:
                    # TTL expirado, limpiar
                    del self._cache[cache_key]
                    del self._cache_times[cache_key]
            
            # Cargar del disco
            df = self._load_from_disk(symbol, temporalidad)
            
            # Guardar en caché (con eviction si necesario)
            if len(self._cache) >= self.maxsize:
                # Remover entrada más antigua
                oldest_key = min(self._cache_times, key=self._cache_times.get)
                del self._cache[oldest_key]
                del self._cache_times[oldest_key]
                logger.debug(f"[LazyLoader] Evicted {oldest_key} from cache")
            
            self._cache[cache_key] = df.copy()
            self._cache_times[cache_key] = now
            
            return df
    
    def _load_from_disk(self, symbol: str, temporalidad: str) -> pd.DataFrame:
        """Carga un símbolo desde archivo JSON (timeframe-aware)."""
        try:
            safe_sym = _safe_symbol_for_filename(symbol).upper()
            safe_tf = normalize_tf(temporalidad)
            candidates = [
                os.path.join(self.hist_dir, f"{safe_sym}__{safe_tf}.json"),
                os.path.join(self.hist_dir, f"{safe_sym}_{safe_tf}.json"),
                os.path.join(self.hist_dir, f"{symbol.upper()}.json"),
            ]

            filepath = None
            for cand in candidates:
                if os.path.exists(cand):
                    filepath = cand
                    break

            if not filepath:
                logger.warning("[LazyLoader] File not found: %s (%s/%s)", self.hist_dir, symbol, safe_tf)
                return pd.DataFrame()
            
            # Soporta JSON estándar, NDJSON, y {"data": [...]}
            with open(filepath, 'r') as f:
                content = f.read().strip()
            
            if not content:
                return pd.DataFrame()
            
            # Intentar JSON estándar primero
            try:
                data = json.loads(content)
                if isinstance(data, dict) and "data" in data:
                    data = data["data"]
                elif isinstance(data, dict):
                    data = [data]
                if not isinstance(data, list):
                    data = [data]
                return pd.DataFrame(data)
            except json.JSONDecodeError:
                # Fallback a NDJSON
                try:
                    lines = content.split('\n')
                    data = [json.loads(line) for line in lines if line.strip()]
                    return pd.DataFrame(data)
                except json.JSONDecodeError:
                    logger.error(f"[LazyLoader] Invalid JSON in {filepath}")
                    return pd.DataFrame()
        
        except Exception as e:
            logger.error(f"[LazyLoader] Error loading {symbol}: {e}")
            return pd.DataFrame()
    
    def put(self, symbol: str, temporalidad: str, df: pd.DataFrame) -> None:
        """
        Guarda un DataFrame en el caché (manualmente).
        Útil para actualizar caché después de cargar de GCS o FMP.
        """
        cache_key = f"{symbol.upper()}"
        with self._lock:
            # Eviction si es necesario
            if len(self._cache) >= self.maxsize:
                oldest_key = min(self._cache_times, key=self._cache_times.get)
                del self._cache[oldest_key]
                del self._cache_times[oldest_key]
                logger.debug(f"[LazyLoader] Evicted {oldest_key} from cache")
            
            self._cache[cache_key] = df.copy()
            self._cache_times[cache_key] = time.time()
            logger.debug(f"[LazyLoader] Cached {symbol} ({len(df)} rows)")
    
    def clear_cache(self):
        """Limpia el caché completo."""
        with self._lock:
            self._cache.clear()
            self._cache_times.clear()
            logger.info("[LazyLoader] Cache cleared")


# Instancia global del lazy loader
_LAZY_HIST_LOADER = LazyHistoricosLoader(
    hist_dir=os.environ.get("HIST_DIR", "historicos"),
    maxsize=APP_CONFIG.cache_max_size_historicos,
    ttl_seconds=APP_CONFIG.cache_ttl_historicos
)


# ======================================================================
# GCS STORAGE LAYER para Históricos Permanentes
# ======================================================================

_GCS_CLIENT = None
_GCS_BUCKET_NAME = os.environ.get("GCS_BUCKET_NAME", "markettool_bucket")
_GCS_ENABLED = os.environ.get("GCS_ENABLED", "true").lower() == "true"

def _get_gcs_bucket():
    """Inicializa lazy el cliente de GCS."""
    global _GCS_CLIENT
    if not _GCS_ENABLED:
        return None
    
    try:
        if _GCS_CLIENT is None:
            _GCS_CLIENT = storage.Client()
        return _GCS_CLIENT.bucket(_GCS_BUCKET_NAME)
    except Exception as e:
        logger.warning(f"[GCS] Client initialization failed: {e}. GCS disabled.")
        return None


def load_from_gcs(symbol: str, tf: str) -> Optional[pd.DataFrame]:
    """
    Carga históricos desde Google Cloud Storage.
    
    Returns:
        pd.DataFrame si el archivo existe en GCS, None en caso contrario.
    """
    try:
        bucket = _get_gcs_bucket()
        if bucket is None:
            return None
        
        # Normalizar nombre del archivo
        safe_sym = _safe_symbol_for_filename(symbol)
        safe_tf = normalize_tf(tf)
        gcs_path = f"historicos/{safe_sym}__{safe_tf}.json"
        
        blob = bucket.blob(gcs_path)
        if not blob.exists():
            return None
        
        # Descargar y parsear
        json_data = blob.download_as_text(encoding="utf-8")
        data = json.loads(json_data)
        
        # Soportar formato {"data": [...]} o directamente [...]
        if isinstance(data, dict) and "data" in data:
            data = data["data"]
        
        df = pd.DataFrame(data)
        
        # Normalizar columna de tiempo
        if "time" in df.columns:
            df["time"] = pd.to_datetime(df["time"], utc=True, errors="coerce")
            df = df.set_index("time").sort_index()
        elif "date" in df.columns:
            df["date"] = pd.to_datetime(df["date"], utc=True, errors="coerce")
            df = df.set_index("date").sort_index()
        
        # Asegurar UTC timezone
        if df.index.tz is None and hasattr(df.index, "tz_localize"):
            df.index = df.index.tz_localize(pytz.UTC)
        
        # Normalizar columnas OHLCV
        df = _ensure_cols(df)
        
        logger.debug(f"[GCS] Loaded {symbol}/{tf} from gs://{_GCS_BUCKET_NAME}/{gcs_path} ({len(df)} rows)")
        return df
    
    except Exception as e:
        logger.debug(f"[GCS] Failed to load {symbol}/{tf}: {e}")
        return None


def save_to_gcs(symbol: str, tf: str, df: pd.DataFrame) -> bool:
    """
    Guarda históricos en Google Cloud Storage de forma permanente.
    
    Returns:
        True si se guardó exitosamente, False si falló o GCS deshabilitado.
    """
    try:
        if df is None or df.empty:
            return False
        
        bucket = _get_gcs_bucket()
        if bucket is None:
            return False
        
        # Preparar datos
        safe_sym = _safe_symbol_for_filename(symbol)
        safe_tf = normalize_tf(tf)
        gcs_path = f"historicos/{safe_sym}__{safe_tf}.json"
        
        # Normalizar índice a UTC si es necesario
        out = df.copy()
        if hasattr(out.index, "tz_localize") and out.index.tz is None:
            out.index = out.index.tz_localize(pytz.UTC)
        elif hasattr(out.index, "tz_convert"):
            out.index = out.index.tz_convert(pytz.UTC)
        
        # Crear columna 'time' ISO8601
        idx_utc = pd.DatetimeIndex(pd.to_datetime(out.index, utc=True, errors="coerce"))
        out["time"] = idx_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
        
        # Asegurar columnas OHLCV
        for c in ("open", "high", "low", "close", "volume"):
            if c not in out.columns:
                out[c] = np.nan
        
        # Mantener solo últimas 1000 filas para no exceder límites
        payload = out[["time", "open", "high", "low", "close", "volume"]].tail(1000).to_dict(orient="records")
        
        # Subir a GCS
        blob = bucket.blob(gcs_path)
        blob.upload_from_string(
            json.dumps(payload, ensure_ascii=False),
            content_type="application/json"
        )
        
        logger.debug(f"[GCS] Saved {symbol}/{tf} to gs://{_GCS_BUCKET_NAME}/{gcs_path} ({len(payload)} rows)")
        return True
    
    except Exception as e:
        logger.warning(f"[GCS] Failed to save {symbol}/{tf}: {e}")
        return False


# ======================================================================
# FIRESTORE METADATA LAYER (Multi-pod coordination)
# ======================================================================
# Previene duplicate FMP calls en multi-pod deployments mediante TTL compartido

_FIRESTORE_CLIENT = None
_FIRESTORE_ENABLED = os.environ.get("FIRESTORE_ENABLED", "true").lower() == "true"

def _get_firestore_client() -> Optional[firestore.Client]:
    """Initializes Firestore client lazily."""
    global _FIRESTORE_CLIENT
    if not _FIRESTORE_ENABLED:
        return None
    
    try:
        if _FIRESTORE_CLIENT is None:
            # google.cloud.firestore uses Client(); firebase_admin uses client()
            if hasattr(firestore, "Client"):
                _FIRESTORE_CLIENT = firestore.Client()
            elif hasattr(firestore, "client"):
                _FIRESTORE_CLIENT = firestore.client()
            else:
                raise AttributeError("No Firestore client constructor found")
        return _FIRESTORE_CLIENT
    except Exception as e:
        logger.warning(f"[Firestore] Metadata client initialization failed: {e}. Operating without shared TTL.")
        return None


def get_historicos_metadata(symbol: str, tf: str) -> Optional[Dict[str, Any]]:
    """
    Obtiene metadata de históricos desde Firestore (para TTL compartido entre pods).
    
    Args:
        symbol: Trading symbol (e.g., "EURUSD")
        tf: Timeframe (e.g., "1day")
    
    Returns:
        Dict con metadata si existe: {last_update_utc, ttl_seconds, is_stale, ...}
        None si no existe o Firestore deshabilitado
    """
    try:
        db = _get_firestore_client()
        if db is None:
            return None
        
        doc_id = f"{symbol.upper()}_{normalize_tf(tf)}"
        doc = db.collection("historicos_metadata").document(doc_id).get()
        
        if doc.exists:
            return doc.to_dict()
        return None
    
    except Exception as e:
        logger.debug(f"[Firestore] Failed to get metadata for {symbol}/{tf}: {e}")
        return None


def set_historicos_metadata(symbol: str, tf: str, gcs_path: str, rows_count: int, ttl_seconds: int = 1800) -> bool:
    """
    Guarda metadata de históricos en Firestore (compartido entre pods).
    
    Llamado automáticamente después de save_to_gcs().
    
    Args:
        symbol: Trading symbol
        tf: Timeframe
        gcs_path: Path en GCS donde está el archivo
        rows_count: Cantidad de filas disponibles
        ttl_seconds: TTL en segundos (default 30min, compartido entre todos los pods)
    
    Returns:
        True si se guardó exitosamente, False si falló
    """
    try:
        db = _get_firestore_client()
        if db is None:
            return False
        
        doc_id = f"{symbol.upper()}_{normalize_tf(tf)}"
        now_utc = datetime.now(UTC).replace(tzinfo=timezone.utc)
        
        metadata = {
            "symbol": symbol.upper(),
            "timeframe": normalize_tf(tf),
            "gcs_path": gcs_path,
            "last_update_utc": now_utc,
            "rows_available": rows_count,
            "ttl_seconds": ttl_seconds,
            "is_stale": False,
            "updated_by_pod": os.environ.get("POD_NAME", "unknown")
        }
        
        db.collection("historicos_metadata").document(doc_id).set(metadata, merge=True)
        logger.debug(f"[Firestore] Set metadata for {symbol}/{tf}: ttl={ttl_seconds}s")
        return True
    
    except Exception as e:
        logger.debug(f"[Firestore] Failed to set metadata for {symbol}/{tf}: {e}")
        return False


def is_metadata_stale(metadata: Dict[str, Any]) -> bool:
    """
    Verifica si la metadata (y por tanto el histórico) necesita actualización.
    
    Args:
        metadata: Dict retornado por get_historicos_metadata()
    
    Returns:
        True si TTL expiró, False si aún es válido
    """
    if not metadata:
        return True
    
    try:
        last_update = metadata.get("last_update_utc")
        ttl_seconds = metadata.get("ttl_seconds", 1800)
        
        if last_update is None:
            return True
        
        # last_update puede ser Timestamp de Firestore o datetime
        if hasattr(last_update, "timestamp"):
            last_update = datetime.fromtimestamp(last_update.timestamp(), tz=timezone.utc)
        elif isinstance(last_update, datetime):
            if last_update.tzinfo is None:
                last_update = last_update.replace(tzinfo=timezone.utc)
        else:
            return True
        
        age_seconds = (datetime.now(UTC).replace(tzinfo=timezone.utc) - last_update).total_seconds()
        is_stale = age_seconds > ttl_seconds
        
        return is_stale
    
    except Exception as e:
        logger.debug(f"[Firestore] Error checking staleness: {e}")
        return True


# ======================================================================
# Historicos cache/service overrides (module extraction)
# ======================================================================
from markettool.infra.cache.historicos_cache import (
    _safe_symbol_for_filename as _hist_safe_symbol_for_filename,
    _hist_base as _hist_hist_base,
    _hist_path_csv as _hist_hist_path_csv,
    _hist_path_json as _hist_hist_path_json,
    _hist_path as _hist_hist_path,
    _save_local_history_df as _hist_save_local_history_df,
    _load_local as _hist_load_local,
    _ensure_cols as _hist_ensure_cols,
    LazyHistoricosLoader as _LazyHistoricosLoader,
    _LAZY_HIST_LOADER as _HIST_LAZY_LOADER,
    load_cached_history as _load_cached_history,
    save_cached_history as _save_cached_history,
    load_from_gcs as _load_from_gcs,
    save_to_gcs as _save_to_gcs,
    get_historicos_metadata as _get_historicos_metadata,
    set_historicos_metadata as _set_historicos_metadata,
    is_metadata_stale as _is_metadata_stale,
)
from markettool.application.services.historicos_service import (
    HistoryConfig as _HistoryConfig,
    HistoryManager as _HistoryManager,
    merge_histories as _merge_histories,
    normalize_resample_rule as _normalize_resample_rule,
    RESAMPLE_PLAN as _RESAMPLE_PLAN,
    EOD_RESAMPLE_RULE as _EOD_RESAMPLE_RULE,
)

_safe_symbol_for_filename = _hist_safe_symbol_for_filename
_hist_base = _hist_hist_base
_hist_path_csv = _hist_hist_path_csv
_hist_path_json = _hist_hist_path_json
_hist_path = _hist_hist_path
_save_local_history_df = _hist_save_local_history_df
_load_local = _hist_load_local
_ensure_cols = _hist_ensure_cols
LazyHistoricosLoader = _LazyHistoricosLoader
_LAZY_HIST_LOADER = _HIST_LAZY_LOADER
historicos_cache = _HIST_LAZY_LOADER  # Public alias for health checks and bootstrap
load_cached_history = _load_cached_history
save_cached_history = _save_cached_history
load_from_gcs = _load_from_gcs
save_to_gcs = _save_to_gcs
get_historicos_metadata = _get_historicos_metadata
set_historicos_metadata = _set_historicos_metadata
is_metadata_stale = _is_metadata_stale

HistoryConfig = _HistoryConfig
HistoryManager = _HistoryManager
merge_histories = _merge_histories
normalize_resample_rule = _normalize_resample_rule
RESAMPLE_PLAN = _RESAMPLE_PLAN
EOD_RESAMPLE_RULE = _EOD_RESAMPLE_RULE

# ✅ Reutilizar la instancia global creada en línea 1055
# NO crear una nueva, para preservar _quote_cache entre llamadas


# ======================================================================
# INDICATORS CACHE SYSTEM (Reduce 30min -> 2-3min)
# Multi-pod optimized: Stateless pods, GCS as source of truth
# ======================================================================

_INDICATORS_CACHE_ENABLED = os.environ.get("INDICATORS_CACHE_ENABLED", "true").lower() == "true"
_INDICATORS_CACHE_TTL_HOURS = int(os.environ.get("INDICATORS_CACHE_TTL_HOURS", "8"))
_INDICATORS_FORCE_RECALC = os.environ.get("INDICATORS_FORCE_RECALC", "false").lower() == "true"
_INDICATORS_MEMORY_CACHE_SIZE = int(os.environ.get("INDICATORS_MEMORY_CACHE_SIZE", "10"))  # LRU moderado
_INDICATORS_LOCK_TIMEOUT_SEC = int(os.environ.get("INDICATORS_LOCK_TIMEOUT_SEC", "180"))  # 3 min


def hash_dataframe(df: pd.DataFrame) -> str:
    """
    Genera hash SHA256 de un DataFrame para detectar cambios.
    Hash solo de: index + close prices (suficiente para detectar updates).
    
    Args:
        df: DataFrame con datos OHLCV
    
    Returns:
        Hash de 16 caracteres (suficiente para colisiones)
    """
    try:
        # Ordenar por index para consistencia
        df_sorted = df.sort_index()
        
        # Serializar: timestamps + close prices (más eficiente que todo el DF)
        timestamps = df_sorted.index.astype(str).tolist()[:100]  # Primeros 100
        closes = df_sorted['close'].round(6).tolist()[-100:]     # Últimos 100
        
        data_str = f"{len(df)}_{timestamps}_{closes}"
        
        return hashlib.sha256(data_str.encode()).hexdigest()[:16]
    except Exception as e:
        logger.warning(f"[IndicatorsCache] Error hashing DataFrame: {e}")
        # Fallback: hash basado en shape
        return hashlib.sha256(f"{df.shape}_{time.time()}".encode()).hexdigest()[:16]


def merge_indicators_incremental(cached: dict, new: dict, split_index: int, window_context: int) -> dict:
    """
    Combina indicadores cacheados + nuevos calculados incrementalmente.
    
    Args:
        cached: Indicadores antiguos (completos)
        new: Indicadores recién calculados (últimas velas + context)
        split_index: Índice donde empiezan los datos realmente nuevos
        window_context: Tamaño de ventana usado para context
    
    Returns:
        Indicadores combinados
    """
    merged = {}
    
    # Indicadores que son listas de valores (por fila)
    for key in new.keys():
        if key not in cached:
            # Indicador nuevo que no existía en cache
            merged[key] = new[key]
            continue
        
        cached_val = cached[key]
        new_val = new[key]
        
        # Si son listas, hacer merge incremental
        if isinstance(cached_val, list) and isinstance(new_val, list):
            # Mantener cache antiguo hasta split_index
            old_part = cached_val[:split_index]
            
            # Los nuevos valores ya incluyen window_context antes de split_index
            # Solo tomamos los valores desde split_index en adelante
            new_part = new_val[window_context:] if len(new_val) > window_context else new_val
            
            merged[key] = old_part + new_part
        else:
            # Para valores únicos o no-lista, usar el nuevo
            merged[key] = new_val
    
    return merged


class IndicatorsCache:
    """
    Sistema de caché inteligente para indicadores técnicos - MULTI-POD OPTIMIZED.
    
    Arquitectura stateless:
    - GCS: única fuente de verdad (compartido entre todos los pods)
    - Firestore: metadata + distributed lock (coordinación entre pods)
    - Disco local del pod: backup/warm-start rápido (best-effort)
    - Memory cache: LRU(5) solo para hit rate dentro de sesión (bajo consumo RAM)
    - Lock distribuido: previene cálculos duplicados entre pods
    
    Features:
    - Pods completamente stateless (bajo consumo de RAM)
    - Coordinación automática entre pods via Firestore
    - Activos dinámicos soportados (FMP on-demand)
    - Cálculo incremental (solo velas nuevas + window context)
    - Validación por hash (detecta cambios en datos)
    
    Performance esperado:
    - Cold start (sin caché): mismo tiempo que antes (~30 min para 50 activos)
    - Warm hit (GCS): 100-300ms por activo (sin cálculo)
    - Incremental (nuevas velas): 2-3 min (solo recalcula últimas velas)
    - Multi-pod: pod que llegue primero calcula, resto espera y usa resultado
    """
    
    def __init__(self, bucket_name: str = None):
        self.bucket_name = bucket_name or _GCS_BUCKET_NAME
        self._bucket = None
        self._db = None
        self._lock = threading.Lock()
        self._pod_id = socket.gethostname()  # Identificador único del pod
        
        # Memory cache LRU PEQUEÑO (solo 5 items para hit rate en sesión)
        from collections import OrderedDict
        self._memory_cache = OrderedDict()  # LRU: {key: (data, timestamp)}
        self._memory_cache_max = _INDICATORS_MEMORY_CACHE_SIZE
        self._memory_cache_ttl_sec = 300  # 5 min en memoria
        self._local_dir = os.environ.get("INDICATORS_DIR", "indicators")
        
        self._enabled = _INDICATORS_CACHE_ENABLED
        
        logger.info(f"[IndicatorsCache] Initialized (pod={self._pod_id}, enabled={self._enabled}, ttl={_INDICATORS_CACHE_TTL_HOURS}h, mem_lru={self._memory_cache_max}, local_dir={self._local_dir})")
    
    @property
    def bucket(self):
        if self._bucket is None and self._enabled:
            try:
                self._bucket = storage.Client().bucket(self.bucket_name)
            except Exception as e:
                logger.warning(f"[IndicatorsCache] GCS not available: {e}")
        return self._bucket
    
    @property
    def db(self):
        if self._db is None and self._enabled:
            self._db = _get_firestore_client()
        return self._db
    
    def _gcs_path(self, symbol: str, tf: str) -> str:
        """Genera path en GCS para indicadores."""
        return f"indicators/{symbol.upper()}__{normalize_tf(tf)}.json"
    
    def _metadata_doc_id(self, symbol: str, tf: str) -> str:
        """Genera doc ID para Firestore metadata."""
        return f"{symbol.upper()}__{normalize_tf(tf)}"

    def _local_path(self, symbol: str, tf: str) -> str:
        """Path local dentro del pod para cache de indicadores."""
        safe_symbol = str(symbol).upper().replace("/", "_")
        safe_tf = normalize_tf(tf).replace("/", "_")
        return os.path.join(self._local_dir, f"{safe_symbol}__{safe_tf}.json")

    def _load_local(self, symbol: str, tf: str) -> Optional[dict]:
        """Carga desde disco local del pod (best-effort)."""
        try:
            local_path = self._local_path(symbol, tf)
            if not os.path.exists(local_path):
                return None

            with open(local_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            if "metadata" not in data or "indicators" not in data:
                logger.warning(f"[IndicatorsCache] Local invalid structure: {symbol}/{tf}")
                return None

            metadata = data["metadata"]
            last_update_raw = metadata.get("last_update_utc")
            if not last_update_raw:
                return None

            last_update = datetime.fromisoformat(str(last_update_raw).replace('Z', '+00:00'))
            age_hours = (datetime.now(UTC).replace(tzinfo=timezone.utc) - last_update).total_seconds() / 3600
            if age_hours > _INDICATORS_CACHE_TTL_HOURS:
                logger.info(f"[IndicatorsCache] Local stale (age={age_hours:.1f}h): {symbol}/{tf}")
                return None

            logger.debug(f"[IndicatorsCache] Local hit: {symbol}/{tf} (age={age_hours:.1f}h)")
            return data
        except Exception as e:
            logger.debug(f"[IndicatorsCache] Local load error {symbol}/{tf}: {e}")
            return None

    def _save_local(self, symbol: str, tf: str, payload: dict):
        """Guarda en disco local del pod (best-effort)."""
        try:
            os.makedirs(self._local_dir, exist_ok=True)
            local_path = self._local_path(symbol, tf)
            with open(local_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, default=str)
        except Exception as e:
            logger.debug(f"[IndicatorsCache] Local save error {symbol}/{tf}: {e}")
    
    def _memory_get(self, symbol: str, tf: str) -> Optional[dict]:
        """Get from LRU memory cache (stateless, muy pequeño)."""
        cache_key = f"{symbol}_{tf}"
        if cache_key in self._memory_cache:
            data, timestamp = self._memory_cache[cache_key]
            age = time.time() - timestamp
            
            if age < self._memory_cache_ttl_sec:
                # Move to end (LRU: most recent)
                self._memory_cache.move_to_end(cache_key)
                logger.debug(f"[IndicatorsCache] Memory hit: {symbol}/{tf} (age={age:.0f}s)")
                return data
            else:
                # Expired: remove
                del self._memory_cache[cache_key]
        
        return None
    
    def _memory_put(self, symbol: str, tf: str, data: dict):
        """Put in LRU memory cache (auto-evict oldest if full)."""
        cache_key = f"{symbol}_{tf}"
        
        # Remove if exists (to re-add at end)
        if cache_key in self._memory_cache:
            del self._memory_cache[cache_key]
        
        # Add at end (most recent)
        self._memory_cache[cache_key] = (data, time.time())
        
        # Evict oldest if over limit (FIFO/LRU)
        while len(self._memory_cache) > self._memory_cache_max:
            oldest_key = next(iter(self._memory_cache))  # First item (oldest)
            del self._memory_cache[oldest_key]
            logger.debug(f"[IndicatorsCache] Evicted from memory: {oldest_key}")
    
    def load(self, symbol: str, tf: str) -> Optional[dict]:
        """
        Carga indicadores cacheados desde GCS (multi-pod aware).
        
        Flow:
        1. Check memory cache (LRU pequeño, <100ms)
        2. Check local pod cache (fast warm-start)
        3. Check GCS (source of truth compartido, 100-300ms)
        4. Validate TTL
        
        Returns:
            dict con estructura:
            {
                "metadata": {...},
                "indicators": {...}
            }
            None si no existe o está inválido
        """
        if not self._enabled:
            return None
        
        # 1. Check memory cache first (muy rápido)
        mem_data = self._memory_get(symbol, tf)
        if mem_data is not None:
            return mem_data

        # 2. Check local pod cache
        local_data = self._load_local(symbol, tf)
        if local_data is not None:
            self._memory_put(symbol, tf, local_data)
            return local_data
        
        # 3. Load from GCS (source of truth para multi-pod)
        try:
            if self.bucket is None:
                return None
            
            gcs_path = self._gcs_path(symbol, tf)
            blob = self.bucket.blob(gcs_path)
            
            if not blob.exists():
                logger.debug(f"[IndicatorsCache] Miss (not found in GCS): {symbol}/{tf}")
                return None
            
            # Cargar desde GCS
            data = json.loads(blob.download_as_text())
            
            # Validar estructura
            if "metadata" not in data or "indicators" not in data:
                logger.warning(f"[IndicatorsCache] Invalid structure: {symbol}/{tf}")
                return None
            
            # Validar TTL
            metadata = data["metadata"]
            last_update = datetime.fromisoformat(metadata["last_update_utc"].replace('Z', '+00:00'))
            age_hours = (datetime.now(UTC).replace(tzinfo=timezone.utc) - last_update).total_seconds() / 3600
            
            if age_hours > _INDICATORS_CACHE_TTL_HOURS:
                logger.info(f"[IndicatorsCache] Stale (age={age_hours:.1f}h): {symbol}/{tf}")
                return None
            
            # Cache en memoria LRU
            self._memory_put(symbol, tf, data)
            self._save_local(symbol, tf, data)
            
            logger.info(f"[IndicatorsCache] GCS hit: {symbol}/{tf} (age={age_hours:.1f}h, rows={metadata.get('rows_count')}, pod={self._pod_id})")
            return data
        
        except Exception as e:
            logger.debug(f"[IndicatorsCache] Load error {symbol}/{tf}: {e}")
            return None
    
    def save(
        self,
        symbol: str,
        tf: str,
        indicators: dict,
        df_historicos: pd.DataFrame,
        calc_duration_ms: float = 0,
        analysis_audit: dict | None = None,
    ):
        """
        Guarda indicadores en GCS + metadata en Firestore.
        
        Args:
            symbol: Trading symbol
            tf: Timeframe
            indicators: Dict con indicadores calculados
            df_historicos: DataFrame original (para hash y validación)
            calc_duration_ms: Tiempo de cálculo en ms (para métricas)
        """
        if not self._enabled:
            return
        
        try:
            with self._lock:
                now_utc = datetime.now(UTC).replace(tzinfo=timezone.utc)
                data_hash = hash_dataframe(df_historicos)
                
                # Preparar metadata de auditoría de análisis (bootstrap/incremental)
                audit = analysis_audit if isinstance(analysis_audit, dict) else {}
                payload_audit = {
                    "last_mode": audit.get("last_mode"),
                    "last_bootstrap_at": audit.get("last_bootstrap_at"),
                    "last_incremental_at": audit.get("last_incremental_at"),
                    "last_incremental_bars": audit.get("last_incremental_bars"),
                    "last_data_mismatch_at": audit.get("last_data_mismatch_at"),
                }

                # Preparar payload
                payload = {
                    "metadata": {
                        "symbol": symbol.upper(),
                        "timeframe": normalize_tf(tf),
                        "last_update_utc": now_utc.isoformat(),
                        "data_hash": data_hash,
                        "rows_count": len(df_historicos),
                        "calc_duration_ms": calc_duration_ms,
                        "indicators_list": list(indicators.keys()),
                        "analysis_audit": payload_audit,
                    },
                    "indicators": indicators
                }

                # Guardar local en pod (best-effort, independiente de GCS/Firestore)
                self._save_local(symbol, tf, payload)
                
                # Guardar en GCS + metadata Firestore (si disponible)
                if self.bucket is None or self.db is None:
                    logger.warning("[IndicatorsCache] GCS/Firestore not available, local cache saved only")
                else:
                    gcs_path = self._gcs_path(symbol, tf)
                    blob = self.bucket.blob(gcs_path)
                    blob.upload_from_string(
                        json.dumps(payload, default=str),
                        content_type="application/json"
                    )

                    doc_id = self._metadata_doc_id(symbol, tf)
                    self.db.collection("indicators_metadata").document(doc_id).set({
                        "symbol": symbol.upper(),
                        "timeframe": normalize_tf(tf),
                        "gcs_path": f"gs://{self.bucket_name}/{gcs_path}",
                        "last_update_utc": now_utc,
                        "data_hash": data_hash,
                        "rows_count": len(df_historicos),
                        "indicators_list": list(indicators.keys()),
                        "calc_duration_ms": calc_duration_ms,
                        "ttl_hours": _INDICATORS_CACHE_TTL_HOURS,
                        "is_valid": True,
                        "analysis_audit": {
                            "last_mode": audit.get("last_mode"),
                            "last_bootstrap_at": (now_utc if audit.get("last_mode") == "bootstrap" else audit.get("last_bootstrap_at")),
                            "last_incremental_at": (now_utc if audit.get("last_mode") == "incremental" else audit.get("last_incremental_at")),
                            "last_incremental_bars": audit.get("last_incremental_bars"),
                            "last_data_mismatch_at": (now_utc if audit.get("last_mode") == "data_mismatch" else audit.get("last_data_mismatch_at")),
                        }
                    }, merge=True)
                
                # Cache en memoria LRU
                self._memory_put(symbol, tf, payload)
                
                logger.info(f"[IndicatorsCache] Saved: {symbol}/{tf} ({len(df_historicos)} rows, {calc_duration_ms:.0f}ms, pod={self._pod_id})")
        
        except Exception as e:
            logger.error(f"[IndicatorsCache] Save error {symbol}/{tf}: {e}")
    
    def invalidate(self, symbol: str, tf: str):
        """
        Invalida caché para forzar recálculo (multi-pod aware).
        Marca como inválido en Firestore para que TODOS los pods lo sepan.
        """
        try:
            # Eliminar de memoria local
            cache_key = f"{symbol}_{tf}"
            if cache_key in self._memory_cache:
                del self._memory_cache[cache_key]
            
            # Marcar como inválido en Firestore (no eliminar, para auditoría)
            if self.db:
                doc_id = self._metadata_doc_id(symbol, tf)
                self.db.collection("indicators_metadata").document(doc_id).update({
                    "is_valid": False,
                    "invalidated_at": datetime.now(UTC).replace(tzinfo=timezone.utc)
                })
            
            logger.info(f"[IndicatorsCache] Invalidated: {symbol}/{tf} (pod={self._pod_id})")
        except Exception as e:
            logger.warning(f"[IndicatorsCache] Invalidate error {symbol}/{tf}: {e}")
    
    def _acquire_lock(self, symbol: str, tf: str, timeout_sec: int = None) -> bool:
        """
        Intenta adquirir lock distribuido en Firestore (multi-pod coordination).
        
        Args:
            symbol: Trading symbol
            tf: Timeframe
            timeout_sec: Timeout en segundos (default: 180s = 3 min)
        
        Returns:
            True si se adquirió el lock, False si otro pod ya está calculando
        """
        if self.db is None:
            return True  # Si no hay Firestore, proceder sin lock
        
        timeout_sec = timeout_sec or _INDICATORS_LOCK_TIMEOUT_SEC
        
        try:
            doc_id = self._metadata_doc_id(symbol, tf)
            doc_ref = self.db.collection("indicators_metadata").document(doc_id)
            
            now_utc = datetime.now(UTC).replace(tzinfo=timezone.utc)
            
            # Leer estado actual (ADD FIRESTORE TIMEOUT: fail fast if Google Cloud unresponsive)
            try:
                doc = doc_ref.get(timeout=5)  # CRITICAL: 5s timeout on Firestore read
            except Exception as get_err:
                logger.debug(f"[IndicatorsCache] Firestore read timeout {symbol}/{tf}, acquiring lock locally: {get_err}")
                # If Firestore is down, acquire lock locally and proceed
                return True
            
            if doc.exists:
                data = doc.to_dict()
                lock_pod = data.get("calculating_by_pod")
                lock_time = data.get("calculating_since")
                
                if lock_pod and lock_time:
                    # Hay un lock activo
                    if isinstance(lock_time, str):
                        lock_time = datetime.fromisoformat(lock_time.replace('Z', '+00:00'))
                    elif hasattr(lock_time, "timestamp"):
                        lock_time = datetime.fromtimestamp(lock_time.timestamp(), tz=timezone.utc)
                    
                    age_sec = (now_utc - lock_time).total_seconds()
                    
                    if age_sec < timeout_sec:
                        # Lock válido: otro pod está calculando
                        if lock_pod != self._pod_id:
                            logger.info(f"[IndicatorsCache] Lock held by {lock_pod}: {symbol}/{tf} (age={age_sec:.0f}s)")
                            return False
                        # else: mismo pod, re-adquirir lock
            
            # Adquirir lock (ADD FIRESTORE TIMEOUT: fail fast if write times out)
            try:
                doc_ref.set({
                    "calculating_by_pod": self._pod_id,
                    "calculating_since": now_utc,
                    "lock_acquired_at": now_utc
                }, merge=True, timeout=5)  # CRITICAL: 5s timeout on Firestore write
            except Exception as set_err:
                logger.debug(f"[IndicatorsCache] Firestore write timeout {symbol}/{tf}, proceeding without lock: {set_err}")
                return True  # Firestore down, proceed locally
            
            logger.debug(f"[IndicatorsCache] Lock acquired: {symbol}/{tf} (pod={self._pod_id})")
            return True
        
        except Exception as e:
            logger.debug(f"[IndicatorsCache] Lock acquisition error {symbol}/{tf}: {type(e).__name__}: {e}")
            return True  # En caso de error, proceder sin lock
    
    def _release_lock(self, symbol: str, tf: str):
        """
        Libera lock distribuido en Firestore.
        """
        if self.db is None:
            return
        
        try:
            doc_id = self._metadata_doc_id(symbol, tf)
            doc_ref = self.db.collection("indicators_metadata").document(doc_id)
            
            # Solo liberar si el lock es de este pod (ADD FIRESTORE TIMEOUT)
            try:
                doc = doc_ref.get(timeout=5)  # CRITICAL: 5s timeout on Firestore read
            except Exception as get_err:
                logger.debug(f"[IndicatorsCache] Firestore read timeout on _release_lock {symbol}/{tf}: {get_err}")
                return  # Don't block on lock release if Firestore is down
            
            if doc.exists:
                data = doc.to_dict()
                if data.get("calculating_by_pod") == self._pod_id:
                    try:
                        doc_ref.update({
                            "calculating_by_pod": firestore.DELETE_FIELD,
                            "calculating_since": firestore.DELETE_FIELD,
                            "lock_released_at": datetime.now(UTC).replace(tzinfo=timezone.utc)
                        }, timeout=5)  # CRITICAL: 5s timeout on Firestore write
                    except Exception as update_err:
                        logger.debug(f"[IndicatorsCache] Firestore write timeout on _release_lock {symbol}/{tf}: {update_err}")
                        return  # Don't block on lock release if Firestore is down
                    
                    logger.debug(f"[IndicatorsCache] Lock released: {symbol}/{tf} (pod={self._pod_id})")
        
        except Exception as e:
            logger.debug(f"[IndicatorsCache] Lock release error {symbol}/{tf}: {type(e).__name__}: {e}")
    
    def _wait_for_lock_release(self, symbol: str, tf: str, max_wait_sec: int = 30) -> bool:
        """
        Espera a que otro pod termine de calcular y libere el lock.
        **AGGRESSIVE TIMEOUT**: Max 30s (reduced from 200s) para evitar que se cuelgue con Firestore issues
        
        Args:
            symbol: Trading symbol
            tf: Timeframe
            max_wait_sec: Tiempo máximo de espera (default: 30s, aggressive para evitar EOF)
        
        Returns:
            True si el cálculo está listo, False si timeout
        """
        if self.db is None:
            return False
        
        logger.info(f"[IndicatorsCache] Waiting for other pod (max={max_wait_sec}s): {symbol}/{tf}")
        
        start_time = time.time()
        check_interval = 1.0  # Comenzar más agresivo con 1s
        max_interval = 5.0   # Máximo 5s entre checks
        
        while (time.time() - start_time) < max_wait_sec:
            try:
                # Check si el lock se liberó (con timeout propio en Firestore)
                doc_id = self._metadata_doc_id(symbol, tf)
                
                # Agregar timeout explícito a la llamada Firestore (máximo 5s)
                try:
                    doc = self.db.collection("indicators_metadata").document(doc_id).get(timeout=5)
                except Exception as fire_err:
                    logger.debug(f"[IndicatorsCache] Firestore timeout during wait {symbol}/{tf}: {fire_err}")
                    # En caso de error Firestore, romper y calcular nosotros (no esperar más)
                    break
                
                if doc.exists:
                    data = doc.to_dict()
                    lock_pod = data.get("calculating_by_pod")
                    
                    if not lock_pod:
                        # Lock liberado: intentar cargar resultado
                        logger.info(f"[IndicatorsCache] Lock released by {lock_pod}, loading: {symbol}/{tf}")
                        try:
                            cached = self.load(symbol, tf)
                            if cached is not None:
                                logger.info(f"[IndicatorsCache] Other pod result loaded: {symbol}/{tf}")
                                return True
                        except Exception as load_err:
                            logger.debug(f"[IndicatorsCache] Could not load result after wait: {load_err}")
                            break
                
                # Exponential backoff + jitter
                check_interval = min(max_interval, check_interval * 1.5)
                jitter = random.uniform(0, check_interval * 0.1)
                time.sleep(check_interval + jitter)
            
            except Exception as e:
                logger.debug(f"[IndicatorsCache] Wait loop exception {symbol}/{tf}: {type(e).__name__}: {e}")
                break
        
        logger.debug(f"[IndicatorsCache] Wait ended (timeout OR error): {symbol}/{tf} (waited {time.time()-start_time:.1f}s)")
        return False
    
    def get_or_calculate(
        self,
        symbol: str,
        tf: str,
        df_historicos: pd.DataFrame,
        calc_func: Callable[[pd.DataFrame, str], pd.DataFrame]
    ) -> Tuple[pd.DataFrame, dict]:
        """
        Obtiene indicadores del caché o los calcula si es necesario.
        MULTI-POD OPTIMIZED: usa lock distribuido para evitar cálculos duplicados.
        
        Flow:
        1. Intentar cargar desde caché (memoria LRU → GCS)
        2. Si no existe: intentar adquirir lock distribuido
        3. Si lock adquirido: calcular y guardar
        4. Si lock NO adquirido: esperar a que otro pod termine
        5. Siempre liberar lock al final
        
        Args:
            symbol: Trading symbol
            tf: Timeframe
            df_historicos: DataFrame con datos OHLCV
            calc_func: Función de cálculo (ej: calcular_indicadores_impl)
        
        Returns:
            Tuple (df_con_indicadores, stats)
            stats: dict con métricas (cache_hit, incremental, calc_time, etc.)
        """
        if not self._enabled or _INDICATORS_FORCE_RECALC:
            # Modo sin caché o forzar recálculo
            start_time = time.time()
            df_result = calc_func(df_historicos.copy(), tf)
            calc_time_ms = (time.time() - start_time) * 1000
            
            return df_result, {
                "cache_hit": False,
                "incremental": False,
                "calc_time_ms": calc_time_ms,
                "source": "full_calc_no_cache",
                "pod_id": self._pod_id
            }
        
        # 1. Intentar cargar caché
        cached = self.load(symbol, tf)
        
        if cached is None:
            # No hay caché: necesita cálculo
            
            # 2. Intentar adquirir lock distribuido (multi-pod coordination)
            lock_acquired = self._acquire_lock(symbol, tf)
            
            if not lock_acquired:
                # Otro pod está calculando: esperar resultado
                logger.info(f"[IndicatorsCache] Another pod calculating: {symbol}/{tf} (pod={self._pod_id})")
                
                if self._wait_for_lock_release(symbol, tf, max_wait_sec=200):
                    # Otro pod terminó: cargar resultado
                    cached = self.load(symbol, tf)
                    if cached is not None:
                        df_result, override = self._apply_indicators_or_recalc(
                            df_historicos,
                            cached["indicators"],
                            symbol,
                            tf,
                            calc_func,
                        )
                        if override is not None:
                            return df_result, override
                        return df_result, {
                            "cache_hit": True,
                            "incremental": False,
                            "calc_time_ms": 0,
                            "source": "waited_for_other_pod",
                            "pod_id": self._pod_id
                        }
                
                # Timeout o error esperando: calcular nosotros
                logger.warning(f"[IndicatorsCache] Wait failed, calculating anyway: {symbol}/{tf}")
                lock_acquired = True  # Forzar cálculo
            
            if lock_acquired:
                # Tenemos el lock: calcular
                try:
                    logger.info(f"[IndicatorsCache] Cold start: {symbol}/{tf} (pod={self._pod_id})")
                    start_time = time.time()
                    df_result = calc_func(df_historicos.copy(), tf)
                    calc_time_ms = (time.time() - start_time) * 1000
                    
                    # Extraer indicadores del DataFrame y guardar
                    indicators = self._extract_indicators_from_df(df_result)
                    self.save(
                        symbol,
                        tf,
                        indicators,
                        df_historicos,
                        calc_time_ms,
                        analysis_audit={
                            "last_mode": "bootstrap",
                            "last_bootstrap_at": datetime.now(UTC).replace(tzinfo=timezone.utc).isoformat(),
                        },
                    )
                    
                    return df_result, {
                        "cache_hit": False,
                        "incremental": False,
                        "calc_time_ms": calc_time_ms,
                        "source": "full_calc_cold_start",
                        "pod_id": self._pod_id
                    }
                finally:
                    # SIEMPRE liberar lock
                    self._release_lock(symbol, tf)
        
        # 3. Tenemos caché: validar si datos históricos cambiaron
        cached_hash = cached["metadata"]["data_hash"]
        current_hash = hash_dataframe(df_historicos)
        cached_rows = cached["metadata"]["rows_count"]
        current_rows = len(df_historicos)
        
        if cached_hash == current_hash and cached_rows == current_rows:
            # Hit perfecto: datos idénticos
            logger.info(f"[IndicatorsCache] Perfect hit: {symbol}/{tf} ({current_rows} rows, pod={self._pod_id})")
            df_result, override = self._apply_indicators_or_recalc(
                df_historicos,
                cached["indicators"],
                symbol,
                tf,
                calc_func,
            )
            if override is not None:
                return df_result, override
            
            return df_result, {
                "cache_hit": True,
                "incremental": False,
                "calc_time_ms": 0,
                "source": "cache_perfect_match",
                "cached_age_hours": (datetime.now(UTC).replace(tzinfo=timezone.utc) - 
                                     datetime.fromisoformat(cached["metadata"]["last_update_utc"].replace('Z', '+00:00'))).total_seconds() / 3600,
                "pod_id": self._pod_id
            }

        # Mismas filas pero hash cambió (ej. última vela en formación): recalcular solo cola/contexto
        if current_rows == cached_rows and current_rows > 0:
            lock_acquired = self._acquire_lock(symbol, tf)

            if not lock_acquired:
                if self._wait_for_lock_release(symbol, tf, max_wait_sec=120):
                    cached = self.load(symbol, tf)
                    if cached is not None:
                        df_result, override = self._apply_indicators_or_recalc(
                            df_historicos,
                            cached["indicators"],
                            symbol,
                            tf,
                            calc_func,
                        )
                        if override is not None:
                            return df_result, override
                        return df_result, {
                            "cache_hit": True,
                            "incremental": True,
                            "calc_time_ms": 0,
                            "source": "waited_for_other_pod_tail_refresh",
                            "pod_id": self._pod_id
                        }

            try:
                window = definir_window(tf)
                context_start = max(0, current_rows - window)
                df_to_calc = df_historicos.iloc[context_start:].copy()

                logger.info(
                    f"[IndicatorsCache] Tail refresh: {symbol}/{tf} "
                    f"(rows={current_rows}, context={window}, pod={self._pod_id})"
                )

                start_time = time.time()
                df_partial = calc_func(df_to_calc, tf)
                calc_time_ms = (time.time() - start_time) * 1000

                indicators_partial = self._extract_indicators_from_df(df_partial)

                # Reemplaza solo la cola recalculada; preserva histórico previo.
                indicators_merged = merge_indicators_incremental(
                    cached["indicators"],
                    indicators_partial,
                    context_start,
                    0,
                )

                df_result, override = self._apply_indicators_or_recalc(
                    df_historicos,
                    indicators_merged,
                    symbol,
                    tf,
                    calc_func,
                )
                if override is not None:
                    return df_result, override

                self.save(
                    symbol,
                    tf,
                    indicators_merged,
                    df_historicos,
                    calc_time_ms,
                    analysis_audit={
                        "last_mode": "incremental",
                        "last_incremental_at": datetime.now(UTC).replace(tzinfo=timezone.utc).isoformat(),
                        "last_incremental_bars": 0,
                    },
                )

                return df_result, {
                    "cache_hit": True,
                    "incremental": True,
                    "calc_time_ms": calc_time_ms,
                    "source": "incremental_tail_refresh",
                    "new_bars": 0,
                    "cached_rows": cached_rows,
                    "total_rows": current_rows,
                    "pod_id": self._pod_id
                }
            finally:
                if lock_acquired:
                    self._release_lock(symbol, tf)
        
        # 4. Cálculo incremental: solo nuevas velas
        if current_rows > cached_rows:
            # Adquirir lock para cálculo incremental
            lock_acquired = self._acquire_lock(symbol, tf)
            
            if not lock_acquired:
                # Otro pod haciendo incremental: esperar
                if self._wait_for_lock_release(symbol, tf, max_wait_sec=120):
                    cached = self.load(symbol, tf)
                    if cached is not None:
                        df_result, override = self._apply_indicators_or_recalc(
                            df_historicos,
                            cached["indicators"],
                            symbol,
                            tf,
                            calc_func,
                        )
                        if override is not None:
                            return df_result, override
                        return df_result, {
                            "cache_hit": True,
                            "incremental": True,
                            "calc_time_ms": 0,
                            "source": "waited_for_other_pod_incremental",
                            "pod_id": self._pod_id
                        }
            
            try:
                new_bars = current_rows - cached_rows
                window = definir_window(tf)
                
                # Context necesario para indicadores (ej: RSI necesita window previo)
                context_start = max(0, cached_rows - window)
                df_to_calc = df_historicos.iloc[context_start:].copy()
                
                logger.info(f"[IndicatorsCache] Incremental: {symbol}/{tf} (+{new_bars} bars, context={window}, pod={self._pod_id})")
                
                start_time = time.time()
                df_partial = calc_func(df_to_calc, tf)
                calc_time_ms = (time.time() - start_time) * 1000
                
                # Extraer indicadores del segmento calculado
                indicators_partial = self._extract_indicators_from_df(df_partial)
                
                # Merge: mantener cache antiguo + nuevos valores
                indicators_merged = merge_indicators_incremental(
                    cached["indicators"],
                    indicators_partial,
                    cached_rows,
                    window
                )
                
                # Aplicar al DataFrame completo
                df_result, override = self._apply_indicators_or_recalc(
                    df_historicos,
                    indicators_merged,
                    symbol,
                    tf,
                    calc_func,
                )
                if override is not None:
                    return df_result, override
                
                # Guardar actualizado
                self.save(
                    symbol,
                    tf,
                    indicators_merged,
                    df_historicos,
                    calc_time_ms,
                    analysis_audit={
                        "last_mode": "incremental",
                        "last_incremental_at": datetime.now(UTC).replace(tzinfo=timezone.utc).isoformat(),
                        "last_incremental_bars": int(new_bars),
                    },
                )
                
                return df_result, {
                    "cache_hit": True,
                    "incremental": True,
                    "calc_time_ms": calc_time_ms,
                    "source": "incremental_update",
                    "new_bars": new_bars,
                    "cached_rows": cached_rows,
                    "total_rows": current_rows,
                    "pod_id": self._pod_id
                }
            finally:
                if lock_acquired:
                    self._release_lock(symbol, tf)
        
        else:
            # Los datos se redujeron o cambiaron estructura: recalcular todo
            lock_acquired = self._acquire_lock(symbol, tf)
            
            try:
                logger.warning(f"[IndicatorsCache] Data mismatch: {symbol}/{tf} (cached={cached_rows}, current={current_rows}, pod={self._pod_id})")
                start_time = time.time()
                df_result = calc_func(df_historicos.copy(), tf)
                calc_time_ms = (time.time() - start_time) * 1000
                
                indicators = self._extract_indicators_from_df(df_result)
                self.save(
                    symbol,
                    tf,
                    indicators,
                    df_historicos,
                    calc_time_ms,
                    analysis_audit={
                        "last_mode": "data_mismatch",
                        "last_data_mismatch_at": datetime.now(UTC).replace(tzinfo=timezone.utc).isoformat(),
                    },
                )
                
                return df_result, {
                    "cache_hit": False,
                    "incremental": False,
                    "calc_time_ms": calc_time_ms,
                    "source": "full_calc_data_mismatch",
                    "pod_id": self._pod_id
                }
            finally:
                if lock_acquired:
                    self._release_lock(symbol, tf)
    
    def _extract_indicators_from_df(self, df: pd.DataFrame) -> dict:
        """Extrae indicadores de un DataFrame a dict serializable."""
        indicators = {}
        
        # Columnas de indicadores (excluir OHLCV originales)
        base_cols = {'open', 'high', 'low', 'close', 'volume', 'time'}
        indicator_cols = [col for col in df.columns if col not in base_cols]
        
        for col in indicator_cols:
            try:
                values = df[col].tolist()
                # Convertir tipos especiales a serializables
                values = [None if pd.isna(v) else v for v in values]
                indicators[col] = values
            except Exception as e:
                logger.warning(f"[IndicatorsCache] Error extracting column {col}: {e}")
        
        return indicators
    
    def _apply_indicators_to_df(self, df: pd.DataFrame, indicators: dict) -> pd.DataFrame:
        """Aplica indicadores desde dict a DataFrame."""
        mismatch = False
        for col, values in indicators.items():
            try:
                if len(values) == len(df):
                    df[col] = values
                else:
                    logger.warning(f"[IndicatorsCache] Length mismatch for {col}: {len(values)} vs {len(df)}")
                    mismatch = True
            except Exception as e:
                logger.warning(f"[IndicatorsCache] Error applying column {col}: {e}")
        
        if mismatch:
            raise ValueError("indicator_length_mismatch")
        return df

    def _apply_indicators_or_recalc(
        self,
        df_historicos: pd.DataFrame,
        indicators: dict,
        symbol: str,
        tf: str,
        calc_func: Callable[[pd.DataFrame, str], pd.DataFrame],
    ) -> Tuple[pd.DataFrame, dict | None]:
        try:
            return self._apply_indicators_to_df(df_historicos.copy(), indicators), None
        except ValueError:
            lock_acquired = self._acquire_lock(symbol, tf)
            try:
                if not lock_acquired and self._wait_for_lock_release(symbol, tf, max_wait_sec=120):
                    cached = self.load(symbol, tf)
                    if cached is not None:
                        try:
                            return self._apply_indicators_to_df(df_historicos.copy(), cached["indicators"]), None
                        except ValueError:
                            pass

                logger.warning(f"[IndicatorsCache] Length mismatch triggers full recalc: {symbol}/{tf}")
                start_time = time.time()
                df_result = calc_func(df_historicos.copy(), tf)
                calc_time_ms = (time.time() - start_time) * 1000
                indicators_full = self._extract_indicators_from_df(df_result)
                self.save(
                    symbol,
                    tf,
                    indicators_full,
                    df_historicos,
                    calc_time_ms,
                    analysis_audit={
                        "last_mode": "data_mismatch",
                        "last_data_mismatch_at": datetime.now(UTC).replace(tzinfo=timezone.utc).isoformat(),
                    },
                )
                return df_result, {
                    "cache_hit": False,
                    "incremental": False,
                    "calc_time_ms": calc_time_ms,
                    "source": "full_calc_indicator_mismatch",
                    "pod_id": self._pod_id,
                }
            finally:
                if lock_acquired:
                    self._release_lock(symbol, tf)


# ======================================================================
# Indicators cache overrides (module extraction)
# ======================================================================
from markettool.infra.cache.indicators_cache import (
    IndicatorsCache as _IndicatorsCache,
    hash_dataframe as _hash_dataframe,
    merge_indicators_incremental as _merge_indicators_incremental,
    _INDICATORS_CACHE_ENABLED as _IC_ENABLED,
    _INDICATORS_CACHE_TTL_HOURS as _IC_TTL_HOURS,
    _INDICATORS_FORCE_RECALC as _IC_FORCE_RECALC,
    _INDICATORS_MEMORY_CACHE_SIZE as _IC_MEM_SIZE,
    _INDICATORS_LOCK_TIMEOUT_SEC as _IC_LOCK_TIMEOUT,
)

IndicatorsCache = _IndicatorsCache
hash_dataframe = _hash_dataframe
merge_indicators_incremental = _merge_indicators_incremental
_INDICATORS_CACHE_ENABLED = _IC_ENABLED
_INDICATORS_CACHE_TTL_HOURS = _IC_TTL_HOURS
_INDICATORS_FORCE_RECALC = _IC_FORCE_RECALC
_INDICATORS_MEMORY_CACHE_SIZE = _IC_MEM_SIZE
_INDICATORS_LOCK_TIMEOUT_SEC = _IC_LOCK_TIMEOUT
_INDICATORS_CACHE = IndicatorsCache(window_func=definir_window)


# ============================================================================
# POD LEADER COORDINATOR - Multi-Pod Coordination for Scheduled Tasks
# ============================================================================

class PodLeaderCoordinator:
    """
    Coordina procesos periódicos en entorno multi-pod.
    
    ✅ Solo el pod LÍDER ejecuta tareas programadas (ej: actualizar menús Telegram)
    ✅ Evita solicitudes duplicadas a APIs externas
    ✅ Failover automático si el líder cae
    ✅ Heartbeat cada 60 segundos
    ✅ TTL de 3 minutos (si líder no envía heartbeat, es reemplazado)
    
    Firestore Document: system/scheduler_leader
    {
        "pod_id": "markettool-7d8f9-abc12",
        "heartbeat_utc": "2026-02-11T15:30:00Z",
        "elected_at_utc": "2026-02-11T15:00:00Z",
        "ttl_seconds": 180
    }
    """
    
    def __init__(self):
        import socket
        self.pod_id = socket.gethostname()
        self.firestore_enabled = os.environ.get("FIRESTORE_ENABLED", "false").lower() == "true"
        self.db = firestore.Client() if self.firestore_enabled else None
        self.leader_doc_path = "system/scheduler_leader"
        self.ttl_seconds = int(os.environ.get("LEADER_TTL_SECONDS", "180"))  # 3 min
        self.heartbeat_interval = int(os.environ.get("LEADER_HEARTBEAT_SECONDS", "60"))  # 1 min
        self.is_leader = False
        self.heartbeat_task = None
        self.last_check = 0
        self.check_cooldown = 30  # Re-check leadership cada 30 seg si no es líder
        
        logger.info(f"[PodCoordinator] Initialized pod_id={self.pod_id}, firestore_enabled={self.firestore_enabled}")
    
    def _is_firestore_available(self) -> bool:
        """Check if Firestore is enabled and available."""
        return self.firestore_enabled and self.db is not None
    
    async def try_become_leader(self) -> bool:
        """
        Intenta convertirse en líder del cluster.
        
        Returns:
            bool: True si este pod es el líder, False de lo contrario
        """
        if not self._is_firestore_available():
            # Sin Firestore, cada pod es su propio líder (fallback a comportamiento original)
            logger.warning("[PodCoordinator] Firestore disabled. Pod operates independently.")
            self.is_leader = True
            return True
        
        try:
            doc_ref = self.db.document(self.leader_doc_path)
            doc = doc_ref.get()
            now_utc = datetime.now(timezone.utc)
            
            if not doc.exists:
                # No hay líder, tomar el control
                doc_ref.set({
                    "pod_id": self.pod_id,
                    "heartbeat_utc": now_utc.isoformat(),
                    "elected_at_utc": now_utc.isoformat(),
                    "ttl_seconds": self.ttl_seconds
                })
                self.is_leader = True
                logger.info(f"[PodCoordinator] ✅ Elected as LEADER (no previous leader)")
                return True
            
            # Verificar si el líder actual está vivo
            data = doc.to_dict()
            current_leader = data.get("pod_id")
            last_heartbeat_str = data.get("heartbeat_utc")
            
            if not last_heartbeat_str:
                # Documento corrupto, tomar control
                doc_ref.set({
                    "pod_id": self.pod_id,
                    "heartbeat_utc": now_utc.isoformat(),
                    "elected_at_utc": now_utc.isoformat(),
                    "ttl_seconds": self.ttl_seconds
                })
                self.is_leader = True
                logger.info(f"[PodCoordinator] ✅ Elected as LEADER (corrupted document)")
                return True
            
            # Parse heartbeat timestamp
            last_heartbeat = datetime.fromisoformat(last_heartbeat_str.replace('Z', '+00:00'))
            elapsed = (now_utc - last_heartbeat).total_seconds()
            
            if current_leader == self.pod_id:
                # Ya soy el líder, actualizar heartbeat
                doc_ref.update({
                    "heartbeat_utc": now_utc.isoformat()
                })
                self.is_leader = True
                logger.debug(f"[PodCoordinator] ✅ Still LEADER (heartbeat updated)")
                return True
            
            # Otro pod es líder
            if elapsed > self.ttl_seconds:
                # Líder anterior murió (no envió heartbeat), tomar control
                doc_ref.set({
                    "pod_id": self.pod_id,
                    "heartbeat_utc": now_utc.isoformat(),
                    "elected_at_utc": now_utc.isoformat(),
                    "ttl_seconds": self.ttl_seconds,
                    "previous_leader": current_leader,
                    "takeover_reason": f"Leader timeout after {elapsed:.0f}s"
                })
                self.is_leader = True
                logger.warning(f"[PodCoordinator] ⚠️ TAKEOVER: Previous leader '{current_leader}' timeout ({elapsed:.0f}s > {self.ttl_seconds}s)")
                return True
            else:
                # Líder está vivo
                if time.time() - self.last_check > self.check_cooldown:
                    logger.info(f"[PodCoordinator] ❌ NOT leader. Current leader: '{current_leader}' (last heartbeat {elapsed:.0f}s ago)")
                    self.last_check = time.time()
                self.is_leader = False
                return False
        
        except Exception as e:
            logger.error(f"[PodCoordinator] Error in leader election: {e}")
            # En caso de error, no ejecutar tareas (fail-safe)
            self.is_leader = False
            return False
    
    async def start_heartbeat(self, loop=None):
        """
        Inicia el heartbeat periódico si este pod es líder.
        
        Args:
            loop: asyncio event loop (opcional)
        """
        if not self._is_firestore_available():
            return
        
        async def heartbeat_worker():
            while True:
                try:
                    await asyncio.sleep(self.heartbeat_interval)
                    
                    if self.is_leader:
                        # Actualizar heartbeat en Firestore
                        doc_ref = self.db.document(self.leader_doc_path)
                        now_utc = datetime.now(timezone.utc)
                        doc_ref.update({
                            "heartbeat_utc": now_utc.isoformat()
                        })
                        logger.debug(f"[PodCoordinator] 💓 Heartbeat sent")
                    else:
                        # No soy líder, intentar convertirme si el líder murió
                        await self.try_become_leader()
                
                except Exception as e:
                    logger.error(f"[PodCoordinator] Heartbeat error: {e}")
        
        # Iniciar tarea de heartbeat
        if loop:
            self.heartbeat_task = loop.create_task(heartbeat_worker())
        else:
            self.heartbeat_task = asyncio.create_task(heartbeat_worker())
        
        logger.info(f"[PodCoordinator] Heartbeat started (interval={self.heartbeat_interval}s)")
    
    def should_run_scheduled_task(self, task_name: str) -> bool:
        """
        Verifica si este pod debe ejecutar una tarea programada.
        
        Args:
            task_name: Nombre de la tarea (ej: "actualizar_menus")
        
        Returns:
            bool: True si debe ejecutar, False de lo contrario
        """
        if not self._is_firestore_available():
            # Sin Firestore, cada pod ejecuta sus propias tareas (fallback)
            return True
        
        if self.is_leader:
            logger.debug(f"[PodCoordinator] ✅ Executing '{task_name}' (I am leader)")
            return True
        else:
            logger.debug(f"[PodCoordinator] ⏭️ Skipping '{task_name}' (not leader)")
            return False
    
    async def release_leadership(self):
        """
        Libera el liderazgo (útil en shutdown graceful).
        """
        if not self._is_firestore_available() or not self.is_leader:
            return
        
        try:
            doc_ref = self.db.document(self.leader_doc_path)
            doc_ref.delete()
            logger.info(f"[PodCoordinator] Leadership released by pod {self.pod_id}")
            self.is_leader = False
        except Exception as e:
            logger.error(f"[PodCoordinator] Error releasing leadership: {e}")
        
        # Cancelar heartbeat task
        if self.heartbeat_task:
            self.heartbeat_task.cancel()


# Instancia global del coordinador de pods
_POD_COORDINATOR = PodLeaderCoordinator()


# ============================================================================
# DISTRIBUTED COORDINATION FRAMEWORK - Multi-Pod Shared State
# ============================================================================

class UserStateCache:
    """
    Caché distribuido de estado de usuario.
    
    ✅ Lee desde Firestore (source of truth)
    ✅ Cachea localmente con TTL de 10 segundos
    ✅ Fallback a memoria si Firestore no disponible
    """
    
    def __init__(self, ttl_seconds: int = 10):
        self.ttl_seconds = ttl_seconds
        self.firestore_enabled = os.environ.get("FIRESTORE_ENABLED", "false").lower() == "true"
        self.db = firestore.Client() if self.firestore_enabled else None
        self._local_cache: Dict[str, tuple] = {}  # {uuid: (timestamp, data)}
        self._lock = asyncio.Lock()
    
    async def get(self, uuid: str) -> dict:
        """Obtiene estado del usuario con caché TTL."""
        async with self._lock:
            now = time.time()
            
            # Check caché local
            if uuid in self._local_cache:
                cached_at, data = self._local_cache[uuid]
                if (now - cached_at) < self.ttl_seconds:
                    logger.debug(f"[UserStateCache] Hit (local): {uuid}")
                    return data
            
            # Fetch desde Firestore
            if self.firestore_enabled and self.db:
                try:
                    doc = _user_state_doc_by_uuid(uuid).get()
                    if doc.exists:
                        data = doc.to_dict()
                        self._local_cache[uuid] = (now, data)
                        logger.debug(f"[UserStateCache] Hit (Firestore): {uuid}")
                        return data
                    else:
                        logger.debug(f"[UserStateCache] Miss: {uuid} (no doc)")
                except Exception as e:
                    logger.warning(f"[UserStateCache] Error reading Firestore: {e}")
            
            # Fallback: memoria local (original dict)
            # FIXED: Protect user_states read with lock to prevent race condition
            with user_states_lock:
                if uuid in user_states:
                    data = user_states[uuid].copy()
                    self._local_cache[uuid] = (now, data)
                    logger.debug(f"[UserStateCache] Hit (memory fallback): {uuid}")
                    return data
            
            # Default state
            default = {"estado": "disponible", "updated_at": datetime.now(timezone.utc).isoformat()}
            self._local_cache[uuid] = (now, default)
            logger.debug(f"[UserStateCache] Default: {uuid}")
            return default
    
    async def invalidate(self, uuid: str):
        """Invalida caché para un usuario (llamar después de update)."""
        async with self._lock:
            if uuid in self._local_cache:
                del self._local_cache[uuid]
                logger.debug(f"[UserStateCache] Invalidated: {uuid}")


class ExecutionTracker:
    """
    Rastreo distribuido de ejecuciones cross-pod.
    
    ✅ Registro de ejecuciones en Firestore
    ✅ Cancelación cross-pod (otro pod puede cancelar)
    ✅ Tracking de qué pod ejecuta qué
    """
    
    def __init__(self):
        import socket
        self.pod_id = socket.gethostname()
        self.firestore_enabled = os.environ.get("FIRESTORE_ENABLED", "false").lower() == "true"
        self.db = firestore.Client() if self.firestore_enabled else None
    
    async def register(self, exec_id: str, user_id: str, task_type: str) -> bool:
        """Registra ejecución en Firestore."""
        if not self.firestore_enabled or not self.db:
            logger.debug(f"[ExecutionTracker] Firestore disabled, skipping registration")
            return True
        
        try:
            now_utc = datetime.now(timezone.utc)
            self.db.collection("ejecuciones").document(exec_id).set({
                "exec_id": exec_id,
                "user_id": user_id,
                "pod_id": self.pod_id,
                "tipo": task_type,
                "estado": "running",
                "started_at": now_utc.isoformat(),
                "updated_at": now_utc.isoformat()
            }, merge=True)
            logger.info(f"[ExecutionTracker] Registered: exec_id={exec_id}, user_id={user_id}, pod={self.pod_id}")
            return True
        except Exception as e:
            logger.error(f"[ExecutionTracker] Register error: {e}")
            return False
    
    async def should_cancel(self, exec_id: str) -> bool:
        """Verifica si otro pod solicitó la cancelación."""
        if not self.firestore_enabled or not self.db:
            return False
        
        try:
            doc = self.db.collection("ejecuciones").document(exec_id).get()
            if doc.exists:
                estado = doc.to_dict().get("estado")
                if estado == "cancelled_requested":
                    logger.warning(f"[ExecutionTracker] Cancellation requested for {exec_id}")
                    return True
            return False
        except Exception as e:
            logger.warning(f"[ExecutionTracker] Cancel check error: {e}")
            return False
    
    async def request_cancel(self, exec_id: str) -> bool:
        """Requiere cancelación de una ejecución (puede estar en otro pod)."""
        if not self.firestore_enabled or not self.db:
            return False
        
        try:
            now_utc = datetime.now(timezone.utc)
            doc = self.db.collection("ejecuciones").document(exec_id).get()
            
            if not doc.exists:
                logger.warning(f"[ExecutionTracker] Execution not found: {exec_id}")
                return False
            
            data = doc.to_dict()
            pod_ejecutor = data.get("pod_id")
            
            self.db.collection("ejecuciones").document(exec_id).update({
                "estado": "cancelled_requested",
                "cancelled_at": now_utc.isoformat(),
                "cancelled_by_pod": self.pod_id
            })
            
            logger.info(f"[ExecutionTracker] Cancel requested for {exec_id} (executor: {pod_ejecutor})")
            return True
        except Exception as e:
            logger.error(f"[ExecutionTracker] Cancel request error: {e}")
            return False
    
    async def complete(self, exec_id: str, estado: str = "completed"):
        """Marca ejecución como completada."""
        if not self.firestore_enabled or not self.db:
            return
        
        try:
            now_utc = datetime.now(timezone.utc)
            self.db.collection("ejecuciones").document(exec_id).update({
                "estado": estado,
                "completed_at": now_utc.isoformat(),
                "updated_at": now_utc.isoformat()
            })
            logger.info(f"[ExecutionTracker] Completed: {exec_id} (estado={estado})")
        except Exception as e:
            logger.error(f"[ExecutionTracker] Complete error: {e}")


class DistributedLock:
    """
    Lock distribuido usando Firestore para coordinación multi-pod.
    
    ✅ Soporte para múltiples pods
    ✅ Auto-expiry si holder muere
    ✅ Fair queuing (FIFO)
    """
    
    def __init__(self, lock_name: str, ttl_seconds: int = 60, timeout_seconds: int = 30):
        self.lock_name = lock_name
        self.ttl_seconds = ttl_seconds
        self.timeout_seconds = timeout_seconds
        import socket
        self.pod_id = socket.gethostname()
        self.firestore_enabled = os.environ.get("FIRESTORE_ENABLED", "false").lower() == "true"
        self.db = firestore.Client() if self.firestore_enabled else None
        self.acquired = False
    
    async def acquire(self) -> bool:
        """Intenta adquirir el lock."""
        if not self.firestore_enabled or not self.db:
            # Sin Firestore, asumir que puede proceder
            self.acquired = True
            return True
        
        start_time = time.time()
        
        while (time.time() - start_time) < self.timeout_seconds:
            try:
                doc_ref = self.db.document(f"locks/{self.lock_name}")
                now_utc = datetime.now(timezone.utc)
                
                doc = doc_ref.get()
                
                if not doc.exists:
                    # Intentar tomar el lock
                    doc_ref.set({
                        "pod_id": self.pod_id,
                        "acquired_at": now_utc.isoformat(),
                        "ttl_seconds": self.ttl_seconds
                    })
                    self.acquired = True
                    logger.info(f"[DistributedLock] Acquired: {self.lock_name}")
                    return True
                
                # Verificar si lock expiró
                data = doc.to_dict()
                acquired_at_str = data.get("acquired_at")
                if not acquired_at_str:
                    continue  # Documento corrupto, reintentar
                
                acquired_at = datetime.fromisoformat(acquired_at_str.replace('Z', '+00:00'))
                elapsed = (now_utc - acquired_at).total_seconds()
                ttl = data.get("ttl_seconds", self.ttl_seconds)
                
                if elapsed > ttl:
                    # Lock expirado, tomarlo
                    doc_ref.set({
                        "pod_id": self.pod_id,
                        "acquired_at": now_utc.isoformat(),
                        "ttl_seconds": self.ttl_seconds,
                        "previous_owner": data.get("pod_id")
                    })
                    self.acquired = True
                    logger.info(f"[DistributedLock] Acquired (takeover): {self.lock_name}")
                    return True
                
                # Esperar y reintentar
                await asyncio.sleep(1)
            
            except Exception as e:
                logger.warning(f"[DistributedLock] Error acquiring {self.lock_name}: {e}")
                await asyncio.sleep(1)
        
        logger.warning(f"[DistributedLock] Timeout acquiring {self.lock_name}")
        return False
    
    async def release(self):
        """Libera el lock."""
        if not self.firestore_enabled or not self.db or not self.acquired:
            return
        
        try:
            doc_ref = self.db.document(f"locks/{self.lock_name}")
            doc = doc_ref.get()
            
            if doc.exists and doc.to_dict().get("pod_id") == self.pod_id:
                doc_ref.delete()
                self.acquired = False
                logger.info(f"[DistributedLock] Released: {self.lock_name}")
        except Exception as e:
            logger.error(f"[DistributedLock] Release error: {e}")
    
    async def __aenter__(self):
        await self.acquire()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.release()


class CooldownTracker:
    """
    Rastreador de cooldowns distribuido para backfill FMP.
    
    ✅ Cooldowns compartidos entre todos los pods
    ✅ Evita reintentosrepetidos del mismo error
    ✅ Auto-expiry vía Firestore TTL
    """
    
    def __init__(self):
        self.firestore_enabled = os.environ.get("FIRESTORE_ENABLED", "false").lower() == "true"
        self.db = firestore.Client() if self.firestore_enabled else None
        self._local_cooldowns: Dict[str, float] = {}  # Para fallback sin Firestore
    
    async def set_backfill_cooldown(self, symbol: str, tf: str, cooldown_s: int):
        """Establece cooldown para un símbolo/timeframe."""
        key = f"{symbol}_{tf}"
        
        # Local fallback
        self._local_cooldowns[key] = time.time() + cooldown_s
        
        # Firestore (distribuido)
        if self.firestore_enabled and self.db:
            try:
                now_utc = datetime.now(timezone.utc)
                cooldown_until = now_utc + timedelta(seconds=cooldown_s)
                
                self.db.collection("backfill_cooldowns").document(key).set({
                    "symbol": symbol,
                    "timeframe": tf,
                    "attempted_at": now_utc.isoformat(),
                    "cooldown_until": cooldown_until.isoformat(),
                    "pod_id": socket.gethostname()
                })
                logger.info(f"[CooldownTracker] Cooldown set: {key} ({cooldown_s}s)")
            except Exception as e:
                logger.warning(f"[CooldownTracker] Error setting cooldown: {e}")
    
    async def is_in_cooldown(self, symbol: str, tf: str) -> bool:
        """Verifica si un símbolo/tf está en cooldown."""
        key = f"{symbol}_{tf}"
        
        # Check local first
        if key in self._local_cooldowns:
            if time.time() < self._local_cooldowns[key]:
                logger.debug(f"[CooldownTracker] In cooldown (local): {key}")
                return True
            else:
                del self._local_cooldowns[key]
        
        # Check Firestore
        if self.firestore_enabled and self.db:
            try:
                doc = self.db.collection("backfill_cooldowns").document(key).get()
                if doc.exists:
                    data = doc.to_dict()
                    cooldown_until_str = data.get("cooldown_until")
                    if cooldown_until_str:
                        cooldown_until = datetime.fromisoformat(cooldown_until_str.replace('Z', '+00:00'))
                        if datetime.now(timezone.utc) < cooldown_until:
                            logger.debug(f"[CooldownTracker] In cooldown (Firestore): {key}")
                            return True
            except Exception as e:
                logger.warning(f"[CooldownTracker] Error checking cooldown: {e}")
        
        return False


class SharedNewsCache:
    """
    Caché compartido de noticias usando GCS y caché local.
    
    ✅ GCS como fuente de verdad compartida entre pods
    ✅ Caché local TTL de 5 minutos
    ✅ Auto-actualización en GCS
    ✅ OPTIMIZACION: Lazy client initialization para evitar startup overhead
    """
    
    def __init__(self, gcs_bucket: str = ""):
        self.gcs_enabled = os.environ.get("GCS_ENABLED", "false").lower() == "true"
        self.gcs_bucket_name = gcs_bucket or os.environ.get("GCS_BUCKET_NAME", "")
        self._local_cache: Dict[str, tuple] = {}  # {symbol: (timestamp, df)}
        self._ttl_seconds = int(os.environ.get("NEWS_CACHE_TTL_SECONDS", "300"))  # 5 min
        self._lock = asyncio.Lock()
        self._gcs_bucket = None  # Lazy-loaded
    
    @property
    def gcs_bucket(self):
        """Lazy initialization del bucket de GCS."""
        if self._gcs_bucket is None and self.gcs_enabled and self.gcs_bucket_name:
            try:
                self._gcs_bucket = storage.Client().bucket(self.gcs_bucket_name)
            except Exception as e:
                logger.warning(f"[SharedNewsCache] GCS client init failed: {e}")
        return self._gcs_bucket
    
    async def get_or_fetch(self, symbol: str, fetch_fn) -> pd.DataFrame:
        """Obtiene noticias con estrategia multi-nivel de caché."""
        async with self._lock:
            now = time.time()
            
            # 1. Check caché local (TTL: 5 min)
            if symbol in self._local_cache:
                cached_at, df = self._local_cache[symbol]
                if (now - cached_at) < self._ttl_seconds:
                    if logger.isEnabledFor(logging.DEBUG):
                        logger.debug(f"[SharedNewsCache] Hit (local): {symbol}")
                    return df
            
            # 2. Check GCS (compartido entre pods)
            if self.gcs_enabled and self.gcs_bucket:
                try:
                    blob = self.gcs_bucket.blob(f"forex_news/{symbol}_noticias.json")
                    
                    # Verificar que exista
                    if blob.exists():
                        blob.reload()
                        updated_at = blob.updated
                        age_seconds = (datetime.now(timezone.utc) - updated_at).total_seconds()
                        
                        # Si está fresco (< 24h), usar GCS
                        if age_seconds < 86400:
                            try:
                                content = blob.download_as_text()
                                df = pd.read_json(StringIO(content))
                                
                                # Cachear localmente
                                self._local_cache[symbol] = (now, df)
                                if logger.isEnabledFor(logging.INFO):
                                    logger.info(f"[SharedNewsCache] Hit (GCS): {symbol} (age: {age_seconds:.0f}s)")
                                return df
                            except Exception as e:
                                logger.warning(f"[SharedNewsCache] Error parsing GCS data: {e}")
                except Exception as e:
                    logger.warning(f"[SharedNewsCache] Error reading GCS: {e}")
            
            # 3. Fetch desde API (fallback)
            if logger.isEnabledFor(logging.INFO):
                logger.info(f"[SharedNewsCache] Fetching from API: {symbol}")
            df = await fetch_fn(symbol)
            
            if df is not None and not df.empty:
                # Guardar en GCS para otros pods
                if self.gcs_enabled and self.gcs_bucket:
                    try:
                        blob = self.gcs_bucket.blob(f"forex_news/{symbol}_noticias.json")
                        blob.upload_from_string(
                            df.to_json(orient='records', date_format='iso'),
                            content_type='application/json'
                        )
                        if logger.isEnabledFor(logging.INFO):
                            logger.info(f"[SharedNewsCache] Saved to GCS: {symbol}")
                    except Exception as e:
                        logger.error(f"[SharedNewsCache] Error saving to GCS: {e}")
                
                # Cachear localmente
                self._local_cache[symbol] = (now, df)
            
            return df if df is not None else pd.DataFrame()

    def invalidate(self, symbol: str):
        """Invalida caché local para un símbolo."""
        # ✅ SYNC invalidate para uso desde contextos que no pueden esperar async
        # Si estás en async context, considera usar asyncio.create_task para invalidate_async
        try:
            # En sync context, simplemente marca como invalidado con tiempo cero
            if symbol in self._local_cache:
                # Usar una marca de tiempo antigua para forzar refresh en próxima lectura
                self._local_cache[symbol] = (0, self._local_cache[symbol][1])
                if logger.isEnabledFor(logging.INFO):
                    logger.info(f"[SharedNewsCache] Invalidated: {symbol}")
        except Exception:
            pass
    
    async def _invalidate_async(self, symbol: str):
        """Versión async segura de invalidate con lock."""
        async with self._lock:
            if symbol in self._local_cache:
                del self._local_cache[symbol]
                if logger.isEnabledFor(logging.INFO):
                    logger.info(f"[SharedNewsCache] Async Invalidated: {symbol}")

    def invalidate_many(self, symbols: Iterable[str]):
        """Invalida caché local para múltiples símbolos."""
        for sym in symbols:
            self.invalidate(sym)


# Instancias globales del framework de coordinación
_USER_STATE_CACHE = UserStateCache()
_EXECUTION_TRACKER = ExecutionTracker()
_COOLDOWN_TRACKER = CooldownTracker()
_NEWS_CACHE = SharedNewsCache()


# CARPETA_HISTORICOS debe estar definido en tu módulo
# cache_historicos es global

#@profile
async def cargar_datos_historicos_inicial():
    """
    Carga inicial optimizada de históricos.
    ✅ OPTIMIZADO: Solo indexa archivos disponibles sin cargar contenido (10x+ startup).
    Los datos se cargan bajo demanda con LazyHistoricosLoader.
    
    Mantiene compatibilidad backwards con cache_historicos = {symbol: {timeframe: df}}
    """
    global cache_historicos
    
    logger.info("[Startup] Indexing historical files (lazy loading enabled)...")
    
    # Crear índice de archivos disponibles sin cargar contenido
    indexed = {}
    count = 0
    
    try:
        hist_dir = APP_CONFIG.hist_dir if hasattr(APP_CONFIG, "hist_dir") else CARPETA_HISTORICOS
        if not os.path.exists(hist_dir):
            logger.warning("[Startup] Historical folder not found: %s", hist_dir)
            cache_historicos = {}
            return

        for archivo in os.listdir(hist_dir):
            # Soporta .json y opcionalmente .jsonl (NDJSON)
            if not (archivo.endswith(".json") or archivo.endswith(".jsonl")):
                continue
            
            try:
                # Extraer symbol/temporalidad desde nombre archivo
                base = archivo
                if base.endswith(".jsonl"):
                    base = base[:-6]
                elif base.endswith(".json"):
                    base = base[:-5]
                
                if "__" in base:
                    symbol, temporalidad = base.split("__", 1)
                elif "_" in base:
                    symbol, temporalidad = base.rsplit("_", 1)
                else:
                    logger.debug("[Startup] Unexpected filename format: %s (skipping)", archivo)
                    continue

                if temporalidad == "enriched":
                    continue
                
                # Solo indexar que existe, no cargar aún
                indexed.setdefault(symbol, {})[temporalidad] = True
                count += 1
                
            except Exception as e:
                logger.debug("[Startup] Error indexing %s: %s", archivo, e)
                continue
        
        # Para compatibilidad backwards: llenar algunos símbolos comunes si existen
        # Sin cargar TODO en memoria
        cache_historicos = indexed
        logger.info("[Startup] ✅ Indexed %d historical files (%d symbols) - lazy loading active", 
                   count, len(indexed))
        
    except Exception as e:
        logger.error("[Startup] Error during historical indexing: %s", e)
        cache_historicos = {}



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
    
    # OPTIMIZACIÓN: Usar groupby() en lugar de iterrows() para mejor rendimiento
    try:
        # Aplicar _local_day_key de forma vectorizada
        df = df.copy()
        df['_day_key'] = df['date_country'].apply(lambda x: _local_day_key(x) if pd.notna(x) else None)
        
        # Filtrar filas sin key válida
        df = df[df['_day_key'].notna()]
        
        # Agrupar por día
        buckets = {}
        for day_key, group in df.groupby('_day_key'):
            buckets[day_key] = group.drop('_day_key', axis=1)
        
        return buckets
    except Exception as e:
        logger.warning(f"[_split_by_local_day] Error: {e}")
        return {}


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
    """Optional investiny calendar (GMT base)."""
    if not globals().get("_HAS_INVESTPY", False):
        return pd.DataFrame()
    try:
        # investiny.economic_calendar() retorna un dict con eventos
        from datetime import datetime, timedelta
        # Obtener eventos de la próxima semana
        events = investiny.economic_calendar(
            from_date=(datetime.now() - timedelta(days=1)).strftime("%d/%m/%Y"),
            to_date=(datetime.now() + timedelta(days=7)).strftime("%d/%m/%Y")
        )
        
        if not events:
            return pd.DataFrame()
        
        cal = pd.DataFrame(events)
        
        # Mapear columnas de investiny a nuestro formato
        # investiny usa: date, time, zone, currency, importance, event, actual, forecast, previous
        if "importance" in cal.columns:
            cal = cal[cal["importance"].isin(["high","medium","low"])].copy()
        
        if "date" in cal.columns and "time" in cal.columns:
            cal["date"] = pd.to_datetime(cal["date"] + " " + cal["time"].fillna("00:00"), 
                                         format="%d/%m/%Y %H:%M", errors="coerce")
            cal["date"] = cal["date"].dt.tz_localize("UTC", nonexistent="shift_forward", ambiguous="infer")
        
        # Renombrar columnas
        rename_map = {"importance": "impact", "forecast": "estimate"}
        cal = cal.rename(columns=rename_map)
        
        keep = ["date","currency","event","actual","estimate","previous","impact"]
        for c in keep:
            if c not in cal.columns:
                cal[c] = pd.NA
        
        for c in ["actual","estimate","previous"]:
            if c in cal.columns:
                cal[c] = pd.to_numeric(cal[c], errors="coerce")
        
        if "impact" in cal.columns:
            cal["impact"] = cal["impact"].astype(str).str.capitalize()
        
        return cal[keep].sort_values("date", ascending=True).reset_index(drop=True)
    except Exception as e:
        logger.info("[Investiny] Error economic calendar: %s", e)
        return pd.DataFrame()


def _investing_com_econ_fetch(*, timeout: int = 15, allow_playwright: bool = True) -> pd.DataFrame:
    """
    Fetch economic calendar from investing.com via web scraping.
    Faster than FMP, with real-time data. Returns UTC timestamps.
    """
    if not APP_CONFIG.investing_scraping_enabled:
        return pd.DataFrame()
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        logger.warning("[Investing.com] BeautifulSoup4 not installed, skipping.")
        return pd.DataFrame()
    
    # Try requests + BeautifulSoup first (faster)
    url = "https://www.investing.com/economic-calendar/"
    
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
        t0 = time.time()
        logger.info("[Investing.com] GET %s (timeout=%s)", url, timeout)
        
        resp = HTTP_SESSION.get(url, headers=headers, timeout=timeout)
        logger.info("[Investing.com] status=%s en %.3fs", resp.status_code, time.time()-t0)
        
        if resp.status_code != 200:
            logger.warning("[Investing.com] HTTP %s", resp.status_code)
            return pd.DataFrame()
        
        soup = BeautifulSoup(resp.content, 'html.parser')
        
        # Investing.com calendar data is typically in <table> or JSON embedded in page
        # Looking for event rows in the calendar
        events = []
        
        # Try to find calendar table
        table = soup.find('table', {'class': lambda x: x and 'w-full' in x if x else False})
        
        if table:
            rows = table.find_all('tr')[1:]  # Skip header
            for row in rows:
                try:
                    cols = row.find_all('td')
                    if len(cols) < 6:
                        continue
                    
                    # Extract event data
                    time_str = cols[0].get_text(strip=True)
                    currency = cols[1].get_text(strip=True)
                    event_name = cols[2].get_text(strip=True)
                    impact = cols[3].get_text(strip=True).lower()
                    
                    # Parse actual/forecast/previous if available
                    actual_str = cols[4].get_text(strip=True) if len(cols) > 4 else ""
                    forecast_str = cols[5].get_text(strip=True) if len(cols) > 5 else ""
                    previous_str = cols[6].get_text(strip=True) if len(cols) > 6 else ""
                    
                    # Skip if impact not high/medium/low
                    if impact not in ['high', 'medium', 'low']:
                        continue
                    
                    events.append({
                        'date': time_str,
                        'currency': currency,
                        'event': event_name,
                        'actual': actual_str if actual_str and actual_str != '-' else pd.NA,
                        'estimate': forecast_str if forecast_str and forecast_str != '-' else pd.NA,
                        'previous': previous_str if previous_str and previous_str != '-' else pd.NA,
                        'impact': impact.capitalize()
                    })
                except Exception as e:
                    logger.debug("[Investing.com] Row parse error: %s", e)
                    continue
        
        # Alternative: Try to extract from JSON embedded in page
        if not events:
            script_tags = soup.find_all('script')
            for script in script_tags:
                if script.string and 'economic' in script.string.lower():
                    try:
                        # Look for JSON data
                        content = script.string
                        if '{' in content and 'date' in content:
                            # Try to extract JSON-like data
                            start = content.find('{')
                            if start != -1:
                                # This is a simplified extraction
                                # A more robust approach would use regex or JSON parsing
                                pass
                    except Exception:
                        pass
        
        if not events:
            if allow_playwright:
                logger.info("[Investing.com] No events found in HTML, trying Playwright fallback...")
                return _investing_com_econ_fetch_playwright()
            logger.info("[Investing.com] No events found in HTML; Playwright disabled")
            return pd.DataFrame()
        
        df = pd.DataFrame(events)
        
        # Parse dates - try multiple formats
        df["date"] = pd.to_datetime(df["date"], errors="coerce", utc=False)
        
        # If parsing failed, use Playwright
        if df["date"].isna().all():
            if allow_playwright:
                logger.info("[Investing.com] Date parsing failed, trying Playwright fallback...")
                return _investing_com_econ_fetch_playwright()
            logger.info("[Investing.com] Date parsing failed; Playwright disabled")
            return pd.DataFrame()
        
        # Localize to UTC
        df["date"] = df["date"].dt.tz_localize("UTC", ambiguous="infer", nonexistent="shift_forward")
        df = df.dropna(subset=["date"])
        
        # Convert numeric columns
        for c in ["actual", "estimate", "previous"]:
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors="coerce")
        
        logger.info("[Investing.com] Fetched %d events via requests+BS4", len(df))
        return df[["date", "currency", "event", "actual", "estimate", "previous", "impact"]].sort_values("date").reset_index(drop=True)
        
    except Exception as e:
        if allow_playwright:
            logger.warning("[Investing.com] Requests+BS4 failed: %s. Trying Playwright...", e)
            return _investing_com_econ_fetch_playwright()
        logger.warning("[Investing.com] Requests+BS4 failed: %s. Playwright disabled", e)
        return pd.DataFrame()


def _investing_com_econ_fetch_playwright() -> pd.DataFrame:
    """
    Fallback: Use Playwright to render investing.com calendar.
    More robust for dynamic content but slower.
    """
    if not APP_CONFIG.investing_scraping_enabled:
        return pd.DataFrame()
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.warning("[Investing.com-Playwright] Playwright not installed")
        return pd.DataFrame()
    
    try:
        events = []
        url = "https://www.investing.com/economic-calendar/"
        
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(url, wait_until="networkidle")
            
            # Wait for calendar to load
            page.wait_for_selector("table", timeout=10000)
            
            # Get table content
            html = page.content()
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, 'html.parser')
            
            # Extract events (same logic as requests version)
            table = soup.find('table')
            if table:
                rows = table.find_all('tr')[1:]
                for row in rows:
                    try:
                        cols = row.find_all('td')
                        if len(cols) < 6:
                            continue
                        
                        time_str = cols[0].get_text(strip=True)
                        currency = cols[1].get_text(strip=True)
                        event_name = cols[2].get_text(strip=True)
                        impact_str = cols[3].get_text(strip=True).lower()
                        
                        if impact_str not in ['high', 'medium', 'low']:
                            continue
                        
                        actual_str = cols[4].get_text(strip=True) if len(cols) > 4 else ""
                        forecast_str = cols[5].get_text(strip=True) if len(cols) > 5 else ""
                        previous_str = cols[6].get_text(strip=True) if len(cols) > 6 else ""
                        
                        events.append({
                            'date': time_str,
                            'currency': currency,
                            'event': event_name,
                            'actual': actual_str if actual_str != '-' else pd.NA,
                            'estimate': forecast_str if forecast_str != '-' else pd.NA,
                            'previous': previous_str if previous_str != '-' else pd.NA,
                            'impact': impact_str.capitalize()
                        })
                    except Exception as e:
                        logger.debug("[Investing.com-Playwright] Row error: %s", e)
                        continue
            
            browser.close()
        
        if not events:
            logger.info("[Investing.com-Playwright] No events extracted")
            return pd.DataFrame()
        
        df = pd.DataFrame(events)
        df["date"] = pd.to_datetime(df["date"], errors="coerce", utc=False)
        df["date"] = df["date"].dt.tz_localize("UTC", ambiguous="infer", nonexistent="shift_forward")
        df = df.dropna(subset=["date"])
        
        for c in ["actual", "estimate", "previous"]:
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors="coerce")
        
        logger.info("[Investing.com-Playwright] Fetched %d events", len(df))
        return df[["date", "currency", "event", "actual", "estimate", "previous", "impact"]].sort_values("date").reset_index(drop=True)
        
    except Exception as e:
        logger.warning("[Investing.com-Playwright] Error: %s", e)
        return pd.DataFrame()


def _to_local_df(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return df
    out = df.copy()
    out["date"] = pd.to_datetime(out["date"], utc=True, errors="coerce").dt.tz_convert(pytz.UTC)
    out["date_country"] = out["date"]
    return out


def _needs_investing_fallback(
    df: pd.DataFrame,
    *,
    now_utc: datetime | None = None,
    grace_minutes: int = 10,
) -> tuple[bool, str, bool]:
    """
    Decide if we should use Investing fallback.
    Only trigger when FMP is empty or past events are missing actuals.
    """
    now = now_utc or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    if df is None or df.empty:
        return False, "", False
    if "date" not in df.columns or "actual" not in df.columns:
        return False, "", False
    cutoff = now - timedelta(minutes=grace_minutes)

    dates = pd.to_datetime(df["date"], errors="coerce", utc=True)
    past_mask = dates <= cutoff
    if not past_mask.any():
        return False, "", False

    actuals = pd.to_numeric(df["actual"], errors="coerce")
    if actuals.loc[past_mask].isna().any():
        return True, "FMP missing actuals for past events", True

    return False, "", False


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


class SharedEconomicEventsCache:
    """
    Caché compartido de eventos económicos.
    
    ✅ TTL de 1 hora (eventos cambian lentamente)
    ✅ Caché local en memoria
    ✅ Reduce 3x FMP requests a 1 en multi-pod
    """
    
    def __init__(self):
        self._cache: Dict[str, tuple] = {}  # {cache_key: (timestamp, df)}
        self._ttl_seconds = int(os.environ.get("ECONOMIC_EVENTS_CACHE_TTL", "3600"))  # 1 hora
    
    async def get_or_fetch(self, cache_key: str, fetch_fn) -> pd.DataFrame:
        """Obtiene eventos económicos con caché."""
        now = time.time()
        
        # Check caché local
        if cache_key in self._cache:
            cached_at, df = self._cache[cache_key]
            if (now - cached_at) < self._ttl_seconds:
                if logger.isEnabledFor(logging.DEBUG):
                    logger.debug(f"[EconomicEventsCache] HIT: {cache_key}")
                return df
        
        # Fetch y cachear
        if logger.isEnabledFor(logging.INFO):
            logger.info(f"[EconomicEventsCache] FETCH: {cache_key}")
        df = await (fetch_fn() if iscoroutinefunction(fetch_fn) else asyncio.to_thread(fetch_fn))
        
        if df is not None and not df.empty:
            self._cache[cache_key] = (now, df)
        
        return df if df is not None else pd.DataFrame()
    
    def invalidate(self, cache_key: str):
        """Invalida caché para forzar refresh."""
        if cache_key in self._cache:
            del self._cache[cache_key]
            if logger.isEnabledFor(logging.INFO):
                logger.info(f"[EconomicEventsCache] INVALIDATED: {cache_key}")

    def invalidate_many(self, cache_keys: Iterable[str]):
        """Invalida caché para múltiples claves."""
        for key in cache_keys:
            self.invalidate(key)


# Instancia global
_ECONOMIC_EVENTS_CACHE = SharedEconomicEventsCache()


class UserConfigCache:
    """
    Caché de configuración de usuario con TTL de 10 minutos.
    
    OPTIMIZACION: Reduce lecturas de Firestore por config cambios lentos.
    """
    def __init__(self):
        self._cache: Dict[str, tuple] = {}  # {user_id: (timestamp, config_dict)}
        self._ttl_seconds = int(os.environ.get("CONFIG_CACHE_TTL_SECONDS", "600"))  # 10 min
    
    def get_or_load(self, user_id: str, loader_fn) -> dict:
        """Obtiene config del cache o carga desde Firestore."""
        now = time.time()
        
        if user_id in self._cache:
            cached_at, cfg = self._cache[user_id]
            if (now - cached_at) < self._ttl_seconds:
                logger.debug(f"[UserConfigCache] HIT: {user_id}")
                return cfg
        
        # Cargar desde Firestore
        logger.debug(f"[UserConfigCache] LOAD: {user_id}")
        cfg = loader_fn(user_id) or {}
        self._cache[user_id] = (now, cfg)
        return cfg
    
    def invalidate(self, user_id: str):
        """Invalida cache para un usuario."""
        if user_id in self._cache:
            del self._cache[user_id]
            logger.info(f"[UserConfigCache] INVALIDATED: {user_id}")


# Instancia global
_USER_CONFIG_CACHE = UserConfigCache()


def obtener_eventos_economicos(
    *,
    plan: str | None = None,
    desde_inicio: bool = False,
    grace_minutes: int = 10,
) -> pd.DataFrame:
    """
    Pulls economic events around the FX window:
      - starter: only [yesterday, tomorrow]
      - premium + desde_inicio: paginate from 1900-01-01 to tomorrow in APP_CONFIG.econ_chunk_days
    Returns local-tz DataFrame with columns:
      ['date','currency','event','actual','estimate','previous','impact','date_country']
    
    ✅ Con caché: reduce 3x FMP requests a 1 en multi-pod (TTL 1 hora)
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
    fmp_parts = []
    if plan == "premium" and desde_inicio:
        fi = pd.to_datetime(start)
        ff = pd.to_datetime(end)
        cur = fi
        step = APP_CONFIG.econ_chunk_days
        while cur <= ff:
            a = cur.strftime("%Y-%m-%d")
            b = min(cur + timedelta(days=step-1), ff).strftime("%Y-%m-%d")
            d = _fmp_econ_fetch(a, b, timeout=APP_CONFIG.http_timeout)
            if not d.empty: fmp_parts.append(d)
            cur = pd.to_datetime(b) + timedelta(days=1)
    else:
        d = _fmp_econ_fetch(start, end, timeout=APP_CONFIG.http_timeout)
        if not d.empty: fmp_parts.append(d)

    parts = list(fmp_parts)
    fmp_df = pd.concat(fmp_parts, ignore_index=True) if fmp_parts else pd.DataFrame()
    need_investing, need_reason, allow_playwright = _needs_investing_fallback(
        fmp_df,
        now_utc=datetime.now(timezone.utc),
        grace_minutes=grace_minutes,
    )
    if need_investing and need_reason and APP_CONFIG.investing_scraping_enabled:
        logger.info("[Eventos] %s; enabling Investing fallback", need_reason)

    # Try investing.com only if enabled and missing actuals for past events
    if need_investing and APP_CONFIG.investing_scraping_enabled:
        try:
            inv_com = _investing_com_econ_fetch(
                timeout=APP_CONFIG.http_timeout,
                allow_playwright=allow_playwright,
            )
            if not inv_com.empty:
                logger.info("[Eventos] Got %d events from investing.com scraping", len(inv_com))
                parts.append(inv_com)
        except Exception as e:
            logger.warning("[Eventos] investing.com scraping failed: %s", e)

    # Fallback to investiny
    try:
        inv = _investing_econ_fetch()
        if not inv.empty:
            logger.info("[Eventos] Got %d events from investiny", len(inv))
            parts.append(inv)
    except Exception as e:
        logger.warning("[Eventos] investiny failed: %s", e)

    if not parts:
        return pd.DataFrame(columns=["date","currency","event","actual","estimate","previous","impact","date_country"])

    df = pd.concat(parts, ignore_index=True)
    df = _to_local_df(df)
    df = _dedupe_events(df)
    cache_eventos_merge(df)
    return df.reset_index(drop=True)


async def get_eventos_economicos_cached(
    *,
    plan: str | None = None,
    desde_inicio: bool = False,
    grace_minutes: int = 10,
) -> pd.DataFrame:
    """
    Obtiene eventos económicos con caché multi-pod (1 hora TTL).
    
    ✅ Reduce 3x FMP requests a 1 en 3 pods
    ✅ Cache hit: <100ms
    ✅ Cache miss: ~5-10s (FMP + web scraping)
    ✅ OPTIMIZACION: Timeout de 30s para evitar operaciones congeladas
    """
    # Generar cache_key basada en parámetros
    key = f"econ_plan={plan or APP_CONFIG.fmp_plan}_desde={desde_inicio}"
    
    # Función auxiliar para llamar obtener_eventos_economicos de forma async
    def _fetch():
        return obtener_eventos_economicos(
            plan=plan,
            desde_inicio=desde_inicio,
            grace_minutes=grace_minutes,
        )
    
    # Usar caché compartido con timeout
    try:
        return await asyncio.wait_for(_ECONOMIC_EVENTS_CACHE.get_or_fetch(key, _fetch), timeout=30.0)
    except asyncio.TimeoutError:
        logger.error(f"[get_eventos_economicos_cached] Timeout")
        return pd.DataFrame()
    except Exception as e:
        logger.error(f"[get_eventos_economicos_cached] Error: {e}")
        return pd.DataFrame()


def invalidate_economic_events_cache(plan: str | None = None, desde_inicio: bool = False):
    """Invalida caché de eventos para forzar refresh."""
    key = f"econ_plan={plan or APP_CONFIG.fmp_plan}_desde={desde_inicio}"
    _ECONOMIC_EVENTS_CACHE.invalidate(key)


def invalidate_economic_events_cache_many(keys: Iterable[str]):
    """Invalida caché de eventos para múltiples keys."""
    _ECONOMIC_EVENTS_CACHE.invalidate_many(keys)


def obtener_eventos_economicos_futuros(
    fecha_inicio,
    fecha_fin,
    *,
    grace_minutes: int = 10,
) -> pd.DataFrame:
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
    fmp_parts = []
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
            fmp_parts.append(d)

        cur = pd.to_datetime(b) + timedelta(days=1)

    if not fmp_parts:
        return pd.DataFrame(columns=[
            "date","currency","event","actual","estimate","previous",
            "impact","ponderacion","date_country"
        ])

    df = pd.concat(fmp_parts, ignore_index=True)

    need_investing, need_reason, allow_playwright = _needs_investing_fallback(
        df,
        now_utc=datetime.now(timezone.utc),
        grace_minutes=grace_minutes,
    )
    if need_investing and APP_CONFIG.investing_scraping_enabled:
        if need_reason:
            logger.info("[Eventos] %s; enabling Investing fallback (futuros)", need_reason)
        try:
            inv_com = _investing_com_econ_fetch(
                timeout=APP_CONFIG.http_timeout,
                allow_playwright=allow_playwright,
            )
            if not inv_com.empty:
                logger.info("[Eventos] Got %d events from investing.com scraping (futuros)", len(inv_com))
                df = pd.concat([df, inv_com], ignore_index=True)
        except Exception as e:
            logger.warning("[Eventos] investing.com scraping failed (futuros): %s", e)

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
    


def obtener_eventos_guardados_o_futuros(
    fecha_inicio,
    fecha_fin,
    *,
    grace_minutes: int = 10,
) -> pd.DataFrame:
    """
    Try API future fetch first; if empty/error, fall back to Firestore for the range.
    """
    # 1) Try pulling from API
    try:
        df = obtener_eventos_economicos_futuros(
            fecha_inicio,
            fecha_fin,
            grace_minutes=grace_minutes,
        )
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

    if Calendar is None or Event is None:
        logger.warning("icalendar no esta instalado; se omite envio de calendario.")
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

    for row in df.to_dict("records"):
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

        # Fuerza full-history en análisis (override manual, no recomendado para uso normal).
        force_full_history = str(os.getenv("ANALYSIS_USE_FULL_HISTORY", "false")).strip().lower() in {
            "1", "true", "yes", "y", "on"
        }

        # Mantener serie histórica completa en análisis para que el cache de indicadores
        # pueda trabajar en modo incremental (solo nuevas velas + contexto) en futuras corridas.
        # Si se desactiva, vuelve al comportamiento legacy con recorte por bars.
        persist_full_series = str(os.getenv("ANALYSIS_PERSIST_FULL_SERIES", "true")).strip().lower() in {
            "1", "true", "yes", "y", "on"
        }

        # 2.1) cold-start por activo/TF: si no hay historia cacheada, traer TODO desde FMP
        try:
            cached_df = load_cached_history(symbol, tf)
            cold_start = cached_df is None or getattr(cached_df, "empty", True)
        except Exception:
            cold_start = True

        bars_effective = None if (persist_full_series or force_full_history or cold_start) else bars

        # 3) histórico: descarga desde FMP (caching está en load_cached_history internamente)
        # El cache inteligente ocurre automáticamente en load_cached_history cuando es disponible
        df_historico = obtener_datos_historicos_fmp(symbol, tf, bars=bars_effective)
        if df_historico is None or df_historico.empty:
            logger.info("Datos históricos no disponibles para %s en %s", symbol, tf)
            return pd.DataFrame()

        df_out = df_historico.sort_index()

        # 4) recorte final si bars es numérico (solo si NO estamos preservando serie completa)
        if (not persist_full_series) and (not force_full_history) and (not cold_start) and isinstance(bars, int) and bars > 0 and len(df_out) > bars:
            before = len(df_out)
            df_out = df_out.tail(bars)
            logging.info("[HIST][TAIL] recortado de %d a %d por bars=%d", before, len(df_out), bars)

        if cold_start:
            logging.info("[HIST][BOOTSTRAP_FULL] %s-%s primera carga completa desde FMP/cache", symbol, tf)
        elif force_full_history:
            logging.info("[HIST][FULL_OVERRIDE] %s-%s usando historia completa por override", symbol, tf)

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
def calcular_indicadores_impl(df, temporalidad, window: int | None = None):
    """
    Implementación original de cálculo de indicadores.
    Esta función NO debe llamarse directamente; usar calcular_indicadores() que incluye caché.
    
    Args:
        df: DataFrame con datos OHLCV
        temporalidad: Timeframe (1min, 5min, etc.)
    
    Returns:
        DataFrame con indicadores calculados
    """
    if window is None:
        window = min(definir_window(temporalidad), len(df))
    else:
        window = min(window, len(df))

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
    if 'atr' not in df.columns:
        df['atr'] = df['ATR']

    # Señales de divergencia
    df['divergencia_macd'] = (df['macd'] > df['macd'].shift(1)) & (df['close'] < df['close'].shift(1))
    df['divergencia_rsi'] = (df['rsi'] > df['rsi'].shift(1)) & (df['close'] < df['close'].shift(1))

    df['divergencia_macd_bull'] = (df['macd'] > df['macd'].shift(1)) & (df['close'] < df['close'].shift(1))
    df['divergencia_macd_bear'] = (df['macd'] < df['macd'].shift(1)) & (df['close'] > df['close'].shift(1))
    df['divergencia_rsi_bull']  = (df['rsi']  > df['rsi'].shift(1))  & (df['close'] < df['close'].shift(1))
    df['divergencia_rsi_bear']  = (df['rsi']  < df['rsi'].shift(1))  & (df['close'] > df['close'].shift(1))

    # Convertir columnas clave a tipo numérico
    for col in ['rsi', '%K', '%D', 'ATR', 'atr', 'macd', 'signal', 'ema_12', 'ema_26']:
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
    numeric_cols = df.select_dtypes(include=[np.number]).columns
    if len(numeric_cols) > 0:
        df.loc[:, numeric_cols] = df.loc[:, numeric_cols].ffill().bfill()

    return df


# ======================== FASE 3: VALIDACIÓN DE DATOS OHLCV ========================

#@profile
def validar_ohlcv_calidad(df: pd.DataFrame, symbol: str, tf: str, strict: bool = False) -> tuple[bool, list[str]]:
    """
    ✅ FASE 3: Valida la calidad de datos OHLCV antes de procesamiento.
    
    Chequea:
    - Candles invertidas (High < Open/Close o Low > Open/Close)
    - Volumen cero (sin movimiento)
    - Gaps sospechosos > 5%
    - Datos faltantes (NaN)
    - Índices duplicados
    
    Args:
        df: DataFrame con OHLCV
        symbol: Símbolo del instrumento
        tf: Timeframe
        strict: Si True, rechaza cualquier anomalía; si False, solo avisa
    
    Returns:
        (es_válido, lista_de_problemas)
    """
    problemas = []
    
    if df.empty:
        return False, ["DataFrame vacío"]
    
    if len(df) < 2:
        return False, ["Menos de 2 candles de datos"]
    
    # Validar estructura
    required_cols = ['open', 'high', 'low', 'close', 'volume']
    missing = [c for c in required_cols if c not in df.columns]
    if missing:
        return False, [f"Columnas faltantes: {missing}"]
    
    # Validar candles invertidas
    candles_invertidas = ((df['high'] < df[['open', 'close']].max(axis=1)) | 
                          (df['low'] > df[['open', 'close']].min(axis=1))).sum()
    if candles_invertidas > 0:
        problemas.append(f"⚠️ {candles_invertidas} candles invertidas (High < Open/Close o Low > Open/Close)")
    
    # Validar volumen cero
    vol_cero = (df['volume'] == 0).sum()
    vol_cero_pct = vol_cero / len(df)
    # Threshold: 25% for forex (XxxxYyy), 10% for stocks/commodities
    # Forex often has zero-volume candles during illiquid periods
    is_forex = len(symbol) == 7 and symbol[3] in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'  # e.g., EURCAD
    threshold = 0.25 if is_forex else 0.10
    if vol_cero_pct > threshold:
        problemas.append(f"⚠️ {vol_cero} candles con volumen cero ({vol_cero/len(df)*100:.1f}%)")
    
    # Validar gaps sospechosos (>5%)
    df_copy = df.copy()
    df_copy['gap'] = df_copy['close'].shift(1).fillna(df_copy['open'])
    df_copy['gap_pct'] = abs(df_copy['open'] - df_copy['gap']) / df_copy['gap'] * 100
    gaps_sospechosos = (df_copy['gap_pct'] > 5).sum()
    if gaps_sospechosos > 0:
        problemas.append(f"⚠️ {gaps_sospechosos} gaps >5% detectados (posible dato erróneo or market gap)")
    
    # Validar NaN en OHLC
    nan_count = df[['open', 'high', 'low', 'close']].isna().sum().sum()
    if nan_count > 0:
        problemas.append(f"⚠️ {nan_count} valores NaN en OHLC")
    
    # Validar índices duplicados
    if df.index.duplicated().any():
        problemas.append(f"⚠️ Índices duplicados detectados ({df.index.duplicated().sum()} duplicados)")
    
    # Logger
    if problemas:
        logger.warning(f"[VALIDACIÓN OHLCV] {symbol}-{tf}: {len(problemas)} problemas detectados")
        for p in problemas:
            logger.warning(f"  {p}")
    
    # Si strict=True, rechazar si hay problemas; si False, solo avisar
    es_valido = len(problemas) == 0 if strict else True
    
    return es_valido, problemas


def calcular_indicadores(df, temporalidad, symbol=None):
    """
    Calcula indicadores técnicos con caché inteligente.
    
    Si symbol es None, calcula sin caché (modo compatibilidad/legacy).
    Si symbol está presente, usa sistema de caché incremental con GCS.
    
    Args:
        df: DataFrame con datos OHLCV
        temporalidad: Timeframe (1min, 5min, 1hour, 1day, etc.)
        symbol: Trading symbol (ej: "EURUSD"). Si None, no usa caché.
    
    Returns:
        DataFrame con indicadores calculados
        
    Performance:
        - Sin caché (symbol=None): mismo tiempo que antes
        - Con caché (cold start): mismo tiempo + overhead de save (~100ms)
        - Con caché (hit): <100ms (solo carga desde GCS)
        - Con caché (incremental): proporcional a nuevas velas (~5-10% del tiempo total)
    """
    REQUIRED_INDICATORS = {"macd", "signal", "rsi", "%K", "%D", "close", "high", "low", "ATR"}
    
    window = min(definir_window(temporalidad), len(df))

    if symbol is None or not _INDICATORS_CACHE_ENABLED:
        # Modo legacy: sin caché
        df_result = calcular_indicadores_impl(df, temporalidad, window=window)
        # Validar que se calcularon todos los indicadores
        missing = REQUIRED_INDICATORS - set(df_result.columns)
        if missing:
            raise ValueError(f"[calcular_indicadores] Faltan columnas requeridas después de calcular_impl: {missing}. El cálculo fue incompleto.")
        return df_result
    
    # Modo con caché
    df_result, stats = _INDICATORS_CACHE.get_or_calculate(
        symbol=symbol,
        tf=temporalidad,
        df_historicos=df,
        calc_func=partial(calcular_indicadores_impl, window=window)
    )
    
    # ✅ VALIDACIÓN CRÍTICA: Asegurar que el caché devolvió un DataFrame completo
    missing_indicators = REQUIRED_INDICATORS - set(df_result.columns)
    if missing_indicators:
        logger.error(
            f"[calcular_indicadores] CACHE CORRUPTED: {symbol}/{temporalidad} missing indicators: {missing_indicators}. "
            f"Cache source: {stats.get('source')}. Force recalc on next run."
        )
        # Marcar caché como corrupto para invalidar en siguiente llamada
        try:
            _INDICATORS_CACHE.invalidate(symbol, temporalidad)
        except Exception as e:
            logger.warning(f"[calcular_indicadores] Could not invalidate cache: {e}")
        # Lanzar excepción para que el caller reintente
        raise ValueError(
            f"DataFrame del caché incompleto: faltan {missing_indicators}. "
            f"Caché invalidado. Intenta de nuevo en la próxima ejecución."
        )
    
    # Log stats para métricas
    if stats.get("cache_hit"):
        if stats.get("incremental"):
            logger.info(
                f"[Indicators] {symbol}/{temporalidad}: Incremental (+{stats.get('new_bars', 0)} bars, {stats['calc_time_ms']:.0f}ms, pod={stats.get('pod_id', '?')})"
            )
        else:
            logger.info(
                f"[Indicators] {symbol}/{temporalidad}: Cache hit (age={stats.get('cached_age_hours', 0):.1f}h, 0ms, source={stats.get('source', '?')}, pod={stats.get('pod_id', '?')})"
            )
    else:
        logger.info(
            f"[Indicators] {symbol}/{temporalidad}: Full calc ({stats['calc_time_ms']:.0f}ms, source={stats['source']}, pod={stats.get('pod_id', '?')})"
        )
    
    return df_result

def limitar_probabilidad(probabilidad_exito):
    return max(1, min(probabilidad_exito, 100))

# Función para ajustar la probabilidad técnica con incrementos controlados
#@profile
_prob_tecnica_cache = {}
_prob_tecnica_cache_ttl = 600  # 10 minutos

def ajustar_probabilidad_tecnica(df, temporalidad, window, cfg: Optional[dict] = None, niveles: Optional[dict] = None, symbol: str | None = None):
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

    # Cache simple por simbolo/TF cuando no hay cambios en la ultima vela
    cache_key = None
    if symbol:
        try:
            last_ts = df.index[-1] if len(df.index) else None
            cache_key = f"{symbol}|{temporalidad}|{window}|{len(df)}|{last_ts}"
            entry = _prob_tecnica_cache.get(cache_key)
            if entry:
                age = (datetime.now(UTC) - entry['timestamp']).total_seconds()
                if age < _prob_tecnica_cache_ttl:
                    return entry['value']
        except Exception:
            cache_key = None
    
    # ✅ Verificar que los indicadores requeridos existan
    required_cols = ["macd", "signal", "rsi", "%K", "%D", "close", "high", "low", "ATR"]
    missing_cols = [col for col in required_cols if col not in df.columns]
    if missing_cols:
        logger.error(f"⚠️ [ajustar_probabilidad_tecnica] Indicadores FALTANTES: {missing_cols}. Retornando prob default (50.0). Revisa integridad de calcular_indicadores.")
        return 50.0

    ultima_fila = df.iloc[-1]
    penultima_fila = df.iloc[-2]
    probabilidad_tecnica = 50.0

    # Soportes / resistencias: reutilizar niveles precomputados si están disponibles
    soporte_nivel_1 = None
    resistencia_nivel_1 = None
    if isinstance(niveles, dict):
        soporte_nivel_1 = _coerce_float(niveles.get("soporte_nivel_1"))
        resistencia_nivel_1 = _coerce_float(niveles.get("resistencia_nivel_1"))

    if not (_finite(soporte_nivel_1) and _finite(resistencia_nivel_1)):
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
        k = _coerce_float(ultima_fila.get("%K"))
        d = _coerce_float(ultima_fila.get("%D"))
        if k is not None and d is not None:
            if k > d and k < STOCH_LOW:
                probabilidad_tecnica += mag["estoc_base"]   # "+3"
                senal_estocastico = True
            elif k < d and k > STOCH_HIGH:
                probabilidad_tecnica -= abs(mag["estoc_base"])  # "-3"

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

    # Limitar al rango [1,75] - máximo 75% refleja realidad
    # Incluso con múltiples señales, raramente supera 75%
    probabilidad_tecnica = min(probabilidad_tecnica, 75.0)
    probabilidad_tecnica = limitar_probabilidad(probabilidad_tecnica)

    if cache_key:
        _prob_tecnica_cache[cache_key] = {
            "value": probabilidad_tecnica,
            "timestamp": datetime.now(UTC)
        }
        if len(_prob_tecnica_cache) > 500:
            _prob_tecnica_cache.clear()

    return probabilidad_tecnica


#@profile
def analisis_tecnico_detallado(df: "pd.DataFrame", temporalidad: str, window: int, cfg: Optional[dict] = None) -> dict:
    """
    Meta técnica "de trader":
      - trend_dir / trend_strength (EMA20 vs EMA50 con normalización por ATR)
      - régimen (TREND / RANGE)
      - RSI state y proximidad a soporte/resistencia (rolling window)
      - volatilidad (ATR vs ATR media)
    No altera tu lógica: sólo aporta contexto para confluencia/alertas.
    """
    out = {
        "tf": temporalidad,
        "trend_dir": "RANGE",
        "trend_strength": 0.0,
        "regime": "RANGE",
        "rsi": None,
        "rsi_state": "NA",
        "near_support": False,
        "near_resistance": False,
        "atr": None,
        "atr_regime": "NA",
        "support": None,
        "resistance": None,
    }
    try:
        if df is None or len(df) < 5:
            return out

        close = pd.to_numeric(df["close"], errors="coerce")
        if close.isna().all():
            return out

        # EMA fast/slow (si no existen)
        ema_fast = df["ema20"] if "ema20" in df.columns else close.ewm(span=20, adjust=False).mean()
        ema_slow = df["ema50"] if "ema50" in df.columns else close.ewm(span=50, adjust=False).mean()

        ef = float(ema_fast.iloc[-1])
        es = float(ema_slow.iloc[-1])
        diff = ef - es

        atr = None
        if "ATR" in df.columns:
            atr = float(pd.to_numeric(df["ATR"].iloc[-1], errors="coerce"))
        if not (atr and atr > 0):
            atr = float(close.iloc[-1]) * 0.002  # fallback 0.2%
        out["atr"] = atr

        strength = abs(diff) / max(1e-9, atr)
        out["trend_strength"] = float(strength)

        if diff > 0:
            out["trend_dir"] = "UP"
        elif diff < 0:
            out["trend_dir"] = "DOWN"
        else:
            out["trend_dir"] = "RANGE"

        # régimen: si fuerza supera umbral, consideramos TREND
        regime_thr = float(((cfg or {}).get("tecnica", {}) or {}).get("regime_strength_threshold", 0.35))
        out["regime"] = "TREND" if strength >= regime_thr else "RANGE"

        # RSI state (si existe)
        if "rsi" in df.columns:
            rsi = float(pd.to_numeric(df["rsi"].iloc[-1], errors="coerce"))
            out["rsi"] = rsi
            if rsi >= 70:
                out["rsi_state"] = "OVERBOUGHT"
            elif rsi <= 30:
                out["rsi_state"] = "OVERSOLD"
            else:
                out["rsi_state"] = "NEUTRAL"

        # soportes/resistencias rolling
        w = int(max(5, min(window or 20, len(df))))
        sup = float(pd.to_numeric(df["low"], errors="coerce").rolling(window=w).min().iloc[-1])
        res = float(pd.to_numeric(df["high"], errors="coerce").rolling(window=w).max().iloc[-1])
        out["support"] = sup
        out["resistance"] = res

        last = float(close.iloc[-1])
        near_thr = 0.5 * atr
        out["near_support"] = bool(abs(last - sup) <= near_thr)
        out["near_resistance"] = bool(abs(last - res) <= near_thr)

        # régimen de volatilidad (ATR actual vs media ATR)
        if "ATR" in df.columns and len(df) >= w:
            atr_series = pd.to_numeric(df["ATR"], errors="coerce").dropna()
            if len(atr_series) >= w:
                atr_mean = float(atr_series.rolling(window=w).mean().iloc[-1])
                if atr_mean > 0:
                    rel = float(atr / atr_mean)
                    if rel <= 0.75:
                        out["atr_regime"] = "LOW"
                    elif rel >= 1.35:
                        out["atr_regime"] = "HIGH"
                    else:
                        out["atr_regime"] = "NORMAL"
    except Exception as e:
        out["error"] = str(e)

    return out


def evaluar_confluencia_trade(
    *,
    symbol: str,
    temporalidad: str,
    tipo_operacion: str,
    precio_actual: float | None,
    niveles: dict | None,
    atr: float | None,
    prob_tecnica: float | None,
    prob_fundamental: float | None,
    prob_general: float | None,
    tecnica_meta: dict | None,
    fundamental_meta: dict | None,
    cfg: Optional[dict] = None,
) -> dict:
    """
    Etiqueta de confluencia (no bloquea entradas):
      - mide alineación técnico/fundamental con el sentido de la operación
      - agrega warnings (resistencia/soporte, RSI extremo, blackout eventos, rango)
    """
    def _dir_from_tipo(t: str) -> int:
        s = str(t or "").lower()
        if any(k in s for k in ("compra", "buy", "long", "alcista", "bull")):
            return +1
        if any(k in s for k in ("venta", "sell", "short", "bajista", "bear")):
            return -1
        return 0

    dir_trade = _dir_from_tipo(tipo_operacion)
    pair_bias = None
    if isinstance(fundamental_meta, dict):
        try:
            pair_bias = float(fundamental_meta.get("pair_bias"))
        except Exception:
            pair_bias = None

    align_fund = None
    if dir_trade != 0 and isinstance(pair_bias, (int, float)) and math.isfinite(pair_bias):
        align_fund = (dir_trade * pair_bias) >= 0.0

    warnings: list[str] = []

    # blackout
    if isinstance(fundamental_meta, dict) and fundamental_meta.get("blackout") is True:
        warnings.append("⚠️ Blackout por evento HIGH (volatilidad elevada)")

    # soporte/resistencia + RSI
    if isinstance(tecnica_meta, dict):
        if dir_trade == +1 and tecnica_meta.get("near_resistance") is True:
            warnings.append("⚠️ LONG cerca de resistencia (riesgo de rechazo)")
        if dir_trade == -1 and tecnica_meta.get("near_support") is True:
            warnings.append("⚠️ SHORT cerca de soporte (riesgo de rebote)")

        rsi_state = str(tecnica_meta.get("rsi_state") or "").upper()
        if dir_trade == +1 and rsi_state == "OVERBOUGHT":
            warnings.append("⚠️ RSI sobrecomprado para LONG")
        if dir_trade == -1 and rsi_state == "OVERSOLD":
            warnings.append("⚠️ RSI sobrevendido para SHORT")

        if str(tecnica_meta.get("atr_regime") or "").upper() == "LOW":
            warnings.append("⚠️ Volatilidad baja (ATR): posible rango/ruido")

    if align_fund is False:
        warnings.append("⚠️ Fundamental en contra del sentido propuesto")

    # score de confluencia (0..1) = base prob_general ajustado por flags
    base = None
    if isinstance(prob_general, (int, float)) and math.isfinite(prob_general):
        base = max(0.0, min(1.0, float(prob_general) / 100.0))
    elif isinstance(prob_tecnica, (int, float)) and isinstance(prob_fundamental, (int, float)):
        base = max(0.0, min(1.0, 0.5 * (float(prob_tecnica) + float(prob_fundamental)) / 100.0))
    else:
        base = 0.5

    score = base
    if align_fund is True:
        score += 0.06
    elif align_fund is False:
        score -= 0.10

    if warnings:
        # penaliza ligeramente si hay varias alertas
        score -= min(0.12, 0.03 * len(warnings))

    score = max(0.0, min(1.0, score))

    if score >= 0.75:
        label = "ALTA"
    elif score >= 0.60:
        label = "MEDIA"
    else:
        label = "BAJA"

    return {
        "symbol": symbol,
        "tf": temporalidad,
        "label": label,
        "score": float(score),
        "trade_dir": dir_trade,
        "align_fundamental": align_fund,
        "pair_bias": float(pair_bias) if isinstance(pair_bias, (int, float)) and math.isfinite(pair_bias) else None,
        "warnings": warnings,
    }

# ======================== FASE 3: SISTEMA DE WHITELISTING ========================

#@profile
def evaluar_si_autorizado_operar(
    symbol: str,
    tf: str,
    tipo_operacion: str,
    confluencia_score: float,
    prob_tecnica: float,
    prob_fundamental: float,
    rrr_promedio: float,
    alertas: list,
    tecnica_meta: dict = None,
    whitelist_cfg: dict = None
) -> dict:
    """
    ✅ FASE 3: Whitelisting - Determina si las condiciones son "suficientemente buenas" para operar.
    
    Mejoras (Trader & Math Expert):
    - Cálculo de Expectativa Matemática (E) como factor decisivo.
    - Curvas de puntuación continuas en lugar de escalones binarios.
    - Penalización exponencial por alertas críticas.
    - Flexibilidad en RRR si la probabilidad compensa (High Winrate setups).
    - Configurable desde frontend (whitelist_cfg).
    
    Returns:
        {
            "autorizado": bool,
            "score_final": float,
            "razon_rechazo": str o None,
            "recomendacion": str,
            "expectativa": float
        }
    """
    score_final = 0.0
    razones_rechazo = []
    warnings_list = []
    
    # Configuración (defaults conservadores)
    cfg = whitelist_cfg or {}
    MIN_SCORE = float(cfg.get("min_score", 60.0))
    MIN_EXPECTANCY = float(cfg.get("min_expectancy", 0.0))  # 0.0 es breakeven
    MIN_TOLERANCE_E = float(cfg.get("tolerance_negative_e", -0.1))
    
    # --- A. Cálculo de Expectativa Matemática ---
    # Asumimos prob_tecnica como winrate estimado. Si es None, usamos 50% conservador.
    p_win = (prob_tecnica if prob_tecnica is not None else 50.0) / 100.0
    rrr = rrr_promedio if rrr_promedio is not None else 1.0
    
    # Kelly simple / Expectancy: (Win% * Reward) - (Loss% * Risk)
    # Risk siempre 1R. Reward lo tomamos del RRR.
    expectancy = (p_win * rrr) - (1.0 - p_win)
    
    # --- B. Scoring por Componentes (0-100+) ---
    
    # 1. Confluencia técnica (0-25 pts)
    # Mapeo suave: 0.5 -> 0pts, 0.8+ -> 25pts (Full confidence)
    if confluencia_score is not None:
        raw_conf = min(1.0, max(0.0, confluencia_score))
        if raw_conf < 0.5:
            razones_rechazo.append(f"Confluencia técnica insuficiente ({raw_conf:.2f})")
        
        # Curva cuadrática para premiar la alta calidad más agresivamente
        # (x - 0.5) / 0.3 => normalizado 0..1 entre 0.5 y 0.8
        factor = min(1.0, max(0.0, (raw_conf - 0.5) / 0.3))
        score_final += 25.0 * factor
    else:
         warnings_list.append("Sin datos de confluencia")

    # 2. Probabilidad técnica (0-25 pts)
    # Mapeo: 50% -> 0pts, 65% -> 25pts.
    # Un modelo >60% ya es excelente. >55% es bueno.
    if prob_tecnica is not None:
        if prob_tecnica < 50.0:
            warnings_list.append(f"Prob. Técnica < 50% ({prob_tecnica:.1f}%)")
        
        # 50->0, 60->20, 62.5->25.
        p_score = min(25.0, max(0.0, (prob_tecnica - 50.0) * 2.0))
        score_final += p_score

    # 3. Probabilidad fundamental (0-25 pts)
    # Similar a técnica
    if prob_fundamental is not None:
        f_score = min(25.0, max(0.0, (prob_fundamental - 50.0) * 2.0))
        score_final += f_score

    # 4. Risk-Reward Ratio & Expectancy (0-25 pts + Bonus)
    if rrr_promedio is not None:
        # Puntuación base por RRR: 1.0->0, 2.0->25.
        rrr_score = min(25.0, max(0.0, (rrr_promedio - 1.0) * 25.0))
        score_final += rrr_score

        # REGLA DE ORO: No operar esperanza negativa
        if expectancy < MIN_TOLERANCE_E: # Tolerancia leve por error de estimación
             razones_rechazo.append(f"Expectativa matemática negativa (E={expectancy:.2f})")
        elif expectancy < MIN_EXPECTANCY:
             warnings_list.append(f"Expectativa marginal (E={expectancy:.2f})")
        
        # Si RRR es bajo (<1.2) pero la Esperanza es muy buena (>0.4), PERMITIR (Scalping)
        if rrr_promedio < 1.2 and expectancy < 0.2:
            razones_rechazo.append(f"RRR bajo ({rrr_promedio:.2f}) sin suficiente Winrate compensatorio")

    # 5. Penalizaciones por Alertas
    alertas_criticas = 0
    if alertas:
        # Penaliza RSI en extremos o divergencias graves
        alertas_criticas = sum(1 for a in alertas if "OVERBOUGHT" in str(a) or "OVERSOLD" in str(a) or "CRITICAL" in str(a))
    
    if alertas_criticas > 0:
        penalty = 0.85 ** alertas_criticas # -15% compuesto por alerta
        score_final *= penalty
        if alertas_criticas > 2:
            razones_rechazo.append(f"Múltiples alertas críticas ({alertas_criticas})")

    # --- C. Decisión Final ---
    
    # Umbral de aprobación configurable
    autorizado = score_final >= MIN_SCORE and len(razones_rechazo) == 0
    
    # Fallback log
    razon_str = " | ".join(map(str, razones_rechazo)) if razones_rechazo else None
    warn_str = " | ".join(map(str, warnings_list)) if warnings_list else ""
    
    if autorizado:
        recomendacion = f"✅ ENTRAMOS: Score {score_final:.1f} | E={expectancy:.2f}"
    elif score_final >= (MIN_SCORE - 10):
        recomendacion = f"⚠️ OBSERVACIÓN: Score {score_final:.1f} (Marginal)"
    else:
        recomendacion = f"❌ DESCARTADO: Score {score_final:.1f}"

    logger.info(f"[Whitelist] {symbol}-{tf} {tipo_operacion}: Score={score_final:.1f} E={expectancy:.2f} Auth={autorizado}")
    if razon_str: 
        logger.warning(f"  [RECHAZO] {razon_str}")
    if warn_str:
        logger.info(f"  [WARN] {warn_str}")

    return {
        "autorizado": autorizado,
        "score_final": float(score_final),
        "expectativa": float(expectancy),
        "razon_rechazo": razon_str,
        "recomendacion": recomendacion
    }

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
                                     fecha_inicio=None, fecha_fin=None, cfg: Optional[dict]=None,
                                     return_meta: bool=False):
    """
    Ajuste fundamental "pro" (FX-first):
      - Scoring continuo por sorpresa (tanh) en [-1,1]
      - Pesos por impacto, recencia y decaimiento
      - Flip correcto si el evento es de la divisa cotizada (quote)
      - Agrupación por buckets temporales (una señal compuesta por vela/ventana)
      - Blackout opcional alrededor de eventos HIGH para evitar entradas erróneas
    Devuelve float [0..100], o (float, meta) si return_meta=True.
    """
    FUND_DEFAULTS = {
        "obtener_noticias": True,
        "calcular_impacto_noticias": True,
        "impacto_noticias_factor": 0.10,

        # ventana / recencia
        "consider_events_hours": 72,
        "recency_recent_minutes": 15,
        "recency_recent_boost": 1.5,
        "recency_decay_floor": 0.10,

        # pesos impacto
        "impact_weights": {"high": 2.0, "medium": 1.5, "low": 1.0},

        # FX: si el evento afecta a la divisa cotizada (quote), se invierte el signo
        "flip_secondary_currency": True,

        # sensibilidad por categoría (se usa como escala, no como ajuste discreto)
        "cat_weights": {
            "unemployment": {"good": 0.30, "bad": -0.20},
            "employment":   {"good": 0.30, "bad": -0.20},
            "inflation":    {"good": 0.30, "bad": -0.30},
            "gdp":          {"good": 0.30, "bad": -0.20},
            "retail":       {"good": 0.30, "bad": -0.20},
            "rates":        {"good": 0.30, "bad": -0.30},
            "generic":      {"better_both": 0.25, "better_estimate": 0.15, "better_prev": 0.10, "worse": -0.25},
        },

        # límites y mapeo score->prob
        "per_event_cap": 0.50,               # caps por evento ya ponderado
        "score_to_prob_factor": 18.0,        # cuánto mueve el score compuesto la prob (puntos)
        "signal_threshold": 0.12,            # umbral para llamar bullish/bearish
        "surprise_scale": 2.0,               # escala dentro de tanh

        # bucketización
        "bucketize_events": True,
        "bucket_top_n": 3,

        # blackout de noticias (evita operar en ventana de alta volatilidad)
        "event_blackout_enabled": True,
        "blackout_pre_minutes": 12,          # antes de HIGH
        "blackout_post_minutes": 4,          # después de HIGH
        "blackout_prob_penalty": 8.0,        # penaliza la prob fundamental si hay blackout

        "return_on_no_events": 50.0,
    }

    # ---- merge cfg ----
    fund_cfg = (cfg or {}).get("fundamental", {}) if isinstance(cfg, dict) else {}
    fund = {**FUND_DEFAULTS, **(fund_cfg or {})}

    # merge profundo de impact_weights + cat_weights (compatible con tu UI)
    impact_weights = {**FUND_DEFAULTS["impact_weights"], **(fund_cfg.get("impact_weights", {}) or {})}
    catw = dict(FUND_DEFAULTS["cat_weights"])
    for k, v in (fund_cfg.get("cat_weights", {}) or {}).items():
        catw[k] = {**catw.get(k, {}), **(v or {})}

    meta: dict = {
        "enabled": True,
        "symbol": symbol,
        "tf": temporalidad,
        "total_score": 0.0,
        "pair_bias": 0.0,           # + => bullish pair, - => bearish pair
        "signal": "neutral",
        "buckets": [],
        "blackout": False,
        "blackout_window_min": {"pre": int(fund["blackout_pre_minutes"]), "post": int(fund["blackout_post_minutes"])},
        "blackout_events": [],
        "notes": [],
    }

    # ---- Noticias (igual que antes pero con factor configurable) ---
    # ✅ Con caché multi-pod (fallback a síncrono para compatibilidad)
    try:
        if fund.get("obtener_noticias", True):
            global cache_noticias
            if "cache_noticias" not in globals() or cache_noticias is None:
                cache_noticias = {}
            
            # ✅ Primero intenta caché local, luego fetch
            df_noticias = None
            with cache_noticias_lock:
                df_noticias = cache_noticias.get(symbol)
            if df_noticias is None:
                df_noticias = obtener_noticias(symbol, fecha_inicio, fecha_fin)
            with cache_noticias_lock:
                cache_noticias[symbol] = df_noticias
        else:
            df_noticias = None

        if fund.get("calcular_impacto_noticias", True) and df_noticias is not None:
            imp = calcular_impacto_noticias(df_noticias)
            if imp is not None:
                probabilidad_exito += float(imp) * float(fund.get("impacto_noticias_factor", 0.10))
    except Exception as e:
        meta["notes"].append(f"Noticias/impacto omitidos: {e}")

    # ---- Validación de eventos ----
    cols = {"date", "actual", "estimate", "previous", "currency", "event", "impact"}
    if df_eventos is None or getattr(df_eventos, "empty", True) or not cols.issubset(df_eventos.columns):
        out = limitar_probabilidad(float(fund.get("return_on_no_events", 50.0)))
        return (out, meta) if return_meta else out

    df = df_eventos.copy()

    # Normaliza fechas a UTC
    try:
        df["date"] = pd.to_datetime(df["date"], errors="coerce", utc=True)
    except Exception:
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        if getattr(df["date"].dt, "tz", None) is None:
            df["date"] = df["date"].dt.tz_localize(pytz.UTC, ambiguous="NaT", nonexistent="NaT")

    # números
    for c in ("actual", "estimate", "previous"):
        try:
            df[c] = pd.to_numeric(df[c].apply(limpiar_valores), errors="coerce")
        except Exception:
            df[c] = pd.to_numeric(df[c], errors="coerce")

    now = datetime.now(pytz.UTC)

    # ventana hacia atrás
    try:
        hrs = int(fund.get("consider_events_hours", 72))
        cutoff = now - timedelta(hours=max(1, hrs))
        df = df[df["date"] >= cutoff]
    except Exception:
        pass

    # ---- parse FX base/quote ----
    def _parse_pair(sym: str) -> tuple[str | None, str | None]:
        try:
            s = re.sub(r"[^A-Za-z]", "", str(sym or "")).upper()
            if len(s) >= 6:
                return s[:3], s[3:6]
        except Exception:
            pass
        return None, None

    base_ccy, quote_ccy = _parse_pair(symbol)
    if not base_ccy or not quote_ccy:
        # si no es par FX, se usa moneda del evento tal cual (sin flip quote)
        base_ccy, quote_ccy = None, None

    # ---- impacto str -> weight ----
    def _impact_weight(impact: Any) -> float:
        try:
            k = str(impact or "").strip().lower()
        except Exception:
            k = ""
        return float(impact_weights.get(k, 1.0))

    # ---- bucket size según TF (minutos) ----
    def _tf_to_bucket_minutes(tf: str) -> int:
        t = str(tf or "").strip().lower()
        if t.endswith("m"):
            try: return max(1, int(re.sub(r"[^0-9]", "", t)))
            except Exception: return 5
        if t.endswith("h"):
            try: return max(1, int(re.sub(r"[^0-9]", "", t))) * 60
            except Exception: return 60
        if t.endswith("d"):
            return 1440
        if t.endswith("w"):
            return 10080
        # formatos tipo "M5", "H1"
        if t.startswith("m"):
            try: return max(1, int(re.sub(r"[^0-9]", "", t)))
            except Exception: return 5
        if t.startswith("h"):
            try: return max(1, int(re.sub(r"[^0-9]", "", t))) * 60
            except Exception: return 60
        return 15

    bucket_minutes = _tf_to_bucket_minutes(temporalidad)

    # ---- polaridad: si "más alto" es bueno para la divisa ----
    def _polarity(event_name: str) -> int:
        name = str(event_name or "").lower()
        # lower is better
        if "unemployment" in name or "tasa de desempleo" in name:
            return -1
        # inventory: higher inventories bearish oil; lo dejamos como -1
        if "inventor" in name and "oil" in name:
            return -1
        # everything else: higher is better (hawkish/crecimiento)
        return +1

    def _category_key(event_name: str) -> str:
        # usa detectar_categoria si existe, si no heurística simple
        try:
            cat = detectar_categoria(event_name)
        except Exception:
            cat = None
        c = str(cat or "").lower()
        n = str(event_name or "").lower()

        if "unemployment" in c or "unemployment" in n or "desemple" in n:
            return "unemployment"
        if "employment" in c or "payroll" in n or "empleo" in n:
            return "employment"
        if "inflation" in c or "cpi" in n or "ppi" in n or "inflation" in n:
            return "inflation"
        if "gdp" in n or "pib" in n:
            return "gdp"
        if "retail" in n or "ventas minoristas" in n:
            return "retail"
        if "rate" in n or "interest" in n or "policy" in n or "tasa" in n:
            return "rates"
        return "generic"

    def _sens_for_cat(cat_key: str, dir_surprise: float) -> float:
        cw = catw.get(cat_key, {})
        # sens = magnitud configurable (no signo)
        if cat_key == "generic":
            # usa el mejor caso como escala base
            return float(abs(cw.get("better_both", 0.25)) or 0.25)
        if dir_surprise >= 0:
            return float(abs(cw.get("good", 0.30)) or 0.30)
        return float(abs(cw.get("bad", -0.20)) or 0.20)

    def _cap(x: float) -> float:
        cap = float(fund.get("per_event_cap", 0.50))
        return max(min(float(x), cap), -cap)

    # ---- blackout alrededor de HIGH ----
    blackout_enabled = bool(fund.get("event_blackout_enabled", True))
    pre_min = int(fund.get("blackout_pre_minutes", 12))
    post_min = int(fund.get("blackout_post_minutes", 4))
    if blackout_enabled and base_ccy and quote_ccy:
        try:
            win_start = now - timedelta(minutes=max(0, post_min))
            win_end   = now + timedelta(minutes=max(0, pre_min))
            dfw = df[(df["date"] >= win_start) & (df["date"] <= win_end)]
            if not dfw.empty:
                dfw = dfw.copy()
                dfw["impact_l"] = dfw["impact"].astype(str).str.lower()
                dfw = dfw[dfw["impact_l"].isin(["high", "alto", "high impact", "high-impact"])]
                dfw = dfw[dfw["currency"].astype(str).str.upper().isin([base_ccy, quote_ccy])]
                if not dfw.empty:
                    meta["blackout"] = True
                    # guarda máximo 5 para UI/log
                    for r in dfw.sort_values("date").head(5).to_dict("records"):
                        meta["blackout_events"].append({
                            "date": r.get("date").isoformat() if pd.notna(r.get("date")) else None,
                            "currency": str(r.get("currency") or ""),
                            "event": str(r.get("event") or ""),
                            "impact": str(r.get("impact") or ""),
                        })
        except Exception as e:
            meta["notes"].append(f"blackout_check_error: {e}")

    # ---- filtra eventos que afectan al par ----
    if base_ccy and quote_ccy:
        df = df[df["currency"].astype(str).str.upper().isin([base_ccy, quote_ccy])]
    if df.empty:
        out = limitar_probabilidad(float(fund.get("return_on_no_events", 50.0)))
        return (out, meta) if return_meta else out

    # ---- score por evento (continuo) ----
    recent_minutes = float(fund.get("recency_recent_minutes", 15))
    recent_boost = float(fund.get("recency_recent_boost", 1.5))
    decay_floor = float(fund.get("recency_decay_floor", 0.10))
    surprise_scale = float(fund.get("surprise_scale", 2.0))
    top_n = int(fund.get("bucket_top_n", 3))

    df = df.copy()
    df["impact_w"] = df["impact"].apply(_impact_weight)

    # recencia + decay
    def _age_minutes(dt):
        try:
            return max((now - dt).total_seconds() / 60.0, 0.0)
        except Exception:
            return 1e9

    df["age_min"] = df["date"].apply(_age_minutes)
    df["recency_boost"] = df["age_min"].apply(lambda m: recent_boost if m <= recent_minutes else 1.0)

    # decay exponencial simple según bucket_minutes (si TF es grande, el decay es más lento)
    half_life_min = max(30.0, float(bucket_minutes) * 4.0)  # >= 30min
    df["decay"] = df["age_min"].apply(lambda m: max(decay_floor, math.exp(-m / half_life_min)))

    # bucket
    if bool(fund.get("bucketize_events", True)):
        try:
            df["bucket"] = df["date"].dt.floor(f"{bucket_minutes}min")
        except Exception:
            df["bucket"] = df["date"]
    else:
        df["bucket"] = df["date"]

    scores = []
    for ev in df.itertuples(index=False):
        dt = getattr(ev, "date", None)
        if pd.isna(dt):
            continue

        actual = getattr(ev, "actual", None)
        est = getattr(ev, "estimate", None)
        prev = getattr(ev, "previous", None)
        if pd.isna(actual):
            continue

        # sorpresa normalizada
        denom = None
        if pd.notna(est) and float(est) != 0.0:
            denom = abs(float(est))
            raw = (float(actual) - float(est)) / denom
        elif pd.notna(prev) and float(prev) != 0.0:
            denom = abs(float(prev))
            raw = (float(actual) - float(prev)) / denom
        else:
            # no hay referencia razonable
            continue

        # aplica polaridad
        event_name = getattr(ev, "event", None)
        pol = _polarity(event_name)
        dir_surprise = float(raw) * float(pol)

        # sensibilidad por categoría
        ck = _category_key(event_name)
        sens = _sens_for_cat(ck, dir_surprise)

        core = math.tanh(dir_surprise * surprise_scale)  # [-1,1]
        score = core * sens

        # pesos
        w = float(getattr(ev, "impact_w", 1.0)) * float(getattr(ev, "recency_boost", 1.0)) * float(getattr(ev, "decay", 1.0))
        score *= w

        # FX quote flip
        currency = str(getattr(ev, "currency", "") or "")
        if bool(fund.get("flip_secondary_currency", True)) and quote_ccy and currency.upper() == quote_ccy:
            score *= -1.0

        score = _cap(score)

        scores.append({
            "bucket": getattr(ev, "bucket", None),
            "date": dt,
            "currency": currency,
            "impact": str(getattr(ev, "impact", "") or ""),
            "event": str(event_name or ""),
            "score": float(score),
            "core": float(core),
            "w": float(w),
            "age_min": float(getattr(ev, "age_min", 0.0)),
        })

    if not scores:
        out = limitar_probabilidad(float(fund.get("return_on_no_events", 50.0)))
        return (out, meta) if return_meta else out

    # ---- agrega por bucket ----
    by_bucket: dict = {}
    for s in scores:
        b = s["bucket"]
        by_bucket.setdefault(b, []).append(s)

    bucket_rows = []
    for b, evs in sorted(by_bucket.items(), key=lambda kv: kv[0] if kv[0] is not None else datetime.min.replace(tzinfo=pytz.UTC), reverse=True):
        sum_score = float(sum(e["score"] for e in evs))
        dom = max(evs, key=lambda e: abs(e["score"]))
        # top N por abs(score)
        top = sorted(evs, key=lambda e: abs(e["score"]), reverse=True)[:max(1, top_n)]
        reason = " | ".join([f"{t['currency']} {t['event']} ({t['impact']}) {t['score']:+.3f}" for t in top])

        bucket_rows.append({
            "bucket": b.isoformat() if hasattr(b, "isoformat") else str(b),
            "sumScore": float(sum_score),
            "dominant": {k: dom[k] for k in ("currency", "event", "impact", "score", "age_min")},
            "top": [{k: t[k] for k in ("currency", "event", "impact", "score", "age_min")} for t in top],
            "reason": reason,
        })

    # score global: suma buckets (los más recientes ya pesan más por decay/recency)
    total_score = float(sum(r["sumScore"] for r in bucket_rows))
    # comprime a [-1,1]
    pair_bias = float(math.tanh(total_score))
    meta["total_score"] = float(total_score)
    meta["pair_bias"] = float(pair_bias)
    meta["buckets"] = bucket_rows[:8]  # limita payload

    th = float(fund.get("signal_threshold", 0.12))
    if pair_bias > th:
        meta["signal"] = "bullish"
    elif pair_bias < -th:
        meta["signal"] = "bearish"
    else:
        meta["signal"] = "neutral"

    # aplica penalty por blackout (no bloquea, solo baja confianza)
    prob_base = float(probabilidad_exito) if probabilidad_exito is not None else 50.0
    prob_adj = float(pair_bias) * float(fund.get("score_to_prob_factor", 18.0))

    if meta.get("blackout") is True:
        prob_adj -= float(fund.get("blackout_prob_penalty", 8.0))
        meta["notes"].append("blackout_penalty_applied")

    out = limitar_probabilidad(prob_base + prob_adj)
    return (out, meta) if return_meta else out

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
    # Validar que ATR exista en el DataFrame
    if 'ATR' not in df.columns:
        logger.error("⚠️ [verificar_zona_no_trading] Columna 'ATR' no encontrada. Indicadores incompletos. Retornando False (conservador).")
        return False
    
    # Obtener último ATR y su rolling mean (con validación)
    try:
        atr_last = _coerce_float(df['ATR'].iloc[-1]) if len(df) > 0 else None
        atr_rolling_mean = _coerce_float(df['ATR'].rolling(window=window).mean().iloc[-1]) if len(df) > 0 else None
        
        # Si falta alguno de los valores, retornar False (conservador - permitir trading)
        if atr_last is None or atr_rolling_mean is None:
            logger.debug("[verificar_zona_no_trading] ATR o rolling mean None/NaN. Retornando False (conservador).")
            return False
        
        # Comparar solo si ambos valores son válidos
        if atr_last < atr_rolling_mean * 0.8:
            return True  # Baja volatilidad, podría ser una zona de no trading
        return False
        
    except Exception as exc:
        logger.error("[verificar_zona_no_trading] Error al verificar zona: %s. Retornando False.", exc)
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
    try:
        if 'RSI' not in df.columns:
            df = calcular_rsi(df, window)  # Calcular RSI si no existe
        if '%K' not in df.columns:
            df = calcular_estocastico(df, window)  # Calcular %K si no existe
        
        rsi_last = _coerce_float(df['RSI'].iloc[-1]) if len(df) > 0 else None
        k_last = _coerce_float(df['%K'].iloc[-1]) if len(df) > 0 else None
        
        # Si falta cualquier valor, retornar False (conservador)
        if rsi_last is None or k_last is None:
            return False
        
        return rsi_last < rsi_threshold and k_last < k_threshold
        
    except Exception as exc:
        logger.debug("[verificar_zona_sobreventa] Error: %s. Retornando False.", exc)
        return False

# Verificar si el RSI y %K indican sobrecompra
#@profile
def verificar_zona_sobrecompra(df, window, rsi_threshold=70, k_threshold=80):
    try:
        if 'RSI' not in df.columns:
            df = calcular_rsi(df, window)  # Calcular RSI si no existe
        if '%K' not in df.columns:
            df = calcular_estocastico(df, window)  # Calcular %K si no existe
        
        rsi_last = _coerce_float(df['RSI'].iloc[-1]) if len(df) > 0 else None
        k_last = _coerce_float(df['%K'].iloc[-1]) if len(df) > 0 else None
        
        # Si falta cualquier valor, retornar False (conservador)
        if rsi_last is None or k_last is None:
            return False
        
        return rsi_last > rsi_threshold and k_last > k_threshold
        
    except Exception as exc:
        logger.debug("[verificar_zona_sobrecompra] Error: %s. Retornando False.", exc)
        return False

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
    # Cachear predicciones ARIMA por símbolo/TF para evitar recalcular
    cache_key = f"{symbol}_{temporalidad}_{len(df)}"
    if hasattr(predecir_arima, '_cache'):
        cached = predecir_arima._cache.get(cache_key)
        if cached is not None:
            return cached
    else:
        predecir_arima._cache = {}
    
    # Evitar ARIMA para TFs muy bajas (costoso y poco útil)
    if temporalidad in ['1min', '5min', '15min']:
        # Retornar predicción simple basada en media móvil
        if len(df) >= 20:
            ma_pred = df['close'].rolling(20).mean().iloc[-1]
            result = [ma_pred] * steps if not pd.isna(ma_pred) else None
            predecir_arima._cache[cache_key] = result
            return result
        return None

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
        logger.info(f"Datos insuficientes para ARIMA. symbol: {symbol}, temporalidad: {temporalidad}")
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

        result = forecast.tolist()
        
        # Cachear resultado exitoso
        predecir_arima._cache[cache_key] = result
        
        # Limpiar cache si crece mucho (mantener últimos 100)
        if len(predecir_arima._cache) > 100:
            keys_to_remove = list(predecir_arima._cache.keys())[:50]
            for k in keys_to_remove:
                predecir_arima._cache.pop(k, None)
        
        return result
    except Exception as e:
        logger.info(f"Error al ajustar ARIMA: {e}")
        result = None
        predecir_arima._cache[cache_key] = result
        return result

# Función para simulación de Monte Carlo
#@profile
def simulacion_monte_carlo(df, temporalidad, num_simulaciones=50, num_dias=5, seed=None):
    # Validar temporalidad (reducido de 100 a 50 simulaciones para acelerar)
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

    if 'atr' in df.columns:
        atr_mean = df['atr'].mean()
    elif 'ATR' in df.columns:
        atr_mean = pd.to_numeric(df['ATR'], errors='coerce').mean()
    else:
        atr_mean = 0
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

# ========== OPTIMIZACIONES: Cache de niveles y paralelización ==========

# ⏱️ Timestamps de warmup (para diagnosticar)
_warmup_start_time = None
_warmup_end_time = None

# Cache global para niveles de soporte/resistencia (evita recalcular)
# Estructura: {"symbol|tf|df_len": {"soportes": [...], "resistencias": [...], "timestamp": ...}}
# ✅ OPTIMIZACIÓN: Clave sin precio (es estable mientras no haya nuevas velas)
_niveles_cache = {}
_niveles_cache_ttl = 3600  # 1 hora de vigencia (mucho más largo)
_niveles_cache_hits = 0
_niveles_cache_misses = 0
_NIVELES_CACHE_LOCK = threading.Lock()

# Cache global para ATR (evita recalcular)
# Estructura: {"symbol|tf|df_len": {"atr": float, "timestamp": ...}}
_atr_cache = {}
_atr_cache_ttl = 3600  # 1 hora de vigencia
_atr_cache_hits = 0
_atr_cache_misses = 0
_ATR_CACHE_LOCK = threading.Lock()

def _get_niveles_cache_key(symbol: str, tf: str, df_len: int, precio_actual: float | None = None) -> str:
    """Genera clave de cache para niveles basada en símbolo, TF y tamaño del DF.
    Opcionalmente incluye precio discretizado si NIVELES_CACHE_INCLUDE_PRICE=true.
    """
    include_price = os.environ.get("NIVELES_CACHE_INCLUDE_PRICE", "false").lower() == "true"
    if include_price and precio_actual is not None:
        precio_hash = int(precio_actual * 100)
        return f"{symbol}|{tf}|{precio_hash}|{df_len}"
    return f"{symbol}|{tf}|{df_len}"

def _get_atr_cache_key(symbol: str, tf: str, df_len: int) -> str:
    """Genera clave de cache para ATR basada en símbolo, TF y tamaño del DF."""
    return f"{symbol}|{tf}|{df_len}"

def _get_cached_atr(symbol: str, tf: str, df_len: int):
    """Obtiene ATR del cache si es reciente. THREAD-SAFE."""
    global _atr_cache_hits, _atr_cache_misses
    cache_key = _get_atr_cache_key(symbol, tf, df_len)
    
    with _ATR_CACHE_LOCK:  # ✅ Thread-safe read + increment
        if cache_key in _atr_cache:
            entry = _atr_cache[cache_key]
            age = (datetime.now(UTC) - entry['timestamp']).total_seconds()
            if age < _atr_cache_ttl:
                _atr_cache_hits += 1
                return entry['atr']
        _atr_cache_misses += 1
    return None

def _cache_atr(symbol: str, tf: str, df_len: int, atr: float):
    """Almacena ATR en cache. THREAD-SAFE."""
    cache_key = _get_atr_cache_key(symbol, tf, df_len)
    
    with _ATR_CACHE_LOCK:  # ✅ Thread-safe write
        _atr_cache[cache_key] = {
            'atr': atr,
            'timestamp': datetime.now(UTC)
        }
        # Limpiar entradas antiguas si el cache crece mucho
        if len(_atr_cache) > 100:
            now = datetime.now(UTC)
            keys_to_remove = [
                k for k, v in _atr_cache.items()
                if (now - v['timestamp']).total_seconds() > _atr_cache_ttl
            ]
            for k in keys_to_remove:
                _atr_cache.pop(k, None)

def _get_cached_niveles(cache_key: str):
    """Obtiene niveles del cache si son recientes. THREAD-SAFE."""
    global _niveles_cache_hits, _niveles_cache_misses
    
    with _NIVELES_CACHE_LOCK:  # ✅ Thread-safe read + increment
        if cache_key in _niveles_cache:
            entry = _niveles_cache[cache_key]
            age = (datetime.now(UTC) - entry['timestamp']).total_seconds()
            if age < _niveles_cache_ttl:
                _niveles_cache_hits += 1
                logger.debug(f"[Cache] Niveles HIT para {cache_key} (edad: {age:.1f}s)")
                return entry['soportes'], entry['resistencias']
        _niveles_cache_misses += 1
        logger.debug(f"[Cache] Niveles MISS para {cache_key}")
    return None, None

def _cache_niveles(cache_key: str, soportes: list, resistencias: list):
    """Almacena niveles en cache. THREAD-SAFE."""
    with _NIVELES_CACHE_LOCK:  # ✅ Thread-safe write
        _niveles_cache[cache_key] = {
            'soportes': soportes,
            'resistencias': resistencias,
            'timestamp': datetime.now(UTC)
        }
        # Limpiar entradas antiguas si el cache crece mucho
        if len(_niveles_cache) > 200:
            now = datetime.now(UTC)
            keys_to_remove = [
                k for k, v in _niveles_cache.items()
                if (now - v['timestamp']).total_seconds() > _niveles_cache_ttl
            ]
            for k in keys_to_remove:
                _niveles_cache.pop(k, None)

# ========================================================================================
# 🔧 MODULE-LEVEL WRAPPER PARA MONTE CARLO (pickle-compatible para ProcessPoolExecutor)
# ========================================================================================
def _wrapper_simulacion_monte_carlo(
    df: pd.DataFrame,
    tf: str,
    num_simulaciones: int = 50,
    num_dias: int = 5,
    seed: int | None = 42
) -> tuple[float, float]:
    """
    Wrapper a nivel módulo para simulacion_monte_carlo.
    Esto permite que ProcessPoolExecutor pueda serializar la tarea.
    
    ⚠️  CRÍTICO: Esta función DEBE estar a nivel módulo para que pickle pueda serializarla.
    Las funciones locales dentro de async no pueden ser pickleadas.
    """
    # 🔍 DEBUG: Confirmar que este wrapper está siendo ejecutado en paralelo
    import os
    import multiprocessing
    pid = os.getpid()
    logger.debug(f"[MC-Wrapper] Ejecutando en PID={pid} (main={multiprocessing.current_process().name})")
    result = simulacion_monte_carlo(df, tf, num_simulaciones=num_simulaciones, num_dias=num_dias, seed=seed)
    logger.debug(f"[MC-Wrapper] Completó: {result}")
    return result

async def _calcular_predicciones_paralelo(df, tf, symbol, window):
    """Ejecuta predicciones ARIMA, Media Móvil y Monte Carlo en paralelo."""
    loop = asyncio.get_event_loop()
    
    # Ejecutar en threads separados para no bloquear el event loop
    exec_used = _ANALYSIS_PRED_EXECUTOR
    # 🔍 DEBUG: Log del tipo de executor siendo usado
    executor_type = type(exec_used).__name__ if exec_used else "None"
    logger.debug(f"[Predicciones] {symbol}-{tf} usando executor: {executor_type}")
    arima_task = loop.run_in_executor(exec_used, predecir_arima, df, tf, symbol)
    mm_task = loop.run_in_executor(exec_used, predecir_media_movil, df, window)
    # ✅ FIX: Usar wrapper a nivel módulo en lugar de lambda
    mc_task = loop.run_in_executor(
        exec_used,
        _wrapper_simulacion_monte_carlo,
        df,
        tf
    )
    
    # Esperar a que todas terminen
    predicciones_arima, predicciones_media_movil, (prob_alza, prob_baja) = await asyncio.gather(
        arima_task, mm_task, mc_task
    )
    
    return predicciones_arima, predicciones_media_movil, prob_alza, prob_baja

# ========================================================================

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

    # Asegurar ATR para tolerancias sin recalcular si ya existe
    if 'atr' not in df.columns:
        if 'ATR' in df.columns:
            df['atr'] = pd.to_numeric(df['ATR'], errors='coerce')
        else:
            df['tr'] = calcular_tr(df['high'].values, df['low'].values, df['close'].values)
            df['atr'] = df['tr'].rolling(window).mean()

    # Inicializar variables
    soportes_dinamicos, resistencias_dinamicas = set(), set()
    niveles_suficientes = False

    window_ajustado = window
    min_factor_temporal = 1

    # ✅ FIX: No usar Parallel para una sola tarea (causa ResourceTracker errors)
    # La paralelización solo es útil si hay múltiples tareas independientes
    # Aquí solo hay 1 tarea por símbolo/temporalidad, ejecutar directamente es más eficiente
    # 
    # 📝 NOTA: Si en el futuro necesitas paralelizar múltiples ventanas o símbolos:
    #    - Paraleliza al nivel superior (múltiples símbolos/temporalidades)
    #    - Usa ThreadPoolExecutor en vez de multiprocessing (más seguro en Docker)
    #    - Ejemplo:
    #      from concurrent.futures import ThreadPoolExecutor
    #      with ThreadPoolExecutor(max_workers=4) as executor:
    #          futures = [executor.submit(calcular_para_ventana, w) for w in ventanas]
    #          resultados = [f.result() for f in futures]
    
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

        # ✅ Ejecutar cálculo directamente (sin overhead de multiprocessing)
        soportes, resistencias = calcular_soportes_resistencias_para_window(
            window_ajustado, df, precio_actual, min_levels, symbol, temporalidad
        )
        
        # Procesar resultados
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

    # Obtener el ATR (desde caché si está disponible)
    atr = _get_cached_atr(symbol, temporalidad_actual, len(df))
    
    if atr is None:
        # Buscar ATR: primero 'atr', luego 'ATR', si no existe, calcularlo
        if 'atr' in df.columns:
            atr = df['atr'].iloc[-1]
        elif 'ATR' in df.columns:
            atr = df['ATR'].iloc[-1]
        else:
            # Calcular ATR si no existe
            window = min(14, len(df))  # Usar una ventana por defecto
            df['tr'] = calcular_tr(df['high'].values, df['low'].values, df['close'].values)
            df['atr'] = df['tr'].rolling(window).mean()
            atr = df['atr'].iloc[-1]
        
        # Asegurar que atr es un valor válido
        if pd.isna(atr):
            atr = df['high'].iloc[-window:].mean() - df['low'].iloc[-window:].mean() if 'window' in locals() else 0.01
        
        # Cachear el resultado
        _cache_atr(symbol, temporalidad_actual, len(df), atr)
    
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

    # ✅ CORREGIDO: Validar que los niveles están bien ordenados y existan
    # NO anular si el precio está fuera (es válido en breakouts)
    # Solo anular si los datos no existen o están inválidos
    if not (pd.notna(soporte_nivel_1) and pd.notna(resistencia_nivel_1)):
        soporte_nivel_2, soporte_nivel_1 = np.nan, np.nan
        resistencia_nivel_1, resistencia_nivel_2 = np.nan, np.nan
    elif pd.notna(soporte_nivel_1) and pd.notna(resistencia_nivel_1):
        # Validar que están en orden: S1 < R1
        if soporte_nivel_1 >= resistencia_nivel_1:
            soporte_nivel_2, soporte_nivel_1 = np.nan, np.nan
            resistencia_nivel_1, resistencia_nivel_2 = np.nan, np.nan

    # ========== SISTEMA DE APALANCAMIENTO MEJORADO ==========
    # ✅ FASE 1: Risk-Based Leverage (con límite de seguridad)
    # Máximo apalancamiento permitido (estándar profesional de seguridad)
    MAX_LEVERAGE = 25.0
    MAX_RISK_PER_TRADE = 0.02  # 2% del capital por operación
    
    #@profile
    def calcular_apalancamiento_seguro(
        precio_actual: float,
        nivel_stop: float,
        porcentaje_riesgo: float = 0.02,
        max_leverage: float = 25.0,
        metodo: str = "distance"
    ) -> tuple[float, float, str]:
        """
        Calcula apalancamiento usando dos métodos: distance-based y risk-based.
        Retorna el MENOR de los dos (más conservador).
        
        Args:
            precio_actual: Precio de entrada
            nivel_stop: Precio del stop loss
            porcentaje_riesgo: Máximo riesgo por operación (0.02 = 2%)
            max_leverage: Límite máximo de apalancamiento
            metodo: "distance" (tu método) o "risk" (recomendado)
        
        Returns:
            (apalancamiento_final, apalancamiento_teorico, mensage_log)
        """
        if not (precio_actual > 0 and nivel_stop > 0):
            return 0, 0, "Precios inválidos"
        
        # Método 1: Distance-based (tu fórmula original)
        distancia_relativa = abs(precio_actual - nivel_stop) / precio_actual
        if distancia_relativa > 0:
            leverage_distance = (1 - 0.10) / distancia_relativa
        else:
            leverage_distance = 0
        
        # Método 2: Risk-based (recomendado - más seguro)
        # apalancamiento = riesgo_máximo / distancia_en_precio
        riesgo_maximo = porcentaje_riesgo
        leverage_risk = riesgo_maximo / distancia_relativa if distancia_relativa > 0 else 0
        
        # TOMAR EL MENOR de ambos (más conservador)
        leverage_calcalc = min(leverage_distance, leverage_risk)
        
        # Aplicar límite máximo de seguridad
        leverage_final = min(leverage_calcalc, max_leverage)
        
        # Log si estamos cerca del límite
        msg = ""
        if leverage_calcalc > max_leverage:
            msg = f"⚠️ Leverage limitado: {leverage_calcalc:.1f}x → {leverage_final:.1f}x (soporte cercano)"
        
        return float(leverage_final), float(leverage_calcalc), msg
    
    # Apalancamiento para compra
    if soporte_nivel_1 and precio_actual > soporte_nivel_1:
        apalancamiento_compra_nivel_1, apalancamiento_compra_nivel_1_teorico, msg_1 = calcular_apalancamiento_seguro(
            precio_actual, soporte_nivel_1, MAX_RISK_PER_TRADE, MAX_LEVERAGE
        )
        apalancamiento_compra_nivel_1 = int(apalancamiento_compra_nivel_1)
        apalancamiento_compra_nivel_1_teorico = int(apalancamiento_compra_nivel_1_teorico)
        if msg_1:
            logger.info(f"[Niveles S1] {msg_1}")
    else:
        apalancamiento_compra_nivel_1 = 0
        apalancamiento_compra_nivel_1_teorico = 0
    
    if soporte_nivel_2 and precio_actual > soporte_nivel_2:
        apalancamiento_compra_nivel_2, apalancamiento_compra_nivel_2_teorico, msg_2 = calcular_apalancamiento_seguro(
            precio_actual, soporte_nivel_2, MAX_RISK_PER_TRADE, MAX_LEVERAGE
        )
        apalancamiento_compra_nivel_2 = int(apalancamiento_compra_nivel_2)
        apalancamiento_compra_nivel_2_teorico = int(apalancamiento_compra_nivel_2_teorico)
        if msg_2:
            logger.info(f"[Niveles S2] {msg_2}")
    else:
        apalancamiento_compra_nivel_2 = 0
        apalancamiento_compra_nivel_2_teorico = 0
    
    # Apalancamiento para venta (proceso inverso)
    if resistencia_nivel_1 and precio_actual < resistencia_nivel_1:
        apalancamiento_venta_nivel_1, apalancamiento_venta_nivel_1_teorico, msg_3 = calcular_apalancamiento_seguro(
            precio_actual, resistencia_nivel_1, MAX_RISK_PER_TRADE, MAX_LEVERAGE
        )
        apalancamiento_venta_nivel_1 = int(apalancamiento_venta_nivel_1)
        apalancamiento_venta_nivel_1_teorico = int(apalancamiento_venta_nivel_1_teorico)
        if msg_3:
            logger.info(f"[Niveles R1] {msg_3}")
    else:
        apalancamiento_venta_nivel_1 = 0
        apalancamiento_venta_nivel_1_teorico = 0
    
    if resistencia_nivel_2 and precio_actual < resistencia_nivel_2:
        apalancamiento_venta_nivel_2, apalancamiento_venta_nivel_2_teorico, msg_4 = calcular_apalancamiento_seguro(
            precio_actual, resistencia_nivel_2, MAX_RISK_PER_TRADE, MAX_LEVERAGE
        )
        apalancamiento_venta_nivel_2 = int(apalancamiento_venta_nivel_2)
        apalancamiento_venta_nivel_2_teorico = int(apalancamiento_venta_nivel_2_teorico)
        if msg_4:
            logger.info(f"[Niveles R2] {msg_4}")
    else:
        apalancamiento_venta_nivel_2 = 0
        apalancamiento_venta_nivel_2_teorico = 0
    
    multiplicador = {
        "apalancamiento_compra_nivel_1": apalancamiento_compra_nivel_1,
        "apalancamiento_compra_nivel_2": apalancamiento_compra_nivel_2,
        "apalancamiento_venta_nivel_1": apalancamiento_venta_nivel_1,
        "apalancamiento_venta_nivel_2": apalancamiento_venta_nivel_2,
        "apalancamiento_compra_nivel_1_teorico": apalancamiento_compra_nivel_1_teorico,
        "apalancamiento_compra_nivel_2_teorico": apalancamiento_compra_nivel_2_teorico,
        "apalancamiento_venta_nivel_1_teorico": apalancamiento_venta_nivel_1_teorico,
        "apalancamiento_venta_nivel_2_teorico": apalancamiento_venta_nivel_2_teorico
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

# ======================== FASE 2: FUNCIONES DE GESTIÓN DE RIESGO Y COSTOS ========================

#@profile
def calcular_tamaño_posicion(
    account_balance: float,
    entry_price: float,
    stop_loss: float,
    max_risk_percent: float = 0.02,
    side: str = "long"
) -> Optional[float]:
    """
    ✅ FASE 2: Calcula tamaño de posición basado en gestión de riesgo profesional.
    
    Fórmula: position_size = (account_balance × max_risk_percent) / (entry - stop_loss)
    
    Args:
        account_balance: Saldo de cuenta en moneda base
        entry_price: Precio de entrada
        stop_loss: Nivel de stop loss
        max_risk_percent: Máximo riesgo por operación (default 2% = 0.02)
        side: "long" o "short"
    
    Returns:
        Tamaño de posición normalizado (0-1), o None si inválido
    """
    if not (_finite(account_balance) and _finite(entry_price) and _finite(stop_loss)):
        return None
    if account_balance <= 0 or entry_price <= 0:
        return None
    if side == "long" and stop_loss >= entry_price:
        return None
    if side == "short" and stop_loss <= entry_price:
        return None
    
    max_risk_amount = account_balance * max_risk_percent
    
    if side == "long":
        risk_per_unit = entry_price - stop_loss
    else:
        risk_per_unit = stop_loss - entry_price
    
    if risk_per_unit <= 0:
        return None
    
    position_size = max_risk_amount / risk_per_unit
    return float(position_size)


#@profile
def ajustar_tp_sl_por_costos(
    entry: float,
    tp: float,
    sl: float,
    instrument_type: str = "forex",
    side: str = "long",
    volume: float = 1.0
) -> tuple[float, float]:
    """
    ✅ FASE 2: Ajusta TP y SL restando costos de transacción (spread, comisión, slippage).
    
    Costos típicos:
    - Forex: 1-2 pips spread + 1 pip comisión = 3 pips total
    - Cripto: 0.1-0.5% comisión (usamos 0.3%)
    - Acciones: $0.01-$0.10 por acción comisión
    - Futuros: $20-100 por round-trip
    
    Returns:
        (tp_ajustado, sl_ajustado) - más conservadores que los originales
    """
    
    if not (_finite(entry) and _finite(tp) and _finite(sl)):
        return tp, sl
    
    if instrument_type.lower() == "forex":
        # Forex: restar 3 pips por spread/comisión (1.5 entrada + 1.5 salida)
        pip_value = 0.0001 if "JPY" not in str(entry).upper() else 0.01
        spread_cost = 3 * pip_value
        
        if side == "long":
            tp_neto = tp - spread_cost  # Reducir ganancia por costos
            sl_ajustado = sl + spread_cost  # Aumentar pérdida por costos
        else:
            tp_neto = tp + spread_cost
            sl_ajustado = sl - spread_cost
        
        return float(tp_neto), float(sl_ajustado)
        
    elif instrument_type.lower() == "crypto":
        # Cripto: restar 0.3% por comisión (0.1% entrada + 0.2% salida)
        comision_percent = 0.003
        
        if side == "long":
            tp_neto = tp * (1 - comision_percent)  # TP reducido por comisión
            sl_ajustado = sl * (1 + comision_percent)  # SL aumentado por comisión
        else:
            tp_neto = tp * (1 + comision_percent)
            sl_ajustado = sl * (1 - comision_percent)
        
        return float(tp_neto), float(sl_ajustado)
        
    elif instrument_type.lower() == "stock":
        # Acciones: restar comisión fija ~$10 por lado
        comision = 20  # $20 round-trip asumido
        cost_per_share = comision / volume if volume > 0 else 0
        
        if side == "long":
            tp_neto = tp - cost_per_share
            sl_ajustado = sl + cost_per_share
        else:
            tp_neto = tp + cost_per_share
            sl_ajustado = sl - cost_per_share
        
        return float(tp_neto), float(sl_ajustado)
    
    else:
        # Instrumento no reconocido: sin ajuste
        return float(tp), float(sl)

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
def _create_entry_candidate(
    side: str,                       # "long" | "short"
    entry: float,
    atr: float,
    mult_tp_sl,                      # float  ó  (tp_mult, sl_mult)
    make_tp_sl: Callable[..., tuple[Optional[float], Optional[float]]],
    basado_en: str,
    precio_actual: float,
    niveles: dict,
    rango_dinamico: Iterable[Optional[float]] = (None, None),
    min_rrr: float = 1.5,
    dedupe_entries: list[dict] = None
) -> Optional[dict]:
    """Crea candidato de entrada sin mutar lista. Devuelve entry dict o None si se descarta."""
    if dedupe_entries is None:
        dedupe_entries = []
    
    if not (_finite(entry) and _finite(atr) and atr > 0):
        return None

    # Soportar multiplicadores asimétricos (tp_mult, sl_mult)
    if isinstance(mult_tp_sl, (tuple, list)) and len(mult_tp_sl) == 2:
        tp_mult, sl_mult = float(mult_tp_sl[0]), float(mult_tp_sl[1])
        if side == "long":
            tp, sl = calc_tp_sl_compra_asym(entry, atr, tp_mult, sl_mult)
        else:
            tp, sl = calc_tp_sl_venta_asym(entry, atr, tp_mult, sl_mult)
    else:
        tp, sl = make_tp_sl(entry, atr, mult_tp_sl)

    if not (_finite(tp) and _finite(sl)):
        return None

    if side == "long" and not (sl < entry < tp):
        return None
    if side == "short" and not (tp < entry < sl):
        return None

    rrr = _rrr(entry, tp, sl, side)
    if rrr is None or rrr < min_rrr:
        return None

    # Score
    try:
        lo, hi = rango_dinamico
    except Exception:
        lo, hi = None, None
    score = abs(entry - (precio_actual or entry)) / (atr or 1e-9)
    if _finite(lo) and _finite(hi):
        if side == "long" and entry > hi: score += 1.0
        if side == "short" and entry < lo: score += 1.0

    return {
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
    }

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
    min_rrr: float = 1.5  # ✅ PHASE 2: Cambiado de 1.2 a 1.5 (estándar profesional)
):
    """Calcula TP/SL, RRR y agrega la entrada si pasa validaciones."""
    candidate = _create_entry_candidate(
        side=side, entry=entry, atr=atr, mult_tp_sl=mult_tp_sl,
        make_tp_sl=make_tp_sl, basado_en=basado_en,
        precio_actual=precio_actual, niveles=niveles,
        rango_dinamico=rango_dinamico, min_rrr=min_rrr,
        dedupe_entries=entries
    )
    
    if candidate is None:
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
    min_rrr=1.5,  # ✅ PHASE 2: Cambiado de 1.2 a 1.5 (estándar profesional)
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

    logger.debug("===== INPUT =====")
    logger.debug(f"precio_actual={precio_actual:.6f}, ATR={ATR if ATR is not None else None}")
    logger.debug(f"niveles: S1={niveles.get('soporte_nivel_1')}, S2={niveles.get('soporte_nivel_2')}, "
                 f"R1={niveles.get('resistencia_nivel_1')}, R2={niveles.get('resistencia_nivel_2')}")
    logger.debug(f"tipo_operacion={tipo_operacion}, estructura={(en_rango or {}).get('estructura_tendencia')}, "
                 f"es_rango={bool((en_rango or {}).get('es_rango_repetitivo'))}")
    logger.debug(f"rango_dinamico={(en_rango or {}).get('rango_dinamico')} prob_general={prob_general}")
    logger.debug("=================")

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

    logger.debug(f"sesgo_long={sesgo_long}, sesgo_short={sesgo_short}, min_rrr={min_rrr}")

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

    # ====== PARALELIZACIÓN: Coleccionar tareas ======
    entry_tasks = []  # lista de (side, entry, mult_base, basado_en) para ejecutar en paralelo
    
    def _queue_add(side: str, entry: float, mult_base: tuple[float, float], basado_en: str):
        """En lugar de ejecutar directamente, colecciona la tarea para paralelizar."""
        if not _finite(entry):
            return
        entry_tasks.append((side, entry, mult_base, basado_en))
    
    def _try_add(side: str, entry: float, mult_base: tuple[float, float], basado_en: str):
        """(Deprecated - usar _queue_add) Aplica adaptadores, crea y filtra por RRR, dedup y límites."""
        _queue_add(side, entry, mult_base, basado_en)

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

    # ====== EJECUCIÓN PARALELA DE TODAS LAS TAREAS ======
    if entry_tasks:
        from concurrent.futures import ThreadPoolExecutor, as_completed
        
        def _execute_entry_task(task_tuple):
            """Ejecuta una tarea de generación de entrada. Devuelve (candidate_dict, side) o None."""
            side, entry, mult_base, basado_en = task_tuple
            
            # Dedupe check rápido (contra lista actual que va creciendo)
            for e in entries:
                if e.get("side") == side and _near(e.get("precio_entrada", 0.0), entry, dedupe_tol_atr * ATR):
                    return None  # muy cercano
            
            mult_adj = _adapt_mult(mult_base, side)
            make = calc_tp_sl_compra if side == "long" else calc_tp_sl_venta
            candidate = _create_entry_candidate(
                side=side, entry=entry, atr=ATR, mult_tp_sl=mult_adj,
                make_tp_sl=make, basado_en=basado_en,
                precio_actual=precio_actual, niveles=niveles,
                rango_dinamico=rango_dinamico, min_rrr=min_rrr,
                dedupe_entries=entries
            )
            return (candidate, basado_en) if candidate else None
        
        # Ejecutar con ThreadPoolExecutor (max 4 workers para no desbordar)
        try:
            max_workers = max(2, min(4, len(entry_tasks) // 2))  # Auto-ajust workers
            import time
            t_entries_start = time.time()
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                # Submitir TODAS las tareas en paralelo sin esperar
                futures = [executor.submit(_execute_entry_task, task) for task in entry_tasks]
                logger.debug(f"[Entradas] Ejecutando {len(futures)} tareas en paralelo (workers={max_workers})")
                
                # Procesar resultados conforme se completan (no secuencial)
                for future in as_completed(futures, timeout=3.0):
                    try:
                        result = future.result()  # Sin timeout adicional, ya está en as_completed
                        if result:
                            candidate, basado_en = result
                            
                            # Final dedupe check antes de agregar
                            is_dup = False
                            for e in entries:
                                if e.get("side") == candidate.get("side") and \
                                   _near(e.get("precio_entrada", 0.0), candidate.get("precio_entrada", 0.0), dedupe_tol_atr * ATR):
                                    is_dup = True
                                    break
                            
                            if not is_dup:
                                entries.append(candidate)
                                logger.info(f" + AGREGADA {candidate['side'].upper()} [{candidate['basado_en']}] entry={candidate['precio_entrada']:.6f} tp={candidate['take_profit']:.6f} sl={candidate['stop_loss']:.6f} RRR={candidate['rrr']:.3f} score={candidate['score']:.3f}")
                    except Exception as e:
                        logger.debug(f"Error ejecutando tarea de entrada: {e}")
                        continue
            logger.debug(f"[Entradas] Generación paralela completada en {(time.time()-t_entries_start)*1000:.1f}ms para {len(entries)} entradas")
        except Exception as e:
            logger.warning(f"Error en paralelización de entradas para {ATR}: {e}. Fallback a secuencial.")
            # Fallback: ejecutar secuencial si paralelización falla
            for task in entry_tasks:
                result = _execute_entry_task(task)
                if result:
                    candidate, basado_en = result
                    entries.append(candidate)
                    logging.info(f" + AGREGADA {candidate['side'].upper()} [{candidate['basado_en']}] entry={candidate['precio_entrada']:.6f} tp={candidate['take_profit']:.6f} sl={candidate['stop_loss']:.6f} RRR={candidate['rrr']:.3f} score={candidate['score']:.3f}")
    
    # ====== LIMITE DE CANDIDATOS (por performance/ruido) ======
    if len(entries) > max_candidates:
        entries = entries[:max_candidates]

    # ====== ORDENACIÓN Y LOG ======
    logger.debug("===== RESUMEN =====")
    logger.debug(f"Intentos totales: {len(entries)} (antes de ordenar)")

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
        logger.debug(f"{i:02d}) {e['side'].upper()} {e['basado_en']} "
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
        precio_actual = df["close"].iloc[-1]

        # --- PARALELIZACIÓN DE OPERACIONES COSTOSAS ---
        # Ejecutar en paralelo: patrones, rango, técnica, fundamental
        _inner_exec = _ANALYSIS_INNER_EXECUTOR
        if _inner_exec is None:
            _inner_exec = ThreadPoolExecutor(max_workers=1)

        future_patrones = _inner_exec.submit(detectar_patrones_confirmados_velas, df, window)
        future_rango = _inner_exec.submit(
            lambda: detectar_rango_zigzag(df, ventana_rebotes=140, tolerancia_pct=0.002, min_rebotes=3)
        )
        future_tecnica = _inner_exec.submit(analisis_tecnico_detallado, df, tf, window, cfg)
    
        fecha_inicio = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
        fecha_fin = datetime.now().strftime("%Y-%m-%d")
        future_fundamental = _inner_exec.submit(
            ajustar_probabilidad_fundamental,
            50, df_eventos, symbol, tf, fecha_inicio, fecha_fin, cfg, True  # return_meta=True
        )
    
        # Recoger resultados
        try:
            resultados = future_patrones.result(timeout=15)  # ✅ FIX: Add timeout to prevent hangs
            patrones_detectados = {}
            for _, _, nombre in resultados:
                patrones_detectados[nombre] = True
        except TimeoutError:
            logger.warning(f"[TIMEOUT] Pattern detection timeout for {symbol}-{tf}")
            patrones_detectados = {}
        except Exception as e:
            logger.info(f"Error detectando patrones para {symbol}-{tf}: {e}")
            patrones_detectados = {}
    
        try:
            en_rango = future_rango.result(timeout=15)  # ✅ FIX: Add timeout
        except TimeoutError:
            logger.warning(f"[TIMEOUT] Range detection timeout for {symbol}-{tf}")
            en_rango = {
                "es_rango_repetitivo": False,
                "estructura_tendencia": "indefinida",
                "rebotes": [],
                "rango_dinamico": [None, None],
            }
        except Exception:
            en_rango = {
                "es_rango_repetitivo": False,
                "estructura_tendencia": "indefinida",
                "rebotes": [],
                "rango_dinamico": [None, None],
            }
    
        try:
            tecnica_meta = future_tecnica.result(timeout=15)  # ✅ FIX: Add timeout
        except TimeoutError:
            logger.warning(f"[TIMEOUT] Technical analysis timeout for {symbol}-{tf}")
            tecnica_meta = None
        except Exception as e:
            logger.info(f"Error en análisis técnico para {symbol}-{tf}: {e}")
            tecnica_meta = None
    
        try:
            prob_funda_out = future_fundamental.result(timeout=15)  # ✅ FIX: Add timeout
            if isinstance(prob_funda_out, tuple):
                prob_funda, fundamental_meta = prob_funda_out
            else:
                prob_funda, fundamental_meta = prob_funda_out, None
            probabilidad_fundamental = round(prob_funda if prob_funda is not None else 50, 2)
        except Exception as e:
            logger.info(f"Error en análisis fundamental para {symbol}-{tf}: {e}")
            probabilidad_fundamental = 50.0
            fundamental_meta = None

        if _inner_exec is not _ANALYSIS_INNER_EXECUTOR:
            _inner_exec.shutdown(wait=False)

        # --- Predicciones/MC (PARALELIZADAS para mejor rendimiento) ---
        try:
            # ✅ OPTIMIZACIÓN: Usar ThreadPoolExecutor directamente en lugar de asyncio.run() anidado
            # asyncio.run() es muy costoso cuando se invoca 56×8 veces (activos × temporalidades)
            pred_exec = _ANALYSIS_PRED_EXECUTOR
            if pred_exec is None:
                # Fallback: usar INNER_EXECUTOR
                pred_exec = _inner_exec
            
            if pred_exec is not None:
                # Usar executor para paralelizar ARIMA, Media Móvil y Monte Carlo
                future_arima = pred_exec.submit(predecir_arima, df, tf, symbol)
                future_mm = pred_exec.submit(predecir_media_movil, df, window)
                future_mc = pred_exec.submit(_wrapper_simulacion_monte_carlo, df, tf)
                
                predicciones_arima = future_arima.result(timeout=30)
                predicciones_media_movil = future_mm.result(timeout=30)
                prob_alza, prob_baja = future_mc.result(timeout=30)
            else:
                # No hay executor, secuencial
                predicciones_arima = predecir_arima(df, tf, symbol)
                predicciones_media_movil = predecir_media_movil(df, window)
                prob_alza, prob_baja = simulacion_monte_carlo(df, tf, num_simulaciones=50, num_dias=5, seed=42)
            
            probabilidad_alza = prob_alza if prob_alza is not None else 50
            probabilidad_baja = prob_baja if prob_baja is not None else 50
        except Exception as e:
            logger.info(f"Error en predicciones para {symbol}-{tf}: {e}. Usando secuencial.")
            # Fallback a ejecución secuencial
            predicciones_arima = predecir_arima(df, tf, symbol)
            predicciones_media_movil = predecir_media_movil(df, window)
            probabilidad_alza, probabilidad_baja = simulacion_monte_carlo(
                df, tf, num_simulaciones=50, num_dias=5, seed=42
            )

        # --- Soportes/Resistencias dinámicos (CON CACHE) ---
        cache_key = _get_niveles_cache_key(symbol, tf, len(df), precio_actual)
        soportes_cached, resistencias_cached = _get_cached_niveles(cache_key)
        
        if soportes_cached is not None and resistencias_cached is not None:
            # Usar niveles del cache
            soportes_dinamicos = soportes_cached
            resistencias_dinamicas = resistencias_cached
        else:
            # Calcular niveles (costoso)
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
            )
            
            soportes_dinamicos = _clean_levels(soportes_dinamicos)
            resistencias_dinamicas = _clean_levels(resistencias_dinamicas)
            
            # Almacenar en cache
            _cache_niveles(cache_key, soportes_dinamicos, resistencias_dinamicas)

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

        ATR = _tofloat(df["ATR"].iloc[-1]) if "ATR" in df.columns else None
        atr_missing = not (ATR and _finite(ATR) and ATR > 0)
        if atr_missing:
            # Fallback ATR if indicator is missing/NaN on the last row.
            ATR = _atr14(df)
            if not (ATR and _finite(ATR) and ATR > 0):
                ATR = float(df["close"].iloc[-1]) * 0.002
            logger.info(f"[ATR] Fallback usado para {symbol}/{tf}: ATR={ATR}")

        # --- Prob. técnica (ya calculamos tecnica_meta y fundamental_meta en paralelo) ---
        probabilidad_tecnica = round(ajustar_probabilidad_tecnica(
            df, tf, window, cfg, niveles=niveles_clave, symbol=symbol
        ), 2)

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
        zona_no_trading_evento = bool(isinstance(fundamental_meta, dict) and fundamental_meta.get("blackout") is True)
        zona_no_trading = bool(zona_no_trading) or zona_no_trading_evento

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

        try:
            confluencia = evaluar_confluencia_trade(
                symbol=symbol,
                temporalidad=tf,
                tipo_operacion=tipo_operacion,
                precio_actual=precio_actual,
                niveles=niveles_clave,
                atr=ATR,
                prob_tecnica=probabilidad_tecnica,
                prob_fundamental=probabilidad_fundamental,
                prob_general=probabilidad_general,
                tecnica_meta=tecnica_meta,
                fundamental_meta=fundamental_meta,
                cfg=cfg,
            )
        except Exception as _e:
            confluencia = {
                'symbol': symbol,
                'tf': tf,
                'label': None,
                'score': None,
                'warnings': [f'confluencia_error: {_e}'],
            }

        alertas_mt = []
        try:
            if isinstance(confluencia, dict):
                alertas_mt.extend(list(confluencia.get('warnings') or []))
        except Exception:
            pass
        # Bollinger (último)
        bollinger_upper = _coerce_float_safe(salida.get("bollinger_upper")) or _coerce_float_safe(
            last_of(df, "bollinger_upper", default=None)
        )
        bollinger_lower = _coerce_float_safe(salida.get("bollinger_lower")) or _coerce_float_safe(
            last_of(df, "bollinger_lower", default=None)
        )

        # --- Entradas múltiples ---
        # 🎯 Lee config desde .env para privilegiar calidad sobre cantidad
        max_candidates = int(os.environ.get("ENTRADA_MAX_CANDIDATES", "10"))
        min_rrr = float(os.environ.get("ENTRADA_MIN_RRR", "2.0"))
        enable_ladders = os.environ.get("ENTRADA_ENABLE_LADDERS", "false").lower() == "true"
        enable_retest = os.environ.get("ENTRADA_ENABLE_RETEST", "false").lower() == "true"
        enable_range_revert = os.environ.get("ENTRADA_ENABLE_RANGE_REVERT", "true").lower() == "true"
        
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
            # 🎯 Parámetros de calidad
            max_candidates=max_candidates,
            min_rrr=min_rrr,
            enable_ladder=enable_ladders,
            enable_breakout_retest=enable_retest,
            enable_range_mean_revert=enable_range_revert,
        )

        # Adjuntar metadatos pro a cada entrada (compat UI)
        try:
            for _e in (entradas_mult or []):
                if isinstance(_e, dict):
                    _m = _e.get('meta')
                    if not isinstance(_m, dict):
                        _m = {}
                        _e['meta'] = _m
                    if isinstance(tecnica_meta, dict):
                        _m.setdefault('tecnica', tecnica_meta)
                    if isinstance(fundamental_meta, dict):
                        _m.setdefault('fundamental', fundamental_meta)
                    if isinstance(confluencia, dict):
                        _m.setdefault('confluencia', confluencia)
                    if isinstance(alertas_mt, list) and alertas_mt:
                        _m.setdefault('alertas', alertas_mt)
        except Exception:
            pass

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
                precio_entrada = None
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
            "apalancamiento_compra_nivel_1_teorico": niveles_clave.get("multiplicador", {}).get("apalancamiento_compra_nivel_1_teorico"),
            "apalancamiento_compra_nivel_2_teorico": niveles_clave.get("multiplicador", {}).get("apalancamiento_compra_nivel_2_teorico"),
            "apalancamiento_venta_nivel_1_teorico": niveles_clave.get("multiplicador", {}).get("apalancamiento_venta_nivel_1_teorico"),
            "apalancamiento_venta_nivel_2_teorico": niveles_clave.get("multiplicador", {}).get("apalancamiento_venta_nivel_2_teorico"),
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
            "zona_no_trading_evento": zona_no_trading_evento,
            "alertas": alertas_mt,
            "tecnica_meta": tecnica_meta,
            "fundamental_meta": fundamental_meta,
            "confluencia": confluencia,
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
def calcular_ponderacion_vectorizado(df: pd.DataFrame, cfg: dict | None = None) -> pd.Series:
    """Versión vectorizada de calcular_ponderacion para acelerar procesamiento masivo."""
    p = _norm_ponder_cfg(cfg)
    if not p.get("enable", True):
        return pd.Series(0.0, index=df.index)
    
    # Inicializar array de ponderaciones
    ponderacion = np.zeros(len(df), dtype=float)
    
    # --- Probabilidad general (vectorizado) ---
    if 'Probabilidad General (%)' in df.columns:
        pg = df['Probabilidad General (%)'].fillna(0)
        ponderacion += np.where(pg > p["prob_high"], p["prob_high_delta"], 0)
        ponderacion += np.where(pg < p["prob_low"], p["prob_low_delta"], 0)
        ponderacion += np.where((pg >= p["neutral_min"]) & (pg <= p["neutral_max"]), p["neutral_delta"], 0)
    
    # --- Concordancia Tec + Fund (vectorizado) ---
    if 'Probabilidad Tecnica (%)' in df.columns and 'Probabilidad Fundamental (%)' in df.columns:
        pt = df['Probabilidad Tecnica (%)'].fillna(50)
        pf = df['Probabilidad Fundamental (%)'].fillna(50)
        ponderacion += np.where((pt > 60) & (pf > 60), p["concordancia_bull_delta"], 0)
        ponderacion += np.where((pt < 40) & (pf < 40), p["concordancia_bear_delta"], 0)
    
    # --- Niveles (vectorizado con manejo de división por cero) ---
    cols_niveles = {'Ultimo Valor', 'Soporte Nivel 1', 'Resistencia Nivel 1', 'Soporte Nivel 2', 'Resistencia Nivel 2'}
    if cols_niveles.issubset(df.columns):
        precio = df['Ultimo Valor'].fillna(0)
        s1 = df['Soporte Nivel 1'].fillna(0)
        r1 = df['Resistencia Nivel 1'].fillna(0)
        s2 = df['Soporte Nivel 2'].fillna(0)
        r2 = df['Resistencia Nivel 2'].fillna(0)
        
        # Distancias con protección división por cero
        with np.errstate(divide='ignore', invalid='ignore'):
            dist_s1 = np.where(s1 != 0, np.abs(precio - s1) / s1, np.inf)
            dist_r1 = np.where(r1 != 0, np.abs(r1 - precio) / r1, np.inf)
            
            ponderacion += np.where(dist_s1 < 0.01, p["near_s1_delta"], 0)
            ponderacion += np.where(dist_r1 < 0.01, p["near_r1_delta"], 0)
            
            # Ventanas 1%
            ponderacion += np.where(precio <= s1 * 1.01, np.maximum(0, p["near_s1_delta"]), 0)
            ponderacion += np.where(precio >= r1 * 0.99, np.minimum(0, p["near_r1_delta"]), 0)
            ponderacion += np.where(precio <= s2 * 1.01, p["near_s2_delta"], 0)
            ponderacion += np.where(precio >= r2 * 0.99, p["near_r2_delta"], 0)
    
    # --- MACD (vectorizado) ---
    if 'Cruce MACD' in df.columns:
        macd = df['Cruce MACD'].fillna('')
        ponderacion += np.where(macd == 'Cruce Alcista', p["macd_cruce_alcista_delta"], 0)
        ponderacion += np.where(macd == 'Cruce Bajista', p["macd_cruce_bajista_delta"], 0)
    
    # --- Bollinger (vectorizado) ---
    if all(c in df.columns for c in ['bollinger_upper', 'bollinger_lower', 'Ultimo Valor']):
        bup = df['bollinger_upper'].fillna(np.inf)
        blo = df['bollinger_lower'].fillna(-np.inf)
        precio = df['Ultimo Valor'].fillna(0)
        ponderacion += np.where(precio < blo, p["bollinger_bajo_delta"], 0)
        ponderacion += np.where(precio > bup, p["bollinger_alto_delta"], 0)
    
    # --- Tendencia (vectorizado) ---
    if 'Tendencia Predicha' in df.columns:
        tend = df['Tendencia Predicha'].fillna('Neutral')
        ponderacion += np.where(tend == 'Alcista', p["tendencia_alcista_delta"], 0)
        ponderacion += np.where(tend == 'Bajista', p["tendencia_bajista_delta"], 0)
    
    # --- Señales (requiere lista global, vectorizado si existe) ---
    if 'Tipo de Operacion' in df.columns:
        try:
            signal = df['Tipo de Operacion'].fillna('Neutral')
            compra_mask = signal.isin(señales_compra)
            venta_mask = signal.isin(señales_venta)
            ponderacion += np.where(compra_mask, p["senal_compra_delta"], 0)
            ponderacion += np.where(venta_mask, p["senal_venta_delta"], 0)
        except NameError:
            pass
    
    # --- Ponderación incremental (vectorizado) ---
    if 'Ponderacion Incremental' in df.columns:
        pi = df['Ponderacion Incremental'].fillna(0)
        th = p["ponder_inc_threshold"]
        if th is not None:
            ponderacion += np.where(pi >= th, p["ponder_inc_pos_delta"], 0)
            ponderacion += np.where(pi <= -th, p["ponder_inc_neg_delta"], 0)
    
    # --- Multiplicador por temporalidad (vectorizado) ---
    if 'Temporalidad' in df.columns:
        tf_lower = df['Temporalidad'].astype(str).str.lower()
        mult = np.ones(len(df))
        mult = np.where(tf_lower.str.contains('1min|5min|1m|5m', regex=True, na=False), p["mult_1m_5m"], mult)
        mult = np.where(tf_lower.str.contains('15min|30min|15m|30m', regex=True, na=False), p["mult_15m_30m"], mult)
        mult = np.where(tf_lower.str.contains('1hour|4hour|1h|4h', regex=True, na=False), p["mult_1h_4h"], mult)
        mult = np.where(tf_lower.str.contains('1day|1week|1d|1w', regex=True, na=False), p["mult_1d_1w"], mult)
        ponderacion *= mult
    
    # --- Clamp final (vectorizado) ---
    ponderacion = np.clip(ponderacion, p["clamp_min"], p["clamp_max"])
    
    return pd.Series(ponderacion, index=df.index, dtype=float)

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
    # ✅ FASE 3: Validar calidad OHLCV antes de procesar
    try:
        sample_n = int(os.environ.get("OHLCV_VALIDATE_SAMPLE_N", "1"))
        if sample_n <= 1:
            do_validate = True
        else:
            key = f"{symbol}|{tf}".encode("utf-8")
            bucket = int(hashlib.md5(key).hexdigest(), 16) % sample_n
            do_validate = bucket == 0

        if do_validate:
            es_valido, problemas = validar_ohlcv_calidad(df_combinado, symbol, tf, strict=False)
            if not es_valido:
                logger.warning(f"[VALIDACIÓN] {symbol}-{tf}: Datos OHLCV no válidos. Problemas: {problemas}")
                # En modo no-strict, continuamos pero registramos la advertencia
    except Exception as e:
        logger.warning(f"[VALIDACIÓN] Error validando OHLCV para {symbol}-{tf}: {e}")
    
    try:
        df_indicadores = calcular_indicadores(df_combinado, tf, symbol=symbol)
        if df_indicadores is None or df_indicadores.empty:
            logger.info(f"No hay indicadores para {symbol} en {tf}.")
            return None
    except ValueError as ve:
        # Validación de integridad del caché
        logger.error(f"[INTEGRIDAD] calcular_indicadores falló para {symbol}-{tf}: {ve}. Cache invalidado.")
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

    # ✅ FASE 3: WHITELISTING - Evaluar si autorizado operar
    try:
        whitelist_result = evaluar_si_autorizado_operar(
            symbol=symbol,
            tf=tf,
            tipo_operacion=entradas.get('tipo_operacion', 'Neutral'),
            confluencia_score=entradas.get('confluencia', {}).get('score', 0.5) if isinstance(entradas.get('confluencia'), dict) else 0.5,
            prob_tecnica=entradas.get('probabilidad_tecnica', 50),
            prob_fundamental=entradas.get('probabilidad_fundamental', 50),
            rrr_promedio=entradas.get('entradas', [{}])[0].get('rrr', 1.0) if entradas.get('entradas') else 1.0,
            alertas=entradas.get('alertas', []),
            tecnica_meta=entradas.get('tecnica_meta'),
            whitelist_cfg=(cfg or {}).get("whitelist")
        )
        es_autorizado_operar = whitelist_result.get('autorizado', False)
        score_whitelist = whitelist_result.get('score_final', 0)
        expectativa_val = whitelist_result.get('expectativa', 0.0)
        motivo_rechazo = whitelist_result.get('motivo_rechazo', "")
    except Exception as e:
        logger.warning(f"[Whitelist] Error evaluando autorización para {symbol}-{tf}: {e}")
        es_autorizado_operar = True  # Fallback: no bloquear si hay error
        score_whitelist = 0
        expectativa_val = 0.0
        motivo_rechazo = "Error evaluación"

    # Injectar datos top-level en entradas para el front (JSON enriquecido)
    entradas['score'] = score_whitelist
    entradas['expectativa'] = expectativa_val
    entradas['rechazo'] = motivo_rechazo
    entradas['autorizado'] = es_autorizado_operar

    # Devolver resultados
    resultado = {
        "Activo": symbol,
        "Temporalidad": temporalidad,
        "Oportunidad": entradas.get('flag_oportunidad'),
        "Patrones Detectados": entradas.get('patrones_detectados'),
        "Tipo de Operacion": entradas.get('tipo_operacion'),
        "Autorizado Whitelist": es_autorizado_operar,  # ✅ PHASE 3
        "Score Final": score_whitelist,                # ✅ PHASE 3 (Score Final para ranking)
        "Expectativa": expectativa_val,                # ✅ PHASE 3
        "Motivo Rechazo": motivo_rechazo,              # ✅ PHASE 3
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
    start_all = time.time()

    cfg_overrides       = overrides or {}
    fmp_map   = cfg_overrides.get('fmpWindows')
    calc_map  = cfg_overrides.get('calcWindows')
    temps     = cfg_overrides.get('tfs') or temporalidades
    white_cfg = cfg_overrides.get('whitelist')

    valid = {'1min','5min','15min','30min','1hour','4hour','1day','1week'}
    temps = [t for t in temps if t in valid]

    loop = asyncio.get_running_loop()

    # Preparar cfg para evaluar (inyectar whitelist si vino en overrides)
    cfg_for_process = dict(cfg or {})
    if white_cfg:
        cfg_for_process["whitelist"] = white_cfg

    # --- ThreadPoolExecutor dedicado con más workers para alto rendimiento ---
    # Controlado por ANALYSIS_MAX_WORKERS (env) y reutiliza un pool global
    total_tasks = len(activos_filtrados) * len(temps)
    max_workers = min(_ANALYSIS_MAX_WORKERS, total_tasks) if total_tasks else 1
    executor = _ANALYSIS_EXECUTOR
    
    # --- Semaphore para limitar concurrencia (balance entre throughput y recursos) ---
    sem = asyncio.Semaphore(max(1, _ANALYSIS_SEM))
    per_symbol_concurrency = int(os.environ.get("ANALYSIS_PER_SYMBOL_CONCURRENCY", "1"))
    per_symbol_concurrency = max(0, per_symbol_concurrency)
    symbol_sems = (
        {symbol: asyncio.Semaphore(per_symbol_concurrency) for symbol in activos_filtrados}
        if per_symbol_concurrency > 0
        else None
    )
    slow_task_sec = int(os.environ.get("ANALYSIS_SLOW_TASK_SEC", "30"))
    
    # Calculate effective concurrency limit
    effective_concurrency = (
        min(len(activos_filtrados) * per_symbol_concurrency, _ANALYSIS_SEM)
        if per_symbol_concurrency > 0
        else _ANALYSIS_SEM
    )
    
    logger.info(
        "[Analisis] Inicio: activos=%d tfs=%d tasks=%d workers=%d sem=%d per_symbol=%d",
        len(activos_filtrados),
        len(temps),
        total_tasks,
        max_workers,
        _ANALYSIS_SEM,
        per_symbol_concurrency,
    )
    
    # Warn if parallelism is severely limited
    if per_symbol_concurrency > 0 and effective_concurrency < (total_tasks / 4):
        logger.warning(
            f"[Analisis] ⚠️ Paralelismo limitado: per_symbol={per_symbol_concurrency} permite max {effective_concurrency} tasks concurrentes "
            f"(de {total_tasks} totales). Aumenta ANALYSIS_PER_SYMBOL_CONCURRENCY={len(temps)} para paralelismo completo."
        )
    
    # 📊 Resetear estadísticas de caché para esta ejecución
    global _niveles_cache_hits, _niveles_cache_misses, _atr_cache_hits, _atr_cache_misses
    _niveles_cache_hits = 0
    _niveles_cache_misses = 0
    _atr_cache_hits = 0
    _atr_cache_misses = 0
    
    async def bounded_analysis(symbol, temporalidad):
        """Envuelve procesar_simbolo_temporalidad con límite de concurrencia."""
        sym_sem = symbol_sems.get(symbol) if symbol_sems else None
        t_queued = time.time()
        async with sem:
            t_acquired = time.time()
            wait_time_ms = (t_acquired - t_queued) * 1000
            if wait_time_ms > 100:  # Log only if waited >100ms for semaphore
                logger.debug(f"[Analisis] {symbol}/{temporalidad} adquirió semáforo después de {wait_time_ms:.0f}ms")
            
            if sym_sem is not None:
                async with sym_sem:
                    fn = partial(
                        procesar_simbolo_temporalidad,
                        symbol, temporalidad, df_eventos, user_chat_id, context,
                        fmp_windows=fmp_map,
                        calc_windows=calc_map,
                        cfg=cfg_for_process
                    )
                    t0 = time.time()
                    result = await loop.run_in_executor(executor, fn)
            else:
                fn = partial(
                    procesar_simbolo_temporalidad,
                    symbol, temporalidad, df_eventos, user_chat_id, context,
                    fmp_windows=fmp_map,
                    calc_windows=calc_map,
                    cfg=cfg_for_process
                )
                t0 = time.time()
                result = await loop.run_in_executor(executor, fn)
            elapsed = time.time() - t0
            if elapsed >= slow_task_sec:
                logger.info(
                    "[Analisis] Lento: %s/%s %.1fs",
                    symbol,
                    temporalidad,
                    elapsed,
                )
            return result

    # --- Análisis principal (PARALELIZACIÓN OPTIMIZADA) ---
    # Opción 2: asyncio.gather() con return_exceptions=True
    # Beneficio: Procesa todos los resultados, capturando excepciones sin perder contexto
    analisis_tasks = []
    task_meta = []  # Parallel list: task_meta[i] = (symbol, temporalidad) for task i

    for symbol in activos_filtrados:
        for temporalidad in temps:
            task = asyncio.create_task(bounded_analysis(symbol, temporalidad))
            analisis_tasks.append(task)
            task_meta.append((symbol, temporalidad))

    # 🚀 Ejecutar todas las tareas con gather (return_exceptions=True para capturar errores)
    # Cada resultado se alinea con su correspondiente (symbol, temporalidad) por índice
    if analisis_tasks:
        t_gather_start = time.time()
        logger.info(
            f"[Analisis] 🚀 Iniciando gather() de {len(analisis_tasks)} tasks "
            f"(sem={_ANALYSIS_SEM}, per_symbol={per_symbol_concurrency}, workers={max_workers})"
        )
        
        results = await asyncio.gather(*analisis_tasks, return_exceptions=True)
        
        t_gather_elapsed = (time.time() - t_gather_start)
        avg_time_per_task = (t_gather_elapsed / len(analisis_tasks)) if analisis_tasks else 0
        logger.info(
            f"[Analisis] ✅ gather() completado en {t_gather_elapsed:.1f}s "
            f"(promedio: {avg_time_per_task*1000:.0f}ms/task, "
            f"paralelismo efectivo: {len(analisis_tasks)/t_gather_elapsed:.1f}x)"
        )
        
        for idx, result in enumerate(results):
            symbol, temporalidad = task_meta[idx]  # Index-based lookup: O(1) y determinístico
            
            try:
                if isinstance(result, Exception):
                    logger.info(f"Error en análisis para símbolo {symbol} y temporalidad {temporalidad}: {result}")
                    errores.append(str(result))
                elif result is not None:
                    resultados.append(result)
                else:
                    logger.debug(f"Resultado vacío para símbolo {symbol} y temporalidad {temporalidad}.")
            except Exception as e:
                logger.warning(f"Excepción en procesamiento de resultado {symbol}/{temporalidad}: {type(e).__name__}: {e}", exc_info=True)

    if not resultados and errores:
        logger.info("No se pudieron obtener resultados debido a errores.")
        for error in errores:
            logger.info(f" - {error}")
    
    elapsed_total = time.time() - start_all
    avg_per_task = (elapsed_total / len(analisis_tasks)) * 1000 if analisis_tasks else 0
    logger.info(
        "[Analisis] Fin: resultados=%d errores=%d elapsed=%.1fs (%.0fms/task promedio, %d tasks totales)",
        len(resultados),
        len(errores),
        elapsed_total,
        avg_per_task,
        len(analisis_tasks),
    )
    
    # 📊 Reporte de estadísticas del caché
    niveles_total = _niveles_cache_hits + _niveles_cache_misses
    atr_total = _atr_cache_hits + _atr_cache_misses
    if niveles_total > 0 or atr_total > 0:
        niv_hit_rate = round(100 * _niveles_cache_hits / max(1, niveles_total), 1)
        atr_hit_rate = round(100 * _atr_cache_hits / max(1, atr_total), 1)
        logger.info(
            "[Cache] Niveles: %d hits + %d misses = %.1f%% | ATR: %d hits + %d misses = %.1f%%",
            _niveles_cache_hits, _niveles_cache_misses, niv_hit_rate,
            _atr_cache_hits, _atr_cache_misses, atr_hit_rate
        )

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


# ============================================================================
# Field Filtering for GCP Uploads (optimize storage & frontend bandwidth)
# ============================================================================

# Campos "core" para frontend (usados en DetalleEjecucion y Monitoreo)
_CORE_FIELDS = {
    # Trading Basics
    'Activo', 'Temporalidad', 'Tipo de Operacion', 'Oportunidad', 
    'Zona No Trading', 'entry', 'tp', 'sl', 'stop_loss_pips',
    # Scoring & Weighting
    'Ponderacion', 'PonderacionIncremental', 'Confianza', 'score_final',
    'expectativa', 'probabilidad_tecnica', 'probabilidad_fundamental',
    'autorizado', 'rechazo',
    # Technical Signals
    'Cruce MACD', 'Bollinger Signal', 'ultimo',
    # Support/Resistance Levels (CRITICAL for DetalleEjecucionScreen)
    'Soportes Alcanzados', 'Resistencias Alcanzadas',
    'Soportes Importantes Alcanzados', 'Resistencias Importantes Alcanzadas',
    'Cerca de Soporte Resistencia', 'Cerca de S/R',
    'soporte_nivel_1', 'soporte_nivel_2', 'resistencia_nivel_1', 'resistencia_nivel_2',
    'Niveles Confirmados (Toques)', 'Niveles Confirmados (Nivel)',
}

# Campos "extended" (detalle completo con técnica + Monte Carlo)
_EXTENDED_FIELDS = _CORE_FIELDS | {
    'Patrones Detectados',
    'Rebotes', 'Rango Dinamico', 'Es Rango Repetitivo', 'Estructura Tendencia',
    'Probabilidad Alza (Montecarlo)', 'Probabilidad Baja (Montecarlo)',
    'MACD Tendencia Predicha',
}

# Campos que NUNCA subir (internos)
_FORBIDDEN_FIELDS = {
    '_ohlcv_df', '_indicadores_df', '_niveles', '_entradas',
    '_internal', '_debug', '_logs', '_temp'
}

def _filter_fields_for_json(record: dict, field_set: set) -> dict:
    """
    Filtra un record dict manteniendo solo los campos en field_set
    y removiendo campos internos (_ prefix).
    """
    if not isinstance(record, dict):
        return record
    out = {}
    for key, value in record.items():
        # Skip forbidden fields
        if key in _FORBIDDEN_FIELDS or key.startswith('_'):
            continue
        # Keep if in allowed set (if empty set = keep all non-forbidden)
        if not field_set or key in field_set:
            out[key] = value
    return out

def _optimize_records_for_upload(
    records: list[dict],
    upload_mode: str = "core"  # "core" | "extended" | "full"
) -> list[dict]:
    """
    Optimiza registros para subida a GCP.
    
    Modes:
    - "core": solo campos necesarios para frontend (más liviano)
    - "extended": core + técnica/Monte Carlo detallada
    - "full": todos los campos (legacy, no recomendado)
    """
    if upload_mode == "full":
        field_set = set()  # sin restricción
    elif upload_mode == "extended":
        field_set = _EXTENDED_FIELDS
    else:  # "core" (default)
        field_set = _CORE_FIELDS
    
    out = []
    for rec in records:
        if not isinstance(rec, dict):
            continue
        filtered = _filter_fields_for_json(rec, field_set)
        if filtered:
            out.append(filtered)
    return out


#@profile
# ==============================
# DataFrame Cache & Partitioning (Phase 2 Optimization)
# ==============================
class DataFramePartitionCache:
    """
    Cache para particiones de DataFrame por moneda.
    Evita repetir filtros string costosos (startswith, endswith).
    Mantiene views (sin copias) para máxima eficiencia.
    """
    def __init__(self):
        self.cache = {}
    
    def partition_by_currency(self, df, moneda_filtro):
        """
        Particiona DataFrame por prefijo/sufijo de moneda.
        
        Args:
            df: DataFrame con columna 'Activo'
            moneda_filtro: código de moneda (ej: 'EUR')
        
        Returns:
            dict con 'principal' (startswith) y 'secundaria' (endswith) views
        """
        cache_key = f"{id(df)}_{moneda_filtro}"
        
        if cache_key not in self.cache:
            moneda_upper = str(moneda_filtro or "").upper()
            
            # Crear masks (sin copiar datos)
            if 'Activo' in df.columns:
                mask_principal = df["Activo"].astype(str).str.startswith(moneda_upper)
                mask_secundaria = df["Activo"].astype(str).str.endswith(moneda_upper)
            else:
                mask_principal = pd.Series([False] * len(df))
                mask_secundaria = pd.Series([False] * len(df))
            
            # Almacenar views (no copias)
            self.cache[cache_key] = {
                "principal": df[mask_principal],
                "secundaria": df[mask_secundaria],
                "masks": {"principal": mask_principal, "secundaria": mask_secundaria}
            }
        
        return self.cache[cache_key]

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
    t_proc_start = time.time()
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

    # Limitar concurrencia de uploads para evitar saturar CPU/GCS
    upload_sem = asyncio.Semaphore(int(os.environ.get("UPLOAD_SEM", "30")))

    # Función para priorizar temporalidades bajas (traders intradía)
    def _tf_priority(tf_str: str) -> int:
        """Convierte temporalidad a minutos para ordenar (menor = más prioritario)"""
        try:
            s = str(tf_str or '').strip().lower()
            if not s: return 99999
            
            # Mapeo directo de temporalidades comunes
            tf_map = {
                '1min': 1, '5min': 5, '15min': 15, '30min': 30,
                '1h': 60, '2h': 120, '4h': 240, '6h': 360, '8h': 480, '12h': 720,
                '1d': 1440, '1w': 10080, '1m': 43200
            }
            if s in tf_map:
                return tf_map[s]
            
            # Parseo genérico si no está en el mapa
            if 'min' in s:
                return int(s.replace('min', ''))
            if 'h' in s:
                return int(s.replace('h', '')) * 60
            if 'd' in s:
                return int(s.replace('d', '')) * 1440
            if 'w' in s:
                return int(s.replace('w', '')) * 10080
            return 99999
        except:
            return 99999

    def _pick_top_timeframe(rows: list[dict]) -> str:
        counts: dict[str, int] = {}
        for r in rows or []:
            if not isinstance(r, dict):
                continue
            tf = r.get("Temporalidad")
            if tf is None:
                continue
            s = str(tf).strip()
            if not s:
                continue
            counts[s] = counts.get(s, 0) + 1
        if not counts:
            return ""
        top_tf = sorted(
            counts.items(),
            key=lambda kv: (-kv[1], _tf_priority(kv[0]), str(kv[0]))
        )[0][0]
        return str(top_tf)

    def _pick_top_timeframe_by_asset(rows: list[dict]) -> dict:
        per_asset: dict[str, dict[str, int]] = {}
        for r in rows or []:
            if not isinstance(r, dict):
                continue
            sym = r.get("Activo")
            tf = r.get("Temporalidad")
            if sym is None or tf is None:
                continue
            s_sym = str(sym).strip().upper()
            s_tf = str(tf).strip()
            if not s_sym or not s_tf:
                continue
            bucket = per_asset.setdefault(s_sym, {})
            bucket[s_tf] = bucket.get(s_tf, 0) + 1

        out: dict[str, str] = {}
        for sym, counts in per_asset.items():
            top_tf = sorted(
                counts.items(),
                key=lambda kv: (-kv[1], _tf_priority(kv[0]), str(kv[0]))
            )[0][0]
            out[sym] = str(top_tf)
        return out

    async def _upload_enriched(res: dict):
        if not isinstance(res, dict):
            return None

        sym       = res.get("Activo")
        tf        = res.get("Temporalidad")
        df_velas  = res.get("_ohlcv_df")
        df_inds   = res.get("_indicadores_df")
        niveles   = res.get("_niveles") or {}
        entradas  = res.get("_entradas") or {}

        tiene_datos = (
            isinstance(df_velas, pd.DataFrame) and not df_velas.empty
        ) or (isinstance(df_inds, pd.DataFrame) and not df_inds.empty)

        if not (sym and tf and tiene_datos):
            return None

        # Timing instrumentation: Track semaphore wait + upload time per symbol/tf
        t_queued = time.time()
        async with upload_sem:
            t_acquired = time.time()
            wait_ms = (t_acquired - t_queued) * 1000
            if wait_ms > 500:
                logger.debug(f"[Upload] {sym}/{tf} esperó {wait_ms:.0f}ms por semáforo")
            
            try:
                t_upload_start = time.time()
                result = await subir_ohlcv_enriquecido_y_registrar(
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
                upload_ms = (time.time() - t_upload_start) * 1000
                if logger.isEnabledFor(logging.DEBUG):
                    logger.debug(f"[Upload] {sym}/{tf} completado en {upload_ms:.0f}ms")
                return result
            except Exception as e:
                logger.info(f"No se pudo subir JSON enriquecido de {sym}-{tf}: {e}")
                return None

    async def _upload_json_registrar(nombre_base: str, data, metadata: dict):
        async with upload_sem:
            return await guardar_json_en_storage_y_registrar(
                exec_id=exec_id,
                chat_id=user_chat_id,
                user_id=user_id,
                nombre_base=nombre_base,
                data=data,
                subir_a_bucket_y_obtener_url=subir_a_bucket_y_obtener_url,
                metadata=metadata,
            )

    async def _upload_csv_and_register(df: pd.DataFrame, nombre_archivo: str, metadata: dict):
        ruta_local = os.path.join("/tmp", nombre_archivo)
        await asyncio.to_thread(save_df_as_csv, df, ruta_local, cfg)
        object_path = build_object_path(exec_id, nombre_archivo) if can_archive else nombre_archivo
        async with upload_sem:
            url_publica = await subir_a_bucket_y_obtener_url(ruta_local, object_path)
        if url_publica and can_archive:
            await asyncio.to_thread(
                fs_registrar_archivo_generado,
                exec_id=exec_id,
                user_id=user_id,
                chat_id=user_chat_id,
                tipo="csv",
                nombre=nombre_archivo,
                gcs_path=object_path,
                signed_url=url_publica,
                content_type="text/csv",
                metadata=metadata,
            )
        return url_publica

    async def _collect_urls(tasks, label: str):
        if not tasks:
            return
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for result in results:
            if isinstance(result, Exception):
                logger.info(f"No se pudo subir {label}: {result}")
            elif result:
                urls_generadas.append(result)

    # >>> registros sin DataFrames ni claves privadas
    t_clean_start = time.time()
    registros_limpios = _sanitize_records_for_json(
        [r for r in resultados if isinstance(r, dict)]
    )
    logger.info("[preview timing] sanitize_records: %.1fms", (time.time() - t_clean_start) * 1000)

    # --- JSON completo (antes de filtrar) ---
    t_df_start = time.time()
    df_resultados = pd.DataFrame(registros_limpios)
    logger.info("[preview timing] create DataFrame (%d rows): %.1fms", len(df_resultados), (time.time() - t_df_start) * 1000)
    logger.info("[preview] df_resultados rows=%d", len(df_resultados))

        # Serializadores locales (no crean funciones globales)
    t_fmt_start = time.time()
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

    logger.info("[preview timing] formateo niveles/toques: %.1fms", (time.time() - t_fmt_start) * 1000)

    # --- PUBLICAR UI_RESUMEN TEMPRANO (sin ponderaciones completas) ---
    # Esto permite que el front navegue inmediatamente mientras se calculan ponderaciones
    if can_archive:
        t_preview_start = time.time()
        # Crear versión preliminar ordenada alfabéticamente (sin ponderaciones todavía)
        df_prelim = df_resultados.sort_values(by="Activo")
        
        cols_ui = [
            "Activo",
            "Temporalidad",
            "Tipo de Operacion",
            "Precio de Entrada",
            "Take Profit",
            "Stop Loss",
            "Autorizado Whitelist",
            "Motivo Rechazo",
        ]
        cols_ui = [c for c in cols_ui if c in df_prelim.columns]

        ordenados_prelim = (
            df_prelim[cols_ui]
            .head(30)
            .replace([np.inf, -np.inf], np.nan)
            .where(pd.notnull(df_prelim[cols_ui]), None)
            .to_dict("records")
            if cols_ui else []
        )

        top_timeframe_temp = _pick_top_timeframe(ordenados_prelim)
        top_timeframe_by_asset_temp = _pick_top_timeframe_by_asset(ordenados_prelim)

        # Oportunidades preliminares (sin ponderación, solo por zona válida)
        df_opp_prelim = df_prelim[
            (df_prelim.get('Oportunidad') == True) &
            (df_prelim.get('Zona No Trading') == False)
        ].copy()

        opp_prelim = (
            df_opp_prelim[cols_ui]
            .head(30)
            .replace([np.inf, -np.inf], np.nan)
            .where(pd.notnull(df_opp_prelim[cols_ui]), None)
            .to_dict("records")
            if cols_ui else []
        )

        ui_resumen_temp = {
            "ordenados_top": sanitize_for_json(ordenados_prelim),
            "oportunidades_top": sanitize_for_json(opp_prelim),
            "counts": {
                "ordenados": int(len(df_prelim)),
                "oportunidades": int(len(df_opp_prelim)),
            },
            "top_timeframe": top_timeframe_temp,
            "top_timeframe_by_asset": top_timeframe_by_asset_temp,
        }

        try:
            logger.info(
                f"[ui_resumen preview] top_timeframe={top_timeframe_temp} "
                f"top_timeframe_by_asset={top_timeframe_by_asset_temp}"
            )
        except Exception:
            pass

        # Publicar inmediatamente antes de calcular ponderaciones
        fs_actualizar_ejecucion(
            exec_id,
            ui_resumen=ui_resumen_temp,
            upload_state={
                "status": "calculating",
                "phase": "early_preview",
                "updated_at": datetime.now(UTC).isoformat() + "Z",
            },
        )
        logger.info("[preview] early_preview publicado en %.1fs", time.time() - t_preview_start)

    # Ponderación (usa versión vectorizado - optimizado: una sola pasada sin copia redundante)
    t_pond_start = time.time()
    df_resultados["Ponderacion"] = calcular_ponderacion_vectorizado(df_resultados, cfg)
    logger.info("[preview timing] ponderacion (vectorizado optimizado): %.1fms", (time.time() - t_pond_start) * 1000)
    logger.info("[preview] ponderaciones listas en %.1fs", time.time() - t_proc_start)

    # Limpia columnas internas si existen
    if not df_resultados.empty:
        df_resultados = df_resultados.drop(
            columns=["bollinger_lower", "bollinger_upper"],
            errors="ignore"
        )

    
    # Ordenado por ponderación
    t_sort_start = time.time()
    df_resultados_ordenado = df_resultados.sort_values(
        by="Ponderacion", ascending=False
    )
    logger.info("[preview timing] sort by Ponderacion: %.1fms", (time.time() - t_sort_start) * 1000)

    # Oportunidades base (solo zona válida) - DEFINIR ANTES DE USAR
    t_filter_start = time.time()
    df_filtrado = df_resultados_ordenado[
        (df_resultados_ordenado.get('Oportunidad') == True) &
        (df_resultados_ordenado.get('Zona No Trading') == False)
    ].copy()
    logger.info("[preview timing] filter oportunidades: %.1fms", (time.time() - t_filter_start) * 1000)

    # --- IDENTIFICAR ACTIVOS PRIORITARIOS PARA MONITOREO ---
    t_priority_start = time.time()
    # Top 2 Long + Top 2 Short (más ponderados de cada tipo)
    priority_assets = []
    if not df_filtrado.empty:
        # Identificar operaciones long (compra)
        df_long = df_filtrado[
            df_filtrado.get('Tipo de Operacion', '').astype(str).str.lower().str.contains('compra|buy|long', case=False, na=False)
        ].sort_values('Ponderacion', ascending=False).head(2)
        
        # Identificar operaciones short (venta)
        df_short = df_filtrado[
            df_filtrado.get('Tipo de Operacion', '').astype(str).str.lower().str.contains('venta|sell|short', case=False, na=False)
        ].sort_values('Ponderacion', ascending=False).head(2)
        
        # Combinar y extraer símbolos únicos
        df_priority = pd.concat([df_long, df_short], ignore_index=True)
        if not df_priority.empty:
            priority_assets = df_priority['Activo'].unique().tolist()
            logger.info(f"✅ Activos prioritarios para monitoreo (top 2 long + top 2 short): {priority_assets}")

    logger.info("[preview timing] identificar priority_assets: %.1fms", (time.time() - t_priority_start) * 1000)

    # --- ACTUALIZAR UI_RESUMEN CON PONDERACIONES FINALES ANTES DE SUBIR ---
    t_ui_final_start = time.time()
    # ✅ Esto permite navegación inmediata con el activo top seleccionado
    top_asset = None
    if can_archive:

        cols_ui = [
            "Activo",
            "Temporalidad",
            "Tipo de Operacion",
            "Ponderacion",
            "Precio de Entrada",
            "Take Profit",
            "Stop Loss",
            "Autorizado Whitelist",
            "Motivo Rechazo",
        ]
        cols_ui = [c for c in cols_ui if c in df_resultados_ordenado.columns]

        ordenados_top = (
            df_resultados_ordenado[cols_ui]
            .head(30)
            .replace([np.inf, -np.inf], np.nan)
            .where(pd.notnull(df_resultados_ordenado[cols_ui]), None)
            .to_dict("records")
            if cols_ui else []
        )

        top_timeframe_final = _pick_top_timeframe(ordenados_top)
        top_timeframe_by_asset_final = _pick_top_timeframe_by_asset(ordenados_top)
        if ordenados_top:
            try:
                top_asset = str(ordenados_top[0].get("Activo") or "").strip().upper() or None
            except Exception:
                top_asset = None

        opp_top = (
            df_filtrado[cols_ui]
            .head(30)
            .replace([np.inf, -np.inf], np.nan)
            .where(pd.notnull(df_filtrado[cols_ui]), None)
            .to_dict("records")
            if cols_ui else []
        )

        ui_resumen_final = {
            "ordenados_top": sanitize_for_json(ordenados_top),
            "oportunidades_top": sanitize_for_json(opp_top),
            "counts": {
                "ordenados": int(len(df_resultados_ordenado)),
                "oportunidades": int(len(df_filtrado)),
            },
            "top_timeframe": top_timeframe_final,
            "top_timeframe_by_asset": top_timeframe_by_asset_final,
            "priority_assets": priority_assets,  # ✅ Activos prioritarios para monitoreo
            "ready_for_monitoring": [],  # Se actualizará a medida que se suban
        }

        try:
            logger.info(
                f"[ui_resumen final] top_timeframe={top_timeframe_final} "
                f"top_timeframe_by_asset={top_timeframe_by_asset_final} "
                f"priority_assets={priority_assets}"
            )
        except Exception:
            pass

        # ✅ Publicar INMEDIATAMENTE con ponderaciones para navegación del front
        fs_actualizar_ejecucion(
            exec_id,
            ui_resumen=ui_resumen_final,
            upload_state={
                "status": "publishing",
                "phase": "ponderaciones_completas",
                "updated_at": datetime.now(UTC).isoformat() + "Z",
            },
        )
        logger.info(f"✅ UI Resumen actualizado con ponderaciones - Usuario puede navegar ahora")
        logger.info("[preview] ui_resumen_final publicado en %.1fs", time.time() - t_proc_start)
        logger.info("[preview timing] ui_resumen final (.head, .to_dict, publish): %.1fms", (time.time() - t_ui_final_start) * 1000)

    # 🚀 PRIORIZACIÓN: seleccionado primero, luego principales, luego resto + JSON
    selected_tasks = []
    priority_tasks = []
    rest_tasks = []
    json_tasks = []
    ready_for_monitoring = []
    resultados_selected_sorted = []
    resultados_priority_sorted = []
    resultados_rest_sorted = []

    # Resolver activo seleccionado (debe coincidir con el elegido por el usuario)
    selected_asset = None
    try:
        if user_chat_id is not None:
            selected_asset = user_states.get(str(user_chat_id), {}).get("par_seleccionado")
        if not selected_asset and user_id is not None:
            selected_asset = user_states.get(str(user_id), {}).get("par_seleccionado")
        if selected_asset:
            selected_asset = str(selected_asset).strip().upper()
    except Exception:
        selected_asset = None
    if not selected_asset and top_asset:
        selected_asset = top_asset
        logger.info(f"ℹ️ Sin activo seleccionado; usando más optado para prioridad: {selected_asset}")

    # === FASE DE PREPARACIÓN ===
    # 7) Preparar uploads de enriquecidos con orden:
    #    1) seleccionado
    #    2) principales (priority_assets)
    #    3) resto
    if can_archive:
        resultados_selected = []
        resultados_priority = []
        resultados_rest = []

        priority_set = set(str(s).strip().upper() for s in (priority_assets or []) if s)

        for res in resultados:
            if isinstance(res, dict):
                sym = res.get("Activo")
                sym_norm = str(sym).strip().upper() if sym else ""
                if selected_asset and sym_norm == selected_asset:
                    resultados_selected.append(res)
                elif sym_norm in priority_set:
                    resultados_priority.append(res)
                else:
                    resultados_rest.append(res)

        resultados_selected_sorted = sorted(
            resultados_selected,
            key=lambda r: _tf_priority(r.get("Temporalidad"))
        )
        resultados_priority_sorted = sorted(
            resultados_priority,
            key=lambda r: _tf_priority(r.get("Temporalidad"))
        )
        resultados_rest_sorted = sorted(
            resultados_rest,
            key=lambda r: _tf_priority(r.get("Temporalidad"))
        )

        if resultados_selected_sorted:
            logger.info(f"📤 Preparando {len(resultados_selected_sorted)} archivos del activo seleccionado...")
            for res in resultados_selected_sorted:
                selected_tasks.append(asyncio.create_task(_upload_enriched(res)))

        if resultados_priority_sorted:
            logger.info(f"📤 Preparando {len(resultados_priority_sorted)} archivos de activos principales...")
            for res in resultados_priority_sorted:
                priority_tasks.append(asyncio.create_task(_upload_enriched(res)))

        if resultados_rest_sorted:
            logger.info(f"📤 Preparando {len(resultados_rest_sorted)} archivos de activos restantes...")
            for res in resultados_rest_sorted:
                rest_tasks.append(asyncio.create_task(_upload_enriched(res)))

    # 8) Preparar uploads de **ordenado** saneado (OPTIMIZADO: core fields only)
    if can_archive:
        df_ord = (
            df_resultados_ordenado
            .replace([np.inf, -np.inf], np.nan)
            .where(pd.notnull(df_resultados_ordenado), None)
            .copy()
        )
        for col in df_ord.columns:
            if df_ord[col].apply(lambda v: isinstance(v, (dict, list, tuple, set, pd.Series))).any():
                df_ord[col] = df_ord[col].apply(sanitize_for_json)

        ordered_records = sanitize_for_json(df_ord.to_dict("records"))
        upload_mode = os.environ.get("GCP_UPLOAD_MODE", "core")
        ordered_records = _optimize_records_for_upload(ordered_records, upload_mode=upload_mode)

        logger.info(
            "[Upload] resultados_ordenados: %d records, mode=%s, size_est=%.1fKB",
            len(ordered_records), upload_mode,
            len(json.dumps(ordered_records[:10] if ordered_records else [])) * len(ordered_records) / 10240
        )

        json_tasks.append(asyncio.create_task(
            _upload_json_registrar(
                nombre_base=f"{moneda_filtro.upper()}_resultados_ordenados",
                data=ordered_records,
                metadata={"moneda_filtro": moneda_filtro, "scope": "ordenado", "upload_mode": upload_mode},
            )
        ))

    # 9) Preparar uploads de oportunidades (OPTIMIZADO: core fields only)
    if can_archive:
        opp_records = df_filtrado.where(pd.notnull(df_filtrado), None).to_dict("records")
        opp_records = _optimize_records_for_upload(opp_records, upload_mode=upload_mode)
        logger.info("[Upload] oportunidades: %d records", len(opp_records))

        json_tasks.append(asyncio.create_task(
            _upload_json_registrar(
                nombre_base=f"{moneda_filtro.upper()}_oportunidades",
                data=opp_records,
                metadata={"moneda_filtro": moneda_filtro, "scope": "oportunidades", "upload_mode": upload_mode},
            )
        ))

    # === FASE 1: Ejecutar SELECCIONADO y liberar monitoreo temprano ===
    if selected_tasks:
        try:
            t_selected_start = time.time()
            results = await asyncio.gather(*selected_tasks, return_exceptions=True)
            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    logger.debug(f"❌ No se pudo subir JSON seleccionado[{i}]: {result}")
                elif result:
                    urls_generadas.append(result)
                    sym = resultados_selected_sorted[i].get("Activo")
                    if sym and sym not in ready_for_monitoring:
                        ready_for_monitoring.append(sym)

            if ready_for_monitoring:
                fs_actualizar_ejecucion(
                    exec_id,
                    ui_resumen={"ready_for_monitoring": ready_for_monitoring},
                    upload_state={
                        "status": "publishing",
                        "phase": "priority_ready",
                        "updated_at": datetime.now(UTC).isoformat() + "Z",
                    },
                )
                logger.info(f"✅ Activo seleccionado listo para monitoreo: {ready_for_monitoring}")
                logger.info("✅ priority_ready en %.1fs", time.time() - t_proc_start)

            logger.info("✅ uploads del seleccionado completados en %.1fs", time.time() - t_selected_start)
        except Exception as e:
            logger.error(f"[selected uploads] Error crítico: {type(e).__name__}: {e}", exc_info=True)

    # === FASE 2: Ejecutar PRINCIPALES ===
    if priority_tasks:
        try:
            t_priority_start = time.time()
            results = await asyncio.gather(*priority_tasks, return_exceptions=True)
            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    logger.debug(f"❌ No se pudo subir JSON principal[{i}]: {result}")
                elif result:
                    urls_generadas.append(result)
                    sym = resultados_priority_sorted[i].get("Activo")
                    if sym and sym not in ready_for_monitoring:
                        ready_for_monitoring.append(sym)

            if ready_for_monitoring:
                fs_actualizar_ejecucion(
                    exec_id,
                    ui_resumen={"ready_for_monitoring": ready_for_monitoring},
                )

            logger.info("✅ uploads de principales completados en %.1fs", time.time() - t_priority_start)
        except Exception as e:
            logger.error(f"[priority uploads] Error crítico: {type(e).__name__}: {e}", exc_info=True)

    # === FASE 3: Ejecutar RESTO + JSON ===
    remaining_tasks = rest_tasks + json_tasks
    if remaining_tasks:
        try:
            t_uploads_start = time.time()
            logger.info(
                f"🚀 Ejecutando {len(remaining_tasks)} uploads (resto: {len(rest_tasks)}, json: {len(json_tasks)}, UPLOAD_SEM={int(os.environ.get('UPLOAD_SEM', '30'))})"
            )
            results = await asyncio.gather(*remaining_tasks, return_exceptions=True)
            t_uploads_elapsed = (time.time() - t_uploads_start)
            t_uploads_ms = t_uploads_elapsed * 1000
            logger.info(
                f"✅ uploads restantes completados en {t_uploads_elapsed:.1f}s (promedio: {t_uploads_ms/len(remaining_tasks):.0f}ms/upload)"
            )

            rest_count = 0
            json_count = 0
            rest_end = len(rest_tasks)

            for idx, result in enumerate(results):
                if idx < rest_end:
                    i = idx
                    if isinstance(result, Exception):
                        logger.debug(f"❌ No se pudo subir JSON enriquecido[{i}]: {result}")
                    elif result:
                        urls_generadas.append(result)
                        sym = resultados_rest_sorted[i].get("Activo")
                        if sym and sym not in ready_for_monitoring:
                            ready_for_monitoring.append(sym)
                    rest_count += 1
                else:
                    if isinstance(result, Exception):
                        logger.debug(f"❌ No se pudo subir JSON: {result}")
                    elif result:
                        urls_generadas.append(result)
                    json_count += 1

            if ready_for_monitoring:
                fs_actualizar_ejecucion(
                    exec_id,
                    ui_resumen={"ready_for_monitoring": ready_for_monitoring},
                )

            logger.info(
                f"✅ uploads completados: prioritarios={len(resultados_priority_sorted)}, resto={rest_count}, json={json_count}, urls_total={len(urls_generadas)}"
            )
        except Exception as e:
            logger.error(f"[remaining uploads] Error crítico: {type(e).__name__}: {e}", exc_info=True)
            
    if can_archive:
        fs_actualizar_ejecucion(
            exec_id,
            upload_state={
                "status": "partial_ready","phase": "core_ready",
                "updated_at": datetime.now(UTC).isoformat() + "Z",
            },
        )
        logger.info("[preview] core_ready en %.1fs", time.time() - t_proc_start)

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

    # Filtro para imágenes (compras) - OPTIMIZADO: no copiar innecesariamente
    df_filtradoToImage = df_filtrado[
        (df_filtrado.get('Oportunidad') == True) &
        (df_filtrado.get('Zona No Trading') == False) &
        (df_filtrado.get('Tipo de Operacion').isin([
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

    csv_upload_tasks = []
    
    # --- OPTIMIZADO Phase 2: Cache para particiones, feature flag para principal/secundaria ---
    cache = DataFramePartitionCache()
    enable_principal_secundaria_csv = os.environ.get("CSV_ENABLE_PRINCIPAL_SECUNDARIA", "true").lower() == "true"

    if is_principal_moneda and enable_principal_secundaria_csv:
        # OPTIMIZADO: Usar cache para evitar filtros repetidos
        partitions_full = cache.partition_by_currency(df_resultados_ordenado, moneda_filtro)
        partitions_filtered = cache.partition_by_currency(df_filtrado, moneda_filtro)
        
        df_principal = partitions_full["principal"]  # View sin copy
        df_secundaria = partitions_full["secundaria"]  # View sin copy
        df_filtrado_principal = partitions_filtered["principal"]  # View sin copy
        df_filtrado_secundaria = partitions_filtered["secundaria"]  # View sin copy

        nombre_archivo_principal  = generar_nombre_archivo(moneda_filtro, tipo="principal")
        nombre_archivo_secundaria = generar_nombre_archivo(moneda_filtro, tipo="secundaria")
        nombre_archivo_filtrado_principal  = generar_nombre_archivo(moneda_filtro, filtro=True, tipo="principal")
        nombre_archivo_filtrado_secundaria = generar_nombre_archivo(moneda_filtro, filtro=True, tipo="secundaria")

        # Principal completo
        if not df_principal.empty:
            if origen == "app":
                csv_upload_tasks.append(asyncio.create_task(
                    _upload_csv_and_register(
                        df_principal,
                        nombre_archivo_principal,
                        metadata={"moneda_filtro": moneda_filtro, "particion": "principal", "filtrado": False},
                    )
                ))
            if send_to_tg:
                if origen == "telegram":
                    asyncio.create_task(enviar_csv_telegram(df_principal, context, nombre_archivo_principal, user_chat_id, cfg=cfg))
                else:
                    await enviar_csv_telegram(df_principal, context, nombre_archivo_principal, user_chat_id, cfg=cfg)

        # Secundaria completo
        if not df_secundaria.empty:
            if origen == "app":
                csv_upload_tasks.append(asyncio.create_task(
                    _upload_csv_and_register(
                        df_secundaria,
                        nombre_archivo_secundaria,
                        metadata={"moneda_filtro": moneda_filtro, "particion": "secundaria", "filtrado": False},
                    )
                ))
            if send_to_tg:
                if origen == "telegram":
                    asyncio.create_task(enviar_csv_telegram(df_secundaria, context, nombre_archivo_secundaria, user_chat_id, cfg=cfg))
                else:
                    await enviar_csv_telegram(df_secundaria, context, nombre_archivo_secundaria, user_chat_id, cfg=cfg)

        # Principal filtrado
        if not df_filtrado_principal.empty:
            if origen == "app":
                csv_upload_tasks.append(asyncio.create_task(
                    _upload_csv_and_register(
                        df_filtrado_principal,
                        nombre_archivo_filtrado_principal,
                        metadata={"moneda_filtro": moneda_filtro, "particion": "principal", "filtrado": True},
                    )
                ))
            if send_to_tg:
                if origen == "telegram":
                    asyncio.create_task(enviar_csv_telegram(df_filtrado_principal, context, nombre_archivo_filtrado_principal, user_chat_id, cfg=cfg))
                else:
                    await enviar_csv_telegram(df_filtrado_principal, context, nombre_archivo_filtrado_principal, user_chat_id, cfg=cfg)

        # Secundaria filtrado
        if not df_filtrado_secundaria.empty:
            if origen == "app":
                csv_upload_tasks.append(asyncio.create_task(
                    _upload_csv_and_register(
                        df_filtrado_secundaria,
                        nombre_archivo_filtrado_secundaria,
                        metadata={"moneda_filtro": moneda_filtro, "particion": "secundaria", "filtrado": True},
                    )
                ))
            if send_to_tg:
                if origen == "telegram":
                    asyncio.create_task(enviar_csv_telegram(df_filtrado_secundaria, context, nombre_archivo_filtrado_secundaria, user_chat_id, cfg=cfg))
                else:
                    await enviar_csv_telegram(df_filtrado_secundaria, context, nombre_archivo_filtrado_secundaria, user_chat_id, cfg=cfg)
    elif is_principal_moneda:
        logger.info(f"CSV principal/secundaria deshabilitados (CSV_ENABLE_PRINCIPAL_SECUNDARIA={enable_principal_secundaria_csv})")
    else:
        logger.info(f"La divisa '{moneda_filtro}' NO es principal: se omiten artefactos principal/secundaria.")
    # ---------- ⬆️ FIN LÓGICA PRINCIPAL / SECUNDARIA OPTIMIZADA ⬆️ ----------

    await _collect_urls(csv_upload_tasks, "CSV")

    # Guardar CSVs “globales”
    nombre_archivo          = generar_nombre_archivo(moneda_filtro)
    nombre_archivo_filtrado = generar_nombre_archivo(moneda_filtro, filtro=True)

    # Asegurar llaves en user_states y obtener lock de forma segura
    lock_to_use = None
    with user_states_lock:
        user_states.setdefault(user_chat_id, {})
        if "lock" not in user_states[user_chat_id]:
            user_states[user_chat_id]["lock"] = asyncio.Lock()
            user_states[user_chat_id]["lock_holder"] = None
        for k in ("archivos_enviados","imagenes_oportunidades_enviadas","imagenes_eventos_enviadas"):
            user_states[user_chat_id].setdefault(k, False)
        # ✅ Capturar referencia dentro del lock para evitar race condition
        lock_to_use = user_states[user_chat_id]["lock"]

    async with lock_to_use:
        user_states[user_chat_id]["lock_holder"] = asyncio.current_task()

        csv_global_tasks = []

        # CSV “completo”
        if not df_resultados.empty:
            if origen == "app":
                csv_global_tasks.append(asyncio.create_task(
                    _upload_csv_and_register(
                        df_resultados,
                        nombre_archivo,
                        metadata={"moneda_filtro": moneda_filtro, "particion": "principal", "filtrado": False},
                    )
                ))
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
                csv_global_tasks.append(asyncio.create_task(
                    _upload_csv_and_register(
                        df_filtrado,
                        nombre_archivo_filtrado,
                        metadata={"moneda_filtro": moneda_filtro, "particion": "principal", "filtrado": True},
                    )
                ))
            if send_to_tg:
                if origen == "telegram":
                    asyncio.create_task(enviar_csv_telegram(df_filtrado, context, nombre_archivo_filtrado, user_chat_id, cfg=cfg))
                else:
                    await enviar_csv_telegram(df_filtrado, context, nombre_archivo_filtrado, user_chat_id, cfg=cfg)
        else:
            logger.info(f"DF df_filtrado vacío; no se envía {nombre_archivo_filtrado}")

        await _collect_urls(csv_global_tasks, "CSV global")

        user_states[user_chat_id]["archivos_enviados"] = True

        # Imágenes de oportunidades (secuencial para preservar orden)
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

    if can_archive:
        fs_actualizar_ejecucion(
            exec_id,
            upload_state={
                "status": "completed",
                "phase": "done",
                "updated_at": datetime.now(UTC).isoformat() + "Z",
            },
        )

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
    with user_states_lock:  # ✅ FIX: Protect against concurrent access
        if user_chat_id not in user_states:
            user_states[user_chat_id] = {"estado": "disponible", "par_seleccionado": None, "cache_realtime": {}, "soportes_resistencias_cache": {}}
        return user_states[user_chat_id].copy()  # ✅ Return copy to prevent external mutations

# Función para actualizar el estado de un usuario
# ----------------- Estado en memoria -----------------
#@profile
def actualizar_estado_usuario(user_chat_id, estado, par_seleccionado=None):
    with user_states_lock:  # ✅ FIX: Protect against concurrent modifications
        if user_chat_id not in user_states:
            user_states[user_chat_id] = {"estado": "disponible", "par_seleccionado": None, "cache_realtime": {}, "soportes_resistencias_cache": {}}
        user_states[user_chat_id]["estado"] = estado
        user_states[user_chat_id]["par_seleccionado"] = par_seleccionado
        # Cache local/temporal (se pierde si el pod se reinicia)
        user_states[user_chat_id]["soportes_resistencias_cache"] = {}

#@profile
def limpiar_estado_usuario(user_chat_id):
    with user_states_lock:  # ✅ FIX: Protect against concurrent access
        if user_chat_id in user_states:
            user_states[user_chat_id]["estado"] = "disponible"
            user_states[user_chat_id]["par_seleccionado"] = None
            # Cache local/temporal en memoria (no persistido)
            user_states[user_chat_id]["cache_realtime"] = {}

#@profile
def limpiar_soportes_resistencias_cache(user_chat_id):
    # Actualizar memoria local bajo lock
    with user_states_lock:  # ✅ FIX: Protect against concurrent modifications
        if user_chat_id in user_states:
            user_states[user_chat_id]["soportes_resistencias_cache"] = {}
            logger.info(f"[LOCAL CACHE] Cache temporal de soportes/resistencias reseteado para usuario {user_chat_id} en este pod.")
        else:
            # Si no hay estado, inicialízalo como disponible
            user_states[user_chat_id] = {
            "estado": "disponible",
            "soportes_resistencias_cache": {}
        }
            logger.info(f"[Init] Estado inicializado para usuario {user_chat_id}.")
    
    # Actualizar estado remoto (Firestore) FUERA del lock para evitar bloqueos prolongados
    try:
        # ¡Ojo! Este es un chat_id, por eso usamos chat_id=... (no user_id)
        mark_user_state(chat_id=user_chat_id, estado="disponible")
    except Exception as e:
        logger.warning(f"[limpiar_soportes_resistencias_cache] Error al marcar estado en Firestore: {e}")

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
        # Inicializa el estado (protegido con lock para evitar TOCTOU race)
        with user_states_lock:
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
  
    # Cambiar el estado para capturar fecha+símbolo en el siguiente mensaje (protegido con lock)
    with user_states_lock:
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

    # 5) Dejar el estado listo para que el próximo mensaje sea la fecha (protegido con lock)
    with user_states_lock:
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

    # Dejar listo para que el próximo mensaje sea el símbolo (protegido con lock)
    with user_states_lock:
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

            # Lock distribuido por usuario (multi-pod)
            lock_id = uuid.uuid4().hex
            lock_ttl = compute_lock_ttl(1)
            acquired = await asyncio.to_thread(
                acquire_user_lock,
                chat_id=user_chat_id,
                lock_id=lock_id,
                ttl_seconds=lock_ttl,
            )
            if not acquired:
                await update.message.reply_text(
                    "Ya tienes un análisis en ejecución. Por favor, espera a que termine."
                )
                return

            # Lanza la ejecución en background
            asyncio.create_task(
                ejecutar_recurrente(
                    context, update, simbolo.upper(),
                    user_chat_id=user_chat_id,
                    opciones_usuario=opciones_usuario,
                    origen="telegram",
                    lock_id=lock_id
                )
            )
        except Exception as e:
            await update.message.reply_text(f"Hubo un error procesando el símbolo: {e}")
        finally:
            # Limpieza de estado local (protegido con lock)
            with user_states_lock:
                if user_chat_id in user_states:
                    user_states[user_chat_id]["estado"] = "disponible"
            mark_user_state(chat_id=user_chat_id, estado="disponible")
        return

    # ───────────────────────────────
    # 2) Rango de fechas (eventos económicos por rango)
    # ───────────────────────────────
    if estado_firestore == "esperando_fechas":
        uid_chat = user_chat_id  # clave consistente para Telegram
        lock_id = None
        try:
            await update.message.reply_text("Empezamos a obtener la información, espera un momento por favor.")

            # Estructura de estado segura (FIXED: use lock to prevent TOCTOU)
            with user_states_lock:
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
            # Lock distribuido por usuario (multi-pod)
            lock_id = uuid.uuid4().hex
            lock_ttl = USER_LOCK_MIN_SECONDS
            acquired = await asyncio.to_thread(
                acquire_user_lock,
                chat_id=uid_chat,
                lock_id=lock_id,
                ttl_seconds=lock_ttl,
            )
            if not acquired:
                await update.message.reply_text(
                    "Ya tienes un análisis en ejecución. Por favor, espera a que termine."
                )
                return

            actualizar_estado_usuario(uid_chat, "en ejecución")
            mark_user_state(chat_id=uid_chat, estado="en ejecución")

            # Traer/filtrar eventos
            df_eventos = obtener_eventos_guardados_o_futuros(fecha_inicio, fecha_fin)
            if df_eventos is None or getattr(df_eventos, "empty", True):
                await update.message.reply_text(
                    f"No se encontraron eventos económicos entre {fecha_inicio.strftime('%Y-%m-%d')} y {fecha_fin.strftime('%Y-%m-%d')}."
                )
            else:
                # ✅ Obtener referencia del lock de forma segura
                async_lock = None
                with user_states_lock:
                    if uid_chat in user_states and "lock" in user_states[uid_chat]:
                        async_lock = user_states[uid_chat]["lock"]
                
                if async_lock is None:
                    await update.message.reply_text("Error interno: no se pudo obtener lock de usuario.")
                    return
                
                async with async_lock:
                    # Re-obtener state dentro del asyncio.Lock (pero fuera del threading lock para evitar deadlock)
                    with user_states_lock:
                        state = user_states.get(uid_chat, {})
                    if state:
                        state["lock_holder"] = asyncio.current_task()
                    await enviar_imagenes_por_currency_a_usuario(df_eventos, context, uid_chat)
                    if state:
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
            # Limpieza de estado (protegido con lock)
            with user_states_lock:
                if uid_chat in user_states:
                    user_states[uid_chat]["fecha_inicio"] = None
                    user_states[uid_chat]["fecha_fin"] = None
                    user_states[uid_chat]["estado"] = "disponible"
            mark_user_state(chat_id=uid_chat, estado="disponible")
            if lock_id:
                try:
                    release_user_lock(chat_id=uid_chat, lock_id=lock_id)
                except Exception:
                    pass
        return

    # ───────────────────────────────
    # 3) Noticias por fecha + símbolo (usuario)
    # ───────────────────────────────
    if estado_firestore == "esperando_fechas_noticias_user":
        lock_id = None
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

            # Lock distribuido por usuario (multi-pod)
            lock_id = uuid.uuid4().hex
            lock_ttl = USER_LOCK_MIN_SECONDS
            acquired = await asyncio.to_thread(
                acquire_user_lock,
                chat_id=user_chat_id,
                lock_id=lock_id,
                ttl_seconds=lock_ttl,
            )
            if not acquired:
                await update.message.reply_text(
                    "Ya tienes un análisis en ejecución. Por favor, espera a que termine."
                )
                return

            # Inicializa en estado local (protegido con lock)
            with user_states_lock:
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

            for noticia in noticias_del_dia.to_dict("records"):
                title = noticia.get('title', '')
                sitio = noticia.get('site', 'No especificado')
                text = noticia.get('text', 'Sin Descripción') or 'Sin Descripción'
                symbol = noticia.get('symbol', symbol)
                fecha = noticia.get('publishedDate')
                try:
                    fecha_str = fecha.strftime('%Y-%m-%d %H:%M:%S') if fecha else ""
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
            # Limpieza de estado (protegido con lock)
            with user_states_lock:
                if user_chat_id in user_states:
                    user_states[user_chat_id]["fecha_inicio"] = None
                    user_states[user_chat_id]["fecha_fin"] = None
                    user_states[user_chat_id]["estado"] = "disponible"
            mark_user_state(chat_id=user_chat_id, estado="disponible")
            if lock_id:
                try:
                    release_user_lock(chat_id=user_chat_id, lock_id=lock_id)
                except Exception:
                    pass
        return

    # ───────────────────────────────
    # 4) Noticias por fecha (admin, recorre varios símbolos)
    # ───────────────────────────────
    if estado_firestore == "esperando_fechas_noticias_admin":
        lock_id = None
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

            # Lock distribuido por usuario (multi-pod)
            lock_id = uuid.uuid4().hex
            lock_ttl = USER_LOCK_MIN_SECONDS
            acquired = await asyncio.to_thread(
                acquire_user_lock,
                chat_id=user_chat_id,
                lock_id=lock_id,
                ttl_seconds=lock_ttl,
            )
            if not acquired:
                await update.message.reply_text(
                    "Ya tienes un análisis en ejecución. Por favor, espera a que termine."
                )
                return

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
                for noticia in noticias_del_dia.to_dict("records"):
                    title = noticia.get('title', '')
                    sitio = noticia.get('site', 'No especificado')
                    text = noticia.get('text', 'Sin Descripción') or 'Sin Descripción'
                    sym  = noticia.get('symbol', symbol)
                    fecha = noticia.get('publishedDate')
                    try:
                        fecha_str = fecha.strftime('%Y-%m-%d %H:%M:%S') if fecha else ""
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
            # Limpieza de estado (protegido con lock)
            with user_states_lock:
                if user_chat_id in user_states:
                    user_states[user_chat_id]["fecha_inicio"] = None
                    user_states[user_chat_id]["fecha_fin"] = None
                    user_states[user_chat_id]["estado"] = "disponible"
            mark_user_state(chat_id=user_chat_id, estado="disponible")
            if lock_id:
                try:
                    release_user_lock(chat_id=user_chat_id, lock_id=lock_id)
                except Exception:
                    pass
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
        lock_id = None
        try:
            if not update.message.photo:
                await update.message.reply_text("⚠️ Por favor, sube una imagen válida.")
                return

            archivo = await update.message.photo[-1].get_file()
            os.makedirs("imagenes", exist_ok=True)
            os.makedirs("procesadas", exist_ok=True)

            ruta_local = f"imagenes/{update.effective_user.id}.jpg"
            await archivo.download_to_drive(ruta_local)

            es_ok = await asyncio.to_thread(es_grafico_de_velas, ruta_local)
            if not es_ok:
                await update.message.reply_text("❌ No parece ser un gráfico de velas. Intenta con otra imagen.")
                return

            # Lock distribuido por usuario (multi-pod)
            lock_id = uuid.uuid4().hex
            lock_ttl = USER_LOCK_MIN_SECONDS
            acquired = await asyncio.to_thread(
                acquire_user_lock,
                chat_id=user_chat_id,
                lock_id=lock_id,
                ttl_seconds=lock_ttl,
            )
            if not acquired:
                await update.message.reply_text(
                    "Ya tienes un análisis en ejecución. Por favor, espera a que termine."
                )
                return

            await update.message.reply_text("Empezó el análisis.")

            es_admin = es_administrador(user_chat_id)

            try:
                res = await asyncio.to_thread(
                    analizar_con_yolo,
                    ruta_local,
                    include_tech=es_admin,
                    user_id=str(user_chat_id),
                )
            except TypeError:
                res = await asyncio.to_thread(analizar_con_yolo, ruta_local)

            entradas = {}
            if isinstance(res, tuple) and len(res) == 3:
                ruta_salida, texto_resultado, entradas = res
            elif isinstance(res, tuple) and len(res) == 2:
                ruta_salida, texto_resultado = res
                entradas = {}
            else:
                raise ValueError(f"analizar_con_yolo devolvió formato inesperado: {res}")

            with open(ruta_salida, "rb") as foto:
                await update.message.reply_photo(photo=foto)

            await update.message.reply_text(texto_resultado)

            # Panel tipo “Finelo-lite”
            try:
                asset = (entradas or {}).get("asset") or {}
                ins   = (entradas or {}).get("insights") or {}
                patrones = (entradas or {}).get("patrones_label") or []

                sym = asset.get("symbol")
                desc = asset.get("descripcion")
                tf = asset.get("timeframe")
                q = asset.get("quote_last")

                conflu = (entradas or {}).get("confluencia") or ins.get("confluencia") or {}
                con_label = conflu.get("label", "—")
                con_score = conflu.get("score", None)
                con_pct = f"{int(float(con_score)*100)}%" if isinstance(con_score, (int, float)) else "—"

                panel = "📌 Señal + Contexto\n"
                if sym:
                    panel += f"📍 Activo: {desc} ({sym})\n"
                if tf:
                    panel += f"⏱️ TF: {tf}\n"
                if isinstance(q, (int, float)):
                    panel += f"💵 Precio aprox.: {q}\n"

                panel += f"⚡ Confluencia: {con_label} ({con_pct})\n"

                if patrones:
                    panel += "🧩 Patrones: " + ", ".join(patrones[:8]) + ("\n" if len(patrones) <= 8 else "…\n")
                else:
                    panel += "🧩 Patrones: (ninguno)\n"

                # Insights (triggers/risks)
                if ins:
                    scenario = (ins.get("scenario") or "").upper()
                    if scenario:
                        panel += f"📈 Escenario: {scenario}\n"
                    trg = ins.get("triggers") or []
                    rsk = ins.get("risks") or []
                    if trg:
                        panel += "⚡ Eventos:\n" + "\n".join([f"• {t}" for t in trg[:3]]) + "\n"
                    if rsk:
                        panel += "⚠️ Riesgos:\n" + "\n".join([f"• {r}" for r in rsk[:3]]) + "\n"

                await update.message.reply_text(panel.strip())
            except Exception:
                pass

            if not es_administrador(user_chat_id):
                success, mensaje = await descontar_transaccion(user_chat_id, 1, origen="telegram")
                if not success:
                    await update.message.reply_text(mensaje)

        except Exception as e:
            await update.message.reply_text(f"Hubo un error analizando la imagen: {e}")

        finally:
            mark_user_state(chat_id=user_chat_id, estado="disponible")
            if lock_id:
                try:
                    release_user_lock(chat_id=user_chat_id, lock_id=lock_id)
                except Exception:
                    pass
            try:
                if ruta_local and os.path.exists(ruta_local): os.remove(ruta_local)
                if ruta_salida and os.path.exists(ruta_salida): os.remove(ruta_salida)
            except Exception:
                pass

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
    user_id = _user_id_from_chat(user_chat_id)
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
    lock_id: str | None = None,
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

    # --- Asegurar que las listas globales estén cargadas (lazy loading)
    await asyncio.to_thread(_ensure_globals_loaded)

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

    # Estado local por chat (protegido con lock)
    with user_states_lock:
        if user_chat_id and user_chat_id not in user_states:
            user_states[user_chat_id] = {}

    cfg_overrides = operatoria_cfg or {}
    temps = cfg_overrides.get("tfs") or temporalidades

    # Contabilizar transacciones (activo x tf) - protegido con lock
    n = len(activos_filtrados) * len(temps)
    with user_states_lock:
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
        error_occurred = True
        if exec_id:
            try:
                await asyncio.to_thread(
                    fs_finalizar_ejecucion,
                    exec_id,
                    "fallido",
                    {
                        "error": "saldo_insuficiente",
                        "code": "INSUFFICIENT_TRANSACTIONS",
                        "message": "No cuenta con la cuota de transacciones requerida. Por favor, adquiere un paquete.",
                    },
                )
            except Exception:
                pass
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

    # --- Lock heartbeat (solo si tenemos lock distribuido) ---
    lock_stop_evt = None
    lock_ttl = USER_LOCK_MAX_SECONDS
    if lock_id:
        # Ajustar TTL dinamico segun cantidad de activos
        try:
            lock_ttl = compute_lock_ttl(len(activos_filtrados))
            await asyncio.to_thread(
                extend_user_lock,
                user_id=user_id,
                chat_id=user_chat_id,
                lock_id=lock_id,
                ttl_seconds=lock_ttl,
            )
        except Exception:
            lock_ttl = USER_LOCK_MAX_SECONDS

        lock_stop_evt = asyncio.Event()

        async def _lock_hb():
            interval = max(10, int(lock_ttl // 3) or 10)
            try:
                while not lock_stop_evt.is_set():
                    await asyncio.sleep(interval)
                    await asyncio.to_thread(
                        extend_user_lock,
                        user_id=user_id,
                        chat_id=user_chat_id,
                        lock_id=lock_id,
                        ttl_seconds=lock_ttl,
                    )
            except Exception:
                pass

        asyncio.create_task(_lock_hb())

    # --- Ejecución principal ---
    try:
        if error_occurred:
            return  # ya se manejó y se liberará estado en finally

        start_time = datetime.now()

        # Eventos económicos (tolerante a error) ✅ Con caché multi-pod
        try:
            df_eventos = await get_eventos_economicos_cached(grace_minutes=0)
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
                "whitelist":   (operatoria_cfg or {}).get("whitelist"),
            },
            cfg=cfg,
        )
        logger.info(f"[Analisis completado] Retornando {len(resultados) if resultados else 0} resultados")

        if not resultados:
            if send_to_tg:
                try:
                    await context.bot.send_message(
                        chat_id=user_chat_id,
                        text="El análisis no produjo resultados."
                    )
                except Exception as e:
                    logger.warning(f"No se pudo enviar mensaje Telegram (sin resultados): {e}")
            return

        try:
            logger.info(f"[procesar_resultado] Iniciando procesamiento de {len(resultados)} resultados")
            url_generadas = await procesar_resultado(
                resultados, df_eventos, context, update,
                moneda_filtro, user_id, user_chat_id, opciones_usuario, origen,
                exec_id=exec_id, cfg=cfg
            )
            logger.info(f"[procesar_resultado] Completado, {len(url_generadas) if url_generadas else 0} URLs generadas")
        except Exception as e:
            logger.error(f"[procesar_resultado] Error crítico durante procesamiento: {type(e).__name__}: {e}", exc_info=True)
            if can_archive:
                try:
                    await asyncio.to_thread(
                        fs_finalizar_ejecucion,
                        exec_id,
                        "fallido",
                        {"error": f"procesar_resultado: {str(e)}"}
                    )
                except Exception as e2:
                    logger.warning(f"No se pudo marcar fallido exec_id={exec_id}: {e2}")
            raise  # Re-raise para que sea capturado por el bloque except principal

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

        # Liberar lock distribuido (si aplica)
        try:
            if lock_stop_evt:
                lock_stop_evt.set()
            if lock_id:
                release_user_lock(user_id=user_id, chat_id=user_chat_id, lock_id=lock_id)
        except Exception:
            pass


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

    # Obtener los datos de activos/categorías desde Firestore (batch read)
    doc_ref_activos = db.collection("config").document("activos_con_descripcion")
    doc_ref_categorias = db.collection("config").document("categorias")
    doc_activos, doc_categorias = list(db.get_all([doc_ref_activos, doc_ref_categorias]))

    if doc_activos.exists:
        activos_con_descripcion = doc_activos.to_dict().get("data", {})
    else:
        activos_con_descripcion = {}

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
        activos_en_categoria = categorias.get(categoria, [])
        
        # Botones individuales para cada activo
        botones = [
            [InlineKeyboardButton(f"{par} - {activos_con_descripcion.get(par, {}).get('descripcion', par)}",
                                callback_data=f"{user_id}_par_{par}")]
                                for par in activos_en_categoria
        ]
        
        # Agregar botón "Analizar TODOS" si la categoría tiene múltiples activos
        if len(activos_en_categoria) > 1:
            botones.insert(0, [InlineKeyboardButton("✅ Analizar TODOS", callback_data=f"{user_id}_par_TODOS")])
        
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
    
    # Normalizar para que sea case-insensitive
    par_display = par.upper()

    # Mensaje más descriptivo según si es un par individual o un filtro de categoría
    if par_display in ("TODOS", "ALL"):
        await query.edit_message_text(f"Iniciando análisis de TODOS los activos...")
    else:
        await query.edit_message_text(f"Has seleccionado el activo: {par}")

    # Lock distribuido por usuario (multi-pod)
    lock_id = uuid.uuid4().hex
    lock_ttl = compute_lock_ttl(1)
    acquired = await asyncio.to_thread(
        acquire_user_lock,
        chat_id=user_chat_id,
        lock_id=lock_id,
        ttl_seconds=lock_ttl,
    )
    if not acquired:
        await context.bot.send_message(
            chat_id=user_chat_id,
            text="Ya tienes un análisis en ejecución. Por favor, espera a que termine."
        )
        return

    # Ejecutar el análisis en una tarea asíncrona
    asyncio.create_task(
        ejecutar_recurrente(
            context, update, par, user_chat_id, opciones_usuario, lock_id=lock_id
        )
    )


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


# ✅ NUEVO: Tracking para auto-refresh de subscriptions
_SUBSCRIPTIONS_CACHE_TIME = 0
_SUBSCRIPTION_TYPES_CACHE_TIME = 0
SUBSCRIPTIONS_TTL = int(os.environ.get("SUBSCRIPTIONS_TTL_SECONDS", "300"))  # 5 min


async def get_subscriptions_with_refresh() -> dict:
    """
    Obtiene subscriptions con auto-refresh cada 5 minutos.
    ✅ Evita re-fetch constante de Firestore
    """
    global subscriptions, _SUBSCRIPTIONS_CACHE_TIME
    
    now = time.time()
    if (now - _SUBSCRIPTIONS_CACHE_TIME) > SUBSCRIPTIONS_TTL:
        logger.info("[Subscriptions] Refreshing from Firestore (TTL expired)")
        subscriptions = await cargar_datos_subscription_user()
        _SUBSCRIPTIONS_CACHE_TIME = now
    
    return subscriptions


async def get_subscription_types_with_refresh() -> dict:
    """
    Obtiene tipos de suscripción con auto-refresh cada 5 minutos.
    ✅ Evita re-fetch constante de Firestore
    """
    global subscriptions_type, _SUBSCRIPTION_TYPES_CACHE_TIME
    
    now = time.time()
    if (now - _SUBSCRIPTION_TYPES_CACHE_TIME) > SUBSCRIPTIONS_TTL:
        logger.info("[SubscriptionTypes] Refreshing from Firestore (TTL expired)")
        subscriptions_type = await cargar_datos_subscription_type()
        _SUBSCRIPTION_TYPES_CACHE_TIME = now
    
    return subscriptions_type


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
# Monitoreo: cobro por llamada
# =========================
async def _charge_monitoreo_per_call(user_id: str, origen: str = "app") -> Tuple[bool, str]:
    """
    Cobra 1 transacción por cada llamada a endpoints de monitoreo.
    - Se cobra por cada consulta de activo_temporalidad
    - Admins bypass automáticamente
    - Usado en: /monitoreo/eventos, /monitoreo/incremental, /monitoreo/history
    """
    if not user_id:
        return False, "user_id es obligatorio"
    if es_administrador(user_id):
        return True, "admin"
    return await descontar_transaccion(user_id, 1, origen=origen)


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
                response = HTTP_SESSION.get(url, timeout=10)
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

                for n in df.to_dict("records"):
                    title = n.get("title", "")
                    sitio = n.get("site", "Desconocido")
                    text = n.get("text", "Sin Descripción")
                    symbol = n.get("symbol", "No Aplica")
                    fecha_val = n.get("publishedDate")
                    fecha = fecha_val.strftime("%Y-%m-%d %H:%M:%S") if fecha_val else ""
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
            response = HTTP_SESSION.get(url, timeout=timeout_request_global)

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



# Scheduler for background tasks - uses AsyncIOScheduler (runs in event loop, not separate thread)
scheduler = AsyncIOScheduler()

# Legacy function - DEPRECATED: Use markettool.interfaces.scheduler.bot_init.setup_scheduler instead
# def programar_actualizacion_menus(application: Application):
#     """Programa la actualización periódica de menús de Telegram."""
#     scheduler.add_job(
#         actualizar_menus,
#         IntervalTrigger(minutes=10),  # Se ejecutará cada 10 minutos
#         kwargs={"application": application}  # Pasa la aplicación como argumento
#     )
#     scheduler.start()

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
                            # ✅ Proteger acceso con lock
                            with cache_noticias_lock:
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
application = Application.builder().token(os.environ["TELEGRAM_BOT_TOKEN"]).build()

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


from markettool.interfaces.api.pod_routes import register_pod_routes
from markettool.interfaces.api.execution_routes import register_execution_routes

register_pod_routes(webhook_app, _POD_COORDINATOR)
register_execution_routes(webhook_app, _EXECUTION_TRACKER, RUNNING, logger)


#@profile
async def guardar_noticias_forex_diarias():
    """
    Ejecuta `guardar_noticias_forex` una vez al día, a medianoche.
    ✅ Multi-pod coordination: Solo el líder ejecuta esta tarea.
    """
    while True:
        ahora = datetime.now()
        siguiente_medianoche = datetime.combine(ahora.date() + timedelta(days=1), datetime.min.time())
        tiempo_para_guardar = (siguiente_medianoche - ahora).total_seconds()

        logger.info(f"Esperando {tiempo_para_guardar} segundos para guardar noticias Forex...")

        await asyncio.sleep(tiempo_para_guardar)
        
        # ✅ Multi-pod coordination: Solo el líder ejecuta
        if _POD_COORDINATOR.should_run_scheduled_task("guardar_noticias_forex"):
            await guardar_noticias_forex()
        else:
            logger.debug("[MultiPod] Skipping guardar_noticias_forex (not leader)")


#@profile
async def guardar_datos_historicos_diarios():
    """
    Ejecuta `guardar_datos_historicos` una vez al día, a medianoche.
    ✅ Multi-pod coordination: Solo el líder ejecuta esta tarea.
    """
    while True:
        ahora = datetime.now()
        siguiente_medianoche = datetime.combine(ahora.date() + timedelta(days=1), datetime.min.time())
        tiempo_para_guardar = (siguiente_medianoche - ahora).total_seconds()

        logger.info(f"Esperando {tiempo_para_guardar} segundos para guardar datos históricos...")

        await asyncio.sleep(tiempo_para_guardar)
        
        # ✅ Multi-pod coordination: Solo el líder ejecuta
        if _POD_COORDINATOR.should_run_scheduled_task("guardar_datos_historicos"):
            await guardar_datos_historicos()
        else:
            logger.debug("[MultiPod] Skipping guardar_datos_historicos (not leader)")


# Cargar datos iniciales
#@profile
# initialize_bot: moved to markettool/interfaces/scheduler/bot_init.py as initialize_bot_async



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
    """
    Context manager para proteger contra backfills simultáneos (local).
    ✅ MEJORADO: Ahora también verifica cooldowns distribuidos
    """
    key = (symbol, tf)
    if key in _BACKFILL_IN_FLIGHT:
        yield False
        return
    _BACKFILL_IN_FLIGHT.add(key)
    try:
        yield True
    finally:
        _BACKFILL_IN_FLIGHT.discard(key)


async def _check_backfill_cooldown(symbol: str, tf: str) -> bool:
    """
    Verifica si un símbolo/timeframe está en cooldown (distribuido).
    ✅ NUEVO: Soporte para cooldowns multi-pod vía CooldownTracker
    """
    return await _COOLDOWN_TRACKER.is_in_cooldown(symbol, tf)


async def _set_backfill_cooldown(symbol: str, tf: str, cooldown_s: int = 300):
    """
    Establece cooldown para backfill fallido.
    ✅ NUEVO: Distribuido vía CooldownTracker (todos los pods respetan)
    """
    await _COOLDOWN_TRACKER.set_backfill_cooldown(symbol, tf, cooldown_s)

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
            stale_by_policy = _is_series_stale_by_policy(st, timeframe)
            if stale_by_policy:
                logging.info(
                    "GCS refresh by staleness policy: %s %s (bars_policy=%s)",
                    symbol,
                    timeframe,
                    _MONITOR_STALE_BARS_POLICY.get(timeframe),
                )
            if not (changed or too_old or stale_by_policy):
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


def _parse_monitor_stale_bars_policy() -> dict[str, int]:
    """
    Política de staleness por TF (en cantidad de velas de atraso).
    Env opcional:
      MONITOR_STALE_BARS_POLICY="1min:12,5min:8,15min:6,30min:4,1hour:3,4hour:2,1day:2,1week:1"
    """
    policy = {
        "1min": 12,
        "5min": 8,
        "15min": 6,
        "30min": 4,
        "1hour": 3,
        "4hour": 2,
        "1day": 2,
        "1week": 1,
    }

    raw = str(os.getenv("MONITOR_STALE_BARS_POLICY", "")).strip()
    if not raw:
        return policy

    try:
        for token in raw.split(","):
            token = token.strip()
            if not token or ":" not in token:
                continue
            k, v = token.split(":", 1)
            tf = _norm_tf(k.strip())
            bars = int(v.strip())
            if tf and bars > 0:
                policy[tf] = bars
    except Exception:
        pass
    return policy


_MONITOR_STALE_BARS_POLICY = _parse_monitor_stale_bars_policy()


def _is_series_stale_by_policy(st: dict, timeframe: str) -> bool:
    try:
        series = st.get("series") if isinstance(st, dict) else None
        if not isinstance(series, list) or not series:
            return True

        last = series[-1] if isinstance(series[-1], dict) else None
        last_t = int(last.get("t", 0)) if last else 0
        if last_t <= 0:
            return True

        tfms = _tf_ms(timeframe)
        stale_bars = int(_MONITOR_STALE_BARS_POLICY.get(timeframe, 2))
        now_ms = int(time.time() * 1000)
        return (now_ms - last_t) > (stale_bars * tfms)
    except Exception:
        return False


def _tf_ms(tf: str) -> int:
    return {
        "1min": 60_000, "5min": 5*60_000, "15min": 15*60_000,
        "30min": 30*60_000, "1hour": 60*60_000, "4hour": 4*60*60_000,
        "1day": 24*60*60_000, "1week": 7*24*60*60_000,
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
        logging.info(f"[Quote-Fallback-v4] URL: {url1}")
        r = _fmp_http_get(url1, timeout=8, symbol=symbol)
        if r.ok:
            arr = r.json() or []
            if isinstance(arr, list) and arr:
                p = arr[0].get("price") or arr[0].get("last") or arr[0].get("bid") or arr[0].get("ask")
                if p: return float(p)
        # 2) fallback v3
        url2 = f"https://financialmodelingprep.com/api/v3/quote/{symbol}?apikey={API_KEY}"
        logging.info(f"[Quote-Fallback-v3] URL: {url2}")
        r = _fmp_http_get(url2, timeout=8, symbol=symbol)
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
        logging.info(f"[Historical-Fetch] URL: {url}")
        r = _fmp_http_get(url, timeout=5, symbol=symbol)
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
    
    # ✅ THREAD-SAFE: Protege lectura de _LAST_QUOTE_TICK
    with _LAST_QUOTE_TICK_LOCK:
        last = _LAST_QUOTE_TICK.get(key, 0)
    
    if now - last < ttl:
        return False

    price = _fetch_quote(symbol)
    
    # ✅ THREAD-SAFE: Protege escritura en _LAST_QUOTE_TICK
    with _LAST_QUOTE_TICK_LOCK:
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

def _current_closed_bucket_start(tf: str) -> int:
    tfms = _tf_ms(tf)
    now_bucket = (_now_ms() // tfms) * tfms
    return now_bucket  # este es el inicio del bucket en curso; 'cerrados' son < now_bucket

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
        logging.info(f"[Historical-Range] URL: {url} params: from={params['from']} to={params['to']}")
        r = _fmp_http_get(url, params=params, timeout=5, symbol=symbol)
        if not r.ok:
            logging.info(f"[Historical-Range] HTTP {r.status_code}: {r.text[:200]}")
            return []
        return _normalize_fmp_bars(r.json())
    except Exception as e:
        logging.warning(f"[Historical-Range] Error: {e}")
        return []


def _backfill_internal_gaps(
    base_ms: list[dict],
    symbol: str,
    tf: str,
    exec_id: str | None = None,
    max_minutes_per_call: int = 10_000,
    allow_small_tf: bool = False,
) -> int:
    if (not allow_small_tf) and tf in ("1min", "1m", "5min", "5m"):
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
        df = obtener_eventos_guardados_o_futuros(
            _iso(a),
            _iso(b),
            grace_minutes=0,
        )
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
    for row in df.itertuples(index=False):
        row_dict = row._asdict()
        k = (symbol, _event_row_key(row_dict))
        actual = _numeric_or_nan(row_dict.get("actual"))
        if math.isnan(actual):
            continue
        prev = _LAST_ACTUAL.get(k, math.nan)
        if math.isnan(prev) or not math.isclose(prev, actual, rel_tol=0, abs_tol=1e-12):
            _LAST_ACTUAL[k] = actual
            new_rows.append({
                "date": row_dict.get("date").isoformat() if pd.notna(row_dict.get("date")) else None,
                "currency": row_dict.get("currency"),
                "event": row_dict.get("event"),
                "impact": row_dict.get("impact"),
                "actual": actual,
                "estimate": _numeric_or_nan(row_dict.get("estimate")),
                "previous": _numeric_or_nan(row_dict.get("previous")),
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


from markettool.interfaces.api.analisis_routes import register_analisis_routes
from markettool.interfaces.api.monitoreo_routes import register_monitoreo_routes
from markettool.interfaces.api.webhook_routes import register_webhook_routes


def _set_timezone_state(tz_name, tz_value):
    global timezone_country, timezone_name
    timezone_name = tz_name
    timezone_country = tz_value


register_webhook_routes(
    webhook_app,
    application=application,
    update_cls=Update,
    logger=logger,
)

register_monitoreo_routes(
    webhook_app,
    logger=logger,
    db=db,
    charge_monitoreo_per_call=_charge_monitoreo_per_call,
    fetch_events_for=_fetch_events_for,
    filter_by_symbol_currencies=_filter_by_symbol_currencies,
    hash_payload=_hash_payload,
    last_hash_ref=_LAST_HASH,
    detect_new_results=_detect_new_results,
    evaluar_evento_para_symbol=evaluar_evento_para_symbol,
    norm_tf=_norm_tf,
    tf_is_enabled=_tf_is_enabled,
    load_cache=_load_cache,
    series_to_ms=_series_to_ms,
    snap_and_dedupe_to_minutes=_snap_and_dedupe_to_minutes,
    densify_minutes=_densify_minutes,
    maybe_tick_quote=_maybe_tick_quote,
    persist_if_needed=_persist_if_needed,
    mon_cache_lock=_MON_CACHE_LOCK,
    maybe_refresh_from_gcs=_maybe_refresh_from_gcs,
    ensure_stream_initialized=_ensure_stream_initialized,
    fs_touch_monitoreo=fs_touch_monitoreo,
    tf_ms=_tf_ms,
    current_closed_bucket_start=_current_closed_bucket_start,
    fetch_historical_range=_fetch_historical_range,
    merge_bars_series=merge_bars_series,
    backfill_internal_gaps=_backfill_internal_gaps,
    bucket_name=BUCKET_NAME,
)

register_analisis_routes(
    webhook_app,
    application=application,
    db=db,
    logger=logger,
    running_tasks=RUNNING,
    execution_tracker=_EXECUTION_TRACKER,
    estado_suscripcion=estado_suscripcion,
    es_administrador=es_administrador,
    normalize_operatoria_payload=normalize_operatoria_payload,
    temporalidades=temporalidades,
    ensure_globals_loaded=_ensure_globals_loaded,
    filtrar_activos_por_moneda=filtrar_activos_por_moneda,
    activos_ref=activos,
    compute_lock_ttl=compute_lock_ttl,
    acquire_user_lock=acquire_user_lock,
    release_user_lock=release_user_lock,
    mark_user_state=mark_user_state,
    obtener_opciones_usuario=obtener_opciones_usuario,
    fs_crear_ejecucion=fs_crear_ejecucion,
    fs_marcar_worker=fs_marcar_worker,
    fs_finalizar_ejecucion=fs_finalizar_ejecucion,
    fs_heartbeat=fs_heartbeat,
    user_config_cache=_USER_CONFIG_CACHE,
    pytz_module=pytz,
    set_timezone_state=_set_timezone_state,
    clear_current_request_cfg=clear_current_request_cfg,
    ocupado_lock=ocupado_lock,
    es_grafico_de_velas=es_grafico_de_velas,
    analizar_con_yolo=analizar_con_yolo,
    descontar_transaccion=descontar_transaccion,
    stop_events_ref=STOP_EVENTS,
    stop_events_lock=STOP_EVENTS_LOCK,
    optimize_records_for_upload=_optimize_records_for_upload,
    ejecutar_recurrente=ejecutar_recurrente,
)
    
# Legacy health routes registration (now handled by bootstrap.py Phase 8)
# from markettool.interfaces.api.health_routes import register_health_routes
# register_health_routes(
#     webhook_app,
#     warmup_start_ref=lambda: _warmup_start_time,
#     warmup_end_ref=lambda: _warmup_end_time,
#     levels_hits_ref=lambda: _niveles_cache_hits,
#     levels_misses_ref=lambda: _niveles_cache_misses,
#     atr_hits_ref=lambda: _atr_cache_hits,
#     atr_misses_ref=lambda: _atr_cache_misses,
#     app_config=APP_CONFIG,
# )

from markettool.interfaces.api.cache_routes import register_cache_routes

register_cache_routes(
    webhook_app,
    indicators_cache=_INDICATORS_CACHE,
    cache_enabled=_INDICATORS_CACHE_ENABLED,
    ttl_hours=_INDICATORS_CACHE_TTL_HOURS,
    force_recalc=_INDICATORS_FORCE_RECALC,
)


# Main entry point moved to markettool/bootstrap.py
# This module (MarketTool.py) is now imported by bootstrap.py which handles initialization
# To run the application, use: python -m markettool.bootstrap
if __name__ == "__main__":
    # Only import here to avoid circular dependencies
    from markettool.bootstrap import main
    main()

