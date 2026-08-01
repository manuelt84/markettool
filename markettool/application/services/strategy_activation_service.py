"""
Strategy Activation Service

Implements differential activation logic for trading strategies based on:
- Multi-timeframe context availability
- Strategy type (critical/high/medium/low MTF dependency)
- Mode (backtest vs live)
- HTF staleness in live mode

This service determines whether a strategy should be activated for a given
timeframe and context, returning activation status, weight, and reasoning.

Based on: memory/MASTER_LOGICA_NEGOCIO_MARKETTOOL_2026-07-31.md
"""

import logging
from typing import Dict, Optional, Any, Tuple, List
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)


class MTFDependencyLevel(Enum):
    """Multi-timeframe dependency classification"""
    CRITICAL = "critical"      # Requires MTF in backtest, conservative in live
    HIGH = "high"              # Benefits significantly from MTF
    MEDIUM = "medium"          # Works well single-TF, improves with MTF
    LOW = "low"                # Can operate single-TF without problems


class ActivationMode(Enum):
    """Activation mode: backtest or live"""
    BACKTEST = "backtest"
    LIVE = "live"


@dataclass
class MultiTFContext:
    """Multi-timeframe context for strategy activation"""
    enabled: bool
    higher_tf_1: Optional[Dict[str, Any]] = None  # First higher TF data
    higher_tf_2: Optional[Dict[str, Any]] = None  # Second higher TF data
    staleness_min: float = 0.0                    # Minutes since HTF last closed
    inference_available: bool = False             # Can infer current HTF direction


@dataclass
class ActivationResult:
    """Result of strategy activation check"""
    activate: bool
    reason: str
    weight: float                               # Weight to apply (0.0-1.0)
    confidence_adjustment: float = 0.0          # Additional confidence boost/cut
    warnings: List[str] = field(default_factory=list)
    fallback_mode: Optional[str] = None         # e.g., 'intra_tf_only', 'liquidity_sweeps_only'


# ==================== STRATEGY CONFIGURATIONS ====================

