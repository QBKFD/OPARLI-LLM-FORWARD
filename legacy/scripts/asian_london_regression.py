#!/usr/bin/env python3
"""
Asian → London session regression analysis for XAUUSD.

Answers:
  1. Does Asian direction predict London direction? (continuation vs reversal)
  2. Does Asian range size predict London breakout quality?
  3. Does Asian close position predict London ORB success?
  4. What is the London reversal rate for gold?

Usage:
    python3 scripts/asian_london_regression.py
"""

import sys
import os
import datetime
import numpy as np
import pandas as pd
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

LONDON_TZ = ZoneInfo("Europe/London")


def get_london_open_utc_hour(date) -> int:
    dt = datetime.datetime(date.year, date.month, date.day, 8, 0, tzinfo=LONDON_TZ)
    return dt.astimezone(datetime.timezone.utc).hour


def load_1h() -> pd.DataFrame:
    import psycopg2
    db_url = os.environ.get('DATABASE_URL')
    if not db_url:
        raise ValueError("DATABASE_URL not set")
    conn = psycopg2.connect(db_url)
    # Try the materialized view first, fall back to resampling 1min
    try:
        query = """
            SELECT timestamp AT TIME ZONE 'UTC' as timestamp,
                   open::FLOAT, high::FLOAT, low::FLOAT, close::FLOAT
            FROM ohlcv_1h
            WHERE symbol = 'XAUUSD'
            ORDER BY timestamp
        """
        df = pd.read_sql(query, conn, parse_dates=['timestamp'])
    except Exception:
        conn.rollback()
        query = """
            SELECT
                date_trunc('hour', timestamp AT TIME ZONE 'UTC') as timestamp,
                (array_agg(open::FLOAT ORDER BY timestamp))[1]  as open,
                MAX(high::FLOAT)                                 as high,
                MIN(low::FLOAT)                                  as low,
                (array_agg(close::FLOAT ORDER BY timestamp DESC))[1] as close
            FROM ohlcv_historical_1min
            WHERE symbol = 'XAUUSD'
            GROUP BY 1
            ORDER BY 1
        """
        df = pd.read_sql(query, conn, parse_dates=['timestamp'])
    conn.close()

    df['timestamp'] = pd.to_datetime(df['timestamp']).dt.tz_localize(None)
    df.set_index('timestamp', inplace=True)
    df.sort_index(inplace=True)
    return df


def compute_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    hl = df['high'] - df['low']
    hc = (df['high'] - df['close'].shift(1)).abs()
    lc = (df['low']  - df['close'].shift(1)).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    return tr.ewm(span=period, adjust=False).mean()


def build_session_frame(df: pd.DataFrame) -> pd.DataFrame:
    atr = compute_atr(df)
    records = []

    for day in sorted(set(df.index.date)):
        lo_hour = get_london_open_utc_hour(day)

        # --- Asian session 00:00 – London open ---
        a_start = pd.Timestamp(day.year, day.month, day.day, 0, 0)
        a_end   = pd.Timestamp(day.year, day.month, day.day, lo_hour, 0)
        asian   = df[(df.index >= a_start) & (df.index < a_end)]
        if len(asian) < 4:
            continue

        a_open  = float(asian.iloc[0]['open'])
        a_close = float(asian.iloc[-1]['close'])
        a_high  = float(asian['high'].max())
        a_low   = float(asian['low'].min())
        a_range = a_high - a_low
        if a_range < 0.5:          # degenerate / holiday
            continue

        atr_val = float(atr.get(asian.index[-1], np.nan))

        # --- London session London open – 16:00 UTC ---
        l_start  = pd.Timestamp(day.year, day.month, day.day, lo_hour, 0)
        l_end    = pd.Timestamp(day.year, day.month, day.day, 16, 0)
        london   = df[(df.index >= l_start) & (df.index < l_end)]
        if len(london) < 4:
            continue

        l_open  = float(london.iloc[0]['open'])
        l_close = float(london.iloc[-1]['close'])
        l_high  = float(london['high'].max())
        l_low   = float(london['low'].min())

        # ORB-relevant: did London break above Asian high within first 4 bars?
        london_early = london.iloc[:4]
        broke_high_early = int(london_early['high'].max() > a_high)

        # Direction of Asian session: -1 to +1
        a_dir = (a_close - a_open) / a_range

        records.append({
            'date':                  day,
            'dst_active':            int(lo_hour == 7),

            # --- Asian features ---
            'asian_direction':       a_dir,
            'asian_range_norm':      a_range / atr_val if atr_val > 0 else np.nan,
            'asian_close_position':  (a_close - a_low) / a_range,
            'asian_range':           a_range,
            'asian_high':            a_high,
            'asian_low':             a_low,

            # --- London targets ---
            'broke_asian_high':      int(l_high > a_high),
            'broke_asian_low':       int(l_low  < a_low),
            'broke_high_early':      broke_high_early,

            # Continuation: London moved same direction as Asian session
            'london_continuation':   int(
                (l_close > l_open) == (a_close > a_open)
            ),

            # Normalized London return vs Asian close
            'london_return_norm':    (l_close - a_close) / a_range,

            # If we entered long above Asian high, this is the available reward
            # (distance from Asian high to London high, in ATR)
            'orb_long_reward_atr':   (l_high - a_high) / atr_val if atr_val > 0 else np.nan,
            'orb_long_loss_atr':     (a_high - l_low)  / atr_val if atr_val > 0 else np.nan,
        })

    return pd.DataFrame(records).set_index('date').dropna(subset=['asian_range_norm'])


