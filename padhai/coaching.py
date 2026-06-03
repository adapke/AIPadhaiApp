"""K3 — UPSC / JEE / NEET coaching content + practice engine.

The Indian coaching market is ₹40k Cr+ annually. UPSC: 20L
aspirants/year. JEE: 15L. NEET: 15L. They pay ₹2L+/year/student
when the content is good. Different shape from K-12:

- **UPSC**: prelims MCQ practice (factual recall + reasoning),
  mains essay assistance (long-form Claude critique), current
  affairs daily digest (RSS-driven)
- **JEE**: physics/chem/math step-by-step worked examples, mock
  test analytics (subject-wise %, time-per-question)
- **NEET**: biology-heavy MCQ practice with detailed solutions

This module ships the **practice-engine schema** + a stub
recommendation function. Content ingest is the bulk of the work
(separate per-exam content sprint).
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
CREATE TABLE IF NOT EXISTS coaching_tracks (
    id           TEXT PRIMARY KEY,
    exam         TEXT NOT NULL,        -- 'upsc' | 'jee_main' | 'jee_advanced' | 'neet'
    name         TEXT NOT NULL,
    description  TEXT,
    subjects     TEXT,                  -- JSON array
    target_year  INTEGER,
    active       INTEGER NOT NULL DEFAULT 1,
    created_at   REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS practice_attempts (
    id              TEXT PRIMARY KEY,
    user_id         TEXT NOT NULL,
    exam            TEXT NOT NULL,
    subject         TEXT NOT NULL,
    question_id     TEXT,               -- backref to question_bank.id
    answered        TEXT,
    correct         INTEGER,             -- 0/1
    time_seconds    INTEGER,
    confidence      INTEGER,             -- 1..5
    created_at      REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_practice_user_time
    ON practice_attempts(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_practice_subj
    ON practice_attempts(user_id, exam, subject);

CREATE TABLE IF NOT EXISTS current_affairs (
    id           TEXT PRIMARY KEY,
    published_at REAL NOT NULL,
    title        TEXT NOT NULL,
    summary      TEXT NOT NULL,
    source_url   TEXT,
    topic_tags   TEXT,                   -- JSON array; 'polity', 'economy', 'science', 'intl'
    relevance    INTEGER NOT NULL DEFAULT 5,  -- 1..10
    exams        TEXT,                    -- JSON array of relevant exams
    created_at   REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ca_time ON current_affairs(published_at DESC);
"""

VALID_EXAMS = {"upsc", "jee_main", "jee_advanced", "neet",
                "cat", "gate"}


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
class Track:
    id: str
    exam: str
    name: str
    description: str | None
    subjects: list[str]
    target_year: int | None
    active: bool


def create_track(
    *, exam: str, name: str,
    description: str | None = None,
    subjects: list[str] | None = None,
    target_year: int | None = None,
) -> Track:
    if exam not in VALID_EXAMS:
        raise ValueError(f"exam must be in {sorted(VALID_EXAMS)}")
    tid = uuid.uuid4().hex
    with _conn() as conn:
        conn.execute(
            "INSERT INTO coaching_tracks "
            "(id, exam, name, description, subjects, target_year, "
            " active, created_at) VALUES (?,?,?,?,?,?,1,?)",
            (tid, exam, name, description,
             json.dumps(subjects) if subjects else None,
             target_year, time.time()),
        )
    return Track(
        id=tid, exam=exam, name=name, description=description,
        subjects=subjects or [], target_year=target_year, active=True,
    )


def list_tracks(*, exam: str | None = None) -> list[Track]:
    sql = ("SELECT id, exam, name, description, subjects, "
           "target_year, active FROM coaching_tracks WHERE active = 1")
    params: list = []
    if exam:
        sql += " AND exam = ?"; params.append(exam)
    sql += " ORDER BY name"
    with _conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [
        Track(
            id=r[0], exam=r[1], name=r[2], description=r[3],
            subjects=json.loads(r[4]) if r[4] else [],
            target_year=r[5], active=bool(r[6]),
        )
        for r in rows
    ]


