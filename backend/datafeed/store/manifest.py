"""The data manifest: what the untracked price files are, and their hashes.

`manifest.json` (next to this file, shipped as package data so it is there in
every image) lists the price files code reads, by repo-relative path: the
untracked real files under database/ and the fixtures. For each one: where it
came from, how it was built, its date range and row count, and hashes:

- `sha256`: of the file's bytes. verify() checks it, so a loader stops
  before reading a file that isn't the one the results were computed on.
- `content_sha256` (parquet only): of the bars themselves (content_sha256()
  below), so a rebuilt file (tools/data/build_clean_parquet.py) can be
  checked even though parquet writers don't produce identical bytes. An
  entry with only a content hash (the fixtures, which are regenerated) is
  checked by content.

Files are matched by name (the manifest key's last part): a loader given a
file with a listed name must get exactly that file; any other name (a test
fixture, a scratch extract) isn't checked.

    python -m backend.datafeed.store.manifest check [--data-dir DIR] [--content]
        every listed file (at its path, or by name in DIR): missing, ok, or
        MISMATCH. Exit 1 on any mismatch, 0 otherwise.
    python -m backend.datafeed.store.manifest describe PATH
        the hashes, size, rows and range of PATH, as JSON, for a new entry.

Stdlib only, except content_sha256 and describe on a parquet (pandas).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from functools import lru_cache
from pathlib import Path

MANIFEST = Path(__file__).with_name("manifest.json")
ROOT = Path(__file__).resolve().parents[3]              # the repo root; manifest keys are relative to it
SCHEMA = "data_manifest/1"
# the parquet columns content_sha256 covers, in this order
CONTENT_COLUMNS = ("timestamp", "open", "high", "low", "close", "volume")
_CHUNK = 1 << 20


class ManifestError(RuntimeError):
    """A file with a listed name doesn't match its manifest entry."""


def load(path=None) -> dict:
    """The manifest (MANIFEST unless `path`), read once per process per path."""
    return _load(str(path or MANIFEST))


@lru_cache(maxsize=4)
def _load(path: str) -> dict:
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    if doc.get("schema") != SCHEMA:
        raise ManifestError(f"{path}: schema {doc.get('schema')!r}, expected {SCHEMA!r}")
    return doc


def entry_for(path) -> tuple[str, dict] | None:
    """(key, entry) for the listed file with this file's name, else None."""
    name = Path(path).name
    for key, entry in load()["files"].items():
        if Path(key).name == name:
            return key, entry
    return None


def file_sha256(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while block := f.read(_CHUNK):
            h.update(block)
    return h.hexdigest()


def content_sha256(df) -> str:
    """SHA-256 of the bars: per column of CONTENT_COLUMNS in order, the values
    as little-endian 8-byte numbers (timestamp: int64 nanoseconds since the
    epoch, UTC; prices and volume: float64), rows in timestamp order. The
    same bars give the same hash whatever wrote the file."""
    import numpy as np
    import pandas as pd
    df = df.sort_values("timestamp", kind="stable")
    ts = pd.DatetimeIndex(df["timestamp"])
    ts = ts.tz_localize("UTC") if ts.tz is None else ts.tz_convert("UTC")
    h = hashlib.sha256()
    h.update(np.ascontiguousarray(ts.asi8, dtype="<i8").tobytes())
    for c in CONTENT_COLUMNS[1:]:
        h.update(np.ascontiguousarray(df[c].to_numpy(dtype="float64"), dtype="<f8").tobytes())
    return h.hexdigest()


_verified: dict[tuple[str, int, int], str] = {}          # (realpath, size, mtime_ns) -> key, per process


def verify(path, *, content: bool = False) -> str | None:
    """Raise ManifestError unless `path` matches its entry. Returns the key,
    or None when no entry has this name (nothing to check). The byte hash is
    computed once per process per unchanged file. content=True also checks
    content_sha256 (parquet entries only); an entry without `sha256` is
    always checked by content."""
    found = entry_for(path)
    if found is None:
        return None
    key, entry = found
    st = os.stat(path)
    memo = (os.path.realpath(path), st.st_size, st.st_mtime_ns)
    content = content or "sha256" not in entry
    if memo not in _verified and "sha256" in entry:
        if st.st_size != entry["bytes"]:
            raise ManifestError(f"{path}: {st.st_size} bytes, the manifest's {key} has {entry['bytes']}; "
                                f"not the file the results were computed on")
        got = file_sha256(path)
        if got != entry["sha256"]:
            raise ManifestError(f"{path}: SHA-256 {got}, the manifest's {key} has {entry['sha256']}; "
                                f"not the file the results were computed on")
    _verified[memo] = key
    if content:
        if "content_sha256" not in entry:
            raise ManifestError(f"{key} has no content_sha256 (not a parquet entry)")
        import pandas as pd
        got = content_sha256(pd.read_parquet(path, columns=list(CONTENT_COLUMNS)))
        if got != entry["content_sha256"]:
            raise ManifestError(f"{path}: content SHA-256 {got}, the manifest has {entry['content_sha256']}")
    return key


def describe(path) -> dict:
    """The measurable fields of an entry for `path`."""
    out = {"bytes": os.path.getsize(path), "sha256": file_sha256(path)}
    if str(path).endswith(".parquet"):
        import pandas as pd
        df = pd.read_parquet(path)
        ts = pd.DatetimeIndex(df["timestamp"])
        out.update(rows=len(df), first_ts=ts.min().isoformat(), last_ts=ts.max().isoformat(),
                   columns={c: str(t) for c, t in df.dtypes.items()},
                   content_sha256=content_sha256(df[list(CONTENT_COLUMNS)]))
    return out


def check(data_dir=None, content: bool = False, out=None) -> int:
    out = out or sys.stdout
    bad = 0
    for key in load()["files"]:
        p = Path(data_dir) / Path(key).name if data_dir else ROOT / key
        if not p.exists():
            print(f"missing   {key} (not at {p})", file=out)
            continue
        try:
            verify(p, content=content and key.endswith(".parquet"))
            print(f"ok        {key}", file=out)
        except ManifestError as e:
            bad += 1
            print(f"MISMATCH  {key}: {e}", file=out)
    return 1 if bad else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m backend.datafeed.store.manifest")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check")
    c.add_argument("--data-dir", help="look for each file by name here instead of at its path")
    c.add_argument("--content", action="store_true", help="also check the parquet's content_sha256 (pandas)")
    d = sub.add_parser("describe")
    d.add_argument("path")
    a = ap.parse_args(argv)
    if a.cmd == "check":
        return check(a.data_dir, a.content)
    print(json.dumps(describe(a.path), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
