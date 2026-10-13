#!/usr/bin/env python3
"""
ORB FADE Strategy Backtest - Mean Reversion After Breakout

Based on Optional Alpha approach:
- When price breaks ABOVE ORB high → SHORT (expect reversion)
- When price breaks BELOW ORB low → LONG (expect reversion)

This is the OPPOSITE of our current momentum-based ORB strategy.
Their approach works because most breakouts fail and price reverts.

Testing on XAUUSD with:
- 15, 30, 60 minute ORB periods (they found 60 best)
- COMEX open (13:20 UTC) - main gold session
- London open (08:00 UTC) - secondary session

Usage:
    python scripts/smc_backtest_orb_fade.py
"""

import pandas as pd
import numpy as np
from datetime import time
import pathlib

PROJECT_ROOT = pathlib.Path(__file__).parent.parent
DATA_PATH = PROJECT_ROOT / 'data' / 'snapshots' / 'XAUUSD_COMPLETE.csv'


def load_data():
    """Load and prepare OHLCV data"""
    df = pd.read_csv(DATA_PATH)
    df['timestamp'] = pd.to_datetime(df['timestamp'], utc=True).dt.tz_localize(None)
    df.set_index('timestamp', inplace=True)
    df.sort_index(inplace=True)

    # Cut off at June 2025
    cutoff = pd.Timestamp('2025-06-08')
    df = df[df.index < cutoff]

    return df


def resample(df, tf):
    """Resample to specified timeframe"""
    return df.resample(tf).agg({
        'open': 'first',
        'high': 'max',
        'low': 'min',
        'close': 'last',
        'volume': 'sum'
    }).dropna()


def add_atr(df, period=14):
    """Add ATR indicator"""
    high_low = df['high'] - df['low']
    high_close = abs(df['high'] - df['close'].shift())
    low_close = abs(df['low'] - df['close'].shift())
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    df['atr'] = tr.rolling(period).mean()
    return df


def detect_orb_levels(df, session_hour, session_minute=0, orb_minutes=60):
    """
    Detect Opening Range for each session.

    Args:
        df: DataFrame with OHLCV data (5-min bars)
        session_hour: Hour of session open (UTC)
        session_minute: Minute of session open
        orb_minutes: Duration of opening range (15, 30, 60)
    """
    orb_data = []
    df['date'] = df.index.date

    for date, day_data in df.groupby('date'):
        orb_start = session_hour * 60 + session_minute
        orb_end = orb_start + orb_minutes

        orb_bars = day_data[
            (day_data.index.hour * 60 + day_data.index.minute >= orb_start) &
            (day_data.index.hour * 60 + day_data.index.minute < orb_end)
        ]

        if len(orb_bars) < 2:
            continue

        orb_high = orb_bars['high'].max()
        orb_low = orb_bars['low'].min()
        orb_size = orb_high - orb_low
        orb_end_time = orb_bars.index[-1]
        orb_atr = orb_bars['atr'].iloc[-1] if 'atr' in orb_bars.columns else orb_size

        if pd.isna(orb_atr) or orb_atr == 0:
            continue

        # Calculate ORB midpoint for TP target
        orb_mid = (orb_high + orb_low) / 2

        orb_data.append({
            'date': date,
            'orb_end_time': orb_end_time,
            'orb_high': orb_high,
            'orb_low': orb_low,
            'orb_mid': orb_mid,
            'orb_size': orb_size,
            'orb_atr': orb_atr,
            'orb_size_atr': orb_size / orb_atr
        })

    return pd.DataFrame(orb_data)


