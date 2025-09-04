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

print("🔍 GPU habilitada:", torch.cuda.is_available())

# Personalizar los logs
LOGGING_CONFIG["handlers"]["default"] = {
    "level": "INFO",
    "class": "logging.StreamHandler",
    "stream": "ext://sys.stdout",  # Enviar a stdout
}

#log_file = open('output.log', 'w')
#sys.stdout = log_file
#sys.stderr = log_file

# API Key de FMP (Premium)
API_KEY =  os.environ["API_FMP"]

db = firestore.Client()

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

modelo_patrones = YOLO("patrones.pt")
modelo_ruido = YOLO("ruido.pt")

# Lock global para simular carga
ocupado_lock = threading.Lock()

# Inicializar EasyOCR (solo una vez, fuera de la función)
reader = easyocr.Reader(['en'], gpu=True)

def build_object_path(exec_id: str, nombre: str) -> str:
    # Estructura uniforme en el bucket por ejecución
    return f"exec/{exec_id}/{nombre}"

def fs_crear_ejecucion(chat_id: str, activos_solicitados: list[str], origen: str, opciones_usuario: list[str]) -> str:
    exec_id = uuid.uuid4().hex
    doc_ref = db.collection("ejecuciones").document(exec_id)
    doc_ref.set({
        "exec_id": exec_id,
        "chat_id": chat_id,
        "activos_solicitados": activos_solicitados,
        "origen": origen,
        "opciones_usuario": opciones_usuario or [],
        "estado": "en_proceso",
        "archivos": 0,
        "created_at": firestore.SERVER_TIMESTAMP,
        "updated_at": firestore.SERVER_TIMESTAMP,
    })
    return exec_id

def fs_actualizar_ejecucion(exec_id: str, **campos):
    campos["updated_at"] = firestore.SERVER_TIMESTAMP
    db.collection("ejecuciones").document(exec_id).update({k: v for k, v in campos.items() if v is not None})

