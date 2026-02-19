"""
🔗 STANDALONE ANALYZER - Análisis técnico 100% independiente sin MarketTool.py

Propósito:
- Reemplazar completamente el legacy MarketTool.py
- Implementar ARIMA, indicadores, patrones y MC desde cero
- Usar solo bibliotecas estándar: pandas, numpy, statsmodels

Características:
    ✅ ARIMA prediction (statsmodels)
    ✅ Indicadores: RSI, MACD, Bollinger Bands, SMA
    ✅ Detección de patrones de velas
    ✅ Monte Carlo simulation
    ✅ Signal synthesis
    ✅ Timeout enforcement (asyncio)
"""

import asyncio
import logging
import pandas as pd
import numpy as np
from typing import Dict, Optional, Tuple, Any, List
from dataclasses import dataclass
from functools import lru_cache
import time
from datetime import datetime, timedelta, timezone

# Análisis técnico
import warnings
warnings.filterwarnings('ignore')

try:
    from statsmodels.tsa.arima.model import ARIMA
    HAS_STATSMODELS = True
except ImportError:
    HAS_STATSMODELS = False
    logging.warning("statsmodels not installed - ARIMA will use simple MA fallback")

logger = logging.getLogger(__name__)


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


class StandaloneAnalyzer:
    """
    Analizador técnico 100% independiente sin dependencias legacy.
    
    ✅ Objetivo: Reemplazar completamente MarketTool.py
                 Implementación pura de trading técnico
    """
    
    def __init__(self, timeout_arima_seconds: int = 15, timeout_general_seconds: int = 30):
        """
        Args:
            timeout_arima_seconds: Timeout para ARIMA (default 15s)
            timeout_general_seconds: Timeout general (default 30s)
        """
        self.timeout_arima = timeout_arima_seconds
        self.timeout_general = timeout_general_seconds
        
        logger.info(
            f"[StandaloneAnalyzer] ✅ Inicializado sin legacy "
            f"(ARIMA={timeout_arima_seconds}s, general={timeout_general_seconds}s)"
        )
    
    # ==================== ARIMA PREDICTION ====================
    
    async def predict_arima_async(
        self,
        df: pd.DataFrame,
        timeframe: str,
        symbol: str,
        steps: int = 5,
        order: Tuple[int, int, int] = (1, 1, 1)
    ) -> Dict[str, Any]:
        """
        Predicción ARIMA con timeout enforcement.
        
        Args:
            df: DataFrame con precio (column 'close')
            timeframe: TF ('1min', '5min', '1hour', etc)
            symbol: Símbolo ('EURUSD')
            steps: Cuántos pasos predecir
            order: ARIMA order (p, d, q)
        
        Returns:
            {'forecast': array, 'confidence': float, 'error': str or None}
        """
        loop = asyncio.get_event_loop()
        
        try:
            # Ejecutar en thread pool con timeout
            forecast = await asyncio.wait_for(
                loop.run_in_executor(
                    None,
                    self._arima_fit_predict,
                    df['close'].values,
                    steps,
                    order
                ),
                timeout=self.timeout_arima
            )
            
            return {
                'forecast': forecast,
                'confidence': 0.75,  # Confidence default
                'error': None
            }
        
        except asyncio.TimeoutError:
            logger.warning(
                f"[ARIMA] Timeout en {symbol}/{timeframe} (>{self.timeout_arima}s), "
                f"usando fallback MA"
            )
            return {
                'forecast': self._simple_ma_forecast(df['close'].values, steps),
                'confidence': 0.3,  # Lower confidence para fallback
                'error': 'timeout'
            }
        
        except Exception as e:
            logger.error(f"[ARIMA] Error en {symbol}/{timeframe}: {e}")
            return {
                'forecast': self._simple_ma_forecast(df['close'].values, steps),
                'confidence': 0.2,
                'error': str(e)
            }
    
    def _arima_fit_predict(
        self,
        data: np.ndarray,
        steps: int,
        order: Tuple[int, int, int]
    ) -> np.ndarray:
        """Fit ARIMA and forecast."""
        if not HAS_STATSMODELS or len(data) < 10:
            return self._simple_ma_forecast(data, steps)
        
        try:
            # Fit ARIMA
            model = ARIMA(data, order=order, suppress_warnings=True)
            result = model.fit()
            
            # Forecast
            forecast = result.get_forecast(steps=steps)
            return forecast.predicted_mean.values
        
        except Exception as e:
            logger.debug(f"ARIMA fit failed: {e}, using MA fallback")
            return self._simple_ma_forecast(data, steps)
    
    def _simple_ma_forecast(self, data: np.ndarray, steps: int) -> np.ndarray:
        """Simple moving average fallback."""
        if len(data) == 0:
            return np.array([0.0] * steps)
        
        ma = np.mean(data[-min(20, len(data)):])
        return np.array([ma] * steps)
    
    # ==================== TECHNICAL INDICATORS ====================
    
    def compute_rsi(self, df: pd.DataFrame, period: int = 14) -> float:
        """
        Relative Strength Index (0-100).
        
        Returns float: RSI value
        """
        if len(df) < period + 1:
            return 50.0
        
        prices = df['close'].values
        deltas = np.diff(prices)
        
        seed = deltas[:period + 1]
        up = seed[seed >= 0].sum() / period
        down = -seed[seed < 0].sum() / period
        rs = up / down if down != 0 else 0
        
        rsi = 100 - (100 / (1 + rs)) if rs > 0 else 50.0
        return float(rsi)
    
    def compute_macd(
        self,
        df: pd.DataFrame,
        fast: int = 12,
        slow: int = 26,
        signal: int = 9
    ) -> Tuple[float, float, float]:
        """
        MACD (Moving Average Convergence Divergence).
        
        Returns: (macd_line, signal_line, histogram)
        """
        prices = df['close'].values
        
        if len(prices) < slow:
            return 0.0, 0.0, 0.0
        
        ema_fast = pd.Series(prices).ewm(span=fast).mean().iloc[-1]
        ema_slow = pd.Series(prices).ewm(span=slow).mean().iloc[-1]
        macd_line = ema_fast - ema_slow
        
        # Signal line (EMA of MACD)
        macd_series = pd.Series(prices).ewm(span=fast).mean() - \
                      pd.Series(prices).ewm(span=slow).mean()
        signal_line = macd_series.ewm(span=signal).mean().iloc[-1]
        
        histogram = macd_line - signal_line
        
        return float(macd_line), float(signal_line), float(histogram)
    
    def compute_bollinger_bands(
        self,
        df: pd.DataFrame,
        period: int = 20,
        std_dev: float = 2.0
    ) -> Tuple[float, float, float]:
        """
        Bollinger Bands.
        
        Returns: (upper_band, middle_band, lower_band)
        """
        prices = df['close'].values
        
        if len(prices) < period:
            return 0.0, 0.0, 0.0
        
        middle = np.mean(prices[-period:])
        std = np.std(prices[-period:])
        
        upper = middle + (std_dev * std)
        lower = middle - (std_dev * std)
        
        return float(upper), float(middle), float(lower)
    
    def compute_sma(self, df: pd.DataFrame, period: int = 20) -> float:
        """Simple Moving Average."""
        prices = df['close'].values
        
        if len(prices) < period:
            return float(np.mean(prices))
        
        return float(np.mean(prices[-period:]))
    
    def compute_atr(self, df: pd.DataFrame, period: int = 14) -> float:
        """Calculate Average True Range."""
        if len(df) < period:
            return float(df['high'].mean() - df['low'].mean())
        
        high = df['high'].values
        low = df['low'].values
        close = df['close'].values
        
        tr1 = high - low
        tr2 = np.abs(high[1:] - close[:-1])
        tr3 = np.abs(low[1:] - close[:-1])
        
        tr = np.maximum(np.maximum(tr1[1:], tr2), tr3)
        atr = np.mean(tr[-period:]) if len(tr) >= period else np.mean(tr)
        
        return float(atr)
    
    def compute_stochastic(
        self,
        df: pd.DataFrame,
        period: int = 14
    ) -> Dict[str, float]:
        """Calculate Stochastic Oscillator (%K and %D)."""
        if len(df) < period:
            return {'k': 50.0, 'd': 50.0}
        
        low_min = df['low'].rolling(window=period).min()
        high_max = df['high'].rolling(window=period).max()
        
        stoch_k = 100 * (df['close'] - low_min) / (high_max - low_min)
        stoch_d = stoch_k.rolling(window=3).mean()
        
        k_value = float(stoch_k.iloc[-1]) if not pd.isna(stoch_k.iloc[-1]) else 50.0
        d_value = float(stoch_d.iloc[-1]) if not pd.isna(stoch_d.iloc[-1]) else 50.0
        
        return {'k': k_value, 'd': d_value}
    
    def compute_divergences(self, df: pd.DataFrame) -> Dict[str, bool]:
        """Detect MACD and RSI divergences."""
        divergences = {
            'macd_bull': False,
            'macd_bear': False,
            'rsi_bull': False,
            'rsi_bear': False,
        }
        
        if len(df) < 20:
            return divergences
        
        # Calculate indicators
        macd_result = self.compute_macd(df)  # Returns (macd_line, signal_line, histogram) tuple
        rsi = self.compute_rsi(df)
        
        # Extract histogram from tuple (third element)
        macd_hist = macd_result[2] if isinstance(macd_result, tuple) and len(macd_result) >= 3 else 0
        
        # Get recent values for comparison
        closes = df['close'].values
        
        # Simple divergence detection (compare last 2 peaks/troughs)
        if len(closes) >= 2:
            # MACD bullish divergence: price makes lower low, MACD makes higher low
            if closes[-1] < closes[-2]:
                if macd_hist > 0:  # Simplified: if histogram positive while price down
                    divergences['macd_bull'] = True
            
            # MACD bearish divergence: price makes higher high, MACD makes lower high
            if closes[-1] > closes[-2]:
                if macd_hist < 0:  # Simplified: if histogram negative while price up
                    divergences['macd_bear'] = True
            
            # RSI divergences (similar logic)
            if closes[-1] < closes[-2] and rsi > 30:  # Price down but RSI not oversold
                divergences['rsi_bull'] = True
            
            if closes[-1] > closes[-2] and rsi < 70:  # Price up but RSI not overbought
                divergences['rsi_bear'] = True
        
        return divergences
    
    def compute_all_indicators(
        self,
        df: pd.DataFrame,
        timeframe: str = None
    ) -> Dict[str, Any]:
        """
        Compute all technical indicators.
        
        Returns dict with all indicator values (properly structured)
        """
        macd_line, macd_signal, macd_hist = self.compute_macd(df)
        bb_upper, bb_middle, bb_lower = self.compute_bollinger_bands(df)
        
        indicators = {
            'RSI': self.compute_rsi(df),
            'MACD': {
                'macd_line': macd_line,
                'signal_line': macd_signal,
                'histogram': macd_hist
            },
            'Bollinger': {
                'upper': bb_upper,
                'middle': bb_middle,
                'lower': bb_lower
            },
            'SMA20': self.compute_sma(df, 20),
            'SMA50': self.compute_sma(df, 50),
            'ATR': self.compute_atr(df),
            'Stochastic': self.compute_stochastic(df),
            'Divergences': self.compute_divergences(df),
        }
        
        return indicators
    
    # ==================== PATTERN DETECTION ====================
    
    def detect_candle_patterns(self, df: pd.DataFrame) -> List[str]:
        """
        Detección simple de patrones de velas.
        
        Returns list of pattern names detected
        """
        patterns = []
        
        if len(df) < 2:
            return patterns
        
        current = df.iloc[-1]
        prev = df.iloc[-2]
        
        open_, close = current['open'], current['close']
        high, low = current['high'], current['low']
        
        # Vela alcista fuerte
        if close > open_ and (close - open_) > (high - low) * 0.7:
            patterns.append('STRONG_BULLISH')
        
        # Vela bajista fuerte
        if close < open_ and (open_ - close) > (high - low) * 0.7:
            patterns.append('STRONG_BEARISH')
        
        # Doji
        if abs(close - open_) < (high - low) * 0.1:
            patterns.append('DOJI')
        
        # Hammer / Inverse Hammer
        upper_wick = high - max(open_, close)
        lower_wick = min(open_, close) - low
        
        if lower_wick > (high - low) * 0.5 and upper_wick < (high - low) * 0.1:
            patterns.append('HAMMER')
        
        if upper_wick > (high - low) * 0.5 and lower_wick < (high - low) * 0.1:
            patterns.append('INVERSE_HAMMER')
        
        return patterns
    
    # ==================== MONTE CARLO SIMULATION ====================
    
    def monte_carlo_forecast(
        self,
        df: pd.DataFrame,
        steps: int = 10,
        simulations: int = 100
    ) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """
        Monte Carlo simulation para forecast de precios.
        
        Returns: (median_forecast, upper_bound, lower_bound)
        """
        prices = df['close'].values
        
        if len(prices) < 2:
            return np.array([prices[-1]] * steps), \
                   np.array([prices[-1]] * steps), \
                   np.array([prices[-1]] * steps)
        
        returns = np.diff(prices) / prices[:-1]
        mean_return = np.mean(returns)
        std_return = np.std(returns)
        
        # Simular múltiples paths
        simulations_array = np.zeros((simulations, steps))
        
        for i in range(simulations):
            path = [prices[-1]]
            for _ in range(steps):
                shock = np.random.normal(mean_return, std_return)
                next_price = path[-1] * (1 + shock)
                path.append(next_price)
            simulations_array[i, :] = path[1:]
        
        # Calcular percentiles
        median = np.median(simulations_array, axis=0)
        upper = np.percentile(simulations_array, 75, axis=0)
        lower = np.percentile(simulations_array, 25, axis=0)
        
        return median, upper, lower
    
    # ==================== SIGNAL SYNTHESIS ====================
    
    async def synthesize_signal(
        self,
        df: pd.DataFrame,
        symbol: str,
        timeframe: str,
        arima_forecast: Dict[str, Any],
        indicators: Dict[str, Any],
        patterns: List[str],
        mc_forecast: Tuple[np.ndarray, np.ndarray, np.ndarray]
    ) -> Signal:
        """
        Sintetizar una señal de trading a partir de todos los análisis.
        
        Returns: Signal object with direction, confidence, etc.
        """
        current_price = float(df['close'].iloc[-1])
        
        # Scores de cada componente (0-1)
        arima_score = self._score_arima(arima_forecast, current_price)
        indicator_score = self._score_indicators(indicators)
        pattern_score = self._score_patterns(patterns)
        mc_score = self._score_mc(mc_forecast, current_price)
        
        # Promedio ponderado
        scores = [
            (arima_score, 0.35),  # 35% peso
            (indicator_score, 0.30),  # 30% peso
            (pattern_score, 0.20),  # 20% peso
            (mc_score, 0.15),  # 15% peso
        ]
        
        weighted_score = sum(s * w for s, w in scores) / sum(w for _, w in scores)
        
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
        atr = self.compute_atr(df)
        stop_loss = current_price - (2 * atr) if direction == 'BUY' else current_price + (2 * atr)
        take_profit = current_price + (3 * atr) if direction == 'BUY' else current_price - (3 * atr)
        
        reason = f"ARIMA={arima_score:.2f}, Indicators={indicator_score:.2f}, " \
                f"Patterns={pattern_score:.2f}, MC={mc_score:.2f}"
        
        return Signal(
            direction=direction,
            confidence=float(confidence),
            strength=float(weighted_score),
            entry_price=current_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            reason=reason,
            timestamp=datetime.now(timezone.utc)
        )
    
    def _score_arima(self, forecast: Dict[str, Any], current_price: float) -> float:
        """Score the ARIMA forecast (0-1)."""
        if forecast['error'] or len(forecast['forecast']) == 0:
            return 0.5
        
        predicted_direction = forecast['forecast'][-1] > current_price
        score = 0.7 if predicted_direction else 0.3
        
        # Ajustar por confidence
        return score * forecast['confidence']
    
    def _score_indicators(self, indicators: Dict[str, Any]) -> float:
        """Score the technical indicators."""
        # Defensive: ensure indicators is a dict
        if not isinstance(indicators, dict):
            if isinstance(indicators, tuple):
                indicators = next(
                    (item for item in indicators if isinstance(item, dict)),
                    {}
                )
            elif isinstance(indicators, list):
                indicators = indicators[0] if indicators and isinstance(indicators[0], dict) else {}
            else:
                indicators = {}
        
        if not indicators:
            return 0.5
        
        rsi = indicators.get('RSI', 50)
        
        score = 0.5
        
        # RSI oversold = bullish (< 30)
        if rsi < 30:
            score = 0.75
        # RSI overbought = bearish (> 70)
        elif rsi > 70:
            score = 0.25
        # RSI neutral
        elif 40 < rsi < 60:
            score = 0.5
        elif rsi < 50:
            score = 0.6
        else:
            score = 0.4
        
        # MACD - now structured as dict
        macd_data = indicators.get('MACD', {})
        if isinstance(macd_data, dict):
            histogram = macd_data.get('histogram', 0)
        else:
            # Backward compatibility: if still a tuple, extract third element
            try:
                histogram = macd_data[2] if len(macd_data) >= 3 else 0
            except (TypeError, IndexError):
                histogram = 0
        
        if histogram > 0:
            score += 0.1
        else:
            score -= 0.1
        
        return min(max(score, 0.0), 1.0)
    
    def _score_patterns(self, patterns: List[str]) -> float:
        """Score the candle patterns."""
        if not patterns:
            return 0.5
        
        bullish_patterns = {'STRONG_BULLISH', 'HAMMER'}
        bearish_patterns = {'STRONG_BEARISH', 'INVERSE_HAMMER'}
        
        bullish_count = sum(1 for p in patterns if p in bullish_patterns)
        bearish_count = sum(1 for p in patterns if p in bearish_patterns)
        
        if bullish_count > bearish_count:
            return 0.7
        elif bearish_count > bullish_count:
            return 0.3
        else:
            return 0.5
    
    def _score_mc(
        self,
        mc_forecast: Tuple[np.ndarray, np.ndarray, np.ndarray],
        current_price: float
    ) -> float:
        """Score the Monte Carlo forecast."""
        median, upper, lower = mc_forecast
        
        median_direction = median[-1] > current_price
        
        return 0.7 if median_direction else 0.3
    



# ==================== SINGLETON INSTANCE ====================

_analyzer_instance: Optional[StandaloneAnalyzer] = None


def get_analyzer(
    timeout_arima: int = 15,
    timeout_general: int = 30
) -> StandaloneAnalyzer:
    """
    Get or create StandaloneAnalyzer singleton.
    
    Args:
        timeout_arima: Timeout para ARIMA (seconds)
        timeout_general: Timeout general (seconds)
    
    Returns:
        StandaloneAnalyzer instance
    """
    global _analyzer_instance
    
    if _analyzer_instance is None:
        _analyzer_instance = StandaloneAnalyzer(
            timeout_arima_seconds=timeout_arima,
            timeout_general_seconds=timeout_general
        )
    
    return _analyzer_instance
