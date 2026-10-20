"""Stubs for testing the deploy shell scripts without Docker, AWS or Telegram.

`stubs` makes a bin dir with fake `docker`, `aws`, `curl`, `hostname` and
`timeout`, and a root dir standing in for the containers' volumes:
    <root>/data      <-> /data       (scanner_data)
    <root>/datafeed  <-> /datafeed   (datafeed_store)
    <root>/s3/<bucket>/<key>         <-> s3://<bucket>/<key>
Every docker/aws/curl call is appended to <root>/calls.jsonl. docker inspect,
the gateway probe and redis ping read <root>/state.json.
"""

import json
import os
import shutil
import stat
import sys
from pathlib import Path

DEPLOY = Path(__file__).resolve().parents[2] / "deploy" / "scanner"
BACKUP_SQLITE = DEPLOY / "backup_sqlite.py"
EXPORT_PARQUET = DEPLOY / "export_parquet.py"

DOCKER = r'''#!PYTHON
import json, os, shutil, subprocess, sys
root = os.environ["STUB_ROOT"]
args = sys.argv[1:]
with open(os.path.join(root, "calls.jsonl"), "a") as f:
    f.write(json.dumps(["docker"] + args) + "\n")
state = json.load(open(os.path.join(root, "state.json"))) if os.path.exists(os.path.join(root, "state.json")) else {}
maps = {"/data": os.path.join(root, "data"), "/datafeed": os.path.join(root, "datafeed")}
SERVICE = None

def host(p):
    for c, h in sorted(maps.items(), key=lambda kv: -len(kv[0])):
        if p == c or p.startswith(c + "/"):
            return h + p[len(c):]
    return p

def run_in(cmd):
    if cmd == ["python", "-"]:                         # a tools/status probe fed on stdin
        sys.stdin.read()
        out = state.get({"scanner": "journal_stats", "recorder": "stream_stats"}.get(SERVICE, ""))
        if out is None:
            return 1
        print(out)
        return 0
    if cmd[:2] == ["python", "/app/backup_sqlite.py"]:
        return subprocess.call([sys.executable, "BACKUP_SQLITE"] + [host(a) for a in cmd[2:]])
    if cmd[:2] == ["python", "/app/export_parquet.py"]:
        return subprocess.call([sys.executable, "EXPORT_PARQUET"] + [host(a) for a in cmd[2:]])
    if cmd[:2] == ["rm", "-rf"]:
        for a in cmd[2:]:
            shutil.rmtree(host(a), ignore_errors=True)
        return 0
    if cmd[:2] == ["rm", "-f"]:
        for a in cmd[2:]:
            try: os.remove(host(a))
            except FileNotFoundError: pass
        return 0
    if cmd[:2] == ["test", "-f"]:
        return 0 if os.path.isfile(host(cmd[2])) else 1
    if cmd[:2] == ["redis-cli", "ping"]:
        return state.get("redis_rc", 0)
    if cmd[:1] == ["python"] and "-c" in cmd and SERVICE == "recorder":   # watchdog store stats
        print(state.get("store_stats", "0 0"))
        return 0
    if cmd[:1] == ["python"] and "-c" in cmd:          # watchdog gateway probe
        return state.get("probe_rc", 0)
    print("docker stub: unhandled", cmd, file=sys.stderr)
    return 99

if args[:1] == ["inspect"]:
    fmt, name = args[2], args[3]
    c = state.get("containers", {}).get(name)
    if c is None:
        print(f"Error: No such object: {name}", file=sys.stderr); sys.exit(1)
    if "RestartCount" in fmt:
        print(c["restarts"])
    elif "State.Status" in fmt:
        print(c.get("state", "running"))
    else:
        print(c.get("health", "healthy"))
    sys.exit(0)
if args[:1] != ["compose"]:
    sys.exit(98)
a = args[1:]
if a[0] == "exec":
    a = a[1:]
    while a and a[0].startswith("-"): a = a[1:]
    svc, cmd = a[0], a[1:]
    SERVICE = svc
    sys.exit(run_in(cmd))
if a[0] == "cp":
    src, dst = host(a[1].split(":", 1)[1]), a[2]
    if not os.path.exists(src):
        print(f"docker stub: no such path {a[1]}", file=sys.stderr); sys.exit(1)
    shutil.copytree(src, dst) if os.path.isdir(src) else shutil.copyfile(src, dst)
    sys.exit(0)
if a[0] == "run":
    a = a[1:]; ent = None
    while a and a[0].startswith("-"):
        if a[0] == "-v":
            h, c = a[1].split(":")[:2]; maps[c] = h; a = a[2:]; continue
        if a[0] == "--entrypoint":
            ent = a[1]; a = a[2:]; continue
        a = a[1:]
    svc, cmd = a[0], a[1:]
    sys.exit(run_in(([ent] if ent else []) + cmd))
if a[0] in ("stop", "up", "restart", "start"):
    sys.exit(state.get(a[0] + "_rc", 0))
sys.exit(97)
'''

