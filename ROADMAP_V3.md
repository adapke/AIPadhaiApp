# AI Pathshala — Roadmap v3 (v2.1 → v3.0)

ROADMAP.md (v0.10 → v1.0) shipped 28 items — the **school ERP MVP**.
ROADMAP_V2.md (v1.1 → v2.0) shipped 29 items — the **scale + sell +
retain** layer. v2.0.1 → v2.0.4 added 13 hardening items.

v3 is the **moat layer**. Same delivery pattern (monthly releases,
3 items each, commit + PR + merge to main with `autoDeploy: false`).

## Strategic premise

After v2.0 we have a working multi-region multi-language school +
coaching + preschool platform with enterprise SSO, audit trails, and
SOC 2 readiness. We've sold the boring infrastructure layer. v3
addresses the three risks that block hitting ₹500 Cr ARR / 10M MAU:

1. **We're a content generator, not a learning system.** Students
   consume lessons; they don't form learning habits. Parents see
   videos played, not learning gains. The competitive moat is thin.
2. **We can't compete with Byju's / PhysicsWallah on the coaching
   end without live + AI-native learning.** Video-on-demand is
   commoditized; live cohorts + always-on AI tutors are not.
3. **We're K-12 + coaching only.** Adjacencies (corporate training,
   universities, govt employee upskilling) are ₹4L Cr / yr collectively
   and use the same content + delivery stack.

Plus two continuous-investment lanes: **govt depth** (NEP 2020,
DIKSHA, state-board partnerships are slow but compounding) and
**operational maturity** (feature flags, A/B testing, data warehouse,
LLM observability — the boring stuff that compounds team velocity).

Each entry uses the same template: What/Why, Data model, API, UI,
Depends on, Effort (S/M/L/XL), Open Qs.

---

## Sequencing summary

```
AI-native learning depth (the IP moat)
  ┌─ L1 Real-time AI voice tutor (always-on)
  ├─ L2 AI essay/answer grader with rubric matching
  ├─ L3 Handwritten math recognition (multimodal vision)
  ├─ L4 Mock interview AI (UPSC personality, JEE counseling)
  ├─ L5 Adaptive practice-test generator
  └─ L6 LLM observability (cost, latency, hallucination tracking)

Live learning (the engagement moat)
  ┌─ M1 Live cohort classes (synchronous)
  ├─ M2 Live doubt clearing (notebook stream + tutor review)
  ├─ M3 Live mock-test sessions with countdown + leaderboard
  └─ M4 1:1 tutor marketplace

Community + family (the retention moat)
  ┌─ N1 Parent community / forums
  ├─ N2 Family plans + sibling discount
  ├─ N3 Study-buddy matching
  └─ N4 Mentor program (senior → junior)

Marketplace + creator economy
  ┌─ O1 Teacher publishing platform
  ├─ O2 Curriculum content marketplace
  └─ O3 Question bank marketplace

Government depth (compounding revenue)
  ┌─ P1 NEP 2020 + NCF 2023 alignment reporting
  ├─ P2 DIKSHA + NDEAR interoperability
  ├─ P3 State board partnerships (UP/MH/KA/TN/WB)
  └─ P4 NDEAR DigiLocker integration

Operational maturity (compounds team velocity)
  ┌─ Q1 Feature flags + A/B testing framework
  ├─ Q2 Cost optimization (token caching, batch generation)
  ├─ Q3 Data warehouse + event stream (BigQuery / Snowflake)
  ├─ Q4 Customer success automation
  └─ Q5 Sales pipeline integration (HubSpot / Salesforce)

Revenue / B2B expansion (TAM expansion)
  ┌─ R1 Corporate training mode
  ├─ R2 University / NPTEL extension
  ├─ R3 Bundle + voucher engine
  └─ R4 Affiliate program
```

---

# Category L — AI-native learning depth

The single biggest gap vs. Byju's, PhysicsWallah, and Khan Academy.
We have content generation but not active tutoring. L1-L6 build the
always-on AI tutor that watches the student work and intervenes.

