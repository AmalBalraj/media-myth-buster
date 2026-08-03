from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

SHORTCODE_RE = re.compile(
    r"instagram\.com/(?:[\w.\-]+/)?(?:reels?|p|tv)/(?P<shortcode>[A-Za-z0-9_-]+)"
)


class IngestError(RuntimeError):
    """Raised when no adapter can reach the media."""


@dataclass(slots=True)
class CreatorInfo:
    handle: str
    display_name: str | None = None
    ig_user_id: str | None = None
    followers: int | None = None
    media_count: int | None = None
    biography: str | None = None
    is_professional: bool = False
    verified: bool = False


@dataclass(slots=True)
class MediaBundle:
    """The single contract every ingest adapter emits.

    Which rung of the ladder fired (§2.2) must never leak past this boundary —
    that is what keeps the analysis pipeline platform-agnostic.
    """

    platform: str
    shortcode: str
    ingest_path: str  # mention | discovery | oembed | ytdlp
    media_url: str | None = None
    permalink: str | None = None
    media_type: str | None = None
    caption: str | None = None
    posted_at: datetime | None = None
    duration: float | None = None
    like_count: int | None = None
    comment_count: int | None = None
    view_count: int | None = None
    thumbnail_url: str | None = None
    # Photo posts and carousels: one entry per slide, in order. Infographic
    # slideshows are a major misinformation format and carry no audio at all,
    # so the pipeline reads them through OCR rather than transcription.
    image_urls: list[str] = field(default_factory=list)
    creator: CreatorInfo | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def has_video(self) -> bool:
        return bool(self.media_url)

    @property
    def has_images(self) -> bool:
        return bool(self.image_urls)

    @property
    def has_media(self) -> bool:
        return self.has_video or self.has_images

    @property
    def kind(self) -> str:
        return "video" if self.has_video else "images" if self.has_images else "empty"


def canonical_shortcode(url: str) -> str:
    """Extract the stable shortcode so the same reel always maps to one cache key."""
    m = SHORTCODE_RE.search(url)
    if not m:
        raise IngestError(f"Not a recognisable Instagram media URL: {url!r}")
    return m.group("shortcode")


def canonical_permalink(shortcode: str) -> str:
    return f"https://www.instagram.com/reel/{shortcode}/"
