"""python -m tools.research_memory <command>

    fetch-model            download the pinned embedding model once (network)
    build [--commit C]     scan + chunk + embed the corpus -> the index (the one rebuild command)
    scan                   the content scan only (exit 1 on any hit)
    info                   the index's metadata
    retrieve "Q" [-k 5]    the top-k chunks for a question (no LLM)
    ask "Q" [-k 5]         the full graph: retrieve -> answer -> check
    eval [--llm] [--out F] retrieval hit rate at k=3,5; with --llm also citation correctness
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from tools.research_memory.build import DEFAULT_INDEX, REPO


def _embedder():
    from tools.research_memory.embed import SentenceTransformerEmbedder   # noqa: PLC0415

    return SentenceTransformerEmbedder()


def cmd_fetch_model(_: argparse.Namespace) -> int:
    from tools.research_memory.embed import fetch_model   # noqa: PLC0415

    print(fetch_model())
    return 0


def cmd_build(a: argparse.Namespace) -> int:
    from tools.research_memory.build import ScanFailed, build   # noqa: PLC0415
    from tools.research_memory.corpus import CorpusError        # noqa: PLC0415

    try:
        r = build(_embedder(), out=a.index, commit=a.commit)
    except (ScanFailed, CorpusError) as e:
        print(f"build failed: {e}", file=sys.stderr)
        return 1
    print(f"indexed {r.n_files} files, {r.n_chunks} chunks at {r.commit[:12]} -> {r.index_path}")
    print("statuses: " + ", ".join(f"{k} {v}" for k, v in r.statuses.items()))
    if r.dirty:
        print(f"warning: {len(r.dirty)} corpus file(s) differ from HEAD; their committed text was indexed: "
              + ", ".join(r.dirty))
    return 0


def cmd_scan(_: argparse.Namespace) -> int:
    from tools.research_memory.build import ScanFailed, prepare   # noqa: PLC0415
    from tools.research_memory.corpus import CorpusError          # noqa: PLC0415

    try:
        commit, chunks, n_files, _dirty = prepare()
    except (ScanFailed, CorpusError) as e:
        print(f"scan failed: {e}", file=sys.stderr)
        return 1
    print(f"scan clean: {n_files} files, {len(chunks)} chunks at {commit[:12]}")
    return 0


def cmd_info(a: argparse.Namespace) -> int:
    from tools.research_memory.store import Index   # noqa: PLC0415

    print(json.dumps(Index.load(a.index).meta, indent=2))
    return 0


def _retriever(a: argparse.Namespace):
    from tools.research_memory.graph import Retriever   # noqa: PLC0415

    return Retriever(a.index, _embedder)


def cmd_retrieve(a: argparse.Namespace) -> int:
    for i, h in enumerate(_retriever(a).search(a.question, a.k), 1):
        c = h.chunk
        print(f"{i}. {h.score:.3f}  {c.path} § {c.heading_path}  [{c.status}]")
    return 0


def cmd_ask(a: argparse.Namespace) -> int:
    from tools.research_memory.graph import make_default_graph   # noqa: PLC0415

    out = make_default_graph(REPO, a.index, a.config).invoke({"question": a.question, "k": a.k})
    print(out["answer"])
    if out.get("reason"):
        print(f"\n({out['verdict']}: {out['reason']})")
    return 0


def cmd_eval(a: argparse.Namespace) -> int:
    from tools.research_memory.eval import citation_eval, ledger_cost, load_questions, retrieval_eval   # noqa: PLC0415
    from tools.research_memory.store import Index                                                       # noqa: PLC0415

    qs = load_questions()
    retr = _retriever(a)
    report: dict[str, object] = {"index": {k: Index.load(a.index).meta.get(k) for k in
                                           ("build_commit", "embedder", "n_files", "n_chunks", "corpus_sha256")}}
    report["retrieval"] = retrieval_eval(qs, retr.search)
    r = report["retrieval"]
    assert isinstance(r, dict)
    print(f"retrieval: n={r['n']}  hit@3={r['hit@3']:.0%}  hit@5={r['hit@5']:.0%}")
    if a.llm:
        from tools.research_memory.config import Problem, load_config   # noqa: PLC0415
        from tools.research_memory.graph import make_default_graph      # noqa: PLC0415

        graph = make_default_graph(REPO, a.index, a.config)
        c = citation_eval(qs, lambda q: graph.invoke({"question": q, "k": 5}))
        cfg = load_config(a.config)
        if not isinstance(cfg, Problem):
            ledger = Path(cfg.paths.ledger)
            c["cost"] = ledger_cost(ledger if ledger.is_absolute() else REPO / ledger,
                                    [row["call_id"] for row in c["rows"]])
            c["model"], c["effort"] = cfg.answer.model, cfg.answer.effort
        report["citation"] = c
        print(f"citation: correct={c['citation_correct']:.0%}  answered={c['answered']}  "
              f"not_in_documents={c['not_in_documents']}  abstain={c['abstain']}  cost={c.get('cost')}")
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"report: {a.out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m tools.research_memory", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--index", type=Path, default=DEFAULT_INDEX)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("fetch-model").set_defaults(fn=cmd_fetch_model)
    b = sub.add_parser("build")
    b.add_argument("--commit", default=None, help="index the corpus at this commit (default HEAD)")
    b.set_defaults(fn=cmd_build)
    sub.add_parser("scan").set_defaults(fn=cmd_scan)
    sub.add_parser("info").set_defaults(fn=cmd_info)
    for name, fn in (("retrieve", cmd_retrieve), ("ask", cmd_ask)):
        s = sub.add_parser(name)
        s.add_argument("question")
        s.add_argument("-k", type=int, default=5)
        s.add_argument("--config", type=Path, default=None)
        s.set_defaults(fn=fn)
    e = sub.add_parser("eval")
    e.add_argument("--llm", action="store_true", help="also run the graph (paid LLM calls) for citation correctness")
    e.add_argument("--config", type=Path, default=None)
    e.add_argument("--out", type=Path, default=REPO / "database" / "research_memory_eval.json")
    e.set_defaults(fn=cmd_eval)
    a = p.parse_args(argv)
    return int(a.fn(a))


if __name__ == "__main__":
    raise SystemExit(main())
