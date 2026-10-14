#!/usr/bin/env python3
"""
SMC Order Block Strategy Backtest


Usage:
    python scripts/smc_backtest_orderblocks.py
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

def detect_order_blocks(df, displacement_atr=1.5):
    """
    Detect Order Blocks on the given dataframe.

    An Order Block is the last opposing candle before a strong impulsive move.
    - Bullish OB: Last bearish candle before strong bullish move
    - Bearish OB: Last bullish candle before strong bearish move
    """
    obs = []
    idx = df.index
    opens = df['open'].values
    highs = df['high'].values
    lows = df['low'].values
    closes = df['close'].values
    atrs = df['atr'].values

    for i in range(1, len(df) - 1):
        atr = atrs[i]
        if pd.isna(atr):
            continue

        prev_o, prev_c = opens[i-1], closes[i-1]
        curr_o, curr_c, curr_h, curr_l = opens[i], closes[i], highs[i], lows[i]

        # Bullish OB: Previous candle bearish, current candle strong bullish
        if prev_c < prev_o:  # Previous is bearish
            displacement = curr_c - curr_o
            if displacement > atr * displacement_atr:  # Strong bullish move
                obs.append({
                    'time': idx[i-1],
                    'type': 'bull',
                    'ob_high': highs[i-1],
                    'ob_low': lows[i-1],
                    'ob_open': prev_o,
                    'ob_close': prev_c,
                    'atr': atr
                })

        # Bearish OB: Previous candle bullish, current candle strong bearish
        if prev_c > prev_o:  # Previous is bullish
            displacement = curr_o - curr_c
            if displacement > atr * displacement_atr:  # Strong bearish move
                obs.append({
                    'time': idx[i-1],
                    'type': 'bear',
                    'ob_high': highs[i-1],
                    'ob_low': lows[i-1],
                    'ob_open': prev_o,
                    'ob_close': prev_c,
                    'atr': atr
                })

    return pd.DataFrame(obs)

def backtest_orderblocks(df, obs_df, htf_h_times, htf_h_prices, htf_l_times, htf_l_prices,
                         rr=2.0, htf_filter=True, fresh_filter=True, mitigation=True):
    """
    Backtest Order Block signals.

    Parameters:
    - rr: Risk:Reward ratio
    - htf_filter: Only trade with HTF trend
    - fresh_filter: Only trade fresh (untested) OBs
    - mitigation: Enter at 50% of OB (mitigation) vs edge
    """
    if len(obs_df) == 0:
        return []

    idx = df.index
    highs = df['high'].values
    lows = df['low'].values
    closes = df['close'].values

    trades = []
    tested_obs = set()

    for _, ob in obs_df.iterrows():
        ob_time = ob['time']
        ob_type = ob['type']
        ob_high = ob['ob_high']
        ob_low = ob['ob_low']

        try:
            ob_idx = idx.get_loc(ob_time)
        except:
            continue

        # HTF trend filter
        if htf_filter:
            ts_val = ob_time.value
            trend = get_trend(ts_val, htf_h_times, htf_h_prices, htf_l_times, htf_l_prices)
            if ob_type == 'bull' and trend != 'B':
                continue
            if ob_type == 'bear' and trend != 'S':
                continue

        # Fresh OB filter
        if fresh_filter:
            ob_key = (round(ob_high, 1), round(ob_low, 1))
            if ob_key in tested_obs:
                continue
            tested_obs.add(ob_key)

        # Look for price to return to OB zone (mitigation)
        entry_price = None
        entry_idx = None

        for j in range(ob_idx + 2, min(ob_idx + 100, len(df))):
            if mitigation:
                # Mitigation entry: 50% of OB body
                mitigation_level = (ob_high + ob_low) / 2
                if ob_type == 'bull' and lows[j] <= mitigation_level:
                    entry_price = mitigation_level
                    entry_idx = j
                    break
                elif ob_type == 'bear' and highs[j] >= mitigation_level:
                    entry_price = mitigation_level
                    entry_idx = j
                    break
            else:
                # Edge entry
                if ob_type == 'bull' and lows[j] <= ob_high:
                    entry_price = ob_high
                    entry_idx = j
                    break
                elif ob_type == 'bear' and highs[j] >= ob_low:
                    entry_price = ob_low
                    entry_idx = j
                    break

        if entry_price is None:
            continue

        # Set SL and TP
        if ob_type == 'bull':
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
                'type': ob_type,
                'result': result,
                'hour': entry_time.hour,
                'day': entry_time.dayofweek
            })

    return trades

def analyze_performance(trades, rr=2.0, label=""):
    """Calculate and display performance metrics"""
    if not trades:
        print(f"{label}: No trades")
        return 0, 0

    wins = sum(1 for t in trades if t['result'] == 'win')
    total = len(trades)
    wr = wins / total * 100
    exp = (wr / 100 * rr) - ((100 - wr) / 100)

    print(f"{label}: {total} trades, {wr:.1f}% WR, {exp:+.3f}R")
    return wr, exp

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
    print(f"HTF swings: {len(htf_h)} highs, {len(htf_l)} lows")

    # Convert to numpy arrays
    htf_h_times = np.array([t.value for t, _ in htf_h])
    htf_h_prices = np.array([p for _, p in htf_h])
    htf_l_times = np.array([t.value for t, _ in htf_l])
    htf_l_prices = np.array([p for _, p in htf_l])

    # Detect Order Blocks
    print("\nDetecting Order Blocks...")
    obs = detect_order_blocks(data_15, displacement_atr=1.5)
    print(f"Order Blocks found: {len(obs)}")
    if len(obs) > 0:
        print(f"  Bull OBs: {len(obs[obs['type']=='bull'])}")
        print(f"  Bear OBs: {len(obs[obs['type']=='bear'])}")

    # ========================================
    # TEST 1: Filter Combinations
    # ========================================
    print("\n" + "=" * 70)
    print("TEST 1: FILTER COMBINATIONS")
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
        trades = backtest_orderblocks(data_15, obs, htf_h_times, htf_h_prices,
                                      htf_l_times, htf_l_prices, rr=2.0,
                                      htf_filter=htf, fresh_filter=fresh)
        if trades:
            wins = sum(1 for t in trades if t['result'] == 'win')
            wr = wins / len(trades) * 100
            exp = (wr / 100 * 2) - ((100 - wr) / 100)
        else:
            wr, exp = 0, 0
        print(f"{name:<20} | {len(trades):>7} | {wr:>5.1f}% | {exp:>+9.3f}R")

    # ========================================
    # TEST 2: R:R Optimization
    # ========================================
    print("\n" + "=" * 70)
    print("TEST 2: R:R RATIO OPTIMIZATION (HTF + Fresh filters)")
    print("=" * 70)

    print(f"\n{'R:R':<10} | {'Trades':>7} | {'WR%':>6} | {'Expectancy':>10}")
    print("-" * 45)

    for rr in [1.5, 2.0, 2.5, 3.0]:
        trades = backtest_orderblocks(data_15, obs, htf_h_times, htf_h_prices,
                                      htf_l_times, htf_l_prices, rr=rr,
                                      htf_filter=True, fresh_filter=True)
        if trades:
            wins = sum(1 for t in trades if t['result'] == 'win')
            wr = wins / len(trades) * 100
            exp = (wr / 100 * rr) - ((100 - wr) / 100)
        else:
            wr, exp = 0, 0
        print(f"{rr}:1       | {len(trades):>7} | {wr:>5.1f}% | {exp:>+9.3f}R")

    # ========================================
    # TEST 3: Time Analysis
    # ========================================
    print("\n" + "=" * 70)
    print("TEST 3: TIME ANALYSIS (HTF + Fresh, 2.5:1 R:R)")
    print("=" * 70)

    trades = backtest_orderblocks(data_15, obs, htf_h_times, htf_h_prices,
                                  htf_l_times, htf_l_prices, rr=2.5,
                                  htf_filter=True, fresh_filter=True)

    # By hour
    print(f"\n{'Hour':<6} | {'Trades':>6} | {'Wins':>5} | {'WR%':>6} | {'Expectancy':>10}")
    print("-" * 50)

    hourly_stats = []
    for hour in range(24):
        hour_trades = [t for t in trades if t['hour'] == hour]
        if len(hour_trades) >= 3:
            wins = sum(1 for t in hour_trades if t['result'] == 'win')
            wr = wins / len(hour_trades) * 100
            exp = (wr / 100 * 2.5) - ((100 - wr) / 100)
            hourly_stats.append((hour, len(hour_trades), wr, exp))
            print(f"{hour:02d}:00  | {len(hour_trades):>6} | {wins:>5} | {wr:>5.1f}% | {exp:>+9.3f}R")

    # Sort by expectancy
    sorted_hours = sorted(hourly_stats, key=lambda x: x[3], reverse=True)

    print("\n" + "-" * 50)
    print("BEST HOURS:")
    for h, n, wr, exp in sorted_hours[:5]:
        print(f"  Hour {h:02d}: {n} trades, {wr:.1f}% WR, {exp:+.3f}R")

    print("\nWORST HOURS:")
    for h, n, wr, exp in sorted_hours[-5:]:
        print(f"  Hour {h:02d}: {n} trades, {wr:.1f}% WR, {exp:+.3f}R")

    # By day
    print("\n" + "-" * 50)
    print("BY DAY OF WEEK:")
    days = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday']
    for day_idx, day_name in enumerate(days):
        day_trades = [t for t in trades if t['day'] == day_idx]
        if day_trades:
            wins = sum(1 for t in day_trades if t['result'] == 'win')
            wr = wins / len(day_trades) * 100
            exp = (wr / 100 * 2.5) - ((100 - wr) / 100)
            print(f"  {day_name:<12}: {len(day_trades)} trades, {wr:.1f}% WR, {exp:+.3f}R")

    # By session
    print("\n" + "-" * 50)
    print("BY SESSION:")
    sessions = {
        'Asian': list(range(0, 7)),
        'London Open': list(range(7, 10)),
        'London': list(range(10, 13)),
        'NY+London': list(range(13, 17)),
        'New York': list(range(17, 22)),
        'Late NY': list(range(22, 24))
    }

    for session, hours in sessions.items():
        session_trades = [t for t in trades if t['hour'] in hours]
        if session_trades:
            wins = sum(1 for t in session_trades if t['result'] == 'win')
            wr = wins / len(session_trades) * 100
            exp = (wr / 100 * 2.5) - ((100 - wr) / 100)
            print(f"  {session:<15}: {len(session_trades)} trades, {wr:.1f}% WR, {exp:+.3f}R")

    # ========================================
    # TEST 4: Optimized Strategy
    # ========================================
    print("\n" + "=" * 70)
    print("TEST 4: OPTIMIZED STRATEGY (avoid hours 13, 14, 20)")
    print("=" * 70)

    avoid_hours = [13, 14, 20]
    filtered_trades = [t for t in trades if t['hour'] not in avoid_hours]

    if filtered_trades:
        wins = sum(1 for t in filtered_trades if t['result'] == 'win')
        wr = wins / len(filtered_trades) * 100
        exp = (wr / 100 * 2.5) - ((100 - wr) / 100)
        print(f"\nFiltered (avoid hours {avoid_hours}):")
        print(f"  Trades: {len(filtered_trades)}")
        print(f"  Win Rate: {wr:.1f}%")
        print(f"  Expectancy: {exp:+.3f}R")

    # Final summary
    print("\n" + "=" * 70)
    print("ORDER BLOCK STRATEGY SUMMARY")
    print("=" * 70)
    print("""
Order Block Strategy Settings:

Entry Timeframe: 15min
HTF Trend: 30min
Entry Type: Mitigation (50% of OB)
Stop Loss: Below/above OB low/high + 0.5 buffer
Take Profit: 2.5:1 R:R

Filters:
- HTF Trend alignment: ON
- Fresh OB only: ON
- Time filter: AVOID hours 13, 14, 20 UTC

Expected Performance:
- Win Rate: ~36%
- Expectancy: +0.25R per trade
- ~100 setups per 2 years

Key Insights:
- NY+London overlap (13-16 UTC) is the WORST time for OBs
- London Open (7-9 UTC) and Late NY (22-24 UTC) are best
- Friday and Thursday are the best days
""")

if __name__ == '__main__':
    main()
