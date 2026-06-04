"""R4 — Affiliate program.

Influencers + teachers + edu-creators register, get a unique
referral link, earn 10% commission on the first 12 months of any
subscription their link converts.

Tracking flow:
  1. Affiliate registers → gets affiliate_code (short slug)
  2. Their link: https://padhai.app/?ref=<code>
  3. Visit lands → we record an `affiliate_visit` (with utm_*)
  4. If the user signs up + subscribes within `ATTRIBUTION_WINDOW_DAYS`,
     attribute the conversion + start the 12-month commission clock
  5. Every paid invoice during the 12-month window books a
     `commission_event` at 10% (configurable per affiliate for top
     performers)

Different from R3 (vouchers — promotional, buyer-facing) — R4 is
**partner-facing** with a recurring payout. Different from
O1/O2/O3 (marketplaces — partners sell their own content) — R4
partners sell OUR content for commission.

Tables:
  affiliates              one row per partner (slug PK)
  affiliate_visits        click tracking (rolled up to daily aggregate)
  affiliate_attributions  one row per (user_id, affiliate) — the
                          12-month commission window starts here
  commission_events       one row per qualifying paid invoice
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS affiliates (
    code                TEXT PRIMARY KEY,         -- short slug 'creator_alice'
    user_id             TEXT NOT NULL,
    display_name        TEXT NOT NULL,
    email               TEXT,
    kind                TEXT NOT NULL,            -- 'influencer' | 'teacher' | 'creator' | 'partner_org'
    commission_pct      REAL NOT NULL DEFAULT 0.10,
    payout_account_id   TEXT,
    status              TEXT NOT NULL DEFAULT 'active',
    -- 'active' | 'paused' | 'suspended'
    total_clicks        INTEGER NOT NULL DEFAULT 0,
    total_conversions   INTEGER NOT NULL DEFAULT 0,
    total_earned_paise  INTEGER NOT NULL DEFAULT 0,
    notes               TEXT,
    created_at          REAL NOT NULL,
    UNIQUE (user_id)
);
CREATE INDEX IF NOT EXISTS idx_aff_status
    ON affiliates(status, total_earned_paise DESC);

CREATE TABLE IF NOT EXISTS affiliate_visits (
    id              TEXT PRIMARY KEY,
    affiliate_code  TEXT NOT NULL,
    visited_at      REAL NOT NULL,
    landing_path    TEXT,                         -- '/courses/cbse-10-math'
    utm_source      TEXT,
    utm_medium      TEXT,
    utm_campaign    TEXT,
    user_agent      TEXT,
    -- We hash IPs at write time; PDP-DPDP §10 forbids storing raw IPs
    -- for behavioural analytics without consent
    ip_hash         TEXT
);
CREATE INDEX IF NOT EXISTS idx_avisits_code
    ON affiliate_visits(affiliate_code, visited_at DESC);

CREATE TABLE IF NOT EXISTS affiliate_attributions (
    id                  TEXT PRIMARY KEY,
    user_id             TEXT NOT NULL,
    affiliate_code      TEXT NOT NULL,
    attributed_at       REAL NOT NULL,
    commission_until    REAL NOT NULL,            -- attributed_at + 12mo
    landing_visit_id    TEXT,                     -- visit that drove the conversion
    UNIQUE (user_id)                              -- last-touch attribution
);
CREATE INDEX IF NOT EXISTS idx_aattr_aff
    ON affiliate_attributions(affiliate_code, attributed_at DESC);

CREATE TABLE IF NOT EXISTS commission_events (
    id                  TEXT PRIMARY KEY,
    affiliate_code      TEXT NOT NULL,
    user_id             TEXT NOT NULL,
    invoice_id          TEXT NOT NULL,
    invoice_paise       INTEGER NOT NULL,
    commission_paise    INTEGER NOT NULL,
    booked_at           REAL NOT NULL,
    paid_at             REAL,                     -- NULL = pending
    UNIQUE (affiliate_code, invoice_id)
);
CREATE INDEX IF NOT EXISTS idx_cevents_aff
    ON commission_events(affiliate_code, booked_at DESC);
CREATE INDEX IF NOT EXISTS idx_cevents_pending
    ON commission_events(paid_at, booked_at DESC);
"""


VALID_AFFILIATE_KINDS = frozenset({
    "influencer", "teacher", "creator", "partner_org",
})

VALID_AFFILIATE_STATUSES = frozenset({
    "active", "paused", "suspended",
})

# Last-touch attribution window: a click within these many days
# before sign-up still counts. 30 days lines up with typical
# affiliate networks (Impact, Commission Junction default).
ATTRIBUTION_WINDOW_DAYS = 30

