# backend/services/position_sizing.py
"""
Position Sizing Calculator

Confidence-based position sizing for Risk Manager
Scales risk based on Meta-Agent confidence level
"""

import logging
from typing import Dict

logger = logging.getLogger(__name__)


class PositionSizingCalculator:
    """
    Position sizing based on confidence and risk limits

    Confidence-Based Risk Allocation:
    - High confidence (≥0.8): 1.5% risk
    - Moderate confidence (≥0.7): 1.0% risk
    - Low confidence (<0.7): 0.5% risk

    Hard Limits:
    - Max position size: 3% of account value
    """

    # Risk allocation by confidence level
    CONFIDENCE_RISK_MAP = {
        0.8: 1.5,  # High confidence → 1.5% risk
        0.7: 1.0,  # Moderate confidence → 1.0% risk
        0.0: 0.5   # Low confidence → 0.5% risk
    }

    # Hard limit
    MAX_POSITION_SIZE_PCT = 3.0  # Max 3% of account per trade

    def __init__(self):
        logger.info("✓ Position Sizing Calculator initialized")

    def calculate_position_size(
        self,
        account_balance: float,
        meta_confidence: float,
        entry_price: float,
        stop_loss_price: float
    ) -> Dict:
        """
        Calculate position size based on confidence and stop distance

        Args:
            account_balance: Current account balance
            meta_confidence: Meta-Agent confidence (0.0-1.0)
            entry_price: Entry price
            stop_loss_price: Stop-loss price

        Returns:
            Dict with position sizing details:
            {
                'position_size': 10.5,  # Units
                'risk_dollars': 150.0,
                'risk_pct': 1.5,
                'position_value': 27825.0,
                'position_value_pct': 2.78
            }
        """
        try:
            # Determine risk percentage based on confidence
            risk_pct = self._get_risk_pct_from_confidence(meta_confidence)

            # Calculate dollar risk
            risk_dollars = account_balance * (risk_pct / 100)

            # Calculate position size based on stop distance
            risk_per_unit = abs(entry_price - stop_loss_price)

            if risk_per_unit <= 0:
                logger.error(f"Invalid stop distance: {risk_per_unit}")
                return {
                    'position_size': 0.0,
                    'risk_dollars': 0.0,
                    'risk_pct': 0.0,
                    'position_value': 0.0,
                    'position_value_pct': 0.0,
                    'error': 'Invalid stop distance'
                }

            position_size = risk_dollars / risk_per_unit

            # Calculate position value
            position_value = position_size * entry_price

            # Apply max position size limit
            max_position_value = account_balance * (self.MAX_POSITION_SIZE_PCT / 100)

            if position_value > max_position_value:
                # Reduce position size to meet limit
                position_size = max_position_value / entry_price
                position_value = position_size * entry_price

                # Recalculate actual risk
                actual_risk_dollars = position_size * risk_per_unit
                actual_risk_pct = (actual_risk_dollars / account_balance) * 100

                logger.warning(f"Position size capped at {self.MAX_POSITION_SIZE_PCT}% of account: "
                             f"{position_size:.2f} units (was {risk_dollars / risk_per_unit:.2f})")

                return {
                    'position_size': round(position_size, 2),
                    'risk_dollars': round(actual_risk_dollars, 2),
                    'risk_pct': round(actual_risk_pct, 2),
                    'position_value': round(position_value, 2),
                    'position_value_pct': round((position_value / account_balance) * 100, 2),
                    'capped': True,
                    'original_risk_pct': risk_pct
                }

            # No capping needed
            position_value_pct = (position_value / account_balance) * 100

            return {
                'position_size': round(position_size, 2),
                'risk_dollars': round(risk_dollars, 2),
                'risk_pct': round(risk_pct, 2),
                'position_value': round(position_value, 2),
                'position_value_pct': round(position_value_pct, 2),
                'capped': False
            }

        except Exception as e:
            logger.error(f"Error calculating position size: {e}")
            return {
                'position_size': 0.0,
                'risk_dollars': 0.0,
                'risk_pct': 0.0,
                'position_value': 0.0,
                'position_value_pct': 0.0,
                'error': str(e)
            }

    def _get_risk_pct_from_confidence(self, confidence: float) -> float:
        """
        Map confidence to risk percentage

        Args:
            confidence: Meta-Agent confidence (0.0-1.0)

        Returns:
            Risk percentage (0.5, 1.0, or 1.5)
        """
        if confidence >= 0.8:
            return self.CONFIDENCE_RISK_MAP[0.8]  # 1.5%
        elif confidence >= 0.7:
            return self.CONFIDENCE_RISK_MAP[0.7]  # 1.0%
        else:
            return self.CONFIDENCE_RISK_MAP[0.0]  # 0.5%


# Singleton instance
_position_sizing_calculator = None


def get_position_sizing_calculator() -> PositionSizingCalculator:
    """
    Get singleton instance of PositionSizingCalculator

    Returns:
        PositionSizingCalculator instance
    """
    global _position_sizing_calculator
    if _position_sizing_calculator is None:
        _position_sizing_calculator = PositionSizingCalculator()
    return _position_sizing_calculator
