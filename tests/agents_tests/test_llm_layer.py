"""agents.llm building blocks: request, parse, pricing, budget, ledgers. No network."""

import json
import threading
from datetime import datetime, timezone
from pathlib import Path

import pytest

from agents.config import SnapshotConfig
from agents.llm.budget import Budget
from agents.llm.fake import fake_response
from agents.llm.ledger import CallRecord, MemoryLedger, SqliteLedger
from agents.llm.parse import parse_answer
from agents.llm.pricing import cost_usd
from agents.llm.request import ImageBlock, LLMRequest, MissingImage, TextBlock
from agents.owner_config import BudgetSettings
from agents.prompting.output_schema import VOTE_OUTPUT_SCHEMA
from agents.schemas import MetricRow, Snapshot
from agents.snapshot import build_snapshot
from agents.store import SqliteAgentStore

UTC = timezone.utc
T = datetime(2025, 6, 11, 14, 30, tzinfo=UTC)


@pytest.fixture(scope="module")
def snap(replay_db):
    s = build_snapshot(T, SnapshotConfig(mode="replay"), replay_db)
    assert isinstance(s, Snapshot)
    return s


def req(**kw):
    base = dict(agent="visual", round=1, model="claude-opus-5-5", effort="medium", max_tokens=1000,
                system="sys", messages=((TextBlock("hello"),),), output_schema=VOTE_OUTPUT_SCHEMA)
    return LLMRequest(**{**base, **kw})


# ---- request

def test_prompt_hash_covers_every_request_field():
    h = req().prompt_hash
    assert req().prompt_hash == h
    for kw in ({"model": "claude-sonnet-5-5"}, {"effort": "low"}, {"max_tokens": 999}, {"system": "x"},
               {"messages": ((TextBlock("hello!"),),)}, {"output_schema": {"type": "object"}}):
        assert req(**kw).prompt_hash != h, kw
    assert req(agent="technical", round=2).prompt_hash == h            # who asks isn't part of the request


def test_payload_has_no_sampling_parameters():
    p = req().payload()
    assert set(p) == {"model", "max_tokens", "system", "messages", "output_config"}
    assert p["output_config"] == {"effort": "medium", "format": {"type": "json_schema", "schema": VOTE_OUTPUT_SCHEMA}}
    assert p["messages"] == [{"role": "user", "content": [{"type": "text", "text": "hello"}]}]


def test_images_are_hashed_in_the_body_and_attached_in_the_payload():
    r = req(messages=((TextBlock("a"), ImageBlock("ab" * 32)),))
    assert r.body()["messages"][0]["content"][1] == {"type": "image", "sha256": "ab" * 32, "media_type": "image/png"}
    p = r.payload(lambda sha: b"\x89PNG")
    assert p["messages"][0]["content"][1]["source"] == {"type": "base64", "media_type": "image/png",
                                                         "data": "iVBORw=="}
    with pytest.raises(MissingImage, match="no image store"):
        r.payload()
    with pytest.raises(MissingImage, match="KeyError"):
        r.payload({}.__getitem__)


# ---- parse

def answer(**kw):
    base = {"direction": "none", "confidence": 0.4, "reason": "flat", "evidence": []}
    return json.dumps({**base, **kw})


def test_a_none_vote_needs_no_evidence(snap):
    a = parse_answer(answer(), snap)
    assert (a.direction, a.confidence, a.reason, a.evidence) == ("none", 0.4, "flat", ())


def test_refs_are_checked_by_code(snap):
    bar = snap.bars["15m"][-2]
    ok = f"bars:15m:{bar.ts.isoformat()}..{bar.ts.isoformat()}"
    a = parse_answer(answer(direction="long", evidence=[{"ref": ok, "claim": "higher low"},
                                                        {"ref": "level:nope", "claim": "x"}]), snap)
    assert a.direction == "long"
    assert [(e.valid, e.invalid_reason) for e in a.evidence] == [(True, None), (False, "level 'nope' not in snapshot")]


@pytest.mark.parametrize("text, why", [
    (None, "no text"),
    ("not json", "not JSON"),
    ("[1]", "not an object"),
    (json.dumps({"direction": "long"}), "fields"),
    (answer(extra=1), "fields"),
    (answer(direction="up"), "direction"),
    (answer(confidence=1.5), "confidence"),
    (answer(confidence=True), "confidence"),
    (answer(confidence="0.5"), "confidence"),
    (answer(reason="x" * 601), "reason"),
    (answer(evidence=[{"ref": "feature:x", "claim": "c"}] * 7), "at most 6"),
    (answer(evidence=[{"ref": "feature:x"}]), "evidence[0]"),
    (answer(evidence=[{"ref": " ", "claim": "c"}]), "ref is empty"),
    (answer(evidence=[{"ref": "feature:x", "claim": "c" * 201}]), "claim"),
    (answer(direction="short", evidence=[{"ref": "level:nope", "claim": "c"}]), "direction without evidence"),
    (answer(direction="long"), "direction without evidence"),
])
def test_bad_answers_are_reasons(snap, text, why):
    out = parse_answer(text, snap)
    assert isinstance(out, str) and out.startswith("invalid:") and why in out


def test_metric_and_chart_refs(snap):
    from agents.debate.evidence import check_ref
    s = Snapshot(**{**snap.model_dump(exclude={"hash", "unavailable", "metrics"}),
                    "unavailable": {k: v for k, v in snap.unavailable.items() if k != "metrics"},
                    "metrics": (MetricRow(name="rsi14_1h", value=55.0, unit="0-100", available=True),
                                MetricRow(name="vwap", value=None, unit="", available=False, reason="no volume"))})
    assert check_ref("metric:rsi14_1h", s) is None
    assert check_ref("metric:vwap", s) == "metric 'vwap' is unavailable: no volume"
    assert check_ref("metric:atr", s) == "metric 'atr' not in snapshot"
    assert check_ref("chart:4h", s) == "chart '4h' not in snapshot"


