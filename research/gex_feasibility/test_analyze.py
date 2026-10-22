"""analyze.py: R, the gates, the verdict and the report, on probe files made by
tools/options_probe.py against the fake IBKR client."""
import copy
import io
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import analyze  # noqa: E402
from fake_ib import Contracts, world  # noqa: E402
from gex import black76_gamma, dollar_gamma_1pct  # noqa: E402

UTC = timezone.utc
THU_0500 = datetime(2026, 10, 22, 5, 0, tzinfo=UTC)
probe = analyze.probe_module()


def probe_doc(ib=None) -> dict:
    ib = ib or world(THU_0500)
    out = io.StringIO()
    assert probe.main([], ib=ib, contracts=Contracts, now=ib.now, out=out) == 0
    return consistent(json.loads(out.getvalue()))


def consistent(doc: dict) -> dict:
    """Give every option the IBKR gamma Black-76 implies for its own IV and undPrice
    (the fake's constant 0.0015 isn't a real gamma)."""
    snap = analyze.parse_utc(doc["snapshot_start_utc"])
    for o in doc["options"]:
        f = o["fields"]
        if "model_gamma" in f:
            t = (analyze.expiry_utc(o)[0] - snap).total_seconds() / analyze.YEAR_S
            f["model_gamma"]["value"] = black76_gamma(f["model_und_price"]["value"], o["strike"], t, f["model_iv"]["value"])
    return doc


def scale_oi(doc: dict, k: float) -> dict:
    doc = copy.deepcopy(doc)
    for o in doc["options"]:
        if "open_interest" in o["fields"]:
            o["fields"]["open_interest"]["value"] *= k
    return doc


@pytest.fixture(scope="module")
def base():
    return probe_doc()


# --- R by hand -----------------------------------------------------------------------

def test_r_equals_the_hand_computation(base):
    a = analyze.analyze(base)
    snap = analyze.parse_utc(base["snapshot_start_utc"])
    f = (4149.9 + 4150.1) / 2                                         # mid of the underlying's bid and ask
    gex = 0.0
    for o in base["options"]:
        t = (analyze.expiry_utc(o)[0] - snap).total_seconds() / (365 * 86400)
        g = black76_gamma(f, o["strike"], t, 0.25)
        gex += dollar_gamma_1pct(g, f, 500.0)
    # D: GCZ6 150,000 contracts + the 6 other listed months 1,000 each, at 4,150, 100 oz; last 20 complete days
    d = (150_000 + 6 * 1_000) * 100 * 4150.0
    assert a["abs_gex"] == pytest.approx(gex, rel=1e-12)
    assert a["d"] == pytest.approx(d) and len(a["days"]) == 20
    assert a["r"] == pytest.approx(gex / d)
    assert a["gates_ok"], a["gates"]


@pytest.mark.parametrize("target, verdict", [(0.005, "GEX DROPPED"), (0.03, "IN BETWEEN"), (0.08, "BUILD")])
def test_verdict_follows_the_thresholds(base, target, verdict):
    r0 = analyze.analyze(base)["r"]
    a = analyze.analyze(scale_oi(base, target / r0))
    assert a["r"] == pytest.approx(target) and a["verdict"].startswith(verdict)


def test_thresholds_are_inclusive_in_the_middle():
    assert analyze.verdict_of(0.01).startswith("IN BETWEEN") and analyze.verdict_of(0.05).startswith("IN BETWEEN")
    assert analyze.verdict_of(0.00999).startswith("GEX DROPPED") and analyze.verdict_of(0.0501).startswith("BUILD")


# --- gates ---------------------------------------------------------------------------

def _fails(doc, gate):
    a = analyze.analyze(doc)
    assert not a["gates"][gate][0], a["gates"][gate]
    assert a["verdict"].startswith("INCONCLUSIVE")
    return a


def test_g1_outside_trading_hours(base):
    doc = copy.deepcopy(base)
    doc["snapshot_start_utc"] = "2026-10-24T05:00:00.000Z"            # Saturday
    _fails(doc, "G1")


