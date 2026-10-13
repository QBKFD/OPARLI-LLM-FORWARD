# backend/services/gap_detector.py
"""
Gap Detection Service

Scans database for missing bars and identifies gaps in historical data
"""

import logging
from typing import List, Dict, Optional
from datetime import datetime, timedelta
from config.database import get_database

logger = logging.getLogger(__name__)


class GapDetector:
    """
    Detects gaps in market data stored in the database

    A gap is defined as missing 1-minute bars between two timestamps
    """

    def __init__(self):
        self.db = get_database()

    def find_gaps(
        self,
        symbol: str,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        min_gap_minutes: int = 5
    ) -> List[Dict]:
        """
        Find gaps in data for a symbol

        Args:
            symbol: Trading symbol (e.g., 'XAUUSD')
            start_time: Start of search range (defaults to 7 days ago)
            end_time: End of search range (defaults to now)
            min_gap_minutes: Minimum gap size to report (default: 5 minutes)

        Returns:
            List of gaps: [{'start': datetime, 'end': datetime, 'duration_minutes': int}, ...]
        """
        try:
            # Default time range: last 7 days
            if end_time is None:
                end_time = datetime.now()
            if start_time is None:
                start_time = end_time - timedelta(days=7)

            logger.info(f"Scanning for gaps in {symbol} from {start_time} to {end_time}")

            # Query to find gaps using LAG window function
            query = """
                WITH bar_gaps AS (
                    SELECT
                        timestamp as current_time,
                        LAG(timestamp) OVER (ORDER BY timestamp) as previous_time,
                        EXTRACT(EPOCH FROM (timestamp - LAG(timestamp) OVER (ORDER BY timestamp))) / 60 as gap_minutes
                    FROM ohlcv_realtime_1min
                    WHERE symbol = %s
                      AND timestamp >= %s
                      AND timestamp <= %s
                    ORDER BY timestamp
                )
                SELECT
                    previous_time as gap_start,
                    current_time as gap_end,
                    gap_minutes::integer as duration_minutes
                FROM bar_gaps
                WHERE gap_minutes > %s
                ORDER BY previous_time
            """

            with self.db.get_cursor() as cursor:
                cursor.execute(query, (symbol, start_time, end_time, min_gap_minutes))
                results = cursor.fetchall()

            gaps = []
            for row in results:
                gap = {
                    'symbol': symbol,
                    'start': row['gap_start'],
                    'end': row['gap_end'],
                    'duration_minutes': row['duration_minutes'],
                    'duration_hours': round(row['duration_minutes'] / 60, 2),
                    'bars_missing': row['duration_minutes']  # 1 bar per minute
                }
                gaps.append(gap)

            logger.info(f"Found {len(gaps)} gaps for {symbol}")
            return gaps

        except Exception as e:
            logger.error(f"Error finding gaps for {symbol}: {e}")
            return []

    def scan_all_symbols(
        self,
        min_gap_minutes: int = 5
    ) -> Dict[str, List[Dict]]:
        """
        Scan all symbols in database for gaps

        Args:
            min_gap_minutes: Minimum gap size to report

        Returns:
            Dict mapping symbol -> list of gaps
        """
        try:
            # Get all unique symbols
            with self.db.get_cursor() as cursor:
                cursor.execute("""
                    SELECT DISTINCT symbol
                    FROM ohlcv_realtime_1min
                    ORDER BY symbol
                """)
                symbols = [row['symbol'] for row in cursor.fetchall()]

            logger.info(f"Scanning {len(symbols)} symbols for gaps")

            # Find gaps for each symbol
            all_gaps = {}
            for symbol in symbols:
                gaps = self.find_gaps(symbol, min_gap_minutes=min_gap_minutes)
                if gaps:
                    all_gaps[symbol] = gaps

            return all_gaps

        except Exception as e:
            logger.error(f"Error scanning all symbols: {e}")
            return {}

    def get_data_coverage(self, symbol: str, days: int = 7) -> Dict:
        """
        Get data coverage statistics for a symbol

        Args:
            symbol: Trading symbol
            days: Number of days to analyze

        Returns:
            Dict with coverage statistics
        """
        try:
            end_time = datetime.now()
            start_time = end_time - timedelta(days=days)

            with self.db.get_cursor() as cursor:
                # Get bar count
                cursor.execute("""
                    SELECT
                        COUNT(*) as bar_count,
                        MIN(timestamp) as first_bar,
                        MAX(timestamp) as last_bar
                    FROM ohlcv_realtime_1min
                    WHERE symbol = %s
                      AND timestamp >= %s
                      AND timestamp <= %s
                """, (symbol, start_time, end_time))

                result = cursor.fetchone()

                if not result or result['bar_count'] == 0:
                    return {
                        'symbol': symbol,
                        'bar_count': 0,
                        'coverage_percent': 0,
                        'first_bar': None,
                        'last_bar': None,
                        'expected_bars': 0,
                        'missing_bars': 0
                    }

                # Calculate expected bars (assuming 24/7 trading)
                time_span_minutes = (end_time - start_time).total_seconds() / 60
                expected_bars = int(time_span_minutes)

                actual_bars = result['bar_count']
                coverage_percent = (actual_bars / expected_bars * 100) if expected_bars > 0 else 0

                return {
                    'symbol': symbol,
                    'bar_count': actual_bars,
                    'expected_bars': expected_bars,
                    'missing_bars': expected_bars - actual_bars,
                    'coverage_percent': round(coverage_percent, 2),
                    'first_bar': result['first_bar'],
                    'last_bar': result['last_bar'],
                    'days_analyzed': days
                }

        except Exception as e:
            logger.error(f"Error getting coverage for {symbol}: {e}")
            return {}


# Global instance
_detector_instance: Optional[GapDetector] = None


def get_gap_detector() -> GapDetector:
    """Get global gap detector instance (singleton)"""
    global _detector_instance
    if _detector_instance is None:
        _detector_instance = GapDetector()
    return _detector_instance