def section(title: str) -> None:
    print(f"\n{'='*65}")
    print(f"  {title}")
    print(f"{'='*65}")


def run_analysis(df: pd.DataFrame) -> None:
    from scipy import stats

    section(f"SAMPLE OVERVIEW  ({len(df)} trading days, "
            f"{df.index.min()} → {df.index.max()})")

    print(f"  London broke Asian HIGH:  {df['broke_asian_high'].mean():.1%} of days")
    print(f"  London broke Asian LOW:   {df['broke_asian_low'].mean():.1%} of days")
    print(f"  Both broken (inside day): "
          f"{(~df['broke_asian_high'].astype(bool) & ~df['broke_asian_low'].astype(bool)).mean():.1%}")
    print(f"  Asian direction → London continued: {df['london_continuation'].mean():.1%}")
    print(f"  Avg ORB long reward (ATR): {df['orb_long_reward_atr'].mean():+.2f}")
    print(f"  Avg ORB long loss   (ATR): {df['orb_long_loss_atr'].mean():+.2f}")

    # ------------------------------------------------------------------
    section("PEARSON CORRELATIONS WITH LONDON OUTCOMES")
    print(f"  {'Feature':<32} {'Broke high':>11} {'Continuation':>13} {'Lon return':>11}")
    print(f"  {'-'*70}")

    feat_labels = {
        'asian_direction':      'Asian direction  (-1 bear → +1 bull)',
        'asian_range_norm':     'Asian range / ATR(14)',
        'asian_close_position': 'Asian close position (0=low, 1=high)',
    }
    targets = ['broke_asian_high', 'london_continuation', 'london_return_norm']

    for feat, label in feat_labels.items():
        x = df[feat].values
        row = []
        for tgt in targets:
            y = df[tgt].values
            mask = ~(np.isnan(x) | np.isnan(y))
            r, p = stats.pearsonr(x[mask], y[mask])
            sig = '**' if p < 0.01 else ('*' if p < 0.05 else '  ')
            row.append(f"{r:+.3f}{sig}")
        print(f"  {label[:32]:<32} {row[0]:>11} {row[1]:>13} {row[2]:>11}")
    print(f"\n  * p<0.05   ** p<0.01")

    # ------------------------------------------------------------------
    section("ORB BREAKOUT RATE BY ASIAN DIRECTION")
    print(f"  {'Asian session':<22} {'N':>5} {'Broke high':>11} "
          f"{'Broke low':>10} {'Lon return':>11} {'Continued':>10}")
    print(f"  {'-'*65}")

    bins   = [-1.01, -0.5, -0.1, 0.1, 0.5, 1.01]
    labels = ['Strong bear', 'Weak bear', 'Neutral', 'Weak bull', 'Strong bull']
    df['a_dir_bucket'] = pd.cut(df['asian_direction'], bins=bins, labels=labels)

    for bucket, grp in df.groupby('a_dir_bucket', observed=True):
        print(f"  {str(bucket):<22} {len(grp):>5} "
              f"{grp['broke_asian_high'].mean():>10.1%} "
              f"{grp['broke_asian_low'].mean():>10.1%} "
              f"{grp['london_return_norm'].mean():>+10.3f} "
              f"{grp['london_continuation'].mean():>10.1%}")

    # ------------------------------------------------------------------
    section("ORB BREAKOUT RATE BY ASIAN RANGE SIZE (quartiles)")
    print(f"  {'Asian range (ATR)':<22} {'N':>5} {'Broke high':>11} "
          f"{'Reward ATR':>11} {'Loss ATR':>10}")
    print(f"  {'-'*62}")

    df['range_bucket'] = pd.qcut(df['asian_range_norm'], q=4,
                                  labels=['Q1 tight', 'Q2', 'Q3', 'Q4 wide'])
    for bucket, grp in df.groupby('range_bucket', observed=True):
        rng_mean = grp['asian_range_norm'].mean()
        print(f"  {str(bucket):<14} (avg {rng_mean:.2f}x) {len(grp):>5} "
              f"{grp['broke_asian_high'].mean():>10.1%} "
              f"{grp['orb_long_reward_atr'].mean():>+10.2f} "
              f"{grp['orb_long_loss_atr'].mean():>10.2f}")

    # ------------------------------------------------------------------
    section("ORB BREAKOUT RATE BY ASIAN CLOSE POSITION")
    print(f"  {'Close position':<22} {'N':>5} {'Broke high':>11} {'Lon return':>11}")
    print(f"  {'-'*52}")

    df['close_pos_bucket'] = pd.cut(
        df['asian_close_position'],
        bins=[0, 0.2, 0.4, 0.6, 0.8, 1.01],
        labels=['Bottom 20%', '20–40%', '40–60%', '60–80%', 'Top 20%']
    )
    for bucket, grp in df.groupby('close_pos_bucket', observed=True):
        print(f"  {str(bucket):<22} {len(grp):>5} "
              f"{grp['broke_asian_high'].mean():>10.1%} "
              f"{grp['london_return_norm'].mean():>+10.3f}")

    # ------------------------------------------------------------------
    section("LOGISTIC REGRESSION: P(London breaks Asian high)")
    try:
        from sklearn.linear_model import LogisticRegression
        from sklearn.preprocessing import StandardScaler
        from sklearn.metrics import roc_auc_score

        feat_cols = ['asian_direction', 'asian_range_norm', 'asian_close_position']
        df_lr = df[feat_cols + ['broke_asian_high']].dropna()
        X = df_lr[feat_cols].values
        y = df_lr['broke_asian_high'].values

        scaler   = StandardScaler()
        X_scaled = scaler.fit_transform(X)
        lr = LogisticRegression(random_state=42)
        lr.fit(X_scaled, y)

        auc = roc_auc_score(y, lr.predict_proba(X_scaled)[:, 1])
        print(f"  AUC: {auc:.3f}   (0.5 = random, 1.0 = perfect)")
        print(f"  Baseline (always predict break): {y.mean():.1%}")
        print(f"\n  Standardised coefficients:")
        for feat, coef in zip(feat_cols, lr.coef_[0]):
            direction = '↑ helps break' if coef > 0 else '↓ hurts break'
            print(f"    {feat:<32} {coef:+.3f}  {direction}")
    except ImportError:
        print("  sklearn not available — pip install scikit-learn")

    # ------------------------------------------------------------------
    section("KEY TAKEAWAYS")
    # Compute the most actionable finding: continuation rate by direction
    bull_days = df[df['asian_direction'] > 0.1]
    bear_days = df[df['asian_direction'] < -0.1]
    print(f"  Bullish Asian → London broke high:  {bull_days['broke_asian_high'].mean():.1%}")
    print(f"  Bearish Asian → London broke high:  {bear_days['broke_asian_high'].mean():.1%}")
    print(f"  Bullish Asian → London continued:   {bull_days['london_continuation'].mean():.1%}")
    print(f"  Bearish Asian → London continued:   {bear_days['london_continuation'].mean():.1%}")
    tight = df[df['asian_range_norm'] < df['asian_range_norm'].quantile(0.25)]
    wide  = df[df['asian_range_norm'] > df['asian_range_norm'].quantile(0.75)]
    print(f"\n  Tight Asian range → ORB breakout:   {tight['broke_asian_high'].mean():.1%}")
    print(f"  Wide  Asian range → ORB breakout:   {wide['broke_asian_high'].mean():.1%}")


