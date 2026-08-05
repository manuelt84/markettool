# Reset de Todas las Temporalidades

## Problema
Se requiere desactivar TODAS las temporalidades de TODOS los activos en Firestore.

## Solución

El script `reset_all_timeframes.py` está creado pero necesita ejecutarse en el **VPS** donde tiene acceso a Firestore.

### Ejecutar en el VPS:

```bash
# SSH al VPS
ssh root@170.239.86.106

# Ir al directorio del proyecto
cd /root/markettool

# Ejecutar el script
python3 reset_all_timeframes.py
```

### Qué hace el script:
1. Se conecta a Firestore (colección `monitoreos`)
2. Obtiene TODOS los documentos de monitoreos
3. Para cada documento (activo):
   - Setea `running: []` (sin TFs corriendo)
   - Setea `selected_tfs: []` (sin TFs seleccionadas)
   - Setea `locked_timeframes: false` (desbloquea timeframes)
   - Actualiza `updated_at` al timestamp actual

### Resultado esperado:
```
🔍 Conectando a Firestore...
📋 Obteniendo todos los documentos de monitoreos...
📊 Encontrados X documentos de monitoreos
  ✓ BTCUSD (exec-123): timeframes desactivados
  ✓ ETHUSD (exec-123): timeframes desactivados
  ...
💾 Aplicando cambios...

✅ ÉXITO: X documentos actualizados
📝 Todas las temporalidades de todos los activos están ahora INACTIVAS
```

## Alternativa: Desde la App

Si no podés acceder al VPS ahora, podés hacerlo manualmente desde la app:

1. Abrir MarketTool app
2. Ir a cada símbolo (BTCUSD, ETHUSD, etc.)
3. En la pantalla de Monitoreo, desactivar todas las TFs una por una
4. Repetir para cada símbolo

**Nota:** Esto es tedioso si hay muchos símbolos. El script lo hace automático.

## Verificación Post-Reset

Después de ejecutar el script, verificar en PostgreSQL (desde el VPS):

```bash
# Conectar a PostgreSQL
psql -h 10.8.0.1 -U markettool -d markettool

# Verificar estado de monitoreos
SELECT 
  symbol,
  exec_id,
  data->'running' as running,
  data->'selected_tfs' as selected_tfs,
  data->'locked_timeframes' as locked
FROM markettool.firestore_docs
WHERE collection_name = 'monitoreos'
LIMIT 10;
```

Todas las columnas `running` y `selected_tfs` deberían ser `[]` (array vacío).
