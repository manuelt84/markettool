# -*- coding: utf-8 -*-
import matplotlib as mpl
#mpl.rcParams['figure.max_open_warning'] = 200
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from urllib.parse import urlencode
import requests
import pandas as pd
pd.set_option('future.no_silent_downcasting', True)
import numpy as np
from datetime import datetime, timedelta
import datetime as _dt
from statsmodels.tsa.arima.model import ARIMA
import concurrent.futures
import telegram
from io import BytesIO
import os
import json
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, BotCommand, BotCommandScopeChat
from telegram.ext import  ApplicationBuilder, Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters, CallbackContext
from telegram.helpers import escape_markdown
import pytz  # Para manejar las zonas horarias
from urllib.parse import urlencode
import investpy
import matplotlib.dates as mdates
import warnings
warnings.filterwarnings("ignore", message="Maximum Likelihood optimization failed to converge")
from textblob import TextBlob
import asyncio
from pandas.tseries.offsets import CustomBusinessDay
from scipy.signal import argrelextrema
from collections import Counter
from numba import njit
from joblib import Parallel, delayed, parallel_backend
import re
from telegram.error import TimedOut
import threading
from scipy.signal import argrelextrema
import hashlib
import time
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.schedulers.background import BackgroundScheduler
from asyncio import Lock, Semaphore
from threading import Lock
from concurrent.futures import ThreadPoolExecutor
import aiofiles
from collections import defaultdict
import logging
from flask import Flask, request, jsonify
from asgiref.wsgi import WsgiToAsgi
import sys
import uvicorn
from uvicorn.config import LOGGING_CONFIG
from google.cloud import firestore
from icalendar import Calendar, Event
import tempfile
from google.cloud import storage
from ultralytics import YOLO
import cv2
from telegram import InputFile
import base64
import easyocr
import cv2
import socket
import torch
import uuid
from typing import Any, Iterable, Mapping, Optional, Callable
from datetime import timedelta, date, datetime, UTC
from textwrap import wrap
from functools import partial
import math
import statistics
from collections.abc import Sequence

timezone_country = pytz.timezone('America/Santiago')
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
API_KEY =  os.environ["API_FMP"]

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

def _sanitize_bars(bars):
    """Devuelve None o un entero > 0."""
    return bars if isinstance(bars, int) and bars > 0 else None

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

# Mapeo de TF frontend -> backend
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

def clear_current_request_cfg(user_chat_id: str) -> None:
    REQUEST_OPERATORIA.pop(user_chat_id, None)

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


def _norm_tf(tf: str) -> str:
    tf = str(tf).lower().strip()
    return _TF_ALIAS.get(tf, tf)

def _fmt_for_tf(tf: str) -> str:
    # FMP acepta fecha-hora para intradía; para 1d/1w basta YYYY-MM-DD
    return '%Y-%m-%d' if tf in ('1day', '1week') else '%Y-%m-%d %H:%M:%S'

def _tf_to_backend(tf: str) -> str | None:
    return TF_MAP.get(str(tf).lower().strip())

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

def _fmt_num(x, nd=5):
    if x is None or (isinstance(x, float) and np.isnan(x)): 
        return ""
    try:
        # evita notación científica y quita ceros innecesarios
        s = f"{float(x):.{nd}f}".rstrip('0').rstrip('.')
        return s
    except Exception:
        return str(x)

def _fmt_pct(x):
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return ""
    try:
        return _fmt_num(float(x), nd=1)
    except Exception:
        return str(x)

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
def _wrap_text(s, width: int) -> str:
    s = "" if s is None else str(s)
    if not s:
        return ""
    return "\n".join(wrap(s, width=width))

def _fmt_num(x, nd=4):
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return ""
    try:
        return f"{float(x):.{nd}f}"
    except Exception:
        return str(x)

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


def _max_line_len(s: str) -> int:
    return max((len(x) for x in str(s).split("\n")), default=0)

def _to_list_str_df(df: pd.DataFrame, wrap_cell=WRAP_WIDTH_CELL, wrap_head=WRAP_WIDTH_HEADER):
    headers = [_wrap_text(c, wrap_head) for c in df.columns]
    cells   = df.astype(str).applymap(lambda x: _wrap_text(x, wrap_cell))
    return headers, cells

def _col_char_widths(headers: list[str], cells: pd.DataFrame,
                     min_chars=6, max_chars=40) -> list[int]:
    w = []
    for j, h in enumerate(headers):
        max_cell = max((_max_line_len(v) for v in cells.iloc[:, j].tolist()), default=0)
        width_j  = max(_max_line_len(h), max_cell) + 2
        w.append(min(max(width_j, min_chars), max_chars))
    return w

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

def _render_table_image(df: pd.DataFrame, max_rows=30, font=12, dpi=180):
    """Devuelve un BytesIO o una lista de BytesIO si hay varias páginas."""
    parts = []
    for i in range(0, len(df), max_rows):
        part = df.iloc[i:i + max_rows]
        headers, cells = _to_list_str_df(part)
        widths = _col_char_widths(headers, cells)
        parts.append(_render_table(headers, cells, widths, font=font, dpi=dpi))
    return parts[0] if len(parts) == 1 else parts

def _wrap_header(h: str, width: int) -> str:
    return _wrap_text(h, width)

def safe_name(s: str) -> str:
    return re.sub(r'[^A-Za-z0-9._-]+', '_', str(s))


def _es_serializable_basico(v: Any) -> bool:
    import numpy as np
    import pandas as pd
    from datetime import datetime, date
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


def _json_default(o):
    if isinstance(o, (np.integer,)):        return int(o)
    if isinstance(o, (np.floating,)):       return float(o)
    if isinstance(o, (np.bool_, bool)):     return bool(o)
    if isinstance(o, (pd.Timestamp, datetime, date)):
        return o.isoformat()

    # 🔧 Soporte explícito para pandas
    if isinstance(o, pd.DataFrame):
        d = o.where(pd.notnull(o), None)
        return d.to_dict(orient="records")
    if isinstance(o, pd.Series):
        s = o.where(pd.notnull(o), None)
        return s.tolist()

    if o is None: return None
    raise TypeError(f"{type(o).__name__} no es JSON serializable")


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


def _json_sanitize_df(d: pd.DataFrame) -> pd.DataFrame:
    d = d.where(pd.notnull(d), None)
    for c in d.columns:
        if pd.api.types.is_bool_dtype(d[c]):   d[c] = d[c].astype(object)
        elif pd.api.types.is_integer_dtype(d[c]): d[c] = d[c].astype(object)
        elif pd.api.types.is_float_dtype(d[c]):   d[c] = d[c].astype(float)
    return d

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

    # flags
    if flag_extras:
        if put_flags_inside:
            flags = d[flag_extras].copy()
            for c in flags.columns:
                flags[c] = flags[c].astype('boolean')
            # construir dict por fila solo con flags True/False (None si NA)
            flags_dicts = []
            for _, row in flags.iterrows():
                fd = {rename_extras.get(k, k): (None if pd.isna(row[k]) else bool(row[k])) for k in flags.columns}
                flags_dicts.append(fd)
            out['flags'] = flags_dicts
        else:
            for c in flag_extras:
                out[ rename_extras.get(c, c) ] = d[c].astype('boolean').astype(object)

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

def _json_safe_numeric(s: pd.Series) -> pd.Series:
    """Convierte a numérico, limpia ±inf/NaN y devuelve None (tipo object) donde falte valor."""
    s = pd.to_numeric(s, errors='coerce')
    s = s.replace([np.inf, -np.inf], np.nan)
    return s.where(s.notna(), None).astype(object)

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

async def subir_ohlcv_enriquecido_y_registrar(
    *, exec_id: str, chat_id: str, symbol: str, temporalidad: str,
    df_velas: pd.DataFrame, df_indicadores: pd.DataFrame | None,
    subir_a_bucket_y_obtener_url,
    niveles: dict | None = None, entradas: dict | None = None,
    extra_metadata: dict | None = None,
) -> str | None:
    from datetime import datetime, UTC

    d_norm = normalizar_df_ohlcv(df_velas)
    payload = construir_payload_enriquecido(
        symbol, temporalidad, d_norm,
        extra_meta=extra_metadata or {},
        niveles=niveles, entradas=entradas,
        include_extras_en_candles=None,   # None = incluye extras si existen
        candles_with_extras=True          # o False si quieres OHLCV puro
    )
    return await guardar_json_en_storage_y_registrar(
        exec_id=exec_id, chat_id=chat_id,
        nombre_base=f"{symbol}_{temporalidad}_enriched",
        data=payload,
        subir_a_bucket_y_obtener_url=subir_a_bucket_y_obtener_url,
        metadata=extra_metadata or {},
    )


async def subir_imagenes_eventos_y_registrar(
    imagen_eventos,                 # BytesIO o list[BytesIO] desde generar_imagen_eventos_oportunidades
    exec_id: str,
    chat_id: str,
    moneda_filtro: str,
    subir_a_bucket_y_obtener_url,   # tu función async (o sync) que retorna la URL
) -> list[str]:
    if imagen_eventos is None:
        return []

    # Normaliza a lista de buffers
    buffers = imagen_eventos if isinstance(imagen_eventos, list) else [imagen_eventos]

    urls_subidas: list[str] = []
    for idx, buf in enumerate(buffers, start=1):
        nombre = f"{moneda_filtro.upper()}_eventos_oportunidades_{idx:02d}.png"
        object_path = build_object_path(exec_id, nombre)  # exec/<exec_id>/<nombre>
        local_path = f"/tmp/{nombre}"

        buf.seek(0)
        with open(local_path, "wb") as f:
            f.write(buf.getvalue())

        # 👈 SUBE y espera la URL
        if asyncio.iscoroutinefunction(subir_a_bucket_y_obtener_url):
            url = await subir_a_bucket_y_obtener_url(local_path, object_path)
        else:
            url = await asyncio.to_thread(subir_a_bucket_y_obtener_url, local_path, object_path)

        urls_subidas.append(url)

        # Registra en Firestore (sync → thread)
        await asyncio.to_thread(
            fs_registrar_archivo_generado,
            exec_id, chat_id,
            tipo="png",
            nombre=nombre,
            gcs_path=object_path,
            signed_url=url,              # o None si prefieres firmar on-demand
            content_type="image/png",
            metadata={"scope": "eventos_oportunidades", "part": idx},
        )

    return urls_subidas


async def subir_json_eventos_filtrados(
    df_eventos: pd.DataFrame,
    divisas_oportunidades,
    exec_id: str,
    chat_id: str,
    moneda_filtro: str,
    timezone_country,                # tu tz destino
    subir_a_bucket_y_obtener_url,    # async o sync
) -> str | None:

    # Normaliza divisas a lista plana de strings
    divs_arr = np.asarray(divisas_oportunidades).ravel() if divisas_oportunidades is not None else np.array([])
    divs = [str(x) for x in divs_arr.tolist() if pd.notna(x) and str(x).strip() != ""]

    if df_eventos is None or df_eventos.empty or len(divs) == 0:
        return None

    if 'date' not in df_eventos.columns or 'currency' not in df_eventos.columns:
        return None

    df = df_eventos[df_eventos['currency'].isin(divs)].copy()
    if df.empty:
        return None

    # Asegura datetime con tz y formatea
    df['date'] = pd.to_datetime(df['date'], errors='coerce', utc=True)
    df = df[df['date'].notna()]
    if df.empty:
        return None

    df['date'] = df['date'].dt.tz_convert(timezone_country).dt.strftime('%Y-%m-%d %H:%M:%S %Z')

    records = df.where(pd.notnull(df), None).to_dict('records')

    json_url = await guardar_json_en_storage_y_registrar(
        exec_id=exec_id,
        chat_id=chat_id,
        nombre_base=f"{moneda_filtro.upper()}_eventos_oportunidades",
        data=records,
        subir_a_bucket_y_obtener_url=subir_a_bucket_y_obtener_url,
        metadata={
            "scope": "eventos_oportunidades",
            "moneda_filtro": moneda_filtro,
            "divisas": divs
        },
    )
    return json_url


def build_object_path(exec_id: str, nombre: str) -> str:
    # Estructura uniforme en el bucket por ejecución
    return f"exec/{exec_id}/{nombre}"


def fs_crear_ejecucion(chat_id: str, activos_solicitados: list[str], origen: str, opciones_usuario: list[str]) -> str:
    exec_id = uuid.uuid4().hex
    doc_ref = db.collection("ejecuciones").document(exec_id)
    payload = {
        "exec_id": exec_id,
        "chat_id": chat_id,
        "activos_solicitados": list(map(str, activos_solicitados or [])),
        "origen": str(origen),
        "opciones_usuario": list(map(str, opciones_usuario or [])),
        "estado": "en_proceso",
        "archivos": 0,
        "created_at": firestore.SERVER_TIMESTAMP,
        "updated_at": firestore.SERVER_TIMESTAMP,
    }
    doc_ref.set(payload)
    return exec_id

def fs_actualizar_ejecucion(exec_id: str, **campos):
    campos = _sanitize_for_firestore({k: v for k, v in campos.items() if v is not None})
    if not isinstance(campos, dict):
        campos = {}
    campos["updated_at"] = firestore.SERVER_TIMESTAMP
    db.collection("ejecuciones").document(exec_id).update(campos)

def fs_finalizar_ejecucion(exec_id: str, estado: str = "completado", resumen: dict | None = None):
    db.collection("ejecuciones").document(exec_id).update({
        "estado": str(estado),
        "resumen": _sanitize_for_firestore(resumen or {}),
        "updated_at": firestore.SERVER_TIMESTAMP
    })

def fs_registrar_archivo_generado(
    exec_id: str,
    chat_id: str,
    *,
    tipo: str,               # 'csv' | 'json' | 'png' | 'html' | ...
    nombre: str,             # p.ej. USD_principal.csv
    gcs_path: str,           # p.ej. exec/<exec_id>/USD_principal.csv
    signed_url: str | None = None,
    content_type: str | None = None,
    metadata: dict | None = None,
):
    payload = {
        "exec_id": str(exec_id),
        "chat_id": str(chat_id),
        "tipo": str(tipo),
        "nombre": str(nombre),
        "gcs_path": str(gcs_path),      # SOLO referencia
        "signed_url": str(signed_url) if signed_url else None,
        "content_type": str(content_type) if content_type else None,
        "metadata": _sanitize_for_firestore(metadata or {}),
        "created_at": firestore.SERVER_TIMESTAMP,
    }
    db.collection("archivos_generados").add(payload)
    db.collection("ejecuciones").document(exec_id).update({
        "archivos": firestore.Increment(1),
        "updated_at": firestore.SERVER_TIMESTAMP
    })

async def guardar_json_en_storage_y_registrar(
    *, exec_id: str, chat_id: str, nombre_base: str,
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
        exec_id, chat_id,
        tipo="json", nombre=nombre, gcs_path=object_path,
        signed_url=url_publica, content_type="application/json",
        metadata=metadata or {},
    )
    return url_publica


def actualizar_estado_esperando_imagen(chat_id: str):
    db = firestore.client()
    user_ref = db.collection("user_states").document(chat_id)
    user_ref.set({"estado": "esperando_grafico_ia"}, merge=True)

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


async def subir_a_bucket_y_obtener_url(nombre_local, nombre_remoto=None, carpeta='analisis'):
    nombre_remoto = nombre_remoto or os.path.basename(nombre_local)
    bucket_name = "markettool_bucket"  # 🔁 Reemplazar con el nombre real de tu bucket

    client = storage.Client()
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(f"{carpeta}/{nombre_remoto}")
    blob.upload_from_filename(nombre_local)
    blob.make_public()  # O usar signed_url si prefieres enlaces temporales

    return blob.public_url

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

def return_state(user_chat_id):
    user_ref = db.collection("user_states").document(user_chat_id)
    user_data = user_ref.get()

    if user_data.exists:
        return user_data.to_dict().get("estado")


def mark_user_state(user_chat_id, state, destinatarios=None):
    """
    Marca el estado del usuario en Firestore.
    Si 'destinatarios' no es None, también los guarda.
    """
    user_ref = db.collection("user_states").document(user_chat_id)

    # Construir el diccionario de actualización
    update_data = {"estado": state}

    if destinatarios is not None:
        update_data["destinatarios"] = destinatarios

    user_ref.set(update_data, merge=True)  # Merge=True evita sobrescribir otros datos existentes



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

async def cargar_timezone_por_defecto(chat_id):
    """Carga la zona horaria predeterminada para un chat_id."""
    chat_ids = await cargar_chat_ids()
    return chat_ids.get(chat_id, {}).get("timezone", "America/Santiago")

def detectar_categoria(event):
    for palabra_clave, categoria in palabras_clave_categoria.items():
        if palabra_clave.lower() in event.lower():
            return categoria
        return None  # Si no se encuentra una categoría, devuelve None

# Cargar la lista de chat_ids desde el archivo
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




def obtener_timezone(user_chat_id):
    """Devuelve la zona horaria de un chat_id si está registrada, o una por defecto."""
    chat_ids = cargar_chat_ids()
    return chat_ids.get(user_chat_id, {}).get("timezone", "America/Santiago")

async def actualizar_timezone(user_chat_id, nueva_timezone):
    """Actualiza la zona horaria de un chat_id."""
    await guardar_chat_id(user_chat_id, timezone=nueva_timezone)


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

def _coerce_float_safe(v):
    try:
        return float(v)
    except Exception:
        return None

def obtener_monedas(symbol):
    """
    Identifica si un símbolo es un par de divisas o un símbolo que no tiene divisa secundaria.
    """
    # Verificar si el símbolo parece un par Forex
    if len(symbol) > 3 and symbol[-3:].isalpha() and symbol[:-3].isalpha():
        return symbol[:-3], symbol[-3:]  # Divisa base y divisa secundaria

    # Si no es un par Forex, asumir que no tiene divisa secundaria
    return symbol, None

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
    fecha_inicio = pd.to_datetime(fecha_inicio).tz_localize('UTC') if pd.to_datetime(fecha_inicio).tzinfo is None else pd.to_datetime(fecha_inicio)

    # Ajustar fecha_fin para incluir todo el día 23
    fecha_fin = pd.to_datetime(fecha_fin).tz_localize('UTC') if pd.to_datetime(fecha_fin).tzinfo is None else pd.to_datetime(fecha_fin)
    fecha_fin = fecha_fin + timedelta(days=1)  # Aumentar un día
    fecha_fin = fecha_fin.replace(hour=0, minute=0, second=0)  # Establecer a las 00:00:00 del siguiente día


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
                            df_nuevas['publishedDate'] = df_nuevas['publishedDate'].dt.tz_localize('UTC', ambiguous='NaT', nonexistent='shift_forward')

                        # Convertir las fechas a la zona horaria configurada
                        df_nuevas['publishedDate'] = df_nuevas['publishedDate'].dt.tz_convert(timezone_country)

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

def obtener_noticias_simbolo(symbol, fecha_inicio, fecha_fin, limite=50, max_reintentos=3, tiempo_espera_inicial=5):
    """
    Obtiene noticias del mercado Forex para un símbolo dado.
    Utiliza caché en memoria y actualiza con los datos más recientes de la API.
    """

    # Convertir fechas de inicio y fin a UTC
    fecha_inicio = pd.to_datetime(fecha_inicio).tz_localize('UTC') if pd.to_datetime(fecha_inicio).tzinfo is None else pd.to_datetime(fecha_inicio)
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
                            df_nuevas['publishedDate'] = df_nuevas['publishedDate'].dt.tz_localize('UTC', ambiguous='NaT', nonexistent='shift_forward')

                        # Convertir las fechas a la zona horaria configurada
                        df_nuevas['publishedDate'] = df_nuevas['publishedDate'].dt.tz_convert(timezone_country)

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

async def obtener_noticias_generales(update, context):
    limite = 20
    max_reintentos = 3
    tiempo_espera_inicial = 5
    user_chat_id = str(update.effective_chat.id)
    noticias = []
    actualizar_estado_usuario(user_chat_id, "en ejecución")
    mark_user_state(user_chat_id, "en ejecución")

    try:
        # Construir la URL del endpoint
        endpoint = "https://financialmodelingprep.com/api/v4/general_news"
        url = f"{endpoint}?limit={limite}&apikey={API_KEY}"

        #logger.info(f"URL de noticias generales: {url}")

        # Variables para control de reintentos
        reintento = 0
        tiempo_espera = tiempo_espera_inicial

        while reintento < max_reintentos:
            try:
                # Realizar la solicitud
                response = requests.get(url, timeout=10)

                if response.status_code == 200:
                    if not response.text.strip():
                        logger.info("La respuesta de la API está vacía.")
                        break

                    noticias = response.json()
                    break
                else:
                    logger.info(f"Error en la API: {response.status_code}")
            except requests.exceptions.RequestException as e:
                logger.info(f"Error al obtener noticias generales: {e}")
                reintento += 1
                if reintento < max_reintentos:
                    logger.info(f"Reintentando en {tiempo_espera} segundos...")
                    time.sleep(tiempo_espera)
                    tiempo_espera *= 2

        # Procesar las noticias recibidas
        if isinstance(noticias, list) and len(noticias) > 0:
            df_nuevas = pd.DataFrame(noticias)

            if 'publishedDate' in df_nuevas.columns:
                # Convertir fechas a datetime y UTC
                df_nuevas['publishedDate'] = pd.to_datetime(df_nuevas['publishedDate'], errors='coerce')
                df_nuevas['publishedDate'] = df_nuevas['publishedDate'].dt.tz_convert('UTC')

                for index, noticia in df_nuevas.iterrows():
                    title = noticia['title']
                    sitio = noticia.get('site', 'Desconocido')
                    text = noticia.get('text', 'Sin Descripción')
                    symbol = noticia.get('symbol', 'No Aplica')
                    fecha = noticia['publishedDate'].strftime('%Y-%m-%d %H:%M:%S')
                    importancia = analizar_importancia(title + ' ' + text)
                    url = noticia['url']
                    link_traductor = f"https://translate.google.com/translate?sl=auto&tl=es&u={url}"  # Enlace a Google Translate

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
                if not es_administrador(user_chat_id):
                    success, mensaje = await descontar_transaccion(user_chat_id, 1)
                    if not success:
                        await update.message.reply_text(mensaje)
                
        else:
            logger.info("No se encontraron noticias válidas en la respuesta.")
    except Exception as e:
        logger.info(f"Error en obtener_noticias_generales: {e}")
    finally:
        mark_user_state(user_chat_id, "disponible")

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


