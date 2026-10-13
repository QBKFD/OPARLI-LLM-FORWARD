#!/usr/bin/env python3
"""
Visualize Sweep Strategy Trades

Creates charts showing:
- Candlestick price data
- Swing highs/lows that were swept
- Entry, Stop Loss, Take Profit levels
- Trade outcomes (win/loss coloring)

Usage:
    python scripts/visualize_sweep_trades.py

Output:
    Saves PNG files to: output/sweep_trades/
"""

import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.patches import Rectangle
import os
import warnings
warnings.filterwarnings('ignore')

# Create output directory
OUTPUT_DIR = 'output/sweep_trades'
os.makedirs(OUTPUT_DIR, exist_ok=True)

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

# =============================================================================
# SWEEP DETECTION WITH FULL TRADE INFO
# =============================================================================

def detect_sweeps_with_trades(df, ltf_h_times, ltf_h_prices, ltf_l_times, ltf_l_prices,
                               lookback=30, min_atr=0.5, disp_atr=1.0, rr=2.0):
    """Detect sweeps and simulate trades, returning full trade info for visualization"""
    trades = []
    idx = df.index
    highs = df['high'].values
    lows = df['low'].values
    closes = df['close'].values
    opens = df['open'].values
    atrs = df['atr'].values

    tested_levels = set()

    for i in range(lookback, len(df) - 1):
        ts = idx[i]
        ts_val = ts.value
        h, l, c, o, atr = highs[i], lows[i], closes[i], opens[i], atrs[i]

        if pd.isna(atr):
            continue

        next_h, next_l, next_c = highs[i+1], lows[i+1], closes[i+1]

        lookback_ts = idx[i - lookback].value
        mask_h = (ltf_h_times > lookback_ts) & (ltf_h_times < ts_val)
        mask_l = (ltf_l_times > lookback_ts) & (ltf_l_times < ts_val)

        # BEARISH SWEEP
        if mask_h.any():
            rec_h = ltf_h_prices[mask_h]
            rec_h_times = ltf_h_times[mask_h]
            for idx_lv, lv in enumerate(rec_h):
                if h > lv and c < lv and (h - lv) >= atr * min_atr and c < o:
                    if next_c < c and (c - next_c) >= atr * disp_atr:
                        # Fresh level filter
                        lk = round(lv, 1)
                        if lk in tested_levels:
                            continue
                        tested_levels.add(lk)

                        # Trade setup
                        entry_price = c
                        sl = h + 0.5
                        risk = sl - entry_price
                        tp = entry_price - risk * rr

                        # Find swing high time
                        swing_time = pd.Timestamp(rec_h_times[idx_lv])

                        # Simulate trade
                        result = 'timeout'
                        exit_idx = None
                        exit_price = None
                        for k in range(i + 1, min(i + 101, len(df))):
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

                        if result != 'timeout':
                            trades.append({
                                'entry_time': ts,
                                'entry_idx': i,
                                'entry_price': entry_price,
                                'type': 'short',
                                'sweep_type': 'bear',
                                'level': lv,
                                'level_time': swing_time,
                                'sl': sl,
                                'tp': tp,
                                'risk': risk,
                                'result': result,
                                'exit_idx': exit_idx,
                                'exit_time': idx[exit_idx] if exit_idx else None,
                                'exit_price': exit_price,
                                'atr': atr
                            })
                        break

        # BULLISH SWEEP
        if mask_l.any():
            rec_l = ltf_l_prices[mask_l]
            rec_l_times = ltf_l_times[mask_l]
            for idx_lv, lv in enumerate(rec_l):
                if l < lv and c > lv and (lv - l) >= atr * min_atr and c > o:
                    if next_c > c and (next_c - c) >= atr * disp_atr:
                        # Fresh level filter
                        lk = round(lv, 1)
                        if lk in tested_levels:
                            continue
                        tested_levels.add(lk)

                        # Trade setup
                        entry_price = c
                        sl = l - 0.5
                        risk = entry_price - sl
                        tp = entry_price + risk * rr

                        # Find swing low time
                        swing_time = pd.Timestamp(rec_l_times[idx_lv])

                        # Simulate trade
                        result = 'timeout'
                        exit_idx = None
                        exit_price = None
                        for k in range(i + 1, min(i + 101, len(df))):
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

                        if result != 'timeout':
                            trades.append({
                                'entry_time': ts,
                                'entry_idx': i,
                                'entry_price': entry_price,
                                'type': 'long',
                                'sweep_type': 'bull',
                                'level': lv,
                                'level_time': swing_time,
                                'sl': sl,
                                'tp': tp,
                                'risk': risk,
                                'result': result,
                                'exit_idx': exit_idx,
                                'exit_time': idx[exit_idx] if exit_idx else None,
                                'exit_price': exit_price,
                                'atr': atr
                            })
                        break

    return trades

