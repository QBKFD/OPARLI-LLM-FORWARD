"""config.py: the owner's research_memory.toml and prompt. No defaults; every gap is a Problem."""

import os
from pathlib import Path

import pytest

from tests.research_memory.helpers import CONFIG, write_config
from tools.research_memory.config import Problem, load_config, load_env_key, load_prompt

PKG = Path(__file__).resolve().parents[2] / "tools" / "research_memory"


def test_valid_config(tmp_path):
    cfg = load_config(write_config(tmp_path))
    assert not isinstance(cfg, Problem)
    assert cfg.answer.model == "claude-sonnet-5-5" and cfg.answer.effort == "default"
    assert cfg.llm.mode == "live" and cfg.budget.max_calls_per_day == 100
    assert cfg.paths.ledger == "database/research_memory_llm.sqlite" and len(cfg.sha256) == 64


def test_missing_file(tmp_path):
    p = load_config(tmp_path / "nope.toml")
    assert isinstance(p, Problem) and p.reason.startswith("config_invalid: file:")


def test_env_var_selects_the_file(tmp_path):
    path = write_config(tmp_path)
    cfg = load_config(None, env={"RESEARCH_MEMORY_CONFIG": str(path)})
    assert not isinstance(cfg, Problem) and cfg.path == path


def test_the_shipped_example_is_never_usable():
    p = load_config(PKG / "config.example.toml")
    assert isinstance(p, Problem) and "placeholder" in p.message


@pytest.mark.parametrize("old,new,key", [
    ('model = "claude-sonnet-5-5"', 'model = "claude-sonnet-latest"', "answer"),
    ('model = "claude-sonnet-5-5"', 'model = "claude-sonnet-5-5-20261101"', "answer"),
    ('effort = "default"', 'effort = "turbo"', "answer.effort"),
    ("max_tokens = 2000", "max_tokens = 0", "answer.max_tokens"),
    ('mode = "live"', 'mode = "sometimes"', "llm.mode"),
    ("max_calls_per_day = 100\n", "", "budget.max_calls_per_day"),
    ('provider = "anthropic"', 'provider = "ollama"', "ollama"),
])
def test_invalid_values(tmp_path, old, new, key):
    p = load_config(write_config(tmp_path, CONFIG.replace(old, new)))
    assert isinstance(p, Problem) and p.key.startswith(key), p


def test_missing_section_and_unknown_section(tmp_path):
    p = load_config(write_config(tmp_path, CONFIG.split("[budget]")[0]))
    assert isinstance(p, Problem) and p.key == "budget"
    p = load_config(write_config(tmp_path, CONFIG + "\n[extra]\nx = 1\n"))
    assert isinstance(p, Problem) and p.key == "extra"


def test_ollama_config(tmp_path):
    text = CONFIG.replace('provider = "anthropic"', 'provider = "ollama"').replace(
        'model = "claude-sonnet-5-5"', 'model = "llama3.1:8b"') + '\n[ollama]\nurl = "http://127.0.0.1:11434"\n'
    cfg = load_config(write_config(tmp_path, text))
    assert not isinstance(cfg, Problem) and cfg.ollama is not None and cfg.answer.model == "llama3.1:8b"


def test_prompt_is_read_verbatim_without_comments(tmp_path):
    cfg = load_config(write_config(tmp_path, prompt="<!-- note -->\nAnswer only from chunks.\n<!-- end -->\n"))
    p = load_prompt(cfg, repo=tmp_path)
    assert not isinstance(p, Problem) and p.text == "Answer only from chunks." and len(p.sha256) == 64


@pytest.mark.parametrize("prompt,match", [(None, "not found"), ("<!-- only a comment -->", "empty")])
def test_missing_or_empty_prompt(tmp_path, prompt, match):
    cfg = load_config(write_config(tmp_path, prompt=prompt))
    p = load_prompt(cfg, repo=tmp_path)
    assert isinstance(p, Problem) and p.key == "prompt" and match in p.message


def test_the_shipped_template_is_never_usable(tmp_path):
    cfg = load_config(write_config(tmp_path, prompt=(PKG / "prompt.template.md").read_text()))
    p = load_prompt(cfg, repo=tmp_path)
    assert isinstance(p, Problem) and "placeholder" in p.message


def test_env_key_from_file_does_not_override(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("# c\nOTHER=1\nexport ANTHROPIC_API_KEY='from-file'\n", encoding="utf-8")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert load_env_key(env) and os.environ["ANTHROPIC_API_KEY"] == "from-file"
    monkeypatch.setenv("ANTHROPIC_API_KEY", "from-env")
    assert load_env_key(env) and os.environ["ANTHROPIC_API_KEY"] == "from-env"
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    assert not load_env_key(tmp_path / "missing.env") and "ANTHROPIC_API_KEY" not in os.environ
