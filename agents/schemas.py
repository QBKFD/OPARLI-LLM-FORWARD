"""Schemas for the agents graph (pydantic v2).

Every model is frozen, refuses unknown fields and refuses NaN/inf. Datetimes
must be tz-aware and are normalised to UTC.

Snapshot   what was knowable at as_of: bars strictly before as_of, plus the
           slots the prompts render (alert, levels, features, metrics,
           events, charts). A slot whose builder doesn't exist yet stays empty
           and is named in `unavailable` with the reason. Its hash covers all
           content.
Vote       one vote: round 1 = blind, round 2.. = discussion rounds. An
           invalid or missing vote becomes an abstain with a reason
           (Vote.abstained), never a guess.
DiscussionView / DiscussionResult  what a discussant sees, and every
           discussion vote with the round count (docs/AGENTS_ARCHITECTURE.md §5.2).
Decision   one arm's decision (blind, discussion; technical_v1 when that agent
           exists). entry/sl/tp/size/sigma stay null until risk/ fills them.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo

from pydantic import AfterValidator, AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from agents.hashing import canonical_json, sha256_hex


def _utc(v: datetime) -> datetime:
    return v.astimezone(timezone.utc)


UtcDatetime = Annotated[AwareDatetime, AfterValidator(_utc)]

# Bar timeframes and their nominal length in minutes.
# - 1m, 15m, 1h: buckets aligned to the UTC epoch. NY offsets are whole hours,
#   so these are aligned in New York time too.
# - 4h (owner decision 2026-10-14): anchored to the NY 17:00 trading-day open.
#   Buckets open at 17, 21, 01, 05, 09 and 13 America/New_York wall-clock time,
#   so the UTC opens shift by an hour with DST (summer 21, 01, 05, 09, 13,
#   17 UTC; winter 22, 02, 06, 10, 14, 18 UTC). A 4h bucket that spans a DST
#   change lasts 3 h or 5 h of real time. That only happens early on Sunday
#   (NY), while the market is closed.
TIMEFRAMES: dict[str, int] = {"1m": 1, "15m": 15, "1h": 60, "4h": 240}
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
NY = ZoneInfo("America/New_York")
NY_4H_ANCHOR_HOUR = 17          # 4h buckets open at local hours = 17 (mod 4), i.e. 1, 5, 9, 13, 17, 21

Direction = Literal["long", "short", "none"]
# blind: Meta on the round-1 votes; discussion: Meta on the final votes;
# technical_v1: the deterministic benchmark's own vote (agents v1).
Arm = Literal["blind", "discussion", "technical_v1"]


def tf_delta(tf: str) -> timedelta:
    """Nominal length. For 4h use bucket_end: a DST bucket is 3 h or 5 h."""
    return timedelta(minutes=TIMEFRAMES[tf])


def _ny_wall(local: datetime) -> datetime:
    """A NY wall-clock time -> UTC. Ambiguous times (the repeated 01:xx hour at
    the end of DST) take the first occurrence. Bucket starts never fall in the
    skipped spring hour."""
    return local.replace(fold=0).astimezone(timezone.utc)


def bucket_start(ts: datetime, tf: str) -> datetime:
    """Open time (UTC) of the tf bucket that contains ts."""
    if tf == "4h":
        local = ts.astimezone(NY)
        hour = local.replace(minute=0, second=0, microsecond=0)
        return _ny_wall(hour - timedelta(hours=(local.hour - NY_4H_ANCHOR_HOUR) % 4))
    return ts - (ts - EPOCH) % tf_delta(tf)


def bucket_end(start: datetime, tf: str) -> datetime:
    """End (UTC, exclusive) of the bucket that opens at start: the next bucket's open."""
    if tf == "4h":
        return _ny_wall(start.astimezone(NY) + timedelta(hours=4))     # wall-clock arithmetic
    return start + tf_delta(tf)


def is_aligned(ts: datetime, tf: str) -> bool:
    return bucket_start(ts, tf) == ts


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
    """A scanner level active at as_of (docs/AGENTS_ARCHITECTURE.md §4.1).
    Filled by the shared level library once it exists; empty until then."""
    id: str = Field(min_length=1)
    type: str = Field(min_length=1)      # PDH PDL PWH PWL DOPEN WOPEN ASIA_H ...
    value: float
    active_from: UtcDatetime
    touched: bool = False


class Event(_Frozen):
    """A calendar event, point in time: actual is set only if released before
    as_of. Filled by the calendar store once it exists; empty until then."""
    id: str = Field(min_length=1)
    ts: UtcDatetime
    currency: str = ""
    tier: int | None = Field(None, ge=1)
    name: str = ""
    actual: str | None = None
    forecast: str | None = None
    previous: str | None = None


