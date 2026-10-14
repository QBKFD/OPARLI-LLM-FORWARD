"""Stop/target bracket from a volatility estimate.

    long:   stop = entry - k·sigma    target = entry + REWARD_RISK·k·sigma
    short:  stop = entry + k·sigma    target = entry - REWARD_RISK·k·sigma

sigma is in price units over the trade's horizon (vol_block's Sigma.sigma_px).
k has no default: the caller chooses it, and it must not be tuned on results.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

from risk._check import positive

REWARD_RISK = 2.0          # target distance = 2 × stop distance (spec)

Side = Literal["long", "short"]


@dataclass(frozen=True)
class Bracket:
    direction: Side
    entry: float
    stop: float
    target: float
    k: float
    sigma: float


def bracket(*, entry, direction: str, sigma, k) -> Bracket:
    """Raises ValueError for a direction other than long/short (including
    "none"), a non-finite or non-positive entry, sigma or k, or a stop or
    target that would not be a positive price distinct from entry."""
    if direction not in ("long", "short"):
        raise ValueError(f"direction must be 'long' or 'short', got {direction!r}")
    entry, sigma, k = positive("entry", entry), positive("sigma", sigma), positive("k", k)
    d = k * sigma
    sign = 1.0 if direction == "long" else -1.0
    stop, target = entry - sign * d, entry + sign * REWARD_RISK * d
    if not (math.isfinite(stop) and math.isfinite(target)) or stop == entry:
        raise ValueError(f"k·sigma = {d!r} gives no usable stop at entry {entry!r}")
    if stop <= 0 or target <= 0:
        raise ValueError(f"{direction} bracket at entry {entry!r} with k·sigma {d!r} "
                         f"gives a non-positive price (stop {stop!r}, target {target!r})")
    return Bracket(direction, entry, stop, target, k, sigma)
