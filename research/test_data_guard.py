"""research/data_guard.py: no study reads an "unused by any study" file unless
its committed PREREG names it.  python -m pytest research/test_data_guard.py -q"""

import re
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import data_guard as dg  # noqa: E402

BACKFILL = "database/ohlcv_data/XAUUSD_ibkr_1min_20260201_20260930.parquet"


def test_the_backfill_is_guarded_by_name_and_variants():
    assert dg.guarded_key(f"/anywhere/{Path(BACKFILL).name}") == BACKFILL
    assert dg.guarded_key("XAUUSD_ibkr_1min_20260201_20260930.rejected.parquet") == BACKFILL
    assert dg.guarded_key("database/ohlcv_data/XAUUSD_1min_UTC_clean.parquet") is None
    dg.assert_may_read("database/ohlcv_data/XAUUSD_1min_UTC_clean.parquet", study="anything")   # not guarded


def test_refused_without_a_prereg():
    with pytest.raises(dg.DataGuardError, match="unused by any study.*doesn't exist"):
        dg.assert_may_read(BACKFILL, study="no_such_study")


def test_an_existing_prereg_that_does_not_name_it_is_refused():
    # research/levels_confirm has a committed PREREG.md that doesn't mention the backfill
    assert (dg.RESEARCH / "levels_confirm" / "PREREG.md").exists()
    with pytest.raises(dg.DataGuardError, match="levels_confirm"):
        dg.assert_may_read(BACKFILL, study="levels_confirm")


@pytest.fixture
def fake_study(tmp_path, monkeypatch):
    """A study dir with a PREREG naming the backfill; git answers scripted."""
    research = tmp_path / "research"
    (research / "s1").mkdir(parents=True)
    (research / "s1" / "PREREG.md").write_text(f"# s1\n\nData: `{BACKFILL}` (2026-02 .. 2026-09).\n")
    monkeypatch.setattr(dg, "RESEARCH", research)
    monkeypatch.setattr(dg, "ROOT", tmp_path)
    state = {"tracked": 0, "clean": 0}

    def git(*args):
        rc = state["tracked"] if args[0] == "ls-files" else state["clean"]
        return types.SimpleNamespace(returncode=rc)
    monkeypatch.setattr(dg, "_git", git)
    return research, state


def test_fallback_allows_a_committed_prereg_that_names_it(fake_study):
    dg.assert_may_read(BACKFILL, study="s1")


def test_fallback_refuses_an_uncommitted_or_edited_prereg(fake_study):
    _, state = fake_study
    state["clean"] = 1
    with pytest.raises(dg.DataGuardError, match="not committed as it is"):
        dg.assert_may_read(BACKFILL, study="s1")
    state["clean"], state["tracked"] = 0, 1
    with pytest.raises(dg.DataGuardError, match="not committed"):
        dg.assert_may_read(BACKFILL, study="s1")


def test_the_registry_decides_once_it_exists(fake_study, monkeypatch):
    research, _ = fake_study
    (research / "experiment_registry.py").write_text(
        "def prereg_data_files(study):\n"
        f"    return {{'s2': frozenset({{{BACKFILL!r}}})}}.get(study)\n")
    monkeypatch.syspath_prepend(str(research))
    sys.modules.pop("experiment_registry", None)
    dg.assert_may_read(BACKFILL, study="s2")
    # the registry overrides the fallback: s1's PREREG names it, but the registry doesn't list it
    with pytest.raises(dg.DataGuardError, match="registry doesn't know study 's1'"):
        dg.assert_may_read(BACKFILL, study="s1")
    sys.modules.pop("experiment_registry", None)


def test_a_registry_without_the_interface_fails_loudly(fake_study, monkeypatch):
    research, _ = fake_study
    (research / "registry.py").write_text("STUDIES = {}\n")
    monkeypatch.syspath_prepend(str(research))
    sys.modules.pop("registry", None)
    with pytest.raises(dg.DataGuardError, match="defines no prereg_data_files"):
        dg.assert_may_read(BACKFILL, study="s1")
    sys.modules.pop("registry", None)


def test_no_research_code_names_a_guarded_file_without_the_guard():
    """Static: a research .py that mentions a guarded file (or its stem) must call assert_may_read."""
    stems = [n.rsplit(".", 1)[0] for n in dg.guarded()]
    assert stems, "the manifest lists no guarded file"
    pat = re.compile("|".join(map(re.escape, stems)))
    here = Path(__file__).resolve()
    offenders = [p.relative_to(dg.ROOT).as_posix() for p in dg.RESEARCH.rglob("*.py")
                 if p.resolve() not in (here, Path(dg.__file__).resolve())
                 and pat.search(t := p.read_text(encoding="utf-8", errors="replace"))
                 and "assert_may_read" not in t]
    assert offenders == []