def record_attempt(
    *,
    user_id: str, exam: str, subject: str,
    question_id: str | None = None,
    answered: str | None = None,
    correct: bool = False,
    time_seconds: int | None = None,
    confidence: int | None = None,
) -> str:
    """Log a practice question attempt. Drives the analytics +
    adaptive-difficulty recommendation."""
    if exam not in VALID_EXAMS:
        raise ValueError(f"exam must be in {sorted(VALID_EXAMS)}")
    aid = uuid.uuid4().hex
    with _conn() as conn:
        conn.execute(
            "INSERT INTO practice_attempts "
            "(id, user_id, exam, subject, question_id, answered, "
            " correct, time_seconds, confidence, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (aid, user_id, exam, subject, question_id, answered,
             1 if correct else 0, time_seconds, confidence,
             time.time()),
        )
    return aid


def subject_stats(*, user_id: str, exam: str) -> dict:
    """Subject-wise accuracy + time/q + count for a user."""
    with _conn() as conn:
        rows = conn.execute(
            "SELECT subject, COUNT(*) as n, "
            "       SUM(correct) as right_count, "
            "       AVG(time_seconds) as avg_time "
            "FROM practice_attempts "
            "WHERE user_id = ? AND exam = ? "
            "GROUP BY subject",
            (user_id, exam),
        ).fetchall()
    return {
        r[0]: {
            "attempts": r[1],
            "correct": r[2] or 0,
            "accuracy": (r[2] or 0) / r[1] if r[1] else 0,
            "avg_time_seconds": r[3],
        }
        for r in rows
    }


def weak_subjects(*, user_id: str, exam: str,
                  min_attempts: int = 5,
                  threshold: float = 0.6) -> list[str]:
    """Subjects where accuracy is below threshold AND attempts are
    >= min_attempts. Used by the recommender to point at problem
    areas."""
    stats = subject_stats(user_id=user_id, exam=exam)
    return [
        subj for subj, s in stats.items()
        if s["attempts"] >= min_attempts and s["accuracy"] < threshold
    ]


# ---------- Current affairs (UPSC-focused) ----------

def add_current_affair(
    *,
    title: str, summary: str,
    source_url: str | None = None,
    topic_tags: list[str] | None = None,
    relevance: int = 5,
    exams: list[str] | None = None,
    published_at: float | None = None,
) -> str:
    cid = uuid.uuid4().hex
    now = time.time()
    with _conn() as conn:
        conn.execute(
            "INSERT INTO current_affairs "
            "(id, published_at, title, summary, source_url, "
            " topic_tags, relevance, exams, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (cid, published_at or now, title, summary, source_url,
             json.dumps(topic_tags) if topic_tags else None,
             max(1, min(10, relevance)),
             json.dumps(exams) if exams else None, now),
        )
    return cid


def daily_digest(*, exam: str = "upsc", limit: int = 20) -> list[dict]:
    """Recent current affairs filtered by exam relevance. Used by the
    UPSC daily-digest module."""
    since = time.time() - 86400 * 2  # 48h window
    with _conn() as conn:
        rows = conn.execute(
            "SELECT id, published_at, title, summary, source_url, "
            "       topic_tags, relevance, exams "
            "FROM current_affairs "
            "WHERE published_at >= ? "
            "ORDER BY relevance DESC, published_at DESC LIMIT ?",
            (since, limit),
        ).fetchall()
    out = []
    for r in rows:
        ex = json.loads(r[7]) if r[7] else []
        if exam and ex and exam not in ex:
            continue
        out.append({
            "id": r[0], "published_at": r[1], "title": r[2],
            "summary": r[3], "source_url": r[4],
            "topic_tags": json.loads(r[5]) if r[5] else [],
            "relevance": r[6], "exams": ex,
        })
    return out
