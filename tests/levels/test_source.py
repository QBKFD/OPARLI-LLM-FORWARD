"""backend.levels.source: the level key and FrameLevelSource."""
import pandas as pd

from backend.levels import FrameLevelSource, build_levels, level_key, touches
from tests.levels.levels_data import START, bars

T = lambda s: pd.Timestamp(s, tz="UTC")   # noqa: E731


def test_the_key_is_type_and_utc_start():
    L = dict(type="ASIA_H", active_from=T("2025-06-16 07:00"))
    assert level_key(L) == "ASIA_H@2025-06-16T07:00Z"
    L = dict(type="PDH", active_from=pd.Timestamp("2025-06-16 09:00", tz="Europe/Berlin"))
    assert level_key(L) == "PDH@2025-06-16T07:00Z"            # always UTC


def test_keys_are_unique_within_a_build():
    keys = [level_key(L) for L in build_levels(bars())]
    assert len(keys) == len(set(keys))


def _by_key(levels):
    return {level_key(L): L for L in levels}


def test_keys_are_stable_across_rebuilds():
    """The same level has the same key whatever window it was built from (a
    longer one, or one starting later), while its id moves; its value is the
    same once its source period is complete in both."""
    b = bars(days=28)
    a_end = START + pd.Timedelta(days=21)
    a = _by_key(build_levels(b[b.index < a_end]))
    longer = _by_key(build_levels(b))
    later_start = START + pd.Timedelta(days=7)
    later = _by_key(build_levels(b[(b.index >= later_start) & (b.index < a_end)]))
    for other in (longer, later):
        common = set(a) & set(other)
        assert len(common) > 50
        for k in common:
            assert a[k]["type"] == other[k]["type"] and a[k]["active_from"] == other[k]["active_from"]
    assert any(a[k]["id"] != later[k]["id"] for k in set(a) & set(later))        # ids move, keys don't
    settled = [k for k in set(a) & set(later)
               if a[k]["created_day"] >= later_start + pd.Timedelta(days=7) and a[k]["active_to"] <= a_end]
    assert settled
    for k in settled:
        assert a[k]["value"] == later[k]["value"], k
    for k in set(a) & set(longer):
        if a[k]["active_to"] <= a_end:
            assert a[k]["value"] == longer[k]["value"], k


def test_levels_at_are_the_active_levels_with_their_key():
    src = FrameLevelSource(bars())
    t = T("2025-06-11 13:30")
    got = src.levels_at(t)
    assert got and all(L["active_from"] <= t < L["active_to"] for L in got)
    assert all(L["key"] == level_key(L) for L in got)
    assert len({L["type"] for L in got}) == len(got)                         # one per type at a time


def test_trigger_levels_are_the_levels_an_alert_was_raised_for():
    b = bars()
    src = FrameLevelSource(b)
    alerts = touches(b, build_levels(b))
    assert alerts
    for a in alerts:
        got = src.trigger_levels(a)
        assert sorted(L["type"] for L in got) == sorted(a["level_types"].split(","))
        assert abs(sum(L["value"] for L in got) / len(got) - a["level_price"]) < 1e-3
