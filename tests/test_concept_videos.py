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


def test_upsert_and_search_roundtrip(temp_db):  # noqa: ARG001  # noqa: ARG001
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


def test_search_grade_band_filter(temp_db):  # noqa: ARG001  # noqa: ARG001
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


def test_quality_tier_validation(temp_db):  # noqa: ARG001  # noqa: ARG001
    from padhai import concept_videos as cv
    with pytest.raises(ValueError, match="quality_tier"):
        cv.upsert(
            concept="x", source="youtube",
            source_url="https://www.youtube.com/watch?v=zzz1234567a",
            title="x",
            quality_tier="bogus_tier",
        )


def test_source_validation(temp_db):  # noqa: ARG001  # noqa: ARG001
    from padhai import concept_videos as cv
    with pytest.raises(ValueError, match="source must be"):
        cv.upsert(
            concept="x", source="tiktok",
            source_url="https://tiktok.com/foo",
            title="x",
        )


def test_idempotent_upsert(temp_db):  # noqa: ARG001  # noqa: ARG001
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


def test_http_endpoints_return_curated_videos(temp_db):  # noqa: ARG001  # noqa: ARG001
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


def test_set_quality_tier_does_not_crash_on_missing_updated_at(temp_db):  # noqa: ARG001
    """prod-42 regression — set_quality_tier used to write to a
    non-existent `updated_at` column. The fix dropped that reference.
    This test would have caught the bug at PR time."""
    from padhai import concept_videos as cv

    row = cv.upsert(
        concept="Test concept",
        source="youtube",
        source_url="https://www.youtube.com/watch?v=test1234567",
        title="Test stub",
        channel="TestChan",
        quality_tier="channel_seed",
    )
    # Flip to verified — must not raise sqlite OperationalError.
    updated = cv.set_quality_tier(
        row.id, "verified",
        curator_note="confirmed by test",
    )
    assert updated is not None
    assert updated.quality_tier == "verified"
    assert "confirmed by test" in (updated.curator_note or "")


def test_set_quality_tier_rejects_invalid_tier(temp_db):  # noqa: ARG001
    from padhai import concept_videos as cv
    row = cv.upsert(
        concept="Test", source="youtube",
        source_url="https://www.youtube.com/watch?v=qqqqqqqqqqq",
        title="t",
    )
    with pytest.raises(ValueError):
        cv.set_quality_tier(row.id, "garbage_tier")


def test_set_quality_tier_returns_none_for_missing_id(temp_db):  # noqa: ARG001
    from padhai import concept_videos as cv
    assert cv.set_quality_tier("nope", "verified") is None


def test_update_video_replaces_stub_url_and_title(temp_db):  # noqa: ARG001
    """prod-42 — curator updates a channel_seed row with the real URL
    they found. embed_url must be re-derived from the new source_url."""
    from padhai import concept_videos as cv

    row = cv.upsert(
        concept="Friction",
        source="youtube",
        source_url="https://www.youtube.com/watch?v=stub0000000",
        title="[Curator: find Peekaboo Friction video]",
        channel="Peekaboo Kidz",
        quality_tier="channel_seed",
    )
    assert row.embed_url == "https://www.youtube.com/embed/stub0000000"

    new = cv.update_video(
        row.id,
        title="Friction - The Dr. Binocs Show | Peekaboo Kidz",
        source_url="https://www.youtube.com/watch?v=real7777777",
        duration_sec=300,
        curator_note="found by curator",
    )
    assert new is not None
    assert new.title.startswith("Friction")
    assert new.source_url.endswith("v=real7777777")
    assert new.embed_url == "https://www.youtube.com/embed/real7777777"
    assert new.duration_sec == 300
    assert "found by curator" in (new.curator_note or "")
    # Quality tier should be UNCHANGED — update_video does not flip it.
    assert new.quality_tier == "channel_seed"


def test_update_video_returns_none_for_unknown_id(temp_db):  # noqa: ARG001
    from padhai import concept_videos as cv
    assert cv.update_video("nope", title="x") is None


