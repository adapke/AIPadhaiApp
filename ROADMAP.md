# AI Pathshala — Roadmap & Scoping (v0.10 → v1.0)

> **Status: SHIPPED.** All 28 items in this doc shipped across v0.10 →
> v1.0.1 (May 2026). This file is kept as the historical scoping
> record. The next-phase plan (v1.1 → v2.0) lives in `ROADMAP_V2.md`
> and covers production scale-out, enterprise sales enablement, mobile
> apps, content depth, and new-market expansion.

Concrete scoping for every deferred item. Each entry has:

- **What/Why** — one paragraph
- **Data model** — new tables / columns
- **API** — endpoint surface
- **UI** — where it lives in the app
- **Depends on** — sequencing constraint
- **Effort** — S (1-2 days) · M (3-5 days) · L (1-2 weeks) · XL (3+ weeks)
- **Open Qs** — product/business decisions needed before code

Effort estimates assume one engineer + matching design / curriculum
input. Multiply 1.5× if the path is unfamiliar (e.g. first time
integrating an SSO provider).

---

## Sequencing summary

```
Foundation (must come first — unblocks everything else)
  ┌─ F1 PRD §12 DB schema migration
  ├─ F2 Production secrets / observability hardening
  └─ F3 Free-tier + render-tier enforcement

Safety + compliance (launch-blockers for school market)
  ┌─ S1 Content moderation classifier  ← needs F2
  ├─ S2 Under-13 parental consent       ← needs F1
  ├─ S3 Source-file retention policy    ← needs F1
  └─ S4 Anti-cheating exam mode         ← independent

School ERP (B2B revenue features)
  ┌─ E1 Per-student analytics           ← needs orgs (shipped v0.9)
  ├─ E2 Notifications                   ← needs orgs
  ├─ E3 Attendance                      ← needs E2
  ├─ E4 Exams + auto-grading            ← needs S4
  ├─ E5 Fees + invoicing                ← needs Razorpay
  ├─ E6 Timetable                       ← needs orgs
  ├─ E7 SSO (Google + Microsoft)        ← independent
  ├─ E8 Parent linking (DPDP)           ← needs S2
  └─ E9 White-label / branded themes    ← needs F1

Content pipeline depth (quality + reach)
  ┌─ C1 Per-mode video generators       ← independent
  ├─ C2 /api/uploads + /api/analyze     ← independent
  ├─ C3 Audio lecture upload (ASR)      ← Bhashini API key
  ├─ C4 Video lecture upload            ← needs C3
  ├─ C5 Whiteboard photo OCR            ← Claude vision (existing)
  ├─ C6 YouTube transcript reference    ← legal review
  └─ C7 Page-level source citations     ← needs F1

Distribution + UX
  ┌─ D1 9:16 Reel renderer              ← independent
  ├─ D2 WhatsApp share                  ← independent
  └─ D3 Offline save (PWA)              ← independent

Avatar tiers (premium revenue)
  ┌─ A1 Photoreal Wav2Lip               ← GPU host + model weights
  └─ A2 Hosted avatar providers         ← API keys + budget
```

---

# Category A — School ERP completions

These are the highest-priority items for B2B sales motion. They turn
the v0.9 portal from "we have basic roster + assignments" into "we can
replace your existing school ERP".

## E1 — Per-student analytics

**What.** Today `org_stats()` shows aggregate "videos this week". Schools
also need to see *which* student watched *which* assignment, and how
they scored on the quiz at the end.

**Why.** Teachers ask "is Riya struggling with photosynthesis?" The
current dashboard can't answer it.

**Data model.**
```sql
CREATE TABLE org_assignment_completions (
  id              TEXT PRIMARY KEY,
  assignment_id   TEXT NOT NULL,
  user_id         TEXT NOT NULL,
  watched_at      REAL,
  watch_pct       INTEGER,        -- 0-100, sampled from <video> timeupdate
  quiz_score      INTEGER,        -- 0-100, NULL if no quiz attempted
  quiz_attempts   INTEGER DEFAULT 0,
  last_attempt_at REAL,
  UNIQUE (assignment_id, user_id)
);

ALTER TABLE org_members ADD COLUMN last_active_at REAL;
```

**API.**
- `POST /api/orgs/{id}/assignments/{aid}/completion` — student client
  POSTs watch + quiz progress every 30s; idempotent on UNIQUE key
- `GET /api/orgs/{id}/assignments/{aid}/stats` — class-level completion
  histogram + per-student rows (admin/teacher only)
