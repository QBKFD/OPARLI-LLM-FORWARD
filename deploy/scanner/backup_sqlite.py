#!/usr/bin/env python3
"""Snapshot, restore and check SQLite files: the scanner journal and the
datafeed store. Stdlib only; runs inside the scanner image (backup.sh and
restore.sh call it through docker compose).

    python backup_sqlite.py snapshot SRC DST [--expect-table T]
        Consistent copy through SQLite's online backup API. That's safe while
        another process writes SRC, also in WAL mode. The copy is switched to
        journal_mode=DELETE, so it is one self-contained file. It must pass
        PRAGMA integrity_check. Refuses to overwrite DST.
    python backup_sqlite.py restore SRC DST [--expect-table T]
        SRC must pass integrity_check (and hold table T). The current DST, if
        any, is kept as DST.pre-restore-<UTC ts> (a snapshot, so its WAL
        content is included). DST's -wal/-shm files are removed: a stale WAL
        would be replayed into the restored file. Then SRC replaces DST
        atomically. The writer must be stopped first (restore.sh does that).
    python backup_sqlite.py check PATH [--expect-table T]

Exit status 0 on success. Otherwise non-zero, with the reason on stderr; DST is
then untouched.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import sys
from datetime import datetime, timezone


class BackupError(Exception):
    pass


def _ro(path: str) -> sqlite3.Connection:
    if not os.path.isfile(path):
        raise BackupError(f"{path}: no such file")
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def check(path: str, expect_table: str | None = None) -> None:
    try:
        con = _ro(path)
        try:
            res = con.execute("PRAGMA integrity_check").fetchone()[0]
            tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        finally:
            con.close()
    except sqlite3.DatabaseError as e:
        raise BackupError(f"{path}: not a usable SQLite database ({e})") from e
    if res != "ok":
        raise BackupError(f"{path}: integrity_check failed: {res}")
    if expect_table and expect_table not in tables:
        raise BackupError(f"{path}: no table {expect_table!r} (has {sorted(tables)})")


def snapshot(src: str, dst: str, expect_table: str | None = None) -> None:
    if os.path.exists(dst):
        raise BackupError(f"{dst} exists; refusing to overwrite")
    os.makedirs(os.path.dirname(dst) or ".", exist_ok=True)
    tmp = dst + ".partial"
    if os.path.exists(tmp):
        os.remove(tmp)
    s = _ro(src)
    try:
        d = sqlite3.connect(tmp)
        try:
            s.backup(d)
            d.execute("PRAGMA journal_mode=DELETE")
        finally:
            d.close()
    finally:
        s.close()
    try:
        check(tmp, expect_table)
    except BackupError:
        os.remove(tmp)
        raise
    os.replace(tmp, dst)


def restore(src: str, dst: str, expect_table: str | None = None, now=None) -> str | None:
    """Returns the path of the pre-restore copy of DST (None if DST didn't exist)."""
    check(src, expect_table)
    kept = None
    if os.path.exists(dst):
        ts = (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
        kept = f"{dst}.pre-restore-{ts}"
        snapshot(dst, kept)
    for side in (dst + "-wal", dst + "-shm", dst + "-journal"):
        if os.path.exists(side):
            os.remove(side)
    tmp = dst + ".restoring"
    shutil.copyfile(src, tmp)
    os.replace(tmp, dst)
    check(dst, expect_table)
    return kept


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="backup_sqlite.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("snapshot", "restore"):
        p = sub.add_parser(name)
        p.add_argument("src")
        p.add_argument("dst")
        p.add_argument("--expect-table")
    p = sub.add_parser("check")
    p.add_argument("path")
    p.add_argument("--expect-table")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "snapshot":
            snapshot(a.src, a.dst, a.expect_table)
            print(f"snapshot {a.src} -> {a.dst}: ok")
        elif a.cmd == "restore":
            kept = restore(a.src, a.dst, a.expect_table)
            print(f"restored {a.src} -> {a.dst}: ok"
                  + (f" (previous file kept as {kept})" if kept else " (no previous file)"))
        else:
            check(a.path, a.expect_table)
            print(f"{a.path}: ok")
    except BackupError as e:
        print(f"backup_sqlite: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
