"""python -m tools.status [--once] [--file PATH] [--no-color] [--interval S]

Without --once: the Textual board (needs textual, the `status` extra).
With --once: one line per component, then exit 0 (all green), 1 (yellow) or
2 (red); stdlib only, for a quick `ssh host 'cd oparli && python3 -m tools.status --once'`.
"""

from __future__ import annotations

import argparse
import os
import sys
import time

from tools.status import config, reader, render

FALLBACK_STALE_S = 900           # used when thresholds.toml itself can't be read


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m tools.status", description="OPARLI status board.")
    ap.add_argument("--file", default=str(reader.DEFAULT_FILE),
                    help="health.json (default $HEALTH_JSON or ~/scanner-health.json)")
    ap.add_argument("--once", action="store_true", help="print one line per component and exit")
    ap.add_argument("--no-color", action="store_true", help="never use colour (NO_COLOR works too)")
    ap.add_argument("--interval", type=float, default=5.0, help="TUI refresh seconds (default 5)")
    ap.add_argument("--thresholds", default=None, help="thresholds file (for stale_after_s)")
    args = ap.parse_args(argv)
    note = None
    try:
        stale = config.load(args.thresholds).health.stale_after_s
    except config.ConfigError as e:
        stale, note = FALLBACK_STALE_S, f"thresholds unreadable ({e}); stale after {FALLBACK_STALE_S} s"
    if args.once:
        h = reader.load(args.file, time.time(), stale)
        color = render.use_color(sys.stdout, os.environ, False if args.no_color else None)
        if note:
            print(f"!! {note}")
        print("\n".join(render.lines(h, time.time(), color)))
        return render.exit_code(h)
    if args.interval <= 0:
        ap.error("--interval must be > 0")
    try:
        from tools.status.tui import StatusApp
    except ImportError as e:
        print(f"the board needs textual ({e}); install the status extra "
              f"(pip install textual==8.2.8) or use --once", file=sys.stderr)
        return 3
    if args.no_color:
        os.environ["NO_COLOR"] = "1"
    StatusApp(args.file, stale, args.interval).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
