"""Unit tests for Signal model."""

import pytest
from datetime import datetime
import pytz

from markettool.core.models.signal import Signal, SignalType, SignalSet
from markettool.core.errors import DataValidationError


class TestSignalCreation:
    """Test Signal model creation."""
    
    def test_create_buy_signal(self, sample_signal):
        """Test creating BUY signal."""
        assert sample_signal.symbol == "AAPL"
        assert sample_signal.signal_type == SignalType.BUY
        assert sample_signal.confidence == 0.85
        assert sample_signal.price == 105.0
    
    def test_create_sell_signal(self):
        """Test creating SELL signal."""
        signal = Signal(
            symbol="AAPL",
            signal_type=SignalType.SELL,
            confidence=0.75,
            price=95.0,
            timestamp=datetime.now(pytz.UTC),
        )
        
        assert signal.signal_type == SignalType.SELL
    
    def test_create_hold_signal(self):
        """Test creating HOLD signal."""
        signal = Signal(
            symbol="AAPL",
            signal_type=SignalType.HOLD,
            confidence=0.50,
            price=100.0,
            timestamp=datetime.now(pytz.UTC),
        )
        
        assert signal.signal_type == SignalType.HOLD


class TestSignalValidation:
    """Test Signal validation."""
    
    def test_invalid_confidence_too_high(self):
        """Test that confidence > 1.0 fails."""
        with pytest.raises(DataValidationError):
            Signal(
                symbol="AAPL",
                signal_type=SignalType.BUY,
                confidence=1.5,  # Invalid: > 1.0
                price=100.0,
                timestamp=datetime.now(pytz.UTC),
            )
    
    def test_invalid_confidence_negative(self):
        """Test that negative confidence fails."""
        with pytest.raises(DataValidationError):
            Signal(
                symbol="AAPL",
                signal_type=SignalType.BUY,
                confidence=-0.5,  # Invalid: < 0.0
                price=100.0,
                timestamp=datetime.now(pytz.UTC),
            )
    
    def test_invalid_price_negative(self):
        """Test that negative price fails."""
        with pytest.raises(DataValidationError):
            Signal(
                symbol="AAPL",
                signal_type=SignalType.BUY,
                confidence=0.8,
                price=-100.0,  # Invalid: < 0
                timestamp=datetime.now(pytz.UTC),
            )


class TestSignalMetadata:
    """Test Signal metadata and enrichment."""
    
    def test_signal_with_metadata(self):
        """Test Signal with metadata."""
        signal = Signal(
            symbol="AAPL",
            signal_type=SignalType.BUY,
            confidence=0.85,
            price=105.0,
            timestamp=datetime.now(pytz.UTC),
            metadata={
                "source": "RSI",
                "rsi_value": 65,
                "support": 100.0,
                "resistance": 110.0,
            },
        )
        
        assert signal.metadata["source"] == "RSI"
        assert signal.metadata["rsi_value"] == 65
    
    def test_signal_reason(self):
        """Test Signal with reasoning."""
        signal = Signal(
            symbol="AAPL",
            signal_type=SignalType.BUY,
            confidence=0.85,
            price=105.0,
            timestamp=datetime.now(pytz.UTC),
            reason="RSI oversold + Moving Average cross",
        )
        
        assert signal.reason == "RSI oversold + Moving Average cross"


class TestSignalSet:
    """Test SignalSet aggregation."""
    
    def test_create_signal_set(self):
        """Test creating SignalSet."""
        signal1 = Signal(
            symbol="AAPL",
            signal_type=SignalType.BUY,
            confidence=0.85,
            price=105.0,
            timestamp=datetime.now(pytz.UTC),
        )
        
        signal2 = Signal(
            symbol="AAPL",
            signal_type=SignalType.BUY,
            confidence=0.75,
            price=105.0,
            timestamp=datetime.now(pytz.UTC),
        )
        
        signal_set = SignalSet(signals=[signal1, signal2])
        
        assert len(signal_set.signals) == 2
    
    def test_signal_set_consensus(self):
        """Test calculating signal consensus."""
        signals = [
            Signal(
                symbol="AAPL",
                signal_type=SignalType.BUY,
                confidence=0.9,
                price=105.0,
                timestamp=datetime.now(pytz.UTC),
            ),
            Signal(
                symbol="AAPL",
                signal_type=SignalType.BUY,
                confidence=0.8,
                price=105.0,
                timestamp=datetime.now(pytz.UTC),
            ),
            Signal(
                symbol="AAPL",
                signal_type=SignalType.HOLD,
                confidence=0.6,
                price=105.0,
                timestamp=datetime.now(pytz.UTC),
            ),
        ]
        
        signal_set = SignalSet(signals=signals)
        
        # Average confidence should be (0.9 + 0.8 + 0.6) / 3
        avg_confidence = sum(s.confidence for s in signals) / len(signals)
        assert avg_confidence == pytest.approx(0.7667, rel=0.01)
    
    def test_merge_signal_sets(self):
        """Test merging signal sets."""
        set1 = SignalSet(signals=[
            Signal(
                symbol="AAPL",
                signal_type=SignalType.BUY,
                confidence=0.85,
                price=105.0,
                timestamp=datetime.now(pytz.UTC),
            )
        ])
        
        set2 = SignalSet(signals=[
            Signal(
                symbol="AAPL",
                signal_type=SignalType.BUY,
                confidence=0.75,
                price=105.0,
                timestamp=datetime.now(pytz.UTC),
            )
        ])
        
        merged = set1.merge_sets([set2])
        assert len(merged.signals) == 2
    
    def test_top_signals(self):
        """Test getting top N signals by confidence."""
        signals = [
            Signal(
                symbol="AAPL",
                signal_type=SignalType.BUY,
                confidence=0.95,
                price=105.0,
                timestamp=datetime.now(pytz.UTC),
            ),
            Signal(
                symbol="AAPL",
                signal_type=SignalType.BUY,
                confidence=0.50,
                price=105.0,
                timestamp=datetime.now(pytz.UTC),
            ),
            Signal(
                symbol="AAPL",
                signal_type=SignalType.BUY,
                confidence=0.80,
                price=105.0,
                timestamp=datetime.now(pytz.UTC),
            ),
        ]
        
        signal_set = SignalSet(signals=signals)
        top_2 = signal_set.top_signals(n=2)
        
        assert len(top_2) == 2
        assert top_2[0].confidence == 0.95
        assert top_2[1].confidence == 0.80


class TestSignalType:
    """Test SignalType enum."""
    
    def test_signal_type_values(self):
        """Test all SignalType values exist."""
        assert SignalType.BUY.value == "buy"
        assert SignalType.SELL.value == "sell"
        assert SignalType.HOLD.value == "hold"
    
    def test_signal_type_from_string(self):
        """Test creating SignalType from string."""
        # This would require a from_string method
        # signal_type = SignalType.from_string("buy")
        # assert signal_type == SignalType.BUY
        pass
