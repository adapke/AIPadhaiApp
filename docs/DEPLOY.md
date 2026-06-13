# Deploy — first APP_ENV=production push

The codebase is production-ready; this doc is the literal sequence
of steps to get it live on a Linux host with HTTPS. Three hosting
options below — pick one before reading the rest.

> **Prereq**: `make all-verify` is green locally. `make security` is
> green locally. If either is red, fix that first — production
> isn't the place to discover regressions.

---

## 0. Decide hosting (pick one before §1)

| Option | Cost / mo | Setup time | Trade-off |
|---|---|---|---|
| **Render** | $7-25 (web) + $7-15 (Postgres) | ~30 min | Managed, autoscale, easy SSL. Good default. |
| **Modal** | Pay-per-second compute | ~60 min | Serverless; cheap for spiky traffic. Job pipeline benefits most. |
| **Spot EC2 / Hetzner** | $5-20 | ~2-4 hr | Cheapest; you babysit OS updates, certbot, monitoring. |

For first launch: **pick Render**. Migrate to Spot once you know
your usage shape and the spot-bootstrap script (`ops/spot-bootstrap.sh`)
won't bite you.

---

## 1. Provider keys — collect all of them BEFORE deploy

Walk through these in parallel — each is its own ~20-min signup +
verification flow. Doing them serially turns deploy day into a week.

| Provider | Walkthrough | What you need |
|---|---|---|
| Anthropic Claude | (already done) | `ANTHROPIC_API_KEY` |
| Postgres | Render/Neon/AWS RDS | `DATABASE_URL` with `?sslmode=require` |
| Object storage | Cloudflare R2 (or S3) | `S3_BUCKET` + `S3_ENDPOINT_URL` + `AWS_ACCESS_KEY_ID` + `AWS_SECRET_ACCESS_KEY` |
| Razorpay | [`docs/RAZORPAY.md`](RAZORPAY.md) | `RAZORPAY_KEY_ID` + `RAZORPAY_KEY_SECRET` + `RAZORPAY_WEBHOOK_SECRET` (test mode first) |
| SMTP | [`docs/SMTP.md`](SMTP.md) | `SMTP_HOST` + `SMTP_PORT` + `SMTP_USER` + `SMTP_PASSWORD` + `SMTP_FROM` |
| Sentry | [`docs/SENTRY.md`](SENTRY.md) | `SENTRY_DSN` + `PADHAI_SENTRY_TEST_TOKEN` |
| PostHog | [`docs/POSTHOG.md`](POSTHOG.md) | `POSTHOG_API_KEY` + `POSTHOG_HOST` |
| TTS (optional) | Bhashini / ElevenLabs | `BHASHINI_API_KEY` / `ELEVENLABS_API_KEY` |
| Photoreal avatar (optional) | HeyGen / D-ID / Tavus | `HEYGEN_API_KEY` etc. |

**Generate the JWT secrets fresh** (do NOT reuse dev):

```bash
python -c "import secrets; print('PADHAI_JWT_SECRET=' + secrets.token_urlsafe(48))"
python -c "import secrets; print('ADMIN_JWT_SECRET=' + secrets.token_urlsafe(48))"
python -c "import secrets; print('PADHAI_SENTRY_TEST_TOKEN=' + secrets.token_urlsafe(32))"
```

Add `PADHAI_SUPERUSER_EMAILS=you@your-org.com` (comma-separated) so
the admin gate has a non-DB allow-list before any users are seeded.

---

## 2. DNS + SSL

Buy a domain (Namecheap / Cloudflare Registrar / Google Domains).

Cloudflare is the cheapest path:

1. Add the domain to Cloudflare.
2. Replace nameservers at your registrar with Cloudflare's pair.
3. Wait 5-30 min for propagation; verify with `dig NS yourdomain.com`.
4. Add a CNAME `app.yourdomain.com` → your Render/Modal/host URL.
5. Cloudflare SSL/TLS → mode "Full (strict)" if your host has a
   real cert (Render does), "Flexible" only as a temporary fallback.

Verify HTTPS:

```bash
curl -I https://app.yourdomain.com/healthz
# → HTTP/2 200 + Server: cloudflare or similar
```

---

## 3. Render-specific deploy (recommended for first launch)

### 3.1 Postgres first

1. Render dashboard → New + → PostgreSQL.
2. Pick `Free` ($0) to start, `Starter` ($7) for first paying user.
3. Region: closest to your users. India users → Singapore or Frankfurt.
4. After provisioning, copy the **Internal Database URL**.

### 3.2 Run liquibase migrations

```bash
# Apply the changesets that ship in db/changesets/
liquibase \
  --url="$RENDER_PG_INTERNAL_URL" \
  --changeLogFile=db/changesets/master.xml \
  update
```

This creates `users`, `lessons`, `jobs`, etc. on the fresh Postgres
instance. Idempotent; safe to re-run.

### 3.3 Run the curriculum + concept-video seeds

These don't ship as liquibase changesets (data, not schema):

```bash
PYTHONPATH=. DATABASE_URL=$RENDER_PG_URL python scripts/seed_curriculum_topics.py
PYTHONPATH=. DATABASE_URL=$RENDER_PG_URL python scripts/build_concept_videos.py
```

### 3.4 Web service

1. Render dashboard → New + → Web Service.
2. Repo: your fork of AIPadhaiApp.
3. Runtime: Docker.
4. Dockerfile path: `Dockerfile` (the main one, not `Dockerfile.dev`).
5. Plan: `Starter` ($7) — `Free` sleeps after 15 min idle which
   breaks the parent-consent flow.
