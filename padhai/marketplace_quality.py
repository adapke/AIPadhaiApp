"""v3.11 — Marketplace quality controls.

Per review §16 — marketplace concepts exist (O1 teacher
publishing, O2 content market, O3 question pack market, M4 tutor
marketplace) but **quality control** is the deciding factor.
Without ratings + refunds + copyright checks, the marketplace
becomes a spam farm.

This module is the unified quality layer across all 4
marketplaces. It doesn't replace any of them; each marketplace
has its own row (course / pack / tutor / question-pack), and we
tie ratings + refunds + reports to those rows by `(item_kind,
item_id)` pairs.

Five tables:
  market_ratings           per (item, user) 1-5 star rating + review
  market_refund_requests   per-purchase refund flow
  market_copyright_claims  DMCA-style takedown requests
  market_quality_scores    cached aggregate quality per item
                           (recomputed on rating/refund/claim writes)
  market_item_status       item-level visibility (active/under_review/removed)

Item kinds:
  course          (O1 — teacher_publishing.published_series)
  content_pack    (O2 — content_market.content_packs)
  question_pack   (O3 — question_pack_market.question_packs_for_sale)
  tutor           (M4 — tutor_marketplace.marketplace_tutors)

Quality score is a 0-100 composite:
  rating_score      mean rating × 20 (5★ = 100, 1★ = 20)
  refund_penalty    -10 per refunded sale per 100 sales (capped 30)
  copyright_penalty 0 / -25 / -100 based on claim severity
  recency_boost     +5 if active rating in last 30d

Items with quality_score < 40 land in `under_review` automatically.
Items with sustained copyright claims auto-remove.

`compute_quality_score(item_kind, item_id)` is the recompute
function — called after every rating / refund / claim write.

Public APIs:
  rate(item_kind, item_id, user_id, rating, review_text?)
  request_refund(item_kind, item_id, purchase_id, user_id, reason)
  decide_refund(refund_id, action, reviewer_user_id)
  file_copyright_claim(item_kind, item_id, claimant_id, ...)
  decide_claim(claim_id, action, reviewer_user_id)
  get_quality(item_kind, item_id) → cached score + breakdown
  list_ratings_for_item(item_kind, item_id)
  set_item_status(item_kind, item_id, status, reason)
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
CREATE TABLE IF NOT EXISTS market_ratings (
    id              TEXT PRIMARY KEY,
    item_kind       TEXT NOT NULL,                   -- 'course' / 'content_pack' / 'question_pack' / 'tutor'
    item_id         TEXT NOT NULL,
    user_id         TEXT NOT NULL,
    rating          INTEGER NOT NULL,                -- 1..5
    review_text     TEXT,
    helpful_count   INTEGER NOT NULL DEFAULT 0,
    created_at      REAL NOT NULL,
    updated_at      REAL NOT NULL,
    UNIQUE (item_kind, item_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_mr_item
    ON market_ratings(item_kind, item_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_mr_user
    ON market_ratings(user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS market_refund_requests (
    id              TEXT PRIMARY KEY,
    item_kind       TEXT NOT NULL,
    item_id         TEXT NOT NULL,
    purchase_id     TEXT NOT NULL,
    user_id         TEXT NOT NULL,
    amount_paise    INTEGER NOT NULL,
    reason          TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending',
    -- 'pending' | 'approved' | 'rejected' | 'auto_expired'
    reviewer_user_id TEXT,
    decision_reason TEXT,
    requested_at    REAL NOT NULL,
    decided_at      REAL,
    sla_due_at      REAL
);
CREATE INDEX IF NOT EXISTS idx_mrf_status
    ON market_refund_requests(status, requested_at);
CREATE INDEX IF NOT EXISTS idx_mrf_user
    ON market_refund_requests(user_id, requested_at DESC);
CREATE INDEX IF NOT EXISTS idx_mrf_item
    ON market_refund_requests(item_kind, item_id);

CREATE TABLE IF NOT EXISTS market_copyright_claims (
    id              TEXT PRIMARY KEY,
    item_kind       TEXT NOT NULL,
    item_id         TEXT NOT NULL,
    claimant_user_id TEXT NOT NULL,
    claimant_kind   TEXT NOT NULL DEFAULT 'individual',
    -- 'individual' | 'publisher' | 'institution' | 'board'
    claim_type      TEXT NOT NULL,
    -- 'plagiarism' | 'unauthorized_use' | 'verbatim_copy' | 'paraphrase'
    severity        TEXT NOT NULL DEFAULT 'moderate',
    -- 'minor' | 'moderate' | 'severe'
    evidence_text   TEXT,
    evidence_url    TEXT,
    status          TEXT NOT NULL DEFAULT 'open',
    -- 'open' | 'upheld' | 'dismissed' | 'auto_resolved'
    reviewer_user_id TEXT,
    decision_reason TEXT,
    filed_at        REAL NOT NULL,
    decided_at      REAL
);
CREATE INDEX IF NOT EXISTS idx_mcc_status
    ON market_copyright_claims(status, filed_at);
CREATE INDEX IF NOT EXISTS idx_mcc_item
    ON market_copyright_claims(item_kind, item_id);

CREATE TABLE IF NOT EXISTS market_quality_scores (
    item_kind       TEXT NOT NULL,
    item_id         TEXT NOT NULL,
    score           REAL NOT NULL,
    rating_avg      REAL,
    rating_count    INTEGER NOT NULL DEFAULT 0,
    refund_count    INTEGER NOT NULL DEFAULT 0,
    copyright_claims_open INTEGER NOT NULL DEFAULT 0,
    copyright_claims_upheld INTEGER NOT NULL DEFAULT 0,
    last_rated_at   REAL,
    computed_at     REAL NOT NULL,
    PRIMARY KEY (item_kind, item_id)
);

CREATE TABLE IF NOT EXISTS market_item_status (
    item_kind       TEXT NOT NULL,
    item_id         TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'active',
    -- 'active' | 'under_review' | 'removed' | 'rejected'
    reason          TEXT,
    updated_by_user_id TEXT,
    updated_at      REAL NOT NULL,
    PRIMARY KEY (item_kind, item_id)
);
"""


