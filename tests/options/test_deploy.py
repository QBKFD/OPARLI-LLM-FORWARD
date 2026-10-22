"""deploy/options: run_snapshot.sh and backup.sh against stub docker, flock,
curl, aws and hostname (no Docker, AWS or Telegram), and the compose file."""
import gzip
import json
import os
import re
import sqlite3
import stat
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from backend.options import store

ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "deploy" / "options"
BACKUP_SQLITE = ROOT / "deploy" / "scanner" / "backup_sqlite.py"

DOCKER = r'''#!PYTHON
import json, os, subprocess, sys
root, args = os.environ["STUB_ROOT"], sys.argv[1:]
with open(os.path.join(root, "calls.jsonl"), "a") as f:
    f.write(json.dumps(["docker"] + args) + "\n")
assert args[:2] == ["compose", "run"], args
rest, maps = args[2:], {"/data": os.path.join(root, "data")}
cmd = []
while rest:
    a = rest.pop(0)
    if a == "-v":
        host, cont = rest.pop(0).rsplit(":", 1)
        maps[cont] = host
    elif a == "--entrypoint":
        cmd.append(rest.pop(0))
    elif a.startswith("-"):
        continue
    else:
        service, cmd = a, cmd + rest
        break
assert service == "options-snapshot", service
if not cmd:                                         # the snapshot itself
    sys.exit(int(os.environ.get("STUB_SNAPSHOT_RC", "0")))
def host(p):
    for c, h in sorted(maps.items(), key=lambda kv: -len(kv[0])):
        if p == c or p.startswith(c + "/"):
            return h + p[len(c):]
    return p
assert cmd[:2] == ["python", "/app/backup_sqlite.py"], cmd
sys.exit(subprocess.call([sys.executable, "BACKUP_SQLITE"] + [host(a) for a in cmd[2:]]))
'''

AWS = r'''#!PYTHON
import json, os, shutil, sys
root, args = os.environ["STUB_ROOT"], sys.argv[1:]
with open(os.path.join(root, "calls.jsonl"), "a") as f:
    f.write(json.dumps(["aws"] + args) + "\n")
if os.environ.get("STUB_AWS_RC"):
    sys.exit(int(os.environ["STUB_AWS_RC"]))
src, dst = [a for a in args if not a.startswith("-") and a not in ("s3", "cp", "AES256")]
out = os.path.join(root, "s3", dst[len("s3://"):])
os.makedirs(os.path.dirname(out), exist_ok=True)
shutil.copyfile(src, out)
'''

CURL = r'''#!PYTHON
import json, os, sys
with open(os.path.join(os.environ["STUB_ROOT"], "calls.jsonl"), "a") as f:
    f.write(json.dumps(["curl"] + sys.argv[1:]) + "\n")
'''

FLOCK = '#!/bin/sh\nexit "${STUB_FLOCK_RC:-0}"\n'
HOSTNAME = '#!/bin/sh\necho testhost\n'


class Stubs:
    def __init__(self, tmp: Path):
        self.root, self.bin, self.home = tmp / "root", tmp / "bin", tmp / "home"
        for d in (self.root / "data", self.bin, self.home):
            d.mkdir(parents=True)
        for name, body in (("docker", DOCKER), ("aws", AWS), ("curl", CURL), ("flock", FLOCK),
                           ("hostname", HOSTNAME)):
            p = self.bin / name
            p.write_text(body.replace("PYTHON", sys.executable).replace("BACKUP_SQLITE", str(BACKUP_SQLITE)))
            p.chmod(p.stat().st_mode | stat.S_IXUSR)

    def run(self, script: str, **env) -> subprocess.CompletedProcess:
        e = {"PATH": f"{self.bin}:/usr/bin:/bin", "HOME": str(self.home), "STUB_ROOT": str(self.root),
             "BACKUP_S3_URI": "s3://test-bucket/oparli", "OPTIONS_LOCK": str(self.root / "lock"), **env}
        return subprocess.run(["bash", str(DEPLOY / script)], env=e, capture_output=True, text=True, timeout=60)

    def calls(self) -> list[list[str]]:
        p = self.root / "calls.jsonl"
        return [json.loads(line) for line in p.read_text().splitlines()] if p.exists() else []

    def telegrams(self) -> list[str]:
        return [next(a[5:] for a in c if a.startswith("text=")) for c in self.calls() if c[0] == "curl"]

    def store_with_a_run(self) -> None:
        con = store.connect(str(self.root / "data" / "options.sqlite"))
        store.record_run(con, {"trade_date": "2026-10-22", "status": "ok", "exit_code": 0,
                               "started_utc": "2026-10-22T05:00:00.000Z", "ended_utc": "2026-10-22T05:11:00.000Z"})
        con.close()


@pytest.fixture
def stubs(tmp_path):
    return Stubs(tmp_path)


def snapshot_runs(calls):
    return [c for c in calls if c[0] == "docker" and c[-1] == "options-snapshot"]