# 12 months on the commission clock starting at attribution.
COMMISSION_WINDOW_DAYS = 365

MIN_COMMISSION_PCT = 0.01
MAX_COMMISSION_PCT = 0.30


_CODE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{2,31}$")


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
class Affiliate:
    code: str
    user_id: str
    display_name: str
    email: str | None
    kind: str
    commission_pct: float
    payout_account_id: str | None
    status: str
    total_clicks: int
    total_conversions: int
    total_earned_paise: int
    notes: str | None
    created_at: float


@dataclass(frozen=True)
class Visit:
    id: str
    affiliate_code: str
    visited_at: float
    landing_path: str | None
    utm_source: str | None
    utm_medium: str | None
    utm_campaign: str | None


@dataclass(frozen=True)
class Attribution:
    id: str
    user_id: str
    affiliate_code: str
    attributed_at: float
    commission_until: float


@dataclass(frozen=True)
class CommissionEvent:
    id: str
    affiliate_code: str
    user_id: str
    invoice_id: str
    invoice_paise: int
    commission_paise: int
    booked_at: float
    paid_at: float | None


_AFF_SEL = (
    "SELECT code, user_id, display_name, email, kind, "
    "       commission_pct, payout_account_id, status, "
    "       total_clicks, total_conversions, total_earned_paise, "
    "       notes, created_at "
    "FROM affiliates"
)


def _row_to_affiliate(r) -> Affiliate:
    return Affiliate(
        code=r[0], user_id=r[1], display_name=r[2],
        email=r[3], kind=r[4], commission_pct=r[5],
        payout_account_id=r[6], status=r[7],
        total_clicks=r[8], total_conversions=r[9],
        total_earned_paise=r[10], notes=r[11],
        created_at=r[12],
    )


# ---------- registration ----------

def register_affiliate(
    *,
    user_id: str,
    code: str,
    display_name: str,
    kind: str,
    email: str | None = None,
    commission_pct: float = 0.10,
) -> Affiliate:
    code = (code or "").strip().lower()
    if not _CODE_RE.match(code):
        raise ValueError(
            "code must be 3-32 lowercase letters/digits/_/- "
            "starting alphanumeric",
        )
    display_name = (display_name or "").strip()
    if len(display_name) < 2 or len(display_name) > 100:
        raise ValueError("display_name must be [2, 100] chars")
    if kind not in VALID_AFFILIATE_KINDS:
        raise ValueError(
            f"kind must be in {sorted(VALID_AFFILIATE_KINDS)}",
        )
    if commission_pct < MIN_COMMISSION_PCT or commission_pct > MAX_COMMISSION_PCT:
        raise ValueError(
            f"commission_pct must be in "
            f"[{MIN_COMMISSION_PCT}, {MAX_COMMISSION_PCT}]",
        )
    try:
        with _conn() as conn:
            conn.execute(
                "INSERT INTO affiliates "
                "(code, user_id, display_name, email, kind, "
                " commission_pct, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (code, user_id, display_name, email, kind,
                 commission_pct, time.time()),
            )
    except sqlite3.IntegrityError as e:
        # Either code or user_id collision
        raise ValueError(
            "affiliate code or user already registered",
        ) from e
    return get_affiliate_by_code(code)  # type: ignore[return-value]


def get_affiliate_by_code(code: str) -> Affiliate | None:
    with _conn() as conn:
        r = conn.execute(
            _AFF_SEL + " WHERE code = ?", (code.lower(),),
        ).fetchone()
    return _row_to_affiliate(r) if r else None


def get_affiliate_by_user(user_id: str) -> Affiliate | None:
    with _conn() as conn:
        r = conn.execute(
            _AFF_SEL + " WHERE user_id = ?", (user_id,),
        ).fetchone()
    return _row_to_affiliate(r) if r else None


def list_affiliates(*, status: str | None = None) -> list[Affiliate]:
    sql = _AFF_SEL
    params: list = []
    if status:
        if status not in VALID_AFFILIATE_STATUSES:
            raise ValueError(
                f"status must be in {sorted(VALID_AFFILIATE_STATUSES)}",
            )
        sql += " WHERE status = ?"; params.append(status)
    sql += " ORDER BY total_earned_paise DESC, display_name"
    with _conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_row_to_affiliate(r) for r in rows]


def set_affiliate_status(*, code: str, status: str) -> bool:
    if status not in VALID_AFFILIATE_STATUSES:
        raise ValueError(
            f"status must be in {sorted(VALID_AFFILIATE_STATUSES)}",
        )
    with _conn() as conn:
        cur = conn.execute(
            "UPDATE affiliates SET status = ? WHERE code = ?",
            (status, code.lower()),
        )
        return cur.rowcount > 0


