# 🔍 Análisis de Errores Potenciales en MarketTool.py

**Fecha**: 2026-02-16  
**Estado**: Revisión completa de imports y exports

---

## ✅ Errores Corregidos Previamente

### 1. ❌ `asgi_app._app` no existe
**Error**: `'WsgiToAsgi' object has no attribute '_app'`  
**Ubicación**: `bootstrap.py` línea 104  
**Causa**: `WsgiToAsgi` wrapper no expone `._app`  
**Solución**: ✅ Usar `webhook_app` directamente (Flask app)

### 2. ❌ Imports legacy de MarketTool.py
**Problema**: APP_CONFIG, HTTP_SESSION, fmp importados desde legacy  
**Solución**: ✅ Migrados a hexagonal (FASE 1)
- `APP_CONFIG` → `load_config()` en bootstrap.py
- `HTTP_SESSION` → `build_session()` en bootstrap.py
- `fmp` → `FMPClient()` creado en bootstrap.py

### 3. ❌ MultiLayerCacheProvider sin capas
**Error**: "At least one cache layer must be configured"  
**Causa**: Todas las capas (memory, local, gcs) estaban en `None`  
**Solución**: ✅ Crear instancias en `containers.py`:
- `MemoryCache()` siempre
- `LocalCache(cache_dir)` siempre
- `GCSCache(bucket)` si hay cliente GCS

---

## ⚠️ Errores Potenciales Encontrados

### 1. 🚨 CRÍTICO: `historicos_cache` no exportado

**Ubicación**: `markettool/interfaces/api/health.py:117`

```python
# health.py intenta importar:
from MarketTool import historicos_cache

# Pero en MarketTool.py NO existe esta variable
```

**Evidencia en MarketTool.py:**
- ✅ `LazyHistoricosLoader` (clase) - línea 5038
- ✅ `_LAZY_HIST_LOADER` (instancia privada) - línea 5040
- ❌ `historicos_cache` - NO EXISTE

**Impacto**: El health check de cache fallará con `ImportError`

**Soluciones posibles:**

**Opción A - Agregar export en MarketTool.py:**
```python
# Línea ~5041 en MarketTool.py
historicos_cache = _LAZY_HIST_LOADER  # Alias público
```

**Opción B - Cambiar import en health.py:**
```python
# En health.py línea 117
from MarketTool import _LAZY_HIST_LOADER as historicos_cache
```

**Opción C - Migrar a hexagonal (mejor):**
```python
# En health.py - usar directamente la clase hexagonal
from markettool.infra.cache.historicos_cache import _LAZY_HIST_LOADER as historicos_cache
```

**Recomendación**: ⭐ **Opción A** (más simple y mantiene compatibilidad)

---

## ✅ Verificación de Variables Exportadas

### Variables con `_` importadas en bootstrap.py:

| Variable | Definida en MarketTool.py | Línea | Estado |
|----------|---------------------------|-------|--------|
| `_POD_COORDINATOR` | ✅ | 6323 | OK |
| `_warmup_start_time` | ✅ | 10243 | OK |
| `_warmup_end_time` | ✅ | 10244 | OK |
| `_niveles_cache_hits` | ✅ | 10251 | OK |
| `_niveles_cache_misses` | ✅ | 10252 | OK |
| `_atr_cache_hits` | ✅ | 10259 | OK |
| `_atr_cache_misses` | ✅ | 10260 | OK |

**Resultado**: ✅ Todas las variables con underscore importadas existen

### Variables públicas importadas:

| Variable | Definida en MarketTool.py | Línea | Estado |
|----------|---------------------------|-------|--------|
| `asgi_app` | ✅ | 18102 | OK |
| `webhook_app` | ✅ | 18100 | OK |
| `application` | ✅ | 18061 | OK |
| `db` (Firestore) | ✅ | 1100 | OK |
| `storage` (GCS) | ✅ | ~1100 | Verificar |
| `scheduler` | ✅ | Existe | OK |

---

## 🔍 Imports Circulares (Verificados)

### MarketTool.py importa desde markettool/ (hexagonal):
✅ **No hay problema** - Es correcto que legacy importe de hexagonal

