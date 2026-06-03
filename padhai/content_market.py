"""O2 — Curriculum content marketplace.

State boards / NGOs / coaching brands publish syllabus-aligned
content packs; schools (orgs) subscribe per-seat. We're the rails;
revenue split happens via standard B2B contract — platform takes a
fixed `platform_fee_pct` (default 10%) of subscription value.

Three tables:
  publishers              one row per publisher org / individual
  content_packs           one row per pack (board / grade / subject)
  content_subscriptions   one row per (org, pack) active subscription
"""

from __future__ import annotations

import os
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path


SCHEMA = """
CREATE TABLE IF NOT EXISTS publishers (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    kind            TEXT NOT NULL,           -- 'board' | 'ngo' | 'coaching' | 'university' | 'creator'
    contact_email   TEXT,
    payout_account_id TEXT,
    verified        INTEGER NOT NULL DEFAULT 0,
    platform_fee_pct REAL NOT NULL DEFAULT 0.10,
    created_at      REAL NOT NULL,
    UNIQUE (name, kind)
);
CREATE INDEX IF NOT EXISTS idx_publishers_kind
    ON publishers(kind, verified DESC, created_at DESC);

CREATE TABLE IF NOT EXISTS content_packs (
    id              TEXT PRIMARY KEY,
    publisher_id    TEXT NOT NULL,
    title           TEXT NOT NULL,
    description     TEXT,
    board           TEXT,
    grade           INTEGER,
    subject         TEXT,
    chapter_count   INTEGER NOT NULL DEFAULT 0,
    price_inr_per_seat_year INTEGER NOT NULL,
    language        TEXT NOT NULL DEFAULT 'en',
    manifest_url    TEXT,                     -- where the content lives (R2 / public CDN)
    status          TEXT NOT NULL DEFAULT 'draft',
    -- draft | published | archived
    subscription_count INTEGER NOT NULL DEFAULT 0,
    created_at      REAL NOT NULL,
    published_at    REAL
);
CREATE INDEX IF NOT EXISTS idx_packs_publisher
    ON content_packs(publisher_id, status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_packs_search
    ON content_packs(status, board, grade, subject);

CREATE TABLE IF NOT EXISTS content_subscriptions (
    id                TEXT PRIMARY KEY,
    org_id            TEXT NOT NULL,
    pack_id           TEXT NOT NULL,
    seats             INTEGER NOT NULL,
    total_paid_paise  INTEGER NOT NULL,
    platform_fee_paise INTEGER NOT NULL,
    publisher_payout_paise INTEGER NOT NULL,
    started_at        REAL NOT NULL,
    expires_at        REAL NOT NULL,
    status            TEXT NOT NULL DEFAULT 'active',
    -- active | expired | cancelled
    cancelled_at      REAL
);
CREATE INDEX IF NOT EXISTS idx_subs_org
    ON content_subscriptions(org_id, status, expires_at DESC);
CREATE INDEX IF NOT EXISTS idx_subs_pack
    ON content_subscriptions(pack_id, status);
CREATE INDEX IF NOT EXISTS idx_subs_renewals
    ON content_subscriptions(status, expires_at);
"""


