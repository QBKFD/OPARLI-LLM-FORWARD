"""Facts -> green / yellow / red, per component, at the threshold boundaries."""

import pytest

from status_helpers import NOW, facts, stream_facts, thresholds
from tools.status.evaluate import all_red, evaluate, level
from tools.status.model import COMPONENTS


def _h(**over):
    return evaluate(facts(**over), thresholds(), NOW).by_name()


def _hs(**over):
    return evaluate(stream_facts(**over), thresholds(), NOW).by_name()


def test_healthy_ibkr_deployment():
    h = evaluate(facts(), thresholds(), NOW)
    assert [c.name for c in h.components] == list(COMPONENTS)
    got = {c.name: (c.status, c.deployed) for c in h.components}
    assert got == {"gateway": ("green", True), "feed": ("green", True), "redis": ("green", False),
                   "recorder": ("green", False), "scanner": ("green", True), "journal": ("green", True),
                   "backups": ("green", True), "disk": ("green", True), "memory": ("green", True),
                   "agents": ("green", False)}
    assert h.generated_ts == "2026-10-15T05:00:00Z" and len(h.thresholds_sha256) == 64
    assert h.by_name()["redis"].reason == "not deployed (no stream profile)"
    assert h.by_name()["agents"].reason == "not deployed"
    assert "ibkr feed in the scanner" in h.by_name()["feed"].reason


def test_healthy_stream_deployment():
    h = _hs()
    assert all(c.status == "green" and c.deployed for n, c in h.items() if n != "agents")
    assert h["feed"].reason.startswith("datafeed stream: last bar 2026-10-15T04:59Z")


def test_level_is_inclusive():
    assert [level(v, 3, 10) for v in (0, 2, 3, 9, 10, 50)] == \
        ["green", "green", "yellow", "yellow", "red", "red"]


@pytest.mark.parametrize("comp, over, status", [
    ("gateway", dict(gateway_ok=0, gateway_fails=1), "yellow"),
    ("gateway", dict(gateway_ok=0, gateway_fails=2), "red"),
    ("gateway", dict(gateway_ok=0, gateway_fails=0), "yellow"),       # failed now, counter not yet bumped
    ("feed", dict(scanner_last_bar_age_min=2), "green"),
    ("feed", dict(scanner_last_bar_age_min=3), "yellow"),
    ("feed", dict(scanner_last_bar_age_min=10), "red"),
    ("feed", dict(scanner_last_bar_age_min=-1), "yellow"),
    ("scanner", dict(scanner_health="unhealthy", scanner_unhealthy_checks=1), "yellow"),
    ("scanner", dict(scanner_health="unhealthy", scanner_unhealthy_checks=2), "red"),
    ("scanner", dict(scanner_health="starting"), "yellow"),
    ("scanner", dict(scanner_health="weird"), "red"),
    ("scanner", dict(scanner_restarts_5min=1), "yellow"),
    ("scanner", dict(scanner_restarts_5min=3), "red"),
    ("scanner", dict(scanner_restarts_5min=-1), "green"),             # unknown delta on the first run
    ("scanner", dict(scanner_state="exited"), "red"),
    ("scanner", dict(scanner_state="missing"), "red"),
    ("scanner", dict(scanner_last_bar_age_min=5), "yellow"),
    ("scanner", dict(scanner_last_bar_age_min=30), "red"),
    ("journal", dict(journal_undecided=1, journal_undecided_oldest_min=59), "green"),
    ("journal", dict(journal_undecided=1, journal_undecided_oldest_min=60), "yellow"),
    ("journal", dict(journal_ok=0, journal_error="no journal at /data/x"), "red"),
    ("backups", dict(backup_age_h=24), "green"),
    ("backups", dict(backup_age_h=25), "yellow"),
    ("backups", dict(backup_age_h=26), "red"),
    ("backups", dict(backup_age_h=-1), "red"),
    ("backups", dict(backup_configured=0), "yellow"),
    ("disk", dict(disk_pct=74), "green"),
    ("disk", dict(disk_pct=75), "yellow"),
    ("disk", dict(disk_pct=85), "red"),
    ("memory", dict(mem_avail_pct=20), "green"),
    ("memory", dict(mem_avail_pct=19), "yellow"),
    ("memory", dict(mem_avail_pct=10), "yellow"),
    ("memory", dict(mem_avail_pct=9), "red"),
    ("memory", dict(mem_avail_pct=-1), "red"),                        # meminfo unreadable
    ("agents", dict(agents_deployed=1, agents_last_run_age_h=2), "green"),
    ("agents", dict(agents_deployed=1, agents_last_run_age_h=24), "yellow"),
    ("agents", dict(agents_deployed=1, agents_last_run_age_h=-1), "yellow"),
])
def test_thresholds(comp, over, status):
    assert _h(**over)[comp].status == status


