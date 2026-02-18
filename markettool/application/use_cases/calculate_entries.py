"""
Use Case: Calculate Trading Entry Signals

Hexagonal architecture implementation of trading signal calculation.
Integrates technical analysis, support/resistance, and fundamental analysis.
"""

import asyncio
import logging
import pandas as pd
import numpy as np
from typing import Dict, Optional, List, Any
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor

from markettool.application.adapters import get_analyzer
from markettool.application.services import (
    get_sr_service,
    get_fundamental_service,
)

logger = logging.getLogger(__name__)


class CalculateEntriesUseCase:
    """
    Calculates trading entry signals with full hexagonal architecture.
    
    Integrates:
    - Technical analysis (StandaloneAnalyzer)
    - Support/Resistance detection
    - Fundamental analysis
    - Risk management
    - Trading validations
    """
    
    def __init__(
        self,
        logger: Optional[logging.Logger] = None,
        max_workers: int = 4
    ):
        self.logger = logger or logging.getLogger(__name__)
        self.analyzer = get_analyzer()
        self.sr_service = get_sr_service(logger=logger)
        self.fundamental_service = get_fundamental_service(logger=logger)
        self.executor = ThreadPoolExecutor(max_workers=max_workers)
    
    async def execute(
        self,
        df: pd.DataFrame,
        df_eventos: pd.DataFrame,
        symbol: str,
        timeframe: str,
        user_chat_id: str = None,
        config: Optional[Dict] = None
    ) -> Dict[str, Any]:
        """
        Calculate entry signals using complete hexagonal analysis.
        
        Args:
            df: OHLCV DataFrame with price data
            df_eventos: Economic events DataFrame
            symbol: Trading symbol
            timeframe: Timeframe (1day, 1h, etc.)
            user_chat_id: User chat ID for logging
            config: Configuration dictionary
        
        Returns:
            Dict with entry signal analysis
        """
        try:
            # === Step 1: Technical Analysis ===
            technical_result = await self._analyze_technical(df, timeframe)
            
            # === Step 2: Support/Resistance ===
            sr_result = self._analyze_support_resistance(df, symbol, timeframe)
            
            # === Step 3: Fundamental Analysis ===
            fundamental_result = await self._analyze_fundamental(
                df_eventos,
                symbol,
                timeframe
            )
            
            # === Step 4: Decision Logic ===
            entry_signal = await self._determine_signal(
                df,
                technical_result,
                sr_result,
                fundamental_result,
                config or {}
            )
            
            # === Return Results ===
            return {
                'tipo_operacion': entry_signal['direction'],
                'probabilidad_alza': entry_signal.get('prob_up', 50),
                'probabilidad_baja': entry_signal.get('prob_down', 50),
                'probabilidad_tecnica': entry_signal.get('technical_prob', 50),
                'probabilidad_fundamental': entry_signal.get('fundamental_prob', 50),
                'confianza': entry_signal.get('confidence', 0),
                'razon': entry_signal.get('reason', ''),
                'niveles': {
                    's1': sr_result.get('s1'),
                    's2': sr_result.get('s2'),
                    'r1': sr_result.get('r1'),
                    'r2': sr_result.get('r2'),
                },
                'atr': sr_result.get('atr'),
                'is_range': sr_result.get('is_range', False),
                'structure': sr_result.get('structure', 'undefined'),
                'technical': technical_result,
                'fundamental': fundamental_result,
            }
        
        except Exception as e:
            self.logger.error(f"Error in CalculateEntriesUseCase: {e}", exc_info=True)
            # Return neutral signal on error
            return {
                'tipo_operacion': 'Neutral',
                'probabilidad_alza': 50,
                'probabilidad_baja': 50,
                'confianza': 0,
                'razon': f'Error: {str(e)}',
                'error': str(e),
            }
    
    # ==================== PRIVATE METHODS ====================
    
    async def _analyze_technical(
        self,
        df: pd.DataFrame,
        timeframe: str
    ) -> Dict[str, Any]:
        """Perform complete technical analysis."""
        try:
            # Compute all indicators
            indicators = self.analyzer.compute_all_indicators(df)
            
            # Detect patterns
            patterns = self.analyzer.detect_candle_patterns(df)
            
            # ARIMA prediction
            arima_result = await self.analyzer.predict_arima_async(
                df, timeframe, symbol='UNKNOWN', steps=5
            )
            
            # Monte Carlo
            mc_median, mc_upper, mc_lower = self.analyzer.monte_carlo_forecast(
                df, num_simulations=100, num_days=5
            )
            
            current_price = float(df['close'].iloc[-1])
            
            return {
                'indicators': indicators,
                'patterns': patterns,
                'arima': arima_result,
                'monte_carlo': {
                    'median': mc_median,
                    'upper': mc_upper,
                    'lower': mc_lower,
                },
                'current_price': current_price,
                'atr': indicators.get('ATR', current_price * 0.01),
                'rsi': indicators.get('RSI', 50),
                'stochastic': indicators.get('Stochastic', {'k': 50, 'd': 50}),
            }
        
        except Exception as e:
            self.logger.warning(f"Technical analysis error: {e}")
            return {'error': str(e)}
    
    def _analyze_support_resistance(
        self,
        df: pd.DataFrame,
        symbol: str,
        timeframe: str
    ) -> Dict[str, Any]:
        """Detect support/resistance and range."""
        try:
            # Calculate window
            window = min(50, len(df))  # Simplified window calculation
            
            # Get S/R levels
            sr_levels = self.sr_service.calculate_support_resistance(
                df,
                window=window,
                atr_multiplier=2.0,
                min_levels=2
            )
            
            # Detect range
            range_result = self.sr_service.detect_zigzag_range(df)
            
            # Get key levels
            key_levels = self.sr_service.get_key_levels(
                df,
                sr_levels.supports,
                sr_levels.resistances,
                atr_threshold=2.0,
                max_levels=2
            )
            
            return {
                'supports': sr_levels.supports,
                'resistances': sr_levels.resistances,
                'atr': sr_levels.atr,
                'is_range': range_result.is_range,
                'structure': range_result.structure,
                's1': key_levels.get('s1'),
                's2': key_levels.get('s2'),
                'r1': key_levels.get('r1'),
                'r2': key_levels.get('r2'),
            }
        
        except Exception as e:
            self.logger.warning(f"S/R analysis error: {e}")
            return {
                'error': str(e),
                'is_range': False,
                'structure': 'undefined',
                'atr': 0,
            }
    
    async def _analyze_fundamental(
        self,
        df_eventos: pd.DataFrame,
        symbol: str,
        timeframe: str
    ) -> Dict[str, Any]:
        """Perform fundamental analysis."""
        try:
            # Base probability
            base_prob = 50.0
            
            # Adjust with economic events
            adjusted_prob, meta = self.fundamental_service.adjust_probability_with_events(
                base_prob,
                df_eventos,
                symbol,
                timeframe,
                date_start='2026-02-11',  # Last 7 days
                date_end='2026-02-18'
            )
            
            return {
                'adjusted_probability': adjusted_prob,
                'base_probability': base_prob,
                'events_impact': meta.get('impact', 0),
                'events_count': meta.get('events_found', 0),
                'metadata': meta,
            }
        
        except Exception as e:
            self.logger.warning(f"Fundamental analysis error: {e}")
            return {
                'error': str(e),
                'adjusted_probability': 50.0,
                'base_probability': 50.0,
            }
    
    async def _determine_signal(
        self,
        df: pd.DataFrame,
        technical: Dict[str, Any],
        sr: Dict[str, Any],
        fundamental: Dict[str, Any],
        config: Dict
    ) -> Dict[str, Any]:
        """Determine final entry signal."""
        
        if 'error' in technical or 'error' in sr:
            return {
                'direction': 'Neutral',
                'prob_up': 50,
                'prob_down': 50,
                'technical_prob': 50,
                'fundamental_prob': 50,
                'confidence': 0,
                'reason': 'Analysis error'
            }
        
        # Synthesize signal using StandaloneAnalyzer
        try:
            signal = await self.analyzer.synthesize_signal(
                df=df,
                symbol='UNKNOWN',
                timeframe='1day',
                arima_forecast=technical.get('arima', {}),
                indicators=technical.get('indicators', {}),
                patterns=technical.get('patterns', []),
                mc_forecast=(
                    technical['monte_carlo'].get('median'),
                    technical['monte_carlo'].get('upper'),
                    technical['monte_carlo'].get('lower'),
                )
            )
        except Exception as e:
            self.logger.warning(f"Signal synthesis error: {e}")
            signal = None
        
        # Get probabilities
        technical_prob = 70 if signal and signal.direction == 'BUY' else (30 if signal and signal.direction == 'SELL' else 50)
        fundamental_prob = fundamental.get('adjusted_probability', 50)
        
        # Combine probabilities
        combined_prob = (technical_prob * 0.6 + fundamental_prob * 0.4)
        
        # Determine direction
        if combined_prob > 65:
            direction = 'Compra'
            prob_up = int(combined_prob)
            prob_down = 100 - prob_up
        elif combined_prob < 35:
            direction = 'Venta'
            prob_down = int(100 - combined_prob)
            prob_up = 100 - prob_down
        else:
            direction = 'Neutral'
            prob_up = 50
            prob_down = 50
        
        # Apply range filter
        if sr.get('is_range'):
            direction = 'Neutral'  # Skip trading in ranges
        
        return {
            'direction': direction,
            'prob_up': prob_up,
            'prob_down': prob_down,
            'technical_prob': technical_prob,
            'fundamental_prob': int(fundamental_prob),
            'confidence': signal.confidence if signal else 0,
            'reason': signal.reason if signal else 'Combined analysis',
        }


# ==================== FACTORY ====================

def get_calculate_entries_use_case(
    logger: Optional[logging.Logger] = None
) -> CalculateEntriesUseCase:
    """Get CalculateEntriesUseCase instance."""
    return CalculateEntriesUseCase(logger=logger)
