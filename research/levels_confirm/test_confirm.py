"""
Levels-confirmation tests (synthetic data only; the research/levels tests are
run alongside).  python -m pytest research/levels_confirm research/levels research/test_sealing.py -q
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import confirm as cf  # noqa: E402

lv, au = cf.lv, cf.au


@pytest.fixture(scope="module")
def world():
    """~150 trading days of synthetic 1-min data (EDT sessions), constant σ factor."""
    rng = np.random.default_rng(12345)
    starts = [d for d in pd.bdate_range("2019-06-03", periods=150)]
    idx = pd.DatetimeIndex([]).tz_localize("UTC")
    parts = [pd.date_range(pd.Timestamp(d, tz="UTC") - pd.Timedelta(hours=2), periods=1380, freq="1min")
             for d in starts]                                   # 22:00 UTC (18:00 EDT) prev evening
    idx = parts[0].append(parts[1:])
    close = 1500 * np.exp(np.cumsum(rng.normal(0, 3e-4, len(idx))))
    op = np.r_[close[0], close[:-1]]
    hi = np.maximum(op, close) + 0.05
    lo = np.minimum(op, close) - 0.05
    ts = idx
    days = lv.attach_levels(lv.day_table(ts, op, hi, lo, close))
    mk = lv.Market(ts.asi8, op, hi, lo, close, np.full(len(ts), 0.004))
    test_idx = list(days.index[~days.excluded][5:])
    units, short = cf.build_units(days, test_idx, mk)
    return days, mk, test_idx, units


def test_placebo_same_distance_same_side_own_day(world):
    days, mk, test_idx, units = world
    by_id = {u["id"]: u for u in units}
    plc = [u for u in units if u["is_placebo"]]
    assert len(plc) > 100
    for u in plc:
        par = by_id[u["parent"]]
        P = u["pday"]
        op, fp = days.at[P, "open"], mk.f[int(days.at[P, "i0"])]
        d_own = u["side"] * (u["level"] - op) / (abs(u["level"]) * fp)
        assert d_own == pytest.approx(par["dist"], rel=1e-9)             # same σ-distance on ITS day
        assert u["side"] == par["side"]
        assert (u["level"] > op) == (u["side"] > 0)                       # same side of its own open
        D = par["pday"]
        pd_ = days.at[D, "open"]
        assert par["dist"] == pytest.approx(par["side"] * (par["level"] - pd_) / (abs(par["level"]) * mk.f[int(days.at[D, "i0"])]))


def test_placebo_days_within_60_and_never_the_real_day(world):
    days, mk, test_idx, units = world
    pos = {di: k for k, di in enumerate(test_idx)}
    by_id = {u["id"]: u for u in units}
    for u in units:
        if u["is_placebo"]:
            D, P = by_id[u["parent"]]["pday"], u["pday"]
            assert P != D
            assert abs(pos[P] - pos[D]) <= cf.WINDOW


def test_exclusion_zone_vs_placebo_days_real_levels(world):
    days, mk, test_idx, units = world
    for u in units:
        if u["is_placebo"]:
            P = u["pday"]
            sp = abs(u["level"]) * mk.f[int(days.at[P, "i0"])]
            for r in (days.at[P, "PDH"], days.at[P, "PDL"]):
                if np.isfinite(r):
                    assert abs(u["level"] - r) >= cf.EXCL * sp - 1e-9


def test_placebo_formula_is_exact():
    for side in (+1, -1):
        for d in (0.1, 0.37, 0.5, 1.2):
            p = cf.placebo_level(2000.0, side, d, 0.01)
            assert side * (p - 2000.0) / (abs(p) * 0.01) == pytest.approx(d, rel=1e-12)


def test_bootstrap_resamples_real_days_with_placebos_attached(world):
    days, mk, test_idx, units = world
    by_id = {u["id"]: u for u in units}
    for u in units:
        if u["is_placebo"]:
            assert u["cluster"] == by_id[u["parent"]]["cluster"]         # carried by the REAL day
    rng = np.random.default_rng(3)
    rows = []
    for c in range(50):
        rows.append({"unit": c, "parent": c, "is_placebo": False, "cluster": f"D{c}", "v": float(rng.random() < 0.4)})
        for j in range(5):
            rows.append({"unit": 1000 + 10 * c + j, "parent": c, "is_placebo": True, "cluster": f"D{c}",
                         "v": float(rng.random() < 0.5)})
    ev = pd.DataFrame(rows)
    cl = pd.Index(sorted(ev.cluster.unique()))
    W = cf.boot_W(len(cl), B=3000, seed=1)
    a = cf.summarize(*cf.effect_draws(ev, "v", cl, W))
    dup = pd.concat([ev, ev.assign(unit=ev.unit + 10**6, parent=np.where(ev.is_placebo, ev.parent + 10**6, ev.parent + 10**6))])
    b = cf.summarize(*cf.effect_draws(dup, "v", cl, W))
    assert a["ci_lo"] == pytest.approx(b["ci_lo"]) and a["ci_hi"] == pytest.approx(b["ci_hi"])


def test_volblock_fit_only_before_2024():
    assert cf.MODES["confirm"]["fit_end"] == "2024-01-01"
    idx = pd.date_range("2023-12-31 23:58", periods=3, freq="1min", tz="UTC")     # last row is 2024-01-01 00:00
    m = pd.DataFrame({"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0}, index=idx)
    with pytest.raises(AssertionError):
        cf.sigma_single_fit(m, m, pd.Timestamp("2024-01-01", tz="UTC"))


def test_loader_asserts_study_cutoff_and_global_seal():
    au.assert_study_window(pd.DatetimeIndex(["2026-01-30 23:59"], tz="UTC"))
    with pytest.raises(AssertionError, match="CUT-OFF"):
        au.assert_study_window(pd.DatetimeIndex(["2026-01-31 00:00"], tz="UTC"))
    with pytest.raises(AssertionError, match="CUT-OFF|SEALED"):
        au.assert_study_window(pd.DatetimeIndex(["2026-10-01 00:00"], tz="UTC"))
    with pytest.raises(AssertionError, match="SEALED"):
        cf.assert_before_global_seal(pd.DatetimeIndex(["2026-10-01"], tz="UTC"))


def test_verdict_mapping_and_holm():
    V = lambda e, p, mde: cf.verdict({"effect": e, "p_less": p, "mde": mde})
    assert V(-0.08, 0.01, 0.05) == "SUPPORTED"
    assert V(0.01, 0.6, 0.05) == "REJECTED"
    assert V(-0.03, 0.2, 0.08) == "REJECTED"
    assert V(-0.06, 0.2, 0.15) == "INCONCLUSIVE"
    assert V(-0.03, 0.01, 0.05) == "SMALLER THAN CLAIMED"
    assert cf.holm([0.01, 0.04]) == pytest.approx([0.02, 0.04])
    assert cf.holm([0.04, 0.01]) == pytest.approx([0.04, 0.02])
