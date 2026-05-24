"""N4 — Senior-mentor program.

Senior students (12th board passers, college students, alumni) mentor
younger ones (8th–10th, JEE/NEET aspirants). Time-banked: each hour
of completed mentoring earns 1 month of free Pro (M3) tier.

Three tables:
  mentor_profiles   per-user mentor application + earned hours
  mentor_sessions   scheduled mentoring slots
  mentor_reviews    mentee rating + feedback

Lifecycle:
  apply → admin approve → active (can be paused / suspended)
  session: scheduled → completed (logs hours)  /  cancelled / no_show
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
CREATE TABLE IF NOT EXISTS mentor_profiles (
    user_id            TEXT PRIMARY KEY,
    bio                TEXT,
    expertise_subjects TEXT,                 -- JSON array
    expertise_exams    TEXT,                 -- JSON array
    available_hours_week INTEGER NOT NULL DEFAULT 2,
    year_of_passing    INTEGER,
    college            TEXT,
    languages          TEXT,                 -- JSON array
    status             TEXT NOT NULL DEFAULT 'applied',
    -- applied | approved | active | paused | suspended
    hours_logged       REAL NOT NULL DEFAULT 0,
    free_months_earned INTEGER NOT NULL DEFAULT 0,
    rating_sum         REAL NOT NULL DEFAULT 0,
    rating_count       INTEGER NOT NULL DEFAULT 0,
    created_at         REAL NOT NULL,
    updated_at         REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_mentor_status
    ON mentor_profiles(status, available_hours_week DESC);

CREATE TABLE IF NOT EXISTS mentor_sessions (
    id              TEXT PRIMARY KEY,
    mentor_user_id  TEXT NOT NULL,
    mentee_user_id  TEXT NOT NULL,
    scheduled_at    REAL NOT NULL,
    duration_min    INTEGER NOT NULL DEFAULT 30,
    topic           TEXT,
    status          TEXT NOT NULL DEFAULT 'scheduled',
    -- scheduled | completed | cancelled | no_show
    notes           TEXT,                     -- mentor's session notes
    started_at      REAL,
    completed_at    REAL,
    actual_duration_min INTEGER,
    created_at      REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_msess_mentor
    ON mentor_sessions(mentor_user_id, scheduled_at DESC);
CREATE INDEX IF NOT EXISTS idx_msess_mentee
    ON mentor_sessions(mentee_user_id, scheduled_at DESC);
CREATE INDEX IF NOT EXISTS idx_msess_status
    ON mentor_sessions(status, scheduled_at);

CREATE TABLE IF NOT EXISTS mentor_reviews (
    id              TEXT PRIMARY KEY,
    session_id      TEXT NOT NULL,
    reviewer_user_id TEXT NOT NULL,
    rating          INTEGER NOT NULL,
    feedback        TEXT,
    created_at      REAL NOT NULL,
    UNIQUE (session_id, reviewer_user_id)
);
"""


VALID_STATUSES = frozenset({
    "applied", "approved", "active", "paused", "suspended",
})

VALID_SESSION_STATUSES = frozenset({
    "scheduled", "completed", "cancelled", "no_show",
})


# Hours needed per free Pro month earned. Tunable; default = 1.
HOURS_PER_FREE_MONTH = 1.0


def _db_path() -> Path:
    custom = os.environ.get("PADHAI_DB_PATH")
    if custom:
        return Path(custom).expanduser()
    return Path.home() / ".padhai" / "jobs.db"


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
class MentorProfile:
    user_id: str
    bio: str | None
    expertise_subjects: list[str]
    expertise_exams: list[str]
    available_hours_week: int
    year_of_passing: int | None
    college: str | None
    languages: list[str]
    status: str
    hours_logged: float
    free_months_earned: int
    rating_avg: float
    rating_count: int
    created_at: float
    updated_at: float


@dataclass(frozen=True)
class Session:
    id: str
    mentor_user_id: str
    mentee_user_id: str
    scheduled_at: float
    duration_min: int
    topic: str | None
    status: str
    notes: str | None
    started_at: float | None
    completed_at: float | None
    actual_duration_min: int | None
    created_at: float


