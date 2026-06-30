"""prod-192 — SAT (US Digital SAT) exam section regression.

Locks the contract for the SAT section across its four pillars:
  * /sat hub page renders (details + videos + flashcards + test CTA)
  * "sat" is a valid practice exam, backed by the seeded SAT bank
  * exam_taxonomy knows the SAT exam + board hint
  * the SAT PYQ seed files import cleanly (40 questions)
  * the SAT concept videos ship in data/concept_videos_seed.json

Uses a temp SQLite path so it never touches the dev DB.
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

SAT_PYQ_FILES = (
    "sat_2024_math.json", "sat_2024_reading_writing.json",
    "sat_2024_math_2.json", "sat_2024_reading_writing_2.json",
)
PYQ_DIR = REPO_ROOT / "data" / "pyq"
EXPECTED_SAT_QUESTIONS = 64  # 32 math + 32 reading & writing (full-length mock pool)
MIN_SAT_VIDEOS = 14


# ---------- the public hub page (no DB needed) ----------

_client = TestClient(__import__("padhai.web", fromlist=["app"]).app)


def test_sat_hub_renders():
    """GET /sat is public and shows all four pillars' anchors."""
    r = _client.get("/sat")
    assert r.status_code == 200
    body = r.text
    for marker in (
        "Digital SAT",          # details
        "400",                  # scoring range
        "1600",
        "Bluebook",             # accurate format detail
        "aNjoBgqrKvE",          # an embedded video id
        "Quadratic formula",    # a flashcard (math)
        "Comma splice",         # a flashcard (grammar)
        "Quick diagnostic",     # the quick single-section diagnostic CTA
        "sat_reading_writing",  # practice subject wired into the page
        "Estimated SAT",        # US-market: scaled section-score framing on results
        "Dates, registration",  # US-market: test-date + registration logistics
        "College Board",        # US-market: where to register
        "1050",                 # US-market: percentile / national-average context
        "2026–27 dates",        # latest: current test cycle (not stale 2025-26)
        "scientific",           # latest 2026: Desmos scientific<->graphing toggle
        "screen-reader",        # latest Spring-2026 Math accommodation
        "Start full-length mock",  # in-app: full-length mock test (no redirect)
        "Full SAT syllabus",       # in-app: complete syllabus rendered on the hub
        "no app-switching",        # in-app: practice stays inside our app
    ):
        assert marker in body, f"/sat page missing expected content: {marker!r}"
    # In-app, not a redirect: the external Bluebook *practice-tests* link is gone
    # (the full-length mock now runs inside our app).
    assert "practice-tests/bluebook" not in body, (
        "/sat should not redirect to College Board's Bluebook practice tests — "
        "the full-length mock is in-app now"
    )
    # prod-193 — per-question answer explanations render after scoring.
    assert "<b>Why:</b>" in body, "/sat should render per-question answer explanations"


def test_sat_router_registered():
    from padhai.routers import _ROUTER_NAMES
    assert "sat" in _ROUTER_NAMES


# ---------- practice exam wiring ----------

def test_sat_is_a_valid_practice_exam():
    from padhai import practice_test
    assert "sat" in practice_test.VALID_EXAMS


# ---------- temp-DB fixture (mirrors test_pyq_import) ----------

@pytest.fixture()
def temp_db(tmp_path, monkeypatch):
    """Pin every module to a throwaway SQLite under tmp_path."""
    db = tmp_path / "sat_test.db"
    monkeypatch.setenv("PADHAI_DB_PATH", str(db))
    monkeypatch.setenv("PADHAI_SKIP_DOTENV", "1")
    from padhai import db as _db
    from padhai import exam_taxonomy as _tax
    from padhai import practice_test as _pt
    from padhai import question_bank as _qb
    importlib.reload(_db)
    importlib.reload(_qb)
    importlib.reload(_tax)
    importlib.reload(_pt)
    yield db


