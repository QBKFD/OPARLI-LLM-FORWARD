"""Touches while no scanner was running are never sent late: they are journaled
as status 'missed_while_down' (with epoch). Both bar sources go through AlertSink."""

import inspect
import threading
from pathlib import Path

import pandas as pd
import pytest

import deterministic_scanner as ds
from datafeed.bus import InMemoryBus
from datafeed.ingest.xauusd_bars import STREAM

from scanner_helpers import FakeLive, Log, bar_msg, tg_msg

T = lambda s: pd.Timestamp(s, tz="UTC")   # noqa: E731


def _a(id, ts, types="PDH", price=3400.0):
    return dict(alert_id=id, alert_ts_utc=ts, level_types=types, level_price=price,
                price_at_alert=price, session=ds.session_of(T(ts)), confluence_count=1)


def _sink(con, **kw):
    sent = []
    kw.setdefault("log", Log())
    return ds.AlertSink(con, kw.pop("epoch", 2), kw.pop("bar_source", "stream"), sent.append, **kw), sent


def _rows(con):
    return con.execute("SELECT alert_id, alert_ts_utc, status, epoch FROM alerts ORDER BY rowid"
                       ).fetchall()


def test_live_alert_after_the_warmup_is_sent(tmp_path):
    con = ds.open_log(tmp_path / "j.sqlite")
    sink, sent = _sink(con, warm_end=T("2025-06-16 10:00"))
    assert sink(_a("A20250616-001", "2025-06-16 10:01:00")) == "sent"
    assert [x["alert_id"] for x in sent] == ["A20250616-001"]
    assert _rows(con) == [("A20250616-001", "2025-06-16 10:01:00", "sent", 2)]


def test_late_flush_of_a_touch_on_a_warmup_bar_is_not_sent(tmp_path):
    con = ds.open_log(tmp_path / "j.sqlite")
    sink, sent = _sink(con, warm_end=T("2025-06-16 10:00"))
    assert sink(_a("A20250616-001", "2025-06-16 09:58:00")) == "missed_while_down"
    assert sink(_a("A20250616-002", "2025-06-16 10:00:00")) == "missed_while_down"   # <= warm end
    assert sent == []
    assert [r[2:] for r in _rows(con)] == [("missed_while_down", 2)] * 2
    assert sink.log.having("missed while down (journaled, not sent)")


def test_startup_records_the_open_bucket_and_later(tmp_path):
    con = ds.open_log(tmp_path / "j.sqlite")
    sink, sent = _sink(con, down_from=T("2025-06-16 07:05"), warm_end=T("2025-06-16 09:00"))
    warm = [_a("A20250616-001", "2025-06-16 06:50:00"),      # flushed by the previous run
            _a("A20250616-002", "2025-06-16 07:02:00"),      # its bucket was still open
            _a("A20250616-003", "2025-06-16 08:10:00")]      # touched while down
    assert sink.startup(warm) == 2
    assert [r[0] for r in _rows(con)] == ["A20250616-002", "A20250616-003"]
    assert {r[2] for r in _rows(con)} == {"missed_while_down"} and sent == []
    assert sink.startup(warm) == 0                           # a second start doesn't repeat them


def test_startup_without_a_previous_run_records_nothing(tmp_path):
    con = ds.open_log(tmp_path / "j.sqlite")
    sink, _ = _sink(con, down_from=None, warm_end=T("2025-06-16 09:00"))
    assert sink.startup([_a("A20250616-001", "2025-06-16 08:10:00")]) == 0
    assert _rows(con) == []


def test_an_alert_already_journaled_is_not_repeated(tmp_path):
    con = ds.open_log(tmp_path / "j.sqlite")
    ds.insert_alert(con, _a("A20250616-002", "2025-06-16 07:02:00"), epoch=1, bar_source="ibkr")
    con.commit()
    sink, sent = _sink(con, down_from=T("2025-06-16 07:05"), warm_end=T("2025-06-16 09:00"))
    assert sink.startup([_a("A20250616-002", "2025-06-16 07:02:00")]) == 0    # sent in epoch 1
    a = _a("A20250616-004", "2025-06-16 09:30:00")
    assert sink(a) == "sent" and sink(a) == "duplicate" and len(sent) == 1


def test_missed_alerts_are_never_decided_but_get_counterfactuals(tmp_path):
    con = ds.open_log(tmp_path / "j.sqlite", check_same_thread=False)
    sink, _ = _sink(con, warm_end=T("2025-06-16 01:00"))
    sink(_a("A20250616-001", "2025-06-16 00:30:00"))                 # missed
    sink(_a("A20250616-002", "2025-06-16 01:30:00"))                 # sent
    assert ds.undecided_alerts(con) == [("A20250616-002", "2025-06-16 01:30:00", 2)]
    assert ds.sweep_auto_missed(con, T("2025-06-16 08:00")) == 1      # only the sent one
    df = pd.DataFrame({"open": 1.0, "high": 2.0, "low": 0.5, "close": 1.0},
                      index=pd.date_range("2025-06-16", periods=600, freq="1min", tz="UTC"))
    assert ds.sweep_counterfactuals(con, df, T("2025-06-16 08:00")) == 2
    tg = type("Tg", (), {"sent": [], "send": lambda self, t: self.sent.append(t)})()
    ds._handle_reply(con, threading.Lock(), tg, tg_msg("A20250616-001 SKIP late"), 2)
    assert tg.sent == ["unknown alert_id A20250616-001"]              # never shown, can't be decided


