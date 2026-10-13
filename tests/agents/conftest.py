"""tests/agents runs under tests/, so pytest also loads tests/conftest.py,
which puts backend/ first on sys.path for the old tests. backend/agents/ is a
package too, so `import agents` could load the old code. Take backend/ off the
path, as tests/datafeed does. test_agents_package.py checks which `agents`
package was loaded.

There is no __init__.py in this directory on purpose: with one, pytest would
import this directory itself as a package named `agents`. Test module names
must therefore not clash with tests/datafeed (no test_package.py, test_store.py,
test_end_to_end.py here)."""

import sys
from pathlib import Path

_BACKEND = str(Path(__file__).resolve().parents[2] / "backend")
sys.path[:] = [p for p in sys.path if p != _BACKEND]

import random  # noqa: E402
from datetime import datetime, timedelta, timezone  # noqa: E402

import pytest  # noqa: E402

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


@pytest.fixture(scope="session")
def replay_db(tmp_path_factory):
    return make_replay_db(tmp_path_factory.mktemp("replay"))
