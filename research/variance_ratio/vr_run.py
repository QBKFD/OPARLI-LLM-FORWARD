#!/usr/bin/env python3
"""research/variance_ratio: run the descriptive analysis once (EXPLORATORY).

    python research/variance_ratio/vr_run.py [--markets XAUUSD,...] [--no-bump]

Bumps ATTEMPTS.json first (kind descriptive_analysis), then per market: dev
1-min bars -> base returns with exclusions -> VR(k) per cell -> BH across every
cell of every market -> results/ (CSV, JSON, one heatmap per market) and
results/tables.md. Writes aggregates only: VR, z, p, n and counts; no price.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import vr  # noqa: E402
import vr_data as D  # noqa: E402
import vr_params as V  # noqa: E402

RESULTS = HERE / "results"
ATTEMPTS = HERE / "ATTEMPTS.json"
SPLITS = ("all", "hour_utc", "session", "sigma240_tercile", "weekday_utc", "news_day")
WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def bump(what: str) -> int:
    d = json.loads(ATTEMPTS.read_text())
    d["outcome_analyses"] += 1
    d["log"].append({"n": len(d["log"]) + 1, "kind": "descriptive_analysis", "date": date.today().isoformat(),
                     "what": what, "by": "owner brief, 2026-10-31 (unattended run)"})
    ATTEMPTS.write_text(json.dumps(d, indent=1) + "\n")
    return d["outcome_analyses"]


def cell_masks(slots: pd.DatetimeIndex, sig: np.ndarray, edges, news: set | None) -> list[tuple[str, str, np.ndarray]]:
    hour = slots.hour.to_numpy()
    out = [("all", "all", np.ones(len(slots), bool))]
    out += [("hour_utc", f"{h:02d}", hour == h) for h in range(24)]
    sess = vr.session_of(hour, V.SESSIONS_UTC)
    out += [("session", name, sess == name) for name, _, _ in V.SESSIONS_UTC]
    t = np.where(np.isfinite(sig), np.digitize(sig, edges), -1)
    out += [("sigma240_tercile", name, t == i) for i, name in enumerate(("low", "mid", "high"))]
    wd = slots.dayofweek.to_numpy()
    out += [("weekday_utc", WEEKDAYS[d], wd == d) for d in range(7)]
    if news is not None:
        nd = np.isin(slots.tz_convert("UTC").date, list(news))
        out += [("news_day", "news", nd), ("news_day", "no_news", ~nd)]
    return out


def run_market(market: str, news: set | None) -> tuple[list[dict], dict]:
    m, checks = D.load(market)
    sig_lab = D.sigma_240(m)
    seam = V.GOLD_SEAM if market == "XAUUSD" else None
    rows, info = [], {"checks": checks}
    for base, bmin in V.BASES.items():
        slots, r, counts = vr.slot_returns(m, bmin, V.TAIL_MIN[base], V.GAP_MIN, V.SHORT_DAY_ROWS, V.ROLL_NY_HOUR,
                                           seam, V.DEV_START, V.DEV_END_EXCL)
        info[f"exclusions_{base}"] = {k: int(v) for k, v in counts.items()}
        mu = float(np.nanmean(r))
        e = np.where(np.isfinite(r), r - mu, 0.0)
        if base == "1h":
            sig = sig_lab.reindex(slots).to_numpy(float)        # the label at the window's start time
            edges = D.tercile_edges(sig[np.isfinite(r)])
            info["sigma240_tercile_edges"] = edges
            info["slots_without_sigma"] = int((np.isfinite(r) & ~np.isfinite(sig)).sum())
            cells = cell_masks(slots, sig, edges, news)
        else:
            cells = [("all", "all", np.ones(len(slots), bool))]   # V9: the 5-min check on the whole sample
        for k in V.KS_HOURS:
            q = k * 60 // bmin
            ok = vr.window_starts(r, q)
            info.setdefault(f"windows_{base}", {})[str(k)] = int(ok.sum())
            for split, cell, mask in cells:
                res = vr.vr_cell(e, ok & mask, q)
                rows.append({"market": market, "base": base, "split": split, "cell": cell, "k_hours": k, **res})
    return rows, info


def heatmap(df: pd.DataFrame, market: str, path: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.colors import LinearSegmentedColormap, TwoSlopeNorm

    h = df[(df["market"] == market) & (df["base"] == "1h") & (df["split"] == "hour_utc")]
    vrs = h.pivot(index="cell", columns="k_hours", values="vr").reindex(columns=list(V.KS_HOURS))
    sig = h.pivot(index="cell", columns="k_hours", values="p_bh").reindex(columns=list(V.KS_HOURS))
    val = np.log2(vrs.to_numpy(float))
    lim = max(np.nanmax(np.abs(val)) if np.isfinite(val).any() else 0.1, 0.05)
    cmap = LinearSegmentedColormap.from_list("div", ["#2a78d6", "#f0efec", "#e34948"])
    cmap.set_bad("#fcfcfb")
    fig, ax = plt.subplots(figsize=(4.6, 8.2), facecolor="#fcfcfb")
    ax.set_facecolor("#fcfcfb")
    im = ax.imshow(np.ma.masked_invalid(val), cmap=cmap, norm=TwoSlopeNorm(0, -lim, lim), aspect="auto")
    for (i, j), v in np.ndenumerate(vrs.to_numpy(float)):
        star = "*" if np.isfinite(sig.iat[i, j]) and sig.iat[i, j] < V.BH_Q else ""
        ax.text(j, i, f"{v:.2f}{star}" if np.isfinite(v) else "n/a", ha="center", va="center",
                fontsize=7, color="#0b0b0b" if np.isfinite(v) else "#52514e")
    ax.set_xticks(range(len(V.KS_HOURS)), [f"k={k}h" for k in V.KS_HOURS], fontsize=8, color="#52514e")
    ax.set_yticks(range(len(vrs)), [f"{c}:00" for c in vrs.index], fontsize=7, color="#52514e")
    ax.set_ylabel("window start, UTC hour", fontsize=8, color="#52514e")
    ax.set_title(f"{market}: VR(k), 1h base, dev 2016-2023\nEXPLORATORY · * BH-adjusted p < {V.BH_Q}",
                 fontsize=9, color="#0b0b0b")
    for s in ax.spines.values():
        s.set_visible(False)
    cb = fig.colorbar(im, ax=ax, fraction=0.05, pad=0.03)
    ticks = [-lim, 0, lim]
    cb.set_ticks(ticks, labels=[f"{2 ** t:.2f}" for t in ticks])
    cb.ax.tick_params(labelsize=7, colors="#52514e")
    cb.outline.set_visible(False)
    cb.set_label("VR (log scale; 1 = random walk)", fontsize=7, color="#52514e")
    fig.tight_layout()
    fig.savefig(path, dpi=150, facecolor="#fcfcfb")
    plt.close(fig)


def table_md(df: pd.DataFrame, market: str) -> str:
    d = df[(df["market"] == market)]
    lines = [f"### {market}", "", "Each cell: VR / z / n (windows); `*` BH-adjusted p < 0.05. EXPLORATORY.", "",
             "| base | split | cell | " + " | ".join(f"k = {k} h" for k in V.KS_HOURS) + " |",
             "|---|---|---|" + "---|" * len(V.KS_HOURS)]
    for (base, split, cell), g in d.groupby(["base", "split", "cell"], sort=False):
        g = g.set_index("k_hours")
        cells = []
        for k in V.KS_HOURS:
            x = g.loc[k]
            if not np.isfinite(x["vr"]):
                cells.append(f"n/a (n {int(x['n'])})")
                continue
            star = "*" if x["p_bh"] < V.BH_Q else ""
            cells.append(f"{x['vr']:.3f}{star} / {x['z']:+.2f} / {int(x['n'])}")
        lines.append(f"| {base} | {split} | {cell} | " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--markets", default=",".join(V.MARKETS))
    ap.add_argument("--calendar", type=Path, default=ROOT / D.CALENDAR_KEY)
    a = ap.parse_args(argv)
    markets = a.markets.split(",")
    prereg = HERE / "PREREG.md"
    status = subprocess.run(["git", "status", "--porcelain", "--", str(prereg)], cwd=ROOT, capture_output=True,
                            text=True, check=True).stdout
    if status.strip():
        raise SystemExit("PREREG.md (the analysis plan) must be committed as it is before the run")
    n = bump(f"variance ratios, dev 2016-2023, markets {','.join(markets)}: all cells, BH across all (EXPLORATORY)")
    news, news_info = None, {"skipped": "calendar missing"}
    if a.calendar.exists():
        news, news_info = D.news_dates(a.calendar)
    rows, infos = [], {}
    for mk in markets:
        print(f"{mk} ...", flush=True)
        rr, info = run_market(mk, news)
        rows += rr
        infos[mk] = info
    df = pd.DataFrame(rows)
    df["p_bh"] = vr.bh(df["p"].to_numpy())
    RESULTS.mkdir(exist_ok=True)
    df.to_csv(RESULTS / "vr_cells.csv", index=False, float_format="%.6g")
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    summary = {"label": "EXPLORATORY, descriptive: no hypothesis, no pass/fail", "attempt": n, "git_head": head,
               "plan_sha256": hashlib.sha256(prereg.read_bytes()).hexdigest(), "news": news_info,
               "cells": int(len(df)), "cells_with_p": int(df["p"].notna().sum()),
               "bh_q_lt_005": int((df["p_bh"] < V.BH_Q).sum()), "markets": infos}
    (RESULTS / "summary.json").write_text(json.dumps(summary, indent=1, default=str) + "\n")
    md = "".join(table_md(df, mk) + "\n" for mk in markets)
    (RESULTS / "tables.md").write_text(md)
    for mk in markets:
        heatmap(df, mk, RESULTS / f"heatmap_{mk}.png")
    print(f"done: {len(df)} cells, {summary['bh_q_lt_005']} with BH p < {V.BH_Q}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
