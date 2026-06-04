"""G2 — Distributed worker entrypoint.

Single command to boot a render worker against the shared queue:

    python -m padhai.worker_entrypoint

Selects between two modes via `REDIS_URL`:

- REDIS_URL set → spawn an `rq` worker, listen on the `renders` queue
  (and optionally `wav2lip-renders` for GPU workers)
- REDIS_URL unset → run the in-process JobRunner in foreground mode
  (the v0.6 → v1.1 path; this exists for parity + local dev)

Container topology after the G2 cutover:

  web replicas (CPU, 0.25 vCPU each)
      ↓ enqueue
  Redis (Upstash or Render Redis)
      ↓ dispatch
  CPU worker fleet (3-30 replicas, autoscale on queue depth)
      + GPU worker (1-3 Modal A10G replicas, listens on wav2lip-renders)

The GPU worker path is already wired (`padhai/gpu_worker.py` +
`modal_deploy.py`); G2 just makes the CPU side match.
"""

from __future__ import annotations

import argparse
import os
import signal
import sys
import time

from . import jobs as _jobs
from . import queue_backend


def dispatch_one(job_id: str) -> dict:
    """Called by RQ workers (and by the in-process fallback) to render
    a single job. Looks up the row, runs the worker_fn, writes the
    result back. Failures bubble up to RQ which records them as a
    Failed-Job and applies the retry policy.

    This module is the import target for `rq.Queue.enqueue("padhai.
    worker_entrypoint.dispatch_one", job_id)` — keeping it small +
    side-effect-free at import time matters for RQ's worker
    fork-on-each-job mode.
    """
    # Lazy import — pulls in the heavy render/talking_head deps only
    # inside the worker process, not in the web tier when this module
    # is imported for type/contract reasons.
    from .web import _render_worker, store  # noqa: WPS433
    job = store.get(job_id)
    if not job:
        raise RuntimeError(f"job {job_id!r} not found in store")
    if job.status not in ("queued", "running"):
        # Idempotency: RQ might re-dispatch the same job_id after a
        # transient redis failure. If the row says it's done, skip.
        return job.result or {}
    store.claim(job_id)
    try:
        result = _render_worker(job)
        store.update(job_id, status="succeeded", result=result)
        return result
    except Exception as e:  # noqa: BLE001
        store.update(job_id, status="failed", error=repr(e))
        raise


def _run_rq_worker(*, queue_name: str, redis_url: str) -> int:
    import redis as _redis
    from rq import Connection, Queue, Worker

    conn = _redis.from_url(redis_url)
    queue = Queue(name=queue_name, connection=conn)
    with Connection(conn):
        worker = Worker([queue])
        print(f"[worker] listening on '{queue_name}' at {redis_url}")
        worker.work(with_scheduler=False, burst=False)
    return 0


def _run_inprocess_worker() -> int:
    """v1.1 fallback. Spins up the same in-process Runner the web tier
    uses, but in a standalone process — useful for local-dev parallel
    workers without Redis.
    """
    from .web import runner
    # The web-tier startup already calls resume_pending. Sleep until
    # SIGTERM; the thread pool inside the runner keeps draining.
    print("[worker] in-process mode (REDIS_URL unset). Ctrl-C to exit.")
    stop = {"flag": False}

    def _sigterm(*_args):
        stop["flag"] = True

    signal.signal(signal.SIGTERM, _sigterm)
    signal.signal(signal.SIGINT, _sigterm)
    try:
        while not stop["flag"]:
            time.sleep(1)
    finally:
        runner.shutdown(wait=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="padhai render worker — RQ-backed when REDIS_URL set, "
                    "in-process otherwise.",
    )
    parser.add_argument(
        "--queue", default=os.environ.get("PADHAI_WORKER_QUEUE", "renders"),
        help="RQ queue name to listen on (default: 'renders'). Use "
             "'wav2lip-renders' for the GPU worker.",
    )
    args = parser.parse_args(argv)

    cfg = queue_backend.resolve()
    print(f"[worker] queue backend: {queue_backend.description()}")

    if cfg.backend == queue_backend.REDIS_BACKEND and cfg.redis_url:
        return _run_rq_worker(queue_name=args.queue, redis_url=cfg.redis_url)
    return _run_inprocess_worker()


if __name__ == "__main__":
    sys.exit(main())
