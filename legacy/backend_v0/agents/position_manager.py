# backend/agents/position_manager.py
"""
Position Manager - Advanced Exit Logic

Handles sophisticated position management including:
1. Trailing stops that follow price
2. Dynamic stop loss based on ATR/volatility
3. Time-based exits (close before weekend)
4. Technical condition-based exits
5. Breakeven stop moves
"""

import logging
from typing import Dict, List, Optional, Callable
from datetime import datetime, time, timedelta
from enum import Enum
import numpy as np

from config.database import get_database

logger = logging.getLogger(__name__)


class ExitReason(Enum):
    """Reasons for position exit"""
    STOP_LOSS = "stop_loss"
    TAKE_PROFIT = "take_profit"
    TRAILING_STOP = "trailing_stop"
    TIME_EXIT = "time_exit"
    WEEKEND_CLOSE = "weekend_close"
    TECHNICAL_EXIT = "technical_exit"
    BREAKEVEN_STOP = "breakeven_stop"
    OPPOSITE_SIGNAL = "opposite_signal"
    MAX_HOLD_TIME = "max_hold_time"
    MANUAL = "manual"


class Position:
    """Represents an open trading position with exit management"""

    def __init__(
        self,
        symbol: str,
        side: str,  # 'BUY' or 'SELL'
        quantity: float,
        entry_price: float,
        entry_time: datetime,
        initial_stop_loss: float,
        initial_take_profit: float,
        order_ids: Optional[Dict] = None
    ):
        self.symbol = symbol
        self.side = side
        self.quantity = quantity
        self.entry_price = entry_price
        self.entry_time = entry_time

        # Stop levels
        self.initial_stop_loss = initial_stop_loss
        self.current_stop_loss = initial_stop_loss
        self.initial_take_profit = initial_take_profit
        self.current_take_profit = initial_take_profit

        # Trailing stop state
        self.trailing_stop_active = False
        self.trailing_stop_distance = abs(entry_price - initial_stop_loss)
        self.highest_price = entry_price if side == 'BUY' else None
        self.lowest_price = entry_price if side == 'SELL' else None

        # Breakeven state
        self.breakeven_triggered = False

        # Order tracking
        self.order_ids = order_ids or {}

        # P&L tracking
        self.unrealized_pnl = 0.0
        self.max_favorable_excursion = 0.0  # Best unrealized profit
        self.max_adverse_excursion = 0.0    # Worst unrealized loss

    def update_price(self, current_price: float) -> Dict:
        """
        Update position with new price, returns any triggered actions

        Args:
            current_price: Current market price

        Returns:
            Dict with 'exit_triggered', 'reason', 'exit_price' if exit needed
        """
        # Calculate current P&L
        if self.side == 'BUY':
            self.unrealized_pnl = (current_price - self.entry_price) * self.quantity
            price_move = current_price - self.entry_price
        else:
            self.unrealized_pnl = (self.entry_price - current_price) * self.quantity
            price_move = self.entry_price - current_price

        # Track excursions
        if self.unrealized_pnl > self.max_favorable_excursion:
            self.max_favorable_excursion = self.unrealized_pnl
        if self.unrealized_pnl < -self.max_adverse_excursion:
            self.max_adverse_excursion = abs(self.unrealized_pnl)

        # Update high/low tracking for trailing stop
        if self.side == 'BUY':
            if self.highest_price is None or current_price > self.highest_price:
                self.highest_price = current_price
        else:
            if self.lowest_price is None or current_price < self.lowest_price:
                self.lowest_price = current_price

        return {'exit_triggered': False}

    def get_status(self) -> Dict:
        """Get position status summary"""
        return {
            'symbol': self.symbol,
            'side': self.side,
            'quantity': self.quantity,
            'entry_price': self.entry_price,
            'entry_time': self.entry_time.isoformat(),
            'current_stop_loss': self.current_stop_loss,
            'current_take_profit': self.current_take_profit,
            'trailing_stop_active': self.trailing_stop_active,
            'breakeven_triggered': self.breakeven_triggered,
            'unrealized_pnl': round(self.unrealized_pnl, 2),
            'max_favorable_excursion': round(self.max_favorable_excursion, 2),
            'hold_time_minutes': (datetime.now() - self.entry_time).total_seconds() / 60
        }