## L1 — Real-time AI voice tutor (always-on)

**What.** Persistent conversational tutor available 24/7 via voice
+ text. Maintains context across sessions (remembers what the
student is working on, what they got wrong yesterday, what their
exam is in 3 weeks). Backed by Claude with tool use for accessing
the student's mastery model (J5), curriculum (J3), and exam
schedule (E4).

**Why.** Khan Academy's Khanmigo charges $4/mo for this; aspirants
pay ₹3000/mo for human tutors that do less. Pricing power.

**Data model.**
```sql
CREATE TABLE tutor_sessions (
  id              TEXT PRIMARY KEY,
  user_id         TEXT NOT NULL,
  started_at      REAL NOT NULL,
  ended_at        REAL,
  context_summary TEXT,                  -- LLM-condensed memory across sessions
  messages_json   TEXT NOT NULL,         -- conversation history
  tokens_in       INTEGER,
  tokens_out      INTEGER,
  cost_inr_paise  INTEGER,
  topic_keys      TEXT,                  -- JSON: topics discussed
  resolved        INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX idx_tutor_user_time ON tutor_sessions(user_id, started_at DESC);

CREATE TABLE tutor_long_memory (
  user_id      TEXT NOT NULL,
  key          TEXT NOT NULL,            -- 'preferred_explanation_style', 'weak_area', etc.
  value_json   TEXT NOT NULL,
  confidence   REAL NOT NULL DEFAULT 1.0,
  last_seen    REAL NOT NULL,
  PRIMARY KEY (user_id, key)
);
```

**API.**
- `POST /api/tutor/sessions` — start a new session; returns session_id
- `POST /api/tutor/sessions/{sid}/message` — send user message; SSE
  stream back the response
- `GET /api/tutor/sessions/{sid}` — session transcript
- `POST /api/tutor/sessions/{sid}/end` — mark resolved

**UI.**
- Floating "Ask AI" bubble across the SPA (lesson page, dashboard, etc.)
- Voice button (browser SpeechRecognition + audio playback via gTTS/
  Sarvam)
- Long-press = follow-up; saved into the tutor_long_memory

**Depends on.** J5 mastery, J3 curriculum, E4 exam.

**Effort.** **L (10 days).** 3 days session/memory schema + Claude
tool-use prompt design, 2 days SSE streaming + voice glue, 2 days
context-summary cron (every 24h: compress old messages into long
memory), 3 days cost tracking + tier gates.

**Open Qs.**
- Per-tier cost cap: M2 students get 100 messages/day, M3 unlimited?
- Privacy: tutor stores conversation — DPDP audit trail. Auto-purge
  after 90 days unless student stars a session.
- Hallucination risk: tutor confidently teaches wrong physics. Mitigation:
  always cite a source (Scene N or NCERT page) for factual claims;
  refuse to teach beyond a confidence threshold.

---

## L2 — AI essay / answer grader with rubric matching

**What.** Student writes an essay (UPSC mains) or descriptive answer
(JEE Advanced, board paper). Claude scores it against a rubric, gives
per-criterion feedback, suggests a model answer + 3 improvements.

**Why.** Essay grading is the most expensive bottleneck in coaching.
A UPSC mains test series costs ₹15,000 — 80% of which is teacher
grading time. We replace 60% of that with AI; humans spot-check.

**Data model.**
```sql
CREATE TABLE essay_rubrics (
  id              TEXT PRIMARY KEY,
  exam            TEXT NOT NULL,           -- 'upsc_mains' | 'jee_adv_descriptive' | etc.
  paper           TEXT NOT NULL,           -- 'GS-1' | 'GS-2' | 'physics-paper-2'
  topic           TEXT,
  criteria_json   TEXT NOT NULL,           -- [{name, weight, description}, ...]
  max_marks       INTEGER NOT NULL,
  model_answer    TEXT,
  created_at      REAL NOT NULL
);

CREATE TABLE essay_submissions (
  id            TEXT PRIMARY KEY,
  user_id       TEXT NOT NULL,
  rubric_id     TEXT NOT NULL,
  text          TEXT NOT NULL,
  ai_score      REAL,
  ai_feedback_json TEXT,
  human_reviewed INTEGER NOT NULL DEFAULT 0,
  human_score   REAL,
  submitted_at  REAL NOT NULL
);
```

