"""
Level behaviour study: PDH/PDL/WH/WL vs placebo levels (measurement study).

Specification: PREREG.md (committed before any result). Decisions: DECISIONS.md.
Every real and placebo level goes through the same function, `run_unit`.

    /Users/x/Desktop/algo_project/backend/venv/bin/python3 research/levels/levels.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
for p in ("vol_features", "vol_features_r2", "vol_features_r3", "vol_block"):
    sys.path.insert(0, str(ROOT / "research" / p))
import run_r3 as r3  # noqa: E402  (XAUUSD.txt loader + cache, seal helpers)
from vol_block import VolBlock  # noqa: E402

OUT = HERE / "results"
FIG = HERE / "figures"
SEAL = pd.Timestamp("2024-01-01", tz="UTC")
NY = "America/New_York"
MIN = 60_000_000_000                       # one minute in ns
EXPECTED_DAY_MIN = 1380
MIN_COVERAGE = 0.80
FILL = 0.10
FILLS = (0.0, 0.10, 0.30)
BS = (0.2, 0.3, 0.5)
HS = (60, 240)
B_MAIN, H_MAIN = 0.3, 240
ELIG = 0.1
BREAK_THR = 0.1
PIERCE_THR = 0.05
RECLAIM_MIN = 30
N_PLACEBO = 20
PLC_LO, PLC_HI, PLC_EXCL = 0.2, 1.0, 0.15
COST_SIDE = 0.30
PATH_OFFSETS = np.arange(-30, 241)
PERIODS = {"CONF": (2007, 2020), "ERA1": (2007, 2013), "ERA2": (2014, 2020), "REPL": (2021, 2023)}
HYP = {"TOUCH": ("H1", -1), "BREAK": ("H2", +1), "SWEEP": ("H3", -1)}   # (hypothesis, target direction)
FAMILIES_PRIMARY = ("DAILY", "WEEKLY")
N_BOOT = 5000


def log(msg):
    r3.r1.log(msg)


def assert_sealed_ns(ts_ns, what):
    if len(ts_ns) and np.max(ts_ns) >= SEAL.value:
        raise AssertionError(f"SEALED DATA: {what} has rows at or after {SEAL.date()}")


# =============================================================================
# Calendar: trading days and weeks
# =============================================================================

def trading_dates(ts: pd.DatetimeIndex) -> np.ndarray:
    """New York trading date: the session 17:00 -> 17:00 NY ends on this date."""
    local = ts.tz_convert(NY).tz_localize(None) + pd.Timedelta(hours=7)
    return local.normalize().values.astype("datetime64[D]")


def day_table(ts: pd.DatetimeIndex, o, h, l, c) -> pd.DataFrame:
    td = trading_dates(ts)
    starts = np.r_[0, np.flatnonzero(td[1:] != td[:-1]) + 1]
    ends = np.r_[starts[1:] - 1, len(td) - 1]
    d = pd.DataFrame({"date": td[starts], "i0": starts, "i1": ends, "n": ends - starts + 1})
    d["open"] = o[starts]
    d["high"] = np.maximum.reduceat(h, starts)
    d["low"] = np.minimum.reduceat(l, starts)
    dow = pd.DatetimeIndex(d["date"]).dayofweek
    d = d[dow < 5].reset_index(drop=True)
    d["excluded"] = d["n"] < MIN_COVERAGE * EXPECTED_DAY_MIN
    iso = pd.DatetimeIndex(d["date"]).isocalendar()
    d["week"] = (iso["year"].astype(int) * 100 + iso["week"].astype(int)).to_numpy()
    return d


def attach_levels(days: pd.DataFrame) -> pd.DataFrame:
    """PDH/PDL from the previous non-excluded trading day; WH/WL from the previous week."""
    d = days.copy()
    prev_ok = ~d["excluded"].shift(1, fill_value=True)
    d["PDH"] = np.where(prev_ok, d["high"].shift(1), np.nan)
    d["PDL"] = np.where(prev_ok, d["low"].shift(1), np.nan)
    w = d.groupby("week").agg(high=("high", "max"), low=("low", "min"), n=("n", "sum")).reset_index()
    w["ok"] = w["n"] >= MIN_COVERAGE * 5 * EXPECTED_DAY_MIN
    w["WH"] = np.where(w["ok"].shift(1, fill_value=False), w["high"].shift(1), np.nan)
    w["WL"] = np.where(w["ok"].shift(1, fill_value=False), w["low"].shift(1), np.nan)
    return d.merge(w[["week", "WH", "WL"]], on="week", how="left")


# =============================================================================
# 5-minute bars and the volatility unit
# =============================================================================

def five_min(ts_ns: np.ndarray, c: np.ndarray):
    b = ts_ns // (5 * MIN)
    last = np.r_[np.flatnonzero(b[1:] != b[:-1]), len(b) - 1]
    bidx = np.r_[0, np.cumsum(b[1:] != b[:-1])]
    return {"end": (b[last] + 1) * 5 * MIN, "close": c[last], "last": last, "of_minute": bidx}


def sigma_factor(m: pd.DataFrame, years):
    """Per-minute factor f with σ_d = price · f, and L at each minute.
    Annual expanding VolBlock fits (PREREG)."""
    ts = m.index
    td_year = pd.DatetimeIndex(trading_dates(ts)).year.to_numpy()
    local = ts.tz_convert(NY).tz_localize(None)
    dst = np.asarray((local - ts.tz_localize(None)) == pd.Timedelta(hours=-4))
    f = np.full(len(m), np.nan)
    Lmin = np.full(len(m), np.nan)
    fit_start = pd.Timestamp("2007-01-01", tz="UTC")
    for Y in years:
        fit_end = pd.Timestamp(f"{max(Y, 2009)}-01-01", tz="UTC")
        vb = VolBlock().fit(m[(ts >= fit_start) & (ts < fit_end)])
        sub = m[(ts >= pd.Timestamp(f"{Y - 1}-12-01", tz="UTC")) & (ts < pd.Timestamp(f"{Y + 1}-01-02", tz="UTC"))]
        L = vb.transform(sub)["L"].ffill()
        pbar = np.array([vb.profile_[:288].mean(), vb.profile_[288:].mean()])
        sel = td_year == Y
        k = np.searchsorted(L.index.asi8, ts.asi8[sel], side="right") - 1
        Lk = np.where(k >= 0, L.to_numpy()[np.clip(k, 0, None)], np.nan)
        f[sel] = np.sqrt(np.exp(Lk) * pbar[dst[sel].astype(int)] * 1440)
        Lmin[sel] = Lk
        log(f"  σ unit {Y}: fit {fit_start.date()}..{fit_end.date()}, median σ/price {np.nanmedian(f[sel]):.4f}")
    return f, Lmin


# =============================================================================
# Events and outcomes (upper-side logic on mirrored arrays)
# =============================================================================

class Market:
    """Minute arrays, mirrored copies, 5-min bars and σ factor, shared by every unit."""

    def __init__(self, ts_ns, o, h, l, c, factor, L=None):
        self.ts = ts_ns
        self.hi = {+1: h, -1: -l}
        self.lo = {+1: l, -1: -h}
        self.cl = {+1: c, -1: -c}
        self.op = {+1: o, -1: -o}
        self.f = factor
        self.L = L if L is not None else np.full(len(ts_ns), np.nan)
        fm = five_min(ts_ns, c)
        self.b_end, self.b_last, self.b_of = fm["end"], fm["last"], fm["of_minute"]
        self.b_close = {+1: fm["close"], -1: -fm["close"]}
        self.last_ts = ts_ns[-1]


def eligible(open_m: float, lvl_m: float, sigma: float) -> bool:
    return (lvl_m - open_m) >= ELIG * sigma


def first_touch(mk: Market, side, i0, i1, lvl_m, fill):
    hit = np.flatnonzero(mk.hi[side][i0:i1 + 1] >= lvl_m + fill)
    return i0 + hit[0] if len(hit) else -1


def first_break(mk: Market, side, i0, i1, lvl_m, level_abs):
    b0, b1 = mk.b_of[i0], mk.b_of[i1]
    last = mk.b_last[b0:b1 + 1]
    thr = lvl_m + BREAK_THR * level_abs * mk.f[last]
    hit = np.flatnonzero(mk.b_close[side][b0:b1 + 1] >= thr)
    return b0 + hit[0] if len(hit) else -1


def first_sweep(mk: Market, side, i0, i1, lvl_m, level_abs):
    thr = lvl_m + PIERCE_THR * level_abs * mk.f[i0:i1 + 1]
    p = np.flatnonzero(mk.hi[side][i0:i1 + 1] >= thr)
    if not len(p):
        return -1
    j = i0 + p[0]
    limit = mk.ts[j] + MIN + RECLAIM_MIN * MIN
    b0, b1 = mk.b_of[j], mk.b_of[i1]
    ends = mk.b_end[b0:b1 + 1]
    ok = (ends <= limit) & (mk.b_close[side][b0:b1 + 1] < lvl_m)
    hit = np.flatnonzero(ok)
    return b0 + hit[0] if len(hit) else -1


def first_passage(hi, lo, cl, ts, j0, t_event, entry, sig, d, touch, bs=BS, hs=HS):
    """Outcome codes per (b, H): 1 target first, 0 stop first (ties -> stop), -1 timeout.
    Returns None if the longest window runs past the data."""
    tmax = t_event + max(hs) * MIN
    if tmax > ts[-1] + MIN:
        return None
    j1 = np.searchsorted(ts, tmax, side="left")
    H, Lw, T = hi[j0:j1], lo[j0:j1], ts[j0:j1]
    out = {}
    for b in bs:
        up, dn = entry + b * sig, entry - b * sig
        tgt = (H >= up) if d > 0 else (Lw <= dn)
        stp = (Lw <= dn) if d > 0 else (H >= up)
        if touch and len(tgt):
            tgt = tgt.copy()
            tgt[0] = False                 # touch bar: only the beyond barrier counts
        for hm in hs:
            k = np.searchsorted(T, t_event + hm * MIN, side="left")
            ft = np.flatnonzero(tgt[:k])
            fs = np.flatnonzero(stp[:k])
            a = ft[0] if len(ft) else np.inf
            s = fs[0] if len(fs) else np.inf
            out[(b, hm)] = -1 if (a == np.inf and s == np.inf) else (1 if a < s else 0)
    moves = {}
    for hm in (60, 240):
        k = np.searchsorted(ts, t_event + hm * MIN, side="left") - 1
        moves[hm] = (cl[k] - entry) * d / sig
    return out, moves


def event_path(cl, ts, t_event, entry, sig, d):
    k = np.searchsorted(ts, t_event + PATH_OFFSETS * MIN, side="right") - 1
    return ((cl[k] - entry) * d / sig).astype(np.float32)


# =============================================================================
# One unit = one level (real or placebo) over its days. Same code for both.
# =============================================================================

def run_unit(unit: dict, mk: Market, days: pd.DataFrame, with_path: bool = False) -> list:
    side, level = unit["side"], unit["level"]
    lvl_m = side * level
    lab = abs(level)
    done = {("TOUCH", f): False for f in FILLS}
    done.update({("BREAK", None): False, ("SWEEP", None): False})
    recs = []
    for di in unit["days"]:
        i0, i1 = int(days.at[di, "i0"]), int(days.at[di, "i1"])
        if not eligible(mk.op[side][i0], lvl_m, lab * mk.f[i0]):
            continue
        for key in [k for k, v in done.items() if not v]:
            etype, fill = key
            if etype == "TOUCH":
                j = first_touch(mk, side, i0, i1, lvl_m, fill)
                if j < 0:
                    continue
                t_ev, j0, entry, touch = mk.ts[j], j, lvl_m, True
                sig, jL = lab * mk.f[j], j
            else:
                bk = (first_break if etype == "BREAK" else first_sweep)(mk, side, i0, i1, lvl_m, lab)
                if bk < 0:
                    continue
                t_ev = mk.b_end[bk]
                j0 = np.searchsorted(mk.ts, t_ev, side="left")
                entry, touch = mk.b_close[side][bk], False
                jL = mk.b_last[bk]
                sig = abs(entry) * mk.f[jL]
            done[key] = True
            hyp, d = HYP[etype]
            if not np.isfinite(sig) or sig <= 0:
                continue
            fp = first_passage(mk.hi[side], mk.lo[side], mk.cl[side], mk.ts, j0, t_ev, entry, sig, d, touch)
            if fp is None:
                continue
            codes, moves = fp
            tgt = codes[(B_MAIN, H_MAIN)]
            if tgt == 1:
                pnl = B_MAIN * sig
            elif tgt == 0:
                pnl = -B_MAIN * sig
            else:
                k = np.searchsorted(mk.ts, t_ev + H_MAIN * MIN, side="left") - 1
                pnl = (mk.cl[side][k] - entry) * d
            rec = {"unit": unit["id"], "parent": unit["parent"], "is_placebo": unit["is_placebo"],
                   "family": unit["family"], "name": unit["name"], "side": side,
                   "cluster": unit["cluster"], "period_date": unit["period_date"],
                   "etype": etype, "fill": fill, "hyp": hyp, "t_event": int(t_ev), "sigma": sig, "L": mk.L[jL],
                   "move60": moves[60], "move240": moves[240], "pnl": pnl - 2 * COST_SIDE,
                   **{f"o_{b}_{h}": v for (b, h), v in codes.items()}}
            if with_path and (etype != "TOUCH" or fill == FILL):
                rec["path"] = event_path(mk.cl[side], mk.ts, t_ev, entry, sig, d)
            recs.append(rec)
        if all(done.values()):
            break
    return recs


def evaluate_levels(units: list, mk: Market, days: pd.DataFrame, with_path: bool = False) -> list:
    """Every unit, real or placebo, goes through run_unit (tested)."""
    out = []
    for u in units:
        out.extend(run_unit(u, mk, days, with_path=with_path))
    return out


# =============================================================================
# Placebos
# =============================================================================

def draw_placebos(rng, level, side, sigma, open_price, real_levels, n=N_PLACEBO, excl_grid=None):
    reals = np.array([r for r in real_levels if np.isfinite(r)])
    out, tries = [], 0
    while len(out) < n and tries < 1000 * n:
        tries += 1
        delta = (1 if rng.random() < 0.5 else -1) * rng.uniform(PLC_LO, PLC_HI) * sigma
        p = level + delta
        if len(reals) and np.min(np.abs(p - reals)) < PLC_EXCL * sigma:
            continue
        if excl_grid is not None and abs(p - excl_grid * np.round(p / excl_grid)) < PLC_EXCL * sigma:
            continue
        if not eligible(side * open_price, side * p, sigma):
            continue
        out.append(p)
    return out


# =============================================================================
# Units
# =============================================================================

def build_units(days: pd.DataFrame, mk: Market, seed=20240101, families=("DAILY", "WEEKLY", "ROUND10", "ROUND50")):
    rng = np.random.default_rng(seed)
    units, stats = [], {"placebo_short": 0}
    uid = 0

    def add(level, side, fam, name, day_list, cluster, pdate, parent, is_plc, j):
        nonlocal uid
        units.append({"id": uid, "level": float(level), "side": side, "family": fam, "name": name,
                      "days": day_list, "cluster": cluster, "period_date": pdate, "parent": parent,
                      "is_placebo": is_plc, "j": j})
        uid += 1
        return uid - 1

    week_generated = {}
    ok_days = days.index[~days["excluded"]]
    week_days = {}
    for x in ok_days:
        week_days.setdefault(int(days.at[x, "week"]), []).append(x)
    for di in ok_days:
        row = days.loc[di]
        i0 = int(row["i0"])
        o = row["open"]
        reals_today = [row["PDH"], row["PDL"], row["WH"], row["WL"]]
        date = pd.Timestamp(row["date"])
        # ---- DAILY
        if "DAILY" in families:
            for name, side in (("PDH", +1), ("PDL", -1)):
                lv = row[name]
                if not np.isfinite(lv):
                    continue
                sig = abs(lv) * mk.f[i0]
                if not (np.isfinite(sig) and eligible(side * o, side * lv, sig)):
                    continue
                pid = add(lv, side, "DAILY", name, [di], f"D{date.date()}", date, None, False, -1)
                units[pid]["parent"] = pid
                pl = draw_placebos(rng, lv, side, sig, o, reals_today)
                stats["placebo_short"] += N_PLACEBO - len(pl)
                for j, p in enumerate(pl):
                    add(p, side, "DAILY", name, [di], f"D{date.date()}", date, pid, True, j)
        # ---- WEEKLY (generated at the first day of the week where the real level is eligible)
        if "WEEKLY" in families:
            wk = int(row["week"])
            for name, side in (("WH", +1), ("WL", -1)):
                lv = row[name]
                if (wk, name) in week_generated or not np.isfinite(lv):
                    continue
                sig = abs(lv) * mk.f[i0]
                if not (np.isfinite(sig) and eligible(side * o, side * lv, sig)):
                    continue
                week_generated[(wk, name)] = True
                rest = [x for x in week_days[wk] if x >= di]
                pid = add(lv, side, "WEEKLY", name, rest, f"W{wk}", date, None, False, -1)
                units[pid]["parent"] = pid
                pl = draw_placebos(rng, lv, side, sig, o, reals_today)
                stats["placebo_short"] += N_PLACEBO - len(pl)
                for j, p in enumerate(pl):
                    add(p, side, "WEEKLY", name, rest, f"W{wk}", date, pid, True, j)
        # ---- ROUND (exploratory)
        for fam, grid in (("ROUND10", 10.0), ("ROUND50", 50.0)):
            if fam not in families:
                continue
            sig = abs(o) * mk.f[i0]
            if not np.isfinite(sig):
                continue
            up = np.ceil((o + ELIG * sig) / grid) * grid
            dn = np.floor((o - ELIG * sig) / grid) * grid
            for name, side, lv in ((f"R{int(grid)}U", +1, up), (f"R{int(grid)}D", -1, dn)):
                s2 = abs(lv) * mk.f[i0]
                pid = add(lv, side, fam, name, [di], f"D{date.date()}", date, None, False, -1)
                units[pid]["parent"] = pid
                pl = draw_placebos(rng, lv, side, s2, o, reals_today + [lv], excl_grid=grid)
                for j, p in enumerate(pl):
                    add(p, side, fam, name, [di], f"D{date.date()}", date, pid, True, j)
    return units, stats


# =============================================================================
# Inference
# =============================================================================

def boot_weights(n_clusters, B=N_BOOT, seed=20240102):
    rng = np.random.default_rng(seed)
    return rng.multinomial(n_clusters, np.full(n_clusters, 1.0 / n_clusters), size=B).astype(np.float32)


def paired_effect(ev: pd.DataFrame, value: str, clusters: pd.Index, W=None):
    """Effect = mean(real) - mean(placebo events of units whose real level had an event)."""
    real = ev[~ev.is_placebo]
    plc = ev[ev.is_placebo & ev.parent.isin(real.unit)]
    cid = pd.Series(np.arange(len(clusters)), index=clusters)
    rs = np.bincount(cid[real.cluster].to_numpy(), weights=real[value].to_numpy(), minlength=len(clusters))
    rn = np.bincount(cid[real.cluster].to_numpy(), minlength=len(clusters)).astype(float)
    ps = np.bincount(cid[plc.cluster].to_numpy(), weights=plc[value].to_numpy(), minlength=len(clusters))
    pn = np.bincount(cid[plc.cluster].to_numpy(), minlength=len(clusters)).astype(float)
    point = rs.sum() / max(rn.sum(), 1) - ps.sum() / max(pn.sum(), 1)
    res = {"real_rate": rs.sum() / max(rn.sum(), 1), "placebo_rate": ps.sum() / max(pn.sum(), 1),
           "effect": point, "n_real": int(rn.sum()), "n_placebo": int(pn.sum())}
    if W is not None and rn.sum() > 0 and pn.sum() > 0:
        with np.errstate(invalid="ignore", divide="ignore"):
            eff = (W @ rs) / (W @ rn) - (W @ ps) / (W @ pn)
        eff = eff[np.isfinite(eff)]
        res.update(ci_lo=float(np.percentile(eff, 2.5)), ci_hi=float(np.percentile(eff, 97.5)),
                   p_one_sided=float((np.sum(eff <= 0) + 1) / (len(eff) + 1)))
    return res


def bh(p: np.ndarray, q: float) -> tuple:
    p = np.asarray(p, float)
    m = len(p)
    order = np.argsort(p)
    ranked = p[order] * m / np.arange(1, m + 1)
    adj = np.minimum.accumulate(ranked[::-1])[::-1]
    out = np.empty(m)
    out[order] = np.minimum(adj, 1.0)
    return out, out < q
