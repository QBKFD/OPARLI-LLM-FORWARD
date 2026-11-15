"""Build the index: corpus -> skip sections -> scan -> chunk -> embed -> write.

    python -m tools.research_memory build        # the one rebuild command

The scan runs on every build and any hit stops it before anything is
embedded or written; the previous index stays. Overrides and skips that
match no heading stop it too. Text is read from HEAD; corpus files with
uncommitted changes are listed as a warning (their committed text is indexed).
"""

from __future__ import annotations

import hashlib
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from tools.research_memory.chunk import Chunk, apply_skips, chunk_document, unmatched_overrides
from tools.research_memory.corpus import DEFAULT_RULES, CorpusError, Rules, dirty_files, load_documents
from tools.research_memory.embed import Embedder
from tools.research_memory.scan import Hit, scan_document
from tools.research_memory.store import write_index

REPO = Path(__file__).resolve().parents[2]
DEFAULT_INDEX = REPO / "database" / "research_memory.sqlite"


class ScanFailed(RuntimeError):
    def __init__(self, hits: list[Hit]) -> None:
        super().__init__(f"content scan: {len(hits)} hit(s); nothing was indexed\n"
                         + "\n".join(f"  {h}" for h in hits))
        self.hits = hits


@dataclass(frozen=True)
class BuildResult:
    commit: str
    n_files: int
    n_chunks: int
    statuses: dict[str, int]
    dirty: list[str]
    index_path: Path


def prepare(repo: Path = REPO, rules_path: Path = DEFAULT_RULES, commit: str | None = None
            ) -> tuple[str, list[Chunk], int, list[str]]:
    """(build commit, chunks, file count, dirty files): everything but embedding.
    Raises CorpusError or ScanFailed."""
    rules = Rules.load(rules_path)
    commit, docs = load_documents(repo, rules, commit)
    try:
        docs = [apply_skips(d, rules.skips) for d in docs]
    except ValueError as e:
        raise CorpusError(str(e)) from None
    hits = [h for d in docs for h in scan_document(d.path, d.text)]
    if hits:
        raise ScanFailed(hits)
    stale = unmatched_overrides(docs, rules.sections)
    if stale:
        raise CorpusError("section overrides that match no heading: "
                          + ", ".join(f"{r.path} {r.heading.pattern!r}" for r in stale))
    chunks = [c for d in docs for c in chunk_document(d, rules.sections)]
    return commit, chunks, len(docs), dirty_files(repo, sorted({c.path for c in chunks}))


def corpus_digest(chunks: list[Chunk]) -> str:
    h = hashlib.sha256()
    for c in chunks:
        h.update(f"{c.chunk_id}\0{c.status}\0{c.text}\0".encode())
    return h.hexdigest()


def build(embedder: Embedder, repo: Path = REPO, out: Path = DEFAULT_INDEX, rules_path: Path = DEFAULT_RULES,
          commit: str | None = None) -> BuildResult:
    commit, chunks, n_files, dirty = prepare(repo, rules_path, commit)
    vectors = embedder.embed_passages([c.embed_text() for c in chunks])
    statuses = dict(sorted(Counter(c.status for c in chunks).items()))
    write_index(out, chunks, vectors, {
        "embedder": embedder.name, "dim": embedder.dim, "build_commit": commit,
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "corpus_sha256": corpus_digest(chunks), "n_files": n_files, "n_chunks": len(chunks),
        "statuses": statuses,
        "rules_sha256": hashlib.sha256(Path(rules_path).read_bytes()).hexdigest()})
    return BuildResult(commit, n_files, len(chunks), statuses, dirty, out)


__all__ = ["BuildResult", "DEFAULT_INDEX", "REPO", "ScanFailed", "build", "corpus_digest", "prepare"]