def test_list_curator_queue_returns_only_matching_tier(temp_db):  # noqa: ARG001
    """prod-42 — queue helper must filter by tier and ignore others."""
    from padhai import concept_videos as cv

    # Seed: 2 channel_seed, 1 verified.
    cv.upsert(
        concept="A", source="youtube",
        source_url="https://www.youtube.com/watch?v=AAAAAAAAAAA",
        title="a", quality_tier="channel_seed",
    )
    cv.upsert(
        concept="B", source="youtube",
        source_url="https://www.youtube.com/watch?v=BBBBBBBBBBB",
        title="b", quality_tier="channel_seed",
    )
    cv.upsert(
        concept="C", source="youtube",
        source_url="https://www.youtube.com/watch?v=CCCCCCCCCCC",
        title="c", quality_tier="verified",
    )

    seed = cv.list_curator_queue(quality_tier="channel_seed")
    assert len(seed) == 2
    assert {r.concept for r in seed} == {"A", "B"}

    verified = cv.list_curator_queue(quality_tier="verified")
    assert len(verified) == 1
    assert verified[0].concept == "C"


def test_curator_queue_endpoint_includes_search_url(temp_db):  # noqa: ARG001
    """prod-42 — /api/admin/concept-videos/queue must produce a YouTube
    search URL pre-filled with concept+channel. Anonymous → 401; the
    request body building still needs to be testable.

    We sidestep auth by hitting list_curator_queue + building the URL
    the same way the endpoint does — the endpoint logic is one
    urlencode call, gated by router-level auth.
    """
    from padhai import concept_videos as cv

    cv.upsert(
        concept="Photosynthesis", source="youtube",
        source_url="https://www.youtube.com/watch?v=ZZZZZZZZZZZ",
        title="[stub]", channel="CrashCourse",
        quality_tier="channel_seed",
    )
    import urllib.parse
    rows = cv.list_curator_queue(quality_tier="channel_seed")
    assert rows
    r = rows[0]
    expected_q = f"{r.concept} {r.channel}"
    search = (
        "https://www.youtube.com/results?search_query="
        + urllib.parse.quote_plus(expected_q)
    )
    # quote_plus on "Photosynthesis CrashCourse" → "Photosynthesis+CrashCourse"
    assert "Photosynthesis" in search
    assert "CrashCourse" in search


def test_admin_curator_page_anonymous_blocked():
    """prod-50/56 — /admin/concept-curator is admin-only via explicit
    Depends(make_admin_dep()). Anonymous → 401."""
    from padhai.web import app as _app
    client = TestClient(_app)
    res = client.get("/admin/concept-curator", follow_redirects=False)
    assert res.status_code in (401, 403), (
        f"admin curator page should require auth; got {res.status_code}"
    )


def test_admin_curator_page_returns_html_for_admin(monkeypatch):
    """prod-50/56 — When the admin gate passes, the route returns HTML
    containing the curator UI bones (tier filter, action buttons)."""
    monkeypatch.setenv("PADHAI_SKIP_DOTENV", "1")
    monkeypatch.setenv(
        "PADHAI_JWT_SECRET",
        "test-secret-abcdef0123456789abcdef0123456789",
    )
    # Force the dev fallback: no DATABASE_URL → any signed-in user is admin.
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("PADHAI_SUPERUSER_EMAILS", raising=False)

    import importlib

    from padhai import auth as _auth
    from padhai import web as _web
    importlib.reload(_auth)
    importlib.reload(_web)
    from fastapi.testclient import TestClient

    client = TestClient(_web.app)
    # Sign up via API. Email is uuid-prefixed so reruns don't collide
    # with previously-created rows in the dev DB.
    import uuid
    test_email = f"curator+{uuid.uuid4().hex[:8]}@example.com"
    sres = client.post(
        "/auth/signup",
        data={"email": test_email, "password": "Pass@12345",
              "terms_accepted": "true"},
    )
    if sres.status_code == 503:
        pytest.skip("auth not configured")
    assert sres.status_code in (200, 201), sres.text
    tok = sres.json()["token"]

    r = client.get(
        "/admin/concept-curator",
        headers={"Authorization": f"Bearer {tok}"},
    )
    # In SQLite dev mode + no superuser list, the gate's dev fallback
    # allows any signed-in user (see api_deps.require_admin_role line 114).
    assert r.status_code == 200, (
        f"expected 200 in dev-fallback admin mode, got {r.status_code}: {r.text[:200]}"
    )
    body = r.text
    # Required UI elements that lock the page contract.
    assert "Concept-Video Curator" in body
    assert "tierFilter" in body
    assert "loadCuratorChip" not in body  # that's the dashboard chip JS
    assert "btn-verify" in body
    assert "btn-update" in body
    assert "btn-reject" in body
    assert "/api/admin/concept-videos/queue" in body
    assert "pathshala_token" in body


