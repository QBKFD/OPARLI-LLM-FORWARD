"""Visual agent (LLM on a chart). Stub: always abstains."""

from backend.agents.schemas import Snapshot, Vote


class VisualStub:
    name = "visual"

    def vote(self, snapshot: Snapshot) -> Vote:
        return Vote.abstained(self.name, "stub: visual agent not implemented")
