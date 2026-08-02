"""DeepSeek V4 is a reasoning model and `max_tokens` bounds reasoning + answer
together. A long transcript can spend the whole budget thinking and return empty
content — which is exactly how the first real reel failed, after burning three
full budgets retrying into the same wall."""

import httpx
import pytest

from app.providers.deepseek import DeepSeek, ProviderError


def response(content, *, reasoning="", finish="stop", tokens=100):
    message = {"role": "assistant", "content": content}
    if reasoning:
        message["reasoning_content"] = reasoning
    return {
        "choices": [{"message": message, "finish_reason": finish}],
        "usage": {"prompt_tokens": 50, "completion_tokens": tokens},
    }


def client_returning(*payloads):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json as _json

        calls.append(_json.loads(request.content))
        return httpx.Response(200, json=payloads[min(len(calls) - 1, len(payloads) - 1)])

    return httpx.AsyncClient(transport=httpx.MockTransport(handler)), calls


@pytest.fixture(autouse=True)
def key(monkeypatch):
    monkeypatch.setattr("app.providers.deepseek.settings.deepseek_api_key", "test")


async def test_reasoning_starvation_retries_with_reasoning_off():
    starved = response("", reasoning="thinking " * 500, finish="length", tokens=6000)
    ok = response('{"claims": [{"text": "x"}]}')
    client, calls = client_returning(starved, ok)

    async with client:
        got = await DeepSeek(client).json(system="s", user="u", stage="claims")

    assert got == {"claims": [{"text": "x"}]}
    assert len(calls) == 2
    assert "reasoning_effort" not in calls[0], "first attempt should allow reasoning"
    assert calls[1]["reasoning_effort"] == "none", "retry must disable reasoning"


async def test_starvation_retry_does_not_append_a_corrective_nudge():
    """The old path replayed the empty answer and asked for JSON, wasting another
    full budget on the same wall."""
    starved = response("", reasoning="thinking", finish="length", tokens=6000)
    ok = response('{"ok": 1}')
    client, calls = client_returning(starved, ok)

    async with client:
        await DeepSeek(client).json(system="s", user="u", stage="claims")

    assert len(calls[1]["messages"]) == 2, "retry should resend the original prompt only"


async def test_reasoning_can_be_disabled_up_front():
    client, calls = client_returning(response('{"a": 1}'))
    async with client:
        await DeepSeek(client).json(system="s", user="u", stage="lean", reasoning=False)
    assert calls[0]["reasoning_effort"] == "none"


async def test_reasoning_is_on_by_default():
    client, calls = client_returning(response('{"a": 1}'))
    async with client:
        await DeepSeek(client).json(system="s", user="u", stage="claims")
    assert "reasoning_effort" not in calls[0]


async def test_malformed_json_still_gets_the_corrective_nudge():
    client, calls = client_returning(response("not json at all"), response('{"a": 1}'))
    async with client:
        got = await DeepSeek(client).json(system="s", user="u", stage="claims")
    assert got == {"a": 1}
    assert len(calls[1]["messages"]) == 4, "nudge should replay the bad answer"


async def test_persistent_failure_raises_rather_than_returning_none():
    client, _ = client_returning(response("still not json"))
    async with client:
        with pytest.raises(ProviderError):
            await DeepSeek(client).json(system="s", user="u", stage="claims", retries=1)


async def test_usage_is_recorded_for_every_attempt():
    client, _ = client_returning(
        response("", reasoning="t", finish="length", tokens=6000),
        response('{"a": 1}', tokens=42),
    )
    async with client:
        ds = DeepSeek(client)
        await ds.json(system="s", user="u", stage="claims")

    assert len(ds.usage) == 2, "a starved attempt still costs money and must be logged"
    assert ds.usage[0]["tokens_out"] == 6000
    assert ds.cost_usd() > 0
