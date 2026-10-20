"""Journal epochs: the epoch/bar_source migration, a new epoch on every
bar-source change (never reused), and the source_switches log."""

import sqlite3
import threading

import pandas as pd
import pytest

import deterministic_scanner as ds
from scanner_helpers import tg_msg

OLD_DDL = """
  CREATE TABLE alerts(alert_id TEXT, alert_ts_utc TEXT, level_types TEXT, level_price REAL,
    price_at_alert REAL, session TEXT, confluence_count INTEGER, inserted_ts_utc TEXT);
  CREATE TABLE decisions(alert_id TEXT, decision TEXT, decision_ts_utc TEXT,
    minutes_from_alert REAL, direction TEXT, entry REAL, stop REAL, target REAL,
    reasoning TEXT, reasoning_ts_utc TEXT, source TEXT);
  CREATE TABLE outcomes(alert_id TEXT, exit_price REAL, r_multiple REAL, exit_type TEXT,
    filled_ts_utc TEXT);
  CREATE TABLE counterfactuals(alert_id TEXT, fwd_high_1h REAL, fwd_low_1h REAL,
    mfe_up_1h REAL, mfe_dn_1h REAL, fwd_high_4h REAL, fwd_low_4h REAL, mfe_up_4h REAL,
    mfe_dn_4h REAL, computed_ts_utc REAL);
"""


def _alert(id="A20251014-001", ts="2025-10-14 01:00:00", types="PDH"):
    return dict(alert_id=id, alert_ts_utc=ts, level_types=types, level_price=1.0,
                price_at_alert=1.0, session="ASIA", confluence_count=1)


def _old_journal(path):
    con = sqlite3.connect(str(path))
    con.executescript(OLD_DDL + """
      INSERT INTO alerts VALUES('A20251014-001','2025-10-14 01:00:00','PDH',1,1,'ASIA',1,'x');
      INSERT INTO alerts VALUES('A20251014-002','2025-10-14 02:00:00','PDL',1,1,'ASIA',1,'x');
      INSERT INTO decisions VALUES('A20251014-001','SKIP','2025-10-14 01:05:00',5,NULL,NULL,
        NULL,NULL,'r','2025-10-14 01:05:00','telegram');
      INSERT INTO outcomes VALUES('A20251014-001',0,0,'none','x');
      INSERT INTO counterfactuals VALUES('A20251014-001',1,1,0,0,1,1,0,0,'x');
    """)
    con.close()


def _cols(con, t):
    return [r[1] for r in con.execute(f"PRAGMA table_info({t})")]


def _switches(con):
    return con.execute("SELECT from_source, to_source, epoch FROM source_switches ORDER BY rowid"
                       ).fetchall()


# ── migration ───────────────────────────────────────────────────────────────
def test_fresh_journal_has_the_columns_and_no_switch_yet(tmp_path):
    con = ds.open_log(tmp_path / "j.sqlite")
    assert _cols(con, "alerts")[-3:] == ["epoch", "bar_source", "status"]
    assert _cols(con, "counterfactuals")[-1] == "epoch"
    assert _cols(con, "decisions")[-3:] == ["epoch", "telegram_update_id", "received_ts_utc"]
    assert _cols(con, "outcomes")[5:] == ["epoch", "decision_rowid", "entry_ts_utc", "entry_price",
                                          "entry_source", "entry_ref_close", "max_hold_min", "reason",
                                          "mae_r", "mfe_r", "bars_used", "gap"]
    assert _cols(con, "source_switches") == ["switch_ts_utc", "from_source", "to_source", "epoch"]
    assert con.execute("PRAGMA user_version").fetchone()[0] == ds.JOURNAL_VERSION == 4
    assert _switches(con) == []


def test_existing_rows_keep_the_current_epoch(tmp_path):
    p = tmp_path / "old.sqlite"
    _old_journal(p)
    con = ds.open_log(p)
    assert con.execute("SELECT alert_id, epoch, bar_source FROM alerts ORDER BY rowid").fetchall() == \
        [("A20251014-001", 1, "ibkr"), ("A20251014-002", 1, "ibkr")]
    for t in ("decisions", "outcomes", "counterfactuals"):
        assert con.execute(f"SELECT epoch FROM {t}").fetchall() == [(1,)]
    assert con.execute("SELECT decision, reasoning, source FROM decisions").fetchall() == \
        [("SKIP", "r", "telegram")]
    assert _switches(con) == [(None, "ibkr", 1)]                 # the state at migration time
    con.close()
    con = ds.open_log(p)                                         # second open: no-op
    assert _cols(con, "alerts").count("epoch") == 1 and _switches(con) == [(None, "ibkr", 1)]


