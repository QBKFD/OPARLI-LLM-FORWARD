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


# 2026-10-15 05:00 UTC: before the daily heartbeat hour (07 UTC)
NOW = 1792040400


def _run(stubs, clock=TUE_10, now=NOW, backup_age_h=1, **state):
    stubs.set_state(**state)
    marker = stubs.root / "bk.state"
    if backup_age_h is None:
        marker.unlink(missing_ok=True)
    else:
        marker.write_text(f"last_ok={now - backup_age_h * 3600}\n")
    before = len(stubs.telegrams())
    r = subprocess.run(["bash", str(stubs.dir / "watchdog.sh")], capture_output=True, text=True, timeout=60,
                       env=stubs.env(WATCHDOG_STATE=stubs.root / "wd.state", WATCHDOG_NOW=now,
                                     BACKUP_STATE=marker, **clock))
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


# ── host and data health, daily heartbeat ───────────────────────────────────
def test_disk_full_alerts_even_in_the_quiet_window_then_recovers(stubs):
    sent, _ = _run(stubs, clock=SAT_10, containers=_containers(), probe_rc=0, disk={str(stubs.root): 91})
    assert len(sent) == 1 and "disk 91% used" in sent[0]
    assert _run(stubs, clock=SAT_10, containers=_containers(), probe_rc=0, disk={str(stubs.root): 92})[0] == []
    sent, _ = _run(stubs, containers=_containers(), probe_rc=0, disk={str(stubs.root): 60})
    assert sent == ["✅ scanner host: disk back to 60%."]


def test_low_memory_alerts(stubs):
    stubs.meminfo(available_pct=6, swap_used_mb=900)
    sent, _ = _run(stubs, containers=_containers(), probe_rc=0)
    assert len(sent) == 1 and "only 6% memory available, swap 900 MB used" in sent[0]
    stubs.meminfo(available_pct=40)
    assert _run(stubs, containers=_containers(), probe_rc=0)[0] == ["✅ scanner host: memory available back to 40%."]


@pytest.mark.parametrize("age, alerted", [(1, False), (25, False), (27, True), (None, True)])
def test_backup_age(stubs, age, alerted):
    sent, _ = _run(stubs, clock=SAT_10, containers=_containers(), probe_rc=0, backup_age_h=age)
    assert bool(sent) is alerted
    if alerted:
        assert ("never" if age is None else f"{age} h ago") in sent[0]


def test_no_backup_check_without_a_backup_uri(stubs):
    (stubs.dir / ".env").write_text("TELEGRAM_BOT_TOKEN=test-token\nTELEGRAM_CHAT_ID=42\n")
    assert _run(stubs, containers=_containers(), probe_rc=0, backup_age_h=None)[0] == []


def test_recorder_lag_alerts_then_recovers(stubs):
    c = _containers(feed=0, rec=0, redis=True)
    assert _run(stubs, containers=c, probe_rc=0, store_stats="3 0")[0] == []
    sent, _ = _run(stubs, containers=c, probe_rc=0, store_stats="22 0")
    assert len(sent) == 1 and "newest live bar is 22 market-open min old" in sent[0]
    sent, _ = _run(stubs, containers=c, probe_rc=0, store_stats="1 0")
    assert sent == ["✅ scanner datafeed: recorder caught up (lag 1 market-open min)."]
    assert _run(stubs, containers=c, probe_rc=0, store_stats="-1 0")[0] == []      # no live bar yet


def test_rejected_messages_growth(stubs):
    c = _containers(feed=0, rec=0, redis=True)
    assert _run(stubs, containers=c, probe_rc=0, store_stats="0 5")[0] == []       # baseline
    sent, _ = _run(stubs, containers=c, probe_rc=0, store_stats="0 8")
    assert len(sent) == 1 and "3 new rejected_messages (total 8)" in sent[0]
    assert _run(stubs, containers=c, probe_rc=0, store_stats="0 8")[0] == []
    assert _run(stubs, containers=c, probe_rc=0, store_stats="0 9", now=NOW + 600)[0] == []   # hourly
    sent, _ = _run(stubs, containers=c, probe_rc=0, store_stats="0 9", now=NOW + 3700)
    assert len(sent) == 1 and "1 new rejected_messages (total 9)" in sent[0]


