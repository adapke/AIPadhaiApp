"""Parent ↔ child account linking with DPDP §9 consent audit.

Different from the bootstrap-signup DPDP flow in padhai/dpdp.py (which
handles "user under 13 signs up; parent must verify"). This module
covers the *post-signup* case: a parent who already has an AI Pathshala
account wants to link to their (already-signed-up) child to see
progress, pay fees, and receive notifications.

Both directions are supported:
- Parent invites child by email (typical school onboarding path)
- Child invites parent (less common but real — e.g. a college student
  wants their parent to see their analytics)

Verification flow:
  1. Initiator hits POST /api/parents/link with the other party's email
  2. We mint a `parent_links` row in status='pending' + a single-use
     verification token (8-day TTL)
  3. The other party gets an in-app notification AND a queued email
     (outbox stub until SMTP wires; same pattern as DPDP)
  4. They GET /auth/parent-link/verify?t=<token>
  5. Verified: status flips to 'verified', consent timestamp + IP
     recorded for audit
  6. Either side can revoke at any time → status='revoked'; the
     consent record stays in the audit trail.

Schema (additive — same DB as orgs/jobs):

  parent_links
    id, parent_user_id, child_user_id, relation (father|mother|guardian),
    consent_signed_at, consent_ip, status (pending|verified|revoked),
    initiated_by (parent|child), created_at

  parent_link_tokens
    token, link_id, audience (parent|child),  -- who must click
    created_at, expires_at
"""

from __future__ import annotations

import os
import secrets
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Literal


TOKEN_TTL_SECONDS = 8 * 86400  # 8 days

VALID_RELATIONS = {"father", "mother", "guardian", "other"}
VALID_STATUSES = {"pending", "verified", "revoked"}

LinkStatus = Literal["pending", "verified", "revoked"]


SCHEMA = """
CREATE TABLE IF NOT EXISTS parent_links (
    id              TEXT PRIMARY KEY,
    parent_user_id  TEXT NOT NULL,
    child_user_id   TEXT NOT NULL,
    relation        TEXT,
    initiated_by    TEXT NOT NULL,         -- 'parent' or 'child'
    status          TEXT NOT NULL,         -- pending | verified | revoked
    consent_signed_at REAL,                -- only set when status='verified'
    consent_ip      TEXT,
    created_at      REAL NOT NULL,
    revoked_at      REAL,
    revoked_by      TEXT,                  -- user_id of revoker
    UNIQUE (parent_user_id, child_user_id)
);
CREATE INDEX IF NOT EXISTS idx_pl_parent ON parent_links(parent_user_id);
CREATE INDEX IF NOT EXISTS idx_pl_child  ON parent_links(child_user_id);

CREATE TABLE IF NOT EXISTS parent_link_tokens (
    token       TEXT PRIMARY KEY,
    link_id     TEXT NOT NULL,
    audience    TEXT NOT NULL,             -- 'parent' or 'child'
    created_at  REAL NOT NULL,
    expires_at  REAL NOT NULL
);
"""


def _db_path() -> Path:
    from . import db as _db
    return _db.sqlite_path()


def _conn() -> sqlite3.Connection:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=10.0)
    conn.executescript(SCHEMA)
    return conn


# ---------- dataclasses ----------

@dataclass(frozen=True)
class ParentLink:
    id: str
    parent_user_id: str
    child_user_id: str
    relation: str | None
    initiated_by: str
    status: str
    consent_signed_at: float | None
    consent_ip: str | None
    created_at: float
    revoked_at: float | None
    revoked_by: str | None


# ---------- create + invite ----------

def invite(
    *,
    parent_user_id: str,
    child_user_id: str,
    initiated_by: Literal["parent", "child"],
    relation: str | None = None,
) -> tuple[ParentLink, str]:
    """Create a pending link + mint a verification token for the OTHER
    party (whoever didn't initiate).

    Raises ValueError on:
      - parent and child are the same user
      - link already exists in 'pending' or 'verified' state
      - unknown relation

    Returns (link, verify_token). The token is single-use; verify() or
    revoke() removes it. The caller composes the verify URL.
    """
    if parent_user_id == child_user_id:
        raise ValueError("a user cannot link to themselves")
    if initiated_by not in ("parent", "child"):
        raise ValueError("initiated_by must be 'parent' or 'child'")
    if relation and relation not in VALID_RELATIONS:
        raise ValueError(f"relation must be in {sorted(VALID_RELATIONS)}")

    lid = uuid.uuid4().hex
    now = time.time()
    audience = "child" if initiated_by == "parent" else "parent"

    with _conn() as conn:
        existing = conn.execute(
            "SELECT id, status FROM parent_links "
            "WHERE parent_user_id = ? AND child_user_id = ?",
            (parent_user_id, child_user_id),
        ).fetchone()
        if existing and existing[1] != "revoked":
            raise ValueError(
                f"link already exists (status={existing[1]}); "
                f"revoke first if you want to re-invite",
            )
        # If a previously-revoked row exists, replace it.
        if existing:
            conn.execute("DELETE FROM parent_links WHERE id = ?", (existing[0],))
        conn.execute(
            "INSERT INTO parent_links "
            "(id, parent_user_id, child_user_id, relation, initiated_by, "
            " status, created_at) "
            "VALUES (?, ?, ?, ?, ?, 'pending', ?)",
            (lid, parent_user_id, child_user_id, relation, initiated_by, now),
        )
        token = secrets.token_urlsafe(32)
        conn.execute(
            "INSERT INTO parent_link_tokens "
            "(token, link_id, audience, created_at, expires_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (token, lid, audience, now, now + TOKEN_TTL_SECONDS),
        )

    return (_by_id(lid), token)


