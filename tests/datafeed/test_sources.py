"""Step 4: IBKR source (against a fake IB, no gateway), replay source, entry point.

The only test that talks to a real gateway is test_live_paper_gateway_manual,
skipped unless DATAFEED_IBKR_LIVE=1 (and then only ever against a local paper
gateway).
"""

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

pd = pytest.importorskip("pandas")
pytest.importorskip("ib_insync")

from datafeed.ingest import xauusd_bars as xb
from datafeed.ingest.base import validate_message
from datafeed.ingest.ibkr import IbkrConfig, IbkrConnection

UTC = timezone.utc
ROOT = Path(__file__).resolve().parents[2]


def T(s):
    return datetime.fromisoformat(s).replace(tzinfo=UTC)


class Event(list):
    def __iadd__(self, fn):
        self.append(fn)
        return self


class FakeIB:
    """Just enough of ib_insync.IB for IbkrConnection. `script` maps loop
    number -> list of tickers to deliver during that sleep()."""

    def __init__(self, history, script=None, drop_at=None, fail_connect=False):
        self.history, self.script, self.drop_at = history, script or {}, drop_at
        self.fail_connect, self.loop, self.up = fail_connect, 0, False
        self.pendingTickersEvent = Event()
        self.calls = []

    def connect(self, host, port, clientId, timeout, readonly):
        self.calls.append(("connect", host, port, clientId, readonly))
        if self.fail_connect:
            raise ConnectionRefusedError("no gateway")
        self.up = True

    def qualifyContracts(self, c):
        return [SimpleNamespace(conId=7, symbol=c.symbol, secType=c.secType)]

    def reqHistoricalData(self, contract, **kw):
        self.calls.append(("history", kw))
        return self.history

    def reqMktData(self, contract, **kw):
        self.calls.append(("mktdata", kw))

    def isConnected(self):
        return self.up

    def sleep(self, s):
        self.loop += 1
        if self.loop == self.drop_at:
            self.up = False
        for fn in self.pendingTickersEvent:
            ticks = self.script.get(self.loop)
            if ticks:
                fn(ticks)

    def disconnect(self):
        self.up = False


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


def _hb(ts, px=4300.0):
    return SimpleNamespace(date=ts, open=px, high=px + 1, low=px - 1, close=px + 0.5)


def _tick(t, bid, ask=None, close=4250.0):
    return SimpleNamespace(contract=SimpleNamespace(conId=7), time=t, bid=bid,
                           ask=bid + 0.2 if ask is None else ask, close=close)


CFG = IbkrConfig("fake", 4002, 99)


def _source(ib, clock, max_loops):
    return xb.IbkrXauusdSource(IbkrConnection(CFG, ib=ib), clock=clock,
                               sleep=lambda s: None, max_loops=max_loops)


def test_history_then_live_bars():
    now = T("2026-03-02 10:05:30")
    hist = [_hb(T("2026-03-02 10:03")), _hb(T("2026-03-02 10:04")),
            _hb(T("2026-03-02 10:05")),                                  # forming: trimmed (#7)
            _hb(T("2026-03-01 12:00"))]                                   # Sunday, closed: invalid
    clock = Clock(now)
    ib = FakeIB(hist, script={1: [_tick(T("2026-03-02 10:05:31"), 4301.0)],
                              2: [_tick(T("2026-03-02 10:05:50"), 4302.0)]})
    src = _source(ib, clock, max_loops=3)
    out = []
    orig_sleep = ib.sleep

    def sleep(s):            # advance the clock past the 10:05 deadline on loop 3
        if ib.loop == 2:
            clock.t = T("2026-03-02 10:06:02")
        orig_sleep(s)
    ib.sleep = sleep
    src.run(out.append)

    for m in out:
        validate_message(m)
    assert [(m["bar_ts"], m["history"]) for m in out] == [
        ("2026-03-02T10:03:00+00:00", True), ("2026-03-02T10:04:00+00:00", True),
        ("2026-03-02T10:05:00+00:00", False)]
    assert out[0]["n_ticks"] is None and out[2]["n_ticks"] == 2
    assert out[2]["open"] == 4301.1 and out[2]["close"] == 4302.1
    h = src.health()
    assert h["history_bars"] == 2 and h["live_bars"] == 1 and h["invalid_bars"] == 1
    assert h["timer_closes"] == 1
    kind, host, port, cid, readonly = ib.calls[0]
    assert (port, cid, readonly) == (4002, 99, True)
    kw = ib.calls[1][1]
    assert (kw["durationStr"], kw["barSizeSetting"], kw["whatToShow"], kw["useRTH"],
            kw["formatDate"]) == ("10 D", "1 min", "MIDPOINT", False, 2)


