# 🚀 Guía Rápida: Arquitectura Hexagonal MarketTool

**Última Actualización**: 2026-02-18  
**Versión**: 1.0 - Post Migración Completa

---

## 📦 Cómo Usar los Nuevos Componentes

### 1. CalculateEntriesUseCase (Orquestador Principal)

```python
from markettool.application.use_cases import get_calculate_entries_use_case
import asyncio

# Obtener instancia del use case
use_case = get_calculate_entries_use_case(logger=logger)

# Ejecutar análisis (async)
result = await use_case.execute(
    df=df_ohlcv,
    df_eventos=df_economic_events,
    symbol='EUR/USD',
    timeframe='1day',
    user_chat_id='user123',
    config={'entrada': {'verificar_zona_no_trading': True}}
)

# Resultado contiene:
# - tipo_operacion: 'Compra' | 'Venta' | 'Neutral'
# - probabilidad_alza/baja: int
# - técnica / fundamental: analysis results
# - niveles: S/R levels
# - confianza: signal strength
```

**Cuándo usar**:
- ✅ Análisis completo de entrada (técnico + fundamental + s/r)
- ✅ Cuando necesitas decisión integral
- ✅ Para nuevas features que requieren múltiples análisis

---

### 2. StandaloneAnalyzer (Análisis Técnico)

```python
from markettool.application.adapters import get_analyzer

analyzer = get_analyzer()

# MÉTODOS DISPONIBLES:

# Indicadores técnicos
indicators = analyzer.compute_all_indicators(df)
# Retorna: Dict con RSI, MACD, Bollinger, ATR, Stochastic, Divergences

# Predicción ARIMA
arima_result = await analyzer.predict_arima_async(
    df=df,
    timeframe='1day',
    symbol='EUR/USD',
    steps=5
)
# Retorna: {'forecast': [...], 'confidence': 0.85, ...}

# Patrones de velas
patterns = analyzer.detect_candle_patterns(df)
# Retorna: ['engulfing', 'hammer', 'doji', ...]

# Monte Carlo
median, upper, lower = analyzer.monte_carlo_forecast(
    df=df,
    num_simulations=100,
    num_days=5
)
# Retorna: (float, float, float) - predicciones estadísticas

# ATR (nuevo)
atr = analyzer.compute_atr(df, period=14)
# Retorna: float - Average True Range

# Estocástico (nuevo)
stoch = analyzer.compute_stochastic(df, period=14)
# Retorna: {'k': float, 'd': float}

# Divergencias (nuevo)
divs = analyzer.compute_divergences(df)
# Retorna: {'bullish': [...], 'bearish': [...]}

# Síntesis de señal
signal = await analyzer.synthesize_signal(
    df=df,
    symbol='EUR/USD',
    timeframe='1day',
    indicators={...},
    patterns=[...],
    arima_forecast={...}
)
# Retorna: Signal(direction='BUY'|'SELL'|'NEUTRAL', confidence=0.75, ...)
```

**Cuándo usar**:
- ✅ Análisis técnico puro
- ✅ Cuando solo necesitas indicadores
- ✅ Testing y validación de patrones

---

### 3. SupportResistanceService (Estructura de Mercado)

```python
from markettool.application.services import get_sr_service

sr = get_sr_service(logger=logger)

# Detectar niveles S/R
sr_levels = sr.calculate_support_resistance(
    df=df,
    window=50,
    atr_multiplier=2.0
)
# Retorna: SupportResistanceLevels(
#     supports=[1.0500, 1.0480, ...],
#     resistances=[1.0650, 1.0680, ...],
#     atr=0.00250,
#     window_used=50
# )

# Detectar rango vs tendencia
range_result = sr.detect_zigzag_range(df)
# Retorna: RangeDetectionResult(
#     is_range=True,
#     structure='rango',  # o 'alcista', 'bajista', 'indefinida'
#     rebounds=5,
#     dynamic_range=(1.0450, 1.0650)
# )

# Obtener niveles clave (s1, s2, r1, r2)
key_levels = sr.get_key_levels(
    df=df,
    supports=[...],
    resistances=[...],
    atr_threshold=2.0
)
# Retorna: {'s1': 1.0500, 's2': 1.0480, 'r1': 1.0650, 'r2': 1.0680}
```

