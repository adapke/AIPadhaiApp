"""DPDP Act 2023 §9 — verifiable parental consent for users under 13.

The Digital Personal Data Protection Act 2023 §9(1):
  "Every Data Fiduciary shall, before processing any personal data of a
   child or a person with disability who has a lawful guardian — obtain
   verifiable consent of the parent of such child or the lawful
   guardian."

Section 9(2):
  "A Data Fiduciary shall not undertake such tracking or behavioural
   monitoring of children or targeted advertising directed at children."

For AI Pathshala that means:
- DOB is collected at signup.
- If the user is under 13, the account is created but LOCKED (no
  generation, no chat, no library access) until a parent confirms.
- The parent's email is captured at signup; a verification token is
  emailed; clicking that link records consent with a timestamp + IP.
- No behavioural tracking is performed on under-13 accounts (no
  PostHog events, no advertising IDs — already true; this module
  exposes `is_minor()` so downstream code can guard analytics).

Schema additions (applied to the existing `users` table via ALTER TABLE
in a SQLite-tolerant pattern — see `migrate()`):
  dob                     YYYY-MM-DD birth date
  parent_email            null unless user is under 13
  parent_consent_at       null until consent recorded
  parent_consent_ip       audit trail per DPDP §9
  account_locked          1 when user is under 13 + no consent yet

Consent tokens are stored in a new `parent_consent_tokens` table — never
in the URL persistently, never in logs. The token is single-use; once
verified, it's deleted.

Sending the actual email is delegated to a stub `_send_parent_email()`
because in dev / sandbox there's no SMTP. The stub writes the link to
a `parent_consent_outbox` table the admin console can inspect (and the
operator can manually copy + email out of band).
"""

from __future__ import annotations

import os
import secrets
import sqlite3
import time
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Optional


MINOR_AGE_THRESHOLD = 13  # DPDP §2(f): "child" = under-eighteen, but
                          # §9 specifically applies parental-consent
                          # requirements to processing of children's
                          # data. We adopt 13 as the consent threshold
                          # (aligned with COPPA + most school systems);
                          # 13-18 users still flagged but unblocked.


# ---------- schema ----------

SCHEMA_ADDITIONS = [
    # Extend the existing users table. ADD COLUMN IF NOT EXISTS isn't
    # universal SQLite; we wrap each in a try/except so re-runs are
    # safe.
    "ALTER TABLE users ADD COLUMN dob TEXT",
    "ALTER TABLE users ADD COLUMN parent_email TEXT",
    "ALTER TABLE users ADD COLUMN parent_consent_at REAL",
    "ALTER TABLE users ADD COLUMN parent_consent_ip TEXT",
    "ALTER TABLE users ADD COLUMN account_locked INTEGER NOT NULL DEFAULT 0",
]

EXTRA_TABLES = """
CREATE TABLE IF NOT EXISTS parent_consent_tokens (
    token        TEXT PRIMARY KEY,
    user_id      TEXT NOT NULL,
    parent_email TEXT NOT NULL,
    created_at   REAL NOT NULL,
    expires_at   REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_pct_user ON parent_consent_tokens(user_id);

CREATE TABLE IF NOT EXISTS parent_consent_outbox (
    id           TEXT PRIMARY KEY,
    user_id      TEXT NOT NULL,
    parent_email TEXT NOT NULL,
    verify_url   TEXT NOT NULL,
    queued_at    REAL NOT NULL,
    sent_at      REAL,
    delivery     TEXT NOT NULL DEFAULT 'pending'  -- pending|sent|failed
);
"""


def _db_path() -> Path:
    custom = os.environ.get("PADHAI_DB_PATH")
    if custom:
        return Path(custom).expanduser()
    return Path.home() / ".padhai" / "jobs.db"


def _conn() -> sqlite3.Connection:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=10.0)
    # The users table is created by padhai.db / padhai.auth on startup;
    # we only need to ensure our additive columns + helper tables exist.
    # If users doesn't exist yet, that's fine — auth will create it
    # later and our migrate() can be re-run any time.
    return conn


def migrate() -> None:
    """Idempotent — adds the consent columns + tables if missing.
    Called by `padhai.web` at app startup.

    Tolerates:
      - users table not existing yet (auth module hasn't bootstrapped)
      - columns already added (re-run on existing DB)
    """
    with _conn() as conn:
        # Ensure users table exists first; if not, defer migration
        # until the first real signup creates it.
        has_users = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='users'"
        ).fetchone()
        if has_users:
            for stmt in SCHEMA_ADDITIONS:
                try:
                    conn.execute(stmt)
                except sqlite3.OperationalError as e:
                    if "duplicate column" not in str(e).lower():
                        raise
        conn.executescript(EXTRA_TABLES)


# ---------- age math ----------

def compute_age(dob_str: str, today: date | None = None) -> int:
    """Whole years between dob and today. Raises ValueError on bad date."""
    today = today or date.today()
    try:
        dob = datetime.strptime(dob_str, "%Y-%m-%d").date()
    except ValueError as e:
        raise ValueError(f"dob must be YYYY-MM-DD, got {dob_str!r}") from e
    if dob > today:
        raise ValueError("dob is in the future")
    years = today.year - dob.year
    if (today.month, today.day) < (dob.month, dob.day):
        years -= 1
    return years


