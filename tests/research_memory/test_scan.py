"""scan.py: every secret rule, price and calendar rows, prose that must pass, masking, the real corpus.

Secret-shaped values are assembled at runtime so no literal sits in the repo.
"""

import subprocess
from pathlib import Path

import pytest

from tools.research_memory.chunk import apply_skips
from tools.research_memory.corpus import Document, Rules, tracked_files
from tools.research_memory.scan import scan_document, scan_rows, scan_secrets

ROOT = Path(__file__).resolve().parents[2]

SECRETS = {
    "anthropic_key": "key: " + "sk-" + "ant-" + "api03-" + "Ab1" * 10,
    "openai_style_key": "sk-" + "Zq9" * 12,
    "aws_access_key": "id " + "AK" + "IA" + "QWERTYUIOPASDFGH",
    "github_token": "gh" + "p_" + "a1B2" * 9,
    "slack_token": "xo" + "xb-" + "1234567890-abcdef",
    "private_key": "-----BEGIN " + "RSA PRIVATE KEY" + "-----",
    "secret_assignment": "api" + "_key = " + "q8Zr2LmN0pXv7Tc4",
    "telegram_bot_token": "123456789" + ":" + "A" * 35,
    "ec2_hostname": "ec2-" + "3-120-45-67" + ".eu-central-1.compute.amazonaws.com",
    "email": "owner" + "@" + "example.org",
    "ipv4_address": "host " + "52.28." + "14.9",
}


@pytest.mark.parametrize("rule", sorted(SECRETS))
def test_each_secret_rule_fires_and_masks(rule):
    line = SECRETS[rule]
    hits = [h for h in scan_secrets("x.md", f"before\n{line}\nafter\n") if h.rule == rule]
    assert hits and hits[0].line == 2
    assert "<masked:" in hits[0].excerpt


def test_masked_excerpt_never_contains_the_value():
    value = "sk-" + "ant-" + "Q" * 30
    (hit,) = [h for h in scan_secrets("x.md", f"token here {value} end") if h.rule == "anthropic_key"]
    assert value not in str(hit) and "Q" * 30 not in str(hit)


@pytest.mark.parametrize("text", [
    "Redis on 127.0.0.1, bound to 0.0.0.0:2024.",
    "ib_insync 0.9.86 and version 1.2.3.4567 (not an address: 1.2.3.999).",
    "SHA-256 `ca54f914b2236b80c6a711f66d2e9a647349acce9a3668f3b618c346925342f5`.",
    "Set `api_key = <your key>` in .env; never commit it.",
    "scp -i ~/Downloads/key.pem ubuntu@<ip>:/tmp/",
])
def test_prose_that_is_not_a_secret(text):
    assert scan_secrets("x.md", text) == []


def test_price_row_with_timestamp():
    hits = scan_rows("x.md", "| 2024-01-02 10:00 | 2051.20 | 2052.10 | 2050.90 | 2051.50 |\n")
    assert [h.rule for h in hits] == ["price_row"]
    assert "2051" not in hits[0].excerpt


def test_price_csv_row():
    assert [h.rule for h in scan_rows("x.md", "2024-01-02,2051.2,2052.1,2050.9,2051.5,120\n")] == ["price_row_csv"]


def test_calendar_row_and_run_of_dated_rows():
    assert [h.rule for h in scan_rows("x.md", "2024-01-05 13:30 USD High Non-Farm Payrolls 256K 164K\n")] == [
        "calendar_row"]
    table = "| Date | n |\n|---|---|\n| 2024-01-02 | 1 |\n| 2024-01-03 | 2 |\n| 2024-01-04 | 3 |\n"
    assert [h.rule for h in scan_rows("x.md", table)] == ["calendar_rows_run"]


@pytest.mark.parametrize("text", [
    # one timestamp in a decision row
    "| N8 | The unscheduled FOMC actions (2020-03-03 10:00 NY, 2020-03-15 17:00 NY) stay in. | x |",
    # a run log in bullets
    "- Attempt 1: 2026-10-22 10:43 UTC, exit 1.\n- Attempt 2: 2026-10-22 11:40 UTC, exit 4.\n"
    "- Attempt 3: 2026-10-22 11:43 UTC, exit 0, used.",
    # percentages next to a timestamp
    "| 2026-10-22T11:43:43.820Z | 97.8% | 1.1% | 0.4% |",
    # prose starting with a date
    "2024-01-01 was used** (the last row is 2023-12-31 23:59, the bars, the frame, the samples)",
    # two dated rows only
    "| 2024-01-02 | a |\n| 2024-01-03 | b |",
])
def test_rows_that_are_not_price_or_calendar_data(text):
    assert scan_rows("x.md", text) == []


def test_scan_document_combines_both():
    text = "contact " + "a" + "@" + "b.io" + "\n| 2024-01-02 10:00 | 1.10 | 1.20 | 1.30 |\n"
    assert sorted(h.rule for h in scan_document("x.md", text)) == ["email", "price_row"]


def test_real_corpus_scans_clean():
    """Every document the real corpus.toml selects, at HEAD, after its skipped sections. (The status
    map is not checked here: `build` refuses an unmapped file, so a PR adding a document stays green.)"""
    rules = Rules.load()
    hits = []
    for path in rules.select(tracked_files(ROOT)):
        text = subprocess.run(["git", "-C", str(ROOT), "show", f"HEAD:{path}"], check=True, capture_output=True,
                              text=True, encoding="utf-8").stdout
        d = apply_skips(Document(path, text, "reference", "x", "x", ""), rules.skips)
        hits += scan_document(d.path, d.text)
    assert hits == [], "\n".join(map(str, hits))
