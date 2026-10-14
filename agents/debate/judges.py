"""Judges: valid claims + the round-1 direction -> long / short / hold.

Pluggable through JUDGES (config: debate.judge). The judge receives the turns
with INVALID CLAIMS REMOVED, so no judge, code or LLM, can be swayed by a claim
that failed the evidence check. Any error or invalid output -> hold, with the
reason (safe_judge).

Slot for an LLM judge: register it in JUDGES under its own name. Not
implemented; nothing here calls a model.
"""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, ConfigDict, ValidationError

from agents.schemas import Direction, Outcome, Turn


class JudgeInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    snapshot_hash: str
    blind_direction: Direction           # the round-1 (blind) decision's direction
    turns: tuple[Turn, ...]              # valid claims only


class Verdict(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    outcome: Outcome
    reason: str


class Judge(Protocol):
    name: str

    def judge(self, inp: JudgeInput) -> Verdict | dict: ...


class CodeJudgeV0:
    """PLACEHOLDER, not to be tuned on results.

    hold, unless one side has strictly more valid claims AND that side (bull =
    long, bear = short) equals the round-1 direction; then that direction.
    Concessions and confidence are not used. With a round-1 direction of
    "none", the outcome is always hold."""

    name = "code_v0"

    def judge(self, inp: JudgeInput) -> Verdict:
        nb = sum(len(t.claims) for t in inp.turns if t.role == "bull")
        ns = sum(len(t.claims) for t in inp.turns if t.role == "bear")
        if nb == ns:
            return Verdict(outcome="hold", reason=f"tie: bull {nb}, bear {ns} valid claims")
        leader, side = ("bull", "long") if nb > ns else ("bear", "short")
        tally = f"{leader} has more valid claims (bull {nb}, bear {ns})"
        if inp.blind_direction == side:
            return Verdict(outcome=side, reason=f"{tally} and agrees with the round-1 direction {side}")
        return Verdict(outcome="hold", reason=f"{tally} but the round-1 direction is {inp.blind_direction}")


JUDGES: dict[str, type] = {"code_v0": CodeJudgeV0}


def make_judge(name: str) -> Judge:
    if name not in JUDGES:
        raise ValueError(f"unknown judge {name!r}; available: {sorted(JUDGES)}")
    return JUDGES[name]()


def safe_judge(judge: Judge, inp: JudgeInput) -> Verdict:
    try:
        out = judge.judge(inp)
        if isinstance(out, Verdict):
            return out
        if isinstance(out, dict):
            return Verdict.model_validate(out)
        return Verdict(outcome="hold", reason=f"judge error: expected Verdict or dict, got {type(out).__name__}")
    except ValidationError as e:
        err = e.errors()[0]
        return Verdict(outcome="hold", reason=f"judge error: invalid verdict: {err['msg']}")
    except Exception as e:                    # noqa: BLE001 - any failure is a hold
        return Verdict(outcome="hold", reason=f"judge error: {type(e).__name__}: {e}"[:500])
