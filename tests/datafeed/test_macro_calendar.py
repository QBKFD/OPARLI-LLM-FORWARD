"""tools/data/macro_calendar.py (docs/MACRO_CALENDAR.md). Made-up calendar rows
only; the checks on the real files skip when they're absent (untracked)."""
import importlib.util
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from backend.datafeed.store import manifest

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("macro_calendar", ROOT / "tools" / "data" / "macro_calendar.py")
mc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mc)
sys.path.insert(0, str(ROOT / "research"))
import data_guard as dg  # noqa: E402

KEYS = [f"database/historical_news/forex_factory_calendar_{y}.csv" for y in range(2015, 2027)] + \
       ["database/historical_news/macro_calendar_utc.parquet"]


def raw(rows):
    """rows: (Date, Time, Currency, Event, Actual, Forecast, Previous)."""
    return pd.DataFrame([{"Date": d, "Time": t, "Currency": c, "Event": e, "Impact": "High", "Actual": a,
                          "Forecast": f, "Previous": p} for d, t, c, e, a, f, p in rows])


def write_csv(dir_, year, rows):
    df = raw(rows)
    df["Combined DateTime"] = "1999-01-01 00:00:00"          # never read: a wrong value can't leak in
    df[mc.COLUMNS].to_csv(dir_ / f"forex_factory_calendar_{year}.csv", index=False)


# --------------------------------------------------------------------------- parsing

@pytest.mark.parametrize("t, flag", [("8:30pm", None), ("12:05am", None), ("All Day", "all_day"),
                                     ("Tentative", "tentative"), ("Oct Data", "period_data"),
                                     ("June Data", "period_data"), ("Q2 Data", "period_data"), ("Day 2", "day_n"),
                                     ("Sep 27th", "other_nontime"), ("22nd-26th", "other_nontime")])
def test_time_flag(t, flag):
    assert mc.time_flag(t) == flag


def test_clock():
    assert mc.clock("12:05am") == (0, 5) and mc.clock("12:30pm") == (12, 30) and mc.clock("8:30pm") == (20, 30)


def test_leap_day_rebuilt_from_the_file_year_and_checked_by_weekday():
    assert mc.parse_date("2016-03-01", 2016) == (date(2016, 3, 1), False)
    assert mc.parse_date("Mon Feb 29", 2016) == (date(2016, 2, 29), True)
    assert mc.parse_date("Tue Feb 29", 2016) == (None, False)            # wrong weekday
    assert mc.parse_date("Mon Feb 29", 2017) == (None, False)            # no such day
    assert mc.parse_date("soon", 2016) == (None, False)


@pytest.mark.parametrize("s, want", [("175K", (175e3, "K", False)), ("222k", (222e3, "K", False)),
                                     ("0.3%", (0.3, "%", False)), ("-1.2B", (-1.2e9, "B", False)),
                                     ("4.5M", (4.5e6, "M", False)), ("2T", (2e12, "T", False)),
                                     ("52.1", (52.1, None, False)), ("0-0-9", (np.nan, None, True)),
                                     ("<0.25%", (np.nan, None, True)), ("Pass", (np.nan, None, True))])
def test_parse_number(s, want):
    v, unit, nn = mc.parse_number(s)
    assert (unit, nn) == want[1:] and (np.isnan(v) if np.isnan(want[0]) else v == pytest.approx(want[0]))


def test_empty_value_is_not_a_parse_failure():
    v, unit, nn = mc.parse_number("")
    assert np.isnan(v) and unit is None and nn is False


@pytest.mark.parametrize("d, t, tz, ny", [
    (date(2024, 11, 1), "1:30pm", "Europe/Warsaw", "08:30"),     # EU back to winter time, US not yet
    (date(2024, 3, 15), "1:30pm", "Europe/Warsaw", "08:30"),     # US on summer time, EU not yet
    (date(2024, 4, 10), "2:30pm", "Europe/Warsaw", "08:30"),
    (date(2024, 1, 11), "2:30pm", "Europe/Warsaw", "08:30"),
    (date(2019, 7, 5), "8:30pm", "Etc/GMT-8", "08:30"),          # fixed UTC+8: 9:30pm in US winter
    (date(2019, 12, 6), "9:30pm", "Etc/GMT-8", "08:30"),
])
def test_localize_handles_the_dst_desync_weeks(d, t, tz, ny):
    ts, flag = mc.localize(d, mc.clock(t), tz)
    assert flag is None and ts.tz_convert(mc.NY).strftime("%H:%M") == ny


