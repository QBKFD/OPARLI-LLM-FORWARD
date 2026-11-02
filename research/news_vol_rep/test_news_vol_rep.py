"""research/news_vol_rep: tests on made-up timestamps and synthetic bars only."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import rep_core  # noqa: E402
import nv  # noqa: E402
import nv_core  # noqa: E402
import nv_params  # noqa: E402
import rep_params as V  # noqa: E402
import run_sigma as rs  # noqa: E402

UTC = "UTC"


def ts(s, tz=UTC):
    return pd.Timestamp(s, tz=tz)


def ny(s):
    return ts(s, "America/New_York").tz_convert(UTC)


def test_params_are_study_a():
    for k in ("DEV_START", "DEV_END_EXCL", "EMBARGO", "HORIZONS", "PRIMARY_H", "COVERAGE", "TIER1",
              "CALENDAR_SHA256", "NW_LAG", "BOOT_B", "SEED", "BH_Q", "SYN_EFFECT_MULT", "SYN_RECOVER"):
        assert getattr(V, k) == getattr(nv_params, k), k
    assert V.FOLD_EDGES.equals(nv_params.FOLD_EDGES)
    assert V.MARKETS == ("XAGUSD", "EURUSD")


def test_fx_open_flags():
    t = pd.DatetimeIndex([ny("2019-01-16 16:59"), ny("2019-01-16 17:00"), ny("2019-01-16 17:14"),
                          ny("2019-01-16 17:15"), ny("2019-01-16 17:59"), ny("2019-01-18 16:59"),
                          ny("2019-01-18 17:15"), ny("2019-01-19 12:00"), ny("2019-01-20 17:14"),
                          ny("2019-01-20 17:15"), ny("2019-07-17 17:05"), ny("2019-07-17 17:20")])
    assert rep_core.fx_open_flags(t).tolist() == [True, False, False, True, True, True, False, False, False, True,
                                                  False, True]


def _fx_bar_times(start, end):
    full = pd.date_range(start, end, freq="1min", inclusive="left", tz=UTC)
    return full[rep_core.fx_open_flags(full)]


def test_calendar_agreement_on_fx_bar_times():
    """R3: on FX-shaped bar times the metals and FX calendars exclude the same hourly windows,
    across a DST change."""
    idx = _fx_bar_times("2019-02-24", "2019-04-01")
    a = rep_core.calendar_agreement(idx, ts("2019-04-01"), (240, 60))
    assert all(v["disagree"] == 0 and v["crossing_metals"] > 0 for v in a.values())


def test_crossing_differs_for_an_off_hour_origin():
    """The agreement holds for whole-hour origins only: a 17:20 NY origin is open under FX."""
    lab = pd.DatetimeIndex([ny("2019-01-16 12:00"), ny("2019-01-16 14:00"), ny("2019-01-16 17:00"),
                            ny("2019-01-16 17:20")])                      # sorted, as labels are
    end = ts("2019-01-20")
    assert rep_core.crossing(lab, end, 60, rep_core.fx_open_flags).tolist() == [False, False, True, False]
    assert rep_core.crossing(lab, end, 60, rs.open_flags).tolist() == [False, False, True, True]


def _analysis(d240, p240, d60, p60, pq1=0.5, pq3=0.5):
    return {"Q2": {"240": {"mean_d": d240}, "60": {"mean_d": d60}},
            "BH_within_market_not_used": {"Q1": {"p": pq1}, "Q2_H240": {"p": p240}, "Q2_H60": {"p": p60},
                                          "Q3": {"p": pq3}}}


def test_combine_bh_over_both_markets():
    a = {"XAGUSD": _analysis(-0.01, 0.01, 0.0, 0.9), "EURUSD": _analysis(-0.01, 0.004, 0.0, 0.9)}
    c = rep_core.combine(a, 0.05)
    assert c["m"] == 8
    p = [0.5, 0.01, 0.9, 0.5, 0.5, 0.004, 0.9, 0.5]
    adj = nv.bh(p)
    assert np.isclose(c["BH_family"]["XAGUSD:Q2_H240"]["p_bh"], adj[1])
    assert np.isclose(c["BH_family"]["EURUSD:Q2_H240"]["p_bh"], adj[5])
    assert c["per_market"]["XAGUSD"]["verdict"] == c["per_market"]["EURUSD"]["verdict"] == "PASS"
    assert c["replication"] == "HOLDS"


def test_combine_one_market_fails():
    # 0.03 passes alone (BH over 4 would give 0.03 x 4 / 2 = 0.06 here too); over 8 it is 0.03 x 8 / 2 = 0.12
    a = {"XAGUSD": _analysis(-0.01, 0.03, 0.0, 0.9), "EURUSD": _analysis(-0.01, 1e-6, 0.0, 0.9)}
    c = rep_core.combine(a, 0.05)
    assert c["per_market"]["XAGUSD"]["verdict"] == "FAIL" and c["per_market"]["EURUSD"]["verdict"] == "PASS"
    assert c["replication"] == "DOES NOT HOLD"
    worse60 = {"XAGUSD": _analysis(-0.01, 1e-6, 0.02, 1e-6), "EURUSD": _analysis(-0.01, 1e-6, 0.0, 0.9)}
    assert rep_core.combine(worse60)["per_market"]["XAGUSD"]["verdict"] == "FAIL"


# ----------------------------------------------------------------------------- synthetic pipeline

def _events():
    data = pd.date_range("2016-01-05 13:30", "2016-06-01", freq="3D", tz=UTC)
    fomc = pd.date_range("2016-01-13 19:00", "2016-06-01", freq="7D", tz=UTC)
    rows = pd.DataFrame({"ts_utc": list(data) + list(fomc),
                         "event": ["CPI m/m"] * len(data) + ["FOMC Statement"] * len(fomc)})
    return nv.group_events(rows, ("CPI m/m",), ("FOMC Statement",))


def _cfg(**kw):
    return nv_core.Config(train_start=ts("2016-01-04"), end=ts("2016-06-01"),
                          edges=pd.DatetimeIndex([ts("2016-03-01"), ts("2016-04-01"), ts("2016-05-01"),
                                                  ts("2016-06-01")]), **kw)


def test_synth_on_bar_times_shares_draws_and_marks_events():
    idx = _fx_bar_times("2016-01-04", "2016-02-01")
    ev = _events()
    m1, tr1 = rep_core.synth_bars_on(idx, ev, effect=True)
    m0, tr0 = rep_core.synth_bars_on(idx, ev, effect=False)
    assert m1.index.equals(idx) and m0.index.equals(idx)
    r1, r0 = np.diff(np.log(m1["close"].to_numpy())), np.diff(np.log(m0["close"].to_numpy()))
    ratio = np.abs(r1) / np.abs(r0)
    first = np.searchsorted(idx.asi8, ev.index.asi8[0])
    assert np.allclose(ratio[:first - 1], 1.0)                       # same draws before the first event
    assert np.isclose(ratio[first - 1], 2.0)                         # sqrt(4) on the event minute's return
    assert np.allclose(tr0.cum_var, tr1.cum_var0)


def test_machinery_check_short_on_fx_times():
    """The PREREG section 7 check on a short FX-shaped sample with a strong effect (x10 for 30 min)."""
    idx = _fx_bar_times("2015-12-28", "2016-06-01")
    out = rep_core.machinery_check(idx, _events(), _cfg(), B=400, mult=10.0)
    assert out["pass"], out["checks"]
    assert all(c > 0 for c in out["effect"]["aug_coef_data"]) and all(c > 0 for c in out["effect"]["aug_coef_fomc"])


def test_pipeline_on_fx_times_never_uses_the_pause_hour():
    """Non-crossing rows on FX bar times: windows of exactly H minutes, none covering 17:00-17:59 NY."""
    idx = _fx_bar_times("2015-12-28", "2016-06-01")
    ev = _events()
    m, _ = rep_core.synth_bars_on(idx, ev, effect=False)
    res = nv_core.run(m, ev, _cfg())
    for H, f in res.rows.items():
        assert ((f["E_ns"] - f.index.asi8) == H * nv.MIN_NS).all()
        for t in f.index[:2000]:
            mins = pd.date_range(t, periods=H, freq="1min").tz_convert("America/New_York")
            assert not (mins.hour == 17).any()
