# Launch Readiness — AI Pathshala

Last updated: 2026-06-15 (prod-179 — Render deploy runbook + one-command
content seeder + render.yaml deploy-bug fixes).

This document is your **one-page checklist** between "code is ready" and
"paying customers in production". It splits cleanly into **DONE
(engineering)** and **YOU (ops + content + legal)**.

---

## 0. Render deploy runbook (prod-179) — the exact sequence

`render.yaml` is now deploy-ready. The three bugs that would have broken
a first deploy are fixed:
  - `PADHAI_JWT_SECRET` + `ADMIN_JWT_SECRET` use `generateValue: true`
    (Render mints strong secrets — no manual paste, no placeholder the
    prod validator rejects).
  - `PADHAI_DB_PATH=/var/padhai/cache/padhai.db` puts the SQLite module
    DB on the **persistent disk** — without this, every redeploy wiped
    all content + per-user module state.
  - `APP_ENV=production` turns on the strict secret + admin-gate +
    launch-readiness checks.

**Architecture note (important):** the app runs a *hybrid* DB:
  - **Postgres** (DATABASE_URL): users, jobs, lessons (core auth + queue).
  - **SQLite on the persistent disk** (PADHAI_DB_PATH): the ~55 module
    tables — concept videos, PYQs, examples, mastery, memory-boost, etc.

This is the supported single-instance mode. It is correct for a soft
launch. It does NOT shard across multiple web replicas (the SQLite
module DB is per-disk), so keep the Render service at **1 instance**
until the module tables are migrated to Postgres. The Liquibase
changeset 002 already carries the Postgres schema for that future
migration.

**Deploy steps:**

1. Push to GitHub, then in Render: **Blueprint → point at this repo**.
   Render reads `render.yaml`, provisions Postgres + the web service +
   the 5GB disk, and auto-generates the JWT secrets.
2. In the Render dashboard, fill the `sync: false` secrets:
   `ANTHROPIC_API_KEY`, `APP_BASE_URL`, `PADHAI_SUPERUSER_EMAILS`,
   and (when ready) SMTP_*, RAZORPAY_*, SENTRY_DSN, S3_*.
3. First deploy runs `preDeployCommand: python -m scripts.migrate`
   (core Postgres schema, idempotent).
4. **Seed content once** (fresh disk is empty). Open a Render shell:
   ```bash
   python -m scripts.seed_all_content
   ```
   This imports ~2,478 PYQs, 90 concept videos (45 auto-promoted to
   verified), and 48 real-world examples — all from repo data files,
   idempotent. Use `--skip-curate` if outbound network is restricted.
5. Bootstrap the first admin (see §2.A.6).
6. Verify: `PADHAI_BASE=https://your.app python scripts/launch_smoke.py --full`
   should report 21/21.

---

## 1. Engineering — DONE ✅

### Verification (prod-161..163)

| Surface | Verified via |
|---|---|
| `/lessons/new` upload | `scripts/launch_smoke.py` POSTs a PNG, gets 202 + job_id (was 422 before prod-153) |
| `/tutor-modes` mode= param | Sends `mode=quick_explain` — server accepts as Form field |
| `/api/doubts` image flow | Rewired to two-step `/api/uploads` → `/api/uploads/{id}/analyze`; both verified |
| `/school` org modals | All 6 endpoints (members/classes/timetable/assignments/fees/exams) return 200/4xx (never 5xx) |
| `/syllabus` 13 state buckets | All present + context-aware chapter links |
| `/concept` + `/mastery` + `/memory-boost` + `/tutor-modes` chrome | All carry the top-nav SPA shell |
| `/healthz` | 200 + json status |

Run anytime:
```bash
python scripts/launch_smoke.py --full     # 21/21 passing locally
```

### Pre-launch automation (prod-164..168)

- **`scripts/dpdp_purge.py`** — DPDP §12 30-day full-purge cron. Discovers
  72 user_id tables dynamically, scrubs audit log to `ANONYMIZED`, deletes
  user row last. Idempotent + safe `--dry-run` mode. Cron:
  ```
  0 3 * * * cd /opt/aipathshala && /opt/venv/bin/python \
      scripts/dpdp_purge.py >> /var/log/dpdp-purge.log 2>&1
  ```

