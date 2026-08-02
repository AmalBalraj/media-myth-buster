"""Last-resort fallback for personal-account reels no sanctioned API can reach.

Off by default (ENABLE_YTDLP_FALLBACK). This violates Meta's ToS and breaks
periodically by design — keep it isolated behind the MediaBundle contract so
nothing downstream ever depends on it.
"""

from __future__ import annotations

import asyncio
import json

from app.config import settings
from app.ingest.base import CreatorInfo, IngestError, MediaBundle, canonical_permalink


async def fetch_via_ytdlp(shortcode: str) -> MediaBundle:
    if not settings.enable_ytdlp_fallback:
        raise IngestError(
            "This reel is not reachable via the Instagram API "
            "(likely a personal, non-Professional account) and the fallback is disabled."
        )

    cmd = ["yt-dlp", "--dump-single-json", "--no-warnings", "--skip-download"]
    if settings.ytdlp_cookies_file:
        cmd += ["--cookies", settings.ytdlp_cookies_file]
    cmd.append(canonical_permalink(shortcode))

    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
    except TimeoutError as exc:
        proc.kill()
        raise IngestError("yt-dlp timed out") from exc

    if proc.returncode != 0:
        raise IngestError(f"yt-dlp failed: {stderr.decode()[:300]}")

    info = json.loads(stdout)
    return MediaBundle(
        platform="instagram",
        shortcode=shortcode,
        ingest_path="ytdlp",
        media_url=info.get("url"),
        permalink=info.get("webpage_url") or canonical_permalink(shortcode),
        media_type="VIDEO",
        caption=info.get("description"),
        duration=info.get("duration"),
        like_count=info.get("like_count"),
        comment_count=info.get("comment_count"),
        view_count=info.get("view_count"),
        thumbnail_url=info.get("thumbnail"),
        creator=CreatorInfo(
            handle=info.get("uploader_id") or info.get("uploader") or "unknown",
            display_name=info.get("uploader"),
        ),
        raw={"extractor": "yt-dlp"},
    )
