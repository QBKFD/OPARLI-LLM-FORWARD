#!/usr/bin/env python3
"""
Deterministic level scanner + decision log.

A MEASUREMENT INSTRUMENT for a human's discretionary judgment. There is no
agent, no LLM, no filtering intelligence anywhere in this loop. It watches price
arrive at pre-defined levels and records that arrival; the human decides.

An alert means "price has ARRIVED at a level, start watching" — NOT "trade now".
Alerts stay open and are referenced by id; a decision may come 40+ minutes later.

Level engine: reused verbatim from notebooks/backtest.ipynb (calibrated on
2023-2025, do not retune). Each level is a row (type, value, active_from,
active_to); active_from is the moment the source period CLOSES, so a level
cannot be touched before it exists — no look-ahead by construction. Building
levels from the full history is therefore safe (proven by the corrupt-future
unit test in --selftest), which keeps the replay and live paths identical.

Replay:   python backend/deterministic_scanner.py --date 2025-06-16
Self-test: python backend/deterministic_scanner.py --selftest
Live:     python backend/deterministic_scanner.py --live | --dry-live

Instrument: INSTRUMENT (datafeed.instrument; default XAUUSD). It sets the live
stream, the bars' symbol and the trading calendar (_market_open). Replay and the
selftest are XAUUSD only (the parquet); the ibkr bar source is XAUUSD only.
TELEGRAM_PREFIX (optional, e.g. [DRYRUN]) starts every Telegram message.

Live bar source: SCANNER_BAR_SOURCE=ibkr (default) | stream.
  ibkr    this process's own IBKR connection (run_live). Unchanged; it is the
          rollback.
  stream  the datafeed stream bars.xauusd.1m via datafeed.bus, consumer group
          "scanner" (run_live_stream). Needs REDIS_URL and DATAFEED_DB. Never
          connects to IBKR: the datafeed feed is the only gateway client.
"""
from __future__ import annotations

import argparse
import os
import sqlite3
from collections import defaultdict
from pathlib import Path

import pandas as pd

# ── Calibration (2023-2025, do not retune) ─────────────────────────────────
PARQUET = Path(__file__).resolve().parent.parent / "database/ohlcv_data/XAUUSD_1min_UTC_clean.parquet"
# SCANNER_DB lets the container point the append-only journal at a mounted
# volume so decisions survive restarts / redeploys.
DB_PATH = Path(os.environ.get(
    "SCANNER_DB", Path(__file__).resolve().parent.parent / "database/scanner_decisions.sqlite"))
SESSIONS = {"ASIA": (0, 7), "LONDON": (7, 12), "NY": (12, 21)}  # UTC hours [start,end)
MIN_BARS_FULL_DAY = 300          # a full trading day needs >= this many bars
SESSION_CARRY_H = 24             # session H/L stay active +24h after close
TOL = 1.00                       # touch tolerance ($)
WINDOW_MIN = 15                  # collapse alerts within the same 15-min bucket
CF_HORIZONS = {"1h": 60, "4h": 240}   # counterfactual MFE/MAE horizons (minutes)
MISSED_AFTER_H = 4               # auto-assign MISSED to alerts unanswered this long
RECONNECT_BACKOFF_S = 20         # wait before exiting on a dropped gateway, so the
                                 # container doesn't hammer-restart while the gateway
                                 # is down for minutes (weekly 2FA window)
SILENCE_WARN_S = 1800            # connected but no bars this long (market hours) -> Telegram warn


# ── Level engine (from notebooks/backtest.ipynb, active_from construction) ──
def session_of(ts) -> str:
    for s, (a, b) in SESSIONS.items():
        if a <= ts.hour < b:
            return s
    return "ROLLOVER"


def build_levels(df: pd.DataFrame) -> list[dict]:
    """Precompute daily/weekly/session aggregates once, emit level instances.

    Each instance carries active_from (source-period close) and active_to, so it
    is invisible outside that window — the no-look-ahead property. Types map to
    the 12 the spec asks for: PDH PDL PWH PWL DOPEN WOPEN {ASIA,LONDON,NY}_{H,L}.
    """
    date = df.index.normalize()
    daily = df.groupby(date).agg(high=("high", "max"), low=("low", "min"),
                                 open=("open", "first"), n=("open", "size"))
    full_days = daily.index[daily["n"] >= MIN_BARS_FULL_DAY]
    hour = df.index.hour
    sess_hl = {
        s: df[(hour >= h0) & (hour < h1)]
           .groupby(df[(hour >= h0) & (hour < h1)].index.normalize())
           .agg(h=("high", "max"), l=("low", "min"))
        for s, (h0, h1) in SESSIONS.items()
    }
    ic = df.index.isocalendar()
    wkkey = pd.Series(ic["year"].astype(str) + "-" + ic["week"].astype(str).str.zfill(2), index=df.index)
    wk = df.groupby(wkkey).agg(high=("high", "max"), low=("low", "min"), open=("open", "first"))

    def wkkey_of(day):
        c = (day - pd.Timedelta(days=day.weekday())).isocalendar()
        return f"{c[0]}-{c[1]:02d}"

    levels: list[dict] = []
    for day in daily.index:
        de = day + pd.Timedelta(days=1)
        today = []
        i = full_days.searchsorted(day)                      # prior FULL trading day
        if i > 0:
            p = full_days[i - 1]
            today += [dict(type="PDH", value=daily.at[p, "high"], active_from=day, active_to=de, arm="static"),
                      dict(type="PDL", value=daily.at[p, "low"],  active_from=day, active_to=de, arm="static")]
        pwk = wkkey_of(day - pd.Timedelta(days=7))
        if pwk in wk.index:
            today += [dict(type="PWH", value=wk.at[pwk, "high"], active_from=day, active_to=de, arm="static"),
                      dict(type="PWL", value=wk.at[pwk, "low"],  active_from=day, active_to=de, arm="static")]
        today.append(dict(type="DOPEN", value=daily.at[day, "open"], active_from=day, active_to=de, arm="open"))
        cwk = wkkey_of(day)
        if cwk in wk.index:
            today.append(dict(type="WOPEN", value=wk.at[cwk, "open"], active_from=day, active_to=de, arm="open"))
        for s, (h0, h1) in SESSIONS.items():
            g = sess_hl[s]
            if day in g.index:
                cl = day + pd.Timedelta(hours=h1)            # active_from = session CLOSE
                ex = cl + pd.Timedelta(hours=SESSION_CARRY_H)
                today += [dict(type=f"{s}_H", value=g.at[day, "h"], active_from=cl, active_to=ex, arm="static"),
                          dict(type=f"{s}_L", value=g.at[day, "l"], active_from=cl, active_to=ex, arm="static")]
        for L in today:
            L["created_day"] = day
        levels += today
    for i, L in enumerate(levels):
        L["id"] = i
    return levels


# ── Streaming scanner (identical code for replay and live) ─────────────────
class Scanner:
    """Feeds one bar at a time. First-touch-only per level instance: once a
    level is touched it is dead forever (no cooldown re-arm — that floods to
    ~15/day; first-touch-only holds ~4.5/day). Raw touches landing in the same
    15-min bucket and at the same price (within TOL) collapse into one alert
    carrying a confluence_count and every contributing level type."""

    def __init__(self, levels: list[dict], tol: float = TOL, window_min: int = WINDOW_MIN):
        self.tol = tol
        self.window = pd.Timedelta(minutes=window_min)
        self.by_day = defaultdict(list)
        for L in levels:
            d, d1 = L["active_from"].normalize(), L["active_to"].normalize()
            while d <= d1:
                self.by_day[d].append(L)
                d += pd.Timedelta(days=1)
        # per-level runtime state: 'open' levels start UNARMED (price sits at the
        # open at creation); static levels are armed from birth.
        self.state = {L["id"]: {"armed": L["arm"] != "open", "alerted": False} for L in levels}
        self._cur_day = None
        self._active = []
        self._bucket_start = None
        self._bucket_touches: list[dict] = []
        # per-day counter, so an alert's id depends only on that day's alerts, not
        # on how far back the history (and a live warm-up) starts
        self._seq = defaultdict(int)

    def on_bar(self, ts, o, h, l, c) -> list[dict]:
        finalized = []
        # flush the previous 15-min bucket once we cross into a new one
        if self._bucket_start is not None and ts - self._bucket_start >= self.window:
            finalized += self._flush()
        if self._bucket_start is None or ts - self._bucket_start >= self.window:
            self._bucket_start = ts.floor(f"{int(self.window.total_seconds() // 60)}min")

        d = ts.normalize()
        if d != self._cur_day:
            self._cur_day = d
            self._active = self.by_day.get(d, [])

        for L in self._active:
            if not (L["active_from"] <= ts < L["active_to"]):
                continue
            s = self.state[L["id"]]
            if s["alerted"]:
                continue
            v = L["value"]
            in_zone = (l - self.tol) <= v <= (h + self.tol)
            left = (h < v - 2 * self.tol) or (l > v + 2 * self.tol)
            if not s["armed"]:
                if left:
                    s["armed"] = True
                continue
            if in_zone:
                self._bucket_touches.append(dict(ts=ts, type=L["type"], value=v, price=c))
                s["alerted"] = True     # first-touch-only: dead forever
        return finalized

    def close(self) -> list[dict]:
        return self._flush()

    def _flush(self) -> list[dict]:
        touches, self._bucket_touches = self._bucket_touches, []
        if not touches:
            return []
        touches.sort(key=lambda t: t["value"])
        clusters, cur = [], [touches[0]]
        for t in touches[1:]:
            if abs(t["value"] - cur[-1]["value"]) <= self.tol:
                cur.append(t)
            else:
                clusters.append(cur); cur = [t]
        clusters.append(cur)
        alerts = []
        for cl in clusters:
            ts0 = min(t["ts"] for t in cl)
            day = ts0.strftime("%Y%m%d")
            self._seq[day] += 1
            alerts.append(dict(
                alert_id=f"A{day}-{self._seq[day]:03d}",
                alert_ts_utc=ts0.strftime("%Y-%m-%d %H:%M:%S"),
                level_types=",".join(sorted({t["type"] for t in cl})),
                level_price=round(sum(t["value"] for t in cl) / len(cl), 3),
                price_at_alert=round(cl[0]["price"], 3),
                session=session_of(ts0),
                confluence_count=len({t["type"] for t in cl}),
            ))
        return alerts


