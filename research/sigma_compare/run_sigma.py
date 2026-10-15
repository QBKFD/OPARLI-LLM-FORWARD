#!/usr/bin/env python3
"""
Sigma forecast comparison: plug-in (vol_block.sigma_over) vs HAR on
(L, S_hd, T) vs EWMA on deseasonalized 1-min returns.

Specification: PREREG.md (committed c75b24f before any result). Choices made
while implementing: DECISIONS.md. Research only.

    .venv/bin/python research/sigma_compare/run_sigma.py
"""

from __future__ import annotations

import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats
from scipy.signal import lfilter

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
for _p in (ROOT / "research", ROOT / "research" / "vol_features", ROOT / "research" / "vol_features_r3"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
import run_r3 as r3  # noqa: E402  (load_conf)
import run_study as r1  # noqa: E402  (load_minutes, dm_test)
import sealing  # noqa: E402

from datafeed.features import vol_block as vb  # noqa: E402

OUT = HERE / "results"
HORIZONS = (60, 240)
COVERAGE = 0.90
EMBARGO = pd.Timedelta(hours=24)
NW_LAG = 8
BH_Q = 0.05
HALF_LIVES = (30, 60, 120, 240, 480, 960, 1920, 3840, 7680, 15360)
EWMA_BURN = pd.Timedelta(days=60)
KS = (0.5, 1.0, 1.5, 2.0)
CANDS = ("a", "b", "c")
NAMES = {"a": "plug-in", "b": "HAR", "c": "EWMA"}
STUDY_SEAL = pd.Timestamp("2024-01-01", tz="UTC")
GRID_SLACK = pd.Timedelta(days=5)
MIN_NS = 60_000_000_000

PERIODS = {
    "CONF": {
        "load_start": pd.Timestamp("2007-01-01", tz="UTC"),
        "train_start": pd.Timestamp("2007-01-01", tz="UTC"),
        "end": pd.Timestamp("2021-01-01", tz="UTC"),
        "edges": pd.date_range("2009-01-01", "2021-01-01", freq="6MS", tz="UTC"),
        "eras": {"2008-2012": ("2008-01-01", "2013-01-01"), "2013-2020": ("2013-01-01", "2021-01-01")},
        "half": True,
    },
    "DEV": {
        "load_start": pd.Timestamp("2020-01-01", tz="UTC"),
        "train_start": pd.Timestamp("2021-01-01", tz="UTC"),
        "end": STUDY_SEAL,
        "edges": pd.date_range("2022-01-01", "2024-01-01", freq="QS", tz="UTC"),
        "eras": None,
        "half": False,
    },
}


def log(msg):
    r1.log(msg)


# =============================================================================
# Seal and data
# =============================================================================

def assert_sealed(idx, end, what):
    """Global seal (>= 2026-10-01), study seal (>= 2024-01-01) and period end."""
    idx = idx if isinstance(idx, pd.DatetimeIndex) else pd.DatetimeIndex(idx)
    sealing.assert_before_global_seal(idx, what)
    lim = min(pd.Timestamp(end), STUDY_SEAL)
    if len(idx) and (idx >= lim).any():
        raise AssertionError(f"SEALED DATA: {what} has rows at or after {lim}")


def load(name: str) -> pd.DataFrame:
    cfg = PERIODS[name]
    if name == "CONF":
        m = r3.load_conf(start=cfg["load_start"], end=cfg["end"])
    else:
        m, _ = r1.load_minutes(start=cfg["load_start"], end=cfg["end"])
    m = m[["open", "high", "low", "close"]]
    assert_sealed(m.index, cfg["end"], f"{name} 1-min")
    return m


# =============================================================================
# Calendar, windows, targets (fold-independent)
# =============================================================================

def open_flags(index: pd.DatetimeIndex) -> np.ndarray:
    """datafeed _market_open, vectorised (tested equal): Sun 18:00 to Fri 17:00
    New York, without the daily 17:00-18:00 break."""
    et = index.tz_convert("America/New_York")
    wd, h = np.asarray(et.dayofweek), np.asarray(et.hour)
    closed = (wd == 5) | ((wd == 6) & (h < 18)) | ((wd == 4) & (h >= 17)) | (h == 17)
    return ~closed


@dataclass
class Win:
    g0: np.ndarray        # grid index of t
    j: np.ndarray         # grid index one past the window's last minute (clipped)
    E_ns: np.ndarray      # window end
    crossing: np.ndarray
    i0: np.ndarray        # bars opening in [t, E): [i0, i1)
    i1: np.ndarray
    rv: np.ndarray
    nret: np.ndarray
    valid: np.ndarray     # RV usable as a target
    close_ref: np.ndarray
    hi: np.ndarray        # max high / min low over the window's open-minute bars
    lo: np.ndarray


@dataclass
class Ctx:
    m: pd.DataFrame
    end: pd.Timestamp
    labels: pd.DatetimeIndex
    bar_ns: np.ndarray
    r: np.ndarray
    has_r: np.ndarray
    bar_bucket: np.ndarray
    grid_start: pd.Timestamp
    grid_open: np.ndarray
    grid_bucket: np.ndarray
    win: dict


def windows(CO: np.ndarray, g0: np.ndarray, H: int):
    """First H open minutes from grid index g0: j = one past the last one."""
    return np.searchsorted(CO, CO[g0] + H, "left")


def build_context(m: pd.DataFrame, end) -> Ctx:
    m = vb._check(m)
    if m.index.unit != "ns":
        m = m.set_axis(m.index.as_unit("ns"))
    end = pd.Timestamp(end)
    bar_ns = m.index.asi8
    r = vb._returns(m).to_numpy()
    has_r = ~np.isnan(r)
    bar_open = open_flags(m.index)
    labels = vb._bar_labels(m.index)
    grid = pd.date_range(labels[0], end + GRID_SLACK, freq="1min", inclusive="left")
    grid_open = open_flags(grid)
    CO = np.concatenate([[0], np.cumsum(grid_open)])
    g0 = (labels.asi8 - grid[0].value) // MIN_NS
    ok_r = bar_open & has_r
    CR = np.concatenate([[0.0], np.cumsum(np.where(ok_r, r ** 2, 0.0))])
    CN = np.concatenate([[0], np.cumsum(ok_r)])
    close = m["close"].to_numpy()
    hi_open = np.where(bar_open, m["high"].to_numpy(), -np.inf)
    lo_open = np.where(bar_open, m["low"].to_numpy(), np.inf)
    win = {}
    for H in HORIZONS:
        j = windows(CO, g0, H)
        complete = j <= len(grid)
        j = np.minimum(j, len(grid))
        E_ns = grid[0].value + j * MIN_NS
        i0 = np.searchsorted(bar_ns, labels.asi8, "left")
        i1 = np.searchsorted(bar_ns, E_ns, "left")
        rv, nret = CR[i1] - CR[i0], CN[i1] - CN[i0]
        valid = complete & (E_ns <= end.value) & (nret >= COVERAGE * H) & (rv > 0) & (i0 > 0)
        close_ref = np.where(i0 > 0, close[np.maximum(i0 - 1, 0)], np.nan)
        hi, lo = np.full(len(labels), np.nan), np.full(len(labels), np.nan)
        for k in np.flatnonzero(valid):
            hi[k] = hi_open[i0[k]:i1[k]].max()
            lo[k] = lo_open[i0[k]:i1[k]].min()
        win[H] = Win(g0, j, E_ns, (j - g0) > H, i0, i1, rv, nret, valid, close_ref, hi, lo)
    return Ctx(m, end, labels, bar_ns, r, has_r, vb._buckets(m.index), grid[0], grid_open,
               vb._buckets(grid), win)


# =============================================================================
# Candidates
# =============================================================================

def ewma(x: np.ndarray, lam: float, v0: float = 1.0) -> np.ndarray:
    """v_i = lam·v_{i-1} + (1 - lam)·x_i, v_{-1} = v0 (scipy lfilter)."""
    y, _ = lfilter([1.0 - lam], [1.0, -lam], x, zi=[lam * v0])
    return y


def ewma_naive(x, lam, v0=1.0):
    out, v = np.empty(len(x)), v0
    for i, xi in enumerate(x):
        v = lam * v + (1.0 - lam) * xi
        out[i] = v
    return out


def qlike(rv, h):
    q = np.asarray(rv) / np.asarray(h)
    return q - np.log(q) - 1.0


@dataclass
class FoldFit:
    s_k: pd.Timestamp
    params: vb.VolParams
    X: np.ndarray                 # [1, L, S_hd, T] at every label
    ok_x: np.ndarray
    P: dict                       # H -> seasonal mass at every label
    lev: dict                     # half-life -> EWMA level at every label
    model: dict                   # H -> beta, c, half_life, n_train, train mask, train QLIKE per half-life


def fit_fold(ctx: Ctx, cfg: dict, s_k: pd.Timestamp, noncross_only: bool = False) -> FoldFit:
    """noncross_only: fit HAR and choose lambda on non-crossing training rows only
    (PREREG AMENDMENT (a), post hoc). The default is the pre-registered fit."""
    m, labels = ctx.m, ctx.labels
    tr_end = s_k - EMBARGO
    params = vb.fit(m[(m.index >= cfg["train_start"]) & (m.index < tr_end)])
    assert pd.Timestamp(params.train_end) <= tr_end
    feats = vb.transform(params, m)
    assert feats.index.equals(labels)
    X = np.column_stack([np.ones(len(labels)), feats["L"], feats["S_hd"], feats["T"]])
    ok_x = np.isfinite(X).all(axis=1)
    prof = np.asarray(params.profile, dtype=float)
    CW = np.concatenate([[0.0], np.cumsum(np.where(ctx.grid_open, prof[ctx.grid_bucket], 0.0))])
    P = {H: CW[ctx.win[H].j] - CW[ctx.win[H].g0] for H in HORIZONS}

    x = ctx.r[ctx.has_r] ** 2 / prof[ctx.bar_bucket[ctx.has_r]]
    q = np.searchsorted(ctx.bar_ns[ctx.has_r], labels.asi8, "left") - 1
    lev = {}
    for hl in HALF_LIVES:
        y = ewma(x, 2.0 ** (-1.0 / hl))
        lev[hl] = np.where(q >= 0, y[np.maximum(q, 0)], 1.0)

    model = {}
    burn = ctx.m.index[0] + EWMA_BURN
    for H in HORIZONS:
        w = ctx.win[H]
        train = ((labels >= cfg["train_start"]) & (labels <= tr_end) & (w.E_ns <= s_k.value)
                 & w.valid & ok_x)
        if noncross_only:
            train &= ~w.crossing
        y = np.log(w.rv[train] / P[H][train])
        beta, *_ = np.linalg.lstsq(X[train], y, rcond=None)
        c = float(np.mean(np.exp(y - X[train] @ beta)))
        tr_l = train & (labels >= burn)
        if not tr_l.any():
            raise ValueError(f"no EWMA training rows after the {EWMA_BURN} burn-in (fold {s_k})")
        qtr = {hl: float(np.mean(qlike(w.rv[tr_l], lev[hl][tr_l] * P[H][tr_l]))) for hl in HALF_LIVES}
        best = None
        for hl in sorted(HALF_LIVES, reverse=True):          # ties -> the longer half-life
            if best is None or qtr[hl] < qtr[best]:
                best = hl
        model[H] = {"beta": beta, "c": c, "half_life": best, "n_train": int(train.sum()),
                    "n_train_ewma": int(tr_l.sum()), "train": train, "qlike_train": qtr}
    return FoldFit(s_k, params, X, ok_x, P, lev, model)


def forecasts(fit: FoldFit, H: int) -> dict:
    mod, P = fit.model[H], fit.P[H]
    with np.errstate(invalid="ignore", over="ignore"):
        return {"a": np.exp(fit.X[:, 1]) * P,
                "b": P * np.exp(fit.X @ mod["beta"]) * mod["c"],
                "c": fit.lev[mod["half_life"]] * P}


def fold_rows(ctx: Ctx, fit: FoldFit, H: int, e_k: pd.Timestamp, fold: int, block: str,
              era: str) -> pd.DataFrame:
    labels, w = ctx.labels, ctx.win[H]
    sel = (labels >= fit.s_k) & (labels < e_k)
    h = forecasts(fit, H)
    f = pd.DataFrame({"fold": fold, "block": block, "era": era,
                      "crossing": w.crossing[sel], "valid": w.valid[sel], "ok_x": fit.ok_x[sel],
                      "rv": w.rv[sel], "E_ns": w.E_ns[sel], "P": fit.P[H][sel],
                      "L": fit.X[sel, 1], "S_hd": fit.X[sel, 2], "T": fit.X[sel, 3],
                      "close_ref": w.close_ref[sel], "hi": w.hi[sel], "lo": w.lo[sel],
                      **{f"h_{c}": h[c][sel] for c in CANDS}}, index=labels[sel])
    f.index.name = "t"
    return f


def common_rows(f: pd.DataFrame) -> pd.DataFrame:
    hs = f[[f"h_{c}" for c in CANDS]].to_numpy()
    keep = (f.valid & f.ok_x & np.isfinite(hs).all(axis=1) & (hs > 0).all(axis=1)
            & np.isfinite(f.close_ref)).to_numpy()
    return f[keep]


# =============================================================================
# Periods
# =============================================================================

def block_label(ts, half):
    return f"{ts.year}H{1 + (ts.month > 6)}" if half else f"{ts.year}Q{(ts.month - 1) // 3 + 1}"


def era_of(ts, eras):
    if not eras:
        return "all"
    for name, (a, b) in eras.items():
        if pd.Timestamp(a, tz="UTC") <= ts < pd.Timestamp(b, tz="UTC"):
            return name
    return "other"


def run_period(name: str, cfg: dict, m: pd.DataFrame, crosscheck: int = 0,
               noncross_only: bool = False) -> dict:
    ctx = build_context(m, cfg["end"])
    edges = cfg["edges"]
    rows = {H: [] for H in HORIZONS}
    coefs, pattern, n_checked = [], None, 0
    rng = np.random.default_rng(20261014)
    for k in range(len(edges) - 1):
        s_k, e_k = edges[k], edges[k + 1]
        fit = fit_fold(ctx, cfg, s_k, noncross_only)
        pat = fit.ok_x[ctx.labels >= cfg["train_start"]]
        if pattern is None:
            pattern = pat
        assert (pat == pattern).all(), "missingness of L, S_hd, T depends on the profile"
        blk, era = block_label(s_k, cfg["half"]), era_of(s_k, cfg["eras"])
        for H in HORIZONS:
            f = fold_rows(ctx, fit, H, e_k, k + 1, blk, era)
            rows[H].append(f)
            mod = fit.model[H]
            assert (ctx.win[H].E_ns[mod["train"]] <= s_k.value).all()
            assert (ctx.labels[mod["train"]] <= s_k - EMBARGO).all()
            coefs.append({"period": name, "fold": k + 1, "block": blk, "H": H,
                          **dict(zip(["b0", "bL", "bS_hd", "bT"], map(float, mod["beta"]))),
                          "smear_c": mod["c"], "half_life": mod["half_life"],
                          "n_train": mod["n_train"], "n_train_ewma": mod["n_train_ewma"]})
            if crosscheck:
                n_checked += check_plugin(ctx, fit, common_rows(f), H, crosscheck, rng)
        log(f"  {name} fold {k + 1} {blk}: " + ", ".join(
            f"H={H} hl={fit.model[H]['half_life']} b={np.round(fit.model[H]['beta'], 3).tolist()}"
            for H in HORIZONS))
    out = {"coefs": coefs, "rows": {}, "n_plugin_checked": n_checked}
    for H in HORIZONS:
        f = pd.concat(rows[H]).sort_index()
        assert_sealed(f.index, cfg["end"], f"{name} H={H} rows")
        c = common_rows(f)
        assert (c.E_ns <= cfg["end"].value).all()
        out["rows"][H] = c
        out[f"n_raw_{H}"] = len(f)
    return out


def check_plugin(ctx, fit, f, H, n, rng) -> int:
    """h_a == sigma_over(state_at(t), H).sigma_ret² on non-crossing rows (rel. 1e-9).
    Returns the number of rows checked."""
    nc = f[~f.crossing]
    done = 0
    if nc.empty:
        return done
    for t in nc.index[rng.choice(len(nc), min(n, len(nc)), replace=False)]:
        sl = ctx.m[(ctx.m.index >= t - pd.Timedelta(days=21)) & (ctx.m.index < t)]
        st = vb.state_at(fit.params, sl, t)
        if st.reason is not None:
            continue
        s = vb.sigma_over(fit.params, st, H)
        assert not s.crosses_close and s.open_minutes == H, (t, H)
        assert math.isclose(s.sigma_ret ** 2, nc.at[t, "h_a"], rel_tol=1e-9), (t, H)
        done += 1
    return done


# =============================================================================
# Evaluation
# =============================================================================

NORMAL_TOUCH = {k: float(2 * (1 - stats.norm.cdf(k))) for k in KS}


def metrics(g: pd.DataFrame) -> dict:
    out = {"n": len(g)}
    rv = g.rv.to_numpy()
    for c in CANDS:
        h = g[f"h_{c}"].to_numpy()
        ratio = rv / h
        sig = g.close_ref.to_numpy() * np.sqrt(h)
        d = {"qlike": float(np.mean(qlike(rv, h))),
             "logmse": float(np.mean((np.log(rv) - np.log(h)) ** 2)),
             "cal_mean": float(np.mean(ratio)), "cal_median": float(np.median(ratio)),
             "cal_p10": float(np.quantile(ratio, 0.10)), "cal_p90": float(np.quantile(ratio, 0.90)),
             "cal_pooled": float(rv.sum() / h.sum())}
        for k in KS:
            d[f"up_{k}"] = float(np.mean(g.hi.to_numpy() >= g.close_ref.to_numpy() + k * sig))
            d[f"dn_{k}"] = float(np.mean(g.lo.to_numpy() <= g.close_ref.to_numpy() - k * sig))
        out[c] = d
    return out


def dm(g: pd.DataFrame, a: str, b: str, loss: str = "qlike") -> dict:
    """DM on d = loss_a - loss_b (rows in time order); p = one-sided 'a better'."""
    g = g.sort_index()
    rv = g.rv.to_numpy()
    if loss == "qlike":
        la, lb = qlike(rv, g[f"h_{a}"]), qlike(rv, g[f"h_{b}"])
    else:
        la = (np.log(rv) - np.log(g[f"h_{a}"])) ** 2
        lb = (np.log(rv) - np.log(g[f"h_{b}"])) ** 2
    d = np.asarray(la) - np.asarray(lb)
    if not np.any(d != 0):                 # identical losses: no evidence either way
        return {"stat": 0.0, "p": 1.0, "mean_d": 0.0}
    s, p = r1.dm_test(np.asarray(la), np.asarray(lb), NW_LAG)
    return {"stat": s, "p": p, "mean_d": float(np.mean(d))}


def bh(pvals: dict, q: float = BH_Q) -> dict:
    """Benjamini-Hochberg: adjusted p and reject flag per key."""
    keys = sorted(pvals, key=lambda k: pvals[k])
    m = len(keys)
    adj, running = {}, 1.0
    for rank in range(m, 0, -1):
        k = keys[rank - 1]
        running = min(running, pvals[k] * m / rank)
        adj[k] = running
    return {k: {"p_adj": adj[k], "reject": adj[k] <= q} for k in pvals}


def verdict(rows: dict) -> dict:
    """The pre-registered rule on CONF non-crossing rows."""
    tests, means = {}, {}
    for H in HORIZONS:
        g = rows[H][~rows[H].crossing]
        for x in ("b", "c"):
            tests[(x, H)] = dm(g, x, "a")
        means[H] = {c: float(np.mean(qlike(g.rv, g[f"h_{c}"]))) for c in CANDS}
    adj = bh({k: v["p"] for k, v in tests.items()})
    sig = {k: adj[k]["reject"] and tests[k]["mean_d"] < 0 for k in tests}
    winners = [x for x in ("b", "c") if all(sig[(x, H)] for H in HORIZONS)]
    if not winners:
        choice, why = "a", "no challenger is significantly better on QLIKE at both horizons"
    elif len(winners) == 1:
        choice, why = winners[0], "significantly better on QLIKE at both horizons"
    else:
        avg = {x: np.mean([means[H][x] for H in HORIZONS]) for x in winners}
        choice = min(avg, key=avg.get)
        why = "both challengers qualify; lower mean QLIKE over the two horizons"
    return {"tests": {f"{x}|H{H}": {**tests[(x, H)], **adj[(x, H)], "significant": sig[(x, H)]}
                      for (x, H) in tests},
            "qlike": {f"H{H}": means[H] for H in HORIZONS},
            "choice": choice, "choice_name": NAMES[choice], "why": why}


def evaluate(name: str, cfg: dict, res: dict) -> dict:
    ev = {}
    for H in HORIZONS:
        f = res["rows"][H]
        for kind, g in (("noncross", f[~f.crossing]), ("cross", f[f.crossing])):
            if g.empty:
                continue
            e = {"all": metrics(g), "dm": {f"{x}|a": dm(g, x, "a") for x in ("b", "c")}}
            e["dm"]["b|c"] = dm(g, "b", "c")
            e["dm_logmse"] = {f"{x}|a": dm(g, x, "a", "logmse") for x in ("b", "c")}
            if cfg["eras"]:
                e["eras"] = {era: {"metrics": metrics(ge),
                                   "dm": {f"{x}|a": dm(ge, x, "a") for x in ("b", "c")}}
                             for era, ge in g.groupby("era")}
            e["folds"] = [{"fold": int(k), "block": b, "n": len(gk),
                           **{c: float(np.mean(qlike(gk.rv, gk[f"h_{c}"]))) for c in CANDS}}
                          for (k, b), gk in g.groupby(["fold", "block"])]
            ev[f"H{H}_{kind}"] = e
    return ev


def main() -> int:
    OUT.mkdir(exist_ok=True)
    allres, coefs = {}, []
    for name, cfg in PERIODS.items():
        log(f"=== {name} ===")
        m = load(name)
        log(f"  1-min rows {len(m):,}")
        res = run_period(name, cfg, m, crosscheck=50)
        coefs += res["coefs"]
        ev = evaluate(name, cfg, res)
        allres[name] = {"rows": {f"H{H}": {"all": res[f"n_raw_{H}"], "common": len(res["rows"][H]),
                                           "noncross": int((~res["rows"][H].crossing).sum()),
                                           "cross": int(res["rows"][H].crossing.sum())}
                                 for H in HORIZONS},
                        "n_plugin_checked": res["n_plugin_checked"], "eval": ev}
        if name == "CONF":
            allres["verdict"] = verdict(res["rows"])
        for H in HORIZONS:
            res["rows"][H].drop(columns=["E_ns"]).to_csv(OUT / f"rows_{name}_H{H}.csv.gz",
                                                         float_format="%.8g")
    allres["normal_touch"] = NORMAL_TOUCH
    pd.DataFrame(coefs).to_csv(OUT / "coefs.csv", index=False, float_format="%.6f")
    (OUT / "results_sigma.json").write_text(json.dumps(allres, indent=2, default=float))
    v = allres["verdict"]
    log(f"VERDICT: {v['choice_name']} ({v['why']})")
    for k, t in v["tests"].items():
        log(f"  {k}: mean_d={t['mean_d']:.5f} DM={t['stat']:.2f} p={t['p']:.3g} "
            f"p_adj={t['p_adj']:.3g} significant={t['significant']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
