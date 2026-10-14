#!/usr/bin/env python3
"""
Backtest results analyzer.

Usage:
    python3 scripts/analyze_backtest.py /tmp/strategy_v3.json
    python3 scripts/analyze_backtest.py /tmp/strategy_v3.json --compare /tmp/strategy_v2.json
"""

import sys
import json
import argparse
import numpy as np


def load(path: str) -> dict:
    with open(path) as f:
        return json.load(f)


def fold_summary(data: dict) -> None:
    folds = data['folds']
    trades = data['trades']

    print(f"\n{'='*65}")
    print(f"FOLD-BY-FOLD RESULTS")
    print(f"{'='*65}")
    print(f"{'Fold':>4} {'Period':>21} {'N':>4} {'WR':>6} {'AvgR':>6}  "
          f"{'AR_filt':>7} {'Dur_filt':>8} {'Cap_filt':>8}")
    print(f"{'-'*65}")

    for f in folds:
        fs = f.get('filter_stats', {})
        sign = '+' if f['avg_r'] >= 0 else ' '
        print(f"{f['fold_idx']+1:>4} {f['test_start']+' '+f['test_end']:>21} "
              f"{f['n_trades']:>4} {f['win_rate']:>6.1%} {sign}{f['avg_r']:>5.2f}  "
              f"{fs.get('asian_range', 0):>7} {fs.get('regime_durability', 0):>8} "
              f"{fs.get('trade_cap', 0):>8}")

    total_trades = sum(f['n_trades'] for f in folds)
    positive = sum(1 for f in folds if f['avg_r'] > 0)
    total_r = sum(f['n_trades'] * f['avg_r'] for f in folds)
    overall_wr = data['aggregate']['win_rate']

    print(f"{'-'*65}")
    print(f"{'TOTAL':>4} {'':>21} {total_trades:>4} {overall_wr:>6.1%} "
          f"{total_r:>+6.2f}R")
    print(f"Positive folds: {positive}/{len(folds)} ({positive/len(folds):.0%})")


def filter_correlation(data: dict) -> None:
    folds = data['folds']

    days_filtered = [f.get('filter_stats', {}).get('asian_range', 0) for f in folds]
    avg_r         = [f['avg_r'] for f in folds]
    n_trades      = [f['n_trades'] for f in folds]

    if sum(days_filtered) == 0:
        print("\nFix 3 (Asian range filter) never triggered — no correlation to compute.")
        return

    corr_r      = np.corrcoef(days_filtered, avg_r)[0, 1]
    corr_trades = np.corrcoef(days_filtered, n_trades)[0, 1]

    print(f"\n{'='*65}")
    print(f"FIX 3 — ASIAN RANGE FILTER SIGNAL CHECK")
    print(f"{'='*65}")
    print(f"Total days filtered across all folds: {sum(days_filtered)}")
    print(f"Corr(days_filtered, fold_avg_r):      {corr_r:+.3f}")
    print(f"  < -0.3  → filter removes more from bad months = genuine signal")
    print(f"  ~  0.0  → filter removes indiscriminately = noise, disable it")
    print(f"Corr(days_filtered, n_trades):        {corr_trades:+.3f}")

    print(f"\nFolds where filter was most active (top 8):")
    print(f"  {'Fold':>4} {'avg_R':>7} {'days_filtered':>13}")
    ranked = sorted(zip(days_filtered, avg_r, folds), key=lambda x: x[0], reverse=True)
    for df, ar, fold in ranked[:8]:
        sign = '+' if ar >= 0 else ' '
        print(f"  {fold['fold_idx']+1:>4} {sign}{ar:>6.2f}  {df:>13}")