# ---------- mentor profile ----------

def apply(
    *,
    user_id: str,
    bio: str | None = None,
    expertise_subjects: list[str] | None = None,
    expertise_exams: list[str] | None = None,
    available_hours_week: int = 2,
    year_of_passing: int | None = None,
    college: str | None = None,
    languages: list[str] | None = None,
) -> MentorProfile:
    """Submit mentor application. Status begins 'applied'; admin
    promotes to 'approved' → 'active'."""
    if available_hours_week < 1 or available_hours_week > 40:
        raise ValueError("available_hours_week must be in [1, 40]")
    if year_of_passing is not None and (
        year_of_passing < 2000 or year_of_passing > 2100
    ):
        raise ValueError("year_of_passing must be in [2000, 2100]")
    now = time.time()
    with _conn() as conn:
        existing = conn.execute(
            "SELECT user_id FROM mentor_profiles WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        if existing:
            raise ValueError("already applied")
        conn.execute(
            "INSERT INTO mentor_profiles "
            "(user_id, bio, expertise_subjects, expertise_exams, "
            " available_hours_week, year_of_passing, college, "
            " languages, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'applied', ?, ?)",
            (user_id, bio,
             json.dumps(expertise_subjects) if expertise_subjects else None,
             json.dumps(expertise_exams) if expertise_exams else None,
             available_hours_week, year_of_passing, college,
             json.dumps(languages) if languages else None,
             now, now),
        )
    return get_profile(user_id)  # type: ignore[return-value]


def get_profile(user_id: str) -> MentorProfile | None:
    with _conn() as conn:
        r = conn.execute(
            "SELECT user_id, bio, expertise_subjects, expertise_exams, "
            "       available_hours_week, year_of_passing, college, "
            "       languages, status, hours_logged, free_months_earned, "
            "       rating_sum, rating_count, created_at, updated_at "
            "FROM mentor_profiles WHERE user_id = ?", (user_id,),
        ).fetchone()
    if not r:
        return None
    rating_count = r[12] or 0
    rating_avg = (r[11] / rating_count) if rating_count else 0.0
    return MentorProfile(
        user_id=r[0], bio=r[1],
        expertise_subjects=json.loads(r[2]) if r[2] else [],
        expertise_exams=json.loads(r[3]) if r[3] else [],
        available_hours_week=r[4],
        year_of_passing=r[5], college=r[6],
        languages=json.loads(r[7]) if r[7] else [],
        status=r[8], hours_logged=r[9],
        free_months_earned=r[10],
        rating_avg=round(rating_avg, 2),
        rating_count=rating_count,
        created_at=r[13], updated_at=r[14],
    )


def set_mentor_status(*, user_id: str, status: str) -> bool:
    if status not in VALID_STATUSES:
        raise ValueError(f"status must be in {sorted(VALID_STATUSES)}")
    with _conn() as conn:
        cur = conn.execute(
            "UPDATE mentor_profiles SET status = ?, updated_at = ? "
            "WHERE user_id = ?",
            (status, time.time(), user_id),
        )
        return cur.rowcount > 0


def list_active_mentors(
    *,
    subject: str | None = None,
    exam: str | None = None,
    limit: int = 30,
) -> list[MentorProfile]:
    """Filter active mentors. Subject/exam filter is a substring match
    on the JSON arrays — sufficient at our scale; switches to proper
    JSON1 + GIN index when we move to Postgres."""
    limit = max(1, min(limit, 200))
    sql = (
        "SELECT user_id, bio, expertise_subjects, expertise_exams, "
        "       available_hours_week, year_of_passing, college, "
        "       languages, status, hours_logged, free_months_earned, "
        "       rating_sum, rating_count, created_at, updated_at "
        "FROM mentor_profiles WHERE status = 'active'"
    )
    params: list = []
    if subject:
        sql += " AND expertise_subjects LIKE ?"; params.append(f"%{subject}%")
    if exam:
        sql += " AND expertise_exams LIKE ?"; params.append(f"%{exam}%")
    sql += " ORDER BY rating_count DESC, hours_logged DESC LIMIT ?"
    params.append(limit)
    with _conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    out = []
    for r in rows:
        rating_count = r[12] or 0
        rating_avg = (r[11] / rating_count) if rating_count else 0.0
        out.append(MentorProfile(
            user_id=r[0], bio=r[1],
            expertise_subjects=json.loads(r[2]) if r[2] else [],
            expertise_exams=json.loads(r[3]) if r[3] else [],
            available_hours_week=r[4],
            year_of_passing=r[5], college=r[6],
            languages=json.loads(r[7]) if r[7] else [],
            status=r[8], hours_logged=r[9],
            free_months_earned=r[10],
            rating_avg=round(rating_avg, 2),
            rating_count=rating_count,
            created_at=r[13], updated_at=r[14],
        ))
    return out


# ---------- sessions ----------

def request_session(
    *,
    mentor_user_id: str,
    mentee_user_id: str,
    scheduled_at: float,
    duration_min: int = 30,
    topic: str | None = None,
) -> Session:
    """Mentee requests a session with an active mentor."""
    if mentor_user_id == mentee_user_id:
        raise ValueError("mentor and mentee must differ")
    if duration_min < 15 or duration_min > 180:
        raise ValueError("duration_min must be in [15, 180]")
    mentor = get_profile(mentor_user_id)
    if not mentor:
        raise ValueError("mentor profile not found")
    if mentor.status != "active":
        raise ValueError(f"mentor status is {mentor.status!r}, not active")
    sid = uuid.uuid4().hex
    with _conn() as conn:
        conn.execute(
            "INSERT INTO mentor_sessions "
            "(id, mentor_user_id, mentee_user_id, scheduled_at, "
            " duration_min, topic, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 'scheduled', ?)",
            (sid, mentor_user_id, mentee_user_id, scheduled_at,
             duration_min, topic, time.time()),
        )
    return get_session(sid)  # type: ignore[return-value]


def get_session(session_id: str) -> Session | None:
    with _conn() as conn:
        r = conn.execute(
            _S_SEL + " WHERE id = ?", (session_id,),
        ).fetchone()
    return _row_to_session(r) if r else None


_S_SEL = (
    "SELECT id, mentor_user_id, mentee_user_id, scheduled_at, "
    "       duration_min, topic, status, notes, started_at, "
    "       completed_at, actual_duration_min, created_at "
    "FROM mentor_sessions"
)


def _row_to_session(r) -> Session:
    return Session(
        id=r[0], mentor_user_id=r[1], mentee_user_id=r[2],
        scheduled_at=r[3], duration_min=r[4], topic=r[5],
        status=r[6], notes=r[7],
        started_at=r[8], completed_at=r[9],
        actual_duration_min=r[10], created_at=r[11],
    )


def list_sessions_for_user(
    user_id: str, *, role: str | None = None, limit: int = 50,
) -> list[Session]:
    """Sessions where `user_id` is mentor or mentee (or both, when
    role is None)."""
    limit = max(1, min(limit, 500))
    if role == "mentor":
        where = "mentor_user_id = ?"
        params: list = [user_id]
    elif role == "mentee":
        where = "mentee_user_id = ?"
        params = [user_id]
    else:
        where = "(mentor_user_id = ? OR mentee_user_id = ?)"
        params = [user_id, user_id]
    with _conn() as conn:
        rows = conn.execute(
            _S_SEL + f" WHERE {where} ORDER BY scheduled_at DESC LIMIT ?",
            (*params, limit),
        ).fetchall()
    return [_row_to_session(r) for r in rows]


def complete_session(
    *,
    session_id: str,
    mentor_user_id: str,
    actual_duration_min: int | None = None,
    notes: str | None = None,
) -> Session:
    """Mentor marks complete + logs actual duration. Side effect:
    bumps mentor's `hours_logged` + recomputes `free_months_earned`."""
    s = get_session(session_id)
    if not s:
        raise ValueError("session not found")
    if s.mentor_user_id != mentor_user_id:
        raise PermissionError("only the mentor can complete")
    if s.status != "scheduled":
        raise ValueError(f"session status is {s.status!r}")
    duration = max(0, min(
        actual_duration_min if actual_duration_min is not None else s.duration_min,
        300,
    ))
    hours = duration / 60.0
    now = time.time()
    with _conn() as conn:
        conn.execute(
            "UPDATE mentor_sessions SET "
            " status = 'completed', notes = ?, "
            " completed_at = ?, actual_duration_min = ? "
            "WHERE id = ?",
            (notes, now, duration, session_id),
        )
        # Bump mentor hours + recompute free months
        conn.execute(
            "UPDATE mentor_profiles SET "
            " hours_logged = hours_logged + ?, "
            " free_months_earned = CAST(("
            "   (hours_logged + ?) / ?) AS INTEGER), "
            " updated_at = ? "
            "WHERE user_id = ?",
            (hours, hours, HOURS_PER_FREE_MONTH, now, mentor_user_id),
        )
    return get_session(session_id)  # type: ignore[return-value]


def cancel_session(
    *,
    session_id: str,
    user_id: str,
) -> bool:
    """Either party can cancel a scheduled session."""
    s = get_session(session_id)
    if not s:
        return False
    if user_id not in (s.mentor_user_id, s.mentee_user_id):
        return False
    if s.status != "scheduled":
        return False
    with _conn() as conn:
        conn.execute(
            "UPDATE mentor_sessions SET status = 'cancelled' "
            "WHERE id = ?", (session_id,),
        )
    return True


def mark_no_show(*, session_id: str, mentor_user_id: str) -> bool:
    """Mentor marks a no-show — different from cancel for analytics."""
    with _conn() as conn:
        cur = conn.execute(
            "UPDATE mentor_sessions SET status = 'no_show' "
            "WHERE id = ? AND mentor_user_id = ? AND status = 'scheduled'",
            (session_id, mentor_user_id),
        )
        return cur.rowcount > 0


# ---------- reviews ----------

def review_session(
    *,
    session_id: str,
    reviewer_user_id: str,
    rating: int,
    feedback: str | None = None,
) -> str:
    """Mentee rates the mentor after a completed session. One review
    per (session, reviewer)."""
    if rating < 1 or rating > 5:
        raise ValueError("rating must be in [1, 5]")
    s = get_session(session_id)
    if not s:
        raise ValueError("session not found")
    if reviewer_user_id != s.mentee_user_id:
        raise PermissionError("only the mentee can review")
    if s.status != "completed":
        raise ValueError("session must be completed before review")
    rid = uuid.uuid4().hex
    try:
        with _conn() as conn:
            conn.execute(
                "INSERT INTO mentor_reviews "
                "(id, session_id, reviewer_user_id, rating, "
                " feedback, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (rid, session_id, reviewer_user_id, rating,
                 feedback, time.time()),
            )
            # Bump mentor rating rollup
            conn.execute(
                "UPDATE mentor_profiles SET "
                " rating_sum = rating_sum + ?, "
                " rating_count = rating_count + 1, "
                " updated_at = ? "
                "WHERE user_id = ?",
                (rating, time.time(), s.mentor_user_id),
            )
    except sqlite3.IntegrityError as e:
        raise ValueError("already reviewed this session") from e
    return rid


def list_reviews(*, mentor_user_id: str, limit: int = 20) -> list[dict]:
    """Recent reviews for a mentor's public profile page."""
    limit = max(1, min(limit, 200))
    with _conn() as conn:
        rows = conn.execute(
            "SELECT r.id, r.session_id, r.reviewer_user_id, "
            "       r.rating, r.feedback, r.created_at "
            "FROM mentor_reviews r "
            "JOIN mentor_sessions s ON s.id = r.session_id "
            "WHERE s.mentor_user_id = ? "
            "ORDER BY r.created_at DESC LIMIT ?",
            (mentor_user_id, limit),
        ).fetchall()
    return [
        {"id": r[0], "session_id": r[1],
         "reviewer_user_id": r[2], "rating": r[3],
         "feedback": r[4], "created_at": r[5]}
        for r in rows
    ]
