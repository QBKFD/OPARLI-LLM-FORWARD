# backend/services/historical_backfill.py
"""
Historical Data Backfill Service

Fetches historical bar data from Interactive Brokers to fill gaps
"""

import logging
from typing import Dict, List, Optional
from datetime import datetime, timedelta
from ib_insync import IB, Stock, Future, Commodity, util
import time

from config.database import get_database
from services.gap_detector import get_gap_detector

logger = logging.getLogger(__name__)


class HistoricalBackfiller:
    """
    Backfills missing historical data from Interactive Brokers

    Respects IB pacing rules: max 60 requests per 10 minutes
    """

    def __init__(self, tws_host='127.0.0.1', tws_port=4002):
        self.tws_host = tws_host
        self.tws_port = tws_port
        self.ib: Optional[IB] = None
        self.db = get_database()
        self.gap_detector = get_gap_detector()

        # Pacing control (IB limits: 60 requests per 10 minutes)
        self.requests_made = 0
        self.requests_window_start = time.time()
        self.max_requests_per_window = 50  # Conservative limit
        self.window_duration = 600  # 10 minutes in seconds

        # Statistics
        self.bars_backfilled = 0
        self.gaps_filled = 0
        self.errors = 0

    def connect(self) -> bool:
        """Connect to TWS/IB Gateway"""
        try:
            logger.info(f"Connecting to TWS for historical data at {self.tws_host}:{self.tws_port}")

            # ib_insync needs a proper event loop - patch asyncio for thread compatibility
            import asyncio
            try:
                loop = asyncio.get_event_loop()
                if loop.is_closed():
                    raise RuntimeError("closed")
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)

            # Patch for nested event loop (ib_insync requirement in threads)
            try:
                import nest_asyncio
                nest_asyncio.apply(loop)
            except ImportError:
                util.patchAsyncio()

            self.ib = IB()
            self.ib.connect(
                host=self.tws_host,
                port=self.tws_port,
                clientId=2,  # Different client ID from real-time streamer
                timeout=20
            )

            logger.info("✓ Connected to TWS for historical backfill")
            return True

        except Exception as e:
            logger.error(f"Failed to connect to TWS: {e}")
            return False

    def disconnect(self):
        """Disconnect from TWS"""
        if self.ib and self.ib.isConnected():
            self.ib.disconnect()
            logger.info("✓ Disconnected from TWS")

    def _check_pacing(self):
        """
        Check and enforce IB pacing rules

        Waits if we've made too many requests in the current window
        """
        current_time = time.time()
        elapsed = current_time - self.requests_window_start

        # Reset window if 10 minutes have passed
        if elapsed >= self.window_duration:
            self.requests_made = 0
            self.requests_window_start = current_time
            logger.info("Pacing window reset")
            return

        # Check if we've hit the limit
        if self.requests_made >= self.max_requests_per_window:
            wait_time = self.window_duration - elapsed
            logger.warning(f"⚠️ Rate limit reached. Waiting {wait_time:.0f}s before continuing...")
            time.sleep(wait_time)
            # Reset after waiting
            self.requests_made = 0
            self.requests_window_start = time.time()

    def _get_contract(self, symbol: str, sec_type: str = 'CMDTY', exchange: str = 'SMART', currency: str = 'USD'):
        """Get and qualify contract"""
        try:
            if sec_type == 'STK':
                contract = Stock(symbol, 'SMART', currency)
            elif sec_type == 'FUT':
                contract = Future(symbol, exchange, currency=currency)
            elif sec_type == 'CMDTY':
                contract = Commodity(symbol, exchange, currency)
            else:
                raise ValueError(f"Unsupported security type: {sec_type}")

            # Qualify contract
            qualified = self.ib.qualifyContracts(contract)

            if not qualified:
                raise ValueError(f"Could not qualify contract for {symbol}")

            return qualified[0]

        except Exception as e:
            logger.error(f"Error getting contract for {symbol}: {e}")
            return None

    def backfill_gap(
        self,
        symbol: str,
        start_time: datetime,
        end_time: datetime,
        sec_type: str = 'CMDTY',
        exchange: str = 'IBMETAL',
        currency: str = 'USD'
    ) -> int:
        """
        Backfill a specific gap with historical data

        Args:
            symbol: Trading symbol
            start_time: Gap start time
            end_time: Gap end time
            sec_type: Security type
            exchange: Exchange
            currency: Currency

        Returns:
            Number of bars backfilled
        """
        if not self.ib or not self.ib.isConnected():
            logger.error("Not connected to TWS. Call connect() first.")
            return 0

        try:
            logger.info(f"Backfilling {symbol}: {start_time} to {end_time}")

            # Check pacing before making request
            self._check_pacing()

            # Get contract
            contract = self._get_contract(symbol, sec_type, exchange, currency)
            if not contract:
                self.errors += 1
                return 0

            # Calculate duration for request
            duration_seconds = (end_time - start_time).total_seconds()
            duration_days = int(duration_seconds / 86400) + 1

            # Request historical data (1-minute bars)
            self.requests_made += 1
            bars = self.ib.reqHistoricalData(
                contract,
                endDateTime=end_time,
                durationStr=f'{duration_days} D',
                barSizeSetting='1 min',
                whatToShow='TRADES',
                useRTH=False,  # Include extended hours
                formatDate=1  # Return as datetime objects
            )

            if not bars:
                logger.warning(f"No historical data returned for {symbol}")
                return 0

            # Filter bars to only include those in the gap range
            gap_bars = [
                bar for bar in bars
                if start_time <= bar.date <= end_time
            ]

            # Save to database
            saved_count = 0
            for bar in gap_bars:
                try:
                    insert_query = """
                        INSERT INTO ohlcv_realtime_1min
                            (symbol, timestamp, open, high, low, close, volume, bar_count, average)
                        VALUES
                            (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (symbol, timestamp) DO NOTHING
                    """

                    with self.db.get_cursor() as cursor:
                        cursor.execute(insert_query, (
                            symbol,
                            bar.date,
                            float(bar.open),
                            float(bar.high),
                            float(bar.low),
                            float(bar.close),
                            int(bar.volume),
                            int(bar.barCount) if hasattr(bar, 'barCount') else None,
                            float(bar.average) if hasattr(bar, 'average') else None
                        ))

                    saved_count += 1

                except Exception as e:
                    logger.error(f"Error saving bar {bar.date}: {e}")
                    self.errors += 1

            self.bars_backfilled += saved_count
            logger.info(f"✓ Backfilled {saved_count} bars for {symbol}")
            return saved_count

        except Exception as e:
            logger.error(f"Error backfilling gap for {symbol}: {e}")
            self.errors += 1
            return 0

    def backfill_symbol(
        self,
        symbol: str,
        sec_type: str = 'CMDTY',
        exchange: str = 'IBMETAL',
        currency: str = 'USD',
        min_gap_minutes: int = 5
    ) -> Dict:
        """
        Find and backfill all gaps for a symbol

        Args:
            symbol: Trading symbol
            sec_type: Security type
            exchange: Exchange
            currency: Currency
            min_gap_minutes: Minimum gap size to backfill

        Returns:
            Dict with backfill statistics
        """
        try:
            logger.info(f"Starting backfill for {symbol}")

            # Find gaps
            gaps = self.gap_detector.find_gaps(symbol, min_gap_minutes=min_gap_minutes)

            if not gaps:
                logger.info(f"No gaps found for {symbol}")
                return {
                    'symbol': symbol,
                    'gaps_found': 0,
                    'gaps_filled': 0,
                    'bars_backfilled': 0
                }

            logger.info(f"Found {len(gaps)} gaps for {symbol}")

            # Backfill each gap
            initial_bars = self.bars_backfilled
            initial_gaps = self.gaps_filled

            for gap in gaps:
                bars_filled = self.backfill_gap(
                    symbol,
                    gap['start'],
                    gap['end'],
                    sec_type,
                    exchange,
                    currency
                )

                if bars_filled > 0:
                    self.gaps_filled += 1

                # Small delay between requests (courtesy)
                time.sleep(0.5)

            return {
                'symbol': symbol,
                'gaps_found': len(gaps),
                'gaps_filled': self.gaps_filled - initial_gaps,
                'bars_backfilled': self.bars_backfilled - initial_bars
            }

        except Exception as e:
            logger.error(f"Error in backfill_symbol for {symbol}: {e}")
            return {
                'symbol': symbol,
                'error': str(e)
            }

    def backfill_range(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        sec_type: str = 'CMDTY',
        exchange: str = 'SMART',
        currency: str = 'USD'
    ) -> Dict:
        """
        Fetch historical data for a specific date range (day by day).

        IB limits each request to 1 day of 1-min bars, so we loop through days.

        Args:
            symbol: Trading symbol (e.g. 'XAUUSD')
            start_date: Start date 'YYYY-MM-DD'
            end_date: End date 'YYYY-MM-DD'
            sec_type: Security type
            exchange: Exchange
            currency: Currency

        Returns:
            Dict with stats
        """
        if not self.ib or not self.ib.isConnected():
            if not self.connect():
                return {'error': 'Could not connect to TWS'}

        try:
            contract = self._get_contract(symbol, sec_type, exchange, currency)
            if not contract:
                return {'error': f'Could not qualify contract for {symbol}'}

            start_dt = datetime.strptime(start_date, '%Y-%m-%d')
            # Use 22:00 UTC as endDateTime - gold session ends at 22:00 UTC
            # Using midnight causes IB to return only the 23:00-23:59 hour
            end_dt = datetime.strptime(end_date, '%Y-%m-%d').replace(hour=22)

            total_saved = 0
            days_processed = 0
            current = end_dt

            # Debug file for diagnosing bar counts
            debug_file = open('/tmp/backfill_debug.log', 'w')
            debug_file.write(f"start_dt={start_dt}, end_dt={end_dt}\n")
            debug_file.flush()

            while current > start_dt:
                self._check_pacing()

                debug_file.write(f"REQ endDateTime={current.isoformat()}\n")
                debug_file.flush()

                try:
                    bars = self.ib.reqHistoricalData(
                        contract,
                        endDateTime=current,
                        durationStr='1 D',
                        barSizeSetting='1 min',
                        whatToShow='MIDPOINT',
                        useRTH=False,
                        formatDate=1,
                        timeout=120
                    )
                    self.requests_made += 1
                except Exception as e:
                    debug_file.write(f"  ERROR: {e}\n")
                    debug_file.flush()
                    time.sleep(10)
                    current -= timedelta(days=1)
                    continue

                if bars:
                    debug_file.write(f"  GOT {len(bars)} bars: {bars[0].date} to {bars[-1].date}\n")
                    debug_file.flush()
                    saved = 0
                    for bar in bars:
                        try:
                            insert_query = """
                                INSERT INTO ohlcv_realtime_1min
                                    (symbol, timestamp, open, high, low, close, volume, bar_count, average)
                                VALUES
                                    (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                                ON CONFLICT (symbol, timestamp) DO NOTHING
                            """
                            with self.db.get_cursor() as cursor:
                                cursor.execute(insert_query, (
                                    symbol,
                                    bar.date,
                                    float(bar.open),
                                    float(bar.high),
                                    float(bar.low),
                                    float(bar.close),
                                    int(bar.volume),
                                    int(bar.barCount) if hasattr(bar, 'barCount') else None,
                                    float(bar.average) if hasattr(bar, 'average') else None
                                ))
                            saved += 1
                        except Exception as e:
                            logger.error(f"Error saving bar: {e}")
                            self.errors += 1

                    total_saved += saved
                    logger.info(f"  Saved {saved} bars (total: {total_saved})")
                else:
                    logger.info(f"  No data (weekend/holiday)")

                days_processed += 1
                current -= timedelta(days=1)
                time.sleep(1)  # Courtesy delay

            self.bars_backfilled += total_saved
            return {
                'symbol': symbol,
                'days_processed': days_processed,
                'bars_backfilled': total_saved,
                'date_range': f'{start_date} to {end_date}'
            }

        except Exception as e:
            logger.error(f"Error in backfill_range: {e}")
            return {'error': str(e)}

    def get_stats(self) -> Dict:
        """Get backfill statistics"""
        return {
            'bars_backfilled': self.bars_backfilled,
            'gaps_filled': self.gaps_filled,
            'errors': self.errors,
            'requests_made': self.requests_made
        }


# Global instance
_backfiller_instance: Optional[HistoricalBackfiller] = None


def get_backfiller() -> HistoricalBackfiller:
    """Get global backfiller instance (singleton)"""
    global _backfiller_instance
    if _backfiller_instance is None:
        _backfiller_instance = HistoricalBackfiller()
    return _backfiller_instance