- `GET /api/orgs/{id}/students/{uid}/stats` — per-student rollup (all
  assignments, watch completion, average quiz score, weak topics)

**UI.**
- Assignments tab → click a row → drawer with class completion bar +
  table of students with their watch_pct + quiz_score
- Members tab → click a student row → drawer with their per-assignment
  history + a "weak topics" list inferred from low quiz scores

**Depends on.** Orgs (shipped). The `<video>` timeupdate beacon needs
the Studio player + Library player to call the completion endpoint.

**Effort.** **M (4 days).** 1 day data model + API, 1 day worker-side
quiz scoring, 1 day teacher-side analytics views, 1 day student-side
beacon + tests.

**Open Qs.**
- Privacy: how granular do we expose individual student data to other
  teachers? Default: only teachers in the student's class can see them.
- DPDP: do we need parent consent before showing a minor's quiz scores
  to a teacher? Probably not (the school is the data fiduciary) but
  legal should confirm before launch.

---

## E2 — Notifications

**What.** Schools push announcements ("Class 8A exam tomorrow"),
assignment reminders, and parent updates. Today: nothing.

**Why.** Without notifications, "assignment due" is a dead inbox
metaphor — students never see the assignment they were assigned.

**Data model.**
```sql
CREATE TABLE org_notifications (
  id          TEXT PRIMARY KEY,
  org_id      TEXT NOT NULL,
  audience    TEXT NOT NULL,    -- 'all', 'class:<id>', 'role:teacher', 'user:<id>'
  kind        TEXT NOT NULL,    -- 'announcement', 'assignment_due', 'system'
  title       TEXT NOT NULL,
  body        TEXT,
  link_url    TEXT,
  sent_by     TEXT NOT NULL,    -- user_id of sender
  send_at     REAL NOT NULL,    -- supports scheduled sends
  channels    TEXT NOT NULL DEFAULT 'in_app',  -- comma-sep: in_app,email,whatsapp
  created_at  REAL NOT NULL
);

CREATE TABLE org_notification_reads (
  notification_id TEXT NOT NULL,
  user_id         TEXT NOT NULL,
  read_at         REAL NOT NULL,
  PRIMARY KEY (notification_id, user_id)
);
```

**API.**
- `POST /api/orgs/{id}/notifications` — create (admin/teacher; teachers
  scoped to their own classes)
- `GET /api/notifications/me` — my unread feed
- `POST /api/notifications/{nid}/read` — mark read

**UI.**
- Bell icon in top-right of header with unread badge
- Drawer panel with list of notifications, "Mark all read" button
- Inside org dashboard → new "Announcements" tab to compose

**Depends on.** Orgs.

**Effort.** **M (5 days).** 1.5 day data + API, 1 day in-app UI, 1 day
email backend (SendGrid / Postmark), 1.5 day WhatsApp via Bhashini /
Gupshup if we want that channel.

**Open Qs.**
- Pick an email provider. SendGrid is easiest; Postmark has better
  deliverability for transactional. ~₹500/mo at our scale.
- WhatsApp Business API needs a verified business account + template
  approval. 2-4 week lead time. Defer to v1.1?
- Scheduled sends — store `send_at` and run a worker every minute? Or
  use Cloudflare Cron Triggers?

---

## E3 — Attendance

**What.** Daily attendance tracking per class. Teachers mark present/
absent/late; aggregate reports show patterns.

**Why.** Schools want this in the same dashboard they use for content.
It's also a hook to drive daily-active engagement (teacher logs in,
marks attendance, sees content alerts).

**Data model.**
```sql
CREATE TABLE org_attendance (
  id          TEXT PRIMARY KEY,
  org_id      TEXT NOT NULL,
  class_id    TEXT NOT NULL,
  user_id     TEXT NOT NULL,   -- student
  date        TEXT NOT NULL,   -- YYYY-MM-DD
  status      TEXT NOT NULL,   -- 'present' | 'absent' | 'late' | 'excused'
  marked_by   TEXT NOT NULL,   -- teacher user_id
  notes       TEXT,
  marked_at   REAL NOT NULL,
  UNIQUE (class_id, user_id, date)
);

CREATE INDEX idx_attendance_class_date ON org_attendance(class_id, date);
```

**API.**
- `GET /api/orgs/{id}/classes/{cid}/attendance?date=YYYY-MM-DD` — daily roster
  with current marks
