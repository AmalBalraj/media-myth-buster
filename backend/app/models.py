from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

EMBED_DIM = 1024


def _uuid() -> str:
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSON}


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Creator(Base, TimestampMixin):
    __tablename__ = "creators"
    __table_args__ = (UniqueConstraint("platform", "handle"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    platform: Mapped[str] = mapped_column(String(32), default="instagram")
    handle: Mapped[str] = mapped_column(String(128), index=True)
    display_name: Mapped[str | None] = mapped_column(String(256))
    ig_user_id: Mapped[str | None] = mapped_column(String(64))
    followers: Mapped[int | None] = mapped_column(Integer)
    media_count: Mapped[int | None] = mapped_column(Integer)
    is_professional: Mapped[bool] = mapped_column(Boolean, default=False)
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    biography: Mapped[str | None] = mapped_column(Text)

    # Rolling track record. Meaningless below MIN_REELS_FOR_CREDIBILITY (see scoring/creator.py).
    accuracy_score: Mapped[float | None] = mapped_column(Float)
    reels_analysed: Mapped[int] = mapped_column(Integer, default=0)

    media: Mapped[list[Media]] = relationship(back_populates="creator")


class Media(Base, TimestampMixin):
    __tablename__ = "media"
    __table_args__ = (UniqueConstraint("platform", "shortcode"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    platform: Mapped[str] = mapped_column(String(32), default="instagram")
    shortcode: Mapped[str] = mapped_column(String(64), index=True)
    permalink: Mapped[str | None] = mapped_column(Text)
    url_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    # Which ingest rung fired: mention | discovery | oembed | ytdlp
    ingest_path: Mapped[str] = mapped_column(String(16))
    media_type: Mapped[str | None] = mapped_column(String(32))
    duration: Mapped[float | None] = mapped_column(Float)
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    caption: Mapped[str | None] = mapped_column(Text)
    storage_key: Mapped[str | None] = mapped_column(Text)
    like_count: Mapped[int | None] = mapped_column(Integer)
    comment_count: Mapped[int | None] = mapped_column(Integer)
    view_count: Mapped[int | None] = mapped_column(Integer)
    phashes: Mapped[list[str] | None] = mapped_column(ARRAY(String(64)))

    creator_id: Mapped[str | None] = mapped_column(ForeignKey("creators.id"))
    # raise_on_sql, not the default lazy load: under asyncio an implicit lazy
    # load raises MissingGreenlet at runtime and only in production. This turns
    # the same mistake into a loud error anywhere, including sync tests.
    # Callers must eager-load it (selectinload) or use creator_id.
    creator: Mapped[Creator | None] = relationship(
        back_populates="media", lazy="raise_on_sql"
    )


class Report(Base, TimestampMixin):
    __tablename__ = "reports"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    media_id: Mapped[str] = mapped_column(ForeignKey("media.id"), index=True)
    pipeline_version: Mapped[str] = mapped_column(String(16))
    # queued | running | done | failed
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    stage: Mapped[str | None] = mapped_column(String(32))
    error: Mapped[str | None] = mapped_column(Text)

    validity_score: Mapped[float | None] = mapped_column(Float)
    validity_ci_low: Mapped[float | None] = mapped_column(Float)
    validity_ci_high: Mapped[float | None] = mapped_column(Float)
    summary: Mapped[str | None] = mapped_column(Text)

    lean_economic: Mapped[float | None] = mapped_column(Float)
    lean_social: Mapped[float | None] = mapped_column(Float)
    lean_confidence: Mapped[float | None] = mapped_column(Float)
    lean_applicable: Mapped[bool] = mapped_column(Boolean, default=True)
    lean_rationale: Mapped[str | None] = mapped_column(Text)

    forensics_score: Mapped[float | None] = mapped_column(Float)
    forensics_confidence: Mapped[float | None] = mapped_column(Float)

    subscores: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    transcript: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    video_analysis: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    media: Mapped[Media] = relationship(lazy="raise_on_sql")
    claims: Mapped[list[Claim]] = relationship(
        back_populates="report", cascade="all, delete-orphan"
    )
    forensics: Mapped[list[ForensicSignal]] = relationship(cascade="all, delete-orphan")


class Claim(Base):
    __tablename__ = "claims"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    report_id: Mapped[str] = mapped_column(ForeignKey("reports.id"), index=True)
    idx: Mapped[int] = mapped_column(Integer, default=0)
    text: Mapped[str] = mapped_column(Text)
    claim_type: Mapped[str] = mapped_column(String(32))
    checkworthiness: Mapped[float] = mapped_column(Float, default=0.5)
    t_start: Mapped[float | None] = mapped_column(Float)
    t_end: Mapped[float | None] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(16), default="asr")  # asr|ocr|caption
    verbatim: Mapped[str | None] = mapped_column(Text)

    verdict: Mapped[str | None] = mapped_column(String(24), index=True)
    confidence: Mapped[float | None] = mapped_column(Float)
    rationale: Mapped[str | None] = mapped_column(Text)

    report: Mapped[Report] = relationship(back_populates="claims")
    evidence: Mapped[list[Evidence]] = relationship(
        back_populates="claim", cascade="all, delete-orphan"
    )


class Evidence(Base):
    __tablename__ = "evidence"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    claim_id: Mapped[str] = mapped_column(ForeignKey("claims.id"), index=True)
    url: Mapped[str] = mapped_column(Text)
    title: Mapped[str | None] = mapped_column(Text)
    publisher: Mapped[str | None] = mapped_column(String(256))
    publisher_credibility: Mapped[float | None] = mapped_column(Float)
    publisher_lean: Mapped[float | None] = mapped_column(Float)
    snippet: Mapped[str | None] = mapped_column(Text)
    stance: Mapped[str | None] = mapped_column(String(16))  # supports|refutes|neutral
    tier: Mapped[str | None] = mapped_column(String(16))  # factcheck|structured|web
    cited: Mapped[bool] = mapped_column(Boolean, default=False)
    retrieved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBED_DIM))

    claim: Mapped[Claim] = relationship(back_populates="evidence")


class ForensicSignal(Base):
    __tablename__ = "forensics"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    report_id: Mapped[str] = mapped_column(ForeignKey("reports.id"), index=True)
    signal: Mapped[str] = mapped_column(String(64))
    raw_score: Mapped[float | None] = mapped_column(Float)
    calibrated_prob: Mapped[float | None] = mapped_column(Float)
    confidence: Mapped[float | None] = mapped_column(Float)
    detail: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class LLMCall(Base, TimestampMixin):
    __tablename__ = "llm_calls"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    report_id: Mapped[str | None] = mapped_column(String(36), index=True)
    stage: Mapped[str] = mapped_column(String(32))
    provider: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(64))
    prompt_hash: Mapped[str] = mapped_column(String(64), index=True)
    tokens_in: Mapped[int | None] = mapped_column(Integer)
    tokens_out: Mapped[int | None] = mapped_column(Integer)
    cached: Mapped[bool] = mapped_column(Boolean, default=False)
    latency_ms: Mapped[int | None] = mapped_column(Integer)


class Mention(Base, TimestampMixin):
    """An @mention of the bot on someone else's reel — the primary sanctioned ingest path."""

    __tablename__ = "mentions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    ig_comment_id: Mapped[str | None] = mapped_column(String(64), unique=True)
    ig_media_id: Mapped[str | None] = mapped_column(String(64), index=True)
    requester: Mapped[str | None] = mapped_column(String(128))
    report_id: Mapped[str | None] = mapped_column(String(36))
    replied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DocCache(Base):
    """URL-keyed cache of fetched evidence pages. The corpus compounds across reports."""

    __tablename__ = "doc_cache"

    url_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    url: Mapped[str] = mapped_column(Text)
    title: Mapped[str | None] = mapped_column(Text)
    publisher: Mapped[str | None] = mapped_column(String(256))
    text: Mapped[str | None] = mapped_column(Text)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
