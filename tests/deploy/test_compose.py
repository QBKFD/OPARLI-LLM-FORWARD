"""deploy/scanner: compose structure, the healthchecks run for real against
local files and Redis, the entrypoint per bar source, and the Dockerfile."""

import os
import re
import subprocess
import time

import pytest
import yaml

from deploy_stubs import DEPLOY

COMPOSE = yaml.safe_load((DEPLOY / "docker-compose.yml").read_text())
S = COMPOSE["services"]


def test_default_profile_is_todays_stack():
    always = {n for n, s in S.items() if not s.get("profiles")}
    assert always == {"ibgateway", "scanner"}
    assert {n for n, s in S.items() if s.get("profiles") == ["stream"]} == {"redis", "feed", "recorder"}
    assert S["scanner"]["environment"]["SCANNER_BAR_SOURCE"] == "${SCANNER_BAR_SOURCE:-ibkr}"


def test_start_order_through_healthchecks():
    assert S["feed"]["depends_on"]["redis"] == {"condition": "service_healthy"}
    assert S["recorder"]["depends_on"]["feed"] == {"condition": "service_healthy"}
    # the scanner never waits on the datafeed: an ibkr-mode scanner must start
    # whatever the stream services do (DECISIONS.md D3)
    assert S["scanner"]["depends_on"] == ["ibgateway"]
    for n in ("redis", "feed", "recorder", "scanner"):
        assert S[n]["healthcheck"]["test"][0] == "CMD", n


def test_redis_persistence_and_memory_policy():
    cmd = " ".join(S["redis"]["command"])
    for flag in ("--appendonly yes", "--appendfsync everysec", "--maxmemory-policy noeviction",
                 "--maxmemory 256mb"):
        assert flag in cmd
    assert S["redis"]["volumes"] == ["redis_data:/data"]
    assert S["redis"]["image"] == "redis:7.4.2-alpine"


def test_nothing_published_but_vnc_on_localhost():
    for n, s in S.items():
        assert s.get("ports", []) == (["127.0.0.1:5900:5900"] if n == "ibgateway" else []), n


def test_secrets_only_by_substitution():
    for n, s in S.items():
        for k, v in s.get("environment", {}).items():
            if any(w in k for w in ("TOKEN", "PASSWORD", "USERID", "CHAT_ID")):
                assert re.fullmatch(r"\$\{[A-Z_]+(:-)?\}", str(v)), (n, k, v)


def test_gateway_client_ids_are_unique():
    scanner = int(S["scanner"]["environment"]["IB_CLIENT_ID"].split(":-")[1].rstrip("}"))
    feed = int(S["feed"]["environment"]["IB_CLIENT_ID"].split(":-")[1].rstrip("}"))
    entry = int(re.search(r"clientId=(\d+)", (DEPLOY / "entrypoint.sh").read_text()).group(1))
    watchdog = int(re.search(r"clientId=(\d+)", (DEPLOY / "watchdog.sh").read_text()).group(1))
    ids = [scanner, scanner + 10, feed, entry, watchdog]
    assert ids == [11, 21, 31, 999, 996] and len(set(ids)) == len(ids)


def test_store_and_volumes_line_up():
    assert S["recorder"]["environment"]["DATAFEED_DB"] == S["scanner"]["environment"]["DATAFEED_DB"] \
        == "/datafeed/datafeed.sqlite"
    assert "datafeed_store:/datafeed" in S["recorder"]["volumes"]
    assert "datafeed_store:/datafeed" in S["scanner"]["volumes"]
    assert "scanner_data:/data" in S["scanner"]["volumes"]
    assert set(COMPOSE["volumes"]) == {"scanner_data", "datafeed_store", "redis_data"}
    for n in ("scanner", "feed", "recorder"):
        assert S[n]["image"] == "oparli-scanner:local"
        assert S[n]["build"]["dockerfile"] == "deploy/scanner/Dockerfile"
    assert S["feed"]["entrypoint"] == ["python", "-m", "datafeed.ingest"]
    assert S["recorder"]["entrypoint"] == ["python", "-m", "datafeed.recorder"]
    assert S["feed"]["environment"]["DATAFEED_SOURCE"] == "ibkr"


def _hc(service, **paths):
    code = S[service]["healthcheck"]["test"][-1]
    for container, local in paths.items():
        code = code.replace(container, local)
    return code


