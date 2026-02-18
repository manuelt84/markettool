"""
Confluence Evaluation Service

Evaluates signal strength by analyzing confluence of multiple indicators.
Higher confluence = higher signal confidence.
"""

import logging
from dataclasses import dataclass
from typing import Optional, Dict, List, Any
from enum import Enum

logger = logging.getLogger(__name__)


class ConfluenceLevel(Enum):
    """Signal confluence strength"""
    VERY_WEAK = "very_weak"  # 1-2 confluences
    WEAK = "weak"  # 3-4 confluences
    MODERATE = "moderate"  # 5-6 confluences
    STRONG = "strong"  # 7-8 confluences
    VERY_STRONG = "very_strong"  # 9+ confluences


@dataclass
class ConfluenceResult:
    """Confluence evaluation result"""
    signal_direction: str  # BUY, SELL, NEUTRAL
    confluence_count: int  # Number of confluent signals
    confluence_pct: float  # Percentage of signals aligned (0-100)
    confluence_level: str  # very_weak, weak, moderate, strong, very_strong
    confluent_signals: List[str]  # Names of confluent signals
    conflicting_signals: List[str]  # Names of non-confluent signals
    confidence_score: float  # 0.0-1.0
    recommendation: str  # "Strong Buy", "Buy", "Neutral", "Sell", "Strong Sell"
    metadata: Dict[str, Any] = None


