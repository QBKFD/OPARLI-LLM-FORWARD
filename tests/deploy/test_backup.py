"""backup_sqlite.py (snapshot / restore / check), backup.sh and restore.sh."""

import gzip
import importlib.util
from datetime import date, datetime, timedelta, timezone
import sqlite3
import subprocess
from pathlib import Path

import pytest

from tests.deploy.deploy_stubs import BACKUP_SQLITE

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
    _store(stubs.root / "datafeed" / "datafeed.sqlite", [datetime.now(timezone.utc).date() - timedelta(days=1)])
    r = _run(stubs, "backup.sh", BACKUP_STATE=stubs.root / "bk.state")
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
    assert all("--sse" in c and "AES256" in c for c in aws if c[1:3] == ["s3", "cp"])
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


@pytest.mark.parametrize("uri", [None, "test-bucket/x", "s3://"])
def test_backup_needs_an_s3_uri(stubs, uri):
    (stubs.dir / ".env").write_text("TELEGRAM_BOT_TOKEN=t\nTELEGRAM_CHAT_ID=1\n"
                                    + (f"BACKUP_S3_URI={uri}\n" if uri else ""))
    r = _run(stubs, "backup.sh", BACKUP_STATE=stubs.root / "bk.state")
    assert r.returncode == 1 and "BACKUP_S3_URI is not set to an s3:// URI" in r.stdout
    assert not (stubs.root / "bk.state").exists()


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


# ── parquet archive + success marker ────────────────────────────────────────

import importlib.util as _ilu  # noqa: E402

from tests.deploy.deploy_stubs import EXPORT_PARQUET  # noqa: E402

_spec = _ilu.spec_from_file_location("export_parquet", EXPORT_PARQUET)
ep = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(ep)


def _store(path, days, kinds=("live",), per_day=3):
    """A datafeed store with `per_day` bars per kind on each given UTC day."""
    from backend.datafeed.store import schema
    con = schema.connect(str(path))
    con.execute("BEGIN")
    for d in days:
        for k in kinds:
            for i in range(per_day):
                ts = datetime(d.year, d.month, d.day, 10, i, tzinfo=timezone.utc).isoformat()
                con.execute("INSERT INTO bars (symbol, bar_ts, kind, open, high, low, close, n_ticks, "
                            "source, schema_version, ingest_ts, recorded_ts) VALUES "
                            "('XAUUSD',?,?,1,2,0.5,1.5,?,?,1,?,?)",
                            (ts, k, 5 if k == "live" else None, "ibkr", ts, ts))
    con.execute("COMMIT")
    con.close()


def test_export_writes_completed_days_only(tmp_path):
    today = date(2026, 10, 15)
    db = tmp_path / "store.sqlite"
    _store(db, [today, today - timedelta(days=1), today - timedelta(days=3), today - timedelta(days=9)],
           kinds=("live", "history"))
    out = tmp_path / "out"
    assert ep.main([str(db), str(out), "--today", "2026-10-15"]) == 0
    files = sorted(str(p.relative_to(out)) for p in out.rglob("*.parquet"))
    assert files == ["date=2026-10-12/XAUUSD.parquet", "date=2026-10-14/XAUUSD.parquet"]   # not today, not -9
    import pandas as pd
    df = pd.read_parquet(out / files[1])
    assert list(df.columns) == ep.COLUMNS and len(df) == 6
    assert str(df["bar_ts"].dt.tz) == "UTC" and str(df["n_ticks"].dtype) == "Int64"
    assert df["n_ticks"].isna().sum() == 3                     # history bars have no tick count
    assert set(df["kind"]) == {"live", "history"}


def test_export_refuses_to_overwrite_and_needs_a_store(tmp_path):
    db = tmp_path / "store.sqlite"
    _store(db, [date(2026, 10, 14)])
    out = tmp_path / "out"
    assert ep.main([str(db), str(out), "--today", "2026-10-15"]) == 0
    with pytest.raises(SystemExit, match="refusing to overwrite"):
        ep.main([str(db), str(out), "--today", "2026-10-15"])
    assert ep.main([str(tmp_path / "missing.sqlite"), str(out)]) == 1


def _archive_keys(stubs):
    return sorted(str(p.relative_to(stubs.root / "s3")) for p in (stubs.root / "s3").rglob("*.parquet"))


def test_backup_archives_missing_days_only_and_marks_success(stubs):
    today = datetime.now(timezone.utc).date()
    d1, d2 = today - timedelta(days=1), today - timedelta(days=2)
    _db(stubs.root / "data" / "scanner_decisions.sqlite", ["A1"]).close()
    _store(stubs.root / "datafeed" / "datafeed.sqlite", [today, d1, d2])
    already = stubs.root / "s3" / "test-bucket" / "oparli" / "scanner" / "archive" / "bars" / f"date={d2}" / "XAUUSD.parquet"
    already.parent.mkdir(parents=True)
    already.write_bytes(b"uploaded earlier")
    r = _run(stubs, "backup.sh", BACKUP_STATE=stubs.root / "bk.state")
    assert r.returncode == 0, r.stdout + r.stderr
    assert _archive_keys(stubs) == sorted([f"test-bucket/oparli/scanner/archive/bars/date={d1}/XAUUSD.parquet",
                                           f"test-bucket/oparli/scanner/archive/bars/date={d2}/XAUUSD.parquet"])
    assert already.read_bytes() == b"uploaded earlier"                       # not uploaded again
    import pandas as pd
    new = stubs.root / "s3" / f"test-bucket/oparli/scanner/archive/bars/date={d1}/XAUUSD.parquet"
    assert len(pd.read_parquet(new)) == 3
    assert "archive: 1 new day(s)" in r.stdout
    assert (stubs.root / "bk.state").read_text().startswith("last_ok=")
    assert not (stubs.root / "data" / "backups" / "archive").exists()        # in-container copy removed


def test_failed_backup_does_not_mark_success(stubs):
    _db(stubs.root / "data" / "scanner_decisions.sqlite", ["A1"]).close()
    r = _run(stubs, "backup.sh", AWS_STUB_RC=1, BACKUP_STATE=stubs.root / "bk.state")
    assert r.returncode == 1 and not (stubs.root / "bk.state").exists()


def test_archive_failure_alerts(stubs):
    _db(stubs.root / "data" / "scanner_decisions.sqlite", ["A1"]).close()
    _store(stubs.root / "datafeed" / "datafeed.sqlite", [datetime.now(timezone.utc).date() - timedelta(days=1)])
    (stubs.root / "datafeed" / "datafeed.sqlite").write_bytes(b"not a database")   # export will fail
    r = _run(stubs, "backup.sh", BACKUP_STATE=stubs.root / "bk.state")
    assert r.returncode == 1
    assert stubs.telegrams()[-1].startswith("⚠️ scanner backup failed:")
