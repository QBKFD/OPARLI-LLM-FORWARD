"""backend.datafeed.features.news_sigma: HAR plus Study A's release indicators,
from a shipped, SHA-256-checked model (research/news_vol/fit_final.py)."""

import hashlib
import json
import math
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from backend.datafeed.calendar_source import CalendarEvent
from backend.datafeed.features import news_sigma as ns
from backend.datafeed.features import vol_block as vb
from backend.datafeed.ingest.xauusd_bars import _market_open

UTC = timezone.utc
FAR = datetime(2030, 1, 1, tzinfo=UTC)                     # a calendar known well past every window


def t(s: str) -> datetime:
    return pd.Timestamp(s, tz="UTC").to_pydatetime()


def state(ts, L=0.0, S_hd=0.0, T=0.0, close=2000.0, reason=None, params=None):
    h = (params or ns.load_news_model().params).sha256
    return vb.VolState(t(ts), None, L, S_hd, T, close, None, reason, params_sha256=h)


def ev(ts, event="CPI m/m", currency="USD", impact="High"):
    return CalendarEvent(ts_utc=t(ts), currency=currency, event=event, impact=impact)


# ---------------------------------------------------------------- the shipped model

def test_shipped_model_loads_with_its_pinned_sha256():
    m = ns.load_news_model()
    assert (m.name, m.version, m.sha256) == ("news_parquet2021_2023", 1, ns.NEWS_SIGMA_MODELS[m.name][1])
    assert set(m.horizons) == {240}                       # owner, 2026-11-08: the 60-min terms are off
    assert all(len(b) == 6 for b, _ in m.horizons.values())
    assert m.groups.currency == "USD" and m.groups.impact == "High"
    assert m.groups.data == {"CPI m/m", "CPI y/y", "Core CPI m/m", "Non-Farm Employment Change"}
    assert m.groups.fomc == {"FOMC Statement", "Federal Funds Rate", "FOMC Press Conference"}


def test_shares_the_default_models_volparams():
    """One state serves both: the fit reused parquet2021_2023's VolParams."""
    news, base = ns.load_news_model(), vb.load_sigma_model("parquet2021_2023")
    assert news.params.sha256 == base.params.sha256
    assert news.base_sha256 == base.sha256


def test_release_coefficients_are_positive():
    for beta, _ in ns.load_news_model().horizons.values():
        assert beta[4] > 0 and beta[5] > beta[4]


def test_sha256_check_rejects_edited_files(tmp_path):
    file, pinned = ns.NEWS_SIGMA_MODELS["news_parquet2021_2023"]
    d = json.loads((vb.MODELS_DIR / file).read_text())
    d["horizons"]["240"]["beta"][5] += 1e-9
    d.pop("sha256")
    d["sha256"] = hashlib.sha256(vb._canonical(d)).hexdigest()       # re-hashed by the editor
    edited = tmp_path / "edited.json"
    edited.write_text(json.dumps(d))
    with pytest.raises(ValueError, match="SHA-256"):                  # the pin still holds
        ns.news_model_from_file(edited, pinned)
    with pytest.raises(ValueError, match="unknown news sigma model"):
        ns.load_news_model("parquet2021_2023")


def test_a_har_file_is_not_a_news_model():
    file, sha = vb.SIGMA_MODELS["parquet2021_2023"]
    with pytest.raises(ValueError, match="not a sigma_har_news model"):
        ns.news_model_from_file(vb.MODELS_DIR / file, sha)


# ---------------------------------------------------------------- the formula

def _as_har(model: ns.NewsSigmaModel) -> ns.NewsSigmaModel:
    """The news model with the base HAR model's beta and c and zero release terms."""
    base = vb.load_sigma_model("parquet2021_2023")
    return replace(model, horizons={H: (b + (0.0, 0.0), c) for H, (b, c) in base.horizons.items()})


@pytest.mark.parametrize("ts", ["2021-03-24 10:00", "2021-03-23 20:00", "2021-11-03 13:17:30"])
def test_zero_release_terms_give_sigma_over_exactly(ts):
    H = 240
    model = _as_har(ns.load_news_model())
    st = state(ts, L=-0.2, S_hd=0.1, T=-0.05, close=1900.0)
    got = ns.sigma_over_news(model, st, H, [ev("2021-03-24 12:30"), ev("2021-11-03 18:00", "FOMC Statement")], FAR)
    assert got.sigma == vb.sigma_over(vb.load_sigma_model("parquet2021_2023"), st, H)


def test_formula_by_hand():
    model = ns.load_news_model()
    st = state("2021-03-24 10:00", L=-0.2, S_hd=0.1, T=-0.05, close=1900.0)
    beta, c = model.horizons[240]
    minutes = [st.t + timedelta(minutes=k) for k in range(240)]
    assert all(_market_open(u) for u in minutes)
    P = np.asarray(model.params.profile)[vb._buckets(pd.DatetimeIndex(minutes))].sum()
    x = beta[0] + beta[1] * -0.2 + beta[2] * 0.1 + beta[3] * -0.05
    for events, dd, df in (([], 0, 0), ([ev("2021-03-24 12:30")], 1, 0),
                           ([ev("2021-03-24 13:00", "FOMC Statement")], 0, 1),
                           ([ev("2021-03-24 12:30"), ev("2021-03-24 13:00", "Federal Funds Rate")], 1, 1)):
        want = math.sqrt(P * math.exp(x + beta[4] * dd + beta[5] * df) * c)
        got = ns.sigma_over_news(model, st, 240, events, FAR)
        assert (got.d_data, got.d_fomc) == (bool(dd), bool(df))
        assert got.sigma.sigma_ret == pytest.approx(want, rel=1e-12)
        assert got.sigma.sigma_px == pytest.approx(1900.0 * want, rel=1e-12)


