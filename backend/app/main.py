from __future__ import annotations

import logging
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import routes_reports, routes_webhook
from app.config import settings
from app.db import init_db
from app.scoring.validity import FORMULA

structlog.configure(
    processors=[
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(
        getattr(logging, settings.log_level.upper(), logging.INFO)
    ),
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    if settings.app_env == "dev":
        await init_db()
    yield


app = FastAPI(
    title="Media Myth Buster",
    version="0.1.0",
    description="Claim-by-claim credibility analysis of short-form social video.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.web_base_url, "http://localhost:3001", "http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(routes_reports.router, prefix="/api")
app.include_router(routes_webhook.router, prefix="/api")


@app.get("/api/health")
async def health() -> dict:
    return {
        "ok": True,
        "pipeline_version": settings.pipeline_version,
        "providers": {
            "deepseek": bool(settings.deepseek_api_key),
            "groq": bool(settings.groq_api_key),
            "gemini": bool(settings.gemini_api_key),
            "forensics": bool(settings.forensics_url),
            "instagram_graph": bool(settings.ig_access_token),
            "factcheck": bool(settings.google_factcheck_api_key),
        },
    }


@app.get("/api/methodology")
async def methodology() -> dict:
    """Served to the UI so the scoring method is never hidden from the reader."""
    from app.pipeline.prompts import LEAN_SYSTEM
    from app.providers.forensics import SIGNAL_CONFIDENCE
    from app.scoring.creator import MIN_REELS
    from app.scoring.validity import EXCLUDED, VERDICT_VALUE

    return {
        "validity_formula": FORMULA,
        "verdict_values": VERDICT_VALUE,
        "excluded_verdicts": sorted(EXCLUDED),
        "forensic_signal_confidence": SIGNAL_CONFIDENCE,
        "creator_min_reels": MIN_REELS,
        "political_lean_rubric": LEAN_SYSTEM,
        "limitations": [
            "Deepfake detection is unreliable on unseen generators and is degraded by "
            "Instagram's re-encoding. It is one signal, never the verdict.",
            "Political lean is subjective; the rubric is Western-leaning by default.",
            "Non-English and code-mixed audio has lower transcription accuracy and much "
            "thinner evidence coverage.",
            "Claims under 24-48 hours old are often genuinely unverifiable.",
            "Creator credibility is noise below 5 analysed reels and is hidden until then.",
            "Personal (non-Professional) Instagram accounts are not reachable via any "
            "sanctioned API.",
        ],
    }
