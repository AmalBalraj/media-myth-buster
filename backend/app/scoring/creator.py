"""Creator track record.

The strongest and most defensible creator signal is accuracy across reels we have
actually analysed. Below MIN_REELS it is noise and must not be displayed — showing a
"32% accurate" badge off one reel is both statistically meaningless and a defamation
risk (ARCHITECTURE.md §11).
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Media, Report

MIN_REELS = 5
# Prior pulls small samples toward "unknown" rather than toward an extreme.
PRIOR_SCORE = 50.0
PRIOR_WEIGHT = 3.0


@dataclass(slots=True)
class CreatorCredibility:
    displayable: bool
    score: float | None
    reels_analysed: int
    history: list[dict]
    note: str


async def compute(session: AsyncSession, creator_id: str | None) -> CreatorCredibility:
    if not creator_id:
        return CreatorCredibility(False, None, 0, [], "No creator information available.")

    rows = (
        await session.execute(
            select(Report.validity_score, Report.created_at, Media.shortcode)
            .join(Media, Report.media_id == Media.id)
            .where(
                Media.creator_id == creator_id,
                Report.status == "done",
                Report.validity_score.is_not(None),
            )
            .order_by(Report.created_at.desc())
            .limit(50)
        )
    ).all()

    history = [
        {"shortcode": sc, "validity": float(v), "at": ts.isoformat() if ts else None}
        for v, ts, sc in rows
    ]
    n = len(history)

    if n < MIN_REELS:
        return CreatorCredibility(
            False, None, n,
            history,
            f"Only {n} of this creator's reels have been analysed. "
            f"A track record needs at least {MIN_REELS} before it means anything.",
        )

    # Recency-weighted so a creator who improved is not judged forever on old work.
    weighted = sum(h["validity"] * (0.95**i) for i, h in enumerate(history))
    weight = sum(0.95**i for i in range(n))
    smoothed = (weighted + PRIOR_SCORE * PRIOR_WEIGHT) / (weight + PRIOR_WEIGHT)

    return CreatorCredibility(
        True,
        round(smoothed, 1),
        n,
        history,
        f"Based on {n} analysed reels, weighted toward recent posts.",
    )
