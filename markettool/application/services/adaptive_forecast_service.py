"""
Adaptive Forecast Service

Provides timeframe-adaptive forecasting to improve prediction speed and accuracy.

Selects the best forecasting method based on timeframe:
- Intraday (1m-4h): EMA-based momentum forecast (fast, lightweight)
- Daily (1d): ARIMA (1,1,1) (balanced)
- Weekly+ (1w+): Prophet if available, else ARIMA (handles seasonality)
"""

import logging
import pandas as pd
import numpy as np
from typing import Dict, Optional, List, Any, Tuple
from dataclasses import dataclass
import warnings

logger = logging.getLogger(__name__)

# Try to import Prophet (optional dependency)
try:
    from prophet import Prophet
    HAS_PROPHET = True
except ImportError:
    HAS_PROPHET = False
    logger.warning("Prophet not installed. Weekly+ timeframes will use ARIMA fallback.")


@dataclass
class ForecastResult:
    """Forecast prediction result"""
    forecast_values: List[float]
    forecast_dates: List[pd.Timestamp]
    method_used: str
    confidence_interval: Tuple[float, float]  # (lower, upper)
    success: bool
    error_message: Optional[str] = None


class AdaptiveForecastService:
    """
    Provides adaptive forecasting based on timeframe.
    
    Method Selection:
    - 1m, 5m, 15m, 1h, 4h: EMA momentum (exponential moving average-based)
    - 1d: ARIMA(1,1,1) 
    - 1w+: Prophet (if available) or ARIMA
    """
    
    def __init__(self, logger: Optional[logging.Logger] = None):
        self.logger = logger or logging.getLogger(__name__)
    
    def forecast(
        self,
        df: pd.DataFrame,
        timeframe: str,
        steps: int = 5,
        closing_column: str = 'close',
    ) -> ForecastResult:
        """
        Forecast future price movement with adaptive method.
        
        Args:
            df: OHLCV DataFrame with price data
            timeframe: Trading timeframe (e.g., '1hour', '1day', '1week')
            steps: Number of periods to forecast
            closing_column: Column name for closing prices
        
        Returns:
            ForecastResult with predictions and method used
        """
        
        if len(df) < 3:
            return ForecastResult(
                forecast_values=[],
                forecast_dates=[],
                method_used='none',
                confidence_interval=(0, 0),
                success=False,
                error_message='Insufficient data',
            )
        
        # Select method based on timeframe
        if self._is_intraday_tf(timeframe):
            return self._forecast_ema(df, steps, closing_column)
        elif self._is_daily_tf(timeframe):
            return self._forecast_arima(df, steps, closing_column)
        else:  # Weekly and above
            if HAS_PROPHET:
                return self._forecast_prophet(df, steps, closing_column)
            else:
                return self._forecast_arima(df, steps, closing_column)
    
    def _is_intraday_tf(self, timeframe: str) -> bool:
        """Check if timeframe is intraday"""
        intraday_tfs = ['1min', '5min', '15min', '30min', '1hour', '4hour']
        return timeframe in intraday_tfs
    
    def _is_daily_tf(self, timeframe: str) -> bool:
        """Check if timeframe is daily"""
        daily_tfs = ['1day', 'daily', '1d']
        return timeframe.lower() in daily_tfs
    
    def _forecast_ema(
        self,
        df: pd.DataFrame,
        steps: int,
        closing_column: str,
    ) -> ForecastResult:
        """
        EMA-based momentum forecast for intraday timeframes.
        
        Fast, lightweight method suitable for 1m-4h analysis.
        Uses exponential moving average trends for momentum.
        """
        try:
            closes = df[closing_column].values
            
            # Calculate EMAs for momentum
            ema_fast = self._calculate_ema(closes, 12)
            ema_slow = self._calculate_ema(closes, 26)
            
            # Calculate momentum
            momentum = ema_fast[-1] - ema_slow[-1]
            current_price = closes[-1]
            
            # Forecast: assume momentum continues with decay
            forecasts = []
            confidence_lower = current_price
            confidence_upper = current_price
            
            for i in range(steps):
                # Momentum decays slightly each step
                decay_factor = 0.95 ** (i + 1)
                step_momentum = momentum * decay_factor
                
                # Normalize by ATR for scale
                atr = np.mean(df['high'].values - df['low'].values) if 'high' in df else 0.01 * current_price
                
                # Predict price
                predicted_price = current_price + (step_momentum / atr) * 0.001
                forecasts.append(predicted_price)
                
                # Track confidence bounds
                confidence_lower = min(confidence_lower, predicted_price - atr * 0.02)
                confidence_upper = max(confidence_upper, predicted_price + atr * 0.02)
            
            # Generate dates
            last_date = df.index[-1] if isinstance(df.index, pd.DatetimeIndex) else pd.Timestamp.now()
            forecast_dates = [
                last_date + pd.Timedelta(hours=(i+1)) for i in range(steps)
            ]
            
            return ForecastResult(
                forecast_values=forecasts,
                forecast_dates=forecast_dates,
                method_used='ema_momentum',
                confidence_interval=(confidence_lower, confidence_upper),
                success=True,
            )
        
        except Exception as e:
            self.logger.warning(f"EMA forecast failed: {e}")
            return ForecastResult(
                forecast_values=[],
                forecast_dates=[],
                method_used='ema_momentum',
                confidence_interval=(0, 0),
                success=False,
                error_message=str(e),
            )
    
    def _forecast_arima(
        self,
        df: pd.DataFrame,
        steps: int,
        closing_column: str,
    ) -> ForecastResult:
        """
        ARIMA(1,1,1) forecast for daily timeframes.
        
        Simple, proven method for 1d+ analysis.
        Uses statsmodels if available, else simple differencing.
        """
        try:
            # Try to import statsmodels
            try:
                from statsmodels.tsa.arima.model import ARIMA
            except ImportError:
                self.logger.warning("statsmodels not installed, using simple differencing forecast")
                return self._forecast_simple_difference(df, steps, closing_column)
            
            closes = df[closing_column].values
            
            # Fit ARIMA(1,1,1)
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                model = ARIMA(closes, order=(1, 1, 1))
                fitted = model.fit(disp=False)
                
                # Forecast
                forecast_result = fitted.get_forecast(steps=steps)
                forecasts = forecast_result.predicted_mean.values
                
                # Get confidence interval
                conf_int = forecast_result.conf_int(alpha=0.05)
                lower = conf_int.iloc[:, 0].values[0]
                upper = conf_int.iloc[:, 1].values[0]
            
            # Generate dates
            last_date = df.index[-1] if isinstance(df.index, pd.DatetimeIndex) else pd.Timestamp.now()
            forecast_dates = [
                last_date + pd.Timedelta(days=(i+1)) for i in range(steps)
            ]
            
            return ForecastResult(
                forecast_values=forecasts.tolist(),
                forecast_dates=forecast_dates,
                method_used='arima(1,1,1)',
                confidence_interval=(lower, upper),
                success=True,
            )
        
        except Exception as e:
            self.logger.warning(f"ARIMA forecast failed: {e}")
            return self._forecast_simple_difference(df, steps, closing_column)
    
    def _forecast_prophet(
        self,
        df: pd.DataFrame,
        steps: int,
        closing_column: str,
    ) -> ForecastResult:
        """
        Prophet forecast for weekly and monthly timeframes.
        
        Handles seasonality and trend changes well.
        Good for longer-term forecasts.
        """
        try:
            if not HAS_PROPHET:
                return self._forecast_arima(df, steps, closing_column)
            
            # Prepare data for Prophet
            df_prophet = df[[closing_column]].copy()
            df_prophet.index.name = 'ds'
            df_prophet = df_prophet.reset_index()
            df_prophet.columns = ['ds', 'y']
            
            # Make sure ds is datetime
            if not isinstance(df_prophet['ds'], pd.DatetimeIndex):
                df_prophet['ds'] = pd.to_datetime(df_prophet['ds'])
            
            # Fit Prophet
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                model = Prophet(
                    yearly_seasonality=True,
                    weekly_seasonality=True,
                    daily_seasonality=False,
                    interval_width=0.95,
                    changepoint_prior_scale=0.05
                )
                model.fit(df_prophet)
                
                # Create future dataframe
                future = model.make_future_dataframe(periods=steps, freq='D')
                forecast = model.predict(future)
                
                # Get last 'steps' predictions
                forecast_subset = forecast.tail(steps)
                forecasts = forecast_subset['yhat'].values.tolist()
                lower = forecast_subset['yhat_lower'].values[0]
                upper = forecast_subset['yhat_upper'].values[0]
                forecast_dates = forecast_subset['ds'].tolist()
            
            return ForecastResult(
                forecast_values=forecasts,
                forecast_dates=forecast_dates,
                method_used='prophet',
                confidence_interval=(lower, upper),
                success=True,
            )
        
        except Exception as e:
            self.logger.warning(f"Prophet forecast failed: {e}")
            return self._forecast_arima(df, steps, closing_column)
    
    def _forecast_simple_difference(
        self,
        df: pd.DataFrame,
        steps: int,
        closing_column: str,
    ) -> ForecastResult:
        """
        Simple differencing forecast (fallback method).
        
        Uses last price change as extrapolation.
        """
        try:
            closes = df[closing_column].values
            
            # Calculate average change
            changes = np.diff(closes)
            avg_change = np.mean(changes[-10:]) if len(changes) > 10 else np.mean(changes)
            
            # Forecast
            current_price = closes[-1]
            forecasts = [current_price + (avg_change * (i + 1)) for i in range(steps)]
            
            # Confidence bounds
            std_change = np.std(changes[-10:]) if len(changes) > 10 else np.std(changes)
            lower = current_price - (std_change * 2)
            upper = current_price + (std_change * 2)
            
            # Generate dates
            last_date = df.index[-1] if isinstance(df.index, pd.DatetimeIndex) else pd.Timestamp.now()
            forecast_dates = [last_date + pd.Timedelta(days=(i+1)) for i in range(steps)]
            
            return ForecastResult(
                forecast_values=forecasts,
                forecast_dates=forecast_dates,
                method_used='simple_difference',
                confidence_interval=(lower, upper),
                success=True,
            )
        
        except Exception as e:
            self.logger.error(f"Simple difference forecast failed: {e}")
            return ForecastResult(
                forecast_values=[],
                forecast_dates=[],
                method_used='simple_difference',
                confidence_interval=(0, 0),
                success=False,
                error_message=str(e),
            )
    
    @staticmethod
    def _calculate_ema(values: np.ndarray, period: int) -> np.ndarray:
        """Calculate exponential moving average"""
        if len(values) < period:
            return values
        
        ema = np.zeros(len(values))
        ema[:period] = np.mean(values[:period])
        
        multiplier = 2.0 / (period + 1)
        
        for i in range(period, len(values)):
            ema[i] = (values[i] - ema[i-1]) * multiplier + ema[i-1]
        
        return ema


# ==================== FACTORY ====================

def get_adaptive_forecast_service(
    logger: Optional[logging.Logger] = None
) -> AdaptiveForecastService:
    """Get AdaptiveForecastService instance"""
    return AdaptiveForecastService(logger=logger)
