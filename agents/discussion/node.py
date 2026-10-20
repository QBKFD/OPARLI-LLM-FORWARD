"""The discussion step of the run graph: runs the subgraph, or records why
nobody spoke. It never raises for a configuration problem."""

from __future__ import annotations

import time
from typing import Any, Callable, Mapping

from agents.discussion.subgraph import Discussant, build_discussion_graph, no_discussion, run_discussion, speakers_of
from agents.owner_config import ConfigProblem, OwnerConfig
from agents.prompting.files import PromptFile, PromptProblem
from agents.schemas import DiscussionResult, Snapshot, Vote


def discussants_of(voters: Mapping[str, Any]) -> dict[str, Discussant]:
    """The voters that can discuss (they have a `discuss` method: the LLM agents)."""
    return {n: v for n, v in voters.items() if callable(getattr(v, "discuss", None))}


def make_discussion(discussants: Mapping[str, Discussant], owner: OwnerConfig,
                    prompt: PromptFile | PromptProblem, timeout_s: float,
                    clock: Callable[[], float] = time.monotonic) -> Callable[[Snapshot, Mapping[str, Vote]],
                                                                               DiscussionResult]:
    """A function (snapshot, round-1 votes) -> DiscussionResult. Settings are
    read once, at graph build time, like the epoch."""
    settings = owner.discussion()
    rounds = 0 if isinstance(settings, ConfigProblem) else settings.rounds
    # the most basic reason first: no discussants, then the config, then the prompt
    if not discussants:
        return lambda snap, votes: no_discussion(snap, votes, rounds, "no LLM discussants in this graph")
    if isinstance(settings, ConfigProblem):
        why = settings.reason
        return lambda snap, votes: no_discussion(snap, votes, 0, why)
    if isinstance(prompt, PromptProblem):
        return lambda snap, votes: no_discussion(snap, votes, rounds, prompt.reason)
    compiled = build_discussion_graph(discussants, rounds, settings.early_stop, timeout_s, clock)

    def discuss(snap: Snapshot, votes: Mapping[str, Vote]) -> DiscussionResult:
        speakers = speakers_of(votes, discussants)
        if not speakers:
            return no_discussion(snap, votes, rounds, "every discussant abstained in round 1")
        return run_discussion(compiled, snap, votes, speakers, rounds)

    return discuss


__all__ = ["discussants_of", "make_discussion"]
