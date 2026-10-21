"""mypy on backend/scanner may not get worse: run it and fail on any finding
(severity and message, counted; file and line ignored, so moving code is not
a change) that tools/mypy_scanner_baseline.txt doesn't hold.

    python tools/mypy_baseline.py            # check (CI)
    python tools/mypy_baseline.py --write    # record the current findings

The baseline was taken in CI's lint environment (mypy only, none of the
scanner's dependencies installed): its findings differ when ib_insync is
installed. Fewer findings than the baseline pass; record them with --write."""
import collections
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "tools" / "mypy_scanner_baseline.txt"
CMD = [sys.executable, "-m", "mypy", "--follow-imports=silent", "backend/scanner"]
LINE = re.compile(r"^[^:]+:\d+: (error|note): (.*)$")


def findings(text):
    return collections.Counter(f"{m.group(1)}: {m.group(2)}" for m in map(LINE.match, text.splitlines()) if m)


def main():
    out = subprocess.run(CMD, cwd=ROOT, capture_output=True, text=True).stdout
    now = findings(out)
    if "--write" in sys.argv:
        BASELINE.write_text("".join(f"{n} {f}\n" for f, n in sorted(now.items())))
        print(f"recorded {sum(now.values())} findings")
        return 0
    base = collections.Counter({f: int(n) for n, f in (l.split(" ", 1) for l in BASELINE.read_text().splitlines())})
    new = now - base
    for f, n in sorted(new.items()):
        print(f"new: {n}x {f}")
    print(f"mypy backend/scanner: {sum(now.values())} findings, baseline {sum(base.values())}, new {sum(new.values())}")
    return 1 if new else 0


if __name__ == "__main__":
    sys.exit(main())
