# G1 — SQLite → Postgres migration runbook

The v1.0 stack runs on SQLite. SQLite serializes writes — every video
request → job insert blocks every other write. At ~50 concurrent
renders, the queue stalls. v2.0's first job is getting off SQLite
and onto managed Postgres.

This runbook is the **operational plan** for that cutover. v1.1 ships
the precursor scaffolding (`padhai/db_backend.py`) so future modules
can target both engines. The actual cutover is an ops sprint —
1-2 weeks of engineering plus a maintenance window — and lives in
a dedicated v1.1.x release per this doc.

## Tier choice: Neon

**Recommendation: Neon serverless Postgres, AP-South-1 (Mumbai).**

Tradeoff matrix:

| Provider | Pros | Cons | Cost @ 4GB RAM |
|---|---|---|---|
| Neon | Serverless scale-to-zero, free pgvector, branching | Newer (founded 2021); fewer India SREs | ~$30/mo |
| Render Postgres | Already on Render; no new vendor | Expensive at scale, no scale-to-zero | ~$45/mo |
| Supabase | Built-in auth + storage we could rip out admin app | Heavier package than we need | ~$25/mo |
| AWS RDS Mumbai | Battle-tested, India residency clear | Operational overhead (we'd need a DBA) | ~$70/mo + ops |

**Why Neon.** Serverless billing matches our spiky pattern (school-day
peaks, off-hours zero). Branching gives us per-PR preview databases.
pgvector is in-built (zero-setup), unblocking `curriculum_index` in
J3. India region (`ap-south-1`) covers DPDP data-residency for H4.

## Architecture

```
                  ┌──────────────────┐
                  │   padhai web     │
                  │   (FastAPI)      │
                  └────────┬─────────┘
                           │
                  padhai/db_backend.py
                  (engine selector)
                           │
            ┌──────────────┴──────────────┐
            ▼                             ▼
    ┌──────────────┐              ┌──────────────┐
    │   SQLite     │              │   Postgres   │
    │   (default,  │              │   (Neon, set │
    │   dev, fresh │              │   via         │
    │   sandboxes) │              │   DATABASE_URL) │
    └──────────────┘              └──────────────┘
```

Selection logic:
- `DATABASE_URL=postgres://...` → use Postgres
- `DATABASE_URL=sqlite:///...` or unset → use SQLite
- (Future: `DATABASE_URL=postgres://...?backend=dual` for dual-write
  during cutover; not in v1.1.)

## Cutover plan (the actual migration, ~2 week sprint)

Five phases, dual-write throughout. Zero application downtime;
maintenance window only for the read-cutover step.

### Phase 1: Dual-write (week 1)

Every write path that today INSERTs to SQLite gets a second INSERT to
Postgres after the SQLite commit succeeds. Reads stay on SQLite.
Postgres is a shadow.

Implementation:
- Wrap `_conn()` returns in a `DualConn` that runs the SQL against
  SQLite first, then schedules an async retry-on-failure write to
  Postgres
- Postgres write failures get queued in `outbox` table, retried by a
  background worker; if a retry permanently fails, Sentry alerts

Per-module work (estimated days):
- `padhai/jobs.py` (1d) — highest-traffic table; nail this first
- `padhai/auth.py` + `padhai/orgs.py` (1d) — user/org join
- `padhai/branding.py`, `padhai/dpdp.py`, `padhai/sso.py`,
  `padhai/audit.py` (1d) — small, additive tables
- `padhai/schema_v2.py` (1d) — 4 tables but lower traffic
- `padhai/attendance.py`, `padhai/exams.py`, `padhai/fees.py`,
  `padhai/notifications.py`, etc. (2d) — the E1-E9 surface

End of Phase 1: every write is in both stores; Postgres is ~consistent
with SQLite within 60s under normal load.

### Phase 2: Backfill (week 1, parallel)

Run a one-off Python script that paginates every SQLite table and
upserts into Postgres. Idempotent on primary keys; safe to re-run.

```bash
PADHAI_DB_PATH=/var/data/jobs.db \
DATABASE_URL=postgres://... \
python -m padhai.tools.backfill_postgres --table jobs --chunk 1000
```

Backfill order matters for foreign-key constraints — but our schema
doesn't yet enforce FKs in Postgres (we'll add them in Phase 5), so
backfill order is just "small tables first" for monitoring sanity.