# =============================================================================
# VISUALIZATION
# =============================================================================

def plot_candlesticks(ax, df, start_idx, end_idx):
    """Plot candlesticks on axis"""
    subset = df.iloc[start_idx:end_idx]

    for i, (ts, row) in enumerate(subset.iterrows()):
        o, h, l, c = row['open'], row['high'], row['low'], row['close']
        color = 'green' if c >= o else 'red'

        # Wick
        ax.plot([i, i], [l, h], color='black', linewidth=0.5)

        # Body
        body_bottom = min(o, c)
        body_height = abs(c - o)
        rect = Rectangle((i - 0.3, body_bottom), 0.6, body_height,
                          facecolor=color, edgecolor='black', linewidth=0.5)
        ax.add_patch(rect)

    return subset

def visualize_trade(df, trade, trade_num, context_bars=50):
    """Create visualization for a single trade"""
    entry_idx = trade['entry_idx']
    exit_idx = trade['exit_idx']

    # Calculate range to show
    start_idx = max(0, entry_idx - context_bars)
    end_idx = min(len(df), exit_idx + context_bars // 2)

    fig, ax = plt.subplots(figsize=(16, 8))

    # Plot candlesticks
    subset = plot_candlesticks(ax, df, start_idx, end_idx)
    x_indices = list(range(len(subset)))

    # Calculate relative positions
    entry_x = entry_idx - start_idx
    exit_x = exit_idx - start_idx
    level_time = trade['level_time']

    # Find level time index
    try:
        level_idx = df.index.get_loc(level_time)
        level_x = level_idx - start_idx
    except:
        level_x = entry_x - 20  # fallback

    # Draw the swept level (horizontal line)
    level_price = trade['level']
    ax.axhline(y=level_price, color='purple', linestyle='--', linewidth=1.5,
               label=f'Swept Level: ${level_price:.2f}')

    # Mark the sweep point (where price exceeded the level)
    sweep_high = trade['entry_price'] + trade['risk'] if trade['type'] == 'short' else trade['sl']
    sweep_low = trade['sl'] if trade['type'] == 'short' else trade['entry_price'] - trade['risk']

    # Entry point
    entry_color = 'blue'
    ax.scatter([entry_x], [trade['entry_price']], color=entry_color, s=150, zorder=5,
               marker='v' if trade['type'] == 'short' else '^',
               label=f"Entry: ${trade['entry_price']:.2f}")

    # SL and TP lines
    sl_color = 'red'
    tp_color = 'green'

    # Draw SL line from entry to exit
    ax.hlines(y=trade['sl'], xmin=entry_x, xmax=exit_x, colors=sl_color,
              linestyles='dashed', linewidth=2, label=f"SL: ${trade['sl']:.2f}")
    ax.hlines(y=trade['tp'], xmin=entry_x, xmax=exit_x, colors=tp_color,
              linestyles='dashed', linewidth=2, label=f"TP: ${trade['tp']:.2f}")

    # Exit point
    exit_color = 'green' if trade['result'] == 'win' else 'red'
    exit_marker = 'o'
    ax.scatter([exit_x], [trade['exit_price']], color=exit_color, s=200, zorder=5,
               marker=exit_marker, edgecolors='black', linewidth=2,
               label=f"Exit ({trade['result'].upper()}): ${trade['exit_price']:.2f}")

    # Fill the trade zone
    if trade['type'] == 'long':
        # Long trade - fill from entry to TP (green) and entry to SL (red)
        ax.fill_between([entry_x, exit_x], trade['entry_price'], trade['tp'],
                        alpha=0.1, color='green')
        ax.fill_between([entry_x, exit_x], trade['sl'], trade['entry_price'],
                        alpha=0.1, color='red')
    else:
        # Short trade
        ax.fill_between([entry_x, exit_x], trade['entry_price'], trade['sl'],
                        alpha=0.1, color='red')
        ax.fill_between([entry_x, exit_x], trade['tp'], trade['entry_price'],
                        alpha=0.1, color='green')

    # Annotations
    ax.annotate('SWEEP!', xy=(entry_x, level_price),
                xytext=(entry_x - 5, level_price + trade['atr'] * 0.5),
                fontsize=10, color='purple', fontweight='bold',
                arrowprops=dict(arrowstyle='->', color='purple'))

    # Title and labels
    result_emoji = "WIN" if trade['result'] == 'win' else "LOSS"
    result_r = '+2R' if trade['result'] == 'win' else '-1R'
    trade_dir = "LONG" if trade['type'] == 'long' else "SHORT"

    ax.set_title(f"Trade #{trade_num}: {trade_dir} - {result_emoji} ({result_r})\n"
                 f"Entry: {trade['entry_time'].strftime('%Y-%m-%d %H:%M')} | "
                 f"Exit: {trade['exit_time'].strftime('%Y-%m-%d %H:%M')}",
                 fontsize=14, fontweight='bold')

    ax.set_xlabel('Bar Index')
    ax.set_ylabel('Price ($)')
    ax.legend(loc='upper left')
    ax.grid(True, alpha=0.3)

    # Set x-axis labels to show times
    tick_positions = list(range(0, len(subset), max(1, len(subset) // 10)))
    tick_labels = [subset.index[i].strftime('%m-%d %H:%M') for i in tick_positions]
    ax.set_xticks(tick_positions)
    ax.set_xticklabels(tick_labels, rotation=45, ha='right')

    plt.tight_layout()
    return fig

def create_summary_chart(trades, df):
    """Create summary chart of all trades"""
    fig, axes = plt.subplots(2, 2, figsize=(16, 12))

    # 1. Win/Loss distribution
    ax1 = axes[0, 0]
    wins = sum(1 for t in trades if t['result'] == 'win')
    losses = len(trades) - wins
    ax1.bar(['Wins', 'Losses'], [wins, losses], color=['green', 'red'])
    ax1.set_title(f'Trade Results (WR: {wins/len(trades)*100:.1f}%)')
    ax1.set_ylabel('Count')
    for i, v in enumerate([wins, losses]):
        ax1.text(i, v + 0.5, str(v), ha='center', fontsize=12, fontweight='bold')

    # 2. Long vs Short distribution
    ax2 = axes[0, 1]
    longs = sum(1 for t in trades if t['type'] == 'long')
    shorts = len(trades) - longs
    long_wins = sum(1 for t in trades if t['type'] == 'long' and t['result'] == 'win')
    short_wins = sum(1 for t in trades if t['type'] == 'short' and t['result'] == 'win')

    x = np.arange(2)
    width = 0.35
    ax2.bar(x - width/2, [longs, shorts], width, label='Total', color='blue', alpha=0.7)
    ax2.bar(x + width/2, [long_wins, short_wins], width, label='Wins', color='green', alpha=0.7)
    ax2.set_xticks(x)
    ax2.set_xticklabels(['Long', 'Short'])
    ax2.set_title('Long vs Short Performance')
    ax2.legend()

    # 3. Equity curve
    ax3 = axes[1, 0]
    equity = [0]
    for t in trades:
        if t['result'] == 'win':
            equity.append(equity[-1] + 2)
        else:
            equity.append(equity[-1] - 1)
    ax3.plot(equity, color='blue', linewidth=2)
    ax3.axhline(y=0, color='black', linestyle='--', alpha=0.5)
    ax3.fill_between(range(len(equity)), equity, 0,
                     where=[e >= 0 for e in equity], color='green', alpha=0.3)
    ax3.fill_between(range(len(equity)), equity, 0,
                     where=[e < 0 for e in equity], color='red', alpha=0.3)
    ax3.set_title(f'Equity Curve (Final: {equity[-1]:+.1f}R)')
    ax3.set_xlabel('Trade #')
    ax3.set_ylabel('Cumulative R')
    ax3.grid(True, alpha=0.3)

    # 4. Monthly distribution
    ax4 = axes[1, 1]
    monthly = {}
    for t in trades:
        month = t['entry_time'].strftime('%Y-%m')
        if month not in monthly:
            monthly[month] = {'wins': 0, 'losses': 0}
        if t['result'] == 'win':
            monthly[month]['wins'] += 1
        else:
            monthly[month]['losses'] += 1

    months = sorted(monthly.keys())
    if len(months) > 20:
        # Show only last 20 months
        months = months[-20:]

    wins_by_month = [monthly[m]['wins'] for m in months]
    losses_by_month = [monthly[m]['losses'] for m in months]

    x = np.arange(len(months))
    ax4.bar(x, wins_by_month, label='Wins', color='green', alpha=0.7)
    ax4.bar(x, [-l for l in losses_by_month], label='Losses', color='red', alpha=0.7)
    ax4.set_xticks(x[::3])  # Every 3rd month
    ax4.set_xticklabels([months[i] for i in range(0, len(months), 3)], rotation=45, ha='right')
    ax4.set_title('Monthly Trade Distribution')
    ax4.legend()
    ax4.axhline(y=0, color='black', linewidth=0.5)

    plt.suptitle('Sweep Strategy - Trade Summary', fontsize=16, fontweight='bold')
    plt.tight_layout()
    return fig

# =============================================================================
# MAIN
# =============================================================================

def main():
    print("=" * 70)
    print("SWEEP STRATEGY TRADE VISUALIZATION")
    print("=" * 70)

    # Load data
    df_raw = load_data()
    print(f"Loaded {len(df_raw):,} 1-min bars")

    # Resample to 15-min
    df = add_atr(resample(df_raw, '15min'))
    print(f"Resampled to {len(df):,} 15-min bars")

    # Detect swings
    print("Detecting swings...")
    ltf_h, ltf_l = get_swings(df, 5)
    print(f"Found {len(ltf_h)} swing highs, {len(ltf_l)} swing lows")

    # Convert to numpy
    ltf_h_times = np.array([t.value for t, _ in ltf_h])
    ltf_h_prices = np.array([p for _, p in ltf_h])
    ltf_l_times = np.array([t.value for t, _ in ltf_l])
    ltf_l_prices = np.array([p for _, p in ltf_l])

    # Detect sweeps and trades (Option B: 1.0x ATR displacement)
    print("\nDetecting sweeps with 1.0x ATR displacement (Option B)...")
    trades = detect_sweeps_with_trades(
        df, ltf_h_times, ltf_h_prices, ltf_l_times, ltf_l_prices,
        lookback=30, min_atr=0.5, disp_atr=1.0, rr=2.0
    )
    print(f"Found {len(trades)} trades")

    # Calculate stats
    wins = sum(1 for t in trades if t['result'] == 'win')
    wr = wins / len(trades) * 100 if trades else 0
    exp = (wr / 100 * 2) - ((100 - wr) / 100)
    print(f"Win Rate: {wr:.1f}%, Expectancy: {exp:+.3f}R")

    # Create summary chart
    print("\nCreating summary chart...")
    fig = create_summary_chart(trades, df)
    fig.savefig(f'{OUTPUT_DIR}/sweep_summary.png', dpi=150, bbox_inches='tight')
    plt.close(fig)
    print(f"Saved: {OUTPUT_DIR}/sweep_summary.png")

    # Visualize sample trades (mix of wins and losses)
    print("\nCreating individual trade charts...")

    # Get sample trades
    win_trades = [t for t in trades if t['result'] == 'win']
    loss_trades = [t for t in trades if t['result'] == 'loss']

    # Sample some trades (5 wins, 3 losses from recent period)
    sample_trades = []
    if len(win_trades) >= 5:
        sample_trades.extend(win_trades[-5:])  # Last 5 wins
    if len(loss_trades) >= 3:
        sample_trades.extend(loss_trades[-3:])  # Last 3 losses

    # Sort by time
    sample_trades.sort(key=lambda x: x['entry_time'])

    for i, trade in enumerate(sample_trades):
        fig = visualize_trade(df, trade, i + 1)
        result_str = 'win' if trade['result'] == 'win' else 'loss'
        filename = f'{OUTPUT_DIR}/trade_{i+1:02d}_{result_str}_{trade["type"]}.png'
        fig.savefig(filename, dpi=150, bbox_inches='tight')
        plt.close(fig)
        print(f"Saved: {filename}")

    print(f"\nDone! Created {len(sample_trades) + 1} charts in {OUTPUT_DIR}/")
    print(f"\nTo view: open {OUTPUT_DIR}/ in Finder")

if __name__ == '__main__':
    main()
