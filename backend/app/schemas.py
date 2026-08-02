from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.ingest.base import SHORTCODE_RE


class AnalyseRequest(BaseModel):
    url: str = Field(..., description="Instagram reel/post URL")
    force: bool = Field(False, description="Bypass the report cache and re-run")

    @field_validator("url")
    @classmethod
    def must_be_instagram(cls, v: str) -> str:
        if not SHORTCODE_RE.search(v):
            raise ValueError("Not a recognisable Instagram reel or post URL")
        return v.strip()


class AnalyseResponse(BaseModel):
    report_id: str
    status: str
    cached: bool
    shortcode: str


class EvidenceOut(BaseModel):
    url: str
    title: str | None
    publisher: str | None
    publisher_credibility: float | None
    tier: str | None
    stance: str | None
    cited: bool
    snippet: str | None


class ClaimOut(BaseModel):
    id: str
    idx: int
    text: str
    claim_type: str
    checkworthiness: float
    t_start: float | None
    t_end: float | None
    source: str
    verdict: str | None
    confidence: float | None
    rationale: str | None
    evidence: list[EvidenceOut]


class ForensicOut(BaseModel):
    signal: str
    raw_score: float | None
    calibrated_prob: float | None
    confidence: float | None
    detail: dict[str, Any] | None


class CreatorOut(BaseModel):
    handle: str
    display_name: str | None
    followers: int | None
    is_professional: bool
    verified: bool


class MediaOut(BaseModel):
    shortcode: str
    permalink: str | None
    caption: str | None
    posted_at: datetime | None
    ingest_path: str
    like_count: int | None
    comment_count: int | None
    view_count: int | None
    creator: CreatorOut | None


class ReportOut(BaseModel):
    id: str
    status: str
    stage: str | None
    error: str | None
    created_at: datetime
    validity_score: float | None
    validity_ci_low: float | None
    validity_ci_high: float | None
    summary: str | None
    lean_applicable: bool
    lean_economic: float | None
    lean_social: float | None
    lean_confidence: float | None
    lean_rationale: str | None
    forensics_score: float | None
    forensics_confidence: float | None
    subscores: dict[str, Any] | None
    transcript: dict[str, Any] | None
    video_analysis: dict[str, Any] | None
    media: MediaOut | None
    claims: list[ClaimOut]
    forensics: list[ForensicOut]
