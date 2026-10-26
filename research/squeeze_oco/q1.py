"""Q1 of research/squeeze_oco, before any outcome: the small-candle score
(Q1_SMALL_DEF option B), its walk-forward quantile threshold, the runs, their
counts and the rule that picks (q, r). Used by 01_vol_state.ipynb and
q1_runs.py.

No value lives here, as in stage1.py: every number comes in as an argument,
and the notebook takes it from its register through need(id). Nothing here
reads a forward return or an outcome; ATTEMPTS.json is not touched.

Option B (owner, 2026-10-26; PROPOSALS.md "Q1_SMALL_DEF"):
    score = range / sigma_tf,  range = high - low of the candle (price),
    sigma_tf² = var_60 × P_candle / P_60
              = P_candle × exp(β_60 · [1, L, S_hd, T]) × c_60
    with the HAR state (L, S_hd, T) of the latest hourly label at or before
    the candle's start (vol_block.state_at at the start), P_60 the model's
    seasonal mass over the next 60 open minutes from there (as sigma_over),
    and P_candle the same profile summed over the candle's open minutes. The
    level factor exp(β·x)·c is the same for both, so sigma_tf² is HAR's level
    times the candle's own seasonal mass. sigma_tf is in price units: × the
    close of the last bar before the candle's start (vol_block's close_ref).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

import stage1
from stage1 import rs, vb


# ----------------------------------------------------------------- candles and their score

def candles(m: pd.DataFrame, minutes: int) -> pd.DataFrame:
    """Complete `minutes`-minute candles of UTC buckets aligned to the hour
    (all `minutes` 1-min bars present, as stage1.candle_counts): start, end,
    high, low, range. Index: the candle's start."""
    if 60 % minutes:
        raise ValueError(f"{minutes} doesn't divide the hour")
    g = m.groupby(m.index.floor(f"{minutes}min"))
    c = pd.DataFrame({"n": g.size(), "high": g["high"].max(), "low": g["low"].min()})
    c = c[c["n"] == minutes].drop(columns="n")
    c.index.name = "start"
    c.insert(0, "end", c.index + pd.Timedelta(minutes=minutes))
    c["range"] = c["high"] - c["low"]
    return c


def candle_scores(m: pd.DataFrame, ctx: rs.Ctx, model: vb.SigmaModel, minutes: int, level_H: int) -> pd.DataFrame:
    """Option B for every complete candle: t0 (the state's hourly label), the
    candle's seasonal mass, sigma_tf (return and price) and score = range /
    sigma_tf_px. A candle without a state at t0 (warm-up, NaN features, no
    label) has score NaN. No price row is returned: high, low and close_ref
    are dropped."""
    beta, c_ = model.horizons[level_H]
    X, ok_x, _ = stage1.features(ctx, model)
    c = candles(m, minutes)
    s = c.index
    j = np.searchsorted(ctx.labels.asi8, s.asi8, "right") - 1        # latest label <= start
    has = (j >= 0)
    jj = np.maximum(j, 0)
    has &= ok_x[jj]
    with np.errstate(invalid="ignore", over="ignore"):
        level = np.exp(X[jj] @ np.asarray(beta)) * c_
    # the candle's open minutes and their seasonal mass under the model's profile
    mins = pd.DatetimeIndex((s.asi8[:, None] + np.arange(minutes)[None, :] * rs.MIN_NS).ravel(), tz="UTC")
    prof = np.asarray(model.params.profile, dtype=float)
    w = np.where(rs.open_flags(mins), prof[vb._buckets(mins)], 0.0).reshape(len(s), minutes)
    mass = w.sum(axis=1)
    closed = (w == 0).sum(axis=1)
    i = np.searchsorted(m.index.asi8, s.asi8, "left") - 1                 # last bar opening before the start
    close_ref = np.where(i >= 0, m["close"].to_numpy()[np.maximum(i, 0)], np.nan)
    sig_ret = np.where(has, np.sqrt(mass * level), np.nan)
    sig_px = close_ref * sig_ret
    out = pd.DataFrame({"end": c["end"], "range": c["range"],
                        "t0": ctx.labels[jj].where(j >= 0), "mass": mass, "closed_minutes": closed,
                        "sigma_tf_ret": sig_ret, "sigma_tf_px": sig_px}, index=s)
    with np.errstate(invalid="ignore", divide="ignore"):
        out["score"] = np.where(has, c["range"].to_numpy() / sig_px, np.nan)
    return out


