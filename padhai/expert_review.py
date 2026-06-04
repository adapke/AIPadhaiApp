"""v3.7 — Expert review workflow.

Per gap-review §12 + §22.10 — AI alone cannot build trust for
Indian exams. The review identifies the missing primitive: an
expert workflow where verified subject-matter experts review +
correct AI-generated answers, approve question packs, and stamp
"verified by teacher" badges on content students see.

Different from v3.5 moderation (community policy / safety) —
this is **academic content quality**. Different from v3.1
accuracy benchmark (offline expert-curated datasets) — this is
**online review of student-facing answers**.

Three tables:
  experts                 verified expert profiles + subject specialisations
  expert_reviews          one row per (target, expert) review action
  expert_verifications    cached "is this content verified?" lookup
                          (denormalised for cheap reads at content render)

Targets the expert workflow operates on (`target_kind` enum):
  ai_answer       — provenance row from v3.1 citations
  qb_question     — J6 question_bank row
  pack            — exam pack / question pack / content pack
  lesson          — generated lesson body

Workflow:
  1. Expert applies via `apply_as_expert()` with credentials +
     subject specialisations
  2. Admin verifies → `approve_expert()` flips status to active
  3. Students or system flag content for review (via `request_review()`)
  4. Experts pick from queue, review + decide:
     • approve  → content gets a "verified" badge
     • correct  → expert provides corrected answer; original
                  flagged with correction
     • reject   → mark as low-quality; gets pulled from feeds
  5. Per-action commission booked to expert (R4 affiliates
     pattern — accumulate paise; payout async)

Public API:
  apply_as_expert(user_id, display_name, subjects, credentials)
  approve_expert(user_id)
  request_review(target_kind, target_id, requested_by, priority)
  list_review_queue(status='pending', subject=..., limit=50)
  decide_review(review_id, action, corrected_answer?, reason?)
  is_verified(target_kind, target_id) -> bool
  list_expert_subjects(user_id) -> list[str]
  expert_earnings_summary(user_id) -> dict
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
CREATE TABLE IF NOT EXISTS experts (
    user_id             TEXT PRIMARY KEY,
    display_name        TEXT NOT NULL,
    bio                 TEXT,
    credentials         TEXT,                       -- '15 yrs IIT-JEE Physics teacher'
    subjects            TEXT NOT NULL,              -- JSON array ['physics', 'chemistry']
    exam_codes          TEXT,                        -- JSON — which exams they're qualified for
    languages           TEXT,                        -- JSON
    status              TEXT NOT NULL DEFAULT 'applied',
    -- 'applied' | 'active' | 'paused' | 'suspended'
    rate_per_review_paise INTEGER NOT NULL DEFAULT 5000,    -- ₹50/review default
    total_reviews       INTEGER NOT NULL DEFAULT 0,
    total_earned_paise  INTEGER NOT NULL DEFAULT 0,
    rating_sum          REAL NOT NULL DEFAULT 0,
    rating_count        INTEGER NOT NULL DEFAULT 0,
    created_at          REAL NOT NULL,
    activated_at        REAL
);
CREATE INDEX IF NOT EXISTS idx_experts_status
    ON experts(status, total_reviews DESC);

CREATE TABLE IF NOT EXISTS expert_reviews (
    id                  TEXT PRIMARY KEY,
    target_kind         TEXT NOT NULL,              -- 'ai_answer' / 'qb_question' / 'pack' / 'lesson'
    target_id           TEXT NOT NULL,
    requested_by        TEXT,                        -- user_id or 'system'
    priority            INTEGER NOT NULL DEFAULT 5,  -- 1=high, 10=low
    subject_hint        TEXT,                        -- for routing to right expert
    status              TEXT NOT NULL DEFAULT 'pending',
    -- 'pending' | 'in_review' | 'approved' | 'corrected' | 'rejected' | 'expired'
    reviewer_user_id    TEXT,
    corrected_answer    TEXT,
    reason              TEXT,
    commission_paise    INTEGER,
    requested_at        REAL NOT NULL,
    claimed_at          REAL,
    decided_at          REAL,
    sla_due_at          REAL                         -- pending → expired after this
);
CREATE INDEX IF NOT EXISTS idx_er_status
    ON expert_reviews(status, priority, requested_at);
CREATE INDEX IF NOT EXISTS idx_er_reviewer
    ON expert_reviews(reviewer_user_id, decided_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_er_target
    ON expert_reviews(target_kind, target_id, status)
    WHERE status IN ('pending', 'in_review');

CREATE TABLE IF NOT EXISTS expert_verifications (
    id                  TEXT PRIMARY KEY,
    target_kind         TEXT NOT NULL,
    target_id           TEXT NOT NULL,
    reviewer_user_id    TEXT NOT NULL,
    review_id           TEXT NOT NULL,
    verified_at         REAL NOT NULL,
    -- Denormalised for cheap is_verified() lookups
    UNIQUE (target_kind, target_id)
);
CREATE INDEX IF NOT EXISTS idx_ev_reviewer
    ON expert_verifications(reviewer_user_id, verified_at DESC);
"""