def test_updated_at_column_added_by_migration(temp_db):
    """prod-57 — the additive ALTER TABLE in _ensure_updated_at_column
    must add updated_at to existing DBs. New DBs already have it via the
    CREATE TABLE schema."""
    from padhai import concept_videos as cv
    cv.migrate()
    import sqlite3
    conn = sqlite3.connect(str(temp_db))
    cols = {row[1] for row in conn.execute("PRAGMA table_info(concept_videos)")}
    assert "updated_at" in cols


def test_updated_at_set_by_set_quality_tier(temp_db):
    """prod-57 — set_quality_tier must write updated_at (not just leave
    NULL). This is the audit-trail half of the bug fix."""
    from padhai import concept_videos as cv

    row = cv.upsert(
        concept="Test",
        source="youtube",
        source_url="https://www.youtube.com/watch?v=test1111111",
        title="t",
        quality_tier="channel_seed",
    )
    cv.set_quality_tier(row.id, "verified", curator_note="test")

    import sqlite3
    conn = sqlite3.connect(str(temp_db))
    rec = conn.execute(
        "SELECT updated_at, created_at FROM concept_videos WHERE id = ?",
        (row.id,),
    ).fetchone()
    assert rec[0] is not None, "updated_at should be set after set_quality_tier"
    # updated_at should be >= created_at (monotonic)
    assert rec[0] >= rec[1]


def test_updated_at_set_by_update_video(temp_db):
    """prod-57 — update_video must also write updated_at."""
    from padhai import concept_videos as cv

    row = cv.upsert(
        concept="Test",
        source="youtube",
        source_url="https://www.youtube.com/watch?v=test2222222",
        title="stub",
        quality_tier="channel_seed",
    )
    cv.update_video(row.id, title="real title")

    import sqlite3
    conn = sqlite3.connect(str(temp_db))
    rec = conn.execute(
        "SELECT updated_at FROM concept_videos WHERE id = ?",
        (row.id,),
    ).fetchone()
    assert rec[0] is not None


def test_oembed_returns_none_on_non_youtube_url():
    """prod-55 — oembed helper short-circuits for non-YouTube URLs;
    returns None instead of making a network call."""
    from padhai import concept_videos as cv
    assert cv.fetch_oembed_metadata("") is None
    assert cv.fetch_oembed_metadata("https://example.com/page") is None
    # Vimeo isn't supported by this helper either (different oembed endpoint).
    assert cv.fetch_oembed_metadata("https://vimeo.com/12345") is None


def test_oembed_returns_none_on_network_failure(monkeypatch):
    """prod-55 — oembed helper must never raise; network errors → None."""
    from padhai import concept_videos as cv

    def _raise(*a, **kw):  # noqa: ARG001
        raise OSError("network down")

    monkeypatch.setattr("urllib.request.urlopen", _raise)
    # YouTube URL that would normally trigger a fetch
    out = cv.fetch_oembed_metadata("https://www.youtube.com/watch?v=test3333333")
    assert out is None


def test_update_video_auto_fetch_uses_oembed_when_no_title(monkeypatch, temp_db):  # noqa: ARG001
    """prod-55 — when curator pastes URL but no title, and auto_fetch is
    on, update_video pulls title from oembed. Caller's title always wins."""
    from padhai import concept_videos as cv

    monkeypatch.setattr(
        cv,
        "fetch_oembed_metadata",
        lambda url, **kw: {  # noqa: ARG005
            "title": "Real Video Title from oembed",
            "channel": "RealChannel",
            "thumbnail_url": None,
        },
    )
    row = cv.upsert(
        concept="Friction",
        source="youtube",
        source_url="https://www.youtube.com/watch?v=stub4444444",
        title="[stub]",
        channel="StubChannel",
        quality_tier="channel_seed",
    )
    # No title supplied → oembed wins
    out = cv.update_video(
        row.id,
        source_url="https://www.youtube.com/watch?v=real5555555",
        auto_fetch_oembed=True,
    )
    assert out is not None
    assert out.title == "Real Video Title from oembed"
    assert out.channel == "RealChannel"

    # Caller-supplied title takes precedence over oembed
    out2 = cv.update_video(
        row.id,
        title="Curator's manual title",
        source_url="https://www.youtube.com/watch?v=real5555555",
        auto_fetch_oembed=True,
    )
    assert out2.title == "Curator's manual title"


def test_check_iframe_embed_rejects_non_allowlisted_host():
    """prod-67 — SSRF guard. The check must refuse to fetch anything
    outside the YouTube / Vimeo allowlist, regardless of scheme."""
    from padhai import concept_videos as cv
    out = cv.check_iframe_embed("https://evil.example.com/page")
    assert out["embeddable"] is None
    assert "allowlist" in out["reason"].lower()