# ---- pricing

def test_cost_at_list_price():
    u = {"input_tokens": 1_000_000, "output_tokens": 100_000, "cache_read_input_tokens": 0,
         "cache_creation_input_tokens": 0}
    assert cost_usd("claude-opus-5-5", u) == pytest.approx(4.0 + 2.0)
    assert cost_usd("claude-sonnet-5-5", {**u, "cache_read_input_tokens": 1_000_000}) == pytest.approx(2 + 1 + 0.2)
    assert cost_usd("claude-opus-5-5", {"input_tokens": 0, "output_tokens": 0,
                                        "cache_creation_input_tokens": 1_000_000}) == pytest.approx(5.0)
    assert cost_usd("claude-unknown-1", u) is None
    assert cost_usd("claude-haiku-5-5", {**u, "cache_read_input_tokens": 5}) is None   # no cache-read price


# ---- ledgers and budget

def rec(call_id, day="2025-06-11", cached=False, attempt=1):
    return CallRecord(call_id=call_id, snapshot_hash="s", agent="visual", round=1, mode="live", attempt=attempt,
                      model_requested="claude-opus-5-5", effort="medium", max_tokens=10, prompt_hash="h",
                      cached=cached, utc_day=day, created_ts=f"{day}T00:00:00+00:00")


@pytest.fixture(params=["memory", "sqlite"])
def ledger(request, tmp_path):
    if request.param == "memory":
        yield MemoryLedger()
    else:
        store = SqliteAgentStore(str(tmp_path / "a.sqlite"))
        yield SqliteLedger(store)
        store.close()


def test_ledger_cache_first_writer_wins(ledger):
    a, b = fake_response({"x": 1}, request_id="a"), fake_response({"x": 2}, request_id="b")
    assert ledger.get_response("h") is None
    assert ledger.put_response("h", a, "t") == a
    assert ledger.put_response("h", b, "t") == a                       # the stored copy is what is used
    assert ledger.get_response("h") == a


def test_ledger_counts_sent_attempts_only(ledger):
    for r in (rec("1"), rec("2"), rec("3", cached=True, attempt=0), rec("4", attempt=0), rec("5", day="2025-06-12")):
        ledger.record_call(r)
    assert ledger.attempts_on("2025-06-11") == 2 and ledger.attempts_on("2025-06-12") == 1
    with pytest.raises(Exception):
        ledger.record_call(rec("1"))                                   # a call id is written once


def test_sqlite_ledger_tables_are_append_only(tmp_path):
    store = SqliteAgentStore(str(tmp_path / "a.sqlite"))
    led = SqliteLedger(store)
    led.record_call(rec("1"))
    led.put_response("h", fake_response({"x": 1}), "t")              # triggers fire per row: give each a row
    for sql in ("UPDATE llm_calls SET agent = 'x'", "DELETE FROM llm_calls", "DELETE FROM llm_responses"):
        with pytest.raises(Exception, match="append-only"):
            store.con.execute(sql)
    store.close()


def test_a_v3_store_is_migrated_to_v4(tmp_path, monkeypatch):
    from agents.store import sqlite as sq
    p = str(tmp_path / "v3.sqlite")
    monkeypatch.setattr(sq, "MIGRATIONS", sq.MIGRATIONS[:3])
    sq.SqliteAgentStore(p).close()
    monkeypatch.undo()
    s = SqliteAgentStore(p)
    names = {r[0] for r in s.con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"llm_calls", "llm_responses"} <= names
    assert {"evidence_json", "call_id"} <= {r[1] for r in s.con.execute("PRAGMA table_info(votes)")}
    s.close()


def budget_cfg(tmp_path, n=2):
    return BudgetSettings(max_calls_per_day=n, kill_switch_path=str(tmp_path / "KILL"))


def test_budget_reserves_up_to_the_cap(tmp_path):
    led = MemoryLedger()
    b = Budget(led, now=lambda: T, env={})
    cfg = budget_cfg(tmp_path)
    assert b.reserve(cfg) == (None, "2025-06-11")
    assert b.reserve(cfg) == (None, "2025-06-11")
    reason, _ = b.reserve(cfg)
    assert reason == "budget: 2 attempts on 2025-06-11, max 2"
    b.release("2025-06-11")
    led.record_call(rec("x"))                                       # the released attempt was recorded
    assert b.reserve(cfg)[0].startswith("budget: 2 attempts")
    with pytest.raises(RuntimeError):
        Budget(led, now=lambda: T, env={}).release("2025-06-11")


def test_budget_holds_under_parallel_agents(tmp_path):
    b = Budget(MemoryLedger(), now=lambda: T, env={})
    cfg, got, lock = budget_cfg(tmp_path, n=5), [], threading.Lock()

    def worker():
        r, _ = b.reserve(cfg)
        with lock:
            got.append(r)

    threads = [threading.Thread(target=worker) for _ in range(20)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert sum(r is None for r in got) == 5


def test_kill_switch_file_and_env(tmp_path):
    cfg = budget_cfg(tmp_path)
    assert Budget(MemoryLedger(), env={}).kill_switch(cfg) is None
    assert Budget(MemoryLedger(), env={"AGENTS_LLM_KILL": "1"}).kill_switch(cfg) == "kill_switch: $AGENTS_LLM_KILL=1"
    assert Budget(MemoryLedger(), env={"AGENTS_LLM_KILL": "0"}).kill_switch(cfg) is None
    Path(cfg.kill_switch_path).write_text("")
    assert Budget(MemoryLedger(), env={}).kill_switch(cfg) == "kill_switch: KILL exists"
