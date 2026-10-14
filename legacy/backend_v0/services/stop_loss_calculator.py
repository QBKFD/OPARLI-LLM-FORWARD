# backend/services/stop_loss_calculator.py
"""
Stop-Loss Calculator

Hybrid stop-loss calculation using ATR and technical levels
Uses wider of ATR-based or technical level-based stops
"""

import logging
from typing import Dict, Optional

logger = logging.getLogger(__name__)


class StopLossCalculator:
    """
    Hybrid stop-loss calculation

    Methods:
    1. ATR-based: entry ± (ATR × multiplier)
    2. Technical level-based: support/resistance with buffer
    3. Hybrid: Use wider of both (more breathing room)

    Validation:
    - Minimum: 1.5× ATR
    - Maximum: 3.0× ATR
    """

    # Stop-loss parameters
    ATR_MULTIPLIER = 2.0
    MIN_STOP_ATR_MULTIPLE = 1.5
    MAX_STOP_ATR_MULTIPLE = 3.0
    TECHNICAL_BUFFER_MULTIPLIER = 0.5  # Buffer below support / above resistance

    def __init__(self):
        logger.info("✓ Stop-Loss Calculator initialized")

    def calculate_stop_loss(
        self,
        direction: str,
        entry_price: float,
        current_atr: float,
        support_level: Optional[float] = None,
        resistance_level: Optional[float] = None
    ) -> Dict:
        """
        Calculate stop-loss using hybrid approach

        Args:
            direction: LONG or SHORT
            entry_price: Entry price
            current_atr: Current ATR value
            support_level: Support level from Technical Analyst (optional)
            resistance_level: Resistance level from Technical Analyst (optional)

        Returns:
            Dict with stop-loss details:
            {
                'stop_loss': 2648.5,
                'stop_distance': 1.5,
                'stop_distance_atr': 0.75,  # Distance in ATR units
                'stop_method': 'hybrid_atr_technical|atr_only',
                'atr_stop': 2649.0,
                'technical_stop': 2648.5,
                'buffer_used': 0.5
            }
        """
        try:
            # Method A: ATR-based stop
            if direction == 'LONG':
                atr_stop = entry_price - (current_atr * self.ATR_MULTIPLIER)
            else:  # SHORT
                atr_stop = entry_price + (current_atr * self.ATR_MULTIPLIER)

            # Method B: Technical level-based stop (if available)
            technical_stop = None
            buffer_used = None

            if direction == 'LONG' and support_level:
                # Place stop below support with buffer
                buffer_used = current_atr * self.TECHNICAL_BUFFER_MULTIPLIER
                technical_stop = support_level - buffer_used

            elif direction == 'SHORT' and resistance_level:
                # Place stop above resistance with buffer
                buffer_used = current_atr * self.TECHNICAL_BUFFER_MULTIPLIER
                technical_stop = resistance_level + buffer_used

            # Use whichever gives WIDER stop (more breathing room)
            if technical_stop:
                if direction == 'LONG':
                    # For LONG, lower stop = wider stop
                    stop_loss = min(atr_stop, technical_stop)
                    stop_method = 'hybrid_atr_technical'
                else:  # SHORT
                    # For SHORT, higher stop = wider stop
                    stop_loss = max(atr_stop, technical_stop)
                    stop_method = 'hybrid_atr_technical'
            else:
                stop_loss = atr_stop
                stop_method = 'atr_only'

            # Calculate stop distance
            stop_distance = abs(entry_price - stop_loss)
            stop_distance_atr = stop_distance / current_atr

            # Validate stop is reasonable (not too tight, not too wide)
            stop_loss = self._validate_stop_distance(
                stop_loss,
                entry_price,
                current_atr,
                direction
            )

            # Recalculate after validation
            stop_distance = abs(entry_price - stop_loss)
            stop_distance_atr = stop_distance / current_atr

            return {
                'stop_loss': round(stop_loss, 2),
                'stop_distance': round(stop_distance, 2),
                'stop_distance_atr': round(stop_distance_atr, 2),
                'stop_method': stop_method,
                'atr_stop': round(atr_stop, 2),
                'technical_stop': round(technical_stop, 2) if technical_stop else None,
                'buffer_used': round(buffer_used, 2) if buffer_used else None
            }

        except Exception as e:
            logger.error(f"Error calculating stop-loss: {e}")
            return {
                'stop_loss': None,
                'stop_distance': None,
                'stop_distance_atr': None,
                'stop_method': 'error',
                'error': str(e)
            }

    def _validate_stop_distance(
        self,
        stop_loss: float,
        entry_price: float,
        current_atr: float,
        direction: str
    ) -> float:
        """
        Validate stop distance is within reasonable range

        Constraints:
        - Minimum: 1.5× ATR
        - Maximum: 3.0× ATR

        Args:
            stop_loss: Calculated stop-loss
            entry_price: Entry price
            current_atr: Current ATR
            direction: LONG or SHORT

        Returns:
            Validated stop-loss (adjusted if needed)
        """
        stop_distance = abs(entry_price - stop_loss)

        # Check if too tight (< 1.5× ATR)
        if stop_distance < current_atr * self.MIN_STOP_ATR_MULTIPLE:
            logger.warning(f"Stop too tight ({stop_distance:.2f}, "
                         f"{stop_distance/current_atr:.2f}× ATR), "
                         f"widening to {self.MIN_STOP_ATR_MULTIPLE}× ATR")

            if direction == 'LONG':
                stop_loss = entry_price - (current_atr * self.MIN_STOP_ATR_MULTIPLE)
            else:  # SHORT
                stop_loss = entry_price + (current_atr * self.MIN_STOP_ATR_MULTIPLE)

        # Check if too wide (> 3.0× ATR)
        elif stop_distance > current_atr * self.MAX_STOP_ATR_MULTIPLE:
            logger.warning(f"Stop too wide ({stop_distance:.2f}, "
                         f"{stop_distance/current_atr:.2f}× ATR), "
                         f"tightening to {self.MAX_STOP_ATR_MULTIPLE}× ATR")

            if direction == 'LONG':
                stop_loss = entry_price - (current_atr * self.MAX_STOP_ATR_MULTIPLE)
            else:  # SHORT
                stop_loss = entry_price + (current_atr * self.MAX_STOP_ATR_MULTIPLE)

        return stop_loss


# Singleton instance
_stop_loss_calculator = None


def get_stop_loss_calculator() -> StopLossCalculator:
    """
    Get singleton instance of StopLossCalculator

    Returns:
        StopLossCalculator instance
    """
    global _stop_loss_calculator
    if _stop_loss_calculator is None:
        _stop_loss_calculator = StopLossCalculator()
    return _stop_loss_calculator
