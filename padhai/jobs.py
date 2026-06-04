"""SQLite-backed background-job queue for the FastAPI service.

The web service can't render synchronously: a real video render takes
1-3 minutes on the M1 cartoon tier, up to 10 minutes on M3 Wav2Lip,
and longer on hosted M4 providers. Render's free / starter plans drop
idle HTTP connections after 60 seconds. So `/lessons` must return
immediately with a job id; clients poll `/jobs/{id}` for status, then
fetch `/jobs/{id}/video` once done.

Why SQLite, not Redis:
  - Redis on Render is a paid add-on; SQLite is free.
  - Render's persistent disk persists SQLite across deploys.
  - Single-instance worker is fine for the prototype; horizontal
    scale-out comes later with a proper queue.

Job lifecycle:
    created → queued → running → (succeeded | failed)

On worker restart, any 'running' jobs are reset to 'queued' so they
re-execute. The cache layer makes this safe — re-running an already-
completed step is a no-op."""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from threading import Lock

SCHEMA = """
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
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
"""

# Idempotent schema upgrades for SQLite when older DB files exist.
# SQLite ignores ADD COLUMN if the column already exists only when wrapped
# in try/except — done in JobStore.__init__.
SCHEMA_MIGRATIONS = [
    "ALTER TABLE jobs ADD COLUMN progress_step TEXT",
    "ALTER TABLE jobs ADD COLUMN progress_percent INTEGER NOT NULL DEFAULT 0",
]


# PRD §10.2 Generation Progress screen — the canonical step sequence the
# worker emits. Keep this list in sync with web._PROGRESS_STEPS so the API
# contract and the runtime emit-set never drift.
PROGRESS_STEPS = (
    "queued",
    "analyzing_document",
    "understanding_topic",
    "creating_script",
    "creating_storyboard",
    "generating_voice",
    "rendering_video",
    "preparing_quiz",
    "uploading",
    "complete",
)


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


