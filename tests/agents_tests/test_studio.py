"""tools/langgraph_studio/studio.py (LangGraph Studio entry point), langgraph.json and
requirements-studio.lock. No server, no network, no LLM: the graph runs
in-process on the session's synthetic replay store."""

import asyncio
import hashlib
import inspect
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from agents.schemas import TriggerEvent
from agents_testkit import STUDIO_PATH, load_studio

studio = load_studio()

ROOT = Path(__file__).resolve().parents[2]
UTC = timezone.utc
PIN = re.compile(r"^([A-Za-z0-9_.\-\[\]]+)==(\S+)")


@pytest.fixture
def fresh_graph(monkeypatch):
    """Clear the process-wide graph before and after a test."""
    monkeypatch.setattr(studio, "_GRAPH", None)
    yield
    studio._GRAPH = None


# --- synthetic data ---------------------------------------------------------

def test_synthetic_rows_are_the_pinned_walk():
    """Same seed, same walk: the agents tests and Studio share this data, so
    a change here would move every replay-based test."""
    rows = studio.synthetic_rows()
    assert len(rows) == 4190
    assert rows[0] == (studio.SYNTH_START, 3300.0, 3300.86, 3299.67, 3300.51, 1.0)
    # the walk the old tests/agents_tests/agents_testkit.py generated, byte for byte
    assert hashlib.sha256(repr(rows).encode()).hexdigest() == \
        "1f687e6c08a97207300cd7a8fdb508ad9bab1f77e54690ed365aa0da797752e0"


def test_synthetic_rows_refuse_naive_or_empty_windows():
    with pytest.raises(ValueError):
        studio.synthetic_rows(datetime(2025, 6, 9), datetime(2025, 6, 10))
    with pytest.raises(ValueError):
        studio.synthetic_rows(studio.SYNTH_END, studio.SYNTH_START)


def test_make_replay_db_refuses_no_rows(tmp_path):
    with pytest.raises(ValueError):
        studio.make_replay_db(tmp_path, [])


def test_record_refuses_to_overwrite(tmp_path):
    p = tmp_path / "datafeed.sqlite"
    p.write_text("x")
    with pytest.raises(FileExistsError):
        studio._record(object(), p)


def test_build_synthetic_db_refuses_an_existing_file(tmp_path):
    p = tmp_path / "s.sqlite"
    p.write_text("x")
    with pytest.raises(FileExistsError):
        studio.build_synthetic_db(p)
    assert p.read_text() == "x"


def test_build_synthetic_db_writes_a_readable_store(tmp_path, monkeypatch):
    from datafeed.store.query import bars_before

    rows = studio.synthetic_rows(studio.SYNTH_START, studio.SYNTH_START + timedelta(hours=2))
    real = studio.make_replay_db
    monkeypatch.setattr(studio, "make_replay_db", lambda d: real(d, rows))
    out = studio.build_synthetic_db(tmp_path / "sub" / "s.sqlite")
    assert [p.name for p in (tmp_path / "sub").iterdir()] == ["s.sqlite"]     # one closed file, no temp dir left
    got = bars_before("XAUUSD", studio.SYNTH_START + timedelta(hours=3), 1000, db=out, kinds=("replay",))
    assert len(got) == len(rows)


# --- parquet replay stores --------------------------------------------------

def test_build_parquet_db_refuses_bars_from_2021(tmp_path):
    with pytest.raises(ValueError, match="development-period"):
        studio.build_parquet_db("x.parquet", datetime(2020, 12, 30, tzinfo=UTC),
                                datetime(2021, 1, 2, tzinfo=UTC), tmp_path / "s.sqlite")
    assert not (tmp_path / "s.sqlite").exists()


def test_build_parquet_db_refuses_naive_times(tmp_path):
    with pytest.raises(ValueError):
        studio.build_parquet_db("x.parquet", datetime(2020, 6, 8), datetime(2020, 6, 9), tmp_path / "s.sqlite")