```python
# MarketTool.py línea 115-117
from markettool.core.config import AppConfig, load_config
from markettool.infra.http.session import build_session
from markettool.infra.fmp import FMPClient, FMPError, FMPPlanNotAllowed
```

### bootstrap.py importa desde MarketTool.py (legacy):
✅ **Minimizado** - Solo variables globales necesarias (16 imports)

**Imports legacy necesarios**:
- Aplicaciones: `asgi_app`, `webhook_app`, `application`
- Clientes: `db`, `storage`
- Scheduler: `scheduler`
- Funciones de carga: `cargar_datos_*` (6 funciones)
- Funciones de guardado: `guardar_*` (2 funciones)
- Función de actualización: `actualizar_menus`
- Coordinador: `_POD_COORDINATOR`
- Métricas: `_warmup_*`, `_*_cache_hits/misses` (6 variables)

**Imports eliminados (migrados a hexagonal)**:
- ❌ `APP_CONFIG` → `load_config()`
- ❌ `HTTP_SESSION` → `build_session()`
- ❌ `fmp` → `FMPClient()`

---

## 📊 Resumen de Estado

| Categoría | Total | OK | Errores | Estado |
|-----------|-------|----|---------|---------| 
| Imports Legacy | 16 | 15 | 1 | ⚠️ |
| Variables Privadas | 7 | 7 | 0 | ✅ |
| Apps/Clientes | 5 | 5 | 0 | ✅ |
| Cache Layers | 3 | 3 | 0 | ✅ |
| Migraciones Hex | 3 | 3 | 0 | ✅ |

**Total de Errores Activos**: 🚨 **1** (historicos_cache)

---

## 🎯 Acciones Recomendadas

### Prioridad ALTA (Hacer ahora):

1. **Exportar `historicos_cache` en MarketTool.py**
   ```python
   # Agregar después de línea 5040
   historicos_cache = _LAZY_HIST_LOADER
   ```

### Prioridad MEDIA (Próxima sesión):

2. **Verificar export de `storage` (GCS Client)**
   - Confirmar que está definido y exportado
   - Si no, crearlo o importarlo correctamente

3. **Documentar convención de naming**
   - Variables con `_` = privadas pero exportables si necesario
   - Variables sin `_` = públicas para export

### Prioridad BAJA (Refactor futuro):

4. **Migrar funciones de carga a hexagonal**
   - `cargar_datos_subscription_user/type`
   - `cargar_chat_ids/admin_ids`
   - Crear repositorios hexagonales

5. **Migrar scheduler jobs a hexagonal**
   - `actualizar_menus` → Use case
   - `guardar_*` → Use cases

---

## ✅ Checklist de Verificación

- [x] Revisar todos los imports desde MarketTool.py
- [x] Verificar que variables importadas existen
- [x] Identificar variables con underscore exportadas
- [x] Buscar acceso a atributos privados (._app)
- [x] Verificar configuración de cache layers
- [ ] **Arreglar export de historicos_cache** ⬅️ **PENDIENTE**
- [ ] Verificar export de storage (GCS)
- [ ] Probar pod con todos los cambios

---

## 📝 Notas Técnicas

### Sobre variables con underscore:
- En Python, `_variable` es una convención para indicar "privado"
- Pero Python no impide importarlas
- En este proyecto, se usan variables con `_` para:
  - Evitar colisiones de nombres
  - Indicar que son "internas" al módulo
  - Pero SI se exportan cuando son necesarias para integración

### Sobre imports circulares:
- **MarketTool.py (legacy) → markettool/ (hexagonal)**: ✅ OK
- **bootstrap.py → MarketTool.py (legacy)**: ✅ OK (solo globales)
- **markettool/ (hexagonal) → MarketTool.py (legacy)**: ⚠️ EVITAR (solo en casos específicos como health.py)

### Próximos pasos de migración hexagonal:
- Ver [LEGACY_VS_HEXAGONAL_ANALYSIS.md](./LEGACY_VS_HEXAGONAL_ANALYSIS.md) para plan completo
- FASE 1: ✅ Completada (APP_CONFIG, HTTP_SESSION, FMPClient, cache layers)
- FASE 2: Pendiente (Factories para Firestore/GCS)
- FASE 3: Pendiente (Use cases para datos Telegram)
- FASE 4: Pendiente (Scheduler jobs hexagonales)
