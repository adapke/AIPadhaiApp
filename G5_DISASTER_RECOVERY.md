# G5 — Disaster Recovery + Backup Strategy

SOC 2 Type 1 (H6) requires a documented DR plan with evidence of
quarterly restore drills. G5 ships the automation + runbook.

## RTO / RPO targets

| Tier | RTO (recovery time) | RPO (data loss tolerance) |
|---|---|---|
| Web tier (FastAPI replicas) | 5 min | 0 — stateless |
| Postgres (Neon) | 1 hour | 5 min — point-in-time recovery |
| Object storage (R2) | 4 hours | 24 hours — daily cross-region replication |
| Worker fleet | 15 min | 0 — jobs queued in Redis, replays cleanly |

These are committed numbers — every quarterly drill must hit them
or we file an incident report.

## Backup automation

### Postgres → Neon native + S3 cold tier

Neon snapshots automatically every hour for the last 7 days; we
push a daily snapshot to S3 Glacier in us-east-1 for the 90-day
archive:

```bash
# Cron job at 02:00 IST daily (defined in render.yaml)
neon_snapshot_to_s3:
  command: |
    pg_dump $DATABASE_URL --format=custom --compress=9 \\
      | aws s3 cp - s3://padhai-backups-glacier/postgres/$(date +%Y%m%d).pg
  schedule: "0 20 * * *"  # 02:00 IST = 20:30 UTC the previous day
```

Cross-region: us-east-1 (independent from ap-south-1 where our
primary lives) — protects against AWS-level Mumbai region failure.

### R2 → R2 cross-region replication

Cloudflare R2 supports per-bucket replication (introduced 2025).
Configure in the dashboard:
- Primary bucket: `padhai-media` (AP-South-1)
- Replica bucket: `padhai-media-replica` (EU-CENTRAL-1)
- Replication: async, ~5 min lag

### Configuration backups

`render.yaml` + this repo are themselves the configuration. Git
history is the change log; pushing to `main` is the change-control
gate. No additional backup needed — GitHub provides 99.95% SLA on
the source.

## Restore procedures

### Scenario A: Postgres corruption / DROP TABLE accident

1. Stop new writes (set `MAINTENANCE_MODE=1` env var)
2. Neon dashboard → Branches → Restore from point-in-time, 5 min
   before the destructive event
3. Verify row counts on the affected tables match expectations
4. Resume traffic
5. File incident report — root cause + 7-day evidence trail

### Scenario B: Neon AP-South-1 entirely down

1. Restore from the latest S3 Glacier snapshot to a fresh Neon
   project in EU or US:
   ```bash
   aws s3 cp s3://padhai-backups-glacier/postgres/20260601.pg .
   pg_restore --dbname=$NEW_DATABASE_URL 20260601.pg
   ```
2. Flip `DATABASE_URL` env in Render
3. Worker fleet picks up new endpoint on next replica restart
4. Expect 1-hour data loss (yesterday's late edits) — acceptable
   per RPO

### Scenario C: R2 region down

R2 replication handles this transparently. Flip the bucket env var:
```
S3_BUCKET=padhai-media          → S3_BUCKET=padhai-media-replica
S3_ENDPOINT_URL=...ap-south-1   → S3_ENDPOINT_URL=...eu-central-1
```

Existing URLs in clients (signed URLs from CDN) keep working since
the CDN edge fetches from whichever origin is configured at the
proxy layer.

### Scenario D: Single corrupted video / lesson

Most common in practice. Don't trigger DR — just regenerate the
video via the admin "retry job" endpoint. Cache layer handles
idempotency.

## Quarterly drill

Run every Q1 in production. The drill itself is a 2-hour window;
prep takes a day:

1. **Day -7**: Schedule drill, notify ops + on-call
2. **Day 0, T-30 min**: Take final pre-drill snapshot
3. **T+0**: Trigger the chosen scenario (we rotate through A-D)
4. **T+60min**: Execute restore. Measure actual RTO.
5. **T+90min**: Verify integrity (row counts, sample queries,
   smoke test the SPA)
6. **T+120min**: Resume normal traffic, file drill report

Drill report fields (auditor evidence):
- Scenario simulated
- Started: <timestamp>
- Restore complete: <timestamp>
- RTO actual: <minutes>
- Data loss observed: <rows / seconds>
- Issues encountered: <text>
- Action items: <text>

## What v2.0 ships (code)

- `padhai/dr.py` — small helper that reads backup metadata + exposes
  it on the admin dashboard
- This runbook
- `render.yaml` cron job for nightly Postgres → Glacier

## What v2.0 doesn't ship (ops)

- Actual S3 Glacier bucket provisioned (~₹500/mo cold storage)
- R2 cross-region replication enabled (~₹200/mo replica cost)
- Drata or Vanta integration for evidence-collection automation
- First quarterly drill executed
