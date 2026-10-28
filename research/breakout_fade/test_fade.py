"""research/breakout_fade on synthetic bars only (no silver data).

    python -m pytest research/breakout_fade -q
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "pullback_structure"))
import fade  # noqa: E402
import fade_params as F  # noqa: E402
import test_pullback as T  # noqa: E402  (pullback_structure's synthetic paths)
from fade import P0, pb  # noqa: E402


def _event(tail):
    mids = T.path(T.BEAR_PREFIX + tail)
    cd, mt, mh, ml, sig = T.series_from_mids(mids)
    s = pb.series(cd, mt, mh, ml, sig, bull=False)
    ev, _ = pb.run_events(s, events_from_ns=0, **{**fade.RUN, "n_swings": 3})
    return [e for e in ev if e.bc], s, mids


def test_the_definitions_are_pullback_structures():
    assert (P0.TIMEFRAME, P0.K, P0.N_SWINGS, P0.X_SIGMA, P0.W, P0.HORIZON) == ("1h", 3, 2, 1.0, 5, 30)
    assert fade.RUN["n_swings"] == 2 and F.STOP_SIGMA == 1.0 and F.SIGMA_MODEL == "parquet2021_2023"


def test_h2_baseline_and_a_fade_that_hits_the_target():
    ev, s, mids = _event([39] * 7 + [30])                 # C: closes above LH, then falls through the swing low
    e = ev[0]
    r = fade.h2_trade(s, e, P0.HORIZON)
    entry, sl, sig = s.c[e.brk], e.sl, e.sigma_brk
    assert r["p"] == pytest.approx(sig / (sig + (entry - sl)))
    assert r["h2"] == "hit" and r["hit"] == 1 and r["target_sigma"] == pytest.approx((entry - sl) / sig)


def test_h2_stop_when_the_break_follows_through():
    ev, s, _ = _event([37, 41, 43])                       # B: the break runs on, the 1 sigma stop is hit
    r = fade.h2_trade(s, ev[0], P0.HORIZON)
    assert r["h2"] == "stop" and r["hit"] == 0


def test_a_bar_holding_target_and_stop_is_the_stop():
    z = np.zeros(1)
    s = pb.Series(z, z, z, z, z, z, z, z, np.arange(2), np.array([5.0, 11.0]), np.array([4.0, 0.0]), z, 1)
    assert fade.first_passage_le_ge(s, 0, 2, 1.0, 10.0) == ("stop", 1)
    assert fade.first_passage_le_ge(s, 0, 1, 4.0, 10.0) == ("hit", 0)      # touching the target counts (<=)


def test_short_days_and_trade_dates():
    idx = pd.date_range("2026-03-09 22:00", "2026-03-11 21:59", freq="1min", tz="UTC")
    m = pd.DataFrame({"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0}, index=idx)
    m = m[~((m.index >= pd.Timestamp("2026-03-10 22:00", tz="UTC")) & (m.index < pd.Timestamp("2026-03-11 14:00", tz="UTC")))]
    short = fade.short_dates(m, F.SHORT_DAY_ROWS)
    days = {pd.Timestamp(d * 86400, unit="s").date().isoformat() for d in short}
    assert days == {"2026-03-11"}                         # NY trade date 03-11 has 480 rows; 03-10 is full


def test_h1_one_sided_and_the_verdicts():
    month = np.repeat(np.arange(96), 7)
    is_b = np.zeros(len(month), bool)
    is_b[::4] = True                                      # share 0.25 against a 0.344 null
    h1 = fade.h1_test(month, is_b, 0.344, 2000, 1)
    assert h1["difference"] < 0 and h1["ci95"][1] < 0 and h1["p_one_sided"] < 0.01
    bh = pb.bh({"H1": h1["p_one_sided"], "H2": 0.5}, F.BH_Q)
    assert fade.verdict(h1, bh, F.MIN_EVENTS) == "PASS"
    flat = fade.h1_test(month, np.tile([True, False, False], len(month) // 3 + 1)[:len(month)], 0.334, 2000, 1)
    assert fade.verdict(flat, pb.bh({"H1": flat["p_one_sided"], "H2": 0.5}, F.BH_Q), F.MIN_EVENTS) == "FAIL"
    assert fade.verdict({"n": 10}, {}, F.MIN_EVENTS).startswith("INCONCLUSIVE")


def test_break_even_spread():
    hit = np.array([1.0, 0.0, 0.0, 1.0])
    tgt = np.array([2.0, 3.0, 1.0, 1.0])
    assert fade.break_even_sigma(hit, tgt) == pytest.approx((2 - 1 - 1 + 1) / 4)


def test_the_data_guard_lets_this_study_read_silver_only():
    sys.path.insert(0, str(HERE.parent))
    import data_guard as dg
    import fade_data
    key = fade_data.DATA_KEY
    assert dg.guarded_key(Path(key).name) == key
    dg.assert_may_read(key, study=F.STUDY)                 # the committed PREREG names the key
    with pytest.raises(dg.DataGuardError):
        dg.assert_may_read("database/ohlcv_data/EURUSD_ibkr_1min_20160101_20260930.parquet", study=F.STUDY)


def test_prereg_on_disk_is_the_pinned_pre_registration():
    import hashlib
    import subprocess
    assert hashlib.sha256((HERE / "PREREG.md").read_bytes()).hexdigest() == F.PREREG_SHA256
    pinned = subprocess.run(["git", "show", f"{F.PREREG_COMMIT}:research/breakout_fade/PREREG.md"],
                            cwd=HERE, capture_output=True, check=False)
    if pinned.returncode == 0:                                # the commit is present (not a shallow clone)
        assert hashlib.sha256(pinned.stdout).hexdigest() == F.PREREG_SHA256
    assert "B1–B20) APPROVED" in (HERE / "PREREG.md").read_text() and "| OPEN |" not in (HERE / "PREREG.md").read_text()