def verify(*, token: str, acting_user_id: str, acting_ip: str) -> ParentLink:
    """Single-use token redemption. Requires that `acting_user_id`
    matches the expected audience (the party who didn't initiate).
    Records consent timestamp + IP per DPDP §9 audit requirements.
    Raises ValueError on invalid/expired token or wrong user.
    """
    now = time.time()
    with _conn() as conn:
        row = conn.execute(
            "SELECT link_id, audience, expires_at "
            "FROM parent_link_tokens WHERE token = ?",
            (token,),
        ).fetchone()
        if not row:
            raise ValueError("invalid token")
        link_id, audience, expires_at = row
        if expires_at < now:
            conn.execute("DELETE FROM parent_link_tokens WHERE token = ?", (token,))
            raise ValueError("token expired; ask the inviter to resend")

        link_row = conn.execute(
            "SELECT parent_user_id, child_user_id, status FROM parent_links "
            "WHERE id = ?",
            (link_id,),
        ).fetchone()
        if not link_row:
            raise ValueError("link no longer exists")
        parent_uid, child_uid, current_status = link_row
        if current_status == "revoked":
            raise ValueError("link was revoked; ask the inviter to re-invite")
        if current_status == "verified":
            # Idempotent — token was already used, just return the row
            conn.execute("DELETE FROM parent_link_tokens WHERE token = ?", (token,))
            return _by_id(link_id)

        # Verify the acting user is the expected audience
        expected_user = parent_uid if audience == "parent" else child_uid
        if acting_user_id != expected_user:
            raise ValueError(
                f"this verification link is for the {audience}, not you. "
                f"Ask them to sign in and click the link.",
            )

        conn.execute(
            "UPDATE parent_links SET status = 'verified', "
            "consent_signed_at = ?, consent_ip = ? WHERE id = ?",
            (now, acting_ip, link_id),
        )
        conn.execute("DELETE FROM parent_link_tokens WHERE token = ?", (token,))
    return _by_id(link_id)


def revoke(*, link_id: str, acting_user_id: str) -> ParentLink:
    """Either side can sever the link. The consent record stays in the
    audit trail (status='revoked' + revoked_at/by columns)."""
    with _conn() as conn:
        link_row = conn.execute(
            "SELECT parent_user_id, child_user_id, status FROM parent_links "
            "WHERE id = ?",
            (link_id,),
        ).fetchone()
        if not link_row:
            raise ValueError("link not found")
        parent_uid, child_uid, status = link_row
        if acting_user_id not in (parent_uid, child_uid):
            raise ValueError("only the linked parent or child may revoke")
        if status == "revoked":
            return _by_id(link_id)
        conn.execute(
            "UPDATE parent_links SET status = 'revoked', "
            "revoked_at = ?, revoked_by = ? WHERE id = ?",
            (time.time(), acting_user_id, link_id),
        )
        conn.execute(
            "DELETE FROM parent_link_tokens WHERE link_id = ?", (link_id,),
        )
    return _by_id(link_id)


# ---------- read ----------

def _by_id(lid: str) -> ParentLink:
    with _conn() as conn:
        r = conn.execute(
            "SELECT id, parent_user_id, child_user_id, relation, "
            "       initiated_by, status, consent_signed_at, consent_ip, "
            "       created_at, revoked_at, revoked_by "
            "FROM parent_links WHERE id = ?",
            (lid,),
        ).fetchone()
    if not r:
        raise KeyError(f"link {lid!r} not found")
    return ParentLink(
        id=r[0], parent_user_id=r[1], child_user_id=r[2], relation=r[3],
        initiated_by=r[4], status=r[5], consent_signed_at=r[6],
        consent_ip=r[7], created_at=r[8], revoked_at=r[9], revoked_by=r[10],
    )


def children_of(parent_user_id: str, *, include_revoked: bool = False) -> list[ParentLink]:
    """All children linked to this parent. Includes pending unless
    `include_revoked=False` (which is the default — pending is fine to
    show as "Awaiting verification")."""
    where = "parent_user_id = ?"
    params: list = [parent_user_id]
    if not include_revoked:
        where += " AND status != 'revoked'"
    with _conn() as conn:
        rows = conn.execute(
            f"SELECT id FROM parent_links WHERE {where} "
            "ORDER BY created_at DESC",
            params,
        ).fetchall()
    return [_by_id(r[0]) for r in rows]


def parents_of(child_user_id: str, *, include_revoked: bool = False) -> list[ParentLink]:
    where = "child_user_id = ?"
    params: list = [child_user_id]
    if not include_revoked:
        where += " AND status != 'revoked'"
    with _conn() as conn:
        rows = conn.execute(
            f"SELECT id FROM parent_links WHERE {where} "
            "ORDER BY created_at DESC",
            params,
        ).fetchall()
    return [_by_id(r[0]) for r in rows]


def is_verified_parent_of(parent_user_id: str, child_user_id: str) -> bool:
    """Used as the permission gate for parent → child data access."""
    with _conn() as conn:
        r = conn.execute(
            "SELECT 1 FROM parent_links WHERE parent_user_id = ? "
            "AND child_user_id = ? AND status = 'verified'",
            (parent_user_id, child_user_id),
        ).fetchone()
    return r is not None
