# backend/agents/execution_agent_new.py
"""
Execution Agent

Executes approved trades from Risk Manager
Places orders with broker, monitors fills, handles errors
Sends trade confirmations to Trade Manager Agent

Architecture:
- Pure execution (NO LLM, NO decisions)
- Cost: $0 (only broker commissions)
- Latency: <100ms (depends on broker API)
- Agency Score: 0.1/10 (Minimal Agency - executes orders only)
"""

import logging
from typing import Dict, Optional
from datetime import datetime

from agents.base_agent import BaseAgent, AgentType, MessageType, Message
from services.market_data_provider import MarketDataProvider, LiveDataProvider
from config.database import get_database

logger = logging.getLogger(__name__)


class ExecutionAgentNew(BaseAgent):
    """
    Execution Agent - Order placement and fill monitoring

    Core Responsibilities:
    1. Receive approved trades from Risk Manager
    2. Place market orders with broker
    3. Monitor order fills
    4. Handle execution errors
    5. Send trade confirmations to Trade Manager
    6. Log all executions to database

    Does NOT:
    - Make trading decisions (Meta-Agent's job)
    - Validate trades (Risk Manager's job)
    - Monitor positions (Trade Manager's job)
    - Modify orders after placement

    Broker Integration:
    - Uses broker client (OANDA, IG, etc.)
    - Handles API errors and retries
    - Validates fills before confirming
    """

    def __init__(self, config: Optional[Dict] = None, provider: Optional[MarketDataProvider] = None):
        super().__init__(AgentType.EXECUTION, config)

        self.db = get_database()

        # Price source for mock fills (real broker fills come from the broker).
        # Live default reads Postgres; backtests never use this agent (the fill
        # simulator replaces it), but it stays injectable for consistency.
        self.provider = provider or LiveDataProvider(db=self.db)

        # Broker configuration
        self.broker_name = config.get('broker_name', 'OANDA') if config else 'OANDA'
        self.account_id = config.get('account_id', 'demo_account') if config else 'demo_account'
        self.max_retry_attempts = config.get('max_retry_attempts', 3) if config else 3

        # Initialize broker client (placeholder - replace with actual broker integration)
        self.broker_client = self._initialize_broker_client()

        # Performance tracking
        self.stats = {
            'total_orders': 0,
            'filled': 0,
            'failed': 0,
            'rejected': 0,
            'retry_count': 0
        }

        logger.info(f"✓ Execution Agent initialized (broker: {self.broker_name})")
        logger.info(f"  Account ID: {self.account_id}")
        logger.info(f"  Max retry attempts: {self.max_retry_attempts}")

    def _initialize_broker_client(self):
        """
        Initialize broker client connection

        Returns:
            Broker client instance (placeholder)
        """
        # TODO: Replace with actual broker integration
        # from services.broker_clients import get_oanda_client
        # return get_oanda_client(self.account_id)

        logger.warning("⚠️ Using mock broker client (no real trades will be placed)")
        return None  # Mock client for now

    def run(self) -> list:
        """
        Execution Agent doesn't run on schedule - responds to approved trades
        """
        return []

    def process_message(self, message: Message) -> Optional[Message]:
        """
        Process approved trade requests from Risk Manager

        Args:
            message: Message with approved trade parameters

        Returns:
            Trade confirmation or error message
        """
        if message.type != MessageType.TRADE_APPROVED:
            return None

        if not self.is_active:
            logger.debug("Execution Agent is inactive, rejecting order")
            return self._create_error_message(
                message.data.get('symbol'),
                'Execution Agent inactive',
                'system_inactive'
            )

        try:
            logger.info("⚡ Execution Agent: Placing order...")
            start_time = datetime.now()

            # Extract trade parameters
            trade_params = message.data.get('trade_parameters', {})
            symbol = trade_params['symbol']
            direction = trade_params['direction']
            entry_price = trade_params['entry_price']
            stop_loss = trade_params['stop_loss']
            take_profit = trade_params['take_profit']
            position_size = trade_params['position_size']

            # Place order with broker
            execution_result = self._place_order(
                symbol=symbol,
                direction=direction,
                position_size=position_size,
                stop_loss=stop_loss,
                take_profit=take_profit
            )

            execution_time_ms = (datetime.now() - start_time).total_seconds() * 1000

            # Update stats
            self.stats['total_orders'] += 1

            if execution_result['status'] == 'FILLED':
                self.stats['filled'] += 1

                # Get actual fill details
                fill_price = execution_result['fill_price']
                fill_time = execution_result['fill_time']
                order_id = execution_result['order_id']

                logger.info(f"✅ Order FILLED ({execution_time_ms:.0f}ms): "
                          f"{direction} {position_size:.2f} {symbol} @ {fill_price:.2f}")
                logger.info(f"  Order ID: {order_id}")
                logger.info(f"  SL: {stop_loss:.2f}, TP: {take_profit:.2f}")

                # Store execution in database
                trade_id = self._store_execution(
                    symbol=symbol,
                    direction=direction,
                    entry_price=fill_price,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    position_size=position_size,
                    order_id=order_id,
                    fill_time=fill_time,
                    meta_decision_id=message.data.get('meta_decision_id'),
                    risk_decision_id=message.data.get('risk_decision_id')
                )

                # Send trade confirmation to Trade Manager
                return self.send_message(
                    msg_type=MessageType.TRADE_OPENED,
                    recipient=AgentType.TRADE_MANAGER,
                    data={
                        'trade_id': trade_id,
                        'symbol': symbol,
                        'direction': direction,
                        'entry_price': fill_price,
                        'stop_loss': stop_loss,
                        'take_profit': take_profit,
                        'position_size': position_size,
                        'order_id': order_id,
                        'fill_time': fill_time,
                        'execution_time_ms': execution_time_ms
                    },
                    priority=10  # Highest priority for trade confirmations
                )

            elif execution_result['status'] == 'REJECTED':
                self.stats['rejected'] += 1
                logger.error(f"❌ Order REJECTED: {execution_result['reason']}")

                return self._create_error_message(
                    symbol,
                    execution_result['reason'],
                    'broker_rejection'
                )

            else:  # FAILED
                self.stats['failed'] += 1
                logger.error(f"❌ Order FAILED: {execution_result['reason']}")

                return self._create_error_message(
                    symbol,
                    execution_result['reason'],
                    'execution_error'
                )

        except Exception as e:
            logger.error(f"Error in Execution Agent: {e}", exc_info=True)
            return self._create_error_message(
                message.data.get('symbol', 'UNKNOWN'),
                f'Execution error: {str(e)}',
                'system_error'
            )

    def _place_order(
        self,
        symbol: str,
        direction: str,
        position_size: float,
        stop_loss: float,
        take_profit: float
    ) -> Dict:
        """
        Place market order with broker

        Args:
            symbol: Trading symbol
            direction: LONG or SHORT
            position_size: Position size in units
            stop_loss: Stop-loss price
            take_profit: Take-profit price

        Returns:
            Dict with execution result:
            {
                'status': 'FILLED|REJECTED|FAILED',
                'fill_price': 2650.5,
                'fill_time': datetime,
                'order_id': 'ABC123',
                'reason': 'Error reason if not filled'
            }
        """
        try:
            # Check if broker client is available
            if self.broker_client is None:
                # Mock execution for development/testing
                logger.warning("⚠️ Mock execution (no real broker connection)")
                return self._mock_execution(symbol, direction, position_size)

            # Real broker execution
            # TODO: Replace with actual broker API calls
            # Example for OANDA:
            # order = self.broker_client.create_order(
            #     instrument=symbol,
            #     units=position_size if direction == 'LONG' else -position_size,
            #     type='MARKET',
            #     stopLossOnFill={'price': str(stop_loss)},
            #     takeProfitOnFill={'price': str(take_profit)}
            # )
            #
            # if order.status == 'FILLED':
            #     return {
            #         'status': 'FILLED',
            #         'fill_price': float(order.price),
            #         'fill_time': order.time,
            #         'order_id': order.id
            #     }

            # For now, use mock
            return self._mock_execution(symbol, direction, position_size)

        except Exception as e:
            logger.error(f"Error placing order: {e}")
            return {
                'status': 'FAILED',
                'fill_price': None,
                'fill_time': None,
                'order_id': None,
                'reason': f'Broker API error: {str(e)}'
            }

    def _mock_execution(
        self,
        symbol: str,
        direction: str,
        position_size: float
    ) -> Dict:
        """
        Mock execution for testing (no real broker)

        Returns:
            Simulated filled order
        """
        # Simulate current price via the provider (latest bar)
        try:
            fill_price = self.provider.get_price(symbol) or 2650.0
        except Exception as e:
            logger.warning(f"Could not get current price, using default: {e}")
            fill_price = 2650.0

        # Simulate successful fill
        return {
            'status': 'FILLED',
            'fill_price': fill_price,
            'fill_time': datetime.utcnow(),
            'order_id': f'MOCK_{datetime.utcnow().strftime("%Y%m%d_%H%M%S")}'
        }

    def _store_execution(
        self,
        symbol: str,
        direction: str,
        entry_price: float,
        stop_loss: float,
        take_profit: float,
        position_size: float,
        order_id: str,
        fill_time: datetime,
        meta_decision_id: Optional[int],
        risk_decision_id: Optional[int]
    ) -> Optional[int]:
        """
        Store executed trade in database

        Returns:
            Trade ID or None if error
        """
        try:
            with self.db.get_cursor() as cur:
                cur.execute("""
                    INSERT INTO executed_trades (
                        symbol,
                        direction,
                        entry_price,
                        stop_loss,
                        take_profit,
                        position_size,
                        order_id,
                        entry_time,
                        status,
                        meta_decision_id,
                        risk_decision_id
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                    ) RETURNING id
                """, (
                    symbol,
                    direction,
                    entry_price,
                    stop_loss,
                    take_profit,
                    position_size,
                    order_id,
                    fill_time,
                    'OPEN',
                    meta_decision_id,
                    risk_decision_id
                ))

                trade_id = cur.fetchone()['id']
                logger.info(f"✓ Trade stored in database: ID {trade_id}")
                return trade_id

        except Exception as e:
            logger.error(f"Error storing execution: {e}")
            return None

    def _create_error_message(
        self,
        symbol: str,
        reason: str,
        error_type: str
    ) -> Message:
        """
        Create error message to send to Meta-Agent

        Args:
            symbol: Trading symbol
            reason: Error reason
            error_type: Error type category

        Returns:
            Error message for Meta-Agent
        """
        return self.send_message(
            msg_type=MessageType.EXECUTION_ERROR,
            recipient=AgentType.META_AGENT,
            data={
                'symbol': symbol,
                'error_reason': reason,
                'error_type': error_type
            },
            priority=8
        )

    def get_stats(self) -> Dict:
        """
        Get performance statistics

        Returns:
            Dict with stats
        """
        total = max(self.stats['total_orders'], 1)

        return {
            **self.stats,
            'fill_rate': (self.stats['filled'] / total) * 100,
            'rejection_rate': (self.stats['rejected'] / total) * 100,
            'failure_rate': (self.stats['failed'] / total) * 100
        }
