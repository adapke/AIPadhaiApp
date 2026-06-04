"""M3 — Live mock-test sessions.

Synchronous mock-test events (UPSC prelims, JEE Main, NEET). Countdown
starts together; all students see results + leaderboard at the same
moment. Sense of cohort + exam-pressure realism.

Reuses:
  - J6 question_bank for the question set
  - J5 mastery (feedback loop after submit)
  - L5 practice_test infrastructure where possible

This module ships the **event scheduling + per-user attempt** layer.
The frontend countdown + leaderboard widget consumes the same
endpoints.

Two tables:
  mock_test_events     scheduled event with question_set + timing
  mock_test_attempts   per-user attempt with answers + score + rank
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
CREATE TABLE IF NOT EXISTS mock_test_events (
    id              TEXT PRIMARY KEY,
    title           TEXT NOT NULL,
    exam            TEXT NOT NULL,
    subject         TEXT,
    scheduled_at    REAL NOT NULL,
    duration_min    INTEGER NOT NULL DEFAULT 60,
    question_set_json TEXT NOT NULL,           -- [{id, question_text, options, correct_answer, marks, difficulty, topic_tags}]
    max_participants INTEGER NOT NULL DEFAULT 5000,
    status          TEXT NOT NULL DEFAULT 'scheduled',
    -- scheduled | open_for_registration | live | ended | cancelled
    started_at      REAL,
    ended_at        REAL,
    created_by      TEXT,
    created_at      REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_mte_time ON mock_test_events(scheduled_at DESC);
CREATE INDEX IF NOT EXISTS idx_mte_status ON mock_test_events(status, scheduled_at);
CREATE INDEX IF NOT EXISTS idx_mte_exam ON mock_test_events(exam, scheduled_at DESC);

CREATE TABLE IF NOT EXISTS mock_test_registrations (
    event_id     TEXT NOT NULL,
    user_id      TEXT NOT NULL,
    registered_at REAL NOT NULL,
    PRIMARY KEY (event_id, user_id)
);

CREATE TABLE IF NOT EXISTS mock_test_attempts (
    id              TEXT PRIMARY KEY,
    event_id        TEXT NOT NULL,
    user_id         TEXT NOT NULL,
    started_at      REAL,
    submitted_at    REAL,
    answers_json    TEXT,
    score           REAL,
    max_score       REAL,
    rank            INTEGER,
    percentile      REAL,
    UNIQUE (event_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_mta_event_score
    ON mock_test_attempts(event_id, score DESC);
"""


VALID_EXAMS = frozenset({
    "upsc_pre", "upsc_mains", "jee_main", "jee_advanced",
    "neet", "cat", "gate", "generic",
})

VALID_STATUSES = frozenset({
    "scheduled", "open_for_registration", "live",
    "ended", "cancelled",
})


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


# ---------- dataclasses ----------

@dataclass(frozen=True)
class MockEvent:
    id: str
    title: str
    exam: str
    subject: str | None
    scheduled_at: float
    duration_min: int
    question_set: list[dict]
    max_participants: int
    status: str
    started_at: float | None
    ended_at: float | None
    created_by: str | None
    created_at: float


@dataclass(frozen=True)
class Attempt:
    id: str
    event_id: str
    user_id: str
    started_at: float | None
    submitted_at: float | None
    answers: dict | None
    score: float | None
    max_score: float | None
    rank: int | None
    percentile: float | None


_EVENT_SEL = (
    "id, title, exam, subject, scheduled_at, duration_min, "
    "question_set_json, max_participants, status, started_at, "
    "ended_at, created_by, created_at"
)


def _row_to_event(r) -> MockEvent:
    return MockEvent(
        id=r[0], title=r[1], exam=r[2], subject=r[3],
        scheduled_at=r[4], duration_min=r[5],
        question_set=json.loads(r[6]) if r[6] else [],
        max_participants=r[7], status=r[8],
        started_at=r[9], ended_at=r[10],
        created_by=r[11], created_at=r[12],
    )


