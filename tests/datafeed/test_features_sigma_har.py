"""datafeed.features.vol_block.sigma_over: HAR sigma from a shipped,
SHA-256-checked model (research/sigma_compare)."""

import json
import math
import subprocess
import sys
import tomllib
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from datafeed.features import vol_block as vb
from datafeed.ingest.xauusd_bars import _market_open

ROOT = Path(__file__).resolve().parents[2]
UTC = "UTC"


def state(t, L=0.0, S_hd=0.0, T=0.0, close=2000.0, reason=None, model=None):
    """A hand-built state stamped with the params hash of `model` (default model if None)."""
    h = (model or vb.load_sigma_model()).params.sha256
    return vb.VolState(pd.Timestamp(t, tz=UTC).to_pydatetime(), None, L, S_hd, T, close, None, reason,
                       params_sha256=h)


# ---------------------------------------------------------------- loading

@pytest.mark.parametrize("name", sorted(vb.SIGMA_MODELS))
def test_shipped_models_load_with_their_pinned_sha256(name):
    m = vb.load_sigma_model(name)
    assert (m.name, m.version, m.sha256) == (name, 1, vb.SIGMA_MODELS[name][1])
    assert set(m.horizons) == {60, 240}
    assert len(m.params.profile) == vb.N_BUCKETS
    assert pd.Timestamp(m.params.train_end) <= pd.Timestamp("2024-01-01", tz=UTC)


def test_default_and_config_selection(monkeypatch):
    monkeypatch.delenv(vb.SIGMA_MODEL_ENV, raising=False)
    assert vb.DEFAULT_SIGMA_MODEL == "parquet2021_2023"
    assert vb.load_sigma_model().name == "parquet2021_2023"
    monkeypatch.setenv(vb.SIGMA_MODEL_ENV, "txt2007_2023")
    assert vb.load_sigma_model().name == "txt2007_2023"
    assert vb.load_sigma_model("parquet2021_2023").name == "parquet2021_2023"   # explicit wins
    monkeypatch.setenv(vb.SIGMA_MODEL_ENV, "plugin")
    with pytest.raises(ValueError, match="unknown sigma model"):
        vb.load_sigma_model()


def test_sha256_check_rejects_edited_files(tmp_path):
    file, pinned = vb.SIGMA_MODELS["txt2007_2023"]
    d = json.loads((vb.MODELS_DIR / file).read_text())
    d["horizons"]["60"]["beta"][1] += 1e-9
    edited = tmp_path / "edited.json"
    edited.write_text(json.dumps(d))                                  # stated hash now wrong
    with pytest.raises(ValueError, match="SHA-256"):
        vb.sigma_model_from_file(edited, pinned)
    d.pop("sha256")                                                   # re-hashed by the editor
    d["sha256"] = __import__("hashlib").sha256(vb._canonical(d)).hexdigest()
    edited.write_text(json.dumps(d))
    with pytest.raises(ValueError, match="SHA-256"):                  # but the pin still holds
        vb.sigma_model_from_file(edited, pinned)
    assert vb.sigma_model_from_file(vb.MODELS_DIR / file, pinned).sha256 == pinned


