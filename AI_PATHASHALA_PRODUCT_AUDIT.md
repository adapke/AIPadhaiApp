# AI Pathashala — Comprehensive Product Audit
**Version:** v3.19.0 | **Audit Date:** 2026-05-24 | **Codebase:** adapke/AIPadhaiApp

> Perspective: Solution Architect + EdTech Product Architect + AI Learning Designer + UI/UX Lead + Indian Education Domain Expert + Teacher + Student Advocate + Parent Advocate + Technical Delivery Lead

---

## 1. Executive Summary

AI Pathashala is a **mature backend platform masquerading as an early-stage product**. The codebase contains 118 Python modules, 11,938 LOC in `web.py` alone, 592 API routes, 155+ database tables, and implementations for every feature a competitive Indian EdTech product needs — NEET/JEE coaching packs, SM-2 flashcards, AI tutoring, live classes, fee management, DPDP compliance, SAML SSO, and video generation across 5 subscription tiers.

The crisis: **almost none of it is reachable by a student sitting at a browser.** The frontend is a single 925-line Python string of embedded HTML/JS (`home_ui.py`). Until this session's fixes, every API call was unauthenticated (missing JWT headers), navigation routes were wrong, and job IDs that weren't UUIDs crashed the server with HTTP 500.

**What actually works end-to-end today:** auth (signup/login/JWT), video generation pipeline (upload → Claude vision → TTS → MP4), job status polling, basic org/class management.

**What exists in backend but is UI-orphaned:** flashcards, quiz engine, AI tutor, forums, doubt clearing, exam packs, coaching tracks, spaced repetition, parent portal, teacher publishing, leaderboards, adaptive mock tests, push notifications, payment/fee management, attendance, timetable, mentor program, diagnostic reports.

**Recommendation:** Do not build new features. Spend 30 days connecting existing backend to a real frontend. The product is built — it just isn't visible.

---

## 2. Current Implementation Audit

### 2.1 Implementation Matrix