def _row_to_attempt(r) -> Attempt:
    return Attempt(
        id=r[0], event_id=r[1], user_id=r[2],
        started_at=r[3], submitted_at=r[4],
        answers=json.loads(r[5]) if r[5] else None,
        score=r[6], max_score=r[7], rank=r[8], percentile=r[9],
    )


# ---------- event CRUD ----------

def schedule_event(
    *,
    title: str,
    exam: str,
    scheduled_at: float,
    duration_min: int,
    question_set: list[dict],
    subject: str | None = None,
    max_participants: int = 5000,
    created_by: str | None = None,
) -> MockEvent:
    if exam not in VALID_EXAMS:
        raise ValueError(f"exam must be in {sorted(VALID_EXAMS)}")
    if duration_min < 5 or duration_min > 480:
        raise ValueError("duration_min must be in [5, 480]")
    if max_participants < 2 or max_participants > 100000:
        raise ValueError("max_participants must be in [2, 100000]")
    if not question_set:
        raise ValueError("question_set required (non-empty)")
    if len(question_set) > 200:
        raise ValueError("question_set too large (max 200)")
    title = (title or "").strip()
    if len(title) < 4:
        raise ValueError("title required (min 4 chars)")
    eid = uuid.uuid4().hex
    # Normalize question rows
    normalized = []
    for q in question_set:
        if not isinstance(q, dict):
            continue
        normalized.append({
            "id": q.get("id") or uuid.uuid4().hex[:12],
            "question_text": q.get("question_text", ""),
            "options": q.get("options"),
            "correct_answer": q.get("correct_answer"),
            "marks": q.get("marks", 1),
            "difficulty": q.get("difficulty"),
            "topic_tags": q.get("topic_tags") or [],
        })
    if not normalized:
        raise ValueError("no valid questions in question_set")
    with _conn() as conn:
        conn.execute(
            "INSERT INTO mock_test_events "
            "(id, title, exam, subject, scheduled_at, duration_min, "
            " question_set_json, max_participants, status, "
            " created_by, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, "
            "        'open_for_registration', ?, ?)",
            (eid, title, exam, subject, scheduled_at, duration_min,
             json.dumps(normalized), max_participants,
             created_by, time.time()),
        )
    return get_event(eid)  # type: ignore[return-value]


def get_event(event_id: str) -> MockEvent | None:
    with _conn() as conn:
        r = conn.execute(
            f"SELECT {_EVENT_SEL} FROM mock_test_events WHERE id = ?",
            (event_id,),
        ).fetchone()
    return _row_to_event(r) if r else None


def list_upcoming_events(
    *, exam: str | None = None, window_hours: float = 168.0,
) -> list[MockEvent]:
    sql = (
        f"SELECT {_EVENT_SEL} FROM mock_test_events "
        "WHERE status IN ('scheduled', 'open_for_registration', 'live') "
        "AND scheduled_at <= ?"
    )
    params: list = [time.time() + window_hours * 3600]
    if exam:
        sql += " AND exam = ?"; params.append(exam)
    sql += " ORDER BY scheduled_at"
    with _conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_row_to_event(r) for r in rows]


def set_event_status(*, event_id: str, status: str) -> bool:
    if status not in VALID_STATUSES:
        raise ValueError(f"status must be in {sorted(VALID_STATUSES)}")
    now = time.time()
    extra = ""
    extras: list = []
    if status == "live":
        extra = ", started_at = COALESCE(started_at, ?)"
        extras.append(now)
    elif status == "ended":
        extra = ", ended_at = COALESCE(ended_at, ?)"
        extras.append(now)
    with _conn() as conn:
        cur = conn.execute(
            f"UPDATE mock_test_events SET status = ?{extra} "
            "WHERE id = ?",
            (status, *extras, event_id),
        )
        return cur.rowcount > 0


# ---------- registration ----------

def register(*, event_id: str, user_id: str) -> bool:
    """Idempotent registration. Returns True if newly registered;
    False if already registered or event closed."""
    e = get_event(event_id)
    if not e:
        return False
    if e.status not in ("scheduled", "open_for_registration"):
        return False
    try:
        with _conn() as conn:
            conn.execute(
                "INSERT INTO mock_test_registrations "
                "(event_id, user_id, registered_at) "
                "VALUES (?, ?, ?)",
                (event_id, user_id, time.time()),
            )
        return True
    except sqlite3.IntegrityError:
        return False


