# 🧵 Análisis: Consistencia de Paralelismo e Hilos

**Fecha**: 2026-02-16  
**Estado**: Revisión de arquitectura de concurrencia

---

## 📊 Resumen Ejecutivo

**INCONSISTENCIA DETECTADA**: El proyecto mezcla 3 paradigmas de concurrencia sin coordinación clara:

1. ✅ **Async/Await** (bootstrap.py) - Moderno, recomendado para I/O
2. ⚠️ **Threading sincrónico** (MarketTool.py) - Locks de threading tradicionales
3. ⚠️ **Multiprocessing** (análisis AI) - Para tareas CPU-bound

**Resultado**: Riesgo de deadlocks, race conditions, y rendimiento subóptimo

---

## 🔴 Problemas Identificados

### 1. **Scheduler: BackgroundScheduler vs AsyncIOScheduler**

**Ubicación**: MarketTool.py línea 23-24, 17772

```python
from apscheduler.schedulers.asyncio import AsyncIOScheduler  # ❌ Importado pero no usado
from apscheduler.schedulers.background import BackgroundScheduler

scheduler = BackgroundScheduler()  # ✅ Actual (thread-based)
```

**Problema**:
- BackgroundScheduler corre en su **propio thread** (no en el event loop async)
- Las tareas que dispara deben usar `asyncio.run_coroutine_threadsafe()` para llamar async code
- Causa latencia y complejidad extra

**Línea 155 en bot_init.py**:
```python
asyncio.run_coroutine_threadsafe(actualizar_menus(application), loop)  # ⚠️ Thread-safe pero complejo
```

---

### 2. **Locks mixtos: threading.Lock vs asyncio.Lock**

**En MarketTool.py (sincrónico)**:
```python
_LAST_QUOTE_TICK_LOCK = threading.Lock()  # Línea 286 - BLOQUEANTE
_FMP_SYMBOL_SEMS_LOCK = threading.Lock()  # Línea 135 - BLOQUEANTE
_ANALYSIS_SEM = ... # BoundedSemaphore sincrónico
```

**En markettool/ (hexagonal)**:
```python
# ❌ NO ENCONTRADOS: asyncio.Lock, asyncio.Semaphore
# Esto significa que la arquitectura hexagonal NO tiene mecanismos de sincronización async
```

**Problema**:
- Si código async intenta adquirir `threading.Lock`, **bloquea el event loop** 🔴
- El event loop no puede procesar otros eventos mientras espera el lock
- Solo debería usarse locks threading en threads de worker, no en async code

---

### 3. **Ejecutores: Threading vs Procesamiento**

**Ubicación**: MarketTool.py líneas 1027-1048

```python
_ANALYSIS_EXECUTOR = ThreadPoolExecutor(max_workers=_ANALYSIS_MAX_WORKERS)
_ANALYSIS_INNER_EXECUTOR = ThreadPoolExecutor(max_workers=_ANALYSIS_INNER_WORKERS)

# Condicionalmente ProcessPoolExecutor con spawn context (seguro con gRPC)
if _ANALYSIS_PRED_USE_PROCESS:
    ctx = multiprocessing.get_context('spawn')
    _ANALYSIS_PRED_EXECUTOR = ProcessPoolExecutor(max_workers=_ANALYSIS_PRED_WORKERS, mp_context=ctx)
else:
    _ANALYSIS_PRED_EXECUTOR = ThreadPoolExecutor(max_workers=_ANALYSIS_PRED_WORKERS)  # Fallback
```

**Estado**:
- ✅ ProcessPoolExecutor usa `spawn` context (seguro, sin fork corruption con gRPC)
- ✅ ThreadPoolExecutor bien configurado con max_workers
- ⚠️ Pero... **¿cómo se integran con async?** No hay `loop.run_in_executor()` visible

---

### 4. **APScheduler: Incompatibilidad con async/await**

**Actual (línea 17772)**:
```python
scheduler = BackgroundScheduler()  # ThreadPoolExecutor interno
# Los jobs usan asyncio.run_coroutine_threadsafe para llamar async
```

**Problema**: 
- BackgroundScheduler es thread-based
- Hay un `AsyncIOScheduler` importado pero no usado
- Debería migrar a AsyncIOScheduler que corre en el mismo event loop