VALID_ITEM_KINDS = frozenset({
    "course", "content_pack", "question_pack", "tutor",
})

VALID_REFUND_STATUSES = frozenset({
    "pending", "approved", "rejected", "auto_expired",
})

VALID_REFUND_ACTIONS = frozenset({"approve", "reject"})

VALID_CLAIMANT_KINDS = frozenset({
    "individual", "publisher", "institution", "board",
})

VALID_CLAIM_TYPES = frozenset({
    "plagiarism", "unauthorized_use",
    "verbatim_copy", "paraphrase",
})

VALID_CLAIM_SEVERITIES = frozenset({
    "minor", "moderate", "severe",
})

VALID_CLAIM_STATUSES = frozenset({
    "open", "upheld", "dismissed", "auto_resolved",
})

VALID_CLAIM_ACTIONS = frozenset({"uphold", "dismiss"})

VALID_ITEM_STATUSES = frozenset({
    "active", "under_review", "removed", "rejected",
})


# Quality-score weights — tunable but defaults shipped sensible.
REFUND_PENALTY_PER_PCT = 1.0    # 1 point lost per 1% refund rate
REFUND_PENALTY_CAP = 30.0
COPYRIGHT_PENALTY = {
    "minor":    10.0,
    "moderate": 25.0,
    "severe":   100.0,    # severe = auto-remove
}
RECENCY_BOOST_DAYS = 30
RECENCY_BOOST = 5.0

# When quality_score < this, item auto-flips to under_review.
QUALITY_UNDER_REVIEW_THRESHOLD = 40.0

# Refund SLA — pending refunds past this auto-expire (counts as
# approved against the seller — protects the buyer).
REFUND_SLA_HOURS = 168    # 7 days


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
class Rating:
    id: str
    item_kind: str
    item_id: str
    user_id: str
    rating: int
    review_text: str | None
    helpful_count: int
    created_at: float
    updated_at: float


