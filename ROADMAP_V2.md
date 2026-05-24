# AI Pathshala — Roadmap v2 (v1.1 → v2.0)

> **Status: SHIPPED.** All 29 items shipped across v1.1 → v2.0 (May
> 2026). This file is kept as the historical scoping record. The
> next-phase plan (v2.1 → v3.0) lives in `ROADMAP_V3.md` and covers
> AI-native learning depth, live learning, community + family,
> marketplace, govt depth, ops maturity, and B2B expansion.

The v0.10 → v1.0 plan (`ROADMAP.md`) shipped all 28 items. That's the
**school ERP MVP**: a working multilingual lesson generator with org,
parents, attendance, exams, fees, branding, PWA, and a photoreal-ready
GPU path. v2.0's plan is the **scale + sell + retain** layer.

## Strategic premise

The three risks that block hitting ₹100 Cr ARR with the v1.0 codebase:

1. **It doesn't scale yet.** SQLite, in-process worker, single Render
   region. Fine for 100 schools, not 10,000.
2. **We can't close enterprise deals.** No SAML, no SCIM, no audit log
   export, no SOC 2, no GeM listing. Govt + chains won't sign.
3. **Engagement craters without mobile.** PWA install rate in India is
   <5%. Native apps drive 4× the daily-active retention.

Plus two continuous-investment lanes — **content depth** (NCERT-aligned
quality bar, math/diagram rendering, voice quality) and **new markets**
(South India languages, SAARC, coaching prep, preschool).

Each entry has the same shape as ROADMAP.md: What/Why, Data model, API,
UI, Depends on, Effort, Open Qs. Effort estimates assume one engineer
+ matching design / curriculum / ops input.

---

## Sequencing summary

```
Production scale-out (must come first — everything else assumes scale)
  ┌─ G1 Postgres migration + Alembic
  ├─ G2 Distributed job queue (Redis + RQ)
  ├─ G3 CDN + signed URLs for video delivery
  ├─ G4 Multi-region deploy (Mumbai + Singapore failover)
  ├─ G5 Disaster recovery + backups
  └─ G6 Load testing + capacity planning

Enterprise sales enablement (B2B compliance unblocks govt + chains)
  ┌─ H1 SAML 2.0 SSO              ← independent
  ├─ H2 SCIM auto-provisioning    ← needs H1
  ├─ H3 Audit log export          ← needs F2 (shipped v0.13)
  ├─ H4 Data residency flag       ← needs G1
  ├─ H5 Custom domains            ← needs E9 (shipped v1.0)
  ├─ H6 SOC 2 Type 1 readiness    ← needs H3
  └─ H7 GeM listing + govt procurement

Mobile + engagement (DAU multiplier)
  ┌─ I1 iOS app (Capacitor)
  ├─ I2 Android app (Capacitor)
  ├─ I3 Push notifications (FCM + APNs)  ← needs I1 or I2
  ├─ I4 Streaks + XP + leaderboards
  ├─ I5 Parent app (separate, simpler)
  └─ I6 Teacher app (subset of admin)

Content depth (quality bar vs. Khan Academy, BYJU'S)
  ┌─ J1 Math equation rendering (KaTeX)
  ├─ J2 Procedural diagram generator
  ├─ J3 NCERT/CBSE curriculum alignment scorer
  ├─ J4 Hindi voice clone v2 (Sarvam.ai)
  ├─ J5 Adaptive difficulty engine
  └─ J6 Question bank import (board past papers)

New markets (TAM expansion)
  ┌─ K1 South Indian language polish
  ├─ K2 SAARC expansion (BD/Nepal/SL)
  ├─ K3 UPSC/JEE/NEET deep coaching content
  └─ K4 K-2 preschool content
```

---

# Category G — Production scale-out

The v1.0 stack runs on SQLite + single-process worker + single Render
region. That's fine for the current pilot scale (<100 schools) but
cracks at ~1,000 concurrent active users.

## G1 — Postgres migration + Alembic

**What.** Move from SQLite-on-disk to managed Postgres (Render Postgres
Pro tier or Supabase). Wire Alembic for proper migrations. Convert the
~40 idempotent `CREATE TABLE IF NOT EXISTS` calls scattered across
modules into versioned Alembic revisions.

**Why.** SQLite serializes writes — every video request → job insert
blocks every other write. At 50 concurrent renders the queue stalls.
Postgres also enables `JSONB` for `payload`/`profile_json` columns we
currently store as TEXT, and `pgvector` for the curriculum index.

**Data model.** No schema changes — same tables, different engine.
- Add `alembic/versions/` with one revision per ROADMAP-era schema
  addition (replays the migrations from `schema_v2.py`,
  `branding.py`, `dpdp.py`, `sso.py`, etc.)
- `payload TEXT` → `payload JSONB` for `jobs`, `video_requests`,
  `org_exams.question_set`, `org_term_reports.subjects_json`
- `pgvector` extension for `curriculum_index.embedding`

**API.** None new. Internal: `padhai/db.py:_conn()` swaps the connection
factory to `psycopg[binary]` with a connection pool. Every module's
`_db_path()` helper is replaced with a shared `get_conn()` returning a
pool-borrowed connection.

