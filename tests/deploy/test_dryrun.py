"""deploy/dryrun: the compose file can't touch production by construction, the
pre-flight refuses what could, and the backup wrapper is pinned to the dry run.
No Docker: compose is parsed, check_env.sh runs against a stub docker."""

import os
import re
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
DRY = ROOT / "deploy" / "dryrun"
PROD = yaml.safe_load((ROOT / "deploy" / "scanner" / "docker-compose.yml").read_text())
C = yaml.safe_load((DRY / "docker-compose.yml").read_text())
S = C["services"]
TOKEN = "123456:dry-run-token-value"


def _env(svc):
    return S[svc].get("environment", {})


def test_no_gateway_and_no_published_ports():
    assert set(S) == {"redis", "feed", "recorder", "scanner"}
    assert not any("ibgateway" in n or "gateway" in str(s.get("image", "")) for n, s in S.items())
    assert not any("ports" in s for s in S.values())
    assert C["name"] == "dryrun"


def test_everything_reaches_its_own_redis_by_a_unique_alias():
    assert S["redis"]["networks"] == {"dryrunnet": {"aliases": ["dryrun-redis"]}}
    for n in ("feed", "recorder", "scanner"):
        assert _env(n)["REDIS_URL"] == "redis://dryrun-redis:6379/0", n


def test_only_the_feed_joins_productions_network_and_it_is_external():
    nets = {n: s.get("networks", C["x-app"]["networks"]) for n, s in S.items()}
    assert [n for n, v in nets.items() if "prod" in v] == ["feed"]
    assert C["networks"]["prod"] == {"external": True, "name": "${PROD_NETWORK:-scanner_scannernet}"}


def test_own_image_volumes_and_memory():
    assert C["x-app"]["image"] == "oparli-scanner:dryrun" != PROD["x-app"]["image"]
    assert set(C["volumes"]) == {"scanner_data", "datafeed_store", "redis_data"}    # prefixed dryrun_ by compose
    limits = {n: int(s["mem_limit"].rstrip("m")) for n, s in S.items()}
    assert limits == {"redis": 96, "feed": 192, "recorder": 128, "scanner": 320} and sum(limits.values()) == 736


def test_instrument_prefix_and_stream_mode():
    for n in ("feed", "recorder", "scanner"):
        assert _env(n)["INSTRUMENT"] == "${INSTRUMENT:-BTCUSD}", n
    sc = _env("scanner")
    assert sc["SCANNER_BAR_SOURCE"] == "stream" and sc["TELEGRAM_PREFIX"] == "[DRYRUN]"
    assert "IB_HOST" not in sc and "IB_CLIENT_ID" not in sc           # the scanner never connects to IBKR
    f = _env("feed")
    assert f["IB_CLIENT_ID"] == "${DRYRUN_FEED_IB_CLIENT_ID:-44}" and f["IB_HOST"] == "ibgateway"
    assert "from_env().stream" in S["feed"]["healthcheck"]["test"][-1]


def test_the_feed_client_id_is_free():
    used = {11, 21, 31, 41, 43, 996, 999}
    assert 44 not in used
    assert set(re.findall(r"\b(\d{2,3})\b", (DRY / "check_env.sh").read_text().split('TAKEN="')[1].split('"')[0])) \
        == {str(x) for x in used}


# ── check_env.sh ───────────────────────────────────────────────────────────
def _setup(tmp_path, token=TOKEN, prod_token="999:prod-token", network_ok=True, gw="running", mode=0o600,
           extra=""):
    d = tmp_path / "dryrun"
    d.mkdir(parents=True)
    (d / "check_env.sh").write_bytes((DRY / "check_env.sh").read_bytes())
    (d / "check_env.sh").chmod(0o755)
    (d / ".env").write_text(f"TELEGRAM_BOT_TOKEN={token}\nTELEGRAM_CHAT_ID=42\nINSTRUMENT=BTCUSD\n"
                            f"DRYRUN_FEED_IB_CLIENT_ID=44\nBACKUP_S3_URI=s3://b/oparli/dryrun\n{extra}")
    (d / ".env").chmod(mode)
    prod = tmp_path / "prod.env"
    prod.write_text(f"TELEGRAM_BOT_TOKEN={prod_token}  # prod\nTELEGRAM_CHAT_ID=42\n")
    b = tmp_path / "bin"
    b.mkdir()
    (b / "docker").write_text("#!/bin/sh\n"
                              f'if [ "$1" = network ]; then exit {0 if network_ok else 1}; fi\n'
                              f'if [ "$1" = inspect ]; then echo {gw}; exit 0; fi\nexit 9\n')
    (b / "docker").chmod(0o755)
    r = subprocess.run(["bash", str(d / "check_env.sh")], capture_output=True, text=True, timeout=30,
                       env={"PATH": f"{b}:{os.environ['PATH']}", "PROD_ENV": str(prod), "HOME": str(tmp_path)})
    return r


