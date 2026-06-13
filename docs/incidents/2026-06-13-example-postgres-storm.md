# Incident 2026-06-13 — Postgres connection storm during lesson-render burst

> **This is a synthetic example** to demonstrate the post-mortem
> format. No real outage occurred. Real incidents replace this file.

**Severity**: Sev1
**Duration**: 14:22 UTC → 14:41 UTC (19 minutes)
**Affected**: ~340 users hit 5xx on `/api/lessons` and `/dashboard`;
no data loss; no DPDP §10 breach.

---

## Timeline

| Time (UTC) | Event |
|---|---|
| 14:21 | First Sentry event: `psycopg.OperationalError: connection pool exhausted` |
| 14:22 | Uptime Robot pings `/healthz` → 503 |
| 14:23 | Sentry "issue burst" alert fires (Slack) |
| 14:25 | Engineer paged via PagerDuty |
| 14:28 | Engineer SSHs in; logs show 200+ in-flight lesson-render workers, each holding a DB connection |
| 14:31 | Root cause identified: a marketing tweet just went viral → traffic spike + worker pool didn't cap concurrent DB connections |
| 14:33 | Quick fix: bump Postgres `max_connections` from 100 → 200 via Render dashboard |
| 14:35 | Render restarts Postgres; brief 30-second `/healthz` red |
| 14:38 | `/healthz` returns green; new lesson requests start succeeding |
| 14:41 | Sentry event rate drops to pre-incident baseline; declared resolved |

---

## Root cause

`padhai/jobs.py:JobRunner` spawns one worker per incoming lesson
request and each worker opens its own `psycopg` connection without
checking out from a shared pool. Under normal load (10-20 concurrent
lessons), this is fine. The viral spike pushed concurrent renders to
200+, exhausting Postgres `max_connections=100`. Every connection
attempt blocked → cascade failure on every other route.

The lesson-render pipeline DOES use a job queue, but the worker
itself opens an ad-hoc connection rather than using the connection
pool that `padhai/db.py` configures.

---

## What went well

- Sentry alert fired within 1 minute of first error.
- Uptime Robot confirmed external impact within 90 seconds.
- The `/healthz` endpoint returned proper `db_status: error` —
  Render's auto-restart logic could have triggered automatically
  with a more aggressive healthcheck threshold.
- Recovery was a config change, no code deploy needed.

## What went badly

- We had no concurrent-worker cap. Anyone with a viral tweet could
  DOS us by sending traffic.
- No load-test exists for the lesson-render pipeline. We had no
  way to know our actual breaking point.
- PagerDuty took 3 minutes from alert → human notification. The
  default escalation policy is too slow for Sev1.
- Postgres `max_connections=100` is the Render Starter default.
  We should have bumped it during the prod-118 deploy.

## Action items

- [ ] **Tracked**: cap concurrent lesson workers at 50 via
      `JobRunner` config (engineering, 1 day). [#issue-tbd]
- [ ] **Tracked**: route lesson-render workers through the shared
      `padhai/db.py` connection pool (engineering, 2 days).
      [#issue-tbd]
- [ ] **Tracked**: add a synthetic load test that fires 100 concurrent
      lesson requests in CI nightly (testing, 1 day). [#issue-tbd]
- [ ] **Tracked**: bump Postgres `max_connections` to 300 as
      baseline in `db/changesets/`. [#issue-tbd]
- [ ] **Tracked**: PagerDuty escalation policy → 30-second
      acknowledgement window before bumping to backup (ops, 30
      minutes). [#issue-tbd]
- [ ] **Tracked**: document the workers-vs-connections pattern
      in `padhai/jobs.py` so future engineers don't recreate this
      (docs, 30 min). [#issue-tbd]

---

## Cost of incident

| Item | Amount |
|---|---|
| Users affected | ~340 |
| Refunds issued | 0 (no paying users were materially harmed) |
| Engineering hours to triage + fix | 1.5 |
| Sentry events consumed | ~3,200 (free tier still has runway) |
| Reputational hit | Small — outage was < 20 min, never trended on Twitter |

---

## Lessons (in order of leverage)

1. **Default config values are not "production-ready"** even on
   a managed host. Audit them before launch, not after.
2. **The honest gap analysis from the 100-sprint retrospective
   missed connection pooling**. Add it to ONBOARDING.md invariants
   so the next engineer doesn't repeat.
3. **Viral spike scenarios are real** at our scale. Plan for 10×
   sudden traffic, not 1.5× steady.
