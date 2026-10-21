"""Step 3: the new bar builder against the scanner's current code path.

The "old path" is the real backend code, loaded by file path without touching
it: TWSConnector._on_pending_tickers (CMDTY tick -> bar) and the scanner's own
on_bar filter (is_final, UTC conversion, _valid_bar). No IBKR connection is
made: TWSConnector() only builds an ib_insync IB object.

Bars must be identical on every sequence except the approved deviations
#1 #2 #3 #4 #7; each of those has its own test asserting the difference.
"""

import importlib.util
import math
import os
import random
import time as _time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("ib_insync")
pd = pytest.importorskip("pandas")

from backend.datafeed.ingest import xauusd_bars as xb
from backend.datafeed.ingest.base import validate_message

ROOT = Path(__file__).resolve().parents[2]
NAN = float("nan")
UTC = timezone.utc


TC = importlib.import_module("backend.scanner.sources.ibkr")      # TWSConnector, the old path
DS = importlib.import_module("backend.scanner")


# ── harnesses ───────────────────────────────────────────────────────────────
# A tick: (now, time, bid, ask, close). `now` is when it arrives; `time` is
# ticker.time (None = IBKR gave none). The old path has no separate receive
# clock: it only sees ticker.time.

def old_path(ticks, symbol="XAUUSD"):
    conn = TC.TWSConnector()
    contract = SimpleNamespace(conId=1, exchange="SMART", secType="CMDTY")
    conn.subscriptions[symbol] = contract
    conn.current_bars[symbol] = {"start_time": None, "ticks": []}
    conn.last_update_time[symbol] = None
    out = []

    def on_bar(_sym, bar):                         # deterministic_scanner.run_live.on_bar
        if not bar.get("is_final"):
            return
        ts = pd.Timestamp(bar["timestamp"])
        ts = ts.tz_convert("UTC") if ts.tz else ts.tz_localize("UTC")
        o, h, l, c = bar["open"], bar["high"], bar["low"], bar["close"]
        if not DS._valid_bar(ts, o, h, l, c):
            return
        out.append((ts.to_pydatetime(), o, h, l, c, bar["tick_count"]))

    conn.on_bar_update(on_bar)
    for _now, t, bid, ask, close in ticks:
        conn._on_pending_tickers([SimpleNamespace(contract=contract, bid=bid, ask=ask,
                                                  close=close, time=t, volume=NAN)])
    return out


def new_path(ticks, symbol="XAUUSD", final_now=None):
    b = xb.BarBuilder(symbol)
    bars = []
    for now, t, bid, ask, close in ticks:
        bars += b.on_tick(now, t, bid, ask, close)
    if final_now is not None:
        bars += b.advance(final_now)
    out = [(x.bar_ts, x.open, x.high, x.low, x.close, x.n_ticks)
           for x in bars if xb._valid_bar(*x.ohlc())]
    return out, b


def T(s):
    return datetime.fromisoformat(s).replace(tzinfo=UTC)


