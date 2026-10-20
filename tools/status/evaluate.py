"""Watchdog facts -> per-component health. Pure functions, stdlib only.

The watchdog passes what it measured as `key=value` facts (all strings). A fact
the watchdog couldn't measure is missing; a value that doesn't parse is bad.
Either makes that component RED ("no data"), never green.

Facts (see watchdog.sh, write_health):
  common     now (epoch s), market_open 0|1, bar_source ibkr|stream,
             stream_expected 0|1 (the stream profile's containers should exist)
  gateway    gateway_ok 0|1, gateway_fails, gateway_relogin_limit 0|1
  scanner    scanner_state (docker State.Status or "missing"), scanner_health
             (healthy|unhealthy|starting|none|missing), scanner_unhealthy_checks,
             scanner_restarts_5min (-1 unknown), scanner_last_bar_age_min (-1 none),
             scanner_last_bar_ts, scanner_last_alert_ts
  journal    journal_ok 0|1, journal_error, journal_take, journal_skip,
             journal_missed, journal_undecided, journal_undecided_oldest_min (-1 none)
  stream     feed_state, feed_restarts_5min, feed_last_bar_age_min (-1 none),
             feed_last_bar_ts, redis_state, redis_fails, recorder_state,
             recorder_lag_min (-1 no live bar), rejected_total,
             rejected_last_growth_ts (epoch s, 0 never)
  host       backup_configured 0|1, backup_age_h (-1 never), disk_pct,
             mem_avail_pct, swap_mb
  agents     agents_deployed 0|1, agents_last_run_age_h (-1 none), agents_last_run_ts
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable, Mapping

from tools.status.config import Thresholds
from tools.status.model import COMPONENTS, ComponentHealth, Health, Status, worst

class FactError(ValueError):
    """A fact is missing or malformed."""


class Facts:
    """Typed access to the watchdog's key=value facts."""

    def __init__(self, raw: Mapping[str, str]):
        self.raw = dict(raw)

    def text(self, key: str, default: str | None = None) -> str:
        v = self.raw.get(key, default)
        if v is None:
            raise FactError(f"{key} not reported")
        return v

    def num(self, key: str) -> int:
        v = self.text(key)
        try:
            return int(v)
        except ValueError:
            raise FactError(f"bad value {key}={v!r}") from None

    def flag(self, key: str) -> bool:
        v = self.num(key)
        if v not in (0, 1):
            raise FactError(f"bad value {key}={v!r} (0 or 1)")
        return v == 1


def iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def level(value: int, yellow: int, red: int) -> Status:
    """Higher is worse; thresholds inclusive."""
    return "red" if value >= red else "yellow" if value >= yellow else "green"


def _closed(f: Facts) -> str:
    return "" if f.raw.get("market_open", "1") == "1" else " (market closed)"


def _container(f: Facts, prefix: str) -> tuple[Status, str] | None:
    """Red unless the container is running; None when it is."""
    state = f.text(f"{prefix}_state")
    if state == "missing":
        return "red", f"{prefix} container missing"
    if state != "running":
        return "red", f"{prefix} container {state}"
    return None


# ── components ─────────────────────────────────────────────────────────────
def gateway(f: Facts, t: Thresholds) -> tuple[Status, str]:
    if f.flag("gateway_ok"):
        return "green", "API handshake ok"
    fails = max(f.num("gateway_fails"), 1)
    s = level(fails, t.gateway.yellow_fails, t.gateway.red_fails)
    limit = " - automatic re-login limit reached" if f.raw.get("gateway_relogin_limit") == "1" else ""
    return s, f"API unreachable, {fails} check(s) in a row (~{fails * 5} min){limit}"


def feed(f: Facts, t: Thresholds) -> tuple[Status, str]:
    if f.flag("stream_expected"):
        bad = _container(f, "feed")
        if bad:
            return bad
        if f.raw.get("feed_probe_error"):
            return "red", f"datafeed stream not readable: {f.raw['feed_probe_error']}"
        age, what, ts = f.num("feed_last_bar_age_min"), "datafeed stream", f.raw.get("feed_last_bar_ts", "")
        restarts = f.num("feed_restarts_5min")
        extra = f", {restarts} restart(s) in 5 min" if restarts > 0 else ""
    else:
        age, what, ts = f.num("scanner_last_bar_age_min"), "ibkr feed in the scanner", \
            f.raw.get("scanner_last_bar_ts", "")
        extra = ""
    if age < 0:
        return "yellow", f"{what}: no live bar yet{extra}{_closed(f)}"
    s = level(age, t.feed.yellow_age_min, t.feed.red_age_min)
    return s, f"{what}: last bar {ts} ({age} market-open min ago){extra}{_closed(f)}"


def redis(f: Facts, t: Thresholds) -> tuple[Status, str] | None:
    if not f.flag("stream_expected"):
        return None
    bad = _container(f, "redis")
    if bad:
        return bad
    fails = f.num("redis_fails")
    if fails == 0:
        return "green", "PING ok"
    return level(fails, t.redis.yellow_fails, t.redis.red_fails), f"PING failed {fails} time(s) in a row"


def recorder(f: Facts, t: Thresholds) -> tuple[Status, str] | None:
    if not f.flag("stream_expected"):
        return None
    bad = _container(f, "recorder")
    if bad:
        return bad
    lag, rej, grew = f.num("recorder_lag_min"), f.num("rejected_total"), f.num("rejected_last_growth_ts")
    now = f.num("now")
    if lag < 0:
        s: Status = "yellow"
        text = "no live bar stored yet"
    else:
        s, text = level(lag, t.recorder.yellow_lag_min, t.recorder.red_lag_min), f"lag {lag} market-open min"
    if rej < 0:
        return "red", f"{text}; rejected_messages not readable"
    text += f"; rejected_messages {rej}"
    if grew > 0 and now - grew < t.recorder.rejected_window_h * 3600:
        s = worst([s, "yellow"])
        text += f" (grew {max(0, (now - grew) // 60)} min ago)"
    return s, text + _closed(f)