- `POST /api/orgs/{id}/classes/{cid}/attendance` — bulk mark (teacher only)
- `GET /api/orgs/{id}/students/{uid}/attendance?from=…&to=…` — student record
- `GET /api/orgs/{id}/classes/{cid}/attendance/report?month=YYYY-MM` —
  monthly heatmap data

**UI.**
- New "Attendance" tab in school dashboard
- Picker: class + date → grid of students with present/absent/late buttons
- Monthly view: calendar heatmap per class

**Depends on.** Notifications (E2) for "low attendance" alerts to parents.

**Effort.** **L (8 days).** 2 days data + API, 2 days teacher daily-mark
UI, 1 day student record view, 2 days monthly reports + heatmap, 1 day
edge cases (holidays, half-days, mass absent for events).

**Open Qs.**
- Half-day / period-wise attendance — yes/no? Indian schools mostly use
  full-day; coaching institutes often use per-period.
- Biometric / RFID integration — out of scope for v1.0. Schools that
  already have biometric systems can CSV-import.
- Auto-notify parents on absent — requires parent linking (E8). Defer
  the auto-notify until E8 ships; manual export works in the meantime.

---

## E4 — Exams + auto-grading

**What.** Schedule a test for a class. Auto-grade MCQ; queue free-form
answers for teacher review. Generate a report card per term.

**Why.** Coaching institutes especially want this — JEE/NEET prep is
test-cycle-driven, and our AI quiz generator already produces MCQs from
any topic.

**Data model.**
```sql
CREATE TABLE org_exams (
  id              TEXT PRIMARY KEY,
  org_id          TEXT NOT NULL,
  class_id        TEXT NOT NULL,
  title           TEXT NOT NULL,
  subject         TEXT,
  scheduled_at    REAL,
  duration_min    INTEGER,
  max_marks       INTEGER NOT NULL,
  status          TEXT NOT NULL,   -- draft | scheduled | in_progress | done | grading
  question_set    JSONB NOT NULL,  -- [{q, options, answer, marks}]
  created_by      TEXT NOT NULL,
  created_at      REAL NOT NULL
);

CREATE TABLE org_exam_attempts (
  id              TEXT PRIMARY KEY,
  exam_id         TEXT NOT NULL,
  user_id         TEXT NOT NULL,
  started_at      REAL,
  submitted_at    REAL,
  answers         JSONB,            -- {q_index: answer}
  auto_score      INTEGER,
  manual_score    INTEGER,
  total_score     INTEGER,
  feedback        TEXT,
  UNIQUE (exam_id, user_id)
);

CREATE TABLE org_term_reports (
  id              TEXT PRIMARY KEY,
  org_id          TEXT NOT NULL,
  user_id         TEXT NOT NULL,
  term            TEXT NOT NULL,   -- '2025-26-T1', etc.
  subjects_json   JSONB,           -- per-subject {avg, rank, comments}
  generated_at    REAL NOT NULL,
  pdf_url         TEXT
);
```

**API.**
- `POST /api/orgs/{id}/exams` — create + generate questions via Claude
  (re-uses pedagogy.generate_quiz)
- `GET /api/orgs/{id}/exams/{eid}/take` — student begins (returns
  questions without answers; auto-submits at duration_min)
- `POST /api/orgs/{id}/exams/{eid}/submit` — answers in
- `POST /api/orgs/{id}/exams/{eid}/grade/{attempt_id}` — teacher marks
  free-form items
- `GET /api/orgs/{id}/students/{uid}/report/{term}` — term report

**UI.**
- New "Exams" tab in school dashboard
- Teacher flow: pick class → pick subject/topic → AI generates 20
  questions → teacher reviews → schedule
- Student flow: when an exam is scheduled for their class, banner in
  Studio + Voice Tutor disabled (anti-cheating mode S4)
- Teacher grading screen: side-by-side question + student answer +
  marks input

**Depends on.** S4 (anti-cheating mode), per-student analytics (E1).

**Effort.** **XL (15 days).** Complex flow: 3 days schema + AI question
gen, 3 days student exam-taking UI with timer + autosave, 2 days teacher
grading UI, 2 days proctoring + anti-cheat lockdown, 3 days term-report
PDF generation, 2 days edge cases (network drop, re-attempts, partial
credit).

**Open Qs.**
- Proctoring level — basic (browser fullscreen + tab-switch detection)
  or webcam recording? Webcam recording adds DPDP complexity.
