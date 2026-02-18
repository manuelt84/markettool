# Paralelización de Generación de Entradas

## Problema Identificado

En los logs se observa que las entradas se generan **secuencialmente**, con microsegundos de diferencia:

```
23:02:45,606 → + AGREGADA LONG [pullback_S2_ladder_2]
23:02:45,607 → + AGREGADA LONG [range_lower_reversion]   (1ms después)
23:02:45,615 → [Whitelist] evaluando...                  (8ms después)
```

## Causa Raíz

La función `generar_entradas_multiples()` (línea ~11300) ejecuta **múltiples llamadas secuenciales a `_try_add()`**:

```python
# Estrategias base
if sesgo_long:
    _try_add("long", s1, mult_pullback_s1, "pullback_S1")        ← Secuencial
    _try_add("long", s2, mult_pullback_s2, "pullback_S2")        ← Espera a que termine
    _try_add("long", r1 + offset, mult_breakout, "breakout_R1")  ← Espera
    ... [20+ más llamadas]
```

Cada `_try_add()` → `_add_entry()` → `_create_entry_candidate()` ejecuta:
- Adaptación de multiplicadores (cálculos contextuales)
- Cálculo de TP/SL
- Cálculo de RRR
- Validaciones

## Solución: Paralelización de Tareas

### Estrategia Propuesta

1. **Coleccionar todas las tareas** sin ejecutarlas:
   ```python
   tasks = [
       ("long", s1, mult_pullback_s1, "pullback_S1"),
       ("long", s2, mult_pullback_s2, "pullback_S2"),
       ("short", r1, mult_pullback_r1, "pullback_R1"),
       ... [20+ más]
   ]
   ```

2. **Ejecutar en paralelo con ThreadPoolExecutor** (IO-bound: contexto de cálculos, sin GIL lock crítico):
   ```python
   from concurrent.futures import ThreadPoolExecutor
   
   def _process_entry_task(task_tuple):
       side, entry_price, mult, name = task_tuple
       return _create_entry_candidate(
           side=side, entry=entry_price, atr=ATR,
           mult_tp_sl=_adapt_mult(mult, side),
           ...
       )
   
   with ThreadPoolExecutor(max_workers=4) as executor:
       results = executor.map(_process_entry_task, tasks)
       entries = [r for r in results if r is not None]
   ```

3. **Beneficio estimado**:
   - **Secuencial actual**: ~30-50ms para 20-30 entradas
   - **Paralelizado**: ~8-15ms (factor 3-5x más rápido)

## Implementación en Código

### Cambios Necesarios

Dos líneas antes del bloque de generación (línea ~11395):

```python
# ====== GENERACIÓN PARALELA DE ENTRADAS ======
entry_tasks = []  # Coleccionar todas las tareas

# Estrategias base
if sesgo_long:
    if _finite(s1): entry_tasks.append(("long", s1, mult_pullback_s1, "pullback_S1"))
    if _finite(s2): entry_tasks.append(("long", s2, mult_pullback_s2, "pullback_S2"))
    ... [todos los _try_add → entry_tasks.append(...)]

# Ejecutar EN PARALELO
from concurrent.futures import ThreadPoolExecutor
with ThreadPoolExecutor(max_workers=4) as executor:
    futures = [
        executor.submit(_create_entry_candidate_task, task, ...)
        for task in entry_tasks
    ]
    for future in futures:
        candidate = future.result()
        if candidate and not _is_duplicate(entries, candidate):
            entries.append(candidate)
            logging.info(f" + AGREGADA ...")
```

## Variables Clave

- **`max_workers=4`**: Equilibrio entre paralelismo y overhead de threading
- **`ThreadPoolExecutor`** vs `ProcessPoolExecutor`: 
  - Usar Threads porque es CPU-light (cálculos simples, no loops complejos)
  - Evitar Processes (overhead de serialización, GIL no es bottleneck aquí)

## Logs esperados post-implementación

```
23:02:45,606 → + AGREGADA LONG [pullback_S2_ladder_2]
23:02:45,606 → + AGREGADA LONG [range_lower_reversion]   (0ms diferencia - paralelo)
23:02:45,608 → + AGREGADA SHORT [pullback_R1]            (0-2ms diferencia)
23:02:45,609 → [Whitelist] evaluando...                  (3-5ms total en lugar de 15+)
```

## Riesgos y Mitigación

| Riesgo | Mitigación |
|--------|-----------|
| Race condition en `entries` list | Usar structure thread-safe o consolidar resultados después |
| Overhead de threading > beneficio | Monitorear; si `max_workers=4` es lento, reducir a 2 |
| Logging desordenado | Aceptable - los logs mantienen timestamps correctos |
| Deduplicación incorrecta | Consolidar después de paralelo, luego deduplicar |

## Próximos Pasos

1. Implementar modificación en `generar_entradas_multiples()`
2. Validar que no hay race conditions en lista `entries`
3. Monitorear logs para confirmar paralelismo
4. Ajustar `max_workers` según timing real
