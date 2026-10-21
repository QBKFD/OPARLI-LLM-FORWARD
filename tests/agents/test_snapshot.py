"""Step 3: snapshot builder on replay data."""

import logging
from datetime import datetime, timedelta, timezone

import pytest

from backend.datafeed.ingest import xauusd_bars
from backend.datafeed.store.query import bars_before

from backend.agents import snapshot as snapmod
from backend.agents.config import SnapshotConfig
from backend.agents.resample import resample
from backend.agents.schemas import Bar, bucket_end
from backend.agents.snapshot import SealedError, Stale, build_snapshot, floor_minute, prepare_snapshot
from tests.agents.agents_testkit import make_replay_db

UTC = timezone.utc
CFG = SnapshotConfig(mode="replay")
T = datetime(2025, 6, 11, 14, 30, 30, tzinfo=UTC)      # mid-session Wednesday, not on a whole minute


def _t(s):
    return datetime.fromisoformat(s).replace(tzinfo=UTC)


def _all_bars(snap):
    return [(tf, b) for tf, series in snap.bars.items() for b in series]


# ---- resampling

def test_resample_ohlc_gaps_and_partial():
    m = lambda hhmm, o, h, l, c: Bar(ts=_t(f"2025-06-11 {hhmm}"), open=o, high=h, low=l, close=c)
    one = (m("12:58", 1, 2, 0.5, 1.5), m("12:59", 1.5, 3, 1, 2),
           m("13:00", 2, 2.5, 1.8, 2.2), m("13:02", 2.2, 4, 2, 3.9),            # 13:01 missing
           m("13:15", 3.9, 4.1, 3.0, 3.1))
    as_of = _t("2025-06-11 13:16")
    q = resample(one, "15m", as_of)
    assert [(b.ts.strftime("%H:%M"), b.open, b.high, b.low, b.close, b.n_1m, b.complete) for b in q] == [
        ("12:45", 1, 3, 0.5, 2, 2, True), ("13:00", 2, 4, 1.8, 3.9, 2, True), ("13:15", 3.9, 4.1, 3.0, 3.1, 1, False)]
    h4 = resample(one, "4h", as_of)
    # 4h buckets open at NY 05:00 / 09:00 EDT = 09:00 / 13:00 UTC in June
    assert [(b.ts.hour, b.n_1m, b.complete) for b in h4] == [(9, 2, True), (13, 3, False)]
    with pytest.raises(ValueError):
        resample(one, "15m", _t("2025-06-11 13:15"))                                 # a bar at as_of


# ---- the "as of t" rule

@pytest.mark.parametrize("t", ["2025-06-10 03:17:59", "2025-06-10 20:59:00", "2025-06-11 00:00:01",
                               "2025-06-11 12:00:00", "2025-06-11 14:30:30", "2025-06-11 23:59:59"])
def test_snapshot_never_contains_a_bar_at_or_after_t(replay_db, t):
    t = _t(t)
    snap = build_snapshot(t, CFG, replay_db)
    assert not isinstance(snap, Stale), snap
    assert snap.as_of == floor_minute(t) <= t
    for tf, b in _all_bars(snap):
        assert b.ts < snap.as_of <= t, (tf, b.ts)
        assert b.complete == (bucket_end(b.ts, tf) <= snap.as_of)


def test_forming_minute_is_excluded(replay_db):
    # The store holds a complete 14:30 bar (replay), but at 14:30:30 it was still forming.
    assert bars_before("XAUUSD", _t("2025-06-11 14:31"), 1, db=replay_db, kinds=("replay",))[0]["bar_ts"] \
        == "2025-06-11T14:30:00+00:00"
    snap = build_snapshot(T, CFG, replay_db)
    assert snap.newest_bar_ts == _t("2025-06-11 14:29")
    assert snap.bars["1m"][-1].ts == _t("2025-06-11 14:29")


def test_partial_buckets_use_only_bars_before_as_of(replay_db):
    snap = build_snapshot(T, CFG, replay_db)
    last_close = snap.bars["1m"][-1].close
    # as_of 14:30: the 15m bucket 14:15-14:30 has just completed; 1h and 4h are still forming
    # (4h opened at 13:00 UTC = 09:00 EDT)
    assert [(snap.bars[tf][-1].ts.strftime("%H:%M"), snap.bars[tf][-1].complete) for tf in ("15m", "1h", "4h")] \
        == [("14:15", True), ("14:00", False), ("13:00", False)]
    assert all(snap.bars[tf][-1].close == last_close for tf in ("15m", "1h", "4h"))
    # the 4h bucket 13:00-17:00 UTC has at most the 90 minutes 13:00..14:29 in it
    assert snap.bars["4h"][-1].ts == _t("2025-06-11 13:00") and snap.bars["4h"][-1].n_1m <= 90


