"""History-flagged bars fill gaps for levels and warm-up only; they never
trigger an alert."""

from pathlib import Path

import pandas as pd
import pytest

import deterministic_scanner as ds
from datafeed.ingest.xauusd_bars import STREAM

from scanner_helpers import FakeLive, Log, bar_msg, make_bus, tg_msg

BUSES = ["memory", pytest.param("redis", marks=pytest.mark.redis)]
T = lambda s: pd.Timestamp(s, tz="UTC")   # noqa: E731


class FillLive(FakeLive):
    """FakeLive with LiveLevels' fill/refresh interface."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.filled, self.refreshed = set(), []

    def fill(self, ts, o, h, l, c):
        if ts in self.hist.index:
            return False
        self.hist.loc[ts] = [o, h, l, c]
        self.hist.sort_index(inplace=True)
        self.filled.add(ts)
        return True

    def refresh(self, now):
        self.refreshed.append(now)


def _hist(minutes, p=3400.0):
    return pd.DataFrame({"open": p, "high": p + 1, "low": p - 1, "close": p + 0.5},
                        index=pd.DatetimeIndex([T(m) for m in minutes]))


def _feed(bus, live, **kw):
    return ds.StreamFeed(bus, STREAM, "s", live, kw.pop("on_alert", lambda a: None), block_ms=None,
                         log=Log(), **kw)


@pytest.mark.parametrize("kind", BUSES)
def test_history_fills_gaps_and_never_reaches_on_bar(kind):
    bus = make_bus(kind)
    live = FillLive(hist=_hist(["2025-06-16 09:58", "2025-06-16 10:00"]))
    bus.publish(STREAM, bar_msg("2025-06-16T09:59:00", 3401, 3402, 3400, 3401, history=True))   # gap
    bus.publish(STREAM, bar_msg("2025-06-16T10:00:00", 3500, 3501, 3499, 3500, history=True))   # has a bar
    bus.publish(STREAM, bar_msg("2025-06-16T09:00:00", 3401, 3402, 3400, 3401, history=True))   # too old
    bus.publish(STREAM, bar_msg("2025-06-16T10:01:00", 3401, 3402, 3400, 3401, history=True,
                                source="replay"))
    f = _feed(bus, live, last_bar_ts=T("2025-06-16 10:00"))
    f.step()
    assert live.bars == []                                              # never the Scanner
    assert f.stats["history_filled"] == 1 and f.stats["history_skipped"] == 1
    assert f.stats["before_warmup"] == 1 and f.stats["wrong_source"] == 1
    assert list(live.hist.index) == [T("2025-06-16 09:58"), T("2025-06-16 09:59"), T("2025-06-16 10:00")]
    assert live.hist.loc[T("2025-06-16 10:00"), "close"] == 3400.5      # live kept over history
    assert live.refreshed == [T("2025-06-16 10:01")]                    # one rebuild for the batch
    f.step()
    assert live.refreshed == [T("2025-06-16 10:01")]                    # no fill, no rebuild


def test_newer_history_fill_keeps_live_bars_in_order():
    """A feed outage after the scanner's last live bar: the history burst fills
    minutes newer than it. The level history stays sorted for the next live bar."""
    hist = _hist(["2025-06-16 09:58"])
    live = ds.LiveLevels(hist, T("2025-06-16 09:59"))
    assert live.fill(T("2025-06-16 10:05"), 3400, 3401, 3399, 3400.5)
    assert not live.fill(T("2025-06-16 09:58"), 1, 2, 0.5, 1)
    live.on_bar(T("2025-06-16 10:06"), 3400, 3401, 3399, 3400.5)
    live.on_bar(T("2025-06-16 10:03"), 3400, 3401, 3399, 3400.5)        # not from the feed, but still sorted
    assert live.hist.index.is_monotonic_increasing
    assert live.filled == {T("2025-06-16 10:05")}


PARQUET = Path(ds.PARQUET)


@pytest.mark.skipif(not PARQUET.exists(), reason="historical parquet not present (untracked)")
@pytest.mark.parametrize("kind", BUSES)
@pytest.mark.parametrize("split", ["00:31", "00:25"])
def test_history_bars_never_trigger_an_alert(kind, split, tmp_path):
    """2025-06-16: the bars from 00:00 to before `split` come only as history (as
    after a feed restart), and they hold the day's first touches (00:06
    DOPEN,WOPEN, 00:14 PDH, 00:24 PWH). Live bars follow until 08:00 (across the
    07:00 rebuild). None of those touches is ever alerted. The filled bars
    still count for the levels (DOPEN is the 00:00 open, ASIA_H/L include
    them) and consume first touches, so everything else equals a continuous
    replay. split 00:25: the 00:24 touch is in the 00:15 bucket that the first
    live bars close, so it reaches the suppression check.

    The three touches are journaled as 'missed_history' (epoch 2), once each,
    although the refresh after the fills and the 07:00 rebuild both derive
    them; the rest is journaled as 'sent'."""
    df = ds.load_data()
    d = T("2025-06-16")
    win = df[(df.index >= ds.warmup_start(d)) & (df.index < T("2025-06-16 08:00"))]
    cut = T(f"2025-06-16 {split}")
    hist_bars = win[(win.index >= d) & (win.index < cut)]
    live_bars = win[win.index >= cut]
    live = ds.LiveLevels(win[win.index < d].copy(), d)
    bus, sent = make_bus(kind), []
    con = ds.open_log(tmp_path / "j.sqlite")
    warm_end = win.index[win.index < d].max()
    sink = ds.AlertSink(con, 2, "stream", sent.append, warm_end=warm_end, log=Log())
    f = _feed(bus, live, on_alert=sink, sources=("replay",), last_bar_ts=warm_end,
              on_history=sink.missed_history)
    for t, o, h, l, c in hist_bars.itertuples(name=None):
        bus.publish(STREAM, bar_msg(t.strftime("%Y-%m-%dT%H:%M:%S"), o, h, l, c, history=True,
                                    source="replay"))
    while f.step():
        pass
    for t, o, h, l, c in live_bars.itertuples(name=None):
        bus.publish(STREAM, bar_msg(t.strftime("%Y-%m-%dT%H:%M:%S"), o, h, l, c, source="replay"))
    while f.step():
        pass
    assert f.stats["history_filled"] == len(hist_bars) == int(split[-2:])
    cut_s = cut.strftime("%Y-%m-%d %H:%M:%S")
    assert [a for a in sent if a["alert_ts_utc"] < cut_s] == []         # never alerted
    assert f.stats["history_touch_suppressed"] == (1 if split == "00:25" else 0)
    # levels: built from the filled bars too
    lv = {(L["type"], L["created_day"]): L["value"] for L in ds.build_levels(live.hist)}
    ref = {(L["type"], L["created_day"]): L["value"] for L in ds.build_levels(win)}
    for t in ("DOPEN", "ASIA_H", "ASIA_L"):
        assert lv[(t, d)] == ref[(t, d)]
    # the rest is a continuous replay minus the touches on history bars
    sc, cont = ds.Scanner(ds.build_levels(win)), []
    for r in win.itertuples(name=None):
        out = sc.on_bar(*r)
        if r[0] >= d:
            cont += out
    want = [a for a in cont if a["alert_ts_utc"] >= cut_s]
    assert want and sent == want
    gone = [a["alert_ts_utc"][11:16] + " " + a["level_types"] for a in cont
            if a["alert_ts_utc"] < cut_s]
    assert gone == ["00:06 DOPEN,WOPEN", "00:14 PDH", "00:24 PWH"]
    rows = con.execute("SELECT alert_ts_utc, level_types, status, epoch, bar_source FROM alerts "
                       "WHERE status = 'missed_history' ORDER BY alert_ts_utc").fetchall()
    assert [(r[0][11:16] + " " + r[1]) for r in rows] == gone            # once each
    assert {r[2:] for r in rows} == {("missed_history", 2, "stream")}
    assert sink.stats["duplicate"] >= 3                                   # the 07:00 rebuild re-derived them
    assert {r[0] for r in con.execute("SELECT status FROM alerts WHERE status != 'missed_history'")} \
        == {"sent"}


def test_an_alert_id_is_never_reused_in_an_epoch(tmp_path):
    """A rebuild over history fills can renumber a day: the Scanner may hand out
    an id already journaled in this epoch for another alert. It gets the day's
    next free number."""
    con = ds.open_log(tmp_path / "j.sqlite")
    a1 = dict(alert_id="A20250616-001", alert_ts_utc="2025-06-16 01:00:00", level_types="PDH",
              level_price=1.0, price_at_alert=1.0, session="ASIA", confluence_count=1)
    sent = []
    sink = ds.AlertSink(con, 2, "stream", sent.append, warm_end=T("2025-06-16 00:00"), log=Log())
    assert sink(a1) == "sent"
    a_new = dict(a1, alert_ts_utc="2025-06-16 02:00:00", level_types="PDL")
    assert sink(a_new) == "sent"
    assert [a["alert_id"] for a in sent] == ["A20250616-001", "A20250616-002"]
    assert sink.log.having("already used in epoch 2")
    assert sink(dict(a1, alert_id="A20250616-002", alert_ts_utc="2025-06-16 03:00:00")) == "sent"
    assert sent[-1]["alert_id"] == "A20250616-003"
    ds.insert_alert(con, a1, epoch=3, bar_source="ibkr")                 # other epoch: free
    assert sorted(r[0] for r in con.execute("SELECT alert_id FROM alerts WHERE epoch = 2")) == \
        ["A20250616-001", "A20250616-002", "A20250616-003"]


def _touch(id, ts, types="PDH", price=3400.0):
    return dict(alert_id=id, alert_ts_utc=ts, level_types=types, level_price=price,
                price_at_alert=price, session="ASIA", confluence_count=1)


def test_missed_history_is_known_by_its_touch_not_its_id(tmp_path):
    con = ds.open_log(tmp_path / "j.sqlite")
    sent = []
    sink = ds.AlertSink(con, 2, "stream", sent.append, log=Log())
    assert sink.missed_history(_touch("A20250616-001", "2025-06-16 00:06:00")) == "missed_history"
    # a later rebuild derives the same touch under another id
    assert sink.missed_history(_touch("A20250616-004", "2025-06-16 00:06:00")) == "duplicate"
    assert sink.missed_history(_touch("A20250616-002", "2025-06-16 00:14:00", "PDL")) == "missed_history"
    assert sent == []
    assert con.execute("SELECT alert_id, status, epoch FROM alerts ORDER BY rowid").fetchall() == \
        [("A20250616-001", "missed_history", 2), ("A20250616-002", "missed_history", 2)]
    sink3 = ds.AlertSink(con, 3, "stream", sent.append, log=Log())     # another epoch: its own record
    assert sink3.missed_history(_touch("A20250616-001", "2025-06-16 00:06:00")) == "missed_history"


def test_missed_history_is_never_decidable_but_gets_counterfactuals(tmp_path):
    import threading
    con = ds.open_log(tmp_path / "j.sqlite", check_same_thread=False)
    sink = ds.AlertSink(con, 2, "stream", lambda a: None, warm_end=T("2025-06-16 00:00"), log=Log())
    sink.missed_history(_touch("A20250616-001", "2025-06-16 00:06:00"))
    sink(_touch("A20250616-002", "2025-06-16 01:30:00", "PDL"))         # a sent one
    assert ds.undecided_alerts(con) == [("A20250616-002", "2025-06-16 01:30:00", 2)]
    assert ds.sweep_auto_missed(con, T("2025-06-16 08:00")) == 1        # only the sent one
    assert con.execute("SELECT alert_id FROM decisions").fetchall() == [("A20250616-002",)]
    df = pd.DataFrame({"open": 1.0, "high": 2.0, "low": 0.5, "close": 1.0},
                      index=pd.date_range("2025-06-16", periods=600, freq="1min", tz="UTC"))
    assert ds.sweep_counterfactuals(con, df, T("2025-06-16 08:00")) == 2
    tg = type("Tg", (), {"sent": [], "send": lambda self, t: self.sent.append(t)})()
    ds._handle_reply(con, threading.Lock(), tg, tg_msg("A20250616-001 TAKE long 1 sl 0 tp 2 x"), 2)
    assert tg.sent == ["unknown alert_id A20250616-001"]


def test_stream_path_wires_missed_history():
    import inspect
    assert "on_history=sink.missed_history" in inspect.getsource(ds.run_live_stream)


@pytest.mark.skipif(not PARQUET.exists(), reason="historical parquet not present (untracked)")
@pytest.mark.parametrize("split, path", [("00:31", "rebuild"), ("00:25", "live flush")])
def test_three_rebuilds_give_exactly_one_missed_history_row(split, path, tmp_path):
    """Both paths, then three more rebuilds. The history-only touches reach the
    sink through the refresh after the fills (and, for split 00:25, the 00:24
    touch through a live bucket flush). Later the natural rebuilds at 00:31 or
    00:25 (first live bar), 07:00 and 12:00 derive them again under possibly
    other ids. Each touch is still exactly one missed_history row."""
    df = ds.load_data()
    d = T("2025-06-16")
    win = df[(df.index >= ds.warmup_start(d)) & (df.index < T("2025-06-16 12:30"))]
    cut = T(f"2025-06-16 {split}")
    live = ds.LiveLevels(win[win.index < d].copy(), d)
    rebuilds = []
    real = live._rebuild

    def counting(now):
        rebuilds.append(now)
        return real(now)

    live._rebuild = counting
    con = ds.open_log(tmp_path / "j.sqlite")
    warm_end = win.index[win.index < d].max()
    seen = []
    sink = ds.AlertSink(con, 2, "stream", lambda a: None, warm_end=warm_end, log=Log())

    def on_history(a):
        seen.append(a["alert_ts_utc"][11:16])
        return sink.missed_history(a)

    bus = make_bus("memory")
    f = _feed(bus, live, on_alert=sink, sources=("replay",), last_bar_ts=warm_end,
              on_history=on_history)
    for t, o, h, l, c in win[(win.index >= d) & (win.index < cut)].itertuples(name=None):
        bus.publish(STREAM, bar_msg(t.strftime("%Y-%m-%dT%H:%M:%S"), o, h, l, c, history=True,
                                    source="replay"))
    for t, o, h, l, c in win[win.index >= cut].itertuples(name=None):
        bus.publish(STREAM, bar_msg(t.strftime("%Y-%m-%dT%H:%M:%S"), o, h, l, c, source="replay"))
    while f.step():
        pass
    after_fill = [r for r in rebuilds if r >= cut - pd.Timedelta(minutes=1)]
    assert len(after_fill) >= 4                  # refresh + first live bar + 07:00 + 12:00
    assert {r.strftime("%H:%M") for r in after_fill} >= {"07:00", "12:00"}
    assert f.stats["history_touch_suppressed"] == (1 if path == "live flush" else 0)
    rows = con.execute("SELECT alert_ts_utc, level_types, COUNT(*) FROM alerts "
                       "WHERE status = 'missed_history' GROUP BY alert_ts_utc, level_types "
                       "ORDER BY alert_ts_utc").fetchall()
    assert [(r[0][11:16], r[1], r[2]) for r in rows] == [
        ("00:06", "DOPEN,WOPEN", 1), ("00:14", "PDH", 1), ("00:24", "PWH", 1)]
    for t in ("00:06", "00:14", "00:24"):
        assert seen.count(t) >= 3                # handed over again and again, journaled once
