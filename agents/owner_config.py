"""The owner's agents.toml: models, effort, max_tokens, discussion, Meta rule,
LLM call settings, budget and render sizes (docs/AGENTS_ARCHITECTURE.md §6.1).

The owner sets every value. Code never writes this file and never supplies a
default model, effort, max_tokens or Meta rule. The only defaults are the two
the owner fixed: discussion.rounds = 2 and discussion.early_stop = true.

Validation is per section, so one bad value can't stop the run. Each accessor
returns the validated settings or a ConfigProblem, and the caller turns a
problem into an abstain with `problem.reason`
(`config_invalid: <key>: <problem>`). Only an unreadable file (missing, not
UTF-8, bad TOML syntax) leaves every section a problem, still without raising.

The path comes from $AGENTS_CONFIG unless one is given. The file is separate
from the infrastructure config (agents/config.py: snapshot, gate, timeouts),
so the owner's file holds the owner's values only.

    cfg = OwnerConfig.load()                       # or OwnerConfig.load("agents.toml")
    call = cfg.call("visual", "round1")            # CallSettings | ConfigProblem
"""

from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from agents.hashing import canonical_json, sha256_hex
from agents.schemas import TIMEFRAMES

ENV_CONFIG = "AGENTS_CONFIG"
LLM_AGENTS = ("visual", "technical", "sentiment")
CODE_AGENTS = ("gex", "technical_v1")
AGENTS = LLM_AGENTS + CODE_AGENTS
RoundKind = Literal["round1", "discussion"]
ROUND_KINDS: tuple[RoundKind, ...] = ("round1", "discussion")
Effort = Literal["low", "medium", "high", "xhigh", "max"]
SECTIONS = ("discussion", "meta", "llm", "budget", "render", "agents")
# an exact model id: claude-<name>-<version parts>; no "-latest" alias, no date suffix
MODEL_ID = re.compile(r"claude-[a-z0-9]+(?:-[a-z0-9]+)*")
DATE_SUFFIX = re.compile(r"-\d{8}$")
MAX_TOKENS_CAP = 128_000
# a value copied from agents.example.toml and never filled in: "<...>"
UNFILLED = re.compile(r"<.*>", re.S)


class _Owner(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, allow_inf_nan=False)


class CallSettings(_Owner):
    """One model call setting block: agents.<name>.round1 or .discussion."""
    model: str
    effort: Effort
    max_tokens: int = Field(ge=1, le=MAX_TOKENS_CAP)

    @field_validator("model")
    @classmethod
    def _exact_id(cls, v: str) -> str:
        if not MODEL_ID.fullmatch(v):
            raise ValueError(f"{v!r} is not an exact model id (e.g. claude-opus-5-5)")
        if v.endswith("-latest") or DATE_SUFFIX.search(v):
            raise ValueError(f"{v!r} is an alias or has a date suffix; use the exact id")
        return v


class DiscussionSettings(_Owner):
    rounds: int = Field(2, ge=1, le=10)          # owner default: 2
    early_stop: bool = True                      # owner default: on


class LLMSettings(_Owner):
    mode: Literal["live", "rerun"]
    timeout_s: float = Field(gt=0, le=600)
    max_retries: int = Field(ge=0, le=5)


class BudgetSettings(_Owner):
    max_calls_per_day: int = Field(ge=0)
    kill_switch_path: str = Field(min_length=1)


class MetaSettings(_Owner):
    rule: str = Field(min_length=1)


@dataclass(frozen=True)
class ConfigProblem:
    """Why a config value can't be used. `reason` is the abstain reason."""
    key: str
    message: str

    @property
    def reason(self) -> str:
        return f"config_invalid: {self.key}: {self.message}"


T = TypeVar("T", bound=BaseModel)


def unfilled_key(data: Any, prefix: str) -> str | None:
    """The dotted key of the first "<...>" placeholder value left in data."""
    if isinstance(data, dict):
        for k, v in data.items():
            hit = unfilled_key(v, f"{prefix}.{k}")
            if hit:
                return hit
    elif isinstance(data, list):
        for i, v in enumerate(data):
            hit = unfilled_key(v, f"{prefix}.{i}")
            if hit:
                return hit
    elif isinstance(data, str) and UNFILLED.fullmatch(data.strip()):
        return prefix
    return None


