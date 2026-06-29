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

**SQLite auto-mode**: if `DATABASE_URL` is not set, all modules share
one SQLite file resolved by `padhai.db.sqlite_path()`:

- Env override: `PADHAI_DB_PATH` (any absolute or `~`-prefixed path)
- Default: `~/.padhai/jobs.db`

This used to differ per module (`auth.py` defaulted to `padhai.db`
while DPDP used `~/.padhai/jobs.db`) — that mismatch caused the
parent-consent 500 (cross-DB UPDATE on a missing `users` table). The
shared helper guarantees every module writes into the same file.

**Mobile dev flow** (Capacitor shells):

```bash
cd mobile
# Point the shells at your local backend (defaults to Android emulator's
# 10.0.2.2:8000 bridge; override with CAPACITOR_SERVER_URL for LAN testing)
node scripts/configure-server.cjs
npm run build              # configure + cap sync
npm run android:run        # configure + emulator launch

# Restore production URLs before a release build:
npm run build:prod         # NODE_ENV=production configure + cap sync
```

The script rewrites `server.url`, `server.cleartext` and
`server.androidScheme` in the three Capacitor configs (student,
parent, teacher). Cypress smoke for the shell entry URLs lives at
`cypress/e2e/15-mobile-shell.cy.js`.

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

### Dev / single-server (default — no `DATABASE_URL`)

Every module shares one SQLite file resolved by
`padhai.db.sqlite_path()`:

- Env override: `PADHAI_DB_PATH` (absolute or `~`-prefixed)
- Default: `~/.padhai/jobs.db`

Auto-created on startup; each module's `migrate()` runs from the
FastAPI lifespan hook.

### Production (`DATABASE_URL` set)

Postgres via `psycopg` (v3). **Critical**: always pass
`options="-c search_path=public"` to `psycopg.connect()` — without this,
schema migrations fail with "no schema selected" on fresh databases.

**Any new `psycopg.connect()` call must include this option.**

Postgres migrations managed by Liquibase at `db/changesets/001_core_schema.sql`.
The first changeset always runs `SET search_path TO public`.

### Backups (SQLite mode)

SQLite has no replication; a single-server SQLite deployment is one
disk failure from total data loss. Use the online-backup API so the
copy is consistent under concurrent writes — never just `cp` the
`.db` file mid-flight.

```bash
# Daily snapshot — runs against a live DB without blocking writers
DB="${PADHAI_DB_PATH:-$HOME/.padhai/jobs.db}"
TARGET="$HOME/.padhai/backups/jobs_$(date -u +%Y%m%d_%H%M%S).db"
mkdir -p "$(dirname "$TARGET")"
sqlite3 "$DB" ".backup '$TARGET'"

# Compress + retain the last 14 days
gzip -9 "$TARGET"
find "$HOME/.padhai/backups" -name 'jobs_*.db.gz' -mtime +14 -delete
```

Add to cron (`crontab -e`):

```cron
# Hourly online backup of the local SQLite DB
17 * * * * /usr/local/bin/padhai-backup.sh >> /var/log/padhai-backup.log 2>&1
```

**Restore**: stop the server, gunzip the snapshot to
`$PADHAI_DB_PATH`, restart. Schema migrations re-run idempotently on
the next boot — they're all `CREATE TABLE IF NOT EXISTS`.

For Postgres deployments, use `pg_dump` + your provider's PITR; this
SQLite procedure does not apply.

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

Constants live in `padhai/models.py` — import them rather than
hard-coding strings:

```python
from .models import HAIKU_MODEL, SONNET_MODEL, OPUS_MODEL
```

| Tier | Constant | Default | Tier env override |
|---|---|---|---|
| Cheap+fast | `HAIKU_MODEL` | `claude-haiku-4-5-20251001` | `PADHAI_HAIKU_MODEL` |
| Balanced | `SONNET_MODEL` | `claude-sonnet-4-6` | `PADHAI_SONNET_MODEL` |
| Strongest | `OPUS_MODEL` | `claude-opus-4-7` | `PADHAI_OPUS_MODEL` |

| Module | Tier | Surface env override |
|---|---|---|
| Essay grader | Sonnet | `PADHAI_ESSAY_GRADER_MODEL` |
| Mock interview | Haiku | `PADHAI_MOCK_INTERVIEW_MODEL` |
| Practice tests | Haiku | `PADHAI_PRACTICE_MODEL` |
| Tutor | Haiku | `PADHAI_TUTOR_MODEL` |
| Doubt vision | Sonnet | `PADHAI_DOUBT_VISION_MODEL` |
| Math vision | Opus | `PADHAI_MATH_VISION_MODEL` |
| Upload chat / quiz / summary | Sonnet/Haiku | `PADHAI_UPLOAD_*_MODEL` |
| Lesson generation | Opus | via `pedagogy.py` MODEL |
| Moderation | Haiku | via `moderation.py` MODEL |

**Model ID format**: always use the full ID including date suffix, e.g.
`claude-haiku-4-5-20251001`, `claude-sonnet-4-6`. Never use bare
`claude-haiku-4-5` (invalid since the 2025-10 rename) — `models.py`
carries a startup assert against the bug form.

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
   `claude-haiku-4-5-20251001`. Every Claude-using module should
   import constants from `padhai/models.py` (`HAIKU_MODEL`,
   `SONNET_MODEL`, `OPUS_MODEL`) — never hard-code model strings.
   `models.py` carries a startup assert against the buggy bare form.

9. **Signup `terms_accepted`** — Auth form must include an explicit
   `terms_accepted` checkbox. Hidden auto-accept is bad UX and was reverted.

10. **`output_config.effort` rejected by Haiku** — The structured-output
    surfaces (explainer / KG-lesson / flashcards / recap) set a reasoning
    `effort` knob inside `output_config`, but route to different tier
    models. Haiku 4.5 returns 400 "This model does not support the effort
    parameter." — which surfaced as a 500 on `POST /explain`
    (`EXPLAINER_MODEL = HAIKU_MODEL`). Fixed centrally in
    `llm_call._create_with_effort_fallback`: on that specific 400, strip
    `effort` and retry once (a 400 bills no tokens, so the retry is free).
    Model-agnostic — no capability table to drift. Don't re-add a raise on
    the first failure. Guard: `tests/test_llm_call_effort_fallback.py`.

11. **Capped AI routes must thread `user_tier`** — `daily_cap_paise(None)
    == 0` (same as free-tier M1), so any route that calls a capped module
    (`essay_grader.grade`, `mock_interview.submit_answer`,
    `practice_test.generate`) WITHOUT passing `user_tier=user.subscription_tier`
    makes `check_daily_cap` treat EVERY user — even uncapped M4a — as free
    tier, raising `BudgetExceeded('premium_feature')` and silently
    degrading to the heuristic (essay scored a flat 0.0, mock used the
    keyword heuristic, practice skipped synthesis). The `routers/learning.py`
    slice dropped the kwarg during extraction from web.py; the `v3.py`
    copies kept it. Always pass the real tier. `practice_test.generate`
    grew a `user_tier` param so the route can forward it. Guard:
    `tests/test_tier_threading.py`.

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
- **Payments (Razorpay)** — `razorpay_client.py` is surfaced by the
  pricing router: `POST /api/pricing/checkout`
  (`padhai/routers/pricing.py`) creates a Razorpay order for a tier and
  degrades gracefully when keys are absent; `GET /api/pricing/plans`
  exposes the tier ladder + `razorpay_configured`. `POST
  /api/webhooks/razorpay` (`web.py`) handles webhook events (both fee +
  subscription). Subscription tier upgrades flow through `auth.py`.
  (There is no `/api/payments` route — that was a stale reference;
  checkout lives at `/api/pricing/checkout`.)
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

### Also done since last review

- **RAG citations — Essay / Mock Interview / Doubt.** All three now
  call `citations.record_answer()` with the right surface. Essay +
  mock-interview record `grounded=False` (no source kind for rubrics);
  doubts cite the student-snapped `image_url` as a `source_kind='upload'`
  citation when present. New `mock_interview` surface added to
  `citations.VALID_SURFACES`.
- **Per-user daily LLM cost cap.** `llm_obs.check_daily_cap()` enforces
  `DAILY_COST_CAPS_BY_TIER` (M1=0 → premium-only; M2=₹20/day; M3=₹100/day;
  M4*=uncapped). Wired into tutor, essay, mock-interview, doubt, lesson,
  explainer, and practice. Pairs with `BudgetExceeded` exception so
  callers fall back to heuristic / "human-tutor will follow up" copy.
- **LLM cost alert thresholds.** New `llm_alerts` table — `record_call`
  emits one row per (user, day, bucket) at 80% and 100% of cap. Bucket
  threshold tunable via `PADHAI_LLM_ALERT_PCT`. `llm_obs.recent_alerts()`
  for the admin dashboard.
- **Shared SQLite path.** `padhai.db.sqlite_path()` — every module
  writes into the same file (`PADHAI_DB_PATH` env override → `~/.padhai/jobs.db`).
  Closes the class of bug that caused the DPDP consent crash.
- **Production-mode admin gate safeguard.** Startup now refuses to boot
  when `APP_ENV=production` with neither `DATABASE_URL` nor
  `PADHAI_SUPERUSER_EMAILS` set — the combination that silently grants
  admin to every signed-in user via `require_admin_role`'s dev fallback.
- **Pytest cookie isolation.** Autouse `_isolate_client_cookies` fixture
  in `tests/conftest.py` clears the session-scoped `TestClient` cookie
  jar before every test. Stops `pathshala_token` from leaking across
  tests and masking "requires auth" assertions.
- **LLM cost alerts in admin UI.** `/admin/llm-costs` shows a
  "Users approaching / over budget" table sourced from
  `admin/data.py:llm_recent_alerts()`. Tier + bucket% + spent/cap
  per row with a summary chip ("N approaching, M blocked").
- **Modules consolidated on shared SQLite path.** All 74 modules in
  `padhai/` now delegate `_db_path()` to `padhai.db.sqlite_path()`
  instead of duplicating the env-lookup boilerplate. Two-line
  one-time replacement; consistent default in every module forever.
- **Central LLM-call wrapper.** New `padhai/llm_call.py` collapses
  `client.messages.create` + `llm_obs.record_call` + cap pre-flight
  into one helper (`call_claude()`). New surfaces start automatically
  cost-tracked; existing surfaces can migrate opportunistically.
- **Multi-page video stitching.** `GET /jobs/{leader_id}/combined.mp4`
  ffmpeg-concats every ready sibling page into one MP4, cached on
  disk keyed by the participating job ids so partial bundles don't
  shadow later full ones. `GET /jobs/{id}/combined` returns the JSON
  status ("3 of 5 pages ready"). `JobStore.find_siblings(leader_id)`
  is the underlying SQL — uses sqlite's json_extract on the payload.
- **Accuracy bench dataset expanded.** Golden answers grew from
  12 → 43 items across CBSE (Class 6-12), ICSE, Maharashtra, TamilNadu,
  Karnataka, JEE, NEET, UPSC, SSC. Five subject domains: math,
  physics, chemistry, biology, polity/geography/history/gk.
- **Mobile interaction Cypress specs.** `cypress/e2e/16-mobile-interactions.cy.js`
  drives the SPA from the three shell entry URLs — checks the
  sign-in affordance is present, mode=parent/teacher query params
  flow into `localStorage.padhai_role`, and PWA-cacheable endpoints
  (`/manifest.json`, `/api/ai-status`) return the shape the shells
  cache for offline boot.
- **SQLite backup procedure.** §10 now documents the sqlite3
  online-backup pattern (`sqlite3 … ".backup"`) with a cron example
  and 14-day retention. Closes the "single-server one-disk-failure
  away from data loss" gap.

### Also done since last review

