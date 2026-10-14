"""Debate subgraph (round 2, disabled by default).

    START -> bull | bear -> next_round -(round <= max_rounds)-> bull | bear ...
                                       -(else)--------------> judge -> END

Inputs: the snapshot, the round-1 votes, and, for the judge only, the blind
decision's direction. Nothing else from the run state enters the subgraph.

Rounds are simultaneous:
- Round 1 (the opening): bull and bear each see the snapshot and the votes,
  not each other.
- Round r > 1: each side also sees the opponent's round r-1 turn, and nothing
  else (not its own earlier turns).

The view carries content only: model ids, prompt hashes, tokens and
latencies are blanked on the votes and on the opponent's turn (content_only).

Each turn goes through safe_turn (failures -> empty turn), then through the
evidence check. The judge sees valid claims only. The DebateResult keeps every
claim, with its flags.
"""

from __future__ import annotations

import operator
import time
from typing import Annotated, Callable, Mapping, TypedDict

from langgraph.graph import END, START, StateGraph

from agents.config import DebateConfig
from agents.debate.debaters import Debater, DebaterView, safe_turn
from agents.debate.evidence import check_turn
from agents.debate.judges import Judge, JudgeInput, safe_judge
from agents.schemas import (ROLE_ORDER, DebateResult, Decision, Snapshot, Turn, Vote,
                            outcome_to_direction)

OPPONENT = {"bull": "bear", "bear": "bull"}
META = {"model_id": None, "prompt_hash": None, "tokens_in": None, "tokens_out": None, "latency_ms": None}


def content_only(x):
    """A Vote or Turn without provenance and measurements (model, prompt hash,
    tokens, latency). Debaters see arguments, not timings, so the same inputs
    always give the same view, and the same view hash, under a real clock."""
    return None if x is None else x.model_copy(update=META)


class DebateState(TypedDict, total=False):
    snapshot: Snapshot
    votes: tuple[Vote, ...]
    blind_direction: str
    round: int
    turns: Annotated[list[Turn], operator.add]
    result: DebateResult


def _valid_only(t: Turn) -> Turn:
    return Turn(**{**t.model_dump(exclude={"claims"}), "claims": tuple(c for c in t.claims if c.valid)})


def build_debate_graph(debaters: Mapping[str, Debater], judge: Judge, cfg: DebateConfig,
                       clock: Callable[[], float] = time.monotonic):
    if set(debaters) != {"bull", "bear"}:
        raise ValueError(f"debaters must be exactly bull and bear, got {sorted(debaters)}")

    def make_side(role):
        def side(state: DebateState):
            r = state["round"]
            prev = next((t for t in state.get("turns", ())
                         if t.role == OPPONENT[role] and t.round == r - 1), None)
            view = DebaterView(role=role, round=r, snapshot=state["snapshot"].model_copy(deep=True),
                               round1_votes=tuple(content_only(v) for v in state["votes"]),
                               opponent_previous_turn=content_only(prev))
            turn = safe_turn(debaters[role], view, cfg.turn_timeout_s, clock)
            return {"turns": [check_turn(turn, state["snapshot"])]}
        return side

    def next_round(state: DebateState):
        return {"round": state["round"] + 1}

    def judge_node(state: DebateState):
        turns = tuple(sorted(state["turns"], key=lambda t: (t.round, ROLE_ORDER[t.role])))
        snap = state["snapshot"]
        verdict = safe_judge(judge, JudgeInput(snapshot_hash=snap.hash, blind_direction=state["blind_direction"],
                                               turns=tuple(_valid_only(t) for t in turns)))
        return {"result": DebateResult(
            snapshot_hash=snap.hash, max_rounds=cfg.max_rounds, turns=turns,
            n_valid_bull=sum(t.n_valid() for t in turns if t.role == "bull"),
            n_valid_bear=sum(t.n_valid() for t in turns if t.role == "bear"),
            judge_name=judge.name, outcome=verdict.outcome, reason=verdict.reason)}

    g = StateGraph(DebateState)
    g.add_node("bull", make_side("bull"))
    g.add_node("bear", make_side("bear"))
    g.add_node("next_round", next_round)
    g.add_node("judge", judge_node)
    g.add_edge(START, "bull")
    g.add_edge(START, "bear")
    g.add_edge(["bull", "bear"], "next_round")
    g.add_conditional_edges("next_round",
                            lambda s: ["bull", "bear"] if s["round"] <= cfg.max_rounds else "judge",
                            ["bull", "bear", "judge"])
    g.add_edge("judge", END)
    return g.compile()


def run_debate(compiled, snapshot: Snapshot, votes, blind_direction: str, max_rounds: int) -> DebateResult:
    out = compiled.invoke({"snapshot": snapshot, "votes": tuple(votes), "blind_direction": blind_direction,
                           "round": 1, "turns": []},
                          config={"recursion_limit": 2 * max_rounds + 5})
    return out["result"]


def make_debate_node(debaters: Mapping[str, Debater], judge: Judge, cfg: DebateConfig,
                     clock: Callable[[], float] = time.monotonic):
    """A node for the run graph: reads only snap, votes and the blind direction;
    adds the debate Decision. The blind rows are never touched."""
    compiled = build_debate_graph(debaters, judge, cfg, clock)

    def debate_node(state):
        snap = state["snap"]
        votes = tuple(state["votes"][n] for n in sorted(state["votes"]))
        result = run_debate(compiled, snap, votes, state["decisions"]["blind"].direction, cfg.max_rounds)
        decision = Decision(run_id=state["run_id"], snapshot_hash=snap.hash, arm="debate",
                            direction=outcome_to_direction(result.outcome), reason=result.reason,
                            decided_by=result.judge_name)
        return {"debate_result": result, "decisions": {"debate": decision}}

    return debate_node
