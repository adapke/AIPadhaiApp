"""Source-file retention + purge.

PRD §18.7 + DPDP §8(7) — minimise stored data. Uploaded source files
(PDFs, images, audio) get auto-deleted N days after the last lesson
derived from them was accessed. Generated artifacts (lesson JSON,
videos, transcripts) are *kept* — those are derivative works the user
explicitly created and pay for.

What this module does NOT do:
  - delete user accounts on inactivity (that's a separate policy)
  - delete cached audio / video that's still referenced by an active
    lesson (cache eviction is sized by disk pressure, not retention)
  - hard-delete admin or moderation audit logs (compliance evidence)

Defaults:
  - Free / Student tiers:  90 days
  - Institutional tiers:  365 days (longer because schools may revisit
                          last year's assignments)
  - Org admin override (set `retention_days` on the org): wins over
    the default
  - Per-upload "keep forever" flag: wins over everything

The purge runs as a periodic job — `purge_due()` is called on a
schedule (cron / Render cron job / Cloudflare scheduled worker) and
deletes everything that's overdue. Idempotent: missing files don't
error; multiple workers safely race.
"""

from __future__ import annotations

import os
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


SCHEMA = """
CREATE TABLE IF NOT EXISTS source_files (
    id              TEXT PRIMARY KEY,
    created_at      REAL NOT NULL,
    last_accessed_at REAL NOT NULL,
    user_id         TEXT,
    org_id          TEXT,
    storage_key     TEXT NOT NULL,         -- R2 / S3 key OR local path
    storage_backend TEXT NOT NULL DEFAULT 'local',
    content_kind    TEXT NOT NULL,         -- 'pdf' | 'image' | 'audio' | 'video'
    size_bytes      INTEGER,
    keep_forever    INTEGER NOT NULL DEFAULT 0,
    purged_at       REAL                   -- set when actually deleted
);
CREATE INDEX IF NOT EXISTS idx_src_files_last ON source_files(last_accessed_at);
CREATE INDEX IF NOT EXISTS idx_src_files_purged ON source_files(purged_at);

CREATE TABLE IF NOT EXISTS retention_policy (
    scope           TEXT PRIMARY KEY,      -- 'default:free' | 'default:institutional' | 'org:<id>'
    days            INTEGER NOT NULL,
    updated_at      REAL NOT NULL,
    updated_by      TEXT                   -- admin user_id
);
"""

# Hardcoded fallback defaults. Operators can override via the
# retention_policy table; values there win.
DEFAULT_RETENTION_DAYS = {
    "free": 90,
    "student": 90,
    "institutional": 365,
}


def _db_path() -> Path:
    from . import db as _db
    return _db.sqlite_path()


def _conn() -> sqlite3.Connection:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=10.0)
    conn.executescript(SCHEMA)
    return conn


@dataclass(frozen=True)
class SourceFile:
    id: str
    storage_key: str
    storage_backend: str
    content_kind: str
    size_bytes: int | None
    user_id: str | None
    org_id: str | None
    last_accessed_at: float
    keep_forever: bool


# ---------- write API (called from /lessons + /explain workers) ----------

def register_upload(
    *, storage_key: str,
    content_kind: str,
    user_id: str | None = None,
    org_id: str | None = None,
    storage_backend: str = "local",
    size_bytes: int | None = None,
) -> str:
    """Record an uploaded file so the retention job knows about it.
    Returns the source_files.id. Idempotent on storage_key — a
    re-upload of the same key updates the last_accessed_at instead of
    creating a duplicate row."""
    import uuid
    now = time.time()
    with _conn() as conn:
        existing = conn.execute(
            "SELECT id FROM source_files WHERE storage_key = ?",
            (storage_key,),
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE source_files SET last_accessed_at = ?, purged_at = NULL "
                "WHERE storage_key = ?",
                (now, storage_key),
            )
            return existing[0]
        sid = uuid.uuid4().hex
        conn.execute(
            "INSERT INTO source_files (id, created_at, last_accessed_at, "
            "user_id, org_id, storage_key, storage_backend, content_kind, "
            "size_bytes) VALUES (?,?,?,?,?,?,?,?,?)",
            (sid, now, now, user_id, org_id, storage_key, storage_backend,
             content_kind, size_bytes),
        )
        return sid


def touch(storage_key: str) -> None:
    """Bump last_accessed_at — call this whenever the source file is
    re-read (e.g. a regenerate or a "change language" request). Resets
    the retention clock."""
    with _conn() as conn:
        conn.execute(
            "UPDATE source_files SET last_accessed_at = ? "
            "WHERE storage_key = ?",
            (time.time(), storage_key),
        )


def set_keep_forever(source_id: str, value: bool, admin_user_id: str) -> None:
    """Admin override — opt this file out of the purge cycle.
    Audit trail lives in moderation_log via the API layer."""
    with _conn() as conn:
        conn.execute(
            "UPDATE source_files SET keep_forever = ? WHERE id = ?",
            (1 if value else 0, source_id),
        )


# ---------- policy lookup ----------

def get_retention_days(*, scope: str, default: int) -> int:
    """Read the configured retention period for a scope, falling back
    to the hardcoded default if unset. Scope examples:
    'default:free', 'default:institutional', 'org:abc-123'."""
    with _conn() as conn:
        row = conn.execute(
            "SELECT days FROM retention_policy WHERE scope = ?", (scope,),
        ).fetchone()
    return row[0] if row else default


