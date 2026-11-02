#!/usr/bin/env python3
"""research/news_vol_rep: event and window counts per market, type, year and H, the
calendar check (R3), the EUR-event count (R5) and the power statement.
No outcome: bar open times and calendar timestamps only; no price column is read.

    python research/news_vol_rep/rep_counts.py   # writes results/counts.json and results/counts.md

Windows count here as in Study A's nv_counts.py: the timestamp part of the validity
rule (complete, ends by 2024-01-01, >= 90 % of minutes with a return by the 5-minute
gap rule, not crossing). The run also needs RV > 0 and finite features.
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

import rep_core  # noqa: E402  (sets sys.path for news_vol and sigma_compare)
import nv  # noqa: E402
import nv_counts  # noqa: E402
import rep_data as D  # noqa: E402
import rep_params as V  # noqa: E402
import run_sigma as rs  # noqa: E402

RESULTS = HERE / "results"
RESID_SD = nv_counts.RESID_SD      # Study A's assumption (gold's HAR log-MSE): no silver or EURUSD value is known
DELTAS = nv_counts.DELTAS


def market_counts(market: str, ev: pd.DataFrame, eur: pd.DatetimeIndex) -> dict:
    idx, checks = D.bar_times(market)
    et = idx.tz_convert("America/New_York")
    out: dict = {"bars": checks,
                 "bars_in_metals_closed_minutes": int((~rs.open_flags(idx)).sum()),
                 "bars_in_own_closed_minutes": int((~(rs.open_flags(idx) if market == "XAGUSD"
                                                      else rep_core.fx_open_flags(idx))).sum()),
                 "bars_ny_17h_weekdays": int(((et.hour == 17) & (et.dayofweek <= 4)).sum()),
                 "calendar_agreement": rep_core.calendar_agreement(idx, V.DEV_END_EXCL, V.HORIZONS),
                 "windows": {}, "power": {}, "eur_events": {}}
    is_f = (ev["type"] == "FOMC").to_numpy()
    ev_ns = ev.index.asi8
    test_lo, test_hi = V.FOLD_EDGES[0], V.FOLD_EDGES[-1]
    for H in V.HORIZONS:
        w = nv_counts.window_table(idx, V.DEV_END_EXCL, H)
        use = w["valid"] & ~w["crossing"]
        lab = w.index
        dd, df_ = nv.in_window(lab.asi8, ev_ns[~is_f], H), nv.in_window(lab.asi8, ev_ns[is_f], H)
        ld, lf = nv.in_window_literal(lab.asi8, ev_ns[~is_f], H), nv.in_window_literal(lab.asi8, ev_ns[is_f], H)
        de = nv.in_window(lab.asi8, eur.asi8, H)
        t = pd.DataFrame({"year": lab.year, "use": use.to_numpy(), "data": dd, "fomc": df_, "any": dd | df_,
                          "both": dd & df_, "lit_any": ld | lf, "eur": de,
                          "crossing": w["crossing"].to_numpy(), "valid": w["valid"].to_numpy()}, index=lab)
        per_year = {}
        for y, g in t.groupby("year"):
            u = g[g["use"]]
            per_year[str(y)] = {"origins": int(len(g)), "crossing": int(g["crossing"].sum()),
                                "invalid_noncrossing": int((~g["valid"] & ~g["crossing"]).sum()),
                                "rows": int(len(u)), "data": int(u["data"].sum()), "fomc": int(u["fomc"].sum()),
                                "any": int(u["any"].sum()), "both": int(u["both"].sum()),
                                "none": int((~u["any"]).sum()), "literal_any": int(u["lit_any"].sum())}
        out["windows"][str(H)] = per_year
        test = t[t["use"] & (lab >= test_lo) & (lab < test_hi)]
        out["eur_events"][str(H)] = {"test_rows": int(len(test)), "with_eur_event": int(test["eur"].sum()),
                                     "with_eur_event_and_no_us_release": int((test["eur"] & ~test["any"]).sum()),
                                     "with_eur_event_and_a_us_release": int((test["eur"] & test["any"]).sum())}
        rel_lab = test.index[test["any"]]
        hit = set()
        for tt in rel_lab.asi8:
            a, b = np.searchsorted(ev_ns, tt, "left"), np.searchsorted(ev_ns, tt + H * nv.MIN_NS, "left")
            hit.update(pd.DatetimeIndex(ev_ns[a:b], tz="UTC").date.tolist())    # one cluster per release date
        n_ev, n_other = len(hit), int((~test["any"]).sum())
        n_other_eff = n_other / (H / 60)
        alpha = 0.05 / 8                    # BH's strictest rank of the 8 tests (R4)
        out["power"][str(H)] = {
            "test_rows": int(len(test)), "release_rows": int(test["any"].sum()),
            "release_data": int(test["data"].sum()), "release_fomc": int(test["fomc"].sum()),
            "release_dates_in_release_rows": n_ev, "other_rows": n_other, "other_rows_effective": n_other_eff,
            "resid_sd_assumed": RESID_SD, "alpha_one_sided_bh_rank1_of_8": alpha,
            f"at_alpha_{alpha}": [nv_counts.power(n_ev, n_other_eff, d, RESID_SD, alpha) for d in DELTAS],
            "at_alpha_0.05": [nv_counts.power(n_ev, n_other_eff, d, RESID_SD, 0.05) for d in DELTAS],
            "Q1_mde_80pct_alpha_rank1": float((stats.norm.ppf(1 - alpha) + stats.norm.ppf(0.8))
                                              * RESID_SD * np.sqrt(1 / n_ev + 1 / n_other_eff))}
    return out


def main() -> int:
    ev, info = D.events()
    eur = D.eur_release_minutes()
    out: dict = {"label": "counts only, no outcome", "calendar": info, "events": {}, "markets": {},
                 "eur_release_minutes_dev": int(len(eur))}
    ev_year = ev.assign(year=ev.index.year)
    out["events"]["per_type_year"] = {f"{t}": {str(y): int(n) for y, n in g.groupby("year").size().items()}
                                      for t, g in ev_year.groupby("type")}
    out["events"]["rows_per_group"] = {str(k): int(v) for k, v in ev["n_rows"].value_counts().sort_index().items()}
    out["eur_per_year"] = {str(y): int(n) for y, n in pd.Series(eur.year).value_counts().sort_index().items()}
    for mk in V.MARKETS:
        out["markets"][mk] = market_counts(mk, ev, eur)
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "counts.json").write_text(json.dumps(out, indent=1) + "\n")
    (RESULTS / "counts.md").write_text(counts_md(out))
    print(counts_md(out))
    return 0


def counts_md(o: dict) -> str:
    c = o["calendar"]
    years = range(2016, 2024)
    L = ["# news_vol_rep: counts (no outcome)", "",
         f"Calendar: `{c['calendar_key']}` (sha256 `{c['calendar_sha256'][:12]}…`, PR #60 merged as "
         f"{c['calendar_commit']}), the same file and events as Study A.", "",
         f"- usd_tier1 rows with a timestamp, dev: {c['tier1_rows_timed']}; grouped into {c['events_grouped']} "
         f"release minutes; minutes mixing FOMC and data: {c['mixed_minutes']}.",
         f"- usd_tier1 rows without a timestamp on dev dates (excluded): {c['tier1_rows_untimed_dev']} "
         f"{c['untimed_flags']}.",
         f"- Rows per release minute: {o['events']['rows_per_group']}.",
         f"- EUR High-impact release minutes in dev (R5, counted only, not flagged): {o['eur_release_minutes_dev']} "
         f"{o['eur_per_year']}.", "",
         "## Release minutes per type and year", "", "| type | " + " | ".join(str(y) for y in years) + " |",
         "|---|" + "---|" * 8]
    for t, d in o["events"]["per_type_year"].items():
        L.append(f"| {t} | " + " | ".join(str(d.get(str(y), 0)) for y in years) + " |")
    for mk, m in o["markets"].items():
        b = m["bars"]
        L += ["", f"## {mk}", "",
              f"Bars: {b['rows']:,} dev 1-min bar times, {b['first']} .. {b['last']} (manifest sha256 "
              f"`{b['manifest_sha256'][:12]}…`).", "",
              f"- Bars in the metals calendar's closed minutes: {m['bars_in_metals_closed_minutes']:,} "
              f"(weekday bars in the 17:00 NY hour: {m['bars_ny_17h_weekdays']:,}); in its own calendar's: "
              f"{m['bars_in_own_closed_minutes']:,}."]
        if mk == "EURUSD":
            L.append("- R3, crossing flags of the hourly labels under the metals calendar (build_context's) and "
                     "EURUSD's own (pause 17:00–17:15 NY, Sunday open 17:15): " + "; ".join(
                         f"H = {H}: {a['crossing_metals']:,} vs {a['crossing_fx']:,} crossing, {a['disagree']} differ"
                         for H, a in m["calendar_agreement"].items()) + ".")
        for H, py in m["windows"].items():
            L += ["", f"### {mk}, hourly origins, H = {H} (non-crossing windows valid by timestamps)", "",
                  "| year | origins | crossing | rows | data | FOMC | any | both | none | literal (t, t+H] any |",
                  "|---|---|---|---|---|---|---|---|---|---|"]
            for y, r in py.items():
                L.append(f"| {y} | {r['origins']} | {r['crossing']} | {r['rows']} | {r['data']} | {r['fomc']} | "
                         f"{r['any']} | {r['both']} | {r['none']} | {r['literal_any']} |")
        L += ["", f"### {mk}, EUR High-impact events in test rows 2018–2023 (R5; not flagged)", "",
              "| H | test rows | with an EUR event | of those, no US release | with a US release too |",
              "|---|---|---|---|---|"]
        for H, e in m["eur_events"].items():
            L.append(f"| {H} | {e['test_rows']} | {e['with_eur_event']} | {e['with_eur_event_and_no_us_release']} | "
                     f"{e['with_eur_event_and_a_us_release']} |")
    L += ["", "## Power (rough, analytic; test folds 2018–2023)", "",
          "As Study A: one cluster per release date; residual sd of log(RV/h) "
          f"{RESID_SD}, gold's HAR log-MSE (no value is known for silver or EURUSD). α is BH's strictest rank "
          "of the 8 tests (R4), 0.05/8. Q2 assumes an oracle augmented arm and lognormal residuals, so it is "
          "optimistic.", "",
          "| market | H | release rows (data, FOMC) | release dates | other rows (eff.) | Q1 MDE, 80 % | δ | "
          "Q1 power | Q2 power (oracle) |", "|---|---|---|---|---|---|---|---|---|"]
    for mk, m in o["markets"].items():
        for H, p in m["power"].items():
            key = f"at_alpha_{p['alpha_one_sided_bh_rank1_of_8']}"
            for i, r in enumerate(p[key]):
                lead = (f"| {mk} | {H} | {p['release_rows']} ({p['release_data']}, {p['release_fomc']}) | "
                        f"{p['release_dates_in_release_rows']} | {p['other_rows_effective']:.0f} | "
                        f"{p['Q1_mde_80pct_alpha_rank1']:.3f} |") if i == 0 else "| | | | | | |"
                L.append(f"{lead} {r['delta']} | {r['Q1_power']} | {r['Q2_power_oracle']} |")
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    sys.exit(main())
