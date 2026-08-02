"""Per-report progress events over Redis pub/sub, surfaced to the browser via SSE.

The pipeline takes 30-90s, so live stage progress is functional, not decoration.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import redis.asyncio as aioredis

from app.config import settings

STAGES = [
    "ingest",
    "transcribe",
    "video",
    "forensics",
    "claims",
    "evidence",
    "adjudicate",
    "score",
    "done",
]

_redis: aioredis.Redis | None = None


def get_redis() -> aioredis.Redis:
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    return _redis


def _channel(report_id: str) -> str:
    return f"report:{report_id}:events"


async def publish(report_id: str, stage: str, status: str, **extra: Any) -> None:
    payload = {"stage": stage, "status": status, **extra}
    r = get_redis()
    await r.publish(_channel(report_id), json.dumps(payload))
    # Keep a short replay buffer so a client connecting mid-run isn't blind to
    # stages that already completed.
    key = f"report:{report_id}:log"
    await r.rpush(key, json.dumps(payload))
    await r.expire(key, 3600)


async def replay(report_id: str) -> list[dict[str, Any]]:
    raw = await get_redis().lrange(f"report:{report_id}:log", 0, -1)
    return [json.loads(x) for x in raw]


async def subscribe(report_id: str) -> AsyncIterator[dict[str, Any]]:
    pubsub = get_redis().pubsub()
    await pubsub.subscribe(_channel(report_id))
    try:
        async for message in pubsub.listen():
            if message.get("type") == "message":
                yield json.loads(message["data"])
    finally:
        await pubsub.unsubscribe(_channel(report_id))
        await pubsub.aclose()
