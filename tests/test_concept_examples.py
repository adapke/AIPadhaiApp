"""prod-137 — Tests for Real-World Examples catalog.

Covers:
  1. Module CRUD: insert / get / review (approve, reject).
  2. list_for_slug: defaults to approved-only.
  3. list_for_slug: never leaks pending/rejected to public.
  4. list_pending_queue: returns only pending in creation order.
  5. Normalisation: slug joins are tolerant of case + punctuation.
  6. Status validation: invalid → ValueError.
  7. Stats rollup: correct counts per status.
  8. HTTP: admin endpoints require auth.
  9. HTTP: GET /api/concept-examples public, returns only approved.
 10. Router: 'concept_examples_routes' is registered.
 11. /concept/{slug} SEO page embeds approved examples (prod-134 wiring).
"""
from __future__ import annotations

import sys
import uuid
from pathlib import Path

from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


def _isolated_db(monkeypatch, tmp_path):
    db_path = tmp_path / f"test_examples_{uuid.uuid4().hex[:6]}.db"
    monkeypatch.setenv("PADHAI_DB_PATH", str(db_path))
    monkeypatch.setenv("PADHAI_SKIP_DOTENV", "1")
    monkeypatch.setenv(
        "PADHAI_JWT_SECRET",
        "test-secret-abcdef0123456789abcdef0123456789",
    )
    monkeypatch.delenv("DATABASE_URL", raising=False)

    import importlib

    from padhai import concept_examples, db
    importlib.reload(db)
    importlib.reload(concept_examples)
    concept_examples.migrate()


def test_insert_and_get_roundtrip(monkeypatch, tmp_path):
    """prod-137 — insert() + get() roundtrip."""
    _isolated_db(monkeypatch, tmp_path)
    from padhai import concept_examples as ce
    row = ce.insert(
        concept_slug="Newton's First Law",
        example_md="A Mumbai local train brakes suddenly...",
        locale="en",
    )
    fetched = ce.get(row.id)
    assert fetched is not None
    assert fetched.concept_slug == "newton s first law"
    assert "Mumbai" in fetched.example_md
    assert fetched.status == "pending"
    assert fetched.locale == "en"
    assert fetched.source == "claude"


def test_review_approve(monkeypatch, tmp_path):
    """prod-137 — review() flips pending → approved."""
    _isolated_db(monkeypatch, tmp_path)
    from padhai import concept_examples as ce
    row = ce.insert(
        concept_slug="Photosynthesis",
        example_md="A village pond fills with water...",
    )
    out = ce.review(
        example_id=row.id,
        reviewer_user_id="curator-1",
        new_status="approved",
        note="great example",
    )
    assert out is not None
    assert out.status == "approved"
    assert out.reviewed_by == "curator-1"
    assert out.reviewed_at is not None
    assert out.review_note == "great example"


def test_review_reject(monkeypatch, tmp_path):
    """prod-137 — review() flips pending → rejected."""
    _isolated_db(monkeypatch, tmp_path)
    from padhai import concept_examples as ce
    row = ce.insert(concept_slug="Acids", example_md="...")
    out = ce.review(
        example_id=row.id,
        reviewer_user_id="curator-1",
        new_status="rejected",
        note="too short",
    )
    assert out is not None
    assert out.status == "rejected"


def test_review_unknown_id_returns_none(monkeypatch, tmp_path):
    """prod-137 — review on unknown id is a no-op (not a crash)."""
    _isolated_db(monkeypatch, tmp_path)
    from padhai import concept_examples as ce
    out = ce.review(
        example_id="nonexistent",
        reviewer_user_id="curator",
        new_status="approved",
    )
    assert out is None


def test_list_for_slug_defaults_to_approved(monkeypatch, tmp_path):
    """prod-137 — Public list returns ONLY approved by default."""
    _isolated_db(monkeypatch, tmp_path)
    from padhai import concept_examples as ce
    r1 = ce.insert(concept_slug="Gravity", example_md="pending one")
    r2 = ce.insert(concept_slug="Gravity", example_md="approved one")
    r3 = ce.insert(concept_slug="Gravity", example_md="rejected one")
    ce.review(
        example_id=r2.id, reviewer_user_id="c", new_status="approved",
    )
    ce.review(
        example_id=r3.id, reviewer_user_id="c", new_status="rejected",
    )
    # r1 stays pending
    rows = ce.list_for_slug("Gravity")
    assert len(rows) == 1
    assert rows[0].id == r2.id
    assert rows[0].status == "approved"


def test_list_pending_queue(monkeypatch, tmp_path):
    """prod-137 — curator queue returns only pending."""
    _isolated_db(monkeypatch, tmp_path)
    from padhai import concept_examples as ce
    r1 = ce.insert(concept_slug="A", example_md="a")
    r2 = ce.insert(concept_slug="B", example_md="b")
    ce.review(example_id=r1.id, reviewer_user_id="c", new_status="approved")
    queue = ce.list_pending_queue()
    assert len(queue) == 1
    assert queue[0].id == r2.id


