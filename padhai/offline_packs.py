"""v3.12 — Offline packs + low-data mode.

Per review §17 — Indian students need low-data, mobile-first,
offline-friendly study. The existing platform assumes always-on
broadband. This module ships the **download manifest** + **size
budget** + **low-data mode signal** so the mobile client (PWA /
Android wrapper) can:

  1. Download notes / audio / video for a study session ahead of
     time
  2. Pin specific Exam Pack chapters for an upcoming flight /
     train ride
  3. Auto-degrade to text-only when on cellular w/ low-data on

Two tables:
  offline_pack_manifests    one row per (user, pack_code, version)
                            with all files + total bytes + expires_at
  offline_downloads         per-user download progress + receipts
                            (so we can re-resume cleanly)

A manifest is generated on-demand. The "pack" is loose — could
be an Exam Pack (`pack_code`), a single chapter (`topic_code`),
or a free-form list of file refs the caller passes.

Files in the manifest:
  ref_kind         'lesson_text' / 'lesson_video' / 'pdf' /
                   'flashcards' / 'audio_recap' / 'mock_paper'
  ref_id           FK to originating table
  bytes            estimated size
  url              signed download URL (CDN module handles signing)
  priority         1 = critical (text/notes); 5 = optional (HD video)

Low-data mode (per-user setting) drives what gets included:
  • text-only       priority 1-2 only; no video, no HD audio
  • standard        priority 1-3 (typical default)
  • full            everything in the pack

PWA + Android client expectations:
  • On WiFi: prefetch the standard manifest for active pack
  • On cellular: only fetch priority 1 (text + flashcards)
  • Service worker keeps a cache of the priority-1 set for offline

This module ships the data layer + manifest generator. The
service worker JS + Android download logic live elsewhere.
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
CREATE TABLE IF NOT EXISTS offline_pack_manifests (
    id              TEXT PRIMARY KEY,
    user_id         TEXT NOT NULL,
    pack_code       TEXT,                            -- exam_packs.code, optional
    topic_code      TEXT,                            -- exam_topics.code, optional
    title           TEXT NOT NULL,
    version         INTEGER NOT NULL DEFAULT 1,
    quality_tier    TEXT NOT NULL DEFAULT 'standard',
    -- 'text_only' | 'standard' | 'full'
    file_count      INTEGER NOT NULL,
    total_bytes     INTEGER NOT NULL,
    files_json      TEXT NOT NULL,                   -- JSON list of file dicts
    expires_at      REAL NOT NULL,
    created_at      REAL NOT NULL,
    UNIQUE (user_id, pack_code, version, quality_tier)
);
CREATE INDEX IF NOT EXISTS idx_opm_user
    ON offline_pack_manifests(user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS offline_downloads (
    id              TEXT PRIMARY KEY,
    manifest_id     TEXT NOT NULL,
    user_id         TEXT NOT NULL,
    bytes_downloaded INTEGER NOT NULL DEFAULT 0,
    files_completed INTEGER NOT NULL DEFAULT 0,
    status          TEXT NOT NULL DEFAULT 'in_progress',
    -- 'in_progress' | 'completed' | 'failed' | 'cancelled'
    started_at      REAL NOT NULL,
    completed_at    REAL,
    last_progress_at REAL,
    network         TEXT,                             -- 'wifi' / 'cellular' / 'unknown'
    UNIQUE (manifest_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_od_user
    ON offline_downloads(user_id, started_at DESC);

CREATE TABLE IF NOT EXISTS low_data_prefs (
    user_id         TEXT PRIMARY KEY,
    quality_tier    TEXT NOT NULL DEFAULT 'standard',
    -- 'text_only' | 'standard' | 'full'
    auto_downgrade_on_cellular INTEGER NOT NULL DEFAULT 1,
    max_daily_mb    INTEGER,                          -- NULL = unlimited
    updated_at      REAL NOT NULL
);
"""


