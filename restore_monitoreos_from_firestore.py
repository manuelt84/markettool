#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LIMPIEZA MONITOREOS - Elimina campos obsoletos selected_tfs y locked_timeframes

CONTEXTO: v79.96 eliminó la lógica de selected_tfs/locked_timeframes.
Ahora solo importa el campo `running` cuando estado='running'.

ESTE SCRIPT:
- Elimina campos obsoletos: selected_tfs, monitor_selected_tfs, selectedTFs, locked_timeframes
- Mantiene solo: running, allowed_timeframes, estado, tf_states
"""

import json
import sys
from datetime import datetime

# Intentar importar firebase_admin
try:
    import firebase_admin
    from firebase_admin import credentials, firestore, initialize_app
except ImportError:
    print("❌ Error: firebase_admin no instalado")
    print("Instalar con: pip3 install firebase-admin")
    sys.exit(1)

# Configuración
FIRESTORE_KEY_PATH = "/root/markettool/trading-firestore.json"
PROJECT_ID = "trading-449607"

def init_firestore():
    """Inicializar cliente de Firestore"""
    try:
        cred = credentials.Certificate(FIRESTORE_KEY_PATH)
        if not firebase_admin._apps:
            initialize_app(cred, {'projectId': PROJECT_ID})
        return firestore.client()
    except Exception as e:
        print(f"❌ Error inicializando Firestore: {e}")
        print(f"Verificá que el archivo {FIRESTORE_KEY_PATH} existe y tiene permisos correctos")
        sys.exit(1)

def cleanup_monitoreos(dry_run=True):
    """
    Limpiar campos obsoletos de documentos de monitoreos
    
    Campos a eliminar:
    - selected_tfs
    - monitor_selected_tfs
    - selectedTFs
    - tfs
    - locked_timeframes
    
    Campos a mantener:
    - running (ÚNICO campo activo para timeframes)
    - allowed_timeframes
    - estado
    - tf_states
    """
    
    db = init_firestore()
    collection_ref = db.collection('monitoreos')
    
    print("🔍 Escaneando documentos de monitoreos...")
    docs = list(collection_ref.stream())
    print(f"📊 Encontrados {len(docs)} documentos")
    
    cleaned_count = 0
    skipped_count = 0
    error_count = 0
    
    # Campos obsoletos a eliminar
    OBSOLETE_FIELDS = ['selected_tfs', 'monitor_selected_tfs', 'selectedTFs', 'tfs', 'locked_timeframes']
    
    for doc in docs:
        try:
            data = doc.to_dict() or {}
            doc_id = doc.id
            
            # Verificar si tiene campos obsoletos
            has_obsolete = any(field in data for field in OBSOLETE_FIELDS)
            
            if has_obsolete:
                cleaned_count += 1
                
                if dry_run:
                    print(f"\n✅ DRY-RUN: {doc_id}")
                    print(f"   running: {data.get('running', [])}")
                    print(f"   estado: {data.get('estado', 'N/A')}")
                    obsolete_found = [f for f in OBSOLETE_FIELDS if f in data]
                    print(f"   A eliminar: {obsolete_found}")
                else:
                    # Construir update para eliminar campos
                    updates = {}
                    for field in OBSOLETE_FIELDS:
                        if field in data:
                            updates[field] = firestore.DELETE_FIELD
                    
                    if updates:
                        doc.reference.update(updates)
                        print(f"✅ CLEANED: {doc_id}")
            else:
                skipped_count += 1
                
        except Exception as e:
            error_count += 1
            print(f"❌ ERROR procesando {doc.id}: {e}")
    
    # Resumen
    print("\n" + "="*60)
    print("📊 RESUMEN")
    print("="*60)
    print(f"Total documentos: {len(docs)}")
    print(f"Limpiados:        {cleaned_count}")
    print(f"Skippeados:       {skipped_count}")
    print(f"Errores:          {error_count}")
    print("="*60)
    
    if dry_run:
        print("\n⚠️  MODO DRY-RUN: No se aplicaron cambios")
        print("Ejecutá sin --dry-run para aplicar las limpiezas")
    else:
        print(f"\n✅ {cleaned_count} documentos limpiados exitosamente")
    
    return cleaned_count, skipped_count, error_count

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description='Limpiar campos obsoletos de monitoreos (selected_tfs, locked_timeframes)')
    parser.add_argument('--dry-run', action='store_true', help='Solo mostrar qué se haría, no aplicar cambios')
    args = parser.parse_args()
    
    print("="*60)
    print("🧹 LIMPIEZA MONITOREOS - Firestore")
    print("="*60)
    print(f"Fecha: {datetime.now().isoformat()}")
    print(f"Modo: {'DRY-RUN (solo preview)' if args.dry_run else 'APLICANDO CAMBIOS'}")
    print("="*60)
    print("\nCampos a eliminar:")
    for field in ['selected_tfs', 'monitor_selected_tfs', 'selectedTFs', 'tfs', 'locked_timeframes']:
        print(f"  - {field}")
    print("\nCampos a mantener:")
    for field in ['running', 'allowed_timeframes', 'estado', 'tf_states']:
        print(f"  ✓ {field}")
    print("="*60)
    
    cleaned, skipped, errors = cleanup_monitoreos(dry_run=args.dry_run)
    
    if errors > 0:
        sys.exit(1)
    sys.exit(0)
