"""Publisher credibility and political lean.

MBFC / AllSides ratings need a licence for public redistribution (ARCHITECTURE.md
§11), so ship a small hand-curated table and load a licensed dataset later via
`load_ratings()`. Unknown publishers get a neutral prior rather than a guess —
never penalise a source purely for being absent from the table.

lean: -1.0 (left) .. 0 (centre) .. +1.0 (right)
credibility: 0.0 .. 1.0
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from urllib.parse import urlparse

import structlog

log = structlog.get_logger(__name__)

NEUTRAL_PRIOR = 0.5

# Domain suffix -> (credibility, lean|None). Deliberately small and auditable.
_SEED: dict[str, tuple[float, float | None]] = {
    # Primary / structured — high credibility, no meaningful lean
    "nih.gov": (0.95, None), "who.int": (0.92, None), "cdc.gov": (0.90, None),
    "nature.com": (0.95, None), "science.org": (0.95, None), "thelancet.com": (0.94, None),
    "nejm.org": (0.95, None), "bmj.com": (0.93, None), "pubmed.ncbi.nlm.nih.gov": (0.93, None),
    "doi.org": (0.85, None), "arxiv.org": (0.70, None), "openalex.org": (0.80, None),
    "worldbank.org": (0.92, None), "imf.org": (0.90, None), "ourworldindata.org": (0.90, None),
    "data.gov": (0.88, None), "europa.eu": (0.88, None), "un.org": (0.85, None),
    "wikipedia.org": (0.75, None), "wikidata.org": (0.78, None),
    # Dedicated fact-checkers
    "snopes.com": (0.85, -0.1), "politifact.com": (0.85, -0.15),
    "factcheck.org": (0.88, 0.0), "fullfact.org": (0.88, 0.0),
    "altnews.in": (0.82, -0.2), "boomlive.in": (0.82, -0.1),
    "reuters.com": (0.90, 0.0), "apnews.com": (0.90, -0.05),
    # News, with lean
    "bbc.co.uk": (0.85, -0.1), "bbc.com": (0.85, -0.1),
    "npr.org": (0.82, -0.3), "theguardian.com": (0.78, -0.4),
    "nytimes.com": (0.82, -0.3), "washingtonpost.com": (0.80, -0.3),
    "wsj.com": (0.82, 0.25), "economist.com": (0.84, 0.1),
    "ft.com": (0.86, 0.05), "bloomberg.com": (0.84, 0.0),
    "foxnews.com": (0.62, 0.6), "breitbart.com": (0.35, 0.85),
    "msnbc.com": (0.62, -0.6), "dailywire.com": (0.45, 0.75),
    "thehindu.com": (0.82, -0.2), "indianexpress.com": (0.80, -0.1),
    "timesofindia.indiatimes.com": (0.68, 0.15), "ndtv.com": (0.75, -0.15),
    "republicworld.com": (0.42, 0.7), "opindia.com": (0.30, 0.85),
    # Low-credibility aggregators / UGC
    "medium.com": (0.35, None), "substack.com": (0.35, None),
    "youtube.com": (0.25, None), "reddit.com": (0.25, None),
    "x.com": (0.20, None), "twitter.com": (0.20, None), "facebook.com": (0.20, None),
    "instagram.com": (0.20, None), "tiktok.com": (0.20, None),
    "quora.com": (0.20, None), "pinterest.com": (0.15, None),
    # Syndication aggregators: they republish other outlets' work, so the URL
    # says nothing about who actually reported it.
    "msn.com": (0.40, None), "news.yahoo.com": (0.45, None),
    "news.google.com": (0.40, None), "flipboard.com": (0.35, None),
}

# Never usable as a citation, whatever the retriever turns up.
#
# A fact-check that rests on an Instagram post is circular: the claim under
# review came from exactly that kind of source. These domains can still inform
# retrieval, but if a claim is supported by nothing else it is unverifiable —
# which is the honest answer.
NOT_CITABLE = frozenset({
    "facebook.com", "instagram.com", "x.com", "twitter.com", "tiktok.com",
    "reddit.com", "quora.com", "pinterest.com", "threads.net", "t.me",
    "youtube.com", "youtu.be",
})

# Below this, a source may appear in the report as context but cannot carry a
# verdict on its own.
CITABLE_MIN_CREDIBILITY = 0.45


def is_citable(url: str) -> bool:
    host = _domain(url)
    if any(host == d or host.endswith("." + d) for d in NOT_CITABLE):
        return False
    return lookup(url)[0] >= CITABLE_MIN_CREDIBILITY

_ratings: dict[str, tuple[float, float | None]] = dict(_SEED)


def load_ratings(path: Path) -> int:
    """Merge a licensed MBFC/AllSides export (CSV: domain,credibility,lean — or JSON)."""
    if not path.exists():
        return 0
    added = 0
    if path.suffix == ".json":
        for domain, vals in json.loads(path.read_text()).items():
            _ratings[domain.lower()] = (float(vals["credibility"]), vals.get("lean"))
            added += 1
    else:
        with path.open() as fh:
            for row in csv.DictReader(fh):
                lean = row.get("lean")
                _ratings[row["domain"].lower()] = (
                    float(row["credibility"]),
                    float(lean) if lean not in (None, "") else None,
                )
                added += 1
    log.info("ratings_loaded", count=added, path=str(path))
    return added


def _domain(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def lookup(url: str) -> tuple[float, float | None]:
    """(credibility, lean). Matches the longest registered suffix."""
    host = _domain(url)
    if not host:
        return NEUTRAL_PRIOR, None
    best: tuple[float, float | None] | None = None
    best_len = -1
    for domain, vals in _ratings.items():
        if (host == domain or host.endswith("." + domain)) and len(domain) > best_len:
            best, best_len = vals, len(domain)
    return best if best else (NEUTRAL_PRIOR, None)


def source_mix_lean(urls: list[str]) -> tuple[float | None, float]:
    """Credibility-weighted lean of the sources backing a reel.

    Returns (lean, confidence). Confidence reflects how many rated sources there
    were — two rated links is not a reading.
    """
    weighted, weight, rated = 0.0, 0.0, 0
    for url in urls:
        cred, lean = lookup(url)
        if lean is None:
            continue
        weighted += lean * cred
        weight += cred
        rated += 1
    if not weight:
        return None, 0.0
    return round(weighted / weight, 3), round(min(rated / 6.0, 1.0), 2)
