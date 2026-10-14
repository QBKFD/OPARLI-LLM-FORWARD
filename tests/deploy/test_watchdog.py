"""watchdog.sh with stub docker/curl/timeout: the existing gateway checks plus
the heartbeat, feed/recorder crash-loop and Redis checks."""

import subprocess

import pytest

TUE_10, SAT_10 = dict(WATCHDOG_NY_DOW=2, WATCHDOG_NY_HOUR=10), dict(WATCHDOG_NY_DOW=6, WATCHDOG_NY_HOUR=10)


def _containers(scanner=0, health="healthy", feed=None, rec=None, redis=False):
    c = {"scanner-scanner-1": {"restarts": scanner, "health": health},
         "scanner-ibgateway-1": {"restarts": 0, "health": "none"}}
    if feed is not None:
        c["scanner-feed-1"] = {"restarts": feed, "health": "healthy"}
    if rec is not None:
        c["scanner-recorder-1"] = {"restarts": rec, "health": "healthy"}
    if redis:
        c["scanner-redis-1"] = {"restarts": 0, "health": "healthy"}
    return c


def _run(stubs, clock=TUE_10, **state):
    stubs.set_state(**state)
    before = len(stubs.telegrams())
    r = subprocess.run(["bash", str(stubs.dir / "watchdog.sh")], capture_output=True, text=True, timeout=60,
                       env=stubs.env(WATCHDOG_STATE=stubs.root / "wd.state", **clock))
    assert r.returncode == 0, r.stdout + r.stderr
    return stubs.telegrams()[before:], r.stdout


def test_all_fine_is_silent(stubs):
    sent, _ = _run(stubs, containers=_containers(), probe_rc=0)
    assert sent == []
    assert "hfails=0" in (stubs.root / "wd.state").read_text()


def test_gateway_down_twice_restarts_and_alerts_then_recovers(stubs):
    assert _run(stubs, containers=_containers(), probe_rc=1)[0] == []
    sent, _ = _run(stubs, containers=_containers(), probe_rc=1)
    assert len(sent) == 1 and "IB Gateway API unreachable" in sent[0]
    assert ["docker", "compose", "restart", "ibgateway"] in stubs.calls()
    sent, _ = _run(stubs, containers=_containers(), probe_rc=0)
    assert sent == ["✅ scanner: IB Gateway reachable again — scanning resumes."]


def test_quiet_window_tracks_but_never_acts(stubs):
    for _ in range(3):
        sent, _ = _run(stubs, clock=SAT_10, containers=_containers(health="unhealthy"), probe_rc=1)
        assert sent == []
    assert ["docker", "compose", "restart", "ibgateway"] not in stubs.calls()


def test_hung_scanner_alerts_once_per_hour_then_recovers(stubs):
    assert _run(stubs, containers=_containers(health="unhealthy"), probe_rc=0)[0] == []
    sent, _ = _run(stubs, containers=_containers(health="unhealthy"), probe_rc=0)
    assert len(sent) == 1 and "no heartbeat for over 5 min" in sent[0]
    assert _run(stubs, containers=_containers(health="unhealthy"), probe_rc=0)[0] == []    # hourly
    assert ["docker", "compose", "restart", "scanner"] not in stubs.calls()               # alert only
    sent, _ = _run(stubs, containers=_containers(health="healthy"), probe_rc=0)
    assert sent == ["✅ scanner: heartbeat back."]


def test_starting_scanner_is_not_hung(stubs):
    for _ in range(3):
        assert _run(stubs, containers=_containers(health="starting"), probe_rc=0)[0] == []


def test_feed_crash_loop_alerts_only_while_the_gateway_is_up(stubs):
    _run(stubs, containers=_containers(feed=0, rec=0), probe_rc=0)
    sent, _ = _run(stubs, containers=_containers(feed=4, rec=0), probe_rc=0)
    assert len(sent) == 1 and "feed restarted 4 times" in sent[0]


def test_feed_restarting_with_the_gateway_down_is_the_gateway_alert(stubs):
    _run(stubs, containers=_containers(feed=0, rec=0), probe_rc=1)
    sent, _ = _run(stubs, containers=_containers(feed=5, rec=0), probe_rc=1)
    assert len(sent) == 1 and "IB Gateway API unreachable" in sent[0]


def test_recorder_crash_loop_alerts(stubs):
    _run(stubs, containers=_containers(feed=0, rec=0), probe_rc=0)
    sent, _ = _run(stubs, containers=_containers(feed=0, rec=3), probe_rc=0)
    assert len(sent) == 1 and "recorder restarted 3 times" in sent[0]


def test_redis_down_twice_alerts_then_recovers(stubs):
    assert _run(stubs, containers=_containers(redis=True), probe_rc=0, redis_rc=1)[0] == []
    sent, _ = _run(stubs, containers=_containers(redis=True), probe_rc=0, redis_rc=1)
    assert len(sent) == 1 and "Redis not answering" in sent[0]
    sent, _ = _run(stubs, containers=_containers(redis=True), probe_rc=0, redis_rc=0)
    assert sent == ["✅ scanner datafeed: Redis answering again."]


def test_no_stream_containers_no_stream_checks(stubs):
    _run(stubs, containers=_containers(), probe_rc=0, redis_rc=1)
    _run(stubs, containers=_containers(), probe_rc=0, redis_rc=1)
    assert not any(c[:4] == ["docker", "compose", "exec", "-T"] and "redis-cli" in c for c in stubs.calls())
    assert stubs.telegrams() == []


def test_old_state_file_still_loads(stubs):
    (stubs.root / "wd.state").write_text('fails=1\nlast_alert=0\nalerted=0\nlast_rc=2\nrelogins=""\n')
    sent, _ = _run(stubs, containers=_containers(scanner=2), probe_rc=1)
    assert len(sent) == 1 and "unreachable for ~10 min" in sent[0]       # fails carried over: 2 in a row
    s = (stubs.root / "wd.state").read_text()
    assert "fails=2" in s and "last_rc_feed=" in s


def test_the_token_never_goes_into_a_message(stubs):
    _run(stubs, containers=_containers(health="unhealthy"), probe_rc=1)
    _run(stubs, containers=_containers(health="unhealthy"), probe_rc=1)
    assert stubs.telegrams() and not any("test-token" in t for t in stubs.telegrams())
    urls = [c[1] for c in stubs.calls() if c[0] == "curl"]
    assert all(u == "https://api.telegram.org/bottest-token/sendMessage" for u in urls)