# ---------------------------------------------------------------- which events count

def test_window_is_t_inclusive_end_exclusive():
    """N1: t <= e < E. Wed 2021-03-24 10:00 UTC + 240 open minutes ends at 14:00."""
    model, st = ns.load_news_model(), state("2021-03-24 10:00")
    assert ns.sigma_over_news(model, st, 240, [ev("2021-03-24 10:00")], FAR).d_data
    assert ns.sigma_over_news(model, st, 240, [ev("2021-03-24 13:59")], FAR).d_data
    assert not ns.sigma_over_news(model, st, 240, [ev("2021-03-24 14:00")], FAR).d_data
    assert not ns.sigma_over_news(model, st, 240, [ev("2021-03-24 09:59")], FAR).d_data


def test_mid_minute_t_starts_at_the_next_minute():
    model = ns.load_news_model()
    st = state("2021-03-24 10:00:20")
    assert not ns.sigma_over_news(model, st, 240, [ev("2021-03-24 10:00")], FAR).d_data
    assert ns.sigma_over_news(model, st, 240, [ev("2021-03-24 14:00")], FAR).d_data


def test_window_over_the_daily_break_uses_its_open_minutes():
    """Tue 2021-03-23 20:00 UTC (16:00 NY): 60 open minutes, the 21:00-22:00 break, then
    180 more, so the 240-minute window ends at 01:00 UTC."""
    model, st = ns.load_news_model(), state("2021-03-23 20:00")
    s = ns.sigma_over_news(model, st, 240, [ev("2021-03-24 00:30")], FAR)
    assert s.d_data and s.sigma.crosses_close
    assert not ns.sigma_over_news(model, st, 240, [ev("2021-03-24 01:00")], FAR).d_data


def test_only_the_models_groups_count():
    model, st = ns.load_news_model(), state("2021-03-24 10:00")
    others = [ev("2021-03-24 10:30", "CPI m/m", currency="EUR"), ev("2021-03-24 10:30", impact="Medium"),
              ev("2021-03-24 10:30", "Unemployment Claims"), ev("2021-03-24 10:30", "Core PCE Price Index m/m"),
              ev("2021-03-24 10:30", "FOMC Meeting Minutes")]
    s = ns.sigma_over_news(model, st, 240, others, FAR)
    assert (s.d_data, s.d_fomc, s.releases) == (False, False, ())
    s = ns.sigma_over_news(model, st, 240, others + [ev("2021-03-24 10:30", "FOMC Press Conference")], FAR)
    assert (s.d_data, s.d_fomc) == (False, True) and [e.event for e in s.releases] == ["FOMC Press Conference"]


# ---------------------------------------------------------------- refusals

def test_a_window_past_the_calendar_raises():
    model, st = ns.load_news_model(), state("2021-03-24 10:00")
    assert ns.sigma_over_news(model, st, 240, [], t("2021-03-24 14:00")).known_until == t("2021-03-24 14:00")
    with pytest.raises(ValueError, match="known_until"):
        ns.sigma_over_news(model, st, 240, [], t("2021-03-24 13:59"))
    with pytest.raises(ValueError, match="naive"):
        ns.sigma_over_news(model, st, 240, [], datetime(2030, 1, 1))


@pytest.mark.parametrize("H", [60, 30, 120, 240.0, True])
def test_only_fitted_horizons(H):
    with pytest.raises(ValueError, match="H must be one of"):
        ns.sigma_over_news(ns.load_news_model(), state("2021-03-24 10:00"), H, [], FAR)


def test_state_from_other_params_or_unusable_raises():
    model = ns.load_news_model()
    txt = vb.load_sigma_model("txt2007_2023").params
    with pytest.raises(ValueError, match="other VolParams"):
        ns.sigma_over_news(model, state("2021-03-24 10:00", params=txt), 240, [], FAR)
    with pytest.raises(ValueError, match="insufficient_history"):
        ns.sigma_over_news(model, state("2021-03-24 10:00", reason="insufficient_history"), 240, [], FAR)
    with pytest.raises(ValueError, match="valid state"):
        ns.sigma_over_news(model, state("2021-03-24 10:00", T=float("nan")), 240, [], FAR)


def test_from_a_real_state_built_with_the_model_params():
    """End to end: synthetic bars -> state_at(model.params) -> sigma_over_news."""
    rng = np.random.default_rng(12345)
    idx = pd.date_range("2021-03-01", "2021-03-24 10:00", freq="1min", tz="UTC", inclusive="left")
    idx = idx[[_market_open(u.to_pydatetime()) for u in idx]]
    close = 1800 * np.exp(np.cumsum(rng.normal(0, 2e-4, len(idx))))
    m = pd.DataFrame({"open": close, "high": close, "low": close, "close": close}, index=idx)
    model = ns.load_news_model()
    st = vb.state_at(model.params, m, pd.Timestamp("2021-03-24 10:00", tz="UTC"))
    assert st.reason is None
    for H in (240,):
        quiet = ns.sigma_over_news(model, st, H, [], FAR).sigma
        fomc = ns.sigma_over_news(model, st, H, [ev("2021-03-24 10:30", "FOMC Statement")], FAR).sigma
        assert fomc.sigma_ret / quiet.sigma_ret == pytest.approx(math.exp(model.horizons[H][0][5] / 2), rel=1e-12)
