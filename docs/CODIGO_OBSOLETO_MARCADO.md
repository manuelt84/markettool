# 🗑️ CÓDIGO OBSOLETO MARCADO - MarketTool.py

**Fecha**: 2025-01-28 (actualizado 2026-02-18)  
**Estado**: ✅ COMPLETADO  
**Acción**: Marcado de funciones obsoletas con avisos DEPRECATED

---

## 📋 Resumen

Se han marcado **8 funciones obsoletas** en MarketTool.py que eran específicamente usadas por el `LegacyMarketToolAdapter` (ahora archivado). Estas funciones se mantienen por compatibilidad con código legacy secuencial, pero incluyen avisos claros de que están obsoletas para ParallelAnalysisEngine v2.

---

## 🔧 Funciones Marcadas como DEPRECATED

### 1. `analisis_tecnico_detallado()` - Línea ~9330
```python
⚠️ DEPRECATED: Esta función está obsoleta para ParallelAnalysisEngine v2.
Use StandaloneAnalyzer.compute_all_indicators() para nueva implementación.
Mantenida solo por compatibilidad con código legacy secuencial.
```
- **Propósito**: Análisis técnico detallado (trend, RSI, régimen)
- **Reemplazo moderno**: `StandaloneAnalyzer.compute_all_indicators(df)`

### 2. `ajustar_probabilidad_fundamental()` - Línea ~9715
```python
⚠️ DEPRECATED: Esta función está obsoleta para ParallelAnalysisEngine v2.
Mantenida solo por compatibilidad con código legacy secuencial.
```
- **Propósito**: Ajuste de probabilidad según eventos fundamentales
- **Reemplazo moderno**: Integrado en `StandaloneAnalyzer.synthesize_signal()`

### 3. `detectar_patrones_confirmados_velas()` - Línea ~10487
```python
⚠️ DEPRECATED: Esta función está obsoleta para ParallelAnalysisEngine v2.
Use StandaloneAnalyzer.detect_candle_patterns() para nueva implementación.
Mantenida solo por compatibilidad con código legacy secuencial.
```
- **Propósito**: Detección de patrones de velas con confirmación
- **Reemplazo moderno**: `StandaloneAnalyzer.detect_candle_patterns(df)`

### 4. `predecir_arima()` - Línea ~10665
```python
⚠️ DEPRECATED: Esta función está obsoleta para ParallelAnalysisEngine v2.
Use StandaloneAnalyzer.predict_arima_async() para nueva implementación.
Mantenida solo por compatibilidad con código legacy secuencial.
```
- **Propósito**: Predicción ARIMA con timeout legacy (45s)
- **Reemplazo moderno**: `await StandaloneAnalyzer.predict_arima_async(df, tf, symbol, steps)`
- **Diferencia**: Nuevo timeout 15s + asyncio nativo

### 5. `simulacion_monte_carlo()` - Línea ~10811
```python
⚠️ DEPRECATED: Esta función está obsoleta para ParallelAnalysisEngine v2.
Use StandaloneAnalyzer.monte_carlo_forecast() para nueva implementación.
Mantenida solo por compatibilidad con código legacy secuencial.
```
- **Propósito**: Simulación Monte Carlo para probabilidades alza/baja
- **Reemplazo moderno**: `StandaloneAnalyzer.monte_carlo_forecast(df, num_sims, num_days)`
- **Diferencia**: Retorna percentiles (median, upper, lower)

### 6. `predecir_media_movil()` - Línea ~10865
```python
⚠️ DEPRECATED: Esta función está obsoleta para ParallelAnalysisEngine v2.
Use StandaloneAnalyzer.compute_sma() para análisis moderno.
Mantenida solo por compatibilidad con código legacy secuencial.
```
- **Propósito**: Predicción simple con media móvil
- **Reemplazo moderno**: `StandaloneAnalyzer.compute_sma(df, period)`

