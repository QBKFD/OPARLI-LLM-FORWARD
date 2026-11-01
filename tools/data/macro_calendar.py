#!/usr/bin/env python3
"""Macro calendar ingest (docs/MACRO_CALENDAR.md): the scraped Forex Factory
CSVs -> one clean UTC table, an anchor check, a coverage report and surprise
counts. Data prep only: no price data, no outcome.

    python tools/data/macro_calendar.py build [--src DIR] [--out PARQUET] [--report JSON]
        Reads database/historical_news/forex_factory_calendar_<year>.csv for
        2015..2026 and writes database/historical_news/macro_calendar_utc.parquet
        (git-ignored) and docs/macro_calendar_report.json (counts only, no row).
        Exit 1 when the anchor check fails: nothing is written then.
    python tools/data/macro_calendar.py manifest [--src DIR] [--out PARQUET]
        Prints the manifest entries of the raw CSVs and the clean table (JSON).

Rules (owner, 2026-10-31):
- UTC comes from Date + Time, localized in the file's source time zone
  (SOURCE_TZ, proven by the anchor check), with DST. "Combined DateTime" is
  never read.
- A Time that isn't a clock time ("All Day", "Tentative", "<Month> Data",
  "Day 2", ...) gives a null timestamp and a flag, never a guessed time. So
  does a local time that doesn't exist or exists twice (a DST change), and a
  row listed in SOURCE_TIME_CONFLICTS.
- The run of next-January rows at the end of each file (the scraper's
  year-wrap) is cut; build() checks each one is in the next year's file.
- Rows at or after the global seal (research/SEALING.md) are dropped right
  after the timestamp is built, before any value is parsed; only their count
  is reported.
- Surprise = actual - forecast for the SURPRISE_SERIES (each event name is its
  own series), with n_prior = the number of earlier surprises of that series.
  The z-score needs a minimum N the owner hasn't picked, so it isn't computed.
  FOMC rows get no surprise.

The report never holds a value: only counts, shares, dates and times of
release.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "database" / "historical_news"
OUT = SRC / "macro_calendar_utc.parquet"
REPORT = ROOT / "docs" / "macro_calendar_report.json"
YEARS = range(2015, 2027)
COLUMNS = ["Date", "Time", "Currency", "Event", "Impact", "Actual", "Forecast", "Previous", "Combined DateTime"]

_spec = importlib.util.spec_from_file_location("sealing", ROOT / "research" / "sealing.py")
sealing = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sealing)
SEAL = sealing.GLOBAL_SEAL

# The browser time zone of each scrape session, proven by the anchor check (MC1 in
# docs/DECISIONS.md): NFP at 8:30pm/9:30pm and FOMC at 2:00am/3:00am local in
# 2015-2023 is fixed UTC+8 (Etc/GMT-8 is UTC+8); NFP at 2:30pm (1:30pm in the
# autumn desync week) in 2024-2026 is Europe/Warsaw.
SOURCE_TZ = {**{y: "Etc/GMT-8" for y in range(2015, 2024)}, **{y: "Europe/Warsaw" for y in range(2024, 2027)}}
NY = ZoneInfo("America/New_York")

CPI = ("CPI m/m", "CPI y/y", "Core CPI m/m", "Core CPI y/y")
NFP = ("Non-Farm Employment Change",)
FOMC = ("FOMC Statement", "Federal Funds Rate")
PCE = ("Core PCE Price Index m/m",)
CLAIMS = ("Unemployment Claims",)
FAMILIES = {"CPI": CPI, "NFP": NFP, "FOMC": FOMC, "PCE": PCE, "claims": CLAIMS}
MONTHLY = ("CPI", "NFP", "PCE")
SURPRISE_SERIES = CPI + NFP + PCE + CLAIMS          # USD only; never FOMC
ANCHORS = {**{e: "08:30" for e in CPI + NFP}, **{e: "14:00" for e in FOMC}}
# Unscheduled FOMC meetings (owner, 2026-10-31): New York local times.
ANCHOR_ALLOW = {("2020-03-03", "10:00"), ("2020-03-15", "17:00")}
N_CANDIDATES = (12, 24, 36, 60)
# Rows whose Time contradicts the rest of their own release (docs/DECISIONS.md MC3): (file year, Date,
# Time, Currency, Event). Both list CPI y/y at 8:30pm local, an hour before CPI m/m
# and Core CPI m/m of the same release (9:30pm = 08:30 EST). They get a null ts_utc
# and the flag source_time_conflict: the time isn't corrected (never a guessed time).
SOURCE_TIME_CONFLICTS = {(2021, "2021-03-10", "8:30pm", "USD", "CPI y/y"),
                         (2022, "2022-03-10", "8:30pm", "USD", "CPI y/y")}

_CLOCK = re.compile(r"^(\d{1,2}):(\d{2})(am|pm)$")
_LEAP = re.compile(r"^(Mon|Tue|Wed|Thu|Fri|Sat|Sun) ([A-Z][a-z]{2}) (\d{1,2})$")
_NUM = re.compile(r"^(-?\d+(?:\.\d+)?)(%|[KMBT])?$", re.IGNORECASE)
_SCALE = {None: 1.0, "%": 1.0, "K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12}


class AnchorError(RuntimeError):
    pass


# --------------------------------------------------------------------------- parsing

def time_flag(t: str) -> str | None:
    """None for a clock time, else the kind of non-time."""
    t = t.strip()
    if _CLOCK.match(t):
        return None
    if t == "All Day":
        return "all_day"
    if t == "Tentative":
        return "tentative"
    if re.fullmatch(r"\w+ Data", t):
        return "period_data"
    if re.fullmatch(r"Day \d+", t):
        return "day_n"
    return "other_nontime"


def clock(t: str) -> tuple[int, int]:
    h, m, ap = _CLOCK.match(t.strip()).groups()
    h = int(h) % 12 + (12 if ap == "pm" else 0)
    return h, int(m)


def parse_date(s: str, year: int) -> tuple[date | None, bool]:
    """(date, rebuilt). ISO dates as they are; the scraper's leap-day form
    "Mon Feb 29" is rebuilt from the file's year and checked against its
    weekday. Anything else: (None, False)."""
    s = s.strip()
    try:
        return date.fromisoformat(s), False
    except ValueError:
        pass
    m = _LEAP.match(s)
    if m:
        try:
            d = datetime.strptime(f"{year} {m.group(2)} {m.group(3)}", "%Y %b %d").date()
        except ValueError:
            return None, False
        if d.strftime("%a") == m.group(1):
            return d, True
    return None, False


def parse_number(s) -> tuple[float, str | None, bool]:
    """(value, unit, nonnumeric). K, M, B, T (either case) scale to units; %
    stays in percent (0.3% -> 0.3). Empty -> (nan, None, False); a value that isn't a number
    ("0-0-9" votes, "<0.25%", "Pass") -> (nan, None, True)."""
    s = "" if s is None or (isinstance(s, float) and np.isnan(s)) else str(s).strip()
    if not s:
        return np.nan, None, False
    m = _NUM.match(s)
    if not m:
        return np.nan, None, True
    unit = m.group(2).upper() if m.group(2) else None
    return float(m.group(1)) * _SCALE[unit], unit, False


def localize(d: date, hm: tuple[int, int], tz: str) -> tuple[pd.Timestamp | None, str | None]:
    """UTC from a local date and clock time; (None, flag) for a time that
    doesn't exist or exists twice in `tz`."""
    naive = pd.Timestamp(d.year, d.month, d.day, hm[0], hm[1])
    try:
        ts = naive.tz_localize(tz, ambiguous="raise", nonexistent="raise")
    except Exception as e:                       # pytz/zoneinfo raise different classes
        name = type(e).__name__
        if "Ambiguous" in name:
            return None, "ambiguous_local"
        if "NonExistent" in name:
            return None, "nonexistent_local"
        raise
    return ts.tz_convert("UTC"), None


