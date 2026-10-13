"""
Regime Validator

Walk-forward expanding window validation for the regime classifier.
Reports per-fold and aggregate metrics including:
- Regime persistence (mean dwell time)
- Silhouette score (secondary)
- Sharpe of filtered vs unfiltered strategy
- Max drawdown comparison
- Win rate and payoff ratio
- Regime distribution

References:
- Nystrup et al. (2020) — walk-forward methodology for jump models
- Pomorski & Gorse (2023) — RF prediction evaluation
"""

import logging
import os
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Tuple
from concurrent.futures import ProcessPoolExecutor, as_completed

import numpy as np
import pandas as pd
from sklearn.metrics import silhouette_score

from .regime_config import RegimeConfig
from .regime_features import RegimeFeatureEngine
from .regime_classifier import RegimeClassifier

logger = logging.getLogger(__name__)


@dataclass
class FoldResult:
    """Metrics for a single walk-forward fold."""

    fold_idx: int
    train_start: str
    train_end: str
    test_start: str
    test_end: str
    n_train_bars: int
    n_test_bars: int

    # Regime quality
    mean_dwell_time: Dict[str, float] = field(default_factory=dict)
    regime_distribution: Dict[str, float] = field(default_factory=dict)
    silhouette: float = np.nan

    # Strategy comparison (filtered vs unfiltered)
    sharpe_filtered: float = np.nan
    sharpe_unfiltered: float = np.nan
    sharpe_improvement: float = np.nan
    max_dd_filtered: float = np.nan
    max_dd_unfiltered: float = np.nan
    win_rate_filtered: float = np.nan
    win_rate_unfiltered: float = np.nan
    payoff_ratio_filtered: float = np.nan
    payoff_ratio_unfiltered: float = np.nan

    # RF prediction
    rf_accuracy: float = np.nan
    rf_accuracy_per_regime: Dict[str, float] = field(default_factory=dict)

    # Model info
    best_lambda: float = np.nan
    selected_features: List[str] = field(default_factory=list)

    # Warnings
    warnings: List[str] = field(default_factory=list)


@dataclass
class ValidationReport:
    """Aggregate validation report across all folds."""

    folds: List[FoldResult] = field(default_factory=list)
    aggregate_sharpe_filtered: float = np.nan
    aggregate_sharpe_unfiltered: float = np.nan
    aggregate_sharpe_improvement: float = np.nan
    aggregate_max_dd_filtered: float = np.nan
    aggregate_max_dd_unfiltered: float = np.nan
    aggregate_mean_dwell_time: Dict[str, float] = field(default_factory=dict)
    aggregate_regime_distribution: Dict[str, float] = field(default_factory=dict)
    degenerate_folds: int = 0
    total_folds: int = 0

    def summary(self) -> str:
        """Human-readable summary."""
        lines = [
            "=" * 60,
            "REGIME CLASSIFIER VALIDATION REPORT",
            "=" * 60,
            f"Total folds: {self.total_folds}",
            f"Degenerate folds: {self.degenerate_folds}",
            "",
            "--- Strategy Performance ---",
            f"Sharpe (filtered):   {self.aggregate_sharpe_filtered:.3f}",
            f"Sharpe (unfiltered): {self.aggregate_sharpe_unfiltered:.3f}",
            f"Improvement:         {self.aggregate_sharpe_improvement:.3f}",
            f"Max DD (filtered):   {self.aggregate_max_dd_filtered:.1%}",
            f"Max DD (unfiltered): {self.aggregate_max_dd_unfiltered:.1%}",
            "",
            "--- Regime Quality ---",
            f"Mean dwell times: {self.aggregate_mean_dwell_time}",
            f"Distribution: {self.aggregate_regime_distribution}",
            "",
            "--- Per-Fold Sharpe ---",
        ]
        for fold in self.folds:
            lines.append(
                f"  Fold {fold.fold_idx}: filtered={fold.sharpe_filtered:.3f}, "
                f"unfiltered={fold.sharpe_unfiltered:.3f}, "
                f"lambda={fold.best_lambda:.1f}"
            )
        lines.append("=" * 60)
        return "\n".join(lines)