# Función para obtener precios históricos por temporalidad
#@profile
import os, json, logging, aiofiles
import pandas as pd

# CARPETA_HISTORICOS debe estar definido en tu módulo
# cache_historicos es global

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



def obtener_datos_historicos_fmp(
    symbol: str,
    temporalidad: str,
    max_reintentos: int = 5,
    tiempo_espera_inicial: int = 5,
    *,
    bars: int | None = None
):
    """
    - Si 'bars' está definido y > 0:
        * NO usa ni actualiza caché.
        * Calcula from/to con base en la TF normalizada y pide a FMP esa ventana.
        * Devuelve exactamente 'bars' velas ordenadas (siempre que la API lo permita).
    - Si 'bars' es None:
        * Comportamiento original incremental usando caché (lee y puede actualizar).
    """
    try:
        tf = _norm_tf(temporalidad)  # '1min','5min','15min','30min','1hour','4hour','1day','1week'
        use_bars = isinstance(bars, int) and bars > 0 and tf in _TF_MINUTES

        # -------------------------
        # MODO 'bars': sin caché
        # -------------------------
        if use_bars:
            now_utc = datetime.now(pytz.utc)
            # Márgen extra para evitar off-by-one
            total_min = _TF_MINUTES[tf] * (bars + 5)
            from_dt = now_utc - timedelta(minutes=total_min)
            to_dt   = now_utc

            fmt = _fmt_for_tf(tf)  # asumes que existe; p.ej. "%Y-%m-%d" o con hora según TF
            from_str = from_dt.strftime(fmt)
            to_str   = to_dt.strftime(fmt)

            url = (
                f"https://financialmodelingprep.com/api/v3/historical-chart/"
                f"{tf}/{symbol}?from={from_str}&to={to_str}&apikey={API_KEY}"
            )

            reintento = 0
            tiempo_espera = tiempo_espera_inicial

            while reintento < max_reintentos:
                try:
                    resp = requests.get(url, timeout=timeout_request_global)
                    if resp.status_code == 200:
                        data = resp.json()
                        if isinstance(data, list) and len(data) > 0:
                            df = pd.DataFrame(data)[['date','open','high','low','close','volume']]
                            df['date'] = pd.to_datetime(df['date'], errors='coerce')
                            df = df.dropna(subset=['date']).set_index('date')
                            df = df.sort_index()
                            # EXACTAMENTE 'bars' al final (por si viene de más)
                            if len(df) > bars:
                                df = df.tail(bars)
                            # Localizar a UTC y convertir a tu zona de salida
                            out = df.copy()
                            out.index = out.index.tz_localize(pytz.utc).tz_convert(timezone_country)
                            return out
                        else:
                            logger.info(f"[FMP bars] Sin datos API para {symbol} {tf}.")
                            return pd.DataFrame()
                    elif resp.status_code == 429:
                        retry_after = int(resp.headers.get("Retry-After", tiempo_espera))
                        logger.info(f"[FMP bars] 429; espero {retry_after}s y reintento.")
                        time.sleep(retry_after)
                        reintento += 1
                    else:
                        logger.info(f"[FMP bars] Error {resp.status_code} URL: {url}")
                        return pd.DataFrame()
                except requests.exceptions.RequestException as e:
                    logger.info(f"[FMP bars] Error de conexión: {e}")
                    reintento += 1
                    if reintento < max_reintentos:
                        logger.info(f"[FMP bars] Reintento en {tiempo_espera}s…")
                        time.sleep(tiempo_espera)
                        tiempo_espera *= 2

            logger.info(f"[FMP bars] Falló tras {max_reintentos} reintentos {symbol} {tf}.")
            return pd.DataFrame()

        # -------------------------------------------
        # MODO incremental (sin 'bars'): con caché
        # -------------------------------------------
        df_local = None
        ultima_fecha = None

        if symbol in cache_historicos and tf in cache_historicos[symbol]:
            df_local = cache_historicos[symbol][tf]
            if df_local is not None and not df_local.empty and 'date' in df_local.columns:
                df_local = df_local.copy()
                df_local['date'] = pd.to_datetime(df_local['date'], errors='coerce')
                df_local = df_local.dropna(subset=['date']).set_index('date')
            ultima_fecha = (df_local.index.max() if (df_local is not None and not df_local.empty) else None)
            logger.info(f"Última fecha en caché {symbol} {tf}: {ultima_fecha}")
        else:
            logger.info(f"Sin caché para {symbol} {tf}")

        if ultima_fecha is not None:
            from_str = ultima_fecha.strftime("%Y-%m-%d")
            to_str   = datetime.now(pytz.utc).strftime("%Y-%m-%d")
            url = (
                f"https://financialmodelingprep.com/api/v3/historical-chart/"
                f"{tf}/{symbol}?from={from_str}&to={to_str}&apikey={API_KEY}"
            )
        else:
            url = (
                f"https://financialmodelingprep.com/api/v3/historical-chart/"
                f"{tf}/{symbol}?apikey={API_KEY}"
            )

        reintento = 0
        tiempo_espera = tiempo_espera_inicial
        while reintento < max_reintentos:
            try:
                response = requests.get(url, timeout=timeout_request_global)
                if response.status_code == 200:
                    data_api = response.json()
                    if isinstance(data_api, list) and len(data_api) > 0:
                        df_api = pd.DataFrame(data_api)[['date','open','high','low','close','volume']]
                        df_api['date'] = pd.to_datetime(df_api['date'], errors='coerce')
                        df_api = df_api.dropna(subset=['date']).set_index('date')

                        if ultima_fecha is not None:
                            # Ambos índices "naive" → comparación válida
                            df_api = df_api[df_api.index > ultima_fecha]

                        if df_local is not None and not df_local.empty and not df_api.empty:
                            df_combinado = pd.concat([df_local, df_api]).drop_duplicates().sort_index()
                        elif df_local is not None and not df_local.empty:
                            df_combinado = df_local
                        elif not df_api.empty:
                            df_combinado = df_api
                        else:
                            df_combinado = pd.DataFrame()

                        # Actualizar caché (MODO incremental SÍ actualiza)
                        if symbol not in cache_historicos:
                            cache_historicos[symbol] = {}
                        cache_historicos[symbol][tf] = df_combinado

                        out = df_combinado.copy()
                        out.index = out.index.tz_localize(pytz.utc).tz_convert(timezone_country)
                        return out
                    else:
                        logger.info("No se encontraron datos nuevos desde la API.")
                        return df_local if df_local is not None else pd.DataFrame()

                elif response.status_code == 429:
                    retry_after = int(response.headers.get("Retry-After", tiempo_espera))
                    logger.info(f"Se excedió el límite de la API. Esperando {retry_after}s…")
                    time.sleep(retry_after)
                    reintento += 1
                else:
                    logger.info(f"Error API FMP: {response.status_code}, URL: {url}")
                    return df_local if df_local is not None else pd.DataFrame()

            except requests.exceptions.RequestException as e:
                logger.info(f"Error de conexión: {e}")
                reintento += 1
                if reintento < max_reintentos:
                    logger.info(f"Reintentando url:{url} en {tiempo_espera}s…")
                    time.sleep(tiempo_espera)
                    tiempo_espera *= 2

        logger.info(f"Falló la obtención de datos para {symbol} {tf} tras {max_reintentos} reintentos.")
        return df_local if df_local is not None else pd.DataFrame()

    except Exception as e:
        logger.info(f"Error inesperado al procesar datos para {symbol} en temporalidad {temporalidad}: {e}")
        return pd.DataFrame()


        
# Función para obtener datos en tiempo real
#@profile
def obtener_dato_realtime_fmp(symbol, max_reintentos=3, tiempo_espera_inicial=5):
    url = f'https://financialmodelingprep.com/api/v3/quote/{symbol}?apikey={API_KEY}'

    reintento = 0
    tiempo_espera = tiempo_espera_inicial

    while reintento < max_reintentos:
        try:
            response = requests.get(url, timeout=timeout_request_global)

            if response.status_code == 200:
                data = response.json()
                if isinstance(data, list) and len(data) > 0:
                    # Obtener el timestamp del JSON y convertirlo a pd.Timestamp
                    timestamp_unix = data[0].get('timestamp', None)
                    if timestamp_unix is not None:
                        fecha_actual = pd.to_datetime(timestamp_unix, unit='s', utc=True).tz_convert(timezone_country)
                    else:
                        # Si no hay timestamp, usar la fecha y hora actual ajustada al país seleccionado
                        fecha_actual = pd.Timestamp.now(tz=timezone_country)

                    return pd.DataFrame([{
                        'date': fecha_actual,
                        'close': data[0]['price']
                    }]).set_index('date')

            elif response.status_code == 429:
                # Manejo del error 429 (Too Many Requests)
                retry_after = int(response.headers.get("Retry-After", tiempo_espera))
                logger.info(f"Se excedió el límite de la API para {symbol}. Esperando {retry_after} segundos antes de reintentar.")
                time.sleep(retry_after)
                reintento += 1
            else:
                logger.info(f"Error al consultar la API para {symbol}: {response.status_code}.")
                return pd.DataFrame()

        except requests.exceptions.RequestException as e:
            logger.info(f"Error de conexión: {e}")
            reintento += 1
            if reintento < max_reintentos:
                logger.info(f"Reintentando url:{url}  en {tiempo_espera} segundos...")
                time.sleep(tiempo_espera)
                tiempo_espera *= 2

    logger.info(f"Falló la obtención de datos en tiempo real para {symbol} después de {max_reintentos} reintentos.")
    return pd.DataFrame()

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


# Función para obtener eventos económicos de los últimos 7 días y ponderarlos por importancia
#@profile
async def obtener_eventos_economicos(max_reintentos=3, tiempo_espera_inicial=5):
    dias_habiles = await obtener_dias_habiles_mercado()

    # Obtener la fecha de ayer y de mañana
    fecha_ayer = dias_habiles[0].strftime('%Y-%m-%d')
    fecha_manana = dias_habiles[1].strftime('%Y-%m-%d')
    
    url = f'https://financialmodelingprep.com/api/v3/economic_calendar?from={fecha_ayer}&to={fecha_manana}&apikey={API_KEY}'
    #logger.info(f"MTORO esta es la url: {url}")

    reintento = 0
    tiempo_espera = tiempo_espera_inicial
    
    while reintento < max_reintentos:
        try:
            response = requests.get(url, timeout_request_global)
            
            fmp_df = pd.DataFrame()  # Inicializar vacío por si no hay eventos
            investing_df = pd.DataFrame()  # Inicializar vacío por si falla la obtención
            
            if response.status_code == 200:
                try:
                    data = response.json()
                    if isinstance(data, list) and len(data) > 0:
                        fmp_df = pd.DataFrame(data)
                        fmp_df['date'] = pd.to_datetime(fmp_df['date'])  # Convertir la fecha a datetime
                        fmp_df = fmp_df[fmp_df['impact'].isin(['High', 'Medium', 'Low'])].copy()
                        fmp_df['impact'] = fmp_df['impact'].str.capitalize()  # Homogeneizar "High", "Medium", etc.
                        fmp_df = fmp_df[['date', 'currency', 'event', 'actual', 'estimate', 'previous', 'impact']]
                        fmp_df = fmp_df.sort_values(by='date', ascending=False).copy()
                        fmp_df['date'] = fmp_df['date'].dt.tz_localize('GMT')
                    else:
                        logger.info("Advertencia: La respuesta de FMP no contiene datos válidos.")
                except ValueError as e:
                    logger.info(f"Error al parsear JSON de FMP: {e}")

            # Obtener eventos de Investing
            try:
                calendar = investpy.economic_calendar(time_zone='GMT')
                calendar = calendar[(calendar['importance'].isin(['high', 'medium', 'low']))].copy()
                calendar['date'] = pd.to_datetime(calendar['date'], format='%d/%m/%Y', errors='coerce')
                calendar['date'] = pd.to_datetime(calendar['date'].dt.strftime('%Y-%m-%d') + ' ' + calendar['time'], errors='coerce')
                calendar['date'] = calendar['date'].dt.tz_localize('GMT')
                calendar = calendar.rename(columns={'importance': 'impact', 'forecast': 'estimate'})
                calendar['impact'] = calendar['impact'].str.capitalize()
                investing_df = calendar[['date', 'currency', 'event', 'actual', 'estimate', 'previous', 'impact']].copy()
            except Exception as e:
                logger.info(f"Error inesperado al obtener datos del calendario económico: {e}")
                
            if fmp_df is not None:
                fmp_df = fmp_df.dropna(axis=1, how='all')
            if investing_df is not None:
                investing_df = investing_df.dropna(axis=1, how='all')

            # Concatenar los DataFrames después de eliminar columnas vacías
            eventos_totales_df = pd.concat([fmp_df, investing_df], ignore_index=True)
        #    eventos_totales_df = fmp_df

            eventos_totales_df['date'] = eventos_totales_df['date'].dt.tz_convert(timezone_country)
            
            # Validar si las columnas 'actual' y 'previous' existen antes de aplicar dropna
            if all(col in eventos_totales_df.columns for col in ['actual', 'previous']):
                # Eliminar filas donde actual o previous sea NaN
                eventos_totales_df = eventos_totales_df.dropna(subset=['actual', 'previous']).copy()
            else:
                logging.warning("Las columnas 'actual' y/o 'previous' no están presentes en los datos.")

            #logger.info(f"Eventos económicos unificados: {eventos_totales_df}")
            return eventos_totales_df
        
        except requests.exceptions.RequestException as e:
                logger.info(f"Error de conexión: {e}")
                reintento += 1
                if reintento < max_reintentos:
                    logger.info(f"Reintentando url:{url} en {tiempo_espera} segundos...")
                    time.sleep(tiempo_espera)
                    tiempo_espera *= 2

    # Si todos los intentos fallaron
    logging.warning("Todos los intentos de obtener eventos económicos fallaron.")
    # Devolver un DataFrame vacío con las columnas esperadas para evitar errores posteriores
    columnas_esperadas = ['date', 'currency', 'event', 'actual', 'estimate', 'previous', 'impact']
    return pd.DataFrame(columns=columnas_esperadas)


def guardar_eventos_completos(eventos):
    """Guarda todos los eventos en Firestore."""
    try:
        collection_ref = db.collection("eventos_completos")
        for evento in eventos:
            # Crear un ID único basado en el evento
            doc_id = f"{evento['currency']}_{evento['date_country']}_{evento['event']}".replace(" ", "_")
            
            # Guardar o actualizar el evento en Firestore
            collection_ref.document(doc_id).set(evento, merge=True)

        print("Eventos guardados/actualizados con éxito en Firestore.")
    except Exception as e:
        print(f"Error al guardar eventos en Firestore: {e}")



async def cargar_eventos_completos():
    """Carga todos los eventos desde Firestore."""
    try:
        collection_ref = db.collection("eventos_completos")
        docs = collection_ref.stream()
        return [doc.to_dict() for doc in docs if doc.exists]
    except Exception as e:
        print(f"Error al cargar eventos: {e}")
        return []


async def obtener_eventos_guardados_o_futuros(fecha_inicio, fecha_fin):
    """Obtiene eventos desde la API o el archivo local si la API no está disponible."""
    try:
        eventos = await obtener_eventos_economicos_futuros(fecha_inicio, fecha_fin)
        if not eventos.empty:
            guardar_eventos_completos(eventos.to_dict(orient="records"))
            return eventos
        else:
            logger.info("No se encontraron eventos en la API. Intentando cargar desde el archivo local.")
            eventos_guardados = await cargar_eventos_completos()
            if eventos_guardados:
                df_guardados = pd.DataFrame(eventos_guardados)
                df_guardados['date_country'] = pd.to_datetime(df_guardados['date_country'])
                return df_guardados[
                    (df_guardados['date_country'] >= fecha_inicio) &
                    (df_guardados['date_country'] <= fecha_fin)
                ]
    except Exception as e:
        logger.info(f"Error al obtener eventos futuros: {e}")
        eventos_guardados = await cargar_eventos_completos()
        if eventos_guardados:
            df_guardados = pd.DataFrame(eventos_guardados)
            df_guardados['date_country'] = pd.to_datetime(df_guardados['date_country'])
            return df_guardados[
                (df_guardados['date_country'] >= fecha_inicio) &
                (df_guardados['date_country'] <= fecha_fin)
            ]
    return pd.DataFrame()


# Función para obtener eventos económicos de los próximos 7 días solo para las divisas listadas y con impacto High y Medium
#@profile
async def obtener_eventos_economicos_futuros(fecha_inicio, fecha_fin, max_reintentos=3, tiempo_espera_inicial=5):
    try:
        # Asegurar que las fechas de entrada estén localizadas
        if fecha_inicio.tzinfo is None:
            fecha_inicio = timezone_country.localize(fecha_inicio)
        if fecha_fin.tzinfo is None:
            fecha_fin = timezone_country.localize(fecha_fin)

        # Ajustar la fecha de fin al final del día
        fecha_fin = fecha_fin + pd.Timedelta(days=1) - pd.Timedelta(seconds=1)

        fecha_inicio_utc = fecha_inicio.astimezone(pytz.UTC)
        fecha_fin_utc = fecha_fin.astimezone(pytz.UTC)

        logger.info(f"Filtrando eventos entre {fecha_inicio_utc} y {fecha_fin_utc} (UTC)")

        # URL de la API
        url = f'https://financialmodelingprep.com/api/v3/economic_calendar?apikey={API_KEY}'
        #logger.info(f"MTORO esta es la url: {url}")

        reintento = 0
        tiempo_espera = tiempo_espera_inicial

        while reintento < max_reintentos:
            try:
                response = requests.get(url, timeout=timeout_request_global)
                if response.status_code == 200:
                    data = response.json()
                    if isinstance(data, list) and len(data) > 0:
                        df = pd.DataFrame(data)

                        # Convertir fechas a datetime y localizarlas en UTC
                        if df['date'].dtype == 'O':
                            df['date'] = pd.to_datetime(df['date'], errors='coerce').dt.tz_localize('UTC')

                        # Obtener rango mínimo y máximo de la API
                        min_date_api = df['date'].min()
                        max_date_api = df['date'].max()
                        logger.info(f"Rango mínimo de fechas en la API: {min_date_api}")
                        logger.info(f"Rango máximo de fechas en la API: {max_date_api}")

                        # Validar si el rango solicitado tiene datos disponibles
                        if fecha_fin_utc < min_date_api or fecha_inicio_utc > max_date_api:
                            logger.info("El rango solicitado no coincide con el rango disponible en la API.")
                            logger.info(f"Fechas disponibles: {min_date_api} a {max_date_api}")
                            return pd.DataFrame()  # Devuelve un DataFrame vacío

                        # Ajustar el rango solicitado al rango disponible en la API
                        fecha_inicio_utc = max(fecha_inicio_utc, min_date_api)
                        fecha_fin_utc = min(fecha_fin_utc, max_date_api)

                        logger.info(f"Rango ajustado: {fecha_inicio_utc} a {fecha_fin_utc}")

                        # Filtrar por impacto y rango de fechas
                        df = df[df['impact'].isin(['High', 'Medium'])]
                        df = df[(df['date'] >= fecha_inicio_utc) & (df['date'] <= fecha_fin_utc)]
                        logger.info(f"Registros tras filtrar por impacto y rango de fechas: {len(df)}")

                        # Convertir fechas a la zona horaria del pais seleccionado
                        df['date_country'] = df['date'].dt.tz_convert(timezone_country)

                        # Filtrar por divisas relevantes
                        divisas_relevantes = set(
                            [symbol[:3] for symbol in activos] +
                            [symbol[-3:] for symbol in activos if len(symbol) > 3]
                        )
                        df = df[df['currency'].isin(divisas_relevantes)]
                        logger.info(f"Registros tras filtrar por divisas relevantes: {len(df)}")

                        # Calcular ponderación y ordenar
                        df['ponderacion'] = df['impact'].apply(lambda x: 1.0 if x == 'High' else 0.5)
                        df = df.sort_values(by=['currency', 'date_country'])

                        logger.info("Datos finales procesados:")
                        logger.info(df[['currency', 'ponderacion', 'date_country', 'event']].head())

                        return df[['currency', 'ponderacion', 'date_country', 'event']]
                else:
                    logger.info(f"Error al consultar la API: {response.status_code}")
            except requests.exceptions.RequestException as e:
                logger.info(f"Error de conexión: {e}")
                reintento += 1
                if reintento < max_reintentos:
                    logger.info(f"Reintentando url:{url} en {tiempo_espera} segundos...")
                    time.sleep(tiempo_espera)
                    tiempo_espera *= 2
    except Exception as e:
        logger.info(f"Error al obtener eventos económicos futuros: {e}")

    return pd.DataFrame()  # Retornar un DataFrame vacío en caso de error