def test_kept_counts(replay_db):
    snap = build_snapshot(T, CFG, replay_db)
    assert {tf: len(s) for tf, s in snap.bars.items()}["1m"] == 240
    assert all(len(snap.bars[tf]) <= n for tf, n in CFG.keep.items())
    assert {b.kind for b in snap.bars["1m"]} == {"replay"} and snap.kinds == ("replay",) and snap.known_by is None


def test_fetch_window_is_aligned_to_largest_timeframe(replay_db):
    # Keep everything: the oldest 4h bucket of a 1-day fetch must equal the same
    # bucket inside a 2-day fetch (not cut at as_of - 1 day = 14:30; its bucket
    # opened at 13:00 UTC = 09:00 EDT).
    keep = {"1m": 10_000, "4h": 1_000}
    one = build_snapshot(T, SnapshotConfig(mode="replay", fetch_lookback=timedelta(days=1), keep=keep), replay_db)
    two = build_snapshot(T, SnapshotConfig(mode="replay", fetch_lookback=timedelta(days=2), keep=keep), replay_db)
    first = one.bars["4h"][0]
    assert first.ts == _t("2025-06-10 13:00")
    assert first == next(b for b in two.bars["4h"] if b.ts == first.ts)
    assert one.bars["4h"] == two.bars["4h"][-len(one.bars["4h"]):]


# ---- hash

def test_same_inputs_give_the_same_hash(replay_db, tmp_path):
    a = build_snapshot(T, CFG, replay_db)
    b = build_snapshot(T, CFG, replay_db)
    c = build_snapshot(T.replace(second=5), CFG, replay_db)                 # same minute -> same as_of
    rebuilt = build_snapshot(T, CFG, make_replay_db(tmp_path))                # store rebuilt from scratch
    assert a.hash == b.hash == c.hash == rebuilt.hash
    assert a == rebuilt
    assert build_snapshot(T + timedelta(minutes=1), CFG, replay_db).hash != a.hash
    assert build_snapshot(T, SnapshotConfig(mode="replay", keep={"1m": 239}), replay_db).hash != a.hash


# ---- stale / no data / sources

def _newest_before(db, t):
    return datetime.fromisoformat(bars_before("XAUUSD", t, 1, db=db, kinds=("replay",))[0]["bar_ts"])


def test_stale_when_the_feed_stops_in_an_open_market(replay_db):
    # synthetic data ends Wed 2025-06-11; Thursday 10:00 UTC the market is open
    out = build_snapshot(_t("2025-06-12 10:00:20"), CFG, replay_db)
    newest = _newest_before(replay_db, _t("2025-06-12 10:00"))
    assert isinstance(out, Stale) and out.newest_bar_ts == newest
    assert out.reason.startswith(f"stale: newest 1m bar {newest.isoformat()} is ")
    assert "behind the last market-open minute 2025-06-12T09:59:00+00:00" in out.reason


def test_staleness_threshold_is_inclusive(replay_db):
    # after the data ends (market open): 5 min behind the last open minute is fresh, 6 is stale
    newest = _newest_before(replay_db, _t("2025-06-12 06:00"))
    assert not isinstance(build_snapshot(newest + timedelta(minutes=6), CFG, replay_db), Stale)
    assert isinstance(build_snapshot(newest + timedelta(minutes=7), CFG, replay_db), Stale)


@pytest.mark.parametrize("t", ["2025-06-10 21:00:10", "2025-06-10 21:30:00", "2025-06-10 21:59:59",
                               "2025-06-10 22:00:00", "2025-06-10 22:00:40"])
def test_daily_break_and_reopen_are_not_stale(replay_db, t):
    # owner decision: closed minutes don't count. In the break, and at the 22:00 UTC
    # reopen, the newest bar is the last one before the close, and that's fresh.
    out = build_snapshot(_t(t), CFG, replay_db)
    assert not isinstance(out, Stale), out
    assert out.newest_bar_ts == _newest_before(replay_db, _t("2025-06-10 21:00"))


