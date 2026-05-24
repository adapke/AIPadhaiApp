# AI Pathshala — Implementation Status

Tracks the 48-page PRD (`AI Pathshala Final Implementation Document`) against
what's actually shipped. Updated per release.

**Current release: v3.19.0** — Home UI UX fixes. Sidebar reduced
to 8 section titles (clicking scrolls to the chip group below).
Chips open an inline drawer with a "Try it" button that calls
the API + pretty-prints the response (no more raw JSON
navigation). Landing page now has a working Sign in / Create
account form posting to /auth/login + /auth/signup. Hero actions
scroll to relevant sections. 0 new endpoints (pure UI fix); 43
release smokes (test_v1.py → test_v3_19.py) all green; 592 total
routes.

**Previously: v3.18.0** — Goal-led home UI (review §26 + HTML
mockup, painted). `home_ui.py` — self-contained HTML+JS, no
framework. Browser `/` now serves the three-column "Exam Hub"
that fetches /api/navigation/manifest + /api/home/me/dashboard on
load. `/home` alias, `/landing` for public visitors,
`/ui-legacy` for the pre-v3.18 dashboard. JSON clients still get
JSON via Accept-header gate. PWA install affordances preserved
(manifest, theme-color, apple meta, service worker,
applyBranding). 3 new endpoints; 42 release smokes (test_v1.py
→ test_v3_18.py) all green; 592 total routes.

**Previously: v3.17.0** — Navigation manifest + student home
aggregator (review §26 + HTML mockup). `navigation.py` — static
manifest of the 8 goal-led sections (Exam Hub / Study Studio /
Mock Tests / AI Tutor / Community / School / Marketplace /
Admin & Trust) with `keep` / `new` badges per §26 and role
filter (student / teacher / parent / admin). `student_home.py`
— defensive composite over readiness + daily plan + next mock +
community + trust + recent fallbacks + module catalog; auto-
headline from weak/strong topics. 4 new endpoints; 41 release
smokes (test_v1.py → test_v3_17.py) all green; 589 total routes.

**Previously: v3.16.0** — Step-by-step math solver (review §7
Photomath / Gauth gap). `step_math.py` — 3 tables. Two solvers:
deterministic SymPy for linear equations (3 validated steps) and
LLM-driven for anything else (steps marked validated=False).
Step-level interaction: students flag confusing steps, tutors
add explanations with citations. Admin queue
`high_flagged_steps` surfaces high-confusion steps for editorial
rewrite. Heuristic problem-kind detection with derivative /
integral checks firing before quadratic (so ∫ x² dx isn't
mis-classified). 10 new endpoints; 40 release smokes (test_v1.py
→ test_v3_16.py) all green; 585 total routes.

**Previously: v3.15.0** — Adaptive personalised Exam Packs
(review §11 + §22). `adaptive_packs.py` — 3 tables. Per-user
overlay on top of v3.1 base Exam Packs. 4 adaptation rules read
from mastery + mock_engine + daily_plan: weak_topic_boost (+50%),
recent_mock_low (+40%), skipped_topic_boost (+30%),
strong_topic_relief (-30%). Adjusted weightage clamped to
[base × 0.3, base × 3.0]. `personalised_topic_view` falls back
to base when no override. should_re_adapt detects 7-day stale.
Full signal audit trail. 9 new endpoints; 39 release smokes
(test_v1.py → test_v3_15.py) all green; 575 total routes.

**Previously: v3.14.0** — Audio recap (review §7 — NotebookLM /
StudyFetch competitor gap). `audio_recap.py` — 2 tables. Structured
script (intro + 2-5 body + outro) with each segment rendered as a
separate audio file for transcript timeline UI. 3 source kinds
(upload / topic / free_text) × 3 answer modes (cited / source_only
/ general). Worker shell with sandbox provider (no-op TTS for dev)
and prod swap to `tts.get_provider()`. `generate_script_from_query`
closes the v3.3 retrieval → recap loop with citations preserved.
9 new endpoints; 38 release smokes (test_v1.py → test_v3_14.py)
all green; 566 total routes.

**Previously: v3.13.0** — WhatsApp / SMS messaging rails
(review §17 part 2). `messaging.py` — 3 tables. Per-user
(phone, channel) opt-in w/ E.164 validation + DPDP §6 consent
+ §13 opt-out + bounced state. Pre-approved templates with
`{{var}}` placeholders + auto-extracted variable list +
daily_max_per_user throttle. Schedule → render → send_due
worker that re-checks opt-in + throttle at send time. Sandbox
provider for dev; env-gated Meta / Twilio / MSG91 in prod.
Up to 3 retries before flipping to failed. 12 new endpoints;
37 release smokes (test_v1.py → test_v3_13.py) all green; 557
total routes.

**Previously: v3.12.0** — Offline packs + low-data mode
(review §17). `offline_packs.py` — 3 tables. Quality tiers
(text_only / standard / full) drive what's in the manifest;
priority 1-5 per file gates inclusion. Idempotent manifest
generation on (user, pack, version, tier). Download lifecycle
(start → update_progress → auto-complete on file_count met) with
resume + cancel. Daily usage tracking with quota_exceeded flag
for cellular gating. Per-user low_data_prefs (quality_tier +
auto_downgrade_on_cellular + max_daily_mb). 11 new endpoints;
36 release smokes (test_v1.py → test_v3_12.py) all green; 545
total routes.

**Previously: v3.11.0** — Marketplace quality controls
(review §16). `marketplace_quality.py` — 5 tables across all 4
marketplaces via (item_kind, item_id) keys. Ratings 1-5 with
helpful_count + UNIQUE (item, user). Refunds with 7d SLA +
auto-expire sweep. Copyright claims (plagiarism / unauthorized_use /
verbatim_copy / paraphrase × minor / moderate / severe); severe
auto-flips item to under_review on filing + auto-removes on uphold.
Quality 0-100 composite (rating × 20 - refund_penalty -
copyright_penalty + recency_boost) with auto-flip to under_review
below threshold 40. Admin override on item status. 15 new endpoints;
35 release smokes (test_v1.py → test_v3_11.py) all green; 534 total
routes.

**Previously: v3.10.0** — Research / PhD tools (review §9).
`research_tools.py` — 6 tables (research_papers + paper_summaries +
literature_collections + collection_papers + research_citations +
research_gaps). Per-user paper library with DOI + arXiv validation,
LLM-summary cache (short + key_findings + methods + limitations +
future_work), Zotero-style collections w/ ordering + paper_count
rollup, literature graph map (edges weighted by shared keywords +
authors), 2-strategy gap detection (auto-flag <30% coverage + caller-
proposed themes), citation manager with paper/tag filtering. 15 new
endpoints; 34 release smokes (test_v1.py → test_v3_10.py) all green;
520 total routes.

