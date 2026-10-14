"""backup_sqlite.py (snapshot / restore / check), backup.sh and restore.sh."""

import gzip
import importlib.util
import sqlite3
import subprocess
from pathlib import Path

import pytest

from deploy_stubs import BACKUP_SQLITE

spec = importlib.util.spec_from_file_location("backup_sqlite", BACKUP_SQLITE)
bs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bs)


def _db(path, rows, wal=False, table="alerts"):
    con = sqlite3.connect(str(path))
    if wal:
        con.execute("PRAGMA journal_mode=WAL")
    con.execute(f"CREATE TABLE IF NOT EXISTS {table}(alert_id TEXT)")
    con.executemany(f"INSERT INTO {table} VALUES(?)", [(r,) for r in rows])
    con.commit()
    return con


def _ids(path, table="alerts"):
    con = sqlite3.connect(str(path))
    try:
        return [r[0] for r in con.execute(f"SELECT alert_id FROM {table} ORDER BY rowid")]
    finally:
        con.close()


# ── backup_sqlite.py ────────────────────────────────────────────────────────
def test_snapshot_of_a_wal_db_with_a_live_writer(tmp_path):
    src = tmp_path / "store.sqlite"
    writer = _db(src, ["a", "b"], wal=True, table="bars")
    writer.execute("BEGIN")
    writer.execute("INSERT INTO bars VALUES('uncommitted')")      # open transaction
    dst = tmp_path / "out" / "snap.sqlite"
    bs.snapshot(str(src), str(dst), "bars")
    writer.rollback()
    assert _ids(dst, "bars") == ["a", "b"]                        # committed rows only
    con = sqlite3.connect(str(dst))
    assert con.execute("PRAGMA journal_mode").fetchone()[0] == "delete"   # self-contained file
    con.close()
    assert not Path(str(dst) + ".partial").exists()


def test_snapshot_refuses_to_overwrite_and_needs_a_source(tmp_path):
    src = tmp_path / "j.sqlite"
    _db(src, ["a"]).close()
    dst = tmp_path / "snap.sqlite"
    dst.write_text("keep me")
    with pytest.raises(bs.BackupError, match="refusing to overwrite"):
        bs.snapshot(str(src), str(dst))
    assert dst.read_text() == "keep me"
    with pytest.raises(bs.BackupError, match="no such file"):
        bs.snapshot(str(tmp_path / "missing.sqlite"), str(tmp_path / "x.sqlite"))


def test_snapshot_checks_the_expected_table(tmp_path):
    src = tmp_path / "j.sqlite"
    _db(src, ["a"]).close()
    with pytest.raises(bs.BackupError, match="no table 'bars'"):
        bs.snapshot(str(src), str(tmp_path / "snap.sqlite"), "bars")
    assert not (tmp_path / "snap.sqlite").exists() and not (tmp_path / "snap.sqlite.partial").exists()


def test_restore_keeps_the_old_file_and_drops_its_wal(tmp_path):
    from datetime import datetime, timezone
    backup = tmp_path / "backup.sqlite"
    _db(backup, ["old-1", "old-2"]).close()
    dst = tmp_path / "data" / "scanner_decisions.sqlite"
    dst.parent.mkdir()
    live = _db(dst, ["new-1", "new-2", "new-3"], wal=True)
    live.execute("PRAGMA wal_autocheckpoint=0")
    live.execute("INSERT INTO alerts VALUES('only-in-wal')")
    live.commit()
    live.close()                                              # may checkpoint; recreate a stale WAL below
    Path(str(dst) + "-wal").write_bytes(b"stale wal that must not be replayed")
    kept = bs.restore(str(backup), str(dst), "alerts", now=datetime(2026, 10, 15, 3, 0, tzinfo=timezone.utc))
    assert kept == f"{dst}.pre-restore-20261015T030000Z"
    assert _ids(dst) == ["old-1", "old-2"]
    assert not Path(str(dst) + "-wal").exists() and not Path(str(dst) + "-shm").exists()
    assert _ids(kept) == ["new-1", "new-2", "new-3", "only-in-wal"]


@pytest.mark.parametrize("bad", ["corrupt", "wrong-table", "missing"])
def test_restore_refuses_a_bad_backup_and_leaves_the_target(tmp_path, bad):
    dst = tmp_path / "scanner_decisions.sqlite"
    _db(dst, ["live"]).close()
    src = tmp_path / "backup.sqlite"
    if bad == "corrupt":
        src.write_bytes(b"SQLite format 3\x00" + b"\x00" * 50 + b"garbage" * 200)
    elif bad == "wrong-table":
        _db(src, ["x"], table="bars").close()
    with pytest.raises(bs.BackupError):
        bs.restore(str(src), str(dst), "alerts")
    assert _ids(dst) == ["live"]
    assert not list(tmp_path.glob("*.pre-restore-*"))


def test_cli_exit_codes(tmp_path):
    src = tmp_path / "j.sqlite"
    _db(src, ["a"]).close()
    ok = subprocess.run(["python3", str(BACKUP_SQLITE), "check", str(src), "--expect-table", "alerts"],
                        capture_output=True, text=True)
    bad = subprocess.run(["python3", str(BACKUP_SQLITE), "check", str(tmp_path / "nope.sqlite")],
                         capture_output=True, text=True)
    assert ok.returncode == 0 and ": ok" in ok.stdout
    assert bad.returncode == 1 and "no such file" in bad.stderr


# ── backup.sh ───────────────────────────────────────────────────────────────
def _run(stubs, script, *args, **env):
    return subprocess.run(["bash", str(stubs.dir / script), *args], env=stubs.env(**env),
                          capture_output=True, text=True, timeout=60)


