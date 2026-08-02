"""Turn a claim into a ranked, credibility-annotated evidence pack.

Fetched pages are cached by URL hash in doc_cache, so the corpus compounds:
later reports on related topics get both faster and better.
"""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass

import httpx
import structlog
import trafilatura
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.evidence import credibility
from app.evidence.sources import Doc, evidence_client, gather
from app.models import DocCache

log = structlog.get_logger(__name__)

MAX_DOCS_PER_CLAIM = 8
SNIPPET_CHARS = 1500


@dataclass(slots=True)
class Passage:
    id: str
    url: str
    title: str | None
    publisher: str | None
    credibility: float
    lean: float | None
    tier: str
    text: str

    def render(self) -> str:
        return (
            f"[{self.id}] {self.title or self.url}\n"
            f"source: {self.publisher} (credibility {self.credibility:.2f}, tier {self.tier})\n"
            f"url: {self.url}\n"
            f"{self.text[:SNIPPET_CHARS]}\n"
        )


def _hash(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()


async def _fetch_text(client: httpx.AsyncClient, url: str) -> str | None:
    try:
        r = await client.get(url, timeout=15)
        if r.status_code >= 400 or "html" not in r.headers.get("content-type", ""):
            return None
        return trafilatura.extract(r.text, include_comments=False, include_tables=True)
    except (httpx.HTTPError, ValueError):
        return None


async def _hydrate(session: AsyncSession, docs: list[Doc]) -> dict[str, str]:
    """Fill in full text for docs whose snippet is too thin to judge stance from."""
    thin = [d for d in docs if not d.snippet or len(d.snippet) < 300]
    if not thin:
        return {}

    hashes = {_hash(d.url): d for d in thin}
    cached = (
        await session.execute(select(DocCache).where(DocCache.url_hash.in_(list(hashes))))
    ).scalars().all()
    out = {c.url: c.text for c in cached if c.text}

    missing = [d for h, d in hashes.items() if d.url not in out]
    if missing:
        async with evidence_client() as client:
            texts = await asyncio.gather(
                *(_fetch_text(client, d.url) for d in missing), return_exceptions=True
            )
        for doc, text in zip(missing, texts, strict=True):
            if isinstance(text, BaseException) or not text:
                continue
            out[doc.url] = text
            await session.execute(
                pg_insert(DocCache)
                .values(
                    url_hash=_hash(doc.url),
                    url=doc.url,
                    title=doc.title,
                    publisher=doc.publisher,
                    text=text[:60000],
                )
                .on_conflict_do_nothing(index_elements=["url_hash"])
            )
    return out


async def evidence_for_claim(
    session: AsyncSession, claim_text: str, topic: str = "general"
) -> list[Passage]:
    docs = await gather(claim_text, topic)
    if not docs:
        return []

    docs = docs[: MAX_DOCS_PER_CLAIM * 2]
    full_text = await _hydrate(session, docs)

    passages: list[Passage] = []
    for i, doc in enumerate(docs):
        body = full_text.get(doc.url) or doc.snippet or ""
        if not body.strip():
            continue
        # Social posts and syndication aggregators are dropped before the model
        # sees them. Leaving them in and hoping the prompt deprioritises them
        # does not work: a live run cited Facebook and Instagram posts as
        # evidence for claims taken from an Instagram reel.
        if not credibility.is_citable(doc.url):
            log.debug("evidence_not_citable", url=doc.url)
            continue
        cred, lean = credibility.lookup(doc.url)
        # Professional fact checks outrank their raw domain rating.
        if doc.tier == "factcheck":
            cred = max(cred, 0.8)
        passages.append(
            Passage(
                id=f"E{i + 1}",
                url=doc.url,
                title=doc.title,
                publisher=doc.publisher,
                credibility=cred,
                lean=lean,
                tier=doc.tier,
                text=body.strip(),
            )
        )

    passages.sort(key=lambda p: ({"factcheck": 0, "structured": 1}.get(p.tier, 2), -p.credibility))
    top = passages[:MAX_DOCS_PER_CLAIM]
    # Re-id after truncation so the LLM's allowed citation set is contiguous.
    for i, p in enumerate(top):
        p.id = f"E{i + 1}"
    return top


def render_pack(passages: list[Passage]) -> str:
    return "\n---\n".join(p.render() for p in passages) or "(no evidence retrieved)"