# Función para generar una imagen por currency
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

    # Convertir la fecha al formato adecuado en la zona horaria del pais seleccionado
    date_country = date.astimezone(timezone_country)

    # Configurar la fecha de inicio y fin en formato ISO8601
    start_date_str = date_country.strftime('%Y%m%dT%H%M%S')
    end_date_str = start_date_str

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
    cal = Calendar()
    cal.add('prodid', '-//Mi Sistema de Trading//ES')
    cal.add('version', '2.0')

    for _, row in df.iterrows():

        # Generar el link de Google Calendar para el evento individual
        link = generar_link_google_calendar(row['event'], row['date_country'], row['currency'], row['ponderacion'])

        if link:
            fecha_country_str = row['date_country'].strftime('%Y-%m-%d %H:%M:%S')
            ponderacion_str = "Media" if row['ponderacion'] == 0.5 else "Alta" if row['ponderacion'] == 1 else str(row['ponderacion'])
            
            # Formato del mensaje con escape para Markdown V2
            evento = (
                f"Evento: {escape_markdown(row['event'], version=2)}\n"
                f"Divisa: {escape_markdown(row['currency'], version=2)}\n"
                f"Fecha: {escape_markdown(fecha_country_str, version=2)}\n"
                f"Ponderación: {escape_markdown(ponderacion_str, version=2)}\n"
                f"[Agregar a Google Calendar]({escape_markdown(link, version=2)})\n"
            )

            # Enviar evento individual por Telegram
            try:
                await context.bot.send_message(chat_id=user_chat_id, text=evento, parse_mode='MarkdownV2')
            except Exception as e:
                logger.info(f"Error al enviar texto de eventos a {user_chat_id}: {e}")

        # Agregar evento al archivo .ics
        event = Event()
        event.add('summary', row['event'])
        event.add('dtstart', row['date_country'])
        event.add('dtend', row['date_country'])  # Puedes modificar esto si los eventos tienen duración
        event.add('description', f"Recordatorio para el evento: {row['event']} ({row['currency']}). Peso: {row['ponderacion']}")
        event.add('location', "Google Calendar")

        cal.add_component(event)

    # Guardar el archivo .ics en un archivo temporal
    with tempfile.NamedTemporaryFile(delete=False, suffix=".ics") as f:
        f.write(cal.to_ical())
        file_path = f.name

    # Enviar el archivo .ics a Telegram
    try:
        await context.bot.send_document(chat_id=user_chat_id, document=open(file_path, 'rb'), filename="eventos_calendar.ics")
    except Exception as e:
        logger.info(f"Error al enviar el archivo de calendario a {user_chat_id}: {e}")
    finally:
        os.remove(file_path)  # Eliminar el archivo después de enviarlo



#@profile
def obtener_valor_realtime_unificado(symbol: str, user_chat_id: str | None = None, *, intentos: int = 1):

    logging.info(f"[RT][IN] symbol={symbol!r} user_chat_id={user_chat_id!r} intentos={intentos}")

    # --- preparar caches (global hermano + bucket de usuario) ---
    global_cache = user_states.setdefault("cache_realtime", {})
    if user_chat_id:
        user_bucket = user_states.setdefault(user_chat_id, {})
        user_cache  = user_bucket.setdefault("cache_realtime", {})
    else:
        user_cache  = global_cache

    # Logs de estado inicial
    logging.info("[RT][STATE] keys(user_states)=%s", list(user_states.keys())[:10])

    # --- preferir valor ya cacheado (usuario -> global) ---
    v = user_cache.get(symbol)
    if _is_finite_number(v):
        logging.info("[RT][HIT] user_cache[%s]=%s", symbol, v)
        return float(v)

    v = global_cache.get(symbol)
    if _is_finite_number(v):
        logging.info("[RT][HIT] global_cache[%s]=%s", symbol, v)
        if user_cache is not global_cache:
            user_cache[symbol] = v        # seed al cache del usuario
            logging.info("[RT][SEED] user_cache[%s] <- %s (desde global)", symbol, v)
        return float(v)

    # --- consultar a la fuente realtime (FMP) ---
    muestras = []
    for i in range(max(1, int(intentos))):
        try:
            df = obtener_dato_realtime_fmp(symbol)
            logging.info("[RT][FMP] intento=%d df_none=%s df_empty=%s",
                         i+1, df is None, (getattr(df, "empty", True) if df is not None else True))
            if df is not None and not df.empty:
                val = float(df.iloc[0]["close"])
                if _is_finite_number(val):
                    muestras.append(val)
                    logging.info("[RT][FMP] intento=%d close=%s (acum=%d)", i+1, val, len(muestras))
                else:
                    logging.info("[RT][FMP] intento=%d close inválido=%s", i+1, val)
        except Exception as e:
            logging.exception("[RT][ERR] fallo FMP %s intento %d", symbol, i+1)

    if not muestras:
        logging.info("[RT][MISS] Sin muestras válidas para %s -> None", symbol)
        return None

    # Si hay varias muestras, usa mediana; si no, la última.
    valor = statistics.median(muestras) if len(muestras) >= 3 else muestras[-1]
    logging.info("[RT][RESOLVE] muestras=%s -> valor=%s", muestras, valor)

    # --- guardar en ambos caches ---
    user_cache[symbol]   = valor
    global_cache[symbol] = valor
    logging.info("[RT][SAVE] user_cache[%s]=%s | global_cache[%s]=%s",
                 symbol, user_cache.get(symbol), symbol, global_cache.get(symbol))

    logging.info(f"[RT] {symbol} = {valor}")
    return valor

def _is_finite_number(x) -> bool:
    try:
        return isinstance(x, (int, float, np.floating)) and math.isfinite(float(x))
    except Exception:
        return False


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
    user_chat_id: str | None = None,
    cfg: dict | None = None
):

    try:
        # --- preparar caches: global (hermano) y usuario ---
        global_cache = user_states.setdefault("cache_realtime", {})

        if user_chat_id is not None:
            user_bucket = user_states.setdefault(user_chat_id, {})
            cache_rt = user_bucket.setdefault("cache_realtime", {})  # cache del usuario
        else:
            cache_rt = global_cache  # cache global

        logging.info(
            "[RT][CACHE] users=%d global_size=%d user_size=%d is_global=%s",
            sum(1 for k in user_states if k != "cache_realtime"),
            len(global_cache),
            len(cache_rt),
            cache_rt is global_cache
        )

        # 1) normalizar TF
        tf = _norm_tf(temporalidad)

        # 2) resolver bars desde cfg; si no hay → None
        bars = get_bars_for_tf(cfg, tf)

        # 3) histórico (acepta bars=None)
        df_historico = obtener_datos_historicos_fmp(symbol, tf, bars=bars)
        if df_historico is None or df_historico.empty:
            logger.info("Datos históricos no disponibles para %s en %s", symbol, tf)
            return pd.DataFrame()
        df_historico = df_historico.sort_index()

        # 4) tick realtime: primero user, luego global (y sembrar si aplica)
        raw_tick = _lookup_rt_tick(cache_rt, symbol)
        tick_src = "user"
        if raw_tick is None and cache_rt is not global_cache:
            raw_tick = _lookup_rt_tick(global_cache, symbol)
            if raw_tick is not None:
                cache_rt[symbol] = raw_tick  # siembra al cache del usuario
                tick_src = "global->seeded"
            else:
                tick_src = "none"

        last_close = _coerce_float(raw_tick)

        try:
            last_hist = float(df_historico["close"].iloc[-1])
        except Exception:
            last_hist = None

        logging.info(
            "[RT][TICK] %s-%s src=%s tick_cache=%r parsed=%s last_hist=%s bars=%s",
            symbol, tf, tick_src, raw_tick, last_close, last_hist, bars
        )

        if last_close is not None:
            try:
                df_mix = actualizar_ultima_vela_con_realtime(
                    df_historico, pd.DataFrame([{"close": last_close}]), symbol, tf
                )
                df_out = df_mix.sort_index()
            except Exception as e:
                logger.info("actualizar_ultima_vela_con_realtime falló para %s-%s: %s", symbol, tf, e)
                df_out = df_historico
        else:
            df_out = df_historico

        # 5) recorte final si bars es numérico
        if isinstance(bars, int) and bars > 0 and len(df_out) > bars:
            before = len(df_out)
            df_out = df_out.tail(bars)
            logging.info("[RT][TAIL] recortado de %d a %d por bars=%d", before, len(df_out), bars)

        logging.info(
            "[RT][RETURN] %s-%s len=%d last_ts=%s last_close=%s tick_aplicado=%s",
            symbol, tf, len(df_out),
            (df_out.index[-1] if not df_out.empty else None),
            (df_out["close"].iloc[-1] if not df_out.empty else None),
            last_close is not None
        )
        return df_out

    except Exception as e:
        logger.info("Se cayó en obtener_datos_con_hilos %s-%s – error: %s", symbol, temporalidad, e)
        return pd.DataFrame()


#@profile
def actualizar_ultima_vela_con_realtime(df, df_realtime, symbol, temporalidad):

    if not isinstance(df, pd.DataFrame) or df.empty:
        logger.info(f"El DataFrame histórico está vacío o no es válido. activo:{symbol} temporalidad:{temporalidad}")
        return df

    if not isinstance(df_realtime, pd.DataFrame) or df_realtime.empty:
        logger.info("El DataFrame de tiempo real está vacío o no es válido.")
        return df
    
    df = df.sort_index(ascending=True)

    # Procesar datos
    price_realtime = df_realtime.iloc[0]['close']
    ultima_vela_index = df.index[-1]

    df.loc[ultima_vela_index, 'high'] = max(df.loc[ultima_vela_index, 'high'], price_realtime)
    df.loc[ultima_vela_index, 'low'] = min(df.loc[ultima_vela_index, 'low'], price_realtime)
    df.loc[ultima_vela_index, 'close'] = price_realtime

    return df

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

    # Detectar cruce del MACD
    df['macd_cruce'] = np.where(
        (df['macd'].shift(1) < df['signal'].shift(1)) & (df['macd'] > df['signal']), 'Cruce Alcista',
        np.where((df['macd'].shift(1) > df['signal'].shift(1)) & (df['macd'] < df['signal']), 'Cruce Bajista', 'No cruce')
    )

    # Detectar si el MACD está cerca de cruzar la señal
    df['macd_cerca_de_cruzar'] = np.where(
        abs(df['macd'] - df['signal']) < (df['macd'].std() * 0.05), 'Cerca del cruce', 'No cerca'
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
    df['true_range'] = np.maximum(df['high'] - df['low'], 
                                  np.maximum(abs(df['high'] - df['close'].shift(1)), 
                                             abs(df['low'] - df['close'].shift(1))))
    df['ATR'] = df['true_range'].rolling(window=window).mean()

    # Señales de divergencia
    df['divergencia_macd'] = (df['macd'] > df['macd'].shift(1)) & (df['close'] < df['close'].shift(1))
    df['divergencia_rsi'] = (df['rsi'] > df['rsi'].shift(1)) & (df['close'] < df['close'].shift(1))

    df['divergencia_macd_bull'] = (df['macd'] > df['macd'].shift(1)) & (df['close'] < df['close'].shift(1))
    df['divergencia_macd_bear'] = (df['macd'] < df['macd'].shift(1)) & (df['close'] > df['close'].shift(1))
    df['divergencia_rsi_bull']  = (df['rsi']  > df['rsi'].shift(1))  & (df['close'] < df['close'].shift(1))
    df['divergencia_rsi_bear']  = (df['rsi']  < df['rsi'].shift(1))  & (df['close'] > df['close'].shift(1))


    # Convertir todas las columnas a tipo float
    for col in ['rsi', '%K', '%D', 'ATR', 'macd', 'signal', 'ema_12', 'ema_26']:
        df[col] = pd.to_numeric(df[col], errors='coerce')

    # Convertir automáticamente columnas que pueden ser numéricas
    cols_excluir = ['macd_cruce', 'macd_cerca_de_cruzar', 'bollinger_signal', 'bollinger_upper', 'bollinger_lower']
    df_interpolar = df.drop(columns=cols_excluir)
    df_interpolar.interpolate(method='linear', inplace=True)
    df.update(df_interpolar)

    # Rellenar valores restantes con forward fill y backward fill
    df.ffill(inplace=True)
    df.bfill(inplace=True)

    return df

# Función para asegurar que la probabilidad esté entre 1 y 100
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

def _fget(cfg: Optional[dict], path: list, default):
    cur = (cfg or {}).get("fundamental", {})
    for k in path:
        cur = cur.get(k, {}) if isinstance(cur, dict) else {}
    return (cfg or {}).get("fundamental", {}).get(path[0], default) if len(path)==1 else (cur or default)

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
    now = datetime.now(timezone_country)
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
def es_compra_arima(precio_actual, arima_prediccion, probabilidad_general, zona_no_trading):
    return precio_actual < arima_prediccion and probabilidad_general > 53 and not zona_no_trading

def es_compra_media_movil(precio_actual, media_movil_prediccion, probabilidad_general, zona_no_trading):
    return precio_actual < media_movil_prediccion and probabilidad_general > 53 and not zona_no_trading

def es_compra_arima_media_movil(precio_actual, arima_prediccion, media_movil_prediccion, probabilidad_general, zona_no_trading):
    return precio_actual < arima_prediccion and precio_actual < media_movil_prediccion and probabilidad_general > 53 and not zona_no_trading

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
def es_venta_arima(precio_actual, arima_prediccion, probabilidad_general, zona_no_trading):
    return precio_actual > arima_prediccion and probabilidad_general < 47 and not zona_no_trading

def es_venta_media_movil(precio_actual, media_movil_prediccion, probabilidad_general, zona_no_trading):
    return precio_actual > media_movil_prediccion and probabilidad_general < 47 and not zona_no_trading

def es_venta_arima_media_movil(precio_actual, arima_prediccion, media_movil_prediccion, probabilidad_general, zona_no_trading):
    return precio_actual > arima_prediccion and precio_actual > media_movil_prediccion and probabilidad_general < 47 and not zona_no_trading

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

def contar_toques(nivel, precios, umbral=0.01):
    return sum(abs(precios - nivel) / nivel <= umbral)

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

def eliminar_niveles_redundantes(niveles, tolerancia):

    niveles_filtrados = []
    for nivel in niveles:
        if not niveles_filtrados or abs(nivel - niveles_filtrados[-1]) > tolerancia:
            niveles_filtrados.append(nivel)
    return niveles_filtrados

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

    def procesar_niveles_importantes(niveles):
        # Validar si es una tupla con un único elemento que contiene una lista
        if isinstance(niveles, tuple) and len(niveles) == 1 and isinstance(niveles[0], list):
            return [float(n) for n in niveles[0]]  # Convertir cada elemento a float

        # Validar si es simplemente una lista
        if isinstance(niveles, list):
            return [float(n) for n in niveles]  # Convertir cada elemento a float

        # Si no cumple con ninguno de los formatos, lanzar un error
        raise ValueError(f"El formato de los niveles no es el esperado. Tipo recibido: {type(niveles)}, contenido: {niveles}")

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

def _finite(x) -> bool:
    try:
        return np.isfinite(float(x))
    except Exception:
        return False

def _tofloat(x):
    try:
        v = float(x)
        return v if np.isfinite(v) else None
    except Exception:
        return None
    
def calc_tp_sl_compra(entry, atr, mult=1.5):
    if not (_finite(entry) and _finite(atr)):
        return None, None
    tp = entry + atr * mult
    sl = entry - atr * mult
    if not (sl < entry < tp):
        return None, None
    return tp, sl

def calc_tp_sl_venta(entry, atr, mult=1.5):
    if not (_finite(entry) and _finite(atr)):
        return None, None
    tp = entry - atr * mult
    sl = entry + atr * mult
    if not (tp < entry < sl):
        return None, None
    return tp, sl

def calc_tp_sl_compra_asym(entry: float, atr: float, tp_mult: float, sl_mult: float):
    if not (_finite(entry) and _finite(atr)):
        return None, None
    tp = entry + atr * tp_mult
    sl = entry - atr * sl_mult
    if not (sl < entry < tp):
        return None, None
    return tp, sl

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

def _finite(x) -> bool:
    try:
        return x is not None and math.isfinite(float(x))
    except Exception:
        return False

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
    # --- parámetros tunables (ahora asimétricos por defecto) ---
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
    min_rrr=1.2
):
    """
    Crea múltiples candidatos (long/short) usando niveles, rango, Bollinger y ATR.
    Filtra por coherencia y RRR >= min_rrr. Ordena por score (menor=mejor).
    """
    entries: list[dict] = []

    logging.info("===== INPUT =====")
    logging.info(f"precio_actual={precio_actual:.6f}, ATR={ATR if ATR is not None else None}")
    logging.info(f"niveles: S1={niveles.get('soporte_nivel_1')}, S2={niveles.get('soporte_nivel_2')}, "
                f"R1={niveles.get('resistencia_nivel_1')}, R2={niveles.get('resistencia_nivel_2')}")
    logging.info(f"tipo_operacion={tipo_operacion}, estructura={(en_rango or {}).get('estructura_tendencia')}, "
                f"es_rango={bool((en_rango or {}).get('es_rango_repetitivo'))}")
    logging.info(f"rango_dinamico={(en_rango or {}).get('rango_dinamico')}")
    logging.info("=================")

    if precio_actual is None or not _finite(precio_actual):
        return entries
    if ATR is None or not _finite(ATR) or ATR <= 0:
        logging.info("ATR inválido: no se generan entradas")
        return entries

    s1 = niveles.get("soporte_nivel_1")
    s2 = niveles.get("soporte_nivel_2")
    r1 = niveles.get("resistencia_nivel_1")
    r2 = niveles.get("resistencia_nivel_2")

    estructura = (en_rango or {}).get("estructura_tendencia", "indefinida")
    es_rango = bool((en_rango or {}).get("es_rango_repetitivo"))
    rango_dinamico = (en_rango or {}).get("rango_dinamico") or (None, None)

    sesgo_long = (tipo_operacion in señales_compra) or (tipo_operacion == "Neutral" and estructura in ("alcista", "indefinida"))
    sesgo_short = (tipo_operacion in señales_venta)  or (tipo_operacion == "Neutral" and estructura == "bajista")

    logging.info(f"sesgo_long={sesgo_long}, sesgo_short={sesgo_short}, min_rrr={min_rrr}")

    midpoint = ((r1 + s1) / 2.0) if _finite(r1) and _finite(s1) else precio_actual

    # LONG
    if sesgo_long:
        if _finite(s1):
            logging.info(f"INTENTO LONG [pullback_S1] entry={s1}")
            _add_entry(entries, side="long", entry=s1, atr=ATR, mult_tp_sl=mult_pullback_s1,
                       make_tp_sl=calc_tp_sl_compra, basado_en="pullback_S1",
                       precio_actual=precio_actual, niveles=niveles, rango_dinamico=rango_dinamico,
                       min_rrr=min_rrr)
        if _finite(s2):
            logging.info(f"INTENTO LONG [pullback_S2] entry={s2}")
            _add_entry(entries, side="long", entry=s2, atr=ATR, mult_tp_sl=mult_pullback_s2,
                       make_tp_sl=calc_tp_sl_compra, basado_en="pullback_S2",
                       precio_actual=precio_actual, niveles=niveles, rango_dinamico=rango_dinamico,
                       min_rrr=min_rrr)
        if _finite(r1):
            br = r1 + breakout_offset_atr * ATR
            logging.info(f"INTENTO LONG [breakout_R1] entry={br}")
            _add_entry(entries, side="long", entry=br, atr=ATR, mult_tp_sl=mult_breakout,
                       make_tp_sl=calc_tp_sl_compra, basado_en="breakout_R1",
                       precio_actual=precio_actual, niveles=niveles, rango_dinamico=rango_dinamico,
                       min_rrr=min_rrr)
        if _finite(midpoint):
            logging.info(f"INTENTO LONG [midpoint] entry={midpoint}")
            _add_entry(entries, side="long", entry=midpoint, atr=ATR, mult_tp_sl=mult_mid,
                       make_tp_sl=calc_tp_sl_compra, basado_en="midpoint",
                       precio_actual=precio_actual, niveles=niveles, rango_dinamico=rango_dinamico,
                       min_rrr=min_rrr)
            e1 = midpoint - scale_offset_atr * ATR
            e2 = midpoint + scale_offset_atr * ATR
            logging.info(f"INTENTO LONG [scale_in_midpoint_-0.5ATR] entry={e1}")
            _add_entry(entries, side="long", entry=e1, atr=ATR, mult_tp_sl=mult_scale_lo,
                       make_tp_sl=calc_tp_sl_compra, basado_en="scale_in_midpoint_-0.5ATR",
                       precio_actual=precio_actual, niveles=niveles, rango_dinamico=rango_dinamico,
                       min_rrr=min_rrr)
            logging.info(f"INTENTO LONG [scale_in_midpoint_+0.5ATR] entry={e2}")
            _add_entry(entries, side="long", entry=e2, atr=ATR, mult_tp_sl=mult_scale_hi,
                       make_tp_sl=calc_tp_sl_compra, basado_en="scale_in_midpoint_+0.5ATR",
                       precio_actual=precio_actual, niveles=niveles, rango_dinamico=rango_dinamico,
                       min_rrr=min_rrr)
        if es_rango and _finite(bollinger_lower):
            e = bollinger_lower + boll_offset_atr * ATR
            logging.info(f"INTENTO LONG [bollinger_lower_reversion] entry={e}")
            _add_entry(entries, side="long", entry=e, atr=ATR, mult_tp_sl=mult_mid,
                       make_tp_sl=calc_tp_sl_compra, basado_en="bollinger_lower_reversion",
                       precio_actual=precio_actual, niveles=niveles, rango_dinamico=rango_dinamico,
                       min_rrr=min_rrr)

    # SHORT
    if sesgo_short:
        if _finite(r1):
            logging.info(f"INTENTO SHORT [pullback_R1] entry={r1}")
            _add_entry(entries, side="short", entry=r1, atr=ATR, mult_tp_sl=mult_pullback_r1,
                       make_tp_sl=calc_tp_sl_venta, basado_en="pullback_R1",
                       precio_actual=precio_actual, niveles=niveles, rango_dinamico=rango_dinamico,
                       min_rrr=min_rrr)
        if _finite(r2):
            logging.info(f"INTENTO SHORT [pullback_R2] entry={r2}")
            _add_entry(entries, side="short", entry=r2, atr=ATR, mult_tp_sl=mult_pullback_r2,
                       make_tp_sl=calc_tp_sl_venta, basado_en="pullback_R2",
                       precio_actual=precio_actual, niveles=niveles, rango_dinamico=rango_dinamico,
                       min_rrr=min_rrr)
        if _finite(s1):
            brk = s1 - breakout_offset_atr * ATR
            logging.info(f"INTENTO SHORT [breakdown_S1] entry={brk}")
            _add_entry(entries, side="short", entry=brk, atr=ATR, mult_tp_sl=mult_breakout,
                       make_tp_sl=calc_tp_sl_venta, basado_en="breakdown_S1",
                       precio_actual=precio_actual, niveles=niveles, rango_dinamico=rango_dinamico,
                       min_rrr=min_rrr)
        if _finite(midpoint):
            logging.info(f"INTENTO SHORT [midpoint] entry={midpoint}")
            _add_entry(entries, side="short", entry=midpoint, atr=ATR, mult_tp_sl=mult_mid,
                       make_tp_sl=calc_tp_sl_venta, basado_en="midpoint",
                       precio_actual=precio_actual, niveles=niveles, rango_dinamico=rango_dinamico,
                       min_rrr=min_rrr)
            e1 = midpoint + scale_offset_atr * ATR
            e2 = midpoint - scale_offset_atr * ATR
            logging.info(f"INTENTO SHORT [scale_in_midpoint_+0.5ATR] entry={e1}")
            _add_entry(entries, side="short", entry=e1, atr=ATR, mult_tp_sl=mult_scale_lo,
                       make_tp_sl=calc_tp_sl_venta, basado_en="scale_in_midpoint_+0.5ATR",
                       precio_actual=precio_actual, niveles=niveles, rango_dinamico=rango_dinamico,
                       min_rrr=min_rrr)
            logging.info(f"INTENTO SHORT [scale_in_midpoint_-0.5ATR] entry={e2}")
            _add_entry(entries, side="short", entry=e2, atr=ATR, mult_tp_sl=mult_scale_hi,
                       make_tp_sl=calc_tp_sl_venta, basado_en="scale_in_midpoint_-0.5ATR",
                       precio_actual=precio_actual, niveles=niveles, rango_dinamico=rango_dinamico,
                       min_rrr=min_rrr)
        if es_rango and _finite(bollinger_upper):
            e = bollinger_upper - boll_offset_atr * ATR
            logging.info(f"INTENTO SHORT [bollinger_upper_reversion] entry={e}")
            _add_entry(entries, side="short", entry=e, atr=ATR, mult_tp_sl=mult_mid,
                       make_tp_sl=calc_tp_sl_venta, basado_en="bollinger_upper_reversion",
                       precio_actual=precio_actual, niveles=niveles, rango_dinamico=rango_dinamico,
                       min_rrr=min_rrr)

    
    logging.info("===== RESUMEN =====")
    logging.info(f"Intentos totales: {len(entries)} (antes de ordenar)")
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
async def enviar_csv_telegram(df, context, filename="resultados.csv",  user_chat_id=None, intentos=3):
    """Función para enviar un archivo CSV a todos los clientes."""

    chat_ids = [user_chat_id] if user_chat_id else clientes_chat_ids

    # Verificar si el DataFrame está vacío antes de generar el archivo
    if df.empty:
        for chat_id in chat_ids:
            for intento in range(intentos):
                try:
                    await context.bot.send_message(chat_id=chat_id, text="No se pudo generar el CSV. El DataFrame está vacío.")
                    break
                except TimedOut:
                    logger.info(f"Intento {intento + 1} fallido. Reintentando...")
                    await asyncio.sleep(2)  # Espera antes de reintentar
        return
    
    # Crear un buffer en memoria para guardar el archivo CSV
    buffer = BytesIO()
    
    # Guardar el DataFrame como CSV en el buffer en memoria
    df.to_csv(buffer, sep=';', index=False, float_format='%.6f')
    buffer.seek(0)  # Volver al inicio del buffer para poder leerlo
    
    # Verificar si el buffer no está vacío
    if buffer.getbuffer().nbytes == 0:
        for chat_id in chat_ids:
            await context.bot.send_message(chat_id=chat_id, text="No se pudo generar el CSV. El archivo está vacío.")
        return
    
    # Enviar el CSV a todos los clientes
    for chat_id in chat_ids:
        try:
            await context.bot.send_document(chat_id=chat_id, document=buffer, filename=f'{filename}')
            buffer.seek(0)  # Restablecer el buffer para el siguiente cliente
        except Exception as e:
            logger.info(f"Error al enviar CSV a {chat_id}: {e}")