**Effort.** **L (8 days).** 2 days rubric ingest (UPSC PYQ rubrics
from Vajiram + Insights), 3 days Claude scoring prompt + few-shot
examples, 2 days teacher-review UI, 1 day calibration against real
human-graded data.

**Open Qs.**
- Bias: Claude may favor certain answer styles. Mitigation: cohort
  testing — 100 random submissions get both AI + 2 human scores; we
  publish the correlation publicly each month.
- Pricing: ₹50/essay (₹15 cost + margin) vs ₹150 manual rate?

---

## L3 — Handwritten math recognition (multimodal vision)

**What.** Student writes math on a tablet / paper. We scan the image,
extract LaTeX, validate the work step-by-step, point out the wrong
step. Like Khan Academy + Microsoft Math Solver, but in 10 languages.

**Why.** Most math practice in India still happens on paper. Until
we close this loop, we're a video player + quiz tool, not a tutor.

**Effort.** **XL (15 days).** Claude vision handles single-equation
recognition out of the box; the work is the step-validator (parse
LaTeX → SymPy → mark each step). 5 days vision-to-LaTeX with
disambiguation, 5 days step validator + suggestion engine, 5 days
mobile UI (tablet pen + camera).

**Depends on.** L1 (so the tutor can explain the wrong step).

**Open Qs.**
- LaTeX → SymPy parser doesn't handle all Indian board notation
  (e.g. `r=a(1+e^(iπ))` quirks). Whitelist common patterns, fallback
  to natural language.
- Handwriting quality is bimodal — clean print vs scrawl. Reject
  inputs below a confidence threshold.

---

## L4 — Mock interview AI

**What.** Voice-based interview simulator. UPSC personality test,
IIT placement prep, MBA interview, doctor PG entrance. Claude plays
the panel; transcribes + scores the answer; rates body language
(when video is enabled).

**Effort.** **L (10 days).**

---

## L5 — Adaptive practice-test generator

**What.** Given a student's mastery profile (J5), generate a 30-min
practice paper that targets their weak areas + has the right
difficulty mix. Uses Claude + the question bank (J6) — pulls
existing questions when available, generates similar-style new ones
when not.

**Effort.** **M (5 days).**

---

## L6 — LLM observability

**What.** Per-prompt tracking: tokens in/out, latency, cost, model
used, prompt template version. Cumulative dashboard. Per-user cost
caps. Hallucination flagging (when a student or teacher reports a
wrong answer, the source prompt + response goes into a review queue).

**Why.** Without this, our Anthropic bill scales linearly with usage
+ we can't tune token use. Cost-saving target: 30% via prompt-caching
+ batch.

**Data model.**
```sql
CREATE TABLE llm_calls (
  id              TEXT PRIMARY KEY,
  user_id         TEXT,
  org_id          TEXT,
  module          TEXT NOT NULL,          -- 'pedagogy' | 'tutor' | 'scorer' | ...
  prompt_version  TEXT NOT NULL,
  model           TEXT NOT NULL,
  tokens_in       INTEGER NOT NULL,
  tokens_out      INTEGER NOT NULL,
  cost_inr_paise  INTEGER NOT NULL,
  latency_ms      INTEGER NOT NULL,
  cached          INTEGER NOT NULL DEFAULT 0,
  created_at      REAL NOT NULL
);
CREATE INDEX idx_llm_user_time ON llm_calls(user_id, created_at DESC);
CREATE INDEX idx_llm_module    ON llm_calls(module, created_at DESC);
```

