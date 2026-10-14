"""Step 5: journal epoch + bar_source migration."""

import sqlite3
import threading

import pandas as pd
import pytest

import deterministic_scanner as ds

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


def test_fresh_journal_has_the_columns(tmp_path):
    con = ds.open_log(tmp_path / "j.sqlite")
    assert _cols(con, "alerts")[-2:] == ["epoch", "bar_source"]
    for t in ("decisions", "outcomes", "counterfactuals"):
        assert _cols(con, t)[-1] == "epoch"
    assert con.execute("PRAGMA user_version").fetchone()[0] == ds.JOURNAL_VERSION == 1


def test_existing_rows_keep_the_current_epoch(tmp_path):
    p = tmp_path / "old.sqlite"
    _old_journal(p)
    con = ds.open_log(p)
    assert con.execute("SELECT alert_id, epoch, bar_source FROM alerts ORDER BY rowid").fetchall() == \
        [("A20251014-001", 1, "ibkr"), ("A20251014-002", 1, "ibkr")]
    for t in ("decisions", "outcomes", "counterfactuals"):
        assert con.execute(f"SELECT epoch FROM {t}").fetchall() == [(1,)]
    # the old columns and values are untouched
    assert con.execute("SELECT decision, reasoning, source FROM decisions").fetchall() == \
        [("SKIP", "r", "telegram")]
    con.close()
    con = ds.open_log(p)                                  # second open: no-op
    assert _cols(con, "alerts").count("epoch") == 1
    assert con.execute("SELECT COUNT(*) FROM alerts").fetchone()[0] == 2


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


def test_writers_label_epoch_and_source(tmp_path):
    con = ds.open_log(tmp_path / "j.sqlite")
    ds.insert_alert(con, _alert())                                      # ibkr path (run_live)
    assert ds._journal_new_alert(con, _alert(ts="2025-10-14 09:00:00", types="NY_L"))   # stream
    assert con.execute("SELECT epoch, bar_source FROM alerts ORDER BY rowid").fetchall() == \
        [(1, "ibkr"), (2, "stream")]
    assert ds.JOURNAL_EPOCHS == {"ibkr": 1, "stream": 2}


def test_same_id_in_two_epochs_stays_two_alerts(tmp_path):
    con = ds.open_log(tmp_path / "j.sqlite")
    ds.insert_alert(con, _alert())                                      # epoch 1, 01:00
    ds._journal_new_alert(con, _alert(ts="2025-10-14 09:00:00", types="NY_L"))   # epoch 2, same id
    con.commit()
    # ibkr path: run_live looks the alert up by id (oldest row) and passes its ts;
    # the decision gets that row's epoch
    ds.record_decision(con, "A20251014-001", "SKIP", pd.Timestamp("2025-10-14 01:10", tz="UTC"),
                       "2025-10-14 01:00:00")
    assert con.execute("SELECT epoch FROM decisions").fetchall() == [(1,)]
    # the epoch-2 alert is still undecided; sweeps keep epochs apart
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


def test_stream_reply_prefers_the_stream_epoch(tmp_path):
    con = ds.open_log(tmp_path / "j.sqlite", check_same_thread=False)
    ds.insert_alert(con, _alert())                                      # epoch 1
    ds._journal_new_alert(con, _alert(ts="2025-10-14 09:00:00", types="NY_L"))   # epoch 2
    ds.insert_alert(con, _alert(id="A20251014-007", ts="2025-10-14 03:00:00"))  # epoch 1 only
    con.commit()
    tg, lock = FakeTg(), threading.Lock()
    ds._handle_reply(con, lock, tg, "A20251014-001 SKIP chop")
    ds._handle_reply(con, lock, tg, "A20251014-007 TAKE long 1 sl 0.5 tp 2 reclaim")
    ds._handle_reply(con, lock, tg, "A20251014-099 SKIP nope")
    assert con.execute("SELECT alert_id, epoch, decision, minutes_from_alert < 1e6 FROM decisions "
                       "ORDER BY rowid").fetchall() == \
        [("A20251014-001", 2, "SKIP", 1), ("A20251014-007", 1, "TAKE", 1)]
    assert tg.sent == ["logged SKIP for A20251014-001", "logged TAKE for A20251014-007",
                       "unknown alert_id A20251014-099"]


def test_pre_migration_code_cannot_write_a_migrated_journal(tmp_path):
    """Pins the rollback rule in docs/SCANNER_SWITCH_NOTES.md: the old image's
    positional INSERT fails on a migrated journal, so roll back with the switch."""
    con = ds.open_log(tmp_path / "j.sqlite")
    with pytest.raises(sqlite3.OperationalError, match="10 columns but 8 values"):
        con.execute("INSERT INTO alerts VALUES(?,?,?,?,?,?,?,datetime('now'))",
                    ("A1", "t", "PDH", 1.0, 1.0, "ASIA", 1))


def test_replay_rows_are_labelled_parquet(tmp_path, monkeypatch):
    idx = pd.date_range("2025-06-11", "2025-06-14", freq="1min", tz="UTC", inclusive="left")
    price = pd.Series(100.0, index=idx)
    day3 = idx >= pd.Timestamp("2025-06-13", tz="UTC")
    price[day3 & (idx.hour == 1)] = 105.0           # leave the day open, then return -> DOPEN
    df = pd.DataFrame({"open": price, "high": price + 0.1, "low": price - 0.1, "close": price})
    monkeypatch.setattr(ds, "load_data", lambda: df)
    con = ds.open_log(tmp_path / "j.sqlite")
    out = ds.replay_day("2025-06-13", con)
    assert len(out) >= 1
    assert set(con.execute("SELECT epoch, bar_source FROM alerts").fetchall()) == {(1, "parquet")}
    assert set(con.execute("SELECT epoch FROM counterfactuals").fetchall()) == {(1,)}
