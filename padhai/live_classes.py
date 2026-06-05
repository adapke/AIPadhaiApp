"""M1 — Live cohort classes.

Scheduled video classes with chat + interactive polls + synchronized
whiteboard. 50-500 students per session. Recorded for async catchup.

We build on top of LiveKit / Daily.co (WebRTC primitives) — don't roll
our own SFU. This module is the **scheduling + access-token layer**;
the actual media plane runs in the provider's cloud.

Provider abstraction:
  - LiveKit when `LIVEKIT_API_KEY` + `LIVEKIT_API_SECRET` are set
  - Daily.co when `DAILY_API_KEY` is set
  - Local dev stub otherwise (returns fake room IDs + tokens so the
    frontend dev path exercises the full schema)

Two tables:
  live_classes               — scheduled class metadata
  live_class_attendees       — who joined + duration tracking

Cost: LiveKit hosted is $50/mo + $0.40/hour-participant. Pass-through
to M3+ tiers; absorb on M4 enterprise.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS live_classes (
    id              TEXT PRIMARY KEY,
    org_id          TEXT,                    -- NULL for cross-org / marketplace classes
    class_id        TEXT,                    -- backref to org_classes when in-school
    title           TEXT NOT NULL,
    subject         TEXT,
    teacher_user_id TEXT NOT NULL,
    scheduled_at    REAL NOT NULL,
    duration_min    INTEGER NOT NULL DEFAULT 60,
    max_attendees   INTEGER NOT NULL DEFAULT 200,
    provider        TEXT NOT NULL,           -- 'livekit' | 'daily' | 'stub'
    room_id         TEXT NOT NULL,
    recording_url   TEXT,
    status          TEXT NOT NULL DEFAULT 'scheduled',  -- scheduled | live | ended | cancelled
    started_at      REAL,
    ended_at        REAL,
    created_at      REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_lc_org_time
    ON live_classes(org_id, scheduled_at DESC);
CREATE INDEX IF NOT EXISTS idx_lc_class_time
    ON live_classes(class_id, scheduled_at DESC);
CREATE INDEX IF NOT EXISTS idx_lc_status
    ON live_classes(status, scheduled_at);

CREATE TABLE IF NOT EXISTS live_class_attendees (
    id              TEXT PRIMARY KEY,
    live_class_id   TEXT NOT NULL,
    user_id         TEXT NOT NULL,
    joined_at       REAL NOT NULL,
    left_at         REAL,
    seconds_present INTEGER,
    UNIQUE (live_class_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_lca_class
    ON live_class_attendees(live_class_id);
"""


VALID_STATUSES = frozenset({"scheduled", "live", "ended", "cancelled"})


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


# ---------- provider selection ----------

def active_provider() -> str:
    """Returns 'livekit' | 'daily' | 'stub'. Order matters: LiveKit
    preferred (cheaper at scale + better Indian latency), Daily as
    fallback, stub when neither is configured (dev path)."""
    if os.environ.get("LIVEKIT_API_KEY") and os.environ.get("LIVEKIT_API_SECRET"):
        return "livekit"
    if os.environ.get("DAILY_API_KEY"):
        return "daily"
    return "stub"


def describe() -> dict:
    return {
        "provider": active_provider(),
        "livekit_configured": bool(
            os.environ.get("LIVEKIT_API_KEY")
            and os.environ.get("LIVEKIT_API_SECRET"),
        ),
        "daily_configured": bool(os.environ.get("DAILY_API_KEY")),
    }


# ---------- room + token issuance ----------

def _create_room(*, room_id: str, provider: str) -> str:
    """Returns the provider-side room identifier. For LiveKit we use
    our own UUID as the room name (LiveKit creates lazily on first
    join). For Daily.co we'd POST /v1/rooms; for stub we just echo
    the local UUID."""
    if provider == "daily":
        # Daily.co room creation. Lazy import; the actual call lands
        # in the v2.3.x ops sprint when DAILY_API_KEY is provisioned.
        try:
            import requests
            r = requests.post(
                "https://api.daily.co/v1/rooms",
                headers={
                    "Authorization": f"Bearer {os.environ['DAILY_API_KEY']}",
                    "Content-Type": "application/json",
                },
                json={
                    "name": room_id,
                    "properties": {
                        "enable_chat": True,
                        "enable_recording": "cloud",
                        "max_participants": 200,
                    },
                },
                timeout=10,
            )
            if r.status_code in (200, 201):
                return r.json().get("name") or room_id
        except Exception as e:
            print(f"[live_classes] Daily.co room create failed: {e}")
    # LiveKit / stub: name is the room
    return room_id