STRATEGY_CONFIGS: Dict[str, Dict[str, Any]] = {
    # 🔴 CRITICAL - Requieren MTF en Backtesting, Conservadoras en Vivo
    'ob': {
        'source_id': 'ob',
        'name': 'Order Blocks',
        'dependency_level': MTFDependencyLevel.CRITICAL,
        
        'backtest': {
            'requires_mtf': True,
            'min_tf': '5m',
            'max_tf': '1d',
            'optimal_tfs': ['15m', '30m', '1h', '4h'],
            'weight_by_tf': {
                '1m': 0.0,   # NO activar - demasiado ruido
                '5m': 0.5,   # Solo si 15m+ disponible
                '15m': 1.0,
                '30m': 1.0,
                '1h': 1.0,
                '4h': 0.9,
                '1d': 0.8,
                '1w': 0.5,
            },
            'win_rate_single_tf': 0.42,
            'win_rate_with_mtf': 0.64,
            'improvement': '+22%',
        },
        
        'live': {
            'conservative_mode': True,
            'fallback_to_intra_tf': True,
            'staleness_threshold_min': 30,
            'inference_allowed': True,
            'weight_when_stale': 0.3,
            'win_rate_expected': 0.55,
        },
    },
    
    'smc': {
        'source_id': 'smc',
        'name': 'Smart Money Concepts (BOS, CHoCh, Liquidity Sweeps)',
        'dependency_level': MTFDependencyLevel.CRITICAL,
        
        'backtest': {
            'requires_mtf': True,
            'min_tf': '5m',
            'max_tf': '1d',
            'optimal_tfs': ['15m', '30m', '1h', '4h'],
            'weight_by_tf': {
                '1m': 0.0,   # NO activar - BOS/CHoCh en 1m es ruido
                '5m': 0.4,   # Solo con 15m+ confirmado
                '15m': 1.0,
                '30m': 1.0,
                '1h': 1.0,
                '4h': 0.9,
                '1d': 0.7,
            },
            'components': {
                'bos': {'requires_mtf': True, 'win_rate_single_tf': 0.45, 'win_rate_with_mtf': 0.68},
                'choch': {'requires_mtf': True, 'win_rate_single_tf': 0.43, 'win_rate_with_mtf': 0.66},
                'liquidity_sweep': {'requires_mtf': False, 'win_rate_single_tf': 0.52, 'win_rate_with_mtf': 0.61},
            },
        },
        
        'live': {
            'conservative_mode': True,
            'fallback_to_intra_tf': False,      # NO activar BOS/CHoCh sin HTF fresco
            'activate_liquidity_sweeps_only': True,  # En modo conservador, solo liquidity sweeps
            'staleness_threshold_min': 20,
            'inference_allowed': True,
        },
    },
    
    'breaker': {
        'source_id': 'breaker',
        'name': 'Breaker Blocks',
        'dependency_level': MTFDependencyLevel.CRITICAL,
        
        'backtest': {
            'requires_mtf': True,
            'min_tf': '5m',
            'max_tf': '1d',
            'optimal_tfs': ['15m', '30m', '1h'],
            'weight_by_tf': {
                '1m': 0.0,
                '5m': 0.4,
                '15m': 1.0,
                '30m': 1.0,
                '1h': 0.9,
                '4h': 0.8,
                '1d': 0.6,
            },
            'win_rate_single_tf': 0.38,
            'win_rate_with_mtf': 0.61,
            'improvement': '+23%',
        },
        
        'live': {
            'conservative_mode': True,
            'fallback_to_intra_tf': True,
            'staleness_threshold_min': 25,
            'inference_allowed': True,
            'weight_when_stale': 0.35,
        },
    },
    
    # 🟠 HIGH - Benefician Significativamente de MTF
    'fvg': {
        'source_id': 'fvg',
        'name': 'Fair Value Gaps / Imbalances',
        'dependency_level': MTFDependencyLevel.HIGH,
        
        'backtest': {
            'requires_mtf': False,
            'ponderate_by_tf': True,
            'weight_by_tf': {
                '1m': 0.3,
                '5m': 0.5,
                '15m': 0.9,
                '30m': 1.0,
                '1h': 1.0,
                '4h': 0.9,
                '1d': 0.8,
                '1w': 0.6,
            },
            'win_rate_single_tf': 0.48,
            'win_rate_weighted': 0.56,
            'improvement': '+8%',
        },
        
        'live': {
            'conservative_mode': False,
            'apply_tf_ponderation': True,
            'infer_respect_probability': True,
        },
    },
    
    'inducement': {
        'source_id': 'inducement',
        'name': 'Inducement / Liquidity Traps',
        'dependency_level': MTFDependencyLevel.HIGH,
        
        'backtest': {
            'requires_mtf': False,
            'recommended_mtf': True,
            'min_tf': '5m',
            'max_tf': '4h',
            'optimal_tfs': ['15m', '30m', '1h'],
            'weight_by_tf': {
                '1m': 0.3,
                '5m': 0.6,
                '15m': 1.0,
                '30m': 1.0,
                '1h': 0.9,
                '4h': 0.7,
                '1d': 0.5,
            },
            'win_rate_single_tf': 0.44,
            'win_rate_with_htf_pools': 0.59,
            'improvement': '+15%',
        },
        
        'live': {
            'conservative_mode': False,
            'infer_liquidity_pools': True,
            'staleness_threshold_min': 40,
        },
    },
    
    'confluence': {
        'source_id': 'confluence',
        'name': 'Confluence Mega Setups',
        'dependency_level': MTFDependencyLevel.HIGH,
        
        'backtest': {
            'requires_mtf_alignment': True,
            'intra_tf_factors': [
                'smc_concepts', 'order_blocks', 'fvg', 'breakers',
                'inducements', 'divergences', 'fibonacci',
            ],
            'mtf_alignment_boost': {
                'aligned_3TF': 0.20,
                'aligned_2TF': 0.10,
                'aligned_1TF': -0.15,
            },
            'max_score': 100,
        },
        
        'live': {
            'calculate_mtf_partial': True,
            'reduced_boost_for_uncertainty': True,
            'mtf_alignment_boost': {
                'aligned_3TF': 0.15,
                'aligned_2TF': 0.08,
                'aligned_1TF': -0.10,
            },
            'include_staleness_warning': True,
        },
    },
    
    # 🟡 MEDIUM - Funcionan Bien Single-TF, Mejoran con MTF
    'fibonacci': {
        'source_id': 'fibonacci',
        'name': 'Fibonacci Zones',
        'dependency_level': MTFDependencyLevel.MEDIUM,
        
        'backtest': {
            'requires_mtf': False,
            'cluster_multi_tf': True,
            'min_tf': '5m',
            'max_tf': '1d',
            'optimal_tfs': ['15m', '30m', '1h', '4h'],
            'weight_by_tf': {
                '1m': 0.4,
                '5m': 0.6,
                '15m': 1.0,
                '30m': 1.0,
                '1h': 0.9,
                '4h': 0.8,
                '1d': 0.7,
                '1w': 0.5,
            },
            'levels': [0.236, 0.382, 0.5, 0.618, 0.786, 1.0],
            'cluster_bonus': 0.15,
            'win_rate_single_tf': 0.51,
            'win_rate_with_cluster': 0.62,
            'improvement': '+11%',
        },
        
        'live': {
            'detect_clusters': True,
            'apply_cluster_bonus': True,
        },
    },
    
    'divergence': {
        'source_id': 'divergence',
        'name': 'Divergencias RSI/MACD',
        'dependency_level': MTFDependencyLevel.MEDIUM,
        
        'backtest': {
            'requires_mtf': False,
            'recommended_filter': True,
            'min_tf': '5m',
            'max_tf': '1d',
            'optimal_tfs': ['15m', '30m', '1h', '4h'],
            'weight_by_tf': {
                '1m': 0.4,
                '5m': 0.7,
                '15m': 1.0,
                '30m': 1.0,
                '1h': 0.9,
                '4h': 0.8,
                '1d': 0.7,
            },
            'types': {
                'regular_bullish': 'Precio hace low más bajo, RSI/MACD hace low más alto',
                'regular_bearish': 'Precio hace high más alto, RSI/MACD hace high más bajo',
                'hidden_bullish': 'Tendencia alcista, retroceso con RSI/MACD fuerte',
                'hidden_bearish': 'Tendencia bajista, retroceso con RSI/MACD débil',
            },
            'win_rate_single_tf': 0.47,
            'win_rate_with_htf_filter': 0.58,
            'improvement': '+11%',
        },
        
        'live': {
            'filter_by_htf_trend': True,
            'staleness_threshold_min': 35,
        },
    },
    
    # 🟢 LOW - Pueden Operar Single-TF Sin Problemas
    'tech': {
        'source_id': 'tech',
        'name': 'Technical Signals (RSI, MACD, BB, EMA, Stoch)',
        'dependency_level': MTFDependencyLevel.LOW,
        
        'backtest': {
            'requires_mtf': False,
            'min_tf': '1m',
            'max_tf': '1w',
            'optimal_tfs': ['5m', '15m', '1h'],
            'weight_by_tf': {
                '1m': 0.7,
                '5m': 1.0,
                '15m': 1.0,
                '30m': 0.9,
                '1h': 0.9,
                '4h': 0.8,
                '1d': 0.7,
                '1w': 0.5,
            },
            'indicators': ['RSI', 'MACD', 'Bollinger Bands', 'EMA', 'Stochastic'],
            'win_rate_single_tf': 0.49,
            'win_rate_optimal': 0.52,
            'improvement': '+3% (marginal)',
        },
        
        'live': {
            'conservative_mode': False,
            'no_mtf_required': True,
        },
    },
    
    'candle': {
        'source_id': 'candle',
        'name': 'Candle Strategies',
        'dependency_level': MTFDependencyLevel.LOW,
        
        'backtest': {
            'requires_mtf': False,
            'min_tf': '1m',
            'max_tf': '1d',
            'optimal_tfs': ['5m', '15m', '30m'],
            'weight_by_tf': {
                '1m': 0.6,
                '5m': 1.0,
                '15m': 1.0,
                '30m': 0.9,
                '1h': 0.8,
                '4h': 0.7,
                '1d': 0.6,
                '1w': 0.3,
            },
            'patterns': [
                'ema_cross_3_9', 'triada', 'engulfing_reclaim',
                'inside_bar_breakout', 'opening_reclaim',
                'pinbar_reversal', 'three_bar_reversal',
            ],
            'win_rate_single_tf': 0.50,
            'win_rate_optimal': 0.54,
            'improvement': '+4% (marginal)',
        },
        
        'live': {
            'conservative_mode': False,
            'no_mtf_required': True,
        },
    },
    
    'sr': {
        'source_id': 'sr',
        'name': 'Support/Resistance Bounce',
        'dependency_level': MTFDependencyLevel.LOW,
        
        'backtest': {
            'requires_mtf': False,
            'recommended_mtf': True,
            'min_tf': '1m',
            'max_tf': '1w',
            'optimal_tfs': ['15m', '30m', '1h', '4h'],
            'weight_by_tf': {
                '1m': 0.5,
                '5m': 0.7,
                '15m': 1.0,
                '30m': 1.0,
                '1h': 1.0,
                '4h': 0.9,
                '1d': 0.8,
                '1w': 0.7,
            },
            'win_rate_single_tf': 0.53,
            'win_rate_with_confluence': 0.58,
            'improvement': '+5% (marginal)',
        },
        
        'live': {
            'conservative_mode': False,
            'use_historical_levels': True,
        },
    },
}

