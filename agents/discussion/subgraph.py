"""The discussion subgraph (docs/AGENTS_ARCHITECTURE.md §5.2), replacing the
bull/bear debate.

    START -> <one node per LLM agent> -> next_round -(go on)-> <agents> ...
                                                    -(done)--> END

- **Speakers:** the discussants (agents with a `discuss` method, the LLM
  agents) whose round-1 vote didn't abstain. The others (GEX, the stubs,
  abstained agents) keep their round-1 vote, and every speaker sees it.
- **Simultaneous rounds:** in discussion round r, every speaker sees the
  others' latest votes from before the round. It doesn't see what the others
  say in the same round.
- **Content only:** others' votes are shown without model ids, prompt hashes,
  tokens or latency.
- Each proposal goes through safe_discuss (a failure or timeout is an
  abstain), then through the vote-change rule (rule.py).
- **Early stop:** with `discussion.early_stop`, the discussion ends after a
  round in which no vote changed (an accepted change). Otherwise it runs
  `discussion.rounds` rounds.

Nothing else from the run state enters the subgraph.
"""

from __future__ import annotations

import operator
import time
from typing import Annotated, Any, Callable, Mapping, Protocol, TypedDict

from langgraph.graph import END, START, StateGraph

from agents.discussion.rule import apply_change_rule
from agents.schemas import DiscussionResult, DiscussionView, Snapshot, Vote
from agents.voters.base import MAX_REASON, call_with_timeout

CONTENT_ONLY = {"model_id": None, "prompt_hash": None, "tokens_in": None, "tokens_out": None, "latency_ms": None,
                "call_id": None}


class Discussant(Protocol):
    def discuss(self, view: DiscussionView) -> Vote: ...


def content_only(v: Vote) -> Vote:
    """A vote without provenance and measurements: arguments, not timings."""
    return v.model_copy(update=CONTENT_ONLY)


def safe_discuss(name: str, agent: Discussant, view: DiscussionView, timeout_s: float,
                 clock: Callable[[], float] = time.monotonic) -> Vote:
    """The discussant's proposal. Any exception, timeout, wrong type, or a vote
    for another agent or round is an abstain with the reason, never a guess."""
    round_ = view.round + 1
    t0 = clock()

    def abstain(reason: str) -> Vote:
        return Vote.abstained(name, reason[:MAX_REASON], round=round_, latency_ms=max(0.0, (clock() - t0) * 1000))

    try:
        out = call_with_timeout(lambda: agent.discuss(view.model_copy(deep=True)), timeout_s)
    except TimeoutError as e:
        return abstain(f"timeout: {e}")
    except Exception as e:                    # noqa: BLE001 - any failure is an abstain
        return abstain(f"error: {type(e).__name__}: {e}")
    if not isinstance(out, Vote):
        return abstain(f"invalid proposal: expected Vote, got {type(out).__name__}")
    if out.agent != name or out.round != round_:
        return abstain(f"invalid proposal: agent {out.agent!r} round {out.round}, expected {name!r} round {round_}")
    return out


class DiscussionState(TypedDict, total=False):
    snapshot: Snapshot
    round1: dict[str, Vote]
    latest: dict[str, Vote]
    speakers: tuple[str, ...]
    round: int                                    # discussion round, 1-based
    votes: Annotated[list[Vote], operator.add]
    result: DiscussionResult


def speakers_of(round1: Mapping[str, Vote], discussants: Mapping[str, Discussant]) -> tuple[str, ...]:
    return tuple(sorted(n for n in discussants if n in round1 and not round1[n].abstain))


def build_discussion_graph(discussants: Mapping[str, Discussant], max_rounds: int, early_stop: bool,
                           timeout_s: float, clock: Callable[[], float] = time.monotonic) -> Any:
    names = sorted(discussants)
    if not names:
        raise ValueError("a discussion needs at least one discussant")

    def make_speaker(name: str) -> Any:
        def speak(state: DiscussionState) -> dict:
            if name not in state["speakers"]:
                return {}
            r, latest = state["round"], state["latest"]
            view = DiscussionView(agent=name, round=r, max_rounds=max_rounds, snapshot=state["snapshot"],
                                  own_previous=content_only(latest[name]),
                                  others=tuple(content_only(latest[o]) for o in sorted(latest) if o != name))
            proposal = safe_discuss(name, discussants[name], view, timeout_s, clock)
            return {"votes": [apply_change_rule(latest[name], proposal, r + 1)]}
        return speak

    def next_round(state: DiscussionState) -> dict:
        r = state["round"]
        this = {v.agent: v for v in state["votes"] if v.round == r + 1}
        latest = {**state["latest"], **this}
        done = r >= max_rounds or (early_stop and not any(v.changed for v in this.values()))
        if not done:
            return {"latest": latest, "round": r + 1}
        votes = tuple(sorted(state["votes"], key=lambda v: (v.round, v.agent)))
        return {"latest": latest, "result": DiscussionResult(
            snapshot_hash=state["snapshot"].hash, max_rounds=max_rounds, rounds_run=r, stopped_early=r < max_rounds,
            speakers=state["speakers"], votes=votes, final=latest)}

    g = StateGraph(DiscussionState)
    for n in names:
        g.add_node(n, make_speaker(n))
        g.add_edge(START, n)
    g.add_node("next_round", next_round)
    g.add_edge(names, "next_round")
    g.add_conditional_edges("next_round", lambda s: END if "result" in s else list(names), [*names, END])
    return g.compile()


def run_discussion(compiled: Any, snapshot: Snapshot, round1: Mapping[str, Vote], speakers: tuple[str, ...],
                   max_rounds: int) -> DiscussionResult:
    out = compiled.invoke({"snapshot": snapshot, "round1": dict(round1), "latest": dict(round1),
                           "speakers": speakers, "round": 1, "votes": []},
                          config={"recursion_limit": 4 * max_rounds + 10})
    return out["result"]


def no_discussion(snapshot: Snapshot, round1: Mapping[str, Vote], max_rounds: int, why: str) -> DiscussionResult:
    """Nobody speaks: the final votes are the round-1 votes, and `why` is kept."""
    return DiscussionResult(snapshot_hash=snapshot.hash, max_rounds=max_rounds, rounds_run=0, stopped_early=False,
                            skip_reason=why, speakers=(), votes=(), final=dict(round1))


__all__ = ["Discussant", "build_discussion_graph", "content_only", "no_discussion", "run_discussion",
           "safe_discuss", "speakers_of"]
