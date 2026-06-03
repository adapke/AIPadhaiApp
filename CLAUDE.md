# AIPadhaiApp — CLAUDE.md

Primary reference for AI-assisted development on this codebase.
Read this before touching any file.

---

## 1. What This Is

AI Pathshala (AIPadhaiApp) is a FastAPI-based EdTech platform for Indian
students. It generates AI-powered explanatory videos from textbook images,
and hosts seven interactive learning modules: Voice Tutor, Live Lecture,
Essay Grader, Math Vision, Mock Interview, Adaptive Practice, and Practice
Tests. It targets NEET/JEE/UPSC/CBSE audiences and runs in Hindi + 9
other Indian languages.

---

## 2. Repo Layout

```
padhai/           Core Python package — all backend + frontend in here
  web.py          Main FastAPI app (~12 000 lines). Routes + SPA HTML.
  home_ui.py      /home and /ui routes — goal-led student home screen.
  ui_pages.py     Shared HTML page templates (notes, flashcards, etc).
  auth.py         JWT auth, bcrypt passwords, tier enforcement.
  dpdp.py         DPDP Act 2023 §9 parental consent module.
  essay_grader.py Essay/answer grader with rubric matching.
  mock_interview.py  AI mock interview with turn-by-turn scoring.
  practice_test.py   Adaptive practice test generator.
  adaptive_packs.py  Personalised topic packs with signal-based weighting.
  doubt_clearing.py  Doubt Q&A (Postgres psycopg backend).
  spaced_repetition.py  SM-2 flashcard engine (Postgres psycopg backend).
  db.py           PostgresJobStore, SQLiteJobStore, get_db_url.
  jobs.py         JobRunner — async render pipeline.
  tts.py          TTS provider routing (Piper / Bhashini / ElevenLabs).
  avatar.py       Talking-head routing (cartoon / wav2lip / D-ID / HeyGen).
  pedagogy.py     Lesson generation, board guidance, language support.
  personalization.py  User profile, video modes, regeneration.
  cache.py        Video/lesson cache (S3 or local disk).
  llm_cache.py    Prompt caching via Anthropic's cache_control API.
  rate_limit.py   Token-bucket rate limiter.
  ingest.py       Image / PDF ingestion.
  dpdp.py         DPDP compliance — parental consent lifecycle.

admin/            Standalone admin Flask app (separate port/Dockerfile).
  app.py          Admin dashboard — jobs, users, rubrics, consent outbox.
  auth.py         Admin JWT auth (ADMIN_JWT_SECRET env var).

scripts/
  run_local.sh    Start dev server on Linux/macOS (PID-file, .env load).
  stop_local.sh   Stop dev server on Linux/macOS.
  run_local.ps1   Start dev server on Windows PowerShell.

cypress/          End-to-end tests (Cypress 13).
  e2e/            Spec files 01–13.
  support/        commands.js — shared cy.apiSignup / cy.apiLogin helpers.

db/changesets/    Liquibase SQL migrations for Postgres.
  001_core_schema.sql   users, lessons, audio_clips, videos, jobs tables.

tests/            Pytest unit tests.
  conftest.py, test_auth.py, test_health.py, test_rate_limit.py, test_uploads.py
```

---

## 3. Running Locally

```bash
# 1. Copy env file and fill in ANTHROPIC_API_KEY at minimum
cp .env.example .env

# 2. Generate a JWT secret (required — server refuses to start without it)
python3 -c 'import secrets; print(secrets.token_urlsafe(48))'
# paste result into .env as PADHAI_JWT_SECRET=...

# 3. Start (Linux/macOS)
bash scripts/run_local.sh          # defaults to port 8000
bash scripts/stop_local.sh         # stop it

# Windows
powershell -ExecutionPolicy Bypass -File scripts\run_local.ps1

# Or directly
python -m uvicorn padhai.web:app --host 0.0.0.0 --port 8000 --log-level info

# Health check
curl http://localhost:8000/healthz
```

**Dev mode**: set `PADHAI_REQUIRE_AUTH=0` in `.env` to allow anonymous
access. Default is `1` (auth required).

**SQLite auto-mode**: if `DATABASE_URL` is not set, all stores use
`padhai.db` in the repo root — no Postgres needed for local dev.