def issue_access_token(
    *,
    live_class_id: str,
    user_id: str,
    role: str = "student",
) -> dict:
    """Returns `{token, room_id, provider, expires_at}` so the
    frontend SDK can connect. Token is per-user, per-class, signed.

    For LiveKit we'd use their SDK to mint a JWT with the room name
    + identity claims. For stub we mint an HMAC token over the same
    fields so the dev path exercises the verify path.
    """
    lc = get(live_class_id)
    if not lc:
        raise ValueError(f"live class {live_class_id!r} not found")
    provider = lc.provider
    ttl_seconds = 4 * 3600   # 4-hour validity covers any class
    expires_at = int(time.time()) + ttl_seconds

    if provider == "livekit":
        try:
            from livekit.api import AccessToken, VideoGrants
            at = AccessToken(
                os.environ["LIVEKIT_API_KEY"],
                os.environ["LIVEKIT_API_SECRET"],
            )
            at = at.with_identity(user_id).with_name(user_id).with_grants(
                VideoGrants(
                    room_join=True, room=lc.room_id,
                    can_publish=(role in ("teacher", "co_host")),
                    can_subscribe=True,
                    can_publish_data=True,
                ),
            ).with_ttl(ttl_seconds)
            return {
                "token": at.to_jwt(),
                "room_id": lc.room_id,
                "provider": "livekit",
                "expires_at": expires_at,
                "ws_url": os.environ.get("LIVEKIT_WS_URL", ""),
            }
        except ImportError:
            # Fallthrough to stub if SDK missing
            pass

    if provider == "daily":
        try:
            import requests
            r = requests.post(
                "https://api.daily.co/v1/meeting-tokens",
                headers={
                    "Authorization": f"Bearer {os.environ['DAILY_API_KEY']}",
                },
                json={
                    "properties": {
                        "room_name": lc.room_id,
                        "user_id": user_id,
                        "is_owner": role in ("teacher", "co_host"),
                        "exp": expires_at,
                    },
                },
                timeout=10,
            )
            if r.status_code in (200, 201):
                return {
                    "token": r.json()["token"],
                    "room_id": lc.room_id,
                    "provider": "daily",
                    "expires_at": expires_at,
                    "room_url": f"https://aipathshala.daily.co/{lc.room_id}",
                }
        except Exception as e:
            print(f"[live_classes] Daily.co token failed: {e}")

    # Stub fallback — HMAC over the claims
    secret = os.environ.get("PADHAI_LIVE_STUB_SECRET", "dev-secret")
    claim = f"{lc.room_id}:{user_id}:{role}:{expires_at}"
    sig = hmac.new(
        secret.encode("utf-8"), claim.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return {
        "token": f"stub.{claim}.{sig}",
        "room_id": lc.room_id,
        "provider": "stub",
        "expires_at": expires_at,
        "stub_note": (
            "Live video provider not configured. This token won't "
            "connect to a real room — wire LIVEKIT_API_KEY or "
            "DAILY_API_KEY to enable."
        ),
    }


# ---------- CRUD ----------

@dataclass(frozen=True)
class LiveClass:
    id: str
    org_id: str | None
    class_id: str | None
    title: str
    subject: str | None
    teacher_user_id: str
    scheduled_at: float
    duration_min: int
    max_attendees: int
    provider: str
    room_id: str
    recording_url: str | None
    status: str
    started_at: float | None
    ended_at: float | None
    created_at: float


_SEL = (
    "id, org_id, class_id, title, subject, teacher_user_id, "
    "scheduled_at, duration_min, max_attendees, provider, room_id, "
    "recording_url, status, started_at, ended_at, created_at"
)


def _row_to_lc(r) -> LiveClass:
    return LiveClass(
        id=r[0], org_id=r[1], class_id=r[2], title=r[3],
        subject=r[4], teacher_user_id=r[5],
        scheduled_at=r[6], duration_min=r[7], max_attendees=r[8],
        provider=r[9], room_id=r[10], recording_url=r[11],
        status=r[12], started_at=r[13], ended_at=r[14],
        created_at=r[15],
    )


def schedule(
    *,
    teacher_user_id: str,
    title: str,
    scheduled_at: float,
    duration_min: int = 60,
    org_id: str | None = None,
    class_id: str | None = None,
    subject: str | None = None,
    max_attendees: int = 200,
) -> LiveClass:
    if duration_min < 5 or duration_min > 480:
        raise ValueError("duration_min must be in [5, 480]")
    if max_attendees < 2 or max_attendees > 5000:
        raise ValueError("max_attendees must be in [2, 5000]")
    if not title.strip():
        raise ValueError("title required")
    lc_id = uuid.uuid4().hex
    room_id = "padhai-" + uuid.uuid4().hex[:24]
    provider = active_provider()
    _create_room(room_id=room_id, provider=provider)
    with _conn() as conn:
        conn.execute(
            "INSERT INTO live_classes "
            "(id, org_id, class_id, title, subject, teacher_user_id, "
            " scheduled_at, duration_min, max_attendees, provider, "
            " room_id, status, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?, 'scheduled', ?)",
            (lc_id, org_id, class_id, title.strip(), subject,
             teacher_user_id, scheduled_at, duration_min,
             max_attendees, provider, room_id, time.time()),
        )
    return get(lc_id)  # type: ignore[return-value]


def get(lc_id: str) -> LiveClass | None:
    with _conn() as conn:
        r = conn.execute(
            f"SELECT {_SEL} FROM live_classes WHERE id = ?", (lc_id,),
        ).fetchone()
    return _row_to_lc(r) if r else None


def list_upcoming(
    *,
    org_id: str | None = None,
    class_id: str | None = None,
    window_hours: float = 168.0,
) -> list[LiveClass]:
    sql = (
        f"SELECT {_SEL} FROM live_classes "
        "WHERE status IN ('scheduled', 'live') "
        "AND scheduled_at <= ? "
    )
    params: list = [time.time() + window_hours * 3600]
    if org_id is not None:
        sql += " AND org_id = ?"; params.append(org_id)
    if class_id is not None:
        sql += " AND class_id = ?"; params.append(class_id)
    sql += " ORDER BY scheduled_at"
    with _conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_row_to_lc(r) for r in rows]