**Previously: v3.9.0** — Socratic tutor mode (review §7
Khanmigo + §14). `socratic_tutor.py` — 1 table. 4-state machine
(diagnose → hint → check → reveal). Confusion detection via 4
combined signals (idk markers, empty replies, short+slow, regex
patterns). Reveal-demand detection. Confusion-tolerance breach
(>2 confusions) auto-forces reveal. `reveal()` records provenance
via v3.1 citations so grounding metrics capture Socratic
exchanges. Per-user `completed_rate` + `avg_confusion_per_exchange`
stats drive the tutor engagement dashboard. 8 new endpoints; 33
release smokes (test_v1.py → test_v3_9.py) all green; 505 total
routes.

**Previously: v3.8.0** — Spaced repetition + active recall
(review §7 — Quizlet/Knowt gap). `spaced_repetition.py` — 4
tables (flashcard_decks + flashcards + flashcards_user_state +
flashcard_reviews). SM-2 algorithm with ease ∈ [1.3, 3.0] +
interval ∈ [1 day, 2 years]; grade 0-5 with <3 resetting state,
≥3 growing interval (1→6→i×ease). Card generation from retrieval
hits (`generate_from_chunks` closes the v3.3 → v3.8 loop) or
question_bank ids. Due queue with optional new-card slack.
Retention 30d metric (% reviews graded ≥3). 11 new endpoints;
32 release smokes (test_v1.py → test_v3_8.py) all green; 497
total routes.

**Previously: v3.7.0** — Expert review workflow (review
§12 + §22.10). The trust-gap closer. `expert_review.py` — 3
tables. Expert apply → admin-approve → active lifecycle with
per-expert rate (₹10-₹500/review), subject-routing, exam_codes,
languages. Review queue with 4 target kinds (ai_answer / qb_question /
pack / lesson), idempotent on (target_kind, target_id), 72h SLA
sweep. Atomic claim → decide (approve/correct/reject); approve +
correct create rows in `expert_verifications` for cheap
`is_verified()` lookups. Full rate for approve/correct, half rate
for reject. Public `/api/verifications/{target_kind}/{target_id}`
drives the "verified by teacher" badge on every content render.
15 new endpoints; 31 release smokes (test_v1.py → test_v3_7.py)
all green; 486 total routes.

**Previously: v3.6.0** — Parent + Teacher dashboards
(review §8). `dashboards.py` — pure read composer over mastery /
readiness / mock_engine / daily_plan / streaks / citations /
moderation_queue. Parent dashboard gated by
`parents.is_verified_parent_of`; teacher dashboard gated by
`orgs.require_role({teacher, admin})`; teacher→student deep-dive
verifies in-org membership. Class-scope via optional class_id;
class aggregate rolls per-student weak topics into a top-5
class-wide list weighted by frequency. Moderation flags raised
by class members surface in the teacher view. 4 new endpoints;
30 release smokes (test_v1.py → test_v3_6.py) all green; 471
total routes.

**Previously: v3.5.0** — Community moderation + reactions
(review §6). `moderation_queue.py` — 3 tables
(mod_flagged_content + mod_actions + mod_reactions). Auto-flag
scanner with 4 rules (blocklist / URL spam / all-caps /
repetition); 0..1 score with flag at 0.40, auto_remove at 0.90;
idempotent on (kind, id). Reviewer queue with SLA breach filter
+ approve/remove/escalate/restore actions + audit trail.
Reactions (like/helpful/thank/report) with unique constraint
enforcing throttle; report reactions auto-create queue entries.
12 new endpoints; 29 release smokes (test_v1.py → test_v3_5.py)
all green; 467 total routes.

**Previously: v3.4.0** — Daily plan generator (review §3 + §6).
Turns Exam-Pack enrollment + readiness + topic tree into a per-day
time-budgeted plan. `daily_plan.py` — 2 tables; 5 block kinds
(read/practice/mock/revise/current_affairs); allocation rules
practice 40% / read 25% / mock 15% / revise 10% / current affairs
10% (govt-only); weak topics auto-prioritised by gap-score
(1-mastery × weightage); auto-select next mock; `get_or_generate`
dashboard entry; `mark_block_done` rolls up to plan completion;
`should_regenerate` triggers (stale / no-plan / readiness-drift);
14-day completion stats feed readiness consistency. 7 new endpoints;
28 release smokes (test_v1.py → test_v3_4.py) all green; 458 total
routes.

**Previously: v3.3.0** — Closes review §4.1's biggest open
gap: retrieval. Token-overlap RAG over `document_pages`
(`retrieval.py` — 3 tables: doc_chunks + chunk_embeddings +
chunk_token_index; word-window chunker w/ overlap + tail merge;
heuristic section detection; idempotent indexing; TF-IDF cosine
scoring normalised to [0, 1]; min_score gating; `hits_to_citations()`
hand-off to v3.1 citations + v3.2 tutor_grounding). Zero-dep
default scorer with provider shim for swapping in
Anthropic / OSS embeddings later. 5 new endpoints; 27 release
smokes (test_v1.py → test_v3_3.py) all green; 451 total routes.

**Previously: v3.2.0** — Phase 1.5 of the gap-review roadmap.
Depth release composing v3.1 foundations into student workflow.
Universal mock engine (`mock_engine.py` — 4 tables; full/sectional/
pyq modes; section timing + negative marking + mark-for-review +
per-question time + auto-grade + cohort percentile + topic
breakdown). Exam Readiness Score (`readiness.py` — 1 table;
0-100 headline per (user, pack) blending mastery 35% + mock 30%
+ coverage 15% + consistency 10% + trust 10%; stale auto-refresh
+ pack leaderboard). Citation-aware tutor wrapper
(`tutor_grounding.py` — 1 table; per-session source_only/official/
general modes; cheat-guard during mock tests; user_recent_fallbacks
audit). 20 new endpoints; 26 release smokes (test_v1.py →
test_v3_2.py) all green; 446 total routes.

**Previously: v3.1.0** — Phase 1 of the gap-review roadmap.
Trust & Accuracy Foundation. Three modules implementing the
review's top-6 critical fixes plus a centralized ownership-check
helper. AI source citations (`citations.py` — 2 tables, every AI
answer links to source/page/section/confidence/citation_text;
three answer modes including `source_only` strict-mode refusal
with `NotGroundedError`; headline `grounding_rate()` metric).
Exam taxonomy + Exam Packs (`exam_taxonomy.py` — 6 tables;
seeded 8 segments / 13 Indian exam bodies / 18 exams /
chapter-level topic trees w/ weightage_pct; 5 deep Exam Packs
from review §Phase 2 — cbse_class_10_2026, cbse_class_12_2026,
upsc_cse_2026, ssc_cgl_2026, ibps_po_2026). AI accuracy benchmark
(`accuracy_bench.py` — 4 tables; 3 task kinds / 4 judges including
lightweight ROUGE-L; dataset versioning + draft→published lock at
≥10 items; `trust_dashboard()` pass-rate aggregate). 24 new
endpoints + `api_deps.require_owner` helper; 25 release smokes
(test_v1.py → test_v3_1.py) all green; 426 total routes.

