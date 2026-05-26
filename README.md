# AI Pathshala (padhai)

Multilingual AI teacher for K-12, coaching, and SAARC markets.
Web + mobile + admin console. Started as a scan-to-video CLI in v0.1;
now a full school-ERP-grade platform with an AI tutoring moat at v3.19.

**What it does in one sentence:** A student points their phone camera at
a textbook page; 30 seconds later they have a personalised bilingual
explainer video, a quiz, flashcards, an AI voice tutor, and a mock exam
— all grounded in their exact syllabus.

---

## Feature highlights

### Student-facing learning modules (the SPA)

| Module | What it does |
|---|---|
| **Video Studio** | Upload any image/PDF → AI generates a 60-120s animated lesson video (Lesson JSON → MP4). Board + exam context injected into the prompt. |
| **Explainer** | Text-only lesson with diagrams for a topic typed free-form. No upload needed. |
| **Ask Doubt** | Source-grounded Q&A: Claude answers from the lesson's scene JSON with `[Scene N]` citations. |
| **Live Lecture** | Tap-to-talk mic loop — Web Speech API ASR, Claude reply, browser TTS. General AI tutoring. |
| **Voice Tutor** | Same voice loop but optionally linked to a lesson for material-grounded answers. |
| **Quiz / Tests** | Auto-generated MCQ quiz from any lesson; instant scoring + retake. |
| **Flashcards** | Spaced-repetition deck (SM-2); tap to flip, rate recall, export to Anki. |
| **Audio Recap** | NotebookLM-style narrated recap of a lesson. |
| **Match Game** | Drag-and-drop vocabulary matching from lesson material. |
| **My Library** | Browse all generated lessons; one-click re-watch or chat. |
| **Notes** | Student note-taking linked to lesson timestamps. |
| **Study Plan** | Personalised week-by-week learning plan from NCERT catalogue + uploaded material. |
| **Curriculum Map** | Browse the full board curriculum (CBSE/ICSE/State boards/NEET/JEE); drill into subjects and chapters. |
| **Essay Grader** | Student writes a UPSC/JEE/board descriptive answer → AI scores per rubric criterion → feedback + suggestions. |
| **Math Check** | Paste image URL of handwritten math → AI extracts LaTeX steps → validates each step (first wrong step flagged). |
| **Mock Interview** | UPSC / JEE / placement / NEET PG voice+text interview simulator → turn-by-turn feedback → final scored report. |
| **Adaptive Practice** | Personalised exam-pack where question difficulty self-adjusts per topic mastery. |

### School / Org ERP

- Multi-org hierarchy: School → Classes → Sections → Students
- Attendance, timetable, fee management with invoice + payment
- Teacher Studio: upload material, assign to classes, view student progress
- Parent View: child's activity, progress, daily summary
- Exam mode: anti-cheat (doubt chat locked during active exam)
- Exam scheduling + leaderboard + certificate generation

### Coaching & exam-prep

- UPSC / JEE / NEET / IGCSE / state-board-specific prompt injection via `BOARD_GUIDANCE` dict
- 12 boards supported: CBSE, ICSE, IGCSE, Maharashtra, Karnataka, TamilNadu, AP\_Telangana, UP, NEET, JEE, UPSC, SSC
- 121 curriculum entries seeded across all boards (Classes 6–12 + competitive exams)
- Past-paper question bank (J6), mock engine (K5), readiness score (K6)
- Socratic tutor mode: Socratic questioning instead of direct answers
- Step-by-step math solver (SymPy + LLM fallback)
- Spaced repetition + active recall (SM-2 algorithm)

### Enterprise & B2B

- SAML 2.0 SP + SCIM 2.0 provisioning + Google / Microsoft OIDC SSO
- White-label branding per org (logo, colours, domain)
- Custom top-level domains per org (`school.example.com`)
- Data residency flag (India / EU / Global) with query routing
- SOC 2 Type 1 evidence dashboard + audit log CSV export
- DPDP (India data-protection) compliance hooks
- GeM e-Marketplace procurement catalog (H7)

