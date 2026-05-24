"""Read-only data access for the admin console.

Standalone — does NOT import from `padhai.*`. The admin reads the
operational database directly via `sqlite3`. The shape of the `jobs`
table is the operational contract between the two services; when the
main app changes that schema, this module's SELECTs need to be updated
in lockstep (or moved to a versioned read-only view).

For the cache directory listings we read the filesystem directly under
`PADHAI_CACHE_DIR` (the main app's setting) — again, filesystem layout
is the contract, not a Python import.
"""

from __future__ import annotations

import json
import os
import sqlite3
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any


# The main app's job DB path. Override via ADMIN_JOBS_DB_PATH if the
# admin runs in a different container than the writer (e.g. via a
# read-replica or shared NFS mount).
def _jobs_db_path() -> Path:
    custom = os.environ.get("ADMIN_JOBS_DB_PATH") or os.environ.get("PADHAI_DB_PATH")
    if custom:
        return Path(custom).expanduser()
    return Path.home() / ".padhai" / "jobs.db"


def _cache_dir() -> Path:
    custom = os.environ.get("PADHAI_CACHE_DIR")
    if custom:
        return Path(custom).expanduser()
    return Path.home() / ".padhai" / "cache"


@dataclass(frozen=True)
class DashboardSummary:
    """Top-of-dashboard numbers — designed to fit one screen scroll."""
    total_jobs: int
    queued: int
    running: int
    succeeded: int
    failed: int
    succeeded_today: int
    failed_today: int
    cache_artifacts_total: int
    avg_render_seconds: float | None
    p95_render_seconds: float | None


def _conn() -> sqlite3.Connection:
    """Open a read-only connection to the jobs DB.

    Uses `mode=ro` so even a buggy admin handler can't accidentally
    write. If the DB file doesn't exist yet (fresh box, no jobs ever
    run), return a connection to an in-memory empty schema so the
    dashboard renders "0 of everything" instead of crashing."""
    path = _jobs_db_path()
    if not path.exists():
        conn = sqlite3.connect(":memory:")
        conn.executescript(_FALLBACK_SCHEMA)
        return conn
    uri = f"file:{path}?mode=ro"
    return sqlite3.connect(uri, uri=True, timeout=10.0)