**Previously: v3.0.0** — **ROADMAP_V3 capstone. 30/30 SHIPPED.**
Tenth + final v3-roadmap release. R2 (university extension —
`university_partners` + `partner_courses` + `partner_enrollments`,
LTI 1.3 / REST / SAML / embed integration kinds, prospect→
contracted→live→paused partner lifecycle, draft→published→archived
course lifecycle, 30% default revenue share configurable in [10%,
70%]), R4 (affiliate program — `affiliates` + `affiliate_visits` +
`affiliate_attributions` + `commission_events`, slug-based referral
codes, 30-day click attribution window, 12-month commission clock,
10% default commission, first-touch attribution sticky, idempotent
commission booking on (affiliate, invoice), IP hashed at write per
DPDP §10), P4 (NDEAR DigiLocker integration —
`digilocker_orgs` + `digilocker_doc_types` + `digilocker_consents` +
`digilocker_issuances`, 4-type whitelisted catalog seeded on
migrate, sandbox→live org activation, DPDP §6 explicit consent
with HKDF-SHA256 Aadhaar hashing, DPDP §13 withdrawal,
pending→queued→issued/failed/revoked issuance lifecycle, SHA-256
body dedup). 31 new endpoints; 24 release smokes (test_v1.py →
test_v3_0.py) all green; 402 total routes.

**Previously: v2.9.0** — Ninth v3-roadmap release. M4 (1:1
tutor marketplace — `marketplace_tutors` + `_bookings` + `_reviews`,
20% platform fee, 30/60/90/120-min booking blocks with escrow +
payout, applied→active→paused tutor lifecycle, requested→
confirmed→in_progress→completed booking lifecycle with
cancel/refund/no_show side-paths, rating rollup), O3 (question pack
marketplace — `qb_setters` + `question_packs_for_sale` +
`qb_pack_items` + `qb_pack_purchases`, 10% platform fee, draft→
published (≥5q) → archived pack lifecycle, idempotent one-time
purchase + refund path with earnings reversal, 3-question preview
for browsing), R3 (vouchers + bundles — `vouchers` +
`voucher_redemptions` + `bundles`, percent/fixed/bundle kinds with
SKU prefix-pattern scoping, per-user + global redemption caps,
min-order constraint, auto-detect bundle picks highest-discount
match). 40 new endpoints; 23 release smokes (test_v1.py →
test_v2_9.py) all green; 371 total routes.

**Previously: v2.8.0** — Eighth v3-roadmap release. P3 (state
board partnerships — `state_partnerships` table with 7-state
seeded catalog, status flow prospect→discovery→pilot→contracted→
live, pipeline_summary aggregator), R1 (corporate training —
4 tables: corporate_orgs / training_paths / enrollments /
xapi_statements, seat-limit enforcement across all paths,
status lifecycle, xAPI emit with 6 verbs), Q5 (sales pipeline —
`leads` + `lead_activities`, 4-component scoring 0-100, stage
transitions with auto-logged activity, outbound CRM webhook to
`SALES_WEBHOOK_URL` for HubSpot/Salesforce sync). 27 new endpoints;
22 release smokes (test_v1.py → test_v2_8.py) all green; 333
total routes.

**Previously: v2.7.0** — Seventh v3-roadmap release. P1 (NEP
2020 + NCF 2023 framework alignment — `nep_competencies` +
`ncf_competencies` + `lesson_alignment` with seeded starter
catalogs, keyword-overlap scoring, per-lesson persisted alignment,
coverage rollups across lesson sets), P2 (DIKSHA + NDEAR
interoperability — `diksha_content_refs` for govt-content import
+ `ndear_exports` for NDEAR-1.0 manifest emission with NEP/NCF
alignment embedded), Q4 (customer success automation —
`org_health_scores` computed from engagement/payment/support/
growth components, `cs_events` for alerts/nudges/onboarding,
`renewal_pipeline` with churn risk derived from health). 23 new
endpoints; 21 release smokes (test_v1.py → test_v2_7.py) all
green; 306 total routes.

**Previously: v2.6.0** — Sixth v3-roadmap release. O1 (teacher
publishing platform — `published_creators` + `_series` + `_lessons`
+ `_purchases`, 70/30 revenue split, draft→published lifecycle,
storefront browse, has_access for purchase OR ownership), O2
(curriculum content marketplace — `publishers` + `content_packs`
+ `content_subscriptions`, 10% platform fee, per-seat pricing,
pro-rated duration, atomic re-subscribe expires prior), N4 (mentor
program — `mentor_profiles` + `_sessions` + `_reviews`, applied→
approved→active lifecycle, time-banked hours → free Pro months
(1h = 1mo), mentee-only review with rating rollup). 31 new
endpoints; 20 release smokes (test_v1.py → test_v2_6.py) all
green; 283 total routes.

**Previously: v2.5.0** — Fifth v3-roadmap release. N1 (parent
forums with scope-based threads, auto-hide at 3 distinct flags,
moderator unhide), N2 (family plans + sibling discount — 0% / 30% /
40% tiers, idempotent subscription with auto-expire of prior),
N3 (study-buddy matching with mastery-aware complementary-topic
scoring + windows + canonical-ordered pairs + two-sided accept +
in-pair messaging). 26 new endpoints; 19 release smokes
(test_v1.py → test_v2_5.py) all green; 252 total routes.

**Previously: v2.4.0** — Fourth v3-roadmap release. L3
(handwritten math recognition — Claude vision → LaTeX extraction +
sympy step validation with first-wrong-step pinpoint, syntactic
fallback when sympy missing, heuristic dev path without API key),
L4 (mock interview AI — 6 tracks UPSC personality / JEE counseling /
IIT placement / NEET PG / MBA admission / generic, AI-driven
follow-up questions, per-criterion scoring with heuristic
fallback, aggregated end report), M3 (live mock-test events —
synchronous scheduled mocks with countdown + leaderboard, race-
safe registration + attempt lifecycle, mastery-feedback loop on
submit, rank/percentile recomputation). 17 new endpoints; 18
release smokes (test_v1.py → test_v2_4.py) all green; 226 total
routes.

ROADMAP_V2.md drives this and subsequent releases.

Release-by-release changelogs live in `git log main`.

---

## 1. Shipped (v0.1 → v0.6.0)

