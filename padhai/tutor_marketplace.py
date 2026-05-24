"""M4 — 1:1 tutor marketplace.

Independent tutors register + set their own hourly rate. Students
book 30/60/90-min sessions. Platform takes 20% commission by default
(configurable per tutor for top earners).

Different from N4 (the volunteer-mentor program) — M4 is **paid**
with escrow + payouts. N4 is **time-banked** (1h mentoring → free
Pro month).

Three tables:
  marketplace_tutors      paid tutor profiles + payout config
  marketplace_bookings    scheduled sessions with escrow + payment
  marketplace_reviews     student rating + feedback per booking

Booking lifecycle:
  requested → confirmed → in_progress → completed → reviewed
  any → cancelled / refunded / no_show
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
CREATE TABLE IF NOT EXISTS marketplace_tutors (
    id                TEXT PRIMARY KEY,
    user_id           TEXT NOT NULL,
    display_name      TEXT NOT NULL,
    bio               TEXT,
    avatar_url        TEXT,
    exams             TEXT,                    -- JSON array
    subjects          TEXT,                    -- JSON array
    languages         TEXT,                    -- JSON array
    rate_inr_per_30min INTEGER NOT NULL,
    platform_fee_pct  REAL NOT NULL DEFAULT 0.20,
    payout_account_id TEXT,
    verified          INTEGER NOT NULL DEFAULT 0,
    status            TEXT NOT NULL DEFAULT 'applied',
    -- 'applied' | 'active' | 'paused' | 'suspended'
    total_earnings_paise INTEGER NOT NULL DEFAULT 0,
    booking_count     INTEGER NOT NULL DEFAULT 0,
    rating_sum        REAL NOT NULL DEFAULT 0,
    rating_count      INTEGER NOT NULL DEFAULT 0,
    created_at        REAL NOT NULL,
    updated_at        REAL NOT NULL,
    UNIQUE (user_id)
);
CREATE INDEX IF NOT EXISTS idx_mt_status
    ON marketplace_tutors(status, rate_inr_per_30min);

CREATE TABLE IF NOT EXISTS marketplace_bookings (
    id                    TEXT PRIMARY KEY,
    tutor_user_id         TEXT NOT NULL,
    student_user_id       TEXT NOT NULL,
    scheduled_at          REAL NOT NULL,
    duration_min          INTEGER NOT NULL,
    topic                 TEXT,
    price_paise           INTEGER NOT NULL,
    platform_fee_paise    INTEGER NOT NULL,
    tutor_payout_paise    INTEGER NOT NULL,
    status                TEXT NOT NULL DEFAULT 'requested',
    -- 'requested' | 'confirmed' | 'in_progress' | 'completed'
    -- | 'cancelled' | 'no_show' | 'refunded'
    payment_status        TEXT NOT NULL DEFAULT 'held',
    -- 'held' (escrowed) | 'paid_out' | 'refunded'
    created_at            REAL NOT NULL,
    confirmed_at          REAL,
    started_at            REAL,
    completed_at          REAL,
    cancelled_at          REAL,
    cancellation_reason   TEXT
);
CREATE INDEX IF NOT EXISTS idx_mb_tutor
    ON marketplace_bookings(tutor_user_id, scheduled_at DESC);
CREATE INDEX IF NOT EXISTS idx_mb_student
    ON marketplace_bookings(student_user_id, scheduled_at DESC);
CREATE INDEX IF NOT EXISTS idx_mb_status
    ON marketplace_bookings(status, scheduled_at);

CREATE TABLE IF NOT EXISTS marketplace_reviews (
    id              TEXT PRIMARY KEY,
    booking_id      TEXT NOT NULL,
    reviewer_user_id TEXT NOT NULL,
    rating          INTEGER NOT NULL,
    feedback        TEXT,
    created_at      REAL NOT NULL,
    UNIQUE (booking_id, reviewer_user_id)
);
"""


VALID_TUTOR_STATUSES = frozenset({
    "applied", "active", "paused", "suspended",
})

VALID_BOOKING_STATUSES = frozenset({
    "requested", "confirmed", "in_progress",
    "completed", "cancelled", "no_show", "refunded",
})

VALID_DURATION_MIN = frozenset({30, 60, 90, 120})

