"""LangGraph Studio entry point: the agents graph on a replay datafeed store.

`langgraph dev` loads `make_graph` through langgraph.json at the repo root
(docs/LANGGRAPH_STUDIO.md). Nothing here calls an LLM, needs an API key or
opens a network connection, and nothing is written outside a temp directory:
- **Datafeed store.** $STUDIO_DATAFEED_DB if set (a replay store, for example
  one written by `build-db` below). Otherwise a synthetic
  replay store, built once per process in a temp directory: Mon 2025-06-09 ..
  Wed 2025-06-11 UTC, a seeded random walk recorded through datafeed's own
  replay path (ReplaySource -> InMemoryBus -> Recorder). The bars are
  synthetic, not market data.
- **Config.** agents/config.example.toml (replay mode, debate off), or
  $STUDIO_AGENTS_CONFIG. Live mode is refused: Studio is for replay only.
- **Agent store.** In memory (MemoryStore), so Studio runs leave no rows.

The seal holds as everywhere: replay as_of after 2026-10-01 raises SealedError.
A store built from the research parquet is limited to bars before
2021-01-01 (the development period; build_parquet_db).

    python tools/langgraph_studio/studio.py run --t 2025-06-11T14:30:00Z   # one run, JSON summary
    python tools/langgraph_studio/studio.py build-db --synthetic --out S.sqlite
    python tools/langgraph_studio/studio.py build-db --parquet P --start 2020-06-08T00:00Z \
        --end 2020-06-13T00:00Z --out S.sqlite

It lives in tools/, not in the agents package: it records bars through
datafeed's bus and recorder, and the agents package reads datafeed only
through store.query (tests/agents_tests/test_agents_package.py).
"""

from __future__ import annotations

import argparse
import asyncio
import functools
import json
import logging
import os
import random
import shutil
import sys
import tempfile
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

import agents
from agents.config import AgentsConfig
from agents.graph import build_graph, run_once
from agents.schemas import TriggerEvent
from agents.store.memory import MemoryStore

log = logging.getLogger(__name__)

UTC = timezone.utc
EXAMPLE_CONFIG = Path(agents.__file__).resolve().parent / "config.example.toml"   # package data
ENV_DB, ENV_CONFIG = "STUDIO_DATAFEED_DB", "STUDIO_AGENTS_CONFIG"
# The synthetic replay data: Mon 2025-06-09 00:00 .. Wed 2025-06-11 23:59 UTC.
SYNTH_START = datetime(2025, 6, 9, tzinfo=UTC)
SYNTH_END = datetime(2025, 6, 12, tzinfo=UTC)
EXAMPLE_T = datetime(2025, 6, 11, 14, 30, tzinfo=UTC)
# Parquet replay stores for Studio stay inside the development period.
PARQUET_END_LIMIT = datetime(2021, 1, 1, tzinfo=UTC)

Row = tuple[datetime, float, float, float, float, float]


def synthetic_rows(start: datetime = SYNTH_START, end: datetime = SYNTH_END, seed: int = 7) -> list[Row]:
    """A seeded 1-min random walk from 3300, (ts, open, high, low, close, volume),
    with about 3 % of minutes missing. Closed minutes are left in: datafeed's
    _valid_bar drops them on replay, as it drops the real data's."""
    if start.tzinfo is None or end.tzinfo is None or not start < end:
        raise ValueError(f"need tz-aware start < end, got {start!r}, {end!r}")
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


def _record(source: Any, path: Path) -> str:
    """Run a datafeed source through an in-memory bus into a new SQLite store."""
    from datafeed.bus import InMemoryBus
    from datafeed.ingest.xauusd_bars import STREAM
    from datafeed.recorder.recorder import Recorder
    from datafeed.store import schema

    if path.exists():
        raise FileExistsError(f"{path} exists; a replay store is written once")
    bus = InMemoryBus()
    source.run(lambda m: bus.publish(STREAM, m))
    con = schema.connect(str(path))
    try:
        Recorder(bus, con, STREAM, block_ms=None, sleep=lambda s: None).run(stop_when_idle=True)
    finally:
        con.close()
    return str(path)


def make_replay_db(directory: Path, rows: Sequence[Row] | None = None) -> str:
    """rows -> parquet -> datafeed ReplaySource -> InMemoryBus -> Recorder ->
    `directory`/datafeed.sqlite. The bars are kind 'replay'. Needs pandas and
    pyarrow (the `replay` extra)."""
    import pandas as pd

    from datafeed.ingest.xauusd_bars import ReplaySource

    rows = list(rows if rows is not None else synthetic_rows())
    if not rows:
        raise ValueError("no rows to replay")
    directory = Path(directory)
    parquet = directory / "replay.parquet"
    pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"]).to_parquet(parquet)
    return _record(ReplaySource(str(parquet), coverage=(rows[0][0], rows[-1][0])), directory / "datafeed.sqlite")


def build_parquet_db(parquet: str | Path, start: datetime, end: datetime, out: Path) -> str:
    """A replay store from the research parquet, bars in [start, end). Refuses
    an end after 2021-01-01: Studio only looks at development-period data."""
    from datafeed.ingest.xauusd_bars import ReplaySource

    if start.tzinfo is None or end.tzinfo is None:
        raise ValueError("start and end must be tz-aware")
    if end > PARQUET_END_LIMIT:
        raise ValueError(f"end {end.isoformat()} is after {PARQUET_END_LIMIT.isoformat()}: Studio replays "
                         "development-period bars only")
    return _record(ReplaySource(str(parquet), start=start, end=end), Path(out))


