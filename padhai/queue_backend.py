"""G2 — Distributed job queue scaffolding.

Today the web tier owns its own `JobRunner` (`padhai/jobs.py`), a
ThreadPoolExecutor pinned to the FastAPI process. Web replicas size up
to handle the heaviest concurrent render load + 1, which means CPU
sits idle most of the time (renders are bursty).

v1.2 introduces an abstraction so we can swap the in-process runner
for a Redis-backed RQ queue at deploy time — workers run in their
own auto-scaling container fleet; web stays tiny.

Selection rule (same shape as `db_backend.py`):

  REDIS_URL=redis://... + `rq` installed → distributed RQ mode
  REDIS_URL=redis://... + `rq` NOT installed → log warning, fall back
  REDIS_URL unset → in-process JobRunner (today's behaviour)

The full cutover is described in `G2_QUEUE_MIGRATION.md`. v1.2 ships
the abstraction + the worker entrypoint module; the actual cutover
needs Upstash/Render Redis provisioned + `rq` added to requirements.

Interface contract — every runner implements:

    enqueue(payload: dict) -> Job       # idempotent on a stable payload key
    resume_pending() -> int             # called at startup; re-claims orphans
    shutdown(wait: bool = False) -> None

Anything new (priority queues, per-tenant fairness, retry-with-backoff)
gets implemented in the RQRunner without breaking the in-process path.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Protocol

REDIS_BACKEND = "redis"
INPROCESS_BACKEND = "inprocess"


@dataclass(frozen=True)
class QueueConfig:
    backend: str           # 'redis' | 'inprocess'
    redis_url: str | None
    rq_available: bool


def resolve() -> QueueConfig:
    """Inspect environment + import state; return what we're going to
    use. Does NOT instantiate anything — that's the caller's job
    (web.py builds the runner from this config)."""
    url = os.environ.get("REDIS_URL", "").strip()
    if not url:
        return QueueConfig(backend=INPROCESS_BACKEND, redis_url=None,
                           rq_available=False)
    try:
        import rq  # noqa: F401
        return QueueConfig(backend=REDIS_BACKEND, redis_url=url,
                           rq_available=True)
    except ImportError:
        return QueueConfig(backend=INPROCESS_BACKEND, redis_url=url,
                           rq_available=False)


def description() -> str:
    """Human banner for the startup log. Hides Redis password if set."""
    cfg = resolve()
    if cfg.backend == REDIS_BACKEND and cfg.redis_url:
        # redis://[user:pass@]host:port/db → keep host+db, hide creds
        try:
            from urllib.parse import urlparse
            p = urlparse(cfg.redis_url)
            host = p.hostname or "?"
            port = p.port or 6379
            db = p.path.lstrip("/") or "0"
            return f"redis-rq://{host}:{port}/{db}"
        except Exception:  # noqa: BLE001
            return "redis-rq://[redacted]"
    if cfg.backend == INPROCESS_BACKEND and cfg.redis_url:
        return ("inprocess (REDIS_URL set but `rq` not installed — "
                "see G2_QUEUE_MIGRATION.md)")
    return "inprocess (single-replica thread pool)"


# ---------- RQ-backed runner (skeleton; only used when configured) ----------

class RQRunner:
    """Skeleton wrapper around python-rq. Lives here to document the
    interface; the real wiring is exercised in the cutover sprint when
    REDIS_URL + `rq` are present.

    Today's web.py instantiates the in-process JobRunner directly
    (matching the v0.6 → v1.1 shape). When the cutover lands we'll
    flip web.py's lifespan to call `build_runner()` below — that's the
    only line that has to change.

    The class methods follow JobRunner's contract exactly so callers
    don't care which one is active.
    """

    def __init__(self, *, redis_url: str, store, worker_fn,
                 max_workers: int = 4, queue_name: str = "renders"):
        # Imports are local so this file doesn't require `rq` to be
        # importable at module load time.
        import redis as _redis
        from rq import Queue
        self._store = store
        self._worker_fn = worker_fn
        self._conn = _redis.from_url(redis_url)
        self._queue = Queue(name=queue_name, connection=self._conn)
        self._max_workers = max_workers
        self._queue_name = queue_name

    def enqueue(self, payload: dict):
        """Write the job row to the DB first (so /jobs/{id} works
        immediately) then dispatch to RQ. The worker, on the other
        side, claims by job_id and calls worker_fn(job).

        Idempotency: the job row's primary key is the dedupe key.
        If a webhook fires twice we'd see store.enqueue raise on
        UNIQUE — caller handles."""
        job = self._store.create(payload)
        self._queue.enqueue(
            "padhai.worker_entrypoint.dispatch_one",
            job.id,
            job_id=job.id,
            retry=_default_retry(),
            failure_ttl=86400,
            result_ttl=3600,
        )
        return job

    def resume_pending(self) -> int:
        """At worker boot, any rows left pending in the DB but not in
        Redis (e.g. crash between DB-write + redis push) get
        re-enqueued. Idempotent — RQ + the worker dedupe on job_id."""
        pending = self._store.pending_ids()
        for job_id in pending:
            self._queue.enqueue(
                "padhai.worker_entrypoint.dispatch_one",
                job_id,
                job_id=job_id,
                retry=_default_retry(),
            )
        return len(pending)

    def shutdown(self, wait: bool = False) -> None:
        # RQ workers shut down on their own SIGTERM; nothing to do
        # from the web tier's side.
        return None


def _default_retry():
    """RQ retry policy: 3 attempts, exponential 30s/120s/600s. Beyond
    that the job is marked failed + we rely on the manual-retry admin
    endpoint to kick it back to life."""
    try:
        from rq.retry import Retry
        return Retry(max=3, interval=[30, 120, 600])
    except ImportError:
        return None


def build_runner(*, store, worker_fn, max_workers: int = 1,
                 job_filter=None):
    """Single seam to construct the right runner at app startup.

    Behaviour:
    - If REDIS_URL + rq available → return RQRunner
    - Else → return the existing in-process JobRunner

    The in-process branch keeps the `job_filter` semantics (so the
    GPU worker's "wav2lip-only" filter from A1 still applies). The
    RQ branch doesn't use it because filtering happens via queue
    names ("renders" vs "wav2lip-renders") at the worker side.
    """
    cfg = resolve()
    if cfg.backend == REDIS_BACKEND and cfg.redis_url and cfg.rq_available:
        return RQRunner(
            redis_url=cfg.redis_url,
            store=store, worker_fn=worker_fn,
            max_workers=max_workers,
        )
    # Fall through to the in-process runner. Lazy import so this
    # module doesn't pull in jobs.py just for type-checking.
    from .jobs import JobRunner
    return JobRunner(
        store=store, worker_fn=worker_fn,
        max_workers=max_workers, job_filter=job_filter,
    )
