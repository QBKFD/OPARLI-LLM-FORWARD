"""thresholds.toml: loading, validation, and the watchdog's alert thresholds."""

import re
import subprocess
import sys
from pathlib import Path

import pytest

from tools.status import config

ROOT = Path(__file__).resolve().parents[2]
WATCHDOG = ROOT / "deploy" / "scanner" / "watchdog.sh"


def _toml(tmp_path, text):
    p = tmp_path / "t.toml"
    p.write_text(text)
    return p


def _default_text():
    return config.DEFAULT_PATH.read_text()


def test_the_shipped_file_loads_and_is_hashed():
    t = config.load()
    assert t.disk.red_pct == 85 and t.memory.red_avail_pct == 10 and t.health.stale_after_s == 900
    assert re.fullmatch(r"[0-9a-f]{64}", t.sha256)


def test_watchdog_fallback_numbers_equal_the_red_thresholds():
    """watchdog.sh keeps its old constants as a fallback: they must equal the file."""
    src = WATCHDOG.read_text()
    t = config.load()
    for line in config.watchdog_env(t):
        k, v = line.split("=")
        assert re.search(rf"^{k}={v}$", src, re.M), (k, v)


@pytest.mark.parametrize("edit, msg", [
    (lambda s: s.replace("red_pct = 85", "red_pct = 85\nextra = 1"), "unknown keys"),
    (lambda s: s.replace("yellow_pct = 75\n", ""), "missing keys"),
    (lambda s: s.replace("red_pct = 85", "red_pct = -1"), "integer >= 0"),
    (lambda s: s.replace("red_pct = 85", "red_pct = true"), "integer >= 0"),
    (lambda s: s.replace("red_pct = 85", 'red_pct = "85"'), "integer >= 0"),
    (lambda s: s.replace("red_pct = 85", "red_pct = 70"), "yellow_pct must be <= red_pct"),
    (lambda s: s.replace("red_avail_pct = 10", "red_avail_pct = 30"), "yellow_avail_pct must be >= red_avail_pct"),
    (lambda s: s.replace("yellow_pct = 75", "yellow_pct = 175").replace("red_pct = 85", "red_pct = 185"),
     "percentage"),
    (lambda s: s.replace("stale_after_s = 900", "stale_after_s = 0"), "stale_after_s must be > 0"),
    (lambda s: s + "\n[bogus]\nx = 1\n", "unknown sections"),
    (lambda s: s.replace("[agents]", "[agentz]"), "unknown sections"),
    (lambda s: s.replace("[disk]", "[disk"), "t.toml"),
])
def test_invalid_files_are_refused_with_the_reason(tmp_path, edit, msg):
    with pytest.raises(config.ConfigError, match=re.escape(msg)):
        config.load(_toml(tmp_path, edit(_default_text())))


def test_a_missing_file(tmp_path):
    with pytest.raises(config.ConfigError, match="cannot read"):
        config.load(tmp_path / "nope.toml")


def test_cli_prints_the_watchdog_env_and_fails_cleanly(tmp_path):
    ok = subprocess.run([sys.executable, "-m", "tools.status.config", "--watchdog-env"], cwd=ROOT,
                        capture_output=True, text=True)
    assert ok.returncode == 0
    assert ok.stdout.split() == ["DISK_ALERT_PCT=85", "MEM_AVAIL_MIN_PCT=10", "BACKUP_MAX_AGE_H=26",
                                 "RECORDER_LAG_MAX_MIN=15"]
    bad = subprocess.run([sys.executable, "-m", "tools.status.config", "--watchdog-env", "--thresholds",
                          str(tmp_path / "nope.toml")], cwd=ROOT, capture_output=True, text=True)
    assert bad.returncode == 1 and bad.stdout == "" and "cannot read" in bad.stderr