def test_no_datafeed_checks_without_the_recorder(stubs):
    _run(stubs, containers=_containers(), probe_rc=0, store_stats="999 999")
    assert not any(c[:5] == ["docker", "compose", "exec", "-T", "recorder"] for c in stubs.calls())


def test_one_daily_heartbeat_every_day_weekends_included(stubs):
    h7 = NOW + 2 * 3600 + 300                                  # 07:05 UTC
    c = _containers(feed=0, rec=0, redis=True)
    assert _run(stubs, clock=SAT_10, containers=c, probe_rc=0, store_stats="2 4")[0] == []   # 05:00: not yet
    sent, _ = _run(stubs, clock=SAT_10, containers=c, probe_rc=0, store_stats="2 4", now=h7)
    assert len(sent) == 1 and sent[0].startswith("💓 scanner daily: gateway up; scanner healthy")
    for part in ("disk 40%", "memory 50% available", "last backup 1 h ago",
                 "recorder lag 2 market-open min, rejected 4"):
        assert part in sent[0]
    assert _run(stubs, containers=c, probe_rc=0, store_stats="2 4", now=h7 + 3600)[0] == []   # same day
    sent, _ = _run(stubs, containers=c, probe_rc=0, store_stats="2 4", now=h7 + 86400)
    assert len(sent) == 1 and sent[0].startswith("💓")                                       # next day


def test_heartbeat_reports_a_down_gateway(stubs):
    sent, _ = _run(stubs, clock=SAT_10, containers=_containers(), probe_rc=1, now=NOW + 3 * 3600)
    assert any(s.startswith("💓 scanner daily: gateway DOWN") for s in sent)


def test_store_stats_snippet_against_a_real_store(tmp_path):
    """The Python the watchdog runs inside the recorder, executed for real."""
    import re
    import sys
    from datetime import datetime, timedelta, timezone

    from backend.datafeed.store import schema
    src = (stubs_dir := __import__("tests.deploy.deploy_stubs", fromlist=["DEPLOY"]).DEPLOY / "watchdog.sh").read_text()
    code = re.search(r'STORE_STATS_PY="(.*?)"\n', src, re.S).group(1).replace('\\"', '"')
    db = tmp_path / "datafeed.sqlite"
    con = schema.connect(str(db))
    con.execute("INSERT INTO rejected_messages VALUES ('s', '1-0', '\"x\"', 'e', 'invalid', '2026-10-15T00:00:00+00:00')")
    out = subprocess.run([sys.executable, "-c", code.replace("/datafeed/datafeed.sqlite", str(db))],
                         capture_output=True, text=True, timeout=60)
    assert out.stdout.split() == ["-1", "1"], out.stderr            # no live bar yet, 1 rejected
    last = (datetime.now(timezone.utc) - timedelta(minutes=9)).replace(second=0, microsecond=0)
    con.execute("INSERT INTO bars (symbol, bar_ts, kind, open, high, low, close, n_ticks, source, "
                "schema_version, ingest_ts, recorded_ts) VALUES ('XAUUSD', ?, 'live', 1, 1, 1, 1, 1, 'ibkr', 1, ?, ?)",
                (last.isoformat(), last.isoformat(), last.isoformat()))
    con.close()
    out = subprocess.run([sys.executable, "-c", code.replace("/datafeed/datafeed.sqlite", str(db))],
                         capture_output=True, text=True, timeout=60)
    lag, rej = map(int, out.stdout.split())
    from backend.datafeed.ingest.xauusd_bars import _market_open
    want = sum(_market_open(last + timedelta(minutes=k)) for k in range(1, 9))   # complete minutes since its end
    assert rej == 1 and lag in (want, want + 1), (lag, want)
    assert stubs_dir.exists()