### 7. `_wrapper_simulacion_monte_carlo()` - Línea ~11192
```python
⚠️ DEPRECATED: Esta función está obsoleta para ParallelAnalysisEngine v2.
Use StandaloneAnalyzer.monte_carlo_forecast() para nueva implementación.
Mantenida solo por compatibilidad con código legacy secuencial.
```
- **Propósito**: Wrapper para ProcessPoolExecutor (pickle-compatible)
- **Reemplazo moderno**: `StandaloneAnalyzer.monte_carlo_forecast()` (ya es async-compatible)

### 8. `calcular_entradas()` - Línea ~12464
```python
⚠️ DEPRECATED: Esta función está obsoleta para ParallelAnalysisEngine v2.
Use StandaloneAnalyzer.synthesize_signal() para nueva implementación.
Mantenida solo por compatibilidad con código legacy secuencial.
```
- **Propósito**: Cálculo de señales de entrada (COMPRA/VENTA/NEUTRAL)
- **Reemplazo moderno**: `await StandaloneAnalyzer.synthesize_signal(df, symbol, tf, ...)`
- **Diferencia**: Retorna dataclass `Signal` tipado

---

## 🎯 Razón del Marcado

Estas funciones eran llamadas exclusivamente por `LegacyMarketToolAdapter` (archivado en `docs/legacy/`). Ahora que el ParallelAnalysisEngine v2 usa `StandaloneAnalyzer`, estas funciones son obsoletas **para ese propósito**.

**Sin embargo**, se mantienen en el código porque:
1. ✅ **Compatibilidad**: Pueden ser usadas por flujos secuenciales legacy si existen
2. ✅ **No rompe nada**: El código legacy secuencial sigue funcionando
3. ⚠️ **Aviso claro**: Desarrolladores saben que están obsoletas para v2

---

## 📊 Comparación: Antes vs Ahora

| Función Legacy | Función Moderna (StandaloneAnalyzer) |
|----------------|--------------------------------------|
| `analisis_tecnico_detallado()` | `compute_all_indicators()` |
| `detectar_patrones_confirmados_velas()` | `detect_candle_patterns()` |
| `predecir_arima()` (45s timeout) | `predict_arima_async()` (15s timeout) |
| `simulacion_monte_carlo()` | `monte_carlo_forecast()` |
| `predecir_media_movil()` | `compute_sma()` |
| `_wrapper_simulacion_monte_carlo()` | `monte_carlo_forecast()` (ya async) |
| `calcular_entradas()` (dict) | `synthesize_signal()` (Signal dataclass) |
| `ajustar_probabilidad_fundamental()` | Integrado en `synthesize_signal()` |

---

## ✅ Validación

- ✅ **Sin errores de compilación** (verificado)
- ✅ **8 funciones marcadas** con avisos DEPRECATED
- ✅ **Código legacy funcional** (no se eliminó, solo se marcó)
- ✅ **Avisos claros** sobre alternativas modernas

---

## 🚀 Próximos Pasos (Opcional)

### Corto Plazo
- [ ] Revisar si hay otros usos de estas funciones fuera de MarketTool.py
- [ ] Considerar agregar logging de deprecation warnings en runtime

### Mediano Plazo
- [ ] Migrar flujos secuenciales restantes a StandaloneAnalyzer
- [ ] Una vez migrado todo, considerar eliminar estas funciones completamente

### Largo Plazo
- [ ] Refactorizar MarketTool.py completamente para usar solo arquitectura hexagonal
- [ ] Eliminar código legacy cuando ya no haya dependencias

---

## 📝 Notas

> **IMPORTANTE**: Estas funciones NO se eliminaron porque pueden ser usadas por:
> - Flujos secuenciales legacy (si existen)
> - Scripts externos que importan de MarketTool.py
> - Otros proyectos en el workspace (tradingnowLocalV2, tradingnowCloudRun, etc.)

> **DECISIÓN**: Se optó por **marcar como obsoleto** en lugar de **eliminar** para:
> 1. Mantener compatibilidad retroactiva
> 2. Evitar romper código dependiente
> 3. Dar tiempo para migración gradual

---

**¡Marcado de código obsoleto completado exitosamente! 🎉**

**Siguiente paso**: Considerar migrar flujos secuenciales a StandaloneAnalyzer para eventualmente eliminar estas funciones.