VALID_QUALITY_TIERS = frozenset({"text_only", "standard", "full"})
VALID_DOWNLOAD_STATUSES = frozenset({
    "in_progress", "completed", "failed", "cancelled",
})
VALID_REF_KINDS = frozenset({
    "lesson_text", "lesson_video", "pdf",
    "flashcards", "audio_recap", "mock_paper",
    "notes",
})


# Default expiry — manifests valid for 7 days. Past that, signed
# URLs typically expire too; client re-fetches.
DEFAULT_EXPIRY_HOURS = 168

# Priority filter per tier:
#   text_only → keep priority 1-2 only
#   standard  → priority 1-3
#   full      → all
QUALITY_PRIORITY_CAP = {
    "text_only": 2,
    "standard":  3,
    "full":      5,
}

# Estimated byte sizes when caller doesn't provide one — rough
# averages for the placeholder.
EST_BYTES_BY_KIND = {
    "lesson_text":  20_000,        # 20 KB plain text
    "notes":        50_000,        # 50 KB markdown
    "flashcards":   30_000,        # 30 KB JSON
    "pdf":          2_000_000,     # 2 MB
    "lesson_video": 50_000_000,    # 50 MB SD video
    "audio_recap":  3_000_000,     # 3 MB MP3
    "mock_paper":   100_000,       # 100 KB
}


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
    with _conn():
        pass


# ---------- dataclasses ----------

@dataclass(frozen=True)
class Manifest:
    id: str
    user_id: str
    pack_code: str | None
    topic_code: str | None
    title: str
    version: int
    quality_tier: str
    file_count: int
    total_bytes: int
    files: list[dict]
    expires_at: float
    created_at: float


@dataclass(frozen=True)
class Download:
    id: str
    manifest_id: str
    user_id: str
    bytes_downloaded: int
    files_completed: int
    status: str
    started_at: float
    completed_at: float | None
    last_progress_at: float | None
    network: str | None


@dataclass(frozen=True)
class LowDataPrefs:
    user_id: str
    quality_tier: str
    auto_downgrade_on_cellular: bool
    max_daily_mb: int | None
    updated_at: float


# ---------- low-data prefs ----------

def get_low_data_prefs(user_id: str) -> LowDataPrefs:
    with _conn() as conn:
        r = conn.execute(
            "SELECT user_id, quality_tier, "
            "       auto_downgrade_on_cellular, "
            "       max_daily_mb, updated_at "
            "FROM low_data_prefs WHERE user_id = ?",
            (user_id,),
        ).fetchone()
    if not r:
        return LowDataPrefs(
            user_id=user_id, quality_tier="standard",
            auto_downgrade_on_cellular=True,
            max_daily_mb=None, updated_at=0,
        )
    return LowDataPrefs(
        user_id=r[0], quality_tier=r[1],
        auto_downgrade_on_cellular=bool(r[2]),
        max_daily_mb=r[3], updated_at=r[4],
    )


def set_low_data_prefs(
    *,
    user_id: str,
    quality_tier: str | None = None,
    auto_downgrade_on_cellular: bool | None = None,
    max_daily_mb: int | None = None,
) -> LowDataPrefs:
    """Update user prefs. Pass only the fields you're changing."""
    if (quality_tier is not None
            and quality_tier not in VALID_QUALITY_TIERS):
        raise ValueError(
            f"quality_tier must be in {sorted(VALID_QUALITY_TIERS)}",
        )
    if (max_daily_mb is not None
            and (max_daily_mb < 0 or max_daily_mb > 100_000)):
        raise ValueError("max_daily_mb must be in [0, 100000]")
    current = get_low_data_prefs(user_id)
    new_tier = quality_tier or current.quality_tier
    new_auto = (
        auto_downgrade_on_cellular
        if auto_downgrade_on_cellular is not None
        else current.auto_downgrade_on_cellular
    )
    new_max = (
        max_daily_mb if max_daily_mb is not None
        else current.max_daily_mb
    )
    now = time.time()
    with _conn() as conn:
        conn.execute(
            "INSERT INTO low_data_prefs "
            "(user_id, quality_tier, "
            " auto_downgrade_on_cellular, max_daily_mb, "
            " updated_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET "
            " quality_tier = excluded.quality_tier, "
            " auto_downgrade_on_cellular = "
            "   excluded.auto_downgrade_on_cellular, "
            " max_daily_mb = excluded.max_daily_mb, "
            " updated_at = excluded.updated_at",
            (user_id, new_tier, 1 if new_auto else 0,
             new_max, now),
        )
    return get_low_data_prefs(user_id)


