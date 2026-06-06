# Competitive Analysis — AIPadhaiApp vs. Indian EdTech Field

**Status: 2026-06-06.** This document is a snapshot. The competitive
landscape moves quarterly; revisit each quarter before fundraise or
launch milestones.

The goal of this analysis is to answer one question honestly: **can
the current functionality challenge the Indian EdTech market, and
what does it actually need to launch?**

Companion docs: PRODUCTION_CHECKLIST.md (deploy-day gates),
SECURITY.md (hardening), CLAUDE.md (engineering reference).

---

## 1. The field (May–June 2026)

### Indian incumbents

| Player | Strength | Weakness |
|---|---|---|
| **BYJU's** | Largest content catalog (~50k pre-recorded lessons), K-12 brand recognition | Cost-cutting after 2023 funding crisis, AI features lag |
| **Vedantu** | Live tutors at scale (1000+), Hindi + regional language support | Per-class pricing, no AI generation |
| **Unacademy** | Strong in JEE/NEET/UPSC, top-coach brand | Expensive, no school ERP layer |
| **PhysicsWallah** | Tier-2/3 price point (~₹5k/year), competitive prep moat | Less polished UX, no real AI |
| **Embibe (Reliance)** | Adaptive learning, deep Reliance pockets | Slow product cadence |
| **Aakash / Toppr** | Hybrid (online + offline coaching) | Legacy tech stack |
| **Doubtnut** | Camera-based doubt solving (10M+ MAU) | Single-feature; no curriculum integration |

### International players accessible in India

| Player | Strength | Weakness for India |
|---|---|---|
| **Khan Academy / Khanmigo** | Free, Socratic AI, Bill Gates endorsed | No CBSE/ICSE alignment; English-only |
| **Quizlet / Knowt** | Mature SRS flashcards | No board-specific content |
| **StudyFetch** | "Talk to your PDF" RAG | No multi-language, no exam-prep focus |
| **NotebookLM (Google)** | Audio recaps, source-grounded chat | Free for now, no education-specific scaffolding |
| **ChatGPT / Claude Pro** | Direct LLM access ($20/mo) | Parents pay; no DPDP §9 minor protection, no curriculum |
| **Photomath / Gauth** | Camera math solver | Math-only, no broader curriculum |

---

## 2. AIPadhaiApp — what's actually in the codebase

Inventoried from the 25 polish/prod sprints. Only counting working
implementations, not aspirational PRD bullets.

### Features that genuinely differentiate

1. **AI-generated explainer videos from textbook images.**
   Photograph a textbook page → 3-5 minute teaching video in 10
   Indian languages. Backend: `padhai/pedagogy.py` (Opus + adaptive
   thinking) + `padhai/render.py` (ffmpeg + TTS). **No major Indian
   competitor ships this.**
2. **10 Indian languages with backend support.** Hindi, Tamil,
   Telugu, Kannada, Marathi, Bengali, Gujarati, Punjabi, Malayalam,
   English. Backend complete; SPA translation status varies
   (P0 audit item — see §4).
3. **9 board/exam tracks** verified by the 370-item accuracy
   bench: CBSE (Class 6-12), ICSE, IGCSE, Maharashtra, Karnataka,
   TamilNadu, AP/Telangana, UP, plus JEE/NEET/UPSC/SSC.
4. **DPDP Act 2023 §9 compliance**: under-18 accounts locked until
   parent-redeemed consent token. Most international tools fail
   this — they treat 13 as the threshold. **Legal moat in India.**
5. **School ERP layer**: orgs / classes / members / attendance /
   timetable / assignments / exams / fees / branding / SCIM /
   SAML. Parent + teacher dashboards. Multi-tenant gates locked
   by parametrised tests. Pure-AI competitors (Khanmigo,
   StudyFetch) don't have this.
6. **Per-tier daily LLM cost cap** (`llm_obs.check_daily_cap`):
   M1 = ₹0/day (premium-only), M2 = ₹20, M3 = ₹100, M4* = uncapped.
   **Unit economics actually solvent at scale.**
7. **Tiered model routing**: Haiku for cheap surfaces (tutor,
   recap, flashcards), Sonnet for balanced (essay, doubt-vision,
   upload chat), Opus for full lesson generation. Centralised in
   `padhai/models.py`.

### Feature parity with major competitors