- **`scripts/generate_prod_secrets.py`** — Emits a ready-to-paste `.env`
  block with strong urlsafe secrets for every variable the production-mode
  safeguard would reject as a placeholder. TODO markers on items needing
  human decisions (Razorpay merchant id, real hostname).
  ```bash
  python scripts/generate_prod_secrets.py > production.env
  # audit, fill TODOs, paste into your secret manager, then delete file
  ```

- **`scripts/auto_curate_videos.py`** — Verified-tier auto-curator.
  Iframe-embed + oembed health-check on channel_seed videos.
  **Already run**: catalog 1 → **45 verified videos** (44 promoted).
  Remaining 25 at channel_seed need human review (oembed failures —
  possibly age-gated or region-restricted).

- **`scripts/check_pg_migrations.py`** — Validates Liquibase changesets
  before Postgres deploy. Currently passes for 001 + 002.

- **`make launch-check`** — One-command pre-deploy gate. Chains
  `verify` + `security` + DPDP dry-run + HTTP smoke + secret check.
  Skips HTTP smoke gracefully if no live server.

- **Launch smoke target** — `make launch-smoke` (also runnable as
  `python scripts/launch_smoke.py`) — 21 checks, full mode hits
  Claude paths (no budget impact — Anthropic rejects 1x1 PNG before
  any meaningful spend).

### Catalog state (post-curator)

| Asset | Count |
|---|---|
| Concept videos — **verified** | **45** (was 1) |
| Concept videos — channel_seed | 25 |
| PYQ catalog | ~2503 |
| Real-world examples (approved) | 48 across 11 concepts |
| Curriculum topics (NCERT-aligned) | 30+ across CBSE / ICSE / state boards |
| i18n catalog | 94 keys × 9 locales (100% coverage of catalog) |
| Endpoint tier map | 786 routes |

---

## 2. YOU — ops + content + legal 🔧

> **prod-171..178 update**: every hard blocker below now has an
> engineering helper to verify it. The verifiers fail loudly when
> credentials are missing — you'll know within seconds whether each
> piece is wired correctly.

### A. Hard blockers (must be done before any paying user)

Each block below pairs the **what** (your ops work) with the **verifier**
(engineering helper that confirms you did it right).

1. **Generate + paste prod secrets**
   ```bash
   python scripts/generate_prod_secrets.py > production.env
   # Audit, fill in TODOs, paste into Render/Vercel/Fly/AWS secret manager.
   # NEVER commit production.env (it's already in .gitignore via *.env.local).
   ```
   *Verifier*: server refuses to boot with placeholder secrets when
   `APP_ENV=production` (prod-32 invariant). `make launch-check` exercises
   the placeholder-detection path.

2. **SMTP** — Without this, parent-consent emails for under-18 students
   stall in `parent_consent_outbox` table. Recommended: SendGrid /
   Postmark / AWS SES.
   ```bash
   # After setting SMTP_HOST / SMTP_USER / SMTP_PASS / SMTP_FROM:
   python scripts/check_smtp.py --check                  # config gates
   python scripts/check_smtp.py --connect                # TLS + auth
   python scripts/check_smtp.py --send=you@yourdomain.com  # end-to-end
   ```
   *Server boot also warns* when `APP_ENV=production` + SMTP missing
   (prod-172). See `docs/SMTP.md`.

3. **Postgres** — SQLite (the dev default) won't survive concurrent
   users. Provision a Postgres DB, run Liquibase:
   ```bash
   liquibase --changeLogFile=db/changesets/master.xml --url=$DATABASE_URL update
   ```
   Or use docker-compose's liquibase service. Changeset 002 now covers
   the previously-missing `concept_videos`, `audit_log`,
   `concept_examples`, `question_bank` tables (prod-171). **Don't forget**:
   every `psycopg.connect()` call in our codebase passes
   `options="-c search_path=public"`. If you write new ones, add that.

   *Verifier*: `python scripts/check_pg_migrations.py` validates the
   changesets parse cleanly without spinning up Postgres.

