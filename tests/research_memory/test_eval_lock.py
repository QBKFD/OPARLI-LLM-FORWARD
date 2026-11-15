"""eval.py (the questions file, the metrics, the ledger cost) and the memory lock."""

import re
from pathlib import Path

from backend.agents.llm.ledger import CallRecord, SqliteLedger
from backend.agents.store.sqlite import SqliteAgentStore
from tools.research_memory.chunk import Chunk
from tools.research_memory.corpus import Rules, tracked_files
from tools.research_memory.eval import citation_eval, ledger_cost, load_questions, retrieval_eval
from tools.research_memory.store import Hit

ROOT = Path(__file__).resolve().parents[2]


def test_questions_file():
    qs = load_questions()
    assert len(qs) == 25 and len({q.id for q in qs}) == 25
    corpus = set(Rules.load().select(tracked_files(ROOT)))
    assert all(p in corpus for q in qs for p in q.expected)
    assert all(q.question.endswith("?") for q in qs)


def hit(path):
    return Hit(Chunk(chunk_id=path, path=path, study="s", doc_type="X", status="result", heading_path="h",
                     heading="h", part=1, n_parts=1, text="t", last_commit="c"), 0.5)


def test_retrieval_eval_counts_hits_at_k():
    from tools.research_memory.eval import Question

    qs = [Question("a", "?", ("x.md",)), Question("b", "?", ("y.md",)), Question("c", "?", ("z.md",))]
    ranked = {"a": ["x.md", "o", "o", "o", "o"], "b": ["o", "o", "o", "y.md", "o"], "c": ["o"] * 5}
    order = iter("abc")
    r = retrieval_eval(qs, lambda q, k: [hit(p) for p in ranked[next(order)][:k]])
    assert r["hit@3"] == 1 / 3 and r["hit@5"] == 2 / 3
    assert [row["rank"] for row in r["rows"]] == [1, 4, None]


def test_citation_eval():
    from tools.research_memory.eval import Question

    qs = [Question("a", "?", ("x.md",)), Question("b", "?", ("y.md",)), Question("c", "?", ("z.md",))]
    outs = iter([
        {"verdict": "answered", "claims": [{"citations": [{"path": "x.md"}]}], "call": {"call_id": "1"}},
        {"verdict": "answered", "claims": [{"citations": [{"path": "other.md"}]}]},
        {"verdict": "not_in_documents"},
    ])
    r = citation_eval(qs, lambda q: next(outs))
    assert r["citation_correct"] == 1 / 3 and r["answered"] == 2 and r["not_in_documents"] == 1
    assert [row["correct"] for row in r["rows"]] == [True, False, False]


def test_ledger_cost(tmp_path):
    path = tmp_path / "ledger.sqlite"
    ledger = SqliteLedger(SqliteAgentStore(str(path)))
    base = dict(snapshot_hash="s", agent="research_memory", round=1, mode="live", model_requested="m",
                effort="default", max_tokens=10, prompt_hash="p", utc_day="2026-11-15", created_ts="t")
    ledger.record_call(CallRecord(call_id="1", attempt=1, cached=False, input_tokens=1000, output_tokens=100,
                                  cost_usd=0.003, **base))
    ledger.record_call(CallRecord(call_id="2", attempt=0, cached=True, **base))
    ledger.record_call(CallRecord(call_id="3", attempt=1, cached=False, input_tokens=5, cost_usd=9.0, **base))
    c = ledger_cost(path, ["1", "2", None])
    assert c == {"calls": 2, "cached": 1, "input_tokens": 1000, "output_tokens": 100, "cost_usd": 0.003,
                 "unpriced_calls": 0}


# --- requirements/requirements-memory.lock ------------------------------------------------------------

PIN = re.compile(r"^([A-Za-z0-9_.\-\[\]]+)==([^\s\\;]+)")


def pins(path):
    out, hashed, cur = {}, {}, None
    for line in path.read_text().splitlines():
        m = PIN.match(line)
        if m:
            cur = m.group(1).split("[")[0].lower().replace("_", "-")
            out[cur], hashed[cur] = m.group(2), 0
        elif line.strip().startswith("--hash=sha256:") and cur:
            hashed[cur] += 1
    return out, hashed


def test_memory_lock_extends_the_other_locks():
    main, _ = pins(ROOT / "requirements/requirements.lock")
    studio, _ = pins(ROOT / "requirements/requirements-studio.lock")
    memory, hashed = pins(ROOT / "requirements/requirements-memory.lock")
    for name, v in {**main, **studio}.items():
        if name in memory and name != "setuptools":
            assert memory[name] == v, f"{name}: memory lock {memory[name]} vs {v}"
    assert memory["sentence-transformers"] == "6.2.0" and memory["torch"] == "2.11.0"
    assert memory["langgraph-cli"] == studio["langgraph-cli"]
    assert all(n > 0 for n in hashed.values())


def test_main_lock_has_no_torch():
    main, _ = pins(ROOT / "requirements/requirements.lock")
    assert not {"torch", "sentence-transformers", "transformers"} & set(main)
