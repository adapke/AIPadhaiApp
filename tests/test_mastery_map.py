"""prod-135 — Tests for the Concept Mastery Map endpoint.

Covers:
  1. Aggregator: untouched user → all topics return color_state='untouched'.
  2. Aggregator: user_topic_mastery row present → mastery + color computed.
  3. Aggregator: time-decay applied when last_practised >14 days old.
  4. Aggregator: subject filter narrows the result set.
  5. Helper: _normalise_topic_key collapses punctuation + case.
  6. Helper: _color_state maps thresholds correctly.
  7. HTTP: endpoints require auth (anonymous → 401).
  8. HTTP: happy-path returns the expected JSON shape.
  9. HTTP: summary endpoint returns only counts.
 10. Router registry: 'mastery_map' is in _ROUTER_NAMES.
"""
from __future__ import annotations

import sqlite3
import sys
import time
import uuid
from pathlib import Path

from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


def _seed_curriculum_and_mastery(monkeypatch, tmp_path, *, user_id, with_mastery=True):
    """Seed curriculum_objectives + user_topic_mastery in a tmp_path DB."""
    db_path = tmp_path / f"test_mastery_{uuid.uuid4().hex[:6]}.db"
    monkeypatch.setenv("PADHAI_DB_PATH", str(db_path))
    monkeypatch.setenv("PADHAI_SKIP_DOTENV", "1")
    monkeypatch.setenv(
        "PADHAI_JWT_SECRET",
        "test-secret-abcdef0123456789abcdef0123456789",
    )
    monkeypatch.delenv("DATABASE_URL", raising=False)

    import importlib

    from padhai import db, mastery, mastery_aggregate
    importlib.reload(db)
    importlib.reload(mastery)
    importlib.reload(mastery_aggregate)

    # Create curriculum_objectives + user_topic_mastery tables.
    mastery.migrate()
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS curriculum_objectives (
            id TEXT PRIMARY KEY,
            board TEXT,
            grade INTEGER,
            subject TEXT,
            chapter TEXT,
            objective TEXT,
            source TEXT,
            revision TEXT,
            created_at REAL
        );
    """)
    # Seed 4 chapters across 2 subjects for CBSE Class 10
    chapters = [
        ("Light Reflection and Refraction", "Science"),
        ("Electricity", "Science"),
        ("Real Numbers", "Math"),
        ("Quadratic Equations", "Math"),
    ]
    for ch, subj in chapters:
        conn.execute(
            "INSERT INTO curriculum_objectives "
            "(id, board, grade, subject, chapter, objective, source, revision, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (uuid.uuid4().hex, "CBSE", 10, subj, ch, "obj", "ncert", "v1", time.time()),
        )

    if with_mastery:
        # User has touched 2 of the 4 — recent + decayed
        now = time.time()
        rows = [
            # topic_key matches normalised chapter; recent fresh attempt
            (user_id, "light reflection and refraction", 0.85, 5, 4, now - 3 * 24 * 3600),
            # decayed — practised 40 days ago, should drop to ~0.21
            (user_id, "real numbers", 0.85, 3, 2, now - 40 * 24 * 3600),
        ]
        for row in rows:
            conn.execute(
                "INSERT OR REPLACE INTO user_topic_mastery "
                "(user_id, topic_key, mastery, attempts, correct, last_seen) "
                "VALUES (?,?,?,?,?,?)",
                row,
            )
    conn.commit()
    conn.close()


def test_aggregator_untouched_user_returns_all_topics_untouched(monkeypatch, tmp_path):
    """prod-135 — A new user with no signal sees every curriculum topic
    as `color_state='untouched'`. SPA can render them as 'not started'."""
    user_id = "user-untouched-" + uuid.uuid4().hex[:6]
    _seed_curriculum_and_mastery(monkeypatch, tmp_path, user_id=user_id, with_mastery=False)
    from padhai import mastery_aggregate
    rows = mastery_aggregate.build_mastery_map(
        user_id=user_id, board="CBSE", grade=10,
    )
    assert len(rows) == 4
    for r in rows:
        assert r.color_state == "untouched", r
        assert r.last_practised is None
        assert r.mastery == 0.0
        assert r.source_attempts == {}


def test_aggregator_existing_mastery_produces_green_and_decayed(monkeypatch, tmp_path):
    """prod-135 — Topic practised 3 days ago at 0.85 → green/fresh.
    Topic practised 40 days ago at 0.85 → decayed + red."""
    user_id = "user-mixed-" + uuid.uuid4().hex[:6]
    _seed_curriculum_and_mastery(monkeypatch, tmp_path, user_id=user_id, with_mastery=True)
    from padhai import mastery_aggregate
    rows = mastery_aggregate.build_mastery_map(
        user_id=user_id, board="CBSE", grade=10,
    )
    by_key = {r.topic_key: r for r in rows}
    # Light reflection — recent + high mastery
    light = by_key["light reflection and refraction"]
    assert light.color_state == "green", light
    assert light.decay_state == "fresh"
    assert light.mastery >= 0.7

    # Real Numbers — decayed
    rn = by_key["real numbers"]
    assert rn.decay_state == "decayed", rn
    # Mastery should have decayed substantially from 0.85
    assert rn.mastery < 0.5, rn
    assert rn.color_state == "red"

    # Untouched topics still appear
    assert by_key["electricity"].color_state == "untouched"
    assert by_key["quadratic equations"].color_state == "untouched"


def test_aggregator_subject_filter(monkeypatch, tmp_path):
    """prod-135 — `subject=Math` returns only Math rows."""
    user_id = "user-subj-" + uuid.uuid4().hex[:6]
    _seed_curriculum_and_mastery(monkeypatch, tmp_path, user_id=user_id, with_mastery=False)
    from padhai import mastery_aggregate
    rows = mastery_aggregate.build_mastery_map(
        user_id=user_id, board="CBSE", grade=10, subject="Math",
    )
    assert len(rows) == 2
    for r in rows:
        assert r.subject == "Math"


def test_normalise_topic_key():
    """prod-135 — Aggressive normalisation lets fuzzy joins work."""
    from padhai.mastery_aggregate import _normalise_topic_key
    assert _normalise_topic_key("Newton's First Law") == "newton s first law"
    assert _normalise_topic_key("  Acids,  Bases  ") == "acids bases"
    assert _normalise_topic_key(None) == ""
    assert _normalise_topic_key("") == ""


def test_color_state_thresholds():
    """prod-135 — color_state maps mastery + decay correctly."""
    from padhai.mastery_aggregate import _color_state
    assert _color_state(0.0, "untouched") == "untouched"
    assert _color_state(0.9, "fresh") == "green"
    assert _color_state(0.9, "decayed") == "yellow"  # green requires not-decayed
    assert _color_state(0.5, "fresh") == "yellow"
    assert _color_state(0.3, "fresh") == "red"
    assert _color_state(0.0, "fresh") == "red"


def test_apply_decay_below_threshold_is_noop():
    """prod-135 — Mastery within fresh window doesn't decay."""
    from padhai.mastery_aggregate import _apply_decay
    now = time.time()
    # 5 days ago — within DECAY_START_SEC
    assert _apply_decay(0.8, now - 5 * 24 * 3600, now) == 0.8


