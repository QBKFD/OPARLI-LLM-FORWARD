# backend/services/session_tracker.py
"""
Session Level Tracker

Tracks and updates session high/low levels in real-time as market data streams in.
Supports Asian, London, New York sessions plus daily and weekly levels.

Session Times (UTC):
- Asian:  00:00 - 09:00 UTC
- London: 08:00 - 17:00 UTC
- New York: 13:00 - 22:00 UTC
- Daily: 00:00 - 23:59 UTC (forex day starts Sunday 22:00 UTC)
- Weekly: Monday 00:00 - Friday 22:00 UTC
"""

import logging
from datetime import datetime, timedelta, time
from typing import Dict, List, Optional, Tuple
from decimal import Decimal
import pytz

from config.database import get_database

logger = logging.getLogger(__name__)

# Session definitions (UTC times)
SESSIONS = {
    'asian': {
        'start': time(0, 0),   # 00:00 UTC
        'end': time(9, 0),     # 09:00 UTC
        'color': '#00CED1',    # Dark Cyan
        'style': 'dotted'
    },
    'london': {
        'start': time(8, 0),   # 08:00 UTC
        'end': time(17, 0),    # 17:00 UTC
        'color': '#FFA500',    # Orange
        'style': 'dotted'
    },
    'newyork': {
        'start': time(13, 0),  # 13:00 UTC
        'end': time(22, 0),    # 22:00 UTC
        'color': '#4169E1',    # Royal Blue
        'style': 'dotted'
    },
    'daily': {
        'start': time(0, 0),
        'end': time(23, 59),
        'color': '#FF4444',    # Red for high, Green for low
        'style': 'dashed'
    },
    'prev_daily': {
        'color': '#AA0000',    # Dark Red for high, Dark Green for low
        'style': 'solid'
    },
    'weekly': {
        'color': '#9932CC',    # Purple
        'style': 'dashed'
    },
    'prev_weekly': {
        'color': '#6B238E',    # Dark Purple
        'style': 'solid'
    }
}