**Effort.** **M (5 days).**

---

# Category M — Live learning

Synchronous learning + the social pressure of cohort. The only thing
preventing us from beating PhysicsWallah on coaching is that they
have live classes + we don't.

## M1 — Live cohort classes

**What.** Scheduled video classes with chat + interactive polls +
synchronized whiteboard. 50-500 students per session. Recorded for
async catchup.

**Effort.** **XL (20 days).** Build on top of LiveKit / Daily.co
(WebRTC primitives) — don't roll our own SFU. Real work: integration
with our auth, recording archival to R2, chat moderation, etc.

**Open Qs.**
- BYO tutor or marketplace? For v3: focus on schools/coaching using
  their OWN tutors via our infrastructure.
- Cost: LiveKit hosted is $50/mo + $0.40/hour-participant. Pass-through
  to M3+ tiers; absorb on enterprise.

---

## M2 — Live doubt clearing

**What.** Student opens the app, photographs their notebook, presses
"ask doubt". Tutor (or AI in L1) sees the image + their typed question
in a queue, responds via audio + annotated image.

**Effort.** **L (10 days).**

---

## M3 — Live mock-test sessions

**What.** Synchronous mock-test events (UPSC pre, JEE Main, NEET).
Countdown timer; all students start together; instant leaderboard +
solutions reveal at the end. Sense of cohort + exam-pressure
realism.

**Effort.** **M (5 days).** Reuses E4 exam engine + M1 infrastructure.

---

## M4 — 1:1 tutor marketplace  **[SHIPPED in v2.9]**

**What.** Independent tutors register, set their own rate, students
book 30-min sessions. Platform takes 20% commission. Payment + escrow
+ dispute handling.

**Effort.** **XL (20 days).** Mostly compliance + UX work (KYC for
tutors, escrow account with Razorpay, dispute flow). Engineering
core is small.

**Open Qs.**
- Marketplace is a different business — high regulatory burden,
  margin compression. Pursue only if data shows demand.

**SHIPPED v2.9 — `padhai/tutor_marketplace.py`.** 3 tables
(`marketplace_tutors` + `_bookings` + `_reviews`); applied→active→
paused tutor lifecycle; requested→confirmed→in_progress→completed
booking lifecycle with cancel/refund/no_show side-paths; 30/60/90/
120-min booking blocks at tutor's chosen rate (₹50–₹5000/30min);
20% platform fee on every booking, escrow→payout flow recorded
with payment_status (`held` / `paid_out` / `refunded`); rating
rollup (rating_sum / rating_count → avg) + student-only review
gate; tutor_earnings_summary aggregator. 14 endpoints under
`/api/marketplace/*`.

---

# Category N — Community + family

## N1 — Parent community / forums

**What.** Per-school + per-grade discussion forums. PTA-style. Replaces
the WhatsApp chaos that parents currently navigate.

**Effort.** **M (5 days).** Self-built (we don't need Discourse-level
features); aim for Stack Overflow-style simplicity.

---

## N2 — Family plans + sibling discount

**What.** One parent account ↔ multiple children. Bulk pricing
(2 kids = 1.7× single-student rate). Shared payment + reporting.

**Effort.** **S (2 days).** Mostly Razorpay subscription tweaks +
parent-child linking already exists (E8).

---

## N3 — Study-buddy matching

**What.** Algorithm pairs students with similar mastery + complementary
weak areas. Pairs unlock joint quiz challenges + chat.

**Effort.** **L (8 days).**

---

## N4 — Mentor program

**What.** Senior students (12th board passers) mentor younger ones
(8th-10th). Time-banked: 1 hour mentoring = 1 month free Pro tier.

**Effort.** **M (5 days).** Mostly UX + matching; engineering is
small.

---

# Category O — Marketplace + creator economy

## O1 — Teacher publishing platform

**What.** Anyone can record + sell a lesson series. We host, render
the avatar + voice, host the videos, handle payments + revenue
share (70/30).

