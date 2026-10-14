# backend/routes/regime_routes.py
"""
Regime Classification API Routes

Endpoint to compute and serve regime labels for frontend chart overlay.
"""

from fastapi import APIRouter, HTTPException, Query
from typing import Annotated
import logging
import sys
import os
import numpy as np
import pandas as pd

# Add project root to path so regime_classifier is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from config.database import get_database
from middleware.security import validate_symbol, validate_timeframe

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/regime", tags=["regime"])

# Cache fitted classifier to avoid re-fitting on every request
_classifier_cache = {}


def _load_regime_classifier():
    """
    Import the regime_classifier package lazily.

    The classifier is a research overlay, not a core trading path, and it pulls
    in a separate dependency set (scikit-learn, joblib, jumpmodels) that is not
    part of backend/requirements.txt. Importing it at module load would take the
    whole API down if those packages are absent, so the import is deferred to
    request time and surfaced as a clean 503 instead.
    """
    try:
        from regime_classifier import (
            RegimeConfig,
            RegimeFeatureEngine,
            RegimeClassifier,
        )
        return RegimeConfig, RegimeFeatureEngine, RegimeClassifier
    except ImportError as e:
        raise HTTPException(
            status_code=503,
            detail=(
                "Regime classifier unavailable: its dependencies are not "
                "installed. Run `pip install -r regime_classifier/requirements.txt` "
                f"(scikit-learn, joblib, jumpmodels). Import error: {e}"
            ),
        )


def _get_ohlcv_from_db(symbol: str, timeframe: str, limit: int) -> pd.DataFrame:
    """Fetch OHLCV data from database and return as DataFrame."""
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
        ORDER BY timestamp DESC
        LIMIT %s
    """

    with db.get_cursor() as cursor:
        cursor.execute(query, (symbol, limit))
        rows = cursor.fetchall()

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df['timestamp'] = pd.to_datetime(df['timestamp'], utc=True)
    df = df.set_index('timestamp').sort_index()
    return df


@router.get("/{symbol}/{timeframe}")
async def get_regime_labels(
    symbol: str,
    timeframe: str,
    limit: Annotated[int, Query(ge=100, le=5000)] = 2000,
    force_refit: bool = False,
):
    """
    Compute regime labels for chart overlay.

    Returns array of {time, regime, color} for frontend rendering.

    Parameters:
    - symbol: XAUUSD etc.
    - timeframe: 1min, 5min, 15min, 30min, 1H, 4H, 1D
    - limit: Number of bars to classify (100-5000, default 2000)
    - force_refit: If true, re-fit classifier even if cached
    """
    try:
        symbol = validate_symbol(symbol)
        timeframe = validate_timeframe(timeframe)
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))

    # Lazily import the classifier so a missing research dependency set fails
    # this endpoint with a clear 503 rather than crashing the whole API at boot.
    RegimeConfig, RegimeFeatureEngine, RegimeClassifier = _load_regime_classifier()

    try:
        # Fetch OHLCV data
        ohlcv = _get_ohlcv_from_db(symbol, timeframe, limit)

        if len(ohlcv) < 200:
            raise HTTPException(
                status_code=400,
                detail=f"Insufficient data: got {len(ohlcv)} bars, need at least 200"
            )

        # Configure for the timeframe
        config = RegimeConfig()

        # Adjust windows for shorter timeframes
        if timeframe in ('1min', '5min'):
            config.hurst_window = 100
            config.autocorr_window = 60
            config.variance_ratio_window = 60
            config.atr_percentile_window = 100
            config.adx_percentile_window = 100
            config.bb_percentile_window = 100

        # Check cache
        cache_key = f"{symbol}_{timeframe}"
        classifier = _classifier_cache.get(cache_key) if not force_refit else None

        if classifier is None:
            # Compute features and fit
            engine = RegimeFeatureEngine(config=config)
            features = engine.compute(ohlcv)

            classifier = RegimeClassifier(config=config)
            classifier.fit(features, ohlcv_df=ohlcv)

            # Cache it
            _classifier_cache[cache_key] = classifier
            logger.info(f"Fitted regime classifier for {cache_key} on {len(ohlcv)} bars")

        # Predict
        engine = RegimeFeatureEngine(config=config)
        features = engine.compute(ohlcv)
        predictions = classifier.predict(features)

        # Build response: array of {time, regime, color}
        regime_colors = {
            'TRENDING': 'rgba(76, 175, 80, 0.15)',   # Green
            'RANGING': 'rgba(33, 150, 243, 0.15)',    # Blue
            'VOLATILE': 'rgba(244, 67, 54, 0.15)',    # Red
            'UNKNOWN': 'rgba(128, 128, 128, 0.08)',   # Gray
        }

        result = []
        for i, (ts, row) in enumerate(predictions.iterrows()):
            label = row['regime_label']
            if pd.isna(label):
                label = 'UNKNOWN'

            # Convert timestamp to unix epoch
            epoch = int(ts.timestamp()) if hasattr(ts, 'timestamp') else int(pd.Timestamp(ts).timestamp())

            result.append({
                'time': epoch,
                'regime': label,
                'color': regime_colors.get(label, regime_colors['UNKNOWN']),
            })

        # Also return regime summary stats
        label_counts = predictions['regime_label'].value_counts().to_dict()

        return {
            'symbol': symbol,
            'timeframe': timeframe,
            'count': len(result),
            'regimes': result,
            'summary': label_counts,
            'colors': regime_colors,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error computing regimes: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Regime computation failed: {str(e)}")


@router.delete("/cache")
async def clear_regime_cache():
    """Clear cached regime classifiers (forces re-fit on next request)."""
    global _classifier_cache
    count = len(_classifier_cache)
    _classifier_cache = {}
    return {"status": "cleared", "entries_removed": count}
