"""research/squeeze_oco/stage1.py on synthetic bars (no data needed).
python -m pytest research/squeeze_oco -q"""
import inspect
import json
import math
import random
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import stage1  # noqa: E402

from backend.datafeed.features import vol_block as vb  # noqa: E402
from backend.datafeed.ingest.xauusd_bars import _market_open  # noqa: E402

START, END = pd.Timestamp("2007-01-01", tz="UTC"), pd.Timestamp("2021-01-01", tz="UTC")


def synthetic(first="2019-03-04", days=12, seed=3):
    """Made-up 1-min bars over the market's open minutes (no real data)."""
    rng, p, rows = random.Random(seed), 1300.0, []
    t = pd.Timestamp(first, tz="UTC")
    for _ in range(days * 1440):
        if _market_open(t.to_pydatetime()):
            o = p
            p = o * math.exp(rng.gauss(0, 0.0003))
            rows.append((t, o, max(o, p) + 0.1, min(o, p) - 0.1, p))
        t += pd.Timedelta(minutes=1)
    return pd.DataFrame([r[1:] for r in rows], columns=["open", "high", "low", "close"],
                        index=pd.DatetimeIndex([r[0] for r in rows], name="timestamp"))


@pytest.mark.parametrize("bad", ["2006-12-31 23:59", "2021-01-01 00:00", "2026-10-01 00:00"])
def test_dev_guard_refuses_outside_the_dev_period(bad):
    with pytest.raises(AssertionError, match="SEALED|OUTSIDE"):
        stage1.dev_guard(pd.DatetimeIndex([bad], tz="UTC"), START, END, "x")


def test_dev_guard_accepts_the_dev_period():
    stage1.dev_guard(pd.DatetimeIndex(["2007-01-01 00:00", "2020-12-31 23:59"], tz="UTC"), START, END, "x")


def test_no_public_function_has_a_default_value():
    """Rule one: values come from the register, so no function carries its own."""
    for name, f in inspect.getmembers(stage1, inspect.isfunction):
        if f.__module__ != "stage1" or name.startswith("_"):
            continue
        defaults = [p for p in inspect.signature(f).parameters.values() if p.default is not p.empty]
        assert not defaults, (name, defaults)


def test_state_table_equals_vol_block_and_sigma_over():
    m = synthetic()
    model = vb.load_sigma_model("parquet2021_2023")
    end = pd.Timestamp("2019-03-16", tz="UTC")
    ctx = stage1.context(m, end, (60, 240), 0.90)
    tbl = stage1.state_table(ctx, model, START, end)
    ok = tbl.dropna(subset=["L", "S_hd", "T"])
    assert len(ok) > 20
    for t in ok.index[::7]:
        st = vb.state_at(model.params, m, t)
        assert st.reason is None
        assert (st.L, st.S_hd, st.T) == pytest.approx((ok.at[t, "L"], ok.at[t, "S_hd"], ok.at[t, "T"]), rel=1e-12)
        for H in (60, 240):
            s = vb.sigma_over(model, st, H)
            assert s.sigma_ret == pytest.approx(ok.at[t, f"sigma_ret_{H}"], rel=1e-9)
            assert s.sigma_px == pytest.approx(ok.at[t, f"sigma_px_{H}"], rel=1e-9)
            assert s.crosses_close == bool(ok.at[t, f"crossing_{H}"])


def test_warm_up_labels_have_no_state():
    m = synthetic()
    model = vb.load_sigma_model("parquet2021_2023")
    end = pd.Timestamp("2019-03-16", tz="UTC")
    ctx = stage1.context(m, end, (60, 240), 0.90)
    tbl = stage1.state_table(ctx, model, START, end)
    first_ok = tbl.dropna(subset=["L"]).index[0]
    assert vb.state_at(model.params, m, first_ok).reason is None
    earlier = ctx.labels[ctx.labels < first_ok]                     # dropped from the table
    assert len(earlier) == vb.WINDOWS[-1]
    assert vb.state_at(model.params, m, earlier[-1]).reason == "insufficient_history"


def test_describe_and_calibration_run_on_synthetic_bars():
    m = synthetic()
    model = vb.load_sigma_model("parquet2021_2023")
    end = pd.Timestamp("2019-03-16", tz="UTC")
    ctx = stage1.context(m, end, (60, 240), 0.90)
    tbl = stage1.state_table(ctx, model, START, end)
    d = stage1.describe(tbl.dropna(), ["L", "sigma_px_60"], [0.25, 0.5, 0.75], None)
    assert list(d.columns) == [0.25, 0.5, 0.75] and list(d.index) == ["L", "sigma_px_60"]
    assert not stage1.describe(tbl.dropna(), ["L"], [0.5], "hour").empty
    cal = stage1.calibration(ctx, model, START, end, True)
    assert set(cal.index.get_level_values("H")) == {60, 240}
    assert (cal["n"] > 0).all() and np.isfinite(cal[["mean_rv_h", "rms_z", "qlike"]].to_numpy()).all()


def test_context_refuses_values_other_than_the_method():
    with pytest.raises(AssertionError, match="differ"):
        stage1.context(synthetic(days=1), END, (60,), 0.90)
    with pytest.raises(AssertionError, match="differ"):
        stage1.context(synthetic(days=1), END, (60, 240), 0.8)


def test_wrong_model_sha_is_refused():
    with pytest.raises(AssertionError, match="register has"):
        stage1.load_model("parquet2021_2023", "0" * 64)


def test_attempt_counter(tmp_path, monkeypatch):
    p = tmp_path / "ATTEMPTS.json"
    p.write_text(stage1.ATTEMPTS.read_text())
    monkeypatch.setattr(stage1, "ATTEMPTS", p)
    assert stage1.attempts()["outcome_analyses"] == 0
    assert stage1.bump_attempt("Q1 first look", "claude") == 1
    assert json.loads(p.read_text())["log"][0]["what"] == "Q1 first look"


def test_committed_counter_is_zero():
    assert stage1.attempts()["outcome_analyses"] == 0