def test_version_1_journal_gets_the_switch_table(tmp_path):
    """A journal migrated to version 1 (epoch columns, no source_switches) that
    already has stream rows: its state row is the newest alert's source/epoch."""
    p = tmp_path / "v1.sqlite"
    _old_journal(p)
    con = sqlite3.connect(str(p))
    con.executescript("""
      ALTER TABLE alerts ADD COLUMN epoch INTEGER NOT NULL DEFAULT 1;
      ALTER TABLE alerts ADD COLUMN bar_source TEXT NOT NULL DEFAULT 'ibkr';
      ALTER TABLE decisions ADD COLUMN epoch INTEGER NOT NULL DEFAULT 1;
      ALTER TABLE outcomes ADD COLUMN epoch INTEGER NOT NULL DEFAULT 1;
      ALTER TABLE counterfactuals ADD COLUMN epoch INTEGER NOT NULL DEFAULT 1;
      INSERT INTO alerts VALUES('A20251015-001','2025-10-15 01:00:00','PDH',1,1,'ASIA',1,'x',2,'stream');
      PRAGMA user_version = 1;
    """)
    con.close()
    con = ds.open_log(p)
    assert _switches(con) == [(None, "stream", 2)]
    assert ds.start_epoch(con, "stream") == 2
    assert ds.start_epoch(con, "ibkr") == 3


def test_migration_is_one_transaction(tmp_path):
    p = tmp_path / "old.sqlite"
    _old_journal(p)
    con = sqlite3.connect(str(p))
    # SQLite column names are case-insensitive: the third ALTER (outcomes) fails
    # with "duplicate column name" after alerts and decisions were altered
    con.execute("ALTER TABLE outcomes ADD COLUMN EPOCH TEXT")
    con.commit()
    con.close()
    with pytest.raises(sqlite3.OperationalError):
        ds.open_log(p)
    con = sqlite3.connect(str(p))
    assert "epoch" not in _cols(con, "alerts") and "epoch" not in _cols(con, "decisions")
    assert con.execute("PRAGMA user_version").fetchone()[0] == 0


def test_pre_migration_code_cannot_write_a_migrated_journal(tmp_path):
    """Pins the rollback rule in docs/SCANNER_SWITCH_NOTES.md: the old image's
    positional INSERT fails on a migrated journal, so roll back with the switch."""
    con = ds.open_log(tmp_path / "j.sqlite")
    with pytest.raises(sqlite3.OperationalError, match="11 columns but 8 values"):
        con.execute("INSERT INTO alerts VALUES(?,?,?,?,?,?,?,datetime('now'))",
                    ("A1", "t", "PDH", 1.0, 1.0, "ASIA", 1))


# ── epochs per bar-source change ────────────────────────────────────────────
def test_every_switch_starts_a_new_epoch_never_reused(tmp_path):
    p = tmp_path / "old.sqlite"
    _old_journal(p)
    con = ds.open_log(p)
    assert ds.start_epoch(con, "ibkr") == 1                      # same source: epoch 1 continues
    assert ds.start_epoch(con, "ibkr") == 1                      # a restart
    assert ds.start_epoch(con, "stream") == 2                    # the switch
    assert ds.start_epoch(con, "stream") == 2                    # a restart
    assert ds.start_epoch(con, "ibkr") == 3                      # rollback: 3, never 1
    assert ds.start_epoch(con, "stream") == 4
    assert _switches(con) == [(None, "ibkr", 1), ("ibkr", "stream", 2), ("stream", "ibkr", 3),
                              ("ibkr", "stream", 4)]
    ts = [r[0] for r in con.execute("SELECT switch_ts_utc FROM source_switches")]
    assert all(pd.Timestamp(t) for t in ts)                      # timestamps are set


def test_first_start_of_a_fresh_journal(tmp_path):
    con = ds.open_log(tmp_path / "j.sqlite")
    assert ds.start_epoch(con, "stream") == 1
    assert _switches(con) == [(None, "stream", 1)]
    assert ds.start_epoch(con, "ibkr") == 2


def test_new_epoch_is_above_every_epoch_in_the_journal(tmp_path):
    con = ds.open_log(tmp_path / "j.sqlite")
    ds.insert_alert(con, _alert(), epoch=7, bar_source="stream")  # e.g. rows from elsewhere
    con.commit()
    assert ds.start_epoch(con, "ibkr") == 8


def test_replay_into_a_journal_is_its_own_epoch(tmp_path, monkeypatch):
    idx = pd.date_range("2025-06-11", "2025-06-14", freq="1min", tz="UTC", inclusive="left")
    price = pd.Series(100.0, index=idx)
    day3 = idx >= pd.Timestamp("2025-06-13", tz="UTC")
    price[day3 & (idx.hour == 1)] = 105.0           # leave the day open, then return -> DOPEN
    df = pd.DataFrame({"open": price, "high": price + 0.1, "low": price - 0.1, "close": price})
    monkeypatch.setattr(ds, "load_data", lambda: df)
    con = ds.open_log(tmp_path / "j.sqlite")
    assert ds.start_epoch(con, "ibkr") == 1
    out = ds.replay_day("2025-06-13", con)
    assert len(out) >= 1
    assert set(con.execute("SELECT epoch, bar_source FROM alerts").fetchall()) == {(2, "parquet")}
    assert set(con.execute("SELECT epoch FROM counterfactuals").fetchall()) == {(2,)}
    assert ds.start_epoch(con, "ibkr") == 3                       # live after a replay: new epoch


