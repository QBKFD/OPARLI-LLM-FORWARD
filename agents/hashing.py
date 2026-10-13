"""Canonical JSON and sha256, so equal content always gives the same hash.

Canonical form: sorted keys, compact separators, ASCII only. Datetimes are UTC
ISO-8601 with "+00:00" (the datafeed form for whole seconds). Floats go through
json's repr, which round-trips exactly. NaN and infinity are refused. Tuples
become lists. Anything else (sets, bytes, naive datetimes, ...) raises, so a
hash never depends on an ambiguous encoding.
"""

from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone

from pydantic import BaseModel


def _norm(x):
    if isinstance(x, BaseModel):
        return _norm(x.model_dump(mode="python"))
    if isinstance(x, dict):
        out = {}
        for k, v in x.items():
            if not isinstance(k, str):
                raise TypeError(f"dict key must be str, got {type(k).__name__}")
            out[k] = _norm(v)
        return out
    if isinstance(x, (list, tuple)):
        return [_norm(v) for v in x]
    if isinstance(x, datetime):
        if x.tzinfo is None:
            raise ValueError(f"naive datetime {x!r}")
        return x.astimezone(timezone.utc).isoformat()
    if x is None or isinstance(x, (bool, int, str)):
        return x
    if isinstance(x, float):
        if not math.isfinite(x):
            raise ValueError(f"non-finite float {x!r}")
        return x
    raise TypeError(f"cannot canonicalise {type(x).__name__}")


def canonical_json(obj) -> str:
    return json.dumps(_norm(obj), sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False)


def sha256_hex(obj) -> str:
    return hashlib.sha256(canonical_json(obj).encode("ascii")).hexdigest()