def df_a_imagen(df: pd.DataFrame, max_filas=30):
    """
    Genera imágenes a partir del DataFrame de oportunidades.
    Devuelve BytesIO o lista de BytesIO.
    """
    if df is None or (isinstance(df, pd.DataFrame) and df.empty):
        logger.info("El DataFrame está vacío, no se puede generar la imagen.")
        return None

    # Si por error te pasan ya un BytesIO, respétalo
    if isinstance(df, BytesIO):
        return df

    df_prep = preparar_df_oportunidades_para_tabla(df)
    if df_prep.empty:
        return None

    # fuente un poco mayor para legibilidad
    return _render_table_image(df_prep, max_rows=max_filas, font=13, dpi=200)


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
    try:
        divisas = (
            pd.Series(list(divisas_oportunidades) if divisas_oportunidades is not None else [])
            .dropna()
            .astype(str)
            .unique()
            .tolist()
        )
    except Exception:
        divisas = []

    # Corta temprano si no hay nada que mostrar
    if df_eventos is None or getattr(df_eventos, "empty", True) or len(divisas) == 0:
        return None  # o [] según tu contrato de retorno

    df = df_eventos.copy()

    if divisas_oportunidades:
        divs = set([str(x).upper() for x in divisas_oportunidades if x])
        if "currency" in df.columns:
            df = df[df["currency"].astype(str).str.upper().isin(divs)]
    if df.empty:
        return None

    # ordenar por tiempo si existe
    for cand in ["t", "time", "timestamp", "fecha", "date", "datetime"]:
        if cand in df.columns:
            try:
                df[cand] = pd.to_datetime(df[cand], errors="coerce", utc=True)
                try:
                    import pytz
                    tz = pytz.timezone(tz_name)
                    df[cand] = df[cand].dt.tz_convert(tz)
                except Exception:
                    df[cand] = df[cand].dt.tz_localize(None)
                df = df.sort_values(cand)
                df["Fecha/Hora"] = df[cand].dt.strftime("%Y-%m-%d %H:%M")
            except Exception:
                pass
            break

    # renombrar
    rename_map = {
        "title": "Evento",
        "event": "Evento",
        "currency": "Moneda",
        "impact": "Impacto",
        "actual": "Actual",
        "forecast": "Estimado",
        "previous": "Anterior",
    }
    for k, v in rename_map.items():
        if k in df.columns and v not in df.columns:
            df[v] = df[k]

    cols_pref = ["Fecha/Hora", "Moneda", "Impacto", "Evento", "Actual", "Estimado", "Anterior"]
    cols = [c for c in cols_pref if c in df.columns]
    if not cols:
        cols = list(df.columns)[:7]

    df = df[cols].fillna("—")

    for c in ["Actual", "Estimado", "Anterior"]:
        if c in df.columns:
            df[c] = df[c].apply(_fmt_num)

    imgs = tabla_a_imagenes(
        df,
        max_filas_por_imagen=max_filas_por_imagen,
        dpi=dpi,
        font_size=font_size,
        wrap_map={"Evento": 15}
    )
    return imgs if imgs else None


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
    exec_id: str | None = None,
    moneda_filtro: str | None = None,
    subir_a_bucket_y_obtener_url=None,
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

    # 2) Subir a bucket si corresponde
    if imgs and exec_id and moneda_filtro and subir_a_bucket_y_obtener_url:
        try:
            # Reutiliza tu registrador existente si acepta lista:
            urls_subidas = await subir_imagenes_eventos_y_registrar(
                imgs, exec_id, user_chat_id, moneda_filtro, subir_a_bucket_y_obtener_url
            )
        except TypeError:
            # En caso de que tu registrador espere una sola imagen, subimos una a una
            urls_subidas = []
            for i, img in enumerate(imgs, 1):
                img.seek(0)
                nombre = f"{exec_id}/eventos_oportunidades_{moneda_filtro}_part_{i}.png"
                url = await subir_a_bucket_y_obtener_url(img, nombre=nombre, content_type="image/png")
                if url:
                    urls_subidas.append(url)

    # 3) Enviar a Telegram
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

    return urls_subidas

#@profile
def graficar_serie_temporal(df, symbol, temporalidad):
    """
    Genera una gráfica de serie temporal con velas y MACD, y la retorna como un archivo en memoria (BytesIO).
    """
    with matplotlib_lock:  # Bloqueo para evitar conflictos entre hilos
        df = df.tail(120)

        if df.empty:
            logger.info(f"No hay datos para {symbol} en temporalidad {temporalidad}.")
            return None

        fig, (ax_candles, ax_macd) = plt.subplots(nrows=2, ncols=1, figsize=(10, 6), sharex=True)

        try:
            # Configurar la gráfica de velas
            ax_candles.set_title(f'{symbol} - {temporalidad}')
            ax_candles.plot(df.index, df['close'], label='Close', alpha=0.75)
            ax_candles.fill_between(df.index, df['low'], df['high'], color='gray', alpha=0.2, label='High-Low')
            ax_candles.plot(df.index, df['ema_12'], label='EMA 12', color='blue', linestyle='--')
            ax_candles.plot(df.index, df['ema_26'], label='EMA 26', color='red', linestyle='--')
            ax_candles.legend(loc='upper left')
            ax_candles.grid(True)

            # Gráfica del MACD
            ax_macd.plot(df.index, df['macd'], label='MACD', color='red')
            ax_macd.plot(df.index, df['signal'], label='Signal Line', color='yellow')
            ax_macd.bar(df.index, df['macd'] - df['signal'], label='Histogram', alpha=0.5, color='green', width=0.01)
            ax_macd.axhline(0, color='black', linewidth=0.5)
            ax_macd.legend(loc='upper left')
            ax_macd.grid(True)

            # Ajustar el layout
            fig.tight_layout()

            # Guardar la imagen en un archivo en memoria
            buf = BytesIO()
            fig.savefig(buf, format='png', bbox_inches='tight')
            buf.seek(0)  # Posicionar el puntero al inicio del buffer
            return buf

        except Exception as e:
            logger.info(f"Error al generar la imagen: {e}")
            return None

        finally:
            plt.close(fig)  # Cierra la figura para liberar recursos

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

    peso_base = max(1, int(inc.get("peso_base", 1)))
    tfs = inc.get("_tfs_list", [])
    if not tfs:
        tfs = [t.strip() for t in DEFAULT_PONDER_INC_CFG["temporalidades"].split(",")]

    # mapa temporalidad -> índice
    idx = {tf.lower(): i for i, tf in enumerate(tfs)}

    def _tf_index(tf_val: str) -> int | None:
        if not tf_val:
            return None
        t = str(tf_val).strip().lower()
        # intenta normalizar equivalencias comunes
        aliases = {
            "1min": "1m", "5min": "5m", "15min": "15m", "30min": "30m",
            "1hour": "1h", "4hour": "4h", "1day": "1d", "1week": "1w"
        }
        t = aliases.get(t, t)
        return idx.get(t)

    # agrupar por activo
    for activo, data in df.groupby("Activo"):
        ponder = 0
        for _, row in data.iterrows():
            tipo = row.get("Tipo de Operacion")
            tf_v = row.get("Temporalidad")
            i = _tf_index(tf_v)
            if i is None:
                continue
            try:
                if tipo in señales_compra:
                    ponder += peso_base * (2 ** i)
                elif tipo in señales_venta:
                    ponder -= peso_base * (2 ** i)
            except NameError:
                # si las listas de señales no están en el scope, no aplica
                pass

        df.loc[df["Activo"] == activo, "Ponderacion Incremental"] = ponder

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

def _norm_ponder_cfg(cfg: dict | None) -> dict:
    try:
        user = ((cfg or {}).get("ponderacion") or {})
        return {**DEFAULT_PONDER_CFG, **user}
    except Exception:
        return DEFAULT_PONDER_CFG.copy()

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
        **({"Niveles Confirmados (Nivel)": entradas.get('niveles_confirmados_orden_nivel_all')} if es_administrador(user_chat_id) else {}),
        **({"Niveles Confirmados (Nivel)": entradas.get('niveles_confirmados_orden_nivel_reduced')} if not es_administrador(user_chat_id) else {}),
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

    # --- Realtime (opcional) ---
    realtime_tasks = [
        loop.run_in_executor(None, obtener_valor_realtime_unificado, symbol, user_chat_id)
        for symbol in activos_filtrados
    ]
    realtime_results = await asyncio.gather(*realtime_tasks, return_exceptions=True)
    for idx, result in enumerate(realtime_results):
        if isinstance(result, Exception):
            logger.info(f"Error en realtime para símbolo {activos_filtrados[idx]}: {result}")
        elif result is None:
            logger.info(f"Resultado de realtime vacío para símbolo {activos_filtrados[idx]}.")

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


