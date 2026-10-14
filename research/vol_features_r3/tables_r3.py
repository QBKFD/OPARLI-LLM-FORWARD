#!/usr/bin/env python3
"""Reporting only: turns results/results_r3.json into results/summary_r3.md."""

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
R = json.loads((HERE / "results" / "results_r3.json").read_text())
MODELS = ["M1", "M1H", "MC", "MCH"]
T = ["y4_ds", "y24_ds"]


def pooled_table(pooled_by_scope, title):
    L = [f"### {title}", "", "| scope | target | rows (test) | BENCH Q | " +
         " | ".join(f"{m} R² / Q" for m in MODELS) + " |",
         "|---|---|---:|---:|" + "---:|" * len(MODELS)]
    for scope, (t, n, p) in pooled_by_scope:
        L.append(f"| {scope} | {t} | {n:,} | {p['BENCH']['qlike']:.3f} | " +
                 " | ".join(f"{p[m]['r2']:.3f} / {p[m]['qlike']:.3f}" for m in MODELS) + " |")
    return L


def dm_table(rows, title):
    L = [f"### {title}", "", "| scope | target | " +
         " | ".join(["M1H vs MC", "MCH vs MC", "MC vs M1", "M1H vs M1"]) + " |",
         "|---|---|" + "---:|" * 4]
    keys = ["M1H|MC", "MCH|MC", "MC|M1", "M1H|M1"]
    for scope, t, d in rows:
        L.append(f"| {scope} | {t} | " + " | ".join(
            f"{d[k]['dm']:.2f} ({d[k]['p_A_better']:.3g})" for k in keys) + " |")
    return L


L = ["# Round 3 auto-generated tables", "",
     "R² against each fold's training mean; Q = QLIKE on the variance scale (lower is better). "
     "DM: statistic (one-sided p for 'A better'); negative favours A.", ""]
pooled, dms = [], []
for P in ["CONF", "DEV"]:
    for t in T:
        tg = R[P]["targets"][t]
        pooled.append((f"{P} all", (t, tg["test_rows"], tg["pooled"])))
        dms.append((f"{P} all", t, tg["dm"]))
        for era, e in tg["eras"].items():
            pooled.append((f"{P} {era}", (t, e["test_rows"], e["pooled"])))
            dms.append((f"{P} {era}", t, e["dm"]))
L += pooled_table(pooled, "Pooled and per era") + [""]
L += dm_table(dms, "Diebold–Mariano") + [""]
for P in ["CONF", "DEV"]:
    for t in T:
        L += [f"### Per block: {P} {t}", "", "| block | n | " + " | ".join(MODELS) + " | BENCH Q | MC Q | M1H Q |",
              "|---|---:|" + "---:|" * (len(MODELS) + 3)]
        for b in R[P]["targets"][t]["per_block"]:
            L.append(f"| {b['block']} | {b['n']} | " + " | ".join(f"{b[m + '_r2']:.3f}" for m in MODELS) +
                     f" | {b['BENCH_q']:.3f} | {b['MC_q']:.3f} | {b['M1H_q']:.3f} |")
        L.append("")
L += ["### Decisions", "", "```json", json.dumps({P: R[P]["decisions"] for P in R}, indent=1), "```"]
(HERE / "results" / "summary_r3.md").write_text("\n".join(L) + "\n")
print("wrote results/summary_r3.md")
