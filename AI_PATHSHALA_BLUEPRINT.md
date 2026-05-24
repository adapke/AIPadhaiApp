# AI Pathshala — Product Blueprint

> A multilingual AI teacher for every Indian student. Scan any page, get a video lesson in your language at your grade level.

---

## 1. Executive Summary

**AI Pathshala** is an India-first AI teacher that turns any study material into personalised video lessons, quizzes, flashcards, and doubt-solving chats — in 14+ Indian languages, at any grade level. Students upload a PDF, scanned notes, or a textbook page; AI Pathshala produces a 5–10 minute animated video with a teacher avatar, plus all the supporting study artefacts, in their chosen language.

What makes it different from StudyFetch (the closest US competitor — $11.5M Series A, 6M users):

1. **Indian languages first-class** — Hindi, Tamil, Telugu, Marathi, Bengali, Gujarati, Kannada, Malayalam, Punjabi, Urdu, Odia, Assamese, Sanskrit, English. Bhashini (Govt of India NMT/TTS, free for DPIIT-recognised startups) is the foundation.
2. **10× cheaper than StudyFetch** — free tier with real value; paid plans from ₹49/mo (vs. their ₹670/mo equivalent).
3. **Photoreal teacher tier** — StudyFetch only ships AI-generated video. We offer cartoon (free) → Wav2Lip self-hosted (Premium) → Synthesia / HeyGen / Tavus / DeepBrain / D-ID (Pro).
4. **NCERT/state-board curriculum awareness** — RAG-mapped to Indian syllabi, not generic global content.
5. **Both students AND teachers generate videos** — Student Upload Studio is a first-class module, not an afterthought.
6. **B2G distribution play** — NEP 2020 mother-tongue mandate, state governments, Diksha pre-load.

Status: **the core technical pipeline is built** (subscription-tier matrix, multilingual TTS, animated render with lip flap, Postgres + R2 production tier, GPU spot orchestrator for photoreal). MVP-ready by week 4 of execution; full Phase-1 launch by month 3.

---

## 2. Product Vision

> *"A student in a rural Maharashtra school points their phone at a Class 8 Science chapter, says 'Marathi madhe samjavun sanga' (explain in Marathi), and watches a 7-minute video with a teacher avatar walking them through photosynthesis with a diagram. They take the 3-question quiz at the end, ask a follow-up question by voice, and unlock a streak badge."*

**One-line vision:** *AI Pathshala is the teacher every Indian student deserves — patient, multilingual, available 24/7, and free if you need it to be.*

The three pillars:

1. **Universal access** — runs on ₹5,000 Android phones, works in low-data mode, supports 14+ Indian languages.
2. **Curriculum-aware** — maps every lesson to the student's actual board (CBSE, ICSE, 28 state boards, NEET/JEE/UPSC).
3. **Pedagogically grounded** — explanations adapt to grade level; quizzes test concepts not memorisation; doubt-solving teaches rather than gives answers.

---

## 3. Target Market

| Segment | Indian TAM | Initial focus | ARPU range |
|---|---|---|---|
| **K-12 school students** | 260M | **Classes 6–12, CBSE + 5 largest state boards** (Phase 1) | Free → ₹49–199/mo |
| **College students** | 40M | Phase 3 | ₹99–299/mo |
| **Competitive exam aspirants** (NEET/JEE/UPSC) | 12M | Phase 3 | ₹399–2,499/mo |
| **Teachers / private tutors** | ~10M | Phase 1 (Teacher Studio) | ₹299–599/mo |
| **Schools (B2B)** | ~1.5M schools | Phase 2 | ₹30/student/year |
| **Coaching institutes (B2B)** | ~200k centres | Phase 3 | ₹100/student/year + photoreal premium |
| **Parents** | parents of 200M+ school kids | Cross-sell add-on | +₹100/mo family pack |
| **Professional / lifelong learners** | ~50M | Phase 5 | ₹299/mo |
| **PhD / research** | ~500k | Phase 5 (Sci-Hub-adjacent simplification tools) | ₹499/mo |
| **International (NRI / Indian-origin)** | ~3M K-12 abroad | Phase 4 | $5–15/mo |

**Geographic focus (Phase 1):** Maharashtra, Karnataka, Tamil Nadu, UP, Bihar, West Bengal — the six states that account for ~55% of K-12 enrolment and have the strongest state-language demand.

---

## 4. User Personas

**Priya, 13, Class 8, government school, Pune.** Phone shared with mom; ₹99/mo data plan; Marathi-medium. Scans her textbook page on the school bus, watches video at home, takes quiz before bed. Pays ₹0; we earn from ads + B2G state contract.

**Rahul, 17, Class 12, private school, Hyderabad.** Targeting JEE Advanced. Tamil first, English fluent. Buys ₹399/mo for photoreal teacher + previous-year-papers feature. Watches at 1.5×, asks follow-ups in Hindi during the morning commute.

**Mrs. Iyer, 38, private tutor, Chennai.** Teaches 22 students online, Class 9–10 Maths. Uploads her own notes; AI Pathshala generates Tamil + English video versions, quizzes, homework, answer keys. Pays ₹299/mo; saves 10 hours/week of content prep.

**Mr. Verma, 45, government high-school principal, Lucknow.** Has Diksha tablets but no good content for Hindi-medium science. State-government deal gives him AI Pathshala for ₹30/student/year; 800 students.

**Anjali, 28, working mother, Bangalore.** Pays ₹199/mo Family Pack for her 6yo + 9yo; parent dashboard tells her weekly progress; Kannada + English narration.

**Vikram, 22, CA aspirant, Jaipur.** Uploads ICAI study material + question papers; gets adaptive practice tests + animated explanations of complex tax concepts; ₹499/mo.

---

## 5. Core Features

