"""Client for the off-box forensics microservice (a Hugging Face Space).

Pixel work never runs on oracle-dev. The Space pulls a signed media URL, runs its
own ffmpeg + ONNX detectors, and returns a signal vector.

Raw detector scores are NOT probabilities. 2026 benchmarks (AIGVDBench: 31
generators, 440k videos) show detectors collapse on unseen generators, and
Instagram's re-encoding destroys forensic traces. Everything below is therefore
calibrated and confidence-gated before it can move the overall score.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx
import structlog

from app.config import settings

log = structlog.get_logger(__name__)


@dataclass(slots=True)
class Signal:
    name: str
    raw_score: float | None
    calibrated_prob: float | None
    confidence: float
    detail: dict[str, Any]


# Piecewise calibration anchors per signal: raw -> P(manipulated). Placeholders until
# fitted on a labelled set; deliberately flat and unconfident so an uncalibrated
# detector cannot dominate a verdict. Refit via eval/calibrate.py.
CALIBRATION: dict[str, list[tuple[float, float]]] = {
    "ai_generated_frames": [(0.0, 0.05), (0.5, 0.30), (0.8, 0.62), (1.0, 0.80)],
    "face_manipulation": [(0.0, 0.05), (0.5, 0.32), (0.8, 0.65), (1.0, 0.82)],
    "splice_recompression": [(0.0, 0.10), (0.5, 0.35), (1.0, 0.60)],
    "audio_spoof": [(0.0, 0.05), (0.5, 0.40), (1.0, 0.75)],
}

# How much each signal is trusted when it fires. C2PA is cryptographic; the rest
# are statistical guesses.
SIGNAL_CONFIDENCE = {
    "c2pa": 0.95,
    "recycled_footage": 0.85,
    "container_forensics": 0.55,
    "audio_spoof": 0.45,
    "face_manipulation": 0.40,
    "ai_generated_frames": 0.35,
    "splice_recompression": 0.30,
}


def calibrate(signal: str, raw: float | None) -> float | None:
    """Piecewise-linear map from a raw detector score to a calibrated probability."""
    if raw is None:
        return None
    anchors = CALIBRATION.get(signal)
    if not anchors:
        return raw
    if raw <= anchors[0][0]:
        return anchors[0][1]
    for (x0, y0), (x1, y1) in zip(anchors, anchors[1:], strict=False):
        if raw <= x1:
            span = x1 - x0
            return y0 if span == 0 else y0 + (raw - x0) * (y1 - y0) / span
    return anchors[-1][1]


async def analyse(media_url: str, shortcode: str) -> list[Signal]:
    """Returns [] when the service is unconfigured — forensics is optional by design."""
    if not settings.forensics_url:
        log.info("forensics_skipped", reason="FORENSICS_URL not set")
        return []

    headers = (
        {"Authorization": f"Bearer {settings.forensics_token}"}
        if settings.forensics_token
        else {}
    )
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(
                settings.forensics_url.rstrip("/") + "/analyse",
                json={"media_url": media_url, "shortcode": shortcode},
                headers=headers,
                timeout=240,
            )
            r.raise_for_status()
            payload = r.json()
    except httpx.HTTPError as exc:
        log.warning("forensics_unavailable", error=str(exc))
        return []

    signals: list[Signal] = []
    for name, node in (payload.get("signals") or {}).items():
        raw = node.get("score")
        signals.append(
            Signal(
                name=name,
                raw_score=raw,
                calibrated_prob=calibrate(name, raw),
                confidence=SIGNAL_CONFIDENCE.get(name, 0.3),
                detail=node.get("detail") or {},
            )
        )
    return signals


def aggregate(signals: list[Signal]) -> tuple[float | None, float]:
    """Confidence-weighted manipulation probability, plus how much to trust it.

    Returns (manipulation_prob, overall_confidence). With no signals, confidence is 0
    and the caller must not let forensics move the validity score at all.
    """
    usable = [s for s in signals if s.calibrated_prob is not None]
    if not usable:
        return None, 0.0

    weight = sum(s.confidence for s in usable)
    prob = sum(s.calibrated_prob * s.confidence for s in usable) / weight

    # Confidence grows with signal count but saturates — seven weak detectors do not
    # add up to certainty.
    coverage = min(len(usable) / 5.0, 1.0)
    mean_conf = weight / len(usable)
    return round(prob, 3), round(coverage * mean_conf, 3)
