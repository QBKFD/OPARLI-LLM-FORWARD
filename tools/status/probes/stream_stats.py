"""Feed facts for health.json: the newest live bar in the datafeed stream.
Runs INSIDE the recorder container (it has REDIS_URL and the redis client),
fed on stdin by watchdog.sh:

    docker compose exec -T recorder python - < tools/status/probes/stream_stats.py

Prints `feed_last_bar_ts=...` and `feed_last_bar_age_min=N` (market-open
minutes since that bar ended; -1 when the newest SCAN_ENTRIES entries hold no
live bar). On any failure it prints `feed_probe_error=...` and no age, so the
board shows the feed red ("no data") instead of the watchdog failing.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable, cast

Entry = tuple[Any, dict[str, Any]]

SCAN_ENTRIES = 200               # newest entries looked at; history bars are skipped
MAX_MINUTES = 100_000


def open_minutes(start: datetime, end: datetime, market_open: Callable[[datetime], bool]) -> int:
    n, t = 0, start
    while t + timedelta(minutes=1) <= end and n < MAX_MINUTES:
        n += bool(market_open(t))
        t += timedelta(minutes=1)
    return n


def newest_live_bar(entries: Iterable[Entry]) -> datetime | None:
    """entries newest first, as XREVRANGE returns them: (id, {"json": "..."}).
    The first entry that is a live (history false) bar; undecodable ones are skipped."""
    for _id, fields in entries:
        raw = fields.get("json") if isinstance(fields, dict) else None
        try:
            msg = json.loads(raw) if raw is not None else None
        except (TypeError, ValueError):
            continue
        if isinstance(msg, dict) and msg.get("history") is False and isinstance(msg.get("bar_ts"), str):
            t = datetime.fromisoformat(msg["bar_ts"])
            return t if t.tzinfo else t.replace(tzinfo=timezone.utc)
    return None


def stats(entries: Iterable[Entry], now: datetime,
          market_open: Callable[[datetime], bool]) -> dict[str, str]:
    t = newest_live_bar(entries)
    if t is None:
        return {"feed_last_bar_age_min": "-1"}
    return {"feed_last_bar_ts": t.strftime("%Y-%m-%dT%H:%MZ"),
            "feed_last_bar_age_min": str(open_minutes(t + timedelta(minutes=1), now, market_open))}


def main() -> None:
    try:
        import redis
        from datafeed.ingest.xauusd_bars import STREAM, _market_open
        r = redis.Redis.from_url(os.environ["REDIS_URL"], socket_timeout=10, decode_responses=True)
        entries = cast(list[Entry], r.xrevrange(STREAM, "+", "-", count=SCAN_ENTRIES))   # sync client
        facts = stats(entries, datetime.now(timezone.utc), _market_open)
    except Exception as e:                        # noqa: BLE001 - reported as a fact, shown red
        facts = {"feed_probe_error": f"{type(e).__name__}: {e}".replace("\n", " ")[:200]}
    print("\n".join(f"{k}={v}" for k, v in facts.items()))


if __name__ == "__main__":
    main()