@dataclass(frozen=True)
class RefundRequest:
    id: str
    item_kind: str
    item_id: str
    purchase_id: str
    user_id: str
    amount_paise: int
    reason: str
    status: str
    reviewer_user_id: str | None
    decision_reason: str | None
    requested_at: float
    decided_at: float | None
    sla_due_at: float | None


@dataclass(frozen=True)
class CopyrightClaim:
    id: str
    item_kind: str
    item_id: str
    claimant_user_id: str
    claimant_kind: str
    claim_type: str
    severity: str
    evidence_text: str | None
    evidence_url: str | None
    status: str
    reviewer_user_id: str | None
    decision_reason: str | None
    filed_at: float
    decided_at: float | None


@dataclass(frozen=True)
class QualityScore:
    item_kind: str
    item_id: str
    score: float
    rating_avg: float | None
    rating_count: int
    refund_count: int
    copyright_claims_open: int
    copyright_claims_upheld: int
    last_rated_at: float | None
    computed_at: float


# ---------- helpers ----------

def _validate_kind(item_kind: str) -> None:
    if item_kind not in VALID_ITEM_KINDS:
        raise ValueError(
            f"item_kind must be in {sorted(VALID_ITEM_KINDS)}",
        )


# ---------- ratings ----------

def rate(
    *,
    item_kind: str,
    item_id: str,
    user_id: str,
    rating: int,
    review_text: str | None = None,
) -> Rating:
    """Submit/update a rating. UNIQUE on (item, user) means
    re-rating updates the existing row + bumps updated_at.
    Triggers quality recompute."""
    _validate_kind(item_kind)
    if rating < 1 or rating > 5:
        raise ValueError("rating must be in [1, 5]")
    if review_text is not None and len(review_text) > 4000:
        raise ValueError("review_text must be ≤ 4000 chars")
    now = time.time()
    with _conn() as conn:
        existing = conn.execute(
            "SELECT id FROM market_ratings "
            "WHERE item_kind = ? AND item_id = ? AND user_id = ?",
            (item_kind, item_id, user_id),
        ).fetchone()
        if existing:
            rid = existing[0]
            conn.execute(
                "UPDATE market_ratings SET "
                " rating = ?, review_text = ?, updated_at = ? "
                "WHERE id = ?",
                (rating, review_text, now, rid),
            )
        else:
            rid = uuid.uuid4().hex
            conn.execute(
                "INSERT INTO market_ratings "
                "(id, item_kind, item_id, user_id, rating, "
                " review_text, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (rid, item_kind, item_id, user_id, rating,
                 review_text, now, now),
            )
    compute_quality_score(
        item_kind=item_kind, item_id=item_id,
    )
    return _get_rating(rid)  # type: ignore[return-value]


def _get_rating(rating_id: str) -> Rating | None:
    with _conn() as conn:
        r = conn.execute(
            "SELECT id, item_kind, item_id, user_id, rating, "
            "       review_text, helpful_count, created_at, "
            "       updated_at "
            "FROM market_ratings WHERE id = ?", (rating_id,),
        ).fetchone()
    if not r:
        return None
    return Rating(
        id=r[0], item_kind=r[1], item_id=r[2], user_id=r[3],
        rating=r[4], review_text=r[5], helpful_count=r[6],
        created_at=r[7], updated_at=r[8],
    )


def list_ratings_for_item(
    *,
    item_kind: str,
    item_id: str,
    limit: int = 50,
) -> list[Rating]:
    _validate_kind(item_kind)
    limit = max(1, min(limit, 500))
    with _conn() as conn:
        rows = conn.execute(
            "SELECT id, item_kind, item_id, user_id, rating, "
            "       review_text, helpful_count, created_at, "
            "       updated_at "
            "FROM market_ratings "
            "WHERE item_kind = ? AND item_id = ? "
            "ORDER BY helpful_count DESC, created_at DESC "
            "LIMIT ?",
            (item_kind, item_id, limit),
        ).fetchall()
    return [
        Rating(
            id=r[0], item_kind=r[1], item_id=r[2], user_id=r[3],
            rating=r[4], review_text=r[5], helpful_count=r[6],
            created_at=r[7], updated_at=r[8],
        )
        for r in rows
    ]