| PRD § | Feature | Module / Endpoint |
|---|---|---|
| §3.1 | Video Studio (partial — modules are separate, not unified) | `/` SPA, 16 sidebar modules |
| §3.2 | Explainer Studio | `POST /explain`, `POST /explain/video` |
| §3.3 | Student Upload Studio | `POST /lessons` (PDF, image, scan) |
| §3.4 | Teacher Studio | `mod-teacher` in SPA |
| §3.5 | Parent Dashboard | `mod-parent` + `GET /me/stats` |
| §4.1 | Teaching mode | default `/lessons` flow |
| §4.2 | Explainer mode | `/explain/video` |
| §4.4 | Kids mode (via level=kg) | `theme_for_level("kg")` → KINDERGARTEN |
| §4.5 | Teacher classroom (via teacher=true) | `mod-teacher` |
| §5.1 | PDF/image/scan upload | `padhai/ingest.py` |
| §5.1 | Typed-topic input | `POST /explain/video` |
| §6.4 | Hindi + 9 other Indic languages | `SUPPORTED_LANGUAGES` |
| §7 | Render scene per language | per-language jobs |
| §8 FR-1 | Upload | `POST /lessons` (multipart) |
| §8 FR-2 | Content understanding | Claude vision in `generate_lesson()` |
| §8 FR-3 | Adaptive script | **v0.6.0**: now driven by `PersonalizationProfile` |
| §8 FR-4 | Storyboard | `Lesson.scenes` |
| §8 FR-6 | M1 rendering | `padhai/render.py` (animated, reveal, static) |
| §8 FR-7 | Multi-language translation | per-language `generate_lesson()` calls |
| §8 FR-8 | Post-video outputs (quiz/notes/flashcards/recap) | `/lessons/{id}/{quiz,flashcards,recap,notes}` |
| §9.1 | Cached video <3s | content-addressed `Cache` |
| §10 | Architecture (FastAPI + Postgres + R2) | `Dockerfile`, `render.yaml`, `padhai/storage.py` |
| §13 | v2 API (uploads → request → status → result → regenerate → chat) | **v0.6.0**: `/api/v2/video-requests/*` |
| §13.6 | `/regenerate` with structured intent | **v0.6.0**: `make_easier`, `make_advanced`, `change_language`, `shorten`, `exam_focused`, `create_short` |
| §17.1 | Cache architecture (generate once, reuse) | 9-tier `Cache` |
| §17.3 | Model routing | Opus 4.7 for vision, Haiku 4.5 for flashcards/quiz/chat/explainer |
| §18.4-6 | Domain disclaimers (medical, finance, legal, policy) | **v0.6.0**: `detect_sensitive_domain()` + `DISCLAIMERS` |

## 2. Partial — ALL CLOSED in v0.7.0 ✅

| PRD § | Gap | Shipped in v0.7.0 |
|---|---|---|
| §3.1 | Unified "Create Video → Customize → Generate" flow | ✅ New `mod-studio` SPA module with 4-step PRD §15 wizard, hits `/api/v2/video-requests` |
| §6.7 | Output formats 9:16 / 1:1 | ✅ `render.set_canvas_dimensions()` + `render_lesson(dimensions=...)`; v2 endpoint threads `profile.output_dimensions` through |
| §6.7 | Duration enforcement | ✅ `build_user_text(target_duration_seconds=...)` adds a word-budget instruction (~140 wpm) to the Claude prompt |
| §8 FR-5 | Storyboard schema v2 (scene_goal, character_action, animation_type, assets) | ✅ `Scene` dataclass extended with 6 optional v2 fields (all backward-compatible; v1 Scenes deserialize cleanly) |
| §9.3 | Per-step progress events (PRD §10.2) | ✅ `JobStore.set_progress()` + `Job.progress_step`/`progress_percent`; workers emit 8 steps; status endpoint reads from DB; UI animates the step list |
| §11 | `output_assets` payload (subtitle_url, audio_url separately) | ✅ `GET /jobs/{id}/audio.mp3` (ffmpeg-extracted from MP4) + `GET /jobs/{id}/subtitles.srt` (built from cached Lesson scenes at ~140 wpm); v2 result surfaces both URLs |
| §13.7 | `/chat` with source citations | ✅ System prompt requires `[Scene N]` markers; `_parse_citations()` extracts and returns `source_citations: [{scene_number, scene_title}]` |

## 3. Deferred (Phase 2-5 per PRD §20)

| PRD § | Phase | Item |
|---|---|---|
| §3.6 | 3 | School / Coaching admin (bulk roster, class groups, SSO) |
| §4.3 | 1 | Standalone Exam Revision mode UI (have backend mode, no dedicated UI yet) |
| §4.9 | 2 | Reel renderer (9:16 vertical, fast-cut composition) |
| §5.2 | 2-3 | Audio lecture upload (Bhashini ASR) |
| §5.2 | 3 | Video lecture upload |
| §5.2 | 3 | YouTube transcript reference |
| §5.2 | 2 | Whiteboard photo OCR |
| §6.8 | 2 | WhatsApp compressed share |
| §10.3 | 4 | Photoreal Wav2Lip avatar (GPU worker code exists, needs production model + GPU host) |
| §10.3 | 4 | Hosted avatar providers (HeyGen, Synthesia, Tavus, DeepBrain, D-ID — code exists, needs API keys) |
| §12 | 2 | Native v2 DB schema migration (uploads / document_pages / video_requests / video_blueprints / generated_videos / generation_jobs / usage_daily / curriculum_index) — current schema works for MVP |
| §15 | 1-2 | Mobile-first Customize Video screen with chips |
| §16 | 2 | Admin console (queue, moderation, cost, cache hit rate, prompt versions) |
| §17.4 | 1 | Render-tier enforcement past M1 cartoon |
| §17.5 | 1 | Video-length controls on the free tier (30s/60s/5min cap) |
| §18.2 | 2 | Under-13 parental consent flow |
| §18.7 | 1 | Source-file retention/purge policy with admin override |
| §19 | 1-5 | Full team & 30-day execution plan (founder/PM stuff, not engineering) |

## 4. Won't build (explicitly out of scope per PRD §19)

- Full photoreal avatar in MVP
- Voice cloning
- Complex 3D animation
- 30-minute videos
- Native iOS app (PWA covers it)
- Marketplace
- Full School ERP (attendance / exams / fees / timetable)
- Audio/video uploads in MVP

## 5. v0.10.0 changelog (first ROADMAP execution)

Five items from ROADMAP.md shipped together as the "safety + first
analytics + share" release.

**F3 — Tier enforcement (PRD §17.4-5)**
- `padhai/personalization.py:TIER_LIMITS` — max_duration_seconds +
  max_render_tier per subscription tier (M1 free → M4e enterprise)
