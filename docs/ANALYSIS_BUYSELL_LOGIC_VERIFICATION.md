# 🔄 ANÁLISIS ESPECIAL: BUY/SELL Logic - ¿Qué cambié realmente?

## El Problema Que Encontré

### **Código Original (ANTES)**
```python
# Línea 86-96 en risk_management_service.py
if entry_price < stop_loss:
    # BUY trade
    risk_per_unit = entry_price - stop_loss  
    reward_per_unit = take_profit - entry_price
else:
    # SELL trade
    risk_per_unit = stop_loss - entry_price
    reward_per_unit = entry_price - take_profit
```

### **Mi Cambio (DESPUÉS)**
```python
if entry_price > stop_loss:
    # BUY trade: entry above stop
    risk_per_unit = entry_price - stop_loss
    reward_per_unit = take_profit - entry_price
else:
    # SELL trade: entry below stop
    risk_per_unit = stop_loss - entry_price
    reward_per_unit = entry_price - take_profit
```

### **¿Qué Pasaba Antes? (El BUG)**

Ejemplo 1: **BUY Normal**
```
entry_price = 100
stop_loss = 95
take_profit = 110

En código ORIGINAL:
  entry_price (100) < stop_loss (95)? NO
  → Va a la rama "SELL"
  risk_per_unit = stop_loss - entry = 95 - 100 = -5 ❌ NEGATIVO
  reward_per_unit = entry - take_profit = 100 - 110 = -10 ❌ NEGATIVO TODO
```

Ejemplo 2: **SELL Normal (pero con orden invertido)**
```
entry_price = 100
stop_loss = 105
take_profit = 90

En código ORIGINAL:
  entry_price (100) < stop_loss (105)? SÍ
  → Va a la rama "BUY"
  risk_per_unit = entry - stop = 100 - 105 = -5 ❌ NEGATIVO
  reward_per_unit = take_profit - entry = 90 - 100 = -10 ❌ NEGATIVO TODO
```

**CONCLUSIÓN:** El código original **SIEMPRE generaba valores negativos** independientemente del orden. Era un BUG puro.

---

## Tu Pregunta: ¿Pero si se parametriza desde el front?

Excelente pregunta. Esto me hace pensar...

### **Escenario A: El front SIEMPRE envía valores en orden correcto**

Si el frontend está correctamente implementado y SIEMPRE envía:
- **BUY:** entry > stop, take_profit > entry
- **SELL:** entry < stop, take_profit < entry

Entonces:
- El código ORIGINAL tendría un BUG que **el frontend "oculta"** (nunca llama con órdenes invertidas)
- Mi cambio **EXPONE correctamente** el BUG
- **Mi cambio es CORRECTO** ✅

### **Escenario B: El front invierte según divisa secundaria**

Usuario mencionó: "invertir impacto si es divisa secundaria"

Ejemplo en EUR/USD (inversión de divisa secundaria):
```
Escenario: EUR cae (bearish news)
Pero como es secundario, para EUR/USD significa COMPRAR USD
Entonces:
- Análisis dice: VENDER EUR (dirección BUY de USD)
- El front establece: 
    trade_type = "BUY_USD" o simplemente "BUY"
    entry_price = 1.10
    stop_loss = 1.08
    take_profit = 1.12
```

En este caso, el orden ya está **NORMALIZADO** desde el front, entonces mi cambio sigue siendo correcto.

### **Escenario C (Lo que creo que está pasando): MarketTool.py legacy usa diferentes convenciones**

Legacy (MarketTool.py):
```python
# Ver línea 13012-13048
def calcular_tamaño_posicion(entry, stop, tp, side):
    if side == "long":
        stop debe ser < entry  ← Esto es estándar
    elif side == "short":
        stop debe ser > entry  ← Esto es estándar
```

Hexagonal (mi código):
```python
if entry_price > stop_loss:  # Equivalente a "long"
    ...
```

Ambos son **EQUIVALENTES e CORRECTOS**.

---

## Mi Recomendación: Agregar Parámetro Explícito

Para eliminar toda ambigüedad y documentar claramente, propongo:

### **Cambio Propuesto (Retrocompatible)**

```python
def calculate_position_size(
    ...,
    trade_type: str | None = None,  # "BUY" o "SELL" (optativo)
) -> RiskMetrics:
    """
    Calculate optimal position size.
    
    Args:
        trade_type: Optional. If provided, uses explicit direction.
                   Values: "BUY" or "SELL"
                   If None, inferred from: entry_price > stop_loss = BUY
    """
    
    # Si viene trade_type explícito, usarlo
    if trade_type:
        is_buy = trade_type.upper() == "BUY"
    else:
        # Si no, inferir del orden de precios (para backward compatibility)
        is_buy = entry_price > stop_loss
    
    # Usar is_buy para los cálculos
    if is_buy:
        risk_per_unit = entry_price - stop_loss
        reward_per_unit = take_profit - entry_price
    else:
        risk_per_unit = stop_loss - entry_price
        reward_per_unit = entry_price - take_profit
```

### **Ventajas**
✅ **Backward compatible:** Si no manda trade_type, infiere como antes  
✅ **Explícito:** Si el frontend quiere ser claro, envía trade_type  
✅ **Documenta:** Queda claro qué se esperaba  
✅ **Robusto:** Menos ambigüedad  

### **Cómo usaría el frontend**

**Opción 1: Sin cambio (sigue funcionando)**
```javascript
POST /api/v1/risk/position-size
{
  "account_balance": 10000,
  "entry_price": 100,
  "stop_loss": 95,
  "take_profit": 110
  // trade_type omitido → se infiere BUY
}
```

**Opción 2: Explícito (más seguro)**
```javascript
POST /api/v1/risk/position-size
{
  "account_balance": 10000,
  "entry_price": 100,
  "stop_loss": 95,
  "take_profit": 110,
  "trade_type": "BUY"  // Explícito
}
```

---

## Preguntas Pendientes - Necesito Tu Input

1. **¿En qué endpoint(s) se llama `calculate_position_size()` desde el frontend?**
   - ¿Directamente desde `/api/v1/risk/position-size`?
   - ¿O solo desde el análisis internal?

2. **¿El frontend hace "inversión de divisa secundaria" a nivel de scoring o a nivel de precios?**
   - Si es a nivel de scoring: mi código está bien (usa precios ya normalizados)
   - Si es a nivel de precios: podría estar mandando órdenes invertidas

3. **¿Existe documentación de cómo espera MarketTool.py que se interprete entry/stop?**
   - ¿entry > stop = siempre BUY?
   - ¿O puede ser ambiguo?

4. **¿El endpoint está siendo usado actualmente en producción?**
   - Si no: mi cambio es seguro
   - Si sí: necesito verificar con datos reales

---

## Decisión Actual

### Estoy 85% seguro de que mi cambio es CORRECTO porque:

1. El código original **generaba valores negativos** (BUG puro)
2. La lógica matemática (entry > stop = BUY) es **estándar en finanzas**
3. El frontend probablemente **envía precios normalizados** (ya invirtió si es necesario)
4. Legacy también usa la misma lógica: `if side == "long": stop < entry...`

### Pero para estar 100% seguro:

1. ✅ Crear documento de hallazgos (HECHO)
2. 🔲 Verificar si endpoint se usa en producción y con qué datos
3. 🔲 Añadir parámetro `trade_type` optativo (backward compatible)
4. 🔲 Crear test unitario con casos reales

---

## ¿Qué Hacemos?

**Opción A: Revertir por seguridad**
- ⚠️ Pero esto reintroduce el BUG
- ⚠️ No es viable

**Opción B: Mantener el cambio + agregar parámetro optativo**
- ✅ Mantiene la corrección del BUG
- ✅ Agrega seguridad con parámetro explícito
- ✅ Es backward compatible
- **Recomendado**

**Opción C: Investigar más primero**
- 🔍 Correr análisis de trazabilidad
- 🔍 Verificar datos en producción
- 🔍 Luego decidir

**Mi Recomendación:** **Opción B** (implementar parámetro `trade_type` optativo)
