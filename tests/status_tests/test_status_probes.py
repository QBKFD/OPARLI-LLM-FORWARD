"""The in-container probes: journal_stats on real SQLite journals (current and
old schemas), stream_stats on XREVRANGE-shaped entries. No Docker, no Redis."""

import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROBES = ROOT / "tools" / "status" / "probes"


def _load(name):
    spec = importlib.util.spec_from_file_location(f"probe_{name}", PROBES / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


js, ss = _load("journal_stats"), _load("stream_stats")
NOW = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)
ALWAYS_OPEN = lambda t: True                                       # noqa: E731

V3 = """
CREATE TABLE alerts(alert_id TEXT, alert_ts_utc TEXT, level_types TEXT, level_price REAL,
  price_at_alert REAL, session TEXT, confluence_count INTEGER, inserted_ts_utc TEXT,
  epoch INTEGER NOT NULL DEFAULT 1, bar_source TEXT NOT NULL DEFAULT 'ibkr', status TEXT NOT NULL DEFAULT 'sent');
CREATE TABLE decisions(alert_id TEXT, decision TEXT, decision_ts_utc TEXT, minutes_from_alert REAL,
  direction TEXT, entry REAL, stop REAL, target REAL, reasoning TEXT, reasoning_ts_utc TEXT, source TEXT,
  epoch INTEGER NOT NULL DEFAULT 1);
CREATE TABLE scanner_state(key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_ts_utc TEXT NOT NULL);
"""


def _journal(path, ddl=V3):
    con = sqlite3.connect(str(path))
    con.executescript(ddl)
    return con


def _v3(path):
    con = _journal(path)
    con.executescript("""
      INSERT INTO scanner_state VALUES('last_live_bar_ts', '2026-10-15 11:55:00+00:00', 'x');
      INSERT INTO alerts(alert_id, alert_ts_utc, epoch, status) VALUES
        ('A20251015-001', '2026-10-15 08:00:00', 2, 'sent'),
        ('A20251015-002', '2026-10-15 09:00:00', 2, 'sent'),
        ('A20251015-003', '2026-10-15 10:30:00', 2, 'sent'),
        ('A20251015-004', '2026-10-15 11:00:00', 2, 'missed_while_down'),
        ('A20251014-001', '2026-10-14 22:00:00', 2, 'sent');
      INSERT INTO decisions(alert_id, decision, epoch) VALUES
        ('A20251015-001', 'TAKE', 2), ('A20251015-001', 'SKIP', 2),      -- a revision: first counts
        ('A20251015-002', 'MISSED', 2), ('A20251014-001', 'SKIP', 2);
    """)
    con.commit()
    return con


def test_journal_stats_on_a_current_journal(tmp_path):
    con = _v3(tmp_path / "j.sqlite")
    out = js.stats(con, NOW, ALWAYS_OPEN)
    assert out == {"journal_ok": "1", "scanner_last_bar_ts": "2026-10-15T11:55Z",
                   "scanner_last_bar_age_min": "4", "scanner_last_alert_ts": "2026-10-15T10:30:00Z",
                   "journal_take": "1", "journal_skip": "0", "journal_missed": "1",
                   "journal_undecided": "1", "journal_undecided_oldest_min": "90"}


def test_journal_stats_counts_only_market_open_minutes(tmp_path):
    con = _v3(tmp_path / "j.sqlite")
    out = js.stats(con, NOW, lambda t: t.minute % 2 == 0)
    assert out["scanner_last_bar_age_min"] == "2"


def test_journal_stats_on_an_old_journal(tmp_path):
    con = _journal(tmp_path / "old.sqlite", """
      CREATE TABLE alerts(alert_id TEXT, alert_ts_utc TEXT, level_types TEXT, level_price REAL,
        price_at_alert REAL, session TEXT, confluence_count INTEGER, inserted_ts_utc TEXT);
      CREATE TABLE decisions(alert_id TEXT, decision TEXT, decision_ts_utc TEXT, minutes_from_alert REAL,
        direction TEXT, entry REAL, stop REAL, target REAL, reasoning TEXT, reasoning_ts_utc TEXT, source TEXT);
      INSERT INTO alerts(alert_id, alert_ts_utc) VALUES ('A1', '2026-10-15 11:30:00');
    """)
    out = js.stats(con, NOW, ALWAYS_OPEN)
    assert out["journal_ok"] == "1" and out["scanner_last_bar_age_min"] == "-1"
    assert out["journal_undecided"] == "1" and out["journal_undecided_oldest_min"] == "30"


def test_journal_stats_empty_journal(tmp_path):
    out = js.stats(_journal(tmp_path / "j.sqlite"), NOW, ALWAYS_OPEN)
    assert out["scanner_last_alert_ts"] == "" and out["journal_take"] == "0"
    assert out["journal_undecided_oldest_min"] == "-1"


def _run_probe(name, env):
    return subprocess.run([sys.executable, "-"], stdin=open(PROBES / f"{name}.py"), capture_output=True,
                          text=True, env={**os.environ, **env}, cwd=ROOT, timeout=60)


def test_journal_probe_reports_a_missing_journal_as_a_fact(tmp_path):
    r = _run_probe("journal_stats", {"SCANNER_DB": str(tmp_path / "none.sqlite")})
    assert r.returncode == 0
    assert r.stdout.splitlines() == ["journal_ok=0",
                                     f"journal_error=FileNotFoundError: no journal at {tmp_path / 'none.sqlite'}"]
    assert not (tmp_path / "none.sqlite").exists()


def test_journal_probe_never_writes(tmp_path):
    p = tmp_path / "j.sqlite"
    _v3(p).close()
    before = p.read_bytes()
    r = _run_probe("journal_stats", {"SCANNER_DB": str(p)})
    assert r.returncode == 0 and "journal_ok=1" in r.stdout.splitlines()
    assert p.read_bytes() == before


def _entry(bar_ts, history=False, source="ibkr"):
    return ("1-0", {"json": json.dumps({"bar_ts": bar_ts, "history": history, "source": source})})


def test_stream_stats_takes_the_newest_live_bar():
    entries = [("9-0", {"json": "not json"}), ("8-0", {"other": "x"}),
               _entry("2026-10-15T11:58:00+00:00", history=True), _entry("2026-10-15T11:50:00+00:00"),
               _entry("2026-10-15T11:40:00+00:00")]
    assert ss.stats(entries, NOW, ALWAYS_OPEN) == {"feed_last_bar_ts": "2026-10-15T11:50Z",
                                                   "feed_last_bar_age_min": "9"}


def test_stream_stats_without_a_live_bar():
    assert ss.stats([_entry("2026-10-15T11:58:00+00:00", history=True)], NOW, ALWAYS_OPEN) == \
        {"feed_last_bar_age_min": "-1"}
    assert ss.stats([], NOW, ALWAYS_OPEN) == {"feed_last_bar_age_min": "-1"}


def test_stream_probe_reports_failures_as_a_fact():
    env = {k: v for k, v in os.environ.items() if k != "REDIS_URL"}
    r = subprocess.run([sys.executable, "-"], stdin=open(PROBES / "stream_stats.py"), capture_output=True,
                       text=True, env=env, cwd=ROOT, timeout=60)
    assert r.returncode == 0 and r.stdout.startswith("feed_probe_error=KeyError")