def is_registered(*, event_id: str, user_id: str) -> bool:
    with _conn() as conn:
        r = conn.execute(
            "SELECT 1 FROM mock_test_registrations "
            "WHERE event_id = ? AND user_id = ?",
            (event_id, user_id),
        ).fetchone()
    return r is not None


def registered_count(event_id: str) -> int:
    with _conn() as conn:
        r = conn.execute(
            "SELECT COUNT(*) FROM mock_test_registrations "
            "WHERE event_id = ?", (event_id,),
        ).fetchone()
    return int(r[0] or 0)


# ---------- attempts ----------

def start_attempt(*, event_id: str, user_id: str) -> Attempt:
    """Begin the attempt clock. Idempotent — re-starting returns the
    existing attempt with its original `started_at`."""
    e = get_event(event_id)
    if not e:
        raise ValueError(f"event {event_id!r} not found")
    if e.status not in ("live", "open_for_registration"):
        raise ValueError(f"event status is {e.status!r}, not joinable")
    if not is_registered(event_id=event_id, user_id=user_id):
        raise ValueError("user not registered for this event")
    now = time.time()
    aid = uuid.uuid4().hex
    with _conn() as conn:
        existing = conn.execute(
            "SELECT id FROM mock_test_attempts "
            "WHERE event_id = ? AND user_id = ?",
            (event_id, user_id),
        ).fetchone()
        if existing:
            return _get_attempt_by_id(existing[0])
        conn.execute(
            "INSERT INTO mock_test_attempts "
            "(id, event_id, user_id, started_at) "
            "VALUES (?, ?, ?, ?)",
            (aid, event_id, user_id, now),
        )
    return _get_attempt_by_id(aid)


def _get_attempt_by_id(attempt_id: str) -> Attempt:
    with _conn() as conn:
        r = conn.execute(
            "SELECT id, event_id, user_id, started_at, submitted_at, "
            "       answers_json, score, max_score, rank, percentile "
            "FROM mock_test_attempts WHERE id = ?",
            (attempt_id,),
        ).fetchone()
    if not r:
        raise ValueError(f"attempt {attempt_id!r} not found")
    return _row_to_attempt(r)


def submit_attempt(
    *,
    event_id: str,
    user_id: str,
    answers: dict[str, str],
) -> Attempt:
    """Grade the attempt + persist. Rank + percentile are
    recomputed across all attempts on the event."""
    e = get_event(event_id)
    if not e:
        raise ValueError(f"event {event_id!r} not found")
    # Score
    total = 0.0
    max_score = 0.0
    for q in e.question_set:
        marks = float(q.get("marks", 1) or 1)
        max_score += marks
        chosen = (answers.get(q["id"]) or "").strip().lower()
        correct = (q.get("correct_answer") or "").strip().lower()
        if chosen and correct and chosen.startswith(correct[:1]):
            total += marks

    with _conn() as conn:
        # Find or create attempt
        existing = conn.execute(
            "SELECT id FROM mock_test_attempts "
            "WHERE event_id = ? AND user_id = ?",
            (event_id, user_id),
        ).fetchone()
        if existing:
            attempt_id = existing[0]
            conn.execute(
                "UPDATE mock_test_attempts SET "
                " submitted_at = ?, answers_json = ?, "
                " score = ?, max_score = ? "
                "WHERE id = ? AND submitted_at IS NULL",
                (time.time(), json.dumps(answers),
                 total, max_score, attempt_id),
            )
        else:
            attempt_id = uuid.uuid4().hex
            conn.execute(
                "INSERT INTO mock_test_attempts "
                "(id, event_id, user_id, started_at, submitted_at, "
                " answers_json, score, max_score) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (attempt_id, event_id, user_id, time.time(),
                 time.time(), json.dumps(answers), total, max_score),
            )

    # Recompute ranks
    _recompute_ranks(event_id)

    # Feed mastery
    try:
        from . import mastery
        for q in e.question_set:
            chosen = (answers.get(q["id"]) or "").strip().lower()
            correct = (q.get("correct_answer") or "").strip().lower()
            is_correct = bool(chosen) and bool(correct) and chosen.startswith(correct[:1])
            for tag in q.get("topic_tags") or []:
                mastery.update(
                    user_id=user_id, topic_key=tag,
                    correct=is_correct,
                )
    except Exception as e_inner:  # noqa: BLE001
        print(f"[mock_test_event] mastery update non-fatal: {e_inner}")

    return _get_attempt_by_id(attempt_id)