def test_sat_taxonomy_seeded(temp_db):  # noqa: ARG001 (fixture used for side-effect)
    """exam_taxonomy seeds the SAT exam + maps it to the SAT board hint."""
    from padhai import exam_taxonomy as tax
    tax.migrate()
    exam = tax.get_exam("sat")
    assert exam is not None, "SAT exam not seeded in taxonomy"
    assert exam.short_title == "SAT"
    assert exam.body_code == "collegeboard"
    assert tax.board_hint_for_exam("sat") == "SAT"


def test_sat_pyq_files_import_clean(temp_db):  # noqa: ARG001
    """Both SAT PYQ batches import; 40 questions land under board='sat'
    with isolated subjects so the practice generator can pull them."""
    from padhai import question_bank
    from scripts.import_pyq import import_batch, load_file

    for name in SAT_PYQ_FILES:
        path = PYQ_DIR / name
        assert path.is_file(), f"missing SAT seed file: {path}"
        data = load_file(path)
        loaded, errors = import_batch(data, dry_run=False)
        assert not errors, f"{name}: {errors}"
        assert loaded > 0, f"{name}: nothing imported"

    stats = question_bank.stats()
    assert stats["by_board"].get("sat") == EXPECTED_SAT_QUESTIONS, stats
    assert stats["by_subject"].get("sat_math") == 32, stats
    assert stats["by_subject"].get("sat_reading_writing") == 32, stats

    # Every SAT question must be answerable (4 options, A–D key, valid difficulty).
    for q in question_bank.search(subject="sat_math", limit=50):
        assert q.options and len(q.options) == 4, q
        assert q.correct_answer in {"A", "B", "C", "D"}, q
        assert q.difficulty in {"easy", "medium", "hard"}, q
        assert q.explanation, f"SAT question missing explanation: {q.question_text[:50]}"


def test_sat_practice_generates_and_scores_from_bank(temp_db):  # noqa: ARG001
    """End-to-end: with the SAT bank seeded, a free-tier (no Claude)
    practice test fills from the bank and scores correctly."""
    from padhai import practice_test as pt
    from scripts.import_pyq import import_batch, load_file

    for name in SAT_PYQ_FILES:
        import_batch(load_file(PYQ_DIR / name), dry_run=False)

    test = pt.generate(
        user_id="sat-tester", exam="sat", subject="sat_math",
        target_minutes=20,
    )
    assert test.exam == "sat"
    assert test.subject == "sat_math"
    assert len(test.questions) > 0
    # No Claude key in tests, so this MUST come from the bank, not synthesis.
    assert test.generation_method in {"bank", "mixed"}, test.generation_method
    assert all(q["source"] == "bank" for q in test.questions), test.questions

    # Answer every question with its real key -> full marks proves scoring.
    answers = {q["id"]: (q.get("correct_answer") or "A") for q in test.questions}
    score = pt.submit(test_id=test.id, answers=answers)
    assert score["total"] == score["max"] > 0, score
    assert score["pct"] == 1.0, score
    # prod-193 — submit surfaces per-question explanations for review.
    assert any(pq.get("explanation") for pq in score["per_question"]), score["per_question"][:2]


# ---------- shipped video catalog ----------

def test_sat_videos_ship_in_seed():
    """The SAT concept videos are exported to the shippable seed JSON
    so they reach production (the dev DB is gitignored)."""
    seed = json.loads(
        (REPO_ROOT / "data" / "concept_videos_seed.json").read_text(encoding="utf-8")
    )
    sat_rows = [r for r in seed if str(r.get("board") or "").upper() == "SAT"]
    assert len(sat_rows) >= MIN_SAT_VIDEOS, (
        f"expected >={MIN_SAT_VIDEOS} SAT videos in the seed, got {len(sat_rows)}"
    )
    # All verified + real YouTube sources.
    for r in sat_rows:
        assert r["quality_tier"] == "verified", r
        assert "youtube.com" in (r.get("source_url") or ""), r
