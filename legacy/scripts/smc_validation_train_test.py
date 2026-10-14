#!/usr/bin/env python3
"""
SMC Strategy Train/Test Validation Framework

Proper out-of-sample validation to prevent overfitting.

Data Split:
- TRAIN: Apr 2021 - Dec 2023 (2.7 years) - Parameter optimization
- TEST:  Jan 2024 - Jun 2025 (1.5 years) - Validation on unseen data

Usage:
    python scripts/smc_validation_train_test.py

This script validates:
1. Liquidity Sweeps (Option A: 0.75x ATR displacement, no HTF filter)
2. Liquidity Sweeps (Option B: 1.0x ATR displacement, no HTF filter)
3. Order Blocks (with HTF filter)
4. FVG-Leads-OB combination
"""

import pandas as pd
import numpy as np
import warnings
from datetime import datetime
warnings.filterwarnings('ignore')

# =============================================================================
# DATA LOADING AND PREPARATION
# =============================================================================

def load_data():
    """Load XAUUSD data with June 2025 cutoff"""
    print("Loading XAUUSD_COMPLETE.csv...")
    df = pd.read_csv('data/snapshots/XAUUSD_COMPLETE.csv')
    df['timestamp'] = pd.to_datetime(df['timestamp'], utc=True).dt.tz_localize(None)
    df.set_index('timestamp', inplace=True)

    # Cut off at June 2025 (before the gap)
    cutoff = pd.Timestamp('2025-06-08')
    df = df[df.index < cutoff]

    print(f"Data range: {df.index[0]} to {df.index[-1]}")
    print(f"Total rows: {len(df):,}")
    return df

def split_data(df):
    """Split into train and test sets"""
    train_end = pd.Timestamp('2023-12-31 23:59:59')

    train = df[df.index <= train_end].copy()
    test = df[df.index > train_end].copy()

    print(f"\nTRAIN: {train.index[0].date()} to {train.index[-1].date()} ({len(train):,} rows)")
    print(f"TEST:  {test.index[0].date()} to {test.index[-1].date()} ({len(test):,} rows)")

    return train, test

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
    tr = pd.concat([
        df['high'] - df['low'],
        abs(df['high'] - df['close'].shift(1)),
        abs(df['low'] - df['close'].shift(1))
    ], axis=1).max(axis=1)
    df['atr'] = tr.rolling(period).mean()
    return df

def get_swings(df, window=5):
    """Detect swing highs and lows"""
    h_arr, l_arr = df['high'].values, df['low'].values
    swings_h, swings_l = [], []

    for i in range(window, len(df) - window):
        if h_arr[i] == max(h_arr[i-window:i+window+1]):
            swings_h.append((df.index[i], h_arr[i]))
        if l_arr[i] == min(l_arr[i-window:i+window+1]):
            swings_l.append((df.index[i], l_arr[i]))

    return swings_h, swings_l

# =============================================================================
# LIQUIDITY SWEEP STRATEGY
# =============================================================================

def detect_sweeps_with_displacement(df, ltf_h_times, ltf_h_prices, ltf_l_times, ltf_l_prices,
                                     lookback=30, min_atr=0.5, disp_atr=0.75):
    """Detect liquidity sweeps with displacement confirmation"""
    sweeps = []
    idx = df.index
    highs = df['high'].values
    lows = df['low'].values
    closes = df['close'].values
    opens = df['open'].values
    atrs = df['atr'].values

    for i in range(lookback, len(df) - 1):
        ts = idx[i]
        ts_val = ts.value
        h, l, c, o, atr = highs[i], lows[i], closes[i], opens[i], atrs[i]

        if pd.isna(atr):
            continue

        next_h, next_l, next_c, next_o = highs[i+1], lows[i+1], closes[i+1], opens[i+1]

        lookback_ts = idx[i - lookback].value
        mask_h = (ltf_h_times > lookback_ts) & (ltf_h_times < ts_val)
        mask_l = (ltf_l_times > lookback_ts) & (ltf_l_times < ts_val)

        # BEARISH SWEEP
        if mask_h.any():
            rec_h = ltf_h_prices[mask_h]
            for lv in rec_h:
                if h > lv and c < lv and (h - lv) >= atr * min_atr and c < o:
                    if next_c < c and (c - next_c) >= atr * disp_atr:
                        sweeps.append({
                            'time': ts, 'type': 'bear', 'level': lv,
                            'atr': atr, 'entry': c
                        })
                        break

        # BULLISH SWEEP
        if mask_l.any():
            rec_l = ltf_l_prices[mask_l]
            for lv in rec_l:
                if l < lv and c > lv and (lv - l) >= atr * min_atr and c > o:
                    if next_c > c and (next_c - c) >= atr * disp_atr:
                        sweeps.append({
                            'time': ts, 'type': 'bull', 'level': lv,
                            'atr': atr, 'entry': c
                        })
                        break

    return pd.DataFrame(sweeps)