---

## 4. Environment Variables

| Variable | Required | Default | Notes |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | For AI features | — | All Claude-powered features fail without this |
| `PADHAI_JWT_SECRET` | Always | — | Server refuses to start if unset; blocked in prod if placeholder |
| `ADMIN_JWT_SECRET` | For admin | — | Admin console uses separate secret |
| `PADHAI_REQUIRE_AUTH` | No | `1` | Set `0` for anonymous dev access |
| `DATABASE_URL` | No | SQLite | `postgresql://user:pass@host:5432/padhai` for Postgres |
| `APP_ENV` | No | — | Set `production` to enable prod-mode secret checks |
| `PADHAI_SKIP_DOTENV` | No | `0` | Set `1` in tests to skip `.env` loading |
| `PADHAI_JWT_TTL` | No | 7 days | Token TTL in seconds |
| `HEYGEN_API_KEY` | For video | — | M4b tier avatar |
| `DID_API_KEY` | For video | — | M4a tier avatar |
| `TAVUS_API_KEY` | For video | — | M4c tier avatar |
| `ELEVENLABS_API_KEY` | For voice | — | Premium TTS |
| `BHASHINI_API_KEY` | For voice | — | Indic language TTS |
| `S3_BUCKET` | No | local disk | Cache storage |
| `S3_ENDPOINT_URL` | No | — | Cloudflare R2 or S3 endpoint |
| `SMTP_HOST` | No | — | Without SMTP, consent emails go to admin outbox table |
| `PADHAI_ESSAY_GRADER_MODEL` | No | `claude-sonnet-4-6` | Override essay model |
| `PADHAI_MOCK_INTERVIEW_MODEL` | No | `claude-haiku-4-5-20251001` | Override interview model |
| `PADHAI_PRACTICE_MODEL` | No | `claude-haiku-4-5-20251001` | Override practice test model |

**Secret validation**: `auth.py:_jwt_secret()` rejects secrets containing
`dev-`, `change-me`, `CHANGE_ME`, `secret-change`, or `placeholder` when
`APP_ENV=production`. In dev it emits a warning only.

---

## 5. Architecture

### FastAPI App (`padhai/web.py`)

Single `FastAPI` instance `app`. Key route groups:

| Prefix | Purpose |
|---|---|
| `/` | Service metadata JSON |
| `/health`, `/healthz` | Readiness probe (returns `git_sha`, `db`, `db_status`, `queue_backend`) |
| `/api/ai-status` | AI feature flags — called by SPA on load |
| `/ui-legacy` | Legacy SPA (single-page app embedded in `_INDEX_HTML`) |
| `/ui`, `/home` | New goal-led home screen (`home_ui.py`) |
| `/auth/signup`, `/auth/login` | Local auth endpoints |
| `/auth/sso/*` | SSO provider integration stubs |
| `/auth/parent-consent*`, `/auth/parent-link/*` | DPDP parental consent flow |
| `/lessons` | POST: multipart upload → video generation job |
| `/jobs/{id}` | Job status polling |
| `/api/v2/video-requests` | Video studio pipeline |
| `/api/uploads` | File upload + analysis |
| `/api/orgs/*` | School/org management (members, classes, assignments, attendance) |
| `/api/parents/*` | Parent–child account linking |
| `/api/notifications/*` | Notification system |
| `/explain`, `/explain/video` | Concept explainer |
| `/curriculum/*` | Curriculum mapping |
| `/chat/{lesson_id}` | Lesson Q&A chat |
| `/metrics` | Prometheus-compatible metrics |

### SPA Frontend (embedded in `web.py`)

The entire student-facing UI is a single HTML string (`_INDEX_HTML`)
served at `/ui-legacy`. It is a vanilla JS SPA with no build step.
Modules are shown as panels that swap in/out; no page navigation.

**Token key**: `pathshala_token` in `localStorage`. This is the canonical
key used everywhere. `pathshala_email` stores the user's email.
**Do not use** `padhai_token` — that was a bug that has been fixed.

Other `localStorage` keys (non-auth, safe to wipe in tests):
- `padhai_role` — `student` | `teacher`
- `padhai_goal` — selected study goal chip
- `padhai_streak` / `padhai_last_visit` — streak tracking
- `padhai_notes_*` — offline notes per lesson