VALID_PUBLISHER_KINDS = frozenset({
    "board", "ngo", "coaching", "university", "creator",
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
class Publisher:
    id: str
    name: str
    kind: str
    contact_email: str | None
    payout_account_id: str | None
    verified: bool
    platform_fee_pct: float
    created_at: float


@dataclass(frozen=True)
class Pack:
    id: str
    publisher_id: str
    title: str
    description: str | None
    board: str | None
    grade: int | None
    subject: str | None
    chapter_count: int
    price_inr_per_seat_year: int
    language: str
    manifest_url: str | None
    status: str
    subscription_count: int
    created_at: float
    published_at: float | None


@dataclass(frozen=True)
class Subscription:
    id: str
    org_id: str
    pack_id: str
    seats: int
    total_paid_paise: int
    platform_fee_paise: int
    publisher_payout_paise: int
    started_at: float
    expires_at: float
    status: str


# ---------- publisher CRUD ----------

def register_publisher(
    *,
    name: str,
    kind: str,
    contact_email: str | None = None,
) -> Publisher:
    name = (name or "").strip()
    if len(name) < 2 or len(name) > 200:
        raise ValueError("name must be [2, 200] chars")
    if kind not in VALID_PUBLISHER_KINDS:
        raise ValueError(f"kind must be in {sorted(VALID_PUBLISHER_KINDS)}")
    pid = uuid.uuid4().hex
    try:
        with _conn() as conn:
            conn.execute(
                "INSERT INTO publishers "
                "(id, name, kind, contact_email, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (pid, name, kind, contact_email, time.time()),
            )
    except sqlite3.IntegrityError as e:
        raise ValueError(f"publisher {name!r}/{kind!r} already exists") from e
    return get_publisher(pid)  # type: ignore[return-value]


def get_publisher(publisher_id: str) -> Publisher | None:
    with _conn() as conn:
        r = conn.execute(
            "SELECT id, name, kind, contact_email, payout_account_id, "
            "       verified, platform_fee_pct, created_at "
            "FROM publishers WHERE id = ?", (publisher_id,),
        ).fetchone()
    if not r:
        return None
    return Publisher(
        id=r[0], name=r[1], kind=r[2],
        contact_email=r[3], payout_account_id=r[4],
        verified=bool(r[5]), platform_fee_pct=r[6],
        created_at=r[7],
    )


def list_publishers(*, kind: str | None = None) -> list[Publisher]:
    sql = (
        "SELECT id, name, kind, contact_email, payout_account_id, "
        "       verified, platform_fee_pct, created_at "
        "FROM publishers"
    )
    params: list = []
    if kind:
        sql += " WHERE kind = ?"; params.append(kind)
    sql += " ORDER BY verified DESC, name"
    with _conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [
        Publisher(
            id=r[0], name=r[1], kind=r[2],
            contact_email=r[3], payout_account_id=r[4],
            verified=bool(r[5]), platform_fee_pct=r[6],
            created_at=r[7],
        )
        for r in rows
    ]


def verify_publisher(publisher_id: str, *, verified: bool = True) -> bool:
    with _conn() as conn:
        cur = conn.execute(
            "UPDATE publishers SET verified = ? WHERE id = ?",
            (1 if verified else 0, publisher_id),
        )
        return cur.rowcount > 0


# ---------- packs ----------

def create_pack(
    *,
    publisher_id: str,
    title: str,
    price_inr_per_seat_year: int,
    description: str | None = None,
    board: str | None = None,
    grade: int | None = None,
    subject: str | None = None,
    chapter_count: int = 0,
    language: str = "en",
    manifest_url: str | None = None,
) -> Pack:
    pub = get_publisher(publisher_id)
    if not pub:
        raise ValueError("publisher not found")
    if not pub.verified:
        raise ValueError("publisher must be verified before creating packs")
    title = (title or "").strip()
    if len(title) < 4 or len(title) > 200:
        raise ValueError("title must be [4, 200] chars")
    if price_inr_per_seat_year < 1 or price_inr_per_seat_year > 50000:
        raise ValueError("price must be in [₹1, ₹50,000]")
    if grade is not None and (grade < 1 or grade > 16):
        raise ValueError("grade must be in [1, 16]")
    if chapter_count < 0 or chapter_count > 500:
        raise ValueError("chapter_count must be in [0, 500]")
    pid = uuid.uuid4().hex
    with _conn() as conn:
        conn.execute(
            "INSERT INTO content_packs "
            "(id, publisher_id, title, description, board, grade, "
            " subject, chapter_count, price_inr_per_seat_year, "
            " language, manifest_url, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft', ?)",
            (pid, publisher_id, title, description, board, grade,
             subject, chapter_count, price_inr_per_seat_year,
             language, manifest_url, time.time()),
        )
    return get_pack(pid)  # type: ignore[return-value]


def get_pack(pack_id: str) -> Pack | None:
    with _conn() as conn:
        r = conn.execute(
            _PACK_SEL + " WHERE id = ?", (pack_id,),
        ).fetchone()
    return _row_to_pack(r) if r else None


_PACK_SEL = (
    "SELECT id, publisher_id, title, description, board, grade, "
    "       subject, chapter_count, price_inr_per_seat_year, "
    "       language, manifest_url, status, subscription_count, "
    "       created_at, published_at "
    "FROM content_packs"
)


def _row_to_pack(r) -> Pack:
    return Pack(
        id=r[0], publisher_id=r[1], title=r[2], description=r[3],
        board=r[4], grade=r[5], subject=r[6],
        chapter_count=r[7], price_inr_per_seat_year=r[8],
        language=r[9], manifest_url=r[10], status=r[11],
        subscription_count=r[12], created_at=r[13],
        published_at=r[14],
    )


def list_packs(
    *,
    publisher_id: str | None = None,
    board: str | None = None,
    grade: int | None = None,
    subject: str | None = None,
    language: str | None = None,
    only_published: bool = True,
    limit: int = 50,
    offset: int = 0,
) -> list[Pack]:
    limit = max(1, min(limit, 200))
    sql = _PACK_SEL
    where = []
    params: list = []
    if only_published:
        where.append("status = 'published'")
    if publisher_id is not None:
        where.append("publisher_id = ?"); params.append(publisher_id)
    if board is not None:
        where.append("board = ?"); params.append(board)
    if grade is not None:
        where.append("grade = ?"); params.append(grade)
    if subject is not None:
        where.append("subject = ?"); params.append(subject)
    if language is not None:
        where.append("language = ?"); params.append(language)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY subscription_count DESC, created_at DESC LIMIT ? OFFSET ?"
    params.extend([limit, max(0, offset)])
    with _conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_row_to_pack(r) for r in rows]


def publish_pack(*, pack_id: str, publisher_id: str) -> bool:
    pack = get_pack(pack_id)
    if not pack:
        raise ValueError("pack not found")
    if pack.publisher_id != publisher_id:
        raise PermissionError("not your pack")
    if pack.chapter_count < 1:
        raise ValueError("pack must have at least one chapter")
    if pack.status != "draft":
        return False
    now = time.time()
    with _conn() as conn:
        conn.execute(
            "UPDATE content_packs SET status = 'published', "
            " published_at = ? WHERE id = ?",
            (now, pack_id),
        )
    return True


def archive_pack(*, pack_id: str, publisher_id: str) -> bool:
    pack = get_pack(pack_id)
    if not pack:
        return False
    if pack.publisher_id != publisher_id:
        raise PermissionError("not your pack")
    with _conn() as conn:
        cur = conn.execute(
            "UPDATE content_packs SET status = 'archived' "
            "WHERE id = ? AND status != 'archived'",
            (pack_id,),
        )
        return cur.rowcount > 0


# ---------- subscriptions ----------

def subscribe(
    *,
    org_id: str,
    pack_id: str,
    seats: int,
    duration_days: int = 365,
) -> Subscription:
    """Org subscribes to a pack. Default duration 1 year. The actual
    payment is a Razorpay charge handled elsewhere; this records the
    contract + computes the platform/publisher split."""
    if seats < 1 or seats > 100000:
        raise ValueError("seats must be in [1, 100000]")
    if duration_days < 30 or duration_days > 365 * 3:
        raise ValueError("duration_days must be in [30, 1095]")
    pack = get_pack(pack_id)
    if not pack:
        raise ValueError("pack not found")
    if pack.status != "published":
        raise ValueError(f"pack status is {pack.status!r}, not subscribable")
    publisher = get_publisher(pack.publisher_id)
    if not publisher:
        raise ValueError("publisher missing")
    # Pro-rate for non-annual durations
    annual_inr = pack.price_inr_per_seat_year * seats
    cost_inr = int(round(annual_inr * (duration_days / 365.0)))
    total_paise = cost_inr * 100
    platform_fee_paise = int(round(total_paise * publisher.platform_fee_pct))
    publisher_payout_paise = total_paise - platform_fee_paise
    sid = uuid.uuid4().hex
    now = time.time()
    expires_at = now + duration_days * 86400
    with _conn() as conn:
        # Expire any prior active sub for this (org, pack) so we
        # don't double-bill or double-count seats.
        conn.execute(
            "UPDATE content_subscriptions SET status = 'expired' "
            "WHERE org_id = ? AND pack_id = ? AND status = 'active'",
            (org_id, pack_id),
        )
        conn.execute(
            "INSERT INTO content_subscriptions "
            "(id, org_id, pack_id, seats, total_paid_paise, "
            " platform_fee_paise, publisher_payout_paise, "
            " started_at, expires_at, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'active')",
            (sid, org_id, pack_id, seats, total_paise,
             platform_fee_paise, publisher_payout_paise,
             now, expires_at),
        )
        conn.execute(
            "UPDATE content_packs SET subscription_count = "
            " (SELECT COUNT(*) FROM content_subscriptions "
            "  WHERE pack_id = ? AND status = 'active') "
            "WHERE id = ?",
            (pack_id, pack_id),
        )
    return _get_subscription(sid)


def _get_subscription(sub_id: str) -> Subscription:
    with _conn() as conn:
        r = conn.execute(
            "SELECT id, org_id, pack_id, seats, total_paid_paise, "
            "       platform_fee_paise, publisher_payout_paise, "
            "       started_at, expires_at, status "
            "FROM content_subscriptions WHERE id = ?", (sub_id,),
        ).fetchone()
    if not r:
        raise ValueError(f"subscription {sub_id!r} not found")
    return _row_to_subscription(r)


def _row_to_subscription(r) -> Subscription:
    return Subscription(
        id=r[0], org_id=r[1], pack_id=r[2], seats=r[3],
        total_paid_paise=r[4],
        platform_fee_paise=r[5],
        publisher_payout_paise=r[6],
        started_at=r[7], expires_at=r[8], status=r[9],
    )


def list_subscriptions_for_org(org_id: str) -> list[Subscription]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT id, org_id, pack_id, seats, total_paid_paise, "
            "       platform_fee_paise, publisher_payout_paise, "
            "       started_at, expires_at, status "
            "FROM content_subscriptions WHERE org_id = ? "
            "ORDER BY started_at DESC",
            (org_id,),
        ).fetchall()
    return [_row_to_subscription(r) for r in rows]


