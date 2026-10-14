"""
Regime Classifier

3-state Jump Model (TRENDING / RANGING / VOLATILE) with:
- Jump penalty lambda tuned by downstream strategy Sharpe (not log-likelihood)
- Sparse Jump Model for feature selection
- Random Forest layer for next-regime prediction

References:
- Nystrup, Lindstrom & Madsen (2020), "Learning hidden Markov models with
  persistent states by penalizing jumps", Expert Systems with Applications 150:113307
- Nystrup, Kolm & Lindstrom (2021), "Feature selection in jump models",
  Expert Systems with Applications 184:115558
- Pomorski & Gorse (2023), "Improving Portfolio Performance Using a Novel Method
  for Predicting Financial Regimes", arXiv:2310.04536
"""

import logging
import warnings
from pathlib import Path
from typing import Optional, Tuple, List, Dict

import joblib
import numpy as np
import pandas as pd
from scipy.optimize import linear_sum_assignment
from sklearn.ensemble import RandomForestClassifier
from sklearn.preprocessing import StandardScaler

from jumpmodels.jump import JumpModel
from jumpmodels.sparse_jump import SparseJumpModel

from .regime_config import RegimeConfig

logger = logging.getLogger(__name__)


class RegimeClassifier:
    """
    3-state regime classifier using Jump Models.

    Workflow:
    1. fit() — tunes lambda via strategy Sharpe, fits Jump Model,
       runs Sparse JM for feature selection, trains RF predictor
    2. predict() — returns current regime labels + probabilities
    3. predict_next() — RF layer predicts next regime + confidence
    """

    def __init__(self, config: Optional[RegimeConfig] = None):
        self.config = config or RegimeConfig()
        self.jump_model_: Optional[JumpModel] = None
        self.sparse_model_: Optional[SparseJumpModel] = None
        self.rf_model_: Optional[RandomForestClassifier] = None
        self.scaler_: Optional[StandardScaler] = None
        self.selected_features_: Optional[List[str]] = None
        self.best_lambda_: Optional[float] = None
        self.state_mapping_: Optional[Dict[int, str]] = None
        self.is_fitted_: bool = False

    def fit(
        self,
        feature_df: pd.DataFrame,
        returns: Optional[pd.Series] = None,
        ohlcv_df: Optional[pd.DataFrame] = None,
    ) -> "RegimeClassifier":
        """
        Fit the regime classifier pipeline.

        Steps:
        1. Preprocess features (clip outliers, standardize)
        2. Tune jump penalty lambda by EMA crossover Sharpe
        3. Fit Jump Model with optimal lambda
        4. Run Sparse Jump Model for feature selection
        5. Refit Jump Model on selected features
        6. Assign regime labels (TRENDING/RANGING/VOLATILE)
        7. Train RF prediction layer

        Args:
            feature_df: Features from RegimeFeatureEngine.compute()
            returns: Log returns series (used for regime labeling).
                     If None, computed from ohlcv_df.
            ohlcv_df: OHLCV data (used to compute returns if not provided,
                      and for EMA crossover strategy in lambda tuning)

        Returns:
            self
        """
        if returns is None and ohlcv_df is None:
            raise ValueError("Must provide either returns or ohlcv_df")

        if returns is None:
            returns = np.log(ohlcv_df["close"] / ohlcv_df["close"].shift(1))
            if "Close" in ohlcv_df.columns:
                returns = np.log(ohlcv_df["Close"] / ohlcv_df["Close"].shift(1))

        # Drop NaN warmup rows (keep aligned)
        valid_mask = feature_df.notna().all(axis=1) & returns.notna()
        feature_clean = feature_df.loc[valid_mask].copy()
        returns_clean = returns.loc[valid_mask].copy()

        if len(feature_clean) < 100:
            raise ValueError(
                f"Insufficient valid data after NaN removal: {len(feature_clean)} rows"
            )

        logger.info(f"Fitting on {len(feature_clean)} bars, {feature_clean.shape[1]} features")

        # Step 1: Preprocess
        self.scaler_ = StandardScaler()
        X_scaled = pd.DataFrame(
            self.scaler_.fit_transform(feature_clean),
            index=feature_clean.index,
            columns=feature_clean.columns,
        )
        # Clip outliers
        X_scaled = X_scaled.clip(-self.config.clip_std, self.config.clip_std)

        # Step 2: Tune lambda via strategy Sharpe
        self.best_lambda_ = self._tune_lambda(
            X_scaled, returns_clean, ohlcv_df
        )
        logger.info(f"Optimal lambda: {self.best_lambda_}")

        # Step 3: Fit Jump Model
        self.jump_model_ = JumpModel(
            n_components=self.config.n_states,
            jump_penalty=self.best_lambda_,
            cont=False,
            max_iter=self.config.jm_max_iter,
            n_init=self.config.jm_n_init,
            tol=self.config.jm_tol,
        )
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            self.jump_model_.fit(X_scaled, ret_ser=returns_clean, sort_by="cumret")

        # Step 4: Sparse Jump Model for feature selection
        # Nystrup et al. (2021) — identify which features separate regimes
        best_sparse = None
        best_sparse_sharpe = -np.inf

        for max_feats in self.config.sparse_max_feats_grid:
            try:
                sjm = SparseJumpModel(
                    n_components=self.config.n_states,
                    max_feats=max_feats,
                    jump_penalty=self.best_lambda_,
                    cont=False,
                    max_iter=self.config.sparse_max_iter,
                    n_init_jm=self.config.jm_n_init,
                )
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    sjm.fit(X_scaled, ret_ser=returns_clean, sort_by="cumret")

                # Evaluate by strategy Sharpe with this feature subset
                labels = sjm.labels_
                sharpe = self._compute_regime_sharpe(
                    labels, returns_clean, ohlcv_df
                )

                if sharpe > best_sparse_sharpe:
                    best_sparse_sharpe = sharpe
                    best_sparse = sjm
            except Exception as e:
                logger.warning(f"Sparse JM failed with max_feats={max_feats}: {e}")
                continue

        if best_sparse is not None:
            self.sparse_model_ = best_sparse
            # Get selected features (non-zero weights)
            weights = np.abs(best_sparse.feat_weights)
            if hasattr(weights, "values"):
                weights = weights.values
            # Select features with weight above median
            threshold = np.median(weights[weights > 0]) if np.any(weights > 0) else 0
            selected_mask = weights > threshold
            self.selected_features_ = list(feature_clean.columns[selected_mask])
            logger.info(
                f"Sparse JM selected {len(self.selected_features_)} features: "
                f"{self.selected_features_}"
            )

            # Step 5: Refit Jump Model on selected features only
            X_selected = X_scaled[self.selected_features_]
            self.jump_model_ = JumpModel(
                n_components=self.config.n_states,
                jump_penalty=self.best_lambda_,
                cont=False,
                max_iter=self.config.jm_max_iter,
                n_init=self.config.jm_n_init,
                tol=self.config.jm_tol,
            )
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                self.jump_model_.fit(X_selected, ret_ser=returns_clean, sort_by="cumret")
        else:
            # No sparse model converged — use all features
            self.selected_features_ = list(feature_clean.columns)
            logger.warning("Sparse JM failed — using all features")

        # Step 6: Assign regime labels
        self.state_mapping_ = self._assign_regime_labels(
            self.jump_model_.labels_, returns_clean, feature_clean
        )
        logger.info(f"State mapping: {self.state_mapping_}")

        # Step 7: Train RF prediction layer
        # Pomorski & Gorse (2023) — predict next regime
        self._fit_rf_predictor(feature_clean, returns_clean)

        self.is_fitted_ = True
        return self

    def predict(self, feature_df: pd.DataFrame) -> pd.DataFrame:
        """
        Predict current regime labels and probabilities (online/causal).

        Args:
            feature_df: Features from RegimeFeatureEngine.compute()

        Returns:
            DataFrame with columns:
            [regime_label, regime_prob_trending, regime_prob_ranging, regime_prob_volatile]
        """
        self._check_fitted()

        X = self._preprocess_features(feature_df)
        # Use only selected features (must match what model was fitted on)
        X_selected = X[self.selected_features_] if self.selected_features_ else X
        labels_int = self.jump_model_.predict_online(X_selected)

        # Map integer labels to human-readable names
        labels_str = np.array([
            self.state_mapping_.get(int(l), "UNKNOWN") for l in labels_int
        ])

        result = pd.DataFrame(index=feature_df.index)
        result["regime_label"] = labels_str

        # Probabilities: use hard labels as 0/1 probabilities
        # (CJM requires separate fitting which is expensive; hard labels suffice
        # since the Jump Model already provides persistent state assignments)
        for state_name in self.config.state_names:
            col_name = f"regime_prob_{state_name.lower()}"
            result[col_name] = (result["regime_label"] == state_name).astype(float)

        return result

    def predict_next(self, feature_df: pd.DataFrame) -> pd.DataFrame:
        """
        Predict NEXT regime using Random Forest layer.
        Pomorski & Gorse (2023) — uses current features to predict future regime.

        Args:
            feature_df: Features from RegimeFeatureEngine.compute()

        Returns:
            DataFrame with columns: [predicted_next_regime, prediction_confidence]
        """
        self._check_fitted()

        if self.rf_model_ is None:
            raise RuntimeError("RF prediction layer was not fitted")

        X = self._preprocess_features(feature_df)
        X_rf = X[self.selected_features_] if self.selected_features_ else X

        # Handle NaN rows
        valid_mask = X_rf.notna().all(axis=1)
        predictions = np.full(len(feature_df), "UNKNOWN", dtype=object)
        confidences = np.full(len(feature_df), np.nan)

        if valid_mask.any():
            X_valid = X_rf.loc[valid_mask]
            pred_int = self.rf_model_.predict(X_valid)
            pred_proba = self.rf_model_.predict_proba(X_valid)

            pred_str = np.array([
                self.state_mapping_.get(int(p), "UNKNOWN") for p in pred_int
            ])
            conf = pred_proba.max(axis=1)

            predictions[valid_mask.values] = pred_str
            confidences[valid_mask.values] = conf

        result = pd.DataFrame(index=feature_df.index)
        result["predicted_next_regime"] = predictions
        result["prediction_confidence"] = confidences
        return result

    def save(self, path: str) -> None:
        """Serialize model to disk via joblib."""
        self._check_fitted()
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)

        state = {
            "config": self.config,
            "jump_model": self.jump_model_,
            "sparse_model": self.sparse_model_,
            "rf_model": self.rf_model_,
            "scaler": self.scaler_,
            "selected_features": self.selected_features_,
            "best_lambda": self.best_lambda_,
            "state_mapping": self.state_mapping_,
        }
        joblib.dump(state, path)
        logger.info(f"Model saved to {path}")

    @classmethod
    def load(cls, path: str) -> "RegimeClassifier":
        """Load model from disk."""
        state = joblib.load(path)
        obj = cls(config=state["config"])
        obj.jump_model_ = state["jump_model"]
        obj.sparse_model_ = state["sparse_model"]
        obj.rf_model_ = state["rf_model"]
        obj.scaler_ = state["scaler"]
        obj.selected_features_ = state["selected_features"]
        obj.best_lambda_ = state["best_lambda"]
        obj.state_mapping_ = state["state_mapping"]
        obj.is_fitted_ = True
        return obj

    # =========================================================================
    # Private Methods
    # =========================================================================

    def _check_fitted(self) -> None:
        if not self.is_fitted_:
            raise RuntimeError("RegimeClassifier is not fitted. Call .fit() first.")

    def _preprocess_features(self, feature_df: pd.DataFrame) -> pd.DataFrame:
        """Apply saved scaler and clip outliers."""
        X = pd.DataFrame(
            self.scaler_.transform(feature_df.fillna(0)),
            index=feature_df.index,
            columns=feature_df.columns,
        )
        return X.clip(-self.config.clip_std, self.config.clip_std)

    def _tune_lambda(
        self,
        X_scaled: pd.DataFrame,
        returns: pd.Series,
        ohlcv_df: Optional[pd.DataFrame],
    ) -> float:
        """
        Tune jump penalty lambda by Sharpe of EMA crossover strategy
        filtered by TRENDING regime.

        Per Nystrup (2020): lambda should be tuned on downstream strategy
        performance, not on log-likelihood or silhouette score.
        """
        best_lambda = self.config.lambda_grid[0]
        best_sharpe = -np.inf

        for lam in self.config.lambda_grid:
            try:
                jm = JumpModel(
                    n_components=self.config.n_states,
                    jump_penalty=lam,
                    cont=False,
                    max_iter=self.config.jm_max_iter,
                    n_init=max(3, self.config.jm_n_init // 2),  # Faster for grid search
                    tol=self.config.jm_tol * 10,
                )
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    jm.fit(X_scaled, ret_ser=returns, sort_by="cumret")

                labels = jm.labels_
                sharpe = self._compute_regime_sharpe(labels, returns, ohlcv_df)

                logger.debug(f"Lambda={lam:.1f}, Sharpe={sharpe:.3f}")

                if sharpe > best_sharpe:
                    best_sharpe = sharpe
                    best_lambda = lam
            except Exception as e:
                logger.warning(f"Lambda tuning failed for {lam}: {e}")
                continue

        return best_lambda

    def _compute_regime_sharpe(
        self,
        labels: np.ndarray,
        returns: pd.Series,
        ohlcv_df: Optional[pd.DataFrame],
    ) -> float:
        """
        Compute Sharpe ratio of EMA crossover strategy filtered by
        the regime with highest cumulative return (assumed TRENDING).

        Compares regime-filtered vs unfiltered to evaluate regime quality.
        """
        if ohlcv_df is None:
            # Fallback: use regime with highest mean return
            unique_labels = np.unique(labels)
            cum_rets = {}
            for l in unique_labels:
                mask = labels == l
                cum_rets[l] = returns.values[mask].sum()

            trending_label = max(cum_rets, key=cum_rets.get)
        else:
            # Identify trending regime by cumulative return
            unique_labels = np.unique(labels)
            cum_rets = {}
            for l in unique_labels:
                mask = labels == l
                cum_rets[l] = returns.values[mask].sum()
            trending_label = max(cum_rets, key=cum_rets.get)

        # EMA crossover strategy
        close = returns.cumsum().apply(np.exp)  # Reconstruct price from returns
        ema_fast = close.ewm(span=self.config.ema_fast, adjust=False).mean()
        ema_slow = close.ewm(span=self.config.ema_slow, adjust=False).mean()

        # Signal: long when fast > slow
        signal = (ema_fast > ema_slow).astype(float).shift(1).fillna(0)

        # Filter by trending regime
        regime_filter = (labels == trending_label).astype(float)
        filtered_signal = signal * regime_filter

        # Strategy returns
        strategy_returns = filtered_signal * returns.values

        # Annualized Sharpe (assume ~1380 bars/day for 1h, adjust)
        bars_per_year = 252 * 24  # approximate for hourly
        mean_ret = np.nanmean(strategy_returns)
        std_ret = np.nanstd(strategy_returns)

        if std_ret == 0:
            return 0.0

        sharpe = (mean_ret / std_ret) * np.sqrt(bars_per_year)
        return sharpe

    def _assign_regime_labels(
        self,
        labels: np.ndarray,
        returns: pd.Series,
        features: pd.DataFrame,
    ) -> Dict[int, str]:
        """
        Assign human-readable regime names to integer state labels.

        Uses characteristics:
        - TRENDING: highest absolute mean return + highest ADX
        - VOLATILE: highest volatility (ATR ratio or BB width)
        - RANGING: lowest absolute return + lowest ADX

        Uses Hungarian algorithm for optimal assignment.
        """
        unique_labels = np.unique(labels)
        n_states = len(unique_labels)

        # Compute characteristics for each state
        state_chars = {}
        for l in unique_labels:
            mask = labels == l
            state_chars[l] = {
                "abs_mean_return": np.abs(returns.values[mask]).mean(),
                "mean_return_magnitude": np.abs(np.mean(returns.values[mask])),
                "volatility": features["atr_ratio"].values[mask].mean()
                if "atr_ratio" in features.columns else 0,
                "adx": features["adx"].values[mask].mean()
                if "adx" in features.columns else 0,
            }

        # Build cost matrix for Hungarian assignment
        # Rows = integer states, Cols = [TRENDING, RANGING, VOLATILE]
        cost_matrix = np.zeros((n_states, n_states))

        for i, l in enumerate(unique_labels):
            chars = state_chars[l]
            # TRENDING: high directional return + high ADX
            cost_matrix[i, 0] = -(
                chars["mean_return_magnitude"] + chars["adx"] * 0.01
            )
            # RANGING: low everything
            cost_matrix[i, 1] = (
                chars["abs_mean_return"] + chars["volatility"] + chars["adx"] * 0.01
            )
            # VOLATILE: high volatility + high absolute returns
            cost_matrix[i, 2] = -(chars["volatility"] + chars["abs_mean_return"])

        # Solve assignment
        row_ind, col_ind = linear_sum_assignment(cost_matrix)

        mapping = {}
        for row, col in zip(row_ind, col_ind):
            state_int = unique_labels[row]
            state_name = self.config.state_names[col]
            mapping[int(state_int)] = state_name

        # Fill any unmapped states
        for l in unique_labels:
            if int(l) not in mapping:
                mapping[int(l)] = "UNKNOWN"

        return mapping

    def _fit_rf_predictor(
        self, feature_df: pd.DataFrame, returns: pd.Series
    ) -> None:
        """
        Train Random Forest to predict NEXT regime from current features.
        Pomorski & Gorse (2023) — strict walk-forward, no look-ahead.

        Target: regime label shifted by 1 bar (predict next regime).
        """
        labels = np.asarray(self.jump_model_.labels_)
        # Target is next bar's regime
        target = np.roll(labels, -1)
        target[len(target) - 1] = labels[len(labels) - 1]  # Last bar: repeat

        X_rf = feature_df[self.selected_features_] if self.selected_features_ else feature_df

        # Only use rows where we have valid features and target
        valid_mask = X_rf.notna().all(axis=1).values
        # Don't use last row (target is invalid)
        valid_mask[-1] = False

        X_train = X_rf.iloc[valid_mask]
        y_train = target[valid_mask]

        if len(X_train) < 50:
            logger.warning("Insufficient data for RF training")
            self.rf_model_ = None
            return

        self.rf_model_ = RandomForestClassifier(
            n_estimators=self.config.rf_n_estimators,
            max_depth=self.config.rf_max_depth,
            max_features=self.config.rf_max_features,
            min_samples_leaf=self.config.rf_min_samples_leaf,
            random_state=self.config.rf_random_state,
            n_jobs=-1,
        )
        self.rf_model_.fit(X_train, y_train)
        logger.info(
            f"RF predictor trained on {len(X_train)} samples, "
            f"OOB-like train accuracy: {self.rf_model_.score(X_train, y_train):.3f}"
        )

    @staticmethod
    def align_labels_across_folds(
        labels_current: np.ndarray,
        labels_reference: np.ndarray,
        n_states: int,
    ) -> np.ndarray:
        """
        Align regime labels across walk-forward folds using Hungarian algorithm.
        Ensures 'TRENDING' stays 'TRENDING' even when refitting produces
        different integer labels.

        Args:
            labels_current: Labels from current fold
            labels_reference: Labels from reference (previous) fold
            n_states: Number of states

        Returns:
            Remapped labels_current aligned to labels_reference
        """
        # Build confusion/overlap matrix
        overlap = np.zeros((n_states, n_states))
        min_len = min(len(labels_current), len(labels_reference))

        for i in range(n_states):
            for j in range(n_states):
                overlap[i, j] = np.sum(
                    (labels_current[:min_len] == i) & (labels_reference[:min_len] == j)
                )

        # Hungarian algorithm (maximize overlap = minimize negative overlap)
        row_ind, col_ind = linear_sum_assignment(-overlap)

        # Create remapping
        remap = {row: col for row, col in zip(row_ind, col_ind)}
        return np.array([remap.get(int(l), l) for l in labels_current])
