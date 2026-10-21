"""Bars in, alerts out: LiveLevels keeps the level engine (backend.levels)
equal to a continuous replay, AlertSink journals and delivers its alerts,
and beat() writes the heartbeat. Shared by both live bar sources."""
from __future__ import annotations

import os

import pandas as pd

from backend.levels.build import SESSIONS, build_levels
from backend.levels.touch import WINDOW_MIN, Scanner
from backend.scanner.journal.writes import insert_alert, record_progress
from backend.scanner.market import _utc
from backend.scanner.telegram import format_alert

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
