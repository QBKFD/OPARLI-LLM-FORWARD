"""Source protocol and the common message envelope.

Every message on every stream is a flat dict that carries these common fields
next to its payload fields:

    schema_version  int   version of this stream's payload schema
    source          str   name of the producing Source (e.g. "ibkr", "replay")
    symbol          str   instrument, e.g. "XAUUSD"
    source_ts       str   market time the data refers to (ISO-8601, UTC)
    ingest_ts       str   when this process received it (ISO-8601, UTC)

Both timestamps are UTC, tz-aware, rendered as ISO-8601 with "+00:00". A
naive datetime is rejected rather than guessed: the old connector's naive
local fallback is exactly the bug this guards against.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Protocol, runtime_checkable

COMMON_FIELDS = ("schema_version", "source", "symbol", "source_ts", "ingest_ts")

Publish = Callable[[dict], Any]


def utc_iso(ts: datetime) -> str:
    """tz-aware datetime -> ISO-8601 UTC string. Naive input raises."""
    if ts.tzinfo is None or ts.utcoffset() is None:
        raise ValueError(f"naive timestamp {ts!r}: timestamps must be tz-aware UTC")
    return ts.astimezone(timezone.utc).isoformat()


def parse_utc(s: str) -> datetime:
    """Inverse of utc_iso. Rejects strings without an offset."""
    ts = datetime.fromisoformat(s)
    if ts.tzinfo is None:
        raise ValueError(f"timestamp {s!r} has no UTC offset")
    return ts.astimezone(timezone.utc)


def make_message(*, schema_version: int, source: str, symbol: str,
                 source_ts: datetime, payload: dict,
                 ingest_ts: datetime | None = None) -> dict:
    """Build a message: common fields + payload. ingest_ts defaults to now (UTC)."""
    clash = set(payload) & set(COMMON_FIELDS)
    if clash:
        raise ValueError(f"payload may not set common fields: {sorted(clash)}")
    return {
        "schema_version": int(schema_version),
        "source": source,
        "symbol": symbol,
        "source_ts": utc_iso(source_ts),
        "ingest_ts": utc_iso(ingest_ts or datetime.now(timezone.utc)),
        **payload,
    }


def validate_message(msg: dict) -> None:
    """Raise ValueError unless every common field is present and well-formed."""
    missing = [f for f in COMMON_FIELDS if f not in msg]
    if missing:
        raise ValueError(f"message missing common fields: {missing}")
    if not isinstance(msg["schema_version"], int):
        raise ValueError("schema_version must be an int")
    for f in ("source", "symbol"):
        if not isinstance(msg[f], str) or not msg[f]:
            raise ValueError(f"{f} must be a non-empty string")
    for f in ("source_ts", "ingest_ts"):
        parse_utc(msg[f])


@runtime_checkable
class Source(Protocol):
    """A producer of one stream.

    run() blocks, calling publish(msg) for each message, and returns only when
    the source is exhausted (replay) or raises on a fatal error (live sources
    are crash-only: the process exits non-zero and Docker restarts it).
    health() must be cheap and safe to call from another thread.
    """

    name: str
    stream: str
    schema_version: int

    def run(self, publish: Publish) -> None: ...

    def health(self) -> dict: ...