def test_reopen_needs_the_new_bars_after_a_few_minutes(replay_db):
    # 22:07: the last open minute is 22:06. If the feed hadn't resumed it would be stale;
    # the synthetic feed did resume.
    out = build_snapshot(_t("2025-06-10 22:07"), CFG, replay_db)
    assert not isinstance(out, Stale) and out.newest_bar_ts >= _t("2025-06-10 22:01")


@pytest.mark.parametrize("as_of, want", [
    ("2025-06-11 14:30", "2025-06-11 14:29"),     # open: one minute back
    ("2025-06-10 21:30", "2025-06-10 20:59"),     # daily break (summer 21:00-22:00 UTC)
    ("2025-06-10 22:00", "2025-06-10 20:59"),     # the reopen minute itself
    ("2025-06-10 22:01", "2025-06-10 22:00"),
    ("2025-01-15 22:30", "2025-01-15 21:59"),     # winter break 22:00-23:00 UTC
    ("2025-06-14 12:00", "2025-06-13 20:59"),     # Saturday -> Friday's close
    ("2025-06-15 22:00", "2025-06-13 20:59"),     # Sunday reopen (18:00 EDT) -> Friday's close
    ("2025-06-15 22:05", "2025-06-15 22:04"),
])
def test_last_open_minute(as_of, want):
    assert snapmod.last_open_minute(_t(as_of)) == _t(want)


def test_no_bars_is_stale(replay_db):
    out = build_snapshot(_t("2025-06-01 12:00"), CFG, replay_db)
    assert isinstance(out, Stale) and out.newest_bar_ts is None and out.reason.startswith("no replay bars")


def test_live_mode_uses_known_by_t():
    assert snapmod.source_for("live", T) == (("live", "history"), T)
    assert snapmod.source_for("replay", T) == (("replay",), None)


def test_naive_t_is_refused(replay_db):
    with pytest.raises(ValueError):
        build_snapshot(T.replace(tzinfo=None), CFG, replay_db)


# ---- seal

def test_seal_has_no_override(replay_db):
    assert snapmod.SEAL_START is xauusd_bars.SEAL_START == datetime(2026, 10, 1, tzinfo=UTC)
    with pytest.raises(SealedError):
        build_snapshot(xauusd_bars.SEAL_START + timedelta(minutes=1), CFG, replay_db)
    # as_of exactly at the seal reads only bars before it: allowed (no data there -> Stale)
    assert isinstance(build_snapshot(xauusd_bars.SEAL_START + timedelta(seconds=59), CFG, replay_db), Stale)


# ---- save before use

def test_prepare_saves_fresh_snapshots_only(replay_db, caplog):
    saved = []

    class Sink:
        def save_snapshot(self, s):
            saved.append(s)

    snap = prepare_snapshot(T, CFG, replay_db, Sink())
    assert saved == [snap]
    with caplog.at_level(logging.WARNING, logger="backend.agents.snapshot"):
        out = prepare_snapshot(_t("2025-06-12 10:00"), CFG, replay_db, Sink())
    assert isinstance(out, Stale) and saved == [snap]
    assert "run skipped" in caplog.text and "stale" in caplog.text


def test_weekend_reopen_is_not_stale(tmp_path):
    from tests.agents.agents_testkit import synthetic_rows
    from backend.agents.config import AgentsConfig
    from backend.agents.graph import build_graph, run_once
    from backend.agents.schemas import TriggerEvent
    from backend.agents.store import MemoryStore
    db = make_replay_db(tmp_path, synthetic_rows(_t("2025-06-13 12:00"), _t("2025-06-16 01:00"), seed=3))
    friday_close = _newest_before(db, _t("2025-06-14 00:00"))
    assert friday_close <= _t("2025-06-13 20:59")
    # Sunday 18:00 EDT = 22:00 UTC: the first trigger of the week runs on Friday's last bar
    out = build_snapshot(_t("2025-06-15 22:00:05"), CFG, db)
    assert not isinstance(out, Stale) and out.newest_bar_ts == friday_close
    cfg = AgentsConfig.model_validate({"snapshot": {"mode": "replay"}})
    state = run_once(build_graph(cfg, MemoryStore(), db),
                     TriggerEvent(source="t", t=_t("2025-06-15 22:00:05"), trigger_id="sun"))
    assert state["status"] == "done"
    # and on Saturday the snapshot is fresh too; the gate stops the run
    sat = run_once(build_graph(cfg, MemoryStore(), db), TriggerEvent(source="t", t=_t("2025-06-14 12:00"),
                                                                      trigger_id="sat"))
    assert sat["skip_reason"] == "gate: market closed at as_of 2025-06-14T12:00:00+00:00"
