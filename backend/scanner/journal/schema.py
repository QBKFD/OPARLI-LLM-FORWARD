"""The journal's tables, its numbered migrations (PRAGMA user_version) and
journal epochs."""
from __future__ import annotations

import sqlite3

from backend.scanner.config import DB_PATH

# ── Append-only SQLite log ─────────────────────────────────────────────────
# Journal epochs. An epoch is one measurement regime of the TAKE-vs-SKIP
# experiment; analysis must not pool epochs without saying so. Epoch 1 is every
# row written before the epoch migration (all from the IBKR live path). Every
# change of bar source starts a NEW epoch, and epochs are never reused: ibkr ->
# stream is epoch 2, a rollback to ibkr is epoch 3, never 1 again. Each change
# is a row in source_switches (start_epoch). Alert ids are numbered per day from
# the bars, so two epochs can reuse an id on a switch day: every child row
# (decision, outcome, counterfactual) carries the epoch of its alert, and joins
# use (alert_id, epoch).
JOURNAL_VERSION = 4              # PRAGMA user_version: 1 epoch columns, 2 source_switches,
                                 # 3 alert status + scanner_state, 4 Telegram update ids + outcome detail
ALERT_STATUSES = ("sent", "missed_while_down", "missed_history")
PRE_MIGRATION_EPOCH = 1          # rows written before the epoch columns existed


def open_log(path=DB_PATH, check_same_thread=True) -> sqlite3.Connection:
    con = sqlite3.connect(str(path), check_same_thread=check_same_thread)
    con.executescript("""
      CREATE TABLE IF NOT EXISTS alerts(
        alert_id TEXT, alert_ts_utc TEXT, level_types TEXT, level_price REAL,
        price_at_alert REAL, session TEXT, confluence_count INTEGER, inserted_ts_utc TEXT);
      CREATE TABLE IF NOT EXISTS decisions(
        alert_id TEXT, decision TEXT, decision_ts_utc TEXT, minutes_from_alert REAL,
        direction TEXT, entry REAL, stop REAL, target REAL, reasoning TEXT,
        reasoning_ts_utc TEXT, source TEXT);
      CREATE TABLE IF NOT EXISTS outcomes(
        alert_id TEXT, exit_price REAL, r_multiple REAL, exit_type TEXT, filled_ts_utc TEXT);
      CREATE TABLE IF NOT EXISTS counterfactuals(
        alert_id TEXT, fwd_high_1h REAL, fwd_low_1h REAL, mfe_up_1h REAL, mfe_dn_1h REAL,
        fwd_high_4h REAL, fwd_low_4h REAL, mfe_up_4h REAL, mfe_dn_4h REAL, computed_ts_utc REAL);
    """)
    _migrate_journal(con)
    return con