def backtest_sweeps(df, sweeps_df, rr=2.0, fresh_filter=True):
    """Backtest sweep signals (no HTF filter - Option A/B style)"""
    if len(sweeps_df) == 0:
        return []

    idx = df.index
    highs = df['high'].values
    lows = df['low'].values
    closes = df['close'].values

    trades = []
    tested_levels = set()

    for _, sw in sweeps_df.iterrows():
        t, typ, lv = sw['time'], sw['type'], sw['level']

        try:
            si = idx.get_loc(t)
        except:
            continue

        if si + 5 >= len(df):
            continue

        # Fresh level filter
        if fresh_filter:
            lk = round(lv, 1)
            if lk in tested_levels:
                continue
            tested_levels.add(lk)

        # Entry setup
        entry_price = closes[si]
        if typ == 'bull':
            sl = lows[si] - 0.5
            risk = entry_price - sl
            tp = entry_price + risk * rr
            trade_type = 'L'
        else:
            sl = highs[si] + 0.5
            risk = sl - entry_price
            tp = entry_price - risk * rr
            trade_type = 'S'

        # Simulate trade
        result = 'timeout'
        for k in range(si + 1, min(si + 101, len(df))):
            if trade_type == 'L':
                if lows[k] <= sl:
                    result = 'loss'
                    break
                if highs[k] >= tp:
                    result = 'win'
                    break
            else:
                if highs[k] >= sl:
                    result = 'loss'
                    break
                if lows[k] <= tp:
                    result = 'win'
                    break

        if result != 'timeout':
            trades.append({
                'time': t,
                'type': typ,
                'result': result,
                'risk': risk
            })

    return trades

# =============================================================================
# ORDER BLOCK STRATEGY
# =============================================================================

def detect_order_blocks(df, ltf_h_times, ltf_h_prices, ltf_l_times, ltf_l_prices,
                        htf_h_times, htf_h_prices, htf_l_times, htf_l_prices,
                        min_imbalance=1.5, lookback=20):
    """Detect order blocks with HTF trend filter"""
    obs = []
    idx = df.index
    highs = df['high'].values
    lows = df['low'].values
    closes = df['close'].values
    opens = df['open'].values
    atrs = df['atr'].values

    for i in range(lookback, len(df) - 3):
        ts = idx[i]
        ts_val = ts.value
        h, l, c, o, atr = highs[i], lows[i], closes[i], opens[i], atrs[i]

        if pd.isna(atr):
            continue

        body = abs(c - o)

        # Check for impulsive candle
        if body < atr * min_imbalance:
            continue

        # Get HTF trend
        mask_h = htf_h_times < ts_val
        mask_l = htf_l_times < ts_val
        rh = htf_h_prices[mask_h][-5:] if mask_h.any() else []
        rl = htf_l_prices[mask_l][-5:] if mask_l.any() else []

        if len(rh) < 2 or len(rl) < 2:
            continue

        bull = int(rh[-1] > rh[-2]) + int(rl[-1] > rl[-2])
        bear = int(rh[-1] < rh[-2]) + int(rl[-1] < rl[-2])
        trend = 'B' if bull > bear else ('S' if bear > bull else 'N')

        # Bullish OB: bearish candle before bullish impulse, with trend
        if c > o and trend == 'B':
            # Look for preceding bearish candle
            for j in range(max(0, i-3), i):
                if closes[j] < opens[j]:
                    obs.append({
                        'time': ts,
                        'type': 'bull',
                        'ob_high': highs[j],
                        'ob_low': lows[j],
                        'atr': atr
                    })
                    break

        # Bearish OB: bullish candle before bearish impulse, with trend
        elif c < o and trend == 'S':
            for j in range(max(0, i-3), i):
                if closes[j] > opens[j]:
                    obs.append({
                        'time': ts,
                        'type': 'bear',
                        'ob_high': highs[j],
                        'ob_low': lows[j],
                        'atr': atr
                    })
                    break

    return pd.DataFrame(obs)