def test_g2_needs_20_complete_days(base):
    doc = copy.deepcopy(base)
    for m in doc["gc_daily_bars"]:
        m["bars"] = m["bars"][-19:]
    _fails(doc, "G2")


def test_d_ignores_the_current_trade_date(base):
    doc = copy.deepcopy(base)
    doc["gc_daily_bars"][0]["bars"].append({"date": "2026-10-22", "close": 4150.0, "volume": 9e9})
    assert analyze.analyze(doc)["d"] == pytest.approx(analyze.analyze(base)["d"])


def test_g3_open_interest_coverage(base):
    doc = copy.deepcopy(base)
    for o in doc["options"][:2]:                                      # 16 of 18 = 88.9 % < 90 %
        del o["fields"]["open_interest"]
    _fails(doc, "G3")
    doc = copy.deepcopy(base)
    del doc["options"][0]["fields"]["open_interest"]                  # 17 of 18 = 94.4 %
    assert analyze.analyze(doc)["gates"]["G3"][0]


def test_g4_filled_iv_share(base):
    doc = copy.deepcopy(base)
    for o in doc["options"][:5]:                                      # 5 of 18 equal OI = 27.8 % > 25 %
        del o["fields"]["model_iv"]
    a = _fails(doc, "G4")
    assert "5 contracts" in a["gates"]["G4"][1]


def test_g5_our_gamma_must_match_ibkr(base):
    doc = copy.deepcopy(base)
    for o in doc["options"]:
        o["fields"]["model_gamma"]["value"] *= 1.2                    # |1/1.2 - 1| = 16.7 %
    _fails(doc, "G5")


def test_g6_needs_an_underlying_price(base):
    doc = copy.deepcopy(base)
    doc["underlyings"][1]["fields"] = {}                              # GCG7: the December expiry has no F
    a = _fails(doc, "G6")
    assert "20261228" in a["gates"]["G6"][1]


# --- pieces ---------------------------------------------------------------------------

def test_iv_fill_takes_the_nearest_strike_and_the_lower_on_a_tie():
    opts = [dict(expiry="E", strike=4000.0, iv=0.20, oi=1), dict(expiry="E", strike=4100.0, iv=None, oi=5),
            dict(expiry="E", strike=4200.0, iv=0.30, oi=1), dict(expiry="E", strike=4250.0, iv=None, oi=0),
            dict(expiry="F", strike=4100.0, iv=None, oi=5)]
    analyze.fill_iv(opts)
    assert opts[1]["iv"] == 0.20 and opts[1]["iv_filled"] and opts[1]["iv_from_strike"] == 4000.0
    assert opts[3]["iv"] is None and not opts[3]["iv_filled"]         # no open interest: no IV needed
    assert opts[4]["iv"] is None                                      # no IV in that expiry


@pytest.mark.parametrize("fields, price, src", [
    (dict(bid=10.0, ask=12.0, last=11.5), 11.0, "mid"),
    (dict(bid=10.0, last=11.5, close=9.0), 11.5, "last"),
    (dict(bid=-1.0, ask=12.0, close=9.0), 9.0, "close"),
    ({}, None, "none"),
])
def test_underlying_price(fields, price, src):
    u = {"fields": {k: {"value": v} for k, v in fields.items()}}
    assert analyze.underlying_price(u) == (price, src)


@pytest.mark.parametrize("o, want", [
    (dict(real_expiration_date="20261124", last_trade_time="12:30:00", time_zone_id="US/Central"),
     "2026-11-24T18:30:00+00:00"),
    (dict(real_expiration_date="20261124", last_trade_time="20261124:1230", time_zone_id="US/Central"),
     "2026-11-24T18:30:00+00:00"),
    (dict(expiry="20261124", real_expiration_date="", last_trade_time="", time_zone_id=""),
     "2026-11-24T18:30:00+00:00"),
])
def test_expiry_time(o, want):
    o = {"expiry": "20261124", **o}
    assert analyze.expiry_utc(o)[0].isoformat() == want