def test_ok_snapshot_then_backup_to_s3(stubs):
    stubs.store_with_a_run()
    r = stubs.run("run_snapshot.sh")
    assert r.returncode == 0, r.stdout + r.stderr
    calls = stubs.calls()
    assert snapshot_runs(calls) == [["docker", "compose", "run", "--rm", "-T", "options-snapshot"]]
    (key,) = [c[-1] for c in calls if c[0] == "aws"]
    assert re.fullmatch(r"s3://test-bucket/oparli/options/options-testhost-\d{8}T\d{6}Z\.sqlite\.gz", key)
    assert "--sse" in next(c for c in calls if c[0] == "aws")
    gz = stubs.root / "s3" / key[len("s3://"):]
    restored = stubs.root / "restored.sqlite"
    restored.write_bytes(gzip.decompress(gz.read_bytes()))
    con = sqlite3.connect(restored)
    assert con.execute("SELECT trade_date, status FROM option_runs").fetchall() == [("2026-10-22", "ok")]
    con.close()
    assert stubs.telegrams() == []
    state = (stubs.home / ".options-snapshot").read_text()
    assert "last_rc=0" in state and "last_ok=" in state
    assert "last_ok=" in (stubs.home / ".options-backup").read_text()


def test_holiday_is_logged_only(stubs):
    r = stubs.run("run_snapshot.sh", STUB_SNAPSHOT_RC="3")
    assert r.returncode == 3 and "skipped (COMEX closed or a CME holiday)" in r.stdout
    assert len(snapshot_runs(stubs.calls())) == 1 and stubs.telegrams() == []
    assert not [c for c in stubs.calls() if c[0] == "aws"]


def test_competing_session_is_one_notice_and_no_retry(stubs):
    (stubs.home / ".options-snapshot").write_text("last_rc=0\nlast_run=1\nlast_ok=1700000000\n")
    r = stubs.run("run_snapshot.sh", STUB_SNAPSHOT_RC="4")
    assert r.returncode == 4 and "not retried" in r.stdout
    assert len(snapshot_runs(stubs.calls())) == 1                    # one run, no retry
    (msg,) = stubs.telegrams()
    assert "10197" in msg and "Not retried" in msg
    state = (stubs.home / ".options-snapshot").read_text()
    assert "last_rc=4" in state and "last_ok=1700000000" in state    # the last ok day is kept


def test_failure_sends_one_telegram(stubs):
    r = stubs.run("run_snapshot.sh", STUB_SNAPSHOT_RC="1")
    assert r.returncode == 1
    (msg,) = stubs.telegrams()
    assert "failed (exit 1)" in msg


def test_a_second_copy_does_not_start(stubs):
    r = stubs.run("run_snapshot.sh", STUB_FLOCK_RC="1")
    assert r.returncode == 0 and "another options snapshot is running" in r.stdout
    assert stubs.calls() == []


def test_backup_failure_is_reported(stubs):
    stubs.store_with_a_run()
    r = stubs.run("run_snapshot.sh", STUB_AWS_RC="1")
    assert r.returncode == 1
    (msg,) = stubs.telegrams()
    assert msg.startswith("⚠️ options backup failed: upload to s3://test-bucket/oparli/options/")
    assert not (stubs.home / ".options-backup").exists()


def test_backup_needs_the_store(stubs):
    r = stubs.run("backup.sh")
    assert r.returncode == 1 and "snapshot of the store" in r.stdout


def test_backup_needs_an_s3_uri(stubs):
    r = stubs.run("backup.sh", BACKUP_S3_URI="")
    assert r.returncode == 1 and "BACKUP_S3_URI" in r.stdout


# --- compose and image ----------------------------------------------------------

COMPOSE = yaml.safe_load((DEPLOY / "docker-compose.yml").read_text())
SCANNER_COMPOSE = yaml.safe_load((ROOT / "deploy" / "scanner" / "docker-compose.yml").read_text())


def test_compose_is_one_shot_on_the_scanner_network():
    assert COMPOSE["name"] == "oparli-options"
    svc = COMPOSE["services"]["options-snapshot"]
    assert svc["profiles"] == ["snapshot"] and "restart" not in svc      # `up` starts nothing; cron runs it
    assert svc["networks"] == ["gateway"]
    net = COMPOSE["networks"]["gateway"]
    assert net["external"] is True and net["name"] == "${GATEWAY_NETWORK:-scanner_scannernet}"
    assert "scannernet" in SCANNER_COMPOSE["networks"]                  # project "scanner" -> scanner_scannernet
    assert svc["volumes"] == ["options_data:/data"] and svc["environment"]["OPTIONS_DB"] == "/data/options.sqlite"
    assert svc["image"] != SCANNER_COMPOSE["x-app"]["image"]             # never retags the scanner's image


def test_client_id_is_unused_by_the_scanner_stack():
    ours = COMPOSE["services"]["options-snapshot"]["environment"]["OPTIONS_IB_CLIENT_ID"]
    assert ours == "${OPTIONS_IB_CLIENT_ID:-62}"
    ids = {m for s in SCANNER_COMPOSE["services"].values() for v in (s.get("environment") or {}).values()
           for m in re.findall(r"CLIENT_ID:-(\d+)", str(v))}
    assert "62" not in ids and "61" not in ids


def test_image_holds_only_the_options_package():
    text = (DEPLOY / "Dockerfile").read_text()
    copies = re.findall(r"^COPY\s+(\S+)", text, re.M)
    assert copies == ["backend/__init__.py", "backend/options", "deploy/scanner/backup_sqlite.py"]
    assert 'ENTRYPOINT ["python", "-m", "backend.options"]' in text


def test_scripts_are_executable():
    for name in ("run_snapshot.sh", "backup.sh"):
        assert os.access(DEPLOY / name, os.X_OK), name
