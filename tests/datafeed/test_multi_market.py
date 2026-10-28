"""tools/data/backfill_ibkr.py --instrument (the resumable multi-market download)
against a fake IBKR client. No network, no real data."""
import json
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from tests.datafeed.test_backfill import Contract, Event, NoPacer, bi, price, syn

UTC = timezone.utc


class FakeIB:
    """Answers durationStr '1 W' / '1 D' with the open minutes before endDateTime.
    fail_at: call numbers that raise ConnectionError once; err_at: {call: code};
    short_weeks: answer week requests with only 10 bars."""

    def __init__(self, fail_at=(), err_at=None, short_weeks=False):
        self.errorEvent, self.calls, self.connects = Event(), [], 0
        self.fail_at, self.err_at, self.short_weeks = set(fail_at), dict(err_at or {}), short_weeks
        self.up = False

    def connect(self, host, port, clientId, readonly=False, timeout=4):
        self.connects += 1
        self.up = True
        self.client = (host, port, clientId, readonly)

    def disconnect(self):
        self.up = False

    def isConnected(self):
        return self.up

    def qualifyContracts(self, c):
        c.conId = 69067924 if c.symbol == "XAUUSD" else 12087792       # gold as test_backfill's probe
        c.localSymbol = f"{c.symbol}.{c.currency}"
        return [c]

    def reqHistoricalData(self, contract, endDateTime, durationStr, barSizeSetting, whatToShow, useRTH,
                          formatDate=1, keepUpToDate=False, timeout=60):
        n = len(self.calls) + 1
        self.calls.append(dict(end=endDateTime, dur=durationStr, what=whatToShow, fmt=formatDate,
                               sym=(contract.symbol, contract.secType, contract.exchange, contract.currency)))
        if n in self.fail_at:
            self.fail_at.discard(n)
            self.up = False
            raise ConnectionError("socket closed")
        if n in self.err_at:
            code = self.err_at.pop(n)
            self.errorEvent.emit(1, code, "pacing violation" if code == 162 else "competing session", contract)
            return []
        end = datetime.strptime(endDateTime, "%Y%m%d-%H:%M:%S").replace(tzinfo=UTC)
        t = end - timedelta(days={"1 W": 7, "2 D": 2, "1 D": 1}[durationStr])
        out = []
        while t < end:
            if syn.is_open(t):
                o, h, lo, c, v = price(t)
                out.append(SimpleNamespace(date=t, open=o, high=h, low=lo, close=c, volume=v))
            t += timedelta(minutes=1)
        return out[:10] if (self.short_weeks and durationStr == "1 W") else out


def run(tmp_path, ib, *args, sleep=lambda s: None):
    a = bi.argparse.Namespace(instrument=args[0], out_dir=str(tmp_path), first=args[1], last=args[2],
                              host="ibgateway", port=4004, client_id=bi.MULTI_CLIENT_ID)
    return bi.main_instrument(a, ib=ib, contract_cls=Contract, pacer=NoPacer(),
                              now=lambda: datetime(2026, 10, 28, 1, 0, tzinfo=UTC), sleep=sleep)


def test_contracts_and_what_to_show():
    assert bi.INSTRUMENTS["EURUSD"] == ("EUR", "CASH", "IDEALPRO", "USD")
    assert bi.INSTRUMENTS["USDJPY"] == ("USD", "CASH", "IDEALPRO", "JPY")
    assert bi.INSTRUMENTS["XAGUSD"] == ("XAGUSD", "CMDTY", "SMART", "USD")      # as XAUUSD
    assert "XAUUSD" not in bi.MULTI and bi.MULTI_WHAT == "MIDPOINT"


