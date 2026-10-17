"""The scanner with a non-default instrument: the calendar switch (24/7), the
stream consumer for another symbol, the guards (ibkr mode and replay are XAUUSD
only) and the Telegram prefix. XAUUSD behaviour is covered by the existing
tests and the selftest, unchanged."""

from datetime import datetime, timezone

import pandas as pd
import pytest

import deterministic_scanner as ds
from datafeed.bus import InMemoryBus
from datafeed.ingest.xauusd_bars import Bar, bar_message
from datafeed.instrument import BTCUSD, XAUUSD

from scanner_helpers import FakeLive, Log

SAT = pd.Timestamp("2026-10-17 12:00", tz="UTC")           # Saturday: gold closed
T = lambda s: pd.Timestamp(s, tz="UTC")                     # noqa: E731


@pytest.fixture
def calendar_247(monkeypatch):
    monkeypatch.setattr(ds, "CALENDAR", "24/7")


def test_the_default_calendar_is_gold():
    assert ds.CALENDAR == "xauusd"
    assert ds._market_open(SAT) is False and ds._valid_bar(SAT, 1, 2, 0.5, 1) is False


def test_configure_instrument_sets_the_calendar(monkeypatch):
    monkeypatch.setattr(ds, "CALENDAR", "xauusd")
    assert ds.configure_instrument({}) == XAUUSD and ds.CALENDAR == "xauusd"
    assert ds.configure_instrument({"INSTRUMENT": "BTCUSD"}) == BTCUSD and ds.CALENDAR == "24/7"
    with pytest.raises(SystemExit, match="instrument: INSTRUMENT='DOGE'"):
        ds.configure_instrument({"INSTRUMENT": "DOGE"})


def test_24_7_is_always_open(calendar_247):
    assert ds._market_open(SAT) and ds._valid_bar(SAT, 1, 2, 0.5, 1)
    assert not ds._valid_bar(SAT, -1, 2, 0.5, 1)               # prices still checked
    assert len(ds._open_minutes(T("2026-10-17 00:00"), T("2026-10-18 00:00"))) == 1440
    assert len(ds._open_minutes(T("2026-10-17 00:00"), T("2026-10-17 00:00"))) == 0


def test_gold_open_minutes_unchanged():
    assert len(ds._open_minutes(T("2026-10-17 00:00"), T("2026-10-18 00:00"))) == 0       # Saturday
    assert len(ds._open_minutes(T("2026-10-13 00:00"), T("2026-10-14 00:00"))) == 1380    # Tuesday


def _btc(ts, p=67000.0, symbol="BTCUSD"):
    t = datetime.fromisoformat(ts).replace(tzinfo=timezone.utc)
    return bar_message(Bar(symbol, t, p, p + 10, p - 10, p + 5, 12), source="ibkr", history=False)


def test_the_stream_consumer_takes_its_own_symbol_on_a_weekend(calendar_247):
    bus, live = InMemoryBus(), FakeLive()
    bus.publish(BTCUSD.stream, _btc("2026-10-17T12:00:00"))
    bus.publish(BTCUSD.stream, _btc("2026-10-17T12:01:00", symbol="XAUUSD"))      # wrong symbol
    bus.publish(BTCUSD.stream, _btc("2026-10-17T12:02:00"))
    feed = ds.StreamFeed(bus, BTCUSD.stream, "scanner-t", live, lambda a: None, symbol="BTCUSD",
                         block_ms=None, log=Log())
    while feed.step():
        pass
    assert [b[0][11:16] for b in live.bars] == ["12:00", "12:02"]
    assert feed.stats["live_bars"] == 2 and feed.stats["invalid_msg"] == 1 and feed.stats["invalid_bars"] == 0


def test_the_same_weekend_bar_is_dropped_on_the_gold_calendar():
    bus, live = InMemoryBus(), FakeLive()
    bus.publish(BTCUSD.stream, _btc("2026-10-17T12:00:00"))
    feed = ds.StreamFeed(bus, BTCUSD.stream, "scanner-t", live, lambda a: None, symbol="BTCUSD",
                         block_ms=None, log=Log())
    feed.step()
    assert live.bars == [] and feed.stats["invalid_bars"] == 1


def test_the_ibkr_bar_source_refuses_another_instrument():
    with pytest.raises(SystemExit, match="ibkr bar source is XAUUSD only, not BTCUSD"):
        ds.run_live(dry=True, instrument=BTCUSD)


def test_run_live_stream_takes_symbol_and_stream_from_the_instrument():
    import inspect
    src = inspect.getsource(ds.run_live_stream)
    assert "symbol, stream = instrument.name, instrument.stream" in src
    assert "progress=sink.progress, symbol=symbol" in src and "STREAM, consumer" not in src


@pytest.mark.parametrize("argv", [["--date", "2025-06-16"], ["--selftest"]])
def test_replay_and_selftest_refuse_another_instrument(monkeypatch, argv):
    monkeypatch.setenv("INSTRUMENT", "BTCUSD")
    monkeypatch.setattr("sys.argv", ["deterministic_scanner.py", *argv])
    with pytest.raises(SystemExit) as e:
        ds.main()
    assert e.value.code == 2


def test_live_passes_the_instrument_on(monkeypatch):
    got = {}
    monkeypatch.setattr(ds, "run_live_stream", lambda dry=False, instrument=None: got.update(i=instrument))
    monkeypatch.setattr(ds, "CALENDAR", "xauusd")
    monkeypatch.setenv("SCANNER_BAR_SOURCE", "stream")
    monkeypatch.setenv("INSTRUMENT", "BTCUSD")
    monkeypatch.setattr("sys.argv", ["deterministic_scanner.py", "--dry-live"])
    ds.main()
    assert got["i"] == BTCUSD and ds.CALENDAR == "24/7"


@pytest.mark.parametrize("prefix, want", [("", "hello"), ("[DRYRUN]", "[DRYRUN] hello"),
                                          ("  [DRYRUN]  ", "[DRYRUN] hello")])
def test_telegram_prefix(monkeypatch, prefix, want):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    monkeypatch.setenv("TELEGRAM_PREFIX", prefix)
    tg = ds.Telegram()
    sent = []
    monkeypatch.setattr(tg, "_get", lambda method, params: sent.append((method, params["text"])))   # no network
    tg.send("hello")
    assert sent == [("sendMessage", want)]
