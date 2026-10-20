"""The daily call budget and the kill switch (docs/AGENTS_ARCHITECTURE.md §6).

- **The kill switch** is the file at `budget.kill_switch_path`, or the env
  var AGENTS_LLM_KILL=1. While either is present, no LLM call is made, and
  every LLM agent abstains `kill_switch`.
- **The budget** is `budget.max_calls_per_day` attempts per UTC day (cache
  hits and rerun misses don't count). An attempt is reserved before it is
  sent, under a lock, so agents running in parallel can't overshoot it.
  Past it, the agent abstains `budget`.
"""

from __future__ import annotations

import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from agents.llm.ledger import LLMLedger
from agents.owner_config import BudgetSettings

KILL_ENV = "AGENTS_LLM_KILL"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Budget:
    def __init__(self, ledger: LLMLedger, now: Callable[[], datetime] = _utcnow,
                 env: dict[str, str] | None = None) -> None:
        self.ledger, self.now = ledger, now
        self.env = env
        self._lock = threading.Lock()
        self._in_flight: dict[str, int] = {}

    def day(self) -> str:
        return self.now().astimezone(timezone.utc).date().isoformat()

    def kill_switch(self, settings: BudgetSettings) -> str | None:
        env = os.environ if self.env is None else self.env
        if env.get(KILL_ENV) == "1":
            return f"kill_switch: ${KILL_ENV}=1"
        if Path(settings.kill_switch_path).exists():
            return f"kill_switch: {Path(settings.kill_switch_path).name} exists"
        return None

    def reserve(self, settings: BudgetSettings) -> tuple[str | None, str]:
        """(None, day) if an attempt is reserved, else (the reason, day).
        A reserved attempt must be released once it is recorded."""
        with self._lock:
            day = self.day()
            used = self.ledger.attempts_on(day) + self._in_flight.get(day, 0)
            if used >= settings.max_calls_per_day:
                return f"budget: {used} attempts on {day}, max {settings.max_calls_per_day}", day
            self._in_flight[day] = self._in_flight.get(day, 0) + 1
            return None, day

    def release(self, day: str) -> None:
        with self._lock:
            n = self._in_flight.get(day, 0) - 1
            if n < 0:
                raise RuntimeError(f"budget: release without a reservation on {day}")
            self._in_flight[day] = n


__all__ = ["Budget", "KILL_ENV"]