class LiveLevels:
    """Keeps a live Scanner equal to a continuous replay over the same bars.

    build_levels() only knows a level once its source period has bars, so a level
    list built at one moment lacks — or holds partial values for — the session H/L
    that close later. So rebuild at every point where new levels become active
    (UTC midnight and each session close), then warm the fresh Scanner on ALL
    history before the current bar WITHOUT emitting. That skips alerts for touches
    that happened before we came online, and keeps first-touch state for levels
    carried over from yesterday, exactly as a continuous replay would hold it."""

    def __init__(self, hist: pd.DataFrame, now):
        self.hist = hist
        self.filled: set = set()        # minutes added from history bars (fill)
        self.history_touches: list = []  # rebuild-derived alerts dated on filled minutes
        # what the first warm-up pass derived but didn't emit: AlertSink.startup
        # journals the part of it that fell into a downtime
        self.warmup_alerts = self._rebuild(now)
        self._epoch_from_bars(now)

    def _epoch_from_bars(self, now):
        # Compare against the epoch of the newest bar the levels were built from,
        # not of `now`. Started after an epoch boundary but before that epoch's
        # first bar (e.g. 00:00:30, before the 00:00 bar), the first bar is in
        # now's epoch and wouldn't rebuild, so the day's levels (DOPEN, WOPEN,
        # PDH, PDL, PWH, PWL; build_levels creates them only for days with bars)
        # would be missing until the next session close. On 2025-06-16 that lost
        # the 00:06, 00:14 and 00:24 alerts.
        built = self.hist.index[self.hist.index < now]
        self.epoch = self._epoch(built.max()) if len(built) else None

    @staticmethod
    def _epoch(ts):
        # changes exactly when new levels can activate: midnight, session closes
        return ts.normalize(), sum(ts.hour >= h1 for _, h1 in SESSIONS.values())

    def _rebuild(self, now) -> list[dict]:
        """Fresh levels and Scanner, warmed on every bar before now. Returns the
        alerts that warm-up pass produced; they are never emitted."""
        self.epoch = self._epoch(now)
        self.scanner = Scanner(build_levels(self.hist))
        warm = []
        for ts, o, h, l, c in self.hist[self.hist.index < now].itertuples(name=None):
            warm += self.scanner.on_bar(ts, o, h, l, c)
        if self.filled:     # touches only history bars made: the caller journals them
            self.history_touches += [a for a in warm if _utc(a["alert_ts_utc"]) in self.filled]
        return warm

    def on_bar(self, ts, o, h, l, c) -> list[dict]:
        self.hist.loc[ts] = [o, h, l, c]
        if len(self.hist) > 1 and self.hist.index[-2] > ts:     # after a newer history fill
            self.hist.sort_index(inplace=True)
        if self._epoch(ts) != self.epoch:
            self._rebuild(ts)
        return self.scanner.on_bar(ts, o, h, l, c)

    def fill(self, ts, o, h, l, c) -> bool:
        """A history bar for a minute that has no bar: it joins the bars levels
        and warm-up passes are built from, and is never fed to the Scanner, so
        it can never trigger an alert. Returns False if the minute has a bar."""
        if ts in self.hist.index:
            return False
        self.hist.loc[ts] = [o, h, l, c]
        self.hist.sort_index(inplace=True)
        self.filled.add(ts)
        return True

    def refresh(self, now) -> None:
        """Rebuild levels and first-touch state from all bars before now (fills
        included) after fills; the warm-up pass's alerts are dropped."""
        self._rebuild(now)
        self._epoch_from_bars(now)


class AlertSink:
    """Where a live path's alerts go (both bar sources).

    An alert is delivered (send(): Telegram, or the terminal in --dry-live) and
    journaled as status 'sent' only if the scanner received its touch live.
    Touches that happened while no scanner was running are never sent late. They
    are journaled as 'missed_while_down', in this process's epoch, so coverage
    gaps are explicit:
      startup()   alerts the first warm-up pass derived from the 15-min bucket
                  that was still open when the previous process stopped, or
                  later (alert_ts >= floor(down_from)); that process can't have
                  sent them. Without a down_from (first start on this journal)
                  nothing is recorded.
      __call__()  alerts flushed live whose earliest touch is on a warm-up bar
                  (alert_ts <= warm_end): a touch made while down, in a bucket
                  that closes after the restart.
    An alert already in the journal (same id, time and types) is not repeated.
    missed_history() journals a touch made only on history bars (stream mode,
    LiveLevels.fill) as 'missed_history': never sent either.
    progress() records the newest live bar, which is the next start's down_from."""

    def __init__(self, con, epoch: int, bar_source: str, send, *, down_from=None,
                 warm_end=None, log=print):
        self.con, self.epoch, self.bar_source, self.send, self.log = con, epoch, bar_source, send, log
        self.down_from = None if down_from is None else _utc(down_from)
        self.warm_end = None if warm_end is None else _utc(warm_end)
        self.stats = dict(sent=0, missed_while_down=0, missed_history=0, duplicate=0)

    def _known(self, a, any_epoch: bool) -> bool:
        sql = "SELECT 1 FROM alerts WHERE alert_id=? AND alert_ts_utc=? AND level_types=?"
        args = [a["alert_id"], a["alert_ts_utc"], a["level_types"]]
        if not any_epoch:
            sql += " AND epoch=?"
            args.append(self.epoch)
        return self.con.execute(sql, args).fetchone() is not None

    def _free_id(self, a) -> dict:
        """Ids are numbered per day by the Scanner. A rebuild over history fills
        can renumber a day, so an id may already be taken in this epoch by
        another alert: then take the day's next free number. Never reuse an id."""
        if not self.con.execute("SELECT 1 FROM alerts WHERE alert_id=? AND epoch=?",
                                (a["alert_id"], self.epoch)).fetchone():
            return a
        prefix = a["alert_id"].rsplit("-", 1)[0]
        used = [int(r[0].rsplit("-", 1)[1]) for r in self.con.execute(
            "SELECT alert_id FROM alerts WHERE alert_id LIKE ? AND epoch=?",
            (prefix + "-%", self.epoch))]
        new = dict(a, alert_id=f"{prefix}-{max(used) + 1:03d}")
        self.log(f"alert id {a['alert_id']} already used in epoch {self.epoch}; "
                 f"this alert is {new['alert_id']}", flush=True)
        return new

    def _missed(self, a) -> str:
        if self._known(a, any_epoch=True):
            self.stats["duplicate"] += 1
            return "duplicate"
        a = self._free_id(a)
        insert_alert(self.con, a, epoch=self.epoch, bar_source=self.bar_source,
                     status="missed_while_down")
        self.con.commit()
        self.stats["missed_while_down"] += 1
        self.log(f"missed while down (journaled, not sent): {a['alert_id']} {a['alert_ts_utc']} "
                 f"{a['level_types']} @ {a['level_price']}", flush=True)
        return "missed_while_down"

    def missed_history(self, a) -> str:
        """A level touched only on history bars. Every rebuild over the filled bars
        derives the touch again, possibly under another id, so it is known by
        its touch (time, types, price) within this epoch, not by its id."""
        if self.con.execute("SELECT 1 FROM alerts WHERE alert_ts_utc=? AND level_types=? AND "
                            "level_price=? AND epoch=?", (a["alert_ts_utc"], a["level_types"],
                                                          a["level_price"], self.epoch)).fetchone():
            self.stats["duplicate"] += 1
            return "duplicate"
        a = self._free_id(a)
        insert_alert(self.con, a, epoch=self.epoch, bar_source=self.bar_source,
                     status="missed_history")
        self.con.commit()
        self.stats["missed_history"] += 1
        self.log(f"touch on history bars only (journaled as missed_history, not sent): "
                 f"{a['alert_id']} {a['alert_ts_utc']} {a['level_types']} @ {a['level_price']}",
                 flush=True)
        return "missed_history"

    def startup(self, warmup_alerts) -> int:
        if self.down_from is None:
            return 0
        bound = self.down_from.floor(f"{WINDOW_MIN}min")
        n = sum(self._missed(a) == "missed_while_down"
                for a in warmup_alerts if _utc(a["alert_ts_utc"]) >= bound)
        if n:
            self.log(f"{n} touch(es) while down since {self.down_from}: journaled as "
                     f"missed_while_down", flush=True)
        return n

    def __call__(self, a) -> str:
        if self.warm_end is not None and _utc(a["alert_ts_utc"]) <= self.warm_end:
            return self._missed(a)
        if self._known(a, any_epoch=False):
            self.stats["duplicate"] += 1
            self.log(f"alert {a['alert_id']} already journaled (redelivered bar); not sent again",
                     flush=True)
            return "duplicate"
        a = self._free_id(a)
        insert_alert(self.con, a, epoch=self.epoch, bar_source=self.bar_source)
        self.con.commit()
        self.send(a)
        self.stats["sent"] += 1
        return "sent"

    def progress(self, ts) -> None:
        record_progress(self.con, ts)


