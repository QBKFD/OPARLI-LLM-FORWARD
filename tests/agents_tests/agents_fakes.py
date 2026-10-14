"""Deterministic fakes for tests only. Nothing here is part of the agents package."""

from __future__ import annotations

import threading
import time

from agents.schemas import Vote


class FixedVoter:
    def __init__(self, name, direction, confidence=0.6, reason="fixed"):
        self.name, self.direction, self.confidence, self.reason = name, direction, confidence, reason

    def vote(self, snapshot):
        return Vote(agent=self.name, direction=self.direction, confidence=self.confidence, reason=self.reason)


class RaisingVoter:
    def vote(self, snapshot):
        raise RuntimeError("boom")


class SlowVoter:
    def __init__(self, seconds):
        self.seconds = seconds

    def vote(self, snapshot):
        time.sleep(self.seconds)
        return Vote(agent="slow", direction="long", confidence=1.0)


class ReturnsVoter:
    """Returns whatever it was given (to test output validation)."""

    def __init__(self, out):
        self.out = out

    def vote(self, snapshot):
        return self.out


class RecordingVoter:
    """Records exactly what it was called with, then abstains."""

    def __init__(self, name, store=None):
        self.name, self.store, self.calls = name, store, []
        self.lock = threading.Lock()

    def vote(self, *args, **kwargs):
        saved = None if self.store is None else set(self.store.snapshots)
        with self.lock:
            self.calls.append({"args": args, "kwargs": kwargs, "saved_snapshots": saved})
        return Vote.abstained(self.name, "recording")


class MutatingVoter:
    """Tries to change the snapshot it was given."""

    def vote(self, snapshot):
        snapshot.bars["1m"] = ()
        snapshot.features["injected"] = 1.0
        return Vote.abstained("mutating", "mutated")


# ---------------------------------------------------------------- debate fakes

BAD_REFS = ("level:no-such-level",
            "bars:1m:2099-01-01T00:00:00+00:00..2099-01-01T00:01:00+00:00",
            "feature:made_up",
            "vibes")


class FakeDebater:
    """Deterministic debater: n_valid claims citing the newest 1m bars, plus
    n_invalid claims citing nothing in the snapshot. The text depends on the
    view (round, opponent's previous turn), so a different view gives a
    different turn."""

    def __init__(self, role, n_valid=1, n_invalid=0, concede=False):
        self.role, self.n_valid, self.n_invalid, self.concede = role, n_valid, n_invalid, concede

    def argue(self, view):
        bars = view.snapshot.bars["1m"]
        opp = view.opponent_previous_turn
        note = "opening" if opp is None else f"answers {len(opp.claims)} {opp.role} claims ({opp.n_valid()} valid)"
        claims = []
        for i in range(self.n_valid):
            b = bars[-1 - i]
            claims.append({"text": f"{self.role} r{view.round} #{i}: 1m close {b.close} ({note})",
                           "evidence_ref": f"bars:1m:{b.ts.isoformat()}..{b.ts.isoformat()}"})
        for i in range(self.n_invalid):
            claims.append({"text": f"{self.role} r{view.round} unsupported #{i}",
                           "evidence_ref": BAD_REFS[i % len(BAD_REFS)]})
        return {"role": self.role, "round": view.round, "claims": claims, "conceded": self.concede}


def FakeBull(**kw):
    return FakeDebater("bull", **kw)


def FakeBear(**kw):
    return FakeDebater("bear", **kw)


class RaisingDebater:
    def argue(self, view):
        raise RuntimeError("debater down")


class SlowDebater:
    def __init__(self, seconds):
        self.seconds = seconds

    def argue(self, view):
        time.sleep(self.seconds)
        return {"claims": []}


class ReturnsDebater:
    def __init__(self, out):
        self.out = out

    def argue(self, view):
        return self.out


class RecordingDebater:
    """Records every call exactly (args and kwargs), then delegates."""

    def __init__(self, inner):
        self.inner, self.calls = inner, []
        self.lock = threading.Lock()

    def argue(self, *args, **kwargs):
        with self.lock:
            self.calls.append((args, kwargs))
        return self.inner.argue(*args, **kwargs)


class SpyJudge:
    def __init__(self, inner):
        self.inner, self.inputs = inner, []
        self.name = inner.name

    def judge(self, inp):
        self.inputs.append(inp)
        return self.inner.judge(inp)


class RaisingJudge:
    name = "raising"

    def judge(self, inp):
        raise RuntimeError("judge down")


class ReturnsJudge:
    name = "returns"

    def __init__(self, out):
        self.out = out

    def judge(self, inp):
        return self.out
