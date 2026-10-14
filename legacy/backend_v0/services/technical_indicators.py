# backend/services/technical_indicators.py
"""
Technical Indicators Calculator

Pure calculation service for Technical Analyst Agent
Deterministic, fast, no LLM calls
"""

import logging
import pandas as pd
import numpy as np
from typing import Dict, List, Tuple, Optional
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


class TechnicalIndicators:
    """
    Calculates technical indicators for trading analysis

    All methods are deterministic: same input = same output
    Optimized for speed (<100ms per analysis)
    """

    @staticmethod
    def calculate_rsi(prices: pd.Series, period: int = 14) -> pd.Series:
        """
        Calculate RSI (Relative Strength Index)

        Args:
            prices: Series of close prices
            period: RSI period (default 14)

        Returns:
            RSI values (0-100)
        """
        delta = prices.diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()

        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))

        return rsi

    @staticmethod
    def calculate_bollinger_bands(
        prices: pd.Series,
        period: int = 20,
        std_dev: float = 2.0
    ) -> Tuple[pd.Series, pd.Series, pd.Series]:
        """
        Calculate Bollinger Bands

        Args:
            prices: Series of close prices
            period: SMA period (default 20)
            std_dev: Standard deviations (default 2.0)

        Returns:
            Tuple of (upper_band, middle_band, lower_band)
        """
        middle = prices.rolling(window=period).mean()
        std = prices.rolling(window=period).std()

        upper = middle + (std * std_dev)
        lower = middle - (std * std_dev)

        return upper, middle, lower

    @staticmethod
    def calculate_atr(
        high: pd.Series,
        low: pd.Series,
        close: pd.Series,
        period: int = 14
    ) -> pd.Series:
        """
        Calculate ATR (Average True Range)

        Args:
            high: Series of high prices
            low: Series of low prices
            close: Series of close prices
            period: ATR period (default 14)

        Returns:
            ATR values
        """
        # True Range = max of:
        # 1. Current high - current low
        # 2. abs(current high - previous close)
        # 3. abs(current low - previous close)

        prev_close = close.shift(1)

        tr1 = high - low
        tr2 = abs(high - prev_close)
        tr3 = abs(low - prev_close)

        true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

        atr = true_range.rolling(window=period).mean()

        return atr

    @staticmethod
    def calculate_ema(prices: pd.Series, period: int) -> pd.Series:
        """
        Calculate EMA (Exponential Moving Average)

        Args:
            prices: Series of prices
            period: EMA period

        Returns:
            EMA values
        """
        return prices.ewm(span=period, adjust=False).mean()

    @staticmethod
    def calculate_vwap(
        high: pd.Series,
        low: pd.Series,
        close: pd.Series,
        volume: pd.Series
    ) -> pd.Series:
        """
        Calculate VWAP (Volume Weighted Average Price)

        Resets daily (assumes data is sorted by time)

        Args:
            high, low, close: Price series
            volume: Volume series

        Returns:
            VWAP values
        """
        typical_price = (high + low + close) / 3
        vwap = (typical_price * volume).cumsum() / volume.cumsum()

        return vwap

    @staticmethod
    def calculate_volume_ma(volume: pd.Series, period: int = 20) -> pd.Series:
        """
        Calculate Volume Moving Average

        Args:
            volume: Series of volume
            period: MA period (default 20)

        Returns:
            Volume MA values
        """
        return volume.rolling(window=period).mean()

    @staticmethod
    def find_support_resistance(
        high: pd.Series,
        low: pd.Series,
        close: pd.Series,
        lookback: int = 20
    ) -> Tuple[float, float]:
        """
        Find recent support and resistance levels

        Args:
            high, low, close: Price series
            lookback: Number of candles to look back (default 20)

        Returns:
            Tuple of (support, resistance)
        """
        recent_high = high.tail(lookback)
        recent_low = low.tail(lookback)

        resistance = recent_high.max()
        support = recent_low.min()

        return support, resistance

    @staticmethod
    def detect_ema_crossover(
        fast_ema: pd.Series,
        slow_ema: pd.Series
    ) -> str:
        """
        Detect EMA crossover

        Args:
            fast_ema: Fast EMA series
            slow_ema: Slow EMA series

        Returns:
            'BULLISH' (fast > slow), 'BEARISH' (fast < slow), or 'NEUTRAL'
        """
        if len(fast_ema) < 2 or len(slow_ema) < 2:
            return 'NEUTRAL'

        # Current state
        current_fast = fast_ema.iloc[-1]
        current_slow = slow_ema.iloc[-1]

        # Previous state
        prev_fast = fast_ema.iloc[-2]
        prev_slow = slow_ema.iloc[-2]

        # Bullish crossover: fast crosses above slow
        if prev_fast <= prev_slow and current_fast > current_slow:
            return 'BULLISH'

        # Bearish crossover: fast crosses below slow
        if prev_fast >= prev_slow and current_fast < current_slow:
            return 'BEARISH'

        # No crossover
        if current_fast > current_slow:
            return 'BULLISH'
        elif current_fast < current_slow:
            return 'BEARISH'
        else:
            return 'NEUTRAL'

    @staticmethod
    def calculate_all_indicators(
        df: pd.DataFrame,
        params: Optional[Dict] = None
    ) -> Dict:
        """
        Calculate all indicators for a timeframe

        Args:
            df: DataFrame with columns: Open, High, Low, Close, Volume
            params: Optional parameter overrides

        Returns:
            Dict of calculated indicators with latest values
        """
        if params is None:
            params = {
                'rsi_period': 14,
                'bb_period': 20,
                'bb_std': 2.0,
                'atr_period': 14,
                'ema_fast': 9,
                'ema_slow': 21,
                'volume_period': 20,
                'sr_lookback': 20
            }

        # Ensure columns are capitalized
        required_cols = ['Open', 'High', 'Low', 'Close', 'Volume']
        for col in required_cols:
            if col not in df.columns and col.lower() in df.columns:
                df[col] = df[col.lower()]

        # Calculate indicators
        rsi = TechnicalIndicators.calculate_rsi(
            df['Close'],
            period=params['rsi_period']
        )

        bb_upper, bb_middle, bb_lower = TechnicalIndicators.calculate_bollinger_bands(
            df['Close'],
            period=params['bb_period'],
            std_dev=params['bb_std']
        )

        atr = TechnicalIndicators.calculate_atr(
            df['High'],
            df['Low'],
            df['Close'],
            period=params['atr_period']
        )

        ema_fast = TechnicalIndicators.calculate_ema(
            df['Close'],
            period=params['ema_fast']
        )

        ema_slow = TechnicalIndicators.calculate_ema(
            df['Close'],
            period=params['ema_slow']
        )

        vwap = TechnicalIndicators.calculate_vwap(
            df['High'],
            df['Low'],
            df['Close'],
            df['Volume']
        )

        volume_ma = TechnicalIndicators.calculate_volume_ma(
            df['Volume'],
            period=params['volume_period']
        )

        support, resistance = TechnicalIndicators.find_support_resistance(
            df['High'],
            df['Low'],
            df['Close'],
            lookback=params['sr_lookback']
        )

        ema_signal = TechnicalIndicators.detect_ema_crossover(ema_fast, ema_slow)

        # Get latest values
        latest = {
            'close': float(df['Close'].iloc[-1]),
            'high': float(df['High'].iloc[-1]),
            'low': float(df['Low'].iloc[-1]),
            'volume': float(df['Volume'].iloc[-1]),

            'rsi': float(rsi.iloc[-1]) if not pd.isna(rsi.iloc[-1]) else 50.0,

            'bb_upper': float(bb_upper.iloc[-1]) if not pd.isna(bb_upper.iloc[-1]) else None,
            'bb_middle': float(bb_middle.iloc[-1]) if not pd.isna(bb_middle.iloc[-1]) else None,
            'bb_lower': float(bb_lower.iloc[-1]) if not pd.isna(bb_lower.iloc[-1]) else None,

            'atr': float(atr.iloc[-1]) if not pd.isna(atr.iloc[-1]) else 10.0,

            'ema_fast': float(ema_fast.iloc[-1]) if not pd.isna(ema_fast.iloc[-1]) else None,
            'ema_slow': float(ema_slow.iloc[-1]) if not pd.isna(ema_slow.iloc[-1]) else None,
            'ema_signal': ema_signal,

            'vwap': float(vwap.iloc[-1]) if not pd.isna(vwap.iloc[-1]) else None,

            'volume_ma': float(volume_ma.iloc[-1]) if not pd.isna(volume_ma.iloc[-1]) else None,
            'volume_ratio': float(df['Volume'].iloc[-1] / volume_ma.iloc[-1]) if not pd.isna(volume_ma.iloc[-1]) and volume_ma.iloc[-1] > 0 else 1.0,

            'support': float(support),
            'resistance': float(resistance)
        }

        return latest


# Singleton instance (stateless, safe to cache)
_indicators: Optional[TechnicalIndicators] = None


def get_technical_indicators() -> TechnicalIndicators:
    """
    Get singleton instance of TechnicalIndicators

    Returns:
        TechnicalIndicators instance
    """
    global _indicators
    if _indicators is None:
        _indicators = TechnicalIndicators()
    return _indicators
