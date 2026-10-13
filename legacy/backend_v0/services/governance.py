# backend/services/governance.py
"""
Governance Service

Pure, testable circuit-breaker and weight-guard logic for the Meta-Agent
(Paper B governance). No database, no LLM, no wall-clock — everything it needs
is passed in, so it produces identical results live and in backtest and can be
unit-tested directly.

Previously these limits existed only as unused attributes on the Meta-Agent
(circuit_breaker_win_rate, max_position_size_multiplier, ...). Now they are
enforced through this single module that both production and backtest call.
"""

from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass(frozen=True)
class GovernanceConfig:
    """All governance thresholds in one place (single source of truth)."""
    circuit_breaker_win_rate: float = 0.45   # pause below this...
    circuit_breaker_lookback: int = 15       # ...over this many recent trades
    max_daily_loss_pct: float = 6.0          # close-all beyond this daily drawdown (%)
    max_trades_per_hour: int = 3
    max_position_size_multiplier: float = 1.5
    weight_floor: float = 0.05
    weight_cap: float = 0.60


# Actions a tripped breaker can demand
ACTION_NONE = None
ACTION_PAUSE = "PAUSE"          # stop opening new trades, human review
ACTION_CLOSE_ALL = "CLOSE_ALL"  # flatten everything, stop for the day


def check_circuit_breakers(
    recent_outcomes: List[str],
    account_state: Dict,
    trades_this_hour: int = 0,
    config: Optional[GovernanceConfig] = None,
) -> Dict:
    """
    Decide whether trading must halt before a new entry.

    Args:
        recent_outcomes: chronological 'WIN'/'LOSS' strings (most recent last).
        account_state:   dict with 'daily_pnl_pct' (negative when down).
        trades_this_hour: entries already taken in the current rolling hour.
        config:          thresholds (defaults if omitted).

    Returns:
        {'tripped': bool, 'action': None|'PAUSE'|'CLOSE_ALL', 'reason': str}
    """
    cfg = config or GovernanceConfig()

    # 1. Daily loss → flatten and stop for the day (most severe)
    daily_pnl_pct = float(account_state.get('daily_pnl_pct', 0.0))
    if daily_pnl_pct <= -abs(cfg.max_daily_loss_pct):
        return {
            'tripped': True,
            'action': ACTION_CLOSE_ALL,
            'reason': f'Daily loss {daily_pnl_pct:.2f}% <= -{cfg.max_daily_loss_pct}%',
        }

    # 2. Win-rate degradation over the lookback window → pause for review
    if len(recent_outcomes) >= cfg.circuit_breaker_lookback:
        window = recent_outcomes[-cfg.circuit_breaker_lookback:]
        wins = sum(1 for o in window if str(o).upper() == 'WIN')
        win_rate = wins / len(window)
        if win_rate < cfg.circuit_breaker_win_rate:
            return {
                'tripped': True,
                'action': ACTION_PAUSE,
                'reason': (f'Win rate {win_rate:.0%} < {cfg.circuit_breaker_win_rate:.0%} '
                           f'over last {cfg.circuit_breaker_lookback} trades'),
            }

    # 3. Overtrading guard → pause new entries this hour (not a full stop)
    if trades_this_hour >= cfg.max_trades_per_hour:
        return {
            'tripped': True,
            'action': ACTION_PAUSE,
            'reason': f'Max trades/hour reached ({trades_this_hour}/{cfg.max_trades_per_hour})',
        }

    return {'tripped': False, 'action': ACTION_NONE, 'reason': 'ok'}


def clamp_position_size_modifier(modifier: float, config: Optional[GovernanceConfig] = None) -> float:
    """Never let confidence scaling exceed the governance cap."""
    cfg = config or GovernanceConfig()
    return max(0.0, min(modifier, cfg.max_position_size_multiplier))


def enforce_weight_bounds(weights: Dict[str, float], config: Optional[GovernanceConfig] = None) -> Dict[str, float]:
    """
    Constrain agent weights to [floor, cap] AND sum to 1.0 simultaneously.

    A single clamp-then-normalize does NOT hold the cap: clamping 0.9→0.60 and
    normalizing {0.60, 0.05, 0.05} re-inflates the first weight to 0.86. We
    instead iterate clamp+normalize to a fixed point, so the cap genuinely holds
    (e.g. {0.9,0.05,0.05} → {0.60,0.20,0.20}).
    """
    cfg = config or GovernanceConfig()
    keys = list(weights.values())
    n = len(weights) or 1

    if sum(v for v in keys if v > 0) <= 0:
        return {k: 1.0 / n for k in weights}  # degenerate → equal weights

    # Feasibility: caps/floors must be able to sum to 1.0 across n agents.
    if cfg.weight_cap * n < 1.0 or cfg.weight_floor * n > 1.0:
        return {k: 1.0 / n for k in weights}

    bounded = dict(weights)
    for _ in range(100):
        bounded = {k: max(cfg.weight_floor, min(cfg.weight_cap, v)) for k, v in bounded.items()}
        total = sum(bounded.values())
        bounded = {k: v / total for k, v in bounded.items()}
        if all(cfg.weight_floor - 1e-9 <= v <= cfg.weight_cap + 1e-9 for v in bounded.values()):
            break
    return bounded
