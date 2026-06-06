# Onboarding — AIPadhaiApp

Your first day on this codebase. Read in order; skim later.

- [README.md](README.md) — product overview, 30-second pitch
- [CONTRIBUTING.md](CONTRIBUTING.md) — dev setup, code conventions
- [CLAUDE.md](CLAUDE.md) — the project bible (long, but §1-§5 + §10-§14 are essential)
- [SECURITY.md](SECURITY.md) — disclosure policy + hardening invariants
- this file — codebase map + maintained tools

---

## 5-minute version: what is this?

FastAPI EdTech platform for Indian K-12 + competitive-exam students.
Backend in Python (`padhai/`), admin app in Flask-style FastAPI
(`admin/`), SPA frontend embedded in `padhai/web.py:_INDEX_HTML`.
SQLite for dev, Postgres for production. Anthropic Claude is the
only AI provider — Haiku for cheap surfaces, Sonnet for balanced,
Opus for full lesson generation.

The product surface, top-level:

- **7 learning modules** — voice tutor, live lecture, essay grader,
  math vision, mock interview, adaptive practice, practice tests
- **AI lesson videos** generated from textbook page images
- **DPDP §9 parental consent flow** for students under 18
- **School ERP** — orgs, classes, members, attendance, timetable,
  assignments, fees, exams, branding, audit, SCIM provisioning
- **Mobile shells** — three Capacitor apps (student/parent/teacher)

---

## Where do I look for X?

| You want to | Read |
|---|---|
| Add a new HTTP endpoint | `padhai/routers/<area>.py` — pick the right slice |
| Call Claude from a new module | `padhai/llm_call.py:call_claude()` |
| Add a new Claude model | `padhai/models.py` — bump the constant only |
| Hash a password / mint a JWT | `padhai/auth.py` |
| DPDP minor consent flow | `padhai/dpdp.py` |
| Render a lesson video | `padhai/pedagogy.py` (planning) + `padhai/render.py` (ffmpeg) |
| Score an essay | `padhai/essay_grader.py` |
| Score a mock interview | `padhai/mock_interview.py` |
| SM-2 flashcards | `padhai/spaced_repetition.py` |
| Org / class / school ERP storage | `padhai/orgs.py` |
| Daily LLM cost cap | `padhai/llm_obs.py:check_daily_cap()` |
| SQLite path | `padhai/db.py:sqlite_path()` (never re-implement) |
| Prompt caching | `padhai/llm_cache.py:with_caching()` |
| Background job pipeline | `padhai/jobs.py:JobRunner` |
| Rate limiter | `padhai/rate_limit.py` (token bucket) |
| Provider key validation | `padhai/web.py:_PROVIDER_KEY_SPECS` |

---

## The 19 router slices (alphabetical)

Each `padhai/routers/<name>.py` is self-contained. Convention: lazy
`from .. import web as _web` inside endpoints so the module imports
standalone for unit testing. Registry: `padhai/routers/__init__.py`.

| Slice | File | Routes | What it covers |
|---|---|---|---|
| 1 | `multipage.py` | 2 | Multi-page video stitching |
| 2 | `explainer.py` | 2 | `/explain` + `/explain/video` |
| 3 | `v2_video.py` | 2 | Video-request status + result |
| 4 | `parents.py` | 5 | Parent ↔ child linking |
| 5 | `orgs_api.py` | 6 | Org CRUD (me / create / detail / members / roster) |
| 6 | `orgs_classes.py` | 2 | Class list + create |
| 7 | `orgs_leaderboard.py` | 1 | XP / streaks leaderboard |
| 8 | `orgs_attendance.py` | 4 | Daily roll + per-student + range summary |
| 9 | `orgs_assignments.py` | 4 | List, create, completion, stats |
| 10 | `orgs_fees.py` | 7 | Structures + invoices + Razorpay |
| 11 | `orgs_exams.py` | 6 | Create / take / submit / grade |
| 12 | `branding.py` | 3 | Resolve + logo upload + serve |
| 13 | `scim.py` | 4 | SCIM 2.0 IdP provisioning |
| 14 | `notifications.py` | 5 | Feed + compose + push fan-out |
| 15 | `orgs_schedule.py` | 4 | Timetable + today + student history |
| 16 | `lesson_detail.py` | 5 | Lesson cache derivatives: flashcards, quiz, notes, SM-2 |
| 17 | `lesson_chat_recap.py` | 3 | RAG chat + recap text + recap MP3 |
| 18 | `curriculum.py` | 2 | NCERT match + catalogue browse |
| 19 | `uploads.py` | 3 | Upload + analyze + lookup |