| Competitor feature | Our equivalent | Status |
|---|---|---|
| BYJU's pre-recorded lessons | On-demand AI generation | Different model; no catalog yet |
| Vedantu live tutors | Marketplace (`tutor_marketplace.py`) | Unbootstrapped (0 tutors signed) |
| Doubtnut camera + voice | `padhai/doubt_clearing.py` | Working, full pipeline |
| Photomath / Gauth | `padhai/math_vision.py` + `step_math.py` | Working, step-validated |
| Khanmigo Socratic | `padhai/socratic_tutor.py` | Working, confusion-detected |
| Quizlet flashcards | `padhai/spaced_repetition.py` (SM-2) | Working |
| StudyFetch RAG | `padhai/retrieval.py` + `routers/uploads_ai.py` | Working with `[Scene N]` citations |
| NotebookLM audio recap | `padhai/audio_recap.py` | Working, cached |
| Unacademy mock tests | `padhai/mock_engine.py` + anti-cheat | Working, tab-blur tracking |
| BYJU's leaderboards | `padhai/streaks.py` + `orgs_leaderboard` | Working |
| Aakash test-series | `padhai/adaptive_packs.py` | Working, mastery-weighted |

### Features built that competitors *also* lack

- **NEP 2020 + NCF 2023 alignment scoring** (`nep_alignment.py`)
  — govt RFP advantage
- **DIKSHA + NDEAR interop** (`diksha.py`) — govt content import
  + export with manifest signing
- **State partnerships pipeline** (`state_partnerships.py`) — 7
  states seeded
- **Corporate training mode** (`corporate.py`) — L&D TAM
  expansion
- **Affiliate program with first-touch attribution + 30-day
  window** (`affiliates.py`)
- **Voucher + bundle engine** (`vouchers.py`) — promotional
  pricing
- **University partnerships** (`university_partners.py`) — LTI
  1.3 + REST + SAML SSO

That's a stronger feature surface than any single Indian
competitor.

---

## 3. The honest gaps (in priority order)

### P0 — blocking commercial launch

These prevent the first 1000 paying users from converting. Engineering
can ship the infrastructure; content + ops need to populate it.

