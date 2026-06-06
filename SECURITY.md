# Security Policy — AIPadhaiApp

We take security seriously. AIPadhaiApp serves school students (many
under 18), processes payments, and handles personal data subject to
the Digital Personal Data Protection Act 2023. Please report
vulnerabilities responsibly.

---

## Reporting a Vulnerability

**Do not open a public GitHub issue.** Vulnerability reports go to:

- **Email:** security@aipathshala.in (PGP key on request)
- **Backup:** open a *private* GitHub Security Advisory at
  https://github.com/<org>/AIPadhaiApp/security/advisories/new

Include:

1. A description of the issue and its impact.
2. Steps to reproduce — ideally a minimal proof-of-concept.
3. Affected endpoint / module / version (commit SHA or release tag).
4. Whether the issue has been disclosed elsewhere.

You will get an acknowledgement within **3 business days** and a
status update within **14 days**. If you don't, please nudge — mail
delivery occasionally fails.

We follow [coordinated disclosure](https://en.wikipedia.org/wiki/Coordinated_disclosure):
we ask that you give us a reasonable window (typically 90 days, less
for actively-exploited issues) to ship a fix before public
disclosure. Reporters who follow this policy are acknowledged in the
release notes (with permission).

We do **not** currently run a paid bug bounty.

---

## Supported Versions

This is a continuously-deployed product; the `main` branch is the
only supported "version" for security fixes. There are no LTS
backports.

| Branch / tag | Security fixes |
|---|---|
| `main` | yes — fixed immediately |
| Tagged releases (`v*`) | superseded by `main`; upgrade |
| Forks / vendored copies | report upstream; we'll fix in `main` |

If you operate a self-hosted instance, treat any unfixed bug in
`main` as one you need to pull. There is no patch branch.

---

## Scope

### In scope

- The FastAPI service in `padhai/web.py` and helper modules in
  `padhai/`.
- The admin app under `admin/`.
- The Cypress / pytest test harness.
- Capacitor mobile shells under `mobile/`.
- Docker / docker-compose configurations under repo root.
- CI workflows under `.github/workflows/`.

### Out of scope

- Findings against third-party services we integrate (Anthropic
  Claude, Razorpay, HeyGen, D-ID, ElevenLabs, Bhashini, Sarvam,
  MSG91, Twilio, Kaleyra, MinIO, Postgres, Redis, S3 / Cloudflare
  R2) — please report those to the vendor.
- Self-DoS via your own account (e.g. burning your own LLM cap).
- Stale third-party dependency CVEs without a working exploit path
  against this service. We track Dependabot separately.
- Issues that require an attacker to already have admin access.
- Theoretical timing side-channels without practical impact.
- Brute-force against `/auth/login` — see "Rate Limiting" below
  for the deployed countermeasure.

---

## Hardening You Should Know About

These are the security gates enforced by the codebase. When fixing a
finding, do not regress them. When auditing, these are the contracts
you can rely on.

### Authentication

- JWTs are HS256 with a 7-day TTL (`PADHAI_JWT_TTL` overrideable).
- `padhai/auth.py:_jwt_secret()` **refuses to start** the server if
  `PADHAI_JWT_SECRET` is unset. In `APP_ENV=production` it also
  rejects placeholder secrets containing `dev-`, `change-me`,
  `CHANGE_ME`, `secret-change`, or `placeholder`.
- Passwords hashed with bcrypt at cost factor 12.
- The admin app uses a **separate** secret (`ADMIN_JWT_SECRET`) and
  a **separate** SQLite database (`~/.padhai/admin.db`). Student
  sessions cannot escalate to admin via session-cookie reuse.
- First-admin bootstrap requires `ADMIN_BOOTSTRAP_TOKEN`; subsequent
  admins are invited from inside the console. The bootstrap env var
  must be unset after the first signup. See CLAUDE.md §17.

### DPDP Act 2023 §9 — minor protection

- "Child" means under **18** in India (not 13 — we do not adopt
  COPPA's carve-out). `padhai/dpdp.py:MINOR_AGE_THRESHOLD = 18`.
- Under-18 accounts are created with `account_locked = 1` and stay
  locked until a parent redeems a single-use, 7-day-TTL consent
  token.
- Locked accounts cannot access learning surfaces and are not
  subject to behavioural tracking.
- Do not lower the age threshold or skip the locking step. The
  Privacy Policy, registration flow, and DPDP module all reference
  the 18 boundary; revert anything that lowers it.

### Admin gating

- `/admin/*` routes mounted at `/admin` use the admin JWT cookie.
- The `/api/admin/*` routes outside the mounted app rely on
  `require_admin_role`. In production this **must** be backed by
  either a DB-recorded admin row (`DATABASE_URL` set) or an
  allow-list (`PADHAI_SUPERUSER_EMAILS` set).
- `web.py` boot **refuses to start** when `APP_ENV=production` with
  neither configured — the dev fallback that grants admin to every
  signed-in user is not allowed in prod. See CLAUDE.md §16 ("admin
  gate safeguard").

### Provider key validation

- 16 provider keys are validated at startup by
  `_validate_provider_keys()` in `padhai/web.py` — prefix, length,
  and placeholder checks.
- In `APP_ENV=production`, invalid or placeholder keys **fail the
  boot**. In dev they warn only.
- When adding a new provider, extend `_PROVIDER_KEY_SPECS`
  (web.py:505-570). Do not skip validation for "convenience".

### Rate limiting

- `padhai/rate_limit.py` provides a token-bucket limiter. Each
  authenticated user gets per-route quotas; anonymous IPs share a
  smaller bucket.
- LLM-calling endpoints additionally enforce a per-user daily cost
  cap via `llm_obs.check_daily_cap()`. Tier defaults:
  - M1: ₹0 / day (premium-only — no Claude calls for free tier)
  - M2: ₹20 / day
  - M3: ₹100 / day
  - M4*: uncapped
- Caps emit alerts to the `llm_alerts` table at 80% and 100% of cap
  (visible in `/admin/llm-costs`).

### SQL safety

- All SQL uses parameter binding — sqlite3 `?` placeholders and
  psycopg `%s`. Never construct SQL via f-strings, even for "trusted"
  inputs like enum values; use `IN (?, ?, ?)` with bound params.
- Postgres connections **must** pass `options="-c search_path=public"`
  to `psycopg.connect()`. Forgetting this breaks fresh-DB migrations
  with "no schema selected". See CLAUDE.md §10.
- All `CREATE TABLE` statements use `IF NOT EXISTS` so restore
  procedures are idempotent.

### Multi-tenancy

- Every `/api/orgs/{org_id}/...` endpoint resolves the org and then
  calls `_require_org_role(org_id, user.id, {roles})` to gate by
  membership. The dependency lives in `web.py`; routers
  (`routers/orgs_classes.py`, `routers/orgs_leaderboard.py`, …)
  late-import it via `from .. import web as _web`.
- `find_by_org()` patterns are forbidden for cross-org leakage tests
  unless the calling user has been verified as an `admin` of that
  specific org.
- Parent-child links (`/api/parents/*`) verify the child belongs to
  the requesting parent before exposing progress data; the gate
  lives in `padhai/routers/parents.py`.

### Secrets in repo

- `.env` is gitignored. `.env.example` documents the keys but
  carries no real secrets.
- `requirements*.txt` is the only lockfile. Pin updates go through
  Dependabot.
- No private keys, no JWT signing keys, no production credentials
  in the repo. The `cypress/fixtures/` test data is synthetic.

### Mobile shells (Capacitor)

- `mobile/scripts/configure-server.cjs` rewrites the dev server URL
  in three Capacitor configs. `npm run build:prod` flips them back
  to production URLs (`NODE_ENV=production`).
- `server.cleartext = true` is only set for dev / emulator usage
  (`10.0.2.2`). Production shells use `https://` with
  `cleartext = false`.

---

## CI Gates

The following checks block merge to `main` and run on every PR:

| Check | Where | Note |
|---|---|---|
| pytest | `.github/workflows/test.yml` (implicit via PR) | 37 unit tests |
| Ruff F+E+I+B+UP | `.github/workflows/lint.yml` | Hard-blocks regressions |
| Accuracy bench (structural) | `.github/workflows/accuracy-bench.yml` | Every PR — fast, no API key |
| Accuracy bench (live) | `.github/workflows/accuracy-bench.yml` | Push to main only — `--min-pass-rate=0.75`, 102 golden items |
| E2E (docker-compose) | `.github/workflows/e2e.yml` | PR + nightly cron |

If you bypass these (e.g. via admin merge), open a follow-up issue
explaining why.

---

## Contact

- **Security:** security@aipathshala.in
- **General:** see CONTRIBUTING.md

Last reviewed: 2026-06-06.

---

## Known gaps (tracked, not yet fixed)

Surfaced by `scripts/audit_endpoint_tiers.py` at prod-8. These are
**real** issues, not classifier defects — verified by anonymous
`TestClient.get(path)` returning HTTP 200 against a development boot
of `padhai.web:app`.

### 1. Unauthenticated `/api/admin/*` endpoints (HIGH)

Many `/api/admin/*` routes registered by `padhai/routers/v3.py` have
no `current_user` dependency and no in-handler auth check. Anonymous
callers can hit them and receive 200. Examples confirmed:

- `GET /api/admin/flags/{flag_key}/exposures`
- `GET /api/admin/forums/flagged`
- `GET /api/admin/doubts/stats`
- `GET /api/admin/cs/at-risk`

Root cause: `padhai/routers/v3.py:19` declares `router = APIRouter()`
with **no** `dependencies=[Depends(current_user), require_admin_role]`
clause, so per-handler auth is opt-in. Many handlers forgot to opt
in. The path prefix `/api/admin/` is *naming convention only*; the
router doesn't enforce it.

Fix shape (next sprint): convert `v3.py`'s `router = APIRouter()` to
`APIRouter(dependencies=[...])` for the `/api/admin/...` subset, OR
split admin routes into a sibling `routers/v3_admin.py` that carries
the dependency at the router level. Audit `docs/ENDPOINT_TIER_MAP.md`
afterwards to confirm the counts move from PUBLIC → ADMIN_ONLY.

Until the fix lands: **gate `/api/admin/*` at the reverse proxy**
(allow only office-IP CIDR or VPN) — `nginx`/Cloudflare WAF rule, not
application code. Add to PRODUCTION_CHECKLIST.md §6.

### 2. Zero tier-gated endpoints

The codebase ships a `_require_tier(user, "Mx")` helper in
`padhai/web.py` but no endpoint actually calls it. Every "premium"
feature (photoreal avatar, multi-page video, advanced practice) is
free for any signed-in user today. This isn't a security issue —
it's a revenue issue — but it's surfaced by the same audit so it
lives here for visibility.

Fix shape: identify the 8–12 endpoints that are intended to be paid,
add `user = _require_tier(user, "M2")` (or higher) at the top of
each handler, re-run the audit script, and update
`docs/ENDPOINT_TIER_MAP.md`. The `test_no_tier_gated_endpoints_yet`
regression test will appropriately fail and need updating once any
of them are gated.
