"""O1 — Teacher publishing platform.

Anyone can record + sell a lesson series. AI Pathshala hosts, renders
the avatar + voice (via the v0.6 pipeline), serves videos via CDN
(G3), and handles payments + revenue share.

Default split: **70/30** (creator/platform). Configurable per-deal
for premium teachers (90/10 for top-grossing) via creator.platform_fee_pct.

Four tables:
  published_creators    one row per onboarded creator
  published_series      one row per lesson series
  published_lessons     one row per lesson inside a series
  series_purchases      one row per student → series transaction
"""

from __future__ import annotations

import os
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS published_creators (
    id                TEXT PRIMARY KEY,
    user_id           TEXT NOT NULL,
    display_name      TEXT NOT NULL,
    bio               TEXT,
    avatar_url        TEXT,
    payout_account_id TEXT,                       -- Razorpay payout account
    verified          INTEGER NOT NULL DEFAULT 0,  -- KYC + ID verified
    platform_fee_pct  REAL NOT NULL DEFAULT 0.30,
    total_earnings_paise INTEGER NOT NULL DEFAULT 0,
    status            TEXT NOT NULL DEFAULT 'pending',
    -- pending | active | suspended
    created_at        REAL NOT NULL,
    UNIQUE (user_id)
);
CREATE INDEX IF NOT EXISTS idx_creators_status
    ON published_creators(status, created_at DESC);

