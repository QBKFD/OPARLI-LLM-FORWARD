"""backend/journal_summary.py: the daily summary, read-only, on current and
old journals."""

import importlib.util
import json
import sqlite3
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

import deterministic_scanner as ds

_PATH = Path(__file__).resolve().parents[2] / "backend" / "journal_summary.py"
_spec = importlib.util.spec_from_file_location("journal_summary", _PATH)
js = importlib.util.module_from_spec(_spec)
sys.modules["journal_summary"] = js
_spec.loader.exec_module(js)

DAY = date(2025, 10, 14)
T = lambda s: pd.Timestamp(s, tz="UTC")          # noqa: E731


def _a(i, ts, day="20251014"):
    return dict(alert_id=f"A{day}-{i:03d}", alert_ts_utc=ts, level_types="PDH", level_price=1.0,
                price_at_alert=1.0, session="LONDON", confluence_count=1)


def _journal(path):
    con = ds.open_log(path)
    for i, ts in enumerate(["2025-10-14 08:00:00", "2025-10-14 09:00:00", "2025-10-14 10:00:00",
                            "2025-10-14 11:00:00", "2025-10-14 12:00:00"], 1):
        ds.insert_alert(con, _a(i, ts), epoch=2, bar_source="ibkr")
    ds.insert_alert(con, _a(6, "2025-10-14 13:00:00"), epoch=2, bar_source="ibkr", status="missed_while_down")
    ds.insert_alert(con, _a(7, "2025-10-14 14:00:00"), epoch=3, bar_source="stream")
    ds.insert_alert(con, _a(1, "2025-10-13 09:00:00", "20251013"), epoch=2, bar_source="ibkr")   # other day
    con.commit()

    def dec(i, d, ts, day="20251014", ep=2, uid=None, stop=0.5):
        ds.record_decision(con, f"A{day}-{i:03d}", d, T(ts), ts, "long", 1.0, stop, 2.0, "r", epoch=ep,
                           update_id=uid)

    dec(1, "TAKE", "2025-10-14 08:05:00", uid=1)
    dec(1, "SKIP", "2025-10-14 08:30:00", uid=2)         # revision: not counted
    dec(2, "TAKE", "2025-10-14 09:05:00", uid=3)         # open outcome
    dec(3, "SKIP", "2025-10-14 10:05:00", uid=4)
    dec(4, "MISSED", "2025-10-14 15:00:00")
    dec(7, "TAKE", "2025-10-14 14:05:00", ep=3, uid=5)
    dec(1, "TAKE", "2025-10-13 09:05:00", day="20251013", uid=6)   # an open TAKE from another day
    # outcomes: alert 1 tp +2R; alert 7 unscorable
    ds.insert_outcome(con, "A20251014-001", 2, 1, dict(
        exit_price=2.0, r_multiple=2.0, exit_type="tp", entry_ts_utc=None, entry_price=1.0,
        entry_source="reply", entry_ref_close=None, max_hold_min=240, reason="target",
        mae_r=0.0, mfe_r=2.0, bars_used=3, gap=0))
    ds.insert_outcome(con, "A20251014-007", 3, 6, dict(
        exit_price=None, r_multiple=None, exit_type="unscorable", entry_ts_utc=None, entry_price=1.0,
        entry_source="reply", entry_ref_close=None, max_hold_min=240, reason="no stop (sl)",
        mae_r=None, mfe_r=None, bars_used=None, gap=None))
    con.commit()
    con.close()