def test_apply_decay_halves_at_one_half_life():
    """prod-135 — At DECAY_START_SEC + DECAY_HALF_LIFE_SEC, mastery halves."""
    from padhai.mastery_aggregate import (
        DECAY_HALF_LIFE_SEC,
        DECAY_START_SEC,
        _apply_decay,
    )
    now = time.time()
    last = now - (DECAY_START_SEC + DECAY_HALF_LIFE_SEC)
    decayed = _apply_decay(0.8, last, now)
    # ~0.4 (one half-life)
    assert 0.35 < decayed < 0.45, decayed


def test_summarise_counts_color_states():
    """prod-135 — summarise() returns correct color-state counts."""
    from padhai.mastery_aggregate import ConceptMastery, summarise
    rows = [
        ConceptMastery("a", "A", "A", "Math", "CBSE", 10, 0.8, 0.8, time.time(),
                       "fresh", "green", {}),
        ConceptMastery("b", "B", "B", "Math", "CBSE", 10, 0.5, 0.5, time.time(),
                       "fresh", "yellow", {}),
        ConceptMastery("c", "C", "C", "Math", "CBSE", 10, 0.2, 0.2, time.time(),
                       "fresh", "red", {}),
        ConceptMastery("d", "D", "D", "Math", "CBSE", 10, 0.0, 0.0, None,
                       "untouched", "untouched", {}),
    ]
    s = summarise(rows)
    assert s == {"green": 1, "yellow": 1, "red": 1, "untouched": 1, "total": 4}


