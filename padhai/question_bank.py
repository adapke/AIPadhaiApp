"""J6 — Board question bank.

Bulk-import past papers (CBSE/ICSE 2015-2025) as a searchable
question bank. Teachers compose tests by pulling from the bank;
AI generates similar-style new questions when needed.

Schema:
  question_bank   one row per question, indexed by (board, grade,
                  subject, chapter, topic_tags JSON)

Acquisition pipeline (operational, not in this module):
- Public sources: NCERT past paper PDFs, CBSE/ICSE official sites,
  state board archives (where freely available)
- OCR + Claude classification: extract → tag by topic → answer key
  matching
- Manual review for high-stakes (board exam) questions

This module ships the read API + an upsert path for the ingest
worker. The ingest worker itself (`padhai/tools/ingest_papers.py`)
lands in v1.7.x.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS question_bank (
    id              TEXT PRIMARY KEY,
    board           TEXT NOT NULL,        -- 'cbse' | 'icse' | 'state_mh' | ...
    grade           INTEGER NOT NULL,
    subject         TEXT NOT NULL,        -- 'mathematics' | 'science' | ...
    chapter         TEXT,
    year            INTEGER,
    paper           TEXT,                  -- 'main' | 'sample' | 'compartment'
    question_text   TEXT NOT NULL,
    options_json    TEXT,                  -- ["a", "b", ...] for MCQ; NULL for free-form
    correct_answer  TEXT,
    marks           INTEGER NOT NULL DEFAULT 1,
    difficulty      TEXT,                  -- 'easy' | 'medium' | 'hard'
    topic_tags      TEXT,                  -- JSON array
    source          TEXT,
    created_at      REAL NOT NULL,
    UNIQUE (board, grade, subject, year, paper, question_text)
);
CREATE INDEX IF NOT EXISTS idx_qb_lookup  ON question_bank(board, grade, subject);
CREATE INDEX IF NOT EXISTS idx_qb_chapter ON question_bank(board, grade, subject, chapter);
CREATE INDEX IF NOT EXISTS idx_qb_year    ON question_bank(board, grade, subject, year DESC);
"""


# prod-138 — NCERT standards correlation column. ALTER TABLE is
# idempotent via duplicate-column-name try/except. Code format:
#   <BOARD>.<GRADE>.<SUBJECT_CODE>.<CHAPTER>[.<LO>]
# e.g. CBSE.10.SCI.CH06         — Class 10 Science, Chapter 6
#      CBSE.10.SCI.CH06.LO03    — same chapter, Learning Outcome 3
#      ICSE.12.PHY.CH02         — ICSE Class 12 Physics, Chapter 2
_NCERT_CODE_MIGRATION = """
ALTER TABLE question_bank ADD COLUMN ncert_code TEXT;
"""

# prod-193 — per-question answer explanation ("why this answer is right"),
# shown after a practice submit. Idempotent ALTER like ncert_code above.
_EXPLANATION_MIGRATION = """
ALTER TABLE question_bank ADD COLUMN explanation TEXT;
"""

VALID_DIFFICULTIES = {"easy", "medium", "hard"}


def _db_path() -> Path:
    from . import db as _db
    return _db.sqlite_path()


def _conn() -> sqlite3.Connection:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=10.0)
    conn.executescript(SCHEMA)
    # prod-138 — idempotent column add for the NCERT code.
    try:
        conn.execute(_NCERT_CODE_MIGRATION)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_qb_ncert "
            "ON question_bank(ncert_code)"
        )
    except sqlite3.OperationalError:
        # Column already exists — expected on every boot after the first.
        pass
    # prod-193 — per-question explanation column (idempotent add).
    with contextlib.suppress(sqlite3.OperationalError):
        conn.execute(_EXPLANATION_MIGRATION)
    return conn


def migrate() -> None:
    with _conn():
        pass


@dataclass(frozen=True)
class Question:
    id: str
    board: str
    grade: int
    subject: str
    chapter: str | None
    year: int | None
    paper: str | None
    question_text: str
    options: list[str] | None      # parsed from options_json
    correct_answer: str | None
    marks: int
    difficulty: str | None
    topic_tags: list[str]
    source: str | None
    created_at: float
    explanation: str | None = None  # prod-193 — shown after a practice submit


def _row_to_question(r) -> Question:
    return Question(
        id=r[0], board=r[1], grade=r[2], subject=r[3], chapter=r[4],
        year=r[5], paper=r[6], question_text=r[7],
        options=json.loads(r[8]) if r[8] else None,
        correct_answer=r[9], marks=r[10], difficulty=r[11],
        topic_tags=json.loads(r[12]) if r[12] else [],
        source=r[13], created_at=r[14],
        explanation=r[15] if len(r) > 15 else None,
    )


_SEL = (
    "id, board, grade, subject, chapter, year, paper, question_text, "
    "options_json, correct_answer, marks, difficulty, topic_tags, "
    "source, created_at, explanation"
)


