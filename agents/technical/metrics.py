"""The Technical metric table (docs/AGENTS_ARCHITECTURE.md §4.3;
docs/PROMPT_GUIDE.md §4.3). One table, computed in code, shared by the
Technical-LLM (as {{metrics_table}}) and by the deterministic Technical v1
agent (agents v1), which reads snapshot.metrics.

- **Complete candles only.** The forming candle never feeds a metric.
- **Wilder smoothing** for ATR and RSI (agents v1 design, owner decision).
  The seed is the simple mean of the first n values.
- **EMA** with alpha = 2 / (n + 1), seeded with the simple mean of the first
  n values.
- **Never imputed.** A metric that can't be computed (too few candles, no σ,
  a flat series for RSI, no volume source) has value None, available False
  and the reason.

    metric_table(bars, features, features_reason, levels) -> tuple[MetricRow, ...]
"""

from __future__ import annotations

import math
from typing import Mapping, Sequence

from agents.schemas import Bar, Level, MetricRow

TIMEFRAMES = ("15m", "1h", "4h")
ATR_N = RSI_N = 14
EMA_FAST, EMA_SLOW = 9, 21
NO_VOLUME = "no volume source: the bars are a CFD bid/ask midpoint; needs a futures-bars source"


def complete(series: Sequence[Bar]) -> list[Bar]:
    return [b for b in series if b.complete]


def wilder_atr(bars: Sequence[Bar], n: int = ATR_N) -> float | str:
    """ATR(n), Wilder: TR_i = max(H - L, |H - C_{i-1}|, |L - C_{i-1}|) from the
    second bar on. The seed is the mean of the first n TRs, then
    ATR = (ATR · (n - 1) + TR) / n. Needs n + 1 bars."""
    if len(bars) < n + 1:
        return f"too few complete candles ({len(bars)}, need {n + 1})"
    tr = [max(b.high - b.low, abs(b.high - p.close), abs(b.low - p.close)) for p, b in zip(bars, bars[1:])]
    atr = sum(tr[:n]) / n
    for x in tr[n:]:
        atr = (atr * (n - 1) + x) / n
    return atr


def wilder_rsi(closes: Sequence[float], n: int = RSI_N) -> float | str:
    """RSI(n), Wilder: average gain and loss seeded with the mean of the first n
    changes, then smoothed as (avg · (n - 1) + x) / n. No losses: 100. No
    gains and no losses (a flat series): not defined. Needs n + 1 closes."""
    if len(closes) < n + 1:
        return f"too few complete candles ({len(closes)}, need {n + 1})"
    d = [b - a for a, b in zip(closes, closes[1:])]
    gain = sum(max(x, 0.0) for x in d[:n]) / n
    loss = sum(max(-x, 0.0) for x in d[:n]) / n
    for x in d[n:]:
        gain = (gain * (n - 1) + max(x, 0.0)) / n
        loss = (loss * (n - 1) + max(-x, 0.0)) / n
    if gain == 0 and loss == 0:
        return "flat: no gains and no losses"
    return 100.0 if loss == 0 else 100.0 - 100.0 / (1.0 + gain / loss)


def ema(values: Sequence[float], n: int) -> float | str:
    """EMA(n) of the last value: seeded with the mean of the first n values."""
    if len(values) < n:
        return f"too few complete candles ({len(values)}, need {n})"
    alpha = 2.0 / (n + 1)
    e = sum(values[:n]) / n
    for v in values[n:]:
        e = alpha * v + (1 - alpha) * e
    return e


def _row(name: str, value: float | str, unit: str) -> MetricRow:
    if isinstance(value, str):
        return MetricRow(name=name, value=None, unit=unit, available=False, reason=value)
    if not math.isfinite(value):
        return MetricRow(name=name, value=None, unit=unit, available=False, reason="not finite")
    return MetricRow(name=name, value=value, unit=unit, available=True)


def _per_sigma(x: float | str, sigma: float | str) -> float | str:
    if isinstance(x, str):
        return x
    if isinstance(sigma, str):
        return sigma
    return x / sigma


def metric_table(bars: Mapping[str, Sequence[Bar]], features: Mapping[str, float], features_reason: str | None,
                 levels: Sequence[Level] = ()) -> tuple[MetricRow, ...]:
    """Every metric, in a fixed order: per timeframe (15m, 1h, 4h) ATR, ATR/σ,
    RSI and EMA spread; then σ at 60 and 240, L, S_hd, T; then the distance to
    each level in σ (by level id); then volume and VWAP (unavailable)."""
    def feature(name: str) -> float | str:
        if features_reason is not None:
            return f"no features: {features_reason}"
        v = features.get(name)
        return f"feature {name} missing" if v is None else v

    s60, s240 = feature("sigma_px_60"), feature("sigma_px_240")
    rows: list[MetricRow] = []
    for tf in TIMEFRAMES:
        series = bars.get(tf)
        if series is None:
            why = f"no {tf} bars in the snapshot"
            atr: float | str = why
            rsi: float | str = why
            spread: float | str = why
        else:
            c = complete(series)
            closes = [b.close for b in c]
            atr, rsi = wilder_atr(c), wilder_rsi(closes)
            fast, slow = ema(closes, EMA_FAST), ema(closes, EMA_SLOW)
            spread = slow if isinstance(slow, str) else fast if isinstance(fast, str) else \
                _per_sigma(fast - slow, s60)
        rows += [_row(f"atr14_{tf}", atr, "$"), _row(f"atr14_{tf}_sigma", _per_sigma(atr, s60), "sigma"),
                 _row(f"rsi14_{tf}", rsi, "0-100"), _row(f"ema_spread_{tf}", spread, "sigma")]
    rows += [_row("sigma_px_60", s60, "$"), _row("sigma_px_240", s240, "$"), _row("L", feature("L"), ""),
             _row("S_hd", feature("S_hd"), ""), _row("T", feature("T"), "")]
    one = bars.get("1m") or ()
    last: float | str = one[-1].close if one else "no 1m bars"
    for lv in sorted(levels, key=lambda x: x.id):
        d = last if isinstance(last, str) else _per_sigma(last - lv.value, s240)
        rows.append(_row(f"dist_sigma_{lv.id}", d, "sigma"))
    rows += [_row("volume", NO_VOLUME, ""), _row("vwap", NO_VOLUME, "$")]
    return tuple(rows)


def metric(rows: Sequence[MetricRow], name: str) -> MetricRow | None:
    """One row by name (for the Technical v1 agent's rules)."""
    return next((r for r in rows if r.name == name), None)


__all__ = ["ATR_N", "EMA_FAST", "EMA_SLOW", "NO_VOLUME", "RSI_N", "TIMEFRAMES", "complete", "ema", "metric",
           "metric_table", "wilder_atr", "wilder_rsi"]
