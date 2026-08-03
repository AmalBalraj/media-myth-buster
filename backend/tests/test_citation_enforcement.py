"""The citation rule is the system's core guarantee, so it is tested mechanically
rather than trusted to the prompt."""

import pytest

from app.evidence.retrieve import Passage
from app.pipeline.runner import _adjudicate_one
from app.providers.deepseek import _extract_json


class FakeDeepSeek:
    def __init__(self, response):
        self.response = response
        self.usage = []

    async def json(self, **kwargs):
        return self.response


def passages(n=3):
    return [
        Passage(
            id=f"E{i + 1}",
            url=f"https://example.org/{i}",
            title=f"Doc {i}",
            publisher="example.org",
            credibility=0.7,
            lean=None,
            tier="web",
            text="some evidence text",
        )
        for i in range(n)
    ]


@pytest.fixture
def patched(monkeypatch):
    def _apply(response, passes):
        async def fake_evidence(session, text, topic="general", **kwargs):
            return passes

        monkeypatch.setattr("app.pipeline.runner.evidence_for_claim", fake_evidence)
        return FakeDeepSeek(response)

    return _apply


async def test_hallucinated_citations_are_dropped(patched):
    ds = patched(
        {"verdict": "false", "confidence": 0.9, "citations": ["E1", "E9", "E42"],
         "rationale": "r", "evidence_quality": "strong"},
        passages(3),
    )
    result, _ = await _adjudicate_one(None, ds, {"text": "c", "claim_type": "factual"})
    assert result["citations"] == ["E1"]
    assert result["verdict"] == "false"  # survives: one real citation remains


async def test_verdict_with_only_fake_citations_is_downgraded(patched):
    ds = patched(
        {"verdict": "false", "confidence": 0.95, "citations": ["E9"],
         "rationale": "r", "evidence_quality": "strong"},
        passages(2),
    )
    result, _ = await _adjudicate_one(None, ds, {"text": "c", "claim_type": "factual"})
    assert result["verdict"] == "unverifiable"
    assert result["citations"] == []
    assert result["confidence"] <= 0.4


async def test_unverifiable_may_legitimately_have_no_citations(patched):
    ds = patched(
        {"verdict": "unverifiable", "confidence": 0.5, "citations": [],
         "rationale": "nothing found", "evidence_quality": "none"},
        passages(2),
    )
    result, _ = await _adjudicate_one(None, ds, {"text": "c", "claim_type": "factual"})
    assert result["verdict"] == "unverifiable"


async def test_no_evidence_short_circuits_to_unverifiable(patched):
    ds = patched({"verdict": "true", "confidence": 1.0, "citations": []}, [])
    result, _ = await _adjudicate_one(None, ds, {"text": "c", "claim_type": "factual"})
    assert result["verdict"] == "unverifiable"
    assert result["evidence_quality"] == "none"


async def test_opinions_skip_retrieval_entirely(patched):
    ds = patched({"verdict": "false"}, passages(3))
    result, evidence = await _adjudicate_one(None, ds, {"text": "c", "claim_type": "opinion"})
    assert result["verdict"] == "opinion"
    assert evidence == []


@pytest.mark.parametrize(
    "raw",
    [
        '{"a": 1}',
        '```json\n{"a": 1}\n```',
        'Here is the result:\n{"a": 1}',
        '```\n{"a": 1}\n```',
    ],
)
def test_json_extraction_survives_model_formatting_habits(raw):
    assert _extract_json(raw) == {"a": 1}
