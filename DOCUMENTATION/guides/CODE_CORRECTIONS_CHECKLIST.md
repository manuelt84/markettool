# 🔧 CORRECCIONES DE CÓDIGO CRÍTICAS

## 1. CORREGIR ERROR DE COLUMNA ESTOCÁSTICO (Línea 7720)

**ANTES (INCORRECTO):**
```python
required_cols = ["macd", "signal", "rsi", "stoch_k", "close", "high", "low", "ATR"]
```

**DESPUÉS (CORRECTO):**
```python
required_cols = ["macd", "signal", "rsi", "%K", "%D", "close", "high", "low", "ATR"]
```

**Archivo:** MarketTool.py, Línea 7720

---

## 2. IMPLEMENTAR LÍMITE DE APALANCAMIENTO (Líneas 9678-9691)

**ANTES (PELIGROSO - Permite 1000x leverage):**
```python
if soporte_nivel_1 and precio_actual > soporte_nivel_1:
    perdida_relativa_nivel_1 = (precio_actual - soporte_nivel_1) / precio_actual
    apalancamiento_compra_nivel_1 = int((1 - porcentaje_residual) / perdida_relativa_nivel_1) if perdida_relativa_nivel_1 > 0 else 0
else:
    apalancamiento_compra_nivel_1 = 0
```

**DESPUÉS (CON LÍMITE SEGURO):**
```python
MAX_LEVERAGE = 25  # Límite razonable para trading profesional

if soporte_nivel_1 and precio_actual > soporte_nivel_1:
    perdida_relativa_nivel_1 = (precio_actual - soporte_nivel_1) / precio_actual
    if perdida_relativa_nivel_1 > 0:
        leverage_calculado = int((1 - porcentaje_residual) / perdida_relativa_nivel_1)
        apalancamiento_compra_nivel_1 = min(leverage_calculado, MAX_LEVERAGE)
    else:
        apalancamiento_compra_nivel_1 = 0
else:
    apalancamiento_compra_nivel_1 = 0
```

**Aplicar a:** Todos los 4 casos de apalancamiento (nivel_1, nivel_2, compra, venta)  
**Archivo:** MarketTool.py, Líneas 9678-9691

---

## 3. CORREGIR VALIDACIÓN INVERTIDA DE NIVELES (Línea 9670)

**ANTES (LÓGICA INVERTIDA - Anula niveles si precio está fuera):**
```python
if soporte_nivel_1 >= precio_actual or precio_actual >= resistencia_nivel_1:
   soporte_nivel_2, soporte_nivel_1 = np.nan, np.nan
   resistencia_nivel_1, resistencia_nivel_2 = np.nan, np.nan
```

**DESPUÉS (CORRECCIÓN - Solo valida si existen):**
```python
# Validación correcta: los niveles son válidos si están bien ordenados
# No debemos anularlos si el precio está en movimiento directivo
if not (pd.notna(soporte_nivel_1) and pd.notna(resistencia_nivel_1)):
    # Solo anular si faltan datos
    soporte_nivel_2, soporte_nivel_1 = np.nan, np.nan
    resistencia_nivel_1, resistencia_nivel_2 = np.nan, np.nan
elif soporte_nivel_1 >= resistencia_nivel_1:
    # Solo anular si están invertidos (error lógico)
    soporte_nivel_2, soporte_nivel_1 = np.nan, np.nan
    resistencia_nivel_1, resistencia_nivel_2 = np.nan, np.nan
# En caso contrario, los niveles son válidos incluso si precio está fuera
```

**Archivo:** MarketTool.py, Línea 9670

---

## 4. AGREGAR GESTIÓN MÍNIMA DE RIESGO (Nueva Función)

**INSERTAR DESPUÉS DE `calc_tp_sl_venta_asym()` (línea 9785):**