# Función para procesar el resultado de cada análisis
#@profile
async def procesar_resultado(resultados, df_eventos, context, update, moneda_filtro, user_chat_id=None, opciones_usuario=[], origen="telegram", exec_id: str | None = None, cfg: dict | None = None):

    urls_generadas = []

    # >>> registros sin DataFrames ni claves privadas
    registros_limpios = _sanitize_records_for_json(
        [r for r in resultados if isinstance(r, dict)]
    )

    # --- JSON completo (antes de filtrar) ---
    df_resultados = pd.DataFrame(registros_limpios)

    # Aplicar la función de ponderación incremental al DataFrame `df_filtrado` en memoria
    df_resultados = df_resultados.copy()
    df_resultados = calcular_ponderacion_incremental_por_divisa(df_resultados, cfg)
    
    # Aplicar la función de ponderación al DataFrame `df_filtrado` en memoria
    df_resultados = df_resultados.copy()
    df_resultados['Ponderacion'] = df_resultados.apply(lambda row: calcular_ponderacion(row, cfg), axis=1).astype(float)

    df_resultados.pop('bollinger_lower')
    df_resultados.pop('bollinger_upper')
    # Ordenar el DataFrame por la columna de ponderación
    df_resultados = df_resultados.copy()
    
    # Verificar si el usuario tiene acceso a "análisis avanzado"
    if "analisis avanzado" in opciones_usuario and not es_administrador(user_chat_id):
        logger.info("El usuario tiene acceso a análisis avanzado.")
    elif "analisis premium" in opciones_usuario and not es_administrador(user_chat_id):
        df_resultados.pop("Soportes Importantes Alcanzados")
        df_resultados.pop("Resistencias Importantes Alcanzadas")
        df_resultados.pop("Niveles Confirmados (Nivel)")
        logger.info("El usuario tiene acceso a análisis premium.")
    elif "analisis basico" in opciones_usuario and not es_administrador(user_chat_id):
        df_resultados.pop("Patrones Detectados")
        df_resultados.pop("Soportes Alcanzados")
        df_resultados.pop("Resistencias Alcanzadas")
        df_resultados.pop("Cerca de Soporte Resistencia")
        df_resultados.pop("Es Rango Repetitivo")
        df_resultados.pop("Estructura Tendencia")
        df_resultados.pop("Rebotes")
        df_resultados.pop("Rango Dinamico")
        df_resultados.pop("Soportes Importantes Alcanzados")
        df_resultados.pop("Resistencias Importantes Alcanzadas")
        df_resultados.pop("Niveles Confirmados (Nivel)")
        df_resultados.pop("Probabilidad Alza (Montecarlo)")
        df_resultados.pop("Probabilidad Baja (Montecarlo)")
        logger.info("El usuario tiene acceso a análisis basico.")  

    df_resultados_ordenado = df_resultados.sort_values(by='Ponderacion', ascending=False)  

    # 7) (Solo app) Subir enriquecidos por símbolo/TF — se mantiene
    if origen == "app" and exec_id:
        urls_enriched = []
        for res in resultados:
            if not isinstance(res, dict):
                continue

            sym = res.get("Activo")
            tf = res.get("Temporalidad")
            df_velas = res.get("_ohlcv_df")
            df_inds = res.get("_indicadores_df")
            niveles = res.get("_niveles") or {}
            entradas = res.get("_entradas") or {}

            tiene_datos = (
                isinstance(df_velas, pd.DataFrame) and not df_velas.empty
            ) or (isinstance(df_inds, pd.DataFrame) and not df_inds.empty)

            if sym and tf and tiene_datos:
                try:
                    #if isinstance(df_resultados, dict):
                    #    entradas = dict(df_resultados)  # copy
                    #    if df_resultados['Ponderacion'] is not None:
                    #        entradas["ponderacion"] = df_resultados['Ponderacion']
                    #    if df_resultados['Ponderacion Incremental'] is not None:
                    #        entradas["ponderacion_incremental"] = int(round(float(df_resultados["Ponderacion Incremental"])))
                    #elif isinstance(df_resultados, list):
                    #    # Si 'entradas' es una lista, la envolvemos para poder agregar campos
                    #    entradas = {"entradas": df_resultados}
                    #    if df_resultados['Ponderacion'] is not None:
                    #        entradas["ponderacion"] = df_resultados['Ponderacion']
                    #    if df_resultados['Ponderacion Incremental'] is not None:
                    #        entradas["ponderacion_incremental"] = int(round(float(df_resultados["Ponderacion Incremental"])))
                    #else:
                    #    entradas = {}
                    #    if df_resultados['Ponderacion'] is not None:
                    #        entradas["ponderacion"] = df_resultados['Ponderacion']
                    #    if df_resultados['Ponderacion Incremental'] is not None:
                    #        entradas["ponderacion_incremental"] = int(round(float(df_resultados["Ponderacion Incremental"])))

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
                    )
                    if url:
                        urls_enriched.append(url)
                except Exception as e:
                    logger.info(f"No se pudo subir JSON enriquecido de {sym}-{tf}: {e}")
        if urls_enriched:
            urls_generadas.extend(urls_enriched)

    # 8) (Solo app) Subir **ordenado** saneado (reemplaza al “resultados_completos”)
    if origen == "app" and exec_id:
        # Limpieza escalar (NaN/±Inf -> None) y anidados
        df_ord = (
            df_resultados_ordenado
            .replace([np.inf, -np.inf], np.nan)
            .where(pd.notnull(df_resultados_ordenado), None)
            .copy()
        )
        # Sanea celdas anidadas si las hubiera
        for col in df_ord.columns:
            if df_ord[col].apply(lambda v: isinstance(v, (dict, list, tuple, set, pd.Series))).any():
                df_ord[col] = df_ord[col].apply(sanitize_for_json)

        # Convierte a records y (ligeramente redundante) sanea por si acaso
        ordered_records = sanitize_for_json(df_ord.to_dict("records"))

        url_ordenado = await guardar_json_en_storage_y_registrar(
            exec_id=exec_id,
            chat_id=user_chat_id,
            nombre_base=f"{moneda_filtro.upper()}_resultados_ordenados",
            data=ordered_records,  # DataFrame ya serializable
            subir_a_bucket_y_obtener_url=subir_a_bucket_y_obtener_url,
            metadata={"moneda_filtro": moneda_filtro, "scope": "ordenado"},
        )
        if url_ordenado:
            urls_generadas.append(url_ordenado)        
    
    # Filtrar solo las oportunidades donde flag_oportunidad es True, Zona No Trading es False y el tipo de operación no es "Neutral"
    df_filtrado = df_resultados_ordenado[
        (df_resultados_ordenado['Oportunidad'] == True) &
        (df_resultados_ordenado['Zona No Trading'] == False) 
    ]

    # --- JSON oportunidades ---
    if origen == "app" and exec_id:
        opp_records = df_filtrado.where(pd.notnull(df_filtrado), None).to_dict("records")
        url_opp = await guardar_json_en_storage_y_registrar(
            exec_id=exec_id,
            chat_id=user_chat_id,
            nombre_base=f"{moneda_filtro.upper()}_oportunidades",
            data=opp_records,  # <- IMPORTANTE
            subir_a_bucket_y_obtener_url=subir_a_bucket_y_obtener_url,
            metadata={"moneda_filtro": moneda_filtro, "scope": "oportunidades"},
        )
        if url_opp: urls_generadas.append(url_opp)

    df_resultadosToImage = pd.DataFrame(df_filtrado)

    # Extraer divisas de los símbolos de las oportunidades
    if 'Activo' in df_filtrado.columns and not df_filtrado.empty:
        divisas_oportunidades = (
            df_filtrado['Activo']
            .astype(str)              # por si viene algún tipo no-string
            .str.slice(0, 3)
            .dropna()
            .unique()
            .tolist()                 # <- clave: pásalo a list para que el truth value no sea ambiguo
        )
    else:
        divisas_oportunidades = []

    df_filtradoToImage = df_resultadosToImage[
        (df_resultadosToImage['Oportunidad'] == True) &
        (df_resultadosToImage['Zona No Trading'] == False) &
        ((df_resultadosToImage['Tipo de Operacion'] == "Compra") | 
        (df_resultadosToImage['Tipo de Operacion'] == "Compra Fuerte") |
        (df_resultadosToImage['Tipo de Operacion'] == "Compra Predicha con ARIMA y Media Movil") |
        (df_resultadosToImage['Tipo de Operacion'] == "Compra Predicha con Media Movil") |
        (df_resultadosToImage['Tipo de Operacion'] == "Compra Predicha con ARIMA"))
    ]

    if es_administrador(user_chat_id): 
        df_filtradoToImage.pop("Niveles Confirmados (Toques)")
        df_filtradoToImage.pop("Niveles Confirmados (Nivel)")
        df_filtradoToImage.pop("Soportes Importantes Alcanzados")
        df_filtradoToImage.pop("Resistencias Importantes Alcanzadas")
        df_filtradoToImage.pop("Patrones Detectados")
        df_filtradoToImage.pop("Soportes Alcanzados")
        df_filtradoToImage.pop("Resistencias Alcanzadas")
        df_filtradoToImage.pop("Cerca de Soporte Resistencia")
        df_filtradoToImage.pop("Es Rango Repetitivo")
        df_filtradoToImage.pop("Estructura Tendencia")
        df_filtradoToImage.pop("Rebotes")
        df_filtradoToImage.pop("Rango Dinamico")
        df_filtradoToImage.pop("Probabilidad Alza (Montecarlo)")
        df_filtradoToImage.pop("Probabilidad Baja (Montecarlo)")
        df_filtradoToImage.pop('Oportunidad')  # Eliminar 'Oportunidad'
        df_filtradoToImage.pop('Zona No Trading')  # Eliminar 'Zona No Trading'
        df_filtradoToImage.pop('Cruce MACD')
        df_filtradoToImage.pop('MACD Cerca')
        df_filtradoToImage.pop('Bollinger Signal')
        df_filtradoToImage.pop('MACD Tendencia Predicha')
        df_filtradoToImage.pop('Probabilidad Tecnica (%)')
        df_filtradoToImage.pop('Probabilidad Fundamental (%)')
        df_filtradoToImage.pop('Zona Sobreventa RSI-Stochastic')
        df_filtradoToImage.pop('Zona Sobrecompra RSI-Stochastic')
        df_filtradoToImage.pop('Ponderacion')
        df_filtradoToImage.pop('Ponderacion Incremental')
        df_filtradoToImage.pop('Soporte Nivel 2')
        df_filtradoToImage.pop('Soporte Nivel 1')
        df_filtradoToImage.pop('Resistencia Nivel 1')
        df_filtradoToImage.pop('Resistencia Nivel 2')
        df_filtradoToImage.pop('Precio de Entrada')
        df_filtradoToImage.pop('Take Profit')
        df_filtradoToImage.pop('Stop Loss')
    elif "analisis avanzado" in opciones_usuario:
        df_filtradoToImage.pop("Soportes Importantes Alcanzados")
        df_filtradoToImage.pop("Resistencias Importantes Alcanzadas")
        df_filtradoToImage.pop("Niveles Confirmados (Nivel)")
        df_filtradoToImage.pop("Patrones Detectados")
        df_filtradoToImage.pop("Soportes Alcanzados")
        df_filtradoToImage.pop("Resistencias Alcanzadas")
        df_filtradoToImage.pop("Cerca de Soporte Resistencia")
        df_filtradoToImage.pop("Es Rango Repetitivo")
        df_filtradoToImage.pop("Estructura Tendencia")
        df_filtradoToImage.pop("Rebotes")
        df_filtradoToImage.pop("Rango Dinamico")
        df_filtradoToImage.pop("Probabilidad Alza (Montecarlo)")
        df_filtradoToImage.pop("Probabilidad Baja (Montecarlo)")
        df_filtradoToImage.pop('Oportunidad')  # Eliminar 'Oportunidad'
        df_filtradoToImage.pop('Zona No Trading')  # Eliminar 'Zona No Trading'
        df_filtradoToImage.pop('Cruce MACD')
        df_filtradoToImage.pop('MACD Cerca')
        df_filtradoToImage.pop('Bollinger Signal')
        df_filtradoToImage.pop('MACD Tendencia Predicha')
        df_filtradoToImage.pop('Probabilidad Tecnica (%)')
        df_filtradoToImage.pop('Probabilidad Fundamental (%)')
        df_filtradoToImage.pop('Zona Sobreventa RSI-Stochastic')
        df_filtradoToImage.pop('Zona Sobrecompra RSI-Stochastic')
        df_filtradoToImage.pop('Ponderacion')
        df_filtradoToImage.pop('Ponderacion Incremental')
        df_filtradoToImage.pop('Soporte Nivel 2')
        df_filtradoToImage.pop('Soporte Nivel 1')
        df_filtradoToImage.pop('Resistencia Nivel 1')
        df_filtradoToImage.pop('Resistencia Nivel 2')
        df_filtradoToImage.pop('Precio de Entrada')
        df_filtradoToImage.pop('Take Profit')
        df_filtradoToImage.pop('Stop Loss')
        logger.info("El usuario tiene acceso a análisis avanzado.")
    elif "analisis premium" in opciones_usuario:
        df_filtradoToImage.pop("Patrones Detectados")
        df_filtradoToImage.pop("Soportes Alcanzados")
        df_filtradoToImage.pop("Resistencias Alcanzadas")
        df_filtradoToImage.pop("Cerca de Soporte Resistencia")
        df_filtradoToImage.pop("Es Rango Repetitivo")
        df_filtradoToImage.pop("Estructura Tendencia")
        df_filtradoToImage.pop("Rebotes")
        df_filtradoToImage.pop("Rango Dinamico")
        df_filtradoToImage.pop("Probabilidad Alza (Montecarlo)")
        df_filtradoToImage.pop("Probabilidad Baja (Montecarlo)")
        df_filtradoToImage.pop('Oportunidad')  # Eliminar 'Oportunidad'
        df_filtradoToImage.pop('Zona No Trading')  # Eliminar 'Zona No Trading'
        df_filtradoToImage.pop('Cruce MACD')
        df_filtradoToImage.pop('MACD Cerca')
        df_filtradoToImage.pop('Bollinger Signal')
        df_filtradoToImage.pop('MACD Tendencia Predicha')
        df_filtradoToImage.pop('Probabilidad Tecnica (%)')
        df_filtradoToImage.pop('Probabilidad Fundamental (%)')
        df_filtradoToImage.pop('Zona Sobreventa RSI-Stochastic')
        df_filtradoToImage.pop('Zona Sobrecompra RSI-Stochastic')
        df_filtradoToImage.pop('Ponderacion')
        df_filtradoToImage.pop('Ponderacion Incremental')
        df_filtradoToImage.pop('Soporte Nivel 2')
        df_filtradoToImage.pop('Soporte Nivel 1')
        df_filtradoToImage.pop('Resistencia Nivel 1')
        df_filtradoToImage.pop('Resistencia Nivel 2')
        df_filtradoToImage.pop('Precio de Entrada')
        df_filtradoToImage.pop('Take Profit')
        df_filtradoToImage.pop('Stop Loss')
        logger.info("El usuario tiene acceso a análisis premium.")
    elif "analisis basico" in opciones_usuario:
        df_filtradoToImage.pop('Oportunidad')  # Eliminar 'Oportunidad'
        df_filtradoToImage.pop('Zona No Trading')  # Eliminar 'Zona No Trading'
        df_filtradoToImage.pop('Cruce MACD')
        df_filtradoToImage.pop('MACD Cerca')
        df_filtradoToImage.pop('Bollinger Signal')
        df_filtradoToImage.pop('MACD Tendencia Predicha')
        df_filtradoToImage.pop('Probabilidad Tecnica (%)')
        df_filtradoToImage.pop('Probabilidad Fundamental (%)')
        df_filtradoToImage.pop('Zona Sobreventa RSI-Stochastic')
        df_filtradoToImage.pop('Zona Sobrecompra RSI-Stochastic')
        df_filtradoToImage.pop('Ponderacion')
        df_filtradoToImage.pop('Ponderacion Incremental')
        df_filtradoToImage.pop('Soporte Nivel 2')
        df_filtradoToImage.pop('Soporte Nivel 1')
        df_filtradoToImage.pop('Resistencia Nivel 1')
        df_filtradoToImage.pop('Resistencia Nivel 2')
        df_filtradoToImage.pop('Precio de Entrada')
        df_filtradoToImage.pop('Take Profit')
        df_filtradoToImage.pop('Stop Loss')
        logger.info("El usuario tiene acceso a análisis basico.")    

    if "analisis premium" in opciones_usuario or "analisis avanzado" in opciones_usuario or es_administrador(user_chat_id):
        # Dividir los datos según si la divisa consultada es principal o secundaria
        if moneda_filtro.upper() in categorias["Principales"]:
            # Filtrar pares donde la moneda es principal
            df_principal = df_resultados_ordenado[df_resultados_ordenado['Activo'].str.startswith(moneda_filtro.upper())]
            # Filtrar pares donde la moneda es secundaria
            df_secundaria = df_resultados_ordenado[df_resultados_ordenado['Activo'].str.endswith(moneda_filtro.upper())]

            # Guardar los archivos separados para principales y secundarios
            nombre_archivo_principal = generar_nombre_archivo(moneda_filtro, tipo="principal")
            #df_principal.to_csv(nombre_archivo_principal, sep=';', index=False, float_format='%.5f')

            nombre_archivo_secundaria = generar_nombre_archivo(moneda_filtro, tipo="secundaria")
            #df_secundaria.to_csv(nombre_archivo_secundaria, sep=';', index=False, float_format='%.5f')

            logger.info(f"Archivo principal generado: {nombre_archivo_principal}" )
            logger.info(f"Archivo secundaria generado: {nombre_archivo_secundaria}")

            # Enviar los archivos por Telegram

            # Verificar si los DataFrame no están vacíos antes de enviarlos
            if not df_principal.empty:
                if origen == "app":
                    ruta_local = os.path.join("/tmp", nombre_archivo_principal)
                    df_principal.to_csv(ruta_local, sep=';', index=False, float_format='%.5f')

                    if exec_id:
                        object_path = build_object_path(exec_id, nombre_archivo_principal)
                    else:
                        object_path = nombre_archivo_principal  # fallback sin exec_id si fuera necesario

                    url_publica = await subir_a_bucket_y_obtener_url(ruta_local, object_path)
                    urls_generadas.append(url_publica)

                    if exec_id:
                        await asyncio.to_thread(
                            fs_registrar_archivo_generado,
                            exec_id, user_chat_id,
                            tipo="csv",
                            nombre=nombre_archivo_principal,
                            gcs_path=object_path,
                            signed_url=url_publica,     # o None si no quieres guardarla
                            content_type="text/csv",
                            metadata={"moneda_filtro": moneda_filtro, "particion": "principal", "filtrado": False},
                        )

                if origen == "app":
                    await enviar_csv_telegram(df_principal, context, nombre_archivo_principal, user_chat_id)
                else:
                    asyncio.create_task(enviar_csv_telegram(df_principal, context, nombre_archivo_principal, user_chat_id))
            else:
                logger.info(f"El DataFrame principal está vacío. No se enviará el archivo: {nombre_archivo_principal}")

            if not df_secundaria.empty:
                if origen == "app":
                    ruta_local = os.path.join("/tmp", nombre_archivo_secundaria)
                    df_secundaria.to_csv(ruta_local, sep=';', index=False, float_format='%.5f')

                    if exec_id:
                        object_path = build_object_path(exec_id, nombre_archivo_secundaria)
                    else:
                        object_path = nombre_archivo_secundaria  # fallback sin exec_id si fuera necesario

                    url_publica = await subir_a_bucket_y_obtener_url(ruta_local, object_path)
                    urls_generadas.append(url_publica)

                    if exec_id:
                        await asyncio.to_thread(
                            fs_registrar_archivo_generado,
                            exec_id, user_chat_id,
                            tipo="csv",
                            nombre=nombre_archivo_secundaria,
                            gcs_path=object_path,
                            signed_url=url_publica,     # o None si no quieres guardarla
                            content_type="text/csv",
                            metadata={"moneda_filtro": moneda_filtro, "particion": "principal", "filtrado": False},
                        )

                if origen == "app":
                    await enviar_csv_telegram(df_secundaria, context, nombre_archivo_secundaria, user_chat_id)
                else:
                    asyncio.create_task(enviar_csv_telegram(df_secundaria, context, nombre_archivo_secundaria, user_chat_id))
            else:
                logger.info(f"El DataFrame secundario está vacío. No se enviará el archivo: {nombre_archivo_secundaria}")

            # Filtrar también el archivo de oportunidades
            df_filtrado_principal = df_filtrado[df_filtrado['Activo'].str.startswith(moneda_filtro.upper())]
            df_filtrado_secundaria = df_filtrado[df_filtrado['Activo'].str.endswith(moneda_filtro.upper())]

            # Guardar los archivos filtrados separados para principales y secundarios
            nombre_archivo_filtrado_principal = generar_nombre_archivo(moneda_filtro, filtro=True, tipo="principal")
            #df_filtrado_principal.to_csv(nombre_archivo_filtrado_principal, sep=';', index=False, float_format='%.5f')

            nombre_archivo_filtrado_secundaria = generar_nombre_archivo(moneda_filtro, filtro=True, tipo="secundaria")
            #df_filtrado_secundaria.to_csv(nombre_archivo_filtrado_secundaria, sep=';', index=False, float_format='%.5f')

            # Verificar si los DataFrame no están vacíos antes de enviarlos por telegram
            if not df_filtrado_principal.empty:
                if origen == "app":
                    ruta_local = os.path.join("/tmp", nombre_archivo_filtrado_principal)
                    df_filtrado_principal.to_csv(ruta_local, sep=';', index=False, float_format='%.5f')

                    if exec_id:
                        object_path = build_object_path(exec_id, nombre_archivo_filtrado_principal)
                    else:
                        object_path = nombre_archivo_filtrado_principal  # fallback sin exec_id si fuera necesario

                    url_publica = await subir_a_bucket_y_obtener_url(ruta_local, object_path)
                    urls_generadas.append(url_publica)

                    if exec_id:
                        await asyncio.to_thread(
                            fs_registrar_archivo_generado,
                            exec_id, user_chat_id,
                            tipo="csv",
                            nombre=nombre_archivo_filtrado_principal,
                            gcs_path=object_path,
                            signed_url=url_publica,     # o None si no quieres guardarla
                            content_type="text/csv",
                            metadata={"moneda_filtro": moneda_filtro, "particion": "principal", "filtrado": False},
                        )
                
                if origen == "app":
                    await enviar_csv_telegram(df_filtrado_principal, context, nombre_archivo_filtrado_principal, user_chat_id)
                else:
                    asyncio.create_task(enviar_csv_telegram(df_filtrado_principal, context, nombre_archivo_filtrado_principal, user_chat_id))
            else:
                logger.info(f"El DataFrame filtrado principal está vacío. No se enviará el archivo: {nombre_archivo_filtrado_principal}")

            if not df_filtrado_secundaria.empty:
                if origen == "app":
                    ruta_local = os.path.join("/tmp", nombre_archivo_filtrado_secundaria)
                    df_filtrado_secundaria.to_csv(ruta_local, sep=';', index=False, float_format='%.5f')

                    if exec_id:
                        object_path = build_object_path(exec_id, nombre_archivo_filtrado_secundaria)
                    else:
                        object_path = nombre_archivo_filtrado_secundaria  # fallback sin exec_id si fuera necesario

                    url_publica = await subir_a_bucket_y_obtener_url(ruta_local, object_path)
                    urls_generadas.append(url_publica)

                    if exec_id:
                        await asyncio.to_thread(
                            fs_registrar_archivo_generado,
                            exec_id, user_chat_id,
                            tipo="csv",
                            nombre=nombre_archivo_filtrado_secundaria,
                            gcs_path=object_path,
                            signed_url=url_publica,     # o None si no quieres guardarla
                            content_type="text/csv",
                            metadata={"moneda_filtro": moneda_filtro, "particion": "principal", "filtrado": False},
                        )

                if origen == "app":
                    await enviar_csv_telegram(df_filtrado_secundaria, context, nombre_archivo_filtrado_secundaria, user_chat_id)
                else:
                    asyncio.create_task(enviar_csv_telegram(df_filtrado_secundaria, context, nombre_archivo_filtrado_secundaria, user_chat_id))
            else:
                logger.info(f"El DataFrame filtrado secundario está vacío. No se enviará el archivo: {nombre_archivo_filtrado_secundaria}")
        

    # Guardar el archivo principal
    nombre_archivo = generar_nombre_archivo(moneda_filtro)
    #df_resultados_ordenado.to_csv(nombre_archivo, sep=';', index=False, float_format='%.5f')
    
    # Guardar el archivo filtrado
    nombre_archivo_filtrado = generar_nombre_archivo(moneda_filtro, filtro=True)
    #df_filtrado.to_csv(nombre_archivo_filtrado, sep=';', index=False, float_format='%.5f')

    # Mostrar los resultados en formato de tabla
    #logger.info(f"Archivo completo generado: {nombre_archivo}")
    #logger.info(f"Archivo filtrado generado: {nombre_archivo_filtrado}")

    # Asegurar que el usuario tiene las claves necesarias en user_states
    if "lock" not in user_states[user_chat_id]:
        user_states[user_chat_id]["lock"] = asyncio.Lock()  # Agregar Lock si no existe
        user_states[user_chat_id]["lock_holder"] = None  # Inicializar con None
    if "archivos_enviados" not in user_states[user_chat_id]:
        user_states[user_chat_id]["archivos_enviados"] = False  # Estado inicial
    if "imagenes_oportunidades_enviadas" not in user_states[user_chat_id]:
        user_states[user_chat_id]["imagenes_oportunidades_enviadas"] = False  # Estado inicial
    if "imagenes_eventos_enviadas" not in user_states[user_chat_id]:
        user_states[user_chat_id]["imagenes_eventos_enviadas"] = False  # Estado inicial
    

    async with user_states[user_chat_id]["lock"]:
        user_states[user_chat_id]["lock_holder"] = asyncio.current_task()
        # Validar si df_resultados no está vacío antes de enviarlo como CSV
        if not df_resultados.empty:
            if origen == "app":
                ruta_local = os.path.join("/tmp", nombre_archivo)
                df_resultados.to_csv(ruta_local, sep=';', index=False, float_format='%.5f')

                if exec_id:
                    object_path = build_object_path(exec_id, nombre_archivo)
                else:
                    object_path = nombre_archivo  # fallback sin exec_id si fuera necesario

                url_publica = await subir_a_bucket_y_obtener_url(ruta_local, object_path)
                urls_generadas.append(url_publica)

                if exec_id:
                    await asyncio.to_thread(
                        fs_registrar_archivo_generado,
                        exec_id, user_chat_id,
                        tipo="csv",
                        nombre=nombre_archivo,
                        gcs_path=object_path,
                        signed_url=url_publica,     # o None si no quieres guardarla
                        content_type="text/csv",
                        metadata={"moneda_filtro": moneda_filtro, "particion": "principal", "filtrado": False},
                    )
            
            await enviar_csv_telegram(df_resultados, context, nombre_archivo, user_chat_id)
        else:
            logger.info(f"El DataFrame df_resultados está vacío. No se enviará el archivo CSV: {nombre_archivo}")

        # Validar si df_filtrado no está vacío antes de enviarlo como CSV
        if not df_filtrado.empty:
            if origen == "app":
                ruta_local = os.path.join("/tmp", nombre_archivo_filtrado)
                df_filtrado.to_csv(ruta_local, sep=';', index=False, float_format='%.5f')

                if exec_id:
                    object_path = build_object_path(exec_id, nombre_archivo_filtrado)
                else:
                    object_path = nombre_archivo_filtrado  # fallback sin exec_id si fuera necesario

                url_publica = await subir_a_bucket_y_obtener_url(ruta_local, object_path)
                urls_generadas.append(url_publica)

                if exec_id:
                    await asyncio.to_thread(
                        fs_registrar_archivo_generado,
                        exec_id, user_chat_id,
                        tipo="csv",
                        nombre=nombre_archivo_filtrado,
                        gcs_path=object_path,
                        signed_url=url_publica,     # o None si no quieres guardarla
                        content_type="text/csv",
                        metadata={"moneda_filtro": moneda_filtro, "particion": "principal", "filtrado": False},
                    )
            
            await enviar_csv_telegram(df_filtrado, context, nombre_archivo_filtrado, user_chat_id)
        else:
            logger.info(f"El DataFrame df_filtrado está vacío. No se enviará el archivo CSV: {nombre_archivo_filtrado}")

        user_states[user_chat_id]["archivos_enviados"] = True

        # Enviar imágenes solo después de que los archivos hayan sido enviados
        if user_states[user_chat_id]["archivos_enviados"]:    
            # Verificar si df_filtradoToImage no está vacío antes de enviar la imagen
            if not df_filtradoToImage.empty:
                df_para_imagen = preparar_df_oportunidades_para_tabla(df_filtradoToImage)

                imagenes = tabla_a_imagenes(
                    df_para_imagen,
                    max_filas_por_imagen=18,   # ajusta a gusto
                    dpi=170,
                    font_size=9,
                    wrap_map={"Tipo de Operación": 22}
                )

                if imagenes:
                    for i, img in enumerate(imagenes, 1):
                        try:
                            caption = "Oportunidades relacionadas a los activos seleccionados."
                            if len(imagenes) > 1:
                                caption += f" Parte {i} de {len(imagenes)}"
                            await context.bot.send_photo(chat_id=user_chat_id, photo=img, caption=caption)
                        except Exception as e:
                            logger.info(f"Error enviando imagen de oportunidades: {e}")
            else:
                logger.info(f"El DataFrame df_filtradoToImage está vacío. No se enviará la imagen.")

            user_states[user_chat_id]["imagenes_oportunidades_enviadas"] = True

            if user_states[user_chat_id]["imagenes_oportunidades_enviadas"]:  
                if not df_eventos.empty and divisas_oportunidades is not None and len(divisas_oportunidades) > 0:
                    # 1) JSON de eventos filtrados
                    if origen == "app" and exec_id:
                        json_eventos_url = await subir_json_eventos_filtrados(
                            df_eventos, divisas_oportunidades, exec_id, user_chat_id, moneda_filtro,
                            timezone_country, subir_a_bucket_y_obtener_url
                        )
                        if json_eventos_url:
                            urls_generadas.append(json_eventos_url)
                    # 2) Imágenes de eventos (subir y enviar)
                    urls_imgs = await enviar_imagen_eventos_oportunidades(
                        df_eventos, divisas_oportunidades, context, user_chat_id,
                        exec_id=exec_id, moneda_filtro=moneda_filtro,
                        subir_a_bucket_y_obtener_url=subir_a_bucket_y_obtener_url
                    )
                    if urls_imgs:
                        urls_generadas.extend(urls_imgs)
                else:
                    logger.info("El DataFrame df_eventos está vacío o divisas_oportunidades no contiene elementos válidos.")
                
                # Marcar imágenes como enviadas
                user_states[user_chat_id]["imagenes_eventos_enviadas"] = True

            if not es_administrador(user_chat_id):
                success, mensaje = await descontar_transaccion(user_chat_id, user_states[user_chat_id]["numero_transacciones"])
                if not success:
                    await update.message.reply_text(mensaje)
            
    logger.info(f"Devolviendo URLs al frontend: {urls_generadas}")
    
    urls_generadas = _solo_strings_urls(urls_generadas)
    logger.info(f"Devolviendo URLs al frontend: {urls_generadas}")
    return urls_generadas


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
def obtener_estado_usuario(user_chat_id):
    if user_chat_id not in user_states:
        user_states[user_chat_id] = {"estado": "disponible", "par_seleccionado": None, "cache_realtime": {}, "soportes_resistencias_cache": {}}
    return user_states[user_chat_id]

