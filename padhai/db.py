"""PostgreSQL backend for the job queue + content caches.

Why Postgres for the 1M-user tier:
  - SQLite-over-NFS contends above ~10 concurrent writers; Postgres
    handles 10k+ trivially
  - Real foreign keys + transactions give us per-user usage aggregation
    for billing without a separate analytics pipeline
  - JSONB columns keep the payload-blob design intact while making each
    field queryable + indexable
  - Available everywhere: Render Postgres, AWS RDS, Aurora Serverless,
    Supabase, Neon, Cloud SQL — pick on price/locality

The classes here are drop-in replacements for the SQLite-backed
JobStore and FilesystemCache: same method names, same semantics,
same idempotency guarantees. `get_db_url()` decides which backend to
construct per process; if `DATABASE_URL` is set we use Postgres,
otherwise the SQLite path keeps working for dev.

This module declares the SCHEMA but doesn't run migrations on import —
that's the job of `scripts/migrate.py` which applies sql/ files in
order. Run once at deploy time, not on every container start."""

from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from dataclasses import dataclass
from typing import Any

# psycopg is imported lazily so the module loads cleanly even when the
# library isn't installed in the dev environment.


SCHEMA_SQL = r"""
-- ===========================================================================
-- PadhAI schema for the production tier (PostgreSQL 14+)
-- See padhai/db.py for the Python interface that uses these tables.
-- ===========================================================================

SET search_path TO public;

CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ---- Users ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS users (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email               TEXT UNIQUE NOT NULL,
    -- Bcrypt-hashed password (set when using LocalAuth). Nullable so
    -- ClerkAuth / OAuth users can have a row without a password.
    password_hash       TEXT,
    -- ID at the external IDP (Clerk, Auth0, etc.) when not using LocalAuth.
    external_id         TEXT UNIQUE,
    dob                 TEXT,
    parent_email        TEXT,
    parent_consent_at   DOUBLE PRECISION,
    parent_consent_ip   TEXT,
    account_locked      BOOLEAN NOT NULL DEFAULT FALSE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- Subscription matrix axes (see LEARN.md §7.1).
    subscription_tier   TEXT NOT NULL DEFAULT 'M1',
    subscription_level  TEXT NOT NULL DEFAULT 'L3'
);
-- Idempotent column adds for existing installs created before auth
-- shipped — keep these so migrate.py is safe to re-run.
ALTER TABLE users ADD COLUMN IF NOT EXISTS password_hash TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS external_id TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS dob TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS parent_email TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS parent_consent_at DOUBLE PRECISION;
ALTER TABLE users ADD COLUMN IF NOT EXISTS parent_consent_ip TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS account_locked BOOLEAN NOT NULL DEFAULT FALSE;
CREATE UNIQUE INDEX IF NOT EXISTS idx_users_external_id
    ON users (external_id) WHERE external_id IS NOT NULL;

-- ---- Lesson JSON cache (Claude vision output, dedup'd) -------------------
CREATE TABLE IF NOT EXISTS lessons (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    image_hash      TEXT NOT NULL,
    language_code   TEXT NOT NULL,
    level           TEXT NOT NULL,
    model           TEXT NOT NULL,
    payload         JSONB NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (image_hash, language_code, level, model)
);
CREATE INDEX IF NOT EXISTS idx_lessons_lookup
    ON lessons (image_hash, language_code, level, model);

-- ---- TTS audio cache -----------------------------------------------------
CREATE TABLE IF NOT EXISTS audio_clips (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    text_hash       TEXT NOT NULL,
    language_code   TEXT NOT NULL,
    provider        TEXT NOT NULL,
    storage_key     TEXT NOT NULL,
    duration_s      NUMERIC(10, 3),
    size_bytes      BIGINT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (text_hash, language_code, provider)
);

-- ---- Rendered video cache ------------------------------------------------
-- The hot path: a content_key match here means we serve the cached MP4
-- URL straight to the client with no compute. view_count + last_served_at
-- drive the pre-render manifest (lever #6 in LEARN.md §6.1).
CREATE TABLE IF NOT EXISTS videos (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    image_hash              TEXT NOT NULL,
    language_code           TEXT NOT NULL,
    level                   TEXT NOT NULL,
    theme                   TEXT NOT NULL,
    talking_head_provider   TEXT NOT NULL,
    render_mode             TEXT NOT NULL,
    storage_key             TEXT NOT NULL,
    duration_s              NUMERIC(10, 3),
    size_bytes              BIGINT,
    view_count              BIGINT NOT NULL DEFAULT 0,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_served_at          TIMESTAMPTZ,
    UNIQUE (image_hash, language_code, level, theme,
            talking_head_provider, render_mode)
);
CREATE INDEX IF NOT EXISTS idx_videos_lookup
    ON videos (image_hash, language_code, level, theme,
               talking_head_provider, render_mode);
CREATE INDEX IF NOT EXISTS idx_videos_popular
    ON videos (view_count DESC, last_served_at DESC);

-- ---- Job queue (replaces the SQLite jobs table) --------------------------
CREATE TABLE IF NOT EXISTS jobs (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID REFERENCES users(id) ON DELETE SET NULL,
    status      TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    payload     JSONB NOT NULL,
    result      JSONB,
    error       TEXT,
    progress_step    TEXT,
    progress_percent INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_jobs_status_created
    ON jobs (status, created_at);
CREATE INDEX IF NOT EXISTS idx_jobs_user
    ON jobs (user_id, created_at DESC);
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS progress_step TEXT;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS progress_percent INTEGER NOT NULL DEFAULT 0;

-- ---- Daily usage aggregates (for billing reports) ------------------------
-- Materialised from `jobs` overnight via a cron job; avoids scanning
-- the whole jobs table for monthly invoices.
CREATE TABLE IF NOT EXISTS usage_daily (
    user_id              UUID REFERENCES users(id) ON DELETE CASCADE,
    day                  DATE NOT NULL,
    videos_generated     INTEGER NOT NULL DEFAULT 0,
    videos_cached        INTEGER NOT NULL DEFAULT 0,
    minutes_generated    NUMERIC(10, 2) NOT NULL DEFAULT 0,
    cost_inr             NUMERIC(10, 2) NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, day)
);
"""


