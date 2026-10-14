"""Debater interface, the view a debater receives, and the wrapper around a turn.

A debater gets one DebaterView and nothing else:
  role, round, a copy of the snapshot, the round-1 votes, and the opponent's
  previous turn (None in round 1).
Not its own earlier turns, not the blind decision, not the judge.

Bull must argue LONG, bear must argue SHORT; conceding is allowed.

safe_turn never lets a debater stop the debate. Each of these becomes an empty
turn (no claims) with `error` set to the reason:
- an exception;
- a timeout;
- None or a wrong type;
- a turn for another role or round;
- anything failing Turn validation.
A debater can't mark its own claims valid: the flags are dropped here and set
by the evidence check.
"""

from __future__ import annotations

import time
from typing import Callable, Protocol

from pydantic import BaseModel, ConfigDict, ValidationError

from agents.hashing import sha256_hex
from agents.schemas import Claim, Role, Snapshot, Turn, Vote
from agents.voters.base import MAX_REASON, call_with_timeout


class DebaterView(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    role: Role
    round: int
    snapshot: Snapshot
    round1_votes: tuple[Vote, ...]
    opponent_previous_turn: Turn | None = None


class Debater(Protocol):
    def argue(self, view: DebaterView) -> Turn | dict | None: ...


def _claim_input(c):
    if isinstance(c, Claim):
        return {"text": c.text, "evidence_ref": c.evidence_ref}
    if isinstance(c, dict):
        return {k: v for k, v in c.items() if k not in ("valid", "invalid_reason")}
    return c                                          # rejected by validation below


def safe_turn(debater: Debater, view: DebaterView, timeout_s: float,
              clock: Callable[[], float] = time.monotonic) -> Turn:
    role, rnd = view.role, view.round
    view_hash = sha256_hex(view)
    t0 = clock()

    def ms():
        return max(0.0, (clock() - t0) * 1000.0)

    def empty(reason, latency):
        return Turn(role=role, round=rnd, error=reason[:MAX_REASON], prompt_hash=view_hash, latency_ms=latency)

    try:
        out = call_with_timeout(lambda: debater.argue(view), timeout_s)
    except TimeoutError as e:
        return empty(f"timeout: {e}", ms())
    except Exception as e:                            # noqa: BLE001 - any failure is an empty turn
        return empty(f"error: {type(e).__name__}: {e}", ms())
    latency = ms()
    if out is None:
        return empty("invalid turn: no turn returned", latency)
    if isinstance(out, Turn):
        data = out.model_dump()
        data["claims"] = list(out.claims)
    elif isinstance(out, dict):
        data = dict(out)
    else:
        return empty(f"invalid turn: expected Turn or dict, got {type(out).__name__}", latency)
    if data.get("role", role) != role or data.get("round", rnd) != rnd:
        return empty(f"invalid turn: role/round {data.get('role')!r}/{data.get('round')!r}, "
                     f"expected {role!r}/{rnd}", latency)
    claims = data.get("claims", ())
    if not isinstance(claims, (list, tuple)):
        return empty("invalid turn: claims must be a list", latency)
    data.update(role=role, round=rnd, claims=tuple(_claim_input(c) for c in claims), latency_ms=latency)
    if data.get("prompt_hash") is None:
        data["prompt_hash"] = view_hash
    if data.get("error") is not None:
        return empty(f"debater reported: {data['error']}", latency)
    try:
        return Turn.model_validate(data)
    except ValidationError as e:
        err = e.errors()[0]
        return empty(f"invalid turn: {'.'.join(map(str, err['loc'])) or 'turn'}: {err['msg']}", latency)
