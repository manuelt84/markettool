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
        verbose=True,
        download_enabled=True
    )
    
    # Forzar extracción de cualquier zip pendiente
    import zipfile
    import glob
    for zip_path in glob.glob('/app/models/easyocr/*.zip'):
        try:
            print(f"[EasyOCR] Extrayendo {zip_path}...")
            with zipfile.ZipFile(zip_path, 'r') as zf:
                zf.extractall('/app/models/easyocr')
            os.remove(zip_path)
            print(f"[EasyOCR] ✅ {zip_path} extraído y eliminado")
        except Exception as e:
            print(f"[EasyOCR] ⚠️  Error extrayendo {zip_path}: {e}")
    
    # Verificar que los modelos existen
    pth_files = glob.glob('/app/models/easyocr/*.pth')
    print(f"[EasyOCR] Modelos .pth encontrados: {len(pth_files)}")
    for pth in pth_files:
        print(f"  - {os.path.basename(pth)} ({os.path.getsize(pth) / 1024 / 1024:.1f} MB)")
    
    print("[EasyOCR] ✅ Modelos descargados correctamente en /app/models/easyocr")
    print("[EasyOCR] ✅ Todas las dependencias pre-cacheadas exitosamente")
    
except Exception as e:
    print(f"[EasyOCR] ⚠️  Error descargando modelos: {e}", file=sys.stderr)
    print(f"[EasyOCR] ℹ️  El sistema intentará descargar en runtime si es necesario", file=sys.stderr)
    # No fallar el build si falla esto - es mejor tener imagen que nada
    sys.exit(0)
