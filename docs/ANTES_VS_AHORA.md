# 🔄 ARQUITECTURA: ANTES vs AHORA

**Fecha**: 2025-01-28  
**Cambio**: Opción B (Adapter Pattern) → 100% Standalone  

---

## 📊 Comparación Visual

### ANTES (Opción B con Adapter)

```
┌─────────────────────────────────────────────────────┐
│  ParallelAnalysisEngine v2                         │
│                                                     │
│  ┌──────────────────────────────────────────────┐ │
│  │ Nivel 1: Multi-Asset (18 assets)            │ │
│  │  ┌────────────────────────────────────────┐ │ │
│  │  │ Nivel 2: Multi-Timeframe (7 TFs)      │ │ │
│  │  │  ┌──────────────────────────────────┐ │ │ │
│  │  │  │ Nivel 3: Entry Calculation      │ │ │ │
│  │  │  │  ▼ LegacyMarketToolAdapter      │ │ │ │
│  │  │  │    ▼ MarketTool.py (600 líneas) │ │ │ │
│  │  │  │      - ARIMA (predict)          │ │ │ │
│  │  │  │      - Indicators (RSI, MACD)   │ │ │ │
│  │  │  │      - Patterns (YOLO model)    │ │ │ │
│  │  │  │      - Monte Carlo              │ │ │ │
│  │  │  └──────────────────────────────────┘ │ │ │
│  │  └────────────────────────────────────────┘ │ │
│  └──────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────┘

Configuración:
  .env:
    - ARIMA_MODE=standard
    - ARIMA_TIMEOUT=45
    - PARALLEL_TIMEOUT_PREDICTION_ARIMA=15  ← Conflicto!

Problemas:
  ❌ Dos códigos acoplados (adapter + legacy)
  ❌ Dos sets de configuración (.env confuso)
  ❌ Timeouts conflictivos (45s vs 15s)
  ❌ Difícil de testear (mocks de legacy)
  ❌ Overhead del adapter pattern
```

---

### AHORA (100% Standalone)

```
┌─────────────────────────────────────────────────────┐
│  ParallelAnalysisEngine v2 (Standalone)            │
│                                                     │
│  ┌──────────────────────────────────────────────┐ │
│  │ Nivel 1: Multi-Asset (18 assets)            │ │
│  │  ┌────────────────────────────────────────┐ │ │
│  │  │ Nivel 2: Multi-Timeframe (7 TFs)      │ │ │
│  │  │  ┌──────────────────────────────────┐ │ │ │
│  │  │  │ Nivel 3: Entry Calculation      │ │ │ │
│  │  │  │  ▼ StandaloneAnalyzer           │ │ │ │
│  │  │  │    (1,000 líneas pure Python)   │ │ │ │
│  │  │  │    - ARIMA (statsmodels)        │ │ │ │
│  │  │  │    - Indicators (numpy/pandas)  │ │ │ │
│  │  │  │    - Patterns (OHLC logic)      │ │ │ │
│  │  │  │    - Monte Carlo (numpy)        │ │ │ │
│  │  │  │    - Signal Synthesis           │ │ │ │
│  │  │  └──────────────────────────────────┘ │ │ │
│  │  └────────────────────────────────────────┘ │ │
│  └──────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────┘

Configuración:
  .env:
    - PARALLEL_TIMEOUT_PREDICTION_ARIMA=15  ✅ Solo una configuración

Ventajas:
  ✅ Un solo código (standalone)
  ✅ Configuración unificada (solo PARALLEL_*)
  ✅ Timeouts nativos (asyncio)
  ✅ Fácil de testear (solo Python)
  ✅ Sin overhead (más rápido)
```

---

## 📋 Cambios por Archivo

### 1. `markettool/application/adapters/`

**ANTES**:
```
adapters/
├── __init__.py              # Export: LegacyMarketToolAdapter, get_adapter
└── legacy_adapter.py        # 600+ líneas (wrapper de MarketTool.py)
```

**AHORA**:
```
adapters/
├── __init__.py              # Export: StandaloneAnalyzer, get_analyzer, Signal
└── standalone_analyzer.py   # 1,000+ líneas (pure Python implementation)

🗑️ ARCHIVADO:
docs/legacy/legacy_adapter_ARCHIVED_20250128.py
```

---

### 2. `parallel_analysis_v2.py`

