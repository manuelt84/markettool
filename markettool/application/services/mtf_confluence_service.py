"""
Multi-timeframe Confluence Service

Analyzes trading signals across multiple timeframes to increase confidence
and reduce false signals.

Implements confluence evaluation by comparing signals on:
- Primary timeframe (e.g., 1h entry)
- Higher timeframe 1 (e.g., 4h confirmation)
- Higher timeframe 2 (e.g., 1d direction)
"""

import logging
from typing import Dict, Optional, Any, Tuple
from dataclasses import dataclass
import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class TimeframeSignal:
    """Signal from a specific timeframe"""
    timeframe: str
    direction: str  # 'Compra', 'Venta', or 'Neutral'
    probability: float
    confidence: float
    technical_prob: float
    fundamental_prob: float


@dataclass
class ConfluenceResult:
    """Result of multi-timeframe confluence analysis"""
    primary_signal: TimeframeSignal
    higher_signal_1: Optional[TimeframeSignal]
    higher_signal_2: Optional[TimeframeSignal]
    confluence_level: int  # 1, 2, or 3 timeframes aligned
    confidence_boost: float
    final_probability: float
    final_direction: str
    analysis: str  # Human-readable explanation


class MultiTimeframeConfluenceService:
    """
    Analyzes signals across multiple timeframes to improve accuracy.
    
    Confluence Logic:
    - 3/3 TF aligned (primary + 2 higher): +0.2 confidence boost
    - 2/3 TF aligned (primary + 1 higher): +0.1 confidence boost
    - 1/3 TF aligned (only primary): -0.15 confidence penalty
    """
    
    def __init__(self, logger: Optional[logging.Logger] = None):
        self.logger = logger or logging.getLogger(__name__)
    
    def analyze_confluence(
        self,
        primary_signal: TimeframeSignal,
        higher_signal_1: Optional[TimeframeSignal] = None,
        higher_signal_2: Optional[TimeframeSignal] = None,
    ) -> ConfluenceResult:
        """
        Analyze signal confluence across timeframes.
        
        Args:
            primary_signal: Signal from primary/entry timeframe
            higher_signal_1: Signal from first higher timeframe (optional)
            higher_signal_2: Signal from second higher timeframe (optional)
        
        Returns:
            ConfluenceResult with aligned signals and confidence adjustments
        """
        
        # Count aligned signals
        aligned_signals = 1  # Primary is always counted
        confidence_boost = 0.0
        analysis_parts = [f"Primary {primary_signal.timeframe}: {primary_signal.direction}"]
        
        # Check first higher timeframe alignment
        if higher_signal_1:
            if higher_signal_1.direction == primary_signal.direction:
                aligned_signals += 1
                analysis_parts.append(f"✓ Aligned with {higher_signal_1.timeframe}: {higher_signal_1.direction}")
            else:
                analysis_parts.append(f"✗ Divergence with {higher_signal_1.timeframe}: {higher_signal_1.direction}")
        
        # Check second higher timeframe alignment
        if higher_signal_2:
            if higher_signal_2.direction == primary_signal.direction:
                aligned_signals += 1
                analysis_parts.append(f"✓ Aligned with {higher_signal_2.timeframe}: {higher_signal_2.direction}")
            else:
                analysis_parts.append(f"✗ Divergence with {higher_signal_2.timeframe}: {higher_signal_2.direction}")
        
        # Calculate confidence adjustment
        if aligned_signals == 3:
            confidence_boost = 0.20  # Strong confluence
            confluence_level = 3
            analysis_parts.append("Strong confluence: All timeframes aligned")
        elif aligned_signals == 2:
            confidence_boost = 0.10  # Moderate confluence
            confluence_level = 2
            analysis_parts.append("Moderate confluence: Primary + 1 higher TF aligned")
        else:
            confidence_boost = -0.15  # Weak signal, no confluence
            confluence_level = 1
            analysis_parts.append("Weak signal: Only primary timeframe, no confluence")
        
        # Calculate final probability
        base_prob = primary_signal.probability
        final_prob = min(100, max(0, base_prob + (confidence_boost * 100)))
        
        return ConfluenceResult(
            primary_signal=primary_signal,
            higher_signal_1=higher_signal_1,
            higher_signal_2=higher_signal_2,
            confluence_level=confluence_level,
            confidence_boost=confidence_boost,
            final_probability=final_prob,
            final_direction=primary_signal.direction,
            analysis="\n".join(analysis_parts),
        )
    
    def get_timeframe_hierarchy(
        self,
        primary_tf: str,
    ) -> Tuple[str, str]:
        """
        Get recommended higher timeframes for confluence analysis.
        
        Args:
            primary_tf: Primary trading timeframe
        
        Returns:
            Tuple of (higher_tf_1, higher_tf_2) for confluence
        
        Examples:
            '1min' -> ('5min', '15min')
            '5min' -> ('15min', '1hour')
            '1hour' -> ('4hour', '1day')
            '4hour' -> ('1day', '1week')
            '1day' -> ('1week', '1month')
        """
        
        hierarchy_map = {
            '1min': ('5min', '15min'),
            '5min': ('15min', '1hour'),
            '15min': ('1hour', '4hour'),
            '1hour': ('4hour', '1day'),
            '4hour': ('1day', '1week'),
            '1day': ('1week', '1month'),
            '1week': ('1month', None),
            '1month': (None, None),
        }
        
        return hierarchy_map.get(primary_tf, ('1day', '1week'))
    
    def validate_confluence_signal(
        self,
        confluence_result: ConfluenceResult,
        min_confluence_level: int = 2,
        min_probability: float = 60.0,
    ) -> Tuple[bool, str]:
        """
        Validate whether a signal should be traded based on confluence rules.
        
        Args:
            confluence_result: Result from analyze_confluence()
            min_confluence_level: Minimum timeframes to align (1-3)
            min_probability: Minimum final probability (0-100)
        
        Returns:
            Tuple of (should_trade, reason)
        """
        
        reasons = []
        should_trade = True
        
        # Check confluence level
        if confluence_result.confluence_level < min_confluence_level:
            should_trade = False
            reasons.append(
                f"Insufficient confluence: {confluence_result.confluence_level} "
                f"< {min_confluence_level} required"
            )
        
        # Check probability
        if confluence_result.final_probability < min_probability:
            should_trade = False
            reasons.append(
                f"Low probability: {confluence_result.final_probability:.1f} "
                f"< {min_probability:.1f} required"
            )
        
        reason = "; ".join(reasons) if reasons else "Valid signal: All checks passed"
        
        return should_trade, reason
    
    def analyze_with_data(
        self,
        primary_df: pd.DataFrame,
        higher_df_1: Optional[pd.DataFrame],
        higher_df_2: Optional[pd.DataFrame],
        primary_tf: str,
        primary_analysis: Dict[str, Any],
        higher_analysis_1: Optional[Dict[str, Any]] = None,
        higher_analysis_2: Optional[Dict[str, Any]] = None,
    ) -> ConfluenceResult:
        """
        Analyze confluence using actual dataframes and analysis results.
        
        Args:
            primary_df: Primary timeframe OHLCV data
            higher_df_1: First higher timeframe OHLCV data
            higher_df_2: Second higher timeframe OHLCV data
            primary_tf: Primary timeframe string (e.g., '1hour')
            primary_analysis: Analysis result from calculate_entries
            higher_analysis_1: Analysis result from higher timeframe
            higher_analysis_2: Analysis result from higher timeframe
        
        Returns:
            ConfluenceResult
        """
        
        # Create primary signal
        primary_signal = TimeframeSignal(
            timeframe=primary_tf,
            direction=primary_analysis.get('tipo_operacion', 'Neutral'),
            probability=primary_analysis.get('probabilidad_alza', 50),
            confidence=primary_analysis.get('confianza', 0),
            technical_prob=primary_analysis.get('probabilidad_tecnica', 50),
            fundamental_prob=primary_analysis.get('probabilidad_fundamental', 50),
        )
        
        # Create higher timeframe signals
        higher_signal_1 = None
        if higher_analysis_1:
            higher_tf_1, _ = self.get_timeframe_hierarchy(primary_tf)
            higher_signal_1 = TimeframeSignal(
                timeframe=higher_tf_1,
                direction=higher_analysis_1.get('tipo_operacion', 'Neutral'),
                probability=higher_analysis_1.get('probabilidad_alza', 50),
                confidence=higher_analysis_1.get('confianza', 0),
                technical_prob=higher_analysis_1.get('probabilidad_tecnica', 50),
                fundamental_prob=higher_analysis_1.get('probabilidad_fundamental', 50),
            )
        
        higher_signal_2 = None
        if higher_analysis_2:
            _, higher_tf_2 = self.get_timeframe_hierarchy(primary_tf)
            if higher_tf_2:
                higher_signal_2 = TimeframeSignal(
                    timeframe=higher_tf_2,
                    direction=higher_analysis_2.get('tipo_operacion', 'Neutral'),
                    probability=higher_analysis_2.get('probabilidad_alza', 50),
                    confidence=higher_analysis_2.get('confianza', 0),
                    technical_prob=higher_analysis_2.get('probabilidad_tecnica', 50),
                    fundamental_prob=higher_analysis_2.get('probabilidad_fundamental', 50),
                )
        
        # Analyze confluence
        return self.analyze_confluence(primary_signal, higher_signal_1, higher_signal_2)


# ==================== FACTORY ====================

def get_mtf_confluence_service(
    logger: Optional[logging.Logger] = None
) -> MultiTimeframeConfluenceService:
    """Get MultiTimeframeConfluenceService instance"""
    return MultiTimeframeConfluenceService(logger=logger)
