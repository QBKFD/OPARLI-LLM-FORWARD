"""python -m backend.datafeed.ingest  — run one bar source and publish to the bus.

    DATAFEED_SOURCE=replay (default) | ibkr
    REDIS_URL              Redis for the bus (required unless --stdout)

    --stdout   print messages as JSON lines instead of publishing (no Redis)

The ibkr source reads IB_HOST / IB_PORT / IB_CLIENT_ID. In development and
tests point it only at a local paper gateway. In production with the scanner in
stream mode it is the ONLY client of the production gateway (the scanner never
connects); see datafeed/README.md. Exit status is non-zero on any fatal error
(crash-only: the container restarts the process).

The process ends with os._exit, after flushing logging, stdout and stderr, so
C++ static destructors don't run. Once the replay source has read a parquet,
pyarrow's thread pool is torn down by one of them, and that can deadlock
(about 1 run in 1,000 under load: the process printed everything, then hung
in exit() forever). See docs/decisions/fix-ingest-exit-hang.md (F4).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import traceback
from typing import NoReturn

from backend.datafeed.ingest.base import validate_message


def build_source(env=os.environ):
    kind = env.get("DATAFEED_SOURCE", "replay")
    if kind == "replay":
        from backend.datafeed.ingest.xauusd_bars import ReplaySource
        return ReplaySource.from_env(env)
    if kind == "ibkr":
        from backend.datafeed.ingest.ibkr import IbkrConfig, IbkrConnection
        from backend.datafeed.ingest.xauusd_bars import IbkrXauusdSource
        return IbkrXauusdSource(IbkrConnection(IbkrConfig.from_env(env)))
    raise SystemExit(f"unknown DATAFEED_SOURCE={kind!r} (replay | ibkr)")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m backend.datafeed.ingest")
    ap.add_argument("--stdout", action="store_true", help="print messages, don't publish")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    src = build_source()
    if args.stdout:
        def publish(msg):
            validate_message(msg)
            print(json.dumps(msg), flush=True)
    else:
        from backend.datafeed.bus import RedisStreamBus
        bus = RedisStreamBus()

        def publish(msg):
            validate_message(msg)
            bus.publish(src.stream, msg)
    src.run(publish)
    logging.getLogger("backend.datafeed.ingest").info("source finished: %s", json.dumps(src.health()))
    return 0


def run(argv=None) -> NoReturn:
    """main(), then os._exit with the status Python would have exited with:
    main's return value, a SystemExit's code (a message is printed and gives
    1), 1 with the traceback for any other exception, 130 for Ctrl-C."""
    try:
        code = main(argv)
    except SystemExit as e:
        if e.code is None or isinstance(e.code, int):
            code = e.code or 0
        else:
            print(e.code, file=sys.stderr)
            code = 1
    except KeyboardInterrupt:
        traceback.print_exc()
        code = 130
    except BaseException:                    # noqa: BLE001 - reported, then the process ends with 1
        traceback.print_exc()
        code = 1
    logging.shutdown()
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.flush()
        except (OSError, ValueError):        # a closed pipe: nothing left to flush to
            pass
    os._exit(code)


if __name__ == "__main__":
    run()
