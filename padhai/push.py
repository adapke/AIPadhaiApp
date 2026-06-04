"""I3 — Push notifications (FCM + APNs + Web Push).

When the v1.4 mobile apps (I1/I2) land, they'll need a server-side
push fan-out so we can deliver:
- Assignment-due reminders (E1 + E2)
- Exam alerts (E4)
- Attendance flags for parents (E3 + E8)
- Notification announcements (E2)
- Daily streak nudges (I4)

This module is the data + dispatch plumbing. The actual platform
sends (FCM HTTP v1 API for Android, APNs HTTP/2 for iOS, Web Push for
browsers) are skeletoned — they activate when `FCM_SERVER_KEY` /
`APNS_KEY_ID` / `VAPID_PRIVATE_KEY` are configured. Otherwise sends
are logged-not-sent so the schema + queue + audit trail still
exercise end-to-end during development.

Schema:

  push_tokens   one row per device per user; UNIQUE (user_id, token)
                supports user logging into multiple devices

  push_log      one row per send attempt; capacity-planned at
                ~5 notifications/user/day × 100k users = 500k/day
                pruned to 90 days hot retention

Categories — users opt in/out per category, default ON for everyone
except 'marketing':
  - assignment_due
  - exam_alert
  - attendance
  - announcement
  - streak
  - marketing  (default OFF)
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

SCHEMA = """
CREATE TABLE IF NOT EXISTS push_tokens (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL,
    platform    TEXT NOT NULL,        -- 'fcm' | 'apns' | 'web'
    token       TEXT NOT NULL,
    device_id   TEXT,                  -- client-supplied device fingerprint
    app_version TEXT,
    active      INTEGER NOT NULL DEFAULT 1,
    created_at  REAL NOT NULL,
    last_used   REAL,
    UNIQUE (user_id, token)
);
CREATE INDEX IF NOT EXISTS idx_push_tokens_user ON push_tokens(user_id, active);

CREATE TABLE IF NOT EXISTS push_prefs (
    user_id     TEXT NOT NULL,
    category    TEXT NOT NULL,
    enabled     INTEGER NOT NULL DEFAULT 1,
    updated_at  REAL NOT NULL,
    PRIMARY KEY (user_id, category)
);

