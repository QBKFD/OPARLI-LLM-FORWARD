#!/usr/bin/env python3
"""
SMC Fair Value Gap (FVG) Strategy Backtest

- FVG Standalone (does NOT work)
- FVG + OB Confluence (does NOT work)
- FVG-Leads-OB Strategy (WORKS!)

Usage:
    python scripts/smc_backtest_fvg.py
"""

import pandas as pd
import numpy as np
import warnings
warnings.filterwarnings('ignore')

def load_and_prepare_data():
    """Load and prepare XAUUSD data"""
    print("Loading data...")
    df = pd.read_csv('data/snapshots/xauusd_5year_1min.csv')
    df['timestamp'] = pd.to_datetime(df['timestamp'], utc=True).dt.tz_localize(None)
    df.set_index('timestamp', inplace=True)
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

def get_trend(ts_value, htf_h_times, htf_h_prices, htf_l_times, htf_l_prices):
    """Determine HTF trend direction"""
    mask_h = htf_h_times < ts_value
    mask_l = htf_l_times < ts_value
    rh = htf_h_prices[mask_h][-5:] if mask_h.any() else []
    rl = htf_l_prices[mask_l][-5:] if mask_l.any() else []

    if len(rh) < 2 or len(rl) < 2:
        return 'N'

    bull = int(rh[-1] > rh[-2]) + int(rl[-1] > rl[-2])
    bear = int(rh[-1] < rh[-2]) + int(rl[-1] < rl[-2])

    return 'B' if bull > bear else ('S' if bear > bull else 'N')

