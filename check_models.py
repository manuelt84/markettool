#!/usr/bin/env python3
"""
Script de diagnóstico para verificar que los modelos YOLO están correctamente configurados.
Ejecutar en el contenedor: python check_models.py
"""

import os
import sys
from pathlib import Path

def check_models():
    print("=" * 60)
    print("✅ VERIFICACIÓN DE MODELOS YOLO")
    print("=" * 60)
    
    app_root = Path.cwd()
    print(f"\n📁 Working directory: {app_root}")
    
    # Verificar archivos locales
    models = ["patrones.pt", "ruido.pt"]
    all_exist = True
    
    print("\n📦 Modelos locales:")
    for model in models:
        model_path = app_root / model
        if model_path.exists():
            size_mb = model_path.stat().st_size / (1024 * 1024)
            print(f"  ✅ {model}: {size_mb:.1f} MB")
        else:
            print(f"  ❌ {model}: NO ENCONTRADO")
            all_exist = False
    
    # Verificar configuración YOLO
    print("\n⚙️  Configuración YOLO:")
    yolo_config = Path.home() / ".ultralytics" / ".ultralytics.yaml"
    if yolo_config.exists():
        print(f"  ✅ Configuración: {yolo_config}")
    else:
        print(f"  ⚠️  Configuración no encontrada: {yolo_config}")
    
    # Intentar cargar los modelos
    print("\n🔄 Intentando cargar modelos...")
    try:
        from ultralytics import YOLO
        print("  ✅ ultralytics.YOLO importado correctamente")
        
        for model in models:
            model_path = app_root / model
            if model_path.exists():
                print(f"  ⏳ Cargando {model}...", end=" ", flush=True)
                try:
                    m = YOLO(str(model_path))
                    print(f"✅ OK")
                except Exception as e:
                    print(f"❌ ERROR: {e}")
                    all_exist = False
    except ImportError as e:
        print(f"  ❌ No se pudo importar ultralytics: {e}")
        all_exist = False
    
    # Resumen
    print("\n" + "=" * 60)
    if all_exist:
        print("✅ TODOS LOS MODELOS ESTÁN CORRECTAMENTE CONFIGURADOS")
        return 0
    else:
        print("❌ PROBLEMAS DETECTADOS - Revisa arriba")
        return 1

if __name__ == "__main__":
    sys.exit(check_models())
