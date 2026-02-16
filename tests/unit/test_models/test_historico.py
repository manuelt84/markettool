"""Unit tests for Historico model."""

import pytest
import pandas as pd
from datetime import datetime, timedelta
import pytz

from markettool.core.models.historico import Historico
from markettool.core.errors import DataValidationError


class TestHistoricoCreation:
    """Test Historico model creation and initialization."""
    
    def test_create_historico_with_lists(self, sample_historico):
        """Test creating Historico with list data."""
        assert sample_historico.symbol == "AAPL"
        assert sample_historico.timeframe == "1h"
        assert len(sample_historico.open) == 3
        assert len(sample_historico.close) == 3
    
    def test_create_historico_with_numpy_arrays(self):
        """Test creating Historico with numpy arrays."""
        import numpy as np
        
        historico = Historico(
            symbol="GOOGL",
            timeframe="4h",
            open=np.array([150.0, 151.0]),
            high=np.array([151.0, 152.0]),
            low=np.array([149.0, 150.0]),
            close=np.array([150.5, 151.5]),
            volume=np.array([500000, 600000]),
            timestamps=[
                datetime.now(pytz.UTC) - timedelta(hours=4),
                datetime.now(pytz.UTC),
            ],
        )
        
        assert historico.symbol == "GOOGL"
        assert len(historico.close) == 2
    
    def test_historico_validation_mismatched_lengths(self):
        """Test validation fails with mismatched array lengths."""
        with pytest.raises(DataValidationError):
            Historico(
                symbol="MSFT",
                timeframe="1d",
                open=[100.0, 101.0],
                high=[102.0, 103.0],
                low=[99.0, 100.0],
                close=[100.5, 101.5, 102.5],  # Different length!
                volume=[1000000, 1100000],
                timestamps=[
                    datetime.now(pytz.UTC),
                    datetime.now(pytz.UTC) + timedelta(days=1),
                ],
            )


class TestHistoricoOperations:
    """Test Historico operations and transformations."""
    
    def test_get_length(self, sample_historico):
        """Test getting length of historico."""
        assert len(sample_historico) == 3
    
    def test_to_dataframe(self, sample_historico):
        """Test converting to DataFrame."""
        df = sample_historico.to_dataframe()
        
        assert isinstance(df, pd.DataFrame)
        assert len(df) == 3
        assert "open" in df.columns
        assert "close" in df.columns
        assert "volume" in df.columns
    
    def test_resample_to_higher_timeframe(self, sample_historico):
        """Test resampling to higher timeframe."""
        # Create historico with hourly data
        now = datetime.now(pytz.UTC)
        historico = Historico(
            symbol="AAPL",
            timeframe="1h",
            open=[100.0, 101.0, 102.0, 103.0],
            high=[101.0, 102.0, 103.0, 104.0],
            low=[99.0, 100.0, 101.0, 102.0],
            close=[100.5, 101.5, 102.5, 103.5],
            volume=[1M for 1M in [1000000, 1100000, 1200000, 1300000]],
            timestamps=[now - timedelta(hours=3), now - timedelta(hours=2),
                       now - timedelta(hours=1), now],
        )
        
        # This would test resampling (method not yet implemented)
        # resampled = historico.resample("4h")
        # assert len(resampled) == 1
    
    def test_merge_historicos(self, sample_historico):
        """Test merging two Historico objects."""
        historico1 = sample_historico
        
        now = datetime.now(pytz.UTC)
        historico2 = Historico(
            symbol="AAPL",
            timeframe="1h",
            open=[103.0, 104.0],
            high=[104.0, 105.0],
            low=[102.0, 103.0],
            close=[103.5, 104.5],
            volume=[1400000, 1500000],
            timestamps=[now + timedelta(hours=1), now + timedelta(hours=2)],
        )
        
        merged = historico1.merge(historico2)
        assert len(merged) == 5
        assert merged.symbol == "AAPL"


class TestHistoricoProperties:
    """Test Historico properties and computed values."""
    
    def test_current_price(self, sample_historico):
        """Test getting current (closing) price."""
        current = sample_historico.current_price()
        assert current == 102.5
    
    def test_change(self, sample_historico):
        """Test calculating change from open to close."""
        change = sample_historico.change()
        assert change == 102.5 - 100.0
    
    def test_min_max(self, sample_historico):
        """Test getting min/max values."""
        min_val = sample_historico.min()
        max_val = sample_historico.max()
        
        assert min_val == 99.0  # Lowest low
        assert max_val == 103.0  # Highest high
    
    def test_is_bullish(self, sample_historico):
        """Test detecting bullish candles."""
        # Last candle: close (102.5) > open (102.0) = bullish
        assert sample_historico.is_bullish()
    
    def test_total_volume(self, sample_historico):
        """Test calculating total volume."""
        total = sample_historico.total_volume()
        expected = 1000000 + 1100000 + 1200000
        assert total == expected
    
    def test_average_volume(self, sample_historico):
        """Test calculating average volume."""
        avg = sample_historico.average_volume()
        expected = (1000000 + 1100000 + 1200000) / 3
        assert avg == expected
    
    def test_volatility(self, sample_historico):
        """Test calculating volatility (high-low range)."""
        vol = sample_historico.volatility()
        # Last candle: high (103.0) - low (101.0) = 2.0
        assert vol == 2.0
