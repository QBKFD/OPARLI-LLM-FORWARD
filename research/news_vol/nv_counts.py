#!/usr/bin/env python3
"""research/news_vol (Study A): event and window counts, and the power statement.
No outcome: bar open times and calendar timestamps only; no price column is read.

    python research/news_vol/nv_counts.py      # writes results/counts.json and results/counts.md

A window counts here when it passes the timestamp part of the validity rule
(complete, ends by 2024-01-01, >= 90 % of its minutes with a 1-min return by the
5-minute gap rule, does not cross a close). The run also needs RV > 0 and finite
L, S_hd and T, which can drop a few more rows; the run reports its own counts.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import nv  # noqa: E402
import nv_core  # noqa: E402,F401  (sets sys.path for run_sigma)
import nv_data as D  # noqa: E402
import nv_params as V  # noqa: E402
import run_sigma as rs  # noqa: E402

from backend.datafeed.features import vol_block as vb  # noqa: E402

RESULTS = HERE / "results"
RESID_SD = 0.55     # sd of log(RV / HAR forecast) at H = 240: sqrt(log-MSE) 0.30 CONF, 0.25 DEV in sigma_compare
DELTAS = (0.1, 0.2, 0.3, 0.5)


def window_table(idx: pd.DatetimeIndex, end: pd.Timestamp, H: int) -> pd.DataFrame:
    """Hourly origins with their window's timestamp-only validity (sigma_compare's rules)."""
    bar_ns = idx.asi8
    labels = vb._bar_labels(idx)
    grid = pd.date_range(labels[0], end + rs.GRID_SLACK, freq="1min", inclusive="left")
    CO = np.concatenate([[0], np.cumsum(rs.open_flags(grid))])
    g0 = (labels.asi8 - grid[0].value) // nv.MIN_NS
    j = rs.windows(CO, g0, H)
    complete = j <= len(grid)
    j = np.minimum(j, len(grid))
    E_ns = grid[0].value + j * nv.MIN_NS
    dt = np.diff(bar_ns, prepend=bar_ns[0] - 10 * nv.MIN_NS)
    ok_r = rs.open_flags(idx) & (dt <= 5 * nv.MIN_NS) & (np.arange(len(idx)) > 0)
    CN = np.concatenate([[0], np.cumsum(ok_r)])
    i0 = np.searchsorted(bar_ns, labels.asi8, "left")
    i1 = np.searchsorted(bar_ns, E_ns, "left")
    nret = CN[i1] - CN[i0]
    crossing = (j - g0) > H
    valid = complete & (E_ns <= end.value) & (nret >= V.COVERAGE * H) & (i0 > 0)
    return pd.DataFrame({"valid": valid, "crossing": crossing}, index=labels)


def power(n_events: int, n_other_eff: float, delta: float, s: float, alpha_one: float) -> dict:
    """Rough, analytic: one row per event (the overlapping windows of one event count once).
    Q1: difference in mean log(RV/h) of delta, one-sided. Q2: the augmented arm removes a
    log bias delta on release rows exactly (an oracle); y ~ N(delta, s^2) there;
    d = q(y - delta) - q(y) with q(y) = e^y - y - 1; two-sided at 2 alpha_one."""
    se1 = s * np.sqrt(1.0 / n_events + 1.0 / n_other_eff)
    p1 = float(stats.norm.cdf(delta / se1 - stats.norm.ppf(1 - alpha_one)))
    a = np.exp(-delta) - 1.0
    ed = np.exp(delta + s ** 2 / 2) * a + delta
    sd = abs(a) * np.sqrt((np.exp(s ** 2) - 1.0) * np.exp(2 * delta + s ** 2))
    p2 = float(stats.norm.cdf(np.sqrt(n_events) * abs(ed) / sd - stats.norm.ppf(1 - alpha_one)))
    return {"delta": delta, "Q1_power": round(p1, 3), "Q2_power_oracle": round(p2, 3)}


def main() -> int:
    idx, checks = D.bar_times()
    ev, info = D.events()
    out: dict = {"label": "counts only, no outcome", "bars": checks, "calendar": info, "events": {}, "windows": {},
                 "power": {}}
    ev_year = ev.assign(year=ev.index.year)
    out["events"]["per_type_year"] = {f"{t}": {str(y): int(n) for y, n in g.groupby("year").size().items()}
                                      for t, g in ev_year.groupby("type")}
    out["events"]["rows_per_group"] = {str(k): int(v) for k, v in ev["n_rows"].value_counts().sort_index().items()}
    out["events"]["unscheduled_fomc_in_set"] = [str(t) for t in ev.index
                                                if t in (pd.Timestamp("2020-03-03 15:00", tz="UTC"),
                                                         pd.Timestamp("2020-03-15 21:00", tz="UTC"))]
    is_f = (ev["type"] == "FOMC").to_numpy()
    ev_ns = ev.index.asi8
    test_lo, test_hi = V.FOLD_EDGES[0], V.FOLD_EDGES[-1]
    for H in V.HORIZONS:
        w = window_table(idx, V.DEV_END_EXCL, H)
        use = w["valid"] & ~w["crossing"]
        lab = w.index
        dd, df_ = nv.in_window(lab.asi8, ev_ns[~is_f], H), nv.in_window(lab.asi8, ev_ns[is_f], H)
        ld, lf = nv.in_window_literal(lab.asi8, ev_ns[~is_f], H), nv.in_window_literal(lab.asi8, ev_ns[is_f], H)
        t = pd.DataFrame({"year": lab.year, "use": use.to_numpy(), "train_or_test": lab >= V.DEV_START,
                          "data": dd, "fomc": df_, "any": dd | df_, "both": dd & df_,
                          "lit_data": ld, "lit_fomc": lf, "lit_any": ld | lf, "crossing": w["crossing"].to_numpy(),
                          "valid": w["valid"].to_numpy()}, index=lab)
        per_year = {}
        for y, g in t.groupby("year"):
            u = g[g["use"]]
            per_year[str(y)] = {"origins": int(len(g)), "crossing": int(g["crossing"].sum()),
                                "invalid_noncrossing": int((~g["valid"] & ~g["crossing"]).sum()),
                                "rows": int(len(u)), "data": int(u["data"].sum()), "fomc": int(u["fomc"].sum()),
                                "any": int(u["any"].sum()), "both": int(u["both"].sum()),
                                "none": int((~u["any"]).sum()),
                                "literal_data": int(u["lit_data"].sum()), "literal_fomc": int(u["lit_fomc"].sum()),
                                "literal_any": int(u["lit_any"].sum())}
        out["windows"][str(H)] = per_year
        test = t[t["use"] & (lab >= test_lo) & (lab < test_hi)]
        rel_lab = test.index[test["any"]]
        hit = set()
        for tt in rel_lab.asi8:
            a, b = np.searchsorted(ev_ns, tt, "left"), np.searchsorted(ev_ns, tt + H * nv.MIN_NS, "left")
            hit.update(ev_ns[a:b].tolist())
        n_ev, n_other = len(hit), int((~test["any"]).sum())
        n_other_eff = n_other / (H / 60)
        out["power"][str(H)] = {
            "test_rows": int(len(test)), "release_rows": int(test["any"].sum()), "events_in_release_rows": n_ev,
            "other_rows": n_other, "other_rows_effective": n_other_eff, "resid_sd_assumed": RESID_SD,
            "alpha_one_sided_bh_rank1_of_4": 0.05 / 4,
            "at_alpha_0.0125": [power(n_ev, n_other_eff, d, RESID_SD, 0.05 / 4) for d in DELTAS],
            "at_alpha_0.05": [power(n_ev, n_other_eff, d, RESID_SD, 0.05) for d in DELTAS],
            "Q1_mde_80pct_alpha_0.0125": float((stats.norm.ppf(1 - 0.05 / 4) + stats.norm.ppf(0.8))
                                               * RESID_SD * np.sqrt(1 / n_ev + 1 / n_other_eff))}
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "counts.json").write_text(json.dumps(out, indent=1) + "\n")
    (RESULTS / "counts.md").write_text(counts_md(out))
    print(counts_md(out))
    return 0


def counts_md(o: dict) -> str:
    L = ["# news_vol: counts (no outcome)", "",
         f"Bars: {o['bars']['rows']:,} XAUUSD dev 1-min bar times (manifest sha256 "
         f"`{o['bars']['manifest_sha256'][:12]}…`). Calendar: `{o['calendar']['calendar_key']}` "
         f"(sha256 `{o['calendar']['calendar_sha256'][:12]}…`, PR #60 merged as {o['calendar']['calendar_commit']}).",
         "", f"- usd_tier1 rows with a timestamp, dev: {o['calendar']['tier1_rows_timed']}; grouped into "
         f"{o['calendar']['events_grouped']} release minutes; minutes mixing FOMC and data: {o['calendar']['mixed_minutes']}.",
         f"- usd_tier1 rows without a timestamp on dev dates (excluded): {o['calendar']['tier1_rows_untimed_dev']} "
         f"{o['calendar']['untimed_flags']}.",
         f"- Rows per release minute: {o['events']['rows_per_group']}.",
         f"- Unscheduled FOMC actions in the set: {o['events']['unscheduled_fomc_in_set']}.", "",
         "## Release minutes per type and year", "", "| type | " + " | ".join(str(y) for y in range(2016, 2024)) + " |",
         "|---|" + "---|" * 8]
    for t, d in o["events"]["per_type_year"].items():
        L.append(f"| {t} | " + " | ".join(str(d.get(str(y), 0)) for y in range(2016, 2024)) + " |")
    for H, py in o["windows"].items():
        L += ["", f"## Hourly origins, H = {H} (non-crossing windows valid by timestamps)", "",
              "| year | origins | crossing | rows | data | FOMC | any | both | none | literal (t, t+H] any |",
              "|---|---|---|---|---|---|---|---|---|---|"]
        for y, c in py.items():
            L.append(f"| {y} | {c['origins']} | {c['crossing']} | {c['rows']} | {c['data']} | {c['fomc']} | "
                     f"{c['any']} | {c['both']} | {c['none']} | {c['literal_any']} |")
    L += ["", "## Power (rough, analytic; test folds 2018–2023)", "",
          "One row per event (an event's overlapping windows count once); residual sd of log(RV/h) "
          f"{RESID_SD} (sigma_compare's HAR log-MSE, seen data); Q2 assumes an oracle augmented arm.", "",
          "| H | release rows | events | other rows (eff.) | Q1 MDE, 80 %, α 0.0125 | δ | Q1 power | Q2 power (oracle) |",
          "|---|---|---|---|---|---|---|---|"]
    for H, p in o["power"].items():
        for i, r in enumerate(p["at_alpha_0.0125"]):
            lead = (f"| {H} | {p['release_rows']} | {p['events_in_release_rows']} | {p['other_rows_effective']:.0f} | "
                    f"{p['Q1_mde_80pct_alpha_0.0125']:.3f} |") if i == 0 else "| | | | | |"
            L.append(f"{lead} {r['delta']} | {r['Q1_power']} | {r['Q2_power_oracle']} |")
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    sys.exit(main())
