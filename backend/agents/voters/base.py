"""Voter interface and the wrapper that turns any failure into an abstain.

A voter gets one argument, a deep copy of the Snapshot, and nothing else: no
graph state and no other vote. It returns a Vote or a plain dict (e.g. parsed
LLM JSON). safe_vote never guesses. Each of these becomes an abstain with the
reason:
- an exception;
- a timeout;
- None;
- a wrong type;
- a vote for another agent or another round;
- anything that fails strict Vote validation.
"""

from __future__ import annotations

import threading
import time
from typing import Callable, Protocol

from pydantic import ValidationError

from backend.agents.schemas import Snapshot, Vote

MAX_REASON = 500


class VoteAgent(Protocol):
    def vote(self, snapshot: Snapshot) -> Vote | dict | None: ...


def call_with_timeout(fn: Callable, timeout_s: float):
    """Run fn in a daemon thread and wait up to timeout_s. Python can't kill a
    thread: a timed-out call keeps running in the background, but its result is
    discarded and it can't block interpreter exit."""
    box: dict = {}

    def target():
        try:
            box["value"] = fn()
        except Exception as e:                # noqa: BLE001 - reported to the caller
            box["error"] = e

    th = threading.Thread(target=target, daemon=True)
    th.start()
    th.join(timeout_s)
    if th.is_alive():
        raise TimeoutError(f"no result after {timeout_s}s")
    if "error" in box:
        raise box["error"]
    if "value" not in box:
        raise RuntimeError("call ended without a result")
    return box["value"]


def _short(e: BaseException) -> str:
    return f"{type(e).__name__}: {e}"[:MAX_REASON]


def validate_vote(name: str, out, latency_ms: float | None = None) -> Vote:
    def abstain(reason):
        return Vote.abstained(name, reason[:MAX_REASON], latency_ms=latency_ms)

    if out is None:
        return abstain("invalid vote: no vote returned")
    if isinstance(out, Vote):
        data = out.model_dump()
    elif isinstance(out, dict):
        data = dict(out)
    else:
        return abstain(f"invalid vote: expected Vote or dict, got {type(out).__name__}")
    if data.get("agent", name) != name:
        return abstain(f"invalid vote: names agent {data.get('agent')!r}, expected {name!r}")
    if data.get("round", 1) != 1:
        return abstain(f"invalid vote: round {data.get('round')!r}, a round-1 vote has round 1")
    data["agent"], data["latency_ms"] = name, latency_ms
    try:
        return Vote.model_validate(data, strict=True)
    except ValidationError as e:
        err = e.errors()[0]
        return abstain(f"invalid vote: {'.'.join(map(str, err['loc'])) or 'vote'}: {err['msg']}")


def safe_vote(name: str, agent: VoteAgent, snapshot: Snapshot, timeout_s: float,
              clock: Callable[[], float] = time.monotonic) -> Vote:
    view = snapshot.model_copy(deep=True)
    t0 = clock()

    def ms():
        return max(0.0, (clock() - t0) * 1000.0)

    try:
        out = call_with_timeout(lambda: agent.vote(view), timeout_s)
    except TimeoutError as e:
        return Vote.abstained(name, f"timeout: {e}", latency_ms=ms())
    except Exception as e:                    # noqa: BLE001 - any failure is an abstain
        return Vote.abstained(name, f"error: {_short(e)}", latency_ms=ms())
    return validate_vote(name, out, ms())