def mark_review_helpful(*, rating_id: str) -> bool:
    """Other students upvote a review — bumps helpful_count.
    No throttling here; v3.5 reactions does that via UNIQUE rows."""
    with _conn() as conn:
        cur = conn.execute(
            "UPDATE market_ratings SET "
            " helpful_count = helpful_count + 1 "
            "WHERE id = ?", (rating_id,),
        )
        return cur.rowcount > 0


# ---------- refunds ----------

def request_refund(
    *,
    item_kind: str,
    item_id: str,
    purchase_id: str,
    user_id: str,
    amount_paise: int,
    reason: str,
) -> RefundRequest:
    _validate_kind(item_kind)
    if amount_paise < 1:
        raise ValueError("amount_paise must be ≥ 1")
    reason = (reason or "").strip()
    if len(reason) < 5 or len(reason) > 1000:
        raise ValueError("reason must be [5, 1000] chars")
    rid = uuid.uuid4().hex
    now = time.time()
    sla = now + REFUND_SLA_HOURS * 3600
    with _conn() as conn:
        conn.execute(
            "INSERT INTO market_refund_requests "
            "(id, item_kind, item_id, purchase_id, user_id, "
            " amount_paise, reason, status, requested_at, "
            " sla_due_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)",
            (rid, item_kind, item_id, purchase_id, user_id,
             amount_paise, reason, now, sla),
        )
    return _get_refund(rid)  # type: ignore[return-value]


def _get_refund(refund_id: str) -> RefundRequest | None:
    with _conn() as conn:
        r = conn.execute(
            "SELECT id, item_kind, item_id, purchase_id, user_id, "
            "       amount_paise, reason, status, reviewer_user_id, "
            "       decision_reason, requested_at, decided_at, "
            "       sla_due_at "
            "FROM market_refund_requests WHERE id = ?",
            (refund_id,),
        ).fetchone()
    if not r:
        return None
    return RefundRequest(
        id=r[0], item_kind=r[1], item_id=r[2],
        purchase_id=r[3], user_id=r[4],
        amount_paise=r[5], reason=r[6], status=r[7],
        reviewer_user_id=r[8], decision_reason=r[9],
        requested_at=r[10], decided_at=r[11],
        sla_due_at=r[12],
    )


def get_refund(refund_id: str) -> RefundRequest | None:
    return _get_refund(refund_id)


def list_refund_queue(
    *,
    status: str = "pending",
    item_kind: str | None = None,
    limit: int = 50,
) -> list[RefundRequest]:
    if status not in VALID_REFUND_STATUSES:
        raise ValueError(
            f"status must be in {sorted(VALID_REFUND_STATUSES)}",
        )
    limit = max(1, min(limit, 500))
    sql = (
        "SELECT id, item_kind, item_id, purchase_id, user_id, "
        "       amount_paise, reason, status, reviewer_user_id, "
        "       decision_reason, requested_at, decided_at, "
        "       sla_due_at "
        "FROM market_refund_requests WHERE status = ?"
    )
    params: list = [status]
    if item_kind:
        _validate_kind(item_kind)
        sql += " AND item_kind = ?"; params.append(item_kind)
    sql += " ORDER BY requested_at LIMIT ?"
    params.append(limit)
    with _conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [
        RefundRequest(
            id=r[0], item_kind=r[1], item_id=r[2],
            purchase_id=r[3], user_id=r[4],
            amount_paise=r[5], reason=r[6], status=r[7],
            reviewer_user_id=r[8], decision_reason=r[9],
            requested_at=r[10], decided_at=r[11],
            sla_due_at=r[12],
        )
        for r in rows
    ]


