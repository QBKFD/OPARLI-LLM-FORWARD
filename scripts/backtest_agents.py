#!/usr/bin/env python3
"""
scripts/backtest_agents.py

Strategy-agnostic backtest harness for the OPARLI agent system.

There is NO hardcoded strategy here. This script replays historical bars
through the REAL production decision pipeline and records whatever the agents
decide:

    HistoricalDataProvider  (historical data, as_of-bounded → no lookahead)
            │
            ▼
    Technical  ── run_technical_analysis()          [services/technical_service.py]
    Visual     ── VisualAnalysisService.analyze()   [services/analysis/visual_analysis.py]
    Sentiment  ── (neutral until a news source is wired)
            │
            ▼
    Meta       ── MetaDecisionService.make_decision() [services/analysis/meta_decision.py]
            │
            ▼
    Risk       ── run_risk_validation()             [services/risk_service.py]
            │
            ▼
    Execution  ── simulated bar-by-bar fills (the ONLY piece that differs from live)

The exact same services run in production. Swapping HistoricalDataProvider →
LiveDataProvider (and as_of=now) is the whole difference between this and live.

Modes:
    (default)      Technical + Meta + Risk, deterministic, FREE, fast.
    --with-visual  Also runs the Visual agent → real LLM calls (costs money).

Run:
    python scripts/backtest_agents.py --start 2023-01-01 --end 2023-04-01
    python scripts/backtest_agents.py --with-visual --start 2023-01-01 --end 2023-02-01
"""

import sys
import argparse
import logging
from pathlib import Path
from datetime import datetime, timedelta
from io import BytesIO
from typing import Dict, Optional

import pandas as pd
import numpy as np
import pytz

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT / 'backend'))

from services.market_data_provider import HistoricalDataProvider
from services.technical_service import run_technical_analysis
from services.technical_indicators import TechnicalIndicators
from services.analysis.meta_decision import MetaDecisionService
from services.risk_service import run_risk_validation

DATA_CSV = REPO_ROOT / 'database/ohlcv_data/XAUUSD_COMPLETE.csv'
TZ_UTC = pytz.utc

logging.basicConfig(level=logging.WARNING, format='%(asctime)s %(levelname)s %(message)s', datefmt='%H:%M:%S')
logger = logging.getLogger('backtest_agents')
logger.setLevel(logging.INFO)

# Layer-1 weight evolution (mirrors weight_manager multiplicative rule)
WIN_FACTOR, LOSS_FACTOR = 1.05, 0.95
WEIGHT_FLOOR, WEIGHT_CAP = 0.05, 0.60
DEFAULT_WEIGHTS = {'visual': 0.35, 'technical': 0.50, 'sentiment': 0.15}


# ── Chart rendering (only needed for --with-visual) ──────────────────────────
def render_png(df: pd.DataFrame, title: str = '') -> bytes:
    import mplfinance as mpf
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    plot_df = df.set_index('Timestamp') if 'Timestamp' in df.columns else df
    fig, _ = mpf.plot(
        plot_df, type='candle', style='charles', volume=False, title=title,
        figsize=(12, 6), returnfig=True, tight_layout=True,
    )
    buf = BytesIO()
    fig.savefig(buf, format='png', bbox_inches='tight', pad_inches=0, dpi=100)
    buf.seek(0)
    data = buf.read()
    buf.close()
    plt.close(fig)
    return data


# ── Execution simulation (the only non-shared piece) ─────────────────────────
def simulate_fill(df_1m, direction, entry, stop, target, entry_dt, max_hold_min=240):
    end_dt = entry_dt + timedelta(minutes=max_hold_min)
    bars = df_1m[(df_1m.index >= entry_dt) & (df_1m.index <= end_dt)]
    for ts, bar in bars.iterrows():
        if direction == 'LONG':
            if bar['Low'] <= stop:
                return {'exit': stop, 'exit_dt': ts, 'reason': 'stop', 'win': False}
            if bar['High'] >= target:
                return {'exit': target, 'exit_dt': ts, 'reason': 'target', 'win': True}
        else:
            if bar['High'] >= stop:
                return {'exit': stop, 'exit_dt': ts, 'reason': 'stop', 'win': False}
            if bar['Low'] <= target:
                return {'exit': target, 'exit_dt': ts, 'reason': 'target', 'win': True}
    if bars.empty:
        return {'exit': entry, 'exit_dt': entry_dt, 'reason': 'no_data', 'win': False}
    last = float(bars['Close'].iloc[-1])
    win = (last > entry) if direction == 'LONG' else (last < entry)
    return {'exit': last, 'exit_dt': bars.index[-1], 'reason': 'timeout', 'win': win}


