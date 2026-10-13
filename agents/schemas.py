"""Schemas for the agents graph (pydantic v2).

Every model is frozen, refuses unknown fields and refuses NaN/inf. Datetimes
must be tz-aware and are normalised to UTC.

Snapshot   what was knowable at as_of: bars strictly before as_of (+ empty
           slots for levels, features, events). Its hash covers all content.
Vote       one round-1 ("blind") vote. An invalid or missing vote becomes an
           abstain with a reason (Vote.abstained), never a guess.
Claim      one debate claim + the evidence_ref it rests on. `valid` is set by
           the evidence check in code, never by the debater.
Turn       one debater's turn in one round.
DebateResult  all turns + the judge's outcome.
Decision   one arm's decision (blind or debate). entry/sl/tp/size/sigma stay
           null until a risk/ package fills them.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from agents.hashing import canonical_json, sha256_hex


def _utc(v: datetime) -> datetime:
    return v.astimezone(timezone.utc)


UtcDatetime = Annotated[AwareDatetime, AfterValidator(_utc)]

# Bar timeframes and their length in minutes. Buckets are aligned to the UTC
# epoch, so 4h bars open at 00, 04, 08, 12, 16 and 20 UTC.
TIMEFRAMES: dict[str, int] = {"1m": 1, "15m": 15, "1h": 60, "4h": 240}
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)

Direction = Literal["long", "short", "none"]
Role = Literal["bull", "bear"]
Arm = Literal["blind", "debate"]
Outcome = Literal["long", "short", "hold"]

ROLE_SIDE: dict[str, str] = {"bull": "long", "bear": "short"}
ROLE_ORDER: dict[str, int] = {"bull": 0, "bear": 1}


def tf_delta(tf: str) -> timedelta:
    return timedelta(minutes=TIMEFRAMES[tf])


def is_aligned(ts: datetime, tf: str) -> bool:
    return (ts - EPOCH) % tf_delta(tf) == timedelta(0)


def outcome_to_direction(outcome: str) -> str:
    """Debate outcome -> Decision direction: hold means no position ("none")."""
    return "none" if outcome == "hold" else outcome


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)


class _ModelMeta(_Frozen):
    """Columns for a model-backed producer (LLM agent, debater or judge later).
    All null for code."""
    model_id: str | None = None
    prompt_hash: str | None = None
    tokens_in: int | None = Field(None, ge=0)
    tokens_out: int | None = Field(None, ge=0)
    latency_ms: float | None = Field(None, ge=0)


# ---------------------------------------------------------------- snapshot

class Bar(_Frozen):
    ts: UtcDatetime                      # bar open time
    open: float
    high: float
    low: float
    close: float
    n_1m: int = Field(1, ge=1)           # 1-min bars inside; gaps stay visible
    complete: bool = True                # bucket end <= as_of
    kind: str | None = None              # 1m only: live / history / replay


class Level(_Frozen):
    """Placeholder until a levels/ package exists."""
    id: str = Field(min_length=1)
    price: float
    label: str = ""


class Event(_Frozen):
    """Placeholder until datafeed/ingest/calendar.py exists."""
    id: str = Field(min_length=1)
    ts: UtcDatetime
    title: str = ""


class Snapshot(_Frozen):
    schema_version: Literal[1] = 1
    symbol: str = Field(min_length=1)
    as_of: UtcDatetime                   # every bar has ts < as_of
    kinds: tuple[str, ...]
    known_by: UtcDatetime | None = None
    bars: dict[str, tuple[Bar, ...]]
    newest_bar_ts: UtcDatetime
    levels: tuple[Level, ...] = ()
    features: dict[str, float] = Field(default_factory=dict)
    events: tuple[Event, ...] = ()
    hash: str = ""                       # computed; a wrong value raises

    def content_hash(self) -> str:
        return sha256_hex(self.model_dump(mode="python", exclude={"hash"}))

    def series(self, tf: str) -> tuple[Bar, ...]:
        return self.bars[tf]

    def to_json(self) -> str:
        return canonical_json(self)

    @model_validator(mode="after")
    def _check(self):
        if not is_aligned(self.as_of, "1m"):
            raise ValueError(f"as_of {self.as_of} is not a whole minute")
        if "1m" not in self.bars:
            raise ValueError("bars must include '1m'")
        for tf, series in self.bars.items():
            if tf not in TIMEFRAMES:
                raise ValueError(f"unknown timeframe {tf!r}")
            step, prev = tf_delta(tf), None
            for b in series:
                if b.ts >= self.as_of:
                    raise ValueError(f"{tf} bar {b.ts} is not before as_of {self.as_of}")
                if not is_aligned(b.ts, tf):
                    raise ValueError(f"{tf} bar {b.ts} is not aligned")
                if prev is not None and b.ts <= prev:
                    raise ValueError(f"{tf} bars are not strictly increasing at {b.ts}")
                if b.complete != (b.ts + step <= self.as_of):
                    raise ValueError(f"{tf} bar {b.ts}: complete={b.complete} is wrong")
                if tf == "1m" and b.n_1m != 1:
                    raise ValueError("a 1m bar has n_1m == 1")
                prev = b.ts
        one = self.bars["1m"]
        if not one or one[-1].ts != self.newest_bar_ts:
            raise ValueError("newest_bar_ts must be the last 1m bar")
        for name, ids in (("level", [x.id for x in self.levels]), ("event", [x.id for x in self.events])):
            if len(ids) != len(set(ids)):
                raise ValueError(f"duplicate {name} id")
        h = self.content_hash()
        if not self.hash:
            object.__setattr__(self, "hash", h)          # frozen: set once, here only
        elif self.hash != h:
            raise ValueError(f"snapshot hash {self.hash} does not match its content ({h})")
        return self


# ---------------------------------------------------------------- votes

class Vote(_ModelMeta):
    agent: str = Field(min_length=1)
    round: int = Field(1, ge=1)          # 1 = blind
    direction: Direction
    confidence: float = Field(ge=0, le=1)
    reason: str = ""
    abstain: bool = False
    abstain_reason: str | None = None

    @model_validator(mode="after")
    def _check(self):
        if self.abstain:
            if self.direction != "none" or self.confidence != 0 or not self.abstain_reason:
                raise ValueError("an abstain vote has direction 'none', confidence 0 and an abstain_reason")
        elif self.abstain_reason is not None:
            raise ValueError("abstain_reason is set but abstain is false")
        return self

    @classmethod
    def abstained(cls, agent: str, reason: str, **meta) -> "Vote":
        return cls(agent=agent, direction="none", confidence=0.0, abstain=True,
                   abstain_reason=reason, **meta)


# ---------------------------------------------------------------- debate

class Claim(_Frozen):
    text: str
    evidence_ref: str                    # raw, as the debater produced it
    valid: bool | None = None            # None until the evidence check runs
    invalid_reason: str | None = None

    @model_validator(mode="after")
    def _check(self):
        if (self.valid is False) != (self.invalid_reason is not None):
            raise ValueError("invalid_reason is set exactly when valid is False")
        return self


class Turn(_ModelMeta):
    role: Role
    round: int = Field(ge=1)
    claims: tuple[Claim, ...] = ()
    conceded: bool = False
    error: str | None = None             # set -> an empty turn

    @model_validator(mode="after")
    def _check(self):
        if self.error is not None and (self.claims or self.conceded):
            raise ValueError("a turn with an error has no claims and no concession")
        return self

    def n_valid(self) -> int:
        return sum(c.valid is True for c in self.claims)


class DebateResult(_Frozen):
    snapshot_hash: str
    max_rounds: int = Field(ge=1)
    turns: tuple[Turn, ...]              # ordered by (round, bull before bear)
    n_valid_bull: int = Field(ge=0)
    n_valid_bear: int = Field(ge=0)
    judge_name: str
    outcome: Outcome
    reason: str

    @model_validator(mode="after")
    def _check(self):
        keys = [(t.round, ROLE_ORDER[t.role]) for t in self.turns]
        if keys != sorted(set(keys)):
            raise ValueError("turns must be unique and ordered by (round, bull before bear)")
        for role, n in (("bull", self.n_valid_bull), ("bear", self.n_valid_bear)):
            if n != sum(t.n_valid() for t in self.turns if t.role == role):
                raise ValueError(f"n_valid_{role} does not match the turns")
        return self


# ---------------------------------------------------------------- decision

class Decision(_ModelMeta):
    run_id: str = Field(min_length=1)
    snapshot_hash: str
    arm: Arm
    direction: Direction
    reason: str
    decided_by: str                      # "aggregator_v0" or the judge's name
    entry: float | None = None           # entry .. sigma: filled by risk/ later
    sl: float | None = None
    tp: float | None = None
    size: float | None = Field(None, ge=0)
    sigma: float | None = Field(None, ge=0)


# ---------------------------------------------------------------- trigger

class TriggerEvent(_Frozen):
    """What starts a run. Clock or external event: the producer is not part of
    this package. The payload is stored with the run, never shown to agents."""
    source: str = Field(min_length=1)
    t: UtcDatetime
    trigger_id: str = Field(min_length=1)
    payload: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check(self):
        try:
            canonical_json(self.payload)
        except (TypeError, ValueError) as e:
            raise ValueError(f"payload must be plain JSON: {e}") from None
        return self
