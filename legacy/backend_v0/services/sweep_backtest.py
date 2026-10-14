"""
Liquidity Sweep Backtest Service

Runs the validated Liquidity Sweep strategy with train/test split validation.
Returns trades, validation metrics, and swing structure for chart visualization.
"""

import pandas as pd
import numpy as np
from datetime import datetime
from typing import Dict, List, Optional, Tuple
import os


# Train/Test split dates
TRAIN_START = '2021-04-01'
TRAIN_END = '2023-12-31'
TEST_START = '2024-01-01'
TEST_END = '2025-06-30'

# Data file path (relative to project root)
import pathlib
PROJECT_ROOT = pathlib.Path(__file__).parent.parent.parent
DATA_PATH = PROJECT_ROOT / 'data' / 'snapshots' / 'XAUUSD_COMPLETE.csv'


def load_data() -> pd.DataFrame:
    """Load XAUUSD data from CSV"""
    if not DATA_PATH.exists():
        raise FileNotFoundError(f"Data file not found: {DATA_PATH}")

    df = pd.read_csv(DATA_PATH)
    df['timestamp'] = pd.to_datetime(df['timestamp'], utc=True).dt.tz_localize(None)
    df.set_index('timestamp', inplace=True)
    return df


def resample(df: pd.DataFrame, tf: str) -> pd.DataFrame:
    """Resample to specified timeframe"""
    return df.resample(tf).agg({
        'open': 'first',
        'high': 'max',
        'low': 'min',
        'close': 'last',
        'volume': 'sum'
    }).dropna()