def _alert_sender(tg, dry: bool):
    """Deliver one alert: the terminal in --dry-live, else Telegram. A send
    failure must not stop the feed; the alert is already journaled."""
    def send(a):
        if dry:
            print("ALERT  " + format_alert(a).replace("\n", " | "), flush=True)
            return
        try:
            tg.send(format_alert(a))
        except Exception as e:
            print(f"telegram send failed for {a['alert_id']}: {e}")
    return send


# ── Counterfactual: forward MFE/MAE at 1h/4h (direction-agnostic) ──────────
def counterfactual(df: pd.DataFrame, ts_str: str, price: float) -> dict:
    ts = pd.Timestamp(ts_str, tz="UTC")
    out = {"alert_id": None}
    for name, mins in CF_HORIZONS.items():
        w = df.loc[ts: ts + pd.Timedelta(minutes=mins)]
        hi, lo = (w["high"].max(), w["low"].min()) if len(w) else (price, price)
        out[f"fwd_high_{name}"] = round(hi, 3)
        out[f"fwd_low_{name}"] = round(lo, 3)
        out[f"mfe_up_{name}"] = round(hi - price, 2)     # what a long would have gained
        out[f"mfe_dn_{name}"] = round(price - lo, 2)     # what a short would have gained
    return out


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
JOURNAL_VERSION = 3              # PRAGMA user_version: 1 epoch columns, 2 source_switches,
                                 # 3 alert status + scanner_state
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


def _utc(ts) -> pd.Timestamp:
    """Parse a stored/naive timestamp as UTC (stored alert timestamps are naive)."""
    t = pd.Timestamp(ts)
    return t.tz_localize("UTC") if t.tz is None else t.tz_convert("UTC")


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
                    epoch: int | None = None):
    """Append a decision. reasoning_ts == decision_ts, which is the proof that the
    reasoning was recorded at decision time, not reconstructed with hindsight.
    epoch: the decided alert's epoch; None looks it up from (alert_id, alert_ts)."""
    if epoch is None:
        epoch = _alert_epoch(con, alert_id, alert_ts)
    mins = (_utc(decision_ts) - _utc(alert_ts)).total_seconds() / 60
    con.execute("INSERT INTO decisions (alert_id, decision, decision_ts_utc, minutes_from_alert, "
                "direction, entry, stop, target, reasoning, reasoning_ts_utc, source, epoch) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (alert_id, decision, str(decision_ts), round(mins, 2), direction,
                 entry, stop, target, reasoning, str(decision_ts), source, epoch))
    con.commit()


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


def alerts_needing_counterfactual(con, now_utc):
    """Alerts old enough that a full 4h forward window exists but with no
    counterfactual row yet — the SKIP/MISSED 'what would have happened' data
    can't be computed at alert time, only once the future bars arrive.
    Rows are (alert_id, alert_ts, price_at_alert, epoch)."""
    now = _utc(now_utc)
    rows = con.execute(
        "SELECT a.alert_id, a.alert_ts_utc, a.price_at_alert, a.epoch FROM alerts a "
        "LEFT JOIN counterfactuals cf ON a.alert_id = cf.alert_id AND a.epoch = cf.epoch "
        "WHERE cf.alert_id IS NULL"
    ).fetchall()
    horizon = max(CF_HORIZONS.values())
    return [r for r in rows if (now - _utc(r[1])).total_seconds() >= horizon * 60]


def sweep_counterfactuals(con, df, now_utc) -> int:
    """Fill forward MFE/MAE for alerts whose 4h window has elapsed, from the
    accumulated live bar frame. Runs live so the experiment data is captured."""
    n = 0
    for alert_id, alert_ts, price, epoch in alerts_needing_counterfactual(con, now_utc):
        cf = counterfactual(df, alert_ts, price)
        insert_counterfactual(con, alert_id, cf, epoch=epoch)
        n += 1
    if n:
        con.commit()
    return n


# ── Telegram (stdlib only — message carries facts, never a recommendation) ──
def format_alert(a: dict) -> str:
    """EXACTLY the fields the spec allows: id, ts UTC, level type(s), level
    price, current price, session, confluence. No bias, no commentary."""
    return (f"{a['alert_id']}\n"
            f"{a['alert_ts_utc']} UTC\n"
            f"{a['session']}\n"
            f"{a['level_types']} @ {a['level_price']}\n"
            f"price {a['price_at_alert']}\n"
            f"confluence {a['confluence_count']}")


def parse_reply(text: str) -> dict | None:
    """Parse a human reply. Deterministic, no interpretation.
       '<id> TAKE long 4128 sl 4122 tp 4140 <reason>'  or  '<id> SKIP <reason>'."""
    tok = text.strip().split()
    if len(tok) < 2:
        return None
    alert_id, verb = tok[0], tok[1].upper()
    if verb == "SKIP":
        return dict(alert_id=alert_id, decision="SKIP", reasoning=" ".join(tok[2:]))
    if verb == "TAKE":
        d = dict(alert_id=alert_id, decision="TAKE", direction=None,
                 entry=None, stop=None, target=None, reasoning="")
        rest = tok[2:]
        if rest and rest[0].lower() in ("long", "short"):
            d["direction"] = rest[0].lower(); rest = rest[1:]
        if rest and _isnum(rest[0]):
            d["entry"] = float(rest[0]); rest = rest[1:]
        i = 0
        while i < len(rest):
            w = rest[i].lower()
            if w == "sl" and i + 1 < len(rest) and _isnum(rest[i + 1]):
                d["stop"] = float(rest[i + 1]); i += 2; continue
            if w == "tp" and i + 1 < len(rest) and _isnum(rest[i + 1]):
                d["target"] = float(rest[i + 1]); i += 2; continue
            break
        d["reasoning"] = " ".join(rest[i:])
        return d
    return None


def _isnum(s: str) -> bool:
    try:
        float(s); return True
    except ValueError:
        return False


class Telegram:
    """Thin sendMessage / getUpdates wrapper. Token + chat id from env.
    TELEGRAM_PREFIX (optional): put in front of every message, with a space,
    e.g. [DRYRUN] for a dry-run stack sharing the chat."""
    def __init__(self):
        import os
        self.token = os.environ.get("TELEGRAM_BOT_TOKEN")
        self.chat_id = os.environ.get("TELEGRAM_CHAT_ID")
        self.prefix = os.environ.get("TELEGRAM_PREFIX", "").strip()
        if not self.token or not self.chat_id:
            raise RuntimeError("Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in the environment")
        self._base = f"https://api.telegram.org/bot{self.token}"

    def _get(self, method, params):
        import json
        import urllib.parse
        import urllib.request
        url = f"{self._base}/{method}?{urllib.parse.urlencode(params)}"
        with urllib.request.urlopen(url, timeout=70) as r:
            return json.load(r)

    def send(self, text: str):
        if self.prefix:
            text = f"{self.prefix} {text}"
        return self._get("sendMessage", {"chat_id": self.chat_id, "text": text})

    def poll(self, offset: int):
        """Long-poll replies. Returns (messages, next_offset)."""
        res = self._get("getUpdates", {"offset": offset, "timeout": 60})
        msgs, nxt = [], offset
        for u in res.get("result", []):
            nxt = u["update_id"] + 1
            m = u.get("message") or {}
            if str(m.get("chat", {}).get("id")) == str(self.chat_id) and "text" in m:
                msgs.append(m["text"])
        return msgs, nxt


# ── Replay ──────────────────────────────────────────────────────────────────
def load_data() -> pd.DataFrame:
    df = pd.read_parquet(PARQUET).set_index("timestamp")[["open", "high", "low", "close"]].astype(float).sort_index()
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    return df


def replay_day(date: str, con=None) -> pd.DataFrame:
    df = load_data()
    levels = build_levels(df)
    day = pd.Timestamp(date, tz="UTC")
    day_bars = df.loc[date:date]
    if day_bars.empty:
        print(f"No data for {date} (range {df.index[0].date()}..{df.index[-1].date()})")
        return pd.DataFrame()

    scanner = Scanner(levels)
    alerts = []
    for ts, o, h, l, c in day_bars.itertuples(name=None):
        alerts += scanner.on_bar(ts, o, h, l, c)
    alerts += scanner.close()

    rows = []
    epoch = start_epoch(con, "parquet") if con is not None else None
    for a in alerts:
        cf = counterfactual(df, a["alert_ts_utc"], a["price_at_alert"])
        if con is not None:
            insert_alert(con, a, epoch=epoch, bar_source="parquet")
            insert_counterfactual(con, a["alert_id"], cf, epoch=epoch)
        rows.append({**a, **{k: v for k, v in cf.items() if k != "alert_id"}})
    if con is not None:
        con.commit()
    return pd.DataFrame(rows)


# ── Live loop (reuses tws_connector for the feed, Postgres for level history) ─
def _load_recent_history(symbol: str, days: int, host: str, port: int, client_id: int) -> pd.DataFrame:
    """Recent 1-min bars straight from IBKR (MIDPOINT, same pricing the live feed
    builds from) so the level engine has yesterday's / last week's / today's
    session data to build from. Deliberately NOT the Postgres ohlcv_1min view —
    that stops at the last backfill and would leave the scanner with no levels."""
    from ib_insync import IB, Contract
    ib = IB()
    ib.connect(host, port, clientId=client_id, timeout=10, readonly=True)
    try:
        c = ib.qualifyContracts(Contract(symbol=symbol, secType="CMDTY", exchange="SMART", currency="USD"))[0]
        bars = ib.reqHistoricalData(c, endDateTime="", durationStr=f"{days} D",
                                    barSizeSetting="1 min", whatToShow="MIDPOINT",
                                    useRTH=False, formatDate=2)
    finally:
        ib.disconnect()
    df = pd.DataFrame([(b.date, b.open, b.high, b.low, b.close) for b in bars],
                      columns=["timestamp", "open", "high", "low", "close"]).set_index("timestamp")
    df.index = pd.DatetimeIndex(df.index)
    df.index = df.index.tz_localize("UTC") if df.index.tz is None else df.index.tz_convert("UTC")
    return df[["open", "high", "low", "close"]].astype(float).sort_index()