def test_a_local_time_a_dst_change_skips_or_repeats_gets_no_time():
    assert mc.localize(date(2024, 3, 31), (2, 30), "Europe/Warsaw") == (None, "nonexistent_local")
    assert mc.localize(date(2024, 10, 27), (2, 30), "Europe/Warsaw") == (None, "ambiguous_local")


def test_every_year_has_a_source_tz():
    assert set(mc.SOURCE_TZ) == set(mc.YEARS)


# --------------------------------------------------------------------------- one file

def test_clean_year_flags_untimed_rows_and_drops_sealed_ones_before_parsing():
    df, n = mc.clean_year(raw([
        ("2026-09-30", "All Day", "USD", "Bank Holiday", "", "", ""),          # local day ends before the seal: kept
        ("2026-09-30", "11:00pm", "USD", "Late", "1.0", "", ""),               # 21:00 UTC: kept
        ("2026-10-01", "1:30am", "USD", "Early", "1.0", "", ""),               # 23:30 UTC on 09-30: kept
        ("2026-10-01", "2:30am", "USD", "Sealed", "1.0", "", ""),              # 00:30 UTC on 10-01: sealed
        ("2026-10-01", "All Day", "USD", "Sealed day", "", "", ""),           # most of the day is sealed
        ("2026-06-05", "Oct Data", "USD", "Non-Farm Employment Change", "", "", ""),
    ]), 2026)
    assert n["sealed_dropped"] == 2 and len(df) == 4
    assert not df["event"].str.startswith("Sealed").any()
    assert df["ts_utc"].max() < mc.SEAL
    by = df.set_index("event")
    assert pd.isna(by.loc["Bank Holiday", "ts_utc"]) and by.loc["Bank Holiday", "time_flag"] == "all_day"
    assert pd.isna(by.loc["Non-Farm Employment Change", "ts_utc"])
    assert by.loc["Non-Farm Employment Change", "time_flag"] == "period_data"
    assert by.loc["Early", "ts_utc"] == pd.Timestamp("2026-09-30 23:30", tz="UTC")


def test_a_listed_source_time_conflict_gets_no_time():
    df, n = mc.clean_year(raw([("2021-03-10", "8:30pm", "USD", "CPI y/y", "1.7%", "1.7%", "1.4%"),
                               ("2021-03-10", "9:30pm", "USD", "CPI m/m", "0.4%", "0.4%", "0.3%")]), 2021)
    by = df.set_index("event")
    assert pd.isna(by.loc["CPI y/y", "ts_utc"]) and by.loc["CPI y/y", "time_flag"] == "source_time_conflict"
    assert by.loc["CPI m/m", "ts_utc"] == pd.Timestamp("2021-03-10 13:30", tz="UTC")
    assert n["source_time_conflict"] == 1


def test_the_year_wrap_tail_is_cut_and_checked_against_the_next_file():
    r = raw([("2024-12-30", "2:30pm", "USD", "A", "", "", ""), ("2024-12-31", "All Day", "USD", "Bank Holiday", "", "", ""),
             ("2024-01-02", "2:30pm", "USD", "A", "", "", ""),            # really 2025-01-02
             ("2024-01-03", "4:00pm", "USD", "B", "", "", "")])           # really 2025-01-03
    body, tail = mc.split_wrap(r, 2024)
    assert body["Date"].tolist() == ["2024-12-30", "2024-12-31"] and len(tail) == 2
    nxt = raw([("2025-01-02", "2:30pm", "USD", "A", "", "", "")])
    w = mc.wrap_check(tail, 2024, nxt)
    assert w["matched_in_next_file"] == 1 and w["unmatched"] == ["2024-01-03 4:00pm USD B"]
    ordered = raw([("2024-01-02", "2:30pm", "USD", "A", "", "", ""), ("2024-12-31", "2:30pm", "USD", "A", "", "", "")])
    assert len(mc.split_wrap(ordered, 2024)[1]) == 0


# --------------------------------------------------------------------------- anchors

def _anchor_rows(cpi_time="2:30pm", fomc=()):
    rows = [("2024-01-11", cpi_time, "USD", "CPI m/m", "0.3%", "0.2%", "0.1%"),
            ("2024-01-31", "8:00pm", "USD", "FOMC Statement", "", "", "")]
    return mc.clean_year(raw(rows + list(fomc)), 2024)[0]


