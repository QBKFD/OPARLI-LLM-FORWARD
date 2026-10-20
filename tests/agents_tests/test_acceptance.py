"""Step 7: the acceptance list, through the full graph + SQLite agent store, on
replay data. One test per item (A1..A9), plus a run over one real parquet day
(skipped when the untracked parquet is absent).

A1 a snapshot never contains a bar >= t
A2 the same inputs give the same snapshot hash
A3 a node that raises becomes an abstain and the graph still finishes
A4 no discussant -> the discussion is skipped with its reason; both decisions exist
A5 a discussion -> two decisions on the same snapshot (blind, discussion); round-1 rows unchanged
A6 a ref that doesn't resolve is kept as invalid and never counts as evidence for a change
A7 a discussant error -> its vote is carried forward and the discussion still finishes
A8 a discussant sees nothing outside the snapshot + its own previous vote + the others' latest
   votes, content only
A9 same snapshot + same fake discussants -> identical DiscussionResult
(A4-A9 replaced the bull/bear debate items in agents v2, job 4.)
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from agents.config import AgentsConfig
from agents.graph import build_graph, run_once
from agents.schemas import TriggerEvent
from agents.store import SqliteAgentStore
from agents.voters import default_voters
from agents_fakes import FakeDiscussant, FixedVoter, RaisingVoter, bar_ref
from agents_testkit import DATA_END, DATA_START, make_replay_db, write_test_prompts

UTC = timezone.utc
T = datetime(2025, 6, 11, 14, 30, 30, tzinfo=UTC)
FIXED = lambda: 0.0                                           # noqa: E731
OFF = AgentsConfig.model_validate({"snapshot": {"mode": "replay"}})
PARQUET = Path(__file__).resolve().parents[2] / "database/ohlcv_data/XAUUSD_1min_UTC_clean.parquet"


def ev(t=T, i="1"):
    return TriggerEvent(source="acceptance", t=t, trigger_id=i)


def long_voters():
    v = default_voters()
    v.update(visual=FixedVoter("visual", "long"), technical=FixedVoter("technical", "long"))
    return v


@pytest.fixture
def store(tmp_path):
    s = SqliteAgentStore(str(tmp_path / "agents.sqlite"))
    yield s
    s.close()


def rows(store, table, run_id, drop=("run_id", "created_ts")):
    cur = store.con.execute(f"SELECT * FROM {table} WHERE run_id = ? ORDER BY 1, 2, 3", (run_id,))
    return [{k: r[k] for k in r.keys() if k not in drop} for r in cur]


def discussion_graph(store, db, tmp_path, visual, technical):
    v = long_voters()
    v.update(visual=visual, technical=technical)
    return build_graph(OFF, store, db, voters=v, clock=FIXED, prompts_dir=write_test_prompts(tmp_path / "prompts"))


# A1 ------------------------------------------------------------------------

def test_a1_snapshot_never_contains_a_bar_at_or_after_t(store, replay_db):
    g = build_graph(OFF, store, replay_db)
    t, n_done = DATA_START + timedelta(hours=6, seconds=41), 0
    while t < DATA_END + timedelta(hours=2):                  # every 37 min, off the minute, past the data end
        out = run_once(g, ev(t, t.isoformat()))
        if out.get("snap") is not None:
            snap = store.load_snapshot(out["snap"].hash)       # what was stored, not the in-memory object
            for series in snap.bars.values():
                assert all(b.ts < snap.as_of <= t for b in series)
            n_done += out["status"] == "done"
        t += timedelta(minutes=37, seconds=13)
    assert n_done > 50


# A2 ------------------------------------------------------------------------

def test_a2_same_inputs_same_snapshot_hash(tmp_path, replay_db):
    a = SqliteAgentStore(str(tmp_path / "a.sqlite"))
    b = SqliteAgentStore(str(tmp_path / "b.sqlite"))
    other_db = make_replay_db(tmp_path)                       # datafeed store rebuilt from the same parquet
    ha = run_once(build_graph(OFF, a, replay_db), ev())["snap"].hash
    hb = run_once(build_graph(OFF, b, other_db), ev(T.replace(second=2)))["snap"].hash
    assert ha == hb
    assert a.con.execute("SELECT content_json FROM snapshots").fetchone()[0] == \
        b.con.execute("SELECT content_json FROM snapshots").fetchone()[0]
    assert run_once(build_graph(OFF, a, replay_db), ev(T + timedelta(minutes=1)))["snap"].hash != ha


# A3 ------------------------------------------------------------------------

def test_a3_raising_node_becomes_abstain_and_graph_finishes(store, replay_db):
    voters = long_voters()
    voters["technical"] = RaisingVoter()
    out = run_once(build_graph(OFF, store, replay_db, voters=voters, clock=FIXED), ev(), run_id="a3")
    assert out["status"] == "done"
    tech = [r for r in rows(store, "votes", "a3") if r["agent"] == "technical"][0]
    assert (tech["abstain"], tech["direction"], tech["confidence"], tech["abstain_reason"]) == \
        (1, "none", 0.0, "error: RuntimeError: boom")
    assert store.load_run("a3")["run"].status == "done"
    assert [r["arm"] for r in rows(store, "decisions", "a3")] == ["blind", "discussion"]


# A4 ------------------------------------------------------------------------

def test_a4_no_discussant_skips_the_discussion_with_its_reason(store, replay_db):
    out = run_once(build_graph(OFF, store, replay_db, voters=long_voters(), clock=FIXED), ev(), run_id="a4")
    dec = rows(store, "decisions", "a4")
    assert [d["arm"] for d in dec] == ["blind", "discussion"]
    assert dec[0]["direction"] == dec[1]["direction"] == "long"
    disc = rows(store, "discussions", "a4")[0]
    assert (disc["rounds_run"], disc["skip_reason"]) == (0, "no LLM discussants in this graph")
    assert all(v["round"] == 1 for v in rows(store, "votes", "a4")) and out["discussion"].votes == ()


# A5 ------------------------------------------------------------------------

def test_a5_two_decisions_on_one_snapshot_round1_unchanged(store, replay_db, tmp_path):
    flip = lambda v: {"direction": "short", "confidence": 0.7, "refs": [bar_ref(v.snapshot, 4)]}   # noqa: E731
    run_once(discussion_graph(store, replay_db, tmp_path, FakeDiscussant("visual", "long", script=flip),
                              FakeDiscussant("technical", "long", script=flip)), ev(), run_id="on")
    dec = rows(store, "decisions", "on")
    assert [d["arm"] for d in dec] == ["blind", "discussion"] and dec[0]["snapshot_hash"] == dec[1]["snapshot_hash"]
    assert (dec[0]["direction"], dec[1]["direction"]) == ("long", "short")   # both changed, citing a new bar
    plain = SqliteAgentStore(str(tmp_path / "plain.sqlite"))
    run_once(build_graph(OFF, plain, replay_db, voters=long_voters(), clock=FIXED), ev(), run_id="on")
    keep = ("agent", "direction", "confidence", "abstain")
    r1 = lambda st: [{k: r[k] for k in keep} for r in rows(st, "votes", "on") if r["round"] == 1]   # noqa: E731
    assert r1(store) == r1(plain)                                           # round-1 rows are the same
    plain.close()


# A6 ------------------------------------------------------------------------

def test_a6_an_unresolved_ref_is_invalid_and_never_counts(store, replay_db, tmp_path):
    bad = lambda v: {"direction": "short", "confidence": 0.9, "refs": ["level:no-such-level"]}   # noqa: E731
    run_once(discussion_graph(store, replay_db, tmp_path, FakeDiscussant("visual", "long", script=bad),
                              FakeDiscussant("technical", "long")), ev(), run_id="a6")
    v = [r for r in rows(store, "votes", "a6") if r["agent"] == "visual" and r["round"] == 2][0]
    assert (v["direction"], v["change_rejected"]) == ("long", "no new snapshot ref")
    prop = json.loads(v["rejected_proposal_json"])
    assert prop["evidence"][0]["valid"] is False and "not in snapshot" in prop["evidence"][0]["invalid_reason"]


# A7 ------------------------------------------------------------------------

def test_a7_a_discussant_error_and_the_discussion_still_finishes(store, replay_db, tmp_path):
    boom = lambda v: (_ for _ in ()).throw(RuntimeError("discussant down"))   # noqa: E731
    out = run_once(discussion_graph(store, replay_db, tmp_path, FakeDiscussant("visual", "long", script=boom),
                                    FakeDiscussant("technical", "long")), ev(), run_id="err")
    assert out["status"] == "done"
    v = [r for r in rows(store, "votes", "err") if r["agent"] == "visual" and r["round"] == 2][0]
    assert (v["direction"], v["abstain"]) == ("long", 0)
    assert v["change_rejected"] == "call failed: error: RuntimeError: discussant down"
    assert out["final_decisions"]["discussion"].direction == "long"


# A8 ------------------------------------------------------------------------

def test_a8_a_discussant_sees_only_its_inputs(store, replay_db, tmp_path):
    a, b = FakeDiscussant("visual", "long"), FakeDiscussant("technical", "short")
    out = run_once(discussion_graph(store, replay_db, tmp_path, a, b), ev(), run_id="a8")
    (view,) = a.views
    assert view.snapshot == out["snap"] and view.round == 1 and view.max_rounds == 2
    assert {v.agent for v in view.others} == {"gex", "sentiment", "technical"}
    assert all(v.model_id is None and v.call_id is None and v.latency_ms is None for v in view.others)
    assert view.own_previous.direction == "long"
    assert set(type(view).model_fields) == {"agent", "round", "max_rounds", "snapshot", "own_previous", "others"}


# A9 ------------------------------------------------------------------------

def test_a9_same_snapshot_same_fakes_identical_discussion_result(tmp_path, replay_db):
    def once(name):
        st = SqliteAgentStore(str(tmp_path / f"{name}.sqlite"))
        flip = lambda v: {"direction": "short", "confidence": 0.7, "refs": [bar_ref(v.snapshot, 4)]}   # noqa: E731
        out = run_once(discussion_graph(st, replay_db, tmp_path / name, FakeDiscussant("visual", "long", script=flip),
                                        FakeDiscussant("technical", "long")), ev(), run_id="x")
        st.close()
        return out["discussion"]
    assert once("a") == once("b")


def test_real_parquet_day_through_the_graph(tmp_path):
    if not PARQUET.exists():
        pytest.skip("historical parquet not present (untracked)")
    from datafeed.bus import InMemoryBus
    from datafeed.ingest.xauusd_bars import STREAM, ReplaySource
    from datafeed.recorder.recorder import Recorder
    from datafeed.store import schema

    bus = InMemoryBus()
    ReplaySource(str(PARQUET), start="2025-06-04", end="2025-06-12").run(lambda m: bus.publish(STREAM, m))
    db = str(tmp_path / "datafeed.sqlite")
    con = schema.connect(db)
    Recorder(bus, con, STREAM, block_ms=None, sleep=lambda s: None).run(stop_when_idle=True)
    con.close()

    store = SqliteAgentStore(str(tmp_path / "agents.sqlite"))
    g = discussion_graph(store, db, tmp_path, FakeDiscussant("visual", "long"), FakeDiscussant("technical", "short"))
    t0 = datetime(2025, 6, 11, tzinfo=UTC) + timedelta(seconds=17)
    outs = [run_once(g, ev(t0 + timedelta(minutes=30 * i), str(i))) for i in range(48)]
    done = [o for o in outs if o["status"] == "done"]
    skipped = [o for o in outs if o["status"] == "skipped"]
    # Only the daily break (21:00-22:00 UTC in June) is skipped, by the gate.
    # The 22:00 reopen runs: staleness counts market-open minutes only.
    assert [o["as_of"].strftime("%H:%M") for o in skipped] == ["21:00", "21:30"]
    assert all(o["skip_reason"].startswith("gate: market closed") for o in skipped)
    assert len(done) == 46
    assert any(o["as_of"].strftime("%H:%M") == "22:00" for o in done)
    for o in done:
        snap = store.load_snapshot(o["snap"].hash)
        assert all(b.ts < o["event"].t for s in snap.bars.values() for b in s)
        assert len(snap.bars["1m"]) == 240 and snap.bars["4h"]
        assert {d.arm for d in store.load_run(o["run_id"])["decisions"]} == {"blind", "discussion"}
    store.close()
