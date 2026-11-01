"""backend.datafeed.calendar_source: the calendar read as of t. Made-up rows only."""

import subprocess
import sys
from datetime import datetime, timedelta, timezone

import pytest

from backend.datafeed import calendar_source as cs

UTC = timezone.utc


def dt(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=UTC)


ROWS = [
    {"ts_utc": dt("2030-01-10 13:30"), "time_flag": None, "currency": "USD", "event": "Made-up CPI m/m",
     "impact": "High", "actual": "0.4%", "forecast": "0.2%", "previous": "0.1%",
     "actual_num": 0.4, "forecast_num": 0.2, "previous_num": 0.1, "surprise": 0.2},
    {"ts_utc": dt("2030-01-10 15:00"), "time_flag": None, "currency": "EUR", "event": "Made-up Speech",
     "impact": "Low", "actual": None, "forecast": None, "previous": None,
     "actual_num": None, "forecast_num": None, "previous_num": None, "surprise": None},
    {"ts_utc": dt("2030-01-29 19:00"), "time_flag": None, "currency": "USD", "event": "Made-up Rate Decision",
     "impact": "High", "actual": "5.00%", "forecast": "5.00%", "previous": "4.75%",
     "actual_num": 5.0, "forecast_num": 5.0, "previous_num": 4.75, "surprise": float("nan")},
    {"ts_utc": None, "time_flag": "all_day", "currency": "USD", "event": "Made-up Holiday", "impact": "Holiday"},
    {"ts_utc": None, "time_flag": "tentative", "currency": "USD", "event": "Made-up Auction", "impact": "Low"},
    {"ts_utc": None, "time_flag": "all_day", "currency": "JPY", "event": "Made-up Holiday", "impact": "Holiday"},
    {"ts_utc": None, "currency": "USD", "event": "Made-up Note", "impact": "Low"},
]


@pytest.fixture
def cal():
    return cs.from_rows(ROWS)


def test_untimed_rows_are_excluded_and_counted(cal):
    assert len(cal.events) == 3
    assert cal.untimed == {"all_day": 2, "tentative": 1, "unflagged": 1}
    assert cal.n_untimed == 4
    assert [e.ts_utc for e in cal.events] == sorted(e.ts_utc for e in cal.events)


def test_actual_hidden_until_after_release(cal):
    cpi = lambda evs: next(e for e in evs if e.event == "Made-up CPI m/m")   # noqa: E731
    before = cpi(cs.events_as_of(dt("2030-01-10 13:29"), cal))
    at = cpi(cs.events_as_of(dt("2030-01-10 13:30"), cal))
    after = cpi(cs.events_as_of(dt("2030-01-10 13:30:01"), cal))
    for e in (before, at):                      # at t == ts_utc it is still hidden
        assert not e.released and e.actual is None and e.actual_num is None and e.surprise is None
        assert e.forecast == "0.2%" and e.previous == "0.1%" and e.forecast_num == 0.2   # the schedule is public
    assert after.released and after.actual == "0.4%" and after.actual_num == 0.4 and after.surprise == 0.2


def test_every_timed_event_is_listed_at_any_t(cal):
    early = cs.events_as_of(dt("2029-12-31 00:00"), cal)
    assert len(early) == 3 and not any(e.released for e in early)
    assert all(e.actual is None for e in early)


def test_nan_becomes_none(cal):
    rate = next(e for e in cal.events if e.event == "Made-up Rate Decision")
    assert rate.surprise is None and rate.actual_num == 5.0


def test_bounds_and_filter(cal):
    t = dt("2030-02-01 00:00")
    got = cs.events_as_of(t, cal, start=dt("2030-01-10 13:30"), end=dt("2030-01-29 19:00"))
    assert [e.event for e in got] == ["Made-up CPI m/m", "Made-up Speech"]     # start inclusive, end exclusive
    usd = cs.events_as_of(t, cal, where=lambda e: e.currency == "USD" and e.impact == "High")
    assert [e.event for e in usd] == ["Made-up CPI m/m", "Made-up Rate Decision"]


def test_news_window_bounds_inclusive(cal):
    t = dt("2030-01-10 14:00")
    w = cs.news_window(t, timedelta(minutes=30), timedelta(hours=1), cal)
    assert [e.event for e in w] == ["Made-up CPI m/m", "Made-up Speech"]        # 13:30 and 15:00, both edges
    assert w[0].released and not w[1].released
    assert cs.news_window(t, timedelta(minutes=29), timedelta(minutes=59), cal) == []


def test_news_window_hides_actual_of_future_events(cal):
    w = cs.news_window(dt("2030-01-10 13:00"), timedelta(0), timedelta(hours=1), cal)
    assert len(w) == 1 and w[0].actual is None and not w[0].released


def test_news_window_on_a_view_never_reveals(cal):
    view = cs.events_as_of(dt("2030-01-01 00:00"), cal)                       # everything hidden
    w = cs.news_window(dt("2030-02-01 00:00"), timedelta(days=30), timedelta(0), view)
    assert len(w) == 3 and all(e.actual is None for e in w)


def test_news_window_where_and_errors(cal):
    t = dt("2030-01-10 14:00")
    w = cs.news_window(t, timedelta(hours=1), timedelta(hours=1), cal, where=lambda e: e.currency == "EUR")
    assert [e.event for e in w] == ["Made-up Speech"]
    with pytest.raises(ValueError):
        cs.news_window(t, timedelta(minutes=-1), timedelta(0), cal)


def test_times_must_be_aware(cal):
    with pytest.raises(ValueError):
        cs.events_as_of(datetime(2030, 1, 10, 14, 0), cal)
    with pytest.raises(ValueError):
        cs.from_rows([{**ROWS[0], "ts_utc": datetime(2030, 1, 10, 13, 30)}])
    assert cs.events_as_of("2030-01-10T14:00:00+00:00", cal)[0].released


def test_other_offsets_are_converted(cal):
    t = datetime(2030, 1, 10, 8, 31, tzinfo=timezone(timedelta(hours=-5)))      # 13:31 UTC
    assert cs.events_as_of(t, cal)[0].released


def test_load_calendar_parquet(tmp_path):
    pd = pytest.importorskip("pandas")
    pytest.importorskip("pyarrow")
    df = pd.DataFrame(ROWS)
    for c in cs.LOAD_COLUMNS:
        if c not in df:
            df[c] = None
    df["ts_utc"] = pd.to_datetime(df["ts_utc"], utc=True)
    for c in ("actual_num", "forecast_num", "previous_num", "surprise"):
        df[c] = df[c].astype(float)
    p = tmp_path / "made_up_calendar.parquet"
    df.to_parquet(p, index=False)
    cal = cs.load_calendar(p)
    assert len(cal.events) == 3 and cal.n_untimed == 4 and cal.sha256
    assert cal.events[0].ts_utc == dt("2030-01-10 13:30") and cal.events[0].ts_utc.tzinfo is not None
    assert cs.load_calendar(p, expected_sha256=cal.sha256).events == cal.events
    with pytest.raises(ValueError, match="SHA-256"):
        cs.load_calendar(p, expected_sha256="0" * 64)


def test_core_import_does_not_load_pandas():
    out = subprocess.run([sys.executable, "-c",
                          "import sys; import backend.datafeed.calendar_source; print('pandas' in sys.modules)"],
                         capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "False"
