"""Sanctioned Meta Graph API ingest paths.

Three surfaces, in descending order of legitimacy (ARCHITECTURE.md §2):

  1. mentioned_media  - media that @mentioned the bot, INCLUDING media we don't own.
  2. business_discovery - any Professional account's public media, by username.
  3. oEmbed - app-token only; thumbnail + author attribution, no user token needed.

The trick that makes (2) usable from a bare reel URL: a URL like
/reel/{shortcode}/ carries no username, so we call oEmbed first to learn
`author_name`, then business_discovery that handle and match on shortcode.
"""

from __future__ import annotations

from datetime import datetime

import httpx

from app.config import settings
from app.ingest.base import CreatorInfo, IngestError, MediaBundle, canonical_permalink

MEDIA_FIELDS = (
    "id,caption,media_type,media_url,permalink,timestamp,like_count,comments_count,view_count"
)
ACCOUNT_FIELDS = (
    "id,username,name,biography,followers_count,media_count,profile_picture_url,website"
)


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S%z")
    except ValueError:
        return None


class GraphAPI:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self.client = client

    async def _get(self, path: str, params: dict[str, str]) -> dict:
        r = await self.client.get(f"{settings.graph_base}/{path}", params=params, timeout=20)
        if r.status_code >= 400:
            raise IngestError(f"Graph API {path} -> {r.status_code}: {r.text[:300]}")
        return r.json()

    # ── oEmbed ───────────────────────────────────────────────────────────────
    async def oembed(self, permalink: str) -> dict:
        """App-token only. Cheap probe: validates the URL and reveals the author handle."""
        if not (settings.meta_app_id and settings.meta_app_secret):
            raise IngestError("oEmbed needs META_APP_ID and META_APP_SECRET")
        return await self._get(
            "instagram_oembed",
            {
                "url": permalink,
                "access_token": f"{settings.meta_app_id}|{settings.meta_app_secret}",
                "omitscript": "true",
            },
        )

    # ── business_discovery ───────────────────────────────────────────────────
    async def business_discovery(self, username: str, limit: int = 25) -> dict:
        """Target must be a Professional (Business/Creator) account. Personal accounts 404."""
        if not (settings.ig_user_id and settings.ig_access_token):
            raise IngestError("business_discovery needs IG_USER_ID and IG_ACCESS_TOKEN")
        fields = (
            f"business_discovery.username({username})"
            f"{{{ACCOUNT_FIELDS},media.limit({limit}){{{MEDIA_FIELDS}}}}}"
        )
        return await self._get(
            settings.ig_user_id, {"fields": fields, "access_token": settings.ig_access_token}
        )

    # ── mentioned_media ──────────────────────────────────────────────────────
    async def mentioned_media(self, ig_media_id: str) -> dict:
        """Media that @mentioned us. Works on media we do NOT own — the key affordance."""
        fields = (
            f"mentioned_media.media_id({ig_media_id})"
            "{caption,media_type,media_url,permalink,timestamp,username,like_count,comments_count}"
        )
        return await self._get(
            settings.ig_user_id, {"fields": fields, "access_token": settings.ig_access_token}
        )


def _creator_from_discovery(node: dict) -> CreatorInfo:
    return CreatorInfo(
        handle=node.get("username", ""),
        display_name=node.get("name"),
        ig_user_id=node.get("id"),
        followers=node.get("followers_count"),
        media_count=node.get("media_count"),
        biography=node.get("biography"),
        is_professional=True,  # business_discovery only resolves Professional accounts
    )


def _bundle_from_media_node(
    node: dict, *, shortcode: str, ingest_path: str, creator: CreatorInfo | None
) -> MediaBundle:
    return MediaBundle(
        platform="instagram",
        shortcode=shortcode,
        ingest_path=ingest_path,
        media_url=node.get("media_url"),
        permalink=node.get("permalink") or canonical_permalink(shortcode),
        media_type=node.get("media_type"),
        caption=node.get("caption"),
        posted_at=_parse_ts(node.get("timestamp")),
        like_count=node.get("like_count"),
        comment_count=node.get("comments_count"),
        view_count=node.get("view_count"),
        creator=creator,
        raw=node,
    )


async def fetch_via_discovery(
    client: httpx.AsyncClient, shortcode: str, username: str | None = None
) -> MediaBundle:
    """Resolve a reel by shortcode. Falls back to oEmbed to learn the handle first."""
    api = GraphAPI(client)
    permalink = canonical_permalink(shortcode)
    thumbnail = None

    if not username:
        oe = await api.oembed(permalink)
        username = (oe.get("author_name") or "").strip()
        thumbnail = oe.get("thumbnail_url")
        if not username:
            raise IngestError("oEmbed returned no author_name; cannot resolve creator")

    payload = await api.business_discovery(username)
    node = payload.get("business_discovery")
    if not node:
        raise IngestError(
            f"@{username} is not reachable via business_discovery "
            "(personal accounts are not exposed by any sanctioned API)"
        )

    creator = _creator_from_discovery(node)
    for item in node.get("media", {}).get("data", []):
        if shortcode in (item.get("permalink") or ""):
            bundle = _bundle_from_media_node(
                item, shortcode=shortcode, ingest_path="discovery", creator=creator
            )
            bundle.thumbnail_url = thumbnail
            return bundle

    raise IngestError(
        f"Reel {shortcode} not in @{username}'s {len(node.get('media', {}).get('data', []))} "
        "most recent media — older than the discovery window"
    )


async def fetch_via_mention(client: httpx.AsyncClient, ig_media_id: str) -> MediaBundle:
    api = GraphAPI(client)
    payload = await api.mentioned_media(ig_media_id)
    node = payload.get("mentioned_media") or {}
    if not node:
        raise IngestError(f"mentioned_media returned nothing for {ig_media_id}")

    permalink = node.get("permalink") or ""
    shortcode = permalink.rstrip("/").rsplit("/", 1)[-1] or ig_media_id
    creator = CreatorInfo(handle=node.get("username", "unknown"), is_professional=False)
    return _bundle_from_media_node(
        node, shortcode=shortcode, ingest_path="mention", creator=creator
    )
