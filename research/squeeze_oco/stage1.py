"""Stage 1 of research/squeeze_oco (exploratory, not a PREREG): load the dev
period and describe the HAR vol state. Used by 01_vol_state.ipynb.

No value lives here. Every number, window and name comes in as an argument,
and the notebook takes it from its register through need(id). The work is
done by the repo's own code:
- data: research/vol_features_r3/run_r3.py load_conf (XAUUSD.txt through the
  audit_long cache, UTC, duplicates dropped keeping the first), after
  backend.datafeed.store.manifest.verify on XAUUSD.txt and research/data_guard;
- seal: research/sealing.py assert_before_global_seal and run_r3.assert_sealed;
- grid, windows and the HAR forecast: research/sigma_compare/run_sigma.py
  build_context and posthoc.py refeature / rows_from / calib (the method of
  the post-hoc calibration check P2), with the shipped, SHA-checked model.

Committed outputs show aggregates only (IBKR data, own use only): no
function here returns or prints a price row.
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
for _p in (ROOT, ROOT / "research", ROOT / "research" / "vol_features_r3", ROOT / "research" / "sigma_compare"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import data_guard  # noqa: E402
import posthoc  # noqa: E402  (research/sigma_compare: refeature, rows_from, calib)
import run_r3 as r3  # noqa: E402  (load_conf, assert_sealed)
import run_sigma as rs  # noqa: E402  (build_context, HORIZONS, COVERAGE)
import sealing  # noqa: E402

from backend.datafeed.features import vol_block as vb  # noqa: E402
from backend.datafeed.store import manifest  # noqa: E402

STUDY = "squeeze_oco"
ATTEMPTS = HERE / "ATTEMPTS.json"


# ----------------------------------------------------------------- guards

def dev_guard(idx, start: pd.Timestamp, end_excl: pd.Timestamp, what: str) -> None:
    """Refuse any timestamp outside [start, end_excl). The upper side is the
    repo's seal guards (the global seal, and run_r3's study end); the repo has
    no lower-side guard, so that side is one comparison."""
    idx = idx if isinstance(idx, pd.DatetimeIndex) else pd.DatetimeIndex(idx)
    sealing.assert_before_global_seal(idx, what)
    r3.assert_sealed(pd.DataFrame(index=idx), what, end=end_excl)
    if len(idx) and (idx < start).any():
        raise AssertionError(f"OUTSIDE THE DEV PERIOD: {what} has rows before {start}")


# ----------------------------------------------------------------- data

def load_dev(txt_key: str, start: pd.Timestamp, end_excl: pd.Timestamp, verify_cache: bool) -> tuple[pd.DataFrame, dict]:
    """The dev-period 1-min bars and a check record. XAUUSD.txt must match the
    data manifest; with verify_cache, the loader's cache must equal a fresh
    parse of XAUUSD.txt by the loader's own code (audit_long.load_raw)."""
    txt = ROOT / txt_key
    data_guard.assert_may_read(txt, study=STUDY)
    key = manifest.verify(txt)
    if key != txt_key:
        raise AssertionError(f"{txt} matched manifest entry {key!r}, not {txt_key!r}")
    checks = {"manifest_key": key, "manifest_sha256": manifest.load()["files"][key]["sha256"]}
    if verify_cache:
        import tempfile

        import audit_long
        cached = pd.read_parquet(r3.CACHE)
        saved = audit_long.CACHE
        with tempfile.TemporaryDirectory() as d:
            audit_long.CACHE = Path(d) / "fresh.parquet"
            try:
                fresh = audit_long.load_raw()
            finally:
                audit_long.CACHE = saved
        pd.testing.assert_frame_equal(cached, fresh)
        checks["cache_equals_fresh_parse"] = True
        checks["cache_rows"] = int(len(cached))
    m = r3.load_conf(start=start, end=end_excl)
    dev_guard(m.index, start, end_excl, "dev 1-min bars")
    return m, checks


def coverage_table(m: pd.DataFrame) -> pd.DataFrame:
    """Aggregates only: bars, calendar days with bars, first and last bar time, per year."""
    f = pd.DataFrame({"ts": m.index, "day": m.index.normalize()}, index=m.index)
    g = f.groupby(m.index.year)
    out = pd.DataFrame({"bars": g.size(), "days_with_bars": g["day"].nunique(),
                        "first_bar_utc": g["ts"].min(), "last_bar_utc": g["ts"].max()})
    out.index.name = "year"
    return out


def load_model(name: str, sha256: str) -> vb.SigmaModel:
    model = vb.load_sigma_model(name)          # SHA-256 checked against vol_block.SIGMA_MODELS
    if model.sha256 != sha256:
        raise AssertionError(f"model {name}: SHA-256 {model.sha256}, the register has {sha256}")
    return model


# ----------------------------------------------------------------- vol state on the grid

def context(m: pd.DataFrame, end_excl: pd.Timestamp, horizons: tuple, coverage: float) -> rs.Ctx:
    """sigma_compare's grid: every hourly label of the bars, with the windows of
    the first H market-open minutes from each label."""
    if tuple(horizons) != tuple(rs.HORIZONS) or coverage != rs.COVERAGE:
        raise AssertionError(f"register horizons/coverage {horizons}/{coverage} differ from "
                             f"run_sigma's {rs.HORIZONS}/{rs.COVERAGE}")
    return rs.build_context(m, end_excl)


