# backend/services/trigger_monitor.py
"""
Trigger Monitor Service

Event-driven trigger detection for Scanner Agent
Runs every second to detect events that should trigger a scan

Triggers:
1. Candle close (on 1m, 5m, 15m, 1h, 4h)
2. Price move (±0.3% from last scan)
3. Volume spike (>2× 20-bar average)
4. Volatility change (ATR change >20%)
5. Sentiment event (future: news alerts)
6. Heartbeat (fallback: every 5 minutes)
"""

import logging
from typing import Dict, Optional, List
from datetime import datetime, timedelta
from config.database import get_database

logger = logging.getLogger(__name__)


class TriggerMonitor:
    """
    Real-time trigger detection for event-driven scanning

    Responsibilities:
    - Detect candle closes across all timeframes
    - Monitor price movements
    - Detect volume spikes
    - Track volatility changes
    - Provide heartbeat fallback
    """

    # Trigger thresholds
    PRICE_MOVE_THRESHOLD_PCT = 0.3  # 0.3% price move
    VOLUME_SPIKE_MULTIPLIER = 2.0   # 2× average volume
    VOLATILITY_CHANGE_THRESHOLD_PCT = 20  # 20% ATR change
    HEARTBEAT_INTERVAL_SECONDS = 300  # 5 minutes

    # Timeframes to monitor for candle closes
    MONITORED_TIMEFRAMES = ['1m', '5m', '15m', '1h', '4h']

    def __init__(self, provider=None):
        self.db = get_database()
        # Resamples timeframes from the ohlcv_1min view (replaces old direct
        # queries to the non-existent `candlesticks` table).
        from services.market_data_provider import LiveDataProvider
        self.provider = provider or LiveDataProvider(db=self.db)

        # Last scan state (per symbol)
        self.last_scan_state: Dict[str, Dict] = {}

        # Last candle timestamps (per symbol, per timeframe)
        self.last_candle_timestamps: Dict[str, Dict[str, datetime]] = {}

        # Last heartbeat time
        self.last_heartbeat: Optional[datetime] = None

        logger.info("✓ Trigger Monitor initialized")

    def check_triggers(self, symbol: str) -> Dict:
        """
        Check all triggers for a symbol

        Args:
            symbol: Trading symbol (e.g., 'XAUUSD')

        Returns:
            Dict with trigger results:
            {
                'triggered': True/False,
                'triggers': ['candle_close_5m', 'price_move'],
                'reason': 'Candle close on 5m, price moved +0.35%',
                'priority': 8,
                'metadata': {...}
            }
        """
        try:
            # Initialize state for symbol if needed
            if symbol not in self.last_scan_state:
                self._initialize_symbol_state(symbol)

            triggers = []
            metadata = {}

            # 1. Check candle closes
            candle_triggers = self._check_candle_closes(symbol)
            if candle_triggers:
                triggers.extend(candle_triggers)
                metadata['candle_closes'] = candle_triggers

            # 2. Check price movements
            price_trigger = self._check_price_move(symbol)
            if price_trigger:
                triggers.append(price_trigger['type'])
                metadata['price_move'] = price_trigger

            # 3. Check volume spikes
            volume_trigger = self._check_volume_spike(symbol)
            if volume_trigger:
                triggers.append(volume_trigger['type'])
                metadata['volume_spike'] = volume_trigger

            # 4. Check volatility changes
            volatility_trigger = self._check_volatility_change(symbol)
            if volatility_trigger:
                triggers.append(volatility_trigger['type'])
                metadata['volatility_change'] = volatility_trigger

            # 5. Check heartbeat fallback
            heartbeat_trigger = self._check_heartbeat()
            if heartbeat_trigger:
                triggers.append('heartbeat')
                metadata['heartbeat'] = heartbeat_trigger

            # Determine priority (higher for more significant triggers)
            priority = self._calculate_priority(triggers)

            # Build reason string
            reason = self._build_reason_string(triggers, metadata)

            return {
                'triggered': len(triggers) > 0,
                'triggers': triggers,
                'reason': reason,
                'priority': priority,
                'metadata': metadata,
                'timestamp': datetime.utcnow().isoformat()
            }

        except Exception as e:
            logger.error(f"Error checking triggers for {symbol}: {e}")
            return {
                'triggered': False,
                'triggers': [],
                'reason': f'Error: {str(e)}',
                'priority': 0,
                'metadata': {}
            }

    def _initialize_symbol_state(self, symbol: str):
        """Initialize tracking state for a symbol"""
        self.last_scan_state[symbol] = {
            'last_price': None,
            'last_volume': None,
            'last_atr': None,
            'last_scan_time': None
        }

        self.last_candle_timestamps[symbol] = {tf: None for tf in self.MONITORED_TIMEFRAMES}

        logger.debug(f"Initialized state for {symbol}")

    def _check_candle_closes(self, symbol: str) -> List[str]:
        """
        Check if any new candles have closed since last check

        Returns:
            List of candle close triggers (e.g., ['candle_close_5m', 'candle_close_15m'])
        """
        triggers = []

        try:
            tf_data = self.provider.get_timeframe_data(
                symbol, timeframes=self.MONITORED_TIMEFRAMES, lookback=1
            )
            for tf in self.MONITORED_TIMEFRAMES:
                df = tf_data.get(tf)
                if df is None or df.empty:
                    continue
                latest_timestamp = df['Timestamp'].iloc[-1]
                last_known = self.last_candle_timestamps[symbol][tf]

                # New candle if timestamp changed
                if last_known is None or latest_timestamp > last_known:
                    triggers.append(f'candle_close_{tf}')
                    self.last_candle_timestamps[symbol][tf] = latest_timestamp
                    logger.debug(f"✓ Candle close detected: {symbol} {tf} at {latest_timestamp}")

        except Exception as e:
            logger.error(f"Error checking candle closes for {symbol}: {e}")

        return triggers

    def _check_price_move(self, symbol: str) -> Optional[Dict]:
        """
        Check if price has moved significantly since last scan

        Returns:
            Dict with price move info or None
        """
        try:
            # Current price (latest 1m close) via the provider
            current_price = self.provider.get_price(symbol)
            if current_price is not None:
                last_price = self.last_scan_state[symbol]['last_price']

                if last_price:
                    price_change_pct = abs((current_price - last_price) / last_price * 100)

                    if price_change_pct >= self.PRICE_MOVE_THRESHOLD_PCT:
                        direction = 'up' if current_price > last_price else 'down'

                        # Update last price
                        self.last_scan_state[symbol]['last_price'] = current_price

                        return {
                            'type': 'price_move',
                            'direction': direction,
                            'change_pct': round(price_change_pct, 2),
                            'current_price': current_price,
                            'previous_price': last_price
                        }
                else:
                    # First scan, just store price
                    self.last_scan_state[symbol]['last_price'] = current_price

        except Exception as e:
            logger.error(f"Error checking price move for {symbol}: {e}")

        return None

    def _check_volume_spike(self, symbol: str) -> Optional[Dict]:
        """
        Check if volume has spiked (>2× 20-bar average)

        Returns:
            Dict with volume spike info or None
        """
        try:
            # Last 21 1m candles (current + 20 for the average) via the provider
            tf = self.provider.get_timeframe_data(symbol, timeframes=['1m'], lookback=21)
            df = tf.get('1m')

            if df is not None and len(df) >= 21:
                vols = [float(v) for v in df['Volume']]
                current_volume = vols[-1]              # provider is oldest-first
                avg_volume = sum(vols[-21:-1]) / 20

                if avg_volume > 0:
                    volume_ratio = current_volume / avg_volume

                    if volume_ratio >= self.VOLUME_SPIKE_MULTIPLIER:
                        return {
                            'type': 'volume_spike',
                            'volume_ratio': round(volume_ratio, 2),
                            'current_volume': int(current_volume),
                            'avg_volume': int(avg_volume)
                        }

        except Exception as e:
            logger.error(f"Error checking volume spike for {symbol}: {e}")

        return None

    def _check_volatility_change(self, symbol: str) -> Optional[Dict]:
        """
        Check if volatility (ATR) has changed significantly (>20%)

        Returns:
            Dict with volatility change info or None
        """
        try:
            # Get current ATR from technical indicators
            # This requires calculating ATR from recent candles
            # For now, return None (placeholder for future implementation)
            # TODO: Calculate ATR from last 14 candles and compare to stored value
            pass

        except Exception as e:
            logger.error(f"Error checking volatility for {symbol}: {e}")

        return None

    def _check_heartbeat(self) -> Optional[Dict]:
        """
        Check if heartbeat fallback should trigger

        Returns:
            Dict with heartbeat info or None
        """
        now = datetime.utcnow()

        if self.last_heartbeat is None:
            self.last_heartbeat = now
            return None

        elapsed = (now - self.last_heartbeat).total_seconds()

        if elapsed >= self.HEARTBEAT_INTERVAL_SECONDS:
            self.last_heartbeat = now
            return {
                'elapsed_seconds': int(elapsed),
                'reason': 'Heartbeat fallback (no other triggers)'
            }

        return None

    def _calculate_priority(self, triggers: List[str]) -> int:
        """
        Calculate priority based on triggers

        Higher priority for more significant triggers

        Returns:
            Priority 1-10 (10 = highest)
        """
        if not triggers:
            return 0

        # Priority weights
        weights = {
            'price_move': 9,
            'volume_spike': 8,
            'candle_close_15m': 7,
            'candle_close_5m': 6,
            'candle_close_1h': 8,
            'candle_close_4h': 9,
            'volatility_change': 7,
            'candle_close_1m': 4,
            'heartbeat': 2
        }

        # Return max priority
        priorities = [weights.get(t, 5) for t in triggers]
        return max(priorities)

    def _build_reason_string(self, triggers: List[str], metadata: Dict) -> str:
        """
        Build human-readable reason string from triggers

        Returns:
            Reason string (e.g., "Candle close on 5m, price moved +0.35%")
        """
        if not triggers:
            return "No triggers"

        parts = []

        # Candle closes
        candle_closes = [t for t in triggers if t.startswith('candle_close_')]
        if candle_closes:
            timeframes = [t.replace('candle_close_', '') for t in candle_closes]
            parts.append(f"Candle close on {', '.join(timeframes)}")

        # Price move
        if 'price_move' in triggers and 'price_move' in metadata:
            pm = metadata['price_move']
            direction_symbol = '+' if pm['direction'] == 'up' else '-'
            parts.append(f"Price moved {direction_symbol}{pm['change_pct']:.2f}%")

        # Volume spike
        if 'volume_spike' in triggers and 'volume_spike' in metadata:
            vs = metadata['volume_spike']
            parts.append(f"Volume spike {vs['volume_ratio']:.1f}×")

        # Volatility
        if 'volatility_change' in triggers:
            parts.append("Volatility change")

        # Heartbeat
        if 'heartbeat' in triggers:
            parts.append("Heartbeat")

        return ', '.join(parts) if parts else "Multiple triggers"


# Singleton instance
_trigger_monitor: Optional[TriggerMonitor] = None


def get_trigger_monitor() -> TriggerMonitor:
    """
    Get singleton instance of TriggerMonitor

    Returns:
        TriggerMonitor instance
    """
    global _trigger_monitor
    if _trigger_monitor is None:
        _trigger_monitor = TriggerMonitor()
    return _trigger_monitor
