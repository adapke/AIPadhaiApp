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
