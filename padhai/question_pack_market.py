"""O3 — Question bank marketplace.

Independent question setters (retired teachers / coaching brand
authors / domain experts) publish curated packs sourced from / made
on top of J6's `question_bank`. Buyers (students, teachers, orgs)
purchase a pack once and can practice/export it. Platform takes
10% of every sale; the rest pays the setter.

Different from O1 (teacher publishing — solo teachers selling whole
courses) and O2 (publisher content packs — board / NGO syllabus
content per-seat). O3 is **question-only** packs sold on a one-time
buy. The atomic unit is a question, not a chapter.

Tables:
  qb_setters                  one row per setter (verify gate)
  question_packs_for_sale     pack metadata + pricing + status
  qb_pack_items               link pack → question (FK question_bank.id)
  qb_pack_purchases           one row per purchase with fee split

Pack lifecycle: draft → published → archived
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
CREATE TABLE IF NOT EXISTS qb_setters (
    id                  TEXT PRIMARY KEY,
    user_id             TEXT NOT NULL,
    display_name        TEXT NOT NULL,
    bio                 TEXT,
    credentials         TEXT,                    -- e.g. '20 yrs CBSE Mathematics teacher'
    payout_account_id   TEXT,
    verified            INTEGER NOT NULL DEFAULT 0,
    platform_fee_pct    REAL NOT NULL DEFAULT 0.10,
    total_earnings_paise INTEGER NOT NULL DEFAULT 0,
    pack_count          INTEGER NOT NULL DEFAULT 0,
    created_at          REAL NOT NULL,
    UNIQUE (user_id)
);
CREATE INDEX IF NOT EXISTS idx_setters_verified
    ON qb_setters(verified DESC, total_earnings_paise DESC);

CREATE TABLE IF NOT EXISTS question_packs_for_sale (
    id                  TEXT PRIMARY KEY,
    setter_user_id      TEXT NOT NULL,
    title               TEXT NOT NULL,
    description         TEXT,
    exam                TEXT,                    -- 'jee' | 'neet' | 'upsc' | ...
    board               TEXT,                    -- 'cbse' | 'icse' | ...
    grade               INTEGER,
    subject             TEXT,
    difficulty          TEXT,                    -- 'easy' | 'medium' | 'hard' | 'mixed'
    question_count      INTEGER NOT NULL DEFAULT 0,
    price_paise         INTEGER NOT NULL,
    status              TEXT NOT NULL DEFAULT 'draft',
    -- 'draft' | 'published' | 'archived'
    purchase_count      INTEGER NOT NULL DEFAULT 0,
    rating_sum          REAL NOT NULL DEFAULT 0,
    rating_count        INTEGER NOT NULL DEFAULT 0,
    preview_question_ids TEXT,                   -- JSON array of 3 sample qids
    created_at          REAL NOT NULL,
    published_at        REAL
);
CREATE INDEX IF NOT EXISTS idx_qbps_setter
    ON question_packs_for_sale(setter_user_id, status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_qbps_search
    ON question_packs_for_sale(status, exam, subject, grade);

CREATE TABLE IF NOT EXISTS qb_pack_items (
    pack_id         TEXT NOT NULL,
    question_id     TEXT NOT NULL,               -- FK question_bank.id
    position        INTEGER NOT NULL,
    added_at        REAL NOT NULL,
    PRIMARY KEY (pack_id, question_id)
);
CREATE INDEX IF NOT EXISTS idx_qbpi_pack
    ON qb_pack_items(pack_id, position);

CREATE TABLE IF NOT EXISTS qb_pack_purchases (
    id                  TEXT PRIMARY KEY,
    pack_id             TEXT NOT NULL,
    buyer_user_id       TEXT NOT NULL,
    price_paise         INTEGER NOT NULL,
    platform_fee_paise  INTEGER NOT NULL,
    setter_payout_paise INTEGER NOT NULL,
    purchased_at        REAL NOT NULL,
    refunded_at         REAL,
    UNIQUE (pack_id, buyer_user_id)
);
CREATE INDEX IF NOT EXISTS idx_qbpp_buyer
    ON qb_pack_purchases(buyer_user_id, purchased_at DESC);
CREATE INDEX IF NOT EXISTS idx_qbpp_pack
    ON qb_pack_purchases(pack_id, purchased_at DESC);
"""