**ANTES**:
```python
from markettool.application.adapters import get_adapter, LegacyMarketToolAdapter

class ParallelAnalysisEngine:
    def __init__(self, ...):
        self.adapter = get_adapter(
            timeout_arima=self.config.timeout_prediction_arima,
            timeout_general=self.config.timeout_per_tf
        )
    
    async def _analyze_tf_entry_signals(self, ...):
        # Tarea 1
        indicators = await self.adapter.compute_indicators_fast(df, tf)
        
        # Tarea 2
        patterns = await self.adapter.detect_candle_patterns(df, symbol, tf)
        
        # Tarea 3
        arima_pred = await self.adapter.predict_arima_safe(df, tf, symbol, steps=5)
        
        # Tarea 4
        mc_scenarios = await self.adapter.generate_monte_carlo_scenarios(
            df, symbol, tf, num_scenarios=100, num_days=5
        )
        
        # Synthesis
        signal = await self.adapter.synthesize_signal(
            symbol=symbol, tf=tf, df=df,
            indicators=indicators, patterns=patterns,
            arima_pred=arima_pred, mc_scenarios=mc_scenarios,
            historical_entries=None, cfg=cfg
        )
```

**AHORA**:
```python
from markettool.application.adapters import get_analyzer, StandaloneAnalyzer

class ParallelAnalysisEngine:
    def __init__(self, ...):
        self.analyzer = get_analyzer()
    
    async def _analyze_tf_entry_signals(self, ...):
        # Tarea 1
        indicators = self.analyzer.compute_all_indicators(df)
        
        # Tarea 2
        patterns = self.analyzer.detect_candle_patterns(df)
        
        # Tarea 3
        arima_pred = await self.analyzer.predict_arima_async(df, tf, symbol, steps=5)
        
        # Tarea 4
        mc_forecast = self.analyzer.monte_carlo_forecast(df, num_simulations=100, num_days=5)
        
        # Synthesis
        signal = await self.analyzer.synthesize_signal(
            df=df, symbol=symbol, timeframe=tf,
            arima_forecast=arima_pred, indicators=indicators,
            patterns=patterns, mc_forecast=mc_forecast
        )
```

**Cambios clave**:
- ✅ `self.adapter` → `self.analyzer`
- ✅ Métodos simplificados (menos parámetros)
- ✅ No más `cfg` o `historical_entries` redundantes
- ✅ Nombres más descriptivos (`mc_forecast` vs `mc_scenarios`)

---

### 3. `.env`

**ANTES**:
```bash
# ⚙️ ARIMA CONFIGURATION (3 modes + Fallback)
ARIMA_MODE=standard                       # ← Legacy variable
ARIMA_TIMEOUT=45                          # ← Legacy timeout (conflicto!)

# ℹ️ NOTA: ARIMA_TIMEOUT=45s es SOLO para legacy MarketTool.py (secuencial)
#          ParallelAnalysisEngine v2 usa PARALLEL_TIMEOUT_PREDICTION_ARIMA=15s CON fallback
```

**AHORA**:
```bash
# ⚙️ ARIMA CONFIGURATION (100% Standalone - No Legacy)
# ℹ️ NOTA: Legacy ARIMA_MODE and ARIMA_TIMEOUT have been removed
#          ParallelAnalysisEngine v2 uses PARALLEL_TIMEOUT_PREDICTION_ARIMA=15s
#          All ARIMA predictions are now pure Python (statsmodels) with fallback to Simple MA
```

**Cambios**:
- ❌ `ARIMA_MODE` eliminado (no más modos legacy)
- ❌ `ARIMA_TIMEOUT` eliminado (no conflictos)
- ✅ Solo `PARALLEL_TIMEOUT_PREDICTION_ARIMA=15` activo

---

## 🔧 Implementaciones Comparadas

### ARIMA Prediction

**ANTES (LegacyMarketToolAdapter)**:
```python
async def predict_arima_safe(self, df, tf, symbol, steps=5):
    """Wrapper de MarketTool.predecir_arima_async con timeout."""
    try:
        result = await asyncio.wait_for(
            self._call_legacy_arima(df, symbol, tf, steps),
            timeout=self.timeout_arima
        )
        return result
    except asyncio.TimeoutError:
        return self._simple_ma_fallback(df, steps)

def _call_legacy_arima(self, df, symbol, tf, steps):
    # Llamada a MarketTool.predecir_arima_async()
    from MarketTool import predecir_arima_async
    return predecir_arima_async(df, symbol, tf, steps)
```

**AHORA (StandaloneAnalyzer)**:
```python
async def predict_arima_async(self, df, timeframe, symbol, steps=5):
    """Predicción ARIMA con timeout enforcement (100% standalone)."""
    loop = asyncio.get_event_loop()
    
    try:
        forecast = await asyncio.wait_for(
            loop.run_in_executor(
                None,
                self._arima_fit_predict,
                df['close'].values,
                steps,
                (1, 1, 1)  # ARIMA order
            ),
            timeout=self.timeout_arima
        )
        return {'forecast': forecast, 'confidence': 0.75, 'error': None}
    
    except asyncio.TimeoutError:
        return {
            'forecast': self._simple_ma_forecast(df['close'].values, steps),
            'confidence': 0.3,
            'error': 'timeout'
        }

def _arima_fit_predict(self, data, steps, order):
    """Fit ARIMA and forecast (statsmodels)."""
    from statsmodels.tsa.arima.model import ARIMA
    model = ARIMA(data, order=order)
    fitted = model.fit()
    forecast = fitted.forecast(steps=steps)
    return forecast.values
```

