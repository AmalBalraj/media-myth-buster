"""Last-resort fallback for personal-account reels no sanctioned API can reach.

Off by default (ENABLE_YTDLP_FALLBACK). This violates Meta's ToS and breaks
periodically by design — keep it isolated behind the MediaBundle contract so
nothing downstream ever depends on it.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

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
        # Checked here rather than left to yt-dlp: a path that is missing inside
        # the container (a host path in a container-bound config is the usual
        # slip) otherwise surfaces as a generic extraction failure.
        cookies = Path(settings.ytdlp_cookies_file)
        if not cookies.is_file():
            raise IngestError(
                f"YTDLP_COOKIES_FILE points at {cookies}, which does not exist "
                "inside the container. Ship it with ./deploy/push-cookies.sh, "
                "which places it in the mounted data/ directory."
            )
        if not os.access(cookies, os.R_OK):
            raise IngestError(f"Cookie file {cookies} exists but is not readable")
        cmd += ["--cookies", str(cookies)]
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
            # `uploader_id` is Instagram's numeric account id, which is useless
            # as a display handle and fragments the creator track record across
            # ids. `channel`/`uploader` carry the @username.
            handle=(
                info.get("channel")
                or info.get("uploader")
                or info.get("uploader_id")
                or "unknown"
            ),
            display_name=info.get("uploader") or info.get("channel"),
            ig_user_id=info.get("uploader_id"),
        ),
        raw={"extractor": "yt-dlp"},
    )