# Floor + ceiling on tutor rates — keep marketplace pricing sane
# while letting top tutors charge real money.
MIN_RATE_PAISE = 5000        # ₹50/30min
MAX_RATE_PAISE = 500000      # ₹5000/30min


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
class Tutor:
    id: str
    user_id: str
    display_name: str
    bio: str | None
    avatar_url: str | None
    exams: list[str]
    subjects: list[str]
    languages: list[str]
    rate_inr_per_30min: int
    platform_fee_pct: float
    payout_account_id: str | None
    verified: bool
    status: str
    total_earnings_paise: int
    booking_count: int
    rating_avg: float
    rating_count: int


@dataclass(frozen=True)
class Booking:
    id: str
    tutor_user_id: str
    student_user_id: str
    scheduled_at: float
    duration_min: int
    topic: str | None
    price_paise: int
    platform_fee_paise: int
    tutor_payout_paise: int
    status: str
    payment_status: str
    created_at: float
    confirmed_at: float | None
    started_at: float | None
    completed_at: float | None
    cancelled_at: float | None
    cancellation_reason: str | None


def _row_to_tutor(r) -> Tutor:
    rc = r[15] or 0
    return Tutor(
        id=r[0], user_id=r[1], display_name=r[2], bio=r[3],
        avatar_url=r[4],
        exams=json.loads(r[5]) if r[5] else [],
        subjects=json.loads(r[6]) if r[6] else [],
        languages=json.loads(r[7]) if r[7] else [],
        rate_inr_per_30min=r[8],
        platform_fee_pct=r[9],
        payout_account_id=r[10], verified=bool(r[11]),
        status=r[12],
        total_earnings_paise=r[13],
        booking_count=r[14],
        rating_avg=round((r[16] or 0) / rc, 2) if rc else 0.0,
        rating_count=rc,
    )


_TUTOR_SEL = (
    "SELECT id, user_id, display_name, bio, avatar_url, "
    "       exams, subjects, languages, rate_inr_per_30min, "
    "       platform_fee_pct, payout_account_id, verified, status, "
    "       total_earnings_paise, booking_count, "
    "       rating_count, rating_sum "
    "FROM marketplace_tutors"
)


def _row_to_booking(r) -> Booking:
    return Booking(
        id=r[0], tutor_user_id=r[1], student_user_id=r[2],
        scheduled_at=r[3], duration_min=r[4], topic=r[5],
        price_paise=r[6], platform_fee_paise=r[7],
        tutor_payout_paise=r[8], status=r[9],
        payment_status=r[10], created_at=r[11],
        confirmed_at=r[12], started_at=r[13],
        completed_at=r[14], cancelled_at=r[15],
        cancellation_reason=r[16],
    )


_BOOK_SEL = (
    "SELECT id, tutor_user_id, student_user_id, scheduled_at, "
    "       duration_min, topic, price_paise, platform_fee_paise, "
    "       tutor_payout_paise, status, payment_status, "
    "       created_at, confirmed_at, started_at, completed_at, "
    "       cancelled_at, cancellation_reason "
    "FROM marketplace_bookings"
)


# ---------- tutor lifecycle ----------

def apply_as_tutor(
    *,
    user_id: str,
    display_name: str,
    rate_inr_per_30min: int,
    bio: str | None = None,
    avatar_url: str | None = None,
    exams: list[str] | None = None,
    subjects: list[str] | None = None,
    languages: list[str] | None = None,
) -> Tutor:
    display_name = (display_name or "").strip()
    if len(display_name) < 2 or len(display_name) > 80:
        raise ValueError("display_name must be [2, 80] chars")
    if rate_inr_per_30min * 100 < MIN_RATE_PAISE:
        raise ValueError(
            f"rate must be ≥ ₹{MIN_RATE_PAISE/100:.0f}",
        )
    if rate_inr_per_30min * 100 > MAX_RATE_PAISE:
        raise ValueError(
            f"rate must be ≤ ₹{MAX_RATE_PAISE/100:.0f}",
        )
    tid = uuid.uuid4().hex
    now = time.time()
    try:
        with _conn() as conn:
            conn.execute(
                "INSERT INTO marketplace_tutors "
                "(id, user_id, display_name, bio, avatar_url, "
                " exams, subjects, languages, rate_inr_per_30min, "
                " status, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'applied', ?, ?)",
                (tid, user_id, display_name, bio, avatar_url,
                 json.dumps(exams) if exams else None,
                 json.dumps(subjects) if subjects else None,
                 json.dumps(languages) if languages else None,
                 rate_inr_per_30min, now, now),
            )
    except sqlite3.IntegrityError as e:
        raise ValueError("already applied as tutor") from e
    return get_tutor_by_user(user_id)  # type: ignore[return-value]


