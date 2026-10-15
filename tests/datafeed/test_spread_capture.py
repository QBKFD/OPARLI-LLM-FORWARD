"""Bid/ask spread capture: per live 1-min bar, spread stats stored next to the
mid bar (migration 4), with no change to bar values or to history/replay
messages; tools/spread_by_hour.py summarises them."""

import importlib.util
import json
import math
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from datafeed.bus import InMemoryBus
from datafeed.ingest.xauusd_bars import STREAM, Bar, BarBuilder, bar_message
from datafeed.recorder.recorder import Recorder
from datafeed.store import schema
from datafeed.store.query import bars_before, spreads

UTC = timezone.utc
T0 = datetime(2026, 3, 2, 10, 0, tzinfo=UTC)          # a Monday, market open
ROOT = Path(__file__).resolve().parents[2]
OLD_KEYS = {"schema_version", "source", "symbol", "source_ts", "ingest_ts", "bar_ts",
            "open", "high", "low", "close", "n_ticks", "history"}


def tick(b, sec, bid, ask, minute=0):
    t = T0 + timedelta(minutes=minute, seconds=sec)
    return b.on_tick(t, t, bid, ask)


# ---------------------------------------------------------------- bar builder

def test_spread_stats_of_a_live_bar():
    b = BarBuilder()
    tick(b, 1, 100.0, 100.5)              # spread 0.5,  mid 100.25
    tick(b, 2, 100.25, 100.5)             # spread 0.25, mid 100.375
    b.on_tick(T0 + timedelta(seconds=3), T0 + timedelta(seconds=3), 100.3, None, 100.0)  # one-sided
    tick(b, 4, 100.0, 101.0)              # spread 1.0,  mid 100.5
    (bar,) = tick(b, 1, 100.0, 100.5, minute=1)      # next minute closes it
    assert (bar.open, bar.high, bar.low, bar.close, bar.n_ticks) == (100.25, 100.5, 100.25, 100.5, 3)
    assert bar.n_quotes == 3 == bar.n_ticks
    assert bar.spread_mean == pytest.approx((0.5 + 0.25 + 1.0) / 3)
    assert (bar.spread_median, bar.spread_max) == (0.5, 1.0)


def test_crossed_quote_gives_a_negative_spread():
    b = BarBuilder()
    tick(b, 1, 100.5, 100.0)
    (bar,) = tick(b, 1, 100.0, 100.5, minute=1)
    assert bar.close == 100.25 and bar.spread_max == -0.5 and bar.n_quotes == 1


def test_bar_values_are_the_midpoints_as_before():
    # OHLC and n_ticks recomputed independently from the mids: the spread capture
    # changes none of them
    rng = random.Random(12345)
    b, quotes, out = BarBuilder(), {}, []
    for i in range(3000):
        t = T0 + timedelta(seconds=i * 0.7)
        bid = round(4300 + rng.uniform(-5, 5), 2)
        ask = round(bid + rng.choice([0.1, 0.2, 0.3, 0.5]), 2)
        if rng.random() < 0.05:
            ask = None                                       # one-sided: skipped
        out += b.on_tick(t, t, bid, ask, 4300.0)
        if ask is not None:
            quotes.setdefault(t.replace(second=0, microsecond=0), []).append((bid, ask))
    assert len(out) > 30
    for bar in out:
        q = quotes[bar.bar_ts]
        mids = [(x + y) / 2.0 for x, y in q]
        assert (bar.open, bar.high, bar.low, bar.close, bar.n_ticks) == \
            (mids[0], max(mids), min(mids), mids[-1], len(q))
        assert bar.n_quotes == len(q) and bar.spread_max == max(y - x for x, y in q)


# ---------------------------------------------------------------- messages

def test_live_message_carries_the_spread_fields():
    bar = Bar("XAUUSD", T0, 1.0, 2.0, 0.5, 1.5, 4, spread_mean=0.3, spread_median=0.25,
              spread_max=0.6, n_quotes=4)
    m = bar_message(bar, source="ibkr", history=False, ingest_ts=T0)
    assert set(m) == OLD_KEYS | set(schema.SPREAD_FIELDS)
    assert (m["spread_mean"], m["spread_median"], m["spread_max"], m["n_quotes"]) == (0.3, 0.25, 0.6, 4)
    assert m["schema_version"] == 1                                  # additive, optional fields