def test_models_ship_as_package_data(tmp_path):
    cfg = tomllib.loads((ROOT / "pyproject.toml").read_text())
    patterns = cfg["tool"]["setuptools"]["package-data"]["datafeed"]
    shipped = {p.name for pat in patterns for p in (ROOT / "datafeed").glob(pat)}
    assert {f for f, _ in vb.SIGMA_MODELS.values()} <= shipped
    out = subprocess.run([sys.executable, "-I", "-c",
                          "from datafeed.features import vol_block as vb;"
                          "print(sorted(vb.load_sigma_model(n).sha256[:8] for n in vb.SIGMA_MODELS))"],
                         cwd=tmp_path, capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == str(sorted(sha[:8] for _, sha in vb.SIGMA_MODELS.values()))


# ---------------------------------------------------------------- the sigma

# sigma_ret, sigma_px, crosses_close from research/sigma_compare/har_sigma.sigma_har
# (commit de17ffc) on the same model files and states.
GOLDEN = {
    "txt2007_2023": [
        ("2021-03-24 10:00", 60, (-0.3, 0.2, -0.1), 1800.0,
         0.0018776809345735562, 3.379825682232401, False),
        ("2021-03-24 19:30", 240, (0.1, -0.4, 0.25), 1750.5,
         0.0036846231679872696, 6.449932855561715, True),
        ("2021-03-26 19:00", 240, (-0.05, 0.0, 0.0), 1733.25,
         0.0037782004773930736, 6.548565977441545, True),
        ("2021-11-08 14:37:30", 60, (0.4, 0.3, -0.2), 1820.0,
         0.004905313388627955, 8.927670367302879, False),
    ],
    "parquet2021_2023": [
        ("2021-03-24 10:00", 60, (-0.3, 0.2, -0.1), 1800.0,
         0.0013882415448463036, 2.4988347807233464, False),
        ("2021-03-24 19:30", 240, (0.1, -0.4, 0.25), 1750.5,
         0.002673799697074056, 4.680486369728135, True),
        ("2021-03-26 19:00", 240, (-0.05, 0.0, 0.0), 1733.25,
         0.0028041564167220106, 4.860304109283425, True),
        ("2021-11-08 14:37:30", 60, (0.4, 0.3, -0.2), 1820.0,
         0.00455761131964396, 8.294852601752007, False),
    ],
}


@pytest.mark.parametrize("name", sorted(GOLDEN))
def test_equals_the_research_reference(name):
    model = vb.load_sigma_model(name)
    for t, H, (L, S_hd, T), close, s_ret, s_px, crosses in GOLDEN[name]:
        s = vb.sigma_over(model, state(t, L, S_hd, T, close, model=model), H)
        assert (s.sigma_ret, s.sigma_px, s.crosses_close, s.open_minutes, s.H) == \
            (s_ret, s_px, crosses, H, H)


def test_formula_by_hand():
    model = vb.load_sigma_model("txt2007_2023")
    st = state("2021-03-24 10:00", L=-0.2, S_hd=0.1, T=-0.05, close=1900.0, model=model)
    beta, c = model.horizons[240]
    minutes = [st.t + timedelta(minutes=k) for k in range(240)]            # no close inside
    assert all(_market_open(u) for u in minutes)
    prof = np.asarray(model.params.profile)
    P = prof[vb._buckets(pd.DatetimeIndex(minutes))].sum()
    want = math.sqrt(P * math.exp(beta[0] + beta[1] * -0.2 + beta[2] * 0.1 + beta[3] * -0.05) * c)
    s = vb.sigma_over(model, st, 240)
    assert s.sigma_ret == pytest.approx(want, rel=1e-12)
    assert s.sigma_px == pytest.approx(1900.0 * want, rel=1e-12)


def test_open_minute_windows():
    # Tue 2021-03-23 20:00 UTC = 16:00 New York (EDT): 60 open minutes, the break
    # 21:00-22:00 UTC, then 180 more; the window spans 300 calendar minutes
    mins, end = vb.open_window(pd.Timestamp("2021-03-23 20:00", tz=UTC), 240)
    assert len(mins) == 240 and end == pd.Timestamp("2021-03-24 01:00", tz=UTC)
    assert all(_market_open(u) for u in mins)
    # Friday 15:00 New York: 120 minutes, then Sunday 18:00 New York
    mins, end = vb.open_window(pd.Timestamp("2021-03-26 19:00", tz=UTC), 240)
    assert end == pd.Timestamp("2021-03-29 00:00", tz=UTC)
    # mid-minute: starts at the next whole minute
    mins, _ = vb.open_window(pd.Timestamp("2021-03-24 10:00:30", tz=UTC), 60)
    assert mins[0] == pd.Timestamp("2021-03-24 10:01", tz=UTC)
    # the plug-in counts calendar minutes instead: same minutes when nothing is closed
    model = vb.load_sigma_model()
    st = state("2021-03-24 10:00")
    assert vb.sigma_over(model, st, 60).crosses_close is False
    assert vb.sigma_plugin(model.params, st, 60).open_minutes == 60


@pytest.mark.parametrize("H", [15, 30, 120, 1440, 0, -60, 60.0, True, None, "60"])
def test_only_fitted_horizons(H):
    with pytest.raises(ValueError, match="H must be one of"):
        vb.sigma_over(vb.load_sigma_model(), state("2021-03-24 10:00"), H)


def test_unusable_state_raises():
    model = vb.load_sigma_model()
    with pytest.raises(ValueError, match="insufficient_history"):
        vb.sigma_over(model, state("2021-03-24 10:00", reason="insufficient_history"), 60)
    with pytest.raises(ValueError, match="valid state"):
        vb.sigma_over(model, state("2021-03-24 10:00", T=float("nan")), 60)


def test_from_a_real_state_built_with_the_model_params():
    """End to end: bars -> state_at(model.params) -> sigma_over."""
    rng = np.random.default_rng(12345)
    idx = pd.date_range("2021-03-01", "2021-03-24 10:00", freq="1min", tz=UTC, inclusive="left")
    idx = idx[[_market_open(t.to_pydatetime()) for t in idx]]
    close = 1800 * np.exp(np.cumsum(rng.normal(0, 2e-4, len(idx))))
    m = pd.DataFrame({"open": close, "high": close, "low": close, "close": close}, index=idx)
    model = vb.load_sigma_model()
    st = vb.state_at(model.params, m, pd.Timestamp("2021-03-24 10:00", tz=UTC))
    assert st.reason is None
    for H in (60, 240):
        s = vb.sigma_over(model, st, H)
        assert s.sigma_ret > 0 and s.sigma_px == st.close_ref * s.sigma_ret


# ---------------------------------------------------------------- the state's params must be the model's

def bars_until(t_end, seed=12345):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2021-03-01", t_end, freq="1min", tz=UTC, inclusive="left")
    idx = idx[[_market_open(t.to_pydatetime()) for t in idx]]
    close = 1800 * np.exp(np.cumsum(rng.normal(0, 2e-4, len(idx))))
    return pd.DataFrame({"open": close, "high": close, "low": close, "close": close}, index=idx)


def test_state_at_stamps_the_params_hash():
    m = bars_until("2021-03-24 10:00")
    for name in vb.SIGMA_MODELS:
        params = vb.load_sigma_model(name).params
        for t in ("2021-03-24 10:00", "2021-02-01 00:00"):          # valid, and no bars at all
            assert vb.state_at(params, m, pd.Timestamp(t, tz=UTC)).params_sha256 == params.sha256
    a, b = (vb.load_sigma_model(n).params.sha256 for n in sorted(vb.SIGMA_MODELS))
    assert a != b and len(a) == 64


def test_sigma_over_refuses_a_state_from_other_params():
    m = bars_until("2021-03-24 10:00")
    t = pd.Timestamp("2021-03-24 10:00", tz=UTC)
    txt, parquet = vb.load_sigma_model("txt2007_2023"), vb.load_sigma_model("parquet2021_2023")
    st_txt = vb.state_at(txt.params, m, t)
    assert st_txt.reason is None
    assert vb.sigma_over(txt, st_txt, 60).sigma_ret > 0                  # matching params: fine
    with pytest.raises(ValueError, match="other VolParams"):
        vb.sigma_over(parquet, st_txt, 60)                                # txt state, parquet model
    unstamped = vb.VolState(st_txt.t, st_txt.label, st_txt.L, st_txt.S_hd, st_txt.T,
                            st_txt.close_ref, st_txt.last_bar_ts, None)   # hand-built, no hash
    with pytest.raises(ValueError, match="other VolParams"):
        vb.sigma_over(txt, unstamped, 60)


def test_params_hash_tracks_the_content():
    p = vb.load_sigma_model().params
    assert p.sha256 == vb.VolParams.from_json(p.to_json()).sha256         # stable over a round trip
    prof = list(p.profile)
    prof[0] = prof[0] * (1 + 1e-12)
    assert vb.VolParams(tuple(prof), p.s_hour_mean, p.train_start, p.train_end).sha256 != p.sha256
