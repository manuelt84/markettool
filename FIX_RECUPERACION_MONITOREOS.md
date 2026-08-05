# 🔧 RESTAURACIÓN DE MONITOREOS - FIX RECUPERACIÓN AUTOMÁTICA

## 📋 PROBLEMA IDENTIFICADO

**Reportado por:** Manuel Toro  
**Fecha:** 2026-08-04 03:44 GMT-4

### Síntoma
Al entrar a un símbolo de un análisis, **ya no se recuperan automáticamente** los monitoreos activos desde Firestore. Todos los símbolos aparecen como desactivados.

### Causa Raíz
El script `reset_all_timeframes.py` ejecutado anteriormente puso:
```javascript
{
  locked_timeframes: false,  // ❌ Esto rompe la recuperación
  running: [],               // ❌ Vacía todos los running
  selected_tfs: []           // ❌ Vacía todas las selecciones
}
```

En TODOS los documentos de la colección `monitoreos`, incluyendo los que tenían monitoreos activos.

### Por qué falla la recuperación
El código de RN (`MonitoreoScreen.tsx`) tiene esta lógica de seguridad:

```typescript
// useFocusEffect ~línea 9410
if (snap.exists()) {
  const fsData = snap.data() || {};
  const firestoreTfs = monitorTfsFromDoc(fsData);
  
  if (firestoreTfs.length > 0) {
    recoveredTfs = firestoreTfs;  // ✅ Usa Firestore
  } else if (fsData.locked_timeframes !== true) {
    recoveredTfs = [];  // ❌ IGNORA AsyncStorage si locked != true
  }
}
```

**Problema:** Como `locked_timeframes: false`, la app ignora cualquier dato en AsyncStorage y no recupera nada.

---

## ✅ SOLUCIÓN

### Script de Restauración
Se creó `restore_monitoreos_from_firestore.py` que:

1. **Escanea** todos los documentos en `monitoreos`
2. **Identifica** documentos que tenían `running: [algo_no_vacío]` antes del reset
3. **Restaura**:
   - `locked_timeframes: true` (habilita recuperación)
   - `selected_tfs: allowed_timeframes` o `running` (restaura selección)

### Ejecución

#### Modo Preview (Recomendado primero)
```bash
cd /home/mtoro/projects/markettool
python3 restore_monitoreos_from_firestore.py --dry-run
```

Esto muestra qué documentos se restaurarían SIN aplicar cambios.

#### Modo Aplicación
```bash
python3 restore_monitoreos_from_firestore.py
```

Aplica las restauraciones realmente.

---

## 📊 IMPACTO ESPERADO

### Antes del Fix
- ❌ Todos los símbolos aparecen desactivados
- ❌ No hay recuperación automática al entrar
- ❌ Usuario debe re-seleccionar TFs manualmente

### Después del Fix
- ✅ Símbolos con monitoreo activo recuperan su estado
- ✅ `locked_timeframes: true` permite lectura desde Firestore
- ✅ `selected_tfs` restaurado desde `allowed_timeframes` o `running`
- ✅ Backtesting se dispara automáticamente al recuperar

---

## 🧪 TESTING POST-FIX

### Test 1: Recuperación Básica
1. Entrar a un símbolo que tenía monitoreo activo
2. Verificar que las TFs se seleccionan automáticamente
3. Verificar que el backtesting se dispara solo

### Test 2: Múltiples Símbolos
1. Navegar entre varios símbolos
2. Cada uno debería recuperar sus TFs específicas
3. Verificar que no se mezclan TFs entre símbolos

### Test 3: Persistencia
1. Cerrar/reabrir la app
2. Volver a entrar al mismo símbolo
3. Verificar que mantiene las TFs recuperadas

---

## ⚠️ NOTAS IMPORTANTES

### Lo que ESTE script hace:
- ✅ Restaura `locked_timeframes: true` en documentos con `running` previo
- ✅ Restaura `selected_tfs` basado en datos existentes
- ✅ Permite que la recuperación automática funcione nuevamente

### Lo que ESTE script NO hace:
- ❌ NO reactiva monitoreos que estaban detenidos antes del reset
- ❌ NO modifica documentos que ya tenían `running: []`
- ❌ NO cambia el estado de `running` (solo metadatos de selección)

### Documentos afectados:
Solo aquellos que cumplían AMBAS condiciones:
1. Tenían `running: [algo_no_vacío]` antes del reset
2. Tienen `locked_timeframes: false` después del reset

---

## 📈 MÉTRICAS

### Estimación:
- **Total documentos:** ~500-2000 (depende de usuarios activos)
- **A restaurar:** ~20-30% (solo los que tenían monitoreo activo)
- **Tiempo estimado:** 10-30 segundos

---

## 🔗 ARCHIVOS RELACIONADOS

- **Script de restauración:** `restore_monitoreos_from_firestore.py`
- **Script de reset original:** `reset_all_timeframes.py`
- **Documentación del reset:** `REVISION_FIRESTORE_COMPLETA.md`
- **Código RN afectado:** `/home/mtoro/projects/markettoolapp/views/MonitoreoScreen.tsx` (~línea 9410)

---

## 🎯 PRÓXIMOS PASOS

1. **Ejecutar en modo dry-run:**
   ```bash
   python3 restore_monitoreos_from_firestore.py --dry-run
   ```

2. **Revisar output** y verificar que los documentos son correctos

3. **Ejecutar en modo aplicación:**
   ```bash
   python3 restore_monitoreos_from_firestore.py
   ```

4. **Testear en la app** entrando a símbolos con monitoreo activo

5. **Monitorear logs** para confirmar que la recuperación funciona

---

**Estado:** ✅ Script creado y listo para ejecutar  
**Riesgo:** BAJO (solo restaura metadatos, no reactiva monitoreos)  
**Impacto:** ALTO (recupera funcionalidad crítica de UX)
