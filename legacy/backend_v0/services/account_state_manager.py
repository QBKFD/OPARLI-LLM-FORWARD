# backend/services/account_state_manager.py
"""
Account State Manager Service

Tracks real-time account state for Risk Manager
Updates on every trade fill, calculates daily P&L, drawdown, etc.
"""

import logging
from typing import Dict, Optional
from datetime import datetime, time
from config.database import get_database

logger = logging.getLogger(__name__)


class AccountStateManager:
    """
    Real-time account state tracking

    Responsibilities:
    - Track current balance
    - Calculate daily P&L
    - Track drawdown from peak
    - Count open positions
    - Count trades today
    - Reset daily counters at market open
    """

    # Market open time (UTC)
    MARKET_OPEN_TIME = time(13, 30)  # 9:30 AM ET

    def __init__(self):
        self.db = get_database()

        # In-memory state cache
        self._state = None
        self._last_update = None

        logger.info("✓ Account State Manager initialized")

    def get_current_state(self) -> Dict:
        """
        Get current account state

        Returns:
            Dict with account state:
            {
                'current_balance': 10500.0,
                'peak_balance': 11000.0,
                'start_of_day_balance': 10000.0,
                'open_positions': 1,
                'trades_today': 5,
                'daily_pnl': 500.0,
                'daily_pnl_pct': 5.0,
                'drawdown_from_peak': -500.0,
                'drawdown_pct': -4.55,
                'timestamp': datetime
            }
        """
        try:
            # Check if daily reset needed
            self._reset_daily_if_needed()

            # Fetch latest state from database
            with self.db.get_cursor() as cur:
                # Get latest account state
                cur.execute("""
                    SELECT
                        current_balance,
                        peak_balance,
                        start_of_day_balance,
                        open_positions,
                        trades_today,
                        daily_pnl,
                        daily_pnl_pct,
                        drawdown_from_peak,
                        drawdown_pct,
                        timestamp
                    FROM account_state
                    ORDER BY timestamp DESC
                    LIMIT 1
                """)

                row = cur.fetchone()

                if row:
                    state = {
                        'current_balance': float(row['current_balance']),
                        'peak_balance': float(row['peak_balance']),
                        'start_of_day_balance': float(row['start_of_day_balance']),
                        'open_positions': int(row['open_positions']),
                        'trades_today': int(row['trades_today']),
                        'daily_pnl': float(row['daily_pnl']) if row['daily_pnl'] else 0.0,
                        'daily_pnl_pct': float(row['daily_pnl_pct']) if row['daily_pnl_pct'] else 0.0,
                        'drawdown_from_peak': float(row['drawdown_from_peak']) if row['drawdown_from_peak'] else 0.0,
                        'drawdown_pct': float(row['drawdown_pct']) if row['drawdown_pct'] else 0.0,
                        'timestamp': row['timestamp']
                    }

                    # Cache state
                    self._state = state
                    self._last_update = datetime.utcnow()

                    return state
                else:
                    # No state in database, initialize
                    logger.warning("No account state found, initializing with default values")
                    return self._initialize_account_state()

        except Exception as e:
            logger.error(f"Error getting account state: {e}")
            # Return cached state if available
            if self._state:
                logger.warning("Returning cached account state due to error")
                return self._state
            else:
                # Return safe defaults
                return self._get_default_state()

    def update_on_trade_fill(
        self,
        trade_id: int,
        fill_price: float,
        position_size: float,
        direction: str,
        is_opening: bool
    ):
        """
        Update account state after trade fill

        Args:
            trade_id: Trade ID
            fill_price: Fill price
            position_size: Position size
            direction: LONG or SHORT
            is_opening: True if opening position, False if closing
        """
        try:
            current_state = self.get_current_state()

            # Update open positions count
            if is_opening:
                new_open_positions = current_state['open_positions'] + 1
                new_trades_today = current_state['trades_today'] + 1
                new_balance = current_state['current_balance']  # Balance doesn't change on open
            else:
                # Closing position, calculate P&L
                new_open_positions = max(0, current_state['open_positions'] - 1)
                new_trades_today = current_state['trades_today']

                # Get trade entry details to calculate P&L
                pnl = self._calculate_trade_pnl(trade_id, fill_price, position_size, direction)
                new_balance = current_state['current_balance'] + pnl

            # Calculate daily P&L
            daily_pnl = new_balance - current_state['start_of_day_balance']
            daily_pnl_pct = (daily_pnl / current_state['start_of_day_balance']) * 100

            # Update peak balance
            new_peak_balance = max(current_state['peak_balance'], new_balance)

            # Calculate drawdown from peak
            drawdown_from_peak = new_balance - new_peak_balance
            drawdown_pct = (drawdown_from_peak / new_peak_balance) * 100 if new_peak_balance > 0 else 0

            # Insert new state
            with self.db.get_cursor() as cur:
                cur.execute("""
                    INSERT INTO account_state (
                        current_balance,
                        peak_balance,
                        start_of_day_balance,
                        open_positions,
                        trades_today,
                        daily_pnl,
                        daily_pnl_pct,
                        drawdown_from_peak,
                        drawdown_pct
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    new_balance,
                    new_peak_balance,
                    current_state['start_of_day_balance'],
                    new_open_positions,
                    new_trades_today,
                    daily_pnl,
                    daily_pnl_pct,
                    drawdown_from_peak,
                    drawdown_pct
                ))

            logger.info(f"✓ Account state updated after trade fill: "
                       f"Balance ${new_balance:.2f}, Open positions: {new_open_positions}")

            # Invalidate cache
            self._state = None

        except Exception as e:
            logger.error(f"Error updating account state on trade fill: {e}")

    def _calculate_trade_pnl(
        self,
        trade_id: int,
        exit_price: float,
        position_size: float,
        direction: str
    ) -> float:
        """
        Calculate P&L for a closed trade

        Args:
            trade_id: Trade ID
            exit_price: Exit price
            position_size: Position size
            direction: LONG or SHORT

        Returns:
            P&L in dollars
        """
        try:
            with self.db.get_cursor() as cur:
                # Get entry price from executed_trades
                cur.execute("""
                    SELECT entry_price
                    FROM executed_trades
                    WHERE id = %s
                """, (trade_id,))

                row = cur.fetchone()

                if row:
                    entry_price = float(row['entry_price'])

                    if direction == 'LONG':
                        pnl = (exit_price - entry_price) * position_size
                    else:  # SHORT
                        pnl = (entry_price - exit_price) * position_size

                    return pnl
                else:
                    logger.error(f"Trade {trade_id} not found for P&L calculation")
                    return 0.0

        except Exception as e:
            logger.error(f"Error calculating trade P&L: {e}")
            return 0.0

    def _reset_daily_if_needed(self):
        """
        Reset daily counters if new trading day started

        Resets:
        - start_of_day_balance = current_balance
        - trades_today = 0
        - daily_pnl = 0
        - daily_pnl_pct = 0
        """
        try:
            current_state = self.get_current_state()
            now = datetime.utcnow()

            # Check if we've passed market open time
            if current_state and current_state['timestamp']:
                last_update_time = current_state['timestamp'].time()
                current_time = now.time()

                # If last update was before open and now is after open, reset
                if last_update_time < self.MARKET_OPEN_TIME <= current_time:
                    logger.info("New trading day detected, resetting daily counters")

                    with self.db.get_cursor() as cur:
                        cur.execute("""
                            INSERT INTO account_state (
                                current_balance,
                                peak_balance,
                                start_of_day_balance,
                                open_positions,
                                trades_today,
                                daily_pnl,
                                daily_pnl_pct,
                                drawdown_from_peak,
                                drawdown_pct
                            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """, (
                            current_state['current_balance'],
                            current_state['peak_balance'],
                            current_state['current_balance'],  # Reset start of day
                            current_state['open_positions'],
                            0,  # Reset trades today
                            0.0,  # Reset daily P&L
                            0.0,  # Reset daily P&L %
                            current_state['drawdown_from_peak'],
                            current_state['drawdown_pct']
                        ))

                    # Invalidate cache
                    self._state = None

        except Exception as e:
            logger.error(f"Error resetting daily counters: {e}")

    def _initialize_account_state(self, initial_balance: float = 10000.0) -> Dict:
        """
        Initialize account state in database

        Args:
            initial_balance: Starting balance

        Returns:
            Initial state dict
        """
        try:
            with self.db.get_cursor() as cur:
                cur.execute("""
                    INSERT INTO account_state (
                        current_balance,
                        peak_balance,
                        start_of_day_balance,
                        open_positions,
                        trades_today,
                        daily_pnl,
                        daily_pnl_pct,
                        drawdown_from_peak,
                        drawdown_pct
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING *
                """, (
                    initial_balance,
                    initial_balance,
                    initial_balance,
                    0,
                    0,
                    0.0,
                    0.0,
                    0.0,
                    0.0
                ))

                row = cur.fetchone()

            logger.info(f"✓ Account state initialized with balance ${initial_balance}")

            return {
                'current_balance': initial_balance,
                'peak_balance': initial_balance,
                'start_of_day_balance': initial_balance,
                'open_positions': 0,
                'trades_today': 0,
                'daily_pnl': 0.0,
                'daily_pnl_pct': 0.0,
                'drawdown_from_peak': 0.0,
                'drawdown_pct': 0.0,
                'timestamp': datetime.utcnow()
            }

        except Exception as e:
            logger.error(f"Error initializing account state: {e}")
            return self._get_default_state()

    def _get_default_state(self) -> Dict:
        """
        Get safe default state (used when database unavailable)

        Returns:
            Default state dict
        """
        return {
            'current_balance': 10000.0,
            'peak_balance': 10000.0,
            'start_of_day_balance': 10000.0,
            'open_positions': 0,
            'trades_today': 0,
            'daily_pnl': 0.0,
            'daily_pnl_pct': 0.0,
            'drawdown_from_peak': 0.0,
            'drawdown_pct': 0.0,
            'timestamp': datetime.utcnow()
        }


# Singleton instance
_account_state_manager: Optional[AccountStateManager] = None


def get_account_state_manager() -> AccountStateManager:
    """
    Get singleton instance of AccountStateManager

    Returns:
        AccountStateManager instance
    """
    global _account_state_manager
    if _account_state_manager is None:
        _account_state_manager = AccountStateManager()
    return _account_state_manager