def test_normalisation_joins_tolerant(monkeypatch, tmp_path):
    """prod-137 — Slug normalisation handles case + punctuation."""
    _isolated_db(monkeypatch, tmp_path)
    from padhai import concept_examples as ce
    ce.insert(concept_slug="Newton's First Law", example_md="x")
    # Approve it so the read-side returns it
    rows = ce.list_for_slug("Newton's First Law", status="*")
    ce.review(example_id=rows[0].id, reviewer_user_id="c", new_status="approved")
    # Same string with case + extra whitespace + commas variations
    # all find the row (punctuation collapses to space).
    assert len(ce.list_for_slug("newton's first law")) == 1
    assert len(ce.list_for_slug("NEWTON'S FIRST LAW")) == 1
    assert len(ce.list_for_slug("  Newton's  First  Law  ")) == 1
    assert len(ce.list_for_slug("Newton's, First, Law")) == 1


def test_invalid_status_raises(monkeypatch, tmp_path):
    """prod-137 — Invalid status in insert() is rejected."""
    _isolated_db(monkeypatch, tmp_path)
    import pytest

    from padhai import concept_examples as ce
    with pytest.raises(ValueError):
        ce.insert(concept_slug="x", example_md="y", status="bogus")
    with pytest.raises(ValueError):
        ce.review(
            example_id="x", reviewer_user_id="c", new_status="bogus",
        )


def test_stats_rollup(monkeypatch, tmp_path):
    """prod-137 — stats() returns correct counts per status."""
    _isolated_db(monkeypatch, tmp_path)
    from padhai import concept_examples as ce
    r1 = ce.insert(concept_slug="A", example_md="a")
    r2 = ce.insert(concept_slug="B", example_md="b")
    r3 = ce.insert(concept_slug="C", example_md="c")
    ce.review(example_id=r1.id, reviewer_user_id="c", new_status="approved")
    ce.review(example_id=r2.id, reviewer_user_id="c", new_status="rejected")
    # r3 still pending
    s = ce.stats()
    assert s["pending"] == 1
    assert s["approved"] == 1
    assert s["rejected"] == 1
    assert s["total"] == 3


def test_admin_endpoints_require_admin(monkeypatch, tmp_path):
    """prod-137 — /api/admin/teacher-tools/* admin-gated → 401 anon."""
    _isolated_db(monkeypatch, tmp_path)
    import importlib

    from padhai import web
    importlib.reload(web)
    client = TestClient(web.app)
    r = client.post(
        "/api/admin/teacher-tools/generate-examples",
        json={"concept_slug": "x"},
    )
    assert r.status_code in (401, 403)
    r = client.get("/api/admin/teacher-tools/examples-queue")
    assert r.status_code in (401, 403)


def test_public_examples_endpoint_is_public(monkeypatch, tmp_path):
    """prod-137 — GET /api/concept-examples is public + filters
    to approved only."""
    _isolated_db(monkeypatch, tmp_path)
    import importlib

    from padhai import concept_examples as ce
    from padhai import web
    importlib.reload(web)

    r1 = ce.insert(concept_slug="Gravity", example_md="pending")
    r2 = ce.insert(concept_slug="Gravity", example_md="approved one")
    ce.review(example_id=r2.id, reviewer_user_id="c", new_status="approved")

    client = TestClient(web.app)
    r = client.get("/api/concept-examples?slug=Gravity")
    assert r.status_code == 200
    body = r.json()
    assert body["slug"] == "Gravity"
    assert body["count"] == 1
    assert body["examples"][0]["id"] == r2.id
    # Verify pending is NOT in the response
    assert all(ex["status"] == "approved" for ex in body["examples"]), body


def test_concept_examples_router_registered():
    """prod-137 — 'concept_examples_routes' is wired into _ROUTER_NAMES."""
    from padhai.routers import _ROUTER_NAMES
    assert "concept_examples_routes" in _ROUTER_NAMES


def test_concept_seo_page_embeds_approved_examples(monkeypatch, tmp_path):
    """prod-137 — /concept/{slug} SEO page surfaces approved examples
    as a 'Real-world examples' section."""
    _isolated_db(monkeypatch, tmp_path)

    import importlib

    from padhai import concept_examples as ce
    from padhai import concept_videos, db
    importlib.reload(db)
    importlib.reload(concept_videos)
    importlib.reload(ce)

    concept_videos.migrate()
    concept_videos.upsert(
        concept="Newton's First Law of Motion",
        source="youtube",
        source_url="https://www.youtube.com/watch?v=abc",
        embed_url="https://www.youtube.com/embed/abc",
        title="Newton's First Law",
        channel="Test channel",
        language="en",
        quality_tier="verified",
    )
    # Insert + approve an example
    row = ce.insert(
        concept_slug="Newton's First Law of Motion",
        example_md="Aman is on a Mumbai local train when it brakes suddenly.",
        locale="en",
    )
    ce.review(example_id=row.id, reviewer_user_id="c", new_status="approved")

    from padhai import web
    importlib.reload(web)
    client = TestClient(web.app)
    r = client.get("/concept/newton-first-law-of-motion")
    assert r.status_code == 200, r.text
    assert "Real-world examples" in r.text
    assert "Mumbai local train" in r.text
