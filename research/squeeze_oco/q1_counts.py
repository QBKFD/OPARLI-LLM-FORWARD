#!/usr/bin/env python3
"""Event counts for PROPOSALS.md: how many candles and hourly labels the dev
period holds, per year, for the Q1 timeframe options. Counts only: no sigma, no
outcome, no forward return, no price. Writes results/q1_event_counts.json.

    python research/squeeze_oco/q1_counts.py

The dev period and data come from the notebook's register (the code cell that
defines REGISTER and need), read through need(): the notebook stays the one source.
The timeframes are the two options Q1_TIMEFRAME (OPEN) names; both are counted,
neither is chosen. The committed results/q1_event_counts.json was written at commit
338c180, when DEV_START was 2007-01-01; a rerun now covers the dev period
2012-2020 (decision log #16) and writes only the spans it holds."""
import json
import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import stage1  # noqa: E402

NOTEBOOK = HERE / "01_vol_state.ipynb"
TIMEFRAMES = (15, 30)                                       # Q1_TIMEFRAME's two options, as PROPOSALS.md listed them


def register() -> dict:
    """The notebook's register cell, run on its own: REGISTER, need, blocked."""
    cells = json.loads(NOTEBOOK.read_text())["cells"]
    (src,) = ["".join(c["source"]) for c in cells if c["cell_type"] == "code" and "REGISTER = [" in "".join(c["source"])]
    ns = {"pd": pd}
    exec(compile(src.split("\nreg = pd.DataFrame")[0], str(NOTEBOOK), "exec"), ns)      # the display lines left out
    return ns


OUT = HERE / "results" / "q1_event_counts.json"


def main() -> int:
    need = register()["need"]
    DEV_START, DEV_END = pd.Timestamp(need("DEV_START")), pd.Timestamp(need("DEV_END_EXCL"))
    m, checks = stage1.load_dev(need("DATA_KEY"), DEV_START, DEV_END, need("CACHE_CHECK"))
    years = {}
    tables = [stage1.candle_counts(m, tf) for tf in TIMEFRAMES] + [stage1.label_counts(m).to_frame()]
    for t in tables:
        for y, row in t.iterrows():
            dup = set(years.get(str(y), {})) & set(row.index)
            assert not dup, f"column names collide: {dup}"
            years.setdefault(str(y), {}).update({k: int(v) for k, v in row.items()})
    out = {"what": "event counts per year on the dev period; counts only (no sigma, outcome or price)",
           "dev_period_utc": [DEV_START.isoformat(), DEV_END.isoformat()], "checks": checks,
           "per_year": years,
           "totals": {k: sum(v[k] for v in years.values()) for k in next(iter(years.values()))},
           # sums of the per-year rows over the regimes the data check shows (notebook "Data check:
           # result"); nothing is excluded, both are reported
           "subtotals": {span: {k: sum(years[str(y)][k] for y in range(a, b + 1)) for k in next(iter(years.values()))}
                         for span, (a, b) in {"2007-2011": (2007, 2011), "2012-2020": (2012, 2020)}.items()
                         if all(str(y) in years for y in range(a, b + 1))}}
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(out, indent=1) + "\n")
    print(json.dumps(out["totals"], indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