def test_build_parquet_db_replays_a_development_window(tmp_path):
    pd = pytest.importorskip("pandas")
    from datafeed.store.query import bars_before

    start = datetime(2020, 6, 9, 12, tzinfo=UTC)
    rows = studio.synthetic_rows(start, start + timedelta(hours=1))
    parquet = tmp_path / "bars.parquet"
    pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"]).to_parquet(parquet)
    out = studio.build_parquet_db(parquet, start, start + timedelta(minutes=30), tmp_path / "s.sqlite")
    got = bars_before("XAUUSD", start + timedelta(hours=2), 1000, db=out, kinds=("replay",))
    assert 0 < len(got) <= 30
    assert all(r["bar_ts"] < "2020-06-09T12:30" for r in got)


# --- config and store resolution --------------------------------------------

def test_resolve_db_uses_the_env_path(tmp_path):
    p = tmp_path / "s.sqlite"
    p.write_text("")
    assert studio.resolve_db({studio.ENV_DB: str(p)}) == str(p)


def test_resolve_db_refuses_a_missing_env_path(tmp_path):
    with pytest.raises(FileNotFoundError):
        studio.resolve_db({studio.ENV_DB: str(tmp_path / "nope.sqlite")})


def test_resolve_db_falls_back_to_the_synthetic_store(monkeypatch):
    monkeypatch.setattr(studio, "_synthetic_db", lambda: "synthetic.sqlite")
    assert studio.resolve_db({}) == "synthetic.sqlite"
    assert studio.resolve_db({studio.ENV_DB: ""}) == "synthetic.sqlite"


def test_load_config_is_the_replay_example():
    cfg = studio.load_config({})
    assert cfg.snapshot.mode == "replay" and cfg.debate.enabled is False


def test_load_config_refuses_live_mode(tmp_path):
    p = tmp_path / "live.toml"
    p.write_text(studio.EXAMPLE_CONFIG.read_text().replace('mode = "replay"', 'mode = "live"'))
    with pytest.raises(ValueError, match="replay only"):
        studio.load_config({studio.ENV_CONFIG: str(p)})


def test_load_config_reports_a_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        studio.load_config({studio.ENV_CONFIG: str(tmp_path / "nope.toml")})


# --- the graph factory ------------------------------------------------------

def test_make_graph_is_async_and_builds_once(fresh_graph, monkeypatch, replay_db):
    monkeypatch.setenv(studio.ENV_DB, replay_db)
    assert inspect.iscoroutinefunction(studio.make_graph)
    g1 = asyncio.run(studio.make_graph({"configurable": {}}))
    g2 = asyncio.run(studio.make_graph())
    assert g1 is g2 is studio.studio_graph()
    assert {"trigger", "snapshot", "gate", "technical", "aggregator", "risk", "store"} <= set(g1.get_graph().nodes)


def test_the_studio_graph_runs_the_example_input(fresh_graph, monkeypatch, replay_db):
    monkeypatch.setenv(studio.ENV_DB, replay_db)
    graph = studio.studio_graph()
    out = graph.invoke(studio.example_input())
    s = studio.summarise(out)
    assert s["status"] == "done" and s["skip_reason"] is None
    assert s["decisions"]["blind"]["direction"] == "none"           # the stubs abstain
    assert set(s["votes"]) == {"sentiment", "technical", "visual"}
    assert s["bars"]["1m"] == 240
    json.dumps(s)                                                    # printable


def test_summarise_a_skipped_run(fresh_graph, monkeypatch, replay_db):
    monkeypatch.setenv(studio.ENV_DB, replay_db)
    out = studio.studio_graph().invoke(studio.example_input(datetime(2025, 6, 14, 12, tzinfo=UTC)))
    s = studio.summarise(out)
    assert s["status"] == "skipped" and s["skip_reason"].startswith("stale")
    assert s["snapshot_hash"] is None and s["votes"] == {} and s["decisions"] == {}


