"""Unit tests for domain models (Historico, Quote, Signal)."""

import unittest
from datetime import datetime
import pandas as pd
import pytz

from markettool.core.models.historico import Historico, TIMEFRAMES
from markettool.core.models.quote import Quote
from markettool.core.models.signal import Signal, SignalType, SignalSet


class TestHistorico(unittest.TestCase):
    """Tests for Historico domain model."""
    
    def setUp(self):
        """Create test data."""
        self.test_data = pd.DataFrame({
            'open': [100, 101, 102],
            'high': [101, 102, 103],
            'low': [99, 100, 101],
            'close': [100.5, 101.5, 102.5],
            'volume': [1000, 1100, 1200],
        }, index=pd.date_range('2024-01-01', periods=3, freq='D'))
    
    def test_historico_creation(self):
        """Test creating Historico instance."""
        hist = Historico(
            symbol='AAPL',
            timeframe='1d',
            data=self.test_data,
            last_updated=datetime.now(pytz.UTC),
        )
        
        self.assertEqual(hist.symbol, 'AAPL')
        self.assertEqual(hist.timeframe, '1d')
        self.assertEqual(len(hist.data), 3)
        self.assertIn('close', hist.data.columns)
    
    def test_historico_validation(self):
        """Test Historico validates required columns."""
        # Missing 'close' column
        bad_data = pd.DataFrame({
            'open': [100, 101, 102],
            'high': [101, 102, 103],
            'low': [99, 100, 101],
        })
        
        with self.assertRaises(ValueError):
            Historico(
                symbol='AAPL',
                timeframe='1d',
                data=bad_data,
            )
    
    def test_historico_resample(self):
        """Test resampling to different timeframe."""
        hist = Historico(
            symbol='AAPL',
            timeframe='1d',
            data=self.test_data,
        )
        
        # Resample to 2-day candles
        resampled = hist.resample('2D')
        self.assertEqual(resampled.timeframe, '2D')
        self.assertEqual(len(resampled.data), 2)  # 3 days -> 2 candles
    
    def test_historico_last_candle(self):
        """Test getting last candle."""
        hist = Historico(
            symbol='AAPL',
            timeframe='1d',
            data=self.test_data,
        )
        
        last = hist.last_candle()
        self.assertEqual(last['close'], 102.5)
        self.assertEqual(last['volume'], 1200)
    
    def test_historico_empty(self):
        """Test empty Historico."""
        hist = Historico(
            symbol='AAPL',
            timeframe='1d',
            data=pd.DataFrame(columns=['open', 'high', 'low', 'close', 'volume']),
        )
        
        self.assertTrue(hist.is_empty())


class TestQuote(unittest.TestCase):
    """Tests for Quote domain model."""
    
    def test_quote_creation(self):
        """Test creating Quote instance."""
        now = datetime.now(pytz.UTC)
        quote = Quote(
            symbol='AAPL',
            price=150.25,
            timestamp=now,
        )
        
        self.assertEqual(quote.symbol, 'AAPL')
        self.assertEqual(quote.price, 150.25)
        self.assertEqual(quote.timestamp, now)
    
    def test_quote_with_bid_ask(self):
        """Test Quote with bid/ask spread."""
        quote = Quote(
            symbol='EURUSD',
            price=1.0950,
            bid=1.0948,
            ask=1.0952,
        )
        
        self.assertIsNotNone(quote.bid)
        self.assertIsNotNone(quote.ask)
        self.assertEqual(quote.ask - quote.bid, 0.0004)
    
    def test_quote_with_metadata(self):
        """Test Quote with additional metadata."""
        quote = Quote(
            symbol='AAPL',
            price=150.25,
            metadata={
                'source': 'fmp',
                'confidence': 0.99,
            }
        )
        
        self.assertEqual(quote.metadata['source'], 'fmp')
        self.assertEqual(quote.metadata['confidence'], 0.99)


class TestSignal(unittest.TestCase):
    """Tests for Signal and SignalSet domain models."""
    
    def test_signal_creation(self):
        """Test creating Signal instance."""
        now = datetime.now(pytz.UTC)
        signal = Signal(
            symbol='AAPL',
            signal_type=SignalType.BUY,
            timestamp=now,
            confidence=0.85,
            entry_price=150.25,
        )
        
        self.assertEqual(signal.symbol, 'AAPL')
        self.assertEqual(signal.signal_type, SignalType.BUY)
        self.assertEqual(signal.confidence, 0.85)
    
    def test_signal_types(self):
        """Test all signal types."""
        now = datetime.now(pytz.UTC)
        for sig_type in [SignalType.BUY, SignalType.SELL, SignalType.HOLD]:
            signal = Signal(
                symbol='AAPL',
                signal_type=sig_type,
                timestamp=now,
                confidence=0.75,
            )
            self.assertEqual(signal.signal_type, sig_type)
    
    def test_signal_with_metadata(self):
        """Test Signal with indicator metadata."""
        now = datetime.now(pytz.UTC)
        signal = Signal(
            symbol='AAPL',
            signal_type=SignalType.BUY,
            timestamp=now,
            confidence=0.85,
            indicators={
                'rsi': 35.2,
                'macd_cross': True,
                'price_action': 'bullish',
            }
        )
        
        self.assertEqual(signal.indicators['rsi'], 35.2)
        self.assertTrue(signal.indicators['macd_cross'])
    
    def test_signal_set_creation(self):
        """Test creating SignalSet with multiple signals."""
        now = datetime.now(pytz.UTC)
        signals = [
            Signal(symbol='AAPL', signal_type=SignalType.BUY, timestamp=now, confidence=0.85),
            Signal(symbol='AAPL', signal_type=SignalType.BUY, timestamp=now, confidence=0.90),
            Signal(symbol='AAPL', signal_type=SignalType.HOLD, timestamp=now, confidence=0.70),
        ]
        
        signal_set = SignalSet(signals=signals)
        self.assertEqual(len(signal_set.signals), 3)
    
    def test_signal_set_top_signals(self):
        """Test getting top N signals by confidence."""
        now = datetime.now(pytz.UTC)
        signals = [
            Signal(symbol='AAPL', signal_type=SignalType.BUY, timestamp=now, confidence=0.65),
            Signal(symbol='AAPL', signal_type=SignalType.BUY, timestamp=now, confidence=0.90),
            Signal(symbol='AAPL', signal_type=SignalType.HOLD, timestamp=now, confidence=0.75),
        ]
        
        signal_set = SignalSet(signals=signals)
        top_2 = signal_set.top_signals(n=2)
        
        self.assertEqual(len(top_2), 2)
        self.assertEqual(top_2[0].confidence, 0.90)
        self.assertEqual(top_2[1].confidence, 0.75)
    
    def test_signal_set_merge(self):
        """Test merging signal sets."""
        now = datetime.now(pytz.UTC)
        set1 = SignalSet(signals=[
            Signal(symbol='AAPL', signal_type=SignalType.BUY, timestamp=now, confidence=0.85),
        ])
        
        set2 = SignalSet(signals=[
            Signal(symbol='AAPL', signal_type=SignalType.HOLD, timestamp=now, confidence=0.70),
        ])
        
        merged = SignalSet.merge_sets([set1, set2])
        self.assertEqual(len(merged.signals), 2)


if __name__ == '__main__':
    unittest.main()
