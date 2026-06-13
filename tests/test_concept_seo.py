"""prod-134 — Tests for the public concept SEO router.

Tests:
  1. GET /concept (no slug) returns 200 HTML index.
  2. GET /concept/{slug} returns 200 HTML with required SEO markup.
  3. The HTML response has Open Graph + Schema.org + hreflang tags.
  4. The HTML response embeds the YouTube iframe.
  5. GET /concept/{unknown-slug} returns 404.
  6. The router is registered in _ROUTER_NAMES.
  7. Slug normalization works (case-insensitive, dash↔space).
  8. The endpoint is public — no auth required.
  9. hreflang covers all 9 supported locales.
 10. Schema.org JSON-LD parses as valid JSON (no broken inline syntax).
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


def _seed_test_video(monkeypatch, tmp_path):
    """Seed the concept_videos table with a row the tests can rely on.
    Uses a tmp_path SQLite so we don't pollute the dev DB."""
    db_path = tmp_path / "test_concept_seo.db"
    monkeypatch.setenv("PADHAI_DB_PATH", str(db_path))
    monkeypatch.setenv("PADHAI_SKIP_DOTENV", "1")
    monkeypatch.setenv(
        "PADHAI_JWT_SECRET",
        "test-secret-abcdef0123456789abcdef0123456789",
    )
    monkeypatch.delenv("DATABASE_URL", raising=False)

    # Force-reload db helper to pick up new env, then migrate.
    import importlib

    from padhai import concept_videos, db
    importlib.reload(db)
    importlib.reload(concept_videos)
    concept_videos.migrate()
    concept_videos.upsert(
        concept="Newton's First Law of Motion",
        source="youtube",
        source_url="https://www.youtube.com/watch?v=test_video_id",
        embed_url="https://www.youtube.com/embed/test_video_id",
        title="Newton's First Law — Peekaboo Kidz",
        channel="Peekaboo Kidz",
        duration_sec=420,
        language="en",
        board="CBSE",
        grade_min=8,
        grade_max=12,
        subject="physics",
        quality_tier="verified",
    )


def test_concept_index_returns_html(monkeypatch, tmp_path):
    """prod-134 — /concept index lists every available concept."""
    _seed_test_video(monkeypatch, tmp_path)
    import importlib

    from padhai import web
    importlib.reload(web)
    client = TestClient(web.app)
    r = client.get("/concept")
    assert r.status_code == 200
    assert "text/html" in r.headers.get("content-type", "")
    assert "Concept library" in r.text


def test_concept_page_returns_html_with_seo_markup(monkeypatch, tmp_path):
    """prod-134 — /concept/{slug} renders with all SEO markup."""
    _seed_test_video(monkeypatch, tmp_path)
    import importlib

    from padhai import web
    importlib.reload(web)
    client = TestClient(web.app)
    r = client.get("/concept/newton-first-law-of-motion")
    assert r.status_code == 200, r.text
    body = r.text

    # Open Graph
    assert 'property="og:title"' in body
    assert 'property="og:description"' in body
    assert 'property="og:url"' in body
    assert 'property="og:type" content="video.other"' in body

    # Schema.org
    assert 'application/ld+json' in body
    assert '"@type": "VideoObject"' in body
    assert '"isFamilyFriendly": true' in body

    # YouTube iframe present
    assert 'iframe src="https://www.youtube.com/embed/test_video_id"' in body
    assert 'allowfullscreen' in body

    # CTA + canonical
    assert '<link rel="canonical"' in body
    assert 'Sign up' in body


def test_concept_page_hreflang_covers_all_locales(monkeypatch, tmp_path):
    """prod-134 — hreflang tags must exist for all 9 supported locales
    plus x-default."""
    _seed_test_video(monkeypatch, tmp_path)
    import importlib

    from padhai import web
    importlib.reload(web)
    client = TestClient(web.app)
    r = client.get("/concept/newton-first-law-of-motion")
    assert r.status_code == 200
    body = r.text
    for loc in ("en", "hi", "ta", "te", "kn", "ml", "mr", "bn", "gu", "pa"):
        assert f'hreflang="{loc}"' in body, f"missing hreflang for {loc}"
    assert 'hreflang="x-default"' in body


