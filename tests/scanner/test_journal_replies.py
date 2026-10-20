"""Telegram replies: the decision time is the message's own date, an update id
is journaled at most once (UNIQUE index), and the getUpdates offset survives a
restart. Journal migration 4."""

import sqlite3
import threading

import pandas as pd
import pytest

import deterministic_scanner as ds
from scanner_helpers import Log, tg_msg

CHAT = "42"


def _alert(id="A20251014-001", ts="2025-10-14 01:00:00"):
    return dict(alert_id=id, alert_ts_utc=ts, level_types="PDH", level_price=1.0,
                price_at_alert=1.0, session="ASIA", confluence_count=1)


class FakeTg:
    def __init__(self):
        self.sent = []

    def send(self, text):
        self.sent.append(text)


def _journal(tmp_path, *alerts, epoch=2):
    con = ds.open_log(tmp_path / "j.sqlite", check_same_thread=False)
    for a in alerts or (_alert(),):
        ds.insert_alert(con, a, epoch=epoch, bar_source="ibkr")
    con.commit()
    return con


def _update(update_id, text="A1 SKIP x", chat=CHAT, date=1760403600, key="message"):
    return {"update_id": update_id, key: {"chat": {"id": int(chat)}, "date": date, "text": text}}


# ── parsing getUpdates ─────────────────────────────────────────────────────
def test_parse_updates_keeps_id_date_and_text():
    msgs, nxt = ds.parse_updates([_update(7, "A1 TAKE long 1 sl 0.5 tp 2", date=1760403600)], CHAT, 0)
    assert msgs == [ds.TelegramMessage(7, pd.Timestamp("2025-10-14 01:00:00", tz="UTC"),
                                       "A1 TAKE long 1 sl 0.5 tp 2")]
    assert nxt == 8


def test_parse_updates_skips_other_chats_edits_and_non_text_but_moves_the_offset():
    updates = [_update(10, chat="99"),                                   # another chat
               _update(11, key="edited_message"),                       # an edit, not a reply
               {"update_id": 12, "message": {"chat": {"id": 42}, "date": 1760403600}},   # no text
               _update(13)]
    msgs, nxt = ds.parse_updates(updates, CHAT, 5)
    assert [m.update_id for m in msgs] == [13] and nxt == 14


@pytest.mark.parametrize("date", [None, "1760403600", 0, -5, True, 1.5])
def test_a_message_without_a_usable_date_is_skipped_never_given_another_time(date):
    log = Log()
    msgs, nxt = ds.parse_updates([_update(3, date=date)], CHAT, 0, log=log)
    assert msgs == [] and nxt == 4
    assert "no usable message date" in " ".join(log)


def test_parse_updates_with_nothing_keeps_the_offset():
    assert ds.parse_updates([], CHAT, 17) == ([], 17)