# Función para actualizar el estado de un usuario
def actualizar_estado_usuario(user_chat_id, estado, par_seleccionado=None):
    estado_usuario = obtener_estado_usuario(user_chat_id)
    estado_usuario["estado"] = estado
    estado_usuario["par_seleccionado"] = par_seleccionado
    estado_usuario["soportes_resistencias_cache"] = {}
    user_states[user_chat_id] = estado_usuario

# Función para limpiar el estado de un usuario
def limpiar_estado_usuario(user_chat_id):
    if user_chat_id in user_states:
        user_states[user_chat_id]["estado"] = "disponible"
        user_states[user_chat_id]["par_seleccionado"] = None
        user_states[user_chat_id]["cache_realtime"] = {}

def limpiar_soportes_resistencias_cache(user_chat_id):
    if user_chat_id in user_states:
        user_states[user_chat_id]["soportes_resistencias_cache"] = {}
        logger.info(f"Cache de soportes y resistencias reseteado para usuario {user_chat_id}.")
    else:
        # Si el usuario no tiene estado, inicializar el estado
        user_states[user_chat_id] = {
            "estado": "disponible",
            "soportes_resistencias_cache": {}
        }
        mark_user_state(user_chat_id, "disponible")
        logger.info(f"Estado inicializado para usuario {user_chat_id}.")