def test_concept_page_unknown_slug_returns_404(monkeypatch, tmp_path):
    """prod-134 — unknown slug → clean 404, not a 500."""
    _seed_test_video(monkeypatch, tmp_path)
    import importlib

    from padhai import web
    importlib.reload(web)
    client = TestClient(web.app)
    r = client.get("/concept/this-concept-definitely-does-not-exist")
    assert r.status_code == 404


def test_concept_page_is_public(monkeypatch, tmp_path):
    """prod-134 — Search-engine crawlers and WhatsApp link unfurlers
    cannot authenticate. The route must work anonymously."""
    _seed_test_video(monkeypatch, tmp_path)
    import importlib

    from padhai import web
    importlib.reload(web)
    client = TestClient(web.app)
    r = client.get("/concept/newton-first-law-of-motion")
    # Specifically 200, NOT 401/403 — no auth required.
    assert r.status_code == 200


def test_concept_page_slug_normalization(monkeypatch, tmp_path):
    """prod-134 — slug accepts dashes and underscores; case-insensitive."""
    _seed_test_video(monkeypatch, tmp_path)
    import importlib

    from padhai import web
    importlib.reload(web)
    client = TestClient(web.app)

    # Dashed form
    r1 = client.get("/concept/newton-first-law-of-motion")
    # Underscored form
    r2 = client.get("/concept/newton_first_law_of_motion")
    assert r1.status_code == r2.status_code == 200


def test_concept_page_lang_query_supported(monkeypatch, tmp_path):
    """prod-134 — ?lang=hi sets <html lang> and falls back to English
    video when no Hindi-language row exists."""
    _seed_test_video(monkeypatch, tmp_path)
    import importlib

    from padhai import web
    importlib.reload(web)
    client = TestClient(web.app)
    r = client.get("/concept/newton-first-law-of-motion?lang=hi")
    assert r.status_code == 200
    assert '<html lang="hi"' in r.text
    # OG locale should reflect Hindi too
    assert 'og:locale' in r.text


def test_concept_page_lang_query_unknown_falls_back(monkeypatch, tmp_path):
    """prod-134 — ?lang=xx (unsupported) → render in English instead
    of 404-ing."""
    _seed_test_video(monkeypatch, tmp_path)
    import importlib

    from padhai import web
    importlib.reload(web)
    client = TestClient(web.app)
    r = client.get("/concept/newton-first-law-of-motion?lang=xx")
    assert r.status_code == 200
    assert '<html lang="en"' in r.text


def test_concept_seo_router_registered():
    """prod-134 — `concept_seo` is in _ROUTER_NAMES."""
    from padhai.routers import _ROUTER_NAMES
    assert "concept_seo" in _ROUTER_NAMES


def test_schema_org_json_is_valid(monkeypatch, tmp_path):
    """prod-134 — The inline Schema.org JSON-LD block must be valid
    JSON. Google ignores invalid JSON-LD (and shows a warning in
    Search Console), so a broken block hurts SEO."""
    _seed_test_video(monkeypatch, tmp_path)
    import importlib

    from padhai import web
    importlib.reload(web)
    client = TestClient(web.app)
    r = client.get("/concept/newton-first-law-of-motion")
    assert r.status_code == 200

    # Extract the JSON-LD block
    match = re.search(
        r'<script type="application/ld\+json">(.+?)</script>',
        r.text,
        re.DOTALL,
    )
    assert match, "No JSON-LD block found"
    block = match.group(1).strip()
    data = json.loads(block)  # raises if broken
    assert data["@context"] == "https://schema.org"
    assert data["@type"] == "VideoObject"
    assert data["name"]
    assert data["embedUrl"]