def test_crash_only_on_disconnect():
    ib = FakeIB([], drop_at=2)
    src = _source(ib, Clock(T("2026-03-02 10:00")), max_loops=10)
    with pytest.raises(SystemExit) as e:
        src.run(lambda m: None)
    assert e.value.code == 1


def test_crash_only_on_connect_failure():
    src = _source(FakeIB([], fail_connect=True), Clock(T("2026-03-02 10:00")), max_loops=1)
    with pytest.raises(SystemExit) as e:
        src.run(lambda m: None)
    assert e.value.code == 1


def test_ibkr_config_requires_env():
    with pytest.raises(RuntimeError, match="IB_PORT"):
        IbkrConfig.from_env({"IB_HOST": "h", "IB_CLIENT_ID": "5"})
    assert IbkrConfig.from_env({"IB_HOST": "h", "IB_PORT": "1", "IB_CLIENT_ID": "5"}).port == 1


def test_ibkr_reads_only_ib_port():
    src = (ROOT / "datafeed/ingest/ibkr.py").read_text()
    for other in ("7497", "4001", "4002", "4003", "4004", "TWS_PORT", "IBKR_PORT"):
        assert other not in src


# ── replay ──────────────────────────────────────────────────────────────────
SMALL = (T("2026-03-01 12:00"), T("2026-03-02 10:03"))      # coverage of small_parquet


@pytest.fixture
def small_parquet(tmp_path):
    rows = [(T("2026-03-02 10:01"), 4301.0, 4302.0, 4300.0, 4301.5, 10.0),
            (T("2026-03-02 10:00"), 4300.0, 4301.0, 4299.0, 4300.5, 20.0),   # out of order
            (T("2026-03-02 10:02"), 4302.0, 4303.0, -1.0, 4302.5, 5.0),      # -1: invalid
            (T("2026-03-01 12:00"), 4290.0, 4291.0, 4289.0, 4290.5, 1.0),    # Sunday: invalid
            (T("2026-03-02 10:03"), 4303.0, 4304.0, 4302.0, 4303.5, 7.0)]
    df = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
    p = tmp_path / "bars.parquet"
    df.to_parquet(p)
    return p


def test_replay_publishes_valid_bars_in_order(small_parquet):
    out = []
    src = xb.ReplaySource(str(small_parquet), coverage=SMALL)
    src.run(out.append)
    for m in out:
        validate_message(m)
    assert [m["bar_ts"][11:16] for m in out] == ["10:00", "10:01", "10:03"]
    assert all(m["source"] == "replay" and m["history"] is False and m["n_ticks"] is None
               and "volume" not in m for m in out)
    h = src.health()
    assert {k: h[k] for k in ("done", "rows", "published", "invalid_bars", "last_bar_ts")} == {
        "done": True, "rows": 5, "published": 3, "invalid_bars": 2,
        "last_bar_ts": "2026-03-02T10:03:00+00:00"}
    assert h["coverage"] == ["2026-03-01T12:00:00+00:00", "2026-03-02T10:03:00+00:00"]


def test_replay_window_and_speed(small_parquet):
    out, sleeps = [], []
    src = xb.ReplaySource(str(small_parquet), start="2026-03-02 10:01", end="2026-03-02 10:03", coverage=SMALL,
                          bars_per_s=4, sleep=sleeps.append)
    src.run(out.append)
    assert [m["bar_ts"][11:16] for m in out] == ["10:01"]
    assert sleeps == [0.25]


def test_replay_real_parquet_one_day():
    path = ROOT / "database/ohlcv_data/XAUUSD_1min_UTC_clean.parquet"
    if not path.exists():
        pytest.skip("historical parquet not present (untracked)")
    out = []
    xb.ReplaySource(str(path), start="2025-06-11", end="2025-06-12").run(out.append)
    df = pd.read_parquet(path, filters=[("timestamp", ">=", pd.Timestamp("2025-06-11", tz="UTC")),
                                        ("timestamp", "<", pd.Timestamp("2025-06-12", tz="UTC"))])
    assert 1000 < len(out) <= len(df)
    assert out[0]["bar_ts"] == df["timestamp"].min().isoformat()