- `build_profile(user_subscription_tier=)` silently clamps duration +
  render_tier; defaults to M1 limits when tier is unknown (defensive
  against typos unlocking everything)
- v2 endpoint passes the user's tier through; anonymous = M1

**S1 — Content moderation (PRD §9.4, §18)**
- New `padhai/moderation.py` — Haiku 4.5 classifier with 9 categories
  (csam, hate, violence, scam, political, copyright, adult, other, ok)
  + severity 0-5
- `moderation_log` SQLite table: stores content_hash (not content) +
  category + severity for admin audit
- Hooks into `POST /explain` and `POST /api/v2/video-requests`
  before any expensive generation; rejects with 422 + category + log_id
- CSAM always blocked regardless of model's `allowed` field
- Fail-open on classifier outage (admin queue catches misses; full
  uptime is worth more than a brief moderation gap)

**S3 — Source-file retention/purge (PRD §18.7, DPDP §8(7))**
- New `padhai/retention.py` — `source_files` + `retention_policy`
  tables
- Defaults: 90 days free/student, 365 days institutional, configurable
  per-org override, per-file keep_forever flag
- `purge_due()` is idempotent + race-safe (UPDATE-as-claim pattern);
  preview via `list_due()`
- `scripts/purge_retention.py` — CLI for Render cron (with --dry-run)
- Local + R2/S3 backends supported