def set_retention_days(*, scope: str, days: int, admin_user_id: str) -> None:
    """Admin sets a retention override. Replace-on-conflict."""
    with _conn() as conn:
        conn.execute(
            "INSERT INTO retention_policy (scope, days, updated_at, updated_by) "
            "VALUES (?,?,?,?) "
            "ON CONFLICT(scope) DO UPDATE SET "
            "  days = excluded.days, updated_at = excluded.updated_at, "
            "  updated_by = excluded.updated_by",
            (scope, days, time.time(), admin_user_id),
        )


# ---------- the purge job ----------

@dataclass(frozen=True)
class PurgeResult:
    scanned: int
    deleted: int
    skipped_keep_forever: int
    skipped_missing_file: int
    bytes_freed: int


def purge_due(now: float | None = None) -> PurgeResult:
    """Delete every source file whose last_accessed_at is older than
    the applicable retention window. Idempotent and safe to run
    concurrently — the UPDATE on purged_at acts as the locking
    primitive (we never re-process an already-purged row).

    Returns counts so the admin / ops can monitor the run."""
    now = now or time.time()
    scanned = 0
    deleted = 0
    skipped_keep_forever = 0
    skipped_missing_file = 0
    bytes_freed = 0

    # Resolve the default retention window once per call.
    free_days = get_retention_days(
        scope="default:free", default=DEFAULT_RETENTION_DAYS["free"])
    inst_days = get_retention_days(
        scope="default:institutional",
        default=DEFAULT_RETENTION_DAYS["institutional"])

    with _conn() as conn:
        rows = conn.execute(
            "SELECT id, storage_key, storage_backend, last_accessed_at, "
            "       org_id, size_bytes, keep_forever "
            "FROM source_files WHERE purged_at IS NULL",
        ).fetchall()

        for sid, key, backend, last_ts, org_id, size, keep in rows:
            scanned += 1
            if keep:
                skipped_keep_forever += 1
                continue
            # Org-specific override > institutional default > free default
            if org_id:
                days = get_retention_days(
                    scope=f"org:{org_id}", default=inst_days)
            else:
                days = free_days

            if now - last_ts < days * 86400:
                continue   # not due yet

            # Race-safe claim: mark purged_at NOW; if someone else got
            # here first, the WHERE clause hits 0 rows and we skip.
            claimed = conn.execute(
                "UPDATE source_files SET purged_at = ? "
                "WHERE id = ? AND purged_at IS NULL",
                (now, sid),
            ).rowcount
            if not claimed:
                continue

            try:
                _delete_file(key, backend)
                deleted += 1
                if size:
                    bytes_freed += size
            except FileNotFoundError:
                skipped_missing_file += 1
            except Exception as e:  # noqa: BLE001 — log + continue
                import sys
                print(f"retention.purge_due: delete failed for {key!r}: {e}",
                      file=sys.stderr)

    return PurgeResult(
        scanned=scanned, deleted=deleted,
        skipped_keep_forever=skipped_keep_forever,
        skipped_missing_file=skipped_missing_file,
        bytes_freed=bytes_freed,
    )


def _delete_file(storage_key: str, backend: str) -> None:
    """Delete from the configured backend. Local backend deletes from
    disk; 'r2' / 's3' goes through padhai.storage."""
    if backend == "local":
        p = Path(storage_key)
        if p.exists():
            p.unlink()
            return
        raise FileNotFoundError(storage_key)

    if backend in ("r2", "s3"):
        try:
            from .storage import get_storage
            store = get_storage()
            store.delete(storage_key)
        except Exception:
            raise
        return

    raise ValueError(f"unknown storage backend: {backend!r}")


# ---------- admin / observability ----------

def stats() -> dict:
    with _conn() as conn:
        total = conn.execute("SELECT COUNT(*) FROM source_files").fetchone()[0]
        active = conn.execute(
            "SELECT COUNT(*) FROM source_files WHERE purged_at IS NULL"
        ).fetchone()[0]
        kept = conn.execute(
            "SELECT COUNT(*) FROM source_files WHERE keep_forever = 1"
        ).fetchone()[0]
        purged = conn.execute(
            "SELECT COUNT(*) FROM source_files WHERE purged_at IS NOT NULL"
        ).fetchone()[0]
        bytes_total = conn.execute(
            "SELECT COALESCE(SUM(size_bytes), 0) FROM source_files "
            "WHERE purged_at IS NULL"
        ).fetchone()[0]
    return {
        "total_tracked": total, "active": active,
        "kept_forever": kept, "purged_total": purged,
        "active_bytes": bytes_total,
    }


def list_due(now: float | None = None, limit: int = 50) -> list[dict]:
    """Preview what `purge_due()` will delete on its next run — useful
    for the admin to sanity-check before a big purge cycle."""
    now = now or time.time()
    free_days = get_retention_days(
        scope="default:free", default=DEFAULT_RETENTION_DAYS["free"])
    inst_days = get_retention_days(
        scope="default:institutional",
        default=DEFAULT_RETENTION_DAYS["institutional"])

    with _conn() as conn:
        rows = conn.execute(
            "SELECT id, storage_key, content_kind, org_id, last_accessed_at, "
            "       size_bytes, keep_forever FROM source_files "
            "WHERE purged_at IS NULL ORDER BY last_accessed_at ASC LIMIT ?",
            (limit * 4,),   # over-fetch so filtering keeps `limit` rows
        ).fetchall()

    out = []
    for r in rows:
        if r[6]:
            continue
        days = inst_days if r[3] else free_days
        age_days = (now - r[4]) / 86400
        if age_days >= days:
            out.append({
                "id": r[0], "storage_key": r[1], "kind": r[2],
                "org_id": r[3], "last_accessed_days_ago": int(age_days),
                "retention_days": days, "size_bytes": r[5],
            })
        if len(out) >= limit:
            break
    return out