VALID_TARGET_KINDS = frozenset({
    "ai_answer", "qb_question", "pack", "lesson",
})

VALID_EXPERT_STATUSES = frozenset({
    "applied", "active", "paused", "suspended",
})

VALID_REVIEW_STATUSES = frozenset({
    "pending", "in_review", "approved",
    "corrected", "rejected", "expired",
})

VALID_DECISIONS = frozenset({"approve", "correct", "reject"})


# Reviewer SLA — pending items expire after this many hours.
# Aged items get re-prioritised in the queue.
SLA_HOURS = 72

# Default commission per review action. Approve/correct earn
# full rate; reject earns half (less work).
DEFAULT_RATE_PAISE = 5000
REJECT_RATE_MULTIPLIER = 0.5

MIN_RATE_PAISE = 1000     # ₹10
MAX_RATE_PAISE = 50000     # ₹500


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
class Expert:
    user_id: str
    display_name: str
    bio: str | None
    credentials: str | None
    subjects: list[str]
    exam_codes: list[str]
    languages: list[str]
    status: str
    rate_per_review_paise: int
    total_reviews: int
    total_earned_paise: int
    rating_avg: float
    rating_count: int
    created_at: float
    activated_at: float | None


@dataclass(frozen=True)
class Review:
    id: str
    target_kind: str
    target_id: str
    requested_by: str | None
    priority: int
    subject_hint: str | None
    status: str
    reviewer_user_id: str | None
    corrected_answer: str | None
    reason: str | None
    commission_paise: int | None
    requested_at: float
    claimed_at: float | None
    decided_at: float | None
    sla_due_at: float | None


# ---------- expert lifecycle ----------

def apply_as_expert(
    *,
    user_id: str,
    display_name: str,
    subjects: list[str],
    credentials: str | None = None,
    bio: str | None = None,
    exam_codes: list[str] | None = None,
    languages: list[str] | None = None,
    rate_per_review_paise: int = DEFAULT_RATE_PAISE,
) -> Expert:
    display_name = (display_name or "").strip()
    if len(display_name) < 2 or len(display_name) > 100:
        raise ValueError("display_name must be [2, 100] chars")
    if not subjects:
        raise ValueError("at least one subject required")
    if len(subjects) > 20:
        raise ValueError("max 20 subjects per expert")
    if rate_per_review_paise < MIN_RATE_PAISE:
        raise ValueError(f"rate must be ≥ ₹{MIN_RATE_PAISE/100:.0f}")
    if rate_per_review_paise > MAX_RATE_PAISE:
        raise ValueError(f"rate must be ≤ ₹{MAX_RATE_PAISE/100:.0f}")
    try:
        with _conn() as conn:
            conn.execute(
                "INSERT INTO experts "
                "(user_id, display_name, bio, credentials, "
                " subjects, exam_codes, languages, "
                " rate_per_review_paise, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (user_id, display_name, bio, credentials,
                 json.dumps(subjects),
                 json.dumps(exam_codes) if exam_codes else None,
                 json.dumps(languages) if languages else None,
                 rate_per_review_paise, time.time()),
            )
    except sqlite3.IntegrityError as e:
        raise ValueError("already applied as expert") from e
    return get_expert(user_id)  # type: ignore[return-value]


def get_expert(user_id: str) -> Expert | None:
    with _conn() as conn:
        r = conn.execute(
            _E_SEL + " WHERE user_id = ?", (user_id,),
        ).fetchone()
    return _row_to_expert(r) if r else None


_E_SEL = (
    "SELECT user_id, display_name, bio, credentials, subjects, "
    "       exam_codes, languages, status, rate_per_review_paise, "
    "       total_reviews, total_earned_paise, "
    "       rating_sum, rating_count, created_at, activated_at "
    "FROM experts"
)