VALID_PACK_STATUSES = frozenset({"draft", "published", "archived"})
VALID_DIFFICULTIES = frozenset({"easy", "medium", "hard", "mixed"})

# Sane pricing band — keep this approachable for student buyers.
MIN_PACK_PRICE_PAISE = 1900       # ₹19
MAX_PACK_PRICE_PAISE = 500000     # ₹5,000


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
class Setter:
    id: str
    user_id: str
    display_name: str
    bio: str | None
    credentials: str | None
    payout_account_id: str | None
    verified: bool
    platform_fee_pct: float
    total_earnings_paise: int
    pack_count: int
    created_at: float


@dataclass(frozen=True)
class Pack:
    id: str
    setter_user_id: str
    title: str
    description: str | None
    exam: str | None
    board: str | None
    grade: int | None
    subject: str | None
    difficulty: str | None
    question_count: int
    price_paise: int
    status: str
    purchase_count: int
    rating_avg: float
    rating_count: int
    preview_question_ids: list[str]
    created_at: float
    published_at: float | None


@dataclass(frozen=True)
class Purchase:
    id: str
    pack_id: str
    buyer_user_id: str
    price_paise: int
    platform_fee_paise: int
    setter_payout_paise: int
    purchased_at: float
    refunded_at: float | None


_SETTER_SEL = (
    "SELECT id, user_id, display_name, bio, credentials, "
    "       payout_account_id, verified, platform_fee_pct, "
    "       total_earnings_paise, pack_count, created_at "
    "FROM qb_setters"
)

_PACK_SEL = (
    "SELECT id, setter_user_id, title, description, exam, board, "
    "       grade, subject, difficulty, question_count, price_paise, "
    "       status, purchase_count, rating_sum, rating_count, "
    "       preview_question_ids, created_at, published_at "
    "FROM question_packs_for_sale"
)


def _row_to_setter(r) -> Setter:
    return Setter(
        id=r[0], user_id=r[1], display_name=r[2], bio=r[3],
        credentials=r[4], payout_account_id=r[5],
        verified=bool(r[6]), platform_fee_pct=r[7],
        total_earnings_paise=r[8], pack_count=r[9],
        created_at=r[10],
    )


def _row_to_pack(r) -> Pack:
    rc = r[14] or 0
    return Pack(
        id=r[0], setter_user_id=r[1], title=r[2], description=r[3],
        exam=r[4], board=r[5], grade=r[6], subject=r[7],
        difficulty=r[8], question_count=r[9], price_paise=r[10],
        status=r[11], purchase_count=r[12],
        rating_avg=round((r[13] or 0) / rc, 2) if rc else 0.0,
        rating_count=rc,
        preview_question_ids=json.loads(r[15]) if r[15] else [],
        created_at=r[16], published_at=r[17],
    )


# ---------- setter lifecycle ----------

def apply_as_setter(
    *,
    user_id: str,
    display_name: str,
    bio: str | None = None,
    credentials: str | None = None,
) -> Setter:
    display_name = (display_name or "").strip()
    if len(display_name) < 2 or len(display_name) > 80:
        raise ValueError("display_name must be [2, 80] chars")
    sid = uuid.uuid4().hex
    try:
        with _conn() as conn:
            conn.execute(
                "INSERT INTO qb_setters "
                "(id, user_id, display_name, bio, credentials, "
                " created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (sid, user_id, display_name, bio, credentials,
                 time.time()),
            )
    except sqlite3.IntegrityError as e:
        raise ValueError("already applied as setter") from e
    return get_setter(user_id)  # type: ignore[return-value]


def get_setter(user_id: str) -> Setter | None:
    with _conn() as conn:
        r = conn.execute(
            _SETTER_SEL + " WHERE user_id = ?", (user_id,),
        ).fetchone()
    return _row_to_setter(r) if r else None


def verify_setter(*, user_id: str, verified: bool = True) -> bool:
    with _conn() as conn:
        cur = conn.execute(
            "UPDATE qb_setters SET verified = ? WHERE user_id = ?",
            (1 if verified else 0, user_id),
        )
        return cur.rowcount > 0


