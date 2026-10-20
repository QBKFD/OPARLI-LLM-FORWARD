"""agents.epoch: any change of an input is a new epoch id, and the id, the
config's SHA-256 and the prompts' SHA-256s are stored on every run and
decision."""

import json
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from agents import epoch as ep
from agents.config import AgentsConfig
from agents.graph import build_graph, run_once
from agents.owner_config import OwnerConfig
from agents.prompting.files import load_all
from agents.schemas import TriggerEvent
from agents.store import SqliteAgentStore
from agents.store.memory import MemoryStore

HERE = Path(__file__).resolve().parent
OK = (HERE / "owner_config_ok.toml").read_text()
UTC = timezone.utc
T = datetime(2025, 6, 11, 14, 30, 30, tzinfo=UTC)
CFG = AgentsConfig.model_validate({"snapshot": {"mode": "replay"}})
VISUAL = "---\nagent: visual\nversion: 1\n---\n## system\nSYSTEXT\n## user\n{{alert_json}}\n"


@pytest.fixture
def prompts(tmp_path):
    d = tmp_path / "prompts"
    d.mkdir()
    (d / "visual.md").write_text(VISUAL)
    return d


def epoch_of(prompts_dir, text=OK, **kw):
    return ep.compute_epoch(load_all(prompts_dir), OwnerConfig.from_text(text), **kw)


def test_same_inputs_same_id(prompts):
    a, b = epoch_of(prompts), epoch_of(prompts)
    assert a.id == b.id and len(a.id) == 64
    assert a.inputs["prompts"]["visual"] == {"sha256": a.prompts_sha256["visual"], "version": "1"}
    assert a.prompts_sha256["technical"] == "absent"
    assert a.inputs["prompts"]["technical"] == {"sha256": None, "problem": "prompt_invalid: technical.md: not found"}


def test_the_epoch_does_not_depend_on_where_the_files_are(prompts, tmp_path):
    """The same prompts and config in another checkout give the same epoch."""
    other = tmp_path / "elsewhere"
    shutil.copytree(prompts, other)
    assert epoch_of(other).id == epoch_of(prompts).id
    missing = [ep.compute_epoch(load_all(d), OwnerConfig.load(d / "agents.toml")).id for d in (prompts, other)]
    assert missing[0] == missing[1]


@pytest.mark.parametrize("change", [
    lambda d: (d / "visual.md").write_text(VISUAL.replace("SYSTEXT", "SYSTEXT ")),       # one space in a prompt
    lambda d: (d / "visual.md").write_text(VISUAL + "<!-- a note -->\n"),        # a comment
    lambda d: (d / "technical.md").write_text(VISUAL.replace("visual", "technical")),   # a new prompt
    lambda d: (d / "visual.md").unlink(),                                        # a removed prompt
])
def test_any_prompt_change_is_a_new_epoch(prompts, change):
    before = epoch_of(prompts).id
    change(prompts)
    assert epoch_of(prompts).id != before


@pytest.mark.parametrize("old, new", [
    ('model = "claude-opus-5-5"', 'model = "claude-sonnet-5-5"'),                 # visual round 1 only
    ('effort = "low"', 'effort = "medium"'),                                      # visual discussion only
    ("max_calls_per_day = 40", "max_calls_per_day = 39"),                         # any value at all
    ('rule = "quorum_v0"', 'rule = "fixed_meta_v1"'),
])
def test_any_config_value_change_is_a_new_epoch(prompts, old, new):
    assert epoch_of(prompts, OK.replace(old, new, 1)).id != epoch_of(prompts).id


def test_comments_and_formatting_in_the_config_are_not_a_new_epoch(prompts):
    assert epoch_of(prompts, "# note\n" + OK.replace("rounds = 3", "rounds   =   3  # same")).id == \
        epoch_of(prompts).id


def test_the_run_context_and_the_code_are_in_the_epoch(prompts, tmp_path):
    base = epoch_of(prompts)
    assert epoch_of(prompts, journal_epoch=7).id != base.id
    assert epoch_of(prompts, meta_rule_version="2").id != base.id           # quorum_v0 is version "1"
    assert base.inputs["meta"] == {"rule": "quorum_v0", "version": "1"}
    assert epoch_of(prompts, sigma_model_sha256="ab" * 32).id != base.id
    code = tmp_path / "code"
    shutil.copytree(ep.PKG, code, ignore=shutil.ignore_patterns("__pycache__", "prompts", "experiments"))
    assert epoch_of(prompts, code_root=code).id == base.id
    (code / "prompting" / "render.py").write_text((code / "prompting" / "render.py").read_text() + "\n# x\n")
    assert epoch_of(prompts, code_root=code).id != base.id


def test_a_missing_input_code_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        ep.input_code_sha256(tmp_path)


def test_calls_are_recorded_per_agent_and_round(prompts):
    calls = epoch_of(prompts).inputs["calls"]
    assert calls["visual"]["round1"]["model"] == "claude-opus-5-5"
    assert calls["visual"]["discussion"] == {"model": "claude-sonnet-5-5", "effort": "low", "max_tokens": 8000}
    assert calls["sentiment"]["round1"] == {"problem": "config_invalid: agents.sentiment.enabled: disabled in config"}


def test_diff_and_version_warnings(prompts):
    old = epoch_of(prompts).inputs
    (prompts / "visual.md").write_text(VISUAL.replace("SYSTEXT", "changed"))
    new = epoch_of(prompts, OK.replace("max_calls_per_day = 40", "max_calls_per_day = 1")).inputs
    assert ep.diff_inputs(old, new) == ["owner_config.sha256", "prompts.visual.sha256"]
    assert ep.version_warnings(old, new) == ["visual.md changed but its version is still '1'"]
    (prompts / "visual.md").write_text(VISUAL.replace("version: 1", "version: 2").replace("sys", "changed"))
    assert ep.version_warnings(old, epoch_of(prompts).inputs) == []
    assert ep.diff_inputs(1, 2) == ["(root)"]


