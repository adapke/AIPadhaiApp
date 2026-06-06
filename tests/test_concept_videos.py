"""prod-14 — Concept-video catalog regression tests.

Locks the contract for the embed-existing-content strategy:
schema + normalisation + search + the FastAPI endpoints.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture()
def temp_db(tmp_path, monkeypatch):
    """Pin concept_videos to a throwaway SQLite under tmp_path so
    tests don't touch the dev DB."""
    db = tmp_path / "cv_test.db"
    monkeypatch.setenv("PADHAI_DB_PATH", str(db))
    monkeypatch.setenv("PADHAI_SKIP_DOTENV", "1")
    monkeypatch.setenv(
        "PADHAI_JWT_SECRET",
        "test-secret-abcdef0123456789abcdef0123456789",
    )
    # Reload modules so the new env var takes effect
    import importlib

    from padhai import concept_videos as cv
    from padhai import db as _db
    importlib.reload(_db)
    importlib.reload(cv)
    yield db


def test_normalisation_strips_english_possessive():
    from padhai import concept_videos as cv
    assert cv._normalise_concept("Newton's First Law") == "newton first law"
    assert cv._normalise_concept("Newton’s First Law") == "newton first law"  # curly apostrophe
    assert cv._normalise_concept("Boyle's Law") == "boyle law"


def test_normalisation_lowercases_and_strips_punctuation():
    from padhai import concept_videos as cv
    assert cv._normalise_concept("Photosynthesis!") == "photosynthesis"
    assert cv._normalise_concept(" Cell Division ") == "cell division"
    assert cv._normalise_concept("a-b-c") == "a b c"  # hyphens become spaces


def test_normalisation_preserves_devanagari():
    """Hindi concept names use Devanagari (U+0900–U+097F). The
    normaliser must keep those code points intact so Hindi lookups
    work end-to-end."""
    from padhai import concept_videos as cv
    out = cv._normalise_concept("न्यूटन का गति का पहला नियम")
    assert out == "न्यूटन का गति का पहला नियम"


def test_derive_embed_url_handles_youtube_watch_form():
    from padhai import concept_videos as cv
    assert (
        cv._derive_embed_url("https://www.youtube.com/watch?v=adLj6kygwds")
        == "https://www.youtube.com/embed/adLj6kygwds"
    )
    assert (
        cv._derive_embed_url("https://youtu.be/dQw4w9WgXcQ")
        == "https://www.youtube.com/embed/dQw4w9WgXcQ"
    )


def test_derive_embed_url_passthrough_non_youtube():
    from padhai import concept_videos as cv
    u = "https://www.khanacademy.org/science/biology/foo"
    assert cv._derive_embed_url(u) == u


def test_upsert_and_search_roundtrip(temp_db):  # noqa: ARG001
    from padhai import concept_videos as cv
    v = cv.upsert(
        concept="Newton's First Law of Motion",
        source="youtube",
        source_url="https://www.youtube.com/watch?v=adLj6kygwds",
        title="What Is Newton's First Law Of Motion?",
        channel="Peekaboo Kidz",
        subject="physics",
        grade_min=6, grade_max=10,
        quality_tier="verified",
    )
    assert v.embed_url == "https://www.youtube.com/embed/adLj6kygwds"
    assert v.quality_tier == "verified"

    # Substring match in either direction
    rows = cv.search(concept="Newton First Law")
    assert len(rows) == 1
    assert rows[0].id == v.id


def test_search_grade_band_filter(temp_db):  # noqa: ARG001
    from padhai import concept_videos as cv
    cv.upsert(
        concept="Calculus",
        source="youtube",
        source_url="https://www.youtube.com/watch?v=aaa1234567a",
        title="x", channel="3Blue1Brown",
        grade_min=11, grade_max=12,
    )
    cv.upsert(
        concept="Counting",
        source="youtube",
        source_url="https://www.youtube.com/watch?v=bbb1234567a",
        title="y", channel="Peekaboo Kidz",
        grade_min=1, grade_max=4,
    )
    # Grade 12 should get Calculus, not Counting
    rows12 = cv.search(grade=12)
    assert {r.concept for r in rows12} == {"Calculus"}
    # Grade 2 should get Counting, not Calculus
    rows2 = cv.search(grade=2)
    assert {r.concept for r in rows2} == {"Counting"}


