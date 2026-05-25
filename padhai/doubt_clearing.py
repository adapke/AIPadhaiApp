"""M2 — Live doubt clearing.

Student photographs their notebook, presses "ask doubt". Tutor (or
AI in L1) sees the image + their typed question in a queue, responds
via audio + annotated image.

Flow:
  1. Student POSTs `/api/doubts` with image_url + question_text
  2. Server queues, returns `doubt_id`
  3. Tutor pulls from queue (or AI takes over via L1 escalation)
  4. Tutor responds with response_text + optional response_image_url +
     response_audio_url
  5. Student sees response in their dashboard / push notification

When no human tutor claims within `PADHAI_DOUBT_AI_ESCALATE_MIN`
minutes (default 15), the doubt auto-escalates to the L1 tutor —
Claude vision reads the image + answers the question.

Single table: `doubt_requests`. Status flow:
  pending → claimed → answered (terminal)
  pending → ai_answered (terminal)
  pending → cancelled (terminal)
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
CREATE TABLE IF NOT EXISTS doubt_requests (
    id                TEXT PRIMARY KEY,
    user_id           TEXT NOT NULL,
    org_id            TEXT,
    subject           TEXT,
    question_text     TEXT NOT NULL,
    image_url         TEXT,                  -- student's notebook photo
    audio_url         TEXT,                  -- optional student voice clip
    status            TEXT NOT NULL DEFAULT 'pending',
    -- pending | claimed | answered | ai_answered | cancelled
    assigned_tutor_id TEXT,
    claimed_at        REAL,
    response_text     TEXT,
    response_image_url TEXT,
    response_audio_url TEXT,
    response_method   TEXT,                  -- 'human' | 'ai'
    response_at       REAL,
    ai_call_id        TEXT,                  -- backref to llm_obs.llm_calls
    created_at        REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_doubt_user_time
    ON doubt_requests(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_doubt_status_age
    ON doubt_requests(status, created_at);
CREATE INDEX IF NOT EXISTS idx_doubt_tutor
    ON doubt_requests(assigned_tutor_id, status);
"""


VALID_STATUSES = frozenset({
    "pending", "claimed", "answered", "ai_answered", "cancelled",
})


def _db_path() -> Path:
    custom = os.environ.get("PADHAI_DB_PATH")
    if custom:
        return Path(custom).expanduser()
    return Path.home() / ".padhai" / "jobs.db"


def _use_postgres() -> bool:
    return os.environ.get("DATABASE_URL", "").startswith("postgres")


_schema_applied = False


class _DB:
    """Thin wrapper around sqlite3 / psycopg that normalises ? → %s for
    Postgres and handles schema bootstrap once per process."""

    def __init__(self) -> None:
        global _schema_applied
        self._pg = _use_postgres()
        if self._pg:
            import psycopg  # type: ignore[import]
            self._conn = psycopg.connect(os.environ["DATABASE_URL"])
            if not _schema_applied:
                for stmt in [s.strip() for s in SCHEMA.split(";") if s.strip()]:
                    self._conn.execute(stmt)
                self._conn.commit()
                _schema_applied = True
        else:
            path = _db_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(path), timeout=10.0)
            if not _schema_applied:
                self._conn.executescript(SCHEMA)
                _schema_applied = True

    def execute(self, sql: str, params=()):
        if self._pg:
            sql = sql.replace("?", "%s")
        return self._conn.execute(sql, params)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is None:
            self._conn.commit()
        else:
            self._conn.rollback()
        self._conn.close()


def _conn() -> _DB:
    return _DB()


def migrate() -> None:
    with _conn():
        pass


# ---------- dataclass ----------

@dataclass(frozen=True)
class Doubt:
    id: str
    user_id: str
    org_id: str | None
    subject: str | None
    question_text: str
    image_url: str | None
    audio_url: str | None
    status: str
    assigned_tutor_id: str | None
    claimed_at: float | None
    response_text: str | None
    response_image_url: str | None
    response_audio_url: str | None
    response_method: str | None
    response_at: float | None
    ai_call_id: str | None
    created_at: float


_SEL = (
    "id, user_id, org_id, subject, question_text, image_url, "
    "audio_url, status, assigned_tutor_id, claimed_at, "
    "response_text, response_image_url, response_audio_url, "
    "response_method, response_at, ai_call_id, created_at"
)


def _row_to_doubt(r) -> Doubt:
    return Doubt(
        id=r[0], user_id=r[1], org_id=r[2], subject=r[3],
        question_text=r[4], image_url=r[5], audio_url=r[6],
        status=r[7], assigned_tutor_id=r[8], claimed_at=r[9],
        response_text=r[10], response_image_url=r[11],
        response_audio_url=r[12], response_method=r[13],
        response_at=r[14], ai_call_id=r[15], created_at=r[16],
    )


# ---------- write paths ----------

def submit(
    *,
    user_id: str,
    question_text: str,
    org_id: str | None = None,
    subject: str | None = None,
    image_url: str | None = None,
    audio_url: str | None = None,
) -> Doubt:
    question_text = (question_text or "").strip()
    if len(question_text) < 5:
        raise ValueError("question_text too short (min 5 chars)")
    if len(question_text) > 4000:
        raise ValueError("question_text too long (max 4000 chars)")
    if not (image_url or audio_url or question_text):
        raise ValueError("at least one of image / audio / text required")
    did = uuid.uuid4().hex
    with _conn() as conn:
        conn.execute(
            "INSERT INTO doubt_requests "
            "(id, user_id, org_id, subject, question_text, "
            " image_url, audio_url, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?)",
            (did, user_id, org_id, subject, question_text,
             image_url, audio_url, time.time()),
        )
    return get(did)  # type: ignore[return-value]


