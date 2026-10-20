"""Tests for expected_exclusions.py: synthetic bars only, no data files."""

import json
import random
import sys
import types
from datetime import timedelta
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import expected_exclusions as ee  # noqa: E402

UTC = "UTC"


def ts(s):
    return pd.Timestamp(s, tz=UTC)


# --- entry_time -------------------------------------------------------------

def test_entry_is_two_minutes_after_the_flush_bar_while_open():
    assert ee.entry_time(ts("2025-06-10 14:15")) == ts("2025-06-10 14:17")


def test_entry_skips_the_daily_break():
    # June: the break is 21:00-22:00 UTC (17:00-18:00 New York, EDT)
    assert ee.entry_time(ts("2025-06-10 20:59")) == ts("2025-06-10 22:00")


def test_entry_skips_the_weekend():
    # Friday 20:58 UTC + 2 min = 21:00, the Friday close (EDT) -> Sunday 22:00 UTC
    assert ee.entry_time(ts("2025-06-13 20:58")) == ts("2025-06-15 22:00")


def test_entry_time_refuses_naive_times():
    with pytest.raises(ValueError):
        ee.entry_time(pd.Timestamp("2025-06-10 14:15"))


# --- crosses_close / closure_kind -------------------------------------------

@pytest.mark.parametrize("entry, H, expected", [
    ("2025-06-10 14:00", 60, False),
    ("2025-06-10 14:00", 240, False),
    ("2025-06-10 17:00", 240, False),     # [17:00, 21:00) ends exactly at the break
    ("2025-06-10 17:01", 240, True),
    ("2025-06-10 20:00", 60, False),
    ("2025-06-10 20:01", 60, True),
    ("2025-01-14 21:30", 60, True),       # January: the break is 22:00-23:00 UTC (EST)
    ("2025-01-14 21:00", 60, False),
])
def test_crosses_close(entry, H, expected):
    assert ee.crosses_close(ts(entry), H) is expected


@pytest.mark.parametrize("H", [0, -60, True, 60.0, "60"])
def test_crosses_close_refuses_bad_horizons(H):
    with pytest.raises(ValueError):
        ee.crosses_close(ts("2025-06-10 14:00"), H)


def test_closure_kind():
    assert ee.closure_kind(ts("2025-06-10 14:00"), 240) is None
    assert ee.closure_kind(ts("2025-06-10 18:00"), 240) == "daily_break"
    assert ee.closure_kind(ts("2025-06-13 19:00"), 240) == "weekend"


# --- replay_alerts ----------------------------------------------------------

def _fake_scanner(emit_at, drop=()):
    """A stand-in scanner module: emits one alert when the bar at emit_at
    arrives, and drops the bars in `drop` in _valid_bar."""
    seen = []

    class Scanner:
        def __init__(self, levels):
            self.levels = levels

        def on_bar(self, t, o, h, lo, c):
            seen.append(t)
            if t == emit_at:
                return [{"alert_id": "A1", "alert_ts_utc": (t - timedelta(minutes=15)).strftime("%Y-%m-%d %H:%M:%S")}]
            return []

    mod = types.SimpleNamespace(
        Scanner=Scanner,
        build_levels=lambda df: ["level"],
        _valid_bar=lambda t, o, h, lo, c: t not in drop,
    )
    return mod, seen


