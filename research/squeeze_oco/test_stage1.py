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


def test_dev_guard_with_the_owners_2012_start():
    start = pd.Timestamp("2012-01-01", tz="UTC")
    with pytest.raises(AssertionError, match="OUTSIDE"):
        stage1.dev_guard(pd.DatetimeIndex(["2011-12-30 21:59"], tz="UTC"), start, END, "x")
    stage1.dev_guard(pd.DatetimeIndex(["2012-01-02 23:00"], tz="UTC"), start, END, "x")


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


# --- data check and Q1 counts ---------------------------------------------------------

def bars_at(times):
    idx = pd.DatetimeIndex(sorted(times), name="timestamp")
    return pd.DataFrame({"open": 1.0, "high": 1.1, "low": 0.9, "close": 1.0}, index=idx)


def with_break(first, days):
    """2015+-style: every open minute (16:59 NY is the last bar before the break)."""
    t = pd.Timestamp(first, tz="UTC")
    return [t + pd.Timedelta(minutes=k) for k in range(days * 1440)
            if _market_open((t + pd.Timedelta(minutes=k)).to_pydatetime())]


def continuous(first, days):
    """2007-2011-style: every minute Mon 00:00 .. Fri 23:59 UTC, no daily break."""
    t = pd.Timestamp(first, tz="UTC")
    return [t + pd.Timedelta(minutes=k) for k in range(days * 1440)
            if (t + pd.Timedelta(minutes=k)).weekday() <= 4]


def test_break_table_tells_a_daily_break_from_none():
    b = stage1.break_table(bars_at(with_break("2019-06-02", 14)))     # June: US DST
    row = b.loc[(2019, "DST")]
    assert row["break_min_mode"] == 61 and row["break_share"] == 1.0
    assert (row["last_bar_mode_utc"], row["first_bar_mode_utc"]) == ("20:59", "22:00")
    c = stage1.break_table(bars_at(continuous("2008-06-02", 14)))
    assert c.loc[(2008, "DST")]["break_min_mode"] == 1
    w = stage1.break_table(bars_at(with_break("2019-01-06", 14)))     # January: standard time
    assert (w.loc[(2019, "STD")]["last_bar_mode_utc"], w.loc[(2019, "STD")]["first_bar_mode_utc"]) == ("21:59", "23:00")


def test_holiday_table_counts_by_utc_date_and_hour():
    xmas = pd.Timestamp("2018-12-25", tz="UTC")
    closed = [t for t in with_break("2018-12-23", 4)                   # 2012+-style: closed on the day,
              if t.normalize() != xmas or t.hour == 23]                # reopening at 23:00 UTC
    times = closed + continuous("2008-12-22", 5)
    h = stage1.holiday_table(bars_at(times), range(2008, 2020))
    assert h.loc[2018, "bars_dec_25"] == 60 and h.loc[2018, "holiday_bars_at_23_utc"] == 60   # the 23:00 reopening only
    assert h.loc[2018, "holiday_bars_other_hours"] == 0
    assert h.loc[2008, "bars_dec_25"] == 1440 and h.loc[2008, "holiday_bars_other_hours"] == 1380
    assert h.loc[2019, "good_friday"] == "2019-04-19" and h.loc[2008, "good_friday"] == "2008-03-21"
    assert (h["bars_on_utc_saturdays"] == 0).all()


def test_bars_per_trade_date_and_weekend_trade_dates():
    b = stage1.bars_per_trade_date(bars_at(with_break("2019-06-02", 14)))
    assert b.loc[2019, "median_bars_per_trade_date"] == 1380 and b.loc[2019, "weekend_trade_dates"] == 0
    c = stage1.bars_per_trade_date(bars_at(continuous("2008-06-02", 14)))
    assert c.loc[2008, "weekend_trade_dates"] == 2                    # Friday evenings roll to Saturday's date