# A copy of just the columns this module reads — enough for an empty
# fallback connection when the main app hasn't started yet. Kept in
# sync with padhai/jobs.py manually; mismatches are caught by the
# SELECT statements below.
_FALLBACK_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id          TEXT PRIMARY KEY,
    status      TEXT NOT NULL,
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL,
    payload     TEXT NOT NULL,
    result      TEXT,
    error       TEXT,
    progress_step    TEXT,
    progress_percent INTEGER NOT NULL DEFAULT 0
);
"""


def dashboard_summary() -> DashboardSummary:
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM jobs")
        total = cur.fetchone()[0]

        rows = dict(cur.execute(
            "SELECT status, COUNT(*) FROM jobs GROUP BY status"
        ).fetchall())

        cur.execute(
            "SELECT status, COUNT(*) FROM jobs "
            "WHERE created_at > strftime('%s','now') - 86400 "
            "GROUP BY status"
        )
        today = dict(cur.fetchall())

        cur.execute(
            "SELECT updated_at - created_at FROM jobs "
            "WHERE status='succeeded' AND updated_at > created_at "
            "ORDER BY updated_at DESC LIMIT 200"
        )
        durations = [r[0] for r in cur.fetchall() if r[0] is not None]

    avg = sum(durations) / len(durations) if durations else None
    p95 = None
    if durations:
        sd = sorted(durations)
        p95 = sd[int(len(sd) * 0.95)] if len(sd) > 1 else sd[0]

    return DashboardSummary(
        total_jobs=total,
        queued=rows.get("queued", 0),
        running=rows.get("running", 0),
        succeeded=rows.get("succeeded", 0),
        failed=rows.get("failed", 0),
        succeeded_today=today.get("succeeded", 0),
        failed_today=today.get("failed", 0),
        cache_artifacts_total=_cache_artifact_total(),
        avg_render_seconds=avg,
        p95_render_seconds=p95,
    )


def _cache_artifact_total() -> int:
    """Count rendered artifacts sitting in the on-disk cache directory."""
    root = _cache_dir()
    if not root.exists():
        return 0
    total = 0
    for sub in ("lessons", "videos", "audio", "flashcards", "recaps",
                "notes", "explainers", "learning_paths", "curriculum"):
        path = root / sub
        if path.exists():
            total += sum(1 for _ in path.iterdir())
    return total


def list_jobs(
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict]:
    """Paginated job list with all the fields the admin queue view needs."""
    where = ""
    params: list[Any] = []
    if status:
        where = "WHERE status = ?"
        params.append(status)
    params.extend([limit, offset])

    with _conn() as conn:
        rows = conn.execute(
            f"SELECT id, status, created_at, updated_at, payload, error, "
            f"progress_step, progress_percent "
            f"FROM jobs {where} ORDER BY created_at DESC "
            f"LIMIT ? OFFSET ?",
            params,
        ).fetchall()

    out: list[dict] = []
    for r in rows:
        try:
            payload = json.loads(r[4])
        except (ValueError, TypeError):
            payload = {}
        out.append({
            "id": r[0],
            "status": r[1],
            "created_at": r[2],
            "updated_at": r[3],
            "duration_seconds": (r[3] - r[2]) if r[1] == "succeeded" else None,
            "language": payload.get("language", "—"),
            "level": payload.get("level", "—"),
            "video_mode": (payload.get("profile_json") or {}).get("video_mode", "—"),
            "kind": payload.get("kind", "lesson"),
            "topic": payload.get("topic"),
            "user_id": payload.get("user_id"),
            "tier": payload.get("subscription_tier"),
            "error": r[5],
            "progress_step": r[6],
            "progress_percent": r[7] or 0,
        })
    return out


def popular_topics(limit: int = 10) -> list[dict]:
    """Top explainer topics by job count over last 7 days."""
    with _conn() as conn:
        rows = conn.execute(
            "SELECT payload FROM jobs "
            "WHERE created_at > strftime('%s','now') - 604800"
        ).fetchall()
    counter: Counter = Counter()
    for (raw,) in rows:
        try:
            p = json.loads(raw)
        except (ValueError, TypeError):
            continue
        topic = p.get("topic")
        if topic:
            counter[topic.strip().lower()] += 1
    return [{"topic": t, "count": c} for t, c in counter.most_common(limit)]


def language_usage(limit: int = 12) -> list[dict]:
    """Most-requested languages last 7 days."""
    with _conn() as conn:
        rows = conn.execute(
            "SELECT payload FROM jobs "
            "WHERE created_at > strftime('%s','now') - 604800"
        ).fetchall()
    counter: Counter = Counter()
    for (raw,) in rows:
        try:
            p = json.loads(raw)
        except (ValueError, TypeError):
            continue
        lang = p.get("language")
        if lang:
            counter[lang] += 1
    return [{"language": l, "count": c} for l, c in counter.most_common(limit)]


def daily_volume(days: int = 14) -> list[dict]:
    """One row per day: jobs created + succeeded + failed."""
    with _conn() as conn:
        rows = conn.execute(
            "SELECT date(created_at,'unixepoch') AS day, "
            "       COUNT(*) AS created, "
            "       SUM(CASE WHEN status='succeeded' THEN 1 ELSE 0 END) AS ok, "
            "       SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) AS fail "
            "FROM jobs "
            "WHERE created_at > strftime('%s','now') - ? "
            "GROUP BY day ORDER BY day ASC",
            (days * 86400,),
        ).fetchall()
    return [
        {"day": r[0], "created": r[1], "succeeded": r[2], "failed": r[3]}
        for r in rows
    ]


def failed_jobs(limit: int = 50) -> list[dict]:
    """Failed-render queue for the admin to triage + retry."""
    return list_jobs(status="failed", limit=limit)


# ---------- write actions (the only mutations admin performs) ----------

def retry_job(job_id: str) -> str:
    """Move a failed job back to 'queued' so the next worker poll picks
    it up. Returns the new status. Raises if the job doesn't exist or
    isn't in a state that can be retried."""
    import time
    path = _jobs_db_path()
    if not path.exists():
        from fastapi import HTTPException
        raise HTTPException(404, "no job database yet")
    with sqlite3.connect(str(path), timeout=10.0) as conn:
        row = conn.execute(
            "SELECT status FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()
        if not row:
            from fastapi import HTTPException
            raise HTTPException(404, "job not found")
        current = row[0]
        if current not in ("failed", "succeeded"):
            return current
        conn.execute(
            "UPDATE jobs SET status='queued', error=NULL, "
            "progress_step=NULL, progress_percent=0, updated_at=? "
            "WHERE id=?",
            (time.time(), job_id),
        )
    return "queued"


def cancel_job(job_id: str, admin_email: str) -> None:
    """Mark a queued/running job as failed so workers stop picking it up."""
    import time
    path = _jobs_db_path()
    if not path.exists():
        from fastapi import HTTPException
        raise HTTPException(404, "no job database yet")
    with sqlite3.connect(str(path), timeout=10.0) as conn:
        cur = conn.execute(
            "UPDATE jobs SET status='failed', error=?, updated_at=? "
            "WHERE id=? AND status IN ('queued','running')",
            (f"cancelled by admin ({admin_email})", time.time(), job_id),
        )
        if cur.rowcount == 0:
            from fastapi import HTTPException
            raise HTTPException(404, "job not found or not cancellable")


def cache_stats() -> dict:
    """Per-tier artifact count + total bytes."""
    root = _cache_dir()
    out: dict = {"tiers": {}}
    for tier in ("lessons", "videos", "audio", "flashcards", "recaps",
                 "notes", "explainers", "learning_paths", "curriculum"):
        path = root / tier
        if not path.exists():
            out["tiers"][tier] = {"count": 0, "bytes": 0}
            continue
        count = 0
        size = 0
        for entry in path.iterdir():
            if entry.is_file():
                count += 1
                try:
                    size += entry.stat().st_size
                except OSError:
                    pass
        out["tiers"][tier] = {"count": count, "bytes": size}
    out["total_artifacts"] = sum(t["count"] for t in out["tiers"].values())
    out["total_bytes"] = sum(t["bytes"] for t in out["tiers"].values())
    return out
