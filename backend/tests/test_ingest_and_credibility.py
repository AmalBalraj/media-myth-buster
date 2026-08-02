import pytest

from app.evidence import credibility
from app.ingest import _host_allowed
from app.ingest.base import IngestError, canonical_shortcode


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://www.instagram.com/reel/ABC123xyz/", "ABC123xyz"),
        ("https://instagram.com/reels/ABC123xyz", "ABC123xyz"),
        ("https://www.instagram.com/someuser/reel/ABC123xyz/?igsh=abc", "ABC123xyz"),
        ("https://www.instagram.com/p/Xy-_9/", "Xy-_9"),
        ("https://www.instagram.com/tv/Xy-_9/", "Xy-_9"),
    ],
)
def test_shortcode_extraction(url, expected):
    assert canonical_shortcode(url) == expected


def test_url_variants_collapse_to_one_cache_key():
    bare = canonical_shortcode("https://www.instagram.com/reel/ABC123xyz/")
    tracked = canonical_shortcode(
        "https://www.instagram.com/creator/reel/ABC123xyz/?igsh=x&utm_source=ig_web"
    )
    assert bare == tracked


def test_non_instagram_url_rejected():
    with pytest.raises(IngestError):
        canonical_shortcode("https://tiktok.com/@a/video/123")


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data/",  # cloud metadata endpoint
        "http://localhost:8100/api/health",
        "http://127.0.0.1/",
        "https://evil.com/video.mp4",
        "https://cdninstagram.com.evil.com/x.mp4",  # suffix-confusion attempt
    ],
)
def test_ssrf_guard_blocks_non_cdn_hosts(url):
    assert _host_allowed(url) is False


def test_ssrf_guard_allows_meta_cdn():
    assert _host_allowed("https://scontent-lhr8-1.cdninstagram.com/v/t50/video.mp4")
    assert _host_allowed("https://video-lhr6-1.xx.fbcdn.net/v/t42/clip.mp4")


def test_credibility_prefers_longest_matching_domain():
    cred, lean = credibility.lookup("https://pubmed.ncbi.nlm.nih.gov/12345/")
    assert cred == 0.93  # the specific entry, not the broader nih.gov one


def test_unknown_publisher_gets_neutral_prior_not_a_penalty():
    cred, lean = credibility.lookup("https://some-local-newspaper.example/story")
    assert cred == credibility.NEUTRAL_PRIOR
    assert lean is None


def test_subdomains_inherit_the_parent_rating():
    assert credibility.lookup("https://www.bbc.co.uk/news/x")[0] == 0.85
    assert credibility.lookup("https://en.wikipedia.org/wiki/X")[0] == 0.75


def test_source_mix_lean_weights_by_credibility():
    lean, conf = credibility.source_mix_lean(
        ["https://breitbart.com/a", "https://dailywire.com/b", "https://foxnews.com/c"]
    )
    assert lean > 0.5
    assert 0 < conf <= 1


def test_source_mix_lean_is_unset_when_nothing_is_rated():
    lean, conf = credibility.source_mix_lean(["https://unknown.example/a"])
    assert lean is None
    assert conf == 0.0