def get_tutor(tutor_id: str) -> Tutor | None:
    with _conn() as conn:
        r = conn.execute(
            _TUTOR_SEL + " WHERE id = ?", (tutor_id,),
        ).fetchone()
    return _row_to_tutor(r) if r else None


def get_tutor_by_user(user_id: str) -> Tutor | None:
    with _conn() as conn:
        r = conn.execute(
            _TUTOR_SEL + " WHERE user_id = ?", (user_id,),
        ).fetchone()
    return _row_to_tutor(r) if r else None


def set_tutor_status(*, user_id: str, status: str) -> bool:
    if status not in VALID_TUTOR_STATUSES:
        raise ValueError(
            f"status must be in {sorted(VALID_TUTOR_STATUSES)}",
        )
    with _conn() as conn:
        cur = conn.execute(
            "UPDATE marketplace_tutors SET "
            " status = ?, updated_at = ? "
            "WHERE user_id = ?",
            (status, time.time(), user_id),
        )
        return cur.rowcount > 0


def approve_tutor(*, user_id: str, verified: bool = True) -> bool:
    """Admin path — promote 'applied' tutor to 'active'."""
    with _conn() as conn:
        cur = conn.execute(
            "UPDATE marketplace_tutors SET "
            " status = 'active', verified = ?, updated_at = ? "
            "WHERE user_id = ? AND status = 'applied'",
            (1 if verified else 0, time.time(), user_id),
        )
        return cur.rowcount > 0


def search_tutors(
    *,
    exam: str | None = None,
    subject: str | None = None,
    language: str | None = None,
    max_rate: int | None = None,
    limit: int = 30,
) -> list[Tutor]:
    """Active tutors only. Substring match on JSON arrays — good
    enough at our scale; switches to JSON1 + GIN when on Postgres."""
    limit = max(1, min(limit, 200))
    sql = _TUTOR_SEL + " WHERE status = 'active'"
    params: list = []
    if exam:
        sql += " AND exams LIKE ?"; params.append(f"%{exam}%")
    if subject:
        sql += " AND subjects LIKE ?"; params.append(f"%{subject}%")
    if language:
        sql += " AND languages LIKE ?"; params.append(f"%{language}%")
    if max_rate is not None:
        sql += " AND rate_inr_per_30min <= ?"; params.append(max_rate)
    sql += " ORDER BY rating_count DESC, booking_count DESC LIMIT ?"
    params.append(limit)
    with _conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_row_to_tutor(r) for r in rows]


# ---------- bookings ----------

def book_session(
    *,
    tutor_user_id: str,
    student_user_id: str,
    scheduled_at: float,
    duration_min: int = 30,
    topic: str | None = None,
) -> Booking:
    if tutor_user_id == student_user_id:
        raise ValueError("tutor and student must differ")
    if duration_min not in VALID_DURATION_MIN:
        raise ValueError(
            f"duration_min must be in {sorted(VALID_DURATION_MIN)}",
        )
    tutor = get_tutor_by_user(tutor_user_id)
    if not tutor:
        raise ValueError("tutor profile not found")
    if tutor.status != "active":
        raise ValueError(f"tutor status is {tutor.status!r}, not active")
    # Price = rate × (duration_min/30); paise = INR×100
    blocks = duration_min // 30
    price_paise = tutor.rate_inr_per_30min * 100 * blocks
    platform_fee = int(round(price_paise * tutor.platform_fee_pct))
    tutor_payout = price_paise - platform_fee
    bid = uuid.uuid4().hex
    with _conn() as conn:
        conn.execute(
            "INSERT INTO marketplace_bookings "
            "(id, tutor_user_id, student_user_id, scheduled_at, "
            " duration_min, topic, price_paise, platform_fee_paise, "
            " tutor_payout_paise, status, payment_status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, "
            "        'requested', 'held', ?)",
            (bid, tutor_user_id, student_user_id, scheduled_at,
             duration_min, topic, price_paise, platform_fee,
             tutor_payout, time.time()),
        )
    return get_booking(bid)  # type: ignore[return-value]