def load_15m() -> pd.DataFrame:
    import psycopg2
    db_url = os.environ.get('DATABASE_URL')
    conn = psycopg2.connect(db_url)
    try:
        query = """
            SELECT timestamp AT TIME ZONE 'UTC' as timestamp,
                   open::FLOAT, high::FLOAT, low::FLOAT, close::FLOAT
            FROM ohlcv_15min
            WHERE symbol = 'XAUUSD'
            ORDER BY timestamp
        """
        df = pd.read_sql(query, conn, parse_dates=['timestamp'])
    except Exception:
        conn.rollback()
        query = """
            SELECT
                date_trunc('hour', timestamp AT TIME ZONE 'UTC')
                  + INTERVAL '15 min' * (
                      EXTRACT(minute FROM timestamp AT TIME ZONE 'UTC')::int / 15
                  ) as timestamp,
                (array_agg(open::FLOAT  ORDER BY timestamp))[1]       as open,
                MAX(high::FLOAT)                                        as high,
                MIN(low::FLOAT)                                         as low,
                (array_agg(close::FLOAT ORDER BY timestamp DESC))[1]   as close
            FROM ohlcv_historical_1min
            WHERE symbol = 'XAUUSD'
            GROUP BY 1
            ORDER BY 1
        """
        df = pd.read_sql(query, conn, parse_dates=['timestamp'])
    conn.close()
    df['timestamp'] = pd.to_datetime(df['timestamp']).dt.tz_localize(None)
    df.set_index('timestamp', inplace=True)
    df.sort_index(inplace=True)
    return df


