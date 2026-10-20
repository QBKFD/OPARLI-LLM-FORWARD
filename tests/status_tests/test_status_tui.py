"""The Textual board, headless (App.run_test): rows, refresh, and bad input
never crashing it. No network, no real terminal."""

import asyncio

import pytest

pytest.importorskip("textual")

from status_helpers import NOW, facts, thresholds          # noqa: E402
from tools.status import write                             # noqa: E402
from tools.status.evaluate import evaluate                 # noqa: E402
from tools.status.model import COMPONENTS, to_json         # noqa: E402
from tools.status.tui import StatusApp                     # noqa: E402
from textual.widgets import DataTable, Static              # noqa: E402


def _rows(app):
    t = app.query_one(DataTable)
    return {str(t.get_row(k)[1]): str(t.get_row(k)[0]) for k in t.rows}


def _banner(app):
    return str(app.query_one("#banner", Static).render())


def _run(app, steps):
    async def go():
        async with app.run_test(size=(140, 30)) as pilot:
            await pilot.pause()
            return await steps(app, pilot)
    try:
        return asyncio.run(go())
    finally:
        # asyncio.run leaves no current loop; ib_insync (other tests) expects one
        asyncio.set_event_loop(asyncio.new_event_loop())


def test_shows_every_component_and_picks_up_a_new_file(tmp_path):
    p = tmp_path / "h.json"
    write.write_atomic(p, to_json(evaluate(facts(), thresholds(), NOW)))
    clock = {"t": NOW + 10}
    app = StatusApp(p, 900, interval=60, clock=lambda: clock["t"])

    async def steps(app, pilot):
        first = _rows(app)
        write.write_atomic(p, to_json(evaluate(facts(disk_pct=90), thresholds(), NOW)))
        await pilot.press("r")
        await pilot.pause()
        return first, _rows(app), _banner(app)

    first, second, banner = _run(app, steps)
    assert list(first) == list(COMPONENTS)
    assert first["gateway"] == "GREEN" and first["redis"] == "OFF" and first["disk"] == "GREEN"
    assert second["disk"] == "RED" and banner.startswith("OPARLI status: RED")


def test_missing_file_shows_red_and_keeps_running(tmp_path):
    app = StatusApp(tmp_path / "none.json", 900, interval=60, clock=lambda: NOW)

    async def steps(app, pilot):
        return _rows(app), _banner(app), app.is_running

    rows, banner, running = _run(app, steps)
    assert set(rows.values()) == {"RED"} and "no health.json at" in banner and running


def test_stale_file_shows_red(tmp_path):
    p = tmp_path / "h.json"
    write.write_atomic(p, to_json(evaluate(facts(), thresholds(), NOW)))
    app = StatusApp(p, 900, interval=60, clock=lambda: NOW + 3600)

    async def steps(app, pilot):
        return _rows(app), _banner(app)

    rows, banner = _run(app, steps)
    assert set(rows.values()) == {"RED"} and "STALE" in banner


def test_an_error_while_refreshing_is_shown_not_raised(tmp_path, monkeypatch):
    from tools.status import reader
    app = StatusApp(tmp_path / "h.json", 900, interval=60, clock=lambda: NOW)

    def boom(*a, **k):
        raise RuntimeError("disk on fire")

    async def steps(app, pilot):
        monkeypatch.setattr(reader, "load", boom)
        await pilot.press("r")
        await pilot.pause()
        return _banner(app), app.is_running, app.refreshes

    banner, running, n = _run(app, steps)
    assert banner == "refresh failed: RuntimeError: disk on fire" and running and n == 2


def test_the_timer_refreshes_on_its_own(tmp_path):
    app = StatusApp(tmp_path / "none.json", 900, interval=0.05, clock=lambda: NOW)

    async def steps(app, pilot):
        await asyncio.sleep(0.3)
        return app.refreshes

    assert _run(app, steps) >= 3