- **prod-192 — SAT (US Digital SAT) exam section.** First non-India exam.
  Full vertical: new `padhai/routers/sat.py` serves a public `/sat` hub
  (accurate Digital-SAT details, 13 oembed-verified prep videos in
  topic tabs, 24 interactive flip-flashcards, and an inline practice
  test wired to `POST /api/practice/generate` (exam="sat") + `/submit`
  with an **estimated 200–800 section score** on results). Backend
  wiring: `"sat"` in `practice_test.VALID_EXAMS`; College Board body +
  `sat` exam + 8 official content-domain topics + `_EXAM_TO_BOARD_HINT`
  in `exam_taxonomy`; 64 hand-written SAT-style questions seeded into
  `question_bank` (board="sat", subjects `sat_math` / `sat_reading_writing`,
  grade=0) via `data/pyq/sat_2024_{math,reading_writing}.json` — so the
  practice test fills from the bank and works for **free (M1) users**
  (no Claude synthesis needed); 14 SAT videos seeded into `concept_videos`
  (board="SAT") + exported to `data/concept_videos_seed.json`
  (110 → 124 verified). **Cross-surface mapping** (SAT now appears
  everywhere boards/exams do, like CBSE/JEE/NEET): `/syllabus` browser
  (Math + R&W topic tree), onboarding target-exam picker, home
  `EXAM_DATES`/`EXAM_LABELS` countdown, SPA goal picker (new
  "International" optgroup) + `_goalMeta`, and a landing exam chip
  linking to `/sat`. Deliberately skipped the `/curriculum` board
  dropdown (grade 6–12 + no `curriculum_topics` rows → would be empty
  for a grade-0 exam). **US-market content current to 2026–27:** 98 q ·
  2 h 14 m, 400–1600 scoring + percentile context (1050/1300/1500),
  test dates Aug–Dec 2026 + Mar/May/Jun 2027, fee $68/$111/+$38 + waivers,
  Bluebook (timer-pause-on-exit), Desmos scientific↔graphing toggle,
  Spring-2026 Math TTS/screen-reader, superscore/Score Choice/SSD,
  PSAT/NMSQT, official Khan Academy + Bluebook full-length pointers; no
  essay (discontinued 2021 — correctly not added). **Also fixed an
  app-wide latent bug:** the CSP had no `frame-src`, so it fell back to
  `default-src 'self'` and the browser blocked *all* YouTube embeds
  (the existing `/concept` videos too) — added
  `frame-src https://www.youtube.com https://www.youtube-nocookie.com`.
  Tests: new `tests/test_sat.py` (7) — hub renders + US-market markers,
  `sat` in VALID_EXAMS, taxonomy seeded, PYQ import (40), bank-backed
  practice generate+submit scores, SAT videos ship in seed. Endpoint
  map 788 → 789 (+1 PUBLIC `/sat`). **In-app full-length mock** (two
  timed sections, R&W then Math → combined 400–1600 estimate, both
  drawn from the bank) + the **full College Board syllabus** now render
  on the hub itself — no redirect to College Board; the official
  Bluebook full-lengths are only a mentioned option, not a link-out.
  Honest gaps: the in-app mock is ~49 q (27 R&W + 22 Math) vs the
  official 98 (the bank keeps growing); per-question answer
  explanations are the next enhancement.

- **prod-141..146 — SPA wiring + hand-curated content seed.**
  CK-12 patterns from prod-135..140 were API-only at ship; this
  sprint surfaces them as actual user-visible pages and seeds the
  Real-World Examples catalog so `/concept/{slug}` SEO pages have
  real content from day 1.

  - **prod-141** — `/mastery` page in `padhai/routers/mastery_page.py`.
    Server-rendered color-coded grid of the student's mastery (green
    ≥0.7 + fresh / yellow ≥0.4 or stale / red <0.4 or decayed /
    untouched). Subject-filter chips. Defaults to CBSE Class 10
    when no enrollment exists. Anonymous → friendly sign-in landing
    (no 401 ugly response).

  - **prod-142..145** — Four small server-rendered pages in
    `padhai/routers/ck12_ui_pages.py` (one router slice covering
    the related views):
    - `/tutor-modes` — 6 colored chips for the prod-136 modes,
      with bilingual labels. JS opens a tutor session + POSTs the
      test message with `mode=<key>`.
    - `/memory-boost` — daily 3-question pack with streak card.
      Each question shows the bucket (critical / warmup / fresh),
      subject, chapter, options. ✓/✗ buttons POST to the
      /answer endpoint + show the updated streak.
    - `/teacher/class/{class_id}/heat-map` — students × topics
      grid with color-coded mastery cells (% per cell). Reuses
      the prod-140 endpoint internally. Graceful 503 when org
      tables aren't migrated yet (instead of 500).
    - `/admin/examples-queue` — curator inbox showing pending
      `concept_examples` with ✓/✗ inline approve/reject buttons.
      Admin-gated via `require_admin_role`.

  - **prod-146** — `scripts/seed_real_world_examples.py` inserts
    **15 hand-written India-rooted examples** directly as
    `approved` across 11 concepts (Newton's First Law, Photosynthesis,
    Gravity, Acids & Bases, Ohm's Law, Pythagoras, Light Reflection,
    Work-Energy-Power, Cell Structure, Real Numbers, Quadratic
    Equations, Simple Interest). Each example is 300-900 chars,
    references at least one Indian-context token (Mumbai locals,
    kabaddi, monsoon, kirana shops, mid-day meal, autorickshaw,
    ₹, NCERT). Western-context tokens (baseball, Thanksgiving,
    freeway) are explicitly tested-against to defend the contract.
    Idempotent — re-running skips duplicates by body match.
    **No Claude budget burnt** — pre-written human content.

  Tests: 24 new across 3 files (`test_mastery_page.py` ×5,
  `test_ck12_ui_pages.py` ×9, `test_seed_real_world_examples.py` ×6).
  Coverage: anonymous landing pages render, authed pages return
  expected content, error pages degrade gracefully (no 500s),
  Indian-context tokens enforced.

  Total endpoints: 781 → 786 (+5 server-rendered pages — `/mastery`,
  `/tutor-modes`, `/memory-boost`, `/teacher/class/{cid}/heat-map`,
  `/admin/examples-queue`).

  Honest gaps:
  - **Memory Boost daily push notifications** still not wired.
    The cron + FCM fanout is the missing piece — the UI is ready
    to consume but engagement-nudge automation is the lever.
  - **15 hand-curated examples cover 11 concepts** — only a small
    fraction of the ~70 concept_videos catalog. Curator could
    write ~50 more in a day to fully cover the verified-tier
    videos.
  - **No mobile-shell deep links** into the new pages yet —
    Capacitor shells still launch at `/?home=math`. A follow-up
    sprint could add `aipathshala://memory-boost` and
    `aipathshala://mastery` schemes.
  - **/tutor-modes is a standalone demo**, not wired into the
    existing /tutor SPA. The legacy SPA tutor surface needs a
    separate refactor pass to host the chips inline.

- **prod-140 — Class Heat Map (CK-12 Teacher Assistant pattern).**
  New router `padhai/routers/class_heat_map.py` exposes two
  teacher-only endpoints under the existing org role gate:
  - `GET /api/orgs/{org_id}/classes/{class_id}/heat-map?board=CBSE&grade=10[&subject=Math]`
    returns a students × topics matrix with mastery cells.
    Each cell carries `{mastery, color_state, decay_state}`.
    Class roll-up summary included.
  - `GET /api/orgs/{org_id}/classes/{class_id}/heat-map/weak-topics`
    returns top-N topics ranked by class weakness score
    `(red*2 + yellow + untouched*0.5) / (n_students*2)` — the
    "what should I re-teach tomorrow?" feed.

  Auth: caller must be a member of `org_id` with role `admin` or
  `teacher`. Reuses prod-135 mastery aggregator + existing
  `orgs.list_members()`. **No new tables.** Pure read-side join.

  Tests: `tests/test_class_heat_map.py` (6 tests) — anonymous
  rejection, router registration, teacher happy-path with seeded
  class roster, unknown class → 404, empty class returns empty
  matrix, weak-topics endpoint auth-gated.

  Honest gaps:
  - **N+1 reads** — the aggregator runs once per student in the
    class. For a 60-student class, ~60 SQLite reads (~50ms total
    locally; would scale to ~200ms on Postgres). Caching the
    per-student mastery vector keyed on (user_id, board, grade,
    digest_of_recent_attempts) is a follow-up if the latency
    becomes user-visible.
  - **No SPA render yet** — the API is live; the teacher
    Capacitor shell still needs the colored-grid component.
  - **Class summary double-counts** topics that exist for
    multiple students — that's the desired behaviour for
    intensity scaling, but if/when we add a "total unique topics"
    field, it should be derived from the topics axis length.

- **prod-139 — Memory Boost Daily Drill (CK-12 SM-2-daily-3 pattern).**
  New `padhai/memory_boost.py` module + router. Daily 3-item pack
  surfaces:
  - **critical** — a topic in the user's red/yellow zone
  - **warmup** — a green topic (freshness check)
  - **fresh** — an untouched topic (new material)

  Sources: PYQs from `question_bank` filtered by board+grade,
  ranked against the prod-135 mastery map. New tables:
  `memory_boost_picks` (one row per pick, indexed by user_id +
  pack_date for idempotent same-day re-fetch), `memory_boost_answers`
  (responses), `memory_boost_streaks` (current/longest/last_active).

  Endpoints (all auth-required):
  - `GET /api/me/memory-boost?board=CBSE&grade=10` — today's pack
    (idempotent — same picks if called twice same day)
  - `POST /api/me/memory-boost/answer` — record response + bump streak
  - `GET /api/me/memory-boost/streak` — read-only streak feed

  Streak logic: same-day re-answer doesn't double-bump. Gap of 1
  day = streak continues. Gap of >1 day = streak resets to 1.
  IST timezone for daily reset (UTC+05:30).

  Tests: `tests/test_memory_boost.py` (13 tests) — pack creation,
  same-day idempotency, record_answer permission gate (can't
  answer someone else's pick), unknown pick_id → ValueError,
  no double-bump same day, new-user zero streak, hydrate_picks
  inflates question text, all 3 HTTP endpoints auth-gated, router
  registered, migrate() idempotent.

  Honest gaps:
  - **No flashcard / essay source** yet — only PYQs. Adding
    flashcards is a follow-up join.
  - **Pack regenerates if all PYQs deleted** between fetch and
    answer — not a real risk, but worth a guard for prod.
  - **Streak reset doesn't fire push notifications.** Daily 7am
    IST cron + FCM push to nudge users would be the engagement
    lever; out of scope for this sprint.

- **prod-138 — NCERT Standards-aligned Question Tagging (CK-12
  standards correlation).** Add `ncert_code` column to
  `question_bank` via idempotent `ALTER TABLE` migration. Code
  format: `<BOARD>.<GRADE>.<SUBJECT>.<CHAPTER>[.LO<NUM>]` —
  e.g. `CBSE.10.SCI.CH06`, `CBSE.10.SCI.CH06.LO03`.

  New helpers in `padhai/question_bank.py`:
  - `is_valid_ncert_code(code)` — regex validator
  - `set_ncert_code(qid, code)` — persist with shape check
  - `list_by_standard(prefix)` — prefix-match search
  - `count_by_standard(prefix)`
  - `ncert_coverage_stats()` — tagged/untagged/coverage_pct rollup
  - `list_untagged(limit)` — batch tagger reads from here

  New `padhai/ncert_tagger.py` — Claude Sonnet batch tagger.
  System prompt covers all 16 boards + 5 entrance exams + 17
  subject codes. Confidence threshold ('medium' or 'high'); 'low'
  → skipped (returned as null, not guessed). Per-question cost
  ~₹0.02-0.05; full 2500-PYQ tagging ~₹100.

  Endpoints:
  - `GET /api/questions/by-standard?code=CBSE.10.SCI` — public
    prefix-filter
  - `GET /api/admin/teacher-tools/ncert-coverage` — admin coverage
    stats
  - `POST /api/admin/teacher-tools/tag-questions` — admin batch
    tagger

  Tests: `tests/test_ncert_tagging.py` (12 tests) — validator
  shapes, set+get roundtrip, invalid code rejection, prefix-match
  semantics, stats arithmetic, list_untagged, HTTP public + admin
  endpoints, router registered, ALTER TABLE idempotent across
  reload.

  Honest gaps:
  - **Tagger not run on the existing 2500 PYQs yet** — that's an
    ops task with a real ANTHROPIC_API_KEY (~₹100 spend); the
    pipeline is ready.
  - **No SPA filter chip** for "filter by chapter" — needs the
    new-UI sprint to surface.
  - **NCERT-code → curriculum_objectives join** isn't enforced
    yet; tagger could output `CBSE.10.SCI.CH99` even if Class 10
    Science only has 16 chapters. A foreign-key-style validation
    pass is a follow-up.

- **prod-137 — Real-World Examples Catalog (CK-12 concept-page pattern).**
  New `concept_examples` table + curator workflow:
  - `padhai/concept_examples.py` — schema + CRUD (insert as
    pending → curator approve/reject → published)
  - `padhai/concept_examples_generator.py` — Claude Sonnet
    generates 3 India-rooted examples per concept (Mumbai locals,
    kabaddi, mid-day meal — Western contexts explicitly forbidden
    in the system prompt)
  - `padhai/routers/concept_examples_routes.py` — admin: generate
    + queue + approve + reject; public: list approved

  Wired into **prod-134's `/concept/{slug}` SEO page** — approved
  examples render as a "Real-world examples" section between the
  video and the related-concept links. Falls back from
  locale-specific to English when the locale-specific row isn't
  approved yet. Includes `_md_to_safe_html` with bold/italic +
  inline-image (with `rel=nofollow`) support.

  Endpoints:
  - `POST /api/admin/teacher-tools/generate-examples`
  - `GET /api/admin/teacher-tools/examples-queue`
  - `POST /api/admin/teacher-tools/examples/{id}/approve`
  - `POST /api/admin/teacher-tools/examples/{id}/reject`
  - `GET /api/concept-examples?slug=...&locale=en` (public)

  Tests: `tests/test_concept_examples.py` (13 tests) — full CRUD
  roundtrip, status workflow (pending → approved/rejected),
  list_for_slug never leaks non-approved to public, stats rollup,
  slug normalisation, HTTP admin gate, public list filtering,
  router registered, and end-to-end `/concept/{slug}` SEO page
  embeds the approved example markdown ("Mumbai local train"
  appears in the response body).

  Honest gaps:
  - **Catalog is empty at ship time.** A curator needs to run the
    generator on top-30 concepts and approve the best output. ~3
    minutes of curator time per concept; ~90 min for 30 concepts.
  - **No curator-UI page yet** — the queue is API-accessible but
    teachers will want a list-view + 1-click approve/reject. Next
    sprint.
  - **No image generation** — students can embed images via
    `![alt](url)` if they have a hosted URL, but the generator
    won't produce DALL-E-style images. Out of scope.

- **prod-136 — Tutor Mode Switcher (CK-12 Flexi pattern).**
  CK-12's Flexi exposes 10-12 mode-as-product chips (quiz-me,
  real-world-analogy, etc.) as system-prompt skins over the same
  model. Pathshala ships **6 India-tuned modes** in new module
  `padhai/tutor_modes.py`:

  - `quick_explain` — 90-second board-exam recall, ≤120 words
  - `jee_advanced_drill` — multi-step solutions with every line shown
  - `neet_one_liner` — MCQ-style ✓/✗ elimination
  - `cbse_board_answer` — 5-mark structured answer in marking format
  - `desi_analogy` — cricket / kabaddi / Mumbai locals / Diwali / dosa
    (Western analogies explicitly forbidden in the addendum)
  - `rural_simple` — Class 6-8 vocab for first-gen learners, Hindi-mix
    encouraged ('dhakka' instead of 'force')

  Each mode has bilingual EN/HI labels + one-line description for
  the SPA chip render. The system-prompt addendum is appended to
  the base tutor prompt via `apply_mode()` — no model swap, no
  rerun, ~free at runtime.

  Wiring:
  - `tutor.send_message(mode=...)` accepts the optional kwarg.
    Threaded through `_claude_reply` so cap pre-flight + RAG
    grounding still apply.
  - `POST /api/tutor/sessions/{sid}/message` accepts `mode` Form
    field (defaults to None — backward compat).
  - **New** `GET /api/tutor/modes` — public catalog endpoint so
    chips render before sign-in (returns the 6 modes + bilingual
    labels + icons).

  Tests: `tests/test_tutor_modes.py` (11 tests) — catalog
  completeness, addendum non-triviality (≥100 chars each),
  apply_mode contract (unknown / None / empty all pass through),
  case-insensitive lookup, distinct prompt outputs across modes
  (no two modes produce the same final system prompt), HTTP
  catalog endpoint shape, `tutor.send_message` signature
  inspection (mode kwarg present + default None), `desi_analogy`
  prompt actually references Indian-context tokens (≥3 of
  Mumbai/cricket/kabaddi/Diwali/monsoon/rupee/etc).

  Honest gaps:
  - **No SPA chip UI yet.** The catalog endpoint is live; the
    student-facing chips that POST `mode=...` need to be added
    to `/tutor` page in a follow-up sprint.
  - **No per-session mode persistence.** Each turn passes mode
    explicitly; if the SPA wants "sticky" mode across the session
    it should remember user selection client-side. A future sprint
    could persist on `tutor_sessions.preferred_mode`.
  - **No A/B measurement.** We hypothesise these modes lift
    engagement (NEET aspirants prefer one-liner, JEE prefers
    drill) but have no telemetry comparing default-mode vs
    mode-selected reply ratings. PostHog event "tutor.mode.selected"
    would close that loop.