```python
# ─────────────────────────────────────────────────────────────────────
# Risk Management Functions
# ─────────────────────────────────────────────────────────────────────

#@profile
def calcular_tamaño_posicion(
    account_balance: float,
    entry_price: float,
    stop_loss: float,
    max_risk_percent: float = 0.02,
    side: str = "long"
) -> Optional[float]:
    """
    Calcula el tamaño de posición basado en gestión de riesgo profesional.
    
    Formula: position_size = (account_balance * max_risk_percent) / (entry - stop_loss)
    
    Args:
        account_balance: Saldo de cuenta en moneda base
        entry_price: Precio de entrada
        stop_loss: Nivel de stop loss
        max_risk_percent: Máximo riesgo por operación (default 2% = 0.02)
        side: "long" o "short"
    
    Returns:
        Tamaño de posición en unidades, o None si inválido
    """
    if not (_finite(account_balance) and _finite(entry_price) and _finite(stop_loss)):
        return None
    if account_balance <= 0:
        return None
    if side == "long" and stop_loss >= entry_price:
        return None
    if side == "short" and stop_loss <= entry_price:
        return None
    
    max_risk_amount = account_balance * max_risk_percent
    
    if side == "long":
        risk_per_unit = entry_price - stop_loss
    else:
        risk_per_unit = stop_loss - entry_price
    
    if risk_per_unit <= 0:
        return None
    
    position_size = max_risk_amount / risk_per_unit
    return float(position_size)


#@profile
def validar_operacion_riesgo(
    account_balance: float,
    entry_price: float,
    stop_loss: float,
    take_profit: float,
    max_risk_percent: float = 0.02,
    min_rrr: float = 1.2,
    side: str = "long"
) -> dict:
    """
    Validación integral de una operación según estándares de riesgo.
    
    Returns:
        {
            "es_valida": bool,
            "position_size": float o None,
            "riesgo_total": float,
            "recompensa_total": float,
            "rrr": float o None,
            "razones_rechazo": list[str]
        }
    """
    razones = []
    
    # 1. Validar datos básicos
    if not all(_finite(x) for x in [account_balance, entry_price, stop_loss, take_profit]):
        razones.append("Precios no válidos (NaN o Inf)")
        return {
            "es_valida": False,
            "position_size": None,
            "riesgo_total": None,
            "recompensa_total": None,
            "rrr": None,
            "razones_rechazo": razones
        }
    
    # 2. Validar orden SL < Entry < TP
    if side == "long":
        if not (stop_loss < entry_price < take_profit):
            razones.append(f"Orden inválida (long): SL({stop_loss}) < Entry({entry_price}) < TP({take_profit})")
    else:
        if not (take_profit < entry_price < stop_loss):
            razones.append(f"Orden inválida (short): TP({take_profit}) < Entry({entry_price}) < SL({stop_loss})")
    
    # 3. Calcular riesgo/recompensa
    if side == "long":
        riesgo = entry_price - stop_loss
        recompensa = take_profit - entry_price
    else:
        riesgo = stop_loss - entry_price
        recompensa = entry_price - take_profit
    
    rrr = _rrr(entry_price, take_profit, stop_loss, side)
    
    if rrr is None or rrr < min_rrr:
        razones.append(f"RRR insuficiente: {rrr:.2f} < mínimo {min_rrr}")
    
    # 4. Calcular tamaño de posición
    position_size = calcular_tamaño_posicion(
        account_balance, entry_price, stop_loss, max_risk_percent, side
    )
    
    if position_size is None:
        razones.append("No se puede calcular tamaño de posición")
    
    # 5. Validar riesgo máximo
    if position_size is not None:
        riesgo_total = position_size * riesgo
        if riesgo_total > (account_balance * max_risk_percent):
            razones.append(f"Riesgo total ({riesgo_total:.2f}) excede máximo permitido")
    else:
        riesgo_total = None
    
    recompensa_total = (position_size * recompensa) if position_size else None
    
    es_valida = len(razones) == 0 and position_size is not None and rrr is not None
    
    return {
        "es_valida": es_valida,
        "position_size": position_size,
        "riesgo_total": riesgo_total,
        "recompensa_total": recompensa_total,
        "rrr": rrr,
        "razones_rechazo": razones
    }
```

**Archivo:** MarketTool.py, después de línea 9785

---

## 5. AJUSTAR PROBABILIDAD CON LÍMITE REALISTA (Línea 7813)

**ANTES:**
```python
return limitar_probabilidad(probabilidad_tecnica)
```