class ConfluenceEvaluationService:
    """
    Evaluates signal confluence from multiple technical indicators.
    
    Confluence signals are weighted equally and combined to give
    a final signal strength and confidence score.
    """
    
    def __init__(self, logger: Optional[logging.Logger] = None):
        self.logger = logger or logging.getLogger(__name__)
    
    def evaluate_signals(
        self,
        technical_signals: Dict[str, str],  # {signal_name: 'BUY'|'SELL'|'NEUTRAL'}
        weights: Optional[Dict[str, float]] = None,  # Custom weights
        min_confluence: int = 3,  # Minimum signals for confluence
    ) -> ConfluenceResult:
        """
        Evaluate confluence of multiple technical signals.
        
        Args:
            technical_signals: Dict of signal_name -> direction
            weights: Optional custom weights per signal
            min_confluence: Minimum signals for "confluence"
        
        Returns:
            ConfluenceResult with confluent signals and recommendation
        """
        if not technical_signals:
            return ConfluenceResult(
                signal_direction='NEUTRAL',
                confluence_count=0,
                confluence_pct=0,
                confluence_level='very_weak',
                confluent_signals=[],
                conflicting_signals=[],
                confidence_score=0.0,
                recommendation='No signals',
            )
        
        # Normalize weights
        weights = weights or {name: 1.0 for name in technical_signals}
        total_weight = sum(weights.values())
        
        # Count BUY, SELL, NEUTRAL
        buy_score = 0.0
        sell_score = 0.0
        neutral_count = 0
        
        for signal_name, direction in technical_signals.items():
            weight = weights.get(signal_name, 1.0) / total_weight
            
            if direction.upper() == 'BUY':
                buy_score += weight
            elif direction.upper() == 'SELL':
                sell_score += weight
            else:
                neutral_count += 1
        
        # Determine primary direction
        if buy_score > sell_score:
            signal_direction = 'BUY'
            strength = buy_score
            conflicting = sell_score
        elif sell_score > buy_score:
            signal_direction = 'SELL'
            strength = sell_score
            conflicting = buy_score
        else:
            signal_direction = 'NEUTRAL'
            strength = 0.5
            conflicting = 0.5
        
        # Count confluent signals
        confluent_signals, conflicting_signals = self._categorize_signals(
            technical_signals, signal_direction
        )
        
        confluence_count = len(confluent_signals)
        confluence_pct = (confluence_count / len(technical_signals)) * 100 if technical_signals else 0
        
        # Determine confluence level
        confluence_level = self._get_confluence_level(confluence_count)
        
        # Calculate confidence score
        confidence_score = self._calculate_confidence(
            signal_direction, confluence_count, len(technical_signals), strength
        )
        
        # Get recommendation
        recommendation = self._get_recommendation(
            signal_direction, confluence_level, confidence_score
        )
        
        return ConfluenceResult(
            signal_direction=signal_direction,
            confluence_count=confluence_count,
            confluence_pct=confluence_pct,
            confluence_level=confluence_level.value,
            confluent_signals=confluent_signals,
            conflicting_signals=conflicting_signals,
            confidence_score=confidence_score,
            recommendation=recommendation,
            metadata={
                'buy_score': buy_score,
                'sell_score': sell_score,
                'neutral_count': neutral_count,
                'total_signals': len(technical_signals),
            }
        )
    
    def evaluate_with_weights(
        self,
        signals: Dict[str, str],
        signal_weights: Dict[str, float],
    ) -> ConfluenceResult:
        """
        Evaluate signals with custom weights.
        
        Weights should sum to 1.0. Higher weights = more important.
        
        Example:
            signals = {
                'RSI': 'BUY',
                'MACD': 'BUY',
                'Bollinger': 'SELL',
            }
            weights = {
                'RSI': 0.4,
                'MACD': 0.4,
                'Bollinger': 0.2,
            }
        """
        # Normalize weights
        total = sum(signal_weights.values())
        normalized = {k: v/total for k, v in signal_weights.items()}
        
        return self.evaluate_signals(signals, normalized)
    
    def evaluate_multi_timeframe(
        self,
        signals_by_tf: Dict[str, Dict[str, str]],  # {timeframe: {signal: direction}}
        tf_weights: Optional[Dict[str, float]] = None,
    ) -> Dict[str, ConfluenceResult]:
        """
        Evaluate confluence across multiple timeframes.
        
        Args:
            signals_by_tf: Signals grouped by timeframe
            tf_weights: Weight per timeframe (default equal)
        
        Returns:
            Dict with confluence result per timeframe + combined
        """
        if not signals_by_tf:
            return {}
        
        results = {}
        
        # Evaluate each timeframe
        for tf, signals in signals_by_tf.items():
            results[tf] = self.evaluate_signals(signals)
        
        # Combine across timeframes
        if tf_weights is None:
            tf_weights = {tf: 1.0/len(signals_by_tf) for tf in signals_by_tf}
        
        combined_signals = {}
        for tf, result in results.items():
            weight = tf_weights.get(tf, 1.0/len(signals_by_tf))
            combined_signals[f"{tf}_{result.signal_direction}"] = result.signal_direction
        
        # Create weighted signals
        weighted_by_direction = {}
        for signal_name, direction in combined_signals.items():
            tf = signal_name.split('_')[0]
            weight = tf_weights.get(tf, 1.0)
            key = direction
            weighted_by_direction[key] = weighted_by_direction.get(key, 0) + weight
        
        final_direction = max(weighted_by_direction, key=weighted_by_direction.get, default='NEUTRAL')
        results['COMBINED'] = ConfluenceResult(
            signal_direction=final_direction,
            confluence_count=len(results),
            confluence_pct=100.0,
            confluence_level='strong',
            confluent_signals=[],
            conflicting_signals=[],
            confidence_score=0.75,
            recommendation=f'Multi-TF {final_direction}',
        )
        
        return results
    
    # ===================== PRIVATE METHODS =====================
    
    @staticmethod
    def _categorize_signals(
        signals: Dict[str, str],
        target_direction: str
    ) -> tuple[List[str], List[str]]:
        """Categorize signals as confluent or conflicting"""
        confluent = []
        conflicting = []
        
        for signal_name, direction in signals.items():
            if direction.upper() == target_direction.upper():
                confluent.append(signal_name)
            else:
                conflicting.append(signal_name)
        
        return confluent, conflicting
    
    @staticmethod
    def _get_confluence_level(count: int) -> ConfluenceLevel:
        """Determine confluence level from signal count"""
        if count <= 2:
            return ConfluenceLevel.VERY_WEAK
        elif count <= 4:
            return ConfluenceLevel.WEAK
        elif count <= 6:
            return ConfluenceLevel.MODERATE
        elif count <= 8:
            return ConfluenceLevel.STRONG
        else:
            return ConfluenceLevel.VERY_STRONG
    
    @staticmethod
    def _calculate_confidence(
        direction: str,
        confluence_count: int,
        total_signals: int,
        strength: float
    ) -> float:
        """Calculate overall confidence score (0-1)"""
        if direction == 'NEUTRAL':
            return 0.5
        
        # Confluence contribution (max 0.5)
        confluence_score = min(confluence_count / max(1, total_signals), 1.0) * 0.5
        
        # Strength contribution (max 0.5)
        strength_score = min(abs(strength), 1.0) * 0.5
        
        return confluence_score + strength_score
    
    @staticmethod
    def _get_recommendation(
        direction: str,
        confluence_level: ConfluenceLevel,
        confidence: float
    ) -> str:
        """Generate trading recommendation"""
        if direction == 'NEUTRAL':
            return 'Neutral / Wait'
        
        if confluence_level in [ConfluenceLevel.VERY_STRONG, ConfluenceLevel.STRONG]:
            return f'Strong {direction}'
        elif confluence_level == ConfluenceLevel.MODERATE:
            return direction
        else:
            return f'Weak {direction}'


# ==================== FACTORY ====================

def get_confluence_service(
    logger: Optional[logging.Logger] = None
) -> ConfluenceEvaluationService:
    """Get ConfluenceEvaluationService instance"""
    return ConfluenceEvaluationService(logger=logger)
