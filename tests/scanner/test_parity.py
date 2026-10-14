"""Step 6: parity of the stream-mode path.

  stream    datafeed ReplaySource -> bus -> recorder -> store (warm-up), then
            ReplaySource -> bus -> StreamFeed -> LiveLevels (the stream mode path)
  reference one continuous replay (one Scanner over every bar from the stream
            run's warm-up start), the same reference selftest case 10 holds
            the live path to (owner decision 2026-10-14)

The two must be identical, alert for alert (ids, sessions and confluence
included), on every day below and on both buses.

The scanner's --date replay (replay_day) is not the reference. It starts each
day with a fresh Scanner, so a carried session level touched the day before
alerts again. Of the days below that happens on 2025-07-04 only, and
test_date_replay_difference_2025_07_04 asserts exactly that difference.

The days were fixed before the first run, by rule:
  2025-03-09  Sun, US DST starts (reopen 22:00 UTC instead of 23:00)
  2025-03-10  Mon, first full day on EDT
  2025-11-02  Sun, US DST ends (reopen 23:00 UTC)
  2025-11-03  Mon, first full day on EST
  2025-07-04  Fri, the 2025 trading day with the longest intraday run of
              market-open minutes without bars (early close: 241 minutes
              from 16:59 UTC; whole-day closures excluded)
  2025-06-16  Mon, an ordinary day (the scanner docstring's example)
Added after the first run, because 2025-11-02 has no bars at all in the parquet
(the only US DST Sunday 2020-2025 without its hour of bars), so it compares
0 alerts with 0: the most recent DST-end Sunday that has bars, and its Monday:
  2024-11-03  Sun, US DST ends
  2024-11-04  Mon, first full day on EST

The stream-mode scanner comes online right after the newest bar before the
day (at its end, bar_ts + 1 min): D 00:00 midweek, Friday 21:00 for a Monday.
Starting at D 00:00 on 2025-11-03 would find the store "stale" (the parquet
lacks Sunday 23:00-23:59) and take the IBKR fallback, which isn't available
offline; that rule is covered by test_warmup.py.
"""

from pathlib import Path

import pandas as pd
import pytest

import deterministic_scanner as ds
from datafeed.bus import InMemoryBus
from datafeed.ingest.xauusd_bars import STREAM, ReplaySource
from datafeed.recorder.recorder import Recorder
from datafeed.store import schema

from scanner_helpers import Log, redis_bus

PARQUET = Path(ds.PARQUET)
DAYS = ["2025-03-09", "2025-03-10", "2025-11-02", "2025-11-03", "2025-07-04", "2025-06-16",
        "2024-11-03", "2024-11-04"]
KEY = ("alert_ts_utc", "level_price", "level_types", "price_at_alert")

pytestmark = pytest.mark.skipif(not PARQUET.exists(), reason="historical parquet not present (untracked)")


def _key(alerts):
    return [tuple(a[k] for k in KEY) for a in alerts]


def _last_bar_before(t):
    df = pd.read_parquet(PARQUET, columns=["timestamp"],
                         filters=[("timestamp", ">=", t - pd.Timedelta(days=4)), ("timestamp", "<", t)])
    return pd.DatetimeIndex(df["timestamp"]).max()


def _first_bar_after(t):
    df = pd.read_parquet(PARQUET, columns=["timestamp"],
                         filters=[("timestamp", ">=", t), ("timestamp", "<", t + pd.Timedelta(days=4))])
    return pd.DatetimeIndex(df["timestamp"]).min()


def stream_day(day: str, bus, tmp_path):
    """Run one day through the stream-mode path. Returns (alerts of that day, feed, warm-up path)."""
    d = pd.Timestamp(day, tz="UTC")
    now = _last_bar_before(d) + pd.Timedelta(minutes=1)        # the scanner comes online
    start = ds.warmup_start(now)
    # warm-up bars: replay -> bus -> recorder -> store (kind "replay")
    ReplaySource(str(PARQUET), start=start.to_pydatetime(), end=now.to_pydatetime()).run(
        lambda m: bus.publish(STREAM, m))
    store = str(tmp_path / "store.sqlite")
    Recorder(bus, schema.connect(store), STREAM, block_ms=None, sleep=lambda s: None).run(
        stop_when_idle=True)
    hist, path, detail = ds.stream_warmup(now, db=store, kinds=("replay",))
    live = ds.StreamLevels(hist, now)
    con = ds.open_log(tmp_path / "journal.sqlite")
    got = []

    def on_alert(a):
        if ds._journal_new_alert(con, a):
            got.append(a)

    feed = ds.StreamFeed(bus, STREAM, "scanner-parity", live, on_alert, sources=("replay",),
                         last_bar_ts=hist.index.max(), block_ms=None, log=Log())
    while feed.step():                 # the scanner group starts at 0: warm-up entries are backlog
        pass
    # the day, plus the first bar after it, which flushes the day's last 15-min bucket
    # exactly as the next live bar would
    end = _first_bar_after(d + pd.Timedelta(days=1)) + pd.Timedelta(minutes=1)
    ReplaySource(str(PARQUET), start=now.to_pydatetime(), end=end.to_pydatetime()).run(
        lambda m: bus.publish(STREAM, m))
    while feed.step():
        pass
    return [a for a in got if a["alert_ts_utc"][:10] == day], feed, path


