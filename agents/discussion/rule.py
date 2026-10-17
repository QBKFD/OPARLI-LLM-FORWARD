"""The vote-change rule (docs/AGENTS_ARCHITECTURE.md §5.3; AA5, AA6, AA16).

Pure: (the agent's previous vote, its proposal for this round) -> the vote
stored for this round.

- **Same direction and confidence:** kept. The proposal's new reason and
  evidence are stored, `changed = False`, and no new evidence is needed.
- **A change of direction or confidence** is accepted only if the proposal
  cites at least one ref that resolves in the snapshot, isn't a `vote:` ref,
  and wasn't in the previous vote's evidence. Then `changed = True`.
- **Otherwise the change is refused.** The previous vote is carried forward
  with `change_rejected` set to the reason (`no new snapshot ref` / `only
  vote: refs`) and the refused vote kept as `rejected_proposal`.
- **A failed or invalid proposal** (an abstain from the discussant) carries
  the previous vote forward with `change_rejected = "call failed: <reason>"`.
  It's never an abstain mid-discussion.

The stored vote keeps this round's call provenance (model, prompt hash,
tokens, call id), so every row traces to the attempt that produced it.
"""

from __future__ import annotations

from agents.schemas import RejectedChange, Vote

PROVENANCE = ("model_id", "prompt_hash", "tokens_in", "tokens_out", "latency_ms", "call_id")
NO_NEW_REF = "no new snapshot ref"
ONLY_VOTE_REFS = "only vote: refs"


def new_snapshot_refs(previous: Vote, proposal: Vote) -> set[str]:
    """Resolving, non-vote refs in the proposal that the previous vote didn't cite."""
    before = {e.ref for e in previous.evidence}
    return {e.ref for e in proposal.evidence if e.valid and not e.ref.startswith("vote:") and e.ref not in before}


def _carry(previous: Vote, proposal: Vote, round_: int, why: str,
           rejected: RejectedChange | None = None) -> Vote:
    meta = {k: getattr(proposal, k) for k in PROVENANCE}
    return Vote(agent=previous.agent, round=round_, direction=previous.direction, confidence=previous.confidence,
                reason=previous.reason, evidence=previous.evidence, changed=False, change_rejected=why,
                rejected_proposal=rejected, **meta)


def apply_change_rule(previous: Vote, proposal: Vote, round_: int) -> Vote:
    """The vote stored for discussion vote-round `round_` (2, 3, ...)."""
    if previous.abstain:
        raise ValueError(f"{previous.agent}: an abstained agent doesn't take part in the discussion")
    if proposal.agent != previous.agent:
        raise ValueError(f"proposal for {proposal.agent!r}, previous vote of {previous.agent!r}")
    if proposal.abstain:
        return _carry(previous, proposal, round_, f"call failed: {proposal.abstain_reason}")
    same = proposal.direction == previous.direction and proposal.confidence == previous.confidence
    content = proposal.model_copy(update={"round": round_, "changed": not same, "change_rejected": None,
                                          "rejected_proposal": None})
    if same or new_snapshot_refs(previous, proposal):
        return Vote.model_validate(content.model_dump())
    only_votes = any(e.valid and e.ref.startswith("vote:") for e in proposal.evidence)
    rejected = RejectedChange(direction=proposal.direction, confidence=proposal.confidence, reason=proposal.reason,
                              evidence=proposal.evidence)
    return _carry(previous, proposal, round_, ONLY_VOTE_REFS if only_votes else NO_NEW_REF, rejected)


__all__ = ["NO_NEW_REF", "ONLY_VOTE_REFS", "apply_change_rule", "new_snapshot_refs"]