def decide_refund(
    *,
    refund_id: str,
    action: str,
    reviewer_user_id: str,
    decision_reason: str | None = None,
) -> RefundRequest:
    if action not in VALID_REFUND_ACTIONS:
        raise ValueError(
            f"action must be in {sorted(VALID_REFUND_ACTIONS)}",
        )
    refund = _get_refund(refund_id)
    if not refund:
        raise ValueError("refund not found")
    if refund.status != "pending":
        raise ValueError(
            f"refund status is {refund.status!r}, not pending",
        )
    new_status = "approved" if action == "approve" else "rejected"
    now = time.time()
    with _conn() as conn:
        conn.execute(
            "UPDATE market_refund_requests SET "
            " status = ?, reviewer_user_id = ?, "
            " decision_reason = ?, decided_at = ? "
            "WHERE id = ?",
            (new_status, reviewer_user_id,
             (decision_reason or "")[:1000], now, refund_id),
        )
    # Approved refunds count against quality
    if new_status == "approved":
        compute_quality_score(
            item_kind=refund.item_kind, item_id=refund.item_id,
        )
    return _get_refund(refund_id)  # type: ignore[return-value]


def expire_stale_refunds() -> int:
    """Sweep — pending refunds past SLA flip to 'auto_expired'
    (counts as approved against the seller, protecting the buyer)."""
    now = time.time()
    with _conn() as conn:
        rows = conn.execute(
            "SELECT id, item_kind, item_id "
            "FROM market_refund_requests "
            "WHERE status = 'pending' AND sla_due_at < ?",
            (now,),
        ).fetchall()
        n = len(rows)
        if n:
            ids = [r[0] for r in rows]
            ph = ",".join("?" * len(ids))
            conn.execute(
                f"UPDATE market_refund_requests SET "
                " status = 'auto_expired', decided_at = ? "
                f"WHERE id IN ({ph})",
                [now] + ids,
            )
    # Recompute quality on affected items
    for _, ik, iid in rows:
        compute_quality_score(item_kind=ik, item_id=iid)
    return n


# ---------- copyright claims ----------

def file_copyright_claim(
    *,
    item_kind: str,
    item_id: str,
    claimant_user_id: str,
    claim_type: str,
    severity: str = "moderate",
    claimant_kind: str = "individual",
    evidence_text: str | None = None,
    evidence_url: str | None = None,
) -> CopyrightClaim:
    _validate_kind(item_kind)
    if claim_type not in VALID_CLAIM_TYPES:
        raise ValueError(
            f"claim_type must be in {sorted(VALID_CLAIM_TYPES)}",
        )
    if severity not in VALID_CLAIM_SEVERITIES:
        raise ValueError(
            f"severity must be in {sorted(VALID_CLAIM_SEVERITIES)}",
        )
    if claimant_kind not in VALID_CLAIMANT_KINDS:
        raise ValueError(
            f"claimant_kind must be in {sorted(VALID_CLAIMANT_KINDS)}",
        )
    if not evidence_text and not evidence_url:
        raise ValueError(
            "at least one of evidence_text / evidence_url required",
        )
    cid = uuid.uuid4().hex
    now = time.time()
    with _conn() as conn:
        conn.execute(
            "INSERT INTO market_copyright_claims "
            "(id, item_kind, item_id, claimant_user_id, "
            " claimant_kind, claim_type, severity, "
            " evidence_text, evidence_url, status, filed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?)",
            (cid, item_kind, item_id, claimant_user_id,
             claimant_kind, claim_type, severity,
             (evidence_text or "")[:8000] or None,
             evidence_url, now),
        )
    # Severe claims auto-flip item to under_review even before
    # reviewer touches it (precautionary).
    if severity == "severe":
        set_item_status(
            item_kind=item_kind, item_id=item_id,
            status="under_review",
            reason=f"severe copyright claim filed ({cid})",
            updated_by_user_id="system",
        )
    compute_quality_score(item_kind=item_kind, item_id=item_id)
    return _get_claim(cid)  # type: ignore[return-value]