Plus the older slices wired before the polish-N sprints: `catalog`,
`coaching`, `question_bank`, `me`, `orgs_admin`, `v3`, `learning`,
`uploads_ai`, `onboarding`, `dashboard`, `pricing`, `tutor_stream`,
`offline`, `messaging`, `digilocker`, `commerce`, `doubt_ai`,
`ux_signals`, `public_preview`.

---

## Maintained tooling (don't reinvent)

| Tool | What it does | Why we have it |
|---|---|---|
| `padhai/models.py` | Central Claude model-ID constants | Each rename used to require touching 13+ files. Now one. |
| `padhai/db.py:sqlite_path()` | Shared SQLite path resolver | Closed the bug class where modules wrote to different DBs |
| `padhai/llm_call.py:call_claude()` | Wrapped Claude call | Auto-records cost + enforces daily cap |
| `scripts/fix_b904.py` | AST-based B904 mass fixer | Ruff can't autofix B904; this can |
| `scripts/check_model_constants.py` | Lock invariant #5 (no literal `"claude-*"` outside `models.py`) | Closed bug #8 once and for all |
| `scripts/check_router_registry.py` | Lock the router slice ↔ `_ROUTER_NAMES` invariant | Catches half-wired router additions |
| `scripts/backup_sqlite.sh` | Online sqlite3 .backup | Safe under concurrent writes |
| `make verify` | One-command pre-PR gate | Bundles lint + 2 guards + pytest + structural bench (~20s) |
| `scripts/run_accuracy_bench.py` | Lesson-generation regression bench | 280 items across 9 boards/exams |

---

## Key invariants — do not break these

These are codified in CI gates, lint rules, or startup asserts.
Violating them is a sev1 incident:

1. **JWT secret never `dev-*` / `change-me` / `placeholder` in production.**
   `padhai/auth.py:_jwt_secret()` enforces this on boot when
   `APP_ENV=production`.

2. **DPDP minor age threshold is 18, not 13.**
   `padhai/dpdp.py:MINOR_AGE_THRESHOLD = 18`. Privacy Policy + UI
   + lock flow all reference 18.

3. **Admin gate never falls back to "every signed-in user" in
   production.** `web.py:_validate_admin_gate()` refuses to boot
   when `APP_ENV=production` with no admin source configured.

4. **Postgres `psycopg.connect()` always passes
   `options="-c search_path=public"`.** Otherwise fresh-DB
   migrations crash. See CLAUDE.md §10.

5. **Model IDs come from `padhai/models.py`, never literal strings.**
   `models.py` carries a startup assert against the buggy bare
   `claude-haiku-4-5` form.

6. **Raise from inside except always uses `from err` or `from None`.**
   B904 is blocking in ruff; the AST mass fixer handles cleanup.

7. **Every SQL statement uses parameter binding.** No f-strings in
   SQL, even for "trusted" values. SECURITY.md §SQL safety.

8. **Multi-tenant gate before data access.** Every
   `/api/orgs/{org_id}/...` route calls `_require_org_role()`
   before touching `_orgs.*` data. Router unit tests
   (`tests/test_routers.py`) check this for the 14 extracted slices.

---

## First-day checklist

```bash
# 1. Clone + venv
git clone <repo> && cd AIPadhaiApp
python -m venv .venv && source .venv/bin/activate  # or .venv\Scripts\activate
pip install -r requirements.txt -r requirements-test.txt

# 2. Generate dev secrets + .env
cp .env.example .env
echo "PADHAI_JWT_SECRET=$(python -c 'import secrets; print(secrets.token_urlsafe(48))')" >> .env

# 3. Run the verify gate — should be ~20s, all green
make verify

# 4. Start the dev server
bash scripts/run_local.sh                  # Linux/macOS
# OR
powershell -ExecutionPolicy Bypass -File scripts/run_local.ps1  # Windows

# 5. Browse http://localhost:8000/ui and sign up

# 6. Optional: run Cypress E2E (server must be running on :8000)
npx cypress run
```

If `make verify` fails on a fresh clone, that's a real bug — please
open an issue with the output.

---

## Where to ask

- **Project questions** — open a discussion on the repo
- **Security issues** — `security@aipathshala.in` (see SECURITY.md)
- **Architecture decisions** — CLAUDE.md §5 is the source of truth

Last reviewed: 2026-06-06.
