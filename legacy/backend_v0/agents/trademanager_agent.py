# backend/agents/trade_manager_agent.py
"""
Trade Manager Agent

Real-time monitoring during active trades
Executes hard exits immediately or flags soft triggers to Meta-Agent

Agency Score: 0.3/10 (Very Low Agency - minimal governance needed)
"""

import logging
from typing import Dict, List, Optional
from enum import Enum

from agents.base_agent import BaseAgent, AgentType, MessageType, Message
from services.technical_indicators import get_technical_indicators
from services.market_data_provider import MarketDataProvider, LiveDataProvider
from services.clock import Clock, LiveClock
from config.database import get_database

logger = logging.getLogger(__name__)


class ExitType(Enum):
    """Types of exits"""
    HARD_EXIT = "hard_exit"  # Immediate, no Meta-Agent
    SOFT_TRIGGER = "soft_trigger"  # Flag to Meta-Agent for decision


class HardExitReason(Enum):
    """Reasons for immediate hard exit"""
    STOP_LOSS_HIT = "stop_loss_hit"
    TAKE_PROFIT_HIT = "take_profit_hit"
    TIME_LIMIT_EXCEEDED = "time_limit_exceeded"  # >40min
    EXTREME_REVERSAL = "extreme_reversal"  # 2×ATR against position
    DAILY_LOSS_LIMIT = "daily_loss_limit"  # -6% daily loss


class SoftTriggerReason(Enum):
    """Reasons for flagging to Meta-Agent"""
    # Deterioration
    PROFIT_REVERSAL = "profit_reversal"  # >0.5×ATR from max profit
    RSI_FLIP = "rsi_flip"
    MA_CROSS_AGAINST = "ma_cross_against"
    VOLUME_DRIED_UP = "volume_dried_up"
    SPREAD_WIDENED = "spread_widened"  # >$0.50

    # Profit milestones
    PROFIT_1X_ATR = "profit_1x_atr"  # Breakeven SL?
    PROFIT_1_5X_ATR = "profit_1_5x_atr"  # Partial exit?
    PROFIT_2_5X_ATR = "profit_2_5x_atr"  # Take profit?

    # External
    BREAKING_NEWS = "breaking_news"
    VOLATILITY_SPIKE = "volatility_spike"  # >30%

    # Pattern check
    PATTERN_INVALIDATED = "pattern_invalidated"  # Every 10min