def fs_finalizar_ejecucion(exec_id: str, estado: str = "completado", resumen: dict | None = None):
    db.collection("ejecuciones").document(exec_id).update({
        "estado": estado,
        "resumen": resumen or {},
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
    db.collection("archivos_generados").add({
        "exec_id": exec_id,
        "chat_id": chat_id,
        "tipo": tipo,
        "nombre": nombre,
        "gcs_path": gcs_path,     # 👉 SOLO REFERENCIA
        "signed_url": signed_url, # opcional (puedes dejar None si firmas on-demand)
        "content_type": content_type,
        "metadata": metadata or {},
        "created_at": firestore.SERVER_TIMESTAMP,
    })
    db.collection("ejecuciones").document(exec_id).update({
        "archivos": firestore.Increment(1),
        "updated_at": firestore.SERVER_TIMESTAMP
    })

async def guardar_json_en_storage_y_registrar(
    *,
    exec_id: str,
    chat_id: str,
    nombre_base: str,                     # sin extensión
    data_records: list[dict],
    subir_a_bucket_y_obtener_url,         # async def que retorna str
    metadata: dict | None = None,
) -> str:
    nombre = f"{nombre_base}.json"
    object_path = build_object_path(exec_id, nombre)
    local_json = f"/tmp/{nombre}"

    # escribe el JSON local (sync está bien; es pequeño)
    with open(local_json, "w", encoding="utf-8") as f:
        json.dump(data_records, f, ensure_ascii=False)

    url_publica = await subir_a_bucket_y_obtener_url(local_json, object_path)

    # sanity check
    if not isinstance(url_publica, str):
        raise RuntimeError(f"subir_a_bucket_y_obtener_url devolvió {type(url_publica)}, esperaba str")

    # fs_* son funciones síncronas → ejecútalas en thread
    await asyncio.to_thread(
        fs_registrar_archivo_generado,
        exec_id,
        chat_id,
        tipo="json",
        nombre=nombre,
        gcs_path=object_path,
        signed_url=url_publica,          # o None si no quieres guardarla
        content_type="application/json",
        metadata=metadata,
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


def definir_window(temporalidad):
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
async def cargar_datos_historicos_inicial():
    """
    Carga inicial de los datos históricos en un diccionario global desde los archivos locales.
    """
    global cache_historicos
    cache_historicos = {}

    for archivo in os.listdir(CARPETA_HISTORICOS):
        if archivo.endswith(".json"):
            try:
                # Extraer el símbolo y la temporalidad del archivo
                partes_archivo = archivo.replace(".json", "").split("_")
                if len(partes_archivo) != 2:
                    logger.info(f"Formato inesperado del archivo: {archivo}. Saltando.")
                    continue

                symbol, temporalidad = partes_archivo

                archivo_cache = os.path.join(CARPETA_HISTORICOS, archivo)
                async with aiofiles.open(archivo_cache, mode="r", encoding="utf-8") as file:
                    contenido = await file.read()
                    data_local = json.loads(contenido)

                if isinstance(data_local, list) and len(data_local) > 0:
                    df_local = pd.DataFrame(data_local)

                    # Validar y procesar la columna 'date'
                    if 'date' in df_local.columns:
                        df_local['date'] = pd.to_datetime(df_local['date'], errors='coerce')
                        df_local = df_local.dropna(subset=['date'])
                        df_local.set_index('date', inplace=True)
                        df_local.index = pd.to_datetime(df_local.index)

                    if df_local.empty or not isinstance(df_local.index, pd.DatetimeIndex):
                        logger.info(f"Advertencia: El DataFrame cargado desde {archivo} es inválido o está vacío.")
                        continue

                    # Guardar en el cache para el símbolo y temporalidad
                    if symbol not in cache_historicos:
                        cache_historicos[symbol] = {}
                    cache_historicos[symbol][temporalidad] = df_local
                    logger.info(f"Cargados datos históricos para {symbol} en {temporalidad}.")
                else:
                    logger.info(f"Archivo {archivo_cache} está vacío o no contiene datos válidos.")
            except Exception as e:
                logger.info(f"Error al cargar datos de {archivo}: {e}")

    logger.info("Datos históricos cargados en memoria.")

def obtener_datos_historicos_fmp(symbol, temporalidad, max_reintentos=5, tiempo_espera_inicial=5):
    try:
        # Intentar obtener datos desde el cache global
        df_local = None
        ultima_fecha = None

        # Validar si el símbolo y la temporalidad están en el caché
        if symbol in cache_historicos and temporalidad in cache_historicos[symbol]:
            df_local = cache_historicos[symbol][temporalidad]

            if df_local is not None and not df_local.empty and 'date' in df_local.columns:
                df_local['date'] = pd.to_datetime(df_local['date'], errors='coerce')
                df_local = df_local.dropna(subset=['date'])
                df_local.set_index('date', inplace=True)

            ultima_fecha = df_local.index.max() if df_local is not None and not df_local.empty else None
            logger.info(f"Última fecha disponible en caché para {symbol} en {temporalidad}: {ultima_fecha}")
        else:
            logger.info(f"No se encontraron datos en caché para {symbol} en {temporalidad}.")

        # Consultar nuevos datos desde la API
        if ultima_fecha:
            ultima_fecha_api = ultima_fecha.strftime("%Y-%m-%d")
            fecha_actual = datetime.now(pytz.utc).strftime("%Y-%m-%d")
            url = f"https://financialmodelingprep.com/api/v3/historical-chart/{temporalidad}/{symbol}?from={ultima_fecha_api}&to={fecha_actual}&apikey={API_KEY}"
        else:
            url = f"https://financialmodelingprep.com/api/v3/historical-chart/{temporalidad}/{symbol}?apikey={API_KEY}"

        reintento = 0
        tiempo_espera = tiempo_espera_inicial

        while reintento < max_reintentos:
            try:
                response = requests.get(url, timeout=timeout_request_global)

                if response.status_code == 200:
                    # Procesar datos si la respuesta es exitosa
                    data_api = response.json()
                    if isinstance(data_api, list) and len(data_api) > 0:
                        df_api = pd.DataFrame(data_api)[['date', 'open', 'high', 'low', 'close', 'volume']]
                        df_api['date'] = pd.to_datetime(df_api['date'], errors='coerce')
                        df_api = df_api.dropna(subset=['date'])
                        df_api.set_index('date', inplace=True)

                        # Filtrar datos nuevos a partir de la última fecha
                        if ultima_fecha:
                            df_api = df_api[df_api.index > ultima_fecha]

                        if df_local is not None and not df_local.empty and not df_api.empty:
                            df_combinado = pd.concat([df_local, df_api]).drop_duplicates().sort_index()
                        elif df_local is not None and not df_local.empty:
                            df_combinado = df_local
                        elif not df_api.empty:
                            df_combinado = df_api
                        else:
                            df_combinado = pd.DataFrame()

                        # Actualizar el cache global
                        if symbol not in cache_historicos:
                            cache_historicos[symbol] = {}
                        cache_historicos[symbol][temporalidad] = df_combinado

                        # Aplicar la conversión de zona horaria solo al valor retornado
                        df_combinado = df_combinado.copy()
                        df_combinado.index = df_combinado.index.tz_localize(pytz.utc).tz_convert(timezone_country)

                        return df_combinado
                    else:
                        logger.info("No se encontraron datos nuevos desde la API.")
                        return df_local if df_local is not None else pd.DataFrame()

                elif response.status_code == 429:
                    # Manejar límite de tasa (429)
                    retry_after = int(response.headers.get("Retry-After", tiempo_espera))
                    logger.info(f"Se excedió el límite de la API. Esperando {retry_after} segundos antes de reintentar.")
                    time.sleep(retry_after)
                    reintento += 1
                else:
                    # Otros errores de la API
                    logger.info(f"Error al consultar la API de FMP: {response.status_code}, URL: {url}")
                    return df_local if df_local is not None else pd.DataFrame()

            except requests.exceptions.RequestException as e:
                logger.info(f"Error de conexión: {e}")
                reintento += 1
                if reintento < max_reintentos:
                    logger.info(f"Reintentando url:{url} en {tiempo_espera} segundos...")
                    time.sleep(tiempo_espera)
                    tiempo_espera *= 2

        logger.info(f"Falló la obtención de datos para {symbol} en temporalidad {temporalidad} después de {max_reintentos} reintentos.")
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
        table = ax.table(cellText=df_parte.values, colLabels=df_parte.columns, cellLoc='center', loc='center')

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
def obtener_valor_realtime_unificado(symbol):
    if "cache_realtime" not in user_states:
        user_states["cache_realtime"] = {}

    if symbol in user_states["cache_realtime"]:
        return user_states["cache_realtime"][symbol]

    valores_realtime = []
    for _ in range(1):
        try:
            data_realtime = obtener_dato_realtime_fmp(symbol)
            if data_realtime is not None and not data_realtime.empty:
                valores_realtime.append(data_realtime.iloc[0]['close'])
        except Exception as e:
            logger.info(f"Error en realtime para {symbol}: {e}")
    if valores_realtime:
        valor_mas_frecuente = Counter(valores_realtime).most_common(1)[0][0]
        user_states["cache_realtime"][symbol] = valor_mas_frecuente
        return valor_mas_frecuente

    return None

# Implementación de hilos para optimizar las solicitudes de datos
#@profile
def obtener_datos_con_hilos(symbol, temporalidad):
    if "cache_realtime" not in user_states:
        user_states["cache_realtime"] = {}

    try:
        # Obtener datos históricos usando `await`
        df_historico = obtener_datos_historicos_fmp(symbol, temporalidad)

        # Crear DataFrame para datos en tiempo real
        df_realtime = pd.DataFrame([{
            'close': user_states["cache_realtime"].get(symbol, None)
        }])

        if df_historico.empty:
            logger.info(f"Datos históricos no disponibles para {symbol} en temporalidad {temporalidad}")
            return pd.DataFrame()

        # Ordenar y combinar datos
        df_historico = df_historico.sort_index()
        df_realtime_vela = actualizar_ultima_vela_con_realtime(df_historico, df_realtime, symbol, temporalidad)
        df_realtime_vela = df_realtime_vela.sort_index()

        return df_realtime_vela

    except Exception as e:
        logger.info(f"Se cayo en obtener_datos_con_hilos {symbol} en temporalidad {temporalidad} el error es: {e}")
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
def ajustar_probabilidad_tecnica(df, temporalidad, window):

    # Verificar que el DataFrame tiene al menos dos filas
    if len(df) < 2:
        logger.info("No hay suficientes datos para calcular la probabilidad técnica.")
        return 50  # Devolver un valor base (por ejemplo, 50) si no hay suficientes datos

    ultima_fila = df.iloc[-1]
    penultima_fila = df.iloc[-2]

    probabilidad_tecnica = 50  # Valor base

    # Cálculo de soportes y resistencias dentro de la función
    soporte_nivel_1 = df['low'].rolling(window=window).min().iloc[-1]
    resistencia_nivel_1 = df['high'].rolling(window=window).max().iloc[-1]

    # Verificar señales individuales
    senal_macd = False
    senal_rsi = False
    senal_estocastico = False

    # Ponderación basada en si el MACD está por encima o por debajo de la señal
    if ultima_fila['macd'] > ultima_fila['signal']:
        if ultima_fila['macd'] < penultima_fila['macd']:  # MACD retrocediendo
            probabilidad_tecnica += 5  # Aumento moderado por señal débil
        else:
            probabilidad_tecnica += 10  # Ponderación más controlada para señal de compra
        senal_macd = True  # Señal de compra por MACD
    else:
        if ultima_fila['macd'] > penultima_fila['macd']:  # MACD acercándose a cruce alcista
            probabilidad_tecnica -= 5  # Reducción moderada por señal débil
        else:
            probabilidad_tecnica -= 10  # Ponderación más controlada para señal de venta

    # Cruce reciente del MACD con la señal
    if penultima_fila['macd'] < penultima_fila['signal'] and ultima_fila['macd'] > ultima_fila['signal']:
        probabilidad_tecnica += 7  # Cruce alcista reciente
    elif penultima_fila['macd'] > penultima_fila['signal'] and ultima_fila['macd'] < ultima_fila['signal']:
        probabilidad_tecnica -= 7  # Cruce bajista reciente

    # Ajustes por RSI
    if ultima_fila['rsi'] < 25:  # Zona de sobreventa
        probabilidad_tecnica += 3  # Señal alcista
        senal_rsi = True  # Señal de compra por RSI
    elif ultima_fila['rsi'] > 75:  # Zona de sobrecompra
        probabilidad_tecnica -= 3  # Señal bajista

    # Ajustes por Estocástico
    if ultima_fila['%K'] > ultima_fila['%D'] and ultima_fila['%K'] < 25:  # Señal alcista en sobreventa
        probabilidad_tecnica += 3
        senal_estocastico = True  # Señal de compra por Estocástico
    elif ultima_fila['%K'] < ultima_fila['%D'] and ultima_fila['%K'] > 75:  # Señal bajista en sobrecompra
        probabilidad_tecnica -= 3

    # Ajuste por divergencias
    if df['divergencia_macd'].tail(3).sum() > 1 or df['divergencia_rsi'].tail(3).sum() > 1:
        probabilidad_tecnica += 10  # Aumento por divergencias confirmadas

    # Filtrar señales débiles si el ATR es bajo (baja volatilidad)
    if not pd.isna(ultima_fila['ATR']) and ultima_fila['ATR'] < df['ATR'].rolling(window).mean().iloc[-1] * 0.8:
        probabilidad_tecnica -= 5  # Disminuir probabilidad en mercados de baja volatilidad

    # **Ajustes por niveles de soporte y resistencia**
    # Comparar el precio actual con los niveles de soporte y resistencia
    if abs(ultima_fila['close'] - soporte_nivel_1) < (ultima_fila['ATR'] * 0.5):
        probabilidad_tecnica += 3  # Señal alcista reforzada cerca del soporte
    elif abs(ultima_fila['close'] - resistencia_nivel_1) < (ultima_fila['ATR'] * 0.5):
        probabilidad_tecnica -= 3  # Señal bajista reforzada cerca de la resistencia

    # **Bonificación extra si las señales de compra coinciden en MACD, RSI y Estocástico**
    if senal_macd and senal_rsi and senal_estocastico:
        probabilidad_tecnica += 10  # Bonificación más controlada si todas las señales son alcistas

    # Limitar la probabilidad técnica entre 0 y 100
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

#@profile
def ajustar_probabilidad_fundamental(probabilidad_exito, df_eventos, symbol, temporalidad, fecha_inicio=None, fecha_fin=None):
    global cache_noticias

    # Análisis de noticias
    if symbol not in cache_noticias:
        df_noticias =  obtener_noticias(symbol, fecha_inicio, fecha_fin)
    else:
        df_noticias = cache_noticias[symbol]
        
    impacto_noticias =  calcular_impacto_noticias(df_noticias)

    # Ajustar probabilidad con base en el impacto de noticias
    if impacto_noticias is not None:
        probabilidad_exito += impacto_noticias * 0.1  # Ajustar peso según relevancia


    # Validación: DataFrame vacío o columnas críticas ausentes
    columnas_necesarias = {'date', 'actual', 'estimate', 'previous', 'currency', 'event', 'impact'}
    if df_eventos.empty or not columnas_necesarias.issubset(df_eventos.columns):
        logging.info(f"No se encontraron eventos válidos para {symbol}. Usando probabilidad por defecto.")
        return limitar_probabilidad(50)
        
    if df_eventos.empty:
        logging.info(f"No se encontraron eventos pasados para {symbol}. Usando probabilidades por defecto.")
        return limitar_probabilidad(50)
    else:
        # Asegurarse de que la columna 'date' esté en formato datetime
        df_eventos['date'] = pd.to_datetime(df_eventos['date'], errors='coerce')

        # Convertir las columnas 'actual', 'estimate' y 'previous' a tipo float
        df_eventos['actual'] = pd.to_numeric(df_eventos['actual'].apply(limpiar_valores), errors='coerce')
        df_eventos['estimate'] = pd.to_numeric(df_eventos['estimate'].apply(limpiar_valores), errors='coerce')
        df_eventos['previous'] = pd.to_numeric(df_eventos['previous'].apply(limpiar_valores), errors='coerce')

        divisa_principal, divisa_secundaria = obtener_monedas(symbol)

        df_eventos_temp = df_eventos[(df_eventos['currency'].isin([divisa_principal, divisa_secundaria]))]

        # Verificar si el DataFrame no está vacío antes de continuar
        if df_eventos_temp.empty:
            logger.info(f"No se encontraron eventos pasados para el símbolo {symbol}.")
            return probabilidad_exito  # Retornar la probabilidad actual si no hay eventos
        
        # Ordenar por fecha en orden ascendente
        df_eventos_temp = df_eventos_temp.sort_values(by='date', ascending=True)

        # Obtener el evento más reciente
        ahora = datetime.now(timezone_country)
        evento_reciente = df_eventos_temp.iloc[-1]
        
        
        # Ponderar más las noticias recientes
        tiempo_transcurrido = (ahora - evento_reciente['date']).total_seconds() / 60
        ponderacion_reciente = 1.5 if tiempo_transcurrido < 15 else 1.0
        
        # Ordenar por fecha en orden ascendente (del más antiguo al más reciente)
        df_eventos_temp = df_eventos_temp.sort_values(by='date', ascending=True)

        # Obtener el tiempo máximo de diferencia entre el evento más antiguo y ahora (para normalizar)
        tiempo_maximo = (ahora - df_eventos_temp['date'].min()).total_seconds()
        

        if df_eventos_temp.empty:
            logger.info(f"No se encontraron eventos relevantes para {symbol}.")
            return limitar_probabilidad(probabilidad_exito)

    
        for idx, evento in df_eventos_temp.iterrows():

            ajuste = 0  # Variable para ajustar la probabilidad

            # Calcular el tiempo transcurrido desde el evento hasta ahora
            tiempo_transcurrido = (ahora - evento['date']).total_seconds()

            # Normalizar la ponderación (los eventos más recientes tendrán ponderación más alta)
            ponderacion_antiguedad = 1 - (tiempo_transcurrido / tiempo_maximo)

            if evento['actual'] is not None and evento['estimate'] is not None and evento['previous'] is not None:
                categoria = detectar_categoria(evento['event'])

                # Ajuste basado en el análisis de sentimiento
                texto_evento = evento.get('event', '')
                
                # Ajuste según el impacto del evento
                if evento['impact'] == 'High':
                    multiplicador_impacto = 2
                elif evento['impact'] == 'Medium':
                    multiplicador_impacto = 1.5
                else:
                    multiplicador_impacto = 1
                
                # Ponderar más la última noticia si es la más reciente
                if evento['date'] == evento_reciente['date']:
                    multiplicador_impacto *= ponderacion_reciente
                
                # Aplicar la ponderación de antigüedad
                multiplicador_impacto *= ponderacion_antiguedad

                # Petróleo
                if symbol in ['WTI', 'BRENT']:
                    if categoria == 'Crude Oil Inventories':
                        if pd.isna(evento['estimate']):
                            if evento['actual'] < evento['previous']:
                                ajuste = 0.3 * multiplicador_impacto
                            else:
                                ajuste = -0.3 * multiplicador_impacto
                        else:
                            if evento['actual'] < evento['estimate'] and evento['actual'] < evento['previous']:
                                ajuste = 0.3 * multiplicador_impacto
                            elif evento['actual'] < evento['estimate']:
                                ajuste = 0.2 * multiplicador_impacto
                            else:
                                ajuste = -0.3 * multiplicador_impacto
                    elif categoria in ['OPEC Meeting', 'OPEC Decision']:
                        if 'cut' in evento['event'].lower():
                            ajuste = 0.3 * multiplicador_impacto
                        elif 'increase' in evento['event'].lower():
                            ajuste = -0.3 * multiplicador_impacto

                # Soja
                elif symbol == 'SOYB':
                    if categoria == 'Crop Production Report':
                        if pd.isna(evento['estimate']):
                            if evento['actual'] < evento['previous']:
                                ajuste = 0.3 * multiplicador_impacto
                            else:
                                ajuste = -0.3 * multiplicador_impacto
                        else:
                            if evento['actual'] < evento['estimate'] and evento['actual'] < evento['previous']:
                                ajuste = 0.3 * multiplicador_impacto
                            elif evento['actual'] < evento['estimate']:
                                ajuste = 0.2 * multiplicador_impacto
                            else:
                                ajuste = -0.3 * multiplicador_impacto
                    elif categoria == 'Weather Report':
                        if 'drought' in evento['event'].lower() or 'storm' in evento['event'].lower():
                            ajuste = 0.3 * multiplicador_impacto
                        else:
                            ajuste = -0.2 * multiplicador_impacto
                            
                # Lógica para ajustar según si es divisa principal o secundaria
                if evento['currency'] == divisa_secundaria:
                    multiplicador_impacto = multiplicador_impacto*-1

                if categoria is not None:
                    if categoria == 'Unemployment Rate':
                        if pd.isna(evento['estimate']):
                            if evento['actual'] < evento['previous']:
                                ajuste = 0.3 * multiplicador_impacto
                            else:
                                ajuste = -0.2 * multiplicador_impacto
                        else:
                            if evento['actual'] < evento['estimate'] and evento['actual'] < evento['previous']:
                                ajuste = 0.3 * multiplicador_impacto
                            elif evento['actual'] < evento['estimate']:
                                ajuste = 0.2 * multiplicador_impacto
                            else:
                                ajuste = -0.2 * multiplicador_impacto

                    elif categoria == 'Employment Report':
                        if pd.isna(evento['estimate']):
                            if evento['actual'] > evento['previous']:
                                ajuste = 0.3 * multiplicador_impacto
                            else:
                                ajuste = -0.2 * multiplicador_impacto
                        else:
                            if evento['actual'] > evento['estimate'] and evento['actual'] > evento['previous']:
                                ajuste = 0.3 * multiplicador_impacto
                            elif evento['actual'] > evento['estimate']:
                                ajuste = 0.2 * multiplicador_impacto
                            else:
                                ajuste = -0.2 * multiplicador_impacto  # Si los datos son peores que el estimado y el anterior, es negativo

                    elif categoria == 'Inflation Rate':
                        if pd.isna(evento['estimate']):
                            if evento['actual'] < evento['previous']:
                                ajuste = 0.3 * multiplicador_impacto
                            else:
                                ajuste = -0.3 * multiplicador_impacto
                        else:
                            if evento['actual'] < evento['estimate'] and evento['actual'] < evento['previous']:
                                ajuste = 0.3 * multiplicador_impacto
                            elif evento['actual'] < evento['estimate']:
                                ajuste = 0.2 * multiplicador_impacto
                            else:
                                ajuste = -0.3 * multiplicador_impacto

                    elif categoria == 'GDP':
                        if pd.isna(evento['estimate']):
                            if evento['actual'] > evento['previous']:
                                ajuste = 0.3 * multiplicador_impacto
                            else:
                                ajuste = -0.3 * multiplicador_impacto
                        else:
                            if evento['actual'] > evento['estimate'] and evento['actual'] > evento['previous']:
                                ajuste = 0.3 * multiplicador_impacto
                            elif evento['actual'] > evento['estimate']:
                                ajuste = 0.2 * multiplicador_impacto
                            else:
                                ajuste = -0.2 * multiplicador_impacto

                    elif categoria == 'Retail Sales':
                        if pd.isna(evento['estimate']):
                            if evento['actual'] > evento['previous']:
                                ajuste = 0.3 * multiplicador_impacto
                            else:
                                ajuste = -0.2 * multiplicador_impacto
                        else:
                            if evento['actual'] > evento['estimate'] and evento['actual'] > evento['previous']:
                                ajuste = 0.3 * multiplicador_impacto
                            elif evento['actual'] > evento['estimate']:
                                ajuste = 0.2 * multiplicador_impacto
                            else:
                                ajuste = -0.2 * multiplicador_impacto

                    elif categoria == 'Interest Rate':
                        if pd.isna(evento['estimate']):
                            if evento['actual'] < evento['previous']:
                                ajuste = 0.3 * multiplicador_impacto
                            else:
                                ajuste = -0.3 * multiplicador_impacto
                        else:
                            if evento['actual'] < evento['estimate'] and evento['actual'] < evento['previous']:
                                ajuste = 0.3 * multiplicador_impacto
                            elif evento['actual'] < evento['estimate']:
                                ajuste = 0.2 * multiplicador_impacto
                            else:
                                ajuste = -0.3 * multiplicador_impacto
                    else:
                        if pd.isna(evento['estimate']):
                            if evento['actual'] > evento['previous']:
                                ajuste = 0.25 * multiplicador_impacto
                            else:
                                ajuste = -0.25 * multiplicador_impacto
                        else:
                            # Comparar los valores actual, estimado y anterior para decidir el ajuste
                            if evento['actual'] > evento['estimate'] and evento['actual'] > evento['previous']:
                                ajuste = 0.25 * multiplicador_impacto  # Ajuste positivo mayor si actual es mejor que estimate y previous
                            elif evento['actual'] > evento['estimate']:
                                ajuste = 0.15 * multiplicador_impacto  # Ajuste positivo si actual es mejor que estimate pero no previous
                            elif evento['actual'] > evento['previous']:
                                ajuste = 0.1 * multiplicador_impacto  # Ajuste menor si actual es mejor que previous pero no estimate
                            else:
                                ajuste = -0.25 * multiplicador_impacto  # Ajuste negativo si actual es peor que estimate y previous
                else:
                    if pd.isna(evento['estimate']):
                            if evento['actual'] > evento['previous']:
                                ajuste = 0.25 * multiplicador_impacto
                            else:
                                ajuste = -0.25 * multiplicador_impacto
                    else:
                        # Comparar los valores actual, estimado y anterior para decidir el ajuste
                        if evento['actual'] > evento['estimate'] and evento['actual'] > evento['previous']:
                            ajuste = 0.25 * multiplicador_impacto  # Ajuste positivo mayor si actual es mejor que estimate y previous
                        elif evento['actual'] > evento['estimate']:
                            ajuste = 0.15 * multiplicador_impacto  # Ajuste positivo si actual es mejor que estimate pero no previous
                        elif evento['actual'] > evento['previous']:
                            ajuste = 0.1 * multiplicador_impacto  # Ajuste menor si actual es mejor que previous pero no estimate
                        else:
                            ajuste = -0.25 * multiplicador_impacto  # Ajuste negativo si actual es peor que estimate y previous

            # Ajustar la probabilidad de éxito con el nuevo ajuste
            probabilidad_exito += ajuste
    
    return  limitar_probabilidad(probabilidad_exito) 

# Función para calcular la probabilidad general ponderando más la probabilidad fundamental
def calcular_probabilidad_general(probabilidad_tecnica, probabilidad_fundamental):
    # Ponderar más la probabilidad fundamental (50%) y menos la probabilidad técnica (50%)
    return (probabilidad_tecnica * 0.5) + (probabilidad_fundamental * 0.5)

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
    
def detectar_todos_patrones_velas(df: pd.DataFrame, window: int = 10) -> list:

    patrones_encontrados = []

    for i in range(window, len(df)):
        sub_df = df.iloc[i - window:i]
        patrones = []

        if len(sub_df) >= 7:
            highs = sub_df['high'].values[-7:]
            o = sub_df['open'].values[-7:]
            c = sub_df['close'].values[-7:]
            l = sub_df['low'].values[-7:]
            
            
            h1, h2, h3, cabeza, h4, h5, h6 = highs

            # Validar posición central de la cabeza
            i_cabeza = np.argmax(highs)
            hombros_simetricos = abs(h1 - h6) / max(h1, h6, 1e-6) < 0.15 and abs(h2 - h5) / max(h2, h5, 1e-6) < 0.15

            # Validar cuerpo de la vela de la cabeza (evitar mecha dominante)
            cuerpo = abs(c[3] - o[3])
            mecha_sup = highs[3] - max(c[3], o[3])
            mecha_inf = min(c[3], o[3]) - l[3]
            cabeza_definida = cuerpo > 0 and mecha_sup < cuerpo * 1.5 and mecha_inf < cuerpo * 1.5

            # Verifica si ya hay Martillo Invertido u Hombre Colgado detectado
            patrones_en_ventana = [p[2] for p in patrones]


            conflictos_similares = [
                "Martillo", "Martillo Invertido", "Hombre Colgado", "Estrella Fugaz",
                "Pinzas de Techo", "Pinzas de Suelo", "Envolvente Alcista",
                "Envolvente Bajista", "Harami Bajista"
            ]
            conflicto = any(p in patrones_en_ventana for p in conflictos_similares)

            if i_cabeza == 3 and hombros_simetricos and cabeza > h2 and h4 < cabeza and not conflicto and cabeza_definida:
                patrones.append((window - 7, window, "Hombro Cabeza Hombro"))

            l1, l2, l3, cabeza_inv, l4, l5, l6 = l
            # La cabeza debe ser la más baja y estar centrada
            i_cabeza = np.argmin(l)
            hombros_simetricos = abs(l1 - l6) / max(l1, l6, 1e-6) < 0.15 and abs(l2 - l5) / max(l2, l5, 1e-6) < 0.15
            estructura_valida = i_cabeza == 3 and hombros_simetricos and cabeza_inv < l2 and l4 > cabeza_inv

            if estructura_valida and not conflicto:
                patrones.append((window - 7, window, "Hombro Cabeza Hombro Invertido"))

        if len(sub_df) >= 4 and i + 1 < len(df):  # Verificamos también que existe una vela posterior para confirmar

            patrones_en_ventana = [p[2] for p in patrones]
            conflicto_mayor = any(p in patrones_en_ventana for p in ["Hombro Cabeza Hombro", "Hombro Cabeza Hombro Invertido"])

            o = sub_df['open'].iloc[-1]
            c = sub_df['close'].iloc[-1]
            h = sub_df['high'].iloc[-1]
            l = sub_df['low'].iloc[-1]
            cuerpo = abs(c - o)
            mecha_sup = h - max(c, o)
            mecha_inf = min(c, o) - l

            cierre_previos = sub_df['close'].iloc[-3:-1]
            tendencia_bajista = all(cierre_previos.diff().dropna() < 0)
            tendencia_alcista = all(cierre_previos.diff().dropna() > 0)

            # Vela de confirmación (fuera del sub_df)
            vela_conf = df.iloc[i]
            o_conf = vela_conf['open']
            c_conf = vela_conf['close']

            # Martillo (alcista con confirmación)
            if tendencia_bajista and cuerpo > 0 and mecha_inf >= cuerpo * 2 and mecha_sup <= cuerpo * 0.3:
                if c_conf > c and not conflicto_mayor:
                    patrones.append((window - 1, window + 1, "Martillo"))

            # Martillo Invertido (alcista con confirmación)
            if tendencia_bajista and cuerpo > 0 and mecha_sup >= cuerpo * 2 and mecha_inf <= 0.3 * cuerpo:
                if c_conf > c and not conflicto_mayor:
                    patrones.append((window - 1, window + 1, "Martillo Invertido"))

            # Hombre Colgado (bajista con confirmación)
            if tendencia_alcista and cuerpo > 0 and mecha_inf >= cuerpo * 2 and mecha_sup <= 0.3 * cuerpo:
                if c_conf < c and not conflicto_mayor:
                    patrones.append((window - 1, window + 1, "Hombre Colgado"))

            # Estrella Fugaz (bajista con confirmación)
            if tendencia_alcista and cuerpo > 0 and mecha_sup >= cuerpo * 2 and mecha_inf <= 0.3 * cuerpo:
                if c_conf < c and not conflicto_mayor:
                    patrones.append((window - 1, window + 1, "Estrella Fugaz"))

        if len(sub_df) >= 3:

            patrones_en_ventana = [p[2] for p in patrones]
            conflicto_mayor = any(p in patrones_en_ventana for p in ["Hombro Cabeza Hombro", "Hombro Cabeza Hombro Invertido"])

            o = sub_df['open']
            c = sub_df['close']
            l = sub_df['low']
            h = sub_df['high']
            cuerpo = abs(c - o)

            # Tendencias previas (2 velas hacia arriba o abajo)
            cierres_previos = sub_df['close'].iloc[-4:-1]
            tendencia_bajista = all(cierres_previos.diff().dropna() < 0)
            tendencia_alcista = all(cierres_previos.diff().dropna() > 0)

            # Tres Cuervos Negros
            if (
                c.iloc[-3] < o.iloc[-3] and
                c.iloc[-2] < o.iloc[-2] and
                c.iloc[-1] < o.iloc[-1] and
                o.iloc[-2] < o.iloc[-3] and o.iloc[-2] > c.iloc[-3] and
                o.iloc[-1] < o.iloc[-2] and o.iloc[-1] > c.iloc[-2] and
                c.iloc[-1] < c.iloc[-2] and c.iloc[-2] < c.iloc[-3] and
                (h.iloc[-3] - l.iloc[-3]) * 0.1 > (h.iloc[-3] - c.iloc[-3]) and
                (h.iloc[-2] - l.iloc[-2]) * 0.1 > (h.iloc[-2] - c.iloc[-2]) and
                (h.iloc[-1] - l.iloc[-1]) * 0.1 > (h.iloc[-1] - c.iloc[-1])
            ):
                patrones.append((window - 3, window, "Tres Cuervos Negros"))

            # Tres Soldados Blancos
            if (
                c.iloc[-3] > o.iloc[-3] and
                c.iloc[-2] > o.iloc[-2] and
                c.iloc[-1] > o.iloc[-1] and
                o.iloc[-2] >= o.iloc[-3] and o.iloc[-2] <= c.iloc[-3] and
                o.iloc[-1] >= o.iloc[-2] and o.iloc[-1] <= c.iloc[-2] and
                c.iloc[-2] > c.iloc[-3] and
                c.iloc[-1] > c.iloc[-2] and
                (c.iloc[-3] - l.iloc[-3]) < (c.iloc[-3] - o.iloc[-3]) * 0.5 and
                (c.iloc[-2] - l.iloc[-2]) < (c.iloc[-2] - o.iloc[-2]) * 0.5 and
                (c.iloc[-1] - l.iloc[-1]) < (c.iloc[-1] - o.iloc[-1]) * 0.5
            ):
                patrones.append((window - 3, window, "Tres Soldados Blancos"))

            
            # --- Estrella del Amanecer ---
            if tendencia_bajista:
                vela1_bajista = c.shift(2).iloc[-1] < o.shift(2).iloc[-1]
                vela2_pequena = abs(c.shift(1).iloc[-1] - o.shift(1).iloc[-1]) <= (h.shift(1).iloc[-1] - l.shift(1).iloc[-1]) * 0.3
                vela3_alcista = c.iloc[-1] > o.iloc[-1]
                cierre3_por_encima_mitad1 = c.iloc[-1] > (o.shift(2).iloc[-1] + c.shift(2).iloc[-1]) / 2
                if vela1_bajista and vela2_pequena and vela3_alcista and cierre3_por_encima_mitad1:
                    if not conflicto_mayor:
                        patrones.append((window - 3, window, "Estrella del Amanecer"))

            # --- Estrella de la Noche ---
            if tendencia_alcista:
                vela1_alcista = c.shift(2).iloc[-1] > o.shift(2).iloc[-1]
                vela2_pequena = abs(c.shift(1).iloc[-1] - o.shift(1).iloc[-1]) <= (h.shift(1).iloc[-1] - l.shift(1).iloc[-1]) * 0.3
                vela3_bajista = c.iloc[-1] < o.iloc[-1]
                cierre3_por_debajo_mitad1 = c.iloc[-1] < (o.shift(2).iloc[-1] + c.shift(2).iloc[-1]) / 2
                if vela1_alcista and vela2_pequena and vela3_bajista and cierre3_por_debajo_mitad1:
                    if not conflicto_mayor:
                        patrones.append((window - 3, window, "Estrella de la Noche"))

        if len(sub_df) >= 2:

            patrones_en_ventana = [p[2] for p in patrones]
            conflicto_mayor = any(p in patrones_en_ventana for p in ["Hombro Cabeza Hombro", "Hombro Cabeza Hombro Invertido"])

            # --- Pinzas de Techo ---
            if tendencia_alcista:
                max1, max2 = h.iloc[-1], h.iloc[-2]
                techo_casi_igual = abs(max1 - max2) <= (h.iloc[-2] - l.iloc[-2]) * 0.05
                vela1_alcista = c.iloc[-2] > o.iloc[-2]
                vela2_bajista = c.iloc[-1] < o.iloc[-1]
                if techo_casi_igual and vela1_alcista and vela2_bajista:
                    if not conflicto_mayor:
                        patrones.append((window - 2, window, "Pinzas de Techo"))

            # --- Pinzas de Suelo ---
            if tendencia_bajista:
                min1, min2 = l.iloc[-1], l.iloc[-2]
                suelo_casi_igual = abs(min1 - min2) <= (h.iloc[-2] - l.iloc[-2]) * 0.05
                vela1_bajista = c.iloc[-2] < o.iloc[-2]
                vela2_alcista = c.iloc[-1] > o.iloc[-1]
                if suelo_casi_igual and vela1_bajista and vela2_alcista:
                    if not conflicto_mayor:
                        patrones.append((window - 2, window, "Pinzas de Suelo"))

            # --- Envolvente Alcista ---
            if tendencia_bajista:
                cuerpo1 = abs(c.shift(1).iloc[-1] - o.shift(1).iloc[-1])
                cuerpo2 = abs(c.iloc[-1] - o.iloc[-1])
                envolvente = (c.shift(1).iloc[-1] < o.shift(1).iloc[-1]) and \
                            (o.iloc[-1] < c.shift(1).iloc[-1]) and (c.iloc[-1] > o.shift(1).iloc[-1]) and \
                            (cuerpo2 > cuerpo1)
                if envolvente:
                    if not conflicto_mayor:
                        patrones.append((window - 2, window, "Envolvente Alcista"))

            # --- Envolvente Bajista ---
            if tendencia_alcista:
                cuerpo1 = abs(c.shift(1).iloc[-1] - o.shift(1).iloc[-1])
                cuerpo2 = abs(c.iloc[-1] - o.iloc[-1])
                envolvente = (c.shift(1).iloc[-1] > o.shift(1).iloc[-1]) and \
                            (o.iloc[-1] > c.shift(1).iloc[-1]) and (c.iloc[-1] < o.shift(1).iloc[-1]) and \
                            (cuerpo2 > cuerpo1)
                if envolvente:
                    if not conflicto_mayor:
                        patrones.append((window - 2, window, "Envolvente Bajista"))

            # --- Harami Bajista ---
            if tendencia_alcista:
                vela1_alcista = c.shift(1).iloc[-1] > o.shift(1).iloc[-1]
                vela2_dentro = (o.iloc[-1] > o.shift(1).iloc[-1]) and (c.iloc[-1] < c.shift(1).iloc[-1])
                cuerpo2_mas_pequeño = abs(c.iloc[-1] - o.iloc[-1]) < abs(c.shift(1).iloc[-1] - o.shift(1).iloc[-1])
                if vela1_alcista and vela2_dentro and cuerpo2_mas_pequeño:
                    if not conflicto_mayor:
                        patrones.append((window - 2, window, "Harami Bajista"))



        if len(sub_df) >= window:
            bandera_alcista = (sub_df['high'] > sub_df['high'].rolling(window).max().shift(1)) & \
                              (sub_df['low'] > sub_df['low'].rolling(window).min().shift(1)) & \
                              (sub_df['close'] > sub_df['open'])
            if bandera_alcista.iloc[-1]:
                patrones.append((window - 1, window, "Bandera Alcista"))

            bandera_bajista = (sub_df['low'] < sub_df['low'].rolling(window).min().shift(1)) & \
                              (sub_df['high'] < sub_df['high'].rolling(window).max().shift(1)) & \
                              (sub_df['close'] < sub_df['open'])
            if bandera_bajista.iloc[-1]:
                patrones.append((window - 1, window, "Bandera Bajista"))

        # Evitar duplicados cercanos
        for start_idx_local, end_idx_local, patron in patrones:
            global_start_idx = i - window + start_idx_local
            global_end_idx = i - window + end_idx_local
            ya_existe = any(
                prev_patron == patron and abs(prev_start - global_start_idx) <= 2
                for prev_start, prev_end, prev_patron in patrones_encontrados
            )
            if not ya_existe:
                patrones_encontrados.append((global_start_idx, global_end_idx, patron))

    return patrones_encontrados

# Función para detectar patrones de velas japonesas mejorada con Estrella del Amanecer, Estrella de la Noche y Martillo Invertido
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

#@profile
def ajustar_window_dinamico_optimizado(
    df, symbol, temporalidad, precio_actual, max_incremento=5, min_factor=2, max_factor=5, min_levels=2, n_jobs=-1
):
    # Obtener la ventana inicial
    window = min(definir_window(temporalidad), len(df))
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

# Función para calcular puntos de entrada ajustando las probabilidades
#@profile
def calcular_entradas(df, df_eventos, symbol, temporalidad, user_chat_id):
    estado_usuario = obtener_estado_usuario(user_chat_id)
    soportes_resistencias_cache = estado_usuario["soportes_resistencias_cache"]
    window = min(definir_window(temporalidad), len(df))

    # Detección de patrones de velas japonesas
    patrones_detectados =  detectar_patrones_velas(df, window)

    print(f"MTORO - Paso exitosamente la detección de patrones: {patrones_detectados}")
    
    # Predicción de precios futuros con ARIMA
    predicciones_arima =  predecir_arima(df, temporalidad, symbol)
    
    predicciones_media_movil =  predecir_media_movil(df, window)
    
    # Simulación de Monte Carlo para probabilidad de alza/baja
    probabilidad_alza, probabilidad_baja =  simulacion_monte_carlo(df, temporalidad,num_simulaciones=100,num_dias=5,seed=42)
    probabilidad_alza = probabilidad_alza if probabilidad_alza is not None else 50
    probabilidad_baja = probabilidad_baja if probabilidad_baja is not None else 50
    
    precio_actual = df['close'].iloc[-1]
   
    # Calcular soportes y resistencias dinámicos de esta temporalidad
    df, soportes_dinamicos, resistencias_dinamicas =  ajustar_window_dinamico_optimizado(df, symbol, temporalidad, precio_actual, max_incremento=5, min_factor=2, max_factor=4, min_levels=2, n_jobs=-1)
    if symbol not in soportes_resistencias_cache:
        soportes_resistencias_cache[symbol] = {}

    # Agregar soportes y resistencias de esta temporalidad al caché global
    if temporalidad not in soportes_resistencias_cache[symbol]:
        # Si la temporalidad no está en el caché, agregar directamente
        soportes_resistencias_cache[symbol][temporalidad] = {
            "soportes": soportes_dinamicos,
            "resistencias": resistencias_dinamicas,
        }
    else:
        # Si la temporalidad ya existe, combinar o actualizar
        soportes_resistencias_cache[symbol][temporalidad]['soportes'] = list(
            set(soportes_resistencias_cache[symbol][temporalidad]['soportes'] + soportes_dinamicos)
        )
        soportes_resistencias_cache[symbol][temporalidad]['resistencias'] = list(
            set(soportes_resistencias_cache[symbol][temporalidad]['resistencias'] + resistencias_dinamicas)
        )
    niveles_clave =  obtener_niveles_clave(df, soportes_dinamicos, resistencias_dinamicas, soportes_resistencias_cache, symbol, temporalidad, umbral_atr=2.0, max_niveles=2)
    en_rango =  detectar_rango_zigzag(df,ventana_rebotes=140,tolerancia_pct=0.002, min_rebotes=3)

    ATR = df['ATR'].iloc[-1]

    # Calcular probabilidades técnica y fundamental
    probabilidad_tecnica = round(ajustar_probabilidad_tecnica(df,temporalidad,window), 2)
    fecha_inicio = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')
    fecha_fin = datetime.now().strftime('%Y-%m-%d')
    probabilidad_fundamental =  ajustar_probabilidad_fundamental(50, df_eventos, symbol, temporalidad, fecha_inicio=fecha_inicio, fecha_fin=fecha_fin)
    if probabilidad_fundamental is None:
        probabilidad_fundamental = 50
    else:
        probabilidad_fundamental = round(probabilidad_fundamental, 2)
    # Calcular probabilidad general con la nueva ponderación

    probabilidad_general =  calcular_probabilidad_general(probabilidad_tecnica, probabilidad_fundamental)
    if probabilidad_general is None:
        probabilidad_general = 50  # Valor predeterminado para evitar errores
    else:
        probabilidad_general = round(probabilidad_general, 2)

    # Verificar zona de no trading
    zona_no_trading = verificar_zona_no_trading(df, window)
    #logger.info(f"Zona de no trading: {zona_no_trading}")
    
    zona_sobreventa = verificar_zona_sobreventa(df, window)
    #logger.info(f"Zona de sobreventa: {zona_sobreventa}")
    
    zona_sobrecompra = verificar_zona_sobrecompra(df, window)
    #logger.info(f"Zona de sobrecompra: {zona_sobrecompra}")
    
    # Determinación del tipo de operación utilizando las funciones auxiliares
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
        zona_no_trading
    )

    if tipo_operacion in señales_compra or (tipo_operacion == "Neutral" and en_rango['estructura_tendencia'] == "alcista" or (tipo_operacion == "Neutral" and en_rango['estructura_tendencia'] == "indefinida")):
        precio_entrada = (niveles_clave['resistencia_nivel_1'] + niveles_clave['soporte_nivel_1']) / 2 if niveles_clave['resistencia_nivel_1'] and niveles_clave['soporte_nivel_1'] else precio_actual
        take_profit = precio_entrada + ATR * 1.5 if not np.isnan(ATR) else np.nan
        stop_loss = precio_entrada - ATR * 1.5 if not np.isnan(ATR) else np.nan
        
        if not (stop_loss < precio_entrada < take_profit):
            logger.warning(f"Valores incorrectos en EURJPY {temporalidad} (compra): SL={stop_loss}, Entrada={precio_entrada}, TP={take_profit}")
            stop_loss, take_profit = np.nan, np.nan
    
    elif tipo_operacion in señales_venta or (tipo_operacion == "Neutral" and en_rango['estructura_tendencia'] == "bajista"):
        precio_entrada = (niveles_clave['resistencia_nivel_1'] + niveles_clave['soporte_nivel_1']) / 2 if niveles_clave['resistencia_nivel_1'] and niveles_clave['soporte_nivel_1'] else precio_actual
        take_profit = precio_entrada - ATR * 1.5 if not np.isnan(ATR) else np.nan
        stop_loss = precio_entrada + ATR * 1.5 if not np.isnan(ATR) else np.nan
        
        if not (take_profit < precio_entrada < stop_loss):
            logger.warning(f"Valores incorrectos en EURJPY {temporalidad} (venta): TP={take_profit}, Entrada={precio_entrada}, SL={stop_loss}")
            stop_loss, take_profit = np.nan, np.nan

    # Verificar si el precio actual está cerca de soportes o resistencias
    def esta_cerca(precio, nivel, umbral_cercania=0.01):
        if nivel is None:
            return False
        
        return abs(precio - nivel) / precio <= umbral_cercania

    cerca_de_soporte_resistencia = (
        "Cerca de Soporte Nivel 2" if esta_cerca(precio_actual, niveles_clave['soporte_nivel_2']) else
        "Cerca de Soporte Nivel 1" if esta_cerca(precio_actual, niveles_clave['soporte_nivel_1']) else
        "Cerca de Resistencia Nivel 1" if esta_cerca(precio_actual, niveles_clave['resistencia_nivel_1']) else
        "Cerca de Resistencia Nivel 2" if esta_cerca(precio_actual, niveles_clave['resistencia_nivel_2']) else
        "No Cerca"
    )


    # Flag de oportunidad: cuando la probabilidad general es mayor de 53 (compra) o menor de 47 (venta), y no está en zona de no trading
    #flag_oportunidad = True if  (probabilidad_baja > 53 or probabilidad_alza > 53) and not zona_no_trading else False
    flag_oportunidad = False
    if not zona_no_trading:
        if probabilidad_general > 53 and not zona_sobrecompra:  # Compra
            flag_oportunidad = True
        elif probabilidad_general < 47 and not zona_sobreventa:  # Venta
            flag_oportunidad = True


    # Agregar predicción de tendencia en tiempo real
    tendencia_predicha = predecir_tendencia_en_tiempo_real(df, temporalidad)
    
    return {
        "patrones_detectados": patrones_detectados,
        "predicciones_arima": predicciones_arima,
        "predicciones_media_movil": predicciones_media_movil,
        "probabilidad_alza": probabilidad_alza,
        "probabilidad_baja": probabilidad_baja,
        "macd_cruce": df['macd_cruce'].iloc[-1],
        "macd_cerca_de_cruzar": df['macd_cerca_de_cruzar'].iloc[-1],
        "bollinger_signal": df['bollinger_signal'].iloc[-1],
        "bollinger_upper": df['bollinger_upper'].iloc[-1],
        "bollinger_lower": df['bollinger_lower'].iloc[-1],
        "tendencia_predicha": tendencia_predicha,
        "ultimo_valor": precio_actual,
        "soporte_nivel_2": niveles_clave['soporte_nivel_2'],
        "soporte_nivel_1": niveles_clave['soporte_nivel_1'],
        "resistencia_nivel_1": niveles_clave['resistencia_nivel_1'],
        "resistencia_nivel_2": niveles_clave['resistencia_nivel_2'],
        "apalancamiento_compra_nivel_1": niveles_clave["multiplicador"]["apalancamiento_compra_nivel_1"],
        "apalancamiento_compra_nivel_2": niveles_clave["multiplicador"]["apalancamiento_compra_nivel_2"],
        "apalancamiento_venta_nivel_1": niveles_clave["multiplicador"]["apalancamiento_venta_nivel_1"],
        "apalancamiento_venta_nivel_2": niveles_clave["multiplicador"]["apalancamiento_venta_nivel_2"],
        "precio_entrada": precio_entrada,
        "take_profit": take_profit,
        "stop_loss": stop_loss,
        "es_rango_repetitivo": en_rango["es_rango_repetitivo"],
        "estructura_tendencia": en_rango['estructura_tendencia'],
        "rebotes": en_rango["rebotes"],
        "rango_dinamico": en_rango["rango_dinamico"],
		"soportes_alcanzados": niveles_clave['niveles_importantes_soportes'], 
        "resistencias_alcanzadas": niveles_clave['niveles_importantes_resistencias'],
        "cerca_de_soporte_resistencia": cerca_de_soporte_resistencia,
        "soportes_importantes_alcanzados": niveles_clave['soportes_confirmados_orden'],
        "resistencias_importantes_alcanzadas": niveles_clave['resistencias_confirmadas_orden'],
        "niveles_confirmados_orden_toques_all": niveles_clave['niveles_confirmados_orden_toques_all'],
        "niveles_confirmados_orden_nivel_all": niveles_clave['niveles_confirmados_orden_nivel_all'],
        "niveles_confirmados_orden_nivel_reduced": niveles_clave['niveles_confirmados_orden_nivel_reduced'],
        "probabilidad_tecnica": probabilidad_tecnica,
		"probabilidad_tecnica": probabilidad_tecnica,
        "probabilidad_fundamental": probabilidad_fundamental,
        "probabilidad_general": probabilidad_general,
        "tipo_operacion": tipo_operacion,
        "flag_oportunidad": flag_oportunidad,
        "zona_no_trading": zona_no_trading,
        "zona_sobreventa": zona_sobreventa,
        "zona_sobrecompra": zona_sobrecompra
    }

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

# Función para ajustar dinámicamente el ancho de las columnas basado en el contenido
#@profile
def df_a_imagen(df, max_filas=50):
    """
    Genera imágenes a partir de un DataFrame con ajustes dinámicos de ancho de columna y contenido.
    """
    if df.empty:
        logger.info("El DataFrame está vacío, no se puede generar la imagen.")
        return None

    # Dividir el DataFrame en partes si es necesario
    num_filas = len(df)
    buffers = []

    for inicio in range(0, num_filas, max_filas):
        df_parte = df.iloc[inicio:inicio + max_filas]

        # Calcular ancho de cada columna basado en el contenido y las cabeceras
        max_col_widths = [
            max(len(str(item)) for item in df_parte[col].tolist() + [col]) + 2
            for col in df_parte.columns
        ]

        # Configurar tamaño de la figura dinámicamente
        fig_width = sum(max_col_widths) * 0.1  # Ajustar el multiplicador según sea necesario
        fig, ax = plt.subplots(figsize=(min(fig_width, 20), min(len(df_parte) * 0.4, 15)))

        ax.axis('tight')
        ax.axis('off')

        # Crear la tabla
        table = ax.table(cellText=df_parte.values, colLabels=df_parte.columns, cellLoc='center', loc='center')

        # Ajustar escalas y fuente
        table.auto_set_font_size(False)
        table.set_fontsize(10)  # Ajustar tamaño de fuente
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
            plt.savefig(buf, format='png', bbox_inches='tight', dpi=150)  # DPI para mejor claridad
            buf.seek(0)
            plt.close(fig)
            buffers.append(buf)
        except Exception as e:
            logger.info(f"Error al generar la imagen: {e}")
            plt.close(fig)
            return None

    # Retorna una lista de buffers o un único buffer
    return buffers if len(buffers) > 1 else buffers[0]
    
# Función para enviar la imagen a Telegram
#@profile
async def enviar_imagen_a_todos(df, context, moneda_filtro=None, user_chat_id=None, intentos=3):
    imagen = df_a_imagen(df)

    chat_ids = [user_chat_id] if user_chat_id else clientes_chat_ids

    # Verificar si la imagen es None o el buffer está vacío
    if isinstance(imagen, list):  # Si es una lista de imágenes
            total_partes = len(imagen)
            for indice, img in enumerate(imagen, start=1):
                if img.getbuffer().nbytes > 0:
                    await context.bot.send_photo(chat_id=user_chat_id, photo=img, caption=f"Oportunidades relacionadas a los activos seleccionados. Parte {indice} de {total_partes}")
    elif imagen is None or imagen.getbuffer().nbytes == 0:
        for chat_id in chat_ids:
            await context.bot.send_message(chat_id=chat_id, text="No se pudo generar la imagen. El archivo está vacío.")
    else:
        for chat_id in chat_ids:
            for intento in range(intentos):
                try:
                    #await context.bot.send_photo(chat_id=chat_id, photo=imagen, caption=f"Oportunidades de {moneda_filtro}")
                    await context.bot.send_photo(chat_id=chat_id, photo=imagen, caption="Oportunidades relacionadas a los activos seleccionados.")
                    imagen.seek(0)  # Restablecer el buffer
                    break
                except TimedOut:
                    logger.info(f"Intento {intento + 1} fallido. Reintentando...")
                    await asyncio.sleep(2)  # Espera antes de reintentar
                except telegram.error.BadRequest as e:
                    logger.info(f"Error al enviar la imagen a {chat_id}: {e}")

#@profile
def generar_imagen_eventos_oportunidades(df_eventos, divisas_oportunidades, max_filas=50):
    """
    Genera imágenes para eventos económicos filtrados por divisas con ajustes dinámicos de ancho de columna y contenido.
    """
    # Verifica si el DataFrame está vacío
    if df_eventos.empty:
        logger.info("El DataFrame de eventos está vacío.")
        return None

    # Verifica si la columna 'date' existe en el DataFrame
    if 'date' not in df_eventos.columns:
        logger.info("La columna 'date' no existe en el DataFrame de eventos.")
        return None

    # Asegura que las fechas tengan información de zona horaria
    if df_eventos['date'].dt.tz is None:
        df_eventos['date'] = df_eventos['date'].dt.tz_localize('UTC')
    df_eventos['date'] = df_eventos['date'].dt.tz_convert(timezone_country)

    # Filtrar los eventos por las divisas dadas
    df_eventos_filtrados = df_eventos[df_eventos['currency'].isin(divisas_oportunidades)]
    df_eventos_filtrados = df_eventos_filtrados.sort_values(by='date', ascending=True)

    # Formatear las fechas para mostrarlas en la tabla
    df_eventos_filtrados['date'] = df_eventos_filtrados['date'].dt.strftime('%Y-%m-%d %H:%M:%S')

    # Verificar si hay eventos para mostrar
    if df_eventos_filtrados.empty:
        logger.info(f"No hay eventos relacionados a las divisas de las oportunidades.")
        return None

    # Manejar múltiples partes del DataFrame si excede el límite de filas
    num_filas = len(df_eventos_filtrados)
    buffers = []

    for inicio in range(0, num_filas, max_filas):
        df_parte = df_eventos_filtrados.iloc[inicio:inicio + max_filas]

        # Calcular ancho de cada columna basado en el contenido y las cabeceras
        max_col_widths = [
            max(len(str(item)) for item in df_parte[col].tolist() + [col]) + 2
            for col in df_parte.columns
        ]

        # Configurar tamaño de la figura dinámicamente
        fig_width = sum(max_col_widths) * 0.1  # Ajustar el multiplicador según sea necesario
        fig, ax = plt.subplots(figsize=(min(fig_width, 20), min(len(df_parte) * 0.4, 15)))

        ax.axis('tight')
        ax.axis('off')

        # Crear la tabla
        table = ax.table(cellText=df_parte.values, colLabels=df_parte.columns, cellLoc='center', loc='center')

        # Ajustar escalas y fuente
        table.auto_set_font_size(False)
        table.set_fontsize(10)
        table.scale(1.0, 0.7)

        # Ajustar ancho de las columnas
        for i, col_width in enumerate(max_col_widths):
            for (row, col), cell in table.get_celld().items():
                if col == i:  # Ajusta solo las celdas de la columna actual
                    cell.set_width(col_width * 0.01)

        # Ajustar márgenes
        fig.subplots_adjust(left=0.01, right=0.99, top=0.99, bottom=0.01)

        # Guardar la imagen en memoria
        buf = BytesIO()
        try:
            plt.savefig(buf, format='png', bbox_inches='tight', dpi=150)
            buf.seek(0)
            plt.close(fig)
            buffers.append(buf)
        except Exception as e:
            logger.info(f"Error al generar la imagen: {e}")
            plt.close(fig)
            return None

    # Retorna una lista de buffers si hay múltiples partes o un único buffer si solo hay una
    return buffers if len(buffers) > 1 else buffers[0]

        
# Función para enviar la imagen de los eventos relacionados a las oportunidades
#@profile
async def enviar_imagen_eventos_oportunidades(df_eventos, divisas_oportunidades, context, user_chat_id=None, intentos=3):
    # Generar la imagen de los eventos relacionados a las oportunidades
    imagen_eventos = generar_imagen_eventos_oportunidades(df_eventos, divisas_oportunidades)

    chat_ids = [user_chat_id] if user_chat_id else clientes_chat_ids

    # Verificar si la imagen es None o el buffer está vacío
    if isinstance(imagen_eventos, list):  # Si es una lista de imágenes
            total_partes = len(imagen_eventos)
            for indice, img in enumerate(imagen_eventos, start=1):
                if img.getbuffer().nbytes > 0:
                    await context.bot.send_photo(chat_id=user_chat_id, photo=img,caption=f"Eventos relacionados a las oportunidades. Parte {indice} de {total_partes}")
    elif imagen_eventos is None or imagen_eventos.getbuffer().nbytes == 0:
        logger.info(f"No se pudo generar la imagen de eventos relacionados a las oportunidades. El archivo está vacío.")
    else:
        for chat_id in chat_ids:
            for intento in range(intentos):
                try:
                    await context.bot.send_photo(chat_id=chat_id, photo=imagen_eventos, caption="Eventos relacionados a las oportunidades.")
                    imagen_eventos.seek(0)  # Restablecer el buffer para el siguiente envío
                    break
                except TimedOut:
                    logger.info(f"Intento {intento + 1} fallido. Reintentando...")
                    await asyncio.sleep(2)
                except telegram.error.BadRequest as e:
                    logger.info(f"Error al enviar la imagen de eventos a {chat_id}: {e}")
                await asyncio.sleep(2)

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
def calcular_ponderacion_incremental_por_divisa(df):
    
    peso_base = 1

    # Crear una nueva columna para ponderación incremental
    df['Ponderacion Incremental'] = 0

    # Iterar por cada activo (divisa)
    for activo, data in df.groupby('Activo'):
        ponderacion = 0
        # Iterar por cada fila del activo
        for index, row in data.iterrows():
            tipo_operacion = row['Tipo de Operacion']

            # Verificar señales de compra y aplicar ponderación incremental
            if tipo_operacion in señales_compra:
                temporalidad = row['Temporalidad']
                if temporalidad in temporalidades:
                    i = temporalidades.index(temporalidad)
                    ponderacion += peso_base * (2 ** i)

            # Verificar señales de venta y aplicar ponderación incremental
            elif tipo_operacion in señales_venta:
                temporalidad = row['Temporalidad']
                if temporalidad in temporalidades:
                    i = temporalidades.index(temporalidad)
                    ponderacion -= peso_base * (2 ** i)

        # Asignar la ponderación incremental al DataFrame
        df.loc[df['Activo'] == activo, 'Ponderacion Incremental'] = ponderacion

    return df

#@profile
def calcular_ponderacion(row):
    ponderacion = 0

    # Ponderar por Probabilidad General
    probabilidad_general = row.get('Probabilidad General (%)', None)
    if probabilidad_general is not None:
        if probabilidad_general > 60:
            ponderacion += 2
        elif probabilidad_general < 40:
            ponderacion -= 2
        elif 47 <= probabilidad_general <= 53:
            ponderacion -= 1

    # Ponderar por Patrones de Velas
    patrones = row['Patrones Detectados']
    if patrones is None:
        patrones = []  # Inicializar con una lista vacía

    if any(pat in patrones for pat in patrones_alcistas):
        ponderacion += 3
    elif any(pat in patrones for pat in patrones_bajistas):
        ponderacion -= 3

    # Concordancia entre Probabilidad Técnica y Fundamental
    probabilidad_tecnica = row.get('Probabilidad Tecnica (%)', None)
    probabilidad_fundamental = row.get('Probabilidad Fundamental (%)', None)
    if probabilidad_tecnica is None:
        probabilidad_tecnica = 50  # Valor neutral por defecto
    if probabilidad_fundamental is None:
        probabilidad_fundamental = 50  # Valor neutral por defecto

    if probabilidad_tecnica > 60 and probabilidad_fundamental > 60:
        ponderacion += 2
    elif probabilidad_tecnica < 40 and probabilidad_fundamental < 40:
        ponderacion -= 2

    # Proximidad a Soportes y Resistencias
    precio_actual = row.get('Ultimo Valor', None)
    soporte_nivel_1 = row.get('Soporte Nivel 1', None)
    resistencia_nivel_1 = row.get('Resistencia Nivel 1', None)
    soporte_nivel_2 = row.get('Soporte Nivel 2', None)
    resistencia_nivel_2 = row.get('Resistencia Nivel 2', None)

    # Validar que no sean None antes de operar
    if precio_actual is None or soporte_nivel_1 is None:
        logger.info(f"Valores no válidos: precio_actual={precio_actual}, soporte_nivel_1={soporte_nivel_1}")
        return 0  # Asignar una ponderación neutral en caso de datos faltantes

    try: 
        distancia_soporte = abs(precio_actual - soporte_nivel_1) / soporte_nivel_1
    except ZeroDivisionError:
        logger.info(f"Error de división por cero con soporte_nivel_1={soporte_nivel_1}")
        distancia_soporte = float('inf')  # Manejar el caso de división por cero

    try:    
        distancia_resistencia = abs(resistencia_nivel_1 - precio_actual) / resistencia_nivel_1
    except ZeroDivisionError:
        logger.info(f"Error de división por cero con resistencia_superior={resistencia_nivel_1}")
        resistencia_nivel_1 = float('inf')  # Manejar el caso de división por cero

    if distancia_soporte < 0.01:
        ponderacion += 2
    if distancia_resistencia < 0.01:
        ponderacion -= 2

    # Cerca del soporte actual para compras
    if precio_actual <= soporte_nivel_1 * 1.01:
        ponderacion += 2

    # Cerca de la resistencia actual para ventas
    elif precio_actual >= resistencia_nivel_1 * 0.99:
        ponderacion -= 2

    # Cerca del soporte nivel 2 (rebote alcista)
    if soporte_nivel_2 is not None and precio_actual <= soporte_nivel_2 * 1.01:
        ponderacion += 1

    # Cerca de la resistencia nivel 2 (rebote bajista)
    if resistencia_nivel_2 is not None and precio_actual >= resistencia_nivel_2 * 0.99:
        ponderacion -= 1

    # MACD
    macd_cruce = row['Cruce MACD']
    if macd_cruce == 'Cruce Alcista':
        ponderacion += 1
    elif macd_cruce == 'Cruce Bajista':
        ponderacion -= 1

    # Bandas de Bollinger
    bollinger_upper = row['bollinger_upper']
    bollinger_lower = row['bollinger_lower']

    if pd.notna(bollinger_upper) and pd.notna(bollinger_lower):
        if precio_actual < bollinger_lower:  # Señal de sobreventa (compra)
            ponderacion += 2
        elif precio_actual > bollinger_upper:  # Señal de sobrecompra (venta)
            ponderacion -= 2

    # Ajustar por Tendencia Predicha
    tendencia_predicha = row.get('Tendencia Predicha', 'Neutral')
    if tendencia_predicha == 'Alcista':
        ponderacion += 2
    elif tendencia_predicha == 'Bajista':
        ponderacion -= 2

    # Ajuste según la señal detectada
    señal_detectada = row.get('Tipo de Operacion', 'Neutral')
    if señal_detectada in señales_compra:
        ponderacion += 3
    elif señal_detectada in señales_venta:
        ponderacion -= 3

    # Ajustar por Ponderación Incremental
    ponderacion_incremental = row.get('Ponderacion Incremental', 0)
    if ponderacion_incremental > 10:
        ponderacion += 3
    elif ponderacion_incremental < -10:
        ponderacion -= 3

    # **Ajustar pesos según temporalidad**
    temporalidad = row.get('Temporalidad', '').lower()
    if '1min' in temporalidad or '5min' in temporalidad:  # Temporalidades muy cortas
        ponderacion *= 1.1  # Aumentar peso en un 10%
    elif '15min' in temporalidad or '30min' in temporalidad:  # Temporalidades cortas a medias
        ponderacion *= 1.05  # Aumentar peso en un 5%
    elif '1hour' in temporalidad or '4hour' in temporalidad:  # Temporalidades medias
        ponderacion *= 1  # Mantener peso igual
    elif '1day' in temporalidad or '1week' in temporalidad:  # Temporalidades largas
        ponderacion *= 0.9  # Reducir peso en un 10%

    # Normalizar ponderación final
    ponderacion = max(min(ponderacion, 20), -20)

    return float(ponderacion)

#@profile
def procesar_simbolo_temporalidad(symbol, temporalidad, df_eventos, user_chat_id, context):

    try :
        df_combinado = obtener_datos_con_hilos(symbol, temporalidad)
        if df_combinado.empty:
            logger.info(f"No hay datos combinados para {symbol} en temporalidad {temporalidad}.")
            return None
    except Exception as e:
        logger.info(f"Se cayo en obtener_datos_con_hilos {symbol} en temporalidad {temporalidad} el error es: {e}")

    
    try :
        df_indicadores = calcular_indicadores(df_combinado, temporalidad)
        if df_indicadores.empty:
            logger.info(f"No hay indicadores calculados para {symbol} en temporalidad {temporalidad}.")
            return None
    except Exception as e:
        logger.info(f"Se cayo en calcular_indicadores {symbol} en temporalidad {temporalidad} el error es: {e}")
    

    try :
        entradas = {}
        entradas = calcular_entradas(df_indicadores, df_eventos, symbol, temporalidad, user_chat_id)
        if not entradas:
            logger.info(f"No se pudieron calcular entradas para {symbol} en temporalidad {temporalidad}.")
            return None
        # Aquí se genera el gráfico y se envía
        # imagen = graficar_serie_temporal(df_indicadores, symbol, temporalidad)
        
    except Exception as e:
        logger.info(f"Se cayo en calcular_entradas {symbol} en temporalidad {temporalidad} el error es: {e}")
    

    # Devolver los resultados estructurados
    return {
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
async def ejecutar_analisis_con_hilos(df_eventos, activos_filtrados, user_chat_id, context):
    resultados = []
    errores = []

    resultadosReal = []
    erroresReal = []

    loop = asyncio.get_running_loop()

    # ----------- Realtime ----------
    realtime_tasks = [
        loop.run_in_executor(None, obtener_valor_realtime_unificado, symbol)
        for symbol in activos_filtrados
    ]

    realtime_results = await asyncio.gather(*realtime_tasks, return_exceptions=True)

    for idx, result in enumerate(realtime_results):
        if isinstance(result, Exception):
            logger.info(f"Error en realtime para símbolo {activos_filtrados[idx]}: {result}")
            erroresReal.append(str(result))
        elif result is not None:
            resultadosReal.append(result)
        else:
            logger.info(f"Resultado de realtime vacío para símbolo {activos_filtrados[idx]}.")

    if not resultadosReal or erroresReal:
        logger.info("No se pudieron obtener resultados de realtime debido a errores.")
        logger.info("Errores registrados:")
        for error in erroresReal:
            logger.info(f" - {error}")

    # ----------- Análisis principal ----------
    analisis_tasks = []
    task_to_symbol_temporalidad = []

    for symbol in activos_filtrados:
        for temporalidad in temporalidades:
            analisis_tasks.append(
                loop.run_in_executor(None, procesar_simbolo_temporalidad, symbol, temporalidad, df_eventos, user_chat_id, context)
            )
            task_to_symbol_temporalidad.append((symbol, temporalidad))

    analisis_results = await asyncio.gather(*analisis_tasks, return_exceptions=True)

    for idx, result in enumerate(analisis_results):
        symbol, temporalidad = task_to_symbol_temporalidad[idx]
        if isinstance(result, Exception):
            logger.info(f"Error en análisis para símbolo {symbol} y temporalidad {temporalidad}: {result}")
            errores.append(str(result))
        elif result is not None:
            resultados.append(result)
        else:
            logger.info(f"Resultado vacío para símbolo {symbol} y temporalidad {temporalidad}.")

    if not resultados or errores:
        logger.info("No se pudieron obtener resultados debido a errores.")
        logger.info("Errores registrados:")
        for error in errores:
            logger.info(f" - {error}")

    return resultados

# Función para procesar el resultado de cada análisis
#@profile
async def procesar_resultado(resultados, df_eventos, context, update, moneda_filtro, user_chat_id=None, opciones_usuario=[], origen="telegram", exec_id: str | None = None):

    urls_generadas = []

    # --- JSON completo (antes de filtrar) ---
    df_resultados = pd.DataFrame(resultados)
    if origen == "app" and exec_id:
        df_json_records = df_resultados.where(pd.notnull(df_resultados), None).to_dict("records")
        json_url = await guardar_json_en_storage_y_registrar(
            exec_id=exec_id,
            chat_id=user_chat_id,
            nombre_base=f"{moneda_filtro.upper()}_resultados_completos",
            data_records=df_json_records,
            subir_a_bucket_y_obtener_url=subir_a_bucket_y_obtener_url,
            metadata={"moneda_filtro": moneda_filtro, "scope": "completo"},
        )
        if json_url:
            urls_generadas.append(json_url)
            
    # Convertir la lista de resultados en un DataFrame
    df_resultados = pd.DataFrame(resultados)

    # Aplicar la función de ponderación incremental al DataFrame `df_filtrado` en memoria
    df_resultados = df_resultados.copy()
    df_resultados = calcular_ponderacion_incremental_por_divisa(df_resultados)
    
    # Aplicar la función de ponderación al DataFrame `df_filtrado` en memoria
    df_resultados = df_resultados.copy()
    df_resultados['Ponderacion'] = df_resultados.apply(lambda row: calcular_ponderacion(row), axis=1).astype(float)

    df_resultados.pop('bollinger_lower')
    df_resultados.pop('bollinger_upper')
    # Ordenar el DataFrame por la columna de ponderación
    df_resultados = df_resultados.copy()
    df_resultados_ordenado = df_resultados.sort_values(by='Ponderacion', ascending=False)

    # Verificar si el usuario tiene acceso a "análisis avanzado"
    if "analisis avanzado" in opciones_usuario and not es_administrador(user_chat_id):
        logger.info("El usuario tiene acceso a análisis avanzado.")
    elif "analisis premium" in opciones_usuario and not es_administrador(user_chat_id):
        df_resultados_ordenado.pop("Soportes Importantes Alcanzados")
        df_resultados_ordenado.pop("Resistencias Importantes Alcanzadas")
        df_resultados_ordenado.pop("Niveles Confirmados (Nivel)")
        logger.info("El usuario tiene acceso a análisis premium.")
    elif "analisis basico" in opciones_usuario and not es_administrador(user_chat_id):
        df_resultados_ordenado.pop("Patrones Detectados")
        df_resultados_ordenado.pop("Soportes Alcanzados")
        df_resultados_ordenado.pop("Resistencias Alcanzadas")
        df_resultados_ordenado.pop("Cerca de Soporte Resistencia")
        df_resultados_ordenado.pop("Es Rango Repetitivo")
        df_resultados_ordenado.pop("Estructura Tendencia")
        df_resultados_ordenado.pop("Rebotes")
        df_resultados_ordenado.pop("Rango Dinamico")
        df_resultados_ordenado.pop("Soportes Importantes Alcanzados")
        df_resultados_ordenado.pop("Resistencias Importantes Alcanzadas")
        df_resultados_ordenado.pop("Niveles Confirmados (Nivel)")
        df_resultados_ordenado.pop("Probabilidad Alza (Montecarlo)")
        df_resultados_ordenado.pop("Probabilidad Baja (Montecarlo)")
        logger.info("El usuario tiene acceso a análisis basico.")    
    
    # Filtrar solo las oportunidades donde flag_oportunidad es True, Zona No Trading es False y el tipo de operación no es "Neutral"
    df_filtrado = df_resultados_ordenado[
        (df_resultados_ordenado['Oportunidad'] == True) &
        (df_resultados_ordenado['Zona No Trading'] == False) 
    ]

    # --- JSON oportunidades ---
    if origen == "app" and exec_id:
        df_filtrado_records = df_filtrado.where(pd.notnull(df_filtrado), None).to_dict("records")
        opp_json_url = await guardar_json_en_storage_y_registrar(
            exec_id=exec_id,
            chat_id=user_chat_id,
            nombre_base=f"{moneda_filtro.upper()}_oportunidades",
            data_records=df_filtrado_records,
            subir_a_bucket_y_obtener_url=subir_a_bucket_y_obtener_url,
            metadata={"moneda_filtro": moneda_filtro, "scope": "oportunidades"},
        )
        if opp_json_url:
            urls_generadas.append(opp_json_url)

    df_resultadosToImage = pd.DataFrame(df_filtrado)

    # Extraer divisas de los símbolos de las oportunidades
    divisas_oportunidades = df_filtrado['Activo'].str[:3].unique()

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
                 await enviar_imagen_a_todos(df_filtradoToImage, context, moneda_filtro, user_chat_id)
            else:
                logger.info(f"El DataFrame df_filtradoToImage está vacío. No se enviará la imagen.")

            user_states[user_chat_id]["imagenes_oportunidades_enviadas"] = True

            if user_states[user_chat_id]["imagenes_oportunidades_enviadas"]:  
                if not df_eventos.empty and divisas_oportunidades is not None and len(divisas_oportunidades) > 0:
                    # Aquí irían las tareas que deseas ejecutar si la validación es exitosa
                    await enviar_imagen_eventos_oportunidades(df_eventos, divisas_oportunidades, context, user_chat_id)
                else:
                    logger.info("El DataFrame df_eventos está vacío o divisas_oportunidades no contiene elementos válidos.")
                
                # Marcar imágenes como enviadas
                user_states[user_chat_id]["imagenes_eventos_enviadas"] = True

            if not es_administrador(user_chat_id):
                success, mensaje = await descontar_transaccion(user_chat_id, user_states[user_chat_id]["numero_transacciones"])
                if not success:
                    await update.message.reply_text(mensaje)
            
    logger.info(f"Devolviendo URLs al frontend: {urls_generadas}")
    
    return urls_generadas


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
async def ejecutar_recurrente(context, update, moneda_filtro, user_chat_id=None, opciones_usuario=[], origen="telegram", exec_id: str | None = None):
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

        resultados = await ejecutar_analisis_con_hilos(df_eventos, activos_filtrados, user_chat_id, context)

        if not resultados:
            await context.bot.send_message(
                chat_id=user_chat_id,
                text="El análisis no produjo resultados. Verifique los datos y vuelva a intentarlo."
            )
            return

        url_generadas = await procesar_resultado(resultados, df_eventos, context, update, moneda_filtro, user_chat_id, opciones_usuario, origen, exec_id=exec_id)

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
    try:
        if ocupado_lock.locked():
            return "Estoy ocupado", 503
    
        data = request.json
        chat_id = str(data.get("chat_id"))
        activo = data.get("activo")
        origen = data.get("origen", "telegram").lower()

        if not chat_id or not activo:
            return jsonify({"status": "error", "message": "Faltan parámetros obligatorios"}), 400

        # Validar si está registrado
        chat_ids = await cargar_chat_ids()
        if chat_id not in chat_ids:
            return jsonify({"status": "error", "message": "Usuario no registrado"}), 403

        # Validar suscripción activa o si es admin
        if await estado_suscripcion(chat_id) != "activa" and not es_administrador(chat_id):
            return jsonify({"status": "error", "message": "Suscripción inactiva o insuficiente"}), 403

        # Validar opciones habilitadas
        opciones_usuario = await obtener_opciones_usuario(chat_id)
        if not es_administrador(chat_id) and not any(
            opcion in opciones_usuario for opcion in ["analisis basico", "analisis premium", "analisis avanzado"]
        ):
            return jsonify({"status": "error", "message": "No tienes permisos para esta operación"}), 403

        # Validar que no haya un análisis en ejecución
        if return_state(chat_id) == "en ejecución":
            return jsonify({"status": "error", "message": "Ya hay un análisis en ejecución"}), 409

        # Crear ejecución (usar to_thread para no bloquear)
        exec_id = await asyncio.to_thread(fs_crear_ejecucion, chat_id, [activo], origen, opciones_usuario)

        # Lanzar análisis (sin Telegram)
        dummy_update = type("DummyUpdate", (), {
            "effective_chat": type("DummyChat", (), {"id": chat_id})(),
            "callback_query": None,
            "effective_user": type("DummyUser", (), {
                "first_name": "AppUser",
                "id": chat_id  # <-- necesario
            })()
        })()

        dummy_context = type("DummyContext", (), {
            "bot": application.bot
        })()

        urls_generadas = await ejecutar_recurrente(dummy_context, dummy_update, activo, chat_id, opciones_usuario, origen="app", exec_id=exec_id)

        await asyncio.to_thread(fs_finalizar_ejecucion, exec_id, "completado", {"urls": urls_generadas})

        return jsonify({
            "status": "ok",
            "exec_id": exec_id,
            "message": f"Análisis ejecutado para {activo}",
            "download_urls": urls_generadas  
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
        if ocupado_lock.locked():
            ocupado_lock.release()
    
   
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
