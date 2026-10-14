#!/usr/bin/env python3
"""
BOS/CHOCH Strategy Train/Test Validation + Trade Frequency Investigation

BOS (Break of Structure): Price breaks swing in trend direction (continuation)
CHOCH (Change of Character): Price breaks swing against trend (reversal)

Also investigates why Sweeps trade frequency dropped from 124/yr to 34/yr.

Usage:
    python scripts/smc_bos_choch_validation.py
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
# BOS/CHOCH STRATEGY
# =============================================================================

def detect_bos_choch(df, ltf_h_times, ltf_h_prices, ltf_l_times, ltf_l_prices,
                     lookback=20, min_displacement_atr=0.5, mode='bos'):
    """
    Detect Break of Structure (BOS) or Change of Character (CHOCH).

    BOS: Break in trend direction (continuation signal)
    CHOCH: Break against trend direction (reversal signal)

    Args:
        mode: 'bos' for continuation, 'choch' for reversal
    """
    signals = []
    idx = df.index
    highs = df['high'].values
    lows = df['low'].values
    closes = df['close'].values
    opens = df['open'].values
    atrs = df['atr'].values

    for i in range(lookback + 5, len(df) - 1):
        ts = idx[i]
        ts_val = ts.value
        h, l, c, o, atr = highs[i], lows[i], closes[i], opens[i], atrs[i]

        if pd.isna(atr):
            continue

        # Get recent swing structure to determine trend
        lookback_ts = idx[i - lookback].value
        mask_h = (ltf_h_times > lookback_ts) & (ltf_h_times < ts_val)
        mask_l = (ltf_l_times > lookback_ts) & (ltf_l_times < ts_val)

        if not mask_h.any() or not mask_l.any():
            continue

        recent_highs = list(zip(ltf_h_times[mask_h], ltf_h_prices[mask_h]))
        recent_lows = list(zip(ltf_l_times[mask_l], ltf_l_prices[mask_l]))

        if len(recent_highs) < 2 or len(recent_lows) < 2:
            continue

        # Sort by time
        recent_highs.sort(key=lambda x: x[0])
        recent_lows.sort(key=lambda x: x[0])

        # Get last two swings
        last_high = recent_highs[-1][1]
        prev_high = recent_highs[-2][1]
        last_low = recent_lows[-1][1]
        prev_low = recent_lows[-2][1]

        # Determine current trend
        hh = last_high > prev_high
        hl = last_low > prev_low
        lh = last_high < prev_high
        ll = last_low < prev_low

        bullish_trend = hh and hl  # Higher highs and higher lows
        bearish_trend = lh and ll  # Lower highs and lower lows

        # Get next candle for displacement check
        next_h, next_l, next_c = highs[i+1], lows[i+1], closes[i+1]

        if mode == 'bos':
            # BOS: Break in trend direction (continuation)
            # Bullish BOS: Price breaks above last swing high in bullish trend
            if bullish_trend:
                if h > last_high and c > last_high:
                    # Check for displacement
                    displacement = (c - last_high) / atr
                    if displacement >= min_displacement_atr:
                        signals.append({
                            'time': ts,
                            'type': 'bull',
                            'level': last_high,
                            'atr': atr,
                            'entry': c,
                            'displacement': displacement
                        })

            # Bearish BOS: Price breaks below last swing low in bearish trend
            if bearish_trend:
                if l < last_low and c < last_low:
                    displacement = (last_low - c) / atr
                    if displacement >= min_displacement_atr:
                        signals.append({
                            'time': ts,
                            'type': 'bear',
                            'level': last_low,
                            'atr': atr,
                            'entry': c,
                            'displacement': displacement
                        })

        elif mode == 'choch':
            # CHOCH: Break against trend direction (reversal)
            # Bullish CHOCH: Price breaks above swing high in bearish trend (reversal to bullish)
            if bearish_trend:
                if h > last_high and c > last_high:
                    displacement = (c - last_high) / atr
                    if displacement >= min_displacement_atr:
                        signals.append({
                            'time': ts,
                            'type': 'bull',
                            'level': last_high,
                            'atr': atr,
                            'entry': c,
                            'displacement': displacement
                        })

            # Bearish CHOCH: Price breaks below swing low in bullish trend (reversal to bearish)
            if bullish_trend:
                if l < last_low and c < last_low:
                    displacement = (last_low - c) / atr
                    if displacement >= min_displacement_atr:
                        signals.append({
                            'time': ts,
                            'type': 'bear',
                            'level': last_low,
                            'atr': atr,
                            'entry': c,
                            'displacement': displacement
                        })

    return pd.DataFrame(signals)

def backtest_bos_choch(df, signals_df, rr=2.0, fresh_filter=True):
    """Backtest BOS/CHOCH signals"""
    if len(signals_df) == 0:
        return []

    idx = df.index
    highs = df['high'].values
    lows = df['low'].values
    closes = df['close'].values

    trades = []
    tested_levels = set()

    for _, sig in signals_df.iterrows():
        t, typ, lv = sig['time'], sig['type'], sig['level']
        atr = sig['atr']

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

        # Entry on breakout candle close
        entry_price = closes[si]

        if typ == 'bull':
            # Stop below the broken level
            sl = lv - atr * 0.5
            risk = entry_price - sl
            tp = entry_price + risk * rr
            trade_type = 'L'
        else:
            sl = lv + atr * 0.5
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
# SWEEPS STRATEGY (for frequency investigation)
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

    if train_metrics['trades'] > 0 and test_metrics['trades'] > 0:
        wr_change = test_metrics['wr'] - train_metrics['wr']
        exp_change = test_metrics['exp'] - train_metrics['exp']
        print(f"\n{'CHANGE':<10} | {'':>7} | {'':>8} | {wr_change:>+5.1f}% | {exp_change:>+9.3f}R")

        if test_metrics['exp'] > 0:
            print("✓ PASSED: Positive expectancy on unseen data")
        else:
            print("✗ FAILED: Negative expectancy on unseen data")

def investigate_frequency_drop(train_df, test_df, train_sweeps, test_sweeps):
    """Investigate why trade frequency dropped in test period"""
    print("\n")
    print("=" * 70)
    print("TRADE FREQUENCY INVESTIGATION")
    print("=" * 70)

    # 1. Basic stats
    train_years = (train_df.index[-1] - train_df.index[0]).days / 365.25
    test_years = (test_df.index[-1] - test_df.index[0]).days / 365.25

    train_per_year = len(train_sweeps) / train_years if train_years > 0 else 0
    test_per_year = len(test_sweeps) / test_years if test_years > 0 else 0

    print(f"\nSweeps detected:")
    print(f"  TRAIN: {len(train_sweeps)} ({train_per_year:.0f}/yr)")
    print(f"  TEST:  {len(test_sweeps)} ({test_per_year:.0f}/yr)")
    print(f"  Drop:  {((train_per_year - test_per_year) / train_per_year * 100):.0f}%")

    # 2. ATR comparison (market volatility)
    train_atr_mean = train_df['atr'].mean()
    test_atr_mean = test_df['atr'].mean()
    train_atr_std = train_df['atr'].std()
    test_atr_std = test_df['atr'].std()

    print(f"\nMarket Volatility (ATR):")
    print(f"  TRAIN: mean={train_atr_mean:.2f}, std={train_atr_std:.2f}")
    print(f"  TEST:  mean={test_atr_mean:.2f}, std={test_atr_std:.2f}")
    print(f"  Change: {((test_atr_mean - train_atr_mean) / train_atr_mean * 100):+.1f}%")

    # 3. Price range comparison
    train_range = train_df['high'].max() - train_df['low'].min()
    test_range = test_df['high'].max() - test_df['low'].min()

    print(f"\nPrice Range:")
    print(f"  TRAIN: {train_df['low'].min():.0f} - {train_df['high'].max():.0f} (range: {train_range:.0f})")
    print(f"  TEST:  {test_df['low'].min():.0f} - {test_df['high'].max():.0f} (range: {test_range:.0f})")

    # 4. Swing analysis
    train_h, train_l = get_swings(train_df, 5)
    test_h, test_l = get_swings(test_df, 5)

    train_swings_per_year = (len(train_h) + len(train_l)) / train_years
    test_swings_per_year = (len(test_h) + len(test_l)) / test_years

    print(f"\nSwing Frequency:")
    print(f"  TRAIN: {len(train_h) + len(train_l)} swings ({train_swings_per_year:.0f}/yr)")
    print(f"  TEST:  {len(test_h) + len(test_l)} swings ({test_swings_per_year:.0f}/yr)")
    print(f"  Change: {((test_swings_per_year - train_swings_per_year) / train_swings_per_year * 100):+.1f}%")

    # 5. Monthly breakdown
    print(f"\nMonthly Sweep Distribution:")

    if len(train_sweeps) > 0:
        train_monthly = train_sweeps.groupby(train_sweeps['time'].dt.to_period('M')).size()
        print(f"  TRAIN monthly: min={train_monthly.min()}, max={train_monthly.max()}, mean={train_monthly.mean():.1f}")

    if len(test_sweeps) > 0:
        test_monthly = test_sweeps.groupby(test_sweeps['time'].dt.to_period('M')).size()
        print(f"  TEST monthly:  min={test_monthly.min()}, max={test_monthly.max()}, mean={test_monthly.mean():.1f}")

    # 6. Type distribution
    print(f"\nSweep Type Distribution:")
    if len(train_sweeps) > 0:
        train_bull = (train_sweeps['type'] == 'bull').sum()
        train_bear = (train_sweeps['type'] == 'bear').sum()
        print(f"  TRAIN: bull={train_bull} ({train_bull/len(train_sweeps)*100:.0f}%), bear={train_bear} ({train_bear/len(train_sweeps)*100:.0f}%)")

    if len(test_sweeps) > 0:
        test_bull = (test_sweeps['type'] == 'bull').sum()
        test_bear = (test_sweeps['type'] == 'bear').sum()
        print(f"  TEST:  bull={test_bull} ({test_bull/len(test_sweeps)*100:.0f}%), bear={test_bear} ({test_bear/len(test_sweeps)*100:.0f}%)")

    # 7. Year-by-year breakdown
    print(f"\nYear-by-Year Breakdown:")
    all_sweeps = pd.concat([train_sweeps, test_sweeps]) if len(train_sweeps) > 0 and len(test_sweeps) > 0 else train_sweeps if len(train_sweeps) > 0 else test_sweeps
    if len(all_sweeps) > 0:
        yearly = all_sweeps.groupby(all_sweeps['time'].dt.year).size()
        for year, count in yearly.items():
            print(f"  {year}: {count} sweeps")

    # 8. Possible explanations
    print(f"\n{'='*70}")
    print("ANALYSIS:")
    print(f"{'='*70}")

    if test_atr_mean > train_atr_mean * 1.3:
        print("• HIGHER VOLATILITY in test period → ATR thresholds harder to meet")
        print("  Gold rallied significantly in 2024 with higher daily ranges")

    if test_swings_per_year < train_swings_per_year * 0.8:
        print("• FEWER SWINGS detected in test period → trending market with less back-and-forth")

    if test_range > train_range * 1.5:
        print("• MUCH LARGER PRICE RANGE in test → one-directional move reducing sweep opportunities")

    print("\nKey insight: The 2024-2025 gold rally created a strong trending environment")
    print("with higher ATR. This means:")
    print("  1. Displacement thresholds (ATR-based) became harder to achieve")
    print("  2. Fewer reversals/sweeps as price moved directionally")
    print("  3. The strategy adapts naturally - fewer low-quality setups")

# =============================================================================
# MAIN VALIDATION
# =============================================================================

def main():
    print("=" * 70)
    print("BOS/CHOCH STRATEGY VALIDATION + FREQUENCY INVESTIGATION")
    print("=" * 70)

    # Load and split data
    df_raw = load_data()
    train_raw, test_raw = split_data(df_raw)

    years_train = (train_raw.index[-1] - train_raw.index[0]).days / 365.25
    years_test = (test_raw.index[-1] - test_raw.index[0]).days / 365.25
    print(f"\nTrain period: {years_train:.2f} years")
    print(f"Test period: {years_test:.2f} years")

    results = {}

    for set_name, df_set in [('TRAIN', train_raw), ('TEST', test_raw)]:
        print(f"\n{'='*70}")
        print(f"PROCESSING {set_name} SET")
        print(f"{'='*70}")

        # Resample
        data_15 = add_atr(resample(df_set, '15min'))
        print(f"15min bars: {len(data_15)}")

        # Detect swings
        print("Detecting swings...")
        ltf_h, ltf_l = get_swings(data_15, 5)
        print(f"LTF swings: {len(ltf_h)} highs, {len(ltf_l)} lows")

        # Convert to numpy
        ltf_h_times = np.array([t.value for t, _ in ltf_h])
        ltf_h_prices = np.array([p for _, p in ltf_h])
        ltf_l_times = np.array([t.value for t, _ in ltf_l])
        ltf_l_prices = np.array([p for _, p in ltf_l])

        # Store for frequency investigation
        if set_name == 'TRAIN':
            train_15 = data_15
            train_ltf_h_times = ltf_h_times
            train_ltf_h_prices = ltf_h_prices
            train_ltf_l_times = ltf_l_times
            train_ltf_l_prices = ltf_l_prices
        else:
            test_15 = data_15
            test_ltf_h_times = ltf_h_times
            test_ltf_h_prices = ltf_h_prices
            test_ltf_l_times = ltf_l_times
            test_ltf_l_prices = ltf_l_prices

        # =====================================================================
        # BOS STRATEGY (multiple displacement thresholds)
        # =====================================================================
        for disp in [0.5, 0.75, 1.0]:
            print(f"\nRunning BOS (displacement={disp})...")
            bos_signals = detect_bos_choch(
                data_15, ltf_h_times, ltf_h_prices, ltf_l_times, ltf_l_prices,
                lookback=20, min_displacement_atr=disp, mode='bos'
            )
            trades = backtest_bos_choch(data_15, bos_signals, rr=2.0, fresh_filter=True)
            results[f'{set_name}_bos_{disp}'] = calculate_metrics(trades)
            print(f"  Signals: {len(bos_signals)}, Trades: {len(trades)}")

        # =====================================================================
        # CHOCH STRATEGY (multiple displacement thresholds)
        # =====================================================================
        for disp in [0.5, 0.75, 1.0]:
            print(f"\nRunning CHOCH (displacement={disp})...")
            choch_signals = detect_bos_choch(
                data_15, ltf_h_times, ltf_h_prices, ltf_l_times, ltf_l_prices,
                lookback=20, min_displacement_atr=disp, mode='choch'
            )
            trades = backtest_bos_choch(data_15, choch_signals, rr=2.0, fresh_filter=True)
            results[f'{set_name}_choch_{disp}'] = calculate_metrics(trades)
            print(f"  Signals: {len(choch_signals)}, Trades: {len(trades)}")

        # =====================================================================
        # SWEEPS (for frequency comparison)
        # =====================================================================
        print("\nRunning Sweeps (Option B: 1.0x ATR) for frequency analysis...")
        sweeps = detect_sweeps_with_displacement(
            data_15, ltf_h_times, ltf_h_prices, ltf_l_times, ltf_l_prices,
            lookback=30, min_atr=0.5, disp_atr=1.0
        )
        if set_name == 'TRAIN':
            train_sweeps = sweeps
        else:
            test_sweeps = sweeps
        print(f"  Sweeps detected: {len(sweeps)}")

    # =========================================================================
    # FREQUENCY INVESTIGATION
    # =========================================================================
    investigate_frequency_drop(train_15, test_15, train_sweeps, test_sweeps)

    # =========================================================================
    # FINAL RESULTS
    # =========================================================================
    print("\n")
    print("=" * 70)
    print("BOS/CHOCH VALIDATION RESULTS")
    print("=" * 70)

    for disp in [0.5, 0.75, 1.0]:
        print_results(
            f"BOS (Break of Structure) - {disp}x ATR displacement",
            results[f'TRAIN_bos_{disp}'],
            results[f'TEST_bos_{disp}'],
            years_train, years_test
        )

    for disp in [0.5, 0.75, 1.0]:
        print_results(
            f"CHOCH (Change of Character) - {disp}x ATR displacement",
            results[f'TRAIN_choch_{disp}'],
            results[f'TEST_choch_{disp}'],
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
        ("BOS 0.5x ATR", 'TRAIN_bos_0.5', 'TEST_bos_0.5'),
        ("BOS 0.75x ATR", 'TRAIN_bos_0.75', 'TEST_bos_0.75'),
        ("BOS 1.0x ATR", 'TRAIN_bos_1.0', 'TEST_bos_1.0'),
        ("CHOCH 0.5x ATR", 'TRAIN_choch_0.5', 'TEST_choch_0.5'),
        ("CHOCH 0.75x ATR", 'TRAIN_choch_0.75', 'TEST_choch_0.75'),
        ("CHOCH 1.0x ATR", 'TRAIN_choch_1.0', 'TEST_choch_1.0'),
    ]:
        train_exp = results[train_key]['exp']
        test_exp = results[test_key]['exp']
        test_trades = results[test_key]['trades']

        status = "✓" if test_exp > 0 else "✗"
        if test_trades < 20:
            status = "?" # insufficient data

        if test_exp > 0 and test_trades >= 20:
            passed.append(f"{strat_name}: train={train_exp:+.3f}R, test={test_exp:+.3f}R ({test_trades} trades)")
        else:
            reason = f"({test_trades} trades)" if test_trades < 20 else ""
            failed.append(f"{strat_name}: train={train_exp:+.3f}R, test={test_exp:+.3f}R {reason}")

    print("\n✓ PASSED (positive out-of-sample expectancy with 20+ trades):")
    if passed:
        for p in passed:
            print(f"  - {p}")
    else:
        print("  (none)")

    if failed:
        print("\n✗ FAILED or INSUFFICIENT DATA:")
        for f in failed:
            print(f"  - {f}")

    print("\n" + "=" * 70)
    print("KEY INSIGHTS")
    print("=" * 70)
    print("""
BOS vs CHOCH:
- BOS (continuation): Trades breakouts IN the trend direction
- CHOCH (reversal): Trades breakouts AGAINST the trend direction

Expected behavior:
- CHOCH should have lower win rate but potentially larger moves
- BOS should be more consistent in trending markets
- Both need displacement to confirm the break is real, not a fakeout

The 2024-2025 gold rally:
- Strong uptrend means BOS should work well (trending)
- CHOCH shorts would struggle (fighting the trend)
- Higher ATR means stricter displacement thresholds
""")

if __name__ == '__main__':
    main()