# ---------- tracking ----------

def record_visit(
    *,
    code: str,
    landing_path: str | None = None,
    utm_source: str | None = None,
    utm_medium: str | None = None,
    utm_campaign: str | None = None,
    user_agent: str | None = None,
    ip_hash: str | None = None,
) -> str | None:
    """Record an inbound click from an affiliate link. Returns the
    visit id, or None if the code is unknown / suspended."""
    aff = get_affiliate_by_code(code)
    if not aff or aff.status != "active":
        return None
    vid = uuid.uuid4().hex
    with _conn() as conn:
        conn.execute(
            "INSERT INTO affiliate_visits "
            "(id, affiliate_code, visited_at, landing_path, "
            " utm_source, utm_medium, utm_campaign, "
            " user_agent, ip_hash) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (vid, aff.code, time.time(),
             (landing_path or "")[:500] or None,
             (utm_source or "")[:80] or None,
             (utm_medium or "")[:80] or None,
             (utm_campaign or "")[:80] or None,
             (user_agent or "")[:300] or None,
             (ip_hash or "")[:64] or None),
        )
        conn.execute(
            "UPDATE affiliates SET total_clicks = total_clicks + 1 "
            "WHERE code = ?", (aff.code,),
        )
    return vid


def attribute_user(
    *,
    user_id: str,
    affiliate_code: str,
    landing_visit_id: str | None = None,
) -> Attribution | None:
    """Bind a user to an affiliate. Idempotent: first-attribution
    wins (no rebinding to a later code). Returns None if affiliate
    inactive."""
    aff = get_affiliate_by_code(affiliate_code)
    if not aff or aff.status != "active":
        return None
    now = time.time()
    until = now + COMMISSION_WINDOW_DAYS * 86400
    aid = uuid.uuid4().hex
    with _conn() as conn:
        # Existing attribution?
        r = conn.execute(
            "SELECT id, user_id, affiliate_code, attributed_at, "
            "       commission_until "
            "FROM affiliate_attributions WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        if r:
            return Attribution(
                id=r[0], user_id=r[1], affiliate_code=r[2],
                attributed_at=r[3], commission_until=r[4],
            )
        conn.execute(
            "INSERT INTO affiliate_attributions "
            "(id, user_id, affiliate_code, attributed_at, "
            " commission_until, landing_visit_id) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (aid, user_id, aff.code, now, until, landing_visit_id),
        )
        conn.execute(
            "UPDATE affiliates SET "
            " total_conversions = total_conversions + 1 "
            "WHERE code = ?", (aff.code,),
        )
    return Attribution(
        id=aid, user_id=user_id, affiliate_code=aff.code,
        attributed_at=now, commission_until=until,
    )


def get_attribution(user_id: str) -> Attribution | None:
    with _conn() as conn:
        r = conn.execute(
            "SELECT id, user_id, affiliate_code, attributed_at, "
            "       commission_until "
            "FROM affiliate_attributions WHERE user_id = ?",
            (user_id,),
        ).fetchone()
    if not r:
        return None
    return Attribution(
        id=r[0], user_id=r[1], affiliate_code=r[2],
        attributed_at=r[3], commission_until=r[4],
    )


# ---------- commissions ----------

def book_commission(
    *,
    user_id: str,
    invoice_id: str,
    invoice_paise: int,
) -> CommissionEvent | None:
    """Called from the payments webhook on every paid invoice.
    Returns the commission event if attribution applies + the
    invoice falls inside the 12-month window. Idempotent on
    (affiliate, invoice)."""
    if invoice_paise < 1:
        raise ValueError("invoice_paise must be ≥1")
    attribution = get_attribution(user_id)
    if not attribution:
        return None
    now = time.time()
    if now > attribution.commission_until:
        return None
    aff = get_affiliate_by_code(attribution.affiliate_code)
    if not aff or aff.status != "active":
        return None
    commission = int(round(invoice_paise * aff.commission_pct))
    eid = uuid.uuid4().hex
    with _conn() as conn:
        try:
            conn.execute(
                "INSERT INTO commission_events "
                "(id, affiliate_code, user_id, invoice_id, "
                " invoice_paise, commission_paise, booked_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (eid, aff.code, user_id, invoice_id,
                 invoice_paise, commission, now),
            )
        except sqlite3.IntegrityError:
            # Already booked this invoice — fetch + return existing
            r = conn.execute(
                "SELECT id, affiliate_code, user_id, invoice_id, "
                "       invoice_paise, commission_paise, "
                "       booked_at, paid_at "
                "FROM commission_events "
                "WHERE affiliate_code = ? AND invoice_id = ?",
                (aff.code, invoice_id),
            ).fetchone()
            if r:
                return CommissionEvent(
                    id=r[0], affiliate_code=r[1], user_id=r[2],
                    invoice_id=r[3], invoice_paise=r[4],
                    commission_paise=r[5], booked_at=r[6],
                    paid_at=r[7],
                )
            return None
        conn.execute(
            "UPDATE affiliates SET "
            " total_earned_paise = total_earned_paise + ? "
            "WHERE code = ?", (commission, aff.code),
        )
    return CommissionEvent(
        id=eid, affiliate_code=aff.code, user_id=user_id,
        invoice_id=invoice_id, invoice_paise=invoice_paise,
        commission_paise=commission, booked_at=now,
        paid_at=None,
    )


