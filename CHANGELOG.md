# CHANGELOG

All notable releases of AI Pathshala (`padhai/`). Format inspired by
[Keep a Changelog](https://keepachangelog.com); semantic-ish versioning.

The full history lives in `git log main`. This file is the curated
release-by-release summary auditors / new contributors actually read.

---

## Unreleased

### polish-N sprint stack — codebase hardening since v3.20

A run of small focused sprints (numbered `polish` → `polish-17` as of
this writing) targeting three themes: extract web.py into composable
router slices, expand the lint gate, grow the accuracy bench. Each
sprint shipped 4 items in one tight commit. Full sprint-by-sprint
detail in the commit messages — `git log --grep "polish-"`.

**Lint gate** — promoted to blocking, in order: `F` (pyflakes) →
`E` (pycodestyle errors) → `I` (isort) → `B` (bugbear) → `UP`
(pyupgrade) → `SIM` (simplify) → `RUF` (Ruff-specific) → `ARG`
(unused-arguments) → `B904` (raise-without-from inside except).
Nine categories enforced. B904 alone was 344 sites cleaned by a
custom AST-based mass fixer (`scripts/fix_b904.py`).

**Router extraction** — 19 slices lifted out of web.py
(`padhai/routers/`): multipage, explainer, v2_video, parents,
orgs_api, orgs_classes, orgs_leaderboard, orgs_attendance,
orgs_assignments, orgs_fees, orgs_exams, branding, scim,
notifications, orgs_schedule, lesson_detail, lesson_chat_recap,
curriculum, uploads.

**Maintained tools** — `scripts/fix_b904.py` (AST B904 mass fixer),
`scripts/check_model_constants.py` (no literal `claude-*` outside
`padhai/models.py`), `scripts/check_router_registry.py` (router
file ↔ `_ROUTER_NAMES` bidirectional check), `scripts/
backup_sqlite.sh` (online `.backup` API), `make verify` (one-
command pre-PR gate).

**Central tooling** — `padhai/models.py` (Claude model-ID registry,
closes bug #8), `padhai/db.py:sqlite_path()` (shared SQLite path,
closes DPDP cross-DB crash), `padhai/llm_call.py:call_claude()`
(wraps client.messages.create + cost tracking + daily cap).

**Accuracy bench** — grew from 12 → 280 items across 9 board/exam
tracks (CBSE 6-12, ICSE, IGCSE, Maharashtra, Karnataka, TamilNadu,
AP/Telangana, UP + JEE / NEET / UPSC / SSC). ~16% reasoning items
(was 0% lookup-style). Structural mode runs in <5s and is gated on
every PR; live mode runs on push to main with
`--min-pass-rate=0.75`.

**Docs** — `SECURITY.md`, `CONTRIBUTING.md`, `ONBOARDING.md`
(contributor first-day map with 19-router index + maintained tools
+ invariants).

**Tests** — pytest grew 37 → 58 (added `tests/test_routers.py`
covering all 18+ extracted slices).

---

## v3.20.0 — Phase 3 complete: Voice Tutor + 4 AI module UIs wired

All Phase 3 backend modules are now fully connected to the SPA.
No stubs remain in the student-facing UI.

**Voice Tutor (`mod-voice`)** — replaced the "Coming in Phase 3" stub
with a real Web Speech API voice loop backed by `POST /voice/respond`.
Optionally links to a lesson (same grounding as the text Doubt Chat
but spoken). Backend: `voice_tutor_reply()` + `VOICE_TUTOR_SYSTEM`
prompt added to `pedagogy.py`.

**Essay Grader (`mod-essay`)** — rubric picker that loads from
`GET /api/essay/rubrics` on module open; textarea for the answer;
per-criterion score breakdown + suggestions via `POST /api/essay/submissions`.
Wires to the fully-implemented `essay_grader.py` backend (L2).

**Math Check (`mod-mathvision`)** — image URL input → AI extracts LaTeX
steps → step-by-step validation with first-error callout. Wires to
`math_vision.py` via `POST /api/math-vision/submit` + `/validate` (L3).

**Mock Interview (`mod-interview`)** — track selector (UPSC / JEE /
placement / NEET PG / MBA) → voice or text answers → live transcript
with per-answer feedback → final scored report. Correctly wired to
`mock_interview.py` API field names (`turn_index`, `answer_text`,
`interview_id`, `opener.question_text`) (L4).

**Adaptive Practice (`mod-adaptive`)** — pack code input → create
adaptive pack → topic mastery percentages view. Wires to
`adaptive_packs.py` (L5).

Sidebar Tutor group expanded from 2 → 6 items. `showModule()` now
dispatches a `moduleShow` custom event so modules can lazy-load data
(essay rubrics, adaptive pack list) on first visit.

Backend additions:
- `POST /voice/respond` endpoint
- `voice_tutor_reply()` + `VOICE_TUTOR_SYSTEM` in `pedagogy.py`
- `node_modules/` added to `.gitignore`



## v3.19.0 — Home UI UX fixes

v3.18 painted the §26 mockup but the user caught three real
bugs immediately:

  1. Clicking a chip navigated to the raw API URL → dumped JSON
  2. Sidebar listed 24+ items (3 features × 8 sections) → clutter
  3. Landing "Sign in" pointed at /auth/login (POST-only) → 404

All three fixed:

- **Sidebar**: 8 section titles only (Exam Hub / Study Studio /
  Mocks / AI Tutor / Community / School / Marketplace / Admin
  & Trust). Clicking a section scrolls + highlights the
  matching chip group below. Per-feature buttons removed.

- **Chips → inline drawer**: clicking a chip no longer navigates.
  Opens a drawer with the feature title + description + endpoint
  + "Try it" button. The button calls the API, pretty-prints
  the response, shows it inline (truncated to 4KB). Endpoints
  with unfilled `{param}` placeholders get a friendly message
  instead of a 422.

- **Landing → working auth form**: `LANDING_HTML` now carries
  an inline Sign in / Create account tab UI that POSTs to
  `/auth/login` + `/auth/signup` (existing endpoints), then
  redirects to `/home` on success. No more 404 on the Sign-in
  CTA.

- **Hero actions**: scroll to the most-relevant section instead
  of navigating to JSON. Drawer fallback for everything else.

Routes: 592 → 592 (pure UI fix). New smoke
`scripts/test_v3_19.py` (~25 checks). Full regression v1.0 →
v3.19 (43 smokes) green.

---

## v3.18.0 — Goal-led home UI (the actual mockup, painted)

v3.17 shipped the backend APIs (`/api/navigation/manifest` +
`/api/home/me/dashboard`) but `/` still rendered the legacy
SPA. v3.18 paints the goal-led design from the §26 HTML mockup,
fetching live data from those APIs.

- **`padhai/home_ui.py`** — self-contained HTML + inline JS,
  no framework, no build step.
  - `HOME_HTML`: the three-column "Exam Hub" mockup —
    sidebar (driven by manifest), hero with auto-headline +
    actions, exam-pack card with readiness bar, 3 metrics
    (due flashcards / weak topics / citation rate), today's
    study flow (driven by daily-plan), what's next (mock /
    studio / tutor), existing-modules chip grid (with
    `keep` / `new` / `admin` badges per §26), community + trust
    right rail, mobile bottom-nav (5 tabs per mockup).
  - `LANDING_HTML`: minimal public landing for unauthed
    visitors — sign-in CTA + link to the home + manifest API.

- **`/` (browser)** now serves `HOME_HTML` instead of the legacy
  `_INDEX_HTML`. JSON clients still get JSON (Accept-header gate
  preserved).
- **`/home`** alias route, **`/landing`** for public landing,
  **`/ui`** now serves the new home too, **`/ui-legacy`** keeps
  the pre-v3.18 dashboard for bookmark compatibility.

PWA install affordances preserved: manifest link, theme-color
meta, apple-mobile-web-app tags, service worker registration,
`applyBranding` IIFE that recolours CSS vars from
`/api/orgs/{id}/branding` (matches the legacy shape so the
v1-era smoke checks still pass).

Routes: 589 → 592 (+3). New smoke `scripts/test_v3_18.py` (~17
checks) covering both HTML routes, JSON fallback when no
Accept header, navigation manifest accessibility for unauthed
users, dashboard 401 handling. Full regression v1.0 → v3.18
(42 smokes) green.

---

## v3.17.0 — Navigation manifest + student home aggregator

Per gap-review §26 (new section) + the HTML mockup — the
redesign should not remove existing features. It should
**reorganise** them under a goal-led navigation that puts the
student's goal first and surfaces existing modules underneath.

This release ships the backend layer that makes the mockup
runnable: a navigation manifest (so the frontend doesn't
hard-code section structure) + a student home aggregator (so
the home screen loads in one round-trip instead of fanning
out to a dozen endpoints).