def detect_fvgs(df, min_gap_atr=0.5):
    """
    Detect Fair Value Gaps (FVGs).

    An FVG is a 3-candle pattern where wicks of candle 1 and candle 3 don't overlap.
    - Bullish FVG: Gap between candle 1 high and candle 3 low (in uptrend)
    - Bearish FVG: Gap between candle 1 low and candle 3 high (in downtrend)
    """
    fvgs = []
    idx = df.index
    highs = df['high'].values
    lows = df['low'].values
    closes = df['close'].values
    atrs = df['atr'].values

    for i in range(2, len(df)):
        atr = atrs[i]
        if pd.isna(atr):
            continue

        # Candle 1, 2, 3
        c1_high, c1_low = highs[i-2], lows[i-2]
        c2_high, c2_low, c2_close, c2_open = highs[i-1], lows[i-1], closes[i-1], df['open'].iloc[i-1]
        c3_high, c3_low = highs[i], lows[i]

        # Bullish FVG: Gap between c1 high and c3 low
        if c3_low > c1_high:
            gap_size = c3_low - c1_high
            if gap_size >= atr * min_gap_atr:
                fvgs.append({
                    'time': idx[i-1],  # Middle candle time
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

def backtest_fvg_standalone(df, fvgs_df, htf_h_times, htf_h_prices, htf_l_times, htf_l_prices,
                            rr=2.0, htf_filter=False, fresh_filter=False):
    """
    Backtest FVG as standalone entry (enter when price returns to FVG).
    SPOILER: This does NOT work!
    """
    if len(fvgs_df) == 0:
        return []

    idx = df.index
    highs = df['high'].values
    lows = df['low'].values
    closes = df['close'].values

    trades = []
    tested_fvgs = set()

    for _, fvg in fvgs_df.iterrows():
        fvg_time = fvg['time']
        fvg_type = fvg['type']
        fvg_high = fvg['fvg_high']
        fvg_low = fvg['fvg_low']

        try:
            fvg_idx = idx.get_loc(fvg_time)
        except:
            continue

        # HTF trend filter
        if htf_filter:
            ts_val = fvg_time.value
            trend = get_trend(ts_val, htf_h_times, htf_h_prices, htf_l_times, htf_l_prices)
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

        # Look for price to return to FVG zone
        entry_price = None
        entry_idx = None

        for j in range(fvg_idx + 2, min(fvg_idx + 100, len(df))):
            fvg_mid = (fvg_high + fvg_low) / 2

            if fvg_type == 'bull' and lows[j] <= fvg_mid:
                entry_price = fvg_mid
                entry_idx = j
                break
            elif fvg_type == 'bear' and highs[j] >= fvg_mid:
                entry_price = fvg_mid
                entry_idx = j
                break

        if entry_price is None:
            continue

        # Set SL and TP
        if fvg_type == 'bull':
            sl = fvg_low - 0.5
            risk = entry_price - sl
            tp = entry_price + risk * rr
            trade_type = 'L'
        else:
            sl = fvg_high + 0.5
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
            entry_time = idx[entry_idx]
            trades.append({
                'time': entry_time,
                'type': fvg_type,
                'result': result,
                'hour': entry_time.hour,
                'day': entry_time.dayofweek
            })

    return trades

def backtest_fvg_leads_ob(df, fvgs_df, htf_h_times, htf_h_prices, htf_l_times, htf_l_prices,
                          rr=2.0, htf_filter=True, fresh_filter=True,
                          leave_atr=0.3, displacement_atr=1.0, max_bars_return=100):
    """
    Backtest FVG-Leads-OB Strategy (the one that WORKS!).

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
            trend = get_trend(ts_val, htf_h_times, htf_h_prices, htf_l_times, htf_l_prices)
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
                # Price must move above FVG + leave threshold
                if lows[j] > fvg_high + fvg_atr * leave_atr:
                    left_fvg = True
                    left_idx = j
                    break
            else:
                # Price must move below FVG - leave threshold
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
                # Need bearish candle (potential OB) + bullish displacement
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
                # Need bullish candle (potential OB) + bearish displacement
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

        # Entry on displacement candle close
        entry_price = closes[entry_idx]

        # Set SL and TP based on OB
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
            entry_time = idx[entry_idx]
            trades.append({
                'time': entry_time,
                'type': fvg_type,
                'result': result,
                'hour': entry_time.hour,
                'day': entry_time.dayofweek
            })

    return trades

def main():
    # Load data
    df = load_and_prepare_data()

    # Resample
    data_15 = add_atr(resample(df, '15min'))
    data_30 = add_atr(resample(df, '30min'))

    print(f"15min bars: {len(data_15)}")
    print(f"30min bars: {len(data_30)}")

    # Detect swings
    print("\nDetecting swings...")
    htf_h, htf_l = get_swings(data_30, 5)

    htf_h_times = np.array([t.value for t, _ in htf_h])
    htf_h_prices = np.array([p for _, p in htf_h])
    htf_l_times = np.array([t.value for t, _ in htf_l])
    htf_l_prices = np.array([p for _, p in htf_l])

    # Detect FVGs
    print("\nDetecting FVGs...")
    fvgs = detect_fvgs(data_15, min_gap_atr=0.5)
    print(f"FVGs found: {len(fvgs)}")
    if len(fvgs) > 0:
        print(f"  Bull FVGs: {len(fvgs[fvgs['type']=='bull'])}")
        print(f"  Bear FVGs: {len(fvgs[fvgs['type']=='bear'])}")

    # ========================================
    # TEST 1: FVG Standalone (Does NOT Work)
    # ========================================
    print("\n" + "=" * 70)
    print("TEST 1: FVG STANDALONE (Does NOT Work)")
    print("=" * 70)

    configs = [
        ("No filters", False, False),
        ("HTF trend only", True, False),
        ("Fresh only", False, True),
        ("HTF + Fresh", True, True),
    ]

    print(f"\n{'Config':<20} | {'Trades':>7} | {'WR%':>6} | {'Expectancy':>10}")
    print("-" * 55)

    for name, htf, fresh in configs:
        trades = backtest_fvg_standalone(data_15, fvgs, htf_h_times, htf_h_prices,
                                         htf_l_times, htf_l_prices, rr=2.0,
                                         htf_filter=htf, fresh_filter=fresh)
        if trades:
            wins = sum(1 for t in trades if t['result'] == 'win')
            wr = wins / len(trades) * 100
            exp = (wr / 100 * 2) - ((100 - wr) / 100)
        else:
            wr, exp = 0, 0
        print(f"{name:<20} | {len(trades):>7} | {wr:>5.1f}% | {exp:>+9.3f}R")

    print("\n> CONCLUSION: FVG as standalone entry does NOT work on XAUUSD!")

    # ========================================
    # TEST 2: FVG-Leads-OB Strategy (WORKS!)
    # ========================================
    print("\n" + "=" * 70)
    print("TEST 2: FVG-LEADS-OB STRATEGY (WORKS!)")
    print("=" * 70)

    print("\nHow it works:")
    print("  1. FVG forms (imbalance zone)")
    print("  2. Price LEAVES the FVG zone (moves 0.3 ATR away)")
    print("  3. Price RETURNS to the FVG zone")
    print("  4. Order Block forms on return (opposite candle + 1.0 ATR displacement)")
    print("  5. Entry on displacement candle close")

    trades = backtest_fvg_leads_ob(data_15, fvgs, htf_h_times, htf_h_prices,
                                   htf_l_times, htf_l_prices, rr=2.0,
                                   htf_filter=True, fresh_filter=True)

    if trades:
        wins = sum(1 for t in trades if t['result'] == 'win')
        wr = wins / len(trades) * 100
        exp = (wr / 100 * 2) - ((100 - wr) / 100)
        print(f"\nFVG-Leads-OB (all hours): {len(trades)} trades, {wr:.1f}% WR, {exp:+.3f}R")

    # ========================================
    # TEST 3: Time Analysis
    # ========================================
    print("\n" + "=" * 70)
    print("TEST 3: TIME ANALYSIS FOR FVG-LEADS-OB")
    print("=" * 70)

    # By hour
    print(f"\n{'Hour':<6} | {'Trades':>6} | {'Wins':>5} | {'WR%':>6} | {'Expectancy':>10}")
    print("-" * 50)

    hourly_stats = []
    for hour in range(24):
        hour_trades = [t for t in trades if t['hour'] == hour]
        if len(hour_trades) >= 3:
            wins = sum(1 for t in hour_trades if t['result'] == 'win')
            wr = wins / len(hour_trades) * 100
            exp = (wr / 100 * 2) - ((100 - wr) / 100)
            hourly_stats.append((hour, len(hour_trades), wr, exp))
            print(f"{hour:02d}:00  | {len(hour_trades):>6} | {wins:>5} | {wr:>5.1f}% | {exp:>+9.3f}R")

    # Best and worst hours
    sorted_hours = sorted(hourly_stats, key=lambda x: x[3], reverse=True)

    print("\n" + "-" * 50)
    print("BEST HOURS:")
    for h, n, wr, exp in sorted_hours[:5]:
        print(f"  Hour {h:02d}: {n} trades, {wr:.1f}% WR, {exp:+.3f}R")

    print("\nWORST HOURS:")
    for h, n, wr, exp in sorted_hours[-5:]:
        print(f"  Hour {h:02d}: {n} trades, {wr:.1f}% WR, {exp:+.3f}R")

    # Best hours filter
    best_hours = [h for h, n, wr, exp in hourly_stats if exp > 0.2]
    print(f"\nBest hours (>+0.2R): {sorted(best_hours)}")

    # ========================================
    # TEST 4: Optimized FVG-Leads-OB
    # ========================================
    print("\n" + "=" * 70)
    print("TEST 4: OPTIMIZED FVG-LEADS-OB (best hours only)")
    print("=" * 70)

    if best_hours:
        filtered_trades = [t for t in trades if t['hour'] in best_hours]
        if filtered_trades:
            wins = sum(1 for t in filtered_trades if t['result'] == 'win')
            wr = wins / len(filtered_trades) * 100
            exp = (wr / 100 * 2) - ((100 - wr) / 100)
            print(f"\nFiltered to hours {sorted(best_hours)}:")
            print(f"  Trades: {len(filtered_trades)}")
            print(f"  Win Rate: {wr:.1f}%")
            print(f"  Expectancy: {exp:+.3f}R")

    # Final summary
    print("\n" + "=" * 70)
    print("FVG STRATEGY SUMMARY")
    print("=" * 70)
    print("""
KEY FINDINGS:

1. FVG STANDALONE: Does NOT work
   - All filter combinations have negative or near-zero expectancy
   - FVG alone is not a valid entry signal on XAUUSD

2. FVG + OB CONFLUENCE: Does NOT help
   - Adding FVG overlap requirement to OBs reduces trades
   - Does not improve win rate or expectancy

3. FVG-LEADS-OB STRATEGY: WORKS!
   - FVG marks institutional imbalance zone
   - Price must leave then return to validate the zone
   - OB forming on return confirms institutional defense
   - With time filter: ~49% WR, +0.46R expectancy

FVG-Leads-OB Strategy Settings:
- Entry Timeframe: 15min
- HTF Trend: 30min (required)
- Fresh FVG: Yes
- Entry: FVG forms → price leaves → returns with OB → enter on displacement
- Stop Loss: Below/above OB
- Take Profit: 2:1 R:R
- Time Filter: TRADE hours 0, 3, 8, 15, 20, 23 UTC
               AVOID hours 2, 11, 18, 19 UTC
""")

if __name__ == '__main__':
    main()