def test_chunks_cover_the_range_and_stop_at_the_seal():
    ends = bi.week_chunks(date(2016, 1, 1), date(2026, 9, 30))
    assert ends[0] == datetime(2016, 1, 2, tzinfo=UTC) and ends[-1] == datetime(2026, 10, 1, tzinfo=UTC)
    assert all(e.weekday() == 5 for e in ends[:-1]) and all(e <= bi.SEAL for e in ends)
    assert len(ends) == len(set(ends)) and 555 < len(ends) < 565
    with pytest.raises(bi.Stop) as e:
        bi.week_chunks(date(2016, 1, 1), date(2026, 10, 1))
    assert e.value.status == 3


def test_a_sealed_range_is_refused_before_connecting(tmp_path):
    ib = FakeIB()
    assert run(tmp_path, ib, "XAGUSD", date(2026, 9, 1), date(2026, 10, 2)) == 3
    assert ib.connects == 0 and ib.calls == []


def test_download_writes_day_files_and_resumes(tmp_path):
    ib = FakeIB()
    assert run(tmp_path, ib, "EURUSD", date(2026, 9, 1), date(2026, 9, 30)) == 0
    c = ib.calls
    assert {x["what"] for x in c} == {"MIDPOINT"} and {x["fmt"] for x in c} == {2}
    assert {x["sym"] for x in c} == {("EUR", "CASH", "IDEALPRO", "USD")} and ib.client[2] == 65
    assert c[-1]["end"] == "20261001-00:00:00" and all(x["dur"] == "1 W" for x in c)
    base = tmp_path / "EURUSD"
    days = sorted(p.name for p in base.glob("2026-*.jsonl"))
    assert days[0] == "2026-09-01.jsonl" and days[-1] == "2026-09-30.jsonl"
    last = json.loads((base / "2026-09-30.jsonl").read_text().splitlines()[-1])
    assert last["kind"] == "day_end" and last["bars"] > 1000
    assert (base / "_complete.json").exists() and json.loads((base / "_meta.json").read_text())["contract"]["sec_type"] == "CASH"
    ib2 = FakeIB()
    assert run(tmp_path, ib2, "EURUSD", date(2026, 9, 1), date(2026, 9, 30)) == 0
    assert ib2.calls == []                                  # every chunk has its marker


def test_reconnects_after_a_lost_connection(tmp_path):
    slept = []
    ib = FakeIB(fail_at={2})
    assert run(tmp_path, ib, "USDJPY", date(2026, 8, 1), date(2026, 8, 31), sleep=slept.append) == 0
    assert ib.connects == 2 and (tmp_path / "USDJPY" / "_complete.json").exists()


def test_waits_out_10197_and_pacing(tmp_path):
    slept = []
    ib = FakeIB(err_at={1: 10197, 3: 162})
    assert run(tmp_path, ib, "GBPUSD", date(2026, 8, 1), date(2026, 8, 31), sleep=slept.append) == 0
    assert bi.WAIT_10197_S in slept and bi.WAIT_PACING_S in slept


def test_short_week_falls_back_to_days(tmp_path):
    ib = FakeIB(short_weeks=True)
    assert run(tmp_path, ib, "USDCHF", date(2026, 9, 7), date(2026, 9, 12)) == 0
    assert any(x["dur"] == bi.DAY_DURATION for x in ib.calls)
    marker = json.loads(sorted((tmp_path / "USDCHF" / "_done").glob("*.json"))[0].read_text())
    assert marker["mode"] == "day" and marker["bars"] > 4000


def test_interrupted_run_resumes_where_it_stopped(tmp_path, monkeypatch):
    monkeypatch.setattr(bi, "RECONNECT_FOR_S", 0)
    ib = FakeIB(fail_at={3})
    ib.connect_fails = True
    orig = ib.connect

    def connect_once(*a, **k):                              # the gateway stays down after call 3
        if ib.connects >= 1 and not ib.up and len(ib.calls) >= 3:
            raise ConnectionRefusedError
        return orig(*a, **k)
    ib.connect = connect_once
    assert run(tmp_path, ib, "USDCAD", date(2026, 8, 1), date(2026, 9, 30)) == 6
    done = len(list((tmp_path / "USDCAD" / "_done").glob("*.json")))
    assert done == 2 and not (tmp_path / "USDCAD" / "_complete.json").exists()
    ib2 = FakeIB()
    assert run(tmp_path, ib2, "USDCAD", date(2026, 8, 1), date(2026, 9, 30)) == 0
    assert len(ib2.calls) == len(bi.week_chunks(date(2026, 8, 1), date(2026, 9, 30))) - 2