def list_setters(*, verified_only: bool = False) -> list[Setter]:
    sql = _SETTER_SEL
    if verified_only:
        sql += " WHERE verified = 1"
    sql += " ORDER BY total_earnings_paise DESC, display_name"
    with _conn() as conn:
        rows = conn.execute(sql).fetchall()
    return [_row_to_setter(r) for r in rows]


# ---------- pack CRUD ----------

def create_pack(
    *,
    setter_user_id: str,
    title: str,
    price_paise: int,
    description: str | None = None,
    exam: str | None = None,
    board: str | None = None,
    grade: int | None = None,
    subject: str | None = None,
    difficulty: str | None = None,
) -> Pack:
    setter = get_setter(setter_user_id)
    if not setter:
        raise ValueError("setter profile not found")
    if not setter.verified:
        raise ValueError("setter must be verified before creating packs")
    title = (title or "").strip()
    if len(title) < 4 or len(title) > 200:
        raise ValueError("title must be [4, 200] chars")
    if price_paise < MIN_PACK_PRICE_PAISE or price_paise > MAX_PACK_PRICE_PAISE:
        raise ValueError(
            f"price_paise must be in "
            f"[{MIN_PACK_PRICE_PAISE}, {MAX_PACK_PRICE_PAISE}]",
        )
    if grade is not None and (grade < 1 or grade > 16):
        raise ValueError("grade must be in [1, 16]")
    if difficulty is not None and difficulty not in VALID_DIFFICULTIES:
        raise ValueError(
            f"difficulty must be in {sorted(VALID_DIFFICULTIES)}",
        )
    pid = uuid.uuid4().hex
    with _conn() as conn:
        conn.execute(
            "INSERT INTO question_packs_for_sale "
            "(id, setter_user_id, title, description, exam, board, "
            " grade, subject, difficulty, price_paise, status, "
            " created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft', ?)",
            (pid, setter_user_id, title, description, exam, board,
             grade, subject, difficulty, price_paise, time.time()),
        )
        conn.execute(
            "UPDATE qb_setters SET pack_count = pack_count + 1 "
            "WHERE user_id = ?", (setter_user_id,),
        )
    return get_pack(pid)  # type: ignore[return-value]


def get_pack(pack_id: str) -> Pack | None:
    with _conn() as conn:
        r = conn.execute(
            _PACK_SEL + " WHERE id = ?", (pack_id,),
        ).fetchone()
    return _row_to_pack(r) if r else None


def add_question(
    *,
    pack_id: str,
    setter_user_id: str,
    question_id: str,
    position: int | None = None,
) -> int:
    """Append a question to a pack. Returns new question_count."""
    pack = get_pack(pack_id)
    if not pack:
        raise ValueError("pack not found")
    if pack.setter_user_id != setter_user_id:
        raise PermissionError("not your pack")
    if pack.status != "draft":
        raise ValueError("pack must be draft to add questions")
    with _conn() as conn:
        if position is None:
            r = conn.execute(
                "SELECT COALESCE(MAX(position), 0) + 1 "
                "FROM qb_pack_items WHERE pack_id = ?", (pack_id,),
            ).fetchone()
            position = int(r[0])
        try:
            conn.execute(
                "INSERT INTO qb_pack_items "
                "(pack_id, question_id, position, added_at) "
                "VALUES (?, ?, ?, ?)",
                (pack_id, question_id, position, time.time()),
            )
        except sqlite3.IntegrityError:
            return pack.question_count  # already in pack
        r = conn.execute(
            "SELECT COUNT(*) FROM qb_pack_items WHERE pack_id = ?",
            (pack_id,),
        ).fetchone()
        new_count = int(r[0])
        conn.execute(
            "UPDATE question_packs_for_sale SET "
            " question_count = ? WHERE id = ?",
            (new_count, pack_id),
        )
    return new_count


def list_pack_questions(pack_id: str) -> list[str]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT question_id FROM qb_pack_items "
            "WHERE pack_id = ? ORDER BY position",
            (pack_id,),
        ).fetchall()
    return [r[0] for r in rows]


