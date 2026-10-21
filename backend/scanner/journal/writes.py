"""Journal writes: alerts, counterfactual rows, decisions and outcomes,
scanner_state (last live bar, Telegram offset), and the MISSED sweep."""
from __future__ import annotations

import sqlite3

import pandas as pd

from backend.scanner.journal.schema import ALERT_STATUSES, PRE_MIGRATION_EPOCH
from backend.scanner.market import _utc

MISSED_AFTER_H = 4               # auto-assign MISSED to alerts unanswered this long


def insert_alert(con, a: dict, *, epoch: int, bar_source: str, status: str = "sent"):
    if status not in ALERT_STATUSES:
        raise ValueError(f"alert status {status!r}")
    con.execute("INSERT INTO alerts (alert_id, alert_ts_utc, level_types, level_price, "
                "price_at_alert, session, confluence_count, inserted_ts_utc, epoch, bar_source, "
                "status) VALUES(?,?,?,?,?,?,?,datetime('now'),?,?,?)",
                (a["alert_id"], a["alert_ts_utc"], a["level_types"], a["level_price"],
                 a["price_at_alert"], a["session"], a["confluence_count"], epoch, bar_source,
                 status))


def last_live_bar(con) -> pd.Timestamp | None:
    """Newest bar a live scanner (either bar source) processed on this journal."""
    row = con.execute("SELECT value FROM scanner_state WHERE key = 'last_live_bar_ts'").fetchone()
    return _utc(row[0]) if row else None


def record_progress(con, ts) -> None:
    con.execute("INSERT INTO scanner_state VALUES('last_live_bar_ts', ?, datetime('now')) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
                "updated_ts_utc = excluded.updated_ts_utc", (str(_utc(ts)),))
    con.commit()


def insert_counterfactual(con, alert_id: str, cf: dict, *, epoch: int):
    con.execute("INSERT INTO counterfactuals (alert_id, fwd_high_1h, fwd_low_1h, mfe_up_1h, "
                "mfe_dn_1h, fwd_high_4h, fwd_low_4h, mfe_up_4h, mfe_dn_4h, computed_ts_utc, epoch) "
                "VALUES(?,?,?,?,?,?,?,?,?,datetime('now'),?)",
                (alert_id, cf["fwd_high_1h"], cf["fwd_low_1h"], cf["mfe_up_1h"], cf["mfe_dn_1h"],
                 cf["fwd_high_4h"], cf["fwd_low_4h"], cf["mfe_up_4h"], cf["mfe_dn_4h"], epoch))


def _alert_epoch(con, alert_id, alert_ts=None) -> int:
    """Epoch of the alert row (newest one if the id exists in several epochs)."""
    sql, args = "SELECT epoch FROM alerts WHERE alert_id=?", [alert_id]
    if alert_ts is not None:
        sql += " AND alert_ts_utc=?"
        args.append(str(alert_ts))
    row = con.execute(sql + " ORDER BY rowid DESC LIMIT 1", args).fetchone()
    if row:
        return row[0]
    last = con.execute("SELECT epoch FROM source_switches ORDER BY rowid DESC LIMIT 1").fetchone()
    return last[0] if last else PRE_MIGRATION_EPOCH


def record_decision(con, alert_id, decision, decision_ts, alert_ts, direction=None,
                    entry=None, stop=None, target=None, reasoning="", source="telegram",
                    epoch: int | None = None, update_id: int | None = None,
                    received_ts=None) -> bool:
    """Append a decision. reasoning_ts == decision_ts, which is the proof that the
    reasoning was recorded at decision time, not reconstructed with hindsight.
    For a Telegram reply, decision_ts is the message's own date (not when the
    scanner processed it, which is received_ts) and update_id is Telegram's
    update id: the journal holds each update id at most once.
    epoch: the decided alert's epoch; None looks it up from (alert_id, alert_ts).
    Returns False, writing nothing, if update_id is already in the journal."""
    if epoch is None:
        epoch = _alert_epoch(con, alert_id, alert_ts)
    mins = (_utc(decision_ts) - _utc(alert_ts)).total_seconds() / 60
    try:
        con.execute("INSERT INTO decisions (alert_id, decision, decision_ts_utc, minutes_from_alert, "
                    "direction, entry, stop, target, reasoning, reasoning_ts_utc, source, epoch, "
                    "telegram_update_id, received_ts_utc) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (alert_id, decision, str(decision_ts), round(mins, 2), direction,
                     entry, stop, target, reasoning, str(decision_ts), source, epoch, update_id,
                     None if received_ts is None else str(_utc(received_ts))))
    except sqlite3.IntegrityError:
        # only the failed statement is undone; nothing else on the connection
        if update_id is None:
            raise
        return False
    con.commit()
    return True


def record_outcome(con, alert_id, exit_price, r_multiple, exit_type, epoch: int | None = None):
    if epoch is None:
        epoch = _alert_epoch(con, alert_id)
    con.execute("INSERT INTO outcomes (alert_id, exit_price, r_multiple, exit_type, "
                "filled_ts_utc, epoch) VALUES(?,?,?,?,datetime('now'),?)",
                (alert_id, exit_price, r_multiple, exit_type, epoch))
    con.commit()


def undecided_alerts(con):
    """(alert_id, alert_ts, epoch) of sent alerts with no decision row in their
    epoch. missed_while_down alerts were never shown, so they never get one."""
    return con.execute(
        "SELECT a.alert_id, a.alert_ts_utc, a.epoch FROM alerts a "
        "LEFT JOIN decisions d ON a.alert_id = d.alert_id AND a.epoch = d.epoch "
        "WHERE d.alert_id IS NULL AND a.status = 'sent'"
    ).fetchall()


def sweep_auto_missed(con, now_utc) -> int:
    """MISSED = never saw it. Auto-assign to any alert with no reply after 4h, so
    it is excluded from the TAKE-vs-SKIP comparison rather than polluting it."""
    now = _utc(now_utc)
    n = 0
    for alert_id, alert_ts, epoch in undecided_alerts(con):
        if (now - _utc(alert_ts)).total_seconds() >= MISSED_AFTER_H * 3600:
            record_decision(con, alert_id, "MISSED", now, alert_ts,
                            reasoning=f"auto: no reply within {MISSED_AFTER_H}h", source="auto",
                            epoch=epoch)
            n += 1
    return n


TELEGRAM_OFFSET_KEY = "telegram_offset"


def load_offset(con) -> int:
    """The getUpdates offset the last run confirmed (0 on a fresh journal)."""
    row = con.execute("SELECT value FROM scanner_state WHERE key = ?", (TELEGRAM_OFFSET_KEY,)).fetchone()
    return int(row[0]) if row else 0


def save_offset(con, offset: int) -> None:
    con.execute("INSERT INTO scanner_state VALUES(?, ?, datetime('now')) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
                "updated_ts_utc = excluded.updated_ts_utc", (TELEGRAM_OFFSET_KEY, str(int(offset))))
    con.commit()


def _journal_new_alert(con, a: dict, *, epoch: int, bar_source: str) -> bool:
    """Insert the alert unless this exact alert is already journaled in this
    epoch, which happens when a bar is redelivered after a crash between the
    journal commit and the ack. Returns True if it was inserted (and so should
    be sent)."""
    if con.execute("SELECT 1 FROM alerts WHERE alert_id=? AND alert_ts_utc=? AND level_types=? "
                   "AND epoch=?", (a["alert_id"], a["alert_ts_utc"], a["level_types"],
                                   epoch)).fetchone():
        return False
    insert_alert(con, a, epoch=epoch, bar_source=bar_source)
    con.commit()
    return True