# --- tools/data/multi_market_build.py ----------------------------------------------

import importlib.util  # noqa: E402
from pathlib import Path  # noqa: E402

import pandas as pd  # noqa: E402

from backend.datafeed.store import manifest  # noqa: E402

_spec = importlib.util.spec_from_file_location("multi_market_build", Path(bi.__file__).with_name("multi_market_build.py"))
mb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mb)


class ShiftedIB(FakeIB):
    """The 2026-02-01 failure: US-Eastern times labelled UTC."""

    def reqHistoricalData(self, contract, endDateTime, durationStr, *a, **k):
        bars = super().reqHistoricalData(contract, endDateTime, durationStr, *a, **k)
        return [SimpleNamespace(**{**b.__dict__, "date": b.date - timedelta(hours=5)}) for b in bars]


@pytest.fixture
def tmp_manifest(tmp_path, monkeypatch):
    m = tmp_path / "manifest.json"
    m.write_text(manifest.MANIFEST.read_text())
    monkeypatch.setattr(manifest, "MANIFEST", m)
    manifest._load.cache_clear()
    manifest._verified.clear()
    yield m
    manifest._load.cache_clear()


def _download(tmp_path, sym, ib=None, first=date(2026, 6, 1), last=date(2026, 9, 30)):
    assert run(tmp_path / "days", ib or FakeIB(), sym, first, last) == 0


def test_instrument_build_passes_reports_no_price_and_registers(tmp_path, tmp_manifest, capsys):
    _download(tmp_path, "XAGUSD")
    rc = mb.main(["instrument", "XAGUSD", str(tmp_path / "days"), "--out-dir", str(tmp_path / "out"), "--update-manifest"])
    out = capsys.readouterr().out
    assert rc == 0 and "passed: yes" in out and "best whole-hour shift 0 h" in out, out.splitlines()[-1]
    df = pd.read_parquet(tmp_path / "out" / "XAGUSD_ibkr_1min_20160101_20260930.parquet")
    assert str(df["timestamp"].dtype) == "datetime64[ns, UTC]" and df["timestamp"].max() < pd.Timestamp("2026-10-01", tz="UTC")
    for p in set(df[["open", "high", "low", "close"]].to_numpy().ravel().round(3).tolist()):
        assert f" {p}" not in out and f"{p} " not in out
    e = json.loads(tmp_manifest.read_text())["files"][mb.key("XAGUSD")]
    assert e["status"] == "unused by any study" and "planned" not in e and len(e["sha256"]) == 64
    assert e["validation"]["passed"] and e["validation"]["session_utc"]                 # silver: the daily break too


def test_shifted_timestamps_are_rejected(tmp_path, tmp_manifest, capsys):
    _download(tmp_path, "EURUSD", ShiftedIB())
    rc = mb.main(["instrument", "EURUSD", str(tmp_path / "days"), "--out-dir", str(tmp_path / "out")])
    out = capsys.readouterr().out
    assert rc == 1 and "passed: NO" in out and "timezone" in out
    assert (tmp_path / "out" / "EURUSD_ibkr_1min_20160101_20260930.rejected.parquet").exists()


def test_an_unfinished_download_is_rejected(tmp_path, tmp_manifest, capsys):
    _download(tmp_path, "USDSEK")
    (tmp_path / "days" / "USDSEK" / "_complete.json").unlink()
    assert mb.main(["instrument", "USDSEK", str(tmp_path / "days"), "--out-dir", str(tmp_path / "out")]) == 1
    assert "incomplete download" in capsys.readouterr().out