### Auth Flow

1. User submits signup form → `POST /auth/signup` → bcrypt hash stored →
   JWT issued (`HS256`, 7-day TTL) → `{ token, email }` returned
2. Token stored in `localStorage.pathshala_token`
3. All authenticated requests send `Authorization: Bearer <token>`
4. `make_current_user_dependency()` validates token → looks up user →
   raises 401/403 as needed
5. DPDP: if DOB indicates user is under 18 (`MINOR_AGE_THRESHOLD = 18`),
   account is locked until parent consent token is verified

### Job Pipeline

`POST /lessons` → immediate 202 with `{job_id, status_url}` →
`GET /jobs/{job_id}` polling → `GET /jobs/{job_id}/video` when done.

Jobs run in a background thread (dev) or Redis/RQ worker (prod).
Cache is checked before creating any job — popular pages return in <10ms.

---

## 6. Subscription Tiers

Defined in `auth.py:TIER_TO_PROVIDER`. Server-side enforced — clients
cannot request a higher-tier provider than they've paid for.

| Tier | Provider | Notes |
|---|---|---|
| M1 | cartoon | Free / anonymous |
| M2 | cartoon | Premium voice (Bhashini/ElevenLabs) |
| M3 | wav2lip | Lip-sync avatar |
| M4a | d-id | Photo-real avatar |
| M4b | heygen | Photo-real avatar |
| M4c | tavus | Photo-real avatar |
| M4d | synthesia | Photo-real avatar |
| M4e | deepbrain | Photo-real avatar |

`resolve_provider_for_tier(user)` is the single source of truth.
`None` user (anonymous) → `cartoon`.

---

## 7. Module API Shapes (Critical for Frontend)

### Essay Grader (`essay_grader.py`)

`grade()` returns `GradeResult` with:
```python
by_criterion: dict[str, dict]
# Shape: {"CriterionName": {"score": N, "weight": W, "feedback": "..."}}
```
**Frontend must convert dict → array** before rendering:
```javascript
const byC = grade.by_criterion || {};
const criteria = Array.isArray(byC)
  ? byC
  : Object.entries(byC).map(([name, v]) => ({ name, ...v }));
```
Default rubrics are seeded at startup via `seed_default_rubrics()` —
12 rubrics covering UPSC, JEE, CBSE, NEET (descriptive), CAT VA, etc.

Valid exam keys for rubric lookup:
`upsc_mains`, `jee_adv_descriptive`, `cbse_class10_eng`,
`neet_descriptive`, `upsc_essay`, `cat_va`, `generic`

### Mock Interview (`mock_interview.py`)

`end()` response shape:
```python
{
  "criteria_avg": {"CriterionName": avg_score, ...},  # dict, not array
  "summaries": ["per-turn summary", ...],
  "top_improvements": ["tip 1", "tip 2", "tip 3"],
}
```
**Frontend must convert `criteria_avg` dict → array**:
```javascript
const critDict = fb.criteria_avg;
const crit = critDict
  ? Object.entries(critDict).map(([name, avg]) => ({ name, avg }))
  : [];
const summaries = fb.summaries || (fb.summary ? [fb.summary] : []);
const tips = fb.top_improvements || fb.improvements || [];
```

### Adaptive Packs (`adaptive_packs.py`)

`personalised_topic_view()` returns topics with fields:
```python
t.title            # display name (NOT t.topic)
t.adjusted_weightage  # numeric weight (NOT t.mastery)
t.base_weightage
t.topic_code
```
**Frontend must use `t.title` and `t.adjusted_weightage`** — using
`t.topic` or `t.mastery` returns `undefined`.

### Practice Tests (`practice_test.py`)

`generate()` → `PracticeTest` → `submit()` → score + per-question feedback.
Model: `claude-haiku-4-5-20251001` (env override: `PADHAI_PRACTICE_MODEL`).

---

## 8. AI Feature Status (`/api/ai-status`)

