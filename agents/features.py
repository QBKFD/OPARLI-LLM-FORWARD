"""Snapshot features: HAR σ at 60 and 240 minutes and the vol state L, S_hd, T
(docs/AGENTS_ARCHITECTURE.md §4.1).

Computed by the snapshot builder, from the same fetched 1-min bars as the
snapshot (one query, the same known_by), with datafeed's vol_block and the
σ model named in the infrastructure config (`snapshot.sigma_model`; the model
file is SHA-256 checked on load). Nothing here raises for missing data: a
missing model, an invalid vol state (too little history) or a missing pandas
is a reason, and the snapshot marks `features` unavailable with it.

    features_from_rows(rows, as_of, model_name) -> Features
"""

from __future__ import annotations

import functools
import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Sequence

HORIZONS = (60, 240)


@dataclass(frozen=True)
class Features:
    values: dict[str, float] = field(default_factory=dict)
    reason: str | None = None              # why there are none (values is then empty)
    sigma_model: str | None = None
    sigma_model_sha256: str | None = None


@functools.lru_cache(maxsize=4)
def _model(name: str) -> Any:
    import datafeed.features.vol_block as vb   # noqa: PLC0415 - pandas only when features are asked for

    return vb.load_sigma_model(name)


def sigma_model_sha256(name: str | None) -> str | None:
    """The pinned SHA-256 of a σ model name (for the epoch), or None."""
    if name is None:
        return None
    import datafeed.features.vol_block as vb   # noqa: PLC0415

    entry = vb.SIGMA_MODELS.get(name)
    return None if entry is None else entry[1]


def features_from_rows(rows: Sequence[dict], as_of: datetime, model_name: str | None) -> Features:
    """σ at 60 and 240 minutes and L, S_hd, T as of `as_of`, or the reason not."""
    if model_name is None:
        return Features(reason="no sigma model configured (snapshot.sigma_model)")
    try:
        import datafeed.features.vol_block as vb   # noqa: PLC0415

        model = _model(model_name)
    except ImportError as e:
        return Features(reason=f"vol_block unavailable: {e}")
    except Exception as e:                        # noqa: BLE001 - an unknown name, a bad file or SHA
        return Features(reason=f"sigma model {model_name!r}: {type(e).__name__}: {e}")
    state = vb.state_at(model.params, vb.frame_from_rows(list(rows)), as_of)
    if state.reason is not None:
        return Features(reason=f"vol state: {state.reason}", sigma_model=model.name,
                        sigma_model_sha256=model.sha256)
    values = {"L": state.L, "S_hd": state.S_hd, "T": state.T}
    for H in HORIZONS:
        s = vb.sigma_over(model, state, H)
        values.update({f"sigma_px_{H}": s.sigma_px, f"sigma_ret_{H}": s.sigma_ret,
                       f"crosses_close_{H}": 1.0 if s.crosses_close else 0.0})
    if not all(math.isfinite(v) for v in values.values()):
        return Features(reason="vol state: non-finite values", sigma_model=model.name,
                        sigma_model_sha256=model.sha256)
    return Features(values=values, sigma_model=model.name, sigma_model_sha256=model.sha256)


__all__ = ["Features", "HORIZONS", "features_from_rows", "sigma_model_sha256"]
