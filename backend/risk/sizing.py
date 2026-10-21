"""Fixed-fractional position size.

    size = equity × risk_pct / 100 ÷ (|entry - stop| × usd_per_unit_move),
           rounded DOWN to a multiple of size_increment

risk_pct is in PERCENT: 0.5 means 0.5 % of equity (owner, 2026-10-14). A
fraction passed by mistake (0.005) makes the size 100× too small, never too
large. Above MAX_RISK_PCT raises, so a typo like 50 for 0.5 is an error, not a
huge position.

Every argument is required. The XAUUSD constants below are passed explicitly by
the caller. The arithmetic is done in Decimal on each input's shortest repr:
in floats 2058.59 - 2038.59 = 20.000000000000227, so 1000 USD at risk would
size 49 units instead of 50. A risk budget below one increment gives 0.0,
which is not an error.
"""

from __future__ import annotations

from decimal import ROUND_FLOOR, Context, Decimal, localcontext

from backend.risk._check import positive

MAX_RISK_PCT = 5.0         # owner, 2026-10-14

# UNVERIFIED. Both values must be verified against IBKR contract details for
# CMDTY XAUUSD before any live use: reqContractDetails on Contract(secType="CMDTY",
# symbol="XAUUSD", exchange="SMART", currency="USD"), fields multiplier,
# minSize and sizeIncrement.
XAUUSD_USD_PER_UNIT_MOVE = 1.0   # assumed: price is USD per troy ounce, 1 unit = 1 oz
XAUUSD_SIZE_INCREMENT = 1.0      # assumed minimum order increment, in units


def position_size(*, equity, risk_pct, entry, stop, usd_per_unit_move, size_increment) -> float:
    """Raises ValueError for stop == entry, any input that is not a finite
    number > 0, or risk_pct above MAX_RISK_PCT."""
    equity = positive("equity", equity)
    risk_pct = positive("risk_pct", risk_pct)
    if risk_pct > MAX_RISK_PCT:
        raise ValueError(f"risk_pct is in percent and at most {MAX_RISK_PCT}, got {risk_pct!r}")
    entry, stop = positive("entry", entry), positive("stop", stop)
    if entry == stop:
        raise ValueError(f"stop equals entry ({entry!r})")
    usd = positive("usd_per_unit_move", usd_per_unit_move)
    inc = positive("size_increment", size_increment)
    with localcontext(Context(prec=28)):
        def dec(x: float) -> Decimal:
            return Decimal(repr(x))
        budget = dec(equity) * dec(risk_pct) / 100
        raw = budget / (abs(dec(entry) - dec(stop)) * dec(usd))
        n = (raw / dec(inc)).to_integral_value(rounding=ROUND_FLOOR)
        return float(n * dec(inc))
