"""The Meta step of the run graph: one Decision from votes, by the owner's rule.

The same rule decides the `blind` arm (round-1 votes) and the `discussion`
arm (final votes), so the two arms differ only by the discussion. The
technical_v1 benchmark never enters Meta.
"""

from __future__ import annotations

from typing import Mapping

from agents.meta.rules import get_rule
from agents.owner_config import ConfigProblem, OwnerConfig
from agents.schemas import Arm, Decision, Snapshot, Vote


def rule_version(owner: OwnerConfig) -> str | None:
    """The configured rule's version, for the epoch (None if no valid rule)."""
    meta = owner.meta()
    if isinstance(meta, ConfigProblem):
        return None
    rule = get_rule(meta.rule)
    return None if isinstance(rule, str) else rule.version


def meta_decision(votes: Mapping[str, Vote], owner: OwnerConfig, run_id: str, snap: Snapshot,
                  arm: Arm) -> Decision:
    """The arm's Decision. No configured rule, an unknown rule or a config
    problem gives direction none, with the reason; it never raises."""
    meta = owner.meta()
    rule = get_rule(None) if isinstance(meta, ConfigProblem) else get_rule(meta.rule)
    if isinstance(rule, str):
        why = f"{rule} ({meta.reason})" if isinstance(meta, ConfigProblem) else rule
        return Decision(run_id=run_id, snapshot_hash=snap.hash, arm=arm, direction="none", reason=why,
                        decided_by="meta", experiment_id=snap.experiment_id)
    direction, reason = rule.decide(votes)
    return Decision(run_id=run_id, snapshot_hash=snap.hash, arm=arm, direction=direction, reason=reason,
                    decided_by=rule.name, meta_rule=rule.name, meta_version=rule.version,
                    experiment_id=snap.experiment_id)


__all__ = ["meta_decision", "rule_version"]
