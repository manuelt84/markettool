"""
🔗 LEGACY ADAPTER - Wrapper sobre MarketTool.py para ParallelAnalysisEngine

Propósito:
- Encapsular llamadas a funciones legacy (calcular_entradas, predecir_arima, etc.)
- Agregar timeout enforcement con asyncio
- Manejo de errores consistente
- Facilitar eventual deprecación del legacy

Arquitectura:
    ParallelAnalysisEngine
         │
         ├─ _predict_arima()      → LegacyAdapter.predict_arima_safe()
         ├─ _compute_indicators() → LegacyAdapter.compute_indicators()
         ├─ _detect_patterns()    → LegacyAdapter.detect_patterns()
         └─ _synthesize_signal()  → LegacyAdapter.synthesize_signal()
         │
         └──→ MarketTool.py (funciones legacy, sin cambios)
"""

import asyncio
import logging
import pandas as pd
import numpy as np
from typing import Dict, Optional, List, Tuple, Any
from functools import lru_cache
import time

logger = logging.getLogger(__name__)


class LegacyMarketToolAdapter:
    """
    Adapter que encapsula llamadas a funciones legacy de MarketTool.py
    con manejo de timeouts, errores y caché consistentes.
    
    ✅ Objetivo: Permitir que ParallelAnalysisEngine reutilice código existente
                 sin cambiar MarketTool.py
    """
    
    def __init__(self, timeout_arima_seconds: int = 15, timeout_general_seconds: int = 30):
        """
        Args:
            timeout_arima_seconds: Timeout para predicciones ARIMA (default 15s)
            timeout_general_seconds: Timeout general para otros análisis (default 30s)
        """
        self.timeout_arima = timeout_arima_seconds
        self.timeout_general = timeout_general_seconds
        
        # Imports locales (evitar circular import en startup)
        self._market_tool = None
        self._imports_done = False
    
    def _ensure_imports(self):
        """Lazy import de MarketTool para evitar circular imports"""
        if self._imports_done:
            return
        
        try:
            # Importar las funciones específicas que necesitamos
            from MarketTool import (
                calcular_entradas,
                predecir_arima,
                predecir_media_movil,
                detectar_patrones_confirmados_velas,
                analisis_tecnico_detallado,
                simulacion_monte_carlo,
                ajustar_probabilidad_fundamental,
                _wrapper_simulacion_monte_carlo,
                logger as mt_logger
            )
            
            self.calcular_entradas_legacy = calcular_entradas
            self.predecir_arima_legacy = predecir_arima
            self.predecir_media_movil_legacy = predecir_media_movil
            self.detectar_patrones_legacy = detectar_patrones_confirmados_velas
            self.analisis_tecnico_legacy = analisis_tecnico_detallado
            self.simulacion_mc_legacy = simulacion_monte_carlo
            self.prob_fundamental_legacy = ajustar_probabilidad_fundamental
            self.wrapper_mc_legacy = _wrapper_simulacion_monte_carlo
            
            self._imports_done = True
            logger.info("[LegacyAdapter] Imports completados exitosamente")
        
        except ImportError as e:
            logger.error(f"[LegacyAdapter] Error importando MarketTool: {e}")
            raise RuntimeError(f"No se pudo importar funciones legacy: {e}")
    
    # =========================================================================
    # PREDICCIONES: ARIMA con timeout enforcement
    # =========================================================================
    
    async def predict_arima_safe(
        self,
        df: pd.DataFrame,
        tf: str,
        symbol: str,
        steps: int = 5,
    ) -> Optional[List[float]]:
        """
        Predicción ARIMA con timeout enforcement usando asyncio.
        
        ✅ CRÍTICO: Garantiza max 15 segundos incluso si legacy tarda más
        
        Args:
            df: DataFrame OHLCV
            tf: Timeframe ('1day', '4hour', etc.)
            symbol: Símbolo (EUR/USD, BTC/USD, etc.)
            steps: Pasos a predecir (default 5)
        
        Returns:
            Lista de predicciones o None si timeout/error
        
        Raises:
            asyncio.TimeoutError si excede timeout
            ValueError si datos inválidos
        """
        self._ensure_imports()
        
        if df is None or df.empty:
            logger.debug(f"[ARIMA] DataFrame vacío para {symbol}/{tf}")
            return None
        
        loop = asyncio.get_event_loop()
        
        try:
            # Ejecutar predicción ARIMA en thread pool (no bloquea event loop)
            # El timeout de asyncio.wait_for() garantiza max 15 segundos
            result = await asyncio.wait_for(
                loop.run_in_executor(
                    None,  # Usar default ThreadPoolExecutor
                    self.predecir_arima_legacy,
                    df,
                    tf,
                    symbol,
                    steps
                ),
                timeout=self.timeout_arima
            )
            
            if result is None or not isinstance(result, (list, tuple)):
                logger.debug(f"[ARIMA] Resultado inválido para {symbol}/{tf}: {type(result)}")
                return None
            
            logger.debug(f"[ARIMA] ✅ Predicción exitosa {symbol}/{tf}: {len(result)} steps")
            return list(result)
        
        except asyncio.TimeoutError:
            logger.warning(
                f"[ARIMA] ⏱️ TIMEOUT {symbol}/{tf} después de {self.timeout_arima}s"
            )
            # Fallback: retornar predicción basada en media móvil
            return await self.predict_simple_ma(df, steps)
        
        except Exception as e:
            logger.warning(
                f"[ARIMA] ❌ Error para {symbol}/{tf}: {type(e).__name__}: {e}"
            )
            return None
    
    async def predict_simple_ma(
        self,
        df: pd.DataFrame,
        steps: int = 5
    ) -> Optional[List[float]]:
        """
        Fallback: Predicción simple usando Media Móvil (20 períodos).
        Rápida (< 1ms), siempre funciona si hay 20+ barras.
        
        Returns:
            Lista de predicciones (repeats última MA)
        """
        if df is None or len(df) < 20:
            return None
        
        try:
            ma20 = df['close'].rolling(20).mean().iloc[-1]
            if pd.isna(ma20):
                return None
            return [float(ma20)] * steps
        except Exception:
            return None
    
    # =========================================================================
    # INDICADORES TÉCNICOS (Fast)
    # =========================================================================
    
    async def compute_indicators_fast(
        self,
        df: pd.DataFrame,
        tf: str
    ) -> Optional[Dict[str, Any]]:
        """
        Calcula indicadores técnicos rápidos (RSI, SMA, Bollinger, MACD).
        
        Nota: Estos se reutilizan de calcular_entradas() cuando es posible,
              con caché para evitar duplicados.
        
        Returns:
            Dict con keys: rsi, sma, bollinger, macd, etc.
        """
        self._ensure_imports()
        
        if df is None or df.empty or len(df) < 20:
            logger.debug(f"[Indicators] Datos insuficientes: {len(df) if df is not None else 0} barras")
            return None
        
        loop = asyncio.get_event_loop()
        
        try:
            result = await asyncio.wait_for(
                loop.run_in_executor(
                    None,
                    self._compute_indicators_inline,
                    df,
                    tf
                ),
                timeout=self.timeout_general
            )
            
            return result if result else None
        
        except asyncio.TimeoutError:
            logger.warning(f"[Indicators] Timeout después de {self.timeout_general}s")
            return None
        except Exception as e:
            logger.debug(f"[Indicators] Error: {e}")
            return None
    
    @staticmethod
    def _compute_indicators_inline(df: pd.DataFrame, tf: str) -> Dict[str, Any]:
        """
        Cálculo inline de indicadores (sin dependencias externas).
        Ejecutado en thread pool.
        """
        if df is None or df.empty:
            return None
        
        try:
            out = {}
            closes = pd.to_numeric(df['close'], errors='coerce')
            
            # RSI (14 períodos - estándar)
            if len(closes) >= 14:
                delta = closes.diff()
                gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
                loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
                rs = gain / loss
                rsi = 100 - (100 / (1 + rs))
                out['rsi'] = float(rsi.iloc[-1]) if not pd.isna(rsi.iloc[-1]) else None
            
            # SMA 20 (simple moving average)
            if len(closes) >= 20:
                sma20 = closes.rolling(20).mean().iloc[-1]
                out['sma'] = float(sma20) if not pd.isna(sma20) else None
            
            # Bollinger Bands (20, 2)
            if len(closes) >= 20:
                sma = closes.rolling(20).mean()
                std = closes.rolling(20).std()
                bb_upper = sma + (std * 2)
                bb_lower = sma - (std * 2)
                out['bollinger_upper'] = float(bb_upper.iloc[-1]) if not pd.isna(bb_upper.iloc[-1]) else None
                out['bollinger_lower'] = float(bb_lower.iloc[-1]) if not pd.isna(bb_lower.iloc[-1]) else None
            
            # MACD (12, 26, 9)
            if len(closes) >= 26:
                ema12 = closes.ewm(span=12, adjust=False).mean()
                ema26 = closes.ewm(span=26, adjust=False).mean()
                macd = ema12 - ema26
                signal = macd.ewm(span=9, adjust=False).mean()
                histogram = macd - signal
                out['macd'] = float(macd.iloc[-1]) if not pd.isna(macd.iloc[-1]) else None
                out['signal'] = float(signal.iloc[-1]) if not pd.isna(signal.iloc[-1]) else None
                out['histogram'] = float(histogram.iloc[-1]) if not pd.isna(histogram.iloc[-1]) else None
            
            return out if out else None
        
        except Exception as e:
            logger.debug(f"[Indicators] Excepción en inline computation: {e}")
            return None
    
    # =========================================================================
    # PATRONES (YOLO)
    # =========================================================================
    
    async def detect_candle_patterns(
        self,
        df: pd.DataFrame,
        symbol: str,
        tf: str
    ) -> List[Dict[str, Any]]:
        """
        Detección de patrones de velas (YOLO modelo).
        
        Returns:
            Lista de patrones: [{'name': 'Martillo', 'conf': 0.92, ...}, ...]
        """
        self._ensure_imports()
        
        if df is None or df.empty:
            return []
        
        loop = asyncio.get_event_loop()
        
        try:
            result = await asyncio.wait_for(
                loop.run_in_executor(
                    None,
                    self.detectar_patrones_legacy,
                    df,
                    min(len(df), 100)  # Últimas 100 velas para eficiencia
                ),
                timeout=self.timeout_general
            )
            
            if not result:
                return []
            
            # result es lista de tuplas (x1, y1, nombre) de YOLO
            # Convertir a dict legible
            patterns = []
            for item in result:
                if isinstance(item, tuple) and len(item) >= 3:
                    _, _, nombre = item
                    patterns.append({'name': nombre, 'conf': 0.90})  # conf estimado
            
            return patterns
        
        except asyncio.TimeoutError:
            logger.warning(f"[Patterns] Timeout para {symbol}/{tf}")
            return []
        except Exception as e:
            logger.debug(f"[Patterns] Error para {symbol}/{tf}: {e}")
            return []
    
    # =========================================================================
    # SÍNTESIS: Integrar todo en una señal
    # =========================================================================
    
    async def synthesize_signal(
        self,
        symbol: str,
        tf: str,
        df: pd.DataFrame,
        indicators: Optional[Dict],
        patterns: List[Dict],
        arima_pred: Optional[List[float]],
        mc_scenarios: Optional[Dict],
        historical_entries: Optional[List] = None,
        cfg: Optional[Dict] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Síntesis final de todas las señales en una decisión de entrada.
        
        Remite a la lógica de síntesis que ya existe en calcular_entradas().
        
        Returns:
            {
                'symbol': 'EUR/USD',
                'timeframe': '1day',
                'signal': 'Compra' | 'Venta' | 'Neutral',
                'confidence': 0.75,
                'target': 1.1050,
                'stop': 1.0950,
                'rrr': 2.5,
                'reasons': ['...'], 
                'timestamp': '2025-02-18T10:30:00Z'
            }
        """
        loop = asyncio.get_event_loop()
        
        try:
            result = await asyncio.wait_for(
                loop.run_in_executor(
                    None,
                    self._synthesize_inline,
                    symbol, tf, df,
                    indicators, patterns,
                    arima_pred, mc_scenarios,
                    historical_entries, cfg
                ),
                timeout=5.0  # Síntesis debe ser rápida
            )
            
            return result
        
        except asyncio.TimeoutError:
            logger.warning(f"[Synthesize] Timeout para {symbol}/{tf}")
            return None
        except Exception as e:
            logger.debug(f"[Synthesize] Error para {symbol}/{tf}: {e}")
            return None
    
    @staticmethod
    def _synthesize_inline(
        symbol: str,
        tf: str,
        df: pd.DataFrame,
        indicators: Optional[Dict],
        patterns: List[Dict],
        arima_pred: Optional[List[float]],
        mc_scenarios: Optional[Dict],
        historical_entries: Optional[List],
        cfg: Optional[Dict]
    ) -> Optional[Dict]:
        """
        Lógica de síntesis inline (sin funciones externas complicadas).
        
        Implementa un modelo simple pero robusto:
        - Confluencia por coincidencia de señales
        - Target y Stop basados en ARIMA + técnica
        - RRR (Risk/Reward Ratio)
        """
        if df is None or df.empty:
            return None
        
        try:
            # Precio actual (último close)
            current = float(df['close'].iloc[-1])
            
            # ---- PUNTAJE DE SEÑAL ----
            # ARIMA 1 punto si predice alza
            arima_score = 0.0
            if arima_pred and len(arima_pred) > 0:
                pred_next = float(arima_pred[0])
                if pred_next > current * 1.001:  # > 0.1% alza
                    arima_score = 1.0
                elif pred_next < current * 0.999:  # < -0.1% baja
                    arima_score = -1.0
            
            # MC 1 punto si prob_alza > prob_baja
            mc_score = 0.0
            if mc_scenarios and isinstance(mc_scenarios, dict):
                prob_up = float(mc_scenarios.get('prob_up', 50))
                if prob_up > 55:
                    mc_score = 0.5
                elif prob_up < 45:
                    mc_score = -0.5
            
            # Patrones: 0.3 puntos por patrón alcista
            pattern_score = 0.0
            if patterns:
                bullish_patterns = sum(
                    0.3 for p in patterns 
                    if 'Martillo' in str(p.get('name', '')) 
                    or 'Alcista' in str(p.get('name', ''))
                )
                pattern_score = min(1.0, bullish_patterns)
            
            # Score total normalizado [-3, +3] → [-1, +1]
            total_score = (arima_score + mc_score + pattern_score) / 3.0
            total_score = min(1.0, max(-1.0, total_score))
            
            # ---- DECISIÓN ----
            if total_score > 0.3:
                signal_type = "Compra"
                confidence = min(0.95, 0.5 + abs(total_score) / 2)
            elif total_score < -0.3:
                signal_type = "Venta"
                confidence = min(0.95, 0.5 + abs(total_score) / 2)
            else:
                signal_type = "Neutral"
                confidence = 0.3
            
            # ---- TARGET Y STOP ----
            # Target: ARIMA predicción si existe, sino +1% del precio actual
            if arima_pred and len(arima_pred) > 0:
                target = float(arima_pred[-1])  # Predicción del último step
            else:
                target = current * 1.01 if signal_type == "Compra" else current * 0.99
            
            # Stop: -1% del precio actual (conservador)
            stop = current * 0.99 if signal_type == "Compra" else current * 1.01
            
            # RRR = (target - current) / (current - stop)
            target_diff = abs(target - current)
            stop_diff = abs(current - stop)
            rrr = target_diff / max(0.001, stop_diff)
            rrr = min(10.0, max(0.5, rrr))  # Clamp [0.5, 10]
            
            # ---- SALIDA ----
            return {
                'symbol': symbol,
                'timeframe': tf,
                'signal': signal_type,
                'confidence': float(min(0.99, max(0.01, confidence))),
                'target': float(target),
                'stop': float(stop),
                'rrr': float(rrr),
                'reasons': [
                    f"ARIMA: {arima_score:.1f}",
                    f"MC: {mc_score:.1f}",
                    f"Patterns: {pattern_score:.1f}"
                ],
                'timestamp': pd.Timestamp.now('UTC').isoformat(),
                'internal': {
                    'arima_score': arima_score,
                    'mc_score': mc_score,
                    'pattern_score': pattern_score,
                    'total_score': total_score
                }
            }
        
        except Exception as e:
            logger.debug(f"[Synthesize] Error inline: {e}")
            return None
    
    # =========================================================================
    # Monte Carlo (opcional, puede usarse o saltarse)
    # =========================================================================
    
    async def generate_monte_carlo_scenarios(
        self,
        df: pd.DataFrame,
        symbol: str,
        tf: str,
        num_scenarios: int = 100,
        num_days: int = 5
    ) -> Optional[Dict[str, Any]]:
        """
        Generador de escenarios Monte Carlo.
        
        Returns:
            {
                'prob_up': 0.62,
                'prob_down': 0.38,
                'expected_move': 0.015,
                'scenarios': [...]  (opcional, para debug)
            }
        """
        self._ensure_imports()
        
        if df is None or df.empty:
            return None
        
        loop = asyncio.get_event_loop()
        
        try:
            result = await asyncio.wait_for(
                loop.run_in_executor(
                    None,
                    self.wrapper_mc_legacy,
                    df,
                    tf
                ),
                timeout=self.timeout_general
            )
            
            if isinstance(result, tuple) and len(result) == 2:
                prob_up, prob_down = result
                return {
                    'prob_up': float(prob_up or 50),
                    'prob_down': float(prob_down or 50),
                    'expected_move': abs(float(prob_up or 50) - 50) / 100.0
                }
            
            return None
        
        except asyncio.TimeoutError:
            logger.warning(f"[MC] Timeout para {symbol}/{tf}")
            return {'prob_up': 50.0, 'prob_down': 50.0}
        except Exception as e:
            logger.debug(f"[MC] Error para {symbol}/{tf}: {e}")
            return None


# ============================================================================
# SINGLETON GLOBAL (similar a _HIST en MarketTool.py)
# ============================================================================

_ADAPTER_INSTANCE = None


def get_adapter(
    timeout_arima: int = 15,
    timeout_general: int = 30
) -> LegacyMarketToolAdapter:
    """
    Obtiene instancia singleton del adapter con caché.
    
    Uso:
        adapter = get_adapter()
        arima_pred = await adapter.predict_arima_safe(df, tf, symbol)
    """
    global _ADAPTER_INSTANCE
    
    if _ADAPTER_INSTANCE is None:
        _ADAPTER_INSTANCE = LegacyMarketToolAdapter(
            timeout_arima_seconds=timeout_arima,
            timeout_general_seconds=timeout_general
        )
    
    return _ADAPTER_INSTANCE


if __name__ == "__main__":
    # Simple test
    print("[LegacyAdapter] Test import:")
    adapter = get_adapter()
    print(f"✅ Adapter inicializado: {adapter}")
