"""Step 3: what the stream consumer does with each entry (live only, dedupe,
ordering, gaps), ack-after-processing, and resuming after a restart."""

import pandas as pd
import pytest

import deterministic_scanner as ds
from datafeed.ingest.xauusd_bars import STREAM

from scanner_helpers import FakeLive, Log, bar_msg, make_bus

BUSES = ["memory", pytest.param("redis", marks=pytest.mark.redis)]
T = lambda s: pd.Timestamp(s, tz="UTC")   # noqa: E731


def _bar(ts, p=3400.0, **kw):
    return bar_msg(ts, p, p + 1, p - 1, p + 0.5, **kw)


def _feed(bus, live=None, **kw):
    kw.setdefault("block_ms", None)
    kw.setdefault("log", Log())
    return ds.StreamFeed(bus, STREAM, "scanner-t", live or FakeLive(), kw.pop("on_alert", lambda a: None), **kw)


def _drain(feed):
    while feed.step():
        pass
    return feed


def _ts(live):
    return [b[0][11:16] for b in live.bars]


@pytest.mark.parametrize("kind", BUSES)
def test_history_flagged_entries_never_drive_on_bar(kind):
    bus = make_bus(kind)
    bus.publish(STREAM, _bar("2025-06-16T10:00:00"))
    bus.publish(STREAM, _bar("2025-06-16T10:05:00", history=True))   # newer, but history
    bus.publish(STREAM, _bar("2025-06-16T10:01:00"))
    live = FakeLive()
    f = _drain(_feed(bus, live))
    assert _ts(live) == ["10:00", "10:01"]
    assert f.stats["history_skipped"] == 1 and f.stats["live_bars"] == 2


@pytest.mark.parametrize("kind", BUSES)
def test_only_accepted_sources(kind):
    bus = make_bus(kind)
    bus.publish(STREAM, _bar("2025-06-16T10:00:00", source="replay"))
    bus.publish(STREAM, _bar("2025-06-16T10:01:00", source="ibkr"))
    live = FakeLive()
    f = _drain(_feed(bus, live))
    assert _ts(live) == ["10:01"] and f.stats["wrong_source"] == 1
    assert f.log.having("source 'replay' ignored")
    bus2 = make_bus(kind)
    bus2.publish(STREAM, _bar("2025-06-16T10:00:00", source="replay"))
    live2 = FakeLive()
    _drain(_feed(bus2, live2, sources=("replay",)))
    assert _ts(live2) == ["10:00"]


@pytest.mark.parametrize("kind", BUSES)
def test_invalid_entries_are_acked_and_skipped(kind):
    bus = make_bus(kind)
    good = _bar("2025-06-16T10:00:00")
    for bad in ({**good, "schema_version": 2}, {**good, "symbol": "XAGUSD"},
                {k: v for k, v in good.items() if k != "close"}, {**good, "history": "no"},
                {**good, "bar_ts": "2025-06-16T10:00:00"}, {**good, "open": "x"}):
        bus.publish(STREAM, bad)
    bus.publish(STREAM, _bar("2025-06-16T10:01:00", p=-1.0))          # -1 "no quote"
    bus.publish(STREAM, _bar("2025-06-14T10:01:00"))                  # Saturday
    bus.publish(STREAM, good)
    live = FakeLive()
    f = _drain(_feed(bus, live))
    assert f.stats["invalid_msg"] == 6 and f.stats["invalid_bars"] == 2
    assert _ts(live) == ["10:00"]
    assert bus.consume(STREAM, ds.STREAM_GROUP, "scanner-t") == []    # all acked


@pytest.mark.parametrize("kind", BUSES)
def test_dedupe_by_bar_ts(kind):
    bus = make_bus(kind)
    bus.publish(STREAM, _bar("2025-06-16T10:00:00"))
    bus.publish(STREAM, _bar("2025-06-16T10:01:00"))
    bus.publish(STREAM, _bar("2025-06-16T10:01:00"))                  # exact duplicate
    bus.publish(STREAM, _bar("2025-06-16T10:01:00", p=3401.0))        # same minute, other values
    bus.publish(STREAM, _bar("2025-06-16T10:02:00"))
    live = FakeLive()
    f = _drain(_feed(bus, live))
    assert _ts(live) == ["10:00", "10:01", "10:02"]
    assert live.hist.loc[T("2025-06-16 10:01"), "close"] == 3400.5     # first one kept
    assert f.stats["duplicate"] == 1 and f.stats["conflict"] == 1
    assert f.log.having("conflict entry")


@pytest.mark.parametrize("kind", BUSES)
def test_out_of_order_bar_is_dropped_and_logged(kind):
    bus = make_bus(kind)
    for t in ("10:00", "10:02", "10:01", "10:03"):
        bus.publish(STREAM, _bar(f"2025-06-16T{t}:00"))
    live = FakeLive()
    f = _drain(_feed(bus, live))
    assert _ts(live) == ["10:00", "10:02", "10:03"]
    assert f.stats["out_of_order"] == 1
    assert f.log.having("out_of_order entry") and f.log.having("10:01:00+00:00")


@pytest.mark.parametrize("kind", BUSES)
@pytest.mark.parametrize("seq, want", [
    (("2025-06-16T10:00", "2025-06-16T10:05"), [4]),               # 10:01..10:04 missing
    (("2025-06-16T10:00", "2025-06-16T10:01"), []),                # consecutive
    (("2025-06-16T20:59", "2025-06-16T22:00"), []),                # summer daily break
    (("2025-01-13T21:59", "2025-01-13T23:00"), []),                # winter daily break
    (("2025-06-20T20:59", "2025-06-22T22:00"), []),                # weekend
    (("2025-06-20T20:59", "2025-06-22T22:03"), [3]),               # weekend, then 22:00..22:02
    (("2025-06-16T20:58", "2025-06-16T22:01"), [2]),               # 20:59 and 22:00
])
def test_gaps_count_market_open_minutes_only(kind, seq, want):
    bus = make_bus(kind)
    for t in seq:
        bus.publish(STREAM, _bar(t + ":00"))
    f = _drain(_feed(bus))
    gaps = f.log.having("gap of")
    assert [int(g.split("gap of ")[1].split(" ")[0]) for g in gaps] == want
    assert f.stats["gaps"] == len(want) and f.stats["gap_minutes"] == sum(want)
    assert f.stats["live_bars"] == 2