- **prod-135 — Concept Mastery Map (CK-12 BrainFlex pattern).**
  New `padhai/mastery_aggregate.py` is a pure read-side aggregator
  that joins existing per-attempt rows (`user_topic_mastery`,
  `essay_submissions`, `practice_tests`, `flashcard_reviews`)
  against `curriculum_objectives` to return one row per curriculum
  topic with `mastery: 0-1`, `decay_state: fresh|stale|decayed|untouched`,
  `color_state: green|yellow|red|untouched`, and `source_attempts: {module: count}`
  provenance. New endpoints `GET /api/me/mastery-map?board=CBSE&grade=10[&subject=Math]`
  + `GET /api/me/mastery-map/summary` (cheap counts-only feed for
  dashboard widgets).

  Mastery formula: weighted average of cross-module signals
  (flashcard SM-2 grades weighted 1.0, practice-test percentages
  weighted 0.7 — subject-level only, essay scores weighted 0.5 —
  rubric/exam fuzzy-matched). Time-decay: half-life of 14 days
  starts after a 14-day "fresh window" — a topic at 0.85 mastery
  untouched for 28 days drops to ~0.42. Color thresholds:
  green ≥0.7 AND not-decayed, yellow ≥0.4, red below, untouched
  when no signal at all.

  No new tables. No new Claude calls. Robust to missing source
  tables (graceful empty-result fallback). The foundation for
  prod-139 (Memory Boost daily drill) + prod-140 (Class Heat Map).

  Tests: `tests/test_mastery_map.py` (12 tests) covering aggregator
  unit logic (untouched user, decayed mastery, subject filter,
  normalisation, color thresholds, decay function half-life,
  summarise rollup), HTTP contract (anonymous → 401, happy-path
  shape, summary-only endpoint, router registration), and router
  registry presence.

  Honest gaps:
  - **Untouched is currently the default state** for >95% of
    Pathshala users because `user_topic_mastery` is only written
    by the practice flow today. Wiring essay + mock + flashcard
    flows to also `mastery.update()` is part of prod-136..139 —
    they each handle a different signal source.
  - **Topic-key fuzzy match isn't semantic** — `_normalise_topic_key`
    collapses punctuation + case but doesn't handle synonyms
    ("Acids and Bases" vs "Acids, Bases & Salts"). prod-138's
    NCERT-code tagging gives us a canonical join key when it
    lands; until then, ~10-15% of cross-module signals may
    not match the curriculum chapter they belong to.
  - **No SPA wiring yet.** The API is live; the dashboard widget
    that consumes it is the next session.

- **prod-131..134 — CK-12-inspired feature sprint.** Researched CK-12
  Foundation's product (US-based K-12 EdTech with 200M+ users) and
  identified 4 borrowable patterns. Implemented end-to-end:

  - **prod-131 — AI-Resistant Assignment Generator.** New module
    `padhai/ai_resistant_assignments.py` and admin endpoint `POST
    /api/admin/teacher-tools/ai-resistant-assignment` that produces
    homework Claude can't trivially solve, by leaning on 5 distinct
    patterns: (1) student's own context (their kitchen, photo,
    neighbourhood), (2) process-showing rubric (final-answer-only
    forfeits credit), (3) hyper-local Indian framing (NCERT, ₹, km),
    (4) multi-modal asks (hand-drawn diagram, audio, photograph),
    (5) open-ended reflection ("Why do YOU think…"). Sonnet-backed
    via `llm_call.call_claude` with `PADHAI_AI_RESIST_MODEL` env
    override. Cost-cap aware (BudgetExceeded → 429 with /pricing
    upgrade link).

  - **prod-132 — Reading-Level Adjuster.** `POST /api/admin/teacher-
    tools/adjust-reading-level` rewrites text for a target grade
    (Class 1-12) with three style modes (simplify / translate / esl).
    Hyper-local board terminology hint when supplied. Haiku-backed
    for speed; max 8000 chars input (413 on oversize) so accidental
    whole-document submissions don't burn budget.

  - **prod-133 — Math-vision as mobile shell home screen.** Capacitor
    student-shell now launches at `/?home=math` (set by
    `mobile/scripts/configure-server.cjs`). `HOME_HTML` carries an
    inline `<body>`-top redirect that bounces to `/math` (the
    math-vision page from prod-28). CK-12-inspired "scan-and-solve"
    mobile entry; preserves the dashboard at `/` for web. Per-role
    env overrides: `CAPACITOR_HOME_PATH_STUDENT` / `_PARENT` /
    `_TEACHER`. Doc: `mobile/MOBILE_HOME.md`.

  - **prod-134 — Public `/concept/{slug}` SEO surface.** New router
    `padhai/routers/concept_seo.py` serves server-rendered concept
    pages with **Schema.org `VideoObject` JSON-LD** (Google video
    carousel), **Open Graph** metadata (WhatsApp / Facebook rich
    previews — major Indian sharing channel), **hreflang** for all
    9 supported locales + x-default, **YouTube iframe embed**, plus
    related-concept crawl links and a sign-up CTA. `GET /concept`
    indexes the catalog. Public — search-engine crawlers and link
    unfurlers can hit without auth. Falls back from `verified` →
    `channel_seed` tier so any curated concept renders.

  Total test growth: 262 → 295 (+33). New tests:
  `tests/test_teacher_tools.py` (12), `tests/test_mobile_home.py`
  (7), `tests/test_concept_seo.py` (10), plus 2 updated test files
  (`test_endpoint_tier_map.py` re-baselined for the new
  765-endpoint count, `cypress/e2e/15-mobile-shell.cy.js` extended
  for the `/?home=math` redirect check). All ruff categories
  continue to pass; model-id guard, router-registry guard, and
  bench-structural (385/385) green.

  Honest gaps:
  - The teacher-tool endpoints are admin-gated by the prod-9
    router-level injection — that's correct positioning for a
    teacher-only feature, but the *org-admin* gate path needs the
    school-portal UI to surface the buttons before this lands in
    front of real teachers. The endpoint contract is solid; the
    SPA wiring is the next sprint.
  - prod-133 ships a JS redirect; the *first paint* on iOS WebView
    still shows the home page for ~50ms before bouncing. A
    server-side 302 specifically for the shell user-agent would
    be tighter but adds back-end coupling. Will measure on real
    devices once ops has a test phone.
  - prod-134's `/concept` SEO surface helps but is only as good as
    the curated catalogue (~70 concepts at time of writing). The
    auto-curator from prod-42 + the channel_seed → verified queue
    are the levers; a sustained content-curation sprint to push
    past 200 verified concepts would 3× the SEO surface area.

- **prod-3 — Hindi UI audit (single-focus sprint).** Measured the
  real i18n gap: 285 hardcoded English UI strings in `_INDEX_HTML`
  / `HOME_HTML` / `LANDING_HTML` versus 39 i18n keys = **2.5% UI
  coverage** (not the "10 languages supported" the README implies).
  Shipped:

  1. `scripts/audit_i18n.py` — reproducible audit, prints
     coverage % + top-N untranslated by frequency. Wired into
     `make i18n-audit`.

  2. en.json + hi.json grew from 39 → 94 keys (added 55 strings
     across nav, modules, CRUD verbs, form labels, footer).
     Hand-translated Hindi (not machine-translated). Coverage
     of hardcoded strings now **16.8%** — honest progress, far
     from done.

  3. `tests/test_i18n_coverage.py` — 4 regression tests pinning
     `MIN_EN_KEYS = 94`, Hindi parity required, no empty Hindi
     values, every supported locale has `_meta_name` /
     `_meta_native`. Floor is monotonic across future sprints.

  Total pytest: 75 → 79.

  Honest gap: other 7 languages (Tamil, Telugu, Kannada, Malayalam,
  Marathi, Bengali, Gujarati, Punjabi) range from 21% to 42% key
  coverage. They were 100% against 39 keys; the new 55 keys haven't
  been translated yet. Hindi is the launch language so it took
  priority; the rest get back-filled in subsequent sprints.

  Wiring story: the SPA still references hardcoded English text
  in `_INDEX_HTML`. Catalog growth is step 1. Step 2 (next sprint)
  is to actually swap the hardcoded strings for `t(key, locale=user_lang)`
  calls. Without that wiring, the catalog growth is ammunition,
  not coverage.
- **prod-4 — PYQ ingest pipeline (single-focus sprint).** JEE /
  NEET / UPSC students treat past-year questions as table stakes;
  `padhai/question_bank.py` had the schema + `upsert()` API since
  v1.6 but no batch loader, so the table was always empty. Closed
  the gap end-to-end:

  1. `scripts/import_pyq.py` — JSON batch loader. Reads one file
     per exam-year batch with `default_board` / `default_grade` /
     `default_subject` / `default_year` / `default_paper` plus a
     `questions[]` array (per-question overrides supported).
     Idempotent via `question_bank.upsert()` on the natural key
     `(board, grade, subject, year, paper, question_text)`. Glob
     expansion for `data/pyq/*.json`. `--dry-run` for CI.

  2. Seed dataset: 60 JEE Main 2024 questions across math (20),
     physics (20), chemistry (20). Distribution covers easy /
     medium / hard. Lives in `data/pyq/jee_main_2024_{math,physics,
     chemistry}.json` so the pipeline can be exercised offline
     without a separate download.

  3. `tests/test_pyq_import.py` — 4 regression tests pinning the
     contract: seed files present, end-to-end import lands all 60
     rows in `question_bank`, idempotency (re-running doesn't
     double-insert), every imported row has board/grade/subject/
     year/paper/correct_answer/options/difficulty/marks populated.
     Uses `tmp_path` SQLite via `monkeypatch.setenv("PADHAI_DB_PATH")`
     so tests never touch the dev DB.

  Total pytest: 79 → 83. Verified end-to-end: dry-run + real import
  both report `total loaded: 60; errors: 0`, replay stays at 60.

  Honest gap: 60 questions covers one exam session of one board-
  year. Production should target 200+ per major exam (JEE Main,
  JEE Advanced, NEET, UPSC Prelims) across recent 5 years =
  ~5000 questions. That's content-acquisition work (OCR + manual
  review), not engineering — the pipeline is ready for it.