def upsert(
    *, board: str, grade: int, subject: str,
    question_text: str,
    chapter: str | None = None,
    year: int | None = None,
    paper: str | None = None,
    options: list[str] | None = None,
    correct_answer: str | None = None,
    marks: int = 1,
    difficulty: str | None = None,
    topic_tags: list[str] | None = None,
    source: str | None = None,
    explanation: str | None = None,
) -> Question:
    """Insert or replace one question. Idempotent on the natural key
    (board+grade+subject+year+paper+question_text). Ingest workers
    can replay safely."""
    if difficulty and difficulty not in VALID_DIFFICULTIES:
        raise ValueError(
            f"difficulty must be in {sorted(VALID_DIFFICULTIES)}"
        )
    with _conn() as conn:
        try:
            qid = uuid.uuid4().hex
            conn.execute(
                "INSERT INTO question_bank "
                "(id, board, grade, subject, chapter, year, paper, "
                " question_text, options_json, correct_answer, "
                " marks, difficulty, topic_tags, source, created_at, "
                " explanation) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (qid, board, grade, subject, chapter, year, paper,
                 question_text,
                 json.dumps(options) if options else None,
                 correct_answer, marks, difficulty,
                 json.dumps(topic_tags) if topic_tags else None,
                 source, time.time(), explanation),
            )
        except sqlite3.IntegrityError:
            # Already exists — update everything except the natural key
            conn.execute(
                "UPDATE question_bank SET "
                " chapter = ?, options_json = ?, correct_answer = ?, "
                " marks = ?, difficulty = ?, topic_tags = ?, source = ?, "
                " explanation = ? "
                "WHERE board = ? AND grade = ? AND subject = ? "
                "AND year IS ? AND paper IS ? AND question_text = ?",
                (chapter,
                 json.dumps(options) if options else None,
                 correct_answer, marks, difficulty,
                 json.dumps(topic_tags) if topic_tags else None,
                 source, explanation,
                 board, grade, subject, year, paper, question_text),
            )
        r = conn.execute(
            f"SELECT {_SEL} FROM question_bank "
            "WHERE board = ? AND grade = ? AND subject = ? "
            "AND question_text = ?",
            (board, grade, subject, question_text),
        ).fetchone()
    return _row_to_question(r)


def search(
    *,
    board: str | None = None,
    grade: int | None = None,
    subject: str | None = None,
    chapter: str | None = None,
    difficulty: str | None = None,
    text_query: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[Question]:
    limit = max(1, min(limit, 500))
    offset = max(0, offset)
    where: list[str] = []
    params: list = []
    if board is not None:
        where.append("board = ?"); params.append(board)
    if grade is not None:
        where.append("grade = ?"); params.append(grade)
    if subject is not None:
        where.append("subject = ?"); params.append(subject)
    if chapter is not None:
        where.append("chapter = ?"); params.append(chapter)
    if difficulty is not None:
        where.append("difficulty = ?"); params.append(difficulty)
    if text_query:
        where.append("question_text LIKE ?"); params.append(f"%{text_query}%")
    sql = f"SELECT {_SEL} FROM question_bank"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY year DESC, created_at DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    with _conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_row_to_question(r) for r in rows]


# ---------- prod-138 — NCERT standards correlation ----------


# Hierarchical NCERT-code regex. Accepts:
#   CBSE.10.SCI.CH06
#   CBSE.10.SCI.CH06.LO03
#   ICSE.12.PHY.CH02.LO05
#   STATE_MH.9.MATH.CH04
# Prefix any of these to filter at any depth.
_NCERT_CODE_RE = re.compile(
    r"^[A-Z][A-Z0-9_]*\.\d{1,2}\.[A-Z]{2,6}(?:\.[A-Z]{2,4}\d{1,3}){1,2}$"
)


def is_valid_ncert_code(code: str) -> bool:
    """prod-138 — Validate NCERT code shape. Cheap regex; the actual
    chapter existence join is the curriculum_objectives table."""
    if not code or not isinstance(code, str):
        return False
    return bool(_NCERT_CODE_RE.match(code.strip()))


def set_ncert_code(qid: str, code: str | None) -> bool:
    """prod-138 — Set the NCERT code on a single question.

    `code=None` clears the tag.
    Returns True if a row was updated, False if qid not found.
    Code is validated (or None) — invalid shapes raise ValueError so the
    batch tagger can detect a bad Claude output without inserting garbage.
    """
    if code is not None and not is_valid_ncert_code(code):
        raise ValueError(f"invalid NCERT code shape: {code!r}")
    with _conn() as conn:
        cursor = conn.execute(
            "UPDATE question_bank SET ncert_code = ? WHERE id = ?",
            (code, qid),
        )
        return cursor.rowcount > 0


def list_by_standard(
    code_prefix: str, *, limit: int = 50, offset: int = 0,
) -> list[Question]:
    """prod-138 — Filter questions by NCERT code (prefix match).

    A search for `CBSE.10.SCI` matches CBSE.10.SCI.CH01,
    CBSE.10.SCI.CH06.LO03, etc. — useful for "all Class 10
    Science questions". A search for the full LO code returns
    exactly the tagged subset.

    Empty / invalid prefix → empty list (not an error).
    """
    code = (code_prefix or "").strip().upper()
    if not code:
        return []
    pattern = f"{code}%"
    with _conn() as conn:
        rows = conn.execute(
            f"SELECT {_SEL} FROM question_bank "
            "WHERE ncert_code LIKE ? "
            "ORDER BY board, grade, subject, year DESC "
            "LIMIT ? OFFSET ?",
            (pattern, limit, offset),
        ).fetchall()
    return [_row_to_question(r) for r in rows]


