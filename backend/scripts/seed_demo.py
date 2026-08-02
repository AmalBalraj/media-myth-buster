"""Insert a realistic finished report so the UI can be developed and reviewed
without spending API quota or waiting on a live pipeline run.

    python -m scripts.seed_demo

Deliberately includes the awkward cases the report must handle well: an
unverifiable claim, an opinion, a claim whose evidence was retrieved but not
cited, and forensic signals whose confidence is too low to move the score.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from app.config import settings
from app.db import init_db, session_scope
from app.evidence import credibility as cred
from app.models import Claim, Creator, Evidence, ForensicSignal, Media, Report
from app.providers.forensics import Signal, aggregate, calibrate
from app.scoring import validity

CLAIMS = [
    dict(
        text="India's renewable energy capacity crossed 200 GW in 2025.",
        claim_type="statistical", topic="statistics", checkworthiness=0.85,
        t_start=3.2, t_end=8.9, source="asr",
        verdict="mostly_true", confidence=0.78, evidence_quality="strong",
        rationale="Official capacity figures support the milestone, though the "
                  "reported date is several months later than stated.",
        evidence=[
            ("https://data.worldbank.org/indicator/EG.ELC.RNEW.ZS",
             "Renewable electricity output", "World Bank", True),
            ("https://ourworldindata.org/renewable-energy",
             "Renewable Energy", "Our World in Data", True),
            ("https://en.wikipedia.org/wiki/Renewable_energy_in_India",
             "Renewable energy in India", "Wikipedia", False),
        ],
    ),
    dict(
        text="Solar panel manufacturing produces more carbon than the panels ever offset.",
        claim_type="causal", topic="science", checkworthiness=0.95,
        t_start=11.4, t_end=18.0, source="asr",
        verdict="false", confidence=0.91, evidence_quality="strong",
        rationale="Life-cycle analyses put energy payback at one to three years "
                  "against a 25-30 year service life.",
        evidence=[
            ("https://doi.org/10.1038/s41467-021-26212-z",
             "Life-cycle assessment of photovoltaic systems", "Nature Communications", True),
            ("https://www.nature.com/articles/s41560-021-00815-8",
             "Carbon payback of solar PV", "Nature Energy", True),
        ],
    ),
    dict(
        text="The government approved 14 new solar parks last Tuesday.",
        claim_type="factual", topic="politics", checkworthiness=0.7,
        t_start=21.0, t_end=25.5, source="ocr",
        verdict="unverifiable", confidence=0.35, evidence_quality="none",
        rationale="No source covering an approval on that date was found; the claim "
                  "may simply be too recent to have been reported.",
        evidence=[],
    ),
    dict(
        text="Rooftop solar is the single best investment a household can make.",
        claim_type="opinion", topic="general", checkworthiness=0.15,
        t_start=29.8, t_end=34.0, source="asr",
        verdict="opinion", confidence=0.9, evidence_quality="none",
        rationale="This is a value judgement, not a factual claim.",
        evidence=[],
    ),
    dict(
        text="Coal still supplies roughly 70% of India's electricity generation.",
        claim_type="statistical", topic="economics", checkworthiness=0.8,
        t_start=38.1, t_end=44.6, source="asr",
        verdict="true", confidence=0.88, evidence_quality="strong",
        rationale="Generation-mix data from multiple independent sources agrees on "
                  "a share close to 70%.",
        evidence=[
            ("https://ourworldindata.org/electricity-mix", "Electricity Mix",
             "Our World in Data", True),
            ("https://www.iea.org/countries/india", "India energy profile", "IEA", True),
        ],
    ),
    dict(
        text="Wind turbines kill more birds than fossil fuel infrastructure.",
        claim_type="causal", topic="science", checkworthiness=0.75,
        t_start=48.0, t_end=54.2, source="ocr",
        verdict="mostly_false", confidence=0.72, evidence_quality="moderate",
        rationale="Per unit of energy, fossil fuel infrastructure is associated with "
                  "substantially higher avian mortality in the studies retrieved.",
        evidence=[
            ("https://doi.org/10.1016/j.renene.2012.12.074",
             "Avian mortality and energy production", "Renewable Energy", True),
            ("https://en.wikipedia.org/wiki/Wind_turbine_bird_mortality",
             "Wind turbine bird mortality", "Wikipedia", False),
        ],
    ),
]

RAW_SIGNALS = {
    "ai_generated_frames": 0.22,
    "face_manipulation": 0.11,
    "splice_recompression": 0.61,
    "container_forensics": 0.40,
    "audio_spoof": 0.08,
}


async def main() -> None:
    await init_db()
    async with session_scope() as session:
        creator = Creator(
            platform="instagram", handle="demo.energy.explains",
            display_name="Energy Explained", followers=184_000, media_count=612,
            is_professional=True, verified=False,
            biography="Making the energy transition make sense.",
        )
        session.add(creator)
        await session.flush()

        media = Media(
            platform="instagram", shortcode="DEMOreel001",
            permalink="https://www.instagram.com/reel/DEMOreel001/",
            ingest_path="discovery", media_type="VIDEO", duration=58.0,
            posted_at=datetime.now(UTC) - timedelta(days=2),
            caption="The truth about India's solar boom 🔆 #energy #solar",
            like_count=24_300, comment_count=1_180, view_count=612_000,
            creator_id=creator.id, storage_key="/data/media/demo.mp4",
        )
        session.add(media)
        await session.flush()

        signals = [
            Signal(name, raw, calibrate(name, raw),
                   {"ai_generated_frames": 0.35, "face_manipulation": 0.40,
                    "splice_recompression": 0.30, "container_forensics": 0.55,
                    "audio_spoof": 0.45}[name], {})
            for name, raw in RAW_SIGNALS.items()
        ]
        manip_prob, manip_conf = aggregate(signals)

        cited_urls = [e[0] for c in CLAIMS for e in c["evidence"] if e[3]]
        avg_cred = sum(cred.lookup(u)[0] for u in cited_urls) / len(cited_urls)
        result = validity.compute(CLAIMS, manip_prob, manip_conf, avg_cred)

        report = Report(
            media_id=media.id, pipeline_version=settings.pipeline_version,
            status="done", stage="done", finished_at=datetime.now(UTC),
            validity_score=result.score,
            validity_ci_low=result.ci_low, validity_ci_high=result.ci_high,
            summary=(
                "The video's headline figure on renewable capacity is broadly right, "
                "but its central argument — that solar manufacturing never repays its "
                "carbon cost — is contradicted by life-cycle studies. One claim about "
                "a recent government approval could not be verified from any source."
            ),
            lean_applicable=True, lean_economic=-0.32, lean_social=-0.18,
            lean_confidence=0.55,
            lean_rationale=(
                "Framing favours public investment and treats industry objections "
                "sceptically, but presents cost figures even-handedly."
            ),
            forensics_score=manip_prob, forensics_confidence=manip_conf,
            transcript={
                "text": "Let's talk about what's actually happening with solar in India...",
                "language": "en", "duration": 58.0,
                "segments": [
                    {"start": 0.0, "end": 8.9, "text": "Let's talk about solar in India."},
                    {"start": 38.1, "end": 58.0, "text": "Coal still dominates the grid."},
                ],
            },
            video_analysis={
                "visual_summary": "Presenter to camera with overlaid statistics and stock "
                                  "footage of solar installations.",
                "on_screen_text": [
                    {"t_start": 21.0, "t_end": 25.5, "text": "14 NEW SOLAR PARKS APPROVED",
                     "kind": "caption"},
                    {"t_start": 48.0, "t_end": 54.2, "text": "WIND KILLS MORE BIRDS",
                     "kind": "chyron"},
                ],
                "audio_visual_consistency": {"score": 0.8, "notes": "Footage matches narration."},
            },
            subscores={
                **result.subscores,
                "headline": "Right on capacity, wrong on carbon payback",
                "notes": result.notes,
                "formula": validity.FORMULA,
                "claims_total": result.claims_total,
                "claims_scored": result.claims_scored,
                "claims_unverifiable": result.claims_unverifiable,
                "claims_opinion": result.claims_opinion,
                "manipulation_penalty": result.manipulation_penalty,
                "source_mix_lean": cred.source_mix_lean(cited_urls)[0],
                "source_mix_confidence": cred.source_mix_lean(cited_urls)[1],
                "cost_usd": 0.0113,
                "elapsed_sec": 47.3,
                "creator": {
                    "displayable": True, "score": 71.4, "reels_analysed": 9,
                    "note": "Based on 9 analysed reels, weighted toward recent posts.",
                    "history": [{"validity": v} for v in
                                [78, 64, 81, 55, 72, 69, 88, 61, 74]],
                },
            },
        )
        session.add(report)
        await session.flush()

        for idx, c in enumerate(CLAIMS):
            claim = Claim(
                report_id=report.id, idx=idx, text=c["text"],
                claim_type=c["claim_type"], checkworthiness=c["checkworthiness"],
                t_start=c["t_start"], t_end=c["t_end"], source=c["source"],
                verdict=c["verdict"], confidence=c["confidence"],
                rationale=c["rationale"],
            )
            session.add(claim)
            await session.flush()
            for url, title, publisher, is_cited in c["evidence"]:
                credibility, lean = cred.lookup(url)
                session.add(Evidence(
                    claim_id=claim.id, url=url, title=title, publisher=publisher,
                    publisher_credibility=credibility, publisher_lean=lean,
                    tier="structured", cited=is_cited,
                    stance="supports" if is_cited else "neutral",
                    snippet="Retrieved passage text used during adjudication.",
                ))

        for s in signals:
            session.add(ForensicSignal(
                report_id=report.id, signal=s.name, raw_score=s.raw_score,
                calibrated_prob=s.calibrated_prob, confidence=s.confidence, detail={},
            ))

        print(f"seeded report {report.id}")
        print(f"  validity  {result.score} [{result.ci_low}-{result.ci_high}]")
        print(f"  claims    {result.claims_scored} scored / {result.claims_total} total")
        print(f"  forensics prob={manip_prob} conf={manip_conf}")
        print(f"\n  {settings.web_base_url.rstrip('/')}/report/{report.id}")


if __name__ == "__main__":
    asyncio.run(main())
