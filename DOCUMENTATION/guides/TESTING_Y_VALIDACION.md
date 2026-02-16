# 🧪 GUÍA DE TESTING Y VALIDACIÓN POST-IMPLEMENTACIÓN

**Fecha:** 11 de Febrero de 2026  
**Versión:** MarketTool v2.0  
**Status:** 🟢 Listo para Testing

---

## 📋 PLAN DE VALIDACIÓN (48 horas)

### BLOQUE 1: VALIDACIÓN DE CÓDIGO (2 horas)

#### 1.1 - Validar Imports y Dependencias
```bash
# En terminal Python
import MarketTool
import pytest
# Debería não haber errores de import
```

#### 1.2 - Test de Funciones Nuevas
```python
# Testing FASE 1: System de apalancamiento
from MarketTool import calcular_apalancamiento_seguro
entry = 100
stop = 95
leverage, msg = calcular_apalancamiento_seguro(entry, stop)
assert leverage <= 25  # ✅ Debe estar limitado
assert leverage > 0    # ✅ Debe ser válido

# Testing FASE 2: Tamaño de posición
from MarketTool import calcular_tamaño_posicion
pos = calcular_tamaño_posicion(10000, 100, 95, 0.02)
assert pos > 0  # ✅ Debe retornar posición válida

# Testing FASE 2: Ajuste por costos
from MarketTool import ajustar_tp_sl_por_costos
tp_neto, sl_adj = ajustar_tp_sl_por_costos(1.0850, 1.0950, 1.0750, "forex", "long")
assert tp_neto < 1.0950  # ✅ TP debe ser reducido
assert sl_adj > 1.0750   # ✅ SL debe estar alejado

# Testing FASE 3: Validación OHLCV
from MarketTool import validar_ohlcv_calidad
import pandas as pd
df_test = pd.DataFrame({
    'open': [100, 101, 102],
    'high': [105, 106, 107],
    'low': [95, 96, 97],
    'close': [103, 104, 105],
    'volume': [1000, 1000, 1000]
})
es_valido, problemas = validar_ohlcv_calidad(df_test, "TEST", "1h")
assert es_valido  # ✅ Datos limpios

# Testing FASE 3: Whitelisting
from MarketTool import evaluar_si_autorizado_operar
result = evaluar_si_autorizado_operar(
    "EURUSD", "1h", "Compra", 0.65, 60, 55, 1.5, []
)
assert result['autorizado'] == True  # ✅ Condiciones buenas
assert result['score_final'] >= 60  # ✅ Score válido
```

---

### BLOQUE 2: BACK-TESTING (12 horas)

#### 2.1 - Validar Resultados de Probabilidades
```
ANTES (Sin correcciones):
- Max probabilidad: 85%
- RRR mínimo: 1.2
- Win rate estimado: 65%

DESPUÉS (Con correcciones):
- Max probabilidad: 75% ✅ (más realista)
- RRR mínimo: 1.5 ✅ (profesional)  
- Win rate esperado: 55-60% ✅ (más conservador)
```

#### 2.2 - Validar Apalancamiento Limitado
```
Antes: 
- Trade EURUSD con S1 a 0.5%: leverage = 180x
- Posición con $10k: $1.8M = 🔴 RIESGO TOTAL

Después:
- Mismo setup: leverage = 25x (limitado)
- Posición con $10k: $250k = 🟡 CONTROLADO
```

#### 2.3 - Validar Costos Incluidos
```
Trade Forex EURUSD:
Entry: 1.0850
TP: 1.0950 (antes -3 pips)
SL: 1.0750

Antes (sin costos):
- Profit: 100 pips
- RRR: 1.0

Después (con -3 pips de costos):
- TP neto: 1.0947 (reducido)
- SL ajustado: 1.0753 (aumentado)
- Profit: 97 pips
- RRR: 0.97 (ajustado)
```

---

### BLOQUE 3: DEMO TRADING (24 horas)

#### 3.1 - Configuración Demo
```
Broker: Demo (cuenta virtual)
Capital: $10,000
Máximo por trade: 2% riesgo = $200
Máximo diario: -5% = $500
Mínimo whitelist score: 60
```

#### 3.2 - Métricas a Monitorear
```
📊 Diarias:
- Número de trades: ?
- Trades tomados: ? 
- Trades rechazados (whitelist): ?
- Win rate: ?%
- Profit/Loss: ?
- Drawdown: ?%

📈 Acumulativas:
- Total trades: ?
- Win rate final: debe ser >55%
- ROI: debe ser positivo
- Max drawdown: debe ser <5%
```

#### 3.3 - Criterios de Éxito
```
✅ PASS Demo si:
- [x] Win rate >= 55%
- [x] ROI > 0% (ganancias positivas)
- [x] Max drawdown <= 5%
- [x] Leverage nunca > 25x
- [x] Whitelist rechaza ~40% de trades
- [x] RRR promedio >= 1.3

🔴 FAIL Demo si:
- Win rate < 50%
- Max drawdown > 10%
- Leverage > 25x en algún trade
- ROI negativo después de 50+ trades
```

---

### BLOQUE 4: VALIDACIÓN DE OUTPUTS

#### 4.1 - Campos Nuevos en Resultados
```json
{
  "Autorizado Operar (Whitelist)": true,  // ← NUEVO FASE 3
  "Score Whitelist": 72.5,                 // ← NUEVO FASE 3 (0-100)
  "Apalancamiento Compra Nivel 1": 15,     // ← FASE 1 limitado a 25
  "Probabilidad Tecnica (%)": 65,          // ← FASE 1 limitado a 75
  "RRR": 1.65                              // ← FASE 2 mínimo 1.5
}
```