**Effort.** **L (10 days).**

---

## O2 — Curriculum content marketplace

**What.** State boards / NGOs publish their syllabus-aligned content
to our platform; schools subscribe. We're the rails.

**Effort.** **M (5 days).**

---

## O3 — Question bank marketplace  **[SHIPPED in v2.9]**

**What.** Good question setters (retired professors, tuition centers)
publish question sets; teachers + students pay per pack.

**Effort.** **M (5 days).**

**SHIPPED v2.9 — `padhai/question_pack_market.py`.** 4 tables
(`qb_setters`, `question_packs_for_sale`, `qb_pack_items`,
`qb_pack_purchases`); setter verify-gate before creating packs;
pack lifecycle draft → published (≥5 questions required, auto-picks
3-question preview) → archived; per-pack pricing in [₹19, ₹5000]
with 10% platform fee / 90% setter payout; idempotent one-time
purchase by (pack, buyer); paywalled question_ids endpoint after
purchase; refund path that reverses setter earnings;
setter_earnings_summary with by_pack breakdown. 16 endpoints under
`/api/qb-market/*` + `/api/admin/qb-market/*`.

---

# Category P — Government depth

These items compound over years. Each state board partnership is a
1-2 year sales cycle but locks in 5-50 lakh students.

## P1 — NEP 2020 + NCF 2023 alignment

**What.** Ingest the National Education Policy 2020 + National
Curricular Framework 2023 frameworks. Map every lesson to NEP's
"21st century skills" + NCF's competencies. Report card shows
NEP/NCF coverage per student.

**Effort.** **L (10 days).**

---

## P2 — DIKSHA / NDEAR interoperability

**What.** DIKSHA is the govt's national platform with 100M+ users.
NDEAR (National Digital Education Architecture) is the
interoperability spec. Comply with the data formats; let parents +
schools sync content between us and DIKSHA.

**Effort.** **L (10 days).**

---

## P3 — State board partnerships

**What.** Pilot with UP (largest), Maharashtra (English-medium
demand), Karnataka (urban tech-friendly), Tamil Nadu (state board
+ NEET focus), West Bengal (bilingual). Each state gets a custom
deployment with their syllabus + their branding.

**Effort.** **XL per state (~30 days each).** Mostly sales + content
ingest, not engineering.

---

## P4 — NDEAR DigiLocker integration  **[SHIPPED in v3.0]**