class JobStore:
    """Thread-safe SQLite wrapper. `db_path` is created if missing."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()
        with self._connect() as conn:
            conn.executescript(SCHEMA)
            # Apply additive migrations. SQLite has no IF NOT EXISTS for
            # ADD COLUMN, so swallow the duplicate-column error.
            for stmt in SCHEMA_MIGRATIONS:
                try:
                    conn.execute(stmt)
                except sqlite3.OperationalError as e:
                    if "duplicate column name" not in str(e).lower():
                        raise

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path, check_same_thread=False, timeout=30.0)

    def create(self, payload: dict) -> Job:
        job_id = uuid.uuid4().hex
        now = time.time()
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO jobs (id, status, created_at, updated_at, payload) "
                "VALUES (?, 'queued', ?, ?, ?)",
                (job_id, now, now, json.dumps(payload)),
            )
        return Job(
            id=job_id, status="queued",
            created_at=now, updated_at=now,
            payload=payload, result=None, error=None,
        )

    def get(self, job_id: str) -> Job | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id, status, created_at, updated_at, payload, result, error, "
                "progress_step, progress_percent "
                "FROM jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
        if not row:
            return None
        return Job(
            id=row[0],
            status=row[1],
            created_at=row[2],
            updated_at=row[3],
            payload=json.loads(row[4]),
            result=json.loads(row[5]) if row[5] else None,
            error=row[6],
            progress_step=row[7],
            progress_percent=row[8] or 0,
        )

    def set_progress(self, job_id: str, step: str, percent: int) -> None:
        """Worker emits step events as it transitions through the
        PRD §10.2 pipeline. UI polls /api/v2/.../status to surface them.
        Idempotent — safe to call repeatedly with the same step."""
        if step not in PROGRESS_STEPS:
            # Don't raise — bad step name shouldn't crash the worker.
            # Log via stderr only, fall back to no-op.
            import sys
            print(f"jobs.set_progress: unknown step {step!r}", file=sys.stderr)
            return
        now = time.time()
        percent = max(0, min(100, int(percent)))
        with self._lock, self._connect() as conn:
            conn.execute(
                "UPDATE jobs SET progress_step = ?, progress_percent = ?, "
                "updated_at = ? WHERE id = ?",
                (step, percent, now, job_id),
            )

    def update(self, job_id: str, **kwargs) -> None:
        kwargs["updated_at"] = time.time()
        if "result" in kwargs and kwargs["result"] is not None:
            kwargs["result"] = json.dumps(kwargs["result"])
        if not kwargs:
            return
        cols = ", ".join(f"{k} = ?" for k in kwargs)
        vals = list(kwargs.values()) + [job_id]
        with self._lock, self._connect() as conn:
            conn.execute(f"UPDATE jobs SET {cols} WHERE id = ?", vals)

    def claim(self, job_id: str) -> bool:
        """Atomically transition a job from queued → running. Returns
        True if this caller won the race, False if another worker beat
        them to it. The web-service runner and the GPU worker share one
        SQLite DB, so this is the contention point that keeps a job from
        being rendered twice."""
        now = time.time()
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                "UPDATE jobs SET status = 'running', updated_at = ? "
                "WHERE id = ? AND status = 'queued'",
                (now, job_id),
            )
            return cur.rowcount > 0

    def queued_ids(self, limit: int = 50) -> list[str]:
        """Oldest-first list of currently-queued job IDs. Polling workers
        scan this and try to `claim()` each one."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id FROM jobs WHERE status = 'queued' "
                "ORDER BY created_at LIMIT ?",
                (limit,),
            ).fetchall()
        return [r[0] for r in rows]

    def find_siblings(self, leader_id: str) -> list[Job]:
        """Return all sibling jobs (parent_job_id == leader_id) plus
        the leader itself, sorted by page_number. Used by the
        multi-page video stitcher to discover every page job belonging
        to one upload.

        SQLite's json_extract returns NULL for jobs that don't carry
        the field — those are filtered out by the WHERE clause.
        Returns [] when nothing matches the leader_id (single-page
        uploads have no siblings)."""
        out: list[Job] = []
        # Pull the leader itself (page_number=1, no parent_job_id) ...
        leader = self.get(leader_id)
        if leader is not None:
            out.append(leader)
        # ... then the siblings.
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id FROM jobs "
                "WHERE json_extract(payload, '$.parent_job_id') = ? "
                "ORDER BY CAST(json_extract(payload, '$.page_number') "
                "         AS INTEGER)",
                (leader_id,),
            ).fetchall()
        for (sid,) in rows:
            j = self.get(sid)
            if j is not None:
                out.append(j)
        return out

    def pending_ids(self) -> list[str]:
        """Jobs left in queued/running state — used to resume on restart."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id FROM jobs WHERE status IN ('queued', 'running')"
            ).fetchall()
        return [r[0] for r in rows]

    def recent_jobs(
        self, limit: int = 20, filter_user_id: str | None = None,
    ) -> list[Job]:
        """Newest-first list of recent jobs, optionally filtered by user.
        Used by the Library module in the web UI."""
        if filter_user_id:
            # SQLite path: payload is JSON text — naive LIKE filter is fine
            # for prototype scale. PostgresJobStore overrides this with a
            # proper JSONB path query.
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT id, status, created_at, updated_at, payload, "
                    "result, error FROM jobs "
                    "WHERE payload LIKE ? "
                    "ORDER BY created_at DESC LIMIT ?",
                    (f'%"user_id": "{filter_user_id}"%', limit),
                ).fetchall()
        else:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT id, status, created_at, updated_at, payload, "
                    "result, error FROM jobs "
                    "ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        return [
            Job(
                id=r[0], status=r[1],
                created_at=r[2], updated_at=r[3],
                payload=json.loads(r[4]),
                result=json.loads(r[5]) if r[5] else None,
                error=r[6],
            )
            for r in rows
        ]


class JobRunner:
    """In-process job runner — used by the web service.

    Submitting a job:
      - writes it to SQLite (so any other worker can pick it up)
      - if `job_filter(payload)` returns True, also queues the job into
        this process's thread pool for immediate execution. The web
        service uses a filter that returns False for Wav2Lip-tier jobs,
        so those land in SQLite and a GPU worker picks them up.

    Workers always go through `store.claim()` before processing so the
    job is guaranteed to be executed exactly once across the fleet."""

    def __init__(
        self,
        store: JobStore,
        worker_fn: Callable[[Job], dict],
        max_workers: int = 1,
        job_filter: Callable[[dict], bool] | None = None,
        post_succeed_hook: Callable[[Job, dict], None] | None = None,
    ):
        self.store = store
        self.worker_fn = worker_fn
        self.job_filter = job_filter or (lambda payload: True)
        # `post_succeed_hook(job, result)` runs AFTER the job has been
        # marked succeeded in the store — gives the web layer a chance
        # to trigger downstream work (e.g. auto-combine of multi-page
        # video uploads when the last sibling finishes). Best-effort:
        # exceptions inside the hook are swallowed so a hook bug
        # doesn't roll back the successful job.
        self.post_succeed_hook = post_succeed_hook
        self.executor = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="padhai-render",
        )

    def enqueue(self, payload: dict) -> Job:
        job = self.store.create(payload)
        if self.job_filter(payload):
            self.executor.submit(self._run, job.id)
        return job

    def _run(self, job_id: str) -> None:
        # Only one worker wins the race for any given job.
        if not self.store.claim(job_id):
            return
        job = self.store.get(job_id)
        if job is None:
            return
        try:
            result = self.worker_fn(job)
            self.store.update(job_id, status="succeeded", result=result)
        except Exception as e:
            self.store.update(job_id, status="failed", error=str(e))
            return
        if self.post_succeed_hook is not None:
            try:
                # Re-fetch so the hook sees the persisted succeeded state
                # — multi-page auto-combine queries the store for sibling
                # statuses and would otherwise miss the just-finished
                # job.
                refreshed = self.store.get(job_id)
                if refreshed is not None:
                    self.post_succeed_hook(refreshed, result)
            except Exception as e:
                import sys
                print(
                    f"jobs.post_succeed_hook failed (non-fatal): {e}",
                    file=sys.stderr,
                )

    def resume_pending(self) -> int:
        """Re-submit any jobs left in queued/running after a worker
        restart. The `claim()` in `_run` keeps this safe across multiple
        workers."""
        ids = self.store.pending_ids()
        count = 0
        for jid in ids:
            self.store.update(jid, status="queued")
            job = self.store.get(jid)
            if job is not None and self.job_filter(job.payload):
                self.executor.submit(self._run, jid)
                count += 1
        return count

    def shutdown(self, wait: bool = False) -> None:
        self.executor.shutdown(wait=wait)


class PollingRunner:
    """Long-running worker process that picks up queued jobs matching a
    filter and runs them in this process. Used by out-of-process workers
    — typically a GPU instance running padhai/gpu_worker.py.

    Polls SQLite every `poll_seconds`. On each tick, scans the oldest N
    queued jobs, applies `job_filter`, and claims the first match it can.
    `claim()` is atomic so multiple PollingRunner instances on the same
    DB will not collide."""

    def __init__(
        self,
        store: JobStore,
        worker_fn: Callable[[Job], dict],
        job_filter: Callable[[dict], bool],
        poll_seconds: float = 2.0,
        scan_limit: int = 50,
    ):
        self.store = store
        self.worker_fn = worker_fn
        self.job_filter = job_filter
        self.poll_seconds = poll_seconds
        self.scan_limit = scan_limit
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    def run_forever(self) -> None:
        while not self._stop:
            if not self._pick_and_run():
                time.sleep(self.poll_seconds)

    def _pick_and_run(self) -> bool:
        for jid in self.store.queued_ids(limit=self.scan_limit):
            job = self.store.get(jid)
            if job is None or not self.job_filter(job.payload):
                continue
            if not self.store.claim(jid):
                continue
            try:
                result = self.worker_fn(job)
                self.store.update(jid, status="succeeded", result=result)
            except Exception as e:
                self.store.update(jid, status="failed", error=str(e))
            return True
        return False