def _run_hc(code, **env):
    import sys
    return subprocess.run([sys.executable, "-c", code], env={**os.environ, **env},
                          capture_output=True, timeout=30).returncode


def test_scanner_healthcheck_reads_the_heartbeat(tmp_path):
    hb = tmp_path / "heartbeat"
    code = _hc("scanner", **{"/data/heartbeat": str(hb)})
    assert _run_hc(code) == 1                                  # no heartbeat yet
    hb.write_text("x")
    assert _run_hc(code) == 0
    old = time.time() - 301
    os.utime(hb, (old, old))
    assert _run_hc(code) == 1                                  # hung: older than 300 s


def test_recorder_healthcheck_wants_bars(tmp_path):
    db = tmp_path / "datafeed.sqlite"
    code = _hc("recorder", **{"/datafeed/datafeed.sqlite": str(db)})
    assert _run_hc(code) == 1                                  # no store
    from datafeed.store import schema
    con = schema.connect(str(db))
    assert _run_hc(code) == 1                                  # empty store
    con.execute("INSERT INTO bars (symbol, bar_ts, kind, open, high, low, close, n_ticks, source, "
                "schema_version, ingest_ts, recorded_ts) VALUES ('XAUUSD','2025-06-16T10:00:00+00:00',"
                "'live',1,1,1,1,1,'ibkr',1,'2025-06-16T10:01:00+00:00','2025-06-16T10:01:01+00:00')")
    con.close()
    assert _run_hc(code) == 0


@pytest.mark.redis
def test_feed_healthcheck_wants_a_published_bar():
    url = os.environ.get("REDIS_TEST_URL")
    if not url:
        pytest.skip("REDIS_TEST_URL not set (needs a throwaway Redis)")
    redis = pytest.importorskip("redis")
    r = redis.Redis.from_url(url)
    r.flushdb()
    code = _hc("feed")
    assert _run_hc(code, REDIS_URL=url) == 1
    r.xadd("bars.xauusd.1m", {"json": "{}"})
    assert _run_hc(code, REDIS_URL=url) == 0
    r.flushdb()


# ── entrypoint.sh ───────────────────────────────────────────────────────────
def _entrypoint(tmp_path, py_rc, **env):
    """Run entrypoint.sh with stub python/sleep; returns the python calls."""
    b = tmp_path / "bin"
    b.mkdir(exist_ok=True)
    log = tmp_path / "py.log"
    (b / "python").write_text(f'#!/bin/sh\necho "$@" >> {log}\n'
                              f'[ -n "$ALERT_TEXT" ] && echo "ALERT_TEXT=$ALERT_TEXT" >> {log}\n'
                              f'case "$1" in -c) exit {py_rc};; esac\nexit 0\n')
    (b / "sleep").write_text("#!/bin/sh\nexit 0\n")
    for f in ("python", "sleep"):
        (b / f).chmod(0o755)
    r = subprocess.run(["bash", str(DEPLOY / "entrypoint.sh")], capture_output=True, text=True, timeout=30,
                       env={"PATH": f"{b}:/usr/bin:/bin", **env})
    return r, log.read_text().splitlines()


def test_entrypoint_stream_waits_for_redis_never_the_gateway(tmp_path):
    r, calls = _entrypoint(tmp_path, 0, SCANNER_BAR_SOURCE="stream", REDIS_URL="redis://redis:6379/0")
    assert r.returncode == 0 and "waiting for Redis" in r.stdout
    assert "redis" in calls[0] and "ping()" in calls[0]
    assert not any("ib_insync" in c or "clientId" in c for c in calls)
    assert calls[-1] == "/app/deterministic_scanner.py --live"


def test_entrypoint_stream_redis_timeout_alerts_and_exits_nonzero(tmp_path):
    r, calls = _entrypoint(tmp_path, 1, SCANNER_BAR_SOURCE="stream", REDIS_URL="redis://x")
    assert r.returncode == 1
    assert sum("ping()" in c for c in calls) == 120           # 120 x 5 s, as the gateway wait
    assert not any("deterministic_scanner.py" in c for c in calls)          # never started
    assert any("sendMessage" in c for c in calls)
    assert any(c.startswith("ALERT_TEXT=⚠️ scanner: Redis not reachable for 10 min") for c in calls)
    assert "alerting and exiting 1" in r.stderr


def test_entrypoint_redis_wait_length_is_configurable(tmp_path):
    r, calls = _entrypoint(tmp_path, 1, SCANNER_BAR_SOURCE="stream", REDIS_URL="redis://x",
                           REDIS_WAIT_TRIES="3")
    assert r.returncode == 1 and sum("ping()" in c for c in calls) == 3


