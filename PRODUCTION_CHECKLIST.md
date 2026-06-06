# Production Deploy Checklist

Run this before every production push, especially the first one.
Everything below has a corresponding automated check — if `make
verify && make security` both pass, you're 90% there. The remaining
10% is environment + monitoring that can't be tested in CI.

---

## 0. Pre-flight gates (must pass)

```bash
make verify       # lint + 2 invariant guards + pytest (58) + bench (355+)
make security     # 8 security checks (codified SECURITY.md hardenings)
```

Both should print green. If anything red — **stop**, fix, retry.

## 1. Environment variables (production)

| Variable | Required | Validation |
|---|---|---|
| `APP_ENV` | yes | `production` exactly |
| `PADHAI_JWT_SECRET` | yes | ≥48 random bytes, no placeholder phrases |
| `ADMIN_JWT_SECRET` | yes | Distinct from `PADHAI_JWT_SECRET` |
| `DATABASE_URL` | yes | `postgresql://...` with TLS (`?sslmode=require`) |
| `PADHAI_SUPERUSER_EMAILS` | yes if no DB admins seeded | Comma-separated allowlist |
| `ANTHROPIC_API_KEY` | yes for AI features | `sk-ant-` prefix |
| `S3_BUCKET` + `S3_ENDPOINT_URL` | yes (R2 / S3) | Cache layer |
| `SENTRY_DSN` | recommended | Error reporting |
| `RAZORPAY_KEY_ID` + `RAZORPAY_KEY_SECRET` | yes for fees | Payment processing |
| `RAZORPAY_WEBHOOK_SECRET` | yes for fees | Webhook signature verification |

**Generation commands:**

```bash
# Strong JWT secrets
python -c 'import secrets; print(secrets.token_urlsafe(48))'

# Admin bootstrap token (one-time, unset after first admin signup)
python -c 'import secrets; print(secrets.token_urlsafe(32))'
```

**Forbidden values in any secret env var:**

`dev-`, `change-me`, `CHANGE_ME`, `secret-change`, `placeholder`,
`test-secret`, `qa-test-secret`.

The `_jwt_secret()` validator in `padhai/auth.py` rejects these at
boot when `APP_ENV=production`. `scripts/check_security.py` enforces
the same rule in CI.

## 2. Database

- [ ] Postgres v15+ provisioned with TLS on
- [ ] Connection pool sized for `(workers × concurrency)`, default
      `gunicorn -w 4` → 20 connections minimum
- [ ] Liquibase migrations applied: `liquibase --changelog-file=db/changesets/master.xml update`
- [ ] Backup schedule confirmed (PITR enabled on managed Postgres,
      or `pg_dump` cron on self-hosted)
- [ ] Restore tested at least once into a staging DB
- [ ] `search_path` defaults to `public` (either via `DATABASE_URL`
      `?options=-csearch_path%3Dpublic` or per-connection)

## 3. Provider key validation

Every provider key is validated at startup by
`_validate_provider_keys()` in `padhai/web.py` (~16 providers
covered):

- Anthropic — `sk-ant-` prefix + length
- HeyGen, D-ID, Tavus, Synthesia, DeepBrain (photo-real avatars)
- ElevenLabs, Bhashini, Sarvam (voice)
- Razorpay key + secret + webhook secret
- MSG91 / Twilio / Kaleyra (SMS)

In `APP_ENV=production`, invalid or placeholder keys **fail the
boot**. In dev they warn only. Test this:

```bash
APP_ENV=production python -m uvicorn padhai.web:app --workers 1
```

Should refuse to start with a clear error message if any key is
malformed.

## 4. DPDP §9 — minor protection

Locked in by `tests/test_security_invariants.py`:

- `padhai/dpdp.py:MINOR_AGE_THRESHOLD = 18` (NOT 13)
- Under-18 accounts created with `account_locked = 1`
- Parent consent token: single-use, 7-day TTL
- Privacy Policy at `/privacy` references "under 18"

If you ship to a non-Indian audience, audit DPDP-specific text
language separately — the §9 threshold may not be the right
threshold there.

## 5. Admin gate

Production refuses to boot when neither:

- `DATABASE_URL` set (admin role from `users.is_superuser` column)
- `PADHAI_SUPERUSER_EMAILS` set (env-allowlist fallback)

Tested by `_validate_admin_gate()` in the FastAPI lifespan. **Set
at least one.** Recommended: both.

First admin bootstrap (only after `ADMIN_BOOTSTRAP_TOKEN` is set):

```bash
export ADMIN_BOOTSTRAP_TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
# ... start server ...
curl -X POST https://your-domain/admin/signup \
  -d "email=admin@yourdomain.com&password=...&display_name=Admin&bootstrap_token=$ADMIN_BOOTSTRAP_TOKEN"
unset ADMIN_BOOTSTRAP_TOKEN   # NEVER leave this in the deployed env
```

## 6. Rate limiting + LLM cost cap

- [ ] `padhai/rate_limit.py` token-bucket configured for your QPS
- [ ] `llm_obs.DAILY_COST_CAPS_BY_TIER` matches business plan:
      M1 = ₹0 (premium-only), M2 = ₹20/day, M3 = ₹100/day,
      M4* = uncapped
