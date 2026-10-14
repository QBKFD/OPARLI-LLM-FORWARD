# backend/routes/validation_routes.py
"""
Walk-Forward Validation API Routes

Endpoint to run regime classifier walk-forward validation
and return structured per-fold + aggregate results.
"""

from fastapi import APIRouter, HTTPException, Query
from typing import Annotated
import logging
import sys
import os
import numpy as np
import pandas as pd

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from config.database import get_database
from middleware.security import validate_symbol, validate_timeframe

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/validation", tags=["validation"])

# Cache validation results (expensive to compute)
_validation_cache = {}


def _load_regime_validation():
    """
    Import the regime_classifier package lazily.

    The walk-forward validator is a research tool, not a core trading path, and
    it pulls in a separate dependency set (scikit-learn, joblib, jumpmodels) that
    is not part of backend/requirements.txt. Importing it at module load would
    take the whole API down if those packages are absent, so the import is
    deferred to request time and surfaced as a clean 503 instead.
    """
    try:
        from regime_classifier import (
            RegimeConfig,
            RegimeValidator,
        )
        return RegimeConfig, RegimeValidator
    except ImportError as e:
        raise HTTPException(
            status_code=503,
            detail=(
                "Regime validator unavailable: its dependencies are not "
                "installed. Run `pip install -r regime_classifier/requirements.txt` "
                f"(scikit-learn, joblib, jumpmodels). Import error: {e}"
            ),
        )


def _fetch_all_ohlcv(symbol: str, timeframe: str) -> pd.DataFrame:
    """Fetch all available OHLCV data for walk-forward validation."""
    db = get_database()

    view_map = {
        '1min': 'ohlcv_1min',
        '5min': 'ohlcv_5min',
        '15min': 'ohlcv_15min',
        '30min': 'ohlcv_30min',
        '1H': 'ohlcv_1h',
        '4H': 'ohlcv_4h',
        '1D': 'ohlcv_1d'
    }

    view_name = view_map.get(timeframe)
    if not view_name:
        raise ValueError(f"Invalid timeframe: {timeframe}")

    query = f"""
        SELECT
            timestamp AT TIME ZONE 'UTC' as timestamp,
            open::FLOAT as open,
            high::FLOAT as high,
            low::FLOAT as low,
            close::FLOAT as close,
            volume::BIGINT as volume
        FROM {view_name}
        WHERE symbol = %s
        ORDER BY timestamp ASC
    """

    with db.get_cursor() as cursor:
        cursor.execute(query, (symbol,))
        rows = cursor.fetchall()

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df['timestamp'] = pd.to_datetime(df['timestamp'], utc=True)
    df = df.set_index('timestamp').sort_index()
    return df


def _fold_to_dict(fold: "FoldResult") -> dict:
    """Convert FoldResult dataclass to JSON-serializable dict."""
    return {
        'fold_idx': fold.fold_idx,
        'train_start': fold.train_start,
        'train_end': fold.train_end,
        'test_start': fold.test_start,
        'test_end': fold.test_end,
        'n_train_bars': fold.n_train_bars,
        'n_test_bars': fold.n_test_bars,
        'mean_dwell_time': fold.mean_dwell_time,
        'regime_distribution': fold.regime_distribution,
        'silhouette': None if np.isnan(fold.silhouette) else round(fold.silhouette, 4),
        'sharpe_filtered': None if np.isnan(fold.sharpe_filtered) else round(fold.sharpe_filtered, 4),
        'sharpe_unfiltered': None if np.isnan(fold.sharpe_unfiltered) else round(fold.sharpe_unfiltered, 4),
        'sharpe_improvement': None if np.isnan(fold.sharpe_improvement) else round(fold.sharpe_improvement, 4),
        'max_dd_filtered': None if np.isnan(fold.max_dd_filtered) else round(fold.max_dd_filtered, 6),
        'max_dd_unfiltered': None if np.isnan(fold.max_dd_unfiltered) else round(fold.max_dd_unfiltered, 6),
        'win_rate_filtered': None if np.isnan(fold.win_rate_filtered) else round(fold.win_rate_filtered, 4),
        'win_rate_unfiltered': None if np.isnan(fold.win_rate_unfiltered) else round(fold.win_rate_unfiltered, 4),
        'payoff_ratio_filtered': None if np.isnan(fold.payoff_ratio_filtered) else round(fold.payoff_ratio_filtered, 4),
        'payoff_ratio_unfiltered': None if np.isnan(fold.payoff_ratio_unfiltered) else round(fold.payoff_ratio_unfiltered, 4),
        'rf_accuracy': None if np.isnan(fold.rf_accuracy) else round(fold.rf_accuracy, 4),
        'best_lambda': None if np.isnan(fold.best_lambda) else fold.best_lambda,
        'selected_features': fold.selected_features,
        'warnings': fold.warnings,
    }


def _report_to_dict(report: "ValidationReport") -> dict:
    """Convert ValidationReport to JSON-serializable dict."""

    def safe_round(val, decimals=4):
        if val is None or (isinstance(val, float) and np.isnan(val)):
            return None
        return round(val, decimals)

    return {
        'total_folds': report.total_folds,
        'degenerate_folds': report.degenerate_folds,
        'aggregate': {
            'sharpe_filtered': safe_round(report.aggregate_sharpe_filtered),
            'sharpe_unfiltered': safe_round(report.aggregate_sharpe_unfiltered),
            'sharpe_improvement': safe_round(report.aggregate_sharpe_improvement),
            'max_dd_filtered': safe_round(report.aggregate_max_dd_filtered, 6),
            'max_dd_unfiltered': safe_round(report.aggregate_max_dd_unfiltered, 6),
            'mean_dwell_time': {
                k: round(v, 1) for k, v in report.aggregate_mean_dwell_time.items()
            },
            'regime_distribution': {
                k: round(v, 4) for k, v in report.aggregate_regime_distribution.items()
            },
        },
        'folds': [_fold_to_dict(f) for f in report.folds],
        'summary_text': report.summary(),
    }