**UI.** None.

**Depends on.** Nothing (foundational).

**Effort.** **XL (15 days).** 3 days Alembic baseline + first 5
revisions, 3 days connection-pool rewrite + per-module conversion,
3 days dual-write to both engines for a soft cutover, 2 days backfill
of historical data from SQLite to Postgres, 2 days `JSONB` + index
migrations, 2 days perf testing.

**Open Qs.**
- Managed Postgres provider — Render Postgres is convenient but
  expensive at scale (~₹4k/mo for 4GB RAM). Supabase / Neon
  cheaper at the same tier and have automatic pgvector. Verdict:
  **Neon** for cost + serverless scaling.
- Connection pooling — pgBouncer in transaction mode for FastAPI's
  one-conn-per-request pattern, or use Neon's built-in pooler?
- Backfill window — dual-write for 7 days, then cutover. Or
  weekend maintenance window?

---

## G2 — Distributed job queue (Redis + RQ)

**What.** Replace the in-process `Runner` (one thread pool inside the
web tier) with a real distributed queue. Workers run in their own
containers; the web tier just enqueues. Same code, different
deployment topology.

**Why.** Today the web tier blocks on render workers, which means web
replicas are sized for the heaviest concurrent render load. With a
queue, web stays tiny (1 CPU is enough) and worker fleet scales
independently based on queue depth.

**Data model.**
```sql
-- Replace jobs.status flow with RQ's native state + add an outbox
-- pattern so DB row + queue stay consistent.
ALTER TABLE jobs ADD COLUMN rq_job_id TEXT;
ALTER TABLE jobs ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0;
ALTER TABLE jobs ADD COLUMN max_attempts INTEGER NOT NULL DEFAULT 3;

CREATE TABLE job_outbox (
  id            TEXT PRIMARY KEY,
  job_id        TEXT NOT NULL REFERENCES jobs(id),
  payload       JSONB NOT NULL,
  enqueued_at   REAL,           -- NULL until worker picks it up
  created_at    REAL NOT NULL
);
CREATE INDEX idx_outbox_unenqueued ON job_outbox(id) WHERE enqueued_at IS NULL;
```

**API.** No external change. Internal:
- `padhai/queue.py` — RQ wrappers with retry + exponential backoff
- `padhai/worker.py` — entrypoint for the worker container
- `padhai/web.py:_render_worker` becomes `padhai/render_worker.py:run_job`

**UI.** Admin console queue page (already exists) reads from RQ's
introspection API instead of the in-process pending list.

**Depends on.** G1 (Postgres for `job_outbox`).

**Effort.** **L (10 days).** 2 days RQ setup + Redis hosting, 2 days
outbox pattern + idempotent enqueue, 2 days worker container + retry
logic, 2 days admin console rewiring, 2 days testing failure modes
(worker crash, Redis down, partial writes).

**Open Qs.**
- Redis hosting — Upstash (serverless, pay-per-request) or
  Render Redis (managed, fixed cost)? Verdict: **Upstash** below
  10k jobs/day, switch to Render at scale.
- RQ vs Celery vs Arq — Celery is heavyweight; RQ is Python-friendly
  + minimal; Arq is async-native. Verdict: **RQ** (matches current
  team familiarity).
- Wav2Lip GPU worker — keep as separate Modal app (per
  `modal_deploy.py`) or fold into the same RQ queue? Separate is
  cleaner — GPU workers shouldn't share a queue with CPU workers
  because RQ doesn't have per-queue resource hints.

---

## G3 — CDN + signed URLs for video delivery

**What.** Today rendered MP4s are served directly from R2 over public
URLs. Move to a Cloudflare-CDN-fronted setup with signed URLs that
expire after 24h, and a custom domain (`cdn.aipathshala.in`).

**Why.** Two problems with the current path: (a) public R2 URLs leak
revenue (someone shares a paid lesson on a torrent, we pay egress for
every download), (b) R2 egress to India is fast from Mumbai but slow
from Delhi/Bangalore — Cloudflare's 30+ India PoPs fix that.

**Data model.**
```sql
ALTER TABLE generated_videos ADD COLUMN cdn_url TEXT;
ALTER TABLE jobs ADD COLUMN video_signed_until REAL;  -- expiry timestamp
```

