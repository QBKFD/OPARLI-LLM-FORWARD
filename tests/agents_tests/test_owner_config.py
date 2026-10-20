"""agents.owner_config: the owner's agents.toml. Per-section validation, no
code defaults (bar the owner's discussion defaults), problems never raise."""

from pathlib import Path

import pytest

from agents.owner_config import (AGENTS, CallSettings, ConfigProblem, DiscussionSettings, OwnerConfig,
                                 unfilled_key)

HERE = Path(__file__).resolve().parent
OK = (HERE / "owner_config_ok.toml").read_text()
EXAMPLE = HERE.parents[1] / "agents" / "agents.example.toml"


def cfg(text=OK):
    return OwnerConfig.from_text(text, "agents.toml")


def reason(x):
    assert isinstance(x, ConfigProblem), x
    return x.reason


def test_a_valid_file():
    c = cfg()
    assert c.load_error is None and len(c.sha256) == 64
    assert c.call("visual", "round1") == CallSettings(model="claude-opus-5-5", effort="medium", max_tokens=16000)
    assert c.discussion() == DiscussionSettings(rounds=3, early_stop=True)
    assert c.meta().rule == "quorum_v0"
    assert (c.llm().mode, c.llm().timeout_s, c.llm().max_retries) == ("rerun", 30.0, 2)
    assert c.budget().max_calls_per_day == 40
    assert c.bars_rows("15m") == 8
    assert c.problems() == []


def test_round1_and_discussion_settings_are_separate():
    """The owner can give an agent a different model and effort per round."""
    c = cfg()
    r1, disc = c.call("visual", "round1"), c.call("visual", "discussion")
    assert (r1.model, r1.effort) == ("claude-opus-5-5", "medium")
    assert (disc.model, disc.effort, disc.max_tokens) == ("claude-sonnet-5-5", "low", 8000)


def test_the_only_defaults_are_the_owners_discussion_defaults():
    c = cfg(OK.replace("[discussion]\nrounds = 3\n", ""))
    assert c.discussion() == DiscussionSettings(rounds=2, early_stop=True)
    for missing in ("[meta]\nrule = \"quorum_v0\"\n", "[llm]\nmode = \"rerun\"\ntimeout_s = 30\nmax_retries = 2\n"):
        c = cfg(OK.replace(missing, ""))
        assert reason(c.meta() if "meta" in missing else c.llm()).endswith(": missing")


def test_missing_and_unreadable_files(tmp_path):
    c = OwnerConfig.load(env={})
    assert c.sha256 is None and "AGENTS_CONFIG is not set" in c.load_error
    assert reason(c.call("visual", "round1")).startswith("config_invalid: agents.visual: no config file")
    assert reason(OwnerConfig.load(tmp_path / "none.toml").meta()).endswith("agents.toml not found")
    (tmp_path / "bin.toml").write_bytes(b"\xff\xfe")
    assert "unreadable" in OwnerConfig.load(tmp_path / "bin.toml").load_error
    (tmp_path / "env.toml").write_text(OK)
    assert OwnerConfig.load(env={"AGENTS_CONFIG": str(tmp_path / "env.toml")}).sha256 == cfg().sha256


def test_toml_errors_are_problems_not_exceptions():
    bad = cfg("[meta\nrule = 1")
    assert "TOML syntax" in bad.load_error
    assert bad.problems() == [ConfigProblem("agents.toml", bad.load_error)]
    naive = cfg("[meta]\nrule = 1979-05-27T07:32:00\n")          # a naive TOML datetime
    assert "unsupported value" in naive.load_error


def test_the_hash_ignores_formatting_and_comments_but_not_values():
    a = cfg()
    b = cfg("# a comment\n" + OK.replace('rule = "quorum_v0"', 'rule   =   "quorum_v0"   # same'))
    assert a.sha256 == b.sha256
    assert cfg(OK.replace("max_calls_per_day = 40", "max_calls_per_day = 41")).sha256 != a.sha256


@pytest.mark.parametrize("model, why", [
    ("claude-opus-5-5-latest", "alias"),
    ("claude-opus-5-5-20260401", "date suffix"),
    ("Claude-Opus-5-5", "not an exact model id"),
    ("gpt-5", "not an exact model id"),
    ("claude-", "not an exact model id"),
])
def test_bad_model_ids(model, why):
    c = cfg(OK.replace('model = "claude-opus-5-5"', f'model = "{model}"'))
    assert why in reason(c.call("visual", "round1"))
    assert c.call("visual", "discussion").model == "claude-sonnet-5-5"     # the other block is untouched


