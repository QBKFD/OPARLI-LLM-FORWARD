#!/usr/bin/env python3
"""
Step 3: fit the winner (HAR on L, S_hd, T) on all pre-2024 data and save it
as versioned JSON with a SHA-256.

Data: XAUUSD.txt 2007-01-01 .. 2023-12-31 (the usable years; the clean parquet
is the same data from 2020), read from the r2 cache exactly as
run_r3.load_conf does, with the end moved to 2024-01-01.

Fit: run_sigma.fit_fold with one fold whose training window is everything.
VolParams come from all bars before 2024-01-01, and OLS plus smearing from every
label whose window ends by 2024-01-01. Same code as the study's folds.

    .venv/bin/python research/sigma_compare/fit_final.py            # txt fit (step 3)
    .venv/bin/python research/sigma_compare/fit_final.py parquet    # parquet fit (post-hoc)
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import run_sigma as rs  # noqa: E402
from har_sigma import canonical, verify  # noqa: E402

r3 = rs.r3
START = pd.Timestamp("2007-01-01", tz="UTC")
END = rs.STUDY_SEAL                                  # 2024-01-01
VERSION = 1
MODEL = HERE / "model" / f"sigma_har_v{VERSION}.json"


def load_all() -> pd.DataFrame:
    df = pd.read_parquet(r3.CACHE).sort_index()
    df = df[~df.index.duplicated(keep="first")]
    df.index = df.index.tz_localize("UTC")           # the file is UTC (DATA_AUDIT_LONG.md)
    df = df[(df.index >= START) & (df.index < END)][["open", "high", "low", "close"]]
    rs.assert_sealed(df.index, END, "final 1-min")
    return df


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


DEFINITION = ("var(t, H) = P(t, H) * exp(beta . [1, L, S_hd, T]) * smear_c, where "
              "P(t, H) = sum of vol_params.profile[bucket(u)] over the first H "
              "market-open minutes u >= t (datafeed _market_open), and L, S_hd, T = "
              "vol_block.transform(vol_params) at the latest hourly label <= t. "
              "sigma_ret = sqrt(var); sigma_px = close_ref * sigma_ret.")

# source -> (loader, training start, output file, created, study refs, data source text, input file)
SOURCES = {
    "txt": (load_all, START, MODEL, "2026-10-14",
            {"path": "research/sigma_compare", "prereg_commit": "c75b24f",
             "report_commit": "de7a808", "verdict": "HAR replaces the plug-in (REPORT.md §1)"},
            "database/ohlcv_data/XAUUSD.txt via "
            "research/vol_features_r2/cache/xauusd_txt_pre2024_filetime.parquet",
            lambda: Path(r3.CACHE).resolve()),
    # Post-hoc (POSTHOC.md): the parquet fit, training from 2021-01-01 with 2020 as
    # warm-up, rows < 2024-01-01 only (the spent 2024-01..2026-01 rows are not loaded).
    "parquet": (lambda: rs.load("DEV"), pd.Timestamp("2021-01-01", tz="UTC"),
                HERE / "model" / f"sigma_har_parquet_v{VERSION}.json", "2026-10-15",
                {"path": "research/sigma_compare", "prereg_commit": "c75b24f",
                 "report_commit": "de7a808", "posthoc_commit": "fb8ba0d",
                 "verdict": "HAR replaces the plug-in (REPORT.md §1); post-hoc P1, P2 pass "
                            "(POSTHOC_RESULT.md)"},
                "database/ohlcv_data/XAUUSD_1min_UTC_clean.parquet (rows 2020-01-01 .. "
                "< 2024-01-01; training rows from 2021-01-01)",
                lambda: Path(rs.r1.DATA).resolve()),
}


def main(source: str = "txt") -> int:
    load, start, model_path, created, study, source_text, input_file = SOURCES[source]
    m = load()
    rs.log(f"bars {len(m):,}  {m.index[0]} .. {m.index[-1]}")
    ctx = rs.build_context(m, END)
    cfg = {"train_start": start}
    fit = rs.fit_fold(ctx, cfg, END + rs.EMBARGO)   # training = every row with window end <= END
    payload = {
        "model": "sigma_har",
        "version": VERSION,
        "created": created,
        "study": study,
        "definition": DEFINITION,
        "horizons_note": "fitted for H = 60 and 240 only; other H are not supported",
        "data": {"source": source_text,
                 "input_sha256": file_sha256(input_file()),
                 "first_bar": m.index[0].isoformat(), "last_bar": m.index[-1].isoformat(),
                 "n_bars": len(m), "seal": "rows < 2024-01-01"},
        "vol_params": json.loads(fit.params.to_json()),
        "features": ["1", "L", "S_hd", "T"],
        "horizons": {str(H): {"beta": [float(b) for b in fit.model[H]["beta"]],
                              "smear_c": float(fit.model[H]["c"]),
                              "n_train": fit.model[H]["n_train"]} for H in rs.HORIZONS},
    }
    if source != "txt":
        payload["data"]["train_start"] = start.isoformat()
    payload["sha256"] = hashlib.sha256(canonical(payload)).hexdigest()
    model_path.parent.mkdir(exist_ok=True)
    model_path.write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n")
    verify(model_path)
    for H in rs.HORIZONS:
        h = payload["horizons"][str(H)]
        rs.log(f"H={H}: beta={[round(b, 4) for b in h['beta']]} c={h['smear_c']:.4f} "
               f"n_train={h['n_train']:,}")
    rs.log(f"wrote {model_path.relative_to(HERE.parents[1])} sha256={payload['sha256']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "txt"))