def backtest_orb_fade(df, orb_df,
                      min_breakout_atr=0.0,
                      max_breakout_atr=2.0,
                      min_orb_size_pct=0.2,
                      tp_target='mid',  # 'mid' = ORB midpoint, 'opposite' = opposite ORB level
                      rr=1.0,  # Risk:Reward (1.0 means equal risk/reward)
                      max_holding_bars=100,
                      session_end_hour=None):
    """
    Backtest ORB FADE strategy - trade AGAINST the breakout.

    This is the Optional Alpha approach:
    - Breakout above ORB high → SHORT (expect reversion)
    - Breakout below ORB low → LONG (expect reversion)

    Args:
        df: OHLCV DataFrame (5-min bars)
        orb_df: ORB levels DataFrame
        min_breakout_atr: Minimum breakout distance to trigger entry
        max_breakout_atr: Maximum breakout (skip if too extreme)
        min_orb_size_pct: Minimum ORB size as % of price (Optional Alpha uses 0.2%)
        tp_target: Where to take profit - 'mid' or 'opposite'
        rr: Risk:Reward ratio
        max_holding_bars: Max bars to hold
        session_end_hour: Force close at this hour
    """
    trades = []
    idx = df.index
    highs = df['high'].values
    lows = df['low'].values
    opens = df['open'].values
    closes = df['close'].values
    atrs = df['atr'].values if 'atr' in df.columns else None

    for _, orb in orb_df.iterrows():
        orb_end = orb['orb_end_time']
        orb_high = orb['orb_high']
        orb_low = orb['orb_low']
        orb_mid = orb['orb_mid']
        orb_size = orb['orb_size']
        orb_atr = orb['orb_atr']

        # Filter: Minimum ORB size (like Optional Alpha's 0.2% rule)
        # For gold at ~$2000, 0.2% = $4
        price_approx = orb_mid
        min_size = price_approx * min_orb_size_pct / 100
        if orb_size < min_size:
            continue

        try:
            orb_end_idx = idx.get_loc(orb_end)
        except:
            continue

        # Look for breakout after ORB
        start_idx = orb_end_idx + 1
        end_idx = min(orb_end_idx + 60, len(df))  # Look within 5 hours

        breakout_found = False
        breakout_type = None
        entry_idx = None

        for j in range(start_idx, end_idx):
            curr_close = closes[j]
            curr_atr = atrs[j] if atrs is not None else orb_atr

            if pd.isna(curr_atr) or curr_atr == 0:
                curr_atr = orb_atr

            # Breakout ABOVE ORB high → FADE with SHORT
            if curr_close > orb_high:
                breakout_dist = curr_close - orb_high
                breakout_atr = breakout_dist / curr_atr

                if min_breakout_atr <= breakout_atr <= max_breakout_atr:
                    breakout_found = True
                    breakout_type = 'short'  # FADE = go opposite
                    entry_idx = j
                    break

            # Breakout BELOW ORB low → FADE with LONG
            if curr_close < orb_low:
                breakout_dist = orb_low - curr_close
                breakout_atr = breakout_dist / curr_atr

                if min_breakout_atr <= breakout_atr <= max_breakout_atr:
                    breakout_found = True
                    breakout_type = 'long'  # FADE = go opposite
                    entry_idx = j
                    break

            # Skip if price moves too far without clean breakout
            if highs[j] > orb_high + orb_atr * max_breakout_atr:
                break
            if lows[j] < orb_low - orb_atr * max_breakout_atr:
                break

        if not breakout_found:
            continue

        # Entry on breakout bar close
        entry_price = closes[entry_idx]
        entry_time = idx[entry_idx]

        # Calculate SL and TP for FADE trade
        if breakout_type == 'short':
            # Shorting after upside breakout
            # SL = further above (continuation risk)
            # TP = back toward ORB (reversion target)
            if tp_target == 'mid':
                tp = orb_mid
            else:
                tp = orb_low  # More aggressive - opposite side

            # Calculate risk based on R:R
            expected_profit = entry_price - tp
            risk = expected_profit / rr
            sl = entry_price + risk

        else:  # long
            # Longing after downside breakout
            if tp_target == 'mid':
                tp = orb_mid
            else:
                tp = orb_high

            expected_profit = tp - entry_price
            risk = expected_profit / rr
            sl = entry_price - risk

        # Simulate trade
        result = 'timeout'
        exit_idx = None
        exit_price = None

        for k in range(entry_idx + 1, min(entry_idx + max_holding_bars + 1, len(df))):
            # Session end filter
            if session_end_hour is not None:
                if idx[k].hour >= session_end_hour:
                    result = 'timeout'
                    exit_idx = k
                    exit_price = closes[k]
                    break

            if breakout_type == 'short':
                if highs[k] >= sl:
                    result = 'loss'
                    exit_idx = k
                    exit_price = sl
                    break
                if lows[k] <= tp:
                    result = 'win'
                    exit_idx = k
                    exit_price = tp
                    break
            else:  # long
                if lows[k] <= sl:
                    result = 'loss'
                    exit_idx = k
                    exit_price = sl
                    break
                if highs[k] >= tp:
                    result = 'win'
                    exit_idx = k
                    exit_price = tp
                    break

        if result != 'timeout' and exit_idx is not None:
            trades.append({
                'date': orb['date'],
                'time': entry_time,
                'type': breakout_type,
                'result': result,
                'orb_size': orb_size,
                'entry_price': entry_price,
                'exit_price': exit_price,
                'sl': sl,
                'tp': tp,
                'hour': entry_time.hour
            })

    return trades


