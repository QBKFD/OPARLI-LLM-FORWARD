"""The build-time content scan: secrets, price rows and calendar rows.

Runs on every document before anything is chunked or embedded; any hit
fails the build. A hit reports the path, line and rule, and a masked excerpt:
the matched value itself is never printed.

Secrets: Anthropic / OpenAI-style keys, AWS access keys, GitHub and Slack
tokens, private-key blocks, `api_key = <value>`-style assignments, Telegram
bot tokens, IPv4 addresses other than loopback and 0.0.0.0, EC2 hostnames,
e-mail addresses.

Price rows: a line with a timestamp (YYYY-MM-DD HH:MM) and three or more
price-like decimals besides it (percentages excluded), or a CSV line that
starts with a date and has four or more numeric fields. Calendar rows: three or more
consecutive table or CSV rows whose first cell is a date/timestamp, or a line
with a timestamp, a currency code and an impact level. A single timestamp
quoted in prose (a decision, a run log) is neither.
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass

_SECRET_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("anthropic_key", re.compile(r"sk-ant-[A-Za-z0-9_\-]{16,}")),
    ("openai_style_key", re.compile(r"\bsk-[A-Za-z0-9]{32,}\b")),
    ("aws_access_key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b")),
    ("slack_token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}")),
    ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("secret_assignment", re.compile(
        r"(?i)\b(?:api[_-]?key|secret[_-]?key|access[_-]?token|auth[_-]?token|bot[_-]?token|password|passwd)"
        r"\b\s*[:=]\s*['\"]?(?!<)[A-Za-z0-9_\-/+.]{12,}")),
    ("telegram_bot_token", re.compile(r"\b\d{8,10}:[A-Za-z0-9_\-]{35}\b")),
    ("ec2_hostname", re.compile(r"(?i)\bec2-\d{1,3}-\d{1,3}-\d{1,3}-\d{1,3}\.[a-z0-9.\-]*amazonaws\.com\b")),
    ("email", re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}\b")),
)
_IPV4 = re.compile(r"(?<![\d.])(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})(?![\d.])")
_DATETIME = re.compile(r"\b(?:19|20)\d{2}-\d{2}-\d{2}[ T]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?Z?")
_DATE = re.compile(r"\b(?:19|20)\d{2}[-./]\d{2}[-./]\d{2}\b")
_PRICE = re.compile(r"(?<![\w.])\d{1,6}\.\d{1,6}(?![\w.%])(?!\s*(?:%|pp\b|x\b|σ))")
_CCY = re.compile(r"\b(?:USD|EUR|GBP|JPY|CHF|CAD|AUD|NZD|CNY)\b")
_IMPACT = re.compile(r"(?i)\b(?:high|medium|low)(?:\s+impact)?\b|\bholiday\b")
_FIRST_CELL_DATE = re.compile(r"^\s*\|?\s*(?:19|20)\d{2}[-./]\d{2}[-./]\d{2}")
_NUMBER_FIELD = re.compile(r"^\s*-?\d+(?:\.\d+)?\s*$")


@dataclass(frozen=True)
class Hit:
    path: str
    line: int
    rule: str
    excerpt: str            # masked

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: {self.rule}: {self.excerpt}"


def _mask(line: str, start: int, end: int) -> str:
    """The line around the match, the match itself replaced by <masked:N chars>."""
    lo, hi = max(0, start - 30), min(len(line), end + 30)
    return f"{line[lo:start]}<masked:{end - start} chars>{line[end:hi]}".strip()


def _public_ip(groups: tuple[str, ...]) -> bool:
    try:
        ip = ipaddress.IPv4Address(".".join(groups))
    except ValueError:
        return False                      # not an address (an octet > 255): a version number or similar
    return not (ip.is_loopback or ip.is_unspecified)


def scan_secrets(path: str, text: str) -> list[Hit]:
    hits = []
    for n, line in enumerate(text.splitlines(), 1):
        for rule, rx in _SECRET_RULES:
            for m in rx.finditer(line):
                hits.append(Hit(path, n, rule, _mask(line, m.start(), m.end())))
        for m in _IPV4.finditer(line):
            if _public_ip(m.groups()):
                hits.append(Hit(path, n, "ipv4_address", _mask(line, m.start(), m.end())))
    return hits


def _is_data_row(line: str) -> bool:
    """A table row or CSV line whose first cell is a date."""
    return bool(_FIRST_CELL_DATE.match(line)) and ("|" in line or line.count(",") >= 2)


def scan_rows(path: str, text: str) -> list[Hit]:
    hits = []
    lines = text.splitlines()
    run = 0
    for n, line in enumerate(lines, 1):
        dt = _DATETIME.search(line)
        if dt:
            rest = _DATETIME.sub(" ", line)
            if len(_PRICE.findall(rest)) >= 3:
                hits.append(Hit(path, n, "price_row", _mask(line, 0, len(line))))
            if _CCY.search(rest) and _IMPACT.search(rest):
                hits.append(Hit(path, n, "calendar_row", _mask(line, 0, len(line))))
        if _DATE.match(line.strip()) and sum(bool(_NUMBER_FIELD.match(f)) for f in line.split(",")) >= 4:
            hits.append(Hit(path, n, "price_row_csv", _mask(line, 0, len(line))))
        run = run + 1 if _is_data_row(line) else 0
        if run == 3:
            hits.append(Hit(path, n, "calendar_rows_run", f"3+ consecutive dated rows ending at line {n}"))
    return hits


def scan_document(path: str, text: str) -> list[Hit]:
    return scan_secrets(path, text) + scan_rows(path, text)


__all__ = ["Hit", "scan_document", "scan_rows", "scan_secrets"]
