"""Step 1: SCANNER_BAR_SOURCE switch and the stream consumer."""

import pytest

import deterministic_scanner as ds
from datafeed.ingest.xauusd_bars import STREAM

from scanner_helpers import FakeLive, bar_msg, make_bus

BUSES = ["memory", pytest.param("redis", marks=pytest.mark.redis)]


def test_switch_defaults_to_ibkr():
    assert ds.bar_source({}) == "ibkr"
    assert ds.bar_source({"SCANNER_BAR_SOURCE": "ibkr"}) == "ibkr"
    assert ds.bar_source({"SCANNER_BAR_SOURCE": "stream"}) == "stream"


@pytest.mark.parametrize("bad", ["", "IBKR", "redis", "stream "])
def test_switch_rejects_anything_else(bad):
    with pytest.raises(SystemExit, match="SCANNER_BAR_SOURCE"):
        ds.bar_source({"SCANNER_BAR_SOURCE": bad})


@pytest.mark.parametrize("env, want", [({}, "run_live"),
                                       ({"SCANNER_BAR_SOURCE": "ibkr"}, "run_live"),
                                       ({"SCANNER_BAR_SOURCE": "stream"}, "run_live_stream")])
@pytest.mark.parametrize("flag", ["--live", "--dry-live"])
def test_main_dispatches_on_the_switch(monkeypatch, env, want, flag):
    called = []
    monkeypatch.setattr(ds, "run_live", lambda dry=False, **kw: called.append(("run_live", dry)))
    monkeypatch.setattr(ds, "run_live_stream", lambda dry=False, **kw: called.append(("run_live_stream", dry)))
    monkeypatch.delenv("SCANNER_BAR_SOURCE", raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr("sys.argv", ["deterministic_scanner.py", flag])
    ds.main()
    assert called == [(want, flag == "--dry-live")]


@pytest.mark.parametrize("kind", BUSES)
def test_live_bars_drive_on_bar_and_are_acked(kind):
    bus = make_bus(kind)
    for m in (bar_msg("2025-06-16T10:00:00", 3400, 3401, 3399, 3400.5),
              bar_msg("2025-06-16T10:01:00", 3400.5, 3402, 3400, 3401)):
        bus.publish(STREAM, m)
    alert = {"alert_id": "A20250616-001"}
    live, got = FakeLive({"2025-06-16T10:01:00+00:00": [alert]}), []
    feed = ds.StreamFeed(bus, STREAM, "scanner-t", live, got.append, block_ms=None)
    assert feed.step() == 2
    assert [b[0] for b in live.bars] == ["2025-06-16T10:00:00+00:00", "2025-06-16T10:01:00+00:00"]
    assert live.bars[0][1:] == (3400.0, 3401.0, 3399.0, 3400.5)
    assert got == [alert]
    # everything acked: the same consumer gets nothing back
    assert feed.step() == 0
    assert bus.consume(STREAM, ds.STREAM_GROUP, "scanner-t") == []


@pytest.mark.parametrize("kind", BUSES)
def test_group_is_scanner_and_independent_of_the_recorder(kind):
    bus = make_bus(kind)
    bus.publish(STREAM, bar_msg("2025-06-16T10:00:00", 3400, 3401, 3399, 3400.5))
    feed = ds.StreamFeed(bus, STREAM, "scanner-t", FakeLive(), lambda a: None, block_ms=None)
    assert ds.STREAM_GROUP == "scanner"
    assert feed.step() == 1
    # the recorder's group still sees the entry
    assert len(bus.consume(STREAM, "recorder", "recorder-1")) == 1


@pytest.mark.parametrize("kind", BUSES)
def test_separate_groups_each_get_every_entry_one_group_splits(kind):
    """Why a parallel dry run needs its own SCANNER_STREAM_GROUP, not just
    another consumer name: consumers of one group share the entries."""
    bus = make_bus(kind)
    for t in ("10:00", "10:01", "10:02", "10:03"):
        bus.publish(STREAM, bar_msg(f"2025-06-16T{t}:00", 3400, 3401, 3399, 3400.5))
    prod, dry = FakeLive(), FakeLive()
    ds.StreamFeed(bus, STREAM, "scanner-1", prod, lambda a: None, block_ms=None, count=2).step()
    ds.StreamFeed(bus, STREAM, "scanner-dry", dry, lambda a: None, block_ms=None, count=2,
                  group="scanner-dry").step()
    ds.StreamFeed(bus, STREAM, "scanner-1", prod, lambda a: None, block_ms=None, count=2).step()
    ds.StreamFeed(bus, STREAM, "scanner-dry", dry, lambda a: None, block_ms=None, count=2,
                  group="scanner-dry").step()
    assert len(prod.bars) == 4 and len(dry.bars) == 4          # separate groups: all entries each
    shared_a, shared_b = FakeLive(), FakeLive()
    bus2 = make_bus(kind)
    for t in ("10:00", "10:01", "10:02", "10:03"):
        bus2.publish(STREAM, bar_msg(f"2025-06-16T{t}:00", 3400, 3401, 3399, 3400.5))
    ds.StreamFeed(bus2, STREAM, "scanner-1", shared_a, lambda a: None, block_ms=None, count=2).step()
    ds.StreamFeed(bus2, STREAM, "scanner-dry", shared_b, lambda a: None, block_ms=None, count=2).step()
    assert len(shared_a.bars) == 2 and len(shared_b.bars) == 2   # same group: split


def test_stream_group_comes_from_the_env():
    import inspect
    src = inspect.getsource(ds.run_live_stream)
    assert 'env.get("SCANNER_STREAM_GROUP", STREAM_GROUP)' in src and "group=group" in src