The SPA calls this endpoint on load. Response shape:
```json
{
  "anthropic_configured": true/false,
  "video_configured": true/false,
  "features": {
    "voice_tutor": bool,     // requires Anthropic key; no fallback
    "live_lecture": bool,    // requires Anthropic key; no fallback
    "essay_grader": true,    // heuristic fallback works without key
    "math_vision": bool,     // requires Anthropic key; no fallback
    "mock_interview": true,  // heuristic fallback always works
    "adaptive_practice": true, // rule-based; works without key
    "practice_tests": true,  // placeholder mode without key
    "ai_synthesis": bool,
    "lesson_generation": bool
  },
  "degraded_without_ai": ["essay_grader", "mock_interview", "practice_tests"]
}
```
`showAiNote(statusElId, featureKey)` in the SPA reads this and either
shows an error (non-functional) or a "basic mode" note (degraded).

`requireAuthOrPrompt()` gates all authenticated module actions — it opens
the auth modal instead of calling the API when no token is present.

---

## 9. DPDP Compliance (India)

**DPDP Act 2023 §9**: a "child" is any person under **18** (not 13 — India
does not adopt COPPA's 13-year carve-out).

- `dpdp.py:MINOR_AGE_THRESHOLD = 18`
- Client-side age gate: `age < 18` (not `< 13`)
- All error messages, comments, Privacy Policy text reference "under 18"
- Under-18 accounts: created but `account_locked = 1` until parent consent
- Consent token TTL: 7 days; single-use; deletes on redemption
- No behavioural tracking on locked (minor) accounts
- Consent email goes to `parent_consent_outbox` table when SMTP not wired

---

## 10. Database

### Dev (default — no `DATABASE_URL`)

SQLite at `padhai.db` in repo root (or `~/.padhai/jobs.db` for the
DPDP module). Auto-created on startup.

### Production (`DATABASE_URL` set)

Postgres via `psycopg` (v3). **Critical**: always pass
`options="-c search_path=public"` to `psycopg.connect()` — without this,
schema migrations fail with "no schema selected" on fresh databases.

**Any new `psycopg.connect()` call must include this option.**

Postgres migrations managed by Liquibase at `db/changesets/001_core_schema.sql`.
The first changeset always runs `SET search_path TO public`.

### Tables

| Table | Owner module | Notes |
|---|---|---|
| `users` | `auth.py` | UUIDs, bcrypt hash, tier, level, account_locked |
| `lessons` | `db.py` | Image-hash keyed cache |
| `audio_clips` | `db.py` | TTS cache |
| `videos` | `db.py` | Rendered video cache |
| `jobs` | `db.py` | Async render pipeline |
| `usage_daily` | `db.py` | Per-user usage accounting |
| `essay_rubrics` | `essay_grader.py` | Board-specific rubrics |
| `essay_submissions` | `essay_grader.py` | Student submissions + AI scores |
| `parent_consent_tokens` | `dpdp.py` | Single-use verification tokens |
| `parent_consent_outbox` | `dpdp.py` | Email stubs for dev |

---

## 11. AI Models Used

| Module | Default model | Env override |
|---|---|---|
| Essay grader | `claude-sonnet-4-6` | `PADHAI_ESSAY_GRADER_MODEL` |
| Mock interview | `claude-haiku-4-5-20251001` | `PADHAI_MOCK_INTERVIEW_MODEL` |
| Practice tests | `claude-haiku-4-5-20251001` | `PADHAI_PRACTICE_MODEL` |
| Lesson generation | `claude-sonnet-4-6` | via `pedagogy.py` |

**Model ID format**: always use the full ID including date suffix, e.g.
`claude-haiku-4-5-20251001`, `claude-sonnet-4-6`. Never use bare
`claude-haiku-4-5` (invalid since the 2025-10 rename).

All Claude calls should use `llm_cache.py:with_caching()` to enable
Anthropic prompt caching (`cache_control`) for the system prompt block.

---

## 12. Auth Module (`auth.py`)

Key types:
```python
@dataclass
class AuthUser:
    id: str
    email: str
    subscription_tier: str    # "M1" .. "M4e"
    subscription_level: str   # "L1" .. "L5"
    account_locked: bool = False
```

Key functions:
- `hash_password(plain)` / `verify_password(plain, hashed)` — bcrypt rounds=12
- `issue_token(user_id)` → JWT string (HS256, 7-day TTL)
- `decode_token(token)` → user_id or None
- `resolve_provider_for_tier(user)` → provider string
- `make_current_user_dependency(repo_or_getter)` → FastAPI dependency

`UserRepository` protocol implemented by:
- `SQLiteUserRepository(db_path)` — default for dev
- `PostgresUserRepository(pool)` — used when `DATABASE_URL` is set

---

## 13. Tests

### Unit tests (pytest)
```bash
pip install -r requirements-test.txt
PADHAI_SKIP_DOTENV=1 pytest tests/ -v
```

### E2E tests (Cypress)
```bash
npm install
# Server must be running on port 8000 with PADHAI_REQUIRE_AUTH=0
npx cypress open      # interactive
npx cypress run       # headless
```

Cypress spec files:
- `01-health` — `/healthz` probe
- `02-auth-api` — signup/login via API
- `03-auth-modal` — auth modal UI, DPDP under-18 parent email toggle
- `04-navigation` — SPA tab switching
- `05-video-studio` — video request flow
- `06-upload-api` — file upload
- `07-flashcards` — flashcard SRS
- `08-notes` — lesson notes
- `09-profile-export` — user profile export
- `10-curriculum-ui` — curriculum view
- `11-rate-limit` — rate limit headers
- `12-accessibility` — a11y checks
- `13-board-exam-wiring` — board exam selector

Custom commands (`cypress/support/commands.js`):
- `cy.apiSignup(email, password)` — signup via API, yields `{email, token}`
- `cy.apiLogin(email, password)` — login via API, sets `pathshala_token`

---

## 14. Known Fixed Bugs (Do Not Reintroduce)

1. **Token key mismatch** — `home_ui.py` and `ui_pages.py` previously used
   `padhai_token`; the SPA uses `pathshala_token`. Fixed. Canonical key is
   `pathshala_token` everywhere. Never use `padhai_token`.

2. **Essay grader `by_criterion` TypeError** — `grade.by_criterion` is a
   dict, not an array. The SPA converts it via `Object.entries()`. Never
   call `.map()` directly on it.

3. **DPDP age threshold** — Was `13`, now `18`. Do not revert.

4. **`neet_pg` rubric key** — The exam key `neet_pg` does not exist in
   rubrics. Correct key is `neet_descriptive`.

5. **Mock interview `criteria_avg`** — Response field is a dict, not an
   array. Frontend converts via `Object.entries()`.

6. **Adaptive pack field names** — Topics use `t.title` (not `t.topic`) and
   `t.adjusted_weightage` (not `t.mastery`).

7. **Postgres `search_path`** — Any new `psycopg.connect()` call must pass
   `options="-c search_path=public"`.

8. **Claude model ID** — `claude-haiku-4-5` is invalid; use
   `claude-haiku-4-5-20251001`.

9. **Signup `terms_accepted`** — Auth form must include an explicit
   `terms_accepted` checkbox. Hidden auto-accept is bad UX and was reverted.

---

## 15. Dev Server Scripts

| Script | Platform | Purpose |
|---|---|---|
| `scripts/run_local.sh [port]` | Linux/macOS | Start uvicorn, manage PID file, load `.env` |
| `scripts/stop_local.sh [port]` | Linux/macOS | Stop server gracefully via PID file |
| `scripts/run_local.ps1 [port]` | Windows | Same as run_local.sh but PowerShell |

All scripts wait for `/healthz` to return 200 before exiting (up to 7.5s).
PID file: `.padhai_server.pid`. Log file: `padhai_server.log`.

---

## 16. P1 Work Status

Reviewed 2026-06-03. Re-audit before changing.

### Done

- **Provider validation at startup** — `_PROVIDER_KEY_SPECS` in
  `padhai/web.py:505-570` validates 16 provider keys (Anthropic, HeyGen,
  D-ID, ElevenLabs, Sarvam, Bhashini, Razorpay, etc.) by prefix + length
  + placeholder detection. `_validate_provider_keys()` runs in the
  FastAPI lifespan at line 940. Fails hard in `APP_ENV=production`,
  warns in dev.
- **Payments (Razorpay)** — `razorpay_client.py` wired in `web.py`:
  `POST /api/payments` creates orders + verifies signatures (lines
  13008-13038), `POST /api/webhooks/razorpay` (line 13049) handles
  webhook events. Subscription tier upgrades flow through `auth.py`.
- **RAG citations — tutor + lesson** — `padhai/tutor.py` records
  provenance via `tutor_grounding.send_grounded_message()` →
  `citations.record_answer()`. `padhai/pedagogy.py:generate_lesson()`
  now accepts `user_id` / `source_upload_id` / `source_page_number` and
  calls `_record_lesson_provenance()` after generation. Web/render job
  threads `user_id` and `upload_id` from the job payload into both.
- **Exam taxonomy → lesson filter** — `exam_taxonomy.taxonomy_scope_for_user()`
  resolves a user's most-recent active enrollment to `(exam_code, board_hint,
  chapter_titles, scope_summary)`. `pedagogy.generate_lesson()` auto-fills
  `board_hint` from the scope and injects `Syllabus scope: …` into the
  prompt via `build_user_text(taxonomy_scope=…)`.
- **Accuracy benchmark CI** — `tests/fixtures/golden_answers.json`
  carries 12 seed items across CBSE / JEE / NEET / UPSC / Maharashtra.
  `scripts/run_accuracy_bench.py` drives `accuracy_bench.run_benchmark()`
  in structural mode (every PR, stub runner, no Anthropic key) and live
  mode (nightly cron or `workflow_dispatch`, real Claude calls).
  `.github/workflows/accuracy-bench.yml` is the wiring.
- **Mobile shell wiring** — `mobile/scripts/configure-server.cjs` reads
  `CAPACITOR_SERVER_URL` (default `http://10.0.2.2:8000` for Android
  emulator) and rewrites `server.url` + `cleartext` + `androidScheme`
  across the student / parent / teacher Capacitor configs.
  `mobile/package.json` npm scripts (`build`, `build:prod`, `android:run`,
  …) invoke it before `cap sync`. Cypress smoke at
  `cypress/e2e/15-mobile-shell.cy.js` hits the three shell entry URLs.