def count_by_standard(code_prefix: str) -> int:
    """prod-138 — Count of questions matching the code prefix."""
    code = (code_prefix or "").strip().upper()
    if not code:
        return 0
    pattern = f"{code}%"
    with _conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM question_bank WHERE ncert_code LIKE ?",
            (pattern,),
        ).fetchone()
    return int(row[0] if row else 0)


def ncert_coverage_stats() -> dict:
    """prod-138 — How many of our 2500+ PYQs are tagged?
    Surfaces in admin curator-stats page."""
    with _conn() as conn:
        total = conn.execute(
            "SELECT COUNT(*) FROM question_bank"
        ).fetchone()[0]
        tagged = conn.execute(
            "SELECT COUNT(*) FROM question_bank WHERE ncert_code IS NOT NULL"
        ).fetchone()[0]
    return {
        "total": int(total),
        "tagged": int(tagged),
        "untagged": int(total - tagged),
        "coverage_pct": round((tagged / total) * 100, 2) if total else 0.0,
    }


def list_untagged(*, limit: int = 50) -> list[Question]:
    """prod-138 — Batch tagger reads this to find work to do."""
    with _conn() as conn:
        rows = conn.execute(
            f"SELECT {_SEL} FROM question_bank "
            "WHERE ncert_code IS NULL "
            "ORDER BY board, grade, subject, year DESC "
            "LIMIT ?",
            (limit,),
        ).fetchall()
    return [_row_to_question(r) for r in rows]


# ---------- prod-194 — AI answer-explanation backfill ----------


def set_explanation(qid: str, text: str | None) -> bool:
    """prod-194 — set (or clear) the answer explanation on one question.

    Returns True if a row was updated, False if qid not found. The
    backfill worker calls this after generating an explanation via Claude;
    curated explanations (e.g. the SAT seed) are never overwritten because
    the worker only reads `list_without_explanation()`.
    """
    with _conn() as conn:
        cursor = conn.execute(
            "UPDATE question_bank SET explanation = ? WHERE id = ?",
            (text, qid),
        )
        return cursor.rowcount > 0


def list_without_explanation(
    *, limit: int = 100, board: str | None = None, subject: str | None = None,
) -> list[Question]:
    """prod-194 — questions that have no answer explanation yet. The AI
    backfill reads this to find work; optional board/subject narrows it
    (e.g. board='cbse' to explain just the CBSE bank). Treats empty
    string the same as NULL."""
    where = ["(explanation IS NULL OR explanation = '')"]
    params: list = []
    if board is not None:
        where.append("board = ?"); params.append(board)
    if subject is not None:
        where.append("subject = ?"); params.append(subject)
    sql = (
        f"SELECT {_SEL} FROM question_bank WHERE " + " AND ".join(where)
        + " ORDER BY board, subject, created_at DESC LIMIT ?"
    )
    params.append(max(1, min(limit, 2000)))
    with _conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_row_to_question(r) for r in rows]


def explanation_coverage_stats() -> dict:
    """prod-194 — how many bank questions carry an answer explanation."""
    with _conn() as conn:
        total = conn.execute("SELECT COUNT(*) FROM question_bank").fetchone()[0]
        have = conn.execute(
            "SELECT COUNT(*) FROM question_bank "
            "WHERE explanation IS NOT NULL AND explanation != ''"
        ).fetchone()[0]
    return {
        "total": int(total),
        "explained": int(have),
        "missing": int(total - have),
        "coverage_pct": round((have / total) * 100, 2) if total else 0.0,
    }


def get_by_id(qid: str) -> Question | None:
    with _conn() as conn:
        r = conn.execute(
            f"SELECT {_SEL} FROM question_bank WHERE id = ?", (qid,),
        ).fetchone()
    return _row_to_question(r) if r else None


def stats() -> dict:
    """Aggregate counts for the admin / teacher dashboard."""
    with _conn() as conn:
        total = conn.execute("SELECT COUNT(*) FROM question_bank").fetchone()[0]
        by_board = conn.execute(
            "SELECT board, COUNT(*) FROM question_bank GROUP BY board",
        ).fetchall()
        by_grade = conn.execute(
            "SELECT grade, COUNT(*) FROM question_bank GROUP BY grade",
        ).fetchall()
        by_subject = conn.execute(
            "SELECT subject, COUNT(*) FROM question_bank GROUP BY subject",
        ).fetchall()
    return {
        "total": total,
        "by_board": {r[0]: r[1] for r in by_board},
        "by_grade": {str(r[0]): r[1] for r in by_grade},
        "by_subject": {r[0]: r[1] for r in by_subject},
    }
