from __future__ import annotations

import structlog
from arq.connections import RedisSettings

from app.config import settings
from app.db import session_scope
from app.models import Report
from app.pipeline.runner import run_pipeline

log = structlog.get_logger(__name__)


async def analyse_report(ctx: dict, report_id: str, url: str) -> str:
    async with session_scope() as session:
        report = await session.get(Report, report_id)
        if report is None:
            log.warning("report_missing", report_id=report_id)
            return "missing"
        await run_pipeline(session, report, url)
        return report.status


async def reply_to_mention(ctx: dict, mention_id: str) -> str:
    """Post the report link back as a comment reply. Wired in Phase 5, post App Review."""
    from app.instagram.replies import send_reply

    return await send_reply(mention_id)


class WorkerSettings:
    functions = [analyse_report, reply_to_mention]
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    # I/O-bound, but free-tier RPM limits — not cores — are the real ceiling.
    max_jobs = settings.worker_concurrency
    job_timeout = 900
    keep_result = 3600