@pytest.mark.parametrize("source,history,n_ticks", [("ibkr", True, None), ("replay", False, None)])
def test_history_and_replay_messages_are_unchanged(source, history, n_ticks):
    m = bar_message(Bar("XAUUSD", T0, 1.0, 2.0, 0.5, 1.5, n_ticks), source=source,
                    history=history, ingest_ts=T0)
    assert set(m) == OLD_KEYS


# ---------------------------------------------------------------- store

def live_msg(minute=0, spread=(0.3, 0.25, 0.6, 4), close=4300.5):
    bar = Bar("XAUUSD", T0 + timedelta(minutes=minute), 4300.0, 4301.0, 4299.0, close, 4,
              *(spread if spread else (None,) * 4))
    return bar_message(bar, source="ibkr", history=False, ingest_ts=T0 + timedelta(minutes=minute + 1))


def test_migration_4_from_a_version_3_store(tmp_path, monkeypatch):
    path = str(tmp_path / "v3.sqlite")
    monkeypatch.setattr(schema, "MIGRATIONS", schema.MIGRATIONS[:3])
    con = schema.connect(path)
    assert schema.schema_version(con) == 3
    con.execute("INSERT INTO bars VALUES ('XAUUSD','2026-03-02T10:00:00+00:00','live',"
                "1.0,2.0,0.5,1.5,7,'ibkr',1,'2026-03-02T10:01:02+00:00','x')")
    before = dict(con.execute("SELECT * FROM bars").fetchone())
    con.close()
    monkeypatch.undo()
    con = schema.connect(path)
    assert schema.schema_version(con) == 4 == len(schema.MIGRATIONS)
    after = dict(con.execute("SELECT * FROM bars").fetchone())
    assert {k: after[k] for k in before} == before                    # bar values untouched
    assert all(after[f] is None for f in schema.SPREAD_FIELDS)


def test_store_writes_spread_next_to_the_bar(tmp_path):
    path = str(tmp_path / "s.sqlite")
    con = schema.connect(path)
    hist = bar_message(Bar("XAUUSD", T0 + timedelta(minutes=1), 1.0, 2.0, 0.5, 1.5, None),
                       source="ibkr", history=True, ingest_ts=T0)
    con.execute("BEGIN")
    assert schema.insert_bar(con, live_msg()) == "inserted"
    assert schema.insert_bar(con, hist) == "inserted"
    # same bar values, other spread stats: still a duplicate, first recorded kept
    assert schema.insert_bar(con, live_msg(spread=(9.0, 9.0, 9.0, 9))) == "duplicate"
    assert schema.insert_bar(con, live_msg(close=4300.75)) == "conflict"
    con.execute("COMMIT")
    rows = {r["kind"]: dict(r) for r in con.execute("SELECT * FROM bars")}
    assert [rows["live"][f] for f in schema.SPREAD_FIELDS] == [0.3, 0.25, 0.6, 4]
    assert all(rows["history"][f] is None for f in schema.SPREAD_FIELDS)
    # readers of bars see exactly the old columns and values
    got = bars_before("XAUUSD", T0 + timedelta(minutes=5), 10, db=path)
    assert not set(schema.SPREAD_FIELDS) & set(got[0])
    assert (got[0]["open"], got[0]["close"], got[0]["n_ticks"]) == (4300.0, 4300.5, 4)
    assert spreads("XAUUSD", db=path) == [{"bar_ts": "2026-03-02T10:00:00+00:00", "spread_mean": 0.3,
                                           "spread_median": 0.25, "spread_max": 0.6, "n_quotes": 4}]