- **LLM cost in admin** — `/admin/llm-costs` (and JSON sibling
  `/admin/api/llm-costs`) reads from `llm_calls` via
  `admin/data.py:llm_cost_stats()`. UI in `admin/templates.py:render_llm_costs()`
  with 24h / 7d / 30d window chips, by-module + by-model tables, and the
  top 10 users by spend. Admin still doesn't import `padhai.*` — the
  schema is the contract.

### Still pending / next up

1. **RAG citations — surface coverage.** Tutor + lesson record citations;
   Essay Grader, Mock Interview, and Doubt Clearing still don't. Decide
   per-surface whether `source_only` / `official` modes apply.
2. **Mobile QA depth.** Capacitor shell loads, but only one Cypress smoke
   spec covers it. Add interaction specs (login, lesson playback, offline
   notes) once a CI lane can drive the Capacitor `WebView` directly.
3. **Accuracy bench coverage.** Golden dataset is intentionally small
   (12 items) and structural-only on PRs. Expand to ≥100 items across
   UPSC / JEE / NEET / CBSE; then turn the nightly live-mode gate from
   advisory to blocking.
4. **Observability depth.** Cost page exists; per-user daily cap
   enforcement and alert thresholds are not wired yet.

---

## 17. Admin App

Separate Flask app at `admin/app.py`. Runs independently on a different
port. Uses `ADMIN_JWT_SECRET` (distinct from `PADHAI_JWT_SECRET`).

Manages: jobs queue, users, essay rubrics, parent consent outbox, signup
bootstrap. See `.idea/runConfigurations/Admin__standalone_split_out_preview_.xml`
for the IntelliJ run config.

---

## 18. Deployment

- `Dockerfile` — builds the main FastAPI app
- `admin/Dockerfile` — builds the admin app
- `render.yaml` — Render.com deployment config
- `ops/spot-launch.py` / `ops/spot-bootstrap.sh` — AWS spot instance ops

`modal_deploy.py` — Modal.com serverless deployment for GPU workers.

In production: `gunicorn` wraps uvicorn workers. Set `APP_ENV=production`
so secret checks are enforced and dev placeholders block startup.
