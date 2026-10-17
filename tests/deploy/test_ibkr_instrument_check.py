"""tools/ibkr_instrument_check.py against a fake gateway: the paper-only guards,
what it reports, and the verdict. No network, no ib_insync connection."""

import importlib.util
import math
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

TOOL = Path(__file__).resolve().parents[2] / "tools" / "ibkr_instrument_check.py"
spec = importlib.util.spec_from_file_location("ibkr_instrument_check", TOOL)
ic = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ic)


class Event(list):
    def __iadd__(self, fn):
        self.append(fn)
        return self

    def emit(self, *a):
        for fn in self:
            fn(*a)


class FakeIB:
    """Scripted gateway: contract details, bars per whatToShow, ticks, errors."""

    def __init__(self, accounts=("DU123",), details=True, bars=None, ticks=3, md_type=1, hist_errors=None):
        self.errorEvent = Event()
        self.accounts, self.details, self.bars = accounts, details, bars or {}
        self.ticks, self.md_type, self.hist_errors = ticks, md_type, hist_errors or {}
        self.calls, self.connected = [], False

    def connect(self, host, port, clientId, timeout, readonly):
        self.calls.append(("connect", host, port, clientId, readonly))
        self.connected = True

    def disconnect(self):
        self.calls.append(("disconnect",))

    def managedAccounts(self):
        return list(self.accounts)

    def sleep(self, s):                      # the gateway delivers ticks while the tool waits
        tk = getattr(self, "ticker", None)
        if tk is not None and not getattr(self, "delivered", False):
            self.delivered = True
            for _ in range(self.ticks):
                tk.updateEvent.emit(tk)

    def reqContractDetails(self, c):
        self.calls.append(("details", c.symbol, c.secType, c.exchange, c.currency))
        if not self.details:
            self.errorEvent.emit(1, 200, "No security definition has been found", c)
            return []
        contract = SimpleNamespace(conId=479624278, localSymbol="BTC.USD", symbol=c.symbol)
        return [SimpleNamespace(contract=contract, longName="Bitcoin", marketName="BTC", minTick=0.25,
                                timeZoneId="US/Central", tradingHours="20261017:0000-20261017:2359;x;y;z",
                                validExchanges="PAXOS", orderTypes="LMT,MKT")]

    def reqHistoricalData(self, c, endDateTime, durationStr, barSizeSetting, whatToShow, useRTH,
                          formatDate, timeout):
        self.calls.append(("hist", whatToShow, durationStr, useRTH))
        if whatToShow in self.hist_errors:
            self.errorEvent.emit(2, *self.hist_errors[whatToShow], c)
            return []
        n = self.bars.get(whatToShow, 0)
        t = datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc)
        return [SimpleNamespace(date=t, close=67000.0 + i) for i in range(n)]

    def reqMktData(self, c, generic, snapshot, regulatory):
        self.calls.append(("mkt",))
        tk = SimpleNamespace(updateEvent=Event(), bid=67000.0, ask=67001.0, last=math.nan,
                             marketDataType=self.md_type, time=datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc))
        self.ticker = tk
        return tk

    def cancelMktData(self, c):
        self.calls.append(("cancel",))


def _contract(**kw):
    return SimpleNamespace(**kw)


def _run(ib, port=4004, **kw):
    args = dict(host="ibgateway", port=port, client_id=43, symbol="BTC", sec_type="CRYPTO", exchange="PAXOS",
                currency="USD", ticks_s=0.01)
    args.update(kw)
    return ic.run(**args, ib_factory=lambda: ib, contract_factory=_contract)


def test_a_non_paper_port_is_refused_before_connecting():
    ib = FakeIB()
    with pytest.raises(ic.NotPaper):
        _run(ib, port=4003)                                       # the live relay
    assert ib.calls == []


def test_a_live_account_is_refused_before_any_request():
    ib = FakeIB(accounts=("U1234567",))
    with pytest.raises(ic.NotPaper):
        _run(ib)
    assert [c[0] for c in ib.calls] == ["connect", "disconnect"]


def test_available_instrument_full_report():
    ib = FakeIB(bars={"TRADES": 30, "MIDPOINT": 30, "AGGTRADES": 30, "BID_ASK": 30},
                hist_errors={"BID": (162, "Historical Market Data Service error message:No data")})
    r = _run(ib)
    assert ib.calls[0] == ("connect", "ibgateway", 4004, 43, True) and ib.calls[-1] == ("disconnect",)
    assert ("details", "BTC", "CRYPTO", "PAXOS", "USD") in ib.calls
    assert [c[1] for c in ib.calls if c[0] == "hist"] == list(ic.WHAT_TO_SHOW)
    assert all(c[2] == "1800 S" and c[3] is False for c in ib.calls if c[0] == "hist")
    assert ("cancel",) in ib.calls
    assert r["contract"]["conId"] == 479624278 and r["contract"]["tradingHours"][0].startswith("20261017")
    assert r["historical"]["TRADES"]["bars"] == 30 and r["historical"]["ASK"] == {"bars": 0}
    assert r["historical"]["BID"]["errors"] == ["162: Historical Market Data Service error message:No data"]
    assert r["ticks"]["updates_with_price"] == 3 and r["ticks"]["market_data_type"] == 1
    assert r["errors"][0]["entitlement"] == ic.ENTITLEMENT_CODES[162]
    assert r["verdict"] == "available"


def test_no_contract():
    r = _run(FakeIB(details=False))
    assert r["verdict"] == "no contract" and r["historical"] == {} and r["errors"][0]["code"] == 200


def test_contract_but_no_market_data():
    r = _run(FakeIB(ticks=0, hist_errors={w: (354, "Requested market data is not subscribed.")
                                          for w in ic.WHAT_TO_SHOW}))
    assert r["verdict"] == "no market data"
    assert {e["entitlement"] for e in r["errors"]} == {ic.ENTITLEMENT_CODES[354]}


def test_delayed_data_only_is_partial():
    assert _run(FakeIB(bars={"TRADES": 30}, md_type=3))["verdict"] == "partial"


def test_info_messages_are_not_errors():
    ib = FakeIB(bars={"TRADES": 30})
    ib.errorEvent.emit(-1, 2104, "Market data farm connection is OK:usfarm", None)
    assert ic.verdict({"found": True}, {"TRADES": {"bars": 1}}, {"updates_with_price": 1, "market_data_type": 1}) \
        == "available"
    assert _run(ib)["errors"] == []


def test_main_exit_codes(capsys, monkeypatch):
    assert ic.main(["--port", "4003"]) == 3 and "refused" in capsys.readouterr().err

    def no_gateway(*a, **k):
        raise ConnectionRefusedError("connection refused")

    monkeypatch.setattr(ic, "run", no_gateway)
    assert ic.main(["--port", "4004"]) == 4 and "no connection" in capsys.readouterr().err