- **`padhai/navigation.py`** — Static manifest of the 8 §26
  sections (Exam Hub / Study Studio / Mock Tests / AI Tutor /
  Community / School / Marketplace / Admin & Trust). Each
  section lists which existing endpoints feed it, with a
  `keep` / `new` badge mirroring §26's "keep existing
  functionality" instruction. Role filter
  (`student` / `teacher` / `parent` / `admin`) drops features
  the caller can't see. 5-tab mobile bottom-nav per the
  HTML mockup. No DB tables — pure manifest.

- **`padhai/student_home.py`** — Goal-led composite for the
  home screen. Resolves the user's active Exam Pack, pulls
  readiness + today's plan + next mock + community hint +
  trust signal + recent fallbacks + module catalog, and
  returns the mockup's "Exam Hub" view shape. Defensive
  composition (every sub-module call wrapped in `_safe`) so a
  brand-new user with zero signals still gets a clean
  welcome state. Headline auto-generated from weak + strong
  topics ("Your UPSC path is behind in Polity, strong in
  Modern History.").

4 endpoints. Routes: 585 → 589 (+4). New smoke
`scripts/test_v3_17.py` (~30 checks) covering the 8 §26
sections, role-based filtering (admin section dropped from
student view, teacher dashboard appears for teacher role),
defensive fallback for empty user, populated headline
generation, modules grid mirroring the navigation. Full
regression v1.0 → v3.17 (41 smokes) green.

---

## v3.16.0 — Step-by-step math solver

Per review §7 (Photomath / Gauth competitor gap) — students
expect camera-first problem solving with step-by-step
explanations. `math_vision.py` ships OCR → LaTeX + whole-
expression validation; this release adds the **step layer** —
the problem broken into an ordered sequence with per-step
explanations, flagging, and tutor follow-ups.

- **`padhai/step_math.py`** — 3 tables (`step_problems`,
  `problem_steps`, `step_explanations`).

  **Two solvers**: deterministic SymPy for linear equations
  (3 steps: original → isolate variable → divide; all marked
  `validated=True`) and LLM-driven for anything else (caller
  posts step list + final answer; steps marked
  `validated=False` so UI shows "AI-generated, verify yourself").

  **Step-level interaction**: students hit `flag_step()` on a
  step they didn't follow → bumps `flagged_count`. Tutor wrapper
  generates an explanation + persists via `add_step_explanation()`
  with citations from v3.3 retrieval. Multiple explanations per
  step accumulate (different tutors / different angles).

  **Admin queue**: `high_flagged_steps(threshold=3)` surfaces
  steps that confuse multiple students — editorial team rewrites.

  **Problem-kind auto-detection** (heuristic): linear_equation /
  quadratic_equation / derivative / integral / simplify / unknown.
  Derivative + integral checks fire first so power notation in
  the integrand doesn't false-positive as quadratic.

10 endpoints under `/api/step-math/*`. Routes: 575 → 585 (+10).
New smoke `scripts/test_v3_16.py` (~50 checks) covering kind
detection (incl. ordering bug fix for ∫ x² dx), SymPy 3-step
linear solve, LLM step persistence with validated=False, flag
+ explanation flow, high_flagged_steps queue, mark_failed +
retry path. Full regression v1.0 → v3.16 (40 smokes) green.

---

## v3.15.0 — Adaptive / personalised Exam Packs

Per review §11 + §22 — v3.1 Exam Packs ship a static catalog,
but real students differ wildly. A student strong in algebra +
weak in geometry needs a different daily plan than the inverse.
This release adds a per-user weightage overlay.

- **`padhai/adaptive_packs.py`** — 3 tables
  (`personalised_packs`, `personalised_weightages`,
  `pack_adaptation_signals`).

  **Adaptation rules** (additive deltas on base weightage):
  • `weak_topic_boost` +50%  → mastery < 0.40 with ≥2 attempts
  • `recent_mock_low` +40%   → last mock < 30% on topic
  • `skipped_topic_boost` +30% → topic skipped in 2+ daily plans
  • `strong_topic_relief` -30% → mastery > 0.85 with ≥5 attempts

  **Bounds**: final adjusted_weightage clamped to
  `[base × 0.3, base × 3.0]` — prevents extreme outliers that
  wreck the daily plan.

  **`re_adapt(user, base_pack)`** gathers signals from `mastery`
  + `mock_engine` + `daily_plan` modules, applies rules per topic,
  persists overrides + audit-trail signal rows.

  **`personalised_topic_view`** is the read API — returns base +
  adjusted + reasons per topic; falls back to base when no
  overrides exist.

  **`should_re_adapt`** probe: stale (>7 days) or no-pack-yet
  triggers refresh. Dashboard uses it to decide whether to show
  a refresh nudge.

9 endpoints under `/api/adaptive-packs/*`. Routes: 566 → 575 (+9).
New smoke `scripts/test_v3_15.py` (~30 checks) covering rule
firing for weak / strong topics, bounds clamping at ×3 ceiling,
fallback to base weightage when no overrides, stale detection,
signal audit trail. Full regression v1.0 → v3.15 (39 smokes)
green.

---

## v3.14.0 — Audio recap (NotebookLM-style)

Per review §7 (StudyFetch + NotebookLM gap) — "audio recap" is
a polished, on-the-go listening format: a short narrative
summary of a chapter/topic the student can play during a
commute. Existing platform has TTS + retrieval + citations;
this release composes them.

- **`padhai/audio_recap.py`** — 2 tables (`audio_recaps`,
  `audio_recap_segments`).

  **Structured script**: intro + 2-5 body segments + outro.
  Each rendered as a separate audio file so the UI can scrub
  + show a synced transcript timeline.

  **Three source kinds**: `upload` (retrieval-driven over a
  document), `topic` (retrieval over exam_taxonomy chunks), or
  `free_text` (caller supplies the script directly).

  **Three answer modes**: `cited` (default — segments carry
  citation dicts), `source_only` (refuse to render if no
  source chunks — strict mode), `general` (LLM fallback).

  **Worker shell**: `render_pending()` consumes pending recaps,
  loops segments, calls TTS provider. Default `sandbox` provider
  records path + estimated duration without calling TTS — used
  in dev/tests. Production swaps to `tts.get_provider()`
  (gtts / piper / bhashini / elevenlabs / espeak).

  **`generate_script_from_query()`** closes the retrieval → recap
  loop: top-k chunks from v3.3 retrieval become body segments
  with citations preserved.

9 endpoints under `/api/audio-recaps/*`. Routes: 557 → 566 (+9).
New smoke `scripts/test_v3_14.py` (~40 checks) covering script
structure (intro/body/outro), source_only refusal of ungrounded
segments, generate_script over retrieval hits, render worker
sandbox lifecycle, cancel/delete + ownership gates. Full
regression v1.0 → v3.14 (38 smokes) green.

---

## v3.13.0 — WhatsApp / SMS messaging rails

Per review §17 part 2 — Indian students rely on WhatsApp + SMS
heavily. `push.py` ships FCM web push; this release adds the
WhatsApp + SMS rails (template-based, opt-in compliant,
throttled).

- **`padhai/messaging.py`** — 3 tables (`user_phone_channels`,
  `message_templates`, `scheduled_messages`).

  **Channels**: per-user (phone, channel) opt-in with E.164
  validation, DPDP §6 consent_text capture, §13 opt-out flow,
  and `bounced` state for provider webhooks.

  **Templates**: pre-approved bodies with `{{var}}` placeholders.
  WhatsApp requires Meta-approved templates; we mirror that with
  `pending → approved` workflow. Auto-extract variables from
  body. `daily_max_per_user` cap drives the throttle.

  **Scheduling**: caller picks template + supplies vars; we
  render + persist. Channel resolution prefers WhatsApp,
  falls back to SMS. Opt-in + template-approval + variable-
  completeness all validated at schedule time.

  **Worker** (`send_due()`): pulls due-and-scheduled messages,
  re-checks opt-in (in case user opted out between schedule
  and send), enforces daily throttle, calls provider stub,
  records `provider_msg_id` + `sent_at`. Sandbox provider for
  dev/tests; env-gated swap to Meta / Twilio / MSG91 in prod.

  **Retry**: up to 3 attempts on send failure before flipping
  to `failed`.

12 endpoints under `/api/messaging/*` + `/api/admin/messaging/*`.
Routes: 545 → 557 (+12). New smoke `scripts/test_v3_13.py`
(~50 checks) covering E.164 validation, idempotent re-opt-in,
template variable extraction, schedule-time guards, send-time
throttle + opt-out gates, mark_bounced webhook flow. Full
regression v1.0 → v3.13 (37 smokes) green.

---

## v3.12.0 — Offline packs + low-data mode

Per review §17 — Indian students need low-data, mobile-first,
offline-friendly study. Existing platform assumed always-on
broadband. This release ships the download manifest + size
budget + tier-based filtering that the PWA / Android wrapper
needs.

- **`padhai/offline_packs.py`** — 3 tables
  (`offline_pack_manifests`, `offline_downloads`,
  `low_data_prefs`).

  **Quality tiers**: `text_only` (priority 1-2 only) /
  `standard` (1-3) / `full` (all). Each file in the manifest has
  a priority (1=critical text, 5=optional HD video) and a
  byte-size estimate.

  **Manifest generation**: `generate_manifest()` takes a free-
  form file list, filters by user's tier (or explicit override),
  caches under `(user, pack, version, tier)` — idempotent
  re-fetch returns the existing row.

  **Download lifecycle**: `start_download` → `update_progress` →
  auto-`completed` when `files_completed >= file_count`.
  Resumable (same manifest+user returns the in-progress row).
  `cancel_download` for user-initiated stop.

  **Daily data usage**: `user_data_usage_today()` aggregates
  bytes downloaded since midnight; surfaces `quota_exceeded`
  flag when over `max_daily_mb`. PWA reads this on cellular to
  decide whether to defer downloads.

  **Low-data prefs**: per-user `quality_tier` +
  `auto_downgrade_on_cellular` + `max_daily_mb`. Defaults to
  `standard` + auto-downgrade on.

11 endpoints under `/api/offline/*`. Routes: 534 → 545 (+11).
New smoke `scripts/test_v3_12.py` (~50 checks) covering tier
filtering, manifest idempotency on (user, pack, version, tier),
expired manifest excluded from active list, download resume
on re-start, progress auto-completion, quota_exceeded firing,
permission gates on cross-user access. Full regression v1.0 →
v3.12 (36 smokes) green.

---

## v3.11.0 — Marketplace quality controls

Per review §16 — marketplaces exist (O1/O2/O3/M4) but **quality
control** decides whether they become trustworthy or spam-farms.
Ratings + refunds + copyright checks + quality scoring + auto-
moderation across all 4.

- **`padhai/marketplace_quality.py`** — 5 tables
  (`market_ratings`, `market_refund_requests`,
  `market_copyright_claims`, `market_quality_scores`,
  `market_item_status`). Unified layer across all 4 marketplaces
  via `(item_kind, item_id)` keys (item_kind ∈ course /
  content_pack / question_pack / tutor).

  **Ratings**: 1-5 stars + review text + helpful_count. UNIQUE
  (item, user) makes re-rating idempotent. Auto-recomputes
  quality score on every write.

  **Refunds**: request → admin approve/reject. 7-day SLA;
  `expire_stale_refunds()` sweep auto-approves stale pending
  refunds (protects buyer). Approved + auto-expired refunds
  count against quality.

  **Copyright claims**: file with claim_type ∈ plagiarism /
  unauthorized_use / verbatim_copy / paraphrase; severity ∈
  minor / moderate / severe. Severe claims auto-flip item to
  `under_review` on filing; upheld severe claims auto-`removed`.

  **Quality score**: 0-100 composite = rating × 20 - refund_penalty
  (up to -30) - copyright_penalty (10/25/100 by severity) +
  recency_boost (+5 if rated in last 30 days). Items with
  rating_count ≥ 3 and score < 40 auto-flip to `under_review`.

  **Item status**: active / under_review / removed / rejected.
  Auto-flips driven by quality threshold + copyright claim
  events; admin can manually override via `set_item_status`.

15 endpoints (7 public + 8 admin/auth). Routes: 520 → 534
(+14). New smoke `scripts/test_v3_11.py` (~50 checks) covering
rating idempotency, refund SLA sweep, severe-claim auto-flip,
quality threshold auto-flip, admin override restore. Full
regression v1.0 → v3.11 (35 smokes) green.

---

## v3.10.0 — Research / PhD tools

Per review §9 — college students + PhD researchers need
different tooling than school students. Existing platform is
exam-prep first; research workflows need paper-reading +
literature-review + citation-manager + thesis outline support.

- **`padhai/research_tools.py`** — 6 tables (`research_papers`,
  `paper_summaries`, `literature_collections`,
  `collection_papers`, `research_citations`, `research_gaps`).

  **Paper ingest**: per-user library with DOI + arXiv id
  regex-validated; year bounds; UNIQUE (user, DOI) prevents
  duplicates per user. Links to `document_pages` via upload_id.

  **Cached summaries**: 1-paragraph short_summary + structured
  key_findings list + methods + limitations + future_work.
  REPLACE-on-conflict by paper_id. Caller does the LLM call;
  module is the cache + audit layer.

  **Literature collections**: Zotero-style folders. Per-position
  ordering + per-paper notes; auto-incremented positions on add;
  add idempotent on (collection, paper); paper_count rollup.

  **Literature map**: graph view — nodes (papers) + edges
  weighted by shared keywords + authors (×2 weight). Caller
  renders it (D3 / cytoscape).

  **Gap detection**: 2 strategies — (1) auto-flag keywords
  with <30% coverage across the collection, (2) caller-proposed
  themes scored against title/abstract/keywords. Themes with
  ≥50% coverage filtered out (already covered). Persists
  detected gaps + suggested_keywords for next-search hints.

  **Citation manager**: flag any sentence/chunk from any paper
  with optional page + section + note + tags. Filter by paper /
  tag. Foundation for a future BibTeX/APA export module.

15 endpoints under `/api/research/*`. Routes: 505 → 520 (+15).
New smoke `scripts/test_v3_10.py` (~60 checks) covering DOI /
arXiv validation, summary REPLACE semantics, collection
ownership, literature-map edge weighting (shared kws + authors),
gap detection (proposed + auto), citation filtering by paper +
tag, full HTTP auth gating. Full regression v1.0 → v3.10 (34
smokes) green.

---

## v3.9.0 — Socratic tutor mode

Per review §7 (Khanmigo gap) + §14 — the generic tutor answers
questions. Khanmigo's wedge is **Socratic tutoring**: ask
diagnostic questions, give hints before the answer, detect
confusion, guide rather than tell.

- **`padhai/socratic_tutor.py`** — 1 table
  (`socratic_exchanges`).

  **State machine**: `diagnose → hint → check → reveal`. The
  4th answer mode beyond v3.2's `general / source_only / official`.

  **Confusion detection**: 4 signals combined — explicit "idk" /
  "I don't know" markers; empty/whitespace replies; reply-shorter-
  than-10-chars + time-on-page > 60s; a regex-driven set of
  patterns. Confusion bumps the next-step decision back to `hint`
  with a simpler depth instead of advancing.

  **Reveal demand**: student explicitly typing "tell me the
  answer" / "I give up" jumps state to `reveal` regardless of
  depth.

  **Confusion-tolerance breach** (>2 confusions in a single
  exchange) auto-forces reveal — we stop torturing a stuck
  student.

  **`reveal()`** records the final answer with citations via
  v3.1's `citations.record_answer` so provenance + grounding rate
  metrics still capture Socratic exchanges.

  **`user_stats`**: completed-rate + avg-confusion-per-exchange.
  High avg confusion = topic too hard; high abandoned rate =
  engagement issue.

8 endpoints under `/api/socratic/*`. Routes: 497 → 505 (+8).
New smoke `scripts/test_v3_9.py` (~30 checks) covering all 4
confusion patterns, reveal-demand patterns, full state machine
(diagnose→hint→check→reveal with confusion back-off + tolerance
breach), provenance recording in reveal, ownership enforcement.
Full regression v1.0 → v3.9 (33 smokes) green.

---

## v3.8.0 — Spaced repetition + active recall

Per review §7 (Quizlet / Knowt gap) — students need polished
active recall with spaced repetition. Existing `mastery.py`
tracks topic-level signals; this adds card-level review schedules.

- **`padhai/spaced_repetition.py`** — 4 tables
  (`flashcard_decks`, `flashcards`, `flashcards_user_state`,
  `flashcard_reviews`).

  **SM-2 algorithm** with floor/ceiling guards. Ease bounded
  [1.3, 3.0]; interval bounded [1 day, 2 years]. Grade 0-5:
  <3 resets repetitions + interval to 1 day + bumps lapses;
  ≥3 grows interval (1 → 6 → interval × ease).

  **Card generation** from upstream surfaces:
  • `generate_from_chunks(chunks)` — closes the v3.3 retrieval →
    SRS loop: each retrieval hit becomes a card (front = page-
    section context, back = chunk text, citation preserved)
  • `generate_from_questions(qb_ids)` — pulls J6 question_bank
    rows into cards (front = question, back = correct_answer)
  • Manual `add_card()` for student-authored cards

  **Visibility**: private / shared / public. Public decks are
  community-browsable and feed the marketplace.

  **Due queue**: pulls cards with `due_at <= now` plus optionally
  N "new" cards (no state row yet) from the user's own decks.

  **Retention metric**: % of last-30-day reviews graded ≥3 —
  feeds the readiness consistency component in future passes.

11 endpoints under `/api/srs/*`. Routes: 486 → 497 (+11). New
smoke `scripts/test_v3_8.py` (~50 checks) covering SM-2 math
(success grows interval, fail resets, easy bumps ease higher
than good), due-queue with include_new slack, retrieval →
deck loop, permission checks on add/delete. Full regression
v1.0 → v3.8 (32 smokes) green.

---

## v3.7.0 — Expert review workflow

Per review §12 + §22.10 — the highest-trust gap. AI answers
alone don't build student/parent trust for Indian board + comp
exams. This release ships the verified-expert layer.

- **`padhai/expert_review.py`** — 3 tables (`experts`,
  `expert_reviews`, `expert_verifications`).

  **Expert profiles**: apply → admin approves → active.
  Subjects + exam_codes + languages for routing. Per-expert
  `rate_per_review_paise` (₹10-₹500). Rating roll-up (1-5 stars
  → rating_avg).

  **Review queue**: 4 target kinds (`ai_answer` / `qb_question`
  / `pack` / `lesson`). Idempotent on (target_kind, target_id)
  while pending or in_review. Priority 1-10 + subject_hint for
  routing. 72h SLA; `expire_stale_reviews()` sweep moves aged
  pending → expired.

  **Claim → decide flow**: experts atomically claim items
  (subject-routed); decide approve / correct / reject.
  `approve` + `correct` create rows in `expert_verifications`
  (denormalised for cheap `is_verified()` lookups). `correct`
  requires a `corrected_answer` body. Commission booked per
  decision: full rate for approve/correct, half rate for
  reject. Total earnings + counts rolled up to expert profile.

  **Verification lookup**: `is_verified(target_kind, target_id)`
  is a single PK hit — drives the "verified by teacher" badge
  on every content render.

15 endpoints (5 public/auth + 10 admin/expert). Routes: 471 →
486 (+15). New smoke `scripts/test_v3_7.py` (~50 checks)
covering apply/approve/status, subject-routing on claims,
race-loss on duplicate claims, commission math (full vs half),
verification row creation, SLA sweep, queue stats. Full
regression matrix v1.0 → v3.7 (31 smokes) green.

---

## v3.6.0 — Parent + Teacher dashboards

Per review §8 — the data is there (mastery + streaks + mock
attempts + Exam Pack enrollments + daily plans + citations
grounding rate + moderation) but no audience-specific
aggregated view existed. This release ships both, with strict
access control.

- **`padhai/dashboards.py`** — Pure read composer (no new
  tables, hence the no-op `migrate()`).

  **Parent dashboard**: caller's verified children only (uses
  `parents.is_verified_parent_of` + revoked-link filter). Per
  child: exam packs + readiness score + mastery weak/strong top-5
  + recent 5 mock submissions + 14-day plan stats + streak +
  grounding rate + recent fallbacks.

  **Teacher dashboard**: org-scoped (uses `orgs.require_role`
  with `{teacher, admin}`). Class-scoped via optional `class_id`.
  Returns per-student summary cards (compact academic signals,
  no PII beyond name) + class-level rollups (avg readiness,
  avg mock percentile, study consistency, top-5 weak topics
  weighted across students) + moderation flags raised by class
  members.

  **Teacher → student deep-dive**: verifies the target student is
  actually in the caller's org; raises 403 otherwise. Returns the
  same per-student block parent gets for their child.

  Defensive composition: every sub-module call wrapped in `_safe`
  so missing data / non-fatal errors degrade silently to a
  default. Useful for the early-adopter case where most signal
  components are empty.

