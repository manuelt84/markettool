/**
 * Script para desactivar TODAS las temporalidades de TODOS los activos
 * en la colección 'monitoreos' de Firestore.
 * 
 * Uso: node reset_all_timeframes.js
 */

const { initializeApp, getApps, cert } = require('firebase-admin');
const fs = require('fs');
const path = require('path');

// Inicializar Firebase Admin
const credentialsPath = path.join(__dirname, 'trading-firestore.json');
if (!fs.existsSync(credentialsPath)) {
  console.error('❌ Credentials no encontrados:', credentialsPath);
  process.exit(1);
}

if (getApps().length === 0) {
  initializeApp({
    credential: cert(credentialsPath),
  });
}

const admin = require('firebase-admin');
const db = admin.firestore();

async function resetAllTimeframes() {
  console.log('🔍 Conectando a Firestore...');
  
  const monitoreosRef = db.collection('monitoreos');
  
  // Obtener todos los documentos de monitoreos
  console.log('📋 Obteniendo todos los documentos de monitoreos...');
  const snapshot = await monitoreosRef.get();
  
  if (snapshot.empty) {
    console.log('✅ No hay documentos en monitoreos. Nada que hacer.');
    return;
  }
  
  console.log(`📊 Encontrados ${snapshot.size} documentos de monitoreos`);
  
  const batch = db.batch();
  let updatedCount = 0;
  
  snapshot.forEach(doc => {
    const data = doc.data();
    const symbol = data.symbol || 'unknown';
    const execId = data.exec_id || 'unknown';
    
    // Resetear todas las temporalidades
    batch.update(doc.ref, {
      running: [],                    // Sin TFs corriendo
      selected_tfs: [],              // Sin TFs seleccionadas
      locked_timeframes: false,      // Desbloquear timeframes
      // Mantener allowed_timeframes si existe, pero vaciar selección
      updated_at: admin.firestore.FieldValue.serverTimestamp(),
    });
    
    updatedCount++;
    console.log(`  ✓ ${symbol} (${execId}): timeframes desactivados`);
  });
  
  // Commit del batch
  console.log('\n💾 Aplicando cambios...');
  await batch.commit();
  
  console.log(`\n✅ ÉXITO: ${updatedCount} documentos actualizados`);
  console.log('📝 Todas las temporalidades de todos los activos están ahora INACTIVAS');
}

// Ejecutar
resetAllTimeframes()
  .then(() => {
    console.log('\n🎉 Proceso completado');
    process.exit(0);
  })
  .catch(err => {
    console.error('\n❌ ERROR:', err);
    process.exit(1);
  });
