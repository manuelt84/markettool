# Additional Threading & Concurrency Issues Found

## Overview
Continued audit identified **7 additional critical issues** beyond the previously fixed race conditions.

---

## Critical Issues Found (Not Yet Fixed)

### 1. **`cache_noticias` Unprotected Global Dictionary** 🔴 CRITICAL

**Location:** Lines 1158, 4458-4540, 9220-9226, 18140-18190

```python
# Line 1158: Global dict with NO lock
cache_noticias = defaultdict(pd.DataFrame)

# Line 4458-4540: Unprotected read/write in obtener_noticias()
if symbol not in cache_noticias:           # CHECK (no lock)
    cache_noticias[symbol] = pd.DataFrame()  # WRITE (no lock)
df_cache = cache_noticias[symbol]          # READ (no lock)
# ... later ...
cache_noticias[symbol] = df_cache          # WRITE (no lock)
```

**Problem:**
- TOCTOU race condition on every news fetch
- Multiple threads can corrupt cache_noticias simultaneously
- DataFrame writes/reads without synchronization
- Called from:
  - `obtener_noticias()` - line 4451
  - `procesar_simbolo_temporalidad()` path

**Impact:** 🔴 Data corruption, stale news, missing data

**Solution:** Add global lock
```python
cache_noticias = defaultdict(pd.DataFrame)
cache_noticias_lock = threading.Lock()

def obtener_noticias(...):
    global cache_noticias
    with cache_noticias_lock:
        if symbol not in cache_noticias:
            cache_noticias[symbol] = pd.DataFrame()
        df_cache = cache_noticias[symbol].copy()
    # ... work with copy ...
    with cache_noticias_lock:
        cache_noticias[symbol] = df_cache
```

**Severity:** 🔴 CRITICAL - Active on every symbol analysis

---

### 2. **Firestore `.stream()` in async function without `asyncio.to_thread()`** 🟠 HIGH

**Location:** Line 4350 in `cargar_chat_ids()`

```python
async def cargar_chat_ids():  # async function
    try:
        collection_ref = db.collection("chat_ids")
        docs = collection_ref.stream()  # ⚠️ BLOCKING call in async context
        chat_ids = {
            doc.id: doc.to_dict()
            for doc in docs if doc.exists
        }
        return chat_ids
    except Exception as e:
        return {}
```

**Problem:**
- `stream()` is blocking I/O (network call to Firestore)
- Blocking in async function blocks entire event loop
- Other users cannot be served while this completes
- No timeout on Firestore call

**Similar issues at:**
- Line 4330: `cargar_admin_ids()` - same pattern
- Line 4356: `cargar_chat_ids()` - same pattern

**Impact:** 🟠 Event loop starvation, 50-500ms blockage per call, affects ALL concurrent users

**Solution:**
```python
async def cargar_chat_ids():
    try:
        def _load_sync():
            collection_ref = db.collection("chat_ids")
            docs = collection_ref.stream()
            return {doc.id: doc.to_dict() for doc in docs if doc.exists}
        
        chat_ids = await asyncio.to_thread(_load_sync)
        return chat_ids
    except Exception as e:
        return {}
```

**Severity:** 🟠 HIGH - Called at startup and on user commands

---

### 3. **Firestore `.stream()` in sync function without timeout** 🟡 MEDIUM

**Location:** Lines 1417, 7063, 7890, 16532+

```python
# Line 1417: In _sweep_stuck_user_states_once() (watchdog thread)
docs = db.collection("user_states").stream()  # ⚠️ No timeout

# Line 7063: In cargar_eventos_completos()
q = col.where("date_utc", ">=", fi_utc).where("date_utc", "<=", ff_utc)
docs = q.stream()  # ⚠️ No timeout

# Line 16532: In cargar_datos_subscription_user() (async)
docs = db.collection("suscripciones_user").stream()  # ⚠️ No timeout, in async
```

**Problem:**
- If Firestore is slow or unresponsive, blocks thread indefinitely
- Watchdog thread blocks → stops clearing stuck user states
- No way to cancel/timeout the stream
- Could cause thread pool exhaustion

**Impact:** 🟡 Thread starvation, watchdog hangs, analysis queues back up

**Firestore doesn't support timeout on streams**, but can be mitigated with:
```python
# Option 1: Set up client timeout at initialization
from google.cloud.firestore import Client

client = Client()
# Note: Firestore SDK doesn't have stream timeout, but gRPC has deadline
# Can wrap with asyncio.wait_for() if async

# Option 2: For sync functions, add explicit time guard
import signal
def timeout_handler(signum, frame):
    raise TimeoutError("Firestore query timeout")

signal.signal(signal.SIGALRM, timeout_handler)
signal.alarm(10)  # 10 second timeout
try:
    docs = db.collection("user_states").stream()
finally:
    signal.alarm(0)
```

**Severity:** 🟡 MEDIUM - Firestore usually fast, but catastrophic if slow

---

### 4. **`_LAST_SYNC` Dictionary Access Without Lock** 🔴 CRITICAL

**Location:** Lines 281, 287

```python
_LAST_SYNC: Dict[tuple, float] = {}              # Line 281 - NO LOCK!
_LAST_SYNC_LOCK = threading.Lock()               # Line 287 - Lock defined

# Usage at line 4084:
# (But no actual usage/lock shown in code review)
```