**E1 — Per-student analytics (PRD §3.6)**
- New `org_assignment_completions` table on the orgs schema
- `record_completion()` upserts watch_pct + quiz_score; watch_pct
  ratchets up (re-opens don't lower the high-watermark); quiz_score
  takes the best of any attempt
- `assignment_class_stats()` returns the full class roster joined with
  completion state so teachers see students who haven't started
- `student_assignment_history()` for the "click a student" drawer
- 3 new endpoints (POST /completion, GET /stats, GET /history) with
  explicit role guards — students can only POST their own progress
  and only GET their own history; teachers/admins see the class
- Studio Step 4 player will integrate the timeupdate beacon in v0.10.1
- UI: assignment row → click → drawer with KPI tiles + per-student
  table with status pills (completed / in_progress / not_started)

**D2 — WhatsApp share (PRD §6.8)**
- "📲 Share on WhatsApp" button in Studio Step 4 actions
- Uses Web Share API first (native sheet on Android + iOS); falls
  back to `https://wa.me/?text=...` deep link
- Carries lesson URL + mode + language in the share text

**Validated**
- All tier clamps work (M1 user → 10min m4 request → 5min m1 result)
- Moderation classifier (mocked): block, allow, fail-open, log persist
- Retention: idempotent purge, keep_forever respected, missing files
  tolerated, on-disk delete verified
- E1: ratchet (re-open doesn't lower watch_pct), quiz best-of, class
  roster includes never-started students
- HTML markup intact (vs-act-share + sch-modal-stats both render);
  JS balance 738/738 braces · 1897/1897 parens
- App boots at version 0.10.0; routes 47 → 50 (+3 E1 routes)

**Screenshots:** design_previews/47-48 (Studio share button + per-
assignment analytics drawer)

---

## 6. v0.8.1 changelog (Admin refactor per architecture decisions)

User decisions locked in:

| Decision | Implementation |
|---|---|
| Same repo, different dir | `admin/` stays at repo root |
| **Not shared code** (separate later) | All `from padhai import …` removed; admin reads jobs DB directly via `sqlite3`; cache dir via filesystem |
| **New auth** (not reusing main JWT) | Own `admin_users` table at `~/.padhai/admin.db`; own `ADMIN_JWT_SECRET`; own bcrypt; own signup/login/logout endpoints |
| Page refresh fine | No SPA — server-rendered HTML |
| **Same deploy for now** | Single Render service; admin mounted at `/admin/*` in `padhai/web.py` (one line: `app.mount('/admin', _admin_app)`); separate `padhai-admin` service block removed from `render.yaml` |

**New (admin auth)**
- `admin/auth.py` — rewrite: `AdminUser` dataclass (no subscription_tier), `create_user()`/`find_by_email()`/`find_by_id()`/`mark_login()`/`count_users()`; HS256 JWT with 12-hour TTL; bcrypt rounds=12; HttpOnly + SameSite=Lax cookie scoped to `/admin`
- Bootstrap pattern: first signup wins (no admin users → signup form shown); subsequent signups blocked with "Signups are closed"; future invite flow lives in v0.9
- `admin/templates.py render_login(allow_signup, error)` — login form + conditional signup tab + inline error display

**New (admin data layer — no padhai imports)**
- `admin/data.py` — direct SQLite via `sqlite3` with `mode=ro` (read-only by default)
- Graceful fallback: if `~/.padhai/jobs.db` doesn't exist yet, returns an in-memory empty schema so dashboard renders "0 of everything" instead of 500
- Write actions (`retry_job`, `cancel_job`) open a separate read-write connection
- `cache_stats()` walks the filesystem directly under `PADHAI_CACHE_DIR`

**Mount + deploy**
- `padhai/web.py` — `app.mount('/admin', _admin_app)` after the main `app = FastAPI(...)`
- `render.yaml` — `padhai-admin` service block deleted; `ADMIN_JWT_SECRET` env var added to the main service
- `admin/Dockerfile` kept (no `padhai/` copy anymore) for when admin splits to its own service later — mechanical move

**Validated end-to-end**
- `admin.app` imports zero padhai modules
- `padhai/web.py` mounts admin successfully; 16 admin routes nested under `/admin`
- Unauth `GET /admin/` → login page with "First-time setup" banner (when DB empty)
- `POST /admin/signup` → 303 redirect + HttpOnly cookie set + dashboard renders
- Second signup attempt → 403 "Signups are closed"
- Wrong password → 401 "Invalid email or password"
- `POST /admin/api/jobs/{id}/retry` flips a failed job to `queued` (verified by direct SQLite read)
- `POST /admin/api/jobs/{id}/cancel` on missing job → 404
- `GET /admin/logout` clears cookie via `Max-Age=0`
- `GET /admin/api/dashboard|cache-stats|jobs` all return valid JSON with admin token

**Routes**
- Main app: 33 + 16 admin = 49 total under `padhai/web.py`'s `app`
- Admin module standalone (when split out): 13 routes (excluding `/docs`, `/openapi.json`)

---

## 7. v0.8.0 changelog (Admin Console + PRD §16)

**Added: `admin/` — separate FastAPI project**
- `admin/app.py` — admin FastAPI with dashboard, jobs queue, topics & languages pages
- `admin/auth.py` — admin-role gating that wraps `padhai.auth.decode_token`;
  honours `subscription_tier ∈ {M4d, M4e}` and `PADHAI_ADMIN_EMAILS` allowlist;
  SSO via the same `padhai_token` cookie the main app sets
- `admin/data.py` — read-only DB queries: `dashboard_summary()` (KPIs + p95
  render time), `list_jobs()` (with status filter), `popular_topics()`,
  `language_usage()`, `daily_volume()`, `failed_jobs()`
- `admin/templates.py` — server-rendered HTML (no Jinja, just f-strings)
  matching the main app's design tokens
- `admin/Dockerfile` — separate container (no ffmpeg/fonts; admin doesn't
  render video)
- `render.yaml` — `padhai-admin` service block added (also `autoDeploy: false`)

**PRD §16 admin endpoints (all wired through `require_admin`)**
- `GET  /api/dashboard` — KPIs, language usage, top topics, 14-day volume
- `GET  /api/jobs?status=…&limit=&offset=` — paginated job list
- `POST /api/jobs/{id}/retry` — PRD §13 manual retry (moves failed → queued)
- `POST /api/jobs/{id}/cancel` — soft-cancel queued/running
- `GET  /api/cache-stats` — per-tier artifact count + bytes

**Other gaps closed**
- `Dockerfile` — adds LibreOffice (`libreoffice-impress`, `libreoffice-writer`)
  so PRD §5.1 P1 inputs (PPTX, DOCX) work in production. ~400MB image growth.
- `padhai/web.py` — new `GET /jobs/{id}/subtitles.vtt` endpoint (WebVTT
  format for native HTML5 `<video><track>`); v2 result surfaces
  `subtitle_vtt_url`; Studio Step 4 player attaches `<track>` with
  default-on so PRD §15 Screen 5 subtitles toggle works

**Validated**
- Admin auth: unauthenticated GET / returns login page; GET /jobs returns 401;
  POST /api/jobs/x/retry returns 401
- With admin token (cookie or Bearer): all pages render; dashboard shows
  real KPIs (total / in-flight / today / cache artifacts / avg+p95 render);
  jobs queue shows filter chips, status pills, per-job progress %, retry
  and cancel buttons; topics page lists 50 topics + languages
- Retry on missing job returns 404; cancel on missing/finished returns 404
- VTT endpoint returns valid WEBVTT-prefixed content

**Routes**
- Main app: 33 total (was 32 in v0.7.0; +1 for `/jobs/{id}/subtitles.vtt`)
- Admin app: 13 total (incl. `/docs`, `/openapi.json`, `/healthz`)

---

## 8. v0.7.0 changelog (all v0.6 §2 gaps closed)

**Added**
- `padhai/jobs.py` — `Job.progress_step` + `Job.progress_percent` columns (with
  idempotent ALTER TABLE migration); `JobStore.set_progress()`; canonical
  `PROGRESS_STEPS` tuple kept in sync with the API contract
- `padhai/pedagogy.py` — `Scene` v2 fields (`scene_goal`, `character_action`,
  `animation_type`, `assets`, `on_screen_text`, `subtitle` — all Optional);
  `build_user_text(target_duration_seconds=, profile_addendum=)`;
  `generate_lesson(target_duration_seconds=, profile_addendum=)` with profile-aware cache key
- `padhai/render.py` — `set_canvas_dimensions()`; `render_lesson(dimensions=)`;
  layout helpers now read module-level WIDTH/HEIGHT updated per call
- `padhai/web.py` — `_render_worker` + `_render_explainer_video` emit 8
  progress steps; `_progress_for_job()` reads worker emissions; v2 status
  surfaces them; `GET /jobs/{id}/audio.mp3` + `GET /jobs/{id}/subtitles.srt`
  sidecar endpoints; `_build_srt()` reconstructs subtitles from cached Lesson;
  `_parse_citations()` + `CHAT_SYSTEM_PROMPT` updated to require `[Scene N]`;
  `/chat/{lesson_id}` returns `source_citations` with resolved scene titles
- New `mod-studio` SPA module — 4-step PRD §15 wizard (Source → Customize →
  Generate → Result) hits `/api/v2/video-requests`; populates 9 video modes
  from `/api/v2/video-modes`; 8 audience types, 7 ages, 16 grades, 10
  languages, 7 tones, 6 durations, 3 output formats; live progress with
  step-list animation; 9 action buttons including download/audio/subs/chat;
  full regenerate intents wired
- `render.yaml` — `autoDeploy: false` (per request — flip back to redeploy)

**Unchanged**
- All v1 endpoints (`/lessons`, `/explain`, `/jobs/{id}`, `/jobs/{id}/video`)
- v2 endpoints from v0.6.0 (`/api/v2/video-requests*`, `/api/v2/video-modes`)
- All 16 existing SPA modules (Studio is added as the new 17th + new
  primary entry)

**Total routes**: 32 (was 30 in v0.6.0; +2 sidecar artifact routes)

**Validated**
- Citation parser handles all formats: single `[Scene 1]`, comma-joined
  `[Scene 2, Scene 3]`, mixed digits `[Scene 1, 4]`, multiple separate
  brackets, out-of-range filtering
- SRT builder reconstructs valid subtitle timing from scene narration
- Scene v1 (3 fields) and v2 (10 fields) JSON round-trip works
- Bracket balance in JS: 591/591 braces, 1556/1556 parens
- All 4 Studio wizard steps render correctly (screenshots in `design_previews/29-32`)
- v1 + v2 + Studio all coexist; backward-compat verified

---

## 9. v0.6.0 changelog

**Added**
- `padhai/personalization.py` — `PersonalizationProfile` + `build_profile()` + 9 `VIDEO_MODE_TEMPLATES` + sensitive-domain detection + 4 domain disclaimers + `apply_regenerate()` with 6 structured intents
- `POST /api/v2/video-requests` — PRD-shaped video creation
- `GET /api/v2/video-requests/{id}/status` — per-step progress
- `GET /api/v2/video-requests/{id}/result` — final artifacts + 9 user actions
- `POST /api/v2/video-requests/{id}/regenerate` — linked regen with intent
- `GET /api/v2/video-modes` — enumeration for UI dropdowns

**Unchanged**
- All v1 endpoints (`/lessons`, `/explain`, `/jobs`, `/chat`, `/learning-path`, `/curriculum`, `/me/stats`, `/auth/*`, etc.)
- All 16 SPA modules
- Render pipeline, TTS, Docker image

**Migration path**
- New apps: use `/api/v2/video-requests` exclusively.
- Existing apps: stay on `/lessons`, migrate at your own pace.
- v0.8.0 will deprecate v1 with a 6-month transition window.

---

## 10. v1.0.0 changelog — Foundation + premium ship

Closed the final four ROADMAP items: F1, E9, D3, A1. All 28 items now
shipped (see `ROADMAP.md` for the original scoping table).

**F1 — PRD §12 native schema (additive, non-breaking)**
- `padhai/schema_v2.py` — 4 new tables (`document_pages`,
  `video_requests`, `video_blueprints`, `generated_videos`) as the
  future relational shape. `CREATE TABLE IF NOT EXISTS` + UNIQUE
  constraints make migrate() idempotent on every boot.
- Cache-key idempotent for regenerable artifacts (`generated_videos`
  keyed by hash of the inputs that uniquely identify a render).
- Helpers: `add_pages_for_upload`, `record_video_request`,
  `record_generated_video`, `cache_hit_stats`. Existing job rows
  untouched — v1.1 will dual-write, then cut over.

**E9 — White-label institutional branding**
- `padhai/branding.py` — 5 additive columns on `orgs`: `brand_name`,
  `brand_logo_url`, `brand_color`, `brand_accent`, `brand_subdomain`
  (UNIQUE index).
- `resolve_by_subdomain(host)` parses the Host header → leftmost
  label → matching org row. SPA's `applyBranding` IIFE fetches
  `/api/branding/resolve` and sets CSS vars on `<html>`.
- Validators reject non-hex colors, reserved subdomains (`www`,
  `api`, `admin`, etc.), and out-of-range slugs.
- Tolerant of cold-start: `try/except sqlite3.OperationalError` on
  every read so fresh DBs without the `orgs` table don't 500.
- Endpoints: `GET /api/branding/resolve`, `GET/POST
  /api/orgs/{id}/branding`, `POST /api/orgs/{id}/branding/logo`,
  `GET /branding/logo/{filename}`.

**D3 — PWA / offline save**
- `GET /manifest.json` — branding-aware (theme color + name come
  from the resolved org branding when on a school subdomain).
- `GET /sw.js` — two cache layers: `padhai-shell-v1` (HTML / JS /
  CSS) + `padhai-media-v1` (videos, audio, subtitles).
- Strategies: network-first for `/api/`, `/auth/`, `/chat/`
  (returns JSON 503 on offline); cache-first for
  `/jobs/{id}/video|.mp3|.srt|.vtt`.
- SPA shell additions: `<link rel="manifest">`, theme-color +
  `apple-mobile-web-app-*` meta tags, SW registration call.
- `SAVE_OFFLINE` postMessage from the page to cache specific URLs
  on demand (used by the "Save offline" button on each lesson).

**A1 — Photoreal Wav2Lip deployment guide** (`A1_PHOTOREAL_DEPLOY.md`)
- Modal (recommended) / RunPod / dedicated-GPU options.
- Env vars, model weights, source-photo setup.
- Cost model: ₹3-4 per 5-min lesson on an A10G at Modal Mumbai.
- Operational checklist + emergency rollback via
  `PADHAI_TALKING_HEAD_PROVIDER=cartoon` env override.

**Version bump**: `0.16.0` → `1.0.0`. Total routes: 102 (up ~7 from
v0.16).

---

## 11. v1.0.1 changelog — Photoreal follow-up + smoke

Tightly-scoped polish release as promised in A1_PHOTOREAL_DEPLOY.md.

**Added**
- `modal_deploy.py` — production Modal config for the Wav2Lip GPU
  worker. One warm A10G replica in Mumbai, auto-scales to 3 max,
  5-min idle shutdown. Mounts the persistent volume that holds the
  416MB checkpoint + the teacher source photo. `modal deploy
  modal_deploy.py` ships it; `modal app stop padhai-wav2lip` rolls
  it back (avatar router circuit-breaker handles the cutover).
- `scripts/test_v1.py` — 16-check smoke harness for the v1.0
  surfaces (sw.js, manifest.json, branding endpoints, SPA wiring
  markers, F1 tables, version). Runs against a fresh sandbox DB
  to exercise the cold-start path. `PYTHONPATH=. python
  scripts/test_v1.py` → exits 0 on PASS.

**Validated**
- All 16 smoke checks pass on a cold DB:
  - 3× D3 PWA: `/sw.js`, `/manifest.json` default, `/manifest.json`
    with subdomain header (no crash on missing org)
  - 2× E9 branding: `/api/branding/resolve` returns platform
    defaults; `/api/orgs/{id}/branding` requires auth (401)
  - 6× SPA shell: manifest link, theme-color, SW register,
    `applyBranding`, apple-mobile meta, version 1.0.0
  - 4× F1 tables: `document_pages`, `video_requests`,
    `video_blueprints`, `generated_videos` all created at startup
  - 1× version: root endpoint reports `1.0.0`

**Unchanged**
- All v1.0.0 code paths. Pure additive release — no API surface or
  schema changes.

---

## 12. v1.1.0 changelog — Postgres foundation + CDN + audit log

First ROADMAP_V2 release. Three items: G1 scaffolding, G3 full,
H3 full.

**G1 — Postgres migration scaffolding** (cutover deferred to v1.1.x)
- `padhai/db_backend.py` — engine selector that parses `DATABASE_URL`.
  Today returns SQLite by default; recognises `postgres://` URLs
  (and Neon `*.aws.neon.tech` hosts for region detection) so future
  modules can target the abstraction.
- `G1_POSTGRES_MIGRATION.md` — 5-phase cutover runbook: dual-write,
  backfill, read-shadow, cutover window, hardening. Vendor choice
  (Neon serverless, AP-South-1), budget (~$30-50/mo Postgres tier),
  rollback decision tree.
- Startup banner now prints the active backend
  (`[startup] db backend: sqlite://...`).
- Deliberately **not** in v1.1: psycopg dependency, Alembic, per-module
  ports — those land in the dedicated cutover sprint.

**G3 — CDN signed URLs for video / audio / subtitles**
- `padhai/cdn.py` — HMAC-SHA256 signed URLs. `?expires=&sig=` query
  params; TTL clamped to [60s, 7d]; constant-time verify.
- `maybe_redirect(path, request=request)` returns a signed URL when
  `PADHAI_CDN_BASE_URL` + `PADHAI_CDN_SIGNING_KEY` are set AND the
  request isn't a CDN-origin fetch (loop guard via
  `X-CDN-Origin-Fetch` header + `PADHAI_CDN_ORIGIN_SECRET`).
- 4 delivery endpoints wired: `/jobs/{id}/video`, `audio.mp3`,
  `subtitles.srt`, `subtitles.vtt`. When CDN configured → 302 to
  signed CDN URL; else falls through to existing FileResponse /
  R2-direct path.
- Pure additive: no env config → no behaviour change.

**H3 — Audit log + CSV export**
- `padhai/audit.py` — single `audit_log` table, 4 indexes
  (org+time, actor+time, action+time, target). `record()` swallows
  every exception so audit bugs can never block a real mutation.
  `query()` paginated read; `count()` for totals; `export_csv_iter()`
  streams chunks of 500 rows so 100k-row exports don't OOM.
- `actor_from_request(request)` helper extracts IP (X-Forwarded-For
  aware), UA (truncated 200 chars), X-Request-Id for trace correlation.
- Endpoints:
  - `GET /api/orgs/{id}/audit` — paginated query with filters
    (action exact / action_prefix / from_ts / to_ts / limit / offset)
  - `GET /api/orgs/{id}/audit/export.csv` — streaming CSV download
- Wired at 4 high-value sites in v1.1:
  - `POST /auth/login` → `auth.login.success` / `auth.login.fail`
  - `POST /api/orgs/{id}/members` → `org.member.invite`
  - `POST /api/orgs/{id}/branding` → `org.branding.update` (with
    before/after snapshot)
  - `POST /api/orgs/{id}/exams/{eid}/attempts/{aid}/grade` →
    `org.exam.grade.override` (highest-fraud-risk action)
- Future v1.1.x will wire the remaining ~15 sites (E1-E9 mutation
  surface, admin retry/cancel, SSO config, parent link verify).

**Version bump**: 1.0.0 → 1.1.0. Total routes: 105 (was 102; +2 audit
+ subtle: existing video/audio/subtitle handlers gained an optional
Request param for CDN routing but route count stays).

**Validated**
- `scripts/test_v1_1.py` — 23 checks, all pass:
  - 5× G1: SQLite default, Postgres URL detection, Neon region
    parsing, description hides password
  - 6× G3: not-configured fallback, signed URL shape, round-trip
    verify, tampered-sig rejection, expired-URL rejection
  - 7× H3: audit_log table created, record + query roundtrip, IP +
    note preserved, action_prefix filter, before/after JSON
    survives, 401 unauth on both endpoints
  - 2× delivery: /jobs/{id}/video without CDN configured returns 404
    (not 302) for nonexistent jobs
  - 1× CSV: stream starts with header row + includes logged event
  - 1× version: root reports 1.1.0
  - 1× regression: v1.0 smoke (test_v1.py) still passes

---

## 13. v1.2.0 changelog — Queue scaffolding + load tests + push notifications

Second ROADMAP_V2 release. Three items: G2 scaffolding, G6 full,
I3 full.

**G2 — Distributed queue scaffolding** (cutover deferred to v1.2.x)
- `padhai/queue_backend.py` — selector returns `RQRunner` when
  `REDIS_URL` + `rq` available, else falls back to the in-process
  `JobRunner`. `RQRunner` skeleton class documents the interface
  contract (enqueue / resume_pending / shutdown — same as JobRunner).
- `padhai/worker_entrypoint.py` — `python -m padhai.worker_entrypoint`
  boots the right worker mode. RQ-backed when `REDIS_URL` is set;
  in-process fallback otherwise. CLI flag `--queue` for
  `renders` vs `wav2lip-renders` (GPU split).
- `G2_QUEUE_MIGRATION.md` — 1-week cutover plan: provision Upstash,
  spin up worker container, flip web tier, split GPU onto its own
  queue, wire monitoring + SLOs. Vendor choice (Upstash + python-rq),
  cost model (~₹8-12k/mo at pilot scale), rollback decision tree.
- Startup banner now prints both DB + queue backend.
- Deliberately NOT in v1.2: `rq` + `redis` in `requirements.txt`,
  web.py's `runner = JobRunner(...)` flipped to `build_runner(...)`
  (one-line change on the cutover branch).

**G6 — Load testing harness** (full)
- `scripts/loadtest_locustfile.py` — Locust file with two user
  classes:
  - `BrowsingUser` (80% weight) — exercises SPA shell, manifest,
    SW, video-mode catalog, branding, curriculum index, health
  - `VideoUser` (20% weight) — polls status/result on pre-existing
    job IDs passed via `PADHAI_LOADTEST_JOB_IDS` (avoids burning
    Anthropic budget on synthetic loads)
- `@events.quitting` hook enforces SLO gate: fail ratio <0.1%,
  p95 <1500ms, p99 <3000ms. Exit code feeds CI.
- `LOAD_TESTING.md` — run instructions, SLO targets, capacity
  planning matrix (single replica → multi-region scale).

**I3 — Push notifications** (full)
- `padhai/push.py` — three tables: `push_tokens` (UNIQUE on
  user_id+token), `push_prefs` (per-category opt-in), `push_log`
  (one row per send attempt; indexed for unopened-by-user query).
- 6 categories: assignment_due, exam_alert, attendance,
  announcement, streak (all default-ON), marketing (default-OFF).
- `register_token()` idempotent on (user_id, token); first
  registration seeds default prefs.
- `send_one()` fans out to every active token for the user;
  respects opt-out; writes log row with `failed_reason='no_provider'`
  when FCM/APNs/VAPID keys aren't configured (so admin telemetry
  shows what would have been delivered).
