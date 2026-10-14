"""History-flagged bars fill gaps for levels and warm-up only; they never
trigger an alert."""

from pathlib import Path

import pandas as pd
import pytest

import deterministic_scanner as ds
from datafeed.ingest.xauusd_bars import STREAM

from scanner_helpers import FakeLive, Log, bar_msg, make_bus

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
def test_history_bars_never_trigger_an_alert(kind, split):
    """2025-06-16: the bars from 00:00 to before `split` come only as history (as
    after a feed restart), and they hold the day's first touches (00:06
    DOPEN,WOPEN, 00:14 PDH, 00:24 PWH). Live bars follow until 08:00 (across the
    07:00 rebuild). None of those touches is ever alerted. The filled bars
    still count for the levels (DOPEN is the 00:00 open, ASIA_H/L include
    them) and consume first touches, so everything else equals a continuous
    replay. split 00:25: the 00:24 touch is in the 00:15 bucket that the first
    live bars close, so it reaches the suppression check."""
    df = ds.load_data()
    d = T("2025-06-16")
    win = df[(df.index >= ds.warmup_start(d)) & (df.index < T("2025-06-16 08:00"))]
    cut = T(f"2025-06-16 {split}")
    hist_bars = win[(win.index >= d) & (win.index < cut)]
    live_bars = win[win.index >= cut]
    live = ds.LiveLevels(win[win.index < d].copy(), d)
    bus, sent = make_bus(kind), []
    f = _feed(bus, live, on_alert=sent.append, sources=("replay",), last_bar_ts=win.index[win.index < d].max())
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
