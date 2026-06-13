"""prod-138 — Tests for NCERT Standards Tagging.

Covers:
  1. is_valid_ncert_code accepts canonical shapes, rejects garbage.
  2. set_ncert_code persists; get reads it back.
  3. set_ncert_code rejects invalid shapes (ValueError).
  4. list_by_standard prefix-match (CBSE.10.SCI matches CBSE.10.SCI.CH06).
  5. count_by_standard returns correct count.
  6. ncert_coverage_stats arithmetic.
  7. list_untagged returns only untagged rows.
  8. HTTP GET /api/questions/by-standard is public + returns shape.
  9. HTTP GET /api/admin/teacher-tools/ncert-coverage admin-gated.
 10. Router registered.
 11. ALTER TABLE is idempotent across reload (no crash on 2nd boot).
"""
from __future__ import annotations

import sys
import uuid
from pathlib import Path

from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


def _isolated_qb(monkeypatch, tmp_path):
    db_path = tmp_path / f"test_ncert_{uuid.uuid4().hex[:6]}.db"
    monkeypatch.setenv("PADHAI_DB_PATH", str(db_path))
    monkeypatch.setenv("PADHAI_SKIP_DOTENV", "1")
    monkeypatch.setenv(
        "PADHAI_JWT_SECRET",
        "test-secret-abcdef0123456789abcdef0123456789",
    )
    monkeypatch.delenv("DATABASE_URL", raising=False)

    import importlib

    from padhai import db, question_bank
    importlib.reload(db)
    importlib.reload(question_bank)
    question_bank.migrate()


def _seed_question(**overrides):
    """Insert a sample question. Returns the Question dataclass."""
    from padhai import question_bank
    defaults = dict(
        board="cbse",
        grade=10,
        subject="science",
        chapter="Life Processes",
        year=2024,
        paper="main",
        question_text=f"Sample question {uuid.uuid4().hex[:6]}",
    )
    defaults.update(overrides)
    return question_bank.upsert(**defaults)


def test_is_valid_ncert_code():
    """prod-138 — Validator accepts canonical shapes, rejects garbage."""
    from padhai.question_bank import is_valid_ncert_code
    assert is_valid_ncert_code("CBSE.10.SCI.CH06")
    assert is_valid_ncert_code("CBSE.10.SCI.CH06.LO03")
    assert is_valid_ncert_code("ICSE.12.PHY.CH02")
    assert is_valid_ncert_code("JEE_MAIN.0.MATH.CH04")
    assert is_valid_ncert_code("NEET.0.BIO.CH08")
    assert is_valid_ncert_code("STATE_MH.9.MATH.CH04")
    # Invalid
    assert not is_valid_ncert_code("")
    assert not is_valid_ncert_code("cbse.10.sci.ch06")  # lowercase
    assert not is_valid_ncert_code("CBSE.10")            # too short
    assert not is_valid_ncert_code("CBSE.10.SCI")        # missing chapter
    assert not is_valid_ncert_code("garbage")


def test_set_ncert_code_persists(monkeypatch, tmp_path):
    """prod-138 — set + get roundtrip."""
    _isolated_qb(monkeypatch, tmp_path)
    from padhai import question_bank
    q = _seed_question()
    ok = question_bank.set_ncert_code(q.id, "CBSE.10.SCI.CH06")
    assert ok
    # Sanity — searching by the code finds the row
    rows = question_bank.list_by_standard("CBSE.10.SCI.CH06")
    assert len(rows) == 1
    assert rows[0].id == q.id


def test_set_ncert_code_rejects_invalid(monkeypatch, tmp_path):
    """prod-138 — Bad shape raises ValueError before write."""
    _isolated_qb(monkeypatch, tmp_path)
    import pytest

    from padhai import question_bank
    q = _seed_question()
    with pytest.raises(ValueError):
        question_bank.set_ncert_code(q.id, "garbage")
    with pytest.raises(ValueError):
        question_bank.set_ncert_code(q.id, "cbse.10.sci.ch06")
    # Clearing via None still works
    assert question_bank.set_ncert_code(q.id, None) is True


def test_set_ncert_code_unknown_id(monkeypatch, tmp_path):
    """prod-138 — Setting on unknown id returns False (no crash)."""
    _isolated_qb(monkeypatch, tmp_path)
    from padhai import question_bank
    ok = question_bank.set_ncert_code("nonexistent", "CBSE.10.SCI.CH06")
    assert ok is False