class TradeManagerAgent(BaseAgent):
    """
    Trade Manager Agent - Real-time trade monitoring

    Pure rule-based, no LLM
    Cost: $0
    Latency: <10ms
    Frequency: Every second during active trade

    Responsibilities:
    1. Monitor active trades every second
    2. Execute hard exits immediately (SL/TP/time/extreme reversal)
    3. Flag soft triggers to Meta-Agent
    4. Track trade metrics (P&L, max profit, max adverse excursion)
    """

    def __init__(
        self,
        config: Optional[Dict] = None,
        provider: Optional[MarketDataProvider] = None,
        clock: Optional[Clock] = None,
        account_state_fn=None,
    ):
        super().__init__(AgentType.TRADE_MANAGER, config)

        self.indicators = get_technical_indicators()

        # Injected time + price sources. Defaults are the live implementations,
        # so production behaviour is unchanged. Backtests pass a SimulatedClock
        # and a HistoricalDataProvider to replay the whole trade lifecycle — and
        # never touch the database (db stays None).
        self.clock = clock or LiveClock()
        if provider is None:
            self.db = get_database()
            provider = LiveDataProvider(db=self.db)
        else:
            self.db = None
        self.provider = provider
        # Returns account state dict with at least 'daily_pnl_pct'. Live default
        # reads the AccountStateManager; backtest injects a simulated getter.
        self.account_state_fn = account_state_fn or self._live_account_state

        # Hard exit parameters
        self.max_trade_time_minutes = config.get('max_trade_time_minutes', 40) if config else 40
        self.extreme_reversal_multiplier = config.get('extreme_reversal_multiplier', 2.0) if config else 2.0
        self.daily_loss_limit_pct = config.get('daily_loss_limit_pct', 0.06) if config else 0.06  # 6%

        # Soft trigger parameters
        self.profit_reversal_multiplier = config.get('profit_reversal_multiplier', 0.5) if config else 0.5
        self.spread_threshold = config.get('spread_threshold', 0.50) if config else 0.50  # $0.50
        self.volatility_spike_threshold = config.get('volatility_spike_threshold', 0.30) if config else 0.30  # 30%
        self.pattern_check_interval_minutes = config.get('pattern_check_interval_minutes', 10) if config else 10

        # Trade tracking
        self.active_trades: Dict[int, Dict] = {}  # trade_id -> trade_data

        logger.info("✓ Trade Manager initialized (rule-based, 0 agency)")
        logger.info(f"  Hard exits: SL/TP, >{self.max_trade_time_minutes}min, {self.extreme_reversal_multiplier}×ATR reversal, {self.daily_loss_limit_pct:.0%} daily loss")

    def run(self) -> list:
        """
        Monitor all active trades every second

        Returns:
            List of messages (hard exits or soft triggers)
        """
        if not self.active_trades:
            return []

        messages = []

        for trade_id, trade_data in list(self.active_trades.items()):
            # Check hard exits first
            hard_exit = self._check_hard_exits(trade_id, trade_data)
            if hard_exit:
                messages.append(hard_exit)
                continue  # Hard exit executed, skip soft triggers

            # Check soft triggers
            soft_triggers = self._check_soft_triggers(trade_id, trade_data)
            if soft_triggers:
                messages.extend(soft_triggers)

        return messages

    def process_message(self, message: Message) -> Optional[Message]:
        """
        Process trade updates from Execution Agent

        Args:
            message: TRADE_OPENED or TRADE_CLOSED message

        Returns:
            Acknowledgment message
        """
        if message.type == MessageType.TRADE_OPENED:
            return self._handle_trade_opened(message)
        elif message.type == MessageType.TRADE_CLOSED:
            return self._handle_trade_closed(message)

        return None

    def _handle_trade_opened(self, message: Message) -> Optional[Message]:
        """Register new trade for monitoring"""
        trade_data = message.data
        trade_id = trade_data['trade_id']

        # Initialize trade tracking
        self.active_trades[trade_id] = {
            'symbol': trade_data['symbol'],
            'action': trade_data['action'],  # LONG or SHORT
            'entry_price': trade_data['entry_price'],
            'quantity': trade_data['quantity'],
            'stop_loss': trade_data['stop_loss'],
            'take_profit': trade_data['take_profit'],
            'entry_time': trade_data['entry_time'],
            'entry_atr': trade_data.get('entry_atr', 10.0),

            # Tracking
            'max_profit': 0.0,
            'max_adverse_excursion': 0.0,
            'last_pattern_check': self.clock.now(),
            'profit_milestones_triggered': set(),

            # Initial indicators
            'entry_rsi': trade_data.get('entry_rsi', 50.0),
            'entry_ema_signal': trade_data.get('entry_ema_signal', 'NEUTRAL')
        }

        logger.info(f"✓ Trade Manager: Monitoring trade #{trade_id} ({trade_data['action']} {trade_data['symbol']})")
        return None

    def _handle_trade_closed(self, message: Message) -> Optional[Message]:
        """Remove closed trade from monitoring"""
        trade_id = message.data['trade_id']

        if trade_id in self.active_trades:
            del self.active_trades[trade_id]
            logger.info(f"✓ Trade Manager: Stopped monitoring trade #{trade_id}")

        return None

    def _check_hard_exits(self, trade_id: int, trade_data: Dict) -> Optional[Message]:
        """
        Check hard exit conditions (immediate execution)

        Returns:
            HARD_EXIT message if condition met, None otherwise
        """
        # Get current market data
        current_price = self._get_current_price(trade_data['symbol'])
        if current_price is None:
            return None

        # Calculate current P&L
        pnl = self._calculate_pnl(trade_data, current_price)

        # Update max profit/loss tracking
        trade_data['max_profit'] = max(trade_data['max_profit'], pnl)
        if pnl < 0:
            trade_data['max_adverse_excursion'] = min(trade_data['max_adverse_excursion'], pnl)

        # 1. Stop Loss Hit
        if self._is_stop_loss_hit(trade_data, current_price):
            return self._create_hard_exit_message(
                trade_id, trade_data, HardExitReason.STOP_LOSS_HIT,
                f"Stop loss hit at ${current_price:.2f}"
            )

        # 2. Take Profit Hit
        if self._is_take_profit_hit(trade_data, current_price):
            return self._create_hard_exit_message(
                trade_id, trade_data, HardExitReason.TAKE_PROFIT_HIT,
                f"Take profit hit at ${current_price:.2f}"
            )

        # 3. Time Limit Exceeded (>40min)
        time_in_trade = (self.clock.now() - trade_data['entry_time']).total_seconds() / 60
        if time_in_trade > self.max_trade_time_minutes:
            return self._create_hard_exit_message(
                trade_id, trade_data, HardExitReason.TIME_LIMIT_EXCEEDED,
                f"Time limit exceeded: {time_in_trade:.1f}min > {self.max_trade_time_minutes}min"
            )

        # 4. Extreme Reversal (2×ATR against position)
        reversal_amount = self._calculate_reversal(trade_data, current_price)
        if reversal_amount > (self.extreme_reversal_multiplier * trade_data['entry_atr']):
            return self._create_hard_exit_message(
                trade_id, trade_data, HardExitReason.EXTREME_REVERSAL,
                f"Extreme reversal: ${reversal_amount:.2f} > {self.extreme_reversal_multiplier}×ATR"
            )

        # 5. Daily Loss Limit (-6%)
        if self._check_daily_loss_limit():
            return self._create_hard_exit_message(
                trade_id, trade_data, HardExitReason.DAILY_LOSS_LIMIT,
                f"Daily loss limit hit: {self.daily_loss_limit_pct:.0%}"
            )

        return None

    def _check_soft_triggers(self, trade_id: int, trade_data: Dict) -> List[Message]:
        """
        Check soft trigger conditions (flag to Meta-Agent)

        Returns:
            List of SOFT_TRIGGER messages
        """
        triggers = []
        current_price = self._get_current_price(trade_data['symbol'])
        if current_price is None:
            return triggers

        pnl = self._calculate_pnl(trade_data, current_price)

        # DETERIORATION TRIGGERS

        # 1. Profit Reversal (>0.5×ATR from max profit)
        if trade_data['max_profit'] > 0:
            reversal_from_max = trade_data['max_profit'] - pnl
            if reversal_from_max > (self.profit_reversal_multiplier * trade_data['entry_atr']):
                triggers.append(self._create_soft_trigger_message(
                    trade_id, trade_data, SoftTriggerReason.PROFIT_REVERSAL,
                    f"Price reversed ${reversal_from_max:.2f} from max profit"
                ))

        # 2-5. Technical deterioration (RSI, MA, volume, spread)
        # (Simplified - would fetch real-time indicators)

        # PROFIT MILESTONE TRIGGERS

        # 6. Profit >1×ATR (breakeven SL?)
        profit_in_atr = pnl / trade_data['entry_atr']
        if profit_in_atr >= 1.0 and 'PROFIT_1X_ATR' not in trade_data['profit_milestones_triggered']:
            trade_data['profit_milestones_triggered'].add('PROFIT_1X_ATR')
            triggers.append(self._create_soft_trigger_message(
                trade_id, trade_data, SoftTriggerReason.PROFIT_1X_ATR,
                f"Profit milestone: {profit_in_atr:.1f}×ATR - Consider breakeven stop?"
            ))

        # 7. Profit >1.5×ATR (partial exit?)
        if profit_in_atr >= 1.5 and 'PROFIT_1_5X_ATR' not in trade_data['profit_milestones_triggered']:
            trade_data['profit_milestones_triggered'].add('PROFIT_1_5X_ATR')
            triggers.append(self._create_soft_trigger_message(
                trade_id, trade_data, SoftTriggerReason.PROFIT_1_5X_ATR,
                f"Profit milestone: {profit_in_atr:.1f}×ATR - Consider partial exit?"
            ))

        # 8. Profit >2.5×ATR (take profit?)
        if profit_in_atr >= 2.5 and 'PROFIT_2_5X_ATR' not in trade_data['profit_milestones_triggered']:
            trade_data['profit_milestones_triggered'].add('PROFIT_2_5X_ATR')
            triggers.append(self._create_soft_trigger_message(
                trade_id, trade_data, SoftTriggerReason.PROFIT_2_5X_ATR,
                f"Profit milestone: {profit_in_atr:.1f}×ATR - Consider full exit?"
            ))

        # PATTERN CHECK (every 10 minutes)
        minutes_since_check = (self.clock.now() - trade_data['last_pattern_check']).total_seconds() / 60
        if minutes_since_check >= self.pattern_check_interval_minutes:
            trade_data['last_pattern_check'] = self.clock.now()
            triggers.append(self._create_soft_trigger_message(
                trade_id, trade_data, SoftTriggerReason.PATTERN_INVALIDATED,
                f"Pattern check due: {minutes_since_check:.0f}min since last check"
            ))

        return triggers

    def _calculate_pnl(self, trade_data: Dict, current_price: float) -> float:
        """Calculate current P&L"""
        entry_price = trade_data['entry_price']
        quantity = trade_data['quantity']

        if trade_data['action'] == 'LONG':
            return (current_price - entry_price) * quantity
        else:  # SHORT
            return (entry_price - current_price) * quantity

    def _calculate_reversal(self, trade_data: Dict, current_price: float) -> float:
        """Calculate reversal amount from entry"""
        entry_price = trade_data['entry_price']

        if trade_data['action'] == 'LONG':
            # LONG reversal = entry - current (if current < entry)
            return max(0, entry_price - current_price)
        else:  # SHORT
            # SHORT reversal = current - entry (if current > entry)
            return max(0, current_price - entry_price)

    def _is_stop_loss_hit(self, trade_data: Dict, current_price: float) -> bool:
        """Check if stop loss is hit"""
        sl = trade_data['stop_loss']

        if trade_data['action'] == 'LONG':
            return current_price <= sl
        else:  # SHORT
            return current_price >= sl

    def _is_take_profit_hit(self, trade_data: Dict, current_price: float) -> bool:
        """Check if take profit is hit"""
        tp = trade_data['take_profit']

        if trade_data['action'] == 'LONG':
            return current_price >= tp
        else:  # SHORT
            return current_price <= tp

    def _live_account_state(self) -> Dict:
        """Default account-state source in production."""
        from services.account_state_manager import get_account_state_manager
        return get_account_state_manager().get_current_state()

    def _check_daily_loss_limit(self) -> bool:
        """
        Check if daily loss limit exceeded, using the injected account state.
        No hardcoded balance, no direct table query — the account source knows
        the real (or simulated) daily P&L percentage.
        """
        try:
            state = self.account_state_fn() or {}
            daily_pnl_pct = float(state.get('daily_pnl_pct', 0.0))  # negative when down
            return daily_pnl_pct <= -abs(self.daily_loss_limit_pct * 100)
        except Exception as e:
            logger.error(f"Error checking daily loss limit: {e}")
            return False

    def _get_current_price(self, symbol: str) -> Optional[float]:
        """
        Current price at the clock's 'now', via the injected provider.
        Live → latest bar from Postgres; backtest → last bar before as_of.
        """
        try:
            return self.provider.get_price(symbol, as_of=self.clock.now())
        except Exception as e:
            logger.error(f"Error getting current price: {e}")
            return None

    def _create_hard_exit_message(
        self,
        trade_id: int,
        trade_data: Dict,
        reason: HardExitReason,
        details: str
    ) -> Message:
        """Create hard exit message for Execution Agent"""
        logger.warning(f"⚠️ HARD EXIT: Trade #{trade_id} - {reason.value}: {details}")

        return self.send_message(
            msg_type=MessageType.HARD_EXIT,
            recipient=AgentType.EXECUTION,
            data={
                'trade_id': trade_id,
                'exit_type': ExitType.HARD_EXIT.value,
                'reason': reason.value,
                'details': details,
                'symbol': trade_data['symbol'],
                'action': trade_data['action'],
                'max_profit': trade_data['max_profit'],
                'max_adverse_excursion': trade_data['max_adverse_excursion']
            },
            priority=10  # Highest priority
        )

    def _create_soft_trigger_message(
        self,
        trade_id: int,
        trade_data: Dict,
        reason: SoftTriggerReason,
        details: str
    ) -> Message:
        """Create soft trigger message for Meta-Agent"""
        logger.info(f"ℹ️ SOFT TRIGGER: Trade #{trade_id} - {reason.value}: {details}")

        return self.send_message(
            msg_type=MessageType.SOFT_TRIGGER,
            recipient=AgentType.META_AGENT,
            data={
                'trade_id': trade_id,
                'exit_type': ExitType.SOFT_TRIGGER.value,
                'reason': reason.value,
                'details': details,
                'symbol': trade_data['symbol'],
                'action': trade_data['action'],
                'entry_price': trade_data['entry_price'],
                'current_pnl': self._calculate_pnl(
                    trade_data,
                    self._get_current_price(trade_data['symbol']) or trade_data['entry_price']
                ),
                'max_profit': trade_data['max_profit'],
                'time_in_trade_minutes': (self.clock.now() - trade_data['entry_time']).total_seconds() / 60
            },
            priority=8
        )
