"""GEX agent (plain code). It abstains until an options/GEX data source
exists (docs/AGENTS_ARCHITECTURE.md §4.2, §12 Q5)."""

from agents.schemas import Snapshot, Vote

NO_SOURCE = "no options data source"


class GexAgent:
    name = "gex"

    def vote(self, snapshot: Snapshot) -> Vote:
        return Vote.abstained(self.name, NO_SOURCE)
