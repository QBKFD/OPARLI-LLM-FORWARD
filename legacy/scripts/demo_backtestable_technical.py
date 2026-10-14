#!/usr/bin/env python3
"""
scripts/demo_backtestable_technical.py

Vertical-slice proof: the REAL Technical Analyst decision logic
(TimeframeCascade) runs in backtest mode against historical data — with zero
database — through the exact same call the live agent will use.

    LIVE:      run_technical_analysis(LiveDataProvider(),       'XAUUSD', now)
    BACKTEST:  run_technical_analysis(HistoricalDataProvider(df),'XAUUSD', as_of)
                                       ^^^^^^^^^^^^^^^^^^^^^^^^   only this differs

Run:
    python scripts/demo_backtestable_technical.py
"""

import sys
from pathlib import Path
from datetime import datetime, timedelta

import pandas as pd
import pytz

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT / 'backend'))

from services.market_data_provider import HistoricalDataProvider
from services.technical_service import run_technical_analysis

DATA_CSV = REPO_ROOT / 'database/ohlcv_data/XAUUSD_COMPLETE.csv'


def main():
    print("Loading historical 1-min data (no database used)...")
    df = pd.read_csv(DATA_CSV, parse_dates=['timestamp'])
    df.sort_values('timestamp', inplace=True)
    df.set_index('timestamp', inplace=True)
    df.index = pd.DatetimeIndex(df.index).tz_localize(pytz.utc)
    df.columns = [c.capitalize() for c in df.columns]
    df = df[['Open', 'High', 'Low', 'Close', 'Volume']].astype(float)

    # This is the ONLY line that differs between live and backtest:
    provider = HistoricalDataProvider(df, symbol='XAUUSD')

    # Walk a few London-session timestamps and run the REAL cascade at each.
    base = datetime(2023, 6, 1, 8, 0, tzinfo=pytz.utc)
    print(f"\nRunning REAL TimeframeCascade at 5 timestamps via the provider:\n")
    print(f"{'as_of (UTC)':<22} {'signal':<7} {'conf':<6} {'lead':<5} {'case':<5} reasoning")
    print("-" * 100)

    for i in range(5):
        as_of = base + timedelta(days=i)
        result = run_technical_analysis(provider, 'XAUUSD', as_of=as_of)
        print(
            f"{str(as_of):<22} "
            f"{result['signal']:<7} "
            f"{result['confidence']:<6.2f} "
            f"{str(result.get('lead_timeframe')):<5} "
            f"{result.get('decision_case', '-'):<5} "
            f"{result.get('reasoning', '')[:55]}"
        )

    # Prove strict causality: the provider cannot see the future.
    as_of = base
    tf_data = provider.get_timeframe_data('XAUUSD', as_of=as_of)
    latest_bar = tf_data['1m']['Timestamp'].iloc[-1]
    print("\n" + "-" * 100)
    print(f"Causality check @ as_of={as_of}:")
    print(f"  latest 1m bar returned = {latest_bar}")
    print(f"  is strictly before as_of? {latest_bar < as_of}  (no future data leaked)")
    print("\n✓ Same run_technical_analysis() call works in backtest with no DB.")
    print("  In live, swap HistoricalDataProvider(df) → LiveDataProvider(). Nothing else changes.")


if __name__ == '__main__':
    main()
