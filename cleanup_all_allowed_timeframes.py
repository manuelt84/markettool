#!/usr/bin/env python3
"""
Script para limpiar TODOS los allowed_timeframes de TODOS los documentos en monitoreos.

Esto resetea completamente el campo allowed_timeframes para todos los documentos,
independientemente de su estado (running, idle, stopped, etc.).

Uso: python3 cleanup_all_allowed_timeframes.py
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

def cleanup_all_allowed_timeframes():
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
        
        # Limpiar allowed_timeframes SIEMPRE (independientemente del estado)
        if allowed and len(allowed) > 0:
            # Tiene allowed_timeframes con datos - limpiar
            current_batch.update(doc.reference, {
                'allowed_timeframes': None,  # null = borrar campo en Firestore
                'updated_at': firestore.SERVER_TIMESTAMP,
            })
            
            batch_count += 1
            cleaned_count += 1
            print(f"  🧹 {symbol} ({exec_id}): estado={estado}, allowed={len(allowed)} TFs → LIMPIANDO")
        else:
            skipped_count += 1
        
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
    print(f"   - {skipped_count} documentos omitidos (ya tenían allowed_timeframes vacío/null)")
    print("📝 Todos los allowed_timeframes fueron eliminados de Firestore")
    print("\n🎯 Próximo paso: Reiniciar la app para que tome los datos limpios")
    print("   Las TFs ahora se mantendrán desactivadas hasta que el usuario las active manualmente")

if __name__ == "__main__":
    try:
        cleanup_all_allowed_timeframes()
        print("\n🎉 Proceso completado")
    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