# ---- stamped on every run and decision

def ev(t=T):
    return TriggerEvent(source="test", t=t, trigger_id="1")


def test_the_graph_stamps_every_decision(replay_db, prompts):
    owner = OwnerConfig.from_text(OK)
    out = run_once(build_graph(CFG, MemoryStore(), replay_db, owner_config=owner, prompts_dir=prompts), ev())
    want = epoch_of(prompts)
    d = out["final_decisions"]["blind"]
    assert out["epoch_id"] == d.epoch_id == want.id
    assert d.owner_config_sha256 == owner.sha256 and d.prompts_sha256 == want.prompts_sha256


def test_a_missing_config_is_part_of_the_epoch_not_a_crash(replay_db, prompts, monkeypatch):
    monkeypatch.delenv("AGENTS_CONFIG", raising=False)
    out = run_once(build_graph(CFG, MemoryStore(), replay_db, prompts_dir=prompts), ev())
    d = out["final_decisions"]["blind"]
    assert out["status"] == "done" and d.owner_config_sha256 is None and len(d.epoch_id) == 64


def test_the_store_keeps_the_epoch(tmp_path, replay_db, prompts):
    store = SqliteAgentStore(str(tmp_path / "a.sqlite"))
    owner = OwnerConfig.from_text(OK)
    out = run_once(build_graph(CFG, store, replay_db, owner_config=owner, prompts_dir=prompts), ev(), run_id="r1")
    rec = store.load_run("r1")
    assert rec["decisions"] == (out["final_decisions"]["blind"], out["final_decisions"]["discussion"])
    assert {d.epoch_id for d in rec["decisions"]} == {out["epoch_id"]}
    assert rec["run"].epoch_id == out["epoch_id"]
    assert json.loads(rec["run"].epoch_inputs_json) == epoch_of(prompts).inputs
    assert ep.last_stored_inputs(store.path) == epoch_of(prompts).inputs
    skipped = run_once(build_graph(CFG, store, replay_db, owner_config=owner, prompts_dir=prompts),
                       ev(datetime(2025, 6, 12, 10, 0, tzinfo=UTC)), run_id="s1")
    assert skipped["status"] == "skipped" and store.load_run("s1")["run"].epoch_id == out["epoch_id"]
    store.close()


def test_a_v2_store_is_migrated_to_v3(tmp_path, monkeypatch):
    from agents.store import sqlite as sq
    p = str(tmp_path / "v2.sqlite")
    monkeypatch.setattr(sq, "MIGRATIONS", sq.MIGRATIONS[:2])
    sq.SqliteAgentStore(p).close()
    monkeypatch.undo()
    s = SqliteAgentStore(p)
    cols = lambda t: {r[1] for r in s.con.execute(f"PRAGMA table_info({t})")}     # noqa: E731
    assert {"epoch_id", "epoch_inputs_json"} <= cols("runs")
    assert {"epoch_id", "owner_config_sha256", "prompts_sha256_json"} <= cols("decisions")
    s.close()


def test_a_changed_prompt_with_the_same_version_logs_a_warning(tmp_path, replay_db, prompts, caplog):
    store = SqliteAgentStore(str(tmp_path / "a.sqlite"))
    owner = OwnerConfig.from_text(OK)
    run_once(build_graph(CFG, store, replay_db, owner_config=owner, prompts_dir=prompts), ev(), run_id="r1")
    (prompts / "visual.md").write_text(VISUAL.replace("SYSTEXT", "edited"))
    build_graph(CFG, store, replay_db, owner_config=owner, prompts_dir=prompts)
    assert "visual.md changed but its version is still '1'" in caplog.text
    store.close()


# ---- the command

def test_epoch_command(tmp_path, replay_db, prompts, capsys):
    cfg_path = tmp_path / "agents.toml"
    cfg_path.write_text(OK)
    assert ep.main(["--config", str(cfg_path), "--prompts", str(prompts)]) == 0
    out = capsys.readouterr().out
    assert out.startswith(f"epoch_id {epoch_of(prompts).id}\n")
    db = str(tmp_path / "a.sqlite")
    assert ep.main(["--config", str(cfg_path), "--prompts", str(prompts), "--db", db]) == 0
    assert "no stored run with an epoch yet" in capsys.readouterr().out
    store = SqliteAgentStore(db)
    run_once(build_graph(CFG, store, replay_db, owner_config=OwnerConfig.load(cfg_path), prompts_dir=prompts), ev())
    store.close()
    assert ep.main(["--config", str(cfg_path), "--prompts", str(prompts), "--db", db]) == 0
    assert "unchanged since the last stored run" in capsys.readouterr().out
    cfg_path.write_text(OK.replace("rounds = 3", "rounds = 4"))
    ep.main(["--config", str(cfg_path), "--prompts", str(prompts), "--db", db])
    assert "changed since the last stored run:\n  owner_config.sha256" in capsys.readouterr().out


def test_epoch_command_with_a_bad_db(tmp_path, prompts, capsys):
    bad = tmp_path / "not.sqlite"
    bad.write_text("not a database")
    assert ep.main(["--prompts", str(prompts), "--db", str(bad)]) == 1
    assert "cannot read" in capsys.readouterr().err
    sqlite3.connect(":memory:").close()
