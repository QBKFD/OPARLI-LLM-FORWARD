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
- The epoch (agents/epoch.py) is computed once, at build time, from the
  owner's prompt files and agents.toml. Its id, the config's SHA-256 and every
  prompt file's SHA-256 are stamped on every decision; the run row carries the
  id and the inputs it hashes. A missing or invalid prompt or config never
  fails the build: it is part of the epoch, and the agent that needs it
  abstains.
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
from agents.epoch import Epoch, compute_epoch, last_stored_inputs, version_warnings
from agents.hashing import canonical_json
from agents.nodes import gate
from agents.nodes.aggregator import aggregate_v0
from agents.nodes.risk import apply_risk
from agents.owner_config import ConfigProblem, OwnerConfig
from agents.prompting.files import load_all
from agents.schemas import DebateResult, Decision, Snapshot, TriggerEvent, Vote
from agents.prereg import ExperimentError, require_prereg
from agents.snapshot import Stale, _utcnow, floor_minute, prepare_snapshot
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
    epoch_id: str                                          # the graph's epoch, the same for every run
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
                clock: Callable[[], float] = time.monotonic,
                wall_clock: Callable[[], object] = _utcnow,
                owner_config: OwnerConfig | None = None,
                prompts_dir=None,
                journal_epoch: int | None = None,
                llm_client=None,
                llm_ledger=None,
                image_bytes=None):
    """db: the datafeed store (path or sqlite3.Connection; default $DATAFEED_DB).
    voters: name -> VoteAgent (default: the three stubs).
    debaters: {"bull": Debater, "bear": Debater}. Needed only when
        config.debate.enabled; no real debaters ship yet.
    judge: default make_judge(config.debate.judge). The name is checked even
        when the debate is disabled, so a bad config fails at build time.
    clock: monotonic clock for latencies. wall_clock: UTC now, for the live-mode
        "t within 120 s" rule.
    owner_config: the owner's agents.toml (default: OwnerConfig.load(), i.e.
        $AGENTS_CONFIG). prompts_dir: default agents/prompts. journal_epoch:
        the scanner journal epoch the triggers come from, when known. All three
        go into the epoch.
    llm_client: an LLMClient (agents/llm). With one, and voters=None, the round-1
        voters visual, technical and sentiment are LLMVoteAgents built from the
        same prompts and config as the epoch. Without one they stay stubs.
        llm_ledger: default SqliteLedger(store) for a SQLite store, else in
        memory. image_bytes: chart PNG bytes by SHA-256 (no chart store yet).
    Live mode without an experiment, or an experiment without its PREREG,
    fails here at build time, and again in every snapshot."""
    if config.snapshot.experiment_id is not None:
        require_prereg(config.snapshot.experiment_id)
    elif config.snapshot.mode == "live":
        raise ExperimentError("live mode needs snapshot.experiment_id with a PREREG under agents/experiments/")
    owner = owner_config or OwnerConfig.load()
    prompts = load_all(prompts_dir)
    epoch = compute_epoch(prompts, owner, journal_epoch=journal_epoch)
    if voters is None:
        voters = default_voters()
        if llm_client is not None:
            voters.update(_llm_voters(owner, prompts, store, llm_client, llm_ledger, image_bytes, epoch.id,
                                      config.node_timeout_s))
    voters = dict(voters)
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
    epoch_json = canonical_json(epoch.inputs)
    _warn_versions(store, epoch)
    stamp = {"epoch_id": epoch.id, "owner_config_sha256": epoch.owner_config_sha256,
             "prompts_sha256": epoch.prompts_sha256}

    def trigger_node(state: RunState):
        ev = state["event"]
        if not isinstance(ev, TriggerEvent):
            ev = TriggerEvent.model_validate(ev)
        return {"event": ev, "run_id": state.get("run_id") or uuid.uuid4().hex, "epoch_id": epoch.id,
                "as_of": floor_minute(ev.t), "status": "running", "skip_reason": None}

    def snapshot_node(state: RunState):
        ev = state["event"]
        out = prepare_snapshot(ev.t, config.snapshot, db, store, wall_clock, payload=ev.payload)
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
        snap = state["snap"]
        d = aggregate_v0(state["votes"], state["run_id"], snap.hash, snap.experiment_id)
        return {"decisions": {"blind": d.model_copy(update=stamp)}}

    def risk_node(state: RunState):
        return {"final_decisions": {arm: apply_risk(d) for arm, d in state["decisions"].items()}}

    def store_node(state: RunState):
        skipped = state["status"] == "skipped"
        snap = state.get("snap")
        run = RunRecord(run_id=state["run_id"], trigger=state["event"], as_of=state["as_of"],
                        status="skipped" if skipped else "done", skip_reason=state.get("skip_reason"),
                        snapshot_hash=snap.hash if snap is not None else None,
                        config_json=config_json, config_hash=config_hash, code_version=__version__,
                        epoch_id=epoch.id, epoch_inputs_json=epoch_json)
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
        inner = debate_node

        def stamped_debate_node(state: RunState):
            out = inner(state)
            return {**out, "decisions": {arm: d.model_copy(update=stamp) for arm, d in out["decisions"].items()}}

        g.add_node("debate", stamped_debate_node)
        g.add_edge("aggregator", "debate")
        g.add_edge("debate", "risk")
    else:
        g.add_edge("aggregator", "risk")
    g.add_edge("risk", "store")
    g.add_edge("store", END)
    return g.compile()


def _llm_voters(owner, prompts, store, client, ledger, image_bytes, epoch_id, node_timeout_s) -> dict:
    """The LLM round-1 voters, sharing one ledger and one budget."""
    from agents.llm import Budget, CallDeps, MemoryLedger, SqliteLedger, make_llm_voters

    if ledger is None:
        ledger = SqliteLedger(store) if hasattr(store, "con") else MemoryLedger()
    llm = owner.llm()
    if not isinstance(llm, ConfigProblem) and (llm.max_retries + 1) * llm.timeout_s > node_timeout_s:
        log.warning("llm: %d attempts x %.0f s can exceed node_timeout_s %.0f s; the node timeout would abstain "
                    "first", llm.max_retries + 1, llm.timeout_s, node_timeout_s)
    deps = CallDeps(client=client, ledger=ledger, budget=Budget(ledger), image_bytes=image_bytes)
    return make_llm_voters(owner, prompts, deps, epoch_id)


def _warn_versions(store: AgentStore, epoch: Epoch) -> None:
    """Log prompts whose bytes changed since the store's last run while their
    front-matter version didn't (AA10). The epoch changes either way."""
    path = getattr(store, "path", None)
    if not path:
        return
    try:
        old = last_stored_inputs(path)
    except Exception as e:                   # noqa: BLE001 - a warning check never fails the build
        log.warning("epoch: could not read the last stored epoch from %s: %s", path, e)
        return
    for w in version_warnings(old or {}, epoch.inputs):
        log.warning("epoch: %s", w)


def run_once(graph, event: TriggerEvent, run_id: str | None = None) -> RunState:
    state = {"event": event}
    if run_id is not None:
        state["run_id"] = run_id
    return graph.invoke(state)


__all__ = ["RunState", "build_graph", "run_once", "merge_disjoint"]