def test_anchor_check_passes_on_ny_times_and_fails_loudly_otherwise(monkeypatch):
    monkeypatch.setattr(mc, "ANCHOR_ALLOW", set())
    assert mc.anchor_check(_anchor_rows())["passed"]
    bad = mc.anchor_check(_anchor_rows(cpi_time="1:30pm"))
    assert not bad["passed"] and bad["mismatches"][0]["ny"] == "07:30"


def test_anchor_allowlist_covers_only_fomc_and_must_be_found(monkeypatch):
    monkeypatch.setattr(mc, "ANCHOR_ALLOW", {("2024-03-03", "10:00")})
    extra = [("2024-03-03", "4:00pm", "USD", "FOMC Statement", "", "", "")]           # 10:00 NY
    assert mc.anchor_check(_anchor_rows(fomc=extra))["passed"]
    cpi = [("2024-03-03", "4:00pm", "USD", "CPI m/m", "", "", "")]                   # CPI at an allowlisted time
    assert not mc.anchor_check(_anchor_rows(fomc=extra + cpi))["passed"]
    stale = mc.anchor_check(_anchor_rows())
    assert not stale["passed"] and stale["allowlist_missing"] == ["2024-03-03 10:00 NY"]


def test_the_shipped_allowlist_is_the_two_unscheduled_2020_meetings():
    assert mc.ANCHOR_ALLOW == {("2020-03-03", "10:00"), ("2020-03-15", "17:00")}


# --------------------------------------------------------------------------- surprises

def test_surprises_have_no_look_ahead_and_skip_fomc_and_untimed_rows():
    df, _ = mc.clean_year(raw([
        ("2024-02-02", "2:30pm", "USD", "Non-Farm Employment Change", "353K", "185K", "216K"),
        ("2024-03-08", "2:30pm", "USD", "Non-Farm Employment Change", "275K", "198K", "229K"),
        ("2024-03-08", "2:30pm", "USD", "CPI m/m", "0.4%", "0.4%", "0.3%"),           # another series, same time
        ("2024-04-05", "2:30pm", "USD", "Non-Farm Employment Change", "303K", "", "270K"),   # no forecast
        ("2024-05-03", "2:30pm", "USD", "Non-Farm Employment Change", "175K", "243K", "315K"),
        ("2024-05-03", "Oct Data", "USD", "Non-Farm Employment Change", "1K", "2K", ""),
        ("2024-01-31", "8:00pm", "USD", "Federal Funds Rate", "5.50%", "5.50%", "5.50%"),
        ("2024-03-08", "2:30pm", "CAD", "Employment Change", "40.7K", "20.0K", "37.3K"),
    ]), 2024)
    s = mc.surprises(df)
    nfp = s[(s["event"] == "Non-Farm Employment Change") & s["ts_utc"].notna()].sort_values("ts_utc")
    assert nfp["surprise"].tolist()[:2] == pytest.approx([168e3, 77e3])
    assert np.isnan(nfp["surprise"].iloc[2]) and pd.isna(nfp["n_prior"].iloc[2])
    assert nfp["n_prior"].tolist()[:2] == [0, 1] and nfp["n_prior"].iloc[3] == 2
    assert s.loc[s["event"] == "CPI m/m", "n_prior"].tolist() == [0]                 # series apart
    assert s.loc[s["event"].isin(["Federal Funds Rate", "Employment Change"]), "surprise"].isna().all()
    assert s.loc[s["time_flag"] == "period_data", "surprise"].isna().all()
    assert not {"z", "surprise_z", "z_score"} & set(s.columns)                      # N not picked: no z


def test_rows_at_the_same_time_do_not_count_each_other():
    df, _ = mc.clean_year(raw([("2024-02-02", "2:30pm", "USD", "CPI m/m", "0.3%", "0.2%", ""),
                               ("2024-02-02", "2:30pm", "USD", "CPI m/m", "0.4%", "0.2%", "")]), 2024)
    assert mc.surprises(df)["n_prior"].tolist() == [0, 0]


# --------------------------------------------------------------------------- coverage

def test_a_gap_is_missing_at_the_source_unless_the_row_is_there_untimed():
    ts = pd.to_datetime(["2025-08-12 12:30", "2025-09-11 12:30", "2025-10-24 12:30", "2026-01-13 13:30"], utc=True)
    timed = pd.DataFrame({"event": "CPI m/m", "ts_utc": ts})
    none = pd.DataFrame({c: pd.Series(dtype=object) for c in ("event", "date_local", "time_raw")})
    gaps = mc.monthly_gaps(timed, none, "CPI m/m")
    assert [(g["after_utc"][:10], g["missing_releases"]) for g in gaps] == [("2025-10-24", 2)]
    one = pd.DataFrame({"event": ["CPI m/m"], "date_local": ["2025-12-10"], "time_raw": ["Nov Data"]})
    assert mc.monthly_gaps(timed, one, "CPI m/m")[0]["missing_releases"] == 1