**Problem:**
- Lock is defined but appears to be unused
- Dictionary is accessed without lock protection
- If accessed from multiple threads → race condition

**Impact:** 🔴 Potential data corruption

**Solution:** Verify lock is used everywhere `_LAST_SYNC` is accessed

**Severity:** 🔴 CRITICAL if used, LOW if unused

---

### 5. **Missing Lock on Cache Accessor Calls** 🟡 MEDIUM

**Location:** Multiple cache operations

```python
# Pattern found in calcular_indicadores (line 8518):
df_result, stats = _INDICATORS_CACHE.get_or_calculate(...)

# Queue used for threading but dict access itself may race
```

**Problem:**
- `_INDICATORS_CACHE` is a global object
- `.get_or_calculate()` method accesses internal dict
- If that method isn't thread-safe internally → race condition

**Solution:** Verify IndicatorsCache methods are internally thread-safe with locks

**Severity:** 🟡 MEDIUM - Depends on implementation

---

### 6. **Timeout Issues in Analysis Pipeline** 🟡 MEDIUM

**Locations:**
- Line 11877: `future_patrones.result(timeout=15)` 
- Line 11891: `future_rango.result(timeout=15)`
- Line 11905: `future_tecnica.result(timeout=15)`
- Line 11922: `future_fundamental.result(timeout=15)`

**Problem (Recent Fix):**
- Timeouts added (15s), but:
  - 15 seconds is very long (entire analysis is 8-12s)
  - If 4 futures all timeout, total wait = 60 seconds
  - Sequential timeouts, not parallel

**Better approach:**
```python
# Use asyncio.wait_for with concurrent execution
futures = [
    future_patrones,
    future_rango, 
    future_tecnica,
    future_fundamental
]

try:
    results = concurrent.futures.wait(futures, timeout=15)
    for f in results.done:
        f.result()
except TimeoutError:
    logger.warning("Some tasks timed out after 15s")
    for f in futures:
        f.cancel()  # Cancel remaining tasks
```

**Severity:** 🟡 MEDIUM - Already partially fixed, refinement needed

---

### 7. **No Thread Pool Health Monitoring** 🟡 MEDIUM

**Location:** Lines 1108-1130

```python
_ANALYSIS_EXECUTOR = ThreadPoolExecutor(max_workers=max(1, _ANALYSIS_MAX_WORKERS))
_ANALYSIS_INNER_EXECUTOR = ThreadPoolExecutor(...)
_ANALYSIS_PRED_EXECUTOR = ThreadPoolExecutor(...)
```

**Problem:**
- Thread pools created but never monitored
- No circuit breaker if threads start failing
- No queue depth monitoring
- No task timeout enforcement
- Threads can silently die

**Impact:** 🟡 Silent failures, queue exhaustion, memory leaks

**Solution:**
```python
class MonitoredExecutor:
    def __init__(self, executor, max_queue_size=1000):
        self.executor = executor
        self.queue_size = 0
        self.failed_tasks = 0
        
    def submit(self, fn, *args, **kwargs):
        if self.queue_size > self.max_queue_size:
            raise RuntimeError("Thread pool queue too large")
        
        def wrapped():
            try:
                return fn(*args, **kwargs)
            except Exception as e:
                self.failed_tasks += 1
                logger.error(f"Task failed: {e}")
                raise
        
        self.queue_size += 1
        future = self.executor.submit(wrapped)
        future.add_done_callback(lambda _: setattr(self, 'queue_size', self.queue_size - 1))
        return future
```

**Severity:** 🟡 MEDIUM - Latent issue, hard to detect

---

## Summary Table

| Issue | Type | Lines | Severity | Status |
|-------|------|-------|----------|--------|
| `cache_noticias` unsynced | Race | 1158, 4458+ | 🔴 | Not fixed |
| `cargar_chat_ids()` blocking async | Async/IO | 4350 | 🟠 | Not fixed |
| `.stream()` no timeout | Timeout | 1417, 7063+ | 🟡 | Not fixed |
| `_LAST_SYNC` unsynced | Race | 281 | 🔴 | Not fixed (unused?) |
| Cache accessor locks | Threading | 8518 | 🟡 | Check needed |
| Future timeout seq | Timeout | 11877+ | 🟡 | Partially fixed |
| Thread pool health | Monitoring | 1108+ | 🟡 | Not fixed |

---

## Recommended Action Priority

### MUST DO (Critical)
1. Add `cache_noticias_lock` and protect all accesses
2. Add `_LAST_SYNC` lock usage or confirm unused

### SHOULD DO (High)
3. Wrap `.stream()` calls in `asyncio.to_thread()` where async
4. Add timeout/deadline to Firestore operations

### COULD DO (Medium)
5. Refine concurrent future timeout strategy
6. Add thread pool health monitoring
7. Verify IndicatorsCache internal thread-safety

---

## Prevention Checklist for Next Review

- [ ] All global dicts have associated lock
- [ ] No blocking I/O in async functions (use `asyncio.to_thread()`)
- [ ] All Firestore queries have timeout/deadline
- [ ] Thread pools have health monitoring
- [ ] No sequential timeout accumulation in futures
- [ ] Exception handling in all thread pool tasks

---

**Status:** ⏳ Awaiting fixes - 7 issues identified

**Severity:** 3 Critical, 1 High, 3 Medium (if used)
