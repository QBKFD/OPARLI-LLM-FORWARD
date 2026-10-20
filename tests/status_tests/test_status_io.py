"""health.json: the atomic writer, the never-raising reader, --once output."""

import io
import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from status_helpers import NOW, facts, thresholds
from tools.status import reader, render, write
from tools.status.__main__ import main as status_main
from tools.status.evaluate import evaluate
from tools.status.model import COMPONENTS, from_json, to_json

ROOT = Path(__file__).resolve().parents[2]


def _health(**over):
    return evaluate(facts(**over), thresholds(), NOW)


def _write(tmp_path, h=None, name="health.json"):
    p = tmp_path / name
    write.write_atomic(p, to_json(h or _health()))
    return p


# ── writer ─────────────────────────────────────────────────────────────────
def test_parse_facts():
    f, problems = write.parse_facts(["a=1", "journal_error=x = y z", "bad", "=3", "a=2"])
    assert f == {"a": "2", "journal_error": "x = y z"}
    assert problems == ["ignored malformed fact 'bad'", "ignored malformed fact '=3'"]


def test_round_trip():
    h = _health()
    assert from_json(to_json(h)) == h


def test_atomic_write_replaces_and_leaves_no_temp(tmp_path):
    p = _write(tmp_path)
    first = p.read_text()
    _write(tmp_path, _health(disk_pct=90))
    assert p.read_text() != first and from_json(p.read_text()).by_name()["disk"].status == "red"
    assert sorted(x.name for x in tmp_path.iterdir()) == ["health.json"]
    assert oct(p.stat().st_mode & 0o777) == "0o644"


def test_a_failed_write_keeps_the_old_file(tmp_path, monkeypatch):
    p = _write(tmp_path)
    before = p.read_text()

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        _write(tmp_path, _health(disk_pct=90))
    assert p.read_text() == before and sorted(x.name for x in tmp_path.iterdir()) == ["health.json"]