class PositionManager:
    """
    Manages open positions with advanced exit logic

    Features:
    1. Trailing stops - move stop with price
    2. Dynamic stops - adjust based on volatility (ATR)
    3. Time exits - close before weekends, max hold time
    4. Technical exits - exit on indicator signals
    5. Breakeven stops - move stop to breakeven after X profit
    """

    def __init__(self, config: Optional[Dict] = None):
        self.config = config or {}
        self.db = get_database()

        # Open positions
        self.positions: Dict[str, Position] = {}

        # Exit callbacks (for execution)
        self.exit_callbacks: List[Callable] = []

        # ===== TRAILING STOP SETTINGS =====
        self.trailing_stop_enabled = self.config.get('trailing_stop_enabled', True)
        # Activate trailing after this profit (in price points)
        self.trailing_activation_profit = self.config.get('trailing_activation_profit', 20.0)
        # Trail distance (in price points, or use ATR multiplier)
        self.trailing_distance = self.config.get('trailing_distance', 15.0)
        # Use ATR for trailing distance
        self.use_atr_for_trailing = self.config.get('use_atr_for_trailing', True)
        self.trailing_atr_multiplier = self.config.get('trailing_atr_multiplier', 2.0)

        # ===== BREAKEVEN STOP SETTINGS =====
        self.breakeven_enabled = self.config.get('breakeven_enabled', True)
        # Move to breakeven after this profit (in price points)
        self.breakeven_trigger_profit = self.config.get('breakeven_trigger_profit', 15.0)
        # Add buffer above breakeven (in price points)
        self.breakeven_buffer = self.config.get('breakeven_buffer', 2.0)

        # ===== DYNAMIC STOP SETTINGS =====
        self.dynamic_stop_enabled = self.config.get('dynamic_stop_enabled', True)
        # ATR multiplier for initial stop
        self.atr_stop_multiplier = self.config.get('atr_stop_multiplier', 2.5)
        # ATR period
        self.atr_period = self.config.get('atr_period', 14)

        # ===== TIME-BASED EXIT SETTINGS =====
        self.weekend_close_enabled = self.config.get('weekend_close_enabled', True)
        # Close positions on Friday at this time (UTC)
        self.weekend_close_time = time(20, 0)  # 20:00 UTC = 9 PM CET
        # Maximum hold time in hours (0 = disabled)
        self.max_hold_hours = self.config.get('max_hold_hours', 48)

        # ===== TECHNICAL EXIT SETTINGS =====
        self.technical_exit_enabled = self.config.get('technical_exit_enabled', True)
        # Exit if RSI crosses these levels against position
        self.rsi_overbought = self.config.get('rsi_overbought', 75)
        self.rsi_oversold = self.config.get('rsi_oversold', 25)

        logger.info(f"✓ Position Manager initialized")
        logger.info(f"  - Trailing stop: {self.trailing_stop_enabled}")
        logger.info(f"  - Breakeven stop: {self.breakeven_enabled}")
        logger.info(f"  - Dynamic stop (ATR): {self.dynamic_stop_enabled}")
        logger.info(f"  - Weekend close: {self.weekend_close_enabled}")
        logger.info(f"  - Technical exits: {self.technical_exit_enabled}")

    def add_position(
        self,
        symbol: str,
        side: str,
        quantity: float,
        entry_price: float,
        stop_loss: float,
        take_profit: float,
        order_ids: Optional[Dict] = None
    ) -> Position:
        """
        Add a new position to manage

        Args:
            symbol: Trading symbol
            side: 'BUY' or 'SELL'
            quantity: Position size
            entry_price: Entry price
            stop_loss: Initial stop loss
            take_profit: Initial take profit
            order_ids: Dict of order IDs (entry, sl, tp)

        Returns:
            Position object
        """
        # Optionally adjust stop loss based on ATR
        if self.dynamic_stop_enabled:
            atr = self._get_atr(symbol)
            if atr:
                dynamic_sl_distance = atr * self.atr_stop_multiplier
                if side == 'BUY':
                    dynamic_stop = entry_price - dynamic_sl_distance
                    # Use wider of provided SL and ATR-based SL
                    if dynamic_stop < stop_loss:
                        logger.info(f"Using ATR-based stop: ${dynamic_stop:.2f} (ATR={atr:.2f})")
                        stop_loss = dynamic_stop
                else:
                    dynamic_stop = entry_price + dynamic_sl_distance
                    if dynamic_stop > stop_loss:
                        logger.info(f"Using ATR-based stop: ${dynamic_stop:.2f} (ATR={atr:.2f})")
                        stop_loss = dynamic_stop

        position = Position(
            symbol=symbol,
            side=side,
            quantity=quantity,
            entry_price=entry_price,
            entry_time=datetime.now(),
            initial_stop_loss=stop_loss,
            initial_take_profit=take_profit,
            order_ids=order_ids
        )

        self.positions[symbol] = position
        logger.info(f"📊 Position Manager: Added {side} {quantity} {symbol} @ ${entry_price:.2f}")
        logger.info(f"   SL: ${stop_loss:.2f}, TP: ${take_profit:.2f}")

        return position

    def update(self, symbol: str, current_price: float) -> Optional[Dict]:
        """
        Update position and check for exit conditions

        This should be called on every price update (tick/bar)

        Args:
            symbol: Trading symbol
            current_price: Current market price

        Returns:
            Exit signal dict if exit triggered, None otherwise
        """
        if symbol not in self.positions:
            return None

        position = self.positions[symbol]

        # Update position with new price
        position.update_price(current_price)

        # Check exit conditions in order of priority

        # 1. Check stop loss
        exit_signal = self._check_stop_loss(position, current_price)
        if exit_signal:
            return exit_signal

        # 2. Check take profit
        exit_signal = self._check_take_profit(position, current_price)
        if exit_signal:
            return exit_signal

        # 3. Check and update trailing stop
        if self.trailing_stop_enabled:
            exit_signal = self._update_trailing_stop(position, current_price)
            if exit_signal:
                return exit_signal

        # 4. Check breakeven stop
        if self.breakeven_enabled:
            self._check_breakeven(position, current_price)

        # 5. Check time-based exits
        exit_signal = self._check_time_exits(position)
        if exit_signal:
            return exit_signal

        # 6. Check technical exits
        if self.technical_exit_enabled:
            exit_signal = self._check_technical_exits(position, current_price)
            if exit_signal:
                return exit_signal

        return None

    def _check_stop_loss(self, position: Position, current_price: float) -> Optional[Dict]:
        """Check if stop loss is hit"""
        if position.side == 'BUY':
            if current_price <= position.current_stop_loss:
                return self._create_exit_signal(
                    position,
                    ExitReason.TRAILING_STOP if position.trailing_stop_active else ExitReason.STOP_LOSS,
                    current_price
                )
        else:  # SELL
            if current_price >= position.current_stop_loss:
                return self._create_exit_signal(
                    position,
                    ExitReason.TRAILING_STOP if position.trailing_stop_active else ExitReason.STOP_LOSS,
                    current_price
                )
        return None

    def _check_take_profit(self, position: Position, current_price: float) -> Optional[Dict]:
        """Check if take profit is hit"""
        if position.side == 'BUY':
            if current_price >= position.current_take_profit:
                return self._create_exit_signal(position, ExitReason.TAKE_PROFIT, current_price)
        else:  # SELL
            if current_price <= position.current_take_profit:
                return self._create_exit_signal(position, ExitReason.TAKE_PROFIT, current_price)
        return None

    def _update_trailing_stop(self, position: Position, current_price: float) -> Optional[Dict]:
        """
        Update trailing stop based on price movement

        Trailing stop activates after position reaches activation profit,
        then follows price by trailing_distance
        """
        # Calculate current profit in price points
        if position.side == 'BUY':
            profit_points = current_price - position.entry_price

            # Check if we should activate trailing stop
            if not position.trailing_stop_active:
                if profit_points >= self.trailing_activation_profit:
                    position.trailing_stop_active = True
                    logger.info(f"🔄 Trailing stop ACTIVATED for {position.symbol} (profit: ${profit_points:.2f})")

            # If trailing stop is active, update it
            if position.trailing_stop_active:
                # Get trailing distance (use ATR if enabled)
                trail_distance = self._get_trail_distance(position.symbol)

                # Calculate new stop based on highest price
                if position.highest_price:
                    new_stop = position.highest_price - trail_distance

                    # Only move stop up, never down
                    if new_stop > position.current_stop_loss:
                        old_stop = position.current_stop_loss
                        position.current_stop_loss = new_stop
                        logger.info(f"📈 Trailing stop updated: ${old_stop:.2f} → ${new_stop:.2f}")

                        # Notify to update broker order
                        self._notify_stop_update(position)

        else:  # SELL position
            profit_points = position.entry_price - current_price

            if not position.trailing_stop_active:
                if profit_points >= self.trailing_activation_profit:
                    position.trailing_stop_active = True
                    logger.info(f"🔄 Trailing stop ACTIVATED for {position.symbol} (profit: ${profit_points:.2f})")

            if position.trailing_stop_active:
                trail_distance = self._get_trail_distance(position.symbol)

                if position.lowest_price:
                    new_stop = position.lowest_price + trail_distance

                    # Only move stop down, never up
                    if new_stop < position.current_stop_loss:
                        old_stop = position.current_stop_loss
                        position.current_stop_loss = new_stop
                        logger.info(f"📉 Trailing stop updated: ${old_stop:.2f} → ${new_stop:.2f}")
                        self._notify_stop_update(position)

        return None

    def _check_breakeven(self, position: Position, current_price: float):
        """
        Move stop to breakeven after reaching trigger profit
        """
        if position.breakeven_triggered:
            return

        if position.side == 'BUY':
            profit_points = current_price - position.entry_price
            if profit_points >= self.breakeven_trigger_profit:
                # Move stop to breakeven + buffer
                new_stop = position.entry_price + self.breakeven_buffer
                if new_stop > position.current_stop_loss:
                    old_stop = position.current_stop_loss
                    position.current_stop_loss = new_stop
                    position.breakeven_triggered = True
                    logger.info(f"🔒 Breakeven stop set: ${old_stop:.2f} → ${new_stop:.2f}")
                    self._notify_stop_update(position)
        else:  # SELL
            profit_points = position.entry_price - current_price
            if profit_points >= self.breakeven_trigger_profit:
                new_stop = position.entry_price - self.breakeven_buffer
                if new_stop < position.current_stop_loss:
                    old_stop = position.current_stop_loss
                    position.current_stop_loss = new_stop
                    position.breakeven_triggered = True
                    logger.info(f"🔒 Breakeven stop set: ${old_stop:.2f} → ${new_stop:.2f}")
                    self._notify_stop_update(position)

    def _check_time_exits(self, position: Position) -> Optional[Dict]:
        """
        Check time-based exit conditions

        1. Weekend close (Friday evening)
        2. Maximum hold time exceeded
        """
        now = datetime.now()

        # Weekend close check
        if self.weekend_close_enabled:
            # Friday = 4 (Monday = 0)
            if now.weekday() == 4:  # Friday
                if now.time() >= self.weekend_close_time:
                    logger.info(f"⏰ Weekend close triggered for {position.symbol}")
                    return self._create_exit_signal(
                        position,
                        ExitReason.WEEKEND_CLOSE,
                        None  # Will get current price at execution
                    )

        # Max hold time check
        if self.max_hold_hours > 0:
            hold_time = now - position.entry_time
            if hold_time > timedelta(hours=self.max_hold_hours):
                logger.info(f"⏰ Max hold time exceeded for {position.symbol}")
                return self._create_exit_signal(
                    position,
                    ExitReason.MAX_HOLD_TIME,
                    None
                )

        return None

    def _check_technical_exits(self, position: Position, current_price: float) -> Optional[Dict]:
        """
        Check technical indicator-based exits

        Exit if indicators suggest reversal against position
        """
        try:
            # Get recent RSI
            rsi = self._get_rsi(position.symbol)

            if rsi is not None:
                if position.side == 'BUY':
                    # Exit long if RSI is overbought (potential reversal down)
                    if rsi >= self.rsi_overbought:
                        logger.info(f"📊 Technical exit: RSI overbought ({rsi:.1f}) for {position.symbol}")
                        return self._create_exit_signal(
                            position,
                            ExitReason.TECHNICAL_EXIT,
                            current_price
                        )
                else:  # SELL
                    # Exit short if RSI is oversold (potential reversal up)
                    if rsi <= self.rsi_oversold:
                        logger.info(f"📊 Technical exit: RSI oversold ({rsi:.1f}) for {position.symbol}")
                        return self._create_exit_signal(
                            position,
                            ExitReason.TECHNICAL_EXIT,
                            current_price
                        )

        except Exception as e:
            logger.error(f"Error checking technical exits: {e}")

        return None

    def _create_exit_signal(
        self,
        position: Position,
        reason: ExitReason,
        exit_price: Optional[float]
    ) -> Dict:
        """Create exit signal for execution"""
        # Calculate P&L
        if exit_price:
            if position.side == 'BUY':
                pnl = (exit_price - position.entry_price) * position.quantity
            else:
                pnl = (position.entry_price - exit_price) * position.quantity
        else:
            pnl = position.unrealized_pnl

        return {
            'exit_triggered': True,
            'symbol': position.symbol,
            'side': position.side,
            'quantity': position.quantity,
            'entry_price': position.entry_price,
            'exit_price': exit_price,
            'reason': reason.value,
            'pnl': round(pnl, 2),
            'hold_time_minutes': (datetime.now() - position.entry_time).total_seconds() / 60,
            'max_favorable_excursion': position.max_favorable_excursion,
            'max_adverse_excursion': position.max_adverse_excursion,
            'order_ids': position.order_ids
        }

    def _get_trail_distance(self, symbol: str) -> float:
        """Get trailing stop distance, optionally based on ATR"""
        if self.use_atr_for_trailing:
            atr = self._get_atr(symbol)
            if atr:
                return atr * self.trailing_atr_multiplier
        return self.trailing_distance

    def _get_atr(self, symbol: str, period: int = None) -> Optional[float]:
        """
        Calculate Average True Range from database

        ATR = Average of True Range over N periods
        True Range = max(high-low, abs(high-prev_close), abs(low-prev_close))
        """
        period = period or self.atr_period

        try:
            with self.db.get_cursor() as cursor:
                cursor.execute("""
                    SELECT high, low, close
                    FROM ohlcv_realtime_1min
                    WHERE symbol = %s
                    ORDER BY timestamp DESC
                    LIMIT %s
                """, (symbol, period + 1))

                rows = cursor.fetchall()

                if len(rows) < period + 1:
                    return None

                true_ranges = []
                for i in range(len(rows) - 1):
                    high = float(rows[i]['high'])
                    low = float(rows[i]['low'])
                    prev_close = float(rows[i + 1]['close'])

                    tr = max(
                        high - low,
                        abs(high - prev_close),
                        abs(low - prev_close)
                    )
                    true_ranges.append(tr)

                return np.mean(true_ranges)

        except Exception as e:
            logger.error(f"Error calculating ATR: {e}")
            return None

    def _get_rsi(self, symbol: str, period: int = 14) -> Optional[float]:
        """Calculate RSI from database"""
        try:
            with self.db.get_cursor() as cursor:
                cursor.execute("""
                    SELECT close
                    FROM ohlcv_realtime_1min
                    WHERE symbol = %s
                    ORDER BY timestamp DESC
                    LIMIT %s
                """, (symbol, period + 1))

                rows = cursor.fetchall()

                if len(rows) < period + 1:
                    return None

                closes = [float(row['close']) for row in reversed(rows)]

                gains = []
                losses = []

                for i in range(1, len(closes)):
                    change = closes[i] - closes[i - 1]
                    if change > 0:
                        gains.append(change)
                        losses.append(0)
                    else:
                        gains.append(0)
                        losses.append(abs(change))

                avg_gain = np.mean(gains)
                avg_loss = np.mean(losses)

                if avg_loss == 0:
                    return 100

                rs = avg_gain / avg_loss
                rsi = 100 - (100 / (1 + rs))

                return rsi

        except Exception as e:
            logger.error(f"Error calculating RSI: {e}")
            return None

    def _notify_stop_update(self, position: Position):
        """Notify execution agent to update stop loss order"""
        for callback in self.exit_callbacks:
            try:
                callback({
                    'action': 'update_stop',
                    'symbol': position.symbol,
                    'new_stop': position.current_stop_loss,
                    'order_id': position.order_ids.get('stop_loss')
                })
            except Exception as e:
                logger.error(f"Error in stop update callback: {e}")

    def register_exit_callback(self, callback: Callable):
        """Register callback for exit signals"""
        self.exit_callbacks.append(callback)

    def remove_position(self, symbol: str):
        """Remove position after exit"""
        if symbol in self.positions:
            del self.positions[symbol]
            logger.info(f"📊 Position Manager: Removed {symbol}")

    def get_all_positions(self) -> Dict:
        """Get status of all positions"""
        return {
            symbol: pos.get_status()
            for symbol, pos in self.positions.items()
        }

    def get_config(self) -> Dict:
        """Get current configuration"""
        return {
            'trailing_stop': {
                'enabled': self.trailing_stop_enabled,
                'activation_profit': self.trailing_activation_profit,
                'distance': self.trailing_distance,
                'use_atr': self.use_atr_for_trailing,
                'atr_multiplier': self.trailing_atr_multiplier
            },
            'breakeven': {
                'enabled': self.breakeven_enabled,
                'trigger_profit': self.breakeven_trigger_profit,
                'buffer': self.breakeven_buffer
            },
            'dynamic_stop': {
                'enabled': self.dynamic_stop_enabled,
                'atr_multiplier': self.atr_stop_multiplier,
                'atr_period': self.atr_period
            },
            'time_exits': {
                'weekend_close': self.weekend_close_enabled,
                'weekend_close_time': str(self.weekend_close_time),
                'max_hold_hours': self.max_hold_hours
            },
            'technical_exits': {
                'enabled': self.technical_exit_enabled,
                'rsi_overbought': self.rsi_overbought,
                'rsi_oversold': self.rsi_oversold
            }
        }