- **prod-5 — LLM-judge for the accuracy bench (single-focus sprint).**
  The bench had three judges (`exact_match`, `rouge_l`, `quiz_key`)
  none of which tolerate paraphrases — "Gandhi" vs "Mahatma Gandhi"
  vs "M.K. Gandhi" all fail `exact_match` and partially-credit
  under `rouge_l`. So the structural-mode pass_rate=1.000 we
  ship in CI has been hiding the fact that we'd need a smarter
  judge to ever flip the live-mode gate from advisory to
  meaningful. Closed the gap:

  1. `padhai/accuracy_bench.py` — new `_llm_judge()` (Haiku-backed,
     ~₹0.001 per call) returns 1.0/0.5/0.0 for CORRECT / PARTIAL /
     WRONG with one-token output. Registered in `VALID_JUDGES` +
     `_JUDGES`. Judge signature extended to accept `prompt=` so
     the LLM-judge has question context; existing judges accept
     and ignore the new kwarg. Lazy-imports anthropic — structural
     mode still has zero LLM dependency.

  2. `scripts/run_accuracy_bench.py` — new `--limit N` flag for
     cheap sampling (`--limit=20` keeps the baseline run at ~₹0.10).
     Docstring documents the full baseline-capture procedure
     (env var, command, expected output).

  3. `tests/test_llm_judge.py` — 9 regression tests with a fake
     Anthropic client (no real calls in pytest): CORRECT→1.0,
     PARTIAL→0.5, WRONG→0.0, trailing-punctuation tolerance,
     unparseable-verdict guard, empty-actual short-circuit (no
     judge call burned), missing-API-key clean error, registry
     consistency, kwarg compatibility across all 4 existing
     judges.

  Total pytest: 83 → 92.

  Honest gap: the baseline pass_rate is NOT captured yet — that
  requires `ANTHROPIC_API_KEY` and a 20-call run. The pipeline
  is ready; the user runs `python scripts/run_accuracy_bench.py
  --mode=live --judge=llm_judge --limit=20` when they have a key
  in shell env. Structural CI gate is unchanged (385/385 still
  passes); LLM-judge is opt-in via `--judge=llm_judge`.
- **prod-6 — Sentry integration end-to-end (single-focus sprint).**
  `observability.py` had a half-wired `init_sentry()` since v0.13
  but: no FastAPI integration registered (so events carried no
  route context), no test-exception endpoint to validate the pipe
  after deploys, no SDK in any requirements file (lazy-imported
  only), no tests, no noise filtering on 4xx. Closed:

  1. `padhai/observability.py` — `init_sentry()` now registers
     the `StarletteIntegration` + `FastApiIntegration` (transaction
     style `endpoint` for route-template aggregation), pulls release
     from `RENDER_GIT_COMMIT`, and installs a `before_send` hook
     that drops `SENTRY_DROP_STATUSES` events (default: 401/403/
     404/405/422/429). 5xx and statusless events always flow.
     Falls back to the plain SDK when the `[fastapi]` extra isn't
     installed — emits an observability log line so ops sees the
     downgrade.

  2. `GET /__sentry_test` — auto-registered by `install(app)`.
     Raises `_SentryTestException` ("intentional — Sentry
     verification"), routed through the middleware so the integration
     captures it. Gating: in non-production it's open (devs need
     easy access); in production it requires
     `X-Sentry-Test-Token: <PADHAI_SENTRY_TEST_TOKEN>`, otherwise
     404. Returning 404 (not 500/401) means a bot scanning the
     internet can't burn the Sentry quota on the endpoint. The
     exception class is distinct so a Sentry issue filter can drop
     replays.

  3. `requirements-optional.txt` — declares `sentry-sdk[fastapi]>=2.0`
     and `posthog>=3.0` (was: lazy-imported but undeclared, so a
     fresh `pip install -r requirements-optional.txt` was missing
     the SDK).

  4. `tests/test_sentry_wiring.py` — 11 regression tests covering
     init-without-DSN (False, no crash), capture-before-init
     no-op, install-without-Sentry (route still registered),
     test-route raises in non-prod, returns 404 in prod without
     token, returns 404 in prod with wrong token, fires in prod
     with correct token, before_send drops the 6 default 4xx
     codes, keeps 5xx + statusless events, respects env override.
     Reloads observability module per-test so the
     `_sentry_initialised` global isolates cleanly.

  5. `PRODUCTION_CHECKLIST.md` §7 — clarified the test-fire
     procedure (curl + X-Sentry-Test-Token header, 404 in prod
     without token).

  Total pytest: 92 → 103. Verified `/__sentry_test` registers on
  the real `padhai.web:app` (not just synthetic test fixtures).

  Honest gap: the actual DSN-paired dashboard verification still
  requires a Sentry account + DSN; that's the
  `make-the-event-show-up` step that the user runs post-deploy.
  Code path is now end-to-end correct.
- **prod-7 — Supply-chain + secrets + coverage CI gates
  (single-focus sprint).** Three independent gates closing the
  hygiene holes that aren't covered by ruff or pytest:

  1. **pip-audit (`.github/workflows/security-audit.yml`).** Scans
     `requirements.txt` + `requirements-optional.txt` against OSV
     + PyPI advisory feeds. Triggers: every PR touching the deps,
     every push to main, nightly cron at 03:30 UTC (catches a
     CVE disclosed against a pinned version we shipped), manual
     re-run via workflow_dispatch. Suppressions live in
     `.pip-audit-ignore`. `scripts/_pip_audit_ignore_flags.sh`
     translates the file into pip-audit CLI flags so the
     Makefile target and the workflow can't disagree. **Local
     scan at prod-7: zero known vulnerabilities** across both
     files.

  2. **gitleaks (`.github/workflows/gitleaks.yml` + pre-commit).**
     Secret scanner — catches accidental commits of API keys,
     JWT secrets, AWS / Razorpay / Anthropic tokens. Triggers
     on every PR with `fetch-depth: 0` so a secret added then
     deleted is still caught. Pre-commit hook (rev v8.21.2)
     blocks the same patterns on `git commit`. Allowlist in
     `.gitleaks.toml` covers known-safe paths (tests/fixtures,
     data/pyq, padhai/locales, cypress, the bench analysis
     docs) and placeholder strings used in env templates
     (`dev-change-me`, `CHANGE_ME`, `sk-ant-example`).

  3. **Coverage gate (`.github/workflows/coverage.yml` +
     `make coverage`).** pytest-cov with `--cov-fail-under=30`.
     Honest baseline at prod-7: **31.79%** across `padhai/`. The
     gate is a regression backstop — if it falls under 30% a
     PR pulled significant tests or added a major untested
     surface. Floor goes up as tests get added; the workflow
     and Makefile both reference the same `30` so they can't
     drift.

  3 new Makefile targets: `make audit` / `make coverage` /
  `make gitleaks`. The first runs against the local Python env,
  the third requires the gitleaks binary on PATH (Linux/macOS;
  not available on Windows by default).

  Total pytest unchanged: 103 → 103. The gates are CI signal,
  not new test surface — they catch a different class of issue
  (supply chain CVEs / leaked secrets / coverage regressions)
  that no existing check would have caught.

  Honest gaps: (a) gitleaks-action on PRs uses the GitHub
  Marketplace action which requires a one-time PAT/license for
  private repos at scale — should work on public repos out of
  the box; (b) coverage is heavily weighted by `web.py` (the
  ~13k-line SPA-embed module) — the real test gap isn't 31.79%
  uniformly, it's "the router slices are well-tested, the SPA
  bridge isn't"; (c) pip-audit only knows about Python deps —
  the npm/Capacitor side of `mobile/` isn't scanned (separate
  sprint).
- **prod-8 — Endpoint tier audit + machine-readable map
  (single-focus sprint).** `_require_tier()` lives in
  `padhai/web.py` but no canonical inventory of which routes
  actually use it. The map surfaces what's free / paid / admin.
  Shipped:

  1. `scripts/audit_endpoint_tiers.py` — boots the app, walks
     `app.routes`, AST-inspects each handler. Classification
     order (first match wins): ADMIN_ONLY (calls
     `require_admin_role`), TIER_GATED (calls
     `_require_tier(..., "Mx")`), AUTH_REQUIRED (raises 401
     when `user is None`), ANONYMOUS_OK (accepts user=None
     silently), PUBLIC (no current_user param at all). Outputs
     `data/endpoint_tier_map.json` + `docs/ENDPOINT_TIER_MAP.md`.

  2. `data/endpoint_tier_map.json` + `docs/ENDPOINT_TIER_MAP.md`
     — checked-in snapshots. The JSON is the test contract; the
     Markdown is the human-readable table for product / ops.

  3. `tests/test_endpoint_tier_map.py` — 4 regression tests
     pinning EXPECTED_TOTAL=726 and EXPECTED_COUNTS exactly. A
     refactor that adds/removes/reclassifies a route fails CI
     with a clean diff. The `test_no_tier_gated_endpoints_yet`
     test is intentionally a snapshot of the gap — designed to
     be deliberately deleted when the first paid feature gates.

  **Real findings the audit surfaced** (not classifier defects —
  verified by anonymous `TestClient.get(path)` returning 200):

  - **726 endpoints, 0 are tier-gated.** Every premium feature is
    free for any signed-in user. `_require_tier()` exists, no
    handler calls it.
  - **Many `/api/admin/*` routes in v3.py have NO auth at all.**
    Confirmed anonymous-accessible:
    `/api/admin/flags/{key}/exposures`, `/api/admin/forums/flagged`,
    `/api/admin/doubts/stats`, `/api/admin/cs/at-risk`. Root cause:
    `padhai/routers/v3.py:19` is `APIRouter()` without
    `dependencies=[...]`, so handlers have to opt in to auth —
    many forgot. Path prefix `/api/admin/` is naming-only.

  Both findings documented in `SECURITY.md` under "Known gaps
  (tracked, not yet fixed)" with proposed fix shapes. Total
  pytest: 103 → 107.

  Honest gaps: (a) `/__sentry_test` shows up as UNKNOWN because
  it's defined inside a closure (`_register_sentry_test_route`)
  and `inspect.getsource` returns the outer function — the route
  is correctly gated, just unclassifiable by AST. (b) The
  classifier is heuristic; a handler that auth-gates via an
  uncommon pattern (e.g. inside an `if/else` branch) might be
  misclassified. The HTTP test in SECURITY.md is the ground truth.
