# Sprint plan — End-to-end testability

**Goal:** `make e2e` brings up Postgres + MinIO + the app, seeds a
demo dataset, runs an HTTP smoke + a Cypress full-flow spec, and
tears down. Same `make e2e` runs in GitHub Actions. Every shipped
feature gets exercised against real dependencies (Postgres, not
SQLite; MinIO, not local disk) before the next release.

**Why now:** the QA harnesses we shipped over the last three
sessions test individual modules well — RAG citations, daily caps,
exam taxonomy, accuracy bench, etc. all have their own
re-runnable harness. But nothing exercises the **full** pipeline
(signup → upload → render → view → citations recorded → cost
tracked) against the same dependencies production uses. We've been
finding bugs in cross-module integration (DPDP cross-DB crash,
orgs.migrate missing, etc.) precisely because the unit-shaped
harnesses don't catch them.

**Done looks like:** A junior dev clones the repo, runs `make
setup && make e2e`, and 5 minutes later sees a green check. CI runs
the same flow nightly + on every PR that touches a hot path (web,
auth, lessons, pedagogy, citations).

---

## Deliverables (8, sequenced)

### 1. SPRINT_E2E.md  ←  *this file*

Spec for the sprint. Lives at repo root so anyone can pick the
next ticket.

### 2. `scripts/seed_demo.py` — demo dataset

Standalone Python script that creates against any running server:

| Role | Count | Notes |
|---|---|---|
| Admin | 1 | Via `/admin/signup` (uses ADMIN_BOOTSTRAP_TOKEN); `admin@demo.local` / `Demo1234!` |
| Student (adult) | 1 | Enrolled in `cbse_class_10_2026`; this is the canonical "Riya" user the screenshots use |
| Student (minor) | 1 | DOB 12y ago + parent_email set → account_locked=1 + consent token in outbox |
| Parent | 1 | Linked to the minor student via `/api/parents/link` |
| Teacher | 1 | Creates `Demo School` org + invites both students |
| Org | 1 | `Demo School` |
| Exam pack enrollment | 1 | `cbse_class_10_2026` for the adult student |

Idempotent — re-running with the same email addresses replays
without duplicating. Skips with clear message on the email-exists
409.

Run independently:

```bash
PADHAI_REQUIRE_AUTH=0 python scripts/seed_demo.py --base-url http://localhost:8000
```

### 3. `docker-compose.yml` + `Dockerfile.dev`

Services:

- **app** — uvicorn on port 8000, mounts `./` for live reload, reads
  `DATABASE_URL=postgresql://padhai:padhai@postgres:5432/padhai`
- **postgres** — postgres:15, healthcheck `pg_isready`. Persists to
  named volume `padhai_pg_data`.
- **minio** — single-node S3-compatible storage on port 9000;
  console on 9001. The app uses `S3_ENDPOINT_URL=http://minio:9000`
  + `S3_BUCKET=padhai-dev`.
- **liquibase** — one-shot service that runs migrations against
  postgres on startup, then exits. `app` `depends_on` it via
  `condition: service_completed_successfully`.

Volumes:

- `padhai_pg_data` — Postgres data dir
- `padhai_minio_data` — MinIO blob storage

`.env` file (template at `.env.docker.example`) carries the secrets
the app needs: `ANTHROPIC_API_KEY`, `PADHAI_JWT_SECRET`,
`ADMIN_JWT_SECRET`, `ADMIN_BOOTSTRAP_TOKEN`, `RAZORPAY_KEY_ID`,
`RAZORPAY_KEY_SECRET`.

### 4. `scripts/e2e_smoke.py`

End-to-end HTTP smoke. Drives the running server through the full
production-shaped flow:

1. Signup → assert 200 + token
2. POST `/lessons` with a real PNG → assert 202 + job_id
3. Poll `/jobs/{id}` → assert status=succeeded within timeout
4. GET `/jobs/{id}/video` → assert MP4 returned
5. Check `/api/citations/me` → assert lesson provenance recorded
6. Check admin-side `/admin/api/llm-costs` → assert call recorded
7. Sign up a minor → assert account_locked=1 + consent token in DB
8. Redeem the token → assert child can log in

Exits 0 on green, 1 on any failure. Each step logs a `[OK]` /
`[FAIL]` line so the failure surfaces immediately.

### 5. Cypress spec `17-e2e-full-flow.cy.js`