def scanner(f: Facts, t: Thresholds) -> tuple[Status, str]:
    bad = _container(f, "scanner")
    if bad:
        return bad
    found: list[tuple[Status, str]] = []
    health = f.text("scanner_health")
    if health == "unhealthy":
        n = f.num("scanner_unhealthy_checks")
        found.append(("red" if n >= t.scanner.red_unhealthy_checks else "yellow",
                      f"heartbeat stale {n} check(s) in a row (main loop hung?)"))
    elif health == "starting":
        found.append(("yellow", "starting"))
    elif health not in ("healthy", "none"):
        found.append(("red", f"health {health!r}"))
    restarts = f.num("scanner_restarts_5min")
    if restarts > 0:
        found.append((level(restarts, t.scanner.yellow_restarts_5min, t.scanner.red_restarts_5min),
                      f"restarted {restarts} time(s) in 5 min"))
    age = f.num("scanner_last_bar_age_min")
    last_alert = f.raw.get("scanner_last_alert_ts") or "none"
    if age < 0:
        found.append(("yellow", f"no live bar yet; last alert {last_alert}"))
    else:
        found.append((level(age, t.scanner.yellow_bar_age_min, t.scanner.red_bar_age_min),
                      f"last bar {f.raw.get('scanner_last_bar_ts', '?')} ({age} market-open min ago), "
                      f"last alert {last_alert}"))
    return worst(s for s, _ in found), "; ".join(r for _, r in found) + _closed(f)


def journal(f: Facts, t: Thresholds) -> tuple[Status, str]:
    if not f.flag("journal_ok"):
        return "red", f"journal unreadable: {f.raw.get('journal_error') or 'no reason given'}"
    counts = (f"today TAKE {f.num('journal_take')}, SKIP {f.num('journal_skip')}, "
              f"MISSED {f.num('journal_missed')}, unanswered {f.num('journal_undecided')}")
    oldest = f.num("journal_undecided_oldest_min")
    if oldest >= t.journal.yellow_undecided_min:
        return "yellow", f"{counts}; oldest unanswered alert {oldest} min old"
    return "green", counts


def backups(f: Facts, t: Thresholds) -> tuple[Status, str]:
    if not f.flag("backup_configured"):
        return "yellow", "BACKUP_S3_URI not set: no backups are made"
    age = f.num("backup_age_h")
    if age < 0:
        return "red", "no backup has ever succeeded"
    return level(age, t.backups.yellow_age_h, t.backups.red_age_h), f"last success {age} h ago"


def disk(f: Facts, t: Thresholds) -> tuple[Status, str]:
    pct = f.num("disk_pct")
    if not 0 <= pct <= 100:
        raise FactError(f"bad value disk_pct={pct}")
    return level(pct, t.disk.yellow_pct, t.disk.red_pct), f"{pct}% used"


def memory(f: Facts, t: Thresholds) -> tuple[Status, str]:
    avail = f.num("mem_avail_pct")
    if not 0 <= avail <= 100:
        raise FactError(f"memory not measured (mem_avail_pct={avail})")
    s: Status = "red" if avail < t.memory.red_avail_pct else \
        "yellow" if avail < t.memory.yellow_avail_pct else "green"
    swap = f.raw.get("swap_mb")
    return s, f"{avail}% available" + (f", swap {swap} MB used" if swap not in (None, "-1") else "")


def agents(f: Facts, t: Thresholds) -> tuple[Status, str] | None:
    if not f.flag("agents_deployed"):
        return None
    age = f.num("agents_last_run_age_h")
    if age < 0:
        return "yellow", "deployed, no run yet"
    return ("yellow" if age >= t.agents.yellow_last_run_h else "green",
            f"last run {f.raw.get('agents_last_run_ts', '?')} ({age} h ago)")


Evaluator = Callable[[Facts, Thresholds], "tuple[Status, str] | None"]
EVALUATORS: dict[str, Evaluator] = {
    "gateway": gateway, "feed": feed, "redis": redis, "recorder": recorder, "scanner": scanner,
    "journal": journal, "backups": backups, "disk": disk, "memory": memory, "agents": agents}
NOT_DEPLOYED = {"redis": "not deployed (no stream profile)", "recorder": "not deployed (no stream profile)",
                "agents": "not deployed"}


def evaluate(raw: Mapping[str, str], t: Thresholds, now: float) -> Health:
    """Every component, in COMPONENTS order. A missing or malformed fact makes
    that component red with the reason; nothing raises."""
    f, ts = Facts(raw), iso(now)
    f.raw.setdefault("now", str(int(now)))
    out = []
    for name in COMPONENTS:
        try:
            r = EVALUATORS[name](f, t)
        except FactError as e:
            out.append(ComponentHealth(name, "red", f"no data: {e}", ts))
            continue
        if r is None:
            out.append(ComponentHealth(name, "green", NOT_DEPLOYED[name], ts, deployed=False))
        else:
            out.append(ComponentHealth(name, r[0], r[1], ts))
    return Health(ts, tuple(out), t.sha256)


def all_red(reason: str, now: float, thresholds_sha256: str = "") -> Health:
    """Every component red with one reason (e.g. invalid thresholds)."""
    ts = iso(now)
    return Health(ts, tuple(ComponentHealth(n, "red", reason, ts) for n in COMPONENTS), thresholds_sha256)
