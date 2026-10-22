"""backend.options (the daily OG snapshot) against the fake IBKR client of
research/gex_feasibility/fake_ib.py: GC futures listed month by month, as IBKR
lists them, with OG chains on only some months. No network."""
import importlib.util
import json
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from backend.options import snapshot, store

_spec = importlib.util.spec_from_file_location(
    "options_fake_ib", Path(__file__).resolve().parents[2] / "research" / "gex_feasibility" / "fake_ib.py")
fake_ib = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = fake_ib                             # dataclasses look their module up
_spec.loader.exec_module(fake_ib)
Contracts, Script, world = fake_ib.Contracts, fake_ib.Script, fake_ib.world

UTC = timezone.utc
THU_0500 = datetime(2026, 10, 22, 5, 0, tzinfo=UTC)          # a regular Thursday, COMEX open
STRIKES = tuple(4000.0 + 50 * i for i in range(10))           # 3 expiries x 10 strikes x 2 rights = 60 options


def run(db, ib, *args, now=None):
    return snapshot.main(["--db", str(db), *args], ib=ib, contracts=Contracts, now=now or ib.now)


def q(db, sql, *p):
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in con.execute(sql, p)]
    finally:
        con.close()


@pytest.fixture
def db(tmp_path):
    return tmp_path / "data" / "options.sqlite"


def test_full_run_stores_every_contract(db):
    ib = world(THU_0500, strikes=STRIKES,
               scripts={1023: Script(answers="frozen", values={"bid": 5.0, "callOpenInterest": 7.0}),
                        1024: Script(answers="never")})
    assert run(db, ib) == 0
    # read-only, client id 62, 50 lines at most, everything cancelled, disconnected
    assert ib.connected == dict(host="127.0.0.1", port=4004, clientId=62, readonly=True)
    assert ib.disconnected and not ib.active and 50 >= ib.max_active > 7
    assert sorted(ib.cancelled) == sorted(c for c, _, _ in ib.subscribed)
    # chains asked month by month: GCG7, the 5th listed month, holds the December expiry
    (r,) = q(db, "SELECT * FROM option_runs")
    assert (r["status"], r["exit_code"], r["reason"], r["trade_date"]) == ("ok", 0, None, "2026-10-22")
    assert r["oi_settlement_date"] == "2026-10-21" and r["client_id"] == 62
    assert json.loads(r["chain_futures_queried"]) == ["GCV6", "GCX6", "GCZ6", "GCF7", "GCG7"]
    assert json.loads(r["selected_expiries"]) == ["20261027", "20261124", "20261228"]
    assert r["expiry_fallback"] is None and r["max_concurrent_lines"] == ib.max_active
    assert json.loads(r["params"])["batch"] == 50
    assert (r["n_options"], r["n_live"], r["n_frozen"], r["n_none"]) == (60, 58, 1, 1)
    assert r["started_utc"] == "2026-10-22T05:00:00.000Z" and r["ended_utc"] > r["started_utc"]

    opts = {o["con_id"]: o for o in q(db, "SELECT * FROM option_quotes")}
    assert len(opts) == 60
    # underlying by conId: Oct and Nov on GCZ6 (100), Dec on GCG7 (101), from details and from the chain
    assert {(o["expiry"], o["under_con_id"], o["chain_under_con_id"]) for o in opts.values()} == \
        {("20261027", 100, 100), ("20261124", 100, 100), ("20261228", 101, 101)}
    live = next(o for o in opts.values() if o["data_type"] == "live" and o["right"] == "C")
    assert live["ib_market_data_type"] == 1
    assert (live["open_interest"], live["iv"], live["gamma"], live["delta"], live["vega"]) == (500.0, 0.25, 0.0015, 0.5, 1.0)
    assert (live["bid"], live["ask"], live["und_price"], live["opt_price"]) == (10.0, 11.0, 4150.0, 10.5)
    assert live["last_trade_time"] == "12:30:00" and live["time_zone_id"] == "US/Central"
    times = json.loads(live["field_times"])
    assert set(times) >= {"open_interest", "model_iv", "model_gamma", "model_delta", "model_vega", "bid", "ask"}
    assert live["first_tick_utc"].startswith("2026-10-22T05:") and live["first_tick_utc"].endswith("Z")
    assert all(t0 <= t1 for t0, t1 in times.values())
    # live first, frozen only for the contracts with no live tick
    assert ib.data_types_requested == [1, 2]
    assert sorted(c for c, _, t in ib.subscribed if t == 2) == [1023, 1024]
    assert (opts[1023]["data_type"], opts[1023]["ib_market_data_type"], opts[1023]["open_interest"]) == ("frozen", 2, 7.0)
    assert opts[1023]["iv"] is None and opts[1024]["data_type"] == "none" and opts[1024]["field_times"] == "{}"
    # generic ticks: 101 for options, 588 for the futures
    ticks = {c: g for c, g, _ in ib.subscribed}
    assert ticks[100] == "588" and ticks[1021] == "101"

    unds = q(db, "SELECT * FROM underlying_quotes ORDER BY con_id")
    assert [(u["con_id"], u["local_symbol"], u["expiry"]) for u in unds] == [(100, "GCZ6", "20261229"),
                                                                            (101, "GCG7", "20270224")]
    assert (unds[0]["bid"], unds[0]["ask"], unds[0]["last"], unds[0]["close"], unds[0]["open_interest"]) == \
        (4149.9, 4150.1, 4150.0, 4140.0, 322_500.0)
    assert unds[0]["data_type"] == "live"
    assert len(q(db, "SELECT * FROM ok_option_quotes WHERE trade_date = '2026-10-22'")) == 60