def build_synthetic_db(out: Path) -> str:
    """The synthetic replay store, written to `out` (which must not exist),
    so a server can be pointed at a fixed file with $STUDIO_DATAFEED_DB. The
    build itself takes under a second on an idle disk; the Recorder commits
    per bar, so it slows down a lot while another process is writing."""
    out = Path(out)
    if out.exists():
        raise FileExistsError(f"{out} exists; a replay store is written once")
    with tempfile.TemporaryDirectory(prefix="agents-studio-") as d:
        built = Path(make_replay_db(Path(d)))
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(built, out)      # works across filesystems; the store is closed
    return str(out)


@functools.lru_cache(maxsize=1)
def _synthetic_db() -> str:
    """The synthetic store, built once per process in its own temp directory."""
    d = Path(tempfile.mkdtemp(prefix="agents-studio-"))
    path = make_replay_db(d)
    log.info("synthetic replay store at %s", path)
    return path


def resolve_db(env: dict[str, str] | None = None) -> str:
    """$STUDIO_DATAFEED_DB if set (it must exist), else the synthetic store."""
    env = dict(os.environ) if env is None else env
    path = env.get(ENV_DB)
    if path:
        if not Path(path).is_file():
            raise FileNotFoundError(f"${ENV_DB} = {path!r} is not a file")
        return path
    return _synthetic_db()


def load_config(env: dict[str, str] | None = None) -> AgentsConfig:
    """$STUDIO_AGENTS_CONFIG or the example config. Live mode is refused."""
    env = dict(os.environ) if env is None else env
    cfg = AgentsConfig.from_toml(env.get(ENV_CONFIG) or EXAMPLE_CONFIG)
    if cfg.snapshot.mode != "replay":
        raise ValueError(f"Studio runs replay only; the config has snapshot.mode = {cfg.snapshot.mode!r}")
    return cfg


_GRAPH_LOCK = threading.Lock()
_GRAPH: Any = None


def studio_graph() -> Any:
    """The compiled graph, built once per process (config, MemoryStore,
    datafeed store) and reused by every run. Thread-safe."""
    global _GRAPH
    with _GRAPH_LOCK:
        if _GRAPH is None:
            _GRAPH = build_graph(load_config(), MemoryStore(), db=resolve_db())
        return _GRAPH


async def make_graph(config: Any = None) -> Any:
    """Graph factory for `langgraph dev` (langgraph.json). The server calls it
    on its event loop for every run, where blocking I/O is an error
    (blockbuster), so the first build runs in a worker thread. The
    RunnableConfig argument is accepted and ignored: everything comes from the
    environment, so one server process always serves the same graph."""
    return await asyncio.to_thread(studio_graph)


def example_input(t: datetime = EXAMPLE_T, trigger_id: str = "studio-1") -> dict:
    """A run input to paste into Studio: {"event": TriggerEvent as JSON}."""
    return {"event": TriggerEvent(source="studio", t=t, trigger_id=trigger_id).model_dump(mode="json")}


def summarise(state: Mapping[str, Any]) -> dict:
    """The parts of a final run state worth printing: status, skip reason,
    snapshot hash and size, votes and decisions (as JSON)."""
    snap = state.get("snap")
    return {
        "status": state.get("status"),
        "skip_reason": state.get("skip_reason"),
        "as_of": state["as_of"].isoformat() if state.get("as_of") else None,
        "snapshot_hash": snap.hash if snap is not None else None,
        "bars": {tf: len(b) for tf, b in snap.bars.items()} if snap is not None else None,
        "votes": {k: v.model_dump(mode="json") for k, v in (state.get("votes") or {}).items()},
        "decisions": {k: d.model_dump(mode="json") for k, d in (state.get("final_decisions") or {}).items()},
    }


def _utc_arg(s: str) -> datetime:
    t = datetime.fromisoformat(s.replace("Z", "+00:00"))
    if t.tzinfo is None:
        raise argparse.ArgumentTypeError(f"{s!r} has no timezone; use e.g. 2025-06-11T14:30:00Z")
    return t.astimezone(UTC)


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="tools/langgraph_studio/studio.py", description=(__doc__ or "").split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="one graph run on the replay store; prints a JSON summary")
    r.add_argument("--t", type=_utc_arg, default=EXAMPLE_T, help="trigger time, UTC (default %(default)s)")
    r.add_argument("--db", help=f"replay datafeed store (default ${ENV_DB}, else synthetic)")
    b = sub.add_parser("build-db", help="write a replay store: the synthetic one, or research parquet bars before 2021")
    src = b.add_mutually_exclusive_group(required=True)
    src.add_argument("--synthetic", action="store_true", help="the synthetic 2025-06-09..11 walk")
    src.add_argument("--parquet", help="the research parquet; needs --start and --end")
    b.add_argument("--start", type=_utc_arg)
    b.add_argument("--end", type=_utc_arg, help="exclusive")
    b.add_argument("--out", required=True, type=Path, help="the SQLite file to create (must not exist)")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    try:
        if args.cmd == "build-db":
            if args.synthetic:
                print(build_synthetic_db(args.out))
            elif args.start is None or args.end is None:
                p.error("--parquet needs --start and --end")
            else:
                print(build_parquet_db(args.parquet, args.start, args.end, args.out))
            return 0
        env = dict(os.environ, **({ENV_DB: args.db} if args.db else {}))
        graph = build_graph(load_config(env), MemoryStore(), db=resolve_db(env))
        out = run_once(graph, TriggerEvent(source="studio", t=args.t, trigger_id="cli"))
        print(json.dumps(summarise(out), indent=2, sort_keys=True))
        return 0
    except Exception as e:      # the CLI's edge: report with context, exit non-zero
        log.error("%s failed: %s: %s", args.cmd, type(e).__name__, e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