def test_check_env_passes_a_safe_setup(tmp_path):
    r = _setup(tmp_path)
    assert r.returncode == 0, r.stdout
    assert r.stdout.strip().endswith("all checks passed")


def test_check_env_refuses_productions_bot_and_never_prints_it(tmp_path):
    r = _setup(tmp_path, token="999:prod-token")
    assert r.returncode == 1 and "token is production's" in r.stdout
    assert "999:prod-token" not in r.stdout + r.stderr


def test_check_env_refusals(tmp_path):
    cases = [dict(mode=0o644), dict(token=""), dict(network_ok=False), dict(gw="exited"),
             dict(extra="DRYRUN_FEED_IB_CLIENT_ID=31\n"), dict(extra="BACKUP_S3_URI=s3://b/oparli/scanner\n"),
             dict(extra="INSTRUMENT=XAUUSD\n")]
    want = [".env is mode 644", "must be set", "network scanner_scannernet not found", "gateway scanner-ibgateway-1 is exited",
            "client id 31 is taken", "must be under a dryrun/ prefix", "would duplicate production's gold alerts"]
    for i, (kw, msg) in enumerate(zip(cases, want)):
        r = _setup(tmp_path / str(i), **kw)
        assert r.returncode == 1 and msg in r.stdout, (kw, r.stdout)
        assert TOKEN not in r.stdout + r.stderr


def test_check_env_without_env_file(tmp_path):
    d = tmp_path / "d"
    d.mkdir()
    (d / "check_env.sh").write_bytes((DRY / "check_env.sh").read_bytes())
    r = subprocess.run(["bash", str(d / "check_env.sh")], capture_output=True, text=True, timeout=30)
    assert r.returncode == 1 and "no .env here" in r.stdout


# ── backup wrapper ─────────────────────────────────────────────────────────
def test_backup_wrapper_refuses_a_non_dryrun_prefix_and_pins_the_project(tmp_path):
    d = tmp_path / "deploy" / "dryrun"
    d.mkdir(parents=True)
    (d / "backup.sh").write_bytes((DRY / "backup.sh").read_bytes())
    (d / "backup.sh").chmod(0o755)
    (tmp_path / "deploy" / "scanner").mkdir()
    fake = tmp_path / "deploy" / "scanner" / "backup.sh"
    fake.write_text('#!/bin/sh\necho "$COMPOSE_PROJECT_NAME|$COMPOSE_FILE|$BACKUP_S3_URI|$BACKUP_STATE"\n')
    fake.chmod(0o755)
    (d / ".env").write_text("BACKUP_S3_URI=s3://b/oparli/scanner\n")
    r = subprocess.run(["bash", str(d / "backup.sh")], capture_output=True, text=True, env={"PATH": os.environ["PATH"],
                       "HOME": str(tmp_path)})
    assert r.returncode == 1 and "dryrun/ prefix" in r.stderr
    (d / ".env").write_text("BACKUP_S3_URI=s3://b/oparli/dryrun\n")
    r = subprocess.run(["bash", str(d / "backup.sh")], capture_output=True, text=True, env={"PATH": os.environ["PATH"],
                       "HOME": str(tmp_path)})
    assert r.returncode == 0
    assert r.stdout.strip() == f"dryrun|{d}/docker-compose.yml|s3://b/oparli/dryrun|{tmp_path}/.dryrun-backup"