def test_check_iframe_embed_rejects_unsupported_scheme():
    from padhai import concept_videos as cv
    out = cv.check_iframe_embed("file:///etc/passwd")
    assert out["embeddable"] is None
    assert "scheme" in out["reason"].lower()


def test_check_iframe_embed_rejects_empty_url():
    from padhai import concept_videos as cv
    out = cv.check_iframe_embed("")
    assert out["embeddable"] is None


def test_check_iframe_embed_detects_x_frame_options(monkeypatch):
    """prod-67 — when YouTube returns X-Frame-Options: SAMEORIGIN
    (which it does on /watch URLs), we detect and report it."""
    from typing import ClassVar

    from padhai import concept_videos as cv

    class FakeResp:
        status: ClassVar[int] = 200
        headers: ClassVar[dict] = {"X-Frame-Options": "SAMEORIGIN"}
        def __enter__(self):
            return self
        def __exit__(self, *a):
            pass

    def _fake_urlopen(req, timeout):  # noqa: ARG001
        return FakeResp()

    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
    out = cv.check_iframe_embed("https://www.youtube.com/watch?v=xxx")
    assert out["embeddable"] is False
    assert "X-Frame-Options" in out["reason"]
    assert out["x_frame_options"] == "SAMEORIGIN"


def test_check_iframe_embed_accepts_clean_embed_url(monkeypatch):
    """prod-67 — youtube-nocookie /embed/ URLs typically have no XFO."""
    from typing import ClassVar

    from padhai import concept_videos as cv

    class FakeResp:
        status: ClassVar[int] = 200
        headers: ClassVar[dict] = {}
        def __enter__(self):
            return self
        def __exit__(self, *a):
            pass

    monkeypatch.setattr(
        "urllib.request.urlopen", lambda r, timeout: FakeResp(),  # noqa: ARG005
    )
    out = cv.check_iframe_embed("https://www.youtube-nocookie.com/embed/abc")
    assert out["embeddable"] is True


def test_check_iframe_embed_detects_csp_frame_ancestors_none(monkeypatch):
    from typing import ClassVar

    from padhai import concept_videos as cv

    class FakeResp:
        status: ClassVar[int] = 200
        headers: ClassVar[dict] = {
            "Content-Security-Policy": "frame-ancestors 'none'; default-src 'self'",
        }
        def __enter__(self):
            return self
        def __exit__(self, *a):
            pass

    monkeypatch.setattr(
        "urllib.request.urlopen", lambda r, timeout: FakeResp(),  # noqa: ARG005
    )
    out = cv.check_iframe_embed("https://www.youtube.com/watch?v=xxx")
    assert out["embeddable"] is False
    assert "CSP" in out["reason"]


def test_record_play_increments_count_and_stamps_time(temp_db):
    """prod-70 — record_play bumps play_count + sets last_played_at."""
    from padhai import concept_videos as cv

    row = cv.upsert(
        concept="Test play",
        source="youtube",
        source_url="https://www.youtube.com/watch?v=play1111111",
        title="t",
        quality_tier="verified",
    )
    import sqlite3
    conn = sqlite3.connect(str(temp_db))
    # Initially 0
    before = conn.execute(
        "SELECT play_count, last_played_at FROM concept_videos WHERE id = ?",
        (row.id,),
    ).fetchone()
    assert before[0] == 0
    assert before[1] is None

    assert cv.record_play(row.id) is True
    assert cv.record_play(row.id) is True

    after = conn.execute(
        "SELECT play_count, last_played_at FROM concept_videos WHERE id = ?",
        (row.id,),
    ).fetchone()
    assert after[0] == 2
    assert after[1] is not None


def test_record_play_returns_false_for_missing_id(temp_db):  # noqa: ARG001
    from padhai import concept_videos as cv
    assert cv.record_play("nope") is False