def _get_claim(claim_id: str) -> CopyrightClaim | None:
    with _conn() as conn:
        r = conn.execute(
            "SELECT id, item_kind, item_id, claimant_user_id, "
            "       claimant_kind, claim_type, severity, "
            "       evidence_text, evidence_url, status, "
            "       reviewer_user_id, decision_reason, "
            "       filed_at, decided_at "
            "FROM market_copyright_claims WHERE id = ?",
            (claim_id,),
        ).fetchone()
    if not r:
        return None
    return CopyrightClaim(
        id=r[0], item_kind=r[1], item_id=r[2],
        claimant_user_id=r[3], claimant_kind=r[4],
        claim_type=r[5], severity=r[6],
        evidence_text=r[7], evidence_url=r[8],
        status=r[9], reviewer_user_id=r[10],
        decision_reason=r[11], filed_at=r[12],
        decided_at=r[13],
    )


def get_claim(claim_id: str) -> CopyrightClaim | None:
    return _get_claim(claim_id)


def list_claims(
    *,
    status: str = "open",
    item_kind: str | None = None,
    limit: int = 50,
) -> list[CopyrightClaim]:
    if status not in VALID_CLAIM_STATUSES:
        raise ValueError(
            f"status must be in {sorted(VALID_CLAIM_STATUSES)}",
        )
    limit = max(1, min(limit, 500))
    sql = (
        "SELECT id, item_kind, item_id, claimant_user_id, "
        "       claimant_kind, claim_type, severity, "
        "       evidence_text, evidence_url, status, "
        "       reviewer_user_id, decision_reason, "
        "       filed_at, decided_at "
        "FROM market_copyright_claims WHERE status = ?"
    )
    params: list = [status]
    if item_kind:
        _validate_kind(item_kind)
        sql += " AND item_kind = ?"; params.append(item_kind)
    sql += " ORDER BY filed_at LIMIT ?"
    params.append(limit)
    with _conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [
        CopyrightClaim(
            id=r[0], item_kind=r[1], item_id=r[2],
            claimant_user_id=r[3], claimant_kind=r[4],
            claim_type=r[5], severity=r[6],
            evidence_text=r[7], evidence_url=r[8],
            status=r[9], reviewer_user_id=r[10],
            decision_reason=r[11], filed_at=r[12],
            decided_at=r[13],
        )
        for r in rows
    ]


def decide_claim(
    *,
    claim_id: str,
    action: str,
    reviewer_user_id: str,
    decision_reason: str | None = None,
) -> CopyrightClaim:
    if action not in VALID_CLAIM_ACTIONS:
        raise ValueError(
            f"action must be in {sorted(VALID_CLAIM_ACTIONS)}",
        )
    claim = _get_claim(claim_id)
    if not claim:
        raise ValueError("claim not found")
    if claim.status != "open":
        raise ValueError(
            f"claim status is {claim.status!r}, not open",
        )
    new_status = "upheld" if action == "uphold" else "dismissed"
    now = time.time()
    with _conn() as conn:
        conn.execute(
            "UPDATE market_copyright_claims SET "
            " status = ?, reviewer_user_id = ?, "
            " decision_reason = ?, decided_at = ? "
            "WHERE id = ?",
            (new_status, reviewer_user_id,
             (decision_reason or "")[:1000], now, claim_id),
        )
    # Upheld severe claims → auto-remove item
    if (new_status == "upheld" and claim.severity == "severe"):
        set_item_status(
            item_kind=claim.item_kind,
            item_id=claim.item_id,
            status="removed",
            reason=f"severe copyright claim upheld ({claim_id})",
            updated_by_user_id=reviewer_user_id,
        )
    # Dismissed severe claims that were auto-set to under_review:
    # caller may want to restore. Out of scope here — admin
    # handles via set_item_status.
    compute_quality_score(
        item_kind=claim.item_kind, item_id=claim.item_id,
    )
    return _get_claim(claim_id)  # type: ignore[return-value]


# ---------- item status ----------

