"""Transcription via Groq's OpenAI-compatible Whisper endpoint.

Free tier: 2,000 requests/day, 7,200 audio-seconds/hour, org-level (extra keys do
not multiply quota). The endpoint accepts mp4 directly, which is why there is no
ffmpeg step anywhere on the box.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import structlog

from app.config import settings
from app.providers.deepseek import ProviderError

log = structlog.get_logger(__name__)

GROQ_ASR_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
# Groq rejects uploads above ~25MB on the free tier.
MAX_UPLOAD_BYTES = 24 * 1024 * 1024


async def transcribe(path: Path, language: str | None = None) -> dict[str, Any]:
    if not settings.groq_api_key:
        raise ProviderError("GROQ_API_KEY is not set")

    size = path.stat().st_size
    if size > MAX_UPLOAD_BYTES:
        raise ProviderError(
            f"{path.name} is {size / 1e6:.1f}MB; Groq's free tier caps uploads at 24MB"
        )

    data = {
        "model": settings.groq_asr_model,
        "response_format": "verbose_json",
        "timestamp_granularities[]": "segment",
    }
    if language:
        data["language"] = language

    async with httpx.AsyncClient() as client:
        with path.open("rb") as fh:
            r = await client.post(
                GROQ_ASR_URL,
                headers={"Authorization": f"Bearer {settings.groq_api_key}"},
                files={"file": (path.name, fh, "video/mp4")},
                data=data,
                timeout=180,
            )
    if r.status_code >= 400:
        raise ProviderError(f"Groq ASR {r.status_code}: {r.text[:300]}")

    payload = r.json()
    segments = [
        {
            "start": round(float(s.get("start", 0)), 2),
            "end": round(float(s.get("end", 0)), 2),
            "text": (s.get("text") or "").strip(),
            "no_speech_prob": s.get("no_speech_prob"),
        }
        for s in payload.get("segments", [])
    ]
    return {
        "text": (payload.get("text") or "").strip(),
        "language": payload.get("language"),
        "duration": payload.get("duration"),
        "segments": segments,
        "provider": "groq",
        "model": settings.groq_asr_model,
    }


def timeline(transcript: dict[str, Any]) -> str:
    """Render segments as `[12.4-15.9] text` lines for the claim-extraction prompt."""
    lines = [
        f"[{s['start']:.1f}-{s['end']:.1f}] {s['text']}"
        for s in transcript.get("segments", [])
        if s["text"]
    ]
    return "\n".join(lines) or transcript.get("text", "")