- Free-form answer auto-grading via Claude? Risky for board-exam
  schools that need exact rubric matching. Default: teacher-grades all
  free-form; AI provides a *suggestion* the teacher can accept/edit.
- Report card format — board templates vary wildly. Build one
  template per board (CBSE, ICSE, state) or one configurable template?

---

## E5 — Fees + invoicing

**What.** Schools collect term fees. Currently they use Razorpay or a
shoebox. We add invoicing + payment tracking + receipts.

**Why.** Sticky feature — once a school routes fees through us, switching
costs go up. Razorpay does the actual payment; we own the ledger UI.

**Data model.**
```sql
CREATE TABLE org_fee_structures (
  id              TEXT PRIMARY KEY,
  org_id          TEXT NOT NULL,
  name            TEXT NOT NULL,    -- 'Class 8 Annual'
  amount_paise    INTEGER NOT NULL,
  applies_to      TEXT NOT NULL,    -- 'class:<id>' | 'all'
  due_date        TEXT,
  created_at      REAL NOT NULL
);

CREATE TABLE org_fee_invoices (
  id              TEXT PRIMARY KEY,
  org_id          TEXT NOT NULL,
  user_id         TEXT NOT NULL,
  fee_structure_id TEXT NOT NULL,
  amount_paise    INTEGER NOT NULL,
  status          TEXT NOT NULL,    -- pending | paid | overdue | cancelled
  due_date        TEXT,
  paid_at         REAL,
  razorpay_order_id TEXT,
  razorpay_payment_id TEXT,
  receipt_url     TEXT,
  created_at      REAL NOT NULL
);
```

**API.**
- `POST /api/orgs/{id}/fees/structures` — define a fee
- `POST /api/orgs/{id}/fees/generate` — bulk-create invoices for a
  class
- `GET /api/orgs/{id}/fees/invoices` — admin list + filter
- `GET /api/orgs/{id}/fees/my` — student's own invoices
- `POST /api/orgs/{id}/fees/{iid}/pay` — Razorpay order init
- `POST /api/orgs/webhooks/razorpay` — payment confirmation

**UI.**
- New "Fees" tab in school dashboard
- Admin: fee-structure builder + invoice list with status pills
- Student/parent: "Pending fees" card with Razorpay checkout
- Generated receipt PDF (downloadable)

**Depends on.** Razorpay account (you mentioned it's already in the
plan). Parent linking (E8) so parents can pay on behalf of minors.

**Effort.** **L (10 days).** 2 days data + API, 2 days Razorpay
integration + webhook, 2 days admin UI, 2 days student/parent payment
flow, 2 days receipt PDF + invoice export.

**Open Qs.**
- GST handling — schools are GST-exempt for tuition but not for other
  services. Need a CA to confirm what's taxable.
- Refund policy — partial refunds, mid-term withdrawals. Bake into UI
  or handle manually for v1?
- Late fees — auto-add a fixed amount after due date, or % per day?
  Configurable per fee_structure.

---

## E6 — Timetable

**What.** Weekly class schedule per class: Monday 9am Maths, 10am
Science, etc. Students see "what's on today"; teachers see their load.

**Why.** Replaces the printed timetable taped to the classroom wall.
Connects to Voice Tutor / Live Lecture — "today at 10am you have
Maths" CTA.

**Data model.**
```sql
CREATE TABLE org_timetable_slots (
  id              TEXT PRIMARY KEY,
  org_id          TEXT NOT NULL,
  class_id        TEXT NOT NULL,
  day_of_week     INTEGER NOT NULL,   -- 1 = Monday
  start_time      TEXT NOT NULL,      -- 'HH:MM'
  end_time        TEXT NOT NULL,
  subject         TEXT NOT NULL,
  teacher_user_id TEXT,
  room            TEXT,
  created_at      REAL NOT NULL
);
CREATE INDEX idx_timetable_class_day ON org_timetable_slots(class_id, day_of_week);
```

**API.**
- `GET /api/orgs/{id}/classes/{cid}/timetable` — weekly grid
- `POST /api/orgs/{id}/classes/{cid}/timetable` — bulk replace (CSV
  upload supported)
- `GET /api/orgs/{id}/users/{uid}/today` — what's on for me today

**UI.**
- New "Timetable" tab — week grid (rows = periods, columns = days)
- Editable inline by admin; read-only for students
- Today's schedule card on the Studio module (sticky banner)

**Depends on.** Orgs.

