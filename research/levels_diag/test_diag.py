"""Diagnostic helpers.  python -m pytest research/levels_diag -q"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import diag as dg  # noqa: E402

cf, lv = dg.cf, dg.lv


def test_context_matched_placebos_share_the_real_days_class():
    rng = np.random.default_rng(12345)
    starts = list(pd.bdate_range("2019-06-03", periods=160))
    parts = [pd.date_range(pd.Timestamp(d, tz="UTC") - pd.Timedelta(hours=2), periods=1380, freq="1min") for d in starts]
    idx = parts[0].append(parts[1:])
    close = 1500 * np.exp(np.cumsum(rng.normal(0, 3e-4, len(idx))))
    op = np.r_[close[0], close[:-1]]
    hi, lo = np.maximum(op, close) + 0.05, np.minimum(op, close) - 0.05
    days = dg.day_context(lv.attach_levels(lv.day_table(idx, op, hi, lo, close)), close)
    mk = lv.Market(idx.asi8, op, hi, lo, close, np.full(len(idx), 0.004))
    test_idx = list(days.index[~days.excluded][25:])
    ctx = {di: (int(np.digitize(days.at[di, "pos"], [0.33, 0.66])), int(np.sign(days.at[di, "prev_ret"])))
           for di in test_idx if np.isfinite(days.at[di, "pos"]) and np.isfinite(days.at[di, "prev_ret"])}
    units, short = dg.build_units_ctx(days, test_idx, mk, ctx)
    by = {u["id"]: u for u in units}
    plc = [u for u in units if u["is_placebo"]]
    assert len(plc) > 50
    for u in plc:
        D, P = by[u["parent"]]["pday"], u["pday"]
        assert ctx[P] == ctx[D] and P != D
        op_p, fp = days.at[P, "open"], mk.f[int(days.at[P, "i0"])]
        assert u["side"] * (u["level"] - op_p) / (abs(u["level"]) * fp) == pytest.approx(by[u["parent"]]["dist"])


def test_two_sided_p():
    assert dg.two_sided(np.r_[np.full(500, 1.0), np.full(500, -1.0)]) == pytest.approx(1.0, abs=0.01)
    assert dg.two_sided(np.full(1000, 1.0)) < 0.01