def _row_to_expert(r) -> Expert:
    rc = r[12] or 0
    return Expert(
        user_id=r[0], display_name=r[1], bio=r[2],
        credentials=r[3],
        subjects=json.loads(r[4]) if r[4] else [],
        exam_codes=json.loads(r[5]) if r[5] else [],
        languages=json.loads(r[6]) if r[6] else [],
        status=r[7], rate_per_review_paise=r[8],
        total_reviews=r[9], total_earned_paise=r[10],
        rating_avg=round((r[11] or 0) / rc, 2) if rc else 0.0,
        rating_count=rc,
        created_at=r[13], activated_at=r[14],
    )


def approve_expert(user_id: str) -> bool:
    """Admin path — promote 'applied' → 'active'."""
    now = time.time()
    with _conn() as conn:
        cur = conn.execute(
            "UPDATE experts SET status = 'active', activated_at = ? "
            "WHERE user_id = ? AND status = 'applied'",
            (now, user_id),
        )
        return cur.rowcount > 0


def set_expert_status(*, user_id: str, status: str) -> bool:
    if status not in VALID_EXPERT_STATUSES:
        raise ValueError(
            f"status must be in {sorted(VALID_EXPERT_STATUSES)}",
        )
    with _conn() as conn:
        cur = conn.execute(
            "UPDATE experts SET status = ? WHERE user_id = ?",
            (status, user_id),
        )
        return cur.rowcount > 0


def list_experts(
    *,
    status: str | None = None,
    subject: str | None = None,
    limit: int = 100,
) -> list[Expert]:
    limit = max(1, min(limit, 500))
    sql = _E_SEL
    where: list[str] = []
    params: list = []
    if status:
        if status not in VALID_EXPERT_STATUSES:
            raise ValueError(
                f"status must be in {sorted(VALID_EXPERT_STATUSES)}",
            )
        where.append("status = ?"); params.append(status)
    if subject:
        where.append("subjects LIKE ?"); params.append(f"%{subject}%")
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY total_reviews DESC, display_name LIMIT ?"
    params.append(limit)
    with _conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_row_to_expert(r) for r in rows]


def list_expert_subjects(user_id: str) -> list[str]:
    e = get_expert(user_id)
    return e.subjects if e else []


# ---------- review queue ----------

def request_review(
    *,
    target_kind: str,
    target_id: str,
    requested_by: str | None = None,
    priority: int = 5,
    subject_hint: str | None = None,
    sla_hours: int = SLA_HOURS,
) -> Review:
    """Add a review request to the queue. Idempotent on
    (target_kind, target_id) while a pending/in_review request
    exists — re-requesting returns the existing row."""
    if target_kind not in VALID_TARGET_KINDS:
        raise ValueError(
            f"target_kind must be in {sorted(VALID_TARGET_KINDS)}",
        )
    if priority < 1 or priority > 10:
        raise ValueError("priority must be in [1, 10]")
    target_id = (target_id or "").strip()
    if not target_id or len(target_id) > 64:
        raise ValueError("target_id required (max 64 chars)")
    rid = uuid.uuid4().hex
    now = time.time()
    sla_due = now + sla_hours * 3600
    try:
        with _conn() as conn:
            conn.execute(
                "INSERT INTO expert_reviews "
                "(id, target_kind, target_id, requested_by, "
                " priority, subject_hint, status, requested_at, "
                " sla_due_at) "
                "VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?)",
                (rid, target_kind, target_id, requested_by,
                 priority, subject_hint, now, sla_due),
            )
    except sqlite3.IntegrityError:
        # Already pending or in_review — return existing
        existing = _find_active_review(target_kind, target_id)
        if existing:
            return existing
        raise
    return _get_review(rid)  # type: ignore[return-value]


def _find_active_review(
    target_kind: str, target_id: str,
) -> Review | None:
    with _conn() as conn:
        r = conn.execute(
            _R_SEL + " WHERE target_kind = ? AND target_id = ? "
            "AND status IN ('pending', 'in_review')",
            (target_kind, target_id),
        ).fetchone()
    return _row_to_review(r) if r else None


_R_SEL = (
    "SELECT id, target_kind, target_id, requested_by, priority, "
    "       subject_hint, status, reviewer_user_id, "
    "       corrected_answer, reason, commission_paise, "
    "       requested_at, claimed_at, decided_at, sla_due_at "
    "FROM expert_reviews"
)


