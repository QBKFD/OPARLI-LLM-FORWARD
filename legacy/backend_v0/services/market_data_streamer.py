# backend/services/market_data_streamer.py
"""
Market Data Streaming Service

Direct flow: TWS (IB) → Database + WebSocket, with no external message broker.

Simple and robust for Oracle Cloud Free Tier (1GB RAM)
"""

import logging
import threading
import time
from typing import Dict, List, Callable
from datetime import datetime, timezone, timedelta

from services.tws_connector import TWSConnector
from services.database_writer import DatabaseWriter
from config.database import init_database, get_database

logger = logging.getLogger(__name__)


class MarketDataStreamer:
    """
    Simple market data streamer - direct TWS to Database

    No external broker overhead - saves memory on Oracle Cloud Free Tier
    """

    def __init__(
        self,
        tws_host='127.0.0.1',
        tws_port=4002,  # IB Gateway Docker port
    ):
        self.tws_host = tws_host
        self.tws_port = tws_port
        self.tws = None

        # Unique client ID
        self.client_id = int(time.time() % 10000) + 100

        # Database writer
        self.db_writer = DatabaseWriter()

        # 1-minute bar aggregation
        self.minute_bars: Dict[str, Dict] = {}  # symbol -> current minute bar

        # State
        self.is_running = False
        self.websocket_callbacks: List[Callable] = []
        self.fastapi_event_loop = None

        # Health check and auto-reconnection
        self.health_check_interval = 60  # Check every 60 seconds
        self.last_bar_time: Dict[str, datetime] = {}  # Track last bar per symbol
        self.health_check_thread = None
        self.reconnect_attempts = 0
        self.max_reconnect_attempts = 10

        # Symbol mapping (IB symbol -> storage symbol)
        self.symbol_mapping: Dict[str, str] = {}

    def start(self) -> bool:
        """Start the streaming service"""
        if self.is_running:
            logger.warning("Streamer already running")
            return True

        logger.info("=" * 60)
        logger.info("STARTING MARKET DATA STREAMER (Direct DB)")
        logger.info("=" * 60)

        self.is_running = True

        # Initialize database connection pool
        logger.info("Initializing database connection...")
        if not init_database():
            logger.error("Failed to initialize database")
            self.is_running = False
            return False
        logger.info("✅ Database initialized")

        # Create and connect TWS
        logger.info(f"Connecting to TWS at {self.tws_host}:{self.tws_port}")
        self.tws = TWSConnector(
            host=self.tws_host,
            port=self.tws_port,
            client_id=self.client_id
        )

        if not self.tws.connect():
            logger.error("Failed to connect to TWS")
            self.is_running = False
            return False

        logger.info("✅ TWS connected")

        # Register bar callback
        self.tws.on_bar_update(self._on_bar)

        logger.info("=" * 60)
        logger.info("✅ STREAMER READY - Waiting for subscriptions")
        logger.info("=" * 60)

        # Start health check thread
        self._start_health_check()

        return True

    def stop(self):
        """Stop the streaming service"""
        logger.info("Stopping streamer...")
        self.is_running = False

        # Stop health check
        if self.health_check_thread and self.health_check_thread.is_alive():
            self.health_check_thread.join(timeout=5)

        if self.tws:
            self.tws.disconnect()

        logger.info("✅ Streamer stopped")

    def subscribe_symbol(
        self,
        symbol: str,
        exchange: str = 'SMART',
        sec_type: str = 'STK',
        currency: str = 'USD',
        bar_size: int = 5,
        store_as: str = None
    ) -> bool:
        """
        Subscribe to a symbol

        Args:
            symbol: IB symbol to subscribe to (e.g., XAUUSD)
            exchange: IB exchange
            sec_type: Security type
            currency: Currency
            bar_size: Bar size in seconds
            store_as: Optional different symbol name for storage (e.g., XAUUSD)
                      If provided, data will be stored under this name instead
        """
        if not self.is_running or not self.tws:
            logger.error("Streamer not running")
            return False

        if symbol in self.tws.subscriptions:
            logger.info(f"Already subscribed to {symbol}")
            return True

        # Set up symbol mapping if store_as is provided
        storage_symbol = store_as if store_as else symbol
        if store_as:
            self.symbol_mapping[symbol] = store_as
            logger.info(f"Symbol mapping: {symbol} -> {store_as}")

        logger.info(f"Subscribing to {symbol} (storing as {storage_symbol})...")

        # Fill historical gaps before starting real-time streaming
        try:
            self._fill_historical_gaps(
                symbol=symbol,
                exchange=exchange,
                sec_type=sec_type,
                currency=currency
            )
        except Exception as e:
            logger.error(f"Error filling historical gaps: {e}")
            # Continue with subscription even if gap fill fails

        success = self.tws.subscribe_bars(
            symbol=symbol,
            exchange=exchange,
            sec_type=sec_type,
            currency=currency,
            bar_size=bar_size
        )

        if success:
            logger.info(f"✅ Subscribed to {symbol}")
        else:
            logger.error(f"❌ Failed to subscribe to {symbol}")

        return success

    def _fill_historical_gaps(
        self,
        symbol: str,
        exchange: str,
        sec_type: str,
        currency: str
    ):
        """
        Fill gaps in historical data before starting real-time streaming.
        Like TradingView, fetches data between last stored bar and now.
        """
        # Get storage symbol (may be different from IB symbol)
        storage_symbol = self.symbol_mapping.get(symbol, symbol)
        logger.info(f"🔍 Checking for historical gaps for {symbol} (storing as {storage_symbol})...")

        # Get the last bar timestamp from database (use storage symbol)
        last_bar_time = self._get_last_bar_time(storage_symbol)

        if last_bar_time is None:
            # No existing data - fetch last 2 days of historical data
            logger.info(f"No existing data for {symbol}, fetching 2 days of history")
            duration = '2 D'
        else:
            # Calculate gap duration
            now = datetime.now(timezone.utc)
            gap = now - last_bar_time

            if gap.total_seconds() < 120:  # Less than 2 minutes gap
                logger.info(f"No significant gap for {symbol} (last bar: {last_bar_time})")
                return

            # Convert gap to IB duration string
            gap_hours = gap.total_seconds() / 3600
            gap_days = gap.days

            if gap_days >= 1:
                # Add 1 extra day for safety
                duration = f'{gap_days + 1} D'
            else:
                # Gap is less than a day, use hours
                hours = int(gap_hours) + 1
                if hours > 24:
                    duration = '2 D'
                else:
                    duration = f'{hours * 3600} S'  # IB uses seconds for small durations

            logger.info(f"Gap detected for {symbol}: {gap} (fetching {duration} of history)")

        # Fetch historical bars from IB
        what_to_show = 'MIDPOINT' if sec_type == 'CMDTY' else 'TRADES'
        bars = self.tws.fetch_historical_bars(
            symbol=symbol,
            exchange=exchange,
            sec_type=sec_type,
            currency=currency,
            duration=duration,
            bar_size='1 min',
            what_to_show=what_to_show
        )

        if not bars:
            logger.warning(f"No historical bars fetched for {symbol}")
            return

        # Filter bars that are newer than our last stored bar
        new_bars = []
        for bar in bars:
            bar_time = bar.get('timestamp')
            if isinstance(bar_time, str):
                bar_time = datetime.fromisoformat(bar_time.replace('Z', '+00:00'))
                if bar_time.tzinfo is None:
                    bar_time = bar_time.replace(tzinfo=timezone.utc)

            # Only save bars newer than last stored bar
            if last_bar_time is None or bar_time > last_bar_time:
                bar['is_final'] = True  # Mark as final so it gets saved
                bar['symbol'] = storage_symbol  # Use mapped symbol for storage
                new_bars.append(bar)

        if new_bars:
            logger.info(f"💾 Saving {len(new_bars)} historical bars for {storage_symbol}")
            saved = self.db_writer.save_bars_batch(new_bars)
            logger.info(f"✅ Saved {saved} historical bars for {storage_symbol}")
        else:
            logger.info(f"No new historical bars to save for {storage_symbol}")

    def _get_last_bar_time(self, symbol: str) -> datetime:
        """Get the timestamp of the last stored bar for a symbol"""
        try:
            db = get_database()
            query = """
                SELECT MAX(timestamp) as last_time
                FROM ohlcv_realtime_1min
                WHERE symbol = %s
            """
            with db.get_cursor() as cursor:
                cursor.execute(query, (symbol,))
                result = cursor.fetchone()
                if result and result[0]:
                    last_time = result[0]
                    # Ensure timezone aware
                    if last_time.tzinfo is None:
                        last_time = last_time.replace(tzinfo=timezone.utc)
                    return last_time
                return None
        except Exception as e:
            logger.error(f"Error getting last bar time for {symbol}: {e}")
            return None

    def _on_bar(self, symbol: str, bar_data: Dict):
        """
        Callback when TWS sends bar data

        Aggregates into 1-minute bars with tick volume
        """
        try:
            # Apply symbol mapping if configured
            storage_symbol = self.symbol_mapping.get(symbol, symbol)

            # Get timestamp and align to minute
            ts = bar_data.get('timestamp')
            if isinstance(ts, str):
                ts = datetime.fromisoformat(ts.replace('Z', '+00:00'))
            elif ts is None:
                ts = datetime.now(timezone.utc)

            minute_ts = ts.replace(second=0, microsecond=0)
            minute_key = minute_ts.isoformat()

            # Initialize minute bar if needed (use storage_symbol for tracking)
            if storage_symbol not in self.minute_bars or self.minute_bars[storage_symbol].get('minute_key') != minute_key:
                # Save previous minute bar if exists
                if storage_symbol in self.minute_bars and self.minute_bars[storage_symbol].get('tick_count', 0) > 0:
                    self._save_minute_bar(storage_symbol)

                # Start new minute bar (store under storage_symbol, not IB symbol)
                self.minute_bars[storage_symbol] = {
                    'minute_key': minute_key,
                    'timestamp': minute_ts,
                    'symbol': storage_symbol,  # Use mapped symbol for storage
                    'open': bar_data['open'],
                    'high': bar_data['high'],
                    'low': bar_data['low'],
                    'close': bar_data['close'],
                    'tick_count': 1,
                    'volume': 1,  # Tick volume
                    'is_final': False
                }
            else:
                # Update existing minute bar
                bar = self.minute_bars[storage_symbol]
                bar['high'] = max(bar['high'], bar_data['high'])
                bar['low'] = min(bar['low'], bar_data['low'])
                bar['close'] = bar_data['close']
                bar['tick_count'] += 1
                bar['volume'] = bar['tick_count']  # Tick volume

            logger.debug(f"📊 {storage_symbol}: ${bar_data['close']:.2f} (ticks: {self.minute_bars[storage_symbol]['tick_count']})")

            # Update last bar time for health check (use storage_symbol)
            self.last_bar_time[storage_symbol] = datetime.now(timezone.utc)

            # Broadcast LIVE update on every tick (TradingView-style)
            self._broadcast_live(storage_symbol, self.minute_bars[storage_symbol])

        except Exception as e:
            logger.error(f"Error in bar callback: {e}")

    def _save_minute_bar(self, symbol: str):
        """Save completed 1-minute bar to database"""
        if symbol not in self.minute_bars:
            return

        bar = self.minute_bars[symbol].copy()
        bar['is_final'] = True

        logger.info(f"✓ Saving 1-min bar: {symbol} @ {bar['timestamp']} - Vol: {bar['volume']}")

        try:
            self.db_writer.save_bar(bar)
        except Exception as e:
            logger.error(f"Error saving to database: {e}")

        # Broadcast to WebSocket clients
        self._broadcast(symbol, bar)

    def _broadcast_live(self, symbol: str, bar: Dict):
        """Broadcast live tick update to WebSocket clients (every 5 seconds)"""
        # Format for frontend chart update
        live_data = {
            'type': 'live',
            'symbol': symbol,
            'timestamp': bar['timestamp'].isoformat() if hasattr(bar['timestamp'], 'isoformat') else bar['timestamp'],
            'open': float(bar['open']),
            'high': float(bar['high']),
            'low': float(bar['low']),
            'close': float(bar['close']),
            'volume': bar['volume'],
            'is_final': False
        }
        self._broadcast(symbol, live_data)

    def _broadcast(self, symbol: str, data: Dict):
        """Broadcast to WebSocket clients"""
        import asyncio
        import inspect

        for callback in self.websocket_callbacks:
            try:
                if inspect.iscoroutinefunction(callback):
                    if self.fastapi_event_loop:
                        asyncio.run_coroutine_threadsafe(
                            callback(symbol, data),
                            self.fastapi_event_loop
                        )
                else:
                    callback(symbol, data)
            except Exception as e:
                logger.error(f"Broadcast error: {e}")

    def register_websocket_callback(self, callback: Callable):
        """Register WebSocket callback"""
        if self.fastapi_event_loop is None:
            try:
                import asyncio
                self.fastapi_event_loop = asyncio.get_event_loop()
            except RuntimeError:
                pass
        self.websocket_callbacks.append(callback)

    def unregister_websocket_callback(self, callback: Callable):
        """Unregister WebSocket callback"""
        try:
            self.websocket_callbacks.remove(callback)
        except ValueError:
            pass

    def _start_health_check(self):
        """Start health check thread for auto-reconnection"""
        if self.health_check_thread and self.health_check_thread.is_alive():
            logger.warning("Health check thread already running")
            return

        self.health_check_thread = threading.Thread(
            target=self._health_check_worker,
            daemon=True,
            name="HealthCheckThread"
        )
        self.health_check_thread.start()
        logger.info("✅ Health check thread started")

    def _health_check_worker(self):
        """Worker thread that monitors TWS connection health"""
        logger.info("🏥 Health check worker started (checking every 60s)")

        while self.is_running:
            try:
                time.sleep(self.health_check_interval)

                if not self.is_running:
                    break

                # Check TWS connection
                if not self.tws or not self.tws.connected:
                    logger.warning("⚠️ TWS connection lost - attempting reconnection...")
                    self._attempt_reconnect()
                    continue

                # Check if we're receiving data (for subscribed symbols)
                if self.tws.subscriptions:
                    now = datetime.now(timezone.utc)
                    stale_threshold = timedelta(minutes=5)

                    for symbol in list(self.tws.subscriptions.keys()):
                        last_bar = self.last_bar_time.get(symbol)
                        if last_bar and (now - last_bar) > stale_threshold:
                            logger.warning(f"⚠️ No data for {symbol} in {stale_threshold.total_seconds()/60:.0f} minutes - reconnecting...")
                            self._attempt_reconnect()
                            break
                        elif last_bar:
                            logger.info(f"✓ Health OK - {symbol} last bar: {(now - last_bar).total_seconds():.0f}s ago")

            except Exception as e:
                logger.error(f"Health check error: {e}")

        logger.info("🏥 Health check worker stopped")

    def _attempt_reconnect(self):
        """Attempt to reconnect to TWS"""
        if self.reconnect_attempts >= self.max_reconnect_attempts:
            logger.error(f"❌ Max reconnect attempts ({self.max_reconnect_attempts}) reached - giving up")
            return

        self.reconnect_attempts += 1
        logger.info(f"🔄 Reconnection attempt {self.reconnect_attempts}/{self.max_reconnect_attempts}")

        try:
            # Disconnect old connection
            if self.tws:
                try:
                    self.tws.disconnect()
                except:
                    pass

            # Wait before reconnecting
            time.sleep(5)

            # Create new TWS connection with new client ID
            self.client_id = int(time.time() % 10000) + 100
            logger.info(f"Creating new TWS connection (client_id={self.client_id})")

            self.tws = TWSConnector(
                host=self.tws_host,
                port=self.tws_port,
                client_id=self.client_id
            )

            if not self.tws.connect():
                logger.error("❌ Reconnection failed")
                return

            logger.info("✅ Reconnected to TWS")

            # Register callback
            self.tws.on_bar_update(self._on_bar)

            # Re-subscribe to all symbols
            subscribed_symbols = list(self.tws.subscriptions.keys()) if self.tws else []
            for symbol in subscribed_symbols:
                logger.info(f"Re-subscribing to {symbol}...")
                # Note: subscription details would need to be stored to re-subscribe properly
                # For now, this will be handled by the subscribe_symbol call from the API

            # Reset reconnect counter on success
            self.reconnect_attempts = 0
            logger.info("✅ Reconnection complete")

        except Exception as e:
            logger.error(f"Reconnection error: {e}")

    def get_status(self) -> Dict:
        """Get streamer status"""
        tws_status = self.tws.get_connection_status() if self.tws else {'connected': False}
        return {
            'running': self.is_running,
            'tws': tws_status,
            'database_writer': self.db_writer.get_stats(),
            'subscribed_symbols': list(self.tws.subscriptions.keys()) if self.tws else [],
            'websocket_clients': len(self.websocket_callbacks),
            'health_check_active': self.health_check_thread.is_alive() if self.health_check_thread else False,
            'reconnect_attempts': self.reconnect_attempts
        }


# Global instance
_streamer_instance = None


def get_streamer() -> MarketDataStreamer:
    """Get the global streamer instance"""
    global _streamer_instance
    if _streamer_instance is None:
        _streamer_instance = MarketDataStreamer()
    return _streamer_instance


# Standalone runner
if __name__ == '__main__':
    import signal
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    streamer = MarketDataStreamer()

    def signal_handler(sig, frame):
        logger.info("Shutting down...")
        streamer.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    if streamer.start():
        # Subscribe to XAUUSD
        streamer.subscribe_symbol(
            symbol='XAUUSD',
            exchange='IBMETAL',
            sec_type='CMDTY',
            currency='USD'
        )

        # Keep running
        logger.info("Streamer running. Press Ctrl+C to stop.")
        while streamer.is_running:
            time.sleep(1)
    else:
        logger.error("Failed to start streamer")
        sys.exit(1)