- Platform adapters scaffolded — FCM HTTP v1, APNs HTTP/2, Web Push
  VAPID. Real send logic lands in v1.4 when iOS/Android apps ship
  and we have device tokens to test against.
- `fan_out_for_notification(n, recipients)` called automatically
  after `notifications.create()` returns. Audience resolver
  (`_resolve_audience` in web.py) maps `all`/`class:<id>`/`role:<r>`/
  `user:<id>` → concrete user_ids using existing `_orgs.list_members`.
- 7 new endpoints:
  - `POST /api/users/me/push-tokens` — register
  - `DELETE /api/users/me/push-tokens` — soft-delete on logout
  - `GET /api/users/me/push-prefs` — every category's enabled state
  - `POST /api/users/me/push-prefs` — update one category
  - `POST /api/push/{log_id}/opened` — client beacon for open-rate
  - `GET /api/push/log` — diagnostic feed (self or admin-cross-user)
  - `GET /api/push/stats` — last-N-hours aggregate (public, no PII)

**Version bump**: 1.1.0 → 1.2.0. Total routes: 105 → 112 (+7 push).

**Validated** (`scripts/test_v1_2.py`, 27 checks all pass):
- 4× G2: inprocess default, description text, REDIS_URL detection
  without `rq`, fallback warning
- 14× I3: 3 table migrations, token register / idempotent re-register,
  default-ON prefs seeded, set_pref persists, unknown category
  rejected, send_one no-provider path, opt-out skip-no-log,
  recent_log query, stats aggregation, mark_opened idempotency
- 5× HTTP: /api/push/stats public, /api/users/me/push-prefs 401,
  /api/users/me/push-tokens 401, locustfile exists, doc exists,
  runbook exists
- 1× version: 1.2.0
- Regressions: v1.0 + v1.1 smokes still pass (one stale v1.1
  version assertion relaxed to "v1.x line")