| Subsystem | Module | Backend API | Database Tables | Frontend UI | End-to-End Working |
|-----------|--------|-------------|-----------------|-------------|-------------------|
| Auth (local) | `auth.py` | ✅ signup, login, JWT | users | ✅ login form in home_ui.py | ✅ FIXED (JWT headers added) |
| Auth (SSO/SAML) | `auth.py`, `saml.py`, `sso.py` | ✅ SAML ACS, SCIM, OAuth | sso_providers | ⛔ no UI | ❌ backend only |
| Video generation | `pedagogy.py`, `render.py` | ✅ POST /lessons, jobs/* | lessons, jobs, videos | ⚠️ "Try it" drawer only | ⚠️ API-only, no guided flow |
| Upload + Ingest | `ingest.py`, `uploads.py` | ✅ POST /api/uploads | document_pages | ⚠️ drawer only | ⚠️ PDF/JPG work; PPTX/DOCX stub |
| AI Tutor (chat) | `tutor.py`, `tutor_grounding.py` | ✅ POST /chat/{lesson_id} | tutor_conversations | ⛔ no UI | ❌ backend only |
| Flashcards + SM-2 | `spaced_repetition.py` | ✅ POST /lessons/{id}/flashcards | flashcard_decks, flashcards, flashcard_reviews, flashcards_user_state | ⛔ no UI | ❌ backend only |
| Quiz engine | `question_bank.py` | ✅ POST /lessons/{id}/quiz | question_bank, question_options | ⛔ no UI | ❌ backend only |
| Doubt clearing | `doubt_clearing.py` | ✅ /api/doubts | doubt_requests | ⛔ no UI | ❌ backend only |
| Exam packs (NEET/JEE/UPSC) | `exam_taxonomy.py`, `adaptive_packs.py` | ✅ enrollment, topics, packs | exam_packs, exam_pack_enrollments, exam_topics | ⛔ no UI | ❌ backend only |
| Coaching tracks | `coaching.py` | ✅ POST /api/coaching/tracks | coaching_tracks, practice_attempts | ⛔ no UI | ❌ backend only |
| Readiness score | `readiness.py` | ✅ computed score | readiness_scores | ⛔ no UI | ❌ backend only |
| Daily study plan | `daily_plan.py` | ✅ /api/home/me/dashboard | study_plan_items | ⛔ no UI | ❌ backend only |
| Student home dashboard | `student_home.py` | ✅ GET /api/home/me/dashboard | (aggregates multiple) | ⛔ no UI | ❌ API ready, no screen |
| Live classes | `live_classes.py` | ✅ CRUD + attendees | live_classes, live_class_attendees | ⛔ no UI | ❌ backend only |
| Forums | `forums.py` | ✅ threads, posts, flags | forum_threads, forum_posts, forum_flags | ⛔ no UI | ❌ backend only |
| Study buddies | `study_buddies.py` | ✅ /api/buddies/me | study_buddy_pairs | ⛔ no UI | ❌ backend only |
| Mentor program | `mentorship.py` | ✅ profiles, sessions | mentor_profiles, mentor_sessions, mentor_reviews | ⛔ no UI | ❌ backend only |
| Audio recap (NotebookLM-style) | `audio_recap.py` | ✅ POST /lessons/{id}/recap | audio_clips | ⛔ no UI (GET endpoint exists) | ⚠️ audio generated, no player |
| Step-by-step math solver | `step_math.py` | ✅ | math_steps | ⛔ no UI | ❌ backend only |
| Org management | `orgs.py` | ✅ full CRUD | orgs, org_members, classes | ⚠️ partial in home_ui.py | ⚠️ basic create/list works |
| Assignment + grading | `web.py` | ✅ CRUD + stats | assignments, assignment_completions | ⛔ partial in home_ui.py | ⚠️ teacher side only |
| Attendance | `web.py` | ✅ daily + summary | attendance_log | ⛔ no dedicated screen | ❌ API-only |
| Timetable | `web.py` | ✅ CRUD | class_timetable | ⛔ no UI | ❌ backend only |
| Fee management | `web.py`, `razorpay_client.py` | ✅ structures, invoices, pay | fee_structures, fee_invoices | ⛔ no UI | ❌ backend only |
| Razorpay payment | `razorpay_client.py` | ✅ order + webhook | — | ⛔ no UI | ❌ backend only |
| Push notifications | `push.py`, `notifications.py` | ✅ FCM+APNs+Web Push | push_log, notifications | ⛔ no user UI | ⚠️ platform configured, no opt-in screen |
| WhatsApp / SMS | `messaging.py` | ✅ | message_log | ⛔ no UI | ❌ backend only |
| Leaderboard | `web.py` | ✅ GET /api/orgs/{id}/classes/{id}/leaderboard | — | ⛔ no UI | ❌ backend only |
| Streaks | `streaks.py` | ✅ daily streak tracking | user_streaks | ⛔ no UI | ❌ backend only |
| RAG / retrieval | `retrieval.py`, `citations.py` | ✅ BM25 chunking + retrieval | doc_chunks, chunk_embeddings | ⛔ no UI | ⚠️ works within tutor, no standalone UI |
| Personalization | `personalization.py` | ✅ profiles, mode/level/language | — | ⛔ no settings screen | ❌ not user-configurable |
| DPDP compliance | `dpdp.py` | ✅ consent, audit trail | dpdp_consents | ⚠️ parent consent email flow | ⚠️ under-14 consent works; adult flow missing UI |
| SOC 2 audit logs | `soc2.py`, `audit.py` | ✅ | soc2_events | ⛔ no UI | ✅ logging works silently |
| SCIM 2.0 | `scim.py` | ✅ Users CRUD | — | N/A (enterprise API) | ✅ |
| Branding / white-label | `branding.py` | ✅ logo upload, domain resolve | org_branding | ⛔ no UI | ❌ backend only |
| Offline packs | `offline_packs.py` | ✅ | offline_pack_jobs | ⛔ no UI | ❌ backend only |
| LLM cost obs | `llm_obs.py`, `llm_cache.py` | ✅ caching + metrics | llm_cache, llm_obs_events | ⛔ no admin UI | ⚠️ data captured, not visible |
| Navigation manifest | `navigation.py` | ✅ GET /api/navigation/manifest | — | ✅ drives home_ui.py drawer | ✅ FIXED (wrong routes corrected) |
| Job queue (UUID guard) | `db.py` | ✅ PostgresJobStore | jobs | N/A | ✅ FIXED (UUID validation added) |

### 2.2 Frontend Reality

The entire frontend is two Python triple-quoted strings inside `padhai/home_ui.py`:
- `HOME_HTML` — authenticated home page, sidebar + chip grid, inline drawers
- `LANDING_HTML` — unauthenticated landing with inline login/signup forms

There is **no separate frontend repository, no React/Vue/Angular SPA, no mobile app code in this repo** (Capacitor wrapper is mentioned in docs but not present). All UI is embedded server-side HTML with vanilla JS.

---

## 3. Broken / Incomplete Functionality

### P0 — Crashes or Blocks Product Use

| Issue | Location | Impact | Fix |
|-------|----------|--------|-----|
| JWT not sent on API calls | `home_ui.py` fetch() | All API calls return 401 | **FIXED** — `_authHeaders()` added |
| JWT not stored after login | `home_ui.py` landing | Login succeeds but session lost on next page | **FIXED** — `localStorage.setItem('padhai_token', data.token)` added |
| Non-UUID job IDs crash with HTTP 500 | `db.py` PostgresJobStore.get() | `/jobs/{id}` returns 500 on bad IDs | **FIXED** — UUID validation guard added |
| Navigation routes wrong (6 broken URLs) | `navigation.py` | "Try it" in drawer fails silently | **FIXED** — corrected all 6 routes |
| No landing page exists as GET HTML | `web.py` | Users typing `/` or `/landing` get JSON or redirect loop | ✅ exists at `/landing` — LANDING_HTML served |
| PPTX / DOCX ingest raises NotImplemented | `ingest.py` | Users uploading .pptx get 500 | Return 400 with clear message |
| `PADHAI_JWT_SECRET` not set → RuntimeError on startup | `auth.py` | Server crashes with cryptic error | Add startup check with friendly message in lifespan |

### P1 — Features That Exist in Backend But Have No Student-Facing Path

- **Flashcard study flow** — SM-2 is implemented, cards can be generated, but there is no screen to actually study them. The student can never see their deck.
- **AI Tutor chat** — `POST /chat/{lesson_id}` exists and works but there is no chat UI anywhere in home_ui.py.
- **Quiz after lesson** — `POST /lessons/{id}/quiz` generates a quiz, but there is no quiz UI. The student never sees it.
- **Exam Pack enrollment** — Student can enroll in NEET/JEE/UPSC packs via API, but there is no enrollment flow, no pack selection screen.
- **Daily study plan** — `GET /api/home/me/dashboard` returns a rich plan object, but home_ui.py does not render it. The student sees a generic chip grid instead of their personalised plan.
- **Doubt clearing queue** — backend ready for both student submission and tutor response; no UI for either.
- **Streaks and gamification** — data exists but nothing is shown on any screen.
- **Notifications bell** — `GET /api/notifications/me` exists; no notification bell in home_ui.py.

### P2 — Broken UX/Flow Issues

- **No "back" flow after video generation** — after POST /lessons, student gets a job_id but there is no polling UI that shows progress and then auto-plays the video.
- **"Try it" drawer shows raw JSON** — for anything that returns structured data, the drawer does a pretty-print of JSON. Students should see a meaningful rendered result.
- **Personalization not user-editable** — language, level, mode are hardcoded per form. No settings/profile screen.
- **Parent portal completely missing from frontend** — `GET /api/parents/children` exists but there is no parent-role screen.
- **Teacher publishing flow has no UI** — `teacher_publishing.py` module exists with full backend, no corresponding screen.
- **Mobile layout of home_ui.py** — sidebar is desktop-only. On mobile the chip grid stacks but the sidebar overlaps. No hamburger menu.

### P3 — Quality and Completeness Gaps

- PPTX/DOCX ingestion stubs (`raise ValueError`) — should return 400 with message, not 500.
- YouTube URL ingest is stub — documented in `ingest.py` but not implemented.
- Bhashini TTS requires API registration — default gTTS sounds robotic, especially for Hindi content.
- `PADHAI_REQUIRE_AUTH` defaults to 0 (unauthenticated) — production deployments could accidentally expose all data.
- LLM cost visibility — operators cannot see spend from any admin UI.
- No rate limiting on AI endpoints — a single user can drain Anthropic API budget.
- Missing OTP / forgot-password flow — only email+password, no reset mechanism.

---

## 4. Competitive Gap vs StudyFetch and Market Leaders

| Feature | AI Pathashala | StudyFetch | Quizlet | NotebookLM | Khanmigo |
|---------|--------------|------------|---------|------------|---------|
| Upload → study material | ✅ PDF/JPG; PPTX stub | ✅ PDF/PPT/YT/audio | ✅ manual + import | ✅ PDF/Docs/URLs | ❌ Khan content only |
| AI video from notes | ✅ core differentiator | ✅ (Studia AI) | ❌ | ❌ | ❌ |
| Flashcard study (SM-2) | ✅ backend, ❌ no UI | ✅ polished | ✅ best-in-class | ❌ | ❌ |
| Practice quiz | ✅ backend, ❌ no UI | ✅ | ✅ | ❌ | ✅ |
| AI tutor / chat | ✅ backend, ❌ no UI | ✅ AI chat | ❌ | ✅ NotebookLM Q&A | ✅ Khanmigo |
| Audio podcast from notes | ✅ backend (recap.mp3) | ❌ | ❌ | ✅ Audio Overview | ❌ |
| NEET/JEE/UPSC exam packs | ✅ backend, ❌ no UI | ❌ | ❌ | ❌ | ❌ |
| Indian language TTS | ✅ Bhashini/Sarvam | ❌ English only | ❌ | ❌ | ❌ |
| Parent monitoring | ✅ backend, ❌ no UI | ❌ | ❌ | ❌ | ❌ |
| School/LMS integration | ✅ SAML+SCIM | ❌ | ✅ Teacher | ❌ | ✅ Khan schools |
| Offline access | ✅ backend, ❌ no UI | ❌ | ✅ premium | ❌ | ❌ |
| Spaced repetition scheduler | ✅ SM-2 backend | ✅ | ✅ | ❌ | ❌ |
| Live classes | ✅ backend, ❌ no UI | ❌ | ❌ | ❌ | ❌ |
| Fee/payment management | ✅ Razorpay | ❌ | ❌ | ❌ | ❌ |
| Progress analytics | ✅ backend, ❌ no UI | ✅ | ✅ | ❌ | ✅ |
| Mobile app | ⚠️ Capacitor mentioned, not in repo | ✅ | ✅ | ✅ | ✅ |

**Net assessment:** AI Pathashala's backend beats every competitor in feature breadth for the Indian market. The product loses on execution: every gap is a missing UI screen, not a missing backend feature. StudyFetch wins today purely because their flashcard/quiz/chat UI is polished and reachable in 3 clicks. AI Pathashala requires API calls with curl.

---

## 5. India-Specific Product Requirements

### 5.1 What's Already Handled

- **Bhashini TTS** (Government of India, free for DPIIT startups) — 10 Indic languages
- **Sarvam Bulbul** (human-like Hindi TTS) — `voice_sarvam.py`
- **CBSE/ICSE/State Board taxonomy** — seeded in `exam_taxonomy.py`
- **NEET/JEE/UPSC/SSC/IBPS packs** — seeded as exam packs
- **DPDP Act 2023 compliance** — `dpdp.py` with under-14 parental consent
- **DigiLocker integration** — `digilocker.py` module present
- **NEP 2020 alignment** — `nep_alignment.py` module present
- **DIKSHA integration** — `diksha.py` module present
- **Razorpay payments** — HMAC-SHA256 webhook validation
- **Multi-language content** — 10 languages in `pedagogy.py`

### 5.2 Gaps That Are India-Critical

| Requirement | Current State | Priority |
|-------------|---------------|----------|
| Low-bandwidth mode / offline study | Backend ready (`offline_packs.py`), no UI | P1 |
| 2G/3G data-saver video quality | Not implemented; only one video quality per job | P1 |
| Regional board content (Maharashtra, TN, UP, Rajasthan) | Taxonomy has CBSE/NEET/UPSC, state boards not seeded | P1 |
| UPI payment (via Razorpay) | Razorpay configured, no payment UI | P1 |
| SMS OTP login (no smartphone dependency) | Email-only auth; no SMS OTP | P1 |
| Vernacular UI (Hindi/Tamil interface) | UI is English-only | P2 |
| Class 6-10 NCERT alignment | Taxonomy present, content not mapped | P2 |
| Accessibility (screen reader, high contrast) | Not implemented | P2 |
| Government scheme integration (DIKSHA, NIPUN) | Module present, not wired to UI | P2 |
| Video play on low-end Android (MediaSession API, fallback) | Not tested | P2 |
| Parent communication via WhatsApp | `messaging.py` present, no UI | P2 |
| Aadhaar-based age verification (DPDP) | DigiLocker module present, not wired | P3 |

---

## 6. Role-Based User Journey Design

### 6.1 Student Journey (Primary User)

**Onboarding (Day 1):**
1. Land at `/` → see LANDING_HTML
2. Sign up with email + DOB → DPDP parent consent (if under 14)
3. Select exam target (NEET/JEE/CBSE 10/CBSE 12/UPSC/SSC) → enroll in exam pack
4. Set language preference + level → PersonalizationProfile created
5. Land at `/home` → see personalised dashboard with today's plan

**Daily Study Loop:**
1. `/home` shows: today's plan (n items), streak, readiness score for chosen exam
2. Plan item types: video lesson, flashcard review (due cards from SM-2), quiz, doubt session
3. Student taps lesson → upload page or camera → video generated → watched → quiz
4. After lesson: auto-generated flashcards added to daily review queue
5. SM-2 scheduler determines next review date per card
6. Doubt on any topic → photo + question → AI answers in <1min (or human tutor in 15min)
7. End of day → streak updated, push notification tomorrow morning

**Exam Prep Mode (30 days before exam):**
1. Mock test → adaptive question selection from question bank
2. Readiness score updated → weak topics identified
3. Focused video lessons on weak topics auto-suggested
4. Performance analytics → parent notified via WhatsApp

### 6.2 Teacher Journey

**Setup:**
1. Org admin creates teacher account → teacher sees teacher-mode home
2. Teacher creates class group, adds students (bulk roster CSV or invite link)
3. Teacher creates assignment: topic + deadline + video type
4. Assignment appears in student's daily plan automatically

**Daily Teaching:**
1. Teacher uploads chapter → AI generates video lesson + quiz automatically
2. Teacher reviews/edits generated quiz questions before publishing
3. Teacher starts live class → students join via link → real-time doubt queue
4. After class → attendance logged → assignment marked completed
5. Teacher sees per-student performance, flags struggling students
6. Teacher publishes study material to marketplace (optional revenue share)

### 6.3 Parent Journey

**Setup:**
1. Student sends parent link invite → parent creates account
2. Parent linked to student(s) via `parent_link` flow
3. Parent sets daily study goal (hours), exam target

**Ongoing:**
1. Parent dashboard: child's streak, today's study time, weak subjects
2. Weekly WhatsApp/SMS report: progress vs. peers, readiness score
3. Fee payment: view invoices, pay via UPI/card
4. Alert: child missed 3 days → parent notified

### 6.4 School Admin Journey

**Setup:**
1. Admin creates org → adds teachers + students (SCIM or CSV)
2. Configures white-label (logo, colour scheme, custom domain)
3. Configures fee structures per class
4. Enables SAML SSO (Google Workspace / Microsoft 365)

**Operations:**
1. Dashboard: enrollment counts, active users, lesson completions
2. Class performance comparison, top/bottom performers
3. Fee collection status, outstanding invoices
4. Exam results import → alignment to school's question bank

### 6.5 Coaching Institute Journey (NEET/JEE/UPSC)

**Different from school:** batch-based, subject-wise, test series driven

1. Create coaching org → add batches (JEE 2027, NEET 2027)
2. Upload test series → AI generates 50-question mock from question bank
3. Students get adaptive tests, weak topic lessons, full-length mock schedule
4. Leaderboard per batch → peer pressure feature (Indian coaching culture)
5. Doubt clearing queue managed by subject experts
6. Performance PDF reports for parents at month-end

### 6.6 Super Admin (Platform Operator)

1. LLM cost dashboard: daily spend per org, per feature, per model
2. Feature flag management (`feature_flags.py`)
3. Subscription tier management, coupon/voucher management
4. Moderation queue for community content (`moderation_queue.py`)
5. SOC 2 audit log viewer
6. System health: job queue depth, error rate per endpoint

---

## 7. UI/UX Screen Requirements

All screens below need to be built. The existing `home_ui.py` HTML must be expanded — or replaced with a proper SPA — to serve these. Recommended: keep server-rendered HTML for SEO/performance, add Alpine.js or HTMX for interactivity without a build pipeline.

### 7.1 Student Screens

| Screen | Route | Key Elements |
|--------|-------|-------------|
| Landing / Onboarding | `/` | Hero, sign-up form, exam selector, language picker, social proof |
| Login | `/landing` (existing) | Email+password, SSO buttons, forgot-password link |
| Onboarding wizard | `/onboarding` | 3 steps: exam target → class/level → language. Creates PersonalizationProfile |
| Home / Dashboard | `/home` | Streak flame, readiness donut, today's plan list, continue lesson card, due flashcards count, upcoming mock test |
| Lesson generator | `/lessons/new` | Upload box (PDF/JPG/PPTX), topic text field, language/level/mode selectors, generate button |
| Video player | `/lessons/{id}` | Video player, subtitles toggle, notes panel (right), flashcard button, quiz button, share |
| Flashcard study | `/flashcards` | Due cards count, flip card UI, SM-2 rating buttons (Again/Hard/Good/Easy), progress bar |
| Flashcard deck list | `/flashcards/decks` | All decks, add deck, search |
| Quiz screen | `/quiz/{lesson_id}` | MCQ with A/B/C/D, timer, progress, submit, results with explanations |
| AI Tutor chat | `/chat/{lesson_id}` | Chat bubble UI, cited sources, image attachment for doubt photo |
| Exam pack enrollment | `/exam-packs` | Pack grid (NEET/JEE/CBSE 10..), pack detail with syllabus, enroll button |
| Daily plan | `/plan` | Time-blocked study plan, tick off items, reschedule, add custom |
| Readiness score | `/readiness` | Radar chart (5 dimensions), weak topics, suggested actions |
| Mock test | `/mock/{pack_code}` | Full-screen test, timer, flag for review, submit, detailed analysis |
| Progress analytics | `/progress` | Subject-wise bar chart, streak calendar, study hours heatmap |
| Notifications | `/notifications` | List with read/unread, mark-all-read |
| Profile / Settings | `/profile` | Language, level, mode preferences, notification settings, password change |
| Subscription / Upgrade | `/subscribe` | Tier comparison table, Razorpay checkout, invoice history |

### 7.2 Teacher Screens

| Screen | Route | Key Elements |
|--------|-------|-------------|
| Teacher home | `/teacher` | My classes, pending assignments, student alert list |
| Class management | `/teacher/classes` | Class list, add class, student count, timetable link |
| Student roster | `/teacher/classes/{id}/students` | Student list, add/remove, individual progress |
| Assignment creator | `/teacher/assignments/new` | Topic, class selector, video type, due date, auto-generate quiz toggle |
| Doubt queue | `/teacher/doubts` | Pending doubts, claim, respond with text + audio |
| Attendance | `/teacher/attendance` | Date picker, class selector, mark present/absent, summary |
| Content publisher | `/teacher/publish` | Upload lesson, set price, choose marketplace |
| Performance reports | `/teacher/reports` | Per-student, per-class, per-topic weakness heatmap |

### 7.3 Parent Screens

| Screen | Route | Key Elements |
|--------|-------|-------------|
| Parent dashboard | `/parent` | Child card(s): streak, today status, weak subjects |
| Child progress | `/parent/child/{id}` | Same readiness/analytics as student but read-only |
| Fee payment | `/parent/fees` | Pending invoices, pay button, history |
| Communication | `/parent/messages` | Teacher messages, org notifications |
| Settings | `/parent/settings` | WhatsApp/SMS alert preferences |

### 7.4 School Admin Screens

| Screen | Route | Key Elements |
|--------|-------|-------------|
| Admin dashboard | `/admin` | Total students, active users, lesson completions, fee collection |
| Class management | `/admin/classes` | All classes, teacher assignments |
| Student management | `/admin/students` | Roster, bulk import CSV, SCIM sync status |
| Fee management | `/admin/fees` | Structures, generate invoices, collection status |
| Timetable | `/admin/timetable` | Week view per class |
| Branding | `/admin/branding` | Logo upload, colour scheme, custom domain |
| SSO/SAML config | `/admin/sso` | SAML metadata, test login |
| Reports | `/admin/reports` | Org-wide analytics, export |

### 7.5 Super Admin Screens

| Screen | Route | Key Elements |
|--------|-------|-------------|
| Platform overview | `/superadmin` | DAU/MAU, orgs count, revenue |
| LLM cost dashboard | `/superadmin/llm-costs` | Daily spend by org/feature, anomaly alerts |
| Feature flags | `/superadmin/flags` | Toggle features per org/tier |
| Moderation queue | `/superadmin/moderation` | Flagged forum posts, review/approve/remove |
| Org management | `/superadmin/orgs` | All orgs, impersonate, suspend |
| Audit log | `/superadmin/audit` | SOC 2 events, filter by user/action/resource |

---

## 8. Technical Architecture Fixes

### 8.1 Immediate Fixes (Already Done in This Session)

- ✅ JWT auth headers in all home_ui.py fetch() calls
- ✅ JWT stored in localStorage after login
- ✅ UUID validation guard in PostgresJobStore.get()
- ✅ 6 navigation route corrections in navigation.py

### 8.2 Critical Technical Debt

**Monolithic web.py (11,938 LOC):**
The file handles auth, lessons, org management, exams, fees, notifications, SAML, SCIM, leaderboards, branding, push, coaching, and more — all in one file. This makes:
- Feature-level testing impossible
- Team collaboration impossible (merge conflicts on every feature)
- Cold-start debugging very slow

Fix: Extract each domain into a router file under `padhai/routers/`. The `routers/` directory already has 8 files started — expand it. Each file becomes a `APIRouter(prefix="/api/domain", tags=["domain"])`.

**SQLite → Postgres migration for all modules:**
`web.py` uses `db.py` (Postgres-aware). But `spaced_repetition.py`, `coaching.py`, `forums.py`, `retrieval.py`, `doubt_clearing.py`, `mentorship.py`, `live_classes.py`, and 20+ other modules each have their own SQLite `SCHEMA` string and `sqlite3.connect()` calls. These modules will fail silently or use separate SQLite files in production.

Fix: Route all module schemas through the central `db.py` migration system. Run `scripts/migrate.py` to apply all table definitions to Postgres.

**Rate limiting on AI endpoints:**
`POST /lessons`, `POST /chat/{id}`, `POST /explain` all call Anthropic Claude with no per-user rate limit. A single user could make hundreds of concurrent requests.

Fix: `rate_limit.py` exists — wire it to these endpoints. Apply 5 requests/minute per user for AI endpoints.

**PADHAI_REQUIRE_AUTH defaulting to 0:**
`auth.py` `current_user` dependency returns `None` (anonymous) unless `PADHAI_REQUIRE_AUTH=1`. Production will have this unset and expose all student data.

Fix: Default to `1` in production environments. Add a startup warning log when `PADHAI_REQUIRE_AUTH=0`.

**No password reset / forgot password flow:**
The auth module has no reset token mechanism. Users who forget their password are permanently locked out.

Fix: Add `POST /auth/forgot-password` (sends email with time-limited reset token), `POST /auth/reset-password` (validates token, sets new hash).

### 8.3 Infrastructure Requirements

| Component | Dev | Production Requirement |
|-----------|-----|----------------------|
| Database | SQLite (~/.padhai/*.db) | PostgreSQL 14+ (Supabase/Neon/RDS) |
| File storage | Local ~/.padhai/ | Cloudflare R2 / AWS S3 |
| Job queue | In-process thread | Redis + Celery or Postgres-backed Procfile |
| Video rendering | Blocking in web worker | Separate `worker_entrypoint.py` process |
| TTS | gTTS (Google, rate-limited) | Bhashini or Piper (self-hosted) |
| CDN | None | Cloudflare (videos are large) |
| Monitoring | None configured | Sentry + Grafana (LLM obs data is ready) |

---

## 9. AI / RAG / Prompting Architecture

### 9.1 Current State

| Component | Implementation | Quality |
|-----------|---------------|---------|
| Primary model | `claude-opus-4-7` (pedagogy.py) | Good — best reasoning for lesson structure |
| Chat model | `_claude()` Anthropic client (web.py) | Uses default Claude client |
| Prompting | Per-mode in `mode_prompts.py` | 9 video modes, per-level calibration, good |
| RAG retrieval | BM25 token-overlap (`retrieval.py`) | Adequate for small documents; not production-scale |
| Embeddings | Optional via `PADHAI_VECTOR_PROVIDER` | Not configured; falls back to BM25 |
| Grounding | `tutor_grounding.py` — injects doc chunks | Correct approach |
| Caching | `llm_cache.py` — SHA-256 key on (prompt, model) | Good — reduces cost on repeated content |
| Observability | `llm_obs.py` — logs every call with tokens/cost | Data collected, no UI to view it |
| Socratic tutor | `socratic_tutor.py` | Exists, not wired to any UI endpoint |

### 9.2 What Needs to Improve

**RAG quality at scale:**
BM25 token overlap works for a 10-page document but fails for multi-document corpora or question bank searches. For NEET/JEE students with 500+ uploaded documents, retrieval precision will degrade.

Fix: Enable `PADHAI_VECTOR_PROVIDER=anthropic` for embeddings. Consider pgvector extension on the existing Postgres DB — zero additional infrastructure.

**Prompt caching strategy:**
`llm_cache.py` caches full prompt→response. For lesson generation this saves cost on repeated topics. However, chat sessions (`/chat/{lesson_id}`) do not benefit from this because each user message is unique.

Fix: Use Anthropic's prompt caching feature (cache_control=ephemeral) on the system prompt + document context block. This alone cuts chat costs by ~60% on long sessions.

**Model selection by use case:**
All calls currently go to `claude-opus-4-7`. Flashcard generation, quiz generation, and recap audio do not need Opus-level intelligence.

Recommended routing:
- Lesson script (vision + complex reasoning) → `claude-opus-4-7`
- Flashcard/quiz generation → `claude-haiku-4-5-20251001` (10x cheaper)
- Chat responses → `claude-sonnet-4-6`
- Summarization / recap → `claude-haiku-4-5-20251001`

**Hallucination control for curriculum content:**
Current prompts do not include explicit source grounding for factual claims beyond the uploaded page. For NCERT-aligned content this is risky.

Fix: Force the model to only derive content from the provided page image. Add `[SOURCE: {page_id}]` citation requirement to the output schema. Already partially done in `tutor_grounding.py` — apply the same pattern to `pedagogy.py`.

**Indian language quality:**
Hindi/Tamil/Telugu lessons are generated by instructing Claude to narrate in those languages. Claude's Indic output quality is good but may produce code-switching (Hinglish). Bhashini TTS then reads this.

Fix: Add a post-generation "Indic Polish" step using `indic_polish.py` (module exists — verify it's wired) before handing text to TTS.

---

## 10. Database and API Requirements

### 10.1 Missing Tables (Needed for Features to Work End-to-End)

| Missing Table | Purpose | Needed By |
|---------------|---------|-----------|
| `user_personalization` | Per-user language/level/mode preferences | Profile settings screen |
| `payment_orders` | Razorpay order ID → user mapping | Fee payment flow |
| `otp_tokens` | SMS/email OTP for password reset and phone login | Forgot password, phone auth |
| `user_sessions` | Active session log for multi-device logout | Security / DPDP |
| `announcement_reads` | Track which org announcements a user has read | Notification badge count |
| `quiz_attempts` | Full quiz session with per-question timing and answer | Analytics, mistake review |

### 10.2 API Gaps (Endpoints Needed But Not Present)

| Endpoint | Purpose | Priority |
|----------|---------|----------|
| `GET /me/profile` | Return user preferences (language, level, mode) | P0 |
| `PUT /me/profile` | Update preferences | P0 |
| `POST /auth/forgot-password` | Trigger reset email | P0 |
| `POST /auth/reset-password` | Consume reset token + set new password | P0 |
| `GET /flashcards/due` | Return today's due cards (SM-2 queue) | P1 |
| `POST /flashcards/{id}/review` | Submit card rating (Again/Hard/Good/Easy) | P1 |
| `GET /exam-packs` | List all available packs | P1 |
| `POST /exam-packs/{code}/enroll` | Enroll in pack | P1 |
| `GET /me/readiness` | Current readiness score + weak topics | P1 |
| `GET /doubts/queue` | Teacher: pending doubts | P1 |
| `POST /doubts/{id}/respond` | Teacher: submit response | P1 |
| `GET /api/navigation/manifest` | Navigation structure | ✅ exists |

### 10.3 Existing API Issues

- `GET /jobs` returns ALL jobs globally (no user filter) — security issue if `PADHAI_REQUIRE_AUTH=0`
- `POST /lessons` accepts `language` but ignores `PersonalizationProfile` when called directly — bypasses tier enforcement
- `GET /api/orgs/me` returns 404 for users not in any org — should return `{"org": null}` for students

---

## 11. Feature Priority Roadmap

### P0 — Make the Core Student Loop Work (Week 1)

These are the minimum actions needed for a student to have a complete study session:

1. **Profile/preferences screen** — student can set language, level, exam target
2. **Lesson generator screen** — upload PDF → choose language/level → generate → poll progress → auto-play video
3. **Video player screen** — play lesson, embedded quiz button, flashcard button, notes panel
4. **Flashcard study screen** — see due cards, flip, rate, see next card
5. **Quiz screen** — 5 MCQ questions after a lesson, see score + explanations
6. **Today's dashboard** — streak, due cards count, continue last lesson, exam pack progress

### P1 — Complete the Study Ecosystem (Week 2-3)

7. **AI Tutor chat** — chat interface wired to POST /chat/{lesson_id}
8. **Doubt clearing** — photo upload + question → AI response
9. **Exam pack enrollment** — pack selection and enrollment screen
10. **Notifications** — bell icon, notification list
11. **Parent portal** — child dashboard, fee payment
12. **Teacher home** — class management, assignment creator, doubt queue

### P2 — Platform Completeness (Week 4)

13. **Mock test** — full-screen timed test with adaptive questions
14. **Readiness score** — radar chart, weak topic drill-down
15. **Progress analytics** — study hours, streak calendar, subject heatmap
16. **Mobile layout fixes** — hamburger menu, responsive grid, touch-friendly cards
17. **Forgot password** — email reset flow
18. **UPI payment** — Razorpay checkout for subscription upgrade

### P3 — Competitive Advantage (Post Month 1)

19. **Offline pack download** — PWA + service worker, offline video play
20. **WhatsApp notifications** — parent alerts, daily reminder
21. **Leaderboard** — batch/class leaderboard for coaching institutes
22. **Marketplace** — teacher content, earning dashboard
23. **SMS OTP login** — phone-number based auth for rural users
24. **Hindi/Tamil UI** — i18n for core screens
25. **Vernacular NCERT content seeding** — Class 6-10 mapped question bank

---

## 12. 30-Day Build Plan

### Week 1 — Fix Foundations + Core Student Loop (Days 1-7)

**Goal:** One student can complete a full lesson cycle end-to-end from browser.

| Day | Task | Owner Module | Acceptance |
|-----|------|-------------|------------|
| 1 | `/me/profile` GET+PUT endpoints, `user_personalization` table | `web.py` / new `routers/profile.py` | curl returns user preferences |
| 1 | Profile/settings screen in home_ui.py | `home_ui.py` | Student can change language |
| 2 | Lesson generator UI (upload → options → generate) | `home_ui.py` | Upload a PDF, see job polling progress bar |
| 3 | Job polling UI → video player on completion | `home_ui.py` | Video auto-plays when job done |
| 3 | Video player screen with subtitles, notes panel | `home_ui.py` | Student can pause, toggle subs |
| 4 | `GET /flashcards/due` + `POST /flashcards/{id}/review` endpoints | new `routers/flashcards.py` | API returns due cards |
| 4 | Flashcard study screen (flip, SM-2 rating) | `home_ui.py` | Student can work through daily review queue |
| 5 | Quiz screen (MCQ, timer, results) wired to existing quiz endpoint | `home_ui.py` | 5 questions after lesson, score shown |
| 6 | Dashboard aggregation endpoint wire-up: show streak + due cards + plan | `home_ui.py` / `student_home.py` | Home page shows personalised data |
| 7 | Forgot password (email reset token) | `auth.py` + new email module | Student receives reset email |

### Week 2 — Teacher + AI Tutor (Days 8-14)

| Day | Task | Module | Acceptance |
|-----|------|--------|------------|
| 8 | AI Tutor chat UI wired to POST /chat/{lesson_id} | `home_ui.py` | Student can ask questions about a lesson |
| 9 | Doubt clearing: student submit form + AI auto-response | `doubt_clearing.py` + UI | Doubt answered within 60 seconds |
| 10 | Teacher home screen: class list, recent assignments | `home_ui.py` | Teacher sees their classes |
| 11 | Assignment creator: topic → auto-generate → assign to class | `home_ui.py` | Assignment appears in student plan |
| 12 | Doubt queue: teacher view, claim, respond | `home_ui.py` | Teacher can answer queued doubts |
| 13 | Exam pack enrollment screen | `home_ui.py` + `exam_taxonomy.py` | Student enrolled in NEET pack |
| 14 | Notifications bell + list | `home_ui.py` + `notifications.py` | Unread badge, click to list |

### Week 3 — Parent + Payments + Polish (Days 15-21)

| Day | Task | Module | Acceptance |
|-----|------|--------|------------|
| 15 | Parent portal: link, child dashboard | `home_ui.py` + `parents.py` | Parent sees child streak + subjects |
| 16 | Razorpay checkout UI for subscription upgrade | `home_ui.py` + `razorpay_client.py` | Payment completes, tier upgraded |
| 17 | Fee invoice + UPI payment for school fees | `home_ui.py` + fees API | Parent can pay school fee via UPI |
| 18 | Mobile layout: hamburger menu, responsive cards | `home_ui.py` CSS | Works on 375px mobile screen |
| 19 | Rate limiting on AI endpoints | `rate_limit.py` wired | 5 req/min per user enforced |
| 20 | Error pages: 404, 401, 500 — user-friendly | `home_ui.py` | No raw FastAPI error JSON shown |
| 21 | Load test: 100 concurrent lesson generate requests | Locust / k6 | No queue starvation, P95 < 30s |

### Week 4 — Analytics + Mock Tests + Launch Readiness (Days 22-30)

| Day | Task | Module | Acceptance |
|-----|------|--------|------------|
| 22 | Progress analytics screen (streak calendar, subject chart) | `home_ui.py` + stats API | Student sees 7-day progress |
| 23 | Readiness score screen (radar chart, weak topics) | `home_ui.py` + `readiness.py` | NEET student sees topic readiness |
| 24 | Mock test screen (full-screen, timed, adaptive) | `home_ui.py` + `mock_engine.py` | 30-question mock with analysis |
| 25 | PPTX/DOCX ingest (return 400 with clear message until implemented) | `ingest.py` | No 500 on unsupported file types |
| 26 | PADHAI_REQUIRE_AUTH defaulting fix + startup warnings | `auth.py`, `web.py` | No unauthenticated data leaks |
| 27 | Smoke test suite: expand to cover new screens | `.github/workflows/smoke.yml` | CI green on all critical flows |
| 28 | Security audit: XSS in home_ui.py, SQL injection in query params | All | No reflected XSS, parameterized queries |
| 29 | Performance: LLM prompt caching for chat, haiku for quiz gen | `pedagogy.py`, `web.py` | AI cost reduced 40%+ |
| 30 | Staging deploy + pilot with 10 real students | Infrastructure | 10 students complete lesson cycle |

---

## 13. Testing Checklist

### 13.1 Critical Path Smoke Tests

```
[ ] POST /auth/signup → 201, returns user_id
[ ] POST /auth/login → 200, returns JWT token
[ ] GET /auth/me (with JWT) → 200, returns user
[ ] GET /auth/me (no JWT) → 401
[ ] POST /lessons (with JWT, valid PDF) → 202, returns job_id
[ ] GET /jobs/{valid_uuid} → 200, returns status
[ ] GET /jobs/{non-uuid-string} → 404, NOT 500   ← FIXED
[ ] GET /api/navigation/manifest → 200, all routes valid
[ ] POST /lessons/{id}/flashcards → 201, returns cards
[ ] GET /flashcards/due (new) → 200, returns card list
[ ] POST /flashcards/{id}/review → 200, updates SM-2 state
[ ] POST /chat/{lesson_id} → 200, returns AI response
[ ] GET /api/home/me/dashboard → 200, returns hero + metrics
[ ] POST /auth/forgot-password (new) → 200
[ ] Parent link flow: POST /api/parents/link → GET /api/parents/children
[ ] Razorpay webhook POST /api/webhooks/razorpay → 200 with valid HMAC
[ ] SAML: GET /auth/saml/{org_id}/metadata → 200, valid XML
```

### 13.2 Security Checklist

```
[ ] All /api/* endpoints return 401 for unauthenticated requests
[ ] JWT with expired timestamp returns 401
[ ] User A cannot access User B's lessons/flashcards/jobs
[ ] POST /api/orgs/{org_id}/members requires org admin role
[ ] Razorpay webhook rejects requests with invalid HMAC signature
[ ] No SQL constructed via string concatenation (parameterized queries)
[ ] XSS: home_ui.py does not inject unsanitized user content into DOM
[ ] PADHAI_JWT_SECRET is not a default/placeholder value at startup
[ ] DPDP: under-14 signup blocked without parent consent token
[ ] Rate limit: 6th lesson request within 1 minute returns 429
```

### 13.3 Browser / Device Compatibility

```
[ ] Chrome 120+ (desktop)
[ ] Firefox 120+ (desktop)
[ ] Safari 17+ (macOS + iOS)
[ ] Chrome Android (Pixel/Samsung)
[ ] 375px mobile viewport (iPhone SE size)
[ ] Low bandwidth (throttle to 3G in DevTools) — home loads in < 5s
[ ] Offline: service worker serves cached home shell
```

---

## 14. Launch Readiness Checklist

### Infrastructure

```
[ ] DATABASE_URL set to production Postgres (not SQLite)
[ ] ANTHROPIC_API_KEY configured with billing limit
[ ] PADHAI_JWT_SECRET and ADMIN_JWT_SECRET are unique random values
[ ] PADHAI_REQUIRE_AUTH=1 on all web processes
[ ] S3 / R2 configured for video and upload storage
[ ] CDN (Cloudflare) in front of video endpoints
[ ] Redis or Postgres-backed job queue (not in-process threads)
[ ] Worker process (`worker_entrypoint.py`) running separately from web
[ ] Sentry DSN configured for error reporting
[ ] Uptime monitoring (UptimeRobot / BetterUptime) on /health
[ ] Database backups configured (daily + WAL streaming)
```

### Product

```
[ ] 10 real students have completed the full lesson cycle
[ ] At least 3 Indian languages verified (Hindi, Tamil, one more)
[ ] At least 2 exam packs have seeded questions (NEET + CBSE 10)
[ ] Payment flow tested end-to-end with ₹1 test charge + refund
[ ] Parent portal tested with at least 2 parent-student pairs
[ ] Forgot password flow verified with real email delivery
[ ] DPDP consent flow verified with under-14 signup
[ ] Terms of Service and Privacy Policy pages live
[ ] Razorpay KYC completed for live payments
[ ] App Store / Play Store submission (if shipping mobile)
```

### Legal / Compliance

```
[ ] DPDP Act 2023: under-14 consent flow + 72-hour breach notification plan
[ ] Terms of Service reviewed by lawyer for EdTech liability
[ ] Data residency: student data stored in India (ap-south-1 / Mumbai Cloudflare)
[ ] Content moderation: forum posts + doubt images reviewed for CSAM compliance
[ ] Razorpay: nodal account + payment aggregator license (for platform fees)
```

---

## 15. Final Recommendation

**The product is built. Ship the UI.**

AI Pathashala is not an early-stage EdTech startup with a limited backend. It is an enterprise-grade Indian EdTech platform with 592 API routes, SAML/SCIM/DPDP/SOC2 compliance, Razorpay integration, 10-language TTS, 5 avatar tiers, exam-pack-aligned coaching tracks, and a ready SM-2 spaced repetition engine.

The entire gap between this product and market leaders like StudyFetch or Quizlet is **UI/UX surface area**. Every feature a student needs exists in the backend. None of it is reachable from a browser without API knowledge.

**The 30-day plan above will produce a shippable product** — not by adding features, but by connecting the existing backend to a real frontend.

**Do not** rewrite in React or build a separate SPA. The embedded HTML approach in `home_ui.py` is functional. Extend it with HTMX for dynamic interactions (lesson polling, chat streaming, flashcard flips) — zero build pipeline, zero Node.js dependency, same Python deployment.

**Do not** add new AI features. The AI architecture is solid. Reduce cost first (Haiku for quiz/flashcard generation, prompt caching for chat) before adding new model calls.

**Do not** build new backend modules. Fix the 7 P0 issues listed in Section 3, wire the existing modules to the UI, and get 10 pilot students through the full cycle. That data will tell you what to build next.

**The single metric that matters:** Can a Class 10 student in rural Maharashtra upload a chapter from their Marathi textbook, watch a 3-minute Hindi video summary, answer 5 quiz questions, and review 10 flashcards — all in 20 minutes, on a ₹5,000 Android phone? When the answer is yes, you have product-market fit.

---

*Audit produced from direct codebase inspection of 118 Python modules, 592 API routes, and 155+ database tables. All recommendations are based on existing code, not hypothetical additions.*