def update_weights(weights, signals, direction, is_win):
    """Layer-1 multiplicative update; only agents that took a directional side move."""
    new = dict(weights)
    for name, sig in signals.items():
        sig = (sig or 'PASS').upper()
        if sig not in ('LONG', 'SHORT'):
            continue
        correct = (sig == direction) == is_win
        new[name] = min(max(weights[name] * (WIN_FACTOR if correct else LOSS_FACTOR), WEIGHT_FLOOR), WEIGHT_CAP)
    total = sum(new.values())
    return {k: v / total for k, v in new.items()}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--start', default='2023-01-01')
    ap.add_argument('--end', default='2023-04-01')
    ap.add_argument('--symbol', default='XAUUSD')
    ap.add_argument('--scan-every', type=int, default=60, help='minutes between scans')
    ap.add_argument('--account', type=float, default=10_000.0)
    ap.add_argument('--with-visual', action='store_true', help='enable Visual agent (real LLM calls, costs money)')
    ap.add_argument('--output', default=str(REPO_ROOT / 'database/historical_news/backtest_agents_results.csv'))
    args = ap.parse_args()

    # Load data
    logger.info(f"Loading {DATA_CSV}")
    df = pd.read_csv(DATA_CSV, parse_dates=['timestamp'])
    df.sort_values('timestamp', inplace=True)
    df.set_index('timestamp', inplace=True)
    df.index = pd.DatetimeIndex(df.index).tz_localize(TZ_UTC)
    df.columns = [c.capitalize() for c in df.columns]
    df = df[['Open', 'High', 'Low', 'Close', 'Volume']].astype(float)

    provider = HistoricalDataProvider(df, symbol=args.symbol)

    # Visual agent (optional)
    vis_service = None
    if args.with_visual:
        from config.llm_provider import get_llm_client
        from services.analysis.visual_analysis import VisualAnalysisService
        vis_service = VisualAnalysisService(llm_client=get_llm_client())
        logger.info("Visual agent ENABLED (LLM calls will be made)")

    # Meta decision service — deterministic unless LLM explicitly wanted.
    meta = MetaDecisionService(weights=dict(DEFAULT_WEIGHTS))

    # Simulated account state
    balance = args.account
    peak = balance
    weights = dict(DEFAULT_WEIGHTS)

    start = TZ_UTC.localize(datetime.strptime(args.start, '%Y-%m-%d'))
    end = TZ_UTC.localize(datetime.strptime(args.end, '%Y-%m-%d'))

    results = []
    in_trade_until = None
    day = None
    daily_start_balance = balance
    trades_today = 0

    scan = start.replace(hour=0, minute=0)
    step = timedelta(minutes=args.scan_every)

    while scan < end:
        # Daily reset
        if day != scan.date():
            day = scan.date()
            daily_start_balance = balance
            trades_today = 0

        # Skip while a position is open
        if in_trade_until and scan < in_trade_until:
            scan += step
            continue

        as_of = scan

        # --- Technical (pure, free) ---
        tech = run_technical_analysis(provider, args.symbol, as_of=as_of)
        tech_sig = {'signal': tech['signal'], 'confidence': tech['confidence']}

        # Need bars for context / entry price
        entry_price = provider.get_price(args.symbol, as_of=as_of)
        if entry_price is None:
            scan += step
            continue

        # market context (ATR, S/R) from 15m bars for Risk
        tf = provider.get_timeframe_data(args.symbol, timeframes=['15m'], as_of=as_of)
        if '15m' not in tf or len(tf['15m']) < 20:
            scan += step
            continue
        df15 = tf['15m']
        atr = float(TechnicalIndicators.calculate_atr(df15['High'], df15['Low'], df15['Close']).iloc[-1])
        support, resistance = TechnicalIndicators.find_support_resistance(df15['High'], df15['Low'], df15['Close'])

        # --- Visual (optional, LLM) ---
        vis_sig = {'signal': 'PASS', 'confidence': 0.0}
        if vis_service is not None:
            try:
                img = render_png(df15.tail(48), title=f'{args.symbol} 15m @ {as_of}')
                v = vis_service.analyze_chart(image_data=img, market_context={'symbol': args.symbol, 'price': entry_price})
                vis_sig = {'signal': v['signal'], 'confidence': v['confidence']}
            except Exception as e:
                logger.warning(f"Visual failed @ {as_of}: {e}")

        # --- Sentiment (neutral until a news source is wired) ---
        sent_sig = {'signal': 'PASS', 'confidence': 0.5}

        # --- Meta (real service) ---
        meta.set_weights(weights)
        decision = meta.make_decision(
            visual_analysis=vis_sig,
            technical_analysis=tech_sig,
            sentiment_analysis=sent_sig,
            current_price=entry_price,
            use_llm=False,   # deterministic threshold logic; free
        )

        row = {
            'scan_time': str(as_of),
            'balance': round(balance, 2),
            'tech_signal': tech_sig['signal'], 'tech_conf': round(tech_sig['confidence'], 3),
            'vis_signal': vis_sig['signal'], 'vis_conf': round(vis_sig['confidence'], 3),
            'meta_decision': decision['decision'], 'meta_conf': round(decision['confidence'], 3),
            'weighted_score': round(decision.get('weighted_score', 0), 3),
            'agreement': round(decision.get('agreement', 0), 3),
            'w_visual': round(weights['visual'], 4), 'w_technical': round(weights['technical'], 4),
            'trade': False, 'entry': None, 'stop': None, 'target': None,
            'exit': None, 'exit_reason': None, 'pnl_r': None, 'win': None,
        }

        if decision['decision'] not in ('LONG', 'SHORT'):
            results.append(row)
            scan += step
            continue

        # --- Risk (real service, simulated account_state) ---
        drawdown_pct = (balance - peak) / peak * 100 if peak > 0 else 0.0
        daily_pnl_pct = (balance - daily_start_balance) / daily_start_balance * 100
        account_state = {
            'current_balance': balance,
            'daily_pnl_pct': daily_pnl_pct,
            'drawdown_pct': drawdown_pct,
            'open_positions': 0,
            'trades_today': trades_today,
        }
        trade_request = {
            'direction': decision['decision'],
            'symbol': args.symbol,
            'entry_price': entry_price,
            'confidence': decision['confidence'],
            'market_context': {'atr': atr, 'support_level': float(support),
                               'resistance_level': float(resistance), 'spread': 0.30},
        }
        risk = run_risk_validation(trade_request, account_state)

        if risk['status'] != 'APPROVED':
            row['exit_reason'] = f"risk_reject:{risk.get('category')}"
            results.append(row)
            scan += step
            continue

        tp = risk['trade_parameters']
        fill = simulate_fill(df, decision['decision'], entry_price,
                             tp['stop_loss'], tp['take_profit'], as_of)

        # PnL in dollars: units × price move (simple linear)
        move = (fill['exit'] - entry_price) if decision['decision'] == 'LONG' else (entry_price - fill['exit'])
        pnl = move * tp['position_size']
        stop_dist = tp['stop_distance']
        pnl_r = move / stop_dist if stop_dist else 0.0

        balance += pnl
        peak = max(peak, balance)
        trades_today += 1
        in_trade_until = fill['exit_dt']
        weights = update_weights(weights, {'visual': vis_sig['signal'], 'technical': tech_sig['signal']},
                                 decision['decision'], fill['win'])

        row.update({
            'trade': True, 'entry': round(entry_price, 3), 'stop': round(tp['stop_loss'], 3),
            'target': round(tp['take_profit'], 3), 'exit': round(fill['exit'], 3),
            'exit_reason': fill['reason'], 'pnl_r': round(pnl_r, 3), 'win': fill['win'],
            'pnl_usd': round(pnl, 2), 'balance': round(balance, 2),
        })
        results.append(row)
        logger.info(f"{as_of} {decision['decision']} conf={decision['confidence']:.2f} "
                    f"→ {fill['reason']} R={pnl_r:+.2f} bal=${balance:,.0f}")
        scan += step

    # Save + summarize
    out = pd.DataFrame(results)
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(args.output, index=False)

    trades = out[out['trade'] == True]
    print("\n" + "=" * 60)
    print("AGENT-SYSTEM BACKTEST (no hardcoded strategy)")
    print("=" * 60)
    print(f"Period:        {args.start} → {args.end}")
    print(f"Scans:         {len(out):,}  (every {args.scan_every}min)")
    print(f"Visual agent:  {'ON (LLM)' if args.with_visual else 'off'}")
    print(f"Trades taken:  {len(trades):,}")
    if len(trades):
        wr = (trades['win'] == True).mean()
        longs = int((trades['meta_decision'] == 'LONG').sum())
        shorts = int((trades['meta_decision'] == 'SHORT').sum())
        print(f"Win rate:      {wr:.1%}")
        print(f"Direction:     {longs} LONG / {shorts} SHORT")
        print(f"Total R:       {trades['pnl_r'].sum():.2f}")
        print(f"Final balance: ${balance:,.2f}  ({(balance/args.account-1)*100:+.1f}%)")
        print(f"Exit reasons:  {dict(trades['exit_reason'].value_counts())}")
    print(f"\nSaved → {args.output}")
    print("=" * 60)
    # This harness is a plumbing check, not an edge measurement. It runs a
    # single path over a single period with no control arm and no train/test
    # split, and (by default) only the Technical analyst votes. That trades now
    # happen at all confirms the Meta gate is wired correctly after
    # participation-renormalisation — nothing more. Do not read the R or win
    # rate below as evidence of a profitable strategy.
    print("PLUMBING CONFIRMATION, NOT EDGE — single path, single period, no")
    print("control, no train/test split. Not evidence of a profitable strategy.")
    print("=" * 60)


if __name__ == '__main__':
    main()