def backtest_order_blocks(df, obs_df, rr=2.0):
    """Backtest order block entries"""
    if len(obs_df) == 0:
        return []

    idx = df.index
    highs = df['high'].values
    lows = df['low'].values
    closes = df['close'].values

    trades = []

    for _, ob in obs_df.iterrows():
        t, typ = ob['time'], ob['type']
        ob_high, ob_low = ob['ob_high'], ob['ob_low']

        try:
            si = idx.get_loc(t)
        except:
            continue

        if si + 5 >= len(df):
            continue

        # Wait for retest
        entry_found = False
        entry_idx = None

        for k in range(si + 1, min(si + 50, len(df))):
            if typ == 'bull':
                # Price retests OB zone
                if lows[k] <= ob_high and lows[k] >= ob_low:
                    entry_found = True
                    entry_idx = k
                    break
            else:
                if highs[k] >= ob_low and highs[k] <= ob_high:
                    entry_found = True
                    entry_idx = k
                    break

        if not entry_found:
            continue

        # Entry setup
        entry_price = closes[entry_idx]
        if typ == 'bull':
            sl = ob_low - 0.5
            risk = entry_price - sl
            tp = entry_price + risk * rr
            trade_type = 'L'
        else:
            sl = ob_high + 0.5
            risk = sl - entry_price
            tp = entry_price - risk * rr
            trade_type = 'S'

        # Simulate trade
        result = 'timeout'
        for k in range(entry_idx + 1, min(entry_idx + 101, len(df))):
            if trade_type == 'L':
                if lows[k] <= sl:
                    result = 'loss'
                    break
                if highs[k] >= tp:
                    result = 'win'
                    break
            else:
                if highs[k] >= sl:
                    result = 'loss'
                    break
                if lows[k] <= tp:
                    result = 'win'
                    break

        if result != 'timeout':
            trades.append({
                'time': t,
                'type': typ,
                'result': result
            })

    return trades

# =============================================================================
# FVG-LEADS-OB STRATEGY
# =============================================================================

def detect_fvgs(df, min_gap_atr=0.5):
    """
    Detect Fair Value Gaps (FVGs).

    An FVG is a 3-candle pattern where wicks of candle 1 and candle 3 don't overlap.
    """
    fvgs = []
    idx = df.index
    highs = df['high'].values
    lows = df['low'].values
    atrs = df['atr'].values

    for i in range(2, len(df)):
        atr = atrs[i]
        if pd.isna(atr):
            continue

        c1_high, c1_low = highs[i-2], lows[i-2]
        c3_high, c3_low = highs[i], lows[i]

        # Bullish FVG: Gap between c1 high and c3 low
        if c3_low > c1_high:
            gap_size = c3_low - c1_high
            if gap_size >= atr * min_gap_atr:
                fvgs.append({
                    'time': idx[i-1],
                    'type': 'bull',
                    'fvg_high': c3_low,
                    'fvg_low': c1_high,
                    'gap_size': gap_size,
                    'atr': atr
                })

        # Bearish FVG: Gap between c1 low and c3 high
        if c3_high < c1_low:
            gap_size = c1_low - c3_high
            if gap_size >= atr * min_gap_atr:
                fvgs.append({
                    'time': idx[i-1],
                    'type': 'bear',
                    'fvg_high': c1_low,
                    'fvg_low': c3_high,
                    'gap_size': gap_size,
                    'atr': atr
                })

    return pd.DataFrame(fvgs)