def test_list_by_standard_prefix_match(monkeypatch, tmp_path):
    """prod-138 — CBSE.10.SCI matches CBSE.10.SCI.CH06 + CBSE.10.SCI.CH08."""
    _isolated_qb(monkeypatch, tmp_path)
    from padhai import question_bank
    q1 = _seed_question(question_text="q1 about respiration")
    q2 = _seed_question(question_text="q2 about photosynthesis")
    q3 = _seed_question(
        question_text="q3 about geometry", subject="mathematics",
        chapter="Triangles",
    )
    question_bank.set_ncert_code(q1.id, "CBSE.10.SCI.CH06")
    question_bank.set_ncert_code(q2.id, "CBSE.10.SCI.CH08")
    question_bank.set_ncert_code(q3.id, "CBSE.10.MATH.CH07")

    sci = question_bank.list_by_standard("CBSE.10.SCI")
    assert len(sci) == 2
    ch6 = question_bank.list_by_standard("CBSE.10.SCI.CH06")
    assert len(ch6) == 1
    assert ch6[0].id == q1.id


def test_count_by_standard(monkeypatch, tmp_path):
    """prod-138 — count_by_standard reflects matching rows."""
    _isolated_qb(monkeypatch, tmp_path)
    from padhai import question_bank
    q1 = _seed_question(question_text="a")
    q2 = _seed_question(question_text="b")
    question_bank.set_ncert_code(q1.id, "CBSE.10.SCI.CH06")
    question_bank.set_ncert_code(q2.id, "CBSE.10.SCI.CH08")
    assert question_bank.count_by_standard("CBSE.10.SCI") == 2
    assert question_bank.count_by_standard("ICSE.10.PHY") == 0


def test_ncert_coverage_stats(monkeypatch, tmp_path):
    """prod-138 — Stats roll-up is correct."""
    _isolated_qb(monkeypatch, tmp_path)
    from padhai import question_bank
    q1 = _seed_question(question_text="a")
    _seed_question(question_text="b")  # untagged
    _seed_question(question_text="c")  # untagged
    question_bank.set_ncert_code(q1.id, "CBSE.10.SCI.CH06")
    stats = question_bank.ncert_coverage_stats()
    assert stats["total"] == 3
    assert stats["tagged"] == 1
    assert stats["untagged"] == 2
    assert 33.0 < stats["coverage_pct"] < 34.0


def test_list_untagged(monkeypatch, tmp_path):
    """prod-138 — Tagger reads only ncert_code IS NULL rows."""
    _isolated_qb(monkeypatch, tmp_path)
    from padhai import question_bank
    q1 = _seed_question(question_text="a")
    q2 = _seed_question(question_text="b")
    _seed_question(question_text="c")
    question_bank.set_ncert_code(q1.id, "CBSE.10.SCI.CH06")
    untagged = question_bank.list_untagged()
    untagged_ids = {q.id for q in untagged}
    assert q1.id not in untagged_ids
    assert q2.id in untagged_ids


def test_http_by_standard_is_public(monkeypatch, tmp_path):
    """prod-138 — GET /api/questions/by-standard is public + returns shape."""
    _isolated_qb(monkeypatch, tmp_path)
    from padhai import question_bank
    q = _seed_question(question_text="public read test")
    question_bank.set_ncert_code(q.id, "CBSE.10.SCI.CH06")

    import importlib

    from padhai import web
    importlib.reload(web)
    client = TestClient(web.app)
    r = client.get("/api/questions/by-standard?code=CBSE.10.SCI")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["code"] == "CBSE.10.SCI"
    assert body["count"] >= 1
    assert any(q.id == row["id"] for row in body["questions"])


def test_http_admin_coverage_requires_admin(monkeypatch, tmp_path):
    """prod-138 — Coverage endpoint admin-gated by prod-9 injection."""
    _isolated_qb(monkeypatch, tmp_path)
    import importlib

    from padhai import web
    importlib.reload(web)
    client = TestClient(web.app)
    r = client.get("/api/admin/teacher-tools/ncert-coverage")
    assert r.status_code in (401, 403)


def test_questions_by_standard_router_registered():
    """prod-138 — Router is in _ROUTER_NAMES."""
    from padhai.routers import _ROUTER_NAMES
    assert "questions_by_standard" in _ROUTER_NAMES


def test_alter_table_idempotent_across_reload(monkeypatch, tmp_path):
    """prod-138 — Second migrate() doesn't crash on duplicate ALTER."""
    _isolated_qb(monkeypatch, tmp_path)
    from padhai import question_bank
    # First migrate already happened in fixture; call again.
    question_bank.migrate()  # should not raise
    question_bank.migrate()  # idempotent
    # Insert + tag still works after multiple migrates
    q = _seed_question(question_text="after-reload")
    question_bank.set_ncert_code(q.id, "CBSE.10.SCI.CH06")
    rows = question_bank.list_by_standard("CBSE.10.SCI.CH06")
    assert any(r.id == q.id for r in rows)