def _row_to_review(r) -> Review:
    return Review(
        id=r[0], target_kind=r[1], target_id=r[2],
        requested_by=r[3], priority=r[4],
        subject_hint=r[5], status=r[6],
        reviewer_user_id=r[7], corrected_answer=r[8],
        reason=r[9], commission_paise=r[10],
        requested_at=r[11], claimed_at=r[12],
        decided_at=r[13], sla_due_at=r[14],
    )


def _get_review(review_id: str) -> Review | None:
    with _conn() as conn:
        r = conn.execute(
            _R_SEL + " WHERE id = ?", (review_id,),
        ).fetchone()
    return _row_to_review(r) if r else None


def get_review(review_id: str) -> Review | None:
    return _get_review(review_id)


def list_review_queue(
    *,
    status: str = "pending",
    subject: str | None = None,
    target_kind: str | None = None,
    limit: int = 50,
) -> list[Review]:
    if status not in VALID_REVIEW_STATUSES:
        raise ValueError(
            f"status must be in {sorted(VALID_REVIEW_STATUSES)}",
        )
    if target_kind and target_kind not in VALID_TARGET_KINDS:
        raise ValueError(
            f"target_kind must be in {sorted(VALID_TARGET_KINDS)}",
        )
    limit = max(1, min(limit, 500))
    sql = _R_SEL + " WHERE status = ?"
    params: list = [status]
    if subject:
        sql += " AND subject_hint = ?"; params.append(subject)
    if target_kind:
        sql += " AND target_kind = ?"; params.append(target_kind)
    sql += " ORDER BY priority, requested_at LIMIT ?"
    params.append(limit)
    with _conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_row_to_review(r) for r in rows]


def claim_review(*, review_id: str, reviewer_user_id: str) -> Review:
    """Expert picks a review off the queue. Verifies expert is
    active + their subject matches subject_hint (if set).
    Atomic: only one expert wins the claim."""
    expert = get_expert(reviewer_user_id)
    if not expert:
        raise ValueError("not registered as expert")
    if expert.status != "active":
        raise ValueError(
            f"expert status is {expert.status!r}, not active",
        )
    review = _get_review(review_id)
    if not review:
        raise ValueError("review not found")
    if review.status != "pending":
        raise ValueError(
            f"review status is {review.status!r}, can't claim",
        )
    if (review.subject_hint
            and review.subject_hint not in expert.subjects):
        raise PermissionError(
            f"review subject_hint {review.subject_hint!r} "
            f"not in your subjects {expert.subjects}",
        )
    now = time.time()
    with _conn() as conn:
        cur = conn.execute(
            "UPDATE expert_reviews SET "
            " status = 'in_review', reviewer_user_id = ?, "
            " claimed_at = ? "
            "WHERE id = ? AND status = 'pending'",
            (reviewer_user_id, now, review_id),
        )
        if cur.rowcount == 0:
            raise ValueError("review claimed by another expert")
    return _get_review(review_id)  # type: ignore[return-value]


def decide_review(
    *,
    review_id: str,
    reviewer_user_id: str,
    action: str,
    corrected_answer: str | None = None,
    reason: str | None = None,
) -> Review:
    """Expert decision. Maps to status:
        approve → 'approved' + verification row created
        correct → 'corrected' + verification row with corrected_answer
        reject  → 'rejected' (content gets pulled from feeds)
    Books commission to the expert + bumps total_reviews."""
    if action not in VALID_DECISIONS:
        raise ValueError(
            f"action must be in {sorted(VALID_DECISIONS)}",
        )
    review = _get_review(review_id)
    if not review:
        raise ValueError("review not found")
    if review.reviewer_user_id != reviewer_user_id:
        raise PermissionError(
            "only the claiming reviewer can decide",
        )
    if review.status != "in_review":
        raise ValueError(
            f"review status is {review.status!r}, "
            "must be in_review",
        )
    if action == "correct" and not corrected_answer:
        raise ValueError(
            "corrected_answer required for action='correct'",
        )
    expert = get_expert(reviewer_user_id)
    if not expert:
        raise ValueError("expert profile vanished")
    status_map = {
        "approve": "approved", "correct": "corrected",
        "reject": "rejected",
    }
    new_status = status_map[action]
    # Commission math
    rate = expert.rate_per_review_paise
    commission = int(round(rate * REJECT_RATE_MULTIPLIER)) if action == "reject" else rate
    now = time.time()
    with _conn() as conn:
        conn.execute(
            "UPDATE expert_reviews SET "
            " status = ?, corrected_answer = ?, reason = ?, "
            " commission_paise = ?, decided_at = ? "
            "WHERE id = ?",
            (new_status, corrected_answer,
             (reason or "")[:1000], commission, now, review_id),
        )
        # Bump expert tallies
        conn.execute(
            "UPDATE experts SET "
            " total_reviews = total_reviews + 1, "
            " total_earned_paise = total_earned_paise + ? "
            "WHERE user_id = ?",
            (commission, reviewer_user_id),
        )
        # Create verification row for approve / correct (not reject)
        if action in ("approve", "correct"):
            try:  # noqa: SIM105
                conn.execute(
                    "INSERT INTO expert_verifications "
                    "(id, target_kind, target_id, "
                    " reviewer_user_id, review_id, verified_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (uuid.uuid4().hex, review.target_kind,
                     review.target_id, reviewer_user_id,
                     review_id, now),
                )
            except sqlite3.IntegrityError:
                # Already verified by an earlier reviewer — fine
                pass
    return _get_review(review_id)  # type: ignore[return-value]


