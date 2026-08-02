"""DeepSeek V4 Flash — the text reasoning workhorse.

$0.14/M in, $0.28/M out, $0.0028/M on cache hits (98% off), 1M context, 384K output,
text-only. The cache discount is why every prompt here puts the long stable system
block FIRST and the variable payload last: that prefix is what gets cached.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from typing import Any

import httpx
import structlog

from app.config import settings

log = structlog.get_logger(__name__)

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


class ProviderError(RuntimeError):
    pass


def _extract_json(text: str) -> Any:
    """Models occasionally wrap JSON in prose or fences even under json_object mode."""
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    if m := _FENCE_RE.search(text):
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        return json.loads(text[start : end + 1])
    raise ProviderError(f"No JSON object in response: {text[:200]}")


class DeepSeek:
    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client
        self.model = settings.deepseek_model
        self.usage: list[dict[str, Any]] = []

    async def _post(self, payload: dict) -> dict:
        if not settings.deepseek_api_key:
            raise ProviderError("DEEPSEEK_API_KEY is not set")
        headers = {"Authorization": f"Bearer {settings.deepseek_api_key}"}
        url = f"{settings.deepseek_base_url}/chat/completions"

        async def _send(client: httpx.AsyncClient) -> dict:
            r = await client.post(url, json=payload, headers=headers, timeout=180)
            if r.status_code >= 400:
                raise ProviderError(f"DeepSeek {r.status_code}: {r.text[:300]}")
            return r.json()

        if self._client:
            return await _send(self._client)
        async with httpx.AsyncClient() as client:
            return await _send(client)

    async def json(
        self,
        *,
        system: str,
        user: str,
        stage: str,
        temperature: float = 0.2,
        max_tokens: int = 12000,
        retries: int = 2,
        reasoning: bool = True,
    ) -> Any:
        """One JSON call.

        V4 is a reasoning model: it emits `reasoning_content` alongside `content`,
        and `max_tokens` bounds the two *together*. A long transcript can spend the
        entire budget thinking and return empty content — which is what killed the
        first real reel. Budgets here are therefore sized for reasoning + answer,
        and exhaustion self-heals by retrying with reasoning off rather than
        burning another full budget on the same wall.

        `reasoning=False` is for stages where it buys nothing (formatting, scoring
        against a fixed rubric) and costs ~50s of latency per call.
        """
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        prompt_hash = hashlib.sha256((system + user).encode()).hexdigest()[:32]
        use_reasoning = reasoning

        for attempt in range(retries + 1):
            payload: dict[str, Any] = {
                "model": self.model,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "response_format": {"type": "json_object"},
            }
            if not use_reasoning:
                payload["reasoning_effort"] = "none"

            started = time.perf_counter()
            data = await self._post(payload)
            latency_ms = int((time.perf_counter() - started) * 1000)

            usage = data.get("usage", {}) or {}
            choice = data["choices"][0]
            message = choice.get("message", {})
            content = message.get("content") or ""
            reasoning_len = len(message.get("reasoning_content") or "")

            self.usage.append(
                {
                    "stage": stage,
                    "provider": "deepseek",
                    "model": self.model,
                    "prompt_hash": prompt_hash,
                    "tokens_in": usage.get("prompt_tokens"),
                    "tokens_out": usage.get("completion_tokens"),
                    "cached": bool(usage.get("prompt_cache_hit_tokens")),
                    "latency_ms": latency_ms,
                }
            )

            starved = not content.strip() and (
                choice.get("finish_reason") == "length" or reasoning_len > 0
            )
            if starved and use_reasoning:
                # Retrying the same request would just hit the same wall, so drop
                # reasoning and keep the budget for the answer.
                log.warning(
                    "deepseek_reasoning_starved",
                    stage=stage,
                    reasoning_chars=reasoning_len,
                    tokens_out=usage.get("completion_tokens"),
                )
                use_reasoning = False
                continue

            try:
                return _extract_json(content)
            except (ProviderError, json.JSONDecodeError):
                if attempt == retries:
                    raise
                log.warning(
                    "deepseek_bad_json", stage=stage, attempt=attempt,
                    content_preview=content[:120],
                )
                messages += [
                    {"role": "assistant", "content": content[:2000]},
                    {
                        "role": "user",
                        "content": "That was not valid JSON. Reply with the JSON object only.",
                    },
                ]

        raise ProviderError(f"{stage}: exhausted retries without valid JSON")

    def cost_usd(self) -> float:
        """Rough running cost, for the report footer. Cache-hit input is ~free."""
        cost = 0.0
        for u in self.usage:
            tin, tout = u.get("tokens_in") or 0, u.get("tokens_out") or 0
            in_rate = 0.0028 if u.get("cached") else 0.14
            cost += tin / 1e6 * in_rate + tout / 1e6 * 0.28
        return round(cost, 5)