def get_booking(booking_id: str) -> Booking | None:
    with _conn() as conn:
        r = conn.execute(
            _BOOK_SEL + " WHERE id = ?", (booking_id,),
        ).fetchone()
    return _row_to_booking(r) if r else None


def list_bookings(
    *,
    user_id: str,
    role: str | None = None,
    status: str | None = None,
    limit: int = 50,
) -> list[Booking]:
    limit = max(1, min(limit, 500))
    if role == "tutor":
        where = "tutor_user_id = ?"
        params: list = [user_id]
    elif role == "student":
        where = "student_user_id = ?"
        params = [user_id]
    else:
        where = "(tutor_user_id = ? OR student_user_id = ?)"
        params = [user_id, user_id]
    sql = _BOOK_SEL + f" WHERE {where}"
    if status:
        if status not in VALID_BOOKING_STATUSES:
            raise ValueError(
                f"status must be in {sorted(VALID_BOOKING_STATUSES)}"
            )
        sql += " AND status = ?"; params.append(status)
    sql += " ORDER BY scheduled_at DESC LIMIT ?"
    params.append(limit)
    with _conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_row_to_booking(r) for r in rows]


def confirm_booking(*, booking_id: str, tutor_user_id: str) -> Booking:
    """Tutor accepts the booking. After this, both parties expect the
    session to happen."""
    b = get_booking(booking_id)
    if not b:
        raise ValueError("booking not found")
    if b.tutor_user_id != tutor_user_id:
        raise PermissionError("only the tutor can confirm")
    if b.status != "requested":
        raise ValueError(f"booking is {b.status!r}, can't confirm")
    with _conn() as conn:
        conn.execute(
            "UPDATE marketplace_bookings SET "
            " status = 'confirmed', confirmed_at = ? "
            "WHERE id = ?",
            (time.time(), booking_id),
        )
    return get_booking(booking_id)  # type: ignore[return-value]


def start_session(*, booking_id: str, user_id: str) -> Booking:
    """Either party marks the session as started. Useful when video
    starts (WebRTC peer-connection joined)."""
    b = get_booking(booking_id)
    if not b:
        raise ValueError("booking not found")
    if user_id not in (b.tutor_user_id, b.student_user_id):
        raise PermissionError("not your booking")
    if b.status != "confirmed":
        raise ValueError(f"booking is {b.status!r}, can't start")
    now = time.time()
    with _conn() as conn:
        conn.execute(
            "UPDATE marketplace_bookings SET "
            " status = 'in_progress', started_at = ? "
            "WHERE id = ?",
            (now, booking_id),
        )
    return get_booking(booking_id)  # type: ignore[return-value]


def complete_booking(*, booking_id: str, tutor_user_id: str) -> Booking:
    """Tutor marks session complete → escrowed payment releases to
    the tutor (we record payment_status='paid_out'; actual Razorpay
    payout is a separate cron). Bumps tutor.total_earnings."""
    b = get_booking(booking_id)
    if not b:
        raise ValueError("booking not found")
    if b.tutor_user_id != tutor_user_id:
        raise PermissionError("only the tutor can complete")
    if b.status not in ("confirmed", "in_progress"):
        raise ValueError(f"booking is {b.status!r}, can't complete")
    now = time.time()
    with _conn() as conn:
        conn.execute(
            "UPDATE marketplace_bookings SET "
            " status = 'completed', completed_at = ?, "
            " payment_status = 'paid_out' "
            "WHERE id = ?",
            (now, booking_id),
        )
        conn.execute(
            "UPDATE marketplace_tutors SET "
            " total_earnings_paise = total_earnings_paise + ?, "
            " booking_count = booking_count + 1, "
            " updated_at = ? "
            "WHERE user_id = ?",
            (b.tutor_payout_paise, now, tutor_user_id),
        )
    return get_booking(booking_id)  # type: ignore[return-value]


