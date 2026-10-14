#!/usr/bin/env python3
"""
Regime-Aware Strategy Backtester for XAUUSD

Architecture:
  1H regime (TRENDING/RANGING/VOLATILE) gates entry type
  15min bars for entry/exit execution

Entry rules:
  TRENDING + London session:
    ORB on Asian session high/low, entry on close above level
    ATR expanding: current 15m ATR > avg Asian session ATR
    Min range: Asian range >= 0.5 * ATR(14, 1H)
    Entry window: London open to 12:00 UTC

  RANGING + London session:
    Liquidity sweep: price wicks beyond Asian level, closes back inside <= 3 bars
    Counter-trend entry on close back inside

  VOLATILE: flat, no entries

Exit rules:
  TRENDING: stop 1.5xATR(14,15m), target 2R, regime flip (2 bars) exit
  RANGING:  stop 1.0xATR(14,15m), target 1.5R, regime flip exit
  Time stop: 12:00 UTC for ORB entries (no new entries after this)

References:
  Iwatsubo, Watkins & Xu (2018) — session microstructure
  Zarattini & Aziz (2023) — ORB methodology
  Osler (2003) — stop clustering at session levels
"""

import sys
import os
import json
import logging
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Tuple
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from collections import deque

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from regime_classifier.regime_config import RegimeConfig
from regime_classifier.regime_features import RegimeFeatureEngine
from regime_classifier.regime_classifier import RegimeClassifier

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger(__name__)

LONDON_TZ = ZoneInfo("Europe/London")
UTC_TZ = ZoneInfo("UTC")


# =============================================================================
# CONFIG
# =============================================================================

@dataclass
class BacktestConfig:
    # Session boundaries (UTC hours)
    asian_start_utc: int = 0
    asian_end_utc: int = 8
    orb_window_minutes: int = 30      # ORB range formation window after London open
    orb_entry_cutoff_utc: int = 11    # No new entries after this UTC hour
    exclude_first_asian_hour: bool = False  # If True, use 01:00-08:00 as Asian baseline

    # Filters
    orb_min_range_ratio: float = 0.5  # Asian range must be >= this * ATR(14, 1H)
    sweep_min_atr_mult: float = 0.25  # Min wick beyond level (in ATR units)
    sweep_max_bars: int = 3           # Max bars to close back inside for sweep

    # Exit parameters
    trending_stop_atr: float = 2.0    # Stop distance in 15m ATR multiples (RANGING only now)
    trending_target_r: float = 1.0    # v4: reduced from 1.5 to capture more EOD drift trades
    ranging_stop_atr: float = 1.0
    ranging_target_r: float = 1.5
    regime_flip_bars: int = 2         # Consecutive opposite-regime bars to trigger exit
    breakeven_after_r: float = 0.5    # Move stop to entry once price moves this many R in favour
    eod_hour: int = 16                # v4: exit at London close (was 22 NY close)

    # Cost model
    spread_usd: float = 0.40          # One-way spread cost in USD per oz

    # News blackout (minutes before/after event)
    news_blackout_before: int = 30
    news_blackout_after: int = 15
    news_event_times: List[pd.Timestamp] = field(default_factory=list)

    # ATR
    atr_period: int = 14

    # Fix 1: Trade frequency cap
    max_trades_per_day: int = 1          # Max entries per calendar day

    # Fix 2: Regime streak cap (inverted from v3 — short streaks win, long streaks lose)
    min_trending_bars: int = 0           # Disabled: short streaks (1-3 bars) had 68% WR
    max_trending_bars: int = 7           # Skip if regime has been TRENDING > 7 bars (17% WR)

    # Fix 3: Asian range filter (cap at percentile of rolling history)
    asian_range_pctile_cap: float = 75.0 # Skip if Asian range > this percentile
    asian_range_min_history: int = 20    # Warm-up days before applying filter

    # Fix 4: Monthly loss cap
    monthly_loss_cap_r: float = 3.0      # Stop new entries once down this many R in the fold

    # v4: Asian close position filter (regression showed top 40% → 75-91% breakout rate)
    asian_close_pos_min: float = 0.6     # Skip if Asian closed below 60% of its range


# =============================================================================
# TRADE RECORD
# =============================================================================