ALERT_FIELDS = ("alert_id", "alert_ts", "session", "level_types", "level_price", "price_at_alert",
                "confluence_count")


class AlertFacts(_Frozen):
    """The scanner alert's Telegram facts, the only part of the trigger an
    agent sees (never the raw journal row)."""
    alert_id: str = Field(min_length=1)
    alert_ts: UtcDatetime
    session: str
    level_types: tuple[str, ...]
    level_price: float
    price_at_alert: float
    confluence_count: int = Field(ge=1)


class ChartRef(_Frozen):
    """A chart PNG rendered from the snapshot's bars, by content hash."""
    tf: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    width: int = Field(gt=0)
    height: int = Field(gt=0)


class MetricRow(_Frozen):
    """One row of the Technical metric table (job 5). Never imputed: an
    uncomputable metric has value None, available False and a reason."""
    name: str = Field(min_length=1)
    value: float | None
    unit: str
    available: bool
    reason: str | None = None

    @model_validator(mode="after")
    def _check(self):
        if self.available and (self.value is None or self.reason is not None):
            raise ValueError(f"metric {self.name}: an available metric has a value and no reason")
        if not self.available and (self.value is not None or not self.reason):
            raise ValueError(f"metric {self.name}: an unavailable metric has no value and a reason")
        return self


# Snapshot slots that can be missing until their builder exists, and render as
# "unavailable" in a prompt (agents/prompting/render.py).
SLOTS = ("alert", "levels", "features", "metrics", "events", "charts")


class Snapshot(_Frozen):
    schema_version: Literal[3] = 3       # 2: experiment_id; 3: alert, trigger_levels, metrics, charts, unavailable
    symbol: str = Field(min_length=1)
    as_of: UtcDatetime                   # every bar has ts < as_of
    kinds: tuple[str, ...]
    known_by: UtcDatetime | None = None
    bars: dict[str, tuple[Bar, ...]]
    newest_bar_ts: UtcDatetime
    alert: AlertFacts | None = None
    levels: tuple[Level, ...] = ()
    trigger_levels: tuple[str, ...] = ()  # ids in levels that the alert touched
    features: dict[str, float] = Field(default_factory=dict)
    metrics: tuple[MetricRow, ...] = ()
    events: tuple[Event, ...] = ()
    charts: tuple[ChartRef, ...] = ()
    unavailable: dict[str, str] = Field(default_factory=dict)   # slot -> why it is empty
    experiment_id: str | None = None     # provenance; never render it into a prompt
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
            prev = None
            for b in series:
                if b.ts >= self.as_of:
                    raise ValueError(f"{tf} bar {b.ts} is not before as_of {self.as_of}")
                if not is_aligned(b.ts, tf):
                    raise ValueError(f"{tf} bar {b.ts} is not aligned")
                if prev is not None and b.ts <= prev:
                    raise ValueError(f"{tf} bars are not strictly increasing at {b.ts}")
                if b.complete != (bucket_end(b.ts, tf) <= self.as_of):
                    raise ValueError(f"{tf} bar {b.ts}: complete={b.complete} is wrong")
                if tf == "1m" and b.n_1m != 1:
                    raise ValueError("a 1m bar has n_1m == 1")
                prev = b.ts
        one = self.bars["1m"]
        if not one or one[-1].ts != self.newest_bar_ts:
            raise ValueError("newest_bar_ts must be the last 1m bar")
        for name, ids in (("level", [x.id for x in self.levels]), ("event", [x.id for x in self.events]),
                          ("metric", [x.name for x in self.metrics]), ("chart", [x.tf for x in self.charts])):
            if len(ids) != len(set(ids)):
                raise ValueError(f"duplicate {name} id")
        if not set(self.trigger_levels) <= {x.id for x in self.levels}:
            raise ValueError("trigger_levels must be ids in levels")
        if any(x.active_from > self.as_of for x in self.levels):
            raise ValueError("a level is active only from after as_of")
        for slot, why in self.unavailable.items():
            if slot not in SLOTS or not why:
                raise ValueError(f"unavailable: unknown slot {slot!r} or empty reason")
            if getattr(self, slot):
                raise ValueError(f"slot {slot!r} is marked unavailable but has content")
        h = self.content_hash()
        if not self.hash:
            object.__setattr__(self, "hash", h)          # frozen: set once, here only
        elif self.hash != h:
            raise ValueError(f"snapshot hash {self.hash} does not match its content ({h})")
        return self


# ---------------------------------------------------------------- votes

class Evidence(_Frozen):
    """One claim of an LLM vote and the ref it rests on. `valid` is set by the
    evidence check in code (check_ref), never by the model."""
    ref: str
    claim: str = Field(max_length=200)
    valid: bool | None = None
    invalid_reason: str | None = None

    @model_validator(mode="after")
    def _check(self):
        if (self.valid is False) != (self.invalid_reason is not None):
            raise ValueError("invalid_reason is set exactly when valid is False")
        return self