@pytest.mark.parametrize("old, new, key", [
    ('effort = "medium"', 'effort = "extreme"', "agents.visual.round1.effort"),
    ("max_tokens = 16000", "max_tokens = 0", "agents.visual.round1.max_tokens"),
    ("max_tokens = 16000", "max_tokens = 200000", "agents.visual.round1.max_tokens"),
    ("max_tokens = 16000", 'max_tokens = "16000"', "agents.visual.round1.max_tokens"),   # strict: no coercion
    ("max_tokens = 16000", "max_tokens = true", "agents.visual.round1.max_tokens"),
    ("max_tokens = 16000", "max_tokens = 16000\ntemperature = 0", "agents.visual.round1.temperature"),
])
def test_bad_call_values_name_their_key(old, new, key):
    c = cfg(OK.replace(old, new, 1))
    assert reason(c.call("visual", "round1")).startswith(f"config_invalid: {key}:")


def test_missing_blocks_and_agents():
    no_disc = OK.replace('[agents.visual.discussion]\nmodel = "claude-sonnet-5-5"\neffort = "low"\nmax_tokens = 8000\n',
                         "")
    c = cfg(no_disc)
    assert reason(c.call("visual", "discussion")) == "config_invalid: agents.visual.discussion: missing"
    assert isinstance(c.call("visual", "round1"), CallSettings)
    assert reason(cfg().call("sentiment", "round1")) == "config_invalid: agents.sentiment.enabled: disabled in config"
    assert reason(cfg(OK.replace("[agents.visual]\nenabled = true", "[agents.visual]"))
                  .call("visual", "round1")) == "config_invalid: agents.visual.enabled: missing"
    assert "must be true or false" in reason(cfg(OK.replace("[agents.gex]\nenabled = true",
                                                            '[agents.gex]\nenabled = "yes"')).enabled("gex"))
    assert reason(cfg("").enabled("visual")) == "config_invalid: agents.visual: missing"


def test_unknown_keys_agents_and_sections():
    c = cfg(OK + "\n[agents.visual2]\nenabled = true\n[sizing]\nk = 1\n")
    keys = {p.key for p in c.problems()}
    assert {"agents.visual2", "sizing"} <= keys
    assert "unknown agent" in reason(c.enabled("visual2"))
    extra = cfg(OK.replace("[agents.gex]\nenabled = true", "[agents.gex]\nenabled = true\nmodel = \"x\""))
    assert reason(extra.enabled("gex")) == "config_invalid: agents.gex.model: unknown key"


def test_code_agents_have_no_call_settings():
    assert "not an LLM agent" in reason(cfg().call("gex", "round1"))
    assert "unknown round kind" in reason(cfg().call("visual", "round2"))


def test_discussion_bounds():
    assert "discussion.rounds" in reason(cfg(OK.replace("rounds = 3", "rounds = 0")).discussion())
    assert "discussion.rounds" in reason(cfg(OK.replace("rounds = 3", "rounds = 11")).discussion())
    assert "discussion.early_stop" in reason(cfg(OK.replace("rounds = 3", "early_stop = 1")).discussion())


def test_bars_rows():
    c = cfg()
    assert reason(c.bars_rows("4h")) == "config_invalid: render.bars_rows.4h: missing"
    assert "unknown timeframe" in reason(c.bars_rows("5m"))
    assert "1..2000" in reason(cfg(OK.replace('"15m" = 8', '"15m" = 0')).bars_rows("15m"))
    assert "1..2000" in reason(cfg(OK.replace('"15m" = 8', '"15m" = true')).bars_rows("15m"))
    assert "must be a table" in reason(cfg('render = 1').bars_rows("1h"))


def test_unfilled_placeholders_are_refused():
    assert unfilled_key({"a": {"b": ["x", "<y>"]}}, "s") == "s.a.b.1"
    assert unfilled_key({"a": "x<y>"}, "s") is None
    c = cfg(OK.replace('rule = "quorum_v0"', 'rule = "<a Meta rule name>"'))
    assert reason(c.meta()) == "config_invalid: meta.rule: unfilled placeholder from agents.example.toml"


def test_the_example_template_has_every_key_and_no_usable_value():
    """agents.example.toml is a template: every owner value is a placeholder,
    so a copy that isn't filled in gives problems, never a working agent."""
    ex = OwnerConfig.load(EXAMPLE)
    assert ex.load_error is None
    assert set(ex.raw) == {"discussion", "meta", "llm", "budget", "render", "agents"}
    assert set(ex.raw["agents"]) == set(AGENTS)
    for a in ("visual", "technical", "sentiment"):
        assert set(ex.raw["agents"][a]) == {"enabled", "round1", "discussion"}
        assert all(isinstance(ex.call(a, k), ConfigProblem) for k in ("round1", "discussion"))
    for section in (ex.meta(), ex.llm(), ex.budget()):
        assert "unfilled placeholder" in reason(section)
    assert all(isinstance(ex.enabled(a), ConfigProblem) for a in AGENTS)
    assert ex.discussion() == DiscussionSettings()                # the owner's defaults, commented in the file
    filled = EXAMPLE.read_text()
    for k in ("model", "effort", "max_tokens"):                   # the template's keys are the schema's keys
        assert f"\n{k} = " in filled
    assert set(CallSettings.model_fields) == {"model", "effort", "max_tokens"}