# Trading calendar of the live instrument (datafeed.instrument.CALENDARS):
# "xauusd" (the default, below) or "24/7". Set once by configure_instrument()
# when a live path starts; replay and the selftest never change it.
CALENDAR = "xauusd"


def configure_instrument(env=os.environ):
    """The live instrument (INSTRUMENT, INSTRUMENT_*; default XAUUSD). Sets
    CALENDAR. An invalid setting is a startup error."""
    global CALENDAR
    from datafeed.instrument import InstrumentError, from_env
    try:
        inst = from_env(env)
    except InstrumentError as e:
        raise SystemExit(f"instrument: {e}") from None
    CALENDAR = inst.calendar
    return inst


def _market_open(now) -> bool:
    """Approximate XAUUSD (spot gold) trading window, used only to suppress false
    'no data' heartbeat warnings. Sun 18:00 -> Fri 17:00 New York time, minus the
    daily 17:00-18:00 break. Evaluated in New York time because the break moves in
    UTC with US DST (21-22 UTC in summer, 22-23 UTC in winter). Holidays aren't
    modelled — a rare spurious warning is acceptable for a safety-net notification.
    With CALENDAR "24/7" (crypto) it is always open."""
    if CALENDAR == "24/7":
        return True
    et = now.tz_convert("America/New_York")
    wd, h = et.weekday(), et.hour       # Mon=0 .. Sun=6
    if wd == 5:                          # Saturday
        return False
    if wd == 6:                          # Sunday: reopens 18:00 ET
        return h >= 18
    if wd == 4 and h >= 17:              # Friday close
        return False
    if h == 17:                          # daily maintenance break
        return False
    return True


def beat(path) -> None:
    """Heartbeat: write the current UTC time to `path` (SCANNER_HEARTBEAT), via a
    temp file and os.replace so a reader never sees a partial write. The compose
    healthcheck and watchdog.sh treat a stale file as a hung main loop. No path:
    no file (the default). A write error is logged, never raised."""
    if not path:
        return
    try:
        tmp = f"{path}.tmp"
        with open(tmp, "w") as f:
            f.write(pd.Timestamp.now(tz="UTC").isoformat())
        os.replace(tmp, path)
    except OSError as e:
        print(f"heartbeat write to {path} failed ({e!r})", flush=True)


def _send_quietly(tg, text: str):
    """Confirmation messages are best-effort: the decision is already logged."""
    try:
        tg.send(text)
    except Exception as e:
        print(f"telegram send failed ({e!r})", flush=True)


def _valid_bar(ts, o, h, l, c) -> bool:
    """Only real prices inside trading hours may reach the level engine. IBKR sends
    -1 for "no quote" (e.g. while a data farm reconnects) and pushes frozen quotes
    while the market is closed; either becomes a fake touch or a fake session.
    The backtest data has neither: all its bars fall inside _market_open() and
    every price is positive."""
    return _market_open(ts) and all(x == x and x > 0 for x in (o, h, l, c)) and l <= h


def run_live(symbol: str = "XAUUSD", dry: bool = False, instrument=None):
    """Live path: identical scanner code, fed by real IBKR bars. XAUUSD only:
    services.tws_connector subscribes CMDTY bars, nothing else (another
    instrument runs in stream mode, where the datafeed holds the contract).

    dry=False: alerts go to Telegram, TAKE/SKIP replies are logged, MISSED swept.
    dry=True : alerts print to the terminal, no Telegram at all — for watching the
               live feed + scanner without spending messages or needing creds."""
    import os
    import threading
    import time as _time
    if instrument is not None and instrument.name != "XAUUSD":
        raise SystemExit(f"the ibkr bar source is XAUUSD only, not {instrument.name}: "
                         f"run it with SCANNER_BAR_SOURCE=stream")
    from services.tws_connector import TWSConnector

    host = os.environ.get("IB_HOST", "127.0.0.1")
    port = int(os.environ.get("IB_PORT", "7497"))
    client_id = int(os.environ.get("IB_CLIENT_ID", "11"))

    tg = None if dry else Telegram()
    con = open_log(check_same_thread=False)
    epoch = start_epoch(con, "ibkr")
    lock = threading.Lock()

    # history first (separate short-lived connection, distinct clientId). A failure
    # here takes the same crash-only path as a failed tws.connect(): back off, exit,
    # let Docker restart us clean.
    try:
        hist = _load_recent_history(symbol, days=10, host=host, port=port, client_id=client_id + 10)
    except Exception as e:
        print(f"history load failed ({e!r}); backing off then exiting for restart", flush=True)
        _time.sleep(RECONNECT_BACKOFF_S)
        raise SystemExit(1)
    hist = hist[[_valid_bar(*r) for r in hist.itertuples(name=None)]]
    down_from = last_live_bar(con)            # before this process records any progress
    live = LiveLevels(hist, pd.Timestamp.now(tz="UTC"))
    sink = AlertSink(con, epoch, "ibkr", _alert_sender(tg, dry), down_from=down_from,
                     warm_end=hist.index.max() if len(hist) else None)
    sink.startup(live.warmup_alerts)

    hb = {"last": _time.time(), "warned": False}   # last final bar (wall clock)

    def on_bar(_sym, bar):
        if not bar.get("is_final"):
            return
        ts = pd.Timestamp(bar["timestamp"]); ts = ts.tz_convert("UTC") if ts.tz else ts.tz_localize("UTC")
        o, h, l, c = bar["open"], bar["high"], bar["low"], bar["close"]
        if not _valid_bar(ts, o, h, l, c):
            return
        hb["last"] = _time.time()
        with lock:
            for a in live.on_bar(ts, o, h, l, c):
                sink(a)
            sink.progress(ts)

    tws = TWSConnector(host=host, port=port, client_id=client_id, readonly=True)
    # Crash-only design: surface disconnects instead of relying on the connector's
    # in-place reconnect, whose market-data resubscribe is a stub (a silent
    # reconnect would leave us connected with NO bars). On any drop we back off
    # and exit; Docker's restart policy + the entrypoint port-wait do a clean full
    # reconnect + resubscribe via run_live's cold start. One code path to get
    # right (cold start), not two.
    tws.max_retries = 0
    tws.on_bar_update(on_bar)
    if not tws.connect():
        print("could not connect to gateway; backing off then exiting for restart", flush=True)
        _time.sleep(RECONNECT_BACKOFF_S)
        raise SystemExit(1)
    tws.subscribe_bars(symbol, exchange="SMART", sec_type="CMDTY", currency="USD")
    active = sum(1 for L in build_levels(live.hist)
                 if L["active_from"] <= pd.Timestamp.now(tz="UTC") < L["active_to"])
    mode = "DRY (terminal)" if dry else "LIVE (Telegram)"
    print(f"{mode}: scanning {symbol}, {active} levels active, journal epoch {epoch}. "
          f"Ctrl-C to stop.", flush=True)

    offset = 0
    last_sweep = 0.0
    heartbeat = os.environ.get("SCANNER_HEARTBEAT")
    try:
        while True:
            beat(heartbeat)
            # Crash-only: a dropped gateway (daily restart / weekly 2FA) surfaces
            # as not-connected. Back off, then exit so Docker restarts us into a
            # clean full reconnect. The backoff stops a fast restart-loop while the
            # gateway is down for minutes.
            if not tws.connected:
                print("gateway connection lost — backing off then exiting so Docker reconnects clean", flush=True)
                try: tws.disconnect()
                except Exception: pass
                _time.sleep(RECONNECT_BACKOFF_S)
                raise SystemExit(1)

            # Heartbeat: connected but no bars for a while during market hours is a
            # silent stall the self-heal won't catch -> warn once via Telegram.
            now = pd.Timestamp.now(tz="UTC")
            if not _market_open(now):
                # silence only counts while the market is open, so the first
                # minutes after a reopen don't read as a 30-min stall
                hb["last"] = _time.time()
            if not dry and _market_open(now) and _time.time() - hb["last"] > SILENCE_WARN_S:
                if not hb["warned"]:
                    try:
                        tg.send(f"scanner: no XAUUSD bars for >{SILENCE_WARN_S // 60} min "
                                f"during market hours — check the feed")
                    except Exception:
                        pass
                    hb["warned"] = True
            else:
                hb["warned"] = False

            if dry:
                _time.sleep(2)
                continue
            try:
                msgs, offset = tg.poll(offset)
            except Exception as e:
                # a Telegram/network hiccup must not kill the feed (it used to:
                # "connection reset by peer" -> crash -> restart gap). offset is
                # unchanged, so unread replies are fetched again next round.
                print(f"telegram poll failed ({e!r}); retrying", flush=True)
                _time.sleep(5)
                msgs = []
            for text in msgs:
                _handle_reply(con, lock, tg, text, epoch)
            if _time.time() - last_sweep > 300:      # every 5 min
                with lock:
                    now = pd.Timestamp.now(tz="UTC")
                    sweep_auto_missed(con, now)
                    # counterfactuals need the future bars, so fill from the
                    # accumulated live frame once each alert's 4h window elapses
                    sweep_counterfactuals(con, live.hist.sort_index(), now)
                last_sweep = _time.time()
    except KeyboardInterrupt:
        print("\nstopping."); tws.disconnect()


