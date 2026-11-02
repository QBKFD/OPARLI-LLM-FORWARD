"""How often is a level near the price touched in a day? A planning count for
the GEX level test's sample size (PREREG.md §9), on seen XAUUSD data only.

    python research/gex_levels/touch_rate.py        # writes TOUCH_RATE.md next to this file

**No GEX data, no outcomes.** It counts touches only: whether price reached a
level, never what it did next. It uses data from 2007 to 2023 through the
research loaders, which enforce the seals: CONF 2007–2020 from `XAUUSD.txt`,
and DEV 2021–2023 from the parquet.

**The rules, fixed before the count was run:**
- **Days:** the last W = 10 trading days up to and including each monthly OG
  expiry. The expiry is the 4th-last business day of the month, or the
  business day before it when that is a Friday. US holidays aren't modelled,
  so a few expiries are off by a day.
- **Snapshot:** P0 is the close of the last 1-min bar before 05:00 UTC on the
  trade date, within the hour before. Without one, the day is skipped and
  counted.
- **Band:** `|ln(K/P0)| ≤ 2 σ √T`. σ stands in for the ATM IV: the 20-day
  realized volatility of daily closes (17:00 New York), annualized with √252.
  T runs from 05:00 UTC to the expiry at 13:30 New York, at least 1 day.
- **Stand-in levels:** multiples of R, a round step near 1.25 % of P0 (the
  closest in log terms of 5, 10, 25, 50 or 100). Three per day, placed three
  ways:
  - **U (upper bound):** the 3 grid levels nearest P0;
  - **M (central):** 3 drawn without replacement with weight φ(z), where
    `z = ln(K/P0) / (σ √T)`, so open interest is concentrated near the money;
  - **L (lower bound):** 3 drawn uniformly from the band.
  - The draws use seed = the trade date.
- **Touch:** the first 1-min bar after 05:00 UTC, up to 17:00 New York, with
  `low − 1 ≤ K ≤ high + 1` ($1, the scanner's tolerance). If the first bar
  after 05:00 UTC already contains K, it is not counted: the price started at
  the level.
- **q** = touched level-days / level-days.
- **N rule (PREREG §9):** N = the number of cycles from q under placement M,
  rounded up, and never below 16, the owner's figure.
"""

from __future__ import annotations

import math
import sys
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
NY = ZoneInfo("America/New_York")
W = 10
TOL = 1.0
LEVELS = 3
SNAP = time(5, 0)                       # UTC
STEPS = (5, 10, 25, 50, 100)
SEAL = pd.Timestamp("2024-01-01", tz="UTC")
# PREREG §9 power formula (one-sided α 0.025, power 0.80)
Z = 1.959964 + 0.841621
P0_RATE, PLACEBOS, DESIGN_EFFECT, TAU2, DELTA, N_FLOOR = 0.5, 5, 1.2, 0.005, 0.15, 16


# --- calendar -------------------------------------------------------------------------

