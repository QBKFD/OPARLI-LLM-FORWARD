"""Round-1 voters. Each sees only the Snapshot (blind to the others)."""

from agents.voters.base import VoteAgent, safe_vote
from agents.voters.gex import GexAgent
from agents.voters.sentiment import SentimentStub
from agents.voters.technical import TechnicalStub
from agents.voters.visual import VisualStub


def default_voters() -> dict:
    """The four round-1 agents. Without an LLM client, visual, technical and
    sentiment are stubs, and GEX (code) abstains until options data exists."""
    return {"visual": VisualStub(), "technical": TechnicalStub(), "sentiment": SentimentStub(), "gex": GexAgent()}


__all__ = ["VoteAgent", "safe_vote", "default_voters", "GexAgent", "VisualStub", "TechnicalStub", "SentimentStub"]