**Effort.** **M (5 days).** 1 day data + API, 1.5 days week-grid UI
(drag-edit is nice-to-have, list-edit is fine for v1), 1 day CSV
upload + parser, 1.5 day "today" widget + cross-module integration.

**Open Qs.**
- Period structure — Indian schools mostly use 8 periods/day; coaching
  uses 3-4 longer blocks. Free-form start/end time covers both.
- Holiday calendar — out of scope for v1; teachers manually clear days.
- Multi-section teachers — a Maths teacher might teach 8A, 8B, 9A.
  Already supported by `teacher_user_id` per slot.

---

## E7 — SSO (Google Workspace + Microsoft 365)

**What.** "Sign in with Google" / "Sign in with Microsoft" for schools
on Workspace / Office 365. Maps the Google org domain to one of our
orgs automatically.

**Why.** Schools resist creating yet another account. SSO drops the
adoption friction to one click.

**Data model.** No new tables; extend `users`:
```sql
ALTER TABLE users ADD COLUMN sso_provider     TEXT;  -- google | microsoft | null
ALTER TABLE users ADD COLUMN sso_subject      TEXT;  -- OIDC sub claim
ALTER TABLE users ADD COLUMN sso_domain       TEXT;  -- 'stpauls.edu.in'
CREATE INDEX idx_users_sso ON users(sso_provider, sso_subject);

-- Auto-link org by domain
ALTER TABLE orgs ADD COLUMN sso_domain TEXT;  -- if set, new sso signins
                                              -- from this domain auto-join
```

**API.**
- `GET /auth/sso/google/start` — redirect to Google OAuth
- `GET /auth/sso/google/callback` — handle code → token → user lookup
  or create + auto-join org by domain
- Same pair for Microsoft

**UI.**
- "Continue with Google" / "Continue with Microsoft" buttons on the
  sign-in modal
- Org admin: "SSO domain" field in org settings — entering
  `stpauls.edu.in` auto-joins everyone signing in from that domain

**Depends on.**
- Google Cloud project + OAuth client ID/secret (free)
- Azure AD app registration (free)

**Effort.** **M (5 days).** 1 day Google flow (using `authlib`), 1 day
MS flow, 1 day domain auto-join logic + admin UI, 1 day edge cases
(domain conflicts, user already exists), 1 day testing.

**Open Qs.**
- Apple SSO — defer to v1.x; not commonly used by Indian schools.
- SAML 2.0 for enterprise — different protocol from OIDC. Some
  Microsoft customers want SAML; defer until we have an enterprise
  customer asking.
- Just-in-time provisioning vs. SCIM — JIT (create-on-sign-in) is fine
  for v1; SCIM (push-from-IdP) is an enterprise feature.

---

## E8 — Parent linking (DPDP-compliant)

**What.** Parents create accounts and link to their child's profile.
Parent sees child's progress, can pay fees, receives notifications.

**Why.** Required by PRD §3.5 and DPDP Act §9 for minors. We already
have Parent View as a stub — this turns it into real data flow.

**Data model.**
```sql
CREATE TABLE parent_links (
  id              TEXT PRIMARY KEY,
  parent_user_id  TEXT NOT NULL,
  child_user_id   TEXT NOT NULL,
  relation        TEXT,                  -- 'father' | 'mother' | 'guardian'
  consent_signed_at REAL,                -- DPDP §9 consent timestamp
  consent_ip      TEXT,                  -- audit trail
  status          TEXT NOT NULL,         -- 'pending' | 'verified' | 'revoked'
  created_at      REAL NOT NULL,
  UNIQUE (parent_user_id, child_user_id)
);
```

**API.**
- `POST /api/parents/link` — parent invites child by email OR child
  invites parent
- `POST /api/parents/link/{lid}/verify` — child confirms link
- `GET /api/parents/children` — list my children
- `GET /api/parents/children/{uid}/stats` — child's progress (same
  shape as `/me/stats`)

**UI.**
- Parent View → "Link a child" form
- Child side: notification "Your parent wants to link your account"
  with accept/reject
- DPDP consent UI: explicit checkbox, log IP + timestamp

**Depends on.** S2 (under-13 parental consent flow).

**Effort.** **M (5 days).** 1.5 days data + API, 1 day parent UI, 1 day
child verification UI + notification, 1.5 day DPDP consent log + audit
view.

**Open Qs.**
- Child under 13 — DPDP §9 says consent of parent is *required*.
  We can't let an under-13 sign up alone. Two paths: (a) parent
  signs up first, then creates child account; (b) child signs up,
  parent email is required, link is created in pending state.
  (b) is closer to actual user flow; need legal sign-off.