def compute_atr_15m(df: pd.DataFrame, period: int = 14) -> pd.Series:
    hl = df['high'] - df['low']
    hc = (df['high'] - df['close'].shift(1)).abs()
    lc = (df['low']  - df['close'].shift(1)).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    return tr.ewm(span=period, adjust=False).mean()


def simulate_orb_trades(session_df: pd.DataFrame, df_15m: pd.DataFrame,
                        close_pos_threshold: float = 0.6,
                        range_pctile_cap: float = 75.0,
                        stop_mode: str = 'asian_low',   # 'asian_low' or 'atr'
                        stop_atr_mult: float = 2.0,
                        target_r: float = 1.0,
                        entry_cutoff_hour: int = 11,
                        eod_hour: int = 16,
                        breakeven_after_r: float = 0.5,
                        spread_usd: float = 0.40) -> pd.DataFrame:
    """
    Simulate ORB long trades on days where:
      - Asian close position > close_pos_threshold
      - Asian range < range_pctile_cap percentile (optional)

    Uses 15m bars for realistic entry/exit simulation.
    Returns one row per qualifying day with trade outcome.
    """
    atr_15m = compute_atr_15m(df_15m)

    # Apply Asian range cap filter
    range_cap = session_df['asian_range_norm'].quantile(range_pctile_cap / 100)
    qualified = session_df[
        (session_df['asian_close_position'] >= close_pos_threshold) &
        (session_df['asian_range_norm'] <= range_cap)
    ]

    records = []

    for day, row in qualified.iterrows():
        lo_hour = get_london_open_utc_hour(day)
        london_open = pd.Timestamp(day.year, day.month, day.day, lo_hour, 0)
        orb_end     = london_open + pd.Timedelta(minutes=30)
        cutoff      = pd.Timestamp(day.year, day.month, day.day, entry_cutoff_hour, 0)
        eod         = pd.Timestamp(day.year, day.month, day.day, 22, 0)

        asian_high = row['asian_high']
        asian_low  = row['asian_low']

        # 15m bars from ORB end to EOD
        day_bars = df_15m[
            (df_15m.index >= orb_end) &
            (df_15m.index <= eod) &
            (df_15m.index.date == day)
        ]
        if len(day_bars) == 0:
            continue

        # Asian session 15m bars for ATR reference
        asian_bars = df_15m[
            (df_15m.index >= pd.Timestamp(day.year, day.month, day.day, 0, 0)) &
            (df_15m.index < london_open) &
            (df_15m.index.date == day)
        ]
        asian_atr_avg = float(atr_15m.reindex(asian_bars.index).mean()) if len(asian_bars) > 0 else np.nan

        entry_price   = None
        stop_price    = None
        target_price  = None
        original_risk = None   # fixed reference for R calc — never changes after entry
        entry_time    = None
        exit_price    = None
        exit_time     = None
        exit_reason   = None
        r_multiple    = None
        triggered     = False
        breakeven_hit = False

        for ts, bar in day_bars.iterrows():
            if ts > cutoff and entry_price is None:
                break  # past entry window, no entry

            current_atr = float(atr_15m.get(ts, np.nan))
            if np.isnan(current_atr):
                continue

            # --- Manage open trade ---
            if entry_price is not None:
                # Breakeven: move stop to entry once +breakeven_after_r in favour
                if not breakeven_hit and bar['high'] >= entry_price + breakeven_after_r * original_risk:
                    stop_price    = entry_price
                    breakeven_hit = True
                if bar['low'] <= stop_price:
                    exit_price  = stop_price
                    exit_time   = ts
                    exit_reason = 'breakeven' if breakeven_hit else 'stop'
                    break
                if bar['high'] >= target_price:
                    exit_price  = target_price
                    exit_time   = ts
                    exit_reason = 'target'
                    break
                if ts.hour >= eod_hour:
                    exit_price  = bar['close']
                    exit_time   = ts
                    exit_reason = 'eod'
                    break
                continue

            # --- Entry: close above Asian high, ATR expanding ---
            atr_expanding = (not np.isnan(asian_atr_avg)) and (current_atr > asian_atr_avg)
            if bar['close'] > asian_high and atr_expanding:
                triggered    = True
                entry_price  = bar['close'] + spread_usd / 2

                if stop_mode == 'asian_low':
                    stop_price = asian_low - spread_usd / 2
                else:
                    stop_price = entry_price - stop_atr_mult * current_atr

                original_risk = entry_price - stop_price
                if original_risk <= 0:
                    entry_price = None
                    continue
                target_price = entry_price + target_r * original_risk
                entry_time   = ts

        # EOD close if still open
        if entry_price is not None and exit_price is None:
            last = day_bars.iloc[-1]
            exit_price  = last['close']
            exit_time   = day_bars.index[-1]
            exit_reason = 'eod'

        if entry_price is not None and exit_price is not None:
            net_pnl    = (exit_price - entry_price) - spread_usd / 2
            r_multiple = net_pnl / original_risk

        records.append({
            'date':               day,
            'asian_close_pos':    row['asian_close_position'],
            'asian_range_norm':   row['asian_range_norm'],
            'triggered':          triggered,
            'entry_time':         entry_time,
            'entry_price':        round(entry_price, 3) if entry_price is not None else None,
            'stop_price':         round(stop_price, 3) if stop_price is not None else None,
            'target_price':       round(target_price, 3) if target_price is not None else None,
            'exit_price':         round(exit_price, 3) if exit_price is not None else None,
            'exit_time':          exit_time,
            'exit_reason':        exit_reason,
            'r_multiple':         round(r_multiple, 4) if r_multiple is not None else None,
            'asian_high':         round(asian_high, 3),
            'asian_low':          round(asian_low, 3),
            'stop_mode':          stop_mode,
        })

    return pd.DataFrame(records)


