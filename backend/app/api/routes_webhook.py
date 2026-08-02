"""Meta webhook receiver — the primary sanctioned ingest path.

A user comments "@mythbuster check this" on any public reel; Meta pushes the event
here; we resolve the media via mentioned_media and queue an analysis.

Signature verification is mandatory. An unverified webhook endpoint is an open
injection point straight into the job queue.
"""

from __future__ import annotations

import hashlib
import hmac

import httpx
import structlog
from fastapi import APIRouter, Header, HTTPException, Request, Response
from sqlalchemy import select

from app.config import settings
from app.db import session_scope
from app.ingest.graph_api import fetch_via_mention
from app.models import Media, Mention, Report

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/webhook", tags=["webhook"])

MENTION_TRIGGER = "@" + "mythbuster"


@router.get("/instagram")
async def verify(request: Request) -> Response:
    """Meta's subscription handshake."""
    params = request.query_params
    if (
        params.get("hub.mode") == "subscribe"
        and params.get("hub.verify_token") == settings.meta_webhook_verify_token
    ):
        return Response(content=params.get("hub.challenge", ""), media_type="text/plain")
    raise HTTPException(403, "Verification failed")


def _valid_signature(body: bytes, header: str | None) -> bool:
    if not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(
        settings.meta_app_secret.encode(), body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, header.removeprefix("sha256="))


@router.post("/instagram")
async def receive(
    request: Request, x_hub_signature_256: str | None = Header(default=None)
) -> dict:
    body = await request.body()
    if not settings.meta_app_secret:
        raise HTTPException(503, "META_APP_SECRET not configured")
    if not _valid_signature(body, x_hub_signature_256):
        log.warning("webhook_bad_signature")
        raise HTTPException(403, "Invalid signature")

    payload = await request.json()
    queued = 0
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            if change.get("field") != "mentions":
                continue
            queued += await _handle_mention(change.get("value") or {})

    # Always 200 — Meta retries aggressively on anything else.
    return {"ok": True, "queued": queued}


async def _handle_mention(value: dict) -> int:
    comment_id = value.get("comment_id")
    media_id = value.get("media_id")
    if not media_id:
        return 0

    async with session_scope() as session:
        if comment_id:
            seen = (
                await session.execute(
                    select(Mention).where(Mention.ig_comment_id == comment_id)
                )
            ).scalar_one_or_none()
            if seen:
                return 0  # Meta redelivers; treat comment_id as the idempotency key.

        try:
            async with httpx.AsyncClient() as client:
                bundle = await fetch_via_mention(client, media_id)
        except Exception as exc:  # noqa: BLE001 - never fail the webhook response
            log.warning("mention_resolve_failed", media_id=media_id, error=str(exc))
            session.add(Mention(ig_comment_id=comment_id, ig_media_id=media_id))
            return 0

        media = (
            await session.execute(
                select(Media).where(Media.shortcode == bundle.shortcode)
            )
        ).scalar_one_or_none()
        if media is None:
            media = Media(shortcode=bundle.shortcode, ingest_path="mention")
            session.add(media)
            await session.flush()

        report = Report(
            media_id=media.id,
            pipeline_version=settings.pipeline_version,
            status="queued",
            stage="ingest",
        )
        session.add(report)
        session.add(
            Mention(
                ig_comment_id=comment_id,
                ig_media_id=media_id,
                requester=value.get("from", {}).get("username"),
                report_id=report.id,
            )
        )
        await session.flush()

        from app.api.routes_reports import enqueue

        await enqueue(report.id, bundle.permalink or bundle.shortcode)
        log.info("mention_queued", report_id=report.id, shortcode=bundle.shortcode)
        return 1
