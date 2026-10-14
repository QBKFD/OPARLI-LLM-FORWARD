#!/usr/bin/env python3
"""
Walk-Forward Validation Runner

Runs regime classifier walk-forward validation against the database
and prints structured results. Can also save plots.

Usage:
    python scripts/run_walk_forward.py --symbol XAUUSD --timeframe 1H
    python scripts/run_walk_forward.py --symbol XAUUSD --timeframe 1H --train-months 6 --save-plots
"""

import argparse
import sys
import os
import logging

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd
from dotenv import load_dotenv

# Load env from backend
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'backend', '.env'))

from regime_classifier import RegimeConfig, RegimeValidator

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)


def fetch_ohlcv(symbol: str, timeframe: str) -> pd.DataFrame:
    """Fetch all OHLCV data from database."""
    import psycopg2
    from psycopg2.extras import RealDictCursor

    db_url = os.getenv('DATABASE_URL')
    if not db_url:
        raise ValueError("DATABASE_URL not set")

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
        raise ValueError(f"Invalid timeframe: {timeframe}. Valid: {list(view_map.keys())}")

    conn = psycopg2.connect(db_url)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(f"""
                SELECT
                    timestamp AT TIME ZONE 'UTC' as timestamp,
                    open::FLOAT, high::FLOAT, low::FLOAT,
                    close::FLOAT, volume::BIGINT
                FROM {view_name}
                WHERE symbol = %s
                ORDER BY timestamp ASC
            """, (symbol,))
            rows = cur.fetchall()
    finally:
        conn.close()

    if not rows:
        raise ValueError(f"No data for {symbol} at {timeframe}")

    df = pd.DataFrame(rows)
    df['timestamp'] = pd.to_datetime(df['timestamp'], utc=True)
    df = df.set_index('timestamp').sort_index()
    return df


def print_fold_detail(fold):
    """Print detailed metrics for a single fold."""
    print(f"\n  Fold {fold.fold_idx + 1}:")
    print(f"    Train: {fold.train_start} to {fold.train_end} ({fold.n_train_bars} bars)")
    print(f"    Test:  {fold.test_start} to {fold.test_end} ({fold.n_test_bars} bars)")

    if fold.warnings:
        for w in fold.warnings:
            print(f"    WARNING: {w}")
        return

    print(f"    Lambda: {fold.best_lambda}")
    print(f"    Features: {fold.selected_features}")
    print(f"    Distribution: {fold.regime_distribution}")
    print(f"    Dwell times:  {fold.mean_dwell_time}")

    if not np.isnan(fold.sharpe_filtered):
        print(f"    Sharpe filtered:   {fold.sharpe_filtered:+.4f}")
        print(f"    Sharpe unfiltered: {fold.sharpe_unfiltered:+.4f}")
        print(f"    Improvement:       {fold.sharpe_improvement:+.4f}")
        print(f"    Max DD filtered:   {fold.max_dd_filtered:.6f}")
        print(f"    Max DD unfiltered: {fold.max_dd_unfiltered:.6f}")
        print(f"    Win rate filtered: {fold.win_rate_filtered:.2%}")
        print(f"    Payoff filtered:   {fold.payoff_ratio_filtered:.3f}")

    if not np.isnan(fold.rf_accuracy):
        print(f"    RF accuracy:       {fold.rf_accuracy:.2%}")

    if not np.isnan(fold.silhouette):
        print(f"    Silhouette:        {fold.silhouette:.4f}")


def main():
    parser = argparse.ArgumentParser(description='Walk-Forward Regime Validation')
    parser.add_argument('--symbol', default='XAUUSD', help='Trading symbol')
    parser.add_argument('--timeframe', default='1H', help='Timeframe (1min,5min,15min,30min,1H,4H,1D)')
    parser.add_argument('--train-months', type=int, default=6, help='Training window in months')
    parser.add_argument('--test-months', type=int, default=1, help='Test window in months')
    parser.add_argument('--embargo-months', type=int, default=1, help='Embargo between train/test')
    parser.add_argument('--save-plots', action='store_true', help='Save validation plots')
    parser.add_argument('--plot-dir', default='output/validation', help='Directory for plots')
    parser.add_argument('--verbose', action='store_true', help='Show per-fold details')
    args = parser.parse_args()

    print(f"\n{'='*60}")
    print(f"  WALK-FORWARD REGIME VALIDATION")
    print(f"  Symbol: {args.symbol} | Timeframe: {args.timeframe}")
    print(f"  Train: {args.train_months}mo | Test: {args.test_months}mo | Embargo: {args.embargo_months}mo")
    print(f"{'='*60}")

    # Fetch data
    print(f"\nFetching data...")
    ohlcv = fetch_ohlcv(args.symbol, args.timeframe)
    data_months = (ohlcv.index.max() - ohlcv.index.min()).days / 30.44
    print(f"  {len(ohlcv)} bars, {ohlcv.index.min().date()} to {ohlcv.index.max().date()} ({data_months:.1f} months)")

    # Configure
    config = RegimeConfig(
        walk_forward_train_months=args.train_months,
        walk_forward_test_months=args.test_months,
        embargo_months=args.embargo_months,
    )

    if args.timeframe in ('5min', '15min'):
        config.hurst_window = 100
        config.autocorr_window = 60
        config.variance_ratio_window = 60

    # Run
    print(f"\nRunning walk-forward validation...")
    validator = RegimeValidator(config=config)
    report = validator.run_walk_forward(ohlcv)

    # Print results
    print(report.summary())

    if args.verbose:
        print("\n--- Per-Fold Details ---")
        for fold in report.folds:
            print_fold_detail(fold)

    # Save plots
    if args.save_plots:
        os.makedirs(args.plot_dir, exist_ok=True)
        plot_path = os.path.join(
            args.plot_dir,
            f"validation_{args.symbol}_{args.timeframe}.png"
        )
        validator.plot_validation_summary(report, save_path=plot_path)
        print(f"\nPlots saved to {args.plot_dir}/")

    # Summary verdict
    print(f"\n{'='*60}")
    improvement = report.aggregate_sharpe_improvement
    if improvement is not None and not np.isnan(improvement):
        if improvement > 0.1:
            print("  VERDICT: Regime filtering IMPROVES strategy performance")
        elif improvement > -0.1:
            print("  VERDICT: Regime filtering has MARGINAL effect")
        else:
            print("  VERDICT: Regime filtering HURTS performance — review classifier")
    else:
        print("  VERDICT: Could not compute — check data and fold count")
    print(f"{'='*60}\n")


if __name__ == '__main__':
    main()
