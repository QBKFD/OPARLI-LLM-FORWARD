"""tools/options_probe.py against a fake IBKR client (fake_ib.py)."""
import importlib.util
import io
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from fake_ib import Contracts, Script, world  # noqa: E402

spec = importlib.util.spec_from_file_location("options_probe", HERE.parents[1] / "tools" / "options_probe.py")
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)

UTC = timezone.utc
THU_0500 = datetime(2026, 10, 22, 5, 0, tzinfo=UTC)          # a regular Thursday, COMEX open


def run(ib, *args, now=None):
    out = io.StringIO()
    status = probe.main(list(args), ib=ib, contracts=Contracts, now=now or ib.now, out=out)
    return status, (json.loads(out.getvalue()) if out.getvalue() else None)


# --- market hours ---------------------------------------------------------------

@pytest.mark.parametrize("day", [19, 20, 21, 22, 23])                  # Mon..Fri
def test_open_at_0500_utc_on_weekdays(day):
    assert probe.closed_reason(datetime(2026, 10, day, 5, 0, tzinfo=UTC)) is None


@pytest.mark.parametrize("t, why", [
    (datetime(2026, 10, 24, 5, 0, tzinfo=UTC), "weekend"),           # Saturday
    (datetime(2026, 10, 25, 21, 0, tzinfo=UTC), "weekend"),          # Sunday 17:00 New York, before the open
    (datetime(2026, 10, 23, 21, 30, tzinfo=UTC), "weekend"),         # Friday 17:30 New York
    (datetime(2026, 10, 21, 21, 30, tzinfo=UTC), "daily"),           # Wednesday 17:30 New York
    (datetime(2026, 11, 26, 15, 0, tzinfo=UTC), "Thanksgiving"),     # trade date 2026-11-26
    (datetime(2026, 11, 25, 23, 30, tzinfo=UTC), "Thanksgiving"),    # Wed 18:30 NY opens Thanksgiving's trade date
    (datetime(2026, 12, 25, 5, 0, tzinfo=UTC), "Christmas"),
    (datetime(2028, 3, 7, 5, 0, tzinfo=UTC), "holiday calendar covers"),
])
def test_closed(t, why):
    assert why in probe.closed_reason(t)


def test_sunday_evening_is_mondays_trade_date():
    t = datetime(2026, 10, 25, 23, 0, tzinfo=UTC)                    # Sunday 19:00 New York
    assert probe.closed_reason(t) is None and probe.trade_date_of(t).isoformat() == "2026-10-26"


def test_previous_trade_date_skips_weekends_and_holidays():
    assert probe.previous_trade_date(datetime(2026, 10, 26).date()).isoformat() == "2026-10-23"
    assert probe.previous_trade_date(datetime(2026, 11, 27).date()).isoformat() == "2026-11-25"


def test_refuses_when_closed_before_connecting(capsys):
    ib = world(datetime(2026, 10, 24, 5, 0, tzinfo=UTC))
    status, doc = run(ib)
    assert status == 3 and doc is None and ib.connected is None and not ib.subscribed
    assert "refusing to run" in capsys.readouterr().err


# --- a full run --------------------------------------------------------------------

def test_full_run():
    ib = world(THU_0500, scripts={1023: Script(answers="frozen", values={"bid": 5.0, "callOpenInterest": 7.0}),
                                  1024: Script(answers="never")})
    status, doc = run(ib, "--batch", "7")
    assert status == 0
    assert ib.connected == dict(host="127.0.0.1", port=4004, clientId=61, readonly=True)
    assert ib.disconnected and not ib.active and ib.max_active <= 7 and doc["max_concurrent_lines"] <= 7
    # the first expiry is less than 2 days away; the next 3 are taken
    assert doc["selected_expiries"] == ["20261027", "20261124", "20261228"]
    # underlying by underConId: October and November on GCZ6, December on GCG7
    und = {o["expiry"]: o["under_con_id"] for o in doc["options"]}
    assert und == {"20261027": 100, "20261124": 100, "20261228": 101}
    assert [u["local_symbol"] for u in doc["underlyings"]] == ["GCZ6", "GCG7"]
    assert len(doc["options"]) == 3 * 3 * 2
    # live first, then frozen for the silent ones only
    assert ib.data_types_requested == [1, 2]
    frozen_subs = [c for c, _, t in ib.subscribed if t == 2]
    assert sorted(frozen_subs) == [1023, 1024]
    by_con = {o["con_id"]: o for o in doc["options"]}
    assert by_con[1023]["data_type"] == "frozen" and by_con[1023]["ib_market_data_type"] == 2
    assert by_con[1023]["fields"]["open_interest"]["value"] == 7.0
    assert by_con[1024]["data_type"] == "none" and by_con[1024]["fields"] == {}
    live = by_con[1013]
    assert live["data_type"] == "live" and live["ib_market_data_type"] == 1
    f = live["fields"]
    assert f["open_interest"]["value"] == 500.0 and f["model_gamma"]["value"] == 0.0015
    assert f["model_iv"]["value"] == 0.25 and f["model_und_price"]["value"] == 4150.0
    assert f["bid"]["t_first"].startswith("2026-10-22T05:00:") and f["bid"]["t_first"].endswith("Z")
    # generic ticks: 101 for options, 588 for futures
    ticks = {c: g for c, g, _ in ib.subscribed}
    assert ticks[100] == "588" and ticks[1013] == "101"
    assert doc["underlyings"][0]["fields"]["open_interest"]["value"] == 322_500.0
    # GC daily bars for every listed month, the expiry and dates recorded
    assert [b["local_symbol"] for b in doc["gc_daily_bars"]] == ["GCZ6", "GCG7", "GCJ7"]
    assert len(doc["gc_daily_bars"][0]["bars"]) == 22
    assert doc["trade_date"] == "2026-10-22" and doc["oi_settlement_date_inferred"] == "2026-10-21"
    assert {c["trading_class"] for c in doc["chains"]} == {"OG", "G1W"}
    assert doc["options"][0]["last_trade_time"] == "12:30:00" and doc["options"][0]["time_zone_id"] == "US/Central"


def test_10197_competing_session_exits_4_and_cancels_everything(capsys):
    ib = world(THU_0500, scripts={1015: Script(error=10197)})
    status, doc = run(ib)
    assert status == 4 and doc is None
    assert not ib.active and ib.disconnected
    assert "competing live session" in capsys.readouterr().err


def test_line_limit_error_exits_5():
    ib = world(THU_0500, scripts={1015: Script(error=101)})
    status, doc = run(ib)
    assert status == 5 and doc is None and not ib.active and ib.disconnected


def test_batch_must_stay_under_the_line_limit():
    with pytest.raises(SystemExit) as e:
        run(world(THU_0500), "--batch", "200")
    assert e.value.code == 2


def test_not_enough_expiries():
    ib = world(THU_0500, expiries=("20261023", "20261027"))
    status, doc = run(ib)
    assert status == 1 and doc is None and ib.disconnected


def test_writes_to_a_file(tmp_path):
    p, ib = tmp_path / "probe.json", world(THU_0500)
    assert probe.main(["--out", str(p)], ib=ib, contracts=Contracts, now=ib.now) == 0
    assert json.loads(p.read_text())["schema"] == "options_probe/1"