### Govt & regulatory depth

- NEP 2020 / NCF 2023 alignment reporting (`nep_alignment.py`)
- DIKSHA / NDEAR interoperability (`diksha.py`)
- DigiLocker credential integration (`digilocker.py`)
- State board partnerships module (`state_partnerships.py`)

### Platform & ops

- Feature flags + A/B testing (`feature_flags.py`) — admin-gated GET
- LLM observability: cost, latency, hallucination flagging (`llm_obs.py`)
- Token-prompt caching (`llm_cache.py`) for essay rubric reuse
- Affiliate program (`affiliates.py`) + voucher / bundle engine (`vouchers.py`)
- University / NPTEL extension (`university_partners.py`)
- Corporate training mode (`corporate.py`)
- Customer success automation (`customer_success.py`)
- Sales pipeline integration stub (`sales_pipeline.py`)

---

## Quickstart (dev — zero external services)

```bash
# 1. Install Python deps
pip install -r requirements.txt

# 2. Run the app (SQLite + in-process worker + local disk storage)
PYTHONPATH=. uvicorn padhai.web:app --reload

# 3. Open the SPA
open http://localhost:8000

# OpenAPI docs
open http://localhost:8000/docs
```

The dev path needs no external services. Every subsystem degrades
gracefully when its env var is absent (see Environment Variables below).

### First-time setup

1. Visit `http://localhost:8000`
2. Click **Create account** (top-right)
3. Fill email + password → account is created instantly
4. Generate your first lesson: paste any image URL into **Video Studio**
   and hit **Generate**

You need an `ANTHROPIC_API_KEY` to call Claude for lesson generation.
Without it the job will fail but all UI surfaces still work.

---

## Environment Variables

| Variable | What it activates | Default |
|---|---|---|
| `ANTHROPIC_API_KEY` | Claude Opus 4.7 for lesson / quiz / chat / voice generation | Required for AI features |
| `DATABASE_URL=postgres://…` | Postgres backend (see `G1_POSTGRES_MIGRATION.md`) | SQLite (`padhai.db`) |
| `REDIS_URL=redis://…` | Redis + RQ distributed queue (`G2_QUEUE_MIGRATION.md`) | In-process JobRunner |
| `S3_BUCKET` + `S3_ENDPOINT_URL` | Cloudflare R2 / S3 object storage | Local disk (`./uploads/`) |
| `PADHAI_CDN_BASE_URL` + `PADHAI_CDN_SIGNING_KEY` | Signed CDN URLs (G3) | Direct local URLs |
| `SARVAM_API_KEY` | Sarvam.ai `bulbul:v1` neural TTS — 10 Indian languages | Browser speechSynthesis |
| `BHASHINI_API_KEY` | Bhashini neural TTS (free-tier fallback) | — |
| `FCM_SERVER_KEY` | Android push via Firebase | Disabled |
| `APNS_KEY_ID` + `APNS_TEAM_ID` + `APNS_KEY_PATH` | iOS push via APNs | Disabled |
| `VAPID_PRIVATE_KEY` | Web Push (PWA) | Disabled |
| `PADHAI_REGION=mumbai\|singapore\|eu` | Multi-region awareness (G4) | `global` |
| `PADHAI_TALKING_HEAD_PROVIDER` | Force avatar provider regardless of tier | Auto-selected by tier |
| `SAML_IDP_METADATA_URL` | SAML 2.0 SP (H1) | Disabled |
| `RAZORPAY_KEY_ID` + `RAZORPAY_KEY_SECRET` | Payment processing | Disabled |

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│  Browser / Mobile (PWA + Capacitor iOS/Android)                 │
│  Single-page app inlined in padhai/web.py  (~13.3k LOC total)   │
└────────────────────────┬────────────────────────────────────────┘
                         │ HTTP / WebSocket
