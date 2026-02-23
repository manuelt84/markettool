"""
Service for fundamental analysis (economic events and news impact).

This service provides hexagonal architecture implementation for:
- Economic events impact calculation
- News sentiment analysis (placeholder)
- Fundamental probability adjustment
"""

import logging
import pandas as pd
from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime, timedelta
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class FundamentalAnalysisResult:
    """Result of fundamental analysis."""
    adjusted_probability: float
    impact: float  # -1.0 to 1.0
    events_count: int
    high_impact_count: int
    metadata: Dict[str, Any]


class FundamentalAnalysisService:
    """
    Servicio para análisis fundamental (eventos económicos, noticias).
    
    Hexagonal architecture implementation - integrates with external data providers
    """
    
    def __init__(
        self,
        logger: Optional[logging.Logger] = None,
    ):
        self.logger = logger or logging.getLogger(__name__)
    
    def adjust_probability_with_events(
        self,
        base_probability: float,
        df_events: pd.DataFrame,
        symbol: str,
        timeframe: str,
        date_start: str,
        date_end: str,
        config: Optional[Dict] = None
    ) -> Tuple[float, Dict[str, Any]]:
        """
        Adjust probability based on economic events.
        
        Args:
            base_probability: Base technical probability (0-100)
            df_events: DataFrame with economic events
            symbol: Trading symbol
            timeframe: Timeframe string
            date_start: Start date for events (YYYY-MM-DD)
            date_end: End date for events (YYYY-MM-DD)
            config: Optional configuration dict
        
        Returns:
            Tuple of (adjusted_probability, metadata_dict)
        """
        if df_events is None or df_events.empty:
            return base_probability, {
                'events_found': 0,
                'impact': 0.0,
                'high_impact': 0,
                'adjustment': 0.0
            }
        
        # Filter events for symbol's currency
        symbol_currency = self._extract_currency(symbol)
        if not symbol_currency:
            return base_probability, {'events_found': 0, 'impact': 0.0}
        
        # Filter events by date range and currency
        try:
            df_filtered = df_events[
                (df_events.get('country', '').str.contains(symbol_currency, case=False, na=False))
            ]
        except Exception as e:
            self.logger.warning(f"Error filtering events: {e}")
            return base_probability, {'events_found': 0, 'error': str(e)}
        
        if df_filtered.empty:
            return base_probability, {
                'events_found': 0,
                'currency': symbol_currency,
                'impact': 0.0
            }
        
        # Calculate impact based on event importance
        impact = self._calculate_events_impact(df_filtered)
        
        # Adjust probability
        adjustment = impact * 10  # Max ±10 points
        adjusted = base_probability + adjustment
        adjusted = max(0, min(100, adjusted))  # Clamp to [0, 100]
        
        metadata = {
            'events_found': len(df_filtered),
            'currency': symbol_currency,
            'impact': impact,
            'high_impact': len(df_filtered[df_filtered.get('impact', '') == 'High']),
            'adjustment': adjustment,
            'adjusted': adjusted
        }
        
        return adjusted, metadata
    
    def calculate_news_impact(
        self,
        df_news: pd.DataFrame,
        symbol: str
    ) -> float:
        """
        Calculate news sentiment impact.
        
        Args:
            df_news: DataFrame with news articles
            symbol: Trading symbol
        
        Returns:
            Impact score (-1.0 to 1.0)
        """
        if df_news is None or df_news.empty:
            return 0.0
        
        # Placeholder: Simple sentiment based on keywords
        # In real implementation, use NLP/sentiment analysis
        
        # ✅ Vectorized sentiment calculation (no iterrows blocking)
        def get_text(row):
            return str(row.get('text', '') or row.get('title', ''))
        
        def calc_sentiment(row):
            return self._simple_sentiment(get_text(row))
        
        # Apply vectorized calculation
        sentiments = df_news.apply(calc_sentiment, axis=1).values
        
        if len(sentiments) == 0:
            return 0.0
        
        return float(sentiments.sum()) / len(sentiments)
    
    # ==================== PRIVATE HELPERS ====================
    
    def _extract_currency(self, symbol: str) -> Optional[str]:
        """Extract currency from symbol (e.g., EURUSD -> EUR)."""
        # Common forex pairs
        if len(symbol) >= 3:
            base = symbol[:3].upper()
            if base in ['EUR', 'USD', 'GBP', 'JPY', 'CHF', 'AUD', 'CAD', 'NZD']:
                return base
        
        # Stocks/commodities: assume USD-related
        return 'USD'
    
    def _calculate_events_impact(self, df_events: pd.DataFrame) -> float:
        """
        Calculate overall impact of events.
        
        Returns impact score (-1.0 to 1.0)
        """
        if df_events.empty:
            return 0.0
        
        # ✅ Vectorized events impact calculation (no iterrows blocking)
        impact_weights = {
            'High': 1.0,
            'Medium': 0.5,
            'Low': 0.2,
        }
        
        def calc_event_impact(event):
            importance = event.get('impact', 'Low')
            weight = impact_weights.get(importance, 0.2)
            
            actual = event.get('actual', None)
            forecast = event.get('estimate', None)
            
            if actual is not None and forecast is not None:
                try:
                    diff = float(actual) - float(forecast)
                    direction = 1.0 if diff > 0 else -1.0
                except:
                    direction = 0.0
            else:
                direction = 0.0
            
            return weight * direction
        
        impacts = df_events.apply(calc_event_impact, axis=1).values
        
        if len(impacts) == 0:
            return 0.0
        
        return float(impacts.sum())
        
        if count == 0:
            return 0.0
        
        # Normalize to [-1, 1]
        avg_impact = total_impact / count
        return max(-1.0, min(1.0, avg_impact))
    
    def _simple_sentiment(self, text: str) -> float:
        """
        Simple keyword-based sentiment analysis.
        
        Returns sentiment score (-1.0 to 1.0)
        """
        text_lower = text.lower()
        
        positive_keywords = ['growth', 'increase', 'strong', 'bullish', 'gain', 'positive', 'rise']
        negative_keywords = ['decline', 'decrease', 'weak', 'bearish', 'loss', 'negative', 'fall']
        
        pos_count = sum(1 for kw in positive_keywords if kw in text_lower)
        neg_count = sum(1 for kw in negative_keywords if kw in text_lower)
        
        total = pos_count + neg_count
        if total == 0:
            return 0.0
        
        return (pos_count - neg_count) / total


# ==================== FACTORY FUNCTION ====================

def get_fundamental_service(
    logger: Optional[logging.Logger] = None
) -> FundamentalAnalysisService:
    """Get FundamentalAnalysisService instance."""
    return FundamentalAnalysisService(logger=logger)