def cancel_subscription(*, sub_id: str) -> bool:
    with _conn() as conn:
        cur = conn.execute(
            "UPDATE content_subscriptions SET "
            " status = 'cancelled', cancelled_at = ? "
            "WHERE id = ? AND status = 'active'",
            (time.time(), sub_id),
        )
        return cur.rowcount > 0


def publisher_earnings(publisher_id: str) -> dict:
    with _conn() as conn:
        r = conn.execute(
            "SELECT COUNT(s.id), "
            "       COALESCE(SUM(s.publisher_payout_paise), 0), "
            "       COALESCE(SUM(s.seats), 0) "
            "FROM content_subscriptions s "
            "JOIN content_packs p ON p.id = s.pack_id "
            "WHERE p.publisher_id = ? AND s.status = 'active'",
            (publisher_id,),
        ).fetchone()
        by_pack = conn.execute(
            "SELECT p.id, p.title, COUNT(s.id), "
            "       COALESCE(SUM(s.publisher_payout_paise), 0) "
            "FROM content_packs p "
            "LEFT JOIN content_subscriptions s ON s.pack_id = p.id "
            " AND s.status = 'active' "
            "WHERE p.publisher_id = ? "
            "GROUP BY p.id, p.title ORDER BY 4 DESC",
            (publisher_id,),
        ).fetchall()
    return {
        "active_subscriptions": int(r[0] or 0),
        "total_payout_paise": int(r[1] or 0),
        "total_seats": int(r[2] or 0),
        "by_pack": [
            {"pack_id": bp[0], "title": bp[1],
             "active_subs": bp[2], "payout_paise": int(bp[3] or 0)}
            for bp in by_pack
        ],
    }