def add_atr(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    """Add ATR indicator"""
    tr = pd.concat([
        df['high'] - df['low'],
        abs(df['high'] - df['close'].shift(1)),
        abs(df['low'] - df['close'].shift(1))
    ], axis=1).max(axis=1)
    df['atr'] = tr.rolling(period).mean()
    return df


def get_swings(df: pd.DataFrame, window: int = 5) -> Tuple[List, List]:
    """Detect swing highs and lows"""
    h_arr = df['high'].values
    l_arr = df['low'].values
    swings_h, swings_l = [], []

    for i in range(window, len(df) - window):
        if h_arr[i] == max(h_arr[i-window:i+window+1]):
            swings_h.append((df.index[i], h_arr[i]))
        if l_arr[i] == min(l_arr[i-window:i+window+1]):
            swings_l.append((df.index[i], l_arr[i]))

    return swings_h, swings_l


def is_killzone(hour: int) -> bool:
    """Check if hour falls in London Open or NY Open kill zones"""
    return (7 <= hour < 10) or (12 <= hour < 15)


def detect_sweeps(df: pd.DataFrame, ltf_h_times: np.ndarray, ltf_h_prices: np.ndarray,
                  ltf_l_times: np.ndarray, ltf_l_prices: np.ndarray,
                  lookback: int = 30, min_atr: float = 0.5, disp_atr: float = 1.0,
                  min_sweep_atr: float = 0.25, min_disp_body_atr: float = 0.5,
                  session_filter: bool = True) -> pd.DataFrame:
    """
    Detect liquidity sweeps with displacement requirement.

    Filters:
    - min_sweep_atr: Minimum sweep wick size in ATR (below = noise)
    - min_disp_body_atr: Minimum displacement candle body in ATR (weak = no conviction)
    - session_filter: Only detect during London/NY kill zones
    """
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

        # Session filter: only trade during London Open or NY Open
        if session_filter and not is_killzone(ts.hour):
            continue

        next_h, next_l, next_c, next_o = highs[i+1], lows[i+1], closes[i+1], opens[i+1]
        next_body = abs(next_c - next_o)

        lookback_ts = idx[i - lookback].value
        mask_h = (ltf_h_times > lookback_ts) & (ltf_h_times < ts_val)
        mask_l = (ltf_l_times > lookback_ts) & (ltf_l_times < ts_val)

        # BEARISH SWEEP
        if mask_h.any():
            rec_h = ltf_h_prices[mask_h]
            for lv in rec_h:
                sweep_wick = h - lv
                if h > lv and c < lv and sweep_wick >= atr * min_atr and c < o:
                    # ATR minimums: sweep wick and displacement body must be meaningful
                    if sweep_wick < atr * min_sweep_atr:
                        continue
                    if next_c < c and (c - next_c) >= atr * disp_atr and next_body >= atr * min_disp_body_atr:
                        sweeps.append({
                            'time': ts,
                            'type': 'bear',
                            'level': float(lv),
                            'atr': float(atr),
                            'sweep_size': float(sweep_wick),
                            'displacement': float(c - next_c)
                        })
                        break

        # BULLISH SWEEP
        if mask_l.any():
            rec_l = ltf_l_prices[mask_l]
            for lv in rec_l:
                sweep_wick = lv - l
                if l < lv and c > lv and sweep_wick >= atr * min_atr and c > o:
                    # ATR minimums: sweep wick and displacement body must be meaningful
                    if sweep_wick < atr * min_sweep_atr:
                        continue
                    if next_c > c and (next_c - c) >= atr * disp_atr and next_body >= atr * min_disp_body_atr:
                        sweeps.append({
                            'time': ts,
                            'type': 'bull',
                            'level': float(lv),
                            'atr': float(atr),
                            'sweep_size': float(sweep_wick),
                            'displacement': float(next_c - c)
                        })
                        break

    return pd.DataFrame(sweeps)


def backtest_sweeps(df: pd.DataFrame, sweeps_df: pd.DataFrame,
                    rr: float = 2.0, fresh_filter: bool = True) -> List[Dict]:
    """
    Backtest sweep signals with full trade details for visualization.
    """
    if len(sweeps_df) == 0:
        return []

    idx = df.index
    highs = df['high'].values
    lows = df['low'].values
    closes = df['close'].values
    opens = df['open'].values

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
        entry_bar_idx = si + 2  # Enter on bar after displacement confirmed (fixes lookahead bias)
        entry_time = idx[entry_bar_idx]
        entry_price = opens[entry_bar_idx]

        if typ == 'bull':
            sl = lows[si] - 0.5
            risk = entry_price - sl
            tp = entry_price + risk * rr
            trade_type = 'LONG'
        else:
            sl = highs[si] + 0.5
            risk = sl - entry_price
            tp = entry_price - risk * rr
            trade_type = 'SHORT'

        # Simulate trade
        result = 'timeout'
        exit_time = None
        exit_price = None
        exit_bar_idx = None

        for k in range(entry_bar_idx + 1, min(entry_bar_idx + 101, len(df))):
            if trade_type == 'LONG':
                if lows[k] <= sl:
                    result = 'loss'
                    exit_price = sl
                    exit_bar_idx = k
                    break
                if highs[k] >= tp:
                    result = 'win'
                    exit_price = tp
                    exit_bar_idx = k
                    break
            else:
                if highs[k] >= sl:
                    result = 'loss'
                    exit_price = sl
                    exit_bar_idx = k
                    break
                if lows[k] <= tp:
                    result = 'win'
                    exit_price = tp
                    exit_bar_idx = k
                    break

        if result != 'timeout' and exit_bar_idx:
            exit_time = idx[exit_bar_idx]
            pnl_r = rr if result == 'win' else -1.0

            trades.append({
                'entryTime': entry_time.isoformat(),
                'exitTime': exit_time.isoformat(),
                'type': trade_type,
                'direction': typ,
                'entryPrice': round(entry_price, 2),
                'exitPrice': round(exit_price, 2),
                'stopLoss': round(sl, 2),
                'takeProfit': round(tp, 2),
                'level': round(lv, 2),
                'result': result,
                'pnl': round(pnl_r, 2),
                'risk': round(risk, 2)
            })

    return trades


def calculate_metrics(trades: List[Dict], period_name: str) -> Dict:
    """Calculate performance metrics for a set of trades"""
    if not trades:
        return {
            'period': period_name,
            'totalTrades': 0,
            'wins': 0,
            'winRate': 0,
            'expectancy': 0,
            'totalR': 0
        }

    wins = sum(1 for t in trades if t['result'] == 'win')
    total = len(trades)
    wr = wins / total * 100 if total > 0 else 0
    total_r = sum(t['pnl'] for t in trades)
    expectancy = total_r / total if total > 0 else 0

    return {
        'period': period_name,
        'totalTrades': total,
        'wins': wins,
        'winRate': round(wr, 1),
        'expectancy': round(expectancy, 3),
        'totalR': round(total_r, 2)
    }


def run_sweep_backtest(disp_atr: float = 1.0) -> Dict:
    """
    Run the full Liquidity Sweep backtest with train/test validation.

    Returns:
        - trades: Full list of trades with entry/exit details
        - validation: Train/test performance metrics
        - swings: Swing highs/lows for chart overlay
        - candlesticks: OHLCV data for chart
    """
    # Load data
    df = load_data()

    # Resample to 15min
    data_15 = add_atr(resample(df, '15min'))

    # Detect swings on 15min
    ltf_h, ltf_l = get_swings(data_15, 5)

    ltf_h_times = np.array([t.value for t, _ in ltf_h])
    ltf_h_prices = np.array([p for _, p in ltf_h])
    ltf_l_times = np.array([t.value for t, _ in ltf_l])
    ltf_l_prices = np.array([p for _, p in ltf_l])

    # Detect sweeps
    sweeps_df = detect_sweeps(
        data_15, ltf_h_times, ltf_h_prices, ltf_l_times, ltf_l_prices,
        lookback=30, min_atr=0.5, disp_atr=disp_atr
    )

    # Run backtest
    all_trades = backtest_sweeps(data_15, sweeps_df, rr=2.0, fresh_filter=True)

    # Split into train/test
    train_trades = [t for t in all_trades if t['entryTime'] >= TRAIN_START and t['entryTime'] <= TRAIN_END]
    test_trades = [t for t in all_trades if t['entryTime'] >= TEST_START and t['entryTime'] <= TEST_END]

    # Calculate metrics
    train_metrics = calculate_metrics(train_trades, 'Train (2021-04 to 2023-12)')
    test_metrics = calculate_metrics(test_trades, 'Test (2024-01 to 2025-06)')
    overall_metrics = calculate_metrics(all_trades, 'Overall')

    # Include candlestick data for test period only (keeps size reasonable)
    # This allows visualization of trades on the chart
    test_data = data_15[TEST_START:TEST_END].copy()

    # Convert to lightweight-charts format
    candle_data = []
    for ts, row in test_data.iterrows():
        candle_data.append({
            'time': int(ts.timestamp()),
            'open': round(float(row['open']), 2),
            'high': round(float(row['high']), 2),
            'low': round(float(row['low']), 2),
            'close': round(float(row['close']), 2),
        })

    # Include swing levels for the test period
    swing_highs = [
        {'time': int(t.timestamp()), 'price': round(float(p), 2)}
        for t, p in ltf_h if t >= pd.Timestamp(TEST_START) and t <= pd.Timestamp(TEST_END)
    ]
    swing_lows = [
        {'time': int(t.timestamp()), 'price': round(float(p), 2)}
        for t, p in ltf_l if t >= pd.Timestamp(TEST_START) and t <= pd.Timestamp(TEST_END)
    ]

    # Build equity curve
    equity = [10000]  # Starting balance
    for t in all_trades:
        risk_amount = equity[-1] * 0.02  # 2% risk per trade
        pnl = t['pnl'] * risk_amount
        equity.append(equity[-1] + pnl)

    equity_curve = [{'time': i, 'value': round(v, 2)} for i, v in enumerate(equity)]

    # Validation status
    is_validated = train_metrics['expectancy'] > 0 and test_metrics['expectancy'] > 0

    return {
        'strategy': 'Liquidity Sweeps',
        'settings': {
            'displacementATR': disp_atr,
            'minSweepATR': 0.5,
            'minSweepWickATR': 0.25,
            'minDispBodyATR': 0.5,
            'sessionFilter': True,
            'killZones': 'London (07-10 UTC) + NY (12-15 UTC)',
            'lookback': 30,
            'riskReward': 2.0,
            'freshLevelFilter': True
        },
        'trades': all_trades,
        'validation': {
            'isValidated': is_validated,
            'train': train_metrics,
            'test': test_metrics,
            'overall': overall_metrics
        },
        'chartData': {
            'candlesticks': candle_data,
            'swingHighs': swing_highs,
            'swingLows': swing_lows
        },
        'equityCurve': equity_curve,
        'summary': {
            'totalTrades': len(all_trades),
            'winRate': overall_metrics['winRate'],
            'netProfit': overall_metrics['totalR'],
            'profitFactor': round((overall_metrics['wins'] * 2) / max(1, overall_metrics['totalTrades'] - overall_metrics['wins']), 2),
            'maxDrawdown': 0,  # Would need more complex calculation
            'sharpeRatio': 0  # Would need more complex calculation
        }
    }
