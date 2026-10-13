# backend/services/throttle_manager.py
"""
Throttle Manager Service

Rate limiting and throttling for Scanner Agent
Prevents over-scanning and manages API costs

Limits:
- Minimum 3 minutes between scans (same symbol)
- Maximum 500 scans per day
- Maximum 15 full analyses per hour (Stage 2)
"""

import logging
from typing import Dict, Optional
from datetime import datetime, timedelta
from collections import deque

logger = logging.getLogger(__name__)


class ThrottleManager:
    """
    Rate limiting and throttling for Scanner Agent

    Responsibilities:
    - Enforce minimum time between scans
    - Track daily scan count
    - Limit full analyses per hour
    - Provide throttle status
    """

    # Throttle limits
    MIN_SCAN_INTERVAL_SECONDS = 180  # 3 minutes
    MAX_SCANS_PER_DAY = 500
    MAX_FULL_ANALYSES_PER_HOUR = 15

    def __init__(self):
        # Last scan time per symbol
        self.last_scan_times: Dict[str, datetime] = {}

        # Daily scan counter
        self.daily_scan_count = 0
        self.daily_reset_time: Optional[datetime] = None

        # Full analysis timestamps (rolling 1 hour window)
        self.full_analysis_times: deque = deque(maxlen=self.MAX_FULL_ANALYSES_PER_HOUR)

        logger.info("✓ Throttle Manager initialized")

    def can_scan(self, symbol: str) -> Dict:
        """
        Check if scanning is allowed for symbol

        Args:
            symbol: Trading symbol

        Returns:
            Dict with throttle result:
            {
                'allowed': True/False,
                'reason': 'ok|too_soon|daily_limit|hourly_limit',
                'wait_seconds': 120,  # If not allowed, how long to wait
                'metadata': {...}
            }
        """
        try:
            now = datetime.utcnow()

            # Reset daily counter if needed
            self._reset_daily_counter_if_needed(now)

            # 1. Check daily limit
            if self.daily_scan_count >= self.MAX_SCANS_PER_DAY:
                return {
                    'allowed': False,
                    'reason': 'daily_limit',
                    'wait_seconds': self._seconds_until_daily_reset(now),
                    'metadata': {
                        'daily_scan_count': self.daily_scan_count,
                        'daily_limit': self.MAX_SCANS_PER_DAY
                    }
                }

            # 2. Check minimum interval per symbol
            if symbol in self.last_scan_times:
                last_scan = self.last_scan_times[symbol]
                elapsed = (now - last_scan).total_seconds()

                if elapsed < self.MIN_SCAN_INTERVAL_SECONDS:
                    wait_seconds = int(self.MIN_SCAN_INTERVAL_SECONDS - elapsed)
                    return {
                        'allowed': False,
                        'reason': 'too_soon',
                        'wait_seconds': wait_seconds,
                        'metadata': {
                            'symbol': symbol,
                            'elapsed_seconds': int(elapsed),
                            'min_interval_seconds': self.MIN_SCAN_INTERVAL_SECONDS
                        }
                    }

            # All checks passed
            return {
                'allowed': True,
                'reason': 'ok',
                'wait_seconds': 0,
                'metadata': {
                    'daily_scan_count': self.daily_scan_count,
                    'daily_limit': self.MAX_SCANS_PER_DAY
                }
            }

        except Exception as e:
            logger.error(f"Error checking throttle for {symbol}: {e}")
            return {
                'allowed': False,
                'reason': f'error: {str(e)}',
                'wait_seconds': 60,
                'metadata': {}
            }

    def can_run_full_analysis(self) -> Dict:
        """
        Check if full analysis (Stage 2) is allowed

        Returns:
            Dict with throttle result:
            {
                'allowed': True/False,
                'reason': 'ok|hourly_limit',
                'wait_seconds': 300,
                'metadata': {...}
            }
        """
        try:
            now = datetime.utcnow()

            # Clean up old timestamps (>1 hour old)
            cutoff = now - timedelta(hours=1)
            while self.full_analysis_times and self.full_analysis_times[0] < cutoff:
                self.full_analysis_times.popleft()

            # Check hourly limit
            if len(self.full_analysis_times) >= self.MAX_FULL_ANALYSES_PER_HOUR:
                # Calculate wait time until oldest analysis expires
                oldest = self.full_analysis_times[0]
                wait_seconds = int((oldest + timedelta(hours=1) - now).total_seconds())

                return {
                    'allowed': False,
                    'reason': 'hourly_limit',
                    'wait_seconds': max(wait_seconds, 0),
                    'metadata': {
                        'full_analyses_last_hour': len(self.full_analysis_times),
                        'hourly_limit': self.MAX_FULL_ANALYSES_PER_HOUR
                    }
                }

            # Allowed
            return {
                'allowed': True,
                'reason': 'ok',
                'wait_seconds': 0,
                'metadata': {
                    'full_analyses_last_hour': len(self.full_analysis_times),
                    'hourly_limit': self.MAX_FULL_ANALYSES_PER_HOUR
                }
            }

        except Exception as e:
            logger.error(f"Error checking full analysis throttle: {e}")
            return {
                'allowed': False,
                'reason': f'error: {str(e)}',
                'wait_seconds': 60,
                'metadata': {}
            }

    def record_scan(self, symbol: str):
        """
        Record that a scan was performed

        Args:
            symbol: Trading symbol
        """
        now = datetime.utcnow()

        # Update last scan time
        self.last_scan_times[symbol] = now

        # Increment daily counter
        self.daily_scan_count += 1

        logger.debug(f"Recorded scan for {symbol} (daily count: {self.daily_scan_count})")

    def record_full_analysis(self):
        """
        Record that a full analysis (Stage 2) was performed
        """
        now = datetime.utcnow()
        self.full_analysis_times.append(now)

        logger.debug(f"Recorded full analysis (last hour: {len(self.full_analysis_times)})")

    def get_status(self) -> Dict:
        """
        Get current throttle status

        Returns:
            Dict with status information
        """
        now = datetime.utcnow()

        # Clean up old full analysis timestamps
        cutoff = now - timedelta(hours=1)
        while self.full_analysis_times and self.full_analysis_times[0] < cutoff:
            self.full_analysis_times.popleft()

        return {
            'daily_scan_count': self.daily_scan_count,
            'daily_limit': self.MAX_SCANS_PER_DAY,
            'daily_remaining': max(0, self.MAX_SCANS_PER_DAY - self.daily_scan_count),
            'full_analyses_last_hour': len(self.full_analysis_times),
            'hourly_limit': self.MAX_FULL_ANALYSES_PER_HOUR,
            'hourly_remaining': max(0, self.MAX_FULL_ANALYSES_PER_HOUR - len(self.full_analysis_times)),
            'daily_reset_in_seconds': self._seconds_until_daily_reset(now),
            'timestamp': now.isoformat()
        }

    def _reset_daily_counter_if_needed(self, now: datetime):
        """
        Reset daily counter if new day

        Args:
            now: Current datetime
        """
        if self.daily_reset_time is None:
            # First run, set reset time to midnight UTC tomorrow
            self.daily_reset_time = (now + timedelta(days=1)).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            logger.info(f"Daily counter initialized, next reset at {self.daily_reset_time}")

        if now >= self.daily_reset_time:
            # New day, reset counter
            logger.info(f"Daily counter reset (was {self.daily_scan_count})")
            self.daily_scan_count = 0
            self.daily_reset_time = (now + timedelta(days=1)).replace(
                hour=0, minute=0, second=0, microsecond=0
            )

    def _seconds_until_daily_reset(self, now: datetime) -> int:
        """
        Calculate seconds until daily counter resets

        Returns:
            Seconds until midnight UTC
        """
        if self.daily_reset_time is None:
            return 0

        delta = self.daily_reset_time - now
        return max(0, int(delta.total_seconds()))


# Singleton instance
_throttle_manager: Optional[ThrottleManager] = None


def get_throttle_manager() -> ThrottleManager:
    """
    Get singleton instance of ThrottleManager

    Returns:
        ThrottleManager instance
    """
    global _throttle_manager
    if _throttle_manager is None:
        _throttle_manager = ThrottleManager()
    return _throttle_manager
