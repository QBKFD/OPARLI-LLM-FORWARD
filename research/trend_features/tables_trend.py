#!/usr/bin/env python3
"""Reporting only: results/results_trend.json -> results/summary_trend.md."""
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
R = json.loads((HERE / "results" / "results_trend.json").read_text())
MODELS = ["MV", "M1+", "MT", "MV+M1+", "MV+MT", "M1+H", "MTH", "MV+MTH"]
PAIRS = ["MV+MT|MV", "MV+M1+|MV", "MV+M1+|MV+MT", "MT|M1+", "MTH|MT", "M1+H|M1+", "MV+MTH|MV+MT"]
L = ["# Trend study: auto-generated tables", "",
     "R² against each fold's training mean (×100 in the tables below, so 1.09 = 0.0109); "
     "MAE in efficiency units. DM: statistic (one-sided p for 'A better'); negative favours A.", ""]
L += ["## Pooled and per era: R² ×100 / MAE", "",
      "| scope | target | test rows | " + " | ".join(MODELS) + " |", "|---|---|---:|" + "---:|" * len(MODELS)]
dmrows = []
for P in ["CONF", "DEV"]:
    for t in ["E4", "E24"]:
        tg = R[P]["targets"][t]
        scopes = [("all", tg["test_rows"], tg["pooled"], tg["dm"])] + \
                 [(e, v["test_rows"], v["pooled"], v["dm"]) for e, v in tg["eras"].items()]
        for sc, n, pm, d in scopes:
            L.append(f"| {P} {sc} | {t} | {n:,} | " +
                     " | ".join(f"{100 * pm[mn]['r2']:.2f} / {pm[mn]['mae']:.4f}" for mn in MODELS) + " |")
            dmrows.append((f"{P} {sc}", t, d))
L += ["", "## Diebold–Mariano", "", "| scope | target | " + " | ".join(p.replace("|", " vs ") for p in PAIRS) + " |",
      "|---|---|" + "---:|" * len(PAIRS)]
for sc, t, d in dmrows:
    L.append(f"| {sc} | {t} | " + " | ".join(f"{d[p]['dm']:.2f} ({d[p]['p_A_better']:.3g})" for p in PAIRS) + " |")
for P in ["CONF", "DEV"]:
    for t in ["E4", "E24"]:
        L += ["", f"## Per block, R² ×100: {P} {t}", "", "| block | n | " + " | ".join(MODELS) + " |",
              "|---|---:|" + "---:|" * len(MODELS)]
        for b in R[P]["targets"][t]["per_block"]:
            L.append(f"| {b['block']} | {b['n']} | " + " | ".join(f"{100 * b[mn + '_r2']:.2f}" for mn in MODELS) + " |")
L += ["", "## Decisions", "", "```json", json.dumps({P: R[P]["decisions"] for P in R}, indent=1, default=str), "```"]
(HERE / "results" / "summary_trend.md").write_text("\n".join(L) + "\n")
print("wrote results/summary_trend.md")
