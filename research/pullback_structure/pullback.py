"""research/pullback_structure: the pipeline of PREREG.md (P1-P37), as pure
functions on arrays. No value lives here: every parameter comes in as an
argument, and run_*.py take them from the PREREG constants in params.py.

Bear events are computed on the prices as they are; bull events on the
negated prices (high' = -low, low' = -high), so one state machine serves both
(P19). Nothing here prints or returns a price row for committed output: the
event records carry prices for the local gallery only, and the run scripts
write aggregates.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

NS_MIN = 60 * 10**9

# ----------------------------------------------------------------- candles (P1-P3)


def ny_slot(index: pd.DatetimeIndex, edges_ny: tuple[int, ...]) -> tuple[np.ndarray, np.ndarray]:
    """For each 1-min bar (index = its open time, UTC): the New York session day
    (days since 1970; 18:00 NY opens the next day) and the slot within it (P2).
    Slots start at the NY hours `edges_ny` (18, 22, 2, 6, 10, 14); the last one
    ends at 17:00 NY. Bars in the 17:00-18:00 NY hour get slot -1."""
    local = index.tz_convert("America/New_York")
    h = np.asarray(local.hour)
    day = np.asarray(local.tz_localize(None).normalize().asi8) // (86400 * 10**9)
    sess = np.where(h >= 18, day + 1, day)
    since = (h - 18) % 24                                  # hours since 18:00 NY on the session's eve
    rel = (np.asarray(edges_ny) - 18) % 24
    slot = np.searchsorted(rel, since, side="right") - 1
    slot = np.where(since >= 23, -1, slot)
    return sess, slot


def slot_bounds(sess: np.ndarray, slot: np.ndarray, edges_ny: tuple[int, ...], end_ny: int
                ) -> tuple[pd.DatetimeIndex, pd.DatetimeIndex]:
    """UTC start and end of each (session day, slot). Computed in New York local
    time, so a slot spanning a DST change is an hour shorter or longer; a local
    time that doesn't exist (02:00 on the spring-forward day) moves forward."""
    rel = (np.asarray(list(edges_ny) + [end_ny]) - 18) % 24
    rel[-1] = rel[-1] if rel[-1] > rel[-2] else rel[-1] + 24
    eve = pd.to_datetime((sess - 1) * 86400, unit="s") + pd.Timedelta(hours=18)
    s_loc = eve + pd.to_timedelta(rel[slot], unit="h")
    e_loc = eve + pd.to_timedelta(rel[slot + 1], unit="h")
    loc = lambda x: pd.DatetimeIndex(x).tz_localize("America/New_York", ambiguous=False,  # noqa: E731
                                                     nonexistent="shift_forward").tz_convert("UTC")
    return loc(s_loc), loc(e_loc)


