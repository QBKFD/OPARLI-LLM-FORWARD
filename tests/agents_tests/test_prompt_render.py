"""agents.prompting.render and the snapshot slots it reads (schema 3)."""

import json
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from agents.config import SnapshotConfig
from agents.owner_config import OwnerConfig
from agents.prompting.files import parse_prompt
from agents.prompting.render import ImagesPart, RenderProblem, TextPart, render_round1, unavailable_text
from agents.schemas import AlertFacts, ChartRef, Level, MetricRow, Snapshot
from agents.snapshot import NOT_BUILT, alert_from_payload, build_snapshot

UTC = timezone.utc
T = datetime(2025, 6, 11, 14, 30, tzinfo=UTC)
CFG = OwnerConfig.from_text('[render.bars_rows]\n"15m" = 3\n')
JOURNAL_ROW = {"alert_id": "A20250611-004", "alert_ts_utc": "2025-06-11 14:17:00", "session": "NY",
               "level_types": "PDH,ASIA_H", "level_price": 3312.5, "price_at_alert": 3312.1, "confluence_count": 2,
               "status": "sent", "epoch": 7, "bar_source": "ibkr"}     # the extra journal columns are dropped


def prompt(user, system="sys"):
    return parse_prompt(f"---\nagent: visual\nversion: 1\n---\n## system\n{system}\n## user\n{user}\n".encode(),
                        "visual")


@pytest.fixture(scope="module")
def snap(replay_db):
    s = build_snapshot(T, SnapshotConfig(mode="replay"), replay_db, payload={"alert": JOURNAL_ROW})
    assert isinstance(s, Snapshot)
    return s


def filled(snap):
    """The snapshot with every slot filled, as later builders will fill it."""
    sha = "ab" * 32
    return Snapshot(**{**snap.model_dump(exclude={"hash", "unavailable", "levels", "trigger_levels", "metrics",
                                                  "charts", "features", "events"}),
                       "levels": (Level(id="pdh-0611", type="PDH", value=3312.5, active_from=T - timedelta(hours=14)),),
                       "trigger_levels": ("pdh-0611",),
                       "features": {"sigma_px_240": 9.5},
                       "metrics": (MetricRow(name="rsi14_1h", value=61.2, unit="0-100", available=True),
                                   MetricRow(name="volume", value=None, unit="", available=False,
                                             reason="no volume source")),
                       "charts": (ChartRef(tf="15m", sha256=sha, width=1000, height=600),
                                  ChartRef(tf="1h", sha256=sha, width=1000, height=600))})


# ---- snapshot slots

def test_the_alert_facts_come_from_the_payload_allowlist(snap):
    assert snap.alert == AlertFacts(alert_id="A20250611-004", alert_ts=datetime(2025, 6, 11, 14, 17, tzinfo=UTC),
                                    session="NY", level_types=("PDH", "ASIA_H"), level_price=3312.5,
                                    price_at_alert=3312.1, confluence_count=2)
    assert "epoch" not in json.dumps(snap.alert.model_dump(mode="json"))
    assert snap.unavailable == {**NOT_BUILT, "features": "no sigma model configured (snapshot.sigma_model)"}
    assert snap.metrics and snap.metrics[0].name == "atr14_15m"   # the metric table is always built


@pytest.mark.parametrize("payload, why", [
    (None, "the trigger has no alert"),
    ({"note": 1}, "the trigger has no alert"),
    ({"alert": "A1"}, "not a mapping"),
    ({"alert": {**JOURNAL_ROW, "alert_ts_utc": "yesterday"}}, "bad alert_ts_utc"),
    ({"alert": {k: v for k, v in JOURNAL_ROW.items() if k != "level_price"}}, "level_price"),
    ({"alert": {**JOURNAL_ROW, "confluence_count": 0}}, "confluence_count"),
])
def test_bad_or_missing_alerts_are_reasons(payload, why):
    out = alert_from_payload(payload)
    assert isinstance(out, str) and why in out


def test_a_missing_alert_is_marked_unavailable(replay_db):
    s = build_snapshot(T, SnapshotConfig(mode="replay"), replay_db, payload={"note": "manual"})
    assert s.alert is None and s.unavailable["alert"] == "the trigger has no alert"