def publish_pack(*, pack_id: str, setter_user_id: str) -> bool:
    """Promote draft → published. Requires ≥5 questions + auto-picks
    3 preview qids (first 3) so browsers can sample the pack."""
    pack = get_pack(pack_id)
    if not pack:
        raise ValueError("pack not found")
    if pack.setter_user_id != setter_user_id:
        raise PermissionError("not your pack")
    if pack.status != "draft":
        return False
    if pack.question_count < 5:
        raise ValueError("pack must have ≥5 questions to publish")
    qids = list_pack_questions(pack_id)[:3]
    with _conn() as conn:
        conn.execute(
            "UPDATE question_packs_for_sale SET "
            " status = 'published', published_at = ?, "
            " preview_question_ids = ? "
            "WHERE id = ?",
            (time.time(), json.dumps(qids), pack_id),
        )
    return True


def archive_pack(*, pack_id: str, setter_user_id: str) -> bool:
    pack = get_pack(pack_id)
    if not pack:
        return False
    if pack.setter_user_id != setter_user_id:
        raise PermissionError("not your pack")
    with _conn() as conn:
        cur = conn.execute(
            "UPDATE question_packs_for_sale SET status = 'archived' "
            "WHERE id = ? AND status != 'archived'",
            (pack_id,),
        )
        return cur.rowcount > 0


def browse_packs(
    *,
    exam: str | None = None,
    board: str | None = None,
    grade: int | None = None,
    subject: str | None = None,
    max_price_paise: int | None = None,
    limit: int = 30,
    offset: int = 0,
) -> list[Pack]:
    """Browse marketplace — published packs only."""
    limit = max(1, min(limit, 200))
    sql = _PACK_SEL + " WHERE status = 'published'"
    params: list = []
    if exam:
        sql += " AND exam = ?"; params.append(exam)
    if board:
        sql += " AND board = ?"; params.append(board)
    if grade is not None:
        sql += " AND grade = ?"; params.append(grade)
    if subject:
        sql += " AND subject = ?"; params.append(subject)
    if max_price_paise is not None:
        sql += " AND price_paise <= ?"; params.append(max_price_paise)
    sql += " ORDER BY purchase_count DESC, rating_count DESC LIMIT ? OFFSET ?"
    params.extend([limit, max(0, offset)])
    with _conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_row_to_pack(r) for r in rows]


def list_setter_packs(setter_user_id: str) -> list[Pack]:
    with _conn() as conn:
        rows = conn.execute(
            _PACK_SEL + " WHERE setter_user_id = ? "
            "ORDER BY created_at DESC",
            (setter_user_id,),
        ).fetchall()
    return [_row_to_pack(r) for r in rows]


# ---------- purchase ----------

def purchase_pack(
    *,
    pack_id: str,
    buyer_user_id: str,
) -> Purchase:
    """Record a one-time purchase. Razorpay charge is handled
    elsewhere; we capture the contract + fee split. Idempotent on
    (pack_id, buyer_user_id) — re-buys return the existing row."""
    pack = get_pack(pack_id)
    if not pack:
        raise ValueError("pack not found")
    if pack.status != "published":
        raise ValueError(f"pack status is {pack.status!r}, not buyable")
    if pack.setter_user_id == buyer_user_id:
        raise ValueError("setter can't buy their own pack")
    # Idempotency
    with _conn() as conn:
        r = conn.execute(
            "SELECT id, pack_id, buyer_user_id, price_paise, "
            "       platform_fee_paise, setter_payout_paise, "
            "       purchased_at, refunded_at "
            "FROM qb_pack_purchases "
            "WHERE pack_id = ? AND buyer_user_id = ?",
            (pack_id, buyer_user_id),
        ).fetchone()
        if r:
            return Purchase(
                id=r[0], pack_id=r[1], buyer_user_id=r[2],
                price_paise=r[3], platform_fee_paise=r[4],
                setter_payout_paise=r[5], purchased_at=r[6],
                refunded_at=r[7],
            )
    setter = get_setter(pack.setter_user_id)
    if not setter:
        raise ValueError("pack setter missing")
    fee = int(round(pack.price_paise * setter.platform_fee_pct))
    payout = pack.price_paise - fee
    pid = uuid.uuid4().hex
    now = time.time()
    with _conn() as conn:
        conn.execute(
            "INSERT INTO qb_pack_purchases "
            "(id, pack_id, buyer_user_id, price_paise, "
            " platform_fee_paise, setter_payout_paise, "
            " purchased_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (pid, pack_id, buyer_user_id, pack.price_paise,
             fee, payout, now),
        )
        conn.execute(
            "UPDATE question_packs_for_sale SET "
            " purchase_count = purchase_count + 1 WHERE id = ?",
            (pack_id,),
        )
        conn.execute(
            "UPDATE qb_setters SET "
            " total_earnings_paise = total_earnings_paise + ? "
            "WHERE user_id = ?",
            (payout, pack.setter_user_id),
        )
    return Purchase(
        id=pid, pack_id=pack_id, buyer_user_id=buyer_user_id,
        price_paise=pack.price_paise, platform_fee_paise=fee,
        setter_payout_paise=payout, purchased_at=now,
        refunded_at=None,
    )


