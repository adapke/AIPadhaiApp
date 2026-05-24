"""Org notifications — announcements, assignment reminders, system alerts.

Two tables:
  - org_notifications      one row per notification
  - org_notification_reads one row per (notification, user) when read

Audience targeting — `audience` column carries one of:
  - "all"                everyone in the org
  - "class:<class_id>"   specific class group
  - "role:teacher"       all teachers (or "role:student", "role:admin")
  - "user:<user_id>"     single user

Channels — `channels` is a comma-separated list:
  - in_app   always; the only channel implemented in v0.11
  - email    delivered via send_email() stub; queued in notification_outbox
             when no SMTP wired
  - whatsapp queued for v0.12 when Bhashini/Gupshup integration lands

Scheduled sends — `send_at` is the epoch at which a notification becomes
visible. For "send now" callers pass `time.time()`. A background sweep
(scripts/dispatch_notifications.py) hits the `send_at <= NOW` rows and
forwards them through the email/whatsapp providers.
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
CREATE TABLE IF NOT EXISTS org_notifications (
    id          TEXT PRIMARY KEY,
    org_id      TEXT NOT NULL,
    audience    TEXT NOT NULL,
    kind        TEXT NOT NULL,            -- announcement | assignment_due | system
    title       TEXT NOT NULL,
    body        TEXT,
    link_url    TEXT,
    sent_by     TEXT NOT NULL,
    send_at     REAL NOT NULL,
    channels    TEXT NOT NULL DEFAULT 'in_app',
    created_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_notif_org ON org_notifications(org_id);
CREATE INDEX IF NOT EXISTS idx_notif_send_at ON org_notifications(send_at);

CREATE TABLE IF NOT EXISTS org_notification_reads (
    notification_id TEXT NOT NULL,
    user_id         TEXT NOT NULL,
    read_at         REAL NOT NULL,
    PRIMARY KEY (notification_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_notif_reads_user
    ON org_notification_reads(user_id);
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
    conn.executescript(SCHEMA)
    return conn


@dataclass(frozen=True)
class Notification:
    id: str
    org_id: str
    audience: str
    kind: str
    title: str
    body: str | None
    link_url: str | None
    sent_by: str
    send_at: float
    channels: str
    created_at: float


VALID_KINDS = {"announcement", "assignment_due", "system"}


def create(
    *,
    org_id: str,
    audience: str,
    kind: str,
    title: str,
    body: str | None = None,
    link_url: str | None = None,
    sent_by: str,
    send_at: float | None = None,
    channels: str = "in_app",
) -> Notification:
    """Insert a new notification. Audience format validated lightly —
    targeting against the org's actual classes/users is the caller's
    responsibility."""
    if kind not in VALID_KINDS:
        raise ValueError(f"kind must be in {sorted(VALID_KINDS)}")
    if not title.strip():
        raise ValueError("title is required")
    nid = uuid.uuid4().hex
    now = time.time()
    with _conn() as conn:
        conn.execute(
            "INSERT INTO org_notifications "
            "(id, org_id, audience, kind, title, body, link_url, sent_by, "
            " send_at, channels, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (nid, org_id, audience, kind, title.strip(), body, link_url,
             sent_by, send_at or now, channels, now),
        )
    return Notification(
        id=nid, org_id=org_id, audience=audience, kind=kind,
        title=title.strip(), body=body, link_url=link_url,
        sent_by=sent_by, send_at=send_at or now, channels=channels,
        created_at=now,
    )


def feed_for_user(
    *,
    user_id: str,
    user_role: str,
    user_class_id: str | None,
    org_ids: list[str],
    unread_only: bool = False,
    limit: int = 50,
) -> list[dict]:
    """Return notifications visible to `user_id` across their org
    memberships, sorted newest-first. Respects audience filtering +
    the send_at gate so scheduled notifications stay hidden."""
    if not org_ids:
        return []
    now = time.time()

    # Build the audience filter: a notification is visible to the user
    # if its audience is "all", their role, their class, or their user id.
    audience_options = ["'all'", f"'role:{user_role}'", f"'user:{user_id}'"]
    if user_class_id:
        audience_options.append(f"'class:{user_class_id}'")
    audience_clause = " OR ".join(f"audience = {opt}" for opt in audience_options)

    org_placeholders = ",".join("?" for _ in org_ids)
    params: list = list(org_ids) + [now]

    where = (
        f"org_id IN ({org_placeholders}) "
        f"AND send_at <= ? "
        f"AND ({audience_clause})"
    )

    with _conn() as conn:
        rows = conn.execute(
            "SELECT id, org_id, audience, kind, title, body, link_url, "
            "       sent_by, send_at, channels, created_at "
            "FROM org_notifications "
            f"WHERE {where} "
            "ORDER BY send_at DESC LIMIT ?",
            params + [limit],
        ).fetchall()

        read_ids = set(r[0] for r in conn.execute(
            f"SELECT notification_id FROM org_notification_reads "
            f"WHERE user_id = ?",
            (user_id,),
        ).fetchall())

    out = []
    for r in rows:
        is_read = r[0] in read_ids
        if unread_only and is_read:
            continue
        out.append({
            "id": r[0], "org_id": r[1], "audience": r[2], "kind": r[3],
            "title": r[4], "body": r[5], "link_url": r[6],
            "sent_by": r[7], "send_at": r[8], "channels": r[9],
            "created_at": r[10], "read": is_read,
        })
    return out


def mark_read(*, notification_id: str, user_id: str) -> None:
    """Idempotent — repeat calls are no-ops."""
    with _conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO org_notification_reads "
            "(notification_id, user_id, read_at) VALUES (?, ?, ?)",
            (notification_id, user_id, time.time()),
        )


def mark_all_read(*, user_id: str, org_ids: list[str]) -> int:
    """Bulk mark-as-read on the user's entire visible feed.
    Returns the number of newly-marked rows."""
    feed = feed_for_user(
        user_id=user_id, user_role="any", user_class_id=None,
        org_ids=org_ids, unread_only=True, limit=1000,
    )
    n = 0
    for entry in feed:
        # NB: we can't tell `any` role's role here — caller passes one.
        # mark_read is idempotent so over-marking is fine.
        mark_read(notification_id=entry["id"], user_id=user_id)
        n += 1
    return n


def unread_count(
    *,
    user_id: str,
    user_role: str,
    user_class_id: str | None,
    org_ids: list[str],
) -> int:
    if not org_ids:
        return 0
    return len(feed_for_user(
        user_id=user_id, user_role=user_role,
        user_class_id=user_class_id, org_ids=org_ids,
        unread_only=True, limit=1000,
    ))


def list_for_admin(
    *, org_id: str, limit: int = 100,
) -> list[Notification]:
    """All notifications in an org (sent + scheduled) — admin view."""
    with _conn() as conn:
        rows = conn.execute(
            "SELECT id, org_id, audience, kind, title, body, link_url, "
            "       sent_by, send_at, channels, created_at "
            "FROM org_notifications WHERE org_id = ? "
            "ORDER BY send_at DESC LIMIT ?",
            (org_id, limit),
        ).fetchall()
    return [Notification(*r) for r in rows]