def test_snapshot_slot_rules(snap):
    base = snap.model_dump(exclude={"hash"})
    with pytest.raises(ValidationError, match="trigger_levels"):
        Snapshot(**{**base, "trigger_levels": ("nope",)})
    with pytest.raises(ValidationError, match="marked unavailable but has content"):
        Snapshot(**{**base, "features": {"L": 1.0}})
    with pytest.raises(ValidationError, match="unknown slot"):
        Snapshot(**{**base, "unavailable": {"bars": "x"}})
    with pytest.raises(ValidationError, match="active only from after"):
        Snapshot(**{**base, "unavailable": {}, "levels": (Level(id="l", type="PDH", value=1.0,
                                                                  active_from=T + timedelta(minutes=1)),)})
    with pytest.raises(ValidationError, match="duplicate metric"):
        m = MetricRow(name="x", value=1.0, unit="", available=True)
        Snapshot(**{**base, "metrics": (m, m)})


@pytest.mark.parametrize("kw", [
    {"value": None, "available": True},
    {"value": 1.0, "available": True, "reason": "x"},
    {"value": 1.0, "available": False, "reason": "x"},
    {"value": None, "available": False},
])
def test_metric_rows_are_never_imputed(kw):
    with pytest.raises(ValidationError):
        MetricRow(name="m", unit="", **kw)


# ---- rendering

def test_every_round1_placeholder_renders(snap):
    s = filled(snap)
    names = ("as_of", "alert_json", "levels_json", "trigger_levels_json", "features_json", "events_json",
             "metrics_table", "bars_table:15m", "snapshot_json")
    user = " SEP ".join("{{" + n + "}}" for n in names)
    r = render_round1(prompt(user), s, CFG)
    text = r.user_text()
    parts = text.split(" SEP ")
    assert len(parts) == len(names)
    assert parts[0] == "2025-06-11T14:30:00+00:00"
    assert json.loads(parts[1])["alert_id"] == "A20250611-004"
    assert json.loads(parts[2])[0]["id"] == "pdh-0611" and json.loads(parts[3]) == ["pdh-0611"]
    assert json.loads(parts[4]) == {"sigma_px_240": 9.5} and json.loads(parts[5]) == []
    assert "rsi14_1h | 61.2 | 0-100 | true |" in parts[6] and "volume |  |  | false | no volume source" in parts[6]
    assert r.unavailable_used == ()


def test_bars_table_shows_the_last_complete_bars(snap):
    r = render_round1(prompt("{{bars_table:15m}}"), snap, CFG)
    lines = r.user_text().splitlines()
    assert len(lines) == 1 + 3                                     # header + render.bars_rows."15m"
    complete = [b for b in snap.bars["15m"] if b.complete][-3:]
    assert lines[1].startswith(complete[0].ts.isoformat()) and lines[-1].startswith(complete[-1].ts.isoformat())


def test_bars_table_without_a_row_count_is_a_problem(snap):
    r = render_round1(prompt("{{bars_table:1h}}"), snap, CFG)
    assert r == RenderProblem("config_invalid: render.bars_rows.1h: missing")


def test_unavailable_slots_say_so(snap):
    r = render_round1(prompt("L {{levels_json}} F {{features_json}} C {{charts}} A {{alert_json}}"), snap, CFG)
    text = r.user_text()
    assert unavailable_text("levels", NOT_BUILT["levels"]) in text
    assert unavailable_text("features", snap.unavailable["features"]) in text
    assert unavailable_text("charts", NOT_BUILT["charts"]) in text
    assert '"alert_id":"A20250611-004"' in text
    assert r.unavailable_used == ("levels", "features", "charts")


def test_charts_split_the_user_content(snap):
    r = render_round1(prompt("before {{charts}} after"), filled(snap), CFG)
    assert [type(p) for p in r.user] == [TextPart, ImagesPart, TextPart]
    assert [c.tf for c in r.user[1].charts] == ["15m", "1h"]
    assert r.user_text() == "before [images: 15m, 1h] after"


def test_snapshot_json_never_carries_the_experiment_id(snap):
    s = Snapshot(**{**snap.model_dump(exclude={"hash"}), "experiment_id": "exp-1"})
    text = render_round1(prompt("{{snapshot_json}}"), s, CFG).user_text()
    assert "exp-1" not in text and "experiment_id" not in text


def test_rendering_is_deterministic_and_never_touches_system(snap):
    p = prompt("{{alert_json}} {{bars_table:15m}}", system="static {not a placeholder}")
    a, b = render_round1(p, snap, CFG), render_round1(p, snap, CFG)
    assert a == b and a.system == "static {not a placeholder}"


def test_a_discussion_prompt_is_not_rendered_as_round1(snap):
    d = parse_prompt(b"---\nagent: discussion\nversion: 1\n---\n## user\n{{round}}\n", "discussion")
    assert isinstance(render_round1(d, snap, CFG), RenderProblem)