- Multiple parents — single child, two parents. Both have view rights;
  who pays fees? Probably either, last-paid wins.

---

## E9 — White-label / branded themes

**What.** Schools on Enterprise tier upload their logo + pick brand
colors. The student-facing app shows their branding instead of ours.

**Why.** Government / large-coaching-chain deals demand this. Byju's
wouldn't let their students see "Powered by AI Pathshala".

**Data model.**
```sql
ALTER TABLE orgs ADD COLUMN brand_name      TEXT;
ALTER TABLE orgs ADD COLUMN brand_logo_url  TEXT;
ALTER TABLE orgs ADD COLUMN brand_color     TEXT;  -- hex
ALTER TABLE orgs ADD COLUMN brand_subdomain TEXT;  -- 'stpauls' → stpauls.aipathshala.in
```

**API.**
- `POST /api/orgs/{id}/branding` — admin uploads logo + colors
- The main app detects subdomain → fetches `Org.branding_*` → renders
  with overrides

**UI.**
- Org admin: branding form with logo upload + color picker + live
  preview
- Student-facing: header logo + accent color come from branding

**Depends on.** F1 (DB schema with proper orgs). Cloudflare R2 (for
logo hosting). DNS wildcard subdomain setup.

**Effort.** **L (8 days).** 2 days subdomain detection + tenant
resolution middleware, 1 day logo upload to R2, 1 day color theme
override (extract CSS vars), 2 days admin UI + preview, 1 day testing
multiple orgs simultaneously, 1 day edge cases (invalid subdomains,
404 fallback).

**Open Qs.**
- Custom domains (stpauls.edu.in/learn) — yes/no? Adds DNS + SSL
  complexity. Defer to v1.x.
- Per-org email templates — schools want emails from
  `noreply@stpauls.edu.in`. SendGrid / Postmark support custom
  sending domains; cost +₹200/mo per domain. Defer.

---

# Category B — Production safety + compliance

These are non-negotiable for the school market. Don't ship E1-E9 to
real schools without B1 + B2 + B3 in place.

## S1 — Content moderation classifier

**What.** Every user upload (image, topic text) gets a cheap Claude
classifier run before generation: is this education-appropriate? If
no, return 400 with a category (csam/hate/scam/violence/political).

**Why.** A school can't be on the hook for what a student typed. Also
required by India's IT Rules 2021.

**API.**
- Add an internal `_moderate(content)` call inside `POST /lessons` and
  `POST /explain` before any Claude vision / generation call
- Returns `{allowed: bool, category: str | None, severity: int}`
- Log every blocked request with `org_id` + `user_id` for admin review