**Ventajas**:
- ✅ No depende de MarketTool.py
- ✅ Usa statsmodels directamente
- ✅ Control total sobre timeout
- ✅ Retorno tipado (Dict con forecast, confidence, error)

---

### Signal Synthesis

**ANTES (LegacyMarketToolAdapter)**:
```python
async def synthesize_signal(
    self,
    symbol, tf, df,
    indicators, patterns, arima_pred, mc_scenarios,
    historical_entries=None, cfg=None
):
    """Wrapper de MarketTool.sintetizar_señal()"""
    from MarketTool import sintetizar_señal
    
    signal = sintetizar_señal(
        symbol=symbol,
        tf=tf,
        df=df,
        indicadores=indicators,
        patrones=patterns,
        prediccion_arima=arima_pred,
        escenarios_mc=mc_scenarios,
        entradas_recientes=historical_entries,
        config=cfg
    )
    
    return signal  # Formato legacy (dict sin tipo)
```

**AHORA (StandaloneAnalyzer)**:
```python
@dataclass
class Signal:
    """Señal de entrada generada por el análisis."""
    direction: str  # 'BUY', 'SELL', 'HOLD'
    confidence: float  # 0.0-1.0
    strength: float  # 0.0-1.0
    entry_price: float
    stop_loss: float
    take_profit: float
    reason: str
    timestamp: datetime

async def synthesize_signal(
    self,
    df, symbol, timeframe,
    arima_forecast, indicators, patterns, mc_forecast
) -> Signal:
    """Sintetizar una señal de trading (100% standalone)."""
    current_price = float(df['close'].iloc[-1])
    
    # Scores de cada componente (0-1)
    arima_score = self._score_arima(arima_forecast, current_price)
    indicator_score = self._score_indicators(indicators)
    pattern_score = self._score_patterns(patterns)
    mc_score = self._score_mc(mc_forecast, current_price)
    
    # Promedio ponderado
    weighted_score = (
        arima_score * 0.35 +
        indicator_score * 0.30 +
        pattern_score * 0.20 +
        mc_score * 0.15
    )
    
    # Determinar dirección
    if weighted_score > 0.6:
        direction = 'BUY'
        confidence = min(weighted_score, 1.0)
    elif weighted_score < 0.4:
        direction = 'SELL'
        confidence = min(1.0 - weighted_score, 1.0)
    else:
        direction = 'HOLD'
        confidence = 0.5
    
    # Risk management
    atr = self._calculate_atr(df)
    stop_loss = current_price - (2 * atr) if direction == 'BUY' else current_price + (2 * atr)
    take_profit = current_price + (3 * atr) if direction == 'BUY' else current_price - (3 * atr)
    
    return Signal(
        direction=direction,
        confidence=float(confidence),
        strength=float(weighted_score),
        entry_price=current_price,
        stop_loss=stop_loss,
        take_profit=take_profit,
        reason=f"ARIMA={arima_score:.2f}, Indicators={indicator_score:.2f}, ...",
        timestamp=datetime.now(timezone.utc)
    )
```

**Ventajas**:
- ✅ Retorno tipado (dataclass Signal)
- ✅ Risk management integrado (ATR-based)
- ✅ Scoring transparente y ajustable
- ✅ No depende de lógica legacy
- ✅ Timestamps automáticos

---

## 📈 Performance Esperado

| Métrica | Antes (Adapter) | Ahora (Standalone) | Mejora |
|---------|-----------------|-------------------|--------|
| **Overhead** | +5-10% (wrapper calls) | 0% (directo) | ✅ Más rápido |
| **Memory** | +10% (dos objetos) | Base (un objeto) | ✅ Menos RAM |
| **Testability** | Difícil (mocks) | Fácil (pure Python) | ✅ Mejor QA |
| **Maintainability** | 2 códigos | 1 código | ✅ Más simple |
| **Dependencies** | MarketTool.py requerido | Solo pandas, numpy, statsmodels | ✅ Portable |

---

## 🎯 Conclusión

### ✅ Eliminación Exitosa
- **Legacy code**: Completamente desacoplado
- **Adapter pattern**: Archivado
- **Configuración**: Unificada (solo PARALLEL_*)

### ✅ Nueva Arquitectura
- **Standalone**: 100% independiente
- **Pure Python**: statsmodels, numpy, pandas
- **Type-safe**: Dataclass Signal
- **Timeout-safe**: asyncio native

### ✅ Próximos Pasos
1. Testing de integración (50+ activos)
2. Performance benchmarks
3. Deployment a producción

---

**¡Transición completada exitosamente! 🎉**