def test_cli_writes_and_reports_failures(tmp_path):
    out = tmp_path / "sub" / "h.json"
    toks = [f"{k}={v}" for k, v in facts().items()] + ["garbage"]
    r = subprocess.run([sys.executable, "-m", "tools.status.write", "--out", str(out), *toks], cwd=ROOT,
                       capture_output=True, text=True)
    assert r.returncode == 0 and "ignored malformed fact 'garbage'" in r.stderr
    h = from_json(out.read_text())
    assert h.generated_ts == "2026-10-15T05:00:00Z" and h.by_name()["gateway"].status == "green"
    (tmp_path / "dir.json").mkdir()
    r = subprocess.run([sys.executable, "-m", "tools.status.write", "--out", str(tmp_path / "dir.json"), *toks],
                       cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 1 and "cannot write" in r.stderr


def test_invalid_thresholds_still_write_an_all_red_file(tmp_path):
    out = tmp_path / "h.json"
    assert write.main(["--out", str(out), "--thresholds", str(tmp_path / "none.toml"), "now=1792040400"]) == 0
    h = from_json(out.read_text())
    assert {c.status for c in h.components} == {"red"}
    assert h.components[0].reason.startswith("thresholds invalid: cannot read")


def test_bad_now_falls_back_to_the_clock(tmp_path, capsys):
    out = tmp_path / "h.json"
    assert write.main(["--out", str(out), "now=yesterday"]) == 0
    assert "bad now='yesterday'" in capsys.readouterr().err
    assert from_json(out.read_text()).generated_ts.startswith("20")


# ── reader: never raises ───────────────────────────────────────────────────
def _all_red(h):
    return [c.name for c in h.components] == list(COMPONENTS) and {c.status for c in h.components} == {"red"}


def test_good_file(tmp_path):
    h = reader.load(_write(tmp_path), NOW + 60, 900)
    assert h.note is None and h.by_name()["gateway"].status == "green"


def test_missing_file_is_all_red(tmp_path):
    h = reader.load(tmp_path / "none.json", NOW, 900)
    assert _all_red(h) and "no health.json at" in h.note


@pytest.mark.parametrize("content", [
    "", "{", "[]", "null", '{"schema_version": 2}', "\x00\xff garbage",
    json.dumps({"schema_version": 1, "generated_ts": "2026-10-15T05:00:00Z", "thresholds_sha256": "",
                "components": []}),
])
def test_invalid_content_is_all_red(tmp_path, content):
    p = tmp_path / "h.json"
    p.write_bytes(content.encode("utf-8", "surrogateescape") if isinstance(content, str) else content)
    h = reader.load(p, NOW, 900)
    assert _all_red(h) and h.note.startswith(("health.json invalid", "health.json unreadable"))


def test_truncated_file_is_all_red(tmp_path):
    p = _write(tmp_path)
    p.write_text(p.read_text()[:200])
    assert _all_red(reader.load(p, NOW, 900))


def test_wrong_status_or_bad_timestamp_is_all_red(tmp_path):
    p = _write(tmp_path)
    d = json.loads(p.read_text())
    d["components"][0]["status"] = "purple"
    p.write_text(json.dumps(d))
    assert _all_red(reader.load(p, NOW, 900))
    d["components"][0]["status"], d["generated_ts"] = "green", "yesterday"
    p.write_text(json.dumps(d))
    assert _all_red(reader.load(p, NOW, 900))


def test_stale_file_is_all_red_with_the_last_known_state(tmp_path):
    p = _write(tmp_path)
    h = reader.load(p, NOW + 901, 900)
    assert _all_red(h) and h.note.startswith("STALE: health.json written 15 min ago")
    assert h.by_name()["gateway"].reason == "stale - last known: green, API handshake ok"
    assert h.by_name()["redis"].deployed and h.by_name()["redis"].reason.startswith("stale - last known: off")
    assert render.exit_code(h) == 2
    assert reader.load(p, NOW + 900, 900).note is None                     # exactly at the limit: fresh


def test_a_file_from_the_future_is_flagged_not_trusted_blindly(tmp_path):
    h = reader.load(_write(tmp_path), NOW - 600, 900)
    assert "in the future" in h.note and h.by_name()["gateway"].status == "green"


def test_a_huge_file_is_refused(tmp_path):
    p = tmp_path / "h.json"
    p.write_bytes(b" " * (reader.MAX_BYTES + 1))
    assert _all_red(reader.load(p, NOW, 900))


def test_a_directory_instead_of_a_file(tmp_path):
    (tmp_path / "h.json").mkdir()
    assert _all_red(reader.load(tmp_path / "h.json", NOW, 900))


# ── rendering and --once ───────────────────────────────────────────────────
def test_lines_without_colour_are_plain_words():
    h = _health(disk_pct=80, gateway_ok=0, gateway_fails=2)
    out = render.lines(h, NOW + 30, color=False)
    assert out[0] == "OPARLI status: RED (written 30 s ago, 2026-10-15T05:00:00Z)"
    assert out[1] == "RED    gateway   API unreachable, 2 check(s) in a row (~10 min)"
    assert "YELLOW disk      80% used" in out
    assert "OFF    redis     not deployed (no stream profile)" in out
    assert all("\033" not in x for x in out) and len(out) == 1 + len(COMPONENTS)


def test_colour_codes_only_when_asked():
    line = render.component_line(_health().components[0], color=True)
    assert line.startswith("\033[32mGREEN ") and "\033[0m" in line


class _Tty(io.StringIO):
    def isatty(self):
        return True


@pytest.mark.parametrize("stream, env, force, want", [
    (_Tty(), {}, None, True),
    (io.StringIO(), {}, None, False),                 # piped: no colour
    (_Tty(), {"NO_COLOR": ""}, None, False),          # NO_COLOR, any value
    (_Tty(), {"TERM": "dumb"}, None, False),
    (_Tty(), {}, False, False),                       # --no-color
])
def test_use_color(stream, env, force, want):
    assert render.use_color(stream, env, force) is want


@pytest.mark.parametrize("over, code", [({}, 0), (dict(disk_pct=80), 1), (dict(disk_pct=90), 2)])
def test_exit_code_is_the_worst_deployed_status(over, code):
    assert render.exit_code(_health(**over)) == code


def test_once_end_to_end(tmp_path, capsys):
    p = _write(tmp_path)
    code = status_main(["--once", "--file", str(p), "--no-color"])
    out = capsys.readouterr().out.splitlines()
    assert out[0].startswith("OPARLI status: RED") and out[1].startswith("!! STALE: health.json written")
    assert out[2].startswith("RED    gateway   stale - last known: green")      # NOW is days ago
    assert code == 2
    p2 = tmp_path / "missing.json"
    assert status_main(["--once", "--file", str(p2), "--no-color"]) == 2
    assert "no health.json at" in capsys.readouterr().out


def test_once_as_a_subprocess_with_no_colour_support(tmp_path):
    h = replace(_health(), generated_ts=reader.iso(__import__("time").time()))
    p = _write(tmp_path, h)
    r = subprocess.run([sys.executable, "-m", "tools.status", "--once", "--file", str(p)], cwd=ROOT,
                       capture_output=True, text=True, env={**os.environ, "TERM": "dumb"})
    assert r.returncode == 0, r.stdout + r.stderr
    assert "\033" not in r.stdout and r.stdout.splitlines()[1].startswith("GREEN  gateway")