4 endpoints (2 parent + 2 teacher). Routes: 467 → 471 (+4).
New smoke `scripts/test_v3_6.py` (~25 checks) covering verified-
child gating, revoked-link drop, class-vs-org scoping, weak-topic
class rollup, single-student deep-dive PermissionError on out-of-
org students, moderation-flag aggregation. Full regression matrix
v1.0 → v3.6 (30 smokes) green.

---

## v3.5.0 — Community moderation primitives + reactions

Per review §6 — community modules ship (`forums.py`,
`study_buddies.py`, `mentorship.py`, `doubt_clearing.py`) but the
moderation + reaction layer was missing. Without these, the
community loop falls over the moment it has real users —
especially with the K-PhD audience mix of minors + adults.

- **`padhai/moderation_queue.py`** — 3 tables
  (`mod_flagged_content`, `mod_actions`, `mod_reactions`).

  **Auto-flag scanner**: 4 deterministic rules combined into a
  0..1 score: blocklist hits (envrolled via
  `PADHAI_MODERATION_BLOCKLIST`), URL spam (3+ URLs in short
  body), all-caps shouting (≥75% upper across ≥40 chars),
  character / word repetition. Score ≥ 0.40 lands in queue as
  `flag`; ≥ 0.90 lands as `auto_remove`. `scan()` is idempotent on
  `(content_kind, content_id)` so retries don't duplicate.

  **Reviewer queue**: open → approved / removed / escalated.
  `auto_remove` items still get reviewer attention via `restore`
  for false positives. Every decision recorded in `mod_actions`
  for audit. 24h SLA on flagged items; `sla_breached_only`
  filter surfaces stale items.

  **Reactions**: `like / helpful / thank / report`. Unique
  `(target, user, kind)` enforces idempotency naturally — no
  separate rate-limit needed. `report` reactions create
  moderation queue entries automatically (deduped). Public
  endpoint surfaces aggregate counts; authed endpoint shows
  caller's own reactions.