- [ ] `llm_alerts` table watch — alert webhook wired for
      80%/100% bucket breaches
- [ ] Anthropic 529 / rate-limit handling — currently propagates;
      consider adding retry with exponential backoff before launch

## 7. Observability

- [ ] `/metrics` endpoint reachable internally only (block from
      public ingress)
- [ ] `/healthz` exposed to load balancer (returns 200 within 5s
      when DB reachable)
- [ ] `SENTRY_DSN` set + first error verified. In production the
      `/__sentry_test` route requires the `PADHAI_SENTRY_TEST_TOKEN`
      env var to be set and supplied via the `X-Sentry-Test-Token`
      header:
      `curl -H "X-Sentry-Test-Token: $TOKEN" https://your-domain/__sentry_test`
      Returns 404 in production without the token (DoS-safe). In
      dev/staging the endpoint is open. Event should land in your
      Sentry dashboard within 30s; the FastAPI integration tags the
      route template so it's grep-able as `endpoint:/__sentry_test`.
      Adjust noise via `SENTRY_DROP_STATUSES` (default drops 401/403/
      404/405/422/429).
- [ ] Structured JSON logging on stdout shipped to your aggregator
      (Loki / Datadog / CloudWatch)
- [ ] Per-request `request_id` correlation header in place

## 8. Security headers + CORS

The SPA-embedded HTML responses (`/`, `/ui`, `/home`, `/landing`)
should carry CSP / HSTS / X-Frame-Options headers in production.
Currently configured via the reverse proxy (Render / Cloudflare /
nginx) — **verify the reverse proxy sets them**.

Recommended baseline:

```
Strict-Transport-Security: max-age=63072000; includeSubDomains; preload
Content-Security-Policy: default-src 'self'; script-src 'self' 'unsafe-inline' https://checkout.razorpay.com; ...
X-Frame-Options: DENY
X-Content-Type-Options: nosniff
Referrer-Policy: strict-origin-when-cross-origin
Permissions-Policy: geolocation=(), microphone=(self), camera=(self)
```

CORS is intentionally permissive in dev (`PADHAI_REQUIRE_AUTH=0`).
In production the auth gate handles cross-origin requests via
bearer tokens, so wildcard origins are NOT needed — restrict to
your SPA's domain.

## 9. Multi-tenant guards

Every `/api/orgs/{org_id}/...` endpoint calls `_require_org_role`
or `user_role_in_org` before any data access. Locked in by
`tests/test_security_invariants.py` (parametrised across 8 org
routers) + `tests/test_routers.py` (behavioural smoke).

If you add a new org subsystem, the new tests must extend both
parametrise lists, or `make verify` will let through the omission.

## 10. Mobile shells (if shipping native apps)

- [ ] `mobile/scripts/configure-server.cjs` ran with production URL
- [ ] `npm run build:prod` (NODE_ENV=production) before `cap sync`
- [ ] `server.cleartext = false` in all three Capacitor configs
      (student / parent / teacher)
- [ ] Deep-link allowlist matches the production domain

## 11. Backups + disaster recovery

- [ ] SQLite mode: `scripts/backup_sqlite.sh` running on cron
      (default hourly, 14-day retention)
- [ ] Postgres mode: provider PITR enabled + tested restore
- [ ] Off-region replica configured (Render does this; AWS RDS
      requires explicit setup)
- [ ] Restore runbook documented (`SECURITY.md`?)

## 12. SLA / monitoring

- [ ] Uptime monitor pings `/healthz` every 60s
- [ ] Alert thresholds set:
  - p99 latency > 2s sustained 5m → page
  - 5xx rate > 1% over 5m → page
  - LLM daily cost > 90% of plan budget → page
  - Database connections > 80% of pool → warning
- [ ] On-call rotation defined; runbook for each alert

---

## Last-mile sanity

After deploy:

```bash
# 1. Health
curl -fsS https://your-domain/healthz | jq

# 2. Auth flow
curl -X POST https://your-domain/auth/signup -d 'email=test@example.com&password=YourPw1!&terms_accepted=true'

# 3. AI status (Anthropic configured?)
curl https://your-domain/api/ai-status | jq

# 4. Branding (SPA loads with org colours?)
curl https://your-domain/api/branding/resolve
```

If all four return 200 with expected payloads, you're live.

---

## Failure rollback

If a deploy starts paging:

1. **Render**: `render rollback` (or click the previous deploy
   in the dashboard → "Redeploy this version")
2. **Self-hosted**: `git checkout <last-known-good-sha> && systemctl restart padhai`
3. **Database migration regret**: Liquibase's rollback only works
   if the changeset author wrote a `<rollback>` block. Test rollback
   on staging before relying on it in prod.
4. Post-incident: write up in `incidents/YYYY-MM-DD.md` (create
   that directory if first time)

---

## When this checklist is wrong

It's wrong the moment the codebase changes in a way that affects
production. Update this file in the same PR that introduces the new
production-relevant behaviour. The contributor who shipped the
change is responsible for updating the checklist.

Last reviewed: see `git log -1 PRODUCTION_CHECKLIST.md`.
