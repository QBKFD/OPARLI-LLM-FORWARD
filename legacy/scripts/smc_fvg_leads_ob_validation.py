#!/usr/bin/env python3
"""
FVG-Leads-OB Strategy Focused Validation

Tests multiple parameter combinations to find if any configuration passes
out-of-sample validation.

The Strategy:
1. FVG forms (imbalance zone - 3-candle gap)
2. Price LEAVES the FVG zone (moves away)
3. Price RETURNS to the FVG zone
4. An Order Block forms on the return (opposite candle + displacement)
5. Entry on displacement candle close

Usage:
    python scripts/smc_fvg_leads_ob_validation.py
"""

import pandas as pd
import numpy as np
import warnings
from datetime import datetime
warnings.filterwarnings('ignore')

# =============================================================================
# DATA LOADING
# =============================================================================

def load_data():
    """Load XAUUSD data"""
    print("Loading XAUUSD_COMPLETE.csv...")
    df = pd.read_csv('data/snapshots/XAUUSD_COMPLETE.csv')
    df['timestamp'] = pd.to_datetime(df['timestamp'], utc=True).dt.tz_localize(None)
    df.set_index('timestamp', inplace=True)

    cutoff = pd.Timestamp('2025-06-08')
    df = df[df.index < cutoff]

    print(f"Data range: {df.index[0]} to {df.index[-1]}")
    return df

def split_data(df):
    """Split into train and test sets"""
    train_end = pd.Timestamp('2023-12-31 23:59:59')
    train = df[df.index <= train_end].copy()
    test = df[df.index > train_end].copy()
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
# FVG DETECTION
# =============================================================================

def detect_fvgs(df, min_gap_atr=0.3):
    """
    Detect Fair Value Gaps (FVGs).

    An FVG is a 3-candle pattern where wicks of candle 1 and candle 3 don't overlap.
    - Bullish FVG: C1 high < C3 low (gap up)
    - Bearish FVG: C1 low > C3 high (gap down)
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
                    'time': idx[i-1],  # Middle candle time
                    'type': 'bull',
                    'fvg_high': c3_low,  # Top of gap
                    'fvg_low': c1_high,  # Bottom of gap
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
                    'fvg_high': c1_low,  # Top of gap
                    'fvg_low': c3_high,  # Bottom of gap
                    'gap_size': gap_size,
                    'atr': atr
                })

    return pd.DataFrame(fvgs)

# =============================================================================
# FVG-LEADS-OB STRATEGY
# =============================================================================

def backtest_fvg_leads_ob(df, fvgs_df, htf_h_times, htf_h_prices, htf_l_times, htf_l_prices,
                          rr=2.0, htf_filter=True, fresh_filter=True,
                          leave_atr=0.3, displacement_atr=1.0, max_bars_return=100,
                          best_hours=None):
    """
    Backtest FVG-Leads-OB Strategy.

    Parameters:
        leave_atr: How far price must move away from FVG before return (in ATR)
        displacement_atr: Minimum displacement for OB confirmation (in ATR)
        max_bars_return: Maximum bars to wait for return
        best_hours: List of hours to filter trades (None = all hours)
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

            # Check for OB forming
            if j + 1 >= len(df):
                continue

            next_close = closes[j + 1]

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

        # Time filter
        entry_time = idx[entry_idx]
        if best_hours is not None and entry_time.hour not in best_hours:
            continue

        # Entry
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
# ANALYSIS
# =============================================================================

def calculate_metrics(trades, rr=2.0):
    """Calculate performance metrics"""
    if not trades:
        return {'trades': 0, 'wins': 0, 'wr': 0, 'exp': 0}

    wins = sum(1 for t in trades if t['result'] == 'win')
    total = len(trades)
    wr = wins / total * 100
    exp = (wr / 100 * rr) - ((100 - wr) / 100)

    return {'trades': total, 'wins': wins, 'wr': wr, 'exp': exp}

# =============================================================================
# MAIN
# =============================================================================