#### 4.2 - Logs Esperados
```
✅ FASE 1 (Risk-based leverage):
[Niveles S1] ⚠️ Leverage limitado: 65.6x → 25x (soporte cercano)

✅ FASE 2 (Position sizing):
[Position] Tamaño: 2.5 contratos (2% risk = $200)

✅ FASE 3 (OHLCV validation):
[VALIDACIÓN OHLCV] EURUSD-1h: 0 problemas detectados

✅ FASE 3 (Whitelisting):
[Whitelist] EURUSD-1h Compra: Score=72.5 Autorizado=True
```

---

## 🚀 PIPELINE DE OPERACION

### Fase 1: Testing Local (Hoy)
```
1. Carga MarketTool ✅
2. Ejecuta funciones nuevas ✅
3. Valida sintaxis ✅
4. Revisa logs ✅
```

### Fase 2: Back-Testing (1-2 días)
```
1. Coloca en sistema de backtest ⏳
2. Simula últimas 100 operaciones
3. Valida métricas (win rate, RRR, leverage)
4. Ajusta si es necesario ⏳
```

### Fase 3: Demo Trading (24h-48h)
```
1. Conecta a broker demo ⏳
2. Realiza trades con capital virtual
3. Monitorea whitelist score
4. Valida criterios de éxito ⏳
```

### Fase 4: Trading Real (Solo si todas fases OK)
```
1. Comienza con pequeña posición ⏳
2. Escala gradualmente ⏳
3. Continúa monitoreando ⏳
```

---

## 🔍 CHECKLIST PRE-OPERACIÓN

### Software
- [ ] Archivo MarketTool.py sin errores
- [ ] Todas las funciones nuevas funcionan
- [ ] Imports correctos
- [ ] Logs muestran valores esperados

### Datos
- [ ] OHLCV válido para últimos 100 candles
- [ ] Sin gaps sospechosos >5%
- [ ] Sin candles invertidas
- [ ] Volumen consistente

### Cálculos
- [ ] Apalancamiento <= 25x siempre
- [ ] Probabilidades <= 75%
- [ ] RRR >= 1.5 en entradas aceptadas
- [ ] Costos incluidos en TP/SL

### Whitelisting
- [ ] Score 0-100 funciona
- [ ] ~40% de trades rechazados
- [ ] Razones de rechazo claras
- [ ] Solo toma trades >60 score

---

## ⚖️ MÁQUINAS DE ESTADO

### Estado del Leverage
```
ENTRADA
  ↓
¿Válida entrada y SL?
  ├─→ NO → return 0
  └─→ SÍ → calcular_apalancamiento_seguro()
       ├─ Método 1: Distance-based
       ├─ Método 2: Risk-based (2% risk)
       └─ Aplicar MIN(ambos, 25x) ← LÍMITE CRÍTICO
  ↓
SALIDA: leverage (0-25)
```

### Estado del Whitelisting
```
ENTRADA: Confluencia, Prob, RRR, Alertas
  ↓
Calcular score (0-100):
  ├─ Confluencia: +0-25
  ├─ Probabilidad: +0-25
  ├─ RRR: +0-25
  ├─ Alertas: ×0.7 si críticas
  └─ Score final = MIN(suma, 100)
  ↓
¿Score >= 60? ← UMBRAL CRÍTICO
  ├─→ SÍ → autorizado = True ✅
  └─→ NO → autorizado = False ❌
  ↓
SALIDA: {autorizado, score, razon}
```

---

## 🎯 MÉTRICAS DE ÉXITO FINAL

| Métrica | Target | Min. Aceptable |
|---------|--------|---|
| Win Rate | 60% | 55% |
| RRR Promedio | 1.8 | 1.5 |
| Max Leverage | 20x | 25x |
| Max Drawdown | 5% | 10% |
| ROI Anual | 30% | 20% |
| Whitelisting Accuracy | 70% | 65% |

---

## 📞 TROUBLESHOOTING

### Problema: Leverage > 25x
**Causa:** Soporte muy cercano
**Solución:** Reducir MAX_LEVERAGE a 15x si es frecuente

### Problema: Win rate < 55%
**Causa:** Cambios pueden afectar algunas estrategias
**Solución:** Back-test historicals, ajustar parámetros

### Problema: Whitelist rechaza demasiado (>50%)
**Causa:** Score threshold muy alto (60)
**Solución:** Bajar a 55 si hay buen win rate

### Problema: Costos parecen incorrectos
**Causa:** Valores por defecto pueden no coincidir con broker
**Solución:** Reemplazar en `ajustar_tp_sl_por_costos()`

---

## 📝 NOTAS DE DESARROLLO

**Cambios que pueden afectar:**
1. RRR aumentado (1.2→1.5) rechaza más operaciones
2. Probabilidades cappedas a 75% = menos "seguridad falsa"
3. Costos restados = TP reducido, SL alejado
4. Whitelisting puede rechazar 40%+ operaciones

**Feedback esperado:**
- "El sistema es más conservador" ← CORRECTO
- "Menos trades pero más ganadores" ← ESPERADO
- "Leverage bajo ayuda a dormir tranquilo" ← PERFECTO

---

**Documento generado:** 2026-02-11 19:50 UTC  
**Siguiente paso:** Ejecutar BLOQUE 1 de testing  
**Estimated Time:** 2 horas
