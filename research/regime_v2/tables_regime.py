#!/usr/bin/env python3
"""Reporting only: results/*.json|csv -> results/summary_regime.md."""
import json
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
R = json.loads((HERE / "results" / "results_regime.json").read_text())
MAIN = ["JM-A", "JM-B", "BASE-L3", "BASE-ADX20", "BASE-ADX25", "BASE-ADX30", "CONT"]
SENS = ["JM-A-K2", "JM-A-K4", "JM-A-x0.5", "JM-A-x2"]
GRID = [0, 0.5, 1, 2, 5, 10, 20, 50, 100, 200]


def md(df: pd.DataFrame, index: bool = True) -> str:
    """Markdown table without tabulate (not installed)."""
    d = df.reset_index() if index else df
    fmt = lambda v: f"{v:.4g}" if isinstance(v, float) else str(v)
    lines = ["| " + " | ".join(map(str, d.columns)) + " |", "|" + "---|" * len(d.columns)]
    lines += ["| " + " | ".join(fmt(v) for v in row) + " |" for row in d.itertuples(index=False)]
    return "\n".join(lines)
L = ["# Regime v2: auto-generated tables", "",
     "R² ×100 against the unconditional training mean (BASE-0). DM: statistic "
     "(one-sided p for 'A better'); negative favours A.", ""]


def r2row(scope, o, ev, cols):
    return f"| {scope} | {o} | {ev['rows']:,} | " + " | ".join(f"{100 * ev['r2'][c]:.2f}" for c in cols) + " |"


L += ["## Pooled OOS R² ×100 (main labelings)", "", "| scope | outcome | test rows | " + " | ".join(MAIN) + " |",
      "|---|---|---:|" + "---:|" * len(MAIN)]
for P in ["CONF", "DEV"]:
    for o in ["O1", "O2", "O3"]:
        L.append(r2row(f"{P} all", o, R[P]["eval"][o], MAIN))
        for era, ev in R[P]["eras"].items():
            L.append(r2row(f"{P} {era}", o, ev[o], MAIN))
L += ["", "## Sensitivity labelings, R² ×100", "", "| scope | outcome | " + " | ".join(SENS + [f"λ={g:g}" for g in GRID]) + " |",
      "|---|---|" + "---:|" * (len(SENS) + len(GRID))]
for P in ["CONF", "DEV"]:
    for o in ["O1", "O2", "O3"]:
        ev = R[P]["eval"][o]
        L.append(f"| {P} | {o} | " + " | ".join(f"{100 * ev['r2'][c]:.2f}" for c in SENS + [f"JM-A-lam{g:g}" for g in GRID]) + " |")
pairs = list(R["CONF"]["eval"]["O1"]["dm"].keys())
L += ["", "## Diebold–Mariano", "", "| scope | outcome | " + " | ".join(p.replace("|", " vs ") for p in pairs) + " |",
      "|---|---|" + "---:|" * len(pairs)]
for P in ["CONF", "DEV"]:
    for o in ["O1", "O2", "O3"]:
        scopes = [("all", R[P]["eval"][o])] + [(e, ev[o]) for e, ev in R[P]["eras"].items()]
        for sc, ev in scopes:
            L.append(f"| {P} {sc} | {o} | " + " | ".join(
                f"{ev['dm'][p]['dm']:.2f} ({ev['dm'][p]['p_A_better']:.3g})" for p in pairs) + " |")
for P in ["CONF", "DEV"]:
    for o in ["O1", "O2"]:
        L += ["", f"## Per block R² ×100: {P} {o}", "", "| block | n | " + " | ".join(MAIN) + " |",
              "|---|---:|" + "---:|" * len(MAIN)]
        for b in R[P]["eval"][o]["per_block"]:
            L.append(f"| {b['block']} | {b['n']} | " + " | ".join(f"{100 * b[c]:.2f}" for c in MAIN) + " |")
for P in ["CONF", "DEV"]:
    lam = pd.read_csv(HERE / "results" / f"lambda_{P}.csv")
    L += ["", f"## Chosen λ per fold: {P}", "", md(lam[["block", "lam_A3", "lam_B3", "lam_A2", "lam_A4"]], index=False)]
    L += ["", f"## Regime descriptions (test rows): {P}", "",
          md(pd.read_csv(HERE / "results" / f"regime_desc_{P}.csv").groupby("regime")[["n", "mean_L", "mean_S_hd", "mean_T"]]
          .agg({"n": "sum", "mean_L": "mean", "mean_S_hd": "mean", "mean_T": "mean"}))]
    L += ["", f"## Strategies by regime: {P}", "",
          md(pd.read_csv(HERE / "results" / f"strategies_{P}.csv"), index=False)]
L += ["", "## Decisions, S4 and robustness", "", "```json",
      json.dumps({P: {"decisions": R[P]["decisions"], "s4": R[P]["s4"], "robust_dir": R[P]["robust_dir"]} for P in R},
                 indent=1, default=str), "```"]
(HERE / "results" / "summary_regime.md").write_text("\n".join(L) + "\n")
print("wrote results/summary_regime.md")