Verify with a row-count cross-check:
```bash
python -m padhai.tools.verify_postgres
# Outputs: per-table {sqlite_count, postgres_count, delta}
# All deltas should be 0 (or small + bounded by dual-write lag)
```

### Phase 3: Read-shadow (week 2)

Reads still hit SQLite as primary, but for a 10% sample we also fire
the same query against Postgres and log diff results to a
`read_shadow_diffs` table. Goal: catch query-translation bugs
(SQLite-specific syntax, JSON1 functions, etc.) before they hit
production reads.

Run this for 48-72 hours. Expected diffs: zero. Any non-zero diff
gets root-caused before Phase 4.

### Phase 4: Cutover (week 2, maintenance window)

30-minute window, off-hours:
1. Drain the in-process worker (let queued jobs finish)
2. Stop accepting new writes (return 503 for ~10 minutes)
3. Final delta backfill (catches the ~last 60s of dual-write lag)
4. Flip the env: `DATABASE_URL=postgres://...` for all replicas
5. Re-run schema_v2.migrate(), branding.migrate(), etc. in Postgres
   so any leftover IF-NOT-EXISTS conditions clear
6. Resume traffic
7. Monitor for 30 min — query latency, error rate, queue depth

Rollback plan: revert the env var to SQLite. The dual-write was still
active, so SQLite is at most 60s behind. Worst case, ~60s of
Postgres-only writes are lost. Acceptable for an emergency rollback.

### Phase 5: Hardening (week 2-3)

- Add FK constraints to Postgres (deferred in dual-write to avoid
  ordering pain)
- Convert TEXT-encoded JSON columns (`payload`, `profile_json`) to
  proper JSONB; add GIN indexes on commonly-queried JSON paths
- Drop the dual-write hooks; SQLite becomes a read-only archive for
  90 days then deleted
- Enable Neon read replicas in Singapore (precursor to G4)

## Rollback decision tree

| Scenario | Action |
|---|---|
| Postgres write rate >2× SQLite during dual-write | Slow down; investigate slow queries |
| Verify-postgres delta keeps growing | Pause backfill, debug; usually a missing index or a `RETURNING` clause bug |
| Phase 3 read-shadow shows diffs | Fix the query translation BEFORE Phase 4 |
| Phase 4 cutover error rate >0.1% in first 5 min | Roll back env var, debug from logs |
| Phase 5 FK violation on add-constraint | Backfill orphan rows or NULL them; never delete |

## What v1.1 ships (precursor only)

This release adds **`padhai/db_backend.py`** — the engine selector
that future modules can target. It currently always returns SQLite
because the application code uses module-local `_conn()` helpers
(per the additive-migration pattern that's served us well from v0.6
onward). That stays for now.

What the abstraction unlocks:
1. Configuration plumbing (`DATABASE_URL` parsing) is in place when
   the migration sprint kicks off
2. Future modules (audit log, J3 curriculum scorer) can target the
   abstraction from day one — no retrofit needed for them
3. Tests can run against either engine just by setting an env var

What's deliberately NOT in v1.1:
- Per-module ports of existing `_conn()` helpers
- Actual psycopg dependency in `requirements.txt` (added when ops
  provisions the Neon instance)
- Alembic scaffolding (added with the per-module ports; otherwise
  it's premature)

## Budget + timeline

- **Neon Pro tier (~$30-50/mo)** for the first 6 months
- **2-week engineering sprint** for the cutover
- **1 hour maintenance window** for Phase 4
- **3-month parallel SQLite retention** for emergency rollback
- **External consultant audit (~$2000)** of the cutover plan before
  Phase 1 starts — cheap insurance against a data-loss bug at scale

When ops is ready to provision Neon, ping me — I'll add psycopg to
`requirements.txt`, write the `padhai/tools/backfill_postgres.py`
script, and pair on Phase 1.