def test_descriptives(base):
    a = analyze.analyze(base)
    assert a["data_types"] == {"live": 18, "frozen": 0, "none": 0}
    assert a["total_oi"] == 18 * 500 and a["top10_share"] == pytest.approx(1.0)   # only 3 strikes
    assert [k for k, _ in a["top10"]] == [4000.0, 4100.0, 4200.0]
    assert a["front"] == "20261027" and a["front_oi_share"] == pytest.approx(1 / 3)
    e = a["per_expiry"][0]
    assert e["listed"] == (3, 4000.0, 4200.0) and e["quoted"] == (3, 4000.0, 4200.0) and e["underlying"] == "GCZ6"
    assert a["other_classes"] == {"G1W": 1}
    assert a["futures_oi"]["GCZ6"] == 322_500.0
    assert e["contracts_quoted"] == 6 and e["n_contracts"] == 6
    g = a["g5_posthoc"]                                               # consistent() made IBKR's gamma = ours
    assert g["gex_weighted_median"] == pytest.approx(0, abs=1e-9) and g["by_sd"][(0, 1)][1] == pytest.approx(0, abs=1e-9)
    assert a["r_ib"] == pytest.approx(a["r"])
    s = a["sensitivities"]
    assert s["R, next 2 expiries only"] < a["r"]
    assert s["R, D = front GC month only (GCZ6)"] > a["r"]
    assert s["R, IBKR's model gamma instead of ours"] == pytest.approx(a["r"])


def test_report_and_main(base, tmp_path, capsys):
    p = tmp_path / "probe.json"
    p.write_text(json.dumps(base))
    rep = tmp_path / "REPORT.md"
    attempts = tmp_path / "ATTEMPTS.md"
    attempts.write_text("# Probe attempts\n\n- Attempt 1: 2026-10-22 10:43 UTC, exit 1, not used.\n")
    assert analyze.main([str(p), "--report", str(rep), "--attempts-file", str(attempts),
                         "--attempt", "2026-10-21T05:00Z: exit 4, error 10197"]) == 0
    text = rep.read_text()
    assert "- Attempt 1: 2026-10-22 10:43 UTC, exit 1, not used." in text
    assert "Expiries: 3 used, 3 targeted." in text
    assert "20261027 GCZ6 / GCZ6; 20261124 GCZ6 / GCZ6; 20261228 GCG7 / GCG7" in text
    assert "GC futures asked for their option chains: GCV6, GCX6, GCZ6, GCF7, GCG7." in text
    lines = text.splitlines()
    assert lines[0] == "# GEX feasibility: report" and lines[2] == "## Verdict"
    assert "**GEX DROPPED" in lines[4]
    for section in ("## Snapshot", "## Attempts", "## Gates", "## Per expiry", "## Top 10 strikes",
                    "## Sensitivities", "## D, by trade date", "## Post hoc: where G5", "## Known limits"):
        assert section in text
    assert "exit 4, error 10197" in text and "2026-10-21" in text and "SHA-256" in text
    assert "R = " in capsys.readouterr().out


def test_the_fallback_is_in_the_report(tmp_path):
    ib = world(THU_0500, expiries=("20261023", "20261027", "20261124"))
    doc = probe_doc(ib)
    a = analyze.analyze(doc)
    assert a["n_expiries_used"] == 2 and a["sensitivities"]["R, next 2 expiries only"] == pytest.approx(a["r"])
    text = analyze.report(a, source="x.json", sha256="0" * 64, attempts=[])
    assert "Expiries: 2 used, 3 targeted; **fallback**: only 2 OG expiries" in text


def test_committed_attempts_file_lists_attempt_1():
    lines = analyze.read_attempts(HERE / "ATTEMPTS.md")
    assert lines[0].startswith("Attempt 1: 2026-10-22 10:43 UTC, exit 1, not used.")


def test_main_rejects_other_files(tmp_path):
    p = tmp_path / "x.json"
    p.write_text(json.dumps({"schema": "something/9"}))
    with pytest.raises(SystemExit):
        analyze.main([str(p), "--report", str(tmp_path / "R.md")])
