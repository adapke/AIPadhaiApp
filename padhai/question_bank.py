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

import json
import os
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

VALID_DIFFICULTIES = {"easy", "medium", "hard"}


def _db_path() -> Path:
    from . import db as _db
    return _db.sqlite_path()


def _conn() -> sqlite3.Connection:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=10.0)
    conn.executescript(SCHEMA)
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


def _row_to_question(r) -> Question:
    return Question(
        id=r[0], board=r[1], grade=r[2], subject=r[3], chapter=r[4],
        year=r[5], paper=r[6], question_text=r[7],
        options=json.loads(r[8]) if r[8] else None,
        correct_answer=r[9], marks=r[10], difficulty=r[11],
        topic_tags=json.loads(r[12]) if r[12] else [],
        source=r[13], created_at=r[14],
    )


_SEL = (
    "id, board, grade, subject, chapter, year, paper, question_text, "
    "options_json, correct_answer, marks, difficulty, topic_tags, "
    "source, created_at"
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
                " marks, difficulty, topic_tags, source, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (qid, board, grade, subject, chapter, year, paper,
                 question_text,
                 json.dumps(options) if options else None,
                 correct_answer, marks, difficulty,
                 json.dumps(topic_tags) if topic_tags else None,
                 source, time.time()),
            )
        except sqlite3.IntegrityError:
            # Already exists — update everything except the natural key
            conn.execute(
                "UPDATE question_bank SET "
                " chapter = ?, options_json = ?, correct_answer = ?, "
                " marks = ?, difficulty = ?, topic_tags = ?, source = ? "
                "WHERE board = ? AND grade = ? AND subject = ? "
                "AND year IS ? AND paper IS ? AND question_text = ?",
                (chapter,
                 json.dumps(options) if options else None,
                 correct_answer, marks, difficulty,
                 json.dumps(topic_tags) if topic_tags else None,
                 source,
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
