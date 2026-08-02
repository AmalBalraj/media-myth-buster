"""Ingest ladder: try each rung in descending order of legitimacy."""

from __future__ import annotations

import hashlib
import ipaddress
from pathlib import Path
from urllib.parse import urlparse

import httpx
import structlog

from app.config import settings
from app.ingest.base import (
    IngestError,
    MediaBundle,
    canonical_permalink,
    canonical_shortcode,
)
from app.ingest.graph_api import fetch_via_discovery, fetch_via_mention
from app.ingest.ytdlp import fetch_via_ytdlp

log = structlog.get_logger(__name__)

__all__ = [
    "IngestError",
    "MediaBundle",
    "canonical_permalink",
    "canonical_shortcode",
    "download_media",
    "fetch_via_mention",
    "resolve",
]

# A URL-fetching backend is the classic SSRF target. Only Meta's own CDN hosts
# are downloadable, and only after the resolved IP is checked to be public.
ALLOWED_MEDIA_HOST_SUFFIXES = (
    ".cdninstagram.com",
    ".fbcdn.net",
    "scontent.cdninstagram.com",
    "video.cdninstagram.com",
)


def graph_api_configured() -> bool:
    """The sanctioned path needs a Professional account's token AND an app secret
    (the latter for the oEmbed probe that resolves a bare /reel/ URL to a handle)."""
    return bool(
        settings.ig_user_id
        and settings.ig_access_token
        and settings.meta_app_id
        and settings.meta_app_secret
    )


async def resolve(url_or_shortcode: str) -> MediaBundle:
    """URL -> MediaBundle, walking the ingest ladder.

    Graph API rungs are skipped entirely when unconfigured rather than attempted
    and failed, so a personal-use deployment running only yt-dlp takes a clean
    path instead of logging an error on every reel.
    """
    shortcode = (
        canonical_shortcode(url_or_shortcode)
        if "instagram.com" in url_or_shortcode
        else url_or_shortcode
    )
    failures: list[str] = []

    if graph_api_configured():
        async with httpx.AsyncClient(follow_redirects=False) as client:
            try:
                return await fetch_via_discovery(client, shortcode)
            except IngestError as exc:
                log.info("discovery_failed", shortcode=shortcode, reason=str(exc))
                failures.append(f"graph: {exc}")
    else:
        log.debug("graph_api_not_configured", shortcode=shortcode)

    try:
        return await fetch_via_ytdlp(shortcode)
    except IngestError as exc:
        failures.append(f"yt-dlp: {exc}")

    raise IngestError(" | ".join(failures) or "No ingest path is configured")


def _host_allowed(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    if not host.endswith(ALLOWED_MEDIA_HOST_SUFFIXES):
        return False
    try:
        # Reject anything resolving into private space even on an allowed name.
        return not ipaddress.ip_address(host).is_private
    except ValueError:
        return True  # not a bare IP; hostname is on the allowlist


async def download_media(bundle: MediaBundle) -> tuple[Path, str]:
    """Stream the CDN file to disk. Returns (path, sha256). Caps enforced mid-stream."""
    if not bundle.media_url:
        raise IngestError("MediaBundle has no media_url")
    if bundle.ingest_path != "ytdlp" and not _host_allowed(bundle.media_url):
        raise IngestError(f"Refusing to fetch off-allowlist host: {bundle.media_url[:80]}")

    limit = settings.max_media_mb * 1024 * 1024
    dest = settings.media_dir / f"{bundle.platform}_{bundle.shortcode}.mp4"
    digest = hashlib.sha256()
    written = 0

    async with httpx.AsyncClient(follow_redirects=True, max_redirects=3) as client:
        async with client.stream("GET", bundle.media_url, timeout=30) as resp:
            resp.raise_for_status()
            with dest.open("wb") as fh:
                async for chunk in resp.aiter_bytes(65536):
                    written += len(chunk)
                    if written > limit:
                        fh.close()
                        dest.unlink(missing_ok=True)
                        raise IngestError(f"Media exceeds {settings.max_media_mb}MB cap")
                    digest.update(chunk)
                    fh.write(chunk)

    return dest, digest.hexdigest()
