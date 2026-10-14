"""Aggregator v0: round-1 votes -> the blind Decision. Plain code.

PLACEHOLDER, not to be tuned on results. Owner rule (2026-10-14): direction d
(long or short) if at least 2 NON-ABSTAINING votes agree on d, otherwise
"none". Confidence is ignored. Abstains never count, for or against. A "none"
vote is a non-abstaining vote for no position. With 3 voters, at most one
direction can reach 2. With more voters, if both reach 2 the result is "none".
"""

from __future__ import annotations

from typing import Mapping

from agents.schemas import Decision, Vote

NAME = "aggregator_v0"
QUORUM = 2


def aggregate_v0(votes: Mapping[str, Vote], run_id: str, snapshot_hash: str,
                 experiment_id: str | None = None) -> Decision:
    n = len(votes)
    count = {d: sum(1 for v in votes.values() if not v.abstain and v.direction == d)
             for d in ("long", "short", "none")}
    abstain = sum(1 for v in votes.values() if v.abstain)
    winners = [d for d in ("long", "short") if count[d] >= QUORUM]
    direction = winners[0] if len(winners) == 1 else "none"
    tally = f"long {count['long']}, short {count['short']}, none {count['none']}, abstain {abstain} of {n}"
    if direction != "none":
        reason = f"{tally}: {count[direction]} non-abstaining votes agree on {direction}"
    elif winners:
        reason = f"{tally}: both long and short reach {QUORUM}"
    else:
        reason = f"{tally}: no direction has {QUORUM} agreeing non-abstaining votes"
    return Decision(run_id=run_id, snapshot_hash=snapshot_hash, arm="blind", direction=direction,
                    reason=reason, decided_by=NAME, experiment_id=experiment_id)