def _year_rows(year, nfp_time):
    return [(f"{year}-{m:02d}-0{3 + m % 5}", nfp_time, "USD", "Non-Farm Employment Change", "100K", "90K", "80K")
            for m in range(1, 13)]


def test_build_and_cli_on_made_up_files(tmp_path, capsys):
    src = tmp_path / "src"
    src.mkdir()
    write_csv(src, 2024, sorted([r for r in _year_rows(2024, "2:30pm") if r[0][5:7] not in ("03", "11")] +
                                [("2024-03-15", "1:30pm", "USD", "Non-Farm Employment Change", "1K", "1K", "1K"),
                                 ("2024-11-01", "1:30pm", "USD", "Non-Farm Employment Change", "1K", "1K", "1K")]) +
              [("2024-01-04", "2:30pm", "USD", "Non-Farm Employment Change", "1K", "1K", "1K")])   # year wrap
    write_csv(src, 2025, sorted([r for r in _year_rows(2025, "2:30pm") if r[0][5:7] not in ("03", "10", "11")] +
                                [("2025-03-07", "2:30pm", "USD", "Non-Farm Employment Change", "1K", "1K", "1K"),
                                 ("2025-12-16", "Oct Data", "USD", "Non-Farm Employment Change", "", "", "")]))
    out, rep_path = tmp_path / "out.parquet", tmp_path / "report.json"
    monkey_allow = mc.ANCHOR_ALLOW
    mc.ANCHOR_ALLOW = set()
    try:
        assert mc.main(["build", "--src", str(src), "--out", str(out), "--report", str(rep_path)]) == 0
        rep = json.loads(rep_path.read_text())
        assert rep["years_read"] == [2024, 2025] and 2015 in rep["years_missing"]
        assert rep["files"]["2024"]["year_wrap_cut"]["rows"] == 1 and rep["files"]["2024"]["rows_in"] == 13
        assert rep["anchor_check"]["passed"] and rep["anchor_check"]["checked"] == 22
        nfp = rep["coverage"]["families"]["NFP"]
        assert [g["missing_releases"] for g in nfp["missing_at_source"]] == [2]       # Oct and Nov 2025
        assert nfp["untimed_at_source"][0]["time_raw"] == "Oct Data"
        assert "100K" not in rep_path.read_text() and "90K" not in rep_path.read_text()   # counts only
        df = pd.read_parquet(out)
        assert len(df) == 23 and df["ts_utc"].isna().sum() == 1
        # a wrong anchor: exit 1, nothing written
        write_csv(src, 2025, [("2025-01-10", "3:30pm", "USD", "Non-Farm Employment Change", "1K", "1K", "1K")])
        out.unlink()
        rep_path.unlink()
        assert mc.main(["build", "--src", str(src), "--out", str(out), "--report", str(rep_path)]) == 1
        assert not out.exists() and not rep_path.exists()
        assert "ANCHOR CHECK FAILED" in capsys.readouterr().err
    finally:
        mc.ANCHOR_ALLOW = monkey_allow


# --------------------------------------------------------------------------- manifest, guard, git

@pytest.mark.parametrize("key", KEYS)
def test_each_calendar_file_is_listed_guarded_and_ignored(key):
    e = manifest.load()["files"][key]
    assert e["status"] == "unused by any study" and e["in_git"] is False and len(e["sha256"]) == 64
    assert dg.guarded_key(Path(key).name) == key
    with pytest.raises(dg.DataGuardError, match="unused by any study"):
        dg.assert_may_read(key, study="no_such_study")
    assert subprocess.run(["git", "check-ignore", "-q", key], cwd=ROOT).returncode == 0


def test_no_calendar_row_is_tracked():
    tracked = subprocess.run(["git", "ls-files", "database/"], cwd=ROOT, capture_output=True, text=True,
                             check=True).stdout.split()
    assert not [p for p in tracked if p.endswith((".csv", ".parquet"))]


@pytest.mark.parametrize("key", KEYS)
def test_the_real_files_match_the_manifest(key):
    path = ROOT / key
    if not path.exists():
        pytest.skip(f"{key} not present (untracked)")
    assert manifest.verify(path) == key