@pytest.mark.parametrize("kind", BUSES)
def test_gap_after_warmup_is_logged(kind):
    bus = make_bus(kind)
    bus.publish(STREAM, _bar("2025-06-16T10:03:00"))
    f = _drain(_feed(bus, last_bar_ts=T("2025-06-16 10:00")))
    assert f.stats["gap_minutes"] == 2


def _warm_live(minutes, p=3400.0):
    hist = pd.DataFrame({"open": p, "high": p + 1, "low": p - 1, "close": p + 0.5},
                        index=pd.DatetimeIndex([T(m) for m in minutes]))
    return FakeLive(hist=hist)


@pytest.mark.parametrize("kind", BUSES)
def test_backlog_of_a_new_group_is_skipped_quietly(kind):
    """A new group starts at the beginning of the stream: entries the warm-up
    already covers are duplicates, older ones are before_warmup; neither
    reaches on_bar and neither gets a log line of its own."""
    bus = make_bus(kind)
    for t in ("09:50", "09:58", "09:59", "10:00", "10:01"):
        bus.publish(STREAM, _bar(f"2025-06-16T{t}:00"))
    live = _warm_live(["2025-06-16 09:58", "2025-06-16 09:59", "2025-06-16 10:00"])
    f = _drain(_feed(bus, live, last_bar_ts=T("2025-06-16 10:00")))
    assert _ts(live) == ["10:01"]
    assert f.stats["before_warmup"] == 1 and f.stats["duplicate"] == 3
    assert not f.log.having("entry ")                    # no per-entry lines
    assert f.log.having("1 before_warmup, 3 duplicate")  # one batch summary


@pytest.mark.parametrize("kind", BUSES)
def test_backlog_bar_missing_from_warmup_is_not_fed(kind):
    bus = make_bus(kind)
    bus.publish(STREAM, _bar("2025-06-16T09:59:00"))      # not in the warm-up, but older
    live = _warm_live(["2025-06-16 09:58", "2025-06-16 10:00"])
    f = _drain(_feed(bus, live, last_bar_ts=T("2025-06-16 10:00")))
    assert live.bars == [] and f.stats["out_of_order"] == 1


class Crash(Exception):
    pass


@pytest.mark.parametrize("kind", BUSES)
def test_ack_only_after_processing_and_redelivery_on_restart(kind):
    bus = make_bus(kind)
    for t in ("10:00", "10:01", "10:02"):
        bus.publish(STREAM, _bar(f"2025-06-16T{t}:00"))
    alert = {"alert_id": "A20250616-001"}
    sent = []

    def flaky(a):
        if not sent:
            sent.append("crash")
            raise Crash
        sent.append(a)

    live = FakeLive({"2025-06-16T10:01:00+00:00": [alert]})
    f = _feed(bus, live, on_alert=flaky)
    with pytest.raises(Crash):
        f.step()
    assert _ts(live) == ["10:00", "10:01"]               # 10:01 reached on_bar, its alert failed
    # restart: same consumer name gets its unacked entries back (10:01, 10:02),
    # 10:00 was acked. The new warm-up ends at 10:00 (the store had it).
    live2 = _warm_live(["2025-06-16 10:00"])
    live2.alerts_at = {"2025-06-16T10:01:00+00:00": [alert]}
    f2 = _drain(_feed(bus, live2, on_alert=flaky, last_bar_ts=T("2025-06-16 10:00")))
    assert _ts(live2) == ["10:01", "10:02"]
    assert sent == ["crash", alert]
    assert f2.stats["live_bars"] == 2 and f2.stats["entries"] == 2
    assert bus.consume(STREAM, ds.STREAM_GROUP, "scanner-t") == []


@pytest.mark.parametrize("kind", BUSES)
def test_restart_when_the_warmup_already_has_the_unacked_bars(kind):
    bus = make_bus(kind)
    for t in ("10:00", "10:01", "10:02"):
        bus.publish(STREAM, _bar(f"2025-06-16T{t}:00"))
    assert len(bus.consume(STREAM, ds.STREAM_GROUP, "scanner-t")) == 3   # delivered, crash, no ack
    bus.publish(STREAM, _bar("2025-06-16T10:03:00"))
    live = _warm_live(["2025-06-16 10:00", "2025-06-16 10:01", "2025-06-16 10:02"])
    f = _drain(_feed(bus, live, last_bar_ts=T("2025-06-16 10:02")))
    assert _ts(live) == ["10:03"] and f.stats["duplicate"] == 3


def test_journal_insert_is_idempotent(tmp_path):
    con = ds.open_log(tmp_path / "j.sqlite")
    a = dict(alert_id="A20250616-001", alert_ts_utc="2025-06-16 10:01:00", level_types="PDH",
             level_price=3401.0, price_at_alert=3400.5, session="ASIA", confluence_count=1)
    assert ds._journal_new_alert(con, a) is True
    assert ds._journal_new_alert(con, a) is False
    assert ds._journal_new_alert(con, {**a, "alert_ts_utc": "2025-06-16 10:16:00"}) is True
    assert con.execute("SELECT COUNT(*) FROM alerts").fetchone()[0] == 2