def test_poll_returns_parsed_messages(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", CHAT)
    tg = ds.Telegram()
    seen = {}

    def fake_get(method, params):                  # no network
        seen.update(method=method, **params)
        return {"ok": True, "result": [_update(21, "A1 SKIP x")]}

    monkeypatch.setattr(tg, "_get", fake_get)
    msgs, nxt = tg.poll(20)
    assert seen == {"method": "getUpdates", "offset": 20, "timeout": 60}
    assert [(m.update_id, m.text) for m in msgs] == [(21, "A1 SKIP x")] and nxt == 22


# ── decision time = message date ───────────────────────────────────────────
def test_decision_time_is_the_message_date_not_the_processing_time(tmp_path):
    con, tg = _journal(tmp_path), FakeTg()
    ds._handle_reply(con, threading.Lock(), tg, tg_msg("A20251014-001 SKIP chop", 5, "2025-10-14 01:42:07"), 2)
    row = con.execute("SELECT decision_ts_utc, reasoning_ts_utc, minutes_from_alert, telegram_update_id, "
                      "received_ts_utc, epoch FROM decisions").fetchone()
    assert row[:4] == ("2025-10-14 01:42:07+00:00", "2025-10-14 01:42:07+00:00", 42.12, 5)
    assert pd.Timestamp(row[4]) > pd.Timestamp("2026-01-01", tz="UTC")     # processed now, stored apart
    assert row[5] == 2
    assert tg.sent == ["logged SKIP for A20251014-001"]


# ── dedupe on update_id ────────────────────────────────────────────────────
def test_a_redelivered_update_is_journaled_once_and_confirmed_once(tmp_path):
    con, tg, log = _journal(tmp_path), FakeTg(), Log()
    m = tg_msg("A20251014-001 TAKE long 1 sl 0.5 tp 2 reclaim", 9, "2025-10-14 01:10:00")
    ds._handle_reply(con, threading.Lock(), tg, m, 2, log=log)
    ds._handle_reply(con, threading.Lock(), tg, m, 2, log=log)      # e.g. after a restart
    assert con.execute("SELECT COUNT(*) FROM decisions").fetchone()[0] == 1
    assert tg.sent == ["logged TAKE for A20251014-001"]
    assert "telegram update 9 already journaled" in " ".join(log)


def test_the_unique_constraint_is_in_the_database(tmp_path):
    con = _journal(tmp_path)
    assert ds.record_decision(con, "A20251014-001", "SKIP", pd.Timestamp("2025-10-14 01:05", tz="UTC"),
                              "2025-10-14 01:00:00", epoch=2, update_id=77)
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("INSERT INTO decisions (alert_id, decision, epoch, telegram_update_id) "
                    "VALUES('x', 'SKIP', 2, 77)")
    assert not ds.record_decision(con, "A20251014-001", "TAKE", pd.Timestamp("2025-10-14 01:06", tz="UTC"),
                                  "2025-10-14 01:00:00", epoch=2, update_id=77)
    assert con.execute("SELECT decision FROM decisions").fetchall() == [("SKIP",)]


def test_a_duplicate_does_not_discard_other_uncommitted_work(tmp_path):
    con = _journal(tmp_path)
    ds.record_decision(con, "A20251014-001", "SKIP", pd.Timestamp("2025-10-14 01:05", tz="UTC"),
                       "2025-10-14 01:00:00", epoch=2, update_id=1)
    ds.insert_alert(con, _alert("A20251014-002", "2025-10-14 02:00:00"), epoch=2, bar_source="ibkr")
    assert not ds.record_decision(con, "A20251014-001", "SKIP", pd.Timestamp("2025-10-14 01:05", tz="UTC"),
                                  "2025-10-14 01:00:00", epoch=2, update_id=1)
    con.commit()
    assert con.execute("SELECT COUNT(*) FROM alerts").fetchone()[0] == 2


def test_decisions_without_an_update_id_are_not_deduplicated(tmp_path):
    con = _journal(tmp_path)
    for _ in range(2):                                              # e.g. auto MISSED rows
        assert ds.record_decision(con, "A20251014-001", "MISSED", pd.Timestamp("2025-10-14 05:00", tz="UTC"),
                                  "2025-10-14 01:00:00", source="auto", epoch=2)
    assert con.execute("SELECT COUNT(*) FROM decisions").fetchone()[0] == 2


# ── confirmations ──────────────────────────────────────────────────────────
@pytest.mark.parametrize("text, note", [
    ("A20251014-001 TAKE long 4128 tp 4140 no stop", "outcome not scorable: no stop (sl)"),
    ("A20251014-001 TAKE long 4128 sl 4122 x", "outcome not scorable: no target (tp)"),
    ("A20251014-001 TAKE 4128 sl 4122 tp 4140", "outcome not scorable: no direction (long/short)"),
    ("A20251014-001 TAKE long 4128 sl 4130 tp 4140", "outcome not scorable: a long needs stop < entry < target"),
])
def test_a_take_that_cant_be_scored_is_logged_and_says_so(tmp_path, text, note):
    con, tg = _journal(tmp_path), FakeTg()
    ds._handle_reply(con, threading.Lock(), tg, tg_msg(text, 1), 2)
    assert con.execute("SELECT decision FROM decisions").fetchall() == [("TAKE",)]
    assert tg.sent == [f"logged TAKE for A20251014-001 ({note})"]


def test_a_second_reply_is_logged_and_the_confirmation_says_the_first_counts(tmp_path):
    con, tg = _journal(tmp_path), FakeTg()
    ds._handle_reply(con, threading.Lock(), tg, tg_msg("A20251014-001 SKIP chop", 1), 2)
    ds._handle_reply(con, threading.Lock(), tg, tg_msg("A20251014-001 TAKE long 1 sl 0.5 tp 2 x", 2,
                                                       "2025-10-14 01:40:00"), 2)
    assert [r[0] for r in con.execute("SELECT decision FROM decisions ORDER BY rowid")] == ["SKIP", "TAKE"]
    assert tg.sent[1] == "logged TAKE for A20251014-001 (already decided SKIP; the first decision counts)"


def test_garbage_and_unknown_ids_write_nothing(tmp_path):
    con, tg = _journal(tmp_path), FakeTg()
    ds._handle_reply(con, threading.Lock(), tg, tg_msg("hello there", 1), 2)
    ds._handle_reply(con, threading.Lock(), tg, tg_msg("A20251014-999 SKIP x", 2), 2)
    assert con.execute("SELECT COUNT(*) FROM decisions").fetchone()[0] == 0
    assert tg.sent == ["unknown alert_id A20251014-999"]


# ── offset across restarts ─────────────────────────────────────────────────
def test_the_offset_is_stored_in_the_journal(tmp_path):
    con = _journal(tmp_path)
    assert ds.load_offset(con) == 0
    ds.save_offset(con, 123)
    ds.save_offset(con, 130)
    con.close()
    con = ds.open_log(tmp_path / "j.sqlite")
    assert ds.load_offset(con) == 130
    assert con.execute("SELECT COUNT(*) FROM scanner_state WHERE key = 'telegram_offset'").fetchone()[0] == 1


def test_restart_before_the_offset_was_saved_journals_nothing_twice(tmp_path):
    """The loop handles a batch, then saves the offset. A crash between the two
    redelivers the batch on restart; the update ids keep it to one row each."""
    con, tg = _journal(tmp_path, _alert(), _alert("A20251014-002", "2025-10-14 02:00:00")), FakeTg()
    batch = [tg_msg("A20251014-001 SKIP a", 31), tg_msg("A20251014-002 SKIP b", 32)]
    for m in batch:
        ds._handle_reply(con, threading.Lock(), tg, m, 2)
    for m in batch:                                         # restart: offset still 0, same updates again
        ds._handle_reply(con, threading.Lock(), tg, m, 2)
    assert con.execute("SELECT telegram_update_id FROM decisions ORDER BY rowid").fetchall() == [(31,), (32,)]
    assert len(tg.sent) == 2


# ── migration 4 ────────────────────────────────────────────────────────────
def test_migration_4_keeps_existing_rows_and_is_idempotent(tmp_path):
    p = tmp_path / "v3.sqlite"
    con = ds.open_log(p)
    con.execute("PRAGMA user_version = 3")
    con.close()
    raw = sqlite3.connect(str(p))          # rebuild a real v3 journal: drop the v4 columns' effect
    raw.executescript("""
      DROP INDEX decisions_telegram_update; DROP INDEX outcomes_decision;
      CREATE TABLE d3 AS SELECT alert_id, decision, decision_ts_utc, minutes_from_alert, direction,
        entry, stop, target, reasoning, reasoning_ts_utc, source, epoch FROM decisions;
      DROP TABLE decisions; ALTER TABLE d3 RENAME TO decisions;
      CREATE TABLE o3 AS SELECT alert_id, exit_price, r_multiple, exit_type, filled_ts_utc, epoch
        FROM outcomes;
      DROP TABLE outcomes; ALTER TABLE o3 RENAME TO outcomes;
      INSERT INTO decisions VALUES('A1','SKIP','2025-10-14 01:05:00',5,NULL,NULL,NULL,NULL,'r',
        '2025-10-14 01:05:00','telegram',2);
      PRAGMA user_version = 3;
    """)
    raw.close()
    con = ds.open_log(p)
    ds.open_log(p).close()
    assert con.execute("PRAGMA user_version").fetchone()[0] == 4
    assert con.execute("SELECT alert_id, telegram_update_id, received_ts_utc FROM decisions").fetchall() == \
        [("A1", None, None)]
    idx = {r[1] for r in con.execute("PRAGMA index_list(decisions)")} | \
          {r[1] for r in con.execute("PRAGMA index_list(outcomes)")}
    assert {"decisions_telegram_update", "outcomes_decision"} <= idx
