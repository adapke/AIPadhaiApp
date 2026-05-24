"""PRD §12 schema — v1.0 additive migration.

PRD §12 specifies 8 tables. Today we have these covered by various
modules:

  ✓ users               padhai/auth.py
  ✓ uploads             padhai/uploads.py (v0.12)
  ✓ generation_jobs     padhai/jobs.py (named `jobs`)
  ✓ usage_daily         padhai/db.py
  ✓ curriculum_index    padhai/curriculum.py (JSON file + pgvector roadmap)

What was missing until v1.0:

  ✗ document_pages      per-page image + extracted text + embeddings
                        UNBLOCKS: real page-level citations (C7-full),
                        multi-page lesson generation, page-by-page chat
  ✗ video_requests      formal v2 API contract in the schema
                        UNBLOCKS: per-request analytics, retention policy
                        per-request, library pagination
  ✗ video_blueprints    versioned Lesson JSON store
                        UNBLOCKS: prompt-version tracking, A/B testing
                        of generators
  ✗ generated_videos    video metadata + view tracking
                        UNBLOCKS: cache hit-rate dashboards, view
                        analytics, refund logic

This module adds those tables as additive — they don't replace
existing tables, they sit alongside. New features (page citations,
prompt versioning) use them; existing code is unchanged. When v1.1
backfills + cuts over, the old code paths get rewritten one by one
without a big-bang migration.

Idempotent. Safe to call on every process boot. Schema is shared
SQLite/Postgres SQL.
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
-- PRD §12 document_pages: per-page provenance for a single upload.
-- One row per page-image fanned out by ingest_source.
CREATE TABLE IF NOT EXISTS document_pages (
    id              TEXT PRIMARY KEY,
    upload_id       TEXT NOT NULL,
    page_number     INTEGER NOT NULL,        -- 1-indexed
    image_path      TEXT NOT NULL,           -- on-disk path or storage key
    storage_backend TEXT NOT NULL DEFAULT 'local',
    extracted_text  TEXT,                    -- OCR / vision extraction
    layout_json     TEXT,                    -- detected blocks: heading, paragraph, equation, diagram
    embedding       BLOB,                    -- pgvector when on Postgres; raw bytes here
    width           INTEGER,
    height          INTEGER,
    created_at      REAL NOT NULL,
    UNIQUE (upload_id, page_number)
);
CREATE INDEX IF NOT EXISTS idx_doc_pages_upload ON document_pages(upload_id);

-- PRD §12 video_requests: formalises what we today carry in `jobs.payload`.
-- The link to the underlying job lives in job_id; lesson + render
-- outputs live in their own tables.
CREATE TABLE IF NOT EXISTS video_requests (
    id              TEXT PRIMARY KEY,
    user_id         TEXT,
    org_id          TEXT,
    upload_id       TEXT,                    -- nullable for typed-topic flows
    job_id          TEXT,                    -- backref to jobs.id once enqueued
    video_mode      TEXT NOT NULL DEFAULT 'teaching',
    user_type       TEXT NOT NULL DEFAULT 'student',
    age             INTEGER,
    grade           TEXT,
    board           TEXT,
    medium          TEXT,
    language        TEXT NOT NULL DEFAULT 'en',
    difficulty      TEXT,
    tone            TEXT,
    duration_seconds INTEGER,
    avatar_style    TEXT,
    output_format   TEXT NOT NULL DEFAULT '16:9',
    include_quiz    INTEGER NOT NULL DEFAULT 1,
    include_subtitles INTEGER NOT NULL DEFAULT 1,
    status          TEXT NOT NULL DEFAULT 'queued',
    profile_json    TEXT,                    -- PersonalizationProfile snapshot
    created_at      REAL NOT NULL,
    completed_at    REAL
);
CREATE INDEX IF NOT EXISTS idx_vr_user    ON video_requests(user_id);
CREATE INDEX IF NOT EXISTS idx_vr_org     ON video_requests(org_id);
CREATE INDEX IF NOT EXISTS idx_vr_upload  ON video_requests(upload_id);
CREATE INDEX IF NOT EXISTS idx_vr_job     ON video_requests(job_id);
CREATE INDEX IF NOT EXISTS idx_vr_created ON video_requests(created_at DESC);

-- PRD §12 video_blueprints: versioned Lesson JSON, separate from the
-- video file. Same blueprint can render to MP4 once + audio-only once;
-- both videos reference the same blueprint_id.
CREATE TABLE IF NOT EXISTS video_blueprints (
    id              TEXT PRIMARY KEY,
    video_request_id TEXT,
    title           TEXT,
    summary         TEXT,
    script_json     TEXT NOT NULL,           -- the Lesson JSON
    storyboard_json TEXT,
    quiz_json       TEXT,
    subtitles_json  TEXT,
    safety_flags_json TEXT,
    prompt_version  TEXT,
    model           TEXT,                    -- 'claude-opus-4-7' etc.
    quality_score   REAL,                    -- auto-evaluator output 0..1
    created_at      REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_vb_request ON video_blueprints(video_request_id);

-- PRD §12 generated_videos: one row per rendered MP4 (or audio,
-- subtitle, thumbnail). cache_key dedupes; view_count + download_count
-- power the cache-hit-rate dashboard.
CREATE TABLE IF NOT EXISTS generated_videos (
    id              TEXT PRIMARY KEY,
    video_request_id TEXT,
    video_blueprint_id TEXT,
    language        TEXT NOT NULL DEFAULT 'en',
    render_mode     TEXT NOT NULL DEFAULT 'animated',
    output_format   TEXT NOT NULL DEFAULT '16:9',
    resolution      TEXT,
    duration_seconds INTEGER,
    mp4_url         TEXT,
    audio_url       TEXT,
    subtitle_url    TEXT,
    thumbnail_url   TEXT,
    storage_backend TEXT NOT NULL DEFAULT 'local',
    cache_key       TEXT,
    view_count      INTEGER NOT NULL DEFAULT 0,
    download_count  INTEGER NOT NULL DEFAULT 0,
    created_at      REAL NOT NULL,
    UNIQUE (cache_key)                       -- the cache primary key
);
CREATE INDEX IF NOT EXISTS idx_gv_request ON generated_videos(video_request_id);
CREATE INDEX IF NOT EXISTS idx_gv_bp      ON generated_videos(video_blueprint_id);
CREATE INDEX IF NOT EXISTS idx_gv_cache   ON generated_videos(cache_key);
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


def migrate() -> None:
    """Idempotent — called once at startup. The schema string uses
    `CREATE TABLE IF NOT EXISTS` so re-runs are no-ops."""
    with _conn():
        pass  # schema runs in _conn()'s executescript


# ---------- document_pages helpers ----------

@dataclass(frozen=True)
class DocumentPage:
    id: str
    upload_id: str
    page_number: int
    image_path: str
    storage_backend: str
    extracted_text: str | None
    width: int | None
    height: int | None
    created_at: float


def add_pages_for_upload(
    *, upload_id: str, page_image_paths: list[Path],
    storage_backend: str = "local",
) -> list[DocumentPage]:
    """Bulk-insert one document_pages row per page image. Called from
    the upload pipeline after ingest_source fans a multi-page document
    into per-page PNGs.

    Idempotent on UNIQUE(upload_id, page_number) — re-running on the
    same upload returns the existing rows."""
    now = time.time()
    out: list[DocumentPage] = []
    with _conn() as conn:
        for i, path in enumerate(page_image_paths, start=1):
            existing = conn.execute(
                "SELECT id, image_path, storage_backend, extracted_text, "
                "       width, height, created_at "
                "FROM document_pages WHERE upload_id = ? AND page_number = ?",
                (upload_id, i),
            ).fetchone()
            if existing:
                out.append(DocumentPage(
                    id=existing[0], upload_id=upload_id, page_number=i,
                    image_path=existing[1], storage_backend=existing[2],
                    extracted_text=existing[3],
                    width=existing[4], height=existing[5],
                    created_at=existing[6],
                ))
                continue
            page_id = uuid.uuid4().hex
            # Probe image dimensions if possible (best-effort)
            w = h = None
            try:
                from PIL import Image
                with Image.open(path) as img:
                    w, h = img.size
            except Exception:
                pass
            conn.execute(
                "INSERT INTO document_pages "
                "(id, upload_id, page_number, image_path, storage_backend, "
                " width, height, created_at) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (page_id, upload_id, i, str(path), storage_backend,
                 w, h, now),
            )
            out.append(DocumentPage(
                id=page_id, upload_id=upload_id, page_number=i,
                image_path=str(path), storage_backend=storage_backend,
                extracted_text=None, width=w, height=h, created_at=now,
            ))
    return out


def list_pages_for_upload(upload_id: str) -> list[DocumentPage]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT id, upload_id, page_number, image_path, storage_backend, "
            "       extracted_text, width, height, created_at "
            "FROM document_pages WHERE upload_id = ? "
            "ORDER BY page_number ASC",
            (upload_id,),
        ).fetchall()
    return [DocumentPage(*r) for r in rows]


def set_extracted_text(*, page_id: str, text: str) -> None:
    """Called after a vision pass extracts text from a page image.
    Stored separately so multiple analyzers can update independently."""
    with _conn() as conn:
        conn.execute(
            "UPDATE document_pages SET extracted_text = ? WHERE id = ?",
            (text, page_id),
        )


# ---------- video_requests helpers ----------

def record_video_request(
    *,
    user_id: str | None,
    org_id: str | None,
    upload_id: str | None,
    job_id: str | None,
    profile_dict: dict,
    status: str = "queued",
) -> str:
    """Snapshot a v2 video request into the relational schema. The
    profile_dict matches PersonalizationProfile (video_mode, language,
    output_format, etc.) so future analytics can group by any axis
    without parsing job.payload."""
    rid = uuid.uuid4().hex
    now = time.time()
    with _conn() as conn:
        conn.execute(
            "INSERT INTO video_requests "
            "(id, user_id, org_id, upload_id, job_id, video_mode, "
            " user_type, age, grade, language, output_format, "
            " duration_seconds, status, profile_json, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (rid, user_id, org_id, upload_id, job_id,
             profile_dict.get("video_mode", "teaching"),
             profile_dict.get("user_type", "student"),
             profile_dict.get("age"),
             profile_dict.get("grade"),
             profile_dict.get("language_code", "en"),
             profile_dict.get("output_format", "16:9"),
             profile_dict.get("duration_seconds"),
             status,
             json.dumps(profile_dict),
             now),
        )
    return rid


def mark_request_completed(request_id: str) -> None:
    with _conn() as conn:
        conn.execute(
            "UPDATE video_requests SET status = 'completed', "
            "completed_at = ? WHERE id = ?",
            (time.time(), request_id),
        )


# ---------- generated_videos helpers ----------

def record_generated_video(
    *,
    video_request_id: str | None,
    video_blueprint_id: str | None,
    cache_key: str,
    mp4_url: str,
    audio_url: str | None = None,
    subtitle_url: str | None = None,
    thumbnail_url: str | None = None,
    language: str = "en",
    render_mode: str = "animated",
    output_format: str = "16:9",
    resolution: str | None = None,
    duration_seconds: int | None = None,
    storage_backend: str = "local",
) -> str:
    """Idempotent on cache_key. If a row with this cache_key already
    exists, return its id without inserting — that's the cache hit
    semantic."""
    with _conn() as conn:
        existing = conn.execute(
            "SELECT id FROM generated_videos WHERE cache_key = ?",
            (cache_key,),
        ).fetchone()
        if existing:
            return existing[0]
        vid = uuid.uuid4().hex
        conn.execute(
            "INSERT INTO generated_videos "
            "(id, video_request_id, video_blueprint_id, language, "
            " render_mode, output_format, resolution, duration_seconds, "
            " mp4_url, audio_url, subtitle_url, thumbnail_url, "
            " storage_backend, cache_key, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (vid, video_request_id, video_blueprint_id, language,
             render_mode, output_format, resolution, duration_seconds,
             mp4_url, audio_url, subtitle_url, thumbnail_url,
             storage_backend, cache_key, time.time()),
        )
        return vid


def bump_view_count(video_id: str) -> None:
    with _conn() as conn:
        conn.execute(
            "UPDATE generated_videos SET view_count = view_count + 1 "
            "WHERE id = ?", (video_id,),
        )


def bump_download_count(video_id: str) -> None:
    with _conn() as conn:
        conn.execute(
            "UPDATE generated_videos SET download_count = download_count + 1 "
            "WHERE id = ?", (video_id,),
        )


def cache_hit_stats() -> dict:
    """Aggregate cache hit rate for the admin dashboard.

    A "cache hit" is a generated_videos row with view_count > 1 OR a
    cache_key shared by multiple video_requests. Approximate; full
    instrumentation lands when generation goes through the new tables
    natively (v1.1)."""
    with _conn() as conn:
        total_videos = conn.execute(
            "SELECT COUNT(*) FROM generated_videos",
        ).fetchone()[0]
        with_views = conn.execute(
            "SELECT COUNT(*) FROM generated_videos WHERE view_count > 1",
        ).fetchone()[0]
        total_views = conn.execute(
            "SELECT COALESCE(SUM(view_count), 0) FROM generated_videos",
        ).fetchone()[0]
        total_downloads = conn.execute(
            "SELECT COALESCE(SUM(download_count), 0) FROM generated_videos",
        ).fetchone()[0]
    return {
        "total_videos": total_videos,
        "videos_with_repeat_views": with_views,
        "hit_rate_pct": (
            round(with_views * 100 / total_videos, 1) if total_videos else 0.0
        ),
        "total_views": total_views,
        "total_downloads": total_downloads,
    }
