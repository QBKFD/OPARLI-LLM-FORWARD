"""Sentiment agent (LLM on calendar events; no calendar source exists yet). Stub: always abstains."""

from backend.agents.schemas import Snapshot, Vote


class SentimentStub:
    name = "sentiment"

    def vote(self, snapshot: Snapshot) -> Vote:
        return Vote.abstained(self.name, "stub: sentiment agent not implemented")
