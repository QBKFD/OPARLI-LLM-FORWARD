"""research/news_vol (Study A): tests on made-up rows and synthetic bars only."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import nv  # noqa: E402
import nv_core  # noqa: E402
import nv_counts  # noqa: E402
import nv_synth  # noqa: E402

UTC = "UTC"


def ts(s):
    return pd.Timestamp(s, tz=UTC)


def test_group_events_types_and_minutes():
    rows = pd.DataFrame({"ts_utc": [ts("2019-01-11 13:30"), ts("2019-01-11 13:30"), ts("2019-01-30 19:00"),
                                    ts("2019-01-30 19:00"), ts("2019-01-30 19:30")],
                         "event": ["CPI m/m", "Core CPI m/m", "FOMC Statement", "Federal Funds Rate",
                                   "FOMC Press Conference"]})
    g = nv.group_events(rows, ("CPI m/m", "Core CPI m/m"), ("FOMC Statement", "Federal Funds Rate",
                                                             "FOMC Press Conference"))
    assert list(g["type"]) == ["data", "FOMC", "FOMC"]
    assert list(g["n_rows"]) == [2, 2, 1]


def test_group_events_rejects_untimed_unknown_and_seconds():
    good = ("CPI m/m",)
    with pytest.raises(ValueError):
        nv.group_events(pd.DataFrame({"ts_utc": [pd.NaT], "event": ["CPI m/m"]}), good, ())
    with pytest.raises(ValueError):
        nv.group_events(pd.DataFrame({"ts_utc": [ts("2019-01-11 13:30")], "event": ["GDP"]}), good, ())
    with pytest.raises(ValueError):
        nv.group_events(pd.DataFrame({"ts_utc": [ts("2019-01-11 13:30:05")], "event": ["CPI m/m"]}), good, ())


def test_in_window_boundaries():
    t = pd.DatetimeIndex([ts("2019-01-30 18:00"), ts("2019-01-30 19:00"), ts("2019-01-30 20:00")]).asi8
    ev = pd.DatetimeIndex([ts("2019-01-30 19:00")]).asi8
    # N1: the reaction bar opens at 19:00 and closes in (t, t+60] only for t = 19:00
    assert nv.in_window(t, ev, 60).tolist() == [False, True, False]
    # the literal instant reading flags the window ending at 19:00 instead
    assert nv.in_window_literal(t, ev, 60).tolist() == [True, False, False]
    ev2 = pd.DatetimeIndex([ts("2019-01-30 19:30")]).asi8      # off the hour: both readings agree
    assert nv.in_window(t, ev2, 60).tolist() == nv.in_window_literal(t, ev2, 60).tolist() == [False, True, False]
    assert nv.in_window(t, ev, 240).tolist() == [True, True, False]


def test_bh_matches_hand_computation():
    adj = nv.bh([0.01, 0.04, 0.03, 0.5])
    assert np.allclose(adj, [0.04, 0.04 * 4 / 3, 0.04 * 4 / 3, 0.5])
    assert np.isnan(nv.bh([np.nan, 0.01])[0])


def test_verdict_rule():
    assert nv.verdict(-0.01, 0.01, 0.0, 0.9, 0.05)[0] == "PASS"
    assert nv.verdict(-0.01, 0.01, -0.02, 0.001, 0.05)[0] == "PASS"     # better at 60 is fine
    assert nv.verdict(-0.01, 0.01, 0.02, 0.01, 0.05)[0] == "FAIL"      # significantly worse at 60
    assert nv.verdict(-0.01, 0.06, 0.0, 0.9, 0.05)[0] == "FAIL"
    assert nv.verdict(0.01, 0.01, 0.0, 0.9, 0.05)[0] == "FAIL"         # significant, but worse


def test_dm_identical_losses():
    x = np.random.default_rng(1).random(500)
    r = nv.dm_two_sided(x, x, 8)
    assert r["mean_d"] == 0.0 and r["p"] == 1.0


def test_fit_smeared_recovers_beta():
    rng = np.random.default_rng(2)
    X = np.column_stack([np.ones(5000), rng.standard_normal(5000)])
    y = X @ np.array([0.3, -0.7]) + 0.2 * rng.standard_normal(5000)
    beta, c = nv.fit_smeared(X, y)
    assert np.allclose(beta, [0.3, -0.7], atol=0.02)
    assert abs(c - np.exp(0.02)) < 0.01


def _months(n):
    return np.repeat(np.arange(48), n // 48 + 1)[:n]


def test_boot_diff_detects_and_is_seeded():
    rng = np.random.default_rng(3)
    n = 4800
    rel = rng.random(n) < 0.05
    y = rng.standard_normal(n) + 0.5 * rel
    a = nv.boot_diff(y, rel, _months(n), 2000, 7)
    assert a["p_one_sided_gt0"] < 0.01 and a["ci95"][0] > 0
    assert a == nv.boot_diff(y, rel, _months(n), 2000, 7)
    z = nv.boot_diff(rng.standard_normal(n), rel, _months(n), 2000, 7)
    assert z["p_one_sided_gt0"] > 0.01


def test_boot_spearman():
    rng = np.random.default_rng(4)
    x = rng.standard_normal(600)
    s = nv.boot_spearman(x, x + rng.standard_normal(600), _months(600), 500, 7)
    assert s["estimate"] > 0.5 and s["p_two_sided"] < 0.01
    s0 = nv.boot_spearman(x, rng.standard_normal(600), _months(600), 500, 7)
    assert s0["p_two_sided"] > 0.01


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


def test_machinery_check_short():
    """The PREREG section 7 check on a short sample with a strong effect (x10 for 30 min)."""
    out = nv_synth.machinery_check(_events(), _cfg(), B=400, mult=10.0)
    assert out["pass"], out["checks"]
    assert all(c > 0 for c in out["effect"]["aug_coef_data"]) and all(c > 0 for c in out["effect"]["aug_coef_fomc"])


def test_pipeline_rows_are_noncrossing_out_of_fold_and_sealed():
    ev = _events()
    m, _ = nv_synth.synth_bars(ts("2015-12-28"), ts("2016-06-01"), ev, effect=False)
    res = nv_core.run(m, ev, _cfg())
    for H, f in res.rows.items():
        assert ((f["E_ns"] - f.index.asi8) == H * nv.MIN_NS).all()          # no crossing window
        assert f.index.min() >= ts("2016-03-01") and f["E_ns"].max() <= ts("2016-06-01").value
    for c in res.coefs:
        assert c["n_train"] > 0
    with pytest.raises(AssertionError):
        nv_core.run(m, ev, _cfg(seal_end=ts("2016-05-15")))


def test_counts_validity_matches_the_pipeline():
    """nv_counts' timestamp-only validity equals build_context's on bars with no zero RV."""
    ev = _events()
    m, _ = nv_synth.synth_bars(ts("2015-12-28"), ts("2016-03-01"), ev, effect=False)
    m = m.drop(m.index[1000:1400])                                             # a gap
    import run_sigma as rs
    ctx = rs.build_context(m, ts("2016-03-01"))
    for H in (60, 240):
        w = nv_counts.window_table(m.index, ts("2016-03-01"), H)
        assert w.index.equals(ctx.labels)
        assert (w["valid"].to_numpy() == ctx.win[H].valid).all()
        assert (w["crossing"].to_numpy() == ctx.win[H].crossing).all()


def test_truth_is_zero_without_effect():
    ev = _events()
    m, tr = nv_synth.synth_bars(ts("2016-01-04"), ts("2016-02-01"), ev, effect=False)
    t = pd.date_range("2016-01-05", "2016-01-20", freq="1h", tz=UTC).asi8
    with np.errstate(invalid="ignore"):
        lr = tr.log_ratio(t, 240)                     # NaN: no open minute in [t, t+H) (a weekend)
    assert np.isfinite(lr).sum() > 200 and np.allclose(lr[np.isfinite(lr)], 0.0)
