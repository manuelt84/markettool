#!/usr/bin/env python3
"""
Pre-descargar modelos de EasyOCR durante el build del Docker.
Esto evita descargas en tiempo de ejecución que pueden fallar sin internet.
"""

import os
import sys

# Configurar directorios de cache
os.environ['EASY_OCR_MODEL_DIR'] = '/app/models/easyocr'
os.environ['TORCH_HOME'] = '/app/models/torch'

# Crear directorios si no existen
os.makedirs('/app/models/easyocr', exist_ok=True)
os.makedirs('/app/models/torch', exist_ok=True)

try:
    import easyocr
    
    print("[EasyOCR] Descargando modelos de detección y reconocimiento...")
    
    # Descargar modelos solo para inglés (coincide con runtime)
    # Esto descargará:
    # - craft_mlt_25k.pth (modelo de detección)
    # - english_g2.pth (modelo de reconocimiento)
    reader = easyocr.Reader(
        ['en'],  # Solo inglés, coincide con get_easyocr_reader()
        gpu=False,  # CPU durante el build
        model_storage_directory='/app/models/easyocr',
        user_network_directory='/app/models/easyocr',
        verbose=True
    )
    
    print("[EasyOCR] ✅ Modelos descargados correctamente en /app/models/easyocr")
    print("[EasyOCR] ✅ Todas las dependencias pre-cacheadas exitosamente")
    
except Exception as e:
    print(f"[EasyOCR] ⚠️  Error descargando modelos: {e}", file=sys.stderr)
    print(f"[EasyOCR] ℹ️  El sistema intentará descargar en runtime si es necesario", file=sys.stderr)
    # No fallar el build si falla esto - es mejor tener imagen que nada
    sys.exit(0)
