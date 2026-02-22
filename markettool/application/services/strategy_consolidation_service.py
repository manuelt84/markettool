"""
Strategy Consolidation Service
==============================
Consolidates all 12 trading strategies (frontend + backend) into a single service.
Translates detection logic from MonitoreoScreen.tsx to Python backend.

Strategies implemented:
1. Technical Signals (RSI, MACD, Bollinger)
2. S/R Levels (Support/Resistance)
3. Order Blocks
4. Fair Value Gaps
5. Smart Money Concepts
6. Breaker Blocks
7. Liquidity Traps
8. Divergences
9. Fibonacci Zones
10. Mega Setups (Confluence)
11. MarketTool (handled separately)
12. Economic Events (handled separately)
"""

from typing import Dict, List, Tuple, Optional, Any
from datetime import datetime
import numpy as np
from dataclasses import dataclass, asdict


@dataclass
class StrategyResult:
    """Result of strategy detection"""
    detected: bool
    strength: float  # 0-100
    details: Dict[str, Any]
    signals: List[str]


class StrategyConsolidationService:
    """
    Service to consolidate and calculate all 12 trading strategies.
    Works with OHLCV data + indicators.
    """

    def __init__(self):
        self.confluence_weights = {
            'technical_signals': 10,
            'sr_levels': 15,
            'order_blocks': 25,
            'fair_value_gaps': 15,
            'smart_money': 20,
            'breaker_blocks': 20,
            'liquidity_traps': 20,
            'divergences': 15,
            'fibonacci_zones': 10,
        }

    async def consolidate_all_strategies(
        self,
        candles: List[Dict],
        indicators: Dict[str, List[float]],
        symbol: str,
        timeframe: str,
        current_price: float
    ) -> Dict[str, Any]:
        """
        Main method: Detect all 9 client-side strategies + confluence scoring.
        Returns consolidation with confluence_score (0-100).
        """
        
        results = {
            'symbol': symbol,
            'timeframe': timeframe,
            'timestamp': datetime.utcnow().isoformat(),
            'current_price': current_price,
            'strategies': {},
            'confluence_score': 0,
            'high_probability': False,
            'active_strategies_count': 0,
            'description': ''
        }

        # Detect each strategy
        results['strategies']['technical_signals'] = self._detect_technical_signals(
            candles, indicators
        )
        results['strategies']['sr_levels'] = self._detect_sr_levels(
            candles, indicators
        )
        results['strategies']['order_blocks'] = self._detect_order_blocks(
            candles
        )
        results['strategies']['fair_value_gaps'] = self._detect_fair_value_gaps(
            candles
        )
        results['strategies']['smart_money'] = self._detect_smart_money_concepts(
            candles
        )
        results['strategies']['breaker_blocks'] = self._detect_breaker_blocks(
            candles
        )
        results['strategies']['liquidity_traps'] = self._detect_liquidity_traps(
            candles
        )
        results['strategies']['divergences'] = self._detect_divergences(
            candles, indicators
        )
        results['strategies']['fibonacci_zones'] = self._detect_fibonacci_zones(
            candles, current_price
        )

        # Calculate confluence score
        confluence_data = self._calculate_confluence_score(results['strategies'])
        results['confluence_score'] = confluence_data['score']
        results['high_probability'] = confluence_data['high_probability']
        results['active_strategies_count'] = confluence_data['active_count']
        results['description'] = confluence_data['description']

        return results

    def _detect_technical_signals(
        self,
        candles: List[Dict],
        indicators: Dict[str, List[float]]
    ) -> StrategyResult:
        """Detect technical signals (RSI, MACD, Bollinger)"""
        signals = []
        strength = 0
        
        try:
            rsi = indicators.get('rsi', [])
            macd = indicators.get('macd', [])
            bb_upper = indicators.get('bb_upper', [])
            bb_lower = indicators.get('bb_lower', [])
            
            if not candles or len(candles) < 2:
                return StrategyResult(False, 0, {}, [])
            
            close = candles[-1]['close']
            
            # RSI signals
            if rsi and len(rsi) > 0:
                rsi_val = rsi[-1]
                if rsi_val > 70:
                    signals.append('RSI_OVERBOUGHT')
                    strength += 15
                elif rsi_val < 30:
                    signals.append('RSI_OVERSOLD')
                    strength += 15
                elif 50 < rsi_val < 70:
                    signals.append('RSI_STRONG_BULL')
                    strength += 20
                elif 30 < rsi_val < 50:
                    signals.append('RSI_BEAR')
                    strength += 10
            
            # MACD signals
            if macd and len(macd) > 1:
                if macd[-1] > macd[-2]:
                    signals.append('MACD_BULLISH_CROSS')
                    strength += 20
                elif macd[-1] < macd[-2]:
                    signals.append('MACD_BEARISH_CROSS')
                    strength += 20
            
            # Bollinger Bands
            if bb_upper and bb_lower and len(bb_upper) > 0:
                if close > bb_upper[-1]:
                    signals.append('BB_UPPER_BREAK')
                    strength += 15
                elif close < bb_lower[-1]:
                    signals.append('BB_LOWER_BREAK')
                    strength += 15
            
            detected = len(signals) > 0
            strength = min(strength, 100)
            
        except Exception as e:
            return StrategyResult(False, 0, {'error': str(e)}, [])
        
        return StrategyResult(
            detected=detected,
            strength=strength,
            details={'signals': signals, 'rsi': rsi[-1] if rsi else None},
            signals=signals
        )

    def _detect_sr_levels(
        self,
        candles: List[Dict],
        indicators: Dict[str, List[float]]
    ) -> StrategyResult:
        """Detect Support/Resistance levels"""
        signals = []
        strength = 0
        
        try:
            if len(candles) < 20:
                return StrategyResult(False, 0, {}, [])
            
            closes = [c['close'] for c in candles[-20:]]
            highs = [c['high'] for c in candles[-20:]]
            lows = [c['low'] for c in candles[-20:]]
            current_price = candles[-1]['close']
            
            # Support levels (local minima)
            supports = []
            for i in range(1, len(lows) - 1):
                if lows[i] < lows[i-1] and lows[i] < lows[i+1]:
                    supports.append(lows[i])
            
            # Resistance levels (local maxima)
            resistances = []
            for i in range(1, len(highs) - 1):
                if highs[i] > highs[i-1] and highs[i] > highs[i+1]:
                    resistances.append(highs[i])
            
            # Check proximity to levels
            tolerance = (max(highs) - min(lows)) * 0.01  # 1% tolerance
            
            for sup in supports:
                if abs(current_price - sup) < tolerance:
                    signals.append(f'SUPPORT_{sup:.4f}')
                    strength += 25
            
            for res in resistances:
                if abs(current_price - res) < tolerance:
                    signals.append(f'RESISTANCE_{res:.4f}')
                    strength += 25
            
            detected = len(signals) > 0
            strength = min(strength, 100)
            
        except Exception as e:
            return StrategyResult(False, 0, {'error': str(e)}, [])
        
        return StrategyResult(detected, strength, {'supports': supports, 'resistances': resistances}, signals)

    def _detect_order_blocks(self, candles: List[Dict]) -> StrategyResult:
        """Detect Order Blocks (institutional supply/demand zones)"""
        signals = []
        strength = 0
        
        try:
            if len(candles) < 10:
                return StrategyResult(False, 0, {}, [])
            
            # Order blocks: Large candles followed by retracement
            for i in range(len(candles) - 5, len(candles)):
                if i < 0:
                    continue
                
                candle = candles[i]
                body = abs(candle['close'] - candle['open'])
                full_range = candle['high'] - candle['low']
                
                # Large candle = body > 60% of range
                if body > full_range * 0.6:
                    signals.append(f'OB_{candle["close"]:.4f}')
                    strength += 20
            
            detected = len(signals) > 0
            strength = min(strength, 100)
            
        except Exception as e:
            return StrategyResult(False, 0, {'error': str(e)}, [])
        
        return StrategyResult(detected, strength, {'blocks': signals}, signals)

    def _detect_fair_value_gaps(self, candles: List[Dict]) -> StrategyResult:
        """Detect Fair Value Gaps (imbalances, unfilled gaps)"""
        signals = []
        strength = 0
        
        try:
            if len(candles) < 3:
                return StrategyResult(False, 0, {}, [])
            
            # FVG: Gap between candles not filled
            for i in range(1, len(candles) - 1):
                prev_candle = candles[i - 1]
                curr_candle = candles[i]
                next_candle = candles[i + 1]
                
                # Bullish gap: current low > previous high
                if curr_candle['low'] > prev_candle['high']:
                    if next_candle['low'] > curr_candle['low']:  # Gap not filled
                        gap_size = curr_candle['low'] - prev_candle['high']
                        signals.append(f'FVG_BULL_{curr_candle["low"]:.4f}')
                        strength += 20
                
                # Bearish gap: current high < previous low
                elif curr_candle['high'] < prev_candle['low']:
                    if next_candle['high'] < curr_candle['high']:  # Gap not filled
                        gap_size = prev_candle['low'] - curr_candle['high']
                        signals.append(f'FVG_BEAR_{curr_candle["high"]:.4f}')
                        strength += 20
            
            detected = len(signals) > 0
            strength = min(strength, 100)
            
        except Exception as e:
            return StrategyResult(False, 0, {'error': str(e)}, [])
        
        return StrategyResult(detected, strength, {'gaps': signals}, signals)

    def _detect_smart_money_concepts(self, candles: List[Dict]) -> StrategyResult:
        """Detect Smart Money Concepts (break/retest patterns)"""
        signals = []
        strength = 0
        
        try:
            if len(candles) < 10:
                return StrategyResult(False, 0, {}, [])
            
            # SMC: Break and Retest of structure
            # Look for break of previous high/low + retest
            recent = candles[-20:]
            highs = [c['high'] for c in recent]
            lows = [c['low'] for c in recent]
            
            # Previous structure high
            structure_high = max(highs[:-1])
            structure_low = min(lows[:-1])
            
            # Current candle
            curr = candles[-1]
            
            # Break and retest of high
            if curr['low'] > structure_high and curr['close'] < structure_high:
                signals.append('SMC_BRT_HIGH')
                strength += 25
            
            # Break and retest of low
            if curr['high'] < structure_low and curr['close'] > structure_low:
                signals.append('SMC_BRT_LOW')
                strength += 25
            
            # Mitigation block (price comes back to break point)
            if abs(curr['close'] - structure_high) < (highs[-1] - lows[-1]) * 0.1:
                signals.append('SMC_MITIGATION')
                strength += 20
            
            detected = len(signals) > 0
            strength = min(strength, 100)
            
        except Exception as e:
            return StrategyResult(False, 0, {'error': str(e)}, [])
        
        return StrategyResult(detected, strength, {'concepts': signals}, signals)

    def _detect_breaker_blocks(self, candles: List[Dict]) -> StrategyResult:
        """Detect Breaker Blocks (broken support/resistance)"""
        signals = []
        strength = 0
        
        try:
            if len(candles) < 15:
                return StrategyResult(False, 0, {}, [])
            
            # Breaker: Recent high/low broken
            recent = candles[-15:]
            
            # Find recent swing high/low
            for i in range(2, len(recent) - 1):
                # Swing high (local maximum)
                if recent[i]['high'] > recent[i-1]['high'] and recent[i]['high'] > recent[i+1]['high']:
                    swing_high = recent[i]['high']
                    # Check if broken
                    if recent[-1]['low'] < swing_high < recent[-1]['high']:
                        signals.append(f'BREAKER_HIGH_{swing_high:.4f}')
                        strength += 25
                
                # Swing low (local minimum)
                if recent[i]['low'] < recent[i-1]['low'] and recent[i]['low'] < recent[i+1]['low']:
                    swing_low = recent[i]['low']
                    # Check if broken
                    if recent[-1]['low'] < swing_low < recent[-1]['high']:
                        signals.append(f'BREAKER_LOW_{swing_low:.4f}')
                        strength += 25
            
            detected = len(signals) > 0
            strength = min(strength, 100)
            
        except Exception as e:
            return StrategyResult(False, 0, {'error': str(e)}, [])
        
        return StrategyResult(detected, strength, {'breakers': signals}, signals)

    def _detect_liquidity_traps(self, candles: List[Dict]) -> StrategyResult:
        """Detect Liquidity Traps/Inducement (false breakout)"""
        signals = []
        strength = 0
        
        try:
            if len(candles) < 10:
                return StrategyResult(False, 0, {}, [])
            
            # Liquidity trap: price goes above/below level then reverses
            recent = candles[-10:]
            
            # Find extremes
            high_prices = [c['high'] for c in recent[:-1]]
            low_prices = [c['low'] for c in recent[:-1]]
            
            recent_high = max(high_prices)
            recent_low = min(low_prices)
            
            curr = candles[-1]
            range_size = curr['high'] - curr['low'] if len(candles) > 0 else 1
            tolerance = range_size * 0.02
            
            # Bullish trap: broke above, then red candle
            if curr['high'] > recent_high and curr['close'] < curr['open']:
                signals.append(f'TRAP_BULL_{recent_high:.4f}')
                strength += 20
            
            # Bearish trap: broke below, then green candle
            if curr['low'] < recent_low and curr['close'] > curr['open']:
                signals.append(f'TRAP_BEAR_{recent_low:.4f}')
                strength += 20
            
            detected = len(signals) > 0
            strength = min(strength, 100)
            
        except Exception as e:
            return StrategyResult(False, 0, {'error': str(e)}, [])
        
        return StrategyResult(detected, strength, {'traps': signals}, signals)

    def _detect_divergences(
        self,
        candles: List[Dict],
        indicators: Dict[str, List[float]]
    ) -> StrategyResult:
        """Detect Divergences (price vs indicator)"""
        signals = []
        strength = 0
        
        try:
            if len(candles) < 10:
                return StrategyResult(False, 0, {}, [])
            
            # RSI Divergence
            rsi = indicators.get('rsi', [])
            closes = [c['close'] for c in candles]
            
            if rsi and len(rsi) > 5:
                # Bullish divergence: lower low in price, higher low in RSI
                if len(closes) >= 2 and len(rsi) >= 2:
                    if closes[-1] < closes[-2] and rsi[-1] > rsi[-2]:
                        signals.append('DIV_RSI_BULLISH')
                        strength += 25
                    elif closes[-1] > closes[-2] and rsi[-1] < rsi[-2]:
                        signals.append('DIV_RSI_BEARISH')
                        strength += 25
            
            detected = len(signals) > 0
            strength = min(strength, 100)
            
        except Exception as e:
            return StrategyResult(False, 0, {'error': str(e)}, [])
        
        return StrategyResult(detected, strength, {'divergences': signals}, signals)

    def _detect_fibonacci_zones(
        self,
        candles: List[Dict],
        current_price: float
    ) -> StrategyResult:
        """Detect Fibonacci retracement levels"""
        signals = []
        strength = 0
        
        try:
            if len(candles) < 5:
                return StrategyResult(False, 0, {}, [])
            
            # Find swing high and low (recent)
            recent = candles[-20:]
            high = max([c['high'] for c in recent])
            low = min([c['low'] for c in recent])
            
            range_size = high - low
            if range_size == 0:
                return StrategyResult(False, 0, {}, [])
            
            # Fibonacci levels
            fib_levels = {
                '0.236': low + range_size * 0.236,
                '0.382': low + range_size * 0.382,
                '0.500': low + range_size * 0.500,
                '0.618': low + range_size * 0.618,
                '0.786': low + range_size * 0.786,
            }
            
            tolerance = range_size * 0.01  # 1% tolerance
            
            for level_name, level_price in fib_levels.items():
                if abs(current_price - level_price) < tolerance:
                    signals.append(f'FIB_{level_name}_{level_price:.4f}')
                    strength += 15
            
            detected = len(signals) > 0
            strength = min(strength, 100)
            
        except Exception as e:
            return StrategyResult(False, 0, {'error': str(e)}, [])
        
        return StrategyResult(detected, strength, {'fib_levels': signals}, signals)

    def _calculate_confluence_score(self, strategies: Dict[str, StrategyResult]) -> Dict[str, Any]:
        """
        Calculate confluence score based on active strategies.
        Score >= 50 OR 3+ strategies = HIGH PROBABILITY
        """
        active_count = 0
        total_score = 0
        active_strategies = []
        
        for strategy_name, result in strategies.items():
            if result.detected:
                active_count += 1
                active_strategies.append(strategy_name)
                # Weight the strength
                weight = self.confluence_weights.get(strategy_name, 10)
                total_score += (result.strength / 100) * weight
        
        # Normalize score (max possible = sum of all weights)
        max_possible = sum(self.confluence_weights.values())
        final_score = min(int((total_score / max_possible) * 100), 100)
        
        # High probability if score >= 50 OR 3+ strategies
        high_probability = final_score >= 50 or active_count >= 3
        
        description = f"{active_count} strategies active, score {final_score}"
        if high_probability:
            description += " ✓ HIGH PROBABILITY"
        
        return {
            'score': final_score,
            'high_probability': high_probability,
            'active_count': active_count,
            'active_strategies': active_strategies,
            'description': description
        }


# Global instance
strategy_service = StrategyConsolidationService()
