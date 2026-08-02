from app.scoring.validity import compute


def claim(verdict, cw=0.8, conf=0.9, quality="strong"):
    return {
        "verdict": verdict,
        "checkworthiness": cw,
        "confidence": conf,
        "evidence_quality": quality,
    }


def test_all_true_scores_high():
    r = compute([claim("true"), claim("true"), claim("true")])
    assert r.score == 100.0
    assert r.claims_scored == 3


def test_all_false_scores_zero():
    r = compute([claim("false"), claim("false")])
    assert r.score == 0.0


def test_unverifiable_is_excluded_not_scored_as_half():
    """The whole point: a gap in knowledge must not drag a true claim to the middle."""
    both = compute([claim("true"), claim("unverifiable")])
    only_true = compute([claim("true")])
    assert both.score == only_true.score == 100.0
    assert both.claims_unverifiable == 1
    assert both.claims_scored == 1


def test_no_verifiable_claims_yields_no_score():
    r = compute([claim("unverifiable"), claim("unverifiable")])
    assert r.score is None
    assert "No claim could be verified" in r.notes[0]


def test_empty_claim_list_is_not_a_failure():
    r = compute([])
    assert r.score is None
    assert "No check-worthy factual claims" in r.notes[0]


def test_opinions_never_count_against_validity():
    r = compute([claim("true"), claim("opinion")])
    assert r.score == 100.0
    assert r.claims_opinion == 1


def test_thin_evidence_widens_the_interval():
    """Two weakly-evidenced claims must not read as confidently as eight strong ones."""
    thin = compute([claim("true", quality="weak"), claim("true", quality="weak")])
    thick = compute([claim("true") for _ in range(8)])
    assert (thin.ci_high - thin.ci_low) > (thick.ci_high - thick.ci_low)


def test_low_forensic_confidence_cannot_move_the_score():
    base = compute([claim("true")])
    weak = compute([claim("true")], manipulation_prob=0.9, forensics_confidence=0.1)
    assert weak.score == base.score
    assert weak.manipulation_penalty == 0.0
    assert any("too weak" in n for n in weak.notes)


def test_confident_forensics_applies_a_bounded_penalty():
    r = compute([claim("true")], manipulation_prob=1.0, forensics_confidence=1.0)
    assert r.score == 65.0  # capped at a 35-point deduction
    assert r.manipulation_penalty == 35.0


def test_checkworthiness_weights_the_average():
    """A false throwaway line must not sink a video whose important claims check out."""
    r = compute([claim("true", cw=1.0), claim("false", cw=0.1)])
    assert r.score > 85


def test_score_never_leaves_zero_to_hundred():
    r = compute([claim("false")], manipulation_prob=1.0, forensics_confidence=1.0)
    assert 0.0 <= r.score <= 100.0
    assert 0.0 <= r.ci_low <= r.ci_high <= 100.0