def test_entrypoint_alert_snippet_is_hourly(tmp_path):
    """The Python the entrypoint runs to alert, executed for real (no token:
    nothing is sent; the hourly marker logic is what's tested)."""
    import sys
    src = (DEPLOY / "entrypoint.sh").read_text()
    code = re.search(r'ALERT_PY="(.*?)"\n', src, re.S).group(1)
    marker = tmp_path / "marker"
    env = {"PATH": os.environ["PATH"], "ENTRYPOINT_ALERT_MARKER": str(marker), "ALERT_TEXT": "x"}
    assert subprocess.run([sys.executable, "-c", code], env=env, timeout=30).returncode == 0
    assert marker.exists()
    old = time.time() - 600
    os.utime(marker, (old, old))
    subprocess.run([sys.executable, "-c", code], env=env, timeout=30)
    assert abs(marker.stat().st_mtime - old) < 1               # within the hour: not touched (no alert)
    older = time.time() - 4000
    os.utime(marker, (older, older))
    subprocess.run([sys.executable, "-c", code], env=env, timeout=30)
    assert marker.stat().st_mtime > time.time() - 60           # after an hour: alerted again


@pytest.mark.parametrize("env", [{}, {"SCANNER_BAR_SOURCE": "ibkr"}])
def test_entrypoint_ibkr_waits_for_the_gateway(tmp_path, env):
    r, calls = _entrypoint(tmp_path, 0, IB_HOST="ibgateway", IB_PORT="4004", **env)
    assert "waiting for IB Gateway API at ibgateway:4004" in r.stdout
    assert "clientId=999" in calls[0] and "redis" not in calls[0]
    assert calls[-1] == "/app/deterministic_scanner.py --live"


# ── Dockerfile ──────────────────────────────────────────────────────────────
def test_dockerfile_pins_and_copies():
    d = (DEPLOY / "Dockerfile").read_text()
    for pin in ("ib_insync==0.9.86", "pandas==2.3.3", "numpy==2.2.6", "pyarrow==25.0.0",
                "tzdata==2025.3", "redis==5.2.1"):
        assert pin in d
    assert not re.search(r"pip install[^\n]*\s(pyarrow|tzdata|redis)(\s|$)", d)   # nothing unpinned
    for src in ("backend/deterministic_scanner.py", "backend/services/tws_connector.py", "datafeed",
                "backend/journal_summary.py", "scoring", "risk",
                "deploy/scanner/backup_sqlite.py", "deploy/scanner/entrypoint.sh"):
        assert re.search(rf"^COPY\s+{re.escape(src)}\s", d, re.M), src
        assert (DEPLOY.parents[1] / src).exists(), src
    lock = (DEPLOY.parents[1] / "requirements.lock").read_text()
    for pin in ("pandas==2.3.3", "numpy==2.2.6", "pyarrow==25.0.0", "tzdata==2025.3", "redis==5.2.1",
                "ib-insync==0.9.86"):
        assert pin in lock or pin.replace("-", "_") in lock, pin       # same versions as the tested lock


def test_env_example_has_no_real_values():
    env = (DEPLOY / ".env.example").read_text()
    for k in ("SCANNER_BAR_SOURCE=ibkr", "FEED_IB_CLIENT_ID=31", "BACKUP_S3_URI=s3://", "#COMPOSE_PROFILES=stream"):
        assert k in env
    assert "AWS_ACCESS_KEY" not in env and "AWS_SECRET" not in env


def _mb(v):
    v = str(v).lower()
    return int(v[:-1]) if v.endswith("m") else int(float(v[:-1]) * 1024) if v.endswith("g") else int(v) // 2**20


def test_memory_limits_fit_a_3_8_gb_host():
    limits = {n: _mb(s["mem_limit"]) for n, s in S.items()}
    assert set(limits) == set(S)                                  # every service has one
    assert max(limits, key=limits.get) == "ibgateway"             # the heavy one
    assert sum(limits.values()) == 2880
    assert 3.8 * 1024 - sum(limits.values()) >= 800              # room for the OS and Docker
    redis_max = int(S["redis"]["command"][S["redis"]["command"].index("--maxmemory") + 1].rstrip("mb"))
    assert limits["redis"] >= redis_max * 1.4                    # AOF-rewrite fork headroom