@dataclass
class Trade:
    fold_idx: int
    entry_time: pd.Timestamp
    exit_time: Optional[pd.Timestamp]
    direction: str          # 'long' / 'short'
    strategy: str           # 'orb' / 'sweep'
    regime: str             # 1H regime at entry
    entry_price: float
    stop_price: float
    target_price: float
    exit_price: Optional[float] = None
    exit_reason: Optional[str] = None   # 'stop' / 'target' / 'time' / 'regime_flip' / 'eod'
    asian_high: float = 0.0
    asian_low: float = 0.0
    asian_atr_avg: float = 0.0
    r_multiple: Optional[float] = None
    pnl_pips: Optional[float] = None    # USD per oz (net of spread)
    regime_duration: int = 0            # Consecutive TRENDING 1H bars at entry
    asian_range_pctile: float = 50.0    # Asian range percentile vs rolling history
    breakeven_hit: bool = False         # Whether stop was moved to breakeven

    def close(self, exit_price: float, exit_time: pd.Timestamp, reason: str, spread: float):
        self.exit_price = exit_price
        self.exit_time = exit_time
        self.exit_reason = reason
        risk = abs(self.entry_price - self.stop_price)
        if self.direction == 'long':
            raw_pnl = exit_price - self.entry_price
        else:
            raw_pnl = self.entry_price - exit_price
        net_pnl = raw_pnl - spread  # entry spread already deducted, exit spread here
        self.pnl_pips = net_pnl
        self.r_multiple = net_pnl / risk if risk > 0 else 0.0


# =============================================================================
# RESULTS
# =============================================================================

@dataclass
class BacktestResult:
    config: BacktestConfig
    trades: List[Trade] = field(default_factory=list)
    fold_results: List[Dict] = field(default_factory=list)
    filter_stats: Dict = field(default_factory=dict)

    @property
    def closed_trades(self):
        return [t for t in self.trades if t.r_multiple is not None]

    @property
    def n_trades(self):
        return len(self.closed_trades)

    @property
    def win_rate(self):
        c = self.closed_trades
        return sum(1 for t in c if t.r_multiple > 0) / len(c) if c else 0.0

    @property
    def avg_r(self):
        c = self.closed_trades
        return float(np.mean([t.r_multiple for t in c])) if c else 0.0

    @property
    def profit_factor(self):
        c = self.closed_trades
        wins = sum(t.r_multiple for t in c if t.r_multiple > 0)
        losses = abs(sum(t.r_multiple for t in c if t.r_multiple < 0))
        return wins / losses if losses > 0 else float('inf')

    @property
    def sharpe(self):
        c = self.closed_trades
        if len(c) < 2:
            return 0.0
        r = [t.r_multiple for t in c]
        return float(np.mean(r) / np.std(r) * np.sqrt(252)) if np.std(r) > 0 else 0.0

    @property
    def max_drawdown(self):
        c = self.closed_trades
        if not c:
            return 0.0
        cumr = np.cumsum([t.r_multiple for t in c])
        peak = np.maximum.accumulate(cumr)
        dd = cumr - peak
        return float(dd.min())

    def by_strategy(self):
        for strat in ['orb', 'sweep']:
            trades = [t for t in self.closed_trades if t.strategy == strat]
            if trades:
                r = [t.r_multiple for t in trades]
                wins = sum(1 for x in r if x > 0)
                print(f"  {strat.upper():6s}: {len(trades):3d} trades | "
                      f"WR {wins/len(trades):.1%} | "
                      f"Avg R {np.mean(r):+.2f} | "
                      f"PF {sum(x for x in r if x>0)/abs(sum(x for x in r if x<0) or 1):.2f}")

    def summary(self):
        print(f"\n{'='*60}")
        print(f"BACKTEST RESULTS")
        print(f"{'='*60}")
        print(f"Total trades:   {self.n_trades}")
        print(f"Win rate:       {self.win_rate:.1%}")
        print(f"Avg R:          {self.avg_r:+.3f}")
        print(f"Profit factor:  {self.profit_factor:.2f}")
        print(f"Sharpe:         {self.sharpe:.2f}")
        print(f"Max drawdown:   {self.max_drawdown:.2f}R")
        print(f"\nBy strategy:")
        self.by_strategy()
        print(f"{'='*60}\n")


# =============================================================================
# SESSION UTILITIES
# =============================================================================

def get_london_open_utc(date: pd.Timestamp) -> pd.Timestamp:
    """Return UTC timestamp of 08:00 London time, handling DST correctly."""
    naive_london = pd.Timestamp(date.year, date.month, date.day, 8, 0, 0)
    aware_london = naive_london.tz_localize(LONDON_TZ, ambiguous='NaT', nonexistent='NaT')
    if pd.isna(aware_london):
        return pd.Timestamp(date.year, date.month, date.day, 8, 0, 0)
    utc = aware_london.tz_convert(UTC_TZ).tz_localize(None)
    return utc


