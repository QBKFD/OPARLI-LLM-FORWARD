"""Bar source "stream" (SCANNER_BAR_SOURCE=stream): the warm-up from the
datafeed store, the stream consumer (StreamFeed), the staleness alert and the
live loop (run_live_stream). backend.datafeed is imported lazily."""
from __future__ import annotations

import os

import pandas as pd

from backend.levels.build import build_levels
from backend.scanner.config import RECONNECT_BACKOFF_S
from backend.scanner.engine import AlertSink, LiveLevels, _alert_sender, beat
from backend.scanner.journal.outcomes import sweep_counterfactuals, sweep_outcomes
from backend.scanner.journal.schema import open_log, start_epoch
from backend.scanner.journal.writes import last_live_bar, load_offset, save_offset, sweep_auto_missed
from backend.scanner.market import _open_minutes, _utc, _valid_bar
from backend.scanner.telegram import Telegram, _handle_reply, _send_quietly, poll_replies

# ── Stream bar source (SCANNER_BAR_SOURCE=stream) ──────────────────────────
# Bars come from the datafeed stream instead of this process's own IBKR
# connection. Level engine, journal and Telegram are the same code as run_live;
# only where the bars come from changes. run_live above is left as it was: it is
# the rollback (SCANNER_BAR_SOURCE=ibkr). datafeed is imported lazily, so the
# ibkr path doesn't need the package installed.
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
    from backend.datafeed.store.query import bars_before
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

    Reads backend.datafeed.store.query.bars_before over [warmup_start(now), now) (one bar
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
    name gets the entry back first (backend.datafeed.bus: own pending entries first)
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


def run_live_stream(symbol: str = "XAUUSD", dry: bool = False, env=os.environ):
    """Live path fed by the datafeed stream. Same journal, Telegram, replies and
    sweeps as run_live; dry=True prints alerts instead of sending them.

    Never connects to IBKR: the datafeed feed is the only gateway client. The
    warm-up comes from the datafeed store; while it is unusable the scanner
    waits (wait_for_store). Crash-only like run_live: a failing bus (Redis down)
    backs off and exits so Docker restarts a clean cold start."""
    import threading
    import time as _time
    from backend.datafeed.bus import RedisStreamBus
    from backend.datafeed.ingest.xauusd_bars import STREAM

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

    feed = StreamFeed(RedisStreamBus(url=env.get("REDIS_URL")), STREAM, consumer, live, sink,
                      lock=lock, last_bar_ts=warm_end, progress=sink.progress,
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
    print(f"{mode}: scanning {symbol} from stream {STREAM} (group {group}, consumer "
          f"{consumer}), {active} levels active, journal epoch {epoch}. Ctrl-C to stop.", flush=True)

    offset = load_offset(con)
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
            polled_from = offset
            msgs, offset = poll_replies(tg, offset)
            for msg in msgs:
                _handle_reply(con, lock, tg, msg, epoch)
            if msgs or offset != polled_from:
                with lock:
                    save_offset(con, offset)
            if _time.time() - last_sweep > 300:      # every 5 min
                with lock:
                    now = pd.Timestamp.now(tz="UTC")
                    sweep_auto_missed(con, now)
                    sweep_counterfactuals(con, live.hist.sort_index(), now)
                    sweep_outcomes(con, live.hist.sort_index(), now)
                last_sweep = _time.time()
    except KeyboardInterrupt:
        print("\nstopping.")