AWS = r'''#!PYTHON
import json, os, shutil, sys
root = os.environ["STUB_ROOT"]
args = sys.argv[1:]
with open(os.path.join(root, "calls.jsonl"), "a") as f:
    f.write(json.dumps(["aws"] + args) + "\n")
s3 = lambda u: os.path.join(root, "s3", u[len("s3://"):])
if args[:2] == ["s3", "ls"]:
    sys.exit(0 if os.path.exists(s3(args[2])) else 1)
if os.environ.get("AWS_STUB_RC"):
    sys.exit(int(os.environ["AWS_STUB_RC"]))
paths = [x for x in args[2:] if not x.startswith("--") and x != "AES256"]
src, dst = paths
if dst.startswith("s3://"):
    os.makedirs(os.path.dirname(s3(dst)), exist_ok=True); shutil.copyfile(src, s3(dst))
else:
    shutil.copyfile(s3(src), dst)
'''

DF = r'''#!PYTHON
import json, os, sys
root = os.environ["STUB_ROOT"]
p = os.path.join(root, "state.json")
state = json.load(open(p)) if os.path.exists(p) else {}
path = sys.argv[-1]
pct = state.get("disk", {}).get(path, 40)
print("Filesystem 1024-blocks Used Available Capacity Mounted on")
print(f"/dev/root 1000 {pct * 10} {1000 - pct * 10} {pct}% {path}")
'''

CURL = r'''#!PYTHON
import json, os, sys
root = os.environ["STUB_ROOT"]
args = sys.argv[1:]
text = [a[len("text="):] for a in args if a.startswith("text=")]
url = [a for a in args if a.startswith("https://")]
with open(os.path.join(root, "calls.jsonl"), "a") as f:
    f.write(json.dumps(["curl", url[0] if url else "", text[0] if text else ""]) + "\n")
'''


def _write(path, body):
    path.write_text(body.replace("PYTHON", sys.executable).replace("BACKUP_SQLITE", str(BACKUP_SQLITE))
                    .replace("EXPORT_PARQUET", str(EXPORT_PARQUET)))
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


class Stubs:
    def __init__(self, tmp):
        self.root = tmp / "root"
        self.bin = tmp / "bin"
        self.dir = tmp / "deploy"                        # the scripts run from a copy of deploy/scanner
        for d in (self.root / "data", self.root / "datafeed", self.root / "s3", self.bin, self.dir):
            d.mkdir(parents=True)
        _write(self.bin / "docker", DOCKER)
        _write(self.bin / "aws", AWS)
        _write(self.bin / "curl", CURL)
        _write(self.bin / "df", DF)
        self.meminfo(available_pct=50)
        (self.bin / "hostname").write_text("#!/bin/sh\necho testhost\n")
        (self.bin / "timeout").write_text('#!/bin/sh\nshift\nexec "$@"\n')
        for n in ("hostname", "timeout"):
            (self.bin / n).chmod(0o755)
        for f in DEPLOY.iterdir():
            if f.suffix in (".sh", ".py"):
                shutil.copy2(f, self.dir / f.name)
        (self.dir / ".env").write_text("TELEGRAM_BOT_TOKEN=test-token  # comment\nTELEGRAM_CHAT_ID=42\n"
                                       "BACKUP_S3_URI=s3://test-bucket/oparli/scanner/\n")
        self.state = {}

    def meminfo(self, available_pct, swap_used_mb=0):
        total = 3_800_000
        (self.root / "meminfo").write_text(
            f"MemTotal: {total} kB\nMemFree: 100000 kB\nMemAvailable: {total * available_pct // 100} kB\n"
            f"SwapTotal: 2097152 kB\nSwapFree: {2097152 - swap_used_mb * 1024} kB\n")

    def env(self, **extra):
        e = {"PATH": f"{self.bin}:{os.environ['PATH']}", "STUB_ROOT": str(self.root),
             "HOME": str(self.root), "LANG": "C.UTF-8",
             "WATCHDOG_REPO": str(DEPLOY.parents[1]), "STATUS_PYTHON": sys.executable,
             "WATCHDOG_MEMINFO": str(self.root / "meminfo"), "WATCHDOG_DISK_PATHS": str(self.root)}
        e.update({k: str(v) for k, v in extra.items()})
        return e

    def set_state(self, **kw):
        self.state.update(kw)
        (self.root / "state.json").write_text(json.dumps(self.state))

    def calls(self):
        p = self.root / "calls.jsonl"
        return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []

    def telegrams(self):
        return [c[2] for c in self.calls() if c[0] == "curl"]

