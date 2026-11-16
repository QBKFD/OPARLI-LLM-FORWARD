"""The owner's research_memory.toml and the owner's answer prompt.

Like agents.toml (backend/agents/owner_config.py): the owner sets every
value, code supplies no default model, effort, max_tokens, timeout, retries,
budget or prompt. A missing file, a "<...>" placeholder left in, or an
invalid value is a problem; the graph abstains with `problem.reason` and
never falls back. `effort = "default"` sends no effort (the model's default).

The path is $RESEARCH_MEMORY_CONFIG, else tools/research_memory/research_memory.toml.
See config.example.toml. The prompt is the file at `answer.prompt_path`
(relative to the repo root); HTML comments are stripped and the rest is the
system prompt, sent verbatim. prompt.template.md is the empty template.
"""

from __future__ import annotations

import hashlib
import os
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from backend.agents.owner_config import DATE_SUFFIX, MAX_TOKENS_CAP, MODEL_ID, BudgetSettings, LLMSettings, \
    unfilled_key

REPO = Path(__file__).resolve().parents[2]
ENV_CONFIG = "RESEARCH_MEMORY_CONFIG"
DEFAULT_CONFIG = Path(__file__).with_name("research_memory.toml")
_COMMENT = re.compile(r"<!--.*?-->", re.S)
_PLACEHOLDER = re.compile(r"<[^<>\n]*owner[^<>\n]*>", re.I)


class _Strict(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, allow_inf_nan=False)


class AnswerSettings(_Strict):
    provider: Literal["anthropic", "ollama"]
    model: str = Field(min_length=1)
    effort: Literal["default", "low", "medium", "high", "xhigh", "max"]
    max_tokens: int = Field(ge=1, le=MAX_TOKENS_CAP)
    prompt_path: str = Field(min_length=1)

    @model_validator(mode="after")
    def _model_id(self) -> "AnswerSettings":
        m = self.model
        if self.provider == "anthropic" and (not MODEL_ID.fullmatch(m) or m.endswith("-latest")
                                             or DATE_SUFFIX.search(m)):
            raise ValueError(f"{m!r} is not an exact Claude model id (no alias, no date suffix; e.g. claude-sonnet-5-5)")
        return self


class OllamaSettings(_Strict):
    url: str = Field(pattern=r"^https?://")


class PathSettings(_Strict):
    ledger: str = "database/research_memory_llm.sqlite"     # infrastructure, not an owner value
    env_file: str = ".env"


@dataclass(frozen=True)
class Problem:
    key: str
    message: str

    @property
    def reason(self) -> str:
        return f"config_invalid: {self.key}: {self.message}"


@dataclass(frozen=True)
class MemoryConfig:
    path: Path
    answer: AnswerSettings
    llm: LLMSettings
    budget: BudgetSettings
    ollama: OllamaSettings | None
    paths: PathSettings
    sha256: str


def config_path(path: str | Path | None = None, env: dict[str, str] | None = None) -> Path:
    env = dict(os.environ) if env is None else env
    return Path(path or env.get(ENV_CONFIG) or DEFAULT_CONFIG)


def load_config(path: str | Path | None = None, env: dict[str, str] | None = None) -> MemoryConfig | Problem:
    p = config_path(path, env)
    try:
        raw = p.read_bytes()
        data = tomllib.loads(raw.decode("utf-8"))
    except FileNotFoundError:
        return Problem("file", f"{p} not found (copy tools/research_memory/config.example.toml and fill it in)")
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as e:
        return Problem("file", f"{p}: {type(e).__name__}: {e}")
    hole = unfilled_key(data, "research_memory")
    if hole:
        return Problem(hole.removeprefix("research_memory."), "still a <...> placeholder")
    unknown = sorted(set(data) - {"answer", "llm", "budget", "ollama", "paths"})
    if unknown:
        return Problem(unknown[0], "unknown section")
    sections: dict[str, object] = {}
    for key, model in (("answer", AnswerSettings), ("llm", LLMSettings), ("budget", BudgetSettings)):
        if key not in data:
            return Problem(key, "section missing")
        try:
            sections[key] = model.model_validate(data[key])
        except ValidationError as e:
            err = e.errors()[0]
            loc = ".".join(map(str, err["loc"]))
            return Problem(f"{key}.{loc}" if loc else key, err["msg"])
    try:
        ollama = OllamaSettings.model_validate(data["ollama"]) if "ollama" in data else None
        paths = PathSettings.model_validate(data.get("paths", {}))
    except ValidationError as e:
        err = e.errors()[0]
        return Problem(".".join(map(str, err["loc"])) or "ollama/paths", err["msg"])
    answer = sections["answer"]
    assert isinstance(answer, AnswerSettings)
    if answer.provider == "ollama" and ollama is None:
        return Problem("ollama", "section missing (answer.provider = \"ollama\")")
    return MemoryConfig(path=p, answer=answer, llm=sections["llm"], budget=sections["budget"],  # type: ignore[arg-type]
                        ollama=ollama, paths=paths, sha256=hashlib.sha256(raw).hexdigest())


@dataclass(frozen=True)
class Prompt:
    path: Path
    text: str           # the system prompt as sent
    sha256: str         # of the file's bytes


def load_prompt(cfg: MemoryConfig, repo: Path = REPO) -> Prompt | Problem:
    p = Path(cfg.answer.prompt_path)
    p = p if p.is_absolute() else repo / p
    try:
        raw = p.read_bytes()
        text = raw.decode("utf-8")
    except FileNotFoundError:
        return Problem("prompt", f"{p} not found (the owner's prompt; template: prompt.template.md)")
    except UnicodeDecodeError as e:
        return Problem("prompt", f"{p}: not UTF-8 ({e})")
    body = _COMMENT.sub("", text).strip()
    if not body:
        return Problem("prompt", f"{p} is empty")
    if _PLACEHOLDER.search(body):
        return Problem("prompt", f"{p} still holds the template's <owner: ...> placeholder")
    return Prompt(path=p, text=body, sha256=hashlib.sha256(raw).hexdigest())


def load_env_key(env_file: Path, name: str = "ANTHROPIC_API_KEY") -> bool:
    """Put `name` from a .env file into os.environ unless it is already set.
    Reads that one key only; never logs or returns its value."""
    if os.environ.get(name):
        return True
    try:
        lines = env_file.read_text(encoding="utf-8").splitlines()
    except (FileNotFoundError, UnicodeDecodeError):
        return False
    for line in lines:
        k, sep, v = line.strip().removeprefix("export ").partition("=")
        if sep and k.strip() == name:
            v = v.strip().strip("'\"")
            if v:
                os.environ[name] = v
                return True
    return False


__all__ = ["AnswerSettings", "DEFAULT_CONFIG", "ENV_CONFIG", "MemoryConfig", "OllamaSettings", "PathSettings",
           "Problem", "Prompt", "config_path", "load_config", "load_env_key", "load_prompt"]