# ---------- manifest generation ----------

def _filter_for_tier(
    files: list[dict], quality_tier: str,
) -> list[dict]:
    """Drop files above the tier's priority cap."""
    cap = QUALITY_PRIORITY_CAP[quality_tier]
    return [
        f for f in files
        if (f.get("priority") or 5) <= cap
    ]


def _validate_files(files: list[dict]) -> list[dict]:
    """Normalise + validate the input file list. Fills in
    `bytes` if absent (from EST_BYTES_BY_KIND) and `priority`
    if absent (default 3 = standard)."""
    out: list[dict] = []
    for i, f in enumerate(files):
        if not isinstance(f, dict):
            raise ValueError(f"files[{i}] must be dict")
        kind = f.get("ref_kind")
        if kind not in VALID_REF_KINDS:
            raise ValueError(
                f"files[{i}].ref_kind must be in "
                f"{sorted(VALID_REF_KINDS)}",
            )
        ref_id = f.get("ref_id")
        if not ref_id or not isinstance(ref_id, str) or len(ref_id) > 64:
            raise ValueError(
                f"files[{i}].ref_id required (max 64 chars)",
            )
        prio = f.get("priority", 3)
        if prio < 1 or prio > 5:
            raise ValueError(
                f"files[{i}].priority must be in [1, 5]",
            )
        bytes_size = f.get("bytes")
        if bytes_size is None:
            bytes_size = EST_BYTES_BY_KIND.get(kind, 100_000)
        if bytes_size < 1 or bytes_size > 5_000_000_000:
            raise ValueError(
                f"files[{i}].bytes must be in [1, 5GB]",
            )
        out.append({
            "ref_kind": kind, "ref_id": ref_id,
            "priority": int(prio), "bytes": int(bytes_size),
            "url": f.get("url"),
            "title": f.get("title"),
        })
    return out


def generate_manifest(
    *,
    user_id: str,
    title: str,
    files: list[dict],
    pack_code: str | None = None,
    topic_code: str | None = None,
    quality_tier: str | None = None,
    version: int = 1,
    expires_in_hours: int = DEFAULT_EXPIRY_HOURS,
) -> Manifest:
    """Build + persist a manifest. If `quality_tier` is None we
    use the user's pref. Files above the tier's priority cap get
    dropped."""
    title = (title or "").strip()
    if len(title) < 3 or len(title) > 200:
        raise ValueError("title must be [3, 200] chars")
    if not files:
        raise ValueError("at least one file required")
    if version < 1 or version > 999:
        raise ValueError("version must be in [1, 999]")
    if expires_in_hours < 1 or expires_in_hours > 720:    # 30 days
        raise ValueError("expires_in_hours must be in [1, 720]")
    if quality_tier is None:
        quality_tier = get_low_data_prefs(user_id).quality_tier
    if quality_tier not in VALID_QUALITY_TIERS:
        raise ValueError(
            f"quality_tier must be in {sorted(VALID_QUALITY_TIERS)}",
        )
    validated = _validate_files(files)
    filtered = _filter_for_tier(validated, quality_tier)
    total = sum(f["bytes"] for f in filtered)
    now = time.time()
    expires = now + expires_in_hours * 3600
    mid = uuid.uuid4().hex
    try:
        with _conn() as conn:
            conn.execute(
                "INSERT INTO offline_pack_manifests "
                "(id, user_id, pack_code, topic_code, title, "
                " version, quality_tier, file_count, total_bytes, "
                " files_json, expires_at, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (mid, user_id, pack_code, topic_code, title,
                 version, quality_tier, len(filtered), total,
                 json.dumps(filtered), expires, now),
            )
    except sqlite3.IntegrityError:
        # Same (user, pack, version, tier) — return existing row.
        with _conn() as conn:
            r = conn.execute(
                _M_SEL + " WHERE user_id = ? AND pack_code IS ? "
                "AND version = ? AND quality_tier = ?",
                (user_id, pack_code, version, quality_tier),
            ).fetchone()
        if r:
            return _row_to_manifest(r)
        raise
    return get_manifest(mid)  # type: ignore[return-value]