| Gap | Engineering effort | Ops/content effort |
|---|---|---|
| **PYQ database** (Previous Year Questions for JEE Main, NEET, CBSE 10/12) | 1 sprint (ingest pipeline) | 4-8 weeks curation |
| **Hindi UI completeness** (SPA strings audit) | 2-3 sprints (i18n wiring + QA) | 1-2 weeks translation review |
| **Free-trial → paid conversion flow** (what's gated where) | 1 sprint (gate audit + docs) | Pricing decision |
| **DPDP §10/§13 data export + erasure endpoints** | ✅ shipped (prod-2, slice 25) | None |
| **Outcome tracking pipeline** ("Rohan got 95%") | 1 sprint (signal capture) | 3-6 months of users |
| **App Store + Play Store launch assets** | 1 sprint (Capacitor polish) | 1 week design |

### P1 — 3-6 month gaps

| Gap | Why it matters |
|---|---|
| **Bootstrap teacher network** (curated 50-100) | Marketplace works but empty. Credibility from human teacher count. |
| **Real low-bandwidth testing** (2G/3G real devices) | Tier-2/3 cities still have spotty connectivity. `offline_packs.py` exists but untested in production conditions. |
| **Product analytics** (Mixpanel/Amplitude) | Observability ≠ product funnel. Need cohort retention + conversion-step drop-off. |
| **Customer support integration** (Freshdesk/Intercom) | Admin app has audit log; needs ticket flow. |
| **Influencer / referral commercialisation** | `affiliates.py` ships; needs the YouTube partnership playbook. |
| **Lesson video production quality** | AI video vs Vedantu human teachers. Render polish, music, stock illustration library. |
| **Hindi voice quality** (Bhashini vs ElevenLabs) | Voice quality drives "feels professional" perception. |

### P2 — 6-12 month gaps

| Gap | Why it matters |
|---|---|
| **State-board partnerships actually *signed*** | Pipeline ships (`state_partnerships.py`); contracts don't sign themselves |
| **External compliance audit** | DPDP §9 wired but no third-party attestation |
| **Sales pipeline / CRM** (`sales_pipeline.py`) commercialised | Module exists, unused |
| **Coverage measurement + 70% gate** | Currently no `pytest --cov` measurement |
| **Type-check gate** (mypy / pyright) | Codebase is mostly typed but no enforcement |
| **Load testing baseline** (Locust) | Need known QPS limits before SLA promises |
| **Status page / public health dashboard** | Trust signal for B2B/B2G buyers |
| **A/B testing pricing tiers** | `ab_experiments` infra exists; not used for monetisation experiments |

---

## 4. Verdict — can we challenge the Indian market?

**On feature surface: yes.** Match or beat ~80% of what BYJU's,
Vedantu, Unacademy, PhysicsWallah have. The combination of (1) AI
lesson generation, (2) multi-language, (3) multi-board, (4) DPDP §9
minor protection, and (5) school ERP layer is genuinely
defensible — no single Indian competitor has all five.

**Ready to launch: no, not yet.** The blockers aren't engineering.
The remaining work is:

1. **Content**: Pre-curated PYQ + at least 200 seed lesson topics
   (3-6 months)
2. **Localisation**: Hindi UI 100% — backend supports it; the SPA
   string audit needs to happen (3 weeks)
3. **Trust**: First 100 outcome stories from real students
   (6 months of beta users)
4. **GTM**: Tier-2/3 specific marketing campaigns + influencer
   partnerships (ongoing)

**Unicorn-shaped?** Indian K-12 + competitive-prep TAM is ~$20B
and still growing post-2023 consolidation. BYJU's reached
unicorn status in 2018 with a worse feature set than this. The
market exists. The question is **execution + capital**, not
**technology**.

---

## 5. The next 6 prod-N sprints (engineering plan)

Each is one focused sprint (4 items) of the same shape as the
polish-N stack but pointed at deploy readiness instead of code
extraction.

- **prod-2** (this sprint): COMPETITIVE_ANALYSIS.md + DPDP §10/§13
  router slice + bench 370→385.
- **prod-3**: Dependency vulnerability scanning (`pip-audit` CI),
  secret-detection pre-commit hook (`gitleaks`), coverage
  measurement.
- **prod-4**: Hindi UI audit — `grep -r` for hardcoded English
  strings in `_INDEX_HTML`, surface a count + fix list. Set up
  i18n contract test that fails if a new untranslated string
  ships.
- **prod-5**: PYQ ingest pipeline — `scripts/import_pyq.py` reads
  CSV / JSON of past-year questions into `question_bank` with
  exam_code + year + topic tags. Bench → 400+.
- **prod-6**: Free-trial flow audit — script that maps every
  endpoint to (free / paid / tier-gated). Document the gates.
- **prod-7**: Mobile launch assets — App Store / Play Store
  screenshots, deep-link table, onboarding video script.

After that, we're in the "needs real users + real content team"
phase. Engineering's job there is to keep the platform fast,
reliable, and observable while the content / GTM teams operate.

---

## 6. Risks the current codebase does *not* protect against

Listed honestly so we don't over-claim:

- **Anthropic outage / pricing change.** All AI surfaces have
  cheap-mode fallbacks (heuristic essay grader, canned tutor
  reply), but a multi-day Anthropic outage degrades the product
  meaningfully.
- **Cloudflare / R2 outage.** Cache layer hard-depends on it.
  Local-disk fallback works in dev; prod has no second region.
- **Postgres single-node death.** Backups exist; restore time is
  the SLA risk. Need a read replica before promising 99.95% uptime.
- **PII leak through audit logs.** `audit.record()` accepts
  arbitrary `before` / `after` dicts. A future contributor could
  accidentally log a password hash or a session token. Add a PII
  scrubber to the audit middleware before launch.
- **Adversarial inputs to the lesson generator.** Claude is
  trained to refuse, but a determined prompt-injection attack
  via uploaded textbook images could embed instructions. The
  moderation layer catches the obvious cases; the long-tail risk
  remains.
- **Cost cap evasion via multi-account.** Per-user cap is the
  only enforcement; one Anthropic key serves all users. A
  competitor signing up 1000 free accounts costs us the M1 burn.

These need to be in the disclosure section of any investor pitch.

---

Last reviewed: 2026-06-06. Reviewer: codebase audit during prod-N
sprint kickoff. Next review: after prod-7 ships, OR before any
fundraise conversation, OR after first 1000 paying users.
