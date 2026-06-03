"""H3 — Audit log.

Every privileged mutation gets a row in `audit_log`. The row captures
WHO did WHAT to WHICH target, when, from where, and a before/after
snapshot of the changed state (when feasible). Org admins can query
their org's slice via the API; super-admins can query all.

Designed to be cheap on the write path (one INSERT, no joins) and
forgiving on failures — if `record()` throws, the upstream action
still commits. We never want an audit-log bug to kill a real
mutation.

Schema is additive (CREATE TABLE IF NOT EXISTS); idempotent migrate()
runs at startup. CSV export streams rows lazily so a 100k-row org
download doesn't OOM.

Hot vs. cold storage:
- Hot: 90 days in the primary DB (this module)
- Cold: anything older streams to S3 Glacier via a daily job
  (deferred to v1.1.x; the schema is shaped for it via `archived_at`)

SOC 2 Type 1 (H6) requires this exists + is queryable + has retention
documented. Keep this module boring.
"""

from __future__ import annotations

import csv
import io
import json
import os
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_log (
    id              TEXT PRIMARY KEY,
    org_id          TEXT,                    -- NULL for platform-level events
    actor_user_id   TEXT,                    -- NULL for anonymous (failed login)
    actor_ip        TEXT,
    actor_ua        TEXT,                    -- user-agent prefix (truncated to 200 chars)
    action          TEXT NOT NULL,           -- 'org.branding.update' | 'auth.login.fail' | ...
    target_type     TEXT,                    -- 'user' | 'org' | 'exam' | 'job' | ...
    target_id       TEXT,
    before_json     TEXT,                    -- JSON of state-before, when known
    after_json      TEXT,                    -- JSON of state-after, when known
    request_id      TEXT,                    -- matches observability trace id
    note            TEXT,                    -- free-form context (the action's "why")
    created_at      REAL NOT NULL,
    archived_at     REAL                     -- set when cold-storage job moves it
);
CREATE INDEX IF NOT EXISTS idx_audit_org_time    ON audit_log(org_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_actor_time  ON audit_log(actor_user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_action_time ON audit_log(action, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_target      ON audit_log(target_type, target_id);
"""


# ---------- DB plumbing (shared shape with rest of the codebase) ----------

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
    """Idempotent — re-runs are no-ops via IF NOT EXISTS."""
    with _conn():
        pass


# ---------- write path ----------

@dataclass(frozen=True)
class AuditRow:
    id: str
    org_id: str | None
    actor_user_id: str | None
    actor_ip: str | None
    actor_ua: str | None
    action: str
    target_type: str | None
    target_id: str | None
    before_json: str | None
    after_json: str | None
    request_id: str | None
    note: str | None
    created_at: float


def record(
    *,
    action: str,
    org_id: str | None = None,
    actor_user_id: str | None = None,
    actor_ip: str | None = None,
    actor_ua: str | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    before: Any = None,
    after: Any = None,
    request_id: str | None = None,
    note: str | None = None,
) -> None:
    """Insert one audit row. Swallows every exception — the caller's
    real work must not be blocked by an audit-log bug.

    `before` / `after` are arbitrary JSON-serialisable values (dicts,
    primitives). Pass redacted snapshots — don't dump full passwords
    or tokens here.

    `actor_ua` gets truncated to 200 chars (typical UA strings are
    under that; bot-like long ones get cut).
    """
    try:
        before_json = json.dumps(before, default=str) if before is not None else None
        after_json = json.dumps(after, default=str) if after is not None else None
        ua = (actor_ua or "")[:200] or None
        with _conn() as conn:
            conn.execute(
                "INSERT INTO audit_log ("
                "id, org_id, actor_user_id, actor_ip, actor_ua, action, "
                "target_type, target_id, before_json, after_json, "
                "request_id, note, created_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    str(uuid.uuid4()),
                    org_id,
                    actor_user_id,
                    actor_ip,
                    ua,
                    action,
                    target_type,
                    target_id,
                    before_json,
                    after_json,
                    request_id,
                    note,
                    time.time(),
                ),
            )
    except Exception as e:  # noqa: BLE001
        # Never let audit failures break the request. Log + continue.
        # If audit logging is broken at scale, the H3 dashboard will
        # show a gap; that's a sev2 to investigate but not a sev0.
        try:
            print(f"[audit] record failed (non-fatal): {e}")
        except Exception:
            pass


# ---------- read path ----------

def _row_to_audit_row(r) -> AuditRow:
    return AuditRow(
        id=r[0],
        org_id=r[1],
        actor_user_id=r[2],
        actor_ip=r[3],
        actor_ua=r[4],
        action=r[5],
        target_type=r[6],
        target_id=r[7],
        before_json=r[8],
        after_json=r[9],
        request_id=r[10],
        note=r[11],
        created_at=r[12],
    )


_SELECT_COLS = (
    "id, org_id, actor_user_id, actor_ip, actor_ua, action, "
    "target_type, target_id, before_json, after_json, "
    "request_id, note, created_at"
)


def query(
    *,
    org_id: str | None = None,
    actor_user_id: str | None = None,
    action: str | None = None,
    action_prefix: str | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    from_ts: float | None = None,
    to_ts: float | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[AuditRow]:
    """Read audit rows with filters. `action_prefix` does a LIKE
    match (e.g. `org.exam.` matches `org.exam.grade.override`,
    `org.exam.create`, etc.).

    Default order is newest-first; limit is capped at 1000 per call
    (use offset to paginate further or export_csv for bulk pulls)."""
    limit = max(1, min(limit, 1000))
    offset = max(0, offset)
    where: list[str] = []
    params: list = []
    if org_id is not None:
        where.append("org_id = ?")
        params.append(org_id)
    if actor_user_id is not None:
        where.append("actor_user_id = ?")
        params.append(actor_user_id)
    if action is not None:
        where.append("action = ?")
        params.append(action)
    if action_prefix is not None:
        where.append("action LIKE ?")
        params.append(action_prefix + "%")
    if target_type is not None:
        where.append("target_type = ?")
        params.append(target_type)
    if target_id is not None:
        where.append("target_id = ?")
        params.append(target_id)
    if from_ts is not None:
        where.append("created_at >= ?")
        params.append(from_ts)
    if to_ts is not None:
        where.append("created_at <= ?")
        params.append(to_ts)
    sql = f"SELECT {_SELECT_COLS} FROM audit_log"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    with _conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_row_to_audit_row(r) for r in rows]


def count(
    *,
    org_id: str | None = None,
    from_ts: float | None = None,
    to_ts: float | None = None,
) -> int:
    """Total matching rows for the given org / time window. Used by
    the admin UI for paging and by the SOC 2 evidence dashboard for
    'how many audit events per day'."""
    where: list[str] = []
    params: list = []
    if org_id is not None:
        where.append("org_id = ?")
        params.append(org_id)
    if from_ts is not None:
        where.append("created_at >= ?")
        params.append(from_ts)
    if to_ts is not None:
        where.append("created_at <= ?")
        params.append(to_ts)
    sql = "SELECT COUNT(*) FROM audit_log"
    if where:
        sql += " WHERE " + " AND ".join(where)
    with _conn() as conn:
        r = conn.execute(sql, params).fetchone()
    return int(r[0]) if r else 0


# ---------- export ----------

CSV_HEADERS = [
    "id", "created_at_iso", "action",
    "actor_user_id", "actor_ip",
    "org_id", "target_type", "target_id",
    "before_json", "after_json",
    "request_id", "note",
]


def export_csv_iter(
    *,
    org_id: str | None,
    from_ts: float | None = None,
    to_ts: float | None = None,
    chunk_size: int = 500,
) -> Iterable[str]:
    """Generator yielding CSV strings — headers first, then chunks of
    rows. Suitable for FastAPI's StreamingResponse so a 100k-row
    export doesn't materialise in memory."""
    import datetime as _dt

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(CSV_HEADERS)
    yield buf.getvalue()
    buf.seek(0)
    buf.truncate()

    offset = 0
    while True:
        rows = query(
            org_id=org_id,
            from_ts=from_ts,
            to_ts=to_ts,
            limit=chunk_size,
            offset=offset,
        )
        if not rows:
            break
        for r in rows:
            iso = _dt.datetime.utcfromtimestamp(r.created_at).isoformat(
                timespec="seconds"
            ) + "Z"
            writer.writerow([
                r.id, iso, r.action,
                r.actor_user_id or "", r.actor_ip or "",
                r.org_id or "", r.target_type or "", r.target_id or "",
                r.before_json or "", r.after_json or "",
                r.request_id or "", r.note or "",
            ])
        yield buf.getvalue()
        buf.seek(0)
        buf.truncate()
        if len(rows) < chunk_size:
            break
        offset += chunk_size


# ---------- helpers for the web layer ----------

def actor_from_request(request) -> dict[str, Any]:
    """Pull standard actor metadata off a Starlette Request. Used by
    web.py's audit call-sites so we don't repeat the same 4 lines
    everywhere.

    Returns a dict of kwargs suitable to pass to record() via **kwargs.
    """
    headers = request.headers if request is not None else {}
    ip = None
    if request is not None:
        # Respect X-Forwarded-For (we sit behind Cloudflare in prod).
        xff = headers.get("x-forwarded-for") if headers else None
        if xff:
            ip = xff.split(",")[0].strip()
        elif request.client:
            ip = request.client.host
    ua = headers.get("user-agent") if headers else None
    rid = headers.get("x-request-id") if headers else None
    return {"actor_ip": ip, "actor_ua": ua, "request_id": rid}
