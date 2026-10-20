"""Plain-text rendering, shared by --once and the TUI. Stdlib only.

Every line carries the status as a word (GREEN / YELLOW / RED / OFF), so it
reads the same with or without colour."""

from __future__ import annotations

from typing import Mapping, TextIO

from tools.status.model import ComponentHealth, Health, overall
from tools.status.reader import age_text, parse_ts

ANSI = {"green": "\033[32m", "yellow": "\033[33m", "red": "\033[31;1m", "off": "\033[2m"}
RESET = "\033[0m"
EXIT_CODES = {"green": 0, "yellow": 1, "red": 2}


def label(c: ComponentHealth) -> str:
    return "OFF" if not c.deployed else c.status.upper()


def use_color(stream: TextIO, env: Mapping[str, str], force: bool | None = None) -> bool:
    """Colour only on a terminal, never with NO_COLOR set (any value) or TERM=dumb."""
    if force is not None:
        return force
    if "NO_COLOR" in env or env.get("TERM") == "dumb":
        return False
    isatty = getattr(stream, "isatty", None)
    return bool(isatty and isatty())


def component_line(c: ComponentHealth, color: bool) -> str:
    word = f"{label(c):6}"
    if color:
        word = ANSI["off" if not c.deployed else c.status] + word + RESET
    return f"{word} {c.name:9} {c.reason}"


def header(h: Health, now: float) -> str:
    try:
        age = f"written {age_text(now - parse_ts(h.generated_ts))} ago"
    except ValueError:
        age = "written at an unknown time"
    head = f"OPARLI status: {overall(h).upper()} ({age}, {h.generated_ts})"
    return head + (f"\n!! {h.note}" if h.note else "")


def lines(h: Health, now: float, color: bool) -> list[str]:
    return [header(h, now)] + [component_line(c, color) for c in h.components]


def exit_code(h: Health) -> int:
    """0 all green, 1 something yellow, 2 something red (OFF components don't count)."""
    return EXIT_CODES[overall(h)]