def expire_stale_reviews() -> int:
    """Sweep — pending items past their SLA become 'expired'.
    Called by a cron / on dashboard read. Returns count expired."""
    now = time.time()
    with _conn() as conn:
        cur = conn.execute(
            "UPDATE expert_reviews SET status = 'expired' "
            "WHERE status = 'pending' AND sla_due_at < ?",
            (now,),
        )
    return cur.rowcount


# ---------- verification lookups ----------

def is_verified(
    *, target_kind: str, target_id: str,
) -> bool:
    """Cheap lookup — does this content have an approved/corrected
    expert review? Hit on every content render, so kept on the
    denormalised `expert_verifications` table."""
    if target_kind not in VALID_TARGET_KINDS:
        return False
    with _conn() as conn:
        r = conn.execute(
            "SELECT 1 FROM expert_verifications "
            "WHERE target_kind = ? AND target_id = ?",
            (target_kind, target_id),
        ).fetchone()
    return r is not None


def get_verification(
    *, target_kind: str, target_id: str,
) -> dict | None:
    with _conn() as conn:
        r = conn.execute(
            "SELECT id, reviewer_user_id, review_id, verified_at "
            "FROM expert_verifications "
            "WHERE target_kind = ? AND target_id = ?",
            (target_kind, target_id),
        ).fetchone()
    if not r:
        return None
    return {
        "id": r[0], "reviewer_user_id": r[1],
        "review_id": r[2], "verified_at": r[3],
    }


# ---------- ratings + earnings ----------

def rate_expert(
    *, expert_user_id: str, rating: int, rater_user_id: str,
) -> bool:
    """Student rates an expert's review. 1-5 stars. Roll-up
    feeds the expert's rating_avg + drives queue prioritisation."""
    if rating < 1 or rating > 5:
        raise ValueError("rating must be in [1, 5]")
    expert = get_expert(expert_user_id)
    if not expert:
        return False
    with _conn() as conn:
        conn.execute(
            "UPDATE experts SET "
            " rating_sum = rating_sum + ?, "
            " rating_count = rating_count + 1 "
            "WHERE user_id = ?",
            (rating, expert_user_id),
        )
    return True


def expert_earnings_summary(user_id: str) -> dict:
    expert = get_expert(user_id)
    if not expert:
        return {}
    with _conn() as conn:
        by_status = conn.execute(
            "SELECT status, COUNT(*), COALESCE(SUM(commission_paise), 0) "
            "FROM expert_reviews WHERE reviewer_user_id = ? "
            "GROUP BY status",
            (user_id,),
        ).fetchall()
    return {
        "total_reviews": expert.total_reviews,
        "total_earned_paise": expert.total_earned_paise,
        "rating_avg": expert.rating_avg,
        "rating_count": expert.rating_count,
        "by_status": {
            r[0]: {"count": r[1], "earned_paise": int(r[2] or 0)}
            for r in by_status
        },
    }


def queue_stats() -> dict:
    """Admin dashboard: how big's the queue + how aged?"""
    now = time.time()
    with _conn() as conn:
        by_status = conn.execute(
            "SELECT status, COUNT(*) FROM expert_reviews "
            "GROUP BY status",
        ).fetchall()
        breached = conn.execute(
            "SELECT COUNT(*) FROM expert_reviews "
            "WHERE status = 'pending' AND sla_due_at < ?",
            (now,),
        ).fetchone()
    return {
        "by_status": {r[0]: r[1] for r in by_status},
        "sla_breached_pending": int(breached[0] or 0),
    }