def candles(m: pd.DataFrame, edges_ny: tuple[int, ...], end_ny: int, min_share: float) -> tuple[pd.DataFrame, dict]:
    """4h candles from 1-min bars (P1-P3). Returns the kept candles, each with
    open/high/low/close, start and end (UTC), its 1-min row range [i0, i1) in `m`
    and n_min; plus counts of the dropped candles and of break-hour bars. A
    candle is kept if it has at least `min_share` of its scheduled minutes
    (end - start, so a DST-day candle is scheduled 180 or 300 minutes)."""
    if not m.index.is_monotonic_increasing or m.index.has_duplicates:
        raise AssertionError("1-min index must be sorted and unique")
    sess, slot = ny_slot(m.index, edges_ny)
    ok = slot >= 0
    rows = np.flatnonzero(ok)
    key = sess[ok] * 8 + slot[ok]
    if (np.diff(key) < 0).any():
        raise AssertionError("candle keys go backwards in time")
    p0 = np.flatnonzero(np.concatenate([[True], np.diff(key) != 0]))
    p1 = np.concatenate([p0[1:], [len(key)]])
    i0, i1 = rows[p0], rows[p1 - 1] + 1
    if not all(ok[a:b].all() for a, b in zip(i0, i1)):
        raise AssertionError("a break-hour bar sits inside a candle")
    hi, lo = m["high"].to_numpy()[ok], m["low"].to_numpy()[ok]
    start, end = slot_bounds(sess[i0], slot[i0], edges_ny, end_ny)
    sched = ((end - start).total_seconds() // 60).to_numpy().astype(int)
    out = pd.DataFrame({"open": m["open"].to_numpy()[i0], "high": np.maximum.reduceat(hi, p0),
                        "low": np.minimum.reduceat(lo, p0), "close": m["close"].to_numpy()[i1 - 1],
                        "start": start, "end": end, "i0": i0, "i1": i1, "n_min": i1 - i0,
                        "slot": slot[i0], "scheduled": sched})
    t = m.index
    if not ((t[i0] >= start).all() and (t[i1 - 1] < end).all()):
        raise AssertionError("a 1-min bar falls outside its candle's [start, end)")
    keep = out["n_min"].to_numpy() >= min_share * sched
    info = {"candles_any": int(len(out)), "candles_kept": int(keep.sum()),
            "candles_dropped_incomplete": int((~keep).sum()), "bars_in_break_hour": int((~ok).sum())}
    return out[keep].reset_index(drop=True), info


def candles_hourly(m: pd.DataFrame, min_share: float) -> tuple[pd.DataFrame, dict]:
    """1h candles on the UTC hour (Amendment 2, §12; first used for the owner's
    count-only variants). Same columns as candles(); kept with >= min_share of
    the hour's 60 minutes. A New York hour is a UTC hour, so the daily break
    (17:00-18:00 NY) is never inside a candle."""
    if not m.index.is_monotonic_increasing or m.index.has_duplicates:
        raise AssertionError("1-min index must be sorted and unique")
    key = m.index.floor("1h").asi8
    p0 = np.flatnonzero(np.concatenate([[True], np.diff(key) != 0]))
    i1 = np.concatenate([p0[1:], [len(m)]])
    start = pd.DatetimeIndex(key[p0]).tz_localize("UTC")
    out = pd.DataFrame({"open": m["open"].to_numpy()[p0], "high": np.maximum.reduceat(m["high"].to_numpy(), p0),
                        "low": np.minimum.reduceat(m["low"].to_numpy(), p0), "close": m["close"].to_numpy()[i1 - 1],
                        "start": start, "end": start + pd.Timedelta(hours=1), "i0": p0, "i1": i1, "n_min": i1 - p0,
                        "slot": 0, "scheduled": 60})
    keep = out["n_min"].to_numpy() >= min_share * 60
    return out[keep].reset_index(drop=True), {"candles_kept": int(keep.sum()), "candles_dropped_incomplete": int((~keep).sum())}


# ----------------------------------------------------------------- swings (P4, P5)


def swings(x: np.ndarray, k: int, low: bool) -> np.ndarray:
    """Indices i with x[i] strictly below (low) / above (high) every x[j],
    0 < |i - j| <= k. Only i in [k, n - k) can qualify: a swing needs k candles
    on each side, and it is usable from the close of candle i + k (P4)."""
    n = len(x)
    if n < 2 * k + 1:
        return np.array([], dtype=int)
    y = -x if not low else x
    w = np.lib.stride_tricks.sliding_window_view(y, 2 * k + 1)
    centre = w[:, k]
    others = np.delete(w, k, axis=1)
    ok = (centre[:, None] < others).all(axis=1)
    return np.flatnonzero(ok) + k


# ----------------------------------------------------------------- the event state machine (P6-P18, P24, P26)


@dataclass
class Series:
    """One direction's view of the data. For bull, every price is negated."""
    o: np.ndarray
    h: np.ndarray
    lo: np.ndarray
    c: np.ndarray
    cstart: np.ndarray          # candle start, ns UTC
    cend: np.ndarray            # candle end, ns UTC
    i0: np.ndarray
    i1: np.ndarray
    mt: np.ndarray              # 1-min bar open times, ns UTC
    mh: np.ndarray
    ml: np.ndarray
    sig: np.ndarray             # sigma_px over 240 min as of each candle's close (P9); NaN if none
    sign: int                   # +1 bear (prices as is), -1 bull (negated)


def series(cd: pd.DataFrame, m_t: np.ndarray, m_h: np.ndarray, m_l: np.ndarray, sig: np.ndarray, bull: bool) -> Series:
    s = -1 if bull else 1
    o, h, lo, c = (cd[x].to_numpy(float) for x in ("open", "high", "low", "close"))
    if bull:
        o, h, lo, c = -o, -lo, -h, -c
        mh, ml = -m_l, -m_h
    else:
        mh, ml = m_h, m_l
    return Series(o, h, lo, c, pd.DatetimeIndex(cd["start"]).as_unit("ns").asi8,
                  pd.DatetimeIndex(cd["end"]).as_unit("ns").asi8,
                  cd["i0"].to_numpy(), cd["i1"].to_numpy(), m_t, mh, ml, np.asarray(sig, float), s)


def _first(mask: np.ndarray) -> int:
    j = int(np.argmax(mask)) if len(mask) else 0
    return j if len(mask) and mask[j] else -1


def first_passage(s: Series, r0: int, r1: int, down: float, up: float) -> tuple[str, int]:
    """On 1-min rows [r0, r1): 'down' if a low < down comes strictly before any
    high > up; 'up' if a high > up comes first or in the same bar (P13: a bar
    holding both counts against the hypothesis); 'none' if neither. Returns the
    kind and the row of the decisive bar (-1 for none)."""
    a = _first(s.ml[r0:r1] < down)
    b = _first(s.mh[r0:r1] > up)
    if a < 0 and b < 0:
        return "none", -1
    if a >= 0 and (b < 0 or a < b):
        return "down", r0 + a
    return "up", r0 + b


@dataclass
class Event:
    sign: int
    i: int                       # swing-low candle (in this direction's view)
    conf: int                    # i + k: the confirming candle
    sl: float
    lh: float
    lh_i: int
    lh_conf: int
    sigma_conf: float
    kind: str = ""               # terminal kind
    term_ns: int = 0
    turn: int = -1
    ph: float = np.nan
    ph_i: int = -1
    depth: float = np.nan
    p: float = np.nan
    a: int | None = None         # Q1: 1, 0 or None (unresolved)
    q1_month: int = -1
    brk: int = -1
    brk_from: str = ""           # "pullback" or "after_turn"
    sigma_brk: float = np.nan
    bc: str = ""                 # "B", "C" or ""
    bc_w: dict = field(default_factory=dict)   # W -> "B"/"C"/"" (gallery audit)
    q2_month: int = -1
    c_fix: int = -1
    h3: float = np.nan
    p3: float = np.nan
    a3: int | None = None        # Q3
    q3_month: int = -1
    q3_excluded: str = ""

    @property
    def label(self) -> str:
        """The gallery's outcome label (P37): B or C if a close above LH came
        (before any lower low, so first in time), else A if Q1 is A, else ''."""
        if self.bc:
            return self.bc
        return "A" if self.a == 1 else ""


def _month(ns: int) -> int:
    t = pd.Timestamp(ns, tz="UTC")
    return t.year * 12 + t.month - 1


def _bc(s: Series, b: int, target: float, sl: float, w: int, n: int) -> tuple[str, int]:
    """B if a 1-min high >= target in candles b+1 .. b+w before any 1-min low < sl
    (a bar holding both: not B); else C, fixed at the close of b+w. 'trunc' if
    b+w is past the data."""
    if b + w >= n:
        return "trunc", -1
    r0, r1 = s.i0[b + 1], s.i1[b + w]
    hit = _first(s.mh[r0:r1] >= target)
    ll = _first(s.ml[r0:r1] < sl)
    if hit >= 0 and (ll < 0 or hit < ll):
        return "B", r0 + hit
    return "C", r0 + (ll if ll >= 0 else (r1 - r0 - 1))


def run_events(s: Series, k: int, x_sigma: float, w: int, horizon: int, w_alt: tuple[int, ...],
               events_from_ns: int, n_swings: int) -> tuple[list[Event], dict]:
    """The state machine of PREREG §3 for one direction (bear view). Returns the
    events (every one that started) and counts of the starts that were refused."""
    n = len(s.c)
    sl_idx = swings(s.lo, k, low=True)
    sh_idx = swings(s.h, k, low=False)
    events: list[Event] = []
    cnt = {"swing_lows": int(len(sl_idx)), "before_events_start": 0, "not_bearish": 0,
           "lh_broken_before_start": 0, "while_live": 0, "no_sigma": 0}
    live_until = -1
    for i in sl_idx:
        conf = i + k
        if s.cend[conf] < events_from_ns:
            cnt["before_events_start"] += 1
            continue
        lows = sl_idx[sl_idx <= i][-n_swings:]
        # D18: the structure uses every swing high confirmed by the event's start
        # (index <= i, so an outside candle's own high counts); LH (P7) is the
        # newest swing high before the swing-low candle
        highs = sh_idx[sh_idx <= i][-n_swings:]
        before = sh_idx[sh_idx < i]
        if len(lows) < n_swings or len(highs) < n_swings or not len(before) \
                or not (np.diff(s.lo[lows]) < 0).all() or not (np.diff(s.h[highs]) < 0).all():
            cnt["not_bearish"] += 1
            continue
        lh_i = int(before[-1])
        # D19: a close beyond LH on the swing-low candle or during its confirmation
        # breaks the structure before the event exists: no event (no look-ahead)
        if (s.c[i:conf + 1] > s.h[lh_i]).any():
            cnt["lh_broken_before_start"] += 1
            continue
        if s.cend[conf] <= live_until:
            cnt["while_live"] += 1
            continue
        sig = s.sig[conf]
        if not np.isfinite(sig) or sig <= 0:
            cnt["no_sigma"] += 1
            continue
        e = Event(s.sign, int(i), int(conf), float(s.lo[i]), float(s.h[lh_i]), lh_i, lh_i + k, float(sig))
        _track(s, e, x_sigma, w, horizon, w_alt, n)
        events.append(e)
        live_until = e.term_ns if e.kind != "truncated" else np.iinfo(np.int64).max
    return events, cnt


def _track(s: Series, e: Event, x_sigma: float, w: int, horizon: int, w_alt: tuple[int, ...], n: int) -> None:
    sl, lh, i = e.sl, e.lh, e.i
    # ---- phase 1: pullback, from the candle after the confirmation. A close above LH
    # before that never starts an event (D19, which replaces D3)
    t0 = e.conf + 1
    if t0 >= n:
        e.kind = "truncated"
        return
    ll = s.lo[t0:] < sl
    brk = s.c[t0:] > lh
    turn = s.c[t0:] < s.lo[t0 - 1:n - 1]
    j = _first(ll | brk | turn)
    if j < 0:
        e.kind = "truncated"
        return
    t = t0 + j
    if ll[j]:
        e.kind, e.term_ns = "ll_before_turn", int(s.cend[t])
        return
    if brk[j]:
        e.brk, e.brk_from = t, "pullback"
        _break(s, e, x_sigma, w, horizon, w_alt, n)
        return
    # ---- the turn (P8): depth and P at its close
    e.turn = t
    seg = s.h[i + 1:t + 1]
    e.ph_i = int(i + 1 + int(np.argmax(seg)))
    e.ph = float(seg.max())
    e.depth = (e.ph - sl) / e.sigma_conf
    e.p = (e.ph - s.c[t]) / (e.ph - sl)
    e.q1_month = _month(int(s.cend[t]))
    last = t + horizon
    beyond = last >= n
    kind, row = first_passage(s, s.i1[t], s.i1[min(last, n - 1)], sl, e.ph)
    if kind == "none" and beyond:
        e.kind = "truncated"
        return
    e.a = {"down": 1, "up": 0, "none": None}[kind]
    q1_term = int(s.mt[row]) if row >= 0 else int(s.cend[last])
    # ---- Q2 after the turn: the first close above LH before any lower low, up to the horizon
    u0, u1 = t + 1, min(last, n - 1) + 1
    ll_u = _first(s.lo[u0:u1] < sl)
    br_u = _first(s.c[u0:u1] > lh)
    if br_u >= 0 and (ll_u < 0 or br_u < ll_u):
        e.brk, e.brk_from = u0 + br_u, "after_turn"
        _break(s, e, x_sigma, w, horizon, w_alt, n)
        if e.kind != "truncated":
            e.term_ns = max(e.term_ns, q1_term)
        return
    if beyond and ll_u < 0:
        e.kind = "truncated"                       # a break could still come after the data ends
        return
    if e.a == 1:
        e.kind, e.term_ns = "A", q1_term
    elif ll_u >= 0:
        e.kind, e.term_ns = "ll_after_q1", max(q1_term, int(s.cend[u0 + ll_u]))
    else:
        e.kind, e.term_ns = ("no_break" if e.a == 0 else "unresolved"), int(s.cend[last])


def _break(s: Series, e: Event, x_sigma: float, w: int, horizon: int, w_alt: tuple[int, ...], n: int) -> None:
    b = e.brk
    sig = s.sig[b]
    e.q2_month = _month(int(s.cend[b]))
    if not np.isfinite(sig) or sig <= 0:
        e.kind, e.term_ns = "no_sigma_break", int(s.cend[b])
        return
    e.sigma_brk = float(sig)
    target = s.c[b] + x_sigma * sig
    for ww in w_alt:
        e.bc_w[ww] = _bc(s, b, target, e.sl, ww, n)[0]
    lab, row = _bc(s, b, target, e.sl, w, n)
    if lab == "trunc":
        e.kind = "truncated"
        return
    e.bc = lab
    if lab == "B":
        e.kind, e.term_ns = "B", int(s.mt[row])
        return
    # ---- C: fixed at the close of b + w (P16); Q3 from there (P26)
    c = b + w
    e.c_fix = c
    e.q3_month = _month(int(s.cend[c]))
    if s.lo[b + 1:c + 1].min() < e.sl:
        e.q3_excluded, e.kind, e.term_ns = "ll_during_w", "C", int(s.cend[c])
        return
    e.h3 = float(s.h[e.i + 1:c + 1].max())
    e.p3 = (e.h3 - s.c[c]) / (e.h3 - e.sl)
    last = c + horizon
    kind, row = first_passage(s, s.i1[c], s.i1[min(last, n - 1)], e.sl, e.h3)
    if kind == "none" and last >= n:
        e.kind = "truncated"
        return
    e.a3 = 1 if kind == "down" else (0 if kind == "up" else None)
    e.kind = "C"
    e.term_ns = int(s.mt[row]) if row >= 0 else int(s.cend[last])


def run_both(cd: pd.DataFrame, mt: np.ndarray, mh: np.ndarray, ml: np.ndarray, sig: np.ndarray, *, k: int,
             x_sigma: float, w: int, horizon: int, w_alt: tuple[int, ...],
             events_from_ns: int, n_swings: int) -> tuple[pd.DataFrame, list[Event], dict]:
    """Bear on the prices, bull on the negated prices (P19)."""
    evs: list[Event] = []
    counts = {}
    for bull in (False, True):
        s = series(cd, mt, mh, ml, sig, bull)
        e, c = run_events(s, k, x_sigma, w, horizon, w_alt, events_from_ns, n_swings)
        evs += e
        counts["bull" if bull else "bear"] = c
    return records(evs), evs, counts


def tercile_of(depth: np.ndarray, cuts: tuple[float, float]) -> np.ndarray:
    """Amendment 1 §11.1: 0, 1, 2 for depth < cut1, < cut2, >= cut2 (descriptive only)."""
    return np.searchsorted(np.asarray(cuts), depth, side="right")


def records(events: list[Event]) -> pd.DataFrame:
    """One row per event that started. P24: an event not terminal inside the data
    (kind 'truncated') stays a row and is excluded from every question."""
    rows = []
    for e in events:
        rows.append({"dir": "bear" if e.sign == 1 else "bull", "kind": e.kind, "label": e.label,
                     "depth": e.depth, "p": e.p, "a": np.nan if e.a is None else e.a, "q1_month": e.q1_month,
                     "bc": e.bc, "q2_month": e.q2_month, "brk_from": e.brk_from,
                     "p3": e.p3, "a3": np.nan if e.a3 is None else e.a3, "q3_month": e.q3_month, "q3_excluded": e.q3_excluded,
                     **{f"bc_w{ww}": v for ww, v in e.bc_w.items()}})
    cols = ["dir", "kind", "label", "depth", "p", "a", "q1_month", "bc", "q2_month", "brk_from", "p3", "a3",
            "q3_month", "q3_excluded"]
    return pd.DataFrame(rows) if rows else pd.DataFrame(columns=cols)


# ----------------------------------------------------------------- statistics (P27-P29)


def _month_sums(month: np.ndarray, x: np.ndarray, n: np.ndarray):
    u, inv = np.unique(month, return_inverse=True)
    return u, np.bincount(inv, weights=x, minlength=len(u)), np.bincount(inv, weights=n, minlength=len(u))


def boot_mean(month: np.ndarray, x: np.ndarray, b: int, rng: np.random.Generator) -> np.ndarray:
    """Block bootstrap by calendar month (P28): resample months with replacement,
    the statistic is the mean of x over the resampled events."""
    _, sx, sn = _month_sums(month, x, np.ones_like(x))
    idx = rng.integers(0, len(sx), size=(b, len(sx)))
    return sx[idx].sum(1) / sn[idx].sum(1)


def excess_test(month: np.ndarray, x: np.ndarray, b: int, seed: int) -> dict:
    """Mean excess, its 95 % percentile CI, and the one-sided bootstrap p for
    excess > 0 (P27): the share of resamples <= 0, with the +1 correction."""
    rng = np.random.default_rng(seed)
    bs = boot_mean(month, x, b, rng)
    return {"n": int(len(x)), "months": int(len(np.unique(month))), "excess": float(x.mean()),
            "ci95": [float(np.quantile(bs, 0.025)), float(np.quantile(bs, 0.975))],
            "p_one_sided": float(((bs <= 0).sum() + 1) / (b + 1))}


def share_test(month: np.ndarray, is_b: np.ndarray, null: float, b: int, seed: int) -> dict:
    """Q2 (P25, P27): the share B / (B + C), its CI, and the two-sided bootstrap p
    against the random-walk share."""
    rng = np.random.default_rng(seed)
    bs = boot_mean(month, is_b.astype(float), b, rng)
    lo_ = ((bs <= null).sum() + 1) / (b + 1)
    hi_ = ((bs >= null).sum() + 1) / (b + 1)
    return {"n": int(len(is_b)), "months": int(len(np.unique(month))), "share_B": float(is_b.mean()),
            "null_share_B": float(null), "ci95": [float(np.quantile(bs, 0.025)), float(np.quantile(bs, 0.975))],
            "p_two_sided": float(min(1.0, 2 * min(lo_, hi_)))}


def bh(pvals: dict, q: float) -> dict:
    """Benjamini-Hochberg, as research/sigma_compare/run_sigma.py bh."""
    keys = sorted(pvals, key=lambda k: pvals[k])
    m = len(keys)
    adj, running = {}, 1.0
    for rank in range(m, 0, -1):
        key = keys[rank - 1]
        running = min(running, pvals[key] * m / rank)
        adj[key] = running
    return {key: {"p_adj": adj[key], "reject": adj[key] < q} for key in pvals}


# ----------------------------------------------------------------- the random walk (§5)


def simulate_minutes(close0: float, sigma_min: np.ndarray, rng: np.random.Generator):
    """A driftless random walk on given 1-min rows: Gaussian log returns with
    per-row sd sigma_min. Each bar opens at the previous close; high and low are
    the larger and smaller of open and close (§5: 1-min steps)."""
    r = rng.standard_normal(len(sigma_min)) * sigma_min
    lp = np.log(close0) + np.cumsum(r)
    c = np.exp(lp)
    o = np.concatenate([[close0], c[:-1]])
    return o, np.maximum(o, c), np.minimum(o, c), c
