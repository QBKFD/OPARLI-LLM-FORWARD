"""Deterministic fakes for tests only. Nothing here is part of the agents package."""

from __future__ import annotations

import threading
import time

from agents.schemas import Evidence, Vote


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


# ---------------------------------------------------------------- discussion fakes

def bar_ref(snapshot, k=1, tf="1m"):
    """A ref to the k-th newest bar of tf: resolves in the snapshot."""
    b = snapshot.bars[tf][-k]
    return f"bars:{tf}:{b.ts.isoformat()}..{b.ts.isoformat()}"


class FakeDiscussant:
    """A voter that also discusses, deterministically.
    - vote(): `direction` at `confidence`, citing the newest 1m bar.
    - discuss(view): `script(view)` returns the proposal as a dict (direction,
      confidence, refs) or a Vote, or raises. Default: restate the previous
      vote. Every view is recorded."""

    def __init__(self, name, direction="long", confidence=0.6, script=None, abstain=False):
        self.name, self.direction, self.confidence = name, direction, confidence
        self.script, self.abstain = script, abstain
        self.views = []
        self._lock = threading.Lock()

    def vote(self, snapshot):
        if self.abstain:
            return Vote.abstained(self.name, "fake abstain")
        return Vote(agent=self.name, direction=self.direction, confidence=self.confidence, reason="r1",
                    evidence=(Evidence(ref=bar_ref(snapshot), claim="newest bar", valid=True),))

    def discuss(self, view):
        with self._lock:
            self.views.append(view)
        out = self.script(view) if self.script else {"direction": view.own_previous.direction,
                                                     "confidence": view.own_previous.confidence,
                                                     "refs": [e.ref for e in view.own_previous.evidence]}
        if isinstance(out, Vote):
            return out
        from agents.evidence import check_ref, check_vote_ref
        shown = {(v.agent, v.round) for v in (*view.others, view.own_previous)}
        ev = []
        for ref in out.get("refs", ()):
            why = check_vote_ref(ref, shown) if ref.startswith("vote:") else check_ref(ref, view.snapshot)
            ev.append(Evidence(ref=ref, claim="c", valid=why is None, invalid_reason=why))
        return Vote(agent=self.name, round=view.round + 1, direction=out["direction"],
                    confidence=out["confidence"], reason=out.get("reason", f"round {view.round}"),
                    evidence=tuple(ev), call_id=f"{self.name}-{view.round}")