CREATE TABLE IF NOT EXISTS published_series (
    id              TEXT PRIMARY KEY,
    creator_user_id TEXT NOT NULL,
    title           TEXT NOT NULL,
    description     TEXT,
    exam            TEXT,
    subject         TEXT,
    language        TEXT NOT NULL DEFAULT 'en',
    price_paise     INTEGER NOT NULL,
    currency        TEXT NOT NULL DEFAULT 'INR',
    cover_url       TEXT,
    lesson_count    INTEGER NOT NULL DEFAULT 0,
    total_minutes   INTEGER NOT NULL DEFAULT 0,
    status          TEXT NOT NULL DEFAULT 'draft',
    -- draft | published | archived
    purchase_count  INTEGER NOT NULL DEFAULT 0,
    rating_sum      REAL NOT NULL DEFAULT 0,
    rating_count    INTEGER NOT NULL DEFAULT 0,
    created_at      REAL NOT NULL,
    published_at    REAL,
    updated_at      REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_series_creator
    ON published_series(creator_user_id, status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_series_published
    ON published_series(status, exam, subject, published_at DESC);

CREATE TABLE IF NOT EXISTS published_lessons (
    id              TEXT PRIMARY KEY,
    series_id       TEXT NOT NULL,
    position        INTEGER NOT NULL,
    title           TEXT NOT NULL,
    duration_seconds INTEGER NOT NULL DEFAULT 0,
    video_url       TEXT,
    free_preview    INTEGER NOT NULL DEFAULT 0,
    created_at      REAL NOT NULL,
    UNIQUE (series_id, position)
);
CREATE INDEX IF NOT EXISTS idx_lessons_series
    ON published_lessons(series_id, position);

CREATE TABLE IF NOT EXISTS series_purchases (
    id                  TEXT PRIMARY KEY,
    user_id             TEXT NOT NULL,
    series_id           TEXT NOT NULL,
    price_paise         INTEGER NOT NULL,
    platform_fee_paise  INTEGER NOT NULL,
    creator_payout_paise INTEGER NOT NULL,
    purchased_at        REAL NOT NULL,
    refunded_at         REAL,
    UNIQUE (user_id, series_id)
);
CREATE INDEX IF NOT EXISTS idx_purchases_user
    ON series_purchases(user_id, purchased_at DESC);
CREATE INDEX IF NOT EXISTS idx_purchases_series
    ON series_purchases(series_id, purchased_at DESC);
"""


DEFAULT_PLATFORM_FEE_PCT = 0.30
MIN_PRICE_PAISE = 4900           # ₹49 — below this creator doesn't make rupees
MAX_PRICE_PAISE = 9999900        # ₹99,999 cap per series


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
class Creator:
    id: str
    user_id: str
    display_name: str
    bio: str | None
    avatar_url: str | None
    payout_account_id: str | None
    verified: bool
    platform_fee_pct: float
    total_earnings_paise: int
    status: str
    created_at: float


@dataclass(frozen=True)
class Series:
    id: str
    creator_user_id: str
    title: str
    description: str | None
    exam: str | None
    subject: str | None
    language: str
    price_paise: int
    currency: str
    cover_url: str | None
    lesson_count: int
    total_minutes: int
    status: str
    purchase_count: int
    rating_sum: float
    rating_count: int
    created_at: float
    published_at: float | None
    updated_at: float


@dataclass(frozen=True)
class Lesson:
    id: str
    series_id: str
    position: int
    title: str
    duration_seconds: int
    video_url: str | None
    free_preview: bool
    created_at: float


@dataclass(frozen=True)
class Purchase:
    id: str
    user_id: str
    series_id: str
    price_paise: int
    platform_fee_paise: int
    creator_payout_paise: int
    purchased_at: float
    refunded_at: float | None


# ---------- creator onboarding ----------

def apply_as_creator(
    *,
    user_id: str,
    display_name: str,
    bio: str | None = None,
    avatar_url: str | None = None,
) -> Creator:
    display_name = (display_name or "").strip()
    if len(display_name) < 2 or len(display_name) > 80:
        raise ValueError("display_name must be [2, 80] chars")
    cid = uuid.uuid4().hex
    now = time.time()
    try:
        with _conn() as conn:
            conn.execute(
                "INSERT INTO published_creators "
                "(id, user_id, display_name, bio, avatar_url, "
                " status, created_at) "
                "VALUES (?, ?, ?, ?, ?, 'pending', ?)",
                (cid, user_id, display_name, bio, avatar_url, now),
            )
    except sqlite3.IntegrityError as e:
        raise ValueError("already applied as creator") from e
    return get_creator_by_user(user_id)  # type: ignore[return-value]


def get_creator(creator_id: str) -> Creator | None:
    with _conn() as conn:
        r = conn.execute(
            "SELECT id, user_id, display_name, bio, avatar_url, "
            "       payout_account_id, verified, platform_fee_pct, "
            "       total_earnings_paise, status, created_at "
            "FROM published_creators WHERE id = ?", (creator_id,),
        ).fetchone()
    return _row_to_creator(r) if r else None


def get_creator_by_user(user_id: str) -> Creator | None:
    with _conn() as conn:
        r = conn.execute(
            "SELECT id, user_id, display_name, bio, avatar_url, "
            "       payout_account_id, verified, platform_fee_pct, "
            "       total_earnings_paise, status, created_at "
            "FROM published_creators WHERE user_id = ?", (user_id,),
        ).fetchone()
    return _row_to_creator(r) if r else None


def _row_to_creator(r) -> Creator:
    return Creator(
        id=r[0], user_id=r[1], display_name=r[2], bio=r[3],
        avatar_url=r[4], payout_account_id=r[5],
        verified=bool(r[6]), platform_fee_pct=r[7],
        total_earnings_paise=r[8], status=r[9], created_at=r[10],
    )


def approve_creator(*, creator_id: str, verified: bool = True) -> bool:
    """Admin path — promote a pending creator to 'active'."""
    with _conn() as conn:
        cur = conn.execute(
            "UPDATE published_creators SET "
            " status = 'active', verified = ? "
            "WHERE id = ? AND status = 'pending'",
            (1 if verified else 0, creator_id),
        )
        return cur.rowcount > 0


def set_payout_account(
    *,
    user_id: str,
    payout_account_id: str,
) -> bool:
    """Creator sets their Razorpay payout account. Stored as a
    reference id, not credentials."""
    with _conn() as conn:
        cur = conn.execute(
            "UPDATE published_creators SET payout_account_id = ? "
            "WHERE user_id = ?",
            (payout_account_id, user_id),
        )
        return cur.rowcount > 0


# ---------- series ----------

def create_series(
    *,
    creator_user_id: str,
    title: str,
    price_paise: int,
    description: str | None = None,
    exam: str | None = None,
    subject: str | None = None,
    language: str = "en",
    cover_url: str | None = None,
) -> Series:
    c = get_creator_by_user(creator_user_id)
    if not c:
        raise ValueError("creator profile required — apply first")
    if c.status != "active":
        raise ValueError(f"creator status is {c.status!r}, not active")
    title = (title or "").strip()
    if len(title) < 4 or len(title) > 200:
        raise ValueError("title must be [4, 200] chars")
    if price_paise < MIN_PRICE_PAISE or price_paise > MAX_PRICE_PAISE:
        raise ValueError(
            f"price must be in [₹{MIN_PRICE_PAISE/100:.0f}, "
            f"₹{MAX_PRICE_PAISE/100:.0f}]"
        )
    sid = uuid.uuid4().hex
    now = time.time()
    with _conn() as conn:
        conn.execute(
            "INSERT INTO published_series "
            "(id, creator_user_id, title, description, exam, subject, "
            " language, price_paise, currency, cover_url, status, "
            " created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'INR', ?, 'draft', ?, ?)",
            (sid, creator_user_id, title, description, exam, subject,
             language, price_paise, cover_url, now, now),
        )
    return get_series(sid)  # type: ignore[return-value]


def get_series(series_id: str) -> Series | None:
    with _conn() as conn:
        r = conn.execute(
            _SERIES_SEL_SQL + " WHERE id = ?", (series_id,),
        ).fetchone()
    return _row_to_series(r) if r else None


_SERIES_SEL_SQL = (
    "SELECT id, creator_user_id, title, description, exam, subject, "
    "       language, price_paise, currency, cover_url, lesson_count, "
    "       total_minutes, status, purchase_count, rating_sum, "
    "       rating_count, created_at, published_at, updated_at "
    "FROM published_series"
)


def _row_to_series(r) -> Series:
    return Series(
        id=r[0], creator_user_id=r[1], title=r[2], description=r[3],
        exam=r[4], subject=r[5], language=r[6],
        price_paise=r[7], currency=r[8], cover_url=r[9],
        lesson_count=r[10], total_minutes=r[11],
        status=r[12], purchase_count=r[13],
        rating_sum=r[14], rating_count=r[15],
        created_at=r[16], published_at=r[17], updated_at=r[18],
    )


def list_storefront(
    *,
    exam: str | None = None,
    subject: str | None = None,
    language: str | None = None,
    limit: int = 30,
    offset: int = 0,
) -> list[Series]:
    """Published-only browse for students."""
    limit = max(1, min(limit, 200))
    sql = _SERIES_SEL_SQL + " WHERE status = 'published'"
    params: list = []
    if exam: sql += " AND exam = ?"; params.append(exam)
    if subject: sql += " AND subject = ?"; params.append(subject)
    if language: sql += " AND language = ?"; params.append(language)
    sql += " ORDER BY purchase_count DESC, published_at DESC LIMIT ? OFFSET ?"
    params.extend([limit, max(0, offset)])
    with _conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_row_to_series(r) for r in rows]


def list_by_creator(creator_user_id: str) -> list[Series]:
    with _conn() as conn:
        rows = conn.execute(
            _SERIES_SEL_SQL + " WHERE creator_user_id = ? "
            "ORDER BY created_at DESC",
            (creator_user_id,),
        ).fetchall()
    return [_row_to_series(r) for r in rows]


def publish_series(*, series_id: str, creator_user_id: str) -> bool:
    """Creator publishes a draft (must have ≥1 lesson)."""
    s = get_series(series_id)
    if not s:
        raise ValueError("series not found")
    if s.creator_user_id != creator_user_id:
        raise PermissionError("not your series")
    if s.lesson_count < 1:
        raise ValueError("series has no lessons — add at least one before publishing")
    if s.status != "draft":
        return False
    now = time.time()
    with _conn() as conn:
        conn.execute(
            "UPDATE published_series SET "
            " status = 'published', published_at = ?, updated_at = ? "
            "WHERE id = ?",
            (now, now, series_id),
        )
    return True


def archive_series(*, series_id: str, creator_user_id: str) -> bool:
    s = get_series(series_id)
    if not s:
        return False
    if s.creator_user_id != creator_user_id:
        raise PermissionError("not your series")
    with _conn() as conn:
        cur = conn.execute(
            "UPDATE published_series SET "
            " status = 'archived', updated_at = ? "
            "WHERE id = ? AND status != 'archived'",
            (time.time(), series_id),
        )
        return cur.rowcount > 0


# ---------- lessons ----------

def add_lesson(
    *,
    series_id: str,
    creator_user_id: str,
    title: str,
    duration_seconds: int = 0,
    video_url: str | None = None,
    free_preview: bool = False,
) -> Lesson:
    s = get_series(series_id)
    if not s:
        raise ValueError("series not found")
    if s.creator_user_id != creator_user_id:
        raise PermissionError("not your series")
    if s.status == "archived":
        raise ValueError("series is archived")
    title = (title or "").strip()
    if len(title) < 2 or len(title) > 200:
        raise ValueError("lesson title must be [2, 200] chars")
    lid = uuid.uuid4().hex
    now = time.time()
    with _conn() as conn:
        # Next position = current count + 1
        next_pos = conn.execute(
            "SELECT COALESCE(MAX(position), 0) + 1 "
            "FROM published_lessons WHERE series_id = ?",
            (series_id,),
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO published_lessons "
            "(id, series_id, position, title, duration_seconds, "
            " video_url, free_preview, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (lid, series_id, next_pos, title, duration_seconds,
             video_url, 1 if free_preview else 0, now),
        )
        # Bump rollups
        conn.execute(
            "UPDATE published_series SET "
            " lesson_count = lesson_count + 1, "
            " total_minutes = total_minutes + ?, "
            " updated_at = ? "
            "WHERE id = ?",
            (duration_seconds // 60, now, series_id),
        )
    with _conn() as conn:
        r = conn.execute(
            "SELECT id, series_id, position, title, duration_seconds, "
            "       video_url, free_preview, created_at "
            "FROM published_lessons WHERE id = ?", (lid,),
        ).fetchone()
    return Lesson(
        id=r[0], series_id=r[1], position=r[2], title=r[3],
        duration_seconds=r[4], video_url=r[5],
        free_preview=bool(r[6]), created_at=r[7],
    )


def list_lessons(series_id: str) -> list[Lesson]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT id, series_id, position, title, duration_seconds, "
            "       video_url, free_preview, created_at "
            "FROM published_lessons WHERE series_id = ? "
            "ORDER BY position",
            (series_id,),
        ).fetchall()
    return [
        Lesson(
            id=r[0], series_id=r[1], position=r[2], title=r[3],
            duration_seconds=r[4], video_url=r[5],
            free_preview=bool(r[6]), created_at=r[7],
        )
        for r in rows
    ]


# ---------- purchase + revenue split ----------

def purchase(
    *,
    user_id: str,
    series_id: str,
) -> Purchase:
    """Record a purchase. The actual Razorpay charge happens
    elsewhere (E5 fee flow); this captures the platform/creator
    split so the payout cron can disburse the creator's share."""
    s = get_series(series_id)
    if not s:
        raise ValueError("series not found")
    if s.status != "published":
        raise ValueError(f"series status is {s.status!r}, not purchasable")
    if s.creator_user_id == user_id:
        raise ValueError("creators can't buy their own series")
    creator = get_creator_by_user(s.creator_user_id)
    if not creator:
        raise ValueError("creator missing — contact support")
    fee_pct = creator.platform_fee_pct
    platform_fee = round(s.price_paise * fee_pct)
    creator_payout = s.price_paise - platform_fee
    pid = uuid.uuid4().hex
    now = time.time()
    try:
        with _conn() as conn:
            conn.execute(
                "INSERT INTO series_purchases "
                "(id, user_id, series_id, price_paise, "
                " platform_fee_paise, creator_payout_paise, "
                " purchased_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (pid, user_id, series_id, s.price_paise,
                 platform_fee, creator_payout, now),
            )
            # Rollups
            conn.execute(
                "UPDATE published_series SET "
                " purchase_count = purchase_count + 1, updated_at = ? "
                "WHERE id = ?",
                (now, series_id),
            )
            conn.execute(
                "UPDATE published_creators SET "
                " total_earnings_paise = total_earnings_paise + ? "
                "WHERE id = ?",
                (creator_payout, creator.id),
            )
    except sqlite3.IntegrityError as e:
        raise ValueError("already purchased this series") from e
    return _get_purchase(pid)


def _get_purchase(purchase_id: str) -> Purchase:
    with _conn() as conn:
        r = conn.execute(
            "SELECT id, user_id, series_id, price_paise, "
            "       platform_fee_paise, creator_payout_paise, "
            "       purchased_at, refunded_at "
            "FROM series_purchases WHERE id = ?", (purchase_id,),
        ).fetchone()
    return Purchase(
        id=r[0], user_id=r[1], series_id=r[2],
        price_paise=r[3], platform_fee_paise=r[4],
        creator_payout_paise=r[5],
        purchased_at=r[6], refunded_at=r[7],
    )


def has_access(*, user_id: str, series_id: str) -> bool:
    """True if the user has an unrefunded purchase OR is the creator."""
    s = get_series(series_id)
    if s and s.creator_user_id == user_id:
        return True
    with _conn() as conn:
        r = conn.execute(
            "SELECT 1 FROM series_purchases "
            "WHERE user_id = ? AND series_id = ? "
            "AND refunded_at IS NULL", (user_id, series_id),
        ).fetchone()
    return r is not None


def my_purchases(user_id: str) -> list[Purchase]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT id, user_id, series_id, price_paise, "
            "       platform_fee_paise, creator_payout_paise, "
            "       purchased_at, refunded_at "
            "FROM series_purchases WHERE user_id = ? "
            "AND refunded_at IS NULL "
            "ORDER BY purchased_at DESC",
            (user_id,),
        ).fetchall()
    return [
        Purchase(
            id=r[0], user_id=r[1], series_id=r[2],
            price_paise=r[3], platform_fee_paise=r[4],
            creator_payout_paise=r[5],
            purchased_at=r[6], refunded_at=r[7],
        )
        for r in rows
    ]


def creator_earnings(creator_user_id: str) -> dict:
    """Aggregate earnings for the creator dashboard."""
    with _conn() as conn:
        r = conn.execute(
            "SELECT COUNT(p.id), COALESCE(SUM(p.creator_payout_paise), 0) "
            "FROM series_purchases p "
            "JOIN published_series s ON s.id = p.series_id "
            "WHERE s.creator_user_id = ? AND p.refunded_at IS NULL",
            (creator_user_id,),
        ).fetchone()
        by_series = conn.execute(
            "SELECT s.id, s.title, COUNT(p.id), "
            "       COALESCE(SUM(p.creator_payout_paise), 0) "
            "FROM published_series s "
            "LEFT JOIN series_purchases p ON p.series_id = s.id "
            " AND p.refunded_at IS NULL "
            "WHERE s.creator_user_id = ? "
            "GROUP BY s.id, s.title "
            "ORDER BY 4 DESC",
            (creator_user_id,),
        ).fetchall()
    return {
        "total_purchases": int(r[0] or 0),
        "total_payout_paise": int(r[1] or 0),
        "by_series": [
            {"series_id": br[0], "title": br[1],
             "purchases": br[2], "payout_paise": int(br[3] or 0)}
            for br in by_series
        ],
    }
