"""python -m backend.datafeed.recorder  — consume bars.xauusd.1m into the SQLite store.

    REDIS_URL                   bus
    DATAFEED_DB                 store path (created and migrated if missing)
    DATAFEED_RECORDER_CONSUMER  consumer name (default recorder-1); keep it
                                stable across restarts so unacked entries are
                                redelivered to the restarted process
Logs a health line every 60 s.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time

from backend.datafeed.bus import RedisStreamBus
from backend.datafeed.ingest.xauusd_bars import STREAM
from backend.datafeed.recorder.recorder import Recorder
from backend.datafeed.store import schema

HEALTH_EVERY_S = 60


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    path = os.environ.get("DATAFEED_DB")
    if not path:
        raise SystemExit("set DATAFEED_DB")
    rec = Recorder(RedisStreamBus(), schema.connect(path), STREAM,
                   os.environ.get("DATAFEED_RECORDER_CONSUMER", "recorder-1"))
    log = logging.getLogger("backend.datafeed.recorder")
    last = 0.0
    while True:
        rec.step()
        if time.monotonic() - last >= HEALTH_EVERY_S:
            log.info("health %s", json.dumps(rec.health()))
            last = time.monotonic()


if __name__ == "__main__":
    sys.exit(main())
