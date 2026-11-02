"""tools/sigma_now.py against a fake IBKR client, and the L tercile cut-offs
(backend/datafeed/features/l_tercile.py). Synthetic bars, no network; the
cut-off recomputation needs the untracked parquet and skips without it."""
import importlib.util
import io
import json
import math
import random
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from backend.datafeed.features import l_tercile
from backend.datafeed.features import vol_block as vb
from backend.datafeed.ingest.xauusd_bars import _market_open

ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "tools" / "sigma_now.py"
spec = importlib.util.spec_from_file_location("sigma_now", TOOL)
sn = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sn)

UTC = timezone.utc
NOW = datetime(2026, 10, 22, 14, 3, 20, tzinfo=UTC)          # Thursday, market open
PARQUET = ROOT / "database" / "ohlcv_data" / "XAUUSD_1min_UTC_clean.parquet"


def trading_minutes(end: datetime, n_days: int) -> list[datetime]:
    """Open minutes of the last n_days trade dates up to end (a session opens at
    18:00 New York for the next trade date), as IBKR's "N D" gives them."""
    t, out, dates = end.replace(second=0, microsecond=0), [], set()
    while True:
        t -= timedelta(minutes=1)
        if _market_open(t):
            et = t.astimezone(ZoneInfo("America/New_York"))
            dates.add((et + timedelta(hours=6)).date())
            if len(dates) > n_days:
                break
            out.append(t)
        if t < end - timedelta(days=14):
            break
    return sorted(out)


def synthetic(minutes, seed=7, vol=0.0003):
    rng, p, bars = random.Random(seed), 4100.0, []
    for t in minutes:
        o = p
        p = o * math.exp(rng.gauss(0, vol))
        bars.append(SimpleNamespace(date=t, open=o, high=max(o, p) + 0.2, low=min(o, p) - 0.2, close=p,
                                    volume=-1.0))
    return bars


class Event(list):
    def __iadd__(self, h):
        self.append(h)
        return self


class FakeIB:
    def __init__(self, bars=None, error=None, refuse_connect=False):
        self.bars, self.error, self.refuse_connect = bars, error, refuse_connect
        self.errorEvent, self.calls, self.connected, self.disconnected = Event(), [], None, False

    def connect(self, host, port, clientId, readonly=False, timeout=4):
        if self.refuse_connect:
            raise ConnectionRefusedError(111, "Connection refused")
        self.connected = dict(host=host, port=port, clientId=clientId, readonly=readonly)

    def disconnect(self):
        self.disconnected = True

    def qualifyContracts(self, c):
        c.conId = 69067924
        return [c]

    def reqHistoricalData(self, contract, endDateTime, durationStr, barSizeSetting, whatToShow, useRTH,
                          formatDate=1, keepUpToDate=False, timeout=60):
        self.calls.append(dict(contract=contract, end=endDateTime, dur=durationStr, bar=barSizeSetting,
                               what=whatToShow, rth=useRTH, fmt=formatDate, keep=keepUpToDate))
        if self.error:
            for h in self.errorEvent:
                h(1, self.error, "fake error", contract)
            return []
        return list(self.bars)


def run(ib, *args, now=NOW):
    out = io.StringIO()
    rc = sn.main(list(args), ib=ib, contract_cls=SimpleNamespace, now=lambda: now, out=out)
    return rc, out.getvalue()


def test_prints_price_sigma_bands_state_and_tercile(tmp_path):
    ib = FakeIB(synthetic(trading_minutes(NOW, 6)))
    rc, text = run(ib, "--events", str(tmp_path / "none.parquet"))
    assert rc == 0, text
    # read-only, own client id, the datafeed's request: 6 D of 1-min MIDPOINT, UTC
    assert ib.connected == dict(host="127.0.0.1", port=4004, clientId=64, readonly=True) and ib.disconnected
    (c,) = ib.calls
    assert (c["dur"], c["bar"], c["what"], c["rth"], c["fmt"], c["end"]) == ("6 D", "1 min", "MIDPOINT", False, 2, "")
    assert (c["contract"].symbol, c["contract"].secType) == ("XAUUSD", "CMDTY")
    lines = text.splitlines()
    assert lines[0].startswith("XAUUSD sigma now: 2026-10-22 14:03 UTC") and "checked" in lines[0]
    assert lines[1].startswith("price ") and "14:02 UTC" in lines[1]          # the last finished bar
    assert re.search(r"L [+-]\d\.\d{3} = (calm|normal|stressed)", lines[2]) and "S_hd" in lines[2] and " T " in lines[2]
    for H, line in zip((60, 240), lines[3:5]):
        assert line.startswith(f"sigma {H:>3}m $") and "±1σ" in line and "±2σ" in line
    assert "calendar unavailable (no events file" in lines[5]


def test_numbers_are_vol_block_s(tmp_path):
    bars = synthetic(trading_minutes(NOW, 6))
    rc, text = run(FakeIB(bars), "--events", str(tmp_path / "none.parquet"))
    model = vb.load_sigma_model()
    df = pd.DataFrame({k: [getattr(b, k) for b in bars] for k in ("open", "high", "low", "close")},
                      index=pd.DatetimeIndex([b.date for b in bars]))
    st = vb.state_at(model.params, df, NOW)
    s60 = vb.sigma_over(model, st, 60)
    p, d = st.close_ref, s60.sigma_px
    assert f"price      {p:,.2f}" in text
    assert f"±1σ {p - d:,.2f} .. {p + d:,.2f}" in text and f"±2σ {p - 2 * d:,.2f} .. {p + 2 * d:,.2f}" in text
    assert f"L {st.L:+.3f} = {l_tercile.tercile(st.L, l_tercile.load(model))}" in text