- **prod-9 — close prod-8's two findings (single-focus sprint).**
  Both gaps that the tier audit surfaced are now closed end-to-end,
  with HTTP-level verification:

  1. **`/api/admin/*` anonymous gap (HIGH security).** 112 routes
     across `v3.py` (110), `catalog.py` (1), and `doubt_ai.py` (1)
     accepted anonymous traffic — confirmed by direct
     `TestClient.get(path)` at prod-8 returning 200. Rather than
     touch 112 handlers, added a router-level dependency injector
     at `padhai/routers/__init__.py:_inject_admin_dep` that walks
     every router's routes and prepends
     `Depends(api_deps.make_admin_dep())` to any route whose path
     starts with `/api/admin/`. The injection mutates
     `route.dependencies` (the declarative list) — NOT
     `route.dependant.dependencies` (the computed tree) — so that
     `app.include_router` picks it up when building the app's
     APIRoute copies. Verified: same 4 sentinel paths now return
     **401** anonymous. Audit ADMIN_ONLY count: 5 → 117.

  2. **Zero tier-gated endpoints.** Gated `POST /api/v2/video-requests`
     and `POST /api/v2/video-requests/{request_id}/regenerate` at
     **M2** via inline `_require_tier(user, "M2")`. Long-form
     personalised video render is the clearest premium feature
     across competitors (BYJU's / Vedantu / Unacademy). Verified:
     anonymous POST returns 401. Audit TIER_GATED count: 0 → 2.
     The remaining premium surfaces (M3/M4* photoreal, premium
     voice) need pricing decisions before gating — those are
     product work, not engineering.

  3. **Audit classifier upgrade.** Added `_classify_route_deps()`
     to `scripts/audit_endpoint_tiers.py` so router-level
     dependency injections are detected — without this the
     classifier would have continued to report the 112 newly-gated
     routes as PUBLIC/ANONYMOUS_OK based on their unchanged
     handler bodies. The injected-deps check overrides the AST
     classification.

  4. **Updated regression test baselines** in
     `tests/test_endpoint_tier_map.py`: EXPECTED_COUNTS now reads
     `{ADMIN_ONLY: 117, ANONYMOUS_OK: 427, PUBLIC: 163,
     TIER_GATED: 2, AUTH_REQUIRED: 16, UNKNOWN: 1}`. Replaced the
     `test_no_tier_gated_endpoints_yet` snapshot with
     `test_known_tier_gated_endpoints` that pins the exact set of
     gated endpoints — future gate additions need explicit review.

  `SECURITY.md` "Known gaps" section moved both findings to a
  **CLOSED at prod-9** state with the fix-shape preserved as
  history. Total pytest unchanged at 107/107.
- **prod-10 — i18n to 100% for all 8 non-Hindi locales.**
  prod-3 baseline was 21-42% for the 7 non-Hindi/non-English
  languages — they were "100% of 39 keys" before the catalogue
  grew to 94, then regressed. Closed the gap by hand-translating
  every missing key:

  1. `scripts/build_locales.py` — single auditable source of
     truth. Translations live as Python dicts (one per locale),
     the script emits all 8 JSON files in one pass. `--check`
     mode verifies parity without writing. Catches "key in
     translation file but not in EN" (extra) AND "key in EN but
     not translated" (missing).

  2. **All 8 locales now at 100%** of the 94-key EN catalogue:
     ta (Tamil), te (Telugu), kn (Kannada), ml (Malayalam),
     mr (Marathi), bn (Bengali), gu (Gujarati), pa (Punjabi).
     Native-script throughout; English borrowings preserved
     where they're the conventional form in Indian-English
     (DPDP, UPI, Razorpay, WhatsApp, AI).

  3. `tests/test_i18n_coverage.py` — added
     `test_supported_locales_at_or_above_floor` (≥90% gate,
     monotonic) and `test_no_empty_values_in_supported_locales`
     (catches the silent-fallback-to-English failure mode).
     Total pytest: 107 → 109.

  Honest gap that remains: these are first-pass translations.
  Native-speaker review (especially for technical education
  terms — "rubric", "spaced repetition", "adaptive practice")
  is still the right gate before production launch. The
  infrastructure to catch broken/empty translations is now in
  place; quality is a content-review pass, not engineering.
  The catalogue → SPA wiring (swapping hardcoded English in
  `_INDEX_HTML` for `t(key)` calls) is the next sprint —
  without it, the catalogue is ammunition not coverage.
- **prod-11 — SPA wiring (catalog flows into rendered HTML).**
  prod-10 brought 8 non-English locales to 100% of the 94-key
  catalog, but the SPA still served literal English HTML. Closed
  end-to-end at the request layer:

  1. `padhai/i18n.py` — new `localize_template(html, locale)`:
     loops the locale's translation pairs (longest-first to avoid
     substring overlaps), does a literal `str.replace` for each
     EN value present in the template. Filters out `_meta` keys
     and values <4 chars (too risky to substring-match — could
     hit `Up`, `In`). LRU-cached per (template, locale) so the
     per-request cost is just a dict lookup once the cache is
     warm.

  2. `padhai/i18n.py` — new `normalise_locale(value)`: maps a
     raw header / cookie / query value to a supported code.
     Handles region tags (`hi-IN` → `hi`), Accept-Language priority
     lists (`ta-IN,en;q=0.9` → `ta`), unknown locales (falls back
     to `en`).

  3. `padhai/home_ui.py` — `get_home_html(locale=None)` and
     `get_landing_html(locale=None)` now accept a locale param and
     call `localize_template` when set.

  4. `padhai/web.py` — new `_locale_from_request(request)` helper
     resolves locale in this priority order:
     `?lang=` → `padhai_lang` cookie → `Accept-Language` header
     → `en` fallback. Wired into `/ui`, `/home`, and the
     SEO-friendly `/home/{lang}` (which previously only emitted
     hreflang tags but never localized the body).

  5. `tests/test_i18n_wiring.py` — 11 HTTP-level regression
     tests using `TestClient`. Canary string is "Sign in" / its
     8 translations. Tests the four resolution paths (path /
     query / cookie / default), the unknown-locale fallback
     behaviour, the substring-guard (no false matches), and
     the per-locale SEO route for all 9 languages.

  Total pytest: 109 → 120.

  Honest gaps that remain: (a) only the ~47 EN strings in the
  catalog that match HOME_HTML verbatim get localized — the
  other ~245 hardcoded English strings (modal copy, error
  messages, A11y labels with extra characters like
  `🇮🇳 Home`) still render in English. Closing them is a
  catalog-expansion + HTML-edit pass, not engineering. (b) The
  `_INDEX_HTML` legacy SPA at `/ui-legacy` is untouched — it
  predates the catalog and uses inline `data-lang-key` attributes
  that need a different rewiring strategy. (c) Mobile shell
  Capacitor JS doesn't yet read the catalog — it ships English.
- **prod-12 — PYQ seed across boards / exams / mediums.**
  prod-4 shipped 60 JEE Main 2024 questions; production scale is
  thousands across every major exam. Closing the breadth gap
  while staying honest about the depth gap:

  1. `scripts/build_pyq_seed.py` — single Python file with all
     batch data as dicts; emits 27 JSON files under `data/pyq/`.
     Single source of truth (vs hand-editing 27 JSON files in
     lockstep). `--check` mode for CI dry-run.

  2. **27 new seed files, 168 new questions, 228 total** across:
     - **5 national exams**: JEE Main, JEE Advanced, NEET, UPSC
       Prelims (polity / geography / history / economy), CAT
       (Quant + VARC)
     - **2 national boards**: CBSE (Class 10 math/science/social,
       Class 12 physics/chemistry), ICSE (Class 10 math)
     - **7 state boards** (Class 10): Maharashtra, TamilNadu,
       Karnataka, AP/Telangana, Gujarat, WestBengal, UP
     - **Hindi-medium variants**: CBSE Class 10 math + science
       rendered in Devanagari (`mathematics_hindi` /
       `science_hindi` subjects) — proves the pipeline supports
       multi-medium without schema changes

  3. Fixed `scripts/import_pyq.py` validator — previously rejected
     `default_grade: 0` as falsy. Post-school exams (UPSC / CAT /
     SSC) use 0 as a sentinel for "not a school grade"; the
     validator now uses `is None` so 0 passes.

  4. `tests/test_pyq_import.py` — 5 new prod-12 tests on top of
     the existing prod-4 set: full-seed-loads-clean, min-228-
     questions floor, all-13-boards-covered, Hindi-medium subjects
     present, post-school exams use grade=0. Total pytest:
     120 → 125.

  Honest gaps that remain:
    - **Volume**: 228 questions is 1 paper's worth per exam, not
      the 5+ years × 200+ questions per major exam (~5000 total)
      that production demands. The pipeline is ready; the gap is
      content acquisition (OCR + manual review of public papers,
      or licensing deals).
    - **Authenticity**: these are *exam-style* questions in the
      format of each board's paper, not verbatim past-year
      questions. Replacing a batch with real PYQs is just dropping
      a JSON file in `data/pyq/`.
    - **Language mediums**: 2 Hindi-medium variants seeded for
      CBSE. The 7 state boards have an English-medium seed each
      but no Marathi / Tamil / Telugu / Kannada / Gujarati /
      Punjabi / Bengali medium variants yet — also content work.
    - **Subjects**: CBSE Class 12 covers physics + chemistry but
      not biology / English / Hindi / accounts; ICSE only math;
      UPSC Mains entirely absent (only Prelims). Filling these
      is incremental content sprints.
- **prod-13 (slide-based explainer)** — first iteration: Claude
  Haiku writes a 6-section script, gTTS narrates, PIL renders
  slides, moviepy assembles MP4. `scripts/generate_concept_video.py`
  produces `data/concept_videos/newton1_en.mp4` (~₹0.19/video). User
  rejected as "too PPT-like" → pivoted to Manim.
- **prod-13b (Manim animation)** — second iteration:
  `scripts/generate_manim_video.py` has Claude Sonnet write a
  Manim CE Scene class, renders via the bundled imageio-ffmpeg
  binary. Produces real animated explainers (ball motion, force
  arrows, friction in red, applied force in green). ~₹4/video
  (Sonnet writes the scene). User reviewed against Peekaboo Kidz
  / Dr.Binocs reference and concluded the gap to studio-cartoon
  quality is too large for Manim to bridge. Cost analysis showed
  building a Veo3-equivalent model is genuinely impossible at
  startup scale (\$300M-1B). Pivoted again to embed strategy.
