"""
Unit tests for Regime Classifier module.

Tests:
- No look-ahead in features
- Regime label count matches n_states
- Output shape correctness
- Serialization round-trip
- Dwell time > 1 bar minimum
"""

import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from regime_classifier.regime_config import RegimeConfig
from regime_classifier.regime_features import RegimeFeatureEngine
from regime_classifier.regime_classifier import RegimeClassifier


@pytest.fixture
def sample_ohlcv():
    """Generate synthetic OHLCV data resembling gold 1h bars."""
    np.random.seed(42)
    n = 2000
    dates = pd.date_range("2025-01-01", periods=n, freq="1h")

    # Simulate gold-like price with trending and ranging periods
    price = 2650.0
    prices = [price]
    for i in range(1, n):
        # Alternate between trending and mean-reverting
        if (i // 200) % 2 == 0:
            drift = 0.0002  # Trending up
        else:
            drift = -0.00005  # Slight mean reversion

        ret = drift + np.random.normal(0, 0.002)
        price *= (1 + ret)
        prices.append(price)

    close = np.array(prices)
    high = close * (1 + np.abs(np.random.normal(0, 0.001, n)))
    low = close * (1 - np.abs(np.random.normal(0, 0.001, n)))
    open_ = close * (1 + np.random.normal(0, 0.0005, n))

    # Ensure OHLC consistency
    high = np.maximum(high, np.maximum(open_, close))
    low = np.minimum(low, np.minimum(open_, close))

    df = pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": 100},
        index=dates,
    )
    return df


@pytest.fixture
def config():
    """Test config with smaller windows for speed."""
    return RegimeConfig(
        hurst_window=50,
        autocorr_window=30,
        variance_ratio_window=30,
        atr_percentile_window=50,
        adx_percentile_window=50,
        bb_percentile_window=50,
        lambda_grid=[1.0, 5.0, 10.0],
        sparse_max_feats_grid=[3.0, 5.0],
        jm_n_init=3,
        jm_max_iter=100,
        rf_n_estimators=50,
        walk_forward_train_months=6,
    )


class TestRegimeFeatureEngine:
    """Test feature computation."""

    def test_output_shape(self, sample_ohlcv, config):
        """Output has same number of rows as input."""
        engine = RegimeFeatureEngine(config=config)
        features = engine.compute(sample_ohlcv)
        assert len(features) == len(sample_ohlcv)

    def test_no_look_ahead(self, sample_ohlcv, config):
        """
        Features at time t must not depend on data after time t.
        Test by comparing features computed on full data vs truncated data.
        """
        engine = RegimeFeatureEngine(config=config)

        # Compute on full data
        full_features = engine.compute(sample_ohlcv)

        # Compute on first 500 bars only
        truncated = sample_ohlcv.iloc[:500]
        trunc_features = engine.compute(truncated)

        # Features at bar 499 should be identical
        # (allowing for floating point tolerance)
        valid_cols = trunc_features.columns[trunc_features.iloc[499].notna()]
        for col in valid_cols:
            val_full = full_features.iloc[499][col]
            val_trunc = trunc_features.iloc[499][col]
            if not np.isnan(val_full) and not np.isnan(val_trunc):
                assert abs(val_full - val_trunc) < 1e-10, (
                    f"Look-ahead detected in feature '{col}': "
                    f"full={val_full}, truncated={val_trunc}"
                )

    def test_nan_warmup(self, sample_ohlcv, config):
        """First rows should have NaN (warmup period), later rows should not."""
        engine = RegimeFeatureEngine(config=config)
        features = engine.compute(sample_ohlcv)

        # After sufficient warmup, most features should be valid
        warmup = config.hurst_window + 10
        valid_after_warmup = features.iloc[warmup:].notna().mean()
        # At least 80% of features should be valid after warmup
        assert valid_after_warmup.mean() > 0.8

    def test_column_names(self, sample_ohlcv, config):
        """Check expected feature columns exist."""
        engine = RegimeFeatureEngine(config=config)
        features = engine.compute(sample_ohlcv)

        expected = [
            "atr", "atr_ratio", "atr_percentile",
            "bb_width", "bb_width_percentile", "parkinson_vol",
            "adx", "adx_percentile", "di_bias", "ema_slope",
            "hurst_dfa", "autocorr_lag1",
            "variance_ratio_2", "variance_ratio_5",
            "session_asian", "session_london", "session_overlap",
            "session_ny", "time_in_session",
        ]
        for col in expected:
            assert col in features.columns, f"Missing expected column: {col}"

    def test_case_insensitive_columns(self, sample_ohlcv, config):
        """Should work with both upper and lowercase column names."""
        engine = RegimeFeatureEngine(config=config)

        # Uppercase columns
        df_upper = sample_ohlcv.copy()
        df_upper.columns = [c.upper() for c in df_upper.columns]
        features = engine.compute(df_upper)
        assert len(features) == len(df_upper)