def og_expiry(year: int, month: int) -> date:
    """The OG expiry in (year, month): the 4th-last business day, or the business
    day before it if that is a Friday. Weekends only; holidays not modelled."""
    d = (date(year + month // 12, month % 12 + 1, 1) - timedelta(days=1))
    busy = []
    while len(busy) < 5:
        if d.weekday() < 5:
            busy.append(d)
        d -= timedelta(days=1)
    e = busy[3]
    return busy[4] if e.weekday() == 4 else e


def trade_date(ts_utc: pd.DatetimeIndex) -> np.ndarray:
    """CME trade date per bar: a session opening at 18:00 New York belongs to the next day."""
    ny = ts_utc.tz_convert(NY)
    d = ny.normalize().tz_localize(None)
    return np.where(ny.hour >= 18, d + pd.Timedelta(days=1), d).astype("datetime64[D]")


def step_for(p0: float) -> int:
    target = 0.0125 * p0
    return min(STEPS, key=lambda s: abs(math.log(s / target)))


# --- data -------------------------------------------------------------------------------

def load() -> pd.DataFrame:
    sys.path.insert(0, str(ROOT / "research" / "vol_features_r3"))
    sys.path.insert(0, str(ROOT / "research" / "vol_features"))
    import run_r3                                          # its loaders assert the seals
    conf = run_r3.load_conf()                              # 2007-01-01 .. < 2021
    dev = run_r3.load_dev()
    dev = dev[dev.index >= pd.Timestamp("2021-01-01", tz="UTC")]
    m = pd.concat([conf[["open", "high", "low", "close"]], dev[["open", "high", "low", "close"]]]).sort_index()
    m = m[~m.index.duplicated(keep="first")]
    assert len(m) and m.index.max() < SEAL, "SEALED DATA: rows at or after 2024-01-01"
    return m


# --- the count ------------------------------------------------------------------------

def count(m: pd.DataFrame) -> pd.DataFrame:
    td = trade_date(m.index)
    ny = m.index.tz_convert(NY)
    in_session = ny.hour < 17
    closes = pd.Series(m["close"].values[in_session], index=td[in_session]).groupby(level=0).last()
    rv = np.log(closes).diff().rolling(20).std().shift(1) * math.sqrt(252)    # before the day
    days = closes.index.values.astype("datetime64[D]")
    day_set = set(days.tolist())
    hi, lo, cl = m["high"].values, m["low"].values, m["close"].values
    ts = m.index.values

    rows = []
    for y in range(2007, 2024):
        for mo in range(1, 13):
            e = og_expiry(y, mo)
            window = [d for d in sorted(day_set) if d <= e][-W:]
            if not window or window[-1] != e:
                continue                                   # expiry day not in the data
            exp_t = datetime.combine(e, time(13, 30), NY).astimezone(timezone.utc)
            for d in window:
                rec = dict(expiry=e, day=d, period="CONF" if d.year < 2021 else "DEV")
                snap = pd.Timestamp(datetime.combine(d, SNAP), tz="UTC")
                i0 = np.searchsorted(ts, snap.to_datetime64())        # first bar at/after 05:00 UTC
                if i0 == 0 or ts[i0 - 1] < (snap - pd.Timedelta(hours=1)).to_datetime64():
                    rows.append(rec | dict(skipped="no bar in the hour before 05:00 UTC"))
                    continue
                sigma = rv.get(pd.Timestamp(d))
                if sigma is None or not np.isfinite(sigma):
                    rows.append(rec | dict(skipped="no 20-day volatility"))
                    continue
                end = pd.Timestamp(datetime.combine(d, time(17, 0), NY)).tz_convert("UTC").to_datetime64()
                i1 = np.searchsorted(ts, end)
                p0 = cl[i0 - 1]
                t = max((exp_t - snap.to_pydatetime()).total_seconds() / (365 * 86400), 1 / 365)
                half = 2 * sigma * math.sqrt(t)
                r = step_for(p0)
                grid = np.arange(math.ceil(p0 * math.exp(-half) / r) * r, p0 * math.exp(half) + 1e-9, r)
                if len(grid) < LEVELS:
                    rows.append(rec | dict(skipped="fewer than 3 grid levels in the band"))
                    continue
                z = np.log(grid / p0) / (sigma * math.sqrt(t))
                rng = np.random.default_rng(int(d.strftime("%Y%m%d")))
                place = {
                    "U": grid[np.argsort(np.abs(grid - p0), kind="stable")[:LEVELS]],
                    "M": rng.choice(grid, LEVELS, replace=False, p=(w := np.exp(-0.5 * z * z)) / w.sum()),
                    "L": rng.choice(grid, LEVELS, replace=False),
                }
                if i1 <= i0:
                    rows.append(rec | dict(skipped="no bars after 05:00 UTC"))
                    continue
                h, lw = hi[i0:i1], lo[i0:i1]
                for name, levels in place.items():
                    for k in levels:
                        hit = (lw - TOL <= k) & (k <= h + TOL)
                        started = bool(hit[0])
                        rows.append(rec | dict(placement=name, level=float(k), step=r, sigma=sigma, t_days=t * 365,
                                               z=float(np.log(k / p0) / (sigma * math.sqrt(t))),
                                               touched=bool(hit[1:].any()) and not started, started=started))
    return pd.DataFrame(rows)


def cycles_needed(q: float) -> float:
    m = LEVELS * q * W
    s2 = P0_RATE * (1 - P0_RATE) * (1 / m) * (1 + 1 / PLACEBOS) * DESIGN_EFFECT + TAU2
    return Z * Z * s2 / DELTA ** 2


def report(df: pd.DataFrame) -> str:
    lv = df[df["placement"].notna()] if "placement" in df else df.iloc[0:0]
    skipped = df[df.get("skipped").notna()] if "skipped" in df else df.iloc[0:0]
    L = ["# Touch rate on seen XAUUSD data (GEX level test, planning)", "",
         "Made by `touch_rate.py` (its docstring has the rules, fixed before the count). **No GEX data and no "
         "outcomes:** only whether price touched a stand-in level after 05:00 UTC. Data: 2007–2023 (CONF "
         "2007–2020, DEV 2021–2023) through the sealed research loaders.", ""]
    n_days = lv.groupby(["day"]).ngroups
    n_cycles = lv.groupby(["expiry"]).ngroups
    L += [f"- {n_cycles} expiry cycles, {n_days} window days; "
          f"{skipped['day'].nunique() if len(skipped) else 0} days skipped "
          f"({', '.join(f'{k}: {v}' for k, v in skipped['skipped'].value_counts().items()) or 'none'}).",
          f"- Levels that price started on (not counted as touches): "
          f"{', '.join(f'{p} {lv[lv.placement == p].started.mean():.1%}' for p in 'UML')}.", ""]
    L += ["| Placement | Period | Level-days | Touched | q | Median \\|z\\| | Cycles for Δ = 15 pp |",
          "|---|---|---|---|---|---|---|"]
    out = {}
    for p, name in (("U", "U: 3 nearest (upper bound)"), ("M", "M: near-the-money weighted (central)"),
                    ("L", "L: uniform in the band (lower bound)")):
        for per in ("CONF", "DEV", "all"):
            s = lv[lv.placement == p] if per == "all" else lv[(lv.placement == p) & (lv.period == per)]
            q = s.touched.mean()
            out[(p, per)] = q
            L.append(f"| {name if per == 'CONF' else ''} | {per} | {len(s):,} | {int(s.touched.sum()):,} | {q:.3f} | "
                     f"{s.z.abs().median():.2f} | {cycles_needed(q):.1f} |")
    q_m = out[("M", "all")]
    n = max(math.ceil(cycles_needed(q_m)), N_FLOOR)
    L += ["", f"**N (PREREG §9 rule): placement M, all years, q = {q_m:.3f} → {cycles_needed(q_m):.1f} cycles; "
              f"rounded up and at least {N_FLOOR}: N = {n}.**", "",
          "Assumptions behind the cycle counts (PREREG §9): reject rate 0.5, 5 placebos, design effect 1.2, "
          "between-cycle variance 0.005, one-sided α = 0.025, power 0.80, W = 10, 3 levels a day.",
          "", "Caveats: stand-in round levels, not real open interest; realized, not implied, volatility; "
          "US holidays not modelled in the expiry dates; XAUUSD spot, not GC futures."]
    return "\n".join(L) + "\n"


def main() -> int:
    df = count(load())
    (HERE / "TOUCH_RATE.md").write_text(report(df), encoding="utf-8")
    print((HERE / "TOUCH_RATE.md").read_text())
    return 0


if __name__ == "__main__":
    sys.exit(main())