def test_entry_point_stdout(tmp_path):
    # inside the real PARQUET_COVERAGE: the env-configured source uses the default coverage
    rows = [(T("2025-06-11 10:00"), 1.0, 2.0, 0.5, 1.5, 1.0), (T("2025-06-11 10:01"), 2.0, 3.0, 1.5, 2.5, 1.0),
            (T("2025-06-11 10:02"), 3.0, 4.0, 2.5, 3.5, 1.0)]
    p = tmp_path / "in_range.parquet"
    pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"]).to_parquet(p)
    env = {**os.environ, "DATAFEED_SOURCE": "replay", "DATAFEED_REPLAY_PARQUET": str(p),
           "DATAFEED_REPLAY_START": "2025-06-11", "DATAFEED_REPLAY_END": "2025-06-12"}
    env.pop("REDIS_URL", None)
    r = subprocess.run([sys.executable, "-m", "datafeed.ingest", "--stdout"], env=env,
                       capture_output=True, text=True, cwd=ROOT / "tests")
    assert r.returncode == 0, r.stderr
    lines = [json.loads(x) for x in r.stdout.splitlines()]
    assert [m["close"] for m in lines] == [1.5, 2.5, 3.5]


def test_entry_point_unknown_source():
    env = {**os.environ, "DATAFEED_SOURCE": "nope"}
    r = subprocess.run([sys.executable, "-m", "datafeed.ingest", "--stdout"], env=env,
                       capture_output=True, text=True)
    assert r.returncode != 0


@pytest.mark.ibkr
@pytest.mark.skipif(os.environ.get("DATAFEED_IBKR_LIVE") != "1",
                    reason="manual: needs a LOCAL PAPER gateway; set DATAFEED_IBKR_LIVE=1 + IB_*")
def test_live_paper_gateway_manual():
    src = xb.IbkrXauusdSource(IbkrConnection(IbkrConfig.from_env()), max_loops=int(90 / xb.LOOP_S))
    out = []
    src.run(out.append)
    assert any(m["history"] for m in out)
    print(json.dumps(src.health(), indent=1))


# ── seal guard ──────────────────────────────────────────────────────────────
def test_seal_constant_equals_research_seal():
    import importlib.util
    spec = importlib.util.spec_from_file_location("_research_sealing", ROOT / "research/sealing.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert pd.Timestamp(xb.SEAL_START) == mod.GLOBAL_SEAL


WIDE = (T("2026-03-01 00:00"), T("2027-12-31 23:59"))       # fake coverage beyond the seal


@pytest.mark.parametrize("end", [None, "2026-10-01 00:00:01", "2027-01-01"])
def test_replay_window_into_seal_refused(small_parquet, end):
    with pytest.raises(xb.SealedDataError):
        xb.ReplaySource(str(small_parquet), start="2026-03-02", end=end, coverage=WIDE)


def test_replay_window_ending_at_seal_allowed(small_parquet):
    xb.ReplaySource(str(small_parquet), start="2026-03-02", end="2026-10-01", coverage=WIDE)


def test_replay_sealed_row_stops_run(small_parquet, monkeypatch):
    src = xb.ReplaySource(str(small_parquet), end="2026-10-01", coverage=WIDE)
    rows = [(T("2026-09-30 23:59"), 1.0, 2.0, 0.5, 1.5), (T("2026-10-01 00:00"), 1.0, 2.0, 0.5, 1.5)]
    monkeypatch.setattr(src, "rows", lambda: iter(rows))
    out = []
    with pytest.raises(xb.SealedDataError):
        src.run(out.append)
    assert len(out) == 1


# ── parquet coverage ────────────────────────────────────────────────────────
REAL = ROOT / "database/ohlcv_data/XAUUSD_1min_UTC_clean.parquet"


@pytest.mark.parametrize("start,end", [
    ("2020-01-01 22:59", "2020-01-02"),                 # starts before the first bar
    ("2026-01-30", "2026-01-30 22:00:01"),              # ends after the last bar's minute
    ("2026-02-01", "2026-02-02"),                       # entirely after
    ("2019-06-01", "2019-06-02"),                       # entirely before
    ("2025-06-12", "2025-06-11"),                       # empty / reversed
])
def test_window_outside_coverage_is_error(start, end):
    with pytest.raises(xb.CoverageError):
        xb.ReplaySource("unused.parquet", start=start, end=end)


def test_window_at_coverage_edges_allowed():
    src = xb.ReplaySource("unused.parquet")             # whole coverage by default
    assert (src.start, src.end) == (T("2020-01-01 23:00"), T("2026-01-30 22:00"))
    xb.ReplaySource("unused.parquet", start="2020-01-01 23:00", end="2026-01-30 22:00")


def test_coverage_constant_matches_real_parquet():
    if not REAL.exists():
        pytest.skip("historical parquet not present (untracked)")
    ts = pd.read_parquet(REAL, columns=["timestamp"])["timestamp"]
    assert (ts.min().to_pydatetime(), ts.max().to_pydatetime()) == xb.PARQUET_COVERAGE