def test_competing_live_session_is_a_skipped_day_not_a_retry(db, capsys):
    ib = world(THU_0500, strikes=STRIKES, scripts={1030: Script(error=10197)})
    assert run(db, ib) == 4
    assert not ib.active and ib.disconnected
    assert ib.data_types_requested == [1]                     # stopped in the first pass; nothing asked again
    (r,) = q(db, "SELECT * FROM option_runs")
    assert (r["status"], r["exit_code"]) == ("skipped", 4) and "10197" in r["reason"]
    assert "competing live session" in r["reason"] and 10197 in [m["code"] for m in json.loads(r["ib_messages"])]
    assert q(db, "SELECT COUNT(*) n FROM option_quotes")[0]["n"] == 0
    assert "skipped (exit 4)" in capsys.readouterr().err
    # the same day can still be taken by hand later, once the live session is logged out
    assert run(db, world(THU_0500.replace(hour=6))) == 0
    assert [r["status"] for r in q(db, "SELECT status FROM option_runs ORDER BY run_id")] == ["skipped", "ok"]


@pytest.mark.parametrize("t, why", [
    (datetime(2026, 11, 26, 5, 0, tzinfo=UTC), "Thanksgiving"),
    (datetime(2026, 12, 25, 5, 0, tzinfo=UTC), "Christmas"),
    (datetime(2027, 1, 18, 5, 0, tzinfo=UTC), "Martin Luther King"),
    (datetime(2026, 10, 24, 5, 0, tzinfo=UTC), "weekend"),
])
def test_closed_days_are_skipped_without_connecting(db, t, why):
    ib = world(t)
    assert run(db, ib) == 3
    assert ib.connected is None and not ib.subscribed
    (r,) = q(db, "SELECT * FROM option_runs")
    assert r["status"] == "skipped" and why in r["reason"]


def test_holiday_calendar_running_out_is_a_failure(db):
    ib = world(datetime(2028, 3, 7, 5, 0, tzinfo=UTC))
    assert run(db, ib) == 1 and ib.connected is None
    (r,) = q(db, "SELECT * FROM option_runs")
    assert r["status"] == "failed" and "update HOLIDAYS" in r["reason"]