def set_item_status(
    *,
    item_kind: str,
    item_id: str,
    status: str,
    reason: str | None = None,
    updated_by_user_id: str | None = None,
) -> bool:
    _validate_kind(item_kind)
    if status not in VALID_ITEM_STATUSES:
        raise ValueError(
            f"status must be in {sorted(VALID_ITEM_STATUSES)}",
        )
    now = time.time()
    with _conn() as conn:
        conn.execute(
            "INSERT INTO market_item_status "
            "(item_kind, item_id, status, reason, "
            " updated_by_user_id, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(item_kind, item_id) DO UPDATE SET "
            " status = excluded.status, reason = excluded.reason, "
            " updated_by_user_id = excluded.updated_by_user_id, "
            " updated_at = excluded.updated_at",
            (item_kind, item_id, status, reason,
             updated_by_user_id, now),
        )
    return True


def get_item_status(
    *, item_kind: str, item_id: str,
) -> dict:
    """Returns the latest item status. Default 'active' if no row
    exists (most items live in active state without explicit row)."""
    _validate_kind(item_kind)
    with _conn() as conn:
        r = conn.execute(
            "SELECT status, reason, updated_by_user_id, updated_at "
            "FROM market_item_status "
            "WHERE item_kind = ? AND item_id = ?",
            (item_kind, item_id),
        ).fetchone()
    if not r:
        return {
            "status": "active", "reason": None,
            "updated_by_user_id": None, "updated_at": None,
        }
    return {
        "status": r[0], "reason": r[1],
        "updated_by_user_id": r[2], "updated_at": r[3],
    }


# ---------- quality score ----------

def compute_quality_score(
    *,
    item_kind: str,
    item_id: str,
) -> QualityScore:
    """Recompute + cache the quality score. Called automatically on
    every rating/refund/claim write."""
    _validate_kind(item_kind)
    now = time.time()
    with _conn() as conn:
        # Rating aggregate
        r1 = conn.execute(
            "SELECT COUNT(*), AVG(rating), MAX(updated_at) "
            "FROM market_ratings "
            "WHERE item_kind = ? AND item_id = ?",
            (item_kind, item_id),
        ).fetchone()
        rating_count = int(r1[0] or 0)
        rating_avg = float(r1[1]) if r1[1] is not None else None
        last_rated_at = r1[2]

        # Refund count (approved + auto_expired)
        r2 = conn.execute(
            "SELECT COUNT(*) FROM market_refund_requests "
            "WHERE item_kind = ? AND item_id = ? "
            "AND status IN ('approved', 'auto_expired')",
            (item_kind, item_id),
        ).fetchone()
        refund_count = int(r2[0] or 0)

        # Copyright claims
        r3 = conn.execute(
            "SELECT status, severity, COUNT(*) "
            "FROM market_copyright_claims "
            "WHERE item_kind = ? AND item_id = ? "
            "GROUP BY status, severity",
            (item_kind, item_id),
        ).fetchall()

    open_claims = sum(c for s, _, c in r3 if s == "open")
    upheld_claims = sum(c for s, _, c in r3 if s == "upheld")

    # Compute composite
    if rating_avg is None:
        rating_score = 50.0   # neutral default before any rating
    else:
        rating_score = rating_avg * 20.0

    # Refund penalty — relative to rating count as a sales proxy
    # (lacking direct sales data here; quality module operates on
    # public signals).
    denom = max(rating_count, 1)
    refund_pct = refund_count / denom * 100
    refund_penalty = min(
        REFUND_PENALTY_PER_PCT * refund_pct,
        REFUND_PENALTY_CAP,
    )

    # Copyright penalty — most-severe upheld claim drives it
    copyright_penalty = 0.0
    for status, severity, count in r3:
        if status == "upheld":
            copyright_penalty = max(
                copyright_penalty,
                COPYRIGHT_PENALTY.get(severity, 0.0),
            )

    # Recency boost
    recency_boost = 0.0
    if (last_rated_at
            and (now - last_rated_at) < RECENCY_BOOST_DAYS * 86400):
        recency_boost = RECENCY_BOOST

    score = (
        rating_score - refund_penalty
        - copyright_penalty + recency_boost
    )
    score = max(0.0, min(100.0, score))

    with _conn() as conn:
        conn.execute(
            "INSERT INTO market_quality_scores "
            "(item_kind, item_id, score, rating_avg, "
            " rating_count, refund_count, "
            " copyright_claims_open, copyright_claims_upheld, "
            " last_rated_at, computed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(item_kind, item_id) DO UPDATE SET "
            " score = excluded.score, "
            " rating_avg = excluded.rating_avg, "
            " rating_count = excluded.rating_count, "
            " refund_count = excluded.refund_count, "
            " copyright_claims_open = excluded.copyright_claims_open, "
            " copyright_claims_upheld = excluded.copyright_claims_upheld, "
            " last_rated_at = excluded.last_rated_at, "
            " computed_at = excluded.computed_at",
            (item_kind, item_id, round(score, 2),
             round(rating_avg, 3) if rating_avg is not None else None,
             rating_count, refund_count,
             open_claims, upheld_claims,
             last_rated_at, now),
        )
    # Auto-flip status when quality drops
    if (rating_count >= 3
            and score < QUALITY_UNDER_REVIEW_THRESHOLD):
        current_status = get_item_status(
            item_kind=item_kind, item_id=item_id,
        )["status"]
        # Don't override removed/rejected with under_review
        if current_status == "active":
            set_item_status(
                item_kind=item_kind, item_id=item_id,
                status="under_review",
                reason=f"quality score {score:.1f} below threshold",
                updated_by_user_id="system",
            )
    return QualityScore(
        item_kind=item_kind, item_id=item_id,
        score=round(score, 2),
        rating_avg=(
            round(rating_avg, 3) if rating_avg is not None else None
        ),
        rating_count=rating_count,
        refund_count=refund_count,
        copyright_claims_open=open_claims,
        copyright_claims_upheld=upheld_claims,
        last_rated_at=last_rated_at,
        computed_at=now,
    )


