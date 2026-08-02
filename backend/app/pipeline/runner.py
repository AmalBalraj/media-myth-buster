"""The analysis pipeline.

Everything here is orchestration: fetch bytes, call hosted APIs, persist rows.
No transcoding, no model inference, no frame extraction on this machine.
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import events
from app.config import settings
from app.evidence import credibility as cred
from app.evidence import evidence_for_claim, render_pack
from app.ingest import IngestError, MediaBundle, download_media, resolve
from app.models import Claim, Creator, Evidence, ForensicSignal, LLMCall, Media, Report
from app.pipeline import prompts
from app.providers import forensics as forensics_api
from app.providers import gemini_video, groq_asr
from app.providers.deepseek import DeepSeek, ProviderError
from app.scoring import creator as creator_scoring
from app.scoring import validity

log = structlog.get_logger(__name__)

# Free-tier RPM limits are the binding constraint on fan-out, not CPU.
ADJUDICATION_CONCURRENCY = 3


class PipelineError(RuntimeError):
    pass


async def _emit(report_id: str, stage: str, status: str, **extra: Any) -> None:
    await events.publish(report_id, stage, status, **extra)


async def _upsert_creator(session: AsyncSession, bundle: MediaBundle) -> Creator | None:
    if not bundle.creator or not bundle.creator.handle:
        return None
    info = bundle.creator
    existing = (
        await session.execute(
            select(Creator).where(
                Creator.platform == bundle.platform, Creator.handle == info.handle
            )
        )
    ).scalar_one_or_none()

    if existing:
        for field in ("display_name", "ig_user_id", "followers", "media_count", "biography"):
            value = getattr(info, field)
            if value is not None:
                setattr(existing, field, value)
        existing.is_professional = existing.is_professional or info.is_professional
        return existing

    creator = Creator(
        platform=bundle.platform,
        handle=info.handle,
        display_name=info.display_name,
        ig_user_id=info.ig_user_id,
        followers=info.followers,
        media_count=info.media_count,
        biography=info.biography,
        is_professional=info.is_professional,
        verified=info.verified,
    )
    session.add(creator)
    await session.flush()
    return creator


async def _stage_ingest(session: AsyncSession, report: Report, url: str) -> tuple[Media, Path]:
    bundle = await resolve(url)
    if not bundle.has_video:
        raise PipelineError("Resolved the post but it exposes no downloadable video")

    path, digest = await download_media(bundle)
    creator = await _upsert_creator(session, bundle)

    media = (
        await session.execute(
            select(Media).where(
                Media.platform == bundle.platform, Media.shortcode == bundle.shortcode
            )
        )
    ).scalar_one_or_none()
    if media is None:
        media = Media(platform=bundle.platform, shortcode=bundle.shortcode)
        session.add(media)

    media.permalink = bundle.permalink
    media.url_hash = digest
    media.ingest_path = bundle.ingest_path
    media.media_type = bundle.media_type
    media.caption = bundle.caption
    media.posted_at = bundle.posted_at
    media.like_count = bundle.like_count
    media.comment_count = bundle.comment_count
    media.view_count = bundle.view_count
    media.storage_key = str(path)
    media.creator_id = creator.id if creator else None

    await session.flush()
    report.media_id = media.id
    return media, path


async def _stage_transcribe(report_id: str, path: Path) -> dict[str, Any]:
    await _emit(report_id, "transcribe", "start")
    try:
        result = await groq_asr.transcribe(path)
    except ProviderError as exc:
        log.warning("asr_failed", error=str(exc))
        await _emit(report_id, "transcribe", "error", message=str(exc))
        return {"text": "", "segments": [], "error": str(exc)}
    await _emit(
        report_id, "transcribe", "done",
        chars=len(result.get("text", "")), language=result.get("language"),
    )
    return result


async def _stage_video(report_id: str, path: Path) -> dict[str, Any]:
    await _emit(report_id, "video", "start")
    try:
        result = await gemini_video.analyse_video(path)
    except ProviderError as exc:
        log.warning("video_analysis_failed", error=str(exc))
        await _emit(report_id, "video", "error", message=str(exc))
        return {"error": str(exc), "on_screen_text": []}
    await _emit(
        report_id, "video", "done", ocr_spans=len(result.get("on_screen_text") or [])
    )
    return result


async def _stage_forensics(report_id: str, media_url: str, shortcode: str) -> list:
    await _emit(report_id, "forensics", "start")
    signals = await forensics_api.analyse(media_url, shortcode)
    await _emit(report_id, "forensics", "done", signals=len(signals))
    return signals


async def _stage_claims(
    ds: DeepSeek, report_id: str, transcript: dict, video: dict, caption: str | None
) -> list[dict]:
    await _emit(report_id, "claims", "start")
    user = "\n\n".join(
        [
            f"TRANSCRIPT (timestamped):\n{groq_asr.timeline(transcript) or '(none)'}",
            f"ON-SCREEN TEXT (OCR):\n{gemini_video.ocr_timeline(video) or '(none)'}",
            f"CAPTION:\n{caption or '(none)'}",
            f"VISUAL SUMMARY:\n{video.get('visual_summary') or '(none)'}",
        ]
    )
    payload = await ds.json(
        system=prompts.CLAIM_EXTRACTION_SYSTEM, user=user, stage="claims", max_tokens=6000
    )
    claims = payload.get("claims", []) if isinstance(payload, dict) else []
    await _emit(report_id, "claims", "done", count=len(claims))
    return claims


async def _adjudicate_one(
    session: AsyncSession, ds: DeepSeek, claim: dict
) -> tuple[dict, list]:
    """Retrieve evidence for one claim and rule on it, with citations constrained."""
    if claim.get("claim_type") == "opinion":
        return (
            {"verdict": "opinion", "confidence": 0.9, "evidence_quality": "none",
             "rationale": "This is a value judgement, not a factual claim.", "citations": []},
            [],
        )

    passages = await evidence_for_claim(
        session, claim["text"], claim.get("topic", "general")
    )
    if not passages:
        return (
            {"verdict": "unverifiable", "confidence": 0.5, "evidence_quality": "none",
             "rationale": "No relevant evidence was found in any consulted source.",
             "citations": []},
            [],
        )

    user = (
        f"CLAIM: {claim['text']}\n"
        f"TYPE: {claim.get('claim_type')}\n\n"
        f"EVIDENCE (cite only these IDs):\n{render_pack(passages)}"
    )
    result = await ds.json(
        system=prompts.ADJUDICATION_SYSTEM, user=user, stage="adjudicate", max_tokens=1200
    )

    # Enforce the citation rule mechanically. A hallucinated ID is dropped, and a
    # verdict left with no valid citation is downgraded to unverifiable — this is
    # what makes "no unsourced verdicts" a property of the system, not a hope.
    allowed = {p.id for p in passages}
    cited = [c for c in (result.get("citations") or []) if c in allowed]
    dropped = len(result.get("citations") or []) - len(cited)
    if dropped:
        log.warning("dropped_hallucinated_citations", count=dropped, claim=claim["text"][:80])
    result["citations"] = cited

    if not cited and result.get("verdict") not in ("unverifiable", "opinion"):
        log.warning("verdict_without_citation", verdict=result.get("verdict"))
        result.update(
            verdict="unverifiable",
            confidence=min(float(result.get("confidence") or 0.5), 0.4),
            rationale=(
                "The evidence retrieved did not directly address this claim, "
                "so no verdict can be supported."
            ),
            evidence_quality="none",
        )
    return result, passages


async def _stage_adjudicate(
    session: AsyncSession, ds: DeepSeek, report_id: str, claims: list[dict]
) -> list[tuple[dict, dict, list]]:
    await _emit(report_id, "evidence", "start", claims=len(claims))
    sem = asyncio.Semaphore(ADJUDICATION_CONCURRENCY)
    done = 0

    async def worker(claim: dict) -> tuple[dict, dict, list]:
        nonlocal done
        async with sem:
            try:
                verdict, passages = await _adjudicate_one(session, ds, claim)
            except (ProviderError, ValueError) as exc:
                log.warning("adjudication_failed", error=str(exc))
                verdict, passages = (
                    {"verdict": "unverifiable", "confidence": 0.3, "evidence_quality": "none",
                     "rationale": f"Adjudication failed: {exc}", "citations": []},
                    [],
                )
            done += 1
            await _emit(report_id, "evidence", "progress", done=done, total=len(claims))
            return claim, verdict, passages

    results = await asyncio.gather(*(worker(c) for c in claims))
    await _emit(report_id, "adjudicate", "done", count=len(results))
    return list(results)


async def _stage_lean(
    ds: DeepSeek, report_id: str, transcript: dict, video: dict, caption: str | None
) -> dict:
    user = "\n\n".join(
        [
            f"TRANSCRIPT:\n{(transcript.get('text') or '(none)')[:12000]}",
            f"ON-SCREEN TEXT:\n{gemini_video.ocr_timeline(video) or '(none)'}",
            f"CAPTION:\n{caption or '(none)'}",
        ]
    )
    try:
        return await ds.json(
            system=prompts.LEAN_SYSTEM, user=user, stage="lean", max_tokens=1200
        )
    except (ProviderError, ValueError) as exc:
        log.warning("lean_failed", error=str(exc))
        return {"applicable": False, "confidence": 0.0, "rationale": f"Not scored: {exc}"}


async def _stage_summary(
    ds: DeepSeek, adjudicated: list, result: validity.ValidityResult,
    manipulation: tuple[float | None, float],
) -> dict:
    prob, conf = manipulation
    claim_lines = "\n".join(
        f"- [{v.get('verdict')}] {c['text']} ({v.get('rationale', '')})"
        for c, v, _ in adjudicated
    ) or "(no claims found)"
    user = (
        f"CLAIMS AND VERDICTS:\n{claim_lines}\n\n"
        f"VALIDITY: {result.score} (scored {result.claims_scored} of {result.claims_total}; "
        f"{result.claims_unverifiable} unverifiable)\n"
        f"FORENSICS: manipulation probability {prob}, confidence {conf}"
    )
    try:
        return await ds.json(
            system=prompts.SUMMARY_SYSTEM, user=user, stage="summary", max_tokens=800
        )
    except (ProviderError, ValueError):
        return {"summary": "Analysis completed.", "headline": "Analysis complete"}


async def _persist(
    session: AsyncSession,
    report: Report,
    adjudicated: list,
    signals: list,
    ds: DeepSeek,
) -> None:
    for idx, (claim, verdict, passages) in enumerate(adjudicated):
        row = Claim(
            report_id=report.id,
            idx=idx,
            text=claim["text"],
            claim_type=claim.get("claim_type", "factual"),
            checkworthiness=float(claim.get("checkworthiness") or 0.5),
            t_start=claim.get("t_start"),
            t_end=claim.get("t_end"),
            source=claim.get("source", "asr"),
            verbatim=claim.get("verbatim"),
            verdict=verdict.get("verdict"),
            confidence=verdict.get("confidence"),
            rationale=verdict.get("rationale"),
        )
        session.add(row)
        await session.flush()

        cited = set(verdict.get("citations") or [])
        for p in passages:
            session.add(
                Evidence(
                    claim_id=row.id,
                    url=p.url,
                    title=p.title,
                    publisher=p.publisher,
                    publisher_credibility=p.credibility,
                    publisher_lean=p.lean,
                    snippet=(p.text or "")[:1500],
                    tier=p.tier,
                    cited=p.id in cited,
                )
            )

    for s in signals:
        session.add(
            ForensicSignal(
                report_id=report.id,
                signal=s.name,
                raw_score=s.raw_score,
                calibrated_prob=s.calibrated_prob,
                confidence=s.confidence,
                detail=s.detail,
            )
        )

    for call in ds.usage:
        session.add(LLMCall(report_id=report.id, **call))


async def run_pipeline(session: AsyncSession, report: Report, url: str) -> None:
    started = time.perf_counter()
    ds = DeepSeek()
    report.status = "running"
    await session.flush()

    try:
        await _emit(report.id, "ingest", "start")
        media, path = await _stage_ingest(session, report, url)
        await session.commit()
        await _emit(
            report.id, "ingest", "done",
            shortcode=media.shortcode, path=media.ingest_path,
            creator=media.creator.handle if media.creator else None,
        )

        # Transcript, video understanding, and forensics are independent — the
        # wall-clock cost of the whole analysis is roughly the slowest of the three.
        transcript, video, signals = await asyncio.gather(
            _stage_transcribe(report.id, path),
            _stage_video(report.id, path),
            _stage_forensics(report.id, media.storage_key or "", media.shortcode),
        )
        report.transcript = transcript
        report.video_analysis = video

        if not (transcript.get("text") or video.get("on_screen_text")):
            raise PipelineError(
                "Neither transcription nor video analysis produced any content to check"
            )

        claims = await _stage_claims(ds, report.id, transcript, video, media.caption)
        adjudicated = await _stage_adjudicate(session, ds, report.id, claims)

        await _emit(report.id, "score", "start")
        manipulation = forensics_api.aggregate(signals)

        all_urls = [p.url for _, _, ps in adjudicated for p in ps]
        cited_urls = [
            p.url
            for _, v, ps in adjudicated
            for p in ps
            if p.id in set(v.get("citations") or [])
        ]
        avg_cred = (
            sum(cred.lookup(u)[0] for u in cited_urls) / len(cited_urls)
            if cited_urls
            else None
        )

        claim_dicts = [
            {**c, **v, "checkworthiness": c.get("checkworthiness")} for c, v, _ in adjudicated
        ]
        result = validity.compute(
            claim_dicts,
            manipulation_prob=manipulation[0],
            forensics_confidence=manipulation[1],
            source_credibility=avg_cred,
        )

        lean = await _stage_lean(ds, report.id, transcript, video, media.caption)
        mix_lean, mix_conf = cred.source_mix_lean(all_urls)
        summary = await _stage_summary(ds, adjudicated, result, manipulation)

        report.validity_score = result.score
        report.validity_ci_low = result.ci_low
        report.validity_ci_high = result.ci_high
        report.summary = summary.get("summary")
        report.forensics_score = manipulation[0]
        report.forensics_confidence = manipulation[1]
        report.lean_applicable = bool(lean.get("applicable"))
        report.lean_economic = lean.get("economic") if lean.get("applicable") else None
        report.lean_social = lean.get("social") if lean.get("applicable") else None
        report.lean_confidence = lean.get("confidence")
        report.lean_rationale = lean.get("rationale")
        report.subscores = {
            **result.subscores,
            "headline": summary.get("headline"),
            "notes": result.notes,
            "formula": validity.FORMULA,
            "claims_total": result.claims_total,
            "claims_scored": result.claims_scored,
            "claims_unverifiable": result.claims_unverifiable,
            "claims_opinion": result.claims_opinion,
            "manipulation_penalty": result.manipulation_penalty,
            "lean_signals": lean.get("signals"),
            "source_mix_lean": mix_lean,
            "source_mix_confidence": mix_conf,
            "cost_usd": ds.cost_usd(),
            "elapsed_sec": round(time.perf_counter() - started, 1),
        }

        await _persist(session, report, adjudicated, signals, ds)

        if media.creator_id:
            track = await creator_scoring.compute(session, media.creator_id)
            report.subscores["creator"] = {
                "displayable": track.displayable,
                "score": track.score,
                "reels_analysed": track.reels_analysed,
                "note": track.note,
                "history": track.history[:20],
            }
            if media.creator:
                media.creator.reels_analysed = track.reels_analysed + 1
                media.creator.accuracy_score = track.score

        report.status = "done"
        report.stage = "done"
        report.finished_at = datetime.now(UTC)
        await session.commit()
        await _emit(
            report.id, "done", "done",
            validity=result.score, cost=ds.cost_usd(),
            elapsed=report.subscores["elapsed_sec"],
        )

    except (IngestError, PipelineError, ProviderError) as exc:
        await session.rollback()
        report.status = "failed"
        report.error = str(exc)
        await session.merge(report)
        await session.commit()
        await _emit(report.id, "done", "error", message=str(exc))
        log.warning("pipeline_failed", report_id=report.id, error=str(exc))

    except Exception as exc:  # noqa: BLE001 - never leave a report stuck in "running"
        await session.rollback()
        report.status = "failed"
        report.error = f"{type(exc).__name__}: {exc}"
        await session.merge(report)
        await session.commit()
        await _emit(report.id, "done", "error", message=report.error)
        log.exception("pipeline_crashed", report_id=report.id)

    finally:
        if settings.media_retention_days == 0:
            Path(report.media.storage_key).unlink(missing_ok=True) if report.media else None
        log.info("pipeline_usage", report_id=report.id, calls=json.dumps(ds.usage)[:2000])
