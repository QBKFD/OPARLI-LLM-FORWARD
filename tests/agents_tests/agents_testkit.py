"""Synthetic replay data for the agents tests, recorded through the datafeed's
own replay path."""

import random
from datetime import datetime, timedelta, timezone

import pytest

UTC = timezone.utc
# Synthetic replay data: Mon 2025-06-09 00:00 .. Wed 2025-06-11 23:59 UTC. It
# includes the 21:00-22:00 UTC daily break (dropped by datafeed's _valid_bar)
# and ~3% missing minutes.
DATA_START = datetime(2025, 6, 9, tzinfo=UTC)
DATA_END = datetime(2025, 6, 12, tzinfo=UTC)


def synthetic_rows(start=DATA_START, end=DATA_END, seed=7):
    rng, px, rows, t = random.Random(seed), 3300.0, [], start
    while t < end:
        if rng.random() > 0.03:
            o = px
            c = round(px + rng.gauss(0, 0.6), 2)
            h = round(max(o, c) + abs(rng.gauss(0, 0.3)), 2)
            lo = round(min(o, c) - abs(rng.gauss(0, 0.3)), 2)
            rows.append((t, o, h, lo, c, 1.0))
            px = c
        t += timedelta(minutes=1)
    return rows


def make_replay_db(directory, rows=None):
    """rows -> parquet -> datafeed ReplaySource -> InMemoryBus -> Recorder -> SQLite.
    The same path the datafeed uses, so the bars are kind 'replay'."""
    pd = pytest.importorskip("pandas")
    from datafeed.bus import InMemoryBus
    from datafeed.ingest.xauusd_bars import STREAM, ReplaySource
    from datafeed.recorder.recorder import Recorder
    from datafeed.store import schema

    rows = rows if rows is not None else synthetic_rows()
    parquet = directory / "replay.parquet"
    pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"]).to_parquet(parquet)
    bus = InMemoryBus()
    ReplaySource(str(parquet), coverage=(rows[0][0], rows[-1][0])).run(lambda m: bus.publish(STREAM, m))
    path = str(directory / "datafeed.sqlite")
    con = schema.connect(path)
    Recorder(bus, con, STREAM, block_ms=None, sleep=lambda s: None).run(stop_when_idle=True)
    con.close()
    return path



# A minimal discussion.md for tests, so the discussion node runs (fake
# discussants don't read it). Test text, not the owner's prompt.
TEST_DISCUSSION = """---
agent: discussion
version: test
---
## user

Round {{round}} of {{max_rounds}}. Others: {{other_votes_json}}
"""


def write_test_prompts(directory):
    """A prompt directory holding only the test discussion.md."""
    from pathlib import Path
    d = Path(directory)
    d.mkdir(parents=True, exist_ok=True)
    (d / "discussion.md").write_text(TEST_DISCUSSION)
    return d