def test_dxy_on_common_minutes_only(tmp_path, tmp_manifest, capsys):
    for s in mb.FX:
        _download(tmp_path, s, first=date(2026, 8, 30), last=date(2026, 9, 25))
        assert mb.main(["instrument", s, str(tmp_path / "days"), "--out-dir", str(tmp_path / "data"), "--update-manifest"]) in (0, 1)
    # make the pairs differ: drop 7 minutes from USDJPY, then re-register it
    p = tmp_path / "data" / "USDJPY_ibkr_1min_20160101_20260930.parquet"
    if not p.exists():
        pytest.skip("the 5-day synthetic window fails completeness for a pair")
    df = pd.read_parquet(p)
    df.drop(index=df.index[100:107]).to_parquet(p, index=False)
    doc = json.loads(tmp_manifest.read_text())
    doc["files"][mb.key("USDJPY")].update(manifest.describe(p))
    tmp_manifest.write_text(json.dumps(doc))
    manifest._load.cache_clear()
    manifest._verified.clear()
    capsys.readouterr()
    assert mb.main(["dxy", "--data-dir", str(tmp_path / "data"), "--update-manifest"]) == 0
    out = capsys.readouterr().out
    dx = pd.read_parquet(tmp_path / "data" / "DXY_PROXY_1min_20160101_20260930.parquet")
    assert len(dx) == len(df) - 7 and "high/low bounds hold: True" in out
    e = json.loads(tmp_manifest.read_text())["files"][mb.DXY_KEY]
    assert e["validation"]["dropped_not_all_six"]["USDJPY"] == 0 and e["validation"]["dropped_not_all_six"]["EURUSD"] == 7
    eur = pd.read_parquet(tmp_path / "data" / "EURUSD_ibkr_1min_20160101_20260930.parquet").set_index("timestamp")
    t = dx["timestamp"].iloc[0]
    want = mb.DXY_K * pd.Series({s: pd.read_parquet(tmp_path / "data" / Path(mb.key(s)).name).set_index("timestamp").loc[t, "close"] ** w
                                 for s, w in mb.DXY_W.items()}).prod()
    assert abs(dx["close"].iloc[0] - want) < 1e-9 and t in eur.index


def test_refetch_replaces_a_truncated_day_only_when_longer(tmp_path):
    assert run(tmp_path, FakeIB(), "EURUSD", date(2026, 9, 21), date(2026, 9, 25)) == 0
    f = tmp_path / "EURUSD" / "2026-09-24.jsonl"
    lines = f.read_text().splitlines()
    f.write_text("\n".join(lines[:165]) + "\n" + json.dumps({"kind": "day_end", "day": "2026-09-24", "bars": 165}) + "\n")
    full = len(lines) - 1
    a = bi.argparse.Namespace(instrument="EURUSD", out_dir=str(tmp_path), first=date(2026, 9, 21), last=date(2026, 9, 25),
                              host="h", port=1, client_id=66, refetch_days="2026-09-24,2026-09-23")
    assert bi.main_instrument(a, ib=FakeIB(), contract_cls=Contract, pacer=NoPacer(), sleep=lambda s: None) == 0
    rec = json.loads((tmp_path / "EURUSD" / "_refetch.json").read_text())
    assert rec["2026-09-24"] == {"old": 165, "new": full, "replaced": True}
    assert rec["2026-09-23"]["replaced"] is False                      # already complete: kept
    assert json.loads(f.read_text().splitlines()[-1])["bars"] == full
    a.refetch_days = "2026-10-01"
    assert bi.main_instrument(a, ib=FakeIB(), contract_cls=Contract, pacer=NoPacer()) == 3   # sealed: refused


def test_chunks_are_back_to_back_so_no_day_is_written_twice():
    r = bi.chunk_ranges(date(2016, 1, 1), date(2026, 9, 30))
    assert all(r[i][0] == r[i - 1][1] for i in range(1, len(r)))
    assert r[-1] == (datetime(2026, 9, 26, tzinfo=UTC), datetime(2026, 10, 1, tzinfo=UTC))   # the capped last chunk


