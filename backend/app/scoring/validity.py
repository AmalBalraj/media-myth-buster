"""Validity scoring — a published formula, never a black box.

Every term here is surfaced in the UI's "how this was calculated" panel.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

# Verdict -> validity contribution (0 = false, 1 = true).
VERDICT_VALUE = {
    "true": 1.0,
    "mostly_true": 0.8,
    "mixed": 0.5,
    "mostly_false": 0.2,
    "false": 0.0,
}
# Excluded from the average entirely rather than scored as 0.5 — an unverifiable
# claim is a gap in knowledge, not a half-truth.
EXCLUDED = {"unverifiable", "opinion"}

EVIDENCE_WEIGHT = {"strong": 1.0, "moderate": 0.75, "weak": 0.4, "none": 0.0}


@dataclass(slots=True)
class ValidityResult:
    score: float | None
    ci_low: float | None
    ci_high: float | None
    claims_total: int
    claims_scored: int
    claims_unverifiable: int
    claims_opinion: int
    manipulation_penalty: float
    subscores: dict[str, float | None]
    notes: list[str]


def _wilson_interval(mean: float, n: float) -> tuple[float, float]:
    """Wilson score interval, treating the weighted mean as a proportion.

    Effective sample size n is the summed weight, so two lightly-weighted claims
    produce a wide band — which is the honest output.
    """
    if n <= 0:
        return 0.0, 1.0
    z = 1.96
    denom = 1 + z**2 / n
    centre = (mean + z**2 / (2 * n)) / denom
    margin = z * math.sqrt(max(mean * (1 - mean) / n + z**2 / (4 * n**2), 0.0)) / denom
    return max(centre - margin, 0.0), min(centre + margin, 1.0)


def compute(
    claims: list[dict],
    manipulation_prob: float | None = None,
    forensics_confidence: float = 0.0,
    source_credibility: float | None = None,
) -> ValidityResult:
    notes: list[str] = []
    total = len(claims)
    unverifiable = sum(1 for c in claims if c.get("verdict") == "unverifiable")
    opinion = sum(1 for c in claims if c.get("verdict") == "opinion")

    numerator = denominator = 0.0
    scored = 0
    for c in claims:
        verdict = c.get("verdict")
        if verdict in EXCLUDED or verdict not in VERDICT_VALUE:
            continue
        # Weight by how much the claim matters, how sure we are, and how good the
        # evidence was. A confident verdict on weak evidence should not dominate.
        weight = (
            max(float(c.get("checkworthiness") or 0.5), 0.05)
            * max(float(c.get("confidence") or 0.5), 0.1)
            * EVIDENCE_WEIGHT.get(c.get("evidence_quality") or "moderate", 0.75)
        )
        numerator += VERDICT_VALUE[verdict] * weight
        denominator += weight
        scored += 1

    if denominator <= 0:
        notes.append(
            "No claim could be verified against evidence, so no validity score is shown."
            if total
            else "No check-worthy factual claims were found in this video."
        )
        return ValidityResult(
            None, None, None, total, 0, unverifiable, opinion, 0.0,
            {"factual_accuracy": None, "source_quality": source_credibility,
             "manipulation_integrity": None}, notes,
        )

    mean = numerator / denominator
    lo, hi = _wilson_interval(mean, denominator)

    # Forensics can only move the score as far as we trust the forensics.
    penalty = 0.0
    if manipulation_prob is not None and forensics_confidence > 0.15:
        penalty = manipulation_prob * forensics_confidence * 0.35
        notes.append(
            f"Manipulation signals reduced the score by {penalty * 100:.0f} points "
            f"(probability {manipulation_prob:.2f} at confidence {forensics_confidence:.2f})."
        )
    elif manipulation_prob is not None:
        notes.append(
            "Forensic signals were too weak or too few to affect the score; "
            "they are reported for information only."
        )

    if scored < 3:
        notes.append(
            f"Only {scored} claim(s) could be scored, so the confidence interval is wide."
        )
    if unverifiable > scored:
        notes.append(
            f"{unverifiable} of {total} claims could not be verified — treat the score "
            "as covering only the minority that could be checked."
        )

    final = max(0.0, min(mean - penalty, 1.0))
    return ValidityResult(
        score=round(final * 100, 1),
        ci_low=round(max(lo - penalty, 0.0) * 100, 1),
        ci_high=round(min(hi - penalty, 1.0) * 100, 1),
        claims_total=total,
        claims_scored=scored,
        claims_unverifiable=unverifiable,
        claims_opinion=opinion,
        manipulation_penalty=round(penalty * 100, 1),
        subscores={
            "factual_accuracy": round(mean * 100, 1),
            "source_quality": round(source_credibility * 100, 1) if source_credibility else None,
            "manipulation_integrity": (
                round((1 - manipulation_prob) * 100, 1) if manipulation_prob is not None else None
            ),
        },
        notes=notes,
    )


FORMULA = (
    "validity = Σ(wᵢ · verdict_valueᵢ) / Σ(wᵢ) − manipulation_penalty, "
    "where wᵢ = checkworthinessᵢ × confidenceᵢ × evidence_qualityᵢ. "
    "Unverifiable claims and opinions are excluded from both sums. "
    "The interval is a Wilson score interval over the summed weight."
)
