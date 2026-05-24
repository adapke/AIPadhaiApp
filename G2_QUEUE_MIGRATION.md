# G2 — In-process → Redis-RQ queue migration runbook

The v1.0 stack runs renders through a `JobRunner` thread pool living
inside each FastAPI web replica. Fine for the current pilot scale
(<50 concurrent renders) but it ties web sizing to render
sizing — every web replica reserves CPU for the worst-case render
burst, even when serving cached pages.

v1.2 introduces the abstraction; this doc is the cutover plan.

## Topology after cutover

```
            ┌─────────────────────┐
            │  web replicas       │  3-5x 0.25 vCPU
            │  (CPU, tiny)        │  scale on req/s
            └──────────┬──────────┘
                       │  enqueue
                       ▼
             ┌────────────────────┐
             │   Redis            │  Upstash serverless
             │   (renders +       │  (pay-per-op)
             │    wav2lip-renders)│
             └─────┬──────┬───────┘
                   │      │
                   ▼      ▼
        ┌─────────────┐  ┌─────────────────┐
        │ CPU workers │  │ GPU workers     │
        │ 3-30x       │  │ Modal A10G      │
        │ (autoscale  │  │ 1-3x (autoscale │
        │  on queue   │  │  on queue depth)│
        │  depth)     │  │                 │
        └─────────────┘  └─────────────────┘
```

## Vendor choice: Upstash + python-rq

**Redis: Upstash, AP-South-1 (Mumbai).** Free tier covers 10k
commands/day; pay-as-you-go after that. Switch to Render Redis if
sustained load >50k commands/day (~₹2000/mo cheaper at that scale).

**Queue library: python-rq** (not Celery, not Arq).
- RQ is small (~3000 LOC) and reads like ordinary Python — important
  for the on-call team
- Celery is a heavier framework; we'd use <10% of its surface
- Arq is async-native but FastAPI's request handlers already do the
  sync→async dance; sticking with RQ keeps the worker shape identical
  to today's thread-pool runner

## What v1.2 ships (the abstraction)

- `padhai/queue_backend.py` — selector that returns either
  `RQRunner` (when `REDIS_URL` is set + `rq` is installed) or the
  existing in-process `JobRunner`
- `padhai/worker_entrypoint.py` — single command (`python -m
  padhai.worker_entrypoint`) that boots a worker in the right mode

What's NOT in v1.2:
- `rq` + `redis` in requirements.txt (added in the cutover sprint)
- Web tier's `runner = JobRunner(...)` flipped to
  `runner = queue_backend.build_runner(...)` (one-line change, lands
  on the cutover branch)

## Cutover plan (1-week sprint)

### Day 1 — Provision

- Upstash account, create Redis DB in `ap-south-1`
- Copy connection URL into Render env: `REDIS_URL=rediss://...`
- Add to `requirements.txt`: `rq>=1.16`, `redis>=5.0`
- Verify in a shell:
  ```bash
  python -c "from padhai import queue_backend; print(queue_backend.description())"
  # → redis-rq://upstash-host:6379/0
  ```

### Day 2 — Worker container

- New Render service: `padhai-worker` from the same Docker image as
  `padhai-web` but with `command: python -m padhai.worker_entrypoint`
- Scaling rule: 1-10 replicas, scale up when Redis queue depth >5
  for 60s
- Verify health: `curl -I /healthz` on the worker exposes
  RQ's last-heartbeat timestamp

### Day 3 — Flip the web tier

- Web tier `runner = JobRunner(...)` → `runner = queue_backend
  .build_runner(...)`
- The `build_runner()` selector reads `REDIS_URL` at import time
- Deploy. Web tier now enqueues to Redis; renders are claimed by
  the worker service.
- Roll back: unset `REDIS_URL` env var. Web tier falls back to
  in-process JobRunner on next deploy. Nothing else changes.

### Day 4 — GPU worker on its own queue

- Modal deploy (`modal_deploy.py`) — update env so the GPU worker
  listens on `wav2lip-renders` queue: `PADHAI_WORKER_QUEUE=wav2lip-renders`
- Web tier's enqueue path uses `_payload_targets_wav2lip()` (already
  in `web.py`) to pick the right queue name
- Verify by enqueuing one M3-tier render and watching it land on
  the GPU worker, not the CPU fleet

### Day 5 — Monitoring + perf

- Render dashboards wired to:
  - Redis queue depth per queue name (alert if `renders` >100
    for 5 min)
  - Worker error rate (alert if >0.5% over 10 min)
  - p95 worker latency per render mode
- Run the v1.2 load tests (G6) and confirm throughput scales
  linearly with worker replica count
- Sign-off on the SLOs:
  - p50 enqueue→start: <2s
  - p95 5-min render end-to-end: <90s
  - Queue depth p99 over a school day: <50

## Rollback decision tree

| Symptom | Action |
|---|---|
| Worker pod restarts every <60s | Check Redis connection limits (Upstash free tier caps at 100 concurrent) |
| Jobs stuck in 'queued' >5 min | Check worker replicas are running + `rq info` shows active worker |
| Web tier 503ing on enqueue | Redis unreachable — flip `REDIS_URL` to empty, web falls back to in-process |
| Same job runs twice | Idempotency bug — check `dispatch_one` early-return when `status in ('done','failed')` |
| GPU jobs landing on CPU queue | Check `_payload_targets_wav2lip()` + per-queue enqueue logic |

## Cost model

Per Render:
- 3-5 web replicas × ₹400/mo each = ₹1200-2000
- 1-10 CPU workers × ₹600/mo each (only when active) ≈ ₹3000 average
- 1 GPU worker (Modal A10G, ~30% utilisation) ≈ ₹4000
- Upstash Redis: free tier covers up to ~10k schools; pay-as-you-go
  ~₹500/mo at 1k schools active daily

Total ≈ ₹8000-12000/mo at the current pilot scale. Linear scale-up
to ~10k schools without architectural change.

## What v1.2 doesn't ship (deferred to v1.2.x / v1.3)

- Per-tenant fairness (one school's bulk-render shouldn't starve
  another's interactive request) — needs a custom RQ scheduler
- Dead-letter queue with admin retry UI — admin app already has
  per-job retry, this is the bulk version
- Priority queues (M3 paid users get a fast lane) — RQ supports it,
  we just haven't wired the tier signal through
- Worker auto-scaling via custom KEDA scaler on queue depth (Render
  Workers don't expose Redis-depth scaling natively yet; for now we
  set 3-10 replicas + accept the over-provisioning at off-peak)
