"""One LLM call with the cache, the budget, retries and the ledger
(docs/AGENTS_ARCHITECTURE.md §6). Shared by round 1 and the discussion.

    live   cache hit -> reuse it (no new call). Miss -> the kill switch and
           budget are checked, then up to 1 + llm.max_retries attempts. A
           response is stored in the cache before it is used, and what is used
           is the stored copy.
    rerun  cache only. A miss is `cache_miss`, never a call.

Every attempt, every cache hit and every rerun miss is a row in llm_calls.
Only retryable failures (timeouts, connection errors, 408/409/429/5xx) are
retried, with a capped exponential backoff. A failed call returns the reason,
never raises.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

from agents.llm.budget import Budget
from agents.llm.client import LLMCallError, LLMClient, LLMResponse
from agents.llm.ledger import CallRecord, LLMLedger
from agents.llm.pricing import cost_usd
from agents.llm.request import LLMRequest, MissingImage
from agents.owner_config import BudgetSettings, LLMSettings

BACKOFF_BASE_S = 0.5
BACKOFF_MAX_S = 8.0


def backoff_s(attempt: int) -> float:
    """Wait before attempt `attempt + 1`: 0.5, 1, 2, 4, 8, 8 ... seconds."""
    return min(BACKOFF_MAX_S, BACKOFF_BASE_S * 2 ** (attempt - 1))


@dataclass(frozen=True)
class CallContext:
    """What the ledger row needs besides the request."""
    snapshot_hash: str
    prompt_file_sha256: str | None = None
    config_sha256: str | None = None
    epoch_id: str | None = None


@dataclass(frozen=True)
class CallOutcome:
    response: LLMResponse | None
    reason: str | None                 # why there is no response (the abstain reason)
    call_id: str | None
    attempts: int


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class CallDeps:
    client: LLMClient
    ledger: LLMLedger
    budget: Budget
    image_bytes: Callable[[str], bytes] | None = None
    sleep: Callable[[float], None] = time.sleep
    now: Callable[[], datetime] = _utcnow
    new_id: Callable[[], str] = field(default=lambda: uuid.uuid4().hex)


def run_call(request: LLMRequest, llm: LLMSettings, budget_cfg: BudgetSettings, ctx: CallContext,
             deps: CallDeps) -> CallOutcome:
    h = request.prompt_hash

    def record(attempt: int, *, cached: bool = False, resp: LLMResponse | None = None,
               error: str | None = None, request_id: str | None = None, latency_ms: float | None = None) -> str:
        ts = deps.now().astimezone(timezone.utc)
        call_id = deps.new_id()
        u = resp.usage if resp is not None else {}
        deps.ledger.record_call(CallRecord(
            call_id=call_id, snapshot_hash=ctx.snapshot_hash, agent=request.agent, round=request.round,
            mode=llm.mode, attempt=attempt, model_requested=request.model, effort=request.effort,
            max_tokens=request.max_tokens, prompt_hash=h, cached=cached, utc_day=ts.date().isoformat(),
            created_ts=ts.isoformat(), model_returned=resp.model if resp else None,
            prompt_file_sha256=ctx.prompt_file_sha256, config_sha256=ctx.config_sha256, epoch_id=ctx.epoch_id,
            request_id=resp.request_id if resp else request_id, stop_reason=resp.stop_reason if resp else None,
            refusal_category=resp.refusal_category if resp else None, input_tokens=u.get("input_tokens"),
            output_tokens=u.get("output_tokens"), cache_read_tokens=u.get("cache_read_input_tokens"),
            cache_write_tokens=u.get("cache_creation_input_tokens"), latency_ms=latency_ms, error=error,
            cost_usd=None if resp is None or cached else cost_usd(resp.model, u)))
        return call_id

    cached = deps.ledger.get_response(h)
    if cached is not None:
        return CallOutcome(cached, None, record(0, cached=True, resp=cached), 0)
    if llm.mode == "rerun":
        return CallOutcome(None, "cache_miss", record(0, error="cache_miss"), 0)
    killed = deps.budget.kill_switch(budget_cfg)
    if killed:
        return CallOutcome(None, killed, None, 0)
    try:
        payload = request.payload(deps.image_bytes)
    except MissingImage as e:
        return CallOutcome(None, f"charts: {e}", None, 0)
    last_reason, last_id = "api_error: no attempt made", None
    attempts = 1 + llm.max_retries
    for attempt in range(1, attempts + 1):
        if attempt > 1:
            deps.sleep(backoff_s(attempt - 1))
        refused, day = deps.budget.reserve(budget_cfg)
        if refused:
            return CallOutcome(None, refused, last_id, attempt - 1)
        try:
            resp, err = _attempt(deps.client, payload, llm.timeout_s)
            if resp is not None:
                stored = deps.ledger.put_response(h, resp, deps.now().astimezone(timezone.utc).isoformat())
                return CallOutcome(stored, None, record(attempt, resp=resp, latency_ms=resp.latency_ms), attempt)
            assert err is not None
            last_reason = err.reason
            last_id = record(attempt, error=last_reason, request_id=err.request_id)
            if not err.retryable:
                return CallOutcome(None, last_reason, last_id, attempt)
        finally:
            deps.budget.release(day)
    return CallOutcome(None, last_reason, last_id, attempts)


def _attempt(client: LLMClient, payload: dict, timeout_s: float) -> tuple[LLMResponse | None, LLMCallError | None]:
    """One attempt. Any failure becomes an LLMCallError; an unexpected
    exception from the client is not retried."""
    try:
        return client.complete(payload, timeout_s), None
    except LLMCallError as e:
        return None, e
    except Exception as e:                            # noqa: BLE001 - reported as the reason, never raised
        return None, LLMCallError("api_error", f"{type(e).__name__}: {e}")


__all__ = ["BACKOFF_BASE_S", "BACKOFF_MAX_S", "CallContext", "CallDeps", "CallOutcome", "backoff_s", "run_call"]
