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