- **prod-14 — Concept-video embed catalog.** The honest startup
  answer: embed Peekaboo Kidz / Khan Academy / CrashCourse /
  FuseSchool / 3Blue1Brown content via YouTube iframes. AI focuses
  on the personalisation layer (practice / doubt-clearing / mock
  interviews) which is the real differentiator vs BYJU's. Shipped:

  1. `padhai/concept_videos.py` — schema + upsert + search +
     stats. Three quality tiers: `verified` (URL human-confirmed),
     `channel_seed` (trusted channel, curator needs to confirm
     specific URL), `ai_fallback` (no curated content, SPA falls
     back to /explain/video). Substring-LIKE search with English
     possessive stripping ("Newton's First Law" ↔ "newton first
     law"); Devanagari preserved for Hindi queries.

  2. `padhai/routers/concept_videos.py` — `GET /api/concept-videos`,
     `GET /api/concept-videos/stats`, `GET /api/concept-videos/{id}`.
     Public (no auth) so the SPA can hit them on load. Registered
     in the router registry.

  3. `scripts/build_concept_videos.py` — 22 seed rows across
     physics / biology / chemistry / mathematics / geography:
     1 **verified** (the Peekaboo Newton's First Law URL the user
     shared) + 21 **channel_seed** (trusted channels, curator
     spot-checks the specific URL before launch). Includes 1
     Hindi-medium row from Magnet Brains to prove multi-language
     pipeline.

  4. `tests/test_concept_videos.py` — 12 regression tests:
     normalisation (possessive stripping, Devanagari preservation),
     embed-URL derivation, upsert+search roundtrip, grade-band
     filter, validation (source + quality_tier), idempotency,
     full HTTP smoke through TestClient, seed-catalog quality
     ratio (≥1 verified, ≥10 channel_seed). Total pytest:
     125 → 137.

  5. Endpoint tier map updated: 726 → 729 routes (3 new public).
     `tests/test_endpoint_tier_map.py` baselines updated.

  Honest gaps that remain:
    - **21 of 22 seed rows need curator confirmation** before
      launch. A human spends ~30s per URL to flip channel_seed
      → verified. Total curation: ~10 min for prod-14's seed.
    - **No SPA wiring yet** — the API is live, but the home/chat
      surfaces don't yet call `/api/concept-videos?concept=...`
      to embed the video into the lesson flow. Next sprint.
    - **Search is substring-LIKE on normalised name** — not
      semantic. "How does light bend?" won't find the "Light
      Refraction" video. Adding Claude-powered concept extraction
      from the student's question is a future sprint.
    - **No YouTube embed liveness check** — a deleted video stays
      in the DB. A nightly job that HEADs each embed URL would
      catch this; not built yet.
- **Twenty-fifth router slice — DPDP rights (2 routes).**
  `padhai/routers/dpdp_rights.py` lifts `GET /api/me/data/export`
  (DPDP §11 — full personal-data dump as JSON, schema_version: 1,
  rate-limited via file_upload bucket with cost=5) and `DELETE
  /api/me/account` (DPDP §12 — anonymise email + lock account
  immediately, schedule full purge within 30 days, irreversible).
  Both append audit-log entries so the compliance officer can
  prove the request was honoured. Endpoints existed in web.py
  since v3.x but didn't have a dedicated home; consolidating
  them in their own slice makes the legal contract auditable.
  ~165 lines off web.py.
- **COMPETITIVE_ANALYSIS.md.** First-pass competitive deep-dive
  vs the Indian EdTech field (BYJU's / Vedantu / Unacademy /
  PhysicsWallah + international Khanmigo / StudyFetch /
  NotebookLM). Inventories what the codebase actually has from
  the 25 sprints (only counting working implementations, not
  PRD aspirations). Identifies P0 / P1 / P2 gaps with engineering
  vs ops effort estimates. Verdict: feature surface matches or
  beats ~80% of competitors; blockers to launch are content
  (PYQ database), localisation (Hindi UI audit), trust (outcome
  stories), and GTM — not engineering. Maps the next 6 prod-N
  sprints to the P0 list.
- **Accuracy bench 370 → 385 items.** volt, cell, CO2 molar mass,
  Article 21 right to life, 7x=49, train 100m/5s speed, Mumbai,
  neuron unit of nervous system, CO2 turns lime water milky,
  compound interest formula, neuron longest cell, equator,
  5!=120, Na for sodium, Mahatma Gandhi Father of the Nation.
- **Production-readiness sprint (prod-1).** Three deliverables
  shipping the platform from "polished codebase" to "ready to
  promote to production":

  1. **`scripts/check_security.py`** — pre-deploy security audit
     codifying the 8 invariants from SECURITY.md / ONBOARDING.md:
     JWT-secret strength + placeholder rejection, DPDP §9 minor
     threshold == 18, admin-gate fallback when `APP_ENV=production`,
     Anthropic key prefix check, psycopg search_path (advisory),
     bare-Haiku-model rejection (via the existing model-id guard),
     router-registry consistency, f-string SQL with user inputs.
     Wired into a new `make security` target.

  2. **`tests/test_security_invariants.py`** — 17 in-process tests
     (parametrised) that catch security regressions during
     `pytest`, not just at `make verify`. Locks: JWT placeholder
     list, HS256 + bounded TTL, DPDP age 18 + consent TTL, admin
     gate validator existence, db helpers, no bare Haiku literal,
     B904 cleanliness, no risky f-string SQL, every org router
     calls a role gate. Total pytest: 58 → 75.

  3. **`PRODUCTION_CHECKLIST.md`** — deploy-day reference. 12
     sections covering env vars, DB, provider keys, DPDP, admin
     gate, rate limits + cost cap, observability, CSP / security
     headers, multi-tenant guards, mobile shells, backups, SLA
     alerts. Built on top of `make verify && make security` as
     the automated 90% — checklist captures the remaining 10%
     (environment + monitoring) that can't be automated.
- **Accuracy bench 355 → 370 items.** SI unit tesla, knee hinge
  joint, pH(HCl 0.01M)=2, 6 fundamental rights, median of 5 nums,
  270°→3π/2, Röntgen discovered X-rays, Chelmsford Viceroy 1919,
  liver largest gland, 1-4-9-16-25, Au, entomology, λ=h/p,
  Montreal Protocol, Taj Mahal in Agra.
- **Twenty-fourth router slice — push admin (3 routes).**
  `padhai/routers/push_admin.py` covers `POST /api/push/{log_id}/
  opened` (public client beacon — log_id is the auth), `GET
  /api/push/log` (authed — scoped to caller's own rows unless
  they're an admin in any org), `GET /api/push/stats` (public
  aggregate metrics, no PII). ~60 lines off web.py.
- **Accuracy bench 340 → 355 items.** Linear slope=3, virus
  genetic material RNA/DNA, 8 electrons in oxygen, Lok Sabha min
  age 25, GCD(18,24)=6, boat-stream speed 12.5 km/h, Guru Nanak
  founded Sikhism, NaCl molar mass 58.5, Sarvodaya by Gandhi,
  chloroplast for photosynthesis, 0.5 moles in 22g CO2, Jupiter
  largest planet, c=3×10⁸ m/s, RBCs transport O₂, Gandhi 1948.
- **Twenty-third router slice — personalisation (2 routes).**
  `padhai/routers/personalisation.py` covers `GET /me/stats`
  (7-day activity rollup — thin wrapper around the shared
  `_compute_user_stats` that parents.py's `/children/{cid}/stats`
  also uses) and `POST /learning-path` (multi-week study plan via
  Opus + adaptive thinking, deterministically cached on the
  input key — the most expensive Claude surface in the codebase
  at ~₹4-6/call). User's library (recent succeeded lesson jobs)
  is folded into the plan so the planner can recommend re-watches
  rather than always proposing new generation. ~75 lines off
  web.py.
- **Accuracy bench 325 → 340 items.** Euler's e≈2.72, 12 cranial
  nerves, CH4, INC 1885, LCM(4,6)=12, 5-people-12-days inverse
  proportion, Tagore first Nobel, 49 cm² square area, oxygen
  most abundant in crust, m/s² acceleration unit, ostrich egg
  largest cell, (4/3)πr³, sin(90°)=1, KE=25 J, femur longest bone.
- **Twenty-second router slice — misc status (2 routes).**
  `padhai/routers/misc_status.py` bundles two small status
  endpoints that don't fit a larger subsystem:
  `GET /api/exam-mode/active` (authed — S4 anti-cheat: is this
  user in an active exam attempt? doubt-chat + voice-tutor poll
  this), `GET /api/fees/config` (public — Razorpay configured?
  surfaces just the public key_id, nothing sensitive).
  ~20 lines off web.py.
- **Accuracy bench 310 → 325 items.** Escape velocity 11.2 km/s,
  ribosome protein factory, mole SI, Vande Mataram by Bankim
  Chandra, triangle third angle 60°, 7×8=56, Shivaji first
  Maratha king, Ohm's law, apoptosis, √144=12, coulomb,
  pH 7, FDI full form, telephone Bell, ∫1/x dx = ln|x|+C.
- **Twenty-first router slice — avatar admin (3 routes).**
  `padhai/routers/avatar_admin.py` covers the photoreal-avatar
  provider router status surface: `GET /api/avatar-providers`
  (public — which providers configured), `GET /api/avatar-stats`
  (authed — per-provider success/fail counts), `POST
  /api/avatar-stats/reset` (authed — clear circuit-breaker
  counters). ~35 lines off web.py.
- **Accuracy bench 295 → 310 items.** SI unit ohm, vitamin C
  ascorbic acid, sum 1..10 = 55, GST full form, 3-4-5 right
  triangle area, π ≈ 3.14, water molar mass 18, Vatican City,
  Rabindranath Tagore, NaCl, joule SI work unit, Republic Day
  1950, Japanese yen, linear-equation y=2, 46 chromosomes.
- **Twentieth router slice — SSO (3 routes).**
  `padhai/routers/sso.py` covers the OAuth/OIDC sign-in flow:
  `GET /auth/sso/providers` (list configured), `GET /auth/sso/
  {provider}/start` (redirect-to-IdP), `GET /auth/sso/{provider}/
  callback` (code-exchange → session JWT → localStorage bounce
  page). `_sso_redirect_uri` + `_sso_error_page` helpers move
  with the router (only call site). `_set_auth_cookie` +
  `_escape_html` stay in web.py and are late-imported.
  ~165 lines off web.py.
- **Accuracy bench 280 → 295 items.** Added items spanning JEE
  π r², NEET atomic number of carbon, SI unit pressure pascal,
  Vasco da Gama 1498, age problem (4x+16=2(x+16) → 8), 22 skull
  bones, Chandragupta Maurya, log₁₀(10)=1, body temp 98.6°F,
  v=u+at, Rajya Sabha nominations, Sahara desert, longest river
  Ganga, and more.
- **Nineteenth router slice — uploads (3 routes).**
  `padhai/routers/uploads.py` covers the PRD §13.1-2 upload
  pipeline: `POST /api/uploads` (persist + ingest), `POST
  /api/uploads/{id}/analyze` (Claude vision → topic/grade/...),
  `GET /api/uploads/{id}` (look up on page reload). The
  `_UPLOAD_DIR` constant moves with the router (only call site).
  Not to be confused with `uploads_ai.py` which already exists
  and covers the RAG chat/flashcards/quiz/summary AI surface over
  an already-analyzed upload. ~165 lines off web.py.
- **CHANGELOG.md polish-N section.** Consolidated the 17 polish
  sprints (multi-month run hardening web.py + lint + bench) into
  one "polish-N sprint stack" entry under Unreleased. Lists the
  9 enforced lint categories (F E I B UP SIM RUF ARG + B904), the
  19 router slices, the 5 maintained tools, central tooling, the
  bench growth (12 → 280 items across 9 boards), the new docs,
  and the test count growth (37 → 58).
- **Accuracy bench 265 → 280 items.** Added items spanning JEE
  trig (sin π/2), NEET DNA full form, JEE organic IUPAC (propene),
  UPSC Tryst-with-Destiny speech, percentage arithmetic, 2^10
  TamilNadu, ammonia NH3 ICSE, Newton's 2nd law numerical, and
  more.
- **Eighteenth router slice — curriculum mapping (2 routes).**
  `padhai/routers/curriculum.py` covers `POST /lessons/{id}/curriculum`
  (NCERT/state-board catalogue match via `pedagogy.match_curriculum`)
  and `GET /curriculum/index` (browse/filter catalogue with Postgres
  override merge). Static `CURRICULUM` seed list + the
  `curriculum_topics` DB-override merge port cleanly. ~120 lines off
  web.py.
- **Router registry guard — `scripts/check_router_registry.py`.**
  Bidirectional structural check: every `padhai/routers/*.py` file
  appears in `_ROUTER_NAMES`, every name in `_ROUTER_NAMES` has a
  matching file. Closes the bug class where a router file is added
  but not registered (endpoints silently don't mount → SPA 404s on
  the new surface) or registered but the file got deleted (app boot
  crashes in `all_routers()`). Wired into `make verify` between the
  model-id guard and pytest. Tested by simulating an unregistered
  dummy file — guard correctly flagged + exited 1.
- **Accuracy bench 250 → 265 items.** Added 4 hard (JEE |z| for
  z=1+i, NEET osmosis, UPSC ISRO, JEE dipole moment qr),
  4 reasoning (probability 6/10, pencil-cost ratio, GP next term,
  rectangle diagonal 5-12-13), 3 state-board (Maharashtra 1947,
  Karnataka hertz, ICSE oxygen Z=8), 2 NEET/JEE chem-bio (vitamin K
  for clotting, glucose C6H12O6), 2 GK (Brahmaputra source, RBI
  1935). Distribution now `easy=149 / medium=74 / hard=42`.
  Structural runner: 265/265 in 4.5s.
- **Seventeenth router slice — lesson chat + recap (3 routes).**
  `padhai/routers/lesson_chat_recap.py` covers the trickier lesson
  siblings polish-14 deferred: `POST /chat/{lesson_id}` (RAG chat
  grounded in the lesson, with `[Scene N]` citation parsing and
  the S4 exam-mode lock), `POST /lessons/{id}/recap` (Haiku-backed
  text + TTS synthesis, cached), `GET /lessons/{id}/recap.mp3`
  (stream the cached audio). `CHAT_SYSTEM_PROMPT` +
  `_parse_citations` + the two citation regexes move with the
  router (only call site). `_claude()` / `cache` / `_rl` /
  `pedagogy.MODEL` / `tts.get_provider` stay in web.py and are
  late-imported. ~165 lines off web.py.
- **Accuracy bench 235 → 250 items (crossed the 250-item milestone).**
  Added 4 hard (JEE ln(x) derivative, NEET circular motion
  centripetal-a in terms of T, UPSC SLR, JEE VSEPR for NH3),
  4 reasoning (3x-7=11, 60% girls, half tank, 10% loss),
  4 state-board variety (Maharashtra Class 12 nephron, Karnataka
  Class 11 d-orbital electrons, TamilNadu Class 12 farad, AP/
  Telangana femur), 3 NEET/JEE chem-bio depth (O-negative
  universal donor, O2 double bond, K-shell n=1). Distribution
  now `easy=144 / medium=67 / hard=39`. Structural runner: 250/250
  in 4.9s.
- **Router unit tests expanded (21 tests, was 13).** Added
  behavioural coverage for the 4 newest slices:
  notifications (`/api/notifications/me` requires auth), schedule
  (`/api/orgs/.../today` + timetable gate on membership), lesson-
  detail (notes requires auth, quiz unknown-lesson is 404), and
  lesson-chat-recap (chat unknown-lesson hits the 404/429 path,
  recap unknown-lesson is 404). The path-registration test now
  enumerates all 17 slices instead of 13.
- **Sixteenth router slice — lesson-detail cache surfaces (5 routes).**
  `padhai/routers/lesson_detail.py` covers the cache-only lesson
  derivatives: `POST /lessons/{id}/flashcards` (generate or cached),
  `POST /lessons/{id}/quiz` (return cached quiz JSON), `GET/POST
  /lessons/{id}/notes` (per-user persistence), `POST /lessons/{id}/
  flashcards/rate` (SM-2 review beacon). None of these make a Claude
  call — they read the already-generated Lesson JSON or persist
  per-user state. The trickier siblings (`/chat/{id}` with
  CHAT_SYSTEM_PROMPT + citations, `/recap` with TTS deps,
  `/curriculum` mapping) stay in web.py for a future slice.
  ~120 lines off web.py.
- **Model-id guard — `scripts/check_model_constants.py`.** Locks
  invariant #5 from ONBOARDING.md ("model IDs come from models.py").
  Scans every `padhai/**/*.py` file for `"claude-(haiku|sonnet|
  opus)-..."` string literals and fails if any appear outside the
  allowlist (`padhai/models.py` — the source of truth, plus
  `padhai/llm_obs.py` — the pricing table that legitimately needs
  literal keys, plus `padhai/schema_v2.py` — a SQL column comment).
  Wired into `make verify` between lint and pytest. Future
  contributors can't reintroduce the `claude-haiku-4-5` bug class
  without the gate catching it.
- **Accuracy bench 220 → 235 items.** Added 4 hard (JEE 2×2
  determinant, NEET synapse, UPSC CRR, JEE capacitor energy ½CV²),
  4 state-board variety (TamilNadu Class 11 angular momentum,
  AP/Telangana π=3.14, UP barometer, ICSE Class 12 meiosis), 3
  reasoning (LCM/GCD=6, buy-2-get-1-free, 3h45m=225min), 2
  chemistry depth (NaOH molarity, electrolysis of water cathode),
  2 GK (Pratibha Patil, Paris 2024 Olympics). Distribution now
  `easy=136 / medium=64 / hard=35`. Structural runner: 235/235
  in 4.3s.
- **Fifteenth router slice — schedule cluster (4 routes).**
  `padhai/routers/orgs_schedule.py` groups the timetable read/
  write, the per-user "what's on today" endpoint, and the per-
  student assignment-history endpoint. They share a "what's
  happening in this class for this user" theme and the same
  org-membership gate. ~85 lines off web.py. Total slices extracted
  now: 15.
- **Accuracy bench 205 → 220 items.** Added 4 hard items (JEE wave
  λ=v/f, NEET propanoic acid IUPAC, JEE e^-x integral, UPSC
  Article 249), 4 state-board depth (Maharashtra Mariana, Karnataka
  SONAR, TamilNadu NaOH, UP sin 30°), 3 reasoning (20% discount,
  triangle angles, x²+y²), 2 IGCSE (chlorophyll b, joule), 2 NEET
  clinical (vitamin C / scurvy, liver largest gland). Distribution:
  `easy=130 / medium=59 / hard=31`. Structural runner: 220/220 in
  3.7s.
- **ONBOARDING.md — contributor's first-day map.** README + CLAUDE.md
  + CONTRIBUTING.md were each strong individually but a new
  contributor still had to read all three plus archaeology the repo
  for "where do I look for X". ONBOARDING.md is the consolidated
  map: 5-minute product summary, file-by-task lookup table, full
  15-router index, the 7 maintained tools, 8 invariants that are
  codified in CI/lint/asserts, and a 6-step first-day checklist
  that ends in `make verify`. Lives next to README so GitHub shows
  it on the repo home.
- **Fourteenth router slice — notifications.** All 5 notification
  endpoints lifted to `padhai/routers/notifications.py`:
  `GET/POST /api/notifications/me`, `POST .../{nid}/read`,
  `POST .../read-all`, and the org-side composer + log
  (`POST/GET /api/orgs/{id}/notifications`). The `_resolve_audience`
  helper (audience string → user_id list for push fan-out) ports
  with the router since this is its only call site. Push fan-out
  stays best-effort: notification creation never blocks on FCM/APNs
  failure. ~135 lines off web.py.
- **`make verify` — one-command pre-PR gate.** Bundles ruff (all
  9 enforced categories) + pytest (50 tests) + structural-mode
  accuracy bench (205 items, no API key needed) into ~20 seconds.
  `make lint` is also exposed standalone. Replaces the multi-step
  ritual contributors had to remember: ruff, pytest, bench, all
  with the right env vars. CONTRIBUTING.md still points contributors
  to `make verify` as the one command they run before pushing.
- **Accuracy bench 190 → 205 items (crossed the 200 milestone).**
  Added 5 hard (JEE trig period, NEET insulin, JEE SHM phase, UPSC
  GDP, NEET sulfur oxidation state), 3 reasoning (workers ratio,
  number puzzle, average), 3 NEET chemistry/biology depth (neon
  ionization, cerebellum, Zn+HCl), 2 IGCSE (cos 0, CO2), 2 SSC GK
  (Sardar Patel, Canberra). Distribution: `easy=122 / medium=56 /
  hard=27`. Structural runner: 205/205 in 4.1s.
- **Thirteenth router slice — SCIM 2.0 (`/scim/v2/*`).** All four
  SCIM endpoints + the `_scim_authenticate` bearer-token resolver
  lifted to `padhai/routers/scim.py`: `ServiceProviderConfig`
  (IdP discovery), `Users` (list with filter), `Users` POST
  (provision), `Users/{id}` PATCH (deactivate). The per-org bearer
  token is the auth surface — `_scim_authenticate` resolves it to
  `org_id` and 401s on invalid/revoked. ~130 lines off web.py.
- **Router unit-test harness — `tests/test_routers.py`.** 13 tests
  covering: (1) every module in `_ROUTER_NAMES` imports without
  circular errors; (2) one representative URL per extracted slice
  lands on `app.routes` (catches half-extracted slices); (3) the
  org-gated routes 401/403 unauthenticated callers BEFORE any DB
  access; (4) the two intentionally-public branding/SCIM endpoints
  (resolve + ServiceProviderConfig) return 200 without auth; (5)
  parametrised tests across 5 `/api/orgs/{id}/<sub>` paths check
  the role gate runs first. Total tests: 37 → 50.
- **Accuracy bench 175 → 190 items.** Added 5 reasoning / word-
  problem items (shopkeeper profit %, rectangle area, ratio
  problem, pipe fill+empty, simple interest), 4 state-board
  chemistry/biology (Maharashtra CH4, Karnataka nephron, TamilNadu
  mitochondria, ICSE acid pH range), 3 CBSE Class 6-8 (equator,
  Mountbatten, 5! = 120), 3 UPSC GK (ECI, Paris Agreement, Quit
  India 1942). Bench is now ~16% reasoning problems (was ~10%).
  Structural runner: 190/190 in 3.8s.
- **Central model-ID registry — `padhai/models.py`.** 13 files used
  to hardcode Claude model strings (`claude-haiku-4-5-20251001`,
  `claude-sonnet-4-6`, `claude-opus-4-7`). Each rename — like the
  2025-10 Haiku rename that invalidated the bare `claude-haiku-4-5`
  form — required hunting them down. Now there's one module:
  `HAIKU_MODEL` / `SONNET_MODEL` / `OPUS_MODEL` constants with env
  overrides (`PADHAI_HAIKU_MODEL` etc.). Surface-specific overrides
  (`PADHAI_ESSAY_GRADER_MODEL`, `PADHAI_TUTOR_MODEL`, etc.) still
  work — modules read `os.environ.get("PADHAI_<X>_MODEL",
  _models.HAIKU_MODEL)`. The module carries a startup assert against
  the buggy bare form. Migrated 13 files: pedagogy / doubt_clearing
  / essay_grader / math_vision / mock_interview / practice_test /
  moderation / uploads / tutor / routers/uploads_ai /
  routers/tutor_stream. Closes bug #8 in CLAUDE.md §14 (was: tutor.py
  + pedagogy.py used the invalid `claude-haiku-4-5` form).