| Module | What it does | Phase |
|---|---|---|
| **Student Upload Studio** | Upload PDF/image/notes → generate video, quiz, flashcards, notes, mind map, audio lesson, doubt chat | MVP |
| **Teacher / Tutor Studio** | Same uploads → generate multilingual lesson plans, classroom videos, homework, test papers, answer keys, performance analytics | MVP |
| **AI Tutor Chat (Spark)** | Conversational tutor grounded in student's uploaded material; not the open internet | MVP |
| **AI Video Lesson Generator** | The core: page → script → scenes → animated video with teacher avatar | MVP |
| **Multilingual Voice Tutor** | Student speaks one language, gets explanation in same/different language | Phase 2 |
| **Curriculum Mapper** | Auto-tag uploaded material to NCERT chapter / state-board / exam syllabus | Phase 1 |
| **Assessment Engine** | MCQ, short, long, fill-blanks, true/false, case-study, previous-year-style, adaptive tests, weakness analysis | MVP (basic) → Phase 2 (adaptive) |
| **Personalised Learning Path** | Sequence content by class, weak areas, exam date, learning speed | Phase 2 |
| **Parent Dashboard** | Weekly progress, time spent, weak topics, recommended revision | Phase 2 |
| **School / Coaching Dashboard** | Student management, class groups, teacher accounts, assignment tracking, content library | Phase 3 |
| **Doubt-Solving Chat** | Question-grounded answers with citations from material | MVP |
| **Offline Saved Lessons** | Downloadable MP4s for low-data viewing | Phase 1 |
| **WhatsApp-Sharing** | One-tap share generated lesson | Phase 1 |
| **Reels-style Concept Shorts** | 60-sec animated concept explainers | Phase 2 |

---

## 6. Student Upload + Video Creation Flow

```
1. Student opens app → "Upload"
2. Picks source: camera scan / PDF / image / audio recording / typed topic
3. Selects: language (default = device language) + grade level + style
4. Optional: customises (voice gender, teacher avatar style, length)
5. Tap "Create lesson"

6. Server side:
   a. Ingest → page images (PDF/PPTX/DOCX fan out via padhai/ingest.py)
   b. Cache check (image hash → already-rendered MP4?)
      - Hit: serve instantly from R2, charge 0 credits
      - Miss: enqueue job
   c. Claude Opus 4.7 (vision + structured output) → Lesson JSON
      (5–8 scenes, bullets per scene, diagram template, quiz)
   d. Bhashini TTS for chosen language → narration MP3s per scene
   e. Render at 12fps with lip-flap teacher + progressive bullet reveal
   f. ffmpeg stitch → upload to R2 → store URL in Postgres
   g. Notify client via WebSocket or poll

7. Student sees:
   - Video player (autoplay, captions in their language)
   - Quiz button (interactive, voice-input optional)
   - "Ask a doubt" chat (grounded in this lesson's content)
   - "Save offline" (download MP4)
   - "Share on WhatsApp" (deep-link)
```

**Cost per generated video (M1 free tier):** Claude ~₹3-5, Piper TTS ₹0, render ~₹0.10, R2 storage ~₹0.05. **Total: ₹3–5 first time, ₹0 on cache hit.** After 6 months of NCERT/state-board coverage, ~70% of student-initiated renders are cache hits.

---

## 7. Teacher / Tutor Flow

Teachers get a strict superset of student capability plus:

```
1. Teacher uploads chapter (PDF, slides, handwritten notes)
2. Picks "Generate Lesson Pack"
3. Selects target languages (multi-select; e.g., Hindi + English + Marathi)
4. Selects deliverables:
   ☑ Animated lesson video (one per language)
   ☑ Lesson plan (markdown / DOCX)
   ☑ Homework assignment (PDF)
   ☑ Test paper + answer key (PDF)
   ☑ Quiz (online, shareable link)
   ☑ Real-face avatar option (with consent flow + photo upload)

5. Server:
   - One Claude call generates the master lesson plan
   - Bhashini NMT translates narration scripts to N languages (free)
   - One TTS pass per language
   - One render per language (lesson video) — cache shared across teacher's class roster
   - Test/quiz/homework: separate Claude calls keyed off the same source

6. Teacher gets:
   - Shareable links per artefact
   - Student performance analytics (who watched, who passed quiz, weak topics)
   - Edit-in-place for lesson plan + test (Claude regenerates affected video scenes)
   - Optional: monetise by listing on the marketplace (Phase 4)
```

**Teacher monetisation hooks** (Phase 4): teacher can sell lesson packs through the AI Pathshala marketplace; platform takes 20% cut; payout via UPI.

---

## 8. Multilingual Strategy

**Languages — Phase 1 (MVP):** English, Hindi.
**Phase 2:** Marathi, Tamil, Telugu, Kannada, Malayalam, Bengali, Gujarati.
**Phase 3:** Punjabi, Urdu, Odia, Assamese, Sanskrit.
**Phase 4:** Arabic, French, Spanish, German (international markets).

**TTS providers, by language:**

| Language | Primary | Fallback |
|---|---|---|
| English | Piper (offline neural) | ElevenLabs for premium |
| Hindi, Tamil, Telugu, Bengali, Marathi | **Bhashini** (Govt of India, free for DPIIT startups) | Piper per-language voices |
| Other Indic | Bhashini | AI4Bharat IndicTTS (self-host) |
| International (Phase 4) | ElevenLabs multilingual | Azure Speech |