# ----------------------------------------------------------------- threshold (walk-forward by year, per hour)

def hours(index: pd.DatetimeIndex, hour_tz: str | None) -> np.ndarray:
    """The clock hour of each timestamp in hour_tz (the threshold's group), or
    -1 for every row when hour_tz is None (one pooled threshold)."""
    if hour_tz is None:
        return np.full(len(index), -1)
    return np.asarray(index.tz_convert(hour_tz).hour)


def thresholds(scores: pd.Series, q: float, years: list[int], hour_tz: str | None) -> dict[tuple[int, int], float]:
    """For each year Y in `years` and each hour h (the candle start's clock hour
    in hour_tz; -1 if pooled): the q-quantile (numpy's default, linear) of the
    finite scores of the candles that start at hour h in the earlier years of
    `years` (expanding, as run_sigma's folds). Fitted on past years only, never
    on Y. The first year has no earlier year: NaN."""
    s = scores[np.isfinite(scores)]
    yr, hr = s.index.year, hours(s.index, hour_tz)
    v = s.to_numpy()
    out = {}
    for h in sorted(set(hr.tolist())):
        for Y in years:
            tr = v[(yr >= years[0]) & (yr < Y) & (hr == h)]
            out[(Y, h)] = float(np.quantile(tr, q)) if len(tr) else float("nan")
    return out


def small(sc: pd.DataFrame, thr: dict[tuple[int, int], float], by: pd.DatetimeIndex, hour_tz: str | None) -> np.ndarray:
    """score <= the threshold of (the year of `by`, the candle start's hour in
    hour_tz). `by` holds one timestamp per candle: the year in which the
    candle's run is scored. sc's index is the candles' starts."""
    t = np.array([thr.get((y, h), np.nan) for y, h in zip(by.year, hours(pd.DatetimeIndex(sc.index), hour_tz))])
    with np.errstate(invalid="ignore"):
        return np.isfinite(t) & (sc["score"].to_numpy() <= t)


# ----------------------------------------------------------------- runs

def runs_at_labels(sc: pd.DataFrame, labels: pd.DatetimeIndex, thr: dict, minutes: int, r: int,
                   hour_tz: str | None) -> pd.Series:
    """For each hourly label T: True when the r candles ending at T (starts
    T - k·minutes, k = 1..r) are all complete, scored and small, with the
    thresholds of T's year. The run covers the last r·minutes of the hour
    before T, so with r·minutes <= 60 runs at different labels share no candle."""
    if r * minutes > 60:
        raise ValueError(f"r·minutes = {r * minutes} > 60: a run would reach past the hour before its label")
    ok = pd.Series(True, index=labels)
    for k in range(1, r + 1):
        st = labels - pd.Timedelta(minutes=k * minutes)
        x = sc.reindex(st)
        ok &= small(x, thr, labels, hour_tz)
    return ok.rename(f"run_r{r}")


def sliding_runs(sc: pd.DataFrame, thr: dict, minutes: int, r: int, hour_tz: str | None) -> int:
    """Descriptive: candle ends (at any `minutes` boundary) where the last r
    candles are consecutive, complete and small (thresholds of the candle's own
    year). These overlap each other."""
    sm = pd.Series(small(sc, thr, pd.DatetimeIndex(sc.index), hour_tz), index=sc.index)
    full = sm.reindex(pd.date_range(sc.index.min(), sc.index.max(), freq=f"{minutes}min"), fill_value=False)
    return int((full.astype(int).rolling(r).sum() == r).sum())


