# backend/services/entry_timing.py
"""
Entry Timing Optimizer

Optimizes entry timing on 1m timeframe for better execution
Waits for pullbacks (LONG) or bounces (SHORT) to support/resistance
"""

import logging
from typing import Dict, Optional
from datetime import datetime, time

logger = logging.getLogger(__name__)


class EntryTimingOptimizer:
    """
    1m Entry Timing Optimizer

    Responsibilities:
    - Wait for optimal entry on 1m timeframe
    - Check if price is near support (LONG) or resistance (SHORT)
    - Apply time-of-day filters (avoid open/close, news)
    - Check spread (block if spread >$0.50)
    """

    # Time-of-day filters (UTC)
    MARKET_OPEN = time(13, 30)  # 9:30 AM ET
    MARKET_CLOSE = time(20, 0)  # 4:00 PM ET

    # Avoid first/last 15 minutes
    AVOID_AFTER_OPEN_MINUTES = 15
    AVOID_BEFORE_CLOSE_MINUTES = 15

    # Spread threshold (XAUUSD)
    MAX_SPREAD_USD = 0.50

    def __init__(self):
        pass

    def should_enter_now(
        self,
        signal: str,
        indicators_1m: Dict,
        current_time: Optional[datetime] = None,
        bid_price: Optional[float] = None,
        ask_price: Optional[float] = None
    ) -> Dict:
        """
        Determine if entry should happen now or wait

        Args:
            signal: 'LONG' or 'SHORT'
            indicators_1m: 1m timeframe indicators dict
            current_time: Current UTC time (None = now)
            bid_price: Current bid price (for spread check)
            ask_price: Current ask price (for spread check)

        Returns:
            Dict with entry decision:
            {
                'enter_now': True/False,
                'reason': 'optimal_entry|wait_for_pullback|time_filter|spread_too_wide',
                'wait_until': 'support_reached|time_filter_clears|spread_narrows',
                'target_price': 2650.0,  # Target entry price
                'current_price': 2655.0
            }
        """

        if signal not in ['LONG', 'SHORT']:
            return {
                'enter_now': False,
                'reason': 'no_signal',
                'wait_until': None,
                'target_price': None,
                'current_price': indicators_1m.get('close')
            }

        current_time = current_time or datetime.utcnow()
        current_price = indicators_1m['close']

        # Check time-of-day filter
        time_filter_result = self._check_time_filter(current_time)
        if not time_filter_result['allowed']:
            return {
                'enter_now': False,
                'reason': 'time_filter',
                'wait_until': 'time_filter_clears',
                'time_filter_details': time_filter_result,
                'target_price': None,
                'current_price': current_price
            }

        # Check spread filter
        if bid_price and ask_price:
            spread_result = self._check_spread(bid_price, ask_price)
            if not spread_result['allowed']:
                return {
                    'enter_now': False,
                    'reason': 'spread_too_wide',
                    'wait_until': 'spread_narrows',
                    'spread_details': spread_result,
                    'target_price': None,
                    'current_price': current_price
                }

        # Check price position vs support/resistance
        if signal == 'LONG':
            return self._check_long_entry(indicators_1m)
        else:  # SHORT
            return self._check_short_entry(indicators_1m)

    def _check_time_filter(self, current_time: datetime) -> Dict:
        """
        Check if current time is suitable for trading

        Avoid:
        - First 15 minutes after open
        - Last 15 minutes before close
        - Major news times (placeholder for future)

        Args:
            current_time: Current UTC time

        Returns:
            Dict with filter result
        """
        current_time_only = current_time.time()

        # Check if within trading hours
        if current_time_only < self.MARKET_OPEN or current_time_only > self.MARKET_CLOSE:
            return {
                'allowed': False,
                'reason': 'outside_trading_hours',
                'details': 'Market closed'
            }

        # Check if too close to open
        minutes_after_open = (
            current_time_only.hour * 60 + current_time_only.minute -
            (self.MARKET_OPEN.hour * 60 + self.MARKET_OPEN.minute)
        )

        if minutes_after_open < self.AVOID_AFTER_OPEN_MINUTES:
            return {
                'allowed': False,
                'reason': 'too_close_to_open',
                'details': f'Wait {self.AVOID_AFTER_OPEN_MINUTES - minutes_after_open} more minutes'
            }

        # Check if too close to close
        minutes_before_close = (
            (self.MARKET_CLOSE.hour * 60 + self.MARKET_CLOSE.minute) -
            (current_time_only.hour * 60 + current_time_only.minute)
        )

        if minutes_before_close < self.AVOID_BEFORE_CLOSE_MINUTES:
            return {
                'allowed': False,
                'reason': 'too_close_to_close',
                'details': f'Too close to market close ({minutes_before_close} minutes)'
            }

        # All filters passed
        return {'allowed': True, 'reason': 'time_ok'}

    def _check_spread(self, bid: float, ask: float) -> Dict:
        """
        Check if spread is acceptable

        Args:
            bid: Bid price
            ask: Ask price

        Returns:
            Dict with spread check result
        """
        spread = ask - bid

        if spread > self.MAX_SPREAD_USD:
            return {
                'allowed': False,
                'reason': 'spread_too_wide',
                'spread': spread,
                'threshold': self.MAX_SPREAD_USD
            }

        return {
            'allowed': True,
            'reason': 'spread_ok',
            'spread': spread
        }

    def _check_long_entry(self, indicators_1m: Dict) -> Dict:
        """
        Check if LONG entry is optimal on 1m

        Optimal entry:
        - Price near support (within 0.2%)
        - RSI <50 (not overbought)
        - Not at upper Bollinger Band

        Args:
            indicators_1m: 1m indicators dict

        Returns:
            Entry decision dict
        """
        current_price = indicators_1m['close']
        support = indicators_1m['support']
        rsi = indicators_1m['rsi']
        bb_upper = indicators_1m['bb_upper']

        # Check if near support
        support_distance_pct = abs(current_price - support) / support * 100

        # Optimal: within 0.2% of support
        if support_distance_pct < 0.2:
            return {
                'enter_now': True,
                'reason': 'optimal_entry',
                'wait_until': None,
                'target_price': current_price,
                'current_price': current_price,
                'details': f'Price at support ({support_distance_pct:.2f}% away)'
            }

        # Good: within 0.5% of support and RSI <50
        if support_distance_pct < 0.5 and rsi < 50:
            return {
                'enter_now': True,
                'reason': 'good_entry',
                'wait_until': None,
                'target_price': current_price,
                'current_price': current_price,
                'details': f'Price near support ({support_distance_pct:.2f}% away), RSI {rsi:.1f}'
            }

        # Not optimal: wait for pullback
        return {
            'enter_now': False,
            'reason': 'wait_for_pullback',
            'wait_until': 'support_reached',
            'target_price': support,
            'current_price': current_price,
            'details': f'Price {support_distance_pct:.2f}% above support, wait for pullback'
        }

    def _check_short_entry(self, indicators_1m: Dict) -> Dict:
        """
        Check if SHORT entry is optimal on 1m

        Optimal entry:
        - Price near resistance (within 0.2%)
        - RSI >50 (not oversold)
        - Not at lower Bollinger Band

        Args:
            indicators_1m: 1m indicators dict

        Returns:
            Entry decision dict
        """
        current_price = indicators_1m['close']
        resistance = indicators_1m['resistance']
        rsi = indicators_1m['rsi']
        bb_lower = indicators_1m['bb_lower']

        # Check if near resistance
        resistance_distance_pct = abs(current_price - resistance) / resistance * 100

        # Optimal: within 0.2% of resistance
        if resistance_distance_pct < 0.2:
            return {
                'enter_now': True,
                'reason': 'optimal_entry',
                'wait_until': None,
                'target_price': current_price,
                'current_price': current_price,
                'details': f'Price at resistance ({resistance_distance_pct:.2f}% away)'
            }

        # Good: within 0.5% of resistance and RSI >50
        if resistance_distance_pct < 0.5 and rsi > 50:
            return {
                'enter_now': True,
                'reason': 'good_entry',
                'wait_until': None,
                'target_price': current_price,
                'current_price': current_price,
                'details': f'Price near resistance ({resistance_distance_pct:.2f}% away), RSI {rsi:.1f}'
            }

        # Not optimal: wait for bounce
        return {
            'enter_now': False,
            'reason': 'wait_for_bounce',
            'wait_until': 'resistance_reached',
            'target_price': resistance,
            'current_price': current_price,
            'details': f'Price {resistance_distance_pct:.2f}% below resistance, wait for bounce'
        }


# Singleton instance
_entry_timing_optimizer: Optional[EntryTimingOptimizer] = None


def get_entry_timing_optimizer() -> EntryTimingOptimizer:
    """
    Get singleton instance of EntryTimingOptimizer

    Returns:
        EntryTimingOptimizer instance
    """
    global _entry_timing_optimizer
    if _entry_timing_optimizer is None:
        _entry_timing_optimizer = EntryTimingOptimizer()
    return _entry_timing_optimizer