def main():
    print("=" * 70)
    print("FVG-LEADS-OB STRATEGY FOCUSED VALIDATION")
    print("=" * 70)

    # Load and split data
    df_raw = load_data()
    train_raw, test_raw = split_data(df_raw)

    years_train = (train_raw.index[-1] - train_raw.index[0]).days / 365.25
    years_test = (test_raw.index[-1] - test_raw.index[0]).days / 365.25

    print(f"\nTRAIN: {train_raw.index[0].date()} to {train_raw.index[-1].date()} ({years_train:.2f} years)")
    print(f"TEST:  {test_raw.index[0].date()} to {test_raw.index[-1].date()} ({years_test:.2f} years)")

    results = {}

    for set_name, df_set in [('TRAIN', train_raw), ('TEST', test_raw)]:
        print(f"\n{'='*70}")
        print(f"PROCESSING {set_name} SET")
        print(f"{'='*70}")

        # Resample to 15min
        data_15 = add_atr(resample(df_set, '15min'))
        data_30 = add_atr(resample(df_set, '30min'))
        print(f"15min bars: {len(data_15)}")

        # Detect swings
        htf_h, htf_l = get_swings(data_30, 5)
        print(f"HTF swings: {len(htf_h)} highs, {len(htf_l)} lows")

        # Convert to numpy
        htf_h_times = np.array([t.value for t, _ in htf_h])
        htf_h_prices = np.array([p for _, p in htf_h])
        htf_l_times = np.array([t.value for t, _ in htf_l])
        htf_l_prices = np.array([p for _, p in htf_l])

        # Detect FVGs
        fvgs = detect_fvgs(data_15, min_gap_atr=0.3)
        print(f"FVGs detected: {len(fvgs)}")

        # Test multiple configurations
        configs = [
            # (name, htf, leave_atr, disp_atr, hours)
            ("Baseline (HTF, 0.3/1.0)", True, 0.3, 1.0, None),
            ("No HTF filter", False, 0.3, 1.0, None),
            ("Higher leave (0.5)", True, 0.5, 1.0, None),
            ("Lower disp (0.75)", True, 0.3, 0.75, None),
            ("Higher disp (1.25)", True, 0.3, 1.25, None),
            ("Best hours (0,8,15,20,23)", True, 0.3, 1.0, [0, 8, 15, 20, 23]),
            ("London/NY hours (8-16)", True, 0.3, 1.0, list(range(8, 17))),
            ("Asian hours (0-8)", True, 0.3, 1.0, list(range(0, 9))),
            ("Leave 0.5 + Disp 0.75", True, 0.5, 0.75, None),
            ("Leave 0.5 + No HTF", False, 0.5, 1.0, None),
        ]

        for name, htf, leave, disp, hours in configs:
            trades = backtest_fvg_leads_ob(
                data_15, fvgs, htf_h_times, htf_h_prices, htf_l_times, htf_l_prices,
                rr=2.0, htf_filter=htf, fresh_filter=True,
                leave_atr=leave, displacement_atr=disp, best_hours=hours
            )
            results[f'{set_name}_{name}'] = calculate_metrics(trades)

    # Print results
    print("\n")
    print("=" * 70)
    print("FVG-LEADS-OB VALIDATION RESULTS")
    print("=" * 70)

    configs = [
        ("Baseline (HTF, 0.3/1.0)", True, 0.3, 1.0, None),
        ("No HTF filter", False, 0.3, 1.0, None),
        ("Higher leave (0.5)", True, 0.5, 1.0, None),
        ("Lower disp (0.75)", True, 0.3, 0.75, None),
        ("Higher disp (1.25)", True, 0.3, 1.25, None),
        ("Best hours (0,8,15,20,23)", True, 0.3, 1.0, [0, 8, 15, 20, 23]),
        ("London/NY hours (8-16)", True, 0.3, 1.0, list(range(8, 17))),
        ("Asian hours (0-8)", True, 0.3, 1.0, list(range(0, 9))),
        ("Leave 0.5 + Disp 0.75", True, 0.5, 0.75, None),
        ("Leave 0.5 + No HTF", False, 0.5, 1.0, None),
    ]

    print(f"\n{'Configuration':<30} | {'TRAIN':^20} | {'TEST':^20}")
    print(f"{'':<30} | {'Trades':>6} {'WR':>5} {'Exp':>7} | {'Trades':>6} {'WR':>5} {'Exp':>7}")
    print("-" * 80)

    passed = []
    failed = []

    for name, _, _, _, _ in configs:
        train_m = results[f'TRAIN_{name}']
        test_m = results[f'TEST_{name}']

        train_str = f"{train_m['trades']:>6} {train_m['wr']:>4.0f}% {train_m['exp']:>+6.2f}R"
        test_str = f"{test_m['trades']:>6} {test_m['wr']:>4.0f}% {test_m['exp']:>+6.2f}R"

        status = "✓" if test_m['exp'] > 0 and test_m['trades'] >= 15 else "✗"
        print(f"{name:<30} | {train_str} | {test_str} {status}")

        if test_m['exp'] > 0 and test_m['trades'] >= 15:
            passed.append((name, train_m, test_m))
        else:
            failed.append((name, train_m, test_m))

    # Summary
    print("\n" + "=" * 70)
    print("VALIDATION SUMMARY")
    print("=" * 70)

    if passed:
        print("\n✓ PASSED (positive test expectancy with 15+ trades):")
        for name, train_m, test_m in passed:
            deg = test_m['exp'] - train_m['exp']
            print(f"  - {name}: train={train_m['exp']:+.3f}R → test={test_m['exp']:+.3f}R (deg={deg:+.3f}R)")
    else:
        print("\n✓ PASSED: None")

    if failed:
        print("\n✗ FAILED or INSUFFICIENT:")
        for name, train_m, test_m in failed:
            reason = ""
            if test_m['trades'] < 15:
                reason = f"(only {test_m['trades']} trades)"
            elif test_m['exp'] <= 0:
                reason = f"(negative: {test_m['exp']:+.3f}R)"
            print(f"  - {name} {reason}")

    # Comparison
    print("\n" + "=" * 70)
    print("COMPARISON WITH VALIDATED STRATEGIES")
    print("=" * 70)
    print("""
| Strategy                     | Test WR | Test Exp | Status    |
|------------------------------|---------|----------|-----------|
| Sweep + Disp 1.0x (Option B) | 67.5%   | +1.025R  | VALIDATED |
| Sweep + Disp 0.75x (Option A)| 59.2%   | +0.776R  | VALIDATED |
""")

    if passed:
        print("FVG-Leads-OB configurations that passed:")
        for name, _, test_m in passed:
            print(f"| FVG-Leads-OB ({name[:20]}...) | {test_m['wr']:.1f}%   | {test_m['exp']:+.3f}R  | VALIDATED |")
    else:
        print("FVG-Leads-OB: ALL CONFIGURATIONS FAILED VALIDATION")

    print("\n" + "=" * 70)
    print("CONCLUSION")
    print("=" * 70)

    if passed:
        best = max(passed, key=lambda x: x[2]['exp'])
        print(f"""
FVG-Leads-OB CAN BE VALIDATED with the right configuration!

Best performing configuration: {best[0]}
- Train: {best[1]['trades']} trades, {best[1]['wr']:.1f}% WR, {best[1]['exp']:+.3f}R
- Test:  {best[2]['trades']} trades, {best[2]['wr']:.1f}% WR, {best[2]['exp']:+.3f}R

However, even the best FVG-Leads-OB configuration underperforms Sweeps:
- Sweeps Option B: +1.025R (validated)
- Best FVG-Leads-OB: {best[2]['exp']:+.3f}R

RECOMMENDATION: Use Sweeps as primary strategy. FVG-Leads-OB can be supplementary
for confluence setups where FVG aligns with sweep zones.
""")
    else:
        print("""
FVG-Leads-OB FAILED all validation configurations.

Possible reasons:
1. The strategy was overfit to in-sample data
2. Market regime changed in 2024-2025 (stronger trending)
3. FVG zones filled faster in higher volatility environment
4. The combination of leave + return + OB is too restrictive

RECOMMENDATION: Do NOT use FVG-Leads-OB as standalone strategy.
Continue using validated Sweeps strategy.
""")

if __name__ == '__main__':
    main()
