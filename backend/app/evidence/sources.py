"""Evidence sources, tiered most-authoritative-first (ARCHITECTURE.md §6). All free."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

import httpx
import structlog

from app.config import settings

log = structlog.get_logger(__name__)

# Wikimedia enforces a robot policy: an anonymous or default user-agent gets a
# blanket 403. Crossref and OpenAlex likewise prefer a contact address and give
# unidentified callers the slow pool. One header, applied to every source.
CONTACT = "myth-buster@devmindset.in"
USER_AGENT = f"MediaMythBuster/0.1 (+https://myth-buster.devmindset.in; {CONTACT})"
HEADERS = {"User-Agent": USER_AGENT, "Accept": "application/json"}


def evidence_client(**kwargs: Any) -> httpx.AsyncClient:
    """Every outbound evidence request must identify itself."""
    kwargs.setdefault("follow_redirects", True)
    return httpx.AsyncClient(headers=HEADERS, **kwargs)


@dataclass(slots=True)
class Doc:
    url: str
    title: str | None = None
    snippet: str | None = None
    publisher: str | None = None
    tier: str = "web"  # factcheck | structured | web
    extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.publisher:
            self.publisher = urlparse(self.url).hostname or None


async def _get_json(client: httpx.AsyncClient, url: str, params: dict, timeout: int = 15) -> Any:
    r = await client.get(url, params=params, timeout=timeout)
    r.raise_for_status()
    return r.json()


# ── Tier 1: existing professional fact checks ────────────────────────────────
async def google_factcheck(client: httpx.AsyncClient, query: str) -> list[Doc]:
    """The global ClaimReview corpus. If a fact-checker already ruled, this is the
    strongest and cheapest evidence available."""
    if not settings.google_factcheck_api_key:
        return []
    try:
        data = await _get_json(
            client,
            "https://factchecktools.googleapis.com/v1alpha1/claims:search",
            {"query": query[:400], "languageCode": "en", "pageSize": 8,
             "key": settings.google_factcheck_api_key},
        )
    except httpx.HTTPError as exc:
        log.warning("factcheck_failed", error=str(exc))
        return []

    docs: list[Doc] = []
    for claim in data.get("claims", []):
        for review in claim.get("claimReview", []):
            docs.append(
                Doc(
                    url=review.get("url", ""),
                    title=review.get("title") or claim.get("text"),
                    snippet=(
                        f"Claim: {claim.get('text', '')}\n"
                        f"Rating by {review.get('publisher', {}).get('name', 'unknown')}: "
                        f"{review.get('textualRating', 'n/a')}"
                    ),
                    publisher=review.get("publisher", {}).get("name"),
                    tier="factcheck",
                    extra={"rating": review.get("textualRating"),
                           "claimant": claim.get("claimant")},
                )
            )
    return [d for d in docs if d.url]


# ── Tier 2: structured / authoritative ───────────────────────────────────────
async def wikipedia(
    client: httpx.AsyncClient, query: str, lang: str = "en"
) -> list[Doc]:
    """The language edition matters: regional topics are often far better covered
    in the local-language Wikipedia than in English, and sometimes only there."""
    host = f"{lang}.wikipedia.org"
    try:
        data = await _get_json(
            client,
            f"https://{host}/w/api.php",
            {"action": "query", "list": "search", "srsearch": query[:300],
             "format": "json", "srlimit": 4},
        )
    except httpx.HTTPError:
        return []
    return [
        Doc(
            url=f"https://{host}/wiki/{r['title'].replace(' ', '_')}",
            title=r["title"],
            snippet=r.get("snippet", "")
            .replace('<span class="searchmatch">', "")
            .replace("</span>", ""),
            publisher="Wikipedia" if lang == "en" else f"Wikipedia ({lang})",
            tier="structured",
        )
        for r in data.get("query", {}).get("search", [])
    ]


async def europepmc(client: httpx.AsyncClient, query: str) -> list[Doc]:
    try:
        data = await _get_json(
            client,
            "https://www.ebi.ac.uk/europepmc/webservices/rest/search",
            {"query": query[:300], "format": "json", "pageSize": 5, "resultType": "core"},
        )
    except httpx.HTTPError:
        return []
    docs = []
    for r in data.get("resultList", {}).get("result", []):
        doi, pmid = r.get("doi"), r.get("pmid")
        url = f"https://doi.org/{doi}" if doi else (
            f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid else None
        )
        if not url:
            continue
        docs.append(
            Doc(
                url=url,
                title=r.get("title"),
                snippet=(r.get("abstractText") or "")[:1200],
                publisher=r.get("journalInfo", {}).get("journal", {}).get("title") or "Europe PMC",
                tier="structured",
                extra={"year": r.get("pubYear"), "citations": r.get("citedByCount")},
            )
        )
    return docs


async def openalex(client: httpx.AsyncClient, query: str) -> list[Doc]:
    try:
        data = await _get_json(
            client,
            "https://api.openalex.org/works",
            {"search": query[:250], "per-page": 5, "mailto": CONTACT},
        )
    except httpx.HTTPError:
        return []
    docs = []
    for w in data.get("results", []):
        url = w.get("doi") or w.get("id")
        abstract_idx = w.get("abstract_inverted_index") or {}
        abstract = " ".join(abstract_idx.keys())[:800] if abstract_idx else ""
        docs.append(
            Doc(
                url=url,
                title=w.get("title"),
                snippet=abstract,
                # `.get("source", {})` is not enough: OpenAlex sets the key with a
            # null value on works with no journal, so the default never applies.
            publisher=((w.get("primary_location") or {}).get("source") or {}).get(
                "display_name"
            ),
                tier="structured",
                extra={"year": w.get("publication_year"), "citations": w.get("cited_by_count")},
            )
        )
    return [d for d in docs if d.url]


# The World Development Indicators catalogue (~1500 entries) has no server-side
# search, so it is fetched once per process and matched locally. Previously this
# pulled 5 arbitrary indicators per call and matched against them, which could
# never hit.
_WDI_CACHE: list[dict] | None = None
_WDI_LOCK = asyncio.Lock()

STOPWORDS = {
    "about", "above", "after", "approximately", "around", "because", "before",
    "being", "between", "could", "during", "every", "first", "their", "there",
    "these", "those", "under", "which", "while", "would", "percent", "supplies",
}


async def _wdi_catalogue(client: httpx.AsyncClient) -> list[dict]:
    global _WDI_CACHE
    async with _WDI_LOCK:
        if _WDI_CACHE is None:
            try:
                data = await _get_json(
                    client,
                    "https://api.worldbank.org/v2/indicator",
                    {"format": "json", "per_page": "2000", "source": "2"},
                    timeout=30,
                )
                _WDI_CACHE = data[1] if isinstance(data, list) and len(data) > 1 else []
            except (httpx.HTTPError, ValueError, KeyError):
                _WDI_CACHE = []
    return _WDI_CACHE


async def worldbank(client: httpx.AsyncClient, query: str) -> list[Doc]:
    """Match a statistical claim to World Development Indicators."""
    catalogue = await _wdi_catalogue(client)
    if not catalogue:
        return []

    terms = {
        t.strip(".,%'\"").lower()
        for t in query.split()
        if len(t) > 4 and t.strip(".,%'\"").lower() not in STOPWORDS
    }
    if not terms:
        return []

    scored: list[tuple[int, dict]] = []
    for ind in catalogue:
        words = {w.strip("(),").lower() for w in (ind.get("name") or "").split()}
        overlap = len(terms & words)
        # Two matching terms, so "electricity" alone cannot drag in every
        # power-related indicator in the catalogue.
        if overlap >= 2:
            scored.append((overlap, ind))

    scored.sort(key=lambda pair: -pair[0])
    return [
        Doc(
            url=f"https://data.worldbank.org/indicator/{ind.get('id')}",
            title=ind.get("name"),
            snippet=(ind.get("sourceNote") or "")[:600],
            publisher="World Bank",
            tier="structured",
        )
        for _, ind in scored[:3]
    ]


# ── Tier 3: open web ─────────────────────────────────────────────────────────
async def searxng(
    client: httpx.AsyncClient, query: str, limit: int = 6, lang: str = "en"
) -> list[Doc]:
    """Self-hosted metasearch: unlimited and $0, which is what makes broad retrieval viable."""
    try:
        data = await _get_json(
            client,
            f"{settings.searxng_url.rstrip('/')}/search",
            {"q": query[:300], "format": "json", "language": lang,
             "categories": "general,news"},
            timeout=20,
        )
    except httpx.HTTPError as exc:
        log.warning("searxng_failed", error=str(exc))
        return []
    return [
        Doc(url=r["url"], title=r.get("title"), snippet=r.get("content"), tier="web")
        for r in data.get("results", [])[:limit]
        if r.get("url")
    ]


async def tavily(client: httpx.AsyncClient, query: str, limit: int = 5) -> list[Doc]:
    """Quality fallback when SearXNG is down or thin. 1,000 free searches/month."""
    if not settings.tavily_api_key:
        return []
    try:
        r = await client.post(
            "https://api.tavily.com/search",
            json={"api_key": settings.tavily_api_key, "query": query[:380],
                  "max_results": limit, "search_depth": "basic"},
            timeout=25,
        )
        r.raise_for_status()
        data = r.json()
    except httpx.HTTPError:
        return []
    return [
        Doc(url=r["url"], title=r.get("title"), snippet=r.get("content"), tier="web")
        for r in data.get("results", [])
        if r.get("url")
    ]


ROUTES: dict[str, list] = {
    "health": [europepmc, openalex, wikipedia],
    "science": [europepmc, openalex, wikipedia],
    "statistics": [worldbank, openalex, wikipedia],
    "economics": [worldbank, openalex, wikipedia],
    "history": [wikipedia, openalex],
    "politics": [wikipedia],
    "general": [wikipedia],
}


async def gather(
    query: str,
    topic: str = "general",
    *,
    native_query: str | None = None,
    lang: str = "en",
) -> list[Doc]:
    """Fan out across tier 1-3 for one claim, tolerating individual source failures.

    For a non-English source, the English query runs as usual *and* the original
    wording is searched in its own language — a district-level story in Kerala or
    Bihar is often covered only in the regional press, so an English-only search
    returns nothing and the claim reports as unverifiable when it is simply
    unsearched.
    """
    async with evidence_client() as client:
        tasks = [google_factcheck(client, query), searxng(client, query)]
        tasks += [fn(client, query) for fn in ROUTES.get(topic, ROUTES["general"])]

        if lang and lang != "en" and native_query:
            tasks += [
                searxng(client, native_query, lang=lang),
                wikipedia(client, native_query, lang=lang),
            ]

        results = await asyncio.gather(*tasks, return_exceptions=True)
        docs: list[Doc] = []
        for res in results:
            if isinstance(res, BaseException):
                log.warning("evidence_source_failed", error=str(res))
                continue
            docs.extend(res)

        if not any(d.tier == "web" for d in docs):
            # Outside the gather() above, so it needs its own guard — otherwise a
            # fallback failure takes down retrieval for the whole claim, and the
            # claim silently reports as unverifiable.
            try:
                docs.extend(await tavily(client, query))
            except Exception as exc:  # noqa: BLE001
                log.warning("evidence_source_failed", source="tavily", error=str(exc))

    seen: set[str] = set()
    unique = []
    for d in docs:
        key = d.url.split("?")[0].rstrip("/")
        if key not in seen:
            seen.add(key)
            unique.append(d)
    # Fact checks first, then structured sources, then open web.
    order = {"factcheck": 0, "structured": 1, "web": 2}
    return sorted(unique, key=lambda d: order.get(d.tier, 3))