def claim(*, doubt_id: str, tutor_user_id: str) -> bool:
    """Tutor claims a pending doubt. Returns True if the claim
    succeeded (race-safe: only the first claimer wins)."""
    with _conn() as conn:
        cur = conn.execute(
            "UPDATE doubt_requests SET "
            " status = 'claimed', assigned_tutor_id = ?, "
            " claimed_at = ? "
            "WHERE id = ? AND status = 'pending'",
            (tutor_user_id, time.time(), doubt_id),
        )
        return cur.rowcount > 0


def answer(
    *,
    doubt_id: str,
    response_text: str,
    response_image_url: str | None = None,
    response_audio_url: str | None = None,
    method: str = "human",
    ai_call_id: str | None = None,
) -> bool:
    """Mark doubt answered. `method` distinguishes human vs ai
    responses — the latter drives the L1 escalation flow + open-loop
    quality review."""
    if method not in ("human", "ai"):
        raise ValueError("method must be 'human' or 'ai'")
    status = "ai_answered" if method == "ai" else "answered"
    with _conn() as conn:
        cur = conn.execute(
            "UPDATE doubt_requests SET "
            " status = ?, response_text = ?, "
            " response_image_url = ?, response_audio_url = ?, "
            " response_method = ?, response_at = ?, "
            " ai_call_id = COALESCE(?, ai_call_id) "
            "WHERE id = ? AND status IN ('pending', 'claimed')",
            (status, response_text, response_image_url,
             response_audio_url, method, time.time(),
             ai_call_id, doubt_id),
        )
        return cur.rowcount > 0


def cancel(*, doubt_id: str, user_id: str) -> bool:
    """Student cancels their own doubt before someone answers."""
    with _conn() as conn:
        cur = conn.execute(
            "UPDATE doubt_requests SET status = 'cancelled' "
            "WHERE id = ? AND user_id = ? "
            "AND status IN ('pending', 'claimed')",
            (doubt_id, user_id),
        )
        return cur.rowcount > 0


# ---------- read paths ----------

def get(doubt_id: str) -> Doubt | None:
    with _conn() as conn:
        r = conn.execute(
            f"SELECT {_SEL} FROM doubt_requests WHERE id = ?",
            (doubt_id,),
        ).fetchone()
    return _row_to_doubt(r) if r else None


def list_for_user(user_id: str, *, limit: int = 50) -> list[Doubt]:
    limit = max(1, min(limit, 500))
    with _conn() as conn:
        rows = conn.execute(
            f"SELECT {_SEL} FROM doubt_requests "
            "WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
    return [_row_to_doubt(r) for r in rows]


def queue(
    *,
    org_id: str | None = None,
    subject: str | None = None,
    limit: int = 50,
) -> list[Doubt]:
    """Pending doubts a tutor can claim, oldest-first. Filterable by
    org + subject so tutors only see their queue."""
    limit = max(1, min(limit, 500))
    sql = (
        f"SELECT {_SEL} FROM doubt_requests "
        "WHERE status = 'pending'"
    )
    params: list = []
    if org_id is not None:
        sql += " AND (org_id = ? OR org_id IS NULL)"
        params.append(org_id)
    if subject is not None:
        sql += " AND (subject = ? OR subject IS NULL)"
        params.append(subject)
    sql += " ORDER BY created_at LIMIT ?"
    params.append(limit)
    with _conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_row_to_doubt(r) for r in rows]


def stale_for_ai_escalation(*, minutes: float | None = None) -> list[Doubt]:
    """Doubts that have been pending too long. Worker cron picks
    these up + runs L1 AI escalation."""
    if minutes is None:
        try:
            minutes = float(
                os.environ.get("PADHAI_DOUBT_AI_ESCALATE_MIN", "15"),
            )
        except ValueError:
            minutes = 15.0
    cutoff = time.time() - minutes * 60
    with _conn() as conn:
        rows = conn.execute(
            f"SELECT {_SEL} FROM doubt_requests "
            "WHERE status = 'pending' AND created_at < ? "
            "ORDER BY created_at LIMIT 100",
            (cutoff,),
        ).fetchall()
    return [_row_to_doubt(r) for r in rows]


def stats(*, hours: float = 24.0) -> dict:
    since = time.time() - hours * 3600
    with _conn() as conn:
        total = conn.execute(
            "SELECT COUNT(*) FROM doubt_requests WHERE created_at >= ?",
            (since,),
        ).fetchone()[0]
        by_status = conn.execute(
            "SELECT status, COUNT(*) FROM doubt_requests "
            "WHERE created_at >= ? GROUP BY status",
            (since,),
        ).fetchall()
        by_method = conn.execute(
            "SELECT response_method, COUNT(*) FROM doubt_requests "
            "WHERE created_at >= ? AND response_method IS NOT NULL "
            "GROUP BY response_method",
            (since,),
        ).fetchall()
        avg_response_seconds = conn.execute(
            "SELECT AVG(response_at - created_at) FROM doubt_requests "
            "WHERE created_at >= ? AND response_at IS NOT NULL",
            (since,),
        ).fetchone()[0]
    return {
        "hours": hours,
        "total": int(total),
        "by_status": {r[0]: r[1] for r in by_status},
        "by_method": {r[0]: r[1] for r in by_method},
        "avg_response_seconds": int(avg_response_seconds or 0),
    }