UI E2E driving the SPA against the same docker-compose stack:

- Visit `/ui-legacy` → sign in as the seeded student → assert home
  loads
- Navigate to Studio → upload a page → wait for video → assert
  video element renders
- Navigate to Library → assert the just-rendered lesson appears
- Sign out → visit `/auth/parent-consent?t=<token>` (token comes
  from seed_demo's output) → assert "Account unlocked" page

Uses the new `cy.cleanCookies()` autouse fixture for isolation.

### 6. `Makefile` with `make e2e`

```make
.PHONY: setup up down seed smoke cypress e2e

setup:           ## Copy .env from template, generate JWT secrets
	bash scripts/dev_bootstrap.sh

up:              ## docker-compose up -d, wait for healthcheck
	docker-compose up -d
	bash scripts/wait_for_healthz.sh

down:            ## docker-compose down -v
	docker-compose down -v

seed: up         ## Seed the demo dataset
	docker-compose exec app python scripts/seed_demo.py --base-url http://localhost:8000

smoke: seed      ## Run the HTTP e2e smoke
	docker-compose exec app python scripts/e2e_smoke.py --base-url http://localhost:8000

cypress: seed    ## Run the Cypress full-flow spec
	npx cypress run --spec cypress/e2e/17-e2e-full-flow.cy.js

e2e: smoke cypress down
	@echo "E2E green."
```

### 7. `.github/workflows/e2e.yml`

```yaml
name: e2e
on:
  push:
    branches: [main]
    paths: ['padhai/**', 'admin/**', 'mobile/**', 'cypress/**', 'scripts/**']
  workflow_dispatch:
  schedule:
    - cron: '0 21 * * *'  # 02:30 IST nightly

jobs:
  e2e:
    runs-on: ubuntu-latest
    timeout-minutes: 25
    steps:
      - uses: actions/checkout@v4
      - name: docker compose up + wait + healthcheck
        run: make up
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
          PADHAI_JWT_SECRET: ${{ secrets.PADHAI_JWT_SECRET }}
          ADMIN_JWT_SECRET: ${{ secrets.ADMIN_JWT_SECRET }}
          ADMIN_BOOTSTRAP_TOKEN: ${{ secrets.ADMIN_BOOTSTRAP_TOKEN }}
          RAZORPAY_KEY_ID: ${{ secrets.RAZORPAY_KEY_ID }}
          RAZORPAY_KEY_SECRET: ${{ secrets.RAZORPAY_KEY_SECRET }}
      - name: seed demo data
        run: make seed
      - name: HTTP smoke
        run: make smoke
      - name: Cypress
        run: make cypress
      - name: docker logs on fail
        if: failure()
        run: docker-compose logs --tail=200
      - name: teardown
        if: always()
        run: make down
```

### 8. Verify locally + commit

Smoke-run each deliverable in sequence. Hand-run `make e2e` once on
this branch before merging to confirm the full flow really is one
command.

---

## Acceptance criteria

This sprint is done when **every one of these** is true:

- [ ] `make setup && make e2e` exits 0 on a clean clone with only
      `ANTHROPIC_API_KEY` set in `.env`
- [ ] Cypress full-flow spec passes
- [ ] HTTP smoke passes
- [ ] CI workflow runs green on a PR
- [ ] CLAUDE.md §18 (Deployment) updated to point at the
      compose flow + the new `make` targets
- [ ] SPRINT_E2E.md marked "shipped" at the top

---

## What's deliberately out of scope

- Native plugin testing (camera, push) — Detox; see
  `mobile/CYPRESS_CAPACITOR.md`
- Real-Razorpay payment flow with money — sandbox only
- GPU worker (wav2lip) — left out; the compose stack runs cartoon-only
- Load / soak testing — separate sprint
- Multi-page video stitching e2e — covered by existing unit tests +
  on-demand `/jobs/{id}/combined.mp4`

---

## Risk + mitigations

| Risk | Mitigation |
|---|---|
| Anthropic key cost in CI | Cap the smoke to ONE lesson generation; skip nightly run on weekends |
| Postgres flakiness in CI | Healthcheck + 30s timeout before failing |
| MinIO storage interference between runs | `make down` always removes the volume (`-v` flag) |
| Cypress flake on the upload step | `cy.intercept` mocks the Claude call when no key is set; real key when set |
| Cold compose start time | `actions/cache` for the Postgres image; ~30s saving on subsequent runs |
