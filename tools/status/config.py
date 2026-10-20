"""Thresholds: tools/status/thresholds.toml, loaded and validated. Stdlib only.

    python -m tools.status.config --watchdog-env [--thresholds PATH]

prints the watchdog's alert thresholds as KEY=VALUE lines (watchdog.sh reads
them, so its alerts and the board's red use the same numbers).
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import tomllib
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any

DEFAULT_PATH = Path(__file__).with_name("thresholds.toml")


class ConfigError(ValueError):
    """thresholds.toml is missing, unreadable or invalid."""


@dataclass(frozen=True)
class HealthCfg:
    stale_after_s: int


@dataclass(frozen=True)
class GatewayCfg:
    yellow_fails: int
    red_fails: int


@dataclass(frozen=True)
class FeedCfg:
    yellow_age_min: int
    red_age_min: int


@dataclass(frozen=True)
class RedisCfg:
    yellow_fails: int
    red_fails: int


@dataclass(frozen=True)
class RecorderCfg:
    yellow_lag_min: int
    red_lag_min: int
    rejected_window_h: int


@dataclass(frozen=True)
class ScannerCfg:
    yellow_restarts_5min: int
    red_restarts_5min: int
    red_unhealthy_checks: int
    yellow_bar_age_min: int
    red_bar_age_min: int


@dataclass(frozen=True)
class JournalCfg:
    yellow_undecided_min: int


@dataclass(frozen=True)
class BackupsCfg:
    yellow_age_h: int
    red_age_h: int


@dataclass(frozen=True)
class DiskCfg:
    yellow_pct: int
    red_pct: int


@dataclass(frozen=True)
class MemoryCfg:
    yellow_avail_pct: int
    red_avail_pct: int


@dataclass(frozen=True)
class AgentsCfg:
    yellow_last_run_h: int


@dataclass(frozen=True)
class Thresholds:
    health: HealthCfg
    gateway: GatewayCfg
    feed: FeedCfg
    redis: RedisCfg
    recorder: RecorderCfg
    scanner: ScannerCfg
    journal: JournalCfg
    backups: BackupsCfg
    disk: DiskCfg
    memory: MemoryCfg
    agents: AgentsCfg
    sha256: str = ""


# "higher is worse" pairs that must satisfy yellow <= red
_ORDERED = [("gateway", "yellow_fails", "red_fails"), ("feed", "yellow_age_min", "red_age_min"),
            ("redis", "yellow_fails", "red_fails"), ("recorder", "yellow_lag_min", "red_lag_min"),
            ("scanner", "yellow_restarts_5min", "red_restarts_5min"),
            ("scanner", "yellow_bar_age_min", "red_bar_age_min"),
            ("backups", "yellow_age_h", "red_age_h"), ("disk", "yellow_pct", "red_pct")]


def _section(cls: type, name: str, raw: Any) -> Any:
    if not isinstance(raw, dict):
        raise ConfigError(f"[{name}] is missing")
    want = {f.name for f in fields(cls)}
    unknown, missing = set(raw) - want, want - set(raw)
    if unknown:
        raise ConfigError(f"[{name}] unknown keys: {sorted(unknown)}")
    if missing:
        raise ConfigError(f"[{name}] missing keys: {sorted(missing)}")
    for k, v in raw.items():
        if isinstance(v, bool) or not isinstance(v, int) or v < 0:
            raise ConfigError(f"[{name}] {k} must be an integer >= 0, got {v!r}")
    return cls(**raw)


def parse(data: dict[str, Any], sha256: str = "") -> Thresholds:
    """A parsed TOML document -> Thresholds. Raises ConfigError."""
    sections = {f.name: f.type for f in fields(Thresholds) if f.name != "sha256"}
    extra = set(data) - set(sections)
    if extra:
        raise ConfigError(f"unknown sections: {sorted(extra)}")
    types = {"health": HealthCfg, "gateway": GatewayCfg, "feed": FeedCfg, "redis": RedisCfg,
             "recorder": RecorderCfg, "scanner": ScannerCfg, "journal": JournalCfg,
             "backups": BackupsCfg, "disk": DiskCfg, "memory": MemoryCfg, "agents": AgentsCfg}
    t = Thresholds(**{n: _section(types[n], n, data.get(n)) for n in sections}, sha256=sha256)
    for sec, lo, hi in _ORDERED:
        s = getattr(t, sec)
        if getattr(s, lo) > getattr(s, hi):
            raise ConfigError(f"[{sec}] {lo} must be <= {hi}")
    if t.memory.yellow_avail_pct < t.memory.red_avail_pct:
        raise ConfigError("[memory] yellow_avail_pct must be >= red_avail_pct")
    for sec, key in (("disk", "yellow_pct"), ("disk", "red_pct"),
                     ("memory", "yellow_avail_pct"), ("memory", "red_avail_pct")):
        if getattr(getattr(t, sec), key) > 100:
            raise ConfigError(f"[{sec}] {key} is a percentage (0-100)")
    if t.health.stale_after_s == 0:
        raise ConfigError("[health] stale_after_s must be > 0")
    return t


def load(path: str | Path | None = None) -> Thresholds:
    """Read and validate the thresholds file (default: next to this module)."""
    p = Path(path) if path is not None else DEFAULT_PATH
    try:
        raw = p.read_bytes()
    except OSError as e:
        raise ConfigError(f"cannot read {p}: {e}") from e
    try:
        data = tomllib.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as e:
        raise ConfigError(f"{p}: {e}") from e
    return parse(data, hashlib.sha256(raw).hexdigest())


def watchdog_env(t: Thresholds) -> list[str]:
    """The watchdog's alert thresholds: the red values."""
    return [f"DISK_ALERT_PCT={t.disk.red_pct}", f"MEM_AVAIL_MIN_PCT={t.memory.red_avail_pct}",
            f"BACKUP_MAX_AGE_H={t.backups.red_age_h}", f"RECORDER_LAG_MAX_MIN={t.recorder.red_lag_min}"]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Status board thresholds.")
    ap.add_argument("--thresholds", default=None, help=f"thresholds file (default {DEFAULT_PATH})")
    ap.add_argument("--watchdog-env", action="store_true", help="print the watchdog's alert thresholds")
    args = ap.parse_args(argv)
    try:
        t = load(args.thresholds)
    except ConfigError as e:
        print(f"thresholds: {e}", file=sys.stderr)
        return 1
    if args.watchdog_env:
        print("\n".join(watchdog_env(t)))
    else:
        print(f"{args.thresholds or DEFAULT_PATH}: ok (sha256 {t.sha256})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