def test_counts_per_epoch_first_decisions_and_open_outcomes(tmp_path):
    p = tmp_path / "j.sqlite"
    _journal(p)
    s = js.summarize(js.open_readonly(p), DAY)
    e2, e3 = s.epochs
    assert e2.epoch == 2 and e2.alerts == {"sent": 5, "missed_while_down": 1, "missed_history": 0}
    assert e2.decisions == {"TAKE": 2, "SKIP": 1, "MISSED": 1} and e2.undecided == 1 and e2.revisions == 1
    assert e2.outcomes == {"tp": 1, "sl": 0, "timeout": 0, "unscorable": 0} and e2.sum_r == 2.0
    assert e2.open_outcomes == 1
    assert e3.alerts["sent"] == 1 and e3.decisions["TAKE"] == 1 and e3.outcomes["unscorable"] == 1
    assert s.open_outcomes_all == 2                       # today's alert 2 + yesterday's alert 1
    assert s.journal_version == ds.JOURNAL_VERSION
    text = js.format_summary(s)
    assert "TAKE 2, SKIP 1, MISSED 1, undecided 1 (+1 later replies, not counted)" in text
    assert "open outcomes, whole journal: 2" in text
    assert text.isascii()


def test_an_empty_day(tmp_path):
    p = tmp_path / "j.sqlite"
    _journal(p)
    s = js.summarize(js.open_readonly(p), date(2025, 10, 20))
    assert s.epochs == [] and "no alerts" in js.format_summary(s)


def test_the_journal_is_never_written(tmp_path):
    p = tmp_path / "j.sqlite"
    _journal(p)
    before = p.read_bytes()
    con = js.open_readonly(p)
    with pytest.raises(sqlite3.OperationalError):
        con.execute("INSERT INTO alerts (alert_id) VALUES ('x')")
    assert js.main(["--db", str(p), "--date", "2025-10-14"]) == 0
    assert p.read_bytes() == before


def test_a_missing_journal_is_exit_2_and_is_not_created(tmp_path, capsys):
    p = tmp_path / "none.sqlite"
    assert js.main(["--db", str(p)]) == 2
    assert not p.exists() and "no journal" in capsys.readouterr().err


def test_bad_date_is_a_usage_error(tmp_path):
    with pytest.raises(SystemExit) as e:
        js.main(["--db", str(tmp_path / "j.sqlite"), "--date", "14.10.2025"])
    assert e.value.code == 2


def test_json_output(tmp_path, capsys):
    p = tmp_path / "j.sqlite"
    _journal(p)
    assert js.main(["--db", str(p), "--date", "2025-10-14", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["day"] == "2025-10-14" and [e["epoch"] for e in out["epochs"]] == [2, 3]


def test_a_pre_migration_journal_still_summarises(tmp_path):
    """No epoch, status or outcome-detail columns (the journal before PR #7)."""
    p = tmp_path / "old.sqlite"
    con = sqlite3.connect(str(p))
    con.executescript("""
      CREATE TABLE alerts(alert_id TEXT, alert_ts_utc TEXT, level_types TEXT, level_price REAL,
        price_at_alert REAL, session TEXT, confluence_count INTEGER, inserted_ts_utc TEXT);
      CREATE TABLE decisions(alert_id TEXT, decision TEXT, decision_ts_utc TEXT,
        minutes_from_alert REAL, direction TEXT, entry REAL, stop REAL, target REAL,
        reasoning TEXT, reasoning_ts_utc TEXT, source TEXT);
      CREATE TABLE outcomes(alert_id TEXT, exit_price REAL, r_multiple REAL, exit_type TEXT,
        filled_ts_utc TEXT);
      INSERT INTO alerts VALUES('A20251014-001','2025-10-14 01:00:00','PDH',1,1,'ASIA',1,'x');
      INSERT INTO alerts VALUES('A20251014-002','2025-10-14 02:00:00','PDL',1,1,'ASIA',1,'x');
      INSERT INTO decisions VALUES('A20251014-001','TAKE','2025-10-14 01:05:00',5,'long',1,0.5,2,
        'r','2025-10-14 01:05:00','telegram');
    """)
    con.close()
    s = js.summarize(js.open_readonly(p), DAY)
    (e,) = s.epochs
    assert e.epoch == 1 and e.alerts["sent"] == 2 and e.decisions["TAKE"] == 1 and e.undecided == 1
    assert e.open_outcomes == 1 and s.journal_version == 0