# ── Stream bar source (SCANNER_BAR_SOURCE=stream) ──────────────────────────
# Bars come from the datafeed stream instead of this process's own IBKR
# connection. Level engine, journal and Telegram are the same code as run_live;
# only where the bars come from changes. run_live above is left as it was: it is
# the rollback (SCANNER_BAR_SOURCE=ibkr). datafeed is imported lazily, so the
# ibkr path doesn't need the package installed.
BAR_SOURCES = ("ibkr", "stream")
STREAM_GROUP = "scanner"         # own consumer group: its position is independent of the recorder's
                                 # (SCANNER_STREAM_GROUP overrides it, e.g. for a parallel dry run: consumers
                                 # of ONE group split the entries, separate groups each get all of them)
STREAM_BATCH = 500               # entries per consume call
STREAM_BLOCK_MS = 5000           # consume blocks this long when the stream is idle
WARMUP_DAYS = 10                 # stream warm-up window, see warmup_start
WARMUP_MIN_COVERAGE = 0.80       # store must hold >= this share of the window's market-open minutes
WARMUP_MAX_STALE_MIN = 5         # and its newest bar must end <= this many market-open minutes before now
STREAM_STALE_MIN = 10            # no new live bar for this many market-open minutes -> one Telegram alert
STORE_RETRY_S = 30               # unusable store at start: check it again this often


def bar_source(env=os.environ) -> str:
    """SCANNER_BAR_SOURCE, default ibkr. Anything else is a startup error, never a guess."""
    s = env.get("SCANNER_BAR_SOURCE", "ibkr")
    if s not in BAR_SOURCES:
        raise SystemExit(f"SCANNER_BAR_SOURCE={s!r}: must be one of {', '.join(BAR_SOURCES)}")
    return s


def _open_minutes(start, end) -> pd.DatetimeIndex:
    """Complete market-open minutes between start and end: minute opens m with
    start <= m, m + 1 min <= end and _market_open(m). A vectorised copy of
    _market_open (New York weekday and hour); a test pins the two together."""
    a, b = _utc(start).ceil("min"), _utc(end).floor("min")
    if b <= a:                   # date_range(a, a, inclusive="left") would still return [a]
        return pd.DatetimeIndex([], tz="UTC")
    idx = pd.date_range(a, b, freq="1min", inclusive="left")
    if CALENDAR == "24/7":
        return idx
    et = idx.tz_convert("America/New_York")
    wd, h = et.weekday, et.hour
    closed = (wd == 5) | ((wd == 6) & (h < 18)) | ((wd == 4) & (h >= 17)) | (h == 17)
    return idx[~closed]


def warmup_start(now) -> pd.Timestamp:
    """WARMUP_DAYS before now, or Monday 00:00 UTC of the previous ISO week if
    that is earlier. build_levels takes PWH/PWL from the whole previous ISO week;
    from Thursday to Sunday a plain 10-day window starts inside that week and
    would build them from part of it."""
    now = _utc(now)
    prev_monday = now.normalize() - pd.Timedelta(days=now.weekday() + 7)
    return min(now - pd.Timedelta(days=WARMUP_DAYS), prev_monday)


def _only_valid(df: pd.DataFrame) -> pd.DataFrame:
    """Rows passing _valid_bar (a boolean mask, so an empty frame keeps its columns)."""
    ok = [_valid_bar(ts, o, h, l, c) for ts, o, h, l, c in
          df[["open", "high", "low", "close"]].itertuples(name=None)]
    return df[pd.Series(ok, index=df.index, dtype=bool)]


def _store_bars(now, start, db, symbol, kinds) -> pd.DataFrame:
    from datafeed.store.query import bars_before
    rows = bars_before(symbol, now.to_pydatetime(), (now - start).to_pytimedelta(), db=db, kinds=kinds)
    df = pd.DataFrame(rows, columns=["bar_ts", "kind", "open", "high", "low", "close"])
    df.index = pd.DatetimeIndex(pd.to_datetime(df.pop("bar_ts"), utc=True), name="timestamp")
    df[["open", "high", "low", "close"]] = df[["open", "high", "low", "close"]].astype(float)
    return df


class StoreNotReady(Exception):
    """The datafeed store can't warm the scanner yet (missing, too thin or stale)."""


def stream_warmup(now, *, db=None, symbol: str = "XAUUSD", kinds=("live", "history")):
    """Bars that warm LiveLevels before the stream takes over, from the datafeed
    store only: stream mode never opens an IBKR connection. Returns
    (hist, detail); hist has open/high/low/close, only _valid_bar rows.

    Reads datafeed.store.query.bars_before over [warmup_start(now), now) (one bar
    per minute: live preferred, history as fallback). Raises StoreNotReady unless
    it holds >= WARMUP_MIN_COVERAGE of that window's market-open minutes and its
    newest bar ended <= WARMUP_MAX_STALE_MIN market-open minutes ago."""
    now = _utc(now)
    start = warmup_start(now)
    try:
        df = _store_bars(now, start, db, symbol, kinds)
    except Exception as e:
        raise StoreNotReady(f"store unavailable ({e!r})") from e
    df = _only_valid(df)
    expected = len(_open_minutes(start, now))
    cov = len(df) / expected if expected else 0.0
    if df.empty:
        raise StoreNotReady(f"store has no bars since {start}")
    if cov < WARMUP_MIN_COVERAGE:
        raise StoreNotReady(f"store too thin: {len(df)} bars = {cov:.1%} of {expected} market-open "
                            f"minutes since {start} (< {WARMUP_MIN_COVERAGE:.0%})")
    stale = len(_open_minutes(df.index[-1] + pd.Timedelta(minutes=1), now))
    if stale > WARMUP_MAX_STALE_MIN:
        raise StoreNotReady(f"store stale: newest bar {df.index[-1]} ended {stale} market-open "
                            f"minutes before now (> {WARMUP_MAX_STALE_MIN})")
    kinds_n = ", ".join(f"{k} {n}" for k, n in df["kind"].value_counts().sort_index().items())
    return df[["open", "high", "low", "close"]], (
        f"store: {len(df)} bars ({kinds_n}) {df.index[0]} .. {df.index[-1]}, "
        f"{cov:.1%} of market-open minutes, newest bar {stale} market-open min old")


def wait_for_store(notify, *, db=None, symbol: str = "XAUUSD", kinds=("live", "history"),
                   clock=lambda: pd.Timestamp.now(tz="UTC"), sleep=None,
                   retry_s: float = STORE_RETRY_S, n: int = STREAM_STALE_MIN, log=print,
                   beat=None):
    """stream_warmup, retried every retry_s until the store is usable. Returns
    (hist, now, detail), where now is the moment of the successful read.

    One notify() message once the store has been unusable for `n` market-open
    minutes since the wait began (weekends and the daily break don't count),
    and one more when it becomes usable after that message. beat() runs on
    every pass (the heartbeat: waiting is alive, not hung)."""
    import time as _time
    sleep = sleep or _time.sleep
    began, warned = _utc(clock()), False
    while True:
        if beat:
            beat()
        now = _utc(clock())
        try:
            hist, detail = stream_warmup(now, db=db, symbol=symbol, kinds=kinds)
        except StoreNotReady as e:
            waited = len(_open_minutes(began, now))
            log(f"warm-up: {e}; scanner not started, retrying in {retry_s:g}s", flush=True)
            if not warned and waited >= n:
                notify(f"scanner: datafeed store unusable for {waited} market-open minutes "
                       f"({e}) - scanner not started, check the datafeed")
                warned = True
            sleep(retry_s)
            continue
        if warned:
            notify(f"scanner: datafeed store usable again, scanner starting ({detail})")
        return hist, now, detail


class StreamStaleness:
    """One message when no new live bar has arrived for `n` market-open minutes,
    one recovery message when bars resume.

    Silence is counted in complete market-open minutes (_open_minutes) since
    the newest live bar ended (bar_ts + 1 min), or since the feed started if it
    has seen none, so weekends and the daily break never count. Bars the feed
    skips (history, duplicates, other sources) don't reset it."""

    def __init__(self, notify, started, n: int = STREAM_STALE_MIN):
        self.notify, self.started, self.n = notify, _utc(started), n
        self.alerted_at_bar = None      # newest bar when the alert went out; None = not alerted
        self.alerted = False

    def _silent(self, now, last_bar_ts) -> int:
        since = self.started if last_bar_ts is None else last_bar_ts + pd.Timedelta(minutes=1)
        return len(_open_minutes(since, now))

    def check(self, now, last_bar_ts) -> None:
        if self.alerted:
            return
        silent = self._silent(now, last_bar_ts)
        if silent >= self.n:
            last = "none since start" if last_bar_ts is None else f"last bar {last_bar_ts}"
            self.notify(f"scanner: no new XAUUSD bar from the datafeed stream for {silent} "
                        f"market-open minutes ({last}) - check the datafeed")
            self.alerted, self.alerted_at_bar = True, last_bar_ts

    def bar(self, ts) -> None:
        """A new live bar reached the level engine."""
        if not self.alerted:
            return
        silent = self._silent(ts, self.alerted_at_bar)
        self.notify(f"scanner: XAUUSD bars from the datafeed stream resumed (bar {ts}) after "
                    f"{silent} market-open minutes without one")
        self.alerted, self.alerted_at_bar = False, None