def report_simulation(sim: pd.DataFrame, label: str) -> None:
    section(f"ORB TRADE SIMULATION — {label}")

    triggered = sim[sim['triggered']]
    traded    = sim[sim['r_multiple'].notna()]

    print(f"  Qualifying days (filter passed):  {len(sim)}")
    print(f"  Days that triggered entry:        {len(triggered)} "
          f"({len(triggered)/len(sim):.1%})")
    print(f"  Days with completed trade:        {len(traded)}")

    if len(traded) == 0:
        print("  No completed trades.")
        return

    r = traded['r_multiple'].values
    wins = r[r > 0]
    losses = r[r < 0]

    print(f"\n  Win rate:       {(r > 0).mean():.1%}")
    print(f"  Avg R/trade:    {r.mean():+.3f}")
    print(f"  Median R:       {np.median(r):+.3f}")
    print(f"  Profit factor:  "
          f"{wins.sum() / abs(losses.sum()):.2f}" if len(losses) > 0 else "  Profit factor:  ∞")
    print(f"  Total R:        {r.sum():+.2f}R  over {len(traded)} trades")

    print(f"\n  Exit reasons:")
    for reason, grp in traded.groupby('exit_reason'):
        wr = (grp['r_multiple'] > 0).mean()
        print(f"    {reason:<14} {len(grp):>4} trades | WR {wr:.1%} | "
              f"Avg R {grp['r_multiple'].mean():+.3f}")

    print(f"\n  R distribution:")
    pctiles = [10, 25, 50, 75, 90]
    for p in pctiles:
        print(f"    p{p:<3}: {np.percentile(r, p):+.2f}R")

    # Breakdown by close position bucket
    print(f"\n  By Asian close position at entry:")
    traded_copy = traded.copy()
    traded_copy['cp_bucket'] = pd.cut(
        traded_copy['asian_close_pos'],
        bins=[0.6, 0.7, 0.8, 0.9, 1.01],
        labels=['60-70%', '70-80%', '80-90%', '90-100%']
    )
    for bucket, grp in traded_copy.groupby('cp_bucket', observed=True):
        if len(grp) == 0:
            continue
        wr = (grp['r_multiple'] > 0).mean()
        avg_r = grp['r_multiple'].mean()
        print(f"    {str(bucket):<10} {len(grp):>4} trades | WR {wr:.1%} | Avg R {avg_r:+.3f}")


