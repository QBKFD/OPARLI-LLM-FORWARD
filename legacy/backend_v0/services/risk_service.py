# backend/services/risk_service.py
"""
Risk Validation Service

The single source of truth for the Risk Manager's decision: hard-limit checks,
stop-loss, take-profit, position sizing, and final validations.

Both consumers call the SAME function:
  - Production RiskManagerAgent (passes DB-sourced account_state, then logs)
  - Backtests / backtest_agents.py (passes a SIMULATED account_state)

The DB coupling in the original agent was only two things:
  1. account_state IN  → now an injected parameter (live: from DB; backtest: simulated)
  2. decision logging OUT → stays in the agent; the pure service never writes

This mirrors the market-data provider pattern: inject the source, share the logic.
"""

import logging
from typing import Dict, Optional

from services.position_sizing import get_position_sizing_calculator
from services.stop_loss_calculator import get_stop_loss_calculator
from services.take_profit_calculator import get_take_profit_calculator
from services.hard_limit_checker import get_hard_limit_checker

logger = logging.getLogger(__name__)


def run_risk_validation(
    trade_request: Dict,
    account_state: Dict,
    *,
    check_limits: bool = True,
) -> Dict:
    """
    Validate a trade request and compute full trade parameters. Pure w.r.t. the
    market/account: everything it needs is in the two arguments. No DB reads,
    no DB writes.

    Args:
        trade_request: {
            'direction': 'LONG'|'SHORT',
            'symbol': str,
            'entry_price': float,
            'confidence': float,               # Meta-Agent confidence
            'market_context': {'atr', 'support_level', 'resistance_level', 'spread'}
        }
        account_state: {
            'current_balance', 'daily_pnl_pct', 'drawdown_pct',
            'open_positions', 'trades_today'
        }  # live: AccountStateManager; backtest: maintained by the sim loop
        check_limits: run hard-limit gate (set False to size a trade regardless).

    Returns:
        Same shape as the original RiskManagerAgent.validate_trade_request():
        {'status': 'APPROVED'|'REJECTED', ...}
    """
    stop_loss_calculator = get_stop_loss_calculator()
    take_profit_calculator = get_take_profit_calculator()
    position_sizing = get_position_sizing_calculator()
    hard_limit_checker = get_hard_limit_checker()

    try:
        direction = trade_request['direction']
        symbol = trade_request['symbol']
        entry_price = trade_request['entry_price']
        meta_confidence = trade_request['confidence']

        market_context = trade_request.get('market_context', {})
        current_atr = market_context.get('atr')
        support = market_context.get('support_level')
        resistance = market_context.get('resistance_level')
        spread = market_context.get('spread', 0.30)

        # Step 1: Hard limits (uses injected account_state)
        if check_limits:
            limit_check = hard_limit_checker.check_hard_limits(account_state)
            if not limit_check['passed']:
                return {
                    'status': 'REJECTED',
                    'reason': limit_check['reason'],
                    'category': 'hard_limit_violation',
                    'action': limit_check['action'],
                }

        # Step 2: Required fields
        if not current_atr or current_atr <= 0:
            return {'status': 'REJECTED', 'reason': 'Invalid ATR value', 'category': 'invalid_data'}

        # Step 3: Stop-loss
        stop_result = stop_loss_calculator.calculate_stop_loss(
            direction, entry_price, current_atr, support, resistance
        )
        if not stop_result.get('stop_loss'):
            return {
                'status': 'REJECTED',
                'reason': f"Stop-loss calculation failed: {stop_result.get('error', 'Unknown error')}",
                'category': 'calculation_error',
            }
        stop_loss = stop_result['stop_loss']
        stop_distance = stop_result['stop_distance']

        # Step 4: Take-profit
        tp_result = take_profit_calculator.calculate_take_profit(
            direction, entry_price, stop_distance, resistance, support, spread
        )
        if tp_result.get('recommended_action') == 'REJECT_TRADE':
            return {
                'status': 'REJECTED',
                'reason': tp_result['rejection_reason'],
                'category': 'poor_risk_reward',
            }
        take_profit = tp_result['take_profit']
        risk_reward = tp_result['risk_reward_ratio']

        # Step 5: Position size
        position_result = position_sizing.calculate_position_size(
            account_state['current_balance'], meta_confidence, entry_price, stop_loss
        )
        if position_result.get('error'):
            return {
                'status': 'REJECTED',
                'reason': f"Position sizing failed: {position_result['error']}",
                'category': 'calculation_error',
            }

        # Step 6: Final validation checks
        validations = {
            'spread_acceptable': spread < 0.50,
            'risk_reward_acceptable': risk_reward >= 1.5,
            'position_size_valid': position_result['position_size'] > 0,
            'stop_distance_reasonable': (1.5 * current_atr <= stop_distance <= 3.0 * current_atr),
        }
        if not all(validations.values()):
            failed = [k for k, v in validations.items() if not v]
            return {
                'status': 'REJECTED',
                'reason': f"Failed validations: {', '.join(failed)}",
                'category': 'validation_failure',
                'failed_checks': failed,
            }

        # APPROVED
        return {
            'status': 'APPROVED',
            'trade_parameters': {
                'direction': direction,
                'symbol': symbol,
                'entry_price': entry_price,
                'stop_loss': stop_loss,
                'take_profit': take_profit,
                'position_size': position_result['position_size'],
                'risk_dollars': position_result['risk_dollars'],
                'risk_pct': position_result['risk_pct'],
                'risk_reward_ratio': risk_reward,
                'stop_distance': stop_distance,
                'tp_distance': tp_result['tp_distance'],
            },
            'calculations': {
                'stop_method': stop_result['stop_method'],
                'tp_method': tp_result['tp_method'],
                'confidence_used': meta_confidence,
                'atr_used': current_atr,
                'position_capped': position_result.get('capped', False),
            },
            # Raw sub-results so the caller (live agent) can log them
            '_stop_result': stop_result,
            '_tp_result': tp_result,
            '_position_result': position_result,
        }

    except Exception as e:
        logger.error(f"Error in risk validation: {e}", exc_info=True)
        return {'status': 'REJECTED', 'reason': f'Validation error: {str(e)}', 'category': 'system_error'}
