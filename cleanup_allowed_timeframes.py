#!/usr/bin/env python3
"""
Script para limpiar allowed_timeframes inconsistentes en Firestore.

Limpia todos los documentos donde:
- estado='idle' o estado='stopped' o estado='completed'
- PERO allowed_timeframes tiene datos (inconsistencia vieja)

Esto previene que la app restaure TFs incorrectamente al navegar.

Uso: python3 cleanup_allowed_timeframes.py
"""

import os
import sys
from pathlib import Path
from google.cloud import firestore
from google.oauth2 import service_account

# Configurar credenciales
SCRIPT_DIR = Path(__file__).parent
CREDENTIALS_PATH = SCRIPT_DIR / "trading-firestore.json"

if not CREDENTIALS_PATH.exists():
    print(f"❌ Credentials no encontrados: {CREDENTIALS_PATH}")
    sys.exit(1)

print("🔍 Conectando a Firestore...")
credentials = service_account.Credentials.from_service_account_file(str(CREDENTIALS_PATH))
db = firestore.Client(project='trading-449607', credentials=credentials)

def cleanup_inconsistent_allowed_timeframes():
    monitoreos_ref = db.collection('monitoreos')
    
    # Obtener todos los documentos de monitoreos
    print("📋 Obteniendo todos los documentos de monitoreos...")
    docs = monitoreos_ref.stream()
    
    doc_list = list(docs)
    if not doc_list:
        print("✅ No hay documentos en monitoreos. Nada que hacer.")
        return
    
    print(f"📊 Encontrados {len(doc_list)} documentos de monitoreos")
    
    cleaned_count = 0
    skipped_count = 0
    batch_size = 500
    current_batch = db.batch()
    batch_count = 0
    
    for doc in doc_list:
        data = doc.to_dict() or {}
        symbol = data.get('symbol', 'unknown')
        exec_id = data.get('exec_id', 'unknown')
        estado = str(data.get('estado', '')).lower()
        allowed = data.get('allowed_timeframes', [])
        running = data.get('running', [])
        
        # Verificar si hay inconsistencia
        is_stopped = estado in ['idle', 'stopped', 'stop', 'off', 'inactivo', 'completed']
        has_allowed = bool(allowed) and len(allowed) > 0
        
        if is_stopped and has_allowed:
            # Inconsistencia detectada: estado detenido pero allowed_timeframes con datos
            current_batch.update(doc.reference, {
                'allowed_timeframes': None,  # null = borrar campo en Firestore
                'updated_at': firestore.SERVER_TIMESTAMP,
            })
            
            batch_count += 1
            cleaned_count += 1
            print(f"  🧹 {symbol} ({exec_id}): estado={estado}, allowed={len(allowed)} TFs → LIMPIANDO")
        elif is_stopped and not has_allowed:
            skipped_count += 1
            # Ya está limpio, no hacer nada
        else:
            skipped_count += 1
            # Estado running, no tocar
        
        # Commit del batch cada 500 docs
        if batch_count >= batch_size:
            print(f"\n💾 Aplicando batch de {batch_count} cambios...")
            current_batch.commit()
            current_batch = db.batch()
            batch_count = 0
    
    # Commit final
    if batch_count > 0:
        print(f"\n💾 Aplicando último batch de {batch_count} cambios...")
        current_batch.commit()
    
    print(f"\n✅ ÉXITO: {cleaned_count} documentos limpiados")
    print(f"   - {skipped_count} documentos omitidos (ya estaban limpios o están running)")
    print("📝 allowed_timeframes inconsistentes eliminados de Firestore")
    print("\n🎯 Próximo paso: Reiniciar la app para que tome los datos limpios")

if __name__ == "__main__":
    try:
        cleanup_inconsistent_allowed_timeframes()
        print("\n🎉 Proceso completado")
    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