12 endpoints (5 admin `/api/admin/moderation/*` + 4 public/auth
`/api/reactions/*`). Routes: 458 → 467 (+9). New smoke
`scripts/test_v3_5.py` (~50 checks) covering all 4 scanner rules,
auto_remove threshold, idempotency, queue decisions, action
audit, SLA breach filter, reaction throttling, report-to-queue
feedback. Full regression matrix v1.0 → v3.5 (29 smokes) green.

---

## v3.4.0 — Daily plan generator

Per review §3 + §6 — students pick the app because it tells them
**what to study today**. This release ships the scheduler that
turns Exam-Pack enrollment + readiness + topic tree into a
per-day, time-budgeted plan.

- **`padhai/daily_plan.py`** — 2 tables (`daily_plans`,
  `daily_plan_blocks`). 5 block kinds (`read` / `practice` /
  `mock` / `revise` / `current_affairs`). Allocation rules:
  practice 40% / read 25% / mock 15% / revise 10% / current
  affairs 10% (govt segments only — non-govt rolls CA into
  practice). Weak topics surface from `mastery.list_for_user`
  scored by `(1 - mastery) × weightage_pct` so high-weight low-
  mastery topics get priority. Next mock auto-selected from
  unpublished + sectional-first. `should_regenerate` detects
  stale plans (>18h), no-plan-today, or readiness drift >5
  points.

  `get_or_generate(user_id, pack_code)` is the dashboard entry
  point — returns today's plan if it exists, otherwise generates.
  `mark_block_done` updates parent plan's `completion_pct` +
  auto-flips status to `completed` when all blocks ticked.
  `pack_completion_stats` rolling 14-day window feeds the
  readiness `consistency` component.

7 endpoints under `/api/daily-plan/*`. Routes: 451 → 458 (+7).
New smoke `scripts/test_v3_4.py` (~40 checks) covering block
allocation, govt vs non-govt CA differentiation, regen replace
semantics, ownership guards on mark_block_done + skip_plan,
should_regenerate triggers (stale/fresh/no-plan). Full
regression matrix v1.0 → v3.4 (28 smokes) green.

---

## v3.3.0 — Retrieval (RAG) over document_pages

Closes review §4.1's biggest open gap: retrieval. v3.1 stored
citations, v3.2 wrapped the tutor with grounding modes — but
nothing actually **produced** the citations. This release does.

- **`padhai/retrieval.py`** — Token-overlap RAG over the existing
  `document_pages` table. Three tables (`doc_chunks`,
  `chunk_embeddings`, `chunk_token_index`). Word-window chunker
  with overlap (200 words target, 30 overlap, merge-tail to
  avoid tiny tail chunks) + heuristic section detection (catches
  `4.2 Linear Equations`-style headings). Indexing is idempotent
  on `(upload, page, chunk_index)` — safe to re-run after
  re-extracts.

  `retrieve(query, upload_ids, top_k, min_score)` uses
  **TF-IDF cosine** over the inverted index — zero-dep, runs on
  SQLite. Scores normalised to `[0, 1]` so they plug straight into
  `citations.record_answer`'s `relevance` field. `min_score`
  gate lets callers drop low-relevance hits before strict-mode
  grounding decisions. `hits_to_citations()` converts hits to the
  exact dict shape `citations.record_answer` /
  `tutor_grounding.send_grounded_message` expect.

  Provider hook (`PADHAI_RETRIEVAL_PROVIDER`) is in place for
  swapping in Anthropic / OSS embeddings later — falls back to
  tokenscore silently when no provider key is configured. We
  deliberately don't ship sentence-transformers + faiss (500MB
  torch + libgomp); the token scorer is good enough for
  one-document chats up to ~10k chunks per user.