_runs: dict = {}


def _run(day, kind, tmp_path_factory):
    """stream_day once per (day, bus) for the whole module."""
    if (day, kind) not in _runs:
        bus = InMemoryBus(maxlen=1_000_000) if kind == "memory" else redis_bus()
        _runs[(day, kind)] = stream_day(day, bus, tmp_path_factory.mktemp(f"{kind}-{day}"))
    return _runs[(day, kind)]


def _diff(a, b):
    return {"only in --date replay": [x for x in a if x not in b],
            "only in stream mode": [x for x in b if x not in a]}


def _continuous(day):
    """One Scanner over every bar from the stream run's warm-up start through the
    flush bar: the reference the live path is held to (selftest case 10)."""
    d = pd.Timestamp(day, tz="UTC")
    now = _last_bar_before(d) + pd.Timedelta(minutes=1)
    end = _first_bar_after(d + pd.Timedelta(days=1)) + pd.Timedelta(minutes=1)
    df = ds.load_data()
    win = df[(df.index >= ds.warmup_start(now)) & (df.index < end)]
    sc, out = ds.Scanner(ds.build_levels(win)), []
    for r in win.itertuples(name=None):
        out += sc.on_bar(*r)
    return out


@pytest.mark.parametrize("kind", ["memory", pytest.param("redis", marks=pytest.mark.redis)])
@pytest.mark.parametrize("day", DAYS)
def test_stream_mode_matches_continuous_replay(day, kind, tmp_path_factory):
    """THE parity requirement: stream mode == continuous replay, exactly."""
    want = [a for a in _continuous(day) if a["alert_ts_utc"][:10] == day]
    got, feed, path = _run(day, kind, tmp_path_factory)
    assert path == "store"
    assert feed.stats["live_bars"] > 0 and feed.stats["out_of_order"] == 0
    assert feed.stats["conflict"] == 0 and feed.stats["invalid_bars"] == 0
    assert got == want, _diff(_key(want), _key(got))


def test_date_replay_difference_2025_07_04(tmp_path_factory):
    """The documented --date difference, asserted exactly. replay_day starts
    2025-07-04 with a fresh Scanner, so July 3's ASIA_L and LONDON_L, already
    touched on July 3 (12:30, 12:08) and dead under first-touch-only, alert
    again at 04:16 and 04:23. Stream mode (and the continuous reference)
    carries that state over. Anything else in the diff fails the test."""
    day = "2025-07-04"
    date = _key(ds.replay_day(day).to_dict("records"))
    got, _, _ = _run(day, "memory", tmp_path_factory)
    assert _diff(date, _key(got)) == {
        "only in --date replay": [("2025-07-04 04:16:00", 3341.648, "ASIA_L", 3340.715),
                                  ("2025-07-04 04:23:00", 3342.865, "LONDON_L", 3341.498)],
        "only in stream mode": []}
    # the cause: both levels were touched on July 3 in the continuous run
    prev = {(a["level_types"], a["level_price"]): a["alert_ts_utc"]
            for a in _continuous(day) if a["alert_ts_utc"][:10] == "2025-07-03"}
    assert prev[("ASIA_L", 3341.648)] == "2025-07-03 12:30:00"
    assert prev[("LONDON_L", 3342.865)] == "2025-07-03 12:08:00"


def test_gap_day_logs_the_gap(tmp_path_factory):
    _, feed, _ = _run("2025-07-04", "memory", tmp_path_factory)
    gaps = feed.log.having("gap of")
    # early close: last bar 16:58 UTC, so 16:59..20:59 (241 minutes) are missing while
    # _market_open says open; the next bar is the Sunday reopen
    assert any("between 2025-07-04 16:58:00+00:00 and 2025-07-06 22:00:00+00:00" in g
               and "gap of 241 " in g for g in gaps), gaps


@pytest.mark.parametrize("start", ["00:00:00", "00:00:30"])
def test_start_before_the_days_first_bar(start):
    """StreamLevels started between midnight and the day's first bar emits what a
    continuous replay emits. LiveLevels (the ibkr path, left unchanged) misses
    the day's levels until the 07:00 rebuild: this pins that known ibkr-path
    bug; fixing it there is an open decision (docs/SCANNER_SWITCH_NOTES.md)."""
    df = ds.load_data()
    d = pd.Timestamp("2025-06-16", tz="UTC")
    win = df[(df.index >= ds.warmup_start(d)) & (df.index < d + pd.Timedelta(days=1))]
    now = d + pd.Timedelta(start)
    sc, rep = ds.Scanner(ds.build_levels(win)), []
    for r in win.itertuples(name=None):
        out = sc.on_bar(*r)
        if r[0] >= d:
            rep += out
    got = {}
    for cls in (ds.StreamLevels, ds.LiveLevels):
        live, got[cls] = cls(win[win.index < d].copy(), now), []
        for r in win[win.index >= d].itertuples(name=None):
            got[cls] += live.on_bar(*r)
    assert rep and got[ds.StreamLevels] == rep
    missed = [a["alert_ts_utc"][11:16] + " " + a["level_types"] for a in rep if a not in got[ds.LiveLevels]]
    assert missed == ["00:06 DOPEN,WOPEN", "00:14 PDH", "00:24 PWH"]