**Cuándo usar**:
- ✅ Detectar soporte/resistencia dinámicos
- ✅ Validar si es rango o tendencia
- ✅ Obtener niveles clave para entrada/salida

---

### 4. FundamentalAnalysisService (Análisis Fundamental)

```python
from markettool.application.services import get_fundamental_service

fundamental = get_fundamental_service(logger=logger)

# Ajustar probabilidad con eventos económicos
prob, metadata = fundamental.adjust_probability_with_events(
    base_probability=55,
    df_eventos=df_events,
    symbol='EUR',
    timeframe='1day',
    date_start='2026-02-10',
    date_end='2026-02-18'
)
# Retorna: (float, Dict)
#   prob: 58.5 (ajustada con eventos)
#   metadata: {'impact': 3.5, 'events_found': 3, ...}

# Analizar impacto de noticias
impact = fundamental.calculate_news_impact(
    news_articles=['ECB raises rates...', 'Inflation up 2.1%...'],
    symbol='EUR'
)
# Retorna: float (-0.85 a 1.0) - positivo o negativo
```

**Cuándo usar**:
- ✅ Considerar eventos económicos
- ✅ Ajustar probabilidad con macro eventos
- ✅ Análisis de calendario económico

---

## 🔗 Integración en Flujo Existente

### Código Antiguo (Legacy)

```python
# MarketTool.py línea ~13126
entradas = calcular_entradas(
    df_indicadores, df_eventos, symbol, tf, user_chat_id,
    calc_windows=calc_windows
)
# Función legacy de 525 líneas - DEPRECADA
```

### Código Nuevo (Hexagonal)

```python
# MarketTool.py línea 13126
entradas = _calcular_entradas_hexagonal(
    df_indicadores, df_eventos, symbol, tf, user_chat_id,
    calc_windows=calc_windows, cfg=cfg
)
# Adapter que usa CalculateEntriesUseCase internamente
# 100% compatible con código existente
```

**Lo bueno**: No necesitas cambiar nada en tu código que llame a `calcular_datos()`. 
Todo funciona igual, pero internamente usa la nueva arquitectura hexagonal.

---

## 🏗️ Cómo Agregar Nuevos Servicios

Si necesitas crear un nuevo servicio (ej: `RiskManagementService`):

### 1. Crear el Service

```python
# markettool/application/services/risk_management_service.py

from dataclasses import dataclass
from typing import Dict, Optional
import logging

@dataclass
class RiskMetrics:
    """Resultado de análisis de riesgo"""
    max_loss_pcts: float
    Kelly_fraction: float
    position_size: float
    warning: Optional[str] = None

class RiskManagementService:
    """Calcular posición y riesgo"""
    
    def __init__(self, logger: Optional[logging.Logger] = None):
        self.logger = logger or logging.getLogger(__name__)
    
    def calculate_position_size(
        self,
        account_balance: float,
        risk_pct: float,
        entry: float,
        stop_loss: float
    ) -> RiskMetrics:
        """Calcular tamaño de posición y riesgo"""
        loss_pct = abs(stop_loss - entry) / entry
        max_loss = account_balance * risk_pct
        position_size = max_loss / loss_pct
        
        return RiskMetrics(
            max_loss_pcts=risk_pct,
            Kelly_fraction=self._calculate_kelly(...),
            position_size=position_size
        )

def get_risk_service(logger=None) -> RiskManagementService:
    """Factory function"""
    return RiskManagementService(logger=logger)
```

### 2. Exportar en __init__.py

```python
# markettool/application/services/__init__.py

from .risk_management_service import (
    RiskManagementService,
    get_risk_service,
    RiskMetrics,
)

__all__ = [
    # ... existing ...
    'RiskManagementService',
    'get_risk_service',
    'RiskMetrics',
]
```

### 3. Usar en Use Case

```python
# markettool/application/use_cases/calculate_entries.py

from markettool.application.services import get_risk_service

class CalculateEntriesUseCase:
    def __init__(self, logger=None, ...):
        self.risk_service = get_risk_service(logger=logger)
        # ...
    
    async def execute(self, ...):
        # ... tu análisis existente ...
        
        # Nuevo: calcular riesgo
        risk = self.risk_service.calculate_position_size(
            account_balance=10000,
            risk_pct=0.02,
            entry=entry_price,
            stop_loss=sl_price
        )
        
        return {
            # ... datos existentes ...
            'risk': risk,
        }
```