def get_manifest(manifest_id: str) -> Manifest | None:
    with _conn() as conn:
        r = conn.execute(
            _M_SEL + " WHERE id = ?", (manifest_id,),
        ).fetchone()
    return _row_to_manifest(r) if r else None


_M_SEL = (
    "SELECT id, user_id, pack_code, topic_code, title, "
    "       version, quality_tier, file_count, total_bytes, "
    "       files_json, expires_at, created_at "
    "FROM offline_pack_manifests"
)


def _row_to_manifest(r) -> Manifest:
    return Manifest(
        id=r[0], user_id=r[1], pack_code=r[2],
        topic_code=r[3], title=r[4],
        version=r[5], quality_tier=r[6],
        file_count=r[7], total_bytes=r[8],
        files=json.loads(r[9]),
        expires_at=r[10], created_at=r[11],
    )


def list_user_manifests(
    user_id: str,
    *,
    active_only: bool = True,
) -> list[Manifest]:
    sql = _M_SEL + " WHERE user_id = ?"
    params: list = [user_id]
    if active_only:
        sql += " AND expires_at > ?"; params.append(time.time())
    sql += " ORDER BY created_at DESC"
    with _conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_row_to_manifest(r) for r in rows]


def delete_manifest(*, manifest_id: str, user_id: str) -> bool:
    with _conn() as conn:
        cur = conn.execute(
            "DELETE FROM offline_pack_manifests "
            "WHERE id = ? AND user_id = ?",
            (manifest_id, user_id),
        )
        if cur.rowcount > 0:
            conn.execute(
                "DELETE FROM offline_downloads "
                "WHERE manifest_id = ?", (manifest_id,),
            )
        return cur.rowcount > 0


# ---------- downloads ----------

def start_download(
    *,
    manifest_id: str,
    user_id: str,
    network: str | None = None,
) -> Download:
    manifest = get_manifest(manifest_id)
    if not manifest:
        raise ValueError("manifest not found")
    if manifest.user_id != user_id:
        raise PermissionError("not your manifest")
    if manifest.expires_at < time.time():
        raise ValueError("manifest expired")
    if network and network not in ("wifi", "cellular", "unknown"):
        raise ValueError("network must be in {wifi, cellular, unknown}")
    did = uuid.uuid4().hex
    now = time.time()
    try:
        with _conn() as conn:
            conn.execute(
                "INSERT INTO offline_downloads "
                "(id, manifest_id, user_id, started_at, "
                " last_progress_at, network) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (did, manifest_id, user_id, now, now, network),
            )
    except sqlite3.IntegrityError:
        # Resume — return existing
        existing = _get_active_download(manifest_id, user_id)
        if existing:
            return existing
        raise
    return _get_download(did)  # type: ignore[return-value]


def _get_active_download(
    manifest_id: str, user_id: str,
) -> Download | None:
    with _conn() as conn:
        r = conn.execute(
            _D_SEL + " WHERE manifest_id = ? AND user_id = ?",
            (manifest_id, user_id),
        ).fetchone()
    return _row_to_download(r) if r else None


def _get_download(download_id: str) -> Download | None:
    with _conn() as conn:
        r = conn.execute(
            _D_SEL + " WHERE id = ?", (download_id,),
        ).fetchone()
    return _row_to_download(r) if r else None