5 endpoints under `/api/retrieval/*`. Routes: 446 → 451 (+5).
New smoke `scripts/test_v3_3.py` (~30 checks) covering
tokenisation, chunking + tail-merge, section heuristic,
indexing idempotency, scoped retrieval, min_score gating,
end-to-end retrieve → cite → record_answer wiring. Full
regression matrix v1.0 → v3.3 (27 smokes) green.

---

## v3.2.0 — Depth: mock engine + readiness + tutor grounding

**Phase 1.5 of the gap-review roadmap.** Composition release —
ties the v3.1 foundations (citations, exam taxonomy, accuracy
bench) into the existing student workflow (tutor, mastery,
streaks). Three modules; no fresh surface beyond what wires the
pieces together.

- **`padhai/mock_engine.py`** — Universal mock test engine (review
  §15). 4 tables (`mock_papers`, `mock_paper_questions`,
  `mock_attempts`, `mock_responses`). Section timing + negative
  marking + mark-for-review + per-position time tracking + auto-
  grade on submit + percentile recompute across cohort. Three
  modes (`full` / `sectional` / `pyq`). Topic breakdown rolled
  up automatically from each question's `topic_code`. Cohort
  stats (mean / median / p90) per paper. Paper lifecycle
  draft → published (≥5 questions required) → archived;
  publishing locks the question list.

- **`padhai/readiness.py`** — Exam Readiness Score (review §18).
  Single table (`exam_pack_readiness`). Headline 0-100 score per
  (user, pack) blends 5 components with default weights:
  mastery (35%) + mock perf (30%) + topic coverage (15%) +
  consistency from streaks (10%) + trust from citations
  grounding rate (10%). Each component is interpretable
  standalone — the UI surfaces all 5 so the student knows where
  to push. `STALE_AFTER_SEC = 3600` triggers auto-refresh on
  dashboard reads. `pack_leaderboard()` powers per-pack
  competitive rankings.

- **`padhai/tutor_grounding.py`** — Citation-aware tutor wrapper
  (review §14). 1 table (`tutor_session_modes`). Per-session
  answer mode (`general` / `source_only` / `official`).
  `send_grounded_message()` wraps any tutor reply: in strict
  modes, refuses ungrounded answers + returns `NOT_FOUND_MESSAGE`;
  `official` requires an `official_doc` citation specifically;
  cheat-guard refuses replies while a mock test is active.
  `user_recent_fallbacks()` lists every time we declined to
  answer — drives the "upload more material" nudge.

Routes: 426 → 446 (+20). New smoke `scripts/test_v3_2.py` runs
~90 checks across grading math, percentile recompute, readiness
component blending, fallback flows, and HTTP auth gates. Full
regression matrix v1.0 → v3.2 (26 smokes) green.

---

## v3.1.0 — Trust & Accuracy Foundation

**Phase 1 of the gap-review roadmap.** Three modules implementing
the review's top-6 critical-fix list: source grounding, exam
taxonomy, accuracy benchmark, plus a centralized ownership-check
helper. No new surface area beyond what these foundations need —
the principle is **depth before breadth** until accuracy is
measurable + grounded.

- **AI source citations** — `padhai/citations.py`. Two tables
  (`ai_answer_provenance` + `ai_citations`). Every AI answer
  links to (source_file, page_number, section, confidence,
  citation_text). Three answer modes: `source_only` (refuse if
  no citation), `official` (refuse if no official-source citation),
  `general` (LLM fallback with citations attached when available).
  `NotGroundedError` raises with `NOT_FOUND_MESSAGE` constant for
  the "I couldn't find this in your material" UX guard. Headline
  metric `grounding_rate()` aggregates by surface (tutor / lesson /
  quiz / essay / doubt) — drives the v3.1 trust dashboard.
  4 endpoints under `/api/citations/*` + `/api/admin/citations/*`.

- **Exam taxonomy + Exam Packs** — `padhai/exam_taxonomy.py`.
  Six tables (`exam_segments`, `exam_bodies`, `exams`,
  `exam_topics`, `exam_packs`, `exam_pack_enrollments`). Seeded
  catalog: 8 segments (kinder → research), 13 Indian exam bodies,
  18 exams across school / competitive / govt / professional,
  chapter trees with weightage_pct for CBSE 10 / CBSE 12 / UPSC
  CSE / SSC CGL / IBPS PO. **5 deep Exam Packs from review §Phase
  2** seeded on migrate: `cbse_class_10_2026`, `cbse_class_12_2026`,
  `upsc_cse_2026`, `ssc_cgl_2026`, `ibps_po_2026`, each with
  pattern_summary + cutoff_summary + estimated_hours. Per-pack
  student enrollment with daily_minutes budget + target_date +
  active/paused/completed/abandoned lifecycle. 13 endpoints under
  `/api/exam-taxonomy/*` + `/api/exam-packs/*` +
  `/api/admin/exam-taxonomy/*` + `/api/admin/exam-packs/*`.

- **AI accuracy benchmark** — `padhai/accuracy_bench.py`. Four
  tables (`bench_datasets`, `bench_items`, `bench_runs`,
  `bench_results`). Three task kinds (`answer_correctness`,
  `citation_correctness`, `quiz_key_correctness`); four judges
  (`exact_match`, `rouge_l`, `llm_judge`, `citation_check`) with
  lightweight ROUGE-L LCS implementation that avoids new deps.
  Dataset versioning + draft→published lock (≥10 items required).
  `run_benchmark(dataset_id, target, judge, runner)` is the
  one-shot API — caller hands in a callable that takes a prompt
  and returns `{"answer":..., "citations":[...]}`; we score every
  item, percentile the scores, and persist per-item diagnostics.
  Crashing runners are gracefully skipped (counted separately).
  `trust_dashboard()` aggregates pass rate + mean score per
  target — the headline v3.1 metric. 7 endpoints under
  `/api/admin/bench/*`.

- **`api_deps.require_owner` helper** — centralized ownership
  check (review §13). Resource modules register a resolver at
  import time (`register_owner_resolver`); endpoints call
  `require_owner(resource_type, resource_id, user)` and get
  consistent 401/404/403 semantics. Replaces ad-hoc checks
  scattered across job / upload / video / chat / note endpoints
  (migration happens incrementally as those endpoints are touched).

Routes: 402 → 426 (+24). New smoke `scripts/test_v3_1.py` runs
~85 checks across all four areas + HTTP + auth gates +
ownership-helper semantics + benchmark judges including crash
handling. Full regression matrix v1.0 → v3.1 (25 smokes) green.

**What's NOT in v3.1** (deliberately deferred per review §3):
- Phase 2 deep content (NCERT ingest, PYQ pipeline scale) — content
  work, not engineering
- Phase 3 community depth — community modules ship already, depth
  is moderation policy + content
- Hindi/regional UI typography + offline/PWA mode — UX streams

---

## v3.0.0 — University extension + affiliate program + DigiLocker

**ROADMAP_V3 capstone.** Tenth and final v3-roadmap release. Closes
out all 30 P/M/N/O/Q/R items: R2 (university / govt MOOC extension),
R4 (affiliate program), P4 (NDEAR DigiLocker integration). With
this drop the v3 surface is **30/30 SHIPPED** and the platform is
feature-complete against the 48-page PRD.

- **R2 — University / NPTEL extension** — `padhai/university_partners.py`.
  3 tables (`university_partners`, `partner_courses`,
  `partner_enrollments`). Per-partner integration kind (`lti13` /
  `rest_api` / `saml_sso` / `embed`) — NPTEL gets LTI 1.3 + signing
  keys; smaller universities use plain REST. Partner lifecycle
  `prospect → contracted → live → paused`; course `draft → published
  → archived`; enrollment `enrolled → in_progress → completed`
  (with `withdrawn` side-path). Revenue share configurable per
  contract in `[10%, 70%]` (default 30% to mirror NPTEL standard).
  `partner_stats()` aggregator drives the partner dashboard.
  11 endpoints under `/api/university/*` + `/api/admin/university/*`.

- **R4 — Affiliate program** — `padhai/affiliates.py`.
  4 tables (`affiliates`, `affiliate_visits`,
  `affiliate_attributions`, `commission_events`). Slug-based
  referral codes (`creator_alice`); 30-day click attribution window;
  12-month commission window after first paid invoice. 10%
  default commission (configurable per affiliate in [1%, 30%]
  for top performers). Idempotent `attribute_user` enforces
  first-touch attribution; `book_commission` is idempotent on
  (affiliate, invoice). Per-affiliate `affiliate_earnings` +
  program-wide `program_summary` rollups. IPs hashed at write
  (DPDP §10). 11 endpoints under `/api/affiliates/*` +
  `/api/admin/affiliates/*`.

