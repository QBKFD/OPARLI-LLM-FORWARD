#!/usr/bin/env python3
"""research/news_vol: fit the live news-aware sigma model (feat/news-sigma).

Not part of Study A's PREREG, and no outcome test: Study A's verdict (PASS, #64;
replicated on XAGUSD and EURUSD, #69) chose the design. This fits it once for live
use, as research/sigma_compare/fit_final.py did for HAR. DECISIONS.md, NS1-NS6.

Data and rows: exactly those of the default live model, parquet2021_2023
(sigma_compare fit_final.py parquet): the clean XAUUSD parquet, rows 2020-01-01 ..
< 2024-01-01; VolParams and OLS from 2021-01-01; every hourly label whose window
ends by 2024-01-01, crossing windows included (sigma_compare's pre-registered fit).
The base fit must reproduce the shipped model (VolParams hash, beta, smear_c), or
nothing is written.

Augmented arm: Study A's (D5) on the same rows: X = [1, L, S_hd, T, d_data, d_fomc],
OLS of ln(RV / P) plus Duan smearing. d_x = 1 when a grouped usd_tier1 release of
that type falls in the window, t <= e < E (N1), E = the end of the window's H
market-open minutes (t + H unless the window crosses a close).

    .venv/bin/python research/news_vol/fit_final.py
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import nv_core  # noqa: E402,F401  (puts research/sigma_compare on sys.path)
import nv  # noqa: E402
import nv_data  # noqa: E402
import nv_params as V  # noqa: E402
import run_sigma as rs  # noqa: E402
from har_sigma import canonical  # noqa: E402

from backend.datafeed.features import news_sigma as ns  # noqa: E402
from backend.datafeed.features import vol_block as vb  # noqa: E402

ROOT = HERE.parents[1]
BASE = "parquet2021_2023"
START = pd.Timestamp("2021-01-01", tz="UTC")
END = rs.STUDY_SEAL                                  # 2024-01-01
VERSION = 1
NAME = f"sigma_har_news_parquet2021_2023_v{VERSION}.json"
MODEL = HERE / "model" / NAME
LIVE = ROOT / "backend" / "datafeed" / "features" / "models" / NAME
RESULT = HERE / "model" / "fit_final_result.json"

DEFINITION = ("var(t, H) = P(t, H) * exp(beta . [1, L, S_hd, T, d_data, d_fomc]) * smear_c. P, L, S_hd, "
              "T as in sigma_har (P over the first H market-open minutes u >= t, the window [t, E)). "
              "d_data / d_fomc = 1 when an event of that group (currency, impact and names in "
              "'events') has t <= ts_utc < E, else 0. Same VolParams as the base model.")


def flags(labels: pd.DatetimeIndex, E_ns: np.ndarray, ev_ns: np.ndarray) -> np.ndarray:
    """1.0 where an event e has label <= e < E (the window's own end)."""
    ev = np.sort(np.asarray(ev_ns, dtype=np.int64))
    return ((np.searchsorted(ev, E_ns, "left") - np.searchsorted(ev, labels.asi8, "left")) > 0).astype(float)


def main() -> int:
    base = vb.load_sigma_model(BASE)
    m = rs.load("DEV")
    rs.log(f"bars {len(m):,}  {m.index[0]} .. {m.index[-1]}")
    ctx = rs.build_context(m, END)
    fit = rs.fit_fold(ctx, {"train_start": START}, END + rs.EMBARGO)

    # the base fit must be the shipped model
    if fit.params.sha256 != base.params.sha256:
        raise AssertionError(f"VolParams differ from {BASE}: {fit.params.sha256} vs {base.params.sha256}")
    for H in rs.HORIZONS:
        b, c = base.horizons[H]
        if tuple(float(x) for x in fit.model[H]["beta"]) != b or float(fit.model[H]["c"]) != c:
            raise AssertionError(f"H={H}: base fit {fit.model[H]['beta']}, {fit.model[H]['c']} != {BASE} {b}, {c}")
    rs.log(f"base fit reproduces {BASE} exactly (VolParams {base.params.sha256[:12]}…, beta, smear_c)")

    ev, info = nv_data.events()
    is_f = (ev["type"] == "FOMC").to_numpy()
    ev_ns = pd.DatetimeIndex(ev.index).asi8
    labels = ctx.labels
    horizons, result = {}, {"base_model": BASE, "horizons": {}}
    for H in rs.HORIZONS:
        w, P, train = ctx.win[H], fit.P[H], fit.model[H]["train"]
        d_data, d_fomc = flags(labels, w.E_ns, ev_ns[~is_f]), flags(labels, w.E_ns, ev_ns[is_f])
        y = np.log(w.rv[train] / P[train])
        b_har, c_har = nv.fit_smeared(fit.X[train], y)
        assert np.array_equal(b_har, fit.model[H]["beta"]) and c_har == fit.model[H]["c"]
        Xa = np.column_stack([fit.X, d_data, d_fomc])
        beta, c = nv.fit_smeared(Xa[train], y)
        horizons[str(H)] = {"beta": [float(b) for b in beta], "smear_c": float(c), "n_train": int(train.sum()),
                            "n_train_data": int(d_data[train].sum()), "n_train_fomc": int(d_fomc[train].sum()),
                            "n_train_crossing": int((train & w.crossing).sum()),
                            "n_train_crossing_with_release": int((train & w.crossing & ((d_data + d_fomc) > 0)).sum())}
        # descriptive, in-sample (no test): the news model against the base off and on releases
        h_har = P * np.exp(fit.X @ b_har) * c_har
        h_aug = P * np.exp(Xa @ beta) * c
        none = train & (d_data == 0) & (d_fomc == 0)
        nc = train & ~w.crossing
        bn, cn = nv.fit_smeared(Xa[nc], np.log(w.rv[nc] / P[nc]))
        result["horizons"][str(H)] = {
            "variance_multiplier_data": float(np.exp(beta[4])), "variance_multiplier_fomc": float(np.exp(beta[5])),
            "median_ratio_news_to_base_no_release": float(np.median(h_aug[none] / h_har[none])),
            "median_ratio_news_to_base_data": float(np.median(h_aug[train & (d_data == 1)] / h_har[train & (d_data == 1)])),
            "median_ratio_news_to_base_fomc": float(np.median(h_aug[train & (d_fomc == 1)] / h_har[train & (d_fomc == 1)])),
            "noncrossing_rows_only_fit": {"beta": [float(b) for b in bn], "smear_c": float(cn), "n_train": int(nc.sum())},
        }
        rs.log(f"H={H}: beta={[round(b, 4) for b in beta]} c={c:.4f} n_train={int(train.sum()):,} "
               f"(data {horizons[str(H)]['n_train_data']}, FOMC {horizons[str(H)]['n_train_fomc']})")

    data = json.loads((vb.MODELS_DIR / vb.SIGMA_MODELS[BASE][0]).read_text())["data"]   # same bars and rows
    data["calendar"] = {"key": nv_data.CALENDAR_KEY, "sha256": info["calendar_sha256"],
                        "commit": info["calendar_commit"], "events_grouped_dev": info["events_grouped"]}
    payload = {
        "model": ns.MODEL_KIND,
        "version": VERSION,
        "created": "2026-11-08",
        "study": {"path": "research/news_vol", "prereg_commit": "843b447", "results_merge": "4b72e08",
                  "verdict": "PASS (REPORT.md section 1); replicated on XAGUSD and EURUSD "
                             "(research/news_vol_rep, PREREG d653c68, results 9f9c8b0)",
                  "fit": "research/news_vol/fit_final.py, not pre-registered (DECISIONS.md NS1-NS6)"},
        "base_model": {"name": BASE, "sha256": base.sha256},
        "definition": DEFINITION,
        "horizons_note": "fitted for H = 60 and 240 only; other H are not supported",
        "data": data,
        "events": {"currency": "USD", "impact": "High", "data": list(V.DATA_EVENTS), "fomc": list(V.FOMC_EVENTS),
                   "rule": "t <= ts_utc < E; untimed rows never count"},
        "vol_params": json.loads(fit.params.to_json()),
        "features": list(ns.FEATURES),
        "horizons": horizons,
    }
    payload["sha256"] = hashlib.sha256(canonical(payload)).hexdigest()
    MODEL.parent.mkdir(exist_ok=True)
    MODEL.write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n")
    shutil.copyfile(MODEL, LIVE)
    ns.news_model_from_file(LIVE, payload["sha256"])                 # the live loader accepts it
    result["model_sha256"] = payload["sha256"]
    RESULT.write_text(json.dumps(result, indent=1, sort_keys=True) + "\n")
    rs.log(f"wrote {MODEL.relative_to(ROOT)} and {LIVE.relative_to(ROOT)} sha256={payload['sha256']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
