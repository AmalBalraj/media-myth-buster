"""Transcription, video analysis and forensics are optional by design — the
pipeline checks afterwards that at least one of transcript/OCR produced content.

They previously caught only ProviderError, so a malformed provider response
(Gemini returning a top-level JSON array) raised TypeError and killed an entire
analysis that the transcript alone could have carried."""

import pytest

from app.pipeline import runner
from app.providers.deepseek import ProviderError
from app.providers.gemini_video import _coerce_analysis


@pytest.fixture(autouse=True)
def silence_events(monkeypatch):
    async def noop(*a, **k):
        return None

    monkeypatch.setattr(runner.events, "publish", noop)


@pytest.mark.parametrize(
    "boom",
    [TypeError("list indices must be integers"), KeyError("candidates"),
     ValueError("bad"), ProviderError("quota")],
)
async def test_video_stage_degrades_on_any_provider_error(monkeypatch, boom, tmp_path):
    async def explode(path, model=None):
        raise boom

    monkeypatch.setattr(runner.gemini_video, "analyse_video", explode)
    result = await runner._stage_video("r1", tmp_path / "x.mp4")
    assert result["on_screen_text"] == []
    assert "error" in result


@pytest.mark.parametrize("boom", [TypeError("boom"), ProviderError("quota")])
async def test_transcribe_stage_degrades(monkeypatch, boom, tmp_path):
    async def explode(path, language=None):
        raise boom

    monkeypatch.setattr(runner.groq_asr, "transcribe", explode)
    result = await runner._stage_transcribe("r1", tmp_path / "x.mp4")
    assert result["text"] == ""
    assert result["segments"] == []


async def test_forensics_stage_degrades(monkeypatch):
    async def explode(media_url, shortcode):
        raise RuntimeError("space is cold-starting")

    monkeypatch.setattr(runner.forensics_api, "analyse", explode)
    assert await runner._stage_forensics("r1", "/data/x.mp4", "ABC") == []


# ── Gemini shape coercion ────────────────────────────────────────────────────

def test_object_response_passes_through():
    got = _coerce_analysis({"on_screen_text": [], "visual_summary": "s"}, "m")
    assert got["_model"] == "m"


def test_object_wrapped_in_a_list_is_unwrapped():
    got = _coerce_analysis([{"on_screen_text": [{"text": "hi"}], "visual_summary": "s"}], "m")
    assert got["visual_summary"] == "s"
    assert got["_model"] == "m"


def test_bare_span_list_is_treated_as_ocr():
    """OCR is the field claim extraction actually depends on, so a bare list of
    spans is still usable rather than a total loss."""
    got = _coerce_analysis(
        [{"t_start": 0, "text": "COAL 70%", "kind": "caption"},
         {"t_start": 3, "text": "EIFFEL 330m", "kind": "chyron"}], "m")
    assert len(got["on_screen_text"]) == 2
    assert got["_coerced"] is True


@pytest.mark.parametrize("junk", ["a string", 42, [], [1, 2, 3], None])
def test_unusable_shapes_raise_a_clear_provider_error(junk):
    with pytest.raises(ProviderError, match="unusable shape"):
        _coerce_analysis(junk, "m")


def test_ocr_timeline_tolerates_coerced_output():
    from app.providers.gemini_video import ocr_timeline

    coerced = _coerce_analysis([{"t_start": 1.5, "text": "X", "kind": "caption"}], "m")
    assert "X" in ocr_timeline(coerced)