- **P4 — NDEAR DigiLocker integration** — `padhai/digilocker.py`.
  4 tables (`digilocker_orgs`, `digilocker_doc_types`,
  `digilocker_consents`, `digilocker_issuances`). 4-type
  whitelisted catalog (`course_completion`, `exam_certificate`,
  `corporate_training`, `tutor_session_log`) seeded on migrate.
  Per-org sandbox → live activation flow. DPDP §6 explicit
  consent capture with HKDF-SHA256 Aadhaar hashing (never raw),
  exact consent_text stored verbatim for audit. DPDP §13 withdrawal
  via `revoke_consent`. Issuance lifecycle `pending → queued →
  issued / failed / revoked`. SHA-256 doc-body deduplication
  prevents double-issuance of the same credential. 9 endpoints
  under `/api/digilocker/*` + `/api/admin/digilocker/*`.

Route count: 371 → 402 (+31). New smoke `scripts/test_v3_0.py`
runs ~110 checks across data layer + HTTP + 401 auth gates + DPDP
consent gate + idempotency + fee math. Full regression matrix
(24 smoke files v1.0 → v3.0) green.

---

## v2.9.0 — Tutor marketplace + question-pack market + vouchers

M4 + O3 + R3 from ROADMAP_V3.md. Ninth v3-roadmap release.
**Three new revenue surfaces in one drop**: paid 1:1 tutors with
escrow, independent question setters selling curated practice
packs, and a full promo/bundle engine to drive top-of-funnel.

- **M4 — 1:1 tutor marketplace** — `padhai/tutor_marketplace.py`.
  Three tables (`marketplace_tutors`, `marketplace_bookings`,
  `marketplace_reviews`) wiring the **paid** counterpart to N4's
  volunteer mentorship. Tutor lifecycle: `applied` → `active`
  (admin approve) → `paused` / `suspended`. Booking lifecycle:
  `requested` → `confirmed` (tutor) → `in_progress` (either party
  joins) → `completed` (tutor) → `reviewed` (student). Side paths
  for `cancelled` / `refunded` / `no_show`. Pricing in 30-min
  blocks (30/60/90/120) at the tutor's chosen rate within
  ₹50–₹5000/30min. 20% platform fee by default (configurable per
  tutor for top earners). Earnings rolled up to tutor profile +
  ratings averaged. 14 new endpoints under `/api/marketplace/*`.

- **O3 — Question pack marketplace** — `padhai/question_pack_market.py`.
  Independent question setters (retired teachers, coaching brand
  authors, domain experts) verify with admin → publish curated
  packs sourced from / on top of J6's `question_bank`. Tables:
  `qb_setters`, `question_packs_for_sale`, `qb_pack_items`,
  `qb_pack_purchases`. Pack lifecycle draft → published (≥5
  questions required) → archived. One-time buy with idempotent
  purchase + 10% platform fee + 3-question preview for browsing.
  Refund path that reverses earnings. 16 new endpoints under
  `/api/qb-market/*`.

- **R3 — Vouchers + bundles** — `padhai/vouchers.py`. Promo coupons
  (`percent` / `fixed` / `bundle` kinds) with SKU scoping
  (`course_math_*` patterns), per-user + global redemption caps,
  start/expiry windows, min-order constraints. Atomic
  `redeem_voucher` + dry-run `validate_voucher`. Pre-defined
  bundles auto-discount when all required SKUs are in cart;
  `apply_bundle()` picks the highest-discount matching bundle.
  Distinguishes from R1 (geo + PPP) and R2 (income scholarships)
  — R3 is **promotional**, not means-tested. Three tables
  (`vouchers`, `voucher_redemptions`, `bundles`) and 10 new
  endpoints under `/api/{admin/,}vouchers` + `/api/bundles`.

Route count: 333 → 371 (+38). Smoke `scripts/test_v2_9.py` runs
~100 checks across data layer + HTTP + 401 auth gates + fee
math + idempotency. Full regression matrix (23 smoke files
v1.0 → v2.9) green.

---

## v2.8.0 — State partnerships + corporate training + sales pipeline

P3 + R1 + Q5 from ROADMAP_V3.md. Eighth v3-roadmap release.
**State govt deals + corporate L&D TAM expansion + sales ops
plumbing.** With P3 + R1 we go beyond K-12 schools into the
₹4L Cr govt + corporate adjacencies; Q5 makes the deal flow
visible + syncable to whatever CRM the sales team prefers.

- **P3 — State board partnerships** — `padhai/state_partnerships.py`.
  `state_partnerships` table with 7-state starter catalog
  (UP / MH / KA / TN / WB / DL / TG, the 5 ROADMAP_V3 priorities
  + 2 inbound). Status flow: prospect → discovery → pilot →
  contracted → live → paused. Per-state syllabus pack list +
  pilot org list + branding overrides. `pipeline_summary()`
  aggregates contract value + reachable students by status.
  Idempotent seed.
- **R1 — Corporate training mode** — `padhai/corporate.py`.
  4 tables: `corporate_orgs` (5 integration kinds: api / scorm /
  xapi / lti / sso_saml), `training_paths` (8 categories incl.
  compliance / security / soft_skills, draft → published →
  archived), `enrollments` (UNIQUE per (path, employee),
  status lifecycle, completion_pct tracking), `xapi_statements`
  (6 verbs: experienced / attempted / completed / passed /
  failed / progressed). **Seat-limit enforcement** across all
  paths for a corp — re-enrollment of existing employee doesn't
  consume a new seat.
- **Q5 — Sales pipeline integration** — `padhai/sales_pipeline.py`.
  `leads` + `lead_activities`. 4-component scoring (size 0-30 /
  intent 0-30 / recency 0-20 / completeness 0-20, plus stage
  adjustment ±50). Auto-recompute on stage change + activity log.
  Stage transitions auto-logged as activity for audit.
  **Outbound CRM webhook** to `SALES_WEBHOOK_URL` fires on
  create + stage_change events — HubSpot/Salesforce inbound
  endpoints accept the JSON shape directly.
- 27 new endpoints in `routers/v3.py`. 333 total routes. 22
  release smokes green.

## v2.7.0 — NEP/NCF alignment + DIKSHA/NDEAR + customer success

P1 + P2 + Q4 from ROADMAP_V3.md. Seventh v3-roadmap release. The
**govt sales unlock**: NEP/NCF compliance reporting, DIKSHA
interop, plus the customer success automation that compounds
revenue retention as the school base grows.

- **P1 — NEP 2020 + NCF 2023 alignment** — `padhai/nep_alignment.py`.
  Three tables: `nep_competencies` (10-item seed across fln / 21cs /
  digital / multidisciplinary categories), `ncf_competencies`
  (12-item seed across foundational / preparatory / middle /
  secondary stages × language/math/sciences/social), `lesson_
  alignment` (per-lesson per-competency scores). Keyword-overlap
  scorer with normalize → match → rank. `coverage_summary()`
  aggregates across lesson sets for student / org reports. Idempotent
  seed on migrate.
- **P2 — DIKSHA + NDEAR interoperability** — `padhai/diksha.py`.
  `diksha_content_refs` for cataloging imported DIKSHA content;
  UNIQUE(diksha_id) so re-import updates metadata. Two import
  paths: `import_from_manifest()` (admin paste) and
  `import_from_api()` (gated on `DIKSHA_API_KEY`). NDEAR 1.0
  manifest emitter (`build_ndear_manifest`) embeds NEP/NCF
  alignment; `record_export()` tracks each emission with manifest
  SHA for dedup. Govt RFP submission ready.
- **Q4 — Customer success automation** — `padhai/customer_success.py`.
  Three tables: `org_health_scores` (4 components × 25 max each:
  engagement / payment / support / growth), `cs_events` (kinds:
  onboarding_step / alert / nudge / escalation / note × severities
  info / warning / critical), `renewal_pipeline` (per-org with
  derived churn_risk from health band). `compute_health()` reads
  Q3 events + E5 fee invoices + org_members + cs_events without
  schema changes. 7-step onboarding sequence catalog. Global
  unresolved-alerts CSM queue. Renewal pipeline sorts high-risk
  first.
- 23 new endpoints in `routers/v3.py`. 306 total routes. 21 release
  smokes green.

## v2.6.0 — Teacher publishing + content marketplace + mentor program

O1 + O2 + N4 from ROADMAP_V3.md. Sixth v3-roadmap release. The
creator-economy layer: teachers sell series, publishers (boards /
NGOs / coaching) sell curriculum packs to schools, senior students
mentor juniors for free Pro months.

- **O1 — Teacher publishing platform** — `padhai/teacher_publishing.py`.
  Four tables: `published_creators` (apply → admin approve → active),
  `published_series` (draft → published → archived),
  `published_lessons` (auto-positioned), `series_purchases` (UNIQUE
  on user+series so no double-buy). **70/30 split** (creator/platform);
  per-creator override via `platform_fee_pct`. `has_access()` returns
  True for buyers OR the creator. Storefront filters by exam /
  subject / language. Creator earnings rollup per series. 11 endpoints.