if __name__ == '__main__':
    print("Loading 1H OHLCV...")
    df_1h = load_1h()
    print(f"  {len(df_1h)} bars  "
          f"({df_1h.index.min().date()} → {df_1h.index.max().date()})")

    print("Computing session statistics...")
    session_df = build_session_frame(df_1h)

    run_analysis(session_df)

    print("\nLoading 15m OHLCV for trade simulation...")
    df_15m = load_15m()
    print(f"  {len(df_15m)} bars")

    # --- Baseline (v3 parameters) ---
    sim_baseline = simulate_orb_trades(
        session_df, df_15m,
        close_pos_threshold=0.6,
        range_pctile_cap=75.0,
        stop_mode='asian_low',
        target_r=1.5,
        eod_hour=22,
        breakeven_after_r=999,  # disabled
    )
    report_simulation(sim_baseline, "BASELINE | close_pos>0.6 | stop=Asian low | 1.5R | EOD 22:00")

    # Export baseline trades to CSV for inspection
    baseline_trades = sim_baseline[sim_baseline['triggered']].copy()
    out_path = '/tmp/baseline_trades.csv'
    baseline_trades.to_csv(out_path, index=False)
    print(f"\n  [Baseline trades saved to {out_path} — {len(baseline_trades)} rows]")

    # --- v4: breakeven + lower target + London close exit ---
    sim_v4 = simulate_orb_trades(
        session_df, df_15m,
        close_pos_threshold=0.6,
        range_pctile_cap=75.0,
        stop_mode='asian_low',
        target_r=1.0,
        eod_hour=16,
        breakeven_after_r=0.5,
    )
    report_simulation(sim_v4, "v4 | close_pos>0.6 | Asian low stop | BE@0.5R | 1.0R | EOD 16:00")

    # --- v4 tighter: top 20% close position ---
    sim_v4_tight = simulate_orb_trades(
        session_df, df_15m,
        close_pos_threshold=0.8,
        range_pctile_cap=75.0,
        stop_mode='asian_low',
        target_r=1.0,
        eod_hour=16,
        breakeven_after_r=0.5,
    )
    report_simulation(sim_v4_tight, "v4 tight | close_pos>0.8 | Asian low stop | BE@0.5R | 1.0R | EOD 16:00")
