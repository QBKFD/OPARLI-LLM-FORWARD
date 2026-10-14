# backend/services/key_level_detector.py
"""
Key Level Detector Service

Detects if current price is near key support/resistance levels
Used by Stage 1 quality filter in Scanner Agent

Criteria:
- Within 0.3% of support/resistance (from Technical Indicators)
- Within 0.2% of recent swing high/low
- At psychological levels (e.g., 2600, 2650, 2700)
"""

import logging
from typing import Dict, Optional, List
from config.database import get_database
from services.market_data_provider import MarketDataProvider, LiveDataProvider

logger = logging.getLogger(__name__)


class KeyLevelDetector:
    """
    Detects if price is at key support/resistance levels

    Responsibilities:
    - Check if price near support/resistance
    - Detect swing highs/lows
    - Identify psychological levels
    """

    # Distance thresholds
    KEY_LEVEL_THRESHOLD_PCT = 0.3  # 0.3% from support/resistance
    SWING_LEVEL_THRESHOLD_PCT = 0.2  # 0.2% from swing high/low

    # Psychological level spacing
    PSYCHOLOGICAL_LEVEL_SPACING = 50  # Every $50 (2600, 2650, 2700, etc.)

    def __init__(self, provider: Optional[MarketDataProvider] = None):
        self.db = get_database()
        # Resamples any timeframe from the ohlcv_1min view (replaces the old
        # direct query to the non-existent `candlesticks` table).
        self.provider = provider or LiveDataProvider(db=self.db)
        logger.info("✓ Key Level Detector initialized")

    def is_at_key_level(self, symbol: str, current_price: float, indicators_1m: Dict) -> Dict:
        """
        Check if current price is at a key level

        Args:
            symbol: Trading symbol
            current_price: Current price
            indicators_1m: 1m timeframe indicators (should include support/resistance)

        Returns:
            Dict with key level analysis:
            {
                'at_key_level': True/False,
                'level_type': 'support|resistance|swing_high|swing_low|psychological',
                'level_value': 2650.0,
                'distance_pct': 0.15,
                'reason': 'Price at support (0.15% away)'
            }
        """
        try:
            # 1. Check support/resistance from indicators
            support = indicators_1m.get('support')
            resistance = indicators_1m.get('resistance')

            if support:
                distance_to_support = abs(current_price - support) / support * 100
                if distance_to_support <= self.KEY_LEVEL_THRESHOLD_PCT:
                    return {
                        'at_key_level': True,
                        'level_type': 'support',
                        'level_value': support,
                        'distance_pct': round(distance_to_support, 2),
                        'reason': f'Price at support ({distance_to_support:.2f}% away)'
                    }

            if resistance:
                distance_to_resistance = abs(current_price - resistance) / resistance * 100
                if distance_to_resistance <= self.KEY_LEVEL_THRESHOLD_PCT:
                    return {
                        'at_key_level': True,
                        'level_type': 'resistance',
                        'level_value': resistance,
                        'distance_pct': round(distance_to_resistance, 2),
                        'reason': f'Price at resistance ({distance_to_resistance:.2f}% away)'
                    }

            # 2. Check swing highs/lows
            swing_result = self._check_swing_levels(symbol, current_price)
            if swing_result['at_key_level']:
                return swing_result

            # 3. Check psychological levels
            psych_result = self._check_psychological_levels(current_price)
            if psych_result['at_key_level']:
                return psych_result

            # Not at key level
            return {
                'at_key_level': False,
                'level_type': None,
                'level_value': None,
                'distance_pct': None,
                'reason': 'Price not at key level'
            }

        except Exception as e:
            logger.error(f"Error checking key levels for {symbol}: {e}")
            return {
                'at_key_level': False,
                'level_type': None,
                'level_value': None,
                'distance_pct': None,
                'reason': f'Error: {str(e)}'
            }

    def _check_swing_levels(self, symbol: str, current_price: float) -> Dict:
        """
        Check if price near recent swing highs/lows

        Swing high: Highest high in last 20 candles with lower highs on both sides
        Swing low: Lowest low in last 20 candles with higher lows on both sides

        Returns:
            Dict with swing level analysis
        """
        try:
            # 5m candles resampled from ohlcv_1min via the provider
            tf = self.provider.get_timeframe_data(symbol, timeframes=['5m'], lookback=50)
            df5 = tf.get('5m')

            if df5 is not None and len(df5) >= 20:
                # Provider returns oldest-first; the "last 20 bars" are the tail
                recent = df5.tail(20)
                highs = [float(x) for x in recent['High']]
                lows = [float(x) for x in recent['Low']]

                # Find swing high/low (extremes in the last 20 bars)
                swing_high = max(highs)
                swing_low = min(lows)

                # Check distance to swing high
                distance_to_high = abs(current_price - swing_high) / swing_high * 100
                if distance_to_high <= self.SWING_LEVEL_THRESHOLD_PCT:
                    return {
                        'at_key_level': True,
                        'level_type': 'swing_high',
                        'level_value': swing_high,
                        'distance_pct': round(distance_to_high, 2),
                        'reason': f'Price at swing high ({distance_to_high:.2f}% away)'
                    }

                # Check distance to swing low
                distance_to_low = abs(current_price - swing_low) / swing_low * 100
                if distance_to_low <= self.SWING_LEVEL_THRESHOLD_PCT:
                    return {
                        'at_key_level': True,
                        'level_type': 'swing_low',
                        'level_value': swing_low,
                        'distance_pct': round(distance_to_low, 2),
                        'reason': f'Price at swing low ({distance_to_low:.2f}% away)'
                    }

        except Exception as e:
            logger.error(f"Error checking swing levels for {symbol}: {e}")

        return {
            'at_key_level': False,
            'level_type': None,
            'level_value': None,
            'distance_pct': None,
            'reason': 'Not at swing level'
        }

    def _check_psychological_levels(self, current_price: float) -> Dict:
        """
        Check if price near psychological levels (e.g., 2600, 2650, 2700)

        Returns:
            Dict with psychological level analysis
        """
        # Find nearest psychological level
        nearest_level = round(current_price / self.PSYCHOLOGICAL_LEVEL_SPACING) * self.PSYCHOLOGICAL_LEVEL_SPACING

        # Calculate distance
        distance_pct = abs(current_price - nearest_level) / nearest_level * 100

        # Within 0.1% of psychological level
        if distance_pct <= 0.1:
            return {
                'at_key_level': True,
                'level_type': 'psychological',
                'level_value': nearest_level,
                'distance_pct': round(distance_pct, 2),
                'reason': f'Price at psychological level ${nearest_level} ({distance_pct:.2f}% away)'
            }

        return {
            'at_key_level': False,
            'level_type': None,
            'level_value': None,
            'distance_pct': None,
            'reason': 'Not at psychological level'
        }


# Singleton instance
_key_level_detector: Optional[KeyLevelDetector] = None


def get_key_level_detector() -> KeyLevelDetector:
    """
    Get singleton instance of KeyLevelDetector

    Returns:
        KeyLevelDetector instance
    """
    global _key_level_detector
    if _key_level_detector is None:
        _key_level_detector = KeyLevelDetector()
    return _key_level_detector
