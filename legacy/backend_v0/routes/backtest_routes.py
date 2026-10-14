"""
Backtest API Routes

Endpoints for running strategy backtests with out-of-sample validation.
Serves pre-computed results for performance (avoids loading 70MB CSV on each request).
"""

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import Optional
import logging
import json
import pathlib

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/backtest", tags=["backtest"])

# Pre-computed results path
STATIC_DIR = pathlib.Path(__file__).parent.parent / 'static'
SWEEP_RESULTS_FILE = STATIC_DIR / 'sweep_backtest_results.json'

# Cache the results in memory after first load
_cached_sweep_results = None


def get_sweep_results():
    """Load pre-computed sweep backtest results (cached in memory)"""
    global _cached_sweep_results
    if _cached_sweep_results is None:
        if not SWEEP_RESULTS_FILE.exists():
            raise FileNotFoundError(f"Pre-computed results not found: {SWEEP_RESULTS_FILE}")
        with open(SWEEP_RESULTS_FILE, 'r') as f:
            _cached_sweep_results = json.load(f)
        logger.info(f"Loaded pre-computed sweep results: {len(_cached_sweep_results['trades'])} trades")
    return _cached_sweep_results


@router.post("/sweep")
async def run_sweep_strategy():
    """
    Get pre-computed Liquidity Sweep backtest results.

    Uses Option B settings (1.0x ATR displacement) which has been validated:
    - Train: +1.055R expectancy (235 trades)
    - Test: +1.077R expectancy (39 trades)

    Returns:
    - trades: List of all trades with entry/exit details
    - validation: Train/test performance metrics
    - equityCurve: Equity progression over time
    - summary: Overall performance metrics
    """
    try:
        result = get_sweep_results()
        logger.info(f"Returning pre-computed sweep results: {result['summary']['totalTrades']} trades")
        return result
    except FileNotFoundError as e:
        logger.error(f"Results file not found: {e}")
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Error loading results: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to load results: {str(e)}")


@router.get("/sweep/trades")
async def get_sweep_trades(
    start_date: Optional[str] = Query(None, description="Filter trades from this date (YYYY-MM-DD)"),
    end_date: Optional[str] = Query(None, description="Filter trades until this date (YYYY-MM-DD)"),
    result_filter: Optional[str] = Query(None, description="Filter by result: 'win' or 'loss'")
):
    """
    Get Sweep strategy trades with optional filtering.
    """
    try:
        result = get_sweep_results()
        trades = result['trades']

        # Apply filters
        if start_date:
            trades = [t for t in trades if t['entryTime'] >= start_date]
        if end_date:
            trades = [t for t in trades if t['entryTime'] <= end_date]
        if result_filter:
            trades = [t for t in trades if t['result'] == result_filter]

        return {
            'trades': trades,
            'count': len(trades)
        }
    except Exception as e:
        logger.error(f"Error fetching trades: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/sweep/validation")
async def get_sweep_validation():
    """
    Get out-of-sample validation results for the Sweep strategy.

    Shows train vs test performance to verify the strategy isn't overfit.
    """
    try:
        result = get_sweep_results()
        return result['validation']
    except Exception as e:
        logger.error(f"Error fetching validation: {e}")
        raise HTTPException(status_code=500, detail=str(e))