**Effort.** **S (2 days).** Claude Haiku 4.5 + structured-output prompt
that returns the classification JSON. Test against the
[Anthropic ACE](https://www.anthropic.com/research) safe-completion examples.

**Open Qs.**
- Latency budget — adds ~400ms per request. Acceptable? Yes; runs in
  parallel with the upload S3 PUT.
- Admin review queue UI — included in Admin Console as
  `/admin/moderation` (~1 extra day).

---

## S2 — Under-13 parental consent flow

**What.** Signup asks for date of birth. If under 13, gate the account
behind parent email verification per DPDP §9.

**Effort.** **M (4 days).** 1 day signup form changes, 1 day parent
email + verification token, 1 day account-locked-until-consent state,
1 day audit trail + admin override.

---

## S3 — Source-file retention/purge policy

**What.** Auto-delete uploaded PDFs/images N days after the last
related lesson is accessed. Configurable per org (default 90 days for
students, 365 for institutional).

**Why.** DPDP §8(7) data minimization. Also reduces R2 spend.

**Effort.** **S (2 days).** Background job + admin UI to override
defaults + audit log.

---

## S4 — Anti-cheating exam mode

**What.** When a student is taking an exam (E4), disable:
- Voice Tutor + Doubt Chat for that exam's subject
- Browser tab switch tracking (in-app warning + log)
- Right-click + copy-paste on exam questions

**Why.** Coaching institutes won't pay for exam features that don't
prevent cheating.

**Effort.** **M (4 days).** 1 day exam-state flag + UI lockdown, 1 day
tab-blur detection + flagging, 1 day teacher dashboard "flagged
attempts" view, 1 day testing.

---

# Category C — Content pipeline depth

## C1 — Per-mode video generators

**What.** v0.6 added a PersonalizationProfile that nudges Claude via
prompt addendum, but all 9 modes still use the same SYSTEM_PROMPT and
schema. Real differentiation needs:
- `generate_reel()` — 3-beat 9:16 output
- `generate_revision()` — formula recap structure
- `generate_parent_explanation()` — 3-scene 60-90s
- `generate_training()` — process + checklist
- `generate_awareness()` — hook + CTA structure

**Effort.** **M (5 days).** 1 day per mode × 5 modes (each: distinct
prompt + schema + 1-2 sample outputs to validate).

**Open Qs.** Are all 9 modes worth differentiating, or do we collapse
to 5 (teaching, explainer, reel, revision, awareness)? Probably the
latter.

---

## C2 — `/api/uploads` + `/api/uploads/{id}/analyze`

**What.** PRD §13's exact contract: separate upload step from generate
step. Returns `{detected_topic, suggested_grade, suggested_modes}` so
the Studio Step-2 dropdowns can preselect.

**Effort.** **S (2 days).** New endpoint + UI Step-1.5 between Source
and Customize.

---

## C3 — Audio lecture upload (Bhashini ASR)

**What.** Teacher records a 20-min audio lecture; we transcribe via
Bhashini, run it through the lesson generator as if it were a PDF.

**Effort.** **L (8 days).** 2 days Bhashini ASR integration + chunking
for long audio, 1 day upload UI, 2 days transcript-to-lesson adapter,
2 days quality tuning (Indic ASR has noise issues), 1 day edge cases.

**Open Qs.** Bhashini ASR free tier limits; may need Sarvam.ai
fallback. Audio formats — WAV / MP3 / M4A all supported by ffmpeg.

---

## C4 — Video lecture upload

**What.** Same as C3 but with video. Extract audio + key-frame slides.

**Effort.** **L (10 days).** Depends on C3.

---

## C5 — Whiteboard photo OCR

**What.** Phone photo of a classroom whiteboard → cleaned text +
diagrams → lesson.

**Effort.** **S (2 days).** Claude vision already handles this well;
just add to ingest pipeline.

---

## C6 — YouTube transcript reference

**What.** Teacher pastes a YouTube URL; we fetch the transcript (when
legally available) and use it as a lesson source.

**Effort.** **M (4 days).** 1 day transcript-fetch library +
fallbacks, 1 day legal-rights check (only allow channels with public
transcripts or Creative Commons license), 2 days testing.

**Open Qs.** Heavy legal review. Most YouTube content is
copyright-protected even if the transcript is technically public.
Recommendation: only allow user-pasted transcript text, not URL
scraping.

---

## C7 — Page-level source citations

**What.** Currently `/chat` returns `[Scene N]` citations. Upgrade to
`{upload_id, page_number}` so a parent can see "this answer came from
page 12 of the textbook your child uploaded".

**Effort.** **M (4 days).** 1 day data model (add page_refs to Scene),
2 days lesson generator changes to track page provenance, 1 day chat
prompt + parser updates.

**Depends on.** F1 (document_pages table from PRD §12 schema).

---

# Category D — Distribution + UX

## D1 — 9:16 Reel renderer

**What.** v0.7 added `dimensions` param to render_lesson but the
layout assumes 16:9. For Reels we need vertical-first composition:
teacher at the bottom, content stacked vertically, no widescreen
diagrams.

**Effort.** **M (5 days).** 1.5 day vertical layout in
`render.py`, 1.5 day vertical-safe diagram templates, 1 day Reel-mode
prompt changes, 1 day testing.

---

## D2 — WhatsApp share

**What.** "Share via WhatsApp" button on every generated video. Posts
to user's WhatsApp Web / mobile via `wa.me/?text=...` link or native
Web Share API.

**Effort.** **S (1 day).** Trivial — Web Share API + fallback to
`wa.me` deep link.

**Open Qs.** Compressed-for-WhatsApp render variant — 480p, max 16MB.
+1 day if we want a dedicated low-bandwidth pipeline.

---

## D3 — Offline save (PWA)

**What.** Service worker that caches lessons for offline playback.
Critical for low-data + village deployments.

**Effort.** **M (5 days).** 1 day PWA manifest + service worker,
1.5 days video caching strategy (IndexedDB for metadata, Cache API
for media), 1 day UI for "saved offline" list, 1.5 days quota
management + eviction.

**Open Qs.** Cache size limit per user — 500MB? 1GB? Browser-imposed
limits vary.

---

# Category E — Avatar tiers (premium revenue)

## A1 — Photoreal Wav2Lip avatar

**What.** Code already exists (`padhai/talking_head.py:Wav2LipProvider`).
Needs production GPU host + model weights + monitoring.

**Effort.** **L (8 days).** 2 days GPU worker deployment (RunPod /
Modal), 2 days Wav2Lip model + first-mile testing, 2 days queue +
fallback logic, 2 days lip-sync quality testing across languages.

**Open Qs.**
- Cost: ~₹3-4/lesson for the GPU minutes. Pass through to M3 tier
  (Premium) or eat the cost on M4 (Enterprise)?
- Model size: Wav2Lip is 416MB; loads in ~3s. Acceptable cold start.

---

## A2 — Hosted avatar providers (HeyGen / Synthesia / Tavus / D-ID)

**What.** Code already exists for all four (`padhai/talking_head.py`).
Needs API keys + budget + integration tests.

**Effort.** **M (5 days).** 1 day per provider integration test +
billing wiring, 1 day fallback routing logic when one provider is
down/quota-exhausted.

**Open Qs.**
- Pick one primary, rotate the rest as fallback. HeyGen is most
  mature for India + Hindi. Tavus is cheapest. Synthesia is best
  English. D-ID is fastest single-frame.

---

# Category F — Architecture foundation

## F1 — PRD §12 native DB schema migration

**What.** Migrate from current 6-table schema (`users, lessons,
audio_clips, videos, jobs, usage_daily`) to PRD §12's 8-table model
(`uploads, document_pages, video_requests, video_blueprints,
generated_videos, generation_jobs, usage_daily, curriculum_index`).

**Why.** Many later features (C7 page citations, E1 per-student
analytics, library pagination) need the document_pages + video_requests
shape. Doing this once now is cheaper than retrofitting later.

**Effort.** **XL (15 days).** 3 days schema design + Alembic migrations,
4 days dual-write code (write to both old and new tables during
transition), 3 days reader migration (point reads at new tables one
by one), 3 days backfill of historical data, 2 days cutover + delete
old tables.

**Open Qs.**
- Downtime tolerance — zero downtime via dual-write is standard but
  expensive. A 30-min maintenance window is much simpler.
- pgvector for `curriculum_index` — adds an extension; Render Postgres
  starter doesn't have it by default. Worth verifying.

---

## F2 — Production secrets / observability hardening

**What.** Move from environment variables to a real secrets manager
(Render Environment Groups → AWS Secrets Manager when we leave Render).
Add Sentry, PostHog, request tracing.

**Effort.** **M (4 days).**

---

## F3 — Free-tier duration + render-tier enforcement

**What.** Free users capped at 5-min videos and M1 (cartoon) tier
regardless of what they request. Currently any user can request 10-min
M4 photoreal.

**Effort.** **S (2 days).** Enforce in `build_profile()` based on
`user.subscription_tier`.

---

# Suggested v0.10 → v1.0 sequencing

If you want to ship one release per month:

| Release | Items | Theme |
|---|---|---|
| **v0.10** | F3, S1, S3, E1, D2 | Safety + first analytics + WhatsApp share |
| **v0.11** | E2, E7, S2 | Notifications, SSO, child safety |
| **v0.12** | C1, C2, D1 | Content pipeline depth + Reels |
| **v0.13** | E3, E6, F2 | Attendance, Timetable, ops hardening |
| **v0.14** | E8, C5, C7 | Parents, OCR, page citations |
| **v0.15** | S4, E4 | Anti-cheat + Exams |
| **v0.16** | E5, A2 | Fees, hosted avatars |
| **v1.0**  | F1, A1, E9, D3 | Schema migration, photoreal avatar, white-label, offline |

That's 9 releases ≈ 9 months of work. Sensible for a 2-engineer team.

# Total effort

- Category A (School ERP): 9 items, ~60 days
- Category B (Safety): 4 items, ~12 days
- Category C (Content pipeline): 7 items, ~35 days
- Category D (Distribution): 3 items, ~11 days
- Category E (Avatars): 2 items, ~13 days
- Category F (Foundation): 3 items, ~21 days

**Total: 28 items, ~152 days = ~30 weeks of single-engineer work.**

With 2 engineers + 1 designer + 1 PM and parallel tracks, plausibly
ships in 4 calendar months at a fast cadence, or 6 months at a
sustainable one.