def trade_analysis(data: dict) -> None:
    trades = data['trades']
    if not trades:
        print("\nNo trades to analyze.")
        return

    wins   = [t for t in trades if t['r_multiple'] > 0]
    losses = [t for t in trades if t['r_multiple'] <= 0]

    print(f"\n{'='*65}")
    print(f"TRADE-LEVEL ANALYSIS")
    print(f"{'='*65}")
    print(f"Total: {len(trades)}  Wins: {len(wins)}  Losses: {len(losses)}")

    # Regime duration at entry
    if any('regime_duration' in t for t in trades):
        win_dur   = [t['regime_duration'] for t in wins   if 'regime_duration' in t]
        loss_dur  = [t['regime_duration'] for t in losses if 'regime_duration' in t]
        print(f"\nRegime duration at entry (consecutive TRENDING 1H bars):")
        if win_dur:
            print(f"  Winners  — mean: {np.mean(win_dur):.1f}  "
                  f"p25: {np.percentile(win_dur, 25):.0f}  "
                  f"p50: {np.percentile(win_dur, 50):.0f}  "
                  f"p75: {np.percentile(win_dur, 75):.0f}")
        if loss_dur:
            print(f"  Losers   — mean: {np.mean(loss_dur):.1f}  "
                  f"p25: {np.percentile(loss_dur, 25):.0f}  "
                  f"p50: {np.percentile(loss_dur, 50):.0f}  "
                  f"p75: {np.percentile(loss_dur, 75):.0f}")

        # WR by duration bucket
        print(f"\n  WR by regime duration at entry:")
        buckets = [(1, 3), (4, 7), (8, 14), (15, 999)]
        for lo, hi in buckets:
            bucket = [t for t in trades if lo <= t.get('regime_duration', 0) <= hi]
            if bucket:
                wr = sum(1 for t in bucket if t['r_multiple'] > 0) / len(bucket)
                avg = np.mean([t['r_multiple'] for t in bucket])
                print(f"    {lo:>2}-{hi if hi < 999 else '∞':>3} bars: "
                      f"{len(bucket):>4} trades | WR {wr:.1%} | Avg R {avg:+.2f}")

    # Asian range percentile at entry
    if any('asian_range_pctile' in t for t in trades):
        win_pct  = [t['asian_range_pctile'] for t in wins   if 'asian_range_pctile' in t]
        loss_pct = [t['asian_range_pctile'] for t in losses if 'asian_range_pctile' in t]
        print(f"\nAsian range percentile at entry:")
        if win_pct:
            print(f"  Winners — mean: {np.mean(win_pct):.1f}  "
                  f"p50: {np.percentile(win_pct, 50):.1f}")
        if loss_pct:
            print(f"  Losers  — mean: {np.mean(loss_pct):.1f}  "
                  f"p50: {np.percentile(loss_pct, 50):.1f}")

    # Exit reason breakdown
    print(f"\nExit reasons:")
    reasons = {}
    for t in trades:
        r = t.get('exit_reason', 'unknown')
        reasons.setdefault(r, []).append(t['r_multiple'])
    for reason, rs in sorted(reasons.items()):
        wr = sum(1 for r in rs if r > 0) / len(rs)
        print(f"  {reason:<14} {len(rs):>4} trades | WR {wr:.1%} | Avg R {np.mean(rs):+.2f}")


def compare(v_new: dict, v_old: dict, label_new: str, label_old: str) -> None:
    def agg(d):
        folds = d['folds']
        total_r = sum(f['n_trades'] * f['avg_r'] for f in folds)
        positive = sum(1 for f in folds if f['avg_r'] > 0)
        return {
            'total_trades': d['aggregate']['n_trades'],
            'win_rate': d['aggregate']['win_rate'],
            'total_r': total_r,
            'avg_r_per_trade': d['aggregate']['avg_r'],
            'positive_folds': positive,
            'n_folds': len(folds),
        }

    a = agg(v_new)
    b = agg(v_old)

    print(f"\n{'='*65}")
    print(f"VERSION COMPARISON")
    print(f"{'='*65}")
    print(f"{'Metric':<22} {label_new:>18} {label_old:>18}")
    print(f"{'-'*65}")

    def row(label, key, fmt='.2f', suffix=''):
        va, vb = a[key], b[key]
        delta = va - vb
        sign = '+' if delta >= 0 else ''
        print(f"{label:<22} {va:>18{fmt}}{suffix} {vb:>18{fmt}}{suffix}   "
              f"({sign}{delta:{fmt}}{suffix})")

    print(f"{'Trades':<22} {a['total_trades']:>18} {b['total_trades']:>18}")
    print(f"{'Win rate':<22} {a['win_rate']:>17.1%}  {b['win_rate']:>17.1%}")
    row('Avg R/trade', 'avg_r_per_trade')
    row('Total R', 'total_r')
    print(f"{'Positive folds':<22} "
          f"{a['positive_folds']}/{a['n_folds']:>14}  "
          f"{b['positive_folds']}/{b['n_folds']:>14}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('results', help='Path to strategy JSON results file')
    parser.add_argument('--compare', default=None, help='Path to previous version JSON to compare')
    args = parser.parse_args()

    data = load(args.results)

    fold_summary(data)
    filter_correlation(data)
    trade_analysis(data)

    if args.compare:
        old = load(args.compare)
        compare(data, old, label_new=args.results, label_old=args.compare)


if __name__ == '__main__':
    main()
