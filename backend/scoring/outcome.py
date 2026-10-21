"""Outcome of a bracketed trade, walked forward on 1-min bars.

    score(entry_time, direction, entry_price, stop, target, max_hold_minutes, bars)
        -> Outcome(reason, exit_time, exit_price, r_multiple, mae, mfe, ...)

The bracket can be the agent's own stop/target, or the neutral k·sigma
bracket from backend.risk.bracket with the HAR sigma over the holding period
(score_neutral), so one trade can be scored twice on the same bars.

BARS
    (bar_ts, open, high, low, close) tuples, bar_ts = the minute's open time
    (tz-aware). Only bars that open at or after entry_time and end by
    entry_time + max_hold are used (bar_ts >= entry_time, bar_ts + 1 min <=
    entry_time + H). A bar that opened before entry_time is never used, even
    when entry_time is mid-minute. bars_from_frame / bars_from_rows /
    read_bars build the tuples.

RULES (conservative; see DECISIONS.md)
    - The stop is checked before the target. A bar whose range holds both
      counts as a stop.
    - Gap through the stop: if a bar opens beyond the stop, the exit is at that
      open (worse than the stop). A gap through the target fills at the target,
      with no credit for the better open.
    - Timeout: no level reached by the last bar inside the holding period. The
      exit is at that bar's close.
    - exit_time is the open time of the bar in which the exit happened (for a
      timeout, the last bar used).
    - R = (exit - entry) / |entry - stop| for a long, mirrored for a short.
    - MAE / MFE are price distances >= 0 from entry over the bars held.
      - Bars before the exit bar count in full.
      - In the exit bar the order of high and low is unknown, so:
        - MAE takes that bar's adverse extreme in full, but at a stop it is the
          exit price;
        - MFE takes only what is certain: the exit price at a target, else the
          bar's open.
    - A bar whose high is below its open or close (or low above) is widened
      to hold them. Vendor bars have this, and it only makes a stop more likely.
    - No bar inside the holding period raises NoBarsError.
"""

from __future__ import annotations

import math
import numbers
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterable, Literal

from backend.datafeed.store.query import DEFAULT_KINDS, bars_before
from backend.risk.bracket import Bracket, bracket

BAR = timedelta(minutes=1)
Side = Literal["long", "short"]


class NoBarsError(ValueError):
    """No 1-min bar inside the holding period: the trade can't be scored."""


@dataclass(frozen=True)
class Outcome:
    reason: Literal["target", "stop", "timeout"]
    exit_time: datetime          # open time of the exit bar
    exit_price: float
    r_multiple: float
    mae: float                   # max adverse excursion, price units, >= 0
    mfe: float                   # max favourable excursion, price units, >= 0
    mae_r: float
    mfe_r: float
    bars_used: int               # bars walked, the exit bar included
    gap: bool                    # the stop was filled at a gapped open beyond it


def _real(name: str, x) -> float:
    if isinstance(x, bool) or not isinstance(x, numbers.Real) or not math.isfinite(float(x)) or x <= 0:
        raise ValueError(f"{name} must be a finite number > 0, got {x!r}")
    return float(x)


def _aware(name: str, t) -> datetime:
    if not isinstance(t, datetime) or t.tzinfo is None or t.utcoffset() is None:
        raise ValueError(f"{name} must be a tz-aware datetime, got {t!r}")
    return t


def _window(bars: Iterable, entry_time: datetime, horizon_end: datetime) -> list[tuple]:
    out, prev = [], None
    for b in bars:
        ts, o, h, lo, c = b
        _aware("bar_ts", ts)
        if prev is not None and ts <= prev:
            raise ValueError(f"bars must be strictly increasing in time ({prev} then {ts})")
        prev = ts
        if ts < entry_time or ts + BAR > horizon_end:
            continue
        o, h, lo, c = (_real("bar price", x) for x in (o, h, lo, c))
        # vendor bars can have a high below the open or close (or a low above);
        # widen the range to hold them, which can only make a stop more likely
        out.append((ts, o, max(h, o, c), min(lo, o, c), c))
    return out