_D_SEL = (
    "SELECT id, manifest_id, user_id, bytes_downloaded, "
    "       files_completed, status, started_at, completed_at, "
    "       last_progress_at, network "
    "FROM offline_downloads"
)


def _row_to_download(r) -> Download:
    return Download(
        id=r[0], manifest_id=r[1], user_id=r[2],
        bytes_downloaded=r[3], files_completed=r[4],
        status=r[5], started_at=r[6], completed_at=r[7],
        last_progress_at=r[8], network=r[9],
    )


def update_progress(
    *,
    download_id: str,
    user_id: str,
    bytes_downloaded: int,
    files_completed: int,
) -> Download:
    """Client reports progress. Doesn't validate against manifest
    size — client can over-report briefly (e.g. range downloads
    that retry)."""
    if bytes_downloaded < 0 or files_completed < 0:
        raise ValueError("bytes / files must be ≥0")
    download = _get_download(download_id)
    if not download:
        raise ValueError("download not found")
    if download.user_id != user_id:
        raise PermissionError("not your download")
    if download.status not in ("in_progress",):
        raise ValueError(
            f"download is {download.status!r}, can't update",
        )
    now = time.time()
    manifest = get_manifest(download.manifest_id)
    completed = (
        manifest is not None
        and files_completed >= manifest.file_count
    )
    new_status = "completed" if completed else "in_progress"
    completed_at = now if completed else None
    with _conn() as conn:
        conn.execute(
            "UPDATE offline_downloads SET "
            " bytes_downloaded = ?, files_completed = ?, "
            " last_progress_at = ?, status = ?, "
            " completed_at = COALESCE(?, completed_at) "
            "WHERE id = ?",
            (bytes_downloaded, files_completed, now,
             new_status, completed_at, download_id),
        )
    return _get_download(download_id)  # type: ignore[return-value]


def cancel_download(*, download_id: str, user_id: str) -> bool:
    with _conn() as conn:
        cur = conn.execute(
            "UPDATE offline_downloads SET "
            " status = 'cancelled', completed_at = ? "
            "WHERE id = ? AND user_id = ? "
            "AND status = 'in_progress'",
            (time.time(), download_id, user_id),
        )
        return cur.rowcount > 0


def list_user_downloads(
    user_id: str,
    *,
    status: str | None = None,
    limit: int = 50,
) -> list[Download]:
    limit = max(1, min(limit, 500))
    sql = _D_SEL + " WHERE user_id = ?"
    params: list = [user_id]
    if status:
        if status not in VALID_DOWNLOAD_STATUSES:
            raise ValueError(
                f"status must be in "
                f"{sorted(VALID_DOWNLOAD_STATUSES)}",
            )
        sql += " AND status = ?"; params.append(status)
    sql += " ORDER BY started_at DESC LIMIT ?"
    params.append(limit)
    with _conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_row_to_download(r) for r in rows]


def user_data_usage_today(user_id: str) -> dict:
    """Bytes downloaded today (for max_daily_mb enforcement).
    Client-side gate; server-side as a sanity check."""
    now = time.time()
    today_start = now - (now % 86400)
    with _conn() as conn:
        r = conn.execute(
            "SELECT COALESCE(SUM(bytes_downloaded), 0) "
            "FROM offline_downloads "
            "WHERE user_id = ? AND last_progress_at >= ?",
            (user_id, today_start),
        ).fetchone()
    bytes_today = int(r[0] or 0)
    prefs = get_low_data_prefs(user_id)
    quota_mb = prefs.max_daily_mb or 0
    used_mb = bytes_today / (1024 * 1024)
    return {
        "bytes_today": bytes_today,
        "mb_today": round(used_mb, 2),
        "max_daily_mb": quota_mb or None,
        "quota_exceeded": bool(
            quota_mb and used_mb > quota_mb,
        ),
    }
