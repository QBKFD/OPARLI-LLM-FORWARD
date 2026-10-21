"""backend.levels.build: the level instances and their activity windows."""
import pandas as pd

from backend.levels import MIN_BARS_FULL_DAY, SESSION_CARRY_H, SESSIONS, build_levels, session_of
from tests.levels.levels_data import bars

T = lambda s: pd.Timestamp(s, tz="UTC")   # noqa: E731


def test_ids_are_the_list_positions():
    levels = build_levels(bars())
    assert [L["id"] for L in levels] == list(range(len(levels)))


def test_at_most_one_level_per_type_and_day():
    levels = build_levels(bars())
    keys = [(L["type"], L["created_day"]) for L in levels]
    assert len(keys) == len(set(keys))


def test_daily_and_weekly_levels_are_active_for_their_day():
    for L in build_levels(bars()):
        if L["type"] in ("PDH", "PDL", "PWH", "PWL", "DOPEN", "WOPEN"):
            assert L["active_from"] == L["created_day"]
            assert L["active_to"] == L["created_day"] + pd.Timedelta(days=1)


def test_session_levels_start_at_the_session_close_and_carry_24h():
    for L in build_levels(bars()):
        s = L["type"].rsplit("_", 1)[0]
        if s in SESSIONS:
            close = L["created_day"] + pd.Timedelta(hours=SESSIONS[s][1])
            assert L["active_from"] == close
            assert L["active_to"] == close + pd.Timedelta(hours=SESSION_CARRY_H)


def test_previous_day_levels_come_from_the_previous_full_day():
    b = bars()
    days = b.groupby(b.index.normalize())
    full = [d for d, g in days if len(g) >= MIN_BARS_FULL_DAY]
    by = {(L["type"], L["created_day"]): L["value"] for L in build_levels(b)}
    for prev, day in zip(full, full[1:]):
        assert by[("PDH", day)] == days.get_group(prev)["high"].max()
        assert by[("PDL", day)] == days.get_group(prev)["low"].min()


def test_no_look_ahead():
    """Corrupting every bar from a day on changes no level created on that day
    from earlier periods (selftest case 1, on synthetic bars)."""
    b = bars()
    d0 = T("2025-06-11")
    before = {L["type"]: L["value"] for L in build_levels(b) if L["created_day"] == d0}
    b2 = b.copy()
    b2.loc[b2.index >= d0, "high"] += 10000
    after = {L["type"]: L["value"] for L in build_levels(b2) if L["created_day"] == d0}
    for t in ("PDH", "PDL", "PWH", "PWL"):
        assert t in before and before[t] == after[t], t


def test_session_of_is_the_utc_hour():
    assert session_of(T("2025-06-02 06:59")) == "ASIA"
    assert session_of(T("2025-06-02 07:00")) == "LONDON"
    assert session_of(T("2025-06-02 12:00")) == "NY"
    assert session_of(T("2025-06-02 21:30")) == "ROLLOVER"
