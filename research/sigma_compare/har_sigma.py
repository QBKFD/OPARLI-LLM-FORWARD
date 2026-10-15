"""Reference implementation of the sigma proposed in PROPOSAL.md (HAR on L,
S_hd, T), for review. Live code doesn't use it; nothing in datafeed/ or risk/
changes.

It takes the same VolState as vol_block.sigma_over and returns the same
Sigma type. The window is the first H market-open minutes from t, which is the
window the study fitted and tested.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from datafeed.features import vol_block as vb
from datafeed.ingest.xauusd_bars import _market_open

MAX_SEARCH = timedelta(days=7)          # longer than any closure the calendar has


def canonical(payload: dict) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def verify(path) -> dict:
    """Read a model file; raises unless its sha256 matches the other fields."""
    d = json.loads(Path(path).read_text())
    want = d.pop("sha256")
    got = hashlib.sha256(canonical(d)).hexdigest()
    if got != want:
        raise ValueError(f"{path}: sha256 mismatch ({got} != {want})")
    d["sha256"] = want
    return d


@dataclass(frozen=True)
class HarSigmaModel:
    version: int
    sha256: str
    params: vb.VolParams
    horizons: dict                      # H -> (beta (4,), smear_c)


def from_payload(d: dict) -> HarSigmaModel:
    return HarSigmaModel(
        version=int(d["version"]), sha256=d.get("sha256", ""),
        params=vb.VolParams.from_json(json.dumps(d["vol_params"])),
        horizons={int(H): (tuple(float(b) for b in v["beta"]), float(v["smear_c"]))
                  for H, v in d["horizons"].items()})


def load_model(path) -> HarSigmaModel:
    return from_payload(verify(path))


def open_window(t, H: int):
    """First H market-open minutes from t (minute opens >= t). Returns (minutes, end)."""
    u = pd.Timestamp(t).ceil("1min").to_pydatetime()
    stop, out = u + MAX_SEARCH, []
    while len(out) < H:
        if u >= stop:
            raise ValueError(f"fewer than {H} open minutes within {MAX_SEARCH} of {t}")
        if _market_open(u):
            out.append(u)
        u += timedelta(minutes=1)
    return out, out[-1] + timedelta(minutes=1)


def sigma_har(model: HarSigmaModel, state: vb.VolState, H: int) -> vb.Sigma:
    """var = P(t, H) · exp(beta · [1, L, S_hd, T]) · smear_c over the next H open minutes.
    open_minutes is H by construction; crosses_close says the window spans a close."""
    if H not in model.horizons:
        raise ValueError(f"H = {H!r} not fitted (have {sorted(model.horizons)})")
    if state.reason is not None or not math.isfinite(state.close_ref):
        raise ValueError(f"no sigma without a valid state (reason {state.reason!r})")
    beta, c = model.horizons[H]
    minutes, end = open_window(state.t, H)
    prof = np.asarray(model.params.profile, dtype=float)
    P = float(prof[vb._buckets(pd.DatetimeIndex(minutes))].sum())
    x = (1.0, state.L, state.S_hd, state.T)
    var = P * math.exp(sum(b * xi for b, xi in zip(beta, x))) * c
    sigma_ret = math.sqrt(var)
    start = pd.Timestamp(state.t).ceil("1min").to_pydatetime()
    return vb.Sigma(H=H, sigma_ret=sigma_ret, sigma_px=state.close_ref * sigma_ret, open_minutes=H,
                    crosses_close=(end - start) > timedelta(minutes=H))