4. **Razorpay** — Get test-mode keys → verify webhook signing → flip to live.
   ```bash
   # After setting RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET / RAZORPAY_WEBHOOK_SECRET:
   python scripts/check_razorpay.py --check --verify-sig        # signature round-trip
   python scripts/check_razorpay.py --create-order              # real ₹1 test order
   ```
   *Verifier* refuses `--create-order` with live keys; you can't
   accidentally bill a real customer ₹1 from the smoke. See
   `docs/RAZORPAY.md` / `docs/RAZORPAY_TEST_MODE.md`.

5. **Sentry DSN** — Without this, prod errors are invisible. Free tier
   covers ~5k events/month — sufficient for the first 1000 users.
   ```bash
   pip install 'sentry-sdk[fastapi]>=2.0'
   python scripts/check_sentry.py --check                       # config + SDK
   python scripts/check_sentry.py --fire                        # actually fires /__sentry_test
   ```
   *Verifier* checks DSN format, confirms SDK is installed, and
   `--fire` posts to `/__sentry_test` so you can see the event land
   in your Sentry Issues feed.

6. **First admin** — One-line helper:
   ```bash
   # On the SERVER (not your laptop):
   export ADMIN_BOOTSTRAP_TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
   # restart the app
   python scripts/bootstrap_admin.py \
       --email ops@yourdomain.com \
       --password 'YourStrongPw1!' \
       --display-name 'Ops' \
       --base https://api.yourdomain.com
   # then UNSET ADMIN_BOOTSTRAP_TOKEN and restart
   ```
   *The script clear-warns you to unset the token after success.*

7. **Mobile shell URLs**
   ```bash
   cd mobile
   CAPACITOR_SERVER_URL=https://api.yourdomain.com npm run build:prod
   # build:prod now CHAINS check:prod which refuses to bundle
   # if any config still points at 10.0.2.2 / localhost / placeholder.
   ```
   *Verifier* (prod-177) is now part of `npm run build:prod` — you
   physically can't ship an app-store build with a dev URL anymore.
   App-store reviews (Play + iOS) typically take 1-2 weeks; start in
   parallel.

### B. Soft blockers (won't kill launch but will hurt growth)

1. **Curator pass on remaining 25 channel_seed videos** — Open
   `/admin/concept-curator` as an admin. Walk through the queue.
   ~30s per video. Total: 12-15 min.

2. **NCERT tagging on 2500 PYQs** — Run once:
   ```bash
   python -m padhai.ncert_tagger    # ~₹100 Anthropic spend
   ```
   This enables `?ncert_code=CBSE.10.SCI.CH06` filtering on the
   question bank. Without it, /memory-boost and /practice can only
   filter by board+grade, not by chapter.

3. **PYQ catalog 2500 → 5000+** — Content acquisition. Existing
   ingest script:
   ```bash
   python scripts/import_pyq.py data/pyq/<your-batch>.json
   ```
   JEE/NEET aspirants treat past-year papers as table stakes. The
   pipeline is ready; this is just sourcing + JSON munging.

4. **SPA hardcoded English strings** — 94 i18n keys cover ~17% of
   the SPA UI. Hindi/Tamil/Telugu users still see mostly English.
   Catalog growth + JS rewiring is its own sprint.

5. **Real-world examples** — 48 approved across 11 concepts. The
   curator could write ~50 more in a day to cover all 45 verified
   videos and start surfacing on `/concept/{slug}` SEO pages.

### C. Legal / business (engineering can't help here)

1. **T&C + Privacy Policy review** — `/terms` and `/privacy` pages
   exist but need real lawyer eyes for:
   - DPDP Act 2023 §5-9 compliance (minor lock, parental consent, age verification)
   - Consumer Protection Act 2019 (refund policy, dispute resolution)
   - GST + tax compliance if charging in INR
2. **Pricing model** — M1..M4e tier code exists; actual ₹/month for
   each tier needs business decision.