def read_raw(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    if list(df.columns) != COLUMNS:
        raise SystemExit(f"{path.name}: columns {list(df.columns)}, expected {COLUMNS}")
    return df.drop(columns=["Combined DateTime"])           # local time; never read (owner)


def split_wrap(raw: pd.DataFrame, year: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(body, tail). The scraper's last weekly page runs into the next January
    and stamps those days with the file's year: the file ends with a run of
    rows whose dates step back from December to January (docs/DECISIONS.md MC4). That tail is
    the next year's first week; it is cut here and checked against the next
    file in build()."""
    d = [parse_date(x, year)[0] for x in raw["Date"]]
    for i in range(len(d) - 1, 0, -1):
        if d[i] and d[i - 1] and d[i] < d[i - 1]:
            if d[i - 1].month == 12 and d[i].month == 1:
                return raw.iloc[:i], raw.iloc[i:]
            break
    return raw, raw.iloc[:0]


def wrap_check(tail: pd.DataFrame, year: int, nxt: pd.DataFrame | None) -> dict:
    """How many tail rows the next file has on the same day of year + 1 (by
    currency and event; the time isn't compared, the files' zones differ)."""
    out = {"rows": int(len(tail)), "next_file": nxt is not None}
    if nxt is None or tail.empty:
        out["matched_in_next_file"] = 0
        return out
    key = lambda df, y: list(zip(df["Date"].str.strip().str[5:], df["Currency"].str.strip(),   # noqa: E731
                                 df["Event"].str.strip()))
    have = set(key(nxt[nxt["Date"].str.startswith(str(year + 1))], year + 1))
    keys = key(tail, year)
    out["matched_in_next_file"] = sum(k in have for k in keys)
    out["unmatched"] = [f"{year}-{d} {t.strip()} {c} {e}" for (d, c, e), t in zip(keys, tail["Time"]) if (d, c, e) not in have]
    return out


def local_day_end_utc(d: date, tz: str) -> pd.Timestamp:
    nxt = pd.Timestamp(d) + pd.Timedelta(days=1)
    return nxt.tz_localize(tz, nonexistent="shift_forward", ambiguous=False).tz_convert("UTC")


def clean_year(raw: pd.DataFrame, year: int) -> tuple[pd.DataFrame, dict]:
    """The clean rows of one file, before the seal cut, plus that file's counts."""
    tz = SOURCE_TZ[year]
    out, n = [], {"rows_in": len(raw), "date_rebuilt": 0, "date_unparsed": 0, "sealed_dropped": 0,
                  "wrong_year": 0}
    for r in raw.itertuples(index=False):
        d, rebuilt = parse_date(r.Date, year)
        n["date_rebuilt"] += rebuilt
        flag = time_flag(r.Time)
        ts = None
        if d is None:
            n["date_unparsed"] += 1
            flag = flag or "date_unparsed"
        else:
            n["wrong_year"] += d.year != year
            if (year, r.Date.strip(), r.Time.strip(), r.Currency.strip(), r.Event.strip()) in SOURCE_TIME_CONFLICTS:
                flag = "source_time_conflict"
                n["source_time_conflict"] = n.get("source_time_conflict", 0) + 1
            if flag is None:
                ts, flag = localize(d, clock(r.Time), tz)
        # the seal, before any value is parsed: a timed row by its UTC time; an
        # untimed one when any part of its local day falls at or after the seal
        if (ts is not None and ts >= SEAL) or (ts is None and d is not None and local_day_end_utc(d, tz) > SEAL):
            n["sealed_dropped"] += 1
            continue
        out.append({"ts_utc": ts, "date_local": d.isoformat() if d else None, "time_raw": r.Time.strip(),
                    "time_flag": flag, "date_rebuilt": rebuilt, "source_year": year, "source_tz": tz,
                    "currency": r.Currency.strip(), "event": r.Event.strip(), "impact": r.Impact.strip(),
                    "actual": r.Actual.strip(), "forecast": r.Forecast.strip(), "previous": r.Previous.strip()})
    df = pd.DataFrame(out)
    df["ts_utc"] = pd.to_datetime(df["ts_utc"], utc=True)
    for col in ("actual", "forecast", "previous"):
        parsed = df[col].map(parse_number)
        df[f"{col}_num"] = parsed.map(lambda p: p[0]).astype(float)
        df[f"{col}_unit"] = parsed.map(lambda p: p[1])
        df[f"{col}_nonnumeric"] = parsed.map(lambda p: p[2]).astype(bool)
    return df, n


def surprises(df: pd.DataFrame) -> pd.DataFrame:
    """surprise = actual - forecast for SURPRISE_SERIES (USD, timed rows), and
    n_prior = how many surprises of the same series have a strictly earlier
    ts_utc. No look-ahead: a row never counts itself or a later row, and rows
    at the same time don't count each other."""
    df = df.copy()
    df["surprise"] = np.nan
    df["n_prior"] = pd.array([pd.NA] * len(df), dtype="Int64")
    sel = df["currency"].eq("USD") & df["event"].isin(SURPRISE_SERIES) & df["ts_utc"].notna()
    s = df["actual_num"] - df["forecast_num"]
    df.loc[sel, "surprise"] = s[sel]
    for _, g in df[sel & s.notna()].groupby("event"):
        t = g["ts_utc"].sort_values()
        df.loc[t.index, "n_prior"] = np.searchsorted(t.to_numpy(), t.to_numpy(), side="left")
    return df


# --------------------------------------------------------------------------- checks

def anchor_check(df: pd.DataFrame) -> dict:
    """Every timed USD CPI/NFP row at 08:30 New York, every FOMC statement and
    rate decision at 14:00 New York, except ANCHOR_ALLOW. Each allowlisted
    meeting must be found (a stale allowlist fails too)."""
    a = df[df["currency"].eq("USD") & df["event"].isin(list(ANCHORS)) & df["ts_utc"].notna()]
    ny = a["ts_utc"].dt.tz_convert(NY)
    hhmm = ny.dt.strftime("%H:%M")
    want = a["event"].map(ANCHORS)
    key = list(zip(ny.dt.strftime("%Y-%m-%d"), hhmm))
    allowed = pd.Series([k in ANCHOR_ALLOW for k in key], index=a.index) & a["event"].isin(FOMC)
    bad = a[(hhmm != want) & ~allowed]
    found = {k for k, ok in zip(key, allowed) if ok}
    untimed = df[df["currency"].eq("USD") & df["event"].isin(list(ANCHORS)) & df["ts_utc"].isna()]
    return {"checked": int(len(a)), "by_event": {e: int((a["event"] == e).sum()) for e in ANCHORS},
            "allowlisted": sorted(f"{d} {t} NY" for d, t in found),
            "allowlist_missing": sorted(f"{d} {t} NY" for d, t in ANCHOR_ALLOW - found),
            "mismatches": [{"event": r.event, "ts_utc": r.ts_utc.isoformat(), "ny": f"{h}", "want": w}
                           for r, h, w in zip(bad.itertuples(), hhmm[bad.index], want[bad.index])],
            "untimed_anchor_rows": [{"event": r.event, "date_local": r.date_local, "time_raw": r.time_raw}
                                    for r in untimed.itertuples()],
            "passed": bool(bad.empty and found == ANCHOR_ALLOW)}


def anchor_mismatches_under(raw: dict[int, pd.DataFrame], tz: str) -> dict[int, int]:
    """Diagnostic for MC1: anchor mismatches per year if every file were in `tz`."""
    out = {}
    for y, r in raw.items():
        r = split_wrap(r, y)[0]
        keep = r[r["Currency"].eq("USD") & r["Event"].str.strip().isin(list(ANCHORS))]
        bad = 0
        for x in keep.itertuples(index=False):
            d, _ = parse_date(x.Date, y)
            if d is None or time_flag(x.Time) is not None:
                continue
            ts, _ = localize(d, clock(x.Time), tz)
            if ts is None or ts >= SEAL:
                continue
            ny = ts.tz_convert(NY)
            if ny.strftime("%H:%M") != ANCHORS[x.Event.strip()] and \
                    (ny.strftime("%Y-%m-%d"), ny.strftime("%H:%M")) not in ANCHOR_ALLOW:
                bad += 1
        out[y] = bad
    return out


GAP_DAYS = 45            # a monthly series with no release for longer than this is missing one


def scrape_cutoff(df: pd.DataFrame) -> dict[int, str]:
    """Per file: the last ts_utc with an actual, i.e. about when it was scraped.
    Later timed rows are schedule only (not yet released when scraped)."""
    a = df[df["actual"].ne("") & df["ts_utc"].notna()]
    return {int(y): g["ts_utc"].max().isoformat() for y, g in a.groupby("source_year")}


def monthly_gaps(timed: pd.DataFrame, untimed: pd.DataFrame, event: str) -> list[dict]:
    """Releases missing at the source: a gap of more than GAP_DAYS between two
    consecutive timed releases of `event`, missing round(gap / 30.44) - 1 less
    the untimed rows of `event` dated inside the gap (those are there, without
    a usable time: untimed_at_source)."""
    t = timed.loc[timed["event"] == event, "ts_utc"].sort_values()
    out = []
    for a, b in zip(t.iloc[:-1], t.iloc[1:]):
        days = (b - a).total_seconds() / 86400
        if days > GAP_DAYS:
            u = untimed[(untimed["event"] == event) & (untimed["date_local"] > a.date().isoformat())
                        & (untimed["date_local"] < b.date().isoformat())]
            n = max(1, round(days / 30.44) - 1) - len(u)     # a row there without a time isn't missing
            if n > 0:
                out.append({"event": event, "after_utc": a.isoformat(), "before_utc": b.isoformat(),
                            "gap_days": round(days, 1), "missing_releases": n,
                            "untimed_rows_in_gap": [f"{r.date_local} {r.time_raw!r}" for r in u.itertuples()]})
    return out


def coverage(df: pd.DataFrame) -> dict:
    """Per year (UTC; untimed rows by their local date): counts, filled shares,
    duplicates, revisions, missing-at-source and parse failures, per family."""
    usd = df[df["currency"].eq("USD")].copy()
    usd["year"] = np.where(usd["ts_utc"].notna(), usd["ts_utc"].dt.year.astype("Int64").astype(str),
                           usd["date_local"].str[:4])
    cut = scrape_cutoff(df)
    first = pd.Timestamp.min.tz_localize("UTC")        # a file without any actual: every timed row is after it
    usd["after_scrape"] = usd["ts_utc"].notna() & usd["actual"].eq("") & \
        (usd["ts_utc"] > usd["source_year"].map(lambda y: pd.Timestamp(cut[y]) if y in cut else first))
    rep: dict = {"through_utc": df["ts_utc"].max().isoformat(), "scrape_cutoff_utc": {str(k): v for k, v in cut.items()},
                 "families": {}}
    for fam, events in FAMILIES.items():
        f = usd[usd["event"].isin(events)]
        timed = f[f["ts_utc"].notna()]
        untimed = f[f["ts_utc"].isna()]
        fr: dict = {"events": list(events), "per_year": {}, "missing_at_source": [], "untimed_at_source": [],
                    "parse_failures": []}
        dup = timed[timed.duplicated(["ts_utc", "event"], keep=False)]
        for y in sorted(set(usd["year"].dropna())):
            t = timed[timed["year"] == y]
            r = t[~t["after_scrape"]]                  # released by the time the file was scraped
            u = untimed[untimed["year"] == y]
            if not len(t) and not len(u):
                continue
            row = {"rows": int(len(t)), "by_event": {e: int((t["event"] == e).sum()) for e in events},
                   "actual_filled": round(float((r["actual"] != "").mean()), 4) if len(r) else None,
                   "forecast_filled": round(float((r["forecast"] != "").mean()), 4) if len(r) else None,
                   "after_scrape": int(t["after_scrape"].sum()), "untimed": int(len(u)),
                   "duplicates": int((dup["year"] == y).sum())}
            if fam != "FOMC":
                rev = 0
                for _, g in timed[timed["year"] <= y].sort_values("ts_utc").groupby("event"):
                    prev_actual = g["actual_num"].shift(1)
                    r = (g["year"] == y) & g["previous_num"].notna() & prev_actual.notna() & \
                        ~np.isclose(g["previous_num"], prev_actual)
                    rev += int(r.sum())
                row["revised_previous"] = rev
            fr["per_year"][y] = row
        # parse failures: a timed row whose actual/forecast/previous is there but isn't a number
        if fam != "FOMC":
            nn = timed[timed[["actual_nonnumeric", "forecast_nonnumeric", "previous_nonnumeric"]].any(axis=1)]
            fr["parse_failures"] = [{"event": r.event, "ts_utc": r.ts_utc.isoformat()} for r in nn.itertuples()]
        fr["parse_failures"] += [{"event": r.event, "date_local": r.date_local, "time_raw": r.time_raw}
                                 for r in f[f["time_flag"].eq("date_unparsed")].itertuples()]
        # rows that are there but carry no usable time (annotations, the MC3 conflicts)
        fr["untimed_at_source"] = [{"event": r.event, "date_local": r.date_local, "time_raw": r.time_raw,
                                    "flag": r.time_flag} for r in untimed.itertuples()]
        # missing at the source: an expected release with no row in the CSV at all
        if timed.empty:
            pass
        elif fam in MONTHLY:
            for e in events:
                fr["missing_at_source"] += monthly_gaps(timed, untimed, e)
                t0 = timed.loc[timed["event"] == e, "ts_utc"].min()
                fr.setdefault("first_release_utc", {})[e] = None if pd.isna(t0) else t0.isoformat()
        elif fam == "claims":
            wk = timed["ts_utc"].dt.tz_convert(NY).dt.tz_localize(None).dt.to_period("W-SAT")
            all_w = pd.period_range(wk.min(), wk.max(), freq="W-SAT")
            for w in sorted(set(all_w) - set(wk)):
                fr["missing_at_source"].append({"event": CLAIMS[0], "release_week_ny": f"{w.start_time.date()}..{w.end_time.date()}"})
        rep["families"][fam] = fr
    return rep


def full_coverage(cov: dict) -> dict:
    """CPI and NFP: per event, the first release, the releases missing at the
    source and the rows there without a usable time. FOMC: statements per year
    (8 scheduled; 2020 adds the two unscheduled meetings)."""
    out = {}
    for fam in ("CPI", "NFP"):
        fr = cov["families"][fam]
        out[fam] = {e: {"first_release_utc": fr.get("first_release_utc", {}).get(e),
                        "missing_at_source": sum(m["missing_releases"] for m in fr["missing_at_source"] if m["event"] == e),
                        "untimed_at_source": sum(1 for m in fr["untimed_at_source"] if m["event"] == e)}
                    for e in FAMILIES[fam]}
    st = cov["families"]["FOMC"]["per_year"]
    out["FOMC"] = {y: v["by_event"]["FOMC Statement"] for y, v in st.items()}
    return out


def surprise_counts(df: pd.DataFrame) -> dict:
    s = df[df["event"].isin(SURPRISE_SERIES) & df["currency"].eq("USD") & df["ts_utc"].notna()]
    out = {}
    for e in SURPRISE_SERIES:
        g = s[s["event"] == e]
        ok = g[g["surprise"].notna()]
        out[e] = {"rows": int(len(g)), "with_surprise": int(len(ok)),
                  "no_actual": int(g["actual_num"].isna().sum()), "no_forecast": int(g["forecast_num"].isna().sum()),
                  "z_eligible_if_N": {str(n): int((ok["n_prior"] >= n).sum()) for n in N_CANDIDATES},
                  "first_eligible_utc_if_N": {str(n): (ok.loc[ok["n_prior"] >= n, "ts_utc"].min().isoformat()
                                                      if (ok["n_prior"] >= n).any() else None) for n in N_CANDIDATES}}
    fomc = df[df["currency"].eq("USD") & df["event"].isin(FOMC)]
    out["FOMC (no surprise, by rule)"] = {"rows": int(len(fomc)), "with_surprise": 0}
    return out


# --------------------------------------------------------------------------- build

def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def load(src: Path) -> tuple[dict[int, pd.DataFrame], list[int]]:
    raw, missing = {}, []
    for y in YEARS:
        p = src / f"forex_factory_calendar_{y}.csv"
        if p.exists():
            raw[y] = read_raw(p)
        else:
            missing.append(y)
    return raw, missing


def build(src: Path = SRC) -> tuple[pd.DataFrame, dict]:
    raw, missing = load(src)
    if not raw:
        raise SystemExit(f"no forex_factory_calendar_<year>.csv in {src}")
    parts, files = [], {}
    for y, r in raw.items():
        body, tail = split_wrap(r, y)
        df, n = clean_year(body, y)
        n["rows_in"] = len(r)
        n["year_wrap_cut"] = wrap_check(tail, y, raw.get(y + 1))
        n["source_tz"] = SOURCE_TZ[y]
        n["time_flags"] = {k: int(v) for k, v in df["time_flag"].value_counts().items()}
        files[y] = n
        parts.append(df)
    df = pd.concat(parts, ignore_index=True)
    exact = df.duplicated(keep="first")
    df = df[~exact].sort_values(["ts_utc", "date_local", "currency", "event"], na_position="last",
                                kind="stable").reset_index(drop=True)
    sealing.assert_before_global_seal(pd.DatetimeIndex(df["ts_utc"].dropna()), "macro calendar")
    df = surprises(df)
    gaps = []
    for a, b in zip(sorted(raw), sorted(raw)[1:]):     # the UTC hours no file covers at a file boundary
        end_a = local_day_end_utc(date(a, 12, 31), SOURCE_TZ[a])
        start_b = pd.Timestamp(b, 1, 1).tz_localize(SOURCE_TZ[b]).tz_convert("UTC")
        if start_b != end_a:
            gaps.append({"files": f"{a}|{b}", "from_utc": min(end_a, start_b).isoformat(),
                         "to_utc": max(end_a, start_b).isoformat(), "kind": "gap" if start_b > end_a else "overlap"})
    rep = {"years_missing": missing, "years_read": sorted(raw), "files": {str(y): v for y, v in files.items()},
           "file_boundaries": gaps, "rows_out": int(len(df)), "exact_duplicates_dropped": int(exact.sum()),
           "timed": int(df["ts_utc"].notna().sum()), "untimed": int(df["ts_utc"].isna().sum()),
           "nonnumeric_values_all_events": {c: int(df[f"{c}_nonnumeric"].sum()) for c in ("actual", "forecast", "previous")},
           "anchor_check": anchor_check(df),
           "anchor_mismatches_if_all_files_were_europe_warsaw": {str(k): v for k, v in
                                                                 anchor_mismatches_under(raw, "Europe/Warsaw").items()}}
    rep["coverage"] = coverage(df)
    rep["full_coverage"] = full_coverage(rep["coverage"])
    rep["surprises"] = surprise_counts(df)
    return df, rep


def manifest_entries(src: Path = SRC, out: Path = OUT) -> dict:
    raw, _ = load(src)
    entries = {}
    for y, r in raw.items():
        p = src / f"forex_factory_calendar_{y}.csv"
        df, n = clean_year(split_wrap(r, y)[0], y)
        n["rows_in"] = len(r)
        ts = df["ts_utc"].dropna()
        entries[f"database/historical_news/{p.name}"] = {
            "in_git": False, "status": "unused by any study",
            "source": {"provider": "Forex Factory economic calendar, scraped (tools/scraper_fxfac)",
                       "licence": "unclear (scraped): local only, never in the repo"},
            "timezone": f"Date and Time are local to the scrape's browser: {SOURCE_TZ[y]} "
                        "(docs/DECISIONS.md MC1, proven by the anchor check). Combined DateTime is local too "
                        "and is never read.",
            "periods": "research/SEALING.md: rows from 2026-10-01 on are sealed and dropped by the builder",
            "doc": "docs/MACRO_CALENDAR.md", "bytes": p.stat().st_size, "sha256": file_sha256(p),
            "rows": n["rows_in"], "first_ts": ts.min().isoformat(), "last_ts": ts.max().isoformat(),
            "sealed_rows": n["sealed_dropped"]}
    if out.exists():
        df = pd.read_parquet(out)
        ts = df["ts_utc"].dropna()
        entries["database/historical_news/macro_calendar_utc.parquet"] = {
            "in_git": False, "status": "unused by any study",
            "source": {"built_by": "tools/data/macro_calendar.py build",
                       "from": [f"database/historical_news/forex_factory_calendar_{y}.csv" for y in sorted(raw)],
                       "licence": "derived from scraped data: local only, never in the repo"},
            "timezone": "ts_utc is UTC; null where the source gives no clock time (time_flag says why)",
            "periods": "research/SEALING.md: nothing at or after 2026-10-01",
            "doc": "docs/MACRO_CALENDAR.md", "bytes": out.stat().st_size, "sha256": file_sha256(out),
            "rows": len(df), "first_ts": ts.min().isoformat(), "last_ts": ts.max().isoformat(),
            "columns": {c: str(t) for c, t in df.dtypes.items()}}
    return entries


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="tools/data/macro_calendar.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("build", "manifest"):
        p = sub.add_parser(name)
        p.add_argument("--src", type=Path, default=SRC)
        p.add_argument("--out", type=Path, default=OUT)
        if name == "build":
            p.add_argument("--report", type=Path, default=REPORT)
    a = ap.parse_args(argv)
    if a.cmd == "manifest":
        print(json.dumps(manifest_entries(a.src, a.out), indent=1))
        return 0
    df, rep = build(a.src)
    ac = rep["anchor_check"]
    print(f"years read {rep['years_read'][0]}..{rep['years_read'][-1]}, missing {rep['years_missing']}; "
          f"rows {rep['rows_out']} (timed {rep['timed']}, untimed {rep['untimed']})")
    print(f"anchor check: {ac['checked']} rows, {len(ac['mismatches'])} mismatches, "
          f"allowlisted {ac['allowlisted']}, allowlist missing {ac['allowlist_missing']}")
    if not ac["passed"]:
        for m in ac["mismatches"][:50]:
            print(f"  MISMATCH {m}", file=sys.stderr)
        print("ANCHOR CHECK FAILED: nothing written", file=sys.stderr)
        return 1
    a.out.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(a.out, index=False)
    a.report.write_text(json.dumps(rep, indent=1, default=str) + "\n", encoding="utf-8")
    print(f"wrote {a.out} and {a.report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
