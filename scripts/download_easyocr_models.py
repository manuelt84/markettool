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
    
    # Descargar modelos para inglés y español (los más comunes)
    # Esto descargará:
    # - detection_craft.pth (para detección)
    # - en_dict.txt (English OCR)
    # - es_dict.txt (Spanish OCR) si está disponible
    reader = easyocr.Reader(
        ['en', 'es'],
        gpu=False,  # CPU durante el build
        model_storage_directory='/app/models/easyocr',
        user_network_directory='/app/models/easyocr',
        verbose=True
    )
    
    print("[EasyOCR] ✅ Modelos descargados correctamente en /app/models/easyocr")
    
    # Test rápido
    print("[EasyOCR] Realizando test de inicialización...")
    test_result = reader.readtext(
        "https://upload.wikimedia.org/wikipedia/commons/thumb/a/a5/Tsunami_by_hokusai_19th_century.jpg/1200px-Tsunami_by_hokusai_19th_century.jpg"
    )
    print(f"[EasyOCR] ✅ Test exitoso: {len(test_result)} textos detectados")
    
except Exception as e:
    print(f"[EasyOCR] ⚠️  Error descargando modelos: {e}", file=sys.stderr)
    print(f"[EasyOCR] ℹ️  El sistema intentará descargar en runtime si es necesario", file=sys.stderr)
    # No fallar el build si falla esto - es mejor tener imagen que nada
    sys.exit(0)

print("[EasyOCR] ✅ Todas las dependencias pre-cacheadas exitosamente")