def backtest_fvg_leads_ob(df, fvgs_df, htf_h_times, htf_h_prices, htf_l_times, htf_l_prices,
                          rr=2.0, htf_filter=True, fresh_filter=True,
                          leave_atr=0.3, displacement_atr=1.0, max_bars_return=100,
                          best_hours=None):
    """
    Backtest FVG-Leads-OB Strategy.

    The strategy:
    1. FVG forms (imbalance zone)
    2. Price LEAVES the FVG zone (moves away by at least leave_atr * ATR)
    3. Price RETURNS to the FVG zone
    4. An Order Block forms on the return (opposite candle + displacement)
    5. Entry on displacement candle close
    """
    if len(fvgs_df) == 0:
        return []

    idx = df.index
    highs = df['high'].values
    lows = df['low'].values
    opens = df['open'].values
    closes = df['close'].values
    atrs = df['atr'].values

    trades = []
    tested_fvgs = set()

    for _, fvg in fvgs_df.iterrows():
        fvg_time = fvg['time']
        fvg_type = fvg['type']
        fvg_high = fvg['fvg_high']
        fvg_low = fvg['fvg_low']
        fvg_atr = fvg['atr']

        try:
            fvg_idx = idx.get_loc(fvg_time)
        except:
            continue

        # HTF trend filter
        if htf_filter:
            ts_val = fvg_time.value
            mask_h = htf_h_times < ts_val
            mask_l = htf_l_times < ts_val
            rh = htf_h_prices[mask_h][-5:] if mask_h.any() else []
            rl = htf_l_prices[mask_l][-5:] if mask_l.any() else []

            if len(rh) < 2 or len(rl) < 2:
                continue

            bull = int(rh[-1] > rh[-2]) + int(rl[-1] > rl[-2])
            bear = int(rh[-1] < rh[-2]) + int(rl[-1] < rl[-2])
            trend = 'B' if bull > bear else ('S' if bear > bull else 'N')

            if fvg_type == 'bull' and trend != 'B':
                continue
            if fvg_type == 'bear' and trend != 'S':
                continue

        # Fresh FVG filter
        if fresh_filter:
            fvg_key = (round(fvg_high, 1), round(fvg_low, 1))
            if fvg_key in tested_fvgs:
                continue
            tested_fvgs.add(fvg_key)

        # STEP 1: Price must LEAVE the FVG zone first
        left_fvg = False
        left_idx = None

        for j in range(fvg_idx + 1, min(fvg_idx + max_bars_return, len(df))):
            if fvg_type == 'bull':
                if lows[j] > fvg_high + fvg_atr * leave_atr:
                    left_fvg = True
                    left_idx = j
                    break
            else:
                if highs[j] < fvg_low - fvg_atr * leave_atr:
                    left_fvg = True
                    left_idx = j
                    break

        if not left_fvg:
            continue

        # STEP 2: Look for price to RETURN to FVG zone AND form an OB
        entry_found = False
        entry_idx = None
        ob_low = None
        ob_high = None

        for j in range(left_idx + 1, min(fvg_idx + max_bars_return, len(df) - 1)):
            curr_atr = atrs[j]
            if pd.isna(curr_atr):
                continue

            curr_open = opens[j]
            curr_close = closes[j]
            curr_high = highs[j]
            curr_low = lows[j]

            # Check if price is in FVG zone
            if fvg_type == 'bull':
                in_fvg = curr_low <= fvg_high and curr_high >= fvg_low
            else:
                in_fvg = curr_high >= fvg_low and curr_low <= fvg_high

            if not in_fvg:
                continue

            # Check for OB forming: opposite candle + displacement on next candle
            if j + 1 >= len(df):
                continue

            next_close = closes[j + 1]
            next_high = highs[j + 1]
            next_low = lows[j + 1]

            if fvg_type == 'bull':
                is_ob_candle = curr_close < curr_open
                displacement = next_close - curr_high
                has_displacement = next_close > curr_high and displacement >= curr_atr * displacement_atr

                if is_ob_candle and has_displacement:
                    entry_found = True
                    entry_idx = j + 1
                    ob_low = curr_low
                    ob_high = curr_high
                    break
            else:
                is_ob_candle = curr_close > curr_open
                displacement = curr_low - next_close
                has_displacement = next_close < curr_low and displacement >= curr_atr * displacement_atr

                if is_ob_candle and has_displacement:
                    entry_found = True
                    entry_idx = j + 1
                    ob_low = curr_low
                    ob_high = curr_high
                    break

        if not entry_found:
            continue

        # Time filter (if specified)
        entry_time = idx[entry_idx]
        if best_hours is not None and entry_time.hour not in best_hours:
            continue

        # Entry on displacement candle close
        entry_price = closes[entry_idx]

        if fvg_type == 'bull':
            sl = ob_low - 0.5
            risk = entry_price - sl
            tp = entry_price + risk * rr
            trade_type = 'L'
        else:
            sl = ob_high + 0.5
            risk = sl - entry_price
            tp = entry_price - risk * rr
            trade_type = 'S'

        # Simulate trade
        result = 'timeout'
        for k in range(entry_idx + 1, min(entry_idx + 101, len(df))):
            if trade_type == 'L':
                if lows[k] <= sl:
                    result = 'loss'
                    break
                if highs[k] >= tp:
                    result = 'win'
                    break
            else:
                if highs[k] >= sl:
                    result = 'loss'
                    break
                if lows[k] <= tp:
                    result = 'win'
                    break

        if result != 'timeout':
            trades.append({
                'time': entry_time,
                'type': fvg_type,
                'result': result,
                'hour': entry_time.hour
            })

    return trades