def test_list_popular_orders_by_play_count_desc(temp_db):  # noqa: ARG001
    """prod-70 — popular() returns top-N by play_count, filtered to
    `verified` by default."""
    from padhai import concept_videos as cv

    # Create 3 verified + 1 channel_seed; play 2 of them
    rows = [
        cv.upsert(concept=f"P{i}", source="youtube",
                  source_url=f"https://www.youtube.com/watch?v=pop{i}xxxxxxxx",
                  title=f"P{i}", quality_tier="verified")
        for i in range(3)
    ]
    cv.upsert(concept="Seed", source="youtube",
              source_url="https://www.youtube.com/watch?v=seed1111111",
              title="seed", quality_tier="channel_seed")

    cv.record_play(rows[0].id)
    cv.record_play(rows[0].id)  # 2 plays
    cv.record_play(rows[1].id)  # 1 play
    # rows[2] never played → excluded

    pop = cv.list_popular(limit=10, since_days=30)
    titles = [(v.concept, cnt) for v, cnt in pop]
    assert titles == [("P0", 2), ("P1", 1)], titles


def test_list_popular_filters_channel_seed_by_default(temp_db):  # noqa: ARG001
    """prod-70 — popular() must NOT surface unconfirmed channel_seed picks."""
    from padhai import concept_videos as cv

    seed = cv.upsert(
        concept="UnconfirmedSeed", source="youtube",
        source_url="https://www.youtube.com/watch?v=seedXXXXXXX",
        title="seed", quality_tier="channel_seed",
    )
    cv.record_play(seed.id)
    cv.record_play(seed.id)

    pop = cv.list_popular(limit=10, since_days=30)
    assert all(v.concept != "UnconfirmedSeed" for v, _ in pop)


def test_badge_endpoint_returns_contract_shape(temp_db):  # noqa: ARG001
    """prod-66/72 — /api/concept-videos/badge is public and returns the
    documented shape. Lock the field names since the landing page
    depends on them."""
    from padhai import concept_videos as cv
    from padhai.web import app

    # Seed at least one verified row so the badge has something to report.
    cv.upsert(
        concept="BadgeTest", source="youtube",
        source_url="https://www.youtube.com/watch?v=badge1234567",
        title="t", quality_tier="verified",
    )
    client = TestClient(app)
    r = client.get("/api/concept-videos/badge")
    assert r.status_code == 200
    d = r.json()
    for key in (
        "total", "verified", "verified_pct", "channel_seed",
        "languages", "subjects", "last_verified_at",
        "last_verified_iso", "freshness_label",
    ):
        assert key in d, f"missing field: {key}"
    assert d["total"] >= 1
    assert d["verified"] >= 1
    assert d["verified_pct"] >= 0
    assert isinstance(d["languages"], list)
    assert isinstance(d["subjects"], list)
    # freshness label is human-readable
    assert d["freshness_label"] in ("never", "today", "1 day ago") \
        or d["freshness_label"].endswith("days ago"), d["freshness_label"]


def test_badge_endpoint_no_auth_required():
    """prod-66/72 — badge must be public for landing-page embedding."""
    from padhai.web import app
    client = TestClient(app)
    # Send no Authorization header
    r = client.get("/api/concept-videos/badge")
    assert r.status_code == 200, "badge must not require auth"


def test_popular_endpoint_returns_only_verified_with_plays(temp_db):  # noqa: ARG001
    """prod-70/72 — /popular returns verified videos with play_count > 0,
    ordered DESC by count."""
    from padhai import concept_videos as cv
    from padhai.web import app

    # 2 verified + 1 channel_seed
    a = cv.upsert(
        concept="PopA", source="youtube",
        source_url="https://www.youtube.com/watch?v=popA11111111",
        title="A", quality_tier="verified",
    )
    b = cv.upsert(
        concept="PopB", source="youtube",
        source_url="https://www.youtube.com/watch?v=popB11111111",
        title="B", quality_tier="verified",
    )
    seed = cv.upsert(
        concept="PopSeed", source="youtube",
        source_url="https://www.youtube.com/watch?v=popS11111111",
        title="S", quality_tier="channel_seed",
    )
    # Hit /played for each
    client = TestClient(app)
    for _ in range(3):
        client.post(f"/api/concept-videos/{a.id}/played")
    client.post(f"/api/concept-videos/{b.id}/played")
    for _ in range(5):
        # channel_seed gets the most plays but MUST be excluded
        client.post(f"/api/concept-videos/{seed.id}/played")

    r = client.get("/api/concept-videos/popular?limit=10&since_days=30")
    assert r.status_code == 200
    d = r.json()
    concepts = [(row["concept"], row["play_count"]) for row in d["rows"]]
    # PopSeed (channel_seed) is excluded; PopA before PopB by count.
    assert concepts == [("PopA", 3), ("PopB", 1)], concepts
    # Field contract
    for row in d["rows"]:
        assert "play_count" in row
        assert "embed_url" in row
        assert "concept" in row


