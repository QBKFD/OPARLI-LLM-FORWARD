"""The run graph (LangGraph).

    trigger -> snapshot -(stale)--------------------------------------------> store -> END
                  |
                 gate -(no-go)---------------------------------------------> store
                  |  fan-out, one superstep
        visual | technical | sentiment     each gets only a copy of the Snapshot
                  |  fan-in (waits for all)
              aggregator -> blind Decision
                  |
           [debate, if config.debate.enabled] -> debate Decision
                  |
                 risk (stub, pass-through)
                  |
                 store

- The snapshot is saved in the snapshot node, before any agent node runs.
- Everything else is written by the store node, once, in one transaction.
- Config, voters, store, datafeed db and clock are bound when the graph is
  built, not kept in the state.
- Only voter failures are absorbed (as abstains). A failure in the snapshot,
  gate, aggregator or store raises out of run_once (crash-only).
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Annotated, Callable, Mapping, TypedDict

from langgraph.graph import END, START, StateGraph

from agents import __version__
from agents.config import AgentsConfig
from agents.debate import make_debate_node, make_judge
from agents.nodes import gate
from agents.nodes.aggregator import aggregate_v0
from agents.nodes.risk import apply_risk
from agents.schemas import DebateResult, Decision, Snapshot, TriggerEvent, Vote
from agents.snapshot import Stale, floor_minute, prepare_snapshot
from agents.store.base import AgentStore, RunRecord
from agents.voters import default_voters, safe_vote

log = logging.getLogger(__name__)

ARM_ORDER = {"blind": 0, "debate": 1}


def merge_disjoint(a: dict | None, b: dict | None) -> dict:
    """Reducer for parallel writers: keys must not repeat (one vote per agent,
    one decision per arm)."""
    a, b = a or {}, b or {}
    dup = set(a) & set(b)
    if dup:
        raise ValueError(f"duplicate keys in state update: {sorted(dup)}")
    return {**a, **b}


class RunState(TypedDict, total=False):
    run_id: str
    event: TriggerEvent
    as_of: object
    status: str                                            # running | skipped | done
    skip_reason: str | None
    snap: Snapshot
    votes: Annotated[dict[str, Vote], merge_disjoint]       # round 1, by agent
    decisions: Annotated[dict[str, Decision], merge_disjoint]   # by arm, before risk
    debate_result: DebateResult
    final_decisions: dict[str, Decision]                   # by arm, after risk


def build_graph(config: AgentsConfig, store: AgentStore, db=None, *,
                voters: Mapping[str, object] | None = None,
                debaters: Mapping[str, object] | None = None,
                judge=None,
                clock: Callable[[], float] = time.monotonic):
    """db: the datafeed store (path or sqlite3.Connection; default $DATAFEED_DB).
    voters: name -> VoteAgent (default: the three stubs).
    debaters: {"bull": Debater, "bear": Debater}. Needed only when
        config.debate.enabled; no real debaters ship yet.
    judge: default make_judge(config.debate.judge). The name is checked even
        when the debate is disabled, so a bad config fails at build time."""
    voters = dict(default_voters() if voters is None else voters)
    if not voters:
        raise ValueError("at least one voter")
    judge = judge if judge is not None else make_judge(config.debate.judge)
    debate_node = None
    if config.debate.enabled:
        if debaters is None:
            raise ValueError("debate.enabled needs debaters (bull, bear); none ship in agents/ yet")
        debate_node = make_debate_node(debaters, judge, config.debate, clock)
    names = sorted(voters)
    config_json, config_hash = config.to_json(), config.config_hash()

    def trigger_node(state: RunState):
        ev = state["event"]
        if not isinstance(ev, TriggerEvent):
            ev = TriggerEvent.model_validate(ev)
        return {"event": ev, "run_id": state.get("run_id") or uuid.uuid4().hex,
                "as_of": floor_minute(ev.t), "status": "running", "skip_reason": None}

    def snapshot_node(state: RunState):
        out = prepare_snapshot(state["event"].t, config.snapshot, db, store)
        if isinstance(out, Stale):
            return {"status": "skipped", "skip_reason": out.reason}
        return {"snap": out}

    def gate_node(state: RunState):
        reason = gate.check(state["snap"], config.gate)
        if reason:
            log.info("run %s skipped by gate: %s", state["run_id"], reason)
            return {"status": "skipped", "skip_reason": f"gate: {reason}"}
        return {}

    def make_voter(name, agent):
        def voter_node(state: RunState):
            return {"votes": {name: safe_vote(name, agent, state["snap"], config.node_timeout_s, clock)}}
        return voter_node

    def aggregator_node(state: RunState):
        return {"decisions": {"blind": aggregate_v0(state["votes"], state["run_id"], state["snap"].hash)}}

    def risk_node(state: RunState):
        return {"final_decisions": {arm: apply_risk(d) for arm, d in state["decisions"].items()}}

    def store_node(state: RunState):
        skipped = state["status"] == "skipped"
        snap = state.get("snap")
        run = RunRecord(run_id=state["run_id"], trigger=state["event"], as_of=state["as_of"],
                        status="skipped" if skipped else "done", skip_reason=state.get("skip_reason"),
                        snapshot_hash=snap.hash if snap is not None else None,
                        config_json=config_json, config_hash=config_hash, code_version=__version__)
        votes = [] if skipped else [state["votes"][n] for n in sorted(state["votes"])]
        decisions = [] if skipped else sorted(state["final_decisions"].values(), key=lambda d: ARM_ORDER[d.arm])
        store.save_run(run, votes, decisions, debate=None if skipped else state.get("debate_result"))
        return {"status": run.status}

    g = StateGraph(RunState)
    g.add_node("trigger", trigger_node)
    g.add_node("snapshot", snapshot_node)
    g.add_node("gate", gate_node)
    for n in names:
        g.add_node(n, make_voter(n, voters[n]))
    g.add_node("aggregator", aggregator_node)
    g.add_node("risk", risk_node)
    g.add_node("store", store_node)

    g.add_edge(START, "trigger")
    g.add_edge("trigger", "snapshot")
    g.add_conditional_edges("snapshot", lambda s: "store" if s["status"] == "skipped" else "gate",
                            ["gate", "store"])
    g.add_conditional_edges("gate", lambda s: "store" if s["status"] == "skipped" else list(names),
                            [*names, "store"])
    g.add_edge(names, "aggregator")
    if debate_node is not None:
        g.add_node("debate", debate_node)
        g.add_edge("aggregator", "debate")
        g.add_edge("debate", "risk")
    else:
        g.add_edge("aggregator", "risk")
    g.add_edge("risk", "store")
    g.add_edge("store", END)
    return g.compile()


def run_once(graph, event: TriggerEvent, run_id: str | None = None) -> RunState:
    state = {"event": event}
    if run_id is not None:
        state["run_id"] = run_id
    return graph.invoke(state)


__all__ = ["RunState", "build_graph", "run_once", "merge_disjoint"]