- **O2 — Curriculum content marketplace** — `padhai/content_market.py`.
  Three tables: `publishers` (verify gate; 5 kinds: board, ngo,
  coaching, university, creator), `content_packs` (board / grade /
  subject / chapter_count), `content_subscriptions` (per-seat pricing,
  atomic re-subscribe expires prior). **10% platform fee** (configurable
  per publisher). Pro-rated duration billing. Publisher earnings
  dashboard. 9 endpoints.
- **N4 — Mentor program** — `padhai/mentorship.py`. Three tables:
  `mentor_profiles` (applied → approved → active → paused → suspended),
  `mentor_sessions` (scheduled → completed / cancelled / no_show),
  `mentor_reviews` (mentee-only, UNIQUE per session+reviewer, rating
  rollup). **Time-banked**: 1h completed mentoring = 1 free month of
  Pro (M3) tier (configurable via `HOURS_PER_FREE_MONTH`). 11 endpoints.
- 31 new endpoints in `routers/v3.py`. 283 total routes. 20 release
  smokes green.

## v2.5.0 — Forums + family plans + study buddies

N1 + N2 + N3 from ROADMAP_V3.md. Fifth v3-roadmap release. The
retention moat: students stay because their friends + parents are
here too.

- **N1 — Parent / student forums** — `padhai/forums.py`. Three
  tables: `forum_threads`, `forum_posts`, `forum_flags`. Scopes:
  org / class / grade / public. **Auto-hide at 3 distinct flags**
  (configurable). Moderator `unhide_post` clears flag count.
  Soft-delete (deleted_at) preserves audit trail. Lock + pin per
  thread. Author-only edit. 7 endpoints.
- **N2 — Family plans + sibling discount** — `padhai/family_plans.py`.
  `family_groups` + `family_members` + `family_subscriptions`.
  Pricing tiers: 1 child 0% off → 2nd child 30% → 3rd+ 40%.
  Idempotent subscription (re-subscribe expires the prior).
  Primary-parent guard prevents accidental family deletion.
  `quote()` is a public marketing endpoint. 8 endpoints.
- **N3 — Study-buddy matching** — `padhai/study_buddies.py`.
  `buddy_profiles` + `buddy_pairs` + `buddy_messages`. Matching
  algorithm scores: same exam (+3) / grade (+2) / language (+1) /
  complementary J5-weak-topics (+2) / overlapping windows (+1).
  Canonical pair ordering via lexically-smaller user_a_id +
  UNIQUE(a, b) so duplicate proposals collapse. **Two-sided
  accept** before status=active. `opt_out()` dissolves all
  active/proposed pairs. 11 endpoints.
- 26 new endpoints in `routers/v3.py`. 252 total routes. 19
  release smokes green.

## v2.4.0 — Math vision + mock interview + live mock-test events

L3 + L4 + M3 from ROADMAP_V3.md. Fourth v3-roadmap release.

- **L3 — Handwritten math vision** — `padhai/math_vision.py`.
  Two-stage pipeline: vision extracts LaTeX steps from a notebook
  image (Claude vision via Anthropic SDK with prompt caching);
  sympy validates each adjacent-step pair for algebraic equivalence
  and points at the first wrong step. Syntactic fallback when
  sympy unavailable (whitespace-normalized string equality).
  `MIN_CONFIDENCE` gate rejects low-confidence extractions to avoid
  misleading feedback.
- **L4 — Mock interview AI** — `padhai/mock_interview.py`.
  6 tracks (UPSC personality, JEE counseling, IIT placement,
  NEET PG, MBA admission, generic) with curated opening-question
  banks. Per-turn Claude scoring on 4 criteria
  (clarity/depth/relevance/confidence); follow-up questions
  generated by Claude based on the prior answer. Heuristic fallback
  uses hedge-word density + length. End-of-interview report
  aggregates per-criterion averages + top improvement themes.
- **M3 — Live mock-test events** — `padhai/mock_test_events.py`.
  Scheduled synchronous mock tests with countdown + live
  leaderboard. Race-safe registration + per-user attempt
  lifecycle (start → submit). Rank + percentile recomputed after
  every submit. Each question's correctness feeds the J5 mastery
  model — students who do poorly on Polity questions get more
  Polity practice via L5 next time.
- 17 new endpoints in `routers/v3.py`. 226 total routes. 18
  release smokes green.

## v2.3.0 — Live cohorts + doubt clearing + data warehouse

M1 + M2 + Q3 from ROADMAP_V3.md. Third v3-roadmap release.

- **M1 — Live cohort classes** — `padhai/live_classes.py`. Two
  tables: `live_classes` + `live_class_attendees`. Provider
  abstraction: LiveKit when `LIVEKIT_API_KEY`+`LIVEKIT_API_SECRET`
  set, Daily.co when `DAILY_API_KEY` set, HMAC-signed dev stub
  otherwise. Per-role access-token issuance (teacher can publish,
  student subscribes). Lifecycle: scheduled → live → ended →
  cancelled. Attendance tracking with seconds_present rollup.
  8 endpoints.
- **M2 — Live doubt clearing** — `padhai/doubt_clearing.py`.
  Student photo+question → tutor queue → claim (race-safe via
  conditional UPDATE) → answer with text+image+audio. Status
  flow: pending → claimed → answered (human) OR ai_answered OR
  cancelled. `stale_for_ai_escalation()` surfaces doubts older
  than `PADHAI_DOUBT_AI_ESCALATE_MIN` (default 15) min for the
  L1 auto-answer cron. 7 endpoints + stats.
- **Q3 — Event stream + data warehouse** — `padhai/analytics.py`.
  `events` (kind / props / source / user_id / org_id / session_id)
  + `daily_metrics` (date / metric / dimensions / value).
  Sampling via `PADHAI_EVENTS_SAMPLE_PCT`. DAU/MAU/funnel/D1-D7-D30
  retention queries. Daily rollup worker (`rollup_yesterday()`)
  pre-aggregates by metric to keep dashboards fast. Token-bucket
  rate-limited `/api/events` ingestion endpoint. 7 endpoints.
- 209 total routes. 17 release smokes green.

## v2.2.0 — Essay grader + adaptive practice tests + cost optimization

L2 + L5 + Q2 from ROADMAP_V3.md. Second v3-roadmap release.

- **L2 — Essay grader** — `padhai/essay_grader.py`. `essay_rubrics`
  (UNIQUE per exam/paper/topic) + `essay_submissions`. Claude-based
  per-criterion scoring with structured JSON output; heuristic
  fallback (keyword-overlap on criterion descriptions) when no API
  key. Teacher human-review queue with score override. 6 endpoints:
  list/upsert rubrics, submit/get/list submissions, human-review.
- **L5 — Adaptive practice tests** — `padhai/practice_test.py`.
  `practice_tests` table. Difficulty mix (30% recall / 50% standard
  / 20% stretch); recall slots target J5 weak topics, pulled from
  J6 question bank. Claude synthesis for missing slots; placeholder
  fallback when neither bank nor API available. Submit feeds each
  question's correctness back into J5 mastery so future tests
  adapt. 5 endpoints: create/get/list/start/submit.
- **Q2 — LLM cost optimization** — `padhai/llm_cache.py`.
  `with_caching()` wraps Anthropic system blocks with
  `cache_control: ephemeral` for the 90% input-cache discount.
  `submit_batch()` writes a JSONL outbox for the 50% batch
  discount; `poll_batch()` retrieves results. Env-gated
  (`PADHAI_LLM_CACHE_ENABLED`, `PADHAI_LLM_BATCH_ENABLED`); dev
  path works without keys.
- L2 + L5 both use `llm_cache.with_caching()` so the rubric / exam
  style guide is cached across submissions.
- 12 new endpoints in `routers/v3.py`. 185 total routes.

## v2.1.0 — First v3-roadmap release

L1 + L6 + Q1 from ROADMAP_V3.md — the foundation trio every other
v3 release builds on.

- **L1 — Real-time AI voice tutor** — `padhai/tutor.py`. Persistent
  conversations with per-user long memory + context summary. Daily
  cost cap per tier (M1=blocked, M2=₹20/day, M3=₹100/day,
  M4=uncapped). Canned fallback when `ANTHROPIC_API_KEY` unset so
  the dev path works. 5 endpoints: start, message, get, end, list.
- **L6 — LLM observability** — `padhai/llm_obs.py`. `llm_calls`
  table tracks tokens / cost / latency / cache hit per call.
  Model cost rates baked in (Opus/Sonnet/Haiku + cached variants).
  `record_call()` helper called from every Claude-touching module.
  Hallucination flag queue (`llm_flags`) with severity-sorted
  pending list. 3 endpoints: stats, flag, queue.
- **Q1 — Feature flags + A/B testing** — `padhai/feature_flags.py`.
  Token-bucket-style rollout (rollout_pct 0-100), deterministic
  hash bucket per (flag, user_id) — stable across requests so A/B
  cohorts hold. Variant selection with integer weights. Exposure
  logging at 10% sample rate for downstream A/B analysis. 5
  endpoints: list/upsert/delete/exposures + per-user resolve_all.