def test_candle_and_label_counts():
    m = bars_at(with_break("2019-06-03", 1))                          # a Monday: open but for the 21:00 hour
    c15, c30 = stage1.candle_counts(m, 15), stage1.candle_counts(m, 30)
    assert c15.loc[2019, "buckets_15m_complete"] == 92 and c15.loc[2019, "buckets_15m_complete_ending_on_hour"] == 23
    assert c30.loc[2019, "buckets_30m_complete"] == 46 and c30.loc[2019, "buckets_30m_complete_ending_on_hour"] == 23
    gap = m.drop(m.index[5])                                          # one missing minute
    assert stage1.candle_counts(gap, 15).loc[2019, "buckets_15m_complete"] == 91
    with pytest.raises(ValueError):
        stage1.candle_counts(m, 7)
    assert stage1.label_counts(m).loc[2019] == 23


def test_committed_counts_hold_counts_only():
    d = json.loads((Path(__file__).resolve().parent / "results" / "q1_event_counts.json").read_text())
    for v in [*d["per_year"].values(), d["totals"], *d["subtotals"].values()]:
        assert all(isinstance(x, int) for x in v.values()), v
    t, s = d["totals"], d["subtotals"]
    assert all(t[k] == s["2007-2011"][k] + s["2012-2020"][k] for k in t)
    assert set(d["per_year"]) == {str(y) for y in range(2007, 2021)}


def test_q1_counts_reads_the_notebook_register():
    import importlib.util
    spec = importlib.util.spec_from_file_location("q1_counts", Path(__file__).resolve().parent / "q1_counts.py")
    q = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(q)
    ns = q.register()
    need = ns["need"]
    assert need("DEV_START") == "2012-01-01T00:00:00+00:00" and need("DEV_END_EXCL") == "2021-01-01T00:00:00+00:00"
    assert need("HORIZONS") == (60, 240)
    assert need("L_TERCILES") == (-0.4144399979178456, -0.018438540066194775)       # PR #42's file, :6-7
    assert "2007-2011 excluded" in need("DATA_CHECK_ACCEPTED") and need("Q1_SCORING")["test"].startswith("A: sigma_compare")
    # the owner's Q1 decisions (decision log #21-#25) and the rule's result (#28)
    assert need("Q1_TIMEFRAME") == 15 and need("Q1_SMALL_DEF")["option"] == "B"
    assert need("Q1_H") == {"primary": 240, "secondary": 60, "multiplicity": "BH across the two"}
    assert (need("Q1_SMALL_Q"), need("Q1_RUN_LENGTH")) == (0.20, 4)
    assert need("Q1_THRESHOLD_KIND")["hour_tz"] == "America/New_York"                  # decision log #31
    assert "349" in ns["_REG"]["Q1_SMALL_Q"]["source"]
    assert need("Q1_NW_LAG_240") == need("Q1_NW_LAG_60") == 8 and "pinned" in need("Q1_AUG_FIT")
    assert ns["_REG"]["Q1_SMALL_Q"]["status"] == ns["_REG"]["Q1_RUN_LENGTH"]["status"] == "APPROVED-BY-RULE"
    for open_id in ("Q2_SEED",):
        with pytest.raises(ns["RegisterError"], match="OPEN"):
            need(open_id)
    for proposed in ("Q1_FOLDS", "Q1_AUG_ESTIMATOR"):
        with pytest.raises(ns["RegisterError"], match="PROPOSED"):
            need(proposed)
    statuses = {r["id"]: r["status"] for r in ns["REGISTER"]}
    assert statuses["EVAL_GRID"] == statuses["DESCRIBE_QUANTILES"] == statuses["DESCRIBE_HOUR"] == "APPROVED"
    assert sorted(i for i, st in statuses.items() if st == "PROPOSED") == ["Q1_AUG_ESTIMATOR", "Q1_FOLDS"]
    assert not [i for i, st in statuses.items() if st == "OPEN" and i.startswith("Q1_")]           # Q1: nothing OPEN
    assert all(r["value"] is None for r in ns["REGISTER"] if r["status"] == "OPEN")      # no OPEN item filled


def test_describe_names_its_groups_and_refuses_others():
    idx = pd.date_range("2019-03-04 01:00", periods=48, freq="1h", tz="UTC")
    tbl = pd.DataFrame({"L": np.arange(48.0)}, index=idx)
    assert stage1.describe(tbl, ["L"], [0.5], "hour").index.name == "hour_utc"
    assert stage1.describe(tbl, ["L"], [0.5], "year").index.name == "year"
    with pytest.raises(ValueError):
        stage1.describe(tbl, ["L"], [0.5], "month")
