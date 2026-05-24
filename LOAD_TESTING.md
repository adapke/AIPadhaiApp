# G6 — Load testing + capacity planning

`scripts/loadtest_locustfile.py` is a Locust harness exercising the
v1.x hot endpoints at sustained concurrency. This doc covers how to
run it, what the SLOs are, and how to read the results.

## Quick start

```bash
pip install locust
locust -f scripts/loadtest_locustfile.py \
       --host http://localhost:8000 \
       --headless --users 200 --spawn-rate 20 --run-time 60s \
       --csv=/tmp/padhai_loadtest
```

Exit code 0 → passed SLOs. Non-zero → CI gate fails (see
`_enforce_thresholds` at the bottom of the locustfile).

## SLO targets

| Metric | Target | Current pilot |
|---|---|---|
| Failure ratio | <0.1% | TBD — run baseline once Postgres lands |
| p95 latency (all endpoints) | <1500ms | TBD |
| p99 latency, `/api/v2/video-requests/{id}/status` | <800ms | TBD |
| Sustained concurrency (browse-heavy mix) | 1000 users | Single web replica handles ~150 |

The numbers above are **post-G1/G2 targets**. Today's SQLite +
in-process queue tops out at ~150 concurrent users on a single
Render replica; horizontal scaling helps for reads but writes
serialize on the SQLite file lock.

## Test scenarios

### Browse-heavy (default, 80% of synthetic traffic)
`BrowsingUser` — exercises the SPA shell, manifest, service worker,
video-mode catalog, branding resolution, curriculum index, health.
Mostly cache-friendly + small JSON. This is the "student opens the
app, scrolls through library" pattern.

### Video-heavy (20%)
`VideoUser` — polls status / result on pre-existing job_ids passed
via `PADHAI_LOADTEST_JOB_IDS`. We **don't** submit real renders
against staging by default — that burns ~₹3/render in Anthropic +
TTS costs.

To exercise the full submit→render→deliver path:

```bash
PADHAI_LOADTEST_JOB_IDS="job_abc,job_def,job_ghi" \
locust -f scripts/loadtest_locustfile.py ...
```

The job IDs must be real, completed jobs in the target environment
(usually 3-5 cached lessons that exercise every render mode +
language).

## Reading the output

Locust writes:
- `/tmp/padhai_loadtest_stats.csv` — per-endpoint counts + latency
  percentiles
- `/tmp/padhai_loadtest_stats_history.csv` — sampled every 10s for
  the time-series plot
- `/tmp/padhai_loadtest_failures.csv` — per-failure details

Key columns:

| Column | Use |
|---|---|
| `99%` | p99 latency — the SLO line for premium UX |
| `Average response size` | gzip working? bundle size growing? |
| `Failure Count` | should be 0 for the duration |
| `Requests/s` | throughput; matches up against worker fleet size |

## Capacity planning matrix

After the G1 + G2 cutover lands, run this matrix and update the table
in `ROADMAP_V2.md` G6:

| Web replicas | Worker replicas | Postgres tier | Sustained users | Cost/mo |
|---|---|---|---|---|
| 1 × 0.5 vCPU | 1 × 1 vCPU | Neon hobby | 200 | ~₹2000 |
| 3 × 0.5 vCPU | 3 × 1 vCPU | Neon pro | 1000 | ~₹6000 |
| 5 × 0.5 vCPU | 10 × 1 vCPU | Neon scale | 5000 | ~₹20000 |
| 10 × 1 vCPU | 30 × 1 vCPU | Neon scale + read replicas | 25000 | ~₹80000 |

(Linear scale up to ~25k concurrent; beyond that we re-evaluate the
DB tier + add read-replica routing per G4.)

## What v1.2 ships

The Locust file + this doc. Actually running it against staging
(populating the SLO column above with real numbers) is an ops task
that happens after the v1.2 deploy + the v1.3 enterprise tier
cutover, because that's when we'll have enough load to make the
numbers meaningful.

## What v1.2 doesn't ship

- A CI workflow that runs the load test on every PR — too noisy at
  current scale, defer to v1.4 when we have a dedicated staging
  environment
- Distributed load generation (Locust master+workers across multiple
  IPs) — single-instance Locust handles 1000+ users; multi-node is
  for 10k+ which is post-mobile-launch
- Chaos testing (kill Redis mid-run, restart a worker) — separate
  hardening sprint
