"""corpus.py: selection from git ls-files, statuses, rule violations; the real corpus.toml."""

from pathlib import Path

import pytest

from tests.research_memory.helpers import RULES, git, make_repo
from tools.research_memory.corpus import (CorpusError, Rules, dirty_files, doc_type_of, load_documents, study_of,
                                          tracked_files)

ROOT = Path(__file__).resolve().parents[2]


def test_selection_uses_tracked_files_patterns_extras_and_excludes(tmp_path):
    repo, rules_path = make_repo(tmp_path)
    rules = Rules.load(rules_path)
    chosen = rules.select(tracked_files(repo))
    assert chosen == sorted([
        "docs/decisions/feat-x.md", "research/README.md", "research/alpha/DECISIONS.md",
        "research/alpha/PREREG.md", "research/alpha/PROPOSAL.md", "research/alpha/REPORT.md"])
    assert "research/alpha/results/REPORT.md" not in chosen       # excluded glob
    assert "research/alpha/UNTRACKED.md" not in chosen            # untracked: never seen
    assert "research/alpha/notes.txt" not in chosen               # not markdown


def test_statuses_from_paths_and_globs(tmp_path):
    repo, rules_path = make_repo(tmp_path)
    rules = Rules.load(rules_path)
    commit, docs = load_documents(repo, rules)
    by = {d.path: d for d in docs}
    assert by["research/alpha/PROPOSAL.md"].status == "draft"
    assert by["research/alpha/DECISIONS.md"].status == "decision log"
    assert by["docs/decisions/feat-x.md"].status == "decision log"
    assert by["research/alpha/REPORT.md"].status == "result"
    assert all(d.last_commit == commit for d in docs)             # one commit in the fixture
    assert len(commit) == 40


def test_unmapped_file_fails(tmp_path):
    repo, rules_path = make_repo(tmp_path, rules=RULES.replace('"research/README.md" = "reference"\n', ""))
    with pytest.raises(CorpusError, match="research/README.md: in the corpus but has no status"):
        load_documents(repo, Rules.load(rules_path))


def test_stale_status_entry_fails(tmp_path):
    repo, rules_path = make_repo(tmp_path, rules=RULES + '\n[status.extra]\n')
    rules = Rules.load(rules_path)
    rules.status_paths["research/gone/REPORT.md"] = "result"
    with pytest.raises(CorpusError, match="not in the corpus"):
        load_documents(repo, rules)


def test_unknown_status_fails(tmp_path):
    _, rules_path = make_repo(tmp_path, rules=RULES.replace('= "draft"', '= "sort of draft"'))
    with pytest.raises(CorpusError, match="unknown status"):
        Rules.load(rules_path)


def test_untracked_extra_path_fails(tmp_path):
    repo, rules_path = make_repo(tmp_path, rules=RULES.replace('"research/alpha/PROPOSAL.md"]',
                                                               '"research/alpha/PROPOSAL.md", "research/x.md"]'))
    with pytest.raises(CorpusError, match="not tracked"):
        load_documents(repo, Rules.load(rules_path))


def test_committed_text_is_indexed_and_dirty_files_are_reported(tmp_path):
    repo, rules_path = make_repo(tmp_path)
    (repo / "research/alpha/REPORT.md").write_text("# edited, not committed\n", encoding="utf-8")
    _, docs = load_documents(repo, Rules.load(rules_path))
    report = next(d for d in docs if d.path == "research/alpha/REPORT.md")
    assert "walrus index lowers QLIKE" in report.text
    assert dirty_files(repo, [d.path for d in docs]) == ["research/alpha/REPORT.md"]


def test_last_commit_is_per_file(tmp_path):
    repo, rules_path = make_repo(tmp_path)
    first = git(repo, "rev-parse", "HEAD").strip()
    (repo / "research/README.md").write_text("# research/\n\nUpdated.\n", encoding="utf-8")
    git(repo, "commit", "-q", "-am", "readme")
    second = git(repo, "rev-parse", "HEAD").strip()
    commit, docs = load_documents(repo, Rules.load(rules_path))
    by = {d.path: d.last_commit for d in docs}
    assert commit == second and by["research/README.md"] == second and by["research/alpha/REPORT.md"] == first


def test_study_and_doc_type():
    assert study_of("research/news_vol/REPORT.md") == "news_vol"
    assert study_of("research/SEALING.md") == "research"
    assert study_of("docs/decisions/feat-x.md") == "docs"
    assert doc_type_of("docs/decisions/feat-x.md") == "DECISIONS"
    assert doc_type_of("research/x/DECISIONS_2.md") == "DECISIONS"
    assert doc_type_of("research/x/POSTHOC_RESULT.md") == "POSTHOC_RESULT"


# --- the real corpus.toml ----------------------------------------------------------------------------

def test_real_rules_load_and_name_tracked_files():
    """The real corpus.toml parses, its statuses are valid, and what it names exists. Whether every
    selected file has a status is checked by `build`, not here: a PR adding a document stays green."""
    rules = Rules.load()
    paths = rules.select(tracked_files(ROOT))
    assert set(rules.status_paths) <= set(paths)
    assert {r.path for r in (*rules.sections, *rules.skips)} <= set(paths)
    assert rules.status_of("research/agents_v1/PREREG.md") == "draft"        # "PREREG (DRAFT)" in its title
    assert rules.status_of("research/news_vol/DECISIONS.md") == "decision log"
    assert rules.status_of("research/gex_feasibility/OWNER_DECISION.md") == "owner decision"
    assert not any("/results/" in p for p in paths)
    assert all(p.endswith(".md") for p in paths)
