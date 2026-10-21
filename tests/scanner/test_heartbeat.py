"""Scanner heartbeat (SCANNER_HEARTBEAT): what the compose healthcheck and
watchdog.sh read to tell a hung main loop from a live one."""

import inspect
import os

import pandas as pd

import deterministic_scanner as ds

from tests.scanner.scanner_helpers import Log


def test_beat_writes_the_utc_time(tmp_path):
    p = tmp_path / "heartbeat"
    before = pd.Timestamp.now(tz="UTC")
    ds.beat(str(p))
    t = pd.Timestamp(p.read_text())
    assert t.tz is not None and before <= t <= pd.Timestamp.now(tz="UTC")
    assert not (tmp_path / "heartbeat.tmp").exists()          # replaced, not left behind


def test_no_path_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    ds.beat(None)
    ds.beat("")
    assert os.listdir(tmp_path) == []


def test_a_failed_write_never_raises(tmp_path, capsys):
    ds.beat(str(tmp_path / "missing-dir" / "heartbeat"))
    assert "heartbeat write" in capsys.readouterr().out


def test_waiting_for_the_store_beats(tmp_path):
    beats = []
    t = {"now": pd.Timestamp("2025-06-18 12:00:30", tz="UTC")}

    def sleep(s):
        t["now"] += pd.Timedelta(seconds=s)
        if len(beats) >= 3:
            raise KeyboardInterrupt

    try:
        ds.wait_for_store(lambda m: None, db=str(tmp_path / "missing.sqlite"), clock=lambda: t["now"],
                          sleep=sleep, log=Log(), beat=lambda: beats.append(1))
    except KeyboardInterrupt:
        pass
    assert len(beats) == 3


def test_both_main_loops_beat_every_pass():
    import re
    for fn in (ds.run_live, ds.run_live_stream):
        src = inspect.getsource(fn)
        # the main (Telegram) loop: its first statement is the beat
        assert re.search(r"try:\n\s+while True:\n\s+beat\(heartbeat\)", src), fn.__name__
        assert 'SCANNER_HEARTBEAT' in src