def set_status(*, live_class_id: str, status: str) -> bool:
    if status not in VALID_STATUSES:
        raise ValueError(f"status must be in {sorted(VALID_STATUSES)}")
    now = time.time()
    set_clause = "status = ?"
    params = [status]
    if status == "live":
        set_clause += ", started_at = COALESCE(started_at, ?)"
        params.append(now)
    elif status == "ended":
        set_clause += ", ended_at = COALESCE(ended_at, ?)"
        params.append(now)
    params.append(live_class_id)
    with _conn() as conn:
        cur = conn.execute(
            f"UPDATE live_classes SET {set_clause} WHERE id = ?",
            params,
        )
        return cur.rowcount > 0


def set_recording(*, live_class_id: str, url: str) -> bool:
    with _conn() as conn:
        cur = conn.execute(
            "UPDATE live_classes SET recording_url = ? WHERE id = ?",
            (url, live_class_id),
        )
        return cur.rowcount > 0


def cancel(*, live_class_id: str) -> bool:
    return set_status(live_class_id=live_class_id, status="cancelled")


# ---------- attendance ----------

def record_join(*, live_class_id: str, user_id: str) -> str:
    """Idempotent: first call inserts, repeat calls bump joined_at
    so we see the latest reconnect."""
    aid = uuid.uuid4().hex
    now = time.time()
    with _conn() as conn:
        existing = conn.execute(
            "SELECT id FROM live_class_attendees "
            "WHERE live_class_id = ? AND user_id = ?",
            (live_class_id, user_id),
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE live_class_attendees SET joined_at = ? "
                "WHERE id = ?",
                (now, existing[0]),
            )
            return existing[0]
        conn.execute(
            "INSERT INTO live_class_attendees "
            "(id, live_class_id, user_id, joined_at) "
            "VALUES (?, ?, ?, ?)",
            (aid, live_class_id, user_id, now),
        )
    return aid


def record_leave(*, live_class_id: str, user_id: str) -> bool:
    now = time.time()
    with _conn() as conn:
        r = conn.execute(
            "SELECT id, joined_at FROM live_class_attendees "
            "WHERE live_class_id = ? AND user_id = ?",
            (live_class_id, user_id),
        ).fetchone()
        if not r:
            return False
        seconds = int(now - r[1]) if r[1] else 0
        conn.execute(
            "UPDATE live_class_attendees SET "
            " left_at = ?, seconds_present = COALESCE(seconds_present, 0) + ? "
            "WHERE id = ?",
            (now, max(0, seconds), r[0]),
        )
    return True


def list_attendees(live_class_id: str) -> list[dict]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT user_id, joined_at, left_at, seconds_present "
            "FROM live_class_attendees WHERE live_class_id = ?",
            (live_class_id,),
        ).fetchall()
    return [
        {"user_id": r[0], "joined_at": r[1], "left_at": r[2],
         "seconds_present": r[3]}
        for r in rows
    ]