def calculate_metrics(trades, rr=1.0):
    """Calculate performance metrics"""
    if not trades:
        return {'trades': 0, 'wins': 0, 'wr': 0, 'exp': 0}

    wins = sum(1 for t in trades if t['result'] == 'win')
    total = len(trades)
    wr = wins / total * 100
    exp = (wr / 100 * rr) - ((100 - wr) / 100)

    return {
        'trades': total,
        'wins': wins,
        'wr': wr,
        'exp': exp
    }


def main():
    print("=" * 70)
    print("ORB FADE STRATEGY BACKTEST - Mean Reversion After Breakout")
    print("Based on Optional Alpha approach - FADE the breakout")
    print("=" * 70)

    # Load data
    print("\nLoading data...")
    df = load_data()
    print(f"Loaded {len(df)} 1-min bars")

    # Resample to 5-min for strategy
    data_5 = resample(df, '5min')
    data_5 = add_atr(data_5)
    print(f"Resampled to {len(data_5)} 5-min bars")

    # Calculate data duration
    days = (data_5.index[-1] - data_5.index[0]).days
    years = days / 365
    print(f"Data spans {years:.2f} years")

    # Train/Test split
    train_end = pd.Timestamp('2024-01-01')
    data_train = data_5[data_5.index < train_end]
    data_test = data_5[data_5.index >= train_end]

    years_train = (data_train.index[-1] - data_train.index[0]).days / 365
    years_test = (data_test.index[-1] - data_test.index[0]).days / 365

    print(f"\nTRAIN: {data_train.index[0].date()} to {data_train.index[-1].date()} ({years_train:.1f} years)")
    print(f"TEST:  {data_test.index[0].date()} to {data_test.index[-1].date()} ({years_test:.1f} years)")

    # ==========================================================================
    # TEST DIFFERENT ORB DURATIONS AND SESSIONS
    # ==========================================================================

    sessions = [
        ("COMEX (13:20 UTC)", 13, 20),
        ("London (08:00 UTC)", 8, 0),
    ]

    orb_durations = [15, 30, 60]
    rr_options = [0.5, 1.0, 1.5, 2.0]

    for session_name, session_hour, session_minute in sessions:
        print(f"\n{'=' * 70}")
        print(f"ORB FADE - {session_name}")
        print(f"{'=' * 70}")

        for orb_minutes in orb_durations:
            print(f"\n--- {orb_minutes}-Minute ORB ---")

            # Detect ORB levels
            orb_train = detect_orb_levels(data_train, session_hour, session_minute, orb_minutes)
            orb_test = detect_orb_levels(data_test, session_hour, session_minute, orb_minutes)

            print(f"ORB sessions - TRAIN: {len(orb_train)}, TEST: {len(orb_test)}")

            if len(orb_train) > 0:
                avg_size = orb_train['orb_size'].mean()
                avg_pct = (avg_size / orb_train['orb_mid'].mean()) * 100
                print(f"Avg ORB size: ${avg_size:.2f} ({avg_pct:.2f}%)")

            print(f"\n{'Config':<25} | {'TRAIN':^25} | {'TEST':^25}")
            print(f"{'':<25} | {'Trades':>6} {'WR%':>6} {'Exp':>7} /yr | {'Trades':>6} {'WR%':>6} {'Exp':>7} /yr")
            print("-" * 85)

            best_config = None
            best_test_exp = -999

            for rr in rr_options:
                for tp_target in ['mid', 'opposite']:
                    for min_bo in [0.0, 0.25, 0.5]:
                        config_name = f"RR{rr} {tp_target[:3]} bo>{min_bo}"

                        # TRAIN
                        trades_train = backtest_orb_fade(
                            data_train, orb_train,
                            min_breakout_atr=min_bo,
                            max_breakout_atr=2.0,
                            min_orb_size_pct=0.2,
                            tp_target=tp_target,
                            rr=rr
                        )
                        m_train = calculate_metrics(trades_train, rr)
                        per_yr_train = m_train['trades'] / years_train if years_train > 0 else 0

                        # TEST
                        trades_test = backtest_orb_fade(
                            data_test, orb_test,
                            min_breakout_atr=min_bo,
                            max_breakout_atr=2.0,
                            min_orb_size_pct=0.2,
                            tp_target=tp_target,
                            rr=rr
                        )
                        m_test = calculate_metrics(trades_test, rr)
                        per_yr_test = m_test['trades'] / years_test if years_test > 0 else 0

                        # Only print promising configs
                        if m_train['trades'] >= 20 and m_train['wr'] >= 45:
                            print(f"{config_name:<25} | {m_train['trades']:>6} {m_train['wr']:>5.1f}% {m_train['exp']:>+6.2f}R {per_yr_train:>3.0f} | "
                                  f"{m_test['trades']:>6} {m_test['wr']:>5.1f}% {m_test['exp']:>+6.2f}R {per_yr_test:>3.0f}")

                            if m_test['exp'] > best_test_exp and m_test['trades'] >= 10:
                                best_test_exp = m_test['exp']
                                best_config = {
                                    'name': config_name,
                                    'orb_min': orb_minutes,
                                    'train': m_train,
                                    'test': m_test,
                                    'session': session_name
                                }

            if best_config and best_config['test']['exp'] > 0:
                print(f"\n✓ Best: {best_config['name']} - Test: {best_config['test']['wr']:.1f}% WR, {best_config['test']['exp']:+.3f}R")

    # ==========================================================================
    # COMPARE WITH MOMENTUM ORB AND SWEEPS
    # ==========================================================================
    print("\n" + "=" * 70)
    print("COMPARISON: FADE vs MOMENTUM vs SWEEPS")
    print("=" * 70)

    print("""
Strategy Comparison (Out-of-Sample Test Results):

| Strategy                     | Approach    | Test WR | Test Exp | Status    |
|------------------------------|-------------|---------|----------|-----------|
| Sweep + Disp 1.0x            | Momentum    | 67.5%   | +1.025R  | VALIDATED |
| ORB Momentum (best)          | Momentum    | ???     | ???      | ???       |
| ORB Fade (best)              | Reversion   | ???     | ???      | ???       |

Key Insight:
- Optional Alpha trades EQUITY INDICES (SPY/QQQ) - more mean-reverting
- Gold (XAUUSD) tends to TREND more strongly
- Fade strategies may work worse on trending instruments like gold
- Our Sweep strategy already captures reversion after failed breakouts
""")

    # ==========================================================================
    # SUMMARY
    # ==========================================================================
    print("\n" + "=" * 70)
    print("ORB FADE STRATEGY SUMMARY")
    print("=" * 70)
    print("""
FADE APPROACH (Optional Alpha Style):
- When price breaks ABOVE ORB → SHORT (expect reversion)
- When price breaks BELOW ORB → LONG (expect reversion)
- Betting that most breakouts fail

KEY DIFFERENCES FROM MOMENTUM ORB:
- Momentum: Trade WITH breakout, expect continuation
- Fade: Trade AGAINST breakout, expect reversion

WHY THIS MIGHT NOT WORK AS WELL ON GOLD:
1. Gold is a trending instrument (especially 2024-2025 bull run)
2. COMEX/London opens often see genuine breakouts, not fakeouts
3. Institutional flows create directional momentum
4. Our Sweep strategy already captures the "failed breakout" edge

RECOMMENDATION:
- Stick with validated Sweep strategy (+1.025R)
- ORB Fade may work on equity indices but less suited for gold
- Consider testing on ES/NQ futures if interested in fade approach
""")


if __name__ == '__main__':
    main()
