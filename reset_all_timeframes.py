#!/usr/bin/env python3
"""
Script para desactivar TODAS las temporalidades de TODOS los activos
en la colección 'monitoreos' de Firestore.

Uso: python3 reset_all_timeframes.py
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

def reset_all_timeframes():
    monitoreos_ref = db.collection('monitoreos')
    
    # Obtener todos los documentos de monitoreos
    print("📋 Obteniendo todos los documentos de monitoreos...")
    docs = monitoreos_ref.stream()
    
    doc_list = list(docs)
    if not doc_list:
        print("✅ No hay documentos en monitoreos. Nada que hacer.")
        return
    
    print(f"📊 Encontrados {len(doc_list)} documentos de monitoreos")
    
    updated_count = 0
    batch_size = 500
    batches = []
    current_batch = db.batch()
    batch_count = 0
    
    for doc in doc_list:
        data = doc.to_dict()
        symbol = data.get('symbol', 'unknown')
        exec_id = data.get('exec_id', 'unknown')
        
        # Resetear todas las temporalidades
        current_batch.update(doc.reference, {
            'running': [],                    # Sin TFs corriendo
            'estado': 'stopped',              # Estado detenido
            'updated_at': firestore.SERVER_TIMESTAMP,
        })
        
        batch_count += 1
        updated_count += 1
        print(f"  ✓ {symbol} ({exec_id}): timeframes desactivados")
        
        # Commit del batch cada 500 docs
        if batch_count >= batch_size:
            print(f"\n💾 Aplicando batch de {batch_count} cambios...")
            current_batch.commit()
            batches.append(batch_count)
            current_batch = db.batch()
            batch_count = 0
    
    # Commit final
    if batch_count > 0:
        print(f"\n💾 Aplicando último batch de {batch_count} cambios...")
        current_batch.commit()
        batches.append(batch_count)
    
    print(f"\n✅ ÉXITO: {updated_count} documentos actualizados en {len(batches)} batches")
    print("📝 Todas las temporalidades de todos los activos están ahora INACTIVAS")

if __name__ == "__main__":
    try:
        reset_all_timeframes()
        print("\n🎉 Proceso completado")
    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
