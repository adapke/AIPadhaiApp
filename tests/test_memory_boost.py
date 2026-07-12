"""prod-139 — Tests for Memory Boost daily drill.

Covers:
  1. get_or_create_pack produces ≤3 picks on a fresh user (untouched
     for now since mastery is empty).
  2. get_or_create_pack is idempotent — second call returns same picks.
  3. record_answer persists + bumps streak.
  4. record_answer rejects unauthorised pick_id (different user).
  5. Streak: first answer → current=1, longest=1.
  6. Streak: same-day re-answer doesn't double-bump.
  7. get_streak returns zeros for new user.
  8. hydrate_picks inflates pyq item_ref to question payload.
  9. HTTP GET /api/me/memory-boost requires auth.
 10. HTTP POST /api/me/memory-boost/answer requires auth.
 11. HTTP GET /api/me/memory-boost/streak requires auth.
 12. Router 'memory_boost_routes' is registered.
 13. Idempotent migrate (no crash on reload).
"""
from __future__ import annotations

import sys
import uuid
from pathlib import Path

from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


def _isolated_mb(monkeypatch, tmp_path):
    db_path = tmp_path / f"test_memboost_{uuid.uuid4().hex[:6]}.db"
    monkeypatch.setenv("PADHAI_DB_PATH", str(db_path))
    monkeypatch.setenv("PADHAI_SKIP_DOTENV", "1")
    monkeypatch.setenv(
        "PADHAI_JWT_SECRET",
        "test-secret-abcdef0123456789abcdef0123456789",
    )
    monkeypatch.delenv("DATABASE_URL", raising=False)

    import importlib

    from padhai import db, memory_boost, question_bank
    importlib.reload(db)
    importlib.reload(question_bank)
    importlib.reload(memory_boost)
    question_bank.migrate()
    memory_boost.migrate()


def _seed_pyqs(n=3):
    from padhai import question_bank
    ids = []
    for i in range(n):
        q = question_bank.upsert(
            board="cbse",
            grade=10,
            subject="science",
            chapter=f"Chapter {i+1}",
            year=2024,
            paper="main",
            question_text=f"Test question {i+1} {uuid.uuid4().hex[:6]}",
        )
        ids.append(q.id)
    return ids


def test_pack_creation_with_pyqs(monkeypatch, tmp_path):
    """prod-139 — Pack creation returns at most 3 picks."""
    _isolated_mb(monkeypatch, tmp_path)
    _seed_pyqs(n=5)
    from padhai import memory_boost
    picks = memory_boost.get_or_create_pack(
        user_id="user-1", board="cbse", grade=10,
    )
    assert 1 <= len(picks) <= 3, picks
    for p in picks:
        assert p.user_id == "user-1"
        assert p.bucket in {"critical", "warmup", "fresh"}
        assert p.item_kind == "pyq"


def test_pack_idempotent_same_day(monkeypatch, tmp_path):
    """prod-139 — Second call returns the SAME picks (no fresh generation)."""
    _isolated_mb(monkeypatch, tmp_path)
    _seed_pyqs(n=5)
    from padhai import memory_boost
    a = memory_boost.get_or_create_pack(user_id="user-1", board="cbse", grade=10)
    b = memory_boost.get_or_create_pack(user_id="user-1", board="cbse", grade=10)
    assert [p.id for p in a] == [p.id for p in b]


def test_record_answer_bumps_streak(monkeypatch, tmp_path):
    """prod-139 — record_answer persists + sets current_streak=1."""
    _isolated_mb(monkeypatch, tmp_path)
    _seed_pyqs(n=3)
    from padhai import memory_boost
    picks = memory_boost.get_or_create_pack(
        user_id="user-streak", board="cbse", grade=10,
    )
    assert picks, "expected at least one pick"
    out = memory_boost.record_answer(
        pick_id=picks[0].id,
        user_id="user-streak",
        was_correct=True,
        time_seconds=12,
    )
    assert out["recorded"] is True
    assert out["streak"]["current_streak"] == 1
    assert out["streak"]["longest_streak"] >= 1


def test_record_answer_rejects_other_users_pick(monkeypatch, tmp_path):
    """prod-139 — Trying to answer someone else's pick → PermissionError."""
    _isolated_mb(monkeypatch, tmp_path)
    _seed_pyqs(n=3)
    import pytest

    from padhai import memory_boost
    picks = memory_boost.get_or_create_pack(
        user_id="user-A", board="cbse", grade=10,
    )
    with pytest.raises(PermissionError):
        memory_boost.record_answer(
            pick_id=picks[0].id, user_id="user-B", was_correct=True,
        )


def test_record_answer_unknown_pick(monkeypatch, tmp_path):
    """prod-139 — Unknown pick_id → ValueError."""
    _isolated_mb(monkeypatch, tmp_path)
    import pytest

    from padhai import memory_boost
    with pytest.raises(ValueError):
        memory_boost.record_answer(
            pick_id="bogus", user_id="u", was_correct=True,
        )


