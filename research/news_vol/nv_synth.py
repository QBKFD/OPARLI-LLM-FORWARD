"""research/news_vol (Study A): synthetic 1-min bars and the machinery check (PREREG 1c).

No real price is read. The bars live on the market-open minutes of the sigma_compare
calendar (Sun 18:00 to Fri 17:00 New York, without the daily 17:00-18:00 break).
Per-minute variance = SIGMA_MIN^2 x profile(UTC hour) x exp(x_hour) x M(u):
- profile(h) = 1 + 1.5 exp(-((h - 14) / 2.5)^2), a fixed intraday shape;
- x_hour: an hourly AR(1) of log variance (phi SYN_AR, innovation sd SYN_AR_SD);
- M(u) = SYN_EFFECT_MULT for the SYN_EFFECT_MIN minutes from each grouped event's
  minute in the "effect" world, 1 everywhere in the "null" world.
Both worlds share every random draw (one seed); only M differs.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

import nv
import nv_core
import nv_params as V
import run_sigma as rs


@dataclass
class Truth:
    grid_ns: np.ndarray
    cum_var: np.ndarray        # cumulative per-minute variance, with M
    cum_var0: np.ndarray       # the same without M

    def log_ratio(self, t_ns: np.ndarray, H: int) -> np.ndarray:
        """log(true variance with the effect / without) over the minutes [t, t+H)."""
        i0 = np.searchsorted(self.grid_ns, t_ns, "left")
        i1 = np.searchsorted(self.grid_ns, t_ns + H * nv.MIN_NS, "left")
        return np.log((self.cum_var[i1] - self.cum_var[i0]) / (self.cum_var0[i1] - self.cum_var0[i0]))


def synth_bars(start, end, events: pd.DataFrame, effect: bool, seed: int = V.SYN_SEED,
               mult: float = V.SYN_EFFECT_MULT, minutes: int = V.SYN_EFFECT_MIN) -> tuple[pd.DataFrame, Truth]:
    full = pd.date_range(start, end, freq="1min", inclusive="left")
    grid = full[rs.open_flags(full)]
    rng = np.random.default_rng(seed)
    hours = pd.date_range(pd.Timestamp(start).floor("1h"), end, freq="1h")
    eta = rng.standard_normal(len(hours))
    x = np.empty(len(hours))
    x[0] = 0.0
    for i in range(1, len(hours)):
        x[i] = V.SYN_AR * x[i - 1] + V.SYN_AR_SD * eta[i]
    hi = np.searchsorted(hours.asi8, grid.floor("1h").asi8)
    h_utc = np.asarray(grid.hour)
    var0 = V.SYN_SIGMA_MIN ** 2 * (1.0 + 1.5 * np.exp(-((h_utc - 14) / 2.5) ** 2)) * np.exp(x[hi])
    M = np.ones(len(grid))
    if effect and len(events):
        ev = pd.DatetimeIndex(events.index).asi8
        g = grid.asi8
        for e in ev:
            a, b = np.searchsorted(g, e, "left"), np.searchsorted(g, e + minutes * nv.MIN_NS, "left")
            M[a:b] = mult
    var = var0 * M
    r = np.sqrt(var) * rng.standard_normal(len(grid))
    close = V.SYN_PRICE0 * np.exp(np.cumsum(r))
    opn = np.concatenate([[V.SYN_PRICE0], close[:-1]])
    m = pd.DataFrame({"open": opn, "high": np.maximum(opn, close), "low": np.minimum(opn, close), "close": close},
                     index=grid)
    m.index.name = "timestamp"
    tr = Truth(grid.asi8, np.concatenate([[0.0], np.cumsum(var)]), np.concatenate([[0.0], np.cumsum(var0)]))
    return m, tr


def machinery_check(events: pd.DataFrame, cfg: nv_core.Config, B: int = V.BOOT_B, seed: int = V.SYN_SEED,
                    mult: float = V.SYN_EFFECT_MULT, minutes: int = V.SYN_EFFECT_MIN) -> dict:
    """PREREG section 6. Effect world: Q1 > 0 with p < 0.05 and Q1 / truth in SYN_RECOVER;
    the augmented arm lower with DM p < 0.05 at both H. Null world: DM p > 0.05 at both H."""
    start = min(cfg.train_start, cfg.edges[0]) - pd.Timedelta(days=7)
    out: dict = {"seed": seed, "mult": mult, "minutes": minutes, "B": B}
    for world in ("effect", "null"):
        m, truth = synth_bars(start, cfg.end, events, world == "effect", seed, mult, minutes)
        res = nv_core.run(m, events, cfg)
        a = nv_core.analyse(res, cfg.horizons[0], B, seed, V.NW_LAG, V.BH_Q)
        f = res.rows[cfg.horizons[0]]
        tl = truth.log_ratio(f.index.asi8, cfg.horizons[0])
        true_diff = float(tl[f["rel"].to_numpy()].mean() - tl[~f["rel"].to_numpy()].mean())
        aug = [c for c in res.coefs if c["arm"] == "aug"]
        out[world] = {"Q1": a["Q1"], "Q2": a["Q2"], "Q3": a["Q3"], "true_Q1_diff": true_diff,
                      "aug_coef_data": [c["d_data"] for c in aug], "aug_coef_fomc": [c["d_fomc"] for c in aug],
                      "rows": {str(H): int(len(g)) for H, g in res.rows.items()},
                      "release_rows": {str(H): int(g["rel"].sum()) for H, g in res.rows.items()}}
    e, n = out["effect"], out["null"]
    ratio = e["Q1"]["estimate"] / e["true_Q1_diff"] if e["true_Q1_diff"] > 0 else float("nan")
    lo, hi = V.SYN_RECOVER
    checks = {
        "effect_Q1_positive_p<0.05": e["Q1"]["estimate"] > 0 and e["Q1"]["p_one_sided_gt0"] < 0.05,
        "effect_Q1_recovers_truth": bool(lo <= ratio <= hi),
        **{f"effect_Q2_H{H}_aug_lower_p<0.05": e["Q2"][str(H)]["mean_d"] < 0 and e["Q2"][str(H)]["p"] < 0.05
           for H in cfg.horizons},
        **{f"null_Q2_H{H}_p>0.05": n["Q2"][str(H)]["p"] > 0.05 for H in cfg.horizons},
    }
    out["Q1_ratio_to_truth"] = ratio
    out["checks"] = {k: bool(v) for k, v in checks.items()}
    out["pass"] = all(checks.values())
    return out