CREATE TABLE IF NOT EXISTS push_log (
    id            TEXT PRIMARY KEY,
    user_id       TEXT NOT NULL,
    notification_id TEXT,                 -- backref to org_notifications.id if applicable
    category      TEXT NOT NULL,
    platform      TEXT NOT NULL,
    title         TEXT NOT NULL,
    body          TEXT,
    payload_json  TEXT,
    sent_at       REAL NOT NULL,
    delivered_at  REAL,                   -- set by webhook from FCM/APNs
    opened_at     REAL,                   -- set by client beacon
    failed_reason TEXT
);
CREATE INDEX IF NOT EXISTS idx_push_log_user_time   ON push_log(user_id, sent_at DESC);
CREATE INDEX IF NOT EXISTS idx_push_log_notif       ON push_log(notification_id);
CREATE INDEX IF NOT EXISTS idx_push_log_unopened    ON push_log(user_id) WHERE opened_at IS NULL;
"""


# Categories users can subscribe to. Default-ON list applies on first
# token registration; user can opt out per category later.
ALL_CATEGORIES = (
    "assignment_due",
    "exam_alert",
    "attendance",
    "announcement",
    "streak",
    "marketing",
)
DEFAULT_ON = tuple(c for c in ALL_CATEGORIES if c != "marketing")

VALID_PLATFORMS = frozenset({"fcm", "apns", "web"})


# ---------- DB plumbing ----------

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
class PushToken:
    id: str
    user_id: str
    platform: str
    token: str
    device_id: str | None
    app_version: str | None
    active: bool
    created_at: float
    last_used: float | None


@dataclass(frozen=True)
class PushPref:
    user_id: str
    category: str
    enabled: bool
    updated_at: float


@dataclass(frozen=True)
class PushLogEntry:
    id: str
    user_id: str
    notification_id: str | None
    category: str
    platform: str
    title: str
    body: str | None
    payload_json: str | None
    sent_at: float
    delivered_at: float | None
    opened_at: float | None
    failed_reason: str | None


# ---------- token registration ----------

def register_token(
    *,
    user_id: str,
    platform: str,
    token: str,
    device_id: str | None = None,
    app_version: str | None = None,
) -> PushToken:
    """Register or refresh a push token. Idempotent on (user_id, token);
    re-registering with a fresh `last_used` is the standard heartbeat
    pattern apps use after they reopen.

    Side effect: on FIRST token for a user, seeds default opt-in prefs.
    """
    if platform not in VALID_PLATFORMS:
        raise ValueError(f"platform must be in {sorted(VALID_PLATFORMS)}")
    if not token.strip():
        raise ValueError("token is required")
    now = time.time()
    with _conn() as conn:
        existed = conn.execute(
            "SELECT id FROM push_tokens WHERE user_id = ? AND token = ?",
            (user_id, token),
        ).fetchone()
        if existed:
            conn.execute(
                "UPDATE push_tokens SET active = 1, last_used = ?, "
                "device_id = COALESCE(?, device_id), "
                "app_version = COALESCE(?, app_version) "
                "WHERE id = ?",
                (now, device_id, app_version, existed[0]),
            )
            tid = existed[0]
        else:
            tid = uuid.uuid4().hex
            conn.execute(
                "INSERT INTO push_tokens "
                "(id, user_id, platform, token, device_id, app_version, "
                " active, created_at, last_used) "
                "VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)",
                (tid, user_id, platform, token, device_id, app_version,
                 now, now),
            )
            # Seed prefs once per user (idempotent on PK)
            for cat in ALL_CATEGORIES:
                conn.execute(
                    "INSERT OR IGNORE INTO push_prefs "
                    "(user_id, category, enabled, updated_at) "
                    "VALUES (?, ?, ?, ?)",
                    (user_id, cat, 1 if cat in DEFAULT_ON else 0, now),
                )
        row = conn.execute(
            "SELECT id, user_id, platform, token, device_id, app_version, "
            "       active, created_at, last_used "
            "FROM push_tokens WHERE id = ?",
            (tid,),
        ).fetchone()
    return PushToken(
        id=row[0], user_id=row[1], platform=row[2], token=row[3],
        device_id=row[4], app_version=row[5],
        active=bool(row[6]), created_at=row[7], last_used=row[8],
    )


def deactivate_token(*, user_id: str, token: str) -> bool:
    """Soft-delete on logout. Returns True if a row was updated."""
    with _conn() as conn:
        cur = conn.execute(
            "UPDATE push_tokens SET active = 0 "
            "WHERE user_id = ? AND token = ?",
            (user_id, token),
        )
        return cur.rowcount > 0


def list_active_tokens(user_id: str) -> list[PushToken]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT id, user_id, platform, token, device_id, app_version, "
            "       active, created_at, last_used "
            "FROM push_tokens WHERE user_id = ? AND active = 1",
            (user_id,),
        ).fetchall()
    return [
        PushToken(
            id=r[0], user_id=r[1], platform=r[2], token=r[3],
            device_id=r[4], app_version=r[5],
            active=bool(r[6]), created_at=r[7], last_used=r[8],
        )
        for r in rows
    ]


# ---------- prefs ----------

def set_pref(*, user_id: str, category: str, enabled: bool) -> None:
    if category not in ALL_CATEGORIES:
        raise ValueError(f"category must be in {sorted(ALL_CATEGORIES)}")
    with _conn() as conn:
        conn.execute(
            "INSERT INTO push_prefs (user_id, category, enabled, updated_at) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(user_id, category) DO UPDATE SET "
            " enabled = excluded.enabled, updated_at = excluded.updated_at",
            (user_id, category, 1 if enabled else 0, time.time()),
        )


def get_prefs(user_id: str) -> dict[str, bool]:
    """Returns every category → enabled. Categories not yet in the DB
    default to the DEFAULT_ON set (so a never-registered user still
    answers truthfully)."""
    with _conn() as conn:
        rows = conn.execute(
            "SELECT category, enabled FROM push_prefs WHERE user_id = ?",
            (user_id,),
        ).fetchall()
    stored = {r[0]: bool(r[1]) for r in rows}
    return {c: stored.get(c, c in DEFAULT_ON) for c in ALL_CATEGORIES}


def is_opted_in(*, user_id: str, category: str) -> bool:
    return get_prefs(user_id).get(category, category in DEFAULT_ON)


# ---------- send fan-out ----------

@dataclass(frozen=True)
class SendResult:
    user_id: str
    delivered: int                # how many devices accepted
    failed: int                   # how many returned platform-level errors
    skipped_opt_out: bool         # user has the category off
    log_ids: list[str]


def send_one(
    *,
    user_id: str,
    category: str,
    title: str,
    body: str | None = None,
    notification_id: str | None = None,
    payload: dict | None = None,
) -> SendResult:
    """Fan out to every active token for the user. Respects opt-out:
    if the user has `category` disabled, returns immediately with
    `skipped_opt_out=True` and no log entries written.

    Real platform send is gated on env config — when keys aren't set
    we still write the push_log row with platform='fcm'/'apns'/'web'
    and `failed_reason='no_provider'` so admin telemetry shows what
    would have been delivered. This makes the development experience
    same-shape as production."""
    if not is_opted_in(user_id=user_id, category=category):
        return SendResult(
            user_id=user_id, delivered=0, failed=0,
            skipped_opt_out=True, log_ids=[],
        )
    tokens = list_active_tokens(user_id)
    if not tokens:
        return SendResult(
            user_id=user_id, delivered=0, failed=0,
            skipped_opt_out=False, log_ids=[],
        )
    delivered = 0
    failed = 0
    log_ids: list[str] = []
    payload_json = json.dumps(payload) if payload is not None else None
    now = time.time()

    # v2.0.4 — collect per-token results in memory, then write them
    # all in a single transaction at the end. Previous version opened
    # one connection per token (N round-trips), which is N× the
    # filesystem fsync cost on SQLite + N× the round-trip count on
    # the future Postgres path.
    rows_to_insert: list[tuple] = []
    for tok in tokens:
        log_id = uuid.uuid4().hex
        failed_reason: str | None = None
        try:
            _platform_send(tok, title=title, body=body, payload=payload)
            delivered += 1
        except _NoProvider:
            failed_reason = "no_provider"
            failed += 1
        except Exception as e:  # noqa: BLE001
            failed_reason = str(e)[:200]
            failed += 1
        rows_to_insert.append((
            log_id, user_id, notification_id, category,
            tok.platform, title, body, payload_json, now,
            failed_reason,
        ))
        log_ids.append(log_id)

    if rows_to_insert:
        try:
            with _conn() as conn:
                conn.executemany(
                    "INSERT INTO push_log "
                    "(id, user_id, notification_id, category, platform, "
                    " title, body, payload_json, sent_at, failed_reason) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    rows_to_insert,
                )
        except Exception as e:  # noqa: BLE001
            # Single failure flushes all the log rows for this send,
            # which is acceptable — the platform-side sends already
            # happened. Caller's audit trail will have the gap.
            print(f"[push] batch log insert failed (non-fatal): {e}")
            log_ids = []   # don't claim we logged what we didn't

    return SendResult(
        user_id=user_id, delivered=delivered, failed=failed,
        skipped_opt_out=False, log_ids=log_ids,
    )


def fan_out_for_notification(notification, recipients: Iterable[str]) -> list[SendResult]:
    """Called right after `notifications.create()` returns. Maps the
    notification's `kind` to a push category, then sends to every
    resolved recipient. Caller is responsible for expanding
    audience ('all' | 'class:<id>' | 'role:teacher' | 'user:<id>')
    into the concrete user_id list — that logic already lives in
    `notifications.feed_for_user` + the org member tables."""
    cat = _KIND_TO_CATEGORY.get(notification.kind, "announcement")
    results = []
    for uid in recipients:
        results.append(
            send_one(
                user_id=uid,
                category=cat,
                title=notification.title,
                body=notification.body,
                notification_id=notification.id,
                payload={"link_url": notification.link_url} if notification.link_url else None,
            )
        )
    return results


_KIND_TO_CATEGORY = {
    "announcement": "announcement",
    "assignment_due": "assignment_due",
    "system": "announcement",
}


# ---------- platform adapters ----------

class _NoProvider(Exception):
    """Raised when a platform send is attempted but the relevant API
    keys aren't configured. Caller logs as failed_reason='no_provider'
    and the send is a no-op."""


def _platform_send(tok: PushToken, *, title: str, body: str | None,
                   payload: dict | None) -> None:
    """Dispatch to the right platform adapter. Each adapter raises
    `_NoProvider` when keys are absent — keeping the call sites
    consistent so the log shows 'no_provider' rather than 'unconfigured'
    or some other ambiguous string."""
    if tok.platform == "fcm":
        return _send_fcm(tok.token, title=title, body=body, payload=payload)
    if tok.platform == "apns":
        return _send_apns(tok.token, title=title, body=body, payload=payload)
    if tok.platform == "web":
        return _send_web(tok.token, title=title, body=body, payload=payload)
    raise ValueError(f"unknown platform {tok.platform!r}")


def _send_fcm(token: str, *, title: str, body: str | None,
              payload: dict | None) -> None:
    """FCM HTTP v1 send. Real call lives in the cutover sprint when
    `FCM_SERVER_KEY` (legacy) or service-account JSON (v1) is set."""
    key = os.environ.get("FCM_SERVER_KEY")
    if not key:
        raise _NoProvider("FCM_SERVER_KEY unset")
    # Real implementation: build the FCM v1 payload + POST to
    # fcm.googleapis.com/v1/projects/{project}/messages:send with
    # OAuth bearer token from service-account JSON. Deferred until
    # I1/I2 mobile apps land and we have actual device tokens to
    # test against.
    raise _NoProvider("FCM adapter scaffolded; full send lands in v1.4")


def _send_apns(token: str, *, title: str, body: str | None,
               payload: dict | None) -> None:
    """APNs HTTP/2 send via Apple's apns-helper. Same deferred
    pattern as FCM."""
    key_id = os.environ.get("APNS_KEY_ID")
    if not key_id:
        raise _NoProvider("APNS_KEY_ID unset")
    raise _NoProvider("APNs adapter scaffolded; full send lands in v1.4")


def _send_web(token: str, *, title: str, body: str | None,
              payload: dict | None) -> None:
    """Web Push via VAPID. Doesn't need a Mobile app — Chrome / Edge
    / Firefox PWA installs can subscribe."""
    vapid = os.environ.get("VAPID_PRIVATE_KEY")
    if not vapid:
        raise _NoProvider("VAPID_PRIVATE_KEY unset")
    raise _NoProvider("Web Push adapter scaffolded; full send lands in v1.5")


# ---------- read paths for admin / dashboard ----------

def recent_log(*, user_id: str | None = None, limit: int = 100) -> list[PushLogEntry]:
    """Most-recent N push attempts. Admin dashboard surface; also used
    by the per-user notification history view."""
    limit = max(1, min(limit, 500))
    with _conn() as conn:
        if user_id is None:
            rows = conn.execute(
                "SELECT id, user_id, notification_id, category, platform, "
                "       title, body, payload_json, sent_at, delivered_at, "
                "       opened_at, failed_reason "
                "FROM push_log ORDER BY sent_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, user_id, notification_id, category, platform, "
                "       title, body, payload_json, sent_at, delivered_at, "
                "       opened_at, failed_reason "
                "FROM push_log WHERE user_id = ? "
                "ORDER BY sent_at DESC LIMIT ?",
                (user_id, limit),
            ).fetchall()
    return [
        PushLogEntry(
            id=r[0], user_id=r[1], notification_id=r[2], category=r[3],
            platform=r[4], title=r[5], body=r[6], payload_json=r[7],
            sent_at=r[8], delivered_at=r[9], opened_at=r[10],
            failed_reason=r[11],
        )
        for r in rows
    ]


def mark_opened(*, log_id: str) -> bool:
    """Beacon from the client when the user taps the push. Used by
    open-rate analytics in the admin dashboard."""
    with _conn() as conn:
        cur = conn.execute(
            "UPDATE push_log SET opened_at = ? "
            "WHERE id = ? AND opened_at IS NULL",
            (time.time(), log_id),
        )
        return cur.rowcount > 0


def stats_for_period(*, hours: float = 24.0) -> dict[str, int]:
    """Aggregate metrics for the last N hours. Used by the SOC 2
    evidence dashboard + the admin overview page."""
    since = time.time() - (hours * 3600)
    with _conn() as conn:
        sent = conn.execute(
            "SELECT COUNT(*) FROM push_log WHERE sent_at >= ?", (since,),
        ).fetchone()[0]
        delivered = conn.execute(
            "SELECT COUNT(*) FROM push_log "
            "WHERE sent_at >= ? AND failed_reason IS NULL",
            (since,),
        ).fetchone()[0]
        opened = conn.execute(
            "SELECT COUNT(*) FROM push_log "
            "WHERE sent_at >= ? AND opened_at IS NOT NULL",
            (since,),
        ).fetchone()[0]
        failed_no_provider = conn.execute(
            "SELECT COUNT(*) FROM push_log "
            "WHERE sent_at >= ? AND failed_reason = 'no_provider'",
            (since,),
        ).fetchone()[0]
    return {
        "sent": sent,
        "delivered": delivered,
        "opened": opened,
        "failed_no_provider": failed_no_provider,
        "hours": int(hours),
    }