6. Environment variables: paste every value from §1 + §2 + this
   list:

   ```bash
   APP_ENV=production
   APP_BASE_URL=https://app.yourdomain.com
   DATABASE_URL=<from §3.1>
   PADHAI_JWT_SECRET=<from §1>
   ADMIN_JWT_SECRET=<from §1>
   ANTHROPIC_API_KEY=<your key>
   PADHAI_SUPERUSER_EMAILS=you@your-org.com
   # + all provider keys from §1
   ```

7. Custom domain → `app.yourdomain.com`. Render auto-provisions a
   Let's Encrypt cert (cert visible in the dashboard after ~2 min).

### 3.5 First-boot validation

```bash
# Must return 200 with `db_status: ok` and a real git_sha
curl https://app.yourdomain.com/healthz

# Must refuse with 401 (anonymous can't hit admin endpoints in prod)
curl https://app.yourdomain.com/admin/health
# → 401

# Sentry test fire
curl https://app.yourdomain.com/__sentry_test \
  -H "X-Sentry-Test-Token: $PADHAI_SENTRY_TEST_TOKEN"
# → 500 + event in Sentry within 30s
```

If `db_status: error` — check that `DATABASE_URL` is the *internal*
URL (Render Postgres has separate internal + external; web service
must use internal).

---

## 4. Modal-specific deploy (alternate)

For job pipelines (lesson rendering) Modal is cheaper than running
a 24/7 web worker:

```bash
modal deploy modal_deploy.py
```

The script imports the FastAPI app and exposes it under a Modal HTTPS
endpoint. Set the same env vars as §3.4 via Modal's secrets UI.

Caveat: Modal has cold-start (~2-3s on first request after idle).
For a student-facing front door, that's noticeable. Use Modal for
the worker pipeline + Render/Spot for the web tier.

---

## 5. Spot EC2 / Hetzner deploy (cheapest, most ops)

`ops/spot-bootstrap.sh` + `ops/spot-launch.py` provision a spot
instance with:

- Python 3.13
- Postgres 15
- Nginx + Certbot
- The repo cloned + `pip install -e .`
- Systemd unit for the uvicorn worker

```bash
# From your laptop (requires AWS creds):
python ops/spot-launch.py --region us-east-1 --instance-type t4g.small
```

You then SSH in, fill `/etc/padhai/.env`, run `systemctl restart padhai`,
and certbot for SSL.

This is the cheapest option (~$5/mo) but you own OS updates, log
rotation, monitoring. Only pick this if you've run a production
Linux server before.

---

## 6. Post-deploy smoke test

After §3.5 / §4 / §5 — run the smoke spec:

```bash
# Run the Cypress smoke against the production URL (read-only — does
# NOT create real users / payments)
CYPRESS_BASE_URL=https://app.yourdomain.com npx cypress run \
  --spec cypress/e2e/01-health.cy.js
```

If it passes, browse to `https://app.yourdomain.com/` and try:
- Sign up with a real email (your own — verifies SMTP)
- Generate one lesson (verifies Anthropic key + job pipeline)
- Buy M2 tier on test-mode Razorpay (verifies payment flow)
- Open `/admin/health` as the superuser (verifies admin gate)

---

## 7. Day-2 ops (cron)

On the production host, add the nightly ops cron entry:

```cron
23 3 * * * cd /opt/padhai && \
    PADHAI_DB_PATH=/var/lib/padhai/jobs.db \
    AUTO_DEMOTE=1 \
    /opt/padhai/scripts/nightly_ops.sh \
    >> /var/log/padhai-nightly.log 2>&1
```

This runs at 03:23 UTC nightly:
- SQLite online backup (kept 14 days by default)
- Iframe-health check on verified concept videos (demotes broken
  rows back to channel_seed automatically)
- Stats snapshot JSON for ops alerting

See [`scripts/nightly_ops.sh`](../scripts/nightly_ops.sh) for env knobs.

For Postgres-backed deploys, also set up:
- `pg_dump` nightly backup (your provider may do this for you)
- Disk-usage alerts (Render does this; spot doesn't)
- Sentry release tagging — set `RENDER_GIT_COMMIT` env var or
  push a Sentry release on each deploy

---

## 8. First-week checklist

| Day | Task |
|---|---|
| 0 (launch) | All of §1-§7 |
| 1 | Watch Sentry for unexpected 5xx; fix or filter |
| 2 | Verify SMTP delivery rates (most providers show inbox-vs-spam in dashboard) |
| 3 | Check Razorpay dashboard — any test payments? Any failed webhooks? |
| 5 | First weekly accuracy bench (`make bench` against prod data) |
| 7 | First weekly backup-restore drill — pick an old backup, restore to a scratch DB, verify integrity |

---

## 9. Rollback

If something is fire on production:

```bash
# Render: dashboard → Manual Deploy → pick a known-good commit SHA
# Modal: modal deploy --tag <previous>
# Spot: git checkout <previous-tag> && systemctl restart padhai
```

Postgres rollback is harder — schema changes are forward-only.
Always ship a hotfix migration before reverting code.

---

## 10. After-launch deferred work

These are the items from the 100-sprint retrospective that genuinely
remain after a successful production deploy:

- **PYQ catalog 2103 → 5000+** — content acquisition
- **Curator verification** — 69 channel_seed videos × ~30 sec each
- **Native-speaker translation review** — Hindi + state-board
- **LMS integration for `/live`** — Zoom/Meet/Jitsi pick
- **Detox harness for Capacitor mobile**

None of these block the production push. They block the *next* tier
of launch quality (scale, polish, mobile parity).
