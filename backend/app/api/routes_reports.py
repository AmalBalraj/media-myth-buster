from __future__ import annotations

import asyncio
import json

from arq import create_pool
from arq.connections import RedisSettings
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sse_starlette.sse import EventSourceResponse

from app import events
from app.config import settings
from app.db import get_session
from app.ingest.base import canonical_shortcode
from app.models import Claim, Media, Report
from app.schemas import AnalyseRequest, AnalyseResponse, ReportOut

router = APIRouter(tags=["reports"])


async def enqueue(report_id: str, url: str) -> None:
    pool = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    try:
        await pool.enqueue_job("analyse_report", report_id, url)
    finally:
        await pool.aclose()


@router.post("/analyse", response_model=AnalyseResponse)
async def analyse(
    body: AnalyseRequest, request: Request, session: AsyncSession = Depends(get_session)
) -> AnalyseResponse:
    shortcode = canonical_shortcode(body.url)

    if not body.force:
        cached = (
            await session.execute(
                select(Report)
                .join(Media, Report.media_id == Media.id)
                .where(
                    Media.shortcode == shortcode,
                    Report.pipeline_version == settings.pipeline_version,
                    Report.status.in_(("done", "running", "queued")),
                )
                .order_by(Report.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if cached:
            return AnalyseResponse(
                report_id=cached.id, status=cached.status, cached=True, shortcode=shortcode
            )

    # The Media row is created by the ingest stage; the report is the handle the
    # client polls in the meantime.
    placeholder = (
        await session.execute(select(Media).where(Media.shortcode == shortcode))
    ).scalar_one_or_none()
    if placeholder is None:
        placeholder = Media(shortcode=shortcode, ingest_path="pending")
        session.add(placeholder)
        await session.flush()

    report = Report(
        media_id=placeholder.id,
        pipeline_version=settings.pipeline_version,
        status="queued",
        stage="ingest",
    )
    session.add(report)
    await session.commit()

    await enqueue(report.id, body.url)
    return AnalyseResponse(
        report_id=report.id, status="queued", cached=False, shortcode=shortcode
    )


@router.get("/reports/{report_id}", response_model=ReportOut)
async def get_report(
    report_id: str, session: AsyncSession = Depends(get_session)
) -> Report:
    report = (
        await session.execute(
            select(Report)
            .where(Report.id == report_id)
            .options(
                selectinload(Report.media).selectinload(Media.creator),
                selectinload(Report.claims).selectinload(Claim.evidence),
                selectinload(Report.forensics),
            )
        )
    ).scalar_one_or_none()
    if report is None:
        raise HTTPException(404, "Report not found")
    report.claims.sort(key=lambda c: c.idx)
    return report


@router.get("/reports/{report_id}/events")
async def report_events(report_id: str, request: Request) -> EventSourceResponse:
    """SSE progress. Replays completed stages first so a late client isn't blind."""

    async def stream():
        for past in await events.replay(report_id):
            yield {"event": "stage", "data": json.dumps(past)}

        queue: asyncio.Queue = asyncio.Queue()

        async def pump() -> None:
            async for msg in events.subscribe(report_id):
                await queue.put(msg)

        task = asyncio.create_task(pump())
        try:
            while not await request.is_disconnected():
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=15)
                except TimeoutError:
                    yield {"event": "ping", "data": "{}"}
                    continue
                yield {"event": "stage", "data": json.dumps(msg)}
                if msg.get("stage") == "done":
                    break
        finally:
            task.cancel()

    return EventSourceResponse(stream())


@router.get("/reports")
async def recent_reports(
    limit: int = 20, session: AsyncSession = Depends(get_session)
) -> list[dict]:
    rows = (
        await session.execute(
            select(Report, Media)
            .join(Media, Report.media_id == Media.id)
            .where(Report.status == "done")
            .order_by(Report.created_at.desc())
            .limit(min(limit, 100))
        )
    ).all()
    return [
        {
            "id": r.id,
            "shortcode": m.shortcode,
            "permalink": m.permalink,
            "validity_score": r.validity_score,
            "summary": r.summary,
            "created_at": r.created_at,
        }
        for r, m in rows
    ]
