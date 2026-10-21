"""Input validation shared by bracket and sizing."""

from __future__ import annotations

import math
import numbers


def positive(name: str, x) -> float:
    """x as a float; raises ValueError unless it is a finite real number > 0."""
    if isinstance(x, bool) or not isinstance(x, numbers.Real):
        raise ValueError(f"{name} must be a real number, got {x!r}")
    x = float(x)
    if not math.isfinite(x) or x <= 0:
        raise ValueError(f"{name} must be finite and > 0, got {x!r}")
    return x