def user_has_pack(*, pack_id: str, user_id: str) -> bool:
    with _conn() as conn:
        r = conn.execute(
            "SELECT 1 FROM qb_pack_purchases "
            "WHERE pack_id = ? AND buyer_user_id = ? "
            "AND refunded_at IS NULL",
            (pack_id, user_id),
        ).fetchone()
    return r is not None


def list_user_purchases(buyer_user_id: str) -> list[Purchase]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT id, pack_id, buyer_user_id, price_paise, "
            "       platform_fee_paise, setter_payout_paise, "
            "       purchased_at, refunded_at "
            "FROM qb_pack_purchases WHERE buyer_user_id = ? "
            "ORDER BY purchased_at DESC",
            (buyer_user_id,),
        ).fetchall()
    return [
        Purchase(
            id=r[0], pack_id=r[1], buyer_user_id=r[2],
            price_paise=r[3], platform_fee_paise=r[4],
            setter_payout_paise=r[5], purchased_at=r[6],
            refunded_at=r[7],
        )
        for r in rows
    ]


def refund_purchase(*, purchase_id: str) -> bool:
    """Admin path — refund a purchase. Reverses earnings + bumps
    refunded_at."""
    now = time.time()
    with _conn() as conn:
        r = conn.execute(
            "SELECT pack_id, setter_payout_paise "
            "FROM qb_pack_purchases "
            "WHERE id = ? AND refunded_at IS NULL",
            (purchase_id,),
        ).fetchone()
        if not r:
            return False
        pack_id, payout = r[0], int(r[1])
        conn.execute(
            "UPDATE qb_pack_purchases SET refunded_at = ? "
            "WHERE id = ?", (now, purchase_id),
        )
        # Pull setter user_id via pack
        s = conn.execute(
            "SELECT setter_user_id FROM question_packs_for_sale "
            "WHERE id = ?", (pack_id,),
        ).fetchone()
        if s:
            conn.execute(
                "UPDATE qb_setters SET "
                " total_earnings_paise = "
                "   MAX(0, total_earnings_paise - ?) "
                "WHERE user_id = ?",
                (payout, s[0]),
            )
    return True


def setter_earnings_summary(setter_user_id: str) -> dict:
    with _conn() as conn:
        r = conn.execute(
            "SELECT COUNT(p.id), "
            "       COALESCE(SUM(p.setter_payout_paise), 0) "
            "FROM qb_pack_purchases p "
            "JOIN question_packs_for_sale qp ON qp.id = p.pack_id "
            "WHERE qp.setter_user_id = ? AND p.refunded_at IS NULL",
            (setter_user_id,),
        ).fetchone()
        by_pack = conn.execute(
            "SELECT qp.id, qp.title, COUNT(p.id), "
            "       COALESCE(SUM(p.setter_payout_paise), 0) "
            "FROM question_packs_for_sale qp "
            "LEFT JOIN qb_pack_purchases p ON p.pack_id = qp.id "
            " AND p.refunded_at IS NULL "
            "WHERE qp.setter_user_id = ? "
            "GROUP BY qp.id, qp.title ORDER BY 4 DESC",
            (setter_user_id,),
        ).fetchall()
    return {
        "total_purchases": int(r[0] or 0),
        "total_payout_paise": int(r[1] or 0),
        "by_pack": [
            {"pack_id": bp[0], "title": bp[1],
             "purchases": bp[2], "payout_paise": int(bp[3] or 0)}
            for bp in by_pack
        ],
    }