# =============================================================================
# ANALYSIS FUNCTIONS
# =============================================================================

def calculate_metrics(trades, rr=2.0):
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

def print_results(name, train_metrics, test_metrics, years_train, years_test):
    """Print formatted results"""
    print(f"\n{'='*70}")
    print(f"{name}")
    print(f"{'='*70}")

    print(f"\n{'Set':<10} | {'Trades':>7} | {'Per Year':>8} | {'WR%':>6} | {'Expectancy':>10}")
    print("-" * 55)

    train_per_year = train_metrics['trades'] / years_train if years_train > 0 else 0
    test_per_year = test_metrics['trades'] / years_test if years_test > 0 else 0

    print(f"{'TRAIN':<10} | {train_metrics['trades']:>7} | {train_per_year:>7.0f}/yr | {train_metrics['wr']:>5.1f}% | {train_metrics['exp']:>+9.3f}R")
    print(f"{'TEST':<10} | {test_metrics['trades']:>7} | {test_per_year:>7.0f}/yr | {test_metrics['wr']:>5.1f}% | {test_metrics['exp']:>+9.3f}R")

    # Degradation analysis
    if train_metrics['trades'] > 0 and test_metrics['trades'] > 0:
        wr_change = test_metrics['wr'] - train_metrics['wr']
        exp_change = test_metrics['exp'] - train_metrics['exp']
        print(f"\n{'CHANGE':<10} | {'':>7} | {'':>8} | {wr_change:>+5.1f}% | {exp_change:>+9.3f}R")

        if test_metrics['exp'] > 0:
            print("✓ PASSED: Positive expectancy on unseen data")
        else:
            print("✗ FAILED: Negative expectancy on unseen data")

# =============================================================================
# MAIN VALIDATION
# =============================================================================