def clustering(run_ends: pd.DatetimeIndex, trade_dates: pd.DatetimeIndex, spacings_min: tuple,
               quantiles: tuple) -> dict:
    """How clustered the run ends are: gaps between consecutive runs (hours),
    the share whose previous hourly label is also a run, the longest chain of
    consecutive hourly runs, trade dates with runs, runs per UTC hour of the
    label, and for each spacing S (min)
    the runs kept by a greedy pass that keeps a run only S or more minutes after
    the last kept one."""
    t = pd.DatetimeIndex(run_ends).sort_values()
    out: dict = {"runs": len(t)}
    if len(t) == 0:
        return out
    gap_h = np.diff(t.asi8) / 3.6e12
    out["gap_hours_quantiles"] = {str(p): float(np.quantile(gap_h, p)) for p in quantiles} if len(gap_h) else {}
    out["share_previous_label_also_run"] = float(np.mean(gap_h == 1.0)) if len(gap_h) else 0.0
    chain, best = 1, 1
    for g in gap_h:
        chain = chain + 1 if g == 1.0 else 1
        best = max(best, chain)
    out["longest_chain_hours"] = best
    td = pd.Series(trade_dates)
    per = td.value_counts()
    out["trade_dates_with_runs"] = int(len(per))
    out["runs_per_trade_date_max"] = int(per.max())
    out["runs_per_trade_date_share_1"] = float(np.mean(per == 1))
    out["runs_per_utc_hour_of_label"] = {int(h): int(n) for h, n in pd.Series(t.hour).value_counts().sort_index().items()}
    for S in spacings_min:
        kept, last = 0, None
        for x in t.asi8:
            if last is None or x - last >= S * rs.MIN_NS:
                kept, last = kept + 1, x
        out[f"greedy_spaced_{S}min"] = kept
    return out


# ----------------------------------------------------------------- the counts the rule reads

def event_counts(sc: pd.DataFrame, grid: pd.DataFrame, years: list[int], qs: tuple, r_values: tuple,
                 minutes: int, spacings_min: tuple, quantiles: tuple, hour_tz: str | None) -> dict:
    """Counts only, per (q, r): the walk-forward thresholds, and per year of the
    run's label: runs at the grid's labels (candle-disjoint by construction),
    the same restricted to labels whose H-window doesn't cross a close (a
    calendar fact, no return read), and the overlapping sliding runs at any
    candle end; then totals, runs per hour of the run's candles in hour_tz
    (UTC when hour_tz is None), and clustering over all years. `grid` is
    stage1.state_table's frame (labels with a state) with its crossing_H columns."""
    import audit_long
    out: dict = {"thresholds": {}, "per_year": {}, "totals": {}, "per_hour": {}, "clustering": {}}
    for q in qs:
        thr = thresholds(sc["score"], q, years, hour_tz)
        out["thresholds"][q] = thr
        for r in r_values:
            run = runs_at_labels(sc, grid.index, thr, minutes, r, hour_tz)
            ends = grid.index[run.to_numpy()]
            yrs = {}
            for Y in years:
                inY = ends.year == Y
                row = {"runs": int(inY.sum())}
                for col in [c for c in grid.columns if c.startswith("crossing_")]:
                    row[f"runs_noncrossing_{col.split('_')[1]}"] = int((inY & ~grid.loc[ends, col].to_numpy().astype(bool)).sum())
                row["sliding_runs_overlapping"] = sliding_runs(sc[sc.index.year == Y], thr, minutes, r, hour_tz)
                yrs[Y] = row
            out["per_year"][(q, r)] = yrs
            out["totals"][(q, r)] = {k: sum(v[k] for v in yrs.values()) for k in next(iter(yrs.values()))}
            first = ends - pd.Timedelta(minutes=minutes)               # the run's last candle: same clock hour as all its candles
            hr = pd.Series(np.asarray(first.tz_convert(hour_tz or "UTC").hour))
            cross = {col.split("_")[1]: grid.loc[ends, col].to_numpy().astype(bool) for col in grid.columns if col.startswith("crossing_")}
            out["per_hour"][(q, r)] = {int(h): {"runs": int((hr == h).sum()),
                                                **{f"runs_noncrossing_{H}": int(((hr == h).to_numpy() & ~c).sum()) for H, c in cross.items()}}
                                       for h in sorted(hr.unique())}
            td = audit_long.session_date((ends - pd.Timedelta(minutes=minutes)).tz_localize(None))
            out["clustering"][(q, r)] = clustering(ends, td, spacings_min, quantiles)
    return out


# ----------------------------------------------------------------- the owner's (q, r) rule

def choose(counts: dict, r_order: tuple, q_order: tuple, min_runs: int) -> dict:
    """The rule decided before the counts (owner, 2026-10-26): for r in r_order,
    the first q in q_order (strictest first) with counts[(q, r)] >= min_runs;
    None if no pair qualifies (stop: too little data)."""
    for r in r_order:
        for q in q_order:
            if counts[(q, r)] >= min_runs:
                return {"q": q, "r": r, "n": counts[(q, r)]}
    return {"q": None, "r": None, "n": None}