@router.get("/walk-forward/{symbol}/{timeframe}")
async def run_walk_forward_validation(
    symbol: str,
    timeframe: str,
    train_months: Annotated[int, Query(ge=3, le=24)] = 6,
    test_months: Annotated[int, Query(ge=1, le=6)] = 1,
    embargo_months: Annotated[int, Query(ge=0, le=3)] = 1,
    force_rerun: bool = False,
):
    """
    Run walk-forward expanding-window validation on the regime classifier.

    Returns per-fold metrics (Sharpe, drawdown, dwell times, RF accuracy)
    and aggregate results across all folds.

    Parameters:
    - symbol: XAUUSD etc.
    - timeframe: 1H, 4H, 1D (recommended: 1H for enough data)
    - train_months: Initial training window in months (default 6)
    - test_months: Test window in months (default 1)
    - embargo_months: Gap between train and test (default 1)
    - force_rerun: If true, ignore cached results
    """
    try:
        symbol = validate_symbol(symbol)
        timeframe = validate_timeframe(timeframe)
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))

    cache_key = f"{symbol}_{timeframe}_{train_months}_{test_months}_{embargo_months}"

    if not force_rerun and cache_key in _validation_cache:
        logger.info(f"Returning cached validation for {cache_key}")
        return _validation_cache[cache_key]

    # Lazily import the classifier so a missing research dependency set fails
    # this endpoint with a clear 503 rather than crashing the whole API at boot.
    RegimeConfig, RegimeValidator = _load_regime_validation()

    try:
        # Fetch all available data
        logger.info(f"Fetching all {timeframe} data for {symbol}...")
        ohlcv = _fetch_all_ohlcv(symbol, timeframe)

        if len(ohlcv) < 500:
            raise HTTPException(
                status_code=400,
                detail=f"Insufficient data: {len(ohlcv)} bars. Need at least 500 for walk-forward."
            )

        # Configure with faster settings for validation
        config = RegimeConfig(
            walk_forward_train_months=train_months,
            walk_forward_test_months=test_months,
            embargo_months=embargo_months,
            # Speed optimizations for walk-forward (many folds)
            lambda_grid=[0.1, 1.0, 5.0, 20.0, 100.0],  # 5 instead of 9
            jm_n_init=5,          # 5 instead of 10
            jm_max_iter=300,      # 300 instead of 1000
            sparse_max_feats_grid=[3.0, 8.0, 20.0],  # 3 instead of 5
            sparse_max_iter=15,   # 15 instead of 30
        )

        # Adjust feature windows for shorter timeframes
        if timeframe in ('5min', '15min'):
            config.hurst_window = 100
            config.autocorr_window = 60
            config.variance_ratio_window = 60

        logger.info(
            f"Starting walk-forward validation: {len(ohlcv)} bars, "
            f"train={train_months}mo, test={test_months}mo, embargo={embargo_months}mo"
        )

        # Run validation
        validator = RegimeValidator(config=config)
        report = validator.run_walk_forward(ohlcv)

        # Build response
        result = {
            'symbol': symbol,
            'timeframe': timeframe,
            'total_bars': len(ohlcv),
            'data_start': str(ohlcv.index.min().date()),
            'data_end': str(ohlcv.index.max().date()),
            'config': {
                'train_months': train_months,
                'test_months': test_months,
                'embargo_months': embargo_months,
            },
            'report': _report_to_dict(report),
        }

        # Cache
        _validation_cache[cache_key] = result
        logger.info(f"Walk-forward validation complete: {report.total_folds} folds")

        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Walk-forward validation failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Validation failed: {str(e)}"
        )


@router.delete("/cache")
async def clear_validation_cache():
    """Clear cached validation results."""
    global _validation_cache
    count = len(_validation_cache)
    _validation_cache = {}
    return {"status": "cleared", "entries_removed": count}


@router.get("/data-info/{symbol}/{timeframe}")
async def get_data_info(
    symbol: str,
    timeframe: str,
    train_months: int = 6,
    test_months: int = 1,
    embargo_months: int = 1,
):
    """
    Get info about available data for validation planning.
    Returns date range, bar count, and estimated fold count.
    """
    try:
        symbol = validate_symbol(symbol)
        timeframe = validate_timeframe(timeframe)
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))

    try:
        ohlcv = _fetch_all_ohlcv(symbol, timeframe)

        if len(ohlcv) == 0:
            return {
                'symbol': symbol,
                'timeframe': timeframe,
                'total_bars': 0,
                'data_start': None,
                'data_end': None,
                'months_available': 0,
                'estimated_folds': 0,
            }

        data_start = ohlcv.index.min()
        data_end = ohlcv.index.max()
        months_available = (data_end - data_start).days / 30.44

        # Estimate folds using actual config params
        min_months_needed = train_months + embargo_months + test_months
        if months_available >= min_months_needed:
            estimated_folds = max(0, int((months_available - train_months - embargo_months) / test_months))
        else:
            estimated_folds = 0

        return {
            'symbol': symbol,
            'timeframe': timeframe,
            'total_bars': len(ohlcv),
            'data_start': str(data_start.date()),
            'data_end': str(data_end.date()),
            'months_available': round(months_available, 1),
            'estimated_folds': estimated_folds,
        }

    except Exception as e:
        logger.error(f"Error getting data info: {e}")
        raise HTTPException(status_code=500, detail=str(e))