def get_quality(
    *, item_kind: str, item_id: str,
) -> QualityScore | None:
    _validate_kind(item_kind)
    with _conn() as conn:
        r = conn.execute(
            "SELECT item_kind, item_id, score, rating_avg, "
            "       rating_count, refund_count, "
            "       copyright_claims_open, "
            "       copyright_claims_upheld, "
            "       last_rated_at, computed_at "
            "FROM market_quality_scores "
            "WHERE item_kind = ? AND item_id = ?",
            (item_kind, item_id),
        ).fetchone()
    if not r:
        return None
    return QualityScore(
        item_kind=r[0], item_id=r[1], score=r[2],
        rating_avg=r[3], rating_count=r[4],
        refund_count=r[5],
        copyright_claims_open=r[6],
        copyright_claims_upheld=r[7],
        last_rated_at=r[8], computed_at=r[9],
    )


def top_items_by_quality(
    *,
    item_kind: str | None = None,
    min_rating_count: int = 3,
    limit: int = 20,
) -> list[QualityScore]:
    """Best-selling card on the marketplace home — drives the
    'top rated' / 'most trusted' list."""
    limit = max(1, min(limit, 200))
    sql = (
        "SELECT item_kind, item_id, score, rating_avg, "
        "       rating_count, refund_count, "
        "       copyright_claims_open, copyright_claims_upheld, "
        "       last_rated_at, computed_at "
        "FROM market_quality_scores "
        "WHERE rating_count >= ?"
    )
    params: list = [min_rating_count]
    if item_kind:
        _validate_kind(item_kind)
        sql += " AND item_kind = ?"; params.append(item_kind)
    sql += " ORDER BY score DESC, rating_count DESC LIMIT ?"
    params.append(limit)
    with _conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [
        QualityScore(
            item_kind=r[0], item_id=r[1], score=r[2],
            rating_avg=r[3], rating_count=r[4],
            refund_count=r[5],
            copyright_claims_open=r[6],
            copyright_claims_upheld=r[7],
            last_rated_at=r[8], computed_at=r[9],
        )
        for r in rows
    ]
