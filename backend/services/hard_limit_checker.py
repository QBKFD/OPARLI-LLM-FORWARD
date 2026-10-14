# backend/services/hard_limit_checker.py
"""
Hard Limit Checker

Enforces non-negotiable account protection rules
No overrides allowed - these are circuit breakers
"""

import logging
from typing import Dict
from config.database import get_database

logger = logging.getLogger(__name__)


class HardLimitChecker:
    """
    Hard limit enforcement for account protection

    Hard Limits (Non-Negotiable):
    1. Max daily loss: -6% from start of day → STOP ALL
    2. Max drawdown: -15% from peak equity → STOP ALL
    3. Max position size: 3% of account per trade
    4. Max open positions: 2 concurrent trades
    5. Max trades per day: 10 trades
    6. Min account balance: $1000 emergency stop

    All violations are logged to database
    """

    # Hard limits (cannot be overridden)
    MAX_DAILY_LOSS_PCT = -6.0
    MAX_DRAWDOWN_PCT = -15.0
    MAX_POSITION_SIZE_PCT = 3.0
    MAX_OPEN_POSITIONS = 2
    MAX_TRADES_PER_DAY = 10
    MIN_ACCOUNT_BALANCE = 1000.0

    def __init__(self, db=None):
        # The limit checks themselves are pure — everything they need is in the
        # account_state argument. Only violation LOGGING touches the database,
        # so the connection is resolved lazily and its absence is not fatal.
        # This is what lets risk_service.run_risk_validation honour its
        # documented contract ("no DB reads, no DB writes") in backtests, which
        # previously died at import-time on a missing DATABASE_URL.
        self._db = db
        logger.info("✓ Hard Limit Checker initialized")

    @property
    def db(self):
        if self._db is None:
            self._db = get_database()
        return self._db

    def check_hard_limits(self, account_state: Dict) -> Dict:
        """
        Check all hard limits

        Args:
            account_state: Current account state from AccountStateManager

        Returns:
            Dict with check result:
            {
                'passed': True/False,
                'reason': 'All hard limits OK' or 'Daily loss limit hit...',
                'action': None or 'STOP_TRADING_FOR_DAY|STOP_ALL_TRADING',
                'violations': []  # List of violated limits
            }
        """
        violations = []

        # 1. Check daily loss limit
        daily_pnl_pct = account_state.get('daily_pnl_pct', 0.0)

        if daily_pnl_pct <= self.MAX_DAILY_LOSS_PCT:
            violations.append({
                'limit_type': 'daily_loss',
                'limit_value': self.MAX_DAILY_LOSS_PCT,
                'actual_value': daily_pnl_pct,
                'action': 'STOP_TRADING_FOR_DAY',
                'reason': f'Daily loss limit hit: {daily_pnl_pct:.2f}% (max {self.MAX_DAILY_LOSS_PCT}%)'
            })

        # 2. Check max drawdown from peak
        drawdown_pct = account_state.get('drawdown_pct', 0.0)

        if drawdown_pct <= self.MAX_DRAWDOWN_PCT:
            violations.append({
                'limit_type': 'max_drawdown',
                'limit_value': self.MAX_DRAWDOWN_PCT,
                'actual_value': drawdown_pct,
                'action': 'STOP_ALL_TRADING',
                'reason': f'Max drawdown hit: {drawdown_pct:.2f}% (max {self.MAX_DRAWDOWN_PCT}%)'
            })

        # 3. Check max open positions
        open_positions = account_state.get('open_positions', 0)

        if open_positions >= self.MAX_OPEN_POSITIONS:
            violations.append({
                'limit_type': 'max_open_positions',
                'limit_value': self.MAX_OPEN_POSITIONS,
                'actual_value': open_positions,
                'action': 'WAIT_FOR_POSITION_CLOSE',
                'reason': f'Max open positions: {open_positions}/{self.MAX_OPEN_POSITIONS}'
            })

        # 4. Check max trades per day
        trades_today = account_state.get('trades_today', 0)

        if trades_today >= self.MAX_TRADES_PER_DAY:
            violations.append({
                'limit_type': 'max_trades_per_day',
                'limit_value': self.MAX_TRADES_PER_DAY,
                'actual_value': trades_today,
                'action': 'STOP_TRADING_FOR_DAY',
                'reason': f'Daily trade limit: {trades_today}/{self.MAX_TRADES_PER_DAY}'
            })

        # 5. Check minimum account balance (emergency stop)
        current_balance = account_state.get('current_balance', 0.0)

        if current_balance < self.MIN_ACCOUNT_BALANCE:
            violations.append({
                'limit_type': 'min_account_balance',
                'limit_value': self.MIN_ACCOUNT_BALANCE,
                'actual_value': current_balance,
                'action': 'STOP_ALL_TRADING_PERMANENTLY',
                'reason': f'Account balance below minimum: ${current_balance:.2f} < ${self.MIN_ACCOUNT_BALANCE}'
            })

        # Log violations to database (best-effort; never blocks the verdict)
        if violations:
            self._log_violations(violations)


            # Determine most severe action
            actions = [v['action'] for v in violations]
            if 'STOP_ALL_TRADING_PERMANENTLY' in actions:
                action = 'STOP_ALL_TRADING_PERMANENTLY'
            elif 'STOP_ALL_TRADING' in actions:
                action = 'STOP_ALL_TRADING'
            elif 'STOP_TRADING_FOR_DAY' in actions:
                action = 'STOP_TRADING_FOR_DAY'
            else:
                action = 'WAIT_FOR_POSITION_CLOSE'

            # Build combined reason
            reasons = [v['reason'] for v in violations]
            reason = '; '.join(reasons)

            logger.error(f"🚨 HARD LIMIT VIOLATION: {reason} → {action}")

            return {
                'passed': False,
                'reason': reason,
                'action': action,
                'violations': violations
            }

        # All checks passed
        return {
            'passed': True,
            'reason': 'All hard limits OK',
            'action': None,
            'violations': []
        }

    def _log_violations(self, violations: list):
        """
        Log hard limit violations to database

        Args:
            violations: List of violation dicts
        """
        try:
            db = self.db
        except Exception as e:
            logger.debug(f"No database configured; skipping violation log ({e})")
            return

        try:
            with db.get_cursor() as cur:
                for violation in violations:
                    cur.execute("""
                        INSERT INTO hard_limit_violations (
                            limit_type,
                            limit_value,
                            actual_value,
                            action_taken
                        ) VALUES (%s, %s, %s, %s)
                    """, (
                        violation['limit_type'],
                        violation['limit_value'],
                        violation['actual_value'],
                        violation['action']
                    ))

                logger.info(f"Logged {len(violations)} hard limit violations to database")

        except Exception as e:
            logger.error(f"Error logging hard limit violations: {e}")


# Singleton instance
_hard_limit_checker = None


def get_hard_limit_checker() -> HardLimitChecker:
    """
    Get singleton instance of HardLimitChecker

    Returns:
        HardLimitChecker instance
    """
    global _hard_limit_checker
    if _hard_limit_checker is None:
        _hard_limit_checker = HardLimitChecker()
    return _hard_limit_checker