def mark_commission_paid(commission_id: str) -> bool:
    """Admin path — bookkeeping confirms the commission was paid
    out to the affiliate (Razorpay payout)."""
    with _conn() as conn:
        cur = conn.execute(
            "UPDATE commission_events SET paid_at = ? "
            "WHERE id = ? AND paid_at IS NULL",
            (time.time(), commission_id),
        )
        return cur.rowcount > 0


def affiliate_earnings(code: str) -> dict:
    code = code.lower()
    with _conn() as conn:
        r = conn.execute(
            "SELECT COUNT(*), "
            "       COALESCE(SUM(commission_paise), 0), "
            "       COALESCE(SUM(CASE WHEN paid_at IS NULL THEN "
            "                         commission_paise ELSE 0 END), 0), "
            "       COALESCE(SUM(CASE WHEN paid_at IS NOT NULL THEN "
            "                         commission_paise ELSE 0 END), 0) "
            "FROM commission_events WHERE affiliate_code = ?",
            (code,),
        ).fetchone()
        clicks = conn.execute(
            "SELECT total_clicks, total_conversions, "
            "       total_earned_paise "
            "FROM affiliates WHERE code = ?",
            (code,),
        ).fetchone()
    if not clicks:
        return {}
    return {
        "total_clicks": clicks[0],
        "total_conversions": clicks[1],
        "total_earned_paise": clicks[2],
        "commission_events": int(r[0] or 0),
        "total_commission_paise": int(r[1] or 0),
        "pending_paise": int(r[2] or 0),
        "paid_paise": int(r[3] or 0),
        "conversion_rate": round(
            (clicks[1] / clicks[0]) if clicks[0] else 0, 4,
        ),
    }


def list_commission_events(
    *,
    affiliate_code: str | None = None,
    pending_only: bool = False,
    limit: int = 100,
) -> list[CommissionEvent]:
    limit = max(1, min(limit, 1000))
    sql = (
        "SELECT id, affiliate_code, user_id, invoice_id, "
        "       invoice_paise, commission_paise, booked_at, paid_at "
        "FROM commission_events WHERE 1=1"
    )
    params: list = []
    if affiliate_code:
        sql += " AND affiliate_code = ?"
        params.append(affiliate_code.lower())
    if pending_only:
        sql += " AND paid_at IS NULL"
    sql += " ORDER BY booked_at DESC LIMIT ?"
    params.append(limit)
    with _conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [
        CommissionEvent(
            id=r[0], affiliate_code=r[1], user_id=r[2],
            invoice_id=r[3], invoice_paise=r[4],
            commission_paise=r[5], booked_at=r[6], paid_at=r[7],
        )
        for r in rows
    ]


def program_summary() -> dict:
    """Top-line affiliate program metrics — drives the GTM
    leadership dashboard."""
    with _conn() as conn:
        r = conn.execute(
            "SELECT COUNT(*), "
            "       COALESCE(SUM(total_clicks), 0), "
            "       COALESCE(SUM(total_conversions), 0), "
            "       COALESCE(SUM(total_earned_paise), 0) "
            "FROM affiliates WHERE status = 'active'",
        ).fetchone()
        pending = conn.execute(
            "SELECT COALESCE(SUM(commission_paise), 0) "
            "FROM commission_events WHERE paid_at IS NULL",
        ).fetchone()
    return {
        "active_affiliates": int(r[0] or 0),
        "total_clicks": int(r[1] or 0),
        "total_conversions": int(r[2] or 0),
        "total_commissioned_paise": int(r[3] or 0),
        "pending_payouts_paise": int(pending[0] or 0),
    }
