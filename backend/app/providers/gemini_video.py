"""Video understanding via the Gemini File API.

Replaces every local vision step: on-screen text OCR, visual-audio consistency,
staged/recycled-footage cues, visible source attribution. Gemini samples frames
server-side, so the box never extracts one.

Free tier (shared 250k TPM, full 1M context):
  2.5 Flash-Lite  15 RPM / 1000 RPD   volume work
  2.5 Flash       10 RPM /  250 RPD   main video pass
  2.5 Pro          5 RPM /  100 RPD   hard cases
"""

from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path
from typing import Any

import httpx
import structlog

from app.config import settings
from app.providers.deepseek import ProviderError, _extract_json

log = structlog.get_logger(__name__)

BASE = "https://generativelanguage.googleapis.com"
UPLOAD_URL = f"{BASE}/upload/v1beta/files"

VIDEO_PROMPT = """You are a forensic media analyst examining a short-form social video.

Report only what is VISIBLE. Never infer facts from world knowledge — another system
verifies factual accuracy. Your job is describing what the video shows and how it is put
together.

Return JSON with exactly these keys:
{
  "on_screen_text": [{"t_start": float, "t_end": float, "text": str, "kind": "caption|chyron|screenshot|watermark|other"}],
  "visual_summary": str,
  "scenes": [{"t_start": float, "t_end": float, "description": str, "footage_kind": "original|stock|screen_recording|archival|graphic|unclear"}],
  "audio_visual_consistency": {"score": float, "notes": str},
  "staging_cues": [str],
  "visible_attribution": [str],
  "people_on_screen": int,
  "quality_flags": [str]
}

Guidance:
- on_screen_text drives claim extraction. Transcribe burned-in captions, chyrons, and
  text inside screenshots verbatim. This is the most important field.
- audio_visual_consistency.score: 1.0 = footage clearly shows what the narration
  describes; 0.0 = footage is unrelated B-roll. Use 0.5 when unclear.
- staging_cues: signs of reenactment, scripted delivery, or misleading juxtaposition.
- visible_attribution: watermarks, handles, outlet logos, cited sources shown on screen.
- quality_flags: heavy compression, visible cuts mid-sentence, obvious green screen.
"""


async def _upload(client: httpx.AsyncClient, path: Path) -> str:
    size = path.stat().st_size
    start = await client.post(
        UPLOAD_URL,
        params={"key": settings.gemini_api_key},
        headers={
            "X-Goog-Upload-Protocol": "resumable",
            "X-Goog-Upload-Command": "start",
            "X-Goog-Upload-Header-Content-Length": str(size),
            "X-Goog-Upload-Header-Content-Type": "video/mp4",
            "Content-Type": "application/json",
        },
        json={"file": {"display_name": path.stem}},
        timeout=60,
    )
    if start.status_code >= 400:
        raise ProviderError(f"Gemini upload start {start.status_code}: {start.text[:300]}")

    session_url = start.headers.get("x-goog-upload-url")
    if not session_url:
        raise ProviderError("Gemini upload start returned no upload URL")

    finish = await client.post(
        session_url,
        headers={
            "Content-Length": str(size),
            "X-Goog-Upload-Offset": "0",
            "X-Goog-Upload-Command": "upload, finalize",
        },
        content=path.read_bytes(),
        timeout=300,
    )
    if finish.status_code >= 400:
        raise ProviderError(f"Gemini upload {finish.status_code}: {finish.text[:300]}")

    file_obj = finish.json().get("file", {})
    name, uri = file_obj.get("name"), file_obj.get("uri")
    if not uri:
        raise ProviderError("Gemini upload returned no file URI")

    # Video files sit in PROCESSING while Gemini indexes frames; generateContent
    # fails if we reference them too early.
    for _ in range(60):
        if file_obj.get("state") == "ACTIVE":
            return uri
        if file_obj.get("state") == "FAILED":
            raise ProviderError("Gemini failed to process the uploaded video")
        await asyncio.sleep(2)
        poll = await client.get(
            f"{BASE}/v1beta/{name}", params={"key": settings.gemini_api_key}, timeout=30
        )
        file_obj = poll.json()
    raise ProviderError("Gemini file stayed in PROCESSING for 2 minutes")


# Free-tier capacity is shared, so 503 ("high demand") and 429 are routine rather
# than exceptional. Retrying costs nothing against the quota — a rejected request
# is not a billed one — and falling back to the lighter model beats losing the
# only source of on-screen text.
RETRY_STATUSES = {429, 500, 502, 503, 504}
RETRY_BACKOFF = (4, 12, 30)


async def _generate(
    client: httpx.AsyncClient, model: str, parts: list[dict[str, Any]]
) -> dict[str, Any]:
    body = {
        "contents": [{"parts": parts}],
        "generationConfig": {
            "temperature": 0.1,
            "responseMimeType": "application/json",
            "maxOutputTokens": 8192,
        },
    }
    models = [model]
    if model != settings.gemini_light_model:
        models.append(settings.gemini_light_model)

    last = ""
    for candidate in models:
        for attempt, wait in enumerate((*RETRY_BACKOFF, None)):
            r = await client.post(
                f"{BASE}/v1beta/models/{candidate}:generateContent",
                params={"key": settings.gemini_api_key},
                json=body,
                timeout=300,
            )
            if r.status_code < 400:
                return r.json()

            last = f"{r.status_code}: {r.text[:200]}"
            if r.status_code not in RETRY_STATUSES or wait is None:
                break
            log.warning(
                "gemini_retrying", model=candidate, status=r.status_code,
                attempt=attempt, sleeping=wait,
            )
            await asyncio.sleep(wait)

        log.warning("gemini_model_exhausted", model=candidate, error=last)

    raise ProviderError(f"Gemini generate {last}")


