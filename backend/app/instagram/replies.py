"""Public replies to @mentions.

Replying on someone else's reel publishes an accusation in their comment section.
Policy (ARCHITECTURE.md §11): post a neutral link, never a verdict in the comment
text. The reader clicks through to the full report with its caveats and citations
attached — a bare "FALSE" in a comment strips all of that away.
"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import structlog
from sqlalchemy import select

from app.config import settings
from app.db import session_scope
from app.models import Mention, Report

log = structlog.get_logger(__name__)

TEMPLATE = "Automated claim-by-claim analysis with sources: {url}"


async def send_reply(mention_id: str) -> str:
    async with session_scope() as session:
        mention = await session.get(Mention, mention_id)
        if mention is None or mention.replied_at or not mention.ig_comment_id:
            return "skipped"

        report = (
            await session.execute(select(Report).where(Report.id == mention.report_id))
        ).scalar_one_or_none()
        if report is None or report.status != "done":
            return "not_ready"

        url = f"{settings.web_base_url.rstrip('/')}/report/{report.id}"
        async with httpx.AsyncClient() as client:
            r = await client.post(
                f"{settings.graph_base}/{mention.ig_comment_id}/replies",
                params={
                    "message": TEMPLATE.format(url=url),
                    "access_token": settings.ig_access_token,
                },
                timeout=20,
            )
        if r.status_code >= 400:
            log.warning("reply_failed", status=r.status_code, body=r.text[:200])
            return "failed"

        mention.replied_at = datetime.now(UTC)
        return "sent"
