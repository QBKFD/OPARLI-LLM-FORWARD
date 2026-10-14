# backend/services/tws_connector.py
"""
TWS (Interactive Brokers) Connection Service using ib_insync

Handles connection to TWS/IB Gateway and subscribes to real-time market data.
"""

from ib_insync import IB, Stock, Future, Commodity, CFD, util
from typing import Callable, Dict, Optional, List
import logging
import time
import asyncio
import threading
from concurrent.futures import Future as ThreadFuture
from datetime import datetime
from queue import Queue

logger = logging.getLogger(__name__)


class TWSConnector:
    """
    Manages connection to Interactive Brokers TWS/Gateway

    Features:
    - Connect/disconnect to TWS
    - Subscribe to real-time bars
    - Handle connection events
    - Auto-reconnection with exponential backoff
    """

    def __init__(self, host='127.0.0.1', port=7497, client_id=1, readonly=False):
        self.host = host
        self.port = port
        self.client_id = client_id
        # True for data-only clients of a Read-Only API gateway: skips ib_insync's
        # order sync at connect (rejected with error 321 there, then times out)
        self.readonly = readonly

        self.ib = IB()
        self.connected = False
        self.subscriptions: Dict[str, any] = {}  # symbol -> contract
        self.bar_callbacks: List[Callable] = []

        # Bar building from ticks (for commodities)
        self.current_bars: Dict[str, dict] = {}  # symbol -> current bar data
        self.bar_interval = 60  # seconds (1-minute bars)
        self.update_interval = 5  # Send updates every 5 seconds
        self.last_update_time: Dict[str, datetime] = {}  # symbol -> last update timestamp

        # Reconnection settings
        self.max_retries = 5
        self.retry_delay = 5  # seconds
        self.current_retry = 0

        # IB thread management
        self._ib_thread = None
        self._ib_loop = None
        self._command_queue = Queue()
        self._result_queue = Queue()

    def _ib_thread_worker(self):
        """Worker thread that runs IB event loop"""
        self._ib_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._ib_loop)

        try:
            # Connect
            self._ib_loop.run_until_complete(
                self.ib.connectAsync(
                    host=self.host,
                    port=self.port,
                    clientId=self.client_id,
                    timeout=20,
                    readonly=self.readonly
                )
            )
            self.connected = True

            # Register event handlers
            self.ib.disconnectedEvent += self._on_disconnected
            self.ib.errorEvent += self._on_error
            self.ib.pendingTickersEvent += self._on_pending_tickers

            logger.info("✓ Connected to TWS successfully")
            self._result_queue.put(True)

            # Keep running to process IB events
            self._ib_loop.run_forever()

        except Exception as e:
            logger.error(f"Failed to connect to TWS: {e}")
            self.connected = False
            self._result_queue.put(False)

    def connect(self) -> bool:
        """
        Connect to TWS/IB Gateway

        Returns:
            bool: True if connection successful
        """
        logger.info(f"Connecting to TWS at {self.host}:{self.port} (client_id={self.client_id})")

        # Start IB thread
        self._ib_thread = threading.Thread(target=self._ib_thread_worker, daemon=True)
        self._ib_thread.start()

        # Wait for connection result
        try:
            result = self._result_queue.get(timeout=30)
            return result
        except:
            logger.error("Connection timeout")
            return False

    def disconnect(self):
        """Disconnect from TWS"""
        try:
            if self.connected:
                # Cancel all subscriptions with the matching cancel call: CMDTY is
                # a reqMktData tick stream (cancelMktData); stocks/futures are
                # reqRealTimeBars (cancelRealTimeBars). Using the wrong one raises
                # "'Commodity' object has no attribute 'reqId'".
                for symbol, contract in self.subscriptions.items():
                    try:
                        if getattr(contract, 'secType', None) == 'CMDTY':
                            self.ib.cancelMktData(contract)
                        else:
                            self.ib.cancelRealTimeBars(contract)
                    except Exception as e:
                        logger.warning(f"Error canceling subscription for {symbol}: {e}")

                self.ib.disconnect()
                self.connected = False
                logger.info("✓ Disconnected from TWS")
        except Exception as e:
            logger.error(f"Error disconnecting from TWS: {e}")

    def _subscribe_bars_sync(
        self,
        symbol: str,
        exchange: str,
        sec_type: str,
        currency: str,
        bar_size: int,
        what_to_show: str
    ) -> bool:
        """Internal synchronous method for subscribing to bars"""
        try:
            # Create contract
            if sec_type == 'STK':
                contract = Stock(symbol, exchange, currency)
            elif sec_type == 'FUT':
                contract = Future(symbol, exchange, currency=currency)
            elif sec_type == 'CMDTY':
                contract = Commodity(symbol, exchange, currency)
            else:
                logger.error(f"Unsupported security type: {sec_type}")
                return False

            # Qualify contract (get full contract details)
            # ib_insync handles async internally
            qualified_contracts = self.ib.qualifyContracts(contract)
            if not qualified_contracts:
                logger.error(f"Could not qualify contract for {symbol}")
                return False

            contract = qualified_contracts[0]

            # For commodities, use ticker updates with pendingTickersEvent
            # (reqRealTimeBars doesn't support CMDTY)
            if sec_type == 'CMDTY':
                logger.info(f"Using tick-by-tick data for commodity {symbol}")
                # Request STREAMING market data (not snapshot)
                ticker = self.ib.reqMktData(contract, genericTickList='', snapshot=False)

                # Initialize bar builder for this symbol
                self.current_bars[symbol] = {
                    'start_time': None,
                    'ticks': []
                }
                self.last_update_time[symbol] = None

                # Store subscription
                self.subscriptions[symbol] = contract
                logger.info(f"✓ Subscribed to {symbol} ({sec_type}) tick stream -> building {self.bar_interval}s bars")
                return True

            # For stocks and futures, use real-time bars
            bars = self.ib.reqRealTimeBars(
                contract,
                barSize=bar_size,
                whatToShow=what_to_show,
                useRTH=False  # Include extended trading hours
            )

            # Register update handler
            bars.updateEvent += self._on_bar_update

            # Store subscription
            self.subscriptions[symbol] = contract

            logger.info(f"✓ Subscribed to {symbol} ({sec_type}) {bar_size}s bars")
            return True

        except Exception as e:
            logger.error(f"Error subscribing to {symbol}: {e}")
            return False

    def subscribe_bars(
        self,
        symbol: str,
        exchange: str = 'SMART',
        sec_type: str = 'STK',
        currency: str = 'USD',
        bar_size: int = 5,
        what_to_show: str = 'TRADES'
    ) -> bool:
        """
        Subscribe to real-time bars for a symbol
        """
        if not self.connected:
            logger.error("Not connected to TWS. Call connect() first.")
            return False

        if not self._ib_loop:
            logger.error("IB event loop not running")
            return False

        # Run subscribe in IB thread
        future = asyncio.run_coroutine_threadsafe(
            self._subscribe_bars_async(symbol, exchange, sec_type, currency, bar_size, what_to_show),
            self._ib_loop
        )

        try:
            return future.result(timeout=15)
        except Exception as e:
            logger.error(f"Subscribe timeout: {e}")
            return False

    async def _subscribe_bars_async(self, symbol, exchange, sec_type, currency, bar_size, what_to_show):
        """Async version of subscribe for running in IB thread"""
        try:
            # Create contract
            if sec_type == 'STK':
                contract = Stock(symbol, exchange, currency)
            elif sec_type == 'FUT':
                contract = Future(symbol, exchange, currency=currency)
            elif sec_type == 'CMDTY':
                contract = Commodity(symbol, exchange, currency)
            elif sec_type == 'CFD':
                contract = CFD(symbol, exchange, currency)
            else:
                logger.error(f"Unsupported security type: {sec_type}")
                return False

            # Qualify contract using async version
            qualified_contracts = await self.ib.qualifyContractsAsync(contract)
            if not qualified_contracts:
                logger.error(f"Could not qualify contract for {symbol}")
                return False

            contract = qualified_contracts[0]

            # For commodities and CFDs, use ticker updates (reqRealTimeBars doesn't support them)
            if sec_type in ('CMDTY', 'CFD'):
                logger.info(f"Using tick-by-tick data for {sec_type} {symbol}")
                self.ib.reqMktData(contract, genericTickList='', snapshot=False)

                self.current_bars[symbol] = {'start_time': None, 'ticks': []}
                self.last_update_time[symbol] = None
                self.subscriptions[symbol] = contract
                logger.info(f"✓ Subscribed to {symbol} ({sec_type}) tick stream")
                return True

            # For stocks/futures use real-time bars
            bars = self.ib.reqRealTimeBars(contract, barSize=bar_size, whatToShow=what_to_show, useRTH=False)
            bars.updateEvent += self._on_bar_update
            self.subscriptions[symbol] = contract
            logger.info(f"✓ Subscribed to {symbol} ({sec_type}) {bar_size}s bars")
            return True

        except Exception as e:
            logger.error(f"Error subscribing to {symbol}: {e}")
            return False

    def unsubscribe_bars(self, symbol: str) -> bool:
        """
        Unsubscribe from real-time bars

        Args:
            symbol: Symbol to unsubscribe

        Returns:
            bool: True if unsubscription successful
        """
        if symbol not in self.subscriptions:
            logger.warning(f"No subscription found for {symbol}")
            return False

        try:
            contract = self.subscriptions[symbol]
            if getattr(contract, 'secType', None) == 'CMDTY':
                self.ib.cancelMktData(contract)   # CMDTY is a reqMktData tick stream
            else:
                self.ib.cancelRealTimeBars(contract)
            del self.subscriptions[symbol]

            logger.info(f"✓ Unsubscribed from {symbol}")
            return True

        except Exception as e:
            logger.error(f"Error unsubscribing from {symbol}: {e}")
            return False

    def on_bar_update(self, callback: Callable):
        """
        Register a callback for bar updates

        Args:
            callback: Function(symbol, bar_data) to call on each bar update
        """
        self.bar_callbacks.append(callback)

    def _get_bar_start_time(self, timestamp):
        """Round down to nearest bar interval"""
        seconds = timestamp.second
        rounded_seconds = (seconds // self.bar_interval) * self.bar_interval
        return timestamp.replace(second=rounded_seconds, microsecond=0)

    def _create_bar_from_ticks(self, symbol: str, bar_ticks, bar_start_time, is_final=True):
        """Create OHLCV bar from accumulated ticks"""
        if not bar_ticks:
            return None

        prices = [t['price'] for t in bar_ticks]
        volumes = [t['volume'] for t in bar_ticks if t['volume']]

        # Get exchange from subscribed contract
        exchange = 'UNKNOWN'
        if symbol in self.subscriptions:
            contract = self.subscriptions[symbol]
            exchange = contract.exchange if hasattr(contract, 'exchange') else 'UNKNOWN'

        return {
            'symbol': symbol,
            'exchange': exchange,
            'timestamp': bar_start_time.isoformat(),
            'open': float(prices[0]),
            'high': float(max(prices)),
            'low': float(min(prices)),
            'close': float(prices[-1]),
            'volume': int(sum(volumes)) if volumes else 0,
            'average': float(sum(prices) / len(prices)),
            'tick_count': len(bar_ticks),
            'is_final': is_final  # False for live updates, True for completed bars
        }

    def _on_pending_tickers(self, tickers):
        """
        Handle pending ticker updates (event-driven)
        This builds 1-minute bars from ticks for commodities, with live updates every 5 seconds
        """
        try:
            logger.debug(f"pendingTickersEvent fired with {len(tickers)} tickers")
            for ticker in tickers:
                # Find which symbol this ticker belongs to
                symbol = None
                for sym, contract in self.subscriptions.items():
                    if hasattr(ticker, 'contract') and ticker.contract.conId == contract.conId:
                        symbol = sym
                        break

                if not symbol or symbol not in self.current_bars:
                    continue

                # For commodities, use bid/ask midpoint if available, otherwise use close
                # This ensures we get live prices instead of stale close prices
                # IBKR sends -1 (or 0) for "no quote", e.g. while a data farm
                # reconnects; a -1 must never become a price in a bar.
                def _px(x):
                    return x is not None and x == x and x > 0

                price = None
                if _px(ticker.bid) and _px(ticker.ask):
                    # Use midpoint of bid/ask for live price
                    price = (float(ticker.bid) + float(ticker.ask)) / 2.0
                elif _px(ticker.close):
                    # Fallback to close price
                    price = float(ticker.close)

                if price is None:
                    continue

                tick_data = {
                    'time': ticker.time if ticker.time else datetime.now(),
                    'price': price,
                    'volume': int(ticker.volume) if ticker.volume and ticker.volume == ticker.volume else 0
                }

                # Determine which bar this tick belongs to
                bar_start = self._get_bar_start_time(tick_data['time'])

                # Initialize current bar if needed
                if self.current_bars[symbol]['start_time'] is None:
                    self.current_bars[symbol]['start_time'] = bar_start
                    self.current_bars[symbol]['ticks'] = []

                # Check if we've moved to a new bar interval
                if bar_start > self.current_bars[symbol]['start_time']:
                    # Finalize the previous bar (is_final=True)
                    completed_bar = self._create_bar_from_ticks(
                        symbol,
                        self.current_bars[symbol]['ticks'],
                        self.current_bars[symbol]['start_time'],
                        is_final=True
                    )

                    if completed_bar:
                        # Notify all registered callbacks with the completed bar
                        for callback in self.bar_callbacks:
                            try:
                                callback(symbol, completed_bar)
                            except Exception as e:
                                logger.error(f"Error in bar callback: {e}")

                    # Start new bar
                    self.current_bars[symbol]['start_time'] = bar_start
                    self.current_bars[symbol]['ticks'] = [tick_data]
                    self.last_update_time[symbol] = tick_data['time']
                else:
                    # Add tick to current bar
                    self.current_bars[symbol]['ticks'].append(tick_data)

                    # Check if we should send a live update (every ~5 seconds)
                    current_time = tick_data['time']
                    last_update = self.last_update_time.get(symbol)

                    if last_update is None or (current_time - last_update).total_seconds() >= self.update_interval:
                        # Send live update with current bar state (is_final=False)
                        live_bar = self._create_bar_from_ticks(
                            symbol,
                            self.current_bars[symbol]['ticks'],
                            self.current_bars[symbol]['start_time'],
                            is_final=False
                        )

                        if live_bar:
                            # Notify all registered callbacks with the live update
                            for callback in self.bar_callbacks:
                                try:
                                    callback(symbol, live_bar)
                                except Exception as e:
                                    logger.error(f"Error in bar callback: {e}")

                        self.last_update_time[symbol] = current_time

        except Exception as e:
            logger.error(f"Error in pending tickers handler: {e}")

    def _on_bar_update(self, bars, hasNewBar):
        """
        Internal handler for bar updates from ib_insync

        Args:
            bars: RealTimeBarList object
            hasNewBar: Boolean indicating if there's a new bar
        """
        if not hasNewBar:
            return

        try:
            bar = bars[-1]  # Get latest bar

            # Find symbol for this bar
            symbol = None
            for sym, contract in self.subscriptions.items():
                if contract.conId == bar.contract.conId:
                    symbol = sym
                    break

            if not symbol:
                logger.warning("Received bar for unknown symbol")
                return

            # Convert to standard format
            bar_data = {
                'symbol': symbol,
                'timestamp': bar.time.isoformat(),
                'open': float(bar.open_),
                'high': float(bar.high),
                'low': float(bar.low),
                'close': float(bar.close),
                'volume': int(bar.volume),
                'wap': float(bar.wap),  # Weighted average price
                'count': int(bar.count)  # Number of trades
            }

            # Call all registered callbacks
            for callback in self.bar_callbacks:
                try:
                    callback(symbol, bar_data)
                except Exception as e:
                    logger.error(f"Error in bar callback: {e}")

        except Exception as e:
            logger.error(f"Error processing bar update: {e}")

    def _on_disconnected(self):
        """Handle disconnection event"""
        logger.warning("Disconnected from TWS")
        self.connected = False

        # Attempt to reconnect
        self._reconnect()

    def _on_error(self, reqId, errorCode, errorString, contract):
        """Handle error events"""
        logger.error(f"TWS Error {errorCode}: {errorString}")

        # Handle specific error codes
        if errorCode == 1100:  # Connectivity lost
            logger.warning("Connectivity between IB and TWS lost")
        elif errorCode == 1101:  # Connectivity restored
            logger.info("Connectivity restored")
        elif errorCode == 1102:  # Connectivity restored (data farm)
            logger.info("Connectivity restored (data farm)")

    def _reconnect(self):
        """Attempt to reconnect with exponential backoff"""
        if self.current_retry >= self.max_retries:
            logger.error(f"Max reconnection attempts ({self.max_retries}) reached")
            return

        self.current_retry += 1
        delay = self.retry_delay * (2 ** (self.current_retry - 1))

        logger.info(f"Reconnecting in {delay} seconds (attempt {self.current_retry}/{self.max_retries})")
        time.sleep(delay)

        if self.connect():
            # Resubscribe to all symbols
            for symbol in list(self.subscriptions.keys()):
                logger.info(f"Resubscribing to {symbol}")
                # Note: You may need to store subscription parameters to resubscribe properly
                # For now, this is a simplified version

    def run_event_loop(self):
        """
        Run the IB event loop

        Call this in a separate thread or use ib.sleep() for async operation
        """
        logger.info("Starting TWS event loop")
        try:
            while self.connected:
                self.ib.sleep(1)
        except KeyboardInterrupt:
            logger.info("Event loop interrupted")
        finally:
            self.disconnect()

    def get_connection_status(self) -> dict:
        """
        Get current connection status

        Returns:
            dict: Connection status information
        """
        return {
            'connected': self.connected,
            'host': self.host,
            'port': self.port,
            'client_id': self.client_id,
            'subscriptions': len(self.subscriptions),
            'symbols': list(self.subscriptions.keys())
        }

    def fetch_historical_bars(
        self,
        symbol: str,
        exchange: str = 'SMART',
        sec_type: str = 'STK',
        currency: str = 'USD',
        duration: str = '1 D',
        bar_size: str = '1 min',
        what_to_show: str = 'MIDPOINT',
        end_datetime: str = ''
    ) -> List[Dict]:
        """
        Fetch historical bars from IB to fill gaps

        Args:
            symbol: Trading symbol
            exchange: Exchange
            sec_type: Security type (STK, CMDTY, FUT)
            currency: Currency
            duration: How far back to look (e.g., '1 D', '2 D', '1 W')
            bar_size: Bar size (e.g., '1 min', '5 mins', '1 hour')
            what_to_show: TRADES, MIDPOINT, BID, ASK
            end_datetime: End datetime (empty for now)

        Returns:
            List of bar dictionaries
        """
        if not self.connected or not self._ib_loop:
            logger.error("Not connected to TWS")
            return []

        future = asyncio.run_coroutine_threadsafe(
            self._fetch_historical_async(symbol, exchange, sec_type, currency, duration, bar_size, what_to_show, end_datetime),
            self._ib_loop
        )

        try:
            return future.result(timeout=60)
        except Exception as e:
            logger.error(f"Historical data fetch timeout: {e}")
            return []

    async def _fetch_historical_async(
        self,
        symbol: str,
        exchange: str,
        sec_type: str,
        currency: str,
        duration: str,
        bar_size: str,
        what_to_show: str,
        end_datetime: str
    ) -> List[Dict]:
        """Async fetch historical bars"""
        try:
            # Create contract
            if sec_type == 'STK':
                contract = Stock(symbol, exchange, currency)
            elif sec_type == 'FUT':
                contract = Future(symbol, exchange, currency=currency)
            elif sec_type == 'CMDTY':
                contract = Commodity(symbol, exchange, currency)
            elif sec_type == 'CFD':
                contract = CFD(symbol, exchange, currency)
            else:
                logger.error(f"Unsupported security type: {sec_type}")
                return []

            # Qualify contract
            qualified_contracts = await self.ib.qualifyContractsAsync(contract)
            if not qualified_contracts:
                logger.error(f"Could not qualify contract for {symbol}")
                return []

            contract = qualified_contracts[0]

            logger.info(f"Fetching historical data for {symbol}: {duration} of {bar_size} bars")

            # Request historical data
            bars = await self.ib.reqHistoricalDataAsync(
                contract,
                endDateTime=end_datetime,
                durationStr=duration,
                barSizeSetting=bar_size,
                whatToShow=what_to_show,
                useRTH=False,
                formatDate=1
            )

            if not bars:
                logger.warning(f"No historical bars returned for {symbol}")
                return []

            # Convert to standard format
            result = []
            for bar in bars:
                result.append({
                    'symbol': symbol,
                    'timestamp': bar.date.isoformat() if hasattr(bar.date, 'isoformat') else str(bar.date),
                    'open': float(bar.open),
                    'high': float(bar.high),
                    'low': float(bar.low),
                    'close': float(bar.close),
                    'volume': int(bar.volume) if bar.volume else 0,
                    'average': float(bar.average) if hasattr(bar, 'average') and bar.average else 0,
                    'bar_count': int(bar.barCount) if hasattr(bar, 'barCount') and bar.barCount else 0
                })

            logger.info(f"Fetched {len(result)} historical bars for {symbol}")
            return result

        except Exception as e:
            logger.error(f"Error fetching historical data for {symbol}: {e}")
            return []