def score(*, entry_time: datetime, direction: str, entry_price, stop, target,
          max_hold_minutes: int, bars: Iterable) -> Outcome:
    """Walk forward from entry_time until the stop, the target or the end of
    max_hold_minutes. Raises ValueError on inconsistent inputs and NoBarsError
    when no bar falls inside the holding period."""
    if direction not in ("long", "short"):
        raise ValueError(f"direction must be 'long' or 'short', got {direction!r}")
    entry_time = _aware("entry_time", entry_time)
    entry, stop, target = (_real(n, x) for n, x in
                           (("entry_price", entry_price), ("stop", stop), ("target", target)))
    if isinstance(max_hold_minutes, bool) or not isinstance(max_hold_minutes, int) or max_hold_minutes <= 0:
        raise ValueError(f"max_hold_minutes must be a positive int, got {max_hold_minutes!r}")
    long = direction == "long"
    if long and not stop < entry < target:
        raise ValueError(f"a long needs stop < entry < target, got {stop}, {entry}, {target}")
    if not long and not target < entry < stop:
        raise ValueError(f"a short needs target < entry < stop, got {target}, {entry}, {stop}")

    used = _window(bars, entry_time, entry_time + timedelta(minutes=max_hold_minutes))
    if not used:
        raise NoBarsError(f"no bar between {entry_time} and +{max_hold_minutes} min")
    risk = abs(entry - stop)
    sign = 1.0 if long else -1.0
    mae = mfe = 0.0

    def adverse(px):                      # distance against the trade, >= 0 when adverse
        return max(0.0, sign * (entry - px))

    def favourable(px):
        return max(0.0, sign * (px - entry))

    for n, (ts, o, h, lo, c) in enumerate(used, start=1):
        worst, best = (lo, h) if long else (h, lo)
        stop_hit = worst <= stop if long else worst >= stop
        target_hit = best >= target if long else best <= target
        if stop_hit:                                         # also when both are inside the bar
            gapped = o <= stop if long else o >= stop
            px = o if gapped else stop
            return _outcome("stop", ts, px, entry, risk, sign, max(mae, adverse(px)),
                            max(mfe, favourable(o)), n, gapped and o != stop)
        if target_hit:
            return _outcome("target", ts, target, entry, risk, sign, max(mae, adverse(worst)),
                            max(mfe, favourable(target)), n, False)
        mae, mfe = max(mae, adverse(worst)), max(mfe, favourable(best))
    ts, *_, c = used[-1]
    return _outcome("timeout", ts, c, entry, risk, sign, mae, mfe, len(used), False)


def _outcome(reason, ts, px, entry, risk, sign, mae, mfe, n, gap) -> Outcome:
    return Outcome(reason=reason, exit_time=ts, exit_price=px, r_multiple=sign * (px - entry) / risk,
                   mae=mae, mfe=mfe, mae_r=mae / risk, mfe_r=mfe / risk, bars_used=n, gap=gap)


@dataclass(frozen=True)
class NeutralScore:
    bracket: Bracket             # backend.risk.bracket: stop at k·sigma, target at 2k·sigma
    sigma: object                # datafeed vol_block.Sigma (HAR) over the holding period
    outcome: Outcome


def score_neutral(*, entry_time: datetime, direction: str, entry_price, k, max_hold_minutes: int,
                  bars: Iterable, model, state) -> NeutralScore:
    """Score the neutral k·sigma bracket. sigma is the HAR sigma over the holding
    period, vol_block.sigma_over(model, state, max_hold_minutes).sigma_px, so
    max_hold_minutes must be a fitted horizon (60 or 240). state must be as of
    entry_time and computed with model.params (sigma_over checks the hash)."""
    # vol_block needs numpy and pandas; importing it here keeps score() stdlib-only
    from backend.datafeed.features import vol_block as vb
    if state.t != _aware("entry_time", entry_time):
        raise ValueError(f"state is as of {state.t}, not the entry time {entry_time}")
    sig = vb.sigma_over(model, state, max_hold_minutes)
    b = bracket(entry=entry_price, direction=direction, sigma=sig.sigma_px, k=k)
    return NeutralScore(b, sig, score(entry_time=entry_time, direction=direction, entry_price=b.entry,
                                      stop=b.stop, target=b.target,
                                      max_hold_minutes=max_hold_minutes, bars=bars))


# ---------------------------------------------------------------- bar sources

def bars_from_rows(rows: Iterable[dict]) -> list[tuple]:
    """backend.datafeed.store.query rows (bar_ts as ISO string) -> bar tuples."""
    return [(datetime.fromisoformat(r["bar_ts"]), r["open"], r["high"], r["low"], r["close"])
            for r in rows]


def bars_from_frame(df) -> list[tuple]:
    """A pandas OHLC frame on a tz-aware DatetimeIndex of bar opens -> bar tuples."""
    return [(ts.to_pydatetime(), float(o), float(h), float(lo), float(c))
            for ts, o, h, lo, c in zip(df.index, df["open"], df["high"], df["low"], df["close"])]


def read_bars(symbol: str, entry_time: datetime, max_hold_minutes: int, *, db=None,
              kinds=None, known_by=None) -> list[tuple]:
    """Bars of the holding period from the store: bars_before(entry + H, lookback H)
    returns exactly the bars that open at or after entry and end by entry + H."""
    end = _aware("entry_time", entry_time) + timedelta(minutes=max_hold_minutes)
    rows = bars_before(symbol, end, timedelta(minutes=max_hold_minutes), db=db,
                       known_by=known_by, kinds=kinds or DEFAULT_KINDS)
    return bars_from_rows(rows)
