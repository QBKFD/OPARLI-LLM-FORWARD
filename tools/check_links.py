"""Check every relative link in the repo's Markdown files.

  python tools/check_links.py [ROOT]

A link is `[text](target)` or `[text]: target`. External links (http, https,
mailto) and same-file anchors are not checked; for any other target, the file
or directory it names must exist (an `#anchor` after it is ignored). Fenced
code blocks and inline code are skipped. `legacy/` is a frozen archive and is
not checked. Prints each broken link and exits 1 if there is any.
"""
import re
import subprocess
import sys
from pathlib import Path

SKIP_DIRS = ("legacy/",)
INLINE = re.compile(r"!?\[[^\]]*\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")
REFDEF = re.compile(r"^\s{0,3}\[[^\]]+\]:\s*<?(\S+?)>?(?:\s+\"[^\"]*\")?\s*$")
CODE_SPAN = re.compile(r"(`+).+?\1")


def markdown_files(root: Path) -> list[Path]:
    out = subprocess.run(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard", "*.md"], cwd=root, capture_output=True, check=True)
    return [root / p for p in out.stdout.decode().split("\0") if p and not p.startswith(SKIP_DIRS)]


def links(text: str):
    fence = None
    for n, line in enumerate(text.splitlines(), 1):
        m = re.match(r"\s*(`{3,}|~{3,})", line)
        if m:
            if fence is None:
                fence = m.group(1)
            elif m.group(1).startswith(fence):
                fence = None
            continue
        if fence:
            continue
        line = CODE_SPAN.sub("", line)
        for t in INLINE.findall(line):
            yield n, t
        m = REFDEF.match(line)
        if m:
            yield n, m.group(1)


def broken(root: Path) -> list[str]:
    bad = []
    for f in markdown_files(root):
        for n, target in links(f.read_text(encoding="utf-8")):
            if re.match(r"[a-z][a-z0-9+.-]*:", target, re.I) or target.startswith("#"):
                continue
            path = target.split("#", 1)[0]
            dest = (root / path.lstrip("/")) if path.startswith("/") else (f.parent / path)
            if not dest.exists():
                bad.append(f"{f.relative_to(root)}:{n}: {target}")
    return bad


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
    bad = broken(root)
    for b in bad:
        print(b)
    print(f"{len(markdown_files(root))} files, {len(bad)} broken links")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