async def manejar_fecha_eventos(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user_chat_id = str(update.effective_chat.id)

    chat_ids = await cargar_chat_ids()
    if user_chat_id not in chat_ids:
        await update.message.reply_text("No estás registrado. Por favor, usa /start para registrarte.")
        return
    
    if await estado_suscripcion(user_chat_id) != 'activa' and not es_administrador(user_chat_id):
        await update.message.reply_text("No tiene una suscripción activa o no cuenta con la cuota de transacciones requerida.\n" \
                                        "Por favor,  contacta con un administrador.")
        return
    
    opciones_usuario = await obtener_opciones_usuario(user_chat_id)
    if not es_administrador(user_chat_id) and (not opciones_usuario or not any(opcion in opciones_usuario for opcion in ["eventos"])):
        await context.bot.send_message(chat_id=user_chat_id, text="No tienes opciones habilitadas para esta operación. Por favor, adquiere una suscripción.")
        return

    if return_state(user_chat_id) == "en ejecución":
        await context.bot.send_message(
            chat_id=user_chat_id, 
            text="Ya tienes un análisis en ejecución. Por favor, espera a que termine."
        )
        return

    try:
        # Inicializar el estado del usuario
        if user_chat_id not in user_states:
            user_states[user_chat_id] = {}
        
        user_states[user_chat_id]["estado"] = "esperando_fechas"
        user_states[user_chat_id]["fecha_inicio"] = None
        user_states[user_chat_id]["fecha_fin"] = None
        mark_user_state(user_chat_id, "esperando_fechas")

        mensaje = "Por favor, envíame las fechas de inicio y fin en formato YYYY-MM-DD separadas por un espacio."
        await context.bot.send_message(chat_id=user_chat_id, text=mensaje)
    except Exception as e:
        logger.info(f"Error al manejar el comando 'eventos_futuros': {e}")


async def manejar_fecha_noticias_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_chat_id = str(update.effective_chat.id)

    chat_ids = await cargar_chat_ids()
    if user_chat_id not in chat_ids:
        await update.message.reply_text("No estás registrado. Por favor, usa /start para registrarte.")
        return

    if await estado_suscripcion(user_chat_id) != 'activa' and not es_administrador(user_chat_id):
        await update.message.reply_text("No tiene una suscripción activa o no cuenta con la cuota de transacciones requerida.\n" \
                                        "Por favor,  contacta con un administrador.")
        return
    
    opciones_usuario = await obtener_opciones_usuario(user_chat_id)
    if not es_administrador(user_chat_id) and (not opciones_usuario or not any(opcion in opciones_usuario for opcion in ["noticias"])):
        await context.bot.send_message(chat_id=user_chat_id, text="No tienes opciones habilitadas para esta operación. Por favor, adquiere una suscripción.")
        return
    
    # Inicializar el estado del usuario para manejar noticias
    estado_usuario = obtener_estado_usuario(user_chat_id)
    if  return_state(user_chat_id) == "en ejecución":
        await context.bot.send_message(
            chat_id=user_chat_id,
            text="Ya tienes un análisis en ejecución. Por favor, espera a que termine."
        )
        return
  
    # Cambiar el estado para manejar noticias
    estado_usuario["estado"] = "esperando_fechas_noticias_user"
    estado_usuario["fecha_inicio"] = None
    estado_usuario["fecha_fin"] = None
    mark_user_state(user_chat_id, "esperando_fechas_noticias_user")

    mensaje = "Por favor, envíame una fecha y un simbolo(por ejemplo: AAPL, BTCUSD, etc.) en formato: YYYY-MM-DD simbolo."
    await context.bot.send_message(chat_id=user_chat_id, text=mensaje)

async def manejar_fecha_noticias_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_chat_id = str(update.effective_chat.id)

    chat_ids = await cargar_chat_ids()
    if user_chat_id not in chat_ids:
        await update.message.reply_text("No estás registrado. Por favor, usa /start para registrarte.")
        return

    if await estado_suscripcion(user_chat_id) != 'activa' and not es_administrador(user_chat_id):
        await update.message.reply_text("No tiene una suscripción activa o no cuenta con la cuota de transacciones requerida.\n" \
                                        "Por favor,  contacta con un administrador.")
        return
    
    opciones_usuario = await obtener_opciones_usuario(user_chat_id)
    if not es_administrador(user_chat_id) and (not opciones_usuario or not any(opcion in opciones_usuario for opcion in ["noticias"])):
        await context.bot.send_message(chat_id=user_chat_id, text="No tienes opciones habilitadas para esta operación. Por favor, adquiere una suscripción.")
        return
    
    # Inicializar el estado del usuario para manejar noticias
    estado_usuario = obtener_estado_usuario(user_chat_id)
    if return_state(user_chat_id) == "en ejecución":
        await context.bot.send_message(
            chat_id=user_chat_id,
            text="Ya tienes un análisis en ejecución. Por favor, espera a que termine."
        )
        return
  
    # Cambiar el estado para manejar noticias
    estado_usuario["estado"] = "esperando_fechas_noticias_admin"
    estado_usuario["fecha_inicio"] = None
    estado_usuario["fecha_fin"] = None
    mark_user_state(user_chat_id, "esperando_fechas_noticias_admin")

    mensaje = "Por favor, envíame una fecha formato YYYY-MM-DD."
    await context.bot.send_message(chat_id=user_chat_id, text=mensaje)


async def manejar_ia_grafico(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_chat_id = str(update.effective_chat.id)

    try:
        mark_user_state(user_chat_id, "esperando_grafico_ia")
        await update.message.reply_text("📸 Por favor, sube una imagen de un gráfico de velas para que pueda analizarla con IA.")
    except Exception as e:
        await update.message.reply_text(f"Ocurrió un error al preparar el análisis: {e}")


async def analizar_simbolo(update, context):
    """Pide al usuario que ingrese un símbolo."""
    user_chat_id = str(update.effective_chat.id)

    # Verificar si el usuario está registrado
    chat_ids = await cargar_chat_ids()
    if user_chat_id not in chat_ids:
        await update.message.reply_text("No estás registrado. Por favor, usa /start para registrarte.")
        return

    # Validar estado de la suscripción
    if await estado_suscripcion(user_chat_id) != 'activa' and not es_administrador(user_chat_id):
        await update.message.reply_text("No tiene una suscripción activa o no cuenta con la cuota de transacciones requerida.\n" \
                                        "Por favor,  contacta con un administrador.")
        return
    
    # Inicializar el estado del usuario para manejar noticias
    estado_usuario = obtener_estado_usuario(user_chat_id)
    if  return_state(user_chat_id) == "en ejecución":
        await context.bot.send_message(
            chat_id=user_chat_id,
            text="Ya tienes un análisis en ejecución. Por favor, espera a que termine."
        )
        return

    # Cambiar el estado para manejar noticias
    estado_usuario["estado"] = "esperando_simbolo"
    mark_user_state(user_chat_id, "esperando_simbolo")

    # Solicitar el símbolo al usuario
    await update.message.reply_text("Por favor, ingresa el símbolo que deseas analizar (por ejemplo: AAPL, BTCUSD, etc.)\n"\
                                    "En caso de no conocer puede consultar a soporte: manuelt84@gmaill.com")
    context.user_data["esperando_simbolo"] = True  # Marcar que estamos esperando un símbolo


def analizar_importancia(texto):
    if not texto or pd.isna(texto):
        return "Sin clasificación"  # Sin ajuste si no hay texto

    # Analizar el sentimiento usando TextBlob
    sentimiento = TextBlob(texto).sentiment.polarity

    # Determinar la importancia basada en el sentimiento
    if sentimiento > 0.2:
        return "Alta"  # Alta importancia positiva
    elif sentimiento < -0.2:
        return "Baja"  # Alta importancia negativa
    else:
        return "Media"  # Importancia neutral
    

async def manejar_respuesta_fechas(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user_chat_id = str(update.effective_chat.id)

    global timezone_country
    timezone_country = pytz.timezone(await cargar_timezone_por_defecto(user_chat_id))

    if  return_state(user_chat_id) == "disponible":
        await update.message.reply_text("Por favor, usa el comando adecuado primero.")
        return

    estado_firestore=return_state(user_chat_id)

    # Manejar símbolo ingresado por el usuario
    if  estado_firestore == "esperando_simbolo":
        try:
            await update.message.reply_text(f"Empezamos a obtenerla información, espere un momento por favor.")

            # Capturar el símbolo ingresado por el usuario
            simbolo = update.message.text.strip()

            # Validar el símbolo (puedes agregar validaciones específicas aquí)
            if not simbolo:
                raise ValueError("El símbolo no puede estar vacío.")

            opciones_usuario = await obtener_opciones_usuario(user_chat_id)

            # Ejecutar el análisis en una tarea asíncrona
            asyncio.create_task(ejecutar_recurrente(context, update, simbolo.upper(), user_chat_id, opciones_usuario))

        except Exception as e:
            await update.message.reply_text(f"Hubo un error procesando el símbolo: {e}")

        finally:
            # Limpiar el estado del usuario
            if user_chat_id in user_states:
                user_states[user_chat_id]["estado"] = "disponible"
            mark_user_state(user_chat_id, "disponible")

    elif estado_firestore == "esperando_fechas":
        try:
            uid = str(update.effective_user.id)   # usuario de Telegram -> Firestore/user_states
            chat_id = update.effective_chat.id    # chat para enviar mensajes

            await update.message.reply_text("Empezamos a obtener la información, espere un momento por favor.")

            # Inicializa estructura para evitar KeyError (si no existe)
            state = user_states.setdefault(uid, {})
            state.setdefault("estado", "disponible")
            state.setdefault("links_enviados", False)
            state.setdefault("imagenes_enviadas", False)
            state.setdefault("lock", asyncio.Lock())
            state.setdefault("lock_holder", None)
            state["fecha_inicio"] = None
            state["fecha_fin"] = None

            fechas = update.message.text.split()
            if len(fechas) != 2:
                raise ValueError("Debes ingresar dos fechas en formato YYYY-MM-DD YYYY-MM-DD.")

            fecha_inicio = pd.to_datetime(fechas[0], format="%Y-%m-%d", errors='coerce')
            fecha_fin    = pd.to_datetime(fechas[1], format="%Y-%m-%d", errors='coerce')
            if pd.isnull(fecha_inicio) or pd.isnull(fecha_fin):
                raise ValueError("Formato de fecha inválido.")
            if fecha_inicio > fecha_fin:
                raise ValueError("La fecha de inicio debe ser menor o igual a la fecha de fin.")

            fecha_hoy = datetime.now(timezone_country).date()
            if (fecha_fin.date() - fecha_inicio.date()).days > 7:
                raise ValueError("El rango de fechas no puede exceder 7 días para eventos.")
            if fecha_inicio.date() < (fecha_hoy - timedelta(days=14)):
                raise ValueError("El rango de fechas no puede superar 14 días en el pasado para eventos.")
            if fecha_fin.date() > (fecha_hoy + timedelta(days=14)):
                raise ValueError("El rango de fechas no puede superar 14 días en el futuro para eventos.")

            state["fecha_inicio"] = fecha_inicio
            state["fecha_fin"] = fecha_fin
            actualizar_estado_usuario(uid, "en ejecución")
            mark_user_state(uid, "en ejecución")

            # Obtener eventos futuros
            df_eventos = await obtener_eventos_guardados_o_futuros(fecha_inicio, fecha_fin)
            if df_eventos.empty:
                await update.message.reply_text(
                    f"No se encontraron eventos económicos entre {fecha_inicio.strftime('%Y-%m-%d')} y {fecha_fin.strftime('%Y-%m-%d')}."
                )
            else:
                # Ya están inicializados en 'state', no revalides claves
                async with state["lock"]:
                    state["lock_holder"] = asyncio.current_task()
                    await enviar_imagenes_por_currency_a_usuario(df_eventos, context, chat_id)
                    state["imagenes_enviadas"] = True

                    if state["imagenes_enviadas"]:
                        asyncio.create_task(enviar_eventos_y_archivo_calendar(df_eventos, context, chat_id))
                        state["links_enviados"] = True

                    if not es_administrador(uid):
                        success, mensaje = await descontar_transaccion(uid, 1)
                        if not success:
                            await update.message.reply_text(mensaje)

        except Exception as e:
            await update.message.reply_text(f"Hubo un error procesando las fechas: {e}")
        finally:
            if uid in user_states:
                user_states[uid]["fecha_inicio"] = None
                user_states[uid]["fecha_fin"] = None
                user_states[uid]["estado"] = "disponible"
            mark_user_state(uid, "disponible")


    elif estado_firestore == "esperando_fechas_noticias_user":
        try:
            await update.message.reply_text(f"Empezamos a obtenerla información, espere un momento por favor.")

            input = update.message.text.split()
            if len(input) != 2:
                raise ValueError("Debes ingresar una fecha en formato YYYY-MM-DD simbolo.")

            fecha_inicio = pd.to_datetime(input[0], format="%Y-%m-%d", errors='coerce').tz_localize(timezone_country)
            fecha_fin = fecha_inicio
            if pd.isnull(fecha_inicio):
                raise ValueError("Formato de fecha inválido.")

            fecha_hoy = datetime.now(timezone_country).replace(tzinfo=timezone_country).date()

            # Validaciones para noticias
            if fecha_fin.date() > fecha_hoy:
                raise ValueError("La fecha final no puede ser mayor que hoy para noticias.")

            user_states[user_chat_id]["fecha_inicio"] = fecha_inicio
            user_states[user_chat_id]["fecha_fin"] = fecha_fin
            actualizar_estado_usuario(user_chat_id, "en ejecución")
            mark_user_state(user_chat_id, "en ejecución")

            # Obtener noticias
            noticias = obtener_noticias_simbolo(input[1].upper(), fecha_inicio, fecha_fin, limite=15)

            if noticias.empty:  # Verificar si el DataFrame está vacío
                await update.message.reply_text("No se encontraron noticias en el rango de fechas especificado para el símbolo ingresado.")
                return

            # Verificar si las columnas esperadas existen
            if not all(col in noticias.columns for col in ['symbol', 'publishedDate', 'url', 'title']):
                logger.info(f"Las columnas esperadas no están presentes en el DataFrame para el símbolo {input[1]}: {noticias.columns.tolist()}")
                return  

            # Filtrar noticias del día
            noticias_del_dia = noticias[noticias['publishedDate'].dt.date == fecha_inicio.date()]
            if noticias_del_dia.empty:
                await update.message.reply_text("No se encontraron noticias publicadas en la fecha ingresada.")
                return

            # Enviar mensajes de cada noticia
            for index, noticia in noticias_del_dia.iterrows():
                title = noticia['title']
                sitio = noticia.get('site', 'No especificado')
                text = noticia.get('text', 'Sin Descripción')
                symbol = noticia['symbol']
                fecha = noticia['publishedDate'].strftime('%Y-%m-%d %H:%M:%S')
                importancia = analizar_importancia(title + ' ' + text)
                url = noticia['url']
                link_traductor = f"https://translate.google.com/translate?sl=auto&tl=es&u={url}"  # Enlace a Google Translate

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

            if not es_administrador(user_chat_id):
                success, mensaje = await descontar_transaccion(user_chat_id, 1)
                if not success:
                    await update.message.reply_text(mensaje)
        except ValueError as e:
            await update.message.reply_text(f"Error: {str(e)}")
        except Exception as e:
            await update.message.reply_text(f"Hubo un error procesando las fechas para noticias: {e}")

        finally:
            # Limpiar el estado del usuario
            if user_chat_id in user_states:
                user_states[user_chat_id]["fecha_inicio"] = None
                user_states[user_chat_id]["fecha_fin"] = None
                user_states[user_chat_id]["estado"] = "disponible"
            mark_user_state(user_chat_id, "disponible")
    
    elif estado_firestore == "esperando_fechas_noticias_admin":
        try:
            await update.message.reply_text(f"Empezamos a obtenerla información, espere un momento por favor.")

            fechas = update.message.text.split()
            if len(fechas) != 1:
                raise ValueError("Debes ingresar una fecha en formato YYYY-MM-DD.")

            fecha_inicio = pd.to_datetime(fechas[0], format="%Y-%m-%d", errors='coerce').tz_localize(timezone_country)
            fecha_fin = fecha_inicio
            if pd.isnull(fecha_inicio):
                raise ValueError("Formato de fecha inválido.")

            fecha_hoy = datetime.now(timezone_country).replace(tzinfo=timezone_country).date()

            # Validaciones para noticias
            if fecha_fin.date() > fecha_hoy:
                raise ValueError("La fecha final no puede ser mayor que hoy para noticias.")

            user_states[user_chat_id]["fecha_inicio"] = fecha_inicio
            user_states[user_chat_id]["fecha_fin"] = fecha_fin
            actualizar_estado_usuario(user_chat_id, "en ejecución")
            mark_user_state(user_chat_id, "en ejecución")

            # Obtener noticias
            activos_filtrados = filtrar_activos_por_moneda(activos, 'todos')
            todas_las_noticias = []  # Lista para acumular todas las noticias

            for symbol in activos_filtrados:
                noticias = obtener_noticias_simbolo(symbol, fecha_inicio, fecha_fin, limite=2) #symbol, fecha_inicio=None, fecha_fin=None, limite=50, max_reintentos=3, tiempo_espera_inicial=5
                
                if noticias.empty:
                    continue  # No hay noticias, continuar con el siguiente símbolo

                # Verificar si las columnas esperadas existen
                if not all(col in noticias.columns for col in ['symbol', 'publishedDate', 'url', 'title']):
                    logger.info(f"Las columnas esperadas no están presentes en el DataFrame para el símbolo {symbol}: {noticias.columns.tolist()}")
                    continue  # O manejar de otra forma según tus necesidades
                
                todas_las_noticias.append(noticias)

            if not todas_las_noticias:
                await update.message.reply_text("No se encontraron noticias en el rango de fechas especificado.")
                return

            # Enviar mensajes de cada noticia
            for noticias in todas_las_noticias:
                noticias_del_dia = noticias[noticias['publishedDate'].dt.date == fecha_inicio.date()]
                for index, noticia in noticias_del_dia.iterrows():
                    title = noticia['title']
                    sitio = noticia['site']
                    text = noticia['text'] if 'text' in noticia else 'Sin Descripción'
                    symbol = noticia['symbol']
                    fecha = noticia['publishedDate'].strftime('%Y-%m-%d %H:%M:%S')
                    importancia = analizar_importancia(title + ' ' + text) 
                    url = noticia['url']
                    link_traductor = f"https://translate.google.com/translate?sl=auto&tl=es&u={url}"  # Enlace a Google Translate

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

            if not es_administrador(user_chat_id):
                success, mensaje = await descontar_transaccion(user_chat_id, 1)
                if not success:
                    await update.message.reply_text(mensaje)        
        
        except Exception as e:
            await update.message.reply_text(f"Hubo un error procesando las fechas para noticias: {e}")

        finally:
            # Limpiar el estado del usuario
            if user_chat_id in user_states:
                user_states[user_chat_id]["fecha_inicio"] = None
                user_states[user_chat_id]["fecha_fin"] = None
                user_states[user_chat_id]["estado"] = "disponible"
            mark_user_state(user_chat_id, "disponible")
    
    elif estado_firestore == "modo_envio_mensaje":
        try:
            mensaje_usuario = update.message.text if update.message.text else update.message.caption  # Capturar mensaje en texto o caption
            archivos_guardados = []  # Lista para almacenar rutas de archivos

            # 📸 Capturar imágenes
            if update.message.photo:
                imagen_usuario = update.message.photo[-1]  # Toma la mejor calidad
                file_id = imagen_usuario.file_id
                archivos_guardados.append({"tipo": "imagen", "file_id": file_id})

            # 🎥 Capturar videos
            if update.message.video:
                video_usuario = update.message.video
                file_id = video_usuario.file_id
                archivos_guardados.append({"tipo": "video", "file_id": file_id})

            # 📂 Capturar documentos
            if update.message.document:
                documento_usuario = update.message.document
                file_id = documento_usuario.file_id
                archivos_guardados.append({"tipo": "documento", "file_id": file_id})

            # Obtener destinatario manual si existe
            user_ref = db.collection("user_states").document(user_chat_id)
            user_data = user_ref.get().to_dict() if user_ref.get().exists else {}
            destinatario_manual = user_data.get("destinatario_manual")

            # Guardar mensaje en Firestore
            user_ref.set({
                "mensaje_admin": mensaje_usuario,
                "archivos_guardados": archivos_guardados,
                "destinatario_manual": destinatario_manual  # Mantener para `confirmar_envio`
            }, merge=True)

            # Botones para confirmar o cancelar el envío
            keyboard = [
                [InlineKeyboardButton("✅ Confirmar Envío", callback_data="confirmar_envio")],
                [InlineKeyboardButton("❌ Cancelar", callback_data="cancelar_envio_mensaje")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)

            await update.message.reply_text("📩 ¿Confirmas el envío del mensaje?", reply_markup=reply_markup)
        except Exception as e:
            await update.message.reply_text(f"Hubo un error procesando el envío del mensaje: {e}")
            mark_user_state(user_chat_id, "disponible")


    elif estado_firestore == "esperando_id_usuario":
        try:
            await recibir_usuario_especifico(update, context)  # Redirigir a función de ingreso de ID
        except Exception as e:
            await update.message.reply_text(f"Hubo un error procesando el envío del mensaje a un usuario específico: {e}")
            mark_user_state(user_chat_id, "disponible")

    elif estado_firestore == "esperando_grafico_ia":
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
                success, mensaje = await descontar_transaccion(user_chat_id, 1)
                if not success:
                    await update.message.reply_text(mensaje)    

        except Exception as e:
            await update.message.reply_text(f"Hubo un error analizando la imagen: {e}")

        finally:
            mark_user_state(user_chat_id, "disponible")
            try:
                if os.path.exists(ruta_local):
                    os.remove(ruta_local)
                if 'ruta_salida' in locals() and os.path.exists(ruta_salida):
                    os.remove(ruta_salida)
            except Exception as cleanup_error:
                print(f"⚠️ Error al eliminar archivos temporales: {cleanup_error}")


async def recibir_usuario_especifico(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Guarda el ID del usuario específico y activa el modo de envío de mensaje."""
    user_chat_id = str(update.effective_chat.id)
    message_text = update.message.text.strip()

    if not message_text.isdigit():
        await update.message.reply_text("⚠️ Ingresa un ID de usuario válido (solo números).")
        return
    
    # Guardar ID del usuario en Firestore directamente
    user_ref = db.collection("user_states").document(user_chat_id)
    user_ref.set({"destinatario_manual": message_text}, merge=True)  # 👈 Se guarda aquí manualmente

    # Guardar ID en Firestore y activar el modo de envío de mensaje
    mark_user_state(user_chat_id, "modo_envio_mensaje", "usuario_especifico")
    await update.message.reply_text(f"✅ Usuario {message_text} seleccionado. Ahora envía el mensaje o archivo.")


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
        
# Función para manejar el ciclo recurrente del análisis
#@profile
async def ejecutar_recurrente(context, update, moneda_filtro, user_chat_id=None, opciones_usuario=[], origen="telegram", exec_id: str | None = None, operatoria_cfg: dict | None = None, cfg: dict | None = None):
    activos_filtrados = filtrar_activos_por_moneda(activos, moneda_filtro)
    if user_chat_id not in user_states:
            user_states[user_chat_id] = {}
            
    user_states[user_chat_id]["numero_transacciones"] = len(activos_filtrados)

    if not activos_filtrados:
        await context.bot.send_message(
            chat_id=user_chat_id,
            text="No se encontraron activos para analizar con el filtro especificado."
        )
        return

    if await estado_suscripcion(user_chat_id, user_states[user_chat_id]["numero_transacciones"]) == 'transacciones_insuficientes' and not es_administrador(user_chat_id):
        await context.bot.send_message(
            chat_id=user_chat_id,
            text="No cuenta con la cuota de transacciones requerida. Por favor, contacta con un administrador."
        )
        return
    
    if return_state(user_chat_id) == "en ejecución":
        await context.bot.send_message(
            chat_id=user_chat_id, 
            text="Ya tienes un análisis en ejecución. Por favor, espera a que termine."
        )
        return
    
    # Pon la config en memoria para esta ejecución
    if user_chat_id not in user_states:
        user_states[user_chat_id] = {}
    user_states[user_chat_id]["operatoria_cfg"] = operatoria_cfg or {}

    user = update.effective_user.first_name
    await context.bot.send_message(chat_id=user_chat_id, text=f"Hola {user}, comenzó el análisis. Por favor, espera un momento...")

    actualizar_estado_usuario(user_chat_id, "en ejecución", moneda_filtro)
    mark_user_state(user_chat_id, "en ejecución")
    limpiar_soportes_resistencias_cache(user_chat_id)
    estado_usuario = obtener_estado_usuario(user_chat_id)
    estado_usuario["cache_realtime"] = {}

    logger.info(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Ejecutando análisis para el usuario {user_chat_id}...")

    if exec_id:
        try:
            await asyncio.to_thread(fs_actualizar_ejecucion, exec_id,
                                    activos_resueltos=activos_filtrados,
                                    numero_transacciones=len(activos_filtrados))
        except Exception as e:
            logging.warning(f"No se pudo actualizar ejecucion {exec_id}: {e}")

    try:
        start_time = datetime.now()
        try:
            df_eventos = await obtener_eventos_economicos()  # Ensure this function is async
        except Exception as e:
            logging.warning(f"Error al obtener eventos económicos: {e}")
            df_eventos = None  # continuar sin eventos

        resultados = await ejecutar_analisis_con_hilos(
            df_eventos, activos_filtrados, user_chat_id, context,
            overrides={
                'tfs': (operatoria_cfg or {}).get('tfs'),
                'fmpWindows':    (operatoria_cfg or {}).get('fmpWindows'),
                'calcWindows':   (operatoria_cfg or {}).get('calcWindows'),
            },
            cfg=cfg
        )

        if not resultados:
            await context.bot.send_message(
                chat_id=user_chat_id,
                text="El análisis no produjo resultados. Verifique los datos y vuelva a intentarlo."
            )
            return

        url_generadas = await procesar_resultado(resultados, df_eventos, context, update, moneda_filtro, user_chat_id, opciones_usuario, origen, exec_id=exec_id, cfg=cfg)

        elapsed_time = (datetime.now() - start_time).total_seconds()
        logger.info(f"[{datetime.now()}] Análisis finalizado para usuario {user_chat_id}. Tiempo: {elapsed_time:.2f} segundos.")
        return url_generadas
    
    finally:
        limpiar_estado_usuario(user_chat_id)
        mark_user_state(user_chat_id, "disponible")


async def stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Maneja el comando /stop para eliminar el chat_id del registro."""
    user_chat_id = str(update.effective_chat.id)
    
    await resetear_menu_usuario(context, user_chat_id)

    await eliminar_chat_id(user_chat_id)

    await update.message.reply_text("Has sido eliminado del registro. Si deseas volver a usar el bot, usa el comando /start.")

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
async def trader_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user_chat_id = str(update.effective_chat.id)
    chat_ids = await cargar_chat_ids()

    if user_chat_id not in chat_ids:
        await update.message.reply_text("No estás registrado. Por favor, usa /start para registrarte.")
        return
    
    if await estado_suscripcion(user_chat_id) != 'activa' and not es_administrador(user_chat_id):
        await update.message.reply_text("No tiene una suscripción activa o no cuenta con la cuota de transacciones requerida.\n" \
                                        "Por favor,  contacta con un administrador.")
        return
        
    await menu(update, context)


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


async def seleccionar_par(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_chat_id = str(update.callback_query.message.chat_id)

    global timezone_country
    timezone_country = pytz.timezone(await cargar_timezone_por_defecto(user_chat_id))

    chat_ids = await cargar_chat_ids()
    if user_chat_id not in chat_ids:
        await update.message.reply_text("No estás registrado. Por favor, usa /start para registrarte.")
        return

    if await estado_suscripcion(user_chat_id) != 'activa' and not es_administrador(user_chat_id):
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

    if  return_state(user_chat_id) == "en ejecución":
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



async def menu_usuario_registrado(bot, user_chat_id):
    """El menú del usuario registrado según su estado de suscripción."""
    try:
        if  await estado_suscripcion(user_chat_id) == 'activa' :
            # Menú para usuarios con suscripción activa
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
            # Menú para usuarios sin suscripción activa
            comandos_principales = [
                BotCommand("menu_suscripciones", "Menu suscripción"),
                BotCommand("verificar_pago", "Verificar pago"),
                BotCommand("listar_pagos", "Listar pagos"),
                BotCommand("verificar_suscripcion", "Verificar suscripción"),
                BotCommand("stop", "Detener el bot"),
            ]

        # Configurar comandos para el usuario
        await bot.set_my_commands(comandos_principales, scope=BotCommandScopeChat(user_chat_id))
    except Exception as e:
        logger.info(f"Error al resetear el menú para el usuario {user_chat_id}: {e}")


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

async def comando_reset_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando para que un usuario pueda resetear su menú."""
    global timezone_country
    user_chat_id = update.effective_chat.id
    await resetear_menu_usuario(context, user_chat_id)

async def cargar_datos_subscription_user():
    """Carga los datos de suscripción de usuarios desde Firestore o devuelve un diccionario vacío si no hay datos."""
    try:
        # Obtén la referencia a la colección "suscripciones"
        collection_ref = db.collection("suscripciones_user")
        
        # Consulta todos los documentos de la colección
        docs = collection_ref.stream()

        # Construir un diccionario con los datos de los usuarios
        datos_suscripciones = {
            doc.id: doc.to_dict()
            for doc in docs if doc.exists
        }

        return datos_suscripciones
    except Exception as e:
        print(f"Error al cargar datos de suscripción de usuarios desde Firestore: {e}")
        return {}  # Devuelve un diccionario vacío en caso de error


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


async def guardar_datos(data):
    """Guarda o actualiza los datos de suscripción de usuarios en Firestore."""
    try:
        for user_id, detalles in data.items():
            # Referencia al documento del usuario en la colección "suscripciones"
            doc_ref = db.collection("suscripciones_user").document(str(user_id))
            
            # Guardar los datos en Firestore (merge=True para actualizar campos existentes)
            doc_ref.set(detalles, merge=True)
        
        print("Datos de suscripción guardados/actualizados exitosamente.")

        # 🔄 **Recargar `subscriptions` en memoria después de guardar en Firestore**
        global subscriptions
        subscriptions = await cargar_datos_subscription_user()

    except Exception as e:
        print(f"Error al guardar datos de suscripción en Firestore: {e}")



# Función para verificar si un usuario es administrador
def es_administrador(user_id):
    return user_id in admin_ids

# Función para agregar un usuario a la lista de suscritos
async def agregar_suscripcion(update: Update, context: ContextTypes.DEFAULT_TYPE):
    subscriptions = await cargar_datos_subscription_user()
    user_chat_id = str(update.effective_user.id)
    if not es_administrador(user_chat_id):
        await update.message.reply_text("No tienes permisos para usar este comando.")
        return

    args = context.args
    if len(args) < 3 or len(args) > 4:
        await update.message.reply_text("Uso: /agregar_suscripcion <user_id> <tipo_suscripcion> <nombre_usuario> [hash_transaccion]")
        return

    user_id, tipo_suscripcion, nombre_usuario = args[:3]
    user_id = int(user_id)
    
    # Obtener el hash_transaccion si se proporciona
    hash_transaccion = args[3] if len(args) == 4 else None

    # Validar el tipo de suscripción
    if tipo_suscripcion not in subscriptions_type:
        await update.message.reply_text("Tipo de suscripción no válido.")
        return

    # Obtener los detalles de la suscripción
    detalles_suscripcion = subscriptions_type[tipo_suscripcion]
    duracion = detalles_suscripcion["duracion"]

    if duracion == "1 mes":
        dias =30
    elif duracion == "6 meses":
        dias = 30 * 6
    elif duracion == "1 año":
        dias = 365
    else:
        await update.message.reply_text("Duración de la suscripción no reconocida.")
        return


    inicio = datetime.now()
    fin = inicio + timedelta(days=dias)

    # Generar el ID de pago
    id_pago = generar_hash(str(user_id), tipo_suscripcion)

    # Actualizar las suscripciones
    subscriptions[user_id] = {
        "nombre_usuario": nombre_usuario,
        "id_pago": id_pago,  # Agregar el id_pago a la suscripción
        "inicio": inicio.isoformat(),
        "fin": fin.isoformat(),
        "limite_transacciones": detalles_suscripcion["transacciones_maximas"],  # Límite de transacciones
        "transacciones_restantes": detalles_suscripcion["transacciones_maximas"],
        "opciones": detalles_suscripcion["opciones"]  # Opciones de la suscripción
    }
    await guardar_datos(subscriptions)

    # Agregar pago pendiente
    pagos_pendientes = await cargar_pagos_pendientes()

    pago_info = {
        "user_id": str(user_id),
        "monto": detalles_suscripcion["precio"],
        "id_pago": id_pago,
        "estado": "verificado",  # O "pendiente" si así lo deseas
        "suscripcion": tipo_suscripcion,
        "fecha": datetime.now().isoformat()
    }
    
    # Incluir el hash_transaccion si se proporcionó
    if hash_transaccion:
        pago_info["hash_transaccion"] = hash_transaccion

    index_existente = next(
        (index for index, pago in enumerate(pagos_pendientes["pendientes"]) if pago["user_id"] == str(user_id) and pago["estado"] == "pendiente"), 
        None
    )

    if index_existente is not None:
        # Reemplazar el pago existente con el nuevo
        pagos_pendientes["pendientes"][index_existente] = pago_info
        logger.info(f"Pago pendiente actualizado para el usuario {user_id}.")
    else:
        # Agregar un nuevo pago pendiente
        pagos_pendientes["pendientes"].append(pago_info)
        logger.info(f"Nuevo pago pendiente agregado para el usuario {user_id}.")

    guardar_pagos_pendientes(pagos_pendientes)

    await update.message.reply_text(
        f"Suscripción agregada para {nombre_usuario}. Tu ID de pago es {id_pago}."
        + (f"\nHash de transacción: {hash_transaccion}" if hash_transaccion else "")
    )

async def eliminar_suscripcion(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Elimina una suscripción de un usuario en Firestore y en memoria."""
    user_chat_id = str(update.effective_user.id)

    if not es_administrador(user_chat_id):
        await update.message.reply_text("No tienes permisos para usar este comando.")
        return

    args = context.args
    if len(args) != 1:
        await update.message.reply_text("Uso: /eliminar_suscripcion <user_id>")
        return

    user_id = args[0]

    # Referencia al documento en Firestore
    doc_ref = db.collection("suscripciones_user").document(user_id)

    # Verificar si la suscripción existe
    user_data = doc_ref.get()
    if not user_data.exists:
        await update.message.reply_text(f"No se encontró una suscripción para el ID: {user_id}.")
        return

    # Obtener el nombre de usuario antes de eliminar
    nombre_usuario = user_data.to_dict().get("nombre_usuario", "Usuario desconocido")

    try:
        # Eliminar el documento de Firestore
        doc_ref.delete()

        # 🔥 Eliminar también de memoria
        subscriptions = await cargar_datos_subscription_user()  # Recargar datos actualizados
        if user_id in subscriptions:
            del subscriptions[user_id]  # Ahora sí se elimina de memoria

        await update.message.reply_text(f"✅ Suscripción eliminada para {nombre_usuario} (ID: {user_id}).")

    except Exception as e:
        await update.message.reply_text(f"⚠️ Error al eliminar la suscripción: {e}")


# Función para verificar el estado de suscripción de un usuario
async def verificar_suscripcion(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global subscriptions
    subscriptions = await cargar_datos_subscription_user()

    user_chat_id = str(update.effective_user.id)
    suscripcion = subscriptions.get(user_chat_id)

    if suscripcion:
        inicio = datetime.fromisoformat(suscripcion["inicio"])
        fin = datetime.fromisoformat(suscripcion["fin"])
        transacciones_restantes = suscripcion["transacciones_restantes"]

        if fin < datetime.now():
            estado = "expirada"
            mensaje = f"Tu suscripción está {estado}.\nInicio: {inicio}\nFin: {fin}"
        elif transacciones_restantes <= 0:
            estado = "inactiva"
            mensaje = f"Tu suscripción está {estado} por falta de transacciones.\nTransacciones restantes: {transacciones_restantes}"
        else:
            estado = "activa"
            mensaje = f"Tu suscripción está {estado}.\nInicio: {inicio}\nFin: {fin}\nTransacciones restantes: {transacciones_restantes}"
            await menu_usuario_registrado(context.bot, user_chat_id)
            
        await update.message.reply_text(mensaje)

    elif es_administrador(user_chat_id):
        await update.message.reply_text("No necesitas suscripción, eres administrador!")
        await menu_usuario_administrador(context, user_chat_id)

    else:
        await update.message.reply_text("No tienes una suscripción activa. Por favor, contacta con un administrador.")


async def estado_suscripcion(user_chat_id, numero_transacciones = 1):
    global subscriptions
    subscriptions = await cargar_datos_subscription_user()

    suscripcion = subscriptions.get(user_chat_id)

    if suscripcion:
        inicio = datetime.fromisoformat(suscripcion["inicio"])
        fin = datetime.fromisoformat(suscripcion["fin"])
        transacciones_restantes = suscripcion["transacciones_restantes"]

        # Determinar el estado basado en las fechas y el número de transacciones restantes
        if fin < datetime.now(pytz.utc):
            estado = "expirada"
        elif transacciones_restantes <= 0:
            estado = "inactiva"
        elif numero_transacciones > transacciones_restantes:
            estado = "transacciones_insuficientes"
        else:
            estado = "activa"
    else:
        # Si no tiene suscripción activa, establecemos un estado "sin suscripción"
        estado = "sin suscripción"

    # Retornar el estado para posibles validaciones posteriores
    return estado



# Función para listar todas las suscripciones (solo admin)
async def listar_suscripciones(update: Update, context: CallbackContext):
    subscriptions = await cargar_datos_subscription_user()
    user_chat_id = str(update.effective_user.id)
    if not es_administrador(user_chat_id):
        await update.message.reply_text("No tienes permisos para usar este comando.")
        return

    if not subscriptions:
        await update.message.reply_text("No hay usuarios suscritos.")
        return

    mensaje = "Suscripciones actuales:\n"
    for user_id, data in subscriptions.items():
        inicio = datetime.fromisoformat(data["inicio"])
        fin = datetime.fromisoformat(data["fin"])
        estado = "activa" if fin >= datetime.now() else "expirada"
        transacciones_restantes = data.get("transacciones_restantes", 0)  # Obtener las transacciones restantes
        
        mensaje += (
            f"- {data['nombre_usuario']} (ID: {user_id})\n"
            f"  Id Pago: {data['id_pago']}\n"  # Se corrigió las comillas para acceder a 'id_pago'
            f"  Inicio: {inicio}\n"
            f"  Fin: {fin}\n"
            f"  Estado: {estado}\n"
            f"  Transacciones Restantes: {transacciones_restantes}\n\n"  # Añadir el número de transacciones restantes
        )

    await update.message.reply_text(mensaje)


# Función para cargar pagos pendientes
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


async def obtener_opciones_usuario(user_chat_id):
    global subscriptions
    suscripcion = subscriptions.get(user_chat_id)  # Aquí debe estar el diccionario que contiene las suscripciones.
    
    if not suscripcion or suscripcion.get("estado") == "inactiva":
        return []

    if datetime.now().isoformat() > suscripcion["fin"]:
        suscripcion["estado"] = "inactiva"
        await guardar_datos(subscriptions)
        return []

    return suscripcion.get("opciones", [])

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



async def cancelar_suscripcion(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Maneja la cancelación de la selección de suscripción."""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("Has cancelado la selección de la suscripción... ")

async def cancelar_zonas_horarias(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Maneja la cancelación de la selección del timezone."""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("Has cancelado la selección del timezone... ")


async def volver_al_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Volver al menú principal"""
    await mostrar_menu_suscripciones(update, context)


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


def generar_hash(user_id, suscripcion):
    datos = f"{user_id}-{suscripcion}-{time.time()}"
    hash_valor = hashlib.sha256(datos.encode()).hexdigest()
    id_pago = str(int(hash_valor, 16))[:7]
    return id_pago

# Procesar selección de suscripción
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

# Verificar pago
async def verificar_pago(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_chat_id = str(update.effective_user.id)
    args = context.args

    if len(args) != 2:
        await update.message.reply_text("Uso: /verificar_pago <id_pago> <hash_transaccion>")
        return

    id_pago_proporcionado = args[0]
    hash_proporcionado = args[1]
    pagos_pendientes = await cargar_pagos_pendientes()

    # Verificar si el hash ya está registrado
    for pago in pagos_pendientes["pendientes"] + pagos_pendientes.get("verificados", []):
        if pago.get("hash_transaccion") == hash_proporcionado:
            await update.message.reply_text("El hash de transacción ya está asociado con otro pago.")
            return

    for pago in pagos_pendientes["pendientes"]:
        if pago["user_id"] == user_chat_id and pago["id_pago"] == id_pago_proporcionado:
            if pago["estado"] == "verificado":
                await update.message.reply_text("Este pago ya ha sido verificado.")
                return

            # Obtener el monto esperado de la suscripción
            monto_esperado = pago["monto"]

            # Validar transacción en la blockchain
            pago_verificado = await validar_pago_blockchain(hash_proporcionado, monto_esperado )
            if not pago_verificado:
                await update.message.reply_text("La transacción no fue verificada en la blockchain o el monto no coincide. Por favor verifica el hash y el monto.")
                return

            # Activar suscripción
            pago["estado"] = "verificado"
            pago["hash_transaccion"] = hash_proporcionado
            guardar_pagos_pendientes(pagos_pendientes)
            duracion = subscriptions_type[pago["suscripcion"]]["duracion"]
            inicio = datetime.now()
            if duracion == "1 mes":
                fin = inicio + timedelta(days=30)
            elif duracion == "6 meses":
                fin = inicio + timedelta(days=30 * 6)
            elif duracion == "1 año":
                fin = inicio + timedelta(days=365)
            else:
                await update.message.reply_text("Duración de la suscripción no reconocida.")
                return


            # Registrar suscripción activa
            subscriptions[user_chat_id] = {
                "nombre_usuario": update.effective_user.full_name,
                "id_pago": pago["id_pago"],
                "inicio": inicio.isoformat(),
                "fin": fin.isoformat(),
                "limite_transacciones": subscriptions_type[pago["suscripcion"]]["transacciones_maximas"],
                "transacciones_restantes": subscriptions_type[pago["suscripcion"]]["transacciones_maximas"],
                "opciones": subscriptions_type[pago["suscripcion"]]["opciones"]
            }
            await guardar_datos(subscriptions)

            await update.message.reply_text(
                f"Pago verificado. Tu suscripción {pago['suscripcion']} está activa hasta {fin}."
            )

            if not es_administrador(user_chat_id):
                logger.info("Es usuario registrado se procede a actualizar el menú")
                await menu_usuario_registrado(context.bot, user_chat_id)
            elif es_administrador(user_chat_id):
                logger.info("Es administrador se procede a actualizar el menú")
                await menu_usuario_administrador(context, user_chat_id)

            return

    await update.message.reply_text("No se encontró un pago pendiente con ese hash.")

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


async def descontar_transaccion(user_chat_id, numero_transacciones_in=1):
    try:
        # Forzar el user_chat_id como string para evitar errores de tipo
        user_chat_id = str(user_chat_id)

        # Obtener suscripción
        suscripcion = subscriptions.get(user_chat_id)
        if not suscripcion:
            return False, f"❌ No se encontró una suscripción activa para {user_chat_id}."

        # Convertir transacciones restantes a entero de forma segura
        transacciones_restantes_raw = suscripcion.get("transacciones_restantes", 0)
        try:
            transacciones_restantes = int(transacciones_restantes_raw)
        except ValueError:
            return False, f"❌ El valor 'transacciones_restantes' no es numérico: {transacciones_restantes_raw}"

        # Descontar transacción
        transacciones_restantes -= numero_transacciones_in
        suscripcion["transacciones_restantes"] = transacciones_restantes

        # Cambiar estado si está agotado
        if transacciones_restantes <= 0:
            suscripcion["estado"] = "inactiva"

        # Guardar cambios
        await guardar_datos(subscriptions)

        # Actualizar también user_states si existe
        if user_chat_id in user_states:
            user_states[user_chat_id]["numero_transacciones"] = numero_transacciones_in

        return True, f"✅ Transacción exitosa. Te quedan {transacciones_restantes} transacciones."

    except Exception as e:
        return False, f"❌ Error inesperado al descontar transacción: {e}"


# Programa la tarea para que se ejecute todos los días a las 00:00
scheduler = BackgroundScheduler()
def programar_actualizacion_menus(application: Application):
    loop = asyncio.get_running_loop()

    def actualizar():
        asyncio.run_coroutine_threadsafe(actualizar_menus(application), loop)

    scheduler.add_job(
        actualizar,
        IntervalTrigger(minutes=10),  # Se ejecutará cada 10 minutos
    )

    scheduler.start()

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
                                    .dt.tz_localize(pytz.utc)  # Asignar timezone UTC
                                    .dt.tz_convert(timezone_country)  # Convertir al timezone del usuario
                                )
                            if symbol not in cache_noticias:
                                cache_noticias[symbol] = {}
                            cache_noticias[symbol][temporalidad] = df_local.sort_values("publishedDate")
                            logger.info(f"Noticias cargadas en memoria para {symbol} ({temporalidad}).")
                    except Exception as e:
                        logger.info(f"Error al cargar noticias de {symbol} ({temporalidad}): {e}")
    logger.info("Noticias cargadas en memoria.")


async def guardar_noticias_forex():
    """
    Guarda las noticias Forex almacenadas en `cache_noticias` en archivos locales.
    """
    try:
        for symbol, df_local in cache_noticias.items():
            archivo_cache = os.path.join(CARPETA_FOREX_NEWS, f"{symbol}_noticias.json")
            if 'publishedDate' in df_local.columns:
                df_local["publishedDate"] = df_local["publishedDate"].dt.tz_localize(None)  # Eliminar timezone
            async with aiofiles.open(archivo_cache, "w", encoding="utf-8") as file:
                await file.write(df_local.to_json(orient="records", date_format="iso"))
            logger.info(f"Noticias guardadas para {symbol}.")
    except Exception as e:
        logger.info(f"Error al guardar noticias Forex: {e}")

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


async def enviar_mensaje_segmentado(chat_id, mensaje, bot):
    """Envía un mensaje en partes si excede el límite de 4096 caracteres."""
    max_length = 4096  # Límite de Telegram
    partes = [mensaje[i:i + max_length] for i in range(0, len(mensaje), max_length)]
    
    for parte in partes:
        try:
            await bot.send_message(chat_id=chat_id, text=parte)
        except Exception as e:
            print(f"Error al enviar mensaje a {chat_id}: {e}")


async def confirmar_envio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Envía el mensaje a los destinatarios seleccionados y limpia el estado."""
    query = update.callback_query
    await query.answer()

    user_id = str(update.effective_user.id)

    # Obtener los datos guardados en Firestore
    user_ref = db.collection("user_states").document(user_id)
    user_data = user_ref.get()
    datos_usuario = user_data.to_dict() if user_data.exists else {}

    mensaje = datos_usuario.get("mensaje_admin")
    archivos_guardados = datos_usuario.get("archivos_guardados", [])

    # Determinar destinatarios
    destinatarios_tipo = datos_usuario.get("destinatarios", "todos")

    destinatario_manual = datos_usuario.get("destinatario_manual")

    if destinatario_manual:
        destinatarios = [destinatario_manual]
    # Obtener la lista de destinatarios con la nueva lógica corregida
    elif destinatarios_tipo == "todos":
        destinatarios = await cargar_chat_ids()  # Todos los usuarios registrados
    elif destinatarios_tipo == "suscriptores_activos":
        suscripciones = await cargar_datos_subscription_user()
        destinatarios = [
            str(user_id) for user_id in suscripciones.keys()
            if await estado_suscripcion(user_id) == "activa"
        ]  # Solo suscriptores activos
    elif destinatarios_tipo == "suscriptores_inactivos":
        suscripciones = await cargar_datos_subscription_user()
        destinatarios = [
            str(user_id) for user_id in suscripciones.keys()
            if await estado_suscripcion(user_id) in ["inactiva", "expirada", "transacciones_insuficientes"]
        ]  # Solo suscriptores inactivos
    else:
        destinatarios = []

    # Enviar mensaje o documento
    mensaje = datos_usuario.get("mensaje_admin")

    # 🚀 Enviar mensajes y archivos
    for destinatario in destinatarios:
        try:
            texto_enviado = False  # Controla si el texto ya fue enviado
            
            for archivo in archivos_guardados:
                tipo = archivo.get("tipo")  # Extraer el tipo correctamente
                file_id = archivo.get("file_id")  # Extraer file_id correctamente

                if not file_id:  # Si no hay file_id, saltamos este archivo
                    continue

                # Enviar imagen o video con texto como caption si no se ha enviado antes
                if tipo == "imagen":
                    await context.bot.send_photo(chat_id=destinatario, photo=file_id, caption=mensaje if not texto_enviado else None)
                    texto_enviado = True  # Marcar que el mensaje ya fue enviado
                elif tipo == "video":
                    await context.bot.send_video(chat_id=destinatario, video=file_id, caption=mensaje if not texto_enviado else None)
                    texto_enviado = True  # Marcar que el mensaje ya fue enviado
                elif tipo == "documento":
                    await context.bot.send_document(chat_id=destinatario, document=file_id)
            
            # Si no había imagen ni video, enviar el mensaje de texto
            if mensaje and not texto_enviado:
                await context.bot.send_message(chat_id=destinatario, text=mensaje)

        except Exception as e:
            print(f"⚠️ Error al enviar mensaje a {destinatario}: {e}")

    # 🧹 Limpiar datos en Firestore después del envío
    user_ref.set({
        "mensaje_admin": None,
        "archivos_guardados": [],
        "destinatario_manual": None,
        "destinatarios": None,
        "estado": "disponible"  # 👈 ¡Estado cambiado a "disponible" aquí!
    }, merge=True)

    await query.edit_message_text("✅ Mensaje enviado a los destinatarios seleccionados.")
    

async def procesar_envio_mensaje(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Guarda la opción elegida y activa el modo de envío de mensaje."""
    user_chat_id = str(update.effective_chat.id)
    query = update.callback_query
    await query.answer()

    if return_state(user_chat_id) == "en ejecución":
        await context.bot.send_message(
            chat_id=user_chat_id,
            text="Ya tienes un análisis en ejecución. Por favor, espera a que termine."
        )
        return

    destinatarios = query.data.replace("mensaje_", "")

    # Si elige "usuario_especifico", pedimos que ingrese el ID manualmente
    if destinatarios == "usuario_especifico":
        # Guardar estado temporal en Firestore para esperar el ID manualmente
        mark_user_state(user_chat_id, "esperando_id_usuario", destinatarios)
        await query.edit_message_text("🔢 Por favor, ingresa el ID del usuario al que deseas enviar el mensaje:")
        return  # No continuamos hasta recibir el ID

    # Guardar estado y destinatarios en Firestore para envío masivo
    mark_user_state(user_chat_id, "modo_envio_mensaje", destinatarios)
    await query.edit_message_text("✍️ Envía el mensaje o archivo que deseas compartir.")


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
        

@webhook_app.route('/analisis/ejecutar', methods=['POST'])
async def ejecutar_analisis_desde_app():
    chat_id_local = None
    acquired_lock = False
    try:
        # Si ya hay otro procesando, responde 503
        if ocupado_lock.locked():
            return "Estoy ocupado", 503

        # A partir de aquí, tomamos el lock
        await asyncio.to_thread(ocupado_lock.acquire)
        acquired_lock = True

        data = request.json or {}
        chat_id = str(data.get("chat_id") or "")           # preferido
        chat_id_local = chat_id  # para finally
        user_id = str(data.get("user_id") or "")           # alternativo
        activo  = data.get("activo")
        origen  = (data.get("origen") or "telegram").lower()

        # si no viene chat_id, intenta resolverlo con user_id
        if not chat_id and user_id:
            try:
                doc = db.collection('user_ids').document(user_id).get()
                chat_id = (doc.to_dict() or {}).get('telegram_id') or ""
            except Exception:
                chat_id = ""

        if not chat_id or not activo:
            return jsonify({"status": "error", "message": "Faltan parámetros obligatorios"}), 400

        # Validar si está registrado
        chat_ids = await cargar_chat_ids()
        if chat_id not in chat_ids:
            return jsonify({"status": "error", "message": "Usuario no registrado"}), 403

        # Validar suscripción activa o admin
        if await estado_suscripcion(chat_id) != "activa" and not es_administrador(chat_id):
            return jsonify({"status": "error", "message": "Suscripción inactiva o insuficiente"}), 403

        # Validar permisos
        opciones_usuario = await obtener_opciones_usuario(chat_id)
        if not es_administrador(chat_id) and not any(
            opcion in opciones_usuario for opcion in ["analisis basico", "analisis premium", "analisis avanzado"]
        ):
            return jsonify({"status": "error", "message": "No tienes permisos para esta operación"}), 403

        # Validar que no haya un análisis en ejecución
        if return_state(chat_id) == "en ejecución":
            return jsonify({"status": "error", "message": "Ya hay un análisis en ejecución"}), 409

        # -------- leer config enviada desde la app --------
        # Tu app ahora manda 'setup'; dejamos 'operatoria' por compatibilidad.
        raw_cfg = None
        if isinstance(data.get("setup"), dict):
            raw_cfg = data["setup"]
        elif isinstance(data.get("operatoria"), dict):
            op = data["operatoria"]
            raw_cfg = op.get("config", op)

        op_cfg = normalize_operatoria_payload(raw_cfg) if raw_cfg else None

        # Crear ejecución (to_thread para no bloquear)
        exec_id = await asyncio.to_thread(fs_crear_ejecucion, chat_id, [activo], origen, opciones_usuario)

        # Lanzar análisis (sin Telegram)
        dummy_update = type("DummyUpdate", (), {
            "effective_chat": type("DummyChat", (), {"id": chat_id})(),
            "callback_query": None,
            "effective_user": type("DummyUser", (), {
                "first_name": "AppUser",
                "id": chat_id
            })()
        })()

        dummy_context = type("DummyContext", (), {"bot": application.bot})()

        cfg = (db.collection('user_ids')
          .document(user_id)
          .collection('user_config')
          .document('current')
          .get()
          .to_dict() or {})

        task = asyncio.create_task(
            ejecutar_recurrente(
                dummy_context,
                dummy_update,
                activo,
                chat_id,
                opciones_usuario,
                origen="app",
                exec_id=exec_id,
                operatoria_cfg=op_cfg,  # <- pasa la config hacia abajo
                cfg=cfg
            )
        )
        task = asyncio.shield(task)

        urls_generadas = await task
        await asyncio.to_thread(fs_finalizar_ejecucion, exec_id, "completado", {"urls": urls_generadas})

        return jsonify({
            "status": "ok",
            "exec_id": exec_id,
            "message": f"Análisis ejecutado para {activo}",
            "download_urls": _solo_strings_urls(urls_generadas)
        }), 200

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
        # Limpia la cfg por-request si alcanzamos a tener chat_id
        try:
            if chat_id_local:
                clear_current_request_cfg(chat_id_local)
        except Exception:
            pass

        # Libera el lock solo si lo tomamos aquí
        if acquired_lock and ocupado_lock.locked():
            try:
                ocupado_lock.release()
            except Exception:
                pass
    
   
@webhook_app.route('/analisis/imagen', methods=['POST'])
def subir_imagen_y_analizar():
    try:
        if ocupado_lock.locked():
            return "Estoy ocupado", 503
        
        data = request.form
        chat_id = str(data.get("chat_id"))

        if not chat_id or "imagen" not in request.files:
            return jsonify({"status": "error", "message": "Faltan parámetros o archivo"}), 400

        # Verificar permisos
        if chat_id not in cargar_chat_ids():
            return jsonify({"status": "error", "message": "Usuario no registrado"}), 403

        if estado_suscripcion(chat_id) != "activa" and not es_administrador(chat_id):
            return jsonify({"status": "error", "message": "Suscripción inactiva"}), 403

        # Guardar la imagen
        os.makedirs("imagenes", exist_ok=True)
        os.makedirs("procesadas", exist_ok=True)

        imagen = request.files["imagen"]
        ruta_local = f"imagenes/{chat_id}.jpg"
        imagen.save(ruta_local)

        # Establecer estado y llamar directamente a flujo IA
        mark_user_state(chat_id, "esperando_grafico_ia")

        if not es_grafico_de_velas(ruta_local):
            mark_user_state(chat_id, "disponible")
            return jsonify({"status": "error", "message": "❌ No parece ser un gráfico de velas"}), 400

        ruta_salida, texto_resultado = analizar_con_yolo(ruta_local)

         # Validar ruta_salida
        if not os.path.exists(ruta_salida):
            logger.warning(f"[IA] No se generó imagen procesada en: {ruta_salida}")
            mark_user_state(chat_id, "disponible")
            return jsonify({"status": "error", "message": "No se generó imagen procesada"}), 500

        # Enviar resultado a Telegram
        with open(ruta_salida, 'rb') as f:
            application.bot.send_photo(
                chat_id=chat_id,
                photo=InputFile(f),
            )
            application.bot.send_message(chat_id=chat_id, text=texto_resultado)

        if not es_administrador(chat_id):
            success, mensaje = descontar_transaccion(chat_id, 1)
            if not success:
                application.bot.send_message(chat_id=chat_id, text=mensaje)

        mark_user_state(chat_id, "disponible")
        
        if not os.path.exists(ruta_salida):
            return jsonify({
                "status": "error",
                "message": "No se generó la imagen procesada"
            }), 500

        # Codificar la imagen procesada para devolverla a la app
        try:
            with open(ruta_salida, "rb") as f:
                img_base64 = base64.b64encode(f.read()).decode("utf-8")
        except Exception as e:
            logger.error(f"Error al codificar imagen procesada: {e}")
            return jsonify({
                "status": "error",
                "message": "Error al codificar la imagen procesada"
            }), 500

        return jsonify({
            "status": "ok",
            "message": texto_resultado,
            "imagen_base64": img_base64 or None
        }), 200

    except Exception as e:
        logger.exception(f"❌ Error inesperado en subir_imagen_y_analizar: {e}")
        if chat_id:
            mark_user_state(chat_id, "disponible")
        return jsonify({"status": "error", "message": str(e)}), 500
    
    finally:
        # Limpiar archivos temporales
        try:
            if os.path.exists(ruta_local):
                os.remove(ruta_local)
            if os.path.exists(ruta_salida):
                os.remove(ruta_salida)
        except Exception as cleanup_error:
            logger.warning(f"No se pudieron eliminar archivos temporales: {cleanup_error}")

        if ocupado_lock.locked():
            ocupado_lock.release()

# Ruta para el webhook
@webhook_app.route('/webhook', methods=['POST'])
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
def health():
    return {"status": "ok", "instance": socket.gethostname()}
    
@webhook_app.route('/healthz', methods=['GET'])
def health_check():
    return jsonify({"status": "ok"}), 200

@webhook_app.route('/', methods=['GET'])
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
                timeout_graceful_shutdown=900  # Permite apagar tareas con 5 minutos de gracia
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