def test_streak_no_double_bump_same_day(monkeypatch, tmp_path):
    """prod-139 — Answering 2 picks on the same day keeps current_streak=1."""
    _isolated_mb(monkeypatch, tmp_path)
    _seed_pyqs(n=5)
    from padhai import memory_boost
    picks = memory_boost.get_or_create_pack(
        user_id="user-d", board="cbse", grade=10,
    )
    if len(picks) >= 2:
        memory_boost.record_answer(
            pick_id=picks[0].id, user_id="user-d", was_correct=True,
        )
        out = memory_boost.record_answer(
            pick_id=picks[1].id, user_id="user-d", was_correct=True,
        )
        assert out["streak"]["current_streak"] == 1


def test_get_streak_for_new_user(monkeypatch, tmp_path):
    """prod-139 — Unseen user → all zeros."""
    _isolated_mb(monkeypatch, tmp_path)
    from padhai import memory_boost
    s = memory_boost.get_streak("never-seen-user")
    assert s == {"current_streak": 0, "longest_streak": 0, "last_active_date": None}


def test_hydrate_picks_inflates_pyq(monkeypatch, tmp_path):
    """prod-139 — hydrate_picks attaches the question_text."""
    _isolated_mb(monkeypatch, tmp_path)
    _seed_pyqs(n=3)
    from padhai import memory_boost
    picks = memory_boost.get_or_create_pack(
        user_id="user-h", board="cbse", grade=10,
    )
    inflated = memory_boost.hydrate_picks(picks)
    assert len(inflated) == len(picks)
    for entry in inflated:
        assert "pick_id" in entry
        assert "bucket" in entry
        assert "item" in entry
        if not entry["item"].get("missing"):
            assert "question_text" in entry["item"]


def test_http_memory_boost_requires_auth(monkeypatch, tmp_path):
    """prod-139 — GET /api/me/memory-boost requires auth."""
    _isolated_mb(monkeypatch, tmp_path)
    import importlib

    from padhai import web
    importlib.reload(web)
    client = TestClient(web.app)
    r = client.get("/api/me/memory-boost?board=CBSE&grade=10")
    assert r.status_code in (401, 403)


def test_http_answer_requires_auth(monkeypatch, tmp_path):
    """prod-139 — POST /api/me/memory-boost/answer requires auth."""
    _isolated_mb(monkeypatch, tmp_path)
    import importlib

    from padhai import web
    importlib.reload(web)
    client = TestClient(web.app)
    r = client.post(
        "/api/me/memory-boost/answer",
        json={"pick_id": "x", "was_correct": True},
    )
    assert r.status_code in (401, 403)


def test_http_streak_requires_auth(monkeypatch, tmp_path):
    """prod-139 — GET /api/me/memory-boost/streak requires auth."""
    _isolated_mb(monkeypatch, tmp_path)
    import importlib

    from padhai import web
    importlib.reload(web)
    client = TestClient(web.app)
    r = client.get("/api/me/memory-boost/streak")
    assert r.status_code in (401, 403)


def test_memory_boost_router_registered():
    """prod-139 — 'memory_boost_routes' is registered."""
    from padhai.routers import _ROUTER_NAMES
    assert "memory_boost_routes" in _ROUTER_NAMES


def test_migrate_idempotent(monkeypatch, tmp_path):
    """prod-139 — Calling migrate() twice is a no-op."""
    _isolated_mb(monkeypatch, tmp_path)
    from padhai import memory_boost
    memory_boost.migrate()
    memory_boost.migrate()
    # Sanity — DB usable after re-migrate
    assert memory_boost.get_streak("u") == {
        "current_streak": 0, "longest_streak": 0, "last_active_date": None,
    }


def test_pattern_grade0_case_insensitive_distinct(monkeypatch, tmp_path):
    """prod-237 — Memory Boost must work for exam patterns and every board:

    - exam-pattern questions live at grade 0 (SAT/UPSC/…); a grade the data
      doesn't have must still resolve via grade relaxation,
    - board matching is case-insensitive ('SAT' → stored 'sat'),
    - a fresh user (no mastery) gets 3 DISTINCT questions (the old code
      collided all three buckets onto the same fallback question).
    """
    _isolated_mb(monkeypatch, tmp_path)
    from padhai import memory_boost, question_bank
    for i, subj in enumerate(("sat_math", "sat_reading_writing", "sat_math")):
        question_bank.upsert(
            board="sat", grade=0, subject=subj, chapter=f"C{i}",
            year=2024, paper="main",
            question_text=f"SAT q{i} {uuid.uuid4().hex[:6]}",
        )
    # Uppercase board + a grade the data lacks (10) — should still work.
    picks = memory_boost.get_or_create_pack(user_id="u-sat", board="SAT", grade=10)
    assert len(picks) == 3, f"expected 3 picks, got {len(picks)}"
    assert len({p.item_ref for p in picks}) == 3, "picks must be 3 distinct questions"
    hydrated = memory_boost.hydrate_picks(picks)
    assert all(not h["item"].get("missing") for h in hydrated)
    assert all(h["item"].get("question_text") for h in hydrated)
