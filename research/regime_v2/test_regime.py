"""
Regime v2 tests.   backend/venv/bin/python3 -m pytest research/regime_v2 -q
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_regime as rr  # noqa: E402
from jump import JumpModel, sqdist, order_by, mean_duration  # noqa: E402


def _two_state(n_seg=16, seg=400, noise=0.7, seed=12345):
    rng = np.random.default_rng(seed)
    s = np.repeat(np.arange(n_seg) % 2, seg)
    X = np.c_[2.0 * s, -1.5 * s] + rng.normal(0, noise, (len(s), 2))
    return X, s


def test_offline_recovers_states_and_causal_lags_then_matches():
    X, s = _two_state()
    jm = JumpModel(K=2, lam=20, seed=1).fit(X)
    off = jm.offline_labels(X)
    flip = (off != s).mean() > 0.5
    off = 1 - off if flip else off
    assert (off == s).mean() > 0.95
    causal = jm.causal_labels(X)
    causal = 1 - causal if flip else causal
    switches = np.flatnonzero(np.diff(s)) + 1
    lags = []
    for i in switches:
        j = np.flatnonzero(causal[i:i + 100] == s[i])
        assert len(j), f"causal never reached the new state after switch at {i}"
        lags.append(j[0])
    assert max(lags) > 0                                   # causal labels do lag
    settled = np.ones(len(s), bool)
    for i in switches:
        settled[i:i + 30] = False
    assert (causal[settled] == s[settled]).mean() > 0.95   # and match once settled


def test_lambda_zero_is_nearest_centroid():
    X, _ = _two_state(seed=3)
    jm = JumpModel(K=3, lam=0.0, seed=2).fit(X)
    nearest = np.argmin(sqdist(X, jm.centroids_), axis=1)
    np.testing.assert_array_equal(jm.offline_labels(X), nearest)
    np.testing.assert_array_equal(jm.causal_labels(X), nearest)


def test_causal_labels_unchanged_when_data_is_appended():
    X, _ = _two_state(seed=5)
    jm = JumpModel(K=3, lam=5, seed=4).fit(X[:3000])
    full = jm.causal_labels(X)
    rng = np.random.default_rng(12345)
    for t in rng.choice(np.arange(10, len(X)), 20, replace=False):
        np.testing.assert_array_equal(jm.causal_labels(X[:t]), full[:t])


def test_naming_is_by_training_mean_of_L():
    rng = np.random.default_rng(12345)
    L = np.r_[rng.normal(5, .1, 100), rng.normal(-3, .1, 100), rng.normal(1, .1, 100)]
    raw = np.r_[np.zeros(100, int), np.ones(100, int), np.full(100, 2)]
    rank = order_by(L, raw, 3)
    assert list(rank) == [2, 0, 1]                      # state 1 (lowest L) -> CALM=0
    assert [rr.NAMES3[r] for r in rank] == ["STRESSED", "CALM", "NORMAL"]


def test_scaler_and_lambda_selection_use_only_training_rows():
    X, _ = _two_state(n_seg=12, seed=7)
    n = len(X)
    fit_mask = np.arange(n) < 3200
    val_mask = (np.arange(n) >= 3300) & (np.arange(n) < 4000)
    y = X[:, 0] + np.random.default_rng(1).normal(0, 0.3, n)
    upto = np.arange(n) < 4000                          # training window ends at row 4000
    Xs, mu, sd = rr.standardize(X[upto][fit_mask[upto]], X[upto])
    lam1, _ = rr.select_lambda(Xs, fit_mask[upto], val_mask[upto], y[upto], 2, 1, "A",
                               grid=[0.0, 5.0, 50.0])
    X2 = X.copy()
    X2[4000:] += 100.0                                   # rows after the training window
    y2 = y.copy()
    y2[4000:] = -50.0
    Xs2, mu2, sd2 = rr.standardize(X2[upto][fit_mask[upto]], X2[upto])
    lam2, _ = rr.select_lambda(Xs2, fit_mask[upto], val_mask[upto], y2[upto], 2, 1, "A",
                               grid=[0.0, 5.0, 50.0])
    assert lam1 == lam2
    np.testing.assert_array_equal(mu, mu2)
    np.testing.assert_array_equal(sd, sd2)


def test_conditional_forecast_uses_only_training_means():
    lab_tr = np.array([0, 0, 1, 1, 2])
    y_tr = np.array([1.0, 3.0, 10.0, 12.0, 7.0])
    lab_te = np.array([1, 0, 2, 5])
    f = rr.cond_mean_forecast(lab_tr, y_tr, lab_te)
    np.testing.assert_allclose(f, [11.0, 2.0, 7.0, y_tr.mean()])      # unseen label -> training mean
    # test outcomes never enter: the function doesn't even receive them


def test_mean_duration():
    assert mean_duration(np.array([0, 0, 0, 1, 1, 0])) == 2.0


def test_sealed_assert_fires():
    bad = pd.DataFrame({"x": [1.0]}, index=pd.DatetimeIndex(["2024-01-02"], tz="UTC"))
    with pytest.raises(AssertionError, match="SEALED"):
        rr.r3.assert_sealed(bad)


def test_fit_rejects_nan():
    X, _ = _two_state()
    X[5, 0] = np.nan
    with pytest.raises(ValueError):
        JumpModel(K=2, lam=1).fit(X)