**Translation strategy (lever #4 from cost section):**
- Generate the master `Lesson` JSON in English via Claude (one vision call)
- Bhashini NMT translates narration text to N Indian languages (free)
- TTS each language separately

**Cost comparison for a 7-min lesson in 10 languages:**
- Without this strategy: 10 Claude calls × ₹5 = ₹50 + 10 TTS calls
- With NMT-translation strategy: **1 Claude call (₹5) + 10 Bhashini NMT calls (₹0) + 10 TTS calls (₹0–5)** = ~₹5–10 total. **5–10× cheaper.**

UX:
- Default language = device locale
- One-tap language switch on any lesson (instant — pre-generated)
- Mixed-language input supported ("explain in Hinglish")

---

## 9. NCERT / State-Board Strategy (legally safe)

**Hard rule:** AI Pathshala will not host, copy, or redistribute NCERT or any state-board textbook content. NCERT books are copyrighted by the National Council of Educational Research and Training; even though they're freely viewable on ePathshala/DIKSHA, redistribution requires permission.

**Four legal paths to NCERT-aligned learning:**

1. **Curriculum mapping without content copying.** We maintain a structured index: "Class 8 → Science → Chapter 6 (Combustion and Flame) → Topics: types of fuel, conditions for combustion, ignition temperature." We map user-uploaded scans to this index. The index itself is metadata (chapter numbers, topic names) — not copyrighted.

2. **User-uploaded personal study material.** Students scanning their own textbook for personal use falls within fair dealing (Indian Copyright Act §52(1)(a)(i) — research or private study). We generate explanations from the upload; we don't redistribute the upload.

3. **Official link surfacing.** "Want the original NCERT PDF? [Open in ePathshala]" — deep-link to NCERT's own free portal. Never proxy.

4. **AI-generated explanations.** Our `Lesson` JSON is original creative work generated from the user's input — not a reproduction of the source text. It's analogous to a tutor explaining a chapter rather than reading it aloud.

**What we DO ship:**
- Curriculum mapper (CBSE Class 1–12, ICSE, 5 largest state boards in Phase 1)
- "Topics in this chapter" navigator
- "Practice from this chapter" quizzes (generated by Claude, original)
- Teacher-uploaded explanations (the teacher owns these)
- Previous-year-paper style questions (generated, not reproduced)

**What we DO NOT ship:**
- NCERT chapter PDFs
- Verbatim textbook content
- Scanned-and-OCR'd textbook libraries

**Future option (Phase 3+):** Approach NCERT directly for a content-partnership licence. Bhashini's parent (MeitY) has existing govt partnerships; warm intro is possible.

---

## 10. AI Video Generation Strategy

Already built. The staged generation strategy the prompt asked for is *exactly* our M1–M4 tier matrix:

| Stage | Tier | Tech | Cost per 7-min video |
|---|---|---|---|
| **Stage 1** Script + slides + TTS + reveal animation | M1 (Free) | Claude + Piper + PIL + ffmpeg | ~₹3 |
| **Stage 2** Cartoon teacher avatar with lip-flap | M1 (Free, included) | + `padhai/avatar.py` | ~₹0 incremental |
| **Stage 3** Production Indic voices | M2 (₹49–199) | + Bhashini TTS | +₹0.5 |
| **Stage 4** Photoreal lip-synced teacher (one persona) | M3 (₹149–999) | Wav2Lip on AWS Spot GPU | +₹3-4 |
| **Stage 5** Hosted photoreal, multi-persona, voice clone | M4 (₹399–2,499) | Synthesia / HeyGen / Tavus / DeepBrain / D-ID + ElevenLabs | +₹25-200 |

**Caching strategy that makes the unit economics work:**
- Lesson cache: `sha256(image_bytes, language, level, model)` → Lesson JSON
- Audio cache: `sha256(narration_text, language, provider)` → MP3
- **Video cache:** `sha256(image_bytes, language, level, theme, provider, render_mode)` → MP4 in R2
- Once a Class 10 Chapter 3 page is rendered, the next million students who scan that exact page serve from cache (~₹0 marginal).

**Render-on-demand, not pre-render-everything:** the pre-render manifest (Claude Batch API at 50% off) populates the cache for the top 5,000 most-requested pages overnight; the long tail renders on user demand and is cached after first use.

---

## 11. Technical Architecture

```
┌──────────────────────────────────────────────────────────────────────┐
│                         Client Apps                                   │
│  Android (React Native or Flutter)  •  PWA (Next.js)  •  iOS later   │
└──────────────────────┬───────────────────────────────────────────────┘
                       │ HTTPS
                       ▼
┌──────────────────────────────────────────────────────────────────────┐
│                  Edge: Cloudflare                                     │
│  • Workers route / cache / WAF                                        │
│  • R2 buckets serve videos directly (zero egress fee)                │
└──────────────────────┬───────────────────────────────────────────────┘
                       │
                       ▼
┌──────────────────────────────────────────────────────────────────────┐
│            Web Tier (FastAPI on Render / Cloud Run)                   │
│  Auth + REST + WebSocket  •  reads + writes Postgres, enqueues jobs  │
└──────────┬───────────────────────────────────────────────┬───────────┘
           │                                               │
           ▼                                               ▼
┌──────────────────────────┐               ┌──────────────────────────────┐
│  Postgres (Neon / RDS)   │               │  Job Queue (SQLite / pg)     │
│  • users, lessons,       │               │  Workers poll, atomic claim  │
│    audio, videos, jobs,  │               └──────────┬───────────────────┘
│    usage_daily           │                          │
│  • pgvector for RAG      │            ┌─────────────┴────────────────┐
└──────────┬───────────────┘            ▼                              ▼
           │                  ┌──────────────────┐         ┌──────────────────┐
           │                  │  CPU Workers     │         │  GPU Workers     │
           │                  │  (Render)        │         │  (AWS Spot       │
           │                  │  • cartoon       │         │   g4dn.xlarge)   │
           │                  │  • M2/M4 hosted  │         │  • Wav2Lip M3    │
           │                  └──────────┬───────┘         └─────────┬────────┘
           │                             │                           │
           │                             ▼                           ▼
           │                  ┌───────────────────────────────────────────┐
           │                  │  External services                         │
           │                  │  • Claude API (Opus 4.7 + Haiku 4.5)      │
           │                  │  • Bhashini (TTS + NMT, free)             │
           │                  │  • R2 (videos, audio, source uploads)     │
           │                  │  • ElevenLabs / HeyGen / Synthesia etc.   │
           │                  └───────────────────────────────────────────┘
           │
           ▼
┌──────────────────────────────────────────────────────────────────────┐
│                    Admin + Analytics                                  │
│  • Posthog (open-source, self-hosted) for product analytics          │
│  • Grafana + Loki for ops; Sentry for errors                         │
│  • Internal admin UI (Retool or custom Next.js)                      │
└──────────────────────────────────────────────────────────────────────┘
```

**Document ingestion pipeline:** `padhai/ingest.py` — PDF (PyMuPDF) → PNG per page; PPTX/DOCX (LibreOffice headless) → PDF → PNG; image passthrough; audio (Phase 2) Whisper → synthetic text page; video (Phase 3) frame sampling + Whisper.

**OCR:** Claude vision reads images directly — no separate OCR step. This is better than Tesseract for diagrams + equations and matches our existing pedagogy pipeline.

**STT / TTS / Translation:** Bhashini primary, Piper fallback, ElevenLabs for premium. Already abstracted behind `TTSProvider` Protocol.

**Vector DB:** pgvector extension on the same Postgres — no separate service. Use it for RAG over uploaded materials (Phase 2).

**Storage:** Cloudflare R2 (zero egress) for videos, audio, source uploads. Postgres for metadata.

**Queue:** Postgres-backed job table with atomic `claim()` — already built. Redis later if we cross ~10k jobs/min.

**Payments:** Razorpay (UPI + cards), Stripe for international.

**Content moderation:** Claude itself with a moderation system prompt; flag adult/cheating/harmful content. Manual review queue for flagged content.

---

## 12. AI / RAG Architecture

### Model routing (the big cost lever)

| Task | Model | Why |
|---|---|---|
| **Lesson generation from a page** | Claude Opus 4.7 (vision + adaptive thinking) | Hardest task; sets the entire pedagogy + diagram quality |
| **Doubt-solving chat (grounded)** | Claude Haiku 4.5 | Short, factual, repetitive — Haiku is fine |
| **Translation (script → 14 langs)** | Bhashini NMT | Free, optimised for Indic, no Claude needed |
| **Quiz from existing lesson** | Claude Haiku 4.5 | Already have the Lesson JSON; just remix |
| **Content moderation** | Claude Haiku 4.5 | Cheap classifier |
| **Curriculum mapping** (page → NCERT chapter) | text-embedding-3-small + pgvector | Semantic match against pre-indexed syllabus |
| **Adaptive test generation** | Claude Opus 4.7 (rare path) | Needs careful reasoning about student's weak topics |

### RAG pipeline (Phase 2+)

```
Upload → ingest → embed each page (text-embedding-3-small) → pgvector store
                                                                    │
                                                                    ▼
                                       student asks doubt → embed question
                                                                    │
                                                                    ▼
                                       top-K relevant pages → Claude Haiku
                                       (page text in system prompt) → answer
                                       + citation back to source page
```

**Why pgvector not Pinecone/Weaviate:** one less service to operate, fits in the same Postgres we already have, free up to a million vectors.

**Citation requirement:** every chat answer surfaces "From your uploaded material, page 3" with a deep-link. Builds trust + stops hallucination.

### Prompt caching

Claude's prompt caching (5-min and 1-hour TTLs) saves ~90% on input tokens. We use it for:
- The system prompt (frozen, cached for an hour)
- The user's uploaded material context (cached for a session)
- The curriculum index (cached for a day)

Result: a doubt-solving session with 10 follow-ups hits the same context cache, so only the question + answer pay full token price.

### Guardrails

- **System prompt instruction:** "Never give direct answers to exam questions; explain the underlying concept and let the student work through it."
- **Output filter:** Claude classifies its own output for adult/harmful content before serving.
- **Citation enforcement:** doubt-solving answers must cite a specific page from uploaded material; if no citation possible, response is "I don't see that in your uploaded material — try uploading the related chapter."

---

## 13. Database + Storage Design

Schema already shipped in `padhai/db.py`. Summary:

| Table | Purpose | Row count at 1M users | Storage |
|---|---|---|---|
| `users` | accounts + tier + level + auth | 1M | ~250 MB |
| `lessons` | Claude vision output cache (JSONB) | ~50k (deduped) | ~250 MB |
| `audio_clips` | TTS metadata; bytes in R2 | ~500k | ~50 MB |
| `videos` | Final MP4 metadata + view_count | ~150k | ~30 MB |
| `jobs` | Runtime queue, 30-day rolling | ~10M | ~5 GB |
| `usage_daily` | Per-user/day aggregates for billing | ~30M | ~3 GB |
| `embeddings` (pgvector, Phase 2) | RAG over uploaded material | ~5M | ~10 GB |
| `curriculum_index` (Phase 1) | NCERT/state-board topic map | ~50k | ~50 MB |

**Total at 1M users: ~20 GB.** Comfortably fits in a $25/mo Neon Launch plan; move to Aurora Serverless v2 once reads exceed 5k QPS.

**Storage (R2):**

| Content | Volume | Monthly cost |
|---|---|---|
| Cached video catalogue | ~250k MP4s × 10 MB = 2.5 TB | ~₹3,000 (storage) |
| Audio cache | ~500k clips × 1 MB = 500 GB | ~₹600 |
| Source uploads | ~5M images × 500 KB = 2.5 TB | ~₹3,000 |
| **Egress (the kicker)** | 50 TB/mo | **₹0 on R2** vs ~₹3.8L on S3 |

---

## 14. Cost Optimization Plan

**Seven levers, in priority order** (all but #6 and #7 already implemented):

| # | Lever | Implementation | Saving |
|---|---|---|---|
| 1 | **Video cache** (key: image + lang + level + theme + provider) | `Cache.get_video` / `put_video` in `padhai/cache.py` | dominant — 70%+ hit rate after Phase 1 |
| 2 | **Lesson cache** (Claude output dedup) | `Cache.get_lesson` / `put_lesson` | ~₹3-5 per cache hit |
| 3 | **Audio cache** (TTS output dedup) | `Cache.get_audio` / `put_audio` | small per hit but compounds |
| 4 | **Translate, don't regenerate** | 1 Claude call + Bhashini NMT for 14 langs | 5-10× cheaper for multilingual catalogue |
| 5 | **Wav2Lip on AWS Spot GPU** instead of HeyGen for M3 | `padhai/gpu_worker.py` + `ops/spot-bootstrap.sh` | ~10× cheaper than hosted M4 |
| 6 | **Claude Batch API** for pre-render manifest | `padhai/prerender.py` + `scripts/prerender.py` | 50% off Claude list price |
| 7 | **R2 over S3** | `padhai/storage.py::S3Storage` w/ R2 endpoint | ~₹3.8L/mo saved at 1M users |

**Worked example: 100,000 students scan the same NCERT page in Hindi.**

| | Without our cache stack | With our cache stack |
|---|---|---|
| Claude calls | 100k × ₹5 = ₹5,00,000 | 1 × ₹5 = ₹5 |
| Bhashini TTS | 100k × ₹2 = ₹2,00,000 | 1 × ₹2 = ₹2 |
| Render | 100k × ~₹0.10 | 1 × ₹0.10 |
| **Total** | **~₹7,50,000** | **~₹5,000** (mostly CDN serving) |
| Per student | **₹7.50** | **₹0.05** |

That's a **150× cost reduction** on the trafficked content.

**Free-tier economics:**
- Free users get 5 videos/month, ad-supported
- Most of those 5 videos are cache hits (popular NCERT pages)
- Marginal cost per free user ≈ **₹0.50/month**
- Ad revenue per free user ≈ **₹2-5/month** (display + native)
- **Free tier is gross-margin positive from day 60.**

---

## 15. Safety, Compliance, Copyright

### Indian regulatory

| Regulation | Implication | Implementation |
|---|---|---|
| **DPDP Act 2023** | Consent for personal data, right to erasure, data localisation | Consent manager in app; SQL `DELETE CASCADE` for user-data deletion; all data in Mumbai/Hyderabad regions |
| **DPDP Rules (draft 2025) — minors** | Parental consent required for users < 18 | V-CIP-style parent verification before signup; parent email tied to child account; parental dashboard |
| **IT Rules 2021 — content moderation** | Notify-and-takedown for unlawful content | In-app report button; 24h takedown SLA; grievance officer per the rules |
| **Indian Copyright Act § 52** | Fair dealing for research/private study | Personal-use upload OK; redistribution forbidden; teacher-uploaded content gets attribution |
| **NCERT copyright** | Cannot redistribute NCERT books | See §9; we generate explanations, not reproductions |

### AI-specific

- **AI-generated content labelling:** every generated video carries a "Made with AI" watermark + metadata flag (per the EU AI Act's likely Indian analogue).
- **No cheating mode:** the doubt-solving tutor explains concepts; never gives direct answers to questions phrased "what's the answer to question X". Tested with adversarial prompts.
- **Avatar consent:** real-face avatars require explicit signed consent uploaded with the photo; ID verification for teachers using own face.
- **Content moderation:** Claude-Haiku-as-classifier flags adult/violent/hate/cheating content; manual review queue.
- **Child safety:** content tagged for age range; under-13 accounts have stricter content filters + no chat with strangers.

### Privacy-by-design

- All user-uploaded content encrypted at rest (R2 native)
- TLS 1.3 in transit
- No tracking pixels; first-party analytics only
- 30-day automatic deletion of uploaded source files (lessons cached separately)
- Right-to-deletion: 1-click in settings, full cascade

---

## 16. MVP Scope

**Goal:** ship in 8 weeks with a 5-person team. Get to 1,000 active beta users by week 12.

**MVP includes:**

| Feature | Status | Detail |
|---|---|---|
| Student + Teacher login (email + password) | ✅ shipped (`padhai/auth.py`) | bcrypt + JWT |
| Upload: **PDF + image + photo of notes** | ✅ shipped (`padhai/ingest.py`) | DOCX/PPTX later |
| AI lesson generation | ✅ shipped | Claude Opus 4.7 + structured output |
| **Hindi + English** narration | ✅ shipped | Piper offline, Bhashini wireable |
| Animated video output (cartoon teacher, lip-flap) | ✅ shipped | M1 tier |
| Quiz in video + interactive | ✅ shipped (in video) | Voice quiz CLI exists |
| Flashcards (text-only, MVP) | ❌ to build | 1 day |
| AI tutor chat about uploaded material | ✅ shipped (`POST /lessons/{hash}/.../chat`) | grounded answers |
| Basic dashboard (student: recent lessons, credits) | ❌ to build | 3 days (React Native) |
| Credit-based usage (free 5 videos/mo + buy more) | ❌ to build | 2 days (Razorpay integration) |
| Admin panel (user list, content moderation queue) | ❌ to build | 3 days (Retool) |
| NCERT-safe curriculum tagging (Class 6–12, Maths + Science) | ❌ to build | 1 week (manual curriculum index) |

**MVP explicitly EXCLUDES (don't build yet):**

- ❌ Real-face teacher avatars (M4 tier) — defer to Phase 4
- ❌ More than 2 languages — defer to Phase 2
- ❌ Live lecture transcription
- ❌ College / PhD / research path
- ❌ Coaching institute marketplace
- ❌ Voice cloning
- ❌ Parent dashboard polish (basic only)
- ❌ Reels-style shorts
- ❌ Native iOS (Android-first via React Native; PWA covers iOS)
- ❌ Animation beyond progressive bullet reveal (no manim/lottie yet)
- ❌ Audio/video uploads (Phase 3)

**MVP cost:** ~₹4-6 lakh/month (3 engineers + 1 designer + Render/Neon/R2 infra + Claude tokens for ~5,000 user demos).

---

## 17. Product Roadmap

### Phase 1: MVP (Months 1–3) — Classes 6–12, Maths + Science, EN + HI
- Everything in §16 MVP
- 1,000 beta users
- Single state pilot (Maharashtra or Karnataka)

### Phase 2: Multilingual + adaptive (Months 4–6)
- Add Marathi, Tamil, Telugu, Bengali, Gujarati, Kannada, Malayalam
- RAG over uploaded materials (pgvector)
- Adaptive practice tests
- Parent dashboard
- Offline-saved lessons
- WhatsApp share
- 10,000 active users

### Phase 3: State boards + competitive exams + voice tutor (Months 7–9)
- 5 largest state boards integrated (UP, MH, TN, KA, WB)
- NEET / JEE / UPSC content packs
- Voice tutor (student speaks Tamil, gets answer in Tamil)
- School / coaching dashboards
- B2G pilot with one state government
- 50,000 active users + first 5 school contracts

### Phase 4: Advanced avatars + teacher marketplace (Months 10–12)
- Wav2Lip self-hosted (M3 tier) live
- HeyGen / Synthesia / Tavus integration (M4 tier)
- Teacher marketplace (sell lesson packs)
- iOS native app
- International soft-launch (Indian-origin abroad)
- 200,000 active users

### Phase 5: College + professional + PhD + non-Indic international (Year 2)
- College subjects (engineering, medicine, law, commerce)
- Research-paper simplification
- Arabic, French, Spanish, German
- 1M active users target

---

## 18. Monetization Model

The full subscription matrix is in §7.1 of `LEARN.md`. Headline:

| Plan | Price | Audience | Includes |
|---|---|---|---|
| **Free** | ₹0 | every Indian student | 5 videos/mo, watermarked, ads, EN+HI |
| **Student Basic** | ₹49/mo or ₹399/yr | K-12 mass-market | Unlimited basic, all Indian languages, no ads |
| **Student Pro** | ₹99/mo or ₹799/yr | NEET/JEE/board-exam students | + photoreal Wav2Lip teacher, downloads, adaptive tests |
| **Family Pack** | ₹199/mo | parents of 2–3 kids | Student Pro × 3 |
| **Teacher / Tutor** | ₹299/mo | private tutors | Multilingual generation, performance analytics, marketplace |
| **School (B2B)** | ₹30/student/year | govt + private schools | All M1+M2 features, school dashboard, bulk upload |
| **Coaching (B2B)** | ₹100/student/year | NEET/JEE coaching | M3 photoreal + custom teacher avatars |
| **B2G (state govt)** | negotiated, ₹15–25/student/year | state education depts | Whitelabel + Diksha integration |
| **Video credits** (one-shot) | ₹10 / 5 videos | top-up for free users | non-expiring |

**Revenue mix at Year 3 (target ₹100 Cr ARR):**

- 40% Student paid plans (mass market)
- 25% School / Coaching B2B
- 20% B2G state contracts
- 10% Teacher marketplace (platform cut)
- 5% Ads + credits + misc

---

## 19. Admin Dashboard Requirements

Internal-only, built in Retool (Phase 1) → custom Next.js (Phase 2).

**Modules:**

1. **User management** — list, search, filter by tier/state/active; suspend, upgrade, refund
2. **Content moderation queue** — flagged uploads + flagged AI outputs; approve/reject/escalate
3. **Curriculum index editor** — add/edit topic mappings; bulk-tag uploaded content
4. **Render queue inspector** — see job status, kill stuck jobs, re-run with different params
5. **Cost dashboard** — Claude spend, R2 storage, GPU hours, Bhashini quota usage (daily / per-user)
6. **Cache hit-rate dashboard** — which pages are popular, what to pre-render next
7. **Teacher verification** — review teacher signups (ID, photo, qualifications), approve marketplace listings
8. **Customer support** — ticket viewer + 1-click "regenerate this video", "extend credits", "refund payment"
9. **A/B test runner** — pricing experiments, UX flows
10. **Analytics overview** — DAU, retention, free→paid conversion, language mix, state mix

---

## 20. Feature Prioritization Table

| Feature | User type | Priority | Complexity | Cost impact | MVP? | Business value |
|---|---|---|---|---|---|---|
| PDF/image upload | Student + Teacher | P0 | Low (built) | Low | ✅ | Foundation |
| AI lesson video (cartoon + lip flap) | Student + Teacher | P0 | High (built) | Med | ✅ | Hero feature |
| Hindi + English narration | All | P0 | Low (built) | Low | ✅ | Table stakes for India |
| AI tutor chat (grounded) | Student | P0 | Med (built) | Low | ✅ | StudyFetch parity |
| Quiz in video + interactive | Student | P0 | Med (built) | Low | ✅ | Engagement |
| Flashcards | Student | P1 | Low | Low | ⚠ stretch | Nice-to-have |
| Credit-based usage + Razorpay | All | P0 | Med | Low | ✅ | Revenue |
| Tier-enforced provider routing | All | P0 | Low (built) | Low | ✅ | Pricing enforcement |
| Parent dashboard | Parent | P1 | Med | Low | ❌ | Phase 2 |
| Offline-saved lessons | Student | P1 | Low | Med (storage) | ❌ | Phase 2 |
| Marathi/Tamil/Telugu/Bengali/Gujarati | All | P1 | Med | Low (Bhashini free) | ❌ | Phase 2 |
| Adaptive practice tests | Student | P2 | High | Med | ❌ | Phase 2 |
| Voice tutor (speech in, speech out) | Student | P2 | Med | Med | ❌ | Phase 3 |
| Curriculum mapping (NCERT + 5 state boards) | All | P1 | High (manual content) | Low | ⚠ basic in MVP | Phase 1 essential |
| Wav2Lip M3 photoreal | Student Pro | P2 | High (built) | High (GPU) | ❌ | Phase 4 |
| HeyGen/Synthesia M4 | Coaching | P3 | Med (built) | Very high | ❌ | Phase 4 |
| School / Coaching dashboard | Institutional | P2 | High | Low | ❌ | Phase 3 |
| Teacher marketplace | Teacher | P3 | High | Med | ❌ | Phase 4 |
| College / professional content | College | P3 | High | Med | ❌ | Phase 5 |
| Research-paper simplifier | College | P4 | Med | Low | ❌ | Phase 5 |
| Reels-style shorts | Student | P3 | Med | Low | ❌ | Phase 2 |
| WhatsApp share | All | P1 | Low | Low | ❌ | Phase 1 |
| iOS native | All | P3 | High | Med | ❌ | Phase 4 (PWA covers MVP) |

---

## 21. Risks and Mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| **NCERT copyright claim** | Med | High | §9 strategy — no redistribution; user-personal-use only; pursue partnership |
| **Bhashini outage / policy change** | Med | High | Multi-provider TTS abstraction (built); Piper offline fallback always works |
| **Claude API price hike** | Low | High | Model routing — small models for cheap tasks; pre-render manifest for popular content; consider IndicTrans2 + Llama-3 as fallback for non-critical path |
| **StudyFetch enters India (lower-priced Indic offering)** | Med | High | Defensive moat: photoreal × Indian languages × B2G. None of the three are replicable in <12 months |
| **B2G contract delays** (govt procurement) | High | Med | Don't bet runway on B2G; D2C first, B2G as bonus |
| **Render / cloud outages mid-exam-season** | Med | High | Multi-region failover (R2 + multi-instance Postgres); cache pre-warm before exam season |
| **Adversarial use** (students gaming exam answers) | High | Med | Guardrails: explain not solve; cheating-mode detection in chat |
| **User data leak** | Low | Catastrophic | Encryption at rest + transit; 30-day source-file purge; SOC2-equivalent practices from day 1 |
| **GPU spot preemption rate spikes** | Low | Low | Cache layer makes re-run idempotent; multi-AZ Spot pool |
| **Real-face avatar misuse** (deepfake of teacher) | Med | High | Signed consent + ID verification; watermark + metadata flag; takedown SLA |
| **Indian payment failures** (UPI auth, card decline) | High | Med | Razorpay's retry + alternate-method UX; ₹10 credit top-ups for fragile auth journeys |
| **Mobile network instability** (rural users) | High | Med | Low-data mode (480p), offline downloads, audio-only mode |
| **Talent — ML engineers expensive in India** | Med | Med | Hire one principal ML engineer + 2 mid + 2 junior; remote-first |

---

## 22. Suggested Tech Stack

| Layer | Choice | Why |
|---|---|---|
| **Backend** | Python 3.11 + FastAPI | Already built; Claude SDK first-class; psycopg pool |
| **Frontend mobile** | React Native (Expo) | One codebase Android+iOS+web; Hindi/Indic font support good; community |
| **Frontend web** | Next.js 15 (PWA) | SSR for SEO on lesson pages; same React knowledge as RN |
| **Database** | PostgreSQL + pgvector | Single service for relational + vector; Neon free → Aurora Serverless v2 at scale |
| **Object storage** | Cloudflare R2 | Zero egress (vs ~₹3.8L/mo on S3 at 1M users); S3-compatible API |
| **Queue** | Postgres-backed (built) → Redis later | One less service for now |
| **LLM** | Claude Opus 4.7 (hard) + Haiku 4.5 (cheap) | Best Indic + best vision; Haiku for routing/chat |
| **Embeddings** | OpenAI text-embedding-3-small | Cheapest good multilingual; alternative: Cohere multilingual |
| **TTS** | Bhashini → Piper → ElevenLabs | Free-then-paid ladder |
| **STT** | Bhashini ASR / Whisper | Both work |
| **NMT** | Bhashini | Free for Indic |
| **Video render** | PIL + ffmpeg (built) | Cheap; Manim only when math content needs it |
| **Photoreal avatar (M3)** | Wav2Lip self-hosted on AWS Spot GPU | Cheapest photoreal at scale |
| **Photoreal avatar (M4)** | Synthesia / HeyGen / Tavus / DeepBrain / D-ID | Per-customer choice; all 5 wired |
| **Auth** | Local JWT + Postgres (built) | Defer Clerk until scale demands |
| **Payments** | Razorpay (UPI + cards) + Stripe international | RBI-compliant |
| **Hosting** | Render free → Cloud Run / GKE at scale | Cheap dev; production move when 100k DAU |
| **CDN** | Cloudflare (R2 already there, Workers free tier) | Free up to high volume |
| **Analytics** | PostHog (self-hosted free) + Sentry | No tracking-pixel privacy concern |
| **Ops** | Grafana + Loki + Prometheus | Self-host; cheap |
| **Admin UI** | Retool (Phase 1) → custom Next.js (Phase 2) | Speed of build > polish initially |
| **Content moderation** | Claude Haiku as classifier + manual queue | No separate service needed |

---

## 23. Team Needed for MVP

**Total: 5 people + 2 fractional.**

| Role | Headcount | Why | Monthly cost (Bangalore market) |
|---|---|---|---|
| **CEO / founder** | 1 (you) | Strategy, fundraising, sales | — |
| **Senior backend engineer** (Python/FastAPI/Postgres) | 1 | Maintain + extend the existing codebase | ₹2.5–4 L |
| **ML / AI engineer** | 1 | Prompt design, RAG, model routing, evals | ₹3–5 L |
| **React Native / mobile engineer** | 1 | Android-first app | ₹2–3.5 L |
| **Product designer** (UX for rural Indian users) | 1 | Critical — most teams build for urban English-fluent users | ₹2–3 L |
| **Curriculum / content lead** | 1 | NCERT + state-board mapping, exam expertise; can be a part-time ex-teacher | ₹1–1.5 L |
| **DevOps / SRE** (fractional) | 0.5 | Infra, monitoring, incident response | ₹1.5–2 L |
| **Legal / DPDP** (fractional) | 0.25 | Privacy + IP advisory; per-hour retainer | ₹30k–50k |

**Total monthly burn: ~₹13–20 L (₹1.6–2.4 Cr/year on team alone).** Add ~₹3-5 L/mo for infra + Claude + Bhashini + marketing in Phase 1.

**Founding-team profile pitch:**
- Backend: ex-Razorpay / Swiggy / Razorpay-Curlec / fintech-scale; Python + Postgres comfort.
- ML: ex-AI4Bharat / Niki.ai / Sarvam / one of the Indic-LLM startups; Indic familiarity essential.
- Mobile: ex-Khatabook / Meesho / Cred; Android-first, low-data experience.
- Designer: someone who has shipped products to rural India (not just metros).
- Curriculum: ex-NCERT consultant / ex-board examiner / ex-teacher-trainer.

---

## 24. Estimated Development Timeline

| Week | Milestone |
|---|---|
| **W1** | Hire backend + ML; spin up Render + Neon + R2; auth + upload + lesson generation working in staging |
| **W2** | Animated video pipeline (cartoon + lip flap) wired end-to-end in staging; first 10 internal users test |
| **W3** | Razorpay + credit system; admin panel (Retool); content moderation queue |
| **W4** | Hindi narration via Bhashini live; basic curriculum tagging (Class 6–10 NCERT Maths + Science) |
| **W5** | React Native app: upload, watch, quiz, chat flows |
| **W6** | Closed beta — 50 students + 10 teachers from one school in Maharashtra |
| **W7** | Iterate on UX; instrument funnels; fix top-3 friction points |
| **W8** | Public beta — 500 users; launch Free + Student Basic tiers; press release |
| **M3** | 1,000 active users; Phase 1 retro; plan Phase 2 |
| **M4–6** | 7 Indic languages added; pgvector RAG; parent dashboard; 10k users |
| **M7–9** | State boards; first B2G pilot; voice tutor; 50k users |
| **M10–12** | Wav2Lip live; teacher marketplace; iOS; 200k users |

**Critical-path dependencies:** Bhashini API access (apply Week 1; takes ~2 weeks). NCERT curriculum index (manual work; Week 4 deliverable). Mobile app store listing (allow 1 week for Play Store review).

---

## 25. Final Recommendation

Build AI Pathshala. Three reasons:

1. **The market window is open.** StudyFetch raised $11.5M in mid-2025 for the US college market. Their India-specific equivalent has not been built. The next 12–18 months are when this gets claimed.

2. **The cost story holds.** Cache-driven economics turn a ₹7,50,000 expense into ₹5,000 at scale (150× reduction on popular content). Free tier is gross-margin positive from month 2. The unit economics work even at ₹49/mo entry.

3. **80% of the technical risk is already retired.** Over the prior sessions of this project, we've built: multi-tier avatar abstraction, multilingual TTS, animated render with lip flap, three-tier filesystem cache, Postgres + R2 production tier, GPU spot orchestrator, auth + tier enforcement, FastAPI service deployable to Render. The remaining work is product polish, mobile app, curriculum indexing, and go-to-market.

**The one thing not to compromise on:** *photoreal teacher tier × Indian-language coverage × B2G distribution*. That intersection is the durable moat. Anyone can clone the AI tutor chat in 3 months. Nobody can replicate a state-government contract + Bhashini partnership + GPU fleet for Wav2Lip in under 18 months.

**The one thing to be paranoid about:** StudyFetch hiring 3 Indian engineers, adding Hindi support, and pricing for India. Defend by getting at least one large state-government deal signed within 12 months — that's the only thing they can't quickly copy.

---

# 30-Day Action Plan

Small team (3 people: you + 1 backend + 1 ML), ₹5 L budget for month 1.

## Week 1 — Foundations

**Day 1–2:**
- [ ] Register `aipathshala.com` + `aipathshala.in` + iOS/Android trademarks (₹15k via Vakilsearch)
- [ ] Incorporate Pvt Ltd if not already (₹10k)
- [ ] Apply for **DPIIT Startup India** recognition (free; unlocks Bhashini free tier + tax holidays)
- [ ] Apply for **Bhashini ULCA API access** (free for DPIIT; ~2 weeks)
- [ ] Set up Render account, deploy current PadhAI codebase as the AI Pathshala backend
- [ ] Add `ANTHROPIC_API_KEY` + `PADHAI_JWT_SECRET` env vars; verify `/health` returns 200

**Day 3–5:**
- [ ] Hire #1 — Senior backend engineer (Python/FastAPI; 2.5 L offer letter)
- [ ] Hire #2 — ML engineer (prompts, RAG, model routing; 3.5 L offer letter)
- [ ] Set up Neon free Postgres + Cloudflare R2 bucket; wire env vars; redeploy
- [ ] Run `scripts/migrate.py` to populate Postgres schema
- [ ] First end-to-end test: upload demo NCERT page → get cartoon video back, served from R2

**Day 6–7:**
- [ ] Sketch Figma for the student app — 5 screens: signup, upload, lesson player, quiz, chat
- [ ] Cost model spreadsheet: per-user costs at 1k / 10k / 100k DAU, by tier mix

## Week 2 — MVP backend complete

**Day 8–9:**
- [ ] Backend: Razorpay integration for ₹49/mo subscription + ₹10/5-video credit packs
- [ ] Backend: Admin panel via Retool (user list, content moderation queue, credit grants)

**Day 10–11:**
- [ ] ML: prompt engineering for doubt-solving chat — A/B test grounding strictness vs helpfulness
- [ ] ML: prompt engineering for adaptive quiz — generate 3 difficulty levels for the same lesson
- [ ] Add Bhashini TTS provider; verify Hindi narration end-to-end

**Day 12–14:**
- [ ] Backend: curriculum index seed data — Class 6–10 NCERT Maths + Science (manual, ~200 chapters)
- [ ] Backend: `POST /lessons` auto-tags the lesson against the index (pgvector similarity match)
- [ ] Internal demo to 10 friends/family with kids

## Week 3 — Mobile app + closed beta

**Day 15–17:**
- [ ] Hire #3 — React Native engineer (mid-level; 2.5 L offer letter); start ASAP
- [ ] React Native app skeleton: signup, upload, lesson player, quiz, chat (5 screens, no polish)
- [ ] Wire to backend; test on 2 Android devices (low-end + mid-tier)

**Day 18–21:**
- [ ] Closed beta: 50 students from one school in your network (Pune or Bangalore)
- [ ] Beta UX research — record 20 sessions; identify top-3 friction points
- [ ] Iterate: fix friction points; ship daily updates

## Week 4 — Public beta + first revenue

**Day 22–25:**
- [ ] Marketing landing page (single Next.js page): hero + demo video + signup form
- [ ] First demo video: solar system lesson rendered in Hindi
- [ ] Public beta launch — ProductHunt India + relevant subreddits + 5 EdTech newsletters
- [ ] Set up basic Posthog analytics + Sentry

**Day 26–28:**
- [ ] First 100 paid signups (target — ₹49/mo × 100 = ₹4,900 MRR)
- [ ] Customer support inbox; respond within 24h
- [ ] Weekly product retro

**Day 29–30:**
- [ ] Month-1 retro + Phase-2 planning
- [ ] Investor pitch deck v1 — show MRR + retention + cost-per-active-user
- [ ] First Bhashini integration test (assuming API access granted by now)
- [ ] **Decision point: bootstrap further or raise seed (~₹3–5 Cr at ~₹15 Cr post-money) to fund Phase 2 hiring**

## Month-1 success metrics

| Metric | Target |
|---|---|
| Active beta users | 500+ |
| Paid conversions | 100+ (₹4.9k MRR) |
| Avg videos generated per user | 3+ |
| Cache hit rate by day 30 | 30%+ |
| Median lesson generation time | <90s |
| App store rating | 4.0+ |
| NPS from beta cohort | 40+ |

If you hit these: raise seed; double the team; start the Bhashini integration + Marathi rollout for Phase 2.

If you miss these: **stop and diagnose before raising.** The unit economics depend on usage frequency × cache hit rate; if those aren't trending right, more money won't fix it.

---

*This blueprint is the strategic complement to the technical work already in `padhai/` — the Python namespace is the internal codename for the same product. Everything described here either ships in the current codebase or is on the explicit roadmap. The brand-facing name throughout the user-facing product, marketing, and contracts is **AI Pathshala**.*