def _s3_objects(stubs):
    return sorted(str(p.relative_to(stubs.root / "s3")) for p in (stubs.root / "s3").rglob("*.gz"))


def test_backup_uploads_the_journal_and_the_store(stubs):
    _db(stubs.root / "data" / "scanner_decisions.sqlite", ["A1", "A2"]).close()
    _db(stubs.root / "datafeed" / "datafeed.sqlite", ["b1"], wal=True, table="bars").close()
    r = _run(stubs, "backup.sh")
    assert r.returncode == 0, r.stdout + r.stderr
    objs = _s3_objects(stubs)
    assert len(objs) == 2
    assert objs[0].startswith("test-bucket/oparli/scanner/journal/journal-testhost-")
    assert objs[1].startswith("test-bucket/oparli/scanner/store/store-testhost-")
    # the uploaded journal is a valid SQLite file with the rows
    out = stubs.root / "restored.sqlite"
    out.write_bytes(gzip.decompress((stubs.root / "s3" / objs[0]).read_bytes()))
    assert _ids(out) == ["A1", "A2"]
    aws = [c for c in stubs.calls() if c[0] == "aws"]
    assert all("--sse" in c and "AES256" in c for c in aws)
    assert not any(c[1:3] == ["s3", "rm"] for c in aws)               # never deletes from S3
    assert list((stubs.root / "data" / "backups").iterdir()) == []    # in-container copy removed
    assert stubs.telegrams() == []


def test_backup_without_the_stream_profile_is_journal_only(stubs):
    _db(stubs.root / "data" / "scanner_decisions.sqlite", ["A1"]).close()
    r = _run(stubs, "backup.sh")
    assert r.returncode == 0 and "journal only" in r.stdout
    assert len(_s3_objects(stubs)) == 1


def test_backup_failure_alerts_and_exits_nonzero(stubs):
    _db(stubs.root / "data" / "scanner_decisions.sqlite", ["A1"]).close()
    r = _run(stubs, "backup.sh", AWS_STUB_RC=1)
    assert r.returncode == 1 and "backup FAILED: journal" in r.stdout
    tg = stubs.telegrams()
    assert len(tg) == 1 and "scanner backup failed: journal" in tg[0]
    assert "test-token" not in tg[0]                                  # the token is in the URL only


def test_backup_needs_a_bucket(stubs):
    (stubs.dir / ".env").write_text("TELEGRAM_BOT_TOKEN=t\nTELEGRAM_CHAT_ID=1\n")
    r = _run(stubs, "backup.sh")
    assert r.returncode == 1 and "BACKUP_S3_BUCKET is not set" in r.stdout


# ── restore.sh ──────────────────────────────────────────────────────────────
def _put_backup(stubs, kind, rows, table):
    tmp = stubs.root / f"{kind}-backup.sqlite"
    _db(tmp, rows, table=table).close()
    key = stubs.root / "s3" / "test-bucket" / "p" / kind / f"{kind}-x.sqlite.gz"
    key.parent.mkdir(parents=True, exist_ok=True)
    key.write_bytes(gzip.compress(tmp.read_bytes()))
    return f"s3://test-bucket/p/{kind}/{kind}-x.sqlite.gz"


def test_restore_dry_run_changes_nothing(stubs):
    live = stubs.root / "data" / "scanner_decisions.sqlite"
    _db(live, ["live"]).close()
    uri = _put_backup(stubs, "journal", ["backed-up"], "alerts")
    r = _run(stubs, "restore.sh", "journal", uri)
    assert r.returncode == 0 and "dry run: nothing changed" in r.stdout
    assert _ids(live) == ["live"]
    assert not any(c[:3] == ["docker", "compose", "stop"] for c in stubs.calls())


@pytest.mark.parametrize("kind, target, table, services", [
    ("journal", "data/scanner_decisions.sqlite", "alerts", ["scanner"]),
    ("store", "datafeed/datafeed.sqlite", "bars", ["recorder", "scanner"])])
def test_restore_stops_restores_and_starts(stubs, kind, target, table, services):
    live = stubs.root / target
    _db(live, ["live"], table=table).close()
    uri = _put_backup(stubs, kind, ["backed-up"], table)
    r = _run(stubs, "restore.sh", kind, uri, "--yes")
    assert r.returncode == 0, r.stdout + r.stderr
    assert _ids(live, table) == ["backed-up"]
    assert len(list(live.parent.glob(live.name + ".pre-restore-*"))) == 1
    docker = [c[1:] for c in stubs.calls() if c[0] == "docker"]
    stop = docker.index(["compose", "stop", *services])
    restore = next(i for i, c in enumerate(docker) if "restore" in c)
    up = docker.index(["compose", "up", "-d", *services])
    assert stop < restore < up


def test_restore_failure_starts_the_services_again(stubs):
    live = stubs.root / "data" / "scanner_decisions.sqlite"
    _db(live, ["live"]).close()
    uri = _put_backup(stubs, "journal", ["x"], "bars")                   # wrong table: check fails
    r = _run(stubs, "restore.sh", "journal", uri, "--yes")
    assert r.returncode != 0 and _ids(live) == ["live"]
    assert not any(c[:3] == ["docker", "compose", "stop"] for c in stubs.calls())   # failed before stopping


@pytest.mark.parametrize("args", [[], ["journal"], ["nope", "s3://b/k.sqlite.gz"],
                                  ["journal", "/tmp/local.sqlite.gz"], ["journal", "s3://b/k.sqlite.gz", "-y"]])
def test_restore_usage_errors(stubs, args):
    r = _run(stubs, "restore.sh", *args)
    assert r.returncode == 2 and "usage:" in r.stderr