def cancel_booking(
    *,
    booking_id: str,
    user_id: str,
    reason: str | None = None,
    refund: bool = True,
) -> Booking:
    """Either party can cancel a requested / confirmed booking.
    refund=True (default) sets payment_status='refunded'."""
    b = get_booking(booking_id)
    if not b:
        raise ValueError("booking not found")
    if user_id not in (b.tutor_user_id, b.student_user_id):
        raise PermissionError("not your booking")
    if b.status not in ("requested", "confirmed"):
        raise ValueError(f"booking is {b.status!r}, can't cancel")
    now = time.time()
    final_status = "refunded" if refund else "cancelled"
    payment_status = "refunded" if refund else "held"
    with _conn() as conn:
        conn.execute(
            "UPDATE marketplace_bookings SET "
            " status = ?, payment_status = ?, "
            " cancelled_at = ?, cancellation_reason = ? "
            "WHERE id = ?",
            (final_status, payment_status, now,
             (reason or "")[:500], booking_id),
        )
    return get_booking(booking_id)  # type: ignore[return-value]


def mark_no_show(*, booking_id: str, tutor_user_id: str) -> bool:
    """Tutor marks student no-show → keeps payment (consumed slot)."""
    with _conn() as conn:
        cur = conn.execute(
            "UPDATE marketplace_bookings SET "
            " status = 'no_show', payment_status = 'paid_out' "
            "WHERE id = ? AND tutor_user_id = ? "
            "AND status IN ('requested', 'confirmed', 'in_progress')",
            (booking_id, tutor_user_id),
        )
        if cur.rowcount > 0:
            b = get_booking(booking_id)
            if b:
                conn.execute(
                    "UPDATE marketplace_tutors SET "
                    " total_earnings_paise = total_earnings_paise + ?, "
                    " updated_at = ? "
                    "WHERE user_id = ?",
                    (b.tutor_payout_paise, time.time(), tutor_user_id),
                )
        return cur.rowcount > 0


# ---------- reviews ----------

def review_booking(
    *,
    booking_id: str,
    reviewer_user_id: str,
    rating: int,
    feedback: str | None = None,
) -> str:
    if rating < 1 or rating > 5:
        raise ValueError("rating must be in [1, 5]")
    b = get_booking(booking_id)
    if not b:
        raise ValueError("booking not found")
    if reviewer_user_id != b.student_user_id:
        raise PermissionError("only the student can review")
    if b.status != "completed":
        raise ValueError("booking must be completed before review")
    rid = uuid.uuid4().hex
    try:
        with _conn() as conn:
            conn.execute(
                "INSERT INTO marketplace_reviews "
                "(id, booking_id, reviewer_user_id, rating, "
                " feedback, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (rid, booking_id, reviewer_user_id, rating,
                 feedback, time.time()),
            )
            conn.execute(
                "UPDATE marketplace_tutors SET "
                " rating_sum = rating_sum + ?, "
                " rating_count = rating_count + 1, "
                " updated_at = ? "
                "WHERE user_id = ?",
                (rating, time.time(), b.tutor_user_id),
            )
    except sqlite3.IntegrityError as e:
        raise ValueError("already reviewed this booking") from e
    return rid


def list_reviews(*, tutor_user_id: str, limit: int = 20) -> list[dict]:
    limit = max(1, min(limit, 200))
    with _conn() as conn:
        rows = conn.execute(
            "SELECT r.id, r.booking_id, r.reviewer_user_id, "
            "       r.rating, r.feedback, r.created_at "
            "FROM marketplace_reviews r "
            "JOIN marketplace_bookings b ON b.id = r.booking_id "
            "WHERE b.tutor_user_id = ? "
            "ORDER BY r.created_at DESC LIMIT ?",
            (tutor_user_id, limit),
        ).fetchall()
    return [
        {"id": r[0], "booking_id": r[1],
         "reviewer_user_id": r[2], "rating": r[3],
         "feedback": r[4], "created_at": r[5]}
        for r in rows
    ]


def tutor_earnings_summary(tutor_user_id: str) -> dict:
    with _conn() as conn:
        r = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(tutor_payout_paise), 0) "
            "FROM marketplace_bookings "
            "WHERE tutor_user_id = ? AND status = 'completed'",
            (tutor_user_id,),
        ).fetchone()
        completed = int(r[0] or 0)
        payout = int(r[1] or 0)
        r2 = conn.execute(
            "SELECT COUNT(*) FROM marketplace_bookings "
            "WHERE tutor_user_id = ? AND status = 'requested'",
            (tutor_user_id,),
        ).fetchone()
        requested = int(r2[0] or 0)
    return {
        "completed_sessions": completed,
        "pending_requests": requested,
        "total_payout_paise": payout,
    }
