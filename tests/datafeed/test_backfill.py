"""tools/data/backfill_ibkr.py (EC2 download) against a fake IBKR client, and
tools/data/backfill_build.py (pick, validate, write) on synthetic bars. No
network, no real data."""
import importlib.util
import io
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from backend.datafeed.store import manifest

ROOT = Path(__file__).resolve().parents[2]
UTC = timezone.utc


def _tool(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / "data" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


bi, bb, syn = _tool("backfill_ibkr"), _tool("backfill_build"), _tool("make_synthetic_fixture")


def price(t: datetime, kind: str = "MIDPOINT") -> tuple:
    """A deterministic made-up bar for minute t (no real data)."""
    m = int(t.timestamp() // 60)
    base = 3000.0 + (m % 997) * 0.5
    off = {"MIDPOINT": 0.0, "BID": -0.15, "ASK": 0.15, "TRADES": 0.0}[kind]
    o, c = round(base + off, 3), round(base + off + 0.2, 3)
    return o, round(c + 0.3, 3), round(o - 0.3, 3), c, (-1.0 if kind != "TRADES" else 10.0)


class Event:
    def __init__(self):
        self.h = []

    def __iadd__(self, f):
        self.h.append(f)
        return self

    def emit(self, *a):
        for f in self.h:
            f(*a)


class FakeIB:
    """reqHistoricalData answers with the trading minutes of the 24 h before
    endDateTime (UTC), shifted by `shift_h` to fake a wrong time zone."""

    def __init__(self, *, refuse=("TRADES",), error_on=None, shift_h=0, extra=None, naive=False):
        self.errorEvent, self.calls = Event(), []
        self.refuse, self.error_on, self.shift_h, self.extra, self.naive = refuse, error_on, shift_h, extra, naive
        self.connected = self.disconnected = None

    def connect(self, host, port, clientId, readonly=False, timeout=4):
        self.connected = dict(host=host, port=port, clientId=clientId, readonly=readonly)

    def disconnect(self):
        self.disconnected = True

    def qualifyContracts(self, c):
        c.conId, c.localSymbol = 69067924, "XAUUSD"
        return [c]

    def reqHistoricalData(self, contract, endDateTime, durationStr, barSizeSetting, whatToShow, useRTH,
                          formatDate=1, keepUpToDate=False, timeout=60):
        self.calls.append(dict(end=endDateTime, dur=durationStr, bar=barSizeSetting, what=whatToShow,
                               rth=useRTH, fmt=formatDate))
        if self.error_on and self.error_on[0] == endDateTime:
            self.errorEvent.emit(1, self.error_on[1], self.error_on[2], contract)
            return []
        if whatToShow in self.refuse:
            self.errorEvent.emit(1, 162, "Historical Market Data Service error message:No data of type TRADES", contract)
            return []
        end = datetime.strptime(endDateTime, "%Y%m%d-%H:%M:%S").replace(tzinfo=UTC)
        out, t = [], end - timedelta(days=1)
        while t < end:
            if syn.is_open(t):
                o, h, lo, c, v = price(t, whatToShow)
                when = t + timedelta(hours=self.shift_h)
                out.append(SimpleNamespace(date=when.replace(tzinfo=None) if self.naive else when,
                                           open=o, high=h, low=lo, close=c, volume=v))
            t += timedelta(minutes=1)
        if self.extra:
            out.append(self.extra)
        return out


class Contract(SimpleNamespace):
    pass


class NoPacer:
    def wait(self):
        pass


def download(ib, *args):
    out = io.StringIO()
    rc = bi.main(list(args), ib=ib, contract_cls=Contract, out=out, pacer=NoPacer(),
                 now=lambda: datetime(2026, 10, 23, 7, 0, tzinfo=UTC))
    return rc, out.getvalue()


# --- backfill_ibkr.py -----------------------------------------------------------

def test_plan_covers_the_range_without_saturdays_and_stops_before_the_seal():
    reqs = bi.plan("download", "MIDPOINT")
    days = [d for _, d in reqs]
    assert days == sorted(days, reverse=True) and len(days) == 208
    assert days[0].isoformat() == "2026-09-30" and days[-1].isoformat() == "2026-02-01"
    assert all(d.weekday() != 5 for d in days)
    assert bi.end_of(days[0]) == "20261001-00:00:00"                 # the last bar requested is < 2026-10-01
    assert len(bi.plan("probe", None)) == 4 * 11


def test_pacer_keeps_50_requests_per_10_minutes():
    clock = [0.0]
    slept = []

    def sleep(s):
        slept.append(s)
        clock[0] += s
    p = bi.Pacer(clock=lambda: clock[0], sleep=sleep)
    for _ in range(50):
        p.wait()
    assert slept == []
    p.wait()
    assert slept and clock[0] >= 600


def test_probe_records_a_refused_type_and_asks_in_utc():
    ib = FakeIB()
    rc, text = download(ib, "--probe-overlap")
    assert rc == 0 and ib.disconnected
    assert ib.connected == dict(host="127.0.0.1", port=4004, clientId=63, readonly=True)
    assert {c["fmt"] for c in ib.calls} == {2} and {c["rth"] for c in ib.calls} == {False}
    assert {c["bar"] for c in ib.calls} == {"1 min"} and {c["what"] for c in ib.calls} == set(bi.PROBE_TYPES)
    recs = [json.loads(x) for x in text.splitlines()]
    assert recs[0]["kind"] == "meta" and recs[0]["contract"]["con_id"] == 69067924 and recs[-1]["kind"] == "end"
    trades = [r for r in recs if r["kind"] == "request" and r["type"] == "TRADES"]
    assert trades and all(r["bars"] == 0 and r["errors"][0]["code"] == 162 for r in trades)


@pytest.mark.parametrize("code, msg, status", [
    (10197, "No market data during competing live session", 4),
    (162, "Historical Market Data Service error message:API historical data query cancelled: pacing violation", 5),
])
def test_stops_without_an_end_line(code, msg, status):
    ib = FakeIB(error_on=("20260930-00:00:00", code, msg))
    rc, text = download(ib, "--download", "MIDPOINT")
    assert rc == status and ib.disconnected
    assert '"kind":"end"' not in text


def test_a_bar_in_the_sealed_period_stops_the_run():
    ib = FakeIB(extra=SimpleNamespace(date=datetime(2026, 10, 1, tzinfo=UTC), open=1, high=1, low=1, close=1, volume=-1))
    rc, _ = download(ib, "--download", "MIDPOINT")
    assert rc == 3


def test_a_naive_bar_time_stops_the_run():
    rc, _ = download(FakeIB(naive=True), "--probe-overlap")
    assert rc == 1


# --- backfill_build.py ----------------------------------------------------------

def txt_reference(path: Path, kind="MIDPOINT"):
    """A fake XAUUSD.txt over the overlap, in its format, and a manifest listing it."""
    t, end = datetime(2026, 1, 18, tzinfo=UTC), datetime(2026, 1, 31, tzinfo=UTC)
    lines = ["should contai<TICKER>,<DTYYYYMMDD>,<TIME>,<OPEN>,<HIGH>,<LOW>,<CLOSE>,<VOL>"]
    while t < end:
        if syn.is_open(t):
            o, h, lo, c, _ = price(t, kind)
            lines.append(f"XAUUSD,{t:%Y%m%d},{t:%H%M%S},{o},{h},{lo},{c},120")
        t += timedelta(minutes=1)
    path.write_bytes(("\r\n".join(lines) + "\r\n").encode())


@pytest.fixture
def env(tmp_path, monkeypatch):
    txt = tmp_path / "XAUUSD.txt"
    txt_reference(txt)
    m = tmp_path / "manifest.json"
    real = json.loads(manifest.MANIFEST.read_text())
    real["files"][bb.TXT_KEY].update(bytes=txt.stat().st_size, sha256=manifest.file_sha256(txt))
    m.write_text(json.dumps(real, indent=1))
    monkeypatch.setattr(manifest, "MANIFEST", m)
    manifest._verified.clear()
    return tmp_path, txt, m


def test_pick_chooses_the_type_that_reproduces_the_txt(env, capsys):
    tmp, txt, _ = env
    (tmp / "overlap.jsonl").write_text(download(FakeIB(), "--probe-overlap")[1])
    assert bb.main(["pick", str(tmp / "overlap.jsonl"), "--txt", str(txt), "--out", str(tmp / "pick.json")]) == 0
    pk = json.loads((tmp / "pick.json").read_text())
    assert pk["chosen"] == "MIDPOINT" and pk["table"]["MIDPOINT"]["ohlc_share"] == 1.0
    assert pk["table"]["BID"]["ohlc_share"] == 0.0 and pk["table"]["TRADES"]["ibkr_error_codes"] == [162]
    assert pk["volume_reproduced"] is False                     # MIDPOINT has no volume; the txt does


def test_pick_stops_below_99_5_percent(env, capsys):
    tmp, txt, _ = env
    txt_reference(txt, kind="TRADES")                            # +0 offset but a different file: rehash it
    (tmp / "overlap.jsonl").write_text(download(FakeIB(refuse=("TRADES", "MIDPOINT")), "--probe-overlap")[1])
    m = json.loads(manifest.MANIFEST.read_text())
    m["files"][bb.TXT_KEY].update(bytes=txt.stat().st_size, sha256=manifest.file_sha256(txt))
    manifest.MANIFEST.write_text(json.dumps(m))
    manifest._load.cache_clear()
    manifest._verified.clear()
    assert bb.main(["pick", str(tmp / "overlap.jsonl"), "--txt", str(txt), "--out", str(tmp / "pick.json")]) == 1
    assert json.loads((tmp / "pick.json").read_text())["chosen"] is None
    assert "STOP, no backfill" in capsys.readouterr().out


def _pick_and_download(tmp, txt, ib):
    (tmp / "overlap.jsonl").write_text(download(FakeIB(), "--probe-overlap")[1])
    assert bb.main(["pick", str(tmp / "overlap.jsonl"), "--txt", str(txt), "--out", str(tmp / "pick.json")]) == 0
    rc, text = download(ib, "--download", "MIDPOINT")
    (tmp / "dl.jsonl").write_text(text)
    return rc


def test_build_passes_and_updates_the_manifest(env, capsys):
    tmp, txt, m = env
    assert _pick_and_download(tmp, txt, FakeIB()) == 0
    rc = bb.main(["build", str(tmp / "dl.jsonl"), "--pick", str(tmp / "pick.json"), "--txt", str(txt),
                  "--out-dir", str(tmp / "out"), "--update-manifest"])
    assert rc == 0
    out = tmp / "out" / Path(bb.KEY).name
    df = pd.read_parquet(out)
    assert str(df["timestamp"].dtype) == "datetime64[ns, UTC]" and df["volume"].isna().all()       # -1 -> NaN
    assert df["timestamp"].min() >= pd.Timestamp("2026-02-01", tz="UTC")
    assert df["timestamp"].max() < pd.Timestamp("2026-10-01", tz="UTC")
    assert not df["timestamp"].duplicated().any()                 # overlapping requests deduplicated
    doc = json.loads(m.read_text())
    e = doc["files"][bb.KEY]
    assert e["status"] == "unused by any study" and e["in_git"] is False and "planned" not in e
    assert e["validation"]["passed"] is True and e["validation"]["best_whole_hour_shift"] == 0
    assert e["validation"]["session_utc"]["dst"]["share"] >= 0.9 and e["validation"]["session_utc"]["std"]["share"] >= 0.9
    assert e["source"]["what_to_show"] == "MIDPOINT" and e["source"]["format_date"] == 2
    assert doc["files"][bb.TXT_KEY]["source"]["what_to_show"] == "MIDPOINT"
    manifest._load.cache_clear()
    assert manifest.verify(out) == bb.KEY
    # no price in the entry (it is committed): every number is a count, share, shift or % gap
    prices = set(df[["open", "high", "low", "close"]].to_numpy().ravel().round(3).tolist())

    def numbers(o):
        if isinstance(o, dict):
            for v in o.values():
                yield from numbers(v)
        elif isinstance(o, list):
            for v in o:
                yield from numbers(v)
        elif isinstance(o, float):
            yield o
    assert not [x for x in numbers(e) if round(x, 3) in prices]


def test_a_shifted_download_is_rejected_by_the_session_check(env, capsys):
    """The 2026-02-01 break: US-Eastern times labelled UTC (4-5 h off)."""
    tmp, txt, m = env
    assert _pick_and_download(tmp, txt, FakeIB(shift_h=-5)) == 0
    rc = bb.main(["build", str(tmp / "dl.jsonl"), "--pick", str(tmp / "pick.json"), "--txt", str(txt),
                  "--out-dir", str(tmp / "out"), "--update-manifest"])
    assert rc == 1
    assert (tmp / "out" / Path(bb.KEY).name.replace(".parquet", ".rejected.parquet")).exists()
    assert not (tmp / "out" / Path(bb.KEY).name).exists()
    doc = json.loads(m.read_text())
    assert doc["files"][bb.KEY].get("planned") is True             # the planned entry stays
    rej = doc["files"][bb.KEY.replace(".parquet", ".rejected.parquet")]
    assert rej["validation"]["passed"] is False and rej["validation"]["best_whole_hour_shift"] == 5
    assert any("UTC session check" in f for f in rej["validation"]["failures"])
    assert doc["files"][bb.TXT_KEY]["source"]["what_to_show"] is None   # not filled from a rejected build
    assert "REJECTED" in capsys.readouterr().err


def test_an_incomplete_download_is_rejected(env, capsys):
    tmp, txt, _ = env
    _pick_and_download(tmp, txt, FakeIB())
    lines = (tmp / "dl.jsonl").read_text().splitlines()
    (tmp / "dl.jsonl").write_text("\n".join(lines[:-1]) + "\n")    # no "end" line
    assert bb.main(["build", str(tmp / "dl.jsonl"), "--pick", str(tmp / "pick.json"), "--txt", str(txt),
                    "--out-dir", str(tmp / "out")]) == 1
    assert "incomplete download" in capsys.readouterr().err


def test_conflicting_duplicates_are_rejected():
    meta = {"mode": "download", "what_to_show": "MIDPOINT", "contract": {"con_id": 1}}
    bars = [{"kind": "bar", "type": "MIDPOINT", "t": 1772404800, "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": -1},
            {"kind": "bar", "type": "MIDPOINT", "t": 1772404800, "o": 1, "h": 2, "l": 0.5, "c": 1.6, "v": -1}]
    _, checks, fails = bb.build(meta, [], bars, {"kind": "end"}, {"chosen": "MIDPOINT", "contract": {"con_id": 1}})
    assert checks["conflicting_duplicates"] == 1 and any("conflicting" in f for f in fails)