def gen(start, minutes, seed, gaps=(), bad_quotes=0.0, step_s=(0.3, 9.0)):
    """Random-walk ticks from `start` over `minutes`, received instantly
    (now == time). `gaps` = minute offsets with no ticks. bad_quotes = share of
    ticks with no usable quote at all (-1 / NaN / 0 on both sides, no close)."""
    rnd = random.Random(seed)
    px, t, end, out = 4300.0, start, start + timedelta(minutes=minutes), []
    gaps = set(gaps)
    while t < end:
        t += timedelta(seconds=rnd.uniform(*step_s))
        if int((t - start).total_seconds() // 60) in gaps:
            continue
        if rnd.random() < bad_quotes:
            bad = rnd.choice([-1.0, NAN, 0.0])
            out.append((t, t, bad, bad, rnd.choice([NAN, -1.0, None])))
            continue
        if rnd.random() < 0.7:                    # midpoint moves; else repeat (still a tick)
            px += rnd.choice([-0.05, 0.05, -0.1, 0.1, -0.5, 0.5])
        out.append((t, t, round(px - 0.1, 2), round(px + 0.1, 2), 4250.0))
    return out


# ── equivalence ─────────────────────────────────────────────────────────────
SEQUENCES = {
    "plain_london": gen(T("2026-03-02 09:00:00.5"), 90, 1),
    "repeated_mid_many_ticks": gen(T("2026-03-02 10:00:00.1"), 30, 2, step_s=(0.05, 0.4)),
    "gaps": gen(T("2026-03-03 13:00:00.2"), 60, 3, gaps={5, 6, 7, 20, 41}),
    "minus_one_quotes": gen(T("2026-03-04 14:00:00.3"), 60, 4, bad_quotes=0.15),
    # US DST start 2026-03-08: break moves 22-23 UTC -> 21-22 UTC
    "dst_start_sunday_reopen": gen(T("2026-03-08 21:30:00.4"), 120, 5),
    "dst_start_monday_break": gen(T("2026-03-09 20:30:00.1"), 120, 6),
    "pre_dst_friday_break": gen(T("2026-03-06 21:30:00.2"), 120, 7),
    # US DST end 2026-11-01: break moves back to 22-23 UTC
    "dst_end_sunday_reopen": gen(T("2026-11-01 21:30:00.6"), 120, 8),
    "dst_end_monday_break": gen(T("2026-11-02 21:30:00.3"), 120, 9),
    "summer_break": gen(T("2026-07-14 20:45:00.1"), 90, 10),
    # Friday close with frozen quotes all weekend, then the Sunday reopen
    "friday_close_weekend": (gen(T("2026-07-17 20:50:00.2"), 30, 11, step_s=(1, 20))
                             + gen(T("2026-07-19 21:55:00.7"), 20, 12)),
}


@pytest.mark.parametrize("name", sorted(SEQUENCES))
def test_identical_to_scanner_path(name):
    ticks = SEQUENCES[name]
    old = old_path(ticks)
    new, b = new_path(ticks)
    assert old, f"{name}: sequence produced no bars"
    assert new == old
    h = b.health()
    for k in ("skipped_one_sided", "late_ticks", "ticks_without_ts"):
        assert h[k] == 0, k       # none of the deviations exercised


def test_sequences_cover_the_intended_cases():
    """Guard against vacuous equivalence: the edge cases really occur."""
    old = {n: old_path(s) for n, s in SEQUENCES.items()}
    mins = lambda n: [b[0] for b in old[n]]
    assert any(b[5] > 1 for b in old["repeated_mid_many_ticks"])
    assert any((y - x) > timedelta(minutes=1) for x, y in zip(mins("gaps"), mins("gaps")[1:]))
    assert sum(1 for t in SEQUENCES["minus_one_quotes"] if not xb._px(t[2])) > 20
    # break / close minutes are filtered out by _valid_bar in both paths
    assert not any(m.hour == 21 for m in mins("dst_start_monday_break"))     # summer: 21-22 UTC closed
    assert any(m.hour == 21 for m in mins("dst_end_monday_break"))           # winter: 21 UTC open
    assert not any(m.hour == 22 for m in mins("dst_end_monday_break"))       # winter: 22-23 UTC closed
    assert not any(T("2026-07-17 21:00") <= m < T("2026-07-19 22:00") for m in mins("friday_close_weekend"))


def test_market_open_identical_over_a_year():
    t, end, n = T("2026-01-01 00:00"), T("2027-01-01 00:00"), 0
    while t < end:
        assert xb._market_open(t) == DS._market_open(pd.Timestamp(t)), t
        t += timedelta(minutes=7)
        n += 1
    assert n > 70_000
    with pytest.raises(TypeError):
        xb._market_open(datetime(2026, 1, 5, 10))


def test_valid_bar_identical():
    rnd = random.Random(0)
    vals = [4280.0, 4281.5, -1.0, 0.0, NAN, 4279.0]
    t0 = T("2026-07-13 00:00")
    for _ in range(5000):
        ts = t0 + timedelta(minutes=rnd.randrange(7 * 24 * 60))
        bar = [rnd.choice(vals) for _ in range(4)]
        assert xb._valid_bar(ts, *bar) == DS._valid_bar(pd.Timestamp(ts), *bar)


# ── deviations: each asserts the documented difference ──────────────────────
BASE = [(T("2026-03-02 10:00:05"), T("2026-03-02 10:00:05"), 4300.0, 4300.2, 4250.0),
        (T("2026-03-02 10:00:30"), T("2026-03-02 10:00:30"), 4301.0, 4301.2, 4250.0)]
NEXT = [(T("2026-03-02 10:01:01"), T("2026-03-02 10:01:01"), 4302.0, 4302.2, 4250.0)]


def test_dev1_one_sided_quote_uses_stale_close_old_skips_new():
    one_sided = [(T("2026-03-02 10:00:40"), T("2026-03-02 10:00:40"), NAN, 4301.2, 4250.0)]
    seq = BASE + one_sided + NEXT
    old = old_path(seq)
    new, b = new_path(seq)
    assert old[0][3] == 4250.0 and old[0][5] == 3          # stale close became the low
    assert new[0][3] == 4300.1 and new[0][5] == 2
    assert b.health()["skipped_one_sided"] == 1


def test_dev2_late_tick_folded_old_dropped_new():
    late = [(T("2026-03-02 10:01:02"), T("2026-03-02 10:00:59"), 4310.0, 4310.2, 4250.0)]
    after = [(T("2026-03-02 10:02:00.5"), T("2026-03-02 10:02:00.5"), 4302.0, 4302.2, 4250.0)]
    seq = BASE + NEXT + late + after
    old = old_path(seq)
    new, b = new_path(seq)
    assert old[1][0] == T("2026-03-02 10:01") and old[1][2] == 4310.1   # folded into 10:01
    assert new[1][0] == T("2026-03-02 10:01") and new[1][2] == 4302.1
    assert old[0] == new[0]
    assert b.health()["late_ticks"] == 1


def test_dev3_timer_closes_quiet_bar_old_waits_for_next_tick():
    old = old_path(BASE)
    new, b = new_path(BASE, final_now=T("2026-03-02 10:01:02"))   # minute end + GRACE_S
    assert old == []                                               # still waiting
    assert [x[0] for x in new] == [T("2026-03-02 10:00")]
    assert b.health()["timer_closes"] == 1
    _, b2 = new_path(BASE, final_now=T("2026-03-02 10:01:01.9"))  # inside grace: still open
    assert b2.health()["bars_closed"] == 0


def test_dev3_tick_after_grace_is_late_old_still_accepts():
    # ticker.time 10:00:59 but received at 10:01:03 (after the 10:00 bar's deadline)
    seq = BASE + [(T("2026-03-02 10:01:03"), T("2026-03-02 10:00:59"), 4320.0, 4320.2, 4250.0)] + NEXT
    old = old_path(seq)
    new, b = new_path(seq)
    assert old[0][2] == 4320.1                                     # old: still in its own bar
    assert new[0][2] == 4301.1
    assert b.health()["late_ticks"] == 1 and b.health()["timer_closes"] == 1


def test_dev3_grace_is_a_named_constant():
    assert xb.GRACE_S == 2.0
    b = xb.BarBuilder(grace_s=5.0)
    b.on_tick(*BASE[0])
    assert b.advance(T("2026-03-02 10:01:04")) == []
    assert len(b.advance(T("2026-03-02 10:01:05"))) == 1


@pytest.fixture
def new_york_local_time():
    old = os.environ.get("TZ")
    os.environ["TZ"] = "America/New_York"
    _time.tzset()
    yield
    if old is None:
        del os.environ["TZ"]
    else:
        os.environ["TZ"] = old
    _time.tzset()


def test_dev4_missing_tick_time_old_local_as_utc_new_true_utc(new_york_local_time):
    # old path: ticker.time=None -> naive local datetime.now(); the scanner then
    # tz_localize("UTC")s it, so on a New York host the bar is stamped 4-5 h early
    conn = TC.TWSConnector()
    contract = SimpleNamespace(conId=1, exchange="SMART", secType="CMDTY")
    conn.subscriptions["XAUUSD"] = contract
    conn.current_bars["XAUUSD"] = {"start_time": None, "ticks": []}
    conn.last_update_time["XAUUSD"] = None
    raw = []
    conn.on_bar_update(lambda _s, bar: raw.append(bar))
    conn._on_pending_tickers([SimpleNamespace(contract=contract, bid=4300.0, ask=4300.2,
                                              close=NAN, time=None, volume=NAN)])
    # 1) the open bar is keyed by a NAIVE local minute ...
    start = conn.current_bars["XAUUSD"]["start_time"]
    assert start.tzinfo is None
    # ... which the scanner would label UTC: 4-5 h off on a New York host
    labelled = pd.Timestamp(start).tz_localize("UTC")
    shift = pd.Timestamp(datetime.now(UTC)).floor("min") - labelled
    want = timedelta(hours=4 if _time.localtime().tm_isdst else 5)
    assert abs(shift - want) <= timedelta(minutes=1)
    # 2) worse: every later (tz-aware) tick now fails the naive/aware comparison
    #    inside the handler, is logged and dropped, so no bar is ever emitted again
    #    until the process restarts
    t0 = datetime.now(UTC)
    for k in range(1, 6):
        conn._on_pending_tickers([SimpleNamespace(contract=contract, bid=4301.0, ask=4301.2, close=NAN,
                                                  time=t0 + timedelta(minutes=k), volume=NAN)])
    assert [x for x in raw if x["is_final"]] == []
    assert conn.current_bars["XAUUSD"]["start_time"] == start          # stuck

    now = T("2026-03-02 10:00:20")
    new, b = new_path([(now, None, 4300.0, 4300.2, NAN)], final_now=T("2026-03-02 10:01:02"))
    assert new[0][0] == T("2026-03-02 10:00")                      # receive time, real UTC
    assert b.health()["ticks_without_ts"] == 1
    with pytest.raises(ValueError):
        xb.BarBuilder().on_tick(now, datetime(2026, 3, 2, 10), 4300.0, 4300.2)


def test_dev7_history_forming_minute_dropped():
    now = T("2026-03-02 10:05:30")
    hist = [(T("2026-03-02 10:03"), 1, 2, 0.5, 1.5), (T("2026-03-02 10:04"), 1, 2, 0.5, 1.5),
            (T("2026-03-02 10:05"), 1, 2, 0.5, 1.5)]                # 10:05 is forming
    df = pd.DataFrame([h[1:] for h in hist], columns=["open", "high", "low", "close"],
                      index=pd.DatetimeIndex([h[0] for h in hist]))
    old_kept = df[[DS._valid_bar(*r) for r in df.itertuples(name=None)]]   # run_live's filter
    assert len(old_kept) == 3                                         # old keeps the partial bar
    assert [h[0] for h in xb.trim_history(hist, now)] == [T("2026-03-02 10:03"), T("2026-03-02 10:04")]


# ── message and health ──────────────────────────────────────────────────────
def test_bar_message_schema():
    _, b = new_path(BASE, final_now=T("2026-03-02 10:01:02"))
    bar = xb.Bar("XAUUSD", T("2026-03-02 10:00"), 4300.1, 4301.1, 4300.1, 4301.1, 2)
    m = xb.bar_message(bar, source="ibkr", history=False)
    validate_message(m)
    assert m["source_ts"] == m["bar_ts"] == "2026-03-02T10:00:00+00:00"
    assert m["schema_version"] == xb.SCHEMA_VERSION == 1
    assert "volume" not in m
    assert {"open", "high", "low", "close", "n_ticks", "history"} <= set(m)


def test_health_counters_present():
    h = xb.BarBuilder().health()
    for k in ("skipped_one_sided", "late_ticks", "ticks_without_ts", "timer_closes"):
        assert h[k] == 0
