"""Use case: Run analysis and generate signals."""

from __future__ import annotations

import logging
from typing import Optional

from markettool.core.models.historico import Historico
from markettool.core.models.signal import Signal, SignalType, SignalSet
from markettool.core.ports.cache_provider import CacheProvider
from markettool.core.errors import AnalysisError, InsufficientDataError


class RunAnalysisUseCase:
    """
    Orchestrates technical analysis to generate trading signals.
    """
    
    def __init__(
        self,
        cache_provider: CacheProvider,
        logger: Optional[logging.Logger] = None,
    ):
        self.cache = cache_provider
        self.logger = logger or logging.getLogger(__name__)
    
    async def execute(
        self,
        historico: Historico,
        analysis_type: str = "technical",
    ) -> SignalSet:
        """
        Run analysis on historical data.
        
        Args:
            historico: Historical data to analyze
            analysis_type: Type of analysis ("technical", "fundamental", "news", "pattern")
            
        Returns:
            SignalSet with generated signals
            
        Raises:
            InsufficientDataError: If not enough data
            AnalysisError: If analysis fails
        """
        if historico.is_empty or historico.length < 5:
            raise InsufficientDataError(
                f"Insufficient data for analysis: {historico.length} candles"
            )
        
        self.logger.info(
            f"Running {analysis_type} analysis on {historico.symbol}/{historico.timeframe}"
        )
        
        signals = SignalSet()
        
        try:
            if analysis_type == "technical":
                signal = await self._technical_analysis(historico)
            elif analysis_type == "pattern":
                signal = await self._pattern_analysis(historico)
            else:
                raise AnalysisError(f"Unknown analysis type: {analysis_type}")
            
            if signal:
                signals.add(signal)
                self.logger.info(f"Generated signal: {signal}")
            else:
                self.logger.debug(f"No signals generated for {historico.symbol}")
        
        except Exception as e:
            self.logger.exception(f"Analysis failed: {e}")
            raise AnalysisError(f"Analysis of {historico.symbol} failed: {e}")
        
        return signals
    
    async def _technical_analysis(self, historico: Historico) -> Optional[Signal]:
        """
        Basic technical analysis using simple rules.
        In real app, would integrate with indicators service.
        """
        from datetime import datetime
        import pytz
        
        if historico.length < 5:
            return None
        
        df = historico.df.tail(20)
        
        last_close = float(df.iloc[-1]["close"])
        sma_20 = float(df["close"].mean())
        
        # Simple rule: check position relative to moving average
        confidence = 0.0
        signal_type = SignalType.NEUTRAL
        
        if last_close > sma_20 * 1.02:
            signal_type = SignalType.BUY
            confidence = 0.6
        elif last_close < sma_20 * 0.98:
            signal_type = SignalType.SELL
            confidence = 0.6
        
        if confidence > 0:
            return Signal(
                symbol=historico.symbol,
                signal_type=signal_type,
                timestamp=datetime.now(pytz.UTC),
                confidence=confidence,
                reason=f"Price {last_close:.2f} vs SMA20 {sma_20:.2f}",
                indicators={"SMA20": sma_20, "lastClose": last_close},
                source="technical_analysis",
                analysis_type="technical",
            )
        
        return None
    
    async def _pattern_analysis(self, historico: Historico) -> Optional[Signal]:
        """
        Pattern-based analysis.
        In real app, would use pattern recognition models.
        """
        # Placeholder for pattern recognition
        self.logger.debug(f"Pattern analysis not yet implemented for {historico.symbol}")
        return None
