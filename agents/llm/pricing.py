"""List prices, for the cost column of llm_calls (an estimate, not billing).

USD per million tokens, from the Claude API reference cached 2026-10-06:
input, output, cache read. A 5-minute cache write is billed at 1.25× the
input price. Thinking is billed as output. A model missing here gets a cost
of None, and the call still runs: the price table never decides which models
the owner may use.
"""

from __future__ import annotations

from typing import Mapping

CACHE_WRITE_MULT = 1.25
PRICES: dict[str, tuple[float, float, float | None]] = {
    "claude-opus-5-5": (4.00, 20.00, 0.20),
    "claude-sonnet-5-5": (2.00, 10.00, 0.20),
    "claude-haiku-5-5": (0.10, 0.50, None),       # cache-read price not in the reference
}


def cost_usd(model: str, usage: Mapping[str, int | None]) -> float | None:
    """The list-price cost of one response, or None for an unknown model or a
    cache read on a model without a cache-read price."""
    p = PRICES.get(model)
    if p is None:
        return None
    inp, out, read = p
    n_in = usage.get("input_tokens") or 0
    n_out = usage.get("output_tokens") or 0
    n_read = usage.get("cache_read_input_tokens") or 0
    n_write = usage.get("cache_creation_input_tokens") or 0
    if n_read and read is None:
        return None
    return (n_in * inp + n_out * out + n_read * (read or 0.0) + n_write * inp * CACHE_WRITE_MULT) / 1e6


__all__ = ["CACHE_WRITE_MULT", "PRICES", "cost_usd"]
