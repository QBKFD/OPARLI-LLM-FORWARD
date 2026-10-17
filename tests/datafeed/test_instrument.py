"""datafeed.instrument: presets, overrides, validation, calendars; and the feed,
recorder and entry point using a non-default instrument (fake IB, no gateway)."""

import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from datafeed import instrument as ins
from datafeed.ingest import xauusd_bars as xb

UTC = timezone.utc
ROOT = Path(__file__).resolve().parents[2]
SAT = datetime(2026, 10, 17, 12, 0, tzinfo=UTC)          # Saturday: gold closed, crypto open
TUE = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)


def test_default_is_exactly_the_old_xauusd():
    i = ins.from_env({})
    assert i == ins.XAUUSD
    assert i.contract_fields() == {"symbol": "XAUUSD", "secType": "CMDTY", "exchange": "SMART", "currency": "USD"}
    assert (i.what_to_show, i.stream, i.calendar) == ("MIDPOINT", xb.STREAM, "xauusd")
    assert ins.from_env({"INSTRUMENT": ""}) == ins.XAUUSD


def test_btc_preset_and_overrides():
    b = ins.from_env({"INSTRUMENT": "BTCUSD"})
    assert (b.name, b.ib_symbol, b.sec_type, b.exchange, b.stream, b.calendar) == \
        ("BTCUSD", "BTC", "CRYPTO", "PAXOS", "bars.btcusd.1m", "24/7")
    o = ins.from_env({"INSTRUMENT": "BTCUSD", "INSTRUMENT_WHAT_TO_SHOW": "AGGTRADES",
                      "INSTRUMENT_STREAM": "bars.btcdry.1m"})
    assert (o.what_to_show, o.stream, o.ib_symbol) == ("AGGTRADES", "bars.btcdry.1m", "BTC")


@pytest.mark.parametrize("env, msg", [
    ({"INSTRUMENT": "ETHUSD"}, "INSTRUMENT='ETHUSD'"),
    ({"INSTRUMENT_SEC_TYPE": "OPT"}, "sec_type 'OPT'"),
    ({"INSTRUMENT_WHAT_TO_SHOW": "midpoint"}, "what_to_show 'midpoint'"),
    ({"INSTRUMENT_STREAM": "bars:xau"}, "stream 'bars:xau'"),
    ({"INSTRUMENT_CALENDAR": "weekdays"}, "calendar 'weekdays'"),
    ({"INSTRUMENT_CURRENCY": "usd"}, "currency 'usd'"),
    ({"INSTRUMENT_EXCHANGE": "PAX OS"}, "exchange 'PAX OS'"),
    ({"INSTRUMENT_SYMBOL": "BTC"}, "unknown setting(s) INSTRUMENT_SYMBOL"),
])
def test_invalid_settings_are_startup_errors(env, msg):
    with pytest.raises(ins.InstrumentError, match=msg.replace("(", r"\(").replace(")", r"\)")):
        ins.from_env(env)


def test_calendars():
    assert ins.market_open(TUE, "xauusd") and not ins.market_open(SAT, "xauusd")
    assert ins.market_open(SAT, "24/7") and ins.market_open(TUE, "24/7")
    with pytest.raises(TypeError):
        ins.market_open(SAT.replace(tzinfo=None), "24/7")
    with pytest.raises(ins.InstrumentError):
        ins.market_open(SAT, "lunar")


def test_valid_bar_keeps_xauusd_rules_and_opens_24_7():
    assert xb.valid_bar(SAT, 1, 2, 0.5, 1, "xauusd") is xb._valid_bar(SAT, 1, 2, 0.5, 1) is False
    assert xb.valid_bar(TUE, 1, 2, 0.5, 1) is xb._valid_bar(TUE, 1, 2, 0.5, 1) is True
    assert xb.valid_bar(SAT, 1, 2, 0.5, 1, "24/7") is True
    assert xb.valid_bar(SAT, -1, 2, 0.5, 1, "24/7") is False           # still no "no quote" prices
    assert xb.valid_bar(SAT, 1, 0.4, 0.5, 1, "24/7") is False          # low > high


class Event(list):
    def __iadd__(self, fn):
        self.append(fn)
        return self