def test_endpoint_requires_auth(monkeypatch, tmp_path):
    """prod-135 — anonymous GET /api/me/mastery-map → 401."""
    user_id = "anon-" + uuid.uuid4().hex[:6]
    _seed_curriculum_and_mastery(monkeypatch, tmp_path, user_id=user_id, with_mastery=False)
    import importlib

    from padhai import auth, web
    importlib.reload(auth)
    importlib.reload(web)
    client = TestClient(web.app)
    r = client.get("/api/me/mastery-map?board=CBSE&grade=10")
    assert r.status_code in (401, 403)


def test_endpoint_happy_path(monkeypatch, tmp_path):
    """prod-135 — Authed GET returns expected shape."""
    _seed_curriculum_and_mastery(monkeypatch, tmp_path, user_id="placeholder", with_mastery=False)
    import importlib

    from padhai import auth, web
    importlib.reload(auth)
    importlib.reload(web)
    client = TestClient(web.app)

    email = f"mastery+{uuid.uuid4().hex[:8]}@example.com"
    sres = client.post(
        "/auth/signup",
        data={"email": email, "password": "Pass@12345", "terms_accepted": "true"},
    )
    if sres.status_code == 503:
        import pytest
        pytest.skip("auth not configured in test env")
    tok = sres.json()["token"]

    r = client.get(
        "/api/me/mastery-map?board=CBSE&grade=10",
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert "rows" in body
    assert "summary" in body
    assert body["board"] == "CBSE"
    assert body["grade"] == 10
    # The freshly-signed-up user has no mastery yet, so all rows are
    # untouched.
    assert body["summary"]["untouched"] == body["summary"]["total"]


def test_summary_endpoint_returns_counts_only(monkeypatch, tmp_path):
    """prod-135 — Summary endpoint returns counts without rows."""
    _seed_curriculum_and_mastery(monkeypatch, tmp_path, user_id="placeholder", with_mastery=False)
    import importlib

    from padhai import auth, web
    importlib.reload(auth)
    importlib.reload(web)
    client = TestClient(web.app)

    email = f"sum+{uuid.uuid4().hex[:8]}@example.com"
    sres = client.post(
        "/auth/signup",
        data={"email": email, "password": "Pass@12345", "terms_accepted": "true"},
    )
    if sres.status_code == 503:
        import pytest
        pytest.skip("auth not configured")
    tok = sres.json()["token"]

    r = client.get(
        "/api/me/mastery-map/summary?board=CBSE&grade=10",
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    # Has counts but NOT a rows array
    assert "rows" not in body
    assert "green" in body
    assert "yellow" in body
    assert "red" in body
    assert "untouched" in body
    assert "total" in body


def test_mastery_router_registered():
    """prod-135 — 'mastery_map' is in _ROUTER_NAMES."""
    from padhai.routers import _ROUTER_NAMES
    assert "mastery_map" in _ROUTER_NAMES
