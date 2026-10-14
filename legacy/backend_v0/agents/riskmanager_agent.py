# backend/agents/risk_manager_agent_new.py
"""
Risk Manager Agent

Validates all trade requests from Meta-Agent
Calculates position sizing, stop-loss, and take-profit levels
Enforces account protection limits
Acts as gatekeeper before Execution Agent

Architecture:
- Pure rule-based (NO LLM)
- Cost: $0
- Latency: <10ms
- Agency Score: 0.5/10 (Low Agency - validates and calculates, no autonomy)
"""

import logging
from typing import Dict, Optional
from datetime import datetime

from agents.base_agent import BaseAgent, AgentType, MessageType, Message
from services.account_state_manager import get_account_state_manager
from services.position_sizing import get_position_sizing_calculator
from services.stop_loss_calculator import get_stop_loss_calculator
from services.take_profit_calculator import get_take_profit_calculator
from services.hard_limit_checker import get_hard_limit_checker
from services.risk_service import run_risk_validation
from config.database import get_database

logger = logging.getLogger(__name__)


class RiskManagerAgentNew(BaseAgent):
    """
    Risk Manager Agent - Trade validation and position sizing

    Core Responsibilities:
    1. Account protection (hard limits)
    2. Position sizing (confidence-based)
    3. Stop-loss calculation (hybrid ATR + technical levels)
    4. Take-profit calculation (structure-based)
    5. Trade validation (risk/reward, spread, etc.)

    Hard Limits:
    - Max daily loss: -6%
    - Max drawdown: -15%
    - Max position size: 3% of account
    - Max open positions: 2
    - Max trades per day: 10
    - Min account balance: $1000

    Does NOT:
    - Place orders (Execution Agent's job)
    - Make trade decisions (Meta-Agent's job)
    - Monitor trades (Trade Manager's job)
    - Override its own limits (hardcoded, no exceptions)
    """

    def __init__(self, config: Optional[Dict] = None, account_state_fn=None):
        super().__init__(AgentType.RISK_MANAGER, config)

        # Initialize services. account_state_fn is injectable so backtests and
        # tests can supply a simulated account without a database — the same
        # pattern the Trade Manager already uses.
        self.account_state_manager = get_account_state_manager()
        self.account_state_fn = account_state_fn or self.account_state_manager.get_current_state
        self.position_sizing = get_position_sizing_calculator()
        self.stop_loss_calculator = get_stop_loss_calculator()
        self.take_profit_calculator = get_take_profit_calculator()
        self.hard_limit_checker = get_hard_limit_checker()

        self.db = get_database()

        # Performance tracking
        self.stats = {
            'total_requests': 0,
            'approved': 0,
            'rejected': 0,
            'rejection_reasons': {}
        }

        logger.info("✓ Risk Manager Agent initialized (rule-based, no LLM)")
        logger.info(f"  Hard limits: Daily loss {self.hard_limit_checker.MAX_DAILY_LOSS_PCT}%, "
                   f"Drawdown {self.hard_limit_checker.MAX_DRAWDOWN_PCT}%, "
                   f"Max positions {self.hard_limit_checker.MAX_OPEN_POSITIONS}")

    def run(self) -> list:
        """
        Risk Manager doesn't run on schedule - responds to validation requests
        """
        return []

    def process_message(self, message: Message) -> Optional[Message]:
        """
        Process trade validation requests from Meta-Agent

        Args:
            message: Message with trade request

        Returns:
            Validation result message (APPROVED or REJECTED)
        """
        if message.type != MessageType.TRADE_REQUEST:
            return None

        if not self.is_active:
            logger.debug("Risk Manager is inactive, rejecting trade request")
            return self._create_rejection_message(
                message.data.get('symbol'),
                'Risk Manager inactive',
                'system_inactive'
            )

        try:
            logger.info("🛡️ Risk Manager: Validating trade request...")
            start_time = datetime.now()

            # Extract trade request data
            trade_request = message.data

            # Validate trade
            validation_result = self.validate_trade_request(trade_request)

            execution_time_ms = (datetime.now() - start_time).total_seconds() * 1000

            # Update stats
            self.stats['total_requests'] += 1

            if validation_result['status'] == 'APPROVED':
                self.stats['approved'] += 1
                logger.info(f"✅ Trade APPROVED ({execution_time_ms:.0f}ms): "
                          f"{trade_request['direction']} {trade_request['symbol']} "
                          f"@ {validation_result['trade_parameters']['entry_price']:.2f}")
                logger.info(f"  Position size: {validation_result['trade_parameters']['position_size']:.2f} units "
                          f"(${validation_result['trade_parameters']['risk_dollars']:.2f} risk, "
                          f"{validation_result['trade_parameters']['risk_pct']:.2f}%)")
                logger.info(f"  SL: {validation_result['trade_parameters']['stop_loss']:.2f}, "
                          f"TP: {validation_result['trade_parameters']['take_profit']:.2f}, "
                          f"R:R {validation_result['trade_parameters']['risk_reward_ratio']:.2f}:1")

                # Send to Execution Agent
                return self.send_message(
                    msg_type=MessageType.TRADE_APPROVED,
                    recipient=AgentType.EXECUTION,
                    data={
                        'symbol': trade_request['symbol'],
                        'trade_parameters': validation_result['trade_parameters'],
                        'calculations': validation_result['calculations'],
                        'meta_decision_id': trade_request.get('meta_decision_id'),
                        'validation_time_ms': execution_time_ms
                    },
                    priority=9  # High priority for approved trades
                )

            else:
                # REJECTED
                self.stats['rejected'] += 1

                # Track rejection reason
                category = validation_result.get('category', 'unknown')
                if category not in self.stats['rejection_reasons']:
                    self.stats['rejection_reasons'][category] = 0
                self.stats['rejection_reasons'][category] += 1

                logger.warning(f"❌ Trade REJECTED ({execution_time_ms:.0f}ms): {validation_result['reason']}")

                # Send rejection to Meta-Agent
                return self.send_message(
                    msg_type=MessageType.TRADE_REJECTED,
                    recipient=AgentType.META_AGENT,
                    data={
                        'symbol': trade_request['symbol'],
                        'rejection_reason': validation_result['reason'],
                        'rejection_category': category,
                        'meta_decision_id': trade_request.get('meta_decision_id'),
                        'validation_time_ms': execution_time_ms
                    },
                    priority=7
                )

        except Exception as e:
            logger.error(f"Error in Risk Manager: {e}", exc_info=True)
            return self._create_rejection_message(
                message.data.get('symbol', 'UNKNOWN'),
                f'Risk Manager error: {str(e)}',
                'system_error'
            )

    def validate_trade_request(self, trade_request: Dict) -> Dict:
        """
        Main validation function

        Args:
            trade_request: Trade request from Meta-Agent containing:
                - direction: LONG or SHORT
                - symbol: Trading symbol
                - entry_price: Entry price
                - confidence: Meta-Agent confidence
                - market_context: {atr, support_level, resistance_level, spread}

        Returns:
            Dict with status APPROVED or REJECTED
        """
        try:
            # Source live account state, then run the SHARED validation logic.
            # Backtests call run_risk_validation() directly with a simulated
            # account_state — identical code, no re-implementation.
            account_state = self.account_state_fn()

            result = run_risk_validation(trade_request, account_state)

            # DB logging stays here (side-effect the pure service never does).
            if result['status'] == 'APPROVED':
                decision_id = self._log_decision(
                    trade_request,
                    account_state,
                    result.pop('_stop_result'),
                    result.pop('_tp_result'),
                    result.pop('_position_result'),
                    'APPROVED'
                )
                result['decision_id'] = decision_id
            else:
                # Drop internal sub-results if present
                for k in ('_stop_result', '_tp_result', '_position_result'):
                    result.pop(k, None)

            return result

        except Exception as e:
            logger.error(f"Error validating trade request: {e}", exc_info=True)
            return {
                'status': 'REJECTED',
                'reason': f'Validation error: {str(e)}',
                'category': 'system_error'
            }

    def _log_decision(
        self,
        trade_request: Dict,
        account_state: Dict,
        stop_result: Dict,
        tp_result: Dict,
        position_result: Dict,
        status: str,
        rejection_reason: str = None
    ) -> Optional[int]:
        """
        Log risk management decision to database

        Returns:
            Decision ID or None if error
        """
        try:
            with self.db.get_cursor() as cur:
                cur.execute("""
                    INSERT INTO risk_manager_decisions (
                        trade_request_id,
                        status,
                        rejection_reason,
                        rejection_category,
                        entry_price,
                        stop_loss,
                        take_profit,
                        position_size,
                        risk_dollars,
                        risk_pct,
                        risk_reward_ratio,
                        stop_method,
                        tp_method,
                        confidence_used,
                        atr_used,
                        account_balance,
                        open_positions,
                        daily_pnl
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                    ) RETURNING id
                """, (
                    trade_request.get('meta_decision_id'),
                    status,
                    rejection_reason,
                    trade_request.get('rejection_category') if status == 'REJECTED' else None,
                    trade_request.get('entry_price'),
                    stop_result.get('stop_loss'),
                    tp_result.get('take_profit'),
                    position_result.get('position_size'),
                    position_result.get('risk_dollars'),
                    position_result.get('risk_pct'),
                    tp_result.get('risk_reward_ratio'),
                    stop_result.get('stop_method'),
                    tp_result.get('tp_method'),
                    trade_request.get('confidence'),
                    trade_request.get('market_context', {}).get('atr'),
                    account_state.get('current_balance'),
                    account_state.get('open_positions'),
                    account_state.get('daily_pnl')
                ))

                decision_id = cur.fetchone()['id']
                return decision_id

        except Exception as e:
            logger.error(f"Error logging risk management decision: {e}")
            return None

    def _create_rejection_message(
        self,
        symbol: str,
        reason: str,
        category: str
    ) -> Message:
        """
        Create rejection message to send to Meta-Agent

        Args:
            symbol: Trading symbol
            reason: Rejection reason
            category: Rejection category

        Returns:
            Message for Meta-Agent
        """
        return self.send_message(
            msg_type=MessageType.TRADE_REJECTED,
            recipient=AgentType.META_AGENT,
            data={
                'symbol': symbol,
                'rejection_reason': reason,
                'rejection_category': category
            },
            priority=7
        )

    def get_stats(self) -> Dict:
        """
        Get performance statistics

        Returns:
            Dict with stats
        """
        total = max(self.stats['total_requests'], 1)

        return {
            **self.stats,
            'approval_rate': (self.stats['approved'] / total) * 100,
            'rejection_rate': (self.stats['rejected'] / total) * 100
        }
