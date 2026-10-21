"""Risk node: STUB, pass-through.

A later risk/ package fills entry, sl, tp, size and sigma. Until then each
Decision passes through unchanged and those fields stay null.
"""

from backend.agents.schemas import Decision


def apply_risk(decision: Decision) -> Decision:
    return decision