def features(ctx: rs.Ctx, model: vb.SigmaModel):
    """posthoc.refeature with vol_block.state_at's warm-up rule: a label whose
    longest window (vb.WINDOWS[-1] hourly rows) would reach the input's first
    row, which may be partial, has no state (state_at: "insufficient_history")."""
    X, ok_x, P = posthoc.refeature(ctx, model.params)
    warm = np.arange(len(ctx.labels)) < vb.WINDOWS[-1]
    X = X.copy()
    X[warm, 1:] = np.nan
    return X, ok_x & ~warm, P


def state_table(ctx: rs.Ctx, model: vb.SigmaModel, start: pd.Timestamp, end_excl: pd.Timestamp) -> pd.DataFrame:
    """Per hourly label in [start, end_excl): L, S_hd, T, and for each H the HAR
    variance h, sigma_ret = sqrt(h), sigma_px = close_ref * sigma_ret, whether
    the window crosses a close, and whether it is a valid calibration row."""
    X, ok_x, P = features(ctx, model)
    sel = np.asarray((ctx.labels >= start) & (ctx.labels < end_excl)) & ok_x
    out = pd.DataFrame({"L": X[:, 1], "S_hd": X[:, 2], "T": X[:, 3]}, index=ctx.labels)
    for H in rs.HORIZONS:
        beta, c = model.horizons[H]
        w = ctx.win[H]
        with np.errstate(invalid="ignore", over="ignore"):
            h = P[H] * np.exp(X @ np.asarray(beta)) * c
        out[f"sigma_ret_{H}"] = np.sqrt(h)
        out[f"sigma_px_{H}"] = w.close_ref * np.sqrt(h)
        out[f"crossing_{H}"] = w.crossing
        out[f"calib_row_{H}"] = w.valid & ~w.crossing
    out = out[sel]
    dev_guard(out.index, start, end_excl, "vol state grid")
    return out


def describe(tbl: pd.DataFrame, cols: list[str], quantiles: list[float], by: str | None) -> pd.DataFrame:
    """Quantiles of each column, overall (by=None), by "year" or by "hour" (UTC
    hour of the label)."""
    if by is None:
        return tbl[cols].quantile(quantiles).T
    key = tbl.index.year if by == "year" else tbl.index.hour
    return tbl[cols].groupby(key).quantile(quantiles).unstack()


def calibration(ctx: rs.Ctx, model: vb.SigmaModel, start: pd.Timestamp, end_excl: pd.Timestamp,
                by_year: bool) -> pd.DataFrame:
    """sigma_compare's P2 metrics for the frozen model: mean RV/h, RMS z and
    QLIKE on the valid, finite, non-crossing rows (posthoc.rows_from, posthoc.calib),
    overall and (by_year) per calendar year of the label."""
    X, ok_x, P = features(ctx, model)
    mod = {H: {"beta": np.asarray(model.horizons[H][0]), "c": model.horizons[H][1]} for H in rs.HORIZONS}
    rows = posthoc.rows_from(ctx, X, ok_x, P, mod, start, end_excl)
    recs = []
    for H, g in rows.items():
        dev_guard(g.index, start, end_excl, f"calibration rows H={H}")
        groups = [("all", g)] + ([(str(y), x) for y, x in g.groupby(g.index.year)] if by_year else [])
        for name, x in groups:
            recs.append({"H": H, "period": name, "n": len(x), **posthoc.calib(x, "h_b")})
    return pd.DataFrame(recs).set_index(["H", "period"])


# ----------------------------------------------------------------- record keeping

def attempts() -> dict:
    return json.loads(ATTEMPTS.read_text())


def bump_attempt(what: str, by: str) -> int:
    """Any future outcome analysis calls this before it looks at an outcome."""
    d = attempts()
    d["outcome_analyses"] += 1
    d["log"].append({"n": d["outcome_analyses"], "date": date.today().isoformat(), "what": what, "by": by})
    ATTEMPTS.write_text(json.dumps(d, indent=1) + "\n")
    return d["outcome_analyses"]


# ----------------------------------------------------------------- data check (before steps 2-4)

def _trade_dates(m: pd.DataFrame) -> pd.DatetimeIndex:
    """audit_long.session_date: the New York trading date (18:00 NY opens the next date)."""
    import audit_long
    return audit_long.session_date(m.index.tz_convert("UTC").tz_localize(None))


