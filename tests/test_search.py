"""prod-181 — tests for the unified search feature.

Covers the aggregator unit logic + the two HTTP surfaces
(/api/search JSON, /search HTML), plus the router-registry presence.
Uses an isolated tmp SQLite DB seeded with a verified video, a couple
of PYQs, and an approved example so the grouped result shape is real.
"""
from __future__ import annotations

import importlib
import uuid

from fastapi.testclient import TestClient


def _isolated(monkeypatch, tmp_path):
    db_path = tmp_path / f"search_{uuid.uuid4().hex[:6]}.db"
    monkeypatch.setenv("PADHAI_DB_PATH", str(db_path))
    monkeypatch.setenv("PADHAI_SKIP_DOTENV", "1")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    from padhai import (
        concept_examples,
        concept_videos,
        db,
        question_bank,
        search_aggregate,
    )
    importlib.reload(db)
    importlib.reload(concept_videos)
    importlib.reload(question_bank)
    importlib.reload(concept_examples)
    importlib.reload(search_aggregate)
    concept_videos.migrate()
    question_bank.migrate()
    concept_examples.migrate()
    return db_path, concept_videos, question_bank, concept_examples, search_aggregate


def _seed(cv, qb, ex):
    cv.upsert(
        concept="Newton's First Law of Motion",
        source="youtube",
        source_url="https://www.youtube.com/watch?v=abc123",
        title="Newton's First Law — Peekaboo Kidz",
        channel="Peekaboo Kidz",
        subject="physics",
        language="en",
        quality_tier="verified",
    )
    # A channel_seed video that should NOT show in search (verified-only).
    cv.upsert(
        concept="Newton's Second Law of Motion",
        source="youtube",
        source_url="https://www.youtube.com/watch?v=seed999",
        title="Newton's Second Law (stub)",
        channel="Khan Academy",
        subject="physics",
        language="en",
        quality_tier="channel_seed",
    )
    qb.upsert(
        board="cbse", grade=9, subject="science",
        chapter="Laws of Motion", year=2023, paper="main",
        question_text="State Newton's first law of motion and give an example.",
    )
    ex.insert(
        concept_slug="Newton's First Law of Motion",
        example_md="In a Mumbai local train, your body lurches when it brakes.",
        locale="en", source="human", status="approved",
    )


# ---------- aggregator unit tests ----------


def test_short_query_returns_nothing(monkeypatch, tmp_path):
    _, cv, qb, ex, sa = _isolated(monkeypatch, tmp_path)
    _seed(cv, qb, ex)
    r = sa.unified_search("n")  # 1 char
    assert r.total == 0


def test_finds_across_all_groups(monkeypatch, tmp_path):
    _, cv, qb, ex, sa = _isolated(monkeypatch, tmp_path)
    _seed(cv, qb, ex)
    r = sa.unified_search("newton")
    assert len(r.videos) == 1, r.videos
    assert len(r.questions) == 1, r.questions
    assert len(r.examples) == 1, r.examples
    assert r.total == 3


def test_videos_are_verified_only(monkeypatch, tmp_path):
    _, cv, qb, ex, sa = _isolated(monkeypatch, tmp_path)
    _seed(cv, qb, ex)
    r = sa.unified_search("newton")
    titles = [v.get("title", "") for v in r.videos]
    # The channel_seed "Second Law (stub)" must not appear.
    assert not any("stub" in t.lower() for t in titles), titles
    assert any("First Law" in t for t in titles)


def test_no_match_returns_empty(monkeypatch, tmp_path):
    _, cv, qb, ex, sa = _isolated(monkeypatch, tmp_path)
    _seed(cv, qb, ex)
    r = sa.unified_search("xyzzy-nonexistent-topic")
    assert r.total == 0


def test_per_group_cap(monkeypatch, tmp_path):
    _, _cv, qb, _ex, sa = _isolated(monkeypatch, tmp_path)
    # Seed 5 matching PYQs, cap at 2.
    for i in range(5):
        qb.upsert(
            board="cbse", grade=10, subject="physics",
            chapter="Gravitation", year=2020 + i, paper="main",
            question_text=f"Explain gravitation, variant {i}.",
        )
    r = sa.unified_search("gravitation", per_group=2)
    assert len(r.questions) == 2


def test_to_dict_shape(monkeypatch, tmp_path):
    _, cv, qb, ex, sa = _isolated(monkeypatch, tmp_path)
    _seed(cv, qb, ex)
    d = sa.unified_search("newton").to_dict()
    assert set(d.keys()) == {"query", "total", "videos", "questions", "examples"}
    assert d["query"] == "newton"
    assert d["total"] == 3


# ---------- HTTP surface tests ----------


def test_api_search_endpoint(monkeypatch, tmp_path):
    _isolated(monkeypatch, tmp_path)  # sets env before app import
    from padhai import concept_examples, concept_videos, question_bank, web
    importlib.reload(web)
    cv = importlib.reload(concept_videos)
    qb = importlib.reload(question_bank)
    ex = importlib.reload(concept_examples)
    cv.migrate(); qb.migrate(); ex.migrate()
    _seed(cv, qb, ex)
    client = TestClient(web.app)
    resp = client.get("/api/search", params={"q": "newton"})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["query"] == "newton"
    assert data["total"] >= 1
    assert "videos" in data and "questions" in data and "examples" in data


def test_api_search_short_query_ok(monkeypatch, tmp_path):
    _isolated(monkeypatch, tmp_path)
    from padhai import web
    importlib.reload(web)
    client = TestClient(web.app)
    resp = client.get("/api/search", params={"q": "x"})
    assert resp.status_code == 200
    assert resp.json()["total"] == 0


def test_search_html_page_renders(monkeypatch, tmp_path):
    _isolated(monkeypatch, tmp_path)
    from padhai import web
    importlib.reload(web)
    client = TestClient(web.app)
    # Empty query → prompt page
    r0 = client.get("/search")
    assert r0.status_code == 200
    assert "Search" in r0.text
    assert 'class="topnav"' in r0.text  # SPA chrome
    # Query → results page (no rows seeded in this reload, so empty-state)
    r1 = client.get("/search", params={"q": "newton"})
    assert r1.status_code == 200


def test_router_registered():
    from padhai.routers import _ROUTER_NAMES
    assert "search" in _ROUTER_NAMES
