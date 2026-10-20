"""Turn a model's answer into vote content, checked in code
(docs/PROMPT_GUIDE.md §5).

The structured-output schema fixes the shape. This module checks again what
the schema says, plus what it can't say:
- confidence is a finite number in [0, 1];
- the text lengths;
- at most MAX_EVIDENCE items;
- every evidence ref is checked against the snapshot (check_ref), and its
  validity is set by code;
- a long or short vote needs at least one ref that resolves.

Any failure is a reason ("invalid: ..."), and the agent abstains. Nothing is
guessed from free text.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass

from agents.evidence import check_ref, check_vote_ref
from agents.prompting.output_schema import MAX_CLAIM, MAX_EVIDENCE, MAX_REASON
from agents.schemas import Evidence, Snapshot

FIELDS = {"direction", "confidence", "reason", "evidence"}
EVIDENCE_FIELDS = {"ref", "claim"}


@dataclass(frozen=True)
class Answer:
    direction: str
    confidence: float
    reason: str
    evidence: tuple[Evidence, ...]


def parse_answer(text: str | None, snap: Snapshot, shown_votes: set[tuple[str, int]] | None = None) -> Answer | str:
    """The checked answer, or the reason it can't be used. shown_votes: in a
    discussion, the (agent, round) of the votes the agent was shown, so that
    `vote:<agent>:<round>` refs resolve. In round 1 (None) they never do."""
    if text is None:
        return "invalid: no text in the response"
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        return f"invalid: not JSON ({e.msg} at {e.pos})"
    if not isinstance(data, dict):
        return f"invalid: the answer is a {type(data).__name__}, not an object"
    if set(data) != FIELDS:
        return f"invalid: fields {sorted(data)}, expected {sorted(FIELDS)}"
    direction, conf, reason, evidence = (data[k] for k in ("direction", "confidence", "reason", "evidence"))
    if direction not in ("long", "short", "none"):
        return f"invalid: direction {direction!r}"
    if isinstance(conf, bool) or not isinstance(conf, (int, float)) or not math.isfinite(conf) \
            or not 0 <= conf <= 1:
        return f"invalid: confidence {conf!r} is not a number in [0, 1]"
    if not isinstance(reason, str) or len(reason) > MAX_REASON:
        return f"invalid: reason is not a string of at most {MAX_REASON} characters"
    if not isinstance(evidence, list) or len(evidence) > MAX_EVIDENCE:
        return f"invalid: evidence is not a list of at most {MAX_EVIDENCE} items"
    items = []
    for i, item in enumerate(evidence):
        if not isinstance(item, dict) or set(item) != EVIDENCE_FIELDS:
            return f"invalid: evidence[{i}] must have exactly {sorted(EVIDENCE_FIELDS)}"
        ref, claim = item["ref"], item["claim"]
        if not isinstance(ref, str) or not ref.strip():
            return f"invalid: evidence[{i}].ref is empty"
        if not isinstance(claim, str) or len(claim) > MAX_CLAIM:
            return f"invalid: evidence[{i}].claim is not a string of at most {MAX_CLAIM} characters"
        why = (check_vote_ref(ref, shown_votes) if shown_votes is not None and ref.strip().startswith("vote:")
               else check_ref(ref, snap))
        items.append(Evidence(ref=ref, claim=claim, valid=why is None, invalid_reason=why))
    if direction != "none" and not any(x.valid and not x.ref.startswith("vote:") for x in items):
        return "invalid: direction without evidence (no ref resolves in the snapshot)"
    return Answer(direction=direction, confidence=float(conf), reason=reason, evidence=tuple(items))


__all__ = ["Answer", "parse_answer"]