def break_table(m: pd.DataFrame) -> pd.DataFrame:
    """Where the daily break falls in UTC: per year and US-DST season (audit_long.us_dst
    on the trade date), the most common gap in minutes from a Mon-Thu trade date's
    last bar to the next trade date's first bar (its share among those gaps), and the
    most common UTC clock time of each trade date's last and first bar (their shares).
    The first bar at 18:00 New York follows from the trade-date cut itself; the
    break shows in the gap."""
    import audit_long
    td = _trade_dates(m)
    f = pd.DataFrame({"td": td, "hm": m.index.strftime("%H:%M")}, index=m.index)
    g = f.groupby("td")["hm"]
    ts = pd.Series(m.index, index=m.index).groupby(td.values)
    days = pd.DataFrame({"last": g.last(), "first": g.first()})
    # minutes from a trade date's last bar to the next trade date's first bar
    # (Mon-Thu trade dates only, so the weekend isn't counted as a break)
    gap = (ts.min().shift(-1) - ts.max()).dt.total_seconds() / 60
    days["break_min"] = np.where(days.index.weekday <= 3, gap.to_numpy(), np.nan)
    days["season"] = np.where(audit_long.us_dst(pd.DatetimeIndex(days.index)), "DST", "STD")
    days["year"] = days.index.year
    rows = []
    for (y, s), x in days.groupby(["year", "season"]):
        lv, fv = x["last"].value_counts(), x["first"].value_counts()
        bv = x["break_min"].dropna().value_counts()
        rows.append({"year": y, "season": s, "trade_dates": len(x),
                     "break_min_mode": bv.index[0] if len(bv) else np.nan,
                     "break_share": bv.iloc[0] / bv.sum() if len(bv) else np.nan,
                     "last_bar_mode_utc": lv.index[0], "last_share": lv.iloc[0] / len(x),
                     "first_bar_mode_utc": fv.index[0], "first_share": fv.iloc[0] / len(x)})
    return pd.DataFrame(rows).set_index(["year", "season"])


def holiday_table(m: pd.DataFrame, years: range) -> pd.DataFrame:
    """Bars per year on the UTC calendar dates 1 January, 25 December and Good Friday
    (pandas.tseries.holiday.GoodFriday); how many of those fall in the 23:00 UTC hour
    (where a winter session opens for the next date) and how many in other hours;
    and bars on UTC Saturdays."""
    from pandas.tseries.holiday import GoodFriday
    idx = m.index.tz_convert("UTC")
    day = idx.normalize().tz_localize(None)
    counts = pd.Series(day).value_counts()
    rows = []
    for y in years:
        gf = GoodFriday.dates(pd.Timestamp(y, 1, 1), pd.Timestamp(y, 12, 31))[0]
        on = idx[day.isin([pd.Timestamp(y, 1, 1), pd.Timestamp(y, 12, 25), gf])]
        rows.append({"year": y, "bars_jan_1": int(counts.get(pd.Timestamp(y, 1, 1), 0)),
                     "bars_dec_25": int(counts.get(pd.Timestamp(y, 12, 25), 0)),
                     "good_friday": gf.date().isoformat(), "bars_good_friday": int(counts.get(gf, 0)),
                     "holiday_bars_at_23_utc": int((on.hour == 23).sum()),
                     "holiday_bars_other_hours": int((on.hour != 23).sum()),
                     "bars_on_utc_saturdays": int(((idx.year == y) & (idx.weekday == 5)).sum())})
    return pd.DataFrame(rows).set_index("year")


def bars_per_trade_date(m: pd.DataFrame) -> pd.DataFrame:
    """Per year: trade dates Monday-Friday, the median bars per such trade date, and
    the trade dates falling on a Saturday or Sunday (there should be none)."""
    td = pd.Series(_trade_dates(m)).value_counts().sort_index()
    wk = td[td.index.weekday <= 4]
    we = td[td.index.weekday > 4]
    return pd.DataFrame({"trade_dates_mon_fri": wk.groupby(wk.index.year).size(),
                         "median_bars_per_trade_date": wk.groupby(wk.index.year).median(),
                         "weekend_trade_dates": we.groupby(we.index.year).size()}).fillna(0).astype(int)


# ----------------------------------------------------------------- Q1 sample sizes without sigma (PROPOSALS.md)

def candle_counts(m: pd.DataFrame, minutes: int) -> pd.DataFrame:
    """Per year: `minutes`-minute UTC buckets (aligned to the hour) with at least
    one 1-min bar, those with all `minutes` bars, and the complete ones that end on
    the hour (where an hourly label, and so a HAR state, exists). Counts only."""
    if 60 % minutes:
        raise ValueError(f"{minutes} doesn't divide the hour")
    b = m.index.floor(f"{minutes}min")
    n = pd.Series(1, index=b).groupby(level=0).sum()
    full = n[n == minutes]
    on_hour = full[((full.index + pd.Timedelta(minutes=minutes)).minute == 0)]
    by = lambda s: s.groupby(s.index.year).size()  # noqa: E731
    return pd.DataFrame({f"buckets_{minutes}m_any": by(n), f"buckets_{minutes}m_complete": by(full),
                         f"buckets_{minutes}m_complete_ending_on_hour": by(on_hour)}).fillna(0).astype(int)


def label_counts(m: pd.DataFrame) -> pd.Series:
    """Hourly labels per year (vol_block's 1-hour rows: the grid's size before any state)."""
    lab = pd.DatetimeIndex(vb._bar_labels(m.index).unique())
    return lab.to_series().groupby(lab.year).size().rename("hourly_labels")