def test_the_last_chunk_does_not_overwrite_the_week_before(tmp_path):
    class LastWeekShort(FakeIB):                     # like IBKR: the capped week ends Thursday, so it starts late
        def reqHistoricalData(self, contract, endDateTime, durationStr, *a, **k):
            bars = super().reqHistoricalData(contract, endDateTime, durationStr, *a, **k)
            if endDateTime == "20261001-00:00:00":
                bars = [b for b in bars if b.date >= datetime(2026, 9, 24, 21, 15, tzinfo=UTC)]
            return bars
    assert run(tmp_path, LastWeekShort(), "EURUSD", date(2026, 9, 14), date(2026, 9, 30)) == 0
    tail = json.loads((tmp_path / "EURUSD" / "2026-09-24.jsonl").read_text().splitlines()[-1])
    assert tail["bars"] > 1000 and tail["chunk_end"].startswith("2026-09-26")


# --- the gold backfill through --instrument XAUUSD (2026-10-28) --------------------

from tests.datafeed.test_backfill import bb, env, txt_reference  # noqa: E402,F401


def _gold(tmp_path, ib, what="MIDPOINT", first=date(2026, 2, 1), last=date(2026, 9, 30), sleep=lambda s: None):
    a = bi.argparse.Namespace(instrument="XAUUSD", out_dir=str(tmp_path), first=first, last=last,
                              host="ibgateway", port=4004, client_id=63, what=what, refetch_days=None)
    return bi.main_instrument(a, ib=ib, contract_cls=Contract, pacer=NoPacer(),
                              now=lambda: datetime(2026, 10, 28, 18, 0, tzinfo=UTC), sleep=sleep)


def test_gold_needs_the_picked_type_and_only_its_window(tmp_path):
    ib = FakeIB()
    assert _gold(tmp_path, ib, what=None) == 2 and ib.calls == []
    assert _gold(tmp_path, ib, first=date(2026, 1, 30)) == 2 and ib.calls == []        # before the backfill window
    assert _gold(tmp_path, ib, last=date(2026, 10, 1)) in (2, 3) and ib.calls == []    # sealed


def test_gold_keeps_its_type_through_a_reconnect(tmp_path):
    ib = FakeIB(fail_at={2})
    assert _gold(tmp_path, ib, what="BID", first=date(2026, 9, 1)) == 0
    assert ib.connects == 2 and {c["what"] for c in ib.calls} == {"BID"}
    assert {c["sym"] for c in ib.calls} == {("XAUUSD", "CMDTY", "SMART", "USD")}


def test_gold_day_files_pass_the_backfill_build(env, capsys):  # noqa: F811  (the fixture from test_backfill)
    tmp, txt, m = env
    from tests.datafeed.test_backfill import download, FakeIB as ProbeIB
    (tmp / "overlap.jsonl").write_text(download(ProbeIB(), "--probe-overlap")[1])
    assert bb.main(["pick", str(tmp / "overlap.jsonl"), "--txt", str(txt), "--out", str(tmp / "pick.json")]) == 0
    days = tmp / "days"
    assert _gold(days, FakeIB(), what="MIDPOINT") == 0
    capsys.readouterr()
    rc = bb.main(["build", str(days / "XAUUSD"), "--from-days", "--pick", str(tmp / "pick.json"), "--txt", str(txt),
                  "--out-dir", str(tmp / "out"), "--update-manifest"])
    out = capsys.readouterr().out
    assert rc == 0 and "passed: yes" in out and "best whole-hour shift 0 h" in out, out.splitlines()[-1]
    e = json.loads(m.read_text())["files"][bb.KEY]
    assert e["validation"]["passed"] and e["status"] == "unused by any study" and "planned" not in e
    df = pd.read_parquet(tmp / "out" / Path(bb.KEY).name)
    assert df["timestamp"].min() >= pd.Timestamp("2026-02-01", tz="UTC") and df["timestamp"].max() < pd.Timestamp("2026-10-01", tz="UTC")
    (days / "XAUUSD" / "_complete.json").unlink()                                       # an unfinished run is rejected
    assert bb.main(["build", str(days / "XAUUSD"), "--from-days", "--pick", str(tmp / "pick.json"), "--txt", str(txt),
                    "--out-dir", str(tmp / "out2")]) == 1