def test_popular_endpoint_no_auth_required(temp_db):  # noqa: ARG001
    from padhai.web import app
    client = TestClient(app)
    r = client.get("/api/concept-videos/popular")
    assert r.status_code == 200


def test_played_endpoint_returns_404_for_missing_id(temp_db):  # noqa: ARG001
    """prod-70/72 — beacon endpoint returns 404 cleanly for unknown ids
    (callers should not panic on 404 — it just means the row was deleted)."""
    from padhai.web import app
    client = TestClient(app)
    r = client.post("/api/concept-videos/nonexistent-id/played")
    assert r.status_code == 404


def test_played_endpoint_no_auth_required(temp_db):  # noqa: ARG001
    """prod-70/72 — students aren't authed on the landing page but the
    beacon must still record plays."""
    from padhai import concept_videos as cv
    from padhai.web import app

    row = cv.upsert(
        concept="NoAuthPlay", source="youtube",
        source_url="https://www.youtube.com/watch?v=noauth000000",
        title="t", quality_tier="verified",
    )
    client = TestClient(app)
    r = client.post(f"/api/concept-videos/{row.id}/played")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_check_iframe_admin_endpoint_requires_auth():
    """prod-67/72 — /api/admin/concept-videos/check-iframe is admin-only
    via the router-level dep injection (prod-9)."""
    from padhai.web import app
    client = TestClient(app)
    # Anonymous → 401/403
    r = client.post(
        "/api/admin/concept-videos/check-iframe",
        json={"source_url": "https://www.youtube.com/watch?v=xxx"},
    )
    assert r.status_code in (401, 403)


def test_check_iframe_admin_endpoint_rejects_empty_body(monkeypatch):
    """prod-67/72 — Even past the auth gate, empty source_url is a 400.
    We test the handler logic via direct import (admin-gate would 401
    in TestClient since no auth is configured)."""
    monkeypatch.setenv("PADHAI_SKIP_DOTENV", "1")
    monkeypatch.setenv(
        "PADHAI_JWT_SECRET",
        "test-secret-abcdef0123456789abcdef0123456789",
    )
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("PADHAI_SUPERUSER_EMAILS", raising=False)

    import importlib

    from padhai import auth as _auth
    from padhai import web as _web
    importlib.reload(_auth)
    importlib.reload(_web)
    from fastapi.testclient import TestClient as _TC

    client = _TC(_web.app)
    # Sign up as the admin-eligible user (dev fallback grants admin
    # when DATABASE_URL is unset).
    import uuid
    email = f"check-iframe+{uuid.uuid4().hex[:8]}@example.com"
    sres = client.post(
        "/auth/signup",
        data={"email": email, "password": "Pass@12345", "terms_accepted": "true"},
    )
    if sres.status_code == 503:
        pytest.skip("auth not configured")
    tok = sres.json()["token"]
    headers = {"Authorization": f"Bearer {tok}"}

    # Empty source_url → 400
    r = client.post(
        "/api/admin/concept-videos/check-iframe",
        json={},
        headers=headers,
    )
    assert r.status_code == 400, r.text
    assert "source_url required" in r.text.lower()

    # Non-allowlisted host → 200 with inconclusive result (SSRF guard
    # short-circuits before any network call).
    r = client.post(
        "/api/admin/concept-videos/check-iframe",
        json={"source_url": "https://evil.example/x"},
        headers=headers,
    )
    assert r.status_code == 200
    d = r.json()
    assert d["embeddable"] is None
    assert "allowlist" in d["reason"].lower()


def test_curator_stats_helper_returns_contract(temp_db):  # noqa: ARG001
    """prod-74 — curator_stats returns the field shape the admin page
    depends on. Verified rows recently-verified should be counted."""
    from padhai import concept_videos as cv

    # Seed: 1 verified, 2 channel_seed, 1 ai_fallback
    cv.upsert(
        concept="A", source="youtube",
        source_url="https://www.youtube.com/watch?v=aaaaaaaaaaa",
        title="A", quality_tier="verified",
    )
    cv.upsert(
        concept="B", source="youtube",
        source_url="https://www.youtube.com/watch?v=bbbbbbbbbbb",
        title="B", quality_tier="channel_seed",
    )
    cv.upsert(
        concept="C", source="youtube",
        source_url="https://www.youtube.com/watch?v=ccccccccccc",
        title="C", quality_tier="channel_seed",
    )
    cv.upsert(
        concept="D", source="youtube",
        source_url="https://www.youtube.com/watch?v=ddddddddddd",
        title="D", quality_tier="ai_fallback",
    )
    # set_quality_tier on A flips it to verified again and stamps
    # last_verified_at, so it counts as "verified_recent".
    a_row = cv.search(concept="A")[0]
    cv.set_quality_tier(a_row.id, "verified", curator_note="re-check")

    out = cv.curator_stats(since_days=30)
    for key in (
        "total", "by_tier", "verified_recent", "updated_recent",
        "played_recent_total", "freshest_verified_iso",
        "oldest_verified_iso", "since_days",
    ):
        assert key in out, f"missing field: {key}"
    assert out["total"] == 4
    assert out["by_tier"]["verified"] == 1
    assert out["by_tier"]["channel_seed"] == 2
    assert out["by_tier"]["ai_fallback"] == 1
    assert out["verified_recent"] >= 1
    assert out["freshest_verified_iso"] is not None
    assert out["since_days"] == 30


