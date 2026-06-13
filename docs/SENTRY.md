# Sentry — production error reporting walkthrough

Without Sentry, server-side 5xx errors disappear into `padhai_server.log`
where nobody sees them until something is on fire. With it, you get
the stack trace + request context + release SHA + a Slack ping
within seconds.

Wiring is already done end-to-end (prod-6). This doc is how you
flip it on safely.

---

## 1. Sign up + get DSN

1. Sign up at [sentry.io](https://sentry.io) (free tier: 5k events/mo).
2. Create new project → choose "FastAPI" platform.
3. Copy the DSN — looks like
   `https://abc123def456@o1234567.ingest.sentry.io/9876543`.

---

## 2. `.env` setup

```bash
# Required
SENTRY_DSN=https://abc123...@o1234567.ingest.sentry.io/9876543

# Optional
SENTRY_ENVIRONMENT=production         # tags events
SENTRY_TRACES_SAMPLE_RATE=0.1         # 10% trace sampling (default 0.0)
SENTRY_PROFILES_SAMPLE_RATE=0.0       # CPU profiling (off by default)

# Release identification — bump this on each deploy
RENDER_GIT_COMMIT=$(git rev-parse HEAD)
# Or set explicitly:
# SENTRY_RELEASE=v3.27.0

# Quiet the noise — these HTTP statuses don't fire to Sentry
SENTRY_DROP_STATUSES=401,403,404,405,422,429

# Production-only safety: hide the test-fire route behind a token
PADHAI_SENTRY_TEST_TOKEN=$(python -c 'import secrets;print(secrets.token_urlsafe(32))')
```

Restart the server. On boot, you should see in `padhai_server.log`:

```
sentry-sdk integration ready: release=<git_sha> environment=production
```

If you see `sentry not configured: SENTRY_DSN unset` — the env var
didn't reach the process.

---

## 3. Test fire (verify the pipeline)

The repo ships a dedicated test-fire route at `/__sentry_test`. It
raises a distinct `_SentryTestException` so a Sentry issue filter
can drop test events.

```bash
# Non-production: open path
curl http://127.0.0.1:8000/__sentry_test
# → 500 + Sentry event fires

# Production: requires the test token
curl -H "X-Sentry-Test-Token: $PADHAI_SENTRY_TEST_TOKEN" \
     https://your-domain/__sentry_test
# → 500 + Sentry event fires

# Production without token:
curl https://your-domain/__sentry_test
# → 404 (security: hides the endpoint from scanners)
```

Within 30 seconds, the event should appear in Sentry's Issues feed.
If it doesn't:

- Check `padhai_server.log` for the init message above.
- Check Sentry → Internal → Stats → "Accepted Events". Network
  egress could be the issue if you self-host on a restricted VPC.
- Check the DSN ends in your project's numeric ID, not someone
  else's by accident.

---

## 4. What's filtered out (and what isn't)

The `before_send` hook in `padhai/observability.py:init_sentry()`
drops events by HTTP status code:

- Default `SENTRY_DROP_STATUSES`: `401, 403, 404, 405, 422, 429`
  (4xx that aren't actionable for the dev team)
- `5xx` always flows
- Events with no `status` attribute (background workers, etc.)
  always flow

To change the filter, set `SENTRY_DROP_STATUSES` to a comma-separated
list. Set it to an empty string to disable filtering (and accept
the noise).

Beyond status filtering, the hook also strips:

- Authorization headers (no Bearer tokens leak)
- Cookie headers (no session leaks)
- `request.data` on POST endpoints with `/auth/` prefix
- DPDP minor user emails (regex match on the `email` field)

See `padhai/observability.py:_scrub_event()` for the full list.

---

## 5. Release tagging

Sentry deduplicates issues per `release`. To use this:

```bash
# Render/Vercel-style: their build env exports the commit SHA
SENTRY_RELEASE=$RENDER_GIT_COMMIT  # auto-picked up

# Manual deploy:
SENTRY_RELEASE=v3.27.0
```

If `SENTRY_RELEASE` and `RENDER_GIT_COMMIT` are both unset, Sentry
groups everything under "unknown" — which is fine for dev but
defeats the purpose in prod.

---

## 6. Integrations registered

`init_sentry()` registers (when `[fastapi]` extra is installed):

- `StarletteIntegration` — captures middleware exceptions
- `FastApiIntegration(transaction_style="endpoint")` — groups
  events by route template (`/api/lessons/{lesson_id}` not
  `/api/lessons/abc123`)
- `LoggingIntegration` (default) — captures `logger.error()` calls

If `sentry-sdk[fastapi]` isn't installed, the plain SDK still
initialises (you get capture, but route templates show as raw URLs).
Install with:

```bash
pip install 'sentry-sdk[fastapi]>=2.0'
```

It's already in `requirements-optional.txt`.

---

## 7. Common gotchas

- **No events fire:** DSN unset, or set to a placeholder. The init
  message logs the resolved DSN's project ID — verify it's yours.
- **Every request fires an event:** you forgot to filter 4xx.
  Set `SENTRY_DROP_STATUSES`.
- **Bot scanners burn your quota:** the test-fire route is the
  classic vector. In prod, set `PADHAI_SENTRY_TEST_TOKEN` so the
  route returns 404 without it.
- **Sensitive data in events:** the scrubber removes Authorization,
  Cookie, and emails by default — but a custom logger that logs a
  request body verbatim will still leak. Audit before going live.
- **Events delayed by minutes:** the SDK batches by default. For
  dev testing, set `SENTRY_FLUSH_TIMEOUT=10` to force flush.

---

## 8. Where the code lives

| Surface | File |
|---|---|
| Init + integration registration | `padhai/observability.py:init_sentry()` |
| `before_send` filter + scrubber | `padhai/observability.py:_scrub_event()` |
| Test-fire route | `padhai/observability.py:_register_sentry_test_route()` |
| Optional dep declaration | `requirements-optional.txt` |
| Boot-time call | `padhai/web.py:install_observability()` |

---

## 9. Tests that lock the contract

```bash
PADHAI_SKIP_DOTENV=1 python -m pytest tests/test_sentry_wiring.py -v
```

The 11 tests pin: init-without-DSN no-ops, capture-before-init
no-ops, test-route 404s in production without token, scrubber
drops Authorization headers, status filter drops 401/403/etc.,
release tagging picks up `RENDER_GIT_COMMIT`.

If any of these fail after a refactor of `observability.py`, the
production deploy is unsafe — fix the regression first.
