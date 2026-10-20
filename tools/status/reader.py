"""Read health.json for display. Never raises: a missing, unreadable, invalid or
stale file is shown as every component RED, with the reason in Health.note."""

from __future__ import annotations

import os
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from tools.status.evaluate import iso
from tools.status.model import COMPONENTS, ComponentHealth, Health, HealthFileError, from_json

DEFAULT_FILE = Path(os.environ.get("HEALTH_JSON", "~/scanner-health.json")).expanduser()
MAX_BYTES = 1_000_000            # a real file is ~3 kB; anything this big is not ours
FUTURE_SLACK_S = 120             # a writer clock this far ahead is reported, not trusted


def parse_ts(ts: str) -> float:
    """ISO-8601 (with Z or an offset) -> epoch seconds. Raises ValueError."""
    t = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    if t.tzinfo is None:
        raise ValueError(f"timestamp without a timezone: {ts!r}")
    return t.timestamp()


def age_text(seconds: float) -> str:
    s = max(0, int(seconds))
    if s < 120:
        return f"{s} s"
    if s < 7200:
        return f"{s // 60} min"
    if s < 172800:
        return f"{s // 3600} h"
    return f"{s // 86400} d"


def _all_red(reason: str, now: float) -> Health:
    ts = iso(now)
    return Health(ts, tuple(ComponentHealth(n, "red", reason, ts) for n in COMPONENTS), note=reason)


def load(path: str | Path, now: float, stale_after_s: int) -> Health:
    p = Path(path).expanduser()
    try:
        if p.stat().st_size > MAX_BYTES:
            return _all_red(f"health.json at {p} is too large to be a status file", now)
        text = p.read_text(encoding="utf-8")
    except FileNotFoundError:
        return _all_red(f"no health.json at {p} (is the watchdog cron installed?)", now)
    except (OSError, UnicodeDecodeError) as e:
        return _all_red(f"health.json unreadable: {e}", now)
    try:
        h = from_json(text)
        written = parse_ts(h.generated_ts)
    except (HealthFileError, ValueError) as e:       # json.JSONDecodeError is a ValueError
        return _all_red(f"health.json invalid: {e}", now)
    age = now - written
    if age > stale_after_s:
        reason = f"STALE: health.json written {age_text(age)} ago (watchdog not running?)"
        # nothing is known now, not even what is deployed: every row red
        comps = tuple(replace(c, status="red", deployed=True,
                              reason=f"stale - last known: {c.status if c.deployed else 'off'}, {c.reason}")
                      for c in h.components)
        return replace(h, components=comps, note=reason)
    if age < -FUTURE_SLACK_S:
        return replace(h, note=f"health.json is dated {age_text(-age)} in the future (clock skew?)")
    return h
