# Production Deploy Checklist

Run this before every production push, especially the first one.
Everything below has a corresponding automated check — if `make
all-verify && make security` both pass, you're ~90% there. The
remaining ~10% is environment + monitoring that can't be tested
in CI.

**Last updated** at prod-106. The status column tracks what's
automated vs. what's genuinely external work (signing up to a
provider, flipping DNS, content review).

---

## 0. Pre-flight gates (must pass)

```bash
make verify        # lint + 2 invariant guards + pytest (201) + bench (385)
make security      # 8 security checks (codified SECURITY.md hardenings)
make all-verify    # adds pip-audit + coverage on top of verify
```

All should print green. If anything red — **stop**, fix, retry.

## Companion docs

- [`docs/DEPLOY.md`](docs/DEPLOY.md) — end-to-end deploy walkthrough
  (Render / Modal / Spot decision tree, DNS, SSL, smoke).
- [`docs/MONITORING.md`](docs/MONITORING.md) — day-2 ops watchlist
  (what to check daily, what alerts to set up).
- [`docs/INCIDENT.md`](docs/INCIDENT.md) — incident response
  playbook (5xx burst, payment fraud, DPDP request, cost overrun).
- Provider walkthroughs: [`RAZORPAY`](docs/RAZORPAY.md) ·
  [`SMTP`](docs/SMTP.md) · [`SENTRY`](docs/SENTRY.md) ·
  [`POSTHOG`](docs/POSTHOG.md).

## What's automated vs. what's external

| Concern | Status | Reference |
|---|---|---|
| Schema migrations idempotent | ✅ Done (prod-57, prod-60, prod-70) | `concept_videos.py:_ensure_updated_at_column` |
| Daily Claude cost caps + UI | ✅ Done (prod-33, prod-38, prod-58) | M2=₹100/M3=₹400; `/api/me/cost-today`; admin chip |
| Curator workflow end-to-end | ✅ Done (prod-41..82) | UI + stats + iframe-check + CSV import + nightly cron |
| Nightly ops automation | ✅ Done (prod-91) | `scripts/nightly_ops.sh` + `make nightly-ops` |
| Razorpay walkthrough docs | ✅ Done (prod-103) | `docs/RAZORPAY.md` |
| PYQ catalog (engineering) | ✅ Done (prod-4, pipeline ready) | `scripts/import_pyq.py`; 1853 seeded |
| PYQ catalog (content) | 🟡 Ongoing | Target 5000+; content acquisition work |
| Concept-video curator verification | 🟡 69 to do | All infra built (prod-41..82); human review |
| Real Razorpay live keys | 🔴 Manual | §1, §3c — sign up + KYC |
| Real SMTP keys | 🔴 Manual | §1, §3b — SendGrid / SES / Postmark / Mailgun |
| Real Sentry DSN | 🔴 Manual | §1, §3d — Sentry signup |
| Real PostHog token | 🔴 Manual | §1, §3e — PostHog signup |
| `APP_ENV=production` flip | 🔴 Manual | §1 |
| Linux host deploy | 🔴 Manual | §2 — Render / Modal / spot |
| DNS + SSL termination | 🔴 Manual | Cloudflare / Caddy / nginx |
| Native-speaker translation review | 🔴 Manual | Hindi + state-board translations |
| LMS integration for `/live` | 🟡 Engineering deferred | Zoom / Meet / Jitsi pick |
| Detox harness for mobile | 🟡 Engineering deferred | Capacitor native plugins

## 1. Environment variables (production)

| Variable | Required | Validation |
|---|---|---|
| `APP_ENV` | yes | `production` exactly |
| `APP_BASE_URL` | yes | `https://your-domain.com` — used in password-reset + parent-consent email links |
| `PADHAI_JWT_SECRET` | yes | ≥48 random bytes, no placeholder phrases |
| `ADMIN_JWT_SECRET` | yes | Distinct from `PADHAI_JWT_SECRET` |
| `DATABASE_URL` | yes | `postgresql://...` with TLS (`?sslmode=require`) |
| `PADHAI_SUPERUSER_EMAILS` | yes if no DB admins seeded | Comma-separated allowlist |
| `ANTHROPIC_API_KEY` | yes for AI features | `sk-ant-` prefix |
| `S3_BUCKET` + `S3_ENDPOINT_URL` | yes (R2 / S3) | Cache layer |
| `SMTP_HOST` + `SMTP_USER` + `SMTP_PASSWORD` + `SMTP_FROM` | yes | Without SMTP, password-reset + DPDP §9 parent-consent emails sit in `parent_consent_outbox` table forever |
| `SMTP_PORT` | yes | `587` (STARTTLS) or `465` (TLS-from-start) |
| `SENTRY_DSN` | recommended | Error reporting — `https://<pubkey>@<id>.ingest.sentry.io/<project>` |
| `POSTHOG_KEY` + `POSTHOG_HOST` | recommended | Funnel + retention analytics |
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

## 3b. SMTP — pick ONE provider

> **Full walkthrough**: [`docs/SMTP.md`](docs/SMTP.md) covers all four
> providers side-by-side with test commands, fallback-outbox debug
> recipe, and common gotchas.