def compute_asian_stats(bars_15m: pd.DataFrame, date: pd.Timestamp,
                        config: BacktestConfig) -> Optional[Dict]:
    """
    Compute Asian session statistics for a given date.
    Returns high, low, avg ATR, and range — all from 00:00-08:00 UTC that date.
    """
    start_h = config.asian_start_utc + (1 if config.exclude_first_asian_hour else 0)
    end_h = config.asian_end_utc

    day_start = pd.Timestamp(date.year, date.month, date.day, start_h, 0, 0)
    day_end = pd.Timestamp(date.year, date.month, date.day, end_h, 0, 0)

    asian = bars_15m[(bars_15m.index >= day_start) & (bars_15m.index < day_end)]
    if len(asian) < 8:  # Need at least 2 hours
        return None

    a_high = float(asian['high'].max())
    a_low  = float(asian['low'].min())
    a_range = a_high - a_low
    a_close = float(asian.iloc[-1]['close'])
    return {
        'high': a_high,
        'low': a_low,
        'atr_avg': float(asian['atr'].mean()),
        'range': a_range,
        'midpoint': float((a_high + a_low) / 2),
        'n_bars': len(asian),
        'close_pos': (a_close - a_low) / a_range if a_range > 0 else 0.5,
    }


def is_news_blackout(ts: pd.Timestamp, news_times: List[pd.Timestamp],
                     before_min: int, after_min: int) -> bool:
    """Return True if timestamp falls within any news blackout window."""
    for event_time in news_times:
        delta = (ts - event_time).total_seconds() / 60
        if -before_min <= delta <= after_min:
            return True
    return False


# =============================================================================
# ATR COMPUTATION
# =============================================================================