class StreamFeed:
    """bars.xauusd.1m entries -> LiveLevels.on_bar, one consume batch per step().

    Each entry is classified once (the counter of the same name in stats):
      invalid_msg      malformed or not schema_version 1 / this symbol
      history_filled   history=True for a minute without a bar: added to the
                       levels' bars (LiveLevels.fill), then the levels are
                       rebuilt once per batch; never fed to the Scanner
      history_skipped  history=True for a minute that already has a bar (live
                       is preferred) or when the levels can't be filled
      wrong_source     source not in `sources` (default ibkr; a replay feed on
                       the production stream must never reach Telegram)
      invalid_bars     fails _valid_bar
      before_warmup    older than the warm-up's first bar (backlog of a new group)
      duplicate        bar_ts already seen with the same OHLC
      conflict         bar_ts already seen with other OHLC; the first one is kept
      out_of_order     older than the newest bar seen but not seen itself; dropped,
                       because the level engine only moves forward
      live_bars        newer than everything seen: drives on_bar
    Only live_bars reach the Scanner. A touch made only on history bars never
    alerts; it goes to on_history (AlertSink.missed_history: journaled as
    'missed_history'), whether a live bar flushes it (history_touch_suppressed)
    or a rebuild over the filled bars derives it (LiveLevels.history_touches). A live bar that skips market-open
    minutes is also logged as a gap (minutes without ticks produce no bar, so
    short gaps are normal in quiet markets).

    An entry is acked only after it has been handled: its bar fed to the level
    engine and any alerts it produced passed to on_alert (journaled and sent).
    An exception propagates without the ack, so on restart the same consumer
    name gets the entry back first (datafeed.bus: own pending entries first)
    and the new warm-up decides whether it is a duplicate or still new."""

    def __init__(self, bus, stream: str, consumer: str, live, on_alert, *, lock=None,
                 last_bar_ts=None, sources=("ibkr",), symbol: str = "XAUUSD",
                 count: int = STREAM_BATCH, block_ms: int | None = STREAM_BLOCK_MS, log=print,
                 staleness: StreamStaleness | None = None, clock=lambda: pd.Timestamp.now(tz="UTC"),
                 progress=None, on_history=None, group: str = STREAM_GROUP):
        import contextlib
        self.bus, self.stream, self.consumer = bus, stream, consumer
        self.live, self.on_alert = live, on_alert
        self.lock = lock or contextlib.nullcontext()
        self.sources, self.symbol = tuple(sources), symbol
        self.count, self.block_ms, self.log = count, block_ms, log
        self.staleness, self.clock, self.progress = staleness, clock, progress
        self.on_history = on_history
        self.group = group
        self.last_bar_ts = last_bar_ts          # newest bar the level engine has seen
        self.start_ts = last_bar_ts             # warm-up end: older entries are backlog
        hist = getattr(live, "hist", None)
        self.first_ts = hist.index.min() if hist is not None and len(hist) else None
        self.stats = dict.fromkeys(
            ("entries", "live_bars", "history_filled", "history_skipped", "wrong_source",
             "invalid_msg", "invalid_bars", "before_warmup", "duplicate", "conflict",
             "out_of_order", "gaps", "gap_minutes", "alerts", "history_touch_suppressed"), 0)

    def step(self) -> int:
        batch = self.bus.consume(self.stream, self.group, self.consumer,
                                 count=self.count, block_ms=self.block_ms)
        seen: dict[str, int] = {}
        filled_before = self.stats["history_filled"]
        for d in batch:
            kind = self._handle(d)
            self.bus.ack(self.stream, self.group, d.id)        # only after it was handled
            self.stats[kind] += 1
            self.stats["entries"] += 1
            seen[kind] = seen.get(kind, 0) + 1
            if kind == "live_bars" and self.staleness:
                self.staleness.bar(self.last_bar_ts)
        if self.stats["history_filled"] > filled_before and self.last_bar_ts is not None:
            with self.lock:                     # one rebuild per batch, not per fill
                self.live.refresh(self.last_bar_ts + pd.Timedelta(minutes=1))
                self._history_touches()
        if self.staleness:
            self.staleness.check(self.clock(), self.last_bar_ts)
        other = {k: n for k, n in seen.items() if k != "live_bars"}
        if other:
            self.log(f"stream: batch of {len(batch)}: "
                     + ", ".join(f"{n} {k}" for k, n in sorted(other.items())), flush=True)
        return len(batch)

    def _parse(self, msg):
        if msg.get("schema_version") != 1:
            raise ValueError(f"schema_version {msg.get('schema_version')!r}")
        if msg.get("symbol") != self.symbol:
            raise ValueError(f"symbol {msg.get('symbol')!r}")
        if not isinstance(msg.get("history"), bool):
            raise ValueError(f"history {msg.get('history')!r}")
        ts = pd.Timestamp(msg["bar_ts"])
        if ts.tz is None:
            raise ValueError(f"bar_ts {msg['bar_ts']!r} has no UTC offset")
        return ts.tz_convert("UTC"), tuple(float(msg[k]) for k in ("open", "high", "low", "close"))

    def _handle(self, d) -> str:
        msg = d.msg
        try:
            ts, ohlc = self._parse(msg)
        except (KeyError, TypeError, ValueError) as e:
            self.log(f"stream: invalid entry {d.id} skipped ({e!r}): {str(msg)[:200]}", flush=True)
            return "invalid_msg"
        if msg.get("source") not in self.sources:
            if self.stats["wrong_source"] % 1000 == 0:
                self.log(f"stream: entry {d.id} from source {msg.get('source')!r} ignored "
                         f"(accepting {', '.join(self.sources)})", flush=True)
            return "wrong_source"
        if not _valid_bar(ts, *ohlc):
            return "invalid_bars"
        if msg["history"]:
            return self._history(ts, ohlc)
        with self.lock:
            last = self.last_bar_ts
            if last is not None and ts <= last:
                return self._old(d.id, ts, ohlc)
            missing = len(_open_minutes(last + pd.Timedelta(minutes=1), ts)) if last is not None else 0
            if missing:
                self.stats["gaps"] += 1
                self.stats["gap_minutes"] += missing
                self.log(f"stream: gap of {missing} market-open minute(s) without a bar "
                         f"between {last} and {ts}", flush=True)
            alerts = self.live.on_bar(ts, *ohlc)
            self.last_bar_ts = ts
            self._history_touches()             # a natural rebuild may have derived some
            filled = getattr(self.live, "filled", ())
            for a in alerts:
                if filled and _utc(a["alert_ts_utc"]) in filled:
                    self.stats["history_touch_suppressed"] += 1
                    self._history_touch(a)
                    continue
                self.on_alert(a)
                self.stats["alerts"] += 1
            if self.progress:
                self.progress(ts)               # after its alerts are journaled
        return "live_bars"

    def _history_touch(self, a) -> None:
        if self.on_history:
            self.on_history(a)
        else:
            self.log(f"stream: {a['alert_id']} {a['alert_ts_utc']} {a['level_types']} is a touch "
                     f"on history bars only; never alerted", flush=True)

    def _history_touches(self) -> None:
        """Hand over the touches the last rebuilds derived from filled minutes."""
        touches = getattr(self.live, "history_touches", None)
        while touches:
            self._history_touch(touches.pop(0))

    def _history(self, ts, ohlc) -> str:
        """A history bar: fills a minute without a bar, inside the warm-up window."""
        if self.first_ts is not None and ts < self.first_ts:
            return "before_warmup"
        fill = getattr(self.live, "fill", None)
        if fill is None:
            return "history_skipped"
        with self.lock:
            return "history_filled" if fill(ts, *ohlc) else "history_skipped"

    def _old(self, id, ts, ohlc) -> str:
        """Classify an entry that is not newer than the newest bar seen."""
        if self.first_ts is not None and ts < self.first_ts:
            return "before_warmup"
        hist = getattr(self.live, "hist", None)
        if hist is not None and ts in hist.index:
            row = hist.loc[ts]
            stored = tuple(float(x) for x in row[["open", "high", "low", "close"]])
            kind = "duplicate" if stored == ohlc else "conflict"
        else:
            stored, kind = None, "out_of_order"
        # entries up to the warm-up end are the backlog and only go in the batch
        # summary; after it, every conflict or late bar is worth a line of its own
        if kind != "duplicate" and (self.start_ts is None or ts > self.start_ts):
            what = (f"conflicts with the bar already used {stored}; first one kept"
                    if kind == "conflict" else f"arrived after {self.last_bar_ts}; dropped")
            self.log(f"stream: {kind} entry {id} bar {ts} {ohlc} {what}", flush=True)
        return kind


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


def _handle_reply(con, lock, tg, text: str, epoch: int):
    """One Telegram reply -> decision row (both bar sources). Only sent alerts
    can be decided (a missed_while_down id was never shown). An id that exists
    in several epochs (a switch day) means the alert of this process's epoch,
    else the newest one."""
    r = parse_reply(text)
    if not r:
        return
    with lock:
        row = con.execute("SELECT alert_ts_utc, epoch FROM alerts WHERE alert_id=? "
                          "AND status = 'sent' ORDER BY epoch = ? DESC, rowid DESC LIMIT 1",
                          (r["alert_id"], epoch)).fetchone()
        if not row:
            _send_quietly(tg, f"unknown alert_id {r['alert_id']}")
            return
        record_decision(con, r["alert_id"], r["decision"], pd.Timestamp.now(tz="UTC"),
                        row[0], r.get("direction"), r.get("entry"), r.get("stop"),
                        r.get("target"), r.get("reasoning"), epoch=row[1])
        _send_quietly(tg, f"logged {r['decision']} for {r['alert_id']}")