- New `padhai/routers/v3.py` (7th router) carries the 10 new endpoints.
- 173 total routes (up from 160). 15 release smokes green.

---

## v2.0.4 — 2026-05-20 — Docs + push batching

- **CHANGELOG.md** (this file) — aggregated release history v1.0 → v2.0.3
- **README.md** refresh — current product state (platform, mobile,
  enterprise SSO, coaching, 160 routes) replaces the v0.x CLI prototype
  framing
- `push.send_one()` — batched per-token log inserts into a single
  transaction (was N round-trips, now 1) via `executemany`

## v2.0.3 — 2026-05-20 — Router split pt 2

- Extracted 29 auth-coupled endpoints into `padhai/routers/me.py`
  + `padhai/routers/orgs_admin.py`
- New `padhai/api_deps.py` with `require_user` / `org_or_404` /
  `require_org_role` (web.py keeps backward-compat aliases)
- `web.py`: 11496 → 10891 lines (-605). Cumulative v2.0.2 + v2.0.3:
  11802 → 10891 (-911 / -7.7%)

## v2.0.2 — 2026-05-20 — Router split pt 1

- Extracted 19 public read-only endpoints into 4 router modules:
  `public_preview`, `catalog`, `coaching`, `question_bank`
- `padhai/routers/__init__.py` exposes `all_routers()`
- Pattern: lazy intra-module imports inside endpoint bodies

## v2.0.1 — 2026-05-20 — Hardening

- **CI smoke gate** (`.github/workflows/smoke.yml`) runs all release
  smokes on every PR + main push
- **Rate limits** (`padhai/rate_limit.py`) — token-bucket per IP on
  the 3 public preview endpoints
- **Size caps** — LaTeX 2k, diagram spec 8k, lesson_text 20k chars
- **Bug fix**: streaks `streak_7`/`streak_30` double-grant on
  same-day re-fire (regression check via `xp_events` history)
- `_orgs.deactivate_member` helper replacing inline SQL in SCIM PATCH
- Custom-domain validation (reserved hosts + RFC 6761 TLDs + IP-shape rejection)
- `requirements-optional.txt` documenting deps per feature

## v2.0.0 — 2026-05-20 — ROADMAP_V2 complete

Final ROADMAP_V2 release. **All 29 items shipped across v1.1 → v2.0
in 10 monthly releases.**

- **G5** Disaster recovery — RTO/RPO targets, backup automation
  (Neon → S3 Glacier nightly, R2 cross-region replication), 4
  restore scenarios, quarterly drill protocol
- **J5** Adaptive difficulty — EWMA mastery model, `weak_topics` +
  `strong_topics`, per-topic difficulty recommendation
- **K4** Preschool / Kids Mode v2 — 10-item seed catalog (phonics,
  counting, shapes, nursery, alphabet, stories) en + hi
- **H7** GeM procurement — 6-SKU catalog (school basic/premium,
  JEE/NEET/UPSC coaching, govt-state), 60-90 day paperwork runbook

## v1.9.0 — SAARC + coaching (K2 + K3)

- **K2** Country expansion — IN/BD/NP/LK/PK profiles with payment
  provider (razorpay/bkash/esewa/payhere/jazzcash), currency,
  timezone, education boards. Per-org country routing.
- **K3** UPSC/JEE/NEET coaching engine — `coaching_tracks`,
  `practice_attempts`, `current_affairs` tables. Subject-wise
  accuracy + weak-subject detection. UPSC daily digest.

## v1.8.0 — Curriculum scorer + Sarvam + Indic polish

- **J3** NCERT/CBSE curriculum alignment scorer — keyword overlap
  by default, Claude-path env-flagged. `alignment_score` 0-100 +
  per-objective coverage breakdown.
- **J4** Sarvam.ai bulbul TTS provider — 10 Indic languages,
  ProviderUnavailable fallback to Bhashini.
- **K1** South Indian language rendering polish — Tamil/Telugu/
  Kannada/Malayalam/Bengali per-language IndicProfile (font,
  speech_rate, comma pauses). Malayalam ZWNJ chillu fix.

## v1.7.0 — Parent + Teacher apps + question bank

- **I5 + I6** Parent + Teacher Capacitor variants
  (`mobile/{parent,teacher}/capacitor.config.json`); separate App
  Store listings.
- **J6** Board question bank (CBSE/ICSE past papers) — natural-key
  dedup, board/grade/subject/difficulty/text-LIKE search.

## v1.6.0 — Custom domains + SOC 2 + multi-region

- **H5** Custom top-level domains — verification token flow,
  reserved-host validation, `resolve_by_domain()` for routing
- **H6** SOC 2 Type 1 evidence dashboard — CC4/6/7/9 metrics + 8
  policies + `readiness_score`
- **G4** Multi-region scaffolding — `padhai/region.py`, 1-week
  cutover runbook (Cloudflare LB + Neon read replica)

## v1.5.0 — Streaks + math + diagrams

- **I4** Daily streaks + XP + leaderboards — EWMA-like streak math,
  level thresholds, GitHub-style calendar heatmap
- **J1** Math rendering — `latex_to_spoken()` with iterative
  nested-regex passes, KaTeX SSR when configured
- **J2** Procedural diagram generator — Mermaid + custom SVG
  (cycle, bar_chart, food_chain)

## v1.4.0 — Mobile apps (Capacitor)

- **I1 + I2** Capacitor 6 wrapper around the existing PWA
- `mobile/capacitor.config.json`, `mobile/src/native-bridge.js`
  (FCM/APNs token registration, deep links, native share)
- `MOBILE_BUILD.md` runbook with dev quickstart + store metadata

## v1.3.0 — Enterprise enablement

- **H1** SAML 2.0 SSO — per-org IdP config, SP metadata generator,
  ACS endpoint (python3-saml when present)
- **H2** SCIM 2.0 provisioning — per-org bearer tokens (hashed,
  shown-once), Users list/create/patch, ServiceProviderConfig
- **H4** Data residency flag — `data_residency` (global / india / eu)
  on orgs with downgrade lock + storage routing helper

## v1.2.0 — Queue + load test + push

- **G2** Redis + RQ queue scaffolding — `padhai/queue_backend.py`,
  `padhai/worker_entrypoint.py`, `G2_QUEUE_MIGRATION.md`
- **G6** Load testing — Locust harness with SLO gate (fail <0.1%,
  p95 <1500ms, p99 <3000ms)
- **I3** Push notifications — 3 tables, 6 categories, opt-in prefs,
  auto fan-out via `notifications.create()`

## v1.1.0 — Postgres foundation + CDN + audit

- **G1** Postgres backend selector + 5-phase cutover runbook
- **G3** CDN signed URLs (HMAC-SHA256) for video/audio/subtitles
- **H3** Audit log + CSV export — `audit.record()` + streaming
  `export_csv_iter()`, wired at 4 high-value sites

---

## v1.0.1 — Photoreal follow-up

- `modal_deploy.py` — production Modal config for Wav2Lip GPU worker
- `scripts/test_v1.py` — 16-check smoke harness

## v1.0.0 — Foundation + premium ship

Final ROADMAP v1 release. **All 28 items shipped across v0.10 → v1.0
in 9 releases.**

- **F1** PRD §12 native schema (additive) — `document_pages`,
  `video_requests`, `video_blueprints`, `generated_videos`
- **E9** White-label institutional branding — subdomain resolution,
  CSS-var injection
- **D3** PWA — manifest + service worker, branding-aware theme color
- **A1** Photoreal Wav2Lip deployment guide

## v0.10 → v0.16 — School ERP MVP

10 release cadence shipping the original 28-item ROADMAP. Each
release closed 3-5 items. See `ROADMAP.md` for the full scoping
table. Cumulative surface at v0.16:

- DPDP-compliant under-13 parental consent (S2), source-file retention (S3)
- Per-student analytics (E1), notifications (E2), attendance (E3),
  exams + auto-grading (E4), fees + invoicing (E5), timetable (E6),
  Google + Microsoft OIDC SSO (E7), parent linking (E8)
- Anti-cheating exam mode (S4), content moderation classifier (S1)
- 9 per-mode video generators (C1), v2 uploads API (C2), audio
  lecture upload (C3), video lecture upload (C4), whiteboard OCR
  (C5), page-level source citations (C7)
- 9:16 Reel renderer (D1), WhatsApp share (D2)
- Hosted avatar providers (A2) — HeyGen/Synthesia/Tavus/D-ID

---

## v0.1 → v0.9 — From CLI prototype to platform

Pre-ROADMAP era. Highlights:

- v0.1: scan-to-video CLI
- v0.6: `PersonalizationProfile`, 9 video modes, `/api/v2/video-requests`
- v0.7: Studio 4-step wizard, per-step progress, sidecar audio/
  subtitle endpoints, `[Scene N]` citations
- v0.8: Admin Console (separate app, separate auth)
- v0.9: Orgs / classes / members / assignments