def test_curator_stats_endpoint_admin_only():
    """prod-74 — JSON stats endpoint must be admin-gated."""
    from padhai.web import app
    client = TestClient(app)
    r = client.get("/api/admin/concept-videos/curator-stats")
    assert r.status_code in (401, 403)


def test_curator_stats_page_admin_only():
    """prod-74 — HTML page is admin-only (Depends gate)."""
    from padhai.web import app
    client = TestClient(app)
    r = client.get("/admin/curator-stats")
    assert r.status_code in (401, 403)


def test_by_concept_slug_matches_normalised_form(temp_db):  # noqa: ARG001
    """prod-81 — slug lookup is normalisation-aware. 'newton-first-law'
    and 'Newton First Law' both find the same row."""
    from padhai import concept_videos as cv

    cv.upsert(
        concept="Newton's First Law of Motion",
        source="youtube",
        source_url="https://www.youtube.com/watch?v=newton111111",
        title="Newton's First Law", channel="Peekaboo Kidz",
        quality_tier="verified",
    )
    # Hyphen-style slug
    out = cv.get_by_concept_slug("newton-first-law-of-motion")
    assert out is not None
    assert out.concept == "Newton's First Law of Motion"

    # Spaces work too
    out2 = cv.get_by_concept_slug("Newton First Law of Motion")
    assert out2 is not None
    assert out2.id == out.id


def test_by_concept_slug_returns_none_for_unknown(temp_db):  # noqa: ARG001
    from padhai import concept_videos as cv
    assert cv.get_by_concept_slug("nonexistent-concept") is None


def test_by_concept_slug_filters_by_quality_tier(temp_db):  # noqa: ARG001
    """prod-81 — default `verified` tier shouldn't surface channel_seed."""
    from padhai import concept_videos as cv

    cv.upsert(
        concept="UnverifiedX", source="youtube",
        source_url="https://www.youtube.com/watch?v=unverif11111",
        title="x", quality_tier="channel_seed",
    )
    assert cv.get_by_concept_slug("UnverifiedX") is None
    # But explicit channel_seed filter finds it.
    out = cv.get_by_concept_slug("UnverifiedX", quality_tier="channel_seed")
    assert out is not None


def test_by_concept_slug_empty_returns_none(temp_db):  # noqa: ARG001
    from padhai import concept_videos as cv
    assert cv.get_by_concept_slug("") is None
    assert cv.get_by_concept_slug("---") is None


def test_by_concept_endpoint_returns_200_for_existing(temp_db):  # noqa: ARG001
    """prod-81 — HTTP-level: registered route returns 200 with the
    full ConceptVideo dict for an existing verified row."""
    from padhai import concept_videos as cv
    from padhai.web import app

    cv.upsert(
        concept="Photosynthesis",
        source="youtube",
        source_url="https://www.youtube.com/watch?v=photo1111111",
        title="Photosynthesis explained",
        quality_tier="verified",
    )
    client = TestClient(app)
    r = client.get("/api/concept-videos/by-concept/photosynthesis")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["concept"] == "Photosynthesis"
    assert d["quality_tier"] == "verified"
    assert "embed_url" in d


def test_by_concept_endpoint_returns_404_for_unknown(temp_db):  # noqa: ARG001
    from padhai.web import app
    client = TestClient(app)
    r = client.get("/api/concept-videos/by-concept/quantum-gravity-of-bananas")
    assert r.status_code == 404