class SessionTracker:
    """
    Tracks session levels in real-time

    Updates session_levels table as new bars arrive.
    Provides methods to query current levels for chart generation.
    """

    def __init__(self):
        self.utc = pytz.UTC
        logger.info("✓ Session Tracker initialized")

    def get_current_sessions(self, timestamp: datetime) -> List[str]:
        """
        Determine which sessions are active at given timestamp

        Args:
            timestamp: UTC timestamp

        Returns:
            List of active session names
        """
        if timestamp.tzinfo is None:
            timestamp = self.utc.localize(timestamp)

        current_time = timestamp.time()
        active = ['daily']  # Daily is always active

        # Check each session
        for session_name in ['asian', 'london', 'newyork']:
            session = SESSIONS[session_name]
            start = session['start']
            end = session['end']

            if start <= current_time < end:
                active.append(session_name)

        return active

    def update_levels_from_bar(
        self,
        symbol: str,
        timestamp: datetime,
        high: Decimal,
        low: Decimal,
        open_price: Optional[Decimal] = None,
        close_price: Optional[Decimal] = None
    ):
        """
        Update session levels based on new bar data

        Called by database_writer for each new 1-minute bar.

        Args:
            symbol: Trading symbol
            timestamp: Bar timestamp (UTC)
            high: Bar high price
            low: Bar low price
            open_price: Bar open (optional, for session open tracking)
            close_price: Bar close (optional, for session close tracking)
        """
        if timestamp.tzinfo is None:
            timestamp = self.utc.localize(timestamp)

        date = timestamp.date()
        active_sessions = self.get_current_sessions(timestamp)

        db = get_database()

        for session_type in active_sessions:
            try:
                self._upsert_session_level(
                    db, symbol, date, session_type,
                    high, low, open_price, close_price, timestamp
                )
            except Exception as e:
                logger.error(f"Error updating {session_type} level: {e}")

        # Also update weekly level
        try:
            self._update_weekly_level(db, symbol, date, high, low, timestamp)
        except Exception as e:
            logger.error(f"Error updating weekly level: {e}")

    def _upsert_session_level(
        self,
        db,
        symbol: str,
        date,
        session_type: str,
        high: Decimal,
        low: Decimal,
        open_price: Optional[Decimal],
        close_price: Optional[Decimal],
        timestamp: datetime
    ):
        """
        Insert or update session level

        Uses PostgreSQL UPSERT to atomically update high/low
        """
        session_config = SESSIONS.get(session_type, {})
        session_start = None
        session_end = None

        if 'start' in session_config:
            session_start = datetime.combine(date, session_config['start'])
            session_start = self.utc.localize(session_start)
        if 'end' in session_config:
            session_end = datetime.combine(date, session_config['end'])
            session_end = self.utc.localize(session_end)

        query = """
            INSERT INTO session_levels
                (symbol, date, session_type, high, low, open_price, close_price,
                 session_start, session_end, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
            ON CONFLICT (symbol, date, session_type)
            DO UPDATE SET
                high = GREATEST(session_levels.high, EXCLUDED.high),
                low = LEAST(session_levels.low, EXCLUDED.low),
                close_price = EXCLUDED.close_price,
                updated_at = NOW()
            WHERE session_levels.is_complete = FALSE
        """

        with db.get_cursor() as cur:
            cur.execute(query, (
                symbol, date, session_type,
                high, low, open_price, close_price,
                session_start, session_end
            ))

    def _update_weekly_level(
        self,
        db,
        symbol: str,
        date,
        high: Decimal,
        low: Decimal,
        timestamp: datetime
    ):
        """
        Update weekly high/low

        Week starts on Monday
        """
        # Get Monday of current week
        days_since_monday = date.weekday()
        week_start = date - timedelta(days=days_since_monday)

        query = """
            INSERT INTO session_levels
                (symbol, date, session_type, high, low, updated_at)
            VALUES (%s, %s, 'weekly', %s, %s, NOW())
            ON CONFLICT (symbol, date, session_type)
            DO UPDATE SET
                high = GREATEST(session_levels.high, EXCLUDED.high),
                low = LEAST(session_levels.low, EXCLUDED.low),
                updated_at = NOW()
            WHERE session_levels.is_complete = FALSE
        """

        with db.get_cursor() as cur:
            cur.execute(query, (symbol, week_start, high, low))

    def mark_session_complete(self, symbol: str, date, session_type: str):
        """
        Mark a session as complete (no more updates)

        Called when session time window ends.
        """
        db = get_database()

        query = """
            UPDATE session_levels
            SET is_complete = TRUE, updated_at = NOW()
            WHERE symbol = %s AND date = %s AND session_type = %s
        """

        with db.get_cursor() as cur:
            cur.execute(query, (symbol, date, session_type))

        logger.info(f"✓ Marked {session_type} session complete for {symbol} on {date}")

    def get_current_levels(self, symbol: str, reference_date: Optional[datetime] = None) -> Dict:
        """
        Get all relevant levels for chart generation

        Args:
            symbol: Trading symbol
            reference_date: Reference date (defaults to today)

        Returns:
            Dictionary with all level data for chart drawing
        """
        if reference_date is None:
            reference_date = datetime.now(self.utc)

        if reference_date.tzinfo is None:
            reference_date = self.utc.localize(reference_date)

        today = reference_date.date()
        yesterday = today - timedelta(days=1)

        # Get Monday of current and previous week
        days_since_monday = today.weekday()
        this_week_start = today - timedelta(days=days_since_monday)
        last_week_start = this_week_start - timedelta(days=7)

        db = get_database()

        levels = {}

        # Query today's sessions
        query = """
            SELECT session_type, high, low, open_price, close_price, is_complete
            FROM session_levels
            WHERE symbol = %s AND date = %s
        """

        with db.get_cursor() as cur:
            # Today's levels (asian, london, newyork, daily)
            cur.execute(query, (symbol, today))
            for row in cur.fetchall():
                session_type = row[0]
                levels[session_type] = {
                    'high': float(row[1]) if row[1] else None,
                    'low': float(row[2]) if row[2] else None,
                    'open': float(row[3]) if row[3] else None,
                    'close': float(row[4]) if row[4] else None,
                    'is_complete': row[5],
                    'config': SESSIONS.get(session_type, {})
                }

            # Previous day levels
            cur.execute(query, (symbol, yesterday))
            for row in cur.fetchall():
                if row[0] == 'daily':
                    levels['prev_daily'] = {
                        'high': float(row[1]) if row[1] else None,
                        'low': float(row[2]) if row[2] else None,
                        'is_complete': row[5],
                        'config': SESSIONS.get('prev_daily', {})
                    }

            # Current week levels
            cur.execute(query, (symbol, this_week_start))
            for row in cur.fetchall():
                if row[0] == 'weekly':
                    levels['weekly'] = {
                        'high': float(row[1]) if row[1] else None,
                        'low': float(row[2]) if row[2] else None,
                        'is_complete': row[5],
                        'config': SESSIONS.get('weekly', {})
                    }

            # Previous week levels
            cur.execute(query, (symbol, last_week_start))
            for row in cur.fetchall():
                if row[0] == 'weekly':
                    levels['prev_weekly'] = {
                        'high': float(row[1]) if row[1] else None,
                        'low': float(row[2]) if row[2] else None,
                        'is_complete': row[5],
                        'config': SESSIONS.get('prev_weekly', {})
                    }

        return levels

    def backfill_levels_from_history(self, symbol: str, start_date, end_date=None):
        """
        Backfill session levels from historical 1-minute data

        Useful for populating levels from existing data.

        Args:
            symbol: Trading symbol
            start_date: Start date for backfill
            end_date: End date (defaults to today)
        """
        if end_date is None:
            end_date = datetime.now(self.utc).date()

        logger.info(f"Backfilling session levels for {symbol} from {start_date} to {end_date}")

        db = get_database()

        # Query all 1-minute bars in date range
        query = """
            SELECT timestamp, high, low, open, close
            FROM ohlcv_1min
            WHERE symbol = %s
              AND timestamp >= %s
              AND timestamp < %s
            ORDER BY timestamp
        """

        current_date = start_date
        while current_date <= end_date:
            next_date = current_date + timedelta(days=1)

            with db.get_cursor() as cur:
                cur.execute(query, (symbol, current_date, next_date))
                rows = cur.fetchall()

            for row in rows:
                timestamp, high, low, open_price, close_price = row
                self.update_levels_from_bar(
                    symbol, timestamp,
                    Decimal(str(high)), Decimal(str(low)),
                    Decimal(str(open_price)), Decimal(str(close_price))
                )

            # Mark completed sessions for this date
            for session_type in ['asian', 'london', 'newyork', 'daily']:
                self.mark_session_complete(symbol, current_date, session_type)

            logger.info(f"  ✓ Backfilled {current_date}: {len(rows)} bars")
            current_date = next_date

        logger.info(f"✓ Backfill complete for {symbol}")


# Singleton instance
_session_tracker: Optional[SessionTracker] = None


def get_session_tracker() -> SessionTracker:
    """Get singleton instance of SessionTracker"""
    global _session_tracker
    if _session_tracker is None:
        _session_tracker = SessionTracker()
    return _session_tracker


# Example usage
if __name__ == '__main__':
    from datetime import date

    tracker = get_session_tracker()

    # Check which sessions are active now
    now = datetime.now(pytz.UTC)
    active = tracker.get_current_sessions(now)
    print(f"Active sessions at {now}: {active}")

    # Get current levels for XAUUSD
    levels = tracker.get_current_levels('XAUUSD')
    print(f"\nCurrent levels for XAUUSD:")
    for level_name, level_data in levels.items():
        print(f"  {level_name}: High={level_data.get('high')}, Low={level_data.get('low')}")
