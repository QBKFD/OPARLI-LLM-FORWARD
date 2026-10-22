"""gex.py: textbook values, finite differences, and IBKR's own gamma in a probe file."""
import json
import math
import os
import statistics
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from gex import black76_gamma, black76_price, dollar_gamma_1pct, gbsm_gamma  # noqa: E402


def test_textbook_gamma_haug():
    # Haug, The Complete Guide to Option Pricing Formulas (2nd ed.), gamma example:
    # S 55, X 60, T 0.75, r 0.10, b 0.10, sigma 0.30 -> gamma 0.0278
    assert gbsm_gamma(55, 60, 0.75, 0.10, 0.10, 0.30) == pytest.approx(0.0278, abs=5e-5)


def test_textbook_black76_price_haug():
    # Haug, Black-76 example: F 19, X 19, T 0.75, r 0.10, sigma 0.28 -> call = put = 1.7011
    assert black76_price(19, 19, 0.75, 0.28, "C", r=0.10) == pytest.approx(1.7011, abs=1e-4)
    assert black76_price(19, 19, 0.75, 0.28, "P", r=0.10) == pytest.approx(1.7011, abs=1e-4)


@pytest.mark.parametrize("f, k, t, sigma, r", [
    (4150.0, 4150.0, 5 / 365, 0.22, 0.0), (4150.0, 4400.0, 33 / 365, 0.25, 0.0),
    (4150.0, 3800.0, 67 / 365, 0.30, 0.0), (19.0, 19.0, 0.75, 0.28, 0.10), (100.0, 80.0, 0.25, 0.5, 0.05),
])
@pytest.mark.parametrize("right", ["C", "P"])
def test_black76_gamma_is_the_second_derivative_of_the_price(f, k, t, sigma, r, right):
    h = f * 1e-4
    fd = (black76_price(f + h, k, t, sigma, right, r) - 2 * black76_price(f, k, t, sigma, right, r)
          + black76_price(f - h, k, t, sigma, right, r)) / (h * h)
    assert black76_gamma(f, k, t, sigma, r) == pytest.approx(fd, rel=1e-4)


def test_black76_is_gbsm_with_zero_carry():
    assert black76_gamma(4150, 4200, 0.1, 0.2, 0.03) == gbsm_gamma(4150, 4200, 0.1, 0.03, 0.0, 0.2)


def test_r_zero_gives_the_largest_gamma():
    assert black76_gamma(4150, 4200, 0.1, 0.2) > black76_gamma(4150, 4200, 0.1, 0.2, r=0.05)


def test_dollar_gamma_per_one_percent():
    # gamma 0.001 per $, 1,000 contracts of 100 oz, F 4,000:
    # 0.001 * 100,000 oz = 100 oz per $1; a 1 % move is $40 -> 4,000 oz -> $16m
    assert dollar_gamma_1pct(0.001, 4000.0, 1000) == pytest.approx(16_000_000.0)
    assert dollar_gamma_1pct(0.001, 4000.0, 0) == 0.0


@pytest.mark.parametrize("bad", [dict(f=0), dict(k=-1), dict(t=0), dict(sigma=math.nan)])
def test_rejects_bad_inputs(bad):
    args = dict(f=4150.0, k=4150.0, t=0.1, sigma=0.2) | bad
    with pytest.raises(ValueError):
        black76_gamma(**args)
    with pytest.raises(ValueError):
        dollar_gamma_1pct(0.001, 4000.0, -1)


# --- against IBKR's own gamma, in a real probe file (git-ignored, so usually skipped) ---

def _probe_file():
    env = os.environ.get("GEX_PROBE_JSON")
    if env:
        return Path(env)
    found = sorted((HERE / "data").glob("*.json")) if (HERE / "data").is_dir() else []
    return found[0] if found else None


@pytest.mark.skipif(_probe_file() is None, reason="no probe file (set GEX_PROBE_JSON or put it in data/)")
def test_matches_ibkr_gamma_in_the_probe():
    import analyze
    doc = json.loads(_probe_file().read_text())
    a = analyze.analyze(doc)
    ok, detail = a["gates"]["G5"]
    assert ok, detail
    # and not only the median: show the spread
    snap = analyze.parse_utc(doc["snapshot_start_utc"])
    rel = []
    for o in doc["options"]:
        v = {k: analyze.val(o, k) for k in ("model_gamma", "model_iv", "model_und_price")}
        if all(v.values()) and v["model_gamma"] > 0:
            t = (analyze.expiry_utc(o)[0] - snap).total_seconds() / analyze.YEAR_S
            rel.append(abs(black76_gamma(v["model_und_price"], o["strike"], t, v["model_iv"]) / v["model_gamma"] - 1))
    assert statistics.median(rel) <= 0.05
