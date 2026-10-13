"""Point-in-time reads from the store.

Placeholder for step 1 (package setup): the signature is fixed here so the
import path `datafeed.store.query.bars_before` exists; the implementation and
the full "as of t" rule arrive in step 5.
"""


def bars_before(symbol, t, lookback):
    """Bars for `symbol` with bar_ts strictly < t, at most `lookback` of them."""
    raise NotImplementedError("implemented in step 5")
