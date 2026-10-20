"""The Textual status board. Needs the `status` extra (textual==8.2.8); the
rest of tools/status doesn't.

Reads health.json every `interval` seconds (default 5). A missing, invalid or
stale file shows every component red with a banner (reader.load); an error
while refreshing is shown in the banner and the next refresh tries again, so
the board never exits on bad input. Textual honours NO_COLOR; the status is
always written as a word, too. Keys: q quit, r refresh now.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Callable

from rich.text import Text
from textual.app import App, ComposeResult
from textual.widgets import DataTable, Footer, Header, Static

from tools.status import reader, render
from tools.status.model import ComponentHealth, Health

STYLE = {"GREEN": "bold green", "YELLOW": "bold yellow", "RED": "bold red", "OFF": "dim"}


def status_cell(c: ComponentHealth) -> Text:
    word = render.label(c)
    return Text(word, style=STYLE[word])


class StatusApp(App[None]):
    TITLE = "OPARLI status"
    BINDINGS = [("q", "quit", "Quit"), ("r", "refresh", "Refresh now")]
    CSS = "#banner { padding: 0 1; } DataTable { height: 1fr; }"

    def __init__(self, path: str | Path, stale_after_s: int, interval: float = 5.0,
                 clock: Callable[[], float] = time.time) -> None:
        super().__init__()
        self.path, self.stale_after_s, self.interval, self.clock = path, stale_after_s, interval, clock
        self.last: Health | None = None
        self.refreshes = 0

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("", id="banner")
        yield DataTable(id="table", cursor_type="row", zebra_stripes=True)
        yield Footer()

    def on_mount(self) -> None:
        self.query_one(DataTable).add_columns("status", "component", "reason")
        self.refresh_health()
        self.set_interval(self.interval, self.refresh_health)

    def action_refresh(self) -> None:
        self.refresh_health()

    def refresh_health(self) -> None:
        banner = self.query_one("#banner", Static)
        try:
            now = self.clock()
            h = reader.load(self.path, now, self.stale_after_s)
            table = self.query_one(DataTable)
            table.clear()
            for c in h.components:
                table.add_row(status_cell(c), c.name, c.reason, key=c.name)
            banner.update(Text(render.header(h, now), style="bold red" if h.note else ""))
            self.last = h
        except Exception as e:                 # noqa: BLE001 - shown, retried on the next tick
            banner.update(Text(f"refresh failed: {type(e).__name__}: {e}", style="bold red"))
        self.refreshes += 1
