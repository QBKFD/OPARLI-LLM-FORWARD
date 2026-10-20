"""Meta rules (agents/meta): quorum_v0 keeps the old aggregator's behaviour;
fixed_meta_v1 fixes the legacy meta_decision bugs, with the
PORT_CANDIDATES reproductions as the cases. No configured rule = no decision."""

from datetime import datetime, timezone

import pytest

from agents.meta import RULES, FixedMetaV1, QuorumV0, get_rule, meta_decision, rule_version
from agents.owner_config import OwnerConfig
from agents.schemas import Bar, Snapshot, Vote

UTC = timezone.utc
AS_OF = datetime(2025, 6, 11, 14, 30, tzinfo=UTC)


def votes(*items):
    """items: 'long', 'short', 'none', 'abstain', or (direction, confidence)."""
    out = {}
    for i, it in enumerate(items):
        d, c = (it, 0.6) if isinstance(it, str) else it
        out[f"a{i}"] = Vote.abstained(f"a{i}", "x") if d == "abstain" else Vote(agent=f"a{i}", direction=d,
                                                                                    confidence=c)
    return out


# ---- quorum_v0 (unchanged from aggregator v0)

@pytest.mark.parametrize("dirs, want", [
    (("long", "long", "short"), "long"),
    (("short", "short", "long"), "short"),
    (("long", "short", "none"), "none"),
    (("long", "abstain", "abstain"), "none"),
    (("none", "none", "long"), "none"),
    (("long", "long", "long"), "long"),
    (("long", "long", "abstain"), "long"),
    (("abstain", "abstain", "abstain"), "none"),
])
def test_quorum_v0_two_agreeing_non_abstaining(dirs, want):
    assert QuorumV0().decide(votes(*dirs))[0] == want


def test_quorum_v0_abstains_do_not_count_and_both_directions_is_none():
    d, why = QuorumV0().decide(votes("long", "long", "abstain", "abstain", "abstain"))
    assert d == "long" and "abstain 3 of 5" in why
    assert QuorumV0().decide(votes("long", "long", "short", "short"))[1].endswith("both long and short reach 2")


def test_quorum_v0_ignores_confidence():
    assert QuorumV0().decide(votes(("long", 0.01), ("long", 0.01), ("short", 1.0)))[0] == "long"
    assert QuorumV0().decide(votes(("long", 1.0), "none", "none"))[0] == "none"


# ---- fixed_meta_v1: the legacy bugs, fixed

def test_fix1_direction_comes_from_the_winning_side():
    # legacy, weights .4/.2/.4: Visual SHORT .9 + Sentiment SHORT .9 + Technical LONG .55 gave LONG
    d, why = FixedMetaV1().decide(votes(("short", 0.9), ("short", 0.9), ("long", 0.55)))
    assert d == "short" and "low tier" in why


def test_fix2_two_against_one_can_pass_the_low_tier():
    # legacy: LONG .95 + LONG .95 + SHORT .1 -> agreement 0.6667 failed '>= 0.67' -> PASS
    d, why = FixedMetaV1().decide(votes(("long", 0.95), ("long", 0.95), ("short", 0.1)))
    assert d == "long" and "agreement 2/3" in why


def test_fix3_a_one_one_split_is_none():
    # legacy: Visual SHORT .9 vs Technical LONG .95 -> LONG
    d, why = FixedMetaV1().decide(votes(("short", 0.9), ("long", 0.95)))
    assert d == "none" and "strictly more votes" in why


def test_fix4_one_voter_cannot_decide_alone():
    d, why = FixedMetaV1().decide(votes(("long", 0.7), "none", "abstain"))
    assert d == "none" and "fewer than 2" in why


def test_tiers_are_exact_fractions():
    assert FixedMetaV1().decide(votes(("long", 0.7), ("long", 0.7)))[0] == "long"            # 0.7 >= 7/10 exactly
    assert FixedMetaV1().decide(votes(("long", 0.6), ("long", 0.6), ("short", 0.6)))[0] == "none"   # 0.4 < 0.6
    assert FixedMetaV1().decide(votes(("long", 0.6), ("long", 0.6)))[0] == "long"            # 0.6, agreement 1
    assert FixedMetaV1().decide(votes(("long", 0.59), ("long", 0.59)))[0] == "none"
    assert FixedMetaV1().decide(votes(("long", 0.5), ("short", 0.5), ("long", 0.0), ("short", 0.0)))[1] \
        .startswith("scores tie")


def test_none_votes_and_abstains_do_not_take_part():
    a = FixedMetaV1().decide(votes(("long", 0.9), ("long", 0.9)))
    b = FixedMetaV1().decide(votes(("long", 0.9), ("long", 0.9), "none", "abstain"))
    assert a == b


# ---- the registry and the node

def test_registry_and_lookup():
    assert sorted(RULES) == ["fixed_meta_v1", "quorum_v0"]
    assert all(r.version for r in RULES.values())
    assert get_rule(None) == "meta: no rule configured"
    assert get_rule("best_v9").startswith("meta: unknown rule 'best_v9'")


def snap():
    bar = Bar(ts=datetime(2025, 6, 11, 14, 29, tzinfo=UTC), open=1, high=1, low=1, close=1)
    return Snapshot(symbol="XAUUSD", as_of=AS_OF, kinds=("replay",), bars={"1m": (bar,)}, newest_bar_ts=bar.ts)


def test_meta_decision_uses_the_configured_rule():
    owner = OwnerConfig.from_text('[meta]\nrule = "fixed_meta_v1"\n')
    d = meta_decision(votes(("long", 0.95), ("long", 0.95), ("short", 0.1)), owner, "r", snap(), "blind")
    assert (d.direction, d.decided_by, d.meta_rule, d.meta_version, d.arm) == ("long", "fixed_meta_v1",
                                                                              "fixed_meta_v1", "1", "blind")
    assert rule_version(owner) == "1"


@pytest.mark.parametrize("text, why", [
    ("", "meta: no rule configured (config_invalid: meta: missing)"),
    ('[meta]\nrule = "best_v9"\n', "meta: unknown rule 'best_v9'"),
    ('[meta]\nrule = "<a Meta rule name>"\n', "meta: no rule configured (config_invalid: meta.rule: unfilled"),
])
def test_without_a_valid_rule_meta_decides_none_with_the_reason(text, why):
    owner = OwnerConfig.from_text(text)
    d = meta_decision(votes("long", "long"), owner, "r", snap(), "discussion")
    assert d.direction == "none" and d.reason.startswith(why) and d.meta_rule is None
    assert rule_version(owner) is None