def _recompute_ranks(event_id: str) -> None:
    """Refresh rank + percentile for every submitted attempt.
    Cheap at our scale; if M3 grows to 100k concurrent, push this
    to a Redis-backed leaderboard."""
    with _conn() as conn:
        rows = conn.execute(
            "SELECT id, score FROM mock_test_attempts "
            "WHERE event_id = ? AND submitted_at IS NOT NULL "
            "ORDER BY score DESC, submitted_at",
            (event_id,),
        ).fetchall()
        total = len(rows)
        if total == 0:
            return
        for rank, (aid, _score) in enumerate(rows, start=1):
            percentile = ((total - rank) / total) * 100 if total > 1 else 100.0
            conn.execute(
                "UPDATE mock_test_attempts SET "
                " rank = ?, percentile = ? WHERE id = ?",
                (rank, round(percentile, 2), aid),
            )


def leaderboard(event_id: str, *, limit: int = 100) -> list[dict]:
    """Top-N attempts by score for this event. Used by the live
    leaderboard widget that updates every few seconds during the
    event + freezes at the end."""
    limit = max(1, min(limit, 1000))
    with _conn() as conn:
        rows = conn.execute(
            "SELECT user_id, score, max_score, rank, percentile, "
            "       submitted_at "
            "FROM mock_test_attempts "
            "WHERE event_id = ? AND submitted_at IS NOT NULL "
            "ORDER BY score DESC, submitted_at LIMIT ?",
            (event_id, limit),
        ).fetchall()
    return [
        {"user_id": r[0], "score": r[1], "max_score": r[2],
         "rank": r[3], "percentile": r[4],
         "submitted_at": r[5]}
        for r in rows
    ]


def get_my_attempt(*, event_id: str, user_id: str) -> Attempt | None:
    with _conn() as conn:
        r = conn.execute(
            "SELECT id, event_id, user_id, started_at, submitted_at, "
            "       answers_json, score, max_score, rank, percentile "
            "FROM mock_test_attempts "
            "WHERE event_id = ? AND user_id = ?",
            (event_id, user_id),
        ).fetchone()
    return _row_to_attempt(r) if r else None


def stats(event_id: str) -> dict:
    e = get_event(event_id)
    if not e:
        return {"event_id": event_id, "found": False}
    with _conn() as conn:
        registered = conn.execute(
            "SELECT COUNT(*) FROM mock_test_registrations WHERE event_id = ?",
            (event_id,),
        ).fetchone()[0]
        started = conn.execute(
            "SELECT COUNT(*) FROM mock_test_attempts "
            "WHERE event_id = ? AND started_at IS NOT NULL",
            (event_id,),
        ).fetchone()[0]
        submitted = conn.execute(
            "SELECT COUNT(*) FROM mock_test_attempts "
            "WHERE event_id = ? AND submitted_at IS NOT NULL",
            (event_id,),
        ).fetchone()[0]
        avg_score = conn.execute(
            "SELECT AVG(score) FROM mock_test_attempts "
            "WHERE event_id = ? AND submitted_at IS NOT NULL",
            (event_id,),
        ).fetchone()[0]
    return {
        "event_id": event_id, "found": True,
        "registered": registered, "started": started,
        "submitted": submitted,
        "avg_score": round(avg_score or 0, 2),
        "title": e.title, "status": e.status,
        "scheduled_at": e.scheduled_at, "duration_min": e.duration_min,
    }