**What.** Students store their AI Pathshala certificates / report
cards in DigiLocker (govt's national document vault).

**Effort.** **M (5 days).**

**SHIPPED v3.0 — `padhai/digilocker.py`.** 4 tables
(`digilocker_orgs`, `digilocker_doc_types`, `digilocker_consents`,
`digilocker_issuances`); 4-type whitelisted catalog
(`course_completion`, `exam_certificate`, `corporate_training`,
`tutor_session_log`) seeded on migrate; per-org sandbox → live
activation flow with API-key hash; DPDP §6 explicit consent
capture with HKDF-SHA256 Aadhaar hashing (never raw), exact
consent_text stored verbatim for audit; DPDP §13 withdrawal via
`revoke_consent`; issuance lifecycle pending → queued → issued /
failed / revoked with SHA-256 doc-body dedup preventing double
issuance. 9 endpoints under `/api/digilocker/*` +
`/api/admin/digilocker/*`.

---

# Category Q — Operational maturity

Boring infra that compounds team velocity. None of these directly
unlock revenue, but they're how a 5-person team stays at 5 people
while the user count goes 100k → 10M.

## Q1 — Feature flags + A/B testing

**What.** Server-side feature flags (LaunchDarkly / Statsig / build-our-own).
Every new feature ships behind a flag, gradually rolled out, A/B
tested for engagement/retention impact.

**Effort.** **M (5 days).** Self-built; we don't need LaunchDarkly's
$X/seat pricing.

---

## Q2 — Cost optimization

**What.** Anthropic prompt caching (5-min cache, 90% discount on
cached tokens). Batch API for non-interactive generation (50%
discount). Token compression (template + variable extraction).
Target: 30% cost reduction at current quality.

**Effort.** **M (5 days).**

---

## Q3 — Data warehouse + event stream

**What.** Every user action → Kafka / NATS → BigQuery / Snowflake.
Cohort retention dashboards (Mode / Metabase). The basic analytics
foundation we've been faking with SQLite COUNT queries.

**Effort.** **XL (15 days).**

---

## Q4 — Customer success automation

**What.** Health-score per org (engagement, support tickets, churn
risk). Auto-alerts to CSM. Pre-renewal nudges. Onboarding email
sequences.

**Effort.** **M (5 days).**

---

## Q5 — Sales pipeline integration

**What.** HubSpot / Salesforce sync. Demos auto-logged. School lead
scoring. Quote-to-cash workflow.

**Effort.** **S (2 days).** Mostly HubSpot configuration; engineering
scope is the bidirectional webhook.

---

# Category R — Revenue / B2B expansion

## R1 — Corporate training mode

**What.** Reuse the lesson generator + assessment engine for
corporate L&D. Indian IT companies (Infosys, TCS, Wipro) train
~3M employees/year. ₹1000/employee/year is a ₹3000 Cr TAM.

**Effort.** **L (10 days).** Engineering scope: "org_kind=corporate"
+ different content tags + LMS-style integrations (SCORM / xAPI).

---

## R2 — University / NPTEL extension  **[SHIPPED in v3.0]**

**What.** NPTEL has 2.5M+ enrolled students in govt MOOCs. Partner
with NPTEL + IGNOU + DU correspondence programs to power their
content delivery.

**Effort.** **L (10 days).**

**SHIPPED v3.0 — `padhai/university_partners.py`.** 3 tables
(`university_partners`, `partner_courses`, `partner_enrollments`);
4 integration kinds (`lti13` / `rest_api` / `saml_sso` / `embed`)
— NPTEL gets full LTI 1.3 cfg slot (`lti_client_id` +
`lti_deployment_id`); partner lifecycle prospect → contracted →
live → paused with `contracted_at` auto-stamped on first
contracted transition; course lifecycle draft → published →
archived; enrollment lifecycle enrolled → in_progress → completed
with withdrawn side-path; revenue share configurable in [10%, 70%]
(30% default to mirror NPTEL standard); `partner_stats()`
aggregator drives the partner dashboard. 11 endpoints under
`/api/university/*` + `/api/admin/university/*`.

---

## R3 — Bundle + voucher engine  **[SHIPPED in v2.9]**

**What.** "Buy Class 10 prep + Class 11 prep = 20% off." Voucher
codes from influencers + schools. Auto-applied at checkout.

**Effort.** **S (2 days).**

**SHIPPED v2.9 — `padhai/vouchers.py`.** 3 tables (`vouchers`,
`voucher_redemptions`, `bundles`); three voucher kinds
(`percent` / `fixed` / `bundle`) with SKU prefix-pattern scoping
(e.g. `course_math_*`), per-user + global redemption caps,
start/expiry windows, min-order constraints; atomic
`redeem_voucher` + dry-run `validate_voucher` (raises
`VoucherError` with user-facing reason); bundle definitions of
2–20 SKUs with auto-discount picker that returns the
highest-discount matching bundle for a cart. 10 endpoints under
`/api/vouchers/*` + `/api/admin/vouchers/*` + `/api/bundles/*`.

---

## R4 — Affiliate program  **[SHIPPED in v3.0]**

**What.** Influencer / teacher referrals with tracked unique links;
10% commission for 12 months on referred users' subscriptions.

**Effort.** **M (5 days).**

**SHIPPED v3.0 — `padhai/affiliates.py`.** 4 tables (`affiliates`,
`affiliate_visits`, `affiliate_attributions`, `commission_events`);
slug-based referral codes (`creator_alice`); 30-day click
attribution window; 12-month commission clock starting at
attribution; 10% default commission (configurable per affiliate
in [1%, 30%]); idempotent `attribute_user` (first-touch wins —
later codes can't rebind); idempotent `book_commission` on
(affiliate, invoice) — repeated payments webhook fires don't
double-book; per-affiliate `affiliate_earnings` + program-wide
`program_summary` rollups; IPs hashed at write per DPDP §10.
11 endpoints under `/api/affiliates/*` + `/api/admin/affiliates/*`.

---

# Suggested v2.1 → v3.0 sequencing

| Release | Items | Theme |
|---|---|---|
| **v2.1** | L1, L6, Q1 | AI voice tutor + LLM obs + feature flags (foundation) |
| **v2.2** | L2, L5, Q2 | Essay grader + adaptive tests + cost optimization |
| **v2.3** | M1, M2, Q3 | Live cohorts + doubt clearing + data warehouse |
| **v2.4** | L3, L4, M3 | Handwritten math + mock interviews + live tests |
| **v2.5** | N1, N2, N3 | Parent community + family plans + study buddy |
| **v2.6** | O1, O2, N4 | Teacher publishing + content marketplace + mentor program |
| **v2.7** | P1, P2, Q4 | NEP/NCF + DIKSHA + customer success automation |
| **v2.8** | P3, R1, Q5 | State partnerships + corporate + sales pipeline |
| **v2.9** | M4, O3, R3 | 1:1 marketplace + question marketplace + voucher engine |
| **v3.0** | R2, R4, P4 | University extension + affiliate + DigiLocker |

10 releases ≈ 10 months at one-per-month cadence. A 4-engineer team
compresses to 6 months at aggressive cadence.

# Total effort

- Category L (AI-native depth): 6 items, ~53 days
- Category M (Live learning): 4 items, ~55 days
- Category N (Community + family): 4 items, ~20 days
- Category O (Marketplace): 3 items, ~20 days
- Category P (Govt depth): 4 items, ~155 days (heavy state-content work)
- Category Q (Ops maturity): 5 items, ~32 days
- Category R (B2B expansion): 4 items, ~27 days

**Total: 30 items, ~362 single-engineer-days = ~72 weeks of work.**

With 4 engineers + 1 designer + 1 ops + 1 PM + curriculum specialists
+ a sales team for the govt track, this ships in 12-18 months
depending on cadence.

---

# Risk register

Top derailment risks for v3:

1. **Anthropic API outage / pricing change.** L1 (always-on tutor)
   makes us heavily dependent on Claude. Mitigation: keep the
   Sarvam / OSS-Llama fallback path warm; budget for a multi-model
   router by v2.7.
2. **WebRTC complexity for live classes (M1).** Building a video
   pipeline is famously hard. Mitigation: LiveKit hosted from day 0;
   only consider self-hosting at 100k+ concurrent participants.
3. **Govt partnership timelines.** P3 state-board deals are 12-24
   month sales cycles; revenue lags engineering. Don't bet the
   company on them — run as a separate track from L/M/N/O/Q/R.
4. **Marketplace regulation.** M4 + O1 + O3 create marketplace
   liability (commerce + tax + IP). Get legal sign-off BEFORE
   building, not after.

# What's NOT in v3

Explicit defers — items raised in scoping that we ruled out:

- **Crypto / token rewards** for engagement — RBI ambiguity + DPDP
  + parent backlash; revisit if Indian regulation clarifies
- **AR / VR lessons** — niche; revisit when Apple Vision Pro ships
  at <$1000
- **AI girlfriend / companion mode** — off-brand for an education
  product; deflects to a different company
- **Full self-driving curriculum (no teacher in loop)** — too risky
  for K-12; always keep teacher review for high-stakes content
- **Web3 credentialing on-chain** — solving a problem nobody's asking
  about; revisit if NDEAR adopts blockchain-based attestations
- **Brain-computer interfaces** — yes someone in the design partner
  group asked. No.
