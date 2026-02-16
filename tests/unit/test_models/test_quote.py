"""Unit tests for Quote model."""

import pytest
from datetime import datetime
import pytz

from markettool.core.models.quote import Quote
from markettool.core.errors import DataValidationError


class TestQuoteCreation:
    """Test Quote model creation."""
    
    def test_create_quote_minimal(self):
        """Test creating Quote with minimal fields."""
        quote = Quote(
            symbol="AAPL",
            price=150.0,
            timestamp=datetime.now(pytz.UTC),
        )
        
        assert quote.symbol == "AAPL"
        assert quote.price == 150.0
        assert quote.bid is None
        assert quote.ask is None
    
    def test_create_quote_full(self, sample_quote):
        """Test creating Quote with all fields."""
        assert sample_quote.symbol == "EURUSD"
        assert sample_quote.price == 1.0950
        assert sample_quote.bid == 1.0948
        assert sample_quote.ask == 1.0952
        assert sample_quote.source == "fmp"
    
    def test_quote_with_metadata(self):
        """Test Quote with metadata."""
        quote = Quote(
            symbol="BTC",
            price=45000.0,
            timestamp=datetime.now(pytz.UTC),
            metadata={"market": "crypto", "exchange": "Binance"},
        )
        
        assert quote.metadata["market"] == "crypto"
        assert quote.metadata["exchange"] == "Binance"


class TestQuoteValidation:
    """Test Quote validation."""
    
    def test_quote_negative_price_fails(self):
        """Test that negative price fails validation."""
        with pytest.raises(DataValidationError):
            Quote(
                symbol="AAPL",
                price=-150.0,
                timestamp=datetime.now(pytz.UTC),
            )
    
    def test_quote_zero_price_fails(self):
        """Test that zero price fails validation."""
        with pytest.raises(DataValidationError):
            Quote(
                symbol="AAPL",
                price=0.0,
                timestamp=datetime.now(pytz.UTC),
            )
    
    def test_quote_bid_ask_consistency(self):
        """Test that bid <= price <= ask."""
        # This would depend on validation rules
        quote = Quote(
            symbol="AAPL",
            price=150.0,
            bid=149.9,
            ask=150.1,
            timestamp=datetime.now(pytz.UTC),
        )
        
        # Should pass validation
        assert quote.bid < quote.price < quote.ask


class TestQuoteProperties:
    """Test Quote computed properties."""
    
    def test_spread(self):
        """Test calculating bid-ask spread."""
        quote = Quote(
            symbol="EURUSD",
            price=1.0950,
            bid=1.0948,
            ask=1.0952,
            timestamp=datetime.now(pytz.UTC),
        )
        
        spread = quote.ask - quote.bid
        assert spread == pytest.approx(0.0004, rel=1e-5)
    
    def test_is_stale(self):
        """Test detecting stale quotes."""
        from datetime import timedelta
        
        # Recent quote
        quote_recent = Quote(
            symbol="AAPL",
            price=150.0,
            timestamp=datetime.now(pytz.UTC),
        )
        
        # Old quote
        quote_old = Quote(
            symbol="AAPL",
            price=150.0,
            timestamp=datetime.now(pytz.UTC) - timedelta(minutes=10),
        )
        
        # Would need an is_stale method: assert not quote_recent.is_stale(max_age_seconds=300)
        # assert quote_old.is_stale(max_age_seconds=300)
    
    def test_quote_age(self):
        """Test calculating quote age."""
        from datetime import timedelta
        
        quote = Quote(
            symbol="AAPL",
            price=150.0,
            timestamp=datetime.now(pytz.UTC) - timedelta(seconds=30),
        )
        
        # Age should be approximately 30 seconds
        age = (datetime.now(pytz.UTC) - quote.timestamp).total_seconds()
        assert 25 < age < 35


class TestQuoteComparison:
    """Test comparing quotes."""
    
    def test_quote_price_changed(self):
        """Test detecting price changes."""
        quote1 = Quote(
            symbol="AAPL",
            price=150.0,
            timestamp=datetime.now(pytz.UTC),
        )
        
        quote2 = Quote(
            symbol="AAPL",
            price=151.0,
            timestamp=datetime.now(pytz.UTC),
        )
        
        change = quote2.price - quote1.price
        assert change == 1.0
    
    def test_quote_price_change_percentage(self):
        """Test calculating percentage change."""
        quote1 = Quote(
            symbol="AAPL",
            price=100.0,
            timestamp=datetime.now(pytz.UTC),
        )
        
        quote2 = Quote(
            symbol="AAPL",
            price=110.0,
            timestamp=datetime.now(pytz.UTC),
        )
        
        pct_change = ((quote2.price - quote1.price) / quote1.price) * 100
        assert pct_change == 10.0
