# backend/services/take_profit_calculator.py
"""
Take-Profit Calculator

Structure-based initial take-profit calculation
Sets realistic targets based on support/resistance levels
Enforces minimum 1.5:1 risk/reward ratio
"""

import logging
from typing import Dict, Optional

logger = logging.getLogger(__name__)


class TakeProfitCalculator:
    """
    Structure-based take-profit calculation

    Strategy:
    1. Find next technical level (resistance for LONG, support for SHORT)
    2. Check if R:R ratio ≥ 1.5:1
    3. If ratio too low, reject trade
    4. Set TP slightly before level (buffer for spread)

    Default Fallback:
    - If no technical level: use 2:1 ratio

    Validation:
    - Minimum R:R: 1.5:1
    - Buffer before level: 2× spread
    """

    # Risk/reward parameters
    MIN_RISK_REWARD_RATIO = 1.5
    DEFAULT_RISK_REWARD_RATIO = 2.0
    LEVEL_BUFFER_MULTIPLIER = 2  # Buffer = 2× spread

    def __init__(self):
        logger.info("✓ Take-Profit Calculator initialized")

    def calculate_take_profit(
        self,
        direction: str,
        entry_price: float,
        stop_distance: float,
        resistance_level: Optional[float] = None,
        support_level: Optional[float] = None,
        current_spread: float = 0.30
    ) -> Dict:
        """
        Calculate take-profit target

        Args:
            direction: LONG or SHORT
            entry_price: Entry price
            stop_distance: Distance from entry to stop-loss
            resistance_level: Resistance from Technical/Visual Analyst (optional)
            support_level: Support from Technical/Visual Analyst (optional)
            current_spread: Current bid-ask spread

        Returns:
            Dict with take-profit details:
            {
                'take_profit': 2655.0,
                'tp_distance': 6.5,
                'risk_reward_ratio': 2.17,
                'tp_method': 'structure_based|ratio_default',
                'recommended_action': 'APPROVE|REJECT_TRADE',
                'rejection_reason': 'Resistance too close, R:R < 1.5:1'
            }
        """
        try:
            # LONG: Find resistance level
            if direction == 'LONG':
                if resistance_level:
                    # Distance to resistance
                    distance_to_resistance = resistance_level - entry_price

                    # Check if resistance is realistic target
                    min_target_distance = stop_distance * self.MIN_RISK_REWARD_RATIO

                    if distance_to_resistance < min_target_distance:
                        # Resistance too close, not worth the risk
                        return {
                            'take_profit': None,
                            'tp_distance': None,
                            'risk_reward_ratio': distance_to_resistance / stop_distance if stop_distance > 0 else 0,
                            'tp_method': 'structure_based_rejected',
                            'recommended_action': 'REJECT_TRADE',
                            'rejection_reason': f'Resistance too close, R:R {distance_to_resistance/stop_distance:.2f}:1 < {self.MIN_RISK_REWARD_RATIO}:1'
                        }

                    # Set TP slightly before resistance (buffer for spread)
                    buffer = current_spread * self.LEVEL_BUFFER_MULTIPLIER
                    take_profit = resistance_level - buffer
                    tp_method = 'structure_based'

                else:
                    # No resistance level, use default 2:1 ratio
                    take_profit = entry_price + (stop_distance * self.DEFAULT_RISK_REWARD_RATIO)
                    tp_method = 'ratio_default'

            # SHORT: Find support level
            else:  # SHORT
                if support_level:
                    distance_to_support = entry_price - support_level
                    min_target_distance = stop_distance * self.MIN_RISK_REWARD_RATIO

                    if distance_to_support < min_target_distance:
                        return {
                            'take_profit': None,
                            'tp_distance': None,
                            'risk_reward_ratio': distance_to_support / stop_distance if stop_distance > 0 else 0,
                            'tp_method': 'structure_based_rejected',
                            'recommended_action': 'REJECT_TRADE',
                            'rejection_reason': f'Support too close, R:R {distance_to_support/stop_distance:.2f}:1 < {self.MIN_RISK_REWARD_RATIO}:1'
                        }

                    buffer = current_spread * self.LEVEL_BUFFER_MULTIPLIER
                    take_profit = support_level + buffer
                    tp_method = 'structure_based'

                else:
                    # No support level, use default 2:1 ratio
                    take_profit = entry_price - (stop_distance * self.DEFAULT_RISK_REWARD_RATIO)
                    tp_method = 'ratio_default'

            # Calculate risk/reward ratio
            tp_distance = abs(take_profit - entry_price)
            risk_reward_ratio = tp_distance / stop_distance if stop_distance > 0 else 0

            return {
                'take_profit': round(take_profit, 2),
                'tp_distance': round(tp_distance, 2),
                'risk_reward_ratio': round(risk_reward_ratio, 2),
                'tp_method': tp_method,
                'recommended_action': 'APPROVE',
                'rejection_reason': None
            }

        except Exception as e:
            logger.error(f"Error calculating take-profit: {e}")
            return {
                'take_profit': None,
                'tp_distance': None,
                'risk_reward_ratio': None,
                'tp_method': 'error',
                'recommended_action': 'REJECT_TRADE',
                'rejection_reason': f'Error: {str(e)}'
            }


# Singleton instance
_take_profit_calculator = None


def get_take_profit_calculator() -> TakeProfitCalculator:
    """
    Get singleton instance of TakeProfitCalculator

    Returns:
        TakeProfitCalculator instance
    """
    global _take_profit_calculator
    if _take_profit_calculator is None:
        _take_profit_calculator = TakeProfitCalculator()
    return _take_profit_calculator
