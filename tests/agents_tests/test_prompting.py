"""agents.prompting.files and placeholders: parsing, loading and the check
command. Every failure is a PromptProblem with a reason, never a fallback."""

import hashlib
from pathlib import Path

import pytest

from agents.prompting import files as pf
from agents.prompting.__main__ import main as check_main
from agents.prompting.placeholders import find_placeholders, is_discussion, is_round1, MalformedPlaceholder

ROUND1 = """---
agent: visual
version: 2026-10-20.1
description: a note for people
---
<!-- a note before the first section -->
## system

You read charts.
<!-- a note inside system -->

## user

Alert: {{alert_json}}
{{charts}}
Bars: {{bars_table:15m}} and again {{alert_json}}
"""

DISCUSSION = """---
agent: discussion
version: 1
---
## user

Round {{round}} of {{max_rounds}}. You said {{own_previous_vote_json}}; others: {{other_votes_json}}.
{{change_rule}}
"""


def parse(text, name="visual"):
    return pf.parse_prompt(text.encode(), name)


def invalid(text, name="visual"):
    with pytest.raises(pf.PromptInvalid) as e:
        parse(text, name)
    return str(e.value)


def test_a_round1_file_parses():
    p = parse(ROUND1)
    assert (p.name, p.kind, p.version, p.description) == ("visual", "round1", "2026-10-20.1", "a note for people")
    assert p.system == "You read charts."                      # comment stripped, blank lines trimmed
    assert p.user.startswith("Alert: {{alert_json}}") and p.user.endswith("{{alert_json}}")
    assert p.placeholders == ("alert_json", "charts", "bars_table:15m")   # first use, no repeats
    assert p.sha256 == hashlib.sha256(ROUND1.encode()).hexdigest()


def test_a_discussion_file_parses():
    p = parse(DISCUSSION, "discussion")
    assert p.kind == "discussion" and p.system is None
    assert p.placeholders == ("round", "max_rounds", "own_previous_vote_json", "other_votes_json", "change_rule")


def test_the_hash_covers_comments_too():
    a, b = parse(ROUND1), parse(ROUND1.replace("a note inside system", "another note"))
    assert a.system == b.system and a.sha256 != b.sha256


def test_placeholders_inside_comments_are_ignored():
    p = parse(ROUND1.replace("You read charts.", "You read charts. <!-- {{other_votes_json}} -->"))
    assert "other_votes_json" not in p.placeholders


@pytest.mark.parametrize("text, why", [
    ("agent: visual\n## system\nx\n## user\ny\n", "must start with a '---'"),
    ("---\nagent: visual\nversion: 1\n## system\nx\n## user\ny\n", "no closing '---'"),
    ("---\nagent visual\nversion: 1\n---\n## system\nx\n## user\ny\n", "bad front-matter line"),
    ("---\nagent: visual\nversion: 1\nmodel: x\n---\n## system\nx\n## user\ny\n", "unknown front-matter key 'model'"),
    ("---\nagent: visual\nagent: visual\nversion: 1\n---\n## system\nx\n## user\ny\n", "appears twice"),
    ("---\nagent: visual\nversion:\n---\n## system\nx\n## user\ny\n", "'version' is empty"),
    ("---\nagent: visual\n---\n## system\nx\n## user\ny\n", "'version' is missing"),
    ("---\nversion: 1\n---\n## system\nx\n## user\ny\n", "'agent' is missing"),
    ("---\nagent: technical\nversion: 1\n---\n## system\nx\n## user\ny\n", "doesn't match the file name"),
    ("---\nagent: visual\nversion: TEMPLATE\n---\n## system\nx\n## user\ny\n", "unfilled template"),
    ("---\nagent: visual\nversion: 1\n---\n## user\ny\n## system\nx\n", "in that order"),
    ("---\nagent: visual\nversion: 1\n---\n## system\nx\n", "in that order"),
    ("---\nagent: visual\nversion: 1\n---\n## system\nx\n## user\ny\n## user\nz\n", "in that order"),
    ("---\nagent: visual\nversion: 1\n---\nhello\n## system\nx\n## user\ny\n", "text before the first section"),
    ("---\nagent: visual\nversion: 1\n---\n<!-- open\n## system\nx\n## user\ny\n", "is not closed"),
    ("---\nagent: visual\nversion: 1\n---\n## system\nx <!-- open\n## user\ny\n", "is not closed"),
    ("---\nagent: visual\nversion: 1\n---\n## system\n<!-- only a note -->\n## user\ny\n", "## system is empty"),
    ("---\nagent: visual\nversion: 1\n---\n## system\nx\n## user\n\n", "## user is empty"),
    ("---\nagent: visual\nversion: 1\n---\n## system\nAt {{as_of}}\n## user\ny\n", "## system contains a placeholder"),
    ("---\nagent: visual\nversion: 1\n---\n## system\nx\n## user\n{{alerts_json}}\n", "unknown here"),
    ("---\nagent: visual\nversion: 1\n---\n## system\nx\n## user\n{{other_votes_json}}\n", "a discussion placeholder"),
    ("---\nagent: visual\nversion: 1\n---\n## system\nx\n## user\n{{bars_table:5m}}\n", "unknown here"),
    ("---\nagent: visual\nversion: 1\n---\n## system\nx\n## user\n{{ alert_json }}\n", "malformed placeholder"),
    ("---\nagent: visual\nversion: 1\n---\n## system\nx\n## user\n{{alert_json}\n", "malformed placeholder"),
    ("---\nagent: visual\nversion: 1\n---\n## system\nx\n## user\n{{Alert_JSON}}\n", "malformed placeholder"),
])
def test_invalid_round1_files(text, why):
    assert why in invalid(text)