class FakeIB:
    def __init__(self, history):
        self.history, self.calls, self.up = history, [], False
        self.pendingTickersEvent = Event()

    def connect(self, host, port, clientId, timeout, readonly):
        self.up = True

    def qualifyContracts(self, c):
        self.calls.append(("qualify", c.symbol, c.secType, c.exchange, c.currency))
        return [SimpleNamespace(conId=9, symbol=c.symbol)]

    def reqHistoricalData(self, contract, **kw):
        self.calls.append(("history", kw["whatToShow"], kw["durationStr"]))
        return self.history

    def reqMktData(self, contract, **kw):
        self.calls.append(("mktdata",))

    def isConnected(self):
        return self.up

    def sleep(self, s):
        pass

    def disconnect(self):
        self.up = False


def test_a_btc_feed_requests_its_contract_and_publishes_weekend_bars():
    from datafeed.ingest.ibkr import IbkrConfig, IbkrConnection
    btc = ins.from_env({"INSTRUMENT": "BTCUSD", "INSTRUMENT_WHAT_TO_SHOW": "AGGTRADES"})
    hist = [SimpleNamespace(date=datetime(2026, 10, 17, 11, m, tzinfo=UTC), open=67000.0, high=67010.0,
                            low=66990.0, close=67005.0) for m in range(3)]
    ib = FakeIB(hist)
    out = []
    src = xb.IbkrXauusdSource(IbkrConnection(IbkrConfig("fake", 4002, 44), btc, ib=ib),
                              clock=lambda: SAT, sleep=lambda s: None, max_loops=1)
    assert src.stream == "bars.btcusd.1m"
    src.run(out.append)                                        # max_loops=1: one live loop, then return
    assert ("qualify", "BTC", "CRYPTO", "PAXOS", "USD") in ib.calls
    assert ("history", "AGGTRADES", "10 D") in ib.calls
    assert [m["symbol"] for m in out] == ["BTCUSD"] * 3 and src.stats["invalid_bars"] == 0
    assert all(m["history"] is True for m in out)


def test_the_default_feed_is_unchanged():
    from datafeed.ingest.ibkr import IbkrConfig, IbkrConnection
    ib = FakeIB([])
    src = xb.IbkrXauusdSource(IbkrConnection(IbkrConfig("fake", 4002, 31), ib=ib), clock=lambda: TUE,
                              sleep=lambda s: None, max_loops=1)
    assert (src.stream, src.calendar, src.conn.symbol) == (xb.STREAM, "xauusd", "XAUUSD")
    src.run(lambda m: None)
    assert ("qualify", "XAUUSD", "CMDTY", "SMART", "USD") in ib.calls and ("history", "MIDPOINT", "10 D") in ib.calls


def _entry(env):
    return subprocess.run([sys.executable, "-m", "datafeed.ingest", "--stdout"], capture_output=True, text=True,
                          env={"PATH": "/usr/bin:/bin", **env}, cwd=ROOT, timeout=60)


def test_replay_is_xauusd_only_and_bad_settings_stop_the_entry_point():
    r = _entry({"DATAFEED_SOURCE": "replay", "INSTRUMENT": "BTCUSD"})
    assert r.returncode != 0 and "XAUUSD bars only, not BTCUSD" in r.stderr
    r = _entry({"DATAFEED_SOURCE": "ibkr", "INSTRUMENT_CALENDAR": "weekdays"})
    assert r.returncode != 0 and "instrument: calendar 'weekdays'" in r.stderr


def test_recorder_reads_its_stream_from_the_instrument(monkeypatch, tmp_path):
    from datafeed.recorder import __main__ as rm
    seen = {}

    class StopRecorder(Exception):
        pass

    class FakeRecorder:
        def __init__(self, bus, con, stream, consumer):
            seen["stream"] = stream
            raise StopRecorder

    monkeypatch.setattr(rm, "Recorder", FakeRecorder)
    monkeypatch.setattr(rm, "RedisStreamBus", lambda: None)
    monkeypatch.setenv("DATAFEED_DB", str(tmp_path / "s.sqlite"))
    monkeypatch.setenv("INSTRUMENT", "BTCUSD")
    with pytest.raises(StopRecorder):
        rm.main()
    assert seen["stream"] == "bars.btcusd.1m"
    monkeypatch.setenv("INSTRUMENT", "nope")
    with pytest.raises(SystemExit, match="instrument: INSTRUMENT='nope'"):
        rm.main()