class RejectedChange(_Frozen):
    """A discussion vote the change rule refused (§5.3): kept as data."""
    direction: Direction
    confidence: float = Field(ge=0, le=1)
    reason: str = ""
    evidence: tuple[Evidence, ...] = ()


class Vote(_ModelMeta):
    agent: str = Field(min_length=1)
    round: int = Field(1, ge=1)          # 1 = blind
    direction: Direction
    confidence: float = Field(ge=0, le=1)
    reason: str = ""
    abstain: bool = False
    abstain_reason: str | None = None
    evidence: tuple[Evidence, ...] = ()  # LLM votes; empty for code agents and abstains
    call_id: str | None = None           # the llm_calls row that produced it (LLM agents)
    changed: bool = False                # discussion: an accepted change from this agent's previous vote
    change_rejected: str | None = None   # discussion: why a change was refused, or the call failed
    rejected_proposal: RejectedChange | None = None   # discussion: the refused vote itself

    @model_validator(mode="after")
    def _check(self):
        if self.abstain:
            if self.direction != "none" or self.confidence != 0 or not self.abstain_reason:
                raise ValueError("an abstain vote has direction 'none', confidence 0 and an abstain_reason")
        elif self.abstain_reason is not None:
            raise ValueError("abstain_reason is set but abstain is false")
        if self.round == 1 and (self.changed or self.change_rejected or self.rejected_proposal):
            raise ValueError("changed / change_rejected / rejected_proposal are discussion fields")
        if self.changed and self.change_rejected is not None:
            raise ValueError("a vote is either a changed vote or a refused change, not both")
        if self.rejected_proposal is not None and self.change_rejected is None:
            raise ValueError("a rejected_proposal needs its change_rejected reason")
        return self

    @classmethod
    def abstained(cls, agent: str, reason: str, **meta) -> "Vote":
        return cls(agent=agent, direction="none", confidence=0.0, abstain=True,
                   abstain_reason=reason, **meta)


# ---------------------------------------------------------------- discussion

class DiscussionView(_Frozen):
    """What a discussant sees in one discussion round, and nothing else.
    Votes are content only (no model ids, prompt hashes, tokens or latency)."""
    agent: str
    round: int = Field(ge=1)              # 1..max_rounds within the discussion
    max_rounds: int = Field(ge=1)
    snapshot: Snapshot
    own_previous: Vote
    others: tuple[Vote, ...]              # every other agent's latest vote


class DiscussionResult(_Frozen):
    snapshot_hash: str
    max_rounds: int = Field(ge=0)         # 0 when the discussion config is invalid
    rounds_run: int = Field(ge=0)         # 0: nobody spoke (skip_reason says why)
    stopped_early: bool
    skip_reason: str | None = None
    speakers: tuple[str, ...]
    votes: tuple[Vote, ...]               # every discussion vote, ordered by (round, agent)
    final: dict[str, Vote]                # each agent's last vote (its round-1 vote if it never spoke)

    @model_validator(mode="after")
    def _check(self):
        keys = [(v.round, v.agent) for v in self.votes]
        if keys != sorted(set(keys)):
            raise ValueError("discussion votes must be unique and ordered by (round, agent)")
        if any(v.round < 2 or v.round > self.rounds_run + 1 for v in self.votes):
            raise ValueError("discussion votes have rounds 2 .. rounds_run + 1")
        if any(v.agent not in self.speakers for v in self.votes):
            raise ValueError("only speakers have discussion votes")
        if (self.rounds_run == 0) != (self.skip_reason is not None):
            raise ValueError("a discussion that didn't run has a skip_reason, and only then")
        return self


# ---------------------------------------------------------------- decision

class Decision(_ModelMeta):
    """epoch_id, owner_config_sha256 and prompts_sha256 are stamped on every
    decision by the graph (agents/epoch.py); prompts_sha256 maps each prompt
    file to its SHA-256, or to the reason it could not be loaded."""
    run_id: str = Field(min_length=1)
    snapshot_hash: str
    arm: Arm
    direction: Direction
    reason: str
    decided_by: str                      # the Meta rule (or the agent, for technical_v1)
    meta_rule: str | None = None
    meta_version: str | None = None
    experiment_id: str | None = None     # = the snapshot's
    entry: float | None = None           # entry .. sigma: filled by risk/ later
    sl: float | None = None
    tp: float | None = None
    size: float | None = Field(None, ge=0)
    sigma: float | None = Field(None, ge=0)
    epoch_id: str | None = None
    owner_config_sha256: str | None = None
    prompts_sha256: dict[str, str] = Field(default_factory=dict)


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
