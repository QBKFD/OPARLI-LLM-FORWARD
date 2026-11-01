"""Black-76 gamma and unsigned dollar gamma (|GEX|) for options on futures.

Pure, stdlib only. PREREG.md, "The metric R":

    Γ = e^(−rT) φ(d1) / (F σ √T),   d1 = (ln(F/K) + σ²T/2) / (σ √T)

the same for calls and puts. The study uses r = 0. |GEX| for one contract
line is Γ · OI · multiplier · F² · 0.01: dollars of the underlying traded per
1 % move to keep the position delta-hedged, unsigned.

`gbsm_gamma` is the generalized Black-Scholes-Merton gamma with cost of carry
b (Black-76 is b = 0, with S = F); it exists so the formula can be checked
against textbook values for b ≠ 0 as well.
"""

from __future__ import annotations

import math

MULTIPLIER = 100          # oz per OG option / GC future
ONE_PCT = 0.01


def norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _check(**kw: float) -> None:
    for name, v in kw.items():
        if not (isinstance(v, (int, float)) and math.isfinite(v) and v > 0):
            raise ValueError(f"{name} must be a positive finite number, got {v!r}")


def _d1(s: float, k: float, t: float, b: float, sigma: float) -> float:
    return (math.log(s / k) + (b + 0.5 * sigma * sigma) * t) / (sigma * math.sqrt(t))


def gbsm_gamma(s: float, k: float, t: float, r: float, b: float, sigma: float) -> float:
    """Generalized BSM gamma: e^((b−r)T) φ(d1) / (S σ √T)."""
    _check(s=s, k=k, t=t, sigma=sigma)
    d1 = _d1(s, k, t, b, sigma)
    return math.exp((b - r) * t) * norm_pdf(d1) / (s * sigma * math.sqrt(t))


def black76_gamma(f: float, k: float, t: float, sigma: float, r: float = 0.0) -> float:
    """Gamma of a call or put on a future (per $1 move of F, per unit of underlying)."""
    return gbsm_gamma(f, k, t, r, 0.0, sigma)


def black76_price(f: float, k: float, t: float, sigma: float, right: str, r: float = 0.0) -> float:
    """Black-76 price of a European call ("C") or put ("P") on a future."""
    _check(f=f, k=k, t=t, sigma=sigma)
    d1 = _d1(f, k, t, 0.0, sigma)
    d2 = d1 - sigma * math.sqrt(t)
    df = math.exp(-r * t)
    if right == "C":
        return df * (f * norm_cdf(d1) - k * norm_cdf(d2))
    if right == "P":
        return df * (k * norm_cdf(-d2) - f * norm_cdf(-d1))
    raise ValueError(f"right must be 'C' or 'P', got {right!r}")


def dollar_gamma_1pct(gamma: float, f: float, oi: float, multiplier: float = MULTIPLIER) -> float:
    """Unsigned dollars of the underlying traded per 1 % move: Γ · OI · m · F² · 0.01."""
    if oi < 0:
        raise ValueError(f"open interest can't be negative, got {oi!r}")
    return gamma * oi * multiplier * f * f * ONE_PCT


__all__ = ["MULTIPLIER", "black76_gamma", "black76_price", "dollar_gamma_1pct", "gbsm_gamma",
           "norm_cdf", "norm_pdf"]