class RegimeValidator:
    """
    Walk-forward validation for RegimeClassifier.

    Expanding window: 12-month initial training, 1-month test,
    1-month embargo between train and test.
    """

    def __init__(self, config: Optional[RegimeConfig] = None):
        self.config = config or RegimeConfig()
        self.feature_engine = RegimeFeatureEngine(config=self.config)

    def run_walk_forward(
        self,
        ohlcv_df: pd.DataFrame,
        feature_df: Optional[pd.DataFrame] = None,
    ) -> ValidationReport:
        """
        Run walk-forward expanding window validation.

        Args:
            ohlcv_df: OHLCV DataFrame with DatetimeIndex
            feature_df: Pre-computed features (if None, computes per-fold)

        Returns:
            ValidationReport with per-fold and aggregate metrics
        """
        # Normalize columns
        ohlcv = ohlcv_df.copy()
        ohlcv.columns = [c.lower() for c in ohlcv.columns]

        # Compute returns
        returns = np.log(ohlcv["close"] / ohlcv["close"].shift(1))

        # Compute features once on full dataset — all features are backward-looking
        # (rolling windows, .shift(1), etc.) so slicing feature_df[index < test_end]
        # per fold gives identical results to recomputing per fold, but much faster.
        if feature_df is None:
            logger.info("Computing features on full dataset (backward-looking only)...")
            feature_df = self.feature_engine.compute(ohlcv)

        # Generate fold boundaries
        folds = self._generate_folds(ohlcv.index)
        logger.info(f"Generated {len(folds)} walk-forward folds")

        report = ValidationReport(total_folds=len(folds))

        # Run folds — use parallel processing if multiple cores available
        n_workers = min(len(folds), max(1, os.cpu_count() - 1))

        if n_workers > 1 and len(folds) > 2:
            logger.info(f"Running {len(folds)} folds in parallel ({n_workers} workers)")
            fold_results = self._run_folds_parallel(
                folds, ohlcv, feature_df, returns, n_workers
            )
        else:
            fold_results = self._run_folds_sequential(
                folds, ohlcv, feature_df, returns
            )

        for fold_result in fold_results:
            report.folds.append(fold_result)

            # Check for degenerate solutions
            for state, pct in fold_result.regime_distribution.items():
                if pct < 0.10 or pct > 0.70:
                    report.degenerate_folds += 1
                    fold_result.warnings.append(
                        f"Degenerate: {state} = {pct:.1%} of bars"
                    )
                    break

        # Aggregate metrics
        self._compute_aggregate_metrics(report)
        return report

    def _run_folds_sequential(self, folds, ohlcv, feature_df, returns):
        """Run folds sequentially."""
        results = []
        for fold_idx, (train_start, train_end, test_start, test_end) in enumerate(folds):
            logger.info(
                f"Fold {fold_idx + 1}/{len(folds)}: "
                f"train [{train_start.date()}..{train_end.date()}], "
                f"test [{test_start.date()}..{test_end.date()}]"
            )
            # Slice features up to test_end to prevent look-ahead
            fold_features = feature_df.loc[feature_df.index < test_end]
            result = self._run_single_fold(
                fold_idx=fold_idx, ohlcv=ohlcv, feature_df=fold_features,
                returns=returns, train_start=train_start, train_end=train_end,
                test_start=test_start, test_end=test_end, prev_labels=None,
            )
            results.append(result)
        return results

    def _run_folds_parallel(self, folds, ohlcv, feature_df, returns, n_workers):
        """Run folds in parallel using thread pool."""
        from concurrent.futures import ThreadPoolExecutor

        results = [None] * len(folds)

        def run_fold(fold_idx, train_start, train_end, test_start, test_end):
            logger.info(
                f"Fold {fold_idx + 1}/{len(folds)}: "
                f"train [{train_start.date()}..{train_end.date()}], "
                f"test [{test_start.date()}..{test_end.date()}]"
            )
            fold_features = feature_df.loc[feature_df.index < test_end]
            return self._run_single_fold(
                fold_idx=fold_idx, ohlcv=ohlcv, feature_df=fold_features,
                returns=returns, train_start=train_start, train_end=train_end,
                test_start=test_start, test_end=test_end, prev_labels=None,
            )

        with ThreadPoolExecutor(max_workers=n_workers) as executor:
            futures = {}
            for fold_idx, (train_start, train_end, test_start, test_end) in enumerate(folds):
                future = executor.submit(
                    run_fold, fold_idx, train_start, train_end, test_start, test_end
                )
                futures[future] = fold_idx

            for future in as_completed(futures):
                fold_idx = futures[future]
                results[fold_idx] = future.result()

        return results

    def _generate_folds(
        self, index: pd.DatetimeIndex
    ) -> List[Tuple[pd.Timestamp, pd.Timestamp, pd.Timestamp, pd.Timestamp]]:
        """
        Generate walk-forward fold boundaries.

        Expanding window strategy:
        - Train start is always fixed at data start
        - Train end expands each fold (absorbs previous test data)
        - Embargo gap between train and test prevents leakage
        - Test window slides forward by test_months each fold

        Example with train=6mo, test=1mo, embargo=1mo:
          Fold 1: train [Jan..Jun], embargo Jul, test [Aug..Aug]
          Fold 2: train [Jan..Aug], embargo Sep, test [Oct..Oct]
          Fold 3: train [Jan..Oct], embargo Nov, test [Dec..Dec]
        """
        start_date = index.min()
        end_date = index.max()

        folds = []
        current_train_end = start_date + pd.DateOffset(
            months=self.config.walk_forward_train_months
        )

        while True:
            embargo_end = current_train_end + pd.DateOffset(
                months=self.config.embargo_months
            )
            test_start = embargo_end
            test_end = test_start + pd.DateOffset(
                months=self.config.walk_forward_test_months
            )

            if test_end > end_date:
                break

            folds.append((start_date, current_train_end, test_start, test_end))

            # Expand: absorb test data into next fold's training set
            current_train_end = test_end

        return folds

    def _run_single_fold(
        self,
        fold_idx: int,
        ohlcv: pd.DataFrame,
        feature_df: pd.DataFrame,
        returns: pd.Series,
        train_start: pd.Timestamp,
        train_end: pd.Timestamp,
        test_start: pd.Timestamp,
        test_end: pd.Timestamp,
        prev_labels: Optional[np.ndarray],
    ) -> FoldResult:
        """Run classifier on a single fold and compute metrics."""
        # Split data using timestamp ranges (feature_df may be truncated per-fold)
        train_mask = (ohlcv.index >= train_start) & (ohlcv.index < train_end)
        test_mask = (ohlcv.index >= test_start) & (ohlcv.index < test_end)

        feat_train = feature_df.loc[
            (feature_df.index >= train_start) & (feature_df.index < train_end)
        ]
        feat_test = feature_df.loc[
            (feature_df.index >= test_start) & (feature_df.index < test_end)
        ]
        ret_train = returns.loc[train_mask]
        ret_test = returns.loc[test_mask]
        ohlcv_train = ohlcv.loc[train_mask]

        result = FoldResult(
            fold_idx=fold_idx,
            train_start=str(train_start.date()),
            train_end=str(train_end.date()),
            test_start=str(test_start.date()),
            test_end=str(test_end.date()),
            n_train_bars=len(feat_train),
            n_test_bars=len(feat_test),
        )

        if len(feat_train) < 200 or len(feat_test) < 50:
            result.warnings.append("Insufficient data")
            return result

        # Fit classifier on training data
        try:
            classifier = RegimeClassifier(config=self.config)
            classifier.fit(feat_train, returns=ret_train, ohlcv_df=ohlcv_train)
        except Exception as e:
            result.warnings.append(f"Fit failed: {e}")
            return result

        result.best_lambda = classifier.best_lambda_
        result.selected_features = classifier.selected_features_ or []

        # Predict on test data
        try:
            predictions = classifier.predict(feat_test)
            test_labels = predictions["regime_label"].values
        except Exception as e:
            result.warnings.append(f"Predict failed: {e}")
            return result

        # Align labels with previous fold if available
        if prev_labels is not None and classifier.jump_model_ is not None:
            try:
                train_labels = classifier.jump_model_.labels_
                aligned = RegimeClassifier.align_labels_across_folds(
                    train_labels, prev_labels, self.config.n_states
                )
            except Exception:
                pass

        # Compute metrics
        result.mean_dwell_time = self._compute_dwell_times(test_labels)
        result.regime_distribution = self._compute_distribution(test_labels)

        # Silhouette score (secondary metric)
        try:
            valid_feat = feat_test.dropna()
            if len(valid_feat) > 50:
                labels_for_sil = test_labels[:len(valid_feat)]
                if len(np.unique(labels_for_sil)) > 1:
                    result.silhouette = silhouette_score(
                        valid_feat.values[:len(labels_for_sil)],
                        labels_for_sil,
                        sample_size=min(1000, len(labels_for_sil)),
                    )
        except Exception:
            pass

        # Strategy comparison (pass full ohlcv for EMA pre-warming)
        strategy_metrics = self._compute_strategy_metrics(
            test_labels, ret_test, ohlcv.loc[test_mask], ohlcv_full=ohlcv
        )
        result.sharpe_filtered = strategy_metrics["sharpe_filtered"]
        result.sharpe_unfiltered = strategy_metrics["sharpe_unfiltered"]
        result.sharpe_improvement = (
            result.sharpe_filtered - result.sharpe_unfiltered
        )
        result.max_dd_filtered = strategy_metrics["max_dd_filtered"]
        result.max_dd_unfiltered = strategy_metrics["max_dd_unfiltered"]
        result.win_rate_filtered = strategy_metrics["win_rate_filtered"]
        result.win_rate_unfiltered = strategy_metrics["win_rate_unfiltered"]
        result.payoff_ratio_filtered = strategy_metrics["payoff_ratio_filtered"]
        result.payoff_ratio_unfiltered = strategy_metrics["payoff_ratio_unfiltered"]

        # RF prediction accuracy
        try:
            next_pred = classifier.predict_next(feat_test)
            # Compare predicted next regime to actual next regime
            actual_next = np.roll(test_labels, -1)
            actual_next[-1] = test_labels[-1]
            predicted = next_pred["predicted_next_regime"].values

            valid = predicted != "UNKNOWN"
            if valid.sum() > 0:
                result.rf_accuracy = np.mean(predicted[valid] == actual_next[valid])
        except Exception:
            pass

        return result

    def _compute_dwell_times(self, labels: np.ndarray) -> Dict[str, float]:
        """Mean consecutive bars in each regime state."""
        dwell_times = {}
        for state in np.unique(labels):
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
            dwell_times[str(state)] = np.mean(runs) if runs else 0
        return dwell_times

    def _compute_distribution(self, labels: np.ndarray) -> Dict[str, float]:
        """Percentage of bars in each regime."""
        total = len(labels)
        dist = {}
        for state in np.unique(labels):
            dist[str(state)] = np.sum(labels == state) / total
        return dist

    def _compute_strategy_metrics(
        self,
        labels: np.ndarray,
        returns: pd.Series,
        ohlcv: pd.DataFrame,
        ohlcv_full: Optional[pd.DataFrame] = None,
    ) -> Dict[str, float]:
        """
        Compare EMA crossover filtered by TRENDING vs unfiltered.

        Pre-warms EMAs using trailing data from the full OHLCV to avoid
        initialization bias at the start of the test window.
        """
        close = ohlcv["close"]

        # Pre-warm EMAs: use trailing bars from before the test period
        warmup_bars = self.config.ema_slow * 3  # 3x slow EMA span
        if ohlcv_full is not None:
            test_start_loc = ohlcv_full.index.get_loc(ohlcv.index[0])
            warmup_start = max(0, test_start_loc - warmup_bars)
            warmup_close = ohlcv_full["close"].iloc[warmup_start:test_start_loc + len(close)]
            ema_fast_full = warmup_close.ewm(span=self.config.ema_fast, adjust=False).mean()
            ema_slow_full = warmup_close.ewm(span=self.config.ema_slow, adjust=False).mean()
            # Slice back to test period only
            ema_fast = ema_fast_full.iloc[-len(close):]
            ema_slow = ema_slow_full.iloc[-len(close):]
        else:
            ema_fast = close.ewm(span=self.config.ema_fast, adjust=False).mean()
            ema_slow = close.ewm(span=self.config.ema_slow, adjust=False).mean()

        # Base signal (unfiltered)
        signal = (ema_fast > ema_slow).astype(float).shift(1).fillna(0)
        unfiltered_returns = signal.values * returns.values

        # Filtered signal (only in TRENDING regime)
        trending_mask = (labels == "TRENDING").astype(float)
        filtered_signal = signal.values * trending_mask
        filtered_returns = filtered_signal * returns.values

        bars_per_year = 252 * 24  # Hourly approximation

        def sharpe(rets):
            m = np.nanmean(rets)
            s = np.nanstd(rets)
            return (m / s) * np.sqrt(bars_per_year) if s > 0 else 0.0

        def max_drawdown(rets):
            cum = np.nancumsum(rets)
            running_max = np.maximum.accumulate(cum)
            dd = cum - running_max
            return np.min(dd) if len(dd) > 0 else 0.0

        def win_rate(rets):
            trades = rets[rets != 0]
            return np.mean(trades > 0) if len(trades) > 0 else 0.0

        def payoff_ratio(rets):
            trades = rets[rets != 0]
            wins = trades[trades > 0]
            losses = trades[trades < 0]
            avg_win = np.mean(wins) if len(wins) > 0 else 0
            avg_loss = np.abs(np.mean(losses)) if len(losses) > 0 else 1
            return avg_win / avg_loss if avg_loss > 0 else 0.0

        return {
            "sharpe_filtered": sharpe(filtered_returns),
            "sharpe_unfiltered": sharpe(unfiltered_returns),
            "max_dd_filtered": max_drawdown(filtered_returns),
            "max_dd_unfiltered": max_drawdown(unfiltered_returns),
            "win_rate_filtered": win_rate(filtered_returns),
            "win_rate_unfiltered": win_rate(unfiltered_returns),
            "payoff_ratio_filtered": payoff_ratio(filtered_returns),
            "payoff_ratio_unfiltered": payoff_ratio(unfiltered_returns),
        }

    def _compute_aggregate_metrics(self, report: ValidationReport) -> None:
        """Compute aggregate metrics across all folds."""
        valid_folds = [f for f in report.folds if not np.isnan(f.sharpe_filtered)]

        if not valid_folds:
            return

        report.aggregate_sharpe_filtered = np.mean(
            [f.sharpe_filtered for f in valid_folds]
        )
        report.aggregate_sharpe_unfiltered = np.mean(
            [f.sharpe_unfiltered for f in valid_folds]
        )
        report.aggregate_sharpe_improvement = (
            report.aggregate_sharpe_filtered - report.aggregate_sharpe_unfiltered
        )
        report.aggregate_max_dd_filtered = np.min(
            [f.max_dd_filtered for f in valid_folds]
        )
        report.aggregate_max_dd_unfiltered = np.min(
            [f.max_dd_unfiltered for f in valid_folds]
        )

        # Aggregate dwell times and distribution
        all_dwell = {}
        all_dist = {}
        for f in valid_folds:
            for state, val in f.mean_dwell_time.items():
                all_dwell.setdefault(state, []).append(val)
            for state, val in f.regime_distribution.items():
                all_dist.setdefault(state, []).append(val)

        report.aggregate_mean_dwell_time = {
            k: np.mean(v) for k, v in all_dwell.items()
        }
        report.aggregate_regime_distribution = {
            k: np.mean(v) for k, v in all_dist.items()
        }

    # =========================================================================
    # Plotting
    # =========================================================================

    def plot_regimes(
        self,
        ohlcv_df: pd.DataFrame,
        regime_df: pd.DataFrame,
        title: str = "XAUUSD Regime Classification",
        save_path: Optional[str] = None,
    ) -> None:
        """
        Price chart with background-colored regime periods.
        Green = TRENDING, Yellow = RANGING, Red = VOLATILE.
        """
        import matplotlib.pyplot as plt
        import matplotlib.dates as mdates

        fig, (ax1, ax2) = plt.subplots(
            2, 1, figsize=(16, 10), height_ratios=[3, 1], sharex=True
        )

        ohlcv = ohlcv_df.copy()
        ohlcv.columns = [c.lower() for c in ohlcv.columns]

        # Plot price
        ax1.plot(ohlcv.index, ohlcv["close"], color="white", linewidth=0.8)
        ax1.set_ylabel("Price")
        ax1.set_title(title)
        ax1.set_facecolor("#1e1e1e")
        fig.patch.set_facecolor("#1e1e1e")

        # Color background by regime
        colors = {
            "TRENDING": (0.15, 0.65, 0.40, 0.3),   # Green
            "RANGING": (0.95, 0.85, 0.20, 0.3),     # Yellow
            "VOLATILE": (0.95, 0.30, 0.30, 0.3),    # Red
        }

        labels = regime_df["regime_label"].values
        for i in range(len(labels) - 1):
            color = colors.get(labels[i], (0.5, 0.5, 0.5, 0.1))
            ax1.axvspan(ohlcv.index[i], ohlcv.index[i + 1], color=color)

        # Regime timeline
        regime_numeric = np.where(
            labels == "TRENDING", 2,
            np.where(labels == "VOLATILE", 1, 0)
        )
        ax2.fill_between(
            ohlcv.index[:len(regime_numeric)],
            regime_numeric,
            alpha=0.7,
            step="post",
            color="cyan",
        )
        ax2.set_ylabel("Regime")
        ax2.set_yticks([0, 1, 2])
        ax2.set_yticklabels(["RANGING", "VOLATILE", "TRENDING"])
        ax2.set_facecolor("#1e1e1e")

        # Styling
        for ax in [ax1, ax2]:
            ax.tick_params(colors="white")
            ax.xaxis.label.set_color("white")
            ax.yaxis.label.set_color("white")
            ax.title.set_color("white")
            ax.spines["bottom"].set_color("#333")
            ax.spines["top"].set_color("#333")
            ax.spines["left"].set_color("#333")
            ax.spines["right"].set_color("#333")

        plt.tight_layout()

        if save_path:
            plt.savefig(save_path, dpi=150, bbox_inches="tight", facecolor="#1e1e1e")
            logger.info(f"Regime plot saved to {save_path}")
        else:
            plt.show()

        plt.close()

    def plot_validation_summary(
        self,
        report: ValidationReport,
        save_path: Optional[str] = None,
    ) -> None:
        """Sharpe and drawdown per fold chart."""
        import matplotlib.pyplot as plt

        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 8))

        folds = [f for f in report.folds if not np.isnan(f.sharpe_filtered)]
        fold_nums = [f.fold_idx + 1 for f in folds]

        # Sharpe comparison
        ax1.bar(
            [x - 0.2 for x in fold_nums],
            [f.sharpe_filtered for f in folds],
            width=0.4, label="Filtered (TRENDING only)", color="#26a69a",
        )
        ax1.bar(
            [x + 0.2 for x in fold_nums],
            [f.sharpe_unfiltered for f in folds],
            width=0.4, label="Unfiltered", color="#ef5350",
        )
        ax1.axhline(y=0, color="white", linewidth=0.5, linestyle="--")
        ax1.set_xlabel("Fold")
        ax1.set_ylabel("Sharpe Ratio")
        ax1.set_title("Strategy Sharpe: Regime-Filtered vs Unfiltered")
        ax1.legend()
        ax1.set_facecolor("#1e1e1e")

        # Max drawdown comparison
        ax2.bar(
            [x - 0.2 for x in fold_nums],
            [f.max_dd_filtered for f in folds],
            width=0.4, label="Filtered", color="#26a69a",
        )
        ax2.bar(
            [x + 0.2 for x in fold_nums],
            [f.max_dd_unfiltered for f in folds],
            width=0.4, label="Unfiltered", color="#ef5350",
        )
        ax2.set_xlabel("Fold")
        ax2.set_ylabel("Max Drawdown")
        ax2.set_title("Maximum Drawdown per Fold")
        ax2.legend()
        ax2.set_facecolor("#1e1e1e")

        fig.patch.set_facecolor("#1e1e1e")
        for ax in [ax1, ax2]:
            ax.tick_params(colors="white")
            ax.xaxis.label.set_color("white")
            ax.yaxis.label.set_color("white")
            ax.title.set_color("white")

        plt.tight_layout()

        if save_path:
            plt.savefig(save_path, dpi=150, bbox_inches="tight", facecolor="#1e1e1e")
            logger.info(f"Validation summary saved to {save_path}")
        else:
            plt.show()

        plt.close()