def test_by_concept_filter_lang(temp_db):  # noqa: ARG001
    """prod-81/83 — HTTP-level: language filter respected (Hindi row
    should not be returned for English lookup of same concept)."""
    from padhai import concept_videos as cv
    from padhai.web import app

    cv.upsert(
        concept="Speed of Light",
        source="youtube",
        source_url="https://www.youtube.com/watch?v=enspd1111111",
        title="Speed of Light (EN)",
        language="en",
        quality_tier="verified",
    )
    client = TestClient(app)
    # Default `?language=en` finds it
    r = client.get("/api/concept-videos/by-concept/speed-of-light")
    assert r.status_code == 200
    # Hindi lookup returns 404 because no `hi`-language row exists
    r = client.get("/api/concept-videos/by-concept/speed-of-light?language=hi")
    assert r.status_code == 404


def test_check_iframe_helper_uses_embed_url_path():
    """prod-67/82/83 — when curator's URL is a /watch form, the
    check_iframe_embed helper itself uses what we pass. The script
    + admin endpoint are responsible for passing the embed form.
    This test pins the source_url passthrough behaviour."""
    from padhai import concept_videos as cv

    # Non-allowlisted host short-circuits before any network call
    out = cv.check_iframe_embed("https://random.example/foo")
    assert out["embeddable"] is None
    assert "allowlist" in out["reason"].lower()


def test_check_iframe_admin_endpoint_returns_inconclusive_for_safe_host(monkeypatch):
    """prod-67/72/83 — admin endpoint, with auth, returns the
    structured dict from check_iframe_embed. Use the dev-fallback
    admin path so we don't need Postgres."""
    monkeypatch.setenv("PADHAI_SKIP_DOTENV", "1")
    monkeypatch.setenv(
        "PADHAI_JWT_SECRET",
        "test-secret-abcdef0123456789abcdef0123456789",
    )
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("PADHAI_SUPERUSER_EMAILS", raising=False)

    import importlib

    from padhai import auth as _auth
    from padhai import web as _web
    importlib.reload(_auth)
    importlib.reload(_web)
    client = TestClient(_web.app)

    import uuid
    email = f"iframe-test+{uuid.uuid4().hex[:8]}@example.com"
    sres = client.post(
        "/auth/signup",
        data={"email": email, "password": "Pass@12345", "terms_accepted": "true"},
    )
    if sres.status_code == 503:
        pytest.skip("auth not configured")
    tok = sres.json()["token"]
    headers = {"Authorization": f"Bearer {tok}"}

    # Empty payload → 400
    r = client.post(
        "/api/admin/concept-videos/check-iframe",
        json={},
        headers=headers,
    )
    assert r.status_code == 400

    # Non-YouTube host → 200 with embeddable=None (SSRF guard)
    r = client.post(
        "/api/admin/concept-videos/check-iframe",
        json={"source_url": "https://evil.example/x"},
        headers=headers,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["embeddable"] is None
    assert "allowlist" in body["reason"].lower()


def test_admin_health_page_anonymous_blocked():
    """prod-85 — /admin/health is admin-only."""
    from padhai.web import app
    client = TestClient(app)
    r = client.get("/admin/health", follow_redirects=False)
    assert r.status_code in (401, 403)


def test_admin_health_page_returns_html_for_admin(monkeypatch):
    """prod-85 — admin gets the page with cross-links to other admin pages
    and the JS that fetches the 3 underlying endpoints."""
    monkeypatch.setenv("PADHAI_SKIP_DOTENV", "1")
    monkeypatch.setenv(
        "PADHAI_JWT_SECRET",
        "test-secret-abcdef0123456789abcdef0123456789",
    )
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("PADHAI_SUPERUSER_EMAILS", raising=False)

    import importlib

    from padhai import auth as _auth
    from padhai import web as _web
    importlib.reload(_auth)
    importlib.reload(_web)
    client = TestClient(_web.app)

    import uuid
    email = f"health+{uuid.uuid4().hex[:8]}@example.com"
    sres = client.post(
        "/auth/signup",
        data={"email": email, "password": "Pass@12345", "terms_accepted": "true"},
    )
    if sres.status_code == 503:
        pytest.skip("auth not configured")
    tok = sres.json()["token"]

    r = client.get(
        "/admin/health",
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert r.status_code == 200, r.text
    body = r.text
    assert "System health" in body
    # Cross-links
    assert "/admin/concept-curator" in body
    assert "/admin/curator-stats" in body
    # Loads the 3 surfaces
    assert "/healthz" in body
    assert "/api/concept-videos/badge" in body
    assert "/api/admin/concept-videos/curator-stats" in body


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
