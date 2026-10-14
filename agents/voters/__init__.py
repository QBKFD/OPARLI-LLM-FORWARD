"""Round-1 voters. Each sees only the Snapshot (blind to the others)."""

from agents.voters.base import VoteAgent, safe_vote
from agents.voters.sentiment import SentimentStub
from agents.voters.technical import TechnicalStub
from agents.voters.visual import VisualStub


def default_voters() -> dict:
    """The three round-1 agents. All stubs in this PR: they abstain."""
    return {"visual": VisualStub(), "technical": TechnicalStub(), "sentiment": SentimentStub()}


__all__ = ["VoteAgent", "safe_vote", "default_voters", "VisualStub", "TechnicalStub", "SentimentStub"]
