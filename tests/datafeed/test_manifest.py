"""backend/datafeed/store/manifest.py, its use in ReplaySource, and the two
data tools (tools/data/build_clean_parquet.py, make_fixture.py). Synthetic
files, except the checks on the real files, which skip when they're absent."""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

from backend.datafeed.ingest import xauusd_bars as xb
from backend.datafeed.store import manifest

ROOT = Path(__file__).resolve().parents[2]
CLEAN = "database/ohlcv_data/XAUUSD_1min_UTC_clean.parquet"
TXT = "database/ohlcv_data/XAUUSD.txt"
REAL_FIXTURE = "database/ohlcv_data/XAUUSD_selftest_fixture.parquet"
SYNTHETIC = "tests/fixtures/xauusd_synthetic_selftest.parquet"
BACKFILL = "database/ohlcv_data/XAUUSD_ibkr_1min_20260201_20260930.parquet"


def _tool(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / "data" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def bars(n=5, start="2025-06-11 10:00"):
    ts = pd.date_range(start, periods=n, freq="1min", tz="UTC")
    p = [3300.0 + i for i in range(n)]
    return pd.DataFrame({"timestamp": ts, "open": p, "high": [x + 1 for x in p], "low": [x - 1 for x in p],
                         "close": p, "volume": [10.0 * (i + 1) for i in range(n)]})


@pytest.fixture
def listed(tmp_path, monkeypatch):
    """A manifest listing one synthetic parquet under the clean parquet's name."""
    path = tmp_path / "XAUUSD_1min_UTC_clean.parquet"
    bars().to_parquet(path, index=False)
    m = tmp_path / "manifest.json"
    m.write_text(json.dumps({"schema": manifest.SCHEMA, "files": {CLEAN: manifest.describe(path)}}))
    monkeypatch.setattr(manifest, "MANIFEST", m)
    manifest._verified.clear()
    return path


def test_the_shipped_manifest_lists_the_files_with_hashes():
    doc = manifest.load()
    multi = {f"database/ohlcv_data/{s}_ibkr_1min_20160101_20260930.parquet"
             for s in ("XAGUSD", "EURUSD", "USDJPY", "GBPUSD", "USDCAD", "USDSEK", "USDCHF")} | \
        {"database/ohlcv_data/DXY_PROXY_1min_20160101_20260930.parquet"}             # planned, docs/MULTI_MARKET_DATA.md
    assert set(doc["files"]) == {TXT, CLEAN, REAL_FIXTURE, SYNTHETIC, BACKFILL} | multi
    assert all(doc["files"][k].get("planned") and doc["files"][k]["status"] == "unused by any study" for k in multi)
    for e in (e for e in doc["files"].values() if not e.get("planned")):
        assert len(e.get("sha256") or e["content_sha256"]) == 64 and e["rows"] > 0 and e["first_ts"] < e["last_ts"]
        assert "source" in e and isinstance(e["in_git"], bool)
    t = doc["files"][TXT]["source"]
    assert t["provider"].startswith("IBKR") and "CMDTY" in t["contract"] and t["bar_size"] == "1 min"
    assert t["what_to_show"] is None and "unknown" in t["what_to_show_note"]          # not recorded anywhere
    assert "2026-02-01" in doc["files"][TXT]["timezone_break"]
    p = doc["files"][CLEAN]
    assert p["source"]["derived_from"] == TXT and len(p["content_sha256"]) == 64
    # the replay source's coverage constant is the manifest's range
    first, last = xb.PARQUET_COVERAGE
    assert (first.isoformat(), last.isoformat()) == (p["first_ts"], p["last_ts"])


def test_only_the_synthetic_fixture_is_in_git():
    """IBKR data is the owner's own use only: no real bars in the public repo."""
    files = manifest.load()["files"]
    assert [k for k, e in files.items() if e["in_git"]] == [SYNTHETIC]
    tracked = subprocess.run(["git", "ls-files", "--", "*.parquet", "*.txt", "*.csv"], cwd=ROOT,
                             capture_output=True, text=True, check=True).stdout.split()
    assert not [p for p in tracked if p.startswith(("database/", "tests/")) and p != SYNTHETIC
                and "XAUUSD" in p.upper()]
    for k, e in files.items():
        if not e["in_git"]:
            assert subprocess.run(["git", "check-ignore", "-q", k], cwd=ROOT).returncode == 0, k


def test_the_synthetic_fixture_regenerates_to_the_manifest():
    gen = _tool("make_synthetic_fixture")
    df = gen.frame()
    assert manifest.content_sha256(df) == manifest.load()["files"][SYNTHETIC]["content_sha256"]
    assert manifest.verify(ROOT / SYNTHETIC) == SYNTHETIC                         # the committed file, by content
    assert [w[:2] for w in gen.WINDOWS] == [tuple(w) for w in _tool("make_fixture").WINDOWS]
    # trading minutes only: no bar in the daily break or the weekend
    assert not any(not gen.is_open(t.to_pydatetime()) for t in df["timestamp"])


def test_the_planned_backfill_is_unused_and_unreadable(tmp_path):
    e = manifest.load()["files"][BACKFILL]
    assert e["planned"] is True and e["status"] == "unused by any study" and not e["in_git"]
    p = tmp_path / Path(BACKFILL).name
    bars().to_parquet(p, index=False)
    with pytest.raises(manifest.ManifestError, match="planned, not built yet"):
        manifest.verify(p)


def test_manifest_ships_as_package_data():
    import tomllib
    data = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["setuptools"]["package-data"]
    assert "store/manifest.json" in data["backend.datafeed"]


def test_content_hash_ignores_row_order_and_writer(tmp_path):
    df = bars(20)
    a, b = tmp_path / "a.parquet", tmp_path / "b.parquet"
    df.to_parquet(a, index=False)
    df.sample(frac=1, random_state=1).to_parquet(b, index=False, compression="gzip")
    assert manifest.file_sha256(a) != manifest.file_sha256(b)
    assert manifest.content_sha256(pd.read_parquet(a)) == manifest.content_sha256(pd.read_parquet(b))
    changed = df.copy()
    changed.loc[3, "close"] += 0.001
    assert manifest.content_sha256(changed) != manifest.content_sha256(df)


def test_verify_a_listed_file(listed):
    assert manifest.verify(listed, content=True) == CLEAN


def test_verify_refuses_a_different_file_with_the_listed_name(listed):
    other = bars()
    other.loc[2, "high"] += 5
    other.to_parquet(listed, index=False)                 # same name, other bars
    with pytest.raises(manifest.ManifestError, match="not the file the results were computed on"):
        manifest.verify(listed)


def test_unlisted_names_are_not_checked(tmp_path):
    p = tmp_path / "small.parquet"
    bars().to_parquet(p, index=False)
    assert manifest.verify(p) is None


def test_replay_source_checks_the_manifest_before_reading(listed):
    out = []
    xb.ReplaySource(str(listed), coverage=xb.PARQUET_COVERAGE, start="2025-06-11", end="2025-06-12").run(out.append)
    assert len(out) == 5
    bars(6).to_parquet(listed, index=False)
    with pytest.raises(manifest.ManifestError):
        xb.ReplaySource(str(listed), start="2025-06-11", end="2025-06-12").run(out.append)


def test_content_only_entries_are_checked_by_content(tmp_path, monkeypatch):
    path = tmp_path / "xauusd_synthetic_selftest.parquet"
    bars().to_parquet(path, index=False)
    d = manifest.describe(path)
    m = tmp_path / "manifest.json"
    m.write_text(json.dumps({"schema": manifest.SCHEMA, "files": {SYNTHETIC: {"content_sha256": d["content_sha256"]}}}))
    monkeypatch.setattr(manifest, "MANIFEST", m)
    bars().to_parquet(path, index=False, compression="gzip")                      # other bytes, same bars
    assert manifest.verify(path) == SYNTHETIC
    bars(6).to_parquet(path, index=False)
    with pytest.raises(manifest.ManifestError, match="content SHA-256"):
        manifest.verify(path)


def test_check_cli_reports_missing_ok_and_mismatch(listed, tmp_path, capsys):
    assert manifest.check(listed.parent) == 0
    assert "ok        " + CLEAN in capsys.readouterr().out
    assert manifest.check(tmp_path / "nowhere") == 0
    assert "missing" in capsys.readouterr().out
    bars(7).to_parquet(listed, index=False)
    manifest._verified.clear()
    assert manifest.check(listed.parent) == 1
    assert "MISMATCH" in capsys.readouterr().out


def test_the_module_imports_stdlib_only():
    code = ("import sys, backend.datafeed.store.manifest as m; m.load(); "
            "bad = [x for x in ('pandas', 'numpy', 'pyarrow') if x in sys.modules]; sys.exit(1 if bad else 0)")
    assert subprocess.run([sys.executable, "-c", code], cwd=ROOT).returncode == 0


# --- tools/data/build_clean_parquet.py ---------------------------------------------

TXT_HEADER = "should contai<TICKER>,<DTYYYYMMDD>,<TIME>,<OPEN>,<HIGH>,<LOW>,<CLOSE>,<VOL>\r\n"


def _txt(path, rows):
    path.write_bytes((TXT_HEADER + "".join(f"XAUUSD,{d},{t},{o},{h},{l},{c},{v}\r\n"
                                           for d, t, o, h, l, c, v in rows)).encode())


def test_build_keeps_2020_on_sorted_as_utc(tmp_path):
    build = _tool("build_clean_parquet")
    p = tmp_path / "x.txt"
    _txt(p, [("20191231", "235900", 1, 2, 0.5, 1.5, 5),          # before 2020: dropped
             ("20200102", "000100", 3, 4, 2, 3.5, 7),
             ("20200101", "230000", 1.5, 2.5, 1, 2, 9)])          # not sorted as stored
    df = build.build(p)
    assert [t.isoformat() for t in df["timestamp"]] == ["2020-01-01T23:00:00+00:00", "2020-01-02T00:01:00+00:00"]
    assert df["volume"].tolist() == [9.0, 7.0] and str(df["timestamp"].dtype) == "datetime64[ns, UTC]"
    assert list(df.columns) == list(manifest.CONTENT_COLUMNS)


def test_build_stops_on_a_duplicate_or_a_bad_bar(tmp_path):
    build = _tool("build_clean_parquet")
    p = tmp_path / "x.txt"
    _txt(p, [("20200102", "000100", 3, 4, 2, 3.5, 7), ("20200102", "000100", 3, 4, 2, 3.5, 7)])
    with pytest.raises(SystemExit, match="1 duplicate"):
        build.build(p)
    _txt(p, [("20200102", "000100", 3, 3.2, 2, 3.5, 7)])         # high < close
    with pytest.raises(SystemExit, match="1 bad OHLC"):
        build.build(p)


# --- tools/data/make_fixture.py -------------------------------------------------

def test_fixture_windows_cover_the_selftest_and_end_before_the_seal():
    fx = _tool("make_fixture")
    w = [(pd.Timestamp(a, tz="UTC"), pd.Timestamp(b, tz="UTC")) for a, b in fx.WINDOWS]
    need = [("2023-06-08", "2023-06-16"),                          # case 1: the prior week and 06-15
            ("2024-03-07", "2024-03-13"),
            ("2025-06-02", "2025-06-16 08:00")]                    # 14 days before 06-16, its first 8 h
    for a, b in need:
        a, b = pd.Timestamp(a, tz="UTC"), pd.Timestamp(b, tz="UTC")
        assert any(s <= a and b <= e for s, e in w), (a, b)
    assert max(e for _, e in w) <= pd.Timestamp(xb.SEAL_START)


def test_fixture_refuses_an_unlisted_source(tmp_path):
    fx = _tool("make_fixture")
    src = tmp_path / "small.parquet"
    bars().to_parquet(src, index=False)
    with pytest.raises(SystemExit):
        fx.main(["--src", str(src), "--out", str(tmp_path / "f.parquet")])


# --- the real files (untracked; skipped when absent) --------------------------------

@pytest.mark.parametrize("key", [CLEAN, TXT, REAL_FIXTURE])
def test_the_real_files_match_the_manifest(key):
    path = ROOT / key
    if not path.exists():
        pytest.skip(f"{key} not present (untracked)")
    assert manifest.verify(path, content=key.endswith(".parquet")) == key