class TestRegimeClassifier:
    """Test classifier fitting and prediction."""

    def test_regime_label_count(self, sample_ohlcv, config):
        """Number of unique regime labels should equal n_states."""
        engine = RegimeFeatureEngine(config=config)
        features = engine.compute(sample_ohlcv)

        classifier = RegimeClassifier(config=config)
        classifier.fit(features, ohlcv_df=sample_ohlcv)

        predictions = classifier.predict(features)
        unique_labels = predictions["regime_label"].dropna().unique()
        # Should have at most n_states unique labels
        valid_labels = [l for l in unique_labels if l != "UNKNOWN"]
        assert len(valid_labels) <= config.n_states
        assert len(valid_labels) >= 2  # At least 2 regimes detected

    def test_output_columns(self, sample_ohlcv, config):
        """Predict output has expected columns."""
        engine = RegimeFeatureEngine(config=config)
        features = engine.compute(sample_ohlcv)

        classifier = RegimeClassifier(config=config)
        classifier.fit(features, ohlcv_df=sample_ohlcv)

        predictions = classifier.predict(features)
        assert "regime_label" in predictions.columns

    def test_predict_next_output(self, sample_ohlcv, config):
        """predict_next returns expected columns."""
        engine = RegimeFeatureEngine(config=config)
        features = engine.compute(sample_ohlcv)

        classifier = RegimeClassifier(config=config)
        classifier.fit(features, ohlcv_df=sample_ohlcv)

        next_pred = classifier.predict_next(features)
        assert "predicted_next_regime" in next_pred.columns
        assert "prediction_confidence" in next_pred.columns
        assert len(next_pred) == len(features)

    def test_human_readable_labels(self, sample_ohlcv, config):
        """Labels should be human-readable strings, not integers."""
        engine = RegimeFeatureEngine(config=config)
        features = engine.compute(sample_ohlcv)

        classifier = RegimeClassifier(config=config)
        classifier.fit(features, ohlcv_df=sample_ohlcv)

        predictions = classifier.predict(features)
        labels = predictions["regime_label"].dropna().unique()
        valid_names = {"TRENDING", "RANGING", "VOLATILE", "UNKNOWN"}
        for l in labels:
            assert l in valid_names, f"Unexpected label: {l}"

    def test_serialization_roundtrip(self, sample_ohlcv, config):
        """Save and load should produce identical predictions."""
        engine = RegimeFeatureEngine(config=config)
        features = engine.compute(sample_ohlcv)

        classifier = RegimeClassifier(config=config)
        classifier.fit(features, ohlcv_df=sample_ohlcv)

        # Save
        with tempfile.NamedTemporaryFile(suffix=".joblib", delete=False) as f:
            path = f.name
        classifier.save(path)

        # Load
        loaded = RegimeClassifier.load(path)

        # Compare predictions
        pred_original = classifier.predict(features)
        pred_loaded = loaded.predict(features)

        pd.testing.assert_frame_equal(
            pred_original[["regime_label"]],
            pred_loaded[["regime_label"]],
        )

        # Cleanup
        Path(path).unlink()

    def test_dwell_time_minimum(self, sample_ohlcv, config):
        """
        Mean dwell time per state should be > 1 bar.
        Jump penalty ensures states persist.
        """
        # Use higher lambda to ensure persistence
        config.lambda_grid = [10.0, 20.0, 50.0]
        engine = RegimeFeatureEngine(config=config)
        features = engine.compute(sample_ohlcv)

        classifier = RegimeClassifier(config=config)
        classifier.fit(features, ohlcv_df=sample_ohlcv)

        predictions = classifier.predict(features)
        labels = predictions["regime_label"].values

        # Compute dwell times
        for state in np.unique(labels):
            if state == "UNKNOWN":
                continue
            runs = []
            current_run = 0
            for l in labels:
                if l == state:
                    current_run += 1
                else:
                    if current_run > 0:
                        runs.append(current_run)
                    current_run = 0
            if current_run > 0:
                runs.append(current_run)

            if runs:
                mean_dwell = np.mean(runs)
                assert mean_dwell > 1.0, (
                    f"State {state} has mean dwell time {mean_dwell:.1f} <= 1 bar"
                )


class TestAlignLabels:
    """Test label alignment across folds."""

    def test_alignment_preserves_count(self):
        """Aligned labels should have same unique values."""
        labels_a = np.array([0, 0, 1, 1, 2, 2, 0, 1])
        labels_b = np.array([1, 1, 0, 0, 2, 2, 1, 0])  # Swapped 0 and 1

        aligned = RegimeClassifier.align_labels_across_folds(labels_a, labels_b, 3)
        assert set(aligned) == set(labels_b[:len(aligned)])

    def test_alignment_identity(self):
        """If labels match reference, alignment should be identity."""
        labels = np.array([0, 0, 1, 1, 2, 2])
        aligned = RegimeClassifier.align_labels_across_folds(labels, labels, 3)
        np.testing.assert_array_equal(aligned, labels)
