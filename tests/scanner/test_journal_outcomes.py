"""Realised outcomes of TAKEs: score_take on synthetic 1-min bars (the
scoring.score rules), and sweep_outcomes on a journal (first decision per
alert only, written once, epoch kept)."""

import sqlite3

import pandas as pd
import pytest

import deterministic_scanner as ds

T = lambda s: pd.Timestamp(s, tz="UTC")          # noqa: E731


def bars(start, prices, spread=0.2):
    """One bar per minute from `start`: open = previous close, close = price,
    high/low = max/min of the two +- spread."""
    idx = pd.date_range(start, periods=len(prices), freq="1min", tz="UTC")
    opens = [prices[0]] + list(prices[:-1])
    return pd.DataFrame({"open": opens, "close": prices,
                         "high": [max(o, c) + spread for o, c in zip(opens, prices)],
                         "low": [min(o, c) - spread for o, c in zip(opens, prices)]}, index=idx)


FLAT = bars("2025-10-14 09:00", [100.0] * 400)


# ── score_take ─────────────────────────────────────────────────────────────
def test_a_long_reaching_its_target_is_final_at_once():
    df = bars("2025-10-14 09:58", [100.0, 100.0, 100.0, 100.5, 101.0, 101.5, 102.5, 102.5])
    o = ds.score_take("long", 100.0, 99.0, 102.0, "2025-10-14 10:00:30+00:00", df, T("2025-10-14 10:10"))
    assert (o["exit_type"], o["exit_price"], o["r_multiple"]) == ("tp", 102.0, 2.0)
    assert o["entry_source"] == "reply" and o["entry_price"] == 100.0
    assert o["entry_ts_utc"] == "2025-10-14 10:00:30+00:00"     # mid-minute: scored from 10:01
    assert o["entry_ref_close"] == 100.0                          # the 10:00 bar's close
    assert o["max_hold_min"] == ds.OUTCOME_MAX_HOLD_MIN == 240 and o["gap"] == 0


def test_a_short_hitting_its_stop():
    df = bars("2025-10-14 10:00", [100.0, 100.4, 100.9, 101.6, 101.0])
    o = ds.score_take("short", 100.0, 101.5, 97.0, "2025-10-14 10:00:00+00:00", df, T("2025-10-14 10:10"))
    assert o["exit_type"] == "sl" and o["r_multiple"] == -1.0 and o["reason"] == "stop"


def test_a_bar_holding_both_levels_is_a_stop():
    df = pd.DataFrame({"open": [100.0], "high": [103.0], "low": [98.0], "close": [100.0]},
                      index=pd.DatetimeIndex([T("2025-10-14 10:01")]))
    o = ds.score_take("long", 100.0, 99.0, 102.0, "2025-10-14 10:01:00+00:00", df, T("2025-10-14 15:00"))
    assert o["exit_type"] == "sl" and o["r_multiple"] == -1.0


def test_a_gap_through_the_stop_fills_at_the_open():
    df = pd.DataFrame({"open": [100.0, 97.0], "high": [100.2, 97.2], "low": [99.8, 96.8],
                       "close": [100.0, 97.0]},
                      index=pd.DatetimeIndex([T("2025-10-14 10:01"), T("2025-10-14 10:02")]))
    o = ds.score_take("long", 100.0, 99.0, 102.0, "2025-10-14 10:01:00+00:00", df, T("2025-10-14 15:00"))
    assert (o["exit_type"], o["exit_price"], o["r_multiple"], o["gap"]) == ("sl", 97.0, -3.0, 1)


def test_a_timeout_is_final_only_after_the_holding_period_settles():
    end = T("2025-10-14 10:00") + pd.Timedelta(minutes=240)
    args = ("long", 100.0, 99.0, 102.0, "2025-10-14 10:00:00+00:00", FLAT)
    assert ds.score_take(*args, end) is None
    assert ds.score_take(*args, end + pd.Timedelta(minutes=ds.OUTCOME_SETTLE_MIN - 1)) is None
    o = ds.score_take(*args, end + pd.Timedelta(minutes=ds.OUTCOME_SETTLE_MIN))
    assert o["exit_type"] == "timeout" and o["r_multiple"] == 0.0 and o["bars_used"] == 240


def test_without_an_entry_the_first_bar_open_after_the_reply_is_used():
    df = bars("2025-10-14 10:00", [100.0, 100.0, 100.3, 101.0, 103.0])
    o = ds.score_take("long", None, 99.0, 102.0, "2025-10-14 10:00:20+00:00", df, T("2025-10-14 10:10"))
    assert o["entry_source"] == "market_open" and o["entry_ts_utc"] == "2025-10-14 10:01:00+00:00"
    assert o["entry_price"] == 100.0 and o["exit_type"] == "tp" and o["r_multiple"] == 2.0


def test_without_an_entry_and_no_bar_yet_it_waits():
    assert ds.score_take("long", None, 99.0, 102.0, "2025-10-14 20:00:00+00:00", FLAT,
                         T("2025-10-14 20:30")) is None


