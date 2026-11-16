"""Evaluation: retrieval hit rate at k = 3 and 5, and citation correctness.

eval/questions.toml holds 25 questions written from the documents, each
with the file(s) that answer it (`expected`).

- Retrieval (no LLM): a question is a hit at k when any of its top-k chunks
  comes from an expected file. Also the rank of the first such chunk.
- Citation correctness (LLM, k = 5): the graph's answer passed the citation
  check AND cites at least one chunk from an expected file. Reported with
  the not-in-the-documents and abstain counts, and the list-price cost of
  the run's calls (backend/agents/llm/pricing.py) from the ledger.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from tools.research_memory.store import Hit

DEFAULT_QUESTIONS = Path(__file__).with_name("eval") / "questions.toml"
KS = (3, 5)


@dataclass(frozen=True)
class Question:
    id: str
    question: str
    expected: tuple[str, ...]


def load_questions(path: Path = DEFAULT_QUESTIONS) -> list[Question]:
    with open(path, "rb") as f:
        rows = tomllib.load(f)["question"]
    qs = [Question(r["id"], r["question"], tuple(r["expected"])) for r in rows]
    ids = [q.id for q in qs]
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate question ids")
    return qs


def first_rank(hits: list[Hit], expected: tuple[str, ...]) -> int | None:
    for i, h in enumerate(hits, 1):
        if h.chunk.path in expected:
            return i
    return None


def retrieval_eval(questions: list[Question], search: Callable[[str, int], list[Hit]],
                   ks: tuple[int, ...] = KS) -> dict[str, Any]:
    kmax = max(ks)
    rows = []
    for q in questions:
        hits = search(q.question, kmax)
        rank = first_rank(hits, q.expected)
        rows.append({"id": q.id, "rank": rank, "top": [h.chunk.path for h in hits]})
    n = len(rows)
    rates = {f"hit@{k}": sum(1 for r in rows if r["rank"] is not None and r["rank"] <= k) / n for k in ks}
    return {"n": n, **rates, "rows": rows}


def citation_eval(questions: list[Question], run: Callable[[str], dict[str, Any]]) -> dict[str, Any]:
    rows = []
    for q in questions:
        out = run(q.question)
        cited = sorted({c["path"] for claim in out.get("claims", []) or [] for c in claim.get("citations", [])})
        correct = out.get("verdict") == "answered" and any(p in q.expected for p in cited)
        rows.append({"id": q.id, "verdict": out.get("verdict"), "reason": out.get("reason"), "cited": cited,
                     "correct": correct, "call_id": (out.get("call") or {}).get("call_id"),
                     "answer": out.get("answer")})
    n = len(rows)
    count = lambda v: sum(1 for r in rows if r["verdict"] == v)   # noqa: E731
    return {"n": n, "citation_correct": sum(r["correct"] for r in rows) / n, "answered": count("answered"),
            "not_in_documents": count("not_in_documents"), "abstain": count("abstain"), "rows": rows}


def ledger_cost(ledger_path: Path, call_ids: list[str]) -> dict[str, Any]:
    """Tokens and list-price cost of the given calls, from the llm_calls rows."""
    import sqlite3   # noqa: PLC0415

    ids = [c for c in call_ids if c]
    if not ids:
        return {"calls": 0, "cost_usd": 0.0}
    con = sqlite3.connect(f"file:{ledger_path}?mode=ro", uri=True)
    try:
        q = (f"SELECT COUNT(*), SUM(cached), SUM(input_tokens), SUM(output_tokens), SUM(cost_usd), "
             f"SUM(cost_usd IS NULL AND cached = 0) FROM llm_calls WHERE call_id IN ({', '.join('?' * len(ids))})")
        n, cached, tin, tout, cost, unpriced = con.execute(q, ids).fetchone()
    finally:
        con.close()
    return {"calls": n, "cached": cached or 0, "input_tokens": tin or 0, "output_tokens": tout or 0,
            "cost_usd": round(cost or 0.0, 6), "unpriced_calls": unpriced or 0}


__all__ = ["DEFAULT_QUESTIONS", "KS", "Question", "citation_eval", "first_rank", "ledger_cost", "load_questions",
           "retrieval_eval"]