---

## 📐 Estructura de Directorios Hexagonal

```
markettool/
├── application/
│   ├── adapters/
│   │   ├── __init__.py
│   │   └── standalone_analyzer.py (MODERNIZADO)
│   │
│   ├── services/  (NEW VALUE OBJECTS)
│   │   ├── __init__.py
│   │   ├── support_resistance_service.py (NEW)
│   │   ├── fundamental_analysis_service.py (NEW)
│   │   ├── ... (otros servicios)
│   │   └── risk_management_service.py (FUTURE)
│   │
│   └── use_cases/  (NEW ORCHESTRATION)
│       ├── __init__.py
│       ├── calculate_entries.py (NEW)
│       └── ... (otros use cases)
│
├── infra/  (EXTERNAL ADAPTERS)
│   ├── fmp/  (FMP API client)
│   └── http/  (HTTP session)
│
└── core/  (DOMAIN & CONFIG)
    └── config.py
```

**Filosofía**:
- **Adapters**: Conectan con external world (APIs, databases)
- **Services**: Lógica de negocio puro, sin dependencias de framework
- **Use Cases**: Orquestan múltiples servicios
- **Core**: Dominio pure (dataclasses, enums, lógica simple)

---

## 🧪 Testing

```python
# test_calculate_entries_use_case.py

import pytest
from markettool.application.use_cases import CalculateEntriesUseCase
from markettool.application.services import (
    get_sr_service,
    get_fundamental_service,
)
from markettool.application.adapters import get_analyzer

@pytest.mark.asyncio
async def test_calculate_entries_basic():
    # Arrange
    use_case = CalculateEntriesUseCase()
    df = pd.DataFrame({...})  # sample data
    
    # Act
    result = await use_case.execute(
        df=df,
        df_eventos=pd.DataFrame(),
        symbol='EUR/USD',
        timeframe='1day'
    )
    
    # Assert
    assert result['tipo_operacion'] in ['Compra', 'Venta', 'Neutral']
    assert 'probabilidad_alza' in result
    assert 'niveles' in result
```

---

## 🔄 Migración Progresiva (Si aplica)

Si en el futuro quieres eliminar completamente la función legacy:

### Paso 1: Crear alias

```python
# MarketTool.py
calcular_entradas_legacy = _calcular_entradas_hexagonal
```

### Paso 2: Deprecation warning

```python
import warnings

def calcular_entradas(*args, **kwargs):
    warnings.warn(
        "calcular_entradas es deprecated, usa _calcular_entradas_hexagonal",
        DeprecationWarning
    )
    return _calcular_entradas_hexagonal(*args, **kwargs)
```

### Paso 3: Eliminar (después de múltiples deprecated warnings)

```python
# Eliminar completamente cuando users tengan tiempo de adaptarse
```

---

## 💡 Tips y Mejores Prácticas

1. **Usar Factory Functions**
   ```python
   # ✅ CORRECTO
   analyzer = get_analyzer()
   
   # ❌ INCORRECTO
   analyzer = StandaloneAnalyzer()  # No, use factory
   ```

2. **Async/Await**
   ```python
   # ✅ CORRECTO
   result = await analyzer.predict_arima_async(df, ...)
   
   # ❌ INCORRECTO (si tienes loop)
   result = asyncio.run(analyzer.predict_arima_async(...))
   ```

3. **Error Handling**
   ```python
   try:
       result = await use_case.execute(...)
   except Exception as e:
       logger.error(f"Use case failed: {e}")
       # Use case retorna neutral signal on error
   ```

4. **Logging**
   ```python
   # Pass logger para auditoría completa
   use_case = get_calculate_entries_use_case(logger=my_logger)
   ```

---

## 📞 Soporte

- **Documentación**: `/docs/MIGRACION_COMPLETADA.md`
- **Code Examples**: Revisa tests en `markettool/tests/`
- **Questions**: Referir al git commit `ed98d75` para cambios detallados

---

**Versión Hexagonal**: ✅ Productivo  
**Backward Compatible**: ✅ Sí  
**Ready for Deployment**: ✅ Sí
