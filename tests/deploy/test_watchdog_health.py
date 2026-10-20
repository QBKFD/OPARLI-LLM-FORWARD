"""watchdog.sh writes health.json every run (stub docker/curl), takes its alert
thresholds from tools/status/thresholds.toml, and keeps working when the
status tooling is broken."""

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from tools.status.model import COMPONENTS, from_json   # noqa: E402

TUE_10 = dict(WATCHDOG_NY_DOW=2, WATCHDOG_NY_HOUR=10)
NOW = 1792040400
JOURNAL = "\n".join(["journal_ok=1", "scanner_last_bar_ts=2026-10-15T04:59Z", "scanner_last_bar_age_min=0",
                     "scanner_last_alert_ts=2026-10-15T03:00:00Z", "journal_take=1", "journal_skip=0",
                     "journal_missed=0", "journal_undecided=0", "journal_undecided_oldest_min=-1"])


def _containers(stream=False, scanner_state="running"):
    c = {"scanner-scanner-1": {"restarts": 0, "health": "healthy", "state": scanner_state},
         "scanner-ibgateway-1": {"restarts": 0, "health": "none"}}
    if stream:
        for n in ("feed", "recorder", "redis"):
            c[f"scanner-{n}-1"] = {"restarts": 0, "health": "healthy", "state": "running"}
    return c


def _run(stubs, **env):
    marker = stubs.root / "bk.state"
    marker.write_text(f"last_ok={NOW - 3600}\n")
    r = subprocess.run(["bash", str(stubs.dir / "watchdog.sh")], capture_output=True, text=True, timeout=120,
                       env=stubs.env(WATCHDOG_STATE=stubs.root / "wd.state", WATCHDOG_NOW=NOW,
                                     BACKUP_STATE=marker, **TUE_10, **env))
    assert r.returncode == 0, r.stdout + r.stderr
    return r.stdout


def _health(stubs):
    return from_json((stubs.root / "scanner-health.json").read_text()).by_name()


def test_an_ibkr_deployment_is_green_with_the_stream_parts_off(stubs):
    stubs.set_state(containers=_containers(), probe_rc=0, journal_stats=JOURNAL)
    _run(stubs)
    h = _health(stubs)
    assert list(h) == list(COMPONENTS)
    assert {n: c.status for n, c in h.items() if c.deployed} == dict.fromkeys(
        ["gateway", "feed", "scanner", "journal", "backups", "disk", "memory"], "green")
    assert {n for n, c in h.items() if not c.deployed} == {"redis", "recorder", "agents"}
    assert h["journal"].reason == "today TAKE 1, SKIP 0, MISSED 0, unanswered 0"
    assert h["backups"].reason == "last success 1 h ago"


def test_a_stream_deployment_reads_the_stream_probe(stubs):
    (stubs.dir / ".env").write_text((stubs.dir / ".env").read_text() + "SCANNER_BAR_SOURCE=stream\n")
    stubs.set_state(containers=_containers(stream=True), probe_rc=0, journal_stats=JOURNAL,
                    store_stats="20 3", stream_stats="feed_last_bar_ts=2026-10-15T04:40Z\nfeed_last_bar_age_min=19")
    _run(stubs)
    h = _health(stubs)
    assert h["feed"].status == "red" and "19 market-open min ago" in h["feed"].reason
    assert h["recorder"].status == "red" and h["recorder"].reason.startswith("lag 20 market-open min")
    assert h["redis"].status == "green" and h["redis"].deployed


def test_the_gateway_down_and_the_scanner_stopped_show_red(stubs):
    stubs.set_state(containers=_containers(scanner_state="exited"), probe_rc=1)
    for _ in range(2):
        _run(stubs)
    h = _health(stubs)
    assert h["gateway"].status == "red" and h["scanner"].status == "red"
    assert (h["journal"].status, h["journal"].reason) == ("red", "journal unreadable: scanner container not running")


def test_a_failing_journal_probe_is_red_not_fatal(stubs):
    stubs.set_state(containers=_containers(), probe_rc=0, journal_stats=None)    # probe exits 1, no output
    _run(stubs)
    assert _health(stubs)["journal"].reason == "journal unreadable: journal probe gave no output"


def test_alert_thresholds_come_from_the_toml(stubs, tmp_path):
    repo = tmp_path / "repo"
    (repo / "tools").mkdir(parents=True)
    subprocess.run(["cp", "-R", str(ROOT / "tools" / "status"), str(repo / "tools" / "status")], check=True)
    toml = repo / "tools" / "status" / "thresholds.toml"
    toml.write_text(toml.read_text().replace("yellow_pct = 75", "yellow_pct = 40").replace("red_pct = 85", "red_pct = 50"))
    stubs.set_state(containers=_containers(), probe_rc=0, journal_stats=JOURNAL, disk={str(stubs.root): 60})
    _run(stubs, WATCHDOG_REPO=repo)
    sent = [c[2] for c in stubs.calls() if c[0] == "curl"]
    assert any("disk 60% used (alert at 50%)" in s for s in sent)
    assert _health(stubs)["disk"].status == "red"


def test_broken_status_tooling_never_stops_the_watchdog(stubs, tmp_path):
    stubs.set_state(containers=_containers(), probe_rc=0, journal_stats=JOURNAL, disk={str(stubs.root): 86})
    out = _run(stubs, WATCHDOG_REPO=tmp_path / "nowhere")
    assert "using the built-in alert thresholds" in out and "health.json not written" in out
    assert any("disk 86% used (alert at 85%)" in c[2] for c in stubs.calls() if c[0] == "curl")
    assert "rej_seen=" in (stubs.root / "wd.state").read_text()


def test_health_json_is_valid_json_with_one_entry_per_component(stubs):
    stubs.set_state(containers=_containers(), probe_rc=0, journal_stats=JOURNAL)
    _run(stubs)
    d = json.loads((stubs.root / "scanner-health.json").read_text())
    assert d["schema_version"] == 1 and [c["name"] for c in d["components"]] == list(COMPONENTS)
    assert sorted(p.name for p in stubs.root.iterdir() if p.name.startswith(".scanner-health")) == []
