"""build.py + store.py: the end-to-end build on the fixture repo, the index file, retrieval, failures."""

import sqlite3

import numpy as np
import pytest

from tests.research_memory.helpers import DOCS, RULES, make_repo
from tools.research_memory.build import ScanFailed, build, prepare
from tools.research_memory.corpus import CorpusError
from tools.research_memory.embed import HashingEmbedder
from tools.research_memory.store import Index, IndexProblem


@pytest.fixture
def built(tmp_path):
    repo, rules = make_repo(tmp_path)
    out = tmp_path / "idx.sqlite"
    result = build(HashingEmbedder(), repo=repo, out=out, rules_path=rules)
    return repo, rules, out, result


def test_build_writes_metadata_and_chunks(built):
    repo, _, out, result = built
    index = Index.load(out)
    m = index.meta
    assert m["embedder"] == "test-hashing-embedder" and m["dim"] == 256
    assert m["build_commit"] == result.commit and len(m["corpus_sha256"]) == 64
    assert m["n_files"] == 6 and m["n_chunks"] == len(index.chunks) == result.n_chunks
    assert index.matrix.shape == (len(index.chunks), 256) and index.matrix.dtype == np.float32
    assert {c.last_commit for c in index.chunks} == {result.commit}
    statuses = {c.heading_path: c.status for c in index.chunks}
    assert statuses["alpha: pre-registration > 4. Amendment 1 (2026-01-02) > 4.1 What changes"] == "amended"
    assert statuses["alpha: pre-registration > 2. Rule"] == "registered"
    assert statuses["Proposal: a penguin index (not run)"] == "draft"
    assert not any("Daily table" in c.heading_path or "2026-01-05" in c.text for c in index.chunks)   # skipped
    assert not any(c.path.endswith("UNTRACKED.md") or "/results/" in c.path for c in index.chunks)
    assert not out.with_name(out.name + ".tmp").exists()


def test_retrieval_ranks_the_matching_chunk_first(built):
    _, _, out, _ = built
    e = HashingEmbedder()
    hits = Index.load(out).search(e.embed_query("walrus index QLIKE verdict"), 3, e.name)
    assert len(hits) == 3 and hits[0].score >= hits[1].score >= hits[2].score
    assert any(h.chunk.path == "research/alpha/REPORT.md" for h in hits[:2])


def test_query_with_another_embedder_is_refused(built):
    _, _, out, _ = built
    other = HashingEmbedder(name="other")
    with pytest.raises(IndexProblem, match="rebuild"):
        Index.load(out).search(other.embed_query("x"), 3, other.name)


def test_missing_index_and_old_schema(tmp_path, built):
    with pytest.raises(IndexProblem, match="run `python -m tools.research_memory build`"):
        Index.load(tmp_path / "none.sqlite")
    _, _, out, _ = built
    con = sqlite3.connect(out)
    con.execute("UPDATE meta SET value = '0' WHERE key = 'schema_version'")
    con.commit()
    con.close()
    with pytest.raises(IndexProblem, match="schema"):
        Index.load(out)


def test_scan_hit_stops_the_build_and_keeps_the_old_index(built, tmp_path):
    repo, rules, out, _ = built
    before = out.read_bytes()
    bad = dict(DOCS)
    bad["research/alpha/REPORT.md"] += "\n        | 2024-01-02 10:00 | 2051.20 | 2052.10 | 2050.90 |\n"   # same indent
    second = tmp_path / "second"
    second.mkdir()
    repo2, rules2 = make_repo(second, docs=bad)
    with pytest.raises(ScanFailed, match="price_row") as e:
        build(HashingEmbedder(), repo=repo2, out=out, rules_path=rules2)
    assert "2051" not in str(e.value)                 # masked
    assert out.read_bytes() == before


def test_without_the_skip_the_dated_table_fails_the_scan(tmp_path):
    rules = RULES.split("[[skip_section]]")[0]
    repo, rules_path = make_repo(tmp_path, rules=rules)
    with pytest.raises(ScanFailed, match="calendar_rows_run"):
        prepare(repo, rules_path)


def test_stale_override_or_skip_stops_the_build(tmp_path):
    repo, rules_path = make_repo(tmp_path, rules=RULES.replace("4\\. Amendment 1", "9\\. Amendment 9"))
    with pytest.raises(CorpusError, match="match no heading"):
        prepare(repo, rules_path)
    (tmp_path / "b").mkdir()
    repo, rules_path = make_repo(tmp_path / "b", rules=RULES.replace("^Daily table$", "^No such table$"))
    with pytest.raises(CorpusError, match="matches no heading"):
        prepare(repo, rules_path)