- **Twelfth router slice — branding (3 routes).** All branding
  endpoints lifted to `padhai/routers/branding.py`:
  `GET /api/branding/resolve` (public — SPA boot lookup),
  `POST /api/orgs/{org_id}/branding/logo` (admin — upload), and
  `GET /branding/logo/{filename}` (public — serve uploaded logos).
  The two public endpoints are deliberately unauthenticated: the
  SPA calls resolve on page load before sign-in, and the logo URLs
  are referenced from HTML/CSS. ~100 lines off web.py.
- **Accuracy bench 160 → 175 items.** 5 more hard items (JEE parallel
  resistance, NEET active immunity, UPSC expansionary monetary
  policy, JEE matrix determinant, NEET Haber-process catalyst), 4
  state-board depth (Karnataka triangle area, TamilNadu Ampère, ICSE
  Babur Mughal, AP/Telangana CO₂ lime water), 3 CBSE Class 11/12
  reinforcement (derivative of e^x, Newton's 3rd law, embryogenesis),
  3 word-problem math (square perimeter, speed×time, consecutive
  integers). Distribution now `easy=108 / medium=45 / hard=22`.
  Structural runner: 175/175 in 3.2s.
- **Lint gate now F + E + I + B + UP + SIM + RUF + ARG blocking.**
  All 69 ARG (unused-argument) findings carry targeted `# noqa:
  ARG00x` markers after a `--add-noqa` pass. Distribution skews
  heavily toward polymorphic provider dispatchers (`talking_head.py`
  16, `push.py` 12, `diagrams.py` 6) where each provider implements
  the same shape but uses different subsets of params; the rest are
  FastAPI's `Depends(get_x)` pattern where the dependency call is
  the goal, not the param value. Gate now strict against new
  violations.
- **Eleventh router slice — `/api/orgs/{id}/exams*`.** Six endpoints
  (create / list / begin / submit / list-attempts / manual-grade
  override) plus two marshalling helpers (`_exam_to_dict`,
  `_attempt_to_dict`) moved to `padhai/routers/orgs_exams.py`.
  ~165 lines removed from web.py. The audit-log entry on manual
  grade override (known fraud vector in school deployments) ports
  cleanly. The companion `/api/exam-mode/active` (anti-cheat status
  surface) stays in web.py — it's a top-level route, not under
  `/api/orgs/`.
- **Accuracy bench 145 → 160 items.** Two new boards: **AP_Telangana**
  (eye iris / linear equations / Kosi river) and **UP Board** (NaCl
  formula / πr² / weber). Added 2 hard items (NEET metaphase /
  UPSC fiscal deficit), 2 chemistry depth, 2 biology depth, 3 word-
  problem math items (was very lookup-style). Distribution now
  `easy=102 / medium=41 / hard=17` across **9 board/exam tracks**
  (was 7). Structural runner: 160/160 in 3.3s.
- **Lint gate now F + E + I + B + UP + SIM + RUF blocking.** `ruff
  --fix --select RUF` cleaned 200 sites in one pass (mostly RUF100
  unused-noqa + RUF102 invalid-rule-code + RUF021); `--unsafe-fixes
  --fix` cleaned 30 more (RUF005 list-concat, RUF046 cast-to-int,
  RUF034 useless-if-else). The 104 unicode-ambiguous findings
  (RUF001/2/3 — em-dashes, smart quotes, Devanagari in
  Indian-language UI strings + bench data) are codebase-ignored
  as intentional. The remaining 18 (4 RUF012 mutable-class-default
  on read-only lookup maps + 14 SIM revisits introduced after
  polish-6) carry targeted `# noqa: RUFxxx/SIMxxx` markers.
- **Tenth router slice — `/api/orgs/{id}/fees*`.** Seven endpoints
  (structure CRUD, bulk-invoice generation, invoice list, fee
  summary, Razorpay payment init, payment confirm) plus two
  marshalling helpers (`_fee_struct_to_dict`, `_invoice_to_dict`)
  moved to `padhai/routers/orgs_fees.py`. ~190 lines removed from
  web.py. The companion `/api/webhooks/razorpay` stays in web.py
  because it dispatches BOTH fees + subscription-tier events.
- **Accuracy bench 130 → 145 items.** Added 4 hard items (NEET
  current-electricity / JEE limits / UPSC 6th Schedule / JEE
  hybridization), 4 CBSE Class 6-8 items (closing the
  primary-school gap), 3 chemistry-depth items, 2 IGCSE items
  (new board coverage — Cambridge curriculum), 2 SSC GK fillers.
  Distribution now `easy=94 / medium=36 / hard=15`. Structural
  runner: 145/145 in 3.1s.
- **B904 cleaned codebase-wide; gate now blocking.** Wrote
  `scripts/fix_b904.py` — an AST-based mass fixer that walks
  every `ExceptHandler`, finds nested `Raise` nodes without a
  `.cause`, and inserts `from <binding>` (or `from None` when the
  handler had no `as` clause). First pass had a UTF-8 byte-offset
  bug (ast's `end_col_offset` is byte-based, not char-based; lines
  containing em-dashes / smart quotes / Devanagari got corrupted).
  Rewrote the editor to manipulate bytes; ran across 27 files →
  344 sites fixed in one pass. v3.py alone had 246. B904 is now
  blocking in `pyproject.toml`; the fixer ships as a maintained
  script so future regressions can be cleaned the same way.
- **Ninth router slice — `/api/orgs/{id}/assignments*`.** Four
  endpoints (list, create, student completion beacon, class stats
  rollup) moved to `padhai/routers/orgs_assignments.py`. ~95 lines
  removed from web.py. The completion POST is intentionally
  permissive for students (they can only write their own
  user_id-keyed row) — that policy is in the router gate, not
  delegated to `_orgs.record_completion`.
- **Accuracy bench 115 → 130 items.** Added 6 hard items (JEE
  gravitation / NEET retroviruses / UPSC Article 352 / NEET MO
  theory / JEE optics / JEE integration), 5 CBSE Class 11/12 items
  (was only 2 across those classes), and 4 SSC/UPSC/state-board
  fillers. Distribution now `easy=86 / medium=33 / hard=11`.
  Structural runner: 130/130 in 2.2s.
- **Lint gate now F + E + I + B + UP + SIM blocking.** Triaged the
  29 SIM (simplify) findings: 16 auto-fixed cleanly (mostly
  `try-except-pass` → `contextlib.suppress`, `if/else` → ternary,
  collapsible-if merges, reimplemented-builtin → `any()` /
  `bool()`). The remaining 13 carry targeted `# noqa: SIMxxx`
  markers — they're intentionally-structured try-pass blocks with
  semantically-meaningful tail comments (`pass  # column already
  exists`) that `contextlib.suppress` would lose, or nested-if
  patterns where the comment between the two ifs clarifies why
  they aren't merged. Gate is now strict against new violations.
- **Eighth router slice — `/api/orgs/{id}/classes/{cid}/attendance*`.**
  Four endpoints (daily roll GET, bulk-mark POST, per-student
  history GET, range-summary GET) moved to
  `padhai/routers/orgs_attendance.py`. Removed ~85 lines from
  web.py. Same late-import pattern; the student-row filter on the
  daily roll stays in the router (it's policy, not storage).
- **Accuracy bench 102 → 115 items.** Closed three coverage gaps:
  added 4 `hard`-difficulty items (was 0), 4 state-board items
  (Maharashtra / ICSE / Karnataka / TamilNadu), and 5 medium items
  in underrepresented UPSC polity / JEE math / NEET chemistry /
  UPSC history / SSC geography. Distribution now
  `easy=83 / medium=28 / hard=4`. Structural runner: 115/115
  passing.
- **Lint gate now F + E + I + B + UP blocking.** `ruff --fix --select UP`
  cleaned 49 sites (mostly `Optional[X]` → `X | None`, `typing.Iterable`
  → `collections.abc.Iterable`, `datetime.utcnow()` → `datetime.now(UTC)`).
  A follow-up `--fix` cleaned the 17 I001 import-sort fallout from
  newly-added `UTC` imports. SIM is the next category — still
  advisory pre-commit only.
- **Seventh router slice — `/api/orgs/{id}/classes/{cid}/leaderboard`.**
  Moved to `padhai/routers/orgs_leaderboard.py` as its own slice
  rather than folded into `orgs_classes.py`, because the I4 streaks /
  XP / leaderboard subsystem is a different owner (`_streaks` vs
  `_orgs`) and a different role gate (students can read the
  leaderboard but not the class roster). Keeping it separate makes
  the rest of I4 easier to lift wholesale later.
- **SECURITY.md.** Vuln disclosure policy at the repo root (email
  + private advisory), supported-versions matrix (main only — no
  LTS backports), in/out-of-scope guidance, and a "Hardening you
  should know about" section that documents the enforced gates
  (JWT secret validation, DPDP §9 minor-locking, admin gate
  safeguard, provider key validation, per-tier daily LLM cap, SQL
  parameter-binding, multi-tenant org gate, mobile shell URL
  rewriter). Reduces the chance a refactor silently regresses
  one of them.
- **All 6 Claude-calling surfaces on `call_claude`.** Migrating
  `pedagogy.generate_lesson` revealed it was the most-expensive
  Claude call (Opus + adaptive thinking) AND the only one not
  calling `llm_obs.record_call` — every lesson render was silently
  uncosted. Fixed as a side-effect of the migration.
  `generate_explainer` + `practice_test._synthesise` also migrated.
- **Three more `call_claude` migrations.** tutor / mock_interview /
  doubt_clearing now use `llm_call.call_claude()` instead of the
  inline client / messages.create / record_call boilerplate. The
  wrapper is now the proven pattern for 4 of the 6 Claude-calling
  surfaces (lesson + practice_test still to migrate, opportunistic).
  Each migration removed ~30-40 lines and made the surface's failure
  modes (SDK-missing / key-missing / Claude-error) easier to read.
- **Fourth router slice — `/api/parents/*`.** All 5 endpoints
  (link, revoke, list children, list parents, child stats) moved
  to `padhai/routers/parents.py`. Removed ~175 lines from web.py.
  The companion `/auth/parent-link/verify` HTML page stays because
  it shares the `_consent_result_page` template.
- **Third router slice.** GET `/api/v2/video-requests/{id}/status`
  + `/result` moved to `padhai/routers/v2_video.py`. The two POST
  endpoints (create + regenerate) stay in web.py for now — too
  many cross-cutting dependencies (PersonalizationProfile,
  moderation, multipart) to lift cleanly.
- **Accuracy bench 74 → 94 items.** Six items away from the 100-
  item gate. Added physics/chemistry/biology fundamentals + Indian
  polity articles + Mughal-era history.
- **Lint gate now F + E + I + B blocking.** Bugbear added after
  fixing 4 B007 unused-loop-control findings + documenting B008
  (FastAPI `Depends()` is the idiom, 530 sites) and B904
  (raise-without-from, 344 sites — dedicated sprint planned)
  as codebase-wide ignores. Promote B904 to active once the
  count is under ~50.
- **Sixth router slice — `/api/orgs/{id}/classes`.** List + create
  moved to `padhai/routers/orgs_classes.py`. Six other class-
  subsystem routes (attendance, timetable, leaderboard) stay in
  web.py for now; they're scattered far apart and worth their own
  slices.
- **CONTRIBUTING.md.** Onboarding doc covering dev setup, the
  Makefile commands, code conventions, the ruff phased ruleset,
  commit message style, and where to add new features. Lives next
  to README so new contributors land on it via GitHub.
- **Lint gate now F + E + I blocking.** All three rule categories
  show "All checks passed!" against the full codebase. Ran
  `ruff --fix --select I` once to sort imports across 21 files
  (231 safe transformations). Next category to promote: `B`
  (bugbear) once findings are triaged.
- **Accuracy bench 94 → 102 items.** Crossed the 100-item gate.
  Added math/physics/chemistry/biology/history/polity items.
- **Live accuracy gate is now blocking.** Runs on every push to
  main (was nightly-only) at `min-pass-rate=0.75`. Per-merge cost
  is ~$0.50 with Haiku; the value is catching lesson-generation
  regressions on the merge commit, not 24 h later.
- **Fifth router slice — `/api/orgs` core CRUD.** Six endpoints
  moved (me / create / detail / members list+add / roster CSV).
  ~130 lines off web.py. The other 30+ `/api/orgs/*` subsystems
  (classes / assignments / attendance / fees / exams / branding /
  notifications) stay for now — each deserves its own router.
- **Lint gate tightened to F + E.** `pyproject.toml` enables both
  pyflakes (F) and pycodestyle errors (E) as blocking, with E701/
  E702/E401/E501/E402/E741 explicitly ignored where the codebase's
  intentional patterns (compact SQL builders, idiomatic `if x:
  return`) would generate noise. Next category to flip is `I`
  (import sort) once `--fix` has been run through the codebase.
- **SQLite backup script.** `scripts/backup_sqlite.sh` uses the
  sqlite3 `.backup` API (safe under concurrent writes), gzips the
  snapshot, prunes >14d by default. Cron template at top of the
  file. Closes the gap §10 had docs-only for.
- **Lint gate.** `.github/workflows/lint.yml` runs ruff F-codes
  (real bugs: undefined names, broken imports) on every PR.
  `pyproject.toml` carries the ruleset; `.pre-commit-config.yaml`
  enforces it locally too. Started F-only on purpose — flipping
  E/I/B/UP/SIM to blocking would have surfaced 1100+ findings in
  one PR. They live as advisory `ruff --fix` only.
- **Three real bugs caught by the new lint gate during setup:**
    1. `padhai/mock_interview.py` — undefined `call_id` after my
       call_claude migration (should have been `call.call_id`).
    2. `padhai/web.py:15017` — `datetime.now(timezone.utc)` used
       without a function-local import; would crash the account-
       delete endpoint on first call.
    3. `padhai/db.py:486` — forward-ref `Path` type annotation
       with no module-level `Path` import; runtime works but
       static checkers fail.
- **Docker-compose E2E stack.** `make e2e` brings up postgres:15 +
  minio + liquibase + the app, seeds a demo dataset
  (`scripts/seed_demo.py`), runs the HTTP smoke
  (`scripts/e2e_smoke.py`) + Cypress full-flow spec
  (`cypress/e2e/17-e2e-full-flow.cy.js`), tears down. Compose pins
  `PADHAI_DB_PATH=/app/data/jobs.db` to a named volume so SQLite
  module tables survive rebuilds. CI wired at
  `.github/workflows/e2e.yml` (PR + nightly cron). See
  `SPRINT_E2E.md` for the full sprint plan.
- **Postgres parity for module tables.** New
  `db/changesets/002_module_tables.sql` covers 12 of the most-
  touched tables (ai_answer_provenance, ai_citations, llm_calls,
  llm_alerts, parent_consent_tokens, parent_consent_outbox,
  exam_packs, exam_pack_enrollments, essay_rubrics,
  essay_submissions, mock_interviews, mock_interview_turns,
  doubt_requests). Composed via `db/changesets/master.xml` so
  adding the next changeset is one `<include>` line.
- **Central LLM wrapper adopted in essay_grader.** `essay_grader.grade()`
  now calls `llm_call.call_claude()` instead of duplicating the
  client + record_call + cost-estimation boilerplate. ~30 lines
  removed; the wrapper is the recommended pattern for new
  surfaces and pending migrations.
- **Second router slice — /explain.** `padhai/routers/explainer.py`
  holds `POST /explain` + `POST /explain/video`. Same late-import
  pattern as `multipage.py`. Removes ~135 lines from web.py.
- **Accuracy bench expanded 43 → 74 items.** Now covers CBSE Class
  6–12, ICSE, Maharashtra, TamilNadu, Karnataka, JEE, NEET, UPSC,
  SSC across math, physics, chemistry, biology, polity/geography/
  history/gk. Still structural-only on PRs; live-mode gate stays
  advisory until ≥100 items.
- **Multi-page worker auto-trigger.** `JobRunner` gained an optional
  `post_succeed_hook` and `web._post_succeed_hook` checks each
  job-completion: when all siblings of a multi-page upload are
  succeeded, it pre-stitches the combined MP4 so the UI's first
  `/jobs/{id}/combined.mp4` GET serves the cached file.
- **SMS provider matrix.** `messaging._provider_send()` now
  dispatches to msg91 / twilio / kaleyra / whatsapp_cloud adapters
  in addition to sandbox. Each adapter is a thin urllib wrapper —
  no provider SDK added to requirements.txt. Credentials read
  lazily so a missing key only fails the specific message.
- **First slice of web.py split.** `padhai/routers/multipage.py`
  holds the new `/jobs/{id}/combined.mp4` + `/jobs/{id}/combined`
  endpoints. Pattern: thin router that late-imports web.py
  internals, registered via `padhai.routers.all_routers()`. Sets
  the template for follow-on extractions; web.py is still 15k+
  lines but this is the proof the extraction works.
- **Cypress + Capacitor gap documented.** `mobile/CYPRESS_CAPACITOR.md`
  spells out why Cypress can't drive native plugins (camera, push,
  filesystem) and lists three follow-up options (emulator bridge,
  Detox rewrite, accept the gap). Picked option C for now — the
  SPA-side specs cover most real regression risk.

### Still pending / next up

1. **Accuracy bench coverage to 100+.** 74 items shipped; ~26 more
   needed for the dataset to be statistically robust enough to flip
   the nightly live-mode gate from advisory to blocking. Pure
   content work needing subject-matter review per item.
2. **Mobile native-plugin testing.** SPA is covered; camera / push /
   filesystem / background still need a Detox or
   emulator-bridge harness — see `mobile/CYPRESS_CAPACITOR.md`.
3. **Continue the web.py split.** First slice (`multipage.py`)
   landed; obvious next candidates are the `/explain*` endpoints,
   `/api/v2/video-requests*`, and `/api/parents/*`. Each is
   self-contained.
4. **Central LLM-call wrapper adoption.** `padhai/llm_call.py`
   exists but no surface uses it yet. Migrating tutor / essay /
   lesson opportunistically would remove ~40 lines per surface.

---

## 17. Admin App

`admin/app.py` is a FastAPI app mounted at `/admin/*` on the main
service. Uses `ADMIN_JWT_SECRET` (distinct from `PADHAI_JWT_SECRET`)
and its own user table at `~/.padhai/admin.db` (override via
`ADMIN_DB_PATH`). The admin store is intentionally separate from the
student SQLite so admin logins don't grant the same session cookie
as student logins.

### Pages

- `GET /admin/` — dashboard (or login form if not signed in)
- `GET /admin/jobs` — job queue with retry / cancel
- `GET /admin/topics` — top topics + language usage
- `GET /admin/llm-costs` — daily / 7d / 30d cost rollup, top users
- `GET /admin/api/dashboard` — JSON sibling of the home dashboard
- `GET /admin/api/llm-costs?hours=N` — JSON LLM cost stats

### First-admin bootstrap

The admin signup endpoint is closed by default. To create the first
admin:

```bash
# 1. Set the bootstrap token to something random + remember it
export ADMIN_BOOTSTRAP_TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
# 2. Boot the server (or restart with the env var in place)
# 3. Sign up via curl — replace the email + password
curl -X POST http://localhost:8000/admin/signup \
  -d "email=admin@example.com&password=YourStrongPw1&display_name=Admin&bootstrap_token=$ADMIN_BOOTSTRAP_TOKEN" \
  -i
# 4. Unset the env var after the first admin exists — additional admins
#    must be invited from inside the console
unset ADMIN_BOOTSTRAP_TOKEN
```

Subsequent admin logins go through `POST /admin/login` (sets the
`admin_token` cookie). Production deployments should additionally set
`PADHAI_SUPERUSER_EMAILS` so the `/api/admin/*` routes outside the
mounted Flask app have a non-DB admin gate.

---

## 18. Deployment

- `Dockerfile` — builds the main FastAPI app
- `admin/Dockerfile` — builds the admin app
- `render.yaml` — Render.com deployment config
- `ops/spot-launch.py` / `ops/spot-bootstrap.sh` — AWS spot instance ops

`modal_deploy.py` — Modal.com serverless deployment for GPU workers.

In production: `gunicorn` wraps uvicorn workers. Set `APP_ENV=production`
so secret checks are enforced and dev placeholders block startup.
