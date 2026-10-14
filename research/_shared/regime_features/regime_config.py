"""
Regime Classifier Configuration

All hyperparameters as a single config dataclass.
"""

from dataclasses import dataclass, field
from typing import List


@dataclass
class RegimeConfig:
    """Configuration for the regime classification pipeline."""

    # Jump Model
    n_states: int = 3
    lambda_grid: List[float] = field(
        default_factory=lambda: [0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 20.0, 50.0, 100.0]
    )
    jm_max_iter: int = 1000
    jm_n_init: int = 10
    jm_tol: float = 1e-8

    # Sparse Jump Model
    sparse_max_feats_grid: List[float] = field(
        default_factory=lambda: [3.0, 5.0, 8.0, 12.0, 20.0]
    )
    sparse_max_iter: int = 30

    # Feature computation
    atr_period: int = 14
    adx_period: int = 14
    ema_period: int = 20
    bb_period: int = 20
    bb_std: float = 2.0
    hurst_window: int = 100
    autocorr_window: int = 50
    variance_ratio_window: int = 50
    rv_windows: List[int] = field(default_factory=lambda: [15, 60, 240])
    atr_percentile_window: int = 100
    adx_percentile_window: int = 100
    bb_percentile_window: int = 100
    parkinson_window: int = 20

    # Session definitions (UTC hours)
    # Asian: 00:00-08:00, London: 08:00-12:00, Overlap: 12:00-16:00, NY: 16:00-21:00
    session_asian: tuple = (0, 8)
    session_london: tuple = (8, 12)
    session_overlap: tuple = (12, 16)
    session_ny: tuple = (16, 21)

    # Random Forest prediction layer
    rf_n_estimators: int = 200
    rf_max_depth: int = 8
    rf_max_features: str = "sqrt"
    rf_min_samples_leaf: int = 20
    rf_random_state: int = 42

    # Walk-forward validation
    walk_forward_train_months: int = 12
    walk_forward_test_months: int = 1
    embargo_months: int = 1

    # Preprocessing
    clip_std: float = 3.0

    # EMA crossover strategy (for Sharpe-based lambda tuning)
    ema_fast: int = 10
    ema_slow: int = 30

    # Regime state names (ordered by assignment after fitting)
    state_names: List[str] = field(
        default_factory=lambda: ["TRENDING", "RANGING", "VOLATILE"]
    )