def test_too_little_history_says_so(tmp_path):
    rc, text = run(FakeIB(synthetic(trading_minutes(NOW, 2))), "--events", str(tmp_path / "x"))
    assert rc == 1 and "no usable vol state: insufficient_history" in text and "--days 7" in text


def test_days_after_the_sunday_open(tmp_path):
    sunday = datetime(2026, 10, 25, 23, 30, tzinfo=UTC)            # 30 min after the weekly open
    assert run(FakeIB(synthetic(trading_minutes(sunday, 6))), now=sunday)[0] == 1
    ib = FakeIB(synthetic(trading_minutes(sunday, 7)))
    rc, text = run(ib, "--days", "7", "--events", str(tmp_path / "x"), now=sunday)
    assert rc == 0 and ib.calls[0]["dur"] == "7 D" and "[market closed" not in text


def test_no_bars():
    rc, text = run(FakeIB([]))
    assert rc == 1 and "no_bars_before_t" in text


def test_competing_session_exits_4(capsys):
    ib = FakeIB(error=10197)
    rc, _ = run(ib)
    assert rc == 4 and ib.disconnected and "10197" in capsys.readouterr().err


def test_no_gateway_exits_3(capsys):
    rc, _ = run(FakeIB(refuse_connect=True))
    assert rc == 3 and "no connection" in capsys.readouterr().err


def test_a_tampered_model_never_connects(monkeypatch):
    file, sha = vb.SIGMA_MODELS[vb.DEFAULT_SIGMA_MODEL]
    monkeypatch.setitem(vb.SIGMA_MODELS, vb.DEFAULT_SIGMA_MODEL, (file, "0" * 64))
    ib = FakeIB([])
    with pytest.raises(ValueError, match="SHA-256 check failed"):
        run(ib)
    assert ib.connected is None


# --- events -----------------------------------------------------------------------

def events_file(path, rows):
    pd.DataFrame({"ts_utc": pd.DatetimeIndex([r[0] for r in rows]).tz_convert("UTC"),
                  "currency": [r[1] for r in rows], "event_normalized": [r[2] for r in rows],
                  "impact": [r[3] for r in rows]}).to_parquet(path, index=False)


def test_event_warning_within_4_hours(tmp_path):
    p = tmp_path / "ev.parquet"
    events_file(p, [("2026-10-20 08:00Z", "USD", "Old", "High"),
                    ("2026-10-22 16:00Z", "USD", "Core CPI m/m", "High"),            # in 1h56
                    ("2026-10-22 15:00Z", "USD", "Crude Oil Inventories", "Medium"),  # not high
                    ("2026-10-22 15:30Z", "EUR", "ECB Press Conference", "High"),     # not USD
                    ("2026-10-22 19:00Z", "USD", "FOMC Statement", "High"),           # beyond 4 h
                    ("2026-10-25 08:00Z", "USD", "Later", "Low")])
    lines = sn.event_lines(p, NOW, 4, ("USD",))
    assert lines == ["WARNING    high-impact event within 4 h: 16:00 UTC USD Core CPI m/m (in 1h56)"]
    assert len(sn.event_lines(p, NOW, 4, ("USD", "EUR"))) == 2


def test_no_event_and_calendar_coverage(tmp_path):
    p = tmp_path / "ev.parquet"
    events_file(p, [("2026-10-20 08:00Z", "USD", "A", "High"), ("2026-10-25 08:00Z", "USD", "B", "High")])
    assert sn.event_lines(p, NOW, 4, ("USD",)) == ["events     no high-impact USD event in the next 4 h"]
    old = tmp_path / "old.parquet"
    events_file(old, [("2015-01-02 13:30Z", "USD", "A", "High"), ("2025-12-31 15:00Z", "USD", "B", "High")])
    (line,) = sn.event_lines(old, NOW, 4, ("USD",))
    assert line.startswith("events     calendar unavailable (the events file covers 2015-01-02 .. 2025-12-31")


# --- L tercile --------------------------------------------------------------------

def test_cutoffs_load_pinned_and_classify():
    model = vb.load_sigma_model()
    c = l_tercile.load(model)
    assert c.q1 < c.q2 and c.model_sha256 == model.sha256
    assert [l_tercile.tercile(x, c) for x in (c.q1 - 1e-9, c.q1, c.q2 - 1e-9, c.q2)] == \
        ["calm", "normal", "normal", "stressed"]                                # digitize: right edge open
    with pytest.raises(ValueError):
        l_tercile.tercile(float("nan"), c)


def test_cutoffs_refuse_an_edited_file(monkeypatch, tmp_path):
    model = vb.load_sigma_model()
    file, sha = l_tercile.CUTOFFS[model.name]
    d = json.loads((vb.MODELS_DIR / file).read_text())
    d["q1"] = -1.0
    (tmp_path / file).write_text(json.dumps(d))
    monkeypatch.setattr(vb, "MODELS_DIR", tmp_path)
    with pytest.raises(ValueError, match="SHA-256 check failed"):
        l_tercile.load(model)


def test_cutoffs_equal_a_recomputation_from_the_parquet():
    if not PARQUET.exists():
        pytest.skip("historical parquet not present (untracked)")
    model = vb.load_sigma_model()
    df = pd.read_parquet(PARQUET, columns=["timestamp", "open", "high", "low", "close"])
    df = df.set_index(pd.DatetimeIndex(df.pop("timestamp")).tz_convert("UTC")).sort_index()
    got = l_tercile.compute(model, df)
    shipped = json.loads((vb.MODELS_DIR / l_tercile.CUTOFFS[model.name][0]).read_text())
    assert (got["q1"], got["q2"], got["n_labels"]) == (shipped["q1"], shipped["q2"], shipped["n_labels"])