def _bars(start, n):
    idx = pd.date_range(ts(start), periods=n, freq="1min")
    return pd.DataFrame({"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0}, index=idx)


def test_replay_alerts_records_flush_and_entry():
    bars = _bars("2025-06-10 14:00", 30)
    mod, seen = _fake_scanner(emit_at=ts("2025-06-10 14:20"), drop={ts("2025-06-10 14:05")})
    (a,) = ee.replay_alerts(bars, scanner=mod)
    assert (a.alert_id, a.flush_ts, a.entry_ts) == ("A1", ts("2025-06-10 14:20"), ts("2025-06-10 14:22"))
    assert a.alert_ts == ts("2025-06-10 14:05")
    assert ts("2025-06-10 14:05") not in seen and len(seen) == 29


def test_replay_alerts_refuses_naive_or_unsorted_bars():
    mod, _ = _fake_scanner(emit_at=None)
    naive = _bars("2025-06-10 14:00", 5).tz_localize(None)
    with pytest.raises(ValueError):
        ee.replay_alerts(naive, scanner=mod)
    with pytest.raises(ValueError):
        ee.replay_alerts(_bars("2025-06-10 14:00", 5).iloc[::-1], scanner=mod)


def test_replay_with_the_real_scanner_on_a_synthetic_walk():
    """The production level engine on 3 synthetic days: alerts come out, each
    emitted by a later bar and entered 2+ minutes after it."""
    rng, px, rows = random.Random(3), 2000.0, []
    for t in pd.date_range(ts("2025-06-09 00:00"), ts("2025-06-11 23:59"), freq="1min"):
        o = px
        px = round(px + rng.gauss(0, 0.8), 2)
        rows.append((t, o, max(o, px) + 0.2, min(o, px) - 0.2, px))
    bars = pd.DataFrame(rows, columns=["t", "open", "high", "low", "close"]).set_index("t")
    alerts = ee.replay_alerts(bars)
    assert alerts, "a 3-day random walk touches its own day opens"
    for a in alerts:
        assert a.flush_ts > a.alert_ts
        assert a.entry_ts >= a.flush_ts + ee.ENTRY_DELAY
        assert ee.load_scanner()._market_open(a.entry_ts)


def test_load_scanner_is_the_production_module():
    ds = ee.load_scanner()
    assert Path(ds.__file__).resolve() == ee.SCANNER_PATH
    assert hasattr(ds, "Scanner") and hasattr(ds, "build_levels")


# --- summarise --------------------------------------------------------------

def _alert(entry, year_ts=None):
    e = ts(entry)
    return ee.AlertTiming("A", ts(year_ts) if year_ts else e - timedelta(minutes=16), e, e)


def test_summarise_counts_and_shares():
    alerts = [
        _alert("2019-06-11 14:00"),            # crosses nothing
        _alert("2019-06-11 18:00"),            # crosses the daily break at 240 only
        _alert("2019-06-14 20:30"),            # Friday: crosses the weekend at 60 and 240
        _alert("2020-06-09 14:00"),
    ]
    out = ee.summarise(alerts, {2019: 2, 2020: 1})
    o = out["overall"]
    assert (o["alerts"], o["trading_days"], o["alerts_per_day"]) == (4, 3, round(4 / 3, 4))
    assert o["H60"] == {"crossing": 1, "crossing_daily_break": 0, "crossing_weekend": 1,
                        "crossing_share": 0.25, "eligible_per_day": 1.0}
    assert o["H240"]["crossing"] == 2 and o["H240"]["crossing_daily_break"] == 1
    assert out["by_year"]["2019"]["H240"]["crossing_share"] == round(2 / 3, 4)
    assert out["by_year"]["2020"]["H240"]["crossing_share"] == 0.0


def test_summarise_year_without_alerts_has_no_share():
    out = ee.summarise([], {2019: 5})
    assert out["by_year"]["2019"]["H240"]["crossing_share"] is None
    assert out["by_year"]["2019"]["alerts_per_day"] == 0.0


def test_summarise_refuses_alerts_outside_the_trading_days():
    with pytest.raises(ValueError):
        ee.summarise([_alert("2021-06-08 14:00")], {2019: 1})


def test_summarise_refuses_zero_trading_days():
    with pytest.raises(ValueError):
        ee.summarise([], {2019: 0})


def test_entry_hour_profile_has_24_hours():
    prof = ee.entry_hour_profile([_alert("2019-06-11 14:00"), _alert("2019-06-11 14:30")])
    assert len(prof) == 24 and prof["14"] == 2 and sum(prof.values()) == 2


def test_trading_days_by_year():
    idx = pd.DatetimeIndex([ts("2019-12-30 10:00"), ts("2019-12-30 11:00"), ts("2020-01-02 10:00")])
    days = ee.trading_days_by_year(idx, lambda i: i.tz_localize(None).normalize().values.astype("datetime64[D]"))
    assert days == {2019: 1, 2020: 1}


# --- I/O edges --------------------------------------------------------------

def test_write_json_atomic(tmp_path):
    p = tmp_path / "sub" / "out.json"
    ee.write_json_atomic(p, {"b": 1, "a": 2})
    assert json.loads(p.read_text()) == {"a": 2, "b": 1}
    assert [f.name for f in p.parent.iterdir()] == ["out.json"]


def test_write_json_atomic_failure_leaves_the_old_file(tmp_path):
    p = tmp_path / "out.json"
    ee.write_json_atomic(p, {"a": 1})
    with pytest.raises(TypeError):
        ee.write_json_atomic(p, {"a": object()})
    assert json.loads(p.read_text()) == {"a": 1}
    assert [f.name for f in tmp_path.iterdir()] == ["out.json"]


def _fake_run_levels(df):
    return types.SimpleNamespace(load_minutes=lambda start: (df, 0))


def test_load_dev_bars_cuts_at_2021():
    df = _bars("2020-12-31 23:58", 4)
    out = ee.load_dev_bars(_fake_run_levels(df))
    assert out.index.max() < ee.DEV_END and len(out) == 2


def test_load_dev_bars_refuses_no_data():
    with pytest.raises(RuntimeError):
        ee.load_dev_bars(_fake_run_levels(_bars("2021-01-04 00:00", 3)))


def test_the_development_period_ends_before_the_holdout():
    assert ee.DEV_END == ts("2021-01-01") and ee.DEV_START == ts("2007-01-01")