@pytest.mark.parametrize("direction, entry, stop, target, reason", [
    (None, 100.0, 99.0, 102.0, "no direction (long/short)"),
    ("long", 100.0, None, 102.0, "no stop (sl)"),
    ("long", 100.0, 99.0, None, "no target (tp)"),
    ("long", 100.0, 101.0, 102.0, "a long needs stop < entry < target"),
    ("short", 100.0, 99.0, 97.0, "a short needs target < entry < stop"),
    ("long", None, 103.0, 102.0, "a long needs stop < target"),
    ("long", 100.0, 0.0, 102.0, "stop must be > 0"),
    ("long", 100.0, float("nan"), 102.0, "stop must be > 0"),
])
def test_takes_that_cant_be_scored_are_final_and_say_why(direction, entry, stop, target, reason):
    o = ds.score_take(direction, entry, stop, target, "2025-10-14 10:00:00+00:00", FLAT, T("2025-10-14 10:05"))
    assert (o["exit_type"], o["reason"], o["r_multiple"]) == ("unscorable", reason, None)


def test_a_market_entry_outside_the_bracket_is_unscorable():
    o = ds.score_take("long", None, 101.0, 105.0, "2025-10-14 10:00:00+00:00", FLAT, T("2025-10-14 10:05"))
    assert o["exit_type"] == "unscorable" and "market entry 100.0" in o["reason"]


def test_no_bar_in_the_holding_period():
    gap = FLAT[FLAT.index < T("2025-10-14 10:00")]
    args = ("long", 100.0, 99.0, 102.0, "2025-10-14 10:00:00+00:00", gap)
    assert ds.score_take(*args, T("2025-10-14 12:00")) is None                 # bars may still come
    o = ds.score_take(*args, T("2025-10-14 15:00"))
    assert o["exit_type"] == "unscorable" and o["reason"] == "no bar inside the holding period"


# ── sweep_outcomes on a journal ────────────────────────────────────────────
def _journal(tmp_path):
    con = ds.open_log(tmp_path / "j.sqlite")
    for i, ts in ((1, "2025-10-14 10:00:00"), (2, "2025-10-14 10:30:00"), (3, "2025-10-14 11:00:00")):
        ds.insert_alert(con, dict(alert_id=f"A20251014-00{i}", alert_ts_utc=ts, level_types="PDH",
                                  level_price=100.0, price_at_alert=100.0, session="LONDON",
                                  confluence_count=1), epoch=2, bar_source="ibkr")
    con.commit()
    return con


def _decide(con, alert, decision, ts, *, entry=100.0, stop=99.0, target=102.0, update_id=None):
    ds.record_decision(con, alert, decision, T(ts), "2025-10-14 10:00:00", "long", entry, stop, target,
                       "r", epoch=2, update_id=update_id)


def test_sweep_scores_first_decision_takes_once_and_skips_the_rest(tmp_path):
    con = _journal(tmp_path)
    _decide(con, "A20251014-001", "TAKE", "2025-10-14 10:02:00", update_id=1)
    _decide(con, "A20251014-001", "SKIP", "2025-10-14 10:20:00", update_id=2)      # a revision
    _decide(con, "A20251014-002", "SKIP", "2025-10-14 10:31:00", update_id=3)
    _decide(con, "A20251014-002", "TAKE", "2025-10-14 10:40:00", update_id=4)      # not the first
    _decide(con, "A20251014-003", "TAKE", "2025-10-14 11:01:00", stop=None, update_id=5)
    df = bars("2025-10-14 10:00", [100.0, 100.0, 100.0] + [100.5, 101.2, 102.4] + [102.4] * 300)
    log = []
    n = ds.sweep_outcomes(con, df, T("2025-10-14 10:30"), log=lambda *a, **k: log.append(a[0]))
    assert n == 2
    rows = con.execute("SELECT alert_id, exit_type, r_multiple, epoch, decision_rowid, reason FROM outcomes "
                       "ORDER BY alert_id").fetchall()
    assert rows == [("A20251014-001", "tp", 2.0, 2, 1, "target"),
                    ("A20251014-003", "unscorable", None, 2, 5, "no stop (sl)")]
    assert ds.sweep_outcomes(con, df, T("2025-10-14 16:00")) == 0                  # never twice
    assert any("A20251014-001" in line and "tp" in line for line in log)


def test_an_open_take_stays_open_until_final(tmp_path):
    con = _journal(tmp_path)
    _decide(con, "A20251014-001", "TAKE", "2025-10-14 10:00:00", target=150.0)
    assert ds.sweep_outcomes(con, FLAT, T("2025-10-14 12:00")) == 0
    assert ds.takes_needing_outcome(con)[0][1] == "A20251014-001"
    assert ds.sweep_outcomes(con, FLAT, T("2025-10-14 14:05")) == 1
    assert con.execute("SELECT exit_type FROM outcomes").fetchall() == [("timeout",)]
    assert ds.takes_needing_outcome(con) == []


def test_skip_and_missed_get_no_outcome(tmp_path):
    con = _journal(tmp_path)
    _decide(con, "A20251014-001", "SKIP", "2025-10-14 10:02:00")
    _decide(con, "A20251014-002", "MISSED", "2025-10-14 14:30:00")
    assert ds.sweep_outcomes(con, FLAT, T("2025-10-15 00:00")) == 0


def test_one_outcome_per_decision_is_enforced_by_the_database(tmp_path):
    con = _journal(tmp_path)
    _decide(con, "A20251014-001", "TAKE", "2025-10-14 10:02:00")
    o = ds.score_take("long", 100.0, 99.0, 102.0, "2025-10-14 10:02:00+00:00", FLAT, T("2025-10-15 00:00"))
    ds.insert_outcome(con, "A20251014-001", 2, 1, o)
    with pytest.raises(sqlite3.IntegrityError):
        ds.insert_outcome(con, "A20251014-001", 2, 1, o)