def test_quality_tier_validation(temp_db):  # noqa: ARG001
    from padhai import concept_videos as cv
    with pytest.raises(ValueError, match="quality_tier"):
        cv.upsert(
            concept="x", source="youtube",
            source_url="https://www.youtube.com/watch?v=zzz1234567a",
            title="x",
            quality_tier="bogus_tier",
        )


def test_source_validation(temp_db):  # noqa: ARG001
    from padhai import concept_videos as cv
    with pytest.raises(ValueError, match="source must be"):
        cv.upsert(
            concept="x", source="tiktok",
            source_url="https://tiktok.com/foo",
            title="x",
        )


def test_idempotent_upsert(temp_db):  # noqa: ARG001
    """Re-upserting the same natural key updates the row rather
    than inserting a duplicate."""
    from padhai import concept_videos as cv
    v1 = cv.upsert(
        concept="X", source="youtube",
        source_url="https://www.youtube.com/watch?v=ccc1234567a",
        title="first title",
    )
    v2 = cv.upsert(
        concept="X", source="youtube",
        source_url="https://www.youtube.com/watch?v=ccc1234567a",
        title="updated title",
    )
    assert cv.stats()["total"] == 1
    assert v2.title == "updated title"
    assert v2.id == v1.id


def test_http_endpoints_return_curated_videos(temp_db):  # noqa: ARG001
    """End-to-end: seed the catalog via the builder script, then
    hit the FastAPI endpoints and confirm the responses."""
    from padhai import concept_videos as cv
    from padhai.web import app
    from scripts.build_concept_videos import CATALOG
    cv.bulk_load(CATALOG)

    client = TestClient(app)

    # stats
    r = client.get("/api/concept-videos/stats")
    assert r.status_code == 200
    data = r.json()
    assert data["total"] >= len(CATALOG)
    assert "verified" in data["by_quality_tier"]
    assert "channel_seed" in data["by_quality_tier"]

    # search by concept name
    r = client.get("/api/concept-videos?concept=Newton First Law")
    assert r.status_code == 200
    rows = r.json()["rows"]
    assert any(
        "Newton's First Law" in v["concept"] for v in rows
    ), rows

    # search filtered by subject + grade
    r = client.get("/api/concept-videos?subject=physics&grade=9")
    assert r.status_code == 200
    assert r.json()["count"] >= 1

    # get-by-id
    verified = [
        v for v in client.get(
            "/api/concept-videos?quality_tier=verified",
        ).json()["rows"]
    ]
    assert verified, "no verified videos in catalog"
    one = verified[0]
    r = client.get(f"/api/concept-videos/{one['id']}")
    assert r.status_code == 200
    assert r.json()["id"] == one["id"]

    # unknown id → 404
    r = client.get("/api/concept-videos/nonexistent-id-xxx")
    assert r.status_code == 404


def test_seed_catalog_quality_distribution():
    """prod-14 — surface the honest gap: most catalog rows are
    channel_seed (curator needs to confirm specific URLs). If
    a future PR shifts the ratio drastically, surface it."""
    from scripts.build_concept_videos import CATALOG
    verified = sum(1 for r in CATALOG if r.get("quality_tier") == "verified")
    seeded = sum(1 for r in CATALOG if r.get("quality_tier") == "channel_seed")
    assert verified >= 1, "at least 1 verified seed row required"
    assert seeded >= 10, "channel_seed coverage too thin"
    # The Peekaboo Newton URL the user shared MUST stay verified.
    pkb = next(
        (r for r in CATALOG
         if "Newton" in r["concept"] and r.get("quality_tier") == "verified"),
        None,
    )
    assert pkb is not None, (
        "the user-confirmed Peekaboo Newton's First Law row "
        "must stay as 'verified'"
    )
    assert "adLj6kygwds" in pkb["source_url"], (
        "verified Peekaboo URL changed — was the user's confirmed one"
    )
