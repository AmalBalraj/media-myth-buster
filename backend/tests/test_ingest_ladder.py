"""The ladder must degrade cleanly when the Graph API is not configured — a
personal-use deployment running only yt-dlp is a supported mode, not a failure."""

import pytest

from app.ingest import IngestError, graph_api_configured, resolve

GRAPH_KEYS = {
    "ig_user_id": "123",
    "ig_access_token": "tok",
    "meta_app_id": "app",
    "meta_app_secret": "sec",
}


def configure(monkeypatch, **overrides):
    for key, value in {**GRAPH_KEYS, **overrides}.items():
        monkeypatch.setattr(f"app.ingest.settings.{key}", value)


def test_graph_configured_requires_all_four(monkeypatch):
    configure(monkeypatch)
    assert graph_api_configured()

    for missing in GRAPH_KEYS:
        configure(monkeypatch, **{missing: ""})
        assert not graph_api_configured(), f"{missing} should be required"


async def test_unconfigured_graph_is_skipped_not_attempted(monkeypatch):
    """No Graph call should be made at all — not attempted-and-caught."""
    configure(monkeypatch, ig_access_token="")
    called = False

    async def explode(*a, **k):
        nonlocal called
        called = True
        raise AssertionError("business_discovery must not be called when unconfigured")

    async def fake_ytdlp(shortcode):
        return f"bundle-for-{shortcode}"

    monkeypatch.setattr("app.ingest.fetch_via_discovery", explode)
    monkeypatch.setattr("app.ingest.fetch_via_ytdlp", fake_ytdlp)

    got = await resolve("https://www.instagram.com/reel/ABC123/")
    assert got == "bundle-for-ABC123"
    assert not called


async def test_graph_failure_falls_through_to_ytdlp(monkeypatch):
    configure(monkeypatch)

    async def graph_fails(*a, **k):
        raise IngestError("not a Professional account")

    async def fake_ytdlp(shortcode):
        return "fallback-bundle"

    monkeypatch.setattr("app.ingest.fetch_via_discovery", graph_fails)
    monkeypatch.setattr("app.ingest.fetch_via_ytdlp", fake_ytdlp)

    assert await resolve("https://www.instagram.com/reel/ABC123/") == "fallback-bundle"


async def test_both_paths_failing_reports_both_reasons(monkeypatch):
    configure(monkeypatch)

    async def graph_fails(*a, **k):
        raise IngestError("personal account")

    async def ytdlp_fails(shortcode):
        raise IngestError("login required")

    monkeypatch.setattr("app.ingest.fetch_via_discovery", graph_fails)
    monkeypatch.setattr("app.ingest.fetch_via_ytdlp", ytdlp_fails)

    with pytest.raises(IngestError) as exc:
        await resolve("https://www.instagram.com/reel/ABC123/")
    assert "personal account" in str(exc.value)
    assert "login required" in str(exc.value)


async def test_ytdlp_disabled_gives_an_actionable_message(monkeypatch):
    configure(monkeypatch, ig_access_token="")
    monkeypatch.setattr("app.ingest.ytdlp.settings.enable_ytdlp_fallback", False)

    with pytest.raises(IngestError) as exc:
        await resolve("https://www.instagram.com/reel/ABC123/")
    assert "not reachable" in str(exc.value)
