"""Regressions for bugs a live run surfaced. Each of these silently produced
zero evidence, which the pipeline then reports as `unverifiable` — a wrong
answer that looks exactly like a correct one."""

import httpx
import pytest

from app.evidence import sources


def test_outbound_requests_identify_themselves():
    """Wikimedia 403s anonymous callers outright; OpenAlex deprioritises them."""
    client = sources.evidence_client()
    ua = client.headers.get("user-agent", "")
    assert "MediaMythBuster" in ua
    assert "devmindset.in" in ua
    assert client.follow_redirects


async def test_openalex_survives_a_work_with_no_journal():
    """OpenAlex sets primary_location.source to null rather than omitting it, so
    `.get("source", {})` returns None and the default never applies."""
    payload = {
        "results": [
            {"id": "https://openalex.org/W1", "title": "Preprint",
             "primary_location": {"source": None}, "publication_year": 2024},
            {"id": "https://openalex.org/W2", "title": "No location at all",
             "primary_location": None},
            {"id": "https://openalex.org/W3", "title": "Journal article",
             "primary_location": {"source": {"display_name": "Nature"}}},
        ]
    }

    transport = httpx.MockTransport(lambda r: httpx.Response(200, json=payload))
    async with httpx.AsyncClient(transport=transport) as client:
        docs = await sources.openalex(client, "anything")

    assert len(docs) == 3  # the null-source work must not blow up the batch
    assert docs[2].publisher == "Nature"
    # With no journal, Doc backfills the publisher from the URL host.
    assert docs[0].publisher == "openalex.org"


async def test_worldbank_needs_two_matching_terms(monkeypatch):
    """One shared word would drag in every loosely related indicator."""
    monkeypatch.setattr(sources, "_WDI_CACHE", [
        {"id": "EG.ELC.COAL.ZS",
         "name": "Electricity production from coal sources (% of total)",
         "sourceNote": "Share of coal."},
        {"id": "EG.ELC.HYRO.ZS",
         "name": "Electricity production from hydroelectric sources (% of total)",
         "sourceNote": "Share of hydro."},
        {"id": "SP.POP.TOTL", "name": "Population, total", "sourceNote": "People."},
    ])
    async with httpx.AsyncClient() as client:
        docs = await sources.worldbank(client, "Coal supplies about 70% of electricity production")

    urls = [d.url for d in docs]
    assert any("EG.ELC.COAL.ZS" in u for u in urls)
    assert not any("SP.POP.TOTL" in u for u in urls)


async def test_worldbank_returns_nothing_when_catalogue_unavailable(monkeypatch):
    monkeypatch.setattr(sources, "_WDI_CACHE", [])
    async with httpx.AsyncClient() as client:
        assert await sources.worldbank(client, "anything at all here") == []


@pytest.mark.parametrize("status", [403, 500])
async def test_a_failing_source_does_not_take_down_retrieval(status, monkeypatch):
    """SearXNG 403s until json is in its `formats` list; that must not zero out
    the other tiers."""

    async def dead(client, query, *a, **k):
        raise httpx.HTTPStatusError("boom", request=None, response=None)

    async def alive(client, query, *a, **k):
        return [sources.Doc(url="https://en.wikipedia.org/wiki/X", title="X",
                            snippet="text", tier="structured")]

    monkeypatch.setattr(sources, "searxng", dead)
    monkeypatch.setattr(sources, "google_factcheck", dead)
    monkeypatch.setattr(sources, "tavily", dead)
    monkeypatch.setattr(sources, "ROUTES", {"general": [alive]})

    docs = await sources.gather("some claim", "general")
    assert len(docs) == 1
    assert docs[0].tier == "structured"


async def test_results_are_deduped_and_ordered_by_tier(monkeypatch):
    async def none(client, query, *a, **k):
        return []

    async def factchecks(client, query, *a, **k):
        return [sources.Doc(url="https://snopes.com/a?utm=1", title="FC",
                            snippet="s", tier="factcheck")]

    async def mixed(client, query, *a, **k):
        return [
            sources.Doc(url="https://example.org/web", title="W", snippet="s", tier="web"),
            sources.Doc(url="https://snopes.com/a", title="dupe", snippet="s",
                        tier="factcheck"),
            sources.Doc(url="https://nih.gov/s", title="S", snippet="s", tier="structured"),
        ]

    monkeypatch.setattr(sources, "google_factcheck", factchecks)
    monkeypatch.setattr(sources, "searxng", none)
    monkeypatch.setattr(sources, "tavily", none)
    monkeypatch.setattr(sources, "ROUTES", {"general": [mixed]})

    docs = await sources.gather("claim", "general")
    assert [d.tier for d in docs] == ["factcheck", "structured", "web"]
