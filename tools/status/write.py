"""Write health.json from the watchdog's facts. Stdlib only; runs on the host.

    python -m tools.status.write --out ~/scanner-health.json [--thresholds PATH] key=value ...

Called by watchdog.sh at the end of every run. The write is atomic: a temp
file in the same directory, fsync, then os.replace, so a reader sees the old
file or the new one, never a partial one. Exit 0 when written, 1 when the
file couldn't be written (the watchdog logs it and carries on).
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
import time
from pathlib import Path

from tools.status import config
from tools.status.evaluate import all_red, evaluate
from tools.status.model import Health, to_json


def parse_facts(tokens: list[str]) -> tuple[dict[str, str], list[str]]:
    """`key=value` tokens -> (facts, problems). A token without '=' or with an
    empty key is reported, not fatal; the value may contain '=' and spaces.
    The last of repeated keys wins."""
    facts: dict[str, str] = {}
    problems = []
    for tok in tokens:
        key, sep, value = tok.partition("=")
        if not sep or not key.strip():
            problems.append(f"ignored malformed fact {tok!r}")
            continue
        facts[key.strip()] = value
    return facts, problems


def build(facts: dict[str, str], thresholds_path: str | None, now: float) -> Health:
    """Health from the facts, or all red when the thresholds can't be loaded."""
    try:
        t = config.load(thresholds_path)
    except config.ConfigError as e:
        return all_red(f"thresholds invalid: {e}", now)
    return evaluate(facts, t, now)


def write_atomic(path: str | Path, text: str) -> None:
    p = Path(path).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=f".{p.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o644)
        os.replace(tmp, p)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Write health.json from watchdog facts.")
    ap.add_argument("--out", required=True, help="health.json path")
    ap.add_argument("--thresholds", default=None, help="thresholds file (default tools/status/thresholds.toml)")
    ap.add_argument("facts", nargs="*", help="key=value facts from the watchdog")
    args = ap.parse_args(argv)
    facts, problems = parse_facts(args.facts)
    for p in problems:
        print(f"status write: {p}", file=sys.stderr)
    try:
        now = float(facts["now"]) if "now" in facts else time.time()
    except ValueError:
        print(f"status write: bad now={facts['now']!r}; using the clock", file=sys.stderr)
        now = time.time()
    health = build(facts, args.thresholds, now)
    try:
        write_atomic(args.out, to_json(health) + "\n")
    except OSError as e:
        print(f"status write: cannot write {args.out}: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