def main():
    print("=" * 70)
    print("SMC STRATEGY TRAIN/TEST VALIDATION")
    print("=" * 70)
    print("\nPurpose: Validate strategies on unseen data to prevent overfitting")
    print("TRAIN: Apr 2021 - Dec 2023 (parameter optimization)")
    print("TEST:  Jan 2024 - Jun 2025 (out-of-sample validation)")

    # Load and split data
    df_raw = load_data()
    train_raw, test_raw = split_data(df_raw)

    # Calculate years for each set
    years_train = (train_raw.index[-1] - train_raw.index[0]).days / 365.25
    years_test = (test_raw.index[-1] - test_raw.index[0]).days / 365.25
    print(f"\nTrain period: {years_train:.2f} years")
    print(f"Test period: {years_test:.2f} years")

    # Process both sets
    results = {}

    for set_name, df_set in [('TRAIN', train_raw), ('TEST', test_raw)]:
        print(f"\n{'='*70}")
        print(f"PROCESSING {set_name} SET")
        print(f"{'='*70}")

        # Resample
        data_15 = add_atr(resample(df_set, '15min'))
        data_30 = add_atr(resample(df_set, '30min'))
        print(f"15min bars: {len(data_15)}")
        print(f"30min bars: {len(data_30)}")

        # Detect swings
        print("Detecting swings...")
        htf_h, htf_l = get_swings(data_30, 5)
        ltf_h, ltf_l = get_swings(data_15, 5)
        print(f"HTF swings: {len(htf_h)} highs, {len(htf_l)} lows")
        print(f"LTF swings: {len(ltf_h)} highs, {len(ltf_l)} lows")

        # Convert to numpy
        htf_h_times = np.array([t.value for t, _ in htf_h])
        htf_h_prices = np.array([p for _, p in htf_h])
        htf_l_times = np.array([t.value for t, _ in htf_l])
        htf_l_prices = np.array([p for _, p in htf_l])
        ltf_h_times = np.array([t.value for t, _ in ltf_h])
        ltf_h_prices = np.array([p for _, p in ltf_h])
        ltf_l_times = np.array([t.value for t, _ in ltf_l])
        ltf_l_prices = np.array([p for _, p in ltf_l])

        # =====================================================================
        # STRATEGY 1: Sweeps Option A (0.75x ATR displacement)
        # =====================================================================
        print("\nRunning Sweeps (Option A: 0.75x ATR)...")
        sweeps_a = detect_sweeps_with_displacement(
            data_15, ltf_h_times, ltf_h_prices, ltf_l_times, ltf_l_prices,
            lookback=30, min_atr=0.5, disp_atr=0.75
        )
        trades_a = backtest_sweeps(data_15, sweeps_a, rr=2.0, fresh_filter=True)
        results[f'{set_name}_sweeps_a'] = calculate_metrics(trades_a)
        print(f"  Sweeps: {len(sweeps_a)}, Trades: {len(trades_a)}")

        # =====================================================================
        # STRATEGY 2: Sweeps Option B (1.0x ATR displacement)
        # =====================================================================
        print("Running Sweeps (Option B: 1.0x ATR)...")
        sweeps_b = detect_sweeps_with_displacement(
            data_15, ltf_h_times, ltf_h_prices, ltf_l_times, ltf_l_prices,
            lookback=30, min_atr=0.5, disp_atr=1.0
        )
        trades_b = backtest_sweeps(data_15, sweeps_b, rr=2.0, fresh_filter=True)
        results[f'{set_name}_sweeps_b'] = calculate_metrics(trades_b)
        print(f"  Sweeps: {len(sweeps_b)}, Trades: {len(trades_b)}")

        # =====================================================================
        # STRATEGY 3: Order Blocks
        # =====================================================================
        print("Running Order Blocks...")
        obs = detect_order_blocks(
            data_15, ltf_h_times, ltf_h_prices, ltf_l_times, ltf_l_prices,
            htf_h_times, htf_h_prices, htf_l_times, htf_l_prices,
            min_imbalance=1.5, lookback=20
        )
        trades_ob = backtest_order_blocks(data_15, obs, rr=2.0)
        results[f'{set_name}_ob'] = calculate_metrics(trades_ob)
        print(f"  OBs: {len(obs)}, Trades: {len(trades_ob)}")

        # =====================================================================
        # STRATEGY 4: FVG-Leads-OB (No Time Filter)
        # =====================================================================
        print("Running FVG-Leads-OB...")
        fvgs = detect_fvgs(data_15, min_gap_atr=0.5)
        trades_fvg = backtest_fvg_leads_ob(
            data_15, fvgs, htf_h_times, htf_h_prices, htf_l_times, htf_l_prices,
            rr=2.0, htf_filter=True, fresh_filter=True,
            leave_atr=0.3, displacement_atr=1.0, best_hours=None
        )
        results[f'{set_name}_fvg'] = calculate_metrics(trades_fvg)
        print(f"  FVGs: {len(fvgs)}, Trades: {len(trades_fvg)}")

        # =====================================================================
        # STRATEGY 5: FVG-Leads-OB (With Best Hours Filter)
        # =====================================================================
        print("Running FVG-Leads-OB (best hours: 0, 8, 15, 20, 23)...")
        best_hours = [0, 8, 15, 20, 23]
        trades_fvg_filtered = backtest_fvg_leads_ob(
            data_15, fvgs, htf_h_times, htf_h_prices, htf_l_times, htf_l_prices,
            rr=2.0, htf_filter=True, fresh_filter=True,
            leave_atr=0.3, displacement_atr=1.0, best_hours=best_hours
        )
        results[f'{set_name}_fvg_hours'] = calculate_metrics(trades_fvg_filtered)
        print(f"  Trades (filtered): {len(trades_fvg_filtered)}")

    # =========================================================================
    # FINAL RESULTS
    # =========================================================================
    print("\n")
    print("=" * 70)
    print("VALIDATION RESULTS SUMMARY")
    print("=" * 70)

    print_results(
        "STRATEGY 1: Liquidity Sweeps (Option A - High Frequency)",
        results['TRAIN_sweeps_a'],
        results['TEST_sweeps_a'],
        years_train, years_test
    )

    print_results(
        "STRATEGY 2: Liquidity Sweeps (Option B - High Quality)",
        results['TRAIN_sweeps_b'],
        results['TEST_sweeps_b'],
        years_train, years_test
    )

    print_results(
        "STRATEGY 3: Order Blocks (with HTF filter)",
        results['TRAIN_ob'],
        results['TEST_ob'],
        years_train, years_test
    )

    print_results(
        "STRATEGY 4: FVG-Leads-OB (no time filter)",
        results['TRAIN_fvg'],
        results['TEST_fvg'],
        years_train, years_test
    )

    print_results(
        "STRATEGY 5: FVG-Leads-OB (best hours: 0,8,15,20,23)",
        results['TRAIN_fvg_hours'],
        results['TEST_fvg_hours'],
        years_train, years_test
    )

    # Summary
    print("\n")
    print("=" * 70)
    print("VALIDATION SUMMARY")
    print("=" * 70)

    passed = []
    failed = []

    for strat_name, train_key, test_key in [
        ("Sweeps Option A", 'TRAIN_sweeps_a', 'TEST_sweeps_a'),
        ("Sweeps Option B", 'TRAIN_sweeps_b', 'TEST_sweeps_b'),
        ("Order Blocks", 'TRAIN_ob', 'TEST_ob'),
        ("FVG-Leads-OB", 'TRAIN_fvg', 'TEST_fvg'),
        ("FVG-Leads-OB (hours)", 'TRAIN_fvg_hours', 'TEST_fvg_hours')
    ]:
        test_exp = results[test_key]['exp']
        if test_exp > 0:
            passed.append(f"{strat_name}: {test_exp:+.3f}R")
        else:
            failed.append(f"{strat_name}: {test_exp:+.3f}R")

    print("\n✓ PASSED (positive out-of-sample expectancy):")
    for p in passed:
        print(f"  - {p}")

    if failed:
        print("\n✗ FAILED (negative out-of-sample expectancy):")
        for f in failed:
            print(f"  - {f}")

    print("\n" + "=" * 70)
    print("INTERPRETATION GUIDE")
    print("=" * 70)
    print("""
1. EXPECTANCY DEGRADATION:
   - Some degradation (0.1-0.3R) is NORMAL and expected
   - Large degradation (>0.5R) suggests overfitting
   - Negative test expectancy = strategy likely overfit

2. TRADE FREQUENCY:
   - Should be similar between train and test (per year)
   - Large difference suggests regime sensitivity

3. WIN RATE CHANGE:
   - ±5% change is normal
   - >10% drop suggests instability

4. STATISTICAL SIGNIFICANCE:
   - Need 30+ trades per set minimum
   - 100+ trades preferred for reliability
""")

if __name__ == '__main__':
    main()