def _migrate_journal(con) -> None:
    """Numbered steps, each one transaction, recorded in PRAGMA user_version.
      1  epoch on every table, bar_source on alerts. Existing rows get epoch 1
         and bar_source 'ibkr' (the column defaults).
      2  source_switches(switch_ts_utc, from_source, to_source, epoch). A
         journal that already has alerts gets one row for its current state
         (from_source NULL, the newest alert's bar_source and epoch).
      3  alerts.status ('sent' for every existing row; 'missed_while_down' and
         'missed_history', see AlertSink) and scanner_state(key, value, updated_ts_utc), which holds
         last_live_bar_ts, the newest bar any live scanner processed.
      4  decisions.telegram_update_id (UNIQUE when set: a redelivered Telegram
         update can't be logged twice) and decisions.received_ts_utc (when the
         scanner processed it; decision_ts_utc is the message's own date).
         outcomes gains the scoring detail (decision_rowid, UNIQUE when set, so
         a TAKE is scored once; entry, reason, MAE/MFE in R, bars used, gap).
         Existing rows keep NULL in every new column.
    Code from before step 1 can't write to a migrated journal (its INSERTs are
    positional), so roll back by switching SCANNER_BAR_SOURCE, not by an older
    image."""
    version = con.execute("PRAGMA user_version").fetchone()[0]
    if version < 1:
        have = {t: {r[1] for r in con.execute(f"PRAGMA table_info({t})")}
                for t in ("alerts", "decisions", "outcomes", "counterfactuals")}
        ddl = [f"ALTER TABLE {t} ADD COLUMN epoch INTEGER NOT NULL DEFAULT {PRE_MIGRATION_EPOCH}"
               for t, cols in have.items() if "epoch" not in cols]
        if "bar_source" not in have["alerts"]:
            ddl.append("ALTER TABLE alerts ADD COLUMN bar_source TEXT NOT NULL DEFAULT 'ibkr'")
        con.executescript("BEGIN; " + "; ".join(ddl + ["PRAGMA user_version = 1"]) + "; COMMIT;")
    if version < 2:
        con.execute("BEGIN")
        try:
            con.execute("CREATE TABLE IF NOT EXISTS source_switches(switch_ts_utc TEXT NOT NULL, "
                        "from_source TEXT, to_source TEXT NOT NULL, epoch INTEGER NOT NULL)")
            newest = con.execute("SELECT bar_source, epoch FROM alerts ORDER BY rowid DESC LIMIT 1"
                                 ).fetchone()
            if newest:
                con.execute("INSERT INTO source_switches VALUES(datetime('now'), NULL, ?, ?)", newest)
            con.execute("PRAGMA user_version = 2")
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
    if version < 3:
        con.executescript("""BEGIN;
          ALTER TABLE alerts ADD COLUMN status TEXT NOT NULL DEFAULT 'sent';
          CREATE TABLE IF NOT EXISTS scanner_state(key TEXT PRIMARY KEY, value TEXT NOT NULL,
                                                   updated_ts_utc TEXT NOT NULL);
          PRAGMA user_version = 3;
          COMMIT;""")
    if version < 4:
        con.executescript("""BEGIN;
          ALTER TABLE decisions ADD COLUMN telegram_update_id INTEGER;
          ALTER TABLE decisions ADD COLUMN received_ts_utc TEXT;
          CREATE UNIQUE INDEX decisions_telegram_update ON decisions(telegram_update_id)
            WHERE telegram_update_id IS NOT NULL;
          ALTER TABLE outcomes ADD COLUMN decision_rowid INTEGER;
          ALTER TABLE outcomes ADD COLUMN entry_ts_utc TEXT;
          ALTER TABLE outcomes ADD COLUMN entry_price REAL;
          ALTER TABLE outcomes ADD COLUMN entry_source TEXT;
          ALTER TABLE outcomes ADD COLUMN entry_ref_close REAL;
          ALTER TABLE outcomes ADD COLUMN max_hold_min INTEGER;
          ALTER TABLE outcomes ADD COLUMN reason TEXT;
          ALTER TABLE outcomes ADD COLUMN mae_r REAL;
          ALTER TABLE outcomes ADD COLUMN mfe_r REAL;
          ALTER TABLE outcomes ADD COLUMN bars_used INTEGER;
          ALTER TABLE outcomes ADD COLUMN gap INTEGER;
          CREATE UNIQUE INDEX outcomes_decision ON outcomes(decision_rowid)
            WHERE decision_rowid IS NOT NULL;
          PRAGMA user_version = 4;
          COMMIT;""")


def start_epoch(con, bar_source: str) -> int:
    """The journal epoch this process writes, for its bar source. If the latest
    source_switches row is for the same source, that epoch continues (a
    restart). Otherwise a new epoch, one above every epoch in the journal, and
    a source_switches row (now, previous source, bar_source, epoch)."""
    last = con.execute("SELECT to_source, epoch FROM source_switches ORDER BY rowid DESC LIMIT 1"
                       ).fetchone()
    if last and last[0] == bar_source:
        return last[1]
    top = max(con.execute(f"SELECT COALESCE(MAX(epoch), 0) FROM {t}").fetchone()[0]
              for t in ("source_switches", "alerts", "decisions", "outcomes", "counterfactuals"))
    epoch = top + 1
    con.execute("INSERT INTO source_switches VALUES(datetime('now'), ?, ?, ?)",
                (last[0] if last else None, bar_source, epoch))
    con.commit()
    return epoch