def test_the_studio_graph_keeps_the_seal(fresh_graph, monkeypatch, replay_db):
    from agents.snapshot import SealedError

    monkeypatch.setenv(studio.ENV_DB, replay_db)
    with pytest.raises(SealedError):
        studio.studio_graph().invoke(studio.example_input(datetime(2026, 10, 2, 14, tzinfo=UTC)))


def test_example_input_is_a_valid_trigger():
    ev = TriggerEvent.model_validate(studio.example_input()["event"])
    assert ev.t == studio.EXAMPLE_T and ev.source == "studio"


# --- the CLI ----------------------------------------------------------------

def test_cli_run_prints_a_summary(replay_db, capsys):
    assert studio.main(["run", "--db", replay_db, "--t", "2025-06-11T14:30:00Z"]) == 0
    s = json.loads(capsys.readouterr().out)
    assert s["status"] == "done" and s["as_of"] == "2025-06-11T14:30:00+00:00"


def test_cli_run_with_a_missing_store_fails_cleanly(tmp_path, caplog):
    assert studio.main(["run", "--db", str(tmp_path / "nope.sqlite")]) == 1
    assert "FileNotFoundError" in caplog.text


def test_cli_refuses_a_naive_time():
    with pytest.raises(SystemExit) as e:
        studio.main(["run", "--t", "2025-06-11T14:30:00"])
    assert e.value.code == 2


def test_cli_build_db_parquet_needs_a_window(tmp_path):
    with pytest.raises(SystemExit) as e:
        studio.main(["build-db", "--parquet", "x.parquet", "--out", str(tmp_path / "s.sqlite")])
    assert e.value.code == 2


def test_cli_build_db_refuses_holdout_data(tmp_path, caplog):
    rc = studio.main(["build-db", "--parquet", "x.parquet", "--start", "2021-03-01T00:00Z",
                      "--end", "2021-03-02T00:00Z", "--out", str(tmp_path / "s.sqlite")])
    assert rc == 1 and "development-period" in caplog.text


# --- langgraph.json and the Studio lock --------------------------------------

def test_langgraph_json_points_at_the_factory():
    cfg = json.loads((ROOT / "langgraph.json").read_text())
    assert cfg["dependencies"] == ["."]
    assert cfg["graphs"] == {"agents": "./tools/langgraph_studio/studio.py:make_graph"}
    path, attr = cfg["graphs"]["agents"].split(":")
    assert (ROOT / path).resolve() == STUDIO_PATH == Path(studio.__file__).resolve()
    assert callable(getattr(studio, attr))
    assert cfg.get("env", {}) == {}                 # no keys or secrets in the repo


def test_studio_module_imports_no_llm_client():
    src = Path(studio.__file__).read_text()
    assert not re.search(r"^\s*(import|from)\s+(anthropic|openai|langchain\w*|google)", src, re.M)


def _pins(path):
    pins, hashed, cur = {}, {}, None
    for line in path.read_text().splitlines():
        m = PIN.match(line)
        if m:
            cur = m.group(1).split("[")[0].lower().replace("_", "-")
            pins[cur], hashed[cur] = m.group(2), 0
        elif line.strip().startswith("--hash=sha256:") and cur:
            hashed[cur] += 1
    return pins, hashed


def test_studio_lock_extends_the_project_lock_unchanged():
    base, _ = _pins(ROOT / "requirements.lock")
    studio_pins, hashed = _pins(ROOT / "requirements-studio.lock")
    assert all(n > 0 for n in hashed.values())
    assert {k: studio_pins.get(k) for k in base} == base       # every project pin, same version
    assert studio_pins["langgraph-cli"] == "0.4.32"
    assert {"setuptools", "wheel", "langgraph-api", "langgraph-runtime-inmem"} <= set(studio_pins)