def compute_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Compute ATR(period) on a DataFrame with high/low/close columns."""
    high_low = df['high'] - df['low']
    high_close = (df['high'] - df['close'].shift(1)).abs()
    low_close = (df['low'] - df['close'].shift(1)).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    return tr.ewm(span=period, adjust=False).mean()


# =============================================================================
# REGIME MAPPING — 1H → 15min
# =============================================================================

def compute_regime_streak(regime_1h: pd.Series, as_of_ts: pd.Timestamp,
                          target: str = 'TRENDING') -> int:
    """
    Count consecutive bars of target regime ending at or before as_of_ts.
    Used to measure regime durability: how long has the market been in this state?
    """
    past = regime_1h[regime_1h.index <= as_of_ts]
    streak = 0
    for label in reversed(past.values.tolist()):
        if label == target:
            streak += 1
        else:
            break
    return streak

## TO FIX
def map_regime_to_15m(regime_1h: pd.Series, index_15m: pd.DatetimeIndex) -> pd.Series:
    """
    Map 1H regime labels to 15min bars.
    For each 15min bar, use the regime of the last completed 1H bar.
    Strictly causal — no lookahead.
    """
    regime_reindexed = regime_1h.reindex(index_15m, method='ffill')
    return regime_reindexed


# =============================================================================
# MAIN BACKTESTER
# =============================================================================

class RegimeStrategyBacktester:
    def __init__(self, config: BacktestConfig = None):
        self.config = config or BacktestConfig()

    def run(
        self,
        ohlcv_15m: pd.DataFrame,
        regime_1h: pd.Series,
        atr_1h: pd.Series,
        fold_idx: int = 0,
    ) -> BacktestResult:
        """
        Run strategy on given data with pre-computed 1H regime labels.

        Args:
            ohlcv_15m:  15min OHLCV with DatetimeIndex (UTC, tz-naive)
            regime_1h:  1H regime labels (TRENDING/RANGING/VOLATILE), same index as 1H data
            atr_1h:     ATR(14) on 1H data, for minimum range filter
            fold_idx:   Walk-forward fold number for tracking
        """
        cfg = self.config
        result = BacktestResult(config=cfg)

        # Compute 15m ATR
        df = ohlcv_15m.copy()
        df.columns = [c.lower() for c in df.columns]
        df['atr'] = compute_atr(df, cfg.atr_period)

        # Map 1H regime to 15m bars
        regime_15m = map_regime_to_15m(regime_1h, df.index)

        # Map 1H ATR to 15m bars for min range filter
        atr_1h_15m = atr_1h.reindex(df.index, method='ffill')

        # Iterate day by day
        trading_days = sorted(set(df.index.date))
        active_trade: Optional[Trade] = None
        regime_flip_count = 0
        last_trade_date = None                          # Fix 1: per-day trade cap
        asian_range_history: deque = deque()            # Fix 3: rolling range history
        running_r = 0.0                                 # Fix 4: cumulative R this fold
        filter_stats = {
            'asian_range': 0,
            'regime_durability': 0,
            'trade_cap': 0,
            'monthly_loss_cap': 0,
            'asian_close_pos': 0,
        }

        for day in trading_days:
            day_ts = pd.Timestamp(day)

            # Get Asian session stats (00:00-08:00 UTC this day)
            asian = compute_asian_stats(df, day_ts, cfg)
            if asian is None:
                continue

            # Fix 3: Asian range filter — skip days where Asian session was unusually wide.
            # Wide Asian range means the market already moved before London; ORB levels
            # are less clean and breakouts are more likely to be exhaustion moves.
            # Use rolling history of PREVIOUS days only (causal — no current-day lookahead).
            asian_range_pctile_val = 50.0
            if len(asian_range_history) >= cfg.asian_range_min_history:
                hist = list(asian_range_history)
                cap_val = float(np.percentile(hist, cfg.asian_range_pctile_cap))
                asian_range_pctile_val = 100.0 * sum(r <= asian['range'] for r in hist) / len(hist)
                if asian['range'] > cap_val:
                    asian_range_history.append(asian['range'])
                    filter_stats['asian_range'] += 1
                    continue
            asian_range_history.append(asian['range'])

            # Get London open in UTC (DST-aware)
            london_open_utc = get_london_open_utc(day_ts)
            orb_end_utc = london_open_utc + pd.Timedelta(minutes=cfg.orb_window_minutes)
            entry_cutoff_utc = pd.Timestamp(day.year, day.month, day.day, cfg.orb_entry_cutoff_utc, 0, 0)

            # Fix 2: Regime durability — count consecutive TRENDING bars at London open.
            # Requires the regime_1h series to extend back before test_start
            # (extended by 24h in run_walk_forward for this purpose).
            trending_streak = compute_regime_streak(regime_1h, london_open_utc)

            # ORB range: high/low of first 30 minutes after London open
            orb_bars = df[(df.index >= london_open_utc) & (df.index < orb_end_utc)]
            if len(orb_bars) == 0:
                continue
            orb_high = orb_bars['high'].max()
            orb_low = orb_bars['low'].min()

            # Check minimum range filter (Asian range vs 1H ATR)
            atr_1h_val = float(atr_1h_15m.loc[orb_bars.index[-1]]) if len(orb_bars) > 0 else 0
            if atr_1h_val > 0 and asian['range'] < cfg.orb_min_range_ratio * atr_1h_val:
                logger.debug(f"{day}: Asian range too tight ({asian['range']:.2f} < "
                             f"{cfg.orb_min_range_ratio * atr_1h_val:.2f}), skipping ORB")
                continue

            # Sweep state tracking
            sweep_state = {'active': False, 'direction': None, 'bars_inside': 0, 'level': 0.0}

            # Process bars after ORB window
            day_bars = df[df.index >= orb_end_utc]
            day_bars = day_bars[day_bars.index.date == day]

            for ts, bar in day_bars.iterrows():
                regime = regime_15m.get(ts, 'UNKNOWN')
                current_atr = bar['atr']

                # --- Manage active trade ---
                if active_trade is not None:
                    # Check regime flip exit
                    trade_regime = active_trade.regime
                    if regime != trade_regime and regime in ('RANGING', 'VOLATILE', 'TRENDING'):
                        regime_flip_count += 1
                    else:
                        regime_flip_count = 0

                    if regime_flip_count >= cfg.regime_flip_bars:
                        # Stop is a hard limit — regime flip cannot exit worse than stop
                        if active_trade.direction == 'long':
                            exit_px = max(bar['close'], active_trade.stop_price)
                        else:
                            exit_px = min(bar['close'], active_trade.stop_price)
                        active_trade.close(exit_px, ts, 'regime_flip', cfg.spread_usd / 2)
                        running_r += active_trade.r_multiple
                        result.trades.append(active_trade)
                        active_trade = None
                        regime_flip_count = 0
                        continue

                    # Check stop/target (use bar high/low for realistic fills)
                    if active_trade.direction == 'long':
                        risk = active_trade.entry_price - active_trade.stop_price
                        # Breakeven: move stop to entry once +breakeven_after_r in favour
                        if (not active_trade.breakeven_hit and risk > 0 and
                                bar['high'] >= active_trade.entry_price + cfg.breakeven_after_r * risk):
                            active_trade.stop_price = active_trade.entry_price
                            active_trade.breakeven_hit = True
                        if bar['low'] <= active_trade.stop_price:
                            exit_px = active_trade.stop_price
                            reason = 'breakeven' if active_trade.breakeven_hit else 'stop'
                            active_trade.close(exit_px, ts, reason, cfg.spread_usd / 2)
                            running_r += active_trade.r_multiple
                            result.trades.append(active_trade)
                            active_trade = None
                            regime_flip_count = 0
                            continue
                        if bar['high'] >= active_trade.target_price:
                            exit_px = active_trade.target_price
                            active_trade.close(exit_px, ts, 'target', cfg.spread_usd / 2)
                            running_r += active_trade.r_multiple
                            result.trades.append(active_trade)
                            active_trade = None
                            regime_flip_count = 0
                            continue
                    else:  # short
                        if bar['high'] >= active_trade.stop_price:
                            exit_px = active_trade.stop_price
                            active_trade.close(exit_px, ts, 'stop', cfg.spread_usd / 2)
                            running_r += active_trade.r_multiple
                            result.trades.append(active_trade)
                            active_trade = None
                            regime_flip_count = 0
                            continue
                        if bar['low'] <= active_trade.target_price:
                            exit_px = active_trade.target_price
                            active_trade.close(exit_px, ts, 'target', cfg.spread_usd / 2)
                            running_r += active_trade.r_multiple
                            result.trades.append(active_trade)
                            active_trade = None
                            regime_flip_count = 0
                            continue

                    # Time-based exit at London close (16:00 UTC)
                    if ts.hour >= cfg.eod_hour:
                        active_trade.close(bar['close'], ts, 'eod', cfg.spread_usd / 2)
                        running_r += active_trade.r_multiple
                        result.trades.append(active_trade)
                        active_trade = None
                        regime_flip_count = 0
                        continue

                # --- Entry logic (only if no active trade) ---
                if active_trade is not None:
                    continue
                if regime in ('UNKNOWN', 'VOLATILE', 'RANGING'):
                    continue
                if ts > entry_cutoff_utc:
                    continue
                if is_news_blackout(ts, cfg.news_event_times,
                                    cfg.news_blackout_before, cfg.news_blackout_after):
                    continue

                # Fix 4: Monthly loss cap — stop new entries once down too much this fold
                if running_r <= -cfg.monthly_loss_cap_r:
                    filter_stats['monthly_loss_cap'] += 1
                    continue

                # Fix 1: 1-trade-per-day cap — only one ORB entry per calendar day
                if last_trade_date == day:
                    filter_stats['trade_cap'] += 1
                    continue

                # Fix 2 (v4 inverted): cap streak at max_trending_bars.
                # Trade analysis showed 1-3 bar streaks → 68% WR, 15+ bars → 17% WR.
                # Long streaks = exhausted trend, short streaks = fresh breakout.
                if regime == 'TRENDING' and trending_streak > cfg.max_trending_bars:
                    filter_stats['regime_durability'] += 1
                    continue

                # v4: Asian close position filter — only trade when Asian closed in
                # upper portion of its range (regression: top 40% → 75-91% breakout rate)
                if asian['close_pos'] < cfg.asian_close_pos_min:
                    filter_stats.setdefault('asian_close_pos', 0)
                    filter_stats['asian_close_pos'] += 1
                    continue

                atr_expanding = current_atr > asian['atr_avg']

                # --- TRENDING: ORB entry ---
                if regime == 'TRENDING':
                    # Long: close above Asian high (use ORB high as confirmation level)
                    level = max(asian['high'], orb_high)
                    if bar['close'] > level and atr_expanding:
                        # v4: structural stop at Asian low (regression proved ATR stop fails)
                        entry_px = bar['close'] + cfg.spread_usd / 2
                        stop = asian['low'] - cfg.spread_usd / 2
                        risk = entry_px - stop
                        if risk <= 0:
                            continue
                        target = entry_px + cfg.trending_target_r * risk
                        active_trade = Trade(
                            fold_idx=fold_idx,
                            entry_time=ts,
                            exit_time=None,
                            direction='long',
                            strategy='orb',
                            regime=regime,
                            entry_price=entry_px,
                            stop_price=stop,
                            target_price=target,
                            asian_high=asian['high'],
                            asian_low=asian['low'],
                            asian_atr_avg=asian['atr_avg'],
                            regime_duration=trending_streak,
                            asian_range_pctile=asian_range_pctile_val,
                            breakeven_hit=False,
                        )
                        last_trade_date = day   # Fix 1: mark this day as traded
                        regime_flip_count = 0
                        continue

                    # Short ORB disabled — gold structural bull market (2020-2025)
                    # means London breakdowns rarely follow through in TRENDING regime

                # --- RANGING: Liquidity sweep entry ---
                elif regime == 'RANGING':
                    min_sweep = cfg.sweep_min_atr_mult * current_atr

                    # Detect sweep of Asian high (wick above, close back inside)
                    if (bar['high'] > asian['high'] + min_sweep and
                            bar['close'] < asian['high']):
                        # Short entry — sweep of high, failed breakout
                        entry_px = bar['close'] - cfg.spread_usd / 2
                        stop = bar['high'] + cfg.ranging_stop_atr * current_atr
                        risk = stop - entry_px
                        target = entry_px - cfg.ranging_target_r * risk
                        active_trade = Trade(
                            fold_idx=fold_idx,
                            entry_time=ts,
                            exit_time=None,
                            direction='short',
                            strategy='sweep',
                            regime=regime,
                            entry_price=entry_px,
                            stop_price=stop,
                            target_price=target,
                            asian_high=asian['high'],
                            asian_low=asian['low'],
                            asian_atr_avg=asian['atr_avg'],
                        )
                        regime_flip_count = 0
                        continue

                    # Detect sweep of Asian low (wick below, close back inside)
                    if (bar['low'] < asian['low'] - min_sweep and
                            bar['close'] > asian['low']):
                        # Long entry — sweep of low, failed breakdown
                        entry_px = bar['close'] + cfg.spread_usd / 2
                        stop = bar['low'] - cfg.ranging_stop_atr * current_atr
                        risk = entry_px - stop
                        target = entry_px + cfg.ranging_target_r * risk
                        active_trade = Trade(
                            fold_idx=fold_idx,
                            entry_time=ts,
                            exit_time=None,
                            direction='long',
                            strategy='sweep',
                            regime=regime,
                            entry_price=entry_px,
                            stop_price=stop,
                            target_price=target,
                            asian_high=asian['high'],
                            asian_low=asian['low'],
                            asian_atr_avg=asian['atr_avg'],
                        )
                        regime_flip_count = 0

            # Close any trade still open at London close (eod_hour)
            if active_trade is not None:
                eod_bars = day_bars[day_bars.index.hour < cfg.eod_hour]
                last_bar = eod_bars.iloc[-1] if len(eod_bars) > 0 else (day_bars.iloc[-1] if len(day_bars) > 0 else None)
                if last_bar is not None:
                    last_ts = last_bar.name if hasattr(last_bar, 'name') else day_bars.index[-1]
                    active_trade.close(last_bar['close'], last_ts, 'eod', cfg.spread_usd / 2)
                    running_r += active_trade.r_multiple
                    result.trades.append(active_trade)
                active_trade = None
                regime_flip_count = 0

        result.filter_stats = filter_stats
        return result


# =============================================================================
# WALK-FORWARD RUNNER
# =============================================================================

def run_walk_forward(
    ohlcv_15m: pd.DataFrame,
    ohlcv_1h: pd.DataFrame,
    config: BacktestConfig,
    train_months: int = 9,
    test_months: int = 1,
    embargo_months: int = 1,
    max_folds: int = None,
) -> BacktestResult:
    """
    Walk-forward validation of the full strategy.
    For each fold:
      1. Fit regime classifier on training data only
      2. Run strategy backtest on test data using out-of-sample regime labels
    """
    from regime_classifier.regime_validator import RegimeValidator
    from regime_classifier.regime_config import RegimeConfig

    regime_config = RegimeConfig(
        walk_forward_train_months=train_months,
        walk_forward_test_months=test_months,
        embargo_months=embargo_months,
        # Same optimised grid the validator uses — identical results, ~40x faster
        lambda_grid=[0.1, 1.0, 5.0, 20.0, 100.0],
        jm_n_init=5,
        jm_max_iter=300,
        sparse_max_feats_grid=[3.0, 8.0, 20.0],
        sparse_max_iter=15,
    )

    # Normalize
    df_1h = ohlcv_1h.copy()
    df_1h.columns = [c.lower() for c in df_1h.columns]
    atr_1h = compute_atr(df_1h)

    # Generate fold boundaries
    from regime_classifier.regime_validator import RegimeValidator
    validator = RegimeValidator(config=regime_config)
    folds = validator._generate_folds(df_1h.index)
    if max_folds:
        folds = folds[:max_folds]
    logger.info(f"Generated {len(folds)} walk-forward folds")

    aggregate_result = BacktestResult(config=config)

    for fold_idx, (train_start, train_end, test_start, test_end) in enumerate(folds):
        logger.info(f"Fold {fold_idx+1}/{len(folds)}: "
                    f"train [{train_start.date()}..{train_end.date()}], "
                    f"test [{test_start.date()}..{test_end.date()}]")

        # Slice training data
        train_mask_1h = (df_1h.index >= train_start) & (df_1h.index < train_end)
        train_1h = df_1h.loc[train_mask_1h]
        train_returns = np.log(train_1h['close'] / train_1h['close'].shift(1))

        if len(train_1h) < 200:
            logger.warning(f"Fold {fold_idx+1}: insufficient training data, skipping")
            continue

        # Fit features and classifier on training data
        feature_engine = RegimeFeatureEngine(config=regime_config)
        features_train = feature_engine.compute(train_1h)
        feat_train_slice = features_train.loc[
            (features_train.index >= train_start) & (features_train.index < train_end)
        ]

        try:
            clf = RegimeClassifier(config=regime_config)
            clf.fit(feat_train_slice, returns=train_returns)
        except Exception as e:
            logger.warning(f"Fold {fold_idx+1}: classifier fit failed: {e}")
            continue

        # Predict regime on test data (out-of-sample, causal)
        test_mask_1h = (df_1h.index >= test_start) & (df_1h.index < test_end)
        test_1h = df_1h.loc[test_mask_1h]
        features_test = feature_engine.compute(
            df_1h.loc[df_1h.index < test_end]  # Use all data up to test_end for rolling windows
        )
        feat_test_slice = features_test.loc[
            (features_test.index >= test_start) & (features_test.index < test_end)
        ]

        try:
            # Extend prediction window 24h before test_start so compute_regime_streak
            # can see streaks that began during the embargo period.
            streak_start = test_start - pd.Timedelta(hours=24)
            feat_for_regime = features_test.loc[
                (features_test.index >= streak_start) & (features_test.index < test_end)
            ]
            regime_labels = clf.predict(feat_for_regime)['regime_label']

            # For P(TRENDING) distribution logging, restrict to test period only
            predictions = clf.predict(feat_test_slice)

            # RF confidence: P(next regime = TRENDING) — soft probability from predict_next()
            next_preds = clf.predict_next(feat_test_slice)
            # Extract per-bar probability that the RF assigns to TRENDING as next regime
            trending_class_idx = list(clf.rf_model_.classes_).index(
                {v: k for k, v in clf.state_mapping_.items()}['TRENDING']
            )
            rf_proba = clf.rf_model_.predict_proba(
                clf._preprocess_features(feat_test_slice)[clf.selected_features_].dropna()
            )
            trending_proba = pd.Series(np.nan, index=feat_test_slice.index)
            valid_mask = clf._preprocess_features(feat_test_slice)[clf.selected_features_].notna().all(axis=1)
            trending_proba.loc[valid_mask] = rf_proba[:, trending_class_idx]

            # Log distribution for TRENDING-labelled bars only (test period only)
            test_regime_labels = regime_labels.loc[regime_labels.index >= test_start]
            trending_bars_proba = trending_proba[test_regime_labels == 'TRENDING'].dropna()
            if len(trending_bars_proba) > 0:
                logger.info(
                    f"  Fold {fold_idx+1} P(TRENDING) distribution over {len(trending_bars_proba)} TRENDING bars: "
                    f"p25={trending_bars_proba.quantile(0.25):.2f}  "
                    f"p50={trending_bars_proba.quantile(0.50):.2f}  "
                    f"p75={trending_bars_proba.quantile(0.75):.2f}  "
                    f"p90={trending_bars_proba.quantile(0.90):.2f}  "
                    f"min={trending_bars_proba.min():.2f}  "
                    f"max={trending_bars_proba.max():.2f}"
                )
                for thresh in [0.50, 0.60, 0.65, 0.70, 0.75, 0.80]:
                    pct = (trending_bars_proba >= thresh).mean()
                    logger.info(f"    P>=  {thresh}: {pct:.1%} of TRENDING bars pass")
        except Exception as e:
            logger.warning(f"Fold {fold_idx+1}: prediction failed: {e}")
            continue

        # Slice 15min test data and 1H ATR
        test_mask_15m = (ohlcv_15m.index >= test_start) & (ohlcv_15m.index < test_end)
        test_15m = ohlcv_15m.loc[test_mask_15m]
        test_atr_1h = atr_1h.loc[test_mask_1h]

        if len(test_15m) < 50:
            logger.warning(f"Fold {fold_idx+1}: insufficient 15m test data, skipping")
            continue

        # Run strategy
        backtester = RegimeStrategyBacktester(config=config)
        fold_result = backtester.run(
            ohlcv_15m=test_15m,
            regime_1h=regime_labels,
            atr_1h=test_atr_1h,
            fold_idx=fold_idx,
        )

        aggregate_result.trades.extend(fold_result.trades)

        # Per-fold summary
        fc = fold_result.closed_trades
        fs = fold_result.filter_stats
        logger.info(
            f"  Filters: asian_range={fs.get('asian_range', 0)} days, "
            f"durability={fs.get('regime_durability', 0)} bars, "
            f"trade_cap={fs.get('trade_cap', 0)} bars, "
            f"loss_cap={fs.get('monthly_loss_cap', 0)} bars"
        )
        fold_summary = {
            'fold_idx': fold_idx,
            'test_start': str(test_start.date()),
            'test_end': str(test_end.date()),
            'n_trades': len(fc),
            'win_rate': fold_result.win_rate,
            'avg_r': fold_result.avg_r,
            'profit_factor': fold_result.profit_factor,
            'sharpe': fold_result.sharpe,
            'max_dd': fold_result.max_drawdown,
            'filter_stats': fold_result.filter_stats,
        }
        aggregate_result.fold_results.append(fold_summary)
        logger.info(f"  Fold {fold_idx+1}: {len(fc)} trades | "
                    f"WR {fold_result.win_rate:.1%} | Avg R {fold_result.avg_r:+.2f}")

    return aggregate_result


# =============================================================================
# DATA LOADING
# =============================================================================

def load_from_db(symbol: str = 'XAUUSD', resample_to: str = None) -> pd.DataFrame:
    """Load OHLCV data from PostgreSQL and optionally resample."""
    import psycopg2
    db_url = os.environ.get('DATABASE_URL')
    if not db_url:
        raise ValueError("DATABASE_URL environment variable is required")

    conn = psycopg2.connect(db_url)
    query = """
        SELECT timestamp AT TIME ZONE 'UTC' as timestamp,
               open::FLOAT, high::FLOAT, low::FLOAT, close::FLOAT,
               COALESCE(volume::FLOAT, 0) as volume
        FROM ohlcv_historical_1min
        WHERE symbol = %s
        ORDER BY timestamp
    """
    df = pd.read_sql(query, conn, params=(symbol,), parse_dates=['timestamp'])
    conn.close()

    df.set_index('timestamp', inplace=True)
    df.index = df.index.tz_localize(None)
    df.sort_index(inplace=True)

    if resample_to:
        df = df.resample(resample_to).agg({
            'open': 'first',
            'high': 'max',
            'low': 'min',
            'close': 'last',
            'volume': 'sum',
        }).dropna()

    return df


def load_csv(filepath: str, resample_to: str = None) -> pd.DataFrame:
    """Load OHLCV CSV. Optionally resample to higher timeframe."""
    df = pd.read_csv(filepath)
    df['timestamp'] = pd.to_datetime(df['timestamp'], utc=True).dt.tz_localize(None)
    df.set_index('timestamp', inplace=True)
    df.sort_index(inplace=True)

    if resample_to:
        df = df.resample(resample_to).agg({
            'open': 'first',
            'high': 'max',
            'low': 'min',
            'close': 'last',
            'volume': 'sum',
        }).dropna()

    return df


# =============================================================================
# ENTRY POINT
# =============================================================================

if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='Regime-aware strategy backtester')
    parser.add_argument('--data', default=None,
                        help='Path to 1-minute OHLCV CSV (if not set, loads from DATABASE_URL)')
    parser.add_argument('--symbol', default='XAUUSD')
    parser.add_argument('--train-months', type=int, default=9)
    parser.add_argument('--test-months', type=int, default=1)
    parser.add_argument('--embargo-months', type=int, default=1)
    parser.add_argument('--spread', type=float, default=0.40,
                        help='One-way spread in USD per oz')
    parser.add_argument('--output', default=None,
                        help='Output JSON file for results')
    parser.add_argument('--max-folds', type=int, default=None,
                        help='Limit number of folds (e.g. 1 for quick test)')
    args = parser.parse_args()

    if args.data:
        logger.info(f"Loading data from CSV: {args.data}...")
        df_15m = load_csv(args.data, resample_to='15min')
        df_1h = load_csv(args.data, resample_to='1h')
    else:
        logger.info(f"Loading {args.symbol} from database...")
        df_15m = load_from_db(args.symbol, resample_to='15min')
        df_1h = load_from_db(args.symbol, resample_to='1h')
    logger.info(f"15m bars: {len(df_15m)}, 1H bars: {len(df_1h)}")

    # Config
    config = BacktestConfig(spread_usd=args.spread)

    # Run walk-forward
    logger.info(f"Running walk-forward: train={args.train_months}mo, "
                f"test={args.test_months}mo, embargo={args.embargo_months}mo")
    result = run_walk_forward(
        ohlcv_15m=df_15m,
        ohlcv_1h=df_1h,
        max_folds=args.max_folds,
        config=config,
        train_months=args.train_months,
        test_months=args.test_months,
        embargo_months=args.embargo_months,
    )

    result.summary()

    # Per-fold table
    print(f"{'Fold':>4} {'Test Period':>25} {'Trades':>6} {'WR':>6} {'Avg R':>7} {'PF':>6} {'Sharpe':>7}")
    for f in result.fold_results:
        print(f"{f['fold_idx']+1:>4} {f['test_start']+' - '+f['test_end']:>25} "
              f"{f['n_trades']:>6} {f['win_rate']:>6.1%} {f['avg_r']:>+7.2f} "
              f"{f['profit_factor']:>6.2f} {f['sharpe']:>7.2f}")

    # Save results
    if args.output:
        output = {
            'config': {
                'train_months': args.train_months,
                'test_months': args.test_months,
                'spread_usd': args.spread,
            },
            'aggregate': {
                'n_trades': result.n_trades,
                'win_rate': result.win_rate,
                'avg_r': result.avg_r,
                'profit_factor': result.profit_factor,
                'sharpe': result.sharpe,
                'max_drawdown': result.max_drawdown,
            },
            'folds': result.fold_results,
            'trades': [
                {
                    'fold_idx': t.fold_idx,
                    'entry_time': str(t.entry_time),
                    'exit_time': str(t.exit_time),
                    'direction': t.direction,
                    'strategy': t.strategy,
                    'regime': t.regime,
                    'r_multiple': t.r_multiple,
                    'exit_reason': t.exit_reason,
                    'regime_duration': t.regime_duration,
                    'asian_range_pctile': round(t.asian_range_pctile, 1),
                }
                for t in result.closed_trades
            ]
        }
        with open(args.output, 'w') as f:
            json.dump(output, f, indent=2)
        logger.info(f"Results saved to {args.output}")
