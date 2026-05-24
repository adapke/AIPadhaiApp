# PadhAI — Scan Any Book, Get a Video Lesson in Your Language

> An AI tutor that turns any printed page into a personalised explainer video — in 10+ Indian languages, at the difficulty level the student chooses.

---

## 1. The One-Liner

Point your phone at any page of any textbook → in under 60 seconds you get back a **5–10 minute animated video lesson** that explains that page, in **your language**, at **your grade level**, with a built-in quiz and a Q&A chatbot tied to the content.

---

## 2. The Problem (and Why Now)

- **India has 260M K–12 students + 40M in higher ed.** ~70% study in non-English mediums; ~40% are first-generation learners whose parents can't help with homework.
- **NEP 2020** mandates teaching in the mother tongue up to Grade 5+ — but content in 22 official languages doesn't exist at scale.
- Tuition is unaffordable for most: average urban tuition ₹2,000/month, rural ₹500/month. ~150M students get *none*.
- **BYJU's collapsed (2024)**, Unacademy/Vedantu shrank, PhysicsWallah is course-driven (not page-level). The category leader seat is empty.
- **Gen-AI made on-demand video generation feasible only in the last 18 months** — this product literally could not have been built in 2023.

---

## 3. The Product

### 3.1 Student flow (90 seconds)
1. Open app → tap **Scan**.
2. Camera captures the page (or multiple pages — chapter mode).
3. Pick **language** (Hindi, Tamil, Telugu, Kannada, Marathi, Bengali, Gujarati, Punjabi, Malayalam, English to start).
4. Pick **level**: *I'm in Grade 6* / *Explain like I'm 5* / *Board exam depth* / *NEET-JEE depth*.
5. App generates and plays a **5–10 min animated video** with a narrator voice, on-screen diagrams, worked examples, and at the end → **3-question quiz** + **"Ask me anything about this page" chatbot**.
6. Save video to "My Library", share to WhatsApp, or schedule daily revision.

