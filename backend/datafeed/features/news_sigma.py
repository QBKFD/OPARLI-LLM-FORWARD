"""News-aware sigma: HAR plus Study A's scheduled-release indicators.

THE MODEL
    var(t, H) = P(t, H) × exp(β · [1, L, S_hd, T, d_data, d_fomc]) × c
    P, L, S_hd and T are sigma_over's (vol_block): P sums the seasonal profile over
    the first H market-open minutes from t, the window [t, E). d_data and d_fomc are
    1 when a scheduled release of that group falls in the window, t <= ts_utc < E.
    The groups (currency, impact, event names) come from the model file, as
    research/news_vol (Study A) defined them: CPI and NFP as data, FOMC statement,
    rate and press conference as FOMC.
    Evidence: Study A (PREREG 843b447), PASS: QLIKE -19 % at 240 min on gold,
    replicated on XAGUSD (-12 %) and EURUSD (-22 %) (research/news_vol_rep).
    The shipped fit is research/news_vol/fit_final.py: the default HAR model's bars
    and VolParams (parquet2021_2023), so a state from state_at(model.params) serves
    both models, and its training rows without the windows that cross the daily
    break (Study A's N4; owner, 2026-11-08). Training years 2021-2023 only: the
    multipliers (variance x2.6 data, x7.7 FOMC) are that period's.
    H = 240 only (owner, 2026-11-08): the 60-min terms, FOMC's included, are off;
    for H = 60 use sigma_over.

WHAT CHANGES AGAINST sigma_over
    Not only release windows: all six terms are refit, and part of the release vol
    sat in the default model's intercept. With no release in the window the news
    model's variance is a median 0.94x the default's (sigma about 3 % lower),
    in-sample 2021-2023 (research/news_vol/model/fit_final_result.json).

THE CALENDAR
    Only the schedule is read (time, currency, impact, name), never an actual. The
    caller passes the events, e.g. calendar_source.events_as_of(t, calendar), and
    `known_until`: the time up to which its calendar is complete. A window that ends
    after known_until raises, so a missing calendar never reads as "no release".
    Untimed rows never count (calendar_source drops them).

WHAT IT DOESN'T DO
    It is off: nothing calls it, and sigma_over, the scanner, risk/ and the agents
    are unchanged. An unscheduled event (the 2020-03-03 cut) is in no calendar. A
    window that crosses a close is outside the training rows; its sigma is computed
    the same way and flagged crosses_close (research/news_vol/DECISIONS.md NS3).
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

from backend.datafeed.features import vol_block as vb

MODEL_KIND = "sigma_har_news"
FEATURES = ("1", "L", "S_hd", "T", "d_data", "d_fomc")
# name -> (file, SHA-256 of the canonical JSON of every field except "sha256"), as
# research/news_vol/fit_final.py wrote it. Pinned here, like vol_block.SIGMA_MODELS.
NEWS_SIGMA_MODELS = {
    "news_parquet2021_2023": ("sigma_har_news_parquet2021_2023_v1.json",
                              "61990324215e145bcc251b213ac8fa850ed0175f63adcc39e32891160fd98b5a"),
}
DEFAULT_NEWS_SIGMA_MODEL = "news_parquet2021_2023"


@dataclass(frozen=True)
class EventGroups:
    currency: str
    impact: str
    data: frozenset[str]
    fomc: frozenset[str]

    def group(self, e) -> str | None:
        """'data', 'fomc' or None for an event with currency, impact and event."""
        if e.currency != self.currency or e.impact != self.impact:
            return None
        return "fomc" if e.event in self.fomc else "data" if e.event in self.data else None


@dataclass(frozen=True)
class NewsSigmaModel:
    name: str
    version: int
    sha256: str
    base_sha256: str           # the HAR model whose rows and VolParams it shares
    params: vb.VolParams       # compute the state with these
    horizons: dict             # H -> (beta (6,), smear_c)
    groups: EventGroups


@dataclass(frozen=True)
class NewsSigma:
    sigma: vb.Sigma
    d_data: bool
    d_fomc: bool
    releases: tuple            # the events that set d_data / d_fomc, by time
    known_until: datetime


def news_model_from_file(path, expected_sha256: str, name: str = "") -> NewsSigmaModel:
    """Load a news sigma model file. Raises unless the SHA-256 of its content equals
    both the value stated in the file and expected_sha256."""
    d = json.loads(Path(path).read_text())
    stated = d.pop("sha256", None)
    got = hashlib.sha256(vb._canonical(d)).hexdigest()
    if got != stated or got != expected_sha256:
        raise ValueError(f"{path}: SHA-256 check failed (content {got}, stated {stated}, "
                         f"expected {expected_sha256})")
    if d.get("model") != MODEL_KIND or tuple(d.get("features", ())) != FEATURES:
        raise ValueError(f"{path}: not a {MODEL_KIND} model with features {FEATURES}")
    horizons = {int(H): (tuple(float(b) for b in v["beta"]), float(v["smear_c"]))
                for H, v in d["horizons"].items()}
    if any(len(beta) != len(FEATURES) for beta, _ in horizons.values()):
        raise ValueError(f"{path}: beta must have {len(FEATURES)} values per horizon")
    ev = d["events"]
    groups = EventGroups(ev["currency"], ev["impact"], frozenset(ev["data"]), frozenset(ev["fomc"]))
    if groups.data & groups.fomc:
        raise ValueError(f"{path}: an event name is in both groups")
    return NewsSigmaModel(name=name, version=int(d["version"]), sha256=got, base_sha256=d["base_model"]["sha256"],
                          params=vb.VolParams.from_json(json.dumps(d["vol_params"])), horizons=horizons,
                          groups=groups)


def load_news_model(name: str | None = None) -> NewsSigmaModel:
    """The shipped news model `name`, else DEFAULT_NEWS_SIGMA_MODEL."""
    name = name or DEFAULT_NEWS_SIGMA_MODEL
    if name not in NEWS_SIGMA_MODELS:
        raise ValueError(f"unknown news sigma model {name!r}; choose from {sorted(NEWS_SIGMA_MODELS)}")
    file, sha = NEWS_SIGMA_MODELS[name]
    return news_model_from_file(vb.MODELS_DIR / file, sha, name)


def releases_in(events: Iterable, start: datetime, end: datetime, groups: EventGroups) -> tuple:
    """(event, group) for every event of a group with start <= ts_utc < end, by time."""
    out = [(e, g) for e in events if start <= e.ts_utc < end and (g := groups.group(e)) is not None]
    return tuple(sorted(out, key=lambda x: x[0].ts_utc))


def sigma_over_news(model: NewsSigmaModel, state: vb.VolState, H: int, events: Iterable,
                    known_until) -> NewsSigma:
    """News-aware sigma over the next H market-open minutes from state.t (see the
    module docstring). Raises like sigma_over, and when the window ends after
    known_until (the calendar can't say whether a release falls in it)."""
    if isinstance(H, bool) or not isinstance(H, int) or H not in model.horizons:
        raise ValueError(f"H must be one of {sorted(model.horizons)} (minutes), got {H!r}")
    if state.params_sha256 != model.params.sha256:
        raise ValueError(f"state was computed with other VolParams (params hash "
                         f"{state.params_sha256!r}, model {model.name!r} needs "
                         f"{model.params.sha256!r}); use state_at / read_state with model.params")
    if state.reason is not None or not all(
            math.isfinite(x) for x in (state.L, state.S_hd, state.T, state.close_ref)):
        raise ValueError(f"no sigma without a valid state (reason {state.reason!r})")
    known = vb._utc_ts(known_until).to_pydatetime()
    beta, c = model.horizons[H]
    minutes, end = vb.open_window(state.t, H)
    if end > known:
        raise ValueError(f"the window ends at {end.isoformat()}, after the calendar's known_until "
                         f"{known.isoformat()}: no news sigma without the schedule")
    start = vb._utc_ts(state.t).ceil("1min").to_pydatetime()
    rel = releases_in(events, start, end, model.groups)
    d_data = any(g == "data" for _, g in rel)
    d_fomc = any(g == "fomc" for _, g in rel)
    prof = np.asarray(model.params.profile, dtype=float)
    P = float(prof[vb._buckets(pd.DatetimeIndex(minutes))].sum())
    x = (1.0, state.L, state.S_hd, state.T, float(d_data), float(d_fomc))
    var = P * math.exp(sum(b * v for b, v in zip(beta, x))) * c
    sigma_ret = math.sqrt(var)
    sigma = vb.Sigma(H=H, sigma_ret=sigma_ret, sigma_px=state.close_ref * sigma_ret, open_minutes=H,
                     crosses_close=(end - start) > pd.Timedelta(minutes=H))
    return NewsSigma(sigma, d_data, d_fomc, tuple(e for e, _ in rel), known)