**DESPUÉS:**
```python
# Limitar probabilidad a máximo 75% (raramente alcanza 80%+)
# Esto refleja mejor la realidad: incluso con múltiples señales,
# la probabilidad real de ganancia rara vez supera 75%
probabilidad_tecnica = min(probabilidad_tecnica, 75)
return limitar_probabilidad(probabilidad_tecnica)
```

**Archivo:** MarketTool.py, Línea 7813

---

## 6. RESTARADJUSTAR COMISIONES EN TP/SL (Nueva función)

**INSERTAR después de `validar_operacion_riesgo()`:**

```python
#@profile
def ajustar_tp_sl_por_costos(
    entry: float,
    tp: float,
    sl: float,
    instrument_type: str,  # "forex", "crypto", "stock", "future"
    side: str = "long",
    volume: float = 1.0
) -> tuple[float, float]:
    """
    Ajusta TP y SL restando costos de transacción (spread, comisión, slippage).
    
    Costos típicos:
    - Forex: 1-2 pips spread
    - Cripto: 0.1-0.5% comisión
    - Acciones: $0.01-$0.10 por acción
    - Futuros: $20-100 por round-trip
    
    Returns:
        (tp_neto, sl_ajustado)
    """
    
    if instrument_type == "forex":
        # Forex: restar 3 pips por spread/comisión (1 pips entrada + 2 pips salida)
        pip_value = 0.0001 if "JPY" not in str(entry) else 0.01
        spread_cost = 3 * pip_value
        
        if side == "long":
            tp_neto = tp - spread_cost
            sl_ajustado = sl + spread_cost
        else:
            tp_neto = tp + spread_cost
            sl_ajustado = sl - spread_cost
            
    elif instrument_type == "crypto":
        # Cripto: restar 0.3% por comisión (0.1% entrada + 0.2% salida)
        comision_percent = 0.003
        
        if side == "long":
            tp_neto = tp * (1 - comision_percent)
            sl_ajustado = sl * (1 + comision_percent)
        else:
            tp_neto = tp * (1 + comision_percent)
            sl_ajustado = sl * (1 - comision_percent)
            
    elif instrument_type == "stock":
        # Acciones: restar comisión fija $10 por lado
        comision = 20  # $20 round-trip asumido
        cost_per_share = comision / volume if volume > 0 else 0
        
        if side == "long":
            tp_neto = tp - cost_per_share
            sl_ajustado = sl + cost_per_share
        else:
            tp_neto = tp + cost_per_share
            sl_ajustado = sl - cost_per_share
            
    else:
        # Default: no ajuste
        tp_neto, sl_ajustado = tp, sl
    
    return tp_neto, sl_ajustado
```

**Archivo:** MarketTool.py, después de función anterior

---

## CAMBIOS RECOMENDADOS EN LÍNEA POR LÍNEA

| Línea | Cambio | Prioridad | Riesgo si no se hace |
|-------|--------|-----------|---------------------|
| 7720 | "stoch_k" → "%K" | 🔴 CRÍTICA | Fallos de cálculo probabilidad |
| 9678-9691 | Agregar MAX_LEVERAGE | 🔴 CRÍTICA | Quiebra cuenta |
| 9670-9672 | Corregir lógica invertida | 🔴 CRÍTICA | No operar en breakouts |
| 7813 | max(..., 75) | 🟠 ALTA | Sobrestimar resultados |
| (nuevo) | Agregar funciones Risk | 🔴 CRÍTICA | Sin gestión de riesgo |
| (nuevo) | Ajustar por costos | 🟠 ALTA | Backtest irreal |

---

## TEST DE VALIDACIÓN POST-CORRECCIÓN

Una vez aplicadas las correcciones, ejecutar:

```python
# test_correcciones.py

# 1. Validar que columnas existan
assert "%K" in df.columns
assert "%D" in df.columns

# 2. Validar límite de apalancamiento
assert max(apalancamientos) <= 25

# 3. Validar cálculos de posición
assert position_size <= (account * 0.02) / (entry - sl)

# 4. Validar TP/SL netos
assert tp_neto < tp  # TP reducido por costos
assert abs(sl_ajustado) > abs(sl)  # SL alejado por costos

# 5. Validar probabilidades
assert max(probabilidades) <= 75
```

---

**Archivo de referencia:** TRADER_AUDIT_REPORT.md  
**Generado:** 2026-02-11
