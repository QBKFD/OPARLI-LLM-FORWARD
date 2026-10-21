"""backend.levels.touch: arming, first-touch-only, the collapse, touches()."""
import pandas as pd

from backend.levels import Scanner, build_levels, touches
from tests.levels.levels_data import bars


def _bars(prices, start="2025-06-03"):
    idx = pd.date_range(start, periods=len(prices), freq="1min", tz="UTC")
    return pd.DataFrame({"open": prices, "high": [x + .1 for x in prices],
                         "low": [x - .1 for x in prices], "close": prices}, index=idx)


def _level(value, idx, arm="static", typ="X", id=0):
    return dict(type=typ, value=value, active_from=idx[0], active_to=idx[-1] + pd.Timedelta(minutes=1),
                arm=arm, created_day=idx[0].normalize(), id=id)


def test_first_touch_only():
    b = _bars([100, 100, 100, 100, 100, 105, 105, 100, 100, 100])
    assert len(touches(b, [_level(100.0, b.index)], window_min=1)) == 1


def test_an_open_level_alerts_only_after_leaving_and_coming_back():
    b = _bars([100, 100, 100, 103, 103, 100])
    got = touches(b, [_level(100.0, b.index, arm="open", typ="DOPEN")], window_min=1)
    assert [a["alert_ts_utc"][-8:] for a in got] == ["00:05:00"]


def test_touches_within_tol_in_one_bucket_collapse_into_one_alert():
    b = _bars([100.0, 100.0, 100.5])
    got = touches(b, [_level(100.0, b.index, typ="PDH", id=0), _level(100.6, b.index, typ="PWH", id=1)])
    assert len(got) == 1
    assert got[0]["level_types"] == "PDH,PWH" and got[0]["confluence_count"] == 2
    assert got[0]["level_price"] == round((100.0 + 100.6) / 2, 3)


def test_alert_ids_are_numbered_per_day():
    b = _bars([100.0] * 3 + [110.0] * 3)
    lv = [_level(100.0, b.index, id=0), _level(110.0, b.index, id=1)]
    got = touches(b, lv, window_min=1)
    assert [a["alert_id"] for a in got] == ["A20250603-001", "A20250603-002"]


def test_touches_is_the_scanner_bar_by_bar_with_the_last_flush():
    b = bars()
    levels = build_levels(b)
    sc, manual = Scanner(levels), []
    for ts, o, h, l, c in b.itertuples(name=None):
        manual += sc.on_bar(ts, o, h, l, c)
    manual += sc.close()
    got = touches(b, levels)
    assert manual and got == manual
