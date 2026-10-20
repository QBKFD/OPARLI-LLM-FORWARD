# backend/: the deterministic scanner and its decision journal

- `deterministic_scanner.py`: the level scanner (replay, selftest, live in
  ibkr or stream mode) and the journal it writes.
- `journal_summary.py`: a read-only daily summary of the journal.
- `services/tws_connector.py`: the IBKR feed used by the ibkr bar source.

The scanner's design notes are in the module docstring,
`docs/SCANNER_SWITCH_NOTES.md` and `docs/DEPLOY_RUNBOOK.md`. This README
covers the journal.

## The journal

SQLite, `$SCANNER_DB` (default `database/scanner_decisions.sqlite`, gitignored).
It is append-only in use, and versioned by `PRAGMA user_version`
(`JOURNAL_VERSION`, currently 4). It migrates on the first open by a newer
scanner.

| Table | One row per |
|---|---|
| `alerts` | alert, with `status` sent / missed_while_down / missed_history and its `epoch` |
| `decisions` | reply (TAKE / SKIP) or auto MISSED |
| `outcomes` | scored TAKE |
| `counterfactuals` | alert: forward MFE/MAE at 1h and 4h |
| `source_switches`, `scanner_state` | epoch changes; last live bar and the Telegram offset |

### Replies: decision time and dedupe (migration 4)

- **Decision time** (`decision_ts_utc` = `reasoning_ts_utc`) is the Telegram
  message's own `date`: when you sent it, in whole seconds. The time the
  scanner processed it is stored apart, in `received_ts_utc`.
  `minutes_from_alert` uses the message date.
- **Dedupe:** `decisions.telegram_update_id` has a UNIQUE index (for non-null
  values), so a Telegram update can be journaled only once. A redelivered
  update (after a restart, or a crash between handling a batch and
  confirming it) is skipped, with no second confirmation message.
- **Offset:** the `getUpdates` offset is saved in `scanner_state`
  (`telegram_offset`) after each batch, so a restart resumes where it left
  off instead of re-reading unconfirmed updates.
- **The first decision per alert and epoch counts.** A later reply is
  journaled, and the confirmation says `already decided <X>; the first
  decision counts`. Revisions are neither scored nor counted.
- **Confirmation of a TAKE that can't be scored**, e.g. `logged TAKE for
  A20251018-003 (outcome not scorable: no stop (sl))`. The TAKE is logged
  anyway; reply again only if you want a revision on record.

Reply format (unchanged): `<id> TAKE long|short <entry> sl <stop> tp <target> <reason>`
or `<id> SKIP <reason>`. The entry may be left out.

### Outcomes of TAKEs

Every 5 minutes, with the MISSED and counterfactual sweeps, `sweep_outcomes`
scores each first-decision TAKE that has no outcome yet. It uses
`scoring.score` on the scanner's 1-min bars:

- **Entry:**
  - your entry price, at your message's time (`entry_source = reply`;
    scoring starts at the first full minute after the message);
  - without an entry, the open of the first bar at or after the message
    (`market_open`).
  - `entry_ref_close` stores the last close before the message, so a stated
    entry far from the market shows up.
- **Exit:** your stop and target. The stop is checked before the target: a
  bar holding both is a stop. A gap through the stop fills at the open, a gap
  through the target at the target. Without either within
  `OUTCOME_MAX_HOLD_MIN` (240 min), it's a **timeout** at the last bar's
  close.
- **When it's written:** a stop or target as soon as the bar that hits it is
  in; a timeout `OUTCOME_SETTLE_MIN` (5 min) after the 240 minutes end.
- **Row:** `exit_type` tp / sl / timeout, `exit_price`, `r_multiple` (R =
  move / |entry − stop|), `mae_r`, `mfe_r`, `bars_used`, `gap`, the entry
  fields, `max_hold_min` and `decision_rowid`. `decision_rowid` has a UNIQUE
  index, so a decision is scored once.
- **Unscorable** (`exit_type = unscorable`, `reason` says why, R null):
  - no direction, stop or target;
  - stop and target on the wrong side of the entry;
  - no bar in the holding period.

`record_outcome()` (manual) still exists. An alert that already has any
outcome row is never auto-scored.

## Daily summary

```bash
python backend/journal_summary.py                         # today (UTC), $SCANNER_DB or the default path
python backend/journal_summary.py --date 2026-10-19 --db database/scanner_decisions.sqlite
python backend/journal_summary.py --json                  # machine-readable
# on the host, against the production journal (read-only):
docker compose exec -T scanner python /app/journal_summary.py
```

Example output:

```
journal 2026-10-19 (UTC), journal version 4
  epoch 1
    alerts    5: sent 5, missed_while_down 0, missed_history 0
    decisions TAKE 2, SKIP 2, MISSED 0, undecided 1
    outcomes  tp 1, sl 0, timeout 0, unscorable 0, open 1; sum R +2.00
  open outcomes, whole journal: 1
```

- Counts are per epoch, never pooled.
- "decisions" counts first decisions only.
- "open" is the day's TAKEs without an outcome yet. The last line counts
  open TAKEs from any day.
- The journal is opened with `mode=ro`, so the command can't create, migrate
  or write it.
- Exit 2 if there is no journal file.

## Tests

```bash
REDIS_TEST_URL=redis://localhost:6379/15 python -m pytest tests/scanner -q
python backend/deterministic_scanner.py --selftest       # needs the historical parquet
```

`tests/scanner/test_journal_replies.py`, `test_journal_outcomes.py` and
`test_journal_summary.py` cover this README. No test uses the network:
Telegram is faked.
