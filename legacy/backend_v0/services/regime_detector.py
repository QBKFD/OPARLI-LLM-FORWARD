# backend/services/regime_detector.py
"""
Regime Detector Service

Classifies market conditions for adaptive technical analysis
Deterministic, fast, no LLM calls
"""

import logging
import pandas as pd
import numpy as np
from typing import Dict, Optional

logger = logging.getLogger(__name__)


class RegimeDetector:
    """
    Detects market regime for adaptive technical analysis

    Regimes:
    - TRENDING: Strong directional movement, ATR increasing, price trending
    - RANGING: Sideways movement, ATR stable, price oscillating
    - VOLATILE: High volatility, ATR >$15, wide BB, choppy price action
    """

    @staticmethod
    def detect_regime(
        indicators: Dict,
        df: pd.DataFrame,
        atr_threshold: float = 15.0
    ) -> Dict:
        """
        Detect current market regime

        Args:
            indicators: Dict from TechnicalIndicators.calculate_all_indicators()
            df: DataFrame with OHLCV data (for trend detection)
            atr_threshold: ATR threshold for volatile regime (default $15)

        Returns:
            Dict with regime classification:
            {
                'regime': 'TRENDING|RANGING|VOLATILE',
                'trend_direction': 'BULLISH|BEARISH|NEUTRAL',
                'volatility': 'HIGH|NORMAL|LOW',
                'confidence': 0.6-0.9,
                'details': {
                    'atr': 12.5,
                    'atr_trend': 'increasing|stable|decreasing',
                    'price_vs_ema': 'above|below|at',
                    'bb_width_pct': 2.5,
                    'range_bound': True/False
                }
            }
        """

        # Extract key indicators
        atr = indicators['atr']
        close = indicators['close']
        ema_21 = indicators['ema_slow']
        bb_upper = indicators['bb_upper']
        bb_lower = indicators['bb_lower']
        bb_middle = indicators['bb_middle']

        # Calculate ATR trend (last 10 candles)
        if len(df) >= 10:
            recent_atr = df['Close'].tail(10).pct_change().abs().mean() * 100
            atr_trend = 'increasing' if recent_atr > atr * 0.9 else 'stable' if recent_atr > atr * 0.7 else 'decreasing'
        else:
            atr_trend = 'stable'

        # Price position vs EMA 21
        price_vs_ema = 'above' if close > ema_21 else 'below' if close < ema_21 else 'at'

        # Bollinger Band width as % of middle band
        bb_width_pct = ((bb_upper - bb_lower) / bb_middle) * 100 if bb_middle else 0

        # Check if price is range-bound (oscillating around VWAP/middle BB)
        vwap = indicators['vwap']
        price_range = abs(close - vwap) / vwap * 100 if vwap else 5
        range_bound = price_range < 0.5  # Within 0.5% of VWAP

        # Detect trend consistency (last 20 candles)
        trend_consistency = RegimeDetector._calculate_trend_consistency(df)

        # Classify regime
        regime, confidence = RegimeDetector._classify_regime(
            atr=atr,
            atr_trend=atr_trend,
            atr_threshold=atr_threshold,
            price_vs_ema=price_vs_ema,
            bb_width_pct=bb_width_pct,
            range_bound=range_bound,
            trend_consistency=trend_consistency
        )

        # Determine trend direction
        if regime == 'TRENDING':
            if price_vs_ema == 'above' and trend_consistency > 0.6:
                trend_direction = 'BULLISH'
            elif price_vs_ema == 'below' and trend_consistency < -0.6:
                trend_direction = 'BEARISH'
            else:
                trend_direction = 'NEUTRAL'
        else:
            trend_direction = 'NEUTRAL'

        # Volatility classification
        if atr > atr_threshold:
            volatility = 'HIGH'
        elif atr > atr_threshold * 0.6:
            volatility = 'NORMAL'
        else:
            volatility = 'LOW'

        return {
            'regime': regime,
            'trend_direction': trend_direction,
            'volatility': volatility,
            'confidence': confidence,
            'details': {
                'atr': atr,
                'atr_trend': atr_trend,
                'price_vs_ema': price_vs_ema,
                'bb_width_pct': round(bb_width_pct, 2),
                'range_bound': range_bound,
                'trend_consistency': round(trend_consistency, 2)
            }
        }

    @staticmethod
    def _calculate_trend_consistency(df: pd.DataFrame, lookback: int = 20) -> float:
        """
        Calculate trend consistency score (-1.0 to 1.0)

        Positive = bullish trend consistency
        Negative = bearish trend consistency
        Near zero = choppy/ranging

        Args:
            df: DataFrame with Close prices
            lookback: Number of candles to analyze

        Returns:
            Trend consistency score
        """
        if len(df) < lookback:
            return 0.0

        prices = df['Close'].tail(lookback)

        # Count higher highs and lower lows
        higher_closes = (prices.diff() > 0).sum()
        lower_closes = (prices.diff() < 0).sum()

        # Normalize to -1.0 to 1.0
        total = higher_closes + lower_closes
        if total == 0:
            return 0.0

        consistency = (higher_closes - lower_closes) / total

        return consistency

    @staticmethod
    def _classify_regime(
        atr: float,
        atr_trend: str,
        atr_threshold: float,
        price_vs_ema: str,
        bb_width_pct: float,
        range_bound: bool,
        trend_consistency: float
    ) -> tuple:
        """
        Classify regime based on indicators

        Returns:
            Tuple of (regime, confidence)
        """

        # VOLATILE: High ATR, wide BB
        if atr > atr_threshold or bb_width_pct > 4.0:
            return 'VOLATILE', 0.85

        # RANGING: Price near VWAP, stable ATR, low trend consistency
        if range_bound and atr_trend == 'stable' and abs(trend_consistency) < 0.4:
            return 'RANGING', 0.80

        # TRENDING: ATR increasing, price trending away from EMA, high consistency
        if atr_trend == 'increasing' and abs(trend_consistency) > 0.6:
            if price_vs_ema == 'above' and trend_consistency > 0:
                return 'TRENDING', 0.85
            elif price_vs_ema == 'below' and trend_consistency < 0:
                return 'TRENDING', 0.85

        # TRENDING (weaker): Price consistently above/below EMA
        if abs(trend_consistency) > 0.5:
            return 'TRENDING', 0.70

        # RANGING (weaker): No clear trend
        if abs(trend_consistency) < 0.3:
            return 'RANGING', 0.65

        # Default: RANGING with lower confidence
        return 'RANGING', 0.60

    @staticmethod
    def get_adaptive_thresholds(regime: str, volatility: str, atr: float) -> Dict:
        """
        Get adaptive indicator thresholds based on regime

        Args:
            regime: Current regime ('TRENDING', 'RANGING', 'VOLATILE')
            volatility: Volatility level ('HIGH', 'NORMAL', 'LOW')
            atr: Current ATR value

        Returns:
            Dict of adjusted thresholds:
            {
                'rsi_oversold': 30,
                'rsi_overbought': 70,
                'bb_lower_pct': 0.05,  # % below lower band for LONG
                'bb_upper_pct': 0.05,  # % above upper band for SHORT
                'volume_threshold': 1.5,  # Volume ratio threshold
                'ema_spread_min': 0.001  # Minimum EMA spread for trend
            }
        """

        # Base thresholds
        thresholds = {
            'rsi_oversold': 30,
            'rsi_overbought': 70,
            'bb_lower_pct': 0.05,
            'bb_upper_pct': 0.05,
            'volume_threshold': 1.5,
            'ema_spread_min': 0.001
        }

        # Adjust for regime
        if regime == 'TRENDING':
            # Trending: Relax mean reversion thresholds, tighten trend filters
            thresholds['rsi_oversold'] = 40
            thresholds['rsi_overbought'] = 60
            thresholds['bb_lower_pct'] = 0.10  # Allow deeper pullbacks
            thresholds['bb_upper_pct'] = 0.10
            thresholds['ema_spread_min'] = 0.002  # Require stronger trend

        elif regime == 'RANGING':
            # Ranging: Tight mean reversion, high volume filter
            thresholds['rsi_oversold'] = 25
            thresholds['rsi_overbought'] = 75
            thresholds['bb_lower_pct'] = 0.02  # Tight entry at extremes
            thresholds['bb_upper_pct'] = 0.02
            thresholds['volume_threshold'] = 2.0  # Higher volume requirement

        elif regime == 'VOLATILE':
            # Volatile: Very tight filters, avoid choppy trades
            thresholds['rsi_oversold'] = 20
            thresholds['rsi_overbought'] = 80
            thresholds['bb_lower_pct'] = 0.01  # Only extreme extremes
            thresholds['bb_upper_pct'] = 0.01
            thresholds['volume_threshold'] = 2.5  # Much higher volume
            thresholds['ema_spread_min'] = 0.003  # Strong trend required

        # Adjust for volatility (ATR-based)
        if volatility == 'HIGH':
            # High volatility: Widen all thresholds proportionally
            thresholds['bb_lower_pct'] *= 1.5
            thresholds['bb_upper_pct'] *= 1.5

        elif volatility == 'LOW':
            # Low volatility: Tighten thresholds
            thresholds['bb_lower_pct'] *= 0.7
            thresholds['bb_upper_pct'] *= 0.7

        return thresholds


# Singleton instance
_regime_detector: Optional[RegimeDetector] = None


def get_regime_detector() -> RegimeDetector:
    """
    Get singleton instance of RegimeDetector

    Returns:
        RegimeDetector instance
    """
    global _regime_detector
    if _regime_detector is None:
        _regime_detector = RegimeDetector()
    return _regime_detector
