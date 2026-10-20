"""Owner decision 4 (2026-10-14): 4h candles anchored to the NY 17:00 trading-day
open (17, 21, 01, 05, 09, 13 America/New_York), DST-safe."""

from datetime import datetime, timedelta, timezone

import pytest

from agents.config import SnapshotConfig
from agents.evidence import check_ref
from agents.resample import resample
from agents.schemas import NY, Bar, bucket_end, bucket_start, is_aligned
from agents.snapshot import build_snapshot
from agents_testkit import make_replay_db, synthetic_rows

UTC = timezone.utc
ANCHORS = {17, 21, 1, 5, 9, 13}
SUMMER_UTC = {21, 1, 5, 9, 13, 17}          # EDT = UTC-4
WINTER_UTC = {22, 2, 6, 10, 14, 18}         # EST = UTC-5


def _u(s):
    return datetime.fromisoformat(s).replace(tzinfo=UTC)


def _continuous(start, end):
    """A 1m bar every minute (no market-hours filter): every bucket is full."""
    out, t, px = [], start, 100.0
    while t < end:
        out.append(Bar(ts=t, open=px, high=px + 1, low=px - 1, close=px + 0.5))
        t, px = t + timedelta(minutes=1), px + 0.01
    return out


@pytest.mark.parametrize("start, end, dst_open, dst_hours", [
    # US DST starts 2025-03-09 02:00 EST -> 03:00 EDT: the 01:00 bucket lasts 3 h
    (_u("2025-03-06 00:00"), _u("2025-03-12 00:00"), _u("2025-03-09 06:00"), 3),
    # US DST ends 2025-11-02 02:00 EDT -> 01:00 EST: the 01:00 bucket lasts 5 h
    (_u("2025-10-30 00:00"), _u("2025-11-05 00:00"), _u("2025-11-02 05:00"), 5),
])
def test_4h_buckets_across_a_dst_change(start, end, dst_open, dst_hours):
    bars = _continuous(start, end)
    h4 = resample(bars, "4h", end)
    # every bucket opens on a NY anchor, on the hour
    for b in h4:
        local = b.ts.astimezone(NY)
        assert (local.hour, local.minute) in {(h, 0) for h in ANCHORS}, local
        assert is_aligned(b.ts, "4h")
    # buckets tile time: each ends where the next opens, and holds exactly its minutes
    for a, b in zip(h4, h4[1:]):
        assert bucket_end(a.ts, "4h") == b.ts
    lengths = {b.ts: bucket_end(b.ts, "4h") - b.ts for b in h4}
    odd = {ts: d for ts, d in lengths.items() if d != timedelta(hours=4)}
    assert odd == {dst_open: timedelta(hours=dst_hours)}
    for b in h4[1:-1]:                                         # first bucket may be cut by the data start
        assert b.n_1m == lengths[b.ts] / timedelta(minutes=1)
    assert sum(b.n_1m for b in h4) == len(bars)
    # UTC opens shift by one hour across the change
    before = {b.ts.hour for b in h4 if b.ts < dst_open}
    after = {b.ts.hour for b in h4 if b.ts > dst_open + timedelta(hours=dst_hours)}
    spring = dst_hours == 3
    assert before == (WINTER_UTC if spring else SUMMER_UTC)
    assert after == (SUMMER_UTC if spring else WINTER_UTC)


@pytest.mark.parametrize("ts, want", [
    ("2025-06-11 14:30", "2025-06-11 13:00"),     # 10:30 EDT -> 09:00 EDT
    ("2025-06-10 21:00", "2025-06-10 21:00"),     # 17:00 EDT: the trading-day open
    ("2025-06-11 00:59", "2025-06-10 21:00"),     # 20:59 EDT -> 17:00 EDT
    ("2025-01-15 22:10", "2025-01-15 22:00"),     # 17:10 EST -> 17:00 EST
    ("2025-01-16 02:00", "2025-01-16 02:00"),     # 21:00 EST
    ("2025-11-02 06:30", "2025-11-02 05:00"),     # 01:30 EST (second 01:xx) -> 01:00 EDT, the first one
])
def test_bucket_start(ts, want):
    assert bucket_start(_u(ts), "4h") == _u(want)


def test_other_timeframes_stay_on_the_utc_epoch():
    t = _u("2025-11-02 06:37")
    assert bucket_start(t, "15m") == _u("2025-11-02 06:30")
    assert bucket_start(t, "1h") == _u("2025-11-02 06:00")
    assert bucket_end(_u("2025-11-02 06:00"), "1h") == _u("2025-11-02 07:00")


@pytest.fixture(scope="module")
def dst_week_db(tmp_path_factory):
    # Thu 2025-10-30 .. Tue 2025-11-04: the Sunday reopen falls on the day DST ends.
    # datafeed's _valid_bar drops the daily break and the weekend.
    rows = synthetic_rows(_u("2025-10-30 00:00"), _u("2025-11-04 18:00"), seed=11)
    return make_replay_db(tmp_path_factory.mktemp("dst"), rows)


def test_replay_snapshot_across_the_dst_change(dst_week_db):
    cfg = SnapshotConfig(mode="replay", fetch_lookback=timedelta(days=7), keep={"1m": 240, "4h": 1000})
    snap = build_snapshot(_u("2025-11-04 16:30:20"), cfg, dst_week_db)
    h4 = snap.bars["4h"]
    assert all(b.ts.astimezone(NY).hour in ANCHORS for b in h4)
    sat = _u("2025-11-01 12:00")
    assert {b.ts.hour for b in h4 if b.ts < sat} <= SUMMER_UTC
    assert {b.ts.hour for b in h4 if b.ts > sat} <= WINTER_UTC
    # Friday's last bucket 13:00-17:00 EDT (17:00-21:00 UTC) closes with the market
    fri = next(b for b in h4 if b.ts == _u("2025-10-31 17:00"))
    assert fri.complete and 200 < fri.n_1m <= 240
    # the week opens Sunday 18:00 EST inside the 17:00 EST bucket (22:00 UTC): 3 hours of bars
    sun = next(b for b in h4 if b.ts.astimezone(NY).weekday() == 6)
    assert sun.ts == _u("2025-11-02 22:00") and 150 < sun.n_1m <= 180
    assert not any(_u("2025-10-31 21:00") <= b.ts < _u("2025-11-02 22:00") for b in h4)   # nothing over the weekend
    # the partial bucket at as_of 16:30 UTC (11:30 EST) opened at 09:00 EST = 14:00 UTC
    assert (h4[-1].ts, h4[-1].complete) == (_u("2025-11-04 14:00"), False)


def test_evidence_refs_use_the_ny_anchored_opens(dst_week_db):
    snap = build_snapshot(_u("2025-11-04 16:30:20"), SnapshotConfig(mode="replay", keep={"1m": 240, "4h": 1000}),
                          dst_week_db)
    assert check_ref("bars:4h:2025-10-31T17:00:00+00:00..2025-11-04T14:00:00+00:00", snap) is None
    assert check_ref("bars:4h:2025-11-03T13:00:00-05:00..2025-11-03T13:00:00-05:00", snap) is None   # NY-time ref
    assert "no 4h bar opens at" in check_ref("bars:4h:2025-11-04T12:00:00+00:00..2025-11-04T12:00:00+00:00", snap)