@pytest.mark.parametrize("bad", [
    {"spread_mean": 0.3},                                                       # partial
    {"spread_mean": 0.3, "spread_median": 0.2, "spread_max": 0.5, "n_quotes": 0},
    {"spread_mean": 0.3, "spread_median": 0.2, "spread_max": 0.5, "n_quotes": True},
    {"spread_mean": 0.3, "spread_median": 0.2, "spread_max": 0.5, "n_quotes": 2.0},
    {"spread_mean": math.nan, "spread_median": 0.2, "spread_max": 0.5, "n_quotes": 2},
    {"spread_mean": "0.3", "spread_median": 0.2, "spread_max": 0.5, "n_quotes": 2},
])
def test_malformed_spread_fields_raise(bad):
    with pytest.raises(ValueError):
        schema.spread_values(live_msg(spread=None) | bad)


def test_recorder_rejects_a_malformed_spread_and_keeps_the_rest(tmp_path):
    path = str(tmp_path / "s.sqlite")
    bus = InMemoryBus()
    bus.publish(STREAM, live_msg(0))
    bus.publish(STREAM, live_msg(1, spread=None) | {"spread_mean": 0.3})       # partial
    bus.publish(STREAM, live_msg(2))
    rec = Recorder(bus, schema.connect(path), STREAM, "recorder-1", block_ms=None,
                   sleep=lambda s: None)
    rec.run(stop_when_idle=True)
    con = schema.connect(path)
    assert [r[0] for r in con.execute("SELECT bar_ts FROM bars ORDER BY bar_ts")] == \
        ["2026-03-02T10:00:00+00:00", "2026-03-02T10:02:00+00:00"]
    (stage, raw), = con.execute("SELECT stage, raw FROM rejected_messages").fetchall()
    assert stage == "invalid" and json.loads(raw)["bar_ts"] == "2026-03-02T10:01:00+00:00"


def test_spreads_window(tmp_path):
    path = str(tmp_path / "s.sqlite")
    con = schema.connect(path)
    con.execute("BEGIN")
    for i in range(5):
        schema.insert_bar(con, live_msg(i))
    con.execute("COMMIT")
    got = spreads("XAUUSD", T0 + timedelta(minutes=1), T0 + timedelta(minutes=3), db=path)
    assert [r["bar_ts"][11:16] for r in got] == ["10:01", "10:02"]


# ---------------------------------------------------------------- tools/spread_by_hour.py

def tool():
    spec = importlib.util.spec_from_file_location("spread_by_hour", ROOT / "tools" / "spread_by_hour.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def row(ts, mean, median, mx, n):
    return {"bar_ts": ts, "spread_mean": mean, "spread_median": median, "spread_max": mx, "n_quotes": n}


def test_summary_by_hour():
    t = tool()
    rows = [row("2026-03-02T13:00:00+00:00", 0.2, 0.2, 0.3, 10),
            row("2026-03-02T13:01:00+00:00", 0.4, 0.3, 0.9, 30),
            row("2026-03-02T22:05:00+00:00", 1.0, 0.8, 2.5, 5)]
    by = {r["hour"]: r for r in t.summarise(rows)}
    assert by["13"]["bars"] == 2 and by["13"]["quotes"] == 40
    assert by["13"]["mean"] == pytest.approx((0.2 * 10 + 0.4 * 30) / 40)       # quote-weighted
    assert by["13"]["median"] == pytest.approx(0.25) and by["13"]["max"] == 0.9
    assert by["all"]["bars"] == 3 and by["all"]["max"] == 2.5
    ny = {r["hour"] for r in t.summarise(rows, "America/New_York")}             # EST: UTC-5
    assert ny == {"08", "17", "all"}


def test_cli_prints_and_writes_csv(tmp_path, capsys):
    path = str(tmp_path / "s.sqlite")
    con = schema.connect(path)
    con.execute("BEGIN")
    for i in range(3):
        schema.insert_bar(con, live_msg(i))
    con.execute("COMMIT")
    out_csv = tmp_path / "out.csv"
    t = tool()
    assert t.main(["--db", path, "--csv", str(out_csv)]) == 0
    printed = capsys.readouterr().out
    assert "| 10 | 3 | 12 | 0.300 |" in printed and "| all |" in printed
    assert out_csv.read_text().splitlines()[0] == "hour,bars,quotes,mean,median,p90,max"
    assert t.main(["--db", path, "--start", "2027-01-01T00:00:00+00:00"]) == 1   # nothing there
