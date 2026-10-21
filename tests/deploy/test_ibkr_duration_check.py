"""tools/ibkr_duration_check.py: the verdict logic on synthetic bars, and the
paper-only guards (no gateway involved)."""

import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.datafeed.ingest.xauusd_bars import _market_open

TOOL = Path(__file__).resolve().parents[2] / "tools" / "ibkr_duration_check.py"
spec = importlib.util.spec_from_file_location("ibkr_duration_check", TOOL)
dc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dc)

NOW = datetime(2025, 7, 3, 14, 0, tzinfo=timezone.utc)      # Thursday


def _minutes(start, end):
    t, out = start.replace(second=0, microsecond=0), []
    while t < end:
        if _market_open(t):
            out.append(t)
        t += timedelta(minutes=1)
    return out


def test_calendar_days():
    bars = _minutes(NOW - timedelta(days=10), NOW)
    r = dc.analyse(bars, NOW, 10)
    assert r["verdict"] == "calendar days" and r["sessions"] < 10
    assert not r["older_than_requested_calendar_days"]


def test_trading_days():
    bars, seen, t = [], set(), NOW
    while True:                                                    # 10 full sessions + the current one
        t -= timedelta(minutes=1)
        if _market_open(t):
            if dc.session_of(t) not in seen and len(seen) == 11:
                break
            seen.add(dc.session_of(t))
            bars.append(t)
    r = dc.analyse(bars, NOW, 10)
    assert r["verdict"] == "trading days" and r["sessions"] == 11
    assert r["calendar_span_days"] > 13


def test_inconclusive():
    assert dc.analyse(_minutes(NOW - timedelta(days=3), NOW), NOW, 10)["verdict"] == "calendar days"
    assert dc.analyse(_minutes(NOW - timedelta(days=30), NOW), NOW, 10)["verdict"] == "inconclusive"
    assert dc.analyse([], NOW, 10)["verdict"] == "inconclusive"


def test_session_rolls_over_at_17_ny():
    # 2025-07-03 summer: 17:00 NY = 21:00 UTC
    assert dc.session_of(datetime(2025, 7, 3, 20, 59, tzinfo=timezone.utc)).isoformat() == "2025-07-03"
    assert dc.session_of(datetime(2025, 7, 3, 22, 0, tzinfo=timezone.utc)).isoformat() == "2025-07-04"


class FakeIB:
    def __init__(self, accounts):
        self.accounts, self.calls = accounts, []

    def connect(self, host, port, clientId, timeout, readonly):
        self.calls.append(("connect", port, clientId, readonly))

    def managedAccounts(self):
        return self.accounts

    def qualifyContracts(self, c):
        self.calls.append(("qualify",))
        return [c]

    def reqHistoricalData(self, contract, **kw):
        self.calls.append(("history", kw))
        return [SimpleNamespace(date=t) for t in _minutes(NOW - timedelta(days=10), NOW)]

    def disconnect(self):
        self.calls.append(("disconnect",))


@pytest.mark.parametrize("port", [4001, 4003, 7496, 4000])
def test_live_ports_are_refused_before_connecting(port):
    ib = FakeIB(["DU123"])
    with pytest.raises(dc.NotPaper, match="not a paper port"):
        dc.run("h", port, 41, 10, ib_factory=lambda: ib, now=NOW)
    assert ib.calls == []


@pytest.mark.parametrize("accounts", [["U123"], ["DU1", "U2"], []])
def test_live_accounts_are_refused_before_any_request(accounts):
    ib = FakeIB(accounts)
    with pytest.raises(dc.NotPaper, match="not all paper"):
        dc.run("h", 4002, 41, 10, ib_factory=lambda: ib, now=NOW)
    assert [c[0] for c in ib.calls] == ["connect", "disconnect"]          # no data requested


def test_paper_run_makes_the_scanners_exact_request():
    ib = FakeIB(["DU123"])
    r = dc.run("h", 4004, 41, 10, ib_factory=lambda: ib, now=NOW)
    assert ib.calls[0] == ("connect", 4004, 41, True)
    hist = [c for c in ib.calls if c[0] == "history"]
    assert len(hist) == 1 and hist[0][1] == dict(endDateTime="", durationStr="10 D", barSizeSetting="1 min",
                                                 whatToShow="MIDPOINT", useRTH=False, formatDate=2)
    assert r["verdict"] == "calendar days" and ib.calls[-1] == ("disconnect",)


def test_cli_refusal_exit_code(capsys):
    assert dc.main(["--port", "4001"]) == 3
    assert "paper gateway only" in capsys.readouterr().err