def run_live_stream(symbol: str = "XAUUSD", dry: bool = False, env=os.environ, instrument=None):
    """Live path fed by the datafeed stream. Same journal, Telegram, replies and
    sweeps as run_live; dry=True prints alerts instead of sending them.

    Never connects to IBKR: the datafeed feed is the only gateway client. The
    warm-up comes from the datafeed store; while it is unusable the scanner
    waits (wait_for_store). Crash-only like run_live: a failing bus (Redis down)
    backs off and exits so Docker restarts a clean cold start."""
    import threading
    import time as _time
    from datafeed.bus import RedisStreamBus
    from datafeed.ingest.xauusd_bars import STREAM

    stream = STREAM                         # default XAUUSD's bars.xauusd.1m
    if instrument is not None:
        symbol, stream = instrument.name, instrument.stream
    consumer = env.get("SCANNER_STREAM_CONSUMER", "scanner-1")   # stable across restarts
    group = env.get("SCANNER_STREAM_GROUP", STREAM_GROUP)         # a dry run alongside: its own group

    tg = None if dry else Telegram()
    con = open_log(check_same_thread=False)
    epoch = start_epoch(con, "stream")
    lock = threading.Lock()

    def notify(text):
        if dry:
            print("FEED   " + text, flush=True)
        else:
            _send_quietly(tg, text)

    down_from = last_live_bar(con)            # before this process records any progress
    heartbeat = env.get("SCANNER_HEARTBEAT")
    hist, now, detail = wait_for_store(notify, db=env.get("DATAFEED_DB"), symbol=symbol,
                                       beat=lambda: beat(heartbeat))
    print(f"warm-up via {detail}", flush=True)
    live = LiveLevels(hist, now)
    warm_end = hist.index.max() if len(hist) else None
    sink = AlertSink(con, epoch, "stream", _alert_sender(tg, dry), down_from=down_from,
                     warm_end=warm_end)
    sink.startup(live.warmup_alerts)

    feed = StreamFeed(RedisStreamBus(url=env.get("REDIS_URL")), stream, consumer, live, sink,
                      lock=lock, last_bar_ts=warm_end, progress=sink.progress, symbol=symbol,
                      on_history=sink.missed_history, group=group,
                      staleness=StreamStaleness(notify, started=now))
    fatal: list[BaseException] = []

    def pump():
        try:
            while True:
                feed.step()
        except Exception as e:
            fatal.append(e)

    threading.Thread(target=pump, name="stream-feed", daemon=True).start()
    active = sum(1 for L in build_levels(live.hist)
                 if L["active_from"] <= pd.Timestamp.now(tz="UTC") < L["active_to"])
    mode = "DRY (terminal)" if dry else "LIVE (Telegram)"
    print(f"{mode}: scanning {symbol} from stream {stream} (group {group}, consumer "
          f"{consumer}), {active} levels active, journal epoch {epoch}. Ctrl-C to stop.", flush=True)

    offset = 0
    last_sweep = 0.0
    try:
        while True:
            beat(heartbeat)
            if fatal:
                print(f"stream feed failed ({fatal[0]!r}); backing off then exiting for restart",
                      flush=True)
                _time.sleep(RECONNECT_BACKOFF_S)
                raise SystemExit(1)
            if dry:
                _time.sleep(2)
                continue
            try:
                msgs, offset = tg.poll(offset)
            except Exception as e:
                print(f"telegram poll failed ({e!r}); retrying", flush=True)
                _time.sleep(5)
                msgs = []
            for text in msgs:
                _handle_reply(con, lock, tg, text, epoch)
            if _time.time() - last_sweep > 300:      # every 5 min
                with lock:
                    now = pd.Timestamp.now(tz="UTC")
                    sweep_auto_missed(con, now)
                    sweep_counterfactuals(con, live.hist.sort_index(), now)
                last_sweep = _time.time()
    except KeyboardInterrupt:
        print("\nstopping.")


