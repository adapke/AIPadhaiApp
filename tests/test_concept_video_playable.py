"""prod-216 — concept-video URL playability guard.

The original prod-14 dev seed (build_concept_videos.py) inserted 21
`channel_seed` rows whose source_url is a `youtube.com/@Channel/search?query=…`
placeholder — curator TODO markers that can never embed or play. (The shipped
seed, data/concept_videos_seed.json, is 124 real verified videos and does NOT
contain these placeholders.) These tests pin the guard that:
  1. classifies a playable video URL vs a channel/search/playlist placeholder,
  2. makes the iframe health-check report placeholders honestly (no network),
  3. refuses to promote a non-playable URL to 'verified'.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from padhai import concept_videos as cv


@pytest.mark.parametrize("url", [
    "https://www.youtube.com/watch?v=CMiPYHNNg28",
    "https://youtu.be/CMiPYHNNg28",
    "https://www.youtube.com/embed/CMiPYHNNg28",
    "https://www.youtube.com/shorts/abcdefghijk",
    "https://example.com/lesson-video.mp4",  # non-YouTube: can't classify → playable
])
def test_playable_urls(url):
    assert cv.is_playable_video_url(url) is True


@pytest.mark.parametrize("url", [
    "https://www.youtube.com/@PeekabooKidz/search?query=gravity",
    "https://www.youtube.com/@crashcourse/search?query=water+cycle",
    "https://www.youtube.com/playlist?list=PLabc123",
    "https://www.youtube.com/@3blue1brown",
    "",
])
def test_non_playable_urls(url):
    assert cv.is_playable_video_url(url) is False


def test_iframe_check_short_circuits_placeholder():
    # A channel-search URL is on an allowlisted host but is not a video: the
    # check must report embeddable=False WITHOUT firing a network request.
    res = cv.check_iframe_embed(
        "https://www.youtube.com/@PeekabooKidz/search?query=gravity",
        timeout_sec=0.1,
    )
    assert res["embeddable"] is False
    assert "not a playable" in res["reason"].lower()


def test_cannot_verify_non_playable(tmp_path, monkeypatch):
    monkeypatch.setenv("PADHAI_DB_PATH", str(tmp_path / "cv.db"))
    monkeypatch.setenv("PADHAI_SKIP_DOTENV", "1")
    from padhai import db as _db
    importlib.reload(_db)
    importlib.reload(cv)

    placeholder = cv.upsert(
        concept="Gravity", source="youtube",
        source_url="https://www.youtube.com/@PeekabooKidz/search?query=gravity",
        title="Gravity (search placeholder)", quality_tier="channel_seed",
    )
    with pytest.raises(ValueError, match="non-playable"):
        cv.set_quality_tier(placeholder.id, "verified")

    # A real playable URL promotes to verified fine.
    good = cv.upsert(
        concept="Photosynthesis", source="youtube",
        source_url="https://www.youtube.com/watch?v=CMiPYHNNg28",
        title="Photosynthesis (UPDATED)", quality_tier="channel_seed",
    )
    out = cv.set_quality_tier(good.id, "verified")
    assert out is not None
    assert out.quality_tier == "verified"