┌────────────────────────▼────────────────────────────────────────┐
│  FastAPI app  (padhai/web.py + padhai/routers/*)                 │
│  ~571 routes across web.py (138) + v3 router (433)              │
│  Middleware: rate-limit · auth · browser-redirect · CORS        │
├─────────────────────────────────────────────────────────────────┤
│  AI pipeline                                                     │
│  pedagogy.py      page image → Lesson JSON (Claude Opus 4.7)    │
│  render.py        Lesson JSON → MP4 (ffmpeg + manim)            │
│  voice_sarvam.py  TTS → WAV (Sarvam bulbul:v1)                  │
│  llm_cache.py     prompt-caching wrapper (Q2 cost optimisation) │
│  llm_obs.py       LLM observability (cost · latency · flags)    │
├─────────────────────────────────────────────────────────────────┤
│  Phase 3 AI modules (all fully implemented)                      │
│  tutor.py          persistent AI tutor sessions                  │
│  essay_grader.py   rubric-based essay scoring                   │
│  math_vision.py    handwritten math → LaTeX → step validation   │
│  mock_interview.py voice interview simulator with scoring        │
│  adaptive_packs.py topic-mastery-aware exam pack generator      │
│  socratic_tutor.py Socratic questioning mode                    │
│  step_math.py      step-by-step solver (SymPy + LLM)           │
│  spaced_repetition.py  SM-2 flashcard scheduling               │
├─────────────────────────────────────────────────────────────────┤
│  Job queue (padhai/queue_backend.py)                             │
│    In-process JobRunner (default)                               │
│    Redis + RQ when REDIS_URL is set                             │
├─────────────────────────────────────────────────────────────────┤
│  Storage (padhai/storage.py)                                     │
│    LocalDiskStorage (default)                                   │
│    Cloudflare R2 / S3 when S3_BUCKET is set                     │
├─────────────────────────────────────────────────────────────────┤
│  Database (padhai/db_backend.py)                                 │
│    SQLite padhai.db (default)                                   │
│    Postgres when DATABASE_URL is set                            │
├─────────────────────────────────────────────────────────────────┤
│  GPU worker (Modal — separate deploy)                            │
│    padhai/gpu_worker.py + modal_deploy.py                       │
│    Wav2Lip photoreal avatar for M3+ tier                        │
└─────────────────────────────────────────────────────────────────┘
```

### How a lesson is generated

1. Student submits image + topic + language + board/exam via **Video Studio**
2. `POST /generate` creates a job; worker picks it up
3. `pedagogy.generate_lesson()` calls Claude Opus 4.7 with `thinking: adaptive`
   - `BOARD_GUIDANCE[board]` paragraph is injected into the prompt so
     the lesson is syllabus-specific (e.g., CBSE vs. Maharashtra board)
4. Claude returns structured `Lesson` JSON (title, scenes, quiz, flashcards)
5. `render.py` converts scenes → MP4 via manim + ffmpeg
6. Job result stored; client polls `GET /jobs/{id}` until `status=succeeded`
7. Student can watch, quiz, chat, or create flashcards from the lesson

### How the Voice Tutor works

1. Student taps mic in **Voice Tutor** module
2. Browser Web Speech API transcribes speech locally (no audio leaves device)
3. Transcript + optional lesson\_id + conversation history POSTed to `POST /voice/respond`
4. If lesson\_id provided: Claude answers grounded in the lesson's scene JSON
5. If no lesson\_id: Claude answers as a general tutor (`VOICE_TUTOR_SYSTEM` prompt)
6. Reply text returned; browser `speechSynthesis` reads it aloud

### Browser-friendly error handling

API errors for browser navigation (`Accept: text/html` GET requests) redirect
to `/?next=<path>` instead of returning raw JSON. This prevents the "wall of
JSON" problem when a student navigates to an auth-gated API URL directly.

---

## Testing

### Cypress E2E (13 spec files, ~130 tests)

```bash
# Install Cypress (one time)
npm install

# Run all specs against a running server
npm run cy:run

# Smoke only (health + auth — fastest CI gate)
npm run cy:run:smoke

# Individual suites
npm run cy:run:health       # 01-health.cy.js
npm run cy:run:auth         # 02-auth-api + 03-auth-modal
npm run cy:run:nav          # 04-navigation
npm run cy:run:studio       # 05-video-studio
npm run cy:run:api          # 02 + 06-upload + 09-profile-export
npm run cy:run:curriculum   # 10-curriculum-ui + 13-board-exam-wiring

# Interactive mode
npm run cy:open
```

Cypress spec files live in `cypress/e2e/`. The config (`cypress.config.js`)
points at `http://127.0.0.1:8000` by default.

### Python smoke tests (per-release)

```bash
# Single release (fastest, ~20s)
PYTHONPATH=. python scripts/test_v2_0_4.py

# Full regression matrix (all releases, ~3 min)
for s in scripts/test_v1.py scripts/test_v1_*.py scripts/test_v2_*.py; do
  PYTHONPATH=. python "$s" || break
done
```

### Load testing

```bash
pip install locust
locust -f scripts/loadtest_locustfile.py --host http://localhost:8000 \
       --headless --users 200 --spawn-rate 20 --run-time 60s
```

See `LOAD_TESTING.md` for SLO targets and capacity-planning matrix.

---

## SPA Modules (sidebar navigation)

The single-page app is served from `/` (or `/ui`). The sidebar has four
collapsible groups:

### Daily (always visible)
- **Home** — personalised dashboard with streak, due flashcards, weak topics
- **Study Plan** — week-by-week learning plan
- **Tests & PYQ** — quiz maker + past-year questions
- **Ask Doubt** — lesson-grounded text Q&A

### Create
- **Video Studio** — scan-to-video lesson generator
- **Explainer** — free-form topic → illustrated lesson
- **My Library** — all generated lessons
- **Notes** — timestamped note-taking

### Study
- **Flashcards** — SM-2 spaced-repetition deck
- **Audio Recap** — NotebookLM-style lesson narration
- **Match Game** — vocabulary drag-and-drop
- **Curriculum Map** — browse board syllabus

### Tutor (6 items)
- **Live Lecture** — general AI voice tutor (no lesson context)
- **Voice Tutor** — lesson-grounded AI voice tutor
- **Essay Grader** — rubric-based essay/answer scoring
- **Math Check** — handwritten math image → step validation
- **Mock Interview** — voice/text interview simulator
- **Adaptive Practice** — personalised exam pack by mastery

### School & Org (footer)
- **Teacher Studio** — teacher dashboard + class management
- **Parent View** — parent progress dashboard
- **School / Coaching** — org admin, fees, timetable, exams

---

## Supported Boards & Exams

All 12 boards have detailed `BOARD_GUIDANCE` prompts that inject syllabus
context, typical question styles, and exam-specific language into every
AI-generated lesson, quiz, and tutor response:

| Board / Exam | Coverage |
|---|---|
| CBSE | Classes 6–12, all major subjects |
| ICSE | Classes 9–10, Physics/Chemistry/Biology |
| IGCSE | International curriculum |
| Maharashtra | SSC/HSC state board |
| Karnataka | State board |
| TamilNadu | Samacheer Kalvi |
| AP\_Telangana | State board |
| UP Board | Uttar Pradesh state board |
| NEET | Biology/Physics/Chemistry for medical entrance |
| JEE | Physics/Chemistry/Maths for engineering entrance |
| UPSC | GS papers + CSAT for civil services |
| SSC | Staff Selection Commission exams |

---

## Avatar / Talking Head

| Tier | Provider | Cost/lesson |
|---|---|---|
| M1 Free | Cartoon (CPU, in-process) | ₹0 |
| M2 Student Basic | Cartoon + Sarvam Hindi voice | ₹0.50 |
| M3 Student Pro | Wav2Lip on Modal A10G GPU | ₹3–4 |
| M4 Enterprise | HeyGen / Synthesia / Tavus / D-ID | ₹15–30 |

Force a provider: `export PADHAI_TALKING_HEAD_PROVIDER=wav2lip`

Selection order (auto): Wav2Lip → DeepBrain → Synthesia → Tavus →
HeyGen → D-ID → Cartoon

---

## Codebase layout

```
padhai/                     Python package — the entire backend
├── web.py                  FastAPI app + SPA HTML (~13.3k LOC)
├── routers/
│   ├── v3.py               Phase 3 router (433 routes)
│   ├── public_preview.py   Math + diagram + scorer (rate-limited)
│   ├── catalog.py          Preschool, procurement, region, etc.
│   ├── coaching.py         Tracks list + daily digest
│   ├── question_bank.py    Search + stats + get
│   ├── me.py               Streak, mastery, push tokens (auth-gated)
│   └── orgs_admin.py       Branding, audit, SAML, SCIM, residency
├── api_deps.py             Shared auth helpers (require_user, org_or_404)
│
├── — AI pipeline —
├── pedagogy.py             Image → Lesson JSON (Claude Opus 4.7)
│                           BOARD_GUIDANCE, LEVEL_GUIDANCE, live/voice tutor
├── render.py               Lesson JSON → MP4
├── cache.py                Content-addressed lesson cache
├── voice_sarvam.py         Sarvam bulbul:v1 TTS (10 Indian languages)
├── llm_cache.py            Prompt-caching wrapper (Q2 cost opt)
├── llm_obs.py              LLM observability: cost, latency, flags
│
├── — Phase 3 AI modules —
├── tutor.py                Persistent AI tutor sessions + long memory
├── essay_grader.py         Rubric-based essay scoring (L2)
├── math_vision.py          Handwritten math → LaTeX → step validation (L3)
├── mock_interview.py       Voice interview simulator + scoring (L4)
├── adaptive_packs.py       Mastery-adaptive exam packs (L5)
├── socratic_tutor.py       Socratic questioning mode (L6-adjacent)
├── step_math.py            Step-by-step solver (SymPy + LLM)
├── spaced_repetition.py    SM-2 flashcard scheduling
├── practice_test.py        Practice test generator + runner
├── readiness.py            Exam readiness score
├── mock_engine.py          Mock test engine
│
├── — School ERP —
├── orgs.py                 Schools / classes / members / roles
├── auth.py                 User repo + JWT
├── audit.py                Audit log (H3)
├── push.py                 FCM + APNs + Web Push (I3)
├── saml.py                 SAML 2.0 SP (H1)
├── scim.py                 SCIM 2.0 provisioning (H2)
├── branding.py             White-label themes (E9)
│
├── — Learning features —
├── curriculum.py           121-entry syllabus seed (all 12 boards)
├── curriculum_scorer.py    NEP / NCERT alignment scorer
├── coaching.py             UPSC/JEE/NEET coaching engine
├── question_bank.py        Past-paper question bank
├── mastery.py              Adaptive difficulty tracking (J5)
├── streaks.py              XP + leaderboards + streaks (I4)
├── daily_plan.py           Day-by-day study planner
├── audio_recap.py          NotebookLM-style audio recap
├── retrieval.py            RAG over document_pages
│
├── — Community & social —
├── forums.py               Discussion threads + reactions
├── study_buddies.py        Peer matching
├── mentorship.py           Senior → junior mentor program
├── family_plans.py         Family subscription + sibling discount
│
├── — Marketplace —
├── teacher_publishing.py   Teacher content publishing
├── content_market.py       Content marketplace
├── question_pack_market.py Question bank marketplace
├── affiliates.py           Affiliate program (R4)
├── vouchers.py             Bundle + voucher engine (R3)
│
├── — Govt depth —
├── nep_alignment.py        NEP 2020 / NCF 2023 reporting (P1)
├── diksha.py               DIKSHA / NDEAR interoperability (P2)
├── state_partnerships.py   State board partnerships (P3)
├── digilocker.py           DigiLocker integration (P4)
│
├── — Platform / ops —
├── feature_flags.py        Feature flags + A/B testing (Q1)
├── analytics.py            Event + funnel analytics
├── notifications.py        In-app notification centre
├── rate_limit.py           In-process token bucket
├── queue_backend.py        In-process / Redis-RQ selector (G2)
├── db_backend.py           SQLite / Postgres selector (G1)
├── storage.py              Local disk / R2-S3 selector
├── cdn.py                  HMAC-signed CDN URLs (G3)
├── region.py               Multi-region awareness (G4)
├── soc2.py                 SOC 2 evidence dashboard (H6)
├── dpdp.py                 DPDP compliance hooks
├── residency.py            Data residency flag (H4)
├── custom_domains.py       Per-org TLS domains (H5)
├── observability.py        Sentry / PostHog wiring
│
├── — Infrastructure —
├── db.py                   Core SQLite helpers
├── jobs.py                 Job dataclass + store
├── uploads.py              Upload handling
├── gpu_worker.py           Modal Wav2Lip worker
└── worker_entrypoint.py    CPU worker entrypoint

cypress/
├── e2e/                    13 Cypress spec files (~130 tests)
│   ├── 01-health.cy.js
│   ├── 02-auth-api.cy.js
│   ├── 03-auth-modal.cy.js
│   ├── 04-navigation.cy.js
│   ├── 05-video-studio.cy.js
│   ├── 06-upload-api.cy.js
│   ├── 07-chat-doubt.cy.js
│   ├── 08-flashcards.cy.js
│   ├── 09-profile-export.cy.js
│   ├── 10-curriculum-ui.cy.js
│   ├── 11-quiz-maker.cy.js
│   ├── 12-live-lecture.cy.js
│   └── 13-board-exam-wiring.cy.js
└── support/
    ├── commands.js         Custom commands: cy.apiSignup, cy.seedUser, etc.
    └── e2e.js              Global uncaught-exception suppressor

mobile/
├── capacitor.config.json   Main student app (iOS + Android)
├── parent/                 Parent app (separate store listing)
└── teacher/                Teacher app (separate store listing)

admin/                      Separate FastAPI admin console
scripts/
├── test_v1.py … test_v3_19.py   Per-release Python smoke suites
└── loadtest_locustfile.py        Locust load-test harness
```

---

## API surface

| Area | Example endpoints |
|---|---|
| **Lesson generation** | `POST /generate`, `GET /jobs/{id}`, `GET /jobs/{id}/video` |
| **Voice** | `POST /live/respond`, `POST /voice/respond` |
| **Chat / doubt** | `POST /chat/{lesson_id}` |
| **Curriculum** | `GET /tiers`, `GET /curriculum/index` |
| **AI tutor** | `POST /api/tutor/sessions`, `POST /api/tutor/sessions/{sid}/message` |
| **Essay grader** | `GET /api/essay/rubrics`, `POST /api/essay/submissions` |
| **Math vision** | `POST /api/math-vision/submit`, `POST /api/math-vision/{sid}/validate` |
| **Mock interview** | `POST /api/mock-interviews`, `POST /api/mock-interviews/{iid}/answer` |
| **Adaptive packs** | `POST /api/adaptive-packs`, `GET /api/adaptive-packs/me` |
| **Step math** | `POST /api/step-math/problems`, `POST /api/step-math/problems/{pid}/llm-steps` |
| **Practice tests** | `POST /api/practice-tests`, `POST /api/practice-tests/{tid}/submit` |
| **Flashcards** | `GET /api/lessons/{id}/flashcards` |
| **Audio recaps** | `POST /api/audio-recaps`, `GET /api/audio-recaps/{rid}` |
| **Orgs / school** | `POST /api/orgs`, `GET /api/orgs/{id}/members`, `POST /api/orgs/{id}/fees/invoices` |
| **Feature flags** | `GET /api/admin/flags` (admin only), `GET /api/me/flags` |
| **LLM observability** | `GET /api/admin/llm/stats`, `POST /api/llm/calls/{id}/flag` |
| **Admin curriculum** | `GET/POST/PUT/DELETE /api/admin/curriculum` |

Full interactive docs at `http://localhost:8000/docs` (OpenAPI/Swagger UI).

---

## Production deployment

```yaml
# render.yaml — autoDeploy: false by default
# Trigger deploys from the Render dashboard or flip autoDeploy: true
```

The recommended production stack:

```
Render.com (web service)  ←→  Postgres (managed)
                          ←→  Redis (managed)
                          ←→  Cloudflare R2 (media storage)
                          ←→  Cloudflare CDN (signed URLs)
                          ←→  Modal.com (Wav2Lip GPU jobs)
```

Each component is optional; the app runs on SQLite + local disk without
any of them.

### Health checks

- `GET /health` — returns `{"status": "ok"}` with DB connectivity
- `GET /healthz` — alias (Kubernetes / ops tooling compatibility)

---

## Versioning & roadmaps

| Roadmap | Scope | Status |
|---|---|---|
| `ROADMAP.md` | v0.10 → v1.0 (28 items — school ERP MVP) | All shipped |
| `ROADMAP_V2.md` | v1.1 → v2.0 (29 items — scale + sell + retain) | All shipped |
| `ROADMAP_V3.md` | v2.1 → v3.0 (30 items — AI moat + live + marketplace) | Shipped through v3.19 |

**Current version:** v3.19 (see `CHANGELOG.md` for full history).

Key v3 milestones shipped:
- **L1** Voice Tutor — lesson-grounded voice Q&A
- **L2** Essay Grader — rubric-based scoring
- **L3** Math Check — handwritten math vision
- **L4** Mock Interview — AI interview simulator
- **L5** Adaptive Practice — mastery-adaptive exam packs
- **L6** LLM Observability — cost + latency + hallucination tracking
- **M1** Live cohort classes
- **M2** Doubt clearing queue
- **M4** Tutor marketplace
- **O3** Question bank marketplace
- **P4** DigiLocker integration
- **Q1** Feature flags + A/B testing
- **R2** University / NPTEL extension
- **R3** Bundle + voucher engine
- **R4** Affiliate program

---

## Other documentation

| File | Purpose |
|---|---|
| `CHANGELOG.md` | Curated per-release history |
| `IMPLEMENTATION_STATUS.md` | PRD section-by-section shipped/deferred tracker |
| `ROADMAP_V3.md` | Full Phase 3 feature specs with data models + API + effort |
| `G1_POSTGRES_MIGRATION.md` | Postgres cutover runbook |
| `G2_QUEUE_MIGRATION.md` | Redis + RQ cutover runbook |
| `G4_MULTI_REGION.md` | Mumbai + Singapore failover runbook |
| `G5_DISASTER_RECOVERY.md` | Backup + restore + drill protocol |
| `A1_PHOTOREAL_DEPLOY.md` | Wav2Lip GPU host setup |
| `H7_GEM_PROCUREMENT.md` | GeM e-Marketplace listing paperwork |
| `MOBILE_BUILD.md` | iOS + Android Capacitor build runbook |
| `LOAD_TESTING.md` | Locust harness + SLO targets + capacity matrix |
| `RUNNING_LOCALLY.md` | Step-by-step local dev guide |

---

## License

Proprietary — AI Pathshala / padhai project.