### 3.2 What the video actually contains
- Khan-Academy-style animated whiteboard (cheap, fast, scalable) — not full Sora-level photorealistic clips (too expensive at scale).
- Real diagrams re-drawn cleanly (not just OCR'd images).
- Math equations rendered with proper notation, step-by-step.
- A consistent friendly narrator avatar per language (kids form attachment → retention).
- Adaptive pacing: Grade 3 video is slower, more visuals, simpler vocab; Grade 12 is dense.

### 3.3 The gamification layer (this is the "game" part)
- **Streaks** (Duolingo-style) for daily scans / videos watched.
- **XP + Levels** per subject; unlock harder content as you level up.
- **Leaderboards** at school / city / state level (opt-in).
- **Badges** for milestones (10 chapters of Physics, 100% on 5 quizzes, etc.).
- **Parent dashboard** (separate app) showing what was learned this week — drives parent buy-in for paid subscription.

---

## 4. Competitive Landscape (and Why We Win)

| Player | What they do | What they miss |
|---|---|---|
| **StudyFetch** | Upload → Spark.E AI tutor + flashcards + quizzes + video explainers + voice calls. $11.5M Series A, 6M users (mostly US college) | English-only, US-priced ($8–12/mo), no NCERT/state-board awareness, no Indian languages, no photoreal teacher tier, no B2G distribution |
| **Khan Academy / Khanmigo** | Free courses + AI text tutor | Fixed catalogue, text-only AI, weak on Indian syllabi & languages |
| **PhysicsWallah** | Recorded human courses, cheap | Not personalised, not scan-driven, English/Hindi only |
| **BYJU's / Vedantu / Unacademy** | Live + recorded courses | Expensive, broken business models, course-shaped not page-shaped |
| **NCERT Diksha (govt)** | Free static content repository | No generation, no personalisation, no engagement loop |
| **Synthesia / HeyGen / Sora** | B2B video gen tools | Not student-facing, no pedagogy, no syllabus alignment |
| **NotebookLM (Google)** | Doc → English audio podcast | Audio only, English only, not pedagogical, not for kids |

**The unfilled box:** *scan-to-video, multilingual, level-adaptive, syllabus-aware, on cheap Android, with a photoreal teacher tier priced for Indian wallets.* That's our wedge.

### 4.1 PadhAI vs StudyFetch (the closest competitor)

StudyFetch raised $11.5M from Owl Ventures + College Board in mid-2025 and has 6M users. They are the right benchmark. Where each side wins:

| Capability | StudyFetch | PadhAI today | Status |
|---|---|---|---|
| Input: PDF, PPTX, DOCX, image | ✅ all | ✅ PDF + image; PPTX/DOCX sketched (need LibreOffice on host) | parity once we ship the LibreOffice path |
| Input: YouTube, lecture audio | ✅ | ❌ roadmap (yt-dlp + Whisper) | gap |
| AI tutor chat about uploaded content | ✅ Spark.E | ✅ `/lessons/{hash}/{lang}/{level}/chat` ships this PR | **parity** |
| Voice tutor calls | ✅ "Call Spark.E" | ⚠️ `padhai.quiz_cli` does voice Q&A; no live phone-call mode | gap |
| Flashcards | ✅ | ⚠️ same Lesson JSON could generate them — roadmap | gap |
| Quizzes embedded in output | ✅ | ✅ rendered into the video | **parity** |
| Video explainers from any content | ✅ | ✅ this is our headline | **parity** |
| Photoreal teacher avatar | ❌ AI-generated only | ✅ M3 Wav2Lip + M4 Synthesia/HeyGen/Tavus/DeepBrain/D-ID | **we win** |
| Live Lecture Assistant (real-time notes) | ✅ | ❌ roadmap | gap |
| Game-style Arcade | ✅ | ⚠️ quiz scenes; full gamification (XP / streaks / leaderboards) on roadmap | partial |
| Indian-language narration | ❌ | ✅ 10 languages via Piper + Bhashini | **we win** |
| NCERT / state-board syllabus awareness | ❌ | ✅ RAG over Indian curriculum (roadmap, schema in place) | **we win** |
| B2G / institutional distribution | ❌ consumer-only | ✅ explicit B2G play in LEARN.md §7 | **we win** |
| Pricing (consumer entry) | $7.99/mo = ~₹670/mo | ₹49–99/mo for M1/M2 | **we win 10×** |
| Pricing (photoreal premium) | n/a (no photoreal tier) | ₹2,499/mo for M4e DeepBrain | **we own this market** |

**Strategic read:** StudyFetch is a horizontal AI-study-buddy aimed at US college students with a chat-first interaction model. We are a vertical India-first scan-to-video tutor with deep pedagogy + photoreal teacher tiering + B2G distribution. There's room for both companies, but in India we should be the default; in the US, StudyFetch should be the default. The competitive risk is StudyFetch adding Hindi/Indic support and pricing for India — defending against that is why the **photoreal-teacher tier × Indian-language coverage × B2G** intersection has to be our durable moat.

### 4.2 Three gaps we're closing this push

1. **PDF input** — `padhai/ingest.py` accepts PDF and fans out one job per page through the existing pipeline. PyMuPDF, no system deps. PPTX/DOCX stubs sketched (LibreOffice on host).
2. **Chat-on-content (Spark.E equivalent)** — `POST /lessons/{hash}/{lang}/{level}/chat` looks up the cached Lesson JSON and answers grounded in *the student's actual material*, not Claude's general knowledge. Free on M1/M2.
3. **Subscription matrix shipped server-side** — `auth.resolve_provider_for_tier()` is the single source of truth that maps `subscription_tier → talking_head_provider`. Server-side enforced, client form values can't override.

---

## 5. Technical Architecture

```
[Phone camera]
      │
      ▼
[OCR + layout parsing]          ← Google Vision / Tesseract + LLM cleanup
      │
      ▼
[Pedagogy script generation]    ← Claude Opus 4.7 (best at reasoning + multilingual)
      │   prompt = page text + grade level + language + syllabus context (RAG)
      ▼
[Script → scene plan]           ← LLM breaks script into ~20 scenes with visuals
      │
      ├──▶ [TTS in chosen language]   ← Bhashini (govt, free) + AI4Bharat for 22 Indian langs
      │                                ElevenLabs / OpenAI for English
      │
      ├──▶ [Whiteboard animation]     ← Remotion / Manim renderer on GPU workers
      │
      └──▶ [Diagram redraw]            ← SVG generation via LLM + template library
      │
      ▼
[Video stitch + caption burn-in]   ← FFmpeg pipeline, output 720p H.264, ~30 MB / 7 min
      │
      ▼
[CDN] ──▶ phone player + WhatsApp shareable link
```

**Key choices:**
- **Whiteboard animation, not full generative video.** Sora/Veo cost ₹100–300 per minute and are still hallucination-prone on diagrams/equations. Whiteboard renders cost ₹2–4/min and look great for education (Khan Academy's entire $500M valuation was built on this aesthetic).
- **Bhashini** (Govt of India's national language translation mission) gives us free/low-cost TTS in 22 Indian languages — massive cost moat vs anyone building from scratch.
- **RAG over NCERT + state-board syllabi** so videos use the same chapter framing the student's school uses — increases trust.
- **All generation async** with a 30-60s wait → user gets push notification when ready. Lets us batch GPU jobs and keep cost down.

---

## 5.1 Data + storage architecture (at 1M-user scale)

| Layer | Local-dev fallback | **Production (1M users)** | Why |
|---|---|---|---|
| **Database** | SQLite on disk | **PostgreSQL** — Render Pg / RDS / Aurora / Supabase | Real concurrency, JSONB for the payload-blob design, foreign keys for per-user usage aggregation, scales to billions of rows |
| **Video storage** | Local disk | **Cloudflare R2** (S3-compatible) | **Zero egress fees** — S3 would cost ~$4,500/mo at 1M-user video egress (50 TB/month at $0.09/GB); R2 charges $0 egress and $0.015/GB-month storage |
| **Audio storage** | Local disk | Same R2 bucket | Same reasoning, smaller volume (~1 MB/clip × hit-rate-amortised) |
| **Cache lookup** | sha256 → local path | sha256 → **Postgres row → R2 URL** | Fleet-wide deduplication — any worker that has seen the (image, lang, level, theme, provider) combination has populated R2; every other instance can serve it without rendering |
| **Serving** | `FileResponse` from disk | **`RedirectResponse(302)` → R2 signed URL** | The web tier never sees the MP4 bytes for cached content; clients pull directly from R2/Cloudflare CDN. This is what unbottlenecks the deploy at 1M users |

Database sizing for 1M users:

| Table | Row estimate at 1M users | Storage |
|---|---|---|
| `users` | 1M | ~200 MB |
| `lessons` (sha256-deduplicated NCERT/state-board catalogue) | ~50k | ~250 MB JSONB |
| `audio_clips` | ~500k | ~50 MB metadata (audio bytes are in R2) |
| `videos` | ~150k (5 tiers × ~30k unique pages cached) | ~30 MB metadata (MP4s are in R2) |
| `jobs` (30-day rolling) | ~10M | ~5 GB JSONB (largest table) |
| `usage_daily` | ~30M | ~3 GB |

**Total: ~9 GB.** Comfortably fits in a $25/mo Render Pg Pro instance. Move to Aurora Serverless v2 when reads exceed ~5k QPS.

R2 sizing for 1M users:

| Content | Dedup'd volume | Monthly cost |
|---|---|---|
| Lesson catalogue (50k pages × 5 tiers avg) | ~250k MP4s × 10 MB | ~₹37 (storage only) |
| Audio cache | ~500k clips × 1 MB | ~₹0.75 |
| **Egress** (the line item that kills S3) | 50 TB/mo | **₹0 on R2** vs ~₹3.8 lakh/mo on S3 |

The R2-vs-S3 egress delta is roughly the entire engineering team's salary every month. This is the call that has to be right.

---

## 6. Unit Economics (the make-or-break number)

**Cost per 7-min video at scale:**
| Step | Cost |
|---|---|
| OCR + layout | ₹0.50 |
| LLM script (Opus 4.7, ~3K input + 2K output tokens, cached syllabus context) | ₹4–6 |
| TTS (Bhashini for Indian langs) | ₹0–2 |
| Whiteboard render on spot GPU | ₹3–5 |
| Storage + CDN | ₹1 |
| **Total** | **₹9–15 per video** |

**Revenue per paying user/month** at ₹99 sub × ~20 videos = ₹99 revenue vs ~₹200 generation cost.
→ **Negative at first.** Get to break-even by:
1. Caching popular pages (NCERT Class 10 Science Chapter 3 will be requested 10M times — generate once, serve forever).
2. After 6 months of caching, ~70% of requests hit cache → cost drops to ₹3–5/video.
3. Then ₹99/month × 20 videos = ₹99 revenue vs ₹70 cost → 30% gross margin.

**This is why the moat compounds:** the more students use it, the cheaper our cost per video, the harder for any competitor to catch up.

### 6.1 The seven cost-saving levers (in priority order)

Most of these are already in the codebase; the rest are well-scoped roadmap items.

| # | Lever | Where it lives | What it saves | How big the saving is |
|---|---|---|---|---|
| 1 | **Video cache** (image+lang+level+theme+provider → MP4) | `padhai/cache.py::get_video / put_video` (shipped) | The *entire* generation cost on repeat requests | The dominant lever. After 6 months of NCERT/state-board coverage, ~70% of requests serve from cache at ~₹0. |
| 2 | **Lesson cache** (image+lang+level → Lesson JSON) | `padhai/cache.py::get_lesson` (shipped) | Claude vision call (~₹3-6/video) | First scan of a page pays full; every subsequent same-language scan is free even at a different theme/tier. |
| 3 | **Audio cache** (text+lang+provider → MP3) | `padhai/cache.py::get_audio` (shipped) | TTS call. Big on Bhashini (paid per char), huge on ElevenLabs (~₹125 / 7-min) | Quiz scripts, scene intros and outros, encouragement phrases all recur — typical hit rate ~40% across a user's session. |
| 4 | **Bhashini for translation, not for English** | `padhai/tts.py::BhashiniProvider` (shipped) + future NMT integration | Avoids re-calling Claude per Indic language; one Claude call → ten language variants | NEP 2020 mandates 10 languages; lever 4 turns that from 10× cost to 1.2× cost. |
| 5 | **Wav2Lip on spot GPU** instead of HeyGen for M3 | `padhai/talking_head.py::Wav2LipProvider` (shipped, runs on user's GPU) | 10-30× per-min vs hosted M4 providers; AWS Spot adds another 60-80% off | Drops M3 marginal cost from ~₹40-60 (HeyGen) to ~₹0.02 × 60 sec × ₹84/$ × spot discount ≈ **₹0.30** per minute. |
| 6 | **Claude Batch API for pre-rendering** | Future — `padhai/pedagogy.batch_generate()` | 50% off list Claude pricing for non-interactive workloads | Pre-render the top 5000 NCERT pages overnight at 50% off; serve those millions of times at ₹0 marginal. |
| 7 | **CDN / edge caching** for the cached MP4s | Future — Cloudflare R2 / CloudFront with origin shield | Bandwidth + origin egress | Once a video is in cache, serving 1M views costs ~₹0.05/view at standard CDN rates. |

### 6.2 Worked example — what happens when 100,000 students scan the same page

NCERT Class 10 Chapter 3 Page 7. Same image, same Hindi narration, M2 tier.

| Without caching | With our cache stack |
|---|---|
| 100,000 Claude vision calls × ₹5 = **₹5,00,000** | 1 call → ₹5; 99,999 hits → ₹0 |
| 100,000 Bhashini TTS calls (per-scene avg 5×) = **~₹2,50,000** | 1 set of calls → ~₹2.50; 99,999 video hits skip TTS entirely |
| 100,000 ffmpeg renders × ~5s CPU each | 1 render; 99,999 cache copies (~10ms each) |
| **Total: ~₹7,50,000** | **Total: ~₹7.50 + 99,999 × ₹0.05 CDN ≈ ₹5,000** |
| Per-student cost: **₹7.50** | Per-student cost: **₹0.05** |

That's a **150× cost reduction** on the most-trafficked content, and the
ratio gets better as the catalogue depth grows. The free M1 tier
becomes economically *positive* (ad revenue > marginal serving cost),
which is what funds the paid-tier upgrade funnel.

### 6.3 What still has to be done manually

- **Build the pre-render manifest** — enumerate the NCERT + 5 state
  boards' pages, batch-process them on Claude's 50%-off batch API,
  write to S3, populate the video cache. One-week engineering project
  worth ~₹2-3 Cr/year in saved Claude spend at unicorn scale.
- **GPU spot-instance orchestrator** for Wav2Lip — managed render
  queue against AWS Spot, with checkpointing so preempted jobs resume.
  Otherwise spot-savings come with reliability cost.
- **ElevenLabs voice-clone reuse policy** — one clone per institutional
  partner, not per teacher per video. Cuts M4 voice cost ~10× on
  repeat usage.

---

## 7. Business Model

### 7.1 The subscription matrix (medium × level)

Pricing is a **two-axis grid**, not a single ladder. The student picks
*how they want to be taught* (the **medium**: cartoon teacher vs photoreal
human, free TTS vs Bhashini vs ElevenLabs voice clone) and *what they're
being taught for* (the **level**: KG → primary → middle → board → competitive
exam). Parents of a NEET aspirant pay far more than parents of a KG child
for the same minute of video, so willingness-to-pay scales on both axes:

| Level / Medium | **M1 Cartoon + free TTS** | **M2 Cartoon + Bhashini** | **M3 Photoreal Wav2Lip** | **M4 Photoreal HeyGen (multi-persona)** |
|---|---|---|---|---|
| **L1 Kindergarten** | Free | ₹49 /mo | ₹149 /mo | ₹399 /mo |
| **L2 Primary (3-5)** | Free | ₹79 /mo | ₹199 /mo | ₹499 /mo |
| **L3 Middle (6-8)** | Free | ₹99 /mo | ₹299 /mo | ₹599 /mo |
| **L4 Secondary / Board (9-12)** | ₹49 /mo | ₹199 /mo | ₹499 /mo | ₹999 /mo |
| **L5 NEET / JEE / UPSC** | ₹99 /mo | ₹399 /mo | ₹999 /mo | ₹2,499 /mo |

**M4 choice within the Pro tier** is per-customer and per-route. All five
share the same `TalkingHeadProvider` interface — flip via
`PADHAI_TALKING_HEAD_PROVIDER` env var or by setting the provider's
specific keys:

| Sub-tier | Provider | Best at | Why pick this | Per-min cost | Suggested L5 retail |
|---|---|---|---|---|---|
| **M4a** | **D-ID** | Photo → talking head | Cheapest hosted; bring any teacher portrait, get a video back fast | ~$0.10-0.20 | ₹999 /mo |
| **M4b** | **HeyGen** | Multilingual, social-friendly | Broad avatar library; voice cloning + lip sync built-in; the best fit for marketing reels and startup-pitch-style explainers | ~$0.30 | ₹1,499 /mo |
| **M4c** | **Tavus** | **Personalized at scale** | Generate 10,000 unique videos with each student's name interpolated; the only provider built for one-to-many personalization | ~$0.40-0.60 | ₹1,799 /mo |
| **M4d** | **Synthesia** | Training & corporate polish | Highest production-bar avatars, designed for business/training; the right pick when an institution is *paying for the look* | ~$0.50 + base | ₹2,099 /mo |
| **M4e** | **DeepBrain (AI Studios)** | News-anchor / finance/UPSC | Confident anchor pose, studio background; signals authority for finance/civil-services content | ~$0.30-0.80 | ₹2,499 /mo |

### 7.2 Sample videos (the marketing page)

Every subscription card on the marketing page carries a **"Watch a
sample"** button that auto-plays a short clip in the same medium and
level the buyer is about to pay for. Today three M1 samples ship in the
repo under `samples/`; M2/M3/M4 samples are generated on the user's
machine via `scripts/build_subscription_samples.py` once the relevant
API keys (Bhashini / Wav2Lip / Synthesia / etc.) are set.

| Tier | Sample (committed) | What the viewer sees | How to generate the higher tiers |
|---|---|---|---|
| **M1 KG** | `samples/m1_kg.mp4` | Cartoon teacher, KINDERGARTEN theme, Piper soft voice, counting lesson | Re-run with Bhashini key for M2; with `WAV2LIP_*` for M3 |
| **M1 Primary** | `samples/m1_primary.mp4` | Cartoon teacher, WHITEBOARD theme, photosynthesis diagram | Same: env vars + re-run |
| **M1 Middle** | `samples/m1_middle.mp4` | Cartoon teacher, DARK_ACADEMIC theme, solar-system diagram | Same: env vars + re-run |
| **M4 NEET / JEE** | (your machine) | Synthesia / Tavus / DeepBrain photoreal teacher narrating Newton's laws | `PADHAI_TALKING_HEAD_PROVIDER=tavus` + `scripts/build_subscription_samples.py` |

### 7.3 M3 alternative — self-hosted on your own GPU

If a school or coaching chain wants photoreal output but with M2-tier
unit economics, **Wav2Lip on a self-hosted A10G** beats every hosted
option at ~$0.02 / min compute (no per-API charge). Trade-off: you
manage the GPU fleet + ML ops. Worth it once aggregate video volume
crosses ~50,000 minutes / month.

Each cell has a *different* unit-economics profile:

| Medium | Avatar runtime | Voice runtime | Cost-to-us / 7-min video |
|---|---|---|---|
| **M1** | PIL cartoon + lip-flap (in-process) | espeak-ng on device | ~₹0 |
| **M2** | PIL cartoon + lip-flap | Bhashini Indic TTS | ~₹0.5 |
| **M3** | Wav2Lip self-hosted on A10G | Bhashini + premium voice | ~₹3–4 (GPU compute) |
| **M4** | HeyGen / D-ID hosted, multi-persona, brandable | ElevenLabs voice clone | ~₹25–40 (paid APIs) |

The M3 cells are the sweet spot: a ~₹3 cost delta over M2 unlocks a 3× ARPU
jump in field tests. M4 is reserved for institutional deals and serious
exam aspirants. M1 covers everything that needs to look free for distribution.

### 7.2 Add-ons (orthogonal to the grid)

- **Family pack**: +₹100/mo, up to 3 kids on the same plan (any cells).
- **Multi-language**: free up to 2 languages; +₹50/mo for all 10.
- **Voice quiz mode** (mic + Whisper STT): included from L2 upwards.
- **Offline downloads**: included from L4 upwards.
- **Parent dashboard + streak analytics**: included from M2 upwards.

### 7.3 B2G — state govts under NEP 2020 mother-tongue mandate

- Sell to state education depts (TN, Karnataka, Maharashtra are actively buying digital learning)
- Per-student licence ₹30/year × 10M students = ₹30 Cr ARR per large state
- Pre-load on Diksha tablets distributed to govt schools
- All B2G uses are forced to **M1** or **M2** (cost discipline) — but on the L1–L4 levels students would otherwise pay for

### 7.4 B2B — schools, coaching chains, publishers

- White-label app for school chains (Delhi Public School, Kendriya Vidyalaya)
- API for publishers (S. Chand, Arihant) to add "Watch the video" QR codes in their books
- ₹50–200 per student per year
- Coaching chains in NEET/JEE/UPSC space upgrade to **M4** with their star faculty's face on every lesson — a defensible moat for the school and a 4-figure-per-student licence for us

---

## 8. Regulatory / Compliance (light vs payments)

Massively simpler than the fintech pitch:

- **DPDP Act 2023** + **DPDPR (Draft 2025)** — consent-based handling of minors' data (verifiable parental consent required for under-18). Build a parental consent module from day one.
- **NCERT/board copyright** — we generate *explanations*, never reproduce the source text verbatim, so we're in fair-use territory. Get an IP opinion from a content lawyer at incorporation.
- **AICTE / state board partnerships** — not licences, but credibility stamps that help B2G sales.
- **Content safety filters** — block adult content, misinformation; LLM safety layer mandatory since users are children.
- **No financial licences needed** unless we add tuition-fee payments — and even then only PA-PG (which we already know how to get).

---

## 9. Capital + Roadmap to Unicorn

**Day-1 (Seed, ₹3–5 Cr / ~$400K):**
- 6 people: 2 ML, 2 mobile, 1 designer, 1 founder
- v0 in 4 months: scan → English video, 3 grade levels, NCERT Class 6–10 Science only
- 5K DAU in beta (single city pilot)

**Series A (₹40–60 Cr / ~$5–7M, month 12):**
- 4 languages added (Hindi, Tamil, Telugu, Marathi)
- All NCERT subjects Class 1–12
- 200K DAU, 20K paying
- First B2G LOI signed

**Series B (₹250 Cr / ~$30M, month 24):**
- 10 Indian languages live
- 2M DAU, 300K paying, ₹30 Cr ARR
- 2 state govt deals live
- International pilot: Bangladesh, Nepal, Sri Lanka (huge unmet demand)

**Series C / unicorn (month 36–42):**
- 10M DAU, 1.5M paying, ₹180 Cr ARR
- 5 state govts + 3 publisher deals
- **Valuation $1B–$1.3B** at ~30× ARR (justified by category leadership + B2G annuity)

---

## 10. The 30-Second Pitch

> *"260 million Indian students still can't get good explanations in their own language at their own level. BYJU's collapsed, NEP 2020 mandates mother-tongue teaching, and generative AI just made on-demand video lessons economically possible. PadhAI lets any student point their phone at any textbook page and get a personalised explainer video in their language and grade level — in 60 seconds. We start B2C freemium for distribution, layer state-government deals for revenue, and our unit cost compounds downward with every cached page. Whoever wins this becomes the Khan Academy of India — and Khan Academy itself can't, because they don't speak our languages and they don't generate video. We do both."*

---

## 11. Immediate Next Steps (60 days)

1. Lock the product spec; build a Figma prototype of the scan → video flow.
2. Sign up for **Bhashini API** (free for startups under DPIIT recognition).
3. Build the script-generation prompt + evaluate on 50 NCERT Class 6 pages — measure accuracy with 5 teachers.
4. Build a CLI v0: page image → MP4 (no app yet) — proves the pipeline.
5. Incorporate, get **DPIIT Startup India** recognition (unlocks tax holidays + Bhashini free tier).
6. Raise ₹3–5 Cr seed; close pilot MoU with 2 schools.