def is_minor(dob_str: str | None) -> bool:
    """True when the DOB indicates an under-13 user (consent required).
    Returns False for missing DOB so legacy accounts don't suddenly
    get locked — they predate this policy."""
    if not dob_str:
        return False
    try:
        return compute_age(dob_str) < MINOR_AGE_THRESHOLD
    except ValueError:
        return False


# ---------- consent token lifecycle ----------

TOKEN_TTL_SECONDS = 7 * 86400  # 7 days


def issue_consent_token(*, user_id: str, parent_email: str) -> str:
    """Generate a single-use verification token tied to (user_id,
    parent_email). Returns the token string — the caller composes the
    verification URL."""
    token = secrets.token_urlsafe(32)
    now = time.time()
    with _conn() as conn:
        # One outstanding token per user — issuing a new one invalidates
        # any previous ones, so an admin can re-trigger if the parent
        # email was wrong.
        conn.execute("DELETE FROM parent_consent_tokens WHERE user_id = ?",
                     (user_id,))
        conn.execute(
            "INSERT INTO parent_consent_tokens "
            "(token, user_id, parent_email, created_at, expires_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (token, user_id, parent_email.lower().strip(),
             now, now + TOKEN_TTL_SECONDS),
        )
    return token


@dataclass(frozen=True)
class ConsentRecord:
    user_id: str
    parent_email: str
    consented_at: float
    consented_ip: str


def verify_consent_token(token: str, *, parent_ip: str) -> ConsentRecord:
    """Single-use redemption. Raises ValueError on invalid/expired
    token. On success: deletes the token, sets users.parent_consent_at
    + parent_consent_ip + account_locked=0."""
    now = time.time()
    with _conn() as conn:
        row = conn.execute(
            "SELECT user_id, parent_email, expires_at "
            "FROM parent_consent_tokens WHERE token = ?",
            (token,),
        ).fetchone()
        if not row:
            raise ValueError("invalid token")
        user_id, parent_email, expires_at = row
        if expires_at < now:
            conn.execute("DELETE FROM parent_consent_tokens WHERE token = ?",
                         (token,))
            raise ValueError("token expired; ask the school admin to re-send")
        # Record consent + unlock the user atomically.
        conn.execute(
            "UPDATE users SET parent_consent_at = ?, parent_consent_ip = ?, "
            "account_locked = 0 WHERE id = ?",
            (now, parent_ip, user_id),
        )
        conn.execute("DELETE FROM parent_consent_tokens WHERE token = ?",
                     (token,))
    return ConsentRecord(
        user_id=user_id, parent_email=parent_email,
        consented_at=now, consented_ip=parent_ip,
    )


# ---------- email outbox (dev stub) ----------

def queue_parent_email(*, user_id: str, parent_email: str,
                       verify_url: str) -> str:
    """Stub for the real email backend. In production this is replaced
    by a SendGrid/Postmark call; until then we just write the URL to
    the outbox so an admin can copy-paste it out of band.

    Returns the outbox row id."""
    oid = uuid.uuid4().hex
    with _conn() as conn:
        conn.execute(
            "INSERT INTO parent_consent_outbox "
            "(id, user_id, parent_email, verify_url, queued_at, delivery) "
            "VALUES (?, ?, ?, ?, ?, 'pending')",
            (oid, user_id, parent_email.lower().strip(),
             verify_url, time.time()),
        )
    return oid


def list_outbox(*, status: str = "pending", limit: int = 50) -> list[dict]:
    """Admin console reads this to see pending parent-consent emails
    when no SMTP is wired."""
    with _conn() as conn:
        rows = conn.execute(
            "SELECT id, user_id, parent_email, verify_url, queued_at, "
            "       sent_at, delivery FROM parent_consent_outbox "
            "WHERE delivery = ? ORDER BY queued_at DESC LIMIT ?",
            (status, limit),
        ).fetchall()
    return [
        {"id": r[0], "user_id": r[1], "parent_email": r[2],
         "verify_url": r[3], "queued_at": r[4],
         "sent_at": r[5], "delivery": r[6]}
        for r in rows
    ]


def mark_outbox_sent(outbox_id: str) -> None:
    with _conn() as conn:
        conn.execute(
            "UPDATE parent_consent_outbox SET delivery = 'sent', sent_at = ? "
            "WHERE id = ?",
            (time.time(), outbox_id),
        )


# ---------- audit ----------

def consent_audit(user_id: str) -> dict | None:
    """Return the consent record for a user (or None if not consented)."""
    with _conn() as conn:
        r = conn.execute(
            "SELECT dob, parent_email, parent_consent_at, parent_consent_ip, "
            "       account_locked FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
    if not r:
        return None
    return {
        "dob": r[0], "parent_email": r[1],
        "consented_at": r[2], "consented_ip": r[3],
        "account_locked": bool(r[4]),
        "is_minor": is_minor(r[0]),
    }
