"""Meta rules: votes -> one direction, in plain code (docs/AGENTS_ARCHITECTURE.md §5.5).

The owner chooses the rule by name (`[meta] rule` in agents.toml). There is
no default rule: a missing or unknown name makes Meta abstain, i.e. the
decision is `none` with the reason. A rule's parameters are part of its
definition; a different parameter set is a new version. The name and version
are stored on every decision and enter the epoch.

Abstaining votes never count, for or against. A `none` vote is a real vote for
no position; each rule says how it counts it.
"""

from __future__ import annotations

from fractions import Fraction
from typing import Mapping, Protocol

from agents.schemas import Direction, Vote


class MetaRule(Protocol):
    name: str
    version: str

    def decide(self, votes: Mapping[str, Vote]) -> tuple[Direction, str]:
        """(direction, reason)."""


def _counts(votes: Mapping[str, Vote]) -> tuple[dict[str, int], int]:
    count = {d: sum(1 for v in votes.values() if not v.abstain and v.direction == d) for d in ("long", "short", "none")}
    return count, sum(1 for v in votes.values() if v.abstain)


class QuorumV0:
    """The skeleton's aggregator v0, unchanged (owner rule 2026-10-14): long or
    short if at least QUORUM non-abstaining votes agree on it, otherwise none.
    Confidence is ignored. If both directions reach the quorum: none."""
    name = "quorum_v0"
    version = "1"
    QUORUM = 2

    def decide(self, votes: Mapping[str, Vote]) -> tuple[Direction, str]:
        count, abstain = _counts(votes)
        winners = [d for d in ("long", "short") if count[d] >= self.QUORUM]
        direction: Direction = winners[0] if len(winners) == 1 else "none"  # type: ignore[assignment]
        tally = (f"long {count['long']}, short {count['short']}, none {count['none']}, "
                 f"abstain {abstain} of {len(votes)}")
        if direction != "none":
            return direction, f"{tally}: {count[direction]} non-abstaining votes agree on {direction}"
        if winners:
            return "none", f"{tally}: both long and short reach {self.QUORUM}"
        return "none", f"{tally}: no direction has {self.QUORUM} agreeing non-abstaining votes"


class FixedMetaV1:
    """The deterministic half of the legacy meta_decision, with its bugs fixed
    (legacy/PORT_CANDIDATES.md; docs/AGENTS_TECHNICAL_DESIGN.md §1):

    - Participants are the long and short votes; `none` and abstains don't take
      part (legacy PASS).
    - **Fewer than MIN_PARTICIPANTS participants: none** (fix 4: one voter can't
      decide alone).
    - **Equal weights** (fix 5). The score of a direction is the sum of its
      votes' confidence / participants, renormalised over the participants.
    - **The direction is the argmax of the scores**, and none on a tie (fix 1).
    - **The winning side needs strictly more votes** than the other (fix 3).
    - **Tiers, compared exactly as fractions** (fix 2):
      - high: score >= 7/10;
      - low: score >= 6/10 and agreement >= 2/3, where agreement =
        winning votes / participants.

      Otherwise none.
    """
    name = "fixed_meta_v1"
    version = "1"
    MIN_PARTICIPANTS = 2
    HIGH = Fraction(7, 10)
    LOW = Fraction(6, 10)
    LOW_AGREEMENT = Fraction(2, 3)

    def decide(self, votes: Mapping[str, Vote]) -> tuple[Direction, str]:
        part = [v for v in votes.values() if not v.abstain and v.direction in ("long", "short")]
        n = len(part)
        if n < self.MIN_PARTICIPANTS:
            return "none", f"{n} directional votes, fewer than {self.MIN_PARTICIPANTS}"
        conf = {d: sum((Fraction(repr(v.confidence)) for v in part if v.direction == d), Fraction(0))
                for d in ("long", "short")}
        count = {d: sum(1 for v in part if v.direction == d) for d in ("long", "short")}
        score = {d: conf[d] / n for d in conf}
        if score["long"] == score["short"]:
            return "none", f"scores tie at {float(score['long']):.4f}"
        win: Direction = "long" if score["long"] > score["short"] else "short"
        lose = "short" if win == "long" else "long"
        tally = (f"{win} {count[win]} vs {lose} {count[lose]}, score {float(score[win]):.4f}, "
                 f"agreement {count[win]}/{n}")
        if count[win] <= count[lose]:
            return "none", f"{tally}: the higher score doesn't have strictly more votes"
        agreement = Fraction(count[win], n)
        if score[win] >= self.HIGH:
            return win, f"{tally}: high tier (score >= 0.7)"
        if score[win] >= self.LOW and agreement >= self.LOW_AGREEMENT:
            return win, f"{tally}: low tier (score >= 0.6, agreement >= 2/3)"
        return "none", f"{tally}: below both tiers"


RULES: dict[str, MetaRule] = {r.name: r for r in (QuorumV0(), FixedMetaV1())}


def get_rule(name: str | None) -> MetaRule | str:
    """The rule, or the reason there is none (Meta then abstains)."""
    if name is None:
        return "meta: no rule configured"
    rule = RULES.get(name)
    return rule if rule is not None else f"meta: unknown rule {name!r}; known: {sorted(RULES)}"


__all__ = ["FixedMetaV1", "MetaRule", "QuorumV0", "RULES", "get_rule"]