# ==================== GLOBAL CONFIG ====================

GLOBAL_CONFIG = {
    'staleness_threshold_conservative': 30,  # Minutos máximos para modo conservador
    'staleness_threshold_moderate': 45,      # Minutos máximos para modo moderado
    'min_weight_threshold': 0.3,             # Si peso < 0.3, no activar estrategia
    'min_sample_size_for_ranking': 30,       # Mínimo samples para estadística válida
    'max_signals_per_tf': 10,                # Máximo señales por TF
    'max_signals_global': 50,                # Máximo señales totales
}


# ==================== TIMEFRAME UTILITIES ====================

TIMEFRAME_ORDER = ['1m', '5m', '15m', '30m', '1h', '4h', '1d', '1w']

TIMEFRAME_MINUTES = {
    '1m': 1, '5m': 5, '15m': 15, '30m': 30,
    '1h': 60, '4h': 240, '1d': 1440, '1w': 10080,
}


def get_higher_timeframes(timeframe: str, available_tfs: List[str]) -> List[str]:
    """Get higher timeframes from available selection"""
    try:
        tf_index = TIMEFRAME_ORDER.index(timeframe)
        higher_tfs = [tf for tf in TIMEFRAME_ORDER[tf_index + 1:] if tf in available_tfs]
        return higher_tfs[:2]  # Return up to 2 higher TFs
    except ValueError:
        return []


