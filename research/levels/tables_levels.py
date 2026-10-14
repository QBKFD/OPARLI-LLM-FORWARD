#!/usr/bin/env python3
"""Reporting only: results/*.json|csv -> results/summary_levels.md."""
import json
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
R = json.loads((HERE / "results" / "results_levels.json").read_text())


def md(df: pd.DataFrame) -> str:
    fmt = lambda v: f"{v:.4g}" if isinstance(v, float) else str(v)
    lines = ["| " + " | ".join(map(str, df.columns)) + " |", "|" + "---|" * len(df.columns)]
    lines += ["| " + " | ".join(fmt(v) for v in row) + " |" for row in df.itertuples(index=False)]
    return "\n".join(lines)


L = ["# Level study: auto-generated tables", "", f"Info: `{json.dumps(R['info'], default=str)}`", ""]
P = pd.DataFrame(R["primary"])
cols = ["family", "hyp", "conf_n_real", "conf_n_placebo", "conf_real_rate", "conf_placebo_rate", "conf_effect",
        "conf_ci_lo", "conf_ci_hi", "conf_p_one_sided", "bh_p", "era1_effect", "era2_effect",
        "repl_n_real", "repl_effect", "repl_p_one_sided", "supported", "replicated"]
L += ["## Primary tests (CONFIRMATION 2007–2020; replication 2021–2023)", "", md(P[cols]), ""]
L += ["## Mean signed move (σ_d) at +60 / +240 min, CONF: real − placebo", "",
      md(pd.DataFrame([{"test": k, "real60": v["move60"]["real_rate"], "plc60": v["move60"]["placebo_rate"],
                        "eff60": v["move60"]["effect"], "real240": v["move240"]["real_rate"],
                        "plc240": v["move240"]["placebo_rate"], "eff240": v["move240"]["effect"]}
                       for k, v in R["moves"].items()])), ""]
L += ["## Outcome categories (CONF, b = 0.3, H = 240)", "", md(pd.DataFrame(R["categories"])), ""]
L += ["## Robustness: share of grid cells with a positive effect", "",
      md(pd.DataFrame([{"test": k, "share_positive": v} for k, v in R["robustness_share_positive"].items()])), ""]
G = pd.read_csv(HERE / "results" / "robustness_grid.csv")
L += ["## Robustness grid (effect, CONF)", "", md(G), ""]
S = pd.DataFrame(R["secondary"])
L += ["## Secondary (BH across all cells)", "",
      md(S[["kind", "family", "hyp", "dim", "cell", "n_real", "n_placebo", "real_rate", "placebo_rate", "effect",
            "ci_lo", "ci_hi", "p_one_sided", "bh_p"]]), ""]
E = pd.DataFrame(R["exploratory_round"])
L += ["## Exploratory: round-number levels", "", md(E[[c for c in cols if c in E.columns and c not in
                                                      ("bh_p", "supported", "replicated")]]), ""]
C = pd.read_csv(HERE / "results" / "event_counts.csv")
piv = C.pivot_table(index=["year"], columns=["name", "etype"], values="real", aggfunc="sum", fill_value=0)
piv.columns = [f"{a}:{b}" for a, b in piv.columns]
L += ["## Real event counts per level / year (main spec)", "", md(piv.reset_index()), ""]
pivp = C.pivot_table(index=["year"], columns=["name", "etype"], values="placebo", aggfunc="sum", fill_value=0)
pivp.columns = [f"{a}:{b}" for a, b in pivp.columns]
L += ["## Placebo event counts per level / year (main spec)", "", md(pivp.reset_index()), ""]
(HERE / "results" / "summary_levels.md").write_text("\n".join(L) + "\n")
print("wrote results/summary_levels.md")
