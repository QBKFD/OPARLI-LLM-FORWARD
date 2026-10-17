"""python -m datafeed.ingest  — run one bar source and publish to the bus.

    DATAFEED_SOURCE=replay (default) | ibkr
    REDIS_URL              Redis for the bus (required unless --stdout)
    INSTRUMENT, INSTRUMENT_*  the instrument (datafeed.instrument; default
                           XAUUSD). The replay source is XAUUSD only (its parquet).

    --stdout   print messages as JSON lines instead of publishing (no Redis)

The ibkr source reads IB_HOST / IB_PORT / IB_CLIENT_ID. In development and
tests point it only at a local paper gateway. In production with the scanner in
stream mode it is the ONLY client of the production gateway (the scanner never
connects); see datafeed/README.md. Exit status is non-zero on any fatal error
(crash-only: the container restarts the process).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys

from datafeed.ingest.base import validate_message


def build_source(env=os.environ):
    from datafeed.instrument import InstrumentError, from_env
    kind = env.get("DATAFEED_SOURCE", "replay")
    try:
        instrument = from_env(env)
    except InstrumentError as e:
        raise SystemExit(f"instrument: {e}") from None
    if kind == "replay":
        if instrument.name != "XAUUSD":
            raise SystemExit(f"the replay source has XAUUSD bars only, not {instrument.name}")
        from datafeed.ingest.xauusd_bars import ReplaySource
        return ReplaySource.from_env(env)
    if kind == "ibkr":
        from datafeed.ingest.ibkr import IbkrConfig, IbkrConnection
        from datafeed.ingest.xauusd_bars import IbkrXauusdSource
        logging.getLogger("datafeed.ingest").info("instrument %s", instrument)
        return IbkrXauusdSource(IbkrConnection(IbkrConfig.from_env(env), instrument))
    raise SystemExit(f"unknown DATAFEED_SOURCE={kind!r} (replay | ibkr)")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m datafeed.ingest")
    ap.add_argument("--stdout", action="store_true", help="print messages, don't publish")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    src = build_source()
    if args.stdout:
        def publish(msg):
            validate_message(msg)
            print(json.dumps(msg), flush=True)
    else:
        from datafeed.bus import RedisStreamBus
        bus = RedisStreamBus()

        def publish(msg):
            validate_message(msg)
            bus.publish(src.stream, msg)
    src.run(publish)
    logging.getLogger("datafeed.ingest").info("source finished: %s", json.dumps(src.health()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