def is_timeframe_higher(tf1: str, tf2: str) -> bool:
    """Check if tf1 is higher than tf2"""
    try:
        return TIMEFRAME_ORDER.index(tf1) > TIMEFRAME_ORDER.index(tf2)
    except ValueError:
        return False


# ==================== STRATEGY ACTIVATION SERVICE ====================

class StrategyActivationService:
    """
    Determines whether a strategy should be activated based on:
    - Strategy configuration
    - Current timeframe
    - Multi-timeframe context availability
    - Mode (backtest vs live)
    - HTF staleness (live mode only)
    """
    
    def __init__(self, logger=None):
        self.logger = logger or logging.getLogger(__name__)
        self.configs = STRATEGY_CONFIGS
        self.global_config = GLOBAL_CONFIG
    
    def should_activate_strategy(
        self,
        source_id: str,
        timeframe: str,
        mode: ActivationMode,
        multi_tf_context: Optional[MultiTFContext] = None,
        available_tfs: Optional[List[str]] = None,
    ) -> ActivationResult:
        """
        Determine if a strategy should be activated.
        
        Args:
            source_id: Strategy source identifier (e.g., 'ob', 'smc', 'tech')
            timeframe: Current timeframe (e.g., '5m', '1h')
            mode: Activation mode (backtest or live)
            multi_tf_context: Multi-timeframe context if available
            available_tfs: List of user-selected timeframes
        
        Returns:
            ActivationResult with activation decision, weight, and reasoning
        """
        config = self.configs.get(source_id)
        if not config:
            return ActivationResult(
                activate=False,
                reason=f"Unknown strategy: {source_id}",
                weight=0.0,
            )
        
        self.logger.debug(
            "[StrategyActivation] Checking %s (%s) for %s/%s with MTF=%s",
            config['name'], source_id, timeframe, mode.value,
            multi_tf_context.enabled if multi_tf_context else False
        )
        
        # Check timeframe bounds
        mode_config = config.get(mode.value, {})
        min_tf = mode_config.get('min_tf', '1m')
        max_tf = mode_config.get('max_tf', '1w')
        
        if not self._is_timeframe_in_range(timeframe, min_tf, max_tf):
            return ActivationResult(
                activate=False,
                reason=f"Timeframe {timeframe} outside range [{min_tf}, {max_tf}]",
                weight=0.0,
            )
        
        # Get base weight for this timeframe
        weight_by_tf = config.get('backtest', {}).get('weight_by_tf', {})
        base_weight = weight_by_tf.get(timeframe, 1.0)
        
        # Apply mode-specific logic
        if mode == ActivationMode.BACKTEST:
            return self._check_backtest_activation(
                source_id, config, timeframe, base_weight, multi_tf_context, available_tfs
            )
        else:  # LIVE
            return self._check_live_activation(
                source_id, config, timeframe, base_weight, multi_tf_context, available_tfs
            )
    
    def _check_backtest_activation(
        self,
        source_id: str,
        config: Dict,
        timeframe: str,
        base_weight: float,
        multi_tf_context: Optional[MultiTFContext],
        available_tfs: Optional[List[str]],
    ) -> ActivationResult:
        """Check activation for backtest mode"""
        backtest_config = config.get('backtest', {})
        requires_mtf = backtest_config.get('requires_mtf', False)
        
        # Check if MTF is required but not available
        if requires_mtf and (not multi_tf_context or not multi_tf_context.enabled):
            # For critical strategies, skip if no MTF
            dependency_level = config.get('dependency_level', MTFDependencyLevel.LOW)
            
            if dependency_level == MTFDependencyLevel.CRITICAL:
                return ActivationResult(
                    activate=False,
                    reason=f"{config['name']} requires MTF context (not available)",
                    weight=0.0,
                )
            elif dependency_level == MTFDependencyLevel.HIGH:
                # Reduce weight significantly
                base_weight *= 0.4
                self.logger.warning(
                    "[Backtest] %s: MTF recommended but not available, reducing weight to %.2f",
                    config['name'], base_weight
                )
        
        # Check if higher TFs are available for this timeframe
        if available_tfs and len(available_tfs) > 1:
            higher_tfs = get_higher_timeframes(timeframe, available_tfs)
            if not higher_tfs and requires_mtf:
                return ActivationResult(
                    activate=False,
                    reason=f"{config['name']} requires higher TF (none available for {timeframe})",
                    weight=0.0,
                )
        
        # Apply minimum weight threshold
        if base_weight < self.global_config['min_weight_threshold']:
            return ActivationResult(
                activate=False,
                reason=f"Weight {base_weight:.2f} below threshold {self.global_config['min_weight_threshold']}",
                weight=base_weight,
            )
        
        return ActivationResult(
            activate=True,
            reason=f"Activated with weight {base_weight:.2f}",
            weight=base_weight,
        )
    
    def _check_live_activation(
        self,
        source_id: str,
        config: Dict,
        timeframe: str,
        base_weight: float,
        multi_tf_context: Optional[MultiTFContext],
        available_tfs: Optional[List[str]],
    ) -> ActivationResult:
        """Check activation for live mode (more conservative)"""
        live_config = config.get('live', {})
        dependency_level = config.get('dependency_level', MTFDependencyLevel.LOW)
        
        # Conservative mode checks
        if live_config.get('conservative_mode', False):
            # Check HTF staleness
            staleness_threshold = live_config.get('staleness_threshold_min', 30)
            
            if multi_tf_context and multi_tf_context.staleness_min > staleness_threshold:
                # HTF is too stale
                self.logger.warning(
                    "[Live] %s/%s: HTF is %.1f min old (> %d min threshold)",
                    source_id, timeframe, multi_tf_context.staleness_min, staleness_threshold
                )
                
                fallback_to_intra_tf = live_config.get('fallback_to_intra_tf', False)
                
                if not fallback_to_intra_tf:
                    # Critical: don't activate at all
                    if dependency_level == MTFDependencyLevel.CRITICAL:
                        # Special case for SMC: activate only liquidity sweeps
                        if source_id == 'smc' and live_config.get('activate_liquidity_sweeps_only', False):
                            return ActivationResult(
                                activate=True,
                                reason="SMC conservative: liquidity sweeps only",
                                weight=0.5,
                                fallback_mode='liquidity_sweeps_only',
                                warnings=[f"HTF context is stale ({multi_tf_context.staleness_min:.1f}min old)"],
                            )
                        
                        return ActivationResult(
                            activate=False,
                            reason=f"{config['name']}: HTF too stale ({multi_tf_context.staleness_min:.1f}min > {staleness_threshold}min)",
                            weight=0.0,
                            warnings=[f"HTF context is stale ({multi_tf_context.staleness_min:.1f}min old)"],
                        )
                
                # Fallback to intra-TF with reduced weight
                weight_when_stale = live_config.get('weight_when_stale', 0.3)
                base_weight = min(base_weight, weight_when_stale)
                
                return ActivationResult(
                    activate=True,
                    reason=f"Fallback to intra-TF (HTF stale), weight {base_weight:.2f}",
                    weight=base_weight,
                    fallback_mode='intra_tf_only',
                    warnings=[f"HTF context is stale ({multi_tf_context.staleness_min:.1f}min old)"],
                )
        
        # Check if MTF is available for strategies that benefit from it
        if not multi_tf_context or not multi_tf_context.enabled:
            ponderate_by_tf = live_config.get('apply_tf_ponderation', False)
            
            if ponderate_by_tf:
                # Apply TF ponderation instead of full MTF
                self.logger.debug(
                    "[Live] %s: No MTF, applying TF ponderation (weight=%.2f)",
                    source_id, base_weight
                )
            elif dependency_level == MTFDependencyLevel.CRITICAL:
                return ActivationResult(
                    activate=False,
                    reason=f"{config['name']}: MTF required but not available in live mode",
                    weight=0.0,
                )
        
        # Apply minimum weight threshold
        if base_weight < self.global_config['min_weight_threshold']:
            return ActivationResult(
                activate=False,
                reason=f"Weight {base_weight:.2f} below threshold {self.global_config['min_weight_threshold']}",
                weight=base_weight,
            )
        
        warnings = []
        if multi_tf_context and multi_tf_context.staleness_min > 0:
            warnings.append(f"HTF context is {multi_tf_context.staleness_min:.1f}min old")
        
        return ActivationResult(
            activate=True,
            reason=f"Activated with weight {base_weight:.2f}",
            weight=base_weight,
            warnings=warnings,
        )
    
    def _is_timeframe_in_range(self, timeframe: str, min_tf: str, max_tf: str) -> bool:
        """Check if timeframe is within [min_tf, max_tf] range"""
        try:
            tf_index = TIMEFRAME_ORDER.index(timeframe)
            min_index = TIMEFRAME_ORDER.index(min_tf) if min_tf in TIMEFRAME_ORDER else 0
            max_index = TIMEFRAME_ORDER.index(max_tf) if max_tf in TIMEFRAME_ORDER else len(TIMEFRAME_ORDER) - 1
            
            return min_index <= tf_index <= max_index
        except ValueError:
            return False
    
    def get_strategy_config(self, source_id: str) -> Optional[Dict]:
        """Get configuration for a specific strategy"""
        return self.configs.get(source_id)
    
    def get_all_strategies(self) -> List[str]:
        """Get list of all strategy source IDs"""
        return list(self.configs.keys())
    
    def analyze_htf_direction(
        self,
        closed_candles: Optional[List[Dict[str, Any]]],
        current_price: float,
    ) -> str:
        """
        Analyze higher timeframe direction from candles.
        Homologa frontend function analyzeHTFDirection() in confluence.ts
        
        Args:
            closed_candles: List of closed candle dicts with keys: t, o, h, l, c
            current_price: Current forming candle price
        
        Returns:
            'bullish', 'bearish', or 'neutral'
        """
        if not closed_candles or len(closed_candles) < 2:
            return 'neutral'
        
        try:
            # Simple trend detection: higher highs + higher lows = bullish
            last_candle = closed_candles[-1]
            prev_candle = closed_candles[-2]
            
            last_close = float(last_candle.get('c', last_candle.get('close', 0)))
            prev_close = float(prev_candle.get('c', prev_candle.get('close', 0)))
            last_high = float(last_candle.get('h', last_candle.get('high', 0)))
            prev_high = float(prev_candle.get('h', prev_candle.get('high', 0)))
            last_low = float(last_candle.get('l', last_candle.get('low', 0)))
            prev_low = float(prev_candle.get('l', prev_candle.get('low', 0)))
            
            # Bullish: higher close + current price above last close
            if last_close > prev_close and current_price > last_close:
                # Additional confirmation: higher high
                if last_high > prev_high:
                    return 'bullish'
            
            # Bearish: lower close + current price below last close
            if last_close < prev_close and current_price < last_close:
                # Additional confirmation: lower low
                if last_low < prev_low:
                    return 'bearish'
            
            return 'neutral'
        except Exception as e:
            self.logger.warning("[MTF] Error analyzing HTF direction: %s", e)
            return 'neutral'
    
    def calculate_mtf_alignment(
        self,
        primary_tf_direction: str,
        multi_tf_context: MultiTFContext,
    ) -> Tuple[int, Dict[str, str]]:
        """
        Calculate MTF alignment across timeframes.
        Homologa frontend logic in calculateConfluenceScore() - MTF alignment section.
        
        Args:
            primary_tf_direction: Direction of primary timeframe ('bullish', 'bearish', 'neutral')
            multi_tf_context: Multi-timeframe context with HTF data
        
        Returns:
            Tuple of (aligned_count, htf_directions_dict)
            - aligned_count: Number of aligned TFs (1-3, where 1 is primary only)
            - htf_directions_dict: {'HTF1': 'bullish', 'HTF2': 'bearish', ...}
        """
        aligned_count = 1  # Primary siempre cuenta
        htf_directions: Dict[str, str] = {}
        
        # Analizar HTF1
        if multi_tf_context.higher_tf_1:
            closed_candles = multi_tf_context.higher_tf_1.get('closed_candles', [])
            current_price = multi_tf_context.higher_tf_1.get('current_price', 0)
            
            direction = self.analyze_htf_direction(closed_candles, current_price)
            htf_directions['HTF1'] = direction
            
            if direction == primary_tf_direction:
                aligned_count += 1
            elif direction != 'neutral':
                aligned_count -= 1  # Penalizar divergencia
        
        # Analizar HTF2 si está disponible
        if multi_tf_context.higher_tf_2:
            closed_candles = multi_tf_context.higher_tf_2.get('closed_candles', [])
            current_price = multi_tf_context.higher_tf_2.get('current_price', 0)
            
            direction = self.analyze_htf_direction(closed_candles, current_price)
            htf_directions['HTF2'] = direction
            
            if direction == primary_tf_direction:
                aligned_count += 1
            elif direction != 'neutral':
                aligned_count -= 1
        
        return aligned_count, htf_directions
    
    def deduce_primary_direction(
        self,
        smc_concepts: Optional[List[Dict[str, Any]]],
        order_blocks: Optional[List[Dict[str, Any]]],
    ) -> str:
        """
        Deduce primary market direction from SMC concepts and Order Blocks.
        Homologa frontend function deducePrimaryDirection() in confluence.ts
        
        Args:
            smc_concepts: List of SMC concept dicts with 'type' key ('bullish'/'bearish')
            order_blocks: List of OB dicts with 'type' key ('bullish'/'bearish')
        
        Returns:
            'bullish', 'bearish', or 'neutral'
        """
        if not smc_concepts and not order_blocks:
            return 'neutral'
        
        bullish_score = 0
        bearish_score = 0
        
        # SMC concepts (weight: 2 points each)
        if smc_concepts:
            for smc in smc_concepts:
                smc_type = smc.get('type', '').lower()
                if smc_type == 'bullish':
                    bullish_score += 2
                elif smc_type == 'bearish':
                    bearish_score += 2
        
        # Order Blocks (weight: 1 point each)
        if order_blocks:
            for ob in order_blocks:
                ob_type = ob.get('type', '').lower()
                if ob_type == 'bullish':
                    bullish_score += 1
                elif ob_type == 'bearish':
                    bearish_score += 1
        
        if bullish_score > bearish_score:
            return 'bullish'
        elif bearish_score > bullish_score:
            return 'bearish'
        else:
            return 'neutral'
    
    def calculate_mtf_alignment_boost(
        self,
        source_id: str,
        aligned_tf_count: int,
        mode: ActivationMode,
    ) -> float:
        """
        Calculate MTF alignment boost for strategies that support it.
        
        Args:
            source_id: Strategy source ID
            aligned_tf_count: Number of aligned timeframes (1-3)
            mode: Activation mode
        
        Returns:
            Boost multiplier (-0.15 to +0.20)
        """
        config = self.configs.get(source_id)
        if not config:
            return 0.0
        
        if mode == ActivationMode.BACKTEST:
            mtf_config = config.get('backtest', {}).get('mtf_alignment_boost', {})
        else:
            mtf_config = config.get('live', {}).get('mtf_alignment_boost', {})
        
        if aligned_tf_count >= 3:
            return mtf_config.get('aligned_3TF', 0.20)
        elif aligned_tf_count == 2:
            return mtf_config.get('aligned_2TF', 0.10)
        else:
            return mtf_config.get('aligned_1TF', -0.15)


# ==================== FACTORY ====================

def get_strategy_activation_service(
    logger=None
) -> StrategyActivationService:
    """Get StrategyActivationService instance"""
    return StrategyActivationService(logger=logger)