@pytest.mark.parametrize("text, why", [
    ("---\nagent: discussion\nversion: 1\n---\n## system\nx\n## user\ny\n", "sections must be ## user"),
    ("---\nagent: discussion\nversion: 1\n---\n## user\n{{alert_json}}\n", "a round-1 placeholder"),
])
def test_invalid_discussion_files(text, why):
    assert why in invalid(text, "discussion")


def test_encoding_problems():
    with pytest.raises(pf.PromptInvalid, match="UTF-8"):
        pf.parse_prompt(b"\xff\xfe---", "visual")
    with pytest.raises(pf.PromptInvalid, match="byte-order mark"):
        pf.parse_prompt("﻿".encode() + ROUND1.encode(), "visual")


def test_an_unknown_prompt_name_is_a_programming_error():
    with pytest.raises(ValueError):
        pf.parse_prompt(ROUND1.encode(), "judge")
    with pytest.raises(ValueError):
        pf.load_prompt("judge")


def test_load_prompt_ok_missing_and_invalid(tmp_path):
    (tmp_path / "visual.md").write_text(ROUND1)
    (tmp_path / "technical.md").write_text(ROUND1)               # agent: visual inside technical.md
    ok = pf.load_prompt("visual", tmp_path)
    assert isinstance(ok, pf.PromptFile) and ok.path == str(tmp_path / "visual.md")
    missing = pf.load_prompt("sentiment", tmp_path)
    assert isinstance(missing, pf.PromptProblem) and missing.sha256 is None
    assert missing.reason == "prompt_invalid: sentiment.md: not found" and missing.path.endswith("sentiment.md")
    bad = pf.load_prompt("technical", tmp_path)
    assert isinstance(bad, pf.PromptProblem) and bad.sha256 == hashlib.sha256(ROUND1.encode()).hexdigest()
    assert "doesn't match the file name" in bad.reason


def test_load_prompt_unreadable(tmp_path):
    (tmp_path / "visual.md").mkdir()                            # a directory, not a file
    p = pf.load_prompt("visual", tmp_path)
    assert isinstance(p, pf.PromptProblem) and "unreadable" in p.reason


def test_load_all_has_every_prompt_name(tmp_path):
    assert list(pf.load_all(tmp_path)) == list(pf.PROMPT_NAMES) == ["visual", "technical", "sentiment", "gex",
                                                                    "discussion"]


def test_the_shipped_templates_are_refused_as_unfilled():
    """The templates are infrastructure: no file can reach a model until the
    owner writes it and sets a version."""
    for name, p in pf.load_all().items():
        assert isinstance(p, pf.PromptProblem), name
        assert "unfilled template" in p.reason, (name, p.reason)
        raw = (pf.PROMPTS_DIR / f"{name}.md").read_text()
        filled = raw.replace("version: TEMPLATE", "version: 1").replace(
            "<!-- the owner's instructions -->", "x").replace(
            "<!-- the per-alert message, with the placeholders you choose -->", "y").replace(
            "<!-- the discussion message, with the placeholders you choose -->", "y")
        pf.parse_prompt(filled.encode(), name)                  # only the version and the text were missing


def test_the_old_debate_prompts_are_untouched():
    """bull.md and bear.md are the owner's; this layer neither reads nor edits them."""
    assert {"bull.md", "bear.md"} <= {p.name for p in pf.PROMPTS_DIR.iterdir()}
    assert "bull" not in pf.PROMPT_NAMES and "bear" not in pf.PROMPT_NAMES


def test_find_placeholders():
    assert find_placeholders("a {{x}} b {{bars_table:1h}} {{x}}") == ["x", "bars_table:1h", "x"]
    assert find_placeholders("no braces here, a { single } is fine") == []
    for bad in ("{{}}", "{{ x}}", "{{x }}", "}} x", "{{x"):
        with pytest.raises(MalformedPlaceholder):
            find_placeholders(bad)


def test_round1_and_discussion_names_are_disjoint():
    assert is_round1("alert_json") and is_round1("bars_table:4h") and not is_round1("bars_table:2h")
    assert is_discussion("other_votes_json") and not is_round1("other_votes_json")
    assert not is_discussion("alert_json")


def test_check_command(tmp_path, capsys):
    cfg = tmp_path / "agents.toml"
    cfg.write_text(Path(__file__).with_name("owner_config_ok.toml").read_text())
    for name, text in (("visual", ROUND1), ("discussion", DISCUSSION)):
        (tmp_path / f"{name}.md").write_text(text)
    rc = check_main(["check", "--prompts", str(tmp_path), "--config", str(cfg)])
    out = capsys.readouterr().out
    assert rc == 1                                              # technical, sentiment, gex are missing
    assert "OK       visual.md" in out and "OK       discussion.md" in out
    assert "INVALID  prompt_invalid: technical.md: not found  (" in out
    assert "CONFIG   OK" in out and "epoch_id " in out
    for name in ("technical", "sentiment", "gex"):
        (tmp_path / f"{name}.md").write_text(ROUND1.replace("agent: visual", f"agent: {name}"))
    assert check_main(["check", "--prompts", str(tmp_path), "--config", str(cfg)]) == 0


def test_check_command_reports_config_problems(tmp_path, capsys):
    rc = check_main(["check", "--prompts", str(tmp_path), "--config", str(tmp_path / "none.toml")])
    assert rc == 1 and "CONFIG   config_invalid: agents.toml:" in capsys.readouterr().out
