"""A misconfigured cookie path is the most likely yt-dlp failure in production
(a host path left in a container-bound config), so it must fail loudly."""

import pytest

from app.ingest.base import IngestError
from app.ingest.ytdlp import fetch_via_ytdlp


@pytest.fixture(autouse=True)
def enabled(monkeypatch):
    monkeypatch.setattr("app.ingest.ytdlp.settings.enable_ytdlp_fallback", True)


async def test_missing_cookie_file_names_the_path_and_the_fix(monkeypatch):
    monkeypatch.setattr(
        "app.ingest.ytdlp.settings.ytdlp_cookies_file", "/data/not-there.txt"
    )
    with pytest.raises(IngestError) as exc:
        await fetch_via_ytdlp("ABC123")
    message = str(exc.value)
    assert "/data/not-there.txt" in message
    assert "push-cookies.sh" in message


async def test_unreadable_cookie_file_is_distinguished_from_missing(
    monkeypatch, tmp_path
):
    cookies = tmp_path / "cookies.txt"
    cookies.write_text("# Netscape HTTP Cookie File\n")
    cookies.chmod(0o000)
    monkeypatch.setattr("app.ingest.ytdlp.settings.ytdlp_cookies_file", str(cookies))
    try:
        with pytest.raises(IngestError, match="not readable"):
            await fetch_via_ytdlp("ABC123")
    finally:
        cookies.chmod(0o600)


async def test_no_cookie_file_configured_is_allowed(monkeypatch):
    """Cookies are optional — some public reels resolve without a session."""
    monkeypatch.setattr("app.ingest.ytdlp.settings.ytdlp_cookies_file", "")

    async def fake_exec(*cmd, **kwargs):
        assert "--cookies" not in cmd

        class Proc:
            returncode = 0

            async def communicate(self):
                return (b'{"url": "https://x.cdninstagram.com/v.mp4"}', b"")

        return Proc()

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)
    bundle = await fetch_via_ytdlp("ABC123")
    assert bundle.ingest_path == "ytdlp"


async def test_disabled_fallback_refuses_before_touching_cookies(monkeypatch):
    monkeypatch.setattr("app.ingest.ytdlp.settings.enable_ytdlp_fallback", False)
    monkeypatch.setattr("app.ingest.ytdlp.settings.ytdlp_cookies_file", "/nope.txt")
    with pytest.raises(IngestError, match="not reachable"):
        await fetch_via_ytdlp("ABC123")