Without SMTP, every signup confirmation, password-reset, and DPDP §9
parent-consent email accumulates in the `parent_consent_outbox` table.
Locked accounts stay locked forever; users can't recover passwords.

| Provider | Free tier | Setup | Env values |
|---|---|---|---|
| **SendGrid** | 100/day forever | Domain auth + API key | `SMTP_HOST=smtp.sendgrid.net` `SMTP_PORT=587` `SMTP_USER=apikey` `SMTP_PASSWORD=<API key>` |
| **AWS SES** | 62k/mo first year | Verify domain + sender, request prod-access | `SMTP_HOST=email-smtp.<region>.amazonaws.com` `SMTP_PORT=587` `SMTP_USER=<SMTP credentials user>` `SMTP_PASSWORD=<SMTP password>` |
| **Postmark** | 100/mo free | Account + Server-token | `SMTP_HOST=smtp.postmarkapp.com` `SMTP_PORT=587` `SMTP_USER=<Server-API token>` `SMTP_PASSWORD=<same>` |
| **Mailgun** | 100/day for 30 days | Verify domain + SMTP creds | `SMTP_HOST=smtp.mailgun.org` `SMTP_PORT=587` `SMTP_USER=postmaster@<domain>` `SMTP_PASSWORD=<SMTP password>` |

Set `SMTP_FROM=noreply@<your-verified-domain>`. The "verified domain"
part is non-negotiable — every provider rejects FROM addresses that
don't pass SPF/DKIM on a domain you own.

**Smoke test after deploy:**

```bash
# Trigger a real password-reset email to yourself
curl -X POST https://your-domain.com/auth/password-reset/request \
  -d "email=admin@your-org.com"
# → should receive within 30s
```

If the email lands in `parent_consent_outbox` instead, SMTP isn't
wired. Check `/admin/parent-consent-outbox` to see queued emails.

## 3c. Razorpay — start in test mode

> **Full walkthrough**: [`docs/RAZORPAY.md`](docs/RAZORPAY.md) covers
> account setup, test cards table, full curl flow (order → verify →
> webhook), going-live checklist, and code paths.

Production payment flow goes through `/api/payments` (create order)
and `/api/webhooks/razorpay` (verify signature + upgrade tier).

1. Sign up at https://dashboard.razorpay.com (no platform fee for
   first ₹50L of GMV in India)
2. Settings → API Keys → Generate **Test Mode** keys first
3. Set the env vars:
   ```
   RAZORPAY_KEY_ID=rzp_test_xxxxxxxxxxxxxx
   RAZORPAY_KEY_SECRET=<32-char hex>
   ```
4. Webhook: Settings → Webhooks → Add. URL: `https://your-domain/api/webhooks/razorpay`
   Events: `payment.captured`, `payment.failed`, `order.paid`.
   Save the webhook secret as `RAZORPAY_WEBHOOK_SECRET`.
5. Test a ₹1 order end-to-end with Razorpay's test card
   `4111 1111 1111 1111` (any future expiry, any 3-digit CVV).
6. Only after the test-mode flow completes successfully: flip to
   **Live Mode** keys.

The provider validator (`_validate_provider_keys()` in `web.py`)
accepts `rzp_test_` and `rzp_live_` prefixes — it does not enforce
which mode you're in.

## 3d. Sentry — error reporting

> **Full walkthrough**: [`docs/SENTRY.md`](docs/SENTRY.md) covers DSN
> setup, integrations registered, before_send filter, test-fire route,
> release tagging, and common gotchas.

`padhai/observability.py` is wired (prod-6) and waits for a DSN.

1. Sign up at https://sentry.io (5k events/mo on free tier)
2. New Project → Platform: Python (FastAPI)
3. Copy the DSN: `https://<32-hex>@<id>.ingest.sentry.io/<project-id>`
4. Set `SENTRY_DSN=<that>` in env
5. Boot the server, then test-fire:
   ```bash
   # In production, requires the X-Sentry-Test-Token header (see prod-6)
   curl https://your-domain/__sentry_test \
     -H "X-Sentry-Test-Token: <PADHAI_SENTRY_TEST_TOKEN>"
   ```
6. Check Sentry dashboard — should see one `_SentryTestException`
   within 30s, tagged with the route template.

Optional: set `SENTRY_DROP_STATUSES=401,403,404,405,422,429` to keep
auth/validation noise out of the dashboard (default behaviour anyway).

## 3e. PostHog — analytics

> **Full walkthrough**: [`docs/POSTHOG.md`](docs/POSTHOG.md) covers
> project setup, the 10 event names already wired in
> `observability.py:track()`, feature-flag pattern, and useful
> funnel/dashboard queries.

1. Sign up at https://posthog.com or self-host
2. Project Settings → Project ID + Project API Key
3. Set `POSTHOG_KEY=<project_api_key>` and `POSTHOG_HOST=https://app.posthog.com`
   (or your self-hosted URL)
4. Verify in PostHog → Live events that page-view events arrive
   within ~30s of someone loading `/home` or `/dashboard`.

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