@pytest.mark.parametrize("comp, over, status", [
    ("feed", dict(feed_last_bar_age_min=10), "red"),
    ("feed", dict(feed_last_bar_age_min=-1), "yellow"),
    ("feed", dict(feed_state="restarting"), "red"),
    ("feed", dict(feed_state="missing"), "red"),
    ("feed", dict(feed_probe_error="ConnectionError: refused"), "red"),
    ("redis", dict(redis_fails=1), "yellow"),
    ("redis", dict(redis_fails=2), "red"),
    ("redis", dict(redis_state="missing"), "red"),
    ("recorder", dict(recorder_lag_min=5), "yellow"),
    ("recorder", dict(recorder_lag_min=15), "red"),
    ("recorder", dict(recorder_lag_min=-1), "yellow"),
    ("recorder", dict(rejected_total=-1), "red"),
    ("recorder", dict(rejected_total=4, rejected_last_growth_ts=int(NOW) - 3600), "yellow"),
    ("recorder", dict(rejected_total=4, rejected_last_growth_ts=int(NOW) - 25 * 3600), "green"),
    ("recorder", dict(recorder_state="exited"), "red"),
])
def test_stream_thresholds(comp, over, status):
    assert _hs(**over)[comp].status == status


def test_a_missing_or_malformed_fact_is_red_never_green():
    f = facts()
    del f["disk_pct"]
    h = evaluate({**f, "mem_avail_pct": "lots"}, thresholds(), NOW).by_name()
    assert (h["disk"].status, h["disk"].reason) == ("red", "no data: disk_pct not reported")
    assert h["memory"].status == "red" and "bad value mem_avail_pct='lots'" in h["memory"].reason
    assert evaluate(facts(disk_pct=140), thresholds(), NOW).by_name()["disk"].status == "red"
    assert evaluate(facts(gateway_ok=2), thresholds(), NOW).by_name()["gateway"].status == "red"
    assert all(c.status == "red" for c in evaluate({}, thresholds(), NOW).components if c.deployed)


def test_a_component_expected_in_stream_mode_is_never_off():
    h = _hs(redis_state="missing", recorder_state="missing")
    assert h["redis"].deployed and h["redis"].status == "red"
    assert h["recorder"].deployed and h["recorder"].status == "red"


def test_reasons_say_what_and_how_long():
    h = _h(gateway_ok=0, gateway_fails=3, gateway_relogin_limit=1, market_open=0)
    assert h["gateway"].reason == "API unreachable, 3 check(s) in a row (~15 min) - automatic re-login limit reached"
    assert h["feed"].reason.endswith("(market closed)")
    j = _h(journal_take=2, journal_skip=1, journal_missed=1, journal_undecided=1,
           journal_undecided_oldest_min=75)["journal"]
    assert j.reason == "today TAKE 2, SKIP 1, MISSED 1, unanswered 1; oldest unanswered alert 75 min old"
    assert _h(scanner_last_alert_ts="")["scanner"].reason.endswith("last alert none")


def test_all_red():
    h = all_red("thresholds invalid: x", NOW)
    assert {c.status for c in h.components} == {"red"} and h.components[0].reason == "thresholds invalid: x"
