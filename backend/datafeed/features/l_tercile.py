"""The L tercile: calm / normal / stressed, from cut-offs fixed on the sigma
model's training window.

Rule (docs/AGENTS_TECHNICAL_DESIGN.md: "cut-offs fixed from the sigma model's
training window"; computed as research/regime_v2's BASE-L3 does):
    L = vol_block.transform(model.params, bars)["L"] at every hourly label
        inside the model's training window [params.train_start, params.train_end],
        the bars being the model's own data (the clean parquet from its
        data.first_bar, so the first labels have their 120-hour history);
    q1, q2 = numpy.nanquantile(L, [1/3, 2/3]);
    tercile = numpy.digitize(L, [q1, q2]): 0 calm (L < q1), 1 normal
    (q1 <= L < q2), 2 stressed (L >= q2).

The cut-offs ship as a file in models/, SHA-256 pinned here, as for the HAR
models; `python -m backend.datafeed.features.l_tercile build PARQUET` recomputes
them (tests/datafeed/test_l_tercile.py checks the shipped file against the
parquet when it's present).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from dataclasses import dataclass

from backend.datafeed.features import vol_block as vb

LABELS = ("calm", "normal", "stressed")
# sigma model name -> (file, SHA-256 of the canonical JSON of every field except "sha256")
CUTOFFS = {
    "parquet2021_2023": ("l_terciles_parquet2021_2023_v1.json",
                         "6ad57b6d8c2b3810cbde3079d8b9753c59e9337b417981e9969934fbee125231"),
}


@dataclass(frozen=True)
class Cutoffs:
    model: str
    model_sha256: str
    q1: float
    q2: float
    sha256: str


def _canonical(d: dict) -> bytes:
    return json.dumps(d, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def load(model: vb.SigmaModel) -> Cutoffs:
    """The shipped cut-offs for `model`. Raises unless the file's SHA-256 is the
    pinned one and it was computed for this model (its SHA-256)."""
    if model.name not in CUTOFFS:
        raise ValueError(f"no L tercile cut-offs for sigma model {model.name!r}")
    file, pinned = CUTOFFS[model.name]
    d = json.loads((vb.MODELS_DIR / file).read_text())
    stated = d.pop("sha256", None)
    got = hashlib.sha256(_canonical(d)).hexdigest()
    if got != stated or got != pinned:
        raise ValueError(f"{file}: SHA-256 check failed (content {got}, stated {stated}, pinned {pinned})")
    if d["model_sha256"] != model.sha256:
        raise ValueError(f"{file} was computed for model SHA-256 {d['model_sha256']}, not {model.sha256}")
    return Cutoffs(model=model.name, model_sha256=model.sha256, q1=float(d["q1"]), q2=float(d["q2"]), sha256=got)


def tercile(L: float, c: Cutoffs) -> str:
    if not math.isfinite(L):
        raise ValueError(f"no tercile for L = {L!r}")
    return LABELS[0 if L < c.q1 else 1 if L < c.q2 else 2]


def compute(model: vb.SigmaModel, bars) -> dict:
    """The cut-off file's content (without "sha256") from 1-min bars covering the
    model's data window. bars: OHLC frame on a UTC DatetimeIndex of bar opens."""
    import numpy as np
    import pandas as pd
    p = model.params
    lo, hi = pd.Timestamp(p.train_start), pd.Timestamp(p.train_end)
    m = bars[bars.index < hi]
    L = vb.transform(p, m)["L"]
    L = L[(L.index >= lo) & (L.index <= hi)]
    q1, q2 = (float(x) for x in np.nanquantile(L.to_numpy(dtype=float), [1 / 3, 2 / 3]))
    return {"kind": "l_terciles", "version": 1, "model": model.name, "model_sha256": model.sha256,
            "rule": "numpy.nanquantile(L, [1/3, 2/3]) over vol_block.transform(model.params) hourly labels in "
                    "[train_start, train_end]; tercile = digitize(L, [q1, q2]): 0 calm, 1 normal, 2 stressed "
                    "(research/regime_v2 BASE-L3)",
            "train_start": pd.Timestamp(p.train_start).isoformat(), "train_end": pd.Timestamp(p.train_end).isoformat(),
            "n_labels": int(L.notna().sum()), "q1": q1, "q2": q2}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m backend.datafeed.features.l_tercile")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="compute the cut-offs from the clean parquet and print the file")
    b.add_argument("parquet")
    b.add_argument("--model", default=vb.DEFAULT_SIGMA_MODEL)
    a = ap.parse_args(argv)
    import pandas as pd
    model = vb.load_sigma_model(a.model)
    df = pd.read_parquet(a.parquet, columns=["timestamp", "open", "high", "low", "close"])
    df = df.set_index(pd.DatetimeIndex(df.pop("timestamp")).tz_convert("UTC")).sort_index()
    d = compute(model, df)
    d["sha256"] = hashlib.sha256(_canonical(d)).hexdigest()
    print(json.dumps(d, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