# ── epochs keep same-id alerts apart ────────────────────────────────────────
def test_same_id_in_two_epochs_stays_two_alerts(tmp_path):
    con = ds.open_log(tmp_path / "j.sqlite")
    ds.insert_alert(con, _alert(), epoch=1, bar_source="ibkr")                     # 01:00
    ds._journal_new_alert(con, _alert(ts="2025-10-14 09:00:00", types="NY_L"),
                          epoch=2, bar_source="stream")                            # same id
    con.commit()
    # a decision given the alert's id and ts gets that row's epoch
    ds.record_decision(con, "A20251014-001", "SKIP", pd.Timestamp("2025-10-14 01:10", tz="UTC"),
                       "2025-10-14 01:00:00")
    assert con.execute("SELECT epoch FROM decisions").fetchall() == [(1,)]
    assert ds.undecided_alerts(con) == [("A20251014-001", "2025-10-14 09:00:00", 2)]
    assert ds.sweep_auto_missed(con, pd.Timestamp("2025-10-14 13:00", tz="UTC")) == 1
    assert con.execute("SELECT epoch, decision FROM decisions ORDER BY rowid").fetchall() == \
        [(1, "SKIP"), (2, "MISSED")]
    df = pd.DataFrame({"open": 1.0, "high": 2.0, "low": 0.5, "close": 1.0},
                      index=pd.date_range("2025-10-14", periods=900, freq="1min", tz="UTC"))
    assert ds.sweep_counterfactuals(con, df, pd.Timestamp("2025-10-14 13:00", tz="UTC")) == 2
    assert sorted(con.execute("SELECT epoch FROM counterfactuals").fetchall()) == [(1,), (2,)]
    assert ds.sweep_counterfactuals(con, df, pd.Timestamp("2025-10-14 14:00", tz="UTC")) == 0
    ds.record_outcome(con, "A20251014-001", 1.0, 0.5, "tp")             # newest row -> epoch 2
    assert con.execute("SELECT epoch FROM outcomes").fetchall() == [(2,)]


class FakeTg:
    def __init__(self):
        self.sent = []

    def send(self, text):
        self.sent.append(text)


@pytest.mark.parametrize("current", [2, 3])
def test_reply_prefers_the_current_epoch(tmp_path, current):
    """Both bar sources use _handle_reply. On a switch day an id can exist in
    the previous epoch and the current one: the reply means the current one.
    An id only in an older epoch still finds that alert."""
    con = ds.open_log(tmp_path / "j.sqlite", check_same_thread=False)
    ds.insert_alert(con, _alert(), epoch=current - 1, bar_source="x")
    ds.insert_alert(con, _alert(ts="2025-10-14 09:00:00", types="NY_L"), epoch=current, bar_source="y")
    ds.insert_alert(con, _alert(id="A20251014-007", ts="2025-10-14 03:00:00"), epoch=current - 1,
                    bar_source="x")
    con.commit()
    tg, lock = FakeTg(), threading.Lock()
    ds._handle_reply(con, lock, tg, tg_msg("A20251014-001 SKIP chop", 1), current)
    ds._handle_reply(con, lock, tg, tg_msg("A20251014-007 TAKE long 1 sl 0.5 tp 2 reclaim", 2), current)
    ds._handle_reply(con, lock, tg, tg_msg("A20251014-099 SKIP nope", 3), current)
    assert con.execute("SELECT alert_id, epoch, decision FROM decisions ORDER BY rowid").fetchall() == \
        [("A20251014-001", current, "SKIP"), ("A20251014-007", current - 1, "TAKE")]
    assert tg.sent == ["logged SKIP for A20251014-001", "logged TAKE for A20251014-007",
                       "unknown alert_id A20251014-099"]


def test_both_live_paths_take_their_epoch_from_start_epoch():
    import inspect
    for fn, src_name in ((ds.run_live, '"ibkr"'), (ds.run_live_stream, '"stream"')):
        src = inspect.getsource(fn)
        assert f"start_epoch(con, {src_name})" in src
        assert "_handle_reply(con, lock, tg, msg, epoch)" in src
        assert "offset = load_offset(con)" in src and "save_offset(con, offset)" in src
        assert "sweep_outcomes(con, live.hist.sort_index(), now)" in src
