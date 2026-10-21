"""Round-1 voters. Each sees only the Snapshot (blind to the others)."""

from backend.agents.voters.base import VoteAgent, safe_vote
from backend.agents.voters.gex import GexAgent
from backend.agents.voters.sentiment import SentimentStub
from backend.agents.voters.technical import TechnicalStub
from backend.agents.voters.visual import VisualStub


def default_voters() -> dict:
    """The four round-1 agents. Without an LLM client, visual, technical and
    sentiment are stubs, and GEX (code) abstains until options data exists."""
    return {"visual": VisualStub(), "technical": TechnicalStub(), "sentiment": SentimentStub(), "gex": GexAgent()}


__all__ = ["VoteAgent", "safe_vote", "default_voters", "GexAgent", "VisualStub", "TechnicalStub", "SentimentStub"]
