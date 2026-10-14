"""
Unit tests for MetaDecisionService weighted-score renormalisation.

THE CLAIM UNDER TEST
    The weighted score is renormalised over the analysts that actually voted,
    so a unanimous call at a given confidence produces the SAME score whether
    one analyst voted or all three did.

Before renormalisation, a technical-only scan was capped at the technical
weight (0.50): even a max-confidence signal scored 0.50 * 0.9 = 0.45, below the
0.60 Meta gate, so the pipeline took zero trades regardless of signal quality.
That was a plumbing artifact of who happened to vote, not a property of the
signal — which is exactly what these tests pin down.
"""

from pytest import approx

from services.analysis.meta_decision import MetaDecisionService

# Default roster weights, named here so the tests read against known numbers.
WEIGHTS = {'visual': 0.35, 'technical': 0.50, 'sentiment': 0.15}

PASS = {'signal': 'PASS', 'confidence': 0.0}


def _service():
    return MetaDecisionService(weights=dict(WEIGHTS))


def test_score_invariant_one_vs_three_voters_unanimous():
    """
    THE headline invariant: unanimous LONG at confidence c scores c, whether
    only technical voted or all three did.
    """
    svc = _service()
    c = 0.80

    long_c = {'signal': 'LONG', 'confidence': c}

    # Only technical votes; visual and sentiment abstain (PASS).
    tech_only, _, _ = svc.calculate_weighted_score(
        visual=PASS, technical=long_c, sentiment=PASS
    )

    # All three vote LONG at the same confidence.
    all_three, _, _ = svc.calculate_weighted_score(
        visual=long_c, technical=long_c, sentiment=long_c
    )

    # Identical up to floating-point rounding from the renormalisation divide.
    assert tech_only == approx(c)
    assert all_three == approx(c)
    assert tech_only == approx(all_three)


def test_score_invariant_holds_for_any_single_voter():
    """The denominator is the voter's own weight, so any single unanimous voter
    — not just the heavily weighted technical one — scores its raw confidence."""
    svc = _service()
    c = 0.72
    vote = {'signal': 'LONG', 'confidence': c}

    for who in ('visual', 'technical', 'sentiment'):
        args = {'visual': PASS, 'technical': PASS, 'sentiment': PASS}
        args[who] = vote
        score, _, breakdown = svc.calculate_weighted_score(**args)
        assert score == approx(c), f"single {who} voter should score {c}, got {score}"
        assert breakdown['participating_weight'] == approx(WEIGHTS[who])


def test_technical_only_can_clear_the_gate():
    """The concrete regression: technical-only at 0.75 must now exceed the 0.60
    low gate. Before renormalisation it scored 0.375 and always PASSed."""
    svc = _service()
    score, agreement, _ = svc.calculate_weighted_score(
        visual=PASS,
        technical={'signal': 'LONG', 'confidence': 0.75},
        sentiment=PASS,
    )
    assert score == 0.75
    assert score >= MetaDecisionService.WEIGHTED_SCORE_THRESHOLD_LOW
    assert agreement == 1.0  # no opposition


def test_renormalisation_is_a_weighted_average_of_agreeing_voters():
    """Two voters agreeing at different confidences yield the weight-weighted
    average of their confidences, not a fraction of the full roster."""
    svc = _service()
    # visual LONG @0.6 (w=0.35), technical LONG @0.9 (w=0.50); sentiment PASS.
    score, agreement, breakdown = svc.calculate_weighted_score(
        visual={'signal': 'LONG', 'confidence': 0.6},
        technical={'signal': 'LONG', 'confidence': 0.9},
        sentiment=PASS,
    )
    expected = (0.6 * 0.35 + 0.9 * 0.50) / (0.35 + 0.50)
    assert abs(score - expected) < 1e-12
    assert breakdown['participating_weight'] == 0.85
    assert agreement == 1.0


def test_opposition_still_splits_the_score():
    """Disagreement is not hidden by renormalisation: the winning direction gets
    only its own share of the participating weight, and agreement drops."""
    svc = _service()
    # technical LONG @0.8 (w=0.50) vs visual SHORT @0.8 (w=0.35); sentiment PASS.
    score, agreement, breakdown = svc.calculate_weighted_score(
        visual={'signal': 'SHORT', 'confidence': 0.8},
        technical={'signal': 'LONG', 'confidence': 0.8},
        sentiment=PASS,
    )
    # LONG share: 0.8*0.50 / (0.50+0.35) = 0.4/0.85 ≈ 0.4706 — below the gate.
    assert abs(score - (0.8 * 0.50 / 0.85)) < 1e-12
    assert score < MetaDecisionService.WEIGHTED_SCORE_THRESHOLD_LOW
    assert agreement == 0.5  # 1 of 2 directional voters opposes


def test_all_pass_scores_zero():
    """No directional voters → zero participating weight → zero score, no
    divide-by-zero."""
    svc = _service()
    score, agreement, breakdown = svc.calculate_weighted_score(
        visual=PASS, technical=PASS, sentiment=PASS
    )
    assert score == 0.0
    assert breakdown['participating_weight'] == 0.0