def _first_error(e: ValidationError) -> tuple[str, str]:
    err = e.errors()[0]
    return ".".join(map(str, err["loc"])), err["msg"]


@dataclass(frozen=True)
class OwnerConfig:
    """A loaded agents.toml. `raw` is the parsed TOML; `sha256` is the SHA-256
    of its canonical JSON (formatting and comments don't count, values do),
    or None when the file couldn't be read (`load_error` says why)."""
    path: str | None
    raw: dict[str, Any] = field(default_factory=dict)
    sha256: str | None = None
    load_error: str | None = None

    # ------------------------------------------------------------ loading

    @classmethod
    def load(cls, path: str | Path | None = None, env: dict[str, str] | None = None) -> "OwnerConfig":
        """Read and parse the file. Never raises: an unreadable file gives an
        OwnerConfig whose every accessor returns a problem."""
        env = dict(os.environ) if env is None else env
        p = str(path) if path is not None else env.get(ENV_CONFIG) or None
        # load_error never includes the path: it enters the epoch, which must be
        # the same for the same file in any checkout. `path` says which file.
        if p is None:
            return cls(None, load_error=f"no config file: ${ENV_CONFIG} is not set")
        try:
            text = Path(p).read_bytes().decode("utf-8")
        except FileNotFoundError:
            return cls(p, load_error="agents.toml not found")
        except (OSError, UnicodeDecodeError) as e:
            return cls(p, load_error=f"agents.toml unreadable ({type(e).__name__})")
        return cls.from_text(text, p)

    @classmethod
    def from_text(cls, text: str, path: str | None = None) -> "OwnerConfig":
        """Parse TOML text (for tests and the check command)."""
        try:
            raw = tomllib.loads(text)
            sha = sha256_hex(raw)
        except tomllib.TOMLDecodeError as e:
            return cls(path, load_error=f"agents.toml TOML syntax: {e}")
        except (TypeError, ValueError) as e:      # e.g. a naive TOML datetime
            return cls(path, load_error=f"agents.toml unsupported value: {e}")
        return cls(path, raw=raw, sha256=sha)

    # ------------------------------------------------------------ accessors

    def _section(self, key: str, model: type[T], data: Any, *, missing_ok: bool = False) -> T | ConfigProblem:
        if self.load_error is not None:
            return ConfigProblem(key, self.load_error)
        if data is None:
            if missing_ok:
                data = {}
            else:
                return ConfigProblem(key, "missing")
        if not isinstance(data, dict):
            return ConfigProblem(key, f"must be a table, got {type(data).__name__}")
        hole = unfilled_key(data, key)
        if hole:
            return ConfigProblem(hole, "unfilled placeholder from agents.example.toml")
        try:
            return model.model_validate(data)
        except ValidationError as e:
            loc, msg = _first_error(e)
            return ConfigProblem(f"{key}.{loc}" if loc else key, msg)

    def _agent(self, agent: str) -> dict | ConfigProblem:
        if self.load_error is not None:
            return ConfigProblem(f"agents.{agent}", self.load_error)
        if agent not in AGENTS:
            return ConfigProblem(f"agents.{agent}", f"unknown agent; expected one of {list(AGENTS)}")
        table = self.raw.get("agents", {})
        if not isinstance(table, dict):
            return ConfigProblem("agents", "must be a table")
        sec = table.get(agent)
        if sec is None:
            return ConfigProblem(f"agents.{agent}", "missing")
        if not isinstance(sec, dict):
            return ConfigProblem(f"agents.{agent}", "must be a table")
        return sec

    def enabled(self, agent: str) -> bool | ConfigProblem:
        """agents.<name>.enabled (required, a bool)."""
        sec = self._agent(agent)
        if isinstance(sec, ConfigProblem):
            return sec
        allowed = {"enabled", *ROUND_KINDS} if agent in LLM_AGENTS else {"enabled"}
        extra = sorted(set(sec) - allowed)
        if extra:
            return ConfigProblem(f"agents.{agent}.{extra[0]}", "unknown key")
        v = sec.get("enabled")
        if isinstance(v, str) and UNFILLED.fullmatch(v.strip()):
            return ConfigProblem(f"agents.{agent}.enabled", "unfilled placeholder from agents.example.toml")
        if not isinstance(v, bool):
            return ConfigProblem(f"agents.{agent}.enabled", "missing" if v is None else "must be true or false")
        return v

    def call(self, agent: str, kind: RoundKind) -> CallSettings | ConfigProblem:
        """The call settings of an LLM agent for round 1 or the discussion."""
        if agent not in LLM_AGENTS:
            return ConfigProblem(f"agents.{agent}", "not an LLM agent; it has no call settings")
        if kind not in ROUND_KINDS:
            return ConfigProblem(f"agents.{agent}.{kind}", f"unknown round kind; expected {list(ROUND_KINDS)}")
        on = self.enabled(agent)
        if isinstance(on, ConfigProblem):
            return on
        if not on:
            return ConfigProblem(f"agents.{agent}.enabled", "disabled in config")
        sec = self._agent(agent)
        assert isinstance(sec, dict)
        return self._section(f"agents.{agent}.{kind}", CallSettings, sec.get(kind))

    def discussion(self) -> DiscussionSettings | ConfigProblem:
        """[discussion]: optional; omitted keys take the owner's defaults."""
        return self._section("discussion", DiscussionSettings, self.raw.get("discussion"), missing_ok=True)

    def meta(self) -> MetaSettings | ConfigProblem:
        return self._section("meta", MetaSettings, self.raw.get("meta"))

    def llm(self) -> LLMSettings | ConfigProblem:
        return self._section("llm", LLMSettings, self.raw.get("llm"))

    def budget(self) -> BudgetSettings | ConfigProblem:
        return self._section("budget", BudgetSettings, self.raw.get("budget"))

    def bars_rows(self, tf: str) -> int | ConfigProblem:
        """render.bars_rows.<tf>: how many complete bars {{bars_table:<tf>}} shows."""
        key = f"render.bars_rows.{tf}"
        if self.load_error is not None:
            return ConfigProblem(key, self.load_error)
        if tf not in TIMEFRAMES:
            return ConfigProblem(key, f"unknown timeframe; expected one of {list(TIMEFRAMES)}")
        render = self.raw.get("render", {})
        rows = render.get("bars_rows", {}) if isinstance(render, dict) else None
        if not isinstance(rows, dict):
            return ConfigProblem("render.bars_rows", "must be a table")
        v = rows.get(tf)
        if v is None:
            return ConfigProblem(key, "missing")
        if isinstance(v, str) and UNFILLED.fullmatch(v.strip()):
            return ConfigProblem(key, "unfilled placeholder from agents.example.toml")
        if isinstance(v, bool) or not isinstance(v, int) or not 1 <= v <= 2000:
            return ConfigProblem(key, "must be an int in 1..2000")
        return v

    # ------------------------------------------------------------ whole file

    def problems(self) -> list[ConfigProblem]:
        """Every problem in the file, for `python -m agents.prompting check`.
        The run itself only ever asks for the sections it needs."""
        if self.load_error is not None:
            return [ConfigProblem("agents.toml", self.load_error)]
        out = [ConfigProblem(k, "unknown section") for k in sorted(set(self.raw) - set(SECTIONS))]
        agents = self.raw.get("agents", {})
        if isinstance(agents, dict):
            out += [ConfigProblem(f"agents.{a}", "unknown agent") for a in sorted(set(agents) - set(AGENTS))]
        checks: list[Any] = [self.discussion(), self.meta(), self.llm(), self.budget()]
        for a in AGENTS:
            on = self.enabled(a)
            checks.append(on)
            if a in LLM_AGENTS and on is True:
                checks += [self.call(a, k) for k in ROUND_KINDS]
        render = self.raw.get("render", {})
        rows = render.get("bars_rows", {}) if isinstance(render, dict) else {}
        for tf in (rows if isinstance(rows, dict) else {}):
            checks.append(self.bars_rows(tf))
        out += [c for c in checks if isinstance(c, ConfigProblem)]
        return out

    def canonical(self) -> str | None:
        """The canonical JSON whose SHA-256 is `sha256`."""
        return None if self.load_error is not None else canonical_json(self.raw)


__all__ = ["AGENTS", "BudgetSettings", "CODE_AGENTS", "CallSettings", "ConfigProblem", "DiscussionSettings",
           "ENV_CONFIG", "LLMSettings", "LLM_AGENTS", "MetaSettings", "OwnerConfig", "ROUND_KINDS"]
