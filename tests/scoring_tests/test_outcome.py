"""scoring.outcome: walk-forward scoring on real XAUUSD 1-min bars (the research
parquet, pre-2024; skipped without it) and synthetic edge cases."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from risk.bracket import bracket
from scoring.outcome import (NoBarsError, Outcome, bars_from_frame, read_bars, score,
                             score_neutral)

ROOT = Path(__file__).resolve().parents[2]
PARQUET = ROOT / "database" / "ohlcv_data" / "XAUUSD_1min_UTC_clean.parquet"
UTC = timezone.utc


def T(s):
    return datetime.fromisoformat(s).replace(tzinfo=UTC)


@pytest.fixture(scope="module")
def real_frame():
    """Real bars between two UTC times (pre-2024), as a pandas OHLC frame."""
    pd = pytest.importorskip("pandas")
    if not PARQUET.exists():
        pytest.skip("research parquet not available")

    def load(start, end):
        df = pd.read_parquet(PARQUET, filters=[("timestamp", ">=", pd.Timestamp(start, tz="UTC")),
                                               ("timestamp", "<", pd.Timestamp(end, tz="UTC"))])
        df = df.set_index("timestamp").sort_index()
        return df[~df.index.duplicated(keep="first")][["open", "high", "low", "close"]]
    return load


@pytest.fixture(scope="module")
def real():
    """Real bars between two UTC times (pre-2024), as tuples."""
    pd = pytest.importorskip("pandas")
    if not PARQUET.exists():
        pytest.skip("research parquet not available")

    def load(start, end):
        df = pd.read_parquet(PARQUET, filters=[("timestamp", ">=", pd.Timestamp(start, tz="UTC")),
                                               ("timestamp", "<", pd.Timestamp(end, tz="UTC"))])
        df = df.set_index("timestamp").sort_index()
        return bars_from_frame(df[~df.index.duplicated(keep="first")])
    return load


def at(bars, ts):
    return next(b for b in bars if b[0] == ts)


# ---------------------------------------------------------------- real bars

def test_same_bar_stop_and_target_counts_as_stop(real):
    t = T("2023-06-13 12:30")                         # US CPI release minute, range 21.46
    bars = real("2023-06-13 12:00", "2023-06-13 14:00")
    _, o, h, lo, _ = at(bars, t)
    stop, target = o - 0.5 * (o - lo), o + 0.5 * (h - o)
    assert lo <= stop < o < target <= h               # both levels inside the first bar
    out = score(entry_time=t, direction="long", entry_price=o, stop=stop, target=target,
                max_hold_minutes=60, bars=bars)
    assert (out.reason, out.exit_time, out.exit_price, out.gap, out.bars_used) == \
        ("stop", t, stop, False, 1)
    assert out.r_multiple == pytest.approx(-1.0)
    # the short side of the same bar: stop above, target below, also a stop
    s_stop, s_target = o + 0.5 * (h - o), o - 0.5 * (o - lo)
    out = score(entry_time=t, direction="short", entry_price=o, stop=s_stop, target=s_target,
                max_hold_minutes=60, bars=bars)
    assert (out.reason, out.exit_price) == ("stop", s_stop)
    assert out.r_multiple == pytest.approx(-1.0)


def test_gap_through_the_stop_fills_at_the_open(real):
    fri, sun = T("2023-10-20 20:59"), T("2023-10-22 22:00")       # last bar Friday -> reopen
    bars = real("2023-10-20 20:00", "2023-10-23 01:00")
    _, o, h, lo, _ = at(bars, fri)
    reopen = at(bars, sun)[1]
    stop = (reopen + lo) / 2                          # below Friday's last low, above the reopen
    assert reopen < stop < lo
    target = o + 10 * (o - stop)
    out = score(entry_time=fri, direction="long", entry_price=o, stop=stop, target=target,
                max_hold_minutes=3 * 1440, bars=bars)
    assert (out.reason, out.exit_time, out.exit_price, out.gap, out.bars_used) == \
        ("stop", sun, reopen, True, 2)
    assert out.r_multiple == pytest.approx((reopen - o) / (o - stop))
    assert out.r_multiple < -1.5                      # worse than the stop
    assert out.mae == pytest.approx(o - reopen)       # at the fill, not the reopen bar's low
    assert out.mfe == pytest.approx(h - o)            # only Friday's bar was fully held


def test_timeout_exits_at_the_last_bar_inside_the_holding_period(real):
    t = T("2023-06-14 03:00:30")                      # mid-minute: first bar used is 03:01
    bars = real("2023-06-14 02:00", "2023-06-14 05:00")
    window = [b for b in bars if b[0] >= t and b[0] + timedelta(minutes=1) <= t + timedelta(minutes=60)]
    entry = at(bars, T("2023-06-14 03:00"))[4]        # the close of the bar before the first used
    out = score(entry_time=t, direction="long", entry_price=entry, stop=entry * 0.95,
                target=entry * 1.05, max_hold_minutes=60, bars=bars)
    assert window[0][0] == T("2023-06-14 03:01") and window[-1][0] == T("2023-06-14 03:59")
    assert (out.reason, out.exit_time, out.exit_price, out.bars_used) == \
        ("timeout", window[-1][0], window[-1][4], len(window))
    assert out.mae == pytest.approx(max(0.0, entry - min(b[3] for b in window)))
    assert out.mfe == pytest.approx(max(0.0, max(b[2] for b in window) - entry))


def test_target_hit_on_real_bars(real):
    t = T("2023-06-14 03:00")
    bars = real("2023-06-14 03:00", "2023-06-14 05:00")
    entry = bars[0][1]
    first30 = [b for b in bars if b[0] < t + timedelta(minutes=30)]
    target = max(b[2] for b in first30)
    assert target > entry
    hit = next(b for b in bars if b[2] >= target)
    stop = entry - 3 * (target - entry)
    out = score(entry_time=t, direction="long", entry_price=entry, stop=stop, target=target,
                max_hold_minutes=120, bars=bars)
    held = [b for b in bars if b[0] <= hit[0]]
    assert min(b[3] for b in held) > stop             # the stop was never touched
    assert (out.reason, out.exit_time, out.exit_price, out.gap) == ("target", hit[0], target, False)
    assert out.r_multiple == pytest.approx(1 / 3)
    assert out.mfe == pytest.approx(target - entry)
    assert out.mae == pytest.approx(max(0.0, entry - min(b[3] for b in held)))


def test_score_twice_neutral_har_and_agent_on_the_same_bars(real, real_frame):
    from datafeed.features import vol_block as vb
    t = T("2023-06-14 08:00")
    model = vb.load_sigma_model()
    st = vb.state_at(model.params, real_frame("2023-05-25", "2023-06-14 08:00"), t)
    assert st.reason is None
    bars = real("2023-06-14 08:00", "2023-06-14 13:00")
    entry, k = bars[0][1], 1.0
    ns = score_neutral(entry_time=t, direction="short", entry_price=entry, k=k,
                       max_hold_minutes=240, bars=bars, model=model, state=st)
    sig = vb.sigma_over(model, st, 240)                    # HAR over the holding period
    assert ns.sigma == sig and sig.sigma_px > 0
    assert ns.bracket == bracket(entry=entry, direction="short", sigma=sig.sigma_px, k=k)
    assert ns.outcome == score(entry_time=t, direction="short", entry_price=entry,
                               stop=ns.bracket.stop, target=ns.bracket.target,
                               max_hold_minutes=240, bars=bars)
    agent = score(entry_time=t, direction="short", entry_price=entry, stop=entry + 1.0,
                  target=entry - 1.0, max_hold_minutes=240, bars=bars)
    assert isinstance(agent, Outcome)


def test_bars_before_entry_are_never_used(real):
    t = T("2023-06-14 03:00:30")
    bars = real("2023-06-14 02:00", "2023-06-14 05:00")
    entry = at(bars, T("2023-06-14 03:00"))[4]
    kw = dict(entry_time=t, direction="long", entry_price=entry, stop=entry - 2.0,
              target=entry + 4.0, max_hold_minutes=60)
    base = score(bars=bars, **kw)
    # the bar that opened at 03:00 (before entry) crashes through the stop: ignored
    poisoned = [(ts, o, h, 1.0, c) if ts <= T("2023-06-14 03:00") else (ts, o, h, lo, c)
                for ts, o, h, lo, c in bars]
    assert score(bars=poisoned, **kw) == base


# ---------------------------------------------------------------- synthetic edges

def bar(ts, o, h, lo, c):
    return (T(ts), o, h, lo, c)


def test_gap_through_the_target_fills_at_the_target():
    bars = [bar("2024-01-02 10:00", 100.0, 100.5, 99.5, 100.0),
            bar("2024-01-02 10:01", 106.0, 107.0, 105.0, 106.5)]          # opens above target
    out = score(entry_time=T("2024-01-02 10:00"), direction="long", entry_price=100.0, stop=98.0,
                target=104.0, max_hold_minutes=10, bars=bars)
    assert (out.reason, out.exit_price, out.r_multiple, out.gap) == ("target", 104.0, 2.0, False)


def test_short_gap_through_the_stop():
    bars = [bar("2024-01-02 10:00", 100.0, 100.5, 99.5, 100.0),
            bar("2024-01-02 10:01", 103.0, 103.5, 102.0, 103.0)]          # opens above the stop
    out = score(entry_time=T("2024-01-02 10:00"), direction="short", entry_price=100.0, stop=101.0,
                target=98.0, max_hold_minutes=10, bars=bars)
    assert (out.reason, out.exit_price, out.gap, out.r_multiple) == ("stop", 103.0, True, -3.0)
    assert out.mae == 3.0 and out.mfe == 0.5


def test_inconsistent_vendor_bar_is_widened():
    bars = [bar("2024-01-02 10:00", 100.0, 99.0, 98.5, 99.5)]            # high below the open
    out = score(entry_time=T("2024-01-02 10:00"), direction="short", entry_price=99.8, stop=99.9,
                target=95.0, max_hold_minutes=5, bars=bars)
    assert out.reason == "stop" and out.exit_price == 100.0 and out.gap   # the open is above the stop


def test_no_bars_raises():
    with pytest.raises(NoBarsError):
        score(entry_time=T("2024-01-06 12:00"), direction="long", entry_price=100.0, stop=99.0,
              target=102.0, max_hold_minutes=60,
              bars=[bar("2024-01-05 21:00", 100.0, 100.0, 100.0, 100.0)])


@pytest.mark.parametrize("change", [
    {"direction": "none"}, {"stop": 101.0}, {"target": 99.0}, {"entry_price": 0.0},
    {"stop": float("nan")}, {"max_hold_minutes": 0}, {"max_hold_minutes": True},
    {"max_hold_minutes": 60.0}, {"entry_time": datetime(2024, 1, 2, 10)},
])
def test_input_errors(change):
    kw = dict(entry_time=T("2024-01-02 10:00"), direction="long", entry_price=100.0, stop=99.0,
              target=102.0, max_hold_minutes=60,
              bars=[bar("2024-01-02 10:00", 100.0, 100.5, 99.5, 100.0)]) | change
    with pytest.raises(ValueError):
        score(**kw)


def test_bars_must_be_increasing_and_positive():
    kw = dict(entry_time=T("2024-01-02 10:00"), direction="long", entry_price=100.0, stop=99.0,
              target=102.0, max_hold_minutes=60)
    with pytest.raises(ValueError, match="increasing"):
        score(bars=[bar("2024-01-02 10:01", 100, 100, 100, 100), bar("2024-01-02 10:00", 100, 100, 100, 100)], **kw)
    with pytest.raises(ValueError, match="bar price"):
        score(bars=[bar("2024-01-02 10:00", 100, 100, -1, 100)], **kw)


def test_read_bars_from_the_store(tmp_path):
    from datafeed.ingest.xauusd_bars import Bar, bar_message
    from datafeed.store import schema
    path = str(tmp_path / "store.sqlite")
    con = schema.connect(path)
    con.execute("BEGIN")
    for i in range(130):
        ts = T("2024-01-02 10:00") + timedelta(minutes=i)
        schema.insert_bar(con, bar_message(Bar("XAUUSD", ts, 100.0 + i, 101.0 + i, 99.0 + i, 100.5 + i, None),
                                           source="replay", history=False, ingest_ts=ts + timedelta(minutes=1)))
    con.execute("COMMIT")
    con.close()
    got = read_bars("XAUUSD", T("2024-01-02 10:30:30"), 60, db=path, kinds=("replay",))
    assert got[0][0] == T("2024-01-02 10:31") and got[-1][0] == T("2024-01-02 11:29")
    assert len(got) == 59 and got[0][1:] == (131.0, 132.0, 130.0, 131.5)


# ---------------------------------------------------------------- neutral bracket: HAR sigma

def har_state(t, model, L=0.0, close=100.0):
    from datafeed.features import vol_block as vb
    return vb.VolState(t, None, L, 0.0, 0.0, close, None, None, params_sha256=model.params.sha256)


def test_score_neutral_uses_har_sigma_over_the_holding_period():
    from datafeed.features import vol_block as vb
    model = vb.load_sigma_model()
    t = T("2024-01-03 15:00")                                # Wednesday, inside market hours
    st = har_state(t, model)
    bars = [bar("2024-01-03 15:00", 100.0, 100.1, 99.9, 100.0)]
    for H in (60, 240):
        ns = score_neutral(entry_time=t, direction="long", entry_price=100.0, k=1.5,
                           max_hold_minutes=H, bars=bars, model=model, state=st)
        sig = vb.sigma_over(model, st, H)
        assert ns.sigma == sig
        assert ns.bracket.stop == pytest.approx(100.0 - 1.5 * sig.sigma_px)
        assert ns.bracket.target == pytest.approx(100.0 + 3.0 * sig.sigma_px)


def test_score_neutral_refusals():
    from datafeed.features import vol_block as vb
    model = vb.load_sigma_model()
    t = T("2024-01-03 15:00")
    bars = [bar("2024-01-03 15:00", 100.0, 100.1, 99.9, 100.0)]
    kw = dict(entry_time=t, direction="long", entry_price=100.0, k=1.0, bars=bars, model=model)
    with pytest.raises(ValueError, match="H must be one of"):          # not a fitted horizon
        score_neutral(max_hold_minutes=120, state=har_state(t, model), **kw)
    with pytest.raises(ValueError, match="entry time"):               # state from another time
        score_neutral(max_hold_minutes=60, state=har_state(t - timedelta(hours=1), model), **kw)
    other = vb.load_sigma_model("txt2007_2023" if model.name != "txt2007_2023" else "parquet2021_2023")
    with pytest.raises(ValueError, match="other VolParams"):         # state from other params
        score_neutral(max_hold_minutes=60, state=har_state(t, other), **kw)