def test_a_second_run_on_the_same_trade_date_requests_nothing(db):
    assert run(db, world(THU_0500)) == 0
    ib = world(THU_0500.replace(hour=7))
    assert run(db, ib) == 0 and ib.connected is None
    runs = q(db, "SELECT status, reason FROM option_runs ORDER BY run_id")
    assert [r["status"] for r in runs] == ["ok", "skipped"] and "already has an ok snapshot (run 1)" in runs[1]["reason"]
    assert q(db, "SELECT COUNT(DISTINCT run_id) n FROM option_quotes")[0]["n"] == 1


def test_line_limit_fails_with_exit_5(db):
    ib = world(THU_0500, scripts={1015: Script(error=101)})
    assert run(db, ib) == 5 and not ib.active and ib.disconnected
    assert q(db, "SELECT status, exit_code FROM option_runs") == [{"status": "failed", "exit_code": 5}]


def test_no_gateway_is_a_failed_day(db):
    ib = world(THU_0500)

    def refuse(*a, **k):
        raise ConnectionRefusedError(111, "Connection refused")
    ib.connect = refuse
    assert run(db, ib) == 1 and ib.disconnected
    (r,) = q(db, "SELECT * FROM option_runs")
    assert r["status"] == "failed" and r["reason"].startswith("ConnectionRefusedError")


def test_too_few_expiries_fails(db):
    ib = world(THU_0500, expiries=("20261023", "20261027"))
    assert run(db, ib) == 1 and not ib.subscribed
    assert "only 1 OG expiries" in q(db, "SELECT reason FROM option_runs")[0]["reason"]


def test_two_expiries_fall_back_and_are_recorded(db):
    ib = world(THU_0500, expiries=("20261023", "20261027", "20261124"))
    assert run(db, ib) == 0
    (r,) = q(db, "SELECT * FROM option_runs")
    assert json.loads(r["selected_expiries"]) == ["20261027", "20261124"] and "using 2" in r["expiry_fallback"]


def test_batch_must_stay_under_the_line_limit(db):
    with pytest.raises(SystemExit) as e:
        run(db, world(THU_0500), "--batch", "200")
    assert e.value.code == 2


def test_store_migration_is_idempotent(db):
    store.connect(str(db)).close()
    con = store.connect(str(db))
    assert [r[0] for r in con.execute("SELECT version FROM schema_version")] == [1]
    assert con.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    con.close()


def test_one_ok_run_per_trade_date_is_enforced_by_the_schema(db):
    con = store.connect(str(db))
    run_row = {"trade_date": "2026-10-22", "status": "ok", "exit_code": 0, "started_utc": "a", "ended_utc": "b"}
    store.record_run(con, run_row)
    with pytest.raises(sqlite3.IntegrityError):
        store.record_run(con, run_row)
    store.record_run(con, {**run_row, "status": "skipped", "reason": "x"})
    con.close()


def test_the_package_imports_no_other_backend_package_and_no_ib_insync():
    code = ("import sys, backend.options.snapshot; "
            "bad = [m for m in sys.modules if m.startswith('backend.') and not m.startswith('backend.options')"
            " or m.startswith('ib_insync')]; print(bad); sys.exit(1 if bad else 0)")
    root = Path(__file__).resolve().parents[2]
    assert subprocess.run([sys.executable, "-c", code], cwd=root).returncode == 0


def test_list_runs_prints_every_outcome(db, capsys):
    assert run(db, world(THU_0500)) == 0
    assert run(db, world(datetime(2026, 11, 26, 5, 0, tzinfo=UTC))) == 3
    capsys.readouterr()
    assert snapshot.main(["--db", str(db), "--list-runs", "5"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].startswith("run 1 2026-10-22 ok exit 0 ") and "18 options (18 live, 0 frozen, 0 none)" in lines[0]
    assert lines[1].startswith("run 2 2026-11-26 skipped exit 3 ") and "Thanksgiving" in lines[1]


def test_list_runs_without_a_store(tmp_path):
    assert snapshot.main(["--db", str(tmp_path / "none.sqlite"), "--list-runs", "3"]) == 1
    assert not (tmp_path / "none.sqlite").exists()
