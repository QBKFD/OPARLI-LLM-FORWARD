"""Aggregator v0: round-1 votes -> the blind Decision. Plain code.

PLACEHOLDER, not to be tuned on results. Rule: direction d (long or short) if
a strict majority of ALL voters vote d, otherwise "none". Abstains and "none"
votes count against. Confidence is ignored. With 3 voters, that's at least 2
in the same direction.
"""

from __future__ import annotations

from typing import Mapping

from agents.schemas import Decision, Vote

NAME = "aggregator_v0"


def aggregate_v0(votes: Mapping[str, Vote], run_id: str, snapshot_hash: str) -> Decision:
    n = len(votes)
    count = {d: sum(1 for v in votes.values() if not v.abstain and v.direction == d)
             for d in ("long", "short", "none")}
    abstain = sum(1 for v in votes.values() if v.abstain)
    winners = [d for d in ("long", "short") if 2 * count[d] > n]
    direction = winners[0] if len(winners) == 1 else "none"
    tally = f"long {count['long']}, short {count['short']}, none {count['none']}, abstain {abstain} of {n}"
    reason = (f"{tally}: strict majority {direction}" if direction != "none"
              else f"{tally}: no strict majority")
    return Decision(run_id=run_id, snapshot_hash=snapshot_hash, arm="blind", direction=direction,
                    reason=reason, decided_by=NAME)