def test_progress_is_the_next_down_from(tmp_path):
    con = ds.open_log(tmp_path / "j.sqlite")
    assert ds.last_live_bar(con) is None
    ds.record_progress(con, T("2025-06-16 10:00"))
    ds.record_progress(con, T("2025-06-16 10:01"))
    assert ds.last_live_bar(con) == T("2025-06-16 10:01")
    assert con.execute("SELECT COUNT(*) FROM scanner_state").fetchone()[0] == 1


def test_stream_feed_records_progress_after_each_live_bar(tmp_path):
    con = ds.open_log(tmp_path / "j.sqlite")
    sink, sent = _sink(con, warm_end=T("2025-06-16 09:59"))
    bus = InMemoryBus()
    for t in ("10:00", "10:01"):
        bus.publish(STREAM, bar_msg(f"2025-06-16T{t}:00", 3400, 3401, 3399, 3400.5))
    bus.publish(STREAM, bar_msg("2025-06-16T10:05:00", 3400, 3401, 3399, 3400.5, history=True))
    feed = ds.StreamFeed(bus, STREAM, "s", FakeLive(), sink, block_ms=None, log=Log(),
                         last_bar_ts=T("2025-06-16 09:59"), progress=sink.progress)
    feed.step()
    assert ds.last_live_bar(con) == T("2025-06-16 10:01")            # history bars aren't progress


def test_both_live_paths_use_the_sink():
    for fn in (ds.run_live, ds.run_live_stream):
        src = inspect.getsource(fn)
        assert src.index("down_from = last_live_bar(con)") < src.index("sink.startup(live.warmup_alerts)")
        assert "AlertSink(con, epoch," in src
    assert "sink.progress(ts)" in inspect.getsource(ds.run_live)
    assert "progress=sink.progress" in inspect.getsource(ds.run_live_stream)


# ── a real restart over parquet bars ────────────────────────────────────────
PARQUET = Path(ds.PARQUET)


@pytest.mark.skipif(not PARQUET.exists(), reason="historical parquet not present (untracked)")
@pytest.mark.parametrize("restart", ["2025-06-16 07:13", "2025-06-16 09:00"])
def test_restart_coverage_is_complete_and_nothing_is_sent_late(tmp_path, restart):
    """Process A runs until 07:05 (its 07:00 bucket still open), no scanner until
    `restart`, then process B. Sent by A + sent by B + missed_while_down by B is
    exactly what one continuous run emits, and B sends nothing touched before it
    came online. 07:13: the 07:11 ASIA_L touch is in B's warm-up but its bucket
    closes live (the late-flush path). 09:00: the warm-up pass flushes it."""
    df = ds.load_data()
    d, stop, up, end = T("2025-06-16"), T("2025-06-16 07:05"), T(restart), T("2025-06-16 12:00")
    win = df[(df.index >= ds.warmup_start(d)) & (df.index < end)]
    sc, ref = ds.Scanner(ds.build_levels(win)), []
    for r in win.itertuples(name=None):
        out = sc.on_bar(*r)
        if r[0] >= d:
            ref += out
    con = ds.open_log(tmp_path / "j.sqlite")

    def run(epoch, start, stop_at):
        known = win.index + pd.Timedelta(minutes=1) <= start
        hist = win[known].copy()
        down_from = ds.last_live_bar(con)
        live = ds.LiveLevels(hist, start)
        sink, sent = _sink(con, epoch=epoch, down_from=down_from, warm_end=hist.index.max())
        sink.startup(live.warmup_alerts)
        for r in win[~known & (win.index < stop_at)].itertuples(name=None):
            for a in live.on_bar(*r):
                sink(a)
            sink.progress(r[0])
        return sent

    sent_a = run(1, d, stop)
    sent_b = run(1, up, end)
    missed = con.execute("SELECT alert_id, alert_ts_utc, level_types FROM alerts "
                         "WHERE status = 'missed_while_down'").fetchall()
    key = lambda a: (a["alert_id"], a["alert_ts_utc"], a["level_types"])   # noqa: E731
    assert missed, "the window must contain a touch while down"
    assert sorted([key(a) for a in sent_a + sent_b] + missed) == sorted(map(key, ref))
    assert all(T(a["alert_ts_utc"]) >= up for a in sent_b)               # nothing late
    assert ("2025-06-16 07:11:00", "ASIA_L") in {(m[1], m[2]) for m in missed}