```python
# ❌ ACTUAL (incompatible con async)
scheduler = BackgroundScheduler()
scheduler.add_job(job_func, IntervalTrigger(minutes=10))  # Corre en thread separado

# ✅ DEBERÍA SER (compatible con async)
scheduler = AsyncIOScheduler()
scheduler.add_job(async_job_func, IntervalTrigger(minutes=10))  # Corre en event loop
```

---

### 5. **Race Conditions Potenciales**

| Componente | Tipo Lock | Protege | Riesgo |
|-----------|-----------|---------|--------|
| FMP API calls | threading.BoundedSemaphore | _FMP_SYMBOL_SEMS | Bajo (bien diseñado) |
| Quote ticks | threading.Lock | _LAST_QUOTE_TICK_LOCK | **ALTO** (async?) |
| Sync operations | threading.Lock | _LAST_SYNC_LOCK | **ALTO** (async?) |
| Analysis | ThreadPoolExecutor | Análisis paralelo | Medio (bounded bien) |
| Predictions | ProcessPoolExecutor | ML inference | Bajo (spawn safe) |
| Cache warming | ? | _warmup_* | Desconocido |

---

## ✅ Recomendaciones (Prioridades)

### CRÍTICO (Hacer YA):

1. **Migrar BackgroundScheduler → AsyncIOScheduler**
   ```python
   # En MarketTool.py línea 17772
   from apscheduler.schedulers.asyncio import AsyncIOScheduler
   scheduler = AsyncIOScheduler()  # Corre en el mismo event loop
   ```
   - ✅ Elimina `asyncio.run_coroutine_threadsafe()` necesario
   - ✅ Más eficiente (sin thread extra)
   - ✅ Menos latencia en jobs

2. **Auditar threading.Lock en contexto async**
   ```python
   # Buscar dónde se usan _LAST_QUOTE_TICK_LOCK, _LAST_SYNC_LOCK desde async code
   # Si se encuentran:
   #   ❌ PROBLEMA: Bloquea event loop
   #   ✅ FIX: Migrar a asyncio.Lock
   ```

3. **Documentar estrategia de sincronización**
   - Async code → asyncio.Lock
   - Thread workers → threading.Lock
   - Semaphores → BoundedSemaphore (si compartido cross-thread)

---

### IMPORTANTE (Esta semana):

4. **Integrar executors con async**
   ```python
   # En lugar de llamar directamente, usar loop.run_in_executor()
   loop = asyncio.get_event_loop()
   result = await loop.run_in_executor(
       executor=_ANALYSIS_EXECUTOR,
       func=run_analysis_sync,
       arg1, arg2
   )
   ```

5. **Validar ProcessPoolExecutor con spawn**
   - ✅ Ya está bien implementado
   - Validar que no hay issues con Firestore/GCS en procesos spawned

---

### DESEABLE (Próxima sprint):

6. **Considerar asyncio.to_thread para tareas simples**
   ```python
   # Para tareas I/O que no son async-native
   result = await asyncio.to_thread(sync_function, arg1, arg2)
   # Más simple que configure executor manualmente
   ```

---

## 📋 Checklist de Auditoría

- [ ] ¿BackgroundScheduler se usa desde contexto async?
- [ ] ¿Se llama `_LAST_QUOTE_TICK_LOCK.acquire()` desde async code?
- [ ] ¿Se llama `_LAST_SYNC_LOCK.acquire()` desde async code?
- [ ] ¿Hay deadlock entre BackgroundScheduler jobs y async tasks?
- [ ] ¿ProcessPoolExecutor predictions funcionan bien con Firestore?
- [ ] ¿ThreadPoolExecutor analysis cause GIL contention?
- [ ] ¿Qué es `loop` en `asyncio.run_coroutine_threadsafe(..., loop)`?

---

## 🎯 Conclusión

**Estado actual**: ⚠️ **FUNCIONA PERO INEFICIENTE**

**Problemas principales**:
1. BackgroundScheduler (thread-based) no sincroniza bien con async
2. threading.Lock sincrónico puede bloquear event loop
3. 3 paradigmas de concurrencia sin coordinación clara

**Impacto**:
- ❌ Latencia en scheduler jobs (extrathread hop)
- ❌ Riesgo de deadlock si locks se usan desde async
- ❌ Rendimiento subóptimo (más context-switches)

**Próximo paso**: Migrar a AsyncIOScheduler + validar que no hay threading.Lock desde async code.