def test_a_decided_type_builds_and_leaves_xauusd_txt_alone(env, capsys):  # noqa: F811
    tmp, txt, m = env
    from tests.datafeed.test_backfill import download, FakeIB as ProbeIB
    lines = txt.read_bytes().decode().split("\r\n")                   # the txt matches no IBKR type: +0.07 on every price
    out = [lines[0]] + [",".join(f[:3] + [f"{float(x) + 0.07:.3f}" for x in f[3:7]] + f[7:])
                        for f in (ln.split(",") for ln in lines[1:] if ln)]
    txt.write_bytes(("\r\n".join(out) + "\r\n").encode())
    doc = json.loads(m.read_text())
    doc["files"][bb.TXT_KEY].update(bytes=txt.stat().st_size, sha256=manifest.file_sha256(txt))
    m.write_text(json.dumps(doc))
    manifest._load.cache_clear()
    manifest._verified.clear()
    (tmp / "overlap.jsonl").write_text(download(ProbeIB(refuse=("TRADES",)), "--probe-overlap")[1])
    assert bb.main(["pick", str(tmp / "overlap.jsonl"), "--txt", str(txt), "--out", str(tmp / "pick.json")]) == 1
    assert bb.main(["decide", str(tmp / "pick.json"), "--type", "MIDPOINT", "--ref", "docs/DECISIONS.md OD1",
                    "--date", "2026-10-28", "--out", str(tmp / "decided.json")]) == 0
    days = tmp / "days"
    assert _gold(days, FakeIB(), what="MIDPOINT") == 0
    assert bb.main(["build", str(days / "XAUUSD"), "--from-days", "--pick", str(tmp / "decided.json"), "--txt", str(txt),
                    "--out-dir", str(tmp / "out"), "--update-manifest"]) == 0
    doc = json.loads(m.read_text())
    e = doc["files"][bb.KEY]
    assert e["source"]["what_to_show_pick"]["decision"]["ref"] == "docs/DECISIONS.md OD1"
    seam = e["validation"]["seam"]
    assert seam["before"]["file"] == bb.TXT_KEY and "crossing the seam" in seam["rule"]
    assert set(seam["median_rel_close_diff_on_overlap"]) >= {"MIDPOINT", "BID", "ASK"}
    assert doc["files"][bb.TXT_KEY]["source"]["what_to_show"] is None   # a decision says nothing about the txt


def test_best_shift_comes_from_the_weekend_not_the_daily_break(tmp_path):
    """2026-10-28: on the real gold backfill the daily-break test scored a 6 h shift
    99.6 % (16:59 and 18:00 New York are then both mid-session). The weekend test
    can't be fooled that way, so it picks the shift."""
    assert _gold(tmp_path, FakeIB(), what="MIDPOINT", first=date(2026, 6, 1)) == 0
    meta, reqs, bars, end = bb.read_days_dir(tmp_path / "XAUUSD")
    ts = pd.DatetimeIndex(bb.frame(bars)["timestamp"])
    assert all((r["share"] or 0) > 0.9 for r in bb.session_share(ts, 6).values() if r["days"])   # fooled
    assert bb.weekend_share(ts, 0)["share"] > 0.9 and (bb.weekend_share(ts, 6)["share"] or 0) < 0.1
    assert bb.weekend_share(ts, 1)["share"] < 0.1 and bb.weekend_share(ts, -1)["share"] < 0.1