# ── Self-test: no look-ahead + first-touch dedup (from the notebook) ────────
def selftest():
    df = load_data()
    fails = []
    # 1) no look-ahead: corrupt FUTURE bars, prior-derived levels must not change
    days = df.groupby(df.index.normalize()).size()
    full = days[days >= MIN_BARS_FULL_DAY].index
    d0 = [d for d in full if d >= pd.Timestamp("2023-06-15", tz="UTC")][0]
    before = {L["type"]: L["value"] for L in build_levels(df) if L["created_day"] == d0}
    df2 = df.copy(); df2.loc[df2.index >= d0, "high"] += 10000
    after = {L["type"]: L["value"] for L in build_levels(df2) if L["created_day"] == d0}
    for t in ("PDH", "PDL", "PWH", "PWL"):
        if t in before and abs(before[t] - after.get(t, -1)) > 1e-6:
            fails.append(f"look-ahead:{t}")
    # 2) first-touch-only: oscillate at a level -> exactly ONE alert (not two)
    idx = pd.date_range("2023-06-15", periods=10, freq="1min", tz="UTC")
    pr = [100, 100, 100, 100, 100, 105, 105, 100, 100, 100]
    p = pd.DataFrame({"open": pr, "high": [x + .1 for x in pr],
                      "low": [x - .1 for x in pr], "close": pr}, index=idx)
    lvl = [dict(type="X", value=100.0, active_from=idx[0], active_to=idx[-1] + pd.Timedelta(minutes=1),
                arm="static", created_day=idx[0].normalize(), id=0)]
    sc = Scanner(lvl, window_min=1)
    got = []
    for ts, o, h, l, c in p.itertuples(name=None):
        got += sc.on_bar(ts, o, h, l, c)
    got += sc.close()
    if len(got) != 1:
        fails.append(f"first-touch-only: got {len(got)} alerts, expected 1")
    # 3) session assignment is pure UTC hour (no DST)
    if not (session_of(pd.Timestamp("2023-03-12 07:30", tz="UTC")) == "LONDON"
            and session_of(pd.Timestamp("2023-06-15 07:30", tz="UTC")) == "LONDON"
            and session_of(pd.Timestamp("2023-03-12 21:30", tz="UTC")) == "ROLLOVER"):
        fails.append("session/DST")
    # 4) open-level arming: price starts AT the open (v=100). It must NOT alert
    #    at creation ("midnight happened"); only after leaving by 2xtol ($2) and
    #    returning does a touch mean "price came back to the open" — real info.
    idx = pd.date_range("2023-06-15", periods=6, freq="1min", tz="UTC")
    pr = [100, 100, 100, 103, 103, 100]   # sit on open, leave by $3, return
    p = pd.DataFrame({"open": pr, "high": [x + .1 for x in pr],
                      "low": [x - .1 for x in pr], "close": pr}, index=idx)
    ol = [dict(type="DOPEN", value=100.0, active_from=idx[0], active_to=idx[-1] + pd.Timedelta(minutes=1),
               arm="open", created_day=idx[0].normalize(), id=0)]
    sc = Scanner(ol, window_min=1)
    got = []
    for ts, o, h, l, c in p.itertuples(name=None):
        got += sc.on_bar(ts, o, h, l, c)
    got += sc.close()
    if len(got) != 1 or got[0]["alert_ts_utc"][-8:] != "00:05:00":
        fails.append(f"open-arming: {[g['alert_ts_utc'] for g in got]} (want one, at 00:05 the return)")
    # 5) reply parser (deterministic, no interpretation)
    t = parse_reply("A20250616-005 TAKE long 4128 sl 4122 tp 4140 london low reclaim")
    if not (t and t["decision"] == "TAKE" and t["direction"] == "long" and t["entry"] == 4128.0
            and t["stop"] == 4122.0 and t["target"] == 4140.0 and t["reasoning"] == "london low reclaim"):
        fails.append(f"parse TAKE: {t}")
    s = parse_reply("A20250616-002 SKIP no structure, into news")
    if not (s and s["decision"] == "SKIP" and s["reasoning"] == "no structure, into news"):
        fails.append(f"parse SKIP: {s}")
    if parse_reply("garbage") is not None:
        fails.append("parse garbage should be None")
    # 6) alert message carries ONLY the allowed fields (no recommendation)
    msg = format_alert(dict(alert_id="A1", alert_ts_utc="2025-06-16 00:06:00", session="ASIA",
                            level_types="DOPEN,WOPEN", level_price=3443.998,
                            price_at_alert=3442.985, confluence_count=2))
    banned = ("buy", "sell", "long", "short", "take", "skip", "recommend", "signal", "trade")
    if any(b in msg.lower() for b in banned):
        fails.append("alert message contains a recommendation word")
    # 7) auto-MISSED after 4h, and not before
    import tempfile
    con = open_log(Path(tempfile.mkdtemp()) / "t.sqlite")
    insert_alert(con, dict(alert_id="A_old", alert_ts_utc="2025-06-16 00:00:00", level_types="PDH",
                           level_price=1.0, price_at_alert=1.0, session="ASIA", confluence_count=1),
                 epoch=1, bar_source="ibkr")
    insert_alert(con, dict(alert_id="A_new", alert_ts_utc="2025-06-16 03:30:00", level_types="PDL",
                           level_price=1.0, price_at_alert=1.0, session="ASIA", confluence_count=1),
                 epoch=1, bar_source="ibkr")
    con.commit()
    n = sweep_auto_missed(con, pd.Timestamp("2025-06-16 04:30:00", tz="UTC"))
    decided = dict(con.execute("SELECT alert_id, decision FROM decisions").fetchall())
    if not (n == 1 and decided.get("A_old") == "MISSED" and "A_new" not in decided):
        fails.append(f"auto-MISSED: n={n} decided={decided}")
    # 8) counterfactual sweep: only alerts with a full 4h forward window get filled
    insert_alert(con, dict(alert_id="A_cf", alert_ts_utc="2025-06-16 00:00:00", level_types="PDH",
                           level_price=100.0, price_at_alert=100.0, session="ASIA", confluence_count=1),
                 epoch=1, bar_source="ibkr")
    con.commit()
    fdf = pd.DataFrame({"open": 100.0, "high": 110.0, "low": 95.0, "close": 100.0},
                       index=pd.date_range("2025-06-16 00:00", periods=300, freq="1min", tz="UTC"))
    # A_cf (00:00) has >4h of bars; A_new (03:30) does not, at now=04:30
    sweep_counterfactuals(con, fdf, pd.Timestamp("2025-06-16 04:30:00", tz="UTC"))
    cfs = dict((r[0], (r[1], r[2])) for r in
               con.execute("SELECT alert_id, mfe_up_4h, mfe_dn_4h FROM counterfactuals").fetchall())
    if not (cfs.get("A_cf") == (10.0, 5.0) and "A_new" not in cfs):
        fails.append(f"counterfactual sweep: cfs={cfs}")

    # 9) heartbeat market hours follow New York time, so the break is right both
    #    sides of US DST (21-22 UTC in summer, 22-23 UTC in winter)
    want = {"2026-07-14 21:30": False, "2026-07-14 22:30": True,    # summer Tue: break, reopened
            "2026-01-13 21:30": True, "2026-01-13 22:30": False,    # winter Tue: open, break
            "2026-01-11 22:30": False, "2026-01-11 23:30": True}    # winter Sun: before/after open
    got = {t: _market_open(pd.Timestamp(t, tz="UTC")) for t in want}
    if got != want:
        fails.append(f"market hours/DST: {[t for t in want if got[t] != want[t]]}")
    # 10) live == continuous replay: from the moment it comes online, LiveLevels
    #     must emit exactly what one Scanner over the same bars emits — across
    #     session closes, a UTC midnight and a weekend reopen
    win = df[(df.index >= pd.Timestamp("2024-03-07", tz="UTC")) & (df.index < pd.Timestamp("2024-03-13", tz="UTC"))]
    t0 = pd.Timestamp("2024-03-08 10:37", tz="UTC")
    sc, rep = Scanner(build_levels(win)), []
    for ts, o, h, l, c in win.itertuples(name=None):
        out = sc.on_bar(ts, o, h, l, c)
        if ts >= t0:
            rep += out
    live, got = LiveLevels(win[win.index < t0].copy(), t0), []
    for ts, o, h, l, c in win[win.index >= t0].itertuples(name=None):
        got += live.on_bar(ts, o, h, l, c)
    if not rep or got != rep:
        fails.append(f"live vs replay: {len(got)} live vs {len(rep)} replay alerts, "
                     f"{sum(a != b for a, b in zip(got, rep))} differ")
    # 11) live bar filter: IBKR's -1 "no quote", NaN, and frozen quotes outside
    #     trading hours never reach the level engine; a normal bar does
    nan = float("nan")
    bars = {"2026-09-28 10:00": (4280, 4281, 4279, 4280.5),     # Mon, London: real
            "2026-09-28 10:01": (4280, 4281, -1.0, -1.0),       # -1 "no quote"
            "2026-09-28 10:02": (4280, nan, 4279, 4280),        # NaN
            "2026-09-27 18:07": (4285, 4285, 4285, 4285),       # Sun before the reopen
            "2026-07-14 21:30": (4280, 4281, 4279, 4280)}       # summer daily break
    ok = {t: _valid_bar(pd.Timestamp(t, tz="UTC"), *v) for t, v in bars.items()}
    if ok != {"2026-09-28 10:00": True, "2026-09-28 10:01": False, "2026-09-28 10:02": False,
              "2026-09-27 18:07": False, "2026-07-14 21:30": False}:
        fails.append(f"live bar filter: {ok}")

    # 12) journal epochs: a pre-migration journal (old DDL, old positional
    #     INSERTs) keeps its rows as epoch 1 / bar_source ibkr; every bar-source
    #     change starts a new epoch and is logged in source_switches (ibkr ->
    #     stream = 2, rollback to ibkr = 3, never 1 again); an alert id reused
    #     by two epochs stays two alerts
    jp = Path(tempfile.mkdtemp()) / "old.sqlite"
    old = sqlite3.connect(str(jp))
    old.executescript("""
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
      INSERT INTO alerts VALUES('A20251014-001','2025-10-14 01:00:00','PDH',1,1,'ASIA',1,'x');
      INSERT INTO decisions VALUES('A20251014-001','SKIP','2025-10-14 01:05:00',5,NULL,NULL,
        NULL,NULL,'r','2025-10-14 01:05:00','telegram');
      INSERT INTO counterfactuals VALUES('A20251014-001',1,1,0,0,1,1,0,0,'x');
    """)
    old.close()
    con = open_log(jp)
    open_log(jp).close()                                   # idempotent
    rows = con.execute("SELECT alert_id, epoch, bar_source FROM alerts").fetchall()
    kids = [con.execute(f"SELECT epoch FROM {t}").fetchall() for t in ("decisions", "counterfactuals")]
    ver = con.execute("PRAGMA user_version").fetchone()[0]
    epochs = [start_epoch(con, src) for src in ("ibkr", "stream", "stream", "ibkr", "stream")]
    switches = con.execute("SELECT from_source, to_source, epoch FROM source_switches").fetchall()
    _journal_new_alert(con, dict(alert_id="A20251014-001", alert_ts_utc="2025-10-14 09:00:00",
                                 level_types="NY_L", level_price=2.0, price_at_alert=2.0,
                                 session="LONDON", confluence_count=1), epoch=4, bar_source="stream")
    n_missed = sweep_auto_missed(con, pd.Timestamp("2025-10-14 14:00:00", tz="UTC"))
    n_cf = sweep_counterfactuals(con, fdf, pd.Timestamp("2025-10-14 14:00:00", tz="UTC"))
    dec = con.execute("SELECT epoch, decision FROM decisions ORDER BY rowid").fetchall()
    cfe = con.execute("SELECT epoch FROM counterfactuals ORDER BY rowid").fetchall()
    if not (rows == [("A20251014-001", 1, "ibkr")] and kids == [[(1,)], [(1,)]]
            and ver == JOURNAL_VERSION == 3 and epochs == [1, 2, 2, 3, 4]
            and switches == [(None, "ibkr", 1), ("ibkr", "stream", 2), ("stream", "ibkr", 3),
                             ("ibkr", "stream", 4)]
            and n_missed == 1 and n_cf == 1
            and dec == [(1, "SKIP"), (4, "MISSED")] and cfe == [(1,), (4,)]):
        fails.append(f"journal epoch: rows={rows} kids={kids} ver={ver} epochs={epochs} "
                     f"switches={switches} missed={n_missed} cf={n_cf} dec={dec} cfe={cfe}")

    # 13) live started between UTC midnight and the day's first bar still builds
    #     the day's levels on that first bar (it used to wait for 07:00)
    d = pd.Timestamp("2025-06-16", tz="UTC")
    win = df[(df.index >= d - pd.Timedelta(days=14)) & (df.index < d + pd.Timedelta(hours=8))]
    sc, rep = Scanner(build_levels(win)), []
    for ts, o, h, l, c in win.itertuples(name=None):
        out = sc.on_bar(ts, o, h, l, c)
        if ts >= d:
            rep += out
    for start in (d, d + pd.Timedelta(seconds=30)):
        live, got = LiveLevels(win[win.index < d].copy(), start), []
        for ts, o, h, l, c in win[win.index >= d].itertuples(name=None):
            got += live.on_bar(ts, o, h, l, c)
        if not rep or got != rep:
            fails.append(f"live start at {start.time()}: {len(got)} live vs {len(rep)} replay alerts")

    print("SELFTEST:", "ALL PASSED" if not fails else f"FAILED {fails}")
    return not fails


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="UTC day to replay, e.g. 2025-06-16")
    ap.add_argument("--db", default=None, help="write alerts+counterfactuals to this sqlite file")
    ap.add_argument("--live", action="store_true", help="run live + Telegram; bars from SCANNER_BAR_SOURCE (ibkr: needs TWS; stream: needs REDIS_URL + DATAFEED_DB)")
    ap.add_argument("--dry-live", action="store_true", help="run live but print alerts to terminal (no Telegram)")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.live or args.dry_live:
        source = bar_source()
        inst = configure_instrument()
        if source == "stream":
            run_live_stream(dry=args.dry_live, instrument=inst)
        else:
            run_live(dry=args.dry_live, instrument=inst)
        return
    if os.environ.get("INSTRUMENT", "XAUUSD") not in ("", "XAUUSD"):
        ap.error("replay and --selftest use the XAUUSD parquet only; unset INSTRUMENT")
    if args.selftest:
        raise SystemExit(0 if selftest() else 1)
    if not args.date:
        ap.error("give --date YYYY-MM-DD, --live, --dry-live, or --selftest")

    con = open_log(args.db) if args.db else None
    out = replay_day(args.date, con)
    if out.empty:
        print(f"{args.date}: 0 alerts")
        return
    show = ["alert_id", "alert_ts_utc", "session", "level_types", "level_price",
            "price_at_alert", "confluence_count", "mfe_up_1h", "mfe_dn_1h", "mfe_up_4h", "mfe_dn_4h"]
    print(f"\n{args.date} — {len(out)} alerts (first-touch-only, {WINDOW_MIN}-min collapse, ${TOL:.2f} tol)\n")
    with pd.option_context("display.max_rows", None, "display.width", 200):
        print(out[show].to_string(index=False))


if __name__ == "__main__":
    main()