@dataclass
class Job:
    id: str
    status: str
    created_at: float
    updated_at: float
    payload: dict
    result: dict | None
    error: str | None
    progress_step: str | None = None
    progress_percent: int = 0
    user_id: str | None = None


class PostgresJobStore:
    """Drop-in replacement for padhai.jobs.JobStore backed by Postgres.

    All operations use a fresh connection from a pool. Atomic `claim`
    is implemented as a single UPDATE … RETURNING so the row lock
    + status transition happen together — no race when many GPU
    workers poll the queue."""

    def __init__(self, dsn: str, min_pool: int = 1, max_pool: int = 10):
        try:
            from psycopg_pool import ConnectionPool
        except ImportError as e:
            raise RuntimeError(
                "psycopg[pool] required for PostgresJobStore "
                "(pip install 'psycopg[binary,pool]')"
            ) from e
        self.pool = ConnectionPool(
            conninfo=dsn,
            kwargs={"options": "-c search_path=public"},
            min_size=min_pool,
            max_size=max_pool,
            open=True,
        )

    @staticmethod
    def _normalise_user_id(payload: dict, user_id: str | None = None) -> str | None:
        raw = user_id or payload.get("user_id")
        if not raw:
            return None
        try:
            return str(uuid.UUID(str(raw)))
        except (TypeError, ValueError):
            return None

    def close(self) -> None:
        self.pool.close()

    def init_schema(self) -> None:
        with self.pool.connection() as conn, conn.cursor() as cur:
            cur.execute(SCHEMA_SQL)

    def create(self, payload: dict, user_id: str | None = None) -> Job:
        user_id = self._normalise_user_id(payload, user_id)
        with self.pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO jobs (status, payload, user_id) "
                "VALUES ('queued', %s::jsonb, %s) "
                "RETURNING id, created_at, updated_at",
                (json.dumps(payload), user_id),
            )
            row = cur.fetchone()
        return Job(
            id=str(row[0]),
            status="queued",
            created_at=row[1].timestamp(),
            updated_at=row[2].timestamp(),
            payload=payload,
            result=None, error=None, user_id=user_id,
        )

    def get(self, job_id: str) -> Job | None:
        try:
            uuid.UUID(job_id)
        except (TypeError, ValueError):
            return None
        with self.pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT id, status, created_at, updated_at, payload, "
                "result, error, progress_step, progress_percent, user_id "
                "FROM jobs WHERE id = %s",
                (job_id,),
            )
            row = cur.fetchone()
        if not row:
            return None
        return Job(
            id=str(row[0]),
            status=row[1],
            created_at=row[2].timestamp(),
            updated_at=row[3].timestamp(),
            payload=row[4],
            result=row[5],
            error=row[6],
            progress_step=row[7],
            progress_percent=row[8] or 0,
            user_id=str(row[9]) if row[9] else None,
        )

    def update(self, job_id: str, **kwargs: Any) -> None:
        if not kwargs:
            return
        cols = []
        vals: list[Any] = []
        for k, v in kwargs.items():
            if k == "result" and v is not None:
                cols.append("result = %s::jsonb")
                vals.append(json.dumps(v))
            else:
                cols.append(f"{k} = %s")
                vals.append(v)
        cols.append("updated_at = NOW()")
        vals.append(job_id)
        with self.pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                f"UPDATE jobs SET {', '.join(cols)} WHERE id = %s",
                vals,
            )

    def claim(self, job_id: str) -> bool:
        with self.pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE jobs SET status = 'running', updated_at = NOW() "
                "WHERE id = %s AND status = 'queued' RETURNING id",
                (job_id,),
            )
            return cur.fetchone() is not None

    def queued_ids(self, limit: int = 50) -> list[str]:
        with self.pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM jobs WHERE status = 'queued' "
                "ORDER BY created_at LIMIT %s",
                (limit,),
            )
            return [str(r[0]) for r in cur.fetchall()]

    def pending_ids(self) -> list[str]:
        with self.pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM jobs WHERE status IN ('queued', 'running')"
            )
            return [str(r[0]) for r in cur.fetchall()]

    def set_progress(self, job_id: str, step: str, percent: int) -> None:
        from .jobs import PROGRESS_STEPS

        if step not in PROGRESS_STEPS:
            import sys
            print(f"db.set_progress: unknown step {step!r}", file=sys.stderr)
            return
        percent = max(0, min(100, int(percent)))
        with self.pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE jobs SET progress_step = %s, progress_percent = %s, "
                "updated_at = NOW() WHERE id = %s",
                (step, percent, job_id),
            )

    def recent_jobs(
        self, limit: int = 20, filter_user_id: str | None = None,
    ) -> list[Job]:
        params: list[Any] = []
        where = ""
        if filter_user_id:
            normalised = self._normalise_user_id({}, filter_user_id)
            if normalised:
                where = "WHERE user_id = %s OR payload->>'user_id' = %s "
                params.extend([normalised, filter_user_id])
            else:
                where = "WHERE payload->>'user_id' = %s "
                params.append(filter_user_id)
        params.append(limit)
        with self.pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT id, status, created_at, updated_at, payload, "
                "result, error, progress_step, progress_percent, user_id "
                f"FROM jobs {where}"
                "ORDER BY created_at DESC LIMIT %s",
                params,
            )
            rows = cur.fetchall()
        return [
            Job(
                id=str(r[0]),
                status=r[1],
                created_at=r[2].timestamp(),
                updated_at=r[3].timestamp(),
                payload=r[4],
                result=r[5],
                error=r[6],
                progress_step=r[7],
                progress_percent=r[8] or 0,
                user_id=str(r[9]) if r[9] else None,
            )
            for r in rows
        ]