async def _delete(client: httpx.AsyncClient, uri: str) -> None:
    name = uri.split("/v1beta/")[-1] if "/v1beta/" in uri else uri.rsplit("files/", 1)[-1]
    name = name if name.startswith("files/") else f"files/{name}"
    try:
        await client.delete(
            f"{BASE}/v1beta/{name}", params={"key": settings.gemini_api_key}, timeout=20
        )
    except httpx.HTTPError:
        log.info("gemini_cleanup_failed", uri=uri)  # files expire in 48h anyway


async def analyse_video(path: Path, model: str | None = None) -> dict[str, Any]:
    if not settings.gemini_api_key:
        raise ProviderError("GEMINI_API_KEY is not set")
    model = model or settings.gemini_video_model

    async with httpx.AsyncClient() as client:
        uri = await _upload(client, path)
        try:
            payload = await _generate(
                client,
                model,
                [
                    {"file_data": {"mime_type": "video/mp4", "file_uri": uri}},
                    {"text": VIDEO_PROMPT},
                ],
            )
        finally:
            await _delete(client, uri)

    try:
        text = payload["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError) as exc:
        raise ProviderError(f"Unexpected Gemini response shape: {json.dumps(payload)[:300]}") from exc

    return _coerce_analysis(_extract_json(text), model)


def _coerce_analysis(result: Any, model: str) -> dict[str, Any]:
    """Normalise the response shape.

    Despite responseMimeType=application/json and a schema in the prompt, Gemini
    sometimes returns a top-level array — either the object wrapped in a list, or
    the on_screen_text entries alone. Assuming a dict here crashed the whole
    pipeline on a stage that is meant to be optional.
    """
    if isinstance(result, dict):
        result["_model"] = model
        return result

    if isinstance(result, list):
        # A single wrapped object is the common case.
        if len(result) == 1 and isinstance(result[0], dict) and "on_screen_text" in result[0]:
            result[0]["_model"] = model
            return result[0]
        # Otherwise treat a list of spans as the OCR payload; it is the field the
        # claim extractor actually depends on.
        spans = [x for x in result if isinstance(x, dict) and "text" in x]
        if spans:
            log.info("gemini_returned_bare_span_list", count=len(spans))
            return {"on_screen_text": spans, "_model": model, "_coerced": True}

    raise ProviderError(
        f"Gemini returned an unusable shape ({type(result).__name__}); "
        "expected an object with on_screen_text"
    )


IMAGE_PROMPT = """You are a forensic media analyst examining a social-media photo post.

The images are the slides of one post, in order. Report only what is VISIBLE — another
system verifies factual accuracy. Transcribing the text is by far the most important
part: these posts carry their claims as text on the image, and nothing else in the
pipeline can read them.

Return JSON with exactly these keys:
{
  "on_screen_text": [{"t_start": float, "t_end": float, "text": str, "kind": "caption|chyron|screenshot|watermark|other"}],
  "visual_summary": str,
  "scenes": [{"t_start": float, "t_end": float, "description": str, "footage_kind": "original|stock|screen_recording|archival|graphic|unclear"}],
  "audio_visual_consistency": {"score": float, "notes": str},
  "staging_cues": [str],
  "visible_attribution": [str],
  "people_on_screen": int,
  "quality_flags": [str]
}

Because there is no timeline, use the slide index for t_start and t_end: slide 1 is
t_start 0 t_end 1, slide 2 is 1 to 2, and so on. One on_screen_text entry per slide,
containing that slide's full text verbatim, in its original script — do not translate.
Set audio_visual_consistency.score to 1.0 and note "photo post, no audio".
visible_attribution: cited sources, watermarks, handles, or logos shown on the slides.
"""


async def analyse_images(paths: list[Path], model: str | None = None) -> dict[str, Any]:
    """Read a photo post or carousel.

    Slides go inline as base64 parts in one request rather than through the File
    API: they are small, it is a single round trip, and there is nothing to clean
    up afterwards.
    """
    if not settings.gemini_api_key:
        raise ProviderError("GEMINI_API_KEY is not set")
    if not paths:
        raise ProviderError("No images to analyse")
    model = model or settings.gemini_video_model

    parts: list[dict[str, Any]] = []
    for path in paths[:12]:
        mime = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
        parts.append(
            {
                "inline_data": {
                    "mime_type": mime,
                    "data": base64.b64encode(path.read_bytes()).decode(),
                }
            }
        )
    parts.append({"text": IMAGE_PROMPT})

    async with httpx.AsyncClient() as client:
        payload = await _generate(client, model, parts)
    try:
        text = payload["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError) as exc:
        raise ProviderError(
            f"Unexpected Gemini response shape: {json.dumps(payload)[:300]}"
        ) from exc

    result = _coerce_analysis(_extract_json(text), model)
    result["_slides"] = len(parts) - 1
    return result


def ocr_timeline(analysis: dict[str, Any]) -> str:
    """Render on-screen text as timestamped lines for the claim-extraction prompt.

    Reels put a large share of their claims in burned-in captions; a transcript-only
    pipeline silently misses them.
    """
    lines = []
    for item in analysis.get("on_screen_text", []) or []:
        text = (item.get("text") or "").strip()
        if not text:
            continue
        start = item.get("t_start")
        stamp = f"[{float(start):.1f}]" if isinstance(start, (int, float)) else "[?]"
        lines.append(f"{stamp} ({item.get('kind', 'other')}) {text}")
    return "\n".join(lines)