3. **GSTIN registration** — Required to charge Indian customers
   (Razorpay's KYC flow walks you through it).
4. **App-store policies** — Google Play / Apple have specific
   requirements for ed-tech apps targeting minors. Review before
   app-store submission.

---

## 3. Soft launch sequence (recommended)

1. **Day 0** — Complete all hard blockers (A1-A7). `make launch-check`
   green.
2. **Day 1-7** — Soft launch to 20-50 invite-only Indian students.
   Monitor:
   - `/admin/health` page (built in prod-85)
   - `/admin/llm-costs` daily spend per user
   - Sentry error rate
   - `/admin/curator-stats` content engagement
3. **Day 8-14** — Address top 3 friction points from soft-launch
   feedback. Run curator pass on remaining 25 channel_seed videos.
4. **Day 15+** — Public launch with paid ads. Monitor concurrent
   user count; if >100 DAU, request Anthropic tier 2/3 quota.

---

## 4. Daily ops automation (already wired)

The cron lines below cover daily operations. Add them to your prod
host's crontab:

```cron
# 03:00 UTC daily — backup + iframe-health + curator stats
0 3 * * *  cd /opt/aipathshala && AUTO_DEMOTE=1 ./scripts/nightly_ops.sh >> /var/log/padhai-nightly.log 2>&1

# 03:30 UTC daily — DPDP §12 30-day purge (low-traffic window)
30 3 * * * cd /opt/aipathshala && /opt/venv/bin/python scripts/dpdp_purge.py >> /var/log/dpdp-purge.log 2>&1

# 01:30 UTC daily (07:00 IST) — Memory Boost push notifications
30 1 * * * cd /opt/aipathshala && /opt/venv/bin/python scripts/memory_boost_daily_push.py >> /var/log/memboost.log 2>&1

# Hourly — online SQLite backup (Postgres uses provider PITR instead)
17 * * * * /opt/aipathshala/scripts/backup_sqlite.sh >> /var/log/padhai-backup.log 2>&1
```

---

## 5. Honest gaps remaining

Nothing engineering-side is hidden. These are the genuine remaining risks:

- **Real-user testing** — Zero usability data yet. The 21-check smoke
  passes, but feature reliability under 50 real students per day is
  unknown until you actually have 50 real students per day.
- **Load testing** — Not done. Single-user dev only. First soft-launch
  cohort IS the load test; size accordingly.
- **Anthropic rate limits** — At scale (~100 DAU) you'll hit tier 1
  quotas. Request tier 2/3 well before public launch.
- **Push notifications** — Memory Boost cron emits via FCM/APNs
  best-effort. Mobile shells need real push credentials configured
  in Firebase / Apple Push.
- **Content moderation** — User-generated content (essays, doubts)
  flows through `moderation.py`, but the moderation thresholds are
  set conservative-defaults. Tune after first 200 student essays.

---

## 6. Quick reference — what to run

| Command | Purpose |
|---|---|
| `python scripts/launch_smoke.py --full` | 21-check HTTP smoke against live server |
| `python scripts/dpdp_purge.py --dry-run` | Preview DPDP §12 purge |
| `python scripts/auto_curate_videos.py --dry-run` | Preview channel_seed promotions |
| `python scripts/generate_prod_secrets.py > production.env` | Strong .env starter |
| `python scripts/check_pg_migrations.py` | Validate Liquibase changesets |
| `python scripts/check_smtp.py --check` | SMTP config gate (--connect / --send for deeper checks) |
| `python scripts/check_razorpay.py --check --verify-sig` | Razorpay config + webhook signature |
| `python scripts/check_sentry.py --check` | Sentry config + SDK |
| `python scripts/check_sentry.py --fire` | Fire `/__sentry_test` to verify pipe |
| `python scripts/bootstrap_admin.py --email=... --password=...` | First-admin signup helper |
| `cd mobile && node scripts/check-prod-config.cjs` | Refuse mobile builds with dev URLs |
| `make provider-checks` | Run all 4 provider config checks at once |
| `make launch-check` | Full 9-step pre-deploy gate (Linux/CI) |
| `make verify` | Quick pre-PR (~20s — lint + tests + bench) |
| `make all-verify` | verify + audit + coverage |

---

*This document was generated as part of prod-170. Update it after
every launch-readiness sprint. The CLAUDE.md §16 "P1 work status"
section is the ongoing engineering ledger; this file is the launch
checklist that sits on top of it.*