class PostgresVideoCache:
    """Drop-in replacement for the video tier of padhai.cache.Cache.

    Backed by:
      - Postgres for the lookup metadata (which key, what duration,
        view counts)
      - An ObjectStorage backend (typically S3/R2) for the MP4 bytes
        themselves

    Returned 'cache hit' is just a URL — the web tier redirects the
    client to it, never streaming the bytes through itself. This is
    what unbottlenecks the deploy at 1M users."""

    def __init__(self, store: PostgresJobStore, object_storage):
        self.store = store
        self.object_storage = object_storage

    @staticmethod
    def _hash(*chunks: bytes) -> str:
        h = hashlib.sha256()
        for c in chunks:
            if isinstance(c, str):
                c = c.encode("utf-8")
            h.update(c)
            h.update(b"\x00")
        return h.hexdigest()

    def lookup(
        self,
        image_bytes: bytes,
        language: str,
        level: str,
        theme: str,
        provider: str,
        render_mode: str,
    ) -> str | None:
        """Return the playback URL for a cached video, or None on miss.
        Increments view_count + last_served_at on hit."""
        image_hash = self._hash(image_bytes)
        with self.store.pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE videos SET view_count = view_count + 1, "
                "last_served_at = NOW() "
                "WHERE image_hash = %s AND language_code = %s "
                "AND level = %s AND theme = %s "
                "AND talking_head_provider = %s AND render_mode = %s "
                "RETURNING storage_key",
                (image_hash, language, level, theme, provider, render_mode),
            )
            row = cur.fetchone()
        if not row:
            return None
        return self.object_storage.url(row[0])

    def store_video(
        self,
        image_bytes: bytes,
        language: str,
        level: str,
        theme: str,
        provider: str,
        render_mode: str,
        local_path,
        duration_s: float | None = None,
    ) -> str:
        """Upload the rendered MP4 + insert the cache row. Returns the
        playback URL."""
        from pathlib import Path
        local_path = Path(local_path)
        image_hash = self._hash(image_bytes)
        key = f"videos/{image_hash}-{language}-{level}-{theme}-{provider}-{render_mode}.mp4"
        url = self.object_storage.put(key, local_path)
        size = local_path.stat().st_size
        with self.store.pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO videos (image_hash, language_code, level, "
                "theme, talking_head_provider, render_mode, storage_key, "
                "duration_s, size_bytes) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (image_hash, language_code, level, theme, "
                "talking_head_provider, render_mode) DO UPDATE SET "
                "storage_key = EXCLUDED.storage_key, "
                "size_bytes = EXCLUDED.size_bytes, "
                "duration_s = EXCLUDED.duration_s",
                (image_hash, language, level, theme, provider, render_mode,
                 key, duration_s, size),
            )
        return url


def get_db_url() -> str | None:
    return os.environ.get("DATABASE_URL")


def use_postgres() -> bool:
    url = (get_db_url() or "").strip()
    return url.startswith(("postgres://", "postgresql://"))
