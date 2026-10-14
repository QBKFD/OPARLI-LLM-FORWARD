"""Technical agent (plain code). Stub: always abstains."""

from agents.schemas import Snapshot, Vote


class TechnicalStub:
    name = "technical"

    def vote(self, snapshot: Snapshot) -> Vote:
        return Vote.abstained(self.name, "stub: technical agent not implemented")
