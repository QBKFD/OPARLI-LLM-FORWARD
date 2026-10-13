# backend/services/database_writer.py
"""
Database Writer Service

Saves market data bars to PostgreSQL (ohlcv_realtime_1min table)
Only stores final bars (is_final=True), not live updates
"""

import logging
from typing import Dict, Optional
from datetime import datetime
from decimal import Decimal
from config.database import get_database, DatabaseConfig
from services.session_tracker import get_session_tracker

logger = logging.getLogger(__name__)


class DatabaseWriter:
    """
    Writes market data bars to PostgreSQL

    Only stores completed bars (is_final=True) to avoid duplicate/incomplete data
    """

    def __init__(self, db: Optional[DatabaseConfig] = None):
        """
        Initialize database writer

        Args:
            db: Database configuration (defaults to global instance)
        """
        self.db = db or get_database()
        self.bars_written = 0
        self.bars_skipped = 0
        self.errors = 0

    def _validate_bar(self, bar_data: Dict) -> bool:
        """
        Validate bar data to reject anomalous bars

        Checks for unrealistic price ranges that indicate bad data
        """
        try:
            o = float(bar_data['open'])
            h = float(bar_data['high'])
            l = float(bar_data['low'])
            c = float(bar_data['close'])

            # CRITICAL: Reject zero or negative prices
            if o <= 0 or h <= 0 or l <= 0 or c <= 0:
                logger.error(f"❌ REJECTED bar with zero/negative price: O={o} H={h} L={l} C={c}")
                return False

            # CRITICAL: Reject NaN or Inf values
            if not all(isinstance(x, (int, float)) and abs(x) < float('inf') for x in [o, h, l, c]):
                logger.error(f"❌ REJECTED bar with NaN/Inf: O={o} H={h} L={l} C={c}")
                return False

            # For gold (XAUUSD), prices should be in reasonable range (1000-10000)
            if any(p < 1000 or p > 10000 for p in [o, h, l, c]):
                logger.error(f"❌ REJECTED bar with unrealistic gold price: O={o} H={h} L={l} C={c}")
                return False

            # Basic OHLC validation
            if h < l:
                logger.warning(f"Invalid bar: high ({h}) < low ({l})")
                return False

            if h < o or h < c:
                logger.warning(f"Invalid bar: high ({h}) < open ({o}) or close ({c})")
                return False

            if l > o or l > c:
                logger.warning(f"Invalid bar: low ({l}) > open ({o}) or close ({c})")
                return False

            # CRITICAL: Reject bars with abnormally large range
            # For 1-minute bars, a range > 20 points indicates market closure spanning
            # These create ugly vertical lines on the chart
            bar_range = h - l
            if bar_range > 20:
                logger.error(f"❌ REJECTED bar with excessive range: {bar_range:.2f} points (max 20)")
                logger.error(f"   This is likely a market closure/reopening bar: O={o} H={h} L={l} C={c}")
                return False

            # CRITICAL: Reject flatline bars (zero movement)
            # These occur during market closures when TWS repeats last price
            # They create dense clusters of bars with no information
            if bar_range < 0.5:  # Less than $0.50 movement
                # Check if OHLC are all nearly identical (within 0.2 points)
                if abs(o - c) < 0.2 and abs(h - l) < 0.2:
                    logger.warning(f"⚠️ REJECTED flatline bar (market likely closed): O={o} H={h} L={l} C={c}")
                    return False

            return True

        except (KeyError, TypeError, ValueError) as e:
            logger.error(f"Bar validation error: {e}")
            return False

    def save_bar(self, bar_data: Dict) -> bool:
        """
        Save a single bar to database

        Args:
            bar_data: Bar dictionary with OHLCV data

        Returns:
            bool: True if saved successfully
        """
        # Only save final bars (skip live updates)
        if not bar_data.get('is_final', True):
            self.bars_skipped += 1
            logger.debug(f"Skipping live update for {bar_data.get('symbol')}")
            return False

        # Validate bar data
        if not self._validate_bar(bar_data):
            self.bars_skipped += 1
            return False

        try:
            symbol = bar_data['symbol']
            timestamp = bar_data['timestamp']

            # Convert ISO timestamp string to datetime if needed
            if isinstance(timestamp, str):
                timestamp = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))

            # Insert into ohlcv_realtime_1min table
            insert_query = """
                INSERT INTO ohlcv_realtime_1min
                    (symbol, timestamp, open, high, low, close, volume, bar_count, average)
                VALUES
                    (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (symbol, timestamp)
                DO UPDATE SET
                    open = EXCLUDED.open,
                    high = EXCLUDED.high,
                    low = EXCLUDED.low,
                    close = EXCLUDED.close,
                    volume = EXCLUDED.volume,
                    bar_count = EXCLUDED.bar_count,
                    average = EXCLUDED.average,
                    updated_at = CURRENT_TIMESTAMP
            """

            with self.db.get_cursor() as cursor:
                cursor.execute(insert_query, (
                    symbol,
                    timestamp,
                    float(bar_data['open']),
                    float(bar_data['high']),
                    float(bar_data['low']),
                    float(bar_data['close']),
                    int(bar_data['volume']),
                    bar_data.get('tick_count'),
                    bar_data.get('average')
                ))

            self.bars_written += 1
            logger.info(f"✓ Saved bar to DB: {symbol} @ {timestamp} (${bar_data['close']:.2f})")

            # Update session levels in real-time
            try:
                session_tracker = get_session_tracker()
                session_tracker.update_levels_from_bar(
                    symbol=symbol,
                    timestamp=timestamp,
                    high=Decimal(str(bar_data['high'])),
                    low=Decimal(str(bar_data['low'])),
                    open_price=Decimal(str(bar_data['open'])),
                    close_price=Decimal(str(bar_data['close']))
                )
            except Exception as e:
                logger.warning(f"Failed to update session levels: {e}")

            return True

        except Exception as e:
            self.errors += 1
            logger.error(f"Failed to save bar to database: {e}")
            logger.error(f"Bar data: {bar_data}")
            return False

    def save_bars_batch(self, bars: list) -> int:
        """
        Save multiple bars in batch

        Args:
            bars: List of bar dictionaries

        Returns:
            int: Number of bars saved successfully
        """
        saved_count = 0

        for bar in bars:
            if self.save_bar(bar):
                saved_count += 1

        return saved_count

    def get_stats(self) -> Dict:
        """
        Get writer statistics

        Returns:
            dict: Statistics about bars written
        """
        return {
            'bars_written': self.bars_written,
            'bars_skipped': self.bars_skipped,
            'errors': self.errors,
            'success_rate': (
                self.bars_written / (self.bars_written + self.errors)
                if (self.bars_written + self.errors) > 0
                else 0
            )
        }

    def reset_stats(self):
        """Reset statistics counters"""
        self.bars_written = 0
        self.bars_skipped = 0
        self.errors = 0


# Global writer instance
_writer_instance: Optional[DatabaseWriter] = None


def get_writer() -> DatabaseWriter:
    """Get global database writer instance (singleton)"""
    global _writer_instance
    if _writer_instance is None:
        _writer_instance = DatabaseWriter()
    return _writer_instance
