"""A live run on a real reel cited Facebook and Instagram posts as evidence.

That is circular: the claim under review came from exactly that kind of source.
Social posts and syndication aggregators are dropped before the model sees them,
so a claim supported by nothing else comes back unverifiable — the honest answer.
"""

import pytest

from app.evidence import credibility as cred


@pytest.mark.parametrize(
    "url",
    [
        "https://www.facebook.com/ndtv/posts/karnataka-news-protests",
        "https://www.instagram.com/p/DbOHxwXH4Fd/",
        "https://x.com/someone/status/1",
        "https://twitter.com/someone/status/1",
        "https://www.reddit.com/r/india/comments/x/",
        "https://www.youtube.com/watch?v=abc",
        "https://youtu.be/abc",
        "https://www.tiktok.com/@a/video/1",
        "https://www.quora.com/What-is-x",
        "https://t.me/somechannel/42",
    ],
)
def test_user_generated_platforms_are_never_citable(url):
    assert not cred.is_citable(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://www.bbc.com/news/articles/x",
        "https://www.nytimes.com/2026/07/18/world/asia/x.html",
        "https://frontline.thehindu.com/environment/x.ece",
        "https://en.wikipedia.org/wiki/Ladakh_protests",
        "https://doi.org/10.1038/x",
        "https://www.reuters.com/world/x",
        "https://timesofindia.indiatimes.com/city/raipur/x",
    ],
)
def test_real_outlets_and_primary_sources_stay_citable(url):
    assert cred.is_citable(url)


def test_syndication_aggregators_are_below_the_bar():
    """msn.com republishes other outlets, so the URL says nothing about who
    actually reported it — and it dominated the first live run."""
    assert not cred.is_citable("https://www.msn.com/en-in/news/India/x")
    assert cred.lookup("https://www.msn.com/en-in/news/India/x")[0] < 0.45


def test_unknown_publishers_remain_citable():
    """A neutral prior must not silently exclude every small outlet."""
    url = "https://some-local-newspaper.example/story"
    assert cred.lookup(url)[0] == cred.NEUTRAL_PRIOR
    assert cred.is_citable(url)


def test_subdomains_of_blocked_platforms_are_blocked():
    assert not cred.is_citable("https://m.facebook.com/x")
    assert not cred.is_citable("https://mobile.twitter.com/x")


def test_lookalike_domains_are_not_blocked():
    """Suffix matching must be on labels, not substrings."""
    assert cred.is_citable("https://facebook.com.example.org/article")
    assert cred.is_citable("https://notfacebook.com/article")


def test_ytdlp_prefers_the_username_over_the_numeric_id():
    """uploader_id is Instagram's numeric account id; using it as the handle
    fragments a creator's track record across ids."""
    import inspect

    from app.ingest.ytdlp import fetch_via_ytdlp  # noqa: F401

    src = inspect.getsource(fetch_via_ytdlp)
    channel_pos = src.index('info.get("channel")')
    uploader_id_pos = src.index('info.get("uploader_id")')
    assert channel_pos < uploader_id_pos, "channel must be preferred over uploader_id"
