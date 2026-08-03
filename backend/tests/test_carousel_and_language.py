"""Photo carousels and non-English posts.

Carousels: infographic slideshows are a major misinformation format and carry no
audio, so the claims live entirely in the slide text. yt-dlp aborts on them with
"No video formats found" unless --ignore-no-formats-error is passed.

Language: `text` is always English because it drives retrieval, `verbatim` keeps
the original script, and non-English claims also get searched in their own
language — regional stories are often covered only in the regional press.
"""

import inspect

import pytest

from app.evidence import sources
from app.ingest.base import MediaBundle
from app.ingest.ytdlp import _best_thumbnail, _carousel_images

# ── Carousel ingest ──────────────────────────────────────────────────────────


def test_ytdlp_passes_ignore_no_formats_error():
    """Without this flag a photo post raises instead of returning its info dict."""
    from app.ingest import ytdlp

    assert "--ignore-no-formats-error" in inspect.getsource(ytdlp.fetch_via_ytdlp)


def test_best_thumbnail_picks_the_largest():
    """OCR quality depends on resolution; these slides are dense text."""
    node = {"thumbnails": [
        {"url": "small.jpg", "width": 150, "preference": -10},
        {"url": "large.jpg", "width": 1440, "preference": 0},
        {"url": "mid.jpg", "width": 640, "preference": -5},
    ]}
    assert _best_thumbnail(node) == "large.jpg"


def test_best_thumbnail_falls_back_to_the_plain_field():
    assert _best_thumbnail({"thumbnail": "only.jpg"}) == "only.jpg"
    assert _best_thumbnail({}) is None


def test_carousel_images_preserve_slide_order():
    info = {"entries": [
        {"thumbnails": [{"url": f"slide{i}.jpg", "width": 1080}]} for i in range(5)
    ]}
    assert _carousel_images(info) == [f"slide{i}.jpg" for i in range(5)]


def test_single_image_post_is_handled():
    assert _carousel_images({"thumbnails": [{"url": "one.jpg", "width": 1080}]}) == ["one.jpg"]


def test_carousel_is_capped():
    info = {"entries": [{"thumbnails": [{"url": f"{i}.jpg"}]} for i in range(40)]}
    assert len(_carousel_images(info, limit=12)) == 12


def test_entries_without_images_are_skipped_not_crashed():
    info = {"entries": [{"thumbnails": [{"url": "a.jpg"}]}, {}, {"thumbnails": []}]}
    assert _carousel_images(info) == ["a.jpg"]


# ── MediaBundle shape ────────────────────────────────────────────────────────


def test_bundle_reports_its_kind():
    video = MediaBundle(platform="instagram", shortcode="A", ingest_path="ytdlp",
                        media_url="https://x/v.mp4")
    photos = MediaBundle(platform="instagram", shortcode="B", ingest_path="ytdlp",
                         image_urls=["https://x/1.jpg", "https://x/2.jpg"])
    empty = MediaBundle(platform="instagram", shortcode="C", ingest_path="ytdlp")

    assert (video.kind, video.has_media, video.has_images) == ("video", True, False)
    assert (photos.kind, photos.has_media, photos.has_video) == ("images", True, False)
    assert (empty.kind, empty.has_media) == ("empty", False)


# ── Language routing ─────────────────────────────────────────────────────────


def test_wikipedia_uses_the_requested_language_edition():
    src = inspect.getsource(sources.wikipedia)
    assert "{lang}.wikipedia.org" in src


async def test_non_english_claims_also_search_the_source_language(monkeypatch):
    queried: list[tuple[str, str]] = []

    async def fake_searxng(client, query, limit=6, lang="en"):
        queried.append(("searxng", lang))
        return []

    async def fake_wikipedia(client, query, lang="en"):
        queried.append(("wikipedia", lang))
        return []

    async def none(client, query, *a, **k):
        return []

    monkeypatch.setattr(sources, "searxng", fake_searxng)
    monkeypatch.setattr(sources, "wikipedia", fake_wikipedia)
    monkeypatch.setattr(sources, "google_factcheck", none)
    monkeypatch.setattr(sources, "tavily", none)
    monkeypatch.setattr(sources, "ROUTES", {"general": [fake_wikipedia]})

    await sources.gather("Coal generates 70% of India's electricity", "general",
                         native_query="भारत में कोयला बिजली", lang="hi")

    assert ("searxng", "hi") in queried, "must search the source language too"
    assert ("wikipedia", "hi") in queried, "must query the hi.wikipedia edition"
    assert ("searxng", "en") in queried, "English search must still run"


async def test_english_claims_do_not_fan_out_twice(monkeypatch):
    queried = []

    async def fake_searxng(client, query, limit=6, lang="en"):
        queried.append(lang)
        return []

    async def none(client, query, *a, **k):
        return []

    monkeypatch.setattr(sources, "searxng", fake_searxng)
    monkeypatch.setattr(sources, "google_factcheck", none)
    monkeypatch.setattr(sources, "tavily", none)
    monkeypatch.setattr(sources, "ROUTES", {"general": []})

    await sources.gather("a claim", "general", native_query=None, lang="en")
    assert queried == ["en"]


@pytest.mark.parametrize("lang,native", [("hi", None), (None, "क्वेरी"), ("en", "query")])
async def test_native_search_needs_both_a_language_and_a_query(monkeypatch, lang, native):
    queried = []

    async def fake_searxng(client, query, limit=6, lang="en"):
        queried.append(lang)
        return []

    async def none(client, query, *a, **k):
        return []

    monkeypatch.setattr(sources, "searxng", fake_searxng)
    monkeypatch.setattr(sources, "google_factcheck", none)
    monkeypatch.setattr(sources, "tavily", none)
    monkeypatch.setattr(sources, "ROUTES", {"general": []})

    await sources.gather("claim", "general", native_query=native, lang=lang or "en")
    assert queried == ["en"]


def test_prompt_specifies_the_translation_contract():
    """This behaviour was previously emergent — the model happened to translate.
    Nothing guaranteed it, so it could silently stop."""
    from app.pipeline.prompts import CLAIM_EXTRACTION_SYSTEM as p

    assert "`text` is ALWAYS English" in p
    assert "verbatim` is ALWAYS the original" in p
    assert "native_query" in p
    assert "ISO 639-1" in p