**API.**
- `GET /jobs/{id}/video` — issues a 302 to a signed CDN URL,
  generated lazily (sign + cache for the request's TTL)
- New `padhai/cdn.py:sign_url(r2_key, ttl_seconds=86400)` —
  HMAC-signed URL with `?expires=` + `?sig=`

**UI.** None visible — same `<video src=...>` works because of the 302.

**Depends on.** Nothing critical (parallel to G1/G2).

**Effort.** **M (5 days).** 1 day Cloudflare Worker for signing, 1 day
DNS + R2 custom-domain hookup, 1 day signed-URL generation + tests,
2 days end-to-end smoke + cache-purge runbook.

**Open Qs.**
- Cloudflare R2 bundled CDN vs Bunny.net — Bunny is cheaper for high
  egress but R2's free egress to Cloudflare is a strong default.
  Verdict: **R2 + Cloudflare** for now; revisit if egress >50TB/mo.
- DRM (Widevine / FairPlay) for premium content? Defer to v2.1 — at
  current price points, signed-URL expiry is enough deterrent.

---

## G4 — Multi-region deploy (Mumbai + Singapore failover)

**What.** Run the web tier in two regions: primary Mumbai (Render or
fly.io), warm-standby Singapore. Cloudflare Load Balancing fails over
on healthcheck miss. Postgres uses Neon's read replicas in Singapore
for sub-100ms reads from Southeast Asia.

**Why.** Mumbai Render goes down ~4 hours/year (per their SLA). For
schools mid-period, that's an outage during peak class time. Singapore
failover is also a hedge for the SAARC market push (K2).

**Data model.** None.

**API.** None — handled at infra layer.

**UI.** None.

**Depends on.** G1 (Postgres replication needs Neon).

**Effort.** **L (8 days).** 2 days Cloudflare LB + healthcheck, 2 days
Singapore Render deploy + secrets duplication, 1 day Neon read-replica
setup, 2 days session-affinity testing (sticky vs. JWT), 1 day failover
drill + runbook.

**Open Qs.**
- Active-active vs. active-standby — active-active is cheaper per
  user but harder to reason about for stateful writes. Verdict:
  **active-standby** until we have 100k DAU.

---

## G5 — Disaster recovery: backups + runbook

**What.** Daily off-region Postgres backups (Neon snapshots → S3 in
us-east-1). R2 cross-region replication. Documented runbook with RTO
(4 hours) and RPO (24 hours) targets.

**Why.** SOC 2 (H6) requires DR plan with evidence of restore drills.
Also: peace of mind when a junior engineer drops a table.

**Data model.** None.

**API.** None.

**UI.** Admin → "DR drills" page showing last successful restore
timestamp + result.

**Depends on.** G1 (Postgres), H6 (SOC 2 requirements).

**Effort.** **M (4 days).** 1 day backup automation (Neon → S3),
1 day R2 replication, 1 day runbook doc, 1 day quarterly drill
automation.

---

## G6 — Load testing + capacity planning

**What.** k6 + Locust scripts that exercise the full request →
generate → render → playback flow at 10k concurrent users. Generates
a capacity-planning doc with break-points and scale-up triggers.

**Effort.** **M (5 days).**

---

# Category H — Enterprise sales enablement

These are the non-negotiables for govt + large-chain deals. Without
them we cap at small-school revenue (~₹50k MRR/school × 500 schools =
₹2.5 Cr MRR). With them, we unlock chain deals (Akash, Allen, FIITJEE,
state govt) worth ₹50L+ each.

## H1 — SAML 2.0 SSO

**What.** Beyond Google/Microsoft OIDC (E7 shipped v0.11), some
enterprise buyers (esp. govt + multinational chains) standardize on
SAML 2.0 against their Okta / AzureAD / Ping / OneLogin IdP. Add a
SAML SP endpoint, IdP metadata upload, and per-org SAML config.

**Data model.**
```sql
CREATE TABLE org_saml_configs (
  id              TEXT PRIMARY KEY,
  org_id          TEXT NOT NULL REFERENCES orgs(id),
  idp_entity_id   TEXT NOT NULL,
  idp_sso_url     TEXT NOT NULL,
  idp_certificate TEXT NOT NULL,  -- PEM
  sp_entity_id    TEXT NOT NULL,
  attribute_map   JSONB,           -- {email: 'mail', name: 'displayName', ...}
  enabled         BOOLEAN NOT NULL DEFAULT TRUE,
  created_at      REAL NOT NULL
);
```

**API.**
- `GET /auth/saml/{org_id}/metadata` — SP metadata XML (uploaded
  to the IdP)
- `POST /auth/saml/{org_id}/acs` — Assertion Consumer Service;
  validates the SAML response, maps attributes to user fields,
  signs JWT
- `GET /auth/saml/{org_id}/slo` — Single Logout
- Admin: `POST /api/orgs/{org_id}/saml` — upload IdP metadata XML

**UI.** Org admin → "SSO" tab → upload IdP metadata + test login button.

**Depends on.** E7 (SSO core), G1 (proper config storage).

**Effort.** **L (8 days).** Use `python3-saml` (OneLogin's library);
3 days SP wiring + attribute mapping, 2 days admin UI + metadata
upload, 1 day SLO + edge cases, 2 days testing against Okta + AzureAD
+ Ping.

**Open Qs.**
- IdP-initiated vs SP-initiated — support both (most enterprise IdPs
  use IdP-initiated for app launcher tiles).
- JIT user provisioning — yes for v1; SCIM (H2) is the proper way.

---

## H2 — SCIM auto-provisioning

**What.** SCIM 2.0 endpoints so the IdP (Okta etc.) pushes user
create/update/deactivate events to us. HR removes a teacher → SAML
session revoked + license freed within minutes.

**Data model.**
```sql
ALTER TABLE users ADD COLUMN scim_external_id TEXT;
ALTER TABLE users ADD COLUMN deactivated_at REAL;
CREATE INDEX idx_users_scim_external ON users(scim_external_id);

CREATE TABLE org_scim_tokens (
  id          TEXT PRIMARY KEY,
  org_id      TEXT NOT NULL,
  token_hash  TEXT NOT NULL,
  created_at  REAL NOT NULL,
  revoked_at  REAL
);
```

**API.** Standard SCIM 2.0:
- `POST /scim/v2/Users` — create
- `GET /scim/v2/Users/{id}` — read
- `PATCH /scim/v2/Users/{id}` — partial update (deactivate)
- `DELETE /scim/v2/Users/{id}` — hard delete (or soft via PATCH)
- `GET /scim/v2/Users` — list with filtering
- Same shape for `/scim/v2/Groups` (org_members)

**Depends on.** H1.

**Effort.** **L (8 days).** SCIM is standardized but the spec edges
(PATCH, filter syntax, attribute schema) eat time.

---

## H3 — Audit log export

**What.** Every privileged action — login, role change, branding
update, exam create, grade override, fee adjust, member invite — gets
an immutable audit row. Admin can export the org's last N days as
CSV or stream to their SIEM via webhook.

**Data model.**
```sql
CREATE TABLE audit_log (
  id          TEXT PRIMARY KEY,
  org_id      TEXT,           -- NULL for platform-level events
  actor_user_id TEXT,
  actor_ip    TEXT,
  action      TEXT NOT NULL,  -- 'org.branding.update' | 'exam.grade.override' | ...
  target_type TEXT,           -- 'user' | 'org' | 'exam' | ...
  target_id   TEXT,
  before_json JSONB,
  after_json  JSONB,
  request_id  TEXT,           -- matches observability trace id
  created_at  REAL NOT NULL
);
CREATE INDEX idx_audit_org_time ON audit_log(org_id, created_at DESC);
CREATE INDEX idx_audit_action ON audit_log(action, created_at DESC);
```

**API.**
- `GET /api/orgs/{id}/audit?from=&to=&action=` — paginated; org
  admin only
- `POST /api/orgs/{id}/audit/export.csv` — async export job
- `POST /api/orgs/{id}/audit/webhook` — configure SIEM endpoint

**UI.** Org admin → "Audit log" tab with filter chips + export button.

**Depends on.** F2 (observability).

**Effort.** **M (5 days).** 1 day data model + write hooks at every
privileged action site, 2 days admin UI, 1 day CSV export, 1 day
webhook + retries.

**Open Qs.**
- Retention — keep audit rows forever, or 1-year rolling? Some
  enterprise contracts require 7 years. Tiered storage: hot 90 days
  in Postgres, cold archive in S3 Glacier.

---

## H4 — Data residency flag

**What.** Per-org flag: "all data for this org must stay in India."
When set, R2 writes route to the AP-South-1 R2 region; Postgres uses
the Mumbai instance; logs/analytics are scrubbed of cross-border PII;
backup destinations are India-only.

**Why.** DPDP §16 (cross-border data flows) + govt school RFPs both
require this. Without it, central government deals are blocked.

**Data model.**
```sql
ALTER TABLE orgs ADD COLUMN data_residency TEXT DEFAULT 'global';
-- values: 'global' | 'india' | 'eu' (future)
```

**API.** Admin: `POST /api/orgs/{id}/data-residency` (super-admin
only — once set, can't be changed without data migration).

**Depends on.** G1 (per-region Postgres), G4 (multi-region infra).

**Effort.** **L (8 days).** 3 days storage routing + connection pool
per residency, 2 days log scrubber, 1 day admin UI, 2 days testing.

---

## H5 — Custom domains

**What.** E9 shipped subdomains (`stpauls.aipathshala.in`). H5 adds
top-level custom domains (`learn.stpauls.edu.in`) via Cloudflare for
SaaS or manual cert provisioning.

**Data model.**
```sql
ALTER TABLE orgs ADD COLUMN custom_domain TEXT;
ALTER TABLE orgs ADD COLUMN domain_verified_at REAL;
CREATE UNIQUE INDEX idx_orgs_custom_domain ON orgs(custom_domain)
  WHERE custom_domain IS NOT NULL;
```

**API.**
- `POST /api/orgs/{id}/custom-domain` — set + return verification
  CNAME / TXT record
- `GET /api/orgs/{id}/custom-domain/verify` — re-check DNS

**Effort.** **L (8 days).** 2 days Cloudflare for SaaS integration
(SSL cert issuance + routing), 1 day DNS verification flow, 2 days
admin UI + per-domain branding inheritance, 3 days edge cases (cert
renewal failures, domain hijacking protection).

---

## H6 — SOC 2 Type 1 readiness

**What.** Get to a point where a SOC 2 auditor can complete a Type 1
assessment in ≤30 days. Means: documented policies, access controls,
incident-response runbooks, vendor management, employee security
training, encryption at rest + in transit, audit logs (H3), DR
plan (G5).

**Why.** Mid-market chains (Akash etc.) ask for it during procurement.
Type 1 ≈ "controls designed correctly"; Type 2 ≈ "controls operated
correctly for 6 months" — Type 1 unblocks deals while we accumulate
Type 2 evidence.

**Data model.** None.

**API.** None (process work).

**UI.** Internal: admin console section showing control evidence
(audit log volume, employee training completion, etc.).

**Depends on.** H3, G5.

**Effort.** **XL (20 days).** Mostly process: 5 days policy docs,
3 days vendor mgmt + employee training rollout, 3 days encryption
audit + remediation, 3 days incident-response runbooks, 6 days
auditor walkthroughs + evidence packaging.

**Open Qs.**
- Auditor — Drata + Vanta automate evidence collection; pick one.
  ~₹4-6L/year for SaaS + ₹6-10L for the actual Type 1 audit.
- ISO 27001 instead? Indian govt RFPs often ask for both. Defer
  ISO to v2.1.

---

## H7 — GeM listing + govt procurement readiness

**What.** Get listed on the Government e-Marketplace (GeM portal) so
state govts can buy directly via PO. Requires Udyam/MSME registration,
GST compliance, NCB (Non-Commercial Bid) docs, and a published price
list per SKU.

**Why.** Indian govt education spend is ~₹4L Cr/year; ~10% is going
digital. GeM is the only legal procurement channel for >₹2L purchases.
Without GeM listing, even a willing IAS officer can't buy our product.

**Effort.** **M (5 days engineering — but 30-60 days calendar time
for paperwork).** Engineering scope is small: build the price-list
publishing endpoint + per-SKU spec sheet generator. Rest is
admin/legal (DocuSign with a CA + lawyer).

---

# Category I — Mobile + engagement

Native mobile is table-stakes for India. PWA install rate is <5% even
when prompted; native apps are 4× the DAU multiplier per analyst
benchmarks (Inc42, RedSeer). Engagement features (streaks, push)
compound on top.

## I1 — iOS app (Capacitor wrapper)

**What.** Use Capacitor (formerly Cordova) to wrap the PWA into a
native iOS app. ~99% code reuse — Capacitor exposes native APIs
(push, biometric, file picker) as JS modules; the existing SPA stays
mostly intact.

**Why.** Apple App Store presence matters for credibility — schools
ask for it during demos. PWA install on iOS is also actively hostile
(Apple downgrades the install banner).

**Data model.** None.

**API.** New `padhai/native.py`:
- `POST /api/native/devices/register` — APNs/FCM token registration
- `POST /api/native/push-prefs` — user notification prefs
- `GET /api/native/version-check` — soft / hard update prompts

**UI.** Existing SPA + a native shell. Native-only additions:
- Splash screen + Apple/Google "Sign in with" buttons
- Native share sheet for "Share via WhatsApp" (vs Web Share API)
- Native video player (better than `<video>` for offline)
- Biometric unlock for the Parent app

**Depends on.** D3 PWA (shipped v1.0).

**Effort.** **L (10 days).** 2 days Capacitor scaffolding + config,
2 days native modules (push, share, biometric), 2 days App Store
listing + screenshots + privacy policy, 2 days TestFlight + reviewer
back-and-forth, 2 days submission edge cases.

**Open Qs.**
- React Native instead of Capacitor — RN gives better perf but the
  existing SPA isn't React-based. Capacitor wraps as-is. Verdict:
  **Capacitor** for v1, RN if we hit perf walls.
- In-App Purchase — Apple takes 30% on digital content. M3 tier
  subscriptions sold inside the iOS app would lose ₹120/year per
  user to Apple. Mitigation: sell only on web; iOS app reads
  subscription state from server.

---

## I2 — Android app (Capacitor wrapper)

**What.** Same as I1 but Android. Lower effort because Android allows
sideloading + side-channel payments (less Apple-tax friction).

**Effort.** **M (5 days).** Capacitor's Android tooling is mature.

---

## I3 — Push notifications

**What.** Server-side push via FCM (Android) + APNs (iOS) + Web Push
(Chrome/Edge). Plumbing for assignment-due reminders, exam alerts,
attendance flags, parent updates, daily streak nudges.

**Data model.**
```sql
CREATE TABLE push_tokens (
  id          TEXT PRIMARY KEY,
  user_id     TEXT NOT NULL,
  platform    TEXT NOT NULL,  -- 'fcm' | 'apns' | 'web'
  token       TEXT NOT NULL,
  device_id   TEXT,
  app_version TEXT,
  active      BOOLEAN NOT NULL DEFAULT TRUE,
  created_at  REAL NOT NULL,
  last_used   REAL,
  UNIQUE (user_id, token)
);

CREATE TABLE push_log (
  id          TEXT PRIMARY KEY,
  user_id     TEXT NOT NULL,
  category    TEXT NOT NULL,    -- 'assignment_due' | 'exam_alert' | ...
  title       TEXT NOT NULL,
  body        TEXT,
  sent_at     REAL NOT NULL,
  delivered_at REAL,
  opened_at   REAL,
  failed_reason TEXT
);
```

**API.**
- `POST /api/push/send` — internal; called by E2 notifications +
  reminders worker
- `POST /api/users/me/push-prefs` — opt-in per category
- `GET /api/push/log` — admin diagnostic

**Depends on.** I1 or I2 (need at least one native app to register
tokens; web push works without).

**Effort.** **M (5 days).** 1 day FCM setup + APNs cert, 1 day token
registration flow, 1 day send fan-out worker, 1 day prefs UI, 1 day
deliverability + failure handling.

---

## I4 — Streaks + XP + leaderboards

**What.** Gamification layer. Daily-streak counter per user; XP awarded
for lesson completion + quiz score + watch time; class/grade
leaderboards. Optional — students/parents can opt out.

**Data model.**
```sql
CREATE TABLE user_streaks (
  user_id           TEXT PRIMARY KEY,
  current_streak    INTEGER NOT NULL DEFAULT 0,
  longest_streak    INTEGER NOT NULL DEFAULT 0,
  last_active_date  TEXT,    -- YYYY-MM-DD
  xp_total          INTEGER NOT NULL DEFAULT 0,
  level             INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE xp_events (
  id              TEXT PRIMARY KEY,
  user_id         TEXT NOT NULL,
  kind            TEXT NOT NULL,    -- 'lesson_done' | 'quiz_perfect' | 'streak_7' | ...
  xp_amount       INTEGER NOT NULL,
  context_json    JSONB,
  created_at      REAL NOT NULL
);
CREATE INDEX idx_xp_user_time ON xp_events(user_id, created_at DESC);
```

**API.**
- `GET /api/me/streak` — my streak + XP + level
- `GET /api/orgs/{id}/classes/{cid}/leaderboard?period=week|month|alltime`
- Internal: `xp.award(user_id, kind, amount)` called from existing
  completion / quiz hooks

**UI.**
- Header XP/streak chip (always visible)
- Profile page: streak calendar (GitHub-style heatmap)
- Class leaderboard widget on Studio dashboard

**Effort.** **M (5 days).** 1 day data + helpers, 1 day XP rules
calibration, 1 day streak calendar UI, 1 day leaderboard UI, 1 day
opt-out + privacy.

**Open Qs.**
- Gamification can harm motivation (Deci & Ryan: extrinsic
  motivation crowds out intrinsic). Default: opt-in for under-13;
  opt-out for 13+. School admin can disable globally.

---

## I5 — Parent app

**What.** Separate (simpler) Capacitor app for parents. Shows their
linked children's progress, fee status, attendance, notifications.
No content creation; no quiz answering. Read-mostly.

**Data model.** None new — uses E8 parent_links + existing reads.

**API.** Reuses existing parent endpoints (`GET /api/parents/*`).

**UI.** Native shell loading a stripped-down SPA mode
(`/ui?mode=parent`).

**Depends on.** I1, I2, E8 (shipped v0.14).

**Effort.** **M (5 days).** 1 day separate Capacitor project,
2 days parent-only SPA mode (subset of existing UI), 2 days
push prefs + biometric unlock + App Store listings.

---

## I6 — Teacher app

**What.** Same idea but for teachers — class roster, mark attendance,
create assignment, see student progress. Optimized for in-class
quick taps (mark attendance from the back of the room).

**Effort.** **M (5 days).**

---

# Category J — Content depth

The v1.0 content engine produces decent lessons but loses to Khan
Academy India + BYJU'S on three axes: math rendering (we render
formulas as plain text), diagrams (we generate descriptions, not
visuals), and voice quality (Bhashini/Piper sound robotic in Hindi).
J1-J4 close those gaps.

## J1 — Math equation rendering

**What.** Pass through LaTeX-like math notation from Claude → KaTeX
SVG render in slides + spoken-out version in TTS narration.
Currently: "x equals negative b plus or minus square root b squared
minus four a c over two a" (TTS-friendly but ugly on slide).

**Data model.** Scene gets `math_blocks: List[{latex, position, alt_text}]`.

**API.** No new endpoints — rendering pipeline change.

**UI.** Slides render KaTeX SVG inline; narration substitutes the
`alt_text` (TTS-friendly form).

**Effort.** **M (5 days).** 1 day Claude prompt to emit math blocks,
2 days KaTeX SSR pipeline, 1 day TTS substitution, 1 day Hindi/Indic
math notation edge cases.

**Open Qs.**
- LaTeX support in Indic scripts (Hindi math text + LaTeX inline)?
  KaTeX handles it but font fallback is tricky.

---

## J2 — Procedural diagram generator

**What.** When the lesson plan calls for a diagram (water cycle,
circuit, food chain), today Claude describes it; we render text.
Upgrade: Claude emits Mermaid / draw.io / D3 spec → we render as
SVG → drops into the slide.

**Data model.** Scene gets `diagrams: List[{spec_lang, spec, alt_text}]`.

**API.** None new.

**UI.** Slide layout reserves a diagram area; SVG renders into it
with proper sizing.

**Effort.** **L (8 days).** 2 days Claude prompt + diagram types
catalog (50 common education diagrams), 2 days Mermaid renderer +
fallback to programmatic SVG for non-Mermaid types, 2 days slide
layout integration, 2 days quality bar (which diagrams render well,
which need manual templates).

**Open Qs.**
- Photo-realistic diagram generation via DALL-E / SD-XL? Slow + ₹3
  per image. Procedural Mermaid is free + instant; reserve image
  generation for "cover slide" only.

---

## J3 — NCERT/CBSE curriculum alignment scorer

**What.** After lesson generation, run a classifier: does this lesson
actually cover the NCERT Class N learning objectives for this topic?
Outputs an "alignment score" (0-100) + a list of objectives
missed/covered. Teachers see this in Studio; schools see aggregate
"curriculum coverage" metrics.

**Data model.**
```sql
CREATE TABLE curriculum_objectives (
  id          TEXT PRIMARY KEY,
  board       TEXT NOT NULL,    -- 'cbse' | 'icse' | 'state_mh' | ...
  grade       INTEGER NOT NULL,
  subject     TEXT NOT NULL,
  chapter     TEXT NOT NULL,
  objective   TEXT NOT NULL,
  source      TEXT,             -- 'NCERT Class 8 Science p.42' for citations
  created_at  REAL NOT NULL
);
CREATE INDEX idx_curr_board_grade ON curriculum_objectives(board, grade, subject);

ALTER TABLE generated_videos ADD COLUMN alignment_score INTEGER;
ALTER TABLE generated_videos ADD COLUMN alignment_json JSONB;
```

**API.**
- `GET /api/curriculum/objectives?board=cbse&grade=8&subject=science&chapter=...`
- Internal: alignment scorer runs after `generate_lesson()`

**UI.** Studio result screen shows the alignment score + missed
objectives.

**Effort.** **L (10 days).** 4 days curriculum-ingest (NCERT
PDFs → structured objectives — needs OCR + manual review for
some grades), 3 days Claude classifier prompt + few-shot examples,
2 days Studio + admin UI, 1 day testing.

**Open Qs.**
- NCERT content is govt-published; ingesting their PDFs into our
  catalog needs an IP review. Likely fair use (we're not
  republishing, just classifying against), but get legal sign-off.

---

## J4 — Hindi voice clone v2 (Sarvam.ai)

**What.** Bhashini Hindi neural voice (used since v0.6) sounds OK but
robotic. Sarvam.ai's `bulbul` model sounds 10× better — like a real
teacher. Add Sarvam as a TTS provider option for paid tiers.

**Data model.** None.

**API.** Existing TTS interface; new `padhai/tts/sarvam.py` provider.

**Effort.** **S (2 days).** 1 day provider integration + key
management, 1 day voice catalog + tier routing (free → Bhashini,
paid → Sarvam).

**Open Qs.**
- Cost — Sarvam is ~₹0.40/min vs Bhashini's free tier. Pass through
  to M2+ tier; free stays on Bhashini.

---

## J5 — Adaptive difficulty engine

**What.** Per-student difficulty calibration. Student aced last 3
quizzes → next lesson skews "advanced" automatically. Student
struggled → next lesson skews "easier" + repeats prerequisites.

**Data model.**
```sql
CREATE TABLE user_topic_mastery (
  user_id     TEXT NOT NULL,
  topic_key   TEXT NOT NULL,    -- 'photosynthesis' | 'quadratic_eq' | ...
  mastery     REAL NOT NULL,    -- 0.0-1.0
  attempts    INTEGER NOT NULL,
  last_seen   REAL NOT NULL,
  PRIMARY KEY (user_id, topic_key)
);
```

**API.** Modify `build_profile()` to read mastery + adjust difficulty +
inject "prerequisite recap" sections for low-mastery topics.

**Effort.** **L (8 days).** 2 days mastery model (Bayesian Knowledge
Tracing or simpler EWMA), 2 days profile integration, 2 days
prerequisite-graph traversal (which topics depend on which), 2 days
testing + tuning.

---

## J6 — Question bank import

**What.** Bulk-import board past papers (CBSE/ICSE 2015-2025) as a
searchable question bank. Teachers compose tests by pulling from the
bank; AI generates similar-style new questions when needed.

**Data model.**
```sql
CREATE TABLE question_bank (
  id          TEXT PRIMARY KEY,
  board       TEXT NOT NULL,
  grade       INTEGER NOT NULL,
  subject     TEXT NOT NULL,
  chapter     TEXT,
  year        INTEGER,
  paper       TEXT,             -- 'main' | 'compartment' | 'sample'
  question    TEXT NOT NULL,
  options     JSONB,            -- MCQ options or NULL for free-form
  answer      TEXT,
  marks       INTEGER,
  difficulty  TEXT,             -- 'easy' | 'medium' | 'hard'
  topic_tags  JSONB,
  source      TEXT,
  created_at  REAL NOT NULL
);
CREATE INDEX idx_qb_board_grade ON question_bank(board, grade, subject);
CREATE INDEX idx_qb_topic ON question_bank USING gin(topic_tags);
```

**Effort.** **L (10 days).** Most of the effort is the data
acquisition + cleaning, not code.

---

# Category K — New markets

## K1 — South Indian language polish

**What.** Tamil, Telugu, Kannada, Malayalam are already in the
language list but the voice + script rendering needs work. Tamil
script rendering breaks on certain conjuncts; Telugu TTS pacing is
off; Malayalam diacritics drop in some PDF outputs.

**Effort.** **M (5 days).** Per-language QA + targeted fixes.

---

## K2 — SAARC expansion (BD/Nepal/SL)

**What.** Bangladesh (Bengali primary + Bangla-medium board), Nepal
(Nepali + NEB curriculum), Sri Lanka (Sinhala + Tamil + national
curriculum). Each is a market 20-100M kids that want our product but
need local curriculum + payment rails.

**Data model.**
```sql
ALTER TABLE orgs ADD COLUMN country TEXT DEFAULT 'IN';
-- 'IN' | 'BD' | 'NP' | 'LK'
ALTER TABLE orgs ADD COLUMN currency TEXT DEFAULT 'INR';
ALTER TABLE curriculum_objectives ADD COLUMN country TEXT NOT NULL DEFAULT 'IN';
```

**API.** Payment integration changes — Razorpay → bKash/SSLCOMMERZ
(BD), eSewa (NP), PayHere (LK).

**Effort.** **XL (15 days per country).** Mostly payment + curriculum
ingest. Start with BD (largest market, easiest Bangla overlap).

---

## K3 — UPSC/JEE/NEET deep coaching content

**What.** Move beyond K-12 into the ₹40k Cr coaching market. UPSC has
20L+ aspirants/year; JEE/NEET each 15L+. Coaching institutes are
willing to pay ₹2L+/year/student. Specialized:
- UPSC: prelims MCQ practice + mains essay assistance + current
  affairs daily digest
- JEE: physics/chem/math problem-solving (step-by-step worked
  examples) + mock test analytics
- NEET: biology/chem/physics + previous-year-question-paper
  retrieval

**Effort.** **XL (30 days per exam track).** Mostly content/curriculum
work; engineering scope is the practice-test engine (~10 days).

---

## K4 — K-2 preschool content

**What.** Younger end of the market. 4-6 year olds. Sing-along
phonics + Hindi varnamala + counting + colors. Different UI metaphor
(parent-driven, swipe-only).

**Effort.** **L (10 days engineering + 30 days content).** Engineering
is mostly a new "Kids mode v2" with audio-first UI + simplified
quiz format.

---

# Suggested v1.1 → v2.0 sequencing

| Release | Items | Theme |
|---|---|---|
| **v1.1** | G1, G3, H3 | Production foundation — Postgres + CDN + audit logs |
| **v1.2** | G2, G6, I3 | Scale — queue + load test + push notifications |
| **v1.3** | H1, H2, H4 | Enterprise SSO + provisioning + data residency |
| **v1.4** | I1, I2 | Mobile apps (iOS + Android) |
| **v1.5** | I4, J1, J2 | Engagement + math + diagrams |
| **v1.6** | H5, H6, G4 | Custom domains + SOC 2 + multi-region |
| **v1.7** | I5, I6, J6 | Parent + teacher apps + question bank |
| **v1.8** | J3, J4, K1 | Curriculum scorer + Hindi voice + South India |
| **v1.9** | K2, K3 | SAARC + coaching prep |
| **v2.0** | G5, J5, K4, H7 | DR + adaptive difficulty + preschool + GeM |

10 releases ≈ 10 months of work at a sustainable cadence. A 3-engineer
team could compress to 6 months.

# Total effort

- Category G (Production scale-out): 6 items, ~47 days
- Category H (Enterprise enablement): 7 items, ~62 days
- Category I (Mobile + engagement): 6 items, ~40 days
- Category J (Content depth): 6 items, ~43 days
- Category K (New markets): 4 items, ~60 days (heavy on per-country
  content ingest)

**Total: 29 items, ~252 days = ~50 weeks of single-engineer work.**

With 3 engineers + 1 designer + 1 ops + 1 PM + curriculum
specialists per language, plausibly ships in 8 calendar months at a
fast cadence, or 12 months at a sustainable one.

---

# Risk register

The three things most likely to derail this plan:

1. **Postgres migration is harder than expected.** Dual-write
   patterns are subtle; data loss on cutover is a brand killer.
   Mitigation: pay an external consultant for a 2-day audit of
   the G1 plan before starting.
2. **Apple App Store rejection cycle.** First submission rejection
   adds 1-2 weeks. Mitigation: do a TestFlight beta with a
   compliance-checklist pass before the real submission.
3. **SOC 2 process takes longer than 30 days.** Auditor + Drata
   + Vanta calendar gating. Mitigation: start H6 paperwork in
   parallel with H1-H4 (zero blocking), not after.

# What's NOT in this roadmap

Explicit defers — items raised in discussion but ruled out of v2.0:

- Full LLM-driven adaptive curriculum (vs J5's lightweight
  difficulty engine) — too research-y for v2.0; revisit at v3
- AR/VR lessons — niche; market not ready
- Live human tutor marketplace — different business; spin out as
  a separate product if pursued
- Chat-only mode (ChatGPT-for-students) — exists in market; we win
  on video, not chat
- B2C parent subscription as a primary revenue model — keeps the
  school relationship; B2C is too crowded
- ISO 27001 — defer to v2.1 after SOC 2 Type 1 lands
- Voice clone of the teacher themselves (vs a generic Hindi voice)
  — privacy + IP review needed; defer to v2.x
